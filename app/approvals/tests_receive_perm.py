"""Run: python -m app.approvals.tests_receive_perm (isolated SQLite database)."""
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="receive_perm_"), "test.db")
    from app import create_app
    app = create_app()
    with app.app_context():
        from flask import g
        from app.db import get_db
        from app.security import ROLES, PERMISSIONS
        from app.approvals import services as svc

        for role in ("storekeeper", "warehouse_manager"):
            assert "proc_receive" in ROLES[role]["perms"]
            assert "proc_purchasing" not in ROLES[role]["perms"]
        assert {"wh_view", "wh_manage"} <= set(ROLES["warehouse_manager"]["perms"])
        assert "wh_view" in ROLES["factory_manager"]["perms"]
        assert "proc_receive" in PERMISSIONS

        conn = get_db()
        try:
            for username, role in (("test_receiver", "storekeeper"), ("test_normal", "normal_user")):
                conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active) "
                             "VALUES (?,'x',?,?,1)", (username, username, role))
            conn.commit()
        finally:
            conn.close()
        pr_id, _ = svc.create_pr(
            {"title": "Receiving permission", "department": "Maintenance"},
            [{"item": "Test receiving part", "qty": 10, "unit_price": 1}],
            {"username": "test_receiver", "role": "storekeeper"}, priced=False)
        assert pr_id
        conn = get_db()
        try:
            conn.execute("UPDATE pr_requests SET status='po_issued', po_no=? WHERE id=?",
                         ("TEST-RECEIVE-PO", pr_id))
            item_id = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
            conn.commit()
        finally:
            conn.close()

        client = app.test_client()
        token = "t" * 64

        def login(username):
            conn = get_db()
            try:
                user = dict(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())
            finally:
                conn.close()
            g.pop("user", None)
            with client.session_transaction() as session:
                session.clear()
                session.update({"uid": user["id"], "ep": user.get("session_epoch") or 0,
                                "username": username, "role": user["role"], "_csrf_token": token})

        login("test_normal")
        url = "/procurement/pr/%s/receive" % pr_id
        form = {"recv_item_id[]": str(item_id), "recv_qty[]": "4", "_csrf": token}
        assert client.post(url, data=form).status_code == 403
        login("test_receiver")
        # Both ordered and dispatched POs expose receiving, without purchasing controls.
        for status in ("po_issued", "in_transit"):
            conn = get_db()
            try:
                conn.execute("UPDATE pr_requests SET status=? WHERE id=?", (status, pr_id))
                conn.commit()
            finally:
                conn.close()
            page = client.get("/procurement/pr/%s" % pr_id)
            assert page.status_code == 200
            assert b'name="recv_qty[]"' in page.data
            assert ("/pr/%s/in-transit" % pr_id).encode() not in page.data
            assert ("/pr/%s/invoice" % pr_id).encode() not in page.data
        conn = get_db()
        try:
            conn.execute("UPDATE pr_requests SET status='po_issued' WHERE id=?", (pr_id,))
            conn.commit()
        finally:
            conn.close()
        assert client.post(url, data=form).status_code == 302
        bundle = svc.get_pr(pr_id)
        assert float(bundle["items"][0]["received_qty"]) == 4
        assert bundle["pr"]["status"] == "partially_received"
        assert len(bundle["grns"]) == 1
    print("all checks passed")


if __name__ == "__main__":
    run()
