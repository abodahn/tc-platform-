"""
Isolated self-test for the traceability module. Runs the real schema + services
against a throwaway SQLite DB (never the repo's platform.db).

    python app/trace/tests_selftest.py
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="trc_"))
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
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    assert cond, "FAIL: " + label
    print("  ok  " + label)


with app.app_context():
    from app.db import get_db
    from app.trace.schema import create_and_seed
    from app.trace import services as svc
    from app.trace.constants import DPP_TOTAL, MAX_CHAIN_DEPTH

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    print("\n[1] schema + seed")
    create_and_seed(conn)          # idempotent: a second boot must not duplicate
    conn.commit()
    n_p = conn.execute("SELECT COUNT(*) AS c FROM trc_partners").fetchone()["c"]
    n_l = conn.execute("SELECT COUNT(*) AS c FROM trc_lots").fetchone()["c"]
    n_c = conn.execute("SELECT COUNT(*) AS c FROM trc_certs").fetchone()["c"]
    ok(n_p == 8, f"partners seeded once ({n_p})")
    ok(n_l == 7, f"lots seeded once ({n_l})")
    ok(n_c == 7, f"certs seeded once ({n_c})")
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_order_lots").fetchone()["c"] == 3,
       "demo lots linked to the demo orders")

    print("\n[2] ancestry walk")
    fab = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='LOT-FAB-0301'").fetchone()["id"]
    chain = svc.lot_chain(fab)
    ok([c["lot_ref"] for c in chain] ==
       ["LOT-FAB-0301", "LOT-DYE-0201", "LOT-YRN-0101", "LOT-FIB-0001"],
       "fabric walks up to the fibre farm in order")
    ok([c["depth"] for c in chain] == [0, 1, 2, 3], "depth is 0-based and increments")
    ok(svc._deepest_tier(chain) == 4, "deepest tier reached is 4 (fibre farm)")
    denim = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='LOT-FAB-0601'").fetchone()["id"]
    ok(len(svc.lot_chain(denim)) == 1, "a lot with no parent returns just itself")
    ok(svc.lot_chain(999999) == [], "unknown lot id returns an empty chain")
    ok(svc.lot_chain(None) == [], "None lot id returns an empty chain")

    print("\n[3] cycle safety")
    # Force a 3-node cycle: FIB -> FAB (which already reaches FIB) = A->B->C->A.
    conn.execute("UPDATE trc_lots SET parent_lot_id=? WHERE lot_ref='LOT-FIB-0001'", (fab,))
    conn.commit()
    cyc, truncated = svc._chain(conn, fab)
    ok(len(cyc) == 4, f"cyclic chain stops after visiting each node once ({len(cyc)})")
    ok(truncated is True, "cycle is reported as a truncated walk")
    ok(len({c['id'] for c in cyc}) == len(cyc), "no node is visited twice")
    # self-parent = 1-node cycle
    conn.execute("UPDATE trc_lots SET parent_lot_id=id WHERE lot_ref='LOT-FAB-0601'")
    conn.commit()
    self_cyc, self_trunc = svc._chain(conn, denim)
    ok(len(self_cyc) == 1 and self_trunc is True, "a lot that is its own parent stops at 1 node")
    conn.execute("UPDATE trc_lots SET parent_lot_id=NULL WHERE lot_ref='LOT-FIB-0001'")
    conn.execute("UPDATE trc_lots SET parent_lot_id=NULL WHERE lot_ref='LOT-FAB-0601'")
    conn.commit()
    # dangling parent: points at a lot that does not exist -> chain just ends
    conn.execute("UPDATE trc_lots SET parent_lot_id=888888 WHERE lot_ref='LOT-FAB-0601'")
    conn.commit()
    dang, dang_trunc = svc._chain(conn, denim)
    ok(len(dang) == 1 and dang_trunc is False, "dangling parent ends the chain without a warning")
    conn.execute("UPDATE trc_lots SET parent_lot_id=NULL WHERE lot_ref='LOT-FAB-0601'")
    conn.commit()
    # depth cap: build a chain longer than MAX_CHAIN_DEPTH
    prev = None
    for i in range(MAX_CHAIN_DEPTH + 5):
        prev = svc.create_lot({"material": f"deep {i}", "parent_lot_id": prev,
                               "fibre_composition": "x"}, None)
    deep, deep_trunc = svc._chain(conn, prev)
    ok(len(deep) == MAX_CHAIN_DEPTH and deep_trunc is True,
       f"walk is capped at MAX_CHAIN_DEPTH ({len(deep)})")

    print("\n[4] certificate status is derived from the dates")
    from datetime import date, timedelta
    ok(svc.cert_status({"valid_until": str(date.today() + timedelta(days=400))}) == "valid", "far future = valid")
    ok(svc.cert_status({"valid_until": str(date.today() + timedelta(days=10))}) == "expiring", "10 days out = expiring")
    ok(svc.cert_status({"valid_until": str(date.today() - timedelta(days=1))}) == "expired", "yesterday = expired")
    ok(svc.cert_status({"valid_until": None}) == "unknown", "no date = unknown")
    ok(svc.cert_status({"valid_until": "not-a-date"}) == "unknown", "garbage date = unknown, no crash")
    ok(svc.cert_status({"valid_until": str(date.today() + timedelta(days=400)),
                        "status": "revoked"}) == "revoked", "revoked beats the dates")
    ok(svc.days_left("") is None and svc.days_left(None) is None, "days_left tolerates empty/None")

    print("\n[5] ESG intensities (record and divide — nothing more)")
    tot = svc._esg_totals([{"energy_kwh": 100, "water_m3": 2, "waste_kg": 5, "garments": 1000}])
    ok(tot["energy_per_pc"] == 0.1, "kWh/garment = 100/1000")
    ok(tot["water_l_per_pc"] == 2.0, "water L/garment = 2 m3 * 1000 / 1000")
    ok(tot["waste_g_per_pc"] == 5.0, "waste g/garment = 5 kg * 1000 / 1000")
    two = svc._esg_totals([{"energy_kwh": 60, "water_m3": 1, "waste_kg": 1, "garments": 1000},
                           {"energy_kwh": 40, "water_m3": 1, "waste_kg": 4, "garments": 1000}])
    ok(two["garments"] == 1000 and two["energy_per_pc"] == 0.1,
       "two records for the same order sum consumption but do NOT sum the pieces")
    zero = svc._esg_totals([{"energy_kwh": 100, "garments": 0}])
    ok(zero["energy_per_pc"] is None, "zero garments -> None, not a division by zero")
    ok(svc._esg_totals([])["water_l_per_pc"] is None, "no records -> None intensities")
    ok(svc._esg_totals([{"energy_kwh": None, "water_m3": None, "waste_kg": None,
                         "garments": None}])["waste_g_per_pc"] is None, "all-None row survives")
    ok(svc._f("") == 0.0 and svc._f("abc") == 0.0 and svc._f(None) == 0.0 and svc._f("7.5") == 7.5,
       "_f coerces blank/garbage/None to 0 and parses numbers")

    print("\n[6] passport assembly + completeness")
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1001'").fetchone()["id"]
    p = svc.passport(oid)
    ok(p is not None, "SO-1001 has a passport")
    ok(p["deepest_tier"] == 4, "SO-1001 traces to tier 4")
    ok(len(p["nodes"]) == 4, "4 chain nodes assembled")
    ok(p["composition"] == ["100% Organic Cotton"], "fibre composition de-duplicated")
    ok(p["completeness"]["total"] == DPP_TOTAL, "all DPP points scored")
    ok(p["completeness"]["pct"] == 100, f"SO-1001 is fully documented ({p['completeness']['pct']}%)")
    ok(p["completeness"]["missing"] == [], "nothing missing on SO-1001")

    oid3 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1003'").fetchone()["id"]
    p3 = svc.passport(oid3)
    ok(p3["completeness"]["pct"] < 70, f"denim order is materially incomplete ({p3['completeness']['pct']}%)")
    miss3 = p3["completeness"]["missing"]
    ok("trc.dpp.tier4" in miss3 and "trc.dpp.tier3" in miss3, "denim chain misses tier 3 and 4")
    ok("trc.dpp.care" in miss3 and "trc.dpp.origin" in miss3, "denim has no declaration yet")
    ok(svc.passport(999999) is None, "unknown order id -> None (route 404s)")

    print("\n[7] passport writes are safe to repeat")
    svc.save_passport(oid3, {"country_of_origin": "Egypt", "care_instructions": "Wash 30",
                             "recycling_info": "Take back", "recycled_content_pct": ""}, None)
    svc.save_passport(oid3, {"country_of_origin": "Egypt", "care_instructions": "Wash 30",
                             "recycling_info": "Take back", "recycled_content_pct": "abc"}, None)
    rows = conn.execute("SELECT COUNT(*) AS c FROM trc_passports WHERE order_id=?", (oid3,)).fetchone()["c"]
    ok(rows == 1, "save_passport upserts — a second save does not create a second header")
    ok(conn.execute("SELECT recycled_content_pct FROM trc_passports WHERE order_id=?",
                    (oid3,)).fetchone()["recycled_content_pct"] == 0.0,
       "non-numeric recycled % becomes 0, not a 500")
    p3b = svc.passport(oid3)
    ok(p3b["completeness"]["pct"] > p3["completeness"]["pct"], "declaring data raises completeness")

    lot_id = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='LOT-FAB-0301'").fetchone()["id"]
    ok(svc.link_order_lot(oid3, lot_id, 100) is True, "first link succeeds")
    ok(svc.link_order_lot(oid3, lot_id, 100) is False, "double-apply is refused, not duplicated")
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_order_lots WHERE order_id=? AND lot_id=?",
                    (oid3, lot_id)).fetchone()["c"] == 1, "exactly one link row")
    ok(svc.link_order_lot(oid3, None, 1) is False, "linking a missing lot id is refused")
    link = conn.execute("SELECT id FROM trc_order_lots WHERE order_id=? AND lot_id=?",
                        (oid3, lot_id)).fetchone()["id"]
    svc.unlink_order_lot(oid3, link)
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_lots WHERE id=?", (lot_id,)).fetchone()["c"] == 1,
       "unlinking removes the link row and never the lot itself")

    print("\n[8] creates tolerate junk input")
    pid = svc.create_partner({"name": "Junk Tier Ltd", "tier": "99"}, None)
    ok(conn.execute("SELECT tier FROM trc_partners WHERE id=?", (pid,)).fetchone()["tier"] == 4,
       "tier 99 clamps to 4")
    pid2 = svc.create_partner({"name": "Blank Tier Ltd", "tier": ""}, None)
    ok(conn.execute("SELECT tier FROM trc_partners WHERE id=?", (pid2,)).fetchone()["tier"] == 2,
       "blank tier defaults to 2")
    ok(svc.create_lot({"material": "Scrap", "parent_lot_id": "x"}, None) is None,
       "a lot naming a parent that does not exist is refused, not silently orphaned")
    lid = svc.create_lot({"material": "Scrap", "qty": "not a number", "partner_id": ""}, None)
    row = conn.execute("SELECT * FROM trc_lots WHERE id=?", (lid,)).fetchone()
    ok(row["qty"] == 0.0 and row["partner_id"] is None and row["parent_lot_id"] is None,
       "garbage qty and a blank partner become 0/NULL")
    ok(row["lot_ref"] == "LOT-%05d" % lid, "blank lot_ref is numbered from the row id")
    svc.record_esg({"order_id": "", "energy_kwh": "", "garments": "abc", "label": "junk"}, None)
    ok(svc.list_esg()[0]["energy_per_pc"] is None, "an all-junk ESG record still lists safely")

    print("\n[9] sweep: bell alerts, once each")
    before = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace'").fetchone()["c"]
    svc.sweep()
    after1 = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace'").fetchone()["c"]
    ok(after1 > before, f"sweep raised {after1 - before} alert(s)")
    kinds = [r["title"] for r in conn.execute(
        "SELECT title FROM notifications WHERE module='trace'").fetchall()]
    ok(any("expired" in k for k in kinds), "the expired RCS certificate alerted")
    ok(any("expiring" in k for k in kinds), "the ZDHC certificate expiring in 25 days alerted")
    ok(any("passport incomplete" in k.lower() for k in kinds),
       "an order shipping soon with a weak passport alerted")
    svc.sweep()
    after2 = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace'").fetchone()["c"]
    ok(after2 == after1, "a second sweep is idempotent — no duplicate alerts")
    cid = conn.execute("SELECT id FROM trc_certs WHERE standard='RCS'").fetchone()["id"]
    svc.renew_cert(cid, str(date.today() + timedelta(days=365)), None)
    ok(conn.execute("SELECT expiry_alerted FROM trc_certs WHERE id=?", (cid,)).fetchone()["expiry_alerted"] == 0,
       "renewing re-arms the expiry alarm")

    print("\n[10] dashboard")
    d = svc.dashboard()
    ok(d["partners_total"] >= 8 and d["lots_total"] >= 7, "counts are populated")
    ok(0 <= d["avg_completeness"] <= 100, f"average completeness in range ({d['avg_completeness']}%)")
    ok(d["by_tier"].get(4, 0) >= 1, "at least one tier-4 partner counted")
    ok(isinstance(d["watch"], list), "expiry watchlist built")

    conn.close()

print("\n[11] templates parse")
import jinja2                                    # noqa: E402
env = jinja2.Environment(loader=jinja2.FileSystemLoader(
    r"D:\TC platform\tc-platform-render\app\templates"))
import glob                                      # noqa: E402
for f in sorted(glob.glob(r"D:\TC platform\tc-platform-render\app\templates\trace\*.html")):
    env.parse(open(f, encoding="utf-8").read())
    CHECKS[0] += 1
    print("  ok  parsed " + os.path.basename(f))

print(f"\nALL {CHECKS[0]} CHECKS PASSED")
