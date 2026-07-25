"""
Warehouse self-test — service + schema layer against an isolated throwaway DB.

Run:  python app/warehouse/tests_selftest.py
Covers the money/quantity invariants and the negative paths, not just the happy one.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wh_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                             # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


app = create_app()
with app.app_context():
    from app.db import get_db
    from app.warehouse.schema import create_and_seed
    from app.warehouse import services as svc

    U = {"username": "selftest"}

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    print("\n--- schema + seed ---")
    mats = {r["code"]: dict(r) for r in conn.execute("SELECT * FROM wh_materials").fetchall()}
    check("8 demo materials seeded", len(mats) == 8, len(mats))
    check("roll_tracked flag independent of kind",
          mats["TRM-ELS-30"]["kind"] == "trim" and mats["TRM-ELS-30"]["roll_tracked"] == 1)
    rolls = conn.execute("SELECT COUNT(*) c FROM wh_rolls").fetchone()["c"]
    check("10 demo rolls seeded", rolls == 10, rolls)
    fg_rows = conn.execute("SELECT COUNT(*) c FROM wh_fg").fetchone()["c"]
    check("FG seeded against demo orders", fg_rows > 0, fg_rows)

    # idempotent: a second boot must not duplicate or destroy anything
    create_and_seed(conn)
    conn.commit()
    check("create_and_seed idempotent",
          conn.execute("SELECT COUNT(*) c FROM wh_materials").fetchone()["c"] == 8
          and conn.execute("SELECT COUNT(*) c FROM wh_rolls").fetchone()["c"] == 10)

    def invariants(tag):
        """(a) header stock == sum of its rolls; (b) ledger sums to the header."""
        bad = []
        for r in conn.execute("SELECT id, code, stock_qty, roll_tracked FROM wh_materials").fetchall():
            led = conn.execute("SELECT COALESCE(SUM(qty),0) s FROM wh_movements WHERE material_id=?",
                               (r["id"],)).fetchone()["s"]
            if not close(led, r["stock_qty"], 1e-4):
                bad.append(f"{r['code']} ledger {led} != stock {r['stock_qty']}")
            if r["roll_tracked"]:
                rs = conn.execute("SELECT COALESCE(SUM(remaining_m),0) s FROM wh_rolls "
                                  "WHERE material_id=?", (r["id"],)).fetchone()["s"]
                if not close(rs, r["stock_qty"], 1e-4):
                    bad.append(f"{r['code']} rolls {rs} != stock {r['stock_qty']}")
        check(f"invariants hold {tag}", not bad, "; ".join(bad))

    invariants("after seed")

    WHT = mats["FAB-JER-WHT"]["id"]
    DEN = mats["FAB-DEN-IND"]["id"]
    BTN = mats["TRM-BTN-18L"]["id"]
    THR = mats["TRM-THR-40"]["id"]
    FLC = mats["FAB-FLC-GRY"]["id"]
    order = conn.execute("SELECT id FROM ord_orders ORDER BY id LIMIT 1").fetchone()
    ORD = order["id"] if order else 1
    conn.close()

    print("\n--- FIFO + shade-band picking ---")
    p = svc.plan_pick(WHT, 200)
    check("200m stays in one dye lot", p["shade_lot"] == "D-4471" and len(p["rolls"]) == 1
          and close(p["rolls"][0]["take_m"], 200), p["shade_lot"])
    check("oldest roll picked first (FIFO)", p["rolls"][0]["roll_no"].endswith("000001"),
          p["rolls"][0]["roll_no"])
    p = svc.plan_pick(WHT, 400)
    check("400m spans two rolls of ONE lot",
          len(p["rolls"]) == 2 and {r["shade_lot"] for r in p["rolls"]} == {"D-4471"}
          and close(p["picked"], 400))
    p = svc.plan_pick(WHT, 500)
    check("no single lot covers 500m -> mixed, flagged",
          p["mixed_lots"] and close(p["shortfall"], 0)
          and {r["shade_lot"] for r in p["rolls"]} == {"D-4471", "D-4472"})
    p = svc.plan_pick(WHT, 900)
    check("shortfall reported, not silently short-picked", close(p["shortfall"], 135)
          and close(p["picked"], 765), p["shortfall"])
    p = svc.plan_pick(WHT, 300, shade_lot="D-4472")
    check("explicit shade lot honoured", len(p["rolls"]) == 1
          and p["rolls"][0]["shade_lot"] == "D-4472")
    p = svc.plan_pick(WHT, 100, min_width_cm=200)
    check("width is a hard filter, not a preference", close(p["shortfall"], 100))
    p = svc.plan_pick(DEN, 300)
    check("quarantined roll is invisible to the picker", close(p["shortfall"], 60), p["shortfall"])
    check("plan_pick(0) is safe", svc.plan_pick(WHT, 0)["rolls"] == [])
    check("plan_pick(None material) is safe", svc.plan_pick(None, 100)["rolls"] == [])
    check("plan_pick(garbage qty) is safe", svc.plan_pick(WHT, "abc")["rolls"] == [])

    print("\n--- reserve / issue / release ---")
    ok, msg, plan = svc.reserve_for_order(ORD, WHT, 900, U)
    conn = get_db()
    res = conn.execute("SELECT COALESCE(SUM(reserved_m),0) s FROM wh_rolls").fetchone()["s"]
    conn.close()
    check("shortfall reserves NOTHING (all-or-nothing)",
          not ok and msg == "shortfall" and close(res, 0), f"{msg}/{res}")

    ok, msg, plan = svc.reserve_for_order(ORD, WHT, 400, U)
    conn = get_db()
    res = conn.execute("SELECT COALESCE(SUM(reserved_m),0) s FROM wh_rolls").fetchone()["s"]
    conn.close()
    check("reserve books exactly what was planned", ok and close(res, 400), f"{msg}/{res}")
    p2 = svc.plan_pick(WHT, 400)
    check("reserved metres are no longer available to a second picker",
          close(p2["picked"], 365), p2["picked"])

    for aid in plan["alloc_ids"]:
        svc.release_allocation(aid, U)
    conn = get_db()
    res = conn.execute("SELECT COALESCE(SUM(reserved_m),0) s FROM wh_rolls").fetchone()["s"]
    conn.close()
    check("release restores the exact pre-reserve value", close(res, 0), res)
    check("releasing twice is refused, not double-counted",
          svc.release_allocation(plan["alloc_ids"][0], U) == (False, "not_reserved"))

    ok, msg, plan = svc.issue_to_order(ORD, WHT, 400, U, notes="cut lay 1")
    check("issue 400m succeeds", ok, msg)
    conn = get_db()
    r1, r2 = conn.execute("SELECT * FROM wh_rolls WHERE material_id=? ORDER BY id LIMIT 2",
                          (WHT,)).fetchall()
    hdr = conn.execute("SELECT stock_qty FROM wh_materials WHERE id=?", (WHT,)).fetchone()["stock_qty"]
    res = conn.execute("SELECT COALESCE(SUM(reserved_m),0) s FROM wh_rolls").fetchone()["s"]
    conn.close()
    check("first roll emptied and derived 'consumed'",
          close(r1["remaining_m"], 0) and r1["status"] == "consumed", r1["status"])
    check("second roll partially cut -> 'partial'",
          close(r2["remaining_m"], 65) and r2["status"] == "partial",
          f"{r2['remaining_m']}/{r2['status']}")
    check("header moved with the rolls", close(hdr, 365), hdr)
    check("issue released the reservation it fulfilled", close(res, 0), res)

    got = svc.issued_for_order(ORD)
    check("issued_for_order returns qty and value",
          close(got["qty"], 400) and close(got["value"], 400 * 3.20),
          f"{got['qty']}/{got['value']}")

    ok, msg, plan = svc.issue_to_order(ORD, WHT, 10000, U)
    check("over-issue refused, nothing written", not ok and msg == "shortfall")
    check("issued total unchanged after a refused issue",
          close(svc.issued_for_order(ORD)["qty"], 400))
    check("issue with no reservation is refused",
          svc.issue_reserved(999999, U) == (False, "nothing_reserved"))
    ok, msg, _ = svc.issue_to_order(ORD, WHT, 0, U)
    check("issue of zero is refused", not ok and msg == "bad_qty")
    ok, msg, _ = svc.issue_to_order(ORD, 999999, 10, U)
    check("issue against an unknown material is refused", not ok and msg == "material_not_found")

    print("\n--- quantity-tracked material (trims) ---")
    ok, msg, _ = svc.reserve_for_order(ORD, BTN, 5000, U)
    check("trim reserve (header-level conditional UPDATE)", ok, msg)
    ok2, msg2, _ = svc.reserve_for_order(ORD, BTN, 999999, U)
    check("trim reserve beyond availability refused", not ok2 and msg2 == "shortfall")
    ok3, msg3 = svc.issue_reserved(ORD, U)
    conn = get_db()
    btn = conn.execute("SELECT stock_qty, reserved_qty FROM wh_materials WHERE id=?",
                       (BTN,)).fetchone()
    conn.close()
    check("trim issue moves stock and clears the reservation",
          ok3 and close(btn["stock_qty"], 41000) and close(btn["reserved_qty"], 0),
          f"{msg3}/{dict(btn)}")

    print("\n--- receiving + weighted-average cost ---")
    ok, msg = svc.receive_qty(THR, 4000, 0.05, U)   # thread: 340 @ 1.15
    conn = get_db()
    m = conn.execute("SELECT stock_qty, avg_cost FROM wh_materials WHERE id=?", (THR,)).fetchone()
    conn.close()
    expect = (340 * 1.15 + 4000 * 0.05) / 4340
    check("weighted average uses PRE-receipt stock",
          ok and close(m["avg_cost"], round(expect, 4), 1e-4), f"{m['avg_cost']} vs {expect}")
    avg_before = m["avg_cost"]
    svc.receive_qty(THR, 100, "", U)
    svc.receive_qty(THR, 100, "0", U)
    svc.receive_qty(THR, 100, "not-a-number", U)
    conn = get_db()
    m2 = conn.execute("SELECT stock_qty, avg_cost FROM wh_materials WHERE id=?", (THR,)).fetchone()
    conn.close()
    check("blank / '0' / garbage price never drags avg_cost to zero",
          close(m2["avg_cost"], avg_before) and close(m2["stock_qty"], 4640),
          f"{m2['avg_cost']}/{m2['stock_qty']}")
    check("receive of 0 refused", svc.receive_qty(THR, 0, 1, U) == (False, "bad_qty"))
    check("negative receipt refused, not abs()'d",
          svc.receive_qty(THR, -50, 1, U) == (False, "bad_qty"))
    check("non-numeric qty refused", svc.receive_qty(THR, "ten", 1, U) == (False, "bad_qty"))
    check("receive into an unknown material refused",
          svc.receive_qty(999999, 10, 1, U) == (False, "material_not_found"))
    check("receive_roll on a quantity material refused",
          svc.receive_roll(THR, {"length_m": 10}, U) == (False, "not_roll_tracked"))
    check("receive_roll with no length refused",
          svc.receive_roll(WHT, {"length_m": 0}, U) == (False, "bad_qty"))

    ok, roll_no = svc.receive_roll(WHT, {"length_m": 250, "shade_lot": "D-4480",
                                         "unit_cost": 3.50, "width_cm": 180}, U)
    check("receive_roll creates a roll and books it through the ledger", ok, roll_no)
    p = svc.plan_pick(WHT, 250, shade_lot="D-4480")
    check("the new roll is immediately pickable", close(p["shortfall"], 0))

    print("\n--- returns, adjustments, holds ---")
    conn = get_db()
    r1id = conn.execute("SELECT id FROM wh_rolls WHERE material_id=? ORDER BY id LIMIT 1",
                        (WHT,)).fetchone()["id"]
    conn.close()
    check("return the end-bit onto the SAME roll", svc.return_to_store(r1id, 50, U, ORD)[0])
    check("cannot return more than the roll ever held",
          svc.return_to_store(r1id, 5000, U) == (False, "exceeds_roll_length"))
    check("return of 0 refused", svc.return_to_store(r1id, 0, U) == (False, "bad_qty"))
    conn = get_db()
    r1 = conn.execute("SELECT remaining_m, status FROM wh_rolls WHERE id=?", (r1id,)).fetchone()
    conn.close()
    check("returned roll is 'partial' again, not a new roll",
          close(r1["remaining_m"], 50) and r1["status"] == "partial", dict(r1))

    check("adjustment cannot push the header negative",
          svc.adjust_stock(THR, -999999, "stock take", U) == (False, "stock_would_go_negative"))
    check("adjustment cannot push a roll negative",
          svc.adjust_stock(WHT, -999999, "stock take", U, roll_id=r1id)
          == (False, "roll_would_go_negative"))
    check("zero adjustment refused", svc.adjust_stock(THR, 0, "noop", U) == (False, "zero_qty"))
    check("garbage adjustment refused", svc.adjust_stock(THR, "x", "noop", U) == (False, "zero_qty"))
    check("real adjustment applies", svc.adjust_stock(THR, -40, "stock take", U)[0])

    conn = get_db()
    qr = conn.execute("SELECT id FROM wh_rolls WHERE status='quarantine' LIMIT 1").fetchone()["id"]
    conn.close()
    ok, st = svc.set_roll_hold(qr, False, U)
    check("hold released -> status re-derived from remaining", ok and st == "available", st)
    p = svc.plan_pick(DEN, 300)
    check("released roll becomes pickable again", close(p["shortfall"], 0), p["shortfall"])

    print("\n--- finished goods matrix ---")
    check("pack creates the SKU", svc.fg_move(ORD, "TC-KNIT-01", "Red", "M", 100, "pack", U)[0])
    check("ship more than packed refused",
          svc.fg_move(ORD, "TC-KNIT-01", "Red", "M", 150, "ship", U)
          == (False, "fg_would_go_negative"))
    check("ship what is packed", svc.fg_move(ORD, "TC-KNIT-01", "Red", "M", 100, "ship", U)[0])
    check("ship an unknown SKU refused",
          svc.fg_move(ORD, "NOPE", "Red", "M", 1, "ship", U) == (False, "fg_not_found"))
    check("fg qty 0 refused", svc.fg_move(ORD, "TC-KNIT-01", "Red", "M", 0, "pack", U)
          == (False, "bad_qty"))
    check("fg bad action refused", svc.fg_move(ORD, "TC-KNIT-01", "Red", "M", 5, "burn", U)
          == (False, "bad_action"))
    check("fg incomplete SKU refused", svc.fg_move(ORD, "", "", "", 5, "pack", U)
          == (False, "sku_required"))
    mx = svc.fg_matrix(ORD)
    check("matrix sizes ordered S,M,L,XL,XXL", mx["sizes"][:5] == ["S", "M", "L", "XL", "XXL"],
          mx["sizes"])
    check("matrix totals reconcile",
          close(mx["totals"]["onhand"], mx["totals"]["packed"] - mx["totals"]["shipped"]))
    empty = svc.fg_matrix(999999)
    check("empty matrix does not divide by zero",
          empty["rows"] == [] and empty["totals"]["onhand"] == 0)

    print("\n--- procurement bridge (reverse leg) ---")
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO pr_requests (pr_no,title,requester,status,currency,total,created_at) "
        "VALUES ('PR-TEST-1','Fabric buy','selftest','po_issued','EGP',0,'2026-01-01')")
    pr1 = cur.lastrowid
    l1 = conn.execute("INSERT INTO pr_items (pr_id,seq,item,unit,qty,unit_price) "
                      "VALUES (?,1,'TRM-THR-40 — Sewing thread 40/2','cone',500,1.30)",
                      (pr1,)).lastrowid
    l2 = conn.execute("INSERT INTO pr_items (pr_id,seq,item,unit,qty,unit_price) "
                      "VALUES (?,2,'FAB-FLC-GRY — Brushed Fleece 320gsm','m',400,5.40)",
                      (pr1,)).lastrowid
    l3 = conn.execute("INSERT INTO pr_items (pr_id,seq,item,unit,qty,unit_price) "
                      "VALUES (?,3,'WIDGET-999 — not a warehouse item','pcs',10,1)",
                      (pr1,)).lastrowid
    cur = conn.execute(
        "INSERT INTO pr_requests (pr_no,title,requester,status,source_module,source_ref,created_at) "
        "VALUES ('PR-TEST-2','Spare buy','selftest','po_issued','maintenance','spare:1','2026-01-01')")
    pr2 = cur.lastrowid
    l4 = conn.execute("INSERT INTO pr_items (pr_id,seq,item,unit,qty,unit_price) "
                      "VALUES (?,1,'TRM-THR-40 — Sewing thread','cone',10,1)", (pr2,)).lastrowid
    conn.commit()
    thr_before = conn.execute("SELECT stock_qty FROM wh_materials WHERE id=?",
                              (THR,)).fetchone()["stock_qty"]
    flc_before = conn.execute("SELECT stock_qty FROM wh_materials WHERE id=?",
                              (FLC,)).fetchone()["stock_qty"]
    conn.close()

    posted = svc.post_receipt_to_material(pr1, {str(l1): 300, l2: 400, l3: 10}, U)
    check("bridge posts only resolvable warehouse lines", close(posted, 700), posted)
    conn = get_db()
    thr_after = conn.execute("SELECT stock_qty FROM wh_materials WHERE id=?",
                             (THR,)).fetchone()["stock_qty"]
    flc_after = conn.execute("SELECT stock_qty FROM wh_materials WHERE id=?",
                             (FLC,)).fetchone()["stock_qty"]
    newroll = conn.execute("SELECT * FROM wh_rolls WHERE grn_ref='PR-TEST-1'").fetchone()
    conn.close()
    check("quantity line lands on the header", close(thr_after, thr_before + 300), thr_after)
    check("roll-tracked line creates a roll",
          newroll is not None and close(newroll["length_m"], 400)
          and close(flc_after, flc_before + 400))
    check("bridge ignores maintenance-owned PRs",
          close(svc.post_receipt_to_material(pr2, {l4: 10}, U), 0))
    check("bridge never raises on a missing PR",
          close(svc.post_receipt_to_material(999999, {1: 5}, U), 0))
    check("bridge treats str and int line keys alike",
          close(svc.post_receipt_to_material(pr1, {str(l1): 50}, U), 50))

    print("\n--- ledger integrity ---")
    conn = get_db()
    tot, uniq = conn.execute(
        "SELECT COUNT(*) c, COUNT(DISTINCT movement_no) u FROM wh_movements").fetchone()[0:2]
    check("every movement is numbered and unique", tot == uniq and tot > 0, f"{tot}/{uniq}")
    neg = conn.execute("SELECT COUNT(*) c FROM wh_materials WHERE stock_qty<0 "
                       "OR reserved_qty<0").fetchone()["c"]
    negr = conn.execute("SELECT COUNT(*) c FROM wh_rolls WHERE remaining_m<0 "
                        "OR reserved_m<0").fetchone()["c"]
    check("nothing ever went negative", neg == 0 and negr == 0, f"{neg}/{negr}")
    invariants("after every operation")
    conn.close()

    print("\n--- shortage sweep ---")
    def bells():
        c = get_db()
        try:
            return c.execute("SELECT COUNT(*) c FROM notifications WHERE module='warehouse'"
                             ).fetchone()["c"]
        finally:
            c.close()

    svc.stock_sweep()
    check("no alert while everything is above its reorder level", bells() == 0, bells())
    svc.issue_to_order(ORD, WHT, 200, U)      # drops WHT below its 500m reorder level
    svc.stock_sweep()
    b1 = bells()
    check("a genuine shortage rings the bell", b1 > 0, b1)
    svc.stock_sweep()
    check("the sweep is deduplicated (no bell flood)", bells() == b1, f"{b1}->{bells()}")
    svc.receive_roll(WHT, {"length_m": 400, "shade_lot": "D-4490", "unit_cost": 3.4}, U)
    svc.stock_sweep()
    check("restock clears the flag without re-alerting", bells() == b1, bells())
    svc.issue_to_order(ORD, WHT, 500, U)
    svc.stock_sweep()
    check("the same material can alert again next time it runs short", bells() > b1,
          f"{b1}->{bells()}")

    conn = get_db()
    invariants("after the sweep")
    conn.close()

    print("\n--- dashboard ---")
    d = svc.dashboard()
    check("dashboard returns every KPI",
          all(k in d for k in ("rolls_available", "total_m", "reserved_m", "shade_lots",
                               "low_count", "fg_onhand", "stock_value", "low", "recent")))

print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
