"""Run: python app/approvals/tests_stock_outbox.py (isolated SQLite database)."""
import os
import sys
import tempfile
from pathlib import Path


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
    import config
    config.Config.DATABASE_URL = ""
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="stock_outbox_"), "stock.db")
    from app import create_app
    app = create_app()
    with app.app_context():
        checks()
    print("all checks passed")


def checks():
    from app.db import get_db
    from app.approvals import services as svc
    from app.maintenance import services as msvc
    from app.warehouse import services as wsvc
    buyer = {"username": "test_buyer", "role": "purchasing_manager", "id": 1}
    store = {"username": "test_store", "role": "warehouse_manager", "id": 2}

    def one(sql, *args):
        conn = get_db()
        try:
            r = conn.execute(sql, args).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()

    def make(title, item, qty, unit, so_no=None):
        """A request exactly as the UI makes one (no prices), then priced."""
        pr_id, _ = svc.create_pr(
            {"title": title, "department": "Maintenance", "so_no": so_no},
            [{"item": item, "qty": qty, "unit_price": 0}],
            {"username": "test_requester", "id": 9}, priced=False)
        li = one("SELECT id FROM pr_items WHERE pr_id=?", pr_id)["id"]
        ok, msg = svc.price_pr(pr_id, {li: unit}, {"tax_rate": 0}, buyer)
        assert ok, "pricing failed: %s" % msg
        return pr_id, li

    def approve_all(pr_id):
        """Walk the REAL ladder to po_issued — one distinct signer per stage,
        because segregation of duties refuses a second stage from one person."""
        from app.approvals.constants import STAGE_ROLES
        # the RFQ gate is not what this test is about — justify it once
        svc.set_single_source(pr_id, "Sole authorised supplier for this part", buyer)
        # nor is the DOAM off-plan memo (repeat test items trip it)
        svc.set_doam_document(pr_id, "deviation_memo", "Test justification memo", buyer)
        for _ in range(20):
            conn = get_db()
            pr = conn.execute("SELECT status, current_seq FROM pr_requests WHERE id=?",
                              (pr_id,)).fetchone()
            steps = conn.execute(
                "SELECT stage FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
                (pr_id, pr["current_seq"])).fetchall()
            conn.close()
            if pr["status"] != "pending" or not steps:
                break
            for s in steps:
                role = sorted(STAGE_ROLES[s["stage"]])[0]
                ok, msg = svc.act_on_step(
                    pr_id, {"username": "test_%s_%s" % (role, pr_id), "role": role,
                            "id": 100 + pr_id}, "approve", "ok")
                assert ok, "ladder stuck at %s: %s" % (s["stage"], msg)
        svc.issue_po(pr_id, buyer)
        st = one("SELECT status FROM pr_requests WHERE id=?", pr_id)["status"]
        assert st == "po_issued", "expected po_issued, got %s" % st
        return one("SELECT po_no FROM pr_requests WHERE id=?", pr_id)["po_no"]

    def execute(sql, *args):
        conn = get_db()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def spare_stock():
        return one("SELECT stock_qty FROM mnt_spare_parts WHERE id=?", spare)["stock_qty"]

    def material_stock():
        return one("SELECT stock_qty FROM wh_materials WHERE id=?", material)["stock_qty"]

    spare = execute("INSERT INTO mnt_spare_parts (code,name,stock_qty,reserved_qty) VALUES (?,?,0,0)",
                    "OUTBOX-SPARE", "Outbox spare")
    assert wsvc.create_material({"code": "OUTBOX-MAT", "name": "Outbox material", "kind": "trim"}, store)[0]
    material = one("SELECT id FROM wh_materials WHERE code='OUTBOX-MAT'")["id"]
    pr, _ = svc.create_pr({"title": "Mixed receipt", "department": "Maintenance"},
                         [{"item": "Outbox spare", "qty": 4, "spare_id": spare},
                          {"item": "Outbox material", "qty": 6}],
                         {"username": "test_requester", "id": 9}, priced=False)
    conn = get_db()
    lines = conn.execute("SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq", (pr,)).fetchall()
    conn.close()
    ls, lm = [r["id"] for r in lines]
    assert svc.price_pr(pr, {ls: 10, lm: 20}, {"tax_rate": 0}, buyer)[0]
    approve_all(pr)
    assert svc.receive_items(pr, {str(ls): 4, lm: 6}, store)[0]
    assert one("SELECT COUNT(*) c FROM pr_stock_postings WHERE pr_id=? AND status='posted'", pr)["c"] == 2
    assert spare_stock() == 4 and material_stock() == 6
    assert svc.post_stock_postings(pr) == {"posted": 0, "failed": 0}
    other, lo = make("Not a material", "Unknown sundry item", 1, 5)
    approve_all(other)
    assert svc.receive_items(other, {lo: 1}, store)[0]
    assert one("SELECT status FROM pr_stock_postings WHERE pr_id=?", other)["status"] == "skipped"
    assert svc.post_stock_postings(pr, manual=True) == {"posted": 0, "failed": 0}
    assert spare_stock() == 4 and material_stock() == 6
    # a skipped line says why, is listed for the PR, and a corrected master is retried
    assert one("SELECT last_error FROM pr_stock_postings WHERE pr_id=?", other)["last_error"] == "no_matching_material"
    assert svc.stock_posting_issues(other, ("skipped",))
    assert wsvc.create_material({"code": "SUNDRY-1", "name": "Unknown sundry item", "kind": "trim"}, store)[0]
    assert svc.post_stock_postings(other) == {"posted": 0, "failed": 0}      # automatic path leaves it
    assert svc.post_stock_postings(other, manual=True)["posted"] == 1
    # two active materials share the line's name: never guess the colour — fail visibly, bell once
    for code, colour in (("AMB-WHT", "White"), ("AMB-NVY", "Navy")):
        assert wsvc.create_material({"code": code, "name": "Ambiguous jersey", "color": colour,
                                     "kind": "trim"}, store)[0]
    amb, la = make("Ambiguous", "Ambiguous jersey", 2, 5)
    approve_all(amb)
    assert svc.receive_items(amb, {la: 2}, store)[0]
    row = one("SELECT status, last_error FROM pr_stock_postings WHERE pr_id=?", amb)
    assert row == {"status": "failed", "last_error": "ambiguous_material"}, row
    assert svc.post_stock_postings(amb)["failed"] == 1
    assert one("SELECT COUNT(*) c FROM notifications WHERE title='Receipt not booked: ambiguous material'")["c"] == 1
    assert one("SELECT COALESCE(SUM(stock_qty),0) s FROM wh_materials WHERE code LIKE 'AMB-%'")["s"] == 0

    bad, line = make("Failed receipt", "Outbox spare", 3, 10)
    execute("UPDATE pr_items SET spare_id=? WHERE id=?", spare, line)
    approve_all(bad)
    # Keep the line valid, but queue a missing target to exercise the store refusal.
    conn = get_db()
    svc._queue_stock_postings(conn, bad, "grn:test-missing", {line: 3})
    conn.execute("UPDATE pr_stock_postings SET target_id=? WHERE pr_id=?", (spare + 999999, bad))
    conn.commit()
    conn.close()
    assert svc.post_stock_postings(bad)["failed"] == 1
    issues = svc.stock_posting_issues(bad)
    assert len(issues) == 1 and issues[0]["last_error"] and issues[0]["status"] == "failed"
    execute("UPDATE pr_stock_postings SET target_id=?, attempts=5 WHERE pr_id=?", spare, bad)
    assert svc.post_stock_postings(bad) == {"posted": 0, "failed": 0}
    assert svc.post_stock_postings(bad, manual=True)["posted"] == 1
    assert svc.post_stock_postings(bad, manual=True)["posted"] == 0
    assert spare_stock() == 7
    assert not svc.stock_posting_issues(bad)

    door, ld = make("Warehouse door", "Outbox material", 2, 20)
    approve_all(door)
    assert svc.receive_items(door, {ld: 2}, store, post_stock=False)[0]
    assert one("SELECT COUNT(*) c FROM pr_stock_postings WHERE pr_id=?", door)["c"] == 0
    assert material_stock() == 6

    # A claim with an uncertain outcome requires store inspection, even on manual retry.
    conn = get_db()
    svc._queue_stock_postings(conn, door, "grn:test-posting", {ld: 2})
    conn.execute("UPDATE pr_stock_postings SET status='posting' WHERE pr_id=?", (door,))
    conn.commit()
    conn.close()
    assert svc.post_stock_postings(door, manual=True)["posted"] == 0
    assert svc.stock_posting_issues(door)[0]["status"] == "posting"
    assert material_stock() == 6
    posting = svc.stock_posting_issues(door)[0]["id"]
    assert svc.resolve_stuck_posting(door, posting, "failed", "  ", buyer) == (False, "note_required")
    assert svc.resolve_stuck_posting(door, posting, "failed", "No stock movement found", buyer) == (True, "failed")
    assert one("SELECT status FROM pr_stock_postings WHERE id=?", posting)["status"] == "failed"
    assert one("SELECT id FROM pr_events WHERE pr_id=? AND action='stock_posting_resolved'", door)
    assert svc.resolve_stuck_posting(door, posting, "posted", "Checked again", buyer) == (False, "not_stuck")
    assert material_stock() == 6

    tid, err = msvc.create_ticket({"description": "Waiting for outbox spare"}, store)
    assert tid, err
    execute("UPDATE mnt_tickets SET status='waiting_stock', assigned_to=? WHERE id=?", "test_assignee", tid)
    execute("INSERT INTO users (username,full_name,role,password_hash,is_active) VALUES (?,?,?,'',1)",
            "test_assignee", "Test Assignee", "maintenance_technician")
    req = execute("INSERT INTO mnt_requests (ticket_id,status) VALUES (?,'out_of_stock')", tid)
    # 20 > what this receipt brings: the request stays short, so arrival keeps
    # notifying (a request the receipt fully covers is resumed instead).
    execute("INSERT INTO mnt_request_items (request_id,spare_id,qty_requested) VALUES (?,?,20)", req, spare)
    assert msvc.receive_stock(spare, 2, 10, store)[0]
    assert one("SELECT status FROM mnt_tickets WHERE id=?", tid)["status"] == "waiting_stock"
    assert one("SELECT COUNT(*) c FROM mnt_notifications WHERE entity_type='ticket' AND entity_id=? AND title='Spare part arrived'", tid)["c"] == 2
    assert one("SELECT COUNT(*) c FROM notifications WHERE target_user=? AND title='Spare part arrived'", "test_assignee")["c"] == 1
    assert spare_stock() == 9

    # a PR raised from a ticket (free-text line, no spare link) still tells the ticket team
    tpr, tl = make("Ticket repair", "Repair", 1, 5)
    execute("UPDATE pr_requests SET source_module='maintenance', source_ref=? WHERE id=?",
            "ticket:%s" % tid, tpr)
    approve_all(tpr)
    assert svc.receive_items(tpr, {tl: 1}, store)[0]
    arrived = "SELECT COUNT(*) c FROM mnt_notifications WHERE entity_type='ticket' AND entity_id=? AND title LIKE 'Parts arrived%'"
    assert one(arrived, tid)["c"] == 1
    svc.post_stock_postings(tpr, manual=True)          # a retry must not repeat it
    assert one(arrived, tid)["c"] == 1
    assert one("SELECT COUNT(*) c FROM notifications WHERE target_user=? AND title='Parts arrived'",
               "test_assignee")["c"] == 1

    # a ticket PR whose line IS spare-linked: one arrival notice for that ticket, not two
    spr, sl = make("Ticket spare", "Outbox spare", 1, 10)
    execute("UPDATE pr_items SET spare_id=? WHERE id=?", spare, sl)
    execute("UPDATE pr_requests SET source_module='maintenance', source_ref=? WHERE id=?",
            "ticket:%s" % tid, spr)
    approve_all(spr)
    assert svc.receive_items(spr, {sl: 1}, store)[0]
    assert one("SELECT COUNT(*) c FROM notifications WHERE target_user=? AND title='Parts arrived'",
               "test_assignee")["c"] == 2
    assert one("SELECT COUNT(*) c FROM notifications WHERE target_user=? AND title='Spare part arrived'",
               "test_assignee")["c"] == 1


if __name__ == "__main__":
    run()
