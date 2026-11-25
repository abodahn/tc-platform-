"""Procurement analytics workspace: one spend definition everywhere (VAT, FX,
budget released, PO split, cancelled POs), whitelisted builder, money gating,
saved reports (owner / share / pin), list drill-down filters, EN/AR/TR keys.

    python -m app.approvals.tests_analytics
"""
import json
import os
import re
import sys
import tempfile
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
        chk("vendor matches header, PO or line", nos(vendor="Fake Supplier B") == {"AN-2"}, nos(vendor="Fake Supplier B"))
        chk("category matches a line", nos(cat="Fake Cat One") == {"AN-2"}, nos(cat="Fake Cat One"))
        chk("cost centre", nos(cc="CC-10") == {"AN-1", "AN-3"}, nos(cc="CC-10"))
        chk("OPEX / CAPEX", nos(kind="capex") == {"AN-2"}, nos(kind="capex"))
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
    chk("CSV carries the same total", "4140" in csv, csv[:200])

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
    body = adm.get("/procurement/analytics?period=all").get_data(as_text=True)
    chk("a pinned report shows on the dashboard", "Admin spend v2" in body)
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
