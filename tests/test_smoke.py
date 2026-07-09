"""
TC Platform — backend smoke tests.

    pip install pytest
    pytest

Covers: app boot, public endpoints, auth guard, login, RBAC, health API,
integration registry and admin edit.
"""
import sys
from pathlib import Path

import pytest

# Make the project importable when pytest runs from /tests
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from config import Config    # noqa: E402


@pytest.fixture()
def client():
    app = create_app()
    app.config["TESTING"] = True
    # reset the in-memory login throttle so tests are independent
    from app.routes import auth as auth_routes
    auth_routes._fails.clear()
    with app.test_client() as c:
        yield c


def csrf(client):
    """Ensure a CSRF token exists in the session and return it."""
    client.get("/login")
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def login(client, user="admin", pwd=None):
    pwd = pwd or Config.ADMIN_PASSWORD
    return client.post("/login",
                       data={"username": user, "password": pwd, "_csrf": csrf(client)},
                       follow_redirects=False)


# ---------------- Public / guards ----------------
def test_app_health_public(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert b"TC Platform" in r.data


def test_dashboard_requires_auth(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


# ---------------- Auth ----------------
def test_login_success_and_dashboard(client):
    r = login(client)
    assert r.status_code == 302
    r2 = client.get("/")
    assert r2.status_code == 200
    assert b"Command Center" in r2.data or b"command_center" in r2.data


def test_login_failure(client):
    r = login(client, pwd="wrong-password")
    assert r.status_code == 200  # re-renders login with flash
    # still unauthenticated
    assert client.get("/", follow_redirects=False).status_code == 302


# ---------------- Security: CSRF, lockout, password policy ----------------
def test_csrf_blocks_post_without_token(client):
    # POST with no _csrf token is rejected
    r = client.post("/login", data={"username": "admin", "password": Config.ADMIN_PASSWORD})
    assert r.status_code == 400


def test_login_lockout_after_repeated_failures(client):
    token = csrf(client)
    for _ in range(5):
        client.post("/login", data={"username": "lockme", "password": "x", "_csrf": token})
    r = client.post("/login", data={"username": "lockme", "password": "x", "_csrf": token},
                    follow_redirects=True)
    assert b"too_many_attempts" in r.data or b"Too many" in r.data


def test_password_policy_rejects_weak(client):
    login(client)
    r = client.post("/admin/users/add", data={
        "username": "weakuser", "password": "short", "role": "normal_user",
        "_csrf": csrf(client),
    }, follow_redirects=True)
    assert b"pw_too_short" in r.data or b"at least 8" in r.data


def test_change_own_password(client):
    # Reset the demo 'exec' account to a known password first, so this test is
    # idempotent regardless of prior runs.
    from app.db import get_db
    from werkzeug.security import generate_password_hash
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE username='exec'",
                 (generate_password_hash("Tc@12345"),))
    conn.commit()
    conn.close()

    login(client, user="exec", pwd="Tc@12345")
    r = client.post("/profile/password", data={
        "current_password": "Tc@12345", "new_password": "NewPass123",
        "confirm_password": "NewPass123", "_csrf": csrf(client),
    }, follow_redirects=True)
    assert b"pw_changed" in r.data
    # revert to keep the demo account password stable
    conn = get_db()
    conn.execute("UPDATE users SET password_hash=? WHERE username='exec'",
                 (generate_password_hash("Tc@12345"),))
    conn.commit()
    conn.close()


# ---------------- Reports CSV export ----------------
def test_csv_export(client):
    login(client)
    r = client.get("/reports/systems.csv")   # Reports Center: /reports/<key>.<fmt>
    assert r.status_code == 200
    assert "text/csv" in r.headers["Content-Type"]
    assert b"Key,Name" in r.data            # CSV uses human column headers


# ---------------- Authenticated pages ----------------
@pytest.mark.parametrize("path", [
    "/launcher", "/module/itsm", "/module/ai_hub", "/module/finance",
    "/reports", "/health", "/roadmap", "/admin/",
])
def test_pages_load(client, path):
    login(client)
    assert client.get(path).status_code == 200


def test_status_api(client):
    login(client)
    data = client.get("/api/status").get_json()
    assert "itsm" in data and "status" in data["itsm"]


def test_unknown_module_404(client):
    login(client)
    assert client.get("/module/does-not-exist").status_code == 404


def test_integrated_module_embeds_app(client):
    # The platform concept: an integrated module renders the app in an iframe.
    login(client)
    html = client.get("/module/itsm").get_data(as_text=True)
    assert 'id="appFrame"' in html or "embed-offline" in html  # iframe, or offline panel


def test_integrated_module_details_view(client):
    login(client)
    html = client.get("/module/itsm?view=details").get_data(as_text=True)
    assert "Launch" in html or "System Health" in html  # details/launch page


# ---------------- RBAC ----------------
def test_normal_user_blocked_from_admin(client):
    # demo user 'agent' is a service_desk_agent without access_admin
    login(client, user="agent", pwd="Admin@1122")
    assert client.get("/admin/").status_code == 403


# ---------------- Production module (real CRUD) ----------------
def test_production_page_loads(client):
    login(client)
    r = client.get("/production/")
    assert r.status_code == 200
    assert b"Production" in r.data


def test_production_module_redirects_to_app(client):
    login(client)
    r = client.get("/module/production", follow_redirects=False)
    assert r.status_code == 302
    assert "/production" in r.headers["Location"]


def test_production_add_and_delete_line(client):
    login(client)
    token = csrf(client)
    # add
    r = client.post("/production/lines/add", data={
        "name": "Pytest Line", "area": "Test", "status": "running",
        "shift": "A", "target_output": "100", "actual_output": "90", "operators": "2",
        "_csrf": token,
    }, follow_redirects=True)
    assert b"Pytest Line" in r.data
    # find its id and delete to keep state clean
    from app.db import get_db
    conn = get_db()
    row = conn.execute("SELECT id FROM production_lines WHERE name='Pytest Line'").fetchone()
    conn.close()
    assert row is not None
    client.post(f"/production/lines/{row['id']}/delete", data={"_csrf": token})
    conn = get_db()
    gone = conn.execute("SELECT id FROM production_lines WHERE name='Pytest Line'").fetchone()
    conn.close()
    assert gone is None


def test_production_edit_requires_permission(client):
    # 'agent' can view but not edit
    login(client, user="agent", pwd="Admin@1122")
    assert client.get("/production/").status_code == 200
    r = client.post("/production/lines/add", data={"name": "Nope", "_csrf": csrf(client)})
    assert r.status_code == 403


# ---------------- Search ----------------
def test_search_finds_module(client):
    login(client)
    data = client.get("/search?q=monitoring").get_json()
    assert any("monitoring" in (r.get("url", "") + r.get("name", "")).lower()
               for r in data["results"])
