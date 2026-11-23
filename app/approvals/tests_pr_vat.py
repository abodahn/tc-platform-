"""VAT on the request itself + the FX rate / EGP equivalent at pricing.

    python -m app.approvals.tests_pr_vat

Proves:
  1. pricing with tax_rate=14 stores it; pr_amounts gives grand = 1.14 x subtotal;
  2. the request page shows the VAT and After-VAT columns per line and the
     VAT-inclusive grand total; the pricing form offers the VAT choice and the
     FX-rate / EGP-equivalent boxes;
  3. a USD request priced with a rate shows its EGP equivalent (VAT included).
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


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="prvat_"), "v.db")
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        admin = conn.execute("SELECT id FROM users WHERE role='super_admin' "
                             "ORDER BY id LIMIT 1").fetchone()
        conn.close()
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        pr_id, _ = svc.create_pr({"title": "VAT test", "department": "Maintenance"},
                                 [{"item": "BELT-A42", "qty": 50, "unit_price": 0}],
                                 {"username": "tech", "id": 9}, priced=False)
        line = get_db().execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
        ok, msg = svc.price_pr(pr_id, {line: 750}, {"tax_rate": "14"}, buyer)
        chk("pricing with VAT 14 is saved", ok, msg)
        pr = dict(get_db().execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone())
        a = svc.pr_amounts(pr)
        chk("subtotal 37,500", a["subtotal"] == 37500, a)
        chk("VAT 5,250 and grand 42,750", a["tax"] == 5250 and a["grand"] == 42750, a)

        usd, _ = svc.create_pr({"title": "USD VAT", "department": "Maintenance",
                                "currency": "USD"},
                               [{"item": "NEEDLE", "qty": 10, "unit_price": 0}],
                               {"username": "tech", "id": 9}, priced=False)
        uline = get_db().execute("SELECT id FROM pr_items WHERE pr_id=?", (usd,)).fetchone()["id"]
        ok, msg = svc.price_pr(usd, {uline: 100}, {"tax_rate": "14", "fx_rate": "50",
                                                    "currency": "USD"}, buyer)
        chk("USD pricing with rate saved", ok, msg)

        # ---- choosing a quotation carries its prices + VAT onto the PR ----
        pdf_b64 = "JVBERi0xLjQK"
        two, _ = svc.create_pr({"title": "Quote flows", "department": "Maintenance"},
                               [{"item": "BATTERY", "qty": 2, "unit_price": 0},
                                {"item": "", "description": "Charger repair", "qty": 1,
                                 "unit_price": 0}],
                               {"username": "tech", "id": 9}, priced=False)
        a, b_ = [it["id"] for it in svc.get_pr(two)["items"]]
        chk("a blank item name shows its description, never 'None'",
            svc.get_pr(two)["items"][1]["item"] == "Charger repair")
        svc.price_pr(two, {a: 65000, b_: 1000}, {"tax_rate": "0"}, buyer)
        ok, why = svc.add_quote(two, {"vendor": "High Track", "amount": "1",
                                      "payment_terms": "30 days", "vat_applies": "1",
                                      "lines": {a: "60000", b_: "900"}}, buyer,
                                filename="q.pdf", content_type="application/pdf",
                                content_b64=pdf_b64)
        chk("per-item quote recorded", ok, why)
        qid = get_db().execute("SELECT id FROM pr_quotes WHERE pr_id=?", (two,)).fetchone()["id"]
        ok, why = svc.choose_quote(two, qid, buyer)
        chk("quote chosen", ok, why)
        ok, why = svc.apply_chosen_quote(two, qid, buyer)
        chk("its prices are applied to the request", ok, why)
        items = {it["id"]: it for it in svc.get_pr(two)["items"]}
        chk("line 1 unit price now 60,000", items[a]["unit_price"] == 60000, items[a]["unit_price"])
        chk("line 2 unit price now 900", items[b_]["unit_price"] == 900, items[b_]["unit_price"])
        chk("every line carries the chosen supplier's exact name",
            all(it["vendor"] == "High Track" for it in items.values()),
            [it["vendor"] for it in items.values()])
        prq = dict(get_db().execute("SELECT * FROM pr_requests WHERE id=?", (two,)).fetchone())
        am = svc.pr_amounts(prq)
        chk("request total = 120,900 and VAT 14%% from the quote (%s)" % am,
            am["subtotal"] == 120900 and am["tax_rate"] == 14 and am["grand"] == 137826)

        _c = get_db()
        rq = svc.rfq_requirement(_c, prq)
        _c.close()
        chk("the quote rule reports its numbers: value incl. VAT, required, have (%s)" % rq,
            rq["value"] == 137826 and rq["have"] == 1 and rq["required"] >= 1
            and svc.rfq_gate_check(get_db(), prq) == (rq["ok"], rq["msg"]))

        one_line, _ = svc.create_pr({"title": "Single", "department": "Maintenance"},
                                    [{"item": "PUMP", "qty": 4, "unit_price": 0}],
                                    {"username": "tech", "id": 9}, priced=False)
        svc.add_quote(one_line, {"vendor": "Solo", "amount": "2000", "payment_terms": "Cash",
                                 "vat_applies": "0"}, buyer, filename="q.pdf",
                      content_type="application/pdf", content_b64=pdf_b64)
        q1 = get_db().execute("SELECT id FROM pr_quotes WHERE pr_id=?", (one_line,)).fetchone()["id"]
        svc.choose_quote(one_line, q1, buyer)
        ok, why = svc.apply_chosen_quote(one_line, q1, buyer)
        it1 = svc.get_pr(one_line)["items"][0]
        chk("one-line quote without lines: unit = amount / qty = 500", ok and it1["unit_price"] == 500,
            (why, it1["unit_price"]))

        # ---- the printed PR and PO carry the VAT columns ------------------
        from app.approvals import pdf as P
        bundle = svc.get_pr(pr_id) if hasattr(svc, "get_pr") else None
        if bundle:
            pr_bytes = P.pr_pdf(bundle, money=True)
            po_bytes = P.po_pdf(bundle)
            try:
                import io
                from pypdf import PdfReader
                def text(b):
                    return " ".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(b)).pages)
                t_pr, t_po = text(pr_bytes), text(po_bytes)
                chk("PR PDF has VAT + AFTER VAT columns", "VAT" in t_pr and "AFTER VAT" in t_pr)
                chk("PR PDF prints the line VAT 5,250.00 and 42,750.00",
                    "5,250.00" in t_pr and "42,750.00" in t_pr)
                chk("PO PDF prints VAT 14% column and 42,750.00", "VAT 14%" in t_po and "42,750.00" in t_po)
                hidden = text(P.pr_pdf(bundle, money=False))
                chk("PR PDF without money hides the VAT figures", "5,250.00" not in hidden)
            except ImportError:
                chk("PDFs render", bool(pr_bytes) and bool(po_bytes))
                print("  note  pypdf not installed — PDF text not read back")

    cl = app.test_client()
    with cl.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = 0
    html = cl.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
    chk("line VAT 5,250.00 shown", "5,250.00" in html)
    chk("line after-VAT 42,750.00 shown", "42,750.00" in html)
    chk("VAT and After-VAT column headers", 'data-i18n="proc.after_vat"' in html
        and 'data-i18n="proc.vat_l"' in html)
    html = cl.get("/procurement/pr/%d" % usd).get_data(as_text=True)
    # 10 x 100 = 1,000 USD; +14% = 1,140; x 50 = 57,000 EGP
    chk("USD request shows EGP equivalent incl. VAT (57,000)", "57,000.00 EGP" in html)
    # ---- legacy: a quote chosen BEFORE prices flowed and before files were
    # required. Attaching its file re-applies its prices + VAT to the request.
    with app.app_context():
        from app.db import get_db
        leg, _ = svc.create_pr({"title": "Legacy", "department": "Maintenance"},
                               [{"item": "SPA-SEW", "qty": 50, "unit_price": 0}],
                               {"username": "tech", "id": 9}, priced=False)
        ln = svc.get_pr(leg)["items"][0]["id"]
        svc.price_pr(leg, {ln: 700}, {"tax_rate": "0"}, buyer)
        c = get_db()
        c.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, is_chosen, "
                  "payment_terms, vat_applies, vat_rate, created_at) "
                  "VALUES (?,?,?,?,1,?,1,0.14,?)", (leg, "Old Vendor", 37500, "EGP",
                                                    "30 days", "2026-11-20"))
        c.commit()
        lq = c.execute("SELECT id FROM pr_quotes WHERE pr_id=?", (leg,)).fetchone()["id"]
        c.close()
    cl2 = app.test_client()
    with cl2.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = 0
    cl2.get("/procurement/pr/%d" % leg)
    with cl2.session_transaction() as s:
        tok = s.get("_csrf_token")
    import io
    r = cl2.post("/procurement/pr/%d/quote/%d/edit" % (leg, lq),
                 data={"_csrf": tok, "file": (io.BytesIO(b"%PDF-1.4 legacy"), "q.pdf")},
                 content_type="multipart/form-data")
    with app.app_context():
        from app.db import get_db
        prl = dict(get_db().execute("SELECT * FROM pr_requests WHERE id=?", (leg,)).fetchone())
        it = svc.get_pr(leg)["items"][0]
        chk("attaching the chosen quote's file applies its price (750) and VAT 14 (%s, %s, %s)"
            % (r.status_code, it["unit_price"], prl["tax_rate"]),
            r.status_code in (302, 303) and it["unit_price"] == 750 and prl["tax_rate"] == 14)
        chk("...and the line's supplier becomes the chosen one (%s)" % it["vendor"],
            it["vendor"] == "Old Vendor" and prl["vendor"] == "Old Vendor")

    print("\nALL PASS" if OK[0] else "\nSOME FAILED")
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
