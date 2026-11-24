"""Vendor quotations PER ITEM: a unit price per request line, not only a total.

    python -m app.approvals.tests_quote_lines

Proves:
  1. line prices are saved and the quote's amount is their sum, computed on the
     server — a posted total is ignored; bad numbers are refused;
  2. a blank line = not quoted -> the quote is partial; VAT applies on the
     computed total;
  3. the per-item matrix marks the lowest unit price per line, ties included;
  4. an old total-only quote still reads and renders, and stays out of the matrix;
  5. after choosing a quote the pricing form is PRE-FILLED with its unit prices,
     while pr_items keeps its prices until Purchasing saves;
  6. a requester (no money yet) and a storekeeper see no unit prices;
  7. editing a quote's lines recomputes its amount; the migration is idempotent
     and a database without the table still reads;
  8. unit prices render at full precision; a quote in another currency is not
     pre-filled; lines changed after quoting are read at the current qty.
"""
import io
import os
import re
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="qln_"), "q.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        import flask
        from app.db import get_db
        from app.approvals import services as svc, schema

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

        for name, role in (("ql_req", "normal_user"), ("ql_store", "storekeeper"),
                           ("ql_admin", "super_admin")):
            run_sql("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                    "is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                    name, "x", name, role)
        req = dict(one("SELECT * FROM users WHERE username='ql_req'"))
        pr_id, _ = svc.create_pr({"title": "Quote lines", "department": "Maintenance"},
                                 [{"item": "QL-BOLT", "qty": 10, "unit_price": 0},
                                  {"item": "QL-NUT", "qty": 4, "unit_price": 0},
                                  {"item": "QL-WASHER", "qty": 2, "unit_price": 0}],
                                 req, priced=False)
        i1, i2, i3 = [it["id"] for it in svc.get_pr(pr_id)["items"]]

        print("\n1. line prices are saved; the server computes the amount")
        ok, why = svc.add_quote(pr_id, {"vendor": "Alpha", "amount": "1",
                                        "payment_terms": "30 days", "vat_applies": "1",
                                        "lines": {i1: "7.31", i2: "20", i3: "100"}}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
        chk("a quote with line prices is recorded", ok, why)
        qa = one("SELECT * FROM pr_quotes WHERE pr_id=? AND vendor='Alpha'", pr_id)
        chk("its amount is the sum of the lines, not the posted 1",
            abs(qa["amount"] - 353.1) < 1e-9, qa["amount"])
        rows = {r["pr_item_id"]: r for r in [one(
            "SELECT * FROM pr_quote_lines WHERE quote_id=? AND pr_item_id=?", qa["id"], i)
            for i in (i1, i2, i3)]}
        chk("three lines are saved", all(rows.values()))
        chk("line total = unit x the request's qty",
            rows[i1]["line_total"] == 73.1 and rows[i2]["line_total"] == 80.0
            and rows[i1]["qty"] == 10, dict(rows[i1]))
        for bad in ("-1", "nan", "inf", "abc"):
            ok, why = svc.add_quote(pr_id, {"vendor": "Bad", "payment_terms": "Cash",
                                            "lines": {i1: bad}}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
            chk("a unit price of %r is refused" % bad, (not ok) and why == "bad_amount", why)
        chk("and nothing was written for it",
            one("SELECT COUNT(*) n FROM pr_quotes WHERE vendor='Bad'")["n"] == 0)

        print("\n2. a blank line = not quoted -> partial; VAT on the computed total")
        ok, why = svc.add_quote(pr_id, {"vendor": "Beta", "payment_terms": "Cash",
                                        "vat_applies": "0",
                                        "lines": {i1: "7.31", i2: "18.5", i3: " "}}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
        chk("a quote with a blank line is accepted", ok, why)
        qs = {q["vendor"]: q for q in svc.get_pr(pr_id)["quotes"]}
        chk("Beta is partial, Alpha is not", qs["Beta"]["partial"] and not qs["Alpha"]["partial"])
        chk("the blank line is not stored", i3 not in qs["Beta"]["lines"]
            and len(qs["Beta"]["lines"]) == 2)
        chk("Beta's amount excludes the unquoted line", qs["Beta"]["amount"] == 147.1,
            qs["Beta"]["amount"])
        chk("VAT applies on the computed total", qs["Alpha"]["after_vat"] == 402.53,
            qs["Alpha"]["after_vat"])

        print("\n3. lowest unit price per item")
        m = svc.quote_matrix(svc.get_pr(pr_id)["items"], svc.get_pr(pr_id)["quotes"])
        cols = [q["vendor"] for q in m["quotes"]]
        by_item = {r["item"]["id"]: dict(zip(cols, r["cells"])) for r in m["rows"]}
        chk("a tie marks both suppliers", by_item[i1]["Alpha"]["best"]
            and by_item[i1]["Beta"]["best"])
        chk("the cheaper one alone otherwise", by_item[i2]["Beta"]["best"]
            and not by_item[i2]["Alpha"]["best"])
        chk("an unquoted cell is empty and a lone price is not 'lowest'",
            by_item[i3]["Beta"] is None and not by_item[i3]["Alpha"]["best"])

        print("\n4. an old total-only quote")
        run_sql("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, created_at) "
                "VALUES (?, 'Legacy', 300, 'EGP', '2026-01-01')", pr_id)
        b = svc.get_pr(pr_id)
        old = [q for q in b["quotes"] if q["vendor"] == "Legacy"][0]
        chk("reads with no lines and is not partial", old["lines"] == {} and not old["partial"])
        chk("and is ranked on its total as before", old["after_vat"] == 300.0)
        chk("it stays out of the matrix",
            "Legacy" not in [q["vendor"] for q in svc.quote_matrix(b["items"], b["quotes"])["quotes"]])

        # HTTP as a super admin (Purchasing).
        uid = one("SELECT id FROM users WHERE username='ql_admin'")["id"]
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
        flask.g.pop("user", None)

        def flashes(c=cli):
            with c.session_transaction() as s:
                return [m for _c, m in s.pop("_flashes", [])]

        r = cli.get("/procurement/pr/%d" % pr_id)
        html = r.get_data(as_text=True)
        chk("the request page renders with old and lined quotes", r.status_code == 200,
            r.status_code)
        chk("the matrix is shown to Purchasing", 'data-i18n="proc.quote.matrix"' in html
            and "7.31" in html)
        chk("partial quotes are marked", 'data-i18n="proc.quote.partial"' in html)
        chk("the add form has a unit-price box per line",
            all('name="uprice_%d"' % i in html for i in (i1, i2, i3)))

        r = cli.post("/procurement/pr/%d/quote" % pr_id,
                     data={"_csrf": "tok", "file": (io.BytesIO(b"%PDF-1.4 test"), "q.pdf"), "vendor": "Gamma", "amount": "999999",
                           "payment_terms": "60 days", "vat_applies": "1",
                           "uprice_%d" % i1: "6", "uprice_%d" % i2: "",
                           "uprice_%d" % i3: "90"},
                     content_type="multipart/form-data")
        flashes()
        g = one("SELECT * FROM pr_quotes WHERE pr_id=? AND vendor='Gamma'", pr_id)
        chk("the route ignores a posted fake total", g and g["amount"] == 240.0,
            g and g["amount"])
        r = cli.post("/procurement/pr/%d/quote" % pr_id,
                     data={"_csrf": "tok", "file": (io.BytesIO(b"%PDF-1.4 test"), "q.pdf"), "vendor": "Delta", "payment_terms": "Cash",
                           "uprice_%d" % i1: "8"},
                     content_type="multipart/form-data")
        flashes()
        d = one("SELECT * FROM pr_quotes WHERE pr_id=? AND vendor='Delta'", pr_id)
        chk("line prices alone (no amount box) are enough", d and d["amount"] == 80.0)

        print("\n5. editing lines recomputes the amount")
        r = cli.post("/procurement/pr/%d/quote/%d/edit" % (pr_id, g["id"]),
                     data={"_csrf": "tok", "amount": "1", "payment_terms": "60 days",
                           "vat_applies": "1", "uprice_%d" % i1: "5",
                           "uprice_%d" % i2: "10", "uprice_%d" % i3: "90"})
        flashes()
        g2 = one("SELECT * FROM pr_quotes WHERE id=?", g["id"])
        chk("the edited amount is the new sum", g2["amount"] == 270.0, g2["amount"])
        chk("and the lines were replaced",
            one("SELECT COUNT(*) n FROM pr_quote_lines WHERE quote_id=?", g["id"])["n"] == 3)
        ok, msg = svc.update_quote(old["id"], {"vendor": "Legacy", "lines": {}}, user=buyer)
        chk("an old quote edited with no line prices is unchanged",
            ok and msg == "no_change" and one("SELECT amount a FROM pr_quotes WHERE id=?",
                                              old["id"])["a"] == 300, msg)
        chk("a bad unit price on edit is refused",
            svc.update_quote(g["id"], {"lines": {i1: "-3"}}, user=buyer)
            == (False, "bad_amount"))

        print("\n6. choosing a quote pre-fills the pricing form, saves nothing")
        ok, msg = svc.choose_quote(pr_id, qa["id"], buyer)
        chk("Alpha is chosen", ok, msg)
        chk("pr_items prices are unchanged",
            all((one("SELECT unit_price p FROM pr_items WHERE id=?", i)["p"] or 0) == 0
                for i in (i1, i2, i3)))
        html = cli.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        pre = {i: re.search(r'name="price_%d"[^>]*value="([^"]*)"' % i, html) for i in (i1, i2, i3)}
        chk("the pricing form is pre-filled from the chosen quote",
            all(pre.values()) and [float(pre[i].group(1)) for i in (i1, i2, i3)] == [7.31, 20, 100],
            {i: p and p.group(1) for i, p in pre.items()})
        chk("with a note saying so", 'data-i18n="proc.quote.prefilled"' in html)

        print("\n6b. prices keep full precision (no 6-digit rounding)")
        ok, why = svc.add_quote(pr_id, {"vendor": "Zeta", "payment_terms": "Cash",
                                        "vat_applies": "0",
                                        "lines": {i1: "15250.75", i2: "1234567"}}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
        z = one("SELECT * FROM pr_quotes WHERE pr_id=? AND vendor='Zeta'", pr_id)
        chk("Zeta recorded at the exact sum", ok and z["amount"] == 5090775.5, z and z["amount"])
        html = cli.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        chk("the edit form shows the exact unit prices",
            re.search(r'name="uprice_%d"[^>]*value="15250\.75"' % i1, html)
            and re.search(r'name="uprice_%d"[^>]*value="1234567(\.0)?"' % i2, html))
        cli.post("/procurement/pr/%d/quote/%d/edit" % (pr_id, z["id"]),
                 data={"_csrf": "tok", "payment_terms": "Cash", "vat_applies": "0",
                       "warranty": "1 year", "uprice_%d" % i1: "15250.75",
                       "uprice_%d" % i2: "1234567", "uprice_%d" % i3: ""})
        flashes()
        z2 = one("SELECT * FROM pr_quotes WHERE id=?", z["id"])
        chk("re-saving the edit form does not drift the amount",
            z2["amount"] == 5090775.5 and z2["warranty"] == "1 year",
            (z2["amount"], z2["warranty"]))
        svc.choose_quote(pr_id, z["id"], buyer)
        html = cli.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        pre1 = re.search(r'name="price_%d"[^>]*value="([^"]*)"' % i1, html)
        chk("the pricing pre-fill is not rounded", pre1 and float(pre1.group(1)) == 15250.75,
            pre1 and pre1.group(1))

        print("\n6c. a chosen quote in another currency is not pre-filled")
        ok, why = svc.add_quote(pr_id, {"vendor": "FxCo", "currency": "USD", "fx_rate": "50",
                                        "payment_terms": "Cash", "lines": {i1: "2"}}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
        fx = one("SELECT * FROM pr_quotes WHERE pr_id=? AND vendor='FxCo'", pr_id)
        svc.choose_quote(pr_id, fx["id"], buyer)
        html = cli.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        pre1 = re.search(r'name="price_%d"[^>]*value="([^"]*)"' % i1, html)
        chk("no USD price is copied into the EGP request",
            fx["currency"] == "USD" and pre1 and float(pre1.group(1)) == 0,
            pre1 and pre1.group(1))
        chk("with a note naming the currency", 'data-i18n="proc.quote.prefill_fx"' in html)

        print("\n6d. request lines changed after quoting")
        run_sql("UPDATE pr_items SET qty=20 WHERE id=?", i1)
        run_sql("DELETE FROM pr_items WHERE id=?", i3)
        b = svc.get_pr(pr_id)
        a = [q for q in b["quotes"] if q["vendor"] == "Alpha"][0]
        chk("a deleted line drops out of the quote", i3 not in a["lines"])
        chk("line total follows the current qty", a["lines"][i1]["line_total"] == 146.2,
            a["lines"][i1]["line_total"])
        chk("the amount is the sum of the visible lines, flagged stale",
            a["amount"] == 226.2 and a["stale"], (a["amount"], a["stale"]))
        chk("ranking uses the recomputed figure", a["after_vat"] == round(226.2 * 1.14, 2),
            a["after_vat"])
        m = svc.quote_matrix(b["items"], b["quotes"])
        chk("the matrix has no row for the deleted line",
            [r["item"]["id"] for r in m["rows"]] == [i1, i2])
        chk("an old total-only quote is not stale",
            not [q for q in b["quotes"] if q["vendor"] == "Legacy"][0]["stale"])
        html = cli.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        chk("the matrix marks the recalculated quote", 'data-i18n="proc.quote.stale"' in html)

        print("\n7. no unit prices without money visibility")
        for name in ("ql_req", "ql_store"):
            c = app.test_client()
            with c.session_transaction() as s:
                s["uid"], s["ep"] = one("SELECT id FROM users WHERE username=?", name)["id"], 0
            flask.g.pop("user", None)     # the app context caches the last signed-in user
            r = c.get("/procurement/pr/%d" % pr_id)
            h = r.get_data(as_text=True)
            chk(name + " opens the request", r.status_code == 200, r.status_code)
            chk(name + " sees no per-item prices",
                'data-i18n="proc.quote.matrix"' not in h and "7.31" not in h
                and "18.50" not in h)

        print("\n8. migration")
        n_lines = one("SELECT COUNT(*) n FROM pr_quote_lines")["n"]
        run_sql("ALTER TABLE pr_quote_lines RENAME TO zz_ql")
        b = svc.get_pr(pr_id)
        chk("a database without the table still reads (total-only)",
            b and all(q["lines"] == {} for q in b["quotes"]))
        chk("and the page still renders", cli.get("/procurement/pr/%d" % pr_id).status_code == 200)
        run_sql("DROP TABLE zz_ql")
        for _ in range(2):
            conn = get_db()
            try:
                schema.create_and_seed(conn)
            finally:
                conn.close()
        chk("create_and_seed twice brings the table back, empty",
            one("SELECT COUNT(*) n FROM pr_quote_lines")["n"] == 0 and n_lines > 0)
        ok, _ = svc.add_quote(pr_id, {"vendor": "Echo", "payment_terms": "Cash",
                                      "lines": {i1: "1"}}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
        chk("and quotes with lines can be added again", ok and one(
            "SELECT COUNT(*) n FROM pr_quote_lines")["n"] == 1)

    print("\nRESULT: " + ("ALL GREEN" if OK[0] else "FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
