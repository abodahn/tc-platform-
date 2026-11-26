"""Procurement corrections: reversals, vendor master, PO vendor gate, returns after
acceptance, invoice/payment segregation, cancel with money, short-close budget
release, EGP spend analytics and EGP stock valuation. Isolated temp DB.

    python -m app.approvals.tests_corrections
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="corr_"), "corr.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


VENDOR = "Corr Vendor"
UNIT = 100.0


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        conn = get_db()
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            uname = "corr_" + stage
            conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                         "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                         (uname, "x", uname, role))
            signer[stage] = {"username": uname, "role": role}
        conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty, reorder_level, "
                     "max_level, avg_cost, is_active) VALUES ('CORR-SP','Corr part',0,0,10000,0,1)")
        sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code='CORR-SP'").fetchone()["id"]
        conn.commit()
        conn.close()
        buyer, store = signer["purchasing"], signer["warehouse"]
        payer = {"username": "corr_payer", "role": "finance_manager"}
        reqr = {"username": "corr_req", "id": 77}

        def q(sql, *a):
            c = get_db()
            try:
                return c.execute(sql, a).fetchone()
            finally:
                c.close()

        def stock():
            return float(q("SELECT stock_qty FROM mnt_spare_parts WHERE id=?", sid)["stock_qty"] or 0)

        # ---- 2. vendor master -------------------------------------------------
        assert svc.create_vendor({"name": VENDOR, "tax_id": "TX-1"}, buyer) == (True, "")
        # a buyer's new supplier starts pending; Finance approves it (vendors lane)
        assert svc.set_vendor_active(q("SELECT id FROM proc_vendors WHERE name=?", VENDOR)["id"],
                                     True, payer) == (True, "")
        assert svc.create_vendor({"name": "  corr VENDOR "}, buyer) == (False, "duplicate_vendor")
        assert svc.create_vendor({"name": "Other", "tax_id": "tx-1"}, buyer) == (False, "duplicate_vendor")
        assert svc.create_vendor({"name": " "}, buyer) == (False, "name_required")
        assert svc.create_vendor({"name": "Second Vendor"}, buyer)[0]
        v2 = q("SELECT id FROM proc_vendors WHERE name='Second Vendor'")["id"]
        assert svc.update_vendor(v2, {"name": "corr vendor"}, buyer) == (False, "duplicate_vendor")
        assert svc.update_vendor(v2, {"name": "Second Vendor", "phone": "1"}, buyer)[0]
        assert svc.update_vendor(v2, {"name": "Renamed Vendor"}, buyer)[0]
        assert q("SELECT name FROM proc_vendors WHERE id=?", v2)["name"] == "Renamed Vendor"
        assert svc.update_vendor(v2, {"name": "Second Vendor"}, buyer)[0]
        assert svc.set_vendor_active(v2, False, buyer, reason="test block")[0]
        assert "Second Vendor" not in [v["name"] for v in svc.list_vendors()]

        def current_stage(pr_id):
            r = q("SELECT stage FROM pr_steps WHERE pr_id=? AND status='pending' ORDER BY seq LIMIT 1", pr_id)
            return r["stage"] if r else None

        def approve(title, vendor=VENDOR, qty=10.0, item=None, department="General Maintenance"):
            pr_id, _ = svc.create_pr({"title": title, "department": department,
                                      "currency": "EGP", "vendor": vendor},
                                     [{"item": item or "Corr part", "qty": qty, "unit_price": 0,
                                       "spare_id": None if item else sid}], reqr, priced=False)
            li = q("SELECT id FROM pr_items WHERE pr_id=?", pr_id)["id"]
            ok, msg = svc.price_pr(pr_id, {li: UNIT}, {"tax_rate": 0}, buyer)
            assert ok, msg
            for _ in range(15):
                stage = current_stage(pr_id)
                if stage is None:
                    break
                ok, msg = svc.act_on_step(pr_id, signer[stage], "approve")
                if not ok and msg == "needs_quotes":
                    for n, mult in (("A", 1.0), ("B", 1.05)):
                        svc.add_quote(pr_id, {"payment_terms": "30 days", "vendor": "Quote %s" % n,
                                              "amount": qty * UNIT * mult}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
                    continue
                if not ok and msg == "deviation_memo_required":
                    assert svc.set_doam_document(pr_id, "deviation_memo", "Replenishment agreed with the supplier ahead of the shutdown.", buyer)[0]
                    continue
                assert ok, (stage, msg)
                if msg == "approved":
                    break
            return pr_id, li

        # ---- 3. PO only to an active master vendor ----------------------------
        p_bad, _ = approve("Unknown supplier", vendor="Nobody Ltd")
        assert svc.issue_po(p_bad, buyer) == (False, "vendor_not_in_master")
        assert svc.create_vendor({"name": "Third Vendor"}, buyer)[0]
        p_inact, _ = approve("Inactive supplier", vendor="Third Vendor")
        svc.set_vendor_active(q("SELECT id FROM proc_vendors WHERE name='Third Vendor'")["id"], False, buyer,
                              reason="test block")
        assert svc.issue_po(p_inact, buyer, force=True) == (False, "vendor_not_in_master")
        assert svc.add_quote(p_inact, {"payment_terms": "30 days", "vendor": "third vendor", "amount": 5}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK") \
            == (False, "vendor_inactive")

        pr_id, li = approve("Corrections")
        vid = q("SELECT id FROM proc_vendors WHERE name=?", VENDOR)["id"]
        assert svc.update_vendor(vid, {"name": "Renamed Corr Vendor"}, buyer) == (False, "vendor_in_use")
        assert q("SELECT name FROM proc_vendors WHERE id=?", vid)["name"] == VENDOR
        assert svc.update_vendor(vid, {"name": VENDOR, "phone": "2"}, buyer)[0]
        assert q("SELECT phone FROM proc_vendors WHERE id=?", vid)["phone"] == "2"
        # case/spacing-only rename also orphans name-keyed history -> refused
        assert svc.update_vendor(vid, {"name": "  corr VENDOR  "}, buyer) == (False, "vendor_in_use")
        assert svc.update_vendor(vid, {"name": "  " + VENDOR + "  "}, buyer)[0]  # same name, trimmed
        # non-ASCII capitals: SQLite LOWER() would miss these; the guard must not
        assert svc.create_vendor({"name": "ÖZTÜRK Tekstil"}, buyer)[0]
        tvid = q("SELECT id FROM proc_vendors WHERE name=?", "ÖZTÜRK Tekstil")["id"]
        approve("Turkish supplier", vendor="öztürk tekstil")
        assert svc.update_vendor(tvid, {"name": "Ozturk Textiles"}, buyer) == (False, "vendor_in_use")
        assert svc.issue_po(pr_id, buyer)[0]
        assert svc.receive_items(pr_id, {li: 10}, store)[0]
        assert stock() == 10

        # ---- 1a. receipt reversal takes stock back out ------------------------
        assert svc.reverse_receipt(pr_id, li, 2, "", buyer) == (False, "reason_required")
        assert svc.reverse_receipt(pr_id, li, 11, "typo", buyer) == (False, "bad_qty")
        assert svc.reverse_receipt(pr_id, li, 2, "typed 10, truck had 8", buyer) == (True, "")
        assert stock() == 8
        r = q("SELECT received_qty FROM pr_items WHERE id=?", li)
        assert float(r["received_qty"]) == 8
        assert q("SELECT status FROM pr_requests WHERE id=?", pr_id)["status"] == "partially_received"
        assert q("SELECT 1 FROM pr_events WHERE pr_id=? AND action='receipt_reversed'", pr_id)
        # stock issued onward -> refused, nothing changes
        c = get_db(); c.execute("UPDATE mnt_spare_parts SET reserved_qty=7 WHERE id=?", (sid,)); c.commit(); c.close()
        assert svc.reverse_receipt(pr_id, li, 2, "again", buyer) == (False, "stock_issued")
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li)["received_qty"]) == 8
        c = get_db(); c.execute("UPDATE mnt_spare_parts SET reserved_qty=0 WHERE id=?", (sid,)); c.commit(); c.close()

        # ---- 4. return to supplier after acceptance -> debit note --------------
        assert svc.return_to_supplier(pr_id, li, 3, "stitching defect found in line", buyer) == (True, "")
        assert stock() == 5
        ret = q("SELECT * FROM pr_returns WHERE pr_id=?", pr_id)
        assert ret and float(ret["qty"]) == 3 and float(ret["total"]) == 300 and ret["dn_no"]
        m = svc.three_way_match(pr_id)
        assert m["payable"] == 500.0, m["payable"]

        # ---- 5. finance records the invoice; recorder may not pay it -----------
        assert svc.add_invoice(pr_id, {"invoice_no": "INV-C1", "amount": 500}, payer)[0]
        assert svc.add_payment(pr_id, {"amount": 100}, payer) == (False, "sod_invoice_payer")
        fin = {"username": "corr_fin2", "role": "finance_manager"}
        assert svc.add_payment(pr_id, {"amount": 100}, fin)[0]
        inv = q("SELECT id FROM pr_invoices WHERE invoice_no='INV-C1'")["id"]
        # ---- 1b. void refused while paid against ------------------------------
        assert svc.void_invoice(inv, "wrong number", buyer) == (False, "invoice_paid")
        # ---- 6. cancel refused while money is out -----------------------------
        assert svc.cancel_pr(pr_id, reqr, is_admin=True) == (False, "has_payments")
        # ---- 1c. payment reversal ---------------------------------------------
        pay = q("SELECT id FROM pr_payments WHERE pr_id=?", pr_id)["id"]
        assert svc.reverse_payment(pay, "bank returned it", fin) == (True, "")
        assert svc.reverse_payment(pay, "twice", fin) == (False, "already_reversed")
        pr = q("SELECT paid_amount, payment_status FROM pr_requests WHERE id=?", pr_id)
        assert float(pr["paid_amount"]) == 0 and pr["payment_status"] == "unpaid"
        assert svc.void_invoice(inv, "wrong number", buyer) == (True, "")
        assert svc.void_invoice(inv, "again", buyer) == (False, "already_void")
        assert not svc.three_way_match(pr_id)["has_invoice"]
        assert svc.add_invoice(pr_id, {"invoice_no": "INV-C1", "amount": 500}, buyer)[0], \
            "a voided number may be re-entered"

        # ---- 7. short-close releases the unreceived value from the budget -----
        dept = "General Maintenance"
        before = svc.budget_status(dept)["spent"]
        assert svc.close_pr(pr_id, buyer, reason="rest cancelled by supplier")[0]
        after = svc.budget_status(dept)["spent"]
        assert round(before - after, 2) == 500.0, (before, after)

        # ---- 6b. cancel marks the supplier orders cancelled --------------------
        p_c, _ = approve("Cancel me")
        assert svc.issue_po(p_c, buyer)[0]
        assert svc.cancel_pr(p_c, reqr, is_admin=True) == (True, "")
        assert q("SELECT status FROM pr_purchase_orders WHERE pr_id=?", p_c)["status"] == "cancelled"

        # ---- 9 + 8. foreign currency: stock cost and spend in EGP -------------
        p_fx, li_fx = approve("USD order", qty=1.0)
        # shortcut: currency flipped after approval (the ladder's RFQ gate is not under test here)
        c = get_db(); c.execute("UPDATE pr_requests SET currency='USD', fx_rate=50 WHERE id=?", (p_fx,)); c.commit(); c.close()
        assert svc.issue_po(p_fx, buyer)[0]
        assert svc.receive_items(p_fx, {li_fx: 1}, store)[0]
        avg = float(q("SELECT last_price FROM mnt_spare_parts WHERE id=?", sid)["last_price"])
        assert avg == UNIT * 50.0, avg
        a = svc.analytics_summary()
        assert a["spend_by_vendor"].get(VENDOR, 0) >= 5000.0, a["spend_by_vendor"]
        heads, rows = svc.export_dataset("spend_vendor")
        assert heads[-1] == "Total (EGP)" and any(r[0] == VENDOR and r[2] >= 5000 for r in rows)
        assert svc.export_dataset("open_orders")[0], "open_orders export binds 3 statuses"
        # ---- 10. warehouse door receipts reverse physical stock atomically ----
        from app.warehouse import services as wh
        c = get_db()
        for code, tracked in (("CORR-QTY", 0), ("CORR-ROLL", 1)):
            c.execute("INSERT INTO wh_materials (code,name,roll_tracked) VALUES (?,?,?)",
                      (code, code + " material", tracked))
        c.commit(); c.close()
        mid = q("SELECT id FROM wh_materials WHERE code='CORR-QTY'")["id"]
        p_door, li_door = approve("Door quantity", qty=100, item="CORR-QTY material",
                                  department="Production")
        ok, po_no = svc.issue_po(p_door, buyer)
        assert ok, po_no
        assert wh.receive_qty(mid, 100, UNIT, store, grn_ref=" " + po_no.lower())[0]  # door stores the typed ref raw
        assert svc.receive_items(p_door, {li_door: 100}, store, post_stock=False)[0]
        assert not q("SELECT 1 FROM pr_stock_postings WHERE pr_id=?", p_door)
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 100
        assert svc.return_to_supplier(p_door, li_door, 40, "defective delivery", buyer) == (True, "")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 60
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li_door)["received_qty"]) == 60
        ret = q("SELECT qty, dn_no FROM pr_returns WHERE pr_id=?", p_door)
        assert ret and float(ret["qty"]) == 40 and ret["dn_no"]
        c = get_db(); c.execute("UPDATE wh_materials SET reserved_qty=50 WHERE id=?", (mid,)); c.commit(); c.close()
        assert svc.reverse_receipt(p_door, li_door, 60, "wrong receipt", buyer) == (False, "stock_issued")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 60
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li_door)["received_qty"]) == 60
        c = get_db(); c.execute("UPDATE wh_materials SET reserved_qty=0 WHERE id=?", (mid,)); c.commit(); c.close()
        assert svc.reverse_receipt(p_door, li_door, 60, "wrong receipt", buyer) == (True, "")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 0
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li_door)["received_qty"]) == 0
        assert float(q("SELECT SUM(qty) AS n FROM pr_stock_postings WHERE pr_id=? AND ref LIKE 'door:%'",
                       p_door)["n"]) == -100

        rid_mat = q("SELECT id FROM wh_materials WHERE code='CORR-ROLL'")["id"]
        p_roll, li_roll = approve("Door rolls", qty=100, item="CORR-ROLL material",
                                  department="Production")
        assert svc.issue_po(p_roll, buyer)[0]
        pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", p_roll)["pr_no"]
        assert wh.receive_rolls(rid_mat, {"grn_ref": pr_no, "unit_cost": UNIT},
                                [{"length_m": 40}, {"length_m": 60}], store)[0]
        assert svc.receive_items(p_roll, {li_roll: 100}, store, post_stock=False)[0]
        assert svc.reverse_receipt(p_roll, li_roll, 80, "incorrect roll lengths", buyer) == (True, "")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", rid_mat)["stock_qty"]) == 20
        c = get_db()
        rolls = c.execute("SELECT remaining_m FROM wh_rolls WHERE material_id=? ORDER BY id",
                          (rid_mat,)).fetchall()
        c.close()
        assert [float(r["remaining_m"]) for r in rolls] == [20, 0]
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li_roll)["received_qty"]) == 20

        p_short, li_short = approve("Door mismatch", qty=10, item="CORR-QTY material",
                                    department="Production")
        assert svc.issue_po(p_short, buyer)[0]
        pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", p_short)["pr_no"]
        assert wh.receive_qty(mid, 5, UNIT, store, grn_ref=pr_no)[0]
        assert svc.receive_items(p_short, {li_short: 10}, store, post_stock=False)[0]
        assert svc.reverse_receipt(p_short, li_short, 10, "mismatched receipt", buyer) == (False, "stock_not_posted")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 5
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li_short)["received_qty"]) == 10
        # Mixed bridged/door receipts cannot each spend the same free stock.
        c = get_db()
        for code in ("CORR-MIX", "CORR-DEACT", "CORR-GONE"):
            c.execute("INSERT INTO wh_materials (code,name,roll_tracked) VALUES (?,?,0)",
                      (code, code + " material"))
        c.commit(); c.close()
        mid = q("SELECT id FROM wh_materials WHERE code='CORR-MIX'")["id"]
        p, li = approve("Door mixed", qty=100, item="CORR-MIX material", department="Production")
        assert svc.issue_po(p, buyer)[0]
        pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", p)["pr_no"]
        assert svc.receive_items(p, {li: 50}, store)[0]
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 50
        assert wh.receive_qty(mid, 50, UNIT, store, grn_ref=pr_no)[0]
        assert svc.receive_items(p, {li: 50}, store, post_stock=False)[0]
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 100
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li)["received_qty"]) == 100
        assert wh.issue_internal(mid, 40, "department", "Sewing", "consumed", store)[0]
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 60
        assert svc.reverse_receipt(p, li, 100, "x", buyer) == (False, "stock_not_posted")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 60
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li)["received_qty"]) == 100
        assert not q("SELECT 1 FROM pr_stock_postings WHERE pr_id=? "
                     "AND (ref LIKE 'rev:%' OR ref LIKE 'door:%')", p)

        # Inactive materials still reverse; missing materials must not skip stock.
        mid = q("SELECT id FROM wh_materials WHERE code='CORR-DEACT'")["id"]
        p, li = approve("Door inactive", qty=20, item="CORR-DEACT material", department="Production")
        assert svc.issue_po(p, buyer)[0]
        pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", p)["pr_no"]
        assert wh.receive_qty(mid, 20, UNIT, store, grn_ref=pr_no)[0]
        assert svc.receive_items(p, {li: 20}, store, post_stock=False)[0]
        c = get_db()
        c.execute("UPDATE wh_materials SET is_active=0 WHERE id=?", (mid,))
        c.commit(); c.close()
        assert svc.return_to_supplier(p, li, 20, "x", buyer) == (True, "")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 0
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li)["received_qty"]) == 0

        mid = q("SELECT id FROM wh_materials WHERE code='CORR-GONE'")["id"]
        p, li = approve("Door missing", qty=10, item="CORR-GONE material", department="Production")
        assert svc.issue_po(p, buyer)[0]
        pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", p)["pr_no"]
        assert wh.receive_qty(mid, 10, UNIT, store, grn_ref=pr_no)[0]
        assert svc.receive_items(p, {li: 10}, store, post_stock=False)[0]
        c = get_db()
        c.execute("DELETE FROM wh_materials WHERE id=?", (mid,))
        c.commit(); c.close()
        assert svc.return_to_supplier(p, li, 10, "x", buyer) == (False, "stock_not_posted")
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li)["received_qty"]) == 10

        # A replacement material that now matches the line must not hide the old one's door stock.
        c = get_db()
        mid = c.execute("INSERT INTO wh_materials (code,name,roll_tracked) "
                        "VALUES ('CORR-OLD','CORR-RENAME material',0)").lastrowid
        c.commit(); c.close()
        p, li = approve("Door renamed", qty=10, item="CORR-RENAME material", department="Production")
        assert svc.issue_po(p, buyer)[0]
        pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", p)["pr_no"]
        assert wh.receive_qty(mid, 10, UNIT, store, grn_ref=pr_no)[0]
        assert svc.receive_items(p, {li: 10}, store, post_stock=False)[0]
        c = get_db()
        c.execute("UPDATE wh_materials SET is_active=0 WHERE id=?", (mid,))
        c.execute("INSERT INTO wh_materials (code,name,roll_tracked) VALUES ('CORR-RENAME','Replacement',0)")
        c.commit(); c.close()
        assert svc.return_to_supplier(p, li, 10, "x", buyer) == (False, "stock_not_posted")
        assert float(q("SELECT stock_qty FROM wh_materials WHERE id=?", mid)["stock_qty"]) == 10
        assert float(q("SELECT received_qty FROM pr_items WHERE id=?", li)["received_qty"]) == 10
        print("PASS: corrections — reversals, vendor master, PO gate, returns, SoD, "
              "cancel, short-close budget, EGP spend and stock cost, warehouse door corrections")
        return True


if __name__ == "__main__":
    run()
