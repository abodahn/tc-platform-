"""
/factory single-source-of-truth test.

The claim under test: /factory is a PRESENTATION layer. Every converted page shows
the real module's live records, and no /factory page writes to a real module's
table behind that module's back.

  1  a record created in the REAL module appears on the /factory page
        orders -> /factory/orders, MES hourly -> /factory/production + command
        center + floor, QMS -> /factory/quality, MES bundles -> /factory/bundles,
        wash -> /factory/wash, people piece rate -> /factory/workforce,
        MES + costing -> /factory/costing, MES downtime -> /factory/intelligence
  2  /factory writes NOTHING: the four old entry POSTs redirect to the owning
        module and every real table (and every sf_* table) is byte-identical after
  3  every /factory route is 200 (the whole blueprint, /logout never requested)
  4  the one page still on its own data says so in the UI
  5  200 in en/ar/tr and every data-i18n key on a /factory template resolves
  6  create_and_seed x3 is idempotent and does not overwrite an admin's edit

    python app/smartfactory/tests_single_source.py
"""
import json
import os
import re
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="sf_"))
os.chdir(TMP)
REPO = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, REPO)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                            # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                               # noqa: E402

FAILED = []
TODAY = date.today()
U = {"username": "sf_test"}


def section(n, title):
    print("\n== %s  %s" % (n, title))


def check(name, got, want):
    ok = got == want
    FAILED.append(name) if not ok else None
    print("  %s %s" % ("PASS " if ok else "FAIL ", name)
          + ("" if ok else "\n         got %r\n         want %r" % (got, want)))


def yes(name, cond, detail=""):
    check(name, bool(cond), True) if cond else check(
        name + ((" -> " + str(detail)) if detail else ""), bool(cond), True)


app = create_app()

# ==========================================================================
section(0, "seed the REAL modules with records /factory must show")
# ==========================================================================
with app.app_context():
    from app.db import get_db
    from app.orders import services as osvc
    from app.mes import services as msvc
    from app.quality import services as qsvc
    from app.wash import services as wsvc
    from app.people import services as psvc
    from app.costing import services as csvc
    from app.mes.constants import HOUR_SLOTS

    conn = get_db()
    LINE = conn.execute("SELECT id, name FROM production_lines ORDER BY id").fetchone()
    LINE_ID, LINE_NAME = LINE["id"], LINE["name"]
    EMP = conn.execute("SELECT id, employee_code, employee_name FROM prob_employees "
                       "ORDER BY id LIMIT 1").fetchone()
    EMP_ID = EMP["id"] if EMP else None
    conn.close()

    ORDER_ID = osvc.create_order(
        {"buyer": "SingleSource Ltd", "po_no": "SSOT-PO-1", "style_ref": "SSOT-STYLE",
         "style_name": "Single Source Jean", "qty": 4000, "unit_price": 12.5,
         "ship_date": str(TODAY + timedelta(days=30)), "status": "in_production"}, U)
    ORDER_NO = None
    with app.app_context():
        from app.db import get_db as _g
        cx = _g()
        ORDER_NO = cx.execute("SELECT order_no FROM ord_orders WHERE id=?",
                              (ORDER_ID,)).fetchone()["order_no"]
        cx.close()
    print("  ..    order %s (id %s) on line %s" % (ORDER_NO, ORDER_ID, LINE_NAME))

    # --- MES: an hour on the board, and a coded loss
    ok, msg = msvc.save_hourly({"line_id": LINE_ID, "order_id": ORDER_ID,
                                "work_date": str(TODAY), "hour_slot": HOUR_SLOTS[7],
                                "target_qty": 400, "actual_qty": 377, "reject_qty": 7,
                                "operators": 12, "smv": 9.0}, U)
    check("mes.save_hourly accepted the hour", (ok, msg), (True, "mes.msg.saved"))
    ok, msg = msvc.add_downtime({"line_id": LINE_ID, "work_date": str(TODAY),
                                 "reason": "changeover", "minutes": 37.0}, U)
    check("mes.add_downtime accepted the loss", (ok, msg), (True, "mes.msg.saved"))

    # --- MES: a bundle in the ledger
    ok, BUNDLE_ID = msvc.create_bundle({"order_id": ORDER_ID, "size": "XXL",
                                        "color": "SsotIndigo", "qty": 55,
                                        "origin_section": "cutting"}, U)
    check("mes.create_bundle accepted the bundle", ok, True)
    with app.app_context():
        from app.db import get_db as _g
        cx = _g()
        BUNDLE_NO = cx.execute("SELECT bundle_no FROM mes_bundles WHERE id=?",
                               (BUNDLE_ID,)).fetchone()["bundle_no"]
        cx.close()

    # --- QMS: an inspection with a defect
    # str(): quality.create_inspection calls .isdigit() on order_id — it takes the
    # form value, not an int. (Pre-existing; noted in the report, not this lane's file.)
    INSP_ID = qsvc.create_inspection({"order_id": str(ORDER_ID), "stage": "end_line",
                                      "lot_size": 4000, "aql": 2.5,
                                      # lot 4000 -> code letter L -> sample 200: the QMS
                                      # leaves a part-inspected lot 'pending' on purpose.
                                      "units_inspected": 200, "defective_units": 4,
                                      "inspector": "ssot"}, U)
    ok, _ = qsvc.add_defect(INSP_ID, {"defect_type": "Zipper defect", "section": "sewing",
                                      "qty": 9, "severity": "major"}, U)
    check("quality.add_defect accepted the defect line", ok, True)
    with app.app_context():
        from app.db import get_db as _g
        cx = _g()
        INSP_REF = cx.execute("SELECT ref FROM qc_inspections WHERE id=?",
                              (INSP_ID,)).fetchone()["ref"]
        cx.close()

    # --- Wash: a recipe version with one bath, and an executed lot on it
    RID = wsvc.create_recipe({"code": "SSOT-WR", "name": "Single source stone",
                              "wash_type": "stone", "order_id": ORDER_ID}, U)
    with app.app_context():
        from app.db import get_db as _g
        cx = _g()
        VID = cx.execute("SELECT id FROM wsh_versions WHERE recipe_id=? ORDER BY id",
                         (RID,)).fetchone()["id"]
        cx.close()
    ok, _ = wsvc.add_step(VID, {"operation": "stone", "temp_c": 60, "minutes": 45,
                                "liquor_ratio": 8, "load_kg": 250}, U)
    check("wash.add_step accepted the bath", ok, True)
    BATCH_ID, err = wsvc.create_batch({"version_id": VID, "order_id": ORDER_ID,
                                       "load_kg": 250, "act_minutes": 45, "act_temp_c": 60,
                                       "act_water_l": 1990, "shade": "SsotBlue"}, U)
    check("wash.create_batch accepted the lot", err, None)
    with app.app_context():
        from app.db import get_db as _g
        cx = _g()
        BATCH_NO = cx.execute("SELECT batch_no FROM wsh_batches WHERE id=?",
                              (BATCH_ID,)).fetchone()["batch_no"]
        cx.close()

    # --- People: one operator-day of piece-rate output
    ok, _pr = psvc.add_piece_rate({"employee_id": EMP_ID, "work_date": str(TODAY),
                                   "operation": "Attach pocket", "order_id": ORDER_ID,
                                   "pieces": 500, "smv": 0.9, "minutes_worked": 480}, U)
    check("people.add_piece_rate accepted the shift", ok, True)

    # --- Costing: the order's real minute rate
    csvc.save_sheet(ORDER_ID, {"smv": 9.0, "cm_rate": 0.44, "overhead_per_unit": 0.2}, U)

# ==========================================================================
section(1, "every converted /factory page shows the REAL module's record")
# ==========================================================================
with app.app_context():
    from app.smartfactory import services as svc

    orders = svc.list_orders()
    row = [o for o in orders if o["id"] == ORDER_ID]
    yes("orders: the new ord_orders row is on /factory/orders", row, orders[:2])
    if row:
        check("orders: it is the REAL order, not an sf_orders copy",
              (row[0]["po_no"], row[0]["buyer"], row[0]["style_code"]),
              ("SSOT-PO-1", "SingleSource Ltd", "SSOT-STYLE"))
        # cst_sheets.smv = 9.0 is the only DEFINITION-level SMV for this order.
        check("orders: SMV comes from the canonical resolver", row[0]["smv"], 9.0)

    prod = svc.list_production()
    hit = [r for r in prod if r["hour_slot"] == HOUR_SLOTS[7] and r["line_id"] == LINE_ID]
    yes("production: the mes_hourly cell is on /factory/production", hit)
    if hit:
        check("production: target/actual/reject are the MES numbers",
              (hit[0]["target_qty"], hit[0]["actual_qty"], hit[0]["reject_qty"]),
              (400, 377, 7))
        check("production: the order is the real order", hit[0]["po_no"], "SSOT-PO-1")

    d = svc.dashboard()
    check("command center: the day shown is the day the floor reported",
          d["work_date"], str(TODAY))
    yes("command center: produced includes the 377 pieces just recorded",
        d["kpis"]["produced"] >= 377, d["kpis"]["produced"])
    yes("command center: rejects include the 7 just recorded",
        d["kpis"]["reject"] >= 7, d["kpis"]["reject"])
    yes("command center/floor: the line is on the live board",
        any(l["line"] == LINE_NAME for l in d["live"]), [l["line"] for l in d["live"]])
    yes("command center: the QMS defect is in the pareto",
        any(p["code"] == "Zipper defect" for p in d["pareto"]), d["pareto"])
    yes("command center: DHU is the QMS DHU, not a /factory calculation",
        d["kpis"]["dhu"] > 0, d["kpis"]["dhu"])

    q = svc.list_quality()
    yes("quality: the qc_inspections row is on /factory/quality",
        any(r["ref"] == INSP_REF for r in q), [r.get("ref") for r in q[:3]])
    qr = [r for r in q if r["ref"] == INSP_REF]
    if qr:
        check("quality: units/defects/verdict are the QMS numbers",
              (qr[0]["units_inspected"], qr[0]["defects"], qr[0]["verdict"]),
              (200.0, 9.0, "pass"))

    b = svc.bundles()
    yes("bundles: the mes_bundles row is on /factory/bundles",
        any(x["bundle_no"] == BUNDLE_NO for x in b["bundles"]),
        [x["bundle_no"] for x in b["bundles"][:3]])
    yes("bundles: WIP comes from the MES move ledger", b["wip_units"] >= 55, b["wip_units"])

    w = svc.wash_list()
    wr = [x for x in w["rows"] if x["batch_no"] == BATCH_NO]
    yes("wash: the wsh_batches lot is on /factory/wash", wr,
        [x["batch_no"] for x in w["rows"][:3]])
    if wr:
        check("wash: the METERED water is used, not the recipe estimate",
              (wr[0]["water_l"], wr[0]["water_metered"]), (1990.0, True))
        check("wash: the load is the wash module's kg", wr[0]["load_kg"], 250.0)
        # one bath, 250 kg x ratio 8 = 2000 L nominal; metered 1990 on 250 kg
        check("wash: intensity is per kg of goods", w["agg"]["water_per_kg"] > 0, True)

    wf = svc.workforce()
    yes("workforce: the ppl_piece_rate operator is on /factory/workforce",
        any(r["code"] == EMP["employee_code"] for r in wf["rows"]),
        [r["code"] for r in wf["rows"][:3]])
    hit = [r for r in wf["rows"] if r["code"] == EMP["employee_code"]]
    if hit:
        # The claim is not "this arithmetic is right" — it is "this is the SAME number
        # the people module reports", which is what one source of truth means.
        theirs = [r for r in psvc.incentive_summary(wf["from_date"], wf["to_date"])["rows"]
                  if r["employee_code"] == EMP["employee_code"]]
        check("workforce: efficiency IS the people module's own figure",
              (hit[0]["efficiency"], hit[0]["produced"]),
              (theirs[0]["efficiency_pct"], int(theirs[0]["pieces"])))
        yes("workforce: the shift just recorded is inside it",
            theirs[0]["pieces"] >= 500, theirs[0]["pieces"])

    c = svc.costing()
    cr = [r for r in c["rows"] if r["po_no"] == "SSOT-PO-1"]
    yes("costing: the order the floor ran is on /factory/costing", cr,
        [r["po_no"] for r in c["rows"][:3]])
    if cr:
        check("costing: produced/reject are the MES numbers", (cr[0]["produced"], cr[0]["reject"]),
              (377, 7))
        check("costing: the minute rate is the order's OWN cst_sheets.cm_rate",
              (cr[0]["rate"], cr[0]["priced"]), (0.44, True))
        check("costing: labour = produced x canonical SMV x that rate",
              cr[0]["labor"], round(377 * 9.0 * 0.44, 0))

    bank = svc.minute_bank()
    yes("intelligence: the coded loss is priced on the minute bank",
        any(r["line"] == LINE_NAME and r["lost_min"] >= 37 for r in bank["rows"]), bank["rows"])
    ship = svc.ship_risk()
    yes("intelligence: ship-risk reads the QMS register",
        any(s["po_no"] == ORDER_NO for s in ship), [s["po_no"] for s in ship])

# ==========================================================================
section(2, "/factory writes NOTHING — the old entry POSTs redirect, they do not save")
# ==========================================================================
TABLES = ["mes_hourly", "mes_downtime", "mes_bundles", "mes_bundle_moves",
          "qc_inspections", "qc_defects", "wsh_batches", "wsh_recipes", "wsh_versions",
          "ord_orders", "ppl_piece_rate", "cst_sheets", "cst_actuals", "wh_rolls",
          "wh_movements", "sf_prod_entries", "sf_quality", "sf_defects", "sf_bundles",
          "sf_wash_batches", "sf_orders", "sf_styles"]


def counts():
    with app.app_context():
        from app.db import get_db as _g
        cx = _g()
        out = {}
        for t in TABLES:
            try:
                out[t] = cx.execute("SELECT COUNT(*) AS c FROM %s" % t).fetchone()["c"]
            except Exception:
                out[t] = None
        cx.close()
        return out


before = counts()
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    tok = None
    body = c.get("/factory/approvals").get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', body)
    tok = m.group(1) if m else ""
    posts = {
        "/factory/production": {"line_id": LINE_ID, "hour_slot": "08:00-09:00",
                                "target_qty": 999, "actual_qty": 999, "lost_min": 99,
                                "operator": "ghost"},
        "/factory/quality": {"line_id": LINE_ID, "stage": "endline", "inspected": 999,
                             "defect": 99, "rework": 9, "reject": 9},
        "/factory/bundles": {"order_id": ORDER_ID, "size": "M", "qty": 999,
                             "operation_at": "cutting"},
        "/factory/wash": {"order_id": ORDER_ID, "recipe": "ghost", "water_l": 999,
                          "energy_kwh": 99, "chemical_kg": 9, "pieces": 999},
    }
    for path, data in posts.items():
        r = c.post(path, data={**data, "_csrf": tok})
        check("POST %s redirects to the owning module" % path, r.status_code, 302)
        yes("POST %s does not land back on /factory" % path,
            "/factory" not in (r.headers.get("Location") or ""), r.headers.get("Location"))
after = counts()
check("no table changed — /factory wrote nothing anywhere",
      sorted(k for k in before if before[k] != after[k]), [])
print("  ..    %d tables compared, %d rows total, unchanged"
      % (len(TABLES), sum(v or 0 for v in after.values())))

with app.app_context():
    src = (Path(REPO) / "app" / "smartfactory" / "services.py").read_text(encoding="utf-8")
    rsrc = (Path(REPO) / "app" / "routes" / "smartfactory.py").read_text(encoding="utf-8")
    writes = re.findall(r"\b(INSERT INTO|UPDATE |DELETE FROM)\s*\w*", src + rsrc)
    check("no INSERT/UPDATE/DELETE anywhere in the /factory read+route layer", writes, [])

# ==========================================================================
section(3, "every /factory route is 200 (whole blueprint; /logout never requested)")
# ==========================================================================
ROUTES = []
for rule in app.url_map.iter_rules():
    if not str(rule).startswith("/factory"):
        continue
    if "GET" not in (rule.methods or set()):
        continue
    path = str(rule)
    if "<kind>" in path:
        for k in ("production", "quality", "costing", "wash"):
            for f in ("csv", "xlsx"):
                ROUTES.append(path.replace("<kind>", k).replace("<fmt>", f))
    elif "<" in path:
        continue
    else:
        ROUTES.append(path)
ROUTES = sorted(set(ROUTES))
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    bad = []
    for p in ROUTES:
        r = c.get(p)                     # RAW status: follow_redirects hides a 302 to /login
        if r.status_code != 200:
            bad.append((p, r.status_code))
    check("all %d /factory GET routes are 200" % len(ROUTES), bad, [])
    print("  ..    " + ", ".join(ROUTES))

# ==========================================================================
section(4, "the one page still on its own data says so")
# ==========================================================================
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    body = c.get("/factory/approvals").get_data(as_text=True)
    yes("approvals carries the own-data label", 'data-i18n="sf.own_data"' in body)
    yes("approvals says it is not the procurement ladder", "/procurement" in body)
    for path, label in (("/factory/production", "/mes/entry"),
                        ("/factory/quality", "/quality/inspections"),
                        ("/factory/bundles", "/mes/bundles"),
                        ("/factory/wash", "/wash/batches"),
                        ("/factory/orders", "/orders/list"),
                        ("/factory/workforce", "/people/incentive"),
                        ("/factory/costing", "/costing")):
        b = c.get(path).get_data(as_text=True)
        yes("%s names its source (%s)" % (path, label),
            'data-i18n="sf.reads_from"' in b and label in b)

# ==========================================================================
section(5, "200 in en/ar/tr and every data-i18n key resolves")
# ==========================================================================
from app.smartfactory.i18n_keys import I18N as NEW                       # noqa: E402

TPLS = sorted((Path(REPO) / "app" / "templates" / "smartfactory").glob("*.html"))
keys = set()
for p in TPLS:
    keys |= {k for k in re.findall(r'data-i18n="([^"]+)"', p.read_text(encoding="utf-8"))
             if "{{" not in k}
# Keys the templates build at render time. Every possible expansion must exist —
# a missing one renders as the RAW KEY to every user, English included.
DYNAMIC = ({"sf.cause.downtime", "sf.cause.quality", "sf.cause.balance"}
           | {"sf.rep_production", "sf.rep_quality", "sf.rep_costing", "sf.rep_wash"})
keys |= DYNAMIC
print("  ..    %d distinct keys across %d templates" % (len(keys), len(TPLS)))

i18n_dir = Path(REPO) / "app" / "static" / "i18n"
for lang, idx in (("en", 0), ("ar", 1), ("tr", 2)):
    have = json.loads((i18n_dir / ("%s.json" % lang)).read_text(encoding="utf-8"))
    missing = sorted(k for k in keys
                     if k not in have and not (NEW.get(k) and NEW[k][idx].strip()))
    check("%s: every data-i18n key resolves" % lang, missing, [])
have_en = json.loads((i18n_dir / "en.json").read_text(encoding="utf-8"))
pending = sorted(k for k in keys if k in NEW and k not in have_en)
print("  ..    %d key(s) supplied by app/smartfactory/i18n_keys.py for the orchestrator "
      "to splice: %s" % (len(pending), pending))
check("every NEW key carries real en+ar+tr",
      sorted(k for k, v in NEW.items() if not (len(v) == 3 and all(str(x).strip() for x in v))),
      [])
check("no NEW key is dead weight",
      sorted(k for k in NEW if k not in keys), [])

with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    for lang in ("en", "ar", "tr"):
        with app.app_context():
            from app.db import get_db as _g
            cx = _g()
            cx.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
            cx.commit()
            cx.close()
        bad = [(p, c.get(p).status_code) for p in ROUTES if c.get(p).status_code != 200]
        check("%s: all /factory routes 200" % lang, bad, [])

# ==========================================================================
section(6, "create_and_seed x3 is idempotent and never overwrites an edit")
# ==========================================================================
with app.app_context():
    from app.db import get_db as _g
    from app.smartfactory.schema import create_and_seed
    cx = _g()
    cx.execute("UPDATE sf_styles SET name=? WHERE code='D-501'", ("ADMIN EDITED",))
    cx.commit()
    n1 = {t: cx.execute("SELECT COUNT(*) AS c FROM %s" % t).fetchone()["c"]
          for t in ("sf_styles", "sf_operations", "sf_orders", "sf_bundles", "sf_cost_config")}
    for _ in range(3):
        create_and_seed(cx)
    n2 = {t: cx.execute("SELECT COUNT(*) AS c FROM %s" % t).fetchone()["c"] for t in n1}
    edited = cx.execute("SELECT name FROM sf_styles WHERE code='D-501'").fetchone()["name"]
    cx.close()
check("seeding three more times duplicates nothing", n2, n1)
check("the admin's edit survived re-seeding", edited, "ADMIN EDITED")

print("\n" + ("ALL GREEN" if not FAILED else "FAILED: %s" % FAILED))
sys.exit(1 if FAILED else 0)
