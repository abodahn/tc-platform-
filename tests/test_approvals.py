"""End-to-end tests for the Procurement & Approvals cycle, on an isolated temp
DB so the real platform.db is never touched. The admin (super_admin) can act on
any ladder stage, so one admin session can sign the whole ladder — but it cannot
also RAISE the request it signs: DOAM §3.4 forbids approving a transaction that
names you as requestor, and no role or setting waives that. Route-driven tests
therefore raise as a requester and sign as the admin."""
import pytest

from _support import login_admin, login_as, get_csrf


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
    # DOAM §4.1 tier 1 (up to 10,000 EGP): the Procurement Manager carries it.
    # The Plant Director became a value-gated L2 approver from tier 2 upward.
    assert C.build_ladder(5000) == ["warehouse", "purchasing"]
    # DOAM §4.1 tier 2 (10,001–200,000): approval sits with the Plant / Supply
    # Chain Director. Finance and the CFO are NOT involved at this value — the
    # single most consequential difference from the paper form this replaced,
    # which collected five approvers here including both of them.
    assert C.build_ladder(48000) == ["warehouse", "purchasing",
                                     "factory_manager", "scd"]
    # Each tier boundary, from both sides — the band edge is where money goes to
    # the wrong signature silently.
    assert C.build_ladder(200_000)[-1] == "scd"          # tier 2 top
    assert C.build_ladder(200_000.01)[-1] == "finance"   # tier 3, Financial Director
    assert C.build_ladder(500_000.01)[-1] == "cfo"       # tier 4, MD or CFO
    assert C.build_ladder(2_000_000.01)[-1] == "ceo"     # tier 5, MD and CFO
    assert C.build_ladder(5_000_000.01)[-1] == "bod"     # tier 6, the Board
    # CAPEX (§4.2) is a different ladder for the same money.
    assert C.build_ladder(300_000, "capex")[-1] == "ceo"
    assert "warehouse" not in C.build_ladder(300_000, "capex")


def test_index_and_seeded_demo(app_client):
    a, c = app_client
    r = c.get("/procurement/")
    assert r.status_code == 200
    # the seeded demo PR should be listed
    assert b"PR-DEMO-13849" in r.data or b"Repair forklift battery" in r.data


ADMIN = {"username": "admin", "role": "super_admin"}


def _gate_and_price(svc, pid, user, unit_price=48000.0):
    """Purchasing stage is the active rung. Assert BOTH purchasing gates fire,
    then satisfy them the way the product intends.

    Gate 1 — pricing: a requester never enters money (routes._can_price() is
    hardcoded False), so the request arrives unpriced and the purchasing stage
    cannot be signed (act_on_step -> 'needs_pricing').
    Gate 2 — RFQ: once priced at 48,000 EGP the total is >= RFQ_VALUE_THRESHOLD
    (25,000), so RFQ_QUOTE_MIN (2) quotes from DISTINCT vendors are required
    (act_on_step -> 'needs_quotes')."""
    assert svc.act_on_step(pid, user, "approve") == (False, "needs_pricing")
    item_id = svc.get_pr(pid)["items"][0]["id"]
    assert svc.price_pr(pid, {item_id: unit_price}, {"tax_rate": 0}, user)[0] is True
    assert svc.act_on_step(pid, user, "approve") == (False, "needs_quotes")
    svc.add_quote(pid, {"vendor": "High Trak", "amount": unit_price}, user)
    svc.add_quote(pid, {"vendor": "Delta", "amount": unit_price + 4000}, user)


def test_full_cycle_create_sign_po(app_client):
    """Route-driven walk of the CURRENT contract: a requester raises an UNPRICED
    request (the commercial lockout strips any price they POST), so it routes the
    three demand stages only. Purchasing prices it at 48,000, which appends the
    value rungs, and the ladder then completes at 5 signatures.

    Threshold arithmetic (constants.APPROVAL_MATRIX, EGP):
      qty 1 x 48,000 = 48,000
      warehouse 0 / factory_manager 0 / purchasing 0  -> always required
      finance 10,000  -> 48,000 >= 10,000  -> required
      cfo     25,000  -> 48,000 >= 25,000  -> required
      ceo    100,000  -> 48,000 <  100,000 -> NOT required
    => warehouse, factory_manager, purchasing, finance, cfo = 5 approvers
       + the requester's own submission = the 6 signatures on the paper form."""
    a, c = app_client
    # The request is raised by a REQUESTER, not by the admin who then signs it.
    # DOAM §3.4: "No person may approve a transaction that also names that person
    # as requestor." This test used one admin session for both ends, which the
    # rule now correctly refuses — the walk below is the real shape of the cycle.
    login_as(c, a, "normal_user")
    r = _new_pr(c)                       # POSTs unit_price 48000 — it must be stripped
    assert r.status_code == 200
    login_admin(c)                       # ...and the admin signs the stages

    # One admin signing EVERY rung is now refused by the dual-role half of the
    # SoD rule, which ships on. This test is about the ladder, the pricing gate
    # and the PO — not about SoD — so it turns the documented exemption on
    # explicitly, through the real settings path. The absolute half still
    # applies and is not waivable: the requester above is a different user, and
    # if that regressed this test fails on self_approval. SoD itself is covered
    # by test_sod_* and app/approvals/tests_escalation.py.
    with a.app_context():
        from app.approvals import services as svc
        ok, msg = svc.set_setting("sod_admin_exempt", "true")
        assert ok, "could not enable the dual-role exemption: %s" % msg
    # find the created PR id from the DB
    with a.app_context():
        from app.approvals import services as svc
        from app.approvals import constants as C
        prs = svc.list_prs(status="pending")
        target = [p for p in prs if p["title"] == "Repair battery"]
        assert target, "PR was not created/submitted"
        pr_id = target[0]["id"]
        bundle = svc.get_pr(pr_id)
        # the requester price lockout: nothing commercial survived the POST
        assert bundle["pr"]["pricing_status"] == "unpriced"
        assert float(bundle["pr"]["total"] or 0) == 0.0
        assert float(bundle["items"][0]["unit_price"] or 0) == 0.0
        # an unpriced request routes the demand stages only, and under the DOAM
        # those are Warehouse + Procurement Manager (§4.1 tier 1)
        assert [s["stage"] for s in bundle["steps"]] == \
            ["warehouse", "purchasing"] == C.DEMAND_STAGES

    # demand stages sign first (admin is super_admin -> may act on any stage)
    for stage in ("warehouse",):
        tok = get_csrf(c)
        c.post(f"/procurement/pr/{pr_id}/approve", data={"_csrf": tok, "comment": "ok"},
               follow_redirects=True)
        with a.app_context():
            from app.approvals import services as svc
            st = {s["stage"]: s["status"] for s in svc.get_pr(pr_id)["steps"]}
            assert st[stage] == "approved", stage

    # PRICING GATE — the route refuses the purchasing signature while unpriced
    tok = get_csrf(c)
    c.post(f"/procurement/pr/{pr_id}/approve", data={"_csrf": tok}, follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        b = svc.get_pr(pr_id)
        assert b["pr"]["status"] == "pending"
        assert {s["stage"]: s["status"] for s in b["steps"]}["purchasing"] == "pending"
        assert svc.act_on_step(pr_id, ADMIN, "approve") == (False, "needs_pricing")
        item_id = b["items"][0]["id"]

    # Purchasing enters the value through the pricing route -> value rungs join
    tok = get_csrf(c)
    c.post(f"/procurement/pr/{pr_id}/price",
           data={"_csrf": tok, f"price_{item_id}": "48000", "tax_rate": "0"},
           follow_redirects=True)
    with a.app_context():
        from app.approvals import services as svc
        from app.approvals import constants as C
        b = svc.get_pr(pr_id)
        assert b["pr"]["pricing_status"] == "priced"
        assert float(b["pr"]["total"]) == 48000.0
        assert [s["stage"] for s in b["steps"]] == \
            ["warehouse", "purchasing", "factory_manager", "scd"] \
            == C.build_ladder(48000)
        # RFQ GATE at 48,000 (>= 25,000) with no quotes yet
        assert svc.act_on_step(pr_id, ADMIN, "approve") == (False, "needs_quotes")

    # two DISTINCT vendor quotes satisfy the RFQ rule
    for vendor, amt in (("High Trak", "48000"), ("Delta", "52000")):
        c.post(f"/procurement/pr/{pr_id}/quote",
               data={"_csrf": get_csrf(c), "vendor": vendor, "amount": amt},
               content_type="multipart/form-data", follow_redirects=True)

    # Purchasing and the value-gated rungs now sign. DERIVED from the ladder
    # rather than named: the DOAM decides who those rungs are, and a hard-coded
    # list here would silently stop testing the real policy the day it changes.
    from app.approvals import constants as _C
    for stage in [s for s in _C.build_ladder(48000) if s != "warehouse"]:
        tok = get_csrf(c)
        c.post(f"/procurement/pr/{pr_id}/approve", data={"_csrf": tok, "comment": "ok"},
               follow_redirects=True)
        with a.app_context():
            from app.approvals import services as svc
            st = {s["stage"]: s["status"] for s in svc.get_pr(pr_id)["steps"]}
            assert st[stage] == "approved", stage

    with a.app_context():
        from app.approvals import services as svc
        from app.approvals import constants as C
        b = svc.get_pr(pr_id)
        assert b["pr"]["status"] == "approved"
        assert b["pr"]["po_no"]
        # Four rungs at 48,000 under DOAM §4.1 tier 2, not the paper form's five:
        # the ladder stops at the two L2 directors and never reaches the CFO.
        assert len(b["steps"]) == len(C.build_ladder(48000)) == 4
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
# A route-raised request is UNPRICED, so it starts with the three demand stages
# (warehouse -> factory_manager -> purchasing). Purchasing prices it at 48,000
# EGP at the pricing gate, which appends the value rungs finance (>= 10,000) and
# cfo (>= 25,000) — ceo (>= 100,000) stays out. Full ladder once priced:
#   warehouse -> factory_manager -> purchasing -> finance -> cfo
_USERS = {
    "warehouse": {"username": "store", "role": "storekeeper"},
    "factory_manager": {"username": "factory", "role": "factory_manager"},
    "purchasing": {"username": "purchasing", "role": "purchasing_manager"},
    "finance": {"username": "finance", "role": "finance_manager"},
    "cfo": {"username": "cfo", "role": "cfo"},
    # DOAM §3.2 L2 / BOD authorities, added when the matrix came into force.
    "scd": {"username": "scd", "role": "supply_chain_director"},
    "ceo": {"username": "md", "role": "ceo"},
    "bod": {"username": "board", "role": "board"},
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
    """The strict-sequence invariant, walked end to end across the pricing gate:
    at every rung nobody further down the ladder can act, and the rung that CAN
    act advances the request by exactly one step."""
    a, c = app_client
    pid = _make_pr(a, c, "Seq2")
    # DOAM §4.1 tier 2 for the 48,000 test request: the value rungs are the
    # two L2 directors, not Finance and the CFO.
    order = ["warehouse", "purchasing", "factory_manager", "scd"]
    with a.app_context():
        from app.approvals import services as svc
        from app.approvals import constants as C
        # the request starts unpriced: only the demand rungs exist
        assert [s["stage"] for s in svc.get_pr(pid)["steps"]] == C.DEMAND_STAGES
        for i, stage in enumerate(order):
            # nobody further down the ladder can act yet (whether or not their
            # rung exists yet — an unbuilt value rung is 'forbidden' too)
            for later in order[i + 1:]:
                assert svc.act_on_step(pid, _USERS[later], "approve")[1] == "forbidden"
            if stage == "purchasing":
                # pricing + RFQ gates fire here; satisfying pricing is what
                # appends the value-gated L2 directors to the ladder
                _gate_and_price(svc, pid, _USERS["purchasing"])
                assert [s["stage"] for s in svc.get_pr(pid)["steps"]] == order \
                    == C.build_ladder(48000)
                # ...and the later rungs are STILL out of turn now they exist
                for later in order[i + 1:]:
                    assert svc.act_on_step(pid, _USERS[later], "approve")[1] == "forbidden"
            ok, msg = svc.act_on_step(pid, _USERS[stage], "approve")
            assert ok is True
            assert msg == ("approved" if stage == order[-1] else "advanced")
        b = svc.get_pr(pid)
        assert b["pr"]["status"] == "approved" and b["pr"]["po_no"]
        assert all(s["status"] == "approved" for s in b["steps"])


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
    """Warehouse and Purchasing approve (Purchasing through the pricing and RFQ
    gates), then the Plant Director rejects -> whole PR rejected; earlier
    signatures kept, the Supply Chain Director never reached.

    The rejecting rung is a DOAM L2 director rather than Finance: at 48,000 EGP
    the DOAM's tier 2 ladder ends at the two directors and never reaches Finance
    or the CFO at all."""
    a, c = app_client
    pid = _make_pr(a, c, "RejLate")
    with a.app_context():
        from app.approvals import services as svc
        assert svc.act_on_step(pid, _USERS["warehouse"], "approve")[1] == "advanced"
        # purchasing cannot sign an unpriced request, and once priced at 48,000
        # cannot sign without competitive quotes; both gates asserted here
        _gate_and_price(svc, pid, _USERS["purchasing"])
        assert svc.act_on_step(pid, _USERS["purchasing"], "approve")[1] == "advanced"
        ok, msg = svc.act_on_step(pid, _USERS["factory_manager"], "reject",
                                  comment="too costly")
        assert ok and msg == "rejected"
        b = svc.get_pr(pid)
        assert b["pr"]["status"] == "rejected"
        st = {s["stage"]: s["status"] for s in b["steps"]}
        assert st["warehouse"] == "approved" and st["purchasing"] == "approved"
        assert st["factory_manager"] == "rejected"
        assert st["scd"] == "pending"  # never reached


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
        # Reject at the SECOND demand rung. Under the DOAM that is Purchasing,
        # not the Plant Director — an unpriced request only carries the demand
        # rungs, and the Plant Director became value-gated at tier 2.
        from app.approvals import constants as C
        svc.act_on_step(pid, _USERS[C.DEMAND_STAGES[1]], "reject", comment="redo")
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
