"""
Self-verification for the quality reports declared in app/quality/reports.py.

Run it:  PYTHONIOENCODING=utf-8 python app/quality/tests_reports.py

Plain asserts, no framework, throwaway SQLite database in a temp directory
(config.Config.DB_PATH points at <repo>/platform.db, so overriding it is
mandatory — a test must never write into the repo).

The assertion that matters is the arithmetic; every expected number is
hand-computed in the comment beside it. The "units 300" assertions exist to catch
a fan-out: the defect count comes from a PRE-GROUPED sub-query, and joining
qc_defects raw would repeat an inspection once per defect line and double its
units — the classic way a DHU report reads a third of the truth.
"""
import csv
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="qcrpt_"))
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

KEYS = ["quality_inspections", "quality_defect_pareto", "quality_stage_dhu"]
PERM = "qc_view"
PAGES = ["/quality/", "/quality/inspections"]
DAY = "2026-06-10"
TOKEN = "RPT-QC-1"

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
    conn.execute("DELETE FROM qc_defects")
    conn.execute("DELETE FROM qc_inspections")
    conn.commit()
    conn.close()


def inspection(conn, ref, stage, units, defective, verdict, defects=()):
    cur = conn.execute(
        "INSERT INTO qc_inspections (ref, stage, lot_size, aql, code_letter, sample_size, "
        "accept_no, reject_no, units_inspected, defective_units, verdict, inspector, "
        "inspection_date, fail_alerted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (ref, stage, 3200, 2.5, "K", 125, 7, 8, units, defective, verdict,
         "RPT Inspector", DAY))
    iid = cur.lastrowid
    for dtype, section, qty, sev in defects:
        conn.execute("INSERT INTO qc_defects (inspection_id, defect_type, section, qty, "
                     "severity) VALUES (?,?,?,?,?)", (iid, dtype, section, qty, sev))
    return iid


def seed_full():
    """Three inspections; the first carries TWO defect lines.

    i1 sewing_inline  units 100, defective 10, FAIL, defects 5 + 3 = 8
       -> DHU 8/100 x 100 = 8.00 ; RFT (100-10)/100 = 90.00 %
    i2 final          units 200, defective  4, PASS, defects 2
       -> DHU 2/200 x 100 = 1.00 ; RFT (200-4)/200 = 98.00 %
    i3 pre_final      units   0, defective  0, PENDING, no defects
       -> no denominator: excluded from the stage roll-up, present in the register
    Overall: 3 inspections, units 300 (NOT 400 — i1 must not be counted twice),
             defects 10, DHU 10/300 x 100 = 3.33, RFT (300-14)/300 = 95.33 %,
             pass rate 1/3 = 33.3 %, 1 failed lot.
    """
    conn = get_db()
    inspection(conn, TOKEN, "sewing_inline", 100, 10, "fail",
               [("Broken stitch", "sewing", 5, "major"),
                ("Open seam", "sewing", 3, "critical")])
    inspection(conn, "RPT-QC-2", "final", 200, 4, "pass",
               [("Stain / soil mark", "finishing", 2, "minor")])
    inspection(conn, "RPT-QC-3", "pre_final", 0, 0, "pending")
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
    NOBODY = nobody_id()

ADMIN = client(1)
NO = client(NOBODY)

print("\n=== Quality reports ===")

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
kp, _ = kpis("quality_inspections")
ok("empty KPIs are 0 not NaN", kp["units"] == 0 and kp["dhu"] == 0 and kp["rft"] == 0, str(kp))

# --- single row + a zero-units row (no denominator) -------------------------
with app.app_context():
    conn = get_db()
    inspection(conn, TOKEN, "final", 100, 5, "pass", [("Loose thread", "finishing", 4, "minor")])
    conn.commit()
    conn.close()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} single-row page 200", r.status_code == 200, str(r.status_code))
kp, res = kpis("quality_inspections")
ok("single: DHU 4.0 (4/100)", near(kp["dhu"], 4.0), str(kp["dhu"]))
ok("single: RFT 95.0 ((100-5)/100)", near(kp["rft"], 95.0), str(kp["rft"]))
ok("single: pass rate 100", near(kp["pass_rate"], 100.0), str(kp["pass_rate"]))
with app.app_context():
    conn = get_db()
    # zero units and NULL everything optional: no denominator anywhere
    conn.execute("INSERT INTO qc_inspections (ref, stage, units_inspected, defective_units, "
                 "verdict, inspection_date) VALUES (?,?,?,?,?,?)",
                 ("RPT-QC-NULL", None, 0, None, "pending", DAY))
    conn.commit()
    conn.close()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} zero/NULL row page 200", r.status_code == 200, str(r.status_code))
kp, _ = kpis("quality_inspections")
ok("zero-unit row does not corrupt DHU", near(kp["dhu"], 4.0), str(kp["dhu"]))

# --- full seed, hand-computed ----------------------------------------------
with app.app_context():
    wipe()
    seed_full()

kp, res = kpis("quality_inspections")
ok("3 inspections", near(kp["inspections"], 3), str(kp["inspections"]))
ok("units 300 — the 2-defect lot is NOT double counted", near(kp["units"], 300),
   str(kp["units"]))
ok("DHU 3.33 (10 defects / 300 units)", near(kp["dhu"], 3.33), str(kp["dhu"]))
ok("RFT 95.33 ((300-14)/300)", near(kp["rft"], 95.33), str(kp["rft"]))
ok("pass rate 33.3 (1 of 3)", near(kp["pass_rate"], 33.3), str(kp["pass_rate"]))
ok("1 failed lot", near(kp["failed"], 1), str(kp["failed"]))
rows = {r["ref"]: r for r in res["rows"]}
ok("i1 DHU 8.0 (8/100)", near(rows[TOKEN]["dhu"], 8.0), str(rows[TOKEN]["dhu"]))
ok("i1 RFT 90.0 ((100-10)/100)", near(rows[TOKEN]["rft"], 90.0), str(rows[TOKEN]["rft"]))
ok("i1 defects 8 (5 + 3)", near(rows[TOKEN]["defects"], 8), str(rows[TOKEN]["defects"]))
ok("i3 zero units leaves DHU blank, not 0", rows["RPT-QC-3"]["dhu"] == "",
   str(rows["RPT-QC-3"]["dhu"]))

kp, res = kpis("quality_defect_pareto")
ok("pareto: 10 defects", near(kp["qty"], 10), str(kp["qty"]))
ok("pareto: 3 occurrences", near(kp["lines"], 3), str(kp["lines"]))
ok("pareto: 3 critical (the open seam)", near(kp["critical"], 3), str(kp["critical"]))
ok("pareto: top defect is the broken stitch (5)",
   res["rows"][0]["defect_type"] == "Broken stitch" and near(res["rows"][0]["qty"], 5),
   str(res["rows"][0]))
ok("pareto: cumulative ends at 100",
   res["chart"] and near(res["chart"]["cumulative"][-1], 100.0),
   str(res["chart"] and res["chart"]["cumulative"]))
kp, _ = kpis("quality_defect_pareto", {"section": "sewing"})
ok("filter section=sewing: 8 defects", near(kp["qty"], 8), str(kp["qty"]))

kp, res = kpis("quality_stage_dhu")
ok("stage: only the 2 stages with counted units", near(len(res["rows"]), 2),
   str([r["stage"] for r in res["rows"]]))
ok("stage: units 300", near(kp["units"], 300), str(kp["units"]))
ok("stage: DHU 3.33", near(kp["dhu"], 3.33), str(kp["dhu"]))
ok("stage: 1 failed lot", near(kp["failed"], 1), str(kp["failed"]))
bystage = {r["stage"]: r for r in res["rows"]}
ok("stage sewing_inline: DHU 8.0", near(bystage["sewing_inline"]["dhu"], 8.0),
   str(bystage["sewing_inline"]["dhu"]))
ok("stage sewing_inline: RFT 90.0", near(bystage["sewing_inline"]["rft"], 90.0),
   str(bystage["sewing_inline"]["rft"]))
ok("stage final: DHU 1.0 (2/200)", near(bystage["final"]["dhu"], 1.0),
   str(bystage["final"]["dhu"]))
ok("stage final: units 200 counted once", near(bystage["final"]["units"], 200),
   str(bystage["final"]["units"]))
ok("stage: worst DHU sorts first", res["rows"][0]["stage"] == "sewing_inline",
   res["rows"][0]["stage"])

body = csv_rows(ADMIN.get("/reporting/quality_stage_dhu.csv").data)
byst = {r["Stage"]: r for r in body}
ok("csv: sewing_inline DHU 8.0", near(byst["sewing_inline"]["DHU"], 8.0),
   byst["sewing_inline"]["DHU"])
ok("csv: final RFT 98.0", near(byst["final"]["RFT %"], 98.0), byst["final"]["RFT %"])

kp, _ = kpis("quality_inspections", {"verdict": "fail"})
ok("filter verdict=fail: 1 inspection, 100 units", near(kp["inspections"], 1)
   and near(kp["units"], 100), str(kp))

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
