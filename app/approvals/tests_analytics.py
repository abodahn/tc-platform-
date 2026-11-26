"""Procurement analytics workspace: one spend definition everywhere (VAT, FX,
budget released, PO split, cancelled POs), whitelisted builder, money gating,
saved reports (owner / share / pin), list drill-down filters, EN/AR/TR keys.

    python -m app.approvals.tests_analytics
"""
import io
import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _app():
    sys.path.insert(0, str(ROOT))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "analytics.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _oracle(conn, svc):
    """The pre-workspace Python algorithm (services._committed_rows), kept here
    as the reference the SQL must keep agreeing with."""
    prs = [dict(r) for r in conn.execute(
        "SELECT * FROM pr_requests WHERE is_active=1 AND status IN ("
        + ",".join("?" for _ in svc._SPEND_STATUSES) + ")", svc._SPEND_STATUSES).fetchall()]
    orders = {}
    for row in conn.execute("SELECT pr_id, vendor, grand FROM pr_purchase_orders "
                            "WHERE COALESCE(status,'issued')<>'cancelled'").fetchall():
        orders.setdefault(row["pr_id"], []).append(dict(row))
    out = {}
    for pr in prs:
        reduced = dict(pr, total=float(pr.get("total") or 0) - float(pr.get("budget_released") or 0))
        value = svc.egp_commitment(reduced)
        pos = orders.get(pr["id"], [])
        grand = sum(float(po["grand"] or 0) for po in pos)
        if pos:
            for po in pos:
                v = (po["vendor"] or "").strip() or "(unassigned)"
                out[v] = out.get(v, 0.0) + (value * float(po["grand"] or 0) / grand if grand else value / len(pos))
        else:
            v = (pr.get("vendor") or "").strip() or "(unassigned)"
            out[v] = out.get(v, 0.0) + value
    return out


def run():
    app = _app()
    from flask import g
    app.before_request(lambda: g.pop("user", None) and None)
    ok = [True]

    def chk(label, cond, extra=""):
        ok[0] &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))

    def near(a, b):
        return a is not None and abs(float(a) - float(b)) < 0.01

    with app.app_context():
        from app.approvals import analytics as an
        from app.approvals import services as svc
        from app.db import get_db

        c = get_db()
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        ids = {}
        for un, role in (("an.staff", "normal_user"),):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, un, role))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        cols = ("pr_no,title,department,vendor,status,total,tax_rate,currency,fx_rate,budget_released,"
                "request_date,created_at,submitted_at,approved_at,is_active,req_del_date,received_at,"
                "cost_center,expenditure_kind")
        ph = ",".join("?" for _ in cols.split(","))
        prs = (
            # committed 1000 net + 14% VAT = 1140, one PO to Fake Supplier A
            ("AN-1", "t1", "IT", "Fake Supplier A", "received", 1000, 14, "EGP", 1, 0, "2026-10-05",
             "2026-10-05 08:00:00", "2026-10-05 08:00:00", "2026-10-07 08:00:00", 1, "2026-10-20",
             "2026-10-19 10:00:00", "CC-10", "opex"),
            # USD 100 - 20 released, fx 50 = 4000, split 3:1 over two live POs (a third is cancelled)
            ("AN-2", "t2", "Production", "Fake Supplier A", "po_issued", 100, 0, "USD", 50, 20, "2026-09-10",
             "2026-09-10 08:00:00", "2026-09-10 08:00:00", "2026-09-12 08:00:00", 1, "2026-09-30",
             None, "CC-20", "capex"),
            # not committed: never counted as spend
            ("AN-3", "t3", "IT", "Fake Supplier C", "pending", 5000, 14, "EGP", 1, 0, "2026-10-01",
             "2026-10-01 08:00:00", "2026-10-01 08:00:00", None, 1, None, None, "CC-10", "opex"),
            # approved, no PO, no supplier: (unassigned)
            ("AN-4", "t4", "Warehouse", "", "approved", 200, 0, "EGP", 1, 0, "2026-08-01",
             "2026-08-01 08:00:00", "2026-08-01 08:00:00", "2026-08-02 08:00:00", 1, None, None, "", "opex"),
        )
        pid = {}
        for row in prs:
            c.execute("INSERT INTO pr_requests (%s) VALUES (%s)" % (cols, ph), row)
            pid[row[0]] = c.execute("SELECT id FROM pr_requests WHERE pr_no=?", (row[0],)).fetchone()["id"]
        for code, name, cat in (("FAKE-1", "Fake Widget", "Fake Cat One"), ("FAKE-2", "Fake Gadget", "Fake Cat Two")):
            c.execute("INSERT INTO proc_items (code, name, category_name) VALUES (?, ?, ?)", (code, name, cat))
        it = {r["code"]: r["id"] for r in c.execute("SELECT id, code FROM proc_items WHERE code LIKE 'FAKE-%'")}
        c.execute("INSERT INTO pr_items (pr_id, item, qty, unit_price, vendor) VALUES (?, 'Toner', 2, 500, 'Fake Supplier A')",
                  (pid["AN-1"],))
        c.execute("INSERT INTO pr_items (pr_id, item, item_id, qty, unit_price) VALUES (?, 'Fake Widget', ?, 1, 60)",
                  (pid["AN-2"], it["FAKE-1"]))
        c.execute("INSERT INTO pr_items (pr_id, item, item_id, qty, unit_price) VALUES (?, 'Fake Gadget', ?, 1, 20)",
                  (pid["AN-2"], it["FAKE-2"]))
        for p, po, v, grand, st in (("AN-1", "PO-AN-1", "Fake Supplier A", 1140, "issued"),
                                    ("AN-2", "PO-AN-2", "Fake Supplier A", 300, "issued"),
                                    ("AN-2", "PO-AN-3", "Fake Supplier B", 100, "issued"),
                                    ("AN-2", "PO-AN-4", "Fake Supplier D", 999, "cancelled")):
            c.execute("INSERT INTO pr_purchase_orders (pr_id, po_no, vendor, grand, status, issued_at) "
                      "VALUES (?, ?, ?, ?, ?, '2026-10-08 09:00:00')", (pid[p], po, v, grand, st))
        for v, amt, chosen in (("Fake Supplier A", 1000, 1), ("Fake Supplier B", 1300, 0)):
            c.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, is_chosen) VALUES (?, ?, ?, 'EGP', ?)",
                      (pid["AN-1"], v, amt, chosen))
        c.commit()

        print("one spend definition")
        chk("COMMITTED is services._SPEND_STATUSES", tuple(an.COMMITTED) == tuple(svc._SPEND_STATUSES))
        rows = {r[0]: r[2] for r in an.spend_rows("supplier")}
        want = {"Fake Supplier A": 1140 + 3000, "Fake Supplier B": 1000, "(unassigned)": 200}
        chk("by supplier: VAT, FX, released budget, PO split, cancelled PO ignored",
            set(rows) == set(want) and all(near(rows[k], v) for k, v in want.items()), rows)
        oracle = _oracle(c, svc)
        chk("the SQL agrees with the old Python algorithm",
            set(oracle) == set(rows) and all(near(rows[k], v) for k, v in oracle.items()), oracle)
        _h, exp = svc.export_dataset("spend_vendor")
        chk("the CSV export serves the same rows", [list(r) for r in exp] == an.spend_rows("supplier"), str(exp)[:120])
        summ = svc.analytics_summary()
        chk("analytics_summary committed spend = the same total", near(summ.get("committed_spend"), 5340),
            summ.get("committed_spend"))
        f = an.parse_filters({"period": "all"})
        k = an.kpis(f)
        chk("KPI spend = 5340, 3 committed requests", near(k["spend"], 5340) and k["requests"] >= 3, k)
        chk("savings = highest - chosen quote", near(k["savings"], 300), k["savings"])
        cfg, _ = an.parse_config({"d1": "category", "m": "spend"})
        cat = {r[0]: r[2] for r in an.query(cfg, f)}
        chk("category allocation splits a request by line weight",
            near(cat.get("Fake Cat One"), 3000) and near(cat.get("Fake Cat Two"), 1000), cat)
        chk("category allocation adds up to the total", near(sum(v or 0 for v in cat.values()), 5340), cat)
        fc = an.parse_filters({"period": "all", "cat": "Fake Cat Two"})
        chk("a category filter narrows spend to the category share", near(an.kpis(fc)["spend"], 1000), an.kpis(fc)["spend"])
        fv = an.parse_filters({"period": "all", "vendor": "Fake Supplier B"})
        chk("a supplier filter narrows spend to the supplier share", near(an.kpis(fv)["spend"], 1000), an.kpis(fv)["spend"])
        for d in an.DIMS:
            for m in an.MEASURES:
                cfg, err = an.parse_config({"d1": d, "m": m})
                if not err:
                    an.shape(cfg, an.query(cfg, f))
        chk("every allowed dimension x measure runs", True)

        print("\nwhitelist")
        chk("an unknown dimension is refused", an.parse_config({"d1": "p.total; DROP", "m": "spend"})[1] == "bad_dim")
        chk("an unknown measure is refused", an.parse_config({"d1": "supplier", "m": "x"})[1] == "bad_measure")
        chk("money measures refused without money", an.parse_config({"d1": "supplier", "m": "spend"}, money=False)[1] == "money_hidden")
        chk("per-request measures cannot split by item", an.parse_config({"d1": "item", "m": "cycle"})[1] == "needs_request_dim")
        cfg, _ = an.parse_config({"d1": "supplier", "m": "spend", "top": "1"})
        sh = an.shape(cfg, an.query(cfg, f))
        chk("top-N keeps the leader and folds the rest into Others",
            sh["keys"] == ["Fake Supplier A", None] and near(sh["data"][0][1], 1200), sh)
        cfg, _ = an.parse_config({"d1": "supplier", "m": "spend", "top": "9999"})
        chk("top-N is capped", cfg["top"] <= an.MAX_TOP, cfg["top"])

        print("\nlist drill-down filters")
        def nos(**kw):
            return {r["pr_no"] for r in svc.list_prs(**kw)}
        chk("vendor matches a live PO (the spend rule)", nos(vendor="Fake Supplier B") == {"AN-2"}, nos(vendor="Fake Supplier B"))
        chk("category matches a line", nos(cat="Fake Cat One") == {"AN-2"}, nos(cat="Fake Cat One"))
        chk("cost centre", nos(cc="CC-10") == {"AN-1", "AN-3"}, nos(cc="CC-10"))
        chk("OPEX / CAPEX", nos(kind="capex") == {"AN-2"}, nos(kind="capex"))
        chk("committed=1 drops pending / draft requests", "AN-3" not in nos(committed="1")
            and {"AN-1", "AN-2", "AN-4"} <= nos(committed="1"), nos(committed="1"))
        chk("committed=open = approved but not received", nos(committed="open") >= {"AN-2", "AN-4"}
            and "AN-1" not in nos(committed="open"), nos(committed="open"))
        chk("cur filter", nos(cur="USD") == {"AN-2"}, nos(cur="USD"))

        print("\nreview fixes")
        # one statement per page piece, S built once: same numbers either way
        inline_k = an.kpis(f, conn=c)
        cfgs = [an.parse_config({"d1": d, "m": "spend"})[0] for d in ("supplier", "category", "month")]
        cfgs.append(an.parse_config({"d1": "category", "m": "requests"})[0])
        inline_q = [an.query(x, f, conn=c) for x in cfgs]
        with an.facts(f, c):
            mat_k = an.kpis(f, conn=c)
            mat_q = an.query_many(cfgs, f, conn=c)
            used = an._with("S", f, an.date_range(f), date.today().isoformat(), c)[0]
        chk("facts(): KPIs identical to the inline build", mat_k == inline_k, (mat_k, inline_k))
        chk("query_many == query one by one", [sorted(x) for x in mat_q] == [sorted(x) for x in inline_q])
        chk("inside facts() S is read from the temp table", an._MAT_TABLE in used, used[:80])
        chk("outside facts() S is inline again", an._MAT_TABLE not in an._with("S", f, ("", ""), "x", c)[0])
        chk("the temp table is dropped afterwards",
            not c.execute("SELECT name FROM sqlite_temp_master WHERE name=?", (an._MAT_TABLE,)).fetchall())

        # partial quotes do not compete; full ones do
        c.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, is_chosen) VALUES (?, 'Fake Supplier A', 120, 'EGP', 1)",
                  (pid["AN-2"],))
        q_part = c.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, is_chosen) VALUES (?, 'Fake Supplier B', 20, 'EGP', 0)",
                           (pid["AN-2"],)).lastrowid
        q_full = c.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, is_chosen) VALUES (?, 'Fake Supplier D', 150, 'EGP', 0)",
                           (pid["AN-2"],)).lastrowid
        lines2 = [x["id"] for x in c.execute("SELECT id FROM pr_items WHERE pr_id=? ORDER BY id", (pid["AN-2"],))]
        c.execute("INSERT INTO pr_quote_lines (quote_id, pr_item_id, unit_price, qty, line_total) VALUES (?, ?, 20, 1, 20)",
                  (q_part, lines2[0]))
        for li, amt in zip(lines2, (100, 50)):
            c.execute("INSERT INTO pr_quote_lines (quote_id, pr_item_id, unit_price, qty, line_total) VALUES (?, ?, ?, 1, ?)",
                      (q_full, li, amt, amt))
        c.commit()
        k2 = an.kpis(f)
        chk("a one-line partial quote is not the 'lowest'", near(k2["missed"], 0), k2["missed"])
        chk("savings vs highest counts the full competing quote", near(k2["savings"], 300 + 30), k2["savings"])
        chk("savings vs first quote is computed", near(k2["vs_first"], 0) and near(k2["quotes"]["firstq"], 1120), k2)
        cfg, _ = an.parse_config({"d1": "supplier", "m": "savings"})
        chk("builder savings = first quote - chosen", near(sum(v or 0 for _a, _b, v, _n in an.query(cfg, f)), 0))

        # a hand-typed request date falls back to created_at (PostgreSQL-safe CASTs)
        extra_cols = ("AN-5", "t5", " IT ", "Fake Supplier A", "approved", 10, 0, "EGP", 1, 0, "5/11/2026",
                      "2026-06-03 08:00:00", "2026-06-03 08:00:00", "2026-06-04 08:00:00", 1, None, None, "", "opex")
        c.execute("INSERT INTO pr_requests (%s) VALUES (%s)" % (cols, ph), extra_cols)
        pid["AN-5"] = c.execute("SELECT id FROM pr_requests WHERE pr_no='AN-5'").fetchone()["id"]
        # AN-6: three small POs to one supplier; AN-7: no PO; AN-8: one PO whose
        # vendor differs from the header and the line.
        for row in (("AN-6", "t6", "QA", "Fake Supplier E", "po_issued", 30, 0, "EGP", 1, 0, "2026-09-15",
                     "2026-09-15 08:00:00", "2026-09-15 08:00:00", "2026-09-16 08:00:00", 1, None, None, "", "opex"),
                    ("AN-7", "t7", "QA", "Fake Supplier E", "approved", 10, 0, "EGP", 1, 0, "2026-10-15",
                     "2026-10-15 08:00:00", "2026-10-15 08:00:00", "2026-10-16 08:00:00", 1, None, None, "", "opex"),
                    ("AN-8", "t8", "QA", "Fake Supplier F", "po_issued", 20, 0, "EGP", 1, 0, "2026-10-20",
                     "2026-10-20 08:00:00", "2026-10-20 08:00:00", "2026-10-21 08:00:00", 1, None, None, "", "opex")):
            c.execute("INSERT INTO pr_requests (%s) VALUES (%s)" % (cols, ph), row)
            pid[row[0]] = c.execute("SELECT id FROM pr_requests WHERE pr_no=?", (row[0],)).fetchone()["id"]
        for n in range(3):
            c.execute("INSERT INTO pr_purchase_orders (pr_id, po_no, vendor, grand, status, issued_at) "
                      "VALUES (?, ?, 'Fake Supplier E', 10, 'issued', '2026-09-17 09:00:00')", (pid["AN-6"], "PO-AN-6-%d" % n))
        c.execute("INSERT INTO pr_purchase_orders (pr_id, po_no, vendor, grand, status, issued_at) "
                  "VALUES (?, 'PO-AN-8', 'Fake Supplier G', 20, 'issued', '2026-10-22 09:00:00')", (pid["AN-8"],))
        c.execute("INSERT INTO pr_items (pr_id, item, unit, qty, unit_price) VALUES (?, 'Fake Bolt', 'Pcs', 5, 2)", (pid["AN-6"],))
        c.execute("INSERT INTO pr_items (pr_id, item, unit, qty, unit_price) VALUES (?, 'Fake Bolt', 'Box', 1, 100)", (pid["AN-7"],))
        c.execute("INSERT INTO pr_items (pr_id, item, unit, qty, unit_price, vendor) VALUES (?, 'Fake Nut', 'Pcs', 4, 5, 'Fake Supplier F')",
                  (pid["AN-8"],))
        c.commit()
        cfg, _ = an.parse_config({"d1": "month", "m": "requests"})
        months = {r[0] for r in an.query(cfg, f)}
        chk("a non-ISO request_date is dated by created_at", "2026-06" in months and not any("/" in m for m in months), months)
        cfg, _ = an.parse_config({"d1": "quarter", "m": "requests"})
        chk("quarters stay readable", all(re.match(r"^\d{4}-Q[1-4]$", r[0]) for r in an.query(cfg, f)), an.query(cfg, f))
        chk("list dept filter is trimmed like analytics", "AN-5" in nos(dept="IT"), nos(dept="IT"))

        # price history: one series per item AND unit -> no fake 4900% rise
        ph_ = an.price_history(f, 10.0)
        chk("Box and Pcs are separate series", {"Fake Bolt (Pcs)", "Fake Bolt (Box)"} <= set(ph_["items"]), ph_["items"])
        chk("no price alert across units", not [a for a in ph_["alerts"] if a["name"] == "Fake Bolt"], ph_["alerts"])
        pick = an.price_history(f, 10.0, "Fake Bolt (Box)")
        chk("picking a series by its label", pick["item"] == "Fake Bolt (Box)" and pick["item_name"] == "Fake Bolt"
            and [p["price"] for p in pick["points"]] == [100.0], pick)

        # line facts use the PO supplier, as spend does
        cfg, _ = an.parse_config({"d1": "supplier", "m": "qty"})
        qty = {r[0]: r[2] for r in an.query(cfg, f)}
        chk("qty of a one-PO request lands on the PO supplier", near(qty.get("Fake Supplier G"), 4)
            and "Fake Supplier F" not in qty, qty)
        chk("list vendor = PO rule (header vendor with another PO supplier is not listed)",
            "AN-8" in nos(vendor="Fake Supplier G") and "AN-8" not in nos(vendor="Fake Supplier F"))

        # supplier ranking without money does not follow spend
        money_rank = [x["supplier"] for x in an.supplier_performance(f)]
        count_rank = [x["supplier"] for x in an.supplier_performance(f, money=False)]
        chk("money users: ranked by spend", money_rank[0] == "Fake Supplier A", money_rank)
        chk("counts-only: ranked by orders", count_rank[0] == "Fake Supplier E", count_rank)
        from app.approvals import vendor_score
        from app.approvals.constants import vendor_key
        cards = vendor_score.scorecards(months=120, conn=c)
        chk("on-time is the vendor scorecard's figure", all(
            x["ontime"] == ((cards.get(vendor_key(x["supplier"])) or {}).get("on_time") or {}).get("pct")
            for x in an.supplier_performance(f)))

        # spend over time: months with no spend are on the axis at 0
        cfg, _ = an.parse_config({"d1": "month", "m": "spend"})
        fr = an.parse_filters({"period": "custom", "from": "2026-05-01", "to": "2026-10-31"})
        sh = an.shape(cfg, an.query(cfg, fr), an.date_range(fr))
        chk("every calendar month is on the axis", sh["keys"] == ["2026-%02d" % m for m in range(5, 11)], sh["keys"])
        chk("an empty month is 0, not skipped", sh["data"][0][sh["keys"].index("2026-07")] == 0.0, sh["data"])
        cfg, _ = an.parse_config({"d1": "quarter", "m": "spend"})
        chk("quarters fill too", an.shape(cfg, an.query(cfg, fr), an.date_range(fr))["keys"] == ["2026-Q2", "2026-Q3", "2026-Q4"])
        c.close()

    def client(uid):
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
        return cli

    print("\nroutes and money")
    adm, staff = client(admin["id"]), client(ids["an.staff"])
    r = adm.get("/procurement/analytics?period=all")
    body = r.get_data(as_text=True)
    chk("the dashboard keeps its URL", r.status_code == 200, r.status_code)
    chk("a money user sees spend", "committed_spend" in body)
    r = staff.get("/procurement/analytics?period=all")
    body = r.get_data(as_text=True)
    chk("staff get a counts-only dashboard", r.status_code == 200 and "committed_spend" not in body, r.status_code)
    chk("no money figure leaks into staff's page", "5,340" not in body and "5340" not in body)
    chk("staff: spend query refused", staff.get("/procurement/analytics/run.json?d1=supplier&m=spend").status_code == 403)
    chk("staff: budget export refused", staff.get("/procurement/analytics/data/budget.xlsx").status_code == 403)
    r = staff.get("/procurement/analytics/run.json?d1=department&m=requests&period=all")
    chk("staff: counts allowed", r.status_code == 200, r.status_code)
    r = adm.get("/procurement/analytics/run.json?d1=supplier&m=spend&period=all")
    chk("admin: spend JSON", r.status_code == 200 and "Fake Supplier A" in r.get_data(as_text=True), r.status_code)
    chk("bad config is a 400", adm.get("/procurement/analytics/run.json?d1=nope&m=spend").status_code == 400)
    for url in ("/procurement/analytics/run.csv?d1=supplier&m=spend&period=all",
                "/procurement/analytics/run.xlsx?d1=month&d2=supplier&m=spend&period=all",
                "/procurement/analytics/data/stages.csv", "/procurement/analytics/data/suppliers.xlsx",
                "/procurement/analytics/builder?d1=month&d2=department&m=spend&period=all",
                "/procurement/list?vendor=Fake+Supplier+A&cat=Fake+Cat+One&cc=CC-20&kind=capex"):
        r = adm.get(url)
        chk("200 " + url, r.status_code == 200, r.status_code)
    csv = adm.get("/procurement/analytics/run.csv?d1=supplier&m=spend&period=all").get_data(as_text=True)
    chk("CSV carries the same total", "4150" in csv, csv[:200])   # 4140 + AN-5 (10, added above)

    print("\ndrill-down and exports")
    spec = adm.get("/procurement/analytics/run.json?d1=supplier&m=spend&period=all&cur=EGP").get_json()
    chk("a money chart drills into committed requests in its currency",
        spec["base"].get("committed") == "1" and spec["base"].get("cur") == "EGP", spec["base"])
    spec = adm.get("/procurement/analytics/run.json?d1=supplier&m=requests&period=all").get_json()
    chk("a count chart drills into every status", "committed" not in spec["base"], spec["base"])
    body = adm.get("/procurement/analytics?period=all").get_data(as_text=True)
    chk("the spend tile links to the committed list", "committed=1" in body)
    chk("the open-commitment tile links to the open list", "committed=open" in body)
    r = adm.get("/procurement/list?committed=1&cur=USD")
    lb = r.get_data(as_text=True)
    chk("the list shows the committed / currency chips", r.status_code == 200 and "proc.analytics.list.committed" in lb
        and "AN-2" in lb and "AN-3" not in lb, r.status_code)
    import openpyxl
    from app.approvals.xlsx import MONEY

    def formats(url):
        wb = openpyxl.load_workbook(io.BytesIO(adm.get(url).data))
        ws = wb.active
        # {column: {formats of its numeric cells}} (openpyxl reads 4150.0 back as int)
        out = {}
        for row in ws.iter_rows():
            for c in row:
                if isinstance(c.value, (int, float)) and not isinstance(c.value, bool):
                    out.setdefault(c.column, set()).add(c.number_format)
        return {col: (v.pop() if len(v) == 1 else v) for col, v in out.items()}
    fm = formats("/procurement/analytics/run.xlsx?d1=supplier&m=spend&period=all")
    chk("Excel: spend is money, the request count is not", fm.get(2) == MONEY and fm.get(3) != MONEY, fm)
    fm = formats("/procurement/analytics/run.xlsx?d1=department&m=cycle&period=all")
    chk("Excel: days are not money", fm and MONEY not in fm.values(), fm)
    fm = formats("/procurement/analytics/data/budget.xlsx?period=all")
    chk("Excel: budget % column is not money", all(v == MONEY for col, v in fm.items() if col in (3, 4))
        and all(v != MONEY for col, v in fm.items() if col == 5), fm)

    print("\nsaved reports")
    def save(cli, **data):
        data.setdefault("_csrf", "tok")
        return cli.post("/procurement/analytics/reports", data=data)

    def reps(owner):
        with app.app_context():
            c = get_db()
            try:
                return [dict(x) for x in c.execute("SELECT * FROM proc_saved_reports WHERE owner=? ORDER BY id", (owner,))]
            finally:
                c.close()

    save(staff, name="Staff spend", qs="d1=supplier&m=spend")
    chk("staff cannot save a money report", not reps("an.staff"))
    save(staff, name="Staff shared", qs="d1=department&m=requests", shared="1")
    chk("staff cannot share", not reps("an.staff"))
    save(staff, name="Staff counts", qs="d1=department&m=requests&period=all")
    mine = reps("an.staff")
    chk("staff save a counts report", len(mine) == 1 and not mine[0]["shared"], mine)
    save(adm, name="Admin spend", qs="d1=supplier&m=spend&period=all", shared="1")
    a = reps(admin["username"])
    chk("admin saves and shares a spend report", len(a) == 1 and a[0]["shared"] == 1, a)
    cfgj = json.loads(a[0]["config_json"])
    chk("config stored as whitelisted keys", cfgj["cfg"]["m"] == "spend" and cfgj["filters"].get("period") == "all", cfgj)
    rid = a[0]["id"]
    save(staff, name="Hijack", qs="d1=department&m=requests", rid=str(rid), mode="update")
    chk("another user cannot update it", reps(admin["username"])[0]["name"] == "Admin spend")
    body = staff.get("/procurement/analytics/builder").get_data(as_text=True)
    chk("shared reports are not listed for staff", "Admin spend" not in body)
    chk("staff cannot open it by id", "Admin spend" not in staff.get("/procurement/analytics/builder?rid=%d" % rid).get_data(as_text=True))
    save(adm, name="Admin spend v2", qs="d1=supplier&m=spend&period=all", rid=str(rid), mode="update", shared="1")
    chk("owner updates", reps(admin["username"])[0]["name"] == "Admin spend v2")
    adm.post("/procurement/analytics/reports/%d/pin" % rid, data={"_csrf": "tok"})
    body = adm.get("/procurement/analytics?period=m3&vendor=Fake+Supplier+B").get_data(as_text=True)
    chk("a pinned report shows on the dashboard", "Admin spend v2" in body)
    chk("a pinned card exports with its own filters", re.search(r'run\.xlsx\?[^"]*period=all', body))
    chk("a pinned card states its own saved filters", "Saved filters:" in body)
    chk("no cap note under the limit", 'data-i18n="proc.analytics.pin_cap"' not in body)
    extra_ids = []
    for n in range(7):
        save(adm, name="Fake pin %d" % n, qs="d1=department&m=requests&period=all")
        extra_ids.append(reps(admin["username"])[-1]["id"])
        adm.post("/procurement/analytics/reports/%d/pin" % extra_ids[-1], data={"_csrf": "tok"})
    body = adm.get("/procurement/analytics?period=all").get_data(as_text=True)
    chk("over the pin limit the page says so", 'data-i18n="proc.analytics.pin_cap"' in body)
    for x in extra_ids:
        adm.post("/procurement/analytics/reports/%d/delete" % x, data={"_csrf": "tok"})
    adm.post("/procurement/analytics/reports/%d/pin" % rid, data={"_csrf": "tok"})
    chk("unpin removes it", "Admin spend v2" not in adm.get("/procurement/analytics?period=all").get_data(as_text=True))
    staff.post("/procurement/analytics/reports/%d/delete" % rid, data={"_csrf": "tok"})
    chk("another user cannot delete it", len(reps(admin["username"])) == 1)
    adm.post("/procurement/analytics/reports/%d/delete" % rid, data={"_csrf": "tok"})
    chk("owner deletes", not reps(admin["username"]))
    r = adm.post("/procurement/analytics/reports/%d/pin" % rid, data={"_csrf": "tok", "next": "https://evil.example/"})
    chk("pin redirect stays inside analytics", "evil" not in (r.headers.get("Location") or ""), r.headers.get("Location"))

    print("\nlanguages")
    from app.approvals.messages import MESSAGES
    from app.approvals.tests_messages_i18n import user_facing_strings
    msgs = user_facing_strings(str(ROOT / "app" / "routes" / "approvals_analytics.py"))
    missing = sorted(m for m in msgs if m not in MESSAGES)
    chk("every analytics flash has AR/TR", msgs and not missing, missing[:3])
    packs = {lang: json.load(open(ROOT / "app" / "static" / "i18n" / ("%s.json" % lang), encoding="utf-8"))
             for lang in ("en", "ar", "tr")}
    gaps = [(lang, key) for key in an.I18N for lang in packs if not packs[lang].get(key)]
    chk("every analytics key is in en/ar/tr.json", not gaps, gaps[:3])
    chk("each key has three different-script entries", all(len(v) == 3 and all(v) for v in an.I18N.values()))
    used = set()
    for name in ("analytics.html", "analytics_builder.html", "_analytics_card.html", "_analytics_filters.html"):
        src = open(ROOT / "app" / "templates" / "approvals" / name, encoding="utf-8").read()
        used |= {m for m in re.findall(r'data-i18n="([a-z0-9_.]+)"', src)}
    unknown = sorted(kk for kk in used if kk not in packs["en"])
    chk("every literal data-i18n key in the templates resolves", not unknown, unknown[:5])

    print("\nALL PASS" if ok[0] else "\nSOME CHECKS FAILED")
    return ok[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
