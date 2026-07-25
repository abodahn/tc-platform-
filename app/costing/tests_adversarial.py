"""Adversarial self-test for the costing module. Isolated throwaway DB.

Run:  python app/costing/tests_adversarial.py
Every check is an assert; the script prints PASS lines and dies on the first
failure. It deliberately posts the things a browser's `type=number` never
sends: NaN, inf, negative allowances, ids that do not exist.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cst_adv_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                          # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                             # noqa: E402

OK = []


def ok(msg):
    OK.append(msg)
    print("  PASS  " + msg)


def head(msg):
    print("\n-- %s --" % msg)


app = create_app()
with app.app_context():
    from app.db import get_db
    from app.costing import services as svc
    from app.costing.schema import create_and_seed

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    # ---------------------------------------------------------------- idempotency
    head("create_and_seed run THREE times")

    def counts():
        return tuple(conn.execute("SELECT COUNT(*) AS c FROM " + t).fetchone()["c"]
                     for t in ("cst_bom_lines", "cst_sheets", "cst_actuals"))

    c1 = counts()
    create_and_seed(conn); conn.commit()
    create_and_seed(conn); conn.commit()
    c3 = counts()
    assert c1 == c3 and c1[0] > 0, (c1, c3)
    ok("3x create_and_seed keeps %d BOM lines / %d sheets / %d actuals" % c1)

    orders = conn.execute("SELECT id, order_no, qty, unit_price, currency FROM ord_orders "
                          "WHERE created_by='seed' ORDER BY id").fetchall()
    O1 = orders[0]["id"]
    print("   demo orders:", [(o["order_no"], o["qty"]) for o in orders])

    # ---------------------------------------------------------------- the formula
    head("required_qty = qty x consumption x (1 + allowance/100)")
    assert svc.required_qty(12000, 0.24, 3) == 2966.4
    ok("12000 x 0.24 x 1.03 == 2966.4")
    assert svc.required_qty(9000, 0.85, 8) == 8262.0
    ok("9000 x 0.85 x 1.08 == 8262.0")
    assert svc.required_qty(6500, 0.62, 0) == 4030.0
    ok("zero allowance is a pure multiply")
    assert svc.required_qty(12000, 0.0008 * 0, 5) == 0.0
    ok("zero consumption -> 0")
    # allowance INFLATES, never deflates
    assert svc.required_qty(100, 1, 10) > svc.required_qty(100, 1, 0)
    ok("a positive allowance increases the buy")

    head("hostile numbers")
    # WHY this matters — the pre-fix behaviour, reproduced:
    naive = float("nan")
    assert not (naive <= 0) and not (naive > 0)
    ok("PROOF: NaN passes BOTH '<= 0' and '> 0' guards, so a plain float() cast "
       "lets it straight into the money path")
    assert svc._num("nan") is None, svc._num("nan")
    ok("_num('nan') is None  (a NaN consumption would poison every total)")
    assert svc._num("inf") is None and svc._num("1e400") is None
    ok("_num('inf') / overflow literal is None")
    assert svc._num("") is None and svc._num("abc") is None and svc._num(None) is None
    ok("blank / garbage / None -> None, never 0.0")
    assert svc._num("0") == 0.0 and svc._f("") == 0.0
    ok("an explicit 0 is kept, a blank falls back only through _f")

    # ---------------------------------------------------------------- write guards
    head("BOM write guards")
    for bad, why in ((None, "blank"), ("0", "zero"), ("-2", "negative"),
                     ("abc", "garbage"), ("nan", "NaN"), ("inf", "infinite")):
        okk, msg = svc.add_bom_line(O1, {"item": "X", "consumption": bad}, None)
        assert not okk and msg == "bad_consumption", (bad, okk, msg)
    ok("consumption: blank/zero/negative/garbage/NaN/inf are all rejected")

    okk, msg = svc.add_bom_line(O1, {"item": "  ", "consumption": "1"}, None)
    assert not okk and msg == "item_required"
    ok("a blank item name is rejected")

    base = svc.cost_sheet(O1)["material"]["total"]
    assert svc.required_qty(12000, 0.24, -150) < 0
    ok("PROOF: an allowance of -150%% makes required_qty NEGATIVE (%.1f) — an "
       "unvalidated allowance turns a BOM line into a credit"
       % svc.required_qty(12000, 0.24, -150))
    okk, msg = svc.add_bom_line(O1, {"item": "Sabotage", "consumption": "1",
                                     "allowance_pct": "-150", "unit_price": "10"}, None)
    assert not okk and msg == "bad_allowance", (okk, msg)
    ok("allowance below -100%% is rejected (it would make the required qty NEGATIVE)")

    okk, msg = svc.add_bom_line(O1, {"item": "Sabotage", "consumption": "1",
                                     "unit_price": "-10"}, None)
    assert not okk and msg == "bad_price", (okk, msg)
    ok("a negative unit price is rejected (it would CREDIT the cost sheet)")

    okk, msg = svc.add_bom_line(O1, {"item": "Sabotage", "consumption": "1",
                                     "unit_price": "nan"}, None)
    assert not okk and msg == "bad_price", (okk, msg)
    ok("a NaN unit price is rejected")
    assert svc.cost_sheet(O1)["material"]["total"] == base
    ok("none of the rejected lines moved the material total (%.2f)" % base)

    # unpriced is allowed and visible
    okk, _ = svc.add_bom_line(O1, {"item": "Interlining", "consumption": "0.05",
                                   "uom": "m", "allowance_pct": "2"}, None)
    assert okk
    b = svc.cost_sheet(O1)
    line = [l for l in b["bom"] if l["item"] == "Interlining"][0]
    assert line["unpriced"] and line["line_cost"] == 0.0
    assert line["required_qty"] == round(O1_qty := float(orders[0]["qty"]) * 0.05 * 1.02, 4)
    assert b["material"]["total"] == base and b["material"]["unpriced"] >= 1
    ok("an unpriced line shows its required qty (%.4f) and adds 0.00, it never guesses"
       % O1_qty)

    # price it -> the total moves by exactly required_qty x price
    lid = line["id"]
    okk, res = svc.update_bom_line(lid, {"item": "Interlining", "consumption": "0.05",
                                         "allowance_pct": "2", "uom": "m", "unit_price": "0.34"})
    assert okk and res == O1
    assert svc.cost_sheet(O1)["material"]["total"] == round(base + round(O1_qty * 0.34, 2), 2)
    ok("pricing it moved material by exactly %.4f x 0.34" % O1_qty)

    okk, msg = svc.update_bom_line(lid, {"consumption": "nan"})
    assert not okk and msg == "bad_consumption"
    okk, msg = svc.update_bom_line(lid, {"consumption": "1", "allowance_pct": "-200"})
    assert not okk and msg == "bad_allowance"
    okk, msg = svc.update_bom_line(lid, {"consumption": "1", "unit_price": "-1"})
    assert not okk and msg == "bad_price"
    ok("the UPDATE path applies the same three guards as the INSERT path")

    okk, msg = svc.update_bom_line(10 ** 6, {"consumption": "1"})
    assert not okk and msg == "not_found"
    ok("editing a BOM line that does not exist is refused")

    okk, res = svc.update_bom_line(lid, {"action": "delete"})
    assert okk and svc.cost_sheet(O1)["material"]["total"] == base
    ok("delete restores the original material total (%.2f)" % base)

    # ---------------------------------------------------------------- foreign ids
    head("writes against an order that does not exist")
    GHOST = 987654
    for fn, args in ((svc.add_bom_line, (GHOST, {"item": "X", "consumption": "1"}, None)),
                     (svc.save_sheet, (GHOST, {"cm_per_unit": "1"}, None)),
                     (svc.add_actual, (GHOST, {"category": "cm", "amount": "5"}, None)),
                     (svc.link_pr_to_order, (GHOST, "PR-NOPE", None))):
        okk, msg = fn(*args)
        assert not okk and msg == "no_order", (fn.__name__, okk, msg)
    ok("BOM / sheet / actual / link-PR all refuse an unknown order id (no orphan rows)")
    assert conn.execute("SELECT COUNT(*) AS c FROM cst_bom_lines WHERE order_id=?",
                        (GHOST,)).fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM cst_sheets WHERE order_id=?",
                        (GHOST,)).fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM cst_actuals WHERE order_id=?",
                        (GHOST,)).fetchone()["c"] == 0
    ok("nothing was written for the ghost order")

    # ---------------------------------------------------------------- cost sheet
    head("cost-sheet figures")
    okk, msg = svc.save_sheet(O1, {"smv": "12.5", "cm_rate": "abc"}, {"username": "t"})
    assert not okk and msg == "bad_number"
    okk, msg = svc.save_sheet(O1, {"smv": "12.5", "cm_rate": "nan"}, {"username": "t"})
    assert not okk and msg == "bad_number"
    okk, msg = svc.save_sheet(O1, {"overhead_per_unit": "-1"}, {"username": "t"})
    assert not okk and msg == "negative"
    ok("a letter / NaN / negative in a cost figure is rejected, never silently 0")

    assert svc.cm_unit({"smv": 12.5, "cm_rate": 0.075, "cm_per_unit": 9}) == 0.9375
    ok("SMV x rate wins when both are set (12.5 x 0.075 = 0.9375)")
    assert svc.cm_unit({"cm_per_unit": 1.1}) == 1.1 and svc.cm_unit({}) == 0.0
    ok("flat CM is used when SMV/rate are absent; an empty sheet is 0")

    # ---------------------------------------------------------------- arithmetic
    head("the sheet adds up, by hand")
    b = svc.cost_sheet(O1)
    qty = float(b["order"]["qty"])
    assert b["material"]["total"] == round(sum(l["line_cost"] for l in b["bom"]), 2)
    ok("material total == sum of the PRINTED line costs")
    manual = (round(svc.cm_unit(b["sheet"]) * qty, 2)
              + round(svc._f(b["sheet"]["overhead_per_unit"]) * qty, 2)
              + round(svc._f(b["sheet"]["freight_per_unit"]) * qty, 2)
              + round(svc._f(b["sheet"]["duty_per_unit"]) * qty, 2)
              + round(svc._f(b["sheet"]["other_per_unit"]) * qty, 2))
    assert b["estimate"]["total"] == round(b["material"]["total"] + manual, 2)
    ok("estimate total == material + CM + OH + freight + duty + other (%.2f)"
       % b["estimate"]["total"])
    assert b["margin"]["revenue"] == round(qty * float(b["order"]["unit_price"]), 2)
    assert b["margin"]["est_value"] == round(b["margin"]["revenue"] - b["estimate"]["total"], 2)
    assert b["margin"]["est_pct"] == round(b["margin"]["est_value"] / b["margin"]["revenue"] * 100, 2)
    ok("margin pct denominator is REVENUE, not cost (%.2f%%)" % b["margin"]["est_pct"])

    head("variance sign convention")
    v = svc._variance(100.0, 110.0)
    assert v["value"] == 10.0 and v["pct"] == 10.0 and v["flag"] == "unfavourable"
    ok("actual > estimate -> POSITIVE value, 'unfavourable' (red in the UI)")
    v = svc._variance(100.0, 90.0)
    assert v["value"] == -10.0 and v["flag"] == "favourable"
    ok("actual < estimate -> negative, 'favourable' (green)")
    assert svc._variance(100.0, 100.004)["flag"] == "on_plan"
    ok("float noise inside the deadband stays 'on_plan'")
    z = svc._variance(0.0, 500.0)
    assert z["pct"] is None and z["flag"] == "unfavourable"
    ok("zero estimate -> pct None (no ZeroDivisionError), flag still unfavourable")
    assert svc.margin(0, 100) == (-100.0, None) and svc.margin(None, 5)[1] is None
    ok("zero/None revenue -> margin pct None, never a fake 100%")

    # ---------------------------------------------------------------- double count
    head("DOUBLE-COUNTING GUARD")
    conn.execute("DELETE FROM cst_actuals WHERE order_id=?", (O1,))
    conn.commit()
    svc.add_actual(O1, {"category": "material", "amount": "1000", "source": "cash buy"}, None)
    b = svc.cost_sheet(O1)
    assert b["actual"]["material"] == 1000.0 and b["material"]["actual_basis"] == "manual"
    ok("with only a manual entry the basis is 'manual' (1000.00)")

    # a linked PR that has really been received
    now = "2026-01-01 00:00:00"
    conn.execute("INSERT INTO pr_requests (pr_no,title,currency,total,status,is_active,"
                 "source_module,source_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("PR-CST-1", "Fabric", b["order"]["currency"], 4500, "received", 1,
                  "costing", svc.order_ref(O1), now))
    pr = conn.execute("SELECT id FROM pr_requests WHERE pr_no='PR-CST-1'").fetchone()["id"]
    conn.execute("INSERT INTO pr_items (pr_id,seq,item,qty,received_qty,unit_price) "
                 "VALUES (?,?,?,?,?,?)", (pr, 1, "Jersey", 900, 900, 5.0))
    conn.commit()
    b = svc.cost_sheet(O1)
    assert b["proc"]["received"] == 4500.0
    assert b["actual"]["material"] == 4500.0 and b["material"]["actual_basis"] == "procured"
    ok("PR receipts (900 x 5.00 = 4500) REPLACE the manual 1000 — not 5500")

    # now pretend the warehouse issued the same fabric: still ONE number
    real_fn = svc._wh_issued_fn
    svc._wh_issued_fn = lambda: (lambda oid: 4300.0)
    try:
        b = svc.cost_sheet(O1)
        assert b["actual"]["material"] == 4300.0 and b["material"]["actual_basis"] == "issued"
        ok("warehouse issues (4300) win over receipts+manual — the total is 4300, not 9800")
        svc._wh_issued_fn = lambda: (lambda oid: (_ for _ in ()).throw(RuntimeError("boom")))
        b = svc.cost_sheet(O1)
        assert b["issued"] is None and b["actual"]["material"] == 4500.0
        ok("a warehouse module that RAISES degrades to 'no issue data', it never 500s")
    finally:
        svc._wh_issued_fn = real_fn

    # foreign currency PR: listed, flagged, NOT summed
    conn.execute("INSERT INTO pr_requests (pr_no,title,currency,total,status,is_active,"
                 "source_module,source_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("PR-CST-2", "Trims", "EGP", 9999, "received", 1, "costing",
                  svc.order_ref(O1), now))
    pr2 = conn.execute("SELECT id FROM pr_requests WHERE pr_no='PR-CST-2'").fetchone()["id"]
    conn.execute("INSERT INTO pr_items (pr_id,seq,item,qty,received_qty,unit_price) "
                 "VALUES (?,?,?,?,?,?)", (pr2, 1, "Thread", 100, 100, 99.99))
    conn.commit()
    b = svc.cost_sheet(O1)
    assert b["proc"]["received"] == 4500.0, b["proc"]["received"]
    assert "EGP" in b["proc"]["mixed_ccy"] and len(b["proc"]["prs"]) == 2
    ok("a foreign-currency PR is listed and flagged but NOT converted at an invented rate")

    head("actual-cost write guards")
    for data, why in (({"category": "banana", "amount": "5"}, "category"),
                      ({"category": "cm", "amount": ""}, "blank"),
                      ({"category": "cm", "amount": "-5"}, "negative"),
                      ({"category": "cm", "amount": "abc"}, "garbage"),
                      ({"category": "cm", "amount": "nan"}, "NaN")):
        okk, _m = svc.add_actual(O1, data, None)
        assert not okk, (data, _m)
    ok("bad category / blank / negative / garbage / NaN amounts are all rejected")
    okk, msg = svc.delete_actual(10 ** 6)
    assert not okk and msg == "not_found"
    ok("deleting an actual that does not exist is refused")

    head("link a PR")
    okk, msg = svc.link_pr_to_order(O1, "   ", None)
    assert not okk and msg == "no_pr"
    okk, msg = svc.link_pr_to_order(O1, "PR-DOES-NOT-EXIST", None)
    assert not okk and msg == "pr_not_found"
    ok("a blank or unknown PR reference is refused")

    # a PR that maintenance already owns must not be silently stolen: link_source()
    # is a blind UPDATE of the ONE source_module/source_ref pair.
    conn.execute("INSERT INTO pr_requests (pr_no,title,currency,total,status,is_active,"
                 "source_module,source_ref,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 ("PR-MAINT-9", "Bearings", "EGP", 100, "approved", 1,
                  "maintenance", "spare:7", now))
    conn.commit()
    okk, msg = svc.link_pr_to_order(O1, "PR-MAINT-9", None)
    assert not okk and msg == "pr_owned", (okk, msg)
    keep = conn.execute("SELECT source_module, source_ref FROM pr_requests "
                        "WHERE pr_no='PR-MAINT-9'").fetchone()
    assert keep["source_module"] == "maintenance" and keep["source_ref"] == "spare:7"
    ok("a PR owned by maintenance keeps its link — costing refuses instead of overwriting it")

    O2 = orders[1]["id"]
    okk, msg = svc.link_pr_to_order(O2, "PR-CST-1", None)
    assert okk, msg
    moved = conn.execute("SELECT source_ref FROM pr_requests WHERE pr_no='PR-CST-1'").fetchone()
    assert moved["source_ref"] == svc.order_ref(O2)
    assert svc.cost_sheet(O1)["proc"]["received"] == 0.0
    ok("moving a PR between two COSTING orders is still allowed and moves the money with it")

    # ---------------------------------------------------------------- the bell
    head("variance sweep")
    conn.execute("DELETE FROM notifications WHERE module='costing'")
    conn.execute("UPDATE cst_sheets SET variance_alerted=0")
    conn.commit()
    svc.variance_sweep()
    n1 = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='costing'").fetchone()["c"]
    svc.variance_sweep()
    n2 = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='costing'").fetchone()["c"]
    assert n1 >= 1 and n1 == n2, (n1, n2)
    ok("the bell rings %d time(s) and a second sweep adds nothing" % n1)
    svc.save_sheet(O1, {"smv": "12.5", "cm_rate": "0.075"}, {"username": "t"})
    assert conn.execute("SELECT variance_alerted AS a FROM cst_sheets WHERE order_id=?",
                        (O1,)).fetchone()["a"] == 0
    ok("a PLAN change re-arms the alarm")
    svc.add_actual(O1, {"category": "cm", "amount": "1"}, None)
    conn2 = get_db()
    armed = conn2.execute("SELECT variance_alerted AS a FROM cst_sheets WHERE order_id=?",
                          (O1,)).fetchone()["a"]
    conn2.close()
    ok("booking an actual leaves the flag alone (%s) so it does not ring per entry" % armed)

    # ---------------------------------------------------------------- dashboard
    head("dashboard + list never raise on the seeded set")
    d = svc.dashboard()
    assert d["orders_costed"] >= 1 and isinstance(d["est_total"], float)
    assert d["act_total"] == round(sum(r["actual"]["total"] for r in d["rows"]
                                       if r["has_actual"]), 2)
    ok("dashboard KPIs computed (costed=%d over=%d est=%.2f act=%.2f)"
       % (d["orders_costed"], d["orders_unfavourable"], d["est_total"], d["act_total"]))
    assert len(svc.list_costed(only_costed=True)) <= len(svc.list_costed())
    ok("?costed=1 is a subset of the full order list")

    # an order with qty 0 must not divide by zero anywhere
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,unit_price,currency,created_by) "
                 "VALUES ('SO-ZERO','Nobody',0,0,'USD','test')")
    conn.commit()
    z = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-ZERO'").fetchone()["id"]
    svc.add_bom_line(z, {"item": "Fabric", "consumption": "0.2", "unit_price": "5"}, None)
    svc.save_sheet(z, {"cm_per_unit": "1"}, {"username": "t"})
    bz = svc.cost_sheet(z)
    assert bz["estimate"]["per_unit"] is None and bz["margin"]["est_pct"] is None
    assert bz["estimate"]["total"] == 0.0
    ok("a zero-qty order gives per_unit/margin None and never divides by zero")
    svc.dashboard()
    ok("the dashboard survives the zero-qty order")

    # ------------------------------------------------- the seed, after real use
    head("re-seed after a user has emptied the BOM")
    before = counts()
    conn.execute("DELETE FROM cst_bom_lines")
    conn.commit()
    demo_sheet = conn.execute("SELECT order_id FROM cst_sheets WHERE updated_by='seed'").fetchone()
    try:
        conn.execute("INSERT INTO cst_sheets (order_id,smv,created_at) VALUES (?,?,?)",
                     (demo_sheet["order_id"], 1, "x"))
        conn.commit()
        raise AssertionError("expected UNIQUE(order_id) to reject the duplicate sheet")
    except AssertionError:
        raise
    except Exception:
        conn.rollback()
    ok("PROOF: re-inserting a demo cost sheet violates UNIQUE(order_id) — gating the "
       "seed on cst_bom_lines alone would raise inside init_db on every boot")
    create_and_seed(conn)
    conn.commit()
    after = counts()
    assert after[1] == before[1] and after[2] == before[2] and after[0] == 0, (before, after)
    ok("the seed now stands down unless ALL THREE tables are empty (%d/%d/%d)" % after)

    conn.close()

# ---------------------------------------------------------------- routes
head("routes: auth, method and rendering, through a real client")
from app.routes.costing import bp as costing_bp        # noqa: E402

app.register_blueprint(costing_bp)
client = app.test_client()

GETS = ["/costing/", "/costing/orders", "/costing/order/1"]
POSTS = ["/costing/order/1/bom", "/costing/bom/1", "/costing/order/1/sheet",
         "/costing/order/1/actual", "/costing/actual/1/delete", "/costing/order/1/link-pr"]
for u in GETS:
    r = client.get(u)
    assert r.status_code == 302 and "/login" in r.headers.get("Location", ""), (u, r.status_code)
ok("every GET route bounces an anonymous caller to the login page")
for u in POSTS:
    assert client.post(u).status_code == 400, u          # platform CSRF, before auth
ok("every POST route is rejected without a CSRF token")
client.get("/costing/")                                  # mint a session token
with client.session_transaction() as s:
    TOK = s["_csrf_token"]
for u in POSTS:
    r = client.post(u, data={"_csrf": TOK})
    assert r.status_code == 302 and "/login" in r.headers.get("Location", ""), (u, r.status_code)
ok("with a valid CSRF token but no session, every POST still bounces to login")
for u in POSTS:
    assert client.get(u).status_code == 405, u
ok("every state-changing route is POST-only (GET -> 405), so nothing mutates from a link")

with app.app_context():
    conn = get_db()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active) "
                 "VALUES ('cstadm','x','Cost Admin','super_admin',1)")
    conn.commit()
    u = conn.execute("SELECT id, session_epoch FROM users WHERE username='cstadm'").fetchone()
    uid, ep = u["id"], (u["session_epoch"] or 0)
    seeded = conn.execute("SELECT id FROM ord_orders WHERE created_by='seed' "
                          "ORDER BY id").fetchall()
    oid, oid2 = seeded[0]["id"], seeded[1]["id"]
    conn.close()
with client.session_transaction() as s:
    s["uid"], s["ep"] = uid, ep
    TOK = s["_csrf_token"]
for u in ["/costing/", "/costing/orders", "/costing/orders?costed=1", "/costing/order/%d" % oid]:
    r = client.get(u)
    assert r.status_code == 200, (u, r.status_code)
    assert b"cost" in r.data.lower()
ok("dashboard, order list and cost sheet all render 200 for an authorised user")
assert client.get("/costing/order/999999").status_code == 404
ok("an unknown order id 404s instead of rendering an empty sheet")

# the order that holds the linked PR: its manual material entry is NOT in the
# total, so the row has to say so.
with app.app_context():
    from app.costing import services as svc2
    svc2.add_actual(oid2, {"category": "material", "amount": "777", "source": "cash"}, None)
    assert svc2.cost_sheet(oid2)["material"]["actual_basis"] == "procured"
    assert svc2.cost_sheet(oid2)["actual"]["material"] == 4500.0
html = client.get("/costing/order/%d" % oid2).data.decode("utf-8")
assert "777.00" in html and "cst.badge.not_counted" in html
ok("a manual material entry the priority rule drops is rendered with a 'not counted' badge, "
   "not as an anonymous number missing from the total")

with app.app_context():
    conn = get_db()
    conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,unit_price,currency,created_by) "
                 "VALUES ('SO-NULL',NULL,NULL,NULL,NULL,'test')")
    conn.commit()
    nid = conn.execute("SELECT id FROM ord_orders WHERE order_no='SO-NULL'").fetchone()["id"]
    conn.close()
assert client.get("/costing/order/%d" % nid).status_code == 200
ok("an order with NULL qty / price / currency still renders (no crash on a half-typed order)")
r = client.post("/costing/order/999999/bom",
                data={"_csrf": TOK, "item": "X", "consumption": "1"})
assert r.status_code == 404
ok("POSTing a BOM line to an unknown order 404s (no orphan row, no silent success)")
r = client.post("/costing/order/%d/bom" % oid,
                data={"_csrf": TOK, "item": "Zip", "consumption": "1", "unit_price": "-5"})
assert r.status_code == 302
with app.app_context():
    conn = get_db()
    n = conn.execute("SELECT COUNT(*) AS c FROM cst_bom_lines WHERE item='Zip'").fetchone()["c"]
    conn.close()
assert n == 0
ok("a negative price POSTed past the browser's min=0 is refused end to end")

# ---------------------------------------------------------------- templates
head("templates parse + i18n keys")
import re                                              # noqa: E402
from jinja2 import Environment, FileSystemLoader       # noqa: E402

ROOT = Path(r"D:\TC platform\tc-platform-render")
env = Environment(loader=FileSystemLoader(str(ROOT / "app" / "templates")))
tpl_dir = ROOT / "app" / "templates" / "costing"
keys = set()
for p in sorted(tpl_dir.glob("*.html")):
    src = p.read_text(encoding="utf-8")
    env.parse(src)
    print("   parsed %s" % p.name)
    for k in re.findall(r'data-i18n="([^"]+)"', src):
        keys.add(k)
ok("all %d costing templates parse" % len(list(tpl_dir.glob('*.html'))))

# app.js does `el.textContent = t(key)` — so a data-i18n element that CONTAINS
# markup has that markup destroyed on every page load.
bad = []
for p in sorted(tpl_dir.glob("*.html")):
    for m in re.finditer(r"<(\w+)[^>]*\bdata-i18n=\"[^\"]+\"[^>]*>(.*?)</\1>",
                         p.read_text(encoding="utf-8"), re.S):
        if "<" in m.group(2):
            bad.append((p.name, m.group(0)[:70]))
assert not bad, "data-i18n element with child markup (app.js would wipe it): %r" % bad
ok("no data-i18n element wraps child markup (app.js sets textContent)")

from app.costing.constants import I18N                 # noqa: E402
dyn = {k for k in keys if "{{" in k}
static_keys = keys - dyn
missing = sorted(static_keys - set(I18N))
assert not missing, "keys that would render RAW on screen: %r" % missing
ok("all %d static data-i18n keys are in the i18n map" % len(static_keys))
for pre, vals in (("cst.cat.", ["material", "cm", "overhead", "freight", "duty", "other"]),
                  ("cst.kind.", ["fabric", "trim", "other"]),
                  ("cst.basis.", ["issued", "procured", "manual", "none"])):
    for v in vals:
        assert pre + v in I18N, pre + v
ok("every dynamic key (cst.cat.* / cst.kind.* / cst.basis.*) is covered for all enum values")
for k, v in I18N.items():
    assert len(v) == 3 and all(str(x).strip() for x in v), k
    assert v[1] != v[0] and v[2] != v[0], "untranslated: %s" % k
    assert re.search(r"[؀-ۿ]", v[1]), "Arabic value is not Arabic: %s" % k
ok("all %d i18n entries have real en/ar/tr text" % len(I18N))

print("\n%d checks passed." % len(OK))
