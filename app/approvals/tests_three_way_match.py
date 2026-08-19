"""Audit 3.4-b7 — the three-way match: dead quantity tolerance, unblocked short
delivery, stale exception report. Each defect proved closed on the REAL flow.

Every request here is born the way the UI makes one — create_pr(priced=False)
with unit_price 0, then price_pr at the pricing gate — then walked up the real
approval ladder to a real PO, received with receive_items, invoiced with
add_invoice and paid with add_payment. Nothing is injected through the service
layer in a state a user cannot produce; that shortcut is exactly how the last
round of "done" managed to be wrong.

Boundary behaviour is the point of a tolerance, so every tolerance is tested
exactly AT its limit and one piastre / one unit beyond it, in both the value and
the quantity direction.

    python app/approvals/tests_three_way_match.py
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="b7_"), "match.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C, reports as RPT

        # One real signer per rung. Separate people, because the dual-role SoD
        # rule refuses one account signing two rungs of the same request — the
        # ladder is walked exactly as a real approval chain would walk it.
        conn = get_db()
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            uname = "b7_" + stage
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
        admin = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
        conn.close()
        buyer = signer["purchasing"]
        reqr = {"username": "b7_req", "id": 78}
        seq = [0]

        def current_stage(pr_id):
            conn = get_db()
            r = conn.execute("SELECT stage FROM pr_steps WHERE pr_id=? AND "
                             "status='pending' ORDER BY seq LIMIT 1", (pr_id,)).fetchone()
            conn.close()
            return r["stage"] if r else None

        def po_lines(title, lines, tax=0):
            """A multi-line PR the way the UI produces one, walked to a live PO.

            `lines` is [(item, unit_of_measure, qty, unit_price)]. Submitted with
            NO price (the requester price lockout), priced at the pricing gate,
            signed up the ladder, PO issued. Returns (pr_id, [line_id, ...]).
            """
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Production", "currency": "EGP",
                 "vendor": "B7 Vendor"},
                [{"item": it, "unit": uom, "qty": q, "unit_price": 0}
                 for it, uom, q, _u in lines], reqr, priced=False)
            conn = get_db()
            lids = [r["id"] for r in conn.execute(
                "SELECT id FROM pr_items WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
            conn.close()
            assert len(lids) == len(lines), (lids, lines)
            ok, msg = svc.price_pr(
                pr_id, {li: u for li, (_i, _m, _q, u) in zip(lids, lines)},
                {"tax_rate": tax}, buyer)
            assert ok, "pricing gate refused: %s" % msg
            total = sum(q * u for _i, _m, q, u in lines)
            for _ in range(15):
                stage = current_stage(pr_id)
                if stage is None:
                    break
                ok, msg = svc.act_on_step(pr_id, signer[stage], "approve")
                if not ok and msg == "needs_quotes":
                    seq[0] += 1
                    for n, mult in (("A", 1.0), ("B", 1.05)):
                        svc.add_quote(pr_id, {"vendor": "B7 Vendor %s%d" % (n, seq[0]),
                                              "amount": total * mult}, buyer)
                    continue
                assert ok, "ladder stuck on %s: %s" % (title, msg)
                if msg == "approved":
                    break
            ok, msg = svc.issue_po(pr_id, buyer)
            assert ok, "PO not issued for %s: %s" % (title, msg)
            return pr_id, lids

        def po(title, qty, unit, tax=0):
            """The single-line case, which is most of this file."""
            pr_id, lids = po_lines(title, [("Widget", "Pcs", qty, unit)], tax)
            return pr_id, lids[0]

        def recv(pr_id, li, qty):
            ok, msg = svc.receive_items(pr_id, {li: qty}, signer["warehouse"])
            assert ok, "receipt refused: %s" % msg

        def recv_many(pr_id, per_line):
            ok, msg = svc.receive_items(pr_id, per_line, signer["warehouse"])
            assert ok, "receipt refused: %s" % msg

        def bill(pr_id, no, amount, tax=0):
            ok, msg = svc.add_invoice(
                pr_id, {"invoice_no": no, "amount": amount, "tax": tax}, buyer)
            assert ok, "invoice refused: %s" % msg

        def pay(pr_id, amount):
            return svc.add_payment(pr_id, {"amount": amount}, signer["finance"])

        def report(pr_id):
            """The exception report's own SQL, on the same row the control read."""
            conn = get_db()
            r = conn.execute(
                "SELECT (%s) AS over_billed, (%s) AS over_received, (%s) AS short_qty, "
                "(%s) AS qty_exc, (%s) AS tol FROM pr_requests p %s %s WHERE p.id=?"
                % (RPT._OVER_BILLED, RPT._OVER_RECEIVED, RPT._SHORT_QTY,
                   RPT._QTY_EXC, RPT._TOL, RPT._INV, RPT._ITM), (pr_id,)).fetchone()
            conn.close()
            return dict(r)

        # ================================================================
        # (c) first, because both other checks lean on it: the report must
        #     read the SAME tolerance as the control, not the old flat 1%.
        # ================================================================
        assert C.DOAM_IN_FORCE, "these are the DOAM's numbers"

        # ---- VALUE tolerance, the 500 EGP floor half (10,000 PO) ------------
        # tolerance = max(2% of 10,000 = 200, 500) = 500 -> the floor binds.
        a, la = po("B7 value at tolerance", 10, 1000.0)
        recv(a, la, 10)
        bill(a, "B7-A", 10_500.00)                  # exactly PO + 500
        ma = svc.three_way_match(a)
        assert ma["tol"] == 500.0, ma["tol"]
        assert ma["price_ok"] and ma["status"] == "matched", ma
        okp, msgp = pay(a, 100.0)
        assert okp, "an at-tolerance invoice must not block payment: %s" % msgp
        ra = report(a)
        assert ra["tol"] == ma["tol"], ("report tolerance %r != control %r"
                                        % (ra["tol"], ma["tol"]))
        assert ra["over_billed"] == 0, (
            "the report called an at-tolerance invoice an exception while the "
            "control called it matched: %r" % ra)

        b, lb = po("B7 value one piastre over", 10, 1000.0)
        recv(b, lb, 10)
        bill(b, "B7-B", 10_500.01)                  # one piastre past the same line
        mb = svc.three_way_match(b)
        assert not mb["price_ok"] and mb["status"] == "mismatch", mb
        okp, msgp = pay(b, 100.0)
        assert not okp and msgp == "match_blocked", (okp, msgp)
        rb = report(b)
        assert rb["over_billed"] > 0, rb

        # ---- VALUE tolerance, the 2% half (100,000 PO) ----------------------
        # tolerance = max(2% of 100,000 = 2,000, 500) = 2,000 -> the pct binds.
        c, lc = po("B7 pct at tolerance", 100, 1000.0)
        recv(c, lc, 100)
        bill(c, "B7-C", 102_000.00)
        mc = svc.three_way_match(c)
        assert mc["tol"] == 2000.0, mc["tol"]
        assert mc["price_ok"] and mc["status"] == "matched", mc
        assert report(c)["over_billed"] == 0, report(c)

        d, ld = po("B7 pct one piastre over", 100, 1000.0)
        recv(d, ld, 100)
        bill(d, "B7-D", 102_000.01)
        md = svc.three_way_match(d)
        assert not md["price_ok"] and md["status"] == "mismatch", md
        assert report(d)["over_billed"] > 0, report(d)

        # ================================================================
        # (a) the QUANTITY tolerance, which had zero readers
        # ================================================================
        # 100 ordered, 95 received -> exactly 5% short -> inside the tolerance.
        e, le = po("B7 qty at tolerance", 100, 100.0)
        recv(e, le, 95)
        bill(e, "B7-E", 10_000.00)                  # vendor bills the whole order
        me = svc.three_way_match(e)
        assert me["qty_tol"] == 5.0, me["qty_tol"]
        assert me["short_qty"] == 5.0 and me["short_value"] == 500.0, me
        assert me["qty_ok"] and me["status"] == "matched", me
        assert any("Short delivery" in f for f in me["flags"]), (
            "an in-tolerance shortfall must still be VISIBLE: %r" % me["flags"])
        re_ = report(e)
        assert re_["short_qty"] == 5 and not re_["qty_exc"], (
            "report must state the shortfall but not call it an exception: %r" % re_)

        # 100 ordered, 94 received -> 6 short, one unit past the 5% line.
        f, lf = po("B7 qty one unit over", 100, 100.0)
        recv(f, lf, 94)
        bill(f, "B7-F", 9_400.00)                   # honest invoice: value is clean,
        mf = svc.three_way_match(f)                 # so only the QUANTITY can fail
        assert mf["price_ok"] and mf["receipt_inv_ok"], (
            "isolate the quantity direction: the value checks must pass here", mf)
        assert mf["short_qty"] == 6.0 and not mf["qty_ok"], mf
        assert mf["status"] == "mismatch", mf
        rf = report(f)
        assert rf["qty_exc"], (
            "report must call a 6-of-100 shortfall an exception: %r" % rf)

        # ...and the constant is genuinely the reader, not a 5 typed into the code:
        saved = C.MATCH_QTY_TOLERANCE_PCT
        try:
            C.MATCH_QTY_TOLERANCE_PCT = 6.0
            assert svc.three_way_match(f)["qty_ok"], (
                "MATCH_QTY_TOLERANCE_PCT is still not read by the match")
            C.MATCH_QTY_TOLERANCE_PCT = 0.0
            assert not svc.three_way_match(e)["qty_ok"], (
                "MATCH_QTY_TOLERANCE_PCT is still not read by the match")
        finally:
            C.MATCH_QTY_TOLERANCE_PCT = saved
        svc.three_way_match(e)                      # restore the cached verdicts
        svc.three_way_match(f)

        # ================================================================
        # (b) short delivery caps the payment at the received value
        # ================================================================
        # 100 @ 100 ordered, 96 received (4 short: INSIDE the 5% tolerance, so
        # every match verdict is green), vendor invoices the whole 10,000. This
        # is the case that quietly paid 10,000 for 9,600 of goods.
        g, lg = po("B7 short delivery cap", 100, 100.0)
        recv(g, lg, 96)
        bill(g, "B7-G", 10_000.00)
        mg = svc.three_way_match(g)
        assert mg["status"] == "matched" and mg["qty_ok"], mg
        assert mg["received_grand"] == 9_600.00 and mg["short_value"] == 400.00, mg

        okp, msgp = pay(g, 10_000.00)
        assert not okp and msgp == "exceeds_received", (
            "paying the whole PO for a short delivery is the defect: %s / %s"
            % (okp, msgp))
        # cap = received 9,600 + the 1% payment tolerance = 9,696
        okp, msgp = pay(g, 9_696.01)
        assert not okp and msgp == "exceeds_received", (okp, msgp)
        okp, msgp = pay(g, 9_696.00)
        assert okp, "exactly at the cap must be accepted: %s" % msgp

        # ...and the same 10,000 on an identical request sails through with the
        # admin override, which is the proof that NOTHING ELSE was stopping it:
        # the PO cap, the invoiced cap and all three match verdicts are green,
        # so before this control the company simply paid 10,000 for 9,600.
        g2, lg2 = po("B7 short delivery, admin override", 100, 100.0)
        recv(g2, lg2, 96)
        bill(g2, "B7-G2", 10_000.00)
        okp, msgp = svc.add_payment(g2, {"amount": 10_000.00}, admin, force=True)
        assert okp, "no other gate objects to this payment: %s" % msgp

        # A fully received order is NOT capped: the new control must not touch
        # the ordinary path.
        h, lh = po("B7 full delivery", 10, 1000.0)
        recv(h, lh, 10)
        bill(h, "B7-H", 10_000.00)
        okp, msgp = pay(h, 10_000.00)
        assert okp, "a fully received order must still be payable in full: %s" % msgp

        # An advance (no invoice yet) is governed by §4.3, not by this cap — it
        # must fail for its own reason, never for a received value of zero.
        i, _li = po("B7 advance", 10, 1000.0)
        okp, msgp = pay(i, 1_000.00)
        assert not okp and msgp.startswith("advance_"), (
            "the received-value cap must not swallow the advance gate: %s" % msgp)

        # ================================================================
        # (d) VERIFIER FINDING (major): the quantity tolerance was applied to a
        #     raw SUM across lines of different units and wildly different unit
        #     values, so a materially short delivery read as "matched".
        #     1,000 Mtr @ 1 + 2 Pcs @ 5,000 = 11,000 EGP (the shape of a thread
        #     line beside two machines; the items are named neutrally because
        #     "thread" is a DIRECT MATERIAL and would pull in the §3.4 gate).
        #     The metres arrive in full, NEITHER unit does: 2 of 1,002 units
        #     short = 0.2% of the summed quantity, inside a 5% scalar tolerance —
        #     while 10,000 of the 11,000 EGP ordered never arrived.
        # ================================================================
        m_id, m_li = po_lines("B7 mixed units", [("Widget A", "Mtr", 1000, 1.0),
                                                 ("Widget B", "Pcs", 2, 5000.0)])
        recv_many(m_id, {m_li[0]: 1000, m_li[1]: 0})
        bill(m_id, "B7-M", 11_000.00)
        mm = svc.three_way_match(m_id)
        assert mm["ordered_grand"] == 11_000.00 and mm["received_value"] == 1_000.00, mm
        assert not mm["qty_ok"], (
            "a whole line missing must fail the quantity check even though the "
            "SUMMED shortfall is 2 of 1,002 units: %r" % mm)
        assert mm["status"] == "mismatch", mm
        rm = report(m_id)
        assert rm["qty_exc"], (
            "the Match exceptions KPI must see the same shortfall the control "
            "sees, or the report and the control drift: %r" % rm)
        # ...and a per-line shortfall INSIDE the tolerance still passes on a
        # multi-line order, so the fix is per-line, not "any shortfall at all".
        n_id, n_li = po_lines("B7 mixed units, both in tolerance",
                              [("Widget A", "Mtr", 1000, 1.0),
                               ("Widget B", "Pcs", 20, 500.0)])
        recv_many(n_id, {n_li[0]: 960, n_li[1]: 19})       # 4% and 5% short
        bill(n_id, "B7-N", 10_460.00)
        mn = svc.three_way_match(n_id)
        assert mn["qty_ok"] and mn["status"] == "matched", mn
        assert not report(n_id)["qty_exc"], report(n_id)

        # ================================================================
        # (e) VERIFIER FINDING (minor): the short-delivery flag quoted the
        #     received value, but add_payment caps against `payable`, which an
        #     open debit note pulls strictly lower. The buyer must read the
        #     number that is actually enforced.
        # ================================================================
        assert ("capped at %s" % format(mg["payable"], ",.2f")) in mg["flags"][0], (
            "the flag must quote the ENFORCED ceiling: %r" % mg["flags"][0])

        # ================================================================
        # (f) VERIFIER FINDING (minor): advance_gate_check compared NET invoiced
        #     against GROSS payments, so on a taxed PR the VAT slice of an
        #     ordinary payment looked like an unauthorised advance and the buyer
        #     was told to approve the vendor instead of that the goods are short.
        # ================================================================
        t_id, t_li = po("B7 taxed short delivery", 100, 100.0, tax=14)
        recv(t_id, t_li, 96)
        bill(t_id, "B7-T", 10_000.00, tax=1_400.00)        # full order, gross 11,400
        mt = svc.three_way_match(t_id)
        assert mt["received_grand"] == 10_944.00, mt       # 9,600 x 1.14
        okp, msgp = pay(t_id, 11_400.00)
        assert not okp and msgp == "exceeds_received", (
            "a taxed short delivery must be refused for being SHORT, not for "
            "looking like an advance: %s / %s" % (okp, msgp))
        okp, msgp = pay(t_id, 11_053.45)                   # cap + 1% + one piastre
        assert not okp and msgp == "exceeds_received", (okp, msgp)
        okp, msgp = pay(t_id, 11_053.44)                   # 9,600 x 1.14 x 1.01
        assert okp, "exactly at the taxed cap must be accepted: %s" % msgp

        # ================================================================
        # (g) VERIFIER FINDING (minor): every payment refusal was hardcoded
        #     English in a trilingual module.
        # ================================================================
        for lang in ("en", "ar", "tr"):
            ui = svc.labels(lang)["ui"]
            for key in ("not_payable", "match_blocked", "over_payment",
                        "exceeds_invoiced", "exceeds_received",
                        "advance_not_authorised", "advance_exceeds_authorised",
                        "advance_guarantee_required", "advance_vendor_not_approved"):
                assert (ui.get(key + "_flash") or "").strip(), (lang, key)
            if lang != "en":
                assert ui["exceeds_received_flash"] !=                     svc.labels("en")["ui"]["exceeds_received_flash"], lang

        # ...and on the REAL route, in Arabic. `g` is already paid to its cap,
        # so any further payment is refused with exceeds_received.
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref='ar' WHERE id=1")
        conn.commit()
        conn.close()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["uid"], sess["ep"], sess["_csrf_token"] = 1, 0, "tok"
        # 100 more: still inside the PO total and inside the invoiced total, so
        # only the received-value cap can refuse it.
        r = client.post("/procurement/pr/%d/payment" % g,
                        data={"_csrf": "tok", "amount": "100"},
                        follow_redirects=True)
        body = r.get_data(as_text=True)
        assert r.status_code == 200, r.status_code
        ar = svc.labels("ar")["ui"]["exceeds_received_flash"]
        assert ar[:40] in body, (
            "the refusal reached an Arabic reader in English: %s"
            % ascii([l for l in body.splitlines() if "TC_FLASH" in l])[:400])
        assert "Short delivery: this payment" not in body, "English leaked too"
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=NULL WHERE id=1")
        conn.commit()
        paid_now = conn.execute("SELECT paid_amount FROM pr_requests WHERE id=?",
                                (g,)).fetchone()["paid_amount"]
        conn.close()
        assert float(paid_now) == 9_696.00, ("the refusal must not move the money",
                                             paid_now)

        # ================================================================
        # (h) VERIFIER FINDING (minor): the governance page advertised only the
        #     2%, omitting the 500 EGP floor (the binding half under 25,000) and
        #     the quantity tolerance entirely.
        # ================================================================
        wf = svc.workflow_view("Production", "en")
        assert wf["match_tolerance_abs"] == C.MATCH_TOLERANCE_ABS, wf
        assert wf["match_qty_tolerance_pct"] == C.MATCH_QTY_TOLERANCE_PCT, wf
        page = " ".join(client.get("/procurement/workflow")
                        .get_data(as_text=True).split())
        assert '2% / 500 EGP / 5% <span data-i18n="pwf.qty_word">qty</span>' in page, (
            "the governance page still advertises only half the tolerance")

        print("mixed-unit PO   : 1,000 Mtr in full + 0 of 2 Pcs ->", mm["status"])
        print("                  report exception:", bool(rm["qty_exc"]))
        print("value tolerance  : 10,500.00 matched / 10,500.01 blocked (floor 500)")
        print("                   102,000.00 matched / 102,000.01 blocked (2%)")
        print("qty tolerance    : 95/100 matched / 94/100 mismatch (5% = 5 units)")
        print("short-delivery   :", me["flags"][0])
        print("payment cap      : 10,000.00 refused, 9,696.01 refused, 9,696.00 paid")
        print("taxed short PO   : 11,400 refused as exceeds_received, 11,053.44 paid")
        print("flag quotes cap  :", mg["flags"][0].rsplit(";", 1)[-1].strip())
        print("report vs control: same tolerance, same verdict on all four boundaries")
        print("PASS: 3.4-b7 closed on the real flow")
        return True


if __name__ == "__main__":
    run()
