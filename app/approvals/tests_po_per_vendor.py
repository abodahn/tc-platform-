"""One purchase order per VENDOR — a supplier's document may never carry a
competitor's lines or prices.

A requisition can buy from several suppliers (pr_items.vendor). It used to issue
ONE order for the whole request, so a three-supplier request produced one
document showing all three suppliers' items and prices — and that document is
what gets emailed to a supplier.

Everything here goes through the REAL flow: create_pr(priced=False) -> price_pr
-> the actual approval ladder -> issue_po, then the PDF bytes are read back, so
what is asserted is what a supplier would actually receive.

The third block is the one that matters most: a request issued BEFORE this
change has NO rows in pr_purchase_orders and its order lives in
pr_requests.po_no. It must still open, still print, still receive.

    python -m app.approvals.tests_po_per_vendor
"""
import base64
import os
import re
import sys
import tempfile
from pathlib import Path
import zlib


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "povendor.db")
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


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, pdf as pdfgen
        from app.approvals.constants import STAGE_ROLES

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        store = {"username": "store", "role": "warehouse_manager", "id": 2}

        def q(sql, *args):
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

        def make(title, lines, tax=0):
            """lines = [(item, qty, unit_price, vendor)] — priced by Purchasing,
            exactly as the UI does it."""
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance",
                 "vendor": lines[0][3], "tax_rate": tax},
                [{"item": i, "qty": qty, "unit_price": 0, "vendor": v}
                 for i, qty, _p, v in lines],
                {"username": "tech", "id": 9}, priced=False)
            ids = [r["id"] for r in q(
                "SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id", pr_id)]
            ok, msg = svc.price_pr(pr_id, {i: l[2] for i, l in zip(ids, lines)},
                                   {"tax_rate": tax}, buyer)
            assert ok, "pricing failed: %s" % msg
            return pr_id, ids

        def approve_and_issue(pr_id):
            """Walk the REAL ladder, one distinct signer per stage."""
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
            drafted = q("SELECT po_no FROM pr_requests WHERE id=?", pr_id)[0]["po_no"]
            ok, res = svc.issue_po(pr_id, buyer)
            assert ok, "issue_po refused: %s" % res
            return drafted, res

        # =================================================================
        # 1. THREE vendors -> three orders, three numbers, three documents
        # =================================================================
        A, B, G = "Alphatex", "Betatrim", "Gammaknit"
        three, _ = make("Spare parts for the compressor overhaul", [
            ("Alphabearing", 10, 100, A),    # 1,000
            ("Betaseal", 20, 100, B),        # 2,000
            ("Gammavalve", 5, 70, G),        # 350
        ])
        drafted, primary = approve_and_issue(three)

        pos = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", three)
        assert len(pos) == 3, "one PO per vendor, got %r" % [p["po_no"] for p in pos]
        assert [p["vendor"] for p in pos] == [A, B, G], [p["vendor"] for p in pos]
        nums = [p["po_no"] for p in pos]
        assert len(set(nums)) == 3, "PO numbers must be distinct: %r" % nums
        assert [p["grand"] for p in pos] == [1000, 2000, 350], [p["grand"] for p in pos]

        # pr_requests.po_no keeps the PRIMARY number — every existing screen,
        # PDF link, report and the maintenance bridge resolve against it.
        head = q("SELECT po_no, status FROM pr_requests WHERE id=?", three)[0]
        assert head["po_no"] == nums[0] == primary == drafted, (head, nums, primary, drafted)
        assert head["status"] == "po_issued", head

        # ...and each PDF carries ONLY its own supplier.
        bundle = svc.get_pr(three)
        assert [p["po_no"] for p in bundle["pos"]] == nums, bundle["pos"]
        others = {A: (B, G, "Betaseal", "Gammavalve", "3,350.00"),
                  B: (A, G, "Alphabearing", "Gammavalve", "3,350.00"),
                  G: (A, B, "Alphabearing", "Betaseal", "3,350.00")}
        mine = {A: ("Alphabearing", "1,000.00"), B: ("Betaseal", "2,000.00"),
                G: ("Gammavalve", "350.00")}
        for p in bundle["pos"]:
            text = _pdf_text(pdfgen.po_pdf(bundle, p))
            assert p["po_no"] in text, "%s must print its own number" % p["po_no"]
            assert p["vendor"] in text, "%s must name its vendor" % p["po_no"]
            for word in mine[p["vendor"]]:
                assert word in text, "%s is missing its own %r" % (p["po_no"], word)
            for leak in others[p["vendor"]]:
                assert leak not in text, (
                    "%s (%s) leaks %r — a supplier must never see a competitor's "
                    "lines or prices" % (p["po_no"], p["vendor"], leak))

        # the one-argument call still works and gives the PRIMARY order
        assert _pdf_text(pdfgen.po_pdf(bundle)) == _pdf_text(
            pdfgen.po_pdf(bundle, bundle["pos"][0])), \
            "po_pdf(bundle) must stay the primary vendor's document"

        # =================================================================
        # 2. ONE vendor -> exactly one order, numbered as it is today
        # =================================================================
        one, _ = make("Bearings for line 3", [
            ("Bearing", 4, 500, "Nilehouse"),      # 2,000
            ("Grease", 2, 250, "Nilehouse"),       # 500
        ])
        drafted1, primary1 = approve_and_issue(one)
        pos1 = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", one)
        assert len(pos1) == 1, "single-vendor request must issue ONE PO: %r" % pos1
        assert pos1[0]["po_no"] == drafted1 == primary1, (pos1, drafted1, primary1)
        assert pos1[0]["grand"] == 2500, pos1[0]
        b1 = svc.get_pr(one)
        t1 = _pdf_text(pdfgen.po_pdf(b1))
        for word in ("Bearing", "Grease", "2,500.00", drafted1, "Nilehouse"):
            assert word in t1, "single-vendor PO lost %r" % word

        # =================================================================
        # 3. A request issued BEFORE this change: no rows, only pr.po_no
        # =================================================================
        legacy, legacy_lines = make("Filters (raised last month)", [
            ("Filter", 6, 300, "Nilehouse"),       # 1,800
        ])
        approve_and_issue(legacy)
        conn = get_db()
        conn.execute("DELETE FROM pr_purchase_orders WHERE pr_id=?", (legacy,))
        conn.commit()
        conn.close()
        old_no = q("SELECT po_no FROM pr_requests WHERE id=?", legacy)[0]["po_no"]

        b2 = svc.get_pr(legacy)                     # still opens
        assert len(b2["pos"]) == 1, "the fallback must synthesise the old order: %r" % b2["pos"]
        assert b2["pos"][0]["po_no"] == old_no, b2["pos"]
        assert b2["pos"][0]["id"] is None, "a synthesised row has no id to link to"
        assert b2["pos"][0]["grand"] == 1800, b2["pos"]
        t2 = _pdf_text(pdfgen.po_pdf(b2))           # still prints
        for word in ("Filter", "1,800.00", old_no):
            assert word in t2, "legacy PO lost %r" % word
        ok, msg = svc.receive_items(legacy, {legacy_lines[0]: 6}, store)   # still receives
        assert ok, "a legacy order must still receive: %s" % msg
        assert q("SELECT status FROM pr_requests WHERE id=?", legacy)[0]["status"] \
            == "received", "legacy receipt did not close the order"
        assert svc.three_way_match(legacy)["ordered_grand"] == 1800, \
            "the three-way match still reads the request, not the PO table"
        ok, msg = svc.add_invoice(legacy, {"invoice_no": "INV-LEGACY-1", "amount": 1800},
                                  buyer)                                  # still invoices
        assert ok, "a legacy order must still be invoiceable: %s" % msg
        assert svc.three_way_match(legacy)["status"] == "matched", \
            svc.three_way_match(legacy)["flags"]
        ok, msg = svc.add_payment(legacy, {"amount": 1800, "method": "transfer"}, buyer)
        assert ok, "a legacy order must still be payable: %s" % msg      # still pays
        assert q("SELECT payment_status FROM pr_requests WHERE id=?",
                 legacy)[0]["payment_status"] == "paid", "legacy payment did not settle"
        # and the un-migrated row is left exactly as it was
        assert not q("SELECT id FROM pr_purchase_orders WHERE pr_id=?", legacy), \
            "reading a legacy order must not write history into the new table"

        # =================================================================
        # 4. SLOPPY SPELLINGS — a trailing space or a capital must not lose a
        #    line off every document, nor split one supplier into two orders.
        #    Only the two web forms trim the field: the maintenance auto-reorder
        #    bridge and the ERP item-master import pass it through raw.
        # =================================================================
        sloppy, _ = make("Belts and seals", [
            ("Alphabelt", 10, 100, "Alphatex "),   # 1,000 — trailing space
            ("Alphaseal", 5, 100, "ALPHATEX"),     # 500   — different case
            ("Betaseal", 20, 100, "Betatrim"),     # 2,000
        ])
        approve_and_issue(sloppy)
        sp = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", sloppy)
        assert len(sp) == 2, \
            "one spelling of one supplier = one order, got %r" % [p["vendor"] for p in sp]
        assert [p["vendor"] for p in sp] == ["Alphatex", "Betatrim"], \
            "the first spelling seen is the one printed: %r" % [p["vendor"] for p in sp]
        assert [p["grand"] for p in sp] == [1500, 2000], [p["grand"] for p in sp]
        b3 = svc.get_pr(sloppy)
        seen = {}
        for p in b3["pos"]:
            text = _pdf_text(pdfgen.po_pdf(b3, p))
            on_it = [w for w in ("Alphabelt", "Alphaseal", "Betaseal") if w in text]
            assert on_it, "%s (%s) printed a total with ZERO order lines" % (
                p["po_no"], p["vendor"])
            for w in on_it:
                assert w not in seen, "%r is on two documents (%s and %s)" % (
                    w, seen.get(w), p["po_no"])
                seen[w] = p["po_no"]
        assert len(seen) == 3, "a line vanished off every document: %r" % seen

        # =================================================================
        # 5. THE SUPPLIER PURCHASING CHOSE, not the one the requester guessed.
        #    create_pr() seeds every line from the header; Purchasing corrects
        #    the header at the pricing gate. The order, its PDF and the EMAIL all
        #    resolve the supplier from the LINE, so the lines must follow.
        # =================================================================
        conn = get_db()
        for nm, em in (("SuggestedCo", "suggested@example.com"),
                       ("RealSupplier", "real@example.com"),
                       ("OtherPick", "other@example.com")):
            conn.execute("INSERT INTO proc_vendors (name, email, is_active, created_at) "
                         "VALUES (?,?,1,?)", (nm, em, "2026-01-01"))
        conn.commit()
        conn.close()
        pick_id, _ = svc.create_pr(
            {"title": "Compressor spares", "department": "Maintenance",
             "vendor": "SuggestedCo", "tax_rate": 0},
            [{"item": "Pumpbearing", "qty": 10, "unit_price": 0},     # inherits the header
             {"item": "Pumpgasket", "qty": 1, "unit_price": 0, "vendor": "OtherPick"}],
            {"username": "tech", "id": 9}, priced=False)
        pick_ids = [r["id"] for r in q(
            "SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id", pick_id)]
        assert [r["vendor"] for r in q(
            "SELECT vendor FROM pr_items WHERE pr_id=? ORDER BY seq, id", pick_id)] \
            == ["SuggestedCo", "OtherPick"], "create_pr seeds the line from the header"
        ok, msg = svc.price_pr(pick_id, {pick_ids[0]: 100, pick_ids[1]: 50},
                               {"tax_rate": 0, "vendor": "RealSupplier"}, buyer)
        assert ok, "pricing failed: %s" % msg
        assert [r["vendor"] for r in q(
            "SELECT vendor FROM pr_items WHERE pr_id=? ORDER BY seq, id", pick_id)] \
            == ["RealSupplier", "OtherPick"], \
            "the line must follow the vendor Purchasing chose, and only that line"
        approve_and_issue(pick_id)
        pk = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", pick_id)
        assert [p["vendor"] for p in pk] == ["RealSupplier", "OtherPick"], \
            "the ORDER names the supplier Purchasing chose: %r" % [p["vendor"] for p in pk]
        b4 = svc.get_pr(pick_id)
        t4 = _pdf_text(pdfgen.po_pdf(b4, b4["pos"][0]))
        assert "RealSupplier" in t4 and "SuggestedCo" not in t4, \
            "the PDF is addressed to the chosen supplier"
        svc.email_po_to_vendor(pick_id, buyer)
        trail = " ".join(r["detail"] or "" for r in q(
            "SELECT detail FROM pr_events WHERE pr_id=? AND action='po_emailed'", pick_id))
        assert "real@example.com" in trail and "suggested@example.com" not in trail, \
            "the PO was emailed to a supplier who was NOT selected: %s" % trail

        # =================================================================
        # 6. The orders must SUM to the request that authorised them: per-vendor
        #    tax rounded independently drifted a piastre above pr_amounts().
        # =================================================================
        cents, _ = make("Gaskets", [
            ("Alphagasket", 1, 100.05, "Alphatex"),
            ("Betagasket", 1, 100.05, "Betatrim"),
            ("Gammagasket", 1, 100.05, "Gammaknit"),
        ], tax=14)
        approve_and_issue(cents)
        cp = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", cents)
        want = svc.pr_amounts(q("SELECT * FROM pr_requests WHERE id=?", cents)[0])
        assert round(sum(p["grand"] for p in cp), 2) == want["grand"],             "the orders total %r where the request is %r" % (
                round(sum(p["grand"] for p in cp), 2), want["grand"])
        for p in cp:
            assert abs(p["grand"] - (p["subtotal"] + p["tax"])) < 0.005, p

        print("PASS: one PO per vendor, no competitor lines on any document, "
              "single-vendor and pre-existing orders unchanged")
        return True


if __name__ == "__main__":
    run()
