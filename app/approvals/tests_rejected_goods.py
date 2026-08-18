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
        pr_id, _ = svc.create_pr(
            {"title": "Spare replenishment", "department": "General Maintenance",
             "currency": "EGP", "vendor": VENDOR},
            [{"item": "Rejected goods test part", "qty": ORDER_QTY, "unit_price": 0,
              "spare_id": sid}], reqr, priced=False)
        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
        submitted_total = conn.execute("SELECT total FROM pr_requests WHERE id=?",
                                       (pr_id,)).fetchone()["total"]
        conn.close()
        assert float(submitted_total or 0) == 0, (
            "the requester price lockout is the whole point of this harness: a UI-"
            "created request must reach the pricing gate at 0, got %r" % submitted_total)
        ok, msg = svc.price_pr(pr_id, {li: UNIT}, {"tax_rate": 0}, buyer)
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
            assert ok, "ladder stuck at %s: %s" % (stage, msg)
            if msg == "approved":
                break
        ok, msg = svc.issue_po(pr_id, buyer)
        assert ok, "PO not issued: %s" % msg
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

        print("ordered %g @ %g  ->  accepted 7, rejected 3" % (ORDER_QTY, UNIT))
        print("stock            ->", stock(), "(not 10)")
        print("debit notes      ->", dn1["dn_no"], "/", dn2["dn_no"],
              "(GRN was", grn["grn_no"] + ")")
        print("payable          -> 1,000 ordered -> 700 after the first debit note")
        print("vendor dues      ->", svc.vendor_dues().get(VENDOR))
        print("PASS: rejected goods are received by nobody, stocked nowhere, "
              "returned on their own document, and unpayable")
        return True


if __name__ == "__main__":
    run()
