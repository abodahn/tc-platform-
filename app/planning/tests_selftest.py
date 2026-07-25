"""
Self-test for the planning module. Runs against a THROWAWAY database (never the
repo's platform.db) and asserts the real invariants with exact numbers:
capacity, required minutes/days, the day spread, feasibility, what-if and the
line-balancing formulas — plus the negative cases (zero, blank, garbage, missing
ids, divide-by-zero, double-apply).

    python app/planning/tests_selftest.py
"""
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="pln_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                       # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                          # noqa: E402

app = create_app()
FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {label} = {got!r}")


def check_true(label, cond):
    check(label, bool(cond), True)


with app.app_context():
    from app.db import get_db
    conn = get_db()
    from app.planning.schema import create_and_seed
    create_and_seed(conn)
    conn.commit()

    from app.planning import services as svc

    today = date.today()

    print("\n[1] daily_capacity_minutes = operators x working_minutes x efficiency/100")
    # 28 operators x 540 min x 65% = 9828.0 exactly
    check("capacity 28x540x65%", svc.daily_capacity_minutes(
        {"operators": 28, "working_minutes": 540, "efficiency_pct": 65}), 9828.0)
    check("capacity zero operators", svc.daily_capacity_minutes(
        {"operators": 0, "working_minutes": 540, "efficiency_pct": 65}), 0.0)
    check("capacity zero minutes", svc.daily_capacity_minutes(
        {"operators": 28, "working_minutes": 0, "efficiency_pct": 65}), 0.0)
    check("capacity zero efficiency", svc.daily_capacity_minutes(
        {"operators": 28, "working_minutes": 540, "efficiency_pct": 0}), 0.0)
    check("capacity blank strings", svc.daily_capacity_minutes(
        {"operators": "", "working_minutes": None, "efficiency_pct": "abc"}), 0.0)
    check("capacity None line", svc.daily_capacity_minutes(None), 0.0)

    print("\n[2] required_minutes = qty x SMV ; required_days = ceil(minutes/capacity)")
    check("12000 x 12.5", svc.required_minutes(12000, 12.5), 150000.0)
    check("required_minutes zero qty", svc.required_minutes(0, 12.5), 0.0)
    check("required_minutes garbage smv", svc.required_minutes(100, "n/a"), 0.0)
    check("days 150000/9828", svc.required_days(150000, 9828), 16)     # 15.26 -> 16
    check("days exact multiple", svc.required_days(19656, 9828), 2)
    check("days zero capacity -> None", svc.required_days(150000, 0), None)
    check("days zero minutes -> None", svc.required_days(0, 9828), None)

    print("\n[3] spread_minutes: per-day consumption sums back to the required minutes")
    sp = svc.spread_minutes(today, 150000, 9828)
    check("spread day count", len(sp), 16)
    check("spread total == minutes", round(sum(m for _, m in sp), 2), 150000.0)
    check("spread first day = full capacity", sp[0][1], 9828.0)
    check("spread last day = remainder", sp[-1][1], round(150000 - 15 * 9828, 2))
    check("spread first date", sp[0][0], today)
    check("spread last date", sp[-1][0], today + timedelta(days=15))
    check("spread zero capacity", svc.spread_minutes(today, 100, 0), [])
    check("spread zero minutes", svc.spread_minutes(today, 0, 100), [])
    check("spread bad date", svc.spread_minutes("not-a-date", 100, 10), [])
    # runaway guard: 1 minute/day capacity must stop at MAX_PLAN_DAYS, not hang
    check("spread runaway guard", len(svc.spread_minutes(today, 10 ** 6, 1)), 365)
    check("plan_end_date", svc.plan_end_date(today, 19656, 9828), str(today + timedelta(days=1)))
    check("plan_end_date no capacity", svc.plan_end_date(today, 100, 0), None)

    print("\n[4] line balancing formulas")
    # bottleneck = max(smv/operators); output/h = 60/bottleneck;
    # efficiency = sum(smv) / (operators x bottleneck) x 100
    ops = [{"name": "A", "smv": 1.0, "operators": 2},      # pitch 0.50
           {"name": "B", "smv": 2.0, "operators": 2},      # pitch 1.00  <- bottleneck
           {"name": "C", "smv": 1.0, "operators": 1}]      # pitch 1.00  <- ties
    b = svc.balance_metrics(ops)
    check("total_smv", b["total_smv"], 4.0)
    check("total_operators", b["total_operators"], 5)
    check("bottleneck_time", b["bottleneck_time"], 1.0)
    check("bottleneck_op", b["bottleneck_op"], "B")
    check("output_per_hour", b["output_per_hour"], 60.0)
    check("line_efficiency 4/(5*1)", b["line_efficiency"], 80.0)
    check("bottleneck rows flagged", sum(1 for r in b["rows"] if r["is_bottleneck"]), 2)
    # negative: unstaffed / empty / garbage must not divide by zero
    z = svc.balance_metrics([{"name": "X", "smv": 5.0, "operators": 0}])
    check("zero-operator op has no pitch", z["bottleneck_time"], 0.0)
    check("zero-operator output_per_hour", z["output_per_hour"], 0.0)
    check("zero-operator efficiency", z["line_efficiency"], 0.0)
    e = svc.balance_metrics([])
    check("empty bulletin efficiency", e["line_efficiency"], 0.0)
    check("None bulletin", svc.balance_metrics(None)["total_smv"], 0.0)
    g = svc.balance_metrics([{"name": "G", "smv": "", "operators": "two"}])
    check("garbage bulletin", (g["total_smv"], g["bottleneck_time"]), (0.0, 0.0))

    print("\n[5] seeded demo data")
    lines = svc.list_lines()
    check("seeded lines", len(lines), 4)
    sew1 = [l for l in lines if l["code"] == "SEW-1"][0]
    check("SEW-1 capacity", sew1["capacity"], 9828.0)
    orders = svc.list_orders()          # NB: sorted by ship_date, not by id
    by_no = {o["order_no"]: o for o in orders}
    tee, hoodie, denim = by_no["SO-1001"], by_no["SO-1002"], by_no["SO-1003"]
    check_true("demo orders present", len(orders) >= 3)
    check("tee SMV seeded", tee["smv"], 12.5)
    check("denim SMV seeded", denim["smv"], 18.5)
    allocs = svc.list_allocations()
    check("seeded allocations", len(allocs), 3)

    print("\n[6] board: load vs capacity, overload flagged")
    bd = svc.board(14)
    check("board rows", len(bd["grid"]), 4)
    check("board columns", len(bd["dates"]), 14)
    g_sew1 = [g for g in bd["grid"] if g["line"]["code"] == "SEW-1"][0]
    check("SEW-1 day0 load% (one allocation at full capacity)", g_sew1["cells"][0]["pct"], 100.0)
    check_true("SEW-1 overloaded once the 2nd allocation starts (day 5)",
               g_sew1["cells"][5]["pct"] > 100.0 and g_sew1["cells"][5]["over"])
    check_true("board reports overloaded cells", bd["overloaded_cells"] > 0)

    print("\n[7] feasibility vs the ship date")
    f = svc.feasibility(denim["id"])
    check("SO-1003 verdict", f["status"], "late")
    check_true("SO-1003 days_late > 0", f["days_late"] > 0)
    check("SO-1003 planned qty", f["planned_qty"], 4000.0)
    check("SO-1003 unplanned qty", f["unplanned_qty"], 5000.0)
    check("feasibility of a missing order", svc.feasibility(999999), None)

    print("\n[8] what-if persists nothing")
    before = len(svc.list_allocations())
    wi = svc.what_if(tee["id"], sew1["id"], str(today), 1000)
    check("what-if ok", wi["ok"], True)
    check("what-if used the order SMV (1000 x 12.5)", wi["minutes"], 12500.0)
    check("what-if days ceil(12500/9828)", wi["days"], 2)
    check_true("what-if sees the existing load as overload", wi["peak_pct"] > 100.0)
    check("what-if wrote nothing", len(svc.list_allocations()), before)
    check("what-if smv override", svc.what_if(tee["id"], sew1["id"], str(today), 1000, smv=20)["minutes"],
          20000.0)
    check("what-if bad line", svc.what_if(tee["id"], 999999, str(today), 10)["reason"],
          "line_not_found")
    check("what-if bad order", svc.what_if(999999, sew1["id"], str(today), 10)["reason"],
          "order_not_found")
    check("what-if zero qty", svc.what_if(tee["id"], sew1["id"], str(today), 0)["reason"], "bad_qty")
    check("what-if garbage qty", svc.what_if(tee["id"], sew1["id"], str(today), "lots")["reason"],
          "bad_qty")
    check("what-if explicit zero SMV", svc.what_if(tee["id"], sew1["id"], str(today), 10, smv=0)["reason"],
          "no_smv")

    print("\n[9] a zero-capacity line cannot be planned on")
    zid = svc.create_line({"code": "DEAD-1", "name": "Idle line", "section": "Sewing",
                           "operators": 0, "working_minutes": 540, "efficiency_pct": 65}, None)
    check("zero-capacity line created",
          [l for l in svc.list_lines() if l["id"] == zid][0]["capacity"], 0.0)
    ok, why = svc.create_allocation(
        {"order_id": tee["id"], "pline_id": zid, "qty": 100, "start_date": str(today)}, None)
    check("allocation onto a zero-capacity line refused", (ok, why), (False, "no_capacity"))
    check("what-if onto a zero-capacity line", svc.what_if(tee["id"], zid, str(today), 100)["reason"],
          "no_capacity")

    print("\n[10] allocation validation + SMV")
    check("allocate zero qty", svc.create_allocation(
        {"order_id": tee["id"], "pline_id": sew1["id"], "qty": 0}, None), (False, "bad_qty"))
    check("allocate garbage qty", svc.create_allocation(
        {"order_id": tee["id"], "pline_id": sew1["id"], "qty": "many"}, None), (False, "bad_qty"))
    check("allocate missing line", svc.create_allocation(
        {"order_id": tee["id"], "pline_id": 999999, "qty": 10}, None), (False, "line_not_found"))
    check("allocate missing order", svc.create_allocation(
        {"order_id": 999999, "pline_id": sew1["id"], "qty": 10}, None), (False, "order_not_found"))
    check("set_smv rejects negative", svc.set_smv(tee["id"], -1)[1], "bad_smv")
    check("set_smv rejects garbage", svc.set_smv(tee["id"], "abc")[1], "bad_smv")
    check("set_smv accepts a number", svc.set_smv(hoodie["id"], 24.5)[0], True)
    check("set_smv is an upsert (no duplicate row)",
          conn.execute("SELECT COUNT(*) AS c FROM pln_order_smv WHERE order_id=?",
                       (hoodie["id"],)).fetchone()["c"], 1)
    check("set_smv value stored", [o for o in svc.list_orders()
                                   if o["id"] == hoodie["id"]][0]["smv"], 24.5)

    print("\n[11] SMV is snapshotted onto the allocation")
    ok, aid = svc.create_allocation({"order_id": hoodie["id"], "pline_id": sew1["id"],
                                     "qty": 500, "start_date": str(today)}, {"username": "tester"})
    check("allocation created", ok, True)
    snap = [a for a in svc.list_allocations(hoodie["id"]) if a["id"] == aid][0]
    check("snapshot took the current SMV", snap["smv"], 24.5)
    svc.set_smv(hoodie["id"], 30.0)                    # later edit must not move the plan
    snap2 = [a for a in svc.list_allocations(hoodie["id"]) if a["id"] == aid][0]
    check("committed plan keeps its SMV", snap2["smv"], 24.5)
    check("committed plan keeps its finish date", snap2["end_date"], snap["end_date"])

    print("\n[12] un-plan is idempotent (double-apply is safe)")
    n_before = len(svc.list_allocations(hoodie["id"]))
    svc.delete_allocation(aid)
    n_after = len(svc.list_allocations(hoodie["id"]))
    check("un-plan removes it from the live plan", n_after, n_before - 1)
    svc.delete_allocation(aid)                          # again
    check("second un-plan changes nothing", len(svc.list_allocations(hoodie["id"])), n_after)
    check("row is cancelled, not deleted",
          conn.execute("SELECT status FROM pln_allocations WHERE id=?", (aid,)).fetchone()["status"],
          "cancelled")
    check("un-plan of a missing id is a no-op", svc.delete_allocation(999999), True)

    print("\n[12b] editing line capacity reflows the projected finish dates")
    # SEW-2 carries the denim allocation: 4000 x 18.5 = 74000 min at 24x540x60% = 7776/day -> 10 days
    sew2 = [l for l in svc.list_lines() if l["code"] == "SEW-2"][0]
    a2 = [a for a in svc.list_allocations(denim["id"]) if a["line_code"] == "SEW-2"][0]
    check("SEW-2 capacity before", sew2["capacity"], 7776.0)
    check("denim days before", a2["days"], 10)
    check("denim finish before", a2["end_date"], str(today + timedelta(days=9)))
    svc.update_line(sew2["id"], {"operators": 48}, None)        # double the line
    sew2b = [l for l in svc.list_lines() if l["code"] == "SEW-2"][0]
    a2b = [a for a in svc.list_allocations(denim["id"]) if a["line_code"] == "SEW-2"][0]
    check("SEW-2 capacity after", sew2b["capacity"], 15552.0)
    check("denim days after", a2b["days"], 5)
    check("denim finish reflowed", a2b["end_date"], str(today + timedelta(days=4)))
    check("feasibility agrees with the board", svc.feasibility(denim["id"])["finish"],
          str(today + timedelta(days=4)))
    svc.update_line(sew2["id"], {"operators": 24}, None)        # restore
    check("reflow is reversible", [a for a in svc.list_allocations(denim["id"])
                                   if a["line_code"] == "SEW-2"][0]["end_date"],
          str(today + timedelta(days=9)))
    check("editing a non-capacity field leaves the plan alone",
          (svc.update_line(sew2["id"], {"code": "SEW-2"}, None),
           [a for a in svc.list_allocations(denim["id"])
            if a["line_code"] == "SEW-2"][0]["end_date"]),
          (True, str(today + timedelta(days=9))))

    print("\n[13] bell alerts reached the notification table")
    n = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='planning'").fetchone()["c"]
    check_true("planning raised at least one bell alert", n > 0)

    print("\n[14] operations bulletin CRUD")
    oid0 = tee["id"]                    # the seeded bulletin belongs to the tee order
    bal = svc.balance(oid0)
    check("seeded bulletin rows", len(bal["rows"]), 9)
    check("seeded bulletin total SMV", bal["total_smv"], 10.5)
    check("seeded bulletin operators", bal["total_operators"], 25)
    check("seeded bottleneck (1.05/2)", bal["bottleneck_time"], 0.525)
    check("seeded efficiency 10.5/(25*0.525)", bal["line_efficiency"], 80.0)
    check("add_op rejects a blank name", svc.add_op(oid0, {"name": "  "})[1], "name_required")
    check("add_op accepts", svc.add_op(oid0, {"name": "Bartack", "smv": 0.4, "operators": 1})[0], True)
    check("bulletin grew", len(svc.balance(oid0)["rows"]), 10)
    new_op = [r for r in svc.balance(oid0)["rows"] if r["name"] == "Bartack"][0]
    check("new op sequenced last", new_op["seq"], 10)
    svc.delete_op(new_op["id"])
    check("op removed", len(svc.balance(oid0)["rows"]), 9)
    check("balance of a missing order", svc.balance(999999), None)

    print("\n[15] create_and_seed is idempotent (safe on every boot, never destroys)")
    lines_before = len(svc.list_lines())
    allocs_before = len(svc.list_allocations())
    ops_before = len(svc.balance(oid0)["rows"])
    create_and_seed(conn)
    create_and_seed(conn)
    check("lines unchanged after re-seed", len(svc.list_lines()), lines_before)
    check("allocations unchanged after re-seed", len(svc.list_allocations()), allocs_before)
    check("operations unchanged after re-seed", len(svc.balance(oid0)["rows"]), ops_before)
    check("edited SMV survived re-seed", [o for o in svc.list_orders()
                                          if o["id"] == hoodie["id"]][0]["smv"], 30.0)

    print("\n[16] dashboard rolls up without exploding")
    d = svc.dashboard()
    check_true("dashboard has KPIs", set(d["kpi"]) >= {"lines", "utilisation", "overloaded",
                                                       "idle_hours", "late", "at_risk", "unplanned"})
    check_true("utilisation is a percentage", 0 <= d["kpi"]["utilisation"] <= 100)
    check("dashboard order count matches", len(d["orders"]), len(svc.list_orders()))

    conn.close()

print("\n" + ("=" * 60))
if FAILS:
    print(f"SELF-TEST FAILED — {len(FAILS)} failure(s):")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("SELF-TEST PASSED — all planning assertions green.")
