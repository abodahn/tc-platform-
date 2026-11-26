"""Imports (shipment + landed cost) and framework agreements.

    python -m app.approvals.tests_imports
"""
import io
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "imports.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    from flask import g
    # One app context for the whole run: drop the cached user between requests.
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import imports as imp
        from app.approvals import services as svc
        from app.approvals import schema
        from app.db import get_db

        c = get_db()
        schema.create_and_seed(c)                       # idempotent second run
        ids, users = {}, {}
        for un, role in (("t.buy", "purchasing_manager"), ("t.buy2", "purchasing_manager"),
                         ("t.fin", "finance_manager"), ("t.itdir", "it_director"),
                         ("t.staff", "normal_user"), ("t.other", "normal_user"),
                         ("t.recv", "normal_user")):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, un, role))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
            users[un] = dict(c.execute("SELECT * FROM users WHERE id=?", (ids[un],)).fetchone())
        # a no-money user who may see every request (goods receipt only)
        c.execute("UPDATE users SET extra_perms='[\"proc_receive\"]' WHERE username='t.recv'")
        c.execute("INSERT INTO proc_vendors (name, is_active, created_at) VALUES ('Fake Vendor A', 1, '2026-01-01')")
        c.execute("INSERT INTO proc_vendors (name, is_active, created_at) VALUES ('Fake Vendor Off', 0, '2026-01-01')")
        c.execute("INSERT INTO proc_items (code, name, unit, active) VALUES ('FAKE-001', 'Fake Catalogue Item', 'Kg', 1)")
        cat_id = c.execute("SELECT id FROM proc_items WHERE code='FAKE-001'").fetchone()["id"]
        c.commit()
        c.close()
        buy, fin, itdir, staff, other = (users[k] for k in ("t.buy", "t.fin", "t.itdir", "t.staff", "t.other"))

        def client(un):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = ids[un], 0, "tok"
            return cli

        def sql(q, a=()):
            conn = get_db()
            try:
                conn.execute(q, a)
                conn.commit()
            finally:
                conn.close()

        def one(q, a=()):
            conn = get_db()
            try:
                return conn.execute(q, a).fetchone()
            finally:
                conn.close()

        # ================= (A) import orders =================
        pid, _ = svc.create_pr({"title": "Import", "department": "IT", "order_type": "import"},
                               [{"item": "Fake Yarn", "qty": 10, "unit_price": 100},
                                {"item": "Fake Button", "qty": 5, "unit_price": 200}],
                               staff, submit=False, priced=True)
        lid, _ = svc.create_pr({"title": "Local", "department": "IT"},
                               [{"item": "Fake Tape", "qty": 1, "unit_price": 10}], staff, submit=False, priced=True)
        sql("UPDATE pr_requests SET status='po_issued', po_no='PO-T-1' WHERE id=?", (pid,))
        sql("UPDATE pr_requests SET status='po_issued', po_no='PO-T-2' WHERE id=?", (lid,))

        ship = {"bl_no": "BL-FAKE-9", "carrier": "Fake Line", "incoterm": "FOB",
                "shipped_at": "2026-11-01", "port_at": "2026-11-10"}
        assert imp.save_shipment(pid, "PO-T-1", ship, staff) == (False, "forbidden")
        assert imp.save_shipment(lid, "PO-T-2", ship, buy) == (False, "not_import")
        assert imp.save_shipment(pid, "PO-NOPE", ship, buy) == (False, "no_po")
        assert imp.save_shipment(pid, "PO-T-1", dict(ship, incoterm="XYZ"), buy) == (False, "bad_incoterm")
        assert imp.save_shipment(pid, "PO-T-1", dict(ship, port_at="2026-10-01"), buy) == (False, "milestone_order")
        assert imp.save_shipment(pid, "PO-T-1", dict(ship, shipped_at="31-31-2026"), buy) == (False, "bad_date")
        assert imp.save_shipment(pid, "PO-T-1", ship, buy) == (True, "saved")
        assert imp.save_shipment(pid, "PO-T-1", dict(ship, cleared_at="2026-11-12"), buy) == (True, "saved")
        assert one("SELECT COUNT(*) n FROM pr_shipments WHERE pr_id=?", (pid,))["n"] == 1   # upsert
        r = client("t.other").post("/procurement/pr/%d/shipment" % pid, data={"_csrf": "tok", "po_no": "PO-T-1"})
        assert r.status_code == 403, r.status_code

        assert imp.add_charge(pid, "PO-T-1", {"kind": "freight", "amount": "5"}, staff) == (False, "forbidden")
        assert imp.add_charge(pid, "PO-T-1", {"kind": "bribe", "amount": "5"}, buy) == (False, "bad_kind")
        for bad in ("nan", "-1", "abc", "0", "inf"):
            assert imp.add_charge(pid, "PO-T-1", {"kind": "freight", "amount": bad}, buy) == (False, "bad_amount"), bad
        assert imp.add_charge(pid, "PO-T-1", {"kind": "freight", "amount": "10", "currency": "USD"},
                              buy) == (False, "fx_required")
        assert imp.add_charge(pid, "PO-T-1", {"kind": "freight", "amount": "10", "currency": "XXX"},
                              buy) == (False, "bad_currency")
        assert imp.add_charge(lid, "PO-T-2", {"kind": "freight", "amount": "10"}, buy) == (False, "not_import")
        assert imp.add_charge(pid, "PO-T-1", {"kind": "duty", "amount": "300"}, buy) == (True, "added")
        assert imp.add_charge(pid, "PO-T-1", {"kind": "freight", "amount": "10", "currency": "USD",
                                              "fx_rate": "50"}, buy) == (True, "added")
        conn = get_db()
        try:
            pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pid,)).fetchone()
            v = imp.landed_view(conn, pr)
        finally:
            conn.close()
        assert len(v) == 1 and v[0]["charges_egp"] == 800.0 and v[0]["po_value_egp"] == 2000.0, v
        l1, l2 = v[0]["lines"]
        assert (l1["alloc_egp"], l1["landed_unit_egp"]) == (400.0, 140.0), l1
        assert (l2["alloc_egp"], l2["landed_unit_egp"]) == (400.0, 280.0), l2
        assert v[0]["landed_egp"] == 2800.0 and v[0]["uplift_pct"] == 40.0
        assert v[0]["next_milestone"] == "delivered_at"
        # by quantity, and the residue goes to the last line
        rows = imp.allocate([{"id": 1, "qty": 10, "unit_price": 100}, {"id": 2, "qty": 5, "unit_price": 200}], 800, "qty")
        assert [r["alloc_egp"] for r in rows] == [533.33, 266.67], rows
        rows = imp.allocate([{"id": i, "qty": 1, "unit_price": 100} for i in range(3)], 100)
        assert [r["alloc_egp"] for r in rows] == [33.33, 33.33, 33.34] and sum(r["alloc_egp"] for r in rows) == 100
        rows = imp.allocate([{"id": 1, "qty": 2, "unit_price": 0}, {"id": 2, "qty": 2, "unit_price": 0}], 10)
        assert [r["alloc_egp"] for r in rows] == [5.0, 5.0]                       # unpriced -> by qty
        rows = imp.allocate([{"id": 1, "qty": 4, "unit_price": 10}], 20, fx=2.0)   # fx applies to the price
        assert rows[0]["landed_unit_egp"] == 25.0, rows

        # stock valuation: OFF by default, landed unit only when switched ON
        item1 = one("SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id", (pid,))["id"]
        assert imp.stock_unit_cost(pid, item1, 100.0) == 100.0
        assert svc.set_setting("landed_cost_to_stock", "1", buy)[0]
        assert imp.stock_unit_cost(pid, item1, 100.0) == 140.0
        local_item = one("SELECT id FROM pr_items WHERE pr_id=?", (lid,))["id"]
        assert imp.stock_unit_cost(lid, local_item, 10.0) == 10.0                  # not an import
        assert svc.set_setting("landed_cost_to_stock", "0", buy)[0]

        # corrections: Finance may, others may not; voids need a reason and are kept
        ch = one("SELECT id FROM pr_landed_charges WHERE pr_id=? AND kind='duty'", (pid,))["id"]
        assert imp.correct_charge(ch, {"amount": "350"}, other)[:2] == (False, "forbidden")
        assert imp.correct_charge(ch, {"amount": "nan"}, fin)[:2] == (False, "bad_amount")
        assert imp.correct_charge(ch, {"amount": "350"}, fin) == (True, "corrected", pid)
        assert imp.void_charge(ch, "", fin)[:2] == (False, "reason_required")
        assert imp.void_charge(ch, "duplicate", fin) == (True, "voided", pid)
        assert imp.void_charge(ch, "again", fin)[:2] == (False, "void")
        assert one("SELECT status FROM pr_landed_charges WHERE id=?", (ch,))["status"] == "void"
        r = client("t.other").post("/procurement/charge/%d/void" % ch, data={"_csrf": "tok", "reason": "x"})
        assert r.status_code == 403
        # route: charge with a document, then the document is money-gated
        r = client("t.buy").post("/procurement/pr/%d/charge" % pid, data={
            "_csrf": "tok", "po_no": "PO-T-1", "kind": "clearance", "amount": "100", "currency": "EGP",
            "document": (io.BytesIO(b"%PDF-1.4 fake"), "clearance.pdf")}, content_type="multipart/form-data")
        assert r.status_code == 302, r.status_code
        doc = one("SELECT id FROM pr_landed_charges WHERE kind='clearance'")["id"]
        assert client("t.buy").get("/procurement/charge/%d/file" % doc).status_code == 200
        assert client("t.staff").get("/procurement/charge/%d/file" % doc).status_code == 403
        r = client("t.buy").post("/procurement/pr/%d/charge" % pid, data={
            "_csrf": "tok", "po_no": "PO-T-1", "kind": "other", "amount": "1",
            "document": (io.BytesIO(b"MZ"), "evil.exe")}, content_type="multipart/form-data")
        assert r.status_code == 302 and one("SELECT COUNT(*) n FROM pr_landed_charges WHERE kind='other'")["n"] == 0

        html = client("t.buy").get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert "proc.imports.title" in html and "BL-FAKE-9" in html and "proc.imports.charges" in html
        html = client("t.staff").get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert "proc.imports.title" in html and "proc.imports.charges" not in html   # no money for the requester
        html = client("t.buy").get("/procurement/pr/%d" % lid).get_data(as_text=True)
        assert "proc.imports.title" not in html                                    # local order

        # ================= (B) framework agreements =================
        good = {"vendor": "Fake Vendor A", "valid_from": "2026-01-01", "valid_to": "2099-12-31",
                "currency": "EGP", "ceiling_value": "1500"}
        lines = [{"item": "Fake Thread Cone", "unit": "Pcs", "unit_price": "10"},
                 {"code": "fake-001", "unit_price": "4.5"}, {}]
        assert imp.create_agreement(good, lines, staff) == (False, "forbidden")
        assert imp.create_agreement(dict(good, vendor="Fake Vendor Off"), lines, buy) == (False, "vendor_not_approved")
        assert imp.create_agreement(dict(good, valid_to="2025-01-01"), lines, buy) == (False, "bad_dates")
        assert imp.create_agreement(dict(good, ceiling_value=""), lines, buy) == (False, "ceiling_required")
        assert imp.create_agreement(dict(good, ceiling_value="-5"), lines, buy) == (False, "bad_ceiling")
        assert imp.create_agreement(good, [{}], buy) == (False, "lines_required")
        assert imp.create_agreement(good, [{"item": "X", "unit_price": "0"}], buy) == (False, "bad_line")
        assert imp.create_agreement(good, [{"code": "NOPE", "unit_price": "1"}], buy) == (False, "unknown_code")
        r = client("t.staff").post("/procurement/agreements", data={"_csrf": "tok"})
        assert r.status_code == 403
        ok, aid = imp.create_agreement(good, lines, buy)
        assert ok, aid
        a = imp.get_agreement(aid)
        assert a["state"] == "draft" and a["ref"].startswith("FA-") and len(a["lines"]) == 2
        assert a["lines"][1]["item_id"] == cat_id and a["lines"][1]["unit"] == "Kg"
        # the bell and /my-work tell the approvers
        assert one("SELECT COUNT(*) n FROM notifications WHERE target_user='t.fin' AND link=?",
                   ("/procurement/agreements/%d" % aid,))["n"] == 1
        assert "proc.imports.mw_title" in client("t.fin").get("/my-work").get_data(as_text=True)

        # drafts draw nothing
        rid, _ = svc.create_pr({"title": "Threads", "department": "IT"},
                               [{"item": "Fake Thread Cone", "qty": 100}], staff, submit=False, priced=False)
        line = one("SELECT id FROM pr_items WHERE pr_id=?", (rid,))["id"]
        al1 = a["lines"][0]["id"]
        assert imp.draw_line(rid, line, al1, buy) == (False, "agreement_inactive")

        # approval: not purchasing alone, not the creator
        assert imp.approve_agreement(aid, users["t.buy2"]) == (False, "forbidden")
        r = client("t.buy2").post("/procurement/agreements/%d/approve" % aid, data={"_csrf": "tok"})
        assert r.status_code == 403
        ok2, aid2 = imp.create_agreement(dict(good, ceiling_value="", ceiling_qty="5"), lines[:1], itdir)
        assert ok2 and imp.approve_agreement(aid2, itdir) == (False, "self_approval")
        assert imp.approve_agreement(aid, fin) == (True, "approved")
        assert imp.approve_agreement(aid, fin) == (False, "not_draft")
        assert imp.get_agreement(aid)["state"] == "active"

        # drawing a line
        html = client("t.buy").get("/procurement/pr/%d" % rid).get_data(as_text=True)
        assert "proc.imports.draw" in html and a["ref"] in html, (
            "imp-agr" in html, "proc.items" in html, len(html), html[:300])
        assert "proc.imports.draw\"" not in client("t.staff").get("/procurement/pr/%d" % rid).get_data(as_text=True)
        assert imp.draw_line(rid, line, al1, staff) == (False, "forbidden")
        assert imp.draw_line(rid, line, a["lines"][1]["id"], buy) == (False, "agreement_item")
        assert imp.draw_line(rid, line, al1, buy) == (True, "drawn")
        it = one("SELECT unit_price, vendor, agreement_line_id FROM pr_items WHERE id=?", (line,))
        assert (it["unit_price"], it["vendor"], it["agreement_line_id"]) == (10.0, "Fake Vendor A", al1)
        assert imp.get_agreement(aid)["used_value"] == 1000.0
        # the requester cannot draw on their own request (pricing-gate rule)
        own, _ = svc.create_pr({"title": "Own", "department": "IT"},
                               [{"item": "Fake Thread Cone", "qty": 1}], buy, submit=False, priced=False)
        own_line = one("SELECT id FROM pr_items WHERE pr_id=?", (own,))["id"]
        assert imp.draw_line(own, own_line, al1, buy) == (False, "self_pricing")
        # ceiling: 1000 used + 60 x 10 = 1600 > 1500
        r2, _ = svc.create_pr({"title": "More", "department": "IT"},
                              [{"item": "fake  thread CONE", "qty": 60}], staff, submit=False, priced=True)
        l2 = one("SELECT id FROM pr_items WHERE pr_id=?", (r2,))["id"]
        assert imp.draw_line(r2, l2, al1, buy) == (False, "agreement_ceiling")
        sql("UPDATE pr_items SET qty=50 WHERE id=?", (l2,))
        # priced request -> goes through the pricing gate
        assert imp.draw_line(r2, l2, al1, buy) == (True, "drawn")
        it = one("SELECT unit_price, est_cost, agreement_line_id FROM pr_items WHERE id=?", (l2,))
        assert (it["unit_price"], it["est_cost"], it["agreement_line_id"]) == (10.0, 500.0, al1)
        assert one("SELECT pricing_status FROM pr_requests WHERE id=?", (r2,))["pricing_status"] == "priced"
        assert imp.get_agreement(aid)["used_value"] == 1500.0 and imp.get_agreement(aid)["used_pct"] == 100.0

        # the competitive-quotation rule is waived for those lines only
        conn = get_db()
        try:
            req = svc.rfq_requirement(conn, conn.execute("SELECT * FROM pr_requests WHERE id=?", (r2,)).fetchone())
        finally:
            conn.close()
        assert req["ok"] and req.get("agreement") and req["required"] == 0, req
        mixed, _ = svc.create_pr({"title": "Mixed", "department": "IT"},
                                 [{"item": "Fake Catalogue Item", "item_id": cat_id, "qty": 2},
                                  {"item": "Fake Lamp", "qty": 1, "unit_price": 30000}],
                                 staff, submit=False, priced=True)
        ml = one("SELECT id FROM pr_items WHERE pr_id=? AND item='Fake Catalogue Item'", (mixed,))["id"]
        assert imp.draw_line(mixed, ml, a["lines"][1]["id"], buy) == (False, "agreement_ceiling")  # 1500 used
        sql("UPDATE proc_agreements SET ceiling_value=2000 WHERE id=?", (aid,))
        res = imp.draw_line(mixed, ml, a["lines"][1]["id"], buy)
        assert res == (True, "drawn"), res
        conn = get_db()
        try:
            req = svc.rfq_requirement(conn, conn.execute("SELECT * FROM pr_requests WHERE id=?", (mixed,)).fetchone())
        finally:
            conn.close()
        assert not req["ok"] and req["required"] >= 1 and req["value"] == 30000.0, req   # only the lamp counts
        html = client("t.buy").get("/procurement/pr/%d" % mixed).get_data(as_text=True)
        assert "proc.imports.waived" in html and "proc.imports.release" in html

        # the ladder is untouched: drawn or not, the same rungs
        assert svc.submit_pr(r2, staff)[0]
        assert one("SELECT COUNT(*) n FROM pr_steps WHERE pr_id=?", (r2,))["n"] >= 1

        # expiry / closing -> normal process again
        sql("UPDATE proc_agreements SET valid_to='2026-01-02' WHERE id=?", (aid,))
        conn = get_db()
        try:
            pr2 = conn.execute("SELECT * FROM pr_requests WHERE id=?", (r2,)).fetchone()
            assert imp.rfq_waiver(conn, pr2)["lines"] == []
            req = svc.rfq_requirement(conn, pr2)
        finally:
            conn.close()
        assert not req.get("agreement"), req
        assert imp.draw_line(rid, line, al1, buy)[1] in ("agreement_expired", "locked")
        assert imp.get_agreement(aid)["state"] == "expired"
        sql("UPDATE proc_agreements SET valid_to='2099-12-31' WHERE id=?", (aid,))
        # a re-priced line has left the agreement
        sql("UPDATE pr_items SET unit_price=11 WHERE id=?", (line,))
        assert imp.get_agreement(aid)["used_value"] == 500.0 + 9.0
        assert imp.release_line(mixed, ml, buy) == (True, "released")
        assert imp.get_agreement(aid)["used_value"] == 500.0
        assert imp.close_agreement(aid, staff) == (False, "forbidden")
        assert imp.close_agreement(aid, buy) == (True, "closed")
        assert imp.draw_line(mixed, ml, a["lines"][1]["id"], buy) == (False, "agreement_inactive")

        # register page, filters, export, detail, search, Arabic
        page = client("t.buy").get("/procurement/agreements").get_data(as_text=True)
        assert a["ref"] in page and "2,000" in page
        # plain requesters see only their own requests (2026-11-26): no agreements register
        assert client("t.staff").get("/procurement/agreements").status_code == 403
        page = client("t.recv").get("/procurement/agreements").get_data(as_text=True)
        assert a["ref"] in page and "2,000" not in page and "agr-new" not in page   # no money, no form
        page = client("t.buy").get("/procurement/agreements?status=draft").get_data(as_text=True)
        assert a["ref"] not in page.split("<tbody>", 1)[-1], page[page.find("<tbody>"):][:600]
        x = client("t.buy").get("/procurement/agreements.xlsx")
        assert x.status_code == 200 and x.data[:2] == b"PK"
        page = client("t.buy").get("/procurement/agreements/%d" % aid).get_data(as_text=True)
        assert "proc.imports.draws" in page and ">More<" in page and "Threads" not in page
        assert client("t.buy").get("/procurement/agreements/99999").status_code == 404
        hits = client("t.buy").get("/search?q=" + a["ref"]).get_json()["results"]
        assert any(h["type"] == "agreement" for h in hits), hits
        hits = client("t.buy").get("/search?q=BL-FAKE-9").get_json()["results"]
        assert any(h["type"] == "shipment" for h in hits), hits
        sql("UPDATE users SET lang_pref='ar' WHERE id=?", (ids["t.buy"],))
        r = client("t.buy").post("/procurement/agreements/%d/close" % aid, data={"_csrf": "tok"},
                                 follow_redirects=True)
        assert "الاتفاقية دي مقفولة خلاص" in r.get_data(as_text=True)
        assert client("t.buy").get("/procurement/agreements").status_code == 200

        # ================= review fixes =================
        def pr_row(conn, p):
            return conn.execute("SELECT * FROM pr_requests WHERE id=?", (p,)).fetchone()

        def waiver(p):
            conn = get_db()
            try:
                return imp.rfq_waiver(conn, pr_row(conn, p))["lines"]
            finally:
                conn.close()

        def rfq(p):
            conn = get_db()
            try:
                return svc.rfq_requirement(conn, pr_row(conn, p))
            finally:
                conn.close()

        def offer(p):
            conn = get_db()
            try:
                return imp.drawable(conn, pr_row(conn, p), [dict(i) for i in conn.execute(
                    "SELECT * FROM pr_items WHERE pr_id=? AND agreement_line_id IS NULL", (p,)).fetchall()])
            finally:
                conn.close()

        def agreement(lines_, ceiling="1000", **kw):
            ok_, x = imp.create_agreement(dict(good, ceiling_value=ceiling, **kw), lines_, buy)
            assert ok_, x
            assert imp.approve_agreement(x, fin) == (True, "approved")
            return x, imp.get_agreement(x)["lines"]

        def mk(title, lines_, priced=True):
            p, _ = svc.create_pr({"title": title, "department": "IT"}, lines_, staff,
                                 submit=False, priced=priced)
            return p, [r["id"] for r in conn_rows("SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id", (p,))]

        def conn_rows(q, a=()):
            conn = get_db()
            try:
                return conn.execute(q, a).fetchall()
            finally:
                conn.close()

        b1, b1l = agreement([{"item": "Fake Rivet", "unit_price": "10"}])
        pa, (la,) = mk("Rivets A", [{"item": "Fake Rivet", "qty": 60, "unit_price": 10}])
        pb, (lb,) = mk("Rivets B", [{"item": "Fake Rivet", "qty": 40, "unit_price": 10}])
        pc, (lc,) = mk("Rivets C", [{"item": "Fake Rivet", "qty": 5, "unit_price": 10}])
        assert lc in offer(pc)
        assert imp.draw_line(pa, la, b1l[0]["id"], buy) == (True, "drawn")
        assert imp.draw_line(pb, lb, b1l[0]["id"], buy) == (True, "drawn")
        assert one("SELECT agreement_drawn_at FROM pr_items WHERE id=?", (la,))["agreement_drawn_at"]
        assert waiver(pa) == [la] and waiver(pb) == [lb]
        assert offer(pc) == {}                         # ceiling used up: not offered
        # A grows after its draw (e.g. a warehouse revise): only the overflow, B, loses it
        sql("UPDATE pr_items SET qty=70 WHERE id=?", (la,))
        assert waiver(pa) == [la] and waiver(pb) == [], (waiver(pa), waiver(pb))
        sql("UPDATE pr_items SET qty=60 WHERE id=?", (la,))
        # drawn inside validity, agreement expired since: the waiver stands
        sql("UPDATE pr_items SET agreement_drawn_at='2026-02-01 09:00:00' WHERE id IN (?,?)", (la, lb))
        sql("UPDATE proc_agreements SET valid_to='2026-03-01' WHERE id=?", (b1,))
        assert waiver(pa) == [la] and waiver(pb) == [lb]
        sql("UPDATE pr_items SET agreement_drawn_at='2026-04-01 09:00:00' WHERE id=?", (lb,))
        assert waiver(pb) == []                        # drawn after the end date
        # a request (re)submitted after the end date is judged on that date
        sql("UPDATE pr_requests SET submitted_at='2026-05-01 09:00:00' WHERE id=?", (pa,))
        assert waiver(pa) == []
        sql("UPDATE pr_requests SET submitted_at=NULL WHERE id=?", (pa,))
        sql("UPDATE proc_agreements SET valid_to='2099-12-31' WHERE id=?", (b1,))
        # an abandoned draft stops holding the ceiling
        sql("UPDATE pr_requests SET created_at='2025-01-01 00:00:00' WHERE id=?", (pa,))
        assert imp.get_agreement(b1)["used_value"] == 400.0
        assert lc in offer(pc)
        sql("UPDATE pr_requests SET created_at=? WHERE id=?", (svc._now(), pa))
        # supplier off the approved list: no waiver, nothing offered
        assert imp.release_line(pb, lb, buy) == (True, "released")
        assert one("SELECT agreement_drawn_at FROM pr_items WHERE id=?", (lb,))["agreement_drawn_at"] is None
        assert lc in offer(pc)
        sql("UPDATE proc_vendors SET is_active=0 WHERE name='Fake Vendor A'")
        assert waiver(pa) == [] and offer(pc) == {}
        sql("UPDATE proc_vendors SET is_active=1 WHERE name='Fake Vendor A'")
        assert waiver(pa) == [la]

        # header discount: the waived part is taken off at its NET value
        b2, b2l = agreement([{"item": "Fake Zipper", "unit_price": "600"}], ceiling="1000000")
        pd, (zd, lamp) = mk("Zips", [{"item": "Fake Zipper", "qty": 100, "unit_price": 600},
                                     {"item": "Fake Lamp", "qty": 1, "unit_price": 40000}])
        assert svc.price_pr(pd, {}, {"disc_type": "pct", "disc_value": "20"}, buy)[0]
        assert imp.draw_line(pd, zd, b2l[0]["id"], buy) == (True, "drawn")
        assert one("SELECT total FROM pr_requests WHERE id=?", (pd,))["total"] == 80000.0
        r_ = rfq(pd)
        assert r_["value"] == 32000.0 and not r_.get("agreement"), r_       # 40,000 x 0.8, not 80k - 60k
        # unpriced request: the draw reprices the line and the request total, pricing stays open
        pu, (zu,) = mk("Zips unpriced", [{"item": "Fake Zipper", "qty": 2}], priced=False)
        assert "proc.imports.agr_exists" in client("t.staff").get("/procurement/pr/%d" % pu).get_data(as_text=True)
        assert imp.draw_line(pu, zu, b2l[0]["id"], buy) == (True, "drawn")
        row = one("SELECT total, pricing_status FROM pr_requests WHERE id=?", (pu,))
        assert (row["total"], row["pricing_status"]) == (1200.0, "unpriced"), dict(row)
        # an all-agreement request: value reported, and DOAM 4.4 still applies
        b3, b3l = agreement([{"code": "FAKE-001", "unit_price": "4.5"}])
        pe, (ce,) = mk("Cat", [{"item": "Fake Catalogue Item", "item_id": cat_id, "qty": 10, "unit_price": 4.5}])
        assert imp.draw_line(pe, ce, b3l[0]["id"], buy) == (True, "drawn")
        r_ = rfq(pe)
        assert r_["ok"] and r_.get("agreement") and r_["value"] == 0.0 and r_["required"] == 0, r_
        sql("UPDATE proc_items SET cost_price=4, has_cost=1 WHERE id=?", (cat_id,))
        r_ = rfq(pe)
        assert not r_["ok"] and r_["required"] == 3, r_                     # 12.5% over target
        sql("UPDATE proc_items SET cost_price=0, has_cost=0 WHERE id=?", (cat_id,))

        # register filter: same key as the order grouping (case, non-ASCII)
        sql("INSERT INTO proc_vendors (name, is_active, created_at) VALUES ('Fake Çukurova Tekstil', 1, '2026-01-01')")
        b4, _ = agreement([{"item": "Fake Hook", "unit_price": "1"}], vendor="Fake Çukurova Tekstil")
        assert [x["id"] for x in imp.list_agreements(vendor=" FAKE ÇUKUROVA TEKSTIL ")] == [b4]
        page = client("t.buy").get("/procurement/agreements/%d" % b1).get_data(as_text=True)
        assert "proc.imports.rs_draft" in page and "→" not in page

        # landed cost: computed once per receipt, and an error rolls the connection back
        assert svc.set_setting("landed_cost_to_stock", "1", buy)[0]
        cache = {}
        unit = imp.stock_unit_cost(pid, item1, 100.0, cache=cache)
        assert unit > 100.0 and pid in cache
        real_lv = imp.landed_view
        imp.landed_view = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            assert imp.stock_unit_cost(pid, item1, 100.0, cache=cache) == unit    # cached
            real_get = imp.get_db
            seen = []

            class _Conn:
                def __init__(self):
                    self.c = real_get()

                def execute(self, *a):
                    return self.c.execute(*a)

                def rollback(self):
                    seen.append("rollback")
                    self.c.rollback()

                def close(self):
                    self.c.close()
            imp.get_db = _Conn
            try:
                assert imp.stock_unit_cost(pid, item1, 100.0) == 100.0
            finally:
                imp.get_db = real_get
            assert seen == ["rollback"], seen
        finally:
            imp.landed_view = real_lv
        assert svc.set_setting("landed_cost_to_stock", "0", buy)[0]

        # shipments and charges only while the order is live
        sql("UPDATE pr_requests SET status='cancelled' WHERE id=?", (pid,))
        live = one("SELECT id FROM pr_landed_charges WHERE pr_id=? AND status='active'", (pid,))["id"]
        assert imp.save_shipment(pid, "PO-T-1", ship, buy) == (False, "ship_locked")
        assert imp.add_charge(pid, "PO-T-1", {"kind": "duty", "amount": "5"}, buy) == (False, "ship_locked")
        assert imp.correct_charge(live, {"amount": "5"}, fin) == (False, "ship_locked", pid)
        assert imp.void_charge(live, "x", fin) == (False, "ship_locked", pid)
        sql("UPDATE pr_requests SET status='po_issued' WHERE id=?", (pid,))

        # every message these routes flash has its Arabic and Turkish
        from app.approvals.messages import MESSAGES
        from app.approvals.tests_messages_i18n import user_facing_strings
        from app.routes import approvals_imports as R
        said = set(R.MSG.values()) | set(R.DRAW_MSG.values()) | set(R.OK.values())
        said |= user_facing_strings(Path(R.__file__))
        missing = sorted(m for m in said if m not in MESSAGES)
        assert not missing, missing
    print("imports + framework agreements: OK")


if __name__ == "__main__":
    run()
