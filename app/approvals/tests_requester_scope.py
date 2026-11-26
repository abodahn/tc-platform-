"""A plain requester sees only their own purchase requests.

    python -m app.approvals.tests_requester_scope
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "scope.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    from flask import g
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import services as svc
        from app.db import get_db

        c = get_db()
        ids = {}
        for un, role in (("sc.alice", "normal_user"), ("sc.bob", "normal_user"), ("sc.buyer", "purchasing_manager")):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, un, role))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        c.commit()
        c.close()
        alice = {"username": "sc.alice", "id": ids["sc.alice"], "role": "normal_user"}
        bob = {"username": "sc.bob", "id": ids["sc.bob"], "role": "normal_user"}
        pa, _ = svc.create_pr({"title": "Alice toner", "department": "IT"},
                              [{"item": "Toner", "qty": 1, "unit_price": 10}], alice, submit=True, priced=True)
        pb, _ = svc.create_pr({"title": "Bob cables", "department": "IT"},
                              [{"item": "Cable", "qty": 1, "unit_price": 10}], bob, submit=True, priced=True)
        c = get_db()
        c.execute("INSERT INTO pr_attachments (pr_id, filename, content_type, size, content_b64, uploaded_by, created_at) "
                  "VALUES (?, 'b.pdf', 'application/pdf', 4, 'JVBERi0=', 'sc.bob', '2026-01-01')", (pb,))
        att_b = c.execute("SELECT id FROM pr_attachments WHERE pr_id=?", (pb,)).fetchone()["id"]
        c.commit()
        c.close()

        assert not svc.sees_all_prs(alice)
        assert svc.can_view_pr(alice, {"requester": "SC.ALICE"}) and not svc.can_view_pr(alice, {"requester": "sc.bob"})

        def client(uid):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
            return cli

        a = client(ids["sc.alice"])
        html = a.get("/procurement/list").get_data(as_text=True)
        assert "Alice toner" in html and "Bob cables" not in html, "list leaks other requests"
        html = a.get("/procurement/list?mine=0").get_data(as_text=True)
        assert "Bob cables" not in html, "mine=0 bypass"
        assert a.get("/procurement/pr/%d" % pa).status_code == 200
        for url in ("/procurement/pr/%d" % pb, "/procurement/pr/%d/pdf" % pb, "/procurement/pr/%d/pr.xlsx" % pb,
                    "/procurement/attachment/%d" % att_b):
            assert a.get(url).status_code == 404, url
        assert a.get("/procurement/analytics").status_code == 403
        x = a.get("/procurement/list.xlsx")
        assert x.status_code == 200
        home = a.get("/procurement/").get_data(as_text=True)
        assert "Bob cables" not in home, "landing page leaks"
        s = a.get("/search?q=Bob").get_data(as_text=True)
        assert "Bob cables" not in s, "global search leaks"

        # A buyer still sees everything.
        b = client(ids["sc.buyer"])
        assert svc.sees_all_prs({"username": "sc.buyer", "id": ids["sc.buyer"], "role": "purchasing_manager"})
        if True:
            html = b.get("/procurement/list").get_data(as_text=True)
            assert "Alice toner" in html and "Bob cables" in html
            assert b.get("/procurement/pr/%d" % pb).status_code == 200
    print("requester scope: OK")


if __name__ == "__main__":
    run()
