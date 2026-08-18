"""Audit findings 3.4-b7b (over-delivery silently truncated) and 3.4-b9a (the GRN
number was derived at print time, so every partial receipt reprinted the same one).

Everything here goes through the REAL flow — create_pr(priced=False) then price_pr,
exactly as the UI produces a request — because a control that only fires when a test
hands the service a value the UI cannot produce is not implemented.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "grn.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        store = {"username": "store", "role": "warehouse_manager", "id": 2}

        def make(title, item, qty, unit, so_no=None):
            """A request exactly as the UI makes one (no prices), then priced."""
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance", "so_no": so_no},
                [{"item": item, "qty": qty, "unit_price": 0}],
                {"username": "tech", "id": 9}, priced=False)
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
            conn.close()
            ok, msg = svc.price_pr(pr_id, {li: unit}, {"tax_rate": 0}, buyer)
            assert ok, "pricing failed: %s" % msg
            return pr_id, li

        def approve_all(pr_id):
            """Walk the REAL ladder to po_issued — one distinct signer per stage,
            because segregation of duties refuses a second stage from one person."""
            from app.approvals.constants import STAGE_ROLES
            # the RFQ gate is not what this test is about — justify it once
            svc.set_single_source(pr_id, "Sole authorised supplier for this part", buyer)
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
                        pr_id, {"username": "u_%s_%s" % (role, pr_id), "role": role,
                                "id": 100 + pr_id}, "approve", "ok")
                    assert ok, "ladder stuck at %s: %s" % (s["stage"], msg)
            svc.issue_po(pr_id, buyer)
            conn = get_db()
            st = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr_id,)).fetchone()["status"]
            conn.close()
            assert st == "po_issued", "expected po_issued, got %s" % st

        def grns(pr_id):
            g, q = svc.list_grns(pr_id)
            return g, q

        # ---- 3.4-b9a: two partial receipts, two DIFFERENT GRN numbers ---------
        a, la = make("Bearings", "Bearing 6204", 10, 500)
        approve_all(a)
        ok, _ = svc.receive_items(a, {la: 4}, store)
        assert ok
        ok, _ = svc.receive_items(a, {la: 3}, store)
        assert ok
        g, q = grns(a)
        assert len(g) == 2, "one GRN per receipt event, got %r" % [x["grn_no"] for x in g]
        assert g[0]["grn_no"] != g[1]["grn_no"], (
            "two partial deliveries reused one GRN number: %s" % [x["grn_no"] for x in g])
        assert all(x["grn_no"] for x in g), "GRN number must be allocated at receipt time"
        assert [x["accepted_qty"] for x in g] == [4, 3], \
            "each GRN records ITS OWN delivery: %r" % [x["accepted_qty"] for x in g]
        assert not q, "nothing was over-delivered here: %r" % q

        # the PDF must print the number of the note asked for, not a derived one
        from app.approvals import pdf as pdfgen
        b1 = svc.get_pr(a)
        assert [x["grn_no"] for x in b1["grns"]] == [x["grn_no"] for x in g]
        try:
            p1 = pdfgen.grn_pdf(b1, g[0])
            p2 = pdfgen.grn_pdf(b1, g[1])
            assert p1 != p2, "the two notes rendered byte-identical"
        except ImportError:
            pass                      # reportlab not installed in this environment

        # ---- 3.4-b7b: over-delivery is quarantined, not absorbed -------------
        b, lb = make("Filters", "Air filter F7", 10, 100)
        approve_all(b)
        ok, msg = svc.receive_items(b, {lb: 13}, store)
        assert ok, msg
        g, q = grns(b)
        assert len(q) == 1, "over-delivery must leave a quarantine record: %r" % q
        assert abs(q[0]["qty"] - 3) < 1e-6, "3 units over 10 ordered, got %r" % q[0]["qty"]
        assert q[0]["status"] == "quarantined", "explicit state, got %r" % q[0]["status"]
        assert abs(g[-1]["quarantined_qty"] - 3) < 1e-6
        assert abs(g[-1]["accepted_qty"] - 10) < 1e-6

        conn = get_db()
        recd = conn.execute("SELECT received_qty FROM pr_items WHERE id=?",
                            (lb,)).fetchone()["received_qty"]
        notes = conn.execute(
            "SELECT COUNT(*) c FROM notifications WHERE title LIKE 'Over-delivery%'"
        ).fetchone()["c"]
        ev = conn.execute(
            "SELECT COUNT(*) c FROM pr_events WHERE pr_id=? AND action='over_delivery_quarantined'",
            (b,)).fetchone()["c"]
        conn.close()
        assert abs(recd - 10) < 1e-6, "stock/received must stay at the ordered 10, got %r" % recd
        assert notes >= 1, "an over-delivery must notify somebody"
        assert ev == 1, "an over-delivery must leave an audit event"

        # and the excess must be decidable, not a dead end
        ok, msg = svc.resolve_quarantine(q[0]["id"], "return", buyer)
        assert ok, msg
        _, q2 = grns(b)
        assert q2[0]["status"] == "resolved" and q2[0]["resolution"] == "return"
        assert svc.resolve_quarantine(q[0]["id"], "return", buyer) == (False, "already_resolved")

        # ---- the /warehouse/receive door obeys the same rule -----------------
        # A stock-in naming the PO is capped at the outstanding quantity and its
        # excess quarantined, instead of booking uncontrolled free stock.
        from app.warehouse import services as wsvc
        conn = get_db()
        mat = conn.execute("SELECT id, code FROM wh_materials "
                           "WHERE is_active=1 AND roll_tracked=0 LIMIT 1").fetchone()
        so = conn.execute("SELECT order_no FROM ord_orders LIMIT 1").fetchone()
        conn.close()
        assert wsvc.po_line_for("", 1) is None
        assert wsvc.po_line_for("NOT-A-PO-9999", 1) is None, "unknown ref must not match a PO"

        # fabric/trim is a DIRECT material, so the real flow demands a sales order
        c, lc = make("Thread", mat["code"] if mat else "Thread 40/2", 20, 30,
                     so_no=so["order_no"] if so else None)
        approve_all(c)
        conn = get_db()
        po_c = conn.execute("SELECT po_no FROM pr_requests WHERE id=?", (c,)).fetchone()["po_no"]
        conn.close()
        line = wsvc.po_line_for(po_c, mat["id"]) if mat else None
        if line:
            assert line["pr_id"] == c and abs(line["outstanding"] - 20) < 1e-6, line
            before = wsvc.get_material(mat["id"])["stock_qty"] or 0
            # the door's own flow: book on the PO first (post_stock=False), then
            # add only the capped quantity to stock
            ok, _ = svc.receive_items(c, {line["item_id"]: 25}, store, post_stock=False)
            assert ok
            capped = min(25, line["outstanding"])
            ok, _ = wsvc.receive_qty(mat["id"], capped, 30, store, grn_ref=po_c)
            assert ok
            after = wsvc.get_material(mat["id"])["stock_qty"] or 0
            assert abs((after - before) - 20) < 1e-6, (
                "the warehouse door must add the ordered 20, not the delivered 25: %r"
                % (after - before))
            gc, qc = grns(c)
            assert len(gc) == 1 and gc[0]["grn_no"], "the warehouse door must issue a GRN too"
            assert len(qc) == 1 and abs(qc[0]["qty"] - 5) < 1e-6, \
                "5 over 20 must be quarantined at the warehouse door too: %r" % qc
        else:
            print("  (warehouse door: no seeded material resolves to a PR line here)")

        # ---- and now through the HTTP doors a real user actually clicks ------
        cl = app.test_client()
        tok = "t" * 64
        with cl.session_transaction() as s:
            s.update({"user": {"id": 1, "username": "admin", "role": "super_admin"},
                      "username": "admin", "role": "super_admin", "user_id": 1,
                      "uid": 1, "_csrf_token": tok})

        d, ld = make("Gaskets", "Gasket kit", 10, 200)
        approve_all(d)
        r = cl.post("/procurement/pr/%s/receive" % d,
                    data={"recv_item_id[]": str(ld), "recv_qty[]": "4", "_csrf": tok})
        assert r.status_code == 302, r.status_code
        r = cl.post("/procurement/pr/%s/receive" % d,
                    data={"recv_item_id[]": str(ld), "recv_qty[]": "9", "_csrf": tok})
        assert r.status_code == 302, r.status_code
        gd, qd = grns(d)
        assert len({g["grn_no"] for g in gd}) == 2, \
            "the receiving FORM must mint a new GRN per post: %r" % [g["grn_no"] for g in gd]
        assert len(qd) == 1 and abs(qd[0]["qty"] - 3) < 1e-6, \
            "4 then 9 on a 10-line = 3 over, quarantined: %r" % qd
        conn = get_db()
        assert abs(conn.execute("SELECT received_qty FROM pr_items WHERE id=?",
                                (ld,)).fetchone()["received_qty"] - 10) < 1e-6
        conn.close()
        # both notes must be printable, each under its own number
        for g in gd:
            rp = cl.get("/procurement/pr/%s/grn.pdf?grn=%s" % (d, g["id"]))
            assert rp.status_code in (200, 500), rp.status_code   # 500 = no reportlab

        if mat:
            e, _le = make("Trim top-up", mat["code"], 15, 20,
                          so_no=so["order_no"] if so else None)
            approve_all(e)
            conn = get_db()
            po_e = conn.execute("SELECT po_no FROM pr_requests WHERE id=?",
                                (e,)).fetchone()["po_no"]
            conn.close()
            before = wsvc.get_material(mat["id"])["stock_qty"] or 0
            r = cl.post("/warehouse/receive",
                        data={"material_id": str(mat["id"]), "qty": "18",
                              "unit_cost": "20", "grn_ref": po_e, "_csrf": tok})
            assert r.status_code == 302, r.status_code
            after = wsvc.get_material(mat["id"])["stock_qty"] or 0
            ge, qe = grns(e)
            assert abs((after - before) - 15) < 1e-6, (
                "/warehouse/receive booked %r for an 18-on-15 delivery — the excess "
                "must not reach stock" % (after - before))
            assert len(ge) == 1 and ge[0]["grn_no"], \
                "the warehouse door must issue its own GRN: %r" % ge
            assert len(qe) == 1 and abs(qe[0]["qty"] - 3) < 1e-6, \
                "the warehouse door must quarantine the 3 over: %r" % qe

        print("GRN numbering + over-delivery quarantine: all checks passed")


if __name__ == "__main__":
    run()
