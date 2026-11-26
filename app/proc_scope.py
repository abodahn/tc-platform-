"""
TC Platform — "procurement-only" scope.

THE RULE. A user is procurement-only when their effective permissions (role
perms + DB role overlay + per-user extra_perms, i.e. security.user_permissions)
hold proc_view and NOTHING else except other proc_* permissions, the generic
baseline below and the read-only warehouse view (wh_view, which buyers carry to
check stock before they order). Such a user only ever works in /procurement, so
the sidebar, '/', global search, the bell and /my-work show procurement only
(plus the read-only warehouse pages when they hold wh_view, and the IT Service
Desk so they can still raise IT tickets). Every other page they could reach by
URL (Orders, the command center, launcher, AI, Smart Factory, BI, registry, ...
— all gated by the generic perms alone) sends them back to /procurement.

Built-in roles affected: normal_user (the default/self-signup role),
purchasing_manager, finance_manager, financial_controller, cfo,
supply_chain_director, hr_director, financial_director (asserted in
app/tests_proconly.py). Top management (SENIOR_ROLES) keeps the whole platform,
the Command Center dashboard included — owner decision 2026-11-26.

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
# Read-only companions of procurement: kept (nav + pages) when held, never a reason
# to leave the scope. wh_manage / wh_approve are real warehouse work and are not here.
COMPANION_PERMS = frozenset({"wh_view"})
# Top management keeps the dashboard and every page it had (owner decision 2026-11-26).
SENIOR_ROLES = frozenset({"ceo", "managing_director", "board", "plant_director"})

# Sidebar keys a procurement-only user may see: the procurement section, My Work,
# the IT Service Desk and the read-only warehouse pages (the nav builder still
# checks each item's own permission).
NAV_KEYS = frozenset(
    [it[0] for sec in NAV if sec["section"] == "nav.procurement_cycle" for it in sec["items"]]
    + [it[0] for sec in NAV for it in sec["items"] if it[4] in COMPANION_PERMS]
    + ["my_work", "itsm"])

# Everything a procurement-only user may still reach. Procurement itself, their
# own account (profile, password, signature, prefs, sign-in/out), and the shell
# the page needs (static, bell feed, search box, type-ahead, push, assistant).
# reports_hub gates every report on its module's own permission, so such a user
# only ever finds the procurement (and, with wh_view, warehouse) reports there.
_OK_BLUEPRINTS = frozenset({"approvals", "proc_aging", "reports_hub", "auth", "accounts",
                            "push", "garamento"})
_OK_ENDPOINTS = frozenset({
    "static", "api.health", "main.service_worker", "main.my_work", "main.search",
    "main.notifications_feed", "main.mark_notifications_read", "main.prefs",
    "main.profile", "main.change_password", "main.save_notif_prefs",
    "main.save_signature", "main.quick_signature", "main.clear_signature",
    "lookup.departments", "lookup.request_for", "lookup.wh_stock",
})
# /api/lookup/<kind>: only the sources procurement pages use (not orders, machines).
_OK_LOOKUPS = frozenset({"items", "vendors", "users"})
# Endpoints keyed by system: the IT Service Desk only.
_OK_SYSTEM_ENDPOINTS = frozenset({"main.module", "sso.launch"})

# Shown once when a URL outside the scope bounces (old bookmark / bell / email link).
OUT_OF_SCOPE_MSG = "This page is outside your procurement access, so you were taken to Procurement."


def is_procurement_only(user):
    if not user or system_scope(user) is not None or user.get("role") in SENIOR_ROLES:
        return False
    perms = user_permissions(user)
    if "*" in perms:
        return False
    proc = {p for p in perms if p.startswith("proc_")}
    # proc_view required: without it /procurement itself is a 403.
    return "proc_view" in proc and not (perms - proc - GENERIC_PERMS - COMPANION_PERMS)


def _allowed(user):
    """The per-request part of the allow-list (the static part is checked in _guard)."""
    ep, args = request.endpoint, request.view_args or {}
    if ep == "lookup.lookup":
        return args.get("kind") in _OK_LOOKUPS
    if ep in _OK_SYSTEM_ENDPOINTS:
        return args.get("key") == "itsm"
    if request.blueprint == "warehouse":
        return "wh_view" in user_permissions(user)
    return False


def _guard():
    if request.endpoint is None or request.endpoint in _OK_ENDPOINTS \
            or request.blueprint in _OK_BLUEPRINTS:
        return None     # cheap path, no user lookup; endpoint None = the normal 404
    from app.auth import current_user
    user = current_user()
    if user and str(user.get("id")) != str(session.get("uid")):
        # g outlived a request: only in scripts/tests that share one app context and
        # drop g.user in a before_request registered AFTER this one. Never in a real
        # request (fresh g each time), so no extra query in production.
        g.pop("user", None)
        user = current_user()
    if not is_procurement_only(user) or _allowed(user):
        return None
    if request.method in ("GET", "HEAD"):
        if request.path != "/":
            from app.routes.approvals import flash   # flashes in the reader's language
            flash(OUT_OF_SCOPE_MSG, "info")
        return redirect(url_for("approvals.index"))
    abort(403)


def init_app(app):
    app.before_request(_guard)
