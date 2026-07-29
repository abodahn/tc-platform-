"""Self-verification for the HR Core reports (app/people/reports.py).

Run it:  PYTHONIOENCODING=utf-8 python app/people/tests_reports.py

Plain asserts, no framework. It builds a throwaway SQLite database in a temp
directory (config.Config.DB_PATH is hardcoded to <repo>/platform.db, so
overriding it is mandatory — the test must never write into the repo) and
replaces the demo HR rows with a fixture whose every number is hand-computed in
the assertion text below. A report with wrong numbers is worse than no report.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="pplrpt_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                            # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

import json                                              # noqa: E402
import re                                                # noqa: E402
from datetime import date, timedelta                     # noqa: E402

from app import create_app                               # noqa: E402
from app.db import get_db                                # noqa: E402
from app.routes import reports_hub                       # noqa: E402
from app.services import reporting as R                  # noqa: E402

REPO = Path(r"D:\TC platform\tc-platform-render")
KEYS = ["people_attendance", "people_leave_liability", "people_skill_coverage",
        "people_incentive"]
PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


def near(a, b, tol=0.005):
    return a is not None and abs(float(a) - float(b)) <= tol


app = create_app()
app.register_blueprint(reports_hub.bp)

TODAY = date.today()
D1, D2, D3 = (str(TODAY - timedelta(days=2)), str(TODAY - timedelta(days=1)), str(TODAY))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
def wipe(conn):
    for t in ("ppl_attendance", "ppl_leave", "ppl_leave_balance", "ppl_skills",
              "ppl_piece_rate"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


def employees(conn):
    """Three employees on the platform master, created only if this test's codes
    are absent (the roster is shared and must never be reset)."""
    ids = []
    for code, name, dept, sec in (("RPT-A", "Amal A", "Sewing", "Line 1"),
                                  ("RPT-B", "Basma B", "Sewing", "Line 2"),
                                  ("RPT-C", "Cemal C", "Cutting", "Cut 1")):
        row = conn.execute("SELECT id FROM prob_employees WHERE employee_code=?",
                           (code,)).fetchone()
        if not row:
            conn.execute("INSERT INTO prob_employees (employee_code,employee_name,"
                         "department,section,active,is_deleted,created_at) "
                         "VALUES (?,?,?,?,1,0,?)", (code, name, dept, sec, str(TODAY)))
            row = conn.execute("SELECT id FROM prob_employees WHERE employee_code=?",
                               (code,)).fetchone()
        ids.append(row["id"])
    conn.commit()
    return ids


def att(conn, eid, day, status, worked=0.0, ot=0.0):
    conn.execute("INSERT INTO ppl_attendance (employee_id,work_date,status,worked_hours,"
                 "ot_hours,created_by,created_at) VALUES (?,?,?,?,?,?,?)",
                 (eid, day, status, worked, ot, "test", str(TODAY)))


with app.app_context():
    conn = get_db()
    E = employees(conn)
    wipe(conn)
    # a user with NO HR permission
    if not conn.execute("SELECT id FROM users WHERE username='ppl_nobody'").fetchone():
        conn.execute("INSERT INTO users (username,password_hash,full_name,email,role,"
                     "is_active,session_epoch) VALUES (?,?,?,?,?,?,?)",
                     ("ppl_nobody", "x", "No Body", "ppl_nobody@x.tc", "normal_user", 1, 0))
    NOBODY = conn.execute("SELECT id FROM users WHERE username='ppl_nobody'").fetchone()["id"]
    conn.commit()
    conn.close()


def client(uid=1):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


print("\n=== people reports ===")

# ---------------------------------------------------------------------------
# 1. Every report renders on an EMPTY dataset (no 500, no division by zero)
# ---------------------------------------------------------------------------
c = client()
for k in KEYS:
    r = c.get(f"/reporting/{k}")
    ok(f"empty dataset: {k} page 200", r.status_code == 200, f"status={r.status_code}")
with app.app_context():
    for k in KEYS:
        res = R.run(R.get(k), {})
        zero = all(near(x["value"], 0) for x in res["kpis"])
        ok(f"empty dataset: {k} KPIs are 0, not an error",
           res["total"] == 0 and zero and not res["error"])

# ---------------------------------------------------------------------------
# 2. Single row — a one-row dataset must not divide by zero either
# ---------------------------------------------------------------------------
with app.app_context():
    conn = get_db()
    att(conn, E[0], D3, "present", 8.0, 0.0)
    conn.commit()
    res = R.run(R.get("people_attendance"), {})
    k = {x["key"]: x for x in res["kpis"]}
    # 1 row, scheduled 1, present 1 -> attendance 100%, absenteeism 0%
    ok("single row: attendance 100% / absenteeism 0%",
       res["total"] == 1 and near(k["attendance_pct"]["value"], 100.0)
       and near(k["absenteeism_pct"]["value"], 0.0),
       f"att={k['attendance_pct']['value']} abs={k['absenteeism_pct']['value']}")
    wipe(conn)
    conn.close()

# ---------------------------------------------------------------------------
# 3. The real fixture + hand-computed arithmetic
# ---------------------------------------------------------------------------
with app.app_context():
    conn = get_db()
    # --- attendance: 9 rows over 3 days ------------------------------------
    att(conn, E[0], D1, "present", 8.0, 1.0)
    att(conn, E[1], D1, "present", 8.0, 0.0)
    att(conn, E[2], D1, "absent")
    att(conn, E[0], D2, "absent")
    att(conn, E[1], D2, "present", 8.0, 2.0)
    att(conn, E[2], D2, "leave")
    att(conn, E[0], D3, "late", 8.0, 0.0)
    att(conn, E[1], D3, "off")
    att(conn, E[2], D3, "holiday")
    # --- leave balances ----------------------------------------------------
    for eid, lt, ent, taken in ((E[0], "annual", 21.0, 5.0), (E[1], "annual", 21.0, 0.0),
                                (E[2], "sick", 7.0, 2.0)):
        conn.execute("INSERT INTO ppl_leave_balance (employee_id,leave_type,year,entitled,"
                     "taken,updated_at) VALUES (?,?,?,?,?,?)",
                     (eid, lt, TODAY.year, ent, taken, str(TODAY)))
    # --- skills ------------------------------------------------------------
    for eid, op, lvl, eff in ((E[0], "Overlock", 5, 108.0), (E[1], "Overlock", 3, 90.0),
                              (E[0], "Bartack", 1, 40.0), (E[2], "Packing", 4, 100.0)):
        conn.execute("INSERT INTO ppl_skills (employee_id,operation,level,efficiency_pct,"
                     "updated_at) VALUES (?,?,?,?,?)", (eid, op, lvl, eff, str(TODAY)))
    # --- piece rate --------------------------------------------------------
    for eid, day, pieces, smv, em, mw, inc in (
            (E[0], D3, 900.0, 0.55, 495.0, 480.0, 50.0),
            (E[0], D2, 600.0, 0.55, 330.0, 480.0, 0.0),
            (E[1], D3, 1000.0, 0.30, 300.0, 480.0, 0.0)):
        conn.execute("INSERT INTO ppl_piece_rate (employee_id,work_date,operation,pieces,smv,"
                     "minutes_worked,earned_minutes,incentive,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (eid, day, "Overlock", pieces, smv, mw, em, inc, str(TODAY)))
    conn.commit()
    conn.close()

with app.app_context():
    res = R.run(R.get("people_attendance"), {})
    k = {x["key"]: x for x in res["kpis"]}
    # scheduled = present|absent|late|leave = 3(A) + 2(B) + 2(C) = 7   (off/holiday excluded)
    # present+late = 2(A: 1 present + 1 late) + 2(B) = 4  -> 4/7 = 57.142857%
    # absent = 1(A) + 1(C) = 2                            -> 2/7 = 28.571429%
    ok("attendance: 9 rows marked", res["total"] == 9, f"total={res['total']}")
    ok("attendance: scheduled = 7 (off/holiday excluded)", near(k["scheduled"]["value"], 7))
    ok("attendance: attendance% = 4/7 = 57.14",
       near(k["attendance_pct"]["value"], 57.142857, 0.01),
       f"={k['attendance_pct']['value']}")
    ok("attendance: absenteeism% = 2/7 = 28.57",
       near(k["absenteeism_pct"]["value"], 28.571429, 0.01),
       f"={k['absenteeism_pct']['value']}")
    ok("attendance: late = 1", near(k["late"]["value"], 1))
    ok("attendance: OT = 1.0 + 2.0 = 3.0", near(k["ot"]["value"], 3.0))
    # worked hours: A 8 + 8, B 8 + 8 = 32
    ok("attendance: worked-hours total = 32", near(res["totals"]["worked_hours"], 32.0),
       f"={res['totals']['worked_hours']}")
    ok("attendance: OT totals row = 3", near(res["totals"]["ot_hours"], 3.0))
    # chart: absences per day = D1:1, D2:1, D3:0 (D3 has no absence row at all)
    ch = res["chart"]
    ok("attendance chart: absences per day = 1,1,0",
       ch and ch["labels"] == [D1, D2, D3] and ch["values"] == [1, 1, 0],
       f"{ch and list(zip(ch['labels'], ch['values']))}")

    f = R.run(R.get("people_attendance"), {"status": "absent"})
    ok("attendance filter status=absent -> 2 rows", f["total"] == 2, f"total={f['total']}")
    f = R.run(R.get("people_attendance"), {"department": "Cutting"})
    ok("attendance filter department -> 3 rows (one employee x 3 days)", f["total"] == 3,
       f"total={f['total']}")

    res = R.run(R.get("people_leave_liability"), {})
    k = {x["key"]: x for x in res["kpis"]}
    # entitled 21+21+7 = 49 ; taken 5+0+2 = 7 ; owed 49-7 = 42
    ok("leave: 3 balance rows", res["total"] == 3)
    ok("leave: entitled = 49", near(k["entitled"]["value"], 49.0))
    ok("leave: taken = 7", near(k["taken"]["value"], 7.0))
    ok("leave: days owed = 42", near(k["remaining"]["value"], 42.0),
       f"={k['remaining']['value']}")
    ok("leave: totals row remaining = 42", near(res["totals"]["remaining"], 42.0))
    # bar chart: Sewing (16 + 21) = 37, Cutting 5
    ch = res["chart"]
    ok("leave chart: Sewing 37, Cutting 5",
       ch and dict(zip(ch["labels"], ch["values"])) == {"Sewing": 37.0, "Cutting": 5.0},
       f"{ch and list(zip(ch['labels'], ch['values']))}")
    f = R.run(R.get("people_leave_liability"), {"leave_type": "annual"})
    ok("leave filter type=annual -> 2 rows, owed 37", f["total"] == 2
       and near({x['key']: x for x in f["kpis"]}["remaining"]["value"], 37.0))

    res = R.run(R.get("people_skill_coverage"), {})
    k = {x["key"]: x for x in res["kpis"]}
    rows = {r["operation"]: r for r in res["rows"]}
    # Overlock: rated 2, capable 2 (levels 5,3), experts 1 (level 5), avg eff (108+90)/2 = 99
    # Bartack : rated 1, capable 0 (level 1 is a trainee)  -> uncovered AND a SPOF
    # Packing : rated 1, capable 1                          -> a SPOF
    ok("skills: 3 operations rated", res["total"] == 3, f"total={res['total']}")
    ok("skills: Overlock capable 2, experts 1, avg eff 99",
       near(rows["Overlock"]["capable"], 2) and near(rows["Overlock"]["experts"], 1)
       and near(rows["Overlock"]["avg_eff"], 99.0), f"{rows.get('Overlock')}")
    ok("skills: level-1 trainee is NOT capable (Bartack capable = 0)",
       near(rows["Bartack"]["capable"], 0))
    ok("skills: single points of failure = 2 (Bartack, Packing)",
       near(k["spof"]["value"], 2), f"={k['spof']['value']}")
    ok("skills: no capable operator = 1 (Bartack)", near(k["uncovered"]["value"], 1))
    ok("skills: thinnest coverage sorts first",
       [r["operation"] for r in res["rows"]] == ["Bartack", "Packing", "Overlock"],
       f"{[r['operation'] for r in res['rows']]}")

    res = R.run(R.get("people_incentive"), {})
    k = {x["key"]: x for x in res["kpis"]}
    rows = {r["code"]: r for r in res["rows"]}
    # A: 2 days, 1500 pcs, earned 825, worked 960 -> 85.9375% ; B: 300/480 -> 62.5%
    # overall: 1125 / 1440 = 78.125% (minute-weighted, NOT the mean of 85.94 and 62.5)
    ok("incentive: 2 operators", res["total"] == 2, f"total={res['total']}")
    ok("incentive: A = 2 days, 1500 pieces, 85.94%",
       near(rows["RPT-A"]["days"], 2) and near(rows["RPT-A"]["pieces"], 1500)
       and near(rows["RPT-A"]["efficiency_pct"], 85.9375, 0.01), f"{rows.get('RPT-A')}")
    ok("incentive: overall efficiency is minute-weighted 1125/1440 = 78.125",
       near(k["efficiency_pct"]["value"], 78.125, 0.01), f"={k['efficiency_pct']['value']}")
    ok("incentive: totals row efficiency = 78.125",
       near(res["totals"]["efficiency_pct"], 78.125, 0.01))
    ok("incentive: incentive total = 50", near(k["incentive"]["value"], 50.0))
    ok("incentive: pieces total = 2500", near(k["pieces"]["value"], 2500.0))
    ch = res["chart"]
    # D2: 330/480 = 68.75 ; D3: (495+300)/(480+480) = 82.8125
    ok("incentive chart: efficiency per day 68.75 then 82.81",
       ch and ch["labels"] == [D2, D3] and near(ch["values"][0], 68.75)
       and near(ch["values"][1], 82.8125, 0.01),
       f"{ch and list(zip(ch['labels'], ch['values']))}")

# ---------------------------------------------------------------------------
# 4. Permission — page AND every export format
# ---------------------------------------------------------------------------
nob = client(NOBODY)
for k in KEYS:
    r = nob.get(f"/reporting/{k}")
    body = r.get_data()
    ok(f"perm: {k} page denied without ppl_view",
       r.status_code in (302, 403) and b"57.1" not in body and b"RPT-A" not in body,
       f"status={r.status_code}")
    for fmt in ("csv", "xlsx", "pdf"):
        r = nob.get(f"/reporting/{k}.{fmt}")
        ok(f"perm: {k}.{fmt} denied without ppl_view",
           r.status_code in (302, 403) and b"RPT-A" not in r.get_data(),
           f"status={r.status_code}")

c = client()
for k in KEYS:
    for fmt, mime in (("csv", "text/csv"), ("xlsx", "application/"), ("pdf", "application/pdf")):
        r = c.get(f"/reporting/{k}.{fmt}")
        ok(f"export: {k}.{fmt} 200 for a permitted user",
           r.status_code == 200 and mime in r.mimetype and len(r.get_data()) > 0,
           f"status={r.status_code} mime={r.mimetype}")
csv_body = c.get("/reporting/people_incentive.csv").get_data().decode("utf-8-sig")
ok("export: CSV carries the computed rows", "RPT-A" in csv_body and "1500" in csv_body)

# ---------------------------------------------------------------------------
# 5. i18n — the module page and the report page in en / ar / tr
# ---------------------------------------------------------------------------
DICT = {lg: json.loads((REPO / "app/static/i18n" / f"{lg}.json").read_text(encoding="utf-8"))
        for lg in ("en", "ar", "tr")}
# keys owned by the reporting hub's own templates (another lane) — reported, not asserted
HUB_KEYS = {k for k in re.findall(
    r'data-i18n="([^"]+)"',
    (REPO / "app/templates/reports/report.html").read_text(encoding="utf-8")
    + (REPO / "app/templates/reports/hub.html").read_text(encoding="utf-8"))}

for lg in ("en", "ar", "tr"):
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lg,))
        conn.commit()
        conn.close()
    c = client()
    r = c.get("/people/")
    body = r.get_data(as_text=True)
    keys = set(re.findall(r'data-i18n="([^"]+)"', body))
    missing = sorted(k for k in keys if k not in DICT[lg])
    ok(f"i18n[{lg}]: /people dashboard 200 and every data-i18n key resolves",
       r.status_code == 200 and not missing, f"missing={missing}")
    for key in KEYS:
        r = c.get(f"/reporting/{key}")
        body = r.get_data(as_text=True)
        keys = set(re.findall(r'data-i18n="([^"]+)"', body))
        mine = sorted(k for k in keys if k not in DICT[lg] and k not in HUB_KEYS)
        ok(f"i18n[{lg}]: {key} page 200, no unresolved key of ours",
           r.status_code == 200 and not mine, f"status={r.status_code} missing={mine}")

with app.app_context():
    conn = get_db()
    conn.execute("UPDATE users SET lang_pref='en' WHERE id=1")
    conn.commit()
    conn.close()

# every declared label really is translated (an empty ar/tr silently falls back)
for key in KEYS:
    spec = R.get(key)
    holes = []
    for lg in ("ar", "tr"):
        if not spec[f"title_{lg}"] or not spec[f"desc_{lg}"]:
            holes.append(f"{key}.title/desc.{lg}")
        for c_ in spec["columns"] + spec["kpis"] + spec["filters"]:
            if not c_.get(lg):
                holes.append(f"{key}.{c_.get('key') or c_.get('name')}.{lg}")
        if spec["chart"] and not spec["chart"].get(lg):
            holes.append(f"{key}.chart.{lg}")
    ok(f"i18n: {key} has AR + TR for every label", not holes, f"{holes}")

hub_missing = sorted(k for k in HUB_KEYS if k not in DICT["en"])
if hub_missing:
    print(f"  NOTE  reporting-hub template keys not yet in en.json (another lane's): "
          f"{hub_missing}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
