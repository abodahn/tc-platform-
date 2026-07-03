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


# ---------------- Strict sequential approval (all the cases) ----------------
# Ladder for 48,000: warehouse -> factory_manager -> purchasing -> finance -> cfo
_USERS = {
    "warehouse": {"username": "store", "role": "storekeeper"},
    "factory_manager": {"username": "factory", "role": "factory_manager"},
    "purchasing": {"username": "purchasing", "role": "purchasing_manager"},
    "finance": {"username": "finance", "role": "finance_manager"},
    "cfo": {"username": "cfo", "role": "cfo"},
}


def _make_pr(a, c, title="Seq"):
    _new_pr(c, title=title)
    return _pr_id(a, title)


def test_out_of_turn_approver_is_blocked(app_client):
    """CFO approves but warehouse hasn't -> blocked; PR stays at warehouse."""
    a, c = app_client
    pid = _make_pr(a, c, "OOO")
    with a.app_context():
        from app.approvals import services as svc
        ok, msg = svc.act_on_step(pid, _USERS["cfo"], "approve")
        assert ok is False and msg == "forbidden"
        b = svc.get_pr(pid)
        assert b["pr"]["status"] == "pending" and b["pr"]["current_seq"] == 1
        assert all(s["status"] == "pending" for s in b["steps"])  # nothing signed


def test_strict_sequence_advances_one_by_one(app_client):
    a, c = app_client
    pid = _make_pr(a, c, "Seq2")
    order = ["warehouse", "factory_manager", "purchasing", "finance", "cfo"]
    with a.app_context():
        from app.approvals import services as svc
        for i, stage in enumerate(order):
            # nobody further down the ladder can act yet
            for later in order[i + 1:]:
                assert svc.act_on_step(pid, _USERS[later], "approve")[1] == "forbidden"
            ok, msg = svc.act_on_step(pid, _USERS[stage], "approve")
            assert ok is True
            assert msg == ("approved" if stage == order[-1] else "advanced")
        b = svc.get_pr(pid)
        assert b["pr"]["status"] == "approved" and b["pr"]["po_no"]


def test_reject_at_first_stage_bounces_to_requester(app_client):
    a, c = app_client
    pid = _make_pr(a, c, "Rej1")
    with a.app_context():
        from app.approvals import services as svc
        ok, msg = svc.act_on_step(pid, _USERS["warehouse"], "reject", comment="no budget")
        assert ok and msg == "rejected"
        b = svc.get_pr(pid)
        assert b["pr"]["status"] == "rejected"
        assert "no budget" in (b["pr"]["rejection_reason"] or "")


def test_reject_at_later_stage_after_some_approved(app_client):
    """Warehouse+Factory+Purchasing approve, then Finance rejects -> whole PR
    rejected; earlier signatures kept, later stage (CFO) never reached."""
    a, c = app_client
    pid = _make_pr(a, c, "RejLate")
    with a.app_context():
        from app.approvals import services as svc
        for stage in ("warehouse", "factory_manager", "purchasing"):
            assert svc.act_on_step(pid, _USERS[stage], "approve")[1] == "advanced"
        ok, msg = svc.act_on_step(pid, _USERS["finance"], "reject", comment="too costly")
        assert ok and msg == "rejected"
        b = svc.get_pr(pid)
        assert b["pr"]["status"] == "rejected"
        st = {s["stage"]: s["status"] for s in b["steps"]}
        assert st["warehouse"] == "approved" and st["purchasing"] == "approved"
        assert st["finance"] == "rejected"
        assert st["cfo"] == "pending"  # never reached


def test_no_action_after_rejection(app_client):
    a, c = app_client
    pid = _make_pr(a, c, "Locked")
    with a.app_context():
        from app.approvals import services as svc
        svc.act_on_step(pid, _USERS["warehouse"], "reject", comment="x")
        # any further approval attempt is refused because the PR is not pending
        assert svc.act_on_step(pid, _USERS["warehouse"], "approve")[1] == "not_pending"
        assert svc.act_on_step(pid, _USERS["cfo"], "approve")[1] == "not_pending"


def test_resubmit_rebuilds_ladder_fresh(app_client):
    a, c = app_client
    pid = _make_pr(a, c, "Fresh")
    with a.app_context():
        from app.approvals import services as svc
        svc.act_on_step(pid, _USERS["warehouse"], "approve")            # advance once
        svc.act_on_step(pid, _USERS["factory_manager"], "reject", comment="redo")
        requester = {"username": "admin", "role": "super_admin"}
        ok, _ = svc.submit_pr(pid, requester)
        assert ok
        b = svc.get_pr(pid)
        assert b["pr"]["status"] == "pending" and b["pr"]["current_seq"] == 1
        assert all(s["status"] == "pending" for s in b["steps"])       # all reset


def test_seeded_passwords_are_admin1122(app_client):
    a, c = app_client
    from werkzeug.security import check_password_hash
    with a.app_context():
        from app.db import get_db
        conn = get_db()
        for uname in ("warehouse", "purchasing", "finance", "cfo", "ceo", "store", "factory"):
            row = conn.execute("SELECT password_hash FROM users WHERE username=?", (uname,)).fetchone()
            assert row and check_password_hash(row["password_hash"], "Admin@1122"), uname
        conn.close()
