"""End-to-end tests for the Procurement & Approvals cycle, on an isolated temp
DB so the real platform.db is never touched. The admin (super_admin) can act on
any ladder stage, so a single admin session can walk the whole cycle."""
import pytest

from _support import login_admin, get_csrf


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "proc.db")
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True
    c = a.test_client()
    login_admin(c)
    return a, c


def _new_pr(c, total_unit=48000, qty=1, title="Repair battery"):
    tok = get_csrf(c)
    return c.post("/procurement/new", data={
        "_csrf": tok, "action": "submit", "title": title,
        "department": "General Maintenance", "currency": "EGP",
        "vendor": "High Trak for Trading",
        "item[]": "Battery", "description[]": "48V 750A",
        "unit[]": "Pcs", "qty[]": str(qty), "current_stock[]": "0",
        "unit_price[]": str(total_unit), "item_notes[]": "",
    }, follow_redirects=True)


def test_constants_ladder():
    from app.approvals import constants as C
    assert C.build_ladder(5000) == ["warehouse", "factory_manager", "purchasing"]
    # 48,000 -> 5 approvers (+requester = 6 signatures), matching the paper form
    assert C.build_ladder(48000) == ["warehouse", "factory_manager", "purchasing",
                                      "finance", "cfo"]
    assert "ceo" in C.build_ladder(200000)


def test_index_and_seeded_demo(app_client):
    a, c = app_client
    r = c.get("/procurement/")
    assert r.status_code == 200
    # the seeded demo PR should be listed
    assert b"PR-DEMO-13849" in r.data or b"Repair forklift battery" in r.data


def test_full_cycle_create_sign_po(app_client):
    a, c = app_client
    r = _new_pr(c)
    assert r.status_code == 200
    # find the created PR id from the DB
    with a.app_context():
        from app.approvals import services as svc
        prs = svc.list_prs(status="pending")
        target = [p for p in prs if p["title"] == "Repair battery"]
        assert target, "PR was not created/submitted"
        pr_id = target[0]["id"]
        bundle = svc.get_pr(pr_id)
        stages = [s["stage"] for s in bundle["steps"]]
        assert stages == ["warehouse", "factory_manager", "purchasing", "finance", "cfo"]

    # walk every stage as admin (super_admin may act on any stage)
    for _ in range(len(stages)):
        tok = get_csrf(c)
        c.post(f"/procurement/pr/{pr_id}/approve", data={"_csrf": tok, "comment": "ok"},
               follow_redirects=True)

    with a.app_context():
        from app.approvals import services as svc
        b = svc.get_pr(pr_id)
        assert b["pr"]["status"] == "approved"
        assert b["pr"]["po_no"]
        assert all(s["status"] == "approved" for s in b["steps"])

    # PDFs render
    assert c.get(f"/procurement/pr/{pr_id}/pdf").status_code == 200
    po = c.get(f"/procurement/pr/{pr_id}/po.pdf")
    assert po.status_code == 200 and po.data[:4] == b"%PDF"

    # issue the PO
    tok = get_csrf(c)
    c.post(f"/procurement/pr/{pr_id}/issue-po", data={"_csrf": tok}, follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        assert svc.get_pr(pr_id)["pr"]["status"] == "po_issued"


def test_reject_returns_to_requester(app_client):
    a, c = app_client
    _new_pr(c, title="Reject me")
    with a.app_context():
        from app.approvals import services as svc
        pr_id = [p for p in svc.list_prs(status="pending") if p["title"] == "Reject me"][0]["id"]
    tok = get_csrf(c)
    c.post(f"/procurement/pr/{pr_id}/reject", data={"_csrf": tok, "comment": "Too expensive"},
           follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        b = svc.get_pr(pr_id)
        assert b["pr"]["status"] == "rejected"
        assert "Too expensive" in (b["pr"]["rejection_reason"] or "")


def test_vendors_page_and_create(app_client):
    a, c = app_client
    assert c.get("/procurement/vendors").status_code == 200
    tok = get_csrf(c)
    c.post("/procurement/vendors", data={"_csrf": tok, "name": "New Vendor Co",
                                         "payment_terms": "Net 30"}, follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        assert any(v["name"] == "New Vendor Co" for v in svc.list_vendors())


def test_bell_notification_on_submit(app_client):
    a, c = app_client
    _new_pr(c, title="Notify me")
    with a.app_context():
        from app.db import get_db
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='procurement'").fetchone()["c"]
        conn.close()
        assert n >= 1
