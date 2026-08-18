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

        def po(title, qty, unit, tax=0):
            """A PR the way the UI produces one, walked to a live PO.

            Submitted with NO price (the requester price lockout), priced at the
            pricing gate, signed up the ladder, PO issued. Returns (pr_id, line_id).
            """
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Production", "currency": "EGP",
                 "vendor": "B7 Vendor"},
                [{"item": "Widget", "qty": qty, "unit_price": 0}], reqr, priced=False)
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pr_id,)).fetchone()["id"]
            conn.close()
            ok, msg = svc.price_pr(pr_id, {li: unit}, {"tax_rate": tax}, buyer)
            assert ok, "pricing gate refused: %s" % msg
            for _ in range(15):
                stage = current_stage(pr_id)
                if stage is None:
                    break
                ok, msg = svc.act_on_step(pr_id, signer[stage], "approve")
                if not ok and msg == "needs_quotes":
                    seq[0] += 1
                    for n, mult in (("A", 1.0), ("B", 1.05)):
                        svc.add_quote(pr_id, {"vendor": "B7 Vendor %s%d" % (n, seq[0]),
                                              "amount": qty * unit * mult}, buyer)
                    continue
                assert ok, "ladder stuck on %s: %s" % (title, msg)
                if msg == "approved":
                    break
            ok, msg = svc.issue_po(pr_id, buyer)
            assert ok, "PO not issued for %s: %s" % (title, msg)
            return pr_id, li

        def recv(pr_id, li, qty):
            ok, msg = svc.receive_items(pr_id, {li: qty}, signer["warehouse"])
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
                "(%s) AS qty_tol, (%s) AS tol FROM pr_requests p %s %s WHERE p.id=?"
                % (RPT._OVER_BILLED, RPT._OVER_RECEIVED, RPT._SHORT_QTY,
                   RPT._QTY_TOL, RPT._TOL, RPT._INV, RPT._ITM), (pr_id,)).fetchone()
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
        assert re_["short_qty"] == 5 and re_["short_qty"] <= re_["qty_tol"], (
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
        assert rf["short_qty"] > rf["qty_tol"], (
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

        print("value tolerance  : 10,500.00 matched / 10,500.01 blocked (floor 500)")
        print("                   102,000.00 matched / 102,000.01 blocked (2%)")
        print("qty tolerance    : 95/100 matched / 94/100 mismatch (5% = 5 units)")
        print("short-delivery   :", me["flags"][0])
        print("payment cap      : 10,000.00 refused, 9,696.01 refused, 9,696.00 paid")
        print("report vs control: same tolerance, same verdict on all four boundaries")
        print("PASS: 3.4-b7 closed on the real flow")
        return True


if __name__ == "__main__":
    run()
