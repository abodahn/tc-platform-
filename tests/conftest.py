"""
Shared pytest fixtures & helpers for the TC Platform test suite.

Existing test files (test_smoke.py, test_maintenance.py) define their own local
fixtures and are unaffected; new test files use the fixtures/helpers here.
"""
import sys
from pathlib import Path

import pytest
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app          # noqa: E402
from app.db import get_db           # noqa: E402
from config import Config           # noqa: E402


@pytest.fixture()
def app():
    a = create_app()
    a.config["TESTING"] = True
    return a


@pytest.fixture()
def client(app):
    # reset the in-memory login throttle so tests are independent
    try:
        from app.routes import auth as auth_routes
        auth_routes._fails.clear()
    except Exception:
        pass
    with app.test_client() as c:
        yield c


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def get_csrf(client):
    """Ensure a CSRF token exists in the session and return it."""
    client.get("/login")
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def ensure_user(app, username, role, password="Tc@12345"):
    """Create (or update) a user with the given role. Idempotent."""
    with app.app_context():
        conn = get_db()
        try:
            row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if row:
                conn.execute("UPDATE users SET role=?, password_hash=?, is_active=1 WHERE username=?",
                             (role, generate_password_hash(password), username))
            else:
                conn.execute(
                    "INSERT INTO users (username, password_hash, full_name, role, created_at) "
                    "VALUES (?,?,?,?,?)",
                    (username, generate_password_hash(password), username.replace("_", " ").title(),
                     role, "2026-01-01 00:00:00"))
            conn.commit()
        finally:
            conn.close()


def login_as(client, app, role):
    """Log the client in as a freshly-ensured user with `role`. Returns response."""
    username = "t_" + role
    ensure_user(app, username, role)
    return client.post("/login",
                       data={"username": username, "password": "Tc@12345", "_csrf": get_csrf(client)},
                       follow_redirects=False)


def login_admin(client):
    """Log in as the seeded super-admin."""
    return client.post("/login",
                       data={"username": Config.ADMIN_USER, "password": Config.ADMIN_PASSWORD,
                             "_csrf": get_csrf(client)},
                       follow_redirects=False)
