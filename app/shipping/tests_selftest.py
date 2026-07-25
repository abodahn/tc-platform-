"""
Shipping self-test — service + schema layer against an isolated throwaway DB.
Run:  python app/shipping/tests_selftest.py
Asserts the packing/CBM/invoice arithmetic exactly, plus the negative cases
(zero, None, empty, non-numeric, missing order, divide-by-zero, double-apply).
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="shp_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                    # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                       # noqa: E402

app = create_app()
FAILS = []
RUN = [0]


def check(name, cond):
    RUN[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)


with app.app_context():
    from app.db import get_db
    from app.shipping.schema import create_and_seed
    from app.shipping import services as svc
    from app.shipping.constants import QTY_TOLERANCE_PCT

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    print("\n[1] pure packing arithmetic")
    m = svc.line_math({"qty_per_carton": 60, "cartons": 50, "net_weight": 12.5,
                       "gross_weight": 13.4, "length_cm": 60, "width_cm": 40, "height_cm": 30})
    check("pieces = 60 x 50 = 3000", m["pieces"] == 3000)
    check("net = 12.5 x 50 = 625.0", m["net_total"] == 625.0)
    check("gross = 13.4 x 50 = 670.0", m["gross_total"] == 670.0)
    # 0.60 x 0.40 x 0.30 m = 0.072 m3 per carton x 50 = 3.6
    check("cbm = 0.6*0.4*0.3*50 = 3.6", m["cbm"] == 3.6)

    print("\n[2] negative / missing inputs never crash and never go negative")
    check("all None -> zeros", svc.line_math({}) == {"pieces": 0, "net_total": 0,
                                                     "gross_total": 0, "cbm": 0})
    check("empty strings -> zeros", svc.line_math(
        {"qty_per_carton": "", "cartons": "", "length_cm": ""})["pieces"] == 0)
    check("non-numeric -> zeros", svc.line_math(
        {"qty_per_carton": "abc", "cartons": "x", "height_cm": "10cm"})["pieces"] == 0)
    check("negative cartons clamp to 0", svc.line_math(
        {"qty_per_carton": 10, "cartons": -5, "length_cm": 60, "width_cm": 40,
         "height_cm": 30})["pieces"] == 0)
    check("negative dimension cannot make negative CBM", svc.line_math(
        {"cartons": 2, "length_cm": -60, "width_cm": 40, "height_cm": 30})["cbm"] == 0)
    check("zero height -> 0 CBM, no crash", svc.line_math(
        {"cartons": 2, "length_cm": 60, "width_cm": 40, "height_cm": 0})["cbm"] == 0)

    print("\n[3] seeded demo shipments")
    ships = svc.list_shipments()
    check("3 demo shipments seeded", len(ships) == 3)
    check("shipment_no generated", all(s["shipment_no"] for s in ships))
    s1 = [s for s in ships if s["invoice_no"] == "INV-2601"][0]
    b = svc.get_shipment(s1["id"])
    check("SH1 has 190 cartons", b["totals"]["cartons"] == 190)
    check("SH1 has 11,400 pieces", b["totals"]["pieces"] == 11400)
    # 190 cartons x 0.072 m3 = 13.68
    check("SH1 CBM = 13.68", b["totals"]["cbm"] == 13.68)
    # net: (12.5*50)+(13.1*45)+(12.5*50)+(13.1*45) = 625+589.5+625+589.5 = 2429.0
    check("SH1 net = 2429.0 kg", b["totals"]["net"] == 2429.0)
    # gross: (13.4*50)+(14.0*45)+(13.4*50)+(14.0*45) = 670+630+670+630 = 2600.0
    check("SH1 gross = 2600.0 kg", b["totals"]["gross"] == 2600.0)
    check("gross >= net", b["totals"]["gross"] >= b["totals"]["net"])
    check("packing summary groups to 4 style/colour/size rows", len(b["summary"]) == 4)
    check("summary pieces foot to the total",
          round(sum(g["pieces"] for g in b["summary"]), 3) == b["totals"]["pieces"])
    check("linked to a demo order", b["order"] is not None and b["order"]["order_no"] == "SO-1001")
    check("dispatched shipment is locked", b["locked"] is True)

    print("\n[4] commercial invoice")
    inv = svc.invoice(s1["id"])
    check("unit price snapshotted from the order = 3.85", inv["shipment"]["unit_price"] == 3.85)
    # 11,400 pcs x 3.85 = 43,890.00
    check("invoice total = 43,890.00", inv["invoice_total"] == 43890.00)
    check("total = sum of the rounded lines",
          inv["invoice_total"] == round(sum(l["amount"] for l in inv["lines"]), 2))
    check("every line amount = pieces x unit price",
          all(l["amount"] == round(l["pieces"] * 3.85, 2) for l in inv["lines"]))
    check("invoice of a missing shipment is None", svc.invoice(999999) is None)

    print("\n[5] write path validation (negative cases)")
    sid = svc.create_shipment({"destination": "Rotterdam, NL", "order_id": "",
                               "incoterm": "FOB", "mode": "sea"}, {"username": "selftest"})
    check("stand-alone shipment (no order) created", bool(sid))
    check("no order -> unit price 0, no crash",
          svc.get_shipment(sid)["shipment"]["unit_price"] == 0)
    check("no order -> order is None", svc.get_shipment(sid)["order"] is None)
    check("bad order id is ignored, not fatal",
          bool(svc.create_shipment({"destination": "X", "order_id": "not-a-number"}, None)))
    check("zero cartons refused", svc.add_carton(sid, {"qty_per_carton": 10, "cartons": 0}, None)
          == (False, "bad_qty"))
    check("non-numeric qty refused", svc.add_carton(sid, {"qty_per_carton": "abc", "cartons": 5}, None)
          == (False, "bad_qty"))
    check("empty qty refused", svc.add_carton(sid, {"qty_per_carton": "", "cartons": ""}, None)
          == (False, "bad_qty"))
    check("negative weight refused", svc.add_carton(
        sid, {"qty_per_carton": 10, "cartons": 2, "net_weight": -1}, None) == (False, "bad_number"))
    check("gross below net refused", svc.add_carton(
        sid, {"qty_per_carton": 10, "cartons": 2, "net_weight": 10, "gross_weight": 8}, None)
        == (False, "gross_below_net"))
    check("carton on a missing shipment refused", svc.add_carton(
        999999, {"qty_per_carton": 10, "cartons": 1}, None) == (False, "shipment_not_found"))
    ok, _ = svc.add_carton(sid, {"qty_per_carton": 24, "cartons": 10, "net_weight": 9,
                                 "gross_weight": 10, "length_cm": 50, "width_cm": 40,
                                 "height_cm": 25, "style": "TC-X", "colour": "Red",
                                 "size": "M"}, None)
    check("valid carton line accepted", ok)
    check("totals reflect the new line", svc.get_shipment(sid)["totals"]["pieces"] == 240)
    check("locked shipment refuses new lines",
          svc.add_carton(s1["id"], {"qty_per_carton": 1, "cartons": 1}, None)
          == (False, "shipment_locked"))

    print("\n[6] delete / lock")
    cid = svc.get_shipment(sid)["cartons"][0]["id"]
    locked_cid = svc.get_shipment(s1["id"])["cartons"][0]["id"]
    # delete_carton returns (ok, reason, shipment_id) — the id lets the caller redirect back
    check("cannot delete a dispatched packing line",
          svc.delete_carton(locked_cid, None)[:2] == (False, "shipment_locked"))
    okd = svc.delete_carton(cid, None)[0]
    check("open packing line deleted", okd)
    check("deleting twice is refused, not fatal", svc.delete_carton(cid, None)[1] == "not_found")
    check("totals back to zero", svc.get_shipment(sid)["totals"]["pieces"] == 0)
    check("invoice of an empty shipment = 0.00, no divide by zero",
          svc.invoice(sid)["invoice_total"] == 0.0)

    print("\n[7] reconciliation")
    rec = {r["order_no"]: r for r in svc.reconciliation()}
    check("3 demo orders reconciled", len(rec) == 3)
    r1 = rec["SO-1001"]
    check("SO-1001 ordered 12,000", r1["qty"] == 12000)
    check("SO-1001 shipped 11,400", r1["shipped"] == 11400)
    check("SO-1001 balance 600 short", r1["balance"] == 600)
    check("SO-1001 fulfilment 95.0%", r1["fulfil_pct"] == 95.0)
    check("SO-1001 flagged short (5%% > %.0f%% tolerance)" % QTY_TOLERANCE_PCT, r1["short"] is True)
    check("SO-1001 not flagged over", r1["over"] is False)
    r3 = rec["SO-1003"]
    check("SO-1003 packed 9,240 vs 9,000 ordered", r3["packed"] == 9240 and r3["qty"] == 9000)
    check("SO-1003 flagged over", r3["over"] is True)
    check("SO-1003 not flagged short", r3["short"] is False)
    r2 = rec["SO-1002"]
    check("SO-1002 planned only -> shipped 0", r2["shipped"] == 0)
    check("SO-1002 not short (nothing has left yet)", r2["short"] is False)
    check("SO-1002 fulfilment 0.0%", r2["fulfil_pct"] == 0.0)

    print("\n[8] divide-by-zero + cancelled are excluded")
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1002'").fetchone()["id"]
    conn.execute("UPDATE ord_orders SET qty=0 WHERE id=?", (oid,))
    conn.commit()
    z = [r for r in svc.reconciliation() if r["order_no"] == "SO-1002"][0]
    check("qty 0 -> fulfil_pct is None, not a crash", z["fulfil_pct"] is None)
    check("qty 0 -> no flags", z["over"] is False and z["short"] is False)
    conn.execute("UPDATE ord_orders SET qty=6500 WHERE id=?", (oid,))
    conn.commit()          # release the write lock before the services open their own conns
    sh2 = conn.execute("SELECT id FROM shp_shipments WHERE order_id=?", (oid,)).fetchone()["id"]
    svc.update_shipment(sh2, {"status": "cancelled"}, None)
    check("cancelled shipment drops out of reconciliation",
          all(r["order_no"] != "SO-1002" for r in svc.reconciliation()))
    svc.update_shipment(sh2, {"status": "planned"}, None)

    print("\n[9] bell sweep is idempotent (double-apply)")
    def bells():
        return conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='shipping'").fetchone()["c"]
    svc.recon_sweep()
    first = bells()
    check("sweep raised one alert per flagged order (2)", first == 2)
    svc.recon_sweep()
    svc.recon_sweep()
    check("re-running the sweep raises nothing new", bells() == first)
    check("alert ledger holds 2 rows",
          conn.execute("SELECT COUNT(*) AS c FROM shp_recon_alerts").fetchone()["c"] == 2)

    print("\n[10] dashboard + optional warehouse hook")
    d = svc.dashboard()
    check("dashboard cartons = 190+231+125 = 546", d["cartons"] == 546)
    check("dashboard counts one short order", d["orders_short"] == 1)
    check("dashboard counts one over order", d["orders_over"] == 1)
    check("dashboard CBM is a rounded number", isinstance(d["cbm"], float))
    check("upcoming ETD list is a list", isinstance(d["upcoming"], list))
    fg = svc.fg_onhand(1)
    check("fg hook returns a number or None (degrades cleanly)", fg is None or isinstance(fg, float))
    check("fg hook with no order id -> None", svc.fg_onhand(None) is None)

    print("\n[11] create_and_seed is re-runnable forever")
    before = conn.execute("SELECT COUNT(*) AS c FROM shp_shipments").fetchone()["c"]
    create_and_seed(conn)
    create_and_seed(conn)
    after = conn.execute("SELECT COUNT(*) AS c FROM shp_shipments").fetchone()["c"]
    check("re-seeding neither duplicates nor destroys", before == after)

    conn.close()

print("\n%d checks, %d failures" % (RUN[0], len(FAILS)))
if FAILS:
    print("FAILED: " + "; ".join(FAILS))
    sys.exit(1)
print("ALL CHECKS PASSED")
