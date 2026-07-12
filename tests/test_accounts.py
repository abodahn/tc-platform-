"""
Authentication & Account Management — integration tests.

Self-contained: builds the app on a throwaway SQLite DB, stubs SMTP (captures the
verification / reset URLs instead of sending), and exercises the full lifecycle,
security controls and a regression that existing username logins keep working.

Run:  pytest tests/test_accounts.py -q
(No real emails are sent; no network; the platform DB is never touched.)
"""
import os
import re
import tempfile

import pytest

os.environ.setdefault("TC_ENV", "development")
os.environ.setdefault("TC_ADMIN_PASSWORD", "Test@1234")

CAP = {}


@pytest.fixture(scope="module")
def app():
    tmp = os.path.join(tempfile.gettempdir(), "acc_pytest.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    import config as cfg
    cfg.Config.DB_PATH = tmp
    from app import create_app
    application = create_app()
    import app.accounts.emails as E
    _v, _r = E.verify_email, E.reset_request
    E.verify_email = lambda u, url, lang="en": (CAP.__setitem__("v", url), _v(u, url, lang))[1]
    E.reset_request = lambda u, url, ttl, lang="en": (CAP.__setitem__("r", url), _r(u, url, ttl, lang))[1]
    E.send = lambda to, built: True
    return application


def _tok(c, path="/login"):
    m = re.search(r'name="csrf-token" content="([^"]+)"', c.get(path).get_data(as_text=True))
    return m.group(1) if m else ""


def _login(c, u, p):
    return c.post("/login", data={"username": u, "password": p, "_csrf": _tok(c), "next": "/"})


def _signup(c, **over):
    f = {"first_name": "Test", "last_name": "User", "email": "t.user@tcgarments.com",
         "employee_id": "E-1001", "mobile": "+201110002200", "company": "T&C Garments S.A.E.",
         "department": "Finance", "job_title": "Analyst", "password": "Str0ng!Passw0rd#7",
         "confirm_password": "Str0ng!Passw0rd#7", "accept_terms": "1", "accept_privacy": "1",
         "preferred_language": "en"}
    f.update(over)
    f["_csrf"] = _tok(c, "/signup")
    return c.post("/signup", data=f)


# ------------------------------------------------------------------ sign up
def test_signup_page_renders(app):
    r = app.test_client().get("/signup")
    assert r.status_code == 200 and "Create your account" in r.get_data(as_text=True)


def test_signup_success_then_verify_then_approve_then_login(app):
    c = app.test_client()
    r = _signup(c, email="nour.sami@tcgarments.com", employee_id="E-7001")
    assert "Registration received" in r.get_data(as_text=True)
    r = c.get("/verify-email?token=" + CAP["v"].split("token=")[1])
    assert "Email verified" in r.get_data(as_text=True)
    with app.app_context():
        from app.db import get_db
        cc = get_db()
        row = cc.execute("SELECT id,account_status,is_active FROM users WHERE email='nour.sami@tcgarments.com'").fetchone()
        cc.close()
    assert row["account_status"] == "pending_approval"
    # admin approves
    ac = app.test_client(); _login(ac, "admin", "Test@1234")
    ac.post("/admin/registrations/%d/approve" % row["id"],
            data={"role": "normal_user", "_csrf": _tok(ac, "/admin/registrations")})
    with app.app_context():
        from app.db import get_db
        cc = get_db()
        st = cc.execute("SELECT account_status,is_active FROM users WHERE id=?", (row["id"],)).fetchone()
        cc.close()
    assert st["account_status"] == "active" and st["is_active"] == 1
    nc = app.test_client()
    r = nc.post("/login", data={"username": "nour.sami@tcgarments.com",
                                "password": "Str0ng!Passw0rd#7", "_csrf": _tok(nc), "next": "/"})
    assert r.status_code == 302 and nc.get("/").status_code == 200


def test_duplicate_and_weak_and_mismatch(app):
    c = app.test_client()
    _signup(c, email="dup@tcgarments.com", employee_id="E-DUP")
    with app.app_context():
        from app.db import get_db
        cc = get_db(); before = cc.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]; cc.close()
    _signup(c, email="dup@tcgarments.com", employee_id="E-DUP")   # neutral, no new row
    with app.app_context():
        from app.db import get_db
        cc = get_db(); after = cc.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]; cc.close()
    assert after == before
    r = _signup(c, email="weak@tcgarments.com", employee_id="E-W", password="password123", confirm_password="password123")
    assert r.status_code == 200 and "acc.err" in r.get_data(as_text=True)   # error keys present in attrs
    r = _signup(c, email="mm@tcgarments.com", employee_id="E-M", confirm_password="Different#123456")
    assert "acc.err.pw_mismatch" in r.get_data(as_text=True)


# --------------------------------------------------------------- verify token
def test_verify_invalid_and_reused(app):
    c = app.test_client()
    _signup(c, email="vt@tcgarments.com", employee_id="E-VT")
    t = CAP["v"].split("token=")[1]
    assert c.get("/verify-email?token=deadbeef").status_code == 200
    assert "Verification problem" in c.get("/verify-email?token=deadbeef").get_data(as_text=True)
    c.get("/verify-email?token=" + t)                       # first use ok
    r = c.get("/verify-email?token=" + t)                   # reuse
    assert "already been used" in r.get_data(as_text=True) or "Already verified" in r.get_data(as_text=True)


# ------------------------------------------------------- forgot / reset / sec
def test_forgot_is_enumeration_safe(app):
    c = app.test_client()
    a = c.post("/forgot-password", data={"identifier": "nobody@nowhere.com", "_csrf": _tok(c, "/forgot-password")})
    assert a.status_code == 200 and "Check your email" in a.get_data(as_text=True)


def test_reset_flow_and_single_use(app):
    c = app.test_client()
    _signup(c, email="rst@tcgarments.com", employee_id="E-RST")
    c.get("/verify-email?token=" + CAP["v"].split("token=")[1])
    with app.app_context():
        from app.db import get_db
        cc = get_db()
        cc.execute("UPDATE users SET account_status='active', is_active=1 WHERE email='rst@tcgarments.com'"); cc.commit(); cc.close()
    c.post("/forgot-password", data={"identifier": "rst@tcgarments.com", "_csrf": _tok(c, "/forgot-password")})
    t = CAP["r"].split("token=")[1]
    r = c.post("/reset-password", data={"token": t, "password": "Rotated!Pass#2026",
                                        "confirm_password": "Rotated!Pass#2026", "_csrf": _tok(c, "/reset-password?token=" + t)})
    assert "Password updated" in r.get_data(as_text=True)
    # single-use: token can't be reused
    r = c.post("/reset-password", data={"token": t, "password": "Another!Pass#2026",
                                        "confirm_password": "Another!Pass#2026", "_csrf": _tok(c, "/reset-password")})
    assert "can" in r.get_data(as_text=True).lower()   # invalid-link state


def test_csrf_and_open_redirect(app):
    c = app.test_client()
    assert c.post("/signup", data={"first_name": "x"}).status_code in (400, 403)
    oc = app.test_client()
    r = oc.post("/login", data={"username": "admin", "password": "Test@1234",
                                "_csrf": _tok(oc), "next": "https://evil.example.com"})
    assert r.status_code == 302 and "evil.example.com" not in (r.headers.get("Location") or "")


def test_permission_enforced_on_admin(app):
    # a non-privileged user cannot reach registration management
    with app.app_context():
        from app.db import get_db
        from werkzeug.security import generate_password_hash
        cc = get_db()
        cc.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,account_status,created_at) "
                   "VALUES (?,?,?,?,?,?,datetime('now'))",
                   ("plain", generate_password_hash("Plain@1234"), "Plain", "normal_user", 1, "active"))
        cc.commit(); cc.close()
    c = app.test_client(); _login(c, "plain", "Plain@1234")
    assert c.get("/admin/registrations").status_code == 403


# --------------------------------------------------------------- regression
def test_existing_username_login_unbroken(app):
    c = app.test_client()
    r = _login(c, "admin", "Test@1234")
    assert r.status_code == 302 and c.get("/").status_code == 200
