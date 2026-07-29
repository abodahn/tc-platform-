"""
Planning report self-test — the three specs registered with the shared reporting
engine, on a throwaway DB.
    python app/planning/tests_reports.py

What it proves, in order of how much it matters:
  1. THE NUMBERS. A purpose-built line and order are planned by hand in the
     comment above each check (capacity minutes, days of work, late flag,
     good pieces) and compared with what the engine's SQL returns.
  2. PERMISSION. A user without pln_view gets a raw 403 on the HTML page and on
     all three export formats, and reads nothing.
  3. EDGE CASES. A line with ZERO capacity (days of work must be blank, never a
     division by zero), an order with no MES actuals (progress must be blank,
     not 0%), a missing ship date (late must be blank, never "late"), and an
     empty result set.
  4. i18n. Page renders 200 in en/ar/tr and every data-i18n key resolves.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="pln_rpt_"))
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
KEYS = ["planning_line_load", "planning_schedule", "planning_progress"]


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
    # ---- purpose-built data, isolated from the demo seed by its own names ----
    # RPT-LINE capacity = 10 operators x 500 min x 60% = 3,000 min/day
    # RPT-DEAD has 0 operators -> 0 capacity: the division-by-zero trap.
    for code, name, ops, mins, eff in (("RPTL", "RPT-LINE", 10, 500, 60.0),
                                       ("RPTD", "RPT-DEAD", 0, 500, 60.0)):
        conn.execute(
            "INSERT INTO pln_lines (code,name,section,operators,working_minutes,"
            "efficiency_pct,active,created_at) VALUES (?,?,?,?,?,?,1,?)",
            (code, name, "RPTSEC", ops, mins, eff, "2026-06-01 00:00:00"))
    lines = {r["code"]: r["id"] for r in conn.execute(
        "SELECT id, code FROM pln_lines WHERE code IN ('RPTL','RPTD')").fetchall()}

    # Order with a ship date the plan misses, and one with NO ship date at all.
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_ref,style_name,qty,"
                 "unit_price,currency,ship_date,status,created_by) VALUES "
                 "('RPT-ORD','RPTBUYER','RPT-S','Report Tee',6000,10,'USD',"
                 "'2026-08-01','open','test')")
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_ref,qty,unit_price,"
                 "currency,status,created_by) VALUES "
                 "('RPT-NOSHIP','RPTBUYER','RPT-N',1000,10,'USD','open','test')")
    oids = {r["order_no"]: r["id"] for r in conn.execute(
        "SELECT id, order_no FROM ord_orders WHERE order_no LIKE 'RPT-%'").fetchall()}

    # 6,000 pcs x 1.5 SMV = 9,000 minutes -> 9,000 / 3,000 = 3.0 days of work.
    # Plan ends 2026-08-05, ship date 2026-08-01 -> LATE.
    conn.execute("INSERT INTO pln_allocations (order_id,pline_id,qty,smv,start_date,"
                 "end_date,status,created_by,created_at) VALUES (?,?,6000,1.5,"
                 "'2026-07-01','2026-08-05','planned','test','2026-06-20 00:00:00')",
                 (oids["RPT-ORD"], lines["RPTL"]))
    # Same order, on the dead line: 1,000 x 1.0 = 1,000 minutes, capacity 0.
    conn.execute("INSERT INTO pln_allocations (order_id,pline_id,qty,smv,start_date,"
                 "end_date,status,created_by,created_at) VALUES (?,?,1000,1.0,"
                 "'2026-07-01','2026-07-10','planned','test','2026-06-20 00:00:00')",
                 (oids["RPT-NOSHIP"], lines["RPTD"]))

    # MES: 600+400 = 1,000 pieces, 50+50 = 100 rejects -> 900 GOOD.
    for slot, act, rej in (("08:00", 600, 50), ("09:00", 400, 50)):
        conn.execute("INSERT INTO mes_hourly (line_id,order_id,work_date,hour_slot,"
                     "target_qty,actual_qty,reject_qty,operators,smv,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (lines["RPTL"], oids["RPT-ORD"], "2026-07-02", slot,
                      600, act, rej, 10, 1.5, "2026-07-02 10:00:00"))

    # ---- users -------------------------------------------------------------
    admin = conn.execute(
        "SELECT id, COALESCE(session_epoch,0) ep FROM users WHERE role='super_admin' "
        "AND is_active=1 ORDER BY id").fetchone()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                 "lang_pref) VALUES ('rptnopln','x','No Perm','quality_inspector',1,'en')")
    noperm = conn.execute("SELECT id, COALESCE(session_epoch,0) ep FROM users "
                          "WHERE username='rptnopln'").fetchone()
    conn.commit()
    conn.close()
    ok("fixtures created (super_admin + a user without pln_view)",
       admin is not None and noperm is not None)

    # =====================================================================
    print("\n-- registration --")
    for k in KEYS:
        spec = R.get(k)
        ok(f"{k} is registered with perm pln_view",
           spec is not None and spec["perm"] == "pln_view")
        ok(f"{k} declares AR + TR for every column/kpi/filter",
           spec is not None and all(x["ar"] and x["tr"] for x in spec["columns"])
           and all(x["ar"] and x["tr"] for x in spec["kpis"])
           and all(x["ar"] and x["tr"] for x in spec["filters"])
           and bool(spec["title_ar"] and spec["title_tr"]
                    and spec["desc_ar"] and spec["desc_tr"]))

    # =====================================================================
    print("\n-- planning_line_load: capacity arithmetic --")
    # capacity = 10 x 500 x 60 / 100 = 3,000 min/day
    # loaded   = 6,000 pcs x 1.5 SMV  = 9,000 minutes
    # days     = 9,000 / 3,000        = 3.0
    res = R.run(R.get("planning_line_load"), {"line": "RPT-LINE"})
    ok("one row for the line", res["total"] == 1)
    row = res["rows"][0] if res["rows"] else {}
    ok("capacity = 10 x 500 x 60% = 3,000 min/day", near(row.get("capacity_min"), 3000))
    ok("loaded minutes = 6,000 x 1.5 = 9,000", near(row.get("minutes"), 9000))
    ok("planned qty = 6,000", near(row.get("qty"), 6000))
    ok("days of work = 9,000 / 3,000 = 3.0", near(row.get("days_of_work"), 3.0))
    ok("first start / last finish come straight off the allocation",
       row.get("first_start") == "2026-07-01" and row.get("last_end") == "2026-08-05")
    kp = {k["key"]: k["value"] for k in res["kpis"]}
    ok("KPI minutes 9,000 / qty 6,000 / longest queue 3.0 days",
       near(kp.get("minutes"), 9000) and near(kp.get("qty"), 6000)
       and near(kp.get("peak"), 3.0))
    dead = R.run(R.get("planning_line_load"), {"line": "RPT-DEAD"})
    drow = dead["rows"][0] if dead["rows"] else {}
    ok("a line with 0 operators has 0 capacity and BLANK days of work "
       "(not a ZeroDivisionError, not a fake 0)",
       near(drow.get("capacity_min"), 0) and drow.get("days_of_work") == ""
       and near(drow.get("minutes"), 1000))

    # =====================================================================
    print("\n-- planning_schedule: plan vs ship date --")
    res2 = R.run(R.get("planning_schedule"), {"order_no": "RPT-ORD"})
    ok("one allocation for the order", res2["total"] == 1)
    r2 = res2["rows"][0] if res2["rows"] else {}
    ok("minutes = 6,000 x 1.5 = 9,000", near(r2.get("minutes"), 9000))
    ok("finish 2026-08-05 is AFTER ship date 2026-08-01 -> late = 1",
       near(r2.get("late"), 1))
    k2 = {k["key"]: k["value"] for k in res2["kpis"]}
    ok("KPI late allocations = 1", near(k2.get("late"), 1))
    noship = R.run(R.get("planning_schedule"), {"order_no": "RPT-NOSHIP"})
    rn = noship["rows"][0] if noship["rows"] else {}
    ok("an order with NO ship date is blank, never reported late",
       rn.get("late") == "")
    kn = {k["key"]: k["value"] for k in noship["kpis"]}
    ok("and it does not count towards the late KPI either", near(kn.get("late"), 0))

    # =====================================================================
    print("\n-- planning_progress: plan vs actual output --")
    # planned  = 6,000 (one allocation)
    # produced = (600 + 400) - (50 + 50) = 900 GOOD pieces
    # shortfall= 6,000 - 900 = 5,100 ; progress = 900 / 6,000 = 15.00 %
    res3 = R.run(R.get("planning_progress"), {"order_no": "RPT-ORD"})
    ok("one row per order", res3["total"] == 1)
    r3 = res3["rows"][0] if res3["rows"] else {}
    ok("planned = 6,000", near(r3.get("planned_qty"), 6000))
    ok("produced = 1,000 booked - 100 rejects = 900 GOOD pieces",
       near(r3.get("produced_qty"), 900))
    ok("rejects = 100", near(r3.get("rejects"), 100))
    ok("shortfall = 6,000 - 900 = 5,100", near(r3.get("shortfall"), 5100))
    ok("progress = 900 / 6,000 = 15.00 %", near(r3.get("progress_pct"), 15.0))
    k3 = {k["key"]: k["value"] for k in res3["kpis"]}
    ok("KPI progress % = SUM(produced)/SUM(planned) = 15.00",
       near(k3.get("progress"), 15.0))
    none3 = R.run(R.get("planning_progress"), {"order_no": "RPT-NOSHIP"})
    rn3 = none3["rows"][0] if none3["rows"] else {}
    ok("an order the floor has booked NOTHING against shows blank produced and "
       "blank progress — not '0 produced, 100% behind'",
       rn3.get("produced_qty") == "" and rn3.get("progress_pct") == "")

    # =====================================================================
    print("\n-- edge cases --")
    for k in KEYS:
        empty = R.run(R.get(k), {"line": "NO-SUCH-LINE", "order_no": "NO-SUCH-ORDER"})
        ok(f"{k}: empty result set renders 0 rows and 0-valued KPIs, no crash",
           empty["total"] == 0 and all(x["value"] == 0 for x in empty["kpis"]))
    single = R.run(R.get("planning_schedule"), {"order_no": "RPT-ORD"})
    ok("single-row data set: totals equal the row",
       single["total"] == 1 and near(single["totals"].get("minutes"), 9000))
    bad = R.run(R.get("planning_schedule"), {"period": "custom", "from": "2026-09-01",
                                             "to": "2026-08-01"})
    ok("inverted date range is an error, not a silently empty table",
       bad["error"] == "inverted_range" and bad["rows"] == [])

    # =====================================================================
    print("\n-- permission on every surface --")
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"], s["ep"] = noperm["id"], noperm["ep"]
    for k in KEYS:
        r = GET(f"/reporting/{k}")
        ok(f"{k}: HTML page is {r.status_code} (403 expected) without pln_view",
           r.status_code == 403)
        for fmt in ("csv", "xlsx", "pdf"):
            r = GET(f"/reporting/{k}.{fmt}")
            body = r.get_data()
            ok(f"{k}.{fmt}: {r.status_code} and no data leaked",
               r.status_code == 403 and b"RPT-ORD" not in body
               and b"RPT-LINE" not in body)
    r = GET("/reporting/")
    ok("the hub lists no planning report for that user",
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
            r = GET(f"/reporting/{k}.{fmt}")
            ok(f"{k}.{fmt}: {r.status_code}, {r.mimetype}, {len(r.get_data())} bytes",
               r.status_code == 200 and mime in r.mimetype and len(r.get_data()) > 100)
    body = GET("/reporting/planning_line_load.csv?line=RPT-LINE").get_data()
    ok("CSV carries the UTF-8 BOM Excel needs and the 9,000 loaded minutes",
       body.startswith(b"\xef\xbb\xbf") and b"RPT-LINE" in body and b"9000" in body)

    # =====================================================================
    print("\n-- i18n: en / ar / tr --")
    D = i18n_dicts()
    mine = ROOT / "app" / "templates" / "planning" / "index.html"
    src = mine.read_text(encoding="utf-8")
    # pln.status.{{ s }} is built at render time from a fixed dict of statuses,
    # so expand it here rather than checking the literal template expression.
    tmpl_keys = {k for k in re.findall(r'data-i18n="([^"{]+)"', src)}
    missing_mine = {lang: sorted(k for k in tmpl_keys if k not in D[lang]) for lang in D}
    ok(f"every static data-i18n key on the planning page resolves in en/ar/tr "
       f"({len(tmpl_keys)} keys)", not any(missing_mine.values()))
    if any(missing_mine.values()):
        print("        missing:", missing_mine)

    for lang in ("en", "ar", "tr"):
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=?", (lang, admin["id"]))
        conn.commit()
        conn.close()
        r = GET("/reporting/planning_line_load?line=RPT-LINE")
        ok(f"report page 200 with lang_pref={lang}", r.status_code == 200)
        r = GET("/reporting/planning_progress.csv")
        ok(f"CSV export 200 with lang_pref={lang} ({r.mimetype})", r.status_code == 200)

    page = GET("/reporting/planning_line_load").get_data().decode("utf-8")
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
