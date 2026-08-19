"""DOAM §5 / Table 12 — the golden thread.

"Requisitions, purchase orders, goods received notes, and invoices are
pre-numbered and carry the controlling sales order or cost center reference."

Direct materials MUST name their sales order; spares, MRO, facility and capital
spend carry an asset or cost centre instead. The check that matters is that a
fabric requisition cannot enter the approval ladder without one.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "thread.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        # Table 12, both halves.
        assert C.cost_object_required(["Fabric, cotton twill"]) == "sales_order"
        assert C.cost_object_required(["TRIM & ACCESSORIES"]) == "sales_order"
        assert C.cost_object_required(["Subcontract wash — order 8841"]) == "sales_order"
        assert C.cost_object_required(["Bearing 6204", "Spares"]) is None
        assert C.cost_object_required(["Toner cartridge", "IT"]) is None

        tech = {"username": "tech", "id": 1}

        # 1. Fabric with no sales order cannot be submitted.
        bad, _ = svc.create_pr({"title": "Fabric for the new style",
                                "department": "Production"},
                               [{"item": "Cotton twill fabric", "qty": 500,
                                 "unit_price": 60}], tech, submit=False)
        ok, msg = svc.submit_pr(bad, tech)
        assert not ok and msg == "cost_object_required", (ok, msg)

        # 2. With a REAL, OPEN sales order on it, the same request goes through.
        #    The reference is validated against ord_orders now (audit 3.4-b2a):
        #    a made-up number like "SO-8841" is refused, so the fixture uses a
        #    live order. tests_so_gate.py drives the validation itself.
        conn = get_db()
        live = conn.execute("SELECT order_no FROM ord_orders WHERE status NOT IN "
                            "('closed','cancelled') ORDER BY id LIMIT 1").fetchone()
        conn.close()
        assert live, "no open sales order on file — the fixture cannot run"
        live_so = live["order_no"]
        good, _ = svc.create_pr({"title": "Fabric for the new style",
                                 "department": "Production", "so_no": live_so},
                                [{"item": "Cotton twill fabric", "qty": 500,
                                  "unit_price": 60}], tech, submit=False)
        ok, msg = svc.submit_pr(good, tech)
        assert ok, "a fabric PR carrying %s was still refused: %s" % (live_so, msg)

        # 3. MRO does not need one — the control must not block the factory's
        #    day-to-day spares purchasing.
        mro, _ = svc.create_pr({"title": "Bearings for line 3",
                                "department": "Maintenance"},
                               [{"item": "Bearing 6204", "qty": 4,
                                 "unit_price": 800}], tech, submit=False)
        ok, msg = svc.submit_pr(mro, tech)
        assert ok, "an MRO request was blocked for want of a sales order: %s" % msg

        # 4. The reference is on the record, so every downstream document has it.
        conn = get_db()
        row = conn.execute("SELECT so_no FROM pr_requests WHERE id=?", (good,)).fetchone()
        conn.close()
        assert row["so_no"] == live_so, row["so_no"]

        # 5. ...and it actually reaches the printed PR / PO / GRN.
        from app.approvals import pdf
        pairs = pdf._cost_object_pairs({"so_no": "SO-8841", "cost_center": None,
                                        "asset_code": "MC-12"})
        assert ("Sales order", "SO-8841") in pairs and ("Asset", "MC-12") in pairs, pairs
        assert pdf._cost_object_pairs({"so_no": None, "cost_center": None,
                                       "asset_code": None}) == [], \
            "an empty cost object must print nothing at all"

        print("PASS: direct materials carry their sales order from PR to GRN")
        return True


if __name__ == "__main__":
    run()
