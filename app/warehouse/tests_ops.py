"""
Warehouse operations self-test — reservations issue, returns, stock counts, stock card,
daily report, internal issue, bin moves and the FG shipping guard, on a throwaway DB.

Run:  python -m app.warehouse.tests_ops
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wh_ops_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
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


app = create_app()
with app.app_context():
    from app.db import get_db
    from app.warehouse.schema import create_and_seed
    from app.warehouse import services as svc

    A, B = {"username": "store_a"}, {"username": "mgr_b"}
    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    def q(sql, args=()):
        c = get_db()
        try:
            r = c.execute(sql, args).fetchone()
            return None if r is None else (r[0] if len(r.keys()) == 1 else dict(r))
        finally:
            c.close()

    if not conn.execute("SELECT id FROM ord_orders LIMIT 1").fetchone():
        conn.execute("INSERT INTO ord_orders (order_no, buyer, status) VALUES ('T-1','T','confirmed')")
        conn.commit()
    ORD = conn.execute("SELECT id FROM ord_orders ORDER BY id LIMIT 1").fetchone()["id"]
    # a second order with no issues and no shipments (the demo seeds a shipment for ORD)
    ORD2 = conn.execute("INSERT INTO ord_orders (order_no, buyer, status) "
                        "VALUES ('T-OPS-2','T','confirmed')").lastrowid
    conn.commit()
    mats = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM wh_materials")}
    conn.close()
    WHT, NVY, BTN, THR = (mats["FAB-JER-WHT"], mats["FAB-JER-NVY"], mats["TRM-BTN-18L"],
                          mats["TRM-THR-40"])
    stock = lambda mid: float(q("SELECT stock_qty FROM wh_materials WHERE id=?", (mid,)))

    print("\n--- 1. reservations: issue one / issue all / filter ---")
    ok, _m, p1 = svc.reserve_for_order(ORD, BTN, 100, A)
    ok2, _m, p2 = svc.reserve_for_order(ORD, BTN, 50, A)
    check("two reservations made", ok and ok2)
    check("order filter + count", svc.count_open_allocations(ORD) == 2
          and len(svc.open_allocations(ORD)) == 2 and svc.count_open_allocations(-1) == 0)
    s0 = stock(BTN)
    check("issue a single allocation", svc.issue_reserved(ORD, A, alloc_ids=p1["alloc_ids"])[0]
          and abs(stock(BTN) - (s0 - 100)) < 1e-9)
    check("forged order matches nothing",
          svc.issue_reserved(ORD + 999, A, alloc_ids=p2["alloc_ids"]) == (False, "nothing_reserved"))
    check("issue all for the order", svc.issue_reserved(ORD, A)[0]
          and svc.count_open_allocations(ORD) == 0 and abs(stock(BTN) - (s0 - 150)) < 1e-9)

    print("\n--- 2. returns from production ---")
    check("order required", svc.return_to_store(BTN, 1, A, None) == (False, "order_required"))
    check("cannot return more than issued",
          svc.return_to_store(BTN, 151, A, ORD) == (False, "exceeds_issued"))
    before = svc.issued_for_order(ORD)
    check("quantity return within issued", svc.return_to_store(BTN, 40, A, ORD)[0])
    check("cap counts earlier returns",
          svc.return_to_store(BTN, 111, A, ORD) == (False, "exceeds_issued"))
    after = svc.issued_for_order(ORD)
    btn_line = lambda d: next(l for l in d["lines"] if l["material_id"] == BTN)
    check("issued_for_order nets the return (qty)",
          abs(btn_line(after)["qty"] - (btn_line(before)["qty"] - 40)) < 1e-9)
    check("issued_for_order nets the return (value)",
          abs(btn_line(after)["value"] - btn_line(before)["value"] * 110 / 150) < 1e-6)
    check("returning the rest nets qty and value to zero",
          svc.return_to_store(BTN, 110, A, ORD)[0]
          and abs(btn_line(svc.issued_for_order(ORD))["qty"]) < 1e-9
          and abs(btn_line(svc.issued_for_order(ORD))["value"]) < 1e-6)
    check("roll-tracked material needs a roll",
          svc.return_to_store(WHT, 1, A, ORD) == (False, "roll_required"))
    ok, msg, plan = svc.issue_to_order(ORD, WHT, 20, A)
    rid = plan["rolls"][0]["roll_id"]
    check("roll return to an order that never got this roll refused",
          svc.return_to_store(None, 1, A, ORD2, roll_id=rid) == (False, "exceeds_issued"))
    check("roll return ok", svc.return_to_store(None, 5, A, ORD, roll_id=rid)[0])
    hdrs, rows = svc.export_dataset("issues-by-order")
    wht_row = [r for r in rows if r[1] == ORD and r[2] == "FAB-JER-WHT"]
    check("issues-by-order export nets returns", wht_row and abs(wht_row[0][5] - 15) < 1e-9,
          str(wht_row))

    print("\n--- 3. stock counts ---")
    check("reason required", svc.submit_count(THR, 1, "", "", A) == (False, "reason_required"))
    check("note required for 'other'", svc.submit_count(THR, 1, "other", "", A)
          == (False, "note_required"))
    s0 = stock(THR)
    check("small positive count auto-applies",
          svc.submit_count(THR, 1, "found", "", A) == (True, "applied") and stock(THR) == s0 + 1)
    s0 = stock(THR)
    check("negative count goes pending", svc.submit_count(THR, -10, "lost", "", A)
          == (True, "pending") and stock(THR) == s0)
    cid = q("SELECT id FROM wh_counts WHERE status='pending' ORDER BY id DESC LIMIT 1")
    check("a bell is raised for the pending count",
          q("SELECT COUNT(*) FROM notifications WHERE title LIKE 'Stock count awaiting%'") >= 1)
    check("pending hidden from its submitter on /my-work",
          all(c["id"] != cid for c in svc.pending_counts_for(A))
          and any(c["id"] == cid for c in svc.pending_counts_for(B)))
    check("own approval refused", svc.approve_count(cid, A) == (False, "own_count")
          and stock(THR) == s0)
    check("other user approves -> stock moves", svc.approve_count(cid, B)[0]
          and abs(stock(THR) - (s0 - 10)) < 1e-9)
    check("second approval refused", svc.approve_count(cid, B) == (False, "not_pending")
          and abs(stock(THR) - (s0 - 10)) < 1e-9)
    svc.submit_count(THR, -5, "damaged", "", A)
    cid2 = q("SELECT id FROM wh_counts WHERE status='pending' ORDER BY id DESC LIMIT 1")
    s1 = stock(THR)
    check("reject leaves stock", svc.reject_count(cid2, B)[0] and stock(THR) == s1)
    check("bad threshold refused", not svc.set_count_threshold(150, B)[0])
    check("threshold 0 saved", svc.set_count_threshold(0, B)[0] and svc.count_auto_pct() == 0)
    check("with 0 % every positive count waits",
          svc.submit_count(THR, 1, "found", "", A) == (True, "pending"))
    svc.set_count_threshold(2, B)

    print("\n--- 4. stock card + daily report ---")
    card = svc.stock_card(THR, "2000-01-01", "2999-12-31")
    check("card closes at the material's stock", abs(card["closing"] - stock(THR)) < 1e-6,
          f"{card['closing']} vs {stock(THR)}")
    check("opening + in - out == closing",
          abs(card["opening"] + card["total_in"] - card["total_out"] - card["closing"]) < 1e-6)
    from datetime import date
    today = date.today().isoformat()
    later = svc.stock_card(THR, today, today)
    check("a range starting today carries the earlier balance as opening",
          abs(later["closing"] - stock(THR)) < 1e-6)
    rows = svc.daily_report("2000-01-01", "2999-12-31", THR)
    check("daily rows chain opening -> closing",
          rows and all(abs(r["opening"] + r["in"] - r["out"] - r["closing"]) < 1e-6 for r in rows)
          and abs(rows[-1]["closing"] - stock(THR)) < 1e-6)
    check("bad dates fall back to defaults", svc.date_range("2026-13-45", "x")[1] == today)

    print("\n--- 5. internal issue + bin move ---")
    s0, iss0 = stock(THR), svc.issued_for_order(ORD)["qty"]
    check("internal issue requires a known destination kind",
          svc.issue_internal(THR, 1, "party", "x", "y", A) == (False, "bad_destination"))
    check("internal issue moves stock", svc.issue_internal(THR, 3, "maintenance", "Line 3",
                                                           "machine oiling", A)[0]
          and abs(stock(THR) - (s0 - 3)) < 1e-9)
    check("...with no order and outside order consumption",
          q("SELECT order_id FROM wh_movements WHERE material_id=? ORDER BY id DESC LIMIT 1",
            (THR,)) is None and svc.issued_for_order(ORD)["qty"] == iss0)
    check("roll-tracked internal issue needs a roll",
          not svc.issue_internal(NVY, 1, "sample", "Proto", "sample", A)[0])
    n0, s0 = q("SELECT COUNT(*) FROM wh_movements"), stock(THR)
    check("bin move", svc.move_bin("B-07", A, material_id=THR)[0]
          and q("SELECT bin FROM wh_materials WHERE id=?", (THR,)) == "B-07"
          and stock(THR) == s0 and q("SELECT COUNT(*) FROM wh_movements") == n0 + 1
          and q("SELECT type FROM wh_movements ORDER BY id DESC LIMIT 1") == "bin_move")
    check("bin move of a roll", svc.move_bin("R-01", A, roll_id=rid)[0]
          and q("SELECT bin FROM wh_rolls WHERE id=?", (rid,)) == "R-01")

    print("\n--- 6. FG: shipping is authoritative ---")
    svc.fg_move(ORD2, "OPS-1", "Red", "M", 100, "pack", A)
    check("manual ship allowed without a shipment",
          svc.fg_move(ORD2, "OPS-1", "Red", "M", 10, "ship", A, manual=True)[0])
    c = get_db()
    c.execute("INSERT INTO shp_shipments (shipment_no, order_id, status) VALUES ('S-T', ?, 'planned')",
              (ORD2,))
    c.commit(); c.close()
    check("manual ship blocked once shipping owns the order",
          svc.fg_move(ORD2, "OPS-1", "Red", "M", 10, "ship", A, manual=True)
          == (False, "shipping_owns_order"))
    check("the shipping path still posts", svc.fg_move(ORD2, "OPS-1", "Red", "M", 10, "ship", A)[0])
    check("manual pack still allowed", svc.fg_move(ORD2, "OPS-1", "Red", "M", 5, "pack", A,
                                                   manual=True)[0])

print("\n" + ("ALL CHECKS PASSED" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
