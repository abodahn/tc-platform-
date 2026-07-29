"""
Warehouse reports self-test — the four specs registered in app/warehouse/reports.py,
executed through the shared engine (app/services/reporting.py).

Run:  python app/warehouse/tests_reports.py     (PYTHONIOENCODING=utf-8 on Windows)

What it proves, in order:
  * the numbers. Every assertion below is a HAND-COMPUTED expected value against
    rows this test inserts itself — a report with wrong numbers is worse than no
    report, so this is the part that matters.
  * empty, single-row and NULL-heavy datasets render without a 500 or a division
    by zero.
  * permission. A permitted user gets 200 on the page and on CSV / XLSX / PDF; a
    user WITHOUT wh_view gets a raw 403 on all four and reads nothing.
  * the page renders 200 with the user's language set to en, ar and tr, and every
    data-i18n key on the warehouse pages this lane touched resolves in all three.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(r"D:\TC platform\tc-platform-render")
TMP = Path(tempfile.mkdtemp(prefix="whrpt_"))
os.chdir(TMP)                                   # never write into the repo
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                    # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                       # noqa: E402

FAILS = []
CHECKS = [0]


def ok(name, cond, extra=""):
    CHECKS[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def head(t):
    print("\n--- %s ---" % t)


def near(a, b, tol=1e-6):
    if a in (None, "") or b is None:
        return a == b
    return abs(float(a) - float(b)) <= tol


app = create_app()
from app.services import reporting as R                          # noqa: E402

# The hub blueprint is registered by app/__init__.py, which this lane may not edit
# and which does not register it YET. Register it here so the permission and export
# surfaces are actually exercised; if the orchestrator has already wired it, this is
# a no-op. (Reported in needs_orchestrator.)
if "reports_hub" not in app.blueprints:
    from app.routes.reports_hub import bp as reports_hub_bp
    app.register_blueprint(reports_hub_bp)
    print("  NOTE  registered reports_hub locally — app/__init__.py does not yet")

KEYS = ["warehouse_stock", "warehouse_rolls", "warehouse_slow", "warehouse_fg"]

head("registration")
for k in KEYS:
    spec = R.get(k)
    ok("%s is registered" % k, spec is not None)
    if spec:
        ok("%s is gated by wh_view" % k, spec["perm"] == "wh_view", spec["perm"])
        labelled = all(c["ar"].strip() and c["tr"].strip() for c in spec["columns"])
        ok("%s: every column has AR + TR text" % k, labelled)
        ok("%s: every KPI has AR + TR text" % k,
           all(x["ar"].strip() and x["tr"].strip() for x in spec["kpis"]))


# --------------------------------------------------------------- fixtures --
# Everything below is prefixed ZZ so a filter can isolate it from the demo seed.
with app.app_context():
    from app.db import get_db
    conn = get_db()

    def mat(code, stock, reserved, cost, reorder, kind="fabric", roll=0):
        conn.execute(
            "INSERT INTO wh_materials (code,name,kind,roll_tracked,color,uom,stock_qty,"
            "reserved_qty,reorder_level,avg_cost,is_active,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,1,'2026-01-01')",
            (code, code + " test", kind, roll, "Red", "m", stock, reserved, reorder, cost))
        return conn.execute("SELECT id FROM wh_materials WHERE code=?", (code,)).fetchone()["id"]

    # stock: 100 on hand / 30 reserved @2.50, 40 / 40 @1.00 (short), and a row that
    # is NULL in every number a report divides or sums.
    a = mat("ZZT-A", 100, 30, 2.5, 0)
    b = mat("ZZT-B", 40, 40, 1.0, 10)
    c = mat("ZZT-C", None, None, None, None)

    # rolls, one shade lot, one of them in quarantine
    rm = mat("ZZR-FAB", 150, 20, 3.0, 0, roll=1)
    for no, length, remaining, reserved, status in (
            ("ZZROLL-1", 120, 100, 20, "partial"), ("ZZROLL-2", 50, 50, 0, "quarantine")):
        conn.execute(
            "INSERT INTO wh_rolls (roll_no,material_id,shade_lot,grade,width_cm,length_m,"
            "remaining_m,reserved_m,unit_cost,status,received_at) "
            "VALUES (?,?,'ZZLOT','A',180,?,?,?,3.0,?,'2026-02-01')",
            (no, rm, length, remaining, reserved, status))

    # slow movers: one material issued twice, one never issued at all
    s1 = mat("ZZS-1", 500, 0, 2.0, 0)
    s2 = mat("ZZS-2", 300, 0, 4.0, 0)
    for qty, cost, when in ((-10, 2.0, "2026-03-01 08:00:00"), (-5, 3.0, "2026-03-05 08:00:00")):
        conn.execute(
            "INSERT INTO wh_movements (movement_no,type,material_id,qty,before_qty,after_qty,"
            "unit_cost,created_at) VALUES (?,'issue',?,?,0,0,?,?)",
            ("ZZM-%s" % when[8:10], s1, qty, cost, when))
    # a RECEIPT on the same material must not count as consumption
    conn.execute("INSERT INTO wh_movements (movement_no,type,material_id,qty,before_qty,"
                 "after_qty,unit_cost,created_at) VALUES "
                 "('ZZM-IN','purchase_receiving',?,999,0,0,9.0,'2026-03-09 08:00:00')", (s1,))

    # finished goods against a real order
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,unit_price,currency,status,"
                 "ship_date,created_by) VALUES "
                 "('ZZ-ORD','ZZ Buyer',1000,5,'USD','open','2026-06-30','test')")
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='ZZ-ORD'").fetchone()["id"]
    for style, colour, size, packed, shipped in (("ZZ-ST", "Red", "M", 100, 40),
                                                 ("ZZ-ST", "Red", "L", None, None)):
        conn.execute(
            "INSERT INTO wh_fg (order_id,style_code,color,size,packed_qty,shipped_qty,uom,"
            "warehouse,created_at,updated_at) VALUES (?,?,?,?,?,?,'pcs','ZZ Store',"
            "'2026-04-01 00:00:00','2026-04-01 00:00:00')",
            (oid, style, colour, size, packed, shipped))
    conn.commit()
    conn.close()


def run(key, **args):
    with app.app_context():
        return R.run(R.get(key), args)


def kpi(res, key):
    return {k["key"]: k["value"] for k in res["kpis"]}.get(key)


# ------------------------------------------------------------- the numbers --
head("warehouse_stock — arithmetic")
res = run("warehouse_stock", code="ZZT")
ok("3 test materials selected", res["total"] == 3, res["total"])
rows = {r["code"]: r for r in res["rows"]}
# available = on hand - reserved: 100-30 = 70
ok("ZZT-A available = 100 - 30 = 70", near(rows["ZZT-A"]["available"], 70),
   rows["ZZT-A"]["available"])
# stock value = on hand x avg cost: 100 x 2.50 = 250
ok("ZZT-A value = 100 x 2.50 = 250", near(rows["ZZT-A"]["stock_value"], 250),
   rows["ZZT-A"]["stock_value"])
ok("ZZT-B available = 40 - 40 = 0", near(rows["ZZT-B"]["available"], 0))
# a row that is NULL everywhere must read 0, not blow up or print None
ok("NULL-heavy ZZT-C reads 0 on hand / 0 available / 0 value",
   near(rows["ZZT-C"]["on_hand"], 0) and near(rows["ZZT-C"]["available"], 0)
   and near(rows["ZZT-C"]["stock_value"], 0))
# totals row: 100+40+0 = 140 on hand, 30+40+0 = 70 reserved, 70+0+0 = 70 available,
# 250 + 40 + 0 = 290 value
ok("totals: on hand 140", near(res["totals"]["on_hand"], 140), res["totals"]["on_hand"])
ok("totals: reserved 70", near(res["totals"]["reserved"], 70), res["totals"]["reserved"])
ok("totals: available 70", near(res["totals"]["available"], 70), res["totals"]["available"])
ok("totals: stock value 250 + 40 + 0 = 290", near(res["totals"]["stock_value"], 290),
   res["totals"]["stock_value"])
ok("KPI materials = 3", near(kpi(res, "materials"), 3), kpi(res, "materials"))
ok("KPI stock value = 290", near(kpi(res, "stock_value"), 290), kpi(res, "stock_value"))
# short: reorder_level > 0 AND available <= reorder_level. Only ZZT-B (0 <= 10);
# ZZT-A has no reorder level and ZZT-C's is NULL.
ok("KPI below reorder level = 1 (only ZZT-B)", near(kpi(res, "short"), 1), kpi(res, "short"))
res2 = run("warehouse_stock", code="ZZT", short="yes")
ok("the 'below reorder level' filter returns exactly ZZT-B",
   res2["total"] == 1 and res2["rows"][0]["code"] == "ZZT-B", res2["total"])
# 1 aggregate (KPIs + totals + count) + 1 rows + 1 chart. Constant in the row count:
# nothing in this report aggregates in Python.
ok("stock report issues exactly 3 queries", res["queries"] == 3, res["queries"])

head("warehouse_rolls — arithmetic")
res = run("warehouse_rolls", shade_lot="ZZLOT")
ok("2 rolls selected", res["total"] == 2, res["total"])
# free = remaining - reserved: (100-20) + (50-0) = 130
ok("totals: remaining 100 + 50 = 150", near(res["totals"]["remaining_m"], 150),
   res["totals"]["remaining_m"])
ok("totals: free (100-20) + (50-0) = 130", near(res["totals"]["free_m"], 130),
   res["totals"]["free_m"])
ok("KPI shade lots = 1", near(kpi(res, "lots"), 1), kpi(res, "lots"))
ok("KPI in quarantine = 1", near(kpi(res, "quarantine"), 1), kpi(res, "quarantine"))
ok("oldest-first default order puts ZZROLL-1 above ZZROLL-2",
   [r["roll_no"] for r in res["rows"]] == ["ZZROLL-1", "ZZROLL-2"],
   [r["roll_no"] for r in res["rows"]])

head("warehouse_slow — arithmetic")
res = run("warehouse_slow", code="ZZS")
ok("2 materials selected", res["total"] == 2, res["total"])
rows = {r["code"]: r for r in res["rows"]}
# issued = 10 + 5 = 15 pieces; value = 10x2.00 + 5x3.00 = 35.00. The +999 receipt
# is a positive movement and must NOT appear in either number.
ok("ZZS-1 issued qty = 10 + 5 = 15 (the receipt is excluded)",
   near(rows["ZZS-1"]["issued_qty"], 15), rows["ZZS-1"]["issued_qty"])
ok("ZZS-1 issued value = 10x2.00 + 5x3.00 = 35", near(rows["ZZS-1"]["issued_value"], 35),
   rows["ZZS-1"]["issued_value"])
ok("ZZS-1 last issued = 2026-03-05", str(rows["ZZS-1"]["last_issue"]).startswith("2026-03-05"),
   rows["ZZS-1"]["last_issue"])
# the never-issued material is the whole point of the LEFT JOIN
ok("ZZS-2 was never issued and still appears", rows["ZZS-2"]["last_issue"] in ("", None),
   repr(rows["ZZS-2"]["last_issue"]))
ok("ZZS-2 issued qty = 0", near(rows["ZZS-2"]["issued_qty"], 0))
ok("never-issued sorts first (slowest mover at the top)",
   res["rows"][0]["code"] == "ZZS-2", res["rows"][0]["code"])
ok("KPI never issued = 1", near(kpi(res, "never"), 1), kpi(res, "never"))
# idle value = stock of the never-issued material x its cost = 300 x 4.00 = 1200
ok("KPI value never issued = 300 x 4.00 = 1200", near(kpi(res, "idle_value"), 1200),
   kpi(res, "idle_value"))
ok("KPI issued value = 35", near(kpi(res, "issued_value"), 35), kpi(res, "issued_value"))

head("warehouse_fg — arithmetic")
res = run("warehouse_fg", order_no="ZZ-ORD")
ok("2 SKUs selected", res["total"] == 2, res["total"])
rows = {r["size"]: r for r in res["rows"]}
# in store = packed - shipped: 100 - 40 = 60
ok("size M in store = 100 - 40 = 60", near(rows["M"]["on_hand"], 60), rows["M"]["on_hand"])
ok("NULL packed/shipped reads 0 in store", near(rows["L"]["on_hand"], 0))
ok("totals: packed 100, shipped 40, in store 60",
   near(res["totals"]["packed"], 100) and near(res["totals"]["shipped"], 40)
   and near(res["totals"]["on_hand"], 60), res["totals"])
ok("the order number is joined in, not the raw id",
   rows["M"]["order_no"] == "ZZ-ORD", rows["M"]["order_no"])


# ------------------------------------------------- empty / single / errors --
head("empty, single-row and bad input")
for k in KEYS:
    res = run(k, code="NO-SUCH-CODE-EVER", order_no="NO-SUCH-CODE-EVER",
              shade_lot="NO-SUCH-CODE-EVER")
    ok("%s: an empty result set renders with no error" % k,
       res["total"] == 0 and res["rows"] == [] and res["error"] is None)
    ok("%s: KPIs on an empty set are 0, never None" % k,
       all(x["value"] == 0 for x in res["kpis"]), [x["value"] for x in res["kpis"]])
    ok("%s: no chart is drawn for an empty set" % k, res["chart"] is None)

res = run("warehouse_stock", code="ZZT-A")
ok("single-row result renders and totals equal that row",
   res["total"] == 1 and near(res["totals"]["stock_value"], 250), res["total"])

res = run("warehouse_rolls", **{"period": "custom", "from": "2026-12-01", "to": "2026-01-01"})
ok("an inverted date range is reported, not silently emptied",
   res["error"] == "inverted_range" and res["rows"] == [], res["error"])
res = run("warehouse_rolls", **{"period": "custom", "from": "not-a-date"})
ok("a garbage date is reported as bad_date", res["error"] == "bad_date", res["error"])
res = run("warehouse_stock", sort="'; DROP TABLE wh_materials--", dir="desc")
ok("an injected sort key is ignored (only declared columns sort)", res["sort"] == "")
with app.app_context():
    from app.db import get_db
    conn = get_db()
    still = conn.execute("SELECT COUNT(*) c FROM wh_materials").fetchone()["c"]
    conn.close()
ok("wh_materials survived the injected sort key", still > 0, still)


# ----------------------------------------------------------- permissions ---
head("permission — page and all three export formats")
client = app.test_client()
with app.app_context():
    from app.db import get_db
    conn = get_db()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,lang_pref) "
                 "VALUES ('zzwhadm','x','WH Admin','super_admin',1,'en')")
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,lang_pref) "
                 "VALUES ('zznoperm','x','No Rights','zz_role_with_no_perms',1,'en')")
    conn.commit()
    rows = {r["username"]: r for r in conn.execute(
        "SELECT id, username, session_epoch FROM users "
        "WHERE username IN ('zzwhadm','zznoperm')").fetchall()}
    conn.close()
ADM = (rows["zzwhadm"]["id"], rows["zzwhadm"]["session_epoch"] or 0)
NUL = (rows["zznoperm"]["id"], rows["zznoperm"]["session_epoch"] or 0)


def login(who):
    with client.session_transaction() as s:
        s["uid"], s["ep"] = who


URLS = []
for k in KEYS:
    URLS += ["/reporting/%s" % k, "/reporting/%s.csv" % k,
             "/reporting/%s.xlsx" % k, "/reporting/%s.pdf" % k]

login(ADM)
for u in URLS:
    r = client.get(u)                          # RAW status: no follow_redirects
    ok("permitted user gets 200 on %s" % u, r.status_code == 200, r.status_code)
csv = client.get("/reporting/warehouse_stock.csv?code=ZZT").data.decode("utf-8-sig")
ok("CSV carries the filtered rows", "ZZT-A" in csv and "ZZT-B" in csv)
ok("CSV starts with a BOM so Excel reads it as UTF-8",
   client.get("/reporting/warehouse_stock.csv?code=ZZT").data.startswith(b"\xef\xbb\xbf"))
pdf = client.get("/reporting/warehouse_stock.pdf?code=ZZT")
ok("PDF is a real PDF", pdf.mimetype == "application/pdf" and pdf.data[:5] == b"%PDF-",
   pdf.mimetype)
xls = client.get("/reporting/warehouse_stock.xlsx?code=ZZT")
ok("XLSX is a real workbook", xls.data[:2] == b"PK", xls.mimetype)

login(NUL)
for u in URLS:
    r = client.get(u)
    ok("user without wh_view gets a raw 403 on %s" % u, r.status_code == 403, r.status_code)
    ok("...and reads none of the data from %s" % u, b"ZZT-A" not in r.data)
r = client.get("/reporting/")
ok("the hub itself lists no warehouse report for that user",
   r.status_code == 200 and b"warehouse_stock" not in r.data, r.status_code)


# ------------------------------------------------------------------ i18n ---
head("i18n — page 200 in en / ar / tr, and every key on OUR pages resolves")
import json                                                      # noqa: E402
DICT = {}
for lang in ("en", "ar", "tr"):
    DICT[lang] = json.loads((REPO / "app" / "static" / "i18n" / ("%s.json" % lang))
                            .read_text(encoding="utf-8"))

login(ADM)
for lang in ("en", "ar", "tr"):
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=?", (lang, ADM[0]))
        conn.commit()
        conn.close()
    for k in KEYS:
        r = client.get("/reporting/%s" % k)
        ok("%s renders 200 with lang_pref=%s" % (k, lang), r.status_code == 200, r.status_code)
        e = client.get("/reporting/%s.csv" % k)
        ok("%s CSV exports in %s" % (k, lang), e.status_code == 200, e.status_code)

# Our own templates: the warehouse pages this lane edited.
missing = []
for p in sorted((REPO / "app" / "templates" / "warehouse").glob("*.html")):
    for key in re.findall(r'data-i18n(?:-ph|-title)?="([^"]+)"', p.read_text(encoding="utf-8")):
        if "{" in key:
            continue          # a Jinja-computed key (rolls.html) — resolved at render time
        for lang in ("en", "ar", "tr"):
            if key not in DICT[lang]:
                missing.append((p.name, key, lang))
ok("every data-i18n key on the warehouse templates resolves in en, ar and tr",
   not missing, missing[:6])
# The Reports link added to the dashboard is data-loc-*, so it can never render raw.
dash = (REPO / "app" / "templates" / "warehouse" / "dashboard.html").read_text(encoding="utf-8")
ok("the dashboard links to the report hub",
   "/reporting/" in dash and 'data-loc-ar="' in dash)

# The SHARED report page (owned by lane A) — reported, not asserted: this lane
# may not edit app/static/i18n/**.
shared = set()
for p in (REPO / "app" / "templates" / "reports").glob("*.html"):
    shared |= set(re.findall(r'data-i18n="([^"]+)"', p.read_text(encoding="utf-8")))
gap = sorted(k for k in shared if k not in DICT["en"])
print("  NOTE  shared report-page keys still missing from i18n/en.json: %d %s"
      % (len(gap), gap[:5]))

print("\n%d checks, %d failed" % (CHECKS[0], len(FAILS)))
print("RESULT:", "ALL GREEN" if not FAILS else "FAILURES: %s" % FAILS)
sys.exit(1 if FAILS else 0)
