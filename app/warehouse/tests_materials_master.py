"""Material master, multi-roll receipt, split, quarantine availability and material
identity on PR receipts — isolated throwaway database, demo seed OFF (the default).

Run: python -m app.warehouse.tests_materials_master
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="wh_master_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ.pop("TC_SEED_DEMO", None)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                             # noqa: E402
config.Config.DATABASE_URL = ""
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


app = create_app()
with app.app_context():
    from app.db import get_db
    from app.warehouse import services as svc

    U = {"username": "master-selftest"}

    def q(sql, *args):
        conn = get_db()
        try:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def mat(code):
        return q("SELECT * FROM wh_materials WHERE code=?", code)[0]

    def reconciles(mid):
        m = mat_by_id(mid)
        rolls = q("SELECT COALESCE(SUM(remaining_m),0) s FROM wh_rolls WHERE material_id=?", mid)[0]["s"]
        led = q("SELECT COALESCE(SUM(qty),0) s FROM wh_movements WHERE material_id=?", mid)[0]["s"]
        per_roll = all(abs(r["remaining_m"] - q("SELECT COALESCE(SUM(qty),0) s FROM wh_movements "
                                                 "WHERE roll_id=?", r["id"])[0]["s"]) < 1e-6
                       for r in q("SELECT id, remaining_m FROM wh_rolls WHERE material_id=?", mid))
        return abs(m["stock_qty"] - rolls) < 1e-6 and abs(rolls - led) < 1e-6 and per_roll

    def mat_by_id(mid):
        return q("SELECT * FROM wh_materials WHERE id=?", mid)[0]

    print("1. demo seed is off by default")
    check("no demo materials", not q("SELECT id FROM wh_materials WHERE notes='DEMO seed data'"))

    print("2. material master edit / deactivate")
    svc.create_material({"code": "JER-WHT", "name": "Jersey 180", "color": "White",
                         "roll_tracked": 1, "width_cm": 180}, U)
    svc.create_material({"code": "JER-NVY", "name": "Jersey 180", "color": "Navy",
                         "roll_tracked": 1, "width_cm": 180}, U)
    svc.create_material({"code": "BTN-18", "name": "Button 18L", "kind": "trim", "uom": "pcs"}, U)
    w, n, b = mat("JER-WHT")["id"], mat("JER-NVY")["id"], mat("BTN-18")["id"]
    ok, msg = svc.update_material(b, {"name": "Button 18L 4-hole", "kind": "trim", "uom": "box",
                                      "reorder_level": "50", "min_level": ""}, U)
    check("edit before history may change uom", ok and mat("BTN-18")["uom"] == "box", msg)
    check("blank number means 0", mat("BTN-18")["min_level"] == 0)
    svc.receive_qty(b, 10, 1, U)
    check("uom locked after stock", svc.update_material(b, {"uom": "pcs"}, U) == (False, "uom_locked"))
    check("bad kind refused", svc.update_material(b, {"kind": "spaceship"}, U)[1] == "bad_kind")
    check("negative refused", svc.update_material(b, {"reorder_level": "-1"}, U)[1] == "bad_number")
    ok, _ = svc.set_material_active(b, False, U)
    check("deactivated + hidden from pickers", ok and b not in [r["id"] for r in svc.list_materials()])
    check("listed under inactive", b in [r["id"] for r in svc.list_materials(inactive=True)])
    check("receipt into inactive refused", svc.receive_qty(b, 5, 1, U) == (False, "material_inactive"))
    check("reactivate", svc.set_material_active(b, True, U)[0] and mat("BTN-18")["is_active"] == 1)

    print("3. multi-roll receipt is one transaction")
    ok, nos = svc.receive_rolls(w, {"grn_ref": "GRN-T1", "unit_cost": "3"},
                                [{"roll_no": "R-1", "length_m": "100", "shade_lot": "L1"},
                                 {"roll_no": "R-2", "length_m": "80", "shade_lot": "L1"},
                                 {"roll_no": "", "length_m": "60", "shade_lot": "L2"}], U)
    check("three rolls booked", ok and len(nos) == 3 and abs(mat("JER-WHT")["stock_qty"] - 240) < 1e-6, nos)
    ok, msg = svc.receive_rolls(w, {}, [{"roll_no": "R-9", "length_m": "10"},
                                        {"roll_no": "R-1", "length_m": "10"}], U)
    check("duplicate in batch rolls back everything",
          not ok and "row 2" in msg and not q("SELECT id FROM wh_rolls WHERE roll_no='R-9'"), msg)
    check("ledger reconciles after receipt", reconciles(w))

    print("4. roll edit + split")
    r1 = q("SELECT id FROM wh_rolls WHERE roll_no='R-1'")[0]["id"]
    check("roll edit", svc.update_roll(r1, {"shade_lot": "L1B", "bin": "A-7", "width_cm": "178"}, U)[0]
          and q("SELECT shade_lot, bin FROM wh_rolls WHERE id=?", r1)[0] == {"shade_lot": "L1B", "bin": "A-7"})
    check("split must sum", svc.split_roll(r1, ["50", "40"], U) == (False, "split_sum_mismatch"))
    check("split needs 2", svc.split_roll(r1, ["100"], U) == (False, "bad_split"))
    ok, new = svc.split_roll(r1, ["50", "30", "20"], U)
    check("split into three", ok and len(new) == 2, new)
    check("parent keeps first length", q("SELECT remaining_m FROM wh_rolls WHERE id=?", r1)[0]["remaining_m"] == 50)
    check("children inherit lot", all(r["shade_lot"] == "L1B" for r in
                                      q("SELECT shade_lot FROM wh_rolls WHERE roll_no IN (?,?)", *new)))
    check("split-off metres cannot be 'returned' to the parent",
          svc.return_to_store(r1, 10, U) == (False, "exceeds_roll_length"))
    check("parent shrinks to its share", q("SELECT length_m, status FROM wh_rolls WHERE id=?", r1)[0]
          == {"length_m": 50, "status": "available"})
    check("1 mm tolerance on the sum", svc.split_roll(q("SELECT id FROM wh_rolls WHERE roll_no=?", new[0])[0]["id"], ["10.0004", "20"], U)[0])
    check("header unchanged + ledger reconciles", abs(mat("JER-WHT")["stock_qty"] - 240) < 1e-6 and reconciles(w))

    print("5. quarantine is not available")
    svc.update_material(w, {"reorder_level": "200"}, U)
    r2 = q("SELECT id FROM wh_rolls WHERE roll_no='R-2'")[0]["id"]
    svc.set_roll_hold(r2, True, U)
    row = [r for r in svc.list_materials() if r["id"] == w][0]
    check("available excludes quarantined roll", abs(row["available"] - 160) < 1e-6 and row["quarantined"] == 80,
          row["available"])
    check("short on availability", row["short"] and w in [r["id"] for r in svc.list_materials(low_only=True)])
    check("pick plan skips quarantined roll", r2 not in [x["roll_id"] for x in svc.plan_pick(w, 150)["rolls"]])

    print("6. material identity on PR lines")
    conn = get_db()
    try:
        check("code wins", svc._match_material(conn, "JER-NVY — Jersey 180")[0]["id"] == n)
        check("shared name is ambiguous", svc._match_material(conn, "Jersey 180") == (None, "ambiguous_material"))
        check("unique name still matches", svc._match_material(conn, "Button 18L 4-hole")[0]["id"] == b)
        check("unknown", svc._match_material(conn, "Office chair") == (None, "no_matching_material"))
        check("legacy resolver returns None when ambiguous", svc._resolve_material(conn, "jersey 180") is None)
    finally:
        conn.close()

print("FAILED: " + ", ".join(FAILS) if FAILS else "all checks passed")
sys.exit(1 if FAILS else 0)
