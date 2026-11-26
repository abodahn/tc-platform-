"""Procurement-only scope (app/proc_scope.py): a user whose only module
permissions are proc_* sees procurement and nothing else; everyone else is
unchanged.

    python -m app.tests_proconly
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "proconly.db")
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_HEALTH_TIMEOUT"] = "1"
    from app import create_app
    return create_app()


# Built-in roles that become procurement-only under the rule (reported to IT).
EXPECTED_PROC_ONLY = {
    "normal_user", "finance_manager", "financial_controller", "cfo", "ceo",
    "supply_chain_director", "plant_director", "hr_director", "financial_director",
    "managing_director", "board",
}

OTHER_NAV = ("command_center", "launcher", "orders", "orders_tna", "ai_center", "bi",
             "sf_cc", "registry", "roadmap", "reporting", "itsm", "production")


def run():
    app = _app()
    from flask import g
    # One app context for the whole run: drop the cached user so each test
    # client acts as its own session, not the previous one. FIRST in the chain,
    # because the scope guard itself reads the user in a before_request.
    app.before_request_funcs.setdefault(None, []).insert(0, lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.db import get_db
        from app.security import ROLES
        from app.proc_scope import is_procurement_only
        from app.approvals import services as svc

        # -- the rule, over every built-in role
        got = {k for k in ROLES if is_procurement_only({"role": k})}
        assert got == EXPECTED_PROC_ONLY, sorted(got ^ EXPECTED_PROC_ONLY)
        assert not is_procurement_only({"role": "super_admin"})
        assert not is_procurement_only({"role": "itsm_user"})          # own system scope
        assert not is_procurement_only({"role": "purchasing_manager"})  # holds wh_view
        assert not is_procurement_only({"role": "storekeeper"})
        assert not is_procurement_only({"role": "normal_user", "extra_perms": '["maint_view"]'})
        assert is_procurement_only({"role": "normal_user", "extra_perms": '["view_reports"]'})
        assert not is_procurement_only(None)

        c = get_db()
        ids = {}
        for un, role, extra in (("t.req", "normal_user", None), ("t.cfo", "cfo", None),
                                ("t.buyer", "purchasing_manager", None),
                                ("t.store", "storekeeper", None),
                                ("t.wide", "normal_user", '["maint_view"]')):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, "
                      "created_at, extra_perms) VALUES (?, 'x', ?, ?, 1, '2026-01-01', ?)",
                      (un, un, role, extra))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        ids["admin"] = c.execute("SELECT id FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone()["id"]
        # an overdue order milestone (My Work + search bait) and three bell rows
        c.execute("INSERT INTO ord_orders (order_no, buyer, style_name, ship_date, status) "
                  "VALUES ('ORD-ZQX-1', 'Zqx Buyer', 'Zqx Tee', '2026-12-30', 'confirmed')")
        oid = c.execute("SELECT id FROM ord_orders WHERE order_no='ORD-ZQX-1'").fetchone()["id"]
        c.execute("INSERT INTO ord_milestones (order_id, name, planned_date) VALUES (?, 'Zqx fabric', '2020-01-01')", (oid,))
        for mod, who, title in (("procurement", "t.req", "PROC-BELL"), ("maintenance", None, "MAINT-BELL"),
                                ("itsm", None, "ITSM-BELL")):
            c.execute("INSERT INTO notifications (severity, module, title, message, target_user, created_at) "
                      "VALUES ('info', ?, ?, 'x', ?, '2026-11-26 08:00:00')", (mod, title, who))
        c.commit()
        c.close()
        svc.create_pr({"title": "Zqx toner", "department": "IT"},
                      [{"item": "Toner", "qty": 1, "unit_price": 10}],
                      {"username": "t.req", "id": ids["t.req"], "role": "normal_user"}, submit=False)

        def client(un):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = ids[un], 0, "tok"
            return cli

        def nav_keys(html):
            import re
            return set(re.findall(r'data-navkey="([a-z_]+)"', html))

        proc_home = "/procurement/"
        for un in ("t.req", "t.cfo"):
            cli = client(un)
            # '/' lands on procurement
            r = cli.get("/")
            assert r.status_code == 302 and r.headers["Location"].endswith(proc_home), (un, r.status_code, r.headers.get("Location"))
            # procurement, My Work and profile still open
            # (the Reports Center only lists reports gated on the user's own module perms)
            for url in (proc_home, "/my-work", "/profile", "/reporting/"):
                assert cli.get(url).status_code == 200, (un, url)
            html = cli.get(proc_home).get_data(as_text=True)
            keys = nav_keys(html)
            assert "procurement" in keys and "my_work" in keys, (un, keys)
            assert not keys & set(OTHER_NAV), (un, keys & set(OTHER_NAV))
            # everything else by URL bounces back (GET) or is refused (POST)
            for url in ("/orders/", "/orders/tna", "/launcher", "/registry", "/roadmap",
                        "/intelligence/", "/factory/", "/bi/", "/module/itsm",
                        "/sso/launch/itsm", "/reports", "/health"):
                r = cli.get(url)
                assert r.status_code == 302 and r.headers["Location"].endswith(proc_home), (un, url, r.status_code)
            r = cli.post("/orders/", data={"_csrf": "tok", "order_no": "X"})
            assert r.status_code == 403, (un, r.status_code)
            # search: procurement results only
            res = cli.get("/search?q=zqx").get_json()["results"]
            assert all(x["type"] not in ("order", "production") and "/module/" not in x["url"]
                       for x in res), (un, res)
            assert all("/module/" not in x["url"] for x in cli.get("/search?q=itsm").get_json()["results"])
            # bell + feed: procurement notifications only
            feed = cli.get("/notifications/feed").get_json()
            assert {i["module"] for i in feed["items"]} <= {"procurement"}, (un, feed["items"])
            # My Work: no order milestones
            assert "ORD-ZQX-1" not in cli.get("/my-work").get_data(as_text=True), un
        # the requester sees their own PR in search and their procurement alert
        req = client("t.req")
        assert any(x["type"] == "pr" for x in req.get("/search?q=zqx").get_json()["results"])
        assert [i["title"] for i in req.get("/notifications/feed").get_json()["items"]] == ["PROC-BELL"]

        # -- unchanged: storekeeper (warehouse perms), super_admin, a requester
        #    with another module's extra permission, and purchasing_manager (wh_view)
        for un in ("t.store", "admin", "t.wide", "t.buyer"):
            cli = client(un)
            r = cli.get("/")
            assert r.status_code == 200, (un, r.status_code, r.headers.get("Location"))
            assert cli.get("/orders/").status_code == 200, un
            keys = nav_keys(cli.get("/my-work").get_data(as_text=True))
            assert "orders" in keys and "command_center" in keys, (un, keys)
            assert any(x["type"] == "order" for x in cli.get("/search?q=zqx").get_json()["results"]), un
            mods = {i["module"] for i in cli.get("/notifications/feed").get_json()["items"]}
            assert "itsm" in mods, (un, mods)
            assert "ORD-ZQX-1" in cli.get("/my-work").get_data(as_text=True), un
        print("tests_proconly: all checks passed")


if __name__ == "__main__":
    run()
