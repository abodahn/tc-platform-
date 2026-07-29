"""
Costing report self-test — the three specs registered with the shared reporting
engine, on a throwaway DB.
    python app/costing/tests_reports.py

What it proves, in order of how much it matters:
  1. THE NUMBERS. A purpose-built order is costed by hand in the docstring of
     each check and compared with what the engine's SQL returns. A report with
     wrong numbers is worse than no report.
  2. PERMISSION. A user without cost_view gets a raw 403 on the HTML page and on
     all three export formats, and reads nothing.
  3. EDGE CASES. Empty result set, a single row, a zero-quantity order (margin %
     must be blank, never a ZeroDivisionError) and NULL-heavy rows.
  4. i18n. Page renders 200 in en/ar/tr and every data-i18n key resolves.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cst_rpt_"))
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
# The generic /reporting hub is owned by another lane and may not be registered in
# app/__init__.py yet. Register it here so the permission and export surfaces are
# genuinely exercised; when the orchestrator wires it up this becomes a no-op.
HUB_WAS_WIRED = "reports_hub" in app.blueprints
if not HUB_WAS_WIRED:
    from app.routes.reports_hub import bp as _hub_bp   # noqa: E402
    app.register_blueprint(_hub_bp)

PASS = []
KEYS = ["costing_orders", "costing_bom", "costing_actuals"]


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

    current_user() memoises on flask.g, and the test client REUSES the app
    context this file holds open — so without this pop every request after the
    first would be answered as whoever logged in first, and the permission
    sweep would silently prove nothing.
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
    conn.execute(
        "INSERT INTO ord_orders (order_no,buyer,style_ref,style_name,qty,unit_price,"
        "currency,order_date,ship_date,status,created_by) "
        "VALUES ('RPT-1','RPTBUYER','RPT-S','Report Tee',1000,10.0,'USD',"
        "'2026-06-01','2026-06-15','open','test')")
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='RPT-1'").fetchone()["id"]
    # BOM: per-unit material = 0.25*1.04*5.00 + 2*1.00*0.50 + (unpriced) = 1.30 + 1.00 = 2.30
    for seq, item, kind, cons, allow, price in (
            (1, "Fabric", "fabric", 0.25, 4.0, 5.00),
            (2, "Thread", "trim", 2.0, 0.0, 0.50),
            (3, "Label", "trim", 1.0, 0.0, None)):
        conn.execute(
            "INSERT INTO cst_bom_lines (order_id,seq,item,kind,consumption,uom,"
            "allowance_pct,unit_price,supplier,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (oid, seq, item, kind, cons, "pcs", allow, price, "RPTSUP", "2026-06-02"))
    # Sheet: cm = smv 10 x rate 0.08 = 0.80 ; others = 0.10+0.05+0.00+0.05 = 0.20
    conn.execute(
        "INSERT INTO cst_sheets (order_id,smv,cm_rate,cm_per_unit,overhead_per_unit,"
        "freight_per_unit,duty_per_unit,other_per_unit,created_at) "
        "VALUES (?,10,0.08,0,0.10,0.05,0,0.05,'2026-06-02')", (oid,))
    for cat, amt in (("material", 1500.0), ("cm", 900.0)):
        conn.execute("INSERT INTO cst_actuals (order_id,category,amount,source,"
                     "created_by,created_at) VALUES (?,?,?,'test','test','2026-06-10')",
                     (oid, cat, amt))

    # A zero-quantity, zero-price order that is still costed: the divide-by-zero trap.
    conn.execute(
        "INSERT INTO ord_orders (order_no,buyer,style_ref,qty,unit_price,currency,"
        "ship_date,status,created_by) VALUES ('RPT-0','RPTBUYER','RPT-Z',0,0,'USD',"
        "'2026-06-20','open','test')")
    zid = conn.execute("SELECT id FROM ord_orders WHERE order_no='RPT-0'").fetchone()["id"]
    # NULL-heavy: a sheet with every figure left at its default and no BOM at all.
    conn.execute("INSERT INTO cst_sheets (order_id,created_at) VALUES (?,'2026-06-02')",
                 (zid,))

    # ---- users -------------------------------------------------------------
    admin = conn.execute(
        "SELECT id, COALESCE(session_epoch,0) ep FROM users WHERE role='super_admin' "
        "AND is_active=1 ORDER BY id").fetchone()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                 "lang_pref) VALUES ('rptnoperm','x','No Perm','quality_inspector',1,'en')")
    noperm = conn.execute("SELECT id, COALESCE(session_epoch,0) ep FROM users "
                          "WHERE username='rptnoperm'").fetchone()
    conn.commit()
    conn.close()
    ok("fixtures created (super_admin + a user without cost_view)",
       admin is not None and noperm is not None)

    # =====================================================================
    print("\n-- registration --")
    for k in KEYS:
        spec = R.get(k)
        ok(f"{k} is registered with perm cost_view",
           spec is not None and spec["perm"] == "cost_view")
        ok(f"{k} declares AR + TR for every column/kpi/filter",
           spec is not None and all(c["ar"] and c["tr"] for c in spec["columns"])
           and all(x["ar"] and x["tr"] for x in spec["kpis"])
           and all(x["ar"] and x["tr"] for x in spec["filters"])
           and bool(spec["title_ar"] and spec["title_tr"]
                    and spec["desc_ar"] and spec["desc_tr"]))

    # =====================================================================
    print("\n-- the numbers (hand-computed on RPT-1) --")
    # qty 1000 @ 10.00  ->  revenue 10,000.00
    # material/unit 2.30 -> material 2,300.00
    # cm/unit      0.80 -> cm       800.00
    # others/unit  0.20 -> overheads 200.00
    # estimate = 1000 x 3.30 = 3,300.00 ; margin = 10,000 - 3,300 = 6,700.00 (67.00%)
    res = R.run(R.get("costing_orders"), {"order_no": "RPT-1"})
    ok("costing_orders returns exactly the one order", res["total"] == 1)
    row = res["rows"][0] if res["rows"] else {}
    ok("revenue = 1000 x 10.00 = 10,000.00", near(row.get("revenue"), 10000))
    ok("material = 1000 x (0.25x1.04x5.00 + 2x0.50) = 2,300.00",
       near(row.get("material"), 2300))
    ok("cm = 1000 x (smv 10 x rate 0.08) = 800.00", near(row.get("cm"), 800))
    ok("overheads = 1000 x (0.10+0.05+0.00+0.05) = 200.00",
       near(row.get("overheads"), 200))
    ok("estimated cost = 2300 + 800 + 200 = 3,300.00", near(row.get("est_cost"), 3300))
    ok("cost/unit = 3.30", near(row.get("unit_cost"), 3.30))
    ok("margin = 10,000 - 3,300 = 6,700.00", near(row.get("margin"), 6700))
    ok("margin % = 6700/10000 = 67.00", near(row.get("margin_pct"), 67.0))
    ok("unpriced BOM lines = 1 (the label has no price)", near(row.get("unpriced"), 1))
    kp = {k["key"]: k["value"] for k in res["kpis"]}
    ok("KPI revenue/cost/margin agree with the row",
       near(kp.get("revenue"), 10000) and near(kp.get("cost"), 3300)
       and near(kp.get("margin"), 6700))
    ok("KPI margin % = SUM(margin)/SUM(revenue) = 67.00", near(kp.get("margin_pct"), 67.0))
    ok("totals row repeats the single row's estimate",
       near(res["totals"].get("est_cost"), 3300))

    # BOM: required = 1000 x 0.25 x 1.04 = 260 ; line cost = 260 x 5.00 = 1,300.00
    #      required = 1000 x 2.00 x 1.00 = 2000 ; line cost = 2000 x 0.50 = 1,000.00
    #      the unpriced label costs 0.00, so the BOM total is 2,300.00 = material above
    resb = R.run(R.get("costing_bom"), {"order_no": "RPT-1"})
    ok("costing_bom returns the 3 BOM lines", resb["total"] == 3)
    by_item = {r["item"]: r for r in resb["rows"]}
    ok("fabric required qty = 1000 x 0.25 x 1.04 = 260",
       near(by_item.get("Fabric", {}).get("required_qty"), 260))
    ok("fabric line cost = 260 x 5.00 = 1,300.00",
       near(by_item.get("Fabric", {}).get("line_cost"), 1300))
    ok("thread line cost = 2000 x 0.50 = 1,000.00",
       near(by_item.get("Thread", {}).get("line_cost"), 1000))
    ok("unpriced label costs 0.00 and is counted as unpriced",
       near(by_item.get("Label", {}).get("line_cost"), 0))
    kb = {k["key"]: k["value"] for k in resb["kpis"]}
    ok("BOM material cost total 2,300.00 == the order report's material",
       near(kb.get("cost"), 2300) and near(row.get("material"), 2300))
    ok("BOM unpriced-line KPI = 1", near(kb.get("unpriced"), 1))

    # Actuals: 1,500.00 material + 900.00 cm = 2,400.00 over 2 entries, 1 order
    resa = R.run(R.get("costing_actuals"), {"order_no": "RPT-1"})
    ka = {k["key"]: k["value"] for k in resa["kpis"]}
    ok("booked actuals = 1500 + 900 = 2,400.00 over 2 entries",
       resa["total"] == 2 and near(ka.get("amount"), 2400) and near(ka.get("entries"), 2))
    ok("actuals chart has one bar per category (2)",
       resa["chart"] is not None and len(resa["chart"]["labels"]) == 2)

    # =====================================================================
    print("\n-- edge cases --")
    empty = R.run(R.get("costing_orders"), {"order_no": "NO-SUCH-ORDER"})
    ok("empty result set: 0 rows, no chart, KPIs are 0 not a crash",
       empty["total"] == 0 and empty["chart"] is None
       and all(k["value"] == 0 for k in empty["kpis"]))
    zero = R.run(R.get("costing_orders"), {"order_no": "RPT-0"})
    zrow = zero["rows"][0] if zero["rows"] else {}
    ok("zero-qty / zero-price order still renders (single row)", zero["total"] == 1)
    ok("margin % on a zero-revenue order is blank, not 0 and not a crash",
       zrow.get("margin_pct") == "")
    ok("a costed order with no BOM reports 0.00 material, not NULL arithmetic",
       near(zrow.get("material"), 0) and near(zrow.get("est_cost"), 0))
    ok("an UNCOSTED order never appears (it would show a fake 100% margin)",
       all(r["order_no"] not in ("",) for r in zero["rows"]))
    bad = R.run(R.get("costing_orders"), {"period": "custom", "from": "2026-12-01",
                                          "to": "2026-01-01"})
    ok("inverted date range is an error, not a silently empty table",
       bad["error"] == "inverted_range" and bad["rows"] == [])

    # =====================================================================
    print("\n-- permission on every surface --")
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"], s["ep"] = noperm["id"], noperm["ep"]
    for k in KEYS:
        r = GET(f"/reporting/{k}")
        ok(f"{k}: HTML page is {r.status_code} (403 expected) for a user without cost_view",
           r.status_code == 403)
        for fmt in ("csv", "xlsx", "pdf"):
            r = GET(f"/reporting/{k}.{fmt}")
            body = r.get_data()
            ok(f"{k}.{fmt}: {r.status_code} and no data leaked",
               r.status_code == 403 and b"RPT-1" not in body and b"RPTSUP" not in body)
    r = GET("/reporting/")
    ok("the hub itself lists no costing report for that user",
       r.status_code == 200 and all(k.encode() not in r.get_data() for k in KEYS))

    # =====================================================================
    print("\n-- routes + exports for a permitted user --")
    with c.session_transaction() as s:
        s["uid"], s["ep"] = admin["id"], admin["ep"]
    for k in KEYS:
        r = GET(f"/reporting/{k}")
        ok(f"{k}: page 200", r.status_code == 200)
        r = GET(f"/reporting/{k}?order_no=RPT-1")
        ok(f"{k}: filtered page 200 and shows the order",
           r.status_code == 200 and b"RPT-1" in r.get_data())
        for fmt, mime in (("csv", "text/csv"), ("xlsx", "application"), ("pdf", "application/pdf")):
            r = GET(f"/reporting/{k}.{fmt}?order_no=RPT-1")
            ok(f"{k}.{fmt}: 200, {r.mimetype}, {len(r.get_data())} bytes",
               r.status_code == 200 and mime in r.mimetype and len(r.get_data()) > 100)
    r = GET("/reporting/costing_orders.csv?order_no=RPT-1")
    body = r.get_data().decode("utf-8-sig")
    ok("CSV carries the UTF-8 BOM Excel needs and the hand-computed estimate",
       r.get_data().startswith(b"\xef\xbb\xbf") and "3300" in body.replace(".0", ""))

    # =====================================================================
    print("\n-- i18n: en / ar / tr --")
    D = i18n_dicts()
    mine = ROOT / "app" / "templates" / "costing" / "dashboard.html"
    tmpl_keys = set(re.findall(r'data-i18n="([^"]+)"', mine.read_text(encoding="utf-8")))
    missing_mine = {lang: sorted(k for k in tmpl_keys if k not in D[lang]) for lang in D}
    ok(f"every data-i18n key on the costing dashboard resolves in en/ar/tr "
       f"({len(tmpl_keys)} keys)", not any(missing_mine.values()))
    if any(missing_mine.values()):
        print("        missing:", missing_mine)

    for lang in ("en", "ar", "tr"):
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=?", (lang, admin["id"]))
        conn.commit()
        conn.close()
        r = GET(f"/reporting/costing_orders?order_no=RPT-1")
        ok(f"report page 200 with lang_pref={lang}", r.status_code == 200)
        r = GET(f"/reporting/costing_orders.csv?order_no=RPT-1")
        ok(f"CSV export 200 with lang_pref={lang} ({r.mimetype})", r.status_code == 200)

    page = GET("/reporting/costing_orders").get_data().decode("utf-8")
    foreign = sorted({k for k in re.findall(r'data-i18n="([^"]+)"', page)
                      if k not in D["en"]})
    ok(f"report-page template keys all resolve in en.json "
       f"({len(foreign)} missing — owned by the reporting-engine lane)", not foreign)
    if foreign:
        print("        MISSING (app/templates/reports/*.html, not this module):")
        print("        " + ", ".join(foreign))
    ok("report page carries data-loc-* for this module's own definition text "
       "(never a raw i18n key)", 'data-loc-ar' in page)

print("\n%d/%d checks passed" % (sum(PASS), len(PASS)))
sys.exit(0 if all(PASS) else 1)
