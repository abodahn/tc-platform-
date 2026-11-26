"""PO lane (DEPLOY 4): real PO revisions, bulk signing, copy a request, and
warehouse low stock -> purchase request.

Everything goes through the real flow (create_pr -> price_pr -> the ladder ->
issue_po) on a throw-away SQLite database.

    python -m app.approvals.tests_porev
"""
import base64
import os
import re
import sys
import tempfile
import zlib
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "porev.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _pdf_text(blob):
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", blob, re.S):
        data = m.group(1)
        for step in (lambda x: base64.a85decode(x, adobe=True), zlib.decompress):
            try:
                data = step(data)
            except Exception:                       # noqa: BLE001 — image / raw stream
                pass
        out.append(data)
    return b"\n".join(out).decode("latin-1")


def run():
    app = _app()
    from flask import g
    # One app context for the whole run: drop the cached user between requests.
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, porev, pdf as pdfgen
        from app.approvals import constants as C
        from app.approvals.messages import MESSAGES
        from app.approvals.tests_messages_i18n import user_facing_strings

        buyer = {"username": "t.buyer", "role": "purchasing_manager", "id": 1}
        tech = {"username": "t.tech", "role": "normal_user", "id": 9}

        def q(sql, *args):
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

        def ex(sql, *args):
            conn = get_db()
            try:
                conn.execute(sql, args)
                conn.commit()
            finally:
                conn.close()

        def make(title, lines, tax=14, requester=tech):
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance", "vendor": lines[0][3],
                 "tax_rate": tax},
                [{"item": i, "qty": qty, "unit_price": 0, "vendor": v} for i, qty, _p, v in lines],
                requester, priced=False)
            ids = [r["id"] for r in q("SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id", pr_id)]
            ok, msg = svc.price_pr(pr_id, {i: l[2] for i, l in zip(ids, lines)}, {"tax_rate": tax}, buyer)
            assert ok, msg
            return pr_id, ids

        def signer(role, tag):
            return {"username": "u_%s_%s" % (role, tag), "role": role, "id": 500}

        def approve_and_issue(pr_id):
            svc.set_single_source(pr_id, "Sole authorised supplier for these parts", buyer)
            for _ in range(20):
                pr = q("SELECT status, current_seq FROM pr_requests WHERE id=?", pr_id)[0]
                steps = q("SELECT stage FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
                          pr_id, pr["current_seq"])
                if pr["status"] != "pending" or not steps:
                    break
                for s in steps:
                    role = sorted(C.STAGE_ROLES[s["stage"]])[0]
                    ok, msg = svc.act_on_step(pr_id, signer(role, pr_id), "approve", "ok")
                    assert ok, (s["stage"], msg)
            ex("INSERT OR IGNORE INTO proc_vendors (name, is_active, created_at) "
               "SELECT DISTINCT TRIM(COALESCE(i.vendor, r.vendor)), 1, '2026-01-01' FROM pr_items i "
               "JOIN pr_requests r ON r.id=i.pr_id WHERE i.pr_id=?", pr_id)
            ok, res = svc.issue_po(pr_id, buyer)
            assert ok, res
            return res

        def po_rows(pr_id):
            return q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", pr_id)

        def sign_all(rev_id, tag):
            out = None
            for _ in range(10):
                st = q("SELECT stage FROM pr_po_rev_steps WHERE rev_id=? AND status='pending' "
                       "ORDER BY seq LIMIT 1", rev_id)
                if not st:
                    break
                role = sorted(C.STAGE_ROLES[st[0]["stage"]])[0]
                ok, out = porev.sign_revision(rev_id, signer(role, "r%s" % tag), "approve")
                assert ok, (st[0]["stage"], out)
            return out

        # ==============================================================
        # (A1) single order: a decrease applies at once, numbered Rev 1
        # ==============================================================
        one, (l1,) = make("Bearings", [("Bearing 6204", 10, 100, "Fake Bearings Co")])
        po_no = approve_and_issue(one)
        assert po_rows(one)[0]["grand"] == 1140.0, po_rows(one)
        ok, res = porev.propose_revision(one, None, {l1: {"qty": "8"}}, "", "", buyer)
        assert (ok, res) == (False, "reason_required")
        ok, res = porev.propose_revision(one, None, {l1: {"qty": "8"}}, "", "Supplier short", tech)
        assert (ok, res) == (False, "self_revise"), res               # the requester may not
        ok, res = porev.propose_revision(one, None, {l1: {"qty": "abc"}}, "", "x", buyer)
        assert (ok, res) == (False, "bad_qty")
        ok, res = porev.propose_revision(one, None, {l1: {"qty": "10"}}, "", "x", buyer)
        assert (ok, res) == (False, "no_change")
        ok, res = porev.propose_revision(one, None, {l1: {"qty": "8"}}, "2026-12-20",
                                         "Supplier can only send 8", buyer)
        assert (ok, res) == (True, "applied"), res
        po = po_rows(one)[0]
        pr = q("SELECT * FROM pr_requests WHERE id=?", one)[0]
        assert (po["rev"], pr["po_rev"], pr["total"], po["grand"]) == (1, 1, 800.0, 912.0), (po, pr["total"])
        assert po["delivery_date"] == "2026-12-20" and po["approved_grand"] == 1140.0
        assert q("SELECT qty FROM pr_items WHERE id=?", l1)[0]["qty"] == 8
        rev = q("SELECT * FROM pr_po_revisions WHERE pr_id=?", one)[0]
        assert rev["status"] == "applied" and rev["old_grand"] == 1140.0 and rev["new_grand"] == 912.0
        assert '"qty": [10.0, 8.0]' in rev["changes_json"], rev["changes_json"]
        # the money controls read the revised order
        m = svc.three_way_match(one)
        assert m["ordered_grand"] == 912.0 and m["ordered_qty"] == 8, m
        assert svc.pr_amounts(q("SELECT * FROM pr_requests WHERE id=?", one)[0])["grand"] == 912.0
        txt = _pdf_text(pdfgen.po_pdf(svc.get_pr(one), None))
        assert "Rev 1" in txt and "2026-12-20" in txt, "revised PO PDF lacks Rev / date"

        # receiving caps follow the revised quantity, and it may not go below what arrived
        ok, msg = svc.receive_items(one, {l1: 5}, buyer)
        assert ok, msg
        assert q("SELECT qty - received_qty AS open FROM pr_items WHERE id=?", l1)[0]["open"] == 3
        ok, res = porev.propose_revision(one, None, {l1: {"qty": "4"}}, "", "less", buyer)
        assert (ok, res) == (False, "qty_below_received")

        # ==============================================================
        # (A2) an increase above the APPROVED value (1,140 as issued — the
        # decrease did not lower it) waits for the value ladder; nothing moves
        # ==============================================================
        ok, res = porev.propose_revision(one, None, {l1: {"unit_price": "120"}}, "",
                                         "back within the approved value", buyer)
        assert (ok, res) == (True, "applied"), res          # 8 x 120 x 1.14 = 1,094.40 <= 1,140
        ex("UPDATE proc_vendors SET email='orders@example.invalid' WHERE name='Fake Bearings Co'")
        ok, res = porev.propose_revision(one, None, {l1: {"unit_price": "150"}}, "",
                                         "Metal price rise", buyer, email=True)
        assert (ok, res) == (True, "pending"), res
        assert po_rows(one)[0]["grand"] == 1094.4 and q("SELECT unit_price FROM pr_items WHERE id=?",
                                                         l1)[0]["unit_price"] == 120
        pend = q("SELECT * FROM pr_po_revisions WHERE pr_id=? AND status='pending'", one)[0]
        stages = q("SELECT stage FROM pr_po_rev_steps WHERE rev_id=? ORDER BY seq", pend["id"])
        want = [s for s in svc.dept_ladder(get_db(), "Maintenance", 1, "opex") if s in C.VALUE_STAGES]
        assert [s["stage"] for s in stages] and set(s["stage"] for s in stages) <= set(want), stages
        # every value rung already signed the original: only the tier's top authority re-signs
        assert [s["stage"] for s in stages] == want[-1:], (stages, want)
        assert porev.propose_revision(one, None, {l1: {"qty": "9"}}, "", "again", buyer) == \
            (False, "rev_pending")
        assert porev.sign_revision(pend["id"], buyer, "approve")[1] == "forbidden"   # not the rung's role
        first_role = sorted(C.STAGE_ROLES[stages[0]["stage"]])[0]
        assert porev.sign_revision(pend["id"], dict(signer(first_role, "x"), username="t.buyer"),
                                   "approve") == (False, "self_revision")
        # the signer queue sees it
        assert any(r["id"] == pend["id"] for r in porev.rev_queue(signer(first_role, "q")))
        assert not porev.rev_queue(tech)
        if len(stages) > 1:
            s1 = signer(first_role, "dual")
            assert porev.sign_revision(pend["id"], s1, "approve")[0]
            nxt = q("SELECT stage FROM pr_po_rev_steps WHERE rev_id=? AND status='pending' "
                    "ORDER BY seq LIMIT 1", pend["id"])[0]["stage"]
            if first_role in C.STAGE_ROLES[nxt]:
                assert porev.sign_revision(pend["id"], s1, "approve") == (False, "dual_role")
        assert sign_all(pend["id"], "a") == "applied"
        po = po_rows(one)[0]
        pr = q("SELECT * FROM pr_requests WHERE id=?", one)[0]
        assert (po["rev"], pr["po_rev"], pr["total"], po["grand"], po["approved_grand"]) == \
            (3, 3, 1200.0, 1368.0, 1368.0), (po, pr["total"])
        assert q("SELECT status FROM pr_po_revisions WHERE id=?", pend["id"])[0]["status"] == "applied"
        ev = q("SELECT * FROM pr_sign_events WHERE pr_id=? AND seq>=1000 ORDER BY id DESC", one)
        assert ev, "no signature events"
        import hashlib
        payload = "|".join(str(x) for x in (pr["pr_no"], ev[0]["seq"], ev[0]["stage"], "approve",
                                            ev[0]["signer"], "%.2f" % pend["new_grand"],
                                            pr["currency"] or "", ev[0]["created_at"]))
        assert ev[0]["doc_hash"] == hashlib.sha256(payload.encode("utf-8")).hexdigest(), \
            "revision signature does not anchor the revised value"
        assert q("SELECT 1 FROM pr_events WHERE pr_id=? AND action='po_emailed'", one), "PO not emailed"

        # a rejected increase leaves the order alone
        ok, res = porev.propose_revision(one, None, {l1: {"unit_price": "160"}}, "", "rise", buyer)
        assert res == "pending"
        rid = q("SELECT id FROM pr_po_revisions WHERE pr_id=? AND status='pending'", one)[0]["id"]
        st = q("SELECT stage FROM pr_po_rev_steps WHERE rev_id=? ORDER BY seq LIMIT 1", rid)[0]["stage"]
        assert porev.sign_revision(rid, signer(sorted(C.STAGE_ROLES[st])[0], "rj"), "reject",
                                   "too dear") == (True, "rejected")
        assert po_rows(one)[0]["grand"] == 1368.0 and q("SELECT status FROM pr_po_revisions "
                                                         "WHERE id=?", rid)[0]["status"] == "rejected"
        # withdraw
        assert porev.propose_revision(one, None, {l1: {"unit_price": "160"}}, "", "rise", buyer)[1] == "pending"
        rid = q("SELECT id FROM pr_po_revisions WHERE pr_id=? AND status='pending'", one)[0]["id"]
        assert porev.cancel_revision(rid, buyer) == (True, "cancelled")
        assert porev.cancel_revision(rid, buyer) == (False, "not_pending")

        # the request's own value controls are asked again at the revised value
        ex("UPDATE pr_requests SET single_source_reason=NULL WHERE id=?", one)
        assert porev.propose_revision(one, None, {l1: {"unit_price": "160"}}, "", "rise",
                                      buyer) == (False, "needs_quotes")
        ex("UPDATE pr_requests SET single_source_reason='Sole supplier' WHERE id=?", one)
        ok, res = porev.propose_revision(one, None, {l1: {"unit_price": "2000000"}}, "", "rise", buyer)
        assert (ok, res) == (False, "business_case_required"), res
        assert q("SELECT unit_price FROM pr_items WHERE id=?", l1)[0]["unit_price"] == 150
        # ... and again at the final signature (the waiver was withdrawn meanwhile)
        assert porev.propose_revision(one, None, {l1: {"unit_price": "160"}}, "", "rise", buyer)[1] == "pending"
        rid = q("SELECT id FROM pr_po_revisions WHERE pr_id=? AND status='pending'", one)[0]["id"]
        # one pending revision per request, whatever races past the check
        dup = True
        try:
            ex("INSERT INTO pr_po_revisions (pr_id, rev_no, status) VALUES (?, 99, 'pending')", one)
        except Exception:                                   # noqa: BLE001 — unique index
            dup = False
        assert not dup, "a second pending revision was accepted"
        cur = q("SELECT * FROM pr_po_rev_steps WHERE rev_id=? AND status='pending'", rid)[0]
        role = sorted(C.STAGE_ROLES[cur["stage"]])[0]
        dual = signer(role, "dual2")
        ex("INSERT INTO pr_po_rev_steps (rev_id, pr_id, seq, stage, status, approver_user) "
           "VALUES (?, ?, 0, 'factory_manager', 'approved', ?)", rid, one, dual["username"])
        assert not any(r["id"] == rid for r in porev.rev_queue(dual)), "dual signer offered"
        assert not porev.revision_view(one, dual)["pending"]["can_sign"]
        assert porev.sign_revision(rid, dual, "approve") == (False, "dual_role")
        ex("DELETE FROM pr_po_rev_steps WHERE rev_id=? AND seq=0", rid)
        assert any(r["id"] == rid for r in porev.rev_queue(dual))
        ex("UPDATE pr_requests SET single_source_reason=NULL WHERE id=?", one)
        assert porev.sign_revision(rid, signer(role, "gate"), "approve") == (False, "needs_quotes")
        assert q("SELECT status FROM pr_po_rev_steps WHERE id=?", cur["id"])[0]["status"] == "pending"
        ex("UPDATE pr_requests SET single_source_reason='Sole supplier' WHERE id=?", one)
        assert porev.cancel_revision(rid, buyer)[0]

        # ==============================================================
        # (A3) tolerance setting: small raises apply, and cannot creep
        # ==============================================================
        ok, _ = svc.set_setting("po_rev_tolerance_pct", "10", {"username": "admin"})
        assert ok
        ok, res = porev.propose_revision(one, None, {l1: {"unit_price": "155"}}, "", "small", buyer)
        assert res == "applied", res                                    # +3.3% of 1,368
        ok, res = porev.propose_revision(one, None, {l1: {"unit_price": "166"}}, "", "small2", buyer)
        assert res == "pending", res          # 1,513.92 > 1,368 x 1.10: measured on the SIGNED value
        rid = q("SELECT id FROM pr_po_revisions WHERE pr_id=? AND status='pending'", one)[0]["id"]
        porev.cancel_revision(rid, buyer)
        svc.set_setting("po_rev_tolerance_pct", "0", {"username": "admin"})

        # nothing may go below what is already invoiced / paid
        ex("INSERT INTO pr_invoices (pr_id, invoice_no, amount, tax, status, created_at) "
           "VALUES (?, 'INV-T1', 900, 0, 'open', '2026-01-01')", one)
        ok, res = porev.propose_revision(one, None, {l1: {"unit_price": "50"}}, "", "cut", buyer)
        assert (ok, res) == (False, "below_billed"), res

        # ==============================================================
        # (A4) split request: only the revised supplier's order moves
        # ==============================================================
        two, (a1, b1) = make("Seals and valves", [("Seal kit", 10, 100, "Fake Alpha Ltd"),
                                                  ("Valve", 4, 50, "Fake Beta Ltd")], tax=0)
        approve_and_issue(two)
        pa, pb = po_rows(two)
        assert porev.propose_revision(two, None, {b1: {"qty": "3"}}, "", "x", buyer) == \
            (False, "po_required")
        ok, res = porev.propose_revision(two, pb["id"], {b1: {"qty": "3"}, a1: {"qty": "1"}}, "",
                                         "One valve dropped", buyer)
        assert res == "applied", res
        pa2, pb2 = po_rows(two)
        assert (pa2["rev"], pa2["grand"], pb2["rev"], pb2["grand"]) == (0, 1000.0, 1, 150.0), (pa2, pb2)
        assert q("SELECT qty FROM pr_items WHERE id=?", a1)[0]["qty"] == 10    # not on this order
        assert q("SELECT po_rev FROM pr_requests WHERE id=?", two)[0]["po_rev"] == 0
        b = svc.get_pr(two)
        assert "Rev 1" not in _pdf_text(pdfgen.po_pdf(b, next(p for p in b["pos"] if p["id"] == pa["id"])))
        assert "Rev 1" in _pdf_text(pdfgen.po_pdf(b, next(p for p in b["pos"] if p["id"] == pb["id"])))
        conn = get_db()
        try:
            assert svc.po_exposure(conn, b["pr"], pb2)["ordered"] == 150.0  # payment cap follows
        finally:
            conn.close()

        ex("UPDATE pr_requests SET disc_amount=10 WHERE id=?", two)
        assert porev.propose_revision(two, pa["id"], {a1: {"qty": "9"}}, "", "x", buyer) == \
            (False, "split_discount")
        ex("UPDATE pr_requests SET disc_amount=NULL WHERE id=?", two)

        # (A5) a request issued before per-vendor orders still revises
        old, (o1,) = make("Gloves", [("Glove", 20, 10, "Fake Gloves Co")], tax=0)
        approve_and_issue(old)
        ex("DELETE FROM pr_purchase_orders WHERE pr_id=?", old)
        assert porev.propose_revision(old, None, {o1: {"qty": "15"}}, "", "less", buyer)[1] == "applied"
        assert (po_rows(old)[0]["rev"], po_rows(old)[0]["grand"]) == (1, 150.0)
        # cutting a short-shipped line to what arrived completes the receipt
        ok, msg = svc.receive_items(old, {o1: 10}, buyer)
        assert ok, msg
        assert q("SELECT status FROM pr_requests WHERE id=?", old)[0]["status"] == "partially_received"
        assert porev.propose_revision(old, None, {o1: {"qty": "10"}}, "", "short shipped",
                                      buyer)[1] == "applied"
        assert q("SELECT status FROM pr_requests WHERE id=?", old)[0]["status"] == "received"

        # ==============================================================
        # HTTP: permissions, bulk signing, queue count, copy, low stock
        # ==============================================================
        c = get_db()
        ids = {}
        for un, role in (("w.req", "normal_user"), ("w.store", "storekeeper"),
                         ("w.buy", "purchasing_manager"), ("w.view", "viewer")):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, "
                      "created_at, sig_png) VALUES (?, 'x', ?, ?, 1, '2026-01-01', ?)",
                      (un, "Test " + un, role, "iVBORw0KGgo=" if un == "w.store" else None))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        c.commit()
        c.close()

        def client(uid):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
            return cli

        req = {"username": "w.req", "id": ids["w.req"], "role": "normal_user"}
        r = client(ids["w.req"]).post("/procurement/pr/%d/po-revision" % one,
                                      data={"_csrf": "tok", "reason": "x"})
        assert r.status_code == 403, r.status_code                       # proc_purchasing only
        r = client(ids["w.req"]).post("/procurement/bulk-sign", data={"_csrf": "tok", "pr_id": "1"})
        assert r.status_code == 403, r.status_code                       # proc_approve only
        # the route posts real form fields
        r = client(ids["w.buy"]).post("/procurement/pr/%d/po-revision" % two,
                                      data={"_csrf": "tok", "po_id": str(pb["id"]),
                                            "qty_%d" % b1: "2", "reason": "two only"})
        assert r.status_code == 302 and po_rows(two)[1]["rev"] == 2, po_rows(two)
        ex("UPDATE pr_requests SET po_rev=5 WHERE id=?", two)       # revised the old way before
        assert porev.propose_revision(two, pa["id"], {a1: {"qty": "9"}}, "", "less", buyer)[1] == "applied"
        assert [p["rev"] for p in po_rows(two)] == [6, 5], po_rows(two)
        page = client(admin["id"]).get("/procurement/pr/%d" % two).get_data(as_text=True)
        assert 'id="porev"' in page and "proc.porev.history" in page and "two only" in page
        # a pending revision renders its rungs and the sign form for a signer
        ex("DELETE FROM pr_invoices WHERE pr_id=?", one)
        assert porev.propose_revision(one, None, {l1: {"unit_price": "400"}}, "", "rise", buyer)[1] == "pending"
        rid = q("SELECT id FROM pr_po_revisions WHERE pr_id=? AND status='pending'", one)[0]["id"]
        ex("UPDATE users SET lang_pref='ar' WHERE id=?", admin["id"])
        page = client(admin["id"]).get("/procurement/pr/%d" % one).get_data(as_text=True)
        assert "/procurement/po-revision/%d/sign" % rid in page and "porev-steps" in page, "pending box"
        r = client(ids["w.req"]).post("/procurement/po-revision/%d/sign" % rid,
                                      data={"_csrf": "tok", "decision": "approve"})
        assert r.status_code == 403                                      # no proc_approve
        r = client(ids["w.buy"]).post("/procurement/po-revision/%d/cancel" % rid, data={"_csrf": "tok"})
        assert r.status_code == 302 and q("SELECT status FROM pr_po_revisions WHERE id=?",
                                          rid)[0]["status"] == "cancelled"

        # 16 requests waiting at the store rung: true count + view all, and bulk sign
        queue = []
        for n in range(16):
            pid, _ = svc.create_pr({"title": "Bulk %d" % n, "department": "Maintenance"},
                                   [{"item": "Rag", "qty": 1}], req, priced=False)
            queue.append(pid)
        ex("UPDATE pr_requests SET hold_reason='Checking spec' WHERE id=?", queue[1])
        html = client(ids["w.store"]).get("/my-work").get_data(as_text=True)
        n = len(svc.my_queue({"username": "w.store", "role": "storekeeper"}))
        assert n >= 16 and "proc.porev.view_all" in html and "(%d)" % n in html, "queue capped"
        assert html.count('name="pr_id" value=') == 15
        r = client(ids["w.store"]).post("/procurement/bulk-sign",
                                        data={"_csrf": "tok", "pr_id": [str(queue[0]), str(queue[1]),
                                                                        str(queue[0]), "junk"]})
        body = r.get_data(as_text=True)
        assert r.status_code == 200 and body.count("b-approved") == 1 and body.count("b-rejected") == 1, body[-2000:]
        assert "on hold" in body
        assert q("SELECT current_seq FROM pr_requests WHERE id=?", queue[0])[0]["current_seq"] == 2
        assert q("SELECT current_seq FROM pr_requests WHERE id=?", queue[1])[0]["current_seq"] == 1
        # no saved signature -> refused, sent to the profile
        r = client(ids["w.buy"]).post("/procurement/bulk-sign", data={"_csrf": "tok", "pr_id": "1"})
        assert r.status_code == 302 and "/profile" in r.headers["Location"], r.headers.get("Location")
        assert len(porev.bulk_sign(list(range(900000, 900060)), {"username": "w.store",
                                                                 "role": "storekeeper"})) == 50
        # the landing page carries the bulk form
        assert "data-porev-bulk" in client(ids["w.store"]).get("/procurement/").get_data(as_text=True)

        # (C) copy: demand only — no price, no vendor
        r = client(ids["w.req"]).get("/procurement/new?copy=%d" % two)
        body = r.get_data(as_text=True)
        assert r.status_code == 200 and "Seal kit" in body
        m = re.search(r"var EDIT_ITEMS = (\[.*?\]);", body)
        assert m and "unit_price" not in m.group(1) and "vendor" not in m.group(1)             and "Fake Alpha" not in m.group(1) and "Seal kit" in m.group(1), m and m.group(1)
        assert client(ids["w.view"]).get("/procurement/new?copy=%d" % two).status_code in (302, 403)
        assert client(ids["w.req"]).get("/procurement/new?copy=99999").status_code == 404
        assert 'copy=%d' % two in client(ids["w.req"]).get("/procurement/pr/%d" % two).get_data(as_text=True)

        # (D) low stock -> on order + a pre-filled request, warned on a duplicate
        ex("INSERT INTO wh_materials (code, name, kind, uom, stock_qty, reserved_qty, reorder_level, "
           "is_active, created_at) VALUES ('FAKE-TAPE-01', 'Fake elastic tape', 'trim', 'm', 10, 0, "
           "100, 1, '2026-01-01')")
        mid = q("SELECT id FROM wh_materials WHERE code='FAKE-TAPE-01'")[0]["id"]
        row = next(r for r in porev.low_stock() if r["id"] == mid)
        assert (row["on_order"], row["suggested"], row["open_prs"]) == (0, 190, []), row  # 2 x 100 - 10
        body = client(ids["w.req"]).get("/procurement/new?stock=%d" % mid).get_data(as_text=True)
        assert "FAKE-TAPE-01" in body and '"qty": 190' in body.replace("190.0", "190"), "stock prefill"
        tape, _ = svc.create_pr({"title": "Tape", "department": "Production"},
                                [{"item": "fake-tape-01", "qty": 30}], req, priced=False)
        row = next(r for r in porev.low_stock() if r["id"] == mid)
        assert (row["on_order"], row["suggested"]) == (30, 160) and row["open_prs"][0]["id"] == tape, row
        r = client(ids["w.req"]).get("/procurement/new?stock=%d" % mid)     # warned, not blocked
        assert r.status_code == 200 and "FAKE-TAPE-01" in r.get_data(as_text=True), r.status_code
        # a request whose line for it has fully arrived is no longer 'on order' for it
        both, _ = svc.create_pr({"title": "Tape and thread", "department": "Production"},
                                [{"item": "FAKE-TAPE-01", "qty": 5}, {"item": "Thread", "qty": 2}],
                                req, priced=False)
        ex("UPDATE pr_items SET received_qty=qty WHERE pr_id=? AND item='FAKE-TAPE-01'", both)
        ex("UPDATE pr_requests SET status='partially_received' WHERE id=?", both)
        row = next(r for r in porev.low_stock() if r["id"] == mid)
        assert row["on_order"] == 30 and [p["id"] for p in row["open_prs"]] == [tape], row
        dash = client(admin["id"]).get("/warehouse/").get_data(as_text=True)
        assert "low-to-pr" in dash and "FAKE-TAPE-01" in dash, "low stock panel missing"
        ex("UPDATE pr_requests SET status='cancelled' WHERE id=?", tape)
        assert next(r for r in porev.low_stock() if r["id"] == mid)["on_order"] == 0

        # (D2) maintenance spares: on order by spare_id, refill to max level
        ex("INSERT INTO mnt_spare_parts (code, name, uom, stock_qty, reorder_level, max_level, "
           "is_active, created_at) VALUES ('FAKE-SP-01', 'Fake belt', 'pcs', 0, 5, 12, 1, '2026-01-01')")
        sid = q("SELECT id FROM mnt_spare_parts WHERE code='FAKE-SP-01'")[0]["id"]
        s = porev.spare_low(sid)
        assert (s["on_order"], s["suggested"], s["open_prs"]) == (0, 12, []), s
        sp_pr, _ = svc.create_pr({"title": "Belts", "department": "Maintenance"},
                                 [{"item": "Belt", "qty": 4, "spare_id": sid}], req, priced=False)
        s = porev.spare_low(sid)
        assert (s["on_order"], s["suggested"]) == (4, 8) and s["open_prs"][0]["id"] == sp_pr, s
        r = client(ids["w.req"]).get("/procurement/new?spare=%d" % sid)
        body = r.get_data(as_text=True)
        m = re.search(r"var EDIT_ITEMS = (\[.*?\]);", body)
        assert r.status_code == 200 and m and '"spare_id": %d' % sid in m.group(1) \
            and "FAKE-SP-01" in m.group(1), m and m.group(1)
        assert client(ids["w.req"]).get("/procurement/new?spare=99999").status_code == 404
        dash = client(admin["id"]).get("/maintenance/").get_data(as_text=True)
        assert "porev-spare" in dash and "spare=%d" % sid in dash, "maintenance low stock lacks PR link"

        # every message the routes flash exists in Arabic and Turkish
        import ast
        src = Path(__file__).resolve().parents[1] / "routes" / "approvals_porev.py"
        strings = set(user_facing_strings(src))
        for node in ast.parse(src.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
                strings |= {ast.literal_eval(v) for v in node.value.values}
        missing = [s for s in strings if s not in MESSAGES]
        assert not missing, missing
    print("porev (PO revisions, bulk sign, copy, low stock): OK")


if __name__ == "__main__":
    run()
