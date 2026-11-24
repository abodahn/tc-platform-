"""A supplier quotation must carry the supplier's own file.

    python -m app.approvals.tests_quote_attachment

Proves:
  1. adding a quote with no file is refused — by the service and by the route,
     with a translated message — and with a file it is recorded;
  2. a quote with no file cannot be CHOSEN (service + route);
  3. the Purchasing rung cannot be approved while the chosen quote has no file;
     attaching one (allowed on a chosen legacy quote, attach-only) unblocks it;
  4. only quotes with a file count toward the RFQ minimum-quotes gate, and a
     single-source purchase's recorded quote also needs its file;
  5. a legacy quote can get its file via the edit route and then be chosen;
  6. the upload's type and size validation applies to add and edit;
  7. the page shows the 'No attachment' badge, hides Choose for such a quote
     and marks the add form's file input required.
"""
import base64
import io
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="qatt_"), "q.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        import flask
        from app.db import get_db
        from app.approvals import services as svc, constants as C
        from app.approvals.messages import translate

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

        def mkuser(name, role, lang="en"):
            run_sql("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                    "is_active, lang_pref, created_at) VALUES (?,?,?,?,1,?,'2026-01-01')",
                    name, "x", name, role, lang)
            return dict(one("SELECT * FROM users WHERE username=?", name))

        def legacy(pr_id, vendor, amount):
            run_sql("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, payment_terms, "
                    "created_at) VALUES (?,?,?,'EGP','30 days','2026-01-01')",
                    pr_id, vendor, amount)
            return one("SELECT id FROM pr_quotes WHERE pr_id=? AND vendor=?",
                       pr_id, vendor)["id"]

        signer = {st: mkuser("qa_" + st, sorted(roles)[0]) for st, roles in C.STAGE_ROLES.items()}
        buyer = signer[C.PRICING_GATE_STAGE]
        store = mkuser("qa_store", "storekeeper")

        def new_pr(title, total):
            pid, _ = svc.create_pr({"title": title, "department": "Production",
                                    "currency": "EGP"},
                                   [{"item": "Pump", "qty": 1, "unit_price": total}],
                                   store, submit=True)
            return pid

        def walk_to_purchasing(pid):
            conn = get_db()
            try:
                for _ in range(10):
                    st = conn.execute("SELECT stage FROM pr_steps WHERE pr_id=? AND "
                                      "status='pending' ORDER BY seq LIMIT 1",
                                      (pid,)).fetchone()
                    if not st or st["stage"] == C.PRICING_GATE_STAGE:
                        return st and st["stage"]
                    svc.act_on_step(pid, signer[st["stage"]], "approve")
            finally:
                conn.close()

        def gate(pid):
            conn = get_db()
            try:
                pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pid,)).fetchone()
                return svc.rfq_gate_check(conn, pr)
            finally:
                conn.close()

        # ------------------------------------------------------------- 1 ----
        print("\n1. a quote needs the supplier's file")
        p1 = new_pr("Attach one", 1000)
        ok, why = svc.add_quote(p1, {"vendor": "Alpha", "amount": 900,
                                     "payment_terms": "30 days"}, buyer)
        chk("the service refuses a quote with no file", (not ok) and why == "attachment_required", why)
        chk("and writes nothing", one("SELECT COUNT(*) n FROM pr_quotes WHERE pr_id=?", p1)["n"] == 0)
        ok, why = svc.add_quote(p1, {"vendor": "Alpha", "amount": 900,
                                     "payment_terms": "30 days"}, buyer, **QF)
        chk("with a file it is recorded", ok, why)
        qa = one("SELECT * FROM pr_quotes WHERE pr_id=? AND vendor='Alpha'", p1)
        chk("and the file is stored", qa["content_b64"] == PDF and qa["filename"] == "q.pdf")

        # HTTP as an Arabic-speaking buyer
        abuyer = mkuser("qa_buyer_ar", buyer["role"], "ar")
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = abuyer["id"], 0, "tok"
        flask.g.pop("user", None)

        def flashes():
            with cli.session_transaction() as s:
                return [m for _c, m in s.pop("_flashes", [])]

        def post(url, **data):
            flask.g.pop("user", None)
            r = cli.post(url, data=dict(data, _csrf="tok"), content_type="multipart/form-data")
            return r, flashes()

        n0 = one("SELECT COUNT(*) n FROM pr_quotes WHERE pr_id=?", p1)["n"]
        r, f = post("/procurement/pr/%d/quote" % p1, vendor="Beta", amount="950",
                    payment_terms="Cash")
        want = translate("Attach the supplier's quotation file.", "ar")
        chk("the route refuses a quote with no file",
            r.status_code == 302 and one("SELECT COUNT(*) n FROM pr_quotes WHERE pr_id=?",
                                         p1)["n"] == n0, r.status_code)
        chk("with a translated message", want in f and want != "Attach the supplier's "
            "quotation file.", f)
        r, f = post("/procurement/pr/%d/quote" % p1, vendor="Beta", amount="950",
                    payment_terms="Cash", file=(io.BytesIO(b"%PDF-1.4 beta"), "beta.pdf"))
        chk("and accepts it with a file",
            one("SELECT filename FROM pr_quotes WHERE pr_id=? AND vendor='Beta'", p1)
            ["filename"] == "beta.pdf", f)

        print("\n6. type and size validation still applies")
        r, f = post("/procurement/pr/%d/quote" % p1, vendor="Gamma", amount="1",
                    payment_terms="Cash", file=(io.BytesIO(b"MZ"), "virus.exe"))
        chk("a disallowed type is refused on add",
            one("SELECT COUNT(*) n FROM pr_quotes WHERE vendor='Gamma'")["n"] == 0
            and translate("Unsupported quote file type.", "ar") in f, f)
        big = io.BytesIO(b"0" * (3 * 1024 * 1024 + 1))
        r, f = post("/procurement/pr/%d/quote" % p1, vendor="Gamma", amount="1",
                    payment_terms="Cash", file=(big, "big.pdf"))
        chk("an oversize file is refused on add",
            one("SELECT COUNT(*) n FROM pr_quotes WHERE vendor='Gamma'")["n"] == 0
            and translate("Quote file too large (max 3 MB).", "ar") in f, f)

        # ------------------------------------------------------------- 2 ----
        print("\n2. a quote with no file cannot be chosen")
        lg = legacy(p1, "Legacy", 800)
        chk("the service refuses", svc.choose_quote(p1, lg, buyer) == (False, "no_attachment"))
        r, f = post("/procurement/pr/%d/quote/%d/choose" % (p1, lg))
        chk("the route refuses, translated",
            not one("SELECT is_chosen c FROM pr_quotes WHERE id=?", lg)["c"]
            and translate("This quotation has no supplier file. Attach it (Edit this "
                          "quotation) before choosing it.", "ar") in f, f)

        html = cli.get("/procurement/pr/%d" % p1).get_data(as_text=True)
        chk("the page shows the 'No attachment' badge", 'data-i18n="proc.quote.no_file"' in html)
        chk("and no Choose button for it",
            "/quote/%d/choose" % lg not in html and "/quote/%d/choose" % qa["id"] in html)
        chk("the add form's file input is required",
            'name="file" required' in html and 'data-i18n="proc.quote.file_hint"' in html)
        chk("the edit form can attach a file",
            'data-i18n="proc.quote.attach_file"' in html and 'enctype="multipart/form-data"' in html)

        print("\n5. a legacy quote gets its file via edit, then can be chosen")
        r, f = post("/procurement/pr/%d/quote/%d/edit" % (p1, lg),
                    file=(io.BytesIO(b"MZ"), "x.exe"))
        chk("edit refuses a disallowed type",
            not one("SELECT content_b64 c FROM pr_quotes WHERE id=?", lg)["c"]
            and translate("Unsupported quote file type.", "ar") in f, f)
        r, f = post("/procurement/pr/%d/quote/%d/edit" % (p1, lg), warranty="1 year")
        chk("editing it without a file is allowed and it stays without one",
            one("SELECT warranty w, content_b64 c FROM pr_quotes WHERE id=?", lg)["w"] == "1 year"
            and not one("SELECT content_b64 c FROM pr_quotes WHERE id=?", lg)["c"], f)
        r, f = post("/procurement/pr/%d/quote/%d/edit" % (p1, lg),
                    file=(io.BytesIO(b"%PDF-1.4 legacy"), "legacy.pdf"))
        chk("the edit route attaches the file",
            one("SELECT filename f FROM pr_quotes WHERE id=?", lg)["f"] == "legacy.pdf", f)
        ok, why = svc.choose_quote(p1, lg, buyer)
        chk("and now it can be chosen", ok, why)
        # Owner 2026-11-24: the chosen quotation can be corrected (file too)
        # while the request is open; the route then re-applies it.
        chk("a chosen quote's file can be replaced while the request is open",
            svc.update_quote(lg, {}, user=buyer, **QF) == (True, ""))

        # ------------------------------------------------------------- 3 ----
        print("\n3. Purchasing cannot approve on a chosen quote with no file")
        p3 = new_pr("Chosen legacy", 1000)
        svc.add_quote(p3, {"vendor": "Filed", "amount": 990, "payment_terms": "30 days"},
                      buyer, **QF)
        l3 = legacy(p3, "Old Chosen", 950)
        run_sql("UPDATE pr_quotes SET is_chosen=1 WHERE id=?", l3)  # chosen before the rule
        chk("the request reaches the Purchasing rung",
            walk_to_purchasing(p3) == C.PRICING_GATE_STAGE)
        chk("the RFQ gate itself is satisfied (one filed quote)", gate(p3) == (True, ""), gate(p3))
        res = svc.act_on_step(p3, buyer, "approve")
        chk("the approval is refused", res == (False, "chosen_quote_no_file"), res)
        chk("the refusal is translated", translate(
            "The chosen quotation has no supplier file. Attach it in the Vendor quotes "
            "panel before approving.", "ar") != "The chosen quotation has no supplier "
            "file. Attach it in the Vendor quotes panel before approving.")
        flask.g.pop("user", None)
        html = cli.get("/procurement/pr/%d" % p3).get_data(as_text=True)
        chk("the page offers an attach-only form for the chosen legacy quote",
            "/quote/%d/edit" % l3 in html)
        chk("a chosen legacy quote may receive its file (attach only)",
            svc.update_quote(l3, {}, user=buyer, **QF) == (True, ""))
        res = svc.act_on_step(p3, buyer, "approve")
        chk("then Purchasing can approve", res[0], res)

        # ------------------------------------------------------------- 4 ----
        print("\n4. only filed quotes count toward the RFQ gate")
        need = int(C.quotes_required(600000))
        p4 = new_pr("Gate count", 600000)
        for i in range(need - 1):
            svc.add_quote(p4, {"vendor": "V%d" % i, "amount": 600000 + i,
                               "payment_terms": "30 days"}, buyer, **QF)
        l4 = legacy(p4, "Unfiled", 600500)
        chk("%d filed + 1 unfiled is short of %d" % (need - 1, need),
            gate(p4) == (False, "needs_quotes"), gate(p4))
        svc.update_quote(l4, {}, user=buyer, **QF)
        chk("filing the last one satisfies the gate", gate(p4) == (True, ""), gate(p4))

        p5 = new_pr("Single source", 600000)
        run_sql("UPDATE pr_requests SET single_source_reason='Sole OEM' WHERE id=?", p5)
        chk("single source with no quote recorded still passes", gate(p5) == (True, ""))
        l5 = legacy(p5, "Sole OEM Ltd", 600000)
        chk("single source whose only quote has no file is refused",
            gate(p5) == (False, "needs_quote_file"), gate(p5))
        svc.update_quote(l5, {}, user=buyer, **QF)
        chk("and passes once the file is attached", gate(p5) == (True, ""), gate(p5))
        flask.g.pop("user", None)

    print("\nRESULT: " + ("ALL GREEN" if OK[0] else "FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
