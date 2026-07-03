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


# ---------------- Next layer ----------------
def _pr_id(a, title):
    with a.app_context():
        from app.approvals import services as svc
        return [p for p in svc.list_prs(status="pending") if p["title"] == title][0]["id"]


def test_new_layer_pages_load(app_client):
    a, c = app_client
    for url in ("/procurement/analytics", "/procurement/budgets", "/procurement/delegations"):
        assert c.get(url).status_code == 200


def test_quotes_add_choose_and_compare(app_client):
    a, c = app_client
    _new_pr(c, title="Quote me")
    pid = _pr_id(a, "Quote me")
    for vendor, amt in (("High Trak", "48000"), ("Delta", "52000")):
        c.post(f"/procurement/pr/{pid}/quote",
               data={"_csrf": get_csrf(c), "vendor": vendor, "amount": amt},
               content_type="multipart/form-data", follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        b = svc.get_pr(pid)
        assert len(b["quotes"]) == 2
        cmp = svc.quote_comparison(b["quotes"])
        assert cmp["saving"] == 4000
        qid = [q for q in b["quotes"] if q["vendor"] == "High Trak"][0]["id"]
    c.post(f"/procurement/pr/{pid}/quote/{qid}/choose",
           data={"_csrf": get_csrf(c)}, follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        assert svc.get_pr(pid)["pr"]["vendor"] == "High Trak"


def test_budget_status_and_over(app_client):
    a, c = app_client
    with a.app_context():
        from app.approvals import services as svc
        svc.set_budget("Widgets", 10000, period=str(svc._year()))
        st = svc.budget_status("Widgets")
        assert st["amount"] == 10000 and st["remaining"] == 10000
        assert svc.budget_status("Widgets", extra=15000)["over"] is True


def test_delegation_grants_stage_authority(app_client):
    a, c = app_client
    with a.app_context():
        from app.approvals import services as svc
        from _support import ensure_user
    # storekeeper cannot act on factory_manager stage on their own
    from _support import ensure_user
    ensure_user(a, "fac1", "factory_manager")
    ensure_user(a, "store1", "storekeeper")
    with a.app_context():
        from app.approvals import services as svc
        store_u = {"username": "store1", "role": "storekeeper"}
        assert svc.can_act(store_u, "factory_manager") is False
        svc.add_delegation("fac1", "store1", None, None, "cover")
        assert svc.can_act(store_u, "factory_manager") is True


def test_escalation_job_runs(app_client):
    a, c = app_client
    _new_pr(c, title="Escalate maybe")
    with a.app_context():
        from app.approvals import services as svc
        # nothing is overdue yet (SLA is 48h) -> 0 escalations, no error
        assert svc.run_escalations() == 0


def test_new_from_ticket_prefill(app_client):
    a, c = app_client
    # maintenance seeds tickets; prefilled form should return 200 and carry a title
    r = c.get("/procurement/new?from_ticket=1")
    assert r.status_code == 200


def test_approver_users_seeded(app_client):
    a, c = app_client
    with a.app_context():
        from app.db import get_db
        conn = get_db()
        roles = {r["role"] for r in conn.execute(
            "SELECT role FROM users WHERE role IN "
            "('warehouse_manager','purchasing_manager','finance_manager','cfo','ceo')").fetchall()}
        conn.close()
    assert roles == {"warehouse_manager", "purchasing_manager", "finance_manager", "cfo", "ceo"}


def test_submit_notifies_stage_approvers(app_client):
    a, c = app_client
    _new_pr(c, title="Ping approvers")
    with a.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        targeted = {r["target_user"] for r in conn.execute(
            "SELECT target_user FROM notifications WHERE module='procurement' "
            "AND target_user IS NOT NULL AND title='New request to sign'").fetchall()}
        eligible = set(svc.eligible_approvers(get_db(), "warehouse"))
        conn.close()
    # at least one warehouse-stage approver was personally notified
    assert targeted & eligible


def test_bell_is_per_user(app_client):
    a, c = app_client
    _new_pr(c, title="Only warehouse")
    with a.app_context():
        from app.routes.main import _unread_notifications
        # a random unrelated user should NOT see the warehouse-targeted "to sign" ping
        rows_other, _ = _unread_notifications("nobody_xyz")
        assert not any(r["title"] == "New request to sign" and r["target_user"]
                       for r in rows_other)
        # a warehouse approver SHOULD see it
        rows_wh, _ = _unread_notifications("warehouse")
        assert any(r["title"] == "New request to sign" for r in rows_wh)


def test_resubmit_after_reject(app_client):
    a, c = app_client
    _new_pr(c, title="Bounce me")
    pid = _pr_id(a, "Bounce me")
    c.post(f"/procurement/pr/{pid}/reject", data={"_csrf": get_csrf(c), "comment": "fix specs"},
           follow_redirects=True)
    c.post(f"/procurement/pr/{pid}/submit", data={"_csrf": get_csrf(c)}, follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        assert svc.get_pr(pid)["pr"]["status"] == "pending"


def test_pdf_layout_renders(app_client):
    a, c = app_client
    _new_pr(c, title="Pdf me")
    pid = _pr_id(a, "Pdf me")
    with a.app_context():
        from app.approvals import services as svc, pdf
        data = pdf.pr_pdf(svc.get_pr(pid))
        assert data[:4] == b"%PDF" and len(data) > 3000
