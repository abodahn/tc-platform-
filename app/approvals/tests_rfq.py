"""Request for Quotation — the PO's document with the money taken out.

An RFQ is what goes out BEFORE a price exists: same goods, same quantities, no
value at all. One per vendor, because you ask several suppliers and compare what
comes back, and it is the document that PRODUCES the competitive quotations the
§4.3 sourcing bands require.

What is proved here, through the real flow:
  1. one RFQ per chosen vendor, each with its own controlled document number;
  2. each PDF carries only that supplier's lines;
  3. the UNIT PRICE and TOTAL columns are EMPTY — no money-shaped string is
     drawn anywhere on the sheet OTHER THAN the quantities, which are the one
     thing on the form that legitimately carries decimals — a 2.5 kg line
     prints "2.50" — and "0.00" appears nowhere;
  4. issuing one changes NOTHING about the request: status, total, vendor,
     pricing state and every ladder rung are byte-identical afterwards;
  5. a returned quotation is recorded against the RFQ it answers;
  6. a database with no pr_rfqs table at all still opens and still quotes.

    python -m app.approvals.tests_rfq
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "rfq.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _pdf_strings(blob):
    """Every string the PDF actually DRAWS, in order — the `(text) Tj` operands
    of the decoded content streams. Coordinates and colour operators are not
    text, so asserting on this list asserts on what a supplier reads."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", blob, re.S):
        data = m.group(1)
        for step in (lambda x: base64.a85decode(x, adobe=True), zlib.decompress):
            try:
                data = step(data)
            except Exception:                       # noqa: BLE001 — image / raw stream
                pass
        for t in re.finditer(rb"\((.*?)\)\s*Tj", data, re.S):
            out.append(t.group(1).decode("latin-1"))
    return out


MONEY = re.compile(r"\d[\d,]*\.\d{2}")


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, pdf as pdfgen

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def q(sql, *args):
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

        # ---------------------------------------------------------------
        # A three-supplier request, priced (so there IS a value to leak)
        # ---------------------------------------------------------------
        A, B, G = "Alphatex", "Betatrim", "Gammaknit"
        # The last line is deliberately FRACTIONAL: a 2.5 unit line prints
        # "2.50" in the QTY column, which is money-SHAPED and entirely correct.
        # This sheet is about the two PRICE columns, so the quantities are
        # excluded from the scan below rather than the scan being quietly true
        # only for as long as nobody writes a fractional line.
        lines = [("Alphabearing", 12, 1234.56, A),
                 ("Betaseal", 7, 999.99, B),
                 ("Gammavalve", 2.5, 45.5, G)]
        pr_id, _ = svc.create_pr(
            {"title": "Spare parts for the compressor overhaul",
             "department": "Maintenance", "vendor": A, "tax_rate": 0},
            [{"item": i, "qty": qty, "unit_price": 0, "vendor": v, "unit": "Pcs"}
             for i, qty, _p, v in lines],
            {"username": "tech", "id": 9}, priced=False)
        ids = [r["id"] for r in q("SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id",
                                  pr_id)]
        ok, msg = svc.price_pr(pr_id, {i: l[2] for i, l in zip(ids, lines)},
                               {"tax_rate": 0}, buyer)
        assert ok, "pricing failed: %s" % msg

        before = q("SELECT * FROM pr_requests WHERE id=?", pr_id)[0]
        steps_before = q("SELECT seq, stage, status, origin, approver_role FROM pr_steps "
                         "WHERE pr_id=? ORDER BY seq, id", pr_id)
        assert before["total"] > 0 and steps_before, before

        # ---------------------------------------------------------------
        # 1. One RFQ per vendor, each a controlled document
        # ---------------------------------------------------------------
        ok, rows = svc.issue_rfqs(pr_id, [A, B, G, " ", A.lower()], buyer,
                                  reply_by="2026-09-01")
        assert ok, "issue_rfqs refused: %s" % rows
        assert len(rows) == 3, "one RFQ per distinct vendor, got %r" % rows
        rfqs = q("SELECT * FROM pr_rfqs WHERE pr_id=? ORDER BY id", pr_id)
        assert [r["vendor"] for r in rfqs] == [A, B, G], rfqs
        nums = [r["rfq_no"] for r in rfqs]
        assert len(set(nums)) == 3, "RFQ numbers must be distinct: %r" % nums
        assert all(re.fullmatch(r"RFQ-\d{4}-\d{6}", n) for n in nums), nums
        assert all(r["status"] == "sent" and r["reply_by"] == "2026-09-01" for r in rfqs), rfqs

        ok, why = svc.issue_rfqs(pr_id, ["", "  "], buyer)
        assert (not ok) and why == "no_vendors", (ok, why)

        # ---------------------------------------------------------------
        # 2. NOTHING about the request moved — it asked for a price, it did
        #    not commit to one.
        # ---------------------------------------------------------------
        after = q("SELECT * FROM pr_requests WHERE id=?", pr_id)[0]
        assert after == before, (
            "issuing an RFQ altered the request: %r" %
            {k: (before[k], after[k]) for k in before if before[k] != after[k]})
        assert q("SELECT seq, stage, status, origin, approver_role FROM pr_steps "
                 "WHERE pr_id=? ORDER BY seq, id", pr_id) == steps_before, \
            "issuing an RFQ moved the approval ladder"

        # ---------------------------------------------------------------
        # 3. Each PDF: own lines, own number, and NO money anywhere
        # ---------------------------------------------------------------
        bundle = svc.get_pr(pr_id)
        assert [r["rfq_no"] for r in bundle["rfqs"]] == nums, bundle["rfqs"]
        mine = {A: "Alphabearing", B: "Betaseal", G: "Gammavalve"}
        prices = ("1,234.56", "999.99", "45.50", "14,814.72")
        qtys = {pdfgen._fmt(qty, 0) for _i, qty, _p, _v in lines}   # {"12","7","2.50"}

        def values(blob):
            """Every money-shaped string the sheet DRAWS that is not a quantity."""
            return [t for t in _pdf_strings(blob)
                    if MONEY.search(t) and t.strip() not in qtys]
        for r in bundle["rfqs"]:
            blob = pdfgen.rfq_pdf(bundle, r)
            assert blob[:5] == b"%PDF-" and blob.rstrip().endswith(b"%%EOF"), \
                "%s is not a valid PDF file" % r["rfq_no"]
            drawn = _pdf_strings(blob)
            text = "\n".join(drawn)
            assert "REQUEST FOR QUOTATION" in text, "%s lost its title" % r["rfq_no"]
            assert r["rfq_no"] in text and r["vendor"] in text, r["rfq_no"]
            assert "T&C-PUF-03" in text, "the controlled-form code must print"
            assert "2026-09-01" in text, "the reply-by date must print"
            assert "Signature & company stamp" in text, "no place for the supplier to sign"
            assert mine[r["vendor"]] in text, "%s lost its own line" % r["rfq_no"]
            for other, word in mine.items():
                if other != r["vendor"]:
                    assert word not in text, "%s leaks %s's line" % (r["rfq_no"], other)
            # the whole point: the price columns are EMPTY BOXES.
            assert not values(blob), "%s prints a value: %r" % (
                r["rfq_no"], values(blob))
            assert "0.00" not in text, "%s prints 0.00 where the supplier writes" % r["rfq_no"]
            for p in prices:
                assert p not in text, "%s leaks the internal price %s" % (r["rfq_no"], p)
            for absent in ("GRAND TOTAL", "Amount in words", "Subtotal"):
                assert absent not in text, "%s commits money it must not (%s)" % (
                    r["rfq_no"], absent)
            # the real quantities DO print
            assert qtys & set(drawn), "quantities missing from %s" % r["rfq_no"]

        # An empty column and a ruled BOX read identically in extracted text, so
        # count the boxes the canvas actually draws: two per line, and none of
        # them carrying a value.
        from reportlab.pdfgen import canvas as _canvas
        boxes, _orig = [], _canvas.Canvas.roundRect

        def _count(self, x, y, bw, bh, r, **kw):
            if kw.get("stroke") == 1 and kw.get("fill") == 1:
                boxes.append((bw, bh))
            return _orig(self, x, y, bw, bh, r, **kw)

        _canvas.Canvas.roundRect = _count
        try:
            pdfgen.rfq_pdf(bundle, bundle["rfqs"][0])       # Alphatex: 1 line
        finally:
            _canvas.Canvas.roundRect = _orig
        assert len(boxes) == 2, ("UNIT PRICE and TOTAL must be ruled boxes to write "
                                 "in, one pair per line: %r" % boxes)
        assert all(bw > 40 and bh > 15 for bw, bh in boxes), "the boxes are unusable: %r" % boxes

        # a supplier the request never named is asked to quote the WHOLE request
        ok, extra = svc.issue_rfqs(pr_id, ["Deltaweave"], buyer)
        assert ok, extra
        b2 = svc.get_pr(pr_id)
        new = [r for r in b2["rfqs"] if r["vendor"] == "Deltaweave"][0]
        t = "\n".join(_pdf_strings(pdfgen.rfq_pdf(b2, new)))
        for word in mine.values():
            assert word in t, "a new supplier is asked to quote everything (%s missing)" % word
        whole = pdfgen.rfq_pdf(b2, new)
        assert not values(whole), "the whole-request RFQ prints a value: %r" % values(whole)
        assert qtys <= set(_pdf_strings(whole)), "the whole-request RFQ lost a quantity"

        # ---------------------------------------------------------------
        # 4. The answer comes back against the document that asked for it
        # ---------------------------------------------------------------
        target = rfqs[1]                                   # Betatrim's RFQ
        ok, msg = svc.add_quote(pr_id, {"vendor": B, "amount": 6800,
                                        "rfq_id": target["id"]}, buyer)
        assert ok, msg
        quote = q("SELECT vendor, amount, rfq_id FROM pr_quotes WHERE pr_id=? "
                  "ORDER BY id DESC", pr_id)[0]
        assert quote["rfq_id"] == target["id"], quote
        assert q("SELECT status FROM pr_rfqs WHERE id=?",
                 target["id"])[0]["status"] == "answered", "the RFQ was never marked answered"
        assert q("SELECT status FROM pr_rfqs WHERE id=?",
                 rfqs[0]["id"])[0]["status"] == "sent", "an unanswered RFQ must stay 'sent'"
        # a quote that answers nothing is still a quote, and an id from another
        # request can never attach itself here
        ok, _ = svc.add_quote(pr_id, {"vendor": "Walk-in", "amount": 7100}, buyer)
        assert ok
        ok, _ = svc.add_quote(pr_id, {"vendor": "Ghost", "amount": 1, "rfq_id": 99999}, buyer)
        assert ok
        assert q("SELECT rfq_id FROM pr_quotes WHERE pr_id=? AND vendor='Ghost'",
                 pr_id)[0]["rfq_id"] is None, "a stray RFQ id must not link"
        # ...and the request STILL has not moved
        assert q("SELECT * FROM pr_requests WHERE id=?", pr_id)[0]["total"] == before["total"]
        assert q("SELECT * FROM pr_requests WHERE id=?", pr_id)[0]["status"] == before["status"]

        # ---------------------------------------------------------------
        # 5. A database that never got the RFQ table still works
        # ---------------------------------------------------------------
        conn = get_db()
        conn.execute("DROP TABLE pr_rfqs")
        conn.commit()
        conn.close()
        legacy = svc.get_pr(pr_id)
        assert legacy and legacy["rfqs"] == [], "the request page must not need the table"
        assert legacy["quotes"], "quotes must still read"
        ok, msg = svc.add_quote(pr_id, {"vendor": "Old Ways", "amount": 500}, buyer)
        assert ok, "quoting must survive a missing RFQ table: %s" % msg

        print("PASS: one RFQ per vendor, empty price boxes, no value printed, "
              "request untouched, answers linked to the document that asked")
        return True


if __name__ == "__main__":
    run()
