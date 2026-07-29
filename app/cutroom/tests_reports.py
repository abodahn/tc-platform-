"""
Self-verification for the cut-room reports declared in app/cutroom/reports.py.

Run it:  PYTHONIOENCODING=utf-8 python app/cutroom/tests_reports.py

Plain asserts, no framework, throwaway SQLite database in a temp directory
(config.Config.DB_PATH points at <repo>/platform.db, so overriding it is
mandatory — a test must never write into the repo).

The assertion that matters is the arithmetic: every expected number is
hand-computed in the comment beside it.
"""
import csv
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cutrpt_"))
os.chdir(TMP)
REPO = Path(r"D:\TC platform\tc-platform-render")
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                              # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                 # noqa: E402
from app.db import get_db                                  # noqa: E402
from app.routes import reports_hub                         # noqa: E402
from app.services import reporting as R                    # noqa: E402

KEYS = ["cutroom_utilisation", "cutroom_vs_order"]
PERM = "cut_view"
PAGES = ["/cutroom/", "/cutroom/lays"]
DAY = "2026-06-10"
TOKEN = "RPT-LAY-1"

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


def near(a, b, tol=0.05):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


app = create_app()
if "reports_hub" not in app.blueprints:      # the orchestrator registers it in app/__init__
    app.register_blueprint(reports_hub.bp)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def wipe():
    conn = get_db()
    conn.execute("DELETE FROM cut_lays")
    conn.commit()
    conn.close()


def order_id():
    conn = get_db()
    r = conn.execute("SELECT id FROM ord_orders WHERE order_no='RPT-ORD-1'").fetchone()
    if not r:
        conn.execute("INSERT INTO ord_orders (order_no, buyer, style_ref, qty, status) "
                     "VALUES (?,?,?,?,?)", ("RPT-ORD-1", "RPT Buyer", "RPT-STY", 1000, "draft"))
        conn.commit()
        r = conn.execute("SELECT id FROM ord_orders WHERE order_no='RPT-ORD-1'").fetchone()
    conn.close()
    return r["id"]


def lay(conn, no, oid, plies, ppp, ml, end, area, width, actual, eff_pct=0, status="cut"):
    conn.execute(
        "INSERT INTO cut_lays (lay_no, order_id, marker_ref, marker_length_m, "
        "marker_width_cm, fabric_width_cm, marker_area_m2, marker_eff_pct, plies, "
        "pieces_per_ply, end_allow_m, actual_fabric_m, fabric_ref, colour, shade_lot, "
        "cut_date, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (no, oid, "MK-RPT", ml, width, width + 2, area, eff_pct, plies, ppp, end,
         actual, "FAB-RPT", "Indigo", "LOT-1", DAY, status))


def seed_full(oid):
    """Two live lays on one order, plus a CANCELLED lay that must never count.

    Lay 1: 50 plies x 8 pcs      = 400 pcs
           theoretical 7.00 x 50 = 350.0 m ; planned (7.05) x 50 = 352.5 m
           measured 360 m -> used 360      ; utilisation 350/360 = 97.22 %
           marker eff = 9.0 m2 / (7.00 x 1.50 m) = 85.7 %
    Lay 2: 40 plies x 10 pcs     = 400 pcs
           theoretical 10.00 x 40 = 400.0 m ; planned (10.05) x 40 = 402.0 m
           NOT measured (actual 0) -> used = planned 402 ; utilisation 99.50 %
           marker eff falls back to the entered 88.0 %
    Live totals: 800 pcs, used 762.0 m, theoretical 750.0 m
           utilisation 750/762 = 98.43 % ; metres wasted 762 - 750 = 12.0
    Order: 1000 pcs ordered -> cut 80.0 %, balance +200 (short-cut), 0.9525 m/garment
    """
    conn = get_db()
    lay(conn, TOKEN, oid, 50, 8, 7.00, 0.05, 9.0, 150, 360.0)
    lay(conn, "RPT-LAY-2", oid, 40, 10, 10.00, 0.05, 0, 160, 0, eff_pct=88.0)
    lay(conn, "RPT-LAY-X", oid, 100, 10, 10.00, 0.05, 0, 160, 5000.0, status="cancelled")
    conn.commit()
    conn.close()


def nobody_id():
    conn = get_db()
    r = conn.execute("SELECT id FROM users WHERE username='rpt_nobody'").fetchone()
    if not r:
        conn.execute("INSERT INTO users (username, password_hash, full_name, email, role, "
                     "is_active, session_epoch) VALUES (?,?,?,?,?,?,?)",
                     ("rpt_nobody", "x", "No Body", "nobody@tc.test", "normal_user", 1, 0))
        conn.commit()
        r = conn.execute("SELECT id FROM users WHERE username='rpt_nobody'").fetchone()
    conn.close()
    return r["id"]


def client(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


def csv_rows(payload):
    rdr = csv.reader(io.StringIO(payload.decode("utf-8-sig")))
    head = next(rdr, [])
    return [dict(zip(head, r)) for r in rdr]


def kpis(key, args=None):
    with app.app_context():
        res = R.run(R.get(key), args or {})
    return {k["key"]: k["value"] for k in res["kpis"]}, res


with app.app_context():
    OID = order_id()
    NOBODY = nobody_id()

ADMIN = client(1)
NO = client(NOBODY)

print("\n=== Cut room reports ===")

for k in KEYS:
    spec = R.get(k)
    ok(f"registered {k}", spec is not None and spec["perm"] == PERM, "" if spec else "missing")

# --- empty ------------------------------------------------------------------
with app.app_context():
    wipe()
for k in KEYS:
    for suffix, mime in (("", None), (".csv", None), (".pdf", "application/pdf")):
        r = ADMIN.get(f"/reporting/{k}{suffix}")
        ok(f"{k}{suffix} empty 200", r.status_code == 200 and (not mime or r.mimetype == mime),
           f"{r.status_code} {r.mimetype}")
kp, _ = kpis("cutroom_utilisation")
ok("empty KPIs are 0 not NaN", kp["lays"] == 0 and kp["util"] == 0, str(kp))

# --- single row + NULL-heavy row --------------------------------------------
with app.app_context():
    conn = get_db()
    lay(conn, TOKEN, OID, 50, 8, 7.00, 0.05, 9.0, 150, 360.0)
    conn.commit()
    conn.close()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} single-row page 200", r.status_code == 200, str(r.status_code))
kp, res = kpis("cutroom_utilisation")
ok("single lay: 400 pieces (50 x 8)", near(kp["pieces"], 400), str(kp["pieces"]))
ok("single lay: utilisation 97.22 (350/360)", near(kp["util"], 97.22), str(kp["util"]))
ok("single lay: marker eff 85.7 (9.0 / 10.5)", near(res["rows"][0]["marker_eff"], 85.7),
   str(res["rows"][0]["marker_eff"]))
with app.app_context():
    conn = get_db()
    conn.execute("INSERT INTO cut_lays (lay_no, order_id, cut_date, status) VALUES (?,?,?,?)",
                 ("RPT-LAY-NULL", OID, DAY, "planned"))     # every metric column NULL
    conn.commit()
    conn.close()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} NULL-heavy row page 200", r.status_code == 200, str(r.status_code))
kp, _ = kpis("cutroom_utilisation")
ok("NULL row does not corrupt pieces", near(kp["pieces"], 400), str(kp["pieces"]))

# --- full seed, hand-computed ----------------------------------------------
with app.app_context():
    wipe()
    seed_full(OID)

kp, res = kpis("cutroom_utilisation")
ok("cancelled lay excluded (2 lays, not 3)", kp["lays"] == 2, str(kp["lays"]))
ok("pieces 800 (400 + 400)", near(kp["pieces"], 800), str(kp["pieces"]))
ok("fabric used 762.0 (360 measured + 402 planned)", near(kp["used_m"], 762.0, 0.11),
   str(kp["used_m"]))
ok("utilisation 98.43 (750/762)", near(kp["util"], 98.43), str(kp["util"]))
ok("metres wasted 12.0 (762 - 750)", near(kp["waste_m"], 12.0, 0.11), str(kp["waste_m"]))
rows = {r["lay_no"]: r for r in res["rows"]}
ok("lay 1: 0.9 m per garment (360/400)", near(rows[TOKEN]["cons"], 0.9, 0.0005),
   str(rows[TOKEN]["cons"]))
ok("lay 2: unmeasured falls back to planned 402", near(rows["RPT-LAY-2"]["used_m"], 402.0, 0.011),
   str(rows["RPT-LAY-2"]["used_m"]))
ok("lay 2: marker eff falls back to the entered 88.0",
   near(rows["RPT-LAY-2"]["marker_eff"], 88.0), str(rows["RPT-LAY-2"]["marker_eff"]))
ok("lay 2: waste 0.5 (100 - 400/402)", near(rows["RPT-LAY-2"]["waste"], 0.5),
   str(rows["RPT-LAY-2"]["waste"]))

kp, res = kpis("cutroom_vs_order")
ok("vs order: one order group", kp["orders"] == 1, str(kp["orders"]))
ok("vs order: ordered 1000 counted ONCE, not once per lay", near(kp["order_qty"], 1000),
   str(kp["order_qty"]))
ok("vs order: cut 800", near(kp["pieces"], 800), str(kp["pieces"]))
ok("vs order: cut 80.0 % (800/1000)", near(kp["cut_pct"], 80.0), str(kp["cut_pct"]))
ok("vs order: 1 order short-cut", kp["short"] == 1, str(kp["short"]))
row = res["rows"][0]
ok("vs order: balance +200 (1000 - 800)", near(row["balance"], 200), str(row["balance"]))
ok("vs order: 0.9525 m per garment (762/800)", near(row["cons"], 0.9525, 0.0005),
   str(row["cons"]))

body = csv_rows(ADMIN.get("/reporting/cutroom_utilisation.csv").data)
bylay = {r["Lay"]: r for r in body}
ok("csv: lay 1 utilisation 97.22", near(bylay[TOKEN]["Utilisation %"], 97.22),
   bylay[TOKEN]["Utilisation %"])
ok("csv: cancelled lay absent", "RPT-LAY-X" not in bylay, ", ".join(bylay))

kp, _ = kpis("cutroom_utilisation", {"status": "cut"})
ok("filter status=cut keeps both lays", kp["lays"] == 2, str(kp["lays"]))

# --- permission on the page AND all three exports ---------------------------
for k in KEYS:
    for url in (f"/reporting/{k}", f"/reporting/{k}.csv", f"/reporting/{k}.xlsx",
                f"/reporting/{k}.pdf"):
        r = NO.get(url)                       # RAW status: no follow_redirects
        leaked = TOKEN.encode() in r.data
        ok(f"no-perm blocked {url}", r.status_code in (302, 403) and not leaked,
           f"{r.status_code} leaked={leaked}")
    r = ADMIN.get(f"/reporting/{k}.xlsx")
    ok(f"{k} xlsx 200", r.status_code == 200 and "spreadsheet" in r.mimetype,
       f"{r.status_code} {r.mimetype}")

# --- i18n -------------------------------------------------------------------
DICTS = {lg: json.loads((REPO / "app/static/i18n" / f"{lg}.json").read_text("utf-8"))
         for lg in ("en", "ar", "tr")}
NEW_KEYS = set()   # this lane adds NO i18n key: the Reports link reuses reports.title
for page in PAGES:
    # the RENDERED page, not the template source: keys built in Jinja only exist
    # once rendered.
    body = ADMIN.get(page).data.decode("utf-8", "replace")
    keys = set(re.findall(r'data-i18n="([^"]+)"', body))
    for lg, d in DICTS.items():
        missing = sorted(k for k in keys if k not in d and k not in NEW_KEYS)
        ok(f"{page} keys resolve in {lg}", not missing, ", ".join(missing[:6]))
for lg in ("en", "ar", "tr"):
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lg,))
        conn.commit()
        conn.close()
    for k in KEYS:
        r = ADMIN.get(f"/reporting/{k}")
        ok(f"{k} page 200 with lang_pref={lg}", r.status_code == 200, str(r.status_code))
        r = ADMIN.get(f"/reporting/{k}.csv")
        ok(f"{k} csv 200 with lang_pref={lg}", r.status_code == 200, str(r.status_code))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
