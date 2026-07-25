"""
Isolated self-test for the wash recipe library. Runs against a throwaway DB in a
temp dir (never the repo's platform.db). Run: python app/wash/tests_selftest.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wsh_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                     # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                        # noqa: E402

app = create_app()
OK = []


def check(label, cond):
    OK.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


def near(a, b, tol=0.05):
    return a is not None and abs(float(a) - float(b)) <= tol


with app.app_context():
    from app.db import get_db
    from app.wash.schema import create_and_seed
    from app.wash import services as svc

    conn = get_db()
    create_and_seed(conn)
    create_and_seed(conn)          # every boot forever: must be a no-op the 2nd time
    conn.commit()

    print("\n[1] schema + seed")
    n_rec = conn.execute("SELECT COUNT(*) AS c FROM wsh_recipes").fetchone()["c"]
    n_ver = conn.execute("SELECT COUNT(*) AS c FROM wsh_versions").fetchone()["c"]
    n_bat = conn.execute("SELECT COUNT(*) AS c FROM wsh_batches").fetchone()["c"]
    check("seed is idempotent: 3 recipes, 4 versions, 3 batches after two runs",
          (n_rec, n_ver, n_bat) == (3, 4, 3))
    check("demo recipes reference the seeded demo orders",
          conn.execute("SELECT COUNT(*) AS c FROM wsh_recipes WHERE order_id IS NOT NULL"
                       ).fetchone()["c"] == 3)
    check("batch_no is unique and derived from the row id",
          conn.execute("SELECT COUNT(DISTINCT batch_no) AS c FROM wsh_batches").fetchone()["c"] == 3)

    stn = conn.execute("SELECT id FROM wsh_recipes WHERE code='WR-STN-01'").fetchone()["id"]
    b = svc.get_recipe(stn)
    v2 = b["sel"]
    check("the approved version is selected by default (v2, not the retired v1)",
          v2["version"] == 2 and v2["status"] == "approved")

    print("\n[2] derived maths — WR-STN-01 v2")
    t = b["tot"]
    # 12+35+10+15+40
    check("cycle_min = SUM(minutes) = 112", near(t["cycle_min"], 112))
    # 120*6 + 120*5 + 120*6 + 120*6 = 720+600+720+720 ; dry step adds nothing
    check("water_l = SUM(load x liquor_ratio) = 2760", near(t["water_l"], 2760))
    check("baths = 4 (the dry step is not a bath)", t["baths"] == 4)
    check("load_kg = nominal machine load = 120", near(t["load_kg"], 120))
    check("max_temp_c = peak BATH temp = 55 (70C dryer air excluded)", near(t["max_temp_c"], 55))
    # 720*35 + 600*25 + 720*20 + 720*20 + 120*50(dry)
    check("heat_lk = 75000 litre-kelvin", near(t["heat_lk"], 75000))
    # 0.8*720 + 1.0*600 + 0.5*720 + 2%*120kg*1000
    check("chem_g = 576+600+360+2400 = 3936", near(t["chem_g"], 3936))

    # constant load + constant ratio => water == load x ratio x baths (the shop-floor form)
    flat = [{"load_kg": 100, "liquor_ratio": 8, "minutes": 10, "temp_c": 40},
            {"load_kg": 100, "liquor_ratio": 8, "minutes": 10, "temp_c": 40},
            {"load_kg": 100, "liquor_ratio": 8, "minutes": 10, "temp_c": 40}]
    ft = svc.totals(flat)
    check("constant load+ratio: water_l == load x ratio x baths (100x8x3 = 2400)",
          near(ft["water_l"], 100 * 8 * 3) and ft["baths"] == 3)

    print("\n[3] impact indicator")
    imp = b["imp"]
    check("water index = (2760/120)/60 x100 = 38.3", near(imp["water"], 38.3, 0.1))
    check("energy index = (75000/120)/1500 x100 = 41.7", near(imp["energy"], 41.7, 0.1))
    check("chem index = (3936/120)/120 x100 = 27.3", near(imp["chem"], 27.3, 0.1))
    check("score = round(mean) = 36, band medium", imp["score"] == 36 and imp["band"] == "medium")
    check("zero goods weight -> score None, never a ZeroDivisionError",
          svc.impact({"load_kg": 0, "water_l": 99, "heat_lk": 99, "chem_g": 99})["score"] is None)
    check("empty recipe -> all zeros, no crash", svc.totals([])["cycle_min"] == 0)
    check("garbage step values are coerced, not raised",
          svc.totals([{"load_kg": "abc", "liquor_ratio": None, "minutes": "", "temp_c": "x",
                       "chemicals": [{"gpl": "n/a", "owg_pct": None}]}])["cycle_min"] == 0)
    check("indices are capped at 100",
          svc.impact({"load_kg": 1, "water_l": 99999, "heat_lk": 99999,
                      "chem_g": 99999})["water"] == 100.0)

    print("\n[4] versioning — a new version SNAPSHOTS, it does not mutate")
    v2_steps_before = len(b["steps"])
    v2_cycle_before = t["cycle_min"]
    v3_id, err = svc.new_version(stn, v2["id"], {"username": "tester"})
    check("new_version returns an id", v3_id and err is None)
    b3 = svc.get_recipe(stn, v3_id)
    check("v3 is a draft numbered 3", b3["sel"]["version"] == 3 and b3["sel"]["status"] == "draft")
    check("v3 copied all 5 steps", len(b3["steps"]) == v2_steps_before)
    check("v3's step rows are NEW rows (different ids)",
          set(s["id"] for s in b3["steps"]).isdisjoint(set(s["id"] for s in b["steps"])))
    check("v3's chemical rows are new rows too",
          set(c["id"] for s in b3["steps"] for c in s["chemicals"]).isdisjoint(
              set(c["id"] for s in b["steps"] for c in s["chemicals"])))
    ok, err = svc.add_step(v3_id, {"operation": "tint", "temp_c": 60, "minutes": 20,
                                   "liquor_ratio": 8, "load_kg": 120}, {"username": "tester"})
    check("a step can be added to the draft v3", ok and err is None)
    b2_after = svc.get_recipe(stn, v2["id"])
    check("EDITING v3 LEFT v2 UNTOUCHED (step count)", len(b2_after["steps"]) == v2_steps_before)
    check("EDITING v3 LEFT v2 UNTOUCHED (cycle time)",
          near(b2_after["tot"]["cycle_min"], v2_cycle_before))
    check("v3 cycle time grew by the new step (112+20=132)",
          near(svc.get_recipe(stn, v3_id)["tot"]["cycle_min"], 132))
    check("frozen: a step cannot be added to the APPROVED v2",
          svc.add_step(v2["id"], {"operation": "rinse"}, None) == (False, "version_frozen"))
    check("missing version id rejected", svc.add_step(999999, {"operation": "rinse"}, None)
          == (False, "version_not_found"))
    check("empty operation rejected", svc.add_step(v3_id, {"operation": "  "}, None)
          == (False, "operation_required"))
    check("new_version on a missing recipe rejected",
          svc.new_version(999999, None, None) == (None, "recipe_not_found"))

    print("\n[5] bulk gate — no approved lab dip, no bulk")
    check("v3 cannot be approved: no lab dip",
          svc.set_version_status(v3_id, "approved", None) == (False, "labdip_not_approved"))
    dip_id, err = svc.create_labdip({"version_id": v3_id, "reference": "LD-TEST"}, {"username": "lab"})
    check("lab dip logged as pending", dip_id and err is None)
    check("v3 still blocked while the dip is pending",
          svc.set_version_status(v3_id, "approved", None) == (False, "labdip_not_approved"))
    check("a rejected dip does not unlock bulk",
          svc.set_labdip_verdict(dip_id, "rejected", "Too dark.", {"username": "lab"})[0]
          and svc.set_version_status(v3_id, "approved", None) == (False, "labdip_not_approved"))
    check("rejecting a shade rang the bell",
          conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='wash' "
                       "AND title LIKE 'Lab dip rejected%'").fetchone()["c"] == 1)
    svc.set_labdip_verdict(dip_id, "approved", None, {"username": "lab"})
    check("v3 approves once its own dip is approved",
          svc.set_version_status(v3_id, "approved", {"username": "boss"}) == (True, None))
    check("approving v3 retired the previous bulk standard v2",
          conn.execute("SELECT status FROM wsh_versions WHERE id=?", (v2["id"],)
                       ).fetchone()["status"] == "retired")
    check("exactly one approved version per recipe",
          conn.execute("SELECT COUNT(*) AS c FROM wsh_versions WHERE recipe_id=? AND "
                       "status='approved'", (stn,)).fetchone()["c"] == 1)
    check("double-apply is a no-op, not a re-approval",
          svc.set_version_status(v3_id, "approved", None) == (False, "already_approved"))
    check("a nonsense status is rejected",
          svc.set_version_status(v3_id, "banana", None) == (False, "bad_status"))
    # A fresh recipe's automatic draft v1 has no steps. (new_version() deliberately
    # REFUSES a missing source, so it can no longer be used to make an empty version.)
    bare_v = conn.execute("SELECT id FROM wsh_versions WHERE recipe_id=?",
                          (svc.create_recipe({"name": "No steps yet"}, None),)).fetchone()["id"]
    bare_dip, _ = svc.create_labdip({"version_id": bare_v}, None)
    svc.set_labdip_verdict(bare_dip, "approved", None, None)
    check("a version with no steps cannot be approved even with an approved dip",
          svc.set_version_status(bare_v, "approved", None) == (False, "no_steps"))
    check("verdict on a missing lab dip rejected",
          svc.set_labdip_verdict(999999, "approved", None, None) == (False, "labdip_not_found"))
    check("a nonsense verdict is rejected",
          svc.set_labdip_verdict(dip_id, "maybe", None, None) == (False, "bad_verdict"))
    check("a lab dip needs a real version",
          svc.create_labdip({"version_id": 999999}, None) == (None, "version_not_found"))
    # The dip must be pinned to a batch that ran THIS version: a swatch cut from a lot
    # that ran another version is evidence for that version, and the bulk gate counts
    # approved dips, so borrowing it would unlock the wrong version.
    own_bid, _e = svc.create_batch({"version_id": v3_id, "load_kg": 120, "act_minutes": 132,
                                    "act_temp_c": 60}, None)
    own_batch = conn.execute("SELECT id, batch_no FROM wsh_batches WHERE id=?", (own_bid,)).fetchone()
    bdip, _e = svc.create_labdip({"version_id": v3_id, "reference": "LD-BATCH",
                                  "batch_id": own_batch["id"]}, None)
    check("a lab dip can be pinned to one batch and reads back with its batch_no",
          next(x for x in svc.list_labdips() if x["id"] == bdip)["batch_no"] == own_batch["batch_no"])
    other_batch = conn.execute("SELECT id FROM wsh_batches WHERE version_id<>? ORDER BY id LIMIT 1",
                               (v3_id,)).fetchone()
    check("a dip cannot borrow the swatch of a lot that ran a different version",
          svc.create_labdip({"version_id": v3_id, "batch_id": other_batch["id"]}, None)
          == (None, "batch_version_mismatch"))

    print("\n[6] batch traceability + deviation alerting")
    nb = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='wash'").fetchone()["c"]
    bid, err = svc.create_batch({"version_id": v3_id, "machine": "Washer 1", "load_kg": 120,
                                 "act_minutes": 130, "act_temp_c": 60, "operator": "OP-1"},
                                {"username": "tester"})
    row = conn.execute("SELECT * FROM wsh_batches WHERE id=?", (bid,)).fetchone()
    check("batch records the VERSION it ran, not just the recipe", row["version_id"] == v3_id)
    check("in-tolerance actuals are not flagged (130 vs 132 min, 60 vs 60 C, 120 vs 120 kg)",
          row["deviation"] is None)
    check("in-tolerance batch raised no alert",
          conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='wash'"
                       ).fetchone()["c"] == nb)
    bid2, _ = svc.create_batch({"version_id": v3_id, "load_kg": 200, "act_minutes": 200,
                                "act_temp_c": 90}, {"username": "tester"})
    row2 = conn.execute("SELECT * FROM wsh_batches WHERE id=?", (bid2,)).fetchone()
    check("out-of-tolerance actuals are flagged on time, temp and load",
          row2["deviation"] and "time" in row2["deviation"] and "temp" in row2["deviation"]
          and "load" in row2["deviation"])
    check("the deviation rang the bell exactly once",
          conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='wash' AND "
                       "title LIKE 'Wash batch off recipe%'").fetchone()["c"] == 1
          and row2["deviation_alerted"] == 1)
    check("a batch cannot be recorded against a missing version",
          svc.create_batch({"version_id": 999999}, None) == (None, "version_not_found"))
    check("a batch with no version id is rejected",
          svc.create_batch({}, None) == (None, "version_not_found"))
    check("a non-numeric version id is rejected",
          svc.create_batch({"version_id": "abc"}, None) == (None, "version_not_found"))
    check("blank / zero actuals judge nothing (no baseline -> no /0)",
          svc.create_batch({"version_id": v3_id, "load_kg": "", "act_minutes": None,
                            "act_temp_c": ""}, None)[1] is None
          and conn.execute("SELECT deviation FROM wsh_batches ORDER BY id DESC LIMIT 1"
                           ).fetchone()["deviation"] is None)
    check("a recipe with zero nominals never divides by zero",
          svc._deviation(svc.totals([]), 100, 100, 100) == "")

    print("\n[7] recipe creation + reads")
    rid = svc.create_recipe({"name": "Acid wash trial", "wash_type": "acid"}, {"username": "t"})
    r = conn.execute("SELECT * FROM wsh_recipes WHERE id=?", (rid,)).fetchone()
    check("a blank code is generated from the row id", r["code"] == "WR-%04d" % rid)
    check("a draft v1 is created with the recipe",
          conn.execute("SELECT COUNT(*) AS c FROM wsh_versions WHERE recipe_id=? AND version=1 "
                       "AND status='draft'", (rid,)).fetchone()["c"] == 1)
    check("get_recipe on a missing id returns None", svc.get_recipe(999999) is None)
    check("list_recipes filters by wash type",
          all(x["wash_type"] == "stone" for x in svc.list_recipes("stone")))
    check("list_recipes reports the approved version",
          next(x for x in svc.list_recipes() if x["code"] == "WR-STN-01")["approved_version"] == 3)
    check("add_chemical needs a real step", svc.add_chemical(999999, {"name": "x"}, None)
          == (False, "step_not_found"))
    check("a blank/garbage step id is rejected, not raised",
          svc.add_chemical("", {"name": "x"}, None) == (False, "step_not_found")
          and svc.add_chemical("abc", {"name": "x"}, None) == (False, "step_not_found"))
    step = conn.execute("SELECT id FROM wsh_steps WHERE version_id=? LIMIT 1", (v3_id,)).fetchone()
    check("add_chemical is blocked on a frozen version",
          svc.add_chemical(step["id"], {"name": "Softener"}, None) == (False, "version_frozen"))
    v1 = conn.execute("SELECT id FROM wsh_versions WHERE recipe_id=? AND version=1", (rid,)).fetchone()
    s1, _e = svc.add_step(v1["id"], {"operation": "rinse", "minutes": 10,
                                     "liquor_ratio": 8, "load_kg": 50, "temp_c": 30}, None)
    sid = conn.execute("SELECT id FROM wsh_steps WHERE version_id=?", (v1["id"],)).fetchone()["id"]
    check("add_chemical accepts the string step id a form actually posts",
          svc.add_chemical(str(sid), {"name": "Acetic acid", "gpl": "0.5"}, None) == (True, None))
    check("a chemical needs a name",
          svc.add_chemical(sid, {"name": "  "}, None) == (False, "name_required"))
    check("g/L dosing bills against the bath: 0.5 g/L x (50kg x 8) = 200 g",
          near(svc.get_recipe(rid, v1["id"])["tot"]["chem_g"], 200))

    print("\n[8] dashboard")
    d = svc.dashboard()
    # Counted against the tables, not hardcoded: a hardcoded total breaks the moment
    # another check creates a recipe, which says nothing about the dashboard.
    check("dashboard returns live counts without crashing",
          d["recipes"] == conn.execute("SELECT COUNT(*) AS c FROM wsh_recipes").fetchone()["c"]
          and d["batches"] == conn.execute("SELECT COUNT(*) AS c FROM wsh_batches").fetchone()["c"]
          and isinstance(d["avg_cycle_min"], float))
    check("avg cycle is over APPROVED versions only", d["avg_cycle_min"] > 0)
    check("rejected/pending shades are surfaced", isinstance(d["pending_dips"], list))
    conn.close()

print("\n%d/%d checks passed" % (sum(OK), len(OK)))
sys.exit(0 if all(OK) else 1)
