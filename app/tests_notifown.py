"""Notifications reach only their owner (lane notifown).

  A  a broadcast (target_user NULL) rings only the owners of its module + super
     admin; reading it as one owner leaves it unread for another; notify.py's
     auto-resolve still hides it for everyone; mark-read never touches a row the
     reader cannot see.
  B  'New request to sign' / overdue reminders go to whose turn it is: the
     warehouse manager is not prompted at the 'warehouse' rung while a storekeeper
     exists, is when none does, and is at the 'warehouse_manager' rung; the
     requester still hears about their own request.

    python -m app.tests_notifown
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="notifown_"), "n.db")
    config.Config.DATABASE_URL = ""
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    from flask import g
    # one app context for the whole run: drop the cached user between requests
    app.before_request(lambda: g.pop("user", None) and None)
    # the feed would pull the real LAN systems' alerts in a background thread
    import app.routes.main as main_routes
    main_routes._kick_notification_sync = lambda: None
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        from app.services import bell_scope, notify

        c = get_db()
        c.execute("UPDATE users SET is_active=0")      # a roster this test controls
        ids = {}
        for un, role in (("no.staff", "normal_user"), ("no.store", "storekeeper"),
                         ("no.whm", "warehouse_manager"), ("no.it", "it_manager"),
                         ("no.admin", "super_admin"), ("no.req", "executive_viewer"),
                         ("no.mm", "maintenance_manager")):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, "
                      "created_at) VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, un, role))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        c.execute("DELETE FROM notifications")
        now = "2026-11-26 10:00:00"
        for mod, title in (("warehouse", "Low stock: X1"), ("maintenance", "Machine down"),
                           ("itsm", "[ITSM] New ticket"), ("mystery", "Unmapped module"),
                           (None, "No module")):
            c.execute("INSERT INTO notifications (severity, module, title, message, created_at) "
                      "VALUES ('warning', ?, ?, 'x', ?)", (mod, title, now))
        c.commit()
        nid = {r["title"]: r["id"] for r in c.execute("SELECT id, title FROM notifications")}

        def u(name):
            return dict(c.execute("SELECT * FROM users WHERE username=?", (name,)).fetchone())

        def titles(name):
            return {r["title"] for r in bell_scope.bell_rows(c, u(name))}

        def unread(name):
            return {r["title"] for r in bell_scope.bell_rows(c, u(name)) if not r["is_read"]}

        # ---- A: who sees which broadcast ----
        assert titles("no.staff") == set(), titles("no.staff")
        assert titles("no.store") == {"Low stock: X1"}, titles("no.store")
        assert titles("no.whm") == {"Low stock: X1"}, titles("no.whm")
        assert titles("no.mm") == {"Machine down"}, titles("no.mm")
        assert titles("no.it") == {"[ITSM] New ticket"}, titles("no.it")
        assert titles("no.admin") == set(nid), titles("no.admin")   # incl. unmapped + NULL module
        assert bell_scope.unread_count(c, u("no.staff")) == 0
        assert bell_scope.unread_count(c, u("no.store")) == 1
        assert bell_scope.unread_count(c, u("no.admin")) == 5

        def client(name):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = ids[name], 0, "tok"
            return cli

        feed = client("no.staff").get("/notifications/feed").get_json()
        assert feed["unread"] == 0 and not feed["items"], feed
        assert client("no.store").get("/notifications/feed").get_json()["unread"] == 1

        # ---- A: per-reader read state ----
        h = {"X-CSRF-Token": "tok"}
        r = client("no.store").post("/notifications/read", json={"id": nid["Low stock: X1"]}, headers=h)
        assert r.status_code == 200, r.status_code
        assert unread("no.store") == set()
        assert unread("no.whm") == {"Low stock: X1"}                 # still unread for the other owner
        assert unread("no.admin") >= {"Low stock: X1"}
        assert c.execute("SELECT is_read FROM notifications WHERE id=?",
                         (nid["Low stock: X1"],)).fetchone()["is_read"] == 0
        # a reader cannot mark a row they cannot see
        client("no.staff").post("/notifications/read", json={"id": nid["Machine down"]}, headers=h)
        client("no.staff").post("/notifications/read", headers=h)
        assert not c.execute("SELECT 1 FROM notification_reads WHERE username='no.staff'").fetchone()
        assert "Machine down" in unread("no.mm")
        # mark all read: only this reader's visible rows
        r = client("no.mm").post("/notifications/read", headers=h)
        assert r.status_code == 200, (r.status_code, r.get_data(as_text=True)[:200])
        assert unread("no.mm") == set(), unread("no.mm")
        assert "Machine down" in unread("no.admin")
        client("no.store").post("/notifications/read", json={"id": nid["Low stock: X1"]}, headers=h)
        assert c.execute("SELECT COUNT(*) n FROM notification_reads WHERE username='no.store'"
                         ).fetchone()["n"] == 1                       # reading twice: one row

        # ---- A: auto-resolve still hides a "system offline" alert for everyone ----
        sysrow = [{"is_integrated": 1, "key": "monitoring", "name_en": "Monitoring",
                   "base_url": "http://x.invalid"}]
        c.commit()
        notify.sync_health_notifications(sysrow, {"monitoring": {"status": "offline"}}, force=True)
        off = [t for t in unread("no.it") if "Monitoring" in t]
        assert off, unread("no.it")
        assert not [t for t in titles("no.staff") if "Monitoring" in t]
        notify.sync_health_notifications(sysrow, {"monitoring": {"status": "online"}}, force=True)
        assert not [t for t in unread("no.it") if "Monitoring" in t]
        assert not [t for t in unread("no.admin") if "Monitoring" in t]

        # ---- B: signature prompts by name ----
        svc.set_budget("IT", 10000000)
        req = {"username": "no.req", "id": ids["no.req"], "role": "executive_viewer"}
        store = u("no.store")
        whm = u("no.whm")

        def new_pr(title):
            pid, _ = svc.create_pr({"title": title, "department": "IT", "currency": "EGP"},
                                   [{"item": "Toner", "unit": "Pcs", "qty": 10, "unit_price": 0}],
                                   req, submit=False, priced=False)
            ok, msg = svc.submit_pr(pid, req)
            assert ok, msg
            return pid

        def prompted(pid, title="New request to sign"):
            return {r["target_user"] for r in c.execute(
                "SELECT target_user FROM notifications WHERE link=? AND title=?",
                (svc._pr_link(pid), title)).fetchall()}

        def stage(pid):
            return c.execute("SELECT s.stage FROM pr_steps s JOIN pr_requests p ON p.id=s.pr_id "
                             "WHERE s.pr_id=? AND s.seq=p.current_seq AND s.status='pending'",
                             (pid,)).fetchone()["stage"]

        c.commit()
        p1 = new_pr("With a storekeeper")
        assert stage(p1) == "warehouse", stage(p1)
        assert prompted(p1) == {"no.store"}, prompted(p1)
        assert svc.can_act_step(whm, c.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? AND stage='warehouse'", (p1,)).fetchone())
        # overdue reminder follows the same rule
        old = (datetime.now(timezone.utc) - timedelta(hours=200)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE pr_steps SET activated_at=? WHERE pr_id=? AND stage='warehouse'", (old, p1))
        c.commit()
        svc.run_escalations()
        assert "no.whm" not in prompted(p1, "Approval overdue"), prompted(p1, "Approval overdue")
        assert "no.store" in prompted(p1, "Approval overdue")
        ok, msg = svc.act_on_step(p1, store, "approve")
        assert ok, msg
        assert stage(p1) == "warehouse_manager", stage(p1)
        assert "no.whm" in prompted(p1), prompted(p1)                 # now it is their turn

        # no storekeeper at all: the manager is prompted at the warehouse rung
        c.execute("UPDATE users SET is_active=0 WHERE username='no.store'")
        c.commit()
        p2 = new_pr("No storekeeper")
        assert stage(p2) == "warehouse"
        assert prompted(p2) == {"no.whm"}, prompted(p2)

        # the requester still hears about their own request
        ok, msg = svc.act_on_step(p2, whm, "reject", comment="not needed")
        assert ok, msg
        assert "no.req" in prompted(p2, "Request rejected"), prompted(p2, "Request rejected")
        assert bell_scope.unread_count(c, u("no.req")) >= 1
        c.close()
    print("tests_notifown: all checks passed")


if __name__ == "__main__":
    run()
