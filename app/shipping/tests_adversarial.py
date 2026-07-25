"""
Adversarial verification of app/shipping — arithmetic, idempotency, double-apply,
locks, snapshots, guards, SQL, route security, i18n and template parsing.

Run:  python app/shipping/tests_adversarial.py
Uses an isolated throwaway DB (Config.DB_PATH is repointed before create_app()).
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="shp_adv_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                        # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"
config.Config.DATABASE_URL = ""

from app import create_app                           # noqa: E402

FAIL = []
N = [0]


def ck(cond, label):
    N[0] += 1
    if cond:
        print("  ok   " + label)
    else:
        print("  FAIL " + label)
        FAIL.append(label)


def section(t):
    print("\n[%s]" % t)


app = create_app()

with app.app_context():
    from app.db import get_db
    from app.shipping.schema import create_and_seed
    from app.shipping import services as svc
    from app.shipping import constants as K

    conn = get_db()

    # ------------------------------------------------------------------
    section("1  create_and_seed x3 — idempotent and non-destructive")
    create_and_seed(conn); conn.commit()
    s1 = conn.execute("SELECT COUNT(*) c FROM shp_shipments").fetchone()["c"]
    c1 = conn.execute("SELECT COUNT(*) c FROM shp_cartons").fetchone()["c"]
    ids1 = [r["id"] for r in conn.execute("SELECT id FROM shp_shipments ORDER BY id").fetchall()]
    create_and_seed(conn); conn.commit()
    create_and_seed(conn); conn.commit()
    s3 = conn.execute("SELECT COUNT(*) c FROM shp_shipments").fetchone()["c"]
    c3 = conn.execute("SELECT COUNT(*) c FROM shp_cartons").fetchone()["c"]
    ids3 = [r["id"] for r in conn.execute("SELECT id FROM shp_shipments ORDER BY id").fetchall()]
    ck(s1 == s3 == 3, "shipments after 3 seeds: %s -> %s" % (s1, s3))
    ck(c1 == c3 == 7, "carton lines after 3 seeds: %s -> %s" % (c1, c3))
    ck(ids1 == ids3, "row ids unchanged (nothing destroyed or recreated)")
    ck(all(r["shipment_no"] for r in
           conn.execute("SELECT shipment_no FROM shp_shipments").fetchall()),
       "every seeded shipment is numbered")

    # ------------------------------------------------------------------
    section("2  pure packing arithmetic — recomputed by hand")
    m = svc.line_math({"qty_per_carton": 60, "cartons": 50, "net_weight": 12.5,
                       "gross_weight": 13.4, "length_cm": 60, "width_cm": 40, "height_cm": 30})
    ck(m["pieces"] == 3000, "60 x 50 = 3000 pcs")
    ck(m["net_total"] == 625.0, "12.5 x 50 = 625.0 kg net")
    ck(m["gross_total"] == 670.0, "13.4 x 50 = 670.0 kg gross")
    ck(m["cbm"] == 3.6, "0.6 x 0.4 x 0.3 x 50 = 3.6 CBM")
    ck(round(0.6 * 0.4 * 0.3, 6) == 0.072, "hand check: 60x40x30 cm = 0.072 m3 (divisor 1e6)")

    poison = [None, "", "abc", "nan", "inf", "-inf", "1e400", "-5", [], {}]
    for p in poison:
        r = svc.line_math({"qty_per_carton": p, "cartons": p, "net_weight": p,
                           "gross_weight": p, "length_cm": p, "width_cm": p, "height_cm": p})
        ok = all(isinstance(v, (int, float)) and v == v and 0 <= v < float("inf")
                 for v in r.values())
        ck(ok, "line_math(%r) -> finite and non-negative" % (p,))
    ck(svc.totals([]) == {"lines": 0, "cartons": 0, "pieces": 0, "net": 0, "gross": 0, "cbm": 0},
       "totals([]) is all zeros, no crash")
    ck(svc.packing_summary([]) == [], "packing_summary([]) == []")
    neg = svc.line_math({"qty_per_carton": 1, "cartons": 1, "length_cm": -60,
                         "width_cm": 40, "height_cm": 30})
    ck(neg["cbm"] == 0.0, "a negative edge cannot make negative CBM")

    # ------------------------------------------------------------------
    section("3  seeded totals — detail page vs the list page SQL aggregate")
    b = svc.get_shipment(1)
    ck(b is not None, "shipment 1 readable")
    ck(b["totals"]["cartons"] == 190, "SH1 cartons = 50+45+50+45 = 190")
    ck(b["totals"]["pieces"] == 11400, "SH1 pieces = 60 x 190 = 11,400")
    ck(b["totals"]["cbm"] == 13.68, "SH1 CBM = 0.072 x 190 = 13.68")
    # net  12.5*50 + 13.1*45 + 12.5*50 + 13.1*45 = 625 + 589.5 + 625 + 589.5 = 2429.0
    ck(b["totals"]["net"] == 2429.0, "SH1 net = 2429.0 kg (hand-summed)")
    # gross 13.4*50 + 14.0*45 + 13.4*50 + 14.0*45 = 670 + 630 + 670 + 630 = 2600.0
    ck(b["totals"]["gross"] == 2600.0, "SH1 gross = 2600.0 kg (hand-summed)")
    ck(b["totals"]["gross"] >= b["totals"]["net"], "gross >= net")
    lst = {r["id"]: r for r in svc.list_shipments()}
    ck(lst[1]["cartons"] == b["totals"]["cartons"], "list-page cartons match the detail page")
    ck(lst[1]["pieces"] == b["totals"]["pieces"], "list-page pieces match the detail page")
    ck(abs(lst[1]["cbm"] - b["totals"]["cbm"]) < 1e-6, "list-page CBM matches the detail page")
    ck(sum(g["pieces"] for g in b["summary"]) == b["totals"]["pieces"],
       "packing summary foots to the total")
    ck(len(b["summary"]) == 4, "summary groups to 4 style/colour/size rows")

    # ------------------------------------------------------------------
    section("4  commercial invoice — the money path")
    inv = svc.invoice(1)
    ck(inv["shipment"]["unit_price"] == 3.85, "unit price snapshotted from the order = 3.85")
    ck(inv["invoice_total"] == 43890.00, "11,400 x 3.85 = 43,890.00")
    ck(round(sum(l["amount"] for l in inv["lines"]), 2) == inv["invoice_total"],
       "total == sum of the rounded lines (a customs invoice foots line by line)")
    ck(all(l["amount"] == round(l["pieces"] * 3.85, 2) for l in inv["lines"]),
       "every line = pieces x the snapshotted price")
    ck(svc.invoice(999999) is None, "invoice of a missing shipment is None (404, not 500)")
    ck(svc.get_shipment(999999) is None, "get_shipment of a missing id is None")
    ck(svc.get_shipment(-1) is None and svc.get_shipment(0) is None, "id <= 0 is None")

    oid = inv["shipment"]["order_id"]
    conn.execute("UPDATE ord_orders SET unit_price=99.99 WHERE id=?", (oid,)); conn.commit()
    inv2 = svc.invoice(1)
    ck(inv2["shipment"]["unit_price"] == 3.85 and inv2["invoice_total"] == 43890.00,
       "repricing the order does NOT move the issued invoice (snapshot holds)")
    conn.execute("UPDATE ord_orders SET unit_price=3.85 WHERE id=?", (oid,)); conn.commit()

    # ------------------------------------------------------------------
    section("5  write guards — every refusal is a clean (False, reason)")
    sid = svc.create_shipment({"destination": "Genoa, IT", "order_id": "",
                               "buyer": "Adversary Ltd"}, {"username": "tester"})
    ck(isinstance(sid, int) and sid > 0, "stand-alone shipment created (id=%s)" % sid)
    st = svc.get_shipment(sid)
    ck(st["order"] is None, "no order linked")
    ck(st["shipment"]["unit_price"] == 0, "no order -> unit price 0, not a crash")
    ck(str(st["shipment"]["shipment_no"]).endswith("%04d" % sid),
       "shipment_no numbered from the row id: %s" % st["shipment"]["shipment_no"])

    bad = svc.create_shipment({"destination": "X", "order_id": "999999"}, {"username": "t"})
    ck(svc.get_shipment(bad)["shipment"]["order_id"] is None,
       "a non-existent order id is dropped, never stored as a dangling link")
    huge = svc.create_shipment({"destination": "X", "order_id": "99999999999999999999"}, {})
    ck(svc.get_shipment(huge)["shipment"]["order_id"] is None, "an out-of-range order id is dropped")
    junk = svc.create_shipment({"destination": "X", "order_id": "abc", "status": "'; DROP--",
                                "incoterm": "ZZZ", "mode": "teleport", "unit_price": "nan"}, {})
    js = svc.get_shipment(junk)["shipment"]
    ck(js["status"] in K.SHIPMENT_STATUS, "a forged status falls back to a real one (%s)" % js["status"])
    ck(js["incoterm"] in K.INCOTERMS and js["mode"] in K.MODES, "forged incoterm/mode fall back")
    ck(js["unit_price"] == 0, "unit_price 'nan' does not poison the invoice")

    for data, why in [
        ({"cartons": "0", "qty_per_carton": "10"}, "zero cartons"),
        ({"cartons": "-5", "qty_per_carton": "10"}, "negative cartons"),
        ({"cartons": "10", "qty_per_carton": "0"}, "zero pcs/carton"),
        ({"cartons": "10", "qty_per_carton": "-1"}, "negative pcs/carton"),
        ({"cartons": "abc", "qty_per_carton": "10"}, "non-numeric cartons"),
        ({"cartons": "", "qty_per_carton": "10"}, "blank cartons"),
        ({"cartons": "10", "qty_per_carton": "nan"}, "NaN pcs/carton"),
        ({"cartons": "10", "qty_per_carton": "1e400"}, "infinite pcs/carton"),
        ({"cartons": "99999999999999", "qty_per_carton": "1"}, "carton count past int4"),
        ({"cartons": "10.9", "qty_per_carton": "5"}, "fractional carton count"),
        ({"cartons": "10", "qty_per_carton": "10", "net_weight": "-1"}, "negative net weight"),
        ({"cartons": "10", "qty_per_carton": "10", "length_cm": "abc"}, "non-numeric dimension"),
        ({"cartons": "10", "qty_per_carton": "10", "net_weight": "1e400"}, "infinite net weight"),
        ({"cartons": "1000000", "qty_per_carton": "1", "net_weight": "1e300"},
         "net weight that would overflow the total to inf"),
        ({"cartons": "10", "qty_per_carton": "10", "net_weight": "10",
          "gross_weight": "9"}, "gross below net"),
    ]:
        ok, reason = svc.add_carton(sid, data, {"username": "t"})
        ck(not ok, "refused: %-24s (%s)" % (why, reason))

    ok, reason = svc.add_carton(999999, {"cartons": "1", "qty_per_carton": "1"}, {})
    ck(not ok and reason == "shipment_not_found", "carton on a missing shipment refused")
    ok, reason = svc.add_carton(sid, {"cartons": "10", "qty_per_carton": "12",
                                      "net_weight": "", "length_cm": "", "width_cm": "",
                                      "height_cm": "", "style": "S", "size": "M"}, {})
    ck(ok, "blank optional numerics accepted as 'not stated'")
    ck(svc.get_shipment(sid)["totals"]["pieces"] == 120, "totals reflect the new line (10 x 12 = 120)")
    ck(svc.get_shipment(sid)["totals"]["cbm"] == 0.0, "unstated dimensions -> 0 CBM, no crash")
    ck(svc.invoice(sid)["invoice_total"] == 0.00, "price 0 -> invoice 0.00, no divide by zero")

    # ------------------------------------------------------------------
    section("6  double-apply — every mutation called twice")
    cid = conn.execute("SELECT id FROM shp_cartons WHERE shipment_id=?", (sid,)).fetchone()["id"]
    ok1, r1, _ = svc.delete_carton(cid, {})
    ok2, r2, _ = svc.delete_carton(cid, {})
    ck(ok1 and not ok2, "second delete of the same line is refused, not fatal (%s / %s)" % (r1, r2))
    ck(svc.get_shipment(sid)["totals"]["pieces"] == 0, "totals back to zero after the delete")
    ok, r, s_ = svc.delete_carton(999999, {})
    ck(not ok and s_ is None, "deleting a non-existent line returns cleanly")

    # ------------------------------------------------------------------
    section("7  locks — a dispatched packing list is a customs document")
    ok, why = svc.update_shipment(sid, {"status": "dispatched"}, {})
    ck(ok, "planned -> dispatched allowed")
    ok, why = svc.add_carton(sid, {"cartons": "1", "qty_per_carton": "1"}, {})
    ck(not ok and why == "shipment_locked", "locked shipment refuses new carton lines")
    ok, why = svc.update_shipment(sid, {"status": "planned"}, {})
    ck(why == "status_locked", "dispatched cannot go back to planned (%s)" % why)
    ck(svc.get_shipment(sid)["shipment"]["status"] == "dispatched", "status really did not move")
    ok, why = svc.update_shipment(sid, {"unit_price": "1.23"}, {})
    ck(not ok and why == "price_locked", "the invoice unit price is frozen once it has left")
    ck(svc.get_shipment(sid)["shipment"]["unit_price"] == 0, "price really did not move")
    ok, why = svc.update_shipment(999999, {"status": "packed"}, {})
    ck(not ok and why == "not_found", "updating a missing shipment -> not_found (404)")

    # the real detail form re-posts every field, including the unchanged price and status
    form = {"buyer": "EU Buyer A", "destination": "Genoa, IT", "incoterm": "FOB", "mode": "sea",
            "carrier": "NEW FORWARDER", "status": "dispatched", "currency": "USD",
            "unit_price": "0", "etd": "2026-07-21"}
    ok, why = svc.update_shipment(sid, form, {})
    ck(ok and why == "updated",
       "re-posting the whole form with an UNCHANGED price is a clean save, not 'price_locked' (%s)"
       % why)
    ck(svc.get_shipment(sid)["shipment"]["carrier"] == "NEW FORWARDER", "the edit really saved")

    dcid = conn.execute("SELECT id FROM shp_cartons WHERE shipment_id=1 LIMIT 1").fetchone()["id"]
    ok, why, back = svc.delete_carton(dcid, {})
    ck(not ok and why == "shipment_locked" and back == 1,
       "a dispatched packing line cannot be deleted, and the caller is told where to go back to")

    # a cancelled shipment must not be resurrected into a shipped state
    cn = svc.create_shipment({"destination": "X", "status": "planned"}, {})
    svc.add_carton(cn, {"cartons": "10", "qty_per_carton": "10"}, {})
    svc.update_shipment(cn, {"status": "cancelled"}, {})
    svc.update_shipment(cn, {"status": "dispatched"}, {})
    ck(svc.get_shipment(cn)["shipment"]["status"] == "cancelled",
       "a cancelled shipment cannot be flipped to dispatched")

    # a shipment must never be born locked-and-empty (no way to enter its packing list)
    dead = svc.create_shipment({"destination": "X", "status": "dispatched"}, {})
    ok, why = svc.add_carton(dead, {"cartons": "1", "qty_per_carton": "1"}, {})
    ck(ok, "a shipment created 'dispatched' can still be packed (born-locked trap) (%s)" % why)

    # ------------------------------------------------------------------
    section("8  reconciliation arithmetic — recomputed by hand")
    rec = {r["order_no"]: r for r in svc.reconciliation()}
    r = rec["SO-1001"]
    ck(r["qty"] == 12000, "SO-1001 ordered 12,000")
    ck(r["packed"] == 11400 and r["shipped"] == 11400, "SO-1001 packed = shipped = 11,400")
    ck(r["balance"] == 600, "balance 12,000 - 11,400 = 600")
    ck(r["fulfil_pct"] == 95.0, "fulfilment 100 x 11,400 / 12,000 = 95.0%")
    ck(r["short"] and not r["over"], "5% below order > 2% tolerance -> SHORT only")
    r3 = rec["SO-1003"]
    # 40 x (120 + 111) = 9,240 packed against 9,000 ordered; tolerance 180
    ck(r3["packed"] == 9240, "SO-1003 packed 40 x 231 = 9,240")
    ck(r3["over"] and not r3["short"], "9,240 > 9,000 + 180 -> OVER only")
    ck(r3["fulfil_pct"] == round(100.0 * r3["shipped"] / 9000, 1), "SO-1003 fulfil_pct exact")
    r2 = rec["SO-1002"]
    ck(r2["shipped"] == 0 and r2["packed"] == 3000, "SO-1002 planned only -> shipped 0")
    ck(not r2["short"], "nothing has left yet -> not short")
    ck(r2["fulfil_pct"] == 0.0, "0 shipped of a real order = 0.0%, not None")

    conn.execute("UPDATE ord_orders SET qty=11400 WHERE order_no='SO-1001'"); conn.commit()
    rb = {x["order_no"]: x for x in svc.reconciliation()}["SO-1001"]
    ck(not rb["short"] and not rb["over"] and rb["fulfil_pct"] == 100.0,
       "exactly on target is neither short nor over")
    # qty such that ordered - 2% == 11400 exactly -> packed sits ON the boundary
    conn.execute("UPDATE ord_orders SET qty=? WHERE order_no='SO-1001'", (11400 / 0.98,))
    conn.commit()
    rb = {x["order_no"]: x for x in svc.reconciliation()}["SO-1001"]
    ck(not rb["short"], "packed exactly ON the -2% boundary is not flagged short")

    conn.execute("UPDATE ord_orders SET qty=0 WHERE order_no='SO-1001'"); conn.commit()
    rz = {x["order_no"]: x for x in svc.reconciliation()}["SO-1001"]
    ck(rz["fulfil_pct"] is None, "qty 0 -> fulfil_pct None, no ZeroDivisionError")
    ck(not rz["short"] and not rz["over"], "qty 0 -> no flag (nothing to compare against)")
    conn.execute("UPDATE ord_orders SET qty=NULL WHERE order_no='SO-1001'"); conn.commit()
    rn = {x["order_no"]: x for x in svc.reconciliation()}["SO-1001"]
    ck(rn["fulfil_pct"] is None, "qty NULL -> fulfil_pct None, no TypeError")
    ck(rn["balance"] == round(0 - rn["shipped"], 3), "qty NULL -> balance reads as 0 ordered")
    conn.execute("UPDATE ord_orders SET qty=12000 WHERE order_no='SO-1001'"); conn.commit()

    # cancelled shipments are excluded from both packed and shipped
    before = {x["order_no"]: x for x in svc.reconciliation()}["SO-1003"]["packed"]
    sh3 = conn.execute("SELECT id FROM shp_shipments WHERE order_id=(SELECT id FROM ord_orders "
                       "WHERE order_no='SO-1003')").fetchone()["id"]
    conn.execute("UPDATE shp_shipments SET status='cancelled' WHERE id=?", (sh3,)); conn.commit()
    after = {x["order_no"]: x for x in svc.reconciliation()}
    ck("SO-1003" not in after, "a cancelled shipment drops out of reconciliation entirely (%s)"
       % before)
    conn.execute("UPDATE shp_shipments SET status='dispatched' WHERE id=?", (sh3,)); conn.commit()

    # ------------------------------------------------------------------
    section("9  recon_sweep — one bell alert per (order, flag), swept three times")
    conn.execute("DELETE FROM notifications WHERE module='shipping'")
    conn.execute("DELETE FROM shp_recon_alerts"); conn.commit()
    svc.recon_sweep(); svc.recon_sweep(); svc.recon_sweep()
    notes = conn.execute("SELECT title,message,link FROM notifications WHERE module='shipping' "
                         "ORDER BY id").fetchall()
    ck(len(notes) == 2, "3 sweeps raised exactly 2 alerts (1 short + 1 over), got %d" % len(notes))
    ck(all(n["link"] == "/shipping/reconciliation" for n in notes), "every alert deep-links")
    ck(any("Short" in n["title"] for n in notes) and any("Over" in n["title"] for n in notes),
       "one short + one over")
    ck(all("11,400 of 12,000" in n["message"] for n in notes if "Short" in n["title"]),
       "the short message quotes shipped-of-ordered correctly")
    ck(all("600 short" in n["message"] for n in notes if "Short" in n["title"]),
       "the short message quotes the gap correctly (12,000 - 11,400 = 600)")
    rows = conn.execute("SELECT order_id,flag FROM shp_recon_alerts ORDER BY id").fetchall()
    ck(len(rows) == 2, "shp_recon_alerts holds 2 idempotency rows")
    oid1001 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1001'").fetchone()["id"]
    ck(any(x["order_id"] == oid1001 and x["flag"] == "short" for x in rows),
       "the alert row keys on the ORDER id, not the shipment id")

    # ------------------------------------------------------------------
    section("10  dashboard + empty tables")
    d = svc.dashboard()
    tot_pieces = conn.execute(
        "SELECT COALESCE(SUM(c.qty_per_carton*c.cartons),0) c FROM shp_cartons c "
        "JOIN shp_shipments s ON s.id=c.shipment_id WHERE s.status<>'cancelled'").fetchone()["c"]
    ck(d["pieces"] == round(tot_pieces, 0), "dashboard pieces == SUM over live shipments")
    ck(d["planned"] + d["dispatched"] + d["delivered"] <=
       conn.execute("SELECT COUNT(*) c FROM shp_shipments").fetchone()["c"],
       "status counters never exceed the row count")
    ck(d["orders_short"] >= 1 and d["orders_over"] >= 1, "dashboard counts the flagged orders")
    ck(isinstance(d["recon"], list) and isinstance(d["recent"], list), "dashboard panels are lists")

    conn.execute("DELETE FROM shp_cartons"); conn.execute("DELETE FROM shp_shipments"); conn.commit()
    d0 = svc.dashboard()
    ck(d0["cartons"] == 0 and d0["pieces"] == 0 and d0["cbm"] == 0,
       "an empty module dashboards as zeros (COALESCE holds), not NULL")
    ck(svc.list_shipments() == [] and svc.reconciliation() == [], "empty reads return []")
    svc.recon_sweep()
    ck(True, "recon_sweep on an empty module does not raise")

    # ------------------------------------------------------------------
    section("11  SQL is parameterised / filters cannot be injected")
    create_and_seed(conn); conn.commit()
    ck(svc.list_shipments(status="planned' OR '1'='1") == [],
       "a quoted status filter matches nothing (bound, not interpolated)")
    ck(len(svc.list_shipments(status="dispatched")) == 2, "status filter still works")
    ck(len(svc.list_shipments(mode="air")) == 1, "mode filter still works")
    import re as _re
    src = (REPO / "app" / "shipping" / "services.py").read_text(encoding="utf-8")
    ck(not [l for l in src.splitlines() if _re.search(r'execute\w*\(\s*f["\']', l)],
       "no f-string SQL anywhere in services.py")

    # ------------------------------------------------------------------
    section("14  status laundering — dispatched -> cancelled -> planned")
    # The two-click path that used to unfreeze a customs document: cancel a shipment
    # that has already left, then re-open it as 'planned'. Every lock keys on the
    # CURRENT status, so the packing list and the invoice price both came back to life.
    L = svc.create_shipment({"destination": "Genoa, IT", "unit_price": "5.00"}, {})
    svc.add_carton(L, {"cartons": "10", "qty_per_carton": "100"}, {})
    ck(svc.update_shipment(L, {"status": "dispatched"}, {})[0], "planned -> dispatched")
    ck(conn.execute("SELECT dispatched_at FROM shp_shipments WHERE id=?", (L,)).fetchone()
       ["dispatched_at"], "departure is stamped on the row, not inferred from the status")
    ok, why = svc.update_shipment(L, {"status": "cancelled"}, {})
    ck(ok, "a shipment that has left may still be cancelled (%s)" % why)
    ok, why = svc.update_shipment(L, {"status": "planned"}, {})
    ck(not ok and why == "status_locked",
       "cancelled-after-departure CANNOT be re-opened to planned (%s)" % why)
    ck(svc.get_shipment(L)["shipment"]["status"] == "cancelled", "the status really did not move")
    ck(svc.get_shipment(L)["locked"], "so its packing list stays locked")
    ok, why = svc.add_carton(L, {"cartons": "1", "qty_per_carton": "1"}, {})
    ck(not ok and why == "shipment_locked", "no carton can be added through the laundered path")
    lcid = conn.execute("SELECT id FROM shp_cartons WHERE shipment_id=?", (L,)).fetchone()["id"]
    ck(not svc.delete_carton(lcid, {})[0], "no carton can be deleted through it either")
    ok, why = svc.update_shipment(L, {"unit_price": "0.01"}, {})
    ck(not ok and why == "price_locked", "and the issued invoice price stays frozen (%s)" % why)
    ck(svc.get_shipment(L)["shipment"]["unit_price"] == 5.0, "price really did not move")
    # the legitimate mis-click recovery must survive: cancel BEFORE it ever left
    M = svc.create_shipment({"destination": "X", "unit_price": "2.00"}, {})
    svc.update_shipment(M, {"status": "cancelled"}, {})
    ok, why = svc.update_shipment(M, {"status": "planned"}, {})
    ck(ok and svc.get_shipment(M)["shipment"]["status"] == "planned",
       "a shipment cancelled BEFORE departure can still be re-opened (%s)" % why)
    ok, why = svc.update_shipment(M, {"unit_price": "3.00"}, {})
    ck(ok and svc.get_shipment(M)["shipment"]["unit_price"] == 3.0,
       "and repriced, because it never left")
    svc.update_shipment(M, {"status": "cancelled"}, {})
    ok, why = svc.update_shipment(M, {"status": "dispatched"}, {})
    ck(not ok and why == "status_locked", "a void shipment still cannot jump to dispatched")
    # a hand-written / legacy 'dispatched' row with NO stamp must still count as departed
    sd = conn.execute("SELECT id FROM shp_shipments WHERE created_by='seed' AND "
                      "status='dispatched' ORDER BY id LIMIT 1").fetchone()["id"]
    stamp0 = conn.execute("SELECT dispatched_at FROM shp_shipments WHERE id=?",
                          (sd,)).fetchone()["dispatched_at"]
    ck(stamp0, "the seeded dispatched shipment carries a departure stamp")
    conn.execute("UPDATE shp_shipments SET dispatched_at=NULL WHERE id=?", (sd,)); conn.commit()
    svc.update_shipment(sd, {"status": "cancelled"}, {})
    ok, why = svc.update_shipment(sd, {"status": "packed"}, {})
    ck(not ok and why == "status_locked",
       "an unstamped dispatched row still cannot be laundered (status fallback) (%s)" % why)
    conn.execute("UPDATE shp_shipments SET status='dispatched', dispatched_at=? WHERE id=?",
                 (stamp0, sd)); conn.commit()

    # ------------------------------------------------------------------
    section("15  the invoice price is bounded on EVERY path")
    H = svc.create_shipment({"destination": "X", "unit_price": "1e308"}, {})
    svc.add_carton(H, {"cartons": "10", "qty_per_carton": "100"}, {})
    inv = svc.invoice(H)
    ck(inv["shipment"]["unit_price"] <= svc.MAX_INT,
       "an absurd price typed at CREATE is refused, not stored (%r)" % inv["shipment"]["unit_price"])
    ck(inv["invoice_total"] == inv["invoice_total"] and inv["invoice_total"] != float("inf"),
       "the invoice total is a finite number, never inf (%r)" % inv["invoice_total"])
    ck(all(l["amount"] != float("inf") for l in inv["lines"]), "no line amount is inf")
    ck(not svc.update_shipment(H, {"unit_price": "1e308"}, {})[0],
       "the same value is refused on the reprice path (unchanged behaviour)")
    N2 = svc.create_shipment({"destination": "X", "unit_price": "-5"}, {})
    ck(svc.get_shipment(N2)["shipment"]["unit_price"] == 0.0, "a negative price never lands")
    nan_id = svc.create_shipment({"destination": "X", "unit_price": "nan"}, {})
    inf_id = svc.create_shipment({"destination": "X", "unit_price": "inf"}, {})
    ck(svc.get_shipment(nan_id)["shipment"]["unit_price"] == 0.0 and
       svc.get_shipment(inf_id)["shipment"]["unit_price"] == 0.0, "NaN / inf prices land as 0")

    # ------------------------------------------------------------------
    section("16  invoice rounding is HALF-UP on the exact decimal operands")
    # A 3dp garment price lands on the half cent constantly, and round() is half-EVEN:
    # round(3.855, 2) is 3.85 and round(7 * 2.675, 2) is 18.72 — both a cent short of
    # what the buyer's calculator and the customs declaration say.
    ck(svc._money(1, 3.855) == 3.86, "1 x 3.855 = 3.86, round() says %.2f" % round(3.855, 2))
    ck(svc._money(7, 2.675) == 18.73,
       "7 x 2.675 = 18.73, round() says %.2f" % round(7 * 2.675, 2))
    ck(svc._money(1, 1.005) == 1.01, "1 x 1.005 = 1.01, round() says %.2f" % round(1.005, 2))
    ck(svc._money(3000, 3.85) == 11550.00, "3000 x 3.85 = 11,550.00 (the ordinary case is unmoved)")
    ck(svc._money(0, 3.85) == 0.0 and svc._money(10, 0) == 0.0, "zero pieces / zero price = 0.00")
    ck(svc._money(2000000000, 2000000000) == 4e18,
       "the bounded ceiling does not raise decimal.InvalidOperation")
    P = svc.create_shipment({"destination": "X", "unit_price": "2.675"}, {})
    svc.add_carton(P, {"cartons": "7", "qty_per_carton": "1", "style": "S"}, {})
    ip = svc.invoice(P)
    ck(ip["invoice_total"] == 18.73, "the page total agrees with the line: %r" % ip["invoice_total"])
    ck(round(sum(l["amount"] for l in ip["lines"]), 2) == ip["invoice_total"],
       "the invoice still foots line by line")

    # ------------------------------------------------------------------
    section("17  dates on an export document are real calendar dates")
    D = svc.create_shipment({"destination": "X", "etd": "not-a-date",
                             "eta": "2026-13-45", "invoice_date": "0000-00-00"}, {})
    ds = svc.get_shipment(D)["shipment"]
    ck(ds["etd"] is None and ds["eta"] is None and ds["invoice_date"] is None,
       "garbage dates never reach a packing list (%r/%r/%r)"
       % (ds["etd"], ds["eta"], ds["invoice_date"]))
    ok, why = svc.update_shipment(D, {"etd": "2026-08-01"}, {})
    ck(ok and svc.get_shipment(D)["shipment"]["etd"] == "2026-08-01", "a real date saves")
    ok, why = svc.update_shipment(D, {"etd": "31/12/2026"}, {})
    ck(why == "bad_date", "a non-ISO date is refused with a reason (%s)" % why)
    ck(svc.get_shipment(D)["shipment"]["etd"] == "2026-08-01", "and the good date is not clobbered")
    ok, why = svc.update_shipment(D, {"etd": ""}, {})
    ck(ok and svc.get_shipment(D)["shipment"]["etd"] is None, "a blank date clears the field")
    ck(svc._date("2026-08-01T00:00:00") == "2026-08-01", "a datetime posted by a picker is trimmed")

    # ------------------------------------------------------------------
    section("18  create_and_seed x3 AFTER the migration (still idempotent)")
    s_before = conn.execute("SELECT COUNT(*) c FROM shp_shipments").fetchone()["c"]
    c_before = conn.execute("SELECT COUNT(*) c FROM shp_cartons").fetchone()["c"]
    create_and_seed(conn); create_and_seed(conn); create_and_seed(conn); conn.commit()
    ck(conn.execute("SELECT COUNT(*) c FROM shp_shipments").fetchone()["c"] == s_before,
       "3 more seeds on a populated module add nothing")
    ck(conn.execute("SELECT COUNT(*) c FROM shp_cartons").fetchone()["c"] == c_before,
       "and destroy nothing")
    ck(conn.execute("SELECT COUNT(*) c FROM shp_shipments WHERE created_by='seed' AND "
                    "status IN ('dispatched','delivered') AND dispatched_at IS NULL"
                    ).fetchone()["c"] == 0, "every departed seed row keeps its stamp")

    conn.close()

# ----------------------------------------------------------------------
section("19  route security — decorators, methods, existence checks")
import ast                                            # noqa: E402
rsrc = (REPO / "app" / "routes" / "shipping.py").read_text(encoding="utf-8")
routes, problems = [], []
for node in ast.walk(ast.parse(rsrc)):
    if not isinstance(node, ast.FunctionDef):
        continue
    decs = []
    for dd in node.decorator_list:
        if isinstance(dd, ast.Call) and isinstance(dd.func, ast.Attribute):
            decs.append(("route", dd))
        elif isinstance(dd, ast.Call) and isinstance(dd.func, ast.Name):
            decs.append((dd.func.id, dd))
        elif isinstance(dd, ast.Name):
            decs.append((dd.id, None))
    names = [n for n, _ in decs]
    if "route" not in names:
        continue
    rt = [x for n, x in decs if n == "route"][0]
    path = rt.args[0].value
    methods = []
    for kw in rt.keywords:
        if kw.arg == "methods":
            methods = [e.value for e in kw.value.elts]
    perm = [x.args[0].value for n, x in decs if n == "permission_required"]
    routes.append((path, node.name, methods or ["GET"], perm))
    if "login_required" not in names:
        problems.append("%s has no @login_required" % node.name)
    if not perm:
        problems.append("%s has no @permission_required" % node.name)
for p, n, meth, perm in routes:
    print("  ---  %-40s %-18s %-6s %s" % (p, n, ",".join(meth), perm))
MUT = ("create", "update", "add", "delete")
ck(not problems, "every route has @login_required + @permission_required %s" % (problems or ""))
ck(all("POST" in m for _, n, m, _ in routes if any(k in n for k in MUT)),
   "every state-changing route is POST-only")
ck(all(p in (["shp_view"], ["shp_manage"]) for _, _, _, p in routes),
   "only the module's own perms are used")
ck(all("shp_manage" in p for _, n, _, p in routes if any(k in n for k in MUT)),
   "every mutating route demands shp_manage")
ck(rsrc.count("abort(404)") >= 3, "read routes abort(404) on a missing id (no IDOR read)")

# ----------------------------------------------------------------------
section("20  templates parse + i18n keys complete in both directions")
import re as _re2                                     # noqa: E402
from jinja2 import Environment, FileSystemLoader      # noqa: E402
env = Environment(loader=FileSystemLoader(str(REPO / "app" / "templates")))
tdir = REPO / "app" / "templates" / "shipping"
used = set()
for f in sorted(tdir.glob("*.html")):
    s = f.read_text(encoding="utf-8")
    try:
        env.parse(s)
        N[0] += 1
        print("  ok   %s parses" % f.name)
    except Exception as e:
        ck(False, "%s parses (%s)" % (f.name, e))
    used |= set(_re2.findall(r'data-i18n(?:-ph)?="([^"]+)"', s))
from app.shipping.constants import I18N                # noqa: E402
missing = sorted(used - set(I18N))
unused = sorted(set(I18N) - used)
ck(not missing, "no template key is missing from I18N %s" % (missing or ""))
ck(not unused, "no dead keys in I18N %s" % (unused or ""))
ck(all(len(v) == 3 and all(isinstance(x, str) and x.strip() for x in v) for v in I18N.values()),
   "every I18N entry is a full (en, ar, tr) triple")
CODE_ONLY = {"shp.ph.carton_no", "shp.ph.currency"}
same = [k for k, v in I18N.items() if k not in CODE_ONLY and (v[1] == v[0] or v[2] == v[0])]
ck(not same, "no Arabic/Turkish value is an English copy %s" % (same[:5] or ""))
noar = [k for k, v in I18N.items()
        if k not in CODE_ONLY and not _re2.search(r"[؀-ۿ]", v[1])]
ck(not noar, "every Arabic value really is Arabic script %s" % (noar[:5] or ""))

# ======================================================================
# SECOND PASS — ground the first pass did not cover: join fan-out across
# several shipments on one order, legacy NULL rows, whole-piece packing,
# the departure stamp under a double-apply, grouping-before-rounding on the
# invoice, scale, and the templates actually RENDERING behind real auth.
# ======================================================================
with app.app_context():
    from app.db import get_db as _get_db                 # noqa: E402
    from app.shipping import services as svc             # noqa: E402
    conn = _get_db()
    NOW = "2026-07-25 00:00:00"

    # ------------------------------------------------------------------
    section("21  join fan-out — two shipments, four carton lines, one order")
    oid = conn.execute(
        "INSERT INTO ord_orders (order_no,buyer,style_ref,style_name,qty,unit_price,currency,"
        "status,ship_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("SO-ADV-1", "Adv Buyer", "TC-ADV", "Adv tee", 10000, 2.50, "USD",
         "open", "2026-09-01", NOW)).lastrowid
    conn.commit()
    s1 = svc.create_shipment({"order_id": oid, "destination": "Hamburg"}, {"username": "adv"})
    s2 = svc.create_shipment({"order_id": oid, "destination": "Hamburg"}, {"username": "adv"})
    CTN = {"style": "TC-ADV", "colour": "Red", "size": "M", "net_weight": 10,
           "gross_weight": 11, "length_cm": 60, "width_cm": 40, "height_cm": 30}
    for sid_, n in ((s1, 30), (s1, 20), (s2, 25), (s2, 25)):
        ok, why = svc.add_carton(sid_, {**CTN, "qty_per_carton": 100, "cartons": n}, {})
        ck(ok, "carton line %d x 100 pcs added to shipment %s (%s)" % (n, sid_, why))
    ck(svc.update_shipment(s1, {"status": "dispatched"}, {})[0], "shipment 1 dispatched")
    lst = {r["id"]: r for r in svc.list_shipments(order_id=oid)}
    ck(len(lst) == 2, "the list page shows 2 rows, not 4 (one per carton line) — %d" % len(lst))
    ck(lst[s1]["cartons"] == 50 and lst[s1]["pieces"] == 5000,
       "list row 1 = 50 cartons / 5,000 pcs (%s/%s)" % (lst[s1]["cartons"], lst[s1]["pieces"]))
    ck(lst[s2]["cartons"] == 50 and lst[s2]["pieces"] == 5000, "list row 2 = 50 cartons / 5,000 pcs")
    rows = [r for r in svc.reconciliation() if r["order_no"] == "SO-ADV-1"]
    ck(len(rows) == 1, "ONE reconciliation row per order, not one per shipment (%d)" % len(rows))
    r = rows[0]
    ck(r["shipments"] == 2, "both shipments counted (%s)" % r["shipments"])
    ck(r["packed"] == 10000,
       "packed = 3000+2000+2500+2500 = 10,000 — no cartesian fan-out (%s)" % r["packed"])
    ck(r["shipped"] == 5000, "shipped counts only what left = 5,000 (%s)" % r["shipped"])
    ck(r["balance"] == 5000 and r["fulfil_pct"] == 50.0,
       "balance 10,000-5,000 = 5,000 and fulfilment 50.0%% (%s/%s)"
       % (r["balance"], r["fulfil_pct"]))
    ck(not r["short"] and not r["over"],
       "half shipped but everything packed covers the order -> not short")
    ck(svc.update_shipment(s2, {"status": "cancelled"}, {})[0], "the balance shipment is cancelled")
    r = [x for x in svc.reconciliation() if x["order_no"] == "SO-ADV-1"][0]
    ck(r["packed"] == 5000 and r["shipments"] == 1,
       "a cancelled shipment leaves packed AND the shipment count (%s/%s)"
       % (r["packed"], r["shipments"]))
    ck(r["short"] and not r["over"], "with the balance void the order is now SHORT")
    ck(r["fulfil_pct"] == 50.0, "fulfilment is unchanged — cancelling ships nothing")

    # ------------------------------------------------------------------
    section("22  legacy / hand-written carton rows with NULL numerics")
    conn.execute("INSERT INTO shp_cartons (shipment_id,carton_no,style,colour,size,"
                 "qty_per_carton,cartons,net_weight,gross_weight,length_cm,width_cm,height_cm,"
                 "created_at) VALUES (?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,?)",
                 (s1, NOW))
    conn.commit()
    b = svc.get_shipment(s1)
    lrow = [x for x in svc.list_shipments(order_id=oid) if x["id"] == s1][0]
    ck(b["totals"]["pieces"] == lrow["pieces"] == 5000,
       "a NULL row changes nothing: detail == list SQL == 5,000 (%s/%s)"
       % (b["totals"]["pieces"], lrow["pieces"]))
    ck(b["totals"]["cbm"] == lrow["cbm"],
       "detail CBM == list SQL CBM with a NULL row (%s/%s)" % (b["totals"]["cbm"], lrow["cbm"]))
    ck(b["totals"]["net"] == 500.0 and b["totals"]["gross"] == 550.0,
       "net 10x50 = 500, gross 11x50 = 550 (%s/%s)" % (b["totals"]["net"], b["totals"]["gross"]))
    ck(len(b["summary"]) == 2, "the NULL row groups as its own blank SKU row (%d)" % len(b["summary"]))
    iv = svc.invoice(s1)
    ck(iv is not None and iv["invoice_total"] == 12500.00,
       "5,000 x 2.50 = 12,500.00 — the NULL row adds 0.00 (%s)" % (iv or {}).get("invoice_total"))
    ck(all(l["amount"] == 0.0 for l in iv["lines"] if not l["style"]),
       "the blank SKU line is priced at 0.00, not skipped or crashed")

    # ------------------------------------------------------------------
    section("23  a piece is a whole garment (the printed pieces column must foot)")
    s3 = svc.create_shipment({"destination": "Genoa", "unit_price": "2.50"}, {})
    ok, why = svc.add_carton(s3, {"qty_per_carton": "60.5", "cartons": "10"}, {})
    ck(not ok and why == "bad_qty",
       "60.5 pcs/carton is refused: '{:,.0f}' would print 3,024 pcs while the invoice "
       "charged 3,024.5 (%s/%s)" % (ok, why))
    ok, why = svc.add_carton(s3, {"qty_per_carton": "0.5", "cartons": "10"}, {})
    ck(not ok and why == "bad_qty", "half a garment per carton is refused (%s)" % why)
    ok, why = svc.add_carton(s3, {"qty_per_carton": "60.0", "cartons": "10.0"}, {})
    ck(ok, "a whole number typed with a decimal point is still accepted (%s)" % why)
    ok, why = svc.add_carton(s3, {"qty_per_carton": " 12 ", "cartons": " 5 "}, {})
    ck(ok, "padded numerics from a form are accepted (%s)" % why)
    t3 = svc.get_shipment(s3)["totals"]
    ck(t3["pieces"] == 660 and float(t3["pieces"]).is_integer(),
       "60x10 + 12x5 = 660 whole pieces (%s)" % t3["pieces"])
    ck(svc.invoice(s3)["invoice_total"] == 1650.00,
       "660 x 2.50 = 1,650.00 and the printed pieces column foots against it")
    # why the guard has to exist, shown on the two functions that disagreed:
    ck("{:,.0f}".format(302.5) == "302" and svc._money(302.5, 2.50) == 756.25
       and svc._money(302, 2.50) == 755.00,
       "the document would print 302 pcs and charge 756.25 instead of 755.00 — 1.25 USD "
       "of undeclared value per line")

    # ------------------------------------------------------------------
    section("28  independent recomputation — 20 shipments x random carton lines")
    import random as _rnd                                # noqa: E402
    from decimal import Decimal as _D                     # noqa: E402
    _rnd.seed(20260725)
    exp = {}                       # shipment id -> independently computed truth
    for k in range(20):
        price = _rnd.choice(["1.005", "2.675", "3.855", "6.4", "12.125"])
        sk = svc.create_shipment({"destination": "RNG", "unit_price": price}, {})
        want = {"cartons": 0, "pieces": _D(0), "net": _D(0), "gross": _D(0), "cbm": _D(0),
                "cbm_exact": _D(0), "price": _D(price), "sku": {}}
        for _ in range(_rnd.randint(1, 8)):
            per, n = _rnd.randint(1, 400), _rnd.randint(1, 500)
            net, gross = _rnd.randint(1, 3000) / 100.0, _rnd.randint(3000, 6000) / 100.0
            L, W, H = (_rnd.randint(10, 120) for _ in range(3))
            sku = (_rnd.choice(["A", "B"]), _rnd.choice(["Red", "Blue"]),
                   _rnd.choice(["S", "M"]))
            ok, why = svc.add_carton(sk, {"qty_per_carton": per, "cartons": n,
                                          "style": sku[0], "colour": sku[1], "size": sku[2],
                                          "net_weight": net, "gross_weight": gross,
                                          "length_cm": L, "width_cm": W, "height_cm": H}, {})
            if not ok:
                ck(False, "random line refused (%s)" % why)
                continue
            want["cartons"] += n
            want["pieces"] += _D(per) * n
            want["net"] += _D(str(net)) * n
            want["gross"] += _D(str(gross)) * n
            # CBM the long way round: three separate cm->m divisions, not one /1e6.
            # The detail page foots line by line, so the expectation rounds per line too
            # (4dp); cbm_exact keeps the unrounded truth to bound the drift below.
            line_cbm = (_D(L) / 100) * (_D(W) / 100) * (_D(H) / 100) * n
            want["cbm"] += round(line_cbm, 4)
            want["cbm_exact"] += line_cbm
            want["sku"][sku] = want["sku"].get(sku, _D(0)) + _D(per) * n
        exp[sk] = want
    lst = {r["id"]: r for r in svc.list_shipments()}
    bad = []
    for sk, want in exp.items():
        t = svc.get_shipment(sk)["totals"]
        iv = svc.invoice(sk)
        # every line amount rounded HALF-UP, then footed — the customs-invoice convention
        want_total = sum((p * want["price"]).quantize(_D("0.01"), "ROUND_HALF_UP")
                         for p in want["sku"].values())
        for label, got, wanted in (
                ("cartons", t["cartons"], int(want["cartons"])),
                ("pieces", t["pieces"], float(want["pieces"])),
                ("net", t["net"], float(round(want["net"], 3))),
                ("gross", t["gross"], float(round(want["gross"], 3))),
                ("cbm", t["cbm"], float(round(want["cbm"], 4))),
                ("sql pieces", lst[sk]["pieces"], float(want["pieces"])),
                ("sql cartons", lst[sk]["cartons"], int(want["cartons"])),
                # the list page rounds the SUM, the detail page sums the ROUNDED lines,
                # so they may disagree by ~1e-4 m3 (0.1 litre) on a many-line shipment
                ("sql cbm", lst[sk]["cbm"], float(round(want["cbm_exact"], 4))),
                ("invoice lines", len(iv["lines"]), len(want["sku"])),
                ("invoice total", iv["invoice_total"], float(want_total))):
            if abs(got - wanted) > 1e-4:
                bad.append("shipment %s %s: %r != %r" % (sk, label, got, wanted))
        drift = abs(_D(str(t["cbm"])) - want["cbm_exact"])
        if drift > _D("0.001"):
            bad.append("shipment %s cbm drift %s" % (sk, drift))
    ck(not bad, "every total, SQL aggregate and invoice matches an independent "
                "Decimal recomputation %s" % (bad[:4] or ""))

    # ------------------------------------------------------------------
    section("24  the departure stamp survives a double-apply")
    s4 = svc.create_shipment({"destination": "Izmir"}, {})
    svc.add_carton(s4, {**CTN, "qty_per_carton": 10, "cartons": 2}, {})

    def _stamp(i):
        return conn.execute("SELECT dispatched_at FROM shp_shipments WHERE id=?",
                            (i,)).fetchone()["dispatched_at"]

    ck(_stamp(s4) is None, "a planned shipment carries no departure stamp")
    ck(svc.update_shipment(s4, {"status": "dispatched"}, {})[0], "dispatched once")
    st0 = _stamp(s4)
    ck(bool(st0), "the stamp is written on departure (%s)" % st0)
    ok, why = svc.update_shipment(s4, {"status": "dispatched"}, {})
    ck(_stamp(s4) == st0, "dispatching a second time does not move the stamp (%s)" % why)
    ck(svc.update_shipment(s4, {"status": "delivered"}, {})[0], "dispatched -> delivered")
    ck(_stamp(s4) == st0, "delivery does not overwrite the departure stamp")
    ck(not svc.add_carton(s4, {**CTN, "qty_per_carton": 1, "cartons": 1}, {})[0],
       "and the packing list stays locked throughout")

    # ------------------------------------------------------------------
    section("25  the invoice groups BEFORE it rounds")
    s5 = svc.create_shipment({"destination": "Piraeus", "unit_price": "3.855"}, {})
    for _ in range(2):
        svc.add_carton(s5, {"qty_per_carton": 1, "cartons": 1,
                            "style": "S", "colour": "C", "size": "M"}, {})
    iv5 = svc.invoice(s5)
    ck(len(iv5["lines"]) == 1, "two carton lines of one SKU are one invoice line (%d)"
       % len(iv5["lines"]))
    ck(iv5["lines"][0]["pieces"] == 2, "the merged line carries 2 pcs")
    ck(iv5["invoice_total"] == 7.71,
       "2 x 3.855 = 7.71; rounding each carton line first would declare 7.72 (%s)"
       % iv5["invoice_total"])
    ck(round(sum(l["amount"] for l in iv5["lines"]), 2) == iv5["invoice_total"],
       "the total still foots line by line")

    # ------------------------------------------------------------------
    section("26  scale — 1,200 order-linked shipments in one reconciliation")
    for i in range(1200):
        o = conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,unit_price,currency,status,"
                         "created_at) VALUES (?,?,?,?,?,?,?)",
                         ("SO-BULK-%04d" % i, "Bulk", 100, 1.0, "USD", "open", NOW)).lastrowid
        sb = conn.execute("INSERT INTO shp_shipments (shipment_no,order_id,buyer,destination,"
                          "status,unit_price,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                          ("SH-BULK-%04d" % i, o, "Bulk", "X", "dispatched", 1.0, "bulk",
                           NOW)).lastrowid
        conn.execute("INSERT INTO shp_cartons (shipment_id,qty_per_carton,cartons,created_at) "
                     "VALUES (?,?,?,?)", (sb, 100, 1, NOW))
    conn.commit()
    big = svc.reconciliation()
    ck(len(big) >= 1200,
       "reconciliation over 1,200 orders returns every row — the IN(...) parameter list does "
       "not silently fall back to [] (%d rows)" % len(big))
    ck(sum(1 for r in big if r["order_no"].startswith("SO-BULK")) == 1200,
       "all 1,200 bulk orders are present")
    ck(all(r["fulfil_pct"] == 100.0 for r in big if r["order_no"].startswith("SO-BULK")),
       "each bulk order reads 100 packed of 100 ordered = 100.0%")
    dsh = svc.dashboard()
    ck(dsh["orders_short"] >= 1 and isinstance(dsh["recon"], list),
       "the dashboard still builds at that size")
    conn.close()

# ----------------------------------------------------------------------
section("27  every route RENDERS behind real auth (not just parses)")
from app.routes.shipping import bp as _shp_bp            # noqa: E402
ck("shipping" in app.blueprints, "the blueprint is registered on the app (app/__init__.py)")
if "shipping" not in app.blueprints:                     # not spliced in yet -> test it anyway
    app.register_blueprint(_shp_bp)
import re as _re0                                        # noqa: E402
_nav = (REPO / "app" / "navigation.py").read_text(encoding="utf-8")
_navkeys = set(_re0.findall(r'\(\s*"([a-z_0-9]+)"\s*,\s*"nav\.', _nav))
_actives = set(_re0.findall(r'active="([^"]+)"',
                            (REPO / "app" / "routes" / "shipping.py").read_text(encoding="utf-8")))
ck(_actives <= _navkeys,
   "every page passes a nav key that EXISTS in app/navigation.py, or base.html renders the "
   "whole sidebar section collapsed with nothing highlighted %s" % (sorted(_actives - _navkeys),))
with app.app_context():
    from app.db import get_db as _g                      # noqa: E402
    _c = _g()
    admin_id = _c.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                          "created_at) VALUES (?,?,?,?,1,?)",
                          ("adv_admin", "x", "Adv Admin", "super_admin", NOW)).lastrowid
    weak_id = _c.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                         "created_at) VALUES (?,?,?,?,1,?)",
                         ("adv_weak", "x", "Adv Weak", "no_such_role", NOW)).lastrowid
    _c.commit()
    SID = _c.execute("SELECT id FROM shp_shipments WHERE created_by='seed' ORDER BY id"
                     ).fetchone()["id"]
    CID = _c.execute("SELECT id FROM shp_cartons WHERE shipment_id=?", (SID,)).fetchone()["id"]
    OPEN_ID = _c.execute("SELECT id FROM shp_shipments WHERE status='planned' ORDER BY id DESC"
                         ).fetchone()["id"]
    _c.close()

cl = app.test_client()
anon = cl.get("/shipping/")
ck(anon.status_code == 302 and "/login" in (anon.headers.get("Location") or ""),
   "anonymous GET /shipping/ redirects to login (%s)" % anon.status_code)
with cl.session_transaction() as sess:
    sess["uid"] = weak_id
    sess["ep"] = 0
ck(cl.get("/shipping/").status_code == 403, "a role without shp_view gets 403")
with cl.session_transaction() as sess:
    sess["uid"] = admin_id
    sess["ep"] = 0
PAGES = ["/shipping/", "/shipping/shipments", "/shipping/shipments?status=dispatched&mode=sea",
         "/shipping/shipments/new", "/shipping/reconciliation",
         "/shipping/shipments/%d" % SID, "/shipping/shipments/%d/packing-list" % SID,
         "/shipping/shipments/%d/invoice" % SID]
for p in PAGES:
    rr = cl.get(p)
    ck(rr.status_code == 200, "GET %s renders 200 (%s)" % (p, rr.status_code))
body = cl.get("/shipping/shipments/%d/invoice" % SID).get_data(as_text=True)
ck("jinja2" not in body.lower() and "Undefined" not in body, "no Jinja error leaked into the page")
for p in ["/shipping/shipments/999999", "/shipping/shipments/999999/invoice",
          "/shipping/shipments/999999/packing-list"]:
    ck(cl.get(p).status_code == 404, "GET %s -> 404, no IDOR / 500 (%s)" % (p, cl.get(p).status_code))
ck(cl.get("/shipping/cartons/%d/delete" % CID).status_code == 405,
   "GET on the delete route is 405 — state changes are POST-only")
noc = cl.post("/shipping/shipments/%d/cartons" % OPEN_ID, data={"qty_per_carton": 1, "cartons": 1})
ck(noc.status_code in (400, 302), "a POST without a CSRF token is refused (%s)" % noc.status_code)
import re as _re3                                        # noqa: E402
form = cl.get("/shipping/shipments/%d" % OPEN_ID).get_data(as_text=True)
tok = _re3.search(r'name="_csrf" value="([^"]+)"', form).group(1)
with app.app_context():
    from app.shipping import services as _svc            # noqa: E402
    before = _svc.get_shipment(OPEN_ID)["totals"]["pieces"]
    posted = cl.post("/shipping/shipments/%d/cartons" % OPEN_ID,
                     data={"_csrf": tok, "qty_per_carton": "7", "cartons": "3",
                           "style": "RT", "colour": "Blue", "size": "S"})
    after = _svc.get_shipment(OPEN_ID)["totals"]["pieces"]
    ck(posted.status_code == 302 and after == before + 21,
       "a real CSRF-carrying POST adds 7x3 = 21 pcs (%s -> %s)" % (before, after))
    bad = cl.post("/shipping/shipments/%d/cartons" % OPEN_ID,
                  data={"_csrf": tok, "qty_per_carton": "-7", "cartons": "3"})
    ck(bad.status_code == 302 and _svc.get_shipment(OPEN_ID)["totals"]["pieces"] == after,
       "a negative quantity POSTs cleanly to a flash, never to the database")

print("\n%d checks, %d failed" % (N[0], len(FAIL)))
for f in FAIL:
    print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
