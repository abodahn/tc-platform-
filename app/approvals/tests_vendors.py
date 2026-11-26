"""Vendors lane: vendor approval (pending -> approved -> blocked), master data
(bank, commercial register, tax card + expiry), supplier documents, only
APPROVED suppliers chosen / awarded / ordered from / advanced, the comments
thread on a request, and Arabic/Turkish statuses, audit names and bell
notifications.

    python -m app.approvals.tests_vendors
"""
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

PDF = "JVBERi0xLjQK"


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "vendors.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    from flask import g
    # One app context for the whole run: without this the next test client
    # would inherit the previous request's user from g.
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import services as svc
        from app.approvals import vendors as V
        from app.approvals import notif_text as N
        from app.approvals import schema
        from app.approvals.messages import translate
        from app.approvals.vendors_text import QUOTE_ERRORS
        from app.db import get_db

        def q(sql, *a):
            c = get_db()
            try:
                return c.execute(sql, a).fetchone()
            finally:
                c.close()

        def x(sql, *a):
            c = get_db()
            try:
                c.execute(sql, a)
                c.commit()
            finally:
                c.close()

        c = get_db()
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        ids = {}
        for un, role in (("v.buyer", "purchasing_manager"), ("v.buyer2", "purchasing_manager"),
                         ("v.fin", "finance_manager"), ("v.fin2", "finance_manager"),
                         ("v.staff", "finance_user"), ("v.other", "finance_user"),
                         ("v.signer", "factory_manager"), ("v.store", "storekeeper")):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, "Test " + un, role))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        c.commit()
        c.close()
        U = {un: {"username": un, "id": ids[un], "role": role} for un, role in (
            ("v.buyer", "purchasing_manager"), ("v.buyer2", "purchasing_manager"),
            ("v.fin", "finance_manager"), ("v.fin2", "finance_manager"),
            ("v.staff", "finance_user"), ("v.other", "finance_user"),
            ("v.signer", "factory_manager"))}
        buyer, fin, staff = U["v.buyer"], U["v.fin"], U["v.staff"]

        def client(uid):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
            return cli

        def vid(name):
            return q("SELECT id FROM proc_vendors WHERE name=?", name)["id"]

        # ---- 1. legacy rows keep working --------------------------------------
        x("INSERT INTO proc_vendors (name, is_active) VALUES ('Legacy Active Co', 1)")
        x("INSERT INTO proc_vendors (name, is_active) VALUES ('Legacy Inactive Co', 0)")
        c = get_db()
        assert V.vendor_state(c, "legacy active co") == "approved"        # NULL status -> is_active
        assert V.vendor_state(c, "Legacy Inactive Co") == "blocked"
        assert svc.vendor_approved(c, "Legacy Active Co")
        assert not svc.vendor_approved(c, "Nobody Ltd")
        schema.backfill_vendor_status(c)
        c.close()
        assert q("SELECT status FROM proc_vendors WHERE name='Legacy Active Co'")["status"] == "approved"
        assert q("SELECT status FROM proc_vendors WHERE name='Legacy Inactive Co'")["status"] == "blocked"

        # ---- 2. a new supplier starts pending, with its master data ------------
        soon = (date.today() + timedelta(days=10)).isoformat()
        past = (date.today() - timedelta(days=3)).isoformat()
        assert svc.create_vendor({"name": "Bad Date Co", "cr_expiry": "31/12/2026"}, buyer) == (False, "bad_date")
        assert svc.create_vendor({"name": "Acme Test Supplies", "email": "acme@example.test",
                                  "bank_name": "Test Bank", "bank_account_name": "Acme",
                                  "bank_account_no": "EG000011112222333344445555",
                                  "cr_no": "CR-1", "cr_expiry": past, "tax_id": "TX-9",
                                  "tax_card_expiry": soon}, buyer) == (True, "")
        acme = vid("Acme Test Supplies")
        row = dict(q("SELECT * FROM proc_vendors WHERE id=?", acme))
        assert row["status"] == "pending" and row["created_by"] == "v.buyer" and row["is_active"] == 1
        assert row["bank_account_no"] == "EG000011112222333344445555" and row["cr_no"] == "CR-1"
        ev = q("SELECT detail FROM pr_events WHERE action='vendor_created' ORDER BY id DESC LIMIT 1")["detail"]
        assert "EG000011112222333344445555" not in ev and "****5555" in ev, ev
        warns = {w["doc"]: w["state"] for w in V.doc_warnings(row)}
        assert warns == {"cr": "expired", "tax_card": "soon"}, warns
        # pending suppliers still appear in RFQ / quote pick lists
        assert "Acme Test Supplies" in [v["name"] for v in svc.list_vendors()]

        # ---- 3. who may approve / block ---------------------------------------
        assert V.approve_vendor(acme, buyer) == (False, "vendor_approve_forbidden")
        r = client(ids["v.buyer"]).post("/procurement/vendors/%d/approve" % acme, data={"_csrf": "tok"})
        assert r.status_code == 403                                         # proc_pay / proc_admin only
        assert svc.create_vendor({"name": "Finance Own Co"}, fin)[0]
        assert V.approve_vendor(vid("Finance Own Co"), fin) == (False, "vendor_self_approve")
        assert V.approve_vendor(vid("Finance Own Co"), U["v.fin2"]) == (True, "")
        assert svc.create_vendor({"name": "Admin Own Co"}, admin)[0]
        assert V.approve_vendor(vid("Admin Own Co"), admin) == (True, "")  # super_admin may
        r = client(ids["v.fin"]).post("/procurement/vendors/%d/approve" % acme, data={"_csrf": "tok"})
        assert r.status_code == 302
        assert q("SELECT status, status_by FROM proc_vendors WHERE id=?", acme)["status"] == "approved"
        assert q("SELECT 1 FROM pr_events WHERE action='vendor_approved' AND detail LIKE ?",
                 "%Acme Test Supplies: pending -> approved%")

        assert V.block_vendor(acme, "  ", buyer) == (False, "block_reason_required")
        assert V.block_vendor(acme, "x", staff) == (False, "vendor_block_forbidden")
        assert svc.set_vendor_active(acme, False, buyer) == (False, "block_reason_required")
        r = client(ids["v.buyer"]).post("/procurement/vendors/%d/block" % acme,
                                        data={"_csrf": "tok", "reason": "Fake invoices <b>"})
        assert r.status_code == 302
        row = dict(q("SELECT * FROM proc_vendors WHERE id=?", acme))
        assert (row["status"], row["is_active"], row["block_reason"]) == ("blocked", 0, "Fake invoices <b>")
        assert q("SELECT 1 FROM pr_events WHERE action='vendor_blocked' AND detail LIKE '%Fake invoices%'")
        assert V.approve_vendor(acme, buyer) == (False, "vendor_approve_forbidden")   # unblock: Finance
        assert svc.set_vendor_active(acme, True, fin) == (True, "")
        assert V.vendor_state(get_db(), "Acme Test Supplies") == "approved"

        # ---- 4. email / bank change sends it back to pending (masked audit) ----
        assert svc.update_vendor(acme, {"name": "Acme Test Supplies", "email": "acme@example.test",
                                        "phone": "123"}, buyer)[0]
        assert q("SELECT status FROM proc_vendors WHERE id=?", acme)["status"] == "approved"   # no change
        assert q("SELECT bank_account_no FROM proc_vendors WHERE id=?", acme)["bank_account_no"] \
            == "EG000011112222333344445555"                                  # not posted -> kept
        assert svc.update_vendor(acme, {"name": "Acme Test Supplies", "email": "acme@example.test",
                                        "bank_account_no": "EG999988887777666655554321"}, buyer)[0]
        row = dict(q("SELECT * FROM proc_vendors WHERE id=?", acme))
        assert row["status"] == "pending" and row["submitted_by"] == "v.buyer"
        ev = q("SELECT detail FROM pr_events WHERE action='vendor_resubmitted' ORDER BY id DESC LIMIT 1")["detail"]
        assert "****5555 -> ****4321" in ev and "EG9999" not in ev and "EG0000" not in ev, ev
        assert not any("EG999988887777666655554321" in (r["detail"] or "")
                       for r in get_db().execute("SELECT detail FROM pr_events").fetchall())
        assert V.approve_vendor(acme, fin) == (True, "")
        assert svc.update_vendor(acme, {"name": "Acme Test Supplies", "email": "new@example.test"}, buyer)[0]
        assert q("SELECT status FROM proc_vendors WHERE id=?", acme)["status"] == "pending"

        # ---- 5. only APPROVED suppliers are chosen / awarded / ordered ---------
        pid, _ = svc.create_pr({"title": "Vendor gate", "department": "IT"},
                               [{"item": "Toner", "qty": 2, "unit_price": 0}], staff, submit=False)
        item = q("SELECT id FROM pr_items WHERE pr_id=?", pid)["id"]
        ok, code = svc.add_quote(pid, {"vendor": "Acme Test Supplies", "amount": "20",
                                       "payment_terms": "30 days", "lines": {item: "10"}}, buyer,
                                 filename="q.pdf", content_type="application/pdf", content_b64=PDF)
        assert ok, code                                                     # pending may still quote
        qid = q("SELECT id FROM pr_quotes WHERE pr_id=? ORDER BY id DESC LIMIT 1", pid)["id"]
        assert svc.choose_quote(pid, qid, buyer) == (False, "vendor_pending")
        assert svc.award_quote_line(pid, item, qid, buyer) == (False, "vendor_pending")
        assert svc.apply_chosen_quote(pid, qid, buyer) == (False, "vendor_pending")
        r = client(ids["v.buyer"]).post("/procurement/pr/%d/quote/%d/choose" % (pid, qid),
                                        data={"_csrf": "tok"}, follow_redirects=True)
        _h = r.get_data(as_text=True)
        assert "still pending approval" in _h, [l for l in _h.splitlines() if "TC_FLASH" in l][:3]
        x("UPDATE pr_requests SET status='approved', vendor='Acme Test Supplies' WHERE id=?", pid)
        x("UPDATE pr_items SET vendor='Acme Test Supplies', unit_price=10 WHERE pr_id=?", pid)
        assert svc.issue_po(pid, buyer) == (False, "vendor_pending")
        assert svc.authorize_advance(pid, 10, "", fin) == (False, "no_po")
        x("UPDATE pr_requests SET status='po_issued' WHERE id=?", pid)
        assert svc.authorize_advance(pid, 10, "", fin) == (False, "vendor_not_approved")
        x("UPDATE pr_requests SET status='draft' WHERE id=?", pid)
        assert V.block_vendor(acme, "Under review", fin)[0]
        assert svc.choose_quote(pid, qid, buyer) == (False, "vendor_blocked")
        assert V.approve_vendor(acme, U["v.fin2"])[0]
        assert svc.choose_quote(pid, qid, buyer) == (True, "")
        # refusal sentences are translated
        assert translate(QUOTE_ERRORS["vendor_pending"], "ar") != QUOTE_ERRORS["vendor_pending"]
        assert translate(QUOTE_ERRORS["vendor_blocked"], "tr") != QUOTE_ERRORS["vendor_blocked"]
        for lang in ("en", "ar", "tr"):
            ui = svc.labels(lang)["ui"]
            for k in ("vendor_pending_flash", "vendor_self_approve_flash", "comment_forbidden_flash",
                      "block_reason_required_flash", "vendor_doc_type_flash"):
                assert ui.get(k), (lang, k)

        # ---- 6. documents ------------------------------------------------------
        import io
        bc = client(ids["v.buyer"])
        r = bc.post("/procurement/vendors/%d/doc" % acme, data={
            "_csrf": "tok", "doc_type": "cr", "file": (io.BytesIO(b"MZ"), "evil.exe")},
            content_type="multipart/form-data")
        assert r.status_code == 302 and not q("SELECT 1 FROM proc_vendor_docs")
        r = bc.post("/procurement/vendors/%d/doc" % acme, data={
            "_csrf": "tok", "doc_type": "cr", "file": (io.BytesIO(b"x" * (3 * 1024 * 1024 + 1)), "big.pdf")},
            content_type="multipart/form-data")
        assert not q("SELECT 1 FROM proc_vendor_docs")
        r = bc.post("/procurement/vendors/%d/doc" % acme, data={
            "_csrf": "tok", "doc_type": "cr", "file": (io.BytesIO(b"%PDF-1.4 test"), "cr.pdf")},
            content_type="multipart/form-data")
        doc = q("SELECT * FROM proc_vendor_docs WHERE vendor_id=?", acme)
        assert doc and doc["doc_type"] == "cr" and doc["uploaded_by"] == "v.buyer"
        assert bc.get("/procurement/vendor-doc/%d" % doc["id"]).data == b"%PDF-1.4 test"
        assert client(ids["v.staff"]).get("/procurement/vendor-doc/%d" % doc["id"]).status_code == 403
        r = client(ids["v.staff"]).post("/procurement/vendors/%d/doc" % acme, data={"_csrf": "tok"})
        assert r.status_code == 403

        # ---- 7. the Vendors page: filter, search, badges, bank privacy ---------
        x("UPDATE proc_vendors SET status='pending' WHERE id=?", vid("Finance Own Co"))
        html = bc.get("/procurement/vendors?status=pending").get_data(as_text=True)
        assert "Finance Own Co" in html and "Legacy Active Co" not in html
        html = bc.get("/procurement/vendors?q=acme").get_data(as_text=True)
        assert "Acme Test Supplies" in html and "Legacy Active Co" not in html
        assert 'data-i18n="proc.vendors.doc_cr"' in html and 'data-i18n="proc.vendors.expired"' in html
        assert "EG999988887777666655554321" in html                     # buyer sees bank details
        html = bc.get("/procurement/vendors?status=warn").get_data(as_text=True)
        assert "Acme Test Supplies" in html and "Legacy Active Co" not in html
        html = client(ids["v.staff"]).get("/procurement/vendors").get_data(as_text=True)
        assert "Acme Test Supplies" in html and "EG9999" not in html     # viewer: no bank data
        assert "proc.vendors.approve" not in html                         # and no approve button
        html = client(ids["v.fin"]).get("/procurement/vendors?status=pending").get_data(as_text=True)
        assert "proc.vendors.approve" in html
        html = client(ids["v.buyer"]).get("/procurement/vendors?status=blocked").get_data(as_text=True)
        assert "Legacy Inactive Co" in html

        # supplier checks on the request page (RFQ / award / PO all live there)
        x("UPDATE proc_vendors SET status='pending' WHERE id=?", acme)
        html = bc.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert 'id="prVendorFlags"' in html and 'data-i18n="proc.vendors.st_pending"' in html
        x("UPDATE proc_vendors SET status='approved' WHERE id=?", acme)

        # ---- 8. comments thread ------------------------------------------------
        assert V.add_comment(pid, "  ", staff) == (False, "comment_required")
        assert V.add_comment(pid, "x" * 1001, staff) == (False, "comment_too_long")
        assert V.add_comment(pid, "Not mine", U["v.other"]) == (False, "comment_forbidden")
        r = client(ids["v.other"]).post("/procurement/pr/%d/comment" % pid, data={"_csrf": "tok", "body": "hi"})
        assert r.status_code == 302 and not q("SELECT 1 FROM pr_comments WHERE author='v.other'")
        assert V.add_comment(pid, "<script>alert(1)</script> which model?", buyer) == (True, "")
        n = q("SELECT title, message FROM notifications WHERE target_user='v.staff' ORDER BY id DESC LIMIT 1")
        assert n and n["title"].startswith("New comment on"), n               # buyer -> requester
        assert q("SELECT 1 FROM pr_events WHERE pr_id=? AND action='comment'", pid)
        # a signer of the request may comment; the requester hears about it
        x("INSERT INTO pr_steps (pr_id, seq, stage, status, approver_user) "
          "VALUES (?, 1, 'factory_manager', 'approved', 'v.signer')", pid)
        assert V.add_comment(pid, "Approved, but check the toner model.", U["v.signer"]) == (True, "")
        # on hold: the requester's reply reaches whoever held it
        x("UPDATE pr_requests SET status='pending', current_seq=2, hold_reason='Which printer?', "
          "held_by='v.fin', held_at='2026-11-26 10:00:00' WHERE id=?", pid)
        x("INSERT INTO pr_steps (pr_id, seq, stage, status) VALUES (?, 2, 'finance', 'pending')", pid)
        before = q("SELECT COUNT(*) c FROM notifications WHERE target_user='v.fin'")["c"]
        sc = client(ids["v.staff"])
        r = sc.post("/procurement/pr/%d/comment" % pid, data={"_csrf": "tok", "body": "The HP 401 in IT."})
        assert r.status_code == 302
        assert q("SELECT COUNT(*) c FROM notifications WHERE target_user='v.fin'")["c"] == before + 1
        html = sc.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert 'id="prComments"' in html and "proc.vendors.cm_reply" in html   # Reply box near the top
        assert html.index('id="prComments"') < html.index("proc.timeline")
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>alert(1)" not in html
        x("UPDATE pr_requests SET hold_reason=NULL, held_by=NULL WHERE id=?", pid)
        # v.other (finance_user) is now an eligible signer of the pending finance
        # rung, so may post; a storekeeper with no part in this request may not
        assert V.add_comment(pid, "eligible signer", U["v.other"]) == (True, "")
        html = client(ids["v.store"]).get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert "The HP 401 in IT." in html and 'name="body"' not in html      # reads, cannot post

        # ---- 9. statuses, audit names and bell notifications translated -------
        html = sc.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert 'data-i18n="proc.status.pending"' in html
        assert 'data-i18n="proc.audit.comment"' in html
        dicts = {l: json.load(open(Path(app.static_folder) / "i18n" / ("%s.json" % l), encoding="utf-8"))
                 for l in ("en", "ar", "tr")}
        from app.approvals import constants as C
        for code in tuple(C.PR_STATUSES) + ("unpaid", "partial", "paid"):
            for l in dicts:
                assert dicts[l].get("proc.status." + code), (l, code)
        new_keys = [k for k in dicts["en"] if k.startswith(("proc.vendors.", "proc.audit.", "proc.status."))]
        for k in new_keys:
            assert dicts["ar"].get(k) and dicts["tr"].get(k), k
        assert N.localise("Ready to close", "PR-2026-000001 is fully paid — close the request.", "ar") == (
            "جاهز للإقفال", "PR-2026-000001 اتدفع بالكامل — اقفل الطلب.")
        assert N.localise("On hold: PR-1", "fin put this on hold: why?", "tr") == (
            "Beklemede: PR-1", "fin bunu beklemeye aldı: why?")
        assert N.localise("PR-1 partly met from stock", "x", "ar")[0] == "PR-1 اتصرف جزء منه من المخزن"
        assert N.localise("Payment complete", "Something new", "en") == ("Payment complete", "Something new")
        assert N.localise("Unknown title", "Unknown body", "ar") == ("Unknown title", "Unknown body")
        x("UPDATE users SET lang_pref='ar' WHERE username='v.staff'")
        feed = client(ids["v.staff"]).get("/notifications/feed").get_json()
        titles = [i["title"] for i in feed["items"]]
        assert any(t.startswith("تعليق جديد على") for t in titles), titles
        assert q("SELECT title FROM notifications WHERE target_user='v.staff' ORDER BY id DESC LIMIT 1")[
            "title"].startswith("New comment on")                              # stored row unchanged
        html = client(ids["v.staff"]).get("/my-work").get_data(as_text=True)
        assert "New comment on" not in html or "تعليق جديد على" in html

        # mobile: every new page renders (the layout is CSS; a 200 with the
        # wrapper classes is what a server-side test can check)
        for path in ("/procurement/vendors", "/procurement/vendors?status=warn&q=a"):
            r = client(admin["id"]).get(path)
            assert r.status_code == 200 and "vnd-list" in r.get_data(as_text=True), path
    print("vendors lane: OK")


if __name__ == "__main__":
    run()
