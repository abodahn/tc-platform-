"""
Self-verification for the MES reports declared in app/mes/reports.py.

Run it:  PYTHONIOENCODING=utf-8 python app/mes/tests_reports.py

Plain asserts, no framework. Builds its own throwaway SQLite database in a temp
directory (config.Config.DB_PATH is hardcoded to <repo>/platform.db, so
overriding it is mandatory — a test must never write into the repo).

The assertion that matters is the arithmetic: every expected number below is
hand-computed in the comment next to it.
"""
import csv
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="mesrpt_"))
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

KEYS = ["mes_hourly", "mes_line_day", "mes_downtime"]
PERM = "mes_view"
PAGES = ["/mes/", "/mes/board", "/mes/bundles"]
DAY = "2026-06-10"

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
    conn.execute("DELETE FROM mes_hourly")
    conn.execute("DELETE FROM mes_downtime")
    conn.commit()
    conn.close()


def line_ids():
    conn = get_db()
    ids = []
    for name in ("RPT Line A", "RPT Line B"):
        r = conn.execute("SELECT id FROM production_lines WHERE name=?", (name,)).fetchone()
        if not r:
            conn.execute("INSERT INTO production_lines (name) VALUES (?)", (name,))
            conn.commit()
            r = conn.execute("SELECT id FROM production_lines WHERE name=?", (name,)).fetchone()
        ids.append(r["id"])
    conn.close()
    return ids


def hourly(conn, line_id, slot, target, actual, reject, operators, smv):
    conn.execute("INSERT INTO mes_hourly (line_id, work_date, hour_slot, target_qty, "
                 "actual_qty, reject_qty, operators, smv) VALUES (?,?,?,?,?,?,?,?)",
                 (line_id, DAY, slot, target, actual, reject, operators, smv))


def seed_full(a, b):
    """Line A: 3 hours, Line B: 1 hour, plus coded downtime on both.

    target  = 100+100+100+50            = 350
    actual  =  90+ 80+100+50            = 320   -> achievement 320/350 = 91.4%
    reject  =   5+  0+  5+ 0            =  10   -> reject 10/320       =  3.13%
    good    =  85+ 80+ 95+50            = 310
    red hour: only 80/100 = 80% < 85    =   1
    earned min A = (85+80+95)*6 = 1560, operator min A = 10*60*3 = 1800 -> 86.7%
    earned min B =        50*6 =  300, operator min B =  5*60*1 =  300 -> 100.0%
    overall efficiency = 1860/2100                                     ->  88.6%
    downtime A = 30+20 = 50 (NOT 150: the pre-grouped join must not multiply it)
    downtime B = 10                                     total lost minutes = 60
    """
    conn = get_db()
    hourly(conn, a, "08:00-09:00", 100, 90, 5, 10, 6.0)
    hourly(conn, a, "09:00-10:00", 100, 80, 0, 10, 6.0)
    hourly(conn, a, "10:00-11:00", 100, 100, 5, 10, 6.0)
    hourly(conn, b, "08:00-09:00", 50, 50, 0, 5, 6.0)
    for line, reason, mins in ((a, "machine_breakdown", 30.0), (a, "changeover", 20.0),
                               (b, "power", 10.0)):
        conn.execute("INSERT INTO mes_downtime (line_id, work_date, reason, minutes) "
                     "VALUES (?,?,?,?)", (line, DAY, reason, mins))
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
    text = payload.decode("utf-8-sig")
    rdr = csv.reader(io.StringIO(text))
    head = next(rdr, [])
    return [dict(zip(head, r)) for r in rdr]


def kpis(key, args=None):
    with app.app_context():
        res = R.run(R.get(key), args or {})
    return {k["key"]: k["value"] for k in res["kpis"]}, res


with app.app_context():
    A, B = line_ids()
    NOBODY = nobody_id()

ADMIN = client(1)
NO = client(NOBODY)

print("\n=== MES reports ===")

# ---------------------------------------------------------------------------
# 1. Registration
# ---------------------------------------------------------------------------
for k in KEYS:
    spec = R.get(k)
    ok(f"registered {k}", spec is not None and spec["perm"] == PERM,
       "" if spec else "missing")

# ---------------------------------------------------------------------------
# 2. Empty tables — no 500, no division by zero
# ---------------------------------------------------------------------------
with app.app_context():
    wipe()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} empty page 200", r.status_code == 200, str(r.status_code))
    r = ADMIN.get(f"/reporting/{k}.csv")
    ok(f"{k} empty csv 200", r.status_code == 200, str(r.status_code))
    r = ADMIN.get(f"/reporting/{k}.pdf")
    ok(f"{k} empty pdf 200", r.status_code == 200 and r.mimetype == "application/pdf",
       f"{r.status_code} {r.mimetype}")
kp, _ = kpis("mes_hourly")
ok("empty KPIs are 0 not NaN", kp["actual"] == 0 and kp["achievement"] == 0, str(kp))

# ---------------------------------------------------------------------------
# 3. Single row
# ---------------------------------------------------------------------------
with app.app_context():
    conn = get_db()
    hourly(conn, A, "08:00-09:00", 100, 90, 5, 10, 6.0)
    conn.commit()
    conn.close()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} single-row page 200", r.status_code == 200, str(r.status_code))
kp, _ = kpis("mes_hourly")
# 90/100 -> 90.0 ; rejects 5/90 -> 5.56 ; no red hour (90 >= 85)
ok("single row: achievement 90.0", near(kp["achievement"], 90.0), str(kp["achievement"]))
ok("single row: red hours 0", kp["red_hours"] == 0, str(kp["red_hours"]))
# a NULL-heavy row must not blow the aggregate up
with app.app_context():
    conn = get_db()
    conn.execute("INSERT INTO mes_hourly (line_id, work_date, hour_slot, target_qty, "
                 "actual_qty, reject_qty, operators, smv) VALUES (?,?,?,?,?,?,?,?)",
                 (A, DAY, "23:00-00:00", 0, None, None, None, None))
    conn.commit()
    conn.close()
r = ADMIN.get("/reporting/mes_line_day")
ok("NULL-heavy row page 200", r.status_code == 200, str(r.status_code))
kp, _ = kpis("mes_hourly")
ok("NULL row does not corrupt achievement", near(kp["achievement"], 90.0),
   str(kp["achievement"]))

# ---------------------------------------------------------------------------
# 4. Hand-computed arithmetic on the full seed
# ---------------------------------------------------------------------------
with app.app_context():
    wipe()
    seed_full(A, B)

kp, res = kpis("mes_hourly")
ok("hourly: output 320", near(kp["actual"], 320), str(kp["actual"]))
ok("hourly: target 350", near(kp["target"], 350), str(kp["target"]))
ok("hourly: achievement 91.4 (320/350)", near(kp["achievement"], 91.4), str(kp["achievement"]))
ok("hourly: reject 3.13 (10/320)", near(kp["reject_pct"], 3.13), str(kp["reject_pct"]))
ok("hourly: 1 red hour (80/100 < 85)", kp["red_hours"] == 1, str(kp["red_hours"]))
ok("hourly: 4 rows", res["total"] == 4, str(res["total"]))
ok("hourly: totals row good = 310", near(res["totals"]["good"], 310),
   str(res["totals"].get("good")))

kp, res = kpis("mes_line_day")
ok("line/day: 2 line-days", kp["line_days"] == 2, str(kp["line_days"]))
ok("line/day: efficiency 88.6 (1860/2100)", near(kp["efficiency"], 88.6), str(kp["efficiency"]))
ok("line/day: downtime 60 (50+10, not 150)", near(kp["downtime"], 60), str(kp["downtime"]))
rows = {r["line"]: r for r in res["rows"]}
ok("line/day: A efficiency 86.7 (1560/1800)", near(rows["RPT Line A"]["efficiency"], 86.7),
   str(rows.get("RPT Line A", {}).get("efficiency")))
ok("line/day: A downtime 50 not multiplied by 3 hours",
   near(rows["RPT Line A"]["downtime"], 50), str(rows["RPT Line A"]["downtime"]))
ok("line/day: B efficiency 100.0 (300/300)", near(rows["RPT Line B"]["efficiency"], 100.0),
   str(rows["RPT Line B"]["efficiency"]))

kp, res = kpis("mes_downtime")
ok("downtime: 60 lost minutes", near(kp["minutes"], 60), str(kp["minutes"]))
ok("downtime: 3 stoppages", near(kp["events"], 3), str(kp["events"]))
ok("downtime: pareto chart cumulative ends at 100",
   res["chart"] and near(res["chart"]["cumulative"][-1], 100.0),
   str(res["chart"] and res["chart"]["cumulative"]))

# the CSV carries the same numbers as the screen
body = csv_rows(ADMIN.get("/reporting/mes_line_day.csv").data)
byline = {r["Line"]: r for r in body}
ok("csv: A output 270 (90+80+100)", near(byline["RPT Line A"]["Output"], 270),
   byline["RPT Line A"]["Output"])
ok("csv: A achievement 90.0 (270/300)", near(byline["RPT Line A"]["Achievement %"], 90.0),
   byline["RPT Line A"]["Achievement %"])
ok("csv: B downtime 10", near(byline["RPT Line B"]["Downtime min"], 10),
   byline["RPT Line B"]["Downtime min"])

# ---------------------------------------------------------------------------
# 5. Filters and sort do not break the numbers
# ---------------------------------------------------------------------------
kp, res = kpis("mes_hourly", {"line": "RPT Line B"})
ok("filter line=B: output 50", near(kp["actual"], 50), str(kp["actual"]))
r = ADMIN.get("/reporting/mes_hourly?sort=actual&dir=desc")
ok("sortable page 200", r.status_code == 200, str(r.status_code))
r = ADMIN.get("/reporting/mes_hourly?period=custom&from=2026-06-30&to=2026-06-01")
ok("inverted range is an error page, not a crash", r.status_code == 200, str(r.status_code))

# ---------------------------------------------------------------------------
# 6. Permission — page AND all three export formats
# ---------------------------------------------------------------------------
for k in KEYS:
    for url in (f"/reporting/{k}", f"/reporting/{k}.csv", f"/reporting/{k}.xlsx",
                f"/reporting/{k}.pdf"):
        r = NO.get(url)                       # RAW status: no follow_redirects
        leaked = b"RPT Line A" in r.data
        ok(f"no-perm blocked {url}", r.status_code in (302, 403) and not leaked,
           f"{r.status_code} leaked={leaked}")
    r = ADMIN.get(f"/reporting/{k}.xlsx")
    ok(f"{k} xlsx 200", r.status_code == 200 and "spreadsheet" in r.mimetype,
       f"{r.status_code} {r.mimetype}")

# ---------------------------------------------------------------------------
# 7. i18n — every data-i18n key on the templates this lane touched resolves
# ---------------------------------------------------------------------------
DICTS = {lg: json.loads((REPO / "app/static/i18n" / f"{lg}.json").read_text("utf-8"))
         for lg in ("en", "ar", "tr")}
NEW_KEYS = set()   # this lane adds NO i18n key: the Reports link reuses reports.title
for page in PAGES:
    # the RENDERED page, not the template source: a key built in Jinja
    # (data-i18n="mes.reason.{{ r.reason }}") only exists once it is rendered.
    body = ADMIN.get(page).data.decode("utf-8", "replace")
    keys = set(re.findall(r'data-i18n="([^"]+)"', body))
    for lg, d in DICTS.items():
        missing = sorted(k for k in keys if k not in d and k not in NEW_KEYS)
        ok(f"{page} keys resolve in {lg}", not missing, ", ".join(missing[:6]))
# the report page itself, in each language (lang_pref drives the server-side PDF
# text; the page markup is language-independent and swapped client-side)
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

# engine-owned keys (app/templates/reports/*, Lane A + orchestrator i18n): report,
# never assert — they are not this lane's files.
eng = set()
for t in ("reports/report.html", "reports/hub.html"):
    p = REPO / "app/templates" / t
    if p.exists():
        eng |= set(re.findall(r'data-i18n="([^"]+)"', p.read_text("utf-8")))
missing_engine = sorted(k for k in eng if k not in DICTS["en"])
print(f"  NOTE  engine i18n keys still missing from en.json: {len(missing_engine)} "
      f"{missing_engine[:8]}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
