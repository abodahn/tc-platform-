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


def role_label(role_key: str) -> str:
    return ROLES.get(role_key, {}).get("label", role_key)


def has_permission(role_key: str, permission: str) -> bool:
    role = ROLES.get(role_key)
    if not role:
        return False
    perms = role["perms"]
    return "*" in perms or permission in perms


def all_role_choices():
    return [(k, v["label"]) for k, v in ROLES.items()]


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
