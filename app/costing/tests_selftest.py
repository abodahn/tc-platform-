"""
Costing self-test — runs the schema + services against a throwaway database.
    python app/costing/tests_selftest.py
Covers the money invariants, not just the happy path: the required-qty formula
with an exact expected number, unpriced lines, division by zero, bad input, the
material double-counting rule, variance direction and the bell threshold.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cst_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                          # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                             # noqa: E402

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


with app.app_context():
    from app.db import get_db
    from app.costing.schema import create_and_seed
    from app.costing import services as svc
    from app.costing.constants import VARIANCE_ALERT_PCT

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    print("\n-- schema + seed --")
    orders = conn.execute("SELECT id, order_no, style_ref, qty, unit_price, currency "
                          "FROM ord_orders ORDER BY id").fetchall()
    print("   demo orders:", [(o["order_no"], o["style_ref"], o["qty"]) for o in orders])
    n_bom = conn.execute("SELECT COUNT(*) c FROM cst_bom_lines").fetchone()["c"]
    n_sheet = conn.execute("SELECT COUNT(*) c FROM cst_sheets").fetchone()["c"]
    n_act = conn.execute("SELECT COUNT(*) c FROM cst_actuals").fetchone()["c"]
    ok("seed created BOM lines / sheets / actuals (%d/%d/%d)" % (n_bom, n_sheet, n_act),
       n_bom > 0 and n_sheet > 0 and n_act > 0)

    create_and_seed(conn); conn.commit()                # idempotent: safe on every boot
    ok("re-seeding does not duplicate or destroy",
       conn.execute("SELECT COUNT(*) c FROM cst_bom_lines").fetchone()["c"] == n_bom)

    print("\n-- the material-plan formula --")
    # 12 000 pcs x 0.24 kg/pc x 1.03 = 2 966.4 kg. Exact, by hand.
    ok("required_qty(12000, 0.24, 3) == 2966.4", svc.required_qty(12000, 0.24, 3) == 2966.4)
    ok("zero allowance is a pure multiply", svc.required_qty(9000, 0.85, 0) == 7650.0)
    ok("blank consumption -> 0, not a crash", svc.required_qty(12000, "", 5) == 0.0)
    ok("None order qty -> 0", svc.required_qty(None, 0.24, 3) == 0.0)
    ok("garbage allowance -> treated as 0%", svc.required_qty(100, 2, "abc") == 200.0)

    print("\n-- margin, variance, division by zero --")
    ok("margin(46200, 36032.58) == (10167.42, 22.01)",
       svc.margin(46200, 36032.58) == (10167.42, 22.01))
    ok("zero revenue -> pct None, no ZeroDivisionError", svc.margin(0, 500) == (-500.0, None))
    ok("None revenue -> pct None", svc.margin(None, 0) == (0.0, None))
    v = svc._variance(1000, 1100)
    ok("overspend is unfavourable +10%%", v["flag"] == "unfavourable" and v["pct"] == 10.0)
    v = svc._variance(1000, 900)
    ok("underspend is favourable -10%%", v["flag"] == "favourable" and v["pct"] == -10.0)
    ok("zero estimate -> pct None, flag still set",
       svc._variance(0, 250)["pct"] is None and svc._variance(0, 250)["flag"] == "unfavourable")
    ok("float noise stays on_plan", svc._variance(1000, 1000.001)["flag"] == "on_plan")

    print("\n-- CM derivation --")
    ok("SMV x rate wins when both set", svc.cm_unit({"smv": 12.5, "cm_rate": 0.075}) == 0.9375)
    ok("flat CM used when SMV/rate absent",
       svc.cm_unit({"smv": 0, "cm_rate": 0, "cm_per_unit": 1.2}) == 1.2)
    ok("empty sheet -> 0 CM", svc.cm_unit({}) == 0.0)

    print("\n-- form input never becomes a silent 0 --")
    ok("_num('') is None", svc._num("") is None)
    ok("_num('abc') is None", svc._num("abc") is None)
    ok("_num(None) is None", svc._num(None) is None)
    ok("_num('0') is 0.0", svc._num("0") == 0.0)
    ok("_f('') falls back to 0.0", svc._f("") == 0.0)

    oid = orders[0]["id"]
    print("\n-- BOM writes + rejections (order %s) --" % orders[0]["order_no"])
    ok("blank item rejected", svc.add_bom_line(oid, {"item": "", "consumption": "1"}, None)
       == (False, "item_required"))
    ok("blank consumption rejected",
       svc.add_bom_line(oid, {"item": "X", "consumption": ""}, None) == (False, "bad_consumption"))
    ok("zero consumption rejected",
       svc.add_bom_line(oid, {"item": "X", "consumption": "0"}, None) == (False, "bad_consumption"))
    ok("garbage consumption rejected",
       svc.add_bom_line(oid, {"item": "X", "consumption": "two"}, None) == (False, "bad_consumption"))

    before = svc.cost_sheet(oid)
    added, _ = svc.add_bom_line(oid, {"item": "Interlining (unpriced)", "kind": "trim",
                                      "consumption": "0.05", "uom": "m",
                                      "allowance_pct": "2", "unit_price": ""}, None)
    after = svc.cost_sheet(oid)
    ok("unpriced line is added but counted as unpriced",
       added and after["material"]["unpriced"] == before["material"]["unpriced"] + 1)
    ok("unpriced line adds 0.00 to the estimate, it does not guess a price",
       after["material"]["total"] == before["material"]["total"])
    line = [l for l in after["bom"] if l["item"].startswith("Interlining")][0]
    ok("unpriced line still shows its required qty (12000*0.05*1.02)",
       line["required_qty"] == 612.0)

    okd, back = svc.update_bom_line(line["id"], {"item": "Interlining (unpriced)", "kind": "trim",
                                                 "consumption": "0.05", "uom": "m",
                                                 "allowance_pct": "2", "unit_price": "0.34"})
    priced = svc.cost_sheet(oid)
    ok("pricing the line moves material by exactly 612.0 x 0.34 = 208.08",
       okd and round(priced["material"]["total"] - before["material"]["total"], 2) == 208.08)
    ok("update rejects a zeroed consumption",
       svc.update_bom_line(line["id"], {"consumption": "0"}) == (False, "bad_consumption"))
    svc.update_bom_line(line["id"], {"action": "delete"})
    ok("delete restores the original material total",
       svc.cost_sheet(oid)["material"]["total"] == before["material"]["total"])

    print("\n-- the whole cost sheet adds up --")
    b = svc.cost_sheet(oid)
    lines_sum = round(sum(l["line_cost"] for l in b["bom"]), 2)
    ok("material total == sum of printed line costs", b["material"]["total"] == lines_sum)
    q = b["order"]["qty"]
    manual = round(b["material"]["total"]
                   + round(b["cm_per_unit"] * q, 2)
                   + round(b["sheet"]["overhead_per_unit"] * q, 2)
                   + round(b["sheet"]["freight_per_unit"] * q, 2)
                   + round(b["sheet"]["duty_per_unit"] * q, 2)
                   + round(b["sheet"]["other_per_unit"] * q, 2), 2)
    ok("estimate total == material + CM + OH + freight + duty + other (%.2f)" % manual,
       b["estimate"]["total"] == manual)
    ok("revenue == qty x unit price",
       b["margin"]["revenue"] == round(q * b["order"]["unit_price"], 2))
    ok("margin value == revenue - estimate",
       b["margin"]["est_value"] == round(b["margin"]["revenue"] - b["estimate"]["total"], 2))
    print("   %s: est %.2f  act %.2f  var %+.2f (%s%%)  margin %.1f%% -> %.1f%%" % (
        b["order"]["order_no"], b["estimate"]["total"], b["actual"]["total"],
        b["variance"]["total"]["value"], b["variance"]["total"]["pct"],
        b["margin"]["est_pct"], b["margin"]["act_pct"]))

    print("\n-- actuals: rejections, then the double-counting rule --")
    ok("bad category rejected",
       svc.add_actual(oid, {"category": "bribes", "amount": "10"}, None) == (False, "bad_category"))
    ok("blank amount rejected",
       svc.add_actual(oid, {"category": "cm", "amount": ""}, None) == (False, "bad_amount"))
    ok("negative amount rejected",
       svc.add_actual(oid, {"category": "cm", "amount": "-5"}, None) == (False, "bad_amount"))
    ok("garbage amount rejected",
       svc.add_actual(oid, {"category": "cm", "amount": "1O0"}, None) == (False, "bad_amount"))

    ok("with only manual entries the basis is 'manual'",
       svc.cost_sheet(oid)["material"]["actual_basis"] == "manual")
    manual_mat = svc.cost_sheet(oid)["actual"]["material"]

    # A procurement receipt for the same order must REPLACE the manual figure,
    # never be added to it — that is the double-count this module exists to avoid.
    pr = conn.execute(
        "INSERT INTO pr_requests (pr_no,title,requester,currency,status,total,is_active,created_at) "
        "VALUES (?,?,?,?,?,?,1,?)",
        ("PR-TEST-0001", "Fabric for SO", "tester", orders[0]["currency"], "received",
         5000.0, "2026-01-01 00:00:00"))
    pr_id = pr.lastrowid
    conn.execute("INSERT INTO pr_items (pr_id,seq,item,qty,unit_price,received_qty) "
                 "VALUES (?,1,'Jersey 180gsm',1000,5.0,900)", (pr_id,))
    conn.execute("UPDATE pr_requests SET source_module='costing', source_ref=? WHERE id=?",
                 (svc.order_ref(oid), pr_id))
    conn.commit()
    b2 = svc.cost_sheet(oid)
    ok("linked PR received value = 900 x 5.00 = 4500.00", b2["proc"]["received"] == 4500.0)
    ok("basis switches to 'procured'", b2["material"]["actual_basis"] == "procured")
    ok("material actual == receipts ONLY (no manual double-count)",
       b2["actual"]["material"] == 4500.0 and manual_mat != 4500.0)

    # A PR in another currency must not be added at an invented rate.
    pr2 = conn.execute(
        "INSERT INTO pr_requests (pr_no,title,requester,currency,status,total,is_active,"
        "source_module,source_ref,created_at) VALUES (?,?,?,?,?,?,1,?,?,?)",
        ("PR-TEST-0002", "Trims", "tester", "EGP", "received", 90000.0, "costing",
         svc.order_ref(oid), "2026-01-01 00:00:00"))
    conn.execute("INSERT INTO pr_items (pr_id,seq,item,qty,unit_price,received_qty) "
                 "VALUES (?,1,'Labels',10000,9.0,10000)", (pr2.lastrowid,))
    conn.commit()
    b3 = svc.cost_sheet(oid)
    ok("foreign-currency PR is listed but NOT summed",
       b3["proc"]["received"] == 4500.0 and "EGP" in b3["proc"]["mixed_ccy"]
       and len(b3["proc"]["prs"]) == 2)

    print("\n-- link helper --")
    ok("blank PR ref rejected", svc.link_pr_to_order(oid, "") == (False, "no_pr"))
    ok("unknown PR rejected", svc.link_pr_to_order(oid, "PR-NOPE")[1] == "pr_not_found")
    other = orders[1]["id"]
    ok("linking by PR number uses source_module/source_ref",
       svc.link_pr_to_order(other, "PR-TEST-0001", {"username": "tester"}) == (True, "linked"))
    ok("the PR now reports against the other order",
       svc.cost_sheet(other)["proc"]["received"] == 4500.0
       and svc.cost_sheet(oid)["proc"]["received"] == 0.0)
    svc.link_pr_to_order(oid, "PR-TEST-0001", {"username": "tester"})   # put it back

    print("\n-- cost-sheet writes --")
    ok("a letter in a cost figure is rejected, not silently zeroed",
       svc.save_sheet(oid, {"smv": "12.5", "cm_rate": "0.O75"}, None) == (False, "bad_number"))
    ok("negative cost figure rejected",
       svc.save_sheet(oid, {"overhead_per_unit": "-1"}, None) == (False, "negative"))
    ok("blank figures are accepted as zero",
       svc.save_sheet(oid, {"smv": "12.5", "cm_rate": "0.08", "overhead_per_unit": "",
                            "freight_per_unit": "0.11"}, {"username": "tester"})
       == (True, "saved"))
    b4 = svc.cost_sheet(oid)
    ok("saved CM per unit == 12.5 x 0.08 == 1.0", b4["cm_per_unit"] == 1.0)
    ok("blank overhead really is 0.00", b4["estimate"]["overhead"] == 0.0)
    ok("upsert did not create a second sheet row",
       conn.execute("SELECT COUNT(*) c FROM cst_sheets WHERE order_id=?", (oid,)).fetchone()["c"] == 1)

    print("\n-- an order with no plan at all --")
    empty = conn.execute(
        "INSERT INTO ord_orders (order_no,buyer,qty,unit_price,currency,status,created_at) "
        "VALUES ('SO-EMPTY','Nobody',0,0,'USD','draft','2026-01-01 00:00:00')").lastrowid
    conn.commit()
    e = svc.cost_sheet(empty)
    ok("zero qty / zero price -> no crash, per-unit and pct are None",
       e["estimate"]["total"] == 0.0 and e["estimate"]["per_unit"] is None
       and e["margin"]["est_pct"] is None and e["margin"]["act_pct"] is None)
    ok("unknown order returns None", svc.cost_sheet(999999) is None)

    print("\n-- dashboard + variance bell --")
    d = svc.dashboard()
    ok("dashboard counts only costed orders", d["orders_costed"] == len(
        conn.execute("SELECT DISTINCT order_id FROM cst_bom_lines").fetchall()))
    ok("at least one order is flagged over estimate", d["orders_unfavourable"] >= 1)
    ok("worst variance is an unfavourable order",
       d["worst"] is not None and d["worst"]["variance"]["total"]["pct"] > VARIANCE_ALERT_PCT)

    n_before = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='costing'").fetchone()["c"]
    svc.variance_sweep()
    n_after = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='costing'").fetchone()["c"]
    ok("sweep rings the bell for the overrun (%d new)" % (n_after - n_before), n_after > n_before)
    svc.variance_sweep()
    n_again = conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='costing'").fetchone()["c"]
    ok("sweep is idempotent — no duplicate alert", n_again == n_after)
    svc.save_sheet(orders[2]["id"], {"smv": "22", "cm_rate": "0.076"}, None)   # re-plan
    svc.variance_sweep()
    ok("changing the plan re-arms the alarm",
       conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='costing'").fetchone()["c"] > n_again)

    print("\n-- warehouse import is defensive --")
    ok("absent warehouse module degrades to None, never a crash",
       svc._wh_value(svc._wh_issued_fn(), oid) is None or isinstance(
           svc._wh_value(svc._wh_issued_fn(), oid), float))

    class Boom:
        def __call__(self, _):
            raise RuntimeError("warehouse exploded")
    ok("a warehouse that raises is swallowed", svc._wh_value(Boom(), oid) is None)
    ok("a warehouse returning a dict is understood",
       svc._wh_value(lambda _: {"value": 1234.5}, oid) == 1234.5)
    ok("a warehouse returning junk degrades to None", svc._wh_value(lambda _: "n/a", oid) is None)
    ok("warehouse issues WIN over procurement receipts (single source)",
       svc._material_actual(7000.0, 4500.0, 300.0) == (7000.0, "issued"))
    ok("no data anywhere -> 0.0 / 'none'", svc._material_actual(None, 0.0, 0.0) == (0.0, "none"))

    conn.close()

print("\n-- template parse --")
from jinja2 import Environment, FileSystemLoader        # noqa: E402

TPL = Path(__file__).resolve().parents[1] / "templates"
env = Environment(loader=FileSystemLoader(str(TPL)))
NAMES = ("costing/dashboard.html", "costing/orders.html", "costing/sheet.html")
for name in NAMES:
    try:
        env.parse((TPL / name).read_text(encoding="utf-8"), name=name)
        ok("parse " + name, True)
    except Exception as exc:
        ok("parse %s: %s" % (name, exc), False)

# Parsing is not rendering: register the blueprint on this throwaway app, log in
# and pull every page for real, so a missing context variable cannot ship as a 500.
print("\n-- live render --")
import re                                              # noqa: E402
from app.routes.costing import bp as costing_bp        # noqa: E402

app.register_blueprint(costing_bp)
cl = app.test_client()
tok = re.search(rb'name="_csrf" value="([^"]+)"', cl.get("/login").data).group(1).decode()
cl.post("/login", data={"username": "admin", "password": "Admin@12345", "_csrf": tok})
for url, want in (("/costing/", 200), ("/costing/orders", 200), ("/costing/orders?costed=1", 200),
                  ("/costing/order/%d" % oid, 200), ("/costing/order/%d" % empty, 200),
                  ("/costing/order/999999", 404)):
    got = cl.get(url).status_code
    ok("GET %s -> %d" % (url, got), got == want)

tok = re.search(rb'name="_csrf" value="([^"]+)"',
                cl.get("/costing/order/%d" % oid).data).group(1).decode()


def post(url, data):
    d = dict(data); d["_csrf"] = tok
    body = cl.post(url, data=d, follow_redirects=True).data.decode("utf-8", "replace")
    m = re.search(r'window\.TC_FLASH\s*=\s*(\[.*?\]);', body, re.S)
    return m.group(1) if m else ""


ok("POST bom add flashes success",
   '"success"' in post("/costing/order/%d/bom" % oid,
                       {"item": "Route trim", "kind": "trim", "consumption": "0.5",
                        "uom": "m", "allowance_pct": "2", "unit_price": "1.25"}))
ok("POST bom add with junk consumption flashes an error",
   '"error"' in post("/costing/order/%d/bom" % oid, {"item": "Bad", "consumption": "zero"}))
ok("POST sheet save flashes success",
   '"success"' in post("/costing/order/%d/sheet" % oid, {"smv": "12.5", "cm_rate": "0.075"}))
ok("POST sheet save with junk flashes an error",
   '"error"' in post("/costing/order/%d/sheet" % oid, {"smv": "twelve"}))
ok("POST actual add flashes success",
   '"success"' in post("/costing/order/%d/actual" % oid, {"category": "cm", "amount": "1000"}))
ok("POST link-pr with an unknown PR flashes an error",
   '"error"' in post("/costing/order/%d/link-pr" % oid, {"pr_ref": "PR-NOPE"}))

with app.app_context():
    from app.db import get_db
    c2 = get_db()
    lid = c2.execute("SELECT id FROM cst_bom_lines WHERE item='Route trim'").fetchone()["id"]
    aid = c2.execute("SELECT id FROM cst_actuals ORDER BY id DESC LIMIT 1").fetchone()["id"]
    c2.close()
ok("POST bom edit flashes success",
   '"success"' in post("/costing/bom/%d" % lid,
                       {"item": "Route trim", "kind": "trim", "consumption": "0.6",
                        "uom": "m", "allowance_pct": "2", "unit_price": "1.25"}))
ok("POST bom delete flashes success",
   '"success"' in post("/costing/bom/%d" % lid, {"action": "delete"}))
ok("POST actual delete flashes success",
   '"success"' in post("/costing/actual/%d/delete" % aid, {}))

print("\n%d/%d checks passed" % (sum(PASS), len(PASS)))
sys.exit(0 if all(PASS) else 1)
