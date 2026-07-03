"""Tests for the per-user digital signature settings section (isolated temp DB)."""
import pytest

from _support import login_admin, get_csrf

PNG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0"
       "lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


@pytest.fixture()
def app_c(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "sig.db")
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True
    c = a.test_client()
    login_admin(c)
    return a, c


def test_signature_columns_migrated(app_c):
    a, c = app_c
    with a.app_context():
        from app.db import get_db
        cols = [r[1] for r in get_db().execute("PRAGMA table_info(users)").fetchall()]
    for col in ("sig_style", "sig_png", "sig_name", "sig_updated_at"):
        assert col in cols


def test_profile_renders_signature_section(app_c):
    a, c = app_c
    r = c.get("/profile")
    assert r.status_code == 200
    assert b"sigPanel" in r.data and b"@font-face" in r.data
    assert b"vendor/fonts/great-vibes.woff2" in r.data
    assert b"vendor/fonts/dancing-script.woff2" in r.data  # the handwriting style


def test_save_then_clear(app_c):
    a, c = app_c
    tok = get_csrf(c)
    r = c.post("/profile/signature", json={"style": "dancing-script", "name": "Ahmed", "png": PNG},
               headers={"X-CSRF-Token": tok})
    assert r.status_code == 200 and r.get_json()["ok"]
    with a.app_context():
        from app.db import get_db
        row = get_db().execute("SELECT sig_style, sig_name, sig_png FROM users WHERE username=?",
                               ("admin",)).fetchone()
        assert row["sig_style"] == "dancing-script"
        assert row["sig_name"] == "Ahmed"
        assert row["sig_png"] and row["sig_png"].startswith("data:image/png")
    # the saved signature shows on the profile page
    assert b'src="data:image/png' in c.get("/profile").data
    # clear wipes it
    assert c.post("/profile/signature/clear", headers={"X-CSRF-Token": tok}).get_json()["ok"]
    with a.app_context():
        from app.db import get_db
        assert get_db().execute("SELECT sig_png FROM users WHERE username=?",
                                ("admin",)).fetchone()["sig_png"] is None


def test_rejects_bad_input(app_c):
    a, c = app_c
    tok = get_csrf(c)
    h = {"X-CSRF-Token": tok}
    assert c.post("/profile/signature", json={"style": "evil", "name": "x", "png": PNG}, headers=h).status_code == 400
    assert c.post("/profile/signature", json={"style": "satisfy", "name": "x", "png": "notdata"}, headers=h).status_code == 400
    big = "data:image/png;base64," + ("A" * 400001)
    assert c.post("/profile/signature", json={"style": "satisfy", "name": "x", "png": big}, headers=h).status_code == 413
    assert c.post("/profile/signature", json={"style": "satisfy"}, headers=h).status_code == 400  # missing fields


def test_requires_login(app_c):
    a, c = app_c
    anon = a.test_client()
    r = anon.post("/profile/signature", json={"style": "satisfy", "name": "x", "png": PNG})
    assert r.status_code in (302, 400)  # redirect to login, or CSRF-blocked
