"""Signature auto-provisioning + server-side generation + one-click quick sig."""
import pytest

from _support import login_admin, get_csrf


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "sig.db")
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True
    c = a.test_client()
    login_admin(c)
    return a, c


def test_generate_png():
    from app.services.signature import generate_png
    png = generate_png("Mahmoud Hassan")
    assert png and png.startswith("data:image/png;base64,") and len(png) > 1000
    assert generate_png("") is None
    assert generate_png("   ") is None


def test_all_users_auto_provisioned(app_client):
    a, c = app_client
    with a.app_context():
        from app.db import get_db
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        missing = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE sig_png IS NULL OR sig_png=''").fetchone()["c"]
        conn.close()
    assert total > 0 and missing == 0     # every user has a signature


def test_quick_signature_endpoint(app_client):
    a, c = app_client
    # clear the admin's signature, then one-click regenerate it
    c.post("/profile/signature/clear", headers={"X-CSRF-Token": get_csrf(c)})
    with a.app_context():
        from app.auth import current_user  # noqa: F401
    r = c.post("/profile/signature/quick", headers={"X-CSRF-Token": get_csrf(c)})
    assert r.status_code == 200
    j = r.get_json()
    assert j["ok"] and j["png"].startswith("data:image/png;base64,")
    # persisted
    with a.app_context():
        from app.db import get_db
        from config import Config
        conn = get_db()
        row = conn.execute("SELECT sig_png FROM users WHERE username=?",
                           (Config.ADMIN_USER,)).fetchone()
        conn.close()
    assert row["sig_png"] and row["sig_png"].startswith("data:image/png;base64,")


def test_approval_stamps_user_signature(app_client):
    """A user's auto-provisioned signature is stamped onto the PR step when they
    approve (so it appears on the paper)."""
    a, c = app_client
    import io
    tok = get_csrf(c)
    c.post("/procurement/new", data={
        "_csrf": tok, "action": "submit", "title": "Sig stamp",
        "department": "General Maintenance", "currency": "EGP",
        "item[]": "X", "description[]": "y", "unit[]": "Pcs",
        "qty[]": "1", "current_stock[]": "0", "unit_price[]": "5000", "item_notes[]": "",
    }, follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        pid = [p for p in svc.list_prs(status="pending") if p["title"] == "Sig stamp"][0]["id"]
    # admin approves the current (warehouse) step
    c.post(f"/procurement/pr/{pid}/approve", data={"_csrf": get_csrf(c)}, follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        b = svc.get_pr(pid)
        signed = [s for s in b["steps"] if s["status"] == "approved"]
        assert signed and signed[0]["sig_png"] and signed[0]["sig_png"].startswith("data:image/png")
