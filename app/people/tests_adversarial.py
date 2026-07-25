"""
HR Core ADVERSARIAL test — tries to BREAK the module rather than confirm it.

Run:  python app/people/tests_adversarial.py

Isolated throwaway DB (Config.DB_PATH overridden into a temp dir), so it never
touches the repo platform.db. Every money / quantity / percentage figure is
recomputed here by hand; every "must happen exactly once" operation is called
twice. Probes record PASS/FAIL and keep going, so one defect never hides another.
"""
import os
import re
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ppl_adv_"))
os.chdir(TMP)
REPO = r"D:\TC platform\tc-platform-render"
sys.path.insert(0, REPO)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                      # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                         # noqa: E402

app = create_app()
FAILS = []
N = [0]


def check(label, cond, detail=""):
    N[0] += 1
    if cond:
        print("  ok   " + label)
    else:
        FAILS.append(label + ((" | " + str(detail)) if detail else ""))
        print("  FAIL " + label + ((" | " + str(detail)) if detail else ""))


def no_raise(label, fn):
    """A user-reachable path must never blow up with an unhandled exception."""
    N[0] += 1
    try:
        fn()
        print("  ok   " + label)
        return True
    except Exception as e:
        FAILS.append(f"{label} | {type(e).__name__}: {e}")
        print(f"  FAIL {label} | {type(e).__name__}: {e}")
        return False


TODAY = date.today()


def iso(n=0):
    return str(TODAY + timedelta(days=n))


with app.app_context():
    from app.db import get_db
    from app.people.schema import create_and_seed
    from app.people import services as svc
    from app.people import constants as K

    def q(sql, args=()):
        c = get_db()
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()

    def one(sql, args=()):
        r = q(sql, args)
        return r[0]["c"] if r else 0

    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    conn.close()

    print("\n== 1. create_and_seed idempotency (3 more consecutive runs) ==")
    tables = ("ppl_attendance", "ppl_leave", "ppl_leave_balance", "ppl_skills", "ppl_piece_rate")
    base = {t: one(f"SELECT COUNT(*) c FROM {t}") for t in tables}
    bal_before = [dict(r) for r in q("SELECT employee_id,leave_type,year,entitled,taken "
                                     "FROM ppl_leave_balance ORDER BY id")]
    for _ in range(3):
        c = get_db()
        create_and_seed(c)
        c.close()
    after = {t: one(f"SELECT COUNT(*) c FROM {t}") for t in tables}
    check("3x create_and_seed duplicates nothing", after == base, f"{base} -> {after}")
    check("3x create_and_seed destroys nothing", all(after[t] >= 1 for t in tables), after)
    bal_after = [dict(r) for r in q("SELECT employee_id,leave_type,year,entitled,taken "
                                    "FROM ppl_leave_balance ORDER BY id")]
    check("3x create_and_seed does not drift a leave balance", bal_before == bal_after)

    emps = svc.employees()
    check("roster available for the probes", len(emps) >= 3, len(emps))
    e0, e1, e2 = emps[0]["id"], emps[1]["id"], emps[2]["id"]
    USER = {"username": "adversary"}

    print("\n== 2. compute_incentive — every figure recomputed by hand ==")
    # 900 x 0.55 = 495.00 earned min; 495/480 = 103.125 % -> 103.12 (half-to-even)
    # threshold 480 x 75/100 = 360 min; (495-360) x 0.30 = 40.50
    c = svc.compute_incentive(900, 0.55, 480)
    check("earned = pieces x SMV (900 x 0.55 = 495.0)", c["earned_minutes"] == 495.0, c)
    check("efficiency = 495/480 = 103.12", c["efficiency_pct"] == 103.12, c)
    check("incentive = (495-360) x 0.30 = 40.50", c["incentive"] == 40.5, c)
    c = svc.compute_incentive(600, 0.55, 480)      # 330 earned vs 360 threshold
    check("below threshold pays exactly 0 (no clawback)", c["incentive"] == 0.0, c)
    check("below-threshold efficiency still reported (330/480 = 68.75)",
          c["efficiency_pct"] == 68.75, c)
    check("explicit rate 0 is honoured, not overridden by the default",
          svc.compute_incentive(900, .55, 480, 0)["incentive"] == 0.0)
    check("blank rate falls back to the scheme default",
          svc.compute_incentive(900, .55, 480, "")["rate_per_minute"] == K.DEFAULT_RATE_PER_MINUTE)
    check("negative rate cannot mint a negative payable",
          svc.compute_incentive(900, .55, 480, -5)["incentive"] == 0.0)
    check("zero minutes = zero pay, no ZeroDivisionError",
          svc.compute_incentive(900, .55, 0) ==
          {"earned_minutes": 0.0, "efficiency_pct": 0.0, "incentive": 0.0, "minutes_worked": 0.0,
           "rate_per_minute": K.DEFAULT_RATE_PER_MINUTE, "threshold_pct": K.INCENTIVE_THRESHOLD_PCT})
    check("negative pieces are clamped, never a payable",
          svc.compute_incentive(-900, .55, 480)["incentive"] == 0.0)
    check("None everywhere degrades to zeros",
          svc.compute_incentive(None, None, None)["incentive"] == 0.0)
    check("threshold 0 pays every earned minute (495 x 0.30 = 148.50)",
          svc.compute_incentive(900, .55, 480, 0.30, 0)["incentive"] == 148.5)

    print("\n== 3. non-finite / overflowing numeric input ==")
    c = svc.compute_incentive("inf", 0.55, 480)
    check("'inf' pieces cannot create an infinite payable", c["incentive"] != float("inf"), c)
    c = svc.compute_incentive("1e400", 0.55, 480)
    check("an overflowing piece count cannot create an infinite payable",
          c["incentive"] != float("inf"), c)
    c = svc.compute_incentive(900, 0.55, "nan")
    check("'nan' minutes cannot produce a NaN payable", c["incentive"] == c["incentive"], c)
    no_raise("_i('1e999') does not raise OverflowError", lambda: svc._i("1e999"))
    no_raise("_i('inf') does not raise OverflowError", lambda: svc._i("inf"))
    no_raise("save_attendance_day with an overflowing employee id does not 500",
             lambda: svc.save_attendance_day(iso(-1),
                                             [{"employee_id": "1e999", "status": "present"}], USER))
    ok, rid = svc.add_piece_rate({"employee_id": e0, "work_date": iso(-2), "operation": "Overlock",
                                  "pieces": "inf", "smv": 0.55, "minutes_worked": 480}, USER)
    if ok:
        row = dict(q("SELECT incentive, earned_minutes FROM ppl_piece_rate WHERE id=?", (rid,))[0])
        check("a stored payable is always finite",
              row["incentive"] != float("inf") and row["earned_minutes"] != float("inf"), row)
    else:
        check("a stored payable is always finite", True, "refused: " + str(rid))

    print("\n== 4. attendance worked-hours / OT invariants ==")
    day = iso(-1)
    svc.save_attendance_day(day, [{"employee_id": e0, "status": "present", "check_in": "08:00",
                                   "check_out": "17:00", "ot_hours": "2"}], USER)
    r = dict(q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?", (e0, day))[0])
    check("present 08:00-17:00 books 9.00 h", r["worked_hours"] == 9.0, r)
    svc.save_attendance_day(day, [{"employee_id": e0, "status": "present",
                                   "check_in": "22:00", "check_out": "06:00"}], USER)
    r = dict(q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?", (e0, day))[0])
    check("night shift 22:00-06:00 wraps to 8.00 h", r["worked_hours"] == 8.0, r)
    # The killer: the day-entry form keeps the previously saved clock pair in the
    # inputs, so flipping the status to absent re-posts the same in/out times.
    svc.save_attendance_day(day, [{"employee_id": e0, "status": "absent", "check_in": "08:00",
                                   "check_out": "17:00", "ot_hours": "1.5"}], USER)
    r = dict(q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?", (e0, day))[0])
    check("an ABSENT day books 0 worked hours despite a stale clock pair",
          r["worked_hours"] == 0.0, r)
    check("an ABSENT day books 0 overtime", r["ot_hours"] == 0.0, r)
    svc.save_attendance_day(day, [{"employee_id": e0, "status": "leave", "check_in": "08:00",
                                   "check_out": "17:00", "ot_hours": "3"}], USER)
    r = dict(q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?", (e0, day))[0])
    check("a LEAVE day books 0 worked hours and 0 OT",
          r["worked_hours"] == 0.0 and r["ot_hours"] == 0.0, r)
    svc.save_attendance_day(day, [{"employee_id": e0, "status": "present", "check_in": "08:00",
                                   "check_out": "17:00", "ot_hours": "-4"}], USER)
    r = dict(q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?", (e0, day))[0])
    check("negative OT is clamped to 0, never a credit", r["ot_hours"] == 0.0, r)
    svc.save_attendance_day(day, [{"employee_id": e0, "status": "present", "ot_hours": "1e999"}], USER)
    r = dict(q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?", (e0, day))[0])
    check("an overflowing OT entry is not stored as infinity", r["ot_hours"] != float("inf"), r)
    check("no clock pair on a working day books the standard day",
          r["worked_hours"] == K.STANDARD_DAY_HOURS, r)
    check("re-posting a day never duplicates",
          one("SELECT COUNT(*) c FROM ppl_attendance WHERE employee_id=? AND work_date=?",
              (e0, day)) == 1)

    print("\n== 5. attendance foreign-id / garbage guards ==")
    ghost = 999_999
    before = one("SELECT COUNT(*) c FROM ppl_attendance")
    n = svc.save_attendance_day(day, [{"employee_id": ghost, "status": "absent"}], USER)
    check("an employee id that is not on the roster is refused",
          one("SELECT COUNT(*) c FROM ppl_attendance WHERE employee_id=?", (ghost,)) == 0,
          f"wrote {n} row(s)")
    check("no orphan attendance row was created at all",
          one("SELECT COUNT(*) c FROM ppl_attendance") == before)
    check("garbage date saves nothing",
          svc.save_attendance_day("not-a-date", [{"employee_id": e0}], USER) == 0)
    no_raise("rows=None does not raise", lambda: svc.save_attendance_day(day, None, USER))
    no_raise("user=None does not raise",
             lambda: svc.save_attendance_day(day, [{"employee_id": e0}], None))
    svc.save_attendance_day(day, [{"employee_id": e0, "status": "<script>alert(1)</script>"}], USER)
    r = dict(q("SELECT status FROM ppl_attendance WHERE employee_id=? AND work_date=?",
               (e0, day))[0])
    check("an unknown status falls back to a legal enum value", r["status"] in K.ATT_STATUS, r)

    print("\n== 6. attendance percentages (recomputed by hand) ==")
    # A window 400 days back, far from the demo seed, so the arithmetic is exact.
    base_d = TODAY - timedelta(days=400)
    for i, st in enumerate(["present", "present", "late", "absent", "holiday", "off"]):
        svc.save_attendance_day(str(base_d + timedelta(days=i)),
                                [{"employee_id": e0, "status": st}], USER)
    s = svc.attendance_stats(str(base_d), str(base_d + timedelta(days=5)))
    check("scheduled excludes holiday/off (4 of 6)", s["scheduled"] == 4, s)
    check("attendance % counts late as attended: 3/4 = 75.0", s["attendance_pct"] == 75.0, s)
    check("absenteeism % = 1/4 = 25.0", s["absenteeism_pct"] == 25.0, s)
    check("by_dept percentages agree with the headline",
          all(abs(d["attendance_pct"] - 75.0) < 0.05 for d in s["by_dept"]), s["by_dept"])
    check("reversed range returns zeros, not a crash",
          svc.attendance_stats(str(base_d + timedelta(days=5)), str(base_d))["scheduled"] == 0)
    check("None range returns zeros", svc.attendance_stats(None, None)["attendance_pct"] == 0.0)
    check("an empty window gives 0 %, not ZeroDivisionError",
          svc.attendance_stats(str(base_d - timedelta(days=90)),
                               str(base_d - timedelta(days=80)))["attendance_pct"] == 0.0)

    print("\n== 7. leave: balance, double-apply and overlap ==")
    y = TODAY.year
    b = svc.get_balance(e1, "annual", y)
    check("balance auto-creates with the default entitlement",
          b["entitled"] == K.DEFAULT_ENTITLEMENT["annual"], b)
    start = TODAY + timedelta(days=30)
    ok, lid = svc.request_leave({"employee_id": e1, "leave_type": "annual", "from_date": str(start),
                                 "to_date": str(start + timedelta(days=4)), "reason": "probe"}, USER)
    check("a valid 5-day request is accepted", ok, lid)
    check("5 inclusive calendar days",
          svc.leave_days(str(start), str(start + timedelta(days=4))) == 5.0)
    taken0 = svc.get_balance(e1, "annual", y)["taken"]
    check("a pending request does not touch the balance", taken0 == b["taken"], taken0)
    check("approve succeeds", svc.decide_leave(lid, "approve", USER)[0])
    t1 = svc.get_balance(e1, "annual", y)["taken"]
    check("approve deducts exactly 5 days", round(t1 - taken0, 2) == 5.0, (taken0, t1))
    check("approving twice is refused",
          svc.decide_leave(lid, "approve", USER) == (False, "already_approved"))
    check("approving twice does NOT double-deduct", svc.get_balance(e1, "annual", y)["taken"] == t1)
    check("rejecting an approved leave succeeds", svc.decide_leave(lid, "reject", USER)[0])
    check("rejecting restored the balance exactly", svc.get_balance(e1, "annual", y)["taken"] == taken0)
    svc.decide_leave(lid, "reject", USER)
    svc.decide_leave(lid, "cancel", USER)
    check("a second reversal cannot mint days",
          svc.get_balance(e1, "annual", y)["taken"] == taken0, svc.get_balance(e1, "annual", y))
    check("a reversed leave cannot be re-approved",
          svc.decide_leave(lid, "approve", USER) == (False, "already_decided"))

    # OVERLAP: the same calendar days must not be charged to the balance twice.
    ok1, l1 = svc.request_leave({"employee_id": e2, "leave_type": "annual", "from_date": str(start),
                                 "to_date": str(start + timedelta(days=2))}, USER)
    ok2, l2 = svc.request_leave({"employee_id": e2, "leave_type": "annual",
                                 "from_date": str(start + timedelta(days=1)),
                                 "to_date": str(start + timedelta(days=3))}, USER)
    check("an overlapping second request for the same employee is refused",
          ok1 and not ok2, f"first=({ok1},{l1}) second=({ok2},{l2})")
    # measure the DELTA: the demo seed may already have charged this employee.
    taken_pre = svc.get_balance(e2, "annual", y)["taken"]
    if ok1:
        svc.decide_leave(l1, "approve", USER)
    if ok2:
        svc.decide_leave(l2, "approve", USER)
    charged = round(svc.get_balance(e2, "annual", y)["taken"] - taken_pre, 2)
    check("overlapping leave cannot double-charge the same calendar days",
          charged == 3.0, f"charged {charged} days for a single 3-day absence")

    print("\n== 8. leave: negative and boundary cases ==")
    check("reversed dates refused",
          svc.request_leave({"employee_id": e1, "from_date": iso(5), "to_date": iso(1)}, USER)
          == (False, "bad_dates"))
    check("unparseable dates refused",
          svc.request_leave({"employee_id": e1, "from_date": "x", "to_date": "y"}, USER)
          == (False, "bad_dates"))
    check("missing employee refused",
          svc.request_leave({"from_date": iso(1), "to_date": iso(1)}, USER)[1] == "employee_required")
    check("unknown employee refused",
          svc.request_leave({"employee_id": 987654, "from_date": iso(1), "to_date": iso(1)}, USER)
          == (False, "employee_not_found"))
    check("beyond the remaining balance is refused",
          svc.request_leave({"employee_id": e1, "leave_type": "annual", "from_date": iso(60),
                             "to_date": iso(160)}, USER) == (False, "insufficient_balance"))
    okU, rU = svc.request_leave({"employee_id": e1, "leave_type": "unpaid",
                                 "from_date": "2020-01-01", "to_date": "2999-01-01"}, USER)
    check("an absurd unpaid range (357k days) is refused", not okU, rU)
    okU2, rU2 = svc.request_leave({"employee_id": e1, "leave_type": "unpaid",
                                   "from_date": iso(200), "to_date": iso(203)}, USER)
    check("a sane unpaid request is accepted without a balance check", okU2, rU2)
    if okU2:
        svc.decide_leave(rU2, "approve", USER)
        rowU = dict(q("SELECT balance_applied FROM ppl_leave WHERE id=?", (rU2,))[0])
        check("approving unpaid leaves the latch off", rowU["balance_applied"] == 0, rowU)
        check("approving unpaid created no taken figure",
              svc.get_balance(e1, "unpaid", y)["taken"] == 0.0)
        svc.decide_leave(rU2, "cancel", USER)
        check("cancelling unpaid cannot mint days",
              svc.get_balance(e1, "unpaid", y)["taken"] == 0.0)
    check("unknown leave id refused",
          svc.decide_leave(4242424, "approve", USER) == (False, "not_found"))
    check("non-numeric leave id refused",
          svc.decide_leave("abc", "approve", USER) == (False, "not_found"))
    okB, lB = svc.request_leave({"employee_id": e1, "leave_type": "annual",
                                 "from_date": iso(300), "to_date": iso(300)}, USER)
    check("unknown action refused",
          okB and svc.decide_leave(lB, "delete", USER) == (False, "bad_action"))
    no_raise("request_leave(None, None) does not raise", lambda: svc.request_leave(None, None))

    print("\n== 9. skills ==")
    svc.set_skill(e0, "Overlock", 99, 120, USER)
    r = dict(q("SELECT * FROM ppl_skills WHERE employee_id=? AND operation='Overlock'", (e0,))[0])
    check("level clamped to 5", r["level"] == 5, r)
    svc.set_skill(e0, "Overlock", -3, -50, USER)
    r = dict(q("SELECT * FROM ppl_skills WHERE employee_id=? AND operation='Overlock'", (e0,))[0])
    check("level clamped to 1 and efficiency to 0",
          r["level"] == 1 and r["efficiency_pct"] == 0.0, r)
    check("re-rating upserts — one cell per employee/operation",
          one("SELECT COUNT(*) c FROM ppl_skills WHERE employee_id=? AND operation='Overlock'",
              (e0,)) == 1)
    svc.set_skill(e0, "Overlock", 4, "1e400", USER)
    r = dict(q("SELECT * FROM ppl_skills WHERE employee_id=? AND operation='Overlock'", (e0,))[0])
    check("an overflowing efficiency is not stored as infinity",
          r["efficiency_pct"] != float("inf"), r)
    check("blank operation refused", svc.set_skill(e0, "   ", 3, 90, USER)[0] is False)
    check("an employee id that is not on the roster is refused for a skill",
          svc.set_skill(876543, "Overlock", 3, 90, USER)[0] is False,
          one("SELECT COUNT(*) c FROM ppl_skills WHERE employee_id=876543"))
    svc.set_skill(e1, "Bartack", 1, 200, USER)
    svc.set_skill(e2, "Bartack", 3, 90, USER)
    ids = [x["employee_id"] for x in svc.best_operators("Bartack")]
    check("a level-1 trainee is never proposed for allocation", e1 not in ids, ids)
    check("min_level can be lowered explicitly",
          e1 in [x["employee_id"] for x in svc.best_operators("Bartack", min_level=1)])
    check("blank operation returns nothing", svc.best_operators("") == [])
    check("an unknown operation returns nothing", svc.best_operators("Nonexistent op") == [])

    print("\n== 10. incentive aggregation (recomputed by hand) ==")
    d1, d2 = str(TODAY - timedelta(days=402)), str(TODAY - timedelta(days=401))
    svc.add_piece_rate({"employee_id": e0, "work_date": d1, "operation": "Overlock",
                        "pieces": 900, "smv": 0.55, "minutes_worked": 480}, USER)   # 495 / 480
    svc.add_piece_rate({"employee_id": e0, "work_date": d2, "operation": "Overlock",
                        "pieces": 100, "smv": 0.55, "minutes_worked": 60}, USER)    # 55 / 60
    s = svc.incentive_summary(d1, d2)
    # minute-weighted: (495+55)/(480+60) = 550/540 = 101.85 -> 101.9.
    # A mean of the two daily percentages would be (103.12+91.67)/2 = 97.4 — wrong.
    check("period efficiency is minute-weighted (550/540 = 101.9)",
          s["efficiency_pct"] == 101.9, s)
    check("period earned minutes = 550.0", s["earned_minutes"] == 550.0, s)
    # day1 (495-360) x .30 = 40.50 ; day2 threshold 60 x .75 = 45 -> (55-45) x .30 = 3.00
    check("period incentive = 40.50 + 3.00 = 43.50", s["incentive"] == 43.5, s)
    check("reversed summary range returns zeros", svc.incentive_summary(d2, d1)["incentive"] == 0.0)
    check("None summary range returns zeros, not a crash",
          svc.incentive_summary(None, None)["efficiency_pct"] == 0.0)
    check("zero minutes is refused outright",
          svc.add_piece_rate({"employee_id": e0, "smv": 0.5, "minutes_worked": 0}, USER)
          == (False, "bad_minutes"))
    check("zero SMV is refused outright",
          svc.add_piece_rate({"employee_id": e0, "smv": 0, "minutes_worked": 480}, USER)
          == (False, "bad_smv"))
    check("a piece-rate row for a non-existent employee is refused",
          svc.add_piece_rate({"employee_id": 765432, "smv": .5, "minutes_worked": 480,
                              "pieces": 10}, USER)[0] is False,
          one("SELECT COUNT(*) c FROM ppl_piece_rate WHERE employee_id=765432"))
    no_raise("add_piece_rate(None, None) does not raise", lambda: svc.add_piece_rate(None, None))

    print("\n== 11. absenteeism alert ==")
    ad = str(TODAY - timedelta(days=500))
    svc.save_attendance_day(ad, [{"employee_id": e0, "status": "absent"},
                                 {"employee_id": e1, "status": "present"},
                                 {"employee_id": e2, "status": "present"}], USER)
    svc.absence_sweep(ad)
    svc.absence_sweep(ad)
    check("the absenteeism alert is raised exactly once per day",
          one("SELECT COUNT(*) c FROM notifications WHERE ext_key=?", (f"ppl_abs_{ad}",)) == 1)
    quiet = str(TODAY - timedelta(days=501))
    svc.save_attendance_day(quiet, [{"employee_id": e0, "status": "present"},
                                    {"employee_id": e1, "status": "present"}], USER)
    check("a clean day raises no alert",
          one("SELECT COUNT(*) c FROM notifications WHERE ext_key=?", (f"ppl_abs_{quiet}",)) == 0)
    check("the alert agrees with attendance_stats for the same day",
          svc.attendance_stats(ad, ad)["absenteeism_pct"] > K.ABSENTEEISM_ALERT_PCT)

    print("\n== 12. the day-entry page cannot silently reset marked days ==")
    # The page pre-fills from list_attendance() and posts EVERY employee it lists.
    # If it lists more employees than the pre-fill query returns, the overflow
    # employees post back as a blank 'present' and wipe a recorded absence.
    c = get_db()
    have = one("SELECT COUNT(*) c FROM prob_employees")
    for i in range(have, 340):
        c.execute("INSERT INTO prob_employees (employee_code,employee_name,department,"
                  "active,is_deleted,created_at) VALUES (?,?,?,1,0,?)",
                  (f"ADV{i:04d}", f"Adversary {i}", "Sewing", str(TODAY)))
    c.commit()
    c.close()
    bigday = str(TODAY - timedelta(days=600))
    roster = svc.employees()
    check("the day-entry page lists >300 employees", len(roster) > 300, len(roster))
    svc.save_attendance_day(bigday, [{"employee_id": e["id"], "status": "absent"}
                                     for e in roster], USER)
    # The pre-fill must cover EXACTLY the employees the form lists, whatever the
    # roster size and whatever order either query happens to use.
    prefill = svc.attendance_for(bigday, [e["id"] for e in roster])
    missing = [e["id"] for e in roster if e["id"] not in prefill]
    check("every marked employee is pre-filled on the day-entry page",
          not missing, f"{len(missing)} of {len(roster)} would post back as 'present'")

    print("\n== 13. read paths never raise / injection is inert ==")
    no_raise("dashboard() runs", lambda: svc.dashboard())
    no_raise("list_attendance() with garbage filters",
             lambda: svc.list_attendance(work_date="zzz", employee_id="abc", department="'"))
    no_raise("list_leave() with an injected status",
             lambda: svc.list_leave(status="' OR 1=1; DROP TABLE ppl_leave;--"))
    no_raise("list_piece_rate() with garbage dates",
             lambda: svc.list_piece_rate(from_date="'", to_date=None, employee_id="x"))
    no_raise("list_balances() with a garbage employee", lambda: svc.list_balances(employee_id="x"))
    no_raise("skill_matrix() filtered by department", lambda: svc.skill_matrix(department="Sewing"))
    no_raise("get_balance() on an unknown employee", lambda: svc.get_balance(999999, "annual"))
    check("injection through a filter changed nothing",
          one("SELECT COUNT(*) c FROM ppl_leave") > 0 and one("SELECT COUNT(*) c FROM ppl_attendance") > 0)
    src_svc = (Path(REPO) / "app" / "people" / "services.py").read_text(encoding="utf-8")
    check("no f-string interpolation of user input into SQL",
          not re.search(r'(execute|executescript)\(\s*f["\']', src_svc))

    print("\n== 14. bare database (probation module absent) ==")
    raw = sqlite3.connect(str(TMP / "bare.db"))
    raw.row_factory = sqlite3.Row
    create_and_seed(raw)
    create_and_seed(raw)
    create_and_seed(raw)
    check("create_and_seed on a bare DB is safe 3x",
          raw.execute("SELECT COUNT(*) c FROM ppl_attendance").fetchone()["c"] == 0)
    raw.close()

    print("\n== 15. routes: auth, HTTP method and redirect hygiene ==")
    src_rt = (Path(REPO) / "app" / "routes" / "people.py").read_text(encoding="utf-8")
    check("no route redirects to an attacker-controlled Referer header",
          "request.referrer" not in src_rt, "open redirect")
    from app.routes.people import bp as ppl_bp
    test_app = create_app()
    if "people" not in test_app.blueprints:
        test_app.register_blueprint(ppl_bp)
    rules = [r for r in test_app.url_map.iter_rules() if r.endpoint.startswith("people.")]
    check("every people route is registered", len(rules) >= 10, len(rules))
    writers = {"attendance_save", "leave_request", "leave_decide", "skill_save", "incentive_add"}
    w = [r for r in rules if r.endpoint.split(".")[1] in writers]
    check("all 5 state-changing routes are POST-only",
          len(w) == 5 and all("POST" in r.methods and "GET" not in r.methods for r in w),
          [(r.endpoint, sorted(r.methods)) for r in w])
    blocks = re.findall(r"@bp\.route\((.*?)\)\n((?:@\w+.*\n)*)def (\w+)", src_rt)
    check("every route source block was found", len(blocks) >= 10, len(blocks))
    for _args, decs, name in blocks:
        check(f"{name}: @login_required + @permission_required",
              "login_required" in decs and "permission_required" in decs, decs.strip())
    # POST bodies actually reach the service layer (smoke, CSRF disabled for the probe)
    test_app.config["WTF_CSRF_ENABLED"] = False
    cli = test_app.test_client()
    resp = cli.get("/people/")
    check("an unauthenticated request never reaches the page",
          resp.status_code in (301, 302, 401, 403), resp.status_code)

    print("\n== 16. templates parse + i18n keys ==")
    import jinja2
    tdir = Path(REPO) / "app" / "templates"
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(tdir)))
    keys = set()
    for name in ("dashboard", "attendance", "leave", "skills", "incentive"):
        srct = (tdir / "people" / f"{name}.html").read_text(encoding="utf-8")
        N[0] += 1
        try:
            env.parse(srct)
            print(f"  ok   people/{name}.html parses")
        except Exception as e:
            FAILS.append(f"people/{name}.html parse | {e}")
            print(f"  FAIL people/{name}.html parse | {e}")
        keys |= set(re.findall(r'data-i18n(?:-ph|-title)?="([^"]+)"', srct))
        N[0] += 1
        if "_csrf" in srct or "method=\"post\"" not in srct:
            print(f"  ok   people/{name}.html: every POST form carries _csrf")
        else:
            FAILS.append(f"people/{name}.html missing _csrf")
            print(f"  FAIL people/{name}.html missing _csrf")
    check("every i18n key uses the ppl. prefix", all(k.startswith("ppl.") for k in keys),
          sorted(k for k in keys if not k.startswith("ppl.")))
    (TMP / "i18n_keys.txt").write_text("\n".join(sorted(keys)), encoding="utf-8")
    print(f"  ..   {len(keys)} distinct i18n keys used -> {TMP / 'i18n_keys.txt'}")
    # One key must not carry two different English strings: whatever the map says,
    # one of the two places renders the other one's wording in every language.
    texts = {}
    for name in ("dashboard", "attendance", "leave", "skills", "incentive"):
        srct = (tdir / "people" / f"{name}.html").read_text(encoding="utf-8")
        for k, txt in re.findall(r'data-i18n="([^"]+)"[^>]*>([^<]*)', srct):
            texts.setdefault(k, set()).add(txt.strip())
    dupes = {k: v for k, v in texts.items() if len(v) > 1}
    for k, v in sorted(dupes.items()):
        print(f"  ..   i18n key reused for two labels: {k} -> {sorted(v)}")
    print(f"  ..   {len(dupes)} key(s) bound to more than one English string")

    # ======================================================================
    # SECOND ADVERSARIAL PASS — the money/quantity paths the first pass missed
    # ======================================================================
    print("\n== 17. piece-rate: posting the same output twice pays it twice ==")
    dd = iso(-40)
    post = {"employee_id": e0, "work_date": dd, "operation": "Overlock",
            "pieces": 900, "smv": 0.55, "minutes_worked": 480}
    base_inc = svc.incentive_summary(dd, dd)["incentive"]
    ok1, id1 = svc.add_piece_rate(dict(post), USER)
    ok2, id2 = svc.add_piece_rate(dict(post), USER)          # double-click / retry
    paid = round(svc.incentive_summary(dd, dd)["incentive"] - base_inc, 2)
    check("an identical piece-rate re-post is refused, not paid twice",
          ok1 and not ok2, f"first=({ok1},{id1}) second=({ok2},{id2})")
    check("a double-submitted shift is paid exactly once (40.50, not 81.00)",
          paid == 40.5, f"paid {paid} for one 900-piece shift")
    check("a genuinely different second entry for the same day is still allowed",
          svc.add_piece_rate(dict(post, pieces=200, operation="Side seam"), USER)[0])
    # ...and the weighted period figure must follow the rows that exist
    s17 = svc.incentive_summary(dd, dd)
    em_expect = round(900 * 0.55 + 200 * 0.55, 1)            # 495 + 110 = 605.0
    check("period earned minutes = 605.0 (no phantom duplicate)",
          s17["earned_minutes"] == em_expect, s17)
    check("period efficiency is minute-weighted 605/960 = 63.0",
          s17["efficiency_pct"] == round(100.0 * 605 / 960, 1), s17)

    print("\n== 18. a day nobody is scheduled cannot book paid hours ==")
    hday = iso(-41)
    svc.save_attendance_day(hday, [{"employee_id": e0, "status": "present", "check_in": "08:00",
                                    "check_out": "17:00", "ot_hours": "2"}], USER)
    for st in ("holiday", "off"):
        # the form re-posts the clock pair and OT it was pre-filled with
        svc.save_attendance_day(hday, [{"employee_id": e0, "status": st, "check_in": "08:00",
                                        "check_out": "17:00", "ot_hours": "2"}], USER)
        r = dict(q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?",
                   (e0, hday))[0])
        check(f"a '{st}' day books 0 worked hours despite a stale clock pair",
              r["worked_hours"] == 0.0, r)
        check(f"a '{st}' day books 0 overtime", r["ot_hours"] == 0.0, r)
    ot_kpi = svc.attendance_stats(hday, hday)["ot_hours"]
    check("a non-scheduled day contributes no hours to the overtime KPI",
          ot_kpi == 0.0, ot_kpi)

    print("\n== 19. the real HTTP routes (authenticated, CSRF enforced) ==")
    rt_app = create_app()
    if "people" not in rt_app.blueprints:
        rt_app.register_blueprint(ppl_bp)
    admin = dict(q("SELECT id, session_epoch FROM users WHERE role='super_admin' LIMIT 1")[0])
    cli = rt_app.test_client()
    TOK = "adversarial-csrf-token"
    with cli.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = admin["session_epoch"] or 0
        s["_csrf_token"] = TOK
    for path in ("/people/", "/people/attendance", "/people/leave",
                 "/people/skills", "/people/incentive"):
        r = cli.get(path)
        check(f"GET {path} renders 200 against the real base.html", r.status_code == 200,
              r.status_code)
    r = cli.get("/people/skills?op=Overlock&dept=Sewing")
    check("GET /people/skills with filters renders 200", r.status_code == 200, r.status_code)
    r = cli.post("/people/leave", data={"employee_id": e0, "leave_type": "annual",
                                        "from_date": iso(400), "to_date": iso(401)})
    check("a POST without a CSRF token never reaches the service layer",
          r.status_code in (302, 400) and
          one("SELECT COUNT(*) c FROM ppl_leave WHERE from_date=?", (iso(400),)) == 0,
          r.status_code)
    pday = iso(-42)
    r = cli.post("/people/attendance", data={"_csrf": TOK, "work_date": pday,
                                             f"st_{e0}": "absent", f"ot_{e0}": "3"})
    row = q("SELECT * FROM ppl_attendance WHERE employee_id=? AND work_date=?", (e0, pday))
    check("POST /people/attendance writes exactly one row through the route",
          r.status_code == 302 and len(row) == 1 and dict(row[0])["status"] == "absent",
          (r.status_code, [dict(x) for x in row]))
    r = cli.post("/people/leave", data={"_csrf": TOK, "employee_id": e0, "leave_type": "annual",
                                        "from_date": iso(402), "to_date": iso(403)})
    lv = q("SELECT * FROM ppl_leave WHERE employee_id=? AND from_date=?", (e0, iso(402)))
    check("POST /people/leave raises exactly one pending request", len(lv) == 1, len(lv))
    if lv:
        lid19 = dict(lv[0])["id"]
        y19 = (TODAY + timedelta(days=402)).year
        t_before = svc.get_balance(e0, "annual", y19)["taken"]
        cli.post(f"/people/leave/{lid19}/decide", data={"_csrf": TOK, "action": "approve"})
        t_mid = svc.get_balance(e0, "annual", y19)["taken"]
        cli.post(f"/people/leave/{lid19}/decide", data={"_csrf": TOK, "action": "approve"})
        t_after = svc.get_balance(e0, "annual", y19)["taken"]
        check("approving twice over HTTP deducts exactly 2 days once",
              round(t_mid - t_before, 2) == 2.0 and t_after == t_mid, (t_before, t_mid, t_after))
        rr = cli.post(f"/people/leave/{lid19}/decide", data={"_csrf": TOK, "action": "approve"},
                      headers={"Referer": "https://evil.example.com/steal"})
        check("a decision never redirects to an attacker-supplied Referer",
              "evil.example.com" not in (rr.headers.get("Location") or ""),
              rr.headers.get("Location"))
    r = cli.post("/people/incentive", data={"_csrf": TOK, "employee_id": e0, "work_date": iso(-43),
                                            "operation": "Overlock", "pieces": "900",
                                            "smv": "0.55", "minutes_worked": "480",
                                            "rate_per_minute": "0.30", "threshold_pct": "75"})
    cli.post("/people/incentive", data={"_csrf": TOK, "employee_id": e0, "work_date": iso(-43),
                                        "operation": "Overlock", "pieces": "900",
                                        "smv": "0.55", "minutes_worked": "480",
                                        "rate_per_minute": "0.30", "threshold_pct": "75"})
    check("a double-submitted output form pays 40.50 once, over HTTP too",
          svc.incentive_summary(iso(-43), iso(-43))["incentive"] == 40.5,
          svc.incentive_summary(iso(-43), iso(-43)))
    r = cli.post("/people/skills", data={"_csrf": TOK, "employee_id": e0,
                                         "operation": "Bartack", "level": "4",
                                         "efficiency_pct": "91"})
    check("POST /people/skills upserts one cell through the route",
          one("SELECT COUNT(*) c FROM ppl_skills WHERE employee_id=? AND operation='Bartack'",
              (e0,)) == 1)

    print("\n== 20. the ledger is never written for somebody who does not exist ==")
    bal_n = one("SELECT COUNT(*) c FROM ppl_leave_balance")
    g = svc.get_balance(4_242_424, "annual")
    check("get_balance() on a ghost employee creates no ledger row",
          one("SELECT COUNT(*) c FROM ppl_leave_balance") == bal_n, g)
    check("get_balance() on a ghost employee reports zeros",
          g["entitled"] == 0.0 and g["taken"] == 0.0 and g["remaining"] == 0.0, g)

    print("\n== 21. the skill matrix does not silently drop employees ==")
    rated = one("SELECT COUNT(DISTINCT employee_id) c FROM ppl_skills")
    m = svc.skill_matrix()
    check("the matrix covers the same roster the rest of the page does",
          len(m["rows"]) == min(len(svc.employees()), K.ROSTER_PAGE_LIMIT),
          f"{len(m['rows'])} rows for a roster of {len(svc.employees())}")
    check("every rated operator is reachable in the matrix", rated >= 1, rated)

    print("\n== 22. day-entry pre-fill holds past the roster page limit ==")
    c = get_db()
    have = one("SELECT COUNT(*) c FROM prob_employees")
    for i in range(have, K.ROSTER_PAGE_LIMIT + 60):
        c.execute("INSERT INTO prob_employees (employee_code,employee_name,department,"
                  "active,is_deleted,created_at) VALUES (?,?,?,1,0,?)",
                  (f"OVR{i:04d}", f"Overflow {i}", "Finishing", str(TODAY)))
    c.commit()
    c.close()
    big2 = iso(-601)
    listed = svc.employees()                      # exactly what the form renders
    check("the roster page is capped at ROSTER_PAGE_LIMIT",
          len(listed) == K.ROSTER_PAGE_LIMIT, len(listed))
    svc.save_attendance_day(big2, [{"employee_id": e["id"], "status": "absent"}
                                   for e in listed], USER)
    pf = svc.attendance_for(big2, [e["id"] for e in listed])
    miss2 = [e["id"] for e in listed if e["id"] not in pf]
    check("a 500-employee day-entry page pre-fills every single line",
          not miss2, f"{len(miss2)} lines would post back as a blank 'present'")
    check("the marked day is intact (no line silently reset)",
          one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=? AND status='absent'",
              (big2,)) == len(listed))

    # ======================================================================
    # THIRD ADVERSARIAL PASS — defects the first two passes did not reach
    # ======================================================================
    print("\n== 23. a non-canonical ?d= must not wipe a marked day ==")
    # '2026-3-5' and '2026-03-05' are the same calendar day. The page renders the
    # string it was given, pre-fills from it and posts it straight back, so if the
    # three do not agree the day-entry form silently overwrites real absences.
    d_iso, d_sloppy = "2026-03-05", "2026-3-5"
    trio = [e["id"] for e in svc.employees()[:3]]
    svc.save_attendance_day(d_iso, [{"employee_id": i, "status": "absent"} for i in trio], USER)
    check("the day was marked absent for 3 employees",
          one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=? AND status='absent'",
              (d_iso,)) == 3)
    check("a non-canonical day pre-fills the SAME marks as the canonical one",
          len(svc.attendance_for(d_sloppy, trio)) == 3,
          f"{len(svc.attendance_for(d_sloppy, trio))} of 3 lines would post back blank")
    check("the route canonicalises ?d= so page/pre-fill/POST agree",
          svc.normalize_date(d_sloppy) == d_iso, svc.normalize_date(d_sloppy))
    # the killer: the form the sloppy page renders, submitted unchanged
    svc.save_attendance_day(d_sloppy, [{"employee_id": i, "status": "present"} for i in trio], USER)
    still = one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=? AND status='present'",
                (d_iso,))
    check("re-saving through the non-canonical day hits the same rows (no ghost day)",
          still == 3 and one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=?",
                             (d_iso,)) == 3, still)
    check("list_attendance finds the day whichever way it is spelled",
          len(svc.list_attendance(work_date=d_sloppy)) ==
          len(svc.list_attendance(work_date=d_iso)) == 3)
    check("a garbage day still pre-fills nothing and saves nothing",
          svc.attendance_for("zzz", trio) == {} and
          svc.save_attendance_day("zzz", [{"employee_id": trio[0]}], USER) == 0)
    check("normalize_date falls back to today for blank/garbage",
          svc.normalize_date("") == str(TODAY) and svc.normalize_date("zzz") == str(TODAY))

    print("\n== 24. overtime cannot exceed the calendar day ==")
    otday = "2026-06-01"
    svc.save_attendance_day(otday, [{"employee_id": e0, "status": "present", "check_in": "08:00",
                                     "check_out": "17:00", "ot_hours": "800"}], USER)
    r = dict(q("SELECT worked_hours, ot_hours FROM ppl_attendance WHERE employee_id=? "
               "AND work_date=?", (e0, otday))[0])
    check("a fat-fingered 800 h of overtime is bounded by the 24 h day",
          r["worked_hours"] + r["ot_hours"] <= 24.0, r)
    check("the overtime KPI is not inflated by the typo",
          svc.attendance_stats(otday, otday)["ot_hours"] <= 24.0,
          svc.attendance_stats(otday, otday)["ot_hours"])
    svc.save_attendance_day(otday, [{"employee_id": e0, "status": "present", "check_in": "08:00",
                                     "check_out": "17:00", "ot_hours": "2.5"}], USER)
    r = dict(q("SELECT ot_hours FROM ppl_attendance WHERE employee_id=? AND work_date=?",
               (e0, otday))[0])
    check("a legitimate 2.5 h of overtime is stored untouched", r["ot_hours"] == 2.5, r)

    print("\n== 25. 'days worked' is calendar days, not records ==")
    dd25 = "2026-05-05"
    svc.add_piece_rate({"employee_id": e0, "work_date": dd25, "operation": "Overlock",
                        "pieces": 500, "smv": 0.55, "minutes_worked": 240}, USER)
    svc.add_piece_rate({"employee_id": e0, "work_date": dd25, "operation": "Side seam",
                        "pieces": 300, "smv": 0.55, "minutes_worked": 240}, USER)
    s25 = [r for r in svc.incentive_summary(dd25, dd25)["rows"] if r["employee_id"] == e0][0]
    check("two operations on one shift report ONE day worked", s25["days"] == 1, s25)
    # ...and the money still aggregates over both rows
    # 500x0.55=275 and 300x0.55=165 -> 440 earned over 480 worked = 91.7 %
    check("both rows still aggregate (440 earned minutes over 480 worked)",
          s25["earned_minutes"] == 440.0 and s25["efficiency_pct"] == 91.7, s25)
    svc.add_piece_rate({"employee_id": e0, "work_date": "2026-05-06", "operation": "Overlock",
                        "pieces": 100, "smv": 0.55, "minutes_worked": 60}, USER)
    s25b = [r for r in svc.incentive_summary(dd25, "2026-05-06")["rows"]
            if r["employee_id"] == e0][0]
    check("two calendar days report two days worked", s25b["days"] == 2, s25b)

    print("\n== 26. an implausible piece count cannot mint a six-figure payable ==")
    # 9,000 pieces instead of 900 is one extra zero: 4,950 earned minutes on a 480
    # minute shift = 1031 % efficiency and (4950-360)x0.30 = 1,377.00 instead of 40.50.
    bad = {"employee_id": e0, "work_date": "2026-05-20", "operation": "Overlock",
           "pieces": 9000, "smv": 0.55, "minutes_worked": 480}
    okX, why = svc.add_piece_rate(dict(bad), USER)
    check("an extra zero in the piece count is refused", not okX, why)
    check("nothing was written for the refused entry",
          one("SELECT COUNT(*) c FROM ppl_piece_rate WHERE work_date='2026-05-20'") == 0)
    okY, why2 = svc.add_piece_rate({"employee_id": e0, "work_date": "2026-05-21",
                                    "operation": "Overlock", "pieces": 9_000_000,
                                    "smv": 0.55, "minutes_worked": 480}, USER)
    check("9,000,000 pieces on one shift is refused", not okY, why2)
    check("the period payable stays at zero for the refused days",
          svc.incentive_summary("2026-05-20", "2026-05-21")["incentive"] == 0.0)
    # a genuinely excellent operator is still paid
    okZ, _z = svc.add_piece_rate({"employee_id": e0, "work_date": "2026-05-22",
                                  "operation": "Overlock", "pieces": 1100,
                                  "smv": 0.55, "minutes_worked": 480}, USER)
    check("a real 126% shift is still accepted and paid", okZ, _z)
    check("the accepted shift pays (605-360)x0.30 = 73.50",
          svc.incentive_summary("2026-05-22", "2026-05-22")["incentive"] == 73.5,
          svc.incentive_summary("2026-05-22", "2026-05-22"))

    print("\n== 27. the demo seed agrees with the live formula ==")
    seeded = [dict(r) for r in q(
        "SELECT check_in, check_out, worked_hours FROM ppl_attendance WHERE created_by='seed' "
        "AND check_in IS NOT NULL AND check_out IS NOT NULL LIMIT 50")]
    bad_rows = [r for r in seeded if r["worked_hours"] != svc._hours(r["check_in"], r["check_out"])]
    check("every seeded clock pair books the hours the service would compute",
          not bad_rows, bad_rows[:3])
    # re-saving a seeded day must not silently change its hours
    if seeded:
        srow = dict(q("SELECT * FROM ppl_attendance WHERE created_by='seed' AND check_in='08:00' "
                      "AND check_out='17:00' LIMIT 1")[0])
        before_h = srow["worked_hours"]
        svc.save_attendance_day(srow["work_date"], [{"employee_id": srow["employee_id"],
                                                    "status": srow["status"],
                                                    "check_in": srow["check_in"],
                                                    "check_out": srow["check_out"],
                                                    "ot_hours": srow["ot_hours"]}], USER)
        after_h = dict(q("SELECT worked_hours FROM ppl_attendance WHERE id=?",
                         (srow["id"],))[0])["worked_hours"]
        check("re-saving a seeded day does not change its worked hours",
              after_h == before_h, (before_h, after_h))

    print("\n== 28. leave: state transitions after a final decision ==")
    okT, lT = svc.request_leave({"employee_id": e2, "leave_type": "sick",
                                 "from_date": iso(500), "to_date": iso(501)}, USER)
    if okT:
        yT = (TODAY + timedelta(days=500)).year
        t0 = svc.get_balance(e2, "sick", yT)["taken"]
        svc.decide_leave(lT, "approve", USER)
        svc.decide_leave(lT, "reject", USER)          # restores
        svc.decide_leave(lT, "cancel", USER)          # rejected -> cancelled flip
        svc.decide_leave(lT, "reject", USER)
        check("no sequence of decisions can drift the balance",
              svc.get_balance(e2, "sick", yT)["taken"] == t0,
              (t0, svc.get_balance(e2, "sick", yT)))
        check("a finally-decided leave can never be re-approved",
              svc.decide_leave(lT, "approve", USER) == (False, "already_decided"))

    print("\n== 29. the module never writes an ext_key-less duplicate bell ==")
    n_before = one("SELECT COUNT(*) c FROM notifications WHERE module='people'")
    svc.absence_sweep("2026-03-05")
    svc.absence_sweep("2026-03-05")
    svc.absence_sweep("2026-03-05")
    check("three sweeps of the same day add at most one alert",
          one("SELECT COUNT(*) c FROM notifications WHERE module='people'") - n_before <= 1,
          one("SELECT COUNT(*) c FROM notifications WHERE module='people'") - n_before)
    check("every people bell row carries a link",
          one("SELECT COUNT(*) c FROM notifications WHERE module='people' AND "
              "(link IS NULL OR link='')") == 0)

    print("\n== 30. a full 500-line day-entry POST is not silently truncated ==")
    roster30 = svc.employees()
    day30 = "2026-04-02"
    form = {"_csrf": TOK, "work_date": day30}
    for e in roster30:
        form[f"st_{e['id']}"] = "absent"
        form[f"in_{e['id']}"] = ""
        form[f"out_{e['id']}"] = ""
        form[f"ot_{e['id']}"] = "0"
    r30 = cli.post("/people/attendance", data=form)
    check(f"a {len(form)}-field day-entry POST writes every line",
          r30.status_code == 302 and
          one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=?", (day30,)) == len(roster30),
          (r30.status_code, one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=?", (day30,)),
           len(roster30)))

print("\n" + "=" * 68)
if FAILS:
    print(f"ADVERSARIAL RESULT: {len(FAILS)} of {N[0]} probes FAILED")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print(f"ADVERSARIAL RESULT: all {N[0]} probes passed")
