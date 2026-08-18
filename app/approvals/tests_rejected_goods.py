"""Audit 3.4-b8 — rejected goods had nowhere to go: no accept/reject split on
receipt, no return to vendor, no debit note, no supplier dues.

Proved on the REAL flow, end to end, exactly as the UI produces it: create_pr
with the requester price lockout (every line submitted at 0), price_pr at the
Purchasing pricing gate, the real approval ladder walked one rung per real
signer, a real PO, then a receipt that ACCEPTS 7 and REJECTS 3 of a 10-piece
order. Nothing is injected through the service layer in a state a user cannot
produce — that shortcut is how the previous round of "done" managed to be wrong.

What is proved here:
  * the rejected quantity never becomes received_qty;
  * it never reaches stock — the spare rises by 7, not 10;
  * it goes back to the supplier as a return carrying a debit note with its own
    document number, distinct from the GRN's and from the next debit note's;
  * the payable ceiling drops by the rejected value, and a payment above it is
    refused by add_payment rather than merely flagged;
  * the open debit note shows as an outstanding due against the vendor, on the
    same list_vendors() rows the Vendors screen renders;
  * settling the debit note releases both the due and the ceiling.

And the four repairs the verifier asked for, each proved the same way:
  * b8-r1  an over-delivery that is partly rejected produces ONE payable figure,
           whichever column the clerk types in — the surplus is quarantined, not
           debited, so the goods that WERE accepted stay payable;
  * b8-r2  /reporting/proc_payables shows the debited-back value and nets it out
           of Outstanding, so the report stops contradicting the payment gate;
  * b8-r3  proc_pay survives an admin re-saving the Financial Director role over
           HTTP with exactly the boxes the editor renders;
  * b8-r4  an unreadable returns register FLAGS itself instead of quietly
           removing the payable cap;
  * b8-r5  the ceiling the payment gate enforces is stated on the page as a
           figure, not only narrated inside a flag.

    python app/approvals/tests_rejected_goods.py
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    # NOT an environment variable: TC_DB is not read by this app, and a stray
    # env var would run these checks against the live beta database.
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="b8_"), "reject.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


VENDOR = "B8 Vendor"
UNIT = 100.0          # priced at the gate, never by the requester
ORDER_QTY = 10.0


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C, pdf as pdfgen

        # ---- one real signer per rung (dual-role SoD refuses one account twice)
        conn = get_db()
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            uname = "b8_" + stage
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (uname, "x", uname, role))
            signer[stage] = {"username": uname, "role": role}
        conn.commit()
        for stage in signer:
            signer[stage]["id"] = conn.execute(
                "SELECT id FROM users WHERE username=?",
                (signer[stage]["username"],)).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO proc_vendors (name, is_active, created_at) "
                     "VALUES (?,1,'2026-01-01')", (VENDOR,))
        # a real maintenance spare, so the receipt posts into a real stock figure
        conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty, reorder_level, "
                     "max_level, avg_cost, is_active) VALUES (?,?,?,?,?,?,1)",
                     ("B8-SP", "Rejected goods test part", 0, 0, 10000, UNIT))
        sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code=?",
                           ("B8-SP",)).fetchone()["id"]
        conn.commit()
        conn.close()
        buyer, store = signer["purchasing"], signer["warehouse"]
        reqr = {"username": "b8_req", "id": 88}

        def stock():
            conn = get_db()
            q = conn.execute("SELECT stock_qty FROM mnt_spare_parts WHERE id=?",
                             (sid,)).fetchone()["stock_qty"]
            conn.close()
            return float(q or 0)

        def current_stage(pr_id):
            conn = get_db()
            r = conn.execute("SELECT stage FROM pr_steps WHERE pr_id=? AND status='pending' "
                             "ORDER BY seq LIMIT 1", (pr_id,)).fetchone()
            conn.close()
            return r["stage"] if r else None

        # ---- a PO born the way the UI makes one -------------------------------
        def make_po(title, tax=0):
            """create_pr with EVERY line at 0 and priced=False, the submitted total
            asserted to be 0, then price_pr at the gate, the real ladder one rung
            per distinct signer, then issue_po. Nothing is injected in a state the
            UI cannot produce — that shortcut is how "done" was wrong before."""
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "General Maintenance",
                 "currency": "EGP", "vendor": VENDOR},
                [{"item": "Rejected goods test part", "qty": ORDER_QTY,
                  "unit_price": 0, "spare_id": sid}], reqr, priced=False)
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pr_id,)).fetchone()["id"]
            submitted_total = conn.execute("SELECT total FROM pr_requests WHERE id=?",
                                           (pr_id,)).fetchone()["total"]
            conn.close()
            assert float(submitted_total or 0) == 0, (
                "the requester price lockout is the whole point of this harness: a "
                "UI-created request must reach the pricing gate at 0, got %r"
                % submitted_total)
            ok, msg = svc.price_pr(pr_id, {li: UNIT}, {"tax_rate": tax}, buyer)
            assert ok, "pricing gate refused: %s" % msg
            for _ in range(15):
                stage = current_stage(pr_id)
                if stage is None:
                    break
                ok, msg = svc.act_on_step(pr_id, signer[stage], "approve")
                if not ok and msg == "needs_quotes":
                    for n, mult in (("A", 1.0), ("B", 1.05)):
                        svc.add_quote(pr_id, {"vendor": "%s %s" % (VENDOR, n),
                                              "amount": ORDER_QTY * UNIT * mult}, buyer)
                    continue
                if not ok and msg == "deviation_memo_required":
                    # later runs re-order a part that now has stock on the shelf,
                    # which is a real §4.4 deviation — write the memo the DOAM
                    # asks for and carry on, exactly as the buyer would.
                    svc.set_doam_document(pr_id, "deviation_memo",
                                          "Replenishment agreed with the supplier "
                                          "ahead of the shutdown.", buyer)
                    continue
                assert ok, "ladder stuck at %s: %s" % (stage, msg)
                if msg == "approved":
                    break
            ok, msg = svc.issue_po(pr_id, buyer)
            assert ok, "PO not issued: %s" % msg
            return pr_id, li

        pr_id, li = make_po("Spare replenishment")
        assert stock() == 0, "nothing is received yet"

        # ================================================================
        # RECEIVE 10, ACCEPT 7, REJECT 3
        # ================================================================
        ok, msg = svc.receive_items(pr_id, {li: 7}, store, rejects={li: 3},
                                    reject_reason="3 units cracked casing — off spec")
        assert ok, "receipt refused: %s" % msg
        assert msg == "partial", (
            "3 of 10 rejected leaves the line outstanding, so the PO cannot be "
            "'received': %s" % msg)

        conn = get_db()
        got = float(conn.execute("SELECT received_qty FROM pr_items WHERE id=?",
                                 (li,)).fetchone()["received_qty"] or 0)
        status = conn.execute("SELECT status FROM pr_requests WHERE id=?",
                              (pr_id,)).fetchone()["status"]
        conn.close()
        assert got == 7, "rejected quantity must NOT count as received: got %r" % got
        assert status == "partially_received", status
        assert stock() == 7, (
            "the rejected 3 must never reach stock — spare rose to %g, not 7" % stock())

        bundle = svc.get_pr(pr_id)
        grn = bundle["grns"][-1]
        rets = bundle["returns"]
        assert len(rets) == 1, "one rejection == one return record: %r" % rets
        dn1 = rets[0]
        assert dn1["dn_no"] and dn1["dn_no"].startswith("DN-"), dn1
        assert dn1["dn_no"] != grn["grn_no"], (
            "the debit note must carry its OWN number, not the GRN's: %s / %s"
            % (dn1["dn_no"], grn["grn_no"]))
        assert float(dn1["qty"]) == 3 and float(dn1["total"]) == 300.0, dn1
        assert dn1["vendor"] == VENDOR and dn1["pr_id"] == pr_id, (
            "the return is recorded against BOTH the PR and the vendor: %r" % dn1)
        assert dn1["reason"] and "off spec" in dn1["reason"], dn1
        assert dn1["status"] == "open", dn1
        # the GRN itself carries the split, so the printed note tells the truth
        import json
        gl = json.loads(grn["lines_json"])[0]
        assert (gl["accepted"], gl["rejected"]) == (7, 3), gl

        # ---- the payable value -------------------------------------------------
        m = svc.three_way_match(pr_id)
        assert m["ordered_grand"] == 1000.0, m
        assert m["received_grand"] == 700.0, m
        assert m["debit_open"] == 300.0, m
        assert m["payable"] == 700.0, (
            "10 ordered at 100, 3 rejected -> 700 payable, not %r" % m["payable"])
        assert any("debit note" in f for f in m["flags"]), m["flags"]

        # the supplier bills for all ten anyway — the usual way this is discovered
        ok, msg = svc.add_invoice(pr_id, {"invoice_no": "B8-INV-1", "amount": 1000},
                                  buyer)
        assert ok, msg
        ok, msg = svc.add_payment(pr_id, {"amount": 800}, signer["finance"])
        assert not ok and msg == "exceeds_received", (
            "800 against 700 of payable value must be REFUSED, not flagged: %s / %s"
            % (ok, msg))
        ok, msg = svc.add_payment(pr_id, {"amount": 700}, signer["finance"])
        assert ok, "the value actually received is payable: %s" % msg
        assert msg == "paid", (
            "700 settles a PO with a 300 debit note against it: %s" % msg)

        # ---- the vendor's outstanding dues, where vendor information lives -----
        dues = svc.vendor_dues()
        assert dues.get(VENDOR, {}).get("amount") == 300.0, dues
        row = next(v for v in svc.list_vendors() if v["name"] == VENDOR)
        assert row["dues"] == 300.0 and row["dues_count"] == 1, (
            "the Vendors screen renders list_vendors() rows — the dues must be "
            "on them: %r" % row)

        # ---- a SECOND rejection gets its OWN number ----------------------------
        ok, msg = svc.receive_items(pr_id, {}, store, rejects={li: 2},
                                    reject_reason="second delivery attempt, same fault")
        assert ok, "a rejection-only receipt must be recordable: %s" % msg
        bundle = svc.get_pr(pr_id)
        dn2 = bundle["returns"][-1]
        assert dn2["dn_no"] != dn1["dn_no"], (
            "every debit note is its own document: %s reused" % dn2["dn_no"])
        assert stock() == 7, "a rejection-only receipt must not move stock"
        m = svc.three_way_match(pr_id)
        assert m["debit_open"] == 500.0 and m["payable"] == 500.0, (
            "two open debit notes (300 + 200) cap the order at 500: %r" % m)

        # ---- settling releases the due and the ceiling -------------------------
        ok, msg = svc.settle_return(dn1["id"], buyer)
        assert ok and msg == dn1["dn_no"], (ok, msg)
        assert svc.settle_return(dn1["id"], buyer) == (False, "already_settled")
        assert svc.vendor_dues().get(VENDOR, {}).get("amount") == 200.0, svc.vendor_dues()
        m = svc.three_way_match(pr_id)
        assert m["debit_open"] == 200.0 and m["payable"] == 700.0, (
            "one 200 debit left: the ceiling is min(700 received, 800 order-less-"
            "debit) = 700, NOT 500 — the rejection must not be deducted twice: %r" % m)

        # ---- the document prints, in the same family as PR/PO/GRN --------------
        try:
            import reportlab            # noqa: F401
        except ImportError:
            print("(reportlab absent — PDF rendering skipped)")
        else:
            data = pdfgen.debit_note_pdf(svc.get_pr(pr_id), dn2)
            assert data[:4] == b"%PDF" and len(data) > 1500, len(data)
            assert C.FORM_CODES["dn"] not in (C.FORM_CODES["grn"], C.FORM_CODES["po"])
            # the GRN still prints, now carrying the accept/reject split
            assert pdfgen.grn_pdf(svc.get_pr(pr_id), grn)[:4] == b"%PDF"

        # ==================================================================
        # REPAIRS — the four defects the verifier found on the round above.
        # ==================================================================
        TAX = 14.0
        GRAND = round(ORDER_QTY * UNIT * (1 + TAX / 100.0), 2)      # 1,140.00

        # ---- b8-r1: ONE physical delivery, ONE payable ---------------------
        # 10 ordered @ 100 + 14% VAT, the supplier sends 13 of which 3 are
        # faulty. Whichever box the clerk types in, the company received the 10
        # it bought and owes 1,140 for them.
        pa, ia = make_po("Over-delivery, accept column", tax=TAX)
        before = stock()
        ok, msg = svc.receive_items(pa, {ia: 13}, store)
        assert ok, msg
        ma = svc.three_way_match(pa)
        assert stock() - before == 10, "only the ordered 10 may reach stock"
        assert ma["received_qty"] == 10 and ma["debit_open"] == 0, ma
        assert abs(ma["payable"] - GRAND) < 0.01, (
            "the 10 ordered arrived: the whole 1,140 is payable, got %r" % ma["payable"])

        pb, ib = make_po("Over-delivery, reject column", tax=TAX)
        before = stock()
        ok, msg = svc.receive_items(pb, {ib: 10}, store, rejects={ib: 3},
                                    reject_reason="3 surplus units, cracked casing")
        assert ok, msg
        mb = svc.three_way_match(pb)
        assert stock() - before == 10, "only the ordered 10 may reach stock"
        assert mb["received_qty"] == 10 and mb["short_qty"] == 0, mb
        assert mb["debit_open"] == 0, (
            "the order was delivered in full and invoiced in full; the 3 rejected "
            "units were never ordered, so there is nothing to debit back: %r" % mb)
        assert abs(mb["payable"] - ma["payable"]) < 0.01, (
            "the same delivery must not produce two payable figures: accept-column "
            "%r vs reject-column %r" % (ma["payable"], mb["payable"]))
        bb = svc.get_pr(pb)
        assert bb["returns"] == [], (
            "a surplus rejection raises no debit note: %r" % bb["returns"])
        qb = bb["quarantine"]
        assert len(qb) == 1 and abs(float(qb[0]["qty"]) - 3) < 1e-9, qb
        assert (qb[0]["status"], qb[0]["resolution"]) == ("resolved", "rejected_surplus"), (
            "surplus that was refused at the door is recorded resolved, not offered "
            "for 'accept as free issue' — it left with the driver: %r" % qb[0])
        # ...and the accepted goods stay fully payable
        ok, msg = svc.add_invoice(pb, {"invoice_no": "B8-OD-1",
                                       "amount": ORDER_QTY * UNIT,
                                       "tax": ORDER_QTY * UNIT * TAX / 100.0}, buyer)
        assert ok, msg
        ok, msg = svc.add_payment(pb, {"amount": GRAND}, signer["finance"])
        assert ok and msg == "paid", (
            "the 10 accepted units are payable in full: %s / %s" % (ok, msg))

        # the split itself: reject 4 on a 10-line with 8 accepted -> 2 debited
        # (the order covers them), 2 quarantined as surplus.
        pc, ic = make_po("Over-delivery, part debited part surplus", tax=TAX)
        ok, msg = svc.receive_items(pc, {ic: 8}, store, rejects={ic: 4},
                                    reject_reason="4 rejected on a 12-piece drop")
        assert ok, msg
        mc = svc.three_way_match(pc)
        bc = svc.get_pr(pc)
        assert abs(float(bc["returns"][0]["qty"]) - 2) < 1e-9, (
            "only the 2 the ORDER covers may be debited: %r" % bc["returns"])
        assert abs(float(bc["quarantine"][0]["qty"]) - 2) < 1e-9, bc["quarantine"]
        assert abs(mc["debit_open"] - 228.0) < 0.01, mc          # 2 x 100 x 1.14
        assert abs(mc["payable"] - 912.0) < 0.01, (              # 8 x 100 x 1.14
            "8 accepted of 10 ordered, 2 debited -> 912 payable: %r" % mc["payable"])

        # ---- b8-r2: the payables report agrees with the payment gate -------
        from app.services import reporting as RPT
        rows = {r["pr_no"]: r for r in
                RPT.run(RPT.get("proc_payables"), {"vendor": VENDOR})["rows"]}
        rc = rows[svc.get_pr(pc)["pr"]["pr_no"]]
        assert abs(float(rc["dn_open"]) - 228.0) < 0.01, (
            "the report must SHOW the debited-back value, not merely apply it: %r" % rc)
        assert abs(float(rc["outstanding"]) - 912.0) < 0.01, (
            "outstanding must be grand - debited - paid = 912, not 1,140: %r" % rc)
        rb = rows[svc.get_pr(pb)["pr"]["pr_no"]]
        assert abs(float(rb["dn_open"])) < 0.01 and abs(float(rb["outstanding"])) < 0.01, (
            "a fully paid order with no debit note is untouched by the new join: %r" % rb)

        # ---- b8-r3: proc_pay is grantable through the REAL settings path ---
        from app import security as sec
        assert "proc_pay" in C.PROC_PERMISSIONS and "proc_pay" in sec.PERMISSIONS, (
            "proc_pay must be a registered permission or /admin/roles/save filters "
            "it out of every role save")
        cat = [k for items in sec.permission_catalogue().values() for k, _l, _d in items]
        assert "proc_pay" in cat, "the Admin -> Roles editor must offer a checkbox"

        # ...and prove the destructive half over HTTP: an admin re-saving the
        # Financial Director role with exactly the boxes the editor renders used
        # to STRIP proc_pay, because /admin/roles/save filters perms[] against
        # PERMISSIONS and load_db_roles then REPLACES the role's code perms.
        conn = get_db()
        for uname, role in (("b8_admin", "super_admin"),
                            ("b8_fd", "financial_director")):
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (uname, "x", uname, role))
        conn.commit()
        uid = {r["username"]: r["id"] for r in conn.execute(
            "SELECT id, username FROM users WHERE username IN ('b8_admin','b8_fd')")}
        conn.close()
        import flask
        cli = app.test_client()

        def login(uname):
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid[uname], 0, "tok"
            # current_user() caches on flask.g, and a test request run inside an
            # already-pushed app context REUSES that g — without this, every
            # request after the first would be answered as the first user.
            flask.g.pop("user", None)

        def fd_can_pay():
            login("b8_fd")
            r = cli.post("/procurement/pr/%d/payment" % pb,
                         data={"_csrf": "tok", "amount": "1"})
            # a bounce to /login would also be "not 403" — reject it, or this
            # whole check passes on a session that was never established.
            assert "/login" not in (r.headers.get("Location") or ""), (
                "not signed in — the check would be vacuous")
            return r.status_code != 403

        assert fd_can_pay(), "the Financial Director holds proc_pay before the save"
        eff = sec.effective_roles()["financial_director"]
        checked = [p for p in cat if p in eff["perms"]]      # the boxes as rendered
        assert "proc_pay" in checked, (
            "if the editor cannot render the box, the save cannot keep it: %r" % checked)
        login("b8_admin")
        r = cli.post("/admin/roles/save",
                     data={"_csrf": "tok", "role_key": "financial_director",
                           "label": eff["label"], "perms[]": checked})
        assert r.status_code in (200, 302), r.status_code
        assert "proc_pay" in sec.effective_roles()["financial_director"]["perms"], (
            "re-saving the role unchanged dropped proc_pay: %r"
            % sec.effective_roles()["financial_director"]["perms"])
        assert fd_can_pay(), (
            "an ordinary admin action switched the payment permission OFF and the "
            "Financial Director now gets 403 on the payment endpoint")

        # ---- b8-r5: the enforced ceiling is STATED, not only narrated ------
        login("b8_admin")
        page = cli.get("/procurement/pr/%d" % pc).get_data().decode("utf-8")
        assert 'data-i18n="proc.rtv.payable"' in page, (
            "the payable tile is missing from the match KPI strip")
        assert "912.00" in page and "228.00" in page, (
            "the page must state the ceiling the payment gate enforces as a figure")
        clean = cli.get("/procurement/pr/%d" % pb).get_data().decode("utf-8")
        assert 'data-i18n="proc.rtv.payable"' not in clean, (
            "no debit note, no tile — the strip stays three-up")

        # ---- b8-r4: a control that cannot read itself says so --------------
        conn = get_db()
        conn.execute("ALTER TABLE pr_returns RENAME TO pr_returns_hidden")
        conn.commit()
        conn.close()
        try:
            blind = svc.three_way_match(pc)
        finally:
            conn = get_db()
            conn.execute("ALTER TABLE pr_returns_hidden RENAME TO pr_returns")
            conn.commit()
            conn.close()
        assert blind["returns_ok"] is False, blind
        assert any("could not be read" in f for f in blind["flags"]), (
            "an unreadable returns register must be REPORTED, not silently treated "
            "as 'no returns': %r" % blind["flags"])
        assert blind["status"] != "matched", (
            "a match that could not evaluate the payable ceiling must not read "
            "'matched': %r" % blind["status"])
        assert svc.three_way_match(pc)["returns_ok"] is True, "restored"

        print("ordered %g @ %g  ->  accepted 7, rejected 3" % (ORDER_QTY, UNIT))
        print("debit notes      ->", dn1["dn_no"], "/", dn2["dn_no"],
              "(GRN was", grn["grn_no"] + ")")
        print("payable          -> 1,000 ordered -> 700 after the first debit note")
        print("vendor dues      ->", svc.vendor_dues().get(VENDOR))
        print("13 delivered, 3 faulty -> payable %.2f whichever column the clerk "
              "types in (was 1,140 / 798)" % mb["payable"])
        print("8 kept, 4 rejected     -> debited %.2f, %g surplus quarantined, "
              "payable %.2f" % (mc["debit_open"], float(bc["quarantine"][0]["qty"]),
                                mc["payable"]))
        print("payables report        -> debited back %.2f, outstanding %.2f "
              "(was 1,140.00)" % (float(rc["dn_open"]), float(rc["outstanding"])))
        print("PASS: rejected goods are received by nobody, stocked nowhere, "
              "returned on their own document, and unpayable")
        return True


if __name__ == "__main__":
    run()
