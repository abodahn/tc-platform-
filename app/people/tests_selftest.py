"""
HR Core self-test — drives the service layer against an isolated throwaway DB.
Run:  python app/people/tests_selftest.py
Covers the exact-number paths (attendance %, leave balance, earned-minute
incentive) and the negative cases: bad dates, garbage numerics, missing
employee, zero minutes, double-approve, double-restore, empty database.
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ppl_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                      # noqa: E402

app = create_app()
OK = []


def check(label, cond):
    assert cond, "FAILED: " + label
    OK.append(label)
    print("  ok  " + label)


with app.app_context():
    from app.db import get_db
    from app.people.schema import create_and_seed
    from app.people import services as svc

    conn = get_db()
    create_and_seed(conn)
    create_and_seed(conn)            # must be safe to run on every boot, forever
    conn.commit()

    def cnt(sql, args=()):
        c = get_db()
        try:
            return c.execute(sql, args).fetchone()["c"]
        finally:
            c.close()

    emps = svc.employees()
    check("roster read from prob_employees (not forked)", len(emps) >= 2)
    e0, e1 = emps[0]["id"], emps[1]["id"]
    seeded_att = cnt("SELECT COUNT(*) c FROM ppl_attendance")
    seeded_pr = cnt("SELECT COUNT(*) c FROM ppl_piece_rate")
    check("demo seed produced attendance", seeded_att > 0)
    check("demo seed produced piece-rate", seeded_pr > 0)
    check("piece-rate demo is order-linked when orders exist",
          cnt("SELECT COUNT(*) c FROM ppl_piece_rate WHERE order_id IS NOT NULL") > 0)

    create_and_seed(get_db())
    check("create_and_seed is idempotent (attendance)",
          cnt("SELECT COUNT(*) c FROM ppl_attendance") == seeded_att)
    check("create_and_seed is idempotent (piece-rate)",
          cnt("SELECT COUNT(*) c FROM ppl_piece_rate") == seeded_pr)

    # --- empty database: no roster at all must not raise ------------------
    raw = sqlite3.connect(str(TMP / "empty.db"))
    raw.row_factory = sqlite3.Row
    create_and_seed(raw)
    check("create_and_seed on an empty DB creates tables and seeds nothing",
          raw.execute("SELECT COUNT(*) c FROM ppl_attendance").fetchone()["c"] == 0)
    raw.close()

    # ======================================================================
    # ATTENDANCE
    # ======================================================================
    D = ["2020-01-06", "2020-01-07", "2020-01-08"]
    svc.save_attendance_day(D[0], [
        {"employee_id": e0, "status": "present", "check_in": "08:00", "check_out": "17:00"},
        {"employee_id": e1, "status": "present", "check_in": "22:00", "check_out": "06:00"}], None)
    svc.save_attendance_day(D[1], [
        {"employee_id": e0, "status": "absent"},
        {"employee_id": e1, "status": "present", "check_in": "08:00", "check_out": "17:00",
         "ot_hours": "-5"}], None)
    n = svc.save_attendance_day(D[2], [
        {"employee_id": e0, "status": "late", "check_in": "08:35", "check_out": "17:00"},
        {"employee_id": e1, "status": "leave"}], None)
    check("bulk day entry writes one row per employee", n == 2)

    row = svc.list_attendance(work_date=D[0], employee_id=e0)[0]
    check("worked hours from the clock pair (08:00-17:00 = 9.0)", row["worked_hours"] == 9.0)
    night = svc.list_attendance(work_date=D[0], employee_id=e1)[0]
    check("night shift wraps past midnight (22:00-06:00 = 8.0)", night["worked_hours"] == 8.0)
    neg = svc.list_attendance(work_date=D[1], employee_id=e1)[0]
    check("negative overtime is clamped to 0 (never a credit)", neg["ot_hours"] == 0.0)
    noclock = svc.list_attendance(work_date=D[2], employee_id=e1)[0]
    check("a non-working day with no clock pair books 0 hours", noclock["worked_hours"] == 0.0)

    st = svc.attendance_stats(D[0], D[2])
    check("scheduled excludes holiday/off (6 working rows)", st["scheduled"] == 6)
    check("attendance % counts late as attended (4/6 = 66.7)", st["attendance_pct"] == 66.7)
    check("absenteeism % (1/6 = 16.7)", st["absenteeism_pct"] == 16.7)

    before = cnt("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=?", (D[0],))
    svc.save_attendance_day(D[0], [{"employee_id": e0, "status": "absent"}], None)
    check("re-posting a day corrects, never duplicates",
          cnt("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=?", (D[0],)) == before)
    st2 = svc.attendance_stats(D[0], D[2])
    check("stats follow the correction (3/6 = 50.0, absent 2/6 = 33.3)",
          st2["attendance_pct"] == 50.0 and st2["absenteeism_pct"] == 33.3)

    check("an unparseable date saves nothing", svc.save_attendance_day("not-a-date", [
        {"employee_id": e0, "status": "present"}], None) == 0)
    check("rows without an employee id are skipped",
          svc.save_attendance_day(D[0], [{"employee_id": "", "status": "present"}], None) == 0)
    empty_range = svc.attendance_stats("2019-01-01", "2019-01-31")
    check("empty range gives 0 %, not ZeroDivisionError",
          empty_range["attendance_pct"] == 0.0 and empty_range["absenteeism_pct"] == 0.0)
    check("reversed range is refused with zeros",
          svc.attendance_stats("2020-01-08", "2020-01-06")["scheduled"] == 0)

    # absenteeism alert: raised once per day, deduplicated on notifications.ext_key
    svc.save_attendance_day("2020-02-10", [
        {"employee_id": e0, "status": "absent"},
        {"employee_id": e1, "status": "present", "check_in": "08:00", "check_out": "17:00"}], None)
    svc.absence_sweep("2020-02-10")
    svc.absence_sweep("2020-02-10")
    check("absenteeism above threshold alerts the bell exactly once",
          cnt("SELECT COUNT(*) c FROM notifications WHERE ext_key=?", ("ppl_abs_2020-02-10",)) == 1)

    # ======================================================================
    # LEAVE
    # ======================================================================
    bal0 = svc.get_balance(e0, "annual", 2030)
    check("balance auto-creates with the default entitlement (21 annual)",
          bal0["entitled"] == 21.0 and bal0["taken"] == 0.0 and bal0["remaining"] == 21.0)

    ok, msg = svc.request_leave({"employee_id": e0, "leave_type": "annual",
                                 "from_date": "2030-03-01", "to_date": "2030-03-25"}, None)
    check("request beyond the remaining balance is refused",
          ok is False and msg == "insufficient_balance")
    ok, msg = svc.request_leave({"employee_id": e0, "leave_type": "annual",
                                 "from_date": "2030-03-10", "to_date": "2030-03-01"}, None)
    check("reversed dates are refused", ok is False and msg == "bad_dates")
    ok, msg = svc.request_leave({"employee_id": "", "leave_type": "annual",
                                 "from_date": "2030-03-01", "to_date": "2030-03-02"}, None)
    check("missing employee is refused", ok is False and msg == "employee_required")
    ok, msg = svc.request_leave({"employee_id": 999999, "leave_type": "annual",
                                 "from_date": "2030-03-01", "to_date": "2030-03-02"}, None)
    check("unknown employee is refused", ok is False and msg == "employee_not_found")

    bells = cnt("SELECT COUNT(*) c FROM notifications WHERE module='people'")
    ok, lid = svc.request_leave({"employee_id": e0, "leave_type": "annual",
                                 "from_date": "2030-03-01", "to_date": "2030-03-05",
                                 "reason": "self-test"}, {"username": "tester"})
    check("valid request is accepted (5 inclusive days)", ok is True)
    check("a pending request rings the bell",
          cnt("SELECT COUNT(*) c FROM notifications WHERE module='people'") == bells + 1)

    check("pending does not touch the balance", svc.get_balance(e0, "annual", 2030)["taken"] == 0.0)
    check("approve succeeds", svc.decide_leave(lid, "approve", {"username": "boss"})[0] is True)
    check("approving deducts exactly 5 days", svc.get_balance(e0, "annual", 2030)["taken"] == 5.0)
    ok, msg = svc.decide_leave(lid, "approve", {"username": "boss"})
    check("approving twice is refused", ok is False and msg == "already_approved")
    check("approving twice does NOT double-deduct",
          svc.get_balance(e0, "annual", 2030)["taken"] == 5.0)
    check("remaining is entitled - taken (16)", svc.get_balance(e0, "annual", 2030)["remaining"] == 16.0)

    check("cancelling an approved leave succeeds", svc.decide_leave(lid, "cancel", None)[0] is True)
    check("cancelling restores the balance", svc.get_balance(e0, "annual", 2030)["taken"] == 0.0)
    ok, msg = svc.decide_leave(lid, "cancel", None)
    check("cancelling twice is refused", ok is False and msg == "already_decided")
    check("cancelling twice does NOT mint days",
          svc.get_balance(e0, "annual", 2030)["taken"] == 0.0)

    ok, lid2 = svc.request_leave({"employee_id": e0, "leave_type": "annual",
                                  "from_date": "2030-04-01", "to_date": "2030-04-02"}, None)
    svc.decide_leave(lid2, "approve", None)
    svc.decide_leave(lid2, "reject", None)
    check("rejecting an approved leave restores the balance",
          svc.get_balance(e0, "annual", 2030)["taken"] == 0.0)

    ok, lid3 = svc.request_leave({"employee_id": e0, "leave_type": "unpaid",
                                  "from_date": "2030-05-01", "to_date": "2030-05-30"}, None)
    check("unpaid leave is not balance-checked", ok is True)
    svc.decide_leave(lid3, "approve", None)
    check("approving unpaid leave leaves the latch off (nothing to restore)",
          cnt("SELECT balance_applied c FROM ppl_leave WHERE id=?", (lid3,)) == 0)
    check("bad action is refused", svc.decide_leave(lid2, "explode", None)[1] == "bad_action")
    check("unknown leave id is refused", svc.decide_leave(999999, "approve", None)[1] == "not_found")

    # ======================================================================
    # SKILL MATRIX
    # ======================================================================
    svc.set_skill(e0, "Overlock", 9, "abc", {"username": "sup"})
    s = [x for x in svc.best_operators("Overlock", limit=50) if x["employee_id"] == e0][0]
    check("skill level is clamped to 1..5", s["level"] == 5)
    check("non-numeric efficiency becomes 0, not a crash", s["efficiency_pct"] == 0.0)
    svc.set_skill(e0, "Overlock", 4, 92.5, {"username": "sup"})
    check("re-rating upserts (one cell per employee/operation)",
          cnt("SELECT COUNT(*) c FROM ppl_skills WHERE employee_id=? AND operation='Overlock'", (e0,)) == 1)
    check("negative efficiency is clamped to 0",
          svc.set_skill(e1, "TESTOP", 1, -50, None)[0] is True and
          cnt("SELECT COUNT(*) c FROM ppl_skills WHERE operation='TESTOP' AND efficiency_pct=0") == 1)
    check("a level-1 trainee is not proposed for allocation", svc.best_operators("TESTOP") == [])
    check("min_level can be lowered explicitly", len(svc.best_operators("TESTOP", min_level=1)) == 1)
    check("blank operation is refused", svc.set_skill(e0, "  ", 3, 90, None)[0] is False)
    check("blank operation lookup returns nothing", svc.best_operators("") == [])
    ranked = svc.best_operators("Overlock", limit=50)
    check("best operators are ranked by efficiency desc",
          all(ranked[i]["efficiency_pct"] >= ranked[i + 1]["efficiency_pct"]
              for i in range(len(ranked) - 1)))
    mx = svc.skill_matrix()
    r0 = [r for r in mx["rows"] if r["id"] == e0][0]
    check("matrix reports operation coverage per employee", r0["ops_count"] >= 1)
    check("matrix columns are the operations actually rated", "Overlock" in mx["operations"])

    # ======================================================================
    # PIECE-RATE / INCENTIVE  (earned-minute model)
    # ======================================================================
    c1 = svc.compute_incentive(900, 0.55, 480, 0.30, 75)
    check("earned minutes = pieces x SMV (900 x 0.55 = 495.0)", c1["earned_minutes"] == 495.0)
    # 495/480 = 103.125 %, rounded half-to-even by Python's round() -> 103.12
    check("efficiency = earned / worked x 100 (495/480 = 103.12)", c1["efficiency_pct"] == 103.12)
    check("incentive = (495 - 360) x 0.30 = 40.5", c1["incentive"] == 40.5)

    c2 = svc.compute_incentive(600, 0.55, 480, 0.30, 75)
    check("below threshold: 330 earned vs 360 required -> efficiency 68.75",
          c2["efficiency_pct"] == 68.75)
    check("below threshold pays exactly 0, never negative", c2["incentive"] == 0.0)

    c3 = svc.compute_incentive(900, 0.55, 0, 0.30, 75)
    check("no minutes worked -> every derived figure is 0 (no division by zero)",
          c3["earned_minutes"] == 0.0 and c3["efficiency_pct"] == 0.0 and c3["incentive"] == 0.0)
    c4 = svc.compute_incentive("abc", None, "", "", "")
    check("garbage/blank inputs fall back safely to 0", c4["incentive"] == 0.0)
    check("blank rate falls back to the scheme default",
          svc.compute_incentive(900, 0.55, 480, "", "")["incentive"] == 40.5)
    c5 = svc.compute_incentive(-900, -0.55, -480, -1, -10)
    check("negative physical quantities are clamped, incentive stays 0", c5["incentive"] == 0.0)
    check("an explicit zero rate honours 'no incentive scheme'",
          svc.compute_incentive(900, 0.55, 480, 0, 75)["incentive"] == 0.0)

    check("piece-rate needs an employee",
          svc.add_piece_rate({"pieces": 10, "smv": 0.5, "minutes_worked": 60}, None)[1] == "employee_required")
    check("piece-rate refuses a zero SMV",
          svc.add_piece_rate({"employee_id": e0, "smv": 0, "minutes_worked": 60}, None)[1] == "bad_smv")
    check("piece-rate refuses zero minutes worked",
          svc.add_piece_rate({"employee_id": e0, "smv": 0.5, "minutes_worked": 0}, None)[1] == "bad_minutes")

    svc.add_piece_rate({"employee_id": e0, "work_date": "2030-05-01", "operation": "Overlock",
                        "pieces": 900, "smv": 0.55, "minutes_worked": 480,
                        "rate_per_minute": 0.30, "threshold_pct": 75}, None)
    svc.add_piece_rate({"employee_id": e0, "work_date": "2030-05-02", "operation": "Overlock",
                        "pieces": 600, "smv": 0.55, "minutes_worked": 480,
                        "rate_per_minute": 0.30, "threshold_pct": 75}, None)
    summ = svc.incentive_summary("2030-05-01", "2030-05-02")
    check("period earned minutes = 495 + 330 = 825.0", summ["earned_minutes"] == 825.0)
    check("period incentive = 40.5 + 0 = 40.5", summ["incentive"] == 40.5)
    check("period efficiency is minute-weighted (825/960 = 85.9)", summ["efficiency_pct"] == 85.9)
    check("stored derived columns match the formula",
          svc.list_piece_rate("2030-05-01", "2030-05-01")[0]["incentive"] == 40.5)
    check("an empty period summarises to zeros, not an error",
          svc.incentive_summary("2031-01-01", "2031-01-02")["efficiency_pct"] == 0.0)

    # ======================================================================
    # DASHBOARD
    # ======================================================================
    d = svc.dashboard()
    for k in ("headcount", "present_today", "absent_today", "leave_today", "pending_leave",
              "attendance_pct", "absenteeism_pct", "avg_efficiency", "incentive_total",
              "by_dept", "top_operators", "pending"):
        assert k in d, "dashboard missing key " + k
    check("dashboard returns every KPI the template reads", d["headcount"] >= 2)
    check("dashboard percentages are never negative",
          d["attendance_pct"] >= 0 and d["absenteeism_pct"] >= 0 and d["avg_efficiency"] >= 0)

    admin = dict(get_db().execute(
        "SELECT id, session_epoch FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
    conn.close()

# --- every template must parse -------------------------------------------
from jinja2 import Environment, FileSystemLoader   # noqa: E402

env = Environment(loader=FileSystemLoader(r"D:\TC platform\tc-platform-render\app\templates"))
tpl_dir = Path(r"D:\TC platform\tc-platform-render\app\templates\people")
for f in sorted(tpl_dir.glob("*.html")):
    env.parse(f.read_text(encoding="utf-8"))
    print("  ok  template parses: people/" + f.name)

# --- and actually render, against the real base.html ----------------------
from app.routes.people import bp                  # noqa: E402

if "people" not in app.blueprints:                # already registered once the app wires it up
    app.register_blueprint(bp)
client = app.test_client()
with client.session_transaction() as s:
    s["uid"] = admin["id"]
    s["ep"] = admin["session_epoch"] or 0
for path in ("/people/", "/people/attendance", "/people/leave",
             "/people/skills?op=Overlock", "/people/incentive"):
    code = client.get(path).status_code
    check("GET %s renders 200" % path, code == 200)

print("\nALL %d CHECKS PASSED + %d TEMPLATES PARSED"
      % (len(OK), len(list(tpl_dir.glob("*.html")))))
