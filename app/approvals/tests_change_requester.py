"""Admin "Change requester" on a request.

    python -m app.approvals.tests_change_requester
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "reqchg.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.approvals import services as svc
        from app.db import get_db

        c = get_db()
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        for un, active in (("sp.request", 1), ("gone.user", 0)):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, 'viewer', ?, '2026-01-01')", (un, un.title(), active))
        c.commit()
        new_id = c.execute("SELECT id FROM users WHERE username='sp.request'").fetchone()["id"]
        c.close()
        pid, _ = svc.create_pr({"title": "Keyed in", "department": "IT"},
                               [{"item": "Toner", "qty": 1, "unit_price": 100}], admin, submit=False, priced=True)

        assert svc.change_requester(pid, "SP.request", admin, "") == (False, "reason_required")
        assert svc.change_requester(pid, "nobody", admin, "x") == (False, "no_user")
        assert svc.change_requester(pid, "gone.user", admin, "x") == (False, "inactive")
        assert svc.change_requester(pid, admin["username"], admin, "x") == (False, "same")
        assert svc.change_requester(99999, "sp.request", admin, "x") == (False, "not_found")
        assert svc.change_requester(pid, "SP.request", admin, "raised by IT")[0]
        c = get_db()
        pr = c.execute("SELECT requester, requester_user_id FROM pr_requests WHERE id=?", (pid,)).fetchone()
        assert (pr["requester"], pr["requester_user_id"]) == ("sp.request", new_id)
        assert c.execute("SELECT 1 FROM pr_events WHERE pr_id=? AND action='requester_changed'", (pid,)).fetchone()
        # Someone who signed a step cannot become the requester.
        c.execute("INSERT INTO pr_steps (pr_id, seq, stage, status, approver_user) "
                  "VALUES (?, 1, 'x', 'approved', ?)", (pid, admin["username"]))
        c.commit()
        c.close()
        assert svc.change_requester(pid, admin["username"], admin, "x") == (False, "signed_own")

        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = admin["id"], 0, "tok"
        html = cli.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert "proc.req_change.title" in html
        r = cli.post("/procurement/pr/%d/requester" % pid, data={"_csrf": "tok", "username": "", "reason": "x"})
        assert r.status_code == 302
    print("change requester: OK")


if __name__ == "__main__":
    run()
