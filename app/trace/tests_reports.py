"""
Traceability reports self-test — the three specs registered in app/trace/reports.py,
executed through the shared engine (app/services/reporting.py).

Run:  python app/trace/tests_reports.py     (PYTHONIOENCODING=utf-8 on Windows)

Proves, in order: the numbers (hand-computed against rows this test inserts, with
certificate dates set relative to TODAY so the derived state is deterministic), the
empty / single-row / NULL-heavy cases, permission on the page AND on CSV, XLSX and
PDF, and a 200 in en, ar and tr with every data-i18n key on our own templates
resolving in all three dictionaries.
"""
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

REPO = Path(r"D:\TC platform\tc-platform-render")
TMP = Path(tempfile.mkdtemp(prefix="trcrpt_"))
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

KEYS = ["trace_lots", "trace_certs", "trace_esg"]

head("registration")
for k in KEYS:
    spec = R.get(k)
    ok("%s is registered" % k, spec is not None)
    if spec:
        ok("%s is gated by trc_view" % k, spec["perm"] == "trc_view", spec["perm"])
        ok("%s: every column has AR + TR text" % k,
           all(c["ar"].strip() and c["tr"].strip() for c in spec["columns"]))
        ok("%s: every KPI has AR + TR text" % k,
           all(x["ar"].strip() and x["tr"].strip() for x in spec["kpis"]))


# --------------------------------------------------------------- fixtures --
TODAY = date.today()


def d(offset):
    return str(TODAY + timedelta(days=offset))


with app.app_context():
    from app.db import get_db
    conn = get_db()

    conn.execute("INSERT INTO trc_partners (name,tier,country,role,status,created_at) "
                 "VALUES ('ZZ Spinner',3,'Egypt','Spinner','active','2026-01-01')")
    pid = conn.execute("SELECT id FROM trc_partners WHERE name='ZZ Spinner'").fetchone()["id"]

    # two lots, the second made from the first (100 + 50 = 150 kg)
    conn.execute("INSERT INTO trc_lots (lot_ref,material,fibre_composition,partner_id,"
                 "parent_lot_id,qty,uom,country_of_origin,received_date,created_at) VALUES "
                 "('ZZL-1','ZZMAT yarn','100% Cotton',?,NULL,100,'kg','Egypt','2026-02-01',"
                 "'2026-02-01')", (pid,))
    l1 = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='ZZL-1'").fetchone()["id"]
    conn.execute("INSERT INTO trc_lots (lot_ref,material,fibre_composition,partner_id,"
                 "parent_lot_id,qty,uom,country_of_origin,received_date,created_at) VALUES "
                 "('ZZL-2','ZZMAT fabric','100% Cotton',?,?,50,'kg','Egypt','2026-03-01',"
                 "'2026-03-01')", (pid, l1))
    # a NULL-heavy lot: no supplier, no quantity, no dates
    conn.execute("INSERT INTO trc_lots (lot_ref,material,created_at) "
                 "VALUES ('ZZL-3','ZZMAT scrap','2026-03-02')")

    conn.execute("INSERT INTO trc_partners (name,tier,country,role,status,created_at) "
                 "VALUES ('ZZ Certholder',2,'Turkey','Fabric mill','active','2026-01-01')")
    cp = conn.execute("SELECT id FROM trc_partners WHERE name='ZZ Certholder'").fetchone()["id"]
    # one valid, one lapsed, one revoked, one that has not started yet
    for std, no, vfrom, vuntil, status in (
            ("GOTS", "ZZ-VALID", d(-100), d(+30), "valid"),
            ("GRS", "ZZ-EXPIRED", d(-400), d(-5), "valid"),
            ("RCS", "ZZ-REVOKED", d(-200), d(+200), "revoked"),
            ("OCS", "ZZ-PENDING", d(+10), d(+400), "valid")):
        conn.execute("INSERT INTO trc_certs (standard,cert_no,issuer,scope,partner_id,"
                     "valid_from,valid_until,status,created_at) "
                     "VALUES (?,?,'ZZ Body','ZZ scope',?,?,?,?,'2026-01-01')",
                     (std, no, cp, vfrom, vuntil, status))

    # ESG: 1000 kWh over 500 garments = 2.000 kWh/pc; 10 m3 = 10 000 L / 500 = 20 L/pc.
    # The second record has NO garment count — the per-piece cell must be blank, not a crash.
    conn.execute("INSERT INTO trc_esg (label,period,energy_kwh,water_m3,waste_kg,garments,"
                 "source,created_at) VALUES ('ZZ month','2026-05',1000,10,50,500,'ZZSRC',"
                 "'2026-05-31 00:00:00')")
    conn.execute("INSERT INTO trc_esg (label,period,energy_kwh,water_m3,waste_kg,garments,"
                 "source,created_at) VALUES ('ZZ no denominator','2026-06',500,5,25,0,"
                 "'ZZSRC','2026-06-30 00:00:00')")
    conn.commit()
    conn.close()


def run(key, **args):
    with app.app_context():
        return R.run(R.get(key), args)


def kpi(res, key):
    return {k["key"]: k["value"] for k in res["kpis"]}.get(key)


# ------------------------------------------------------------- the numbers --
head("trace_lots — arithmetic")
res = run("trace_lots", material="ZZMAT")
ok("3 lots selected", res["total"] == 3, res["total"])
rows = {r["lot_ref"]: r for r in res["rows"]}
ok("total quantity = 100 + 50 + 0 = 150", near(res["totals"]["qty"], 150),
   res["totals"]["qty"])
# the chain link: ZZL-2 was made from ZZL-1, shown as the parent's REFERENCE
ok("ZZL-2 shows its parent as 'ZZL-1', not as a row id",
   rows["ZZL-2"]["parent_ref"] == "ZZL-1", rows["ZZL-2"]["parent_ref"])
ok("ZZL-1 has no parent — the chain ends there", rows["ZZL-1"]["parent_ref"] in ("", None))
ok("the supplier tier is joined in (tier 3)", near(rows["ZZL-1"]["tier"], 3),
   rows["ZZL-1"]["tier"])
# the NULL-heavy lot must still render: no supplier, no quantity, no dates
ok("a lot with no supplier and no quantity still renders as 0 / blank",
   near(rows["ZZL-3"]["qty"], 0) and rows["ZZL-3"]["supplier"] in ("", None),
   rows["ZZL-3"])
ok("KPI lots = 3", near(kpi(res, "lots"), 3), kpi(res, "lots"))
ok("KPI with an upstream lot = 1 (only ZZL-2)", near(kpi(res, "linked"), 1),
   kpi(res, "linked"))
# COUNT(DISTINCT partner_id) ignores the NULL partner: 1 real supplier
ok("KPI suppliers = 1 (the NULL supplier is not counted)",
   near(kpi(res, "suppliers"), 1), kpi(res, "suppliers"))
res2 = run("trace_lots", material="ZZMAT", tier="3")
ok("the tier filter (a text-cast integer column) returns the two lots with a supplier",
   res2["total"] == 2, res2["total"])

head("trace_certs — arithmetic")
res = run("trace_certs", partner="ZZ Certholder")
ok("4 certificates selected", res["total"] == 4, res["total"])
rows = {r["cert_no"]: r for r in res["rows"]}
# state is DERIVED from the dates, never from the stored column
ok("valid_until = today + 30 -> Valid", rows["ZZ-VALID"]["state"] == "Valid",
   rows["ZZ-VALID"]["state"])
ok("valid_until = today - 5 -> Expired (stored status said 'valid')",
   rows["ZZ-EXPIRED"]["state"] == "Expired", rows["ZZ-EXPIRED"]["state"])
ok("a revoked certificate stays Revoked whatever its dates say",
   rows["ZZ-REVOKED"]["state"] == "Revoked", rows["ZZ-REVOKED"]["state"])
ok("valid_from = today + 10 -> Pending, never Valid",
   rows["ZZ-PENDING"]["state"] == "Pending", rows["ZZ-PENDING"]["state"])
ok("KPI valid today = 1", near(kpi(res, "valid"), 1), kpi(res, "valid"))
ok("KPI expired = 1", near(kpi(res, "expired"), 1), kpi(res, "expired"))
ok("KPI revoked = 1", near(kpi(res, "revoked"), 1), kpi(res, "revoked"))
ok("soonest expiry sorts first",
   res["rows"][0]["cert_no"] == "ZZ-EXPIRED", res["rows"][0]["cert_no"])
r2 = run("trace_certs", partner="ZZ Certholder", state="Expired")
ok("the state filter returns exactly the lapsed certificate",
   r2["total"] == 1 and r2["rows"][0]["cert_no"] == "ZZ-EXPIRED", r2["total"])

head("trace_esg — arithmetic")
res = run("trace_esg", source="ZZSRC")
ok("2 records selected", res["total"] == 2, res["total"])
rows = {r["period"]: r for r in res["rows"]}
# 1000 kWh / 500 garments = 2.000
ok("kWh per garment = 1000/500 = 2.0", near(rows["2026-05"]["kwh_per_pc"], 2.0),
   rows["2026-05"]["kwh_per_pc"])
# 10 m3 = 10 000 litres / 500 garments = 20.00 L
ok("water litres per garment = 1000 x 10/500 = 20.0",
   near(rows["2026-05"]["water_l_per_pc"], 20.0), rows["2026-05"]["water_l_per_pc"])
# 0 garments must give a blank intensity, not a division-by-zero
ok("a record with 0 garments leaves the intensity blank (no division by zero)",
   rows["2026-06"]["kwh_per_pc"] in ("", None), rows["2026-06"]["kwh_per_pc"])
ok("totals: energy 1000 + 500 = 1500", near(res["totals"]["energy_kwh"], 1500),
   res["totals"]["energy_kwh"])
ok("totals: waste 50 + 25 = 75", near(res["totals"]["waste_kg"], 75),
   res["totals"]["waste_kg"])
# fleet intensity = 1500 kWh / (500 + 0) garments = 3.000
ok("KPI kWh per garment = 1500/500 = 3.0", near(kpi(res, "kwh_pc"), 3.0), kpi(res, "kwh_pc"))


# ------------------------------------------------- empty / single / errors --
head("empty, single-row and bad input")
for k in KEYS:
    res = run(k, material="NO-SUCH-THING-EVER", partner="NO-SUCH-THING-EVER",
              source="NO-SUCH-THING-EVER")
    ok("%s: an empty result set renders with no error" % k,
       res["total"] == 0 and res["rows"] == [] and res["error"] is None)
    ok("%s: KPIs on an empty set are 0, never None" % k,
       all(x["value"] == 0 for x in res["kpis"]), [x["value"] for x in res["kpis"]])
    ok("%s: no chart is drawn for an empty set" % k, res["chart"] is None)

res = run("trace_lots", material="ZZMAT scrap")
ok("single-row result renders and totals equal that row",
   res["total"] == 1 and near(res["totals"]["qty"], 0), res["total"])
res = run("trace_certs", **{"period": "custom", "from": "2027-01-01", "to": "2026-01-01"})
ok("an inverted date range is reported, not silently emptied",
   res["error"] == "inverted_range" and res["rows"] == [], res["error"])
res = run("trace_esg", **{"period": "custom", "from": "yesterday"})
ok("a garbage date is reported as bad_date", res["error"] == "bad_date", res["error"])
res = run("trace_lots", material="ZZ'); DROP TABLE trc_lots; --")
ok("a quoted filter value is bound as a parameter, not injected", res["total"] == 0,
   res["total"])
with app.app_context():
    from app.db import get_db
    conn = get_db()
    still = conn.execute("SELECT COUNT(*) c FROM trc_lots").fetchone()["c"]
    conn.close()
ok("trc_lots survived the injected filter value", still > 0, still)


# ----------------------------------------------------------- permissions ---
head("permission — page and all three export formats")
client = app.test_client()
with app.app_context():
    from app.db import get_db
    conn = get_db()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,lang_pref) "
                 "VALUES ('zztrcadm','x','Trace Admin','super_admin',1,'en')")
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,lang_pref) "
                 "VALUES ('zztrcnil','x','No Rights','zz_role_with_no_perms',1,'en')")
    conn.commit()
    rows = {r["username"]: r for r in conn.execute(
        "SELECT id, username, session_epoch FROM users "
        "WHERE username IN ('zztrcadm','zztrcnil')").fetchall()}
    conn.close()
ADM = (rows["zztrcadm"]["id"], rows["zztrcadm"]["session_epoch"] or 0)
NUL = (rows["zztrcnil"]["id"], rows["zztrcnil"]["session_epoch"] or 0)


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
csv = client.get("/reporting/trace_lots.csv?material=ZZMAT").data
ok("CSV carries the filtered rows", b"ZZL-1" in csv and b"ZZL-2" in csv)
ok("CSV starts with a BOM so Excel reads it as UTF-8", csv.startswith(b"\xef\xbb\xbf"))
pdf = client.get("/reporting/trace_certs.pdf?partner=ZZ Certholder")
ok("PDF is a real PDF", pdf.mimetype == "application/pdf" and pdf.data[:5] == b"%PDF-",
   pdf.mimetype)
xls = client.get("/reporting/trace_esg.xlsx?source=ZZSRC")
ok("XLSX is a real workbook", xls.data[:2] == b"PK", xls.mimetype)

login(NUL)
for u in URLS:
    r = client.get(u)
    ok("user without trc_view gets a raw 403 on %s" % u, r.status_code == 403, r.status_code)
    ok("...and reads none of the data from %s" % u, b"ZZL-1" not in r.data)
r = client.get("/reporting/")
ok("the hub lists no traceability report for that user",
   r.status_code == 200 and b"trace_lots" not in r.data, r.status_code)


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
for p in sorted((REPO / "app" / "templates" / "trace").glob("*.html")):
    for key in re.findall(r'data-i18n(?:-ph|-title)?="([^"]+)"', p.read_text(encoding="utf-8")):
        if "{" in key:
            continue                       # Jinja-computed key, resolved at render time
        for lang in ("en", "ar", "tr"):
            if key not in DICT[lang]:
                missing.append((p.name, key, lang))
ok("every data-i18n key on the trace templates resolves in en, ar and tr",
   not missing, missing[:6])
dash = (REPO / "app" / "templates" / "trace" / "dashboard.html").read_text(encoding="utf-8")
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
