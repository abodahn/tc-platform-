"""
RBAC access matrix — exercises every GET route against every role.

Guarantees:
  * No route returns 500 for any role (catches template/endpoint regressions
    like a bad url_for or an undefined name).
  * Unauthenticated users are redirected to /login on protected routes.
  * super_admin can reach every route (200/302, never 403/500).
  * permission-guarded routes return 403 for roles lacking the permission,
    EXCEPT for scope-locked roles (see SCOPE_OPEN_BLUEPRINTS below), which are
    redirected off an out-of-scope blueprint before the route ever runs.
"""
from urllib.parse import urlparse

import pytest
from werkzeug.routing import RequestRedirect

from app.security import ROLES, has_permission, system_scope
from _support import login_as, login_admin

# Public GET routes (no auth needed).
PUBLIC = ["/api/health", "/sw.js", "/login"]

# Login-only GET routes (any authenticated user passes the route guard; some
# maintenance routes additionally enforce maint_* perms internally -> 403).
LOGIN = [
    "/", "/notifications/feed", "/profile", "/roadmap", "/search?q=test", "/api/status",
    "/maintenance/", "/maintenance/ai", "/maintenance/approvals", "/maintenance/calendar",
    "/maintenance/easy", "/maintenance/floor", "/maintenance/import", "/maintenance/machines",
    "/maintenance/pm", "/maintenance/reports", "/maintenance/requests", "/maintenance/scan",
    "/maintenance/settings", "/maintenance/spares", "/maintenance/stock", "/maintenance/tickets",
    "/maintenance/tickets/new",
]

# Permission-guarded GET routes (route-level abort(403) when lacking the perm).
PERM = [
    ("/health", "view_system_health"),
    ("/launcher", "open_module"),
    ("/reports", "view_reports"),
    ("/admin/", "access_admin"),
    ("/production/", "open_module"),
    ("/module/itsm", "open_module"),
]

ALL_GET = PUBLIC + LOGIN + [p for p, _ in PERM]
ROLE_KEYS = sorted(ROLES.keys())

# The ONLY blueprints a scope-locked role (security.ROLE_SYSTEM_SCOPE, e.g.
# itsm_user) may reach: infra + the routes that launch their own system.
# routes/main._enforce_system_scope bounces them off everything else with a
# redirect, BEFORE the route's own permission_required can answer. Duplicated
# here on purpose: if the product ever widens that set, this test goes red.
SCOPE_OPEN_BLUEPRINTS = {None, "main", "auth", "sso", "accounts", "api", "garamento"}


def _blueprint_of(app, path):
    """Blueprint name that owns `path` (None for app-level routes)."""
    urls = app.url_map.bind("localhost")
    target = urlparse(path).path
    try:
        endpoint, _ = urls.match(target, method="GET")
    except RequestRedirect as rr:                       # strict-slash variant
        endpoint, _ = urls.match(urlparse(rr.new_url).path, method="GET")
    return endpoint.rsplit(".", 1)[0] if "." in endpoint else None


# ---------------- Unauthenticated ----------------
@pytest.mark.parametrize("path", [p for p in ALL_GET if p not in PUBLIC])
def test_protected_routes_redirect_anonymous(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 302, f"anon GET {path} should redirect, got {r.status_code}"
    assert "/login" in r.headers.get("Location", ""), f"anon GET {path} should go to /login"


@pytest.mark.parametrize("path", PUBLIC)
def test_public_routes_open_anonymous(client, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code == 200, f"public GET {path} -> {r.status_code}"


# ---------------- super_admin can reach everything ----------------
@pytest.mark.parametrize("path", ALL_GET)
def test_super_admin_reaches_everything(client, app, path):
    login_admin(client)
    r = client.get(path, follow_redirects=False)
    assert r.status_code != 500, f"super_admin GET {path} -> 500 (server error!)"
    assert r.status_code in (200, 302), f"super_admin GET {path} -> {r.status_code}"


# ---------------- Full role x route matrix ----------------
@pytest.mark.parametrize("role", ROLE_KEYS)
def test_role_access_matrix(client, app, role):
    login_as(client, app, role)
    # login routes: never crash; either render, redirect, or 403 (internal perm)
    for path in PUBLIC + LOGIN:
        r = client.get(path, follow_redirects=False)
        assert r.status_code != 500, f"role={role} GET {path} -> 500"
        assert r.status_code in (200, 302, 403), f"role={role} GET {path} -> {r.status_code}"
    # permission routes: precise enforcement
    scope = system_scope({"role": role})    # None => unrestricted role
    for path, perm in PERM:
        r = client.get(path, follow_redirects=False)
        assert r.status_code != 500, f"role={role} GET {path} -> 500"
        if scope is not None and _blueprint_of(app, path) not in SCOPE_OPEN_BLUEPRINTS:
            # Scope-locked role on an out-of-scope blueprint: the scope guard
            # must redirect them AWAY before the route runs. Exact 302 on
            # purpose — a 200 means they reached the page, and even a 403 would
            # mean the scope guard stopped firing and only RBAC saved us.
            assert r.status_code == 302, \
                f"role={role} is scope-locked to {sorted(scope)}; GET {path} " \
                f"must be redirected off the blueprint, got {r.status_code}"
            dest = urlparse(r.headers.get("Location", "")).path or "/"
            prefix = urlparse(path).path.rstrip("/") or "/"
            assert not dest.startswith(prefix), \
                f"role={role} GET {path} redirected back into the blueprint ({dest})"
        elif has_permission(role, perm):
            assert r.status_code in (200, 302), \
                f"role={role} HAS {perm} but GET {path} -> {r.status_code}"
        else:
            assert r.status_code == 403, \
                f"role={role} lacks {perm}; GET {path} should be 403, got {r.status_code}"
