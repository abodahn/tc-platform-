"""Delivery follow-up: "required by" on the request form, a promised date per
purchase order, overdue badges / list / My Work / bell (once, then weekly), the
scorecard's on-time metric on the promised date, and cancelling ONE supplier's
order on a split request (receiving, invoices and payments refuse it after).

    python -m app.approvals.tests_delivery
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "delivery.db")
    config.Config.SMTP_HOST = ""          # no mail leaves a test run
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    from flask import g
    # One app context for the whole run: without this the next test client would
    # act as the previous user (current_user caches on g).
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import services as svc, delivery as dl
        from app.approvals import vendor_score as VS
        from app.approvals.messages import MESSAGES
        from app.db import get_db

        today = datetime.now(timezone.utc).date()
        day = lambda n: (today + timedelta(days=n)).isoformat()     # noqa: E731

        c = get_db()
        ids = {}
        for un, role in (("dl.buyer", "purchasing_manager"), ("dl.req", "normal_user"),
                         ("dl.store", "storekeeper"), ("dl.fin", "finance_manager")):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, "
                      "created_at) VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, un, role))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        c.commit()
        c.close()
        U = {k: {"username": k, "id": v, "role": r} for (k, v), r in zip(
            ids.items(), ("purchasing_manager", "normal_user", "storekeeper", "finance_manager"))}
        buyer, req, store, fin = U["dl.buyer"], U["dl.req"], U["dl.store"], U["dl.fin"]
        svc.create_vendor({"name": "DLV Alpha", "email": "alpha@example.test"}, buyer)
        svc.create_vendor({"name": "DLV Beta", "email": "beta@example.test"}, buyer)

        def client(uid):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
            return cli

        def q(sql, *a):
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, a).fetchall()]
            finally:
                conn.close()

        def x(sql, *a):
            conn = get_db()
            try:
                conn.execute(sql, a)
                conn.commit()
            finally:
                conn.close()

        def split_pr(title, due=None, lead=None):
            pid, _ = svc.create_pr(
                {"title": title, "department": "IT", "req_del_date": due},
                [{"item": "Bolt", "qty": 2, "unit_price": 100, "vendor": "DLV Alpha"},
                 {"item": "Nut", "qty": 3, "unit_price": 50, "vendor": "DLV Beta"}],
                req, submit=False, priced=True)
            # Suppliers a buyer adds start pending; Finance approval is not what this test is about.
            x("UPDATE proc_vendors SET status='approved' WHERE name LIKE 'DLV %'")
            if lead is not None:
                x("INSERT INTO pr_quotes (pr_id, vendor, amount, lead_time_days, is_chosen) "
                  "VALUES (?, 'DLV Alpha', 200, ?, 1)", pid, lead)
            x("UPDATE pr_requests SET status='approved' WHERE id=?", pid)
            ok, msg = svc.issue_po(pid, buyer)
            assert ok, msg
            return pid, {p["vendor"]: p for p in q(
                "SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", pid)}

        # -- (A) required-by date: validation + the form posts it
        assert dl.req_date_refusal("") is None and dl.req_date_refusal(day(3)) is None
        assert dl.req_date_refusal(day(-1)) == "The required-by date cannot be in the past."
        assert dl.req_date_refusal("31/12/2026") == "Enter the required-by date as a valid date."
        assert dl.req_date_refusal(day(-9), existing=day(-9)) is None      # unchanged on edit
        r_cli = client(ids["dl.req"])
        form = {"_csrf": "tok", "title": "Wish date", "department": "IT", "item[]": "Toner",
                "description[]": "", "qty[]": "1", "unit[]": "Pcs", "action": "draft"}
        n0 = len(q("SELECT id FROM pr_requests"))
        r = r_cli.post("/procurement/new", data=dict(form, req_del_date=day(-2)))
        assert r.status_code == 302 and len(q("SELECT id FROM pr_requests")) == n0, "past date accepted"
        r_cli.post("/procurement/new", data=dict(form, req_del_date=day(10)))
        row = q("SELECT req_del_date FROM pr_requests ORDER BY id DESC LIMIT 1")[0]
        assert row["req_del_date"] == day(10), row
        assert "proc.delivery.req_by" in r_cli.get("/procurement/new").get_data(as_text=True)

        # -- promised date at issue: quote lead time, else the required-by date
        pid, pos = split_pr("Split one", due=day(20), lead=5)
        a, b = pos["DLV Alpha"], pos["DLV Beta"]
        assert a["promised_date"] == day(5), a["promised_date"]
        assert b["promised_date"] == day(20), b["promised_date"]
        pid0, _ = svc.create_pr({"title": "No dates", "department": "IT"},
                                [{"item": "Pen", "qty": 1, "unit_price": 5, "vendor": "DLV Alpha"}],
                                req, submit=False, priced=True)
        x("UPDATE pr_requests SET status='approved' WHERE id=?", pid0)
        assert svc.issue_po(pid0, buyer)[0]
        single = q("SELECT * FROM pr_purchase_orders WHERE pr_id=?", pid0)[0]
        assert single["promised_date"] is None          # no promise -> never overdue

        # -- buyer edits the promised date (proc_purchasing only, audited)
        b_cli = client(ids["dl.buyer"])
        assert r_cli.post("/procurement/po/%d/promised" % a["id"],
                          data={"_csrf": "tok", "promised_date": day(1)}).status_code == 403
        assert dl.set_promised(a["id"], "soon", buyer)[1] == "bad_date"
        r = b_cli.post("/procurement/po/%d/promised" % a["id"],
                       data={"_csrf": "tok", "promised_date": day(-3)}, follow_redirects=True)
        assert "Promised delivery date saved." in r.get_data(as_text=True)
        assert q("SELECT promised_date FROM pr_purchase_orders WHERE id=?", a["id"])[0]["promised_date"] == day(-3)
        assert q("SELECT 1 FROM pr_events WHERE pr_id=? AND action='po_promised'", pid)

        # -- overdue: badge on the page, the list, My Work, the deliveries view
        assert dl.late_map()[pid]["days"] == 3
        html = b_cli.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert "proc.delivery.title" in html and "proc.delivery.overdue" in html
        assert "proc.delivery.cancel_po" in html                       # buyer: per-order cancel
        rhtml = r_cli.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert "proc.delivery.title" in rhtml and "proc.delivery.cancel_po" not in rhtml
        assert "proc.delivery.overdue" in b_cli.get("/procurement/list").get_data(as_text=True)
        s_cli = client(ids["dl.store"])
        mw = s_cli.get("/my-work").get_data(as_text=True)
        assert "proc.delivery.overdue" in mw and day(-3) in mw
        top = dl.recv_queue(get_db())[0]
        assert top["id"] == pid and top["days_late"] == 3, top
        assert r_cli.get("/procurement/deliveries").status_code == 403
        dv = b_cli.get("/procurement/deliveries?late=1").get_data(as_text=True)
        assert a["po_no"] in dv and b["po_no"] not in dv

        # -- bell: once, then not again until a week has passed
        assert dl.run_overdue_alerts() == 1
        bell = q("SELECT target_user FROM notifications WHERE title='Delivery overdue'")
        assert {"dl.buyer", "dl.req"} <= {n["target_user"] for n in bell}, bell
        assert dl.run_overdue_alerts() == 0
        x("UPDATE pr_purchase_orders SET overdue_notified_at=? WHERE id=?",
          day(-8) + " 08:00:00", a["id"])
        assert dl.run_overdue_alerts() == 1

        # -- (B) cancel ONE supplier's order
        assert r_cli.post("/procurement/po/%d/cancel" % b["id"],
                          data={"_csrf": "tok", "reason": "x"}).status_code == 403
        assert dl.cancel_po(b["id"], "", buyer)[1] == "reason_required"
        assert dl.cancel_po(single["id"], "x", buyer)[1] == "not_split"    # one order = cancel_pr
        x("INSERT INTO pr_payments (pr_id, po_id, amount, created_at) VALUES (?,?,10,'2026-01-01')",
          pid, b["id"])
        assert dl.cancel_po(b["id"], "x", buyer)[1] == "has_payments"
        x("INSERT INTO pr_payments (pr_id, po_id, amount, created_at) VALUES (?,?,-10,'2026-01-01')",
          pid, b["id"])                                                    # reversed: nets to 0
        x("UPDATE pr_requests SET po_sent_at=? WHERE id=?", svc._now(), pid)
        html = b_cli.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert 'name="email_vendor"' in html                               # PO was emailed
        r = b_cli.post("/procurement/po/%d/cancel" % b["id"], follow_redirects=True,
                       data={"_csrf": "tok", "reason": "Supplier cannot deliver", "email_vendor": "1"})
        assert "SMTP" in r.get_data(as_text=True), "no-SMTP case not said"
        st = {p["vendor"]: p["status"] for p in q(
            "SELECT vendor, status FROM pr_purchase_orders WHERE pr_id=?", pid)}
        assert st == {"DLV Alpha": "issued", "DLV Beta": "cancelled"}, st
        pr = q("SELECT status, budget_released FROM pr_requests WHERE id=?", pid)[0]
        assert pr["status"] == "po_issued" and abs(pr["budget_released"] - 150) < 0.01, pr
        assert q("SELECT 1 FROM notifications WHERE title='Purchase order cancelled' "
                 "AND target_user='dl.req'")
        assert dl.cancel_po(b["id"], "again", buyer)[1] == "already_cancelled"
        nut = q("SELECT id FROM pr_items WHERE pr_id=? AND item='Nut'", pid)[0]["id"]
        bolt = q("SELECT id FROM pr_items WHERE pr_id=? AND item='Bolt'", pid)[0]["id"]
        assert dl.cancelled_item_ids(get_db(), pid) == {nut}
        assert svc.receive_items(pid, {nut: 1}, store) == (False, "po_cancelled")
        assert svc.add_payment(pid, {"amount": "5", "po_id": b["id"]}, fin, force=True) \
            == (False, "po_cancelled")
        assert svc.add_invoice(pid, {"invoice_no": "INV-DL1", "amount": "5", "tax": "0",
                                     "po_id": b["id"]}, buyer) == (False, "po_cancelled")
        # ...nor at the warehouse door: refused before any stock moves.
        from app.warehouse import services as wsvc
        assert wsvc.create_material({"code": "DLV-NUT", "name": "Nut", "kind": "trim",
                                     "uom": "pcs"}, store)[0]
        nut_mat = q("SELECT id, stock_qty FROM wh_materials WHERE code='DLV-NUT'")[0]
        pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", pid)[0]["pr_no"]
        assert wsvc.po_line_for(pr_no, nut_mat["id"]) == {"cancelled": True, "pr_id": pid}
        r = client(ids["dl.store"]).post("/warehouse/receive", follow_redirects=True, data={
            "_csrf": "tok", "material_id": str(nut_mat["id"]), "qty": "3",
            "unit_cost": "50", "grn_ref": pr_no})
        assert "That purchase order was cancelled." in r.get_data(as_text=True)
        assert q("SELECT stock_qty FROM wh_materials WHERE id=?",
                 nut_mat["id"])[0]["stock_qty"] == nut_mat["stock_qty"], "stock booked"
        # The PO panel marks the cancelled order and offers no PDF for it.
        html = b_cli.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        # PO document links end "po=<id>&amp;view=1" (they open in the in-app viewer)
        assert ('po=%d&amp;view=1"' % a["id"]) in html and ('po=%d&amp;view=1"' % b["id"]) not in html
        ui = svc.labels("ar")["ui"]
        assert ui["po_cancelled_flash"] and ui["po_cancelled_flash"] != svc.labels("en")["ui"]["po_cancelled_flash"]
        # The rest of the order arrives: the cancelled lines do not hold it open.
        assert svc.receive_items(pid, {bolt: 2}, store)[0]
        assert q("SELECT status FROM pr_requests WHERE id=?", pid)[0]["status"] == "received"
        assert dl.run_overdue_alerts() == 0 and pid not in dl.late_map()
        # The match forgets the cancelled order: nothing short, ceiling = live order.
        m = svc.three_way_match(pid)
        assert m["qty_ok"] and m["short_qty"] == 0, m["flags"]
        full = svc.pr_amounts(q("SELECT * FROM pr_requests WHERE id=?", pid)[0])["grand"]
        assert abs(m["ordered_grand"] - (full - b["grand"])) < 0.01, m["ordered_grand"]

        # -- scorecard: arrived today against a promise 3 days ago = late, even
        #    though the requester's own date (day 20) was still ahead.
        card = VS.scorecards(min_sample=1, today=today)
        alpha = next(v for k, v in card.items() if "alpha" in k)
        assert alpha["on_time"]["pct"] == 0.0, alpha["on_time"]

        # -- every order cancelled -> the request is cancelled, as cancel_pr leaves it
        pid2, pos2 = split_pr("Split two")
        # A debit note under another spelling of the vendor still blocks the cancel.
        x("INSERT INTO pr_returns (dn_no, pr_id, vendor, total, status) "
          "VALUES ('DN-DLV-1', ?, ' dlv alpha ', 10, 'open')", pid2)
        assert dl.cancel_po(pos2["DLV Alpha"]["id"], "dup", buyer)[1] == "already_received"
        x("DELETE FROM pr_returns WHERE dn_no='DN-DLV-1'")
        assert dl.cancel_po(pos2["DLV Alpha"]["id"], "dup", buyer)[0]
        assert q("SELECT status FROM pr_requests WHERE id=?", pid2)[0]["status"] == "po_issued"
        # The last live order = the whole request: an admin decision, as in cancel_pr.
        beta2 = pos2["DLV Beta"]["id"]
        assert dl.cancel_po(beta2, "dup", buyer)[1] == "needs_admin"
        r = b_cli.post("/procurement/po/%d/cancel" % beta2, follow_redirects=True,
                       data={"_csrf": "tok", "reason": "dup"})
        assert "only an administrator" in r.get_data(as_text=True)
        assert not next(p for p in dl.po_states(pid2) if p["id"] == beta2)["can_cancel"]
        assert next(p for p in dl.po_states(pid2, is_admin=True) if p["id"] == beta2)["can_cancel"]
        assert q("SELECT status FROM pr_requests WHERE id=?", pid2)[0]["status"] == "po_issued"
        ok, code, _ = dl.cancel_po(beta2, "dup", buyer, is_admin=True)
        assert ok and code == "cancelled"                       # never emailed: no mail offered
        assert q("SELECT status FROM pr_requests WHERE id=?", pid2)[0]["status"] == "cancelled"

        # -- SMTP set up but the send fails: said as such, not as "no SMTP"
        import config
        from app.services import alerts as AL
        pid3, pos3 = split_pr("Split three")
        x("UPDATE pr_requests SET po_sent_at=? WHERE id=?", svc._now(), pid3)
        old_host, old_send = config.Config.SMTP_HOST, AL.send_email_to
        config.Config.SMTP_HOST, AL.send_email_to = "smtp.example.test", lambda *a, **k: False
        try:
            assert dl.cancel_po(pos3["DLV Alpha"]["id"], "x", buyer)[:2] == (True, "send_failed")
        finally:
            config.Config.SMTP_HOST, AL.send_email_to = old_host, old_send

        # -- an order issued before promised_date existed is dated on first read
        pid4, pos4 = split_pr("Legacy order", lead=4)
        x("UPDATE pr_purchase_orders SET promised_date=NULL, issued_at=? WHERE pr_id=?",
          day(-10) + " 09:00:00", pid4)
        late4 = {p["vendor"]: p for p in dl.open_orders(get_db(), pid4)}
        assert late4["DLV Alpha"]["promised_date"] == day(-6) and late4["DLV Alpha"]["days_late"] == 6
        assert late4["DLV Beta"]["promised_date"] is None      # nothing to date it from

        # -- open-orders page: translated title, labelled cells for the phone layout
        dv = b_cli.get("/procurement/deliveries").get_data(as_text=True)
        assert "<title>Open orders &amp; late deliveries" in dv and "dl-lbl" in dv

        # -- every new sentence exists in Arabic and Turkish
        from app.approvals.tests_messages_i18n import user_facing_strings
        root = Path(__file__).resolve().parents[2]
        from app.routes import approvals_delivery as RD
        need = user_facing_strings(root / "app" / "routes" / "approvals_delivery.py")
        need |= set(RD._PROMISE_ERR.values()) | set(RD._CANCEL_ERR.values()) | set(RD._CANCEL_OK.values())
        need |= {"The required-by date cannot be in the past.",
                 "Enter the required-by date as a valid date.",
                 "That supplier's purchase order was cancelled — its lines cannot be received."}
        missing = [s for s in need if not (MESSAGES.get(s, {}).get("ar") and MESSAGES[s].get("tr"))]
        assert not missing, missing
    print("delivery follow-up: OK")


if __name__ == "__main__":
    run()
