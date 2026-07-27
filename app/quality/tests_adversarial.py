"""
Adversarial test for the QMS module — run:  python app/quality/tests_adversarial.py

Written to BREAK the module, not to bless it: independent Z1.4 table, triple
create_and_seed, hostile form input, denominator/numerator mismatches, route
decorators, SQL parameterisation, template parsing, i18n key coverage.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(r"D:\TC platform\tc-platform-render")
TMP = Path(tempfile.mkdtemp(prefix="qcadv_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                   # noqa: E402
from app.quality import constants as C                       # noqa: E402

fails = []
n_ok = 0


def check(label, got, want):
    global n_ok
    if got != want:
        fails.append(f"{label}: got {got!r}, want {want!r}")
        print(f"  FAIL  {label}: got {got!r}, want {want!r}")
    else:
        n_ok += 1
        print(f"  ok  {label}: {got!r}")


def ctrue(label, cond, detail=""):
    check(label + (f" {detail}" if detail else ""), bool(cond), True)


# ============================================================ 1. AQL table, independently
# Transcribed by hand from ANSI/ASQ Z1.4 Table I + Table II-A (single sampling,
# NORMAL inspection, general inspection level II). Independent of constants.py.
print("== 1. AQL tables re-derived independently ==")
Z14_LETTER = [(8, "A"), (15, "B"), (25, "C"), (50, "D"), (90, "E"), (150, "F"),
              (280, "G"), (500, "H"), (1200, "J"), (3200, "K"), (10000, "L"),
              (35000, "M"), (150000, "N"), (500000, "P")]        # above -> Q
Z14_N = {"A": 2, "B": 3, "C": 5, "D": 8, "E": 13, "F": 20, "G": 32, "H": 50,
         "J": 80, "K": 125, "L": 200, "M": 315, "N": 500, "P": 800, "Q": 1250}
# Ac per AQL column; letters absent from a column carry an arrow in the standard.
Z14_AC = {
    1.0: {"G": 0, "H": 1, "J": 2, "K": 3, "L": 5, "M": 7, "N": 10, "P": 14, "Q": 21},
    1.5: {"F": 0, "G": 1, "H": 2, "J": 3, "K": 5, "L": 7, "M": 10, "N": 14, "P": 21},
    2.5: {"E": 0, "F": 1, "G": 2, "H": 3, "J": 5, "K": 7, "L": 10, "M": 14, "N": 21},
    4.0: {"D": 0, "E": 1, "F": 2, "G": 3, "H": 5, "J": 7, "K": 10, "L": 14, "M": 21},
}


def ref_letter(lot):
    for upper, L in Z14_LETTER:
        if lot <= upper:
            return L
    return "Q"


check("letter table matches Z1.4 for every band edge",
      [ref_letter(x) for x in (1, 8, 9, 15, 16, 25, 26, 50, 51, 90, 91, 150, 151, 280,
                               281, 500, 501, 1200, 1201, 3200, 3201, 10000, 10001,
                               35000, 35001, 150000, 150001, 500000, 500001, 10 ** 9)],
      [C.code_letter(x) for x in (1, 8, 9, 15, 16, 25, 26, 50, 51, 90, 91, 150, 151, 280,
                                  281, 500, 501, 1200, 1201, 3200, 3201, 10000, 10001,
                                  35000, 35001, 150000, 150001, 500000, 500001, 10 ** 9)])
check("sample sizes match Z1.4 Table II-A", C.SAMPLE_SIZE, Z14_N)
check("Ac columns match Z1.4 Table II-A", C.ACCEPT, Z14_AC)
check("sample size strictly increases with the code letter",
      [Z14_N[L] for L in C.CODE_LETTERS],
      sorted(Z14_N[L] for L in C.CODE_LETTERS))
# Textbook anchors from the brief.
check("lot 800 @2.5 -> J/80/Ac5/Re6",
      {k: C.aql_plan(800, 2.5)[k] for k in ("code_letter", "sample_size", "accept", "reject")},
      {"code_letter": "J", "sample_size": 80, "accept": 5, "reject": 6})
check("lot 2000 @2.5 -> K/125/Ac7/Re8",
      {k: C.aql_plan(2000, 2.5)[k] for k in ("code_letter", "sample_size", "accept", "reject")},
      {"code_letter": "K", "sample_size": 125, "accept": 7, "reject": 8})
check("lot 5000 @2.5 -> L/200/Ac10/Re11",
      {k: C.aql_plan(5000, 2.5)[k] for k in ("code_letter", "sample_size", "accept", "reject")},
      {"code_letter": "L", "sample_size": 200, "accept": 10, "reject": 11})
bad_re = [(lot, a) for lot in (1, 5, 60, 200, 800, 2000, 5000, 20000, 100000, 400000, 10 ** 7)
          for a in C.AQL_LEVELS if C.aql_plan(lot, a)["reject"] != C.aql_plan(lot, a)["accept"] + 1]
check("Re == Ac+1 for every lot x AQL", bad_re, [])
# Monotonicity of the RESOLVED plan: a bigger lot must never buy a smaller sample.
mono = []
for a in C.AQL_LEVELS:
    prev = 0
    for lot in (500, 1200, 3200, 10000, 35000, 150000, 500000, 10 ** 7):
        n = C.aql_plan(lot, a)["sample_size"]
        if n < prev:
            mono.append((a, lot, n, prev))
        prev = n
check("resolved sample size never shrinks as the lot grows", mono, [])

# ============================================================ 2. hostile scalar input
print("== 2. hostile input to the pure engine ==")
for bad in ("abc", "", None, float("nan"), float("inf"), 1e400, -1, 0):
    try:
        p = C.aql_plan(bad, 2.5)
        got = ("plan", p["sample_size"])
    except Exception as e:
        got = (type(e).__name__, str(e)[:40])
    print(f"      aql_plan(lot={bad!r}) -> {got}")



def raises(fn, *a):
    """The TYPE of failure matters: a controlled ValueError is caught by the route
    and flashed; an OverflowError/TypeError is a 500."""
    try:
        return ("ok", fn(*a))
    except Exception as e:
        return type(e).__name__


check("infinite lot size -> controlled ValueError, not OverflowError",
      raises(C.aql_plan, float("inf"), 2.5), "ValueError")
check("NaN lot size -> controlled ValueError", raises(C.aql_plan, float("nan"), 2.5), "ValueError")
check("free text lot size -> ValueError", raises(C.aql_plan, "abc", 2.5), "ValueError")
check("unsupported AQL -> ValueError", raises(C.aql_plan, 1000, 6.5), "ValueError")
check("blank lot clamps to 1", C.aql_plan("", 2.5)["sample_size"], 1)
check("negative lot clamps to 1", C.aql_plan(-50, 2.5)["sample_size"], 1)
check("lot 5 @2.5 -> sample capped at the lot (100% inspection)",
      C.aql_plan(5, 2.5)["sample_size"], 5)
check("lot 60 @1.0 walks the down-arrow to G/32/Ac0",
      {k: C.aql_plan(60, 1.0)[k] for k in ("code_letter", "sample_size", "accept")},
      {"code_letter": "G", "sample_size": 32, "accept": 0})
check("lot 600000 @4.0 walks the up-arrow to M/315/Ac21",
      {k: C.aql_plan(600000, 4.0)[k] for k in ("code_letter", "sample_size", "accept")},
      {"code_letter": "M", "sample_size": 315, "accept": 21})
check("verdict: defective == Ac accepts", C.verdict(10, 200, 200, 10), "pass")
check("verdict: defective == Re rejects", C.verdict(10, 200, 200, 11), "fail")
check("verdict: incomplete sample under Ac never accepts", C.verdict(10, 200, 12, 0), "pending")
check("verdict: incomplete sample over Ac still rejects", C.verdict(10, 200, 12, 11), "fail")

# ============================================================ 3. app + isolated DB
app = create_app()
with app.app_context():
    from app.db import get_db                                # noqa: E402
    from app.quality.schema import create_and_seed           # noqa: E402
    from app.quality import services as svc                  # noqa: E402

    conn = get_db()
    print("== 3. create_and_seed idempotency (3 consecutive runs) ==")
    counts = []
    for _ in range(3):
        create_and_seed(conn)
        conn.commit()
        counts.append((conn.execute("SELECT COUNT(*) c FROM qc_inspections").fetchone()["c"],
                       conn.execute("SELECT COUNT(*) c FROM qc_defects").fetchone()["c"]))
    check("3x create_and_seed does not duplicate or destroy", counts, [counts[0]] * 3)

    print("== 4. the seed obeys the module's own incomplete-sample invariant ==")
    rows = [dict(r) for r in conn.execute(
        "SELECT id,ref,lot_size,aql,sample_size,units_inspected,defective_units,accept_no,verdict "
        "FROM qc_inspections ORDER BY id").fetchall()]
    liars = [(r["ref"], r["units_inspected"], r["sample_size"], r["verdict"]) for r in rows
             if r["verdict"] == "pass" and r["units_inspected"] < r["sample_size"]]
    check("no seeded row 'passes' on an incomplete sample", liars, [])
    wrong = [(r["ref"], r["verdict"]) for r in rows
             if r["verdict"] != C.verdict(r["accept_no"], r["sample_size"],
                                          r["units_inspected"], r["defective_units"])]
    check("every seeded verdict recomputes to itself", wrong, [])
    frozen = [(r["ref"], r["sample_size"]) for r in rows
              if r["sample_size"] != C.aql_plan(r["lot_size"], r["aql"])["sample_size"]]
    check("every seeded plan matches the engine for its lot/AQL", frozen, [])

    print("== 5. metrics arithmetic, recomputed by hand ==")
    m = svc.metrics(200, 8, 30)
    check("DHU 30/200*100", m["dhu"], 15.0)
    check("defect rate 8/200*100", m["defect_rate"], 4.0)
    check("RFT (200-8)/200*100", m["rft"], 96.0)
    check("RFT is the complement of the defective rate",
          round(m["rft"] + m["defect_rate"], 6), 100.0)
    check("zero units -> no ZeroDivisionError", svc.metrics(0, 0, 5)["dhu"], 0.0)
    check("None units -> zeros", svc.metrics(None, None, None)["rft"], 0.0)
    # A rate can never exceed 100% / go below 0%: defectives above the units counted
    # is nonsense data, but the KPI must not print RFT -400%.
    bad = svc.metrics(10, 50, 3)
    ctrue("RFT never goes negative", bad["rft"] >= 0.0, f"(got {bad['rft']})")
    ctrue("defective rate never exceeds 100", bad["defect_rate"] <= 100.0,
          f"(got {bad['defect_rate']})")

    print("== 6. Pareto ==")
    P = svc.pareto([{"defect_type": "A", "qty": 1}, {"defect_type": "B", "qty": 1},
                    {"defect_type": "Cc", "qty": 1}])
    check("thirds still close at exactly 100", P[-1]["cum_pct"], 100.0)
    P2 = svc.pareto([{"defect_type": "A", "qty": 7}, {"defect_type": "A", "qty": 3},
                     {"defect_type": "B", "qty": 5}])
    check("same type aggregates then ranks", [(p["defect_type"], p["qty"]) for p in P2],
          [("A", 10.0), ("B", 5.0)])
    check("cum closes at 100 after aggregation", P2[-1]["cum_pct"], 100.0)
    check("pareto of nothing", svc.pareto([]), [])
    check("pareto of all-zero qty does not divide by zero",
          svc.pareto([{"defect_type": "A", "qty": 0}])[0]["pct"], 0.0)

    print("== 7. create_inspection with hostile form data ==")
    # These reach the service straight from request.form; the route only pre-checks
    # lot_size and aql. Every rejection must be a ValueError the route can flash,
    # never an uncaught 500 and never a corrupt row.
    def mk(**kw):
        d = {"lot_size": "5000", "aql": "2.5", "stage": "final"}
        d.update(kw)
        try:
            iid = svc.create_inspection(d, None)
            r = conn.execute("SELECT units_inspected,defective_units,order_id FROM "
                             "qc_inspections WHERE id=?", (iid,)).fetchone()
            return ("created", r["units_inspected"], r["defective_units"])
        except Exception as e:
            return (type(e).__name__, str(e))

    check("non-numeric units_inspected -> flashable ValueError, no row",
          mk(units_inspected="twenty"), ("ValueError", "bad_number"))
    check("defectives > units refused at creation too",
          mk(units_inspected="200", defective_units="500"),
          ("ValueError", "defectives_exceed_units"))
    check("negative counts refused at creation",
          mk(units_inspected="-10", defective_units="-3"), ("ValueError", "negative"))
    check("NaN counts refused at creation", mk(units_inspected="nan"), ("ValueError", "bad_number"))
    # inf passes every < / > guard. One inf lot SUMs into every roll-up: the whole
    # factory's DHU reads 0.0 and RFT reads nan (inf/inf), and the JSON export then
    # emits the literal NaN / Infinity, which is not valid JSON for any consumer.
    for bad_inf in ("inf", "-inf", "Infinity", "1e400"):
        check(f"an infinite units_inspected ({bad_inf}) is refused at creation",
              mk(units_inspected=bad_inf), ("ValueError", "bad_number"))
    check("an infinite defective_units is refused at creation",
          mk(units_inspected="200", defective_units="inf"), ("ValueError", "bad_number"))
    check("an infinite units_inspected is refused on update too",
          svc.record_result(1, "inf", "0", None), (False, "bad_number"))
    # ...and if one ever got in by hand, no roll-up may report nan
    m_inf = svc.metrics(float("inf"), 5, 10)
    check("metrics() on a hand-edited inf row returns zeros, never nan",
          (m_inf["dhu"], m_inf["rft"], m_inf["defect_rate"], m_inf["units"]),
          (0.0, 0.0, 0.0, 0.0))
    check("infinite lot refused at creation", mk(lot_size="inf")[0], "ValueError")
    check("unsupported AQL refused at creation", mk(aql="6.5")[0], "ValueError")
    check("off-list stage refused (the roll-ups group on it)",
          mk(stage="whenever")[:2], ("ValueError", "bad_stage"))

    # A hand-posted order_id that does not exist must not create a dangling link
    # (the register renders a link to orders.detail for it, and by_order drops it).
    iid = svc.create_inspection({"lot_size": "1000", "aql": "2.5", "stage": "final",
                                 "order_id": "999999"}, None)
    linked = conn.execute("SELECT order_id FROM qc_inspections WHERE id=?", (iid,)).fetchone()["order_id"]
    check("unknown order_id is not stored as a dangling link", linked, None)

    print("== 8. record_result guards ==")
    iid = svc.create_inspection({"lot_size": "5000", "aql": "2.5", "stage": "final"}, None)
    check("no counts -> pending", conn.execute("SELECT verdict FROM qc_inspections WHERE id=?",
                                               (iid,)).fetchone()["verdict"], "pending")
    check("frozen plan", tuple(conn.execute(
        "SELECT code_letter,sample_size,accept_no,reject_no FROM qc_inspections WHERE id=?",
        (iid,)).fetchone()), ("L", 200.0, 10.0, 11.0))
    check("text counts rejected", svc.record_result(iid, "ten", "1", None), (False, "bad_number"))
    check("negative rejected", svc.record_result(iid, "-1", "0", None), (False, "negative"))
    check("defectives > units rejected", svc.record_result(iid, "10", "11", None),
          (False, "defectives_exceed_units"))
    check("missing inspection rejected", svc.record_result(10 ** 9, "10", "1", None),
          (False, "not_found"))
    check("complete sample, 10 defective (= Ac) -> pass", svc.record_result(iid, "200", "10", None),
          (True, "pass"))
    check("complete sample, 11 defective (= Re) -> fail", svc.record_result(iid, "200", "11", None),
          (True, "fail"))
    n_bell = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                          "AND severity='critical'").fetchone()["c"]
    svc.record_result(iid, "200", "12", None)
    check("a failed lot bells exactly once",
          conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                       "AND severity='critical'").fetchone()["c"], n_bell)
    check("short sample with 0 defective stays pending", svc.record_result(iid, "12", "0", None),
          (True, "pending"))
    check("short sample already over Ac still fails", svc.record_result(iid, "12", "11", None),
          (True, "fail"))

    print("== 9. add_defect guards ==")
    check("zero qty refused", svc.add_defect(iid, {"defect_type": "Open seam", "qty": "0"}, None),
          (False, "bad_qty"))
    check("negative qty refused", svc.add_defect(iid, {"defect_type": "Open seam", "qty": "-4"}, None),
          (False, "bad_qty"))
    check("text qty refused", svc.add_defect(iid, {"defect_type": "Open seam", "qty": "x"}, None),
          (False, "bad_qty"))
    check("infinite qty refused (it would make DHU infinite)",
          svc.add_defect(iid, {"defect_type": "Open seam", "qty": "inf"}, None), (False, "bad_qty"))
    check("NaN qty refused",
          svc.add_defect(iid, {"defect_type": "Open seam", "qty": "nan"}, None), (False, "bad_qty"))
    check("blank type refused", svc.add_defect(iid, {"defect_type": "   ", "qty": "1"}, None),
          (False, "no_type"))
    check("missing type key refused", svc.add_defect(iid, {"qty": "1"}, None), (False, "no_type"))
    check("unknown inspection refused",
          svc.add_defect(10 ** 9, {"defect_type": "Open seam", "qty": "1"}, None),
          (False, "not_found"))
    check("free-text defect type refused (the Pareto is a picklist)",
          svc.add_defect(iid, {"defect_type": "whatever I typed", "qty": "1"}, None),
          (False, "no_type"))
    check("off-list section refused",
          svc.add_defect(iid, {"defect_type": "Open seam", "section": "moon", "qty": "1"}, None),
          (False, "bad_section"))
    check("off-list severity refused",
          svc.add_defect(iid, {"defect_type": "Open seam", "severity": "apocalyptic",
                               "qty": "1"}, None), (False, "bad_severity"))
    check("picklist type accepted",
          svc.add_defect(iid, {"defect_type": "Open seam", "section": "sewing", "qty": "2"},
                         None)[0], True)

    print("== 10. roll-up numerator / denominator agree ==")
    # An inspection with defect lines but no counted units: its defects must not
    # land in a section's DHU numerator while its units are excluded from the
    # denominator, or the section reads infinitely bad and bells a false alarm.
    ghost = svc.create_inspection({"lot_size": "1000", "aql": "2.5", "stage": "cutting"}, None)
    before = {s["section"]: s["dhu"] for s in svc.by_section()}
    for _ in range(40):
        svc.add_defect(ghost, {"defect_type": "Mis-cut panel", "section": "cutting", "qty": "5"}, None)
    after = {s["section"]: s["dhu"] for s in svc.by_section()}
    check("40 defects on an UNCOUNTED lot do not move a section's DHU",
          after.get("cutting"), before.get("cutting"))
    secs = svc.by_section()
    dhu_hand = []
    for s in secs:
        u = conn.execute("SELECT COALESCE(SUM(units_inspected),0) c FROM qc_inspections "
                         "WHERE stage=? AND units_inspected>0", (s["section"],)).fetchone()["c"]
        dfx = conn.execute("SELECT COALESCE(SUM(d.qty),0) c FROM qc_defects d "
                           "JOIN qc_inspections i ON i.id=d.inspection_id "
                           "WHERE i.stage=? AND i.units_inspected>0", (s["section"],)).fetchone()["c"]
        dhu_hand.append((s["section"], round(float(dfx) / float(u) * 100, 2) if u else 0.0))
    check("by_section DHU equals the hand calculation",
          [(s["section"], s["dhu"]) for s in secs], dhu_hand)
    for s in secs:
        ctrue(f"section {s['section']} RFT within 0..100", 0.0 <= s["rft"] <= 100.0, f"({s['rft']})")
    for o in svc.by_order():
        ctrue(f"order {o['order_no']} RFT within 0..100", 0.0 <= o["rft"] <= 100.0, f"({o['rft']})")

    d = svc.dashboard()
    ctrue("dashboard DHU within sanity", d["dhu"] >= 0.0, f"({d['dhu']})")
    ctrue("dashboard RFT within 0..100", 0.0 <= d["rft"] <= 100.0, f"({d['rft']})")
    ctrue("dashboard defect rate <= 100", d["defect_rate"] <= 100.0, f"({d['defect_rate']})")
    check("pass rate is over decided lots only",
          d["pass_rate"], round(d["passed"] / (d["passed"] + d["failed"]) * 100, 1)
          if (d["passed"] + d["failed"]) else 0.0)
    ctrue("DHU >= defective rate (a unit can carry several defects)",
          d["dhu"] >= d["defect_rate"], f"({d['dhu']} vs {d['defect_rate']})")

    print("== 11. filters and the sweep ==")
    check("list filter by verdict", {r["verdict"] for r in svc.list_inspections(vrd="fail")},
          {"fail"})
    check("SQL-injection string in the stage filter returns nothing, does not execute",
          svc.list_inspections(stage="x'; DROP TABLE qc_inspections; --"), [])
    check("qc_inspections survived the injection attempt",
          conn.execute("SELECT COUNT(*) c FROM qc_inspections").fetchone()["c"] > 0, True)
    check("bad order_id filter does not explode",
          isinstance(svc.list_inspections(order_id="abc"), list), True)
    svc.dhu_sweep()
    warn1 = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                         "AND severity='warning'").fetchone()["c"]
    svc.dhu_sweep()
    warn2 = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                         "AND severity='warning'").fetchone()["c"]
    check("the DHU sweep does not re-bell the same section the same day", warn2, warn1)

    # The dedup must key off the SAME clock the bell is written with, not the
    # local date: in any timezone ahead of UTC the two disagree for the first
    # hours of the local day and the sweep re-belled on every dashboard load.
    # Pin the clock away from today so this cannot pass by accident of the date.
    def _warns():
        return conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                            "AND severity='warning'").fetchone()["c"]

    # `created_at >= today` is an open-ended range, so the pinned day must sit in
    # the PAST of the real clock for this to discriminate: a sweep that dedups off
    # date.today() then looks for rows dated 2026+ and never sees its own 2019 row.
    _real_now = svc._now
    try:
        conn.execute("DELETE FROM notifications WHERE module='quality' AND severity='warning'")
        conn.commit()
        svc._now = lambda: "2019-03-04 22:45:00"     # UTC still the 4th, local already the 5th
        svc.dhu_sweep()
        svc.dhu_sweep()
        svc.dhu_sweep()
        day1 = _warns()
        ctrue("a pinned-clock sweep bells the over-limit section", day1 > 0, f"(got {day1})")
        check("three sweeps on one pinned UTC day bell that section exactly once", day1, 1)
        svc._now = lambda: "2019-03-05 00:05:00"     # next UTC day, minutes later
        svc.dhu_sweep()
        svc.dhu_sweep()
        check("the next UTC day bells again (dedup is per-day, not forever)", _warns(), day1 + 1)
    finally:
        svc._now = _real_now
    conn.close()

# ============================================================ 12. routes
print("== 12. route security ==")
import inspect                                               # noqa: E402
from app.routes import quality as qroutes                    # noqa: E402

src = inspect.getsource(qroutes)
blocks = re.findall(r"@bp\.route\((.*?)\)\n((?:@\w+.*\n)*)def (\w+)", src)
undecorated = [name for args, decs, name in blocks
               if "login_required" not in decs or "permission_required" not in decs]
check("every route is @login_required + @permission_required", undecorated, [])
unsafe = [name for args, decs, name in blocks
          if ("result" in name or "create" in name or "defect" in name) and "POST" not in args]
check("every state-changing route is POST-only", unsafe, [])
check("no f-string SQL in services",
      re.findall(r'f"[^"]*(?:INSERT|UPDATE|DELETE|WHERE)[^"]*\{',
                 (REPO / "app/quality/services.py").read_text(encoding="utf-8")), [])

# ============================================================ 13. templates
print("== 13. templates ==")
from jinja2 import Environment, FileSystemLoader             # noqa: E402

env = Environment(loader=FileSystemLoader(str(REPO / "app/templates")))
tpl_dir = REPO / "app/templates/quality"
keys = set()
for f in sorted(tpl_dir.glob("*.html")):
    s = f.read_text(encoding="utf-8")
    try:
        env.parse(s)
        print(f"  ok  {f.name} parses")
        n_ok += 1
    except Exception as e:
        fails.append(f"{f.name} parse: {e}")
        print(f"  FAIL  {f.name}: {e}")
    keys |= set(re.findall(r'data-i18n="([^"]+)"', s))
    for tok in re.findall(r'\{\{\s*csrf_token', s):
        pass
    forms = re.findall(r'<form[^>]*method="post"[^>]*>(.*?)</form>', s, re.S | re.I)
    for body in forms:
        if "_csrf" not in body:
            fails.append(f"{f.name}: a POST form has no _csrf")
            print(f"  FAIL  {f.name}: POST form without _csrf")
check("every data-i18n key uses the qc. prefix", sorted(k for k in keys if not k.startswith("qc.")), [])
print(f"  ..  {len(keys)} i18n keys used by the templates")

from app.quality.i18n_keys import I18N, UNTRANSLATED         # noqa: E402
check("every template key has a translation", sorted(keys - set(I18N)), [])
check("no unused translations shipped", sorted(set(I18N) - keys), [])
bad_tr = sorted(k for k, v in I18N.items()
                if len(v) != 3 or not all(str(x).strip() for x in v))
check("every translation has en/ar/tr, all non-empty", bad_tr, [])
same = sorted(k for k, v in I18N.items()
              if k not in UNTRANSLATED and (v[0] == v[1] or v[0] == v[2]))
check("ar/tr are not English copies", same, [])

# ============================================================ 14. real HTTP smoke
# A template that PARSES can still 500 at render time (an undefined filter, a
# url_for that does not resolve), so drive every route through the test client.
print("== 14. every route rendered for real ==")
app2 = create_app()                                           # create_app() already registers qroutes.bp
with app2.app_context():
    from app.db import get_db as _gdb                         # noqa: E402
    c = _gdb()
    admin = c.execute("SELECT id, session_epoch FROM users WHERE username='admin'").fetchone()
    iid = c.execute("SELECT id FROM qc_inspections ORDER BY id LIMIT 1").fetchone()["id"]
    c.close()

cl = app2.test_client()
with cl.session_transaction() as s:
    s["uid"] = admin["id"]
    s["ep"] = admin["session_epoch"] or 0
    s["_csrf_token"] = "T" * 64
CSRF = {"_csrf": "T" * 64}
check("GET /quality", cl.get("/quality/").status_code, 200)
check("GET /quality/inspections", cl.get("/quality/inspections").status_code, 200)
check("GET /quality/inspections filtered",
      cl.get("/quality/inspections?order_id=abc&stage=final&verdict=fail").status_code, 200)
check("GET /quality/inspections/new", cl.get("/quality/inspections/new").status_code, 200)
check("GET a real inspection", cl.get(f"/quality/inspections/{iid}").status_code, 200)
check("GET a missing inspection -> 404", cl.get("/quality/inspections/999999").status_code, 404)
check("POST create with junk lot size does not 500",
      cl.post("/quality/inspections", data=dict(CSRF, lot_size="abc", aql="2.5")).status_code, 302)
check("POST create with an infinite lot does not 500",
      cl.post("/quality/inspections", data=dict(CSRF, lot_size="inf", aql="2.5")).status_code, 302)
check("POST create with junk counts does not 500",
      cl.post("/quality/inspections", data=dict(CSRF, lot_size="5000", aql="2.5",
                                                units_inspected="lots")).status_code, 302)
check("POST create with defectives > units does not 500",
      cl.post("/quality/inspections", data=dict(CSRF, lot_size="5000", aql="2.5",
                                                units_inspected="10",
                                                defective_units="90")).status_code, 302)
check("POST a valid inspection",
      cl.post("/quality/inspections", data=dict(CSRF, lot_size="5000", aql="2.5",
                                                stage="final")).status_code, 302)
check("POST result with junk does not 500",
      cl.post(f"/quality/inspections/{iid}/result",
              data=dict(CSRF, units_inspected="x", defective_units="y")).status_code, 302)
check("POST defect with an off-list type does not 500",
      cl.post(f"/quality/inspections/{iid}/defect",
              data=dict(CSRF, defect_type="<script>", qty="1")).status_code, 302)
check("POST without a CSRF token is refused",
      cl.post("/quality/inspections", data={"lot_size": "5000"},
              headers={"Accept": "application/json"}).status_code, 400)
check("dashboard still renders after all that", cl.get("/quality/").status_code, 200)

print()
print(f"{n_ok} checks passed, {len(fails)} failed")
for f in fails:
    print("  !! " + f)
sys.exit(1 if fails else 0)
