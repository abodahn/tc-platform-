"""Audit findings 3.4-b7b (over-delivery silently truncated) and 3.4-b9a (the GRN
number was derived at print time, so every partial receipt reprinted the same one).

Everything here goes through the REAL flow — create_pr(priced=False) then price_pr,
exactly as the UI produces a request, then the HTTP form a user actually posts —
because a control that only fires when a test hands the service a value the UI
cannot produce is not implemented.

There are deliberately NO `if line:` / `if mat:` guards in this file. The previous
round had them, and a non-matching seed skipped the whole warehouse block while the
run still printed "all checks passed". Everything this file needs, it creates.
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
        from app.warehouse import services as wsvc
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        store = {"username": "store", "role": "warehouse_manager", "id": 2}

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
                {"username": "tech", "id": 9}, priced=False)
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
            st = one("SELECT status FROM pr_requests WHERE id=?", pr_id)["status"]
            assert st == "po_issued", "expected po_issued, got %s" % st
            return one("SELECT po_no FROM pr_requests WHERE id=?", pr_id)["po_no"]

        def grns(pr_id):
            """One copy of this query pair lives in get_pr. list_grns was a second."""
            b = svc.get_pr(pr_id)
            return b["grns"], b["quarantine"]

        def stock(mid):
            return float(wsvc.get_material(mid)["stock_qty"] or 0)

        def recd(line_id):
            return float(one("SELECT received_qty FROM pr_items WHERE id=?",
                             line_id)["received_qty"] or 0)

        def status(pr_id):
            return one("SELECT status FROM pr_requests WHERE id=?", pr_id)["status"]

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
        notes = conn.execute(
            "SELECT COUNT(*) c FROM notifications WHERE title LIKE 'Over-delivery%'"
        ).fetchone()["c"]
        ev = conn.execute(
            "SELECT COUNT(*) c FROM pr_events WHERE pr_id=? AND action='over_delivery_quarantined'",
            (b,)).fetchone()["c"]
        conn.close()
        assert abs(recd(lb) - 10) < 1e-6, "received must stay at the ordered 10, got %r" % recd(lb)
        assert notes >= 1, "an over-delivery must notify somebody"
        assert ev == 1, "an over-delivery must leave an audit event"

        # and the excess must be decidable, not a dead end
        ok, msg = svc.resolve_quarantine(q[0]["id"], "return", buyer)
        assert ok, msg
        _, q2 = grns(b)
        assert q2[0]["status"] == "resolved" and q2[0]["resolution"] == "return"
        assert svc.resolve_quarantine(q[0]["id"], "return", buyer) == (False, "already_resolved")

        # ---- materials this file owns, so nothing here can silently skip ------
        ok, _ = wsvc.create_material({"code": "TC-TRIM-1", "name": "Bonded Thread 40/2",
                                      "kind": "trim", "uom": "pcs"}, store)
        assert ok
        ok, _ = wsvc.create_material({"code": "TC-FAB-1", "name": "Cotton Twill 240",
                                      "kind": "fabric", "roll_tracked": 1,
                                      "width_cm": 150}, store)
        assert ok
        trim = one("SELECT id, name FROM wh_materials WHERE code='TC-TRIM-1'")
        fab = one("SELECT id, name FROM wh_materials WHERE code='TC-FAB-1'")
        so_row = one("SELECT order_no FROM ord_orders LIMIT 1")
        so = so_row["order_no"] if so_row else None

        assert wsvc.po_line_for("", trim["id"]) is None
        assert wsvc.po_line_for("NOT-A-PO-9999", trim["id"]) is None, \
            "unknown ref must not match a PO"

        # ---- and now through the HTTP doors a real user actually clicks ------
        cl = app.test_client()
        tok = "t" * 64

        def login(username):
            """current_user() reads users.id from session['uid'] and takes the ROLE
            from the database row, so a made-up session dict logs in as whoever
            uid points at. Use the seeded accounts.

            g.user has to be dropped by hand: the test client reuses the app context
            this test already pushed, so current_user()'s per-request cache would
            otherwise keep answering 'admin' for the rest of the run — and every
            authorisation assertion below would pass vacuously."""
            u = one("SELECT * FROM users WHERE username=?", username)
            assert u, "no seeded %r account to test authorisation with" % username
            from flask import g
            g.pop("user", None)
            with cl.session_transaction() as s:
                s.clear()
                s.update({"uid": u["id"], "ep": u.get("session_epoch") or 0,
                          "username": u["username"], "role": u["role"],
                          "_csrf_token": tok})
            return u["role"]

        assert login("admin") == "super_admin"

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
        assert abs(recd(ld) - 10) < 1e-6
        # both notes must be printable, each under its own number
        for g in gd:
            rp = cl.get("/procurement/pr/%s/grn.pdf?grn=%s" % (d, g["id"]))
            assert rp.status_code in (200, 500), rp.status_code   # 500 = no reportlab

        # ==== BLOCKER 1: the PR line is the material NAME, what a buyer types ==
        # Nothing on the PR form searches wh_materials — both type-aheads read other
        # tables and free text is invited — so joining on the material CODE alone
        # meant the warehouse door's cap never engaged on a line a real user writes.
        e, le = make("Trim top-up", trim["name"], 15, 20, so_no=so)
        po_e = approve_all(e)
        line = wsvc.po_line_for(po_e, trim["id"])
        assert line is not None, (
            "a PR line written as the material NAME (%r) must resolve — this is the "
            "only shape the PR form can produce" % trim["name"])
        assert line["pr_id"] == e and abs(line["outstanding"] - 15) < 1e-6, line

        before = stock(trim["id"])
        r = cl.post("/warehouse/receive",
                    data={"material_id": str(trim["id"]), "qty": "18", "unit_cost": "20",
                          "grn_ref": po_e, "_csrf": tok})
        assert r.status_code == 302, r.status_code
        ge, qe = grns(e)
        assert abs((stock(trim["id"]) - before) - 15) < 1e-6, (
            "/warehouse/receive booked %r for an 18-on-15 delivery — the excess must "
            "not reach stock" % (stock(trim["id"]) - before))
        assert len(ge) == 1 and ge[0]["grn_no"], "the warehouse door must issue a GRN: %r" % ge
        assert len(qe) == 1 and abs(qe[0]["qty"] - 3) < 1e-6, \
            "the warehouse door must quarantine the 3 over: %r" % qe

        # ---- MINOR: 'accept' must EXECUTE, not just record a word ------------
        avg_before = float(one("SELECT avg_cost FROM wh_materials WHERE id=?",
                               trim["id"])["avg_cost"] or 0)
        before = stock(trim["id"])
        ok, msg = svc.resolve_quarantine(qe[0]["id"], "accept", buyer)
        assert ok, msg
        assert abs((stock(trim["id"]) - before) - 3) < 1e-6, (
            "'accept as free issue' left the goods in the building and in no stock "
            "record: stock moved %r, expected 3" % (stock(trim["id"]) - before))
        avg_after = float(one("SELECT avg_cost FROM wh_materials WHERE id=?",
                              trim["id"])["avg_cost"] or 0)
        assert abs(avg_after - avg_before) < 1e-6, (
            "a free issue is booked at zero cost — the moving average must not move: "
            "%r -> %r" % (avg_before, avg_after))

        # ==== BLOCKER 2a: over-delivery AFTER the PO is fully received =========
        # (procurement door) — the commonest shape there is, and it used to be
        # refused with a raw 'not_receivable' and no form on the page.
        f, lf = make("Sealing rings", "Seal kit S12", 10, 150)
        approve_all(f)
        r = cl.post("/procurement/pr/%s/receive" % f,
                    data={"recv_item_id[]": str(lf), "recv_qty[]": "10", "_csrf": tok})
        assert r.status_code == 302
        assert status(f) == "received", status(f)
        r = cl.post("/procurement/pr/%s/receive" % f,
                    data={"recv_item_id[]": str(lf), "recv_qty[]": "3", "_csrf": tok},
                    follow_redirects=True)
        assert r.status_code == 200
        assert b"not_receivable" not in r.data, \
            "the operator was shown the raw refusal code instead of the receipt"
        gf, qf = grns(f)
        assert len(qf) == 1 and abs(qf[0]["qty"] - 3) < 1e-6, (
            "3 more delivered on a closed-out 10 must be quarantined: %r" % qf)
        assert len(gf) == 2 and abs(gf[-1]["accepted_qty"]) < 1e-6, (
            "the second delivery gets its own GRN and accepts nothing: %r"
            % [(x["grn_no"], x["accepted_qty"], x["quarantined_qty"]) for x in gf])
        assert abs(recd(lf) - 10) < 1e-6, "received must stay at 10, got %r" % recd(lf)
        # and the form is on the page, so the operator has a door at all
        page = cl.get("/procurement/pr/%s" % f).data
        assert b'name="recv_qty[]"' in page, \
            "a fully-received PO renders no receiving form — the over-delivery has nowhere to go"

        # ==== BLOCKER 2b: the same shape at the warehouse door =================
        h, lh = make("Trim refill", trim["name"], 10, 20, so_no=so)
        po_h = approve_all(h)
        ok, _ = svc.receive_items(h, {lh: 10}, store)
        assert ok and status(h) == "received"
        before = stock(trim["id"])
        r = cl.post("/warehouse/receive",
                    data={"material_id": str(trim["id"]), "qty": "5", "unit_cost": "20",
                          "grn_ref": po_h, "_csrf": tok})
        assert r.status_code == 302
        assert abs(stock(trim["id"]) - before) < 1e-6, (
            "5 extra units on a closed-out PO went straight into stock as "
            "uncontrolled free stock: delta %r" % (stock(trim["id"]) - before))
        gh, qh = grns(h)
        assert len(qh) == 1 and abs(qh[0]["qty"] - 5) < 1e-6, \
            "the warehouse door must quarantine all 5: %r" % qh
        assert abs(recd(lh) - 10) < 1e-6

        # ==== MAJOR 1: a failed stock move must not book the PO ================
        # Booking first made 4,000 EGP payable through the three-way match for goods
        # that never entered the warehouse.
        i, li = make("Fabric", fab["name"], 100, 40, so_no=so)
        po_i = approve_all(i)
        ok, rn = wsvc.receive_roll(fab["id"], {"length_m": 1, "roll_no": "R-DUP-1"}, store)
        assert ok, rn                       # burn the number the operator will retype
        before = stock(fab["id"])
        r = cl.post("/warehouse/receive",
                    data={"material_id": str(fab["id"]), "length_m": "100",
                          "roll_no": "R-DUP-1", "unit_cost": "40", "grn_ref": po_i,
                          "_csrf": tok})
        assert r.status_code == 302
        assert abs(stock(fab["id"]) - before) < 1e-6, "the duplicate roll must not stock"
        assert status(i) == "po_issued", (
            "the stock move failed, so the PO must NOT be booked — status %r" % status(i))
        assert abs(recd(li)) < 1e-6, "received_qty moved on a delivery that never landed"
        assert grns(i) == ([], []), "a GRN was minted for goods that never arrived: %r" % (grns(i),)
        m = svc.three_way_match(i)
        assert not float(m.get("received_value") or 0), (
            "the phantom delivery is payable through the three-way match: %r" % m)
        # the same post with a fresh roll number books cleanly, both sides
        r = cl.post("/warehouse/receive",
                    data={"material_id": str(fab["id"]), "length_m": "100",
                          "roll_no": "R-FRESH-1", "unit_cost": "40", "grn_ref": po_i,
                          "_csrf": tok})
        assert r.status_code == 302
        assert abs((stock(fab["id"]) - before) - 100) < 1e-6, stock(fab["id"]) - before
        assert status(i) == "received" and abs(recd(li) - 100) < 1e-6, (status(i), recd(li))
        assert len(grns(i)[0]) == 1

        # ==== MAJOR 2: a storekeeper may not close a PO through door two =======
        j, lj = make("Trim for the store", trim["name"], 10, 20, so_no=so)
        po_j = approve_all(j)
        assert login("store") == "storekeeper"
        r = cl.post("/procurement/pr/%s/receive" % j,
                    data={"recv_item_id[]": str(lj), "recv_qty[]": "10", "_csrf": tok})
        assert r.status_code == 403, "the procurement door already refuses a storekeeper"
        before = stock(trim["id"])
        r = cl.post("/warehouse/receive",
                    data={"material_id": str(trim["id"]), "qty": "10", "unit_cost": "20",
                          "grn_ref": po_j, "_csrf": tok})
        assert r.status_code == 302
        assert abs(recd(lj)) < 1e-6 and status(j) == "po_issued", (
            "a storekeeper closed a PO for payment through /warehouse/receive: %s / %r"
            % (status(j), recd(lj)))
        assert grns(j) == ([], []), "a storekeeper minted a GRN: %r" % (grns(j),)
        assert abs(stock(trim["id"]) - before) < 1e-6, \
            "refused on the PO, so nothing may be booked as free stock either"
        # …but plain unreferenced stock-in is still the storekeeper's job
        r = cl.post("/warehouse/receive",
                    data={"material_id": str(trim["id"]), "qty": "7", "unit_cost": "20",
                          "_csrf": tok})
        assert r.status_code == 302
        assert abs((stock(trim["id"]) - before) - 7) < 1e-6, \
            "the storekeeper's ordinary stock-in must still work"

        print("GRN numbering + over-delivery quarantine: all checks passed")
        return True


if __name__ == "__main__":
    run()
