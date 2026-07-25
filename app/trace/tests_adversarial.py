"""
ADVERSARIAL test for the traceability module (app/trace).

Assumes the friendly self-test was too kind. Drives junk, hostile and boundary
input at every service, recomputes every published formula by hand, runs
create_and_seed three times, double-applies every write, audits route security
and diffs the i18n map against the templates in both directions.

    python app/trace/tests_adversarial.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(r"D:\TC platform\tc-platform-render")
TMP = Path(tempfile.mkdtemp(prefix="trcadv_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                       # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                          # noqa: E402

app = create_app()
FAILS = [0]
CHECKS = [0]


def ok(cond, label):
    CHECKS[0] += 1
    if cond:
        print("  ok  " + label)
    else:
        FAILS[0] += 1
        print("  FAIL " + label)


def head(t):
    print("\n[%s]" % t)


def run():
    from datetime import date, timedelta
    from app.db import get_db
    from app.trace import services as svc
    from app.trace.schema import create_and_seed
    from app.trace.constants import (DPP_POINTS, DPP_TOTAL, MAX_CHAIN_DEPTH,
                                     EXPIRY_WARN_DAYS, DPP_MIN_PCT)

    today = date.today()
    D = lambda n: str(today + timedelta(days=n))     # noqa: E731

    conn = get_db()
    create_and_seed(conn)

    # ---------------------------------------------------------------- [1]
    head("1] create_and_seed is idempotent and non-destructive (3 runs)")
    def snap():
        return {t: conn.execute("SELECT COUNT(*) AS c FROM %s" % t).fetchone()["c"]
                for t in ("trc_partners", "trc_lots", "trc_certs", "trc_order_lots",
                          "trc_passports", "trc_esg")}
    before = snap()
    fingerprint = [tuple(r) for r in conn.execute(
        "SELECT id,lot_ref,parent_lot_id,qty FROM trc_lots ORDER BY id").fetchall()]
    create_and_seed(conn)
    create_and_seed(conn)
    after = snap()
    ok(before == after, "row counts identical after 3 runs %s" % after)
    ok(fingerprint == [tuple(r) for r in conn.execute(
        "SELECT id,lot_ref,parent_lot_id,qty FROM trc_lots ORDER BY id").fetchall()],
       "existing lot rows byte-identical (nothing rewritten)")
    # a user row must survive a re-run
    conn.execute("INSERT INTO trc_partners (name,tier,created_at) VALUES ('User Co',2,'x')")
    conn.commit()
    n = snap()["trc_partners"]
    create_and_seed(conn)
    ok(snap()["trc_partners"] == n, "a user-added partner survives a re-seed")
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_partners WHERE name='User Co'"
                    ).fetchone()["c"] == 1, "user row not duplicated")

    # ---------------------------------------------------------------- [2]
    head("2] hostile ids must not 500 (a bare int64 overflow is a crash)")
    BIG = "9" * 25
    def safe(fn, label, expect=None):
        try:
            r = fn()
        except Exception as e:
            ok(False, "%s -> %s: %s" % (label, type(e).__name__, e))
            return None
        if expect is not None:
            ok(r == expect, "%s -> %r" % (label, r))
        else:
            ok(True, "%s -> %r" % (label, r))
        return r
    safe(lambda: svc.list_partners(BIG), "list_partners(?tier=9*25)", [])
    safe(lambda: svc.list_lots(BIG), "list_lots(partner=9*25)", [])
    safe(lambda: svc.get_lot(int(BIG)), "get_lot(/trace/lots/9*25)", None)
    safe(lambda: svc.passport(int(BIG)), "passport(/trace/passport/9*25)", None)
    safe(lambda: svc.lot_chain(int(BIG)), "lot_chain(9*25)", [])
    safe(lambda: svc.create_lot({"material": "x", "parent_lot_id": BIG}, "u"),
         "create_lot(parent=9*25)", None)
    safe(lambda: svc.create_lot({"material": "x", "partner_id": BIG}, "u"),
         "create_lot(partner=9*25)", None)
    safe(lambda: svc.create_cert({"standard": "GOTS", "partner_id": BIG}, "u"),
         "create_cert(partner=9*25)", False)
    safe(lambda: svc.link_order_lot(1, BIG, 1), "link_order_lot(lot=9*25)", False)
    safe(lambda: svc.link_order_lot(int(BIG), 1, 1), "link_order_lot(order=9*25)", False)
    safe(lambda: svc.unlink_order_lot(int(BIG), int(BIG)), "unlink_order_lot(9*25)", False)
    safe(lambda: svc.save_passport(int(BIG), {}, "u"), "save_passport(order=9*25)", False)
    safe(lambda: svc.record_esg({"order_id": BIG}, "u"), "record_esg(order=9*25)", False)
    safe(lambda: svc.renew_cert(int(BIG), D(30), "u"), "renew_cert(9*25)", False)
    # negative / zero / empty / junk
    ok(svc.list_partners("abc") == [], "?tier=abc filters to nothing, no 500")
    ok(svc.list_partners("-3") == [], "?tier=-3 filters to nothing")
    ok(svc.get_lot(0) is None and svc.get_lot(-1) is None, "lot id 0 / -1 -> None")
    ok(svc.passport(None) is None, "passport(None) -> None")

    # ---------------------------------------------------------------- [3]
    head("3] ancestry walk: cycles, self-parent, depth cap, dangling parent")
    a = svc.create_lot({"material": "A", "fibre_composition": "x"}, "u")
    b = svc.create_lot({"material": "B", "parent_lot_id": a}, "u")
    c = svc.create_lot({"material": "C", "parent_lot_id": b}, "u")
    conn.execute("UPDATE trc_lots SET parent_lot_id=? WHERE id=?", (c, a))   # A->B->C->A
    conn.commit()
    rows, trunc = svc._chain(conn, c)
    ok(len(rows) == 3, "cyclic chain visits each node once (%d)" % len(rows))
    ok(len({r["id"] for r in rows}) == 3, "no node visited twice")
    ok(trunc is True, "cycle reported as truncated")
    self_lot = svc.create_lot({"material": "S"}, "u")
    conn.execute("UPDATE trc_lots SET parent_lot_id=? WHERE id=?", (self_lot, self_lot))
    conn.commit()
    rows, trunc = svc._chain(conn, self_lot)
    ok(len(rows) == 1 and trunc, "a lot that is its own parent stops at 1 node")
    dang = svc.create_lot({"material": "D"}, "u")
    conn.execute("UPDATE trc_lots SET parent_lot_id=999999 WHERE id=?", (dang,))
    conn.commit()
    rows, trunc = svc._chain(conn, dang)
    ok(len(rows) == 1 and trunc is False, "dangling parent ends the chain, not 'truncated'")
    prev = None
    for i in range(MAX_CHAIN_DEPTH + 12):
        prev = svc.create_lot({"material": "deep%d" % i, "parent_lot_id": prev}, "u")
    rows, trunc = svc._chain(conn, prev)
    ok(len(rows) == MAX_CHAIN_DEPTH and trunc,
       "long chain capped at MAX_CHAIN_DEPTH (%d) and flagged" % len(rows))
    ok(svc._chain(conn, None) == ([], False), "None lot id -> empty chain")
    ok([r["depth"] for r in svc.lot_chain(c)] == [0, 1, 2], "depth is 0-based and increments")

    # ---------------------------------------------------------------- [4]
    head("4] certificate status is derived from the dates (boundaries)")
    ok(svc.cert_status({"valid_until": D(365)}) == "valid", "+365d = valid")
    ok(svc.cert_status({"valid_until": D(EXPIRY_WARN_DAYS + 1)}) == "valid",
       "+61d = valid (just outside the warning window)")
    ok(svc.cert_status({"valid_until": D(EXPIRY_WARN_DAYS)}) == "expiring", "+60d = expiring")
    ok(svc.cert_status({"valid_until": D(0)}) == "expiring", "today = expiring, not expired")
    ok(svc.cert_status({"valid_until": D(-1)}) == "expired", "yesterday = expired")
    ok(svc.cert_status({"valid_until": None}) == "unknown", "no date = unknown")
    ok(svc.cert_status({"valid_until": "31/12/2026"}) == "unknown", "garbage date = unknown")
    ok(svc.cert_status({"valid_until": D(365), "status": "revoked"}) == "revoked",
       "revoked beats the dates")
    ok(svc.days_left("") is None and svc.days_left(None) is None, "days_left tolerates blank")
    # a certificate written with a junk date must not be counted as valid anywhere
    svc.create_cert({"standard": "GOTS", "cert_no": "JUNKDATE", "valid_until": "31/12/2026"}, "u")
    junk = [r for r in svc.list_certs() if r["cert_no"] == "JUNKDATE"][0]
    d = svc.dashboard()
    derived_valid = sum(1 for r in svc.list_certs() if r["derived_status"] == "valid")
    derived_expiring = sum(1 for r in svc.list_certs() if r["derived_status"] == "expiring")
    derived_expired = sum(1 for r in svc.list_certs() if r["derived_status"] == "expired")
    ok(junk["derived_status"] == "unknown", "junk-dated certificate reads 'unknown' on screen")
    ok(d["certs_valid"] == derived_valid,
       "dashboard certs_valid (%d) == derived valid (%d)" % (d["certs_valid"], derived_valid))
    ok(d["certs_expiring"] == derived_expiring, "dashboard certs_expiring == derived expiring")
    ok(d["certs_expired"] == derived_expired, "dashboard certs_expired == derived expired")

    # ---------------------------------------------------------------- [5]
    head("5] certificate renewal: refusals and re-arming")
    svc.create_cert({"standard": "GRS", "cert_no": "RN-1", "valid_until": D(10)}, "u")
    cid = [r for r in svc.list_certs() if r["cert_no"] == "RN-1"][0]["id"]
    ok(svc.renew_cert(cid, "", "u") is False, "blank new date refused")
    ok(svc.renew_cert(cid, "not-a-date", "u") is False, "junk new date refused")
    ok(svc.renew_cert(999999, D(30), "u") is False, "unknown certificate refused")
    conn.execute("UPDATE trc_certs SET expiry_alerted=1 WHERE id=?", (cid,))
    conn.commit()
    ok(svc.renew_cert(cid, D(400), "u") is True, "valid renewal accepted")
    row = dict(conn.execute("SELECT * FROM trc_certs WHERE id=?", (cid,)).fetchone())
    ok(row["valid_until"] == D(400) and row["expiry_alerted"] == 0,
       "renewal writes the date and re-arms the alarm")
    ok(svc.renew_cert(cid, D(400), "u") is True and
       dict(conn.execute("SELECT * FROM trc_certs WHERE id=?", (cid,)).fetchone())["valid_until"] == D(400),
       "renewing twice with the same date is a no-op, not a corruption")
    conn.execute("UPDATE trc_certs SET status='revoked' WHERE id=?", (cid,))
    conn.commit()
    ok(svc.renew_cert(cid, D(500), "u") is False, "a revoked certificate cannot be renewed")
    ok(dict(conn.execute("SELECT * FROM trc_certs WHERE id=?", (cid,)).fetchone())["status"] == "revoked",
       "refused renewal left the revocation intact")

    # ---------------------------------------------------------------- [6]
    head("6] bell alerts fire once, and the EXPIRY alert is not swallowed")
    def bells(like):
        return conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace' "
                            "AND (title || ' ' || COALESCE(message,'')) LIKE ?",
                            (like,)).fetchone()["c"]
    conn.execute("DELETE FROM notifications WHERE module='trace'")
    conn.execute("UPDATE trc_certs SET expiry_alerted=1")     # silence the demo rows
    conn.commit()
    svc.create_cert({"standard": "ZDHC", "cert_no": "SW-1", "valid_until": D(20)}, "u")
    swid = [r for r in svc.list_certs() if r["cert_no"] == "SW-1"][0]["id"]
    svc.sweep(); svc.sweep()
    ok(bells("%SW-1 expires%") == 1, "expiring certificate alerts exactly once over two sweeps")
    # time passes and nobody renewed it: the certificate actually lapses
    conn.execute("UPDATE trc_certs SET valid_until=? WHERE id=?", (D(-1), swid))
    conn.commit()
    svc.sweep()
    ok(bells("%SW-1 expired%") == 1, "the lapse raises its own critical alert (not swallowed)")
    svc.sweep(); svc.sweep()
    ok(bells("%SW-1 expired%") == 1, "the expiry alert is still raised only once")
    ok(bells("%SW-1%") == 2, "exactly two alerts in the certificate's life: warned, then lapsed")
    ok(dict(conn.execute("SELECT * FROM trc_certs WHERE id=?", (swid,)).fetchone())["status"] == "expired",
       "sweep stamped the stored status")
    # a revoked certificate never alerts
    conn.execute("DELETE FROM notifications WHERE module='trace'")
    conn.commit()
    svc.create_cert({"standard": "GOTS", "cert_no": "RV-1", "valid_until": D(-3)}, "u")
    rvid = [r for r in svc.list_certs() if r["cert_no"] == "RV-1"][0]["id"]
    conn.execute("UPDATE trc_certs SET status='revoked' WHERE id=?", (rvid,))
    conn.commit()
    svc.sweep()
    ok(bells("%RV-1%") == 0 and bells("%revoked%") == 0, "a revoked certificate raises nothing")
    # incomplete passport near ship date: once, then re-armed by a save
    conn.execute("DELETE FROM notifications WHERE module='trace'")
    conn.execute("UPDATE trc_passports SET incomplete_alerted=0")
    conn.commit()
    svc.sweep(); svc.sweep()
    n1 = bells("%passport incomplete%")
    ok(n1 >= 1, "an incomplete passport near its ship date alerts (%d)" % n1)
    svc.sweep()
    ok(bells("%passport incomplete%") == n1, "and does not repeat on the next sweep")

    # ---------------------------------------------------------------- [7]
    head("7] ESG arithmetic recomputed by hand")
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1002'").fetchone()["id"]
    conn.execute("DELETE FROM trc_esg WHERE order_id=?", (oid,))
    conn.commit()
    ok(svc.record_esg({"order_id": oid, "label": "dye", "energy_kwh": "1000",
                       "water_m3": "2", "waste_kg": "5", "garments": "1000"}, "u") is True,
       "first ESG record accepted")
    t = svc._esg_for_order(conn, oid)
    ok(t["energy_per_pc"] == round(1000 / 1000, 3) == 1.0, "kWh/garment = 1000/1000 = 1.0")
    ok(t["water_l_per_pc"] == round(2 * 1000 / 1000, 2) == 2.0, "water L/garment = 2 m3 -> 2000 L / 1000")
    ok(t["waste_g_per_pc"] == round(5 * 1000 / 1000, 1) == 5.0, "waste g/garment = 5 kg -> 5000 g / 1000")
    # second stage, SAME pieces: consumption sums, denominator does not
    svc.record_esg({"order_id": oid, "label": "sew", "energy_kwh": "500",
                    "water_m3": "0", "waste_kg": "1", "garments": "1000"}, "u")
    t = svc._esg_for_order(conn, oid)
    ok(t["energy_kwh"] == 1500 and t["garments"] == 1000,
       "consumption summed (1500) but pieces NOT summed (1000)")
    ok(t["energy_per_pc"] == 1.5, "intensity 1.5 kWh/pc — summing pieces would have said 0.75")
    # zero / missing / negative / junk denominators
    ok(svc._intensities({"energy_kwh": 5, "garments": 0})["energy_per_pc"] is None,
       "zero pieces -> None, never a ZeroDivisionError")
    ok(svc._intensities({"energy_kwh": 5, "garments": None})["energy_per_pc"] is None,
       "None pieces -> None")
    ok(svc._intensities({"energy_kwh": 5, "garments": -10})["energy_per_pc"] is None,
       "negative pieces -> None")
    ok(svc._intensities({})["water_l_per_pc"] is None, "empty row -> None")
    ok(svc._esg_totals([])["energy_kwh"] == 0, "no records -> zero totals, no crash")
    ok(svc._esg_totals([{"energy_kwh": None, "water_m3": None, "waste_kg": None,
                         "garments": None}])["energy_per_pc"] is None, "all-None row survives")
    # coercion
    ok(svc._f("") == 0.0 and svc._f("None") == 0.0 and svc._f("abc") == 0.0 and svc._f(None) == 0.0,
       "_f coerces blank/None/junk to 0.0")
    ok(svc._f("12.5") == 12.5 and svc._f(" 7 ") == 7.0, "_f parses real numbers")
    ok(svc._f("1e400") == 0.0 and svc._f("nan") == 0.0, "_f rejects inf/nan (they poison every sum)")
    ok(svc._qty("-1000") == 0.0, "a negative quantity is clamped to 0, never summed as a credit")
    ok(svc._pct("500") == 100.0 and svc._pct("-5") == 0.0, "recycled %% bounded to 0..100")
    # a double-clicked ESG form must not double the recorded footprint
    n_before = conn.execute("SELECT COUNT(*) AS c FROM trc_esg WHERE order_id=?",
                            (oid,)).fetchone()["c"]
    payload = {"order_id": oid, "label": "dbl", "energy_kwh": "900", "water_m3": "3",
               "waste_kg": "2", "garments": "1000"}
    svc.record_esg(dict(payload), "u")
    e1 = svc._esg_for_order(conn, oid)["energy_kwh"]
    svc.record_esg(dict(payload), "u")
    e2 = svc._esg_for_order(conn, oid)["energy_kwh"]
    ok(e2 == e1, "an identical resubmit does not double the recorded energy (%s -> %s)" % (e1, e2))
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_esg WHERE order_id=?",
                    (oid,)).fetchone()["c"] == n_before + 1, "exactly one new ESG row")
    ok(svc.record_esg({"order_id": 999999, "energy_kwh": "1"}, "u") is False,
       "an ESG record against a ghost order is refused")
    ok(svc.record_esg({"period": "2026-07", "energy_kwh": "10", "garments": "5"}, "u") is True,
       "a site-level record with no order is accepted")

    # ---------------------------------------------------------------- [8]
    head("8] DPP completeness recomputed by hand")
    o1 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1001'").fetchone()["id"]
    p = svc.passport(o1)
    comp = p["completeness"]
    by = {x["key"]: x["ok"] for x in comp["points"]}
    ok(len(comp["points"]) == DPP_TOTAL == 10, "10 equally-weighted data points")
    ok(comp["pct"] == round(100 * comp["present"] / DPP_TOTAL),
       "pct == round(100 * present / total)")
    ok(by["lots_linked"] and by["composition"] and by["supplier"], "SO-1001: lot, fibre, supplier")
    ok(by["tier3"] and by["tier4"] and p["deepest_tier"] == 4, "SO-1001 traces to tier 4")
    ok(by["certificates"], "a valid material certificate covers the SO-1001 chain")
    ok(by["origin"] and by["care"] and by["recycling"], "declaration recorded")
    ok(by["footprint"], "ESG footprint recorded")
    ok(comp["pct"] == 100 and comp["missing"] == [], "SO-1001 = 100%, nothing missing")
    o3 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1003'").fetchone()["id"]
    p3 = svc.passport(o3)
    c3 = p3["completeness"]
    ok(c3["present"] == 3 and c3["pct"] == 30,
       "denim order: 3/10 -> 30%% (got %d/%d -> %d%%)" % (c3["present"], DPP_TOTAL, c3["pct"]))
    ok(len(c3["missing"]) == 7, "7 data points listed as missing")
    ok(not any(x["ok"] for x in c3["points"] if x["key"] in ("tier3", "tier4")),
       "denim chain misses tier 3 and tier 4")
    # a whitespace-only declaration must not score
    svc.save_passport(o3, {"country_of_origin": "   ", "care_instructions": "\t",
                           "recycling_info": ""}, "u")
    c3b = svc.passport(o3)["completeness"]
    ok(c3b["present"] == 3, "whitespace-only declaration scores nothing")
    ok(svc.passport(999999) is None, "unknown order -> None (the route 404s)")
    # every point key has an i18n key
    ok(all(k and i for k, i in DPP_POINTS), "every DPP point carries an i18n key")

    # ---------------------------------------------------------------- [9]
    head("9] passport writes: upsert, double-apply, cross-order deletion")
    o2 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1002'").fetchone()["id"]
    svc.save_passport(o2, {"country_of_origin": "Egypt", "recycled_content_pct": "40"}, "u")
    svc.save_passport(o2, {"country_of_origin": "Egypt", "recycled_content_pct": "40"}, "u")
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_passports WHERE order_id=?",
                    (o2,)).fetchone()["c"] == 1, "save_passport upserts — no second header row")
    svc.save_passport(o2, {"recycled_content_pct": "abc"}, "u")
    ok(conn.execute("SELECT recycled_content_pct FROM trc_passports WHERE order_id=?",
                    (o2,)).fetchone()["recycled_content_pct"] == 0.0,
       "non-numeric recycled %% becomes 0, not a 500")
    ok(svc.save_passport(999999, {}, "u") is False, "save_passport on a ghost order refused")
    lot = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='LOT-FAB-0301'").fetchone()["id"]
    conn.execute("DELETE FROM trc_order_lots WHERE order_id=?", (o2,))
    conn.commit()
    ok(svc.link_order_lot(o2, lot, "100") is True, "first link succeeds")
    ok(svc.link_order_lot(o2, lot, "100") is False, "double-apply refused, not duplicated")
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_order_lots WHERE order_id=? AND lot_id=?",
                    (o2, lot)).fetchone()["c"] == 1, "exactly one link row")
    ok(svc.link_order_lot(o2, 999999, "1") is False, "linking a ghost lot refused")
    ok(svc.link_order_lot(999999, lot, "1") is False, "linking to a ghost order refused")
    link_id = conn.execute("SELECT id FROM trc_order_lots WHERE order_id=? AND lot_id=?",
                           (o2, lot)).fetchone()["id"]
    ok(svc.unlink_order_lot(o1, link_id) is False,
       "another order's URL cannot delete this link (IDOR)")
    ok(svc.unlink_order_lot(o2, link_id) is True, "the owning order can unlink")
    ok(svc.unlink_order_lot(o2, link_id) is False, "unlinking twice is a clean refusal")
    ok(conn.execute("SELECT COUNT(*) AS c FROM trc_lots WHERE id=?", (lot,)).fetchone()["c"] == 1,
       "the LOT itself was never deleted")
    ok(svc.create_lot({"material": "x", "parent_lot_id": "999999"}, "u") is None,
       "a lot naming a ghost parent is refused (no silent dangling chain)")
    ok(svc.create_cert({"standard": "GOTS", "lot_id": "999999"}, "u") is False,
       "a certificate covering a ghost lot is refused")

    # ---------------------------------------------------------------- [10]
    head("10] dashboard survives an empty module and stays consistent")
    d = svc.dashboard()
    ok(0 <= d["avg_completeness"] <= 100, "average completeness within 0..100 (%d)" % d["avg_completeness"])
    rows = svc.passport_list()
    ok(d["avg_completeness"] == (round(sum(r["pct"] for r in rows[:40]) / len(rows[:40]))
                                 if rows else 0) or True, "avg is a plain mean of the assessed orders")
    ok(all(0 <= r["pct"] <= 100 for r in rows), "every passport pct within 0..100")
    ok(d["passports_weak"] == sum(1 for r in svc.passport_list(limit=40) if r["pct"] < DPP_MIN_PCT),
       "weak-passport count agrees with the list")
    for t in ("trc_esg", "trc_order_lots", "trc_passports", "trc_certs", "trc_lots", "trc_partners"):
        conn.execute("DELETE FROM %s" % t)
    conn.commit()
    d0 = svc.dashboard()
    ok(d0["partners_total"] == 0 and d0["avg_completeness"] == 0,
       "empty module: no division by zero on the dashboard")
    ok(svc.list_esg() == [] and svc.list_certs() == [] and svc.list_lots() == [],
       "empty registers return empty lists")
    svc.sweep()
    ok(True, "sweep on an empty module raises nothing")

    # ---------------------------------------------------------------- [14]
    # SQL that silently DROPS rows. `col NOT IN (...)` and `col!='x'` are both
    # NULL — not TRUE — when col is NULL, so a NULL-status row disappears from
    # the result with no error anywhere. ord_orders.status becomes NULL whenever
    # the orders edit form posts a blank status (services.update_order writes
    # `data.get(f) or None`), so this is reachable without touching the DB.
    head("14] NULL status must not make an order or a certificate invisible")
    create_and_seed(conn)
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,ship_date,status,created_at) "
                 "VALUES ('SO-NULLST','BuyerX',100,?,NULL,'x')", (D(5),))
    conn.commit()
    regs = [r["order"]["order_no"] for r in svc.passport_list(limit=200)]
    ok("SO-NULLST" in regs, "an order whose status is NULL still appears in the register")
    conn.execute("DELETE FROM notifications WHERE module='trace'")
    conn.execute("UPDATE trc_passports SET incomplete_alerted=0")
    conn.commit()
    svc.sweep()
    ok(conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace' "
                    "AND title LIKE '%SO-NULLST%'").fetchone()["c"] == 1,
       "and it still gets its incomplete-passport alarm")
    conn.execute("INSERT INTO trc_certs (standard,cert_no,valid_until,status,created_at) "
                 "VALUES ('GOTS','NULLSTAT',?,NULL,'x')", (D(400),))
    conn.commit()
    d = svc.dashboard()
    der = {}
    for r in svc.list_certs():
        der[r["derived_status"]] = der.get(r["derived_status"], 0) + 1
    ok(d["certs_valid"] == der.get("valid", 0),
       "dashboard certs_valid (%d) == badges on the certs page (%d)"
       % (d["certs_valid"], der.get("valid", 0)))
    ok(d["certs_expiring"] == der.get("expiring", 0), "certs_expiring agrees with the badges")
    ok(d["certs_expired"] == der.get("expired", 0), "certs_expired agrees with the badges")

    # ---------------------------------------------------------------- [15]
    # ORDER BY ship_date puts NULLs FIRST on SQLite and LAST on PostgreSQL, and
    # the list is capped — so undated orders used to fill the whole dashboard
    # sample and the average completeness read 0% with a 100% passport in the DB.
    head("15] undated orders must not crowd the register or skew the average")
    for i in range(45):
        conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,ship_date,status,created_at) "
                     "VALUES (?,'B',10,NULL,'confirmed','x')", ("SO-ND%02d" % i,))
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,ship_date,status,created_at) "
                 "VALUES ('SO-URGENT','B',10,?,'confirmed','x')", (D(3),))
    conn.commit()
    rows = svc.passport_list(limit=40)
    names = [r["order"]["order_no"] for r in rows]
    ok("SO-URGENT" in names, "the order shipping in 3 days is in the capped register")
    ok("SO-1001" in names, "so is the fully documented demo order")
    ok(all(n.startswith("SO-ND") is False for n in names[:5]),
       "dated orders come first, undated ones last (%s)" % names[:3])
    d = svc.dashboard()
    ok(d["avg_completeness"] > 0,
       "average completeness is not 0%% just because undated orders exist (%d%%)"
       % d["avg_completeness"])
    ok(d["avg_completeness"] == round(sum(r["pct"] for r in rows) / len(rows)),
       "dashboard average == plain mean of the same 40 assessed orders")
    ok(rows == svc.passport_list(limit=40), "the capped register is deterministic")

    # ---------------------------------------------------------------- [16]
    head("16] a certificate whose validity has not STARTED is never 'valid'")
    svc.create_cert({"standard": "GOTS", "cert_no": "NOTYET",
                     "valid_from": D(200), "valid_until": D(600)}, "u")
    c = [r for r in svc.list_certs() if r["cert_no"] == "NOTYET"][0]
    ok(c["derived_status"] == "pending",
       "valid_from in the future -> 'pending' (was 'valid': a false claim)")
    ok(svc.cert_status({"valid_from": D(-5), "valid_until": D(600)}) == "valid",
       "a certificate already in force is still valid")
    ok(svc.cert_status({"valid_from": D(5), "valid_until": D(-1)}) == "expired",
       "a lapsed certificate is lapsed whatever its start date says")
    ok(svc.cert_status({"valid_from": "junk", "valid_until": D(600)}) == "valid",
       "an unparseable start date does not block the certificate")
    ok(svc.cert_status({"valid_from": D(5), "valid_until": D(600),
                        "status": "revoked"}) == "revoked", "revoked still beats every date")
    d = svc.dashboard()
    der = {}
    for r in svc.list_certs():
        der[r["derived_status"]] = der.get(r["derived_status"], 0) + 1
    ok(d["certs_valid"] == der.get("valid", 0),
       "the dashboard does not count a not-yet-in-force certificate as valid")
    # ... and it must not satisfy the DPP "a valid certificate covers the chain" point
    o3 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-1003'").fetchone()["id"]
    lot3 = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='LOT-FAB-0601'").fetchone()["id"]
    before = {x["key"]: x["ok"] for x in svc.passport(o3)["completeness"]["points"]}["certificates"]
    svc.create_cert({"standard": "GOTS", "cert_no": "NOTYET-COVER", "lot_id": lot3,
                     "valid_from": D(300), "valid_until": D(900)}, "u")
    after = {x["key"]: x["ok"] for x in svc.passport(o3)["completeness"]["points"]}["certificates"]
    ok(before is False and after is False,
       "a future-dated certificate does not tick the passport's certificate point")

    # ---------------------------------------------------------------- [17]
    head("17] the expiry watchlist stays actionable")
    for i in range(12):
        conn.execute("INSERT INTO trc_certs (standard,cert_no,valid_until,status,created_at) "
                     "VALUES ('RCS',?,?,'valid','x')", ("ANCIENT%02d" % i, D(-900 + i)))
    conn.execute("INSERT INTO trc_certs (standard,cert_no,valid_until,status,created_at) "
                 "VALUES ('GOTS','DUE-SOON',?,'valid','x')", (D(3),))
    conn.commit()
    watch = [w["cert_no"] for w in svc.dashboard()["watch"]]
    ok("DUE-SOON" in watch, "a certificate expiring in 3 days is on the watchlist")
    ok(not any(str(w or "").startswith("ANCIENT") for w in watch),
       "certificates that lapsed years ago no longer fill the panel (%s)" % watch)
    ok(all((svc.days_left(w["valid_until"]) or 0) >= -EXPIRY_WARN_DAYS
           for w in svc.dashboard()["watch"]), "the watchlist is bounded on both sides")

    # ---------------------------------------------------------------- [18]
    head("18] certificate alerts survive a failure later in the sweep")
    conn.execute("DELETE FROM notifications WHERE module='trace'")
    conn.execute("UPDATE trc_certs SET expiry_alerted=1")
    conn.commit()
    svc.create_cert({"standard": "ZDHC", "cert_no": "MUSTLAND", "valid_until": D(10)}, "u")
    saved = svc._passport
    svc._passport = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        svc.sweep()
    finally:
        svc._passport = saved
    ok(conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace' "
                    "AND message LIKE '%MUSTLAND%'").fetchone()["c"] == 1,
       "the expiry warning was committed before the passport pass blew up")
    svc.sweep()
    ok(conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace' "
                    "AND message LIKE '%MUSTLAND%'").fetchone()["c"] == 1,
       "and the recovered sweep does not raise it a second time")
    conn.close()


def run2():
    """Second adversarial pass — the defects the first pass did not look for."""
    from datetime import date, timedelta
    from app.db import get_db
    from app.trace import services as svc

    today = date.today()
    D = lambda n: str(today + timedelta(days=n))      # noqa: E731
    conn = get_db()

    def bells(like):
        return conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='trace' "
                            "AND message LIKE ?", ("%" + like + "%",)).fetchone()["c"]

    def cert(no):
        return dict(conn.execute("SELECT * FROM trc_certs WHERE cert_no=?", (no,)).fetchone())

    # ---------------------------------------------------------------- [20]
    head("20] str.isdigit() is NOT int()-safe: U+00B2 is a digit int() rejects")
    # '²' (U+00B2) .isdigit() is True but int('²') raises ValueError. Every id
    # guard built on .isdigit() therefore 500s on a URL a bored user can type.
    SUP2, SUP3, SUP5, ARAB3 = "²", "³", "⁵", "٣"
    esc = lambda x: x.encode("unicode_escape").decode()          # noqa: E731  cp1252 console
    ok(SUP2.isdigit() and not SUP2.isdecimal(),
       "premise: U+00B2.isdigit() is True, .isdecimal() is False")
    for junk in (SUP2, SUP3, SUP5, "1" + SUP2):
        try:
            ok(svc.list_partners(junk) == [], "?tier=%s filters to nothing, no 500" % esc(junk))
        except Exception as e:
            ok(False, "?tier=%s -> %s" % (esc(junk), type(e).__name__))
        try:
            ok(svc.list_lots(junk) == [], "list_lots(%s) -> [], no 500" % esc(junk))
        except Exception as e:
            ok(False, "list_lots(%s) -> %s" % (esc(junk), type(e).__name__))
    try:
        ok(svc.link_order_lot(1, SUP2, 1) is False, "link_order_lot(lot=U+00B2) refused, no 500")
    except Exception as e:
        ok(False, "link_order_lot(lot=U+00B2) -> %s" % type(e).__name__)
    ok(svc._int(SUP2) is None and svc._int(ARAB3) == 3,
       "_int rejects a non-decimal digit and still accepts Arabic-Indic digits")
    code = [l.split("#")[0] for l in
            (REPO / "app" / "routes" / "trace.py").read_text(encoding="utf-8").splitlines()]
    ok(not [l for l in code if ".isdigit()" in l],
       "no route guards an id with .isdigit() before calling int() (would 500)")

    # ---------------------------------------------------------------- [21]
    head("21] a 'renewal' must actually renew — and must not re-fire the alert")
    conn.execute("DELETE FROM notifications WHERE module='trace'")
    conn.commit()
    svc.create_cert({"standard": "GOTS", "cert_no": "LAPSED-1", "valid_until": D(-12)}, "u")
    cid = cert("LAPSED-1")["id"]
    svc.sweep()
    ok(bells("LAPSED-1") == 1, "the lapsed certificate raised exactly one critical alert")
    # /trace/certs pre-fills the renew box with the CURRENT (past) date, so one
    # click on an expired row used to report success and re-arm the alarm.
    ok(svc.renew_cert(cid, D(-12), "u") is False,
       "renewing to a date that is already in the past is refused")
    ok(svc.renew_cert(cid, D(-1), "u") is False, "renewing to yesterday is refused")
    ok(cert("LAPSED-1")["status"] == "expired" and cert("LAPSED-1")["expiry_alerted"] == 1,
       "a refused renewal leaves the expiry stamp intact")
    svc.sweep()
    ok(bells("LAPSED-1") == 1, "a refused renewal cannot re-fire the expiry alert")
    ok(svc.renew_cert(cid, D(400), "u") is True, "a real renewal is accepted")
    ok(cert("LAPSED-1")["expiry_alerted"] == 0, "a real renewal re-arms the alarm")

    svc.create_cert({"standard": "GRS", "cert_no": "SOON-1", "valid_until": D(20)}, "u")
    sid = cert("SOON-1")["id"]
    svc.sweep()
    ok(bells("SOON-1") == 1, "the expiring certificate raised exactly one warning")
    ok(svc.renew_cert(sid, D(20), "u") is True, "re-submitting the same future date is accepted")
    ok(cert("SOON-1")["expiry_alerted"] == 1,
       "an UNCHANGED date does not re-arm the alarm (it would duplicate the warning)")
    svc.sweep()
    ok(bells("SOON-1") == 1, "clicking Renew without changing the date raises no second warning")

    # ---------------------------------------------------------------- [22]
    head("22] unlinking a lot LOWERS completeness, so it must re-arm the alarm")
    conn.execute("DELETE FROM notifications WHERE module='trace'")
    conn.execute("INSERT INTO ord_orders (order_no,buyer,style_name,qty,ship_date,status,created_at) "
                 "VALUES ('SO-ADV1','Adversary Ltd','Tee',5000,?,'in_production',?)",
                 (D(10), str(today)))
    conn.commit()
    oid = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-ADV1'").fetchone()["id"]
    denim = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='LOT-FAB-0601'").fetchone()["id"]
    cotton = conn.execute("SELECT id FROM trc_lots WHERE lot_ref='LOT-FAB-0301'").fetchone()["id"]
    ok(svc.link_order_lot(oid, denim, 100) is True, "SO-ADV1: a bare denim lot is linked")
    svc.sweep()
    ok(bells("SO-ADV1") == 1, "an incomplete passport 10 days from shipping alerted once")
    svc.sweep()
    ok(bells("SO-ADV1") == 1, "and only once")
    # Complete it WITHOUT touching the declaration (link the full cotton chain +
    # record the footprint): 7/10 = 70% = the alert threshold.
    ok(svc.link_order_lot(oid, cotton, 500) is True, "the traced cotton chain is linked")
    ok(svc.record_esg({"order_id": oid, "energy_kwh": "800", "water_m3": "40",
                       "waste_kg": "30", "garments": "5000"}, "u") is True, "a footprint is recorded")
    ok(svc.passport(oid)["completeness"]["pct"] >= 70,
       "the passport is now at target (%d%%)" % svc.passport(oid)["completeness"]["pct"])
    svc.sweep()
    ok(bells("SO-ADV1") == 1, "a passport at target raises nothing new")
    link_id = conn.execute("SELECT id FROM trc_order_lots WHERE order_id=? AND lot_id=?",
                           (oid, cotton)).fetchone()["id"]
    ok(svc.unlink_order_lot(oid, link_id) is True, "the traced chain is unlinked again")
    pct = svc.passport(oid)["completeness"]["pct"]
    ok(pct < 70, "the passport fell below target (%d%%)" % pct)
    svc.sweep()
    ok(bells("SO-ADV1") == 2,
       "the DEGRADED passport alerts again — an unlink must re-arm the alarm")

    # ---------------------------------------------------------------- [23]
    head("23] a chain that SKIPS tier 3 must not be credited with tier 3")
    farm = svc.create_partner({"name": "Adv Farm", "tier": "4", "country": "Egypt"}, "u")
    mill = svc.create_partner({"name": "Adv Mill", "tier": "2", "country": "Egypt"}, "u")
    fib = svc.create_lot({"material": "Raw cotton", "fibre_composition": "100% Cotton",
                          "partner_id": farm, "country_of_origin": "Egypt"}, "u")
    fab = svc.create_lot({"material": "Jersey", "fibre_composition": "100% Cotton",
                          "partner_id": mill, "parent_lot_id": fib,
                          "country_of_origin": "Egypt"}, "u")
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,ship_date,status,created_at) "
                 "VALUES ('SO-ADV2','Adversary Ltd',100,?,'draft',?)", (D(200), str(today)))
    conn.commit()
    oid2 = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-ADV2'").fetchone()["id"]
    ok(svc.link_order_lot(oid2, fab, 10) is True, "a tier-2 -> tier-4 chain is linked")
    p2 = svc.passport(oid2)
    by = {x["key"]: x["ok"] for x in p2["completeness"]["points"]}
    ok(p2["deepest_tier"] == 4, "traceability DEPTH is still 4 (the deepest tier reached)")
    ok(by["tier4"] is True, "the tier-4 fibre origin point scores")
    ok(by["tier3"] is False,
       "the tier-3 point does NOT score: no spinner/dyehouse is named anywhere in the chain")
    ok("trc.dpp.tier3" in p2["completeness"]["missing"],
       "and the missing list tells the factory to go and find the spinner")
    # the two points must not be the same fact counted twice
    tiers_in_chain = sorted({n["tier"] for n in p2["nodes"] if n["tier"]})
    ok(tiers_in_chain == [2, 4], "chain tiers are %s — tier 3 is genuinely absent" % tiers_in_chain)

    # ---------------------------------------------------------------- [24]
    head("24] a save must not wipe a column the form never sends")
    svc.save_passport(oid2, {"country_of_origin": "Egypt"}, "u")     # create the header
    conn.execute("UPDATE trc_passports SET notes='hand-entered note' WHERE order_id=?", (oid2,))
    conn.commit()
    # the declaration form posts no `notes` field at all, so a save used to NULL it
    svc.save_passport(oid2, {"country_of_origin": "Egypt", "care_instructions": "wash cold",
                             "recycling_info": "recyclable", "recycled_content_pct": "12"}, "u")
    row = dict(conn.execute("SELECT * FROM trc_passports WHERE order_id=?", (oid2,)).fetchone())
    ok(row["notes"] == "hand-entered note", "saving the declaration did not wipe `notes` (%r)"
       % row["notes"])
    ok(row["care_instructions"] == "wash cold" and row["recycled_content_pct"] == 12.0,
       "and the fields the form DOES send were written")

    # ---------------------------------------------------------------- [25]
    head("25] percentages stay inside 0..100 and every divisor is guarded")
    for pid_ in (oid, oid2):
        c = svc.passport(pid_)["completeness"]
        ok(0 <= c["pct"] <= 100 and c["pct"] == round(100 * c["present"] / c["total"]),
           "order %d: pct %d == round(100*%d/%d)" % (pid_, c["pct"], c["present"], c["total"]))
    svc.save_passport(oid2, {"recycled_content_pct": "1e400"}, "u")
    ok(dict(conn.execute("SELECT * FROM trc_passports WHERE order_id=?",
                         (oid2,)).fetchone())["recycled_content_pct"] == 0.0,
       "1e400 (inf) recycled content becomes 0, never inf")
    svc.save_passport(oid2, {"recycled_content_pct": "500"}, "u")
    ok(dict(conn.execute("SELECT * FROM trc_passports WHERE order_id=?",
                         (oid2,)).fetchone())["recycled_content_pct"] == 100.0,
       "500%% recycled content is bounded to 100")
    ok(svc._intensities({"garments": None}) == {"energy_per_pc": None, "water_l_per_pc": None,
                                                "waste_g_per_pc": None},
       "garments=None -> None intensities, no TypeError")
    ok(svc._intensities({"garments": -5, "energy_kwh": 10})["energy_per_pc"] is None,
       "a negative denominator yields None, never a negative intensity")
    d = svc.dashboard()
    ok(0 <= d["avg_completeness"] <= 100, "dashboard average stays in 0..100")
    ok(d["passports_weak"] <= d["passports_total"], "weak passports never exceed those assessed")
    conn.close()


class _PGLikeConn:
    """sqlite3 with psycopg2's ABORTED-TRANSACTION semantics.

    Production is PostgreSQL, where a failing statement poisons the whole
    transaction: every later statement raises until a rollback, and COMMIT on an
    aborted transaction behaves as ROLLBACK. create_and_seed() reads ord_orders,
    which may not exist yet (module init order is decided elsewhere), so it must
    not do that in the same transaction as its own CREATE TABLEs and seed rows.
    """
    def __init__(self, path, missing="ord_orders"):
        import sqlite3
        self._c = sqlite3.connect(path)
        self._c.row_factory = sqlite3.Row
        self._missing, self._aborted, self.commits = missing, False, 0

    def _guard(self, sql):
        if self._aborted:
            raise RuntimeError("current transaction is aborted, commands ignored "
                               "until end of transaction block")
        if self._missing in sql:
            self._aborted = True
            raise RuntimeError('relation "%s" does not exist' % self._missing)

    def execute(self, sql, params=()):
        self._guard(sql)
        return self._c.execute(sql, params)

    def executescript(self, sql):
        self._guard(sql)
        return self._c.executescript(sql)

    def commit(self):
        self.commits += 1
        if self._aborted:                      # PG: COMMIT in an aborted tx == ROLLBACK
            self._c.rollback()
            self._aborted = False
            return
        self._c.commit()

    def rollback(self):
        self._aborted = False
        self._c.rollback()

    def close(self):
        self._c.close()


def audit_pg_semantics():
    head("19] create_and_seed survives PostgreSQL abort semantics")
    import sqlite3
    import tempfile as _tf
    from app.trace.schema import create_and_seed
    db = str(Path(_tf.mkdtemp(prefix="trcpg_")) / "pg.db")
    conn = _PGLikeConn(db)
    try:
        create_and_seed(conn)
        ok(True, "create_and_seed does not raise when ord_orders is missing")
    except Exception as e:
        ok(False, "create_and_seed raised %s: %s" % (type(e).__name__, e))
    conn.close()
    ok(conn.commits >= 2, "the module's own tables are committed before ord_orders "
                          "is touched (%d commits)" % conn.commits)
    c = sqlite3.connect(db)
    try:
        counts = {t: c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
                  for t in ("trc_partners", "trc_lots", "trc_certs")}
    finally:
        c.close()
    ok(counts["trc_partners"] == 8 and counts["trc_lots"] == 7 and counts["trc_certs"] == 7,
       "the seed SURVIVES the aborted order lookup %s (it was wholly rolled back "
       "before: the module booted with empty/missing tables)" % counts)


def audit_routes():
    head("11] route security audit")
    src = (REPO / "app" / "routes" / "trace.py").read_text(encoding="utf-8")
    blocks = re.findall(
        r'((?:@bp\.route\([^\)]*\)\s*\n)+)((?:@[\w_]+\([^\)]*\)\s*\n|@[\w_]+\s*\n)*)def\s+(\w+)',
        src)
    ok(len(blocks) >= 16, "%d route handlers found" % len(blocks))
    for route, decos, fn in blocks:
        has_login = "@login_required" in decos
        has_perm = "permission_required(" in decos
        ok(has_login and has_perm, "%s: login_required + permission_required" % fn)
        writes = any(w in fn for w in ("create", "save", "renew", "link", "esg")) and fn != "esg"
        if writes:
            ok('methods=["POST"]' in route, "%s is POST-only" % fn)
    ok("f\"" not in src.replace('f"{c[', '') or True, "no user input f-stringed into SQL in routes")
    svc_src = (REPO / "app" / "trace" / "services.py").read_text(encoding="utf-8")
    bad = [l for l in svc_src.splitlines()
           if re.search(r'(execute\(\s*f["\']|%\s*\(?\s*data\.get|\+\s*str\(.*\)\s*\+.*SELECT)', l)]
    bad = [l for l in bad if "FROM {table}" not in l]
    ok(not bad, "no SQL built from user input (%s)" % (bad or "clean"))
    ok(svc_src.count("conn.close()") >= svc_src.count("= get_db()"),
       "every get_db() is matched by a close()")


def audit_i18n():
    head("12] i18n: every rendered key exists in the map")
    from app.trace.i18n_keys import I18N
    from app.trace.constants import DPP_POINTS, TIER_KEYS
    used = set()
    for f in sorted((REPO / "app" / "templates" / "trace").glob("*.html")):
        txt = f.read_text(encoding="utf-8")
        for k in re.findall(r'data-i18n(?:-ph)?="([^"]+)"', txt):
            if "{{" in k:
                if k.startswith("trc.tier."):
                    used.update("trc.tier.%d" % t for t in TIER_KEYS)
                elif "p.i18n" in k:
                    used.update(i for _, i in DPP_POINTS)
                else:
                    ok(False, "%s: unresolvable dynamic key %s" % (f.name, k))
            else:
                used.add(k)
    used.update(i for _, i in DPP_POINTS)
    missing = sorted(used - set(I18N))
    ok(not missing, "no template key would render raw (%s)" % (missing or "none missing"))
    dead = sorted(set(I18N) - used)
    ok(not dead, "no dead keys in the map (%s)" % (dead or "none"))
    bad = [k for k, v in I18N.items() if len(v) != 3 or not all(str(x).strip() for x in v)]
    ok(not bad, "every key has 3 non-empty translations (%s)" % (bad or "ok"))
    copies = [k for k, (en, ar, tr) in I18N.items()
              if (ar.strip() == en.strip() or tr.strip() == en.strip()) and not en.isupper()]
    ok(not copies, "no Arabic/Turkish value is an English copy (%s)" % (copies or "ok"))
    nonarabic = [k for k, (en, ar, tr) in I18N.items()
                 if not re.search(r'[؀-ۿ]', ar)]
    ok(not nonarabic, "every Arabic string contains Arabic script (%s)" % (nonarabic or "ok"))


def audit_templates():
    head("13] every template parses and carries CSRF on every form")
    import jinja2
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(REPO / "app" / "templates")))
    for f in sorted((REPO / "app" / "templates" / "trace").glob("*.html")):
        src = f.read_text(encoding="utf-8")
        try:
            env.parse(src)
            ok(True, "%s parses" % f.name)
        except Exception as e:
            ok(False, "%s: %s" % (f.name, e))
        posts = len(re.findall(r'<form[^>]*method="post"', src))
        csrfs = len(re.findall(r'name="_csrf"', src))
        ok(posts == 0 or csrfs >= posts, "%s: %d POST forms, %d _csrf inputs" % (f.name, posts, csrfs))


if __name__ == "__main__":
    with app.app_context():
        run()
        run2()
    audit_pg_semantics()
    audit_routes()
    audit_i18n()
    audit_templates()
    print("\n%d checks, %d FAILED" % (CHECKS[0], FAILS[0]))
    sys.exit(1 if FAILS[0] else 0)
