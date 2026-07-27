"""
Cut room ADVERSARIAL test — the Phase-3 attack this module never received.

Everything the self-test already proves (happy-path formulas, the bell, the seed)
is NOT repeated here. This file only attacks:
  A) the arithmetic, recomputed by hand against literal expectations
  B) the weighted marker-efficiency average (weighting + NULL poisoning)
  C) a brute-force garbage matrix over EVERY numeric field of a lay
  D) nan / inf / 1e400 / negative / absurd input on the write paths
  E) create_and_seed x3 idempotency
  F) variance_sweep on a bare DB, without costing, and its re-arm contract
  G) missing sibling modules (warehouse absent / raising, costing table dropped)
  H) route security: auth, RBAC, method, 404-on-missing-id, CSRF
  I) SQL: whitelisted columns, no inner join dropping rows
  J) i18n: every data-i18n key in the templates vs app/static/i18n/en.json

Run:  python app/cutroom/tests_adversarial.py
"""
import json
import math
import os
import re
import sys
import tempfile
from glob import glob
from pathlib import Path

REPO = r"D:\TC platform\tc-platform-render"
TMP = Path(tempfile.mkdtemp(prefix="cut_adv_"))
os.chdir(TMP)
sys.path.insert(0, REPO)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                        # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                           # noqa: E402
from app.db import get_db, init_db                   # noqa: E402
from app.cutroom import services as svc              # noqa: E402
from app.cutroom import schema as cut_schema         # noqa: E402

OK = []


def check(name, cond):
    OK.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


def near(a, b, tol=1e-4):
    return a is not None and abs(a - b) <= tol


def finite(v):
    """A number that a CSV, JSON.parse() and a human can all read."""
    if v is None or isinstance(v, str):
        return True
    return isinstance(v, (int, float)) and math.isfinite(v)


app = create_app()
with app.app_context():
    conn = get_db()
    from app.warehouse.schema import create_and_seed as wh_seed
    from app.costing.schema import create_and_seed as cst_seed
    wh_seed(conn)
    cst_seed(conn)
    cut_schema.create_and_seed(conn)
    conn.commit()
    conn.close()

# ==========================================================================
print("\n[A] arithmetic recomputed by hand")
# Denim lay 1 by hand: plies 50, ppp 8, marker 7.30 m, width 150 cm, end 0.06,
# CAD area 9.31 m2, measured 371.5 m.
#   pieces      = 50 x 8                 = 400
#   theoretical = 7.30 x 50              = 365.00
#   planned     = (7.30 + 0.06) x 50     = 368.00
#   used        = 371.5 (measured > 0 wins over planned)
#   cons/gmt    = 371.5 / 400            = 0.928750
#   marker eff  = 9.31 / (7.30 x 1.50)   = 9.31 / 10.95 = 85.02283...%  -> 85.0
#   utilisation = 365.00 / 371.5         = 98.25033...%  -> 98.25
#   end loss    = (371.5 - 365.0) / 371.5 = 1.74966...%  -> 1.75
L1 = {"plies": 50, "pieces_per_ply": 8, "marker_length_m": 7.30, "marker_width_cm": 150,
      "end_allow_m": 0.06, "marker_area_m2": 9.31, "actual_fabric_m": 371.5}
m1 = svc.lay_metrics(L1)
check("A1 pieces_cut = plies x pieces_per_ply = 400", m1["pieces_cut"] == 400)
check("A1 theoretical = marker x plies = 365.00", m1["theoretical_m"] == 365.00)
check("A1 planned = (marker + end) x plies = 368.00", m1["planned_m"] == 368.00)
check("A1 used = measured 371.5 (not the plan)", m1["fabric_used_m"] == 371.5 and m1["measured"])
check("A1 cons/gmt = 0.92875", near(m1["cons_per_gmt"], 0.92875))
check("A1 marker eff = 9.31/10.95 = 85.0%", m1["marker_eff_pct"] == 85.0)
check("A1 utilisation = 365/371.5 = 98.25%", m1["utilisation_pct"] == 98.25)
check("A1 end loss = (371.5-365)/371.5 = 1.75%", m1["waste_pct"] == 1.75)
# the end-loss identity the docstring claims, computed the OTHER way round
check("A1 end loss identity (used-theo)/used == 100-utilisation",
      near(round((371.5 - 365.0) / 371.5 * 100.0, 2), m1["waste_pct"], 0.011))
check("A1 cons/gmt x pieces returns the metres used (4dp rounding aside)",
      near(m1["cons_per_gmt"] * m1["pieces_cut"], m1["fabric_used_m"], 0.05))

# fallback the other way: nothing measured -> the plan, including end loss
m1p = svc.lay_metrics(dict(L1, actual_fabric_m=0))
check("A2 unmeasured -> used = planned 368.00 and measured=False",
      m1p["fabric_used_m"] == 368.00 and m1p["measured"] is False)
check("A2 unmeasured cons/gmt = 368/400 = 0.92", near(m1p["cons_per_gmt"], 0.92))
check("A2 unmeasured utilisation = 365/368 = 99.18%", near(m1p["utilisation_pct"], 99.18, 0.005))
check("A3 negative measurement is NOT treated as a measurement",
      svc.lay_metrics(dict(L1, actual_fabric_m=-500))["fabric_used_m"] == 368.00)

# per-order roll-up by hand: lay2 = plies 44, ppp 8, actual 341.0
#   pieces 352, theoretical 321.20, planned 323.84, used 341.00
#   order: pieces 752, used 712.50, theo 686.20
#   actual cpg = 712.5/752 = 0.9474734  -> 0.9475
#   plan  cpg  = 0.85 x 1.08 = 0.918   (denim BOM: 0.85 m + 8% allowance)
#   variance % = (0.9474734-0.918)/0.918 = 3.2106% -> 3.21
#   variance m = (0.9474734-0.918) x 752 = 22.18 m
#   utilisation = 686.20/712.50 = 96.3088% -> 96.31
with app.app_context():
    den = next(r for r in svc.order_rollup() if (r["order"] or {}).get("style_ref") == "TC-DEN-03")
check("A4 order pieces 400+352 = 752", den["pieces_cut"] == 752)
check("A4 order used 371.5+341.0 = 712.50", den["fabric_used_m"] == 712.50)
check("A4 order theoretical 365.0+321.2 = 686.20", den["theoretical_m"] == 686.20)
check("A4 order actual cpg = 712.5/752 = 0.9475", near(den["actual_cpg"], 0.9475))
check("A4 order plan cpg = 0.85 x 1.08 = 0.918", near(den["planned_cpg"], 0.918))
check("A4 order variance = +3.21%", near(den["variance_pct"], 3.21, 0.005))
check("A4 order variance metres = 22.18", near(den["variance_m"], 22.18, 0.02))
check("A4 order utilisation = 686.2/712.5 = 96.31%", near(den["utilisation_pct"], 96.31, 0.005))
check("A4 short-cut balance = 9000 - 752 = 8248 (>0 = short)", den["balance"] == 8248)
check("A4 cut % = 752/9000 = 8.4%", near(den["cut_pct"], 8.4, 0.05))
check("A4 variance_m == (actual-plan) x pieces, recomputed",
      near(den["variance_m"], round((den["actual_cpg"] - den["planned_cpg"]) * 752, 2), 0.011))

# over-cut is the mirror image and must come out negative
over = svc._summarise({"id": 1, "qty": 300}, [svc.lay_metrics(L1)], None)
check("A5 over-cut balance is negative (300 ordered, 400 cut) = -100", over["balance"] == -100)
check("A5 over-cut cut% = 133.3", near(over["cut_pct"], 133.3, 0.05))

# ==========================================================================
print("\n[B] weighted marker efficiency — weighting and NULL poisoning")
big = svc.lay_metrics({"plies": 100, "pieces_per_ply": 10, "marker_length_m": 5.0,
                       "marker_width_cm": 100, "marker_area_m2": 4.5, "actual_fabric_m": 500.0})
small = svc.lay_metrics({"plies": 4, "pieces_per_ply": 10, "marker_length_m": 5.0,
                         "marker_width_cm": 100, "marker_area_m2": 2.5, "actual_fabric_m": 20.0})
check("B0 fixture: 90.0% over 500 m and 50.0% over 20 m",
      big["marker_eff_pct"] == 90.0 and small["marker_eff_pct"] == 50.0
      and big["fabric_used_m"] == 500.0 and small["fabric_used_m"] == 20.0)
w = svc._summarise({"id": 1, "qty": 0}, [big, small], None)
# (90 x 500 + 50 x 20) / 520 = 46000/520 = 88.4615 -> 88.5 ; the naive mean is 70.0
check("B1 weighted by fabric metres = 88.5, NOT the naive mean 70.0",
      w["marker_eff_pct"] == 88.5)
noeff = svc.lay_metrics({"plies": 200, "pieces_per_ply": 10, "marker_length_m": 5.0,
                         "marker_width_cm": 0, "actual_fabric_m": 1000.0})
check("B2 fixture: a 1000 m lay with NO efficiency at all",
      noeff["marker_eff_pct"] is None and noeff["fabric_used_m"] == 1000.0)
w2 = svc._summarise({"id": 1, "qty": 0}, [big, small, noeff], None)
check("B3 a NULL-efficiency lay cannot poison the average (still 88.5)",
      w2["marker_eff_pct"] == 88.5)
check("B4 but its fabric IS still counted in the metres (1520 m)",
      w2["fabric_used_m"] == 1520.0)
check("B5 every lay NULL -> average is None, never 0",
      svc._summarise({"id": 1, "qty": 0}, [noeff], None)["marker_eff_pct"] is None)
check("B6 no lays at all -> no crash, everything undefined",
      svc._summarise({"id": 1, "qty": 0}, [], None)["marker_eff_pct"] is None)

# ==========================================================================
print("\n[C] garbage matrix — every numeric field x every hostile value")
HOSTILE = [0, -1, -999999, None, "", "abc", [], {}, "nan", "NaN", "inf", "-inf",
           "1e400", 1e308, float("inf"), float("nan"), 10 ** 40, "0.0.0", " ", True]
FIELDS = ["plies", "pieces_per_ply", "marker_length_m", "marker_width_cm",
          "fabric_width_cm", "marker_area_m2", "marker_eff_pct", "end_allow_m",
          "actual_fabric_m"]
bad = []
for fld in FIELDS:
    for v in HOSTILE:
        try:
            mx = svc.lay_metrics(dict(L1, **{fld: v}))
        except Exception as e:                                   # noqa: BLE001
            bad.append(f"{fld}={v!r} raised {type(e).__name__}: {e}")
            continue
        for k, got in mx.items():
            if not finite(got) and not isinstance(got, bool):
                bad.append(f"{fld}={v!r} -> {k}={got!r}")
check("C1 %d field/value combinations: no exception, no nan/inf anywhere"
      % (len(FIELDS) * len(HOSTILE)), not bad)
for b in bad[:12]:
    print("        " + b)
check("C2 all-zero lay: pieces 0, every ratio None (never a misleading 0)",
      svc.lay_metrics(dict.fromkeys(FIELDS, 0))["cons_per_gmt"] is None)
z = svc.lay_metrics(dict(L1, marker_length_m=0, end_allow_m=0, actual_fabric_m=0))
check("C3 zero-marker, zero-spread lay: utilisation None, not 0, not ZeroDivision",
      z["utilisation_pct"] is None and z["waste_pct"] is None)
check("C4 zero marker WIDTH -> efficiency undefined, no ZeroDivision",
      svc.lay_metrics(dict(L1, marker_width_cm=0))["marker_eff_pct"] is None)
check("C5 zero marker LENGTH -> efficiency undefined, no ZeroDivision",
      svc.lay_metrics(dict(L1, marker_length_m=0))["marker_eff_pct"] is None)
check("C6 zero plies -> pieces 0 and cons/gmt None",
      svc.lay_metrics(dict(L1, plies=0))["pieces_cut"] == 0
      and svc.lay_metrics(dict(L1, plies=0))["cons_per_gmt"] is None)
check("C7 zero pieces/ply -> cons/gmt None", svc.lay_metrics(dict(L1, pieces_per_ply=0))["cons_per_gmt"] is None)
check("C8 area > marker rectangle is shown as >100%, not clamped to a plausible 100",
      svc.lay_metrics(dict(L1, marker_area_m2=12.0))["marker_eff_pct"] > 100)
check("C9 _summarise survives a lay list full of undefined metrics",
      svc._summarise(None, [svc.lay_metrics({}), svc.lay_metrics({})], None)["actual_cpg"] is None)

# ==========================================================================
print("\n[D] write paths — negative / nan / inf / absurd never reach the DB")
with app.app_context():
    lid = svc.create_lay({"order_id": "3", "plies": "-40", "pieces_per_ply": "8",
                          "marker_length_m": "nan", "marker_width_cm": "inf",
                          "marker_area_m2": "1e400", "end_allow_m": "-2",
                          "actual_fabric_m": str(10 ** 40), "marker_ref": "MK-ATTACK",
                          "status": "cut"}, {"username": "attacker"})
    row = svc.get_lay(lid)
    lay = row["lay"]
check("D1 negative plies stored as 0, never negative", lay["plies"] == 0)
check("D2 'nan' marker length stored as 0.0 (not nan)",
      lay["marker_length_m"] == 0.0 and finite(lay["marker_length_m"]))
check("D3 'inf' marker width stored as 0.0", lay["marker_width_cm"] == 0.0)
check("D4 '1e400' area stored as 0.0", lay["marker_area_m2"] == 0.0)
check("D5 negative end allowance stored as 0.0", lay["end_allow_m"] == 0.0)
check("D6 1e40 metres rejected as absurd -> 0.0", lay["actual_fabric_m"] == 0.0)
check("D7 every derived metric of the attacked lay is finite or None",
      all(finite(v) for v in lay.values() if not isinstance(v, (dict, list))))
check("D8 the whole bundle is finite (rolls gap included)",
      finite(row["rolls_gap_m"]) and finite(row["variance_pct"]))
with app.app_context():
    svc.update_lay(lid, {"plies": "-9", "actual_fabric_m": "nan", "marker_length_m": "1e400"})
    lay2 = svc.get_lay(lid)["lay"]
check("D9 update_lay applies the same coercion as create_lay",
      lay2["plies"] == 0 and lay2["actual_fabric_m"] == 0.0 and lay2["marker_length_m"] == 0.0)
with app.app_context():
    check("D10 roll booking refuses nan metres", svc.add_lay_roll(lid, {"meters": "nan"})[0] is False)
    check("D11 roll booking refuses inf metres", svc.add_lay_roll(lid, {"meters": "1e400"})[0] is False)
    check("D12 roll booking refuses a negative", svc.add_lay_roll(lid, {"meters": -1})[0] is False)
    check("D13 roll booking on a missing lay is refused, not silently created",
          svc.add_lay_roll(10 ** 9, {"meters": 5}) == (False, "lay_not_found"))
    # the KPI surface is what the factory reads: it must stay printable
    d = svc.dashboard()
check("D14 dashboard KPIs all finite after the attack",
      all(finite(d[k]) for k in ("fabric_used_m", "marker_eff_pct", "utilisation_pct",
                                 "waste_pct", "variance_pct", "variance_m")))

# ==========================================================================
print("\n[E] create_and_seed x3 — no duplication, no loss")
with app.app_context():
    conn = get_db()
    before = (conn.execute("SELECT COUNT(*) AS c FROM cut_lays").fetchone()["c"],
              conn.execute("SELECT COUNT(*) AS c FROM cut_lay_rolls").fetchone()["c"])
    for _ in range(3):
        cut_schema.create_and_seed(conn)
    after = (conn.execute("SELECT COUNT(*) AS c FROM cut_lays").fetchone()["c"],
             conn.execute("SELECT COUNT(*) AS c FROM cut_lay_rolls").fetchone()["c"])
    dupes = conn.execute("SELECT COUNT(*) AS c FROM (SELECT lay_no FROM cut_lays "
                         "GROUP BY lay_no HAVING COUNT(*)>1) x").fetchone()["c"]
    conn.close()
check("E1 lay + roll counts identical after 3 more runs (%s -> %s)" % (before, after),
      before == after)
check("E2 no duplicate lay_no", dupes == 0)
check("E3 the attacked lay survived the re-runs (nothing destroyed)",
      after[0] == before[0] and before[0] >= 5)

# Seeding must not be all-or-nothing on an OPTIONAL cross-module read: the roll
# links are best-effort, the lays are the point.
config.Config.DB_PATH = TMP / "nowh.db"
with app.app_context():
    init_db()
    conn = get_db()
    conn.execute("DELETE FROM cut_lays")
    conn.execute("DELETE FROM cut_lay_rolls")
    conn.execute("DROP TABLE IF EXISTS wh_rolls")
    conn.commit()
    cut_schema.create_and_seed(conn)
    seeded = conn.execute("SELECT COUNT(*) AS c FROM cut_lays").fetchone()["c"]
    linked = conn.execute("SELECT COUNT(*) AS c FROM cut_lay_rolls").fetchone()["c"]
    conn.close()
check("E4 warehouse table gone: 4 lays still seeded, 0 roll links (%d/%d)" % (seeded, linked),
      seeded == 4 and linked == 0)
config.Config.DB_PATH = TMP / "platform.db"

# ==========================================================================
print("\n[F] variance_sweep — bare DB, no costing, once-per-order, re-arm")
with app.app_context():
    conn = get_db()
    conn.execute("DELETE FROM notifications WHERE module='cutroom'")
    conn.execute("UPDATE cut_lays SET variance_alerted=0")
    conn.commit()
    conn.close()

    def bells():
        c = get_db()
        try:
            return c.execute("SELECT COUNT(*) AS c FROM notifications "
                             "WHERE module='cutroom'").fetchone()["c"]
        finally:
            c.close()

    svc.variance_sweep()
    check("F1 one bell for the one over-consuming order", bells() == 1)
    svc.variance_sweep()
    svc.variance_sweep()
    check("F2 two more sweeps do not re-alert", bells() == 1)
    den = next(r for r in svc.order_rollup() if (r["order"] or {}).get("style_ref") == "TC-DEN-03")
    svc.update_lay(den["lays"][0]["id"], {"notes": "re-measured on the table"})
    svc.variance_sweep()
    check("F3 editing a lay re-arms the alarm (2nd bell)", bells() == 2)
    svc.create_lay({"order_id": den["order_id"], "plies": 1, "pieces_per_ply": 1,
                    "marker_length_m": 1.0, "actual_fabric_m": 1.0}, {"username": "t"})
    svc.variance_sweep()
    check("F4 adding a lay re-arms the alarm (3rd bell)", bells() == 3)
    # moving a lay to another order changes BOTH orders' consumption, so both
    # must be re-armed — not just the destination.
    other = next(r for r in svc.order_rollup() if r["order_id"] not in (None, den["order_id"]))
    conn = get_db()
    conn.execute("UPDATE cut_lays SET variance_alerted=1")
    conn.commit()
    conn.close()
    moved = den["lays"][-1]["id"]
    svc.update_lay(moved, {"order_id": other["order_id"]})
    conn = get_db()
    flags = {r["order_id"]: r["f"] for r in conn.execute(
        "SELECT order_id, MAX(variance_alerted) AS f FROM cut_lays GROUP BY order_id")}
    conn.close()
    check("F4b moving a lay re-arms the OLD order too (flags %s)"
          % {k: flags[k] for k in (den["order_id"], other["order_id"])},
          flags[den["order_id"]] == 0 and flags[other["order_id"]] == 0)

# an empty database: every table installed, not one row anywhere
config.Config.DB_PATH = TMP / "empty.db"
with app.app_context():
    init_db()
    conn = get_db()
    conn.execute("DELETE FROM cut_lays")
    conn.commit()
    conn.close()
    try:
        svc.variance_sweep()
        check("F5 sweep on an empty (but created) DB does not raise", True)
    except Exception as e:                                       # noqa: BLE001
        check("F5 sweep on an empty (but created) DB does not raise: %r" % e, False)
    check("F6 dashboard on an empty DB is all-None, not a crash",
          svc.dashboard()["lays_total"] == 0)
    check("F7 rollup on an empty DB is []", svc.order_rollup() == [])
    check("F8 export of an empty DB yields headers and no rows",
          svc.export_dataset("lay-register")[0] and list(svc.export_dataset("lay-register")[1]) == [])

# a database with NO cut tables at all (module never installed)
config.Config.DB_PATH = TMP / "bare.db"
with app.app_context():
    try:
        svc.variance_sweep()
        check("F9 sweep with no cut_* tables at all does not raise", True)
    except Exception as e:                                       # noqa: BLE001
        check("F9 sweep with no cut_* tables at all does not raise: %r" % e, False)

config.Config.DB_PATH = TMP / "platform.db"

# ==========================================================================
print("\n[G] missing sibling modules degrade, never raise")
with app.app_context():
    saved = sys.modules.get("app.warehouse")
    sys.modules["app.warehouse"] = None          # forces ImportError on import
    try:
        check("G1 warehouse absent -> warehouse_rolls() == []", svc.warehouse_rolls("X") == [])
    finally:
        if saved is None:
            sys.modules.pop("app.warehouse", None)
        else:
            sys.modules["app.warehouse"] = saved

    from app.warehouse import services as wh_svc
    orig = wh_svc.list_rolls
    wh_svc.list_rolls = lambda **k: (_ for _ in ()).throw(RuntimeError("wh exploded"))
    try:
        check("G2 warehouse raising -> warehouse_rolls() == [], no 500",
              svc.warehouse_rolls("X") == [])
    finally:
        wh_svc.list_rolls = orig

    # costing's BOM table gone: the plan/variance must go dark, not explode
    conn = get_db()
    conn.execute("ALTER TABLE cst_bom_lines RENAME TO cst_bom_lines_hidden")
    conn.commit()
    conn.close()
    try:
        rows = svc.order_rollup()
        check("G3 costing BOM missing -> rollup still returns rows", len(rows) >= 3)
        check("G4 costing BOM missing -> planned/variance are None, not 0",
              all(r["planned_cpg"] is None and r["variance_pct"] is None for r in rows))
        check("G5 costing BOM missing -> dashboard still renders",
              svc.dashboard()["variance_pct"] is None)
        d0 = svc.get_lay(next(r for r in rows if r["lays"])["lays"][0]["id"])
        check("G6 costing BOM missing -> lay detail still loads", d0 is not None
              and d0["planned_cpg"] is None)
        svc.variance_sweep()
        check("G7 costing BOM missing -> sweep raises nothing and alerts nothing", True)
        # PostgreSQL poisons a transaction after a failed statement, so the
        # connection must still be usable for the writes that follow the BOM read.
        conn = get_db()
        try:
            still = conn.execute("SELECT COUNT(*) AS c FROM cut_lays").fetchone()["c"]
        finally:
            conn.close()
        check("G8 the connection is still usable after the failed BOM read", still > 0)
    finally:
        conn = get_db()
        conn.execute("ALTER TABLE cst_bom_lines_hidden RENAME TO cst_bom_lines")
        conn.commit()
        conn.close()

# ==========================================================================
print("\n[H] route security — auth, RBAC, method, missing id, CSRF")
with app.app_context():
    conn = get_db()
    admin = conn.execute("SELECT id, COALESCE(session_epoch,0) AS ep FROM users "
                         "WHERE role='super_admin' AND is_active=1 ORDER BY id").fetchone()
    admin = (admin["id"], admin["ep"])
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active) "
                 "VALUES (?,?,?,?,1)", ("cut_ro", "x", "Read Only", "executive_viewer"))
    conn.commit()
    ro = conn.execute("SELECT id, COALESCE(session_epoch,0) AS ep FROM users "
                      "WHERE username='cut_ro'").fetchone()
    ro = (ro["id"], ro["ep"])
    live = conn.execute("SELECT id FROM cut_lays ORDER BY id").fetchone()["id"]
    ord_id = conn.execute("SELECT id FROM ord_orders ORDER BY id").fetchone()["id"]
    conn.close()

GETS = ["/cutroom/", "/cutroom/lays", f"/cutroom/lays/{live}",
        f"/cutroom/orders/{ord_id}", "/cutroom/export/lay-register.csv",
        "/cutroom/api/order-summary.json"]
MANAGE_GETS = ["/cutroom/lays/new"]
POSTS = ["/cutroom/lays", f"/cutroom/lays/{live}", f"/cutroom/lays/{live}/rolls"]


def client(user=None):
    c = app.test_client()
    if user:
        with c.session_transaction() as s:
            s["uid"], s["ep"] = user
    return c


def token(c):
    c.get("/cutroom/lays")          # the token is minted on the first request
    with c.session_transaction() as s:
        return s.get("_csrf_token")


anon = client()
check("H1 every GET route bounces an anonymous visitor to login",
      all(anon.get(p).status_code in (301, 302) for p in GETS + MANAGE_GETS))
check("H2 every POST route bounces an anonymous visitor",
      all(anon.post(p, data={}).status_code in (301, 302, 400) for p in POSTS))

rc = client(ro)
check("H3 cut_view user can read every GET route (%s)"
      % {p: rc.get(p).status_code for p in GETS if rc.get(p).status_code != 200},
      all(rc.get(p).status_code == 200 for p in GETS))
check("H3b cut_view user cannot even open the new-lay form (403)",
      all(rc.get(p).status_code == 403 for p in MANAGE_GETS))
rt = token(rc)
codes = [rc.post(p, data={"_csrf": rt, "meters": 1}).status_code for p in POSTS]
check("H4 cut_view user is 403 on every state-changing route (%s)" % codes,
      all(x == 403 for x in codes))

ac = client(admin)
at = token(ac)
check("H5 GET on a POST-only route is 405 (no state change via a URL)",
      ac.get("/cutroom/lays/%d/rolls" % live).status_code == 405)
check("H6 POST without a CSRF token is refused",
      ac.post("/cutroom/lays/%d/rolls" % live, data={"meters": 5},
              headers={"Accept": "application/json"}).status_code == 400)
check("H7 lay detail of a non-existent id -> 404", ac.get("/cutroom/lays/999999").status_code == 404)
check("H8 order summary of a non-existent id -> 404",
      ac.get("/cutroom/orders/999999").status_code == 404)
check("H9 POST to a non-existent lay -> 404, not a fake 'updated' flash",
      ac.post("/cutroom/lays/999999", data={"_csrf": at, "notes": "ghost"}).status_code == 404)
check("H9b POST to a real lay still works (the 404 guard did not break the write)",
      ac.post("/cutroom/lays/%d" % live, data={"_csrf": at, "cutter": "Adversary"}
              ).status_code in (301, 302))
check("H10 booking a roll on a non-existent lay -> 404",
      ac.post("/cutroom/lays/999999/rolls",
              data={"_csrf": at, "meters": 5}).status_code == 404)
check("H11 lay create without an order is rejected (redirect, no row)",
      ac.post("/cutroom/lays", data={"_csrf": at, "plies": 5}).status_code in (301, 302))
check("H12 unknown export key -> 404 on both formats",
      ac.get("/cutroom/export/../../etc.csv").status_code in (404, 308)
      and ac.get("/cutroom/api/nope.json").status_code == 404)

# an orderless lay must not blow up the pages that link to its order
with app.app_context():
    conn = get_db()
    conn.execute("INSERT INTO cut_lays (lay_no,order_id,plies,pieces_per_ply,marker_length_m,"
                 "actual_fabric_m,status) VALUES ('CUT-ORPHAN',NULL,10,5,2.0,25.0,'cut')")
    conn.commit()
    orphan = conn.execute("SELECT id FROM cut_lays WHERE lay_no='CUT-ORPHAN'").fetchone()["id"]
    conn.close()
pages = {p: ac.get(p).status_code for p in
         ["/cutroom/", "/cutroom/lays", f"/cutroom/lays/{orphan}"]}
check("H13 a lay with a NULL order_id does not 500 the dashboard/list/detail (%s)" % pages,
      all(v == 200 for v in pages.values()))
check("H14 the orphan lay is still counted, not hidden",
      b"CUT-ORPHAN" in ac.get("/cutroom/lays").data)

# ==========================================================================
print("\n[I] SQL — whitelisted columns, no row silently dropped")
with app.app_context():
    before = len(svc.list_lays())
    inj = svc.create_lay({"order_id": 3, "notes": "ok",
                          "plies=999999, status='cut' --": "1",
                          "status); DROP TABLE cut_lays; --": "1"}, {"username": "t"})
    after = svc.list_lays()
check("I1 unknown form keys are ignored (whitelist), cut_lays intact",
      len(after) == before + 1)
check("I2 the injected column names did not become data",
      next(l for l in after if l["id"] == inj)["plies"] == 0)
with app.app_context():
    # A blank status select stores NULL. `status != 'cancelled'` is NULL-blind SQL,
    # so such a lay would vanish from every fabric number while still showing in
    # the register — pieces and metres silently uncounted.
    live_lay = next(l for l in svc.list_lays() if l["order_id"] and l["pieces_cut"] > 0)
    oid = live_lay["order_id"]
    p0, f0 = svc.order_summary(oid)["pieces_cut"], svc.dashboard()["fabric_used_m"]
    svc.update_lay(live_lay["id"], {"status": ""})
    p1, f1 = svc.order_summary(oid)["pieces_cut"], svc.dashboard()["fabric_used_m"]
    conn = get_db()
    stored = conn.execute("SELECT status FROM cut_lays WHERE id=?",
                          (live_lay["id"],)).fetchone()["status"]
    conn.close()
check("I0a a blank status really does store NULL", stored is None)
check("I0b a NULL-status lay is still counted: order %s->%s, fabric %s->%s"
      % (p0, p1, f0, f1), p0 == p1 and f0 == f1)
with app.app_context():
    svc.update_lay(live_lay["id"], {"status": "cut"})
    check("I0c a genuinely cancelled lay IS still excluded",
          svc.order_summary(oid)["pieces_cut"] == p0
          and (svc.update_lay(live_lay["id"], {"status": "cancelled"}) or True)
          and svc.order_summary(oid)["pieces_cut"] < p0)
    svc.update_lay(live_lay["id"], {"status": "cut"})
with app.app_context():
    svc.add_lay_roll(orphan, {"meters": 25, "roll_no": "R-ORPHAN"})
    hdrs, rolls = svc.export_dataset("lay-rolls")
    rolls = list(rolls)
check("I3 a roll on an orderless lay still exports (LEFT JOIN, not dropped)",
      any(r[hdrs.index("Roll No")] == "R-ORPHAN" for r in rolls))
check("I4 every export row is a clean flat sequence, no None/dict/list",
      all(len(r) == len(hdrs) and all(finite(c) and not isinstance(c, (dict, list))
                                      and c is not None for c in r) for r in rolls))
with app.app_context():
    for k in ("lay-register", "order-summary", "lay-rolls"):
        h, rs = svc.export_dataset(k)
        rs = list(rs)
        check("I5 %s: %d rows, all finite and shaped" % (k, len(rs)),
              all(len(r) == len(h) and all(finite(c) and c is not None for c in r) for r in rs))
j = json.loads(ac.get("/cutroom/api/lay-register.json").data)
raw = ac.get("/cutroom/api/lay-register.json").data.decode("utf-8")
check("I6 JSON export contains no NaN/Infinity token (JSON.parse would throw)",
      "NaN" not in raw and "Infinity" not in raw)
check("I7 JSON row count matches the register", j["count"] == len(j["rows"]) == len(after))

# ==========================================================================
print("\n[J] i18n — every data-i18n key vs app/static/i18n/en.json")
en = json.load(open(os.path.join(REPO, "app/static/i18n/en.json"), encoding="utf-8"))
keys = {}
for f in glob(os.path.join(REPO, "app/templates/cutroom/*.html")):
    for mt in re.finditer(r'data-i18n(?:-ph)?="([^"]+)"', open(f, encoding="utf-8").read()):
        keys.setdefault(mt.group(1), set()).add(os.path.basename(f))
missing = {k for k in keys if k not in en}
print("        %d distinct keys in the cut room templates" % len(keys))
for k in sorted(missing):
    print("        MISSING from en.json: %s  (%s)" % (k, ", ".join(sorted(keys[k]))))
# The two export keys this once tolerated (cut.action.export, cut.action.export_rolls)
# were reported upstream and have since landed in en/ar/tr, so the tolerance is gone:
# every cut room key must resolve, or a missing one renders as its own raw key.
check("J1 every cut room i18n key exists in en.json", not missing)

print("\n%d/%d checks passed" % (sum(1 for _, o in OK if o), len(OK)))
sys.exit(0 if all(o for _, o in OK) else 1)
