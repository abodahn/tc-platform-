"""2026-11-26 fixes: change-requester is super-admin only; quotes/attachments
gated; duplicate and NaN payments refused; debit-note settlement needs proof
and Finance; My Work flags the requester's own draft/rejected requests.

    python -m app.approvals.tests_quick_nov26
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "quick26.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    from flask import g
    # The test runs inside one app context, so g (and current_user's cache on it)
    # would outlive a request and the next client would act as the previous user.
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import services as svc
        from app.db import get_db

        c = get_db()
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        ids = {}
        for un, role in (("q.itdir", "it_director"), ("q.staff", "normal_user"),
                         ("q.other", "normal_user"), ("q.fin", "finance_manager")):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, un, role))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        c.commit()
        c.close()
        staff = {"username": "q.staff", "id": ids["q.staff"], "role": "normal_user"}
        pid, _ = svc.create_pr({"title": "Quick", "department": "IT"},
                               [{"item": "Toner", "qty": 1, "unit_price": 100}], staff, submit=False, priced=True)

        def client(uid):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
            return cli

        # -- change requester: super admin only (it_director holds proc_admin)
        it = client(ids["q.itdir"])
        assert "proc.req_change.title" not in it.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        r = it.post("/procurement/pr/%d/requester" % pid, data={"_csrf": "tok", "username": "q.other", "reason": "x"})
        assert r.status_code == 403, (r.status_code, r.get_data(as_text=True)[:300])
        apid, _ = svc.create_pr({"title": "Admin's", "department": "IT"},
                                [{"item": "Toner", "qty": 1, "unit_price": 100}], admin, submit=False, priced=True)
        assert "proc.req_change.title" in client(admin["id"]).get("/procurement/pr/%d" % apid).get_data(as_text=True)

        # -- #2 quotes and attachments
        other = client(ids["q.other"])
        r = other.post("/procurement/pr/%d/quote" % pid, data={"_csrf": "tok", "vendor": "X", "amount": "5"})
        assert r.status_code == 403, r.status_code                       # not a buyer
        r = other.post("/procurement/pr/%d/attach" % pid, data={"_csrf": "tok"})
        assert r.status_code == 403                                      # not their request
        r = client(ids["q.staff"]).post("/procurement/pr/%d/attach" % pid, data={"_csrf": "tok"})
        assert r.status_code == 302                                      # owner may (no file -> message)
        c = get_db()
        c.execute("UPDATE pr_requests SET status='approved' WHERE id=?", (pid,))
        c.commit()
        c.close()
        assert svc.add_quote(pid, {"vendor": "V", "amount": "5", "payment_terms": "30 days"}, admin,
                             filename="q.pdf", content_type="application/pdf",
                             content_b64="JVBERi0xLjQK") == (False, "locked")

        # -- #7 money values
        assert svc._money("nan") is None and svc._money("-1") is None and svc._money("1,500") is None
        assert svc._money("inf") is None and svc._money("12.5") == 12.5 and svc._money("") == 0
        c = get_db()
        c.execute("UPDATE pr_requests SET status='po_issued' WHERE id=?", (pid,))
        c.commit()
        c.close()
        for bad in ("nan", "-5", "abc", "inf"):
            assert svc.add_payment(pid, {"amount": bad}, admin) == (False, "bad_amount"), bad
        try:
            svc.set_budget("IT", "nan")
            raise AssertionError("NaN budget accepted")
        except ValueError:
            pass

        # -- #1 duplicate payment within two minutes
        c = get_db()
        c.execute("INSERT INTO pr_payments (pr_id, amount, reference, created_by, created_at) "
                  "VALUES (?, 40, 'TRX-1', ?, ?)", (pid, admin["username"], svc._now()))
        c.commit()
        assert svc._repeat_payment(c, pid, 40.0, "TRX-1", admin["username"])
        assert not svc._repeat_payment(c, pid, 40.0, "TRX-2", admin["username"])    # another reference
        assert not svc._repeat_payment(c, pid, 40.0, "TRX-1", "q.fin")              # another person
        c.execute("UPDATE pr_payments SET created_at='2026-01-01 00:00:00' WHERE pr_id=?", (pid,))
        c.commit()
        assert not svc._repeat_payment(c, pid, 40.0, "TRX-1", admin["username"])    # long ago
        c.close()

        # -- #4 debit-note settlement
        c = get_db()
        c.execute("INSERT INTO pr_returns (dn_no, pr_id, qty, total, status) VALUES ('DN-Q1', ?, 1, 50, 'open')", (pid,))
        c.commit()
        rid = c.execute("SELECT id FROM pr_returns WHERE dn_no='DN-Q1'").fetchone()["id"]
        c.close()
        assert svc.settle_return(rid, admin) == (False, "settle_type_required")
        assert svc.settle_return(rid, admin, settle_type="credit_note") == (False, "settle_ref_required")
        assert svc.settle_return(rid, admin, settle_type="credit_note", ref="CN-9",
                                 amount="nan") == (False, "settle_amount_required")
        r = client(ids["q.itdir"]).post("/procurement/return/%d/settle" % rid, data={"_csrf": "tok"})
        assert r.status_code == 403                                      # proc_pay only
        assert svc.settle_return(rid, admin, settle_type="credit_note", ref="CN-9", amount="50")[0]
        c = get_db()
        row = c.execute("SELECT status, settle_type, settle_ref, settle_amount FROM pr_returns WHERE id=?", (rid,)).fetchone()
        c.close()
        assert (row["status"], row["settle_type"], row["settle_ref"], row["settle_amount"]) == \
            ("settled", "credit_note", "CN-9", 50.0)

        # -- #11 My Work: a draft counts as waiting on the requester
        pid2, _ = svc.create_pr({"title": "Draft one", "department": "IT"},
                                [{"item": "Cable", "qty": 1, "unit_price": 10}], staff, submit=False, priced=True)
        html = client(ids["q.staff"]).get("/my-work").get_data(as_text=True)
        assert "mywork.needs_you" in html and "mywork.all_clear" not in html, "draft not flagged"
    print("quick fixes 2026-11-26: OK")


if __name__ == "__main__":
    run()
