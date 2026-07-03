"""Admin user-management tests (edit user: update fields, reset password,
self-lockout guard) on an isolated temp DB."""
import pytest

from _support import login_admin, get_csrf


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "admin.db")
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True
    c = a.test_client()
    login_admin(c)
    return a, c


def _uid(a, username):
    with a.app_context():
        from app.db import get_db
        conn = get_db()
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        conn.close()
        return row["id"] if row else None


def test_edit_user_updates_fields(app_client):
    a, c = app_client
    uid = _uid(a, "warehouse")
    assert uid
    r = c.post(f"/admin/users/{uid}/edit", data={
        "_csrf": get_csrf(c), "full_name": "Mahmoud Hassan",
        "email": "mahmoud.hassan@tcgarments.com", "role": "purchasing_manager",
        "password": ""}, follow_redirects=True)
    assert r.status_code == 200
    with a.app_context():
        from app.db import get_db
        conn = get_db()
        row = conn.execute("SELECT full_name, email, role FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
    assert row["full_name"] == "Mahmoud Hassan"
    assert row["email"] == "mahmoud.hassan@tcgarments.com"
    assert row["role"] == "purchasing_manager"


def test_edit_user_password_reset(app_client):
    a, c = app_client
    uid = _uid(a, "finance")
    c.post(f"/admin/users/{uid}/edit", data={
        "_csrf": get_csrf(c), "full_name": "Fin", "email": "", "role": "finance_manager",
        "password": "NewPass123"}, follow_redirects=True)
    # the new password should authenticate
    other = a.test_client()
    r = other.post("/login", data={"username": "finance", "password": "NewPass123",
                                   "_csrf": get_csrf(other)}, follow_redirects=False)
    assert r.status_code in (301, 302)  # successful login redirects


def test_edit_weak_password_rejected(app_client):
    a, c = app_client
    uid = _uid(a, "cfo")
    c.post(f"/admin/users/{uid}/edit", data={
        "_csrf": get_csrf(c), "full_name": "C", "email": "", "role": "cfo",
        "password": "123"}, follow_redirects=True)
    # weak password rejected -> role/name unchanged is fine; password not applied
    other = a.test_client()
    r = other.post("/login", data={"username": "cfo", "password": "123",
                                   "_csrf": get_csrf(other)}, follow_redirects=False)
    assert r.status_code == 200  # login page re-rendered (auth failed)


def test_admin_cannot_demote_self(app_client):
    a, c = app_client
    from config import Config
    uid = _uid(a, Config.ADMIN_USER)
    c.post(f"/admin/users/{uid}/edit", data={
        "_csrf": get_csrf(c), "full_name": "Admin", "email": "", "role": "normal_user",
        "password": ""}, follow_redirects=True)
    with a.app_context():
        from app.db import get_db
        conn = get_db()
        row = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        conn.close()
    assert row["role"] == "super_admin"  # unchanged — self-demotion blocked
