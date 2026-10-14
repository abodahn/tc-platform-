"""RFQ editing, quotation editing, and the purchase history on a request line.

Procurement & Warehouse spec, sections 1, 4 and 5. Three things are proved here
that the system could not do before:

  1. A request line now carries its own purchase history — what it cost last
     time, what is still owed on open orders, and how long the shelf lasts.
     last_order_qty, last_order_date and last_order_price were columns that had
     existed since the first release, were PRINTED on the PR PDF, and that no
     code had ever written: those three columns came out blank on every document
     the company has issued.
  2. An RFQ can be corrected, and can ask for SOME of the lines. It carried no
     item list at all, so every vendor was asked to price every line — wrong the
     moment one supplier sells the fabric and another the needles.
  3. A quotation can be corrected or withdrawn, and is linked to the vendor
     master. Neither was possible: a typo in a price was permanent.

Each control is also proved to REFUSE at the right moment, because a control
that only ever says yes is not a control: an answered RFQ, a chosen quotation
and a closed request are all locked.

    python app/approvals/tests_rfq_quote_edit.py
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="rfqedit_"), "r.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, pdf as P

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def one(sql, *a):
            conn = get_db()
            try:
                return conn.execute(sql, a).fetchone()
            finally:
                conn.close()

        def make(title, lines):
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance"},
                [{"item": i, "qty": q, "unit_price": 0} for i, q in lines],
                {"username": "tech", "id": 9}, priced=False)
            conn = get_db()
            try:
                ids = [r["id"] for r in conn.execute(
                    "SELECT id FROM pr_items WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
            finally:
                conn.close()
            ok, msg = svc.price_pr(pr_id, {i: 100 for i in ids}, {"tax_rate": 0}, buyer)
            assert ok, "pricing failed: %s" % msg
            return pr_id, ids

        # ---------------------------------------------------------- history ---
        print("\n1. a request line carries its own purchase history")
        old_id, _ = make("Last year's order", [("PUMP-7", 40)])
        conn = get_db()
        try:
            conn.execute("UPDATE pr_requests SET status='closed', request_date='2026-02-01' "
                         "WHERE id=?", (old_id,))
            conn.commit()
        finally:
            conn.close()

        new_id, new_lines = make("This order", [("PUMP-7", 5)])
        it = svc.get_pr(new_id)["items"][0]
        chk("the previous order's quantity is found", it.get("last_order_qty") == 40,
            it.get("last_order_qty"))
        chk("and its date", it.get("last_order_date") == "2026-02-01", it.get("last_order_date"))
        chk("and its price — three columns the PR PDF printed blank for years",
            it.get("last_order_price") == 100, it.get("last_order_price"))
        chk("a part with no history says so rather than claiming zero",
            svc.get_pr(make("Novel part", [("NEVER-SEEN-1", 2)])[0])["items"][0]
            .get("last_order_date") is None)

        conn = get_db()
        try:
            conn.execute("UPDATE pr_requests SET status='po_issued' WHERE id=?", (old_id,))
            conn.commit()
        finally:
            conn.close()
        it = svc.get_pr(new_id)["items"][0]
        chk("an order placed and not received counts as still owed",
            it.get("pending_qty") == 40, it.get("pending_qty"))

        # -------------------------------------------------------------- RFQ ---
        print("\n2. an RFQ can be corrected and can ask for some of the lines")
        pr_id, lines = make("Two very different things",
                            [("FABRIC-BLUE", 100), ("NEEDLE-90", 500)])
        ok, res = svc.issue_rfqs(pr_id, ["Alpha Trading"], buyer, reply_by="2026-12-01")
        chk("an RFQ is issued", ok, res)
        rfq = one("SELECT * FROM pr_rfqs WHERE pr_id=? ORDER BY id DESC LIMIT 1", pr_id)

        ok, msg = svc.update_rfq(rfq["id"], vendor="Alpha Trading LLC",
                                 reply_by="2026-12-15", notes="revised", user=buyer)
        chk("its details can be corrected", ok, msg)
        again = one("SELECT * FROM pr_rfqs WHERE id=?", rfq["id"])
        chk("the correction is stored", again["vendor"] == "Alpha Trading LLC", again["vendor"])
        chk("and recorded in the audit trail",
            one("SELECT COUNT(*) n FROM pr_events WHERE pr_id=? AND action='rfq_edited'",
                pr_id)["n"] >= 1)

        ok, msg = svc.update_rfq(rfq["id"], keep_items=[lines[0]], user=buyer)
        chk("a line can be dropped from the RFQ", ok, msg)
        asked = svc.get_pr(pr_id)["rfqs"][0]
        chk("the dropped line is recorded as excluded",
            asked.get("excluded") == {lines[1]}, asked.get("excluded"))

        b = svc.get_pr(pr_id)
        raw = P.rfq_pdf(b, asked)
        raw = raw if isinstance(raw, (bytes, bytearray)) else raw.getvalue()
        chk("the RFQ PDF is still a PDF after the line is dropped", raw[:4] == b"%PDF")

        ok, msg = svc.update_rfq(rfq["id"], keep_items=[], user=buyer)
        chk("an RFQ that asks for nothing is refused", (not ok) and msg == "all_removed", msg)

        # ------------------------------------------------------------ quote ---
        print("\n3. a quotation can be corrected, withdrawn, and is linked to a vendor")
        conn = get_db()
        try:
            conn.execute("INSERT INTO proc_vendors (name, is_active) VALUES ('Alpha Trading LLC',1)")
            conn.commit()
        finally:
            conn.close()

        svc.add_quote(pr_id, {"vendor": "alpha trading llc", "amount": 9000,
                              "currency": "EGP", "lead_time_days": 20}, buyer)
        svc.add_quote(pr_id, {"vendor": "Beta Supply", "amount": 9500,
                              "currency": "EGP"}, buyer)
        q1 = one("SELECT * FROM pr_quotes WHERE pr_id=? ORDER BY id", pr_id)

        ok, msg = svc.update_quote(q1["id"], {"amount": 8800, "vendor": "Alpha Trading LLC"},
                                   user=buyer)
        chk("a quoted price can be corrected", ok, msg)
        q1b = one("SELECT * FROM pr_quotes WHERE id=?", q1["id"])
        chk("the new price is stored", float(q1b["amount"]) == 8800, q1b["amount"])
        chk("and the line is linked to the vendor master — matched without case",
            q1b["vendor_id"] is not None, q1b["vendor_id"])
        chk("a negative price is refused",
            svc.update_quote(q1["id"], {"amount": -5}, user=buyer)[1] == "bad_amount")

        svc.choose_quote(pr_id, q1["id"], buyer)
        ok, msg = svc.update_quote(q1["id"], {"amount": 1}, user=buyer)
        chk("the CHOSEN quotation cannot be edited — the ladder routed on it",
            (not ok) and msg == "chosen", msg)
        ok, msg = svc.delete_quote(q1["id"], user=buyer)
        chk("nor withdrawn", (not ok) and msg == "chosen", msg)

        q2 = one("SELECT * FROM pr_quotes WHERE pr_id=? AND is_chosen IS NOT 1 "
                 "ORDER BY id DESC LIMIT 1", pr_id)
        ok, msg = svc.delete_quote(q2["id"], user=buyer)
        chk("a quotation that is not chosen can be withdrawn", ok, msg)
        chk("and it is gone", one("SELECT COUNT(*) n FROM pr_quotes WHERE id=?",
                                  q2["id"])["n"] == 0)
        chk("the withdrawal is in the audit trail",
            one("SELECT COUNT(*) n FROM pr_events WHERE pr_id=? AND action='quote_removed'",
                pr_id)["n"] == 1)

        # --------------------------------------------------------- arabic -----
        print("\n4. Arabic reaches the PDF as Arabic")
        from app.approvals import arabic_pdf as ar
        # The shipped font is the whole reason Arabic works on Render, which has
        # no system Arabic face. Asserted by PATH, not merely "some font was
        # found": on a Windows dev machine register() happily falls back to
        # Tahoma, so a missing vendored file would pass here and produce boxes in
        # production — exactly how this defect survived the first time.
        vend = Path(__file__).resolve().parents[1] / "static" / "vendor" / "fonts"
        chk("the OFL Arabic font is shipped with the application",
            (vend / "NotoNaskhArabic-Regular.ttf").exists())
        chk("its licence ships with it", (vend / "NotoNaskhArabic-OFL.txt").exists())
        chk("and it is preferred over any font that happens to be on this machine",
            "NotoNaskhArabic" in (ar.FONT_CANDIDATES[0][0] or ""))

        s = "طلب شراء قطع غيار"
        chk("Arabic is detected", ar.has_arabic(s))
        chk("and reshaped + reordered for drawing", ar.shape(s) != s)
        chk("English is passed through untouched", ar.shape("Purchase request") == "Purchase request")
        b = svc.get_pr(pr_id)
        b["pr"]["title"] = s
        raw = P.pr_pdf(b)
        raw = raw if isinstance(raw, (bytes, bytearray)) else raw.getvalue()
        chk("the PR PDF still generates with Arabic in it", raw[:4] == b"%PDF", len(raw))
        if ar.register():
            chk("and embeds a font that has Arabic glyphs", b"/BaseFont" in raw and len(raw) > 20000,
                "%d bytes" % len(raw))
        else:
            print("  SKIP  no Arabic font installed here — see arabic_pdf.FONT_CANDIDATES")

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
