"""
TC Platform — Role Based Access Control (RBAC).

Roles and permissions are defined statically here (simple, auditable, and
maintainable). Users are stored in the database and reference a role key.
Menu visibility and route guards both derive from this single source of truth.
"""

# --- Permission catalogue -------------------------------------------------
PERMISSIONS = [
    "view_dashboard",
    "open_module",
    "manage_users",
    "manage_settings",
    "view_reports",
    "export_reports",
    "view_system_health",
    "manage_integrations",
    "access_admin",
    "manage_production",
]

# --- Roles (key -> display + granted permissions) -------------------------
# "*" means all permissions.
ROLES = {
    "super_admin": {
        "label": "Super Admin",
        "perms": ["*"],
    },
    "it_director": {
        "label": "IT Director",
        "perms": ["view_dashboard", "open_module", "view_reports", "export_reports",
                  "view_system_health", "manage_integrations", "access_admin",
                  "manage_production"],
    },
    "it_manager": {
        "label": "IT Manager",
        "perms": ["view_dashboard", "open_module", "view_reports", "export_reports",
                  "view_system_health", "manage_integrations", "manage_production"],
    },
    "service_desk_agent": {
        "label": "Service Desk Agent",
        "perms": ["view_dashboard", "open_module", "view_reports", "view_system_health"],
    },
    "asset_manager": {
        "label": "Asset Manager",
        "perms": ["view_dashboard", "open_module", "view_reports", "export_reports"],
    },
    "monitoring_admin": {
        "label": "Monitoring Admin",
        "perms": ["view_dashboard", "open_module", "view_system_health", "view_reports"],
    },
    "project_manager": {
        "label": "Project Manager",
        "perms": ["view_dashboard", "open_module", "view_reports", "export_reports"],
    },
    "finance_user": {
        "label": "Finance User",
        "perms": ["view_dashboard", "open_module", "view_reports"],
    },
    "hr_user": {
        "label": "HR User",
        "perms": ["view_dashboard", "open_module", "view_reports"],
    },
    "production_manager": {
        "label": "Production Manager",
        "perms": ["view_dashboard", "open_module", "view_reports", "manage_production"],
    },
    "executive_viewer": {
        "label": "Executive Viewer",
        "perms": ["view_dashboard", "open_module", "view_reports", "view_system_health"],
    },
    "normal_user": {
        "label": "Normal User",
        "perms": ["view_dashboard", "open_module"],
    },
}

DEFAULT_ROLE = "normal_user"

# --- Password policy -------------------------------------------------------
PASSWORD_MIN_LENGTH = 8


def validate_password(pw: str):
    """Return (ok: bool, message_key: str). Policy: >= 8 chars, at least one
    letter and one digit."""
    if not pw or len(pw) < PASSWORD_MIN_LENGTH:
        return False, "pw_too_short"
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        return False, "pw_need_alpha_digit"
    return True, ""


# --- Admin-managed roles overlay (DB) -------------------------------------
# Roles created/edited in Admin -> Roles live in the custom_roles table and are
# merged over the code-defined ROLES below. Cached in-process with a short TTL so
# every gunicorn worker picks up changes within a few seconds without a deploy.
import json as _json          # noqa: E402
import time as _time          # noqa: E402

_EFFECTIVE = None             # merged {role_key: {"label", "perms"}} or None
_ROLES_AT = 0.0
_ROLES_TTL = 20.0             # seconds


def load_db_roles(force: bool = False):
    """(Re)build the effective role map = code ROLES overlaid with DB custom_roles.
    Safe if the table is missing or the DB is briefly unreachable."""
    global _EFFECTIVE, _ROLES_AT
    now = _time.time()
    if not force and _EFFECTIVE is not None and (now - _ROLES_AT) < _ROLES_TTL:
        return
    merged = {k: {"label": v["label"], "perms": list(v["perms"])} for k, v in ROLES.items()}
    try:
        from app.db import get_db
        conn = get_db()
        try:
            rows = conn.execute("SELECT role_key,label,perms_json FROM custom_roles").fetchall()
        finally:
            conn.close()
        for r in rows:
            key = r["role_key"]
            if key == "super_admin":           # never let the root role be weakened
                continue
            try:
                perms = _json.loads(r["perms_json"] or "[]")
                if not isinstance(perms, list):
                    perms = []
            except Exception:
                perms = []
            merged[key] = {"label": r["label"] or key, "perms": perms}
    except Exception:
        pass                                    # table not ready yet -> code roles only
    _EFFECTIVE = merged
    _ROLES_AT = now


def refresh_db_roles():
    load_db_roles(force=True)


def effective_roles():
    load_db_roles()
    return _EFFECTIVE if _EFFECTIVE is not None else ROLES


def role_label(role_key: str) -> str:
    return effective_roles().get(role_key, {}).get("label", role_key)


def has_permission(role_key: str, permission: str) -> bool:
    role = effective_roles().get(role_key)
    if not role:
        return False
    perms = role["perms"]
    return "*" in perms or permission in perms


def all_role_choices():
    return [(k, v["label"]) for k, v in effective_roles().items()]


# --- Per-user permissions (role perms + individual grants) -----------------
def user_permissions(user):
    """Effective permission set for a user = their role's perms PLUS any extra
    permissions granted directly on the user (users.extra_perms, a JSON array).
    Returns a set which may contain '*'."""
    if not user:
        return set()
    role = effective_roles().get(user.get("role"), {})
    perms = set(role.get("perms", []))
    extra = user.get("extra_perms")
    if extra:
        try:
            e = _json.loads(extra) if isinstance(extra, str) else extra
            if isinstance(e, list):
                perms |= {p for p in e if isinstance(p, str)}
        except Exception:
            pass
    return perms


def user_has_permission(user, permission):
    perms = user_permissions(user)
    return "*" in perms or permission in perms


# --- Merge in additional module RBAC (Maintenance, Procurement) ------------
def _merge_module_rbac(module_perms, module_role_perms, module_role_labels):
    """Fold a module's permissions + role grants into the platform catalogue.
    Existing roles gain the new perms; brand-new roles are created with the
    baseline (view_dashboard + open_module) plus their grants."""
    for _p in module_perms:
        if _p not in PERMISSIONS:
            PERMISSIONS.append(_p)
    for _role_key, _perms in module_role_perms.items():
        if _role_key in ROLES:
            _existing = ROLES[_role_key]["perms"]
            if "*" not in _existing:
                for _p in _perms:
                    if _p not in _existing:
                        _existing.append(_p)
        else:
            ROLES[_role_key] = {
                "label": module_role_labels.get(_role_key, _role_key.replace("_", " ").title()),
                "perms": ["view_dashboard", "open_module"] + list(_perms),
            }


from app.maintenance.constants import (  # noqa: E402
    MAINT_PERMISSIONS, MAINT_ROLE_PERMS, MAINT_ROLE_LABELS)
from app.approvals.constants import (  # noqa: E402
    PROC_PERMISSIONS, PROC_ROLE_PERMS, PROC_ROLE_LABELS)
from app.probation.constants import (  # noqa: E402
    HR_PERMISSIONS, HR_ROLE_PERMS, HR_ROLE_LABELS)
from app.accounts.constants import (  # noqa: E402
    USERS_PERMISSIONS, USERS_ROLE_PERMS, USERS_ROLE_LABELS)

_merge_module_rbac(MAINT_PERMISSIONS, MAINT_ROLE_PERMS, MAINT_ROLE_LABELS)
_merge_module_rbac(PROC_PERMISSIONS, PROC_ROLE_PERMS, PROC_ROLE_LABELS)
_merge_module_rbac(HR_PERMISSIONS, HR_ROLE_PERMS, HR_ROLE_LABELS)
_merge_module_rbac(USERS_PERMISSIONS, USERS_ROLE_PERMS, USERS_ROLE_LABELS)


# --- Permission catalogue + built-in set (for the Admin -> Roles editor) ----
BUILTIN_ROLE_KEYS = set(ROLES.keys())   # code-defined roles (captured post-merge)

PERMISSION_LABELS = {
    "view_dashboard": "View dashboard",
    "open_module": "Open modules",
    "manage_users": "Manage users & roles",
    "manage_settings": "Manage settings",
    "view_reports": "View reports",
    "export_reports": "Export reports",
    "view_system_health": "View system health",
    "manage_integrations": "Manage integrations",
    "access_admin": "Access Admin Center",
    "manage_production": "Manage production",
    "maint_view": "Maintenance: view",
    "maint_ticket_create": "Maintenance: raise tickets",
    "maint_technician": "Maintenance: technician actions",
    "maint_manage": "Maintenance: manage / close",
    "maint_approve": "Maintenance: approvals",
    "maint_store": "Maintenance: inventory / store",
    "maint_admin": "Maintenance: admin / settings",
    "proc_view": "Procurement: view",
    "proc_create": "Procurement: raise requests",
    "proc_approve": "Procurement: approve stages",
    "proc_purchasing": "Procurement: purchasing / PO / vendors",
    "proc_admin": "Procurement: admin (any stage, budgets)",
    "prob_view": "Probation: view",
    "prob_evaluate": "Probation: evaluate (manager)",
    "prob_hr_review": "Probation: HR review & decision",
    "prob_reports": "Probation: reports & export",
    "prob_import": "Probation: import data",
    "prob_admin": "Probation: admin (config, templates, reopen)",
    "users_view": "Accounts: view registrations",
    "users_create": "Accounts: create users",
    "users_approve": "Accounts: approve registrations",
    "users_reject": "Accounts: reject registrations",
    "users_suspend": "Accounts: suspend / reactivate",
    "users_unlock": "Accounts: unlock",
    "users_assign_role": "Accounts: assign role & scope",
    "users_initiate_password_reset": "Accounts: initiate password reset",
    "users_view_security_audit": "Accounts: view security audit",
}


PERMISSION_DESC = {
    "view_dashboard": "See the command-center dashboard.",
    "open_module": "Open the integrated systems and modules.",
    "manage_users": "Create/edit users, roles and permissions.",
    "manage_settings": "Change platform settings.",
    "view_reports": "View reports.",
    "export_reports": "Export reports to CSV/PDF.",
    "view_system_health": "See system health & monitoring.",
    "manage_integrations": "Edit integrated-system URLs and settings.",
    "access_admin": "Enter the Admin Center.",
    "manage_production": "Manage production lines and data.",
    "maint_view": "See maintenance dashboards and lists.",
    "maint_ticket_create": "Raise maintenance tickets.",
    "maint_technician": "Do diagnosis, request parts, repair updates.",
    "maint_manage": "Review, assign, close tickets; manage PM.",
    "maint_approve": "Act in the maintenance approvals center.",
    "maint_store": "Inventory: issue/receive/adjust stock.",
    "maint_admin": "Maintenance settings, master data, matrix.",
    "proc_view": "See procurement requests and lists.",
    "proc_create": "Raise purchase requests.",
    "proc_approve": "Approve/reject a stage you're eligible for.",
    "proc_purchasing": "Issue POs, manage vendors and quotes.",
    "proc_admin": "Act on any stage; manage budgets/matrix.",
    "prob_view": "See probation dashboards and cases within your scope.",
    "prob_evaluate": "Evaluate employees assigned to you (draft, submit, correct).",
    "prob_hr_review": "Create/manage cases and take the final HR decision.",
    "prob_reports": "View and export probation reports and analytics.",
    "prob_import": "Import the employee roster or migrate legacy probation data.",
    "prob_admin": "Configure templates, workflow and thresholds; reopen locked cases.",
    "users_view": "See pending registrations and account details.",
    "users_create": "Create user accounts directly.",
    "users_approve": "Approve a registration and assign a role/scope.",
    "users_reject": "Reject a registration with a mandatory reason.",
    "users_suspend": "Suspend or reactivate an account.",
    "users_unlock": "Unlock a locked account.",
    "users_assign_role": "Assign an authorised role and organisation scope.",
    "users_initiate_password_reset": "Send a secure password-reset email.",
    "users_view_security_audit": "View the account security audit trail.",
}


def permission_label(p):
    return PERMISSION_LABELS.get(p, p.replace("_", " ").capitalize())


def permission_desc(p):
    return PERMISSION_DESC.get(p, "")


def permission_catalogue():
    """Permissions grouped for the editor: {group: [(key, label, desc), ...]}."""
    groups = {"Platform": [], "Maintenance": [], "Procurement": [],
              "HR / Probation": [], "Accounts / Users": []}
    for p in PERMISSIONS:
        entry = (p, permission_label(p), permission_desc(p))
        if p.startswith("maint_"):
            groups["Maintenance"].append(entry)
        elif p.startswith("proc_"):
            groups["Procurement"].append(entry)
        elif p.startswith("prob_"):
            groups["HR / Probation"].append(entry)
        elif p.startswith("users_"):
            groups["Accounts / Users"].append(entry)
        else:
            groups["Platform"].append(entry)
    return groups
