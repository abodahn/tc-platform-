"""Choosing the supplier PER LINE from the quotations (owner, 2026-11-25).

    python -m app.approvals.tests_quote_line_award

Supplier A is best on line 1, supplier B on line 2: Purchasing awards each line
of the "Price per item" matrix to a quotation. Proves:
  1. the whole-quote Choose still prices every line and now marks each line
     with its quotation (pr_items.quote_id);
  2. a per-line award carries that quotation's unit price, line discount and
     supplier (never its quote-level discount); a mixed award clears the
     request-level discount, un-chooses every quotation, keeps the first line's
     supplier on the header and gives two POs;
  3. awarding every line to one quotation makes it the chosen one again;
  4. refusals: requester, no file, no price for that line, locked, another
     currency, another VAT; an awarded quotation cannot be removed;
  5. the route flashes success and the page shows the mark and the buttons.
"""
import base64
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]
PDF = base64.b64encode(b"%PDF-1.4 test").decode("ascii")
QF = {"filename": "q.pdf", "content_type": "application/pdf", "content_b64": PDF}


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="qla_"), "q.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        import flask
        from app.db import get_db
        from app.approvals import services as svc, constants as C

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

        def mkuser(name, role):
            run_sql("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                    "is_active, lang_pref, created_at) VALUES (?,?,?,?,1,'en','2026-01-01')",
                    name, "x", name, role)
            return dict(one("SELECT * FROM users WHERE username=?", name))

        signer = {st: mkuser("la_" + st, sorted(roles)[0]) for st, roles in C.STAGE_ROLES.items()}
        buyer = signer[C.PRICING_GATE_STAGE]
        store = mkuser("la_store", "storekeeper")

        def new_pr(title, names):
            pid, _ = svc.create_pr({"title": title, "department": "Production",
                                    "currency": "EGP"},
                                   [{"item": n, "qty": 2, "unit_price": 0} for n in names],
                                   store, submit=True)
            return pid, [it["id"] for it in svc.get_pr(pid)["items"]]

        def add_q(pid, vendor, lines, **kw):
            data = {"vendor": vendor, "currency": "EGP", "payment_terms": "30 days",
                    "vat_applies": "1", "lines": lines}
            data.update(kw)
            ok, why = svc.add_quote(pid, data, buyer, **QF)
            assert ok, why
            return one("SELECT MAX(id) i FROM pr_quotes WHERE pr_id=?", pid)["i"]

        def item(i):
            return dict(one("SELECT * FROM pr_items WHERE id=?", i))

        def req(pid):
            return dict(one("SELECT * FROM pr_requests WHERE id=?", pid))

        def chosen(pid):
            conn = get_db()
            try:
                return {r["id"] for r in conn.execute(
                    "SELECT id FROM pr_quotes WHERE pr_id=? AND is_chosen=1", (pid,)).fetchall()}
            finally:
                conn.close()

        p, (l1, l2, l3) = new_pr("Line award", ["BELT", "PULLEY", "BEARING"])
        qa = add_q(p, "AlphaCo", {l1: "10", l2: "20", l3: "30"},
                   line_discs={l3: ("pct", "5")}, disc_type="pct", disc_value="10")
        qb = add_q(p, "BetaCo", {l1: "12", l2: "15"})

        # ------------------------------------------------------------- 1 ----
        print("\n1. whole-quote Choose marks every line with its quotation")
        chk("choose + apply A", svc.choose_quote(p, qa, buyer)[0]
            and svc.apply_chosen_quote(p, qa, buyer)[0])
        chk("all three lines carry quote A",
            [item(i)["quote_id"] for i in (l1, l2, l3)] == [qa, qa, qa])
        r = req(p)
        chk("A's quote discount became the request discount (pct 10), VAT 14",
            r["disc_type"] == "pct" and r["disc_value"] == 10 and r["tax_rate"] == 14,
            (r["disc_type"], r["disc_value"], r["tax_rate"]))
        whole_total = float(r["total"])

        # ------------------------------------------------------------- 2 ----
        print("\n2. mixed award: line1 -> A, line2 -> B, line3 -> A")
        for i, q in ((l1, qa), (l2, qb), (l3, qa)):
            res = svc.award_quote_line(p, i, q, buyer)
            chk("award line %s" % i, res == (True, ""), res)
        a1, a2, a3 = item(l1), item(l2), item(l3)
        chk("prices 10 / 15 / 30",
            (a1["unit_price"], a2["unit_price"], a3["unit_price"]) == (10, 15, 30))
        chk("vendors A / B / A",
            (a1["vendor"], a2["vendor"], a3["vendor"]) == ("AlphaCo", "BetaCo", "AlphaCo"))
        chk("quote_id A / B / A",
            (a1["quote_id"], a2["quote_id"], a3["quote_id"]) == (qa, qb, qa))
        chk("line 3 keeps A's own 5% line discount",
            a3["disc_type"] == "pct" and a3["disc_value"] == 5, (a3["disc_type"], a3["disc_value"]))
        r = req(p)
        chk("the request discount (A's whole offer) is cleared",
            not r["disc_type"] and not r["disc_value"], (r["disc_type"], r["disc_value"]))
        chk("total = 2x10 + 2x15 + 2x30 less 5% on line 3 = 107",
            abs(float(r["total"]) - 107) < 1e-6, r["total"])
        chk("no quotation is 'chosen' on a mixed award", chosen(p) == set())
        chk("the header vendor is the first line's (AlphaCo)", r["vendor"] == "AlphaCo")
        groups = svc.po_groups(svc.get_pr(p)["items"], r)
        chk("two purchase orders: AlphaCo (2 lines) + BetaCo (1 line)",
            {k: len(v) for k, v in groups.items()} == {"AlphaCo": 2, "BetaCo": 1}, groups.keys())
        chk("an awarded quotation cannot be removed",
            svc.delete_quote(qb, user=buyer) == (False, "awarded"))

        # ------------------------------------------------------------- 3 ----
        print("\n3. every line to one quotation makes it the chosen one")
        chk("award line2 -> A", svc.award_quote_line(p, l2, qa, buyer) == (True, ""))
        chk("A is chosen, B is not", chosen(p) == {qa})
        r = req(p)
        chk("A's whole offer again: its 10% quote discount and total are back",
            r["disc_type"] == "pct" and r["disc_value"] == 10
            and abs(float(r["total"]) - whole_total) < 1e-6, (r["disc_type"], r["total"]))
        chk("header vendor AlphaCo, line 2 back on A at 20",
            req(p)["vendor"] == "AlphaCo" and item(l2)["unit_price"] == 20
            and item(l2)["vendor"] == "AlphaCo")
        chk("B is no longer awarded, so it can be removed... (not removed here)",
            one("SELECT COUNT(*) n FROM pr_items WHERE quote_id=?", qb)["n"] == 0)

        # ------------------------------------------------------------- 4 ----
        print("\n4. refusals")
        chk("the requester cannot award on their own request",
            svc.award_quote_line(p, l1, qb, store) == (False, "self_quote_choice"))
        chk("no price for that line",
            svc.award_quote_line(p, l3, qb, buyer) == (False, "no_line_price"))
        chk("a line or quote of another request is not found",
            svc.award_quote_line(p, 999999, qa, buyer) == (False, "not_found"))
        qn = add_q(p, "NoFileCo", {l1: "8"})
        run_sql("UPDATE pr_quotes SET content_b64=NULL WHERE id=?", qn)
        chk("a quotation without its file is refused",
            svc.award_quote_line(p, l1, qn, buyer) == (False, "no_attachment"))
        qv = add_q(p, "NoVatCo", {l1: "9"}, vat_applies="0")
        chk("a VAT-free line among 14% lines is refused (vat_mismatch)",
            svc.award_quote_line(p, l1, qv, buyer) == (False, "vat_mismatch"))
        qu = add_q(p, "DollarCo", {l1: "1"}, currency="USD", fx_rate="50")
        chk("a USD line among EGP lines is refused",
            svc.award_quote_line(p, l1, qu, buyer) == (False, "partial_other_currency"))
        chk("nothing moved: line 1 still A at 10",
            item(l1)["unit_price"] == 10 and item(l1)["quote_id"] == qa)
        run_sql("UPDATE pr_requests SET status='approved' WHERE id=?", p)
        chk("an approved request is locked",
            svc.award_quote_line(p, l1, qb, buyer) == (False, "locked"))
        run_sql("UPDATE pr_requests SET status='pending' WHERE id=?", p)

        # The first line priced takes the quotation's VAT, as the whole-quote choice does.
        p2, (m1, m2) = new_pr("First line", ["CHAIN", "GEAR"])
        qz = add_q(p2, "ZeroVat", {m1: "5"}, vat_applies="0")
        chk("first award on an unpriced request takes the quote's VAT (0)",
            svc.award_quote_line(p2, m1, qz, buyer) == (True, "")
            and req(p2)["tax_rate"] == 0 and chosen(p2) == {qz})

        # ------------------------------------------------------------- 5 ----
        print("\n5. the route and the page")
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = buyer["id"], 0, "tok"
        flask.g.pop("user", None)
        resp = cli.post("/procurement/pr/%d/quote/%d/award/%d" % (p, qb, l2),
                        data={"_csrf": "tok"})
        with cli.session_transaction() as s:
            f = s.pop("_flashes", [])
        chk("POST redirects back to the quotes panel",
            resp.status_code == 302 and resp.headers["Location"].endswith("#prQuotes"),
            (resp.status_code, resp.headers.get("Location")))
        chk("success flash", any(c == "success" and m.startswith("Line awarded") for c, m in f), f)
        chk("line 2 is BetaCo again", item(l2)["quote_id"] == qb and item(l2)["vendor"] == "BetaCo")
        resp = cli.post("/procurement/pr/%d/quote/%d/award/%d" % (p, qb, l3),
                        data={"_csrf": "tok"})
        with cli.session_transaction() as s:
            f = s.pop("_flashes", [])
        chk("a refusal flashes its message as an error",
            ("error", "This quotation has no price for that line.") in f, f)
        flask.g.pop("user", None)
        html = cli.get("/procurement/pr/%d" % p).get_data(as_text=True)
        chk("the page shows the per-line hint", 'data-i18n="proc.quote.mx_hint"' in html)
        chk("three awarded cells carry the Chosen mark",
            html.count('data-i18n="proc.quote.line_chosen"') == 3,
            html.count('data-i18n="proc.quote.line_chosen"'))
        chk("B's line-1 price has a Choose button",
            "/quote/%d/award/%d" % (qb, l1) in html)
        chk("the awarded cell has no button", "/quote/%d/award/%d" % (qb, l2) not in html)
        chk("a quotation with no file gets no button", "/quote/%d/award/" % qn not in html)
        chk("each Choose button names its supplier and line for screen readers",
            '<span class="sr-only"> BetaCo — BELT</span>' in html)

        # ------------------------------------------------------------- 6 ----
        print("\n6. an awarded (not chosen) quotation stays in step with its lines")
        chk("state: mixed, line 2 on B at 15", chosen(p) == set()
            and item(l2)["quote_id"] == qb and item(l2)["unit_price"] == 15)
        chk("the requester cannot edit a quotation a line rests on",
            svc.update_quote(qb, {"lines": {l1: "12", l2: "25"}}, user=store, pr_id=p)
            == (False, "self_quote_choice"))
        resp = cli.post("/procurement/pr/%d/quote/%d/edit" % (p, qb),
                        data={"_csrf": "tok", "uprice_%d" % l1: "12", "uprice_%d" % l2: "25"})
        with cli.session_transaction() as s:
            f = s.pop("_flashes", [])
        chk("editing B's line-2 price carries it to the line (25, still B)",
            item(l2)["unit_price"] == 25 and item(l2)["quote_id"] == qb, (item(l2), f))
        chk("pricing line 2 by hand clears its award mark",
            svc.price_pr(p, {l2: 18}, {}, buyer, vendors={l2: "GammaCo"})[0]
            and item(l2)["quote_id"] is None and item(l2)["vendor"] == "GammaCo")
        chk("so B can be removed again (no line rests on it)",
            one("SELECT COUNT(*) n FROM pr_items WHERE quote_id=?", qb)["n"] == 0)

        # ------------------------------------------------------------- 7 ----
        print("\n7. requests priced before quote_id existed")
        p3, (k1, k2, k3) = new_pr("Legacy", ["BELT", "PULLEY", "BEARING"])
        ka = add_q(p3, "AlphaCo", {k1: "10", k2: "20", k3: "30"},
                   disc_type="pct", disc_value="10")
        kb = add_q(p3, "BetaCo", {k1: "12", k2: "15"})
        svc.choose_quote(p3, ka, buyer)
        svc.apply_chosen_quote(p3, ka, buyer)
        run_sql("UPDATE pr_items SET quote_id=NULL WHERE pr_id=?", p3)
        chk("re-awarding a legacy line to the chosen A keeps A chosen",
            svc.award_quote_line(p3, k2, ka, buyer) == (True, "") and chosen(p3) == {ka}
            and req(p3)["disc_type"] == "pct")
        run_sql("UPDATE pr_items SET quote_id=NULL WHERE pr_id=?", p3)
        chk("a legacy line to B: A's whole-offer discount does not survive the mix",
            svc.award_quote_line(p3, k2, kb, buyer) == (True, "")
            and not req(p3)["disc_type"] and chosen(p3) == set(), req(p3)["disc_type"])

    print("\nALL PASS" if OK[0] else "\nSOME FAILED")
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
