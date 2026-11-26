"""Notifications reach only their owner (lane notifown).

  A  a broadcast (target_user NULL) rings only the owners of its module + super
     admin; reading it as one owner leaves it unread for another; notify.py's
     auto-resolve still hides it for everyone; mark-read never touches a row the
     reader cannot see.
  B  'New request to sign' / overdue reminders go to whose turn it is: the
     warehouse manager is not prompted at the 'warehouse' rung while a storekeeper
     exists, is when none does, and is at the 'warehouse_manager' rung; the
     requester still hears about their own request. Level-2 overdue reaches
     everyone who may sign. A delegator is replaced by the delegate (even one the
     narrowing left out); an expired delegation changes nothing.
  C  scope-locked accounts see / mark only their systems' rows; read-only health
     viewers do not own IT broadcasts; an integrated system added later is IT's;
     the unread count is capped; BI de-dup is per reader and resolves on 'ok'.

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
                         ("no.mm", "maintenance_manager"), ("no.itsm", "itsm_user")):
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
        assert titles("no.req") == set(), titles("no.req")          # executive: read-only, no IT feed
        assert titles("no.itsm") == set(), titles("no.itsm")

        # ---- C: an integrated system added later is IT's; scope-locked reader ----
        c.execute("INSERT INTO systems (key, name_en, is_integrated) VALUES ('newsys', 'New', 1)")
        c.execute("INSERT INTO notifications (severity, module, title, message, created_at) "
                  "VALUES ('warning', 'newsys', 'New system offline', 'x', ?)", (now,))
        for mod, title in (("itsm", "Mine ITSM"), ("procurement", "Mine proc")):
            c.execute("INSERT INTO notifications (severity, module, title, message, created_at, "
                      "target_user) VALUES ('info', ?, ?, 'x', ?, 'no.itsm')", (mod, title, now))
        c.commit()
        assert "New system offline" in titles("no.it") and "New system offline" not in titles("no.req")
        assert titles("no.itsm") == {"Mine ITSM"}, titles("no.itsm")
        c.execute("DELETE FROM notifications WHERE title='New system offline'")
        c.execute("DELETE FROM systems WHERE key='newsys'")
        c.commit()

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
        # mark all read never touches a row outside a scope-locked reader's systems
        client("no.itsm").post("/notifications/read", headers=h)
        assert {r["title"]: r["is_read"] for r in c.execute(
            "SELECT title, is_read FROM notifications WHERE target_user='no.itsm'")} ==             {"Mine ITSM": 1, "Mine proc": 0}

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
        # critically late: everyone who may sign, the warehouse manager included
        lvl2 = prompted(p1, "Approval overdue (level 2)")
        assert {"no.store", "no.whm", "no.admin"} <= lvl2, lvl2
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

        # ---- B: delegations ----
        c.execute("UPDATE users SET is_active=1 WHERE username='no.store'")
        c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, "
                  "created_at) VALUES ('no.store2', 'x', 'no.store2', 'storekeeper', 1, '2026-01-01')")

        def delegate(frm, to, start=None, end=None):
            c.execute("DELETE FROM proc_delegations")
            c.execute("INSERT INTO proc_delegations (from_user, to_user, from_date, to_date, "
                      "is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)", (frm, to, start, end, now))
            c.commit()

        delegate("no.store", "no.store2", "2000-01-01", "2000-01-31")      # expired: no effect
        p3 = new_pr("Expired delegation")
        assert prompted(p3) == {"no.store", "no.store2"}, prompted(p3)
        delegate("no.store", "no.store2")                                    # away: the delegate only
        p4 = new_pr("Storekeeper away")
        assert prompted(p4) == {"no.store2"}, prompted(p4)
        delegate("no.store", "no.req")                                       # to the requester: kept
        p5 = new_pr("Delegated to the requester")
        assert prompted(p5) == {"no.store", "no.store2"}, prompted(p5)
        c.execute("UPDATE users SET is_active=0 WHERE username='no.store2'")
        delegate("no.store", "no.whm")       # the only storekeeper away -> the manager is asked
        p6 = new_pr("Only storekeeper away")
        assert prompted(p6) == {"no.whm"}, prompted(p6)
        c.execute("DELETE FROM proc_delegations")
        c.commit()

        # ---- C: BI de-dup per reader; a broadcast resolves when the metric is ok ----
        from app.services.bi import jobs as bi_jobs
        assert bi_jobs._insert_notification("critical", "BI t", "m", "bi_alert:999")
        assert not bi_jobs._insert_notification("critical", "BI t", "m", "bi_alert:999")
        assert bi_jobs._insert_notification("critical", "BI t", "m", "bi_alert:999", "no.it")
        bi_jobs._resolve_broadcast("bi_alert:999")
        assert bi_jobs._insert_notification("critical", "BI t", "m", "bi_alert:999")

        # ---- C: the unread count is capped (the badge says 99+) ----
        for i in range(bell_scope.UNREAD_CAP + 20):
            c.execute("INSERT INTO notifications (severity, module, title, message, created_at) "
                      "VALUES ('info', 'warehouse', ?, 'x', ?)", ("Bulk %d" % i, now))
        c.commit()
        assert bell_scope.unread_count(c, u("no.whm")) == bell_scope.UNREAD_CAP
        c.close()
    print("tests_notifown: all checks passed")


if __name__ == "__main__":
    run()
