"""
Cut room self-test — runs the schema + services against throwaway SQLite files.

Two phases on purpose:
  A) orders only            -> the DEGRADED path (no BOM plan, no warehouse rolls)
  B) warehouse + costing too -> the LINKED path (planned consumption, roll booking,
                               fabric-variance bell)
Run:  python app/cutroom/tests_selftest.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cut_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                        # noqa: E402
config.Config.DB_PATH = TMP / "a.db"

from app import create_app                           # noqa: E402
from app.db import get_db, init_db                   # noqa: E402
from app.cutroom.schema import create_and_seed       # noqa: E402
from app.cutroom import services as svc              # noqa: E402

OK = []


def check(name, cond):
    OK.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


def near(a, b, tol=1e-4):
    return a is not None and abs(a - b) <= tol


# ==========================================================================
# 1. lay_metrics — pure formulas, every denominator guarded
# ==========================================================================
print("\n[1] lay_metrics — formulas and guards")
LAY = {"plies": 50, "pieces_per_ply": 8, "marker_length_m": 7.30, "marker_width_cm": 150,
       "end_allow_m": 0.06, "marker_area_m2": 9.31, "actual_fabric_m": 371.5}
mx = svc.lay_metrics(LAY)
check("pieces_cut = plies x pieces_per_ply = 400", mx["pieces_cut"] == 400)
check("theoretical = marker_length x plies = 365.0", mx["theoretical_m"] == 365.0)
check("planned = (marker+end) x plies = 368.0", mx["planned_m"] == 368.0)
check("fabric_used = measured actual (371.5)", mx["fabric_used_m"] == 371.5 and mx["measured"])
check("cons/garment = used/pieces = 0.92875", near(mx["cons_per_gmt"], 0.92875))
check("marker eff = area/(len x width) = 85.0%", mx["marker_eff_pct"] == 85.0)
check("utilisation = theo/used = 98.25%", mx["utilisation_pct"] == 98.25)
check("waste = 100 - utilisation = 1.75%", mx["waste_pct"] == 1.75)
check("INVARIANT utilisation + waste == 100",
      near(mx["utilisation_pct"] + mx["waste_pct"], 100.0, 0.001))

nm = svc.lay_metrics(dict(LAY, actual_fabric_m=0))
check("no measurement -> falls back to planned", nm["fabric_used_m"] == 368.0 and not nm["measured"])
check("planned cons/garment = 368/400 = 0.92", near(nm["cons_per_gmt"], 0.92))

z = svc.lay_metrics({})
check("empty lay: no crash, pieces 0", z["pieces_cut"] == 0)
check("empty lay: cons/garment is None (not 0)", z["cons_per_gmt"] is None)
check("empty lay: utilisation/waste None", z["utilisation_pct"] is None and z["waste_pct"] is None)
check("empty lay: marker eff None", z["marker_eff_pct"] is None)

g = svc.lay_metrics({"plies": "abc", "pieces_per_ply": None, "marker_length_m": "",
                     "marker_width_cm": "wide", "marker_area_m2": [], "actual_fabric_m": "x"})
check("garbage inputs coerce to 0, no exception", g["pieces_cut"] == 0 and g["cons_per_gmt"] is None)

check("zero plies -> pieces 0, cpg None",
      svc.lay_metrics(dict(LAY, plies=0))["cons_per_gmt"] is None)
check("zero pieces_per_ply -> cpg None",
      svc.lay_metrics(dict(LAY, pieces_per_ply=0))["cons_per_gmt"] is None)
check("zero marker width -> eff falls back, no ZeroDivision",
      svc.lay_metrics(dict(LAY, marker_width_cm=0))["marker_eff_pct"] is None)
check("nothing spread at all -> util None, no ZeroDivision",
      svc.lay_metrics(dict(LAY, marker_length_m=0, end_allow_m=0,
                           actual_fabric_m=0))["utilisation_pct"] is None)
check("marker length 0 but fabric spread -> util 0%, not a crash",
      svc.lay_metrics(dict(LAY, marker_length_m=0, actual_fabric_m=0))["utilisation_pct"] == 0.0)
check("entered efficiency used when area unknown",
      svc.lay_metrics(dict(LAY, marker_area_m2=0, marker_eff_pct=87.5))["marker_eff_pct"] == 87.5)
check("CAD area wins over an entered efficiency",
      svc.lay_metrics(dict(LAY, marker_eff_pct=99))["marker_eff_pct"] == 85.0)

# ==========================================================================
# 2. Phase A — orders only: the degraded path
# ==========================================================================
print("\n[2] phase A — orders only (no costing BOM, no warehouse rolls)")
app = create_app()
with app.app_context():
    conn = get_db()
    # create_app() -> init_db() now seeds warehouse, costing AND cutroom itself, so
    # the degraded path has to be re-created deliberately: take the sibling tables
    # away and start the cut room empty again. Without this the phase silently
    # tested the LINKED path and its "no BOM" assertions were false.
    conn.execute("DELETE FROM cut_lays")
    conn.execute("DELETE FROM cut_lay_rolls")
    for _t in ("cst_bom_lines", "wh_rolls"):
        conn.execute(f"DROP TABLE IF EXISTS {_t}")
    conn.commit()
    create_and_seed(conn)
    create_and_seed(conn)                       # idempotent: must not duplicate or destroy
    n = conn.execute("SELECT COUNT(*) AS c FROM cut_lays").fetchone()["c"]
    rolls = conn.execute("SELECT COUNT(*) AS c FROM cut_lay_rolls").fetchone()["c"]
    conn.close()
    check("seeded 4 demo lays from the demo orders", n == 4)
    check("create_and_seed is idempotent (still 4 after a second run)", n == 4)
    check("no roll links without the warehouse module", rolls == 0)

    rows = svc.order_rollup()
    check("rollup returns one row per order with lays", len(rows) == 3)
    check("no BOM -> planned_cpg None on every order",
          all(r["planned_cpg"] is None for r in rows))
    check("no BOM -> variance None, nothing to flag",
          all(r["variance_pct"] is None and r["flag"] is None for r in rows))
    svc.variance_sweep()
    conn = get_db()
    bells = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='cutroom'").fetchone()["c"]
    conn.close()
    check("no plan -> the sweep raises no false alarm", bells == 0)
    check("dashboard renders with no plan", svc.dashboard()["variance_pct"] is None)

# ==========================================================================
# 3. Phase B — warehouse + costing present: the linked path
# ==========================================================================
print("\n[3] phase B — with the costing BOM and warehouse rolls")
config.Config.DB_PATH = TMP / "b.db"
with app.app_context():
    init_db()
    conn = get_db()
    from app.warehouse.schema import create_and_seed as wh_seed
    from app.costing.schema import create_and_seed as cst_seed
    wh_seed(conn)
    cst_seed(conn)
    create_and_seed(conn)
    conn.commit()
    conn.close()

    den = [r for r in svc.order_rollup() if (r["order"] or {}).get("style_ref") == "TC-DEN-03"][0]
    check("denim order: 2 lays", den["lay_count"] == 2)
    check("denim order: 752 pieces cut (400 + 352)", den["pieces_cut"] == 752)
    check("denim order: 712.5 m used", den["fabric_used_m"] == 712.5)
    check("denim order: actual 0.9475 m/garment", near(den["actual_cpg"], 0.9475))
    check("denim order: planned 0.918 m/garment from the BOM (0.85 x 1.08)",
          near(den["planned_cpg"], 0.918))
    check("denim order: variance +3.21% unfavourable",
          near(den["variance_pct"], 3.21, 0.01) and den["flag"] == "unfavourable")
    check("denim order: 22.18 extra metres burnt", near(den["variance_m"], 22.18, 0.01))
    check("denim order: 8248 pieces still to cut", den["balance"] == 8248)
    check("denim order: 8.4% cut", near(den["cut_pct"], 8.4, 0.05))
    check("denim order: utilisation 96.31%", near(den["utilisation_pct"], 96.31, 0.01))
    check("denim order: weighted marker efficiency 85.0%", den["marker_eff_pct"] == 85.0)

    knit = [r for r in svc.order_rollup() if (r["order"] or {}).get("style_ref") == "TC-KNIT-01"][0]
    check("knit order: BOM is in kg -> no metre plan, variance stays None",
          knit["planned_cpg"] is None and knit["variance_pct"] is None)

    knit_lay = [l for l in svc.list_lays() if l["shade_lot"] == "D-5108"][0]
    b = svc.get_lay(knit_lay["id"])
    check("warehouse rolls booked to the knit lay", len(b["rolls"]) == 2)
    check("booked metres reconcile to the fabric used (449.0)", b["rolls_m"] == 449.0)
    check("no unaccounted fabric on that lay", near(b["rolls_gap_m"], 0.0, 0.01))
    check("roll rows carry the warehouse roll id", all(r["roll_id"] for r in b["rolls"]))

    den_lay = [l for l in svc.list_lays() if l["shade_lot"] == "IND-330"][0]
    bd = svc.get_lay(den_lay["id"])
    check("partial roll coverage shows the unaccounted metres",
          near(bd["rolls_gap_m"], 131.5, 0.01))
    check("lay-level variance vs the BOM plan is computed",
          near(bd["variance_pct"], 1.17, 0.02))

# ==========================================================================
# 4. The bell: fires once per order, re-arms on any edit
# ==========================================================================
print("\n[4] fabric-variance bell — idempotent, re-arms on edit")
with app.app_context():
    svc.variance_sweep()
    conn = get_db()
    one = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='cutroom'").fetchone()["c"]
    conn.close()
    check("one bell for the one over-consuming order", one == 1)
    svc.variance_sweep()
    conn = get_db()
    two = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='cutroom'").fetchone()["c"]
    conn.close()
    check("double sweep does NOT double-alert", two == 1)

    den = [r for r in svc.order_rollup() if (r["order"] or {}).get("style_ref") == "TC-DEN-03"][0]
    svc.update_lay(den["lays"][0]["id"], {"notes": "re-measured"})
    svc.variance_sweep()
    conn = get_db()
    three = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='cutroom'").fetchone()["c"]
    conn.close()
    check("editing a lay re-arms the alarm (2nd bell)", three == 2)

# ==========================================================================
# 5. Writes — validation, coercion and negative cases
# ==========================================================================
print("\n[5] writes — validation and negative cases")
with app.app_context():
    lid = svc.create_lay({"order_id": "3", "plies": "ten", "pieces_per_ply": "8",
                          "marker_length_m": "", "marker_width_cm": "abc",
                          "actual_fabric_m": None, "marker_ref": "MK-JUNK"},
                         {"username": "test"})
    lay = svc.get_lay(lid)["lay"]
    check("non-numeric plies stored as 0, not a string", lay["plies"] == 0)
    check("blank marker length stored as 0.0", lay["marker_length_m"] == 0.0)
    check("all-zero lay: no crash, cpg None", lay["cons_per_gmt"] is None)
    check("lay_no generated from the row id", (lay["lay_no"] or "").startswith("CUT-"))

    check("update with nothing to set is a no-op on an existing lay",
          svc.update_lay(lid, {}) is True)
    check("update of a missing lay returns False so the route can 404",
          svc.update_lay(999999, {"notes": "x"}) is False)

    check("roll with 0 metres refused", svc.add_lay_roll(lid, {"meters": 0}) == (False, "bad_meters"))
    check("roll with negative metres refused", svc.add_lay_roll(lid, {"meters": -5})[0] is False)
    check("roll with non-numeric metres refused", svc.add_lay_roll(lid, {"meters": "x"})[0] is False)
    check("roll on a missing lay refused",
          svc.add_lay_roll(999999, {"meters": 10}) == (False, "lay_not_found"))
    check("valid roll booking accepted",
          svc.add_lay_roll(lid, {"meters": "12.5", "roll_no": "R-1"}) == (True, "ok"))

    check("summary of a missing order is None", svc.order_summary(999999) is None)
    check("lay detail of a missing lay is None", svc.get_lay(999999) is None)

    # a lay with no order at all must not break any listing or rollup
    orphan = svc.create_lay({"plies": 5, "pieces_per_ply": 4, "marker_length_m": 2.0},
                            {"username": "test"})
    check("orderless lay still lists", any(l["id"] == orphan for l in svc.list_lays()))
    check("orderless lay does not break the dashboard", svc.dashboard()["lays_total"] >= 5)

    # cancelled lays are excluded from every number
    den = svc.order_summary(3)
    before = den["pieces_cut"]
    real = next(l for l in den["lays"] if l["pieces_cut"] > 0)
    svc.update_lay(real["id"], {"status": "cancelled"})
    after = svc.order_summary(3)["pieces_cut"]
    check("cancelling a lay removes it from the reconciliation", after < before)

print("\n%d/%d checks passed" % (sum(1 for _, o in OK if o), len(OK)))
sys.exit(0 if all(o for _, o in OK) else 1)
