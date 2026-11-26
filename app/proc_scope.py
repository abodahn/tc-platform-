"""
TC Platform — "procurement-only" scope.

THE RULE. A user is procurement-only when their effective permissions (role
perms + DB role overlay + per-user extra_perms, i.e. security.user_permissions)
hold at least one proc_* permission and NOTHING else except the generic
baseline below. Such a user only ever works in /procurement, so the sidebar,
'/', global search, the bell and /my-work show procurement only, and every
other page they could reach by URL (Orders, the command center, launcher, AI,
Smart Factory, BI, registry, ... — all gated by the generic perms alone) sends
them back to /procurement.

Computed from permissions, never stored: grant a user any other module's
permission (role or extra_perms) and they are unaffected. super_admin ('*') and
roles with their own system scope (ROLE_SYSTEM_SCOPE, e.g. itsm_user) are never
procurement-only.
"""
from flask import abort, g, redirect, request, session, url_for

from app.navigation import NAV
from app.security import system_scope, user_permissions

# The baseline almost every role carries. Holding these says nothing about which
# module a user works in, so they do not stop a user being procurement-only.
# Every other permission (admin/health/integration/production ones included)
# belongs to some module, and holding one leaves the user unaffected.
GENERIC_PERMS = frozenset({"view_dashboard", "open_module", "view_reports", "export_reports"})

# Sidebar keys a procurement-only user may see: the procurement section + My Work.
NAV_KEYS = frozenset(
    [it[0] for sec in NAV if sec["section"] == "nav.procurement_cycle" for it in sec["items"]]
    + ["my_work"])

# Everything a procurement-only user may still reach. Procurement itself, their
# own account (profile, password, signature, prefs, sign-in/out), and the shell
# the page needs (static, bell feed, search box, type-ahead, push, assistant).
# reports_hub gates every report on its module's own permission, so such a user
# only ever finds the procurement reports there.
_OK_BLUEPRINTS = frozenset({"approvals", "proc_aging", "reports_hub", "auth", "accounts",
                            "push", "garamento", "lookup"})
_OK_ENDPOINTS = frozenset({
    "static", "api.health", "main.service_worker", "main.my_work", "main.search",
    "main.notifications_feed", "main.mark_notifications_read", "main.prefs",
    "main.profile", "main.change_password", "main.save_notif_prefs",
    "main.save_signature", "main.quick_signature", "main.clear_signature",
})


def is_procurement_only(user):
    if not user or system_scope(user) is not None:
        return False
    perms = user_permissions(user)
    if "*" in perms:
        return False
    proc = {p for p in perms if p.startswith("proc_")}
    return bool(proc) and not (perms - proc - GENERIC_PERMS)


def _guard():
    # Unknown URLs (endpoint None) fall through to the normal 404.
    if request.endpoint is None or request.endpoint in _OK_ENDPOINTS \
            or request.blueprint in _OK_BLUEPRINTS:
        return None
    from app.auth import current_user
    user = current_user()
    if user and user.get("id") != session.get("uid"):
        # g outlived a request (scripts/tests sharing one app context): re-read.
        g.pop("user", None)
        user = current_user()
    if not is_procurement_only(user):
        return None
    if request.method in ("GET", "HEAD"):
        return redirect(url_for("approvals.index"))
    abort(403)


def init_app(app):
    app.before_request(_guard)
