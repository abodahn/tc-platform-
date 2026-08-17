"""DOAM §4.3 — advance payments.

"Advance up to 25% of PO value: FIN-D. Above 30%: CFO or MD, with a bank
guarantee when the order exceeds 500,000 EGP. No advance to a supplier off the
approved vendor list."

The point of these checks is that the rule blocks MONEY, not just that it is
written down: every case goes through add_payment(), which is what the payment
form and the API both call.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "advance.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        # --- the rule itself, at the edges the document names -----------------
        assert C.advance_rule(100000, 25.0)["stages"] == ["finance"]
        assert C.advance_rule(100000, 25.1)["stages"] == ["cfo", "ceo"]
        assert C.advance_rule(600000, 25.1)["guarantee"] is True
        assert C.advance_rule(600000, 25.0)["guarantee"] is False, \
            "a guarantee is only demanded once the advance exceeds the FIN-D ceiling"
        assert C.advance_rule(500000, 40.0)["guarantee"] is False, \
            "500,000 is not 'exceeds 500,000'"

        # --- a real PO to pay against ----------------------------------------
        conn = get_db()
        conn.execute("INSERT INTO proc_vendors (name, is_active) VALUES (?,1)",
                     ("Approved Supplier",))
        conn.execute("INSERT INTO proc_vendors (name, is_active) VALUES (?,0)",
                     ("Delisted Supplier",))
        conn.commit()
        conn.close()

        pr_id, _ = svc.create_pr(
            {"title": "Fabric", "department": "Production", "vendor": "Approved Supplier"},
            [{"item": "Fabric", "qty": 1, "unit_price": 100000}],
            {"username": "buyer", "id": 1}, submit=False)
        conn = get_db()
        conn.execute("UPDATE pr_requests SET status='po_issued' WHERE id=?", (pr_id,))
        conn.commit()
        conn.close()
        fin = {"username": "fin", "role": "financial_director", "id": 2}
        cfo = {"username": "cfo", "role": "cfo", "id": 3}

        # 1. Unauthorised advance is refused.
        ok, msg = svc.add_payment(pr_id, {"amount": 10000}, fin)
        assert not ok and msg == "advance_not_authorised", (ok, msg)

        # 2. The Financial Director may authorise up to 25%.
        ok, msg = svc.authorize_advance(pr_id, 20, "", fin)
        assert ok, "FIN-D refused at 20%%: %s" % msg
        ok, msg = svc.add_payment(pr_id, {"amount": 20000}, fin)
        assert ok, "an authorised 20%% advance was blocked: %s" % msg

        # 3. ...but not beyond it.
        ok, msg = svc.authorize_advance(pr_id, 40, "", fin)
        assert not ok and msg == "not_authorised", \
            "the Financial Director authorised 40%%: %s" % msg
        ok, msg = svc.authorize_advance(pr_id, 40, "", cfo)
        assert ok, "the CFO could not authorise 40%%: %s" % msg

        # 4. A payment beyond what was authorised is refused.
        ok, msg = svc.add_payment(pr_id, {"amount": 30000}, cfo)
        assert not ok and msg == "advance_exceeds_authorised", (ok, msg)

        # 5. Bank guarantee on a large order.
        big, _ = svc.create_pr(
            {"title": "Machine", "department": "Production", "vendor": "Approved Supplier",
             "expenditure_kind": "capex"},
            [{"item": "Loom", "qty": 1, "unit_price": 600000}],
            {"username": "buyer", "id": 1}, submit=False)
        conn = get_db()
        conn.execute("UPDATE pr_requests SET status='po_issued' WHERE id=?", (big,))
        conn.commit()
        conn.close()
        ok, msg = svc.authorize_advance(big, 30, "", cfo)
        assert not ok and msg == "guarantee_required", (ok, msg)
        ok, msg = svc.authorize_advance(big, 30, "BG-2026-77", cfo)
        assert ok, msg

        # 6. No advance to a supplier off the approved list — and an admin
        #    override does not buy one.
        off, _ = svc.create_pr(
            {"title": "Yarn", "department": "Production", "vendor": "Delisted Supplier"},
            [{"item": "Yarn", "qty": 1, "unit_price": 50000}],
            {"username": "buyer", "id": 1}, submit=False)
        conn = get_db()
        conn.execute("UPDATE pr_requests SET status='po_issued' WHERE id=?", (off,))
        conn.commit()
        conn.close()
        ok, msg = svc.authorize_advance(off, 10, "", fin)
        assert not ok and msg == "vendor_not_approved", (ok, msg)
        ok, msg = svc.add_payment(off, {"amount": 5000}, fin, force=True)
        assert not ok and msg == "advance_vendor_not_approved", \
            "an admin override paid an advance to a delisted supplier: %s" % msg

        # 7. Once the supplier has invoiced, it is not an advance and the gate
        #    stops applying.
        conn = get_db()
        conn.execute("INSERT INTO pr_invoices (pr_id, invoice_no, amount, currency, "
                     "status, created_at) VALUES (?,?,?,?,?,?)",
                     (off, "INV-1", 50000, "EGP", "received", "2026-01-01 00:00:00"))
        conn.commit()
        conn.close()
        ok, msg = svc.add_payment(off, {"amount": 5000}, fin, force=True)
        assert ok, "an invoiced payment was blocked by the advance gate: %s" % msg

        print("PASS: advance limits, authority, guarantee and vendor list all enforced")
        return True


if __name__ == "__main__":
    run()
