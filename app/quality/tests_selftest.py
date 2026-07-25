"""
Self-test for the QMS module — run directly:  python app/quality/tests_selftest.py

Drives the AQL engine against published Z1.4 values, then the service layer
against an isolated throwaway database (Config.DB_PATH is redirected so the
repo's platform.db is never touched).
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="qc_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                              # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                 # noqa: E402
from app.quality import constants as C                     # noqa: E402
from app.quality.constants import aql_plan                 # noqa: E402

ok = 0


def check(label, got, want):
    global ok
    assert got == want, f"FAIL {label}: got {got!r}, want {want!r}"
    ok += 1
    print(f"  ok  {label}: {got!r}")


# ---------------------------------------------------------------- AQL engine
print("== AQL engine (ANSI/ASQ Z1.4, general level II, single, normal) ==")
# The three textbook anchors from the brief.
p = aql_plan(800, 2.5)
check("lot 800 -> code/sample", (p["code_letter"], p["sample_size"]), ("J", 80))
check("lot 800 @2.5 -> Ac/Re", (p["accept"], p["reject"]), (5, 6))
p = aql_plan(2000, 2.5)
check("lot 2000 -> code/sample", (p["code_letter"], p["sample_size"]), ("K", 125))
check("lot 2000 @2.5 -> Ac/Re", (p["accept"], p["reject"]), (7, 8))
p = aql_plan(5000, 2.5)
check("lot 5000 -> code/sample", (p["code_letter"], p["sample_size"]), ("L", 200))
check("lot 5000 @2.5 -> Ac/Re", (p["accept"], p["reject"]), (10, 11))

# Range boundaries — every lot in the band must give the same plan.
for lot in (501, 1200):
    check(f"lot {lot} still code J", aql_plan(lot, 2.5)["code_letter"], "J")
for lot in (1201, 3200):
    check(f"lot {lot} still code K", aql_plan(lot, 2.5)["code_letter"], "K")
for lot in (3201, 10000):
    check(f"lot {lot} still code L", aql_plan(lot, 2.5)["code_letter"], "L")
check("lot 500001 -> code Q", C.code_letter(500001), "Q")
check("lot 10_000_000 -> code Q", C.code_letter(10_000_000), "Q")
check("lot 2 -> code A", C.code_letter(2), "A")

# Other AQLs on a known row (code L, n=200).
check("lot 5000 @1.0", (aql_plan(5000, 1.0)["sample_size"], aql_plan(5000, 1.0)["accept"]), (200, 5))
check("lot 5000 @1.5", (aql_plan(5000, 1.5)["sample_size"], aql_plan(5000, 1.5)["accept"]), (200, 7))
check("lot 5000 @4.0", (aql_plan(5000, 4.0)["sample_size"], aql_plan(5000, 4.0)["accept"]), (200, 14))

# Arrow resolution. Down-arrow: lot 200 (code G, n=32) is too small to
# discriminate at AQL 0.65... at 1.0 code G IS in the band (Ac 0), but lot 60
# (code E, n=13) at AQL 1.0 is an arrow -> first plan below = G, n=32, Ac 0.
p = aql_plan(60, 1.0)
check("lot 60 @1.0 arrow-down -> G/32/Ac0", (p["code_letter"], p["sample_size"], p["accept"]), ("G", 32, 0))
check("lot 200 @1.0 -> G/32/Ac0", (aql_plan(200, 1.0)["code_letter"], aql_plan(200, 1.0)["accept"]), ("G", 0))
# Up-arrow: the plan saturates at Ac 21, so a huge lot at AQL 4.0 falls back to M.
p = aql_plan(600000, 4.0)
check("lot 600000 @4.0 arrow-up -> M/315/Ac21", (p["code_letter"], p["sample_size"], p["accept"]), ("M", 315, 21))
p = aql_plan(600000, 1.0)
check("lot 600000 @1.0 -> Q/1250/Ac21", (p["code_letter"], p["sample_size"], p["accept"]), ("Q", 1250, 21))

# 100% inspection rule: sample >= lot.
p = aql_plan(5, 2.5)
check("lot 5 @2.5 -> sample capped at the lot (100%)", p["sample_size"], 5)
check("lot 0 clamps to 1", (aql_plan(0, 2.5)["lot_size"], aql_plan(0, 2.5)["sample_size"]), (1, 1))
check("lot -50 clamps to 1", aql_plan(-50, 2.5)["lot_size"], 1)
try:
    aql_plan(1000, 6.5)
    raise AssertionError("FAIL: unsupported AQL should raise")
except ValueError:
    ok += 1
    print("  ok  unsupported AQL 6.5 raises ValueError")

# Whole-table internal consistency.
bad = [(a, L) for a, col in C.ACCEPT.items() for L in col if L not in C.SAMPLE_SIZE]
check("every plan letter has a sample size", bad, [])
for aql, col in C.ACCEPT.items():
    letters = [L for L in C.CODE_LETTERS if L in col]
    span = C.CODE_LETTERS[C.CODE_LETTERS.index(letters[0]):C.CODE_LETTERS.index(letters[-1]) + 1]
    check(f"AQL {aql} band is contiguous", letters, span)
    acs = [col[L] for L in letters]
    check(f"AQL {aql} Ac non-decreasing", acs, sorted(acs))
for lot in (2, 9, 30, 100, 400, 900, 2000, 5000, 20000, 90000, 300000, 900000):
    for aql in C.AQL_LEVELS:
        pl = aql_plan(lot, aql)
        assert pl["reject"] == pl["accept"] + 1, f"Re != Ac+1 at lot {lot} aql {aql}"
        assert pl["sample_size"] >= 1
ok += 1
print("  ok  Re == Ac + 1 across every lot x AQL combination")

# Verdicts.
check("13 defective vs Ac 10 -> fail", aql_plan(5000, 2.5, 13)["verdict"], "fail")
check("10 defective vs Ac 10 -> pass", aql_plan(5000, 2.5, 10)["verdict"], "pass")
check("11 defective vs Ac 10 (= Re) -> fail", aql_plan(5000, 2.5, 11)["verdict"], "fail")
check("0 defective -> pass", aql_plan(5000, 2.5, 0)["verdict"], "pass")

# ------------------------------------------------------------ metrics maths
print("== DHU / RFT / Pareto ==")
from app.quality import services as svc                    # noqa: E402

m = svc.metrics(200, 8, 30)
check("DHU 30 defects / 200 units", m["dhu"], 15.0)
check("defective rate 8/200", m["defect_rate"], 4.0)
check("RFT (200-8)/200", m["rft"], 96.0)
check("zero units -> no ZeroDivisionError", svc.metrics(0, 0, 5)["dhu"], 0.0)
check("None units -> zeros", svc.metrics(None, None, None)["rft"], 0.0)
check("125 units, 12 defects -> DHU 9.6", svc.metrics(125, 9, 12)["dhu"], 9.6)

par = svc.pareto([{"defect_type": "Open seam", "qty": 6}, {"defect_type": "Broken stitch", "qty": 3},
                  {"defect_type": "Open seam", "qty": 4}, {"defect_type": "Shading", "qty": 2},
                  {"defect_type": "Uneven hem", "qty": 5}])
check("pareto ranks by qty desc", [p["defect_type"] for p in par],
      ["Open seam", "Uneven hem", "Broken stitch", "Shading"])
check("pareto top qty aggregated", par[0]["qty"], 10.0)
check("pareto top pct 10/20", par[0]["pct"], 50.0)
check("pareto cum after 2 rows", par[1]["cum_pct"], 75.0)
check("pareto cum closes at 100", par[-1]["cum_pct"], 100.0)
check("pareto of nothing", svc.pareto([]), [])
check("pareto with all-zero qty", svc.pareto([{"defect_type": "X", "qty": 0}])[0]["cum_pct"], 0.0)

# ------------------------------------------------------------ service layer
print("== services against an isolated DB ==")
app = create_app()
with app.app_context():
    from app.db import get_db
    conn = get_db()
    from app.quality.schema import create_and_seed
    create_and_seed(conn)
    conn.commit()

    check("seed wrote demo inspections",
          conn.execute("SELECT COUNT(*) c FROM qc_inspections").fetchone()["c"], 6)
    check("seed wrote demo defects",
          conn.execute("SELECT COUNT(*) c FROM qc_defects").fetchone()["c"], 17)
    check("seed is idempotent (2nd run adds nothing)",
          (create_and_seed(conn),
           conn.execute("SELECT COUNT(*) c FROM qc_inspections").fetchone()["c"])[1], 6)
    check("seed verdicts",
          [r["verdict"] for r in conn.execute(
              "SELECT verdict FROM qc_inspections ORDER BY id").fetchall()],
          ["pass", "fail", "pass", "fail", "pass", "pending"])

    user = {"username": "selftest"}
    oid = conn.execute("SELECT id FROM ord_orders ORDER BY id").fetchone()["id"]

    # happy path: create pending -> record a passing result
    iid = svc.create_inspection({"order_id": str(oid), "stage": "final", "lot_size": "5000",
                                 "aql": "2.5", "inspector": "Selftest"}, user)
    got = svc.get_inspection(iid)
    check("created plan frozen on the row",
          (got["inspection"]["code_letter"], got["inspection"]["sample_size"],
           got["inspection"]["accept_no"], got["inspection"]["reject_no"]), ("L", 200.0, 10.0, 11.0))
    check("no counts yet -> pending", got["inspection"]["verdict"], "pending")
    check("record 200/4 -> pass", svc.record_result(iid, 200, 4, user), (True, "pass"))
    check("record 200/11 -> fail", svc.record_result(iid, 200, 11, user), (True, "fail"))
    check("failed lot raised a bell",
          conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                       "AND severity='critical'").fetchone()["c"], 1)
    check("fail bell fires once", (svc.record_result(iid, 200, 12, user),
                                   conn.execute("SELECT COUNT(*) c FROM notifications "
                                                "WHERE module='quality' AND severity='critical'"
                                                ).fetchone()["c"])[1], 1)

    # the incomplete-sample invariant: a short sample may reject, never accept
    check("short sample, 0 defective -> pending", svc.record_result(iid, 12, 0, user), (True, "pending"))
    check("short sample, 11 defective -> fail", svc.record_result(iid, 12, 11, user), (True, "fail"))

    # negative cases on the result
    check("negative units rejected", svc.record_result(iid, -1, 0, user), (False, "negative"))
    check("negative defectives rejected", svc.record_result(iid, 10, -3, user), (False, "negative"))
    check("defectives > units rejected", svc.record_result(iid, 10, 11, user),
          (False, "defectives_exceed_units"))
    check("non-numeric rejected", svc.record_result(iid, "abc", 1, user), (False, "bad_number"))
    check("missing inspection rejected", svc.record_result(999999, 1, 0, user), (False, "not_found"))

    # defect lines
    svc.record_result(iid, 200, 6, user)
    check("add defect", svc.add_defect(iid, {"defect_type": "Open seam", "section": "sewing",
                                             "qty": "9", "severity": "critical"}, user), (True, "added"))
    svc.add_defect(iid, {"defect_type": "Loose thread", "section": "finishing", "qty": "3"}, user)
    check("zero qty rejected", svc.add_defect(iid, {"defect_type": "Open seam", "qty": "0"}, user),
          (False, "bad_qty"))
    check("negative qty rejected", svc.add_defect(iid, {"defect_type": "Open seam", "qty": "-4"}, user),
          (False, "bad_qty"))
    check("non-numeric qty rejected", svc.add_defect(iid, {"defect_type": "X", "qty": "many"}, user),
          (False, "bad_qty"))
    check("blank defect type rejected", svc.add_defect(iid, {"defect_type": "  ", "qty": "1"}, user),
          (False, "no_type"))
    check("defect on a missing inspection rejected",
          svc.add_defect(999999, {"defect_type": "Open seam", "qty": "1"}, user), (False, "not_found"))

    got = svc.get_inspection(iid)
    # 12 defects over 200 units = DHU 6.0; 6 defective units = 3% defective, RFT 97%
    check("detail DHU", got["m"]["dhu"], 6.0)
    check("detail defective rate", got["m"]["defect_rate"], 3.0)
    check("detail RFT", got["m"]["rft"], 97.0)
    check("detail pareto cum closes at 100", got["pareto"][-1]["cum_pct"], 100.0)
    check("defect qty never touched defective_units", got["inspection"]["defective_units"], 6.0)

    # filters
    check("filter by verdict=fail returns only fails",
          {r["verdict"] for r in svc.list_inspections(vrd="fail")}, {"fail"})
    check("filter by stage",
          {r["stage"] for r in svc.list_inspections(stage="sewing_inline")}, {"sewing_inline"})
    check("filter by a stage nobody used", svc.list_inspections(stage="nope"), [])
    check("filter by order accepts a query-string str",
          all(r["order_id"] == oid for r in svc.list_inspections(order_id=str(oid))), True)

    # roll-ups (seed only, plus our one extra 'final' inspection)
    secs = {s["section"]: s for s in svc.by_section()}
    check("sewing_inline DHU = 12 defects / 125 units", secs["sewing_inline"]["dhu"], 9.6)
    check("sewing_inline RFT = (125-9)/125", secs["sewing_inline"]["rft"], 92.8)
    check("sewing_inline counted 1 failed lot", secs["sewing_inline"]["failed"], 1)
    check("finishing DHU = 6 / 200", secs["finishing"]["dhu"], 3.0)
    check("by_section sorted worst-first", secs["sewing_inline"]["dhu"] >= secs["finishing"]["dhu"], True)
    check("by_order only covers order-linked inspections",
          all(o["order_id"] for o in svc.by_order()), True)

    d = svc.dashboard()
    check("dashboard counts inspections", d["inspections"], 7)
    check("dashboard pass rate over DECIDED lots only",
          d["pass_rate"], round(d["passed"] / (d["passed"] + d["failed"]) * 100, 1))
    check("dashboard pending excluded from pass rate", d["pending"], 1)
    check("dashboard top defects is a pareto", d["top_defects"][0]["rank"], 1)

    # DHU sweep: sewing_inline runs at 9.6 > 8.0 limit -> one warning, deduped
    svc.dhu_sweep()
    n1 = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                      "AND severity='warning'").fetchone()["c"]
    svc.dhu_sweep()
    n2 = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='quality' "
                      "AND severity='warning'").fetchone()["c"]
    check("DHU sweep alerted over-limit sections", n1 > 0, True)
    check("DHU sweep is deduplicated per day", n2, n1)

    conn.close()

# ------------------------------------------------------------ template parse
print("== templates ==")
from jinja2 import Environment, FileSystemLoader             # noqa: E402

import re                                                   # noqa: E402

env = Environment(loader=FileSystemLoader(r"D:\TC platform\tc-platform-render\app\templates"))
tpl_dir = Path(r"D:\TC platform\tc-platform-render\app\templates\quality")
texts = {}
for f in sorted(tpl_dir.glob("*.html")):
    src = f.read_text(encoding="utf-8")
    env.parse(src, filename=f.name)
    ok += 1
    print(f"  ok  parsed {f.name}")
    # app.js overwrites textContent with the dictionary value, so one key carrying
    # two different English strings silently mistranslates one of the two.
    for key, text in re.findall(r'data-i18n="([^"]+)"[^>]*>([^<]*)', src):
        texts.setdefault(key, set()).add(text.strip())
check("every data-i18n key has exactly one English string",
      {k: v for k, v in texts.items() if len(v) > 1}, {})
print(f"  ok  {len(texts)} distinct i18n keys in the quality templates")

print(f"\nALL {ok} CHECKS PASSED")
