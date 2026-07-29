"""
Procurement report self-test — the four specs registered with the shared
reporting engine, on a throwaway DB.
    python app/approvals/tests_reports.py

What it proves, in order of how much it matters:
  1. THE NUMBERS. Purpose-built purchase requests are added up by hand in the
     comment above each check (tax, FX, budget, 3-way-match tolerance) and
     compared with what the engine's SQL returns.
  2. PERMISSION. A user without proc_view gets a raw 403 on the HTML page and on
     all three export formats, and reads nothing.
  3. EDGE CASES. Empty result set, a single row, NULL tax_rate / fx_rate /
     paid_amount, a department with no budget row (remaining must not crash).
  4. i18n. Page renders 200 in en/ar/tr and every data-i18n key resolves.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="proc_rpt_"))
os.chdir(TMP)                                       # Config.DB_PATH is repo-relative
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                       # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                          # noqa: E402
from app.services import reporting as R             # noqa: E402

app = create_app()
# The generic /reporting hub is owned by another lane and may not be registered
# in app/__init__.py yet. Register it here so the permission and export surfaces
# are genuinely exercised; once the orchestrator wires it up this is a no-op.
if "reports_hub" not in app.blueprints:
    from app.routes.reports_hub import bp as _hub_bp   # noqa: E402
    app.register_blueprint(_hub_bp)

PASS = []
KEYS = ["proc_requests", "proc_budget", "proc_waiting", "proc_payables"]
DEPT = "RPTDEPT"


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


def near(a, b, tol=0.01):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def GET(url):
    """A client GET that first drops the cached user.

    current_user() memoises on flask.g and the test client REUSES the app
    context this file holds open — without this pop every request after the
    first is answered as whoever logged in first, and the permission sweep
    would silently prove nothing.
    """
    from flask import g
    g.pop("user", None)
    return c.get(url)


def i18n_dicts():
    out = {}
    for lang in ("en", "ar", "tr"):
        with open(ROOT / "app" / "static" / "i18n" / f"{lang}.json", encoding="utf-8") as fh:
            out[lang] = json.load(fh)
    return out


with app.app_context():
    from app.db import get_db

    conn = get_db()
    # ---- purpose-built data, isolated from the demo seed by its department ----
    # A: EGP, pending, 1000 net + 10% tax = 1100 gross, fx 1  -> 1100 EGP
    # B: USD, po_issued, 2000 net + 0% tax = 2000 gross, fx 2  -> 4000 EGP
    #    paid 500 USD -> 1000 EGP
    # C: EGP, draft, 500 net, NULL tax_rate / NULL fx_rate     ->  500 EGP
    #    (C is the NULL-heavy row: COALESCE must treat it as 0% tax and FX 1)
    prs = [
        ("RPT-PR-1", "Pending one", "RPTVEND", "EGP", 1000.0, 10.0, 1.0, "pending",
         0.0, None, "unpaid"),
        ("RPT-PR-2", "Ordered one", "RPTVEND2", "USD", 2000.0, 0.0, 2.0, "po_issued",
         500.0, "2026-07-20", "partial"),
        ("RPT-PR-3", "Draft one", "RPTVEND", "EGP", 500.0, None, None, "draft",
         None, None, None),
    ]
    for no, title, vendor, ccy, total, tax, fx, status, paid, due, pay in prs:
        conn.execute(
            "INSERT INTO pr_requests (pr_no,title,requester,department,request_date,"
            "currency,vendor,total,tax_rate,fx_rate,status,paid_amount,due_date,"
            "payment_status,is_active,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (no, title, "test", DEPT, "2026-07-01", ccy, vendor, total, tax, fx,
             status, paid, due, pay, 1, "2026-07-01 09:00:00"))
    ids = {r["pr_no"]: r["id"] for r in conn.execute(
        "SELECT id, pr_no FROM pr_requests WHERE department=?", (DEPT,)).fetchall()}
    # Budget for the department: 10,000 EGP for 2026.
    conn.execute("INSERT INTO proc_budgets (department,period,currency,amount,created_at) "
                 "VALUES (?,?,?,?,?)", (DEPT, "2026", "EGP", 10000.0, "2026-01-01"))
    # Approval ladder on A: rung 1 is ACTIVE (activated_at set), rung 2 is not.
    conn.execute("INSERT INTO pr_steps (pr_id,seq,stage,status,approver_role,"
                 "activated_at,created_at) VALUES (?,1,'warehouse','pending',"
                 "'Warehouse','2026-07-02 08:00:00','2026-07-01 09:00:00')",
                 (ids["RPT-PR-1"],))
    conn.execute("INSERT INTO pr_steps (pr_id,seq,stage,status,approver_role,"
                 "created_at) VALUES (?,2,'finance','pending','Finance',"
                 "'2026-07-01 09:00:00')", (ids["RPT-PR-1"],))
    # B: ordered 10 @ 100 = 1000 in line value, only 6 received -> 600 received value,
    #    4 short. One invoice for 2200 gross.
    conn.execute("INSERT INTO pr_items (pr_id,seq,item,unit,qty,received_qty,"
                 "unit_price,est_cost) VALUES (?,1,'Widget','Pcs',10,6,100,1000)",
                 (ids["RPT-PR-2"],))
    conn.execute("INSERT INTO pr_invoices (pr_id,invoice_no,invoice_date,amount,tax,"
                 "currency,status,created_at) VALUES (?,'RPT-INV-1','2026-07-05',"
                 "2200,0,'USD','received','2026-07-05 10:00:00')", (ids["RPT-PR-2"],))

    # ---- users -------------------------------------------------------------
    admin = conn.execute(
        "SELECT id, COALESCE(session_epoch,0) ep FROM users WHERE role='super_admin' "
        "AND is_active=1 ORDER BY id").fetchone()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                 "lang_pref) VALUES ('rptnoproc','x','No Perm','quality_inspector',1,'en')")
    noperm = conn.execute("SELECT id, COALESCE(session_epoch,0) ep FROM users "
                          "WHERE username='rptnoproc'").fetchone()
    conn.commit()
    conn.close()
    ok("fixtures created (super_admin + a user without proc_view)",
       admin is not None and noperm is not None)

    # =====================================================================
    print("\n-- registration --")
    for k in KEYS:
        spec = R.get(k)
        ok(f"{k} is registered with perm proc_view",
           spec is not None and spec["perm"] == "proc_view")
        ok(f"{k} declares AR + TR for every column/kpi/filter",
           spec is not None and all(x["ar"] and x["tr"] for x in spec["columns"])
           and all(x["ar"] and x["tr"] for x in spec["kpis"])
           and all(x["ar"] and x["tr"] for x in spec["filters"])
           and bool(spec["title_ar"] and spec["title_tr"]
                    and spec["desc_ar"] and spec["desc_tr"]))

    # =====================================================================
    print("\n-- proc_requests: tax + FX arithmetic --")
    res = R.run(R.get("proc_requests"), {"department": DEPT})
    ok("3 requests in the department", res["total"] == 3)
    by = {r["pr_no"]: r for r in res["rows"]}
    ok("A gross = 1000 + 10% = 1,100.00", near(by.get("RPT-PR-1", {}).get("grand"), 1100))
    ok("A EGP  = 1100 x fx 1 = 1,100.00", near(by.get("RPT-PR-1", {}).get("egp"), 1100))
    ok("B gross = 2000 + 0% = 2,000.00", near(by.get("RPT-PR-2", {}).get("grand"), 2000))
    ok("B EGP  = 2000 x fx 2 = 4,000.00", near(by.get("RPT-PR-2", {}).get("egp"), 4000))
    ok("C with NULL tax_rate and NULL fx_rate = 500.00, not NULL",
       near(by.get("RPT-PR-3", {}).get("grand"), 500)
       and near(by.get("RPT-PR-3", {}).get("egp"), 500))
    kp = {k["key"]: k["value"] for k in res["kpis"]}
    ok("KPI total value = 1100 + 4000 + 500 = 5,600.00 EGP", near(kp.get("value"), 5600))
    # "Committed" is the SAME status set services.budget_status() gates against:
    # approved and beyond. A is still PENDING in the ladder and has committed
    # nothing; only B (po_issued) has. Counting A here made the report disagree
    # with the budget check that actually blocks a submission.
    ok("KPI committed = B only = 4,000.00 EGP (draft AND pending excluded)",
       near(kp.get("committed"), 4000))
    ok("KPI awaiting approval = 1", near(kp.get("waiting"), 1))
    # An EGP request that still carries the fx_rate of a currency it was priced
    # in earlier (services.price_pr never resets fx_rate when the currency is
    # changed back) must NOT be multiplied — egp_total() short-circuits on EGP
    # and so must the report. This read 30x too big before the guard.
    conn = get_db()
    conn.execute("UPDATE pr_requests SET fx_rate=30 WHERE pr_no='RPT-PR-3'")
    conn.commit(); conn.close()
    stale = R.run(R.get("proc_requests"), {"department": DEPT, "status": "draft"})
    ok("EGP request carrying a stale fx_rate=30 is still 500.00 EGP, not 15,000",
       near(stale["rows"][0].get("egp"), 500))
    conn = get_db()
    conn.execute("UPDATE pr_requests SET fx_rate=NULL WHERE pr_no='RPT-PR-3'")
    conn.commit(); conn.close()
    ok("column total for EGP value = 5,600.00", near(res["totals"].get("egp"), 5600))
    ok("supplier pareto has 2 bars and a cumulative line ending at 100%",
       res["chart"] is not None and len(res["chart"]["labels"]) == 2
       and near(res["chart"]["cumulative"][-1], 100.0, 0.1))

    # =====================================================================
    print("\n-- proc_budget: budget vs committed vs spent --")
    # budget 10,000 ; committed 4,000 (B only) ; paid 500 USD x fx 2 = 1,000 EGP ;
    # remaining 6,000
    resb = R.run(R.get("proc_budget"), {"department": DEPT})
    ok("one row: one department, one year", resb["total"] == 1)
    rb = resb["rows"][0] if resb["rows"] else {}
    ok("year is taken from the request date = 2026", rb.get("period") == "2026")
    ok("budget = 10,000.00", near(rb.get("budget"), 10000))
    ok("requests = 3", near(rb.get("requests"), 3))
    ok("committed = B only = 4,000.00 (draft and pending excluded)",
       near(rb.get("committed"), 4000))
    ok("paid = 500 USD x fx 2 = 1,000.00 EGP", near(rb.get("spent"), 1000))
    ok("remaining = 10,000 - 4,000 = 6,000.00", near(rb.get("remaining"), 6000))
    kb = {k["key"]: k["value"] for k in resb["kpis"]}
    ok("KPIs repeat the single group exactly",
       near(kb.get("budget"), 10000) and near(kb.get("committed"), 4000)
       and near(kb.get("spent"), 1000) and near(kb.get("remaining"), 6000))
    # A department that holds a budget and has raised NO request is the single
    # most important row on a budget report, and the obvious
    # `pr_requests LEFT JOIN budgets` dropped it — under-reporting total budget.
    conn = get_db()
    conn.execute("INSERT INTO proc_budgets (department,period,currency,amount,created_at) "
                 "VALUES ('RPTUNSPENT','2026','EGP',25000,'2026-01-01')")
    conn.commit(); conn.close()
    unspent = [r for r in R.run(R.get("proc_budget"), {}) ["rows"]
               if r["department"] == "RPTUNSPENT"]
    ok("a budgeted department with no requests is listed: 0 committed, 25,000 remaining",
       len(unspent) == 1 and near(unspent[0]["requests"], 0)
       and near(unspent[0]["committed"], 0) and near(unspent[0]["remaining"], 25000))
    # Two budget rows for the same department+year add up ONCE (proc_budgets has
    # no unique key), and must not fan the PR rows out through the join.
    conn = get_db()
    conn.execute("INSERT INTO proc_budgets (department,period,currency,amount,created_at) "
                 "VALUES (?,'2026','EGP',5000,'2026-01-01')", (DEPT,))
    conn.commit(); conn.close()
    dup = R.run(R.get("proc_budget"), {"department": DEPT})
    ok("duplicate budget rows sum to 15,000 and committed stays 4,000 (no fan-out)",
       dup["total"] == 1 and near(dup["rows"][0]["budget"], 15000)
       and near(dup["rows"][0]["committed"], 4000)
       and near(dup["rows"][0]["requests"], 3))
    ok("the Year filter narrows to one year without a date range",
       R.run(R.get("proc_budget"), {"department": DEPT, "year": "1999"})["total"] == 0
       and R.run(R.get("proc_budget"), {"department": DEPT, "year": "2026"})["total"] == 1)
    # A department with NO budget row must report 0 budget and a NEGATIVE
    # remaining, not a NULL that swallows the whole row.
    conn = get_db()
    conn.execute("INSERT INTO pr_requests (pr_no,title,requester,department,"
                 "request_date,currency,total,status,is_active,created_at) "
                 "VALUES ('RPT-PR-9','No budget','test','RPTNOBUDGET','2026-07-01',"
                 "'EGP',700,'approved',1,'2026-07-01 09:00:00')")
    conn.commit()
    conn.close()
    resn = R.run(R.get("proc_budget"), {"department": "RPTNOBUDGET"})
    rn = resn["rows"][0] if resn["rows"] else {}
    ok("department with no budget row: budget 0, committed 700, remaining -700",
       near(rn.get("budget"), 0) and near(rn.get("committed"), 700)
       and near(rn.get("remaining"), -700))

    # =====================================================================
    print("\n-- proc_waiting: only the ACTIVE unsigned rung --")
    resw = R.run(R.get("proc_waiting"), {"department": DEPT})
    ok("1 waiting step (rung 2 has no activated_at, so it is not waiting yet)",
       resw["total"] == 1)
    rw = resw["rows"][0] if resw["rows"] else {}
    ok("it is rung 1 at the warehouse stage",
       rw.get("seq") == 1 and rw.get("stage") == "warehouse")
    ok("value held up = 1,100.00 EGP", near(rw.get("egp"), 1100))
    kw = {k["key"]: k["value"] for k in resw["kpis"]}
    ok("KPI steps=1, value=1100, requests affected=1",
       near(kw.get("steps"), 1) and near(kw.get("value"), 1100)
       and near(kw.get("prs"), 1))

    # =====================================================================
    print("\n-- proc_payables: 3-way-match exposure on B --")
    # PO gross 2,000.00 ; tolerance = max(1, 1% of 2000) = 20.00
    # invoiced 2,200.00 -> over-billed by 2200 - 2000 = 200.00 (2200 > 2020)
    # received value 6 x 100 = 600.00 -> invoiced over receipt 2200 - 600 = 1,600.00
    # short qty = 10 ordered - 6 received = 4
    # outstanding = 2000 - 500 paid = 1,500.00
    resp = R.run(R.get("proc_payables"), {"vendor": "RPTVEND2"})
    ok("only the PO-stage request appears", resp["total"] == 1)
    rp = resp["rows"][0] if resp["rows"] else {}
    ok("ordered (gross) = 2,000.00", near(rp.get("grand"), 2000))
    ok("invoiced = 2,200.00", near(rp.get("invoiced"), 2200))
    ok("received value = 6 x 100 = 600.00", near(rp.get("received_value"), 600))
    ok("paid = 500.00 ; outstanding = 2000 - 500 = 1,500.00",
       near(rp.get("paid"), 500) and near(rp.get("outstanding"), 1500))
    ok("over-billed = 2200 - 2000 = 200.00 (tolerance 20.00 exceeded)",
       near(rp.get("over_billed"), 200))
    ok("invoiced over receipt = 2200 - 600 = 1,600.00",
       near(rp.get("over_received"), 1600))
    ok("short qty = 10 - 6 = 4", near(rp.get("short_qty"), 4))
    kpp = {k["key"]: k["value"] for k in resp["kpis"]}
    ok("KPI match exceptions = 1", near(kpp.get("exceptions"), 1))
    # Tolerance really is applied: a 10.00 over-bill on a 2,000.00 PO is inside
    # the 20.00 tolerance and must report 0, not 10.
    conn = get_db()
    conn.execute("UPDATE pr_invoices SET amount=2010 WHERE invoice_no='RPT-INV-1'")
    conn.commit()
    conn.close()
    tol = R.run(R.get("proc_payables"), {"vendor": "RPTVEND2"})
    ok("a 10.00 over-bill is inside the 20.00 tolerance -> over-billed 0.00",
       near(tol["rows"][0].get("over_billed"), 0))
    conn = get_db()
    conn.execute("UPDATE pr_invoices SET amount=2200 WHERE invoice_no='RPT-INV-1'")
    conn.commit()
    conn.close()

    # An open PO with NO invoice on file is not a match exception: three_way_match
    # calls that state 'pending', not 'mismatch'. Without the guard the KPI counted
    # every freshly issued PO (short qty = the whole order) and read as if the
    # whole book were broken.
    conn = get_db()
    conn.execute("INSERT INTO pr_requests (pr_no,title,department,vendor,status,currency,"
                 "total,tax_rate,fx_rate,paid_amount,payment_status,created_at,is_active) "
                 "VALUES ('RPT-FRESH','Fresh PO','RPTDEPT','RPTVEND9','po_issued','EGP',"
                 "5000,0,1,0,'unpaid','2026-01-01 00:00:00',1)")
    _fid = conn.execute("SELECT id FROM pr_requests WHERE pr_no='RPT-FRESH'").fetchone()["id"]
    conn.execute("INSERT INTO pr_items (pr_id,seq,item,qty,received_qty,unit_price) "
                 "VALUES (?,1,'Nothing yet',100,0,50)", (_fid,))
    # ... and an OVERPAID request, whose negative outstanding must not be fed to
    # the pareto (a cumulative % that passes 100 and comes back down is fiction).
    conn.execute("INSERT INTO pr_requests (pr_no,title,department,vendor,status,currency,"
                 "total,tax_rate,fx_rate,paid_amount,payment_status,created_at,is_active) "
                 "VALUES ('RPT-OVER','Overpaid','RPTDEPT','RPTVEND8','received','EGP',"
                 "1000,0,1,1500,'paid','2026-01-01 00:00:00',1)")
    conn.commit()
    conn.close()
    fresh = R.run(R.get("proc_payables"), {"vendor": "RPTVEND9"})
    kf = {k["key"]: k["value"] for k in fresh["kpis"]}
    ok("open PO, 100 short, no invoice -> short qty 100 but 0 match exceptions",
       near(fresh["rows"][0].get("short_qty"), 100) and near(kf.get("exceptions"), 0))
    over = R.run(R.get("proc_payables"), {"vendor": "RPTVEND8"})
    ok("overpaid request keeps its negative outstanding in the column",
       near(over["rows"][0].get("outstanding"), -500))
    allp = R.run(R.get("proc_payables"), {})
    _cum = (allp["chart"] or {}).get("cumulative") or []
    ok("outstanding pareto stays monotonic and ends at 100% despite the overpayment",
       bool(_cum) and all(b >= a - 0.01 for a, b in zip(_cum, _cum[1:]))
       and near(_cum[-1], 100))
    conn = get_db()
    conn.execute("DELETE FROM pr_items WHERE pr_id=?", (_fid,))
    conn.execute("DELETE FROM pr_requests WHERE pr_no IN ('RPT-FRESH','RPT-OVER')")
    conn.commit()
    conn.close()

    # =====================================================================
    print("\n-- edge cases --")
    for k in KEYS:
        empty = R.run(R.get(k), {"department": "NO-SUCH-DEPARTMENT",
                                 "vendor": "NO-SUCH-VENDOR"})
        ok(f"{k}: empty result set renders 0 rows and 0-valued KPIs, no crash",
           empty["total"] == 0 and all(x["value"] == 0 for x in empty["kpis"]))
    single = R.run(R.get("proc_requests"), {"department": DEPT, "status": "pending"})
    ok("single-row data set is fine (1 row, totals equal the row)",
       single["total"] == 1 and near(single["totals"].get("egp"), 1100))
    bad = R.run(R.get("proc_requests"), {"period": "custom", "from": "not-a-date"})
    ok("garbage date is an error, not a crash and not a silent empty table",
       bad["error"] == "bad_date")

    # =====================================================================
    print("\n-- permission on every surface --")
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"], s["ep"] = noperm["id"], noperm["ep"]
    for k in KEYS:
        r = GET(f"/reporting/{k}")
        ok(f"{k}: HTML page is {r.status_code} (403 expected) without proc_view",
           r.status_code == 403)
        for fmt in ("csv", "xlsx", "pdf"):
            r = GET(f"/reporting/{k}.{fmt}")
            body = r.get_data()
            ok(f"{k}.{fmt}: {r.status_code} and no data leaked",
               r.status_code == 403 and b"RPT-PR-" not in body
               and b"RPTVEND" not in body)
    r = GET("/reporting/")
    ok("the hub lists no procurement report for that user",
       r.status_code == 200 and all(k.encode() not in r.get_data() for k in KEYS))

    # =====================================================================
    print("\n-- routes + exports for a permitted user --")
    with c.session_transaction() as s:
        s["uid"], s["ep"] = admin["id"], admin["ep"]
    for k in KEYS:
        r = GET(f"/reporting/{k}")
        ok(f"{k}: page 200", r.status_code == 200)
        for fmt, mime in (("csv", "text/csv"), ("xlsx", "application"),
                          ("pdf", "application/pdf")):
            r = GET(f"/reporting/{k}.{fmt}?department={DEPT}")
            ok(f"{k}.{fmt}: {r.status_code}, {r.mimetype}, {len(r.get_data())} bytes",
               r.status_code == 200 and mime in r.mimetype and len(r.get_data()) > 100)
    body = GET(f"/reporting/proc_requests.csv?department={DEPT}").get_data()
    ok("CSV carries the UTF-8 BOM Excel needs for Arabic and the EGP figures",
       body.startswith(b"\xef\xbb\xbf") and b"RPT-PR-1" in body and b"1100" in body)

    # =====================================================================
    print("\n-- i18n: en / ar / tr --")
    D = i18n_dicts()
    mine = ROOT / "app" / "templates" / "approvals" / "index.html"
    tmpl_keys = set(re.findall(r'data-i18n="([^"]+)"', mine.read_text(encoding="utf-8")))
    missing_mine = {lang: sorted(k for k in tmpl_keys if k not in D[lang]) for lang in D}
    ok(f"every data-i18n key on the procurement page resolves in en/ar/tr "
       f"({len(tmpl_keys)} keys)", not any(missing_mine.values()))
    if any(missing_mine.values()):
        print("        missing:", missing_mine)

    for lang in ("en", "ar", "tr"):
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=?", (lang, admin["id"]))
        conn.commit()
        conn.close()
        r = GET("/reporting/proc_requests?department=" + DEPT)
        ok(f"report page 200 with lang_pref={lang}", r.status_code == 200)
        r = GET("/reporting/proc_payables.csv")
        ok(f"CSV export 200 with lang_pref={lang} ({r.mimetype})", r.status_code == 200)

    page = GET("/reporting/proc_requests").get_data().decode("utf-8")
    foreign = sorted({k for k in re.findall(r'data-i18n="([^"]+)"', page)
                      if k not in D["en"]})
    ok(f"report-page template keys all resolve in en.json "
       f"({len(foreign)} missing — owned by the reporting-engine lane)", not foreign)
    if foreign:
        print("        MISSING (app/templates/reports/*.html, not this module):")
        print("        " + ", ".join(foreign))
    ok("report page carries data-loc-* for this module's own definition text",
       'data-loc-ar' in page)

print("\n%d/%d checks passed" % (sum(PASS), len(PASS)))
sys.exit(0 if all(PASS) else 1)
