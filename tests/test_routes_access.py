"""
RBAC access matrix — exercises every GET route against every role.

Guarantees:
  * No route returns 500 for any role (catches template/endpoint regressions
    like a bad url_for or an undefined name).
  * Unauthenticated users are redirected to /login on protected routes.
  * super_admin can reach every route (200/302, never 403/500).
  * permission-guarded routes return 403 for roles lacking the permission.
"""
import pytest

from app.security import ROLES, has_permission
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
    for path, perm in PERM:
        r = client.get(path, follow_redirects=False)
        assert r.status_code != 500, f"role={role} GET {path} -> 500"
        if has_permission(role, perm):
            assert r.status_code in (200, 302), \
                f"role={role} HAS {perm} but GET {path} -> {r.status_code}"
        else:
            assert r.status_code == 403, \
                f"role={role} lacks {perm}; GET {path} should be 403, got {r.status_code}"
