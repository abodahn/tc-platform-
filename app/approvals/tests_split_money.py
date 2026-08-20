"""A requisition that buys from SEVERAL suppliers must not blur them together.

Per-line vendors made one requisition able to carry three suppliers. Everything
downstream of the order still assumed one, and each of these is a place where
that assumption cost money or named the wrong company on a document:

  1. the Purchase Order printed the HEADER vendor whenever a requisition
     resolved to a single supplier the header did not name — and "—", no
     supplier at all, when the header was left blank;
  2. the debit note for goods rejected on receipt was raised against the header
     vendor, so the wrong supplier was debited and the wrong payable reduced;
  3. invoices and payments had no supplier dimension, so one supplier could be
     billed and paid the combined value of every other supplier's order;
  4. the per-vendor PO table offered PDF links that returned HTTP 400 the moment
     anything was delivered;
  5. the Goods Received Note named one supplier over three suppliers' lines;
  6. the RFQ endpoint accepted a direct POST at any status, cancelled included.

Everything here goes through the REAL flow — the ladder, the routes, the PDF
bytes — because every one of these was reachable from the screen.

    python -m app.approvals.tests_split_money
"""
import base64
import os
import re
import tempfile
import zlib


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "splitmoney.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _pdf_text(blob):
    """The PDF's content streams, decoded (reportlab writes them ASCII85+Flate)."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", blob, re.S):
        data = m.group(1)
        for step in (lambda x: base64.a85decode(x, adobe=True), zlib.decompress):
            try:
                data = step(data)
            except Exception:                       # noqa: BLE001 — image / raw stream
                pass
        out.append(data)
    return b"\n".join(out).decode("latin-1")


def run():                                          # noqa: C901 — one linear story
    app = _app()
    with app.app_context():
        import flask
        from app.db import get_db
        from app.approvals import services as svc, pdf as pdfgen
        from app.approvals.constants import STAGE_ROLES

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        store = {"username": "store", "role": "warehouse_manager", "id": 2}
        A, B, G = "Alphatex", "Betatrim", "Gammaknit"

        def q(sql, *args):
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

        def make(title, lines, header=None):
            """lines = [(item, qty, unit_price, vendor)], priced by Purchasing."""
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance",
                 "vendor": lines[0][3] if header is None else header, "tax_rate": 0},
                [{"item": i, "qty": qty, "unit_price": 0, "vendor": v}
                 for i, qty, _p, v in lines],
                {"username": "tech", "id": 9}, priced=False)
            ids = [r["id"] for r in q(
                "SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id", pr_id)]
            ok, msg = svc.price_pr(pr_id, {i: l[2] for i, l in zip(ids, lines)},
                                   {"tax_rate": 0}, buyer)
            assert ok, "pricing failed: %s" % msg
            return pr_id, ids

        def approve_and_issue(pr_id):
            svc.set_single_source(pr_id, "Sole authorised supplier for these parts", buyer)
            for _ in range(20):
                pr = q("SELECT status, current_seq FROM pr_requests WHERE id=?", pr_id)[0]
                steps = q("SELECT stage FROM pr_steps WHERE pr_id=? AND seq=? "
                          "AND status='pending'", pr_id, pr["current_seq"])
                if pr["status"] != "pending" or not steps:
                    break
                for s in steps:
                    role = sorted(STAGE_ROLES[s["stage"]])[0]
                    ok, msg = svc.act_on_step(
                        pr_id, {"username": "u_%s_%s" % (role, pr_id), "role": role,
                                "id": 100 + pr_id}, "approve", "ok")
                    assert ok, "ladder stuck at %s: %s" % (s["stage"], msg)
            ok, res = svc.issue_po(pr_id, buyer)
            assert ok, "issue_po refused: %s" % res
            return res

        # a signed-in super admin, for everything that has to go over HTTP
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                     "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                     ("sm_admin", "x", "sm_admin", "super_admin"))
        conn.commit()
        uid = q("SELECT id FROM users WHERE username='sm_admin'")[0]["id"]
        conn.close()
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
        flask.g.pop("user", None)

        def get(url):
            r = cli.get(url)
            assert "/login" not in (r.headers.get("Location") or ""), \
                "not signed in — this check would be vacuous"
            return r

        # =================================================================
        # 1. ONE vendor group, and it is NOT the header's
        # =================================================================
        for header, expect_absent in ((("Nilehouse"), "Nilehouse"), (None, None)):
            one, _ = make("Bearings for the winder",
                          [("Deltabearing", 100, 12, "Deltaweave"),
                           ("Deltagrease", 50, 4, "Deltaweave")], header=header)
            approve_and_issue(one)
            pos = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", one)
            assert len(pos) == 1 and pos[0]["vendor"] == "Deltaweave", pos
            r = get("/procurement/pr/%d/po.pdf" % one)
            assert r.status_code == 200, r.status_code
            text = _pdf_text(r.get_data())
            assert "Deltaweave" in text, (
                "the order's own supplier is missing from its own purchase order")
            assert "VENDOR" not in text or "Deltaweave" in text
            if expect_absent:
                assert expect_absent not in text, (
                    "the PO printed the stale HEADER vendor %r instead of the "
                    "order's own supplier" % expect_absent)

        # =================================================================
        # 2. three suppliers: rejected goods debit the RIGHT one
        # =================================================================
        three, ids = make("Spare parts for the compressor overhaul", [
            ("Alphabearing", 10, 100, A),    # 1,000
            ("Betaseal", 20, 100, B),        # 2,000
            ("Gammavalve", 5, 70, G),        # 350
        ])
        approve_and_issue(three)
        pos = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", three)
        assert [p["vendor"] for p in pos] == [A, B, G], pos

        # reject 2 Gammavalve (Gammaknit's line), accept the rest of it
        ok, msg = svc.receive_items(three, {ids[0]: 10, ids[1]: 20, ids[2]: 3}, store,
                                    rejects={ids[2]: 2}, reject_reason="Seat scored")
        assert ok, msg
        rets = q("SELECT * FROM pr_returns WHERE pr_id=? ORDER BY id", three)
        assert len(rets) == 1, "one supplier rejected -> one debit note: %r" % rets
        assert rets[0]["vendor"] == G, (
            "the debit note was raised against %r, not the supplier who shipped "
            "the goods (%s)" % (rets[0]["vendor"], G))
        assert rets[0]["total"] == 140.0, rets[0]
        assert rets[0]["vendor_id"] is None, (
            "a non-header supplier must not borrow the header's register id")
        dn = _pdf_text(pdfgen.debit_note_pdf(svc.get_pr(three), rets[0]))
        assert G in dn, "the debit note does not name the supplier being debited"
        assert A not in dn and B not in dn, (
            "the debit note names a supplier that shipped nothing on it")
        assert pos[2]["po_no"] in dn, (
            "the debit note references the wrong purchase order: %s" % pos[2]["po_no"])
        # ...and the money follows the name
        dues = svc.vendor_dues()
        assert (dues.get(G) or {}).get("amount") == 140.0, dues
        assert not dues.get(A), "the header supplier was debited for goods it never shipped"

        # =================================================================
        # 3. the GRN spanning three suppliers says so (defect 5)
        # =================================================================
        grns = q("SELECT * FROM pr_grn WHERE pr_id=? ORDER BY id", three)
        grn = _pdf_text(pdfgen.grn_pdf(svc.get_pr(three), grns[-1]))
        assert "Several suppliers" in grn, (
            "a three-supplier delivery still prints one supplier's name at the top")
        for v in (A, B, G):
            assert v in grn, "the GRN does not name the supplier of every line (%s)" % v

        # =================================================================
        # 4. every supplier's PO stays printable after a delivery (defect 4)
        # =================================================================
        assert q("SELECT status FROM pr_requests WHERE id=?", three)[0]["status"] \
            == "partially_received"
        for p in pos:
            r = get("/procurement/pr/%d/po.pdf?po=%d" % (three, p["id"]))
            assert r.status_code == 200, (
                "%s is unreachable after the first delivery (HTTP %s) — on a split "
                "request this is the only route to it" % (p["po_no"], r.status_code))
            assert p["po_no"] in _pdf_text(r.get_data())

        # =================================================================
        # 5. one supplier cannot be billed or paid the others' money (defect 3)
        # =================================================================
        money, mids = make("Consumables for the overhaul", [
            ("Alphaseal", 10, 100, A),       # 1,000
            ("Betaclip", 20, 100, B),        # 2,000
            ("Gammanut", 5, 70, G),          # 350
        ])
        approve_and_issue(money)
        mpos = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", money)
        gamma = next(p for p in mpos if p["vendor"] == G)
        svc.receive_items(money, {mids[0]: 10, mids[1]: 20, mids[2]: 5}, store)

        ok, msg = svc.add_invoice(money, {"invoice_no": "INV-NOPO", "amount": 3210}, buyer)
        assert not ok and msg == "po_required", (
            "a split requisition accepted an invoice that names no supplier's "
            "order: %s / %s" % (ok, msg))
        # ...and the refusal survives the route, in the reader's language
        r = cli.post("/procurement/pr/%d/invoice" % money,
                     data={"_csrf": "tok", "invoice_no": "INV-HTTP", "amount": "3210"})
        assert r.status_code in (200, 302), r.status_code
        assert not q("SELECT id FROM pr_invoices WHERE invoice_no='INV-HTTP'"), \
            "the route booked an invoice the service refused"
        ok, msg = svc.add_invoice(money, {"invoice_no": "INV-G-BIG", "amount": 3210,
                                          "po_id": gamma["id"]}, buyer)
        assert not ok and msg == "exceeds_po", (
            "%s's 350 order was billed 3,210 — the combined value of the other two "
            "suppliers' orders: %s / %s" % (G, ok, msg))
        # the screen has to OFFER the choice, or the gate below is a dead end
        page = get("/procurement/pr/%d" % money).get_data().decode("utf-8")
        assert page.count('name="po_id"') == 2, (
            "the invoice and payment forms must both let the buyer pick which "
            "supplier's order the money is for (found %d pickers)"
            % page.count('name="po_id"'))
        assert 'data-i18n="proc.for_order"' in page

        alpha = next(p for p in mpos if p["vendor"] == A)
        for no, amount, po in (("INV-A-1", 1000, alpha), ("INV-G-1", 350, gamma)):
            ok, msg = svc.add_invoice(money, {"invoice_no": no, "amount": amount,
                                              "po_id": po["id"]}, buyer)
            assert ok, "a supplier's own order must still be invoiceable: %s" % msg
        assert q("SELECT po_id FROM pr_invoices WHERE invoice_no='INV-G-1'"
                 )[0]["po_id"] == gamma["id"]

        # 1,000 against a 350 order. EVERY request-level cap lets this through —
        # the request is worth 3,350 and 1,350 of it is invoiced — so only the
        # order's own ceiling can stop it.
        ok, msg = svc.add_payment(money, {"amount": 1000, "po_id": gamma["id"]}, buyer)
        assert not ok and msg == "over_payment", (
            "%s's 350 order was paid 1,000 out of another supplier's money: %s"
            % (G, msg))
        ok, msg = svc.add_payment(money, {"amount": 100}, buyer)
        assert not ok and msg == "po_required", (
            "a payment on a split requisition released money against no order: %s" % msg)
        ok, msg = svc.add_payment(money, {"amount": 350, "po_id": gamma["id"]}, buyer)
        assert ok, "the supplier's own order must still be payable: %s" % msg
        assert q("SELECT po_id FROM pr_payments WHERE pr_id=?", money)[0]["po_id"] \
            == gamma["id"]
        ok, msg = svc.add_payment(money, {"amount": 1000, "po_id": alpha["id"]}, buyer)
        assert ok, "the other supplier's own order must still be payable: %s" % msg

        # =================================================================
        # 6. sourcing is closed once the request is not sourceable (defect 6)
        # =================================================================
        cancelled, _ = make("Cancelled request", [("Filter", 6, 300, "Nilehouse")])
        conn = get_db()
        conn.execute("UPDATE pr_requests SET status='cancelled' WHERE id=?", (cancelled,))
        conn.commit()
        conn.close()
        r = cli.post("/procurement/pr/%d/rfq" % cancelled,
                     data={"_csrf": "tok", "vendors": "Nilehouse"})
        assert r.status_code in (200, 302), r.status_code
        assert not q("SELECT id FROM pr_rfqs WHERE pr_id=?", cancelled), (
            "a controlled RFQ number was issued against a cancelled request")

        # =================================================================
        # 7. the one-supplier request is untouched, start to finish
        # =================================================================
        solo, sids = make("Bearings for line 3", [
            ("Bearing", 4, 500, "Nilehouse"), ("Grease", 2, 250, "Nilehouse")])
        approve_and_issue(solo)
        assert len(q("SELECT id FROM pr_purchase_orders WHERE pr_id=?", solo)) == 1
        r = get("/procurement/pr/%d/po.pdf" % solo)
        assert r.status_code == 200
        t = _pdf_text(r.get_data())
        for word in ("Bearing", "Grease", "2,500.00", "Nilehouse"):
            assert word in t, "the single-supplier PO lost %r" % word
        assert "Several suppliers" not in t
        ok, msg = svc.receive_items(solo, {sids[0]: 4, sids[1]: 2}, store)
        assert ok, msg
        g = _pdf_text(pdfgen.grn_pdf(svc.get_pr(solo),
                                     q("SELECT * FROM pr_grn WHERE pr_id=?", solo)[-1]))
        assert "Nilehouse" in g and "Several suppliers" not in g, (
            "the single-supplier GRN changed shape")
        assert "ITEM / DESCRIPTION" in g and g.count("VENDOR") == 1, (
            "the single-supplier GRN grew the split layout's extra VENDOR column")
        # no po_id anywhere: the request IS the order, exactly as before
        ok, msg = svc.add_invoice(solo, {"invoice_no": "INV-SOLO", "amount": 2500}, buyer)
        assert ok, "a single-supplier request must still invoice with no order named: %s" % msg
        ok, msg = svc.add_payment(solo, {"amount": 2500, "method": "transfer"}, buyer)
        assert ok, "a single-supplier request must still pay with no order named: %s" % msg
        assert q("SELECT payment_status FROM pr_requests WHERE id=?",
                 solo)[0]["payment_status"] == "paid", "the solo payment did not settle"

        # =================================================================
        # 8. a request CANCELLED after approval keeps no Purchase Order panel:
        #    _pos_for() synthesises an order from pr_requests.po_no, which
        #    survives the cancellation, and its PDF link answers 400.
        # =================================================================
        live = get("/procurement/pr/%d" % three).get_data(as_text=True)
        assert "proc.po.title" in live, (
            "a real order lost its Purchase Order panel")
        dead, _ = make("Bearings that were never bought",
                       [("Deltabearing", 4, 500, "Deltaweave")])
        approve_and_issue(dead)
        ok, msg = svc.cancel_pr(dead, {"username": "sm_admin"}, is_admin=True)
        assert ok, "admin cancel after approval refused: %s" % msg
        assert get("/procurement/pr/%d/po.pdf" % dead).status_code == 400, (
            "a cancelled request must not serve a purchase order")
        page = get("/procurement/pr/%d" % dead).get_data(as_text=True)
        assert "proc.po.title" not in page, (
            "the cancelled request still advertises a Purchase Order panel whose "
            "PDF link answers 400")

        print("PASS: every purchase order prints its own supplier, the debit note "
              "debits the supplier who shipped, no supplier can be billed or paid "
              "another's money, every order stays printable after delivery, the GRN "
              "names its suppliers, RFQs stop at a closed request — and the "
              "single-supplier request is unchanged")
        return True


if __name__ == "__main__":
    run()
