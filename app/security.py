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
                  "manage_production", "cmp_view", "cmp_manage"],
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
        "perms": ["view_dashboard", "open_module", "view_reports", "export_reports",
                  "wh_view", "wh_manage"],
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
        "perms": ["view_dashboard", "open_module", "view_reports",
                  "cost_view", "cost_manage", "shp_view", "wh_view"],
    },
    "hr_user": {
        "label": "HR User",
        "perms": ["view_dashboard", "open_module", "view_reports",
                  "ppl_view", "ppl_manage", "ppl_approve"],
    },
    # Quality is deliberately its OWN role, not a production grant — an inspector
    # must be independent of the line being inspected (see production_manager above).
    "quality_inspector": {
        "label": "Quality Inspector",
        "perms": ["view_dashboard", "open_module", "view_reports",
                  "qc_view", "qc_inspect", "cut_view", "mes_view", "wsh_view"],
    },
    "storekeeper": {
        "label": "Storekeeper",
        "perms": ["view_dashboard", "open_module", "view_reports",
                  "wh_view", "wh_manage", "maint_view", "maint_store", "shp_view"],
    },
    "compliance_officer": {
        "label": "Compliance Officer",
        "perms": ["view_dashboard", "open_module", "view_reports", "cmp_view", "cmp_manage",
                  # traceability is the material/product side of the same job
                  "trc_view", "trc_manage", "qc_view"],
    },
    "production_manager": {
        "label": "Production Manager",
        "perms": ["view_dashboard", "open_module", "view_reports", "manage_production",
                  # the shop-floor set: plan the lines, run them, cut, and see
                  # material + cost without being able to change money.
                  # NOTE deliberately NO qc_inspect — segregation of duties: production
                  # reads the quality numbers, it does not sign off its own lots.
                  # qc_inspect belongs to a quality/QC role (create one in Admin > Roles).
                  "pln_view", "pln_plan", "mes_view", "mes_entry",
                  "cut_view", "cut_manage", "qc_view",
                  "wh_view", "cost_view", "plm_view", "wsh_view", "shp_view"],
    },
    "executive_viewer": {
        "label": "Executive Viewer",
        "perms": ["view_dashboard", "open_module", "view_reports", "view_system_health",
                  # read-only across the whole operation — never a manage/approve grant
                  "cmp_view", "wh_view", "cost_view", "qc_view", "pln_view", "cut_view",
                  "plm_view", "mes_view", "ppl_view", "trc_view", "shp_view", "wsh_view"],
    },
    "normal_user": {
        "label": "Normal User",
        "perms": ["view_dashboard", "open_module"],
    },
    # ITSM-only account: can sign in and open the IT Service Desk, nothing more.
    # Deliberately has NO view_dashboard (so the command center, AI Intelligence,
    # Smart Factory and roadmap pages 403) and is scope-locked to the 'itsm'
    # system via ROLE_SYSTEM_SCOPE below (so the nav, launcher and launch routes
    # only ever expose ITSM). It gains nothing from any module's RBAC merge.
    "itsm_user": {
        "label": "ITSM User",
        "perms": ["open_module"],
    },
}

DEFAULT_ROLE = "normal_user"

# --- Per-role system scope --------------------------------------------------
# Roles listed here may reach ONLY these system / nav keys, regardless of their
# baseline open_module permission. Keys match navigation.py item keys AND
# systems.key. A role absent here (value None) is unrestricted. Enforced in the
# nav builder, the launcher, and the /module + /sso/launch routes.
ROLE_SYSTEM_SCOPE = {
    "itsm_user": {"itsm"},
}


def system_scope(user):
    """Return the set of system/nav keys a scope-locked user may reach, or None
    when the user is unrestricted (sees everything their perms allow)."""
    if not user:
        return None
    return ROLE_SYSTEM_SCOPE.get(user.get("role"))


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


# --- The manufacturing modules' permissions ---------------------------------
# These twelve grant their permissions by being written straight into ROLES
# above, so they never went through _merge_module_rbac and their names never
# reached PERMISSIONS. That list is not documentation: app/routes/admin.py
# filters every submitted grant through it
#     perms = [p for p in f.getlist("perms[]") if p in PERMISSIONS]
# so an admin editing a role or a user's extra permissions could tick any of
# these 22 boxes, save, get no error, and have the grant silently dropped.
# Each module owns its own permission names; we only fold them in here, so a
# module that adds a permission tomorrow is registered without touching this
# file. tests/test_security_rbac.py asserts no role grants an unregistered
# permission, which is what caught the omission.
_MODULE_PERM_SOURCES = [
    ("app.warehouse.constants", "WH_PERMISSIONS"),
    ("app.costing.constants", "COST_PERMISSIONS"),
    ("app.quality.constants", "QC_PERMISSIONS"),
    ("app.planning.constants", "PLN_PERMISSIONS"),
    ("app.cutroom.constants", "CUT_PERMISSIONS"),
    ("app.plm.constants", "PLM_PERMISSIONS"),
    ("app.mes.constants", "MES_PERMISSIONS"),
    ("app.people.constants", "PPL_PERMISSIONS"),
    ("app.trace.constants", "TRC_PERMISSIONS"),
    ("app.shipping.constants", "SHP_PERMISSIONS"),
    ("app.wash.constants", "WSH_PERMISSIONS"),
    ("app.compliance.constants", "CMP_PERMS"),   # dict: code -> description
]

_MODULE_PERM_DESC = {}          # descriptions any module supplied alongside its codes

for _mod_path, _attr in _MODULE_PERM_SOURCES:
    try:
        _mod = __import__(_mod_path, fromlist=[_attr])
        _catalogue = getattr(_mod, _attr)
    except Exception:
        continue          # a module absent from this build must not break boot
    for _p in _catalogue:                       # iterating a dict yields its keys
        if _p not in PERMISSIONS:
            PERMISSIONS.append(_p)
    if isinstance(_catalogue, dict):            # code -> description form
        _MODULE_PERM_DESC.update(_catalogue)


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
    "cmp_view": "Compliance: view audits, findings & certificates",
    "cmp_manage": "Compliance: manage audits, CAPs & certificates",
    "wh_view": "Warehouse: view raw-material rolls & finished goods",
    "wh_manage": "Warehouse: receive, issue & adjust material",
    "cost_view": "Costing: view BOM, cost sheets & variance",
    "cost_manage": "Costing: manage BOM and cost sheets",
    "qc_view": "Quality: view inspections & defect analytics",
    "qc_inspect": "Quality: record inspections & defects",
    "pln_view": "Planning: view capacity & line loading",
    "pln_plan": "Planning: allocate orders to lines",
    "cut_view": "Cut room: view lays & fabric utilisation",
    "cut_manage": "Cut room: record lays & cut plan",
    "plm_view": "PLM: view styles, tech packs & samples",
    "plm_manage": "PLM: manage styles, tech packs & BOM",
    "plm_approve": "PLM: approve styles & sample rounds",
    "mes_view": "Shop floor: view hourly output, OEE & WIP",
    "mes_entry": "Shop floor: record output, downtime & bundle moves",
    "ppl_view": "People: view attendance, leave & skills",
    "ppl_manage": "People: record attendance, skills & piece-rate",
    "ppl_approve": "People: approve leave requests",
    "trc_view": "Traceability: view chain of custody & passports",
    "trc_manage": "Traceability: manage partners, lots & certificates",
    "shp_view": "Shipping: view shipments & export documents",
    "shp_manage": "Shipping: create shipments & packing lists",
    "wsh_view": "Wash: view recipes, batches & lab dips",
    "wsh_manage": "Wash: manage recipes & batches",
    "wsh_approve": "Wash: approve recipes & lab dips",
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

# Descriptions a module shipped next to its own permission codes (see
# _MODULE_PERM_SOURCES). setdefault, so a description written by hand above
# always wins over the module's terser one.
for _p, _d in _MODULE_PERM_DESC.items():
    PERMISSION_DESC.setdefault(_p, _d)


def permission_label(p):
    return PERMISSION_LABELS.get(p, p.replace("_", " ").capitalize())


def permission_desc(p):
    return PERMISSION_DESC.get(p, "")


# Permission prefix -> the group it belongs to in the Admin > Roles editor.
# Without this the 22 manufacturing permissions all land in "Platform", which
# turns the first group into an unreadable dumping ground of 30-odd checkboxes.
_PERM_GROUPS = [
    ("maint_", "Maintenance"),
    ("proc_", "Procurement"),
    ("prob_", "HR / Probation"),
    ("users_", "Accounts / Users"),
    ("ppl_", "HR / Probation"),
    ("wh_", "Warehouse & Shipping"),
    ("shp_", "Warehouse & Shipping"),
    ("trc_", "Warehouse & Shipping"),
    ("pln_", "Planning & Costing"),
    ("cost_", "Planning & Costing"),
    ("plm_", "Product Development"),
    ("cut_", "Production Floor"),
    ("mes_", "Production Floor"),
    ("wsh_", "Production Floor"),
    ("qc_", "Quality & Compliance"),
    ("cmp_", "Quality & Compliance"),
]

# Fixed display order; every group named above must appear here.
_PERM_GROUP_ORDER = ["Platform", "Planning & Costing", "Product Development",
                     "Production Floor", "Quality & Compliance",
                     "Warehouse & Shipping", "Maintenance", "Procurement",
                     "HR / Probation", "Accounts / Users"]


def permission_catalogue():
    """Permissions grouped for the editor: {group: [(key, label, desc), ...]}.
    Groups are returned in a fixed order and empty ones are dropped, so a build
    without a given module simply shows fewer sections."""
    groups = {name: [] for name in _PERM_GROUP_ORDER}
    for p in PERMISSIONS:
        entry = (p, permission_label(p), permission_desc(p))
        for prefix, group in _PERM_GROUPS:
            if p.startswith(prefix):
                groups[group].append(entry)
                break
        else:
            groups["Platform"].append(entry)
    return {name: items for name, items in groups.items() if items}
