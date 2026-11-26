"""Waiting for Purchasing (quotes): a rung past a signed Purchasing rung that the
competitive-quote rule blocks.

  * the request page says so (banner with the gate's own numbers), the current
    approver's Approve is disabled with the reason, Purchasing gets a jump link;
  * nothing shows before Purchasing, at Purchasing, or once the rule holds;
  * act_on_step still refuses — the screen is not the guard;
  * Purchasing — and only Purchasing — is told: the person who signed THIS
    request's Purchasing rung, else the Purchasing-stage signers when that
    person is inactive; at most once per request per 24 hours;
  * the approver's queue (/procurement and /my-work) carries the badge.

    python -m app.approvals.tests_quotewait
"""
import os
import sys
import tempfile
from pathlib import Path

TITLE = "Quotes needed before the next signature"
PDF = "JVBERi0xLjQK"


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "quotewait.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    from flask import g
    # One app context for the whole test: drop the cached user between requests.
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import services as svc
        from app.approvals.notif_text import localise
        from app.db import get_db

        def q(sql, *a):
            c = get_db()
            try:
                return [dict(r) for r in c.execute(sql, a).fetchall()]
            finally:
                c.close()

        def run_sql(sql, *a):
            c = get_db()
            try:
                c.execute(sql, a)
                c.commit()
            finally:
                c.close()

        for un, role in (("qw.staff", "normal_user"), ("qw.buyer2", "purchasing_manager")):
            run_sql("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                    "VALUES (?, 'x', ?, ?, 1, '2026-01-01')", un, un, role)
        uid = {r["username"]: r["id"] for r in q("SELECT id, username FROM users")}
        user = {r["username"]: r for r in q("SELECT * FROM users")}
        staff = user["qw.staff"]

        def client(un):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid[un], 0, "tok"
            return cli

        def page(un, pid):
            return client(un).get("/procurement/pr/%d" % pid).get_data(as_text=True)

        def new_pr(price):
            pid, _ = svc.create_pr({"title": "Quote wait", "department": "IT"},
                                   [{"item": "Server", "qty": 1, "unit_price": price}],
                                   staff, priced=True)
            return pid

        def past_purchasing(pid):
            """Sign every rung up to and including Purchasing (by 'purchasing')."""
            steps = q("SELECT id, seq, stage FROM pr_steps WHERE pr_id=? ORDER BY seq", pid)
            pseq = next(s["seq"] for s in steps if s["stage"] == "purchasing")
            for s in steps:
                if s["seq"] <= pseq:
                    run_sql("UPDATE pr_steps SET status='approved', approver_user=?, "
                            "acted_at=? WHERE id=?",
                            "purchasing" if s["stage"] == "purchasing" else "st%d" % s["id"],
                            svc._now(), s["id"])
            nxt = min(s["seq"] for s in steps if s["seq"] > pseq)
            run_sql("UPDATE pr_requests SET current_seq=? WHERE id=?", nxt, pid)
            return next(s["stage"] for s in steps if s["seq"] == nxt)

        def quote(pid, vendor, with_file=True, chosen=0):
            run_sql("INSERT INTO pr_quotes (pr_id, vendor, amount, currency, filename, content_type, "
                    "content_b64, uploaded_by, created_at, payment_terms, is_chosen) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    pid, vendor, 1000, "EGP", "q.pdf" if with_file else None,
                    "application/pdf" if with_file else None, PDF if with_file else None,
                    "purchasing", svc._now(), "30 days", chosen)

        def wait(pid):
            c = get_db()
            try:
                pr = c.execute("SELECT * FROM pr_requests WHERE id=?", (pid,)).fetchone()
                return svc.quote_wait(c, pr)
            finally:
                c.close()

        def notices(pid=None):
            rows = q("SELECT target_user, message FROM notifications WHERE title=?", TITLE)
            return rows if pid is None else [r for r in rows if r["message"].startswith(
                q("SELECT pr_no FROM pr_requests WHERE id=?", pid)[0]["pr_no"] + " ")]

        # ---- 1. before Purchasing and AT Purchasing: nothing --------------------
        pid = new_pr(100000)                         # DOAM band: 3 quotes
        assert wait(pid) is None, "shown before Purchasing"
        assert "proc.quotewait.badge" not in page("factory", pid)
        pseq = q("SELECT seq FROM pr_steps WHERE pr_id=? AND stage='purchasing'", pid)[0]["seq"]
        for s in q("SELECT id FROM pr_steps WHERE pr_id=? AND seq<?", pid, pseq):
            run_sql("UPDATE pr_steps SET status='approved', approver_user='x' WHERE id=?", s["id"])
        run_sql("UPDATE pr_requests SET current_seq=? WHERE id=?", pseq, pid)
        assert wait(pid) is None, "shown on Purchasing's own rung"
        ok, msg = svc.act_on_step(pid, user["purchasing"], "approve")
        assert (ok, msg) == (False, "needs_quotes"), (ok, msg)
        assert not notices(pid), "refusal on Purchasing's own rung must not notify"

        # ---- 2. past Purchasing, one quote of three: banner with the numbers ----
        stage = past_purchasing(pid)
        quote(pid, "Alpha")
        w = wait(pid)
        assert w and w["msg"] == "needs_quotes" and (w["required"], w["have"]) == (3, 1), w
        assert w["stage"] == stage
        approver = next(u for u in ("factory", "cfo", "ceo")
                        if svc.can_act_step(user[u], {"stage": stage, "pr_id": pid}))
        html = page(approver, pid)
        assert "proc.quotewait.badge" in html and "proc.quotewait.needs_a" in html, "no banner"
        assert "<bdi>3</bdi>" in html and "<bdi>1</bdi>" in html, "banner numbers"
        assert 'id="prQwNote"' in html and 'disabled aria-describedby="prQwNote"' in html, \
            "the approver's Approve must say why"
        assert 'href="#prQuotes" data-i18n="proc.quotewait.fix"' not in html, "fix link for a non-buyer"
        html = page("purchasing", pid)
        assert 'href="#prQuotes" data-i18n="proc.quotewait.fix"' in html, "Purchasing gets the jump link"
        assert "proc.quotewait.badge" in page("qw.staff", pid), "requester sees why it waits"

        # queue badges, only for the approver's rows
        assert "proc.quotewait.badge" in client(approver).get("/procurement/").get_data(as_text=True)
        assert "proc.quotewait.badge" in client(approver).get("/my-work").get_data(as_text=True)

        # ---- 3. refusal stays; the Purchasing signer is told once --------------
        ok, msg = svc.act_on_step(pid, user[approver], "approve")
        assert (ok, msg) == (False, "needs_quotes"), (ok, msg)
        n = notices(pid)
        assert [r["target_user"] for r in n] == ["purchasing"], n
        assert "3 quotes" in n[0]["message"] and "(it has 1)" in n[0]["message"], n
        ev = q("SELECT actor, detail FROM pr_events WHERE pr_id=? AND action='quotes_needed_notice'", pid)
        assert len(ev) == 1 and "Purchasing told: purchasing" in ev[0]["detail"], ev
        assert svc.act_on_step(pid, user[approver], "approve") == (False, "needs_quotes")
        svc.run_escalations()
        assert len(notices(pid)) == 1, "deduped within 24 hours"
        t, m = localise(TITLE, n[0]["message"], "ar")
        assert t != TITLE and m != n[0]["message"] and "مش هيتوقّع" in m, (t, m)
        t, m = localise(TITLE, n[0]["message"], "tr")
        assert t != TITLE and "imzalanamaz" in m, (t, m)

        # 24h later the periodic pass tells them again
        run_sql("UPDATE pr_events SET created_at='2020-01-01 00:00:00' WHERE action='quotes_needed_notice'")
        svc.run_escalations()
        assert [r["target_user"] for r in notices(pid)] == ["purchasing", "purchasing"]

        # signer inactive -> the Purchasing-stage signers instead
        run_sql("UPDATE pr_events SET created_at='2020-01-01 00:00:00' WHERE action='quotes_needed_notice'")
        run_sql("UPDATE users SET is_active=0 WHERE username='purchasing'")
        svc.run_escalations()
        n = notices(pid)
        assert [r["target_user"] for r in n][2:] == ["qw.buyer2"], n
        run_sql("UPDATE users SET is_active=1 WHERE username='purchasing'")
        assert {r["target_user"] for r in notices()} <= {"purchasing", "qw.buyer2"}, \
            "nobody but Purchasing is told"

        # ---- 4. Purchasing adds the quotes -> banner gone ------------------------
        quote(pid, "Beta")
        quote(pid, "Gamma")
        assert wait(pid) is None
        html = page(approver, pid)
        assert "proc.quotewait.badge" not in html and 'id="prQwNote"' not in html

        # ---- 5. single-source justification clears it; a file-less quote does not
        pid2 = new_pr(100000)
        past_purchasing(pid2)
        assert wait(pid2)["msg"] == "needs_quotes"
        assert svc.set_single_source(pid2, "OEM only", user["purchasing"])[0]
        assert wait(pid2) is None, "single-source reason must clear the banner"
        quote(pid2, "Alpha", with_file=False)
        assert wait(pid2)["msg"] == "needs_quote_file"
        assert "proc.quotewait.no_file" in page(approver, pid2)
        assert svc.act_on_step(pid2, user[approver], "approve") == (False, "needs_quote_file")
        assert len(notices(pid2)) == 1

        # ---- 6. chosen quotation without its file --------------------------------
        pid3 = new_pr(10000)                         # one quote is enough
        past_purchasing(pid3)
        quote(pid3, "Alpha")
        assert wait(pid3) is None, "rule holds: one filed quote"
        quote(pid3, "Beta", with_file=False, chosen=1)
        w3 = wait(pid3)
        assert w3 and w3["msg"] == "chosen_quote_no_file", w3
        a3 = next(u for u in ("factory", "cfo", "ceo", "finance")
                  if svc.can_act_step(user[u], {"stage": w3["stage"], "pr_id": pid3}))
        assert "proc.quotewait.chosen_no_file" in page(a3, pid3)
        assert svc.act_on_step(pid3, user[a3], "approve") == (False, "chosen_quote_no_file")
        assert [r["target_user"] for r in notices(pid3)] == ["purchasing"]

        # a request no longer pending shows nothing
        run_sql("UPDATE pr_requests SET status='rejected' WHERE id=?", pid3)
        assert wait(pid3) is None
    print("quote wait (waiting for Purchasing): OK")


if __name__ == "__main__":
    run()
