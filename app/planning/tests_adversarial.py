"""
ADVERSARIAL test for the planning module.

Every case here is an attempt to make the module produce a WRONG NUMBER, plan the
WRONG ORDER, double-book a line, crash, or lose a row. It asserts the CORRECT
behaviour, so a failing line is a real defect, not a style opinion.

Runs against a THROWAWAY database — never the repo's platform.db.

    python app/planning/tests_adversarial.py
"""
import ast
import math
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

REPO = r"D:\TC platform\tc-platform-render"
TMP = Path(tempfile.mkdtemp(prefix="plnadv_"))
os.chdir(TMP)
sys.path.insert(0, REPO)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                        # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                           # noqa: E402

app = create_app()
FAILS = []


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {label} = {got!r}")


def yes(label, cond):
    check(label, bool(cond), True)


def section(n, title):
    print(f"\n[{n}] {title}")


with app.app_context():
    from app.db import get_db
    from app.planning.schema import create_and_seed
    from app.planning import services as svc
    from app.planning.constants import MAX_PLAN_DAYS, AT_RISK_DAYS

    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    TODAY = date.today()

    def counts(c):
        return tuple(c.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                     for t in ("pln_lines", "pln_order_smv", "pln_allocations", "pln_ops"))

    # ---------------------------------------------------------------- 1
    section(1, "create_and_seed run THREE times: no duplicates, no destruction")
    c1 = counts(conn)
    ids1 = [r["id"] for r in conn.execute("SELECT id FROM pln_lines ORDER BY id").fetchall()]
    create_and_seed(conn); conn.commit()
    create_and_seed(conn); conn.commit()
    c3 = counts(conn)
    ids3 = [r["id"] for r in conn.execute("SELECT id FROM pln_lines ORDER BY id").fetchall()]
    check("counts after 1st seed", c1, (4, 3, 3, 9))
    check("counts unchanged after 3 seeds", c3, c1)
    check("line ids unchanged (nothing recreated)", ids3, ids1)
    dup = conn.execute("SELECT COUNT(*) AS n FROM (SELECT order_id FROM pln_order_smv "
                       "GROUP BY order_id HAVING COUNT(*)>1) x").fetchone()["n"]
    check("no duplicate SMV rows", dup, 0)

    # ---------------------------------------------------------------- 2
    section(2, "capacity arithmetic recomputed by hand")
    cap = svc.daily_capacity_minutes
    check("28 x 540 x 65%", cap({"operators": 28, "working_minutes": 540, "efficiency_pct": 65}),
          round(28 * 540 * 65 / 100.0, 2))
    check("1 x 1 x 0.5% (2dp)", cap({"operators": 1, "working_minutes": 1,
                                     "efficiency_pct": 0.5}), 0.01)
    check("string inputs", cap({"operators": "28", "working_minutes": "540",
                                "efficiency_pct": "65"}), 9828.0)
    check("negative operators -> 0", cap({"operators": -5, "working_minutes": 540,
                                          "efficiency_pct": 65}), 0.0)
    check("negative efficiency -> 0", cap({"operators": 5, "working_minutes": 540,
                                           "efficiency_pct": -65}), 0.0)
    check("None line -> 0", cap(None), 0.0)
    check("empty dict -> 0", cap({}), 0.0)
    check("garbage -> 0", cap({"operators": "many", "working_minutes": None,
                               "efficiency_pct": ""}), 0.0)
    check("over-100% efficiency is allowed (overtime)",
          cap({"operators": 10, "working_minutes": 600, "efficiency_pct": 110}), 6600.0)

    section("2b", "required_minutes / required_days")
    check("12000 x 12.5", svc.required_minutes(12000, 12.5), 150000.0)
    check("negative qty -> 0", svc.required_minutes(-12000, 12.5), 0.0)
    check("negative smv -> 0", svc.required_minutes(12000, -12.5), 0.0)
    check("None -> 0", svc.required_minutes(None, None), 0.0)
    check("ceil(150000/9828)", svc.required_days(150000, 9828), math.ceil(150000 / 9828))
    check("exact multiple is not rounded up", svc.required_days(19656, 9828), 2)
    check("zero capacity -> None (no divide)", svc.required_days(150000, 0), None)
    check("None capacity -> None", svc.required_days(150000, None), None)
    check("negative capacity -> None", svc.required_days(150000, -5), None)

    # ---------------------------------------------------------------- 3
    section(3, "spread_minutes invariants (nothing lost, nothing invented)")
    sp = svc.spread_minutes(TODAY, 150000, 9828)
    check("day count == required_days", len(sp), svc.required_days(150000, 9828))
    check("per-day minutes sum back to the requirement", round(sum(m for _, m in sp), 2), 150000.0)
    check("first day = full capacity", sp[0][1], 9828.0)
    check("last day = remainder", sp[-1][1], round(150000 - 9828 * 15, 2))
    check("no day exceeds capacity", max(m for _, m in sp) <= 9828.0, True)
    check("dates are consecutive",
          [d for d, _ in sp] == [TODAY + timedelta(days=i) for i in range(len(sp))], True)
    drift = svc.spread_minutes(TODAY, 0.3, 0.1)   # float drift must not invent a day
    check("float drift: 0.3 / 0.1 = 3 days not 4", len(drift), 3)
    check("float drift sum", round(sum(m for _, m in drift), 2), 0.3)
    COMBOS = [(q * s, c) for q in (1, 7, 999, 12345) for s in (0.5, 12.5, 33.33)
              for c in (60.0, 480.0, 9828.0, 10000.0)]
    check(f"spread day count == required_days across {len(COMBOS)} combinations",
          all(len(svc.spread_minutes(TODAY, m, c)) ==
              min(svc.required_days(m, c), MAX_PLAN_DAYS) for m, c in COMBOS), True)
    check("spread total == minutes wherever the plan fits inside the guard",
          all(abs(sum(x for _, x in svc.spread_minutes(TODAY, m, c)) - round(m, 2)) < 0.02
              for m, c in COMBOS if svc.required_days(m, c) <= MAX_PLAN_DAYS), True)
    check("a plan past the guard is refused, never truncated into a promise",
          all(svc.plan_end_date(TODAY, m, c) is None
              for m, c in COMBOS if svc.required_days(m, c) > MAX_PLAN_DAYS), True)
    check("combinations that exercise the guard", sum(
        1 for m, c in COMBOS if svc.required_days(m, c) > MAX_PLAN_DAYS), 4)
    check("zero capacity -> []", svc.spread_minutes(TODAY, 100, 0), [])
    check("zero minutes -> []", svc.spread_minutes(TODAY, 0, 9828), [])
    check("bad date -> []", svc.spread_minutes("not-a-date", 100, 10), [])
    check("None date -> []", svc.spread_minutes(None, 100, 10), [])

    # ---------------------------------------------------------------- 4
    section(4, "runaway guard must not report a FAKE finish date")
    # 500,000 minutes at 1 minute/day needs 500,000 days. Truncating the spread at
    # 365 rows and reporting day 365 as the finish is a silently wrong promise.
    check("spread is capped at the guard", len(svc.spread_minutes(TODAY, 500000, 1)),
          MAX_PLAN_DAYS)
    check("capped spread never claims to be complete",
          round(sum(m for _, m in svc.spread_minutes(TODAY, 500000, 1)), 2) < 500000.0, True)
    check("plan_end_date refuses a >1y plan instead of truncating it",
          svc.plan_end_date(TODAY, 500000, 1), None)
    check("plan_end_date at exactly the guard is still planned",
          svc.plan_end_date(TODAY, float(MAX_PLAN_DAYS), 1.0),
          str(TODAY + timedelta(days=MAX_PLAN_DAYS - 1)))
    check("plan_end_date one day past the guard is refused",
          svc.plan_end_date(TODAY, float(MAX_PLAN_DAYS) + 1, 1.0), None)
    check("normal plan_end_date", svc.plan_end_date(TODAY, 150000, 9828),
          str(TODAY + timedelta(days=15)))

    # ---------------------------------------------------------------- 5
    section(5, "line balancing formulas")
    bm = svc.balance_metrics([{"name": "A", "smv": 1.0, "operators": 2},
                              {"name": "B", "smv": 2.0, "operators": 2},
                              {"name": "C", "smv": 1.0, "operators": 1}])
    check("total_smv", bm["total_smv"], 4.0)
    check("total_operators", bm["total_operators"], 5)
    check("bottleneck = max(smv/operators)", bm["bottleneck_time"], 1.0)
    check("bottleneck op", bm["bottleneck_op"], "B")
    check("output/hour = 60/bottleneck", bm["output_per_hour"], 60.0)
    check("efficiency = 4/(5x1)", bm["line_efficiency"], 80.0)
    # An unmanned operation carries SMV but no operators: counting its SMV in the
    # numerator while its 0 operators add nothing to the denominator invents
    # efficiency out of thin air.
    un = svc.balance_metrics([{"name": "manned", "smv": 1.0, "operators": 1},
                              {"name": "unmanned", "smv": 5.0, "operators": 0}])
    check("unmanned op cannot push efficiency over 100%", un["line_efficiency"] <= 100.0, True)
    check("unmanned op efficiency", un["line_efficiency"], 100.0)
    check("unmanned ops are counted and reported", un.get("unmanned_ops"), 1)
    check("unmanned op has no pitch", un["rows"][1]["pitch"], 0.0)
    check("perfectly balanced line is 100%",
          svc.balance_metrics([{"name": "x", "smv": 1.0, "operators": 1},
                               {"name": "y", "smv": 2.0, "operators": 2}])["line_efficiency"],
          100.0)
    check("efficiency never exceeds 100% over 60 random bulletins",
          all(svc.balance_metrics(
              [{"name": "a", "smv": a, "operators": n1},
               {"name": "b", "smv": b, "operators": n2}])["line_efficiency"] <= 100.0
              for a in (0.1, 1.0, 7.5) for b in (0.2, 3.0, 12.0)
              for n1 in (0, 1, 4) for n2 in (0, 2, 9)), True)
    neg = svc.balance_metrics([{"name": "A", "smv": 2.0, "operators": 2},
                               {"name": "bad", "smv": -10.0, "operators": 1}])
    check("a negative SMV never produces a negative total", neg["total_smv"] >= 0.0, True)
    check("a negative SMV never produces a negative efficiency",
          neg["line_efficiency"] >= 0.0, True)
    check("empty bulletin", svc.balance_metrics([])["line_efficiency"], 0.0)
    check("None bulletin", svc.balance_metrics(None)["total_smv"], 0.0)
    check("all-garbage bulletin", svc.balance_metrics(
        [{"name": "x", "smv": "abc", "operators": "abc"}])["output_per_hour"], 0.0)

    # ---------------------------------------------------------------- 6
    section(6, "a missing / garbage order id must NOT silently plan the FIRST order")
    first_order = conn.execute("SELECT id,order_no FROM ord_orders ORDER BY id").fetchone()
    line_id = conn.execute("SELECT id FROM pln_lines WHERE code='SEW-1'").fetchone()["id"]
    yes("fixture: a demo order exists", first_order is not None)

    before = conn.execute("SELECT COUNT(*) AS n FROM pln_allocations").fetchone()["n"]
    check("create_allocation with NO order_id is refused",
          svc.create_allocation({"pline_id": line_id, "qty": 500, "smv": 10}, {}),
          (False, "order_not_found"))
    check("create_allocation with a garbage order_id is refused",
          svc.create_allocation({"order_id": "abc", "pline_id": line_id, "qty": 500,
                                 "smv": 10}, {}), (False, "order_not_found"))
    check("create_allocation with an unknown order id is refused",
          svc.create_allocation({"order_id": 999999, "pline_id": line_id, "qty": 500,
                                 "smv": 10}, {}), (False, "order_not_found"))
    check("no orphan allocation rows were written",
          conn.execute("SELECT COUNT(*) AS n FROM pln_allocations").fetchone()["n"], before)

    check("feasibility(0) is not silently the first order", svc.feasibility(0), None)
    check("feasibility(999999) is None", svc.feasibility(999999), None)
    check("balance(0) is None", svc.balance(0), None)
    check("list_allocations(0) does not return every allocation", svc.list_allocations(0), [])
    check("what_if with a blank order id is refused",
          svc.what_if("", line_id, str(TODAY), 100, 10).get("reason"), "order_not_found")
    check("what_if with an unknown order is refused",
          svc.what_if(999999, line_id, str(TODAY), 100, 10).get("reason"), "order_not_found")
    check("list_allocations() with no filter still returns every open allocation",
          len(svc.list_allocations()), 3)
    check("order_exists rejects 0", svc.order_exists(0), False)
    check("order_exists accepts a real order", svc.order_exists(first_order["id"]), True)

    # ---------------------------------------------------------------- 7
    section(7, "controlled scenario: capacity, feasibility, snapshot, reflow")
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_name,qty,ship_date,status,"
                 "created_at) VALUES (?,?,?,?,?,?,?)",
                 ("ADV-1", "Adversary Ltd", "Test tee", 10000,
                  str(TODAY + timedelta(days=30)), "confirmed", "t"))
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='ADV-1'").fetchone()["id"]
    conn.execute("INSERT INTO pln_lines (code,name,section,operators,working_minutes,"
                 "efficiency_pct,active,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 ("ADV-L", "Adversary line", "Sewing", 10, 500, 50.0, 1, "t"))
    lid = conn.execute("SELECT id FROM pln_lines WHERE code='ADV-L'").fetchone()["id"]
    conn.commit()
    check("fixture capacity 10 x 500 x 50%", svc.daily_capacity_minutes(
        dict(conn.execute("SELECT * FROM pln_lines WHERE id=?", (lid,)).fetchone())), 2500.0)

    check("set_smv rejects a negative", svc.set_smv(oid, -1), (False, "bad_smv"))
    check("set_smv rejects garbage", svc.set_smv(oid, "twelve"), (False, "bad_smv"))
    check("set_smv rejects an unknown order", svc.set_smv(999999, 5), (False, "order_not_found"))
    check("set_smv accepts", svc.set_smv(oid, 0.5), (True, "ok"))
    check("set_smv is an upsert", svc.set_smv(oid, 0.5), (True, "ok"))
    check("still exactly one SMV row", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_order_smv WHERE order_id=?", (oid,)).fetchone()["n"], 1)

    # 10000 pieces x 0.5 SMV = 5000 minutes; 5000 / 2500 = 2 days exactly.
    ok, aid = svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 10000,
                                    "start_date": str(TODAY)}, {"username": "adv"})
    check("allocation committed", ok, True)
    a = dict(conn.execute("SELECT * FROM pln_allocations WHERE id=?", (aid,)).fetchone())
    check("snapshotted smv", a["smv"], 0.5)
    check("end_date = start + 1 (2 days of work)", a["end_date"], str(TODAY + timedelta(days=1)))
    f = svc.feasibility(oid)
    check("planned qty", f["planned_qty"], 10000.0)
    check("unplanned qty clamps at 0", f["unplanned_qty"], 0.0)
    check("required minutes", f["required_minutes"], 5000.0)
    check("verdict on_time", f["status"], "on_time")
    check("days_late is negative when early", f["days_late"] < 0, True)

    section("7b", "SMV snapshot survives an order SMV edit")
    svc.set_smv(oid, 3.0)
    check("committed allocation keeps its snapshot", conn.execute(
        "SELECT smv FROM pln_allocations WHERE id=?", (aid,)).fetchone()["smv"], 0.5)
    check("the order SMV really did change", svc.feasibility(oid)["order"]["smv"], 3.0)
    svc.set_smv(oid, 0.5)

    section("7c", "editing line capacity reflows open allocations, both directions")
    svc.update_line(lid, {"operators": "5"}, {})       # capacity 1250 -> 4 days
    check("halved capacity doubles the plan", conn.execute(
        "SELECT end_date FROM pln_allocations WHERE id=?", (aid,)).fetchone()["end_date"],
        str(TODAY + timedelta(days=3)))
    svc.update_line(lid, {"operators": "10"}, {})      # back to 2500 -> 2 days
    check("restored capacity restores the plan", conn.execute(
        "SELECT end_date FROM pln_allocations WHERE id=?", (aid,)).fetchone()["end_date"],
        str(TODAY + timedelta(days=1)))
    svc.update_line(lid, {"operators": "0"}, {})       # no capacity at all
    check("zero capacity clears the projected finish", conn.execute(
        "SELECT end_date FROM pln_allocations WHERE id=?", (aid,)).fetchone()["end_date"], None)
    check("feasibility says no_capacity", svc.feasibility(oid)["status"], "no_capacity")
    svc.update_line(lid, {"operators": "10"}, {})
    check("reflow recovered", svc.feasibility(oid)["status"], "on_time")
    check("update_line on a missing id reports failure (never a false 'saved')",
          svc.update_line(999999, {"operators": "5"}, {}), False)
    check("update_line with no known field is a no-op", svc.update_line(lid, {"zzz": 1}, {}), False)
    svc.update_line(lid, {"name": ""}, {})
    check("a blank name never nulls the NOT NULL name column", conn.execute(
        "SELECT name FROM pln_lines WHERE id=?", (lid,)).fetchone()["name"], "Adversary line")

    section("7d", "un-planning is idempotent and cancelled load leaves the board")
    yes("line is loaded before un-planning", sum(svc._load_by_day(conn, lid).values()) > 0)
    check("delete_allocation", svc.delete_allocation(aid), True)
    check("second delete is a no-op", svc.delete_allocation(aid), True)
    check("row is cancelled, not destroyed", conn.execute(
        "SELECT status FROM pln_allocations WHERE id=?", (aid,)).fetchone()["status"], "cancelled")
    check("delete of a missing id is a no-op", svc.delete_allocation(999999), True)
    check("cancelled load is off the board", sum(svc._load_by_day(conn, lid).values()), 0)
    check("order is unplanned again", svc.feasibility(oid)["status"], "unplanned")
    frozen = conn.execute("SELECT end_date FROM pln_allocations WHERE id=?",
                          (aid,)).fetchone()["end_date"]
    svc.update_line(lid, {"operators": "7"}, {})
    check("a cancelled allocation is not reflowed by a capacity edit", conn.execute(
        "SELECT end_date FROM pln_allocations WHERE id=?", (aid,)).fetchone()["end_date"], frozen)
    svc.update_line(lid, {"operators": "10"}, {})

    # ---------------------------------------------------------------- 8
    section(8, "create_allocation refuses, never clamps")
    conn.execute("INSERT INTO pln_lines (code,name,operators,working_minutes,efficiency_pct,"
                 "active,created_at) VALUES ('DEAD','Dead line',0,0,0,1,'t')")
    conn.commit()
    dead = conn.execute("SELECT id FROM pln_lines WHERE code='DEAD'").fetchone()["id"]
    bad = [({"order_id": oid, "pline_id": lid, "qty": 0}, "bad_qty"),
           ({"order_id": oid, "pline_id": lid, "qty": -50}, "bad_qty"),
           ({"order_id": oid, "pline_id": lid, "qty": "lots"}, "bad_qty"),
           ({"order_id": oid, "pline_id": 999999, "qty": 10}, "line_not_found"),
           ({"order_id": oid, "pline_id": "x", "qty": 10}, "line_not_found"),
           ({"order_id": oid, "pline_id": lid, "qty": 10, "smv": -3}, "no_smv"),
           ({"order_id": oid, "pline_id": dead, "qty": 10}, "no_capacity"),
           ({"order_id": oid, "pline_id": lid, "qty": 10 ** 9, "smv": 100}, "unplannable")]
    n_alloc = conn.execute("SELECT COUNT(*) AS n FROM pln_allocations").fetchone()["n"]
    for data, want in bad:
        check(f"refused: {want} (qty={data.get('qty')!r} line={data.get('pline_id')!r} "
              f"smv={data.get('smv')!r})", svc.create_allocation(data, {}), (False, want))
    svc.set_smv(oid, 0)
    check("refused: no_smv when the order SMV is 0",
          svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 10}, {}),
          (False, "no_smv"))
    svc.set_smv(oid, 0.5)
    check("nothing was written by any refusal", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_allocations").fetchone()["n"], n_alloc)

    section("8b", "a double submit must not double-book the line")
    d = {"order_id": oid, "pline_id": lid, "qty": 4000, "start_date": str(TODAY)}
    ok1, id1 = svc.create_allocation(dict(d), {"username": "adv"})
    check("first submit is accepted", ok1, True)
    check("identical second submit is refused",
          svc.create_allocation(dict(d), {"username": "adv"}), (False, "duplicate"))
    check("only one live allocation exists for it", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_allocations WHERE order_id=? AND pline_id=? "
        "AND status!='cancelled'", (oid, lid)).fetchone()["n"], 1)
    check("a genuinely different quantity is still accepted",
          svc.create_allocation({**d, "qty": 1000}, {"username": "adv"})[0], True)
    svc.delete_allocation(id1)
    check("a re-plan after un-planning is accepted",
          svc.create_allocation(dict(d), {})[0], True)
    conn.execute("UPDATE pln_allocations SET status='cancelled' WHERE order_id=?", (oid,))
    conn.commit()

    # ---------------------------------------------------------------- 9
    section(9, "load board arithmetic on an isolated line")
    ok, aid = svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 10000,
                                    "start_date": str(TODAY)}, {})
    yes("fixture allocation (5000 min on a 2500 min/day line)", ok)
    row = [g for g in svc.board(14)["grid"] if g["line"]["id"] == lid][0]
    check("board capacity", row["capacity"], 2500.0)
    check("day 0 load %", row["cells"][0]["pct"], 100.0)
    check("day 1 load %", row["cells"][1]["pct"], 100.0)
    check("day 2 is free", row["cells"][2]["pct"], 0.0)
    check("exactly 100% is not an overload", row["cells"][0]["over"], False)
    check("board columns", len(row["cells"]), 14)
    svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 2000,
                           "start_date": str(TODAY)}, {})
    row = [g for g in svc.board(14)["grid"] if g["line"]["id"] == lid][0]
    check("overlap pushes the day over 100%", row["cells"][0]["pct"],
          round(100.0 * 3500 / 2500, 1))
    check("overload flagged", row["cells"][0]["over"], True)
    check("utilisation is never over 100%", svc.board(14)["utilisation"] <= 100.0, True)
    check("a no-capacity line is drawn as '—', not 0%",
          [g for g in svc.board(1)["grid"] if g["line"]["id"] == dead][0]["cells"][0]["no_cap"],
          True)
    check("board horizon clamps: 0", svc.board(0)["days"], 14)
    check("board horizon clamps: -5", svc.board(-5)["days"], 1)
    check("board horizon clamps: 9999", svc.board(9999)["days"], 60)
    check("board horizon clamps: garbage", svc.board("abc")["days"], 14)
    check("board horizon clamps: None", svc.board(None)["days"], 14)

    section("9b", "what-if persists nothing and stacks onto the existing load")
    n_before = conn.execute("SELECT COUNT(*) AS n FROM pln_allocations").fetchone()["n"]
    wi = svc.what_if(oid, lid, str(TODAY), 5000, 0.5)
    check("what_if ok", wi["ok"], True)
    check("what_if minutes 5000 x 0.5", wi["minutes"], 2500.0)
    check("what_if day 0 stacks on the committed 3500", wi["rows"][0]["minutes"], 6000.0)
    check("what_if peak %", wi["peak_pct"], round(100.0 * 6000 / 2500, 1))
    check("what_if wrote nothing", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_allocations").fetchone()["n"], n_before)
    check("what_if refuses exactly what the commit refuses (>1y)",
          svc.what_if(oid, lid, str(TODAY), 10 ** 9, 100)["reason"], "unplannable")
    check("what_if rejects a zero quantity",
          svc.what_if(oid, lid, str(TODAY), 0, 1)["reason"], "bad_qty")
    check("what_if rejects a negative SMV override",
          svc.what_if(oid, lid, str(TODAY), 10, -1)["reason"], "no_smv")
    check("what_if on a dead line", svc.what_if(oid, dead, str(TODAY), 10, 1)["reason"],
          "no_capacity")

    # ---------------------------------------------------------------- 10
    section(10, "feasibility verdict boundaries")
    v = svc._verdict
    ship = TODAY + timedelta(days=10)
    check("finish after ship = late", v(ship + timedelta(days=1), ship, True), ("late", 1))
    check("finish on the ship date = at_risk", v(ship, ship, True), ("at_risk", 0))
    check(f"finish {AT_RISK_DAYS}d early = at_risk",
          v(ship - timedelta(days=AT_RISK_DAYS), ship, True), ("at_risk", -AT_RISK_DAYS))
    check(f"finish {AT_RISK_DAYS + 1}d early = on_time",
          v(ship - timedelta(days=AT_RISK_DAYS + 1), ship, True),
          ("on_time", -(AT_RISK_DAYS + 1)))
    check("no allocations = unplanned", v(None, ship, False), ("unplanned", None))
    check("no finish = no_capacity", v(None, ship, True), ("no_capacity", None))
    check("no ship date = no_ship_date", v(ship, None, True), ("no_ship_date", None))
    check("days_late is positive only when late",
          all(v(ship + timedelta(days=k), ship, True)[1] <= 0 for k in range(-10, 1)), True)

    # ---------------------------------------------------------------- 11
    section(11, "bell alerts")
    conn.execute("DELETE FROM notifications WHERE module='planning'")
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,ship_date,status,created_at) "
                 "VALUES ('ADV-LATE','Adversary Ltd',100000,?,'confirmed','t')",
                 (str(TODAY + timedelta(days=2)),))
    conn.commit()
    lo = conn.execute("SELECT id FROM ord_orders WHERE order_no='ADV-LATE'").fetchone()["id"]
    svc.set_smv(lo, 1.0)
    ok, _res = svc.create_allocation({"order_id": lo, "pline_id": lid, "qty": 100000,
                                     "start_date": str(TODAY)}, {})
    yes("late allocation committed", ok)
    notes = [dict(r) for r in conn.execute(
        "SELECT * FROM notifications WHERE module='planning' ORDER BY id").fetchall()]
    check("a late plan raises a critical alert",
          any(n["severity"] == "critical" for n in notes), True)
    check("the overloaded line raises a warning",
          any(n["severity"] == "warning" for n in notes), True)
    check("the alert links to the order page",
          any((n["link"] or "").endswith(f"/planning/orders/{lo}") for n in notes), True)
    check("an alert never loses the allocation", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_allocations WHERE order_id=?", (lo,)).fetchone()["n"], 1)

    # ---------------------------------------------------------------- 12
    section(12, "operation bulletin guards")
    check("add_op needs a name", svc.add_op(oid, {"name": "  "}), (False, "name_required"))
    check("add_op rejects a negative SMV",
          svc.add_op(oid, {"name": "bad", "smv": -1}), (False, "bad_smv"))
    check("add_op rejects an unknown order",
          svc.add_op(999999, {"name": "x", "smv": 1}), (False, "order_not_found"))
    check("add_op accepts", svc.add_op(oid, {"name": "Join", "smv": 1.5, "operators": 2}),
          (True, "ok"))
    check("add_op auto-sequences",
          svc.add_op(oid, {"name": "Hem", "smv": 1.0, "operators": 1}), (True, "ok"))
    check("sequence numbers", [r["seq"] for r in conn.execute(
        "SELECT seq FROM pln_ops WHERE order_id=? ORDER BY id", (oid,)).fetchall()], [1, 2])
    check("garbage operators is accepted but clamped",
          svc.add_op(oid, {"name": "G", "smv": 1, "operators": "-4"}), (True, "ok"))
    check("operators clamped at 0", conn.execute(
        "SELECT operators FROM pln_ops WHERE order_id=? ORDER BY id DESC LIMIT 1",
        (oid,)).fetchone()["operators"], 0)
    bal = svc.balance(oid)
    check("balance efficiency stays within 0-100",
          0.0 <= bal["line_efficiency"] <= 100.0, True)
    op_id = conn.execute("SELECT id FROM pln_ops WHERE order_id=? ORDER BY id",
                         (oid,)).fetchone()["id"]
    check("delete_op", svc.delete_op(op_id), True)
    check("delete_op twice is a no-op", svc.delete_op(op_id), True)
    check("delete_op of a missing id is a no-op", svc.delete_op(999999), True)
    check("the op really is gone", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_ops WHERE id=?", (op_id,)).fetchone()["n"], 0)

    # ---------------------------------------------------------------- 13
    section(13, "empty world: no lines, no allocations, nothing crashes")
    conn.execute("DELETE FROM pln_allocations")
    conn.execute("DELETE FROM pln_lines")
    conn.execute("DELETE FROM pln_ops")
    conn.execute("DELETE FROM pln_order_smv")
    conn.commit()
    dash = svc.dashboard(14)
    check("empty board utilisation", dash["kpi"]["utilisation"], 0.0)
    check("empty board lines", dash["kpi"]["lines"], 0)
    check("empty board idle hours", dash["kpi"]["idle_hours"], 0)
    check("every open order is unplanned", dash["kpi"]["unplanned"], len(dash["orders"]))
    check("list_lines on an empty table", svc.list_lines(), [])
    check("what_if with no lines", svc.what_if(oid, 1, str(TODAY), 10, 1)["reason"],
          "line_not_found")
    check("feasibility with no allocations", svc.feasibility(oid)["status"], "unplanned")
    check("balance with no ops", svc.balance(oid)["line_efficiency"], 0.0)

    section("13b", "re-seeding an emptied module restores the demo data")
    create_and_seed(conn); conn.commit()
    check("seed restored", counts(conn)[0], 4)

    conn.close()

# ---------------------------------------------------------------- 14
section(14, "templates parse, carry CSRF, and declare their i18n keys")
import jinja2                                        # noqa: E402
env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(REPO, "app", "templates")))
tpl_dir = Path(REPO) / "app" / "templates" / "planning"
keys = set()
for p in sorted(tpl_dir.glob("*.html")):
    src = p.read_text(encoding="utf-8")
    try:
        env.parse(src)
        print(f"  ok    {p.name} parses")
    except Exception as e:                            # pragma: no cover
        FAILS.append(f"{p.name}: {e}")
        print(f"  FAIL  {p.name}: {e}")
    keys |= {k for k in re.findall(r'data-i18n="([^"]+)"', src) if "{{" not in k}
    yes(f"{p.name} extends base.html", src.lstrip().startswith('{% extends "base.html" %}'))
    check(f"{p.name}: every posting form carries _csrf",
          len(re.findall(r'<form[^>]*method="post"', src)), src.count('name="_csrf"'))
# the status tag builds its key dynamically — those six keys must exist too
DYNAMIC = {f"pln.status.{s}" for s in
           ("on_time", "at_risk", "late", "unplanned", "no_capacity", "no_ship_date")}
keys |= DYNAMIC
check("dynamic pln.status.* keys accounted for", len(DYNAMIC & keys), 6)
(Path(REPO) / "app" / "planning" / "i18n_keys.txt").write_text(
    "\n".join(sorted(keys)) + "\n", encoding="utf-8")
print(f"  ..    {len(keys)} distinct i18n keys required (written to app/planning/i18n_keys.txt)")

# ---------------------------------------------------------------- 15
section(15, "route security audit (decorators, methods, id guards)")
src = (Path(REPO) / "app" / "routes" / "planning.py").read_text(encoding="utf-8")
tree = ast.parse(src)
routes = []
for node in ast.walk(tree):
    if not isinstance(node, ast.FunctionDef):
        continue
    decs = []
    for dec in node.decorator_list:
        call = dec if isinstance(dec, ast.Call) else None
        target = call.func if call else dec
        decs.append((getattr(target, "attr", None) or getattr(target, "id", None), call))
    names = [n for n, _ in decs]
    if "route" not in names:
        continue
    rt = [c for n, c in decs if n == "route"][0]
    methods = ["GET"]
    for kw in rt.keywords:
        if kw.arg == "methods":
            methods = [e.value for e in kw.value.elts]
    routes.append((node.name, rt.args[0].value, methods))
check("route count", len(routes), 13)
for fname, path, methods in routes:
    yes(f"{fname}: @login_required", "login_required" in src.split(f"def {fname}(")[0]
        .rsplit("@bp.route", 1)[1])
    yes(f"{fname}: @permission_required", "permission_required" in
        src.split(f"def {fname}(")[0].rsplit("@bp.route", 1)[1])
    if any(w in fname for w in ("create", "update", "delete", "set", "add", "whatif")):
        yes(f"{fname}: state-changing route is POST only", methods == ["POST"])
for fname in ("order_detail", "balance", "smv_set", "op_add"):
    body = src.split(f"def {fname}(")[1].split("\n@bp.route")[0]
    yes(f"{fname}: an unknown id 404s instead of being written to", "abort(404)" in body)

# ================================================================ PASS 2
# Round two: every case below CRASHED or silently lied before it was fixed.
def raises(label, fn):
    """The module must never propagate an exception to the request handler."""
    try:
        fn()
        print(f"  ok    {label} did not raise")
    except Exception as e:
        FAILS.append(f"{label}: RAISED {type(e).__name__}: {e}")
        print(f"  FAIL  {label}: RAISED {type(e).__name__}: {e}")


with app.app_context():
    from app.db import get_db
    from app.planning.schema import create_and_seed
    from app.planning import services as svc
    from app.planning.constants import MAX_PLAN_DAYS

    conn = get_db()
    create_and_seed(conn); conn.commit()
    TODAY = date.today()
    lid = conn.execute("SELECT id FROM pln_lines WHERE code='SEW-1'").fetchone()["id"]
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_name,qty,ship_date,status,"
                 "created_at) VALUES ('ADV-2','Adversary Ltd','Tee',10000,?,'confirmed','t')",
                 (str(TODAY + timedelta(days=60)),))
    conn.commit()
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='ADV-2'").fetchone()["id"]
    svc.set_smv(oid, 10.0)

    # ---------------------------------------------------------------- 16
    section(16, "'inf' / '1e400' / 'nan' are garbage, not quantities (was: 500)")
    check("_f('1e400') is not infinity", svc._f("1e400"), 0.0)
    check("_f('inf')", svc._f("inf"), 0.0)
    check("_f('-inf')", svc._f("-inf"), 0.0)
    check("_f('nan')", svc._f("nan"), 0.0)
    check("_f('1e400', default 100)", svc._f("1e400", 100.0), 100.0)
    check("_i('1e400')", svc._i("1e400"), 0)
    check("a real number still parses", svc._f("12.5"), 12.5)
    raises("board(days='1e400')", lambda: svc.board("1e400"))
    check("board horizon falls back to the default", svc.board("1e400")["days"], 14)
    raises("create_line(operators='1e400')",
           lambda: svc.create_line({"name": "Infinite line", "operators": "1e400",
                                    "working_minutes": "540", "efficiency_pct": "65"}, {}))
    check("an infinite operator count is stored as 0, so the line has no capacity",
          svc.daily_capacity_minutes(dict(conn.execute(
              "SELECT * FROM pln_lines WHERE name='Infinite line'").fetchone())), 0.0)
    check("create_allocation(qty='inf') is refused, not a crash",
          svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": "inf"}, {}),
          (False, "bad_qty"))
    # a typed-but-unparseable SMV override must be refused, NOT silently swapped for
    # the order's own SMV — that would price the plan at a number nobody typed
    check("create_allocation(smv='1e400') is refused",
          svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 10,
                                 "smv": "1e400"}, {}), (False, "no_smv"))
    check("create_allocation(smv='abc') is refused",
          svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 10,
                                 "smv": "abc"}, {}), (False, "no_smv"))
    check("what_if(smv='abc') is refused too",
          svc.what_if(oid, lid, str(TODAY), 10, "abc")["reason"], "no_smv")
    check("a BLANK override still falls back to the order SMV",
          svc.what_if(oid, lid, str(TODAY), 10, "")["smv"], 10.0)
    check("capacity from 1e308 operators is not infinite", svc.daily_capacity_minutes(
        {"operators": "1e308", "working_minutes": 540, "efficiency_pct": 100}), 0.0)

    # ---------------------------------------------------------------- 17
    section(17, "two finite numbers whose product overflows (was: 500)")
    check("required_minutes(1e200, 1e200) overflows to inf", math.isinf(
        svc.required_minutes(1e200, 1e200)), True)
    check("required_days(inf, cap) is None, never int(inf)",
          svc.required_days(float("inf"), 9828), None)
    check("create_allocation is refused, not crashed",
          svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 1e200,
                                 "smv": 1e200}, {}), (False, "unplannable"))
    check("what_if agrees with the commit",
          svc.what_if(oid, lid, str(TODAY), 1e200, 1e200)["reason"], "unplannable")
    raises("spread_minutes(inf)", lambda: svc.spread_minutes(TODAY, float("inf"), 9828))
    check("spread of an infinite requirement", svc.spread_minutes(TODAY, float("inf"), 9828), [])

    # ---------------------------------------------------------------- 18
    section(18, "an absurd capacity must not plan ZERO days / finish before it starts")
    check("required_days never returns 0 days for real work",
          svc.required_days(150000, float("inf")), None)
    check("a plan can never end before it starts",
          svc.plan_end_date(TODAY, 150000, float("inf")), None)
    check("one minute of work still takes one whole day",
          svc.required_days(0.0001, 1e9), 1)
    check("...and that day is the start date itself",
          svc.plan_end_date(TODAY, 0.0001, 1e9), str(TODAY))
    check("finish is never earlier than start over 40 capacity/requirement combinations",
          all(svc.plan_end_date(TODAY, m, c) is None or svc.plan_end_date(TODAY, m, c) >= str(TODAY)
              for m in (0.0001, 1, 150000, 1e18, 1e200) for c in (0.01, 1, 9828, 1e18, 1e200,
                                                                  float("inf"), -1, 0, None)), True)

    # ---------------------------------------------------------------- 19
    section(19, "a start date at the end of the calendar (was: 500)")
    check("plan_end_date near date.max", svc.plan_end_date("9999-12-31", 150000, 9828), None)
    check("spread_minutes near date.max", svc.spread_minutes("9999-12-31", 150000, 9828), [])
    check("the allocation is refused, not crashed",
          svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 12000,
                                 "start_date": "9999-12-31"}, {}), (False, "unplannable"))
    check("what_if near date.max", svc.what_if(oid, lid, "9999-12-31", 12000, 10)["reason"],
          "unplannable")
    check("a start date that still fits is planned normally",
          svc.plan_end_date("9999-12-01", 19656, 9828), "9999-12-02")

    # ---------------------------------------------------------------- 20
    section(20, "an out-of-range URL id must 404, not 500 (SQLite cannot bind > 2**63-1)")
    from app.routes.planning import _pk, _referrer_or
    HUGE = 99999999999999999999
    check("_pk clamps an out-of-range id", _pk(HUGE), 0)
    check("_pk clamps a negative id", _pk(-1), 0)
    check("_pk keeps a real id", _pk(7), 7)
    check("_pk keeps the largest bindable id", _pk(9223372036854775807), 9223372036854775807)
    raises("update_line(clamped)", lambda: svc.update_line(_pk(HUGE), {"operators": "5"}, {}))
    raises("delete_allocation(clamped)", lambda: svc.delete_allocation(_pk(HUGE)))
    raises("delete_op(clamped)", lambda: svc.delete_op(_pk(HUGE)))
    check("update_line on a missing line reports failure", svc.update_line(0, {"operators": "5"}, {}),
          False)
    with app.test_request_context("/planning", headers={"Referer": "https://evil.example/steal"}):
        check("an off-site Referer never becomes a redirect target",
              _referrer_or("/planning"), "/planning")
    with app.test_request_context("/planning", headers={"Referer": "http://localhost/planning/lines"}):
        check("a same-host Referer is honoured", _referrer_or("/planning"),
              "http://localhost/planning/lines")

    # ---------------------------------------------------------------- 21
    section(21, "an order with no status must not vanish from the plan")
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,ship_date,status,created_at) "
                 "VALUES ('ADV-NULLST','Adversary Ltd',500,?,NULL,'t')",
                 (str(TODAY + timedelta(days=20)),))
    conn.commit()
    check("NULL status is not the same as cancelled",
          any(o["order_no"] == "ADV-NULLST" for o in svc.list_orders()), True)
    check("a closed order is still excluded", any(
        o["status"] == "closed" for o in svc.list_orders()), False)
    conn.execute("UPDATE ord_orders SET status='closed' WHERE order_no='ADV-NULLST'")
    conn.commit()
    check("...and closing it does remove it",
          any(o["order_no"] == "ADV-NULLST" for o in svc.list_orders()), False)

    # ---------------------------------------------------------------- 22
    section(22, "deactivating a line must not hide the work already committed to it")
    # a private line, so only this scenario's own load is on it
    conn.execute("INSERT INTO pln_lines (code,name,operators,working_minutes,efficiency_pct,"
                 "active,created_at) VALUES ('RETIRE','Line to retire',10,500,100,1,'t')")
    conn.commit()
    rid = conn.execute("SELECT id FROM pln_lines WHERE code='RETIRE'").fetchone()["id"]
    ok, aid = svc.create_allocation({"order_id": oid, "pline_id": rid, "qty": 500,
                                     "start_date": str(TODAY)}, {})
    yes("fixture allocation on the private line", ok)
    loaded = sum(c["minutes"] for g in svc.board(14)["grid"] if g["line"]["id"] == rid
                 for c in g["cells"])
    check("the line carries 500 x 10 SMV of load while active", loaded, 5000.0)
    svc.update_line(rid, {"active": "0"}, {})
    row = [g for g in svc.board(14)["grid"] if g["line"]["id"] == rid]
    check("an inactive line with committed work stays on the board", len(row), 1)
    check("its minutes are still counted", sum(c["minutes"] for c in row[0]["cells"]), loaded)
    check("feasibility and the board agree it is planned",
          svc.feasibility(oid)["planned_qty"] > 0, True)
    svc.delete_allocation(aid)
    check("once un-planned, the retired line drops off the board",
          [g for g in svc.board(14)["grid"] if g["line"]["id"] == rid], [])
    svc.update_line(rid, {"active": "1"}, {})
    check("re-activating brings it back",
          len([g for g in svc.board(14)["grid"] if g["line"]["id"] == rid]), 1)
    svc.update_line(rid, {"active": "0"}, {})

    # ---------------------------------------------------------------- 23
    section(23, "over-planning an order must be visible, not clamped away")
    ok, a1 = svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 10000,
                                    "start_date": str(TODAY)}, {})
    ok2, a2 = svc.create_allocation({"order_id": oid, "pline_id": lid, "qty": 5000,
                                     "start_date": str(TODAY + timedelta(days=1))}, {})
    yes("two allocations committed", ok and ok2)
    f = svc.feasibility(oid)
    check("order quantity", f["order"]["qty"], 10000.0)
    check("planned quantity", f["planned_qty"], 15000.0)
    check("unplanned quantity still clamps at 0", f["unplanned_qty"], 0.0)
    check("the 5000-piece over-plan is reported", f["over_qty"], 5000.0)
    check("a fully-planned order reports no over-plan",
          (svc.delete_allocation(a2), svc.feasibility(oid)["over_qty"])[1], 0.0)
    check("the dashboard roll-up carries it too",
          "over_qty" in svc.dashboard(1)["orders"][0], True)
    svc.delete_allocation(a1)

    # ---------------------------------------------------------------- 24
    section(24, "the demo seed must never attach itself to REAL orders")
    conn.execute("DELETE FROM pln_order_smv")
    conn.execute("DELETE FROM pln_allocations")
    conn.execute("DELETE FROM pln_ops")
    conn.execute("UPDATE ord_orders SET created_by='ahmed'")   # nothing is demo any more
    conn.commit()
    bells = conn.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    create_and_seed(conn); conn.commit()
    check("no invented SMV on a real order", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_order_smv").fetchone()["n"], 0)
    check("no invented allocation on a real order", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_allocations").fetchone()["n"], 0)
    check("no invented operation bulletin", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_ops").fetchone()["n"], 0)
    check("seeding raised no bell alert for a plan nobody made", conn.execute(
        "SELECT COUNT(*) AS n FROM notifications").fetchone()["n"], bells)
    conn.execute("UPDATE ord_orders SET created_by='seed' WHERE order_no LIKE 'SO-10%'")
    conn.commit()
    create_and_seed(conn); conn.commit()
    check("the demo data does seed against the module's own demo orders", conn.execute(
        "SELECT COUNT(*) AS n FROM pln_order_smv").fetchone()["n"], 3)

    # ---------------------------------------------------------------- 25
    section(25, "an overload just above 100% must not round away")
    conn.execute("INSERT INTO pln_lines (code,name,operators,working_minutes,efficiency_pct,"
                 "active,created_at) VALUES ('ROUND','Rounding line',1,1000,100,1,'t')")
    conn.commit()
    rl = conn.execute("SELECT id FROM pln_lines WHERE code='ROUND'").fetchone()["id"]
    ro = conn.execute("SELECT id FROM ord_orders WHERE order_no='ADV-2'").fetchone()["id"]

    def day0(line_id):
        return [g for g in svc.board(2)["grid"] if g["line"]["id"] == line_id][0]["cells"][0]

    # capacity 1 x 1000 x 100% = 1000 min/day. First fill it EXACTLY: 5000 x 0.2 = 1000.
    svc.create_allocation({"order_id": ro, "pline_id": rl, "qty": 5000, "smv": 0.2,
                           "start_date": str(TODAY)}, {})
    check("exactly 100% is not an overload", (day0(rl)["pct"], day0(rl)["over"]), (100.0, False))
    # now overlap 4 x 0.1 = 0.4 more minutes: 1000.4 / 1000 = 100.04% -> rounds to 100.0%
    svc.create_allocation({"order_id": ro, "pline_id": rl, "qty": 4, "smv": 0.1,
                           "start_date": str(TODAY)}, {})
    check("the loaded minutes really exceed capacity", day0(rl)["minutes"], 1000.4)
    check("the rounded % hides it", day0(rl)["pct"], 100.0)
    check("the overload is flagged from the raw minutes anyway", day0(rl)["over"], True)

    # ---------------------------------------------------------------- 26
    section(26, "create_and_seed x3 again, after the seed guard changed")
    c1 = counts(conn)
    create_and_seed(conn); conn.commit()
    create_and_seed(conn); conn.commit()
    check("counts unchanged after three more seeds", counts(conn), c1)
    conn.close()

# ---------------------------------------------------------------- 27
section(27, "the new template strings declare their i18n keys")
NEW_KEYS = {"pln.kpi.over_qty", "pln.feas.over_hint", "pln.status.inactive"}
tsrc = "".join((Path(REPO) / "app" / "templates" / "planning" / n).read_text(encoding="utf-8")
               for n in ("order.html", "index.html"))
for k in sorted(NEW_KEYS):
    yes(f"{k} is used with data-i18n", f'data-i18n="{k}"' in tsrc)
MAP = Path(REPO) / "app" / "planning" / "i18n_map.json"
yes("app/planning/i18n_map.json exists (en/ar/tr for every key)", MAP.exists())
if MAP.exists():
    import json
    m = json.loads(MAP.read_text(encoding="utf-8"))
    need = set((Path(REPO) / "app" / "planning" / "i18n_keys.txt")
               .read_text(encoding="utf-8").split()) | NEW_KEYS
    for lang in ("en", "ar", "tr"):
        missing = sorted(need - set(m.get(lang, {})))
        check(f"{lang}: keys missing from the map", missing, [])
    # 'SMV' and '#' are the same token in Turkish — everything else must be translated
    SAME_OK = {"pln.field.smv", "pln.field.seq"}
    check("ar is not an English copy",
          sorted(k for k in need if m["ar"].get(k) == m["en"].get(k)), [])
    check("tr is not an English copy",
          sorted(k for k in need - SAME_OK if m["tr"].get(k) == m["en"].get(k)), [])

print("\n" + "=" * 62)
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("ALL ADVERSARIAL CHECKS PASSED")
