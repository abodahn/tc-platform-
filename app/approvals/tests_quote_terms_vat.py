"""Payment terms on RFQs and quotations, and the 14% VAT question on a quote.

    python -m app.approvals.tests_quote_terms_vat

Proves:
  1. payment terms are REQUIRED — a blank is refused by the service and by the
     route (with a translated message), on issuing an RFQ, editing one, adding
     a quotation and editing one; the RFQ PDF prints the terms asked for;
  2. VAT yes -> after-VAT = 1.14 x amount; VAT no -> equal to the amount;
  3. quotes are RANKED on the after-VAT figure: 100,000 with no VAT beats
     90,000 + VAT (102,600);
  4. old rows (no terms, no VAT answer) and a database without the new columns
     still read and render; the migration is idempotent.
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="qtv_"), "q.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        import flask
        from app.db import get_db
        from app.approvals import services as svc, pdf as P, schema
        from app.approvals.messages import translate

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def one(sql, *a):
            conn = get_db()
            try:
                return conn.execute(sql, a).fetchone()
            finally:
                conn.close()

        def run_sql(sql, *a):
            conn = get_db()
            try:
                conn.execute(sql, a)
                conn.commit()
            finally:
                conn.close()

        pr_id, _ = svc.create_pr({"title": "Quote terms", "department": "Maintenance"},
                                 [{"item": "BELT-A42", "qty": 10, "unit_price": 0}],
                                 {"username": "tech", "id": 9}, priced=False)

        # ------------------------------------------------------------ RFQ ----
        print("\n1. payment terms are required on an RFQ")
        ok, why = svc.issue_rfqs(pr_id, ["Alpha"], buyer, payment_terms="   ")
        chk("a blank is refused by the service", (not ok) and why == "terms_required", why)
        chk("and nothing was written", one("SELECT COUNT(*) n FROM pr_rfqs WHERE pr_id=?",
                                           pr_id)["n"] == 0)
        ok, rows = svc.issue_rfqs(pr_id, ["Alpha"], buyer, payment_terms="60 days")
        chk("with terms it is issued", ok, rows)
        rfq = one("SELECT * FROM pr_rfqs WHERE pr_id=?", pr_id)
        chk("and the terms are stored", rfq["payment_terms"] == "60 days", rfq["payment_terms"])
        chk("an edit cannot blank them",
            svc.update_rfq(rfq["id"], payment_terms=" ", user=buyer) == (False, "terms_required"))
        ok, msg = svc.update_rfq(rfq["id"], payment_terms="90 days", user=buyer)
        chk("an edit can change them", ok and one("SELECT payment_terms t FROM pr_rfqs WHERE id=?",
                                                 rfq["id"])["t"] == "90 days", msg)
        b = svc.get_pr(pr_id)
        raw = P.rfq_pdf(b, b["rfqs"][0])
        from app.approvals.tests_rfq import _pdf_strings
        chk("the RFQ PDF prints the terms asked for", "90 days" in _pdf_strings(raw))

        # ---------------------------------------------------------- quote ----
        print("\n2. payment terms are required on a quotation")
        ok, why = svc.add_quote(pr_id, {"vendor": "Alpha", "amount": 100}, buyer)
        chk("a missing value is refused by the service", (not ok) and why == "terms_required", why)
        ok, why = svc.add_quote(pr_id, {"vendor": "Alpha", "amount": 100,
                                        "payment_terms": "  "}, buyer)
        chk("so is a blank", (not ok) and why == "terms_required", why)

        print("\n3. VAT 14%: the amount is BEFORE VAT")
        ok, _ = svc.add_quote(pr_id, {"vendor": "Alpha", "amount": 90000,
                                      "payment_terms": "30 days", "vat_applies": "1"}, buyer)
        ok2, _ = svc.add_quote(pr_id, {"vendor": "Beta", "amount": 100000,
                                       "payment_terms": "Cash", "vat_applies": "0"}, buyer)
        chk("both quotes are recorded", ok and ok2)
        qs = {q["vendor"]: q for q in svc.get_pr(pr_id)["quotes"]}
        chk("VAT yes -> after VAT = 1.14 x", qs["Alpha"]["after_vat"] == 102600.0,
            qs["Alpha"]["after_vat"])
        chk("VAT no -> after VAT = the amount", qs["Beta"]["after_vat"] == 100000.0,
            qs["Beta"]["after_vat"])
        chk("the rate is stored with the row", abs(qs["Alpha"]["vat_rate"] - 0.14) < 1e-9)
        chk("the supplier's terms are stored", qs["Beta"]["payment_terms"] == "Cash")

        print("\n4. quotes are ranked on the after-VAT figure")
        quotes = svc.get_pr(pr_id)["quotes"]
        chk("100,000 with no VAT is listed first", quotes[0]["vendor"] == "Beta",
            [q["vendor"] for q in quotes])
        cmp = svc.quote_comparison(quotes)
        chk("and is the lowest", cmp["lowest"] == 100000.0, cmp)
        chk("the saving is on the after-VAT figures", cmp["saving"] == 2600.0, cmp)

        qa = qs["Alpha"]["id"]
        chk("an edit cannot blank the supplier's terms",
            svc.update_quote(qa, {"payment_terms": ""}, user=buyer) == (False, "terms_required"))
        ok, msg = svc.update_quote(qa, {"vat_applies": "0"}, user=buyer)
        chk("VAT can be switched off on edit", ok and svc.get_pr(pr_id)["quotes"][0]["vendor"]
            == "Alpha", msg)

        # ----------------------------------------------------- old rows -------
        print("\n5. old rows and old databases")
        run_sql("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, created_at) "
                "VALUES (?, 'Legacy', 95000, 'EGP', '2026-01-01')", pr_id)
        old = [q for q in svc.get_pr(pr_id)["quotes"] if q["vendor"] == "Legacy"][0]
        chk("a row with no VAT answer ranks on its bare amount", old["after_vat"] == 95000.0)

        # HTTP: a signed-in super admin, Arabic, to prove the refusal is translated
        run_sql("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                "is_active, lang_pref, created_at) VALUES "
                "('qtv_admin','x','qtv_admin','super_admin',1,'ar','2026-01-01')")
        uid = one("SELECT id FROM users WHERE username='qtv_admin'")["id"]
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
        flask.g.pop("user", None)

        def flashes():
            with cli.session_transaction() as s:
                return [m for _c, m in s.pop("_flashes", [])]

        r = cli.get("/procurement/pr/%d" % pr_id)
        html = r.get_data(as_text=True)
        chk("the request page renders with old and new quotes", r.status_code == 200, r.status_code)
        chk("and the supplier's terms", "Cash" in html)
        chk("the payment-term suggestions are offered", 'id="payTerms"' in html)

        n0 = one("SELECT COUNT(*) n FROM pr_quotes WHERE pr_id=?", pr_id)["n"]
        r = cli.post("/procurement/pr/%d/quote" % pr_id,
                     data={"_csrf": "tok", "vendor": "Gamma", "amount": "5000"},
                     content_type="multipart/form-data")
        f = flashes()
        chk("the route refuses a quote with no terms",
            r.status_code == 302 and one("SELECT COUNT(*) n FROM pr_quotes WHERE pr_id=?",
                                         pr_id)["n"] == n0)
        want = translate("Enter the payment terms the supplier is offering.", "ar")
        chk("with a translated message", want in f and want != "Enter the payment terms "
            "the supplier is offering.", f)
        r = cli.post("/procurement/pr/%d/quote" % pr_id,
                     data={"_csrf": "tok", "vendor": "Gamma", "amount": "5000",
                           "payment_terms": "30 days", "vat_applies": "1"},
                     content_type="multipart/form-data")
        flashes()
        g = one("SELECT * FROM pr_quotes WHERE pr_id=? AND vendor='Gamma'", pr_id)
        chk("and accepts it with terms, VAT yes",
            g is not None and g["vat_applies"] == 1 and g["payment_terms"] == "30 days")
        html = cli.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        chk("the page shows its VAT and after-VAT figures", "5,700" in html and "700" in html)

        r = cli.post("/procurement/pr/%d/rfq" % pr_id,
                     data={"_csrf": "tok", "vendors": "Delta"})
        f = flashes()
        chk("the route refuses an RFQ with no terms",
            one("SELECT COUNT(*) n FROM pr_rfqs WHERE vendor='Delta'")["n"] == 0)
        chk("with a translated message",
            translate("Enter the payment terms you are asking the supplier for.", "ar") in f, f)
        r = cli.post("/procurement/pr/%d/quote/%d/edit" % (pr_id, g["id"]),
                     data={"_csrf": "tok", "payment_terms": " "})
        flashes()
        chk("the edit route refuses blank terms",
            one("SELECT payment_terms t FROM pr_quotes WHERE id=?", g["id"])["t"] == "30 days")

        # A database whose pr_quotes predates the new columns still reads.
        for col in ("vat_rate", "vat_applies", "payment_terms"):
            run_sql("ALTER TABLE pr_quotes DROP COLUMN %s" % col)
        b = svc.get_pr(pr_id)
        chk("get_pr still reads quotes without the new columns",
            b and len(b["quotes"]) == n0 + 1 and all("after_vat" in q for q in b["quotes"]))
        r = cli.get("/procurement/pr/%d" % pr_id)
        chk("and the page still renders", r.status_code == 200, r.status_code)

        print("\n6. the migration is idempotent")
        for _ in range(2):
            conn = get_db()
            try:
                schema.create_and_seed(conn)
            finally:
                conn.close()
        conn = get_db()
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(pr_quotes)").fetchall()]
            rcols = [r[1] for r in conn.execute("PRAGMA table_info(pr_rfqs)").fetchall()]
        finally:
            conn.close()
        chk("the columns are back, once each",
            all(cols.count(c) == 1 for c in ("payment_terms", "vat_applies", "vat_rate"))
            and rcols.count("payment_terms") == 1, cols)
        old = [q for q in svc.get_pr(pr_id)["quotes"] if q["vendor"] == "Legacy"][0]
        chk("old rows read as unknown VAT", old.get("vat_applies") is None
            and old["after_vat"] == 95000.0)

    print("\nRESULT: " + ("ALL GREEN" if OK[0] else "FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
