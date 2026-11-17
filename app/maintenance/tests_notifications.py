"""Run: python -m app.maintenance.tests_notifications (isolated SQLite database)."""
import os
import sys
import tempfile
from pathlib import Path


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ["TC_ENV"] = "development"
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
    import config
    config.Config.DATABASE_URL = ""
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="mnt_notifications_"), "test.db")
    from app import create_app
    app = create_app()
    with app.app_context():
        checks()
    print("all checks passed")


def checks():
    from app.db import get_db
    from app.maintenance import services as svc
    from app.maintenance.messages import translate

    def execute(sql, *args):
        conn = get_db()
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def one(sql, *args):
        conn = get_db()
        try:
            return dict(conn.execute(sql, args).fetchone())
        finally:
            conn.close()

    def user(username, full_name, role="maintenance_technician", lang="en", active=1):
        uid = execute("INSERT INTO users (username,password_hash,full_name,role,lang_pref,is_active) "
                      "VALUES (?,'x',?,?,?,?)", username, full_name, role, lang, active)
        return {"id": uid, "username": username, "full_name": full_name, "role": role}

    tech = user("test_tech_a", "Test Shared Name")
    user("test_tech_b", "Test Shared Name")
    user("test_name_collision", "test_tech_a")
    unique = user("test_unique", "Test Unique Name")
    user("test_inactive", "Test Inactive Name", active=0)
    conn = get_db()
    try:
        assert svc._usernames_for(conn, [" test_tech_a ", "test_tech_a", ""]) == [tech["username"]]
        assert svc._usernames_for(conn, ["Test Shared Name"]) == []
        assert svc._usernames_for(conn, ["Test Unique Name"]) == [unique["username"]]
        assert svc._usernames_for(conn, ["test_inactive", "Test Inactive Name"]) == []
    finally:
        conn.close()

    tid, err = svc.create_ticket({"description": "Test self closure"}, tech)
    assert tid, err
    execute("UPDATE mnt_tickets SET status='resolved' WHERE id=?", tid)
    assert svc.close_ticket(tid, tech)[0]
    count_personal = "SELECT COUNT(*) c FROM notifications WHERE target_user=? AND module='maintenance'"
    assert one(count_personal, tech["username"])["c"] == 0
    # control: someone else closing it does notify the requester
    other_tid, err = svc.create_ticket({"description": "Test closed by another"}, tech)
    assert other_tid, err
    execute("UPDATE mnt_tickets SET status='resolved' WHERE id=?", other_tid)
    assert svc.close_ticket(other_tid, unique)[0]
    assert one(count_personal, tech["username"])["c"] == 1
    execute("DELETE FROM notifications WHERE target_user=?", tech["username"])

    spare = execute("INSERT INTO mnt_spare_parts (code,name,stock_qty,reserved_qty,avg_cost) "
                    "VALUES (?,?,20,0,1)", "TEST-NOTICE-SPARE", "Test notification spare")
    items = [{"spare_id": spare, "qty": 1}]
    conn = get_db()
    try:
        roles = svc._pick_matrix_levels(conn, items)
    finally:
        conn.close()
    approver = user("test_approver", "Test Approver", roles[0] if roles else "maintenance_manager")
    tid, err = svc.create_ticket({"description": "Test spare approval"}, tech)
    assert tid, err
    rid, err = svc.create_request(tid, items, "Test parts", "normal", tech)
    assert rid, err
    assert one(count_personal, approver["username"])["c"] == 1
    assert one("SELECT COUNT(*) c FROM notifications WHERE target_user IS NULL "
               "AND title='Spare request awaiting approval'")["c"] == 0
    assert one("SELECT COUNT(*) c FROM mnt_notifications WHERE entity_type='request' "
               "AND entity_id=? AND title='Spare request awaiting approval'", rid)["c"] == 1

    # Only the actor fills this rung: preserve the broadcast fallback, omit their personal notice.
    execute("UPDATE users SET is_active=0 WHERE role=? AND username<>?", approver["role"], approver["username"])
    self_tid, err = svc.create_ticket({"description": "Test approver self request"}, approver)
    assert self_tid, err
    self_rid, err = svc.create_request(self_tid, items, "Self request", "normal", approver)
    assert self_rid, err
    assert one(count_personal, approver["username"])["c"] == 1
    assert one("SELECT COUNT(*) c FROM notifications WHERE target_user IS NULL "
               "AND title='Spare request awaiting approval'")["c"] == 1
    # Restore the technician for system-triggered stock notices.
    execute("UPDATE users SET is_active=1 WHERE username=?", tech["username"])
    execute("UPDATE mnt_requests SET status='out_of_stock' WHERE id=?", rid)
    execute("UPDATE mnt_tickets SET status='waiting_stock', assigned_to=? WHERE id=?", tech["username"], tid)
    assert svc.receive_stock(spare, 2, 1, approver, skip_ticket=tid)[0]
    assert one(count_personal, tech["username"])["c"] == 0
    assert svc.receive_stock(spare, 2, 1, approver)[0]
    assert one(count_personal, tech["username"])["c"] == 1
    assert one("SELECT message FROM notifications WHERE target_user=? AND module='maintenance'",
               tech["username"])["message"].startswith("TEST-NOTICE-SPARE received (2)")

    execute("UPDATE users SET lang_pref='ar' WHERE username=?", tech["username"])
    assert svc.receive_stock(spare, 2, 1, approver)[0]
    assert one("SELECT COUNT(*) c FROM notifications WHERE target_user=? AND title=?",
               tech["username"], translate("Spare part arrived", "ar"))["c"] == 1


if __name__ == "__main__":
    run()
