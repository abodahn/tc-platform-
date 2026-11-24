"""Quotation currency + FX rate, and editing the chosen quotation.

    python -m app.approvals.tests_quote_fx

Owner report 2026-11-24 (an EGP request priced 10 + 10, one quotation typed
"euro", 972.88 before VAT, chosen): its prices never reached the request, the
quote rule judged a ~1,109 EUR order as 23 EGP, and the chosen quotation could
not be edited. Proves:
  1. typed currencies are normalised ("euro" -> EUR, "$" -> USD) and junk refused;
  2. a foreign quotation needs its FX rate to EGP on add and on edit;
  3. a chosen foreign quotation with NO rate is not applied: a specific reason,
     an error flash, the request unchanged, and the Purchasing sign-off refused;
  4. the CHOSEN quotation can be edited while the request is open; entering its
     rate re-applies it: currency EUR, rate, line prices, line + quote discount,
     VAT — and the quote rule now judges the EGP value (needs 3 quotes);
  5. once the order is placed it cannot be edited, and it never can be removed;
  6. mixed currencies are ranked on the EGP equivalent; a foreign quote with no
     rate is never "lowest", neither in the table nor in the per-item matrix;
  7. a viewer without money visibility sees no quotation figures, and an
     EGP-only request renders as before (no EGP-equivalent column).
"""
import base64
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="qfx_"), "q.db")
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

        signer = {st: mkuser("qf_" + st, sorted(roles)[0]) for st, roles in C.STAGE_ROLES.items()}
        buyer = signer[C.PRICING_GATE_STAGE]
        store = mkuser("qf_store", "storekeeper")
        outsider = mkuser("qf_viewer", "normal_user")

        def new_pr(title, lines):
            pid, _ = svc.create_pr({"title": title, "department": "Production",
                                    "currency": "EGP"},
                                   [{"item": n, "qty": 1, "unit_price": p} for n, p in lines],
                                   store, submit=True)
            return pid, [it["id"] for it in svc.get_pr(pid)["items"]]

        def walk_to_purchasing(pid):
            for _ in range(10):
                st = one("SELECT stage FROM pr_steps WHERE pr_id=? AND status='pending' "
                         "ORDER BY seq LIMIT 1", pid)
                if not st or st["stage"] == C.PRICING_GATE_STAGE:
                    return st and st["stage"]
                svc.act_on_step(pid, signer[st["stage"]], "approve")

        def req(pid):
            return dict(one("SELECT * FROM pr_requests WHERE id=?", pid))

        def rfq(pid):
            conn = get_db()
            try:
                return svc.rfq_requirement(conn, req(pid))
            finally:
                conn.close()

        cli = app.test_client()

        def login(user):
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = user["id"], 0, "tok"
            flask.g.pop("user", None)

        def post(url, **data):
            flask.g.pop("user", None)
            cli.post(url, data=dict(data, _csrf="tok"), content_type="multipart/form-data")
            with cli.session_transaction() as s:
                return s.pop("_flashes", [])

        def page(pid):
            flask.g.pop("user", None)
            r = cli.get("/procurement/pr/%d" % pid)
            return r.status_code, r.get_data(as_text=True)

        # ------------------------------------------------------------- 1 ----
        print("\n1. typed currencies are normalised, junk is refused")
        for raw, want in (("euro", "EUR"), (" Euro ", "EUR"), ("€", "EUR"), ("$", "USD"),
                          ("us$", "USD"), ("dollar", "USD"), ("L.E.", "EGP"), ("جنيه", "EGP"),
                          ("tl", "TRY"), ("ليرة", "TRY"), ("try", "TRY"), ("usd", "USD")):
            chk("%r -> %s" % (raw, want), C.currency_code(raw) == want, C.currency_code(raw))
        chk("'yen' and '' are not currencies",
            C.currency_code("yen") is None and C.currency_code("") is None)

        # ------------------------------------------------------------- 2 ----
        print("\n2. a foreign quotation needs its FX rate, on add and on edit")
        p, (a, b) = new_pr("Owner case", [("BELT", 10), ("PULLEY", 10)])
        chk("the request is priced in EGP (10 + 10, VAT 14%)",
            svc.price_pr(p, {a: 10, b: 10}, {"tax_rate": "14"}, buyer)[0])
        astas = {"vendor": "EuroCo", "payment_terms": "30 days", "vat_applies": "1",
                 "lines": {a: "744.50", b: "501.38"}, "line_discs": {a: ("pct", "4")},
                 "disc_type": "pct", "disc_value": "20"}
        chk("EUR with no rate is refused",
            svc.add_quote(p, dict(astas, currency="euro"), buyer, **QF)
            == (False, "quote_fx_required"))
        chk("a rate of 1 is no rate",
            svc.add_quote(p, dict(astas, currency="EUR", fx_rate="1"), buyer, **QF)
            == (False, "quote_fx_required"))
        chk("an unknown currency is refused",
            svc.add_quote(p, dict(astas, currency="yen", fx_rate="0.3"), buyer, **QF)
            == (False, "bad_currency"))
        chk("nothing was written", one("SELECT COUNT(*) n FROM pr_quotes WHERE pr_id=?", p)["n"] == 0)
        ok, why = svc.add_quote(p, dict(astas, currency="euro", fx_rate="50"), buyer, **QF)
        q = dict(one("SELECT * FROM pr_quotes WHERE pr_id=?", p))
        chk("'euro' with a rate is stored as EUR @ 50", ok and q["currency"] == "EUR"
            and q["fx_rate"] == 50, (why, q["currency"], q["fx_rate"]))
        chk("amount after the 4% line and 20% quote discounts = 972.88",
            abs(q["amount"] - 972.88) < 1e-9, q["amount"])
        chk("switching it to USD without a new rate is refused",
            svc.update_quote(q["id"], {"currency": "USD"}, user=buyer)
            == (False, "quote_fx_required"))
        chk("junk currency on edit is refused",
            svc.update_quote(q["id"], {"currency": "dinar"}, user=buyer) == (False, "bad_currency"))
        login(buyer)
        f = post("/procurement/pr/%d/quote" % p, vendor="Other", amount="100",
                 payment_terms="Cash", currency="EUR", file=(io.BytesIO(b"%PDF-1.4 x"), "o.pdf"))
        chk("the add route refuses EUR without a rate, as an error",
            ("error", "Enter the FX rate to EGP for a quotation in a foreign currency.") in f, f)

        # The live row: typed "euro", no rate, from before this change.
        run_sql("UPDATE pr_quotes SET currency='euro', fx_rate=NULL WHERE id=?", q["id"])
        bq = svc.get_pr(p)["quotes"][0]
        chk("the legacy 'euro' row reads as EUR, rate needed, 1,109.08 after VAT",
            bq["currency"] == "EUR" and bq["rate_needed"] and bq["after_vat"] == 1109.08
            and bq["after_vat_egp"] is None, (bq["currency"], bq["after_vat"]))
        chk("...and the stored row is not rewritten",
            one("SELECT currency c FROM pr_quotes WHERE id=?", q["id"])["c"] == "euro")

        # ------------------------------------------------------------- 3 ----
        print("\n3. a chosen foreign quote with no rate is NOT silently skipped")
        chk("the request reaches the Purchasing rung", walk_to_purchasing(p) == C.PRICING_GATE_STAGE)
        chk("apply_chosen_quote names the reason (service)",
            svc.choose_quote(p, q["id"], buyer)[0]
            and svc.apply_chosen_quote(p, q["id"], buyer) == (False, "quote_fx_required"))
        run_sql("UPDATE pr_quotes SET is_chosen=0 WHERE id=?", q["id"])
        f = post("/procurement/pr/%d/quote/%d/choose" % (p, q["id"]))
        chk("choosing it flashes an ERROR asking for the FX rate on the quotation",
            any(c == "error" and "no FX rate to EGP" in m for c, m in f), f)
        r = req(p)
        chk("the request keeps its explicit, unchanged state (EGP, 20)",
            r["currency"] == "EGP" and r["total"] == 20, (r["currency"], r["total"]))
        chk("the Purchasing sign-off is refused while the quote is not applied",
            svc.act_on_step(p, buyer, "approve") == (False, "chosen_quote_not_applied"))
        code, html = page(p)
        chk("the page flags the rate and opens the chosen quote's edit form",
            code == 200 and 'data-i18n="proc.quote.rate_needed"' in html
            and "<details open>" in html and 'name="fx_rate"' in html
            and 'data-i18n="proc.quote.chosen_edit"' in html)
        chk("the currency is a list of codes, not free text",
            '<select name="currency">' in html and '<input name="currency"' not in html)
        chk("the panel sits full width, below the two columns",
            html.index('id="prQuotes"') > html.index('data-i18n="proc.documents"'))

        # ------------------------------------------------------------- 4 ----
        print("\n4. editing the chosen quotation re-applies it to the request")
        chk("its requester may not re-price it",
            svc.update_quote(q["id"], {"warranty": "1y"}, user=store)
            == (False, "self_quote_choice"))
        f = post("/procurement/pr/%d/quote/%d/edit" % (p, q["id"]), currency="EUR",
                 fx_rate="55", payment_terms="30 days", vat_applies="1")
        chk("saved and applied", ("success", "Quotation updated.") in f and (
            "success", "Quote selected — its prices and VAT are now the request's prices.") in f, f)
        q2 = dict(one("SELECT * FROM pr_quotes WHERE id=?", q["id"]))
        chk("the row now holds EUR @ 55", q2["currency"] == "EUR" and q2["fx_rate"] == 55)
        r = req(p)
        items = {it["id"]: it for it in svc.get_pr(p)["items"]}
        am = svc.pr_amounts(r)
        chk("request currency EUR at 55", r["currency"] == "EUR" and r["fx_rate"] == 55,
            (r["currency"], r["fx_rate"]))
        chk("line prices 744.50 / 501.38, line 1 discount 4%",
            items[a]["unit_price"] == 744.5 and items[b]["unit_price"] == 501.38
            and items[a]["disc_type"] == "pct" and items[a]["disc_value"] == 4,
            (items[a]["unit_price"], items[b]["unit_price"], items[a]["disc_value"]))
        chk("quote discount 20%% carried, VAT 14%%: net 972.88, grand 1,109.08 (%s)" % am,
            r["disc_type"] == "pct" and r["disc_value"] == 20 and am["tax_rate"] == 14
            and abs(am["subtotal"] - 972.88) < 0.005 and abs(am["grand"] - 1109.08) < 0.005)
        chk("every line carries the chosen supplier",
            all(it["vendor"] == "EuroCo" for it in items.values()))
        rq = rfq(p)
        chk("the quote rule judges 53,508.40 EGP BEFORE VAT and needs 3 quotes (%s)" % rq,
            rq["value"] == 53508.4 and rq["required"] == 3 and not rq["ok"]
            and rq["msg"] == "needs_quotes")
        chk("so the sign-off is refused for competitive quotes, not a stale value",
            svc.act_on_step(p, buyer, "approve") == (False, "needs_quotes"))
        n_ev = one("SELECT COUNT(*) n FROM pr_events WHERE pr_id=? AND action='quote_edited'", p)["n"]
        chk("the edit is in the audit trail", n_ev >= 1, n_ev)
        f = post("/procurement/pr/%d/quote/%d/delete" % (p, q["id"]))
        chk("the chosen quotation still cannot be removed",
            one("SELECT COUNT(*) n FROM pr_quotes WHERE id=?", q["id"])["n"] == 1
            and svc.delete_quote(q["id"], user=buyer) == (False, "chosen"), f)

        # ------------------------------------------------------------- 5 ----
        print("\n5. once the order is placed, the chosen quotation is fixed")
        run_sql("UPDATE pr_requests SET status='po_issued' WHERE id=?", p)
        chk("editing is refused (locked)",
            svc.update_quote(q["id"], {"warranty": "2y"}, user=buyer) == (False, "locked"))
        chk("removing is refused", svc.delete_quote(q["id"], user=buyer)[0] is False)
        code, html = page(p)
        chk("the page offers no edit form for it",
            code == 200 and "/quote/%d/edit" % q["id"] not in html)

        # ------------------------------------------------------------- 6 ----
        print("\n6. mixed currencies rank on the EGP equivalent")
        m, (ml,) = new_pr("Mixed", [("MOTOR", 1)])
        for vendor, cur, fx, price in (("EgpCo", "EGP", None, "60000"),
                                       ("UsdCo", "usd", "50", "1000"),
                                       ("EurCo", "EUR", "55", "10")):
            ok, why = svc.add_quote(m, {"vendor": vendor, "currency": cur, "fx_rate": fx,
                                        "payment_terms": "Cash", "vat_applies": "0",
                                        "lines": {ml: price}}, buyer, **QF)
            chk("%s quote recorded" % vendor, ok, why)
        run_sql("UPDATE pr_quotes SET fx_rate=NULL WHERE pr_id=? AND vendor='EurCo'", m)
        bm = svc.get_pr(m)
        chk("ordered cheapest EGP first, the no-rate quote last",
            [x["vendor"] for x in bm["quotes"]] == ["UsdCo", "EgpCo", "EurCo"],
            [x["vendor"] for x in bm["quotes"]])
        cmp_ = svc.quote_comparison(bm["quotes"])
        chk("while EurCo has no rate nothing is 'lowest' and no saving is claimed (%s)" % cmp_,
            cmp_["basis"] == "egp" and cmp_["lowest"] is None and cmp_["saving"] == 0
            and cmp_["currency"] == "EGP")
        cmp2 = svc.quote_comparison([dict(x, fx_rate=55) if x["vendor"] == "EurCo" else x
                                     for x in bm["quotes"]])
        chk("with its rate (55): lowest 550 EGP, saving 59,450 EGP (%s)" % cmp2,
            cmp2["lowest"] == 550 and cmp2["saving"] == 59450)
        chk("EGP 60,000 vs 10 EUR with no rate: neither is 'lowest'",
            svc.quote_comparison([{"amount": 60000, "currency": "EGP"},
                                  {"amount": 10, "currency": "EUR"}])["lowest"] is None)
        mx = svc.quote_matrix(bm["items"], bm["quotes"])
        cells = dict(zip([x["vendor"] for x in mx["quotes"]], mx["rows"][0]["cells"]))
        chk("per-item best is the USD cell, never the 10 EUR with no rate",
            cells["UsdCo"]["best"] and not cells["EgpCo"]["best"] and not cells["EurCo"]["best"])
        same = svc.quote_comparison([{"amount": 100, "currency": "EUR"},
                                     {"amount": 90, "currency": "euro"}])
        chk("one currency (even typed two ways) compares as quoted",
            same["basis"] == "same" and same["lowest"] == 90 and same["currency"] == "EUR", same)
        code, html = page(m)
        # The quotes table up to the per-item matrix (edit rows hold tables of their own).
        tbl = html[html.index('class="tbl qt-tbl"'):html.index('data-i18n="proc.quote.matrix"')]
        rows = {re.search(r'qt-name">(\w+)', r).group(1): r
                for r in tbl.split('<tr class="qt-row')[1:]}
        chk("the page marks no quote lowest while a rate is missing",
            not any('proc.quote.lowest' in r for r in rows.values()))
        chk("it shows the EGP-equivalent column and the rate",
            'data-i18n="proc.fx.egp_equiv">EGP equivalent</th>' in tbl
            and "<bdi>50,000 EGP</bdi>" in rows["UsdCo"] and "1 USD = 50 EGP" in rows["UsdCo"])
        chk("EurCo says its rate is needed", 'proc.quote.rate_needed' in rows["EurCo"])
        svc.choose_quote(m, [x for x in bm["quotes"] if x["vendor"] == "UsdCo"][0]["id"], buyer)
        ok, why = svc.apply_chosen_quote(
            m, [x for x in bm["quotes"] if x["vendor"] == "UsdCo"][0]["id"], buyer)
        rm = req(m)
        chk("choosing the USD quote (with rate) prices the request in USD at 50",
            ok and rm["currency"] == "USD" and rm["fx_rate"] == 50 and rm["total"] == 1000,
            (why, rm["currency"], rm["fx_rate"], rm["total"]))

        # ------------------------------------------------------------- 7 ----
        print("\n7. no figures without money visibility; EGP-only renders as before")
        login(outsider)
        code, html = page(m)
        chk("an outsider opens the request", code == 200, code)
        qp = html[html.index('id="prQuotes"'):]
        chk("and sees no quotation figures",
            "50,000" not in html and "60,000" not in html and "1,000" not in qp
            and "1 USD = 50" not in qp and 'data-i18n="proc.quote.matrix"' not in html,
            [x for x in ("50,000", "60,000", "1,000", "1 USD = 50") if x in qp])
        e, (el,) = new_pr("EGP only", [("BOLT", 100)])
        svc.add_quote(e, {"vendor": "Local", "payment_terms": "Cash", "vat_applies": "1",
                          "lines": {el: "5000"}}, buyer, **QF)
        login(buyer)
        code, html = page(e)
        chk("an EGP-only request renders", code == 200, code)
        chk("with no EGP-equivalent column",
            '<th class="qt-num" data-i18n="proc.fx.egp_equiv">' not in html)
        chk("and its figures as before (5,000 EGP / 700 / 5,700 EGP)",
            "<bdi>5,000 EGP</bdi>" in html and "<bdi>700</bdi>" in html
            and "<bdi>5,700 EGP</bdi>" in html)

        # ------------------------------------------------------------- 8 ----
        print("\n8. review fixes")
        usd_id = [x for x in bm["quotes"] if x["vendor"] == "UsdCo"][0]["id"]
        conn = get_db()
        try:
            ch = svc._chosen_quote(conn, m)
        finally:
            conn.close()
        chk("the chosen-quote read never pulls the supplier file",
            ch and ch["id"] == usd_id and "content_b64" not in ch)
        chk("a quotation cannot be edited through another request's URL (service)",
            svc.update_quote(usd_id, {"amount": "400000"}, user=buyer, pr_id=e)
            == (False, "not_found"))
        f = post("/procurement/pr/%d/quote/%d/edit" % (e, usd_id), amount="400000",
                 currency="USD", fx_rate="50", payment_terms="Cash")
        chk("...nor through the route", ("error", "Quotation not found.") in f
            and one("SELECT amount a FROM pr_quotes WHERE id=?", usd_id)["a"] == 1000, f)
        from app.routes import approvals as RA
        from app.approvals.messages import MESSAGES
        chk("a requester editing their chosen quote reads a sentence, not a code",
            RA._QUOTE_ERRORS.get("self_quote_choice") in MESSAGES)
        run_sql("UPDATE pr_quotes SET fx_rate=52.34567 WHERE id=?", usd_id)
        code, html = page(m)
        chk("the edit form keeps every digit of the rate",
            code == 200 and 'value="52.34567"' in html)
        run_sql("UPDATE pr_requests SET status='in_transit' WHERE id=?", m)
        code, html = page(m)
        chk("an in-transit request offers no edit form for its chosen quote",
            code == 200 and "/quote/%d/edit" % usd_id not in html)

        # Same currency: the request value Purchasing priced rules, not the quote's.
        lq = svc.get_pr(e)["quotes"][0]["id"]
        svc.choose_quote(e, lq, buyer)
        svc.apply_chosen_quote(e, lq, buyer)
        svc.price_pr(e, {el: 4000}, {"tax_rate": "14"}, buyer)
        chk("an applied EGP quote does not raise the rule's value (4,000 before VAT, not 5,000)",
            rfq(e)["value"] == 4000, rfq(e))

        # A PARTIAL quote in another currency must not re-label the other lines.
        t_, (ta, tb) = new_pr("Partial", [("PART-A", 1), ("PART-B", 1)])
        svc.price_pr(t_, {ta: 1000, tb: 1000},
                     {"tax_rate": "14", "currency": "EUR", "fx_rate": "50"}, buyer)
        before = svc.egp_commitment(req(t_))
        ok, why = svc.add_quote(t_, {"vendor": "TlCo", "currency": "tl", "fx_rate": "1.5",
                                     "payment_terms": "Cash", "vat_applies": "0",
                                     "lines": {ta: "3000"}}, buyer, **QF)
        tq = one("SELECT id FROM pr_quotes WHERE pr_id=?", t_)["id"]
        walk_to_purchasing(t_)
        svc.choose_quote(t_, tq, buyer)
        chk("a partial TRY quote on an EUR request is refused with its reason",
            svc.apply_chosen_quote(t_, tq, buyer) == (False, "partial_other_currency"))
        rt = req(t_)
        chk("the request keeps EUR @ 50 and its %s EGP value" % before,
            rt["currency"] == "EUR" and rt["fx_rate"] == 50
            and svc.egp_commitment(rt) == before == 114000, (rt["currency"], rt["fx_rate"]))
        chk("and Purchasing cannot sign it off",
            svc.act_on_step(t_, buyer, "approve")[0] is False)

    print("\nALL PASS" if OK[0] else "\nSOME FAILED")
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
