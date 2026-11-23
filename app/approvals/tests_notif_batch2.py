"""Procurement notifications, batch 2 (review findings #8 - #13): the steps that
used to tell nobody.

  #8  cancel (pending / on the way) and short-close reach the people involved;
  #9  every goods receipt tells Purchasing, a partial one the requester too,
      quarantine asks Purchasing for a decision, the booker is never told;
  #10 an invoice tells Payables; a failed 3-way match tells Purchasing + recorder;
  #11 fully paid -> Purchasing 'Ready to close'; advance -> Payables; voids and
      reversals -> the requester and the other desk;
  #12 a delegate who cannot sign is refused; add / revoke tell both people;
  #13 an auto-reorder request's news reaches real maintenance people.

Every event is checked for its EXACT recipients, and no requester ever reads an
amount.

    python -m app.approvals.tests_notif_batch2
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="notif_b2_"), "n.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        conn = get_db()
        conn.execute("UPDATE users SET is_active=0")      # a roster this test controls
        def add(u, role, active=1):
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, role, is_active, "
                "created_at) VALUES (?,?,?,?,?,'2026-01-01')", (u, "x", u, role, active))
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            add("n2_" + stage, role)
            signer[stage] = {"username": "n2_" + stage, "role": role}
        add("n2_store2", "storekeeper")
        add("n2_admin", "super_admin")
        add("n2_req", "executive_viewer")
        add("n2_payer", "finance_manager")
        add("n2_payer2", "finance_manager")
        add("n2_outsider", "itsm_user")
        add("n2_cfo_off", "cfo", active=0)
        add("n2_mm", "maintenance_manager")
        add("n2_tech", "maintenance_technician")
        add("n2_treq", "executive_viewer")
        conn.execute("INSERT INTO proc_vendors (name, is_active, created_at) "
                     "VALUES ('N2 Vendor',1,'2026-01-01')")
        conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty, reorder_level, "
                     "max_level, avg_cost, is_active) VALUES ('N2-SP','N2 part',0,0,10000,0,1)")
        sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code='N2-SP'").fetchone()["id"]
        treq_id = conn.execute("SELECT id FROM users WHERE username='n2_treq'").fetchone()["id"]
        tid = conn.execute(
            "INSERT INTO mnt_tickets (ticket_no, requester, requester_user_id, assigned_to, "
            "status, created_at) VALUES ('N2-T1','n2_treq',?,'n2_tech','assigned','2026-01-01')",
            (treq_id,)).lastrowid
        conn.commit()
        PUR = set(svc._perm_holders(conn, "proc_purchasing"))
        PAY = set(svc._perm_holders(conn, "proc_pay"))
        RECV = set(svc._perm_holders(conn, "proc_receive"))
        conn.close()
        reqr = {"username": "n2_req", "id": 9101}
        buyer, store, cfo = signer["purchasing"], signer["warehouse"], signer["cfo"]
        payer = {"username": "n2_payer", "role": "finance_manager"}
        payer2 = {"username": "n2_payer2", "role": "finance_manager"}
        admin = {"username": "n2_admin", "role": "super_admin"}
        assert "n2_purchasing" in PUR and "n2_payer" in PAY and "n2_store2" in RECV
        assert buyer["username"] not in PAY and "n2_req" not in PUR | PAY | RECV
        svc.set_budget("IT", 10000000)

        def q(sql, args=()):
            c = get_db()
            try:
                return [dict(r) for r in c.execute(sql, args).fetchall()]
            finally:
                c.close()

        def mark():
            return q("SELECT COALESCE(MAX(id),0) m FROM notifications")[0]["m"]

        def event(since, pid=None):
            """{title: {recipient}} written after `since`; every row targeted, linked
            to the request, and never two rows of one title for one person."""
            got, seen = {}, set()
            for r in q("SELECT * FROM notifications WHERE id>? AND module='procurement'",
                       (since,)):
                assert r["target_user"], r
                if pid is not None:
                    assert r["link"] == svc._pr_link(pid), r
                key = (r["title"], r["target_user"])
                assert key not in seen, ("duplicate", key)
                seen.add(key)
                got.setdefault(r["title"], set()).add(r["target_user"])
            return got

        def cur_stage(pid):
            r = q("SELECT stage FROM pr_steps WHERE pr_id=? AND status='pending' "
                  "ORDER BY seq LIMIT 1", (pid,))
            return r[0]["stage"] if r else None

        def new_pr(title, who=reqr, spare=False):
            pid, _ = svc.create_pr({"title": title, "department": "IT", "currency": "EGP",
                                    "vendor": "N2 Vendor"},
                                   [{"item": "N2 part" if spare else "Toner", "unit": "Pcs",
                                     "qty": 10, "unit_price": 0,
                                     "spare_id": sid if spare else None}],
                                   who, submit=False, priced=False)
            ok, msg = svc.submit_pr(pid, who)
            assert ok, msg
            return pid, q("SELECT id FROM pr_items WHERE pr_id=?", (pid,))[0]["id"]

        def approve(pid, li):
            ok, msg = svc.price_pr(pid, {li: 100}, {"vendor": "N2 Vendor", "currency": "EGP",
                                                    "tax_rate": 0}, buyer)
            assert ok, msg
            for _ in range(20):
                st = cur_stage(pid)
                if st is None:
                    return
                ok, msg = svc.act_on_step(pid, signer[st], "approve")
                if not ok and msg == "needs_quotes":
                    for v in ("N2 A", "N2 B", "N2 C"):
                        svc.add_quote(pid, {"payment_terms": "30 days", "vendor": v,
                                            "amount": 1000}, buyer)
                    continue
                if not ok and msg == "deviation_memo_required":
                    assert svc.set_doam_document(pid, "deviation_memo",
                                                 "Agreed with the supplier.", buyer)[0]
                    continue
                assert ok, (st, msg)
                if msg == "approved":
                    return
            raise AssertionError("ladder did not move")

        def ordered(title, spare=False):
            pid, li = new_pr(title, spare=spare)
            approve(pid, li)
            ok, msg = svc.issue_po(pid, buyer)
            assert ok, msg
            return pid, li

        req = {"n2_req"}

        # ---- #9 partial receipt, then the rest plus an over-delivery ----------
        pa, la = ordered("Full cycle")
        m = mark()
        ok, msg = svc.receive_items(pa, {la: 4}, store)
        assert ok and msg == "partial", msg
        ev = event(m, pa)
        assert ev == {"Goods received - record the invoice": PUR - {store["username"]},
                      "Partial delivery received": req}, ev
        m = mark()
        ok, msg = svc.receive_items(pa, {la: 8}, store)        # 6 outstanding -> 2 over
        assert ok and msg == "received", msg
        ev = event(m, pa)
        assert ev == {"Over-delivery awaiting decision": PUR - {store["username"]},
                      "Over-delivery quarantined": req,
                      "Goods received - record the invoice": PUR - {store["username"]},
                      "Delivery confirmed": req}, ev

        # ---- #10 a matching invoice -> Payables only ---------------------------
        m = mark()
        ok, msg = svc.add_invoice(pa, {"invoice_no": "N2-INV-1", "amount": 1000}, payer)
        assert ok, msg
        assert svc.three_way_match(pa)["status"] == "matched"
        ev = event(m, pa)
        assert ev == {"Invoice ready for payment": PAY - {"n2_payer"}}, ev

        # ---- #11 fully paid -> Purchasing 'Ready to close' ---------------------
        m = mark()
        ok, msg = svc.add_payment(pa, {"amount": 1000}, payer2)
        assert ok and msg == "paid", msg
        ev = event(m, pa)
        assert ev == {"Ready to close": PUR, "Payment complete": req}, ev
        m = mark()
        ok, msg = svc.close_pr(pa, buyer)                        # full close: nothing to say
        assert ok, msg
        assert event(m, pa) == {}

        # ---- #10 a mismatched invoice, #11 void + receipt reversal, #8 short-close
        pb, lb = ordered("Mismatch", spare=True)
        ok, msg = svc.receive_items(pb, {lb: 5}, store)
        assert ok, msg
        m = mark()
        ok, msg = svc.add_invoice(pb, {"invoice_no": "N2-INV-2", "amount": 1000}, buyer)
        assert ok, msg
        assert svc.three_way_match(pb)["status"] == "mismatch"
        ev = event(m, pb)
        assert ev == {"3-way match mismatch": PUR,
                      "Invoice ready for payment": PAY - PUR}, ev
        inv = q("SELECT id FROM pr_invoices WHERE invoice_no='N2-INV-2'")[0]["id"]
        m = mark()
        assert svc.void_invoice(inv, "wrong supplier number", buyer) == (True, "")
        ev = event(m, pb)
        pb_no = q("SELECT pr_no FROM pr_requests WHERE id=?", (pb,))[0]["pr_no"]
        assert ev == {"Invoice voided: " + pb_no: req | (PAY - {buyer["username"]})}, ev
        m = mark()
        assert svc.reverse_receipt(pb, lb, 2, "counted twice", buyer) == (True, "")
        ev = event(m, pb)
        assert ev == {"Receipt corrected: " + pb_no: req | (PAY - {buyer["username"]})}, ev
        m = mark()
        assert svc.close_pr(pb, buyer) == (False, "reason_required")
        ok, msg = svc.close_pr(pb, buyer, reason="supplier discontinued the part")
        assert ok, msg
        ev = event(m, pb)
        assert ev == {"Request short-closed": req}, ev
        sc = q("SELECT message FROM notifications WHERE title='Request short-closed'")[0]
        assert "supplier discontinued the part" in sc["message"], sc

        # ---- #11 advance authorised -> Payables; payment reversal -> Purchasing
        pc, lc = ordered("Advance")
        m = mark()
        ok, msg = svc.authorize_advance(pc, 20, "", cfo)
        assert ok, msg
        assert event(m, pc) == {"Advance payment authorised": PAY - {cfo["username"]}}
        ok, msg = svc.add_payment(pc, {"amount": 200}, payer2)
        assert ok, msg
        pay = q("SELECT id FROM pr_payments WHERE pr_id=?", (pc,))[0]["id"]
        pc_no = q("SELECT pr_no FROM pr_requests WHERE id=?", (pc,))[0]["pr_no"]
        m = mark()
        assert svc.reverse_payment(pay, "bank returned it", payer2) == (True, "")
        ev = event(m, pc)
        assert ev == {"Payment reversed: " + pc_no: req | (PUR - {"n2_payer2"})}, ev

        # ---- #8 cancel: pending (by Purchasing / by the requester), on the way -
        pd, _ = new_pr("Cancel pending")
        prompted = {r["target_user"] for r in q(
            "SELECT target_user FROM notifications WHERE link=? AND title='New request to sign'",
            (svc._pr_link(pd),))}
        assert {"n2_warehouse", "n2_store2"} <= prompted, prompted
        m = mark()
        ok, msg = svc.cancel_pr(pd, buyer, is_purchasing=True)
        assert ok, msg
        assert event(m, pd) == {"Request cancelled": (req | prompted) - {buyer["username"]}}
        assert not q("SELECT id FROM notifications WHERE link=? AND title='New request to sign' "
                     "AND COALESCE(is_read,0)=0", (svc._pr_link(pd),))
        pd2, _ = new_pr("Cancel own")
        m = mark()
        assert svc.cancel_pr(pd2, reqr) == (True, "")
        assert event(m, pd2) == {"Request cancelled": prompted}      # never the canceller
        pe, _ = ordered("Cancel in transit")
        ok, msg = svc.mark_in_transit(pe, buyer)
        assert ok, msg
        m = mark()
        assert svc.cancel_pr(pe, admin, is_admin=True) == (True, "")
        assert event(m, pe) == {"Request cancelled": (req | RECV) - {"n2_admin"}}

        # ---- #12 delegation: refusal, add (dates + waiting count), revoke ----
        pf, _ = new_pr("Waiting on the store")                 # one request at the store rung
        m = mark()
        assert svc.add_delegation("n2_warehouse", "n2_outsider", None, None, "") \
            == (False, "delegate_cannot_sign")
        assert svc.add_delegation("n2_warehouse", "n2_cfo_off", None, None, "") \
            == (False, "delegate_cannot_sign")
        assert not q("SELECT id FROM proc_delegations")
        ok, msg = svc.add_delegation("n2_warehouse", "n2_finance", "2026-01-01", "2099-12-31", "")
        assert ok, msg
        ev = event(m)
        assert ev == {"You can now sign for n2_warehouse": {"n2_finance"},
                      "Signing delegated to n2_finance": {"n2_warehouse"}}, ev
        queue = [p["id"] for p in svc.my_queue({"username": "n2_warehouse",
                                                "role": signer["warehouse"]["role"]})]
        assert pf in queue, queue                                 # the count is this queue
        for r in q("SELECT message FROM notifications WHERE id>?", (m,)):
            assert "2099-12-31" in r["message"] and "%d request(s)" % len(queue)                 in r["message"], r
        did = q("SELECT id FROM proc_delegations")[0]["id"]
        m = mark()
        svc.revoke_delegation(did)
        assert event(m) == {"Delegation revoked": {"n2_warehouse", "n2_finance"}}
        m = mark()
        svc.revoke_delegation(did)                               # already revoked: silent
        assert event(m) == {}
        assert svc.cancel_pr(pf, reqr)[0]

        # ---- #13 an auto-reorder request reaches real maintenance people ------
        bot = {"username": "auto-reorder", "full_name": "Auto Reorder", "id": None}
        pg, _ = svc.create_pr({"title": "Auto reorder", "department": "IT",
                               "currency": "EGP"},
                              [{"item": "N2 part", "unit": "Pcs", "qty": 5, "unit_price": 0,
                                "spare_id": sid}], bot, submit=True, priced=False)
        c = get_db()
        c.execute("UPDATE pr_requests SET source_module='maintenance', source_ref=? WHERE id=?",
                  ("ticket:%d" % tid, pg))
        c.commit()
        c.close()
        keepers = {r["username"] for r in q("SELECT username FROM users WHERE is_active=1 "
                                            "AND role='storekeeper'")}
        maint = {"n2_mm", "n2_treq", "n2_tech"} | keepers
        m = mark()
        ok, msg = svc.act_on_step(pg, signer["warehouse"], "reject", comment="not needed")
        assert ok, msg
        ev = event(m, pg)
        assert ev == {"Request rejected": maint}, ev
        assert not q("SELECT id FROM notifications WHERE target_user='auto-reorder'")
        # a spare-sweep request (no ticket) still reaches the maintenance desk
        c = get_db()
        c.execute("UPDATE pr_requests SET source_ref='spare:1' WHERE id=?", (pg,))
        pr_g = c.execute("SELECT * FROM pr_requests WHERE id=?", (pg,)).fetchone()
        assert svc._requester_people(c, pr_g) == {"n2_mm"} | keepers
        c.close()

        # ---- global: targeted rows only, and no requester ever reads money ----
        assert not q("SELECT id FROM notifications WHERE module='procurement' "
                     "AND target_user IS NULL")
        assert not q("SELECT id FROM notifications WHERE target_user='n2_outsider'")
        for r in q("SELECT title, message FROM notifications WHERE target_user IN "
                   "('n2_req','n2_treq','n2_tech','n2_mm')"):
            for money in ("1,000", "1000", "200.00", "100.00", "reduced by", "debit note raised"):
                assert money not in r["message"] and money not in r["title"], r
        print("tests_notif_batch2: all checks passed")


if __name__ == "__main__":
    run()
