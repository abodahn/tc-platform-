"""
Shipping reports self-test — the three specs registered in app/shipping/reports.py,
executed through the shared engine (app/services/reporting.py).

Run:  python app/shipping/tests_reports.py     (PYTHONIOENCODING=utf-8 on Windows)

Proves, in order: the numbers (hand-computed against rows this test inserts), the
empty / single-row / NULL-heavy cases, permission on the page AND on CSV, XLSX and
PDF, and a 200 in en, ar and tr with every data-i18n key on our own templates
resolving in all three dictionaries.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(r"D:\TC platform\tc-platform-render")
TMP = Path(tempfile.mkdtemp(prefix="shprpt_"))
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

# app/__init__.py does not register the hub blueprint yet and this lane may not
# edit it — register it here so the export surfaces are really exercised.
if "reports_hub" not in app.blueprints:
    from app.routes.reports_hub import bp as reports_hub_bp
    app.register_blueprint(reports_hub_bp)
    print("  NOTE  registered reports_hub locally — app/__init__.py does not yet")

KEYS = ["shipping_register", "shipping_ontime", "shipping_recon"]

head("registration")
for k in KEYS:
    spec = R.get(k)
    ok("%s is registered" % k, spec is not None)
    if spec:
        ok("%s is gated by shp_view" % k, spec["perm"] == "shp_view", spec["perm"])
        ok("%s: every column has AR + TR text" % k,
           all(c["ar"].strip() and c["tr"].strip() for c in spec["columns"]))
        ok("%s: every KPI has AR + TR text" % k,
           all(x["ar"].strip() and x["tr"].strip() for x in spec["kpis"]))


# --------------------------------------------------------------- fixtures --
with app.app_context():
    from app.db import get_db
    conn = get_db()

    def ship(no, buyer, status, etd, dispatched, carrier="ZZ Line", order_id=None):
        conn.execute(
            "INSERT INTO shp_shipments (shipment_no,order_id,buyer,destination,port_loading,"
            "incoterm,mode,carrier,container_no,etd,eta,status,dispatched_at,currency,"
            "unit_price,created_by,created_at) VALUES (?,?,?,'Hamburg','Alexandria','FOB',"
            "'sea',?,'ZZCONT',?,?,?,?,'USD',5,'test','2026-01-01 00:00:00')",
            (no, order_id, buyer, carrier, etd, etd, status, dispatched))
        return conn.execute("SELECT id FROM shp_shipments WHERE shipment_no=?",
                            (no,)).fetchone()["id"]

    def carton(sid, per, cartons, l=60, w=40, h=30):
        conn.execute(
            "INSERT INTO shp_cartons (shipment_id,carton_no,style,colour,size,qty_per_carton,"
            "cartons,net_weight,gross_weight,length_cm,width_cm,height_cm,created_at) "
            "VALUES (?,'1-1','ZZ-ST','Black','M',?,?,10,11,?,?,?,'2026-01-01 00:00:00')",
            (sid, per, cartons, l, w, h))

    # register: one shipment, two carton lines. 60x50 = 3000 pcs + 40x10 = 400 pcs.
    s1 = ship("ZZSH-1", "ZZBUYER", "dispatched", "2026-05-10", "2026-05-09 10:00:00")
    carton(s1, 60, 50)                 # CBM 60x40x30/1e6 = 0.072 x 50 = 3.6
    carton(s1, 40, 10, 0, 0, 0)        # no dimensions typed in -> contributes 0 CBM

    # dispatch performance: on time, late, and one with no ETD at all
    ship("ZZP-ONTIME", "ZZPUNCT", "dispatched", "2026-05-10", "2026-05-09 10:00:00", "ZZ Fast")
    ship("ZZP-LATE", "ZZPUNCT", "delivered", "2026-05-10", "2026-05-12 10:00:00", "ZZ Slow")
    ship("ZZP-NOETD", "ZZPUNCT", "dispatched", None, "2026-05-11 10:00:00", "ZZ Slow")

    # reconciliation: 1000 ordered; 500 shipped, 200 packed-not-shipped, and a
    # CANCELLED shipment of 1000 that must not count as either.
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,unit_price,currency,status,"
                 "ship_date,created_by) VALUES "
                 "('ZZ-REC','ZZ Recon',1000,5,'USD','open','2026-06-30','test')")
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='ZZ-REC'").fetchone()["id"]
    carton(ship("ZZR-A", "ZZ Recon", "dispatched", "2026-06-01", "2026-06-01 08:00:00",
                order_id=oid), 50, 10)
    carton(ship("ZZR-B", "ZZ Recon", "packed", "2026-06-20", None, order_id=oid), 20, 10)
    carton(ship("ZZR-C", "ZZ Recon", "cancelled", "2026-06-25", None, order_id=oid), 100, 10)
    # an order with NO shipment at all, and a NULL quantity — the division guard
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,status,ship_date,created_by) "
                 "VALUES ('ZZ-NOSHIP','ZZ Recon',NULL,'open','2026-07-01','test')")
    conn.commit()
    conn.close()


def run(key, **args):
    with app.app_context():
        return R.run(R.get(key), args)


def kpi(res, key):
    return {k["key"]: k["value"] for k in res["kpis"]}.get(key)


# ------------------------------------------------------------- the numbers --
head("shipping_register — arithmetic")
res = run("shipping_register", buyer="ZZBUYER")
ok("1 shipment selected (the carton lines are rolled up, not listed)",
   res["total"] == 1, res["total"])
row = res["rows"][0]
# pieces = qty_per_carton x cartons, summed: 60x50 + 40x10 = 3000 + 400 = 3400
ok("pieces = 60x50 + 40x10 = 3400", near(row["pieces"], 3400), row["pieces"])
ok("cartons = 50 + 10 = 60", near(row["cartons"], 60), row["cartons"])
# CBM = LxWxH in cm / 1 000 000, times cartons: 60x40x30 = 72000 cm3 = 0.072 m3, x50 = 3.6
ok("CBM = 60x40x30/1e6 x 50 = 3.6 (the dimensionless line adds 0)",
   near(row["cbm"], 3.6), row["cbm"])
ok("KPI pieces = 3400", near(kpi(res, "pieces"), 3400), kpi(res, "pieces"))
ok("KPI cbm = 3.6", near(kpi(res, "cbm"), 3.6), kpi(res, "cbm"))
ok("totals row equals the single shipment", near(res["totals"]["pieces"], 3400))

head("shipping_ontime — arithmetic")
res = run("shipping_ontime", buyer="ZZPUNCT")
ok("3 departed shipments selected", res["total"] == 3, res["total"])
rows = {r["shipment_no"]: r for r in res["rows"]}
# left 2026-05-09 against an ETD of 2026-05-10 -> on time
ok("left 09/05 against ETD 10/05 = On time", rows["ZZP-ONTIME"]["punctuality"] == "On time",
   rows["ZZP-ONTIME"]["punctuality"])
# left 2026-05-12 against an ETD of 2026-05-10 -> late
ok("left 12/05 against ETD 10/05 = Late", rows["ZZP-LATE"]["punctuality"] == "Late",
   rows["ZZP-LATE"]["punctuality"])
# no ETD is NOT on time — that is the assumption that flatters the number
ok("no ETD is neither on time nor late", rows["ZZP-NOETD"]["punctuality"] == "No ETD",
   rows["ZZP-NOETD"]["punctuality"])
ok("KPI on time = 1", near(kpi(res, "on_time"), 1), kpi(res, "on_time"))
ok("KPI late = 1", near(kpi(res, "late"), 1), kpi(res, "late"))
# on-time % = 100 x 1 on-time / 2 shipments that HAVE an ETD = 50.0
ok("KPI on-time % = 100 x 1/2 = 50.0", near(kpi(res, "on_time_pct"), 50.0),
   kpi(res, "on_time_pct"))
r2 = run("shipping_ontime", buyer="ZZPUNCT", punctuality="Late")
ok("the punctuality filter returns exactly the late one",
   r2["total"] == 1 and r2["rows"][0]["shipment_no"] == "ZZP-LATE", r2["total"])
# a shipment that has never left must not be in this report at all
r3 = run("shipping_ontime", buyer="ZZ Recon")
ok("a packed-but-not-departed shipment is excluded",
   all(x["shipment_no"] != "ZZR-B" for x in r3["rows"]),
   [x["shipment_no"] for x in r3["rows"]])

head("shipping_recon — arithmetic")
res = run("shipping_recon", order_no="ZZ-REC")
ok("1 order selected", res["total"] == 1, res["total"])
row = res["rows"][0]
ok("ordered = 1000", near(row["ordered"], 1000), row["ordered"])
# packed = every live shipment: 50x10 (dispatched) + 20x10 (packed) = 700.
# The cancelled 100x10 = 1000 must not appear anywhere.
ok("packed = 50x10 + 20x10 = 700 (the cancelled shipment is excluded)",
   near(row["packed"], 700), row["packed"])
# shipped = only what physically left: 50x10 = 500
ok("shipped = 50x10 = 500 (packed-not-departed does not count)",
   near(row["shipped"], 500), row["shipped"])
ok("balance = 1000 - 500 = 500", near(row["balance"], 500), row["balance"])
# fulfilment = 100 x 500 / 1000 = 50.0
ok("fulfilment % = 100 x 500/1000 = 50.0", near(row["fulfil_pct"], 50.0), row["fulfil_pct"])
ok("shipments counted = 2 live, not 3", near(row["shipments"], 2), row["shipments"])
ok("KPI fulfilment % = 50.0", near(kpi(res, "fulfil"), 50.0), kpi(res, "fulfil"))
res = run("shipping_recon", order_no="ZZ-NOSHIP")
ok("an order with no shipment still appears (the LEFT JOIN is the point)",
   res["total"] == 1, res["total"])
ok("...with 0 packed, 0 shipped and no fulfilment %% (NULL qty, no division by zero)",
   near(res["rows"][0]["packed"], 0) and near(res["rows"][0]["shipped"], 0)
   and res["rows"][0]["fulfil_pct"] in ("", None), res["rows"][0])


# ------------------------------------------------- empty / single / errors --
head("empty, single-row and bad input")
for k in KEYS:
    res = run(k, buyer="NO-SUCH-BUYER-EVER", order_no="NO-SUCH-ORDER-EVER")
    ok("%s: an empty result set renders with no error" % k,
       res["total"] == 0 and res["rows"] == [] and res["error"] is None)
    ok("%s: KPIs on an empty set are 0, never None" % k,
       all(x["value"] == 0 for x in res["kpis"]), [x["value"] for x in res["kpis"]])
    ok("%s: no chart is drawn for an empty set" % k, res["chart"] is None)

res = run("shipping_register", buyer="ZZBUYER")
ok("single-row result renders and totals equal that row",
   res["total"] == 1 and near(res["totals"]["cartons"], 60), res["total"])
res = run("shipping_register", **{"period": "custom", "from": "2026-12-01", "to": "2026-01-01"})
ok("an inverted date range is reported, not silently emptied",
   res["error"] == "inverted_range" and res["rows"] == [], res["error"])
res = run("shipping_recon", **{"period": "custom", "to": "31/06/2026"})
ok("a garbage date is reported as bad_date", res["error"] == "bad_date", res["error"])
res = run("shipping_register", buyer="ZZ' OR 1=1 --")
ok("a quoted filter value is bound as a parameter, not injected", res["total"] == 0,
   res["total"])


# ----------------------------------------------------------- permissions ---
head("permission — page and all three export formats")
client = app.test_client()
with app.app_context():
    from app.db import get_db
    conn = get_db()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,lang_pref) "
                 "VALUES ('zzshpadm','x','Ship Admin','super_admin',1,'en')")
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,lang_pref) "
                 "VALUES ('zzshpnil','x','No Rights','zz_role_with_no_perms',1,'en')")
    conn.commit()
    rows = {r["username"]: r for r in conn.execute(
        "SELECT id, username, session_epoch FROM users "
        "WHERE username IN ('zzshpadm','zzshpnil')").fetchall()}
    conn.close()
ADM = (rows["zzshpadm"]["id"], rows["zzshpadm"]["session_epoch"] or 0)
NUL = (rows["zzshpnil"]["id"], rows["zzshpnil"]["session_epoch"] or 0)


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
csv = client.get("/reporting/shipping_register.csv?buyer=ZZBUYER").data
ok("CSV carries the filtered rows", b"ZZSH-1" in csv)
ok("CSV starts with a BOM so Excel reads it as UTF-8", csv.startswith(b"\xef\xbb\xbf"))
pdf = client.get("/reporting/shipping_recon.pdf?order_no=ZZ-REC")
ok("PDF is a real PDF", pdf.mimetype == "application/pdf" and pdf.data[:5] == b"%PDF-",
   pdf.mimetype)
xls = client.get("/reporting/shipping_ontime.xlsx?buyer=ZZPUNCT")
ok("XLSX is a real workbook", xls.data[:2] == b"PK", xls.mimetype)

login(NUL)
for u in URLS:
    r = client.get(u)
    ok("user without shp_view gets a raw 403 on %s" % u, r.status_code == 403, r.status_code)
    ok("...and reads none of the data from %s" % u, b"ZZSH-1" not in r.data)
r = client.get("/reporting/")
ok("the hub lists no shipping report for that user",
   r.status_code == 200 and b"shipping_register" not in r.data, r.status_code)


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

missing = []
for p in sorted((REPO / "app" / "templates" / "shipping").glob("*.html")):
    for key in re.findall(r'data-i18n(?:-ph|-title)?="([^"]+)"', p.read_text(encoding="utf-8")):
        if "{" in key:
            continue                       # Jinja-computed key, resolved at render time
        for lang in ("en", "ar", "tr"):
            if key not in DICT[lang]:
                missing.append((p.name, key, lang))
ok("every data-i18n key on the shipping templates resolves in en, ar and tr",
   not missing, missing[:6])
dash = (REPO / "app" / "templates" / "shipping" / "dashboard.html").read_text(encoding="utf-8")
ok("the dashboard links to the report hub",
   "/reporting/" in dash and 'data-loc-ar="' in dash)

shared = set()
for p in (REPO / "app" / "templates" / "reports").glob("*.html"):
    shared |= set(re.findall(r'data-i18n="([^"]+)"', p.read_text(encoding="utf-8")))
gap = sorted(k for k in shared if k not in DICT["en"])
print("  NOTE  shared report-page keys still missing from i18n/en.json: %d %s"
      % (len(gap), gap[:5]))

print("\n%d checks, %d failed" % (CHECKS[0], len(FAILS)))
print("RESULT:", "ALL GREEN" if not FAILS else "FAILURES: %s" % FAILS)
sys.exit(1 if FAILS else 0)
