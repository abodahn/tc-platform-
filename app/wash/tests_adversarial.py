"""
Adversarial verification of the wash module — isolated throwaway database.

Written on the assumption that the module's own self-test was too kind. Rules:
  * every claimed formula is RECOMPUTED here by an INDEPENDENT implementation
    (straight SQL + plain Python) and compared row by row, plus a handful of
    values worked out by hand and hard-coded;
  * every exactly-once operation is invoked TWICE;
  * create_and_seed() is run THREE times and every table counted;
  * every division is fed a zero and a None;
  * every id-shaped input is fed a hostile value (huge, negative, float,
    non-numeric, empty, missing) and the process must not raise;
  * the routes are exercised through a real Flask test client, anonymous and
    with an under-privileged user, and every template is actually RENDERED
    (parsing is not rendering).

    python app/wash/tests_adversarial.py
"""
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wash_adv_"))
REPO = Path(__file__).resolve().parents[2]
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
import config                                              # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                 # noqa: E402

FAILS = []
COUNT = [0]


def ck(cond, label):
    COUNT[0] += 1
    if cond:
        print("  PASS  " + label)
    else:
        print("  FAIL  " + label)
        FAILS.append(label)


def section(title):
    print("\n[%s]" % title)


def raises(fn):
    """Return the exception a call raises, or None. A service must never raise."""
    try:
        fn()
        return None
    except Exception as e:                                  # noqa: BLE001
        return e


# ---------------------------------------------------------------------------
# INDEPENDENT re-implementation of the claimed formulas. Deliberately naive and
# written straight from the business rules, not from services.totals().
# ---------------------------------------------------------------------------
def ref_totals(conn, version_id):
    steps = conn.execute("SELECT * FROM wsh_steps WHERE version_id=? ORDER BY step_no, id",
                         (version_id,)).fetchall()
    cycle = water = chem = heat = 0.0
    load = peak = 0.0
    baths = 0
    for s in steps:
        lg = max(0.0, float(s["load_kg"] or 0))
        lr = max(0.0, float(s["liquor_ratio"] or 0))
        mn = max(0.0, float(s["minutes"] or 0))
        tc = max(0.0, float(s["temp_c"] or 0))
        bath = lg * lr                       # litres of bath for this step
        cycle += mn
        water += bath
        if bath > 0:
            baths += 1
            peak = max(peak, tc)             # peak BATH temp only
        load = max(load, lg)
        heat += (bath if bath > 0 else lg) * max(0.0, tc - 20.0)
        for c in conn.execute("SELECT * FROM wsh_chemicals WHERE step_id=?", (s["id"],)).fetchall():
            gpl = max(0.0, float(c["gpl"] or 0))
            owg = max(0.0, float(c["owg_pct"] or 0))
            chem += gpl * bath + owg / 100.0 * lg * 1000.0
    return {"steps": len(steps), "baths": baths, "cycle_min": round(cycle, 2),
            "water_l": round(water, 2), "chem_g": round(chem, 2), "heat_lk": round(heat, 2),
            "load_kg": round(load, 2), "max_temp_c": round(peak, 2)}


def ref_impact(t):
    load = float(t["load_kg"])
    if load <= 0:
        return None

    def ix(v, ref):
        return max(0.0, min(100.0, 100.0 * (float(v) / load) / ref))
    return int(round((ix(t["water_l"], 60.0) + ix(t["heat_lk"], 1500.0)
                      + ix(t["chem_g"], 120.0)) / 3.0))


def ref_deviation(t, load, act_min, act_temp):
    """The three tolerance rules, re-stated from the business description."""
    flags = []
    if t["cycle_min"] > 0 and act_min > 0 and \
            abs(act_min - t["cycle_min"]) / t["cycle_min"] * 100.0 > 15.0:
        flags.append("time")
    if t["max_temp_c"] > 0 and act_temp > 0 and abs(act_temp - t["max_temp_c"]) > 5.0:
        flags.append("temp")
    if t["load_kg"] > 0 and load > 0 and \
            abs(load - t["load_kg"]) / t["load_kg"] * 100.0 > 10.0:
        flags.append("load")
    return flags


def dump(conn, version_id):
    """A byte-exact fingerprint of one version's stored steps + chemical lines."""
    out = []
    for s in conn.execute("SELECT * FROM wsh_steps WHERE version_id=? ORDER BY id",
                          (version_id,)).fetchall():
        out.append(tuple(s[k] for k in s.keys()))
        for c in conn.execute("SELECT * FROM wsh_chemicals WHERE step_id=? ORDER BY id",
                              (s["id"],)).fetchall():
            out.append(tuple(c[k] for k in c.keys()))
    return out


def nrows(conn, t, where="1=1", args=()):
    return conn.execute("SELECT COUNT(*) AS c FROM %s WHERE %s" % (t, where), args).fetchone()["c"]


def bells(conn):
    return nrows(conn, "notifications", "module='wash'")


# ===========================================================================
app = create_app()
U = {"username": "adversary"}

with app.app_context():
    from app.db import get_db
    from app.wash.schema import create_and_seed
    from app.wash import services as svc

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    # -- A. seed idempotency -------------------------------------------------
    section("A. schema + seed idempotency (create_and_seed x3)")
    TABLES = ("wsh_recipes", "wsh_versions", "wsh_steps", "wsh_chemicals",
              "wsh_batches", "wsh_labdips")
    after1 = {t: nrows(conn, t) for t in TABLES}
    create_and_seed(conn)
    create_and_seed(conn)
    conn.commit()
    after3 = {t: nrows(conn, t) for t in TABLES}
    ck(after1 == after3, "3 consecutive seeds neither duplicate nor destroy: %s" % after3)
    ck(after3["wsh_recipes"] == 3 and after3["wsh_versions"] == 4
       and after3["wsh_steps"] == 19 and after3["wsh_chemicals"] == 13,
       "hand-counted seed shape: 3 recipes / 4 versions / 19 steps / 13 chemicals")
    ck(nrows(conn, "wsh_batches", "batch_no IS NULL") == 0,
       "every seeded batch got a batch_no")
    bnos = [r["batch_no"] for r in conn.execute("SELECT batch_no FROM wsh_batches").fetchall()]
    ck(len(bnos) == len(set(bnos)), "batch_no is unique across the seed")
    ck(bells(conn) == 0, "seeding an off-recipe demo batch does NOT flood the bell")

    V2 = conn.execute("SELECT v.id FROM wsh_versions v JOIN wsh_recipes r ON r.id=v.recipe_id "
                      "WHERE r.code='WR-STN-01' AND v.version=2").fetchone()["id"]
    V1 = conn.execute("SELECT v.id FROM wsh_versions v JOIN wsh_recipes r ON r.id=v.recipe_id "
                      "WHERE r.code='WR-STN-01' AND v.version=1").fetchone()["id"]
    R1 = conn.execute("SELECT id FROM wsh_recipes WHERE code='WR-STN-01'").fetchone()["id"]

    # -- B. every formula recomputed independently ---------------------------
    section("B. derived maths recomputed by an independent implementation")
    for v in conn.execute("SELECT id FROM wsh_versions").fetchall():
        got = svc.version_totals(v["id"])
        exp = ref_totals(conn, v["id"])
        ck(got == exp, "version %s totals match the independent recompute (cycle=%s water=%s "
           "chem=%s heat=%s)" % (v["id"], got["cycle_min"], got["water_l"], got["chem_g"],
                                 got["heat_lk"]))

    t2 = svc.version_totals(V2)
    # Hand-worked WR-STN-01 v2: 55C/12min/LR6, 45/35/5, 40/10/6, 40/15/6 @120kg + dry 70C/40min.
    ck(t2["cycle_min"] == 112, "hand: cycle = 12+35+10+15+40 = 112 min")
    ck(t2["water_l"] == 2760, "hand: water = 720+600+720+720 = 2760 L")
    ck(t2["baths"] == 4, "hand: 4 baths (the dry step is not a bath)")
    ck(t2["max_temp_c"] == 55, "hand: peak BATH temp 55 C, the 70 C dryer air excluded")
    ck(t2["heat_lk"] == 75000, "hand: heat = 25200+15000+14400+14400+6000 = 75000 L.K")
    ck(t2["chem_g"] == 3936, "hand: chem = 576+600+360+2400 = 3936 g")
    ck(t2["load_kg"] == 120, "hand: nominal load = 120 kg")

    # The isolated %OWG vs g/L distinction, which is where dosing maths usually rots.
    iso = svc.totals([{"load_kg": 50, "minutes": 10, "temp_c": 20, "liquor_ratio": 8,
                       "chemicals": [{"gpl": 0.5, "owg_pct": 0}]}])
    ck(iso["chem_g"] == 200, "g/L doses the BATH: 0.5 g/L x (50 kg x 8) = 200 g")
    iso2 = svc.totals([{"load_kg": 50, "minutes": 10, "temp_c": 20, "liquor_ratio": 8,
                        "chemicals": [{"gpl": 0, "owg_pct": 2}]}])
    ck(iso2["chem_g"] == 1000, "percent-OWG doses the dry GOODS: 2%% of 50 kg = 1000 g")
    dry = svc.totals([{"load_kg": 50, "minutes": 10, "temp_c": 20, "liquor_ratio": 0,
                       "chemicals": [{"gpl": 5, "owg_pct": 0}]}])
    ck(dry["chem_g"] == 0 and dry["baths"] == 0,
       "a g/L dose on a DRY step (no bath) contributes 0 g, not a phantom mass")

    const = svc.totals([{"load_kg": 100, "minutes": 5, "temp_c": 20, "liquor_ratio": 8}
                        for _ in range(3)])
    ck(const["water_l"] == 100 * 8 * 3,
       "constant load+ratio collapses to the shop-floor form load x ratio x baths")

    # -- C. impact indicator -------------------------------------------------
    section("C. impact indicator (internal, lower is better)")
    i2 = svc.impact(t2)
    ck(i2["water"] == 38.3, "water index = (2760/120)/60 x100 = 38.3")
    ck(i2["energy"] == 41.7, "energy index = (75000/120)/1500 x100 = 41.7")
    ck(i2["chem"] == 27.3, "chem index = (3936/120)/120 x100 = 27.3")
    ck(i2["score"] == 36 and i2["band"] == "medium",
       "equal-weight mean of the three indices = 36 -> band medium")
    ck(i2["score"] == ref_impact(t2), "score matches the independent recompute")
    ck(svc.impact({"load_kg": 0})["score"] is None,
       "zero goods weight -> score None, never a ZeroDivisionError")
    ck(svc.impact({})["score"] is None, "an EMPTY totals dict -> score None, no KeyError")
    ck(svc.impact({"load_kg": None, "water_l": None})["score"] is None,
       "load_kg=None -> score None (None never reaches the division)")
    ck(svc.impact({"load_kg": 10, "water_l": None, "heat_lk": None, "chem_g": None})["score"] == 0,
       "None numerators coerce to 0, they do not raise")
    ck(svc.impact({"load_kg": 1, "water_l": 1e9, "heat_lk": 1e9, "chem_g": 1e9})["score"] == 100,
       "indices are capped at 100 -> the worst possible score is exactly 100")
    ck(svc.impact({"load_kg": 10, "water_l": -500, "heat_lk": -500, "chem_g": -500})["score"] == 0,
       "a negative intensity floors at 0 and cannot band a filthy recipe as 'low'")
    for sc, band in ((33, "low"), (34, "medium"), (66, "medium"), (67, "high")):
        got = svc.impact({"load_kg": 1, "water_l": 0.6 * sc, "heat_lk": 15.0 * sc,
                          "chem_g": 1.2 * sc})
        ck(got["score"] == sc and got["band"] == band,
           "band boundary: score %d -> %s" % (sc, band))
    ck(svc.totals([])["load_kg"] == 0 and svc.impact(svc.totals([]))["band"] == "na",
       "an empty version -> all zeros and band 'na', no crash")

    # -- D. garbage in the stored rows ---------------------------------------
    section("D. hostile / garbage values inside totals()")
    junk = svc.totals([{"load_kg": "abc", "minutes": None, "temp_c": "", "liquor_ratio": "None",
                        "chemicals": [{"gpl": "x", "owg_pct": None}]},
                       {"load_kg": "1e400", "minutes": "inf", "temp_c": "nan",
                        "liquor_ratio": "-inf"},
                       {"load_kg": -100, "minutes": -50, "temp_c": -30, "liquor_ratio": -8}])
    ck(junk == {"steps": 3, "baths": 0, "cycle_min": 0.0, "water_l": 0.0, "chem_g": 0.0,
                "heat_lk": 0.0, "load_kg": 0.0, "max_temp_c": 0.0},
       "garbage, inf, nan and negatives all coerce to 0 - no exception, no negative total")
    ck(svc.totals([{"load_kg": 100, "liquor_ratio": 6, "chemicals": None}])["chem_g"] == 0,
       "chemicals=None is treated as no chemicals")

    # -- E. the snapshot guarantee -------------------------------------------
    section("E. versioning - a new version SNAPSHOTS, it never mutates the old one")
    before_v2_dump = dump(conn, V2)
    before_v2_tot = svc.version_totals(V2)
    V3, err = svc.new_version(R1, V2, U)
    ck(V3 and not err, "new_version returned an id (%r)" % (err,))
    v3row = conn.execute("SELECT * FROM wsh_versions WHERE id=?", (V3,)).fetchone()
    ck(v3row["version"] == 3 and v3row["status"] == "draft", "v3 is a draft numbered 3")
    ck(nrows(conn, "wsh_steps", "version_id=?", (V3,)) == 5, "v3 copied all 5 steps")
    src_ids = {r["id"] for r in conn.execute("SELECT id FROM wsh_steps WHERE version_id=?",
                                             (V2,)).fetchall()}
    new_ids = {r["id"] for r in conn.execute("SELECT id FROM wsh_steps WHERE version_id=?",
                                             (V3,)).fetchall()}
    ck(src_ids.isdisjoint(new_ids), "v3's step rows are NEW rows, not shared with v2")
    ck(svc.version_totals(V3) == before_v2_tot, "v3 starts arithmetically identical to v2")

    ok, err = svc.add_step(V3, {"operation": "tint", "temp_c": 30, "minutes": 20,
                                "liquor_ratio": 6, "load_kg": 120}, U)
    ck(ok, "a step can be appended to the DRAFT v3 (%r)" % (err,))
    steps3 = conn.execute("SELECT id FROM wsh_steps WHERE version_id=? ORDER BY step_no, id",
                          (V3,)).fetchall()
    ok, err = svc.add_chemical(steps3[-1]["id"], {"name": "Direct dye", "gpl": 2, "owg_pct": 0}, U)
    ck(ok, "a chemical can be appended to the draft's new step (%r)" % (err,))
    ck(dump(conn, V2) == before_v2_dump,
       "EDITING v3 LEFT v2 BYTE-IDENTICAL (every stored step + chemical column)")
    ck(svc.version_totals(V2) == before_v2_tot, "EDITING v3 LEFT v2's derived maths identical")
    t3 = svc.version_totals(V3)
    ck(t3["cycle_min"] == 132, "v3 cycle grew by exactly the new step: 112 + 20 = 132")
    ck(t3["water_l"] == 2760 + 720, "v3 water grew by exactly the new bath: 120 x 6 = 720 L")
    ck(t3["chem_g"] == 3936 + 2 * 720, "v3 chem grew by exactly 2 g/L x 720 L = 1440 g")

    # -- F. frozen versions ---------------------------------------------------
    section("F. frozen versions")
    ok, err = svc.add_step(V2, {"operation": "rinse", "minutes": 5}, U)
    ck(not ok and err == "version_frozen", "no step may be added to the APPROVED v2")
    ok, err = svc.add_step(V1, {"operation": "rinse", "minutes": 5}, U)
    ck(not ok and err == "version_frozen", "no step may be added to the RETIRED v1")
    v2step = conn.execute("SELECT id FROM wsh_steps WHERE version_id=? LIMIT 1", (V2,)).fetchone()
    ok, err = svc.add_chemical(v2step["id"], {"name": "Sneaky", "gpl": 9}, U)
    ck(not ok and err == "version_frozen", "no chemical may be added to a frozen version's step")
    ok, err = svc.add_step(V3, {"operation": "not_a_process", "minutes": 5}, U)
    ck(not ok and err == "bad_operation", "an unknown operation is refused, never defaulted")
    ok, err = svc.add_step(V3, {"operation": "", "minutes": 5}, U)
    ck(not ok and err == "operation_required", "an empty operation is refused")
    ok, err = svc.add_step(V3, {"operation": "rinse", "liquor_ratio": -8}, U)
    ck(not ok and err == "negative_value", "a negative liquor ratio is refused at the door")
    ok, err = svc.add_chemical(steps3[-1]["id"], {"name": "X", "gpl": -1}, U)
    ck(not ok and err == "negative_value", "a negative dose is refused at the door")
    ok, err = svc.add_chemical(steps3[-1]["id"], {"name": "   "}, U)
    ck(not ok and err == "name_required", "a blank chemical name is refused")
    ck(svc.version_totals(V2) == before_v2_tot, "after all that, v2 is STILL untouched")

    # -- G. the bulk gate ------------------------------------------------------
    section("G. bulk gate + single bulk standard + double-apply")
    ok, err = svc.set_version_status(V3, "approved", U)
    ck(not ok and err == "labdip_not_approved", "v3 cannot go to bulk with no lab dip")
    dip3, err = svc.create_labdip({"version_id": V3, "reference": "LD-ADV-1"}, U)
    ck(dip3 and not err, "lab dip logged as pending (%r)" % (err,))
    ok, err = svc.set_version_status(V3, "approved", U)
    ck(not ok and err == "labdip_not_approved", "a PENDING dip does not unlock bulk")
    b0 = bells(conn)
    ok, err = svc.set_labdip_verdict(dip3, "rejected", None, U)
    ck(ok, "the dip can be rejected")
    ck(bells(conn) == b0 + 1, "a rejection rings the bell exactly once")
    ok, err = svc.set_labdip_verdict(dip3, "rejected", None, {"username": "someone_else"})
    ck(not ok and err == "already_rejected",
       "re-posting the same verdict is refused - the sign-off is not re-stamped")
    ck(bells(conn) == b0 + 1, "the refused repost did NOT ring a second bell")
    approver = conn.execute("SELECT approver FROM wsh_labdips WHERE id=?", (dip3,)).fetchone()
    ck(approver["approver"] == "adversary",
       "the original approver's name survived the double-submit")
    ok, err = svc.set_version_status(V3, "approved", U)
    ck(not ok and err == "labdip_not_approved", "a REJECTED dip does not unlock bulk either")

    ok, err = svc.set_labdip_verdict(dip3, "approved", None, U)
    ck(ok, "the dip can then be approved")
    ok, err = svc.set_version_status(V3, "approved", U)
    ck(ok, "v3 goes to bulk once its OWN dip is approved (%r)" % (err,))
    ck(conn.execute("SELECT status FROM wsh_versions WHERE id=?", (V2,)).fetchone()["status"]
       == "retired", "approving v3 retired the previous bulk standard v2")
    ck(nrows(conn, "wsh_versions", "recipe_id=? AND status='approved'", (R1,)) == 1,
       "exactly ONE approved version per recipe")
    # "Retire the other approved version" MUST be scoped to this recipe. An UPDATE
    # missing its recipe_id=? would silently retire every bulk standard in the factory.
    ck(nrows(conn, "wsh_versions", "recipe_id<>? AND status='approved'", (R1,))
       == len({v["recipe_id"] for v in conn.execute(
           "SELECT DISTINCT recipe_id FROM wsh_versions WHERE recipe_id<>? "
           "AND status='approved'", (R1,)).fetchall()}),
       "approving R1's v3 did NOT retire any OTHER recipe's bulk standard")
    ck(nrows(conn, "wsh_versions", "status='approved'") >= 2,
       "other recipes still have a live bulk standard of their own")
    row_before = dict(conn.execute("SELECT approved_by, approved_at FROM wsh_versions WHERE id=?",
                                   (V3,)).fetchone())
    ok, err = svc.set_version_status(V3, "approved", {"username": "impostor"})
    ck(not ok and err == "already_approved", "double-approve is refused")
    row_after = dict(conn.execute("SELECT approved_by, approved_at FROM wsh_versions WHERE id=?",
                                  (V3,)).fetchone())
    ck(row_before == row_after, "the refused double-approve did not re-stamp approved_by/at")
    ok, err = svc.set_version_status(V3, "draft", U)
    ck(not ok and err == "cannot_reopen", "an approved version can never be re-opened as a draft")
    ok, err = svc.set_version_status(V3, "wharrgarbl", U)
    ck(not ok and err == "bad_status", "a nonsense status is refused")
    ok, err = svc.set_version_status(10 ** 9, "retired", U)
    ck(not ok and err == "version_not_found", "a missing version id is refused")

    # A version with NO steps can never be a bulk standard, even with a good dip.
    empty_rid = svc.create_recipe({"name": "Empty recipe"}, U)
    empty_v = conn.execute("SELECT id FROM wsh_versions WHERE recipe_id=?",
                           (empty_rid,)).fetchone()["id"]
    ed, _ = svc.create_labdip({"version_id": empty_v}, U)
    svc.set_labdip_verdict(ed, "approved", None, U)
    ok, err = svc.set_version_status(empty_v, "approved", U)
    ck(not ok and err == "no_steps", "a version with no steps cannot be approved for bulk")

    # A dip belonging to ANOTHER version must not unlock this one.
    other_rid = svc.create_recipe({"name": "Other recipe"}, U)
    other_v = conn.execute("SELECT id FROM wsh_versions WHERE recipe_id=?",
                           (other_rid,)).fetchone()["id"]
    svc.add_step(other_v, {"operation": "rinse", "minutes": 10, "liquor_ratio": 6,
                           "load_kg": 100, "temp_c": 30}, U)
    ok, err = svc.set_version_status(other_v, "approved", U)
    ck(not ok and err == "labdip_not_approved",
       "another version's approved dip does NOT unlock this one (the gate is per-version)")

    # -- H. lab dips ----------------------------------------------------------
    section("H. lab dips")
    d, err = svc.create_labdip({"version_id": 10 ** 9}, U)
    ck(d is None and err == "version_not_found", "a lab dip needs a real version")
    ok, err = svc.set_labdip_verdict(10 ** 9, "approved", None, U)
    ck(not ok and err == "labdip_not_found", "a verdict on a missing lab dip is refused")
    ok, err = svc.set_labdip_verdict(dip3, "nonsense", None, U)
    ck(not ok and err == "bad_verdict", "a nonsense verdict is refused")
    d, err = svc.create_labdip({"version_id": V3, "batch_id": 10 ** 9}, U)
    ck(d is None and err == "batch_not_found",
       "a lab dip cannot be pinned to a batch that does not exist")

    # A lab dip taken from a batch that ran a DIFFERENT version would let that
    # other version's physical evidence unlock this version for bulk.
    bid_ok, _ = svc.create_batch({"version_id": V3, "load_kg": 120, "act_minutes": 132,
                                  "act_temp_c": 55}, U)
    d, err = svc.create_labdip({"version_id": other_v, "batch_id": bid_ok}, U)
    ck(d is None and err == "batch_version_mismatch",
       "a dip cannot borrow the evidence of a batch that ran a different version")
    d, err = svc.create_labdip({"version_id": V3, "batch_id": bid_ok}, U)
    ck(d and not err, "a dip against its own batch is accepted (%r)" % (err,))

    # Revoking the dip that unlocked bulk must be loud: bulk is still running.
    b0 = bells(conn)
    ok, err = svc.set_labdip_verdict(dip3, "rejected", "shade drifted", U)
    ck(ok, "the approving dip can be revoked")
    crit = conn.execute("SELECT severity FROM notifications WHERE module='wash' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    ck(bells(conn) == b0 + 1 and crit["severity"] == "critical",
       "revoking the dip of a LIVE bulk standard raises a CRITICAL alert, not a quiet warning")

    # -- I. deviation tolerance ------------------------------------------------
    section("I. batch deviation tolerance")
    VE = conn.execute("SELECT v.id FROM wsh_versions v JOIN wsh_recipes r ON r.id=v.recipe_id "
                      "WHERE r.code='WR-ENZ-02'").fetchone()["id"]
    te = svc.version_totals(VE)
    ck(te["cycle_min"] == 100 and te["max_temp_c"] == 50 and te["load_kg"] == 100,
       "WR-ENZ-02 nominals: 100 min / 50 C peak bath / 100 kg")
    cases = [
        # load, act_min, act_temp, expected flags (independently derived)
        (100, 115, 50, []),           # exactly +15% time -> NOT flagged (strict >)
        (100, 116, 50, ["time"]),
        (100, 85, 50, []),            # exactly -15%
        (100, 84, 50, ["time"]),
        (100, 100, 55, []),           # exactly +5 C
        (100, 100, 56, ["temp"]),
        (100, 100, 45, []),           # exactly -5 C
        (100, 100, 44, ["temp"]),
        (110, 100, 50, []),           # exactly +10% load
        (111, 100, 50, ["load"]),
        (90, 100, 50, []),
        (89, 100, 50, ["load"]),
        (0, 0, 0, []),                # nothing recorded -> nothing judged, no /0
        (140, 160, 80, ["time", "temp", "load"]),
    ]
    for load, am, at, expect in cases:
        got = svc._deviation(te, load, am, at)
        flags = [w for w in ("time", "temp", "load") if got.startswith(w) or ("; " + w) in got]
        ck(flags == expect and ref_deviation(te, load, am, at) == expect,
           "deviation(load=%s, min=%s, temp=%s) -> %s" % (load, am, at, expect or "in tolerance"))
    empty_t = svc.totals([])
    ck(svc._deviation(empty_t, 100, 100, 100) == "",
       "a version with NO nominals (all zero) is never a division by zero")

    # -- J. batches -------------------------------------------------------------
    section("J. batches")
    b0 = bells(conn)
    n_before = nrows(conn, "wsh_batches")
    bid, err = svc.create_batch({"version_id": VE, "load_kg": 100, "act_minutes": 100,
                                 "act_temp_c": 50, "machine": "Washer 1"}, U)
    ck(bid and not err, "an in-tolerance batch is recorded (%r)" % (err,))
    ck(bells(conn) == b0, "an in-tolerance batch does NOT ring the bell")
    row = conn.execute("SELECT * FROM wsh_batches WHERE id=?", (bid,)).fetchone()
    ck(row["deviation"] is None and row["deviation_alerted"] == 0,
       "an in-tolerance batch stores no deviation")
    ck(row["batch_no"] == "WB-%d-%05d" % (date.today().year, bid),
       "batch_no is derived from the row id, not COUNT(*)+1")
    ck(row["recipe_id"] == conn.execute("SELECT recipe_id FROM wsh_versions WHERE id=?",
                                        (VE,)).fetchone()["recipe_id"],
       "recipe_id is denormalised FROM the version, it is not operator input")

    bid2, err = svc.create_batch({"version_id": VE, "load_kg": 100, "act_minutes": 160,
                                  "act_temp_c": 80}, U)
    ck(bells(conn) == b0 + 1, "an off-recipe batch rings the bell exactly once")
    row2 = conn.execute("SELECT * FROM wsh_batches WHERE id=?", (bid2,)).fetchone()
    ck(row2["deviation_alerted"] == 1 and "time" in row2["deviation"],
       "the off-recipe batch is flagged and marked as alerted")
    ck(nrows(conn, "wsh_batches") == n_before + 2, "no phantom rows were written")

    bid3, err = svc.create_batch({"version_id": 10 ** 9}, U)
    ck(bid3 is None and err == "version_not_found", "a batch needs a real version")
    bid4, err = svc.create_batch({"version_id": VE, "load_kg": -50}, U)
    ck(bid4 is None and err == "negative_value", "a negative load is refused")
    ck(nrows(conn, "wsh_batches") == n_before + 2, "the two refusals wrote nothing")

    # -- K. hostile ids on every entry point -------------------------------------
    section("K. hostile foreign ids - a clean reason, never a 500")
    HOSTILE = ["1e30", "9" * 40, "-1", "0", "abc", "", None, "None", "3.9", "inf", "nan",
               "1 OR 1=1", "'; DROP TABLE wsh_recipes; --"]
    for h in HOSTILE:
        errs = [raises(lambda h=h: svc.create_batch({"version_id": h, "order_id": h}, U)),
                raises(lambda h=h: svc.add_chemical(h, {"name": "x"}, U)),
                raises(lambda h=h: svc.create_labdip({"version_id": h, "batch_id": h}, U)),
                raises(lambda h=h: svc.new_version(R1, h, U)),
                raises(lambda h=h: svc.create_recipe({"name": "H", "order_id": h}, U)),
                raises(lambda h=h: svc.get_recipe(R1, h)),
                raises(lambda h=h: svc.set_version_status(h, "retired", U)),
                raises(lambda h=h: svc.set_labdip_verdict(h, "approved", None, U))]
        bad = [type(e).__name__ for e in errs if e is not None]
        ck(not bad, "hostile id %r raised nothing anywhere (%s)" % (h, bad or "clean"))
    ck(nrows(conn, "wsh_recipes") > 0, "the SQL-injection strings did not drop wsh_recipes")
    ck(nrows(conn, "wsh_batches", "version_id NOT IN (SELECT id FROM wsh_versions)") == 0,
       "no batch was written against a version that does not exist")
    ck(nrows(conn, "wsh_labdips",
             "batch_id IS NOT NULL AND batch_id NOT IN (SELECT id FROM wsh_batches)") == 0,
       "no lab dip points at a batch that does not exist")
    ck(nrows(conn, "wsh_recipes", "order_id IS NOT NULL AND order_id > 2147483647") == 0,
       "no out-of-range order id was ever stored")

    # -- L. recipe creation ------------------------------------------------------
    section("L. recipe creation")
    a = svc.create_recipe({"name": "Dup", "code": "WR-DUP-1"}, U)
    b = svc.create_recipe({"name": "Dup again", "code": "WR-DUP-1"}, U)
    ck(a and b is None, "a duplicate operator-typed code is refused, not an IntegrityError 500")
    auto = svc.create_recipe({"name": "Auto coded"}, U)
    code = conn.execute("SELECT code FROM wsh_recipes WHERE id=?", (auto,)).fetchone()["code"]
    ck(code == "WR-%04d" % auto, "an auto code is numbered from the row id: %s" % code)
    ck(nrows(conn, "wsh_versions", "recipe_id=?", (auto,)) == 1,
       "a new recipe always gets exactly one draft v1 - never a recipe with no version")
    svc.create_recipe({"name": "Clash", "code": "WR-%04d" % (auto + 2)}, U)
    nxt = svc.create_recipe({"name": "Next"}, U)
    ncode = conn.execute("SELECT code FROM wsh_recipes WHERE id=?", (nxt,)).fetchone()["code"]
    ck(ncode.startswith("WR-%04d" % nxt),
       "an auto code that a human already typed is suffixed, not a UNIQUE crash: %s" % ncode)
    bad = svc.create_recipe({"name": "Bad type", "wash_type": "; DROP TABLE"}, U)
    wt = conn.execute("SELECT wash_type FROM wsh_recipes WHERE id=?", (bad,)).fetchone()["wash_type"]
    ck(wt == "rinse", "an off-list wash_type falls back to the default, it is not stored")
    v_new, err = svc.new_version(10 ** 9, None, U)
    ck(v_new is None and err == "recipe_not_found", "new_version on a missing recipe is refused")
    n_v = nrows(conn, "wsh_versions")
    v_new, err = svc.new_version(R1, other_v, U)
    ck(v_new is None and err == "source_not_found",
       "a source version from ANOTHER recipe is refused")
    ck(nrows(conn, "wsh_versions") == n_v, "the refused new_version left no orphan draft behind")

    # -- M. reads / dashboard ------------------------------------------------------
    section("M. reads, filters and the dashboard")
    d = svc.dashboard()
    ck(d["recipes"] == nrows(conn, "wsh_recipes"), "dashboard recipe count matches the table")
    appr_ids = [v["id"] for v in conn.execute(
        "SELECT id FROM wsh_versions WHERE status='approved'").fetchall()]
    exp_avg = round(sum(svc.version_totals(i)["cycle_min"] for i in appr_ids)
                    / max(1, len(appr_ids)), 0)
    ck(d["avg_cycle_min"] == exp_avg,
       "avg cycle over APPROVED versions recomputed independently = %s" % exp_avg)
    ck(d["deviations"] == nrows(conn, "wsh_batches", "deviation IS NOT NULL"),
       "off-recipe batch count matches the table")
    rows = svc.list_recipes()
    ck(len(rows) == nrows(conn, "wsh_recipes"), "list_recipes returns every recipe")
    ck(all(r["versions"] >= 1 for r in rows), "every listed recipe has at least one version")
    stn = next(r for r in rows if r["code"] == "WR-STN-01")
    ck(stn["approved_version"] == 3 and stn["versions"] == 3,
       "the listed bulk standard for WR-STN-01 is v3")
    ck(all(r["latest_status"] == "draft" for r in svc.list_recipes(status="draft")),
       "the status filter really filters")
    ck(svc.list_recipes(wash_type="' OR 1=1 --") == [],
       "an injected wash_type filter matches nothing (parameterised)")
    ck(svc.get_recipe(10 ** 9) is None, "get_recipe on a missing id returns None (-> 404)")
    sel = svc.get_recipe(R1)["sel"]
    ck(sel["status"] == "approved",
       "the APPROVED version is selected by default, not the newest or the retired one")
    ck(svc.get_recipe(R1, "banana")["sel"]["id"] == sel["id"],
       "?v=banana falls back to the default version instead of raising")
    ck(svc.get_recipe(R1, other_v)["sel"]["id"] == sel["id"],
       "?v=<another recipe's version> cannot be smuggled into this recipe's page (IDOR)")
    ck(len(svc.list_labdips("approved")) == nrows(conn, "wsh_labdips", "verdict='approved'"),
       "the lab-dip verdict filter matches the table")
    ck(svc.list_labdips("' OR 1=1 --") == [], "an injected verdict filter matches nothing")
    ck(len(svc.list_batches(1)) == 1, "the batch limit is honoured")
    ck(all(r.get("version") is not None for r in svc.list_batches()),
       "no batch row loses its version through the join")
    conn.close()

# ===========================================================================
# N. routes + real template rendering
# ===========================================================================
section("N. route security and real template rendering")
from app.routes.wash import bp as wash_bp                   # noqa: E402
# The orchestrator may or may not have spliced the blueprint into create_app() yet;
# this file has to run either way.
if "wash" not in app.blueprints:
    app.register_blueprint(wash_bp)

with app.app_context():
    from app.db import get_db
    from app import security as sec
    from app.wash.constants import WSH_PERMISSIONS, WSH_ROLE_PERMS, WSH_ROLE_LABELS
    # The orchestrator has not merged this module's RBAC yet, so do it here and
    # exercise the permission decorators for real.
    sec._merge_module_rbac(WSH_PERMISSIONS, WSH_ROLE_PERMS, WSH_ROLE_LABELS)
    conn = get_db()
    admin_id = conn.execute("SELECT id FROM users WHERE role='super_admin' LIMIT 1").fetchone()["id"]
    if not conn.execute("SELECT id FROM users WHERE username='wash_nobody'").fetchone():
        conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active) "
                     "VALUES (?,?,?,?,1)", ("wash_nobody", "x", "No Perms", "normal_user"))
        conn.commit()
    nobody = conn.execute("SELECT id FROM users WHERE username='wash_nobody'").fetchone()["id"]
    R1 = conn.execute("SELECT id FROM wsh_recipes WHERE code='WR-STN-01'").fetchone()["id"]
    V1 = conn.execute("SELECT id FROM wsh_versions WHERE recipe_id=? LIMIT 1",
                      (R1,)).fetchone()["id"]
    D1 = conn.execute("SELECT id FROM wsh_labdips LIMIT 1").fetchone()["id"]
    conn.close()

GETS = ["/wash/", "/wash/recipes", "/wash/batches", "/wash/labdips", "/wash/recipes/%d" % R1]
POSTS = ["/wash/recipes", "/wash/recipes/%d/version" % R1, "/wash/versions/%d/step" % V1,
         "/wash/chemical", "/wash/versions/%d/status" % V1, "/wash/batches",
         "/wash/labdips", "/wash/labdips/%d/verdict" % D1]

c = app.test_client()


def token():
    """The platform's CSRF token is minted during a request, so it only exists in
    the session AFTER one. Posting without it is a 400 from app.csrf and would mask
    whatever the auth decorators do — which is the whole point of this section."""
    c.get("/login")
    with c.session_transaction() as s:
        return s.get("_csrf_token") or ""


def hit(url, **kw):
    return c.get(url, **kw) if url in GETS else c.post(url, data={"_csrf": token()}, **kw)


for url in GETS + POSTS:
    r = hit(url)
    ck(r.status_code in (302, 308) and "/login" in (r.headers.get("Location") or ""),
       "anonymous %s -> redirected to login (got %s)" % (url, r.status_code))

with c.session_transaction() as s:
    s["uid"] = nobody
    s["ep"] = 0
for url in GETS + POSTS:
    r = hit(url)
    ck(r.status_code == 403, "under-privileged %s -> 403 (got %s)" % (url, r.status_code))

with c.session_transaction() as s:
    s["uid"] = admin_id
    s["ep"] = 0
TOK = token()
for url in ["/wash/recipes/%d/version" % R1, "/wash/versions/%d/step" % V1, "/wash/chemical",
            "/wash/versions/%d/status" % V1, "/wash/labdips/%d/verdict" % D1]:
    ck(c.get(url).status_code == 405, "GET %s is refused (405) - POST only" % url)

# A state-changing POST with no CSRF token must never reach a service.
r = c.post("/wash/batches", data={"version_id": V1, "load_kg": 5})
ck(r.status_code in (302, 400), "a POST with no CSRF token is refused (got %s)" % r.status_code)

# Flask's bare <int:> converter is UNBOUNDED; an id past 2^31-1 cannot exist in an
# INTEGER column and used to reach the driver as an OverflowError -> 500.
HUGE = 99999999999999999999
ck(c.get("/wash/recipes/%d" % HUGE).status_code == 404,
   "GET /wash/recipes/<out-of-range id> is a clean 404, not an OverflowError 500")
for name, url, extra in [
        ("version", "/wash/recipes/%d/version" % HUGE, {"source_version_id": V1}),
        ("step", "/wash/versions/%d/step" % HUGE, {"operation": "rinse", "minutes": "5"}),
        ("status", "/wash/versions/%d/status" % HUGE, {"status": "retired"}),
        ("verdict", "/wash/labdips/%d/verdict" % HUGE, {"verdict": "approved"})]:
    d = {"_csrf": TOK}
    d.update(extra)
    ck(c.post(url, data=d).status_code == 404,
       "POST %s with an out-of-range id is a clean 404, not a 500" % name)

for url in GETS:
    r = c.get(url)
    body = r.get_data(as_text=True)
    ck(r.status_code == 200 and "</html>" in body.lower(),
       "GET %s renders a full page for an authorised user (got %s)" % (url, r.status_code))
ck(c.get("/wash/recipes/999999").status_code == 404,
   "an unknown recipe id is a clean 404, not a 500")
ck(c.get("/wash/recipes/%d?v=99999999" % R1).status_code == 200,
   "?v=<missing> still renders (falls back to the default version)")
ck(c.get("/wash/recipes/%d?v=1e30" % R1).status_code == 200,
   "?v=1e30 still renders, no OverflowError 500")
ck(c.get("/wash/recipes?wash_type=%27+OR+1%3D1+--&status=zzz").status_code == 200,
   "injected list filters render an empty list, not a 500")

with app.app_context():
    from app.wash import services as svc2
    empty_rid = svc2.create_recipe({"name": "Renders empty"}, {"username": "adversary"})
body = c.get("/wash/recipes/%d" % empty_rid).get_data(as_text=True)
kpi = body.split('data-i18n="wsh.kpi.impact"')[0][-320:]
ck("pb-kpi-r" not in kpi,
   "a brand-new EMPTY recipe does not render its impact KPI as red/high")

# Referer is a CLIENT-controlled header: redirect(request.referrer) is an open redirect.
for ref in ("https://evil.example/steal", "//evil.example/steal",
            "http://evil.example@localhost/x"):
    r = c.post("/wash/chemical", data={"_csrf": TOK, "step_id": "999999", "name": "x"},
               headers={"Referer": ref})
    ck(r.status_code in (302, 303) and "evil.example" not in (r.headers.get("Location") or ""),
       "off-site Referer %r is not the redirect target (no open redirect)" % ref)
r = c.post("/wash/chemical", data={"_csrf": TOK, "step_id": "999999", "name": "x"},
           headers={"Referer": "http://localhost/wash/recipes/%d" % R1})
ck("/wash/recipes/%d" % R1 in (r.headers.get("Location") or ""),
   "a same-site Referer IS honoured, so the operator lands back on the form")
# Every state-changing route that bounces off the Referer.
for url, extra in [("/wash/versions/%d/step" % V1, {"operation": "rinse"}),
                   ("/wash/versions/%d/status" % V1, {"status": "retired"}),
                   ("/wash/labdips/%d/verdict" % D1, {"verdict": "approved"})]:
    d = {"_csrf": TOK}
    d.update(extra)
    r = c.post(url, data=d, headers={"Referer": "https://evil.example/steal"})
    ck("evil.example" not in (r.headers.get("Location") or ""),
       "%s does not honour an off-site Referer either" % url)

# Reason codes are identifiers, not sentences: none may reach an operator verbatim.
from app.routes.wash import _MSG                              # noqa: E402
CODES = ["negative_value", "value_out_of_range", "version_frozen", "operation_required",
         "bad_operation", "bad_status", "bad_verdict", "no_steps", "cannot_reopen",
         "labdip_not_approved", "recipe_not_found", "version_not_found", "step_not_found",
         "batch_not_found", "labdip_not_found", "batch_version_mismatch",
         "source_not_found", "name_required"]
missing = [x for x in CODES if x not in _MSG]
ck(not missing, "every service reason code has an operator-readable message (%s)" % (missing or "all"))

# -- O. the dashboard KPI must not contradict the recipe sheets --------------
section("O. dashboard aggregates vs the audited totals() path")
with app.app_context():
    from app.db import get_db
    from app.wash import services as svc3
    conn = get_db()
    AV = conn.execute("SELECT id FROM wsh_versions WHERE status='approved' LIMIT 1").fetchone()["id"]
    base = svc3.dashboard()["avg_cycle_min"]
    # totals() floors a negative/NULL minutes to 0 via _nn. A bare SUM(s.minutes) in
    # the dashboard did not, so one bad row made the KPI disagree with every sheet.
    conn.execute("UPDATE wsh_steps SET minutes=-1000 WHERE version_id=? AND step_no=1", (AV,))
    conn.commit()
    got = svc3.dashboard()["avg_cycle_min"]
    appr = [v["id"] for v in conn.execute(
        "SELECT id FROM wsh_versions WHERE status='approved'").fetchall()]
    exp = round(sum(svc3.version_totals(i)["cycle_min"] for i in appr) / max(1, len(appr)), 0)
    ck(got >= 0, "a negative stored minutes row cannot make the avg-cycle KPI negative (%s)" % got)
    ck(got == exp, "avg-cycle KPI still equals the recompute through totals() (%s)" % exp)
    conn.execute("UPDATE wsh_steps SET minutes=0 WHERE version_id=? AND step_no=1", (AV,))
    conn.execute("UPDATE wsh_steps SET minutes=NULL WHERE version_id=? AND step_no=1", (AV,))
    conn.commit()
    ck(svc3.dashboard()["avg_cycle_min"] >= 0, "a NULL minutes row does not NULL the whole KPI")
    conn.close()

# -- P. i18n keys ------------------------------------------------------------
section("P. i18n key inventory")
KEYS = set()
for p in sorted((REPO / "app" / "templates" / "wash").glob("*.html")):
    KEYS |= set(re.findall(r'data-i18n="([^"]+)"', p.read_text(encoding="utf-8")))
ck(KEYS and all(k.startswith("wsh.") for k in KEYS),
   "all %d data-i18n keys are namespaced under wsh." % len(KEYS))
print("  keys: " + " ".join(sorted(KEYS)))

# ===========================================================================
print("\n%d checks, %d failed" % (COUNT[0], len(FAILS)))
for f in FAILS:
    print("  FAILED: " + f)
sys.exit(1 if FAILS else 0)
