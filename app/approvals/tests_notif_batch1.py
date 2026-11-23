"""Procurement notifications, batch 1 (review findings #1, #2, #3, #7).

  #1  no procurement bell row is ever written without a target user, and the
      requester never receives an amount (debit-note value, prices);
  #2  a rung nobody may sign alerts the proc admins (role OR per-user grant,
      active only), and its 48h / 96h reminders go to them, not the requester;
  #3  a PARKED rung (sole signer is the requester) never prompts the requester;
  #7  once one holder signs, the other holder's 'New request to sign' is read.

    python -m app.approvals.tests_notif_batch1
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="notif_b1_"), "n.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _ago(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        conn = get_db()
        conn.execute("UPDATE users SET is_active=0")      # a roster this test controls
        def add(u, role, active=1, extra=None):
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role, is_active, "
                "extra_perms, created_at) VALUES (?,?,?,?,?,?,'2026-01-01')",
                (u, "x", u, role, active, extra))
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            add("nb_" + stage, role)
            signer[stage] = {"username": "nb_" + stage, "role": role}
        add("nb_store2", "storekeeper")                    # 2nd holder of the warehouse rung
        add("nb_admin", "super_admin")
        add("nb_admin_off", "super_admin", active=0)
        add("nb_padmin", "executive_viewer", extra='["proc_admin"]')
        add("nb_outsider", "itsm_user")
        add("nb_req", "executive_viewer")
        for v in ("NB Vendor", "NB A", "NB B", "NB C"):
            conn.execute("INSERT INTO proc_vendors (name, is_active, created_at) "
                         "VALUES (?,1,'2026-01-01')", (v,))
        conn.commit()
        for st in signer:
            signer[st]["id"] = conn.execute("SELECT id FROM users WHERE username=?",
                                            (signer[st]["username"],)).fetchone()["id"]
        conn.close()
        reqr = {"username": "nb_req", "id": 9001}
        buyer, store = signer["purchasing"], signer["warehouse"]
        admins = {"nb_admin", "nb_padmin"}

        def q(sql, args=()):
            c = get_db()
            try:
                return [dict(r) for r in c.execute(sql, args).fetchall()]
            finally:
                c.close()

        def rows(pid, title=None, user=None):
            sql, a = "SELECT * FROM notifications WHERE link=?", [svc._pr_link(pid)]
            if title:
                sql, a = sql + " AND title=?", a + [title]
            if user:
                sql, a = sql + " AND target_user=?", a + [user]
            return q(sql, a)

        def cur_steps(pid):
            return q("SELECT s.* FROM pr_steps s JOIN pr_requests p ON p.id=s.pr_id "
                     "WHERE s.pr_id=? AND s.seq=p.current_seq AND s.status='pending'", (pid,))

        def new_pr(title, submit=True, who=reqr):
            pid, _ = svc.create_pr({"title": title, "department": "IT", "currency": "EGP"},
                                   [{"item": "Toner", "unit": "Pcs", "qty": 10,
                                     "unit_price": 0}], who, submit=False, priced=False)
            if submit:
                ok, msg = svc.submit_pr(pid, who)
                assert ok, msg
            return pid

        def price(pid, unit=100):
            li = q("SELECT id FROM pr_items WHERE pr_id=?", (pid,))[0]["id"]
            ok, msg = svc.price_pr(pid, {li: unit}, {"vendor": "NB Vendor", "currency": "EGP",
                                                     "tax_rate": 0}, buyer)
            assert ok, msg
            return li

        def sign_until(pid, stop_stage=None):
            for _ in range(20):
                st = cur_steps(pid)
                if not st or st[0]["stage"] == stop_stage:
                    return
                ok, msg = svc.act_on_step(pid, signer[st[0]["stage"]], "approve")
                if not ok and msg == "needs_quotes":
                    for v in ("NB A", "NB B", "NB C"):
                        svc.add_quote(pid, {"payment_terms": "30 days", "vendor": v, "amount": 1000}, buyer)
                    continue
                assert ok, (st[0]["stage"], msg)
                if msg == "approved":
                    return
            raise AssertionError("ladder did not move")

        svc.set_budget("IT", 10000000)

        # ---- A. full cycle: sign, PO, revise, GRN reject + debit note, quarantine
        pa = new_pr("Full cycle")
        first = {r["target_user"] for r in rows(pa, "New request to sign")}
        assert {"nb_warehouse", "nb_store2"} <= first, first
        assert "nb_req" not in first, first
        ok, msg = svc.act_on_step(pa, store, "approve")
        assert ok, msg
        # #7: the other holder's prompt is cleared the moment one holder signs
        assert all(r["is_read"] for r in rows(pa, "New request to sign", "nb_store2")), \
            rows(pa, "New request to sign", "nb_store2")
        # …while the next rung's fresh prompt is unread
        assert any(not r["is_read"] for r in rows(pa, "New request to sign",
                                                  "nb_warehouse_manager"))
        li = price(pa)
        sign_until(pa)
        assert q("SELECT status FROM pr_requests WHERE id=?", (pa,))[0]["status"] == "approved"
        # every sign prompt of a finished ladder is read
        assert not [r for r in rows(pa, "New request to sign") if not r["is_read"]]
        ok, msg = svc.issue_po(pa, buyer)
        assert ok, msg
        po_to = {r["target_user"] for r in rows(pa, "Purchase Order issued")}
        assert {"nb_req", "nb_warehouse", "nb_store2", "nb_warehouse_manager"} <= po_to, po_to
        assert "nb_outsider" not in po_to
        ok, msg = svc.revise_po(pa, "delivery date moved", buyer)
        assert ok, msg
        assert "nb_req" in {r["target_user"] for r in rows(pa, "PO revised")}
        ok, msg = svc.receive_items(pa, {li: 7}, store, rejects={li: 3},
                                    reject_reason="cracked")
        assert ok, msg
        dn = rows(pa, "Goods rejected — debit note raised")
        dn_to = {r["target_user"] for r in dn}
        assert "nb_purchasing" in dn_to and "nb_finance" in dn_to, dn_to
        assert "nb_req" not in dn_to, dn_to
        assert "300.00" in dn[0]["message"]
        ok, msg = svc.receive_items(pa, {li: 6}, store)          # 3 outstanding -> 3 over
        assert ok, msg
        qr = {r["target_user"] for r in rows(pa, "Over-delivery quarantined")}
        assert "nb_purchasing" in qr, qr
        assert len(rows(pa, "Over-delivery quarantined", "nb_req")) == 1   # not twice

        # ---- B. a rung nobody may sign: admins alerted, not the requester ------
        pb = new_pr("No factory signer")
        price(pb)
        c = get_db()
        c.execute("UPDATE users SET is_active=0 WHERE username=?", ("nb_factory_manager",))
        c.commit()
        c.close()
        sign_until(pb, stop_stage="factory_manager")
        assert cur_steps(pb)[0]["stage"] == "factory_manager"
        alert = {r["target_user"] for r in rows(pb, "Approval rung has no signer")}
        assert alert == admins, alert        # role + extra_perms grant; inactive skipped
        fm_prompt = [r for r in rows(pb, "New request to sign")
                     if "Factory" in r["message"] or "Plant" in r["message"]]
        assert not fm_prompt, fm_prompt
        c = get_db()
        c.execute("UPDATE pr_steps SET activated_at=? WHERE pr_id=? AND stage='factory_manager'",
                  (_ago(50), pb))
        c.commit()
        c.close()
        svc.run_escalations()
        od = {r["target_user"] for r in rows(pb, "Approval overdue")}
        assert od == admins, od              # 48h fallback: admins, never the requester
        c = get_db()
        c.execute("UPDATE pr_steps SET activated_at=? WHERE pr_id=? AND stage='factory_manager'",
                  (_ago(100), pb))
        c.commit()
        c.close()
        svc.run_escalations()
        l2 = rows(pb, "Approval overdue (level 2)")
        assert sorted(r["target_user"] for r in l2) == sorted(admins), l2   # once each
        # admins cannot sign it (no admin bypass); once a role holder is back and
        # signs the orphan rung, every admin's overdue alerts go read (#7)
        ok, msg = svc.act_on_step(pb, {"username": "nb_admin", "role": "super_admin"}, "approve")
        assert not ok, msg
        c = get_db()
        c.execute("UPDATE users SET is_active=1 WHERE username=?", ("nb_factory_manager",))
        c.commit()
        c.close()
        ok, msg = svc.act_on_step(pb, signer["factory_manager"], "approve")
        assert ok, msg
        stale = [r for r in rows(pb) if not r["is_read"]
                 and r["title"] in ("Approval overdue", "Approval overdue (level 2)")]
        assert not stale, stale

        # ---- C. a parked rung never prompts the requester (#3) ----------------
        c = get_db()
        c.execute("UPDATE proc_escalations SET superior_role='' WHERE role_key='storekeeper'")
        c.execute("UPDATE users SET is_active=0 WHERE username IN "
                  "('nb_warehouse','nb_warehouse_manager')")
        c.commit()
        c.close()
        sk = {"username": "nb_store2", "id": 9002, "role": "storekeeper"}
        pc = new_pr("Parked warehouse", who=sk)
        st = cur_steps(pc)
        assert st[0]["stage"] == "warehouse" and st[0]["esc_role"] == "", st
        assert not rows(pc, "New request to sign", "nb_store2")
        needs = {r["target_user"] for r in rows(pc, "Approval rung needs a signer")}
        assert needs == admins, needs
        assert not rows(pc, "Approval rung has no signer")       # told once, not twice
        c = get_db()
        c.execute("UPDATE pr_steps SET activated_at=? WHERE pr_id=? AND stage='warehouse'",
                  (_ago(50), pc))
        c.commit()
        c.close()
        svc.run_escalations()
        assert {r["target_user"] for r in rows(pc, "Approval overdue")} == admins
        assert not rows(pc, None, "nb_store2") or all(
            r["title"] not in svc._SIGN_PROMPTS for r in rows(pc, None, "nb_store2"))

        # ---- D. send-back / reject / cancel clear the stale prompts -----------
        c = get_db()
        c.execute("UPDATE users SET is_active=1 WHERE username IN "
                  "('nb_warehouse','nb_warehouse_manager')")
        c.commit()
        c.close()
        pd = new_pr("Rejected")
        ok, msg = svc.act_on_step(pd, store, "reject", comment="no")
        assert ok, msg
        assert not [r for r in rows(pd, "New request to sign") if not r["is_read"]]
        pe = new_pr("Cancelled")
        ok, msg = svc.cancel_pr(pe, reqr)
        assert ok, msg
        assert not [r for r in rows(pe, "New request to sign") if not r["is_read"]]

        # ---- global: no broadcast row, no amount ever reaches the requester ---
        nulls = q("SELECT title FROM notifications WHERE module='procurement' "
                  "AND target_user IS NULL")
        assert not nulls, nulls
        assert not q("SELECT id FROM notifications WHERE target_user='nb_outsider'")
        for r in q("SELECT title, message FROM notifications WHERE target_user='nb_req'"):
            assert "debit note raised" not in r["title"], r
            for money in ("300.00", "1,000", "1000", "reduced by"):
                assert money not in r["message"], r
        print("tests_notif_batch1: all checks passed")


if __name__ == "__main__":
    run()
