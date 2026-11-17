"""Isolated self-test: python -m app.approvals.tests_e2e_maint_chain."""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
    import config
    config.Config.DATABASE_URL = ""
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="maint_chain_"), "chain.db")
    from app import create_app
    app = create_app()
    with app.app_context():
        checks()
    print("all checks passed")


def checks():
    from flask import current_app
    from app.db import get_db
    from app.maintenance import services as maint
    from app.maintenance import procure_bridge
    from app.approvals import services as proc
    from app.approvals.constants import STAGE_ROLES, ROLE_LEVEL, holds_authority
    from app.warehouse import services as warehouse
    from app.routes.approvals import bp as procurement_bp

    app = current_app._get_current_object()

    def rows(sql, *args):
        conn = get_db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def one(sql, *args):
        result = rows(sql, *args)
        assert len(result) == 1, "Expected one row: %s; got %r" % (sql, result)
        return result[0]

    def seed(sql, *args):
        """SQL writes are limited to new master data and user accounts."""
        conn = get_db()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def seed_user(role, suffix):
        username = "e2e_" + suffix
        seed("INSERT INTO users (username,full_name,role,password_hash,is_active) "
             "VALUES (?,?,?,'',1)", username, username, role)
        return one("SELECT id,username,full_name,role FROM users WHERE username=?", username)

    requester = seed_user("maintenance_requester", "requester")
    tech = seed_user("maintenance_technician", "tech")
    manager = seed_user("maintenance_manager", "maint_manager")
    factory = seed_user("factory_manager", "factory_manager")
    store = seed_user("storekeeper", "store")
    seed_user("warehouse_manager", "warehouse_manager")
    buyer = seed_user("purchasing_manager", "buyer")
    machine = seed("INSERT INTO mnt_machines (code,name,status,is_active) "
                   "VALUES (?,?,'running',1)", "E2E-MACHINE", "E2E machine")
    spare = seed("INSERT INTO mnt_spare_parts "
                 "(code,name,criticality,stock_qty,reserved_qty,reorder_level,max_level,avg_cost,is_active) "
                 "VALUES (?,?,'critical',3,0,2,10,50,1)", "E2E-SPARE", "E2E spare")

    def spare_state():
        return one("SELECT * FROM mnt_spare_parts WHERE id=?", spare)

    def ticket_state(tid):
        return one("SELECT * FROM mnt_tickets WHERE id=?", tid)

    def machine_state():
        return one("SELECT status,cost_to_date,breakdowns,total_downtime_min "
                   "FROM mnt_machines WHERE id=?", machine)

    def get_as(user, path):
        # A fresh app context also gives each HTTP request its own auth g cache.
        with app.app_context(), app.test_client() as client:
            with client.session_transaction() as session:
                session["uid"] = user["id"]
                session["ep"] = one("SELECT session_epoch FROM users WHERE id=?",
                                    user["id"])["session_epoch"] or 0
            return client.get(path)

    def my_work(user, section):
        """One /my-work section's HTML (sign_queue, maint_approvals, my_tickets ...).
        Sections, not the whole page: the unread-alerts list legitimately repeats
        broadcast bell text, which is not an action item."""
        response = get_as(user, "/my-work")
        assert response.status_code == 200, "BUG: /my-work refused %s: %s" % (
            user["role"], response.status_code)
        html = response.get_data(as_text=True)
        start = html.find('data-i18n="mywork.%s"' % section)
        if start < 0:
            return ""
        end = html.find('data-i18n="mywork.', start + 1)
        return html[start:end if end > 0 else len(html)]

    def open_ticket():
        tid, err = maint.create_ticket({
            "description": "E2E stopped machine needs a replacement spare",
            "machine_id": machine, "machine_code": "E2E-MACHINE",
            "production_stopped": 1}, requester)
        assert tid and not err, "BUG: requester could not open ticket: %s" % err
        assert ticket_state(tid)["status"] == "submitted", "BUG: ticket was not submitted"
        assert machine_state()["status"] == "stopped", "BUG: stopped report did not stop machine"
        return tid

    def assign(tid):
        ticket = ticket_state(tid)
        result = maint.review_assign(tid, tech["username"], "medium",
                                     ticket["response_due"], ticket["resolution_due"], manager)
        assert result[0], "BUG: assignment failed: %r" % (result,)
        assert ticket_state(tid)["assigned_to"] == tech["username"], "BUG: assignment was not saved"
        assert one("SELECT COUNT(*) c FROM notifications WHERE target_user=? "
                   "AND title='Work order assigned'", tech["username"])["c"] == 1, (
                       "BUG: assigned technician did not get one personal work-order notice")

    def diagnose(tid):
        result = maint.add_diagnosis(tid, {"spare_needed": True,
                                          "fault_found": "Worn spare"}, tech)
        assert result[0], "BUG: diagnosis failed: %r" % (result,)
        assert ticket_state(tid)["status"] == "spare_required", "BUG: diagnosis did not request spares"

    def request_spare(tid):
        items = [{"spare_id": spare, "qty": 2}]
        conn = get_db()
        try:
            assert maint._pick_matrix_levels(conn, items) == [
                "maintenance_manager", "factory_manager"], "BUG: critical spare has wrong matrix"
        finally:
            conn.close()
        rid, err = maint.create_request(tid, items, "Replace worn spare", "urgent", tech)
        assert rid and not err, "BUG: spare request failed: %s" % err
        approvals = rows("SELECT * FROM mnt_approvals WHERE request_id=? ORDER BY level", rid)
        assert [(a["level"], a["approver_role"], a["status"]) for a in approvals] == [
            (1, "maintenance_manager", "pending"), (2, "factory_manager", "pending")
        ], "BUG: spare request did not build the two pending rungs"
        return rid, approvals

    def sign_maint(approval, user):
        result = maint.decide_approval(approval["id"], "approve", "E2E reviewed", user)
        assert result[0], "BUG: maintenance approval failed: %r" % (result,)
        assert one("SELECT status FROM mnt_approvals WHERE id=?", approval["id"])[
            "status"] == "approved", "BUG: maintenance signature was not persisted"

    def adjust_count():
        result = maint.adjust_stock(spare, 1, "E2E physical count correction", store)
        assert result[0], "BUG: stock adjustment failed: %r" % (result,)
        assert spare_state()["stock_qty"] == 1, "BUG: adjusted stock is not one"

    def price(pr_id, unit_price):
        lines = rows("SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq", pr_id)
        result = proc.price_pr(pr_id, {line["id"]: unit_price for line in lines},
                               {"tax_rate": 0}, buyer)
        assert result[0], "BUG: purchasing could not price PR: %r" % (result,)
        assert one("SELECT pricing_status FROM pr_requests WHERE id=?", pr_id)[
            "pricing_status"] == "priced", "BUG: pricing gate did not persist"
        return rows("SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq", pr_id)

    def sign_pr_ladder(pr_id):
        """Seed one real, independent account per actual stage, including added rungs."""
        signers = {}
        signed = []
        checked_repeat = False
        for _ in range(40):
            pr = one("SELECT * FROM pr_requests WHERE id=?", pr_id)
            if pr["status"] == "approved":
                break
            assert pr["status"] == "pending", "BUG: PR left its ladder unexpectedly: %s" % pr["status"]
            steps = rows("SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq,id", pr_id)
            for step in steps:
                if step["id"] not in signers:
                    roles = [role for role in sorted(STAGE_ROLES[step["stage"]])
                             if role in ROLE_LEVEL and holds_authority(role, step["stage"])]
                    assert roles, "BUG: no authoritative role for %s" % step["stage"]
                    # Warehouse manager can sign the store rung; a second account signs its own rung.
                    role = "warehouse_manager" if step["stage"] == "warehouse" else roles[0]
                    user = seed_user(role, "pr_%s_step_%s" % (pr_id, step["id"]))
                    assert proc.can_act(user, step["stage"]), "BUG: seeded role cannot sign its stage"
                    signers[step["id"]] = user
            pending = [s for s in steps if s["status"] == "pending" and s["seq"] == pr["current_seq"]]
            assert pending, "BUG: pending PR has no active rung"
            step = pending[0]
            user = signers[step["id"]]
            assert pr["pr_no"] in my_work(user, "sign_queue"), "BUG: pending approver cannot see PR on /my-work"
            assert any(p["id"] == pr_id for p in proc.my_queue(user)), "BUG: PR absent from approver queue"
            if signed and not checked_repeat:
                result = proc.act_on_step(pr_id, signed[-1], "approve", "E2E repeat signer probe")
                assert not result[0] and result[1] in ("dual_role", "forbidden"), (
                    "BUG: previous signer was allowed the next stage: %r" % (result,))
                checked_repeat = True
            result = proc.act_on_step(pr_id, user, "approve", "E2E reviewed")
            if result == (False, "needs_quotes"):
                justified = proc.set_single_source(pr_id, "Sole authorised supplier for this E2E item", buyer)
                assert justified[0], "BUG: single-source justification failed: %r" % (justified,)
                continue
            if result == (False, "deviation_memo_required"):
                justified = proc.set_doam_document(
                    pr_id, "deviation_memo", "E2E replenishment quantity justified by the physical count", buyer)
                assert justified[0], "BUG: deviation justification failed: %r" % (justified,)
                continue
            assert result[0], "BUG: procurement ladder stuck at %s: %r" % (step["stage"], result)
            signed.append(user)
        else:
            assert False, "BUG: procurement ladder did not finish within 40 actions"
        assert checked_repeat, "BUG: ladder did not exercise the repeated-signer refusal"
        completed = rows("SELECT stage,status,approver_user FROM pr_steps WHERE pr_id=?", pr_id)
        assert "purchasing" in {s["stage"] for s in completed}, (
            "BUG: PR approved without the purchasing rung")
        assert all(s["status"] == "approved" for s in completed), "BUG: PR approved with unsigned rungs"
        assert len({s["approver_user"] for s in completed}) == len(completed), (
            "BUG: procurement stages were not signed by distinct users")

    def issue_po(pr_id):
        result = proc.issue_po(pr_id, buyer)
        assert result[0], "BUG: PO issue failed: %r" % (result,)
        assert one("SELECT status FROM pr_requests WHERE id=?", pr_id)["status"] == "po_issued", (
            "BUG: issued PO has wrong status")

    def receive(pr_id, lines):
        result = proc.receive_items(pr_id, {line["id"]: line["qty"] for line in lines}, store)
        assert result == (True, "received"), "BUG: full goods receipt failed: %r" % (result,)
        postings = rows("SELECT * FROM pr_stock_postings WHERE pr_id=?", pr_id)
        assert len(postings) == 1 and postings[0]["status"] == "posted", (
            "BUG: receipt did not produce exactly one posted stock row: %r" % postings)
        return postings[0]

    def retry_postings(pr_id):
        assert proc.post_stock_postings(pr_id) == {"posted": 0, "failed": 0}, (
            "BUG: automatic stock retry posted a completed receipt again")
        assert proc.post_stock_postings(pr_id, manual=True) == {"posted": 0, "failed": 0}, (
            "BUG: manual stock retry posted a completed receipt again")

    def arrival_notices(tid):
        assert one("SELECT COUNT(*) c FROM notifications WHERE target_user=? "
                   "AND title='Spare part arrived' AND link=?", tech["username"],
                   "/maintenance/tickets/%s" % tid)["c"] == 1, (
                       "BUG: technician did not receive exactly one spare-arrival notice")
        assert one("SELECT COUNT(*) c FROM mnt_notifications WHERE target_role='maintenance_manager' "
                   "AND entity_type='ticket' AND entity_id=? AND title='Spare part arrived'", tid)["c"] == 1, (
                       "BUG: manager inbox did not receive exactly one spare-arrival notice")

    def issue(rid, tid):
        before = spare_state()
        result = maint.issue_parts(rid, tech["username"], store)
        assert result[0], "BUG: spare issue failed: %r" % (result,)
        after = spare_state()
        assert after["stock_qty"] == before["stock_qty"] - 2 and after["reserved_qty"] == 0, (
            "BUG: issue did not consume two parts and release the reservation")
        assert ticket_state(tid)["cost"] == round(2 * before["avg_cost"], 2), (
            "BUG: ticket did not receive the issued parts' current average cost")

    def confirm_receiving(rid, tid):
        result = maint.confirm_receiving(rid, tech)
        assert result[0], "BUG: technician receipt confirmation failed: %r" % (result,)
        assert one("SELECT status FROM mnt_requests WHERE id=?", rid)["status"] == "received", (
            "BUG: technician receipt was not saved")
        assert ticket_state(tid)["status"] == "repair", "BUG: received parts did not start repair"

    def repair(tid):
        result = maint.repair_proof(tid, {"action_performed": "Replaced spare; checked machine",
                                        "machine_running": "yes"}, tech)
        assert result[0], "BUG: repair proof failed: %r" % (result,)
        assert ticket_state(tid)["status"] == "testing", "BUG: repair did not advance to testing"

    def test_machine(tid):
        result = maint.record_test(tid, {"test_result": "pass", "safety_check": "pass",
                                       "machine_running": "yes"}, tech)
        assert result[0], "BUG: machine test failed: %r" % (result,)
        assert ticket_state(tid)["status"] == "resolved", "BUG: passed test did not resolve ticket"

    def close(tid):
        result = maint.close_ticket(tid, manager)
        assert result[0], "BUG: ticket closure failed: %r" % (result,)
        assert ticket_state(tid)["status"] == "closed", "BUG: ticket did not close"

    def reopen(tid):
        result = maint.reopen_ticket(tid, manager)
        assert result[0], "BUG: ticket reopen failed: %r" % (result,)
        assert ticket_state(tid)["status"] == "reopened", "BUG: ticket did not reopen"

    # Stop, assign, diagnose, then lose stock between the two approval signatures.
    baseline = machine_state()
    tid = open_ticket()
    assign(tid)
    diagnose(tid)
    ticket_no = ticket_state(tid)["ticket_no"]
    assert ticket_no in my_work(requester, "my_tickets"), "BUG: requester cannot see their open ticket on /my-work"
    assert ticket_no in my_work(tech, "my_tickets"), "BUG: technician cannot see assigned ticket on /my-work"
    for path in ("/maintenance/approvals", procurement_bp.url_prefix + "/"):
        response = get_as(requester, path)
        assert response.status_code == 403, "BUG: maintenance requester was not forbidden from %s: %s" % (
            path, response.status_code)
    rid, approvals = request_spare(tid)
    request_no = one("SELECT request_no FROM mnt_requests WHERE id=?", rid)["request_no"]
    assert request_no in my_work(manager, "maint_approvals"), "BUG: level-one manager cannot see pending request"
    assert request_no not in my_work(factory, "maint_approvals"), (
        "BUG: factory manager is asked to decide before level one is signed")
    assert maint.decide_approval(approvals[1]["id"], "approve", "Too early", factory) == (
        False, "earlier_level_pending"), "BUG: maintenance allowed level two before level one"
    assert maint.decide_approval(approvals[0]["id"], "approve", "Wrong role", tech) == (
        False, "wrong_role"), "BUG: maintenance accepted wrong role at level one"
    sign_maint(approvals[0], manager)
    assert request_no in my_work(factory, "maint_approvals"), "BUG: factory manager cannot see request after level one"
    adjust_count()
    sign_maint(approvals[1], factory)
    assert one("SELECT status FROM mnt_requests WHERE id=?", rid)["status"] == "out_of_stock", (
        "BUG: final approval failed to recheck physical availability")
    assert ticket_state(tid)["status"] == "waiting_stock", "BUG: short ticket is not waiting for stock"
    assert spare_state()["reserved_qty"] == 0, "BUG: short request reserved unavailable stock"

    # The adjustment's real bridge call raises exactly one replenishment PR.
    def active_reorders():
        return rows("SELECT * FROM pr_requests WHERE source_module='maintenance' AND source_ref=? "
                    "AND is_active=1 AND status NOT IN ('rejected','cancelled','closed','received')",
                    "spare:%s" % spare)

    reorders = active_reorders()
    assert len(reorders) == 1, "BUG: stock adjustment did not raise exactly one active reorder PR"
    assert procure_bridge.auto_reorder_check(spare_ids=[spare]) == [], "BUG: reorder check created a duplicate PR"
    assert len(active_reorders()) == 1, "BUG: repeated reorder check changed active PR count"
    pr_id = reorders[0]["id"]
    assert reorders[0]["pricing_status"] == "unpriced", "BUG: auto-reorder bypassed purchasing pricing"
    lines = price(pr_id, 50)
    assert len(lines) == 1 and lines[0]["spare_id"] == spare, "BUG: reorder lost its spare link"
    qty = lines[0]["qty"]
    assert qty == 9, "BUG: auto-reorder did not refill the counted stock to max level"
    sign_pr_ladder(pr_id)
    assert proc.receive_items(pr_id, {lines[0]["id"]: qty}, store) == (False, "not_receivable"), (
        "BUG: procurement accepted goods before PO issuance")
    issue_po(pr_id)
    posting = receive(pr_id, lines)
    assert posting["target"] == "spare" and posting["target_id"] == spare, "BUG: receipt targeted wrong spare"
    assert spare_state()["stock_qty"] == 1 + qty, "BUG: procurement receipt did not replenish spare stock"
    arrival_notices(tid)
    retry_postings(pr_id)
    assert spare_state()["stock_qty"] == 1 + qty, "BUG: posting retries duplicated spare stock"
    arrival_notices(tid)

    # Arrival is notification-only: create_request builds the new approval rows.
    assert one("SELECT status FROM mnt_requests WHERE id=?", rid)["status"] == "out_of_stock", (
        "BUG: arrival mutated the old out-of-stock request")
    assert ticket_state(tid)["status"] == "waiting_stock", "BUG: arrival bypassed spare re-approval"
    rid2, approvals2 = request_spare(tid)
    sign_maint(approvals2[0], manager)
    sign_maint(approvals2[1], factory)
    assert one("SELECT status FROM mnt_requests WHERE id=?", rid2)["status"] == "approved", (
        "BUG: re-raised spare request was not approved")
    assert spare_state()["reserved_qty"] == 2, "BUG: approval did not reserve two parts"
    issue(rid2, tid)
    confirm_receiving(rid2, tid)
    repair(tid)
    test_machine(tid)
    # Backdate the ticket two hours so downtime is measurable (a test run closes within a minute).
    seed("UPDATE mnt_tickets SET created_at=? WHERE id=?",
         (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"), tid)
    close(tid)
    assert ticket_state(tid)["total_downtime_min"] >= 119, "BUG: close did not measure downtime"
    closed = machine_state()
    assert closed["status"] == "running", "BUG: closure did not restart machine"
    assert closed["cost_to_date"] == baseline["cost_to_date"] + ticket_state(tid)["cost"], (
        "BUG: ticket cost did not roll into machine exactly once")
    assert closed["breakdowns"] == baseline["breakdowns"] + 1, "BUG: closure did not count one breakdown"
    assert closed["total_downtime_min"] == baseline["total_downtime_min"] + ticket_state(tid)["total_downtime_min"], (
        "BUG: stopped production downtime was not rolled into machine")
    reopen(tid)
    repair(tid)
    test_machine(tid)
    close(tid)
    assert machine_state() == closed, "BUG: reopen/re-close double-counted machine cost, breakdowns or downtime"

    # A non-spare material line travels through the same procurement receipt outbox.
    def create_material():
        result = warehouse.create_material({"code": "E2E-MATERIAL", "name": "E2E material",
                                            "kind": "trim", "uom": "pcs"}, store)
        assert result[0], "BUG: warehouse material creation failed: %r" % (result,)
        return one("SELECT * FROM wh_materials WHERE code='E2E-MATERIAL'")

    def request_material():
        # Use Warehouse department: this is a material purchase, not an engineering spare.
        pr_id, pr_no = proc.create_pr({"title": "E2E warehouse material", "department": "Warehouse"},
                                     [{"item": "E2E material", "qty": 6, "unit_price": 0}],
                                     store, priced=False)
        assert pr_id and pr_no, "BUG: warehouse material PR creation failed"
        return pr_id

    material = create_material()
    material_pr = request_material()
    material_lines = price(material_pr, 20)
    assert len(material_lines) == 1 and material_lines[0]["spare_id"] is None, (
        "BUG: warehouse material line unexpectedly became a maintenance spare")
    sign_pr_ladder(material_pr)
    issue_po(material_pr)
    posting = receive(material_pr, material_lines)
    assert posting["target"] == "material" and posting["target_id"] == material["id"], (
        "BUG: non-spare line did not resolve to the named warehouse material")
    expected_stock = material["stock_qty"] + material_lines[0]["qty"]
    assert one("SELECT stock_qty FROM wh_materials WHERE id=?", material["id"])["stock_qty"] == expected_stock, (
        "BUG: material receipt did not increase warehouse stock by the ordered quantity")
    retry_postings(material_pr)
    assert one("SELECT stock_qty FROM wh_materials WHERE id=?", material["id"])["stock_qty"] == expected_stock, (
        "BUG: posting retry duplicated warehouse material stock")


if __name__ == "__main__":
    run()
