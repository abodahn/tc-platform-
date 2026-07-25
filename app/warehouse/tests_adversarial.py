"""
Adversarial verification of the warehouse module. Isolated throwaway DB.

Every check here is an attack, not a demo: it tries to make the ledger lie, the
header drift from the rolls, a counter go NaN/negative, a reservation outlive the
batch that created it, or a route corrupt a row it was not given.

    python app/warehouse/tests_adversarial.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wh_adv_"))
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
N = [0]


def ck(cond, label, extra=""):
    N[0] += 1
    print(("  ok   " if cond else "  FAIL ") + label + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(label)


def head(t):
    print(f"\n--- {t} ---")


app = create_app()

with app.app_context():
    from app.db import get_db
    from app.warehouse.schema import create_and_seed
    from app.warehouse import services as svc

    U = {"username": "adv"}
    _f = svc._f

    def q(sql, args=()):
        c = get_db()
        try:
            r = c.execute(sql, args).fetchone()
            return None if r is None else (r[0] if len(r.keys()) == 1 else dict(r))
        finally:
            c.close()

    def rows(sql, args=()):
        c = get_db()
        try:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
        finally:
            c.close()

    def inv(note=""):
        """The invariants this module claims never to break."""
        bad = []
        for m in rows("SELECT id,code,roll_tracked,stock_qty,reserved_qty FROM wh_materials"):
            s = _f(m["stock_qty"])
            if s < 0:
                bad.append(f"{m['code']} header negative {s}")
            if _f(m["reserved_qty"]) < 0:
                bad.append(f"{m['code']} reserved negative")
            if m["roll_tracked"]:
                t = _f(q("SELECT COALESCE(SUM(remaining_m),0) FROM wh_rolls WHERE material_id=?",
                         (m["id"],)))
                if abs(t - s) > 1e-6:
                    bad.append(f"{m['code']} header {s} != rolls {t}")
                # the header mirror of the roll reservations: availability is read from
                # the header (materials list, reorder sweep, dashboard), so it has to
                # carry the same committed metres the rolls do.
                tr = _f(q("SELECT COALESCE(SUM(reserved_m),0) FROM wh_rolls WHERE material_id=?",
                          (m["id"],)))
                if abs(tr - _f(m["reserved_qty"])) > 1e-6:
                    bad.append(f"{m['code']} header reserved {m['reserved_qty']} != rolls {tr}")
            led = _f(q("SELECT COALESCE(SUM(qty),0) FROM wh_movements WHERE material_id=?",
                       (m["id"],)))
            if abs(led - s) > 1e-6:
                bad.append(f"{m['code']} ledger {led} != header {s}")
        if q("SELECT COUNT(*) FROM wh_rolls WHERE remaining_m<0 OR reserved_m<0"):
            bad.append("negative roll counter")
        over = q("SELECT COUNT(*) FROM wh_rolls WHERE reserved_m>remaining_m+1e-9")
        if over:
            bad.append(f"{over} roll(s) reserved beyond what is on them")
        if q("SELECT COUNT(*) FROM wh_fg WHERE packed_qty<shipped_qty"):
            bad.append("FG shipped more than packed")
        if q("SELECT COUNT(*) FROM (SELECT movement_no FROM wh_movements GROUP BY movement_no "
             "HAVING COUNT(*)>1) x"):
            bad.append("duplicate movement_no")
        if q("SELECT COUNT(*) FROM wh_movements WHERE movement_no IS NULL"):
            bad.append("unnumbered movement")
        return bad

    # ------------------------------------------------------------------ seed
    head("idempotency: create_and_seed x3")
    conn = get_db()
    create_and_seed(conn); conn.commit()
    snap = "SELECT (SELECT COUNT(*) FROM wh_materials) a,(SELECT COUNT(*) FROM wh_rolls) b," \
           "(SELECT COUNT(*) FROM wh_movements) c,(SELECT COUNT(*) FROM wh_fg) d"
    s1 = dict(conn.execute(snap).fetchone())
    for _ in range(3):
        create_and_seed(conn); conn.commit()
    s3 = dict(conn.execute(snap).fetchone())
    conn.close()
    ck(s1 == s3 and s1["a"] > 0, "3x create_and_seed duplicates/destroys nothing", f"{s1} -> {s3}")
    ck(inv() == [], "invariants hold on the seeded DB", str(inv()))

    MID = {r["code"]: r["id"] for r in rows("SELECT id,code FROM wh_materials")}
    FAB, NVY, BTN, THR = MID["FAB-JER-WHT"], MID["FAB-JER-NVY"], MID["TRM-BTN-18L"], MID["TRM-THR-40"]

    # ------------------------------------------------- A1 raced batch reserve
    head("A1: a partly-reserved batch must strand nothing")
    real_try = svc._try_reserve
    calls = [0]

    def flaky(c, target, row_id, qty):
        calls[0] += 1
        return False if calls[0] == 2 else real_try(c, target, row_id, qty)

    svc._try_reserve = flaky
    ok, msg, plan = svc.reserve_for_order(9101, NVY, 400, U)     # spans 2 rolls of one lot
    svc._try_reserve = real_try
    ck(not ok and msg == "raced", "the raced batch reports failure", str(msg))
    stranded = q("SELECT COUNT(*) FROM wh_allocations WHERE order_id=9101 AND status='reserved'")
    held = _f(q("SELECT COALESCE(SUM(reserved_m),0) FROM wh_rolls WHERE material_id=?", (NVY,)))
    ck(stranded == 0, "no allocation row survives a rolled-back batch", f"{stranded} rows")
    ck(abs(held) < 1e-9, "no reserved metres survive a rolled-back batch", str(held))

    # ------------------------------------------- A2 phantom alloc vs live one
    head("A2: an abandoned batch must not eat a live reservation")
    r1 = q("SELECT id, remaining_m FROM wh_rolls WHERE material_id=? ORDER BY received_at, id "
           "LIMIT 1", (NVY,))
    ok, msg, _p = svc.reserve_for_order(9102, NVY, r1["remaining_m"], U)
    ck(ok, "a second order reserves the whole first roll", str(msg))
    res_before = _f(q("SELECT reserved_m FROM wh_rolls WHERE id=?", (r1["id"],)))
    ok2, msg2 = svc.issue_reserved(9101, U)                       # the abandoned order
    ck(not ok2, "issuing the abandoned order finds nothing", str(msg2))
    res_after = _f(q("SELECT reserved_m FROM wh_rolls WHERE id=?", (r1["id"],)))
    ck(abs(res_after - res_before) < 1e-9, "the live reservation is untouched",
       f"{res_before} -> {res_after}")
    ck(inv() == [], "invariants hold after the abandoned batch", str(inv()))
    for a in svc.open_allocations():
        svc.release_allocation(a["id"], U)

    # -------------------------------------------------- A3 NaN / Inf attacks
    head("A3: NaN and Infinity quantities")
    before = _f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (BTN,)))
    for bad in ("nan", "inf", "-inf", "1e400", "NaN"):
        svc.receive_qty(BTN, bad, "1.0", U)
        svc.adjust_stock(BTN, bad, "attack", U)
    ck(_f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (BTN,))) == before,
       "no NaN/Inf quantity moves stock", str(before))
    svc.receive_qty(BTN, 100, "1e400", U)
    avg = _f(q("SELECT avg_cost FROM wh_materials WHERE id=?", (BTN,)))
    ck(avg == avg and avg < 1e6, "an infinite unit price cannot poison avg_cost", str(avg))
    svc.adjust_stock(BTN, -100, "undo", U)
    ck(inv() == [], "invariants survive the NaN/Inf attack", str(inv()))

    # --------------------------------- A4 header writes on roll-tracked stock
    head("A4: a roll-tracked header can only move through its rolls")
    h0 = _f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (FAB,)))
    ok, msg = svc.adjust_stock(FAB, -50, "phantom shrink", U)
    ck(not ok and _f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (FAB,))) == h0,
       "header adjust on a roll-tracked material refused", f"{msg}")
    ok, msg = svc.receive_qty(FAB, 100, 3.0, U)
    ck(not ok and _f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (FAB,))) == h0,
       "quantity receipt on a roll-tracked material refused", f"{msg}")

    # ------------------------------------------- A5 cross-material roll write
    head("A5: a roll movement pointed at the wrong material")
    rid = q("SELECT id FROM wh_rolls WHERE material_id=? AND remaining_m>0 ORDER BY id LIMIT 1",
            (FAB,))
    nvy0 = _f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (NVY,)))
    c = get_db(); c.execute("UPDATE wh_materials SET low_alerted=1"); c.commit(); c.close()
    ok, msg = svc.adjust_stock(NVY, -10, "cross-material", U, roll_id=rid)   # roll belongs to FAB
    ck(_f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (NVY,))) == nvy0 and inv() == [],
       "a roll movement cannot debit another material's header", f"{ok}/{msg}")
    ok2, msg2 = svc.adjust_stock(NVY, +10, "cross-material +", U, roll_id=rid)
    ck(_f(q("SELECT low_alerted FROM wh_materials WHERE id=?", (FAB,))) == 0,
       "a positive roll adjustment clears the ROLL's material flag",
       str(_f(q("SELECT low_alerted FROM wh_materials WHERE id=?", (FAB,)))))
    ck(_f(q("SELECT low_alerted FROM wh_materials WHERE id=?", (NVY,))) == 1,
       "...and leaves the unrelated posted material's flag alone",
       str(_f(q("SELECT low_alerted FROM wh_materials WHERE id=?", (NVY,)))))
    svc.adjust_stock(None, -10, "undo", U, roll_id=rid)

    # ------------------------------------------------------ A6 finished goods
    head("A6: finished goods")
    ok1, m1 = svc.fg_move(None, "STY-X", "White", "M", 100, "pack", U)
    ck(not ok1 and m1 == "order_required" and
       q("SELECT COUNT(*) FROM wh_fg WHERE style_code='STY-X'") == 0,
       "an order-less pack is refused (NULL order_id defeats ux_wh_fg_sku)", str(m1))
    ck(not svc.fg_move(0, "STY-X", "W", "M", 100, "pack", U)[0], "order_id 0 refused")
    svc.fg_move(9001, "STY-A", "Navy", "L", 500, "pack", U)
    svc.fg_move(9001, "STY-A", "Navy", "L", 200, "ship", U)
    ok, msg = svc.fg_move(9001, "STY-A", "Navy", "L", 301, "ship", U)
    fg = q("SELECT packed_qty p, shipped_qty s FROM wh_fg WHERE order_id=9001 AND size='L'")
    ck(not ok and _f(fg["p"]) == 500 and _f(fg["s"]) == 200,
       "shipping more than packed is refused, nothing written", f"{msg}")
    ok, msg = svc.fg_move(9001, "STY-A", "Navy", "L", 300, "ship", U)
    fg = q("SELECT packed_qty p, shipped_qty s FROM wh_fg WHERE order_id=9001 AND size='L'")
    ck(ok and _f(fg["p"]) - _f(fg["s"]) == 0, "shipping exactly the balance lands on zero")
    ck(not svc.fg_move(9001, "STY-A", "Navy", "L", "nan", "ship", U)[0], "FG 'nan' refused")
    ck(not svc.fg_move(9001, "STY-A", "Navy", "L", 5, "delete", U)[0], "unknown FG action refused")

    # --------------------------------------------------------- A7 float drift
    head("A7: 400 partial cuts of 0.1 m")
    r = q("SELECT id, remaining_m FROM wh_rolls WHERE material_id=? AND status='available' "
          "ORDER BY id DESC LIMIT 1", (FAB,))
    drift_roll, start = r["id"], _f(r["remaining_m"])
    for _ in range(400):
        if not svc.adjust_stock(None, -0.1, "drift", U, roll_id=drift_roll)[0]:
            break
    end = _f(q("SELECT remaining_m FROM wh_rolls WHERE id=?", (drift_roll,)))
    ck(abs((start - 40.0) - end) < 1e-9, "400 x 0.1 m leaves the roll exact",
       f"{start} -> {end} (want {start - 40.0})")
    ck(inv() == [], "invariants hold after 400 partial cuts", str(inv()))

    # ----------------------------------------------------- A8 the pick itself
    head("A8: the plan may never propose more than was asked for")
    for req in (1.2346, 0.0004, 99.99949, 250.00051, 7.7777777, 1e9):
        p = svc.plan_pick(FAB, req)
        total = round(sum(x["take_m"] for x in p["rolls"]), 9)
        ck(total <= req + 1e-9, f"plan for {req} takes no more than asked", str(total))
        # 'picked' is the display figure (3 dp) — nothing is written from it; what is
        # reserved and cut is take_m, checked above to the last micron.
        ck(p["picked"] <= req + 5e-4, f"picked <= required for {req}", str(p["picked"]))
    ok, msg, plan = svc.issue_to_order(9001, FAB, 10 ** 9, U)
    ck(not ok and msg == "shortfall", "an absurd requirement is a shortfall, not an over-issue", msg)
    qr = q("SELECT id, material_id, remaining_m FROM wh_rolls WHERE status='quarantine' LIMIT 1")
    p = svc.plan_pick(qr["material_id"], 10)
    ck(all(x["roll_id"] != qr["id"] for x in p["rolls"]), "quarantined roll never appears in a pick")
    wide = q("SELECT width_cm FROM wh_rolls WHERE material_id=? AND remaining_m>0 LIMIT 1", (FAB,))
    ck(svc.plan_pick(FAB, 100)["rolls"] and not svc.plan_pick(FAB, 100, None, wide + 10)["rolls"],
       "a roll narrower than the marker is excluded, not substituted")
    ck(svc.plan_pick(None, 10)["shortfall"] == 10, "plan_pick(None material) is safe")
    ck(svc.plan_pick(FAB, "abc")["picked"] == 0, "plan_pick(garbage qty) is safe")
    ck(svc.plan_pick(FAB, -5)["picked"] == 0, "plan_pick(negative) is safe")

    # --------------------------------------------------- A9 reserve / release
    head("A9: reserve, release, double-release")
    avail = _f(q("SELECT COALESCE(SUM(remaining_m-reserved_m),0) FROM wh_rolls WHERE material_id=? "
                 "AND status IN ('available','partial')", (FAB,)))
    ok, msg, _p = svc.reserve_for_order(9201, FAB, avail, U)
    ck(ok, "reserving the entire free balance succeeds", f"{msg} {avail}")
    ck(not svc.reserve_for_order(9202, FAB, 1, U)[0], "a second picker cannot take reserved metres")
    ck(inv() == [], "invariants hold with everything reserved", str(inv()))
    aids = [a["id"] for a in svc.open_allocations()]
    for a in aids:
        svc.release_allocation(a, U)
    for a in aids:
        ck(not svc.release_allocation(a, U)[0], "double release refused") if a == aids[0] else None
    stuck = _f(q("SELECT COALESCE(SUM(reserved_m),0) FROM wh_rolls WHERE material_id=?", (FAB,)))
    ck(stuck == 0, "releasing every allocation strands nothing", str(stuck))

    # ------------------------------------------------------------ A10 returns
    head("A10: returns")
    rr = q("SELECT id, length_m, remaining_m FROM wh_rolls WHERE remaining_m>0 "
           "AND remaining_m<length_m LIMIT 1")
    room = _f(rr["length_m"]) - _f(rr["remaining_m"])
    ck(svc.return_to_store(rr["id"], room + 0.01, U)[1] == "exceeds_roll_length",
       "cannot return more than the roll ever held")
    ck(not svc.return_to_store(rr["id"], "nan", U)[0], "return of 'nan' refused")
    ck(svc.return_to_store(999999, 5, U)[1] == "roll_not_found", "return against unknown roll refused")
    n_rolls = q("SELECT COUNT(*) FROM wh_rolls")
    svc.return_to_store(rr["id"], room, U)
    ck(q("SELECT COUNT(*) FROM wh_rolls") == n_rolls, "a return never creates a second roll")
    ck(q("SELECT status FROM wh_rolls WHERE id=?", (rr["id"],)) == "available",
       "a fully returned roll is 'available' again")

    # ------------------------------------------------------ A11 roll numbers
    head("A11: roll numbering")
    existing = q("SELECT roll_no FROM wh_rolls LIMIT 1")
    try:
        ok, msg = svc.receive_roll(FAB, {"length_m": 50, "roll_no": existing}, U)
        crashed = None
    except Exception as e:                                   # noqa: BLE001
        ok, msg, crashed = False, repr(e), e
    ck(crashed is None and not ok, "a duplicate roll number is rejected, not a 500", str(msg))
    # an operator types the number the NEXT-but-one auto roll will generate
    nxt = _f(q("SELECT COALESCE(MAX(id),0) FROM wh_rolls"))
    clash = svc.doc_no("ROLL", int(nxt) + 2)
    ok, msg = svc.receive_roll(FAB, {"length_m": 10, "roll_no": clash}, U)
    ck(ok, "the manual roll number is accepted", str(msg))
    try:
        ok, msg = svc.receive_roll(FAB, {"length_m": 10}, U)
        crashed = None
    except Exception as e:                                   # noqa: BLE001
        ok, msg, crashed = False, repr(e), e
    ck(crashed is None, "the next auto-numbered receipt does not raise", str(crashed))
    ck(ok, "the next auto-numbered receipt still succeeds", str(msg))
    ck(inv() == [], "invariants hold after the numbering clash", str(inv()))

    # ------------------------------------------------- A12 weighted-avg cost
    head("A12: weighted-average cost, recomputed by hand")
    m = q("SELECT id, stock_qty, avg_cost FROM wh_materials WHERE id=?", (THR,))
    expect = round((_f(m["stock_qty"]) * _f(m["avg_cost"]) + 60 * 1.90) /
                   (_f(m["stock_qty"]) + 60), 4)
    svc.receive_qty(THR, 60, 1.90, U)
    got = _f(q("SELECT avg_cost FROM wh_materials WHERE id=?", (THR,)))
    ck(abs(got - expect) < 1e-9, "moving average matches the hand calculation", f"{got} vs {expect}")
    for p in ("", "0", "rubbish", "-3", None):
        svc.receive_qty(THR, 10, p, U)
    ck(abs(_f(q("SELECT avg_cost FROM wh_materials WHERE id=?", (THR,))) - expect) < 1e-9,
       "blank / 0 / garbage / negative price never moves avg_cost")

    # -------------------------------------------- A13 exactly one movement
    head("A13: exactly one movement per mutation, none per rejection")
    n0 = q("SELECT COUNT(*) FROM wh_movements")
    svc.receive_qty(BTN, 1000, 0.04, U)
    ck(q("SELECT COUNT(*) FROM wh_movements") - n0 == 1, "one receipt writes exactly one movement")
    n1 = q("SELECT COUNT(*) FROM wh_movements")
    svc.reserve_for_order(9301, BTN, 500, U)
    ck(q("SELECT COUNT(*) FROM wh_movements") == n1, "a reservation writes NO movement")
    svc.issue_reserved(9301, U)
    ck(q("SELECT COUNT(*) FROM wh_movements") - n1 == 1 and
       _f(q("SELECT reserved_qty FROM wh_materials WHERE id=?", (BTN,))) == 0,
       "the issue writes one movement and clears the reservation")
    n2 = q("SELECT COUNT(*) FROM wh_movements")
    for bad in (0, "", None, "abc", "nan", "1e400", [], {}):   # (-5 is a LEGAL adjustment)
        svc.receive_qty(BTN, bad, 1, U)
        svc.receive_qty(BTN, -5, 1, U)
        svc.adjust_stock(BTN, bad, "x", U)
        svc.fg_move(9001, "STY-A", "Navy", "L", bad, "pack", U)
        svc.return_to_store(rr["id"], bad, U)
    ck(q("SELECT COUNT(*) FROM wh_movements") - n2 == 0,
       "no garbage quantity ever writes a movement", str(q("SELECT COUNT(*) FROM wh_movements") - n2))

    # ------------------------------------------------ A14 unknown/hostile ids
    head("A14: unknown ids are refused, not crashed")
    for fn, args in ((svc.receive_qty, (999999, 10, 1, U)),
                     (svc.receive_roll, (999999, {"length_m": 10}, U)),
                     (svc.adjust_stock, (999999, 10, "x", U)),
                     (svc.release_allocation, (999999, U)),
                     (svc.set_roll_hold, (999999, True, U)),
                     (svc.issue_reserved, (123456, U)),
                     (svc.reserve_for_order, (1, 999999, 5, U)),
                     (svc.reserve_for_order, (1, None, 5, U)),
                     (svc.create_material, ({"code": ""}, U)),
                     (svc.fg_move, (1, "", "", "", 5, "pack", U))):
        try:
            ck(fn(*args)[0] is False, f"{fn.__name__} refuses cleanly")
        except Exception as e:                               # noqa: BLE001
            ck(False, f"{fn.__name__} raised", repr(e))
    ck(not svc.adjust_stock(None, -10 ** 9, "x", U, roll_id=drift_roll)[0],
       "a billion-metre negative roll adjustment is refused")
    ck(not svc.adjust_stock(BTN, -10 ** 9, "x", U)[0],
       "a billion-unit negative header adjustment is refused")
    ck(inv() == [], "invariants hold after the hostile-id sweep", str(inv()))

    # ------------------------------------------------------- A15 order costing
    head("A15: issued value is frozen at the movement's own cost")
    ok, mno = svc.receive_roll(FAB, {"length_m": 100, "shade_lot": "ADV-1", "unit_cost": 3.0}, U)
    ck(ok, "test roll received", str(mno))
    for _ in range(30):
        svc.issue_to_order(9401, FAB, 0.1, U, shade_lot="ADV-1")
    left = _f(q("SELECT remaining_m FROM wh_rolls WHERE roll_no=?", (mno,)))
    ck(abs(left - 97.0) < 1e-9, "30 x 0.1 m off a 100 m roll leaves exactly 97.0", str(left))
    v = svc.issued_for_order(9401)
    ck(abs(v["qty"] - 3.0) < 1e-9 and abs(v["value"] - 9.0) < 1e-9,
       "3 m valued at the roll's own 3.00/m", f"{v['qty']}/{v['value']}")
    svc.receive_roll(FAB, {"length_m": 50, "unit_cost": 99}, U)
    ck(abs(svc.issued_for_order(9401)["value"] - 9.0) < 1e-9,
       "a later expensive receipt does not rewrite it")
    ck(inv() == [], "final invariants", str(inv()))

    # --------------------------------------------- A17 the atomic reserve guard
    head("A17: the reserve guard lives in the UPDATE, and the issue is atomic")
    c = get_db()
    rg = c.execute("SELECT id, remaining_m, reserved_m FROM wh_rolls WHERE remaining_m>0 "
                   "AND status IN ('available','partial') ORDER BY id LIMIT 1").fetchone()
    free = _f(rg["remaining_m"]) - _f(rg["reserved_m"])
    ck(svc._try_reserve(c, "roll", rg["id"], free + 0.001) is False,
       "the conditional UPDATE refuses one millimetre more than is free", str(free))
    ck(_f(c.execute("SELECT reserved_m FROM wh_rolls WHERE id=?",
                    (rg["id"],)).fetchone()["reserved_m"]) == _f(rg["reserved_m"]),
       "...and wrote nothing while refusing")
    ck(svc._try_reserve(c, "roll", rg["id"], free) is True, "exactly the free balance is accepted")
    c.rollback(); c.close()
    # A stock take can never make a reservation UNBACKABLE, because _move_stock
    # refuses to write off metres that are already committed (reserved_blocks):
    # release first, then correct. That is the stronger guarantee, so assert it —
    # the roll must be untouched and the reservation must still be issuable.
    svc.reserve_for_order(9501, FAB, 120, U)
    a = q("SELECT id, roll_id, qty FROM wh_allocations WHERE order_id=9501 AND status='reserved'")
    r0 = q("SELECT remaining_m, reserved_m FROM wh_rolls WHERE id=?", (a["roll_id"],))
    n0 = q("SELECT COUNT(*) FROM wh_movements")
    ok, msg = svc.adjust_stock(None, -(_f(r0["remaining_m"]) - _f(a["qty"]) + 1),
                               "stock take found less than is reserved", U, roll_id=a["roll_id"])
    ck(not ok and msg == "reserved_blocks",
       "a stock take cannot write off reserved metres", f"{ok}/{msg}")
    ck(q("SELECT remaining_m, reserved_m FROM wh_rolls WHERE id=?", (a["roll_id"],)) == r0
       and q("SELECT COUNT(*) FROM wh_movements") == n0,
       "...and wrote neither the roll nor a movement")
    ok, msg = svc.issue_reserved(9501, U)
    ck(ok, "the reservation is therefore still issuable in full", str(msg))
    ck(q("SELECT status FROM wh_allocations WHERE id=?", (a["id"],)) == "issued",
       "the allocation closes exactly once")
    for x in svc.open_allocations():
        svc.release_allocation(x["id"], U)
    ck(inv() == [], "invariants hold after the stock-take attempt", str(inv()))

    # ------------------------------------------------- A18 procurement bridge
    head("A18: the procurement goods-receipt bridge")
    c = get_db()
    c.execute("INSERT INTO pr_requests (pr_no,title,status,created_at) VALUES "
              "('PR-ADV-1','adv','approved','2026-01-01')")
    pr_id = c.execute("SELECT id FROM pr_requests WHERE pr_no='PR-ADV-1'").fetchone()["id"]
    for item, price in (("TRM-BTN-18L — Button 18L", 0.05), ("NOT-A-MATERIAL — nope", 9.0)):
        c.execute("INSERT INTO pr_items (pr_id,item,qty,unit_price) VALUES (?,?,?,?)",
                  (pr_id, item, 100, price))
    c.commit()
    lines = [dict(r) for r in c.execute("SELECT id,item FROM pr_items WHERE pr_id=?",
                                        (pr_id,)).fetchall()]
    c.close()
    b0 = _f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (BTN,)))
    n0 = q("SELECT COUNT(*) FROM wh_movements")
    posted = svc.post_receipt_to_material(pr_id, {str(l["id"]): 100 for l in lines}, U)
    ck(abs(posted - 100) < 1e-9, "only the recognised line posts", str(posted))
    ck(abs(_f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (BTN,))) - (b0 + 100)) < 1e-9,
       "the button stock rises by exactly the receipt")
    ck(q("SELECT COUNT(*) FROM wh_movements") - n0 == 1,
       "the unresolvable line is skipped, not guessed", str(q("SELECT COUNT(*) FROM wh_movements") - n0))
    ck(svc.post_receipt_to_material(10 ** 9, {"1": 5}, U) == 0.0, "an unknown PR posts nothing")
    ck(svc.post_receipt_to_material(pr_id, {str(lines[0]["id"]): -50}, U) == 0.0,
       "a negative receipt posts nothing")
    ck(inv() == [], "invariants hold after the bridge", str(inv()))

    # -------------------------------------------------------- A19 alert dedup
    head("A19: shortage alerts fire once, and again after a genuine recovery")
    c = get_db()
    c.execute("DELETE FROM notifications WHERE module='warehouse'")
    c.execute("UPDATE wh_materials SET low_alerted=0, reorder_level=0")
    c.execute("UPDATE wh_materials SET reorder_level=stock_qty+1 WHERE id=?", (THR,))
    c.commit(); c.close()
    svc.stock_sweep()
    n1 = q("SELECT COUNT(*) FROM notifications WHERE module='warehouse'")
    ck(n1 == 1, "exactly one bell row for the one short material", str(n1))
    svc.stock_sweep()
    ck(q("SELECT COUNT(*) FROM notifications WHERE module='warehouse'") == 1,
       "a repeat sweep does not re-alert")
    svc.receive_qty(THR, 500, 1.2, U)                     # genuine recovery
    svc.stock_sweep()
    ck(q("SELECT COUNT(*) FROM notifications WHERE module='warehouse'") == 1,
       "a recovered material does not alert")
    c = get_db()
    c.execute("UPDATE wh_materials SET reorder_level=stock_qty+1 WHERE id=?", (THR,))
    c.commit(); c.close()
    svc.stock_sweep()
    ck(q("SELECT COUNT(*) FROM notifications WHERE module='warehouse'") == 2,
       "it alerts again the next time it genuinely runs short")

    # ---------------------------------------------------- A20 the route layer
    head("A17: route decoration + CSRF")
    src = (REPO / "app" / "routes" / "warehouse.py").read_text(encoding="utf-8")
    for decl, decs, name in re.findall(r"@bp\.route\((.*?)\)\n((?:@[\w_]+\([^\n]*\)\n|@[\w_]+\n)*)"
                                       r"def (\w+)", src):
        ck("@login_required" in decs, f"{name}: login_required")
        ck("permission_required" in decs, f"{name}: permission_required")
    ck(src.count("methods=[\"POST\"]") == 8, "8 state-changing routes, all POST-only",
       str(src.count("methods=[\"POST\"]")))
    ck("execute(" not in src, "routes contain no SQL at all")

    # ============ second pass: defects found by adversarial review #2 ==========
    # ------------- B1 an empty pick must not issue the whole order's reservations
    head("B1: a 0.0004 m request must not cut every reservation on the order")
    for x in svc.open_allocations():
        svc.release_allocation(x["id"], U)
    ok, msg, plan = svc.reserve_for_order(9601, FAB, 120, U)         # deliberate reservation
    ck(ok, "120 m deliberately reserved for order 9601", str(msg))
    held = [(a["id"], a["qty"]) for a in svc.open_allocations()]
    stock0 = _f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (FAB,)))
    n0 = q("SELECT COUNT(*) FROM wh_movements")
    # a sub-millimetre requirement against a DIFFERENT material on the same order
    ok2, msg2, p2 = svc.issue_to_order(9601, NVY, 0.0004, U)
    ck(not ok2, "the sub-mm request is refused outright", f"{msg2}")
    ck(abs(_f(q("SELECT stock_qty FROM wh_materials WHERE id=?", (FAB,))) - stock0) < 1e-9,
       "not one metre of the reserved material moved", str(stock0))
    ck(q("SELECT COUNT(*) FROM wh_movements") == n0, "and no movement was written")
    ck([(a["id"], a["qty"]) for a in svc.open_allocations()] == held,
       "the reservation is still open, not silently issued")
    # the same trap through the public helper: an EMPTY id filter matches nothing
    ck(svc.issue_reserved(9601, U, alloc_ids=[])[1] == "nothing_reserved",
       "an empty alloc_ids filter issues NOTHING (not everything)")
    ck(not svc.reserve_for_order(9602, FAB, 0.0004, U)[0],
       "a sub-mm reservation is refused, never a phantom 'reserved' success")
    ck(q("SELECT COUNT(*) FROM wh_allocations WHERE order_id=9602") == 0,
       "...and leaves no allocation row behind")

    # ------------------ B2 availability of ROLL-tracked stock at header level
    head("B2: committed fabric must read as committed on the materials list")
    row = [r for r in svc.list_materials() if r["id"] == FAB][0]
    free = _f(q("SELECT COALESCE(SUM(remaining_m-reserved_m),0) FROM wh_rolls "
                "WHERE material_id=?", (FAB,)))
    ck(abs(row["available"] - free) < 1e-6,
       "list_materials() available == the metres actually free", f"{row['available']} vs {free}")
    c = get_db()
    c.execute("DELETE FROM notifications WHERE module='warehouse'")
    c.execute("UPDATE wh_materials SET low_alerted=0, reorder_level=0")
    # reorder level ABOVE the free balance but BELOW raw stock: only a
    # reserved-aware sweep can see this material is short.
    c.execute("UPDATE wh_materials SET reorder_level=? WHERE id=?", (round(free) + 10, FAB))
    c.commit(); c.close()
    svc.stock_sweep()
    ck(q("SELECT COUNT(*) FROM notifications WHERE module='warehouse'") == 1,
       "the reorder sweep sees reserved metres as gone", str(free))
    ck(svc.dashboard()["low_count"] == 1, "and so does the dashboard 'materials short' KPI")
    ck([r for r in svc.list_materials(low_only=True) if r["id"] == FAB],
       "and the 'short only' filter lists it")
    for x in svc.open_allocations():
        svc.release_allocation(x["id"], U)
    ck(_f(q("SELECT reserved_qty FROM wh_materials WHERE id=?", (FAB,))) == 0,
       "releasing every allocation clears the header mirror too")
    ck(inv() == [], "invariants hold after the release", str(inv()))

    # ------------------------------------------- B3 a NOT NULL order_id crash
    head("B3: reserving with no order refuses instead of raising")
    for oid in (None, 0, ""):
        for mid in (FAB, BTN):
            try:
                r = svc.reserve_for_order(oid, mid, 50, U)
                ck(r[0] is False and r[1] == "order_required",
                   f"reserve(order={oid!r}) refused cleanly", str(r[1]))
            except Exception as e:                           # noqa: BLE001
                ck(False, f"reserve(order={oid!r}) raised", repr(e))

    # ------------------------------- B4 the reservation counter must reach zero
    head("B4: a fractional reservation strands nothing on the counter")
    r0 = _f(q("SELECT reserved_qty FROM wh_materials WHERE id=?", (BTN,)))
    ok, msg, _p = svc.reserve_for_order(9603, BTN, 100.0004, U)
    ck(ok, "100.0004 pcs reserved", str(msg))
    aq = _f(q("SELECT qty FROM wh_allocations WHERE order_id=9603"))
    rq = _f(q("SELECT reserved_qty FROM wh_materials WHERE id=?", (BTN,)))
    ck(abs((rq - r0) - aq) < 1e-9, "the counter holds exactly the allocation's quantity",
       f"{rq - r0} vs {aq}")
    ok, msg = svc.issue_reserved(9603, U)
    ck(ok and abs(_f(q("SELECT reserved_qty FROM wh_materials WHERE id=?", (BTN,))) - r0) < 1e-9,
       "and the issue puts it back to exactly where it started", str(msg))

    # -------------------------------------------- B5 every page, every bad form
    head("B5: route smoke test — no page 500s, no form crashes it")
    from app.routes.warehouse import bp as wh_bp
    if "warehouse" not in app.blueprints:
        app.register_blueprint(wh_bp)
    cl = app.test_client()
    TOK = "t" * 64
    with cl.session_transaction() as s:
        s.update({"user": {"id": 1, "username": "admin", "role": "super_admin"},
                  "username": "admin", "role": "super_admin", "user_id": 1, "uid": 1,
                  "_csrf_token": TOK})
    for p in ("/warehouse/", "/warehouse/materials", "/warehouse/materials?kind=fabric&low=1",
              "/warehouse/rolls", "/warehouse/rolls?material_id=abc&status=zz&shade_lot=%s" % "x",
              "/warehouse/receive", "/warehouse/issue", "/warehouse/fg",
              "/warehouse/fg?order_id=nope", f"/warehouse/issue?material_id={FAB}&required=200",
              f"/warehouse/issue?material_id={FAB}&required=abc&min_width_cm=xyz",
              f"/warehouse/issue?material_id={BTN}&required=500",
              "/warehouse/issue?material_id=999999&required=200"):
        ck(cl.get(p).status_code == 200, f"GET {p}")
    n0 = q("SELECT COUNT(*) FROM wh_movements")
    bad_forms = [
        ("/warehouse/materials", {"code": "", "width_cm": "abc"}),
        ("/warehouse/materials", {"code": "FAB-JER-WHT"}),                  # duplicate
        ("/warehouse/adjust", {"material_id": "abc", "delta": "5"}),
        ("/warehouse/adjust", {"roll_id": "1", "delta": "nan"}),
        ("/warehouse/adjust", {"roll_id": "1", "delta": "-1e9"}),
        ("/warehouse/adjust", {"delta": "10"}),
        ("/warehouse/receive", {"material_id": str(FAB), "length_m": "-5"}),
        ("/warehouse/receive", {"material_id": "zzz", "length_m": "5"}),
        ("/warehouse/receive", {"material_id": str(FAB), "length_m": "1e400"}),
        ("/warehouse/issue", {"material_id": str(FAB), "order_id": "5", "required": "1e400"}),
        ("/warehouse/issue", {"material_id": str(FAB), "order_id": "5", "required": "0.0004"}),
        ("/warehouse/issue", {"material_id": str(FAB), "required": "10"}),   # no order
        ("/warehouse/issue", {"material_id": "abc", "order_id": "abc", "required": "10"}),
        ("/warehouse/fg", {"order_id": "5", "style_code": "S", "color": "c", "size": "M",
                           "qty": "-3", "action": "pack"}),
        ("/warehouse/fg", {"order_id": "5", "style_code": "S", "color": "c", "size": "M",
                           "qty": "3", "action": "wipe"}),
        ("/warehouse/fg", {"order_id": "", "style_code": "", "color": "", "size": "",
                           "qty": "nan"}),
        ("/warehouse/rolls/1/return", {"qty": "abc"}),
        ("/warehouse/rolls/999999/hold", {"hold": "1"}),
        ("/warehouse/allocations/999999/release", {}),
    ]
    codes = set()
    for p, data in bad_forms:
        codes.add(cl.post(p, data=dict(data, _csrf=TOK),
                          headers={"Accept": "text/html"}).status_code)
    ck(codes == {302}, "every hostile form is a clean redirect, never a 500", str(sorted(codes)))
    ck(q("SELECT COUNT(*) FROM wh_movements") == n0,
       "and not one of them wrote a stock movement",
       str(q("SELECT COUNT(*) FROM wh_movements") - n0))
    ck(cl.post("/warehouse/adjust", data={"material_id": str(BTN), "delta": "5"},
               headers={"Accept": "text/html"}).status_code == 302,
       "a CSRF-less POST never reaches the service")
    ck(q("SELECT COUNT(*) FROM wh_movements") == n0, "...and wrote nothing")
    ck(inv() == [], "final invariants after the route fuzz", str(inv()))

    # ----------------------------------------------------- templates + i18n
    head("templates + i18n")
    from jinja2 import Environment, FileSystemLoader
    root = REPO / "app" / "templates"
    env = Environment(loader=FileSystemLoader(str(root)))
    keys, forms_missing_csrf = set(), []
    for f in sorted((root / "warehouse").glob("*.html")):
        src = f.read_text(encoding="utf-8")
        try:
            env.parse(src)
            ck(True, f"{f.name} parses")
        except Exception as e:                               # noqa: BLE001
            ck(False, f"{f.name} parses", repr(e))
        for form in re.findall(r'<form[^>]*method="post".*?</form>', src, re.S | re.I):
            if 'name="_csrf"' not in form:
                forms_missing_csrf.append(f.name)
        keys |= set(re.findall(r'data-i18n(?:-ph|-title)?="([a-z][\w.]*)"', src))
        keys |= set(re.findall(r"'(wh\.[\w.]+)'", src))
    ck(not forms_missing_csrf, "every POST form carries _csrf", str(forms_missing_csrf))
    non_wh = sorted(k for k in keys if not k.startswith("wh."))
    ck(not non_wh, "every i18n key uses the module prefix", str(non_wh))
    print(f"  ..   {len(keys)} distinct i18n keys used by the warehouse templates:")
    for k in sorted(keys):
        print("       " + k)

print(f"\n{N[0] - len(FAILS)}/{N[0]} checks passed")
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
