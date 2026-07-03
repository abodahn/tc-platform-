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

_merge_module_rbac(MAINT_PERMISSIONS, MAINT_ROLE_PERMS, MAINT_ROLE_LABELS)
_merge_module_rbac(PROC_PERMISSIONS, PROC_ROLE_PERMS, PROC_ROLE_LABELS)


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
}


def permission_label(p):
    return PERMISSION_LABELS.get(p, p.replace("_", " ").capitalize())


def permission_catalogue():
    """Permissions grouped for the roles editor: {group: [(key, label), ...]}."""
    groups = {"Platform": [], "Maintenance": [], "Procurement": []}
    for p in PERMISSIONS:
        if p.startswith("maint_"):
            groups["Maintenance"].append((p, permission_label(p)))
        elif p.startswith("proc_"):
            groups["Procurement"].append((p, permission_label(p)))
        else:
            groups["Platform"].append((p, permission_label(p)))
    return groups
