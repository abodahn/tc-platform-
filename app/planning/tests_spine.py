"""
Spine test for LANE 1 — one SMV source of truth, and plan vs actual.

Runs against a THROWAWAY database (never the repo's platform.db) and asserts,
with hand-calculated arithmetic stated inline:

  1  create_and_seed is idempotent three times over and never overwrites an edit
  2  smv_for gives the SAME answer to planning, costing, MES and people
  3  disagreeing stored SMVs are FLAGGED, never silently resolved
  4  a committed plan's SMV snapshot does not move when the canonical SMV changes
  5  plan vs actual matches hand arithmetic (pieces, efficiency, days variance)
  6  no MES history at all -> "no data", never 0%, never ZeroDivisionError
  7  efficiency stays 0..100 and unmanned work never inflates it
  8  every touched page is 200 for en/ar/tr and every data-i18n key resolves
  9  costing money figures are IDENTICAL before and after an SMV disagreement

    python app/planning/tests_spine.py
"""
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

REPO = r"D:\TC platform\tc-platform-render"
TMP = Path(tempfile.mkdtemp(prefix="wf_"))
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


def section(n, title):
    print(f"\n[{n}] {title}")


def check(label, got, want):
    if got != want:
        FAILS.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
    else:
        print(f"  ok    {label} = {got!r}")


def yes(label, cond):
    check(label, bool(cond), True)


def no_raise(label, fn):
    try:
        fn()
        print(f"  ok    {label} did not raise")
    except Exception as e:
        FAILS.append(f"{label}: RAISED {type(e).__name__}: {e}")
        print(f"  FAIL  {label}: RAISED {type(e).__name__}: {e}")


TODAY = date.today()
SLOTS = ["08:00-09:00", "09:00-10:00", "10:00-11:00", "11:00-12:00"]

with app.app_context():
    from app.db import get_db
    from app.planning.schema import create_and_seed
    from app.people.schema import create_and_seed as ppl_seed
    from app.planning import services as svc
    from app.costing import services as csvc
    from app.mes import services as msvc
    from app.people import services as psvc
    from app.services.smv import SMV_TOLERANCE, smv_for, source_of

    conn = get_db()

    # ============================================================== 1
    section(1, "create_and_seed x3: no duplicates, no overwritten edits")

    def counts():
        return tuple(conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"]
                     for t in ("pln_lines", "pln_order_smv", "pln_allocations", "pln_ops",
                               "ppl_piece_rate"))

    create_and_seed(conn); ppl_seed(conn); conn.commit()
    first = counts()
    conn.execute("UPDATE pln_lines SET name='RENAMED BY ADMIN' WHERE code='SEW-1'")
    conn.commit()
    create_and_seed(conn); ppl_seed(conn); conn.commit()
    create_and_seed(conn); ppl_seed(conn); conn.commit()
    check("row counts unchanged after 3 seeds", counts(), first)
    check("admin's edited line name survived re-seeding",
          conn.execute("SELECT name FROM pln_lines WHERE code='SEW-1'").fetchone()["name"],
          "RENAMED BY ADMIN")
    yes("pln_allocations.smv_source column exists",
        "smv_source" in [d[0] for d in conn.execute(
            "SELECT * FROM pln_allocations LIMIT 1").description])
    yes("ppl_piece_rate.smv_source column exists",
        "smv_source" in [d[0] for d in conn.execute(
            "SELECT * FROM ppl_piece_rate LIMIT 1").description])

    # -------------------------------------------------------------- fixtures
    # An order on style D-501, which the style master (sf_styles) defines at 24.5
    # and whose sf_operations bulletin sums to 24.6.
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_ref,style_name,qty,ship_date,"
                 "status,created_at) VALUES ('SPINE-1','Spine Ltd','D-501','Slim Denim',900,?,"
                 "'confirmed','t')", (str(TODAY + timedelta(days=30)),))
    conn.commit()
    O = conn.execute("SELECT id FROM ord_orders WHERE order_no='SPINE-1'").fetchone()["id"]

    # A capacity profile linked to a REAL production line, so the MES can book to it.
    PL = conn.execute("SELECT id FROM production_lines ORDER BY id").fetchone()["id"]
    conn.execute("INSERT INTO pln_lines (line_id,code,name,section,operators,working_minutes,"
                 "efficiency_pct,active,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (PL, "SPN-1", "Spine Line", "Sewing", 10, 600, 15.0, 1, "t"))
    conn.commit()
    L = conn.execute("SELECT id FROM pln_lines WHERE code='SPN-1'").fetchone()["id"]
    # capacity = 10 operators x 600 min x 15% = 900 minutes/day
    check("line capacity 10x600x15%", svc.daily_capacity_minutes(
        dict(conn.execute("SELECT * FROM pln_lines WHERE id=?", (L,)).fetchone())), 900.0)

    # ============================================================== 9a
    section("9a", "costing money figures BEFORE any SMV disagreement exists")
    csvc.save_sheet(O, {"smv": "8.0", "cm_rate": "0.05", "overhead_per_unit": "0.30",
                        "freight_per_unit": "0.10"}, {"username": "spine"})
    b1 = csvc.cost_sheet(O)
    money_before = (b1["cm_per_unit"], b1["estimate"], b1["actual"], b1["margin"],
                    b1["sheet"]["smv"])
    # CM/unit = SMV 8.0 x rate 0.05 = 0.40; 900 pcs -> CM 360.00, overhead 270.00,
    # freight 90.00 => estimate total 720.00 (no BOM lines, so material 0).
    check("cm_per_unit = 8.0 x 0.05", b1["cm_per_unit"], 0.4)
    check("estimate total = (0.40+0.30+0.10) x 900", b1["estimate"]["total"], 720.0)

    # ============================================================== 2 + 3
    section(2, "smv_for: one answer for planning, costing, MES and people")
    ok, why = svc.set_smv(O, 3.0)
    check("planning SMV set", (ok, why), (True, "ok"))
    r = smv_for(conn, order_id=O)
    check("canonical SMV = the planning value (rank 1)", r["smv"], 3.0)
    check("canonical source", r["source"], "planning")
    got = {s["source"]: s["smv"] for s in r["sources"]}
    check("costing source seen", got.get("costing"), 8.0)
    check("style master source seen", got.get("style"), 24.5)
    check("style bulletin source seen", got.get("style_ops"), 24.6)

    section(3, "a style whose stored SMVs disagree is FLAGGED, not resolved away")
    check("conflict raised", r["conflict"], True)
    # spread = style bulletin 24.6 - planning 3.0 = 21.6, far past the 0.5 tolerance
    check("spread high-low", r["spread"], 21.6)
    check("high source", r["high"]["source"], "style_ops")
    check("low source", r["low"]["source"], "planning")
    check("tolerance is a real deadband, not zero", SMV_TOLERANCE, 0.5)
    # 3.0 vs 3.4 is inside tolerance -> NOT a conflict
    from app.services.smv import compare
    check("0.4 apart is not a conflict",
          compare([{"source": "a", "smv": 3.0}, {"source": "b", "smv": 3.4}])["conflict"], False)
    check("0.6 apart IS a conflict",
          compare([{"source": "a", "smv": 3.0}, {"source": "b", "smv": 3.6}])["conflict"], True)

    # ============================================================== 9b
    section("9b", "costing money is IDENTICAL after the disagreement appears")
    b2 = csvc.cost_sheet(O)
    money_after = (b2["cm_per_unit"], b2["estimate"], b2["actual"], b2["margin"],
                   b2["sheet"]["smv"])
    check("cost sheet money unchanged", money_after, money_before)
    check("the costing sheet's own SMV was never rewritten", b2["sheet"]["smv"], 8.0)
    check("costing still prices from its own sheet, not the canonical",
          csvc.cm_unit(b2["sheet"]), 0.4)
    check("the disagreement is surfaced on the costing page", b2["smv_ref"]["conflict"], True)

    # ============================================================== 4
    section(4, "the committed-plan snapshot does not move when the SMV is edited")
    ok, aid = svc.create_allocation(
        {"order_id": O, "pline_id": L, "qty": 900, "start_date": str(TODAY - timedelta(days=3))},
        {"username": "spine"})
    check("allocation committed", ok, True)
    snap = dict(conn.execute("SELECT * FROM pln_allocations WHERE id=?", (aid,)).fetchone())
    check("snapshot took the SMV in force", snap["smv"], 3.0)
    check("snapshot recorded WHERE the number came from", snap["smv_source"], "planning")
    svc.set_smv(O, 9.0)
    check("canonical SMV really did change", smv_for(conn, order_id=O)["smv"], 9.0)
    check("the committed plan keeps its own SMV",
          conn.execute("SELECT smv FROM pln_allocations WHERE id=?", (aid,)).fetchone()["smv"],
          3.0)
    svc.set_smv(O, 3.0)          # put it back for the arithmetic below

    # ============================================================== 5
    section(5, "plan vs actual against hand-calculated arithmetic")
    # PLAN: 900 pcs x 3.0 SMV = 2700 minutes on a 900 min/day line = 3 days.
    # Started 3 days ago, so days -3, -2, -1 are COMPLETE: the plan owes all 900.
    # ACTUAL: 4 hours x 150 pcs = 600 pcs, all on 10 operators at SMV 3.0.
    #   earned      = 4 x 150 x 3.0            = 1800 man-minutes
    #   operator_min= 4 x 10 x 60              = 2400 man-minutes
    #   efficiency  = 1800 / 2400 x 100        = 75.0%
    #   variance    = 600 - 900                = -300 pcs
    #   pieces/day  = 900 / 3 days             = 300
    #   days behind = -300 / 300               = -1.0
    for i, slot in enumerate(SLOTS):
        conn.execute("INSERT INTO mes_hourly (line_id,order_id,work_date,hour_slot,target_qty,"
                     "actual_qty,reject_qty,operators,smv,created_by,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (PL, O, str(TODAY), slot, 200, 150, 0, 10, 3.0, "spine", "t"))
    conn.commit()
    f = svc.feasibility(O)
    check("actual pieces", f["actual"]["pieces"], 600)
    check("earned minutes 4x150x3.0", f["actual"]["earned_min"], 1800.0)
    check("operator minutes 4x10x60", f["actual"]["operator_min"], 2400.0)
    check("measured efficiency 1800/2400", f["actual"]["efficiency"], 75.0)
    check("expected to date (3 complete days of a 3-day plan)", f["vs"]["expected_qty"], 900.0)
    check("variance in pieces 600-900", f["vs"]["variance_qty"], -300.0)
    check("planned pieces per day 900/3", f["vs"]["pieces_per_day"], 300.0)
    check("variance in days -300/300", f["vs"]["variance_days"], -1.0)
    check("an order behind IS visibly behind", f["vs"]["status"], "behind")
    d = svc.dashboard()
    row = [o for o in d["orders"] if o["id"] == O][0]
    check("the dashboard says the same thing", row["vs"]["variance_days"], -1.0)
    check("dashboard KPI counts it behind", d["kpi"]["behind"] >= 1, True)
    check("dashboard KPI counts the SMV disagreement", d["kpi"]["smv_conflicts"] >= 1, True)

    section("5b", "measured efficiency is offered to the plan, never applied to it")
    ln = [x for x in svc.list_lines() if x["id"] == L][0]
    check("configured efficiency untouched by the measurement", ln["efficiency_pct"], 15.0)
    check("measured efficiency surfaced", ln["measured_efficiency"], 75.0)
    check("gap 75.0 - 15.0", ln["efficiency_gap"], 60.0)
    # 10 x 600 x 75% = 4500 minutes/day if the planner accepts the measurement
    check("capacity at the measured efficiency", ln["capacity_measured"], 4500.0)
    svc.update_line(L, {"efficiency_pct": 75.0}, {"username": "spine"})   # the planner accepts
    check("only the planner's own action changes the plan",
          [x for x in svc.list_lines() if x["id"] == L][0]["efficiency_pct"], 75.0)
    svc.update_line(L, {"efficiency_pct": 15.0}, {"username": "spine"})

    # ============================================================== 6
    section(6, "no actuals at all: 'no data', never 0%, never a divide by zero")
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_ref,qty,ship_date,status,"
                 "created_at) VALUES ('SPINE-2','Spine Ltd','NO-SUCH-STYLE',500,?,'confirmed','t')",
                 (str(TODAY + timedelta(days=40)),))
    conn.commit()
    O2 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SPINE-2'").fetchone()["id"]
    svc.set_smv(O2, 5.0)
    no_raise("feasibility on a brand-new order", lambda: svc.feasibility(O2))
    f2 = svc.feasibility(O2)
    check("brand-new order has no actuals", f2["actual"]["has_data"], False)
    check("efficiency is None, NOT 0%", f2["actual"]["efficiency"], None)
    check("status says so", f2["vs"]["status"], "no_actuals")
    check("no invented variance", f2["vs"]["variance_days"], None)
    check("a style nobody defined resolves to the planning value",
          smv_for(conn, order_id=O2)["smv"], 5.0)
    check("...and cannot conflict with itself", smv_for(conn, order_id=O2)["conflict"], False)
    check("an order with no SMV anywhere resolves to None",
          smv_for(conn, order_id=10 ** 9)["smv"], None)

    m0 = svc.measured([])
    check("empty roll-up has no data", m0["has_data"], False)
    check("empty roll-up efficiency is None", m0["efficiency"], None)
    no_raise("plan_vs_actual with zero operators / zero minutes / no allocations",
             lambda: svc.plan_vs_actual({"qty": 100}, [], m0))
    zero_line = {"operators": 0, "working_minutes": 0, "efficiency_pct": 0,
                 "qty": 100, "smv": 5, "start_date": str(TODAY - timedelta(days=2))}
    z = svc.plan_vs_actual({"qty": 100}, [zero_line],
                           svc.measured([{"actual_qty": 10, "operators": 2, "smv": 5,
                                          "work_date": str(TODAY)}]))
    check("zero-capacity allocation owes nothing", z["expected_qty"], 0.0)
    check("...and reports no days variance to divide by", z["variance_days"], None)

    section("6b", "a partial day is not a shortfall")
    # An allocation starting TODAY has no complete day behind it, so it owes 0 —
    # not one day's worth at 9am.
    part = {"operators": 10, "working_minutes": 600, "efficiency_pct": 15,
            "qty": 900, "smv": 3.0, "start_date": str(TODAY)}
    p = svc.plan_vs_actual({"qty": 900}, [part],
                           svc.measured([{"actual_qty": 50, "operators": 10, "smv": 3.0,
                                          "work_date": str(TODAY)}]))
    check("today owes nothing yet", p["expected_qty"], 0.0)
    # 50 pieces against an expectation of 0 is +50/300 = 0.17 days — inside the
    # half-day deadband, so it is reported as on plan rather than as fake news.
    check("...and 50 pieces on day one is 0.2 days ahead", p["variance_days"], 0.2)
    check("...which is inside the deadband, so: on plan", p["status"], "on_plan")

    # ============================================================== 7
    section(7, "efficiency stays 0..100 and unmanned work never inflates it")
    # 3 manned hours (30 pcs x 20 SMV each) + 1 hour with 0 operators producing 500:
    #   earned = 3 x 30 x 20 = 1800, operator_min = 3 x 10 x 60 = 1800 -> 100.0%
    # The unmanned hour's 500 pieces are counted as PIECES but earn nothing.
    rows = [{"actual_qty": 30, "operators": 10, "smv": 20.0, "work_date": str(TODAY)}] * 3
    rows = list(rows) + [{"actual_qty": 500, "operators": 0, "smv": 20.0,
                          "work_date": str(TODAY)}]
    m = svc.measured(rows)
    check("unmanned pieces still counted as output", m["pieces"], 590)
    # A rejected garment burned the line's minutes but cannot be shipped, so it
    # must not pay down the schedule. 900 booked with 300 rejected against a plan
    # that owes 900 is a FULL DAY behind, not 'on plan'.
    rej = [{"actual_qty": 300, "reject_qty": 100, "operators": 10, "smv": 3.0,
            "work_date": str(TODAY - timedelta(days=d))} for d in (3, 2, 1)]
    mr = svc.measured(rej)
    check("gross pieces booked", mr["pieces"], 900)
    check("rejects counted", mr["rejects"], 300)
    check("good = 900 - 300", mr["good"], 600)
    vr = svc.plan_vs_actual({}, [{"qty": 900, "smv": 3.0, "operators": 10,
                                  "working_minutes": 90, "efficiency_pct": 100,
                                  "start_date": str(TODAY - timedelta(days=3))}],
                            mr, today=TODAY)
    check("schedule progress is GOOD pieces, not gross", vr["produced_qty"], 600)
    check("300 rejected garments = 300 pieces short", vr["variance_qty"], -300.0)
    check("...which is a full day behind", vr["variance_days"], -1.0)
    check("and the order is reported BEHIND, not on plan", vr["status"], "behind")
    check("earned minutes ignore the unmanned hour", m["earned_min"], 1800.0)
    check("operator minutes ignore the unmanned hour", m["operator_min"], 1800.0)
    check("efficiency 1800/1800, NOT inflated by the unmanned 500", m["efficiency"], 100.0)
    hot = svc.measured([{"actual_qty": 60, "operators": 10, "smv": 20.0,
                         "work_date": str(TODAY)}])
    # 60 x 20 = 1200 earned over 10 x 60 = 600 man-minutes -> 200% raw
    check("a raw over-100 figure is reported honestly", hot["efficiency_raw"], 200.0)
    check("...but planning is handed a capped 100 (capacity above theory is not real)",
          hot["efficiency"], 100.0)
    neg = svc.measured([{"actual_qty": -5, "operators": 10, "smv": -20.0,
                         "work_date": str(TODAY)}])
    check("a negative SMV never produces a negative efficiency",
          0.0 <= (neg["efficiency"] if neg["efficiency"] is not None else 0.0) <= 100.0, True)

    section("7b", "the balance invariant still holds (5 SMV on 0 operators is not 600%)")
    b = svc.balance_metrics([{"name": "manned", "smv": 1.0, "operators": 1},
                             {"name": "unmanned", "smv": 5.0, "operators": 0}])
    check("line_efficiency stays 0..100", 0.0 <= b["line_efficiency"] <= 100.0, True)
    check("line_efficiency = 1.0 / (1 x 1.0) x 100", b["line_efficiency"], 100.0)

    # ============================================================== people
    section("7c", "the piece-rate snapshot records its source and pays the same")
    svc.add_op(O, {"name": "Attach pocket", "smv": 0.55, "operators": 2})
    try:
        emp = conn.execute("SELECT id FROM prob_employees ORDER BY id").fetchone()
    except Exception:
        conn.rollback()
        emp = None
    if emp:
        okp, pid = psvc.add_piece_rate(
            {"employee_id": emp["id"], "work_date": str(TODAY), "operation": "Attach pocket",
             "order_id": O, "pieces": 500, "smv": 0.55, "minutes_worked": 480}, {"username": "s"})
        check("piece rate recorded", okp, True)
        pr = dict(conn.execute("SELECT * FROM ppl_piece_rate WHERE id=?", (pid,)).fetchone())
        # earned = 500 x 0.55 = 275.0; efficiency = 275/480 x 100 = 57.29
        check("the payable is computed from the SNAPSHOT, unchanged", pr["earned_minutes"], 275.0)
        check("efficiency 275/480", pr["efficiency_pct"], 57.29)
        check("the snapshot's source was recorded", pr["smv_source"], "bulletin_op")
        check("the snapshot value itself is untouched", pr["smv"], 0.55)
    check("source_of labels an off-book number 'manual'",
          source_of(99.0, [{"source": "planning", "smv": 3.0}]), "manual")
    check("source_of matches within tolerance",
          source_of(3.2, [{"source": "planning", "smv": 3.0}]), "planning")

    # ============================================================== MES
    section("7d", "the MES hourly record surfaces an SMV that disagrees")
    conn.execute("UPDATE mes_hourly SET smv=24.5 WHERE line_id=? AND hour_slot=?",
                 (PL, SLOTS[0]))
    conn.commit()
    det = msvc.line_detail(PL, str(TODAY))
    hrs = {h["hour_slot"]: h for h in det["hours"]}
    check("the odd hour is flagged", hrs[SLOTS[0]]["smv_differs"], True)
    check("...against the canonical value", hrs[SLOTS[0]]["smv_canonical"], 3.0)
    check("an agreeing hour is not nagged about", hrs[SLOTS[1]]["smv_differs"], False)
    check("MES, planning and costing all read the same canonical number",
          {hrs[SLOTS[1]]["smv_canonical"], svc.feasibility(O)["smv"]["smv"],
           csvc.cost_sheet(O)["smv_ref"]["smv"], smv_for(conn, order_id=O)["smv"]}, {3.0})
    conn.execute("UPDATE mes_hourly SET smv=3.0 WHERE line_id=? AND hour_slot=?",
                 (PL, SLOTS[0]))
    conn.commit()

    conn.close()

# ============================================================== 8
section(8, "every touched page is 200 for en/ar/tr, and every key resolves")
TPLS = ([Path(REPO) / "app" / "templates" / "planning" / n
         for n in ("index.html", "order.html", "lines.html", "allocate.html", "balance.html")]
        + [Path(REPO) / "app" / "templates" / "costing" / "sheet.html",
           Path(REPO) / "app" / "templates" / "mes" / "line.html"])

from app.planning.i18n_keys import I18N as NEW          # noqa: E402
from app.services.smv import ORDER_SOURCES, OPERATION_SOURCES   # noqa: E402

keys = set()
for p in TPLS:
    src = p.read_text(encoding="utf-8")
    keys |= {k for k in re.findall(r'data-i18n="([^"]+)"', src) if "{{" not in k}
# Keys the templates build at render time. Every possible expansion must exist,
# because a missing one renders as the RAW KEY to every user, English included.
DYNAMIC = ({f"pln.status.{s}" for s in ("on_time", "at_risk", "late", "unplanned",
                                        "no_capacity", "no_ship_date", "ahead", "behind",
                                        "on_plan", "no_actuals")}
           | {k for _s, k, _l in ORDER_SOURCES + OPERATION_SOURCES}
           | {"smv.src.manual"})
keys |= DYNAMIC
print(f"  ..    {len(keys)} distinct keys across {len(TPLS)} templates")

i18n_dir = Path(REPO) / "app" / "static" / "i18n"
for lang, idx in (("en", 0), ("ar", 1), ("tr", 2)):
    have = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))
    missing = sorted(k for k in keys
                     if k not in have and not (NEW.get(k) and NEW[k][idx].strip()))
    check(f"{lang}: every data-i18n key resolves", missing, [])
pending = sorted(k for k in keys if k in NEW and k not in
                 json.loads((i18n_dir / "en.json").read_text(encoding="utf-8")))
print(f"  ..    {len(pending)} key(s) supplied by app/planning/i18n_keys.py for the "
      f"orchestrator to splice: {pending}")
check("every NEW key carries real en+ar+tr",
      sorted(k for k, v in NEW.items() if not (len(v) == 3 and all(str(x).strip() for x in v))),
      [])

with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    for lang in ("en", "ar", "tr"):
        with app.app_context():
            from app.db import get_db
            cx = get_db()
            cx.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
            cx.commit()
            oid = cx.execute("SELECT id FROM ord_orders WHERE order_no='SPINE-1'").fetchone()["id"]
            lid = cx.execute("SELECT id FROM production_lines ORDER BY id").fetchone()["id"]
            cx.close()
        for path in ("/planning/", "/planning/lines", f"/planning/orders/{oid}",
                     "/planning/allocate", f"/planning/balance/{oid}",
                     f"/costing/order/{oid}", f"/mes/line/{lid}"):
            r = c.get(path)          # RAW status: follow_redirects would hide a 302
            check(f"{lang} {path}", r.status_code, 200)
        body = c.get(f"/planning/orders/{oid}").get_data(as_text=True)
        yes(f"{lang}: the plan-vs-actual panel is on the order page",
            "pln.actual.title" in body)
        yes(f"{lang}: the SMV disagreement is on the order page", "smv.conflict" in body)

print("\n" + "=" * 62)
if FAILS:
    print(f"SPINE TEST FAILED — {len(FAILS)} failure(s):")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("SPINE TEST PASSED — all LANE 1 assertions green.")
