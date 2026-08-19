"""The DOAM defects the compliance audit found, each proved through the REAL flow.

Every check here goes through the entry points the web UI uses — create_pr with
the price lockout (total = 0), then price_pr — because that is exactly where the
previous round of "done" was wrong: the controls worked only when a test injected
prices through the service layer, a state a real user cannot produce.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "harden.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _stages(conn, pr_id):
    return [r["stage"] for r in conn.execute(
        "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]


def _unpriced(svc, title, dept, item, qty=1):
    """A request exactly as the UI makes one: no prices at all."""
    return svc.create_pr({"title": title, "department": dept},
                         [{"item": item, "qty": qty, "unit_price": 0}],
                         {"username": "tech", "id": 9}, priced=False)


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def price(pr_id, unit, tax=0):
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
            conn.close()
            ok, msg = svc.price_pr(pr_id, {li: unit}, {"tax_rate": tax}, buyer)
            assert ok, "pricing failed: %s" % msg

        # ---- R7: routing must use the TAX-INCLUSIVE committed value ----------
        a, _ = _unpriced(svc, "Belts", "Maintenance", "V-belt A42")
        price(a, 9900, tax=14)              # net 9,900 -> committed 11,286
        conn = get_db(); la = _stages(conn, a); conn.close()
        assert "factory_manager" in la, (
            "9,900 + 14%% VAT commits 11,286 and must clear the 10,000 tier: %s" % la)

        # a net figure that stays under the tier even with tax must NOT escalate
        b, _ = _unpriced(svc, "Filters", "Quality", "Air filter")
        price(b, 8000, tax=14)              # committed 9,120
        conn = get_db(); lb = _stages(conn, b); conn.close()
        assert "factory_manager" not in lb, "9,120 committed is still tier 1: %s" % lb

        # ---- §3.4: the anti-split aggregate must fire on the REAL flow -------
        c1, _ = _unpriced(svc, "Bearings A", "Sewing", "Bearing 6204")
        price(c1, 9000)
        c2, _ = _unpriced(svc, "Bearings B", "Sewing", "Bearing 6204")
        price(c2, 9000)
        conn = get_db()
        l1, l2 = _stages(conn, c1), _stages(conn, c2)
        agg = conn.execute("SELECT agg_total FROM pr_requests WHERE id=?", (c2,)).fetchone()["agg_total"]
        conn.close()
        assert "factory_manager" not in l1, "the first 9k has nothing to aggregate: %s" % l1
        assert agg and abs(agg - 18000) < 1, "aggregate should be 18,000, got %r" % agg
        assert "factory_manager" in l2, (
            "two related 9k requests priced through the UI flow must route on "
            "18,000 — this is the case that was silently dead: %s" % l2)

        # ---- R3: re-pricing must NOT strip a control rung --------------------
        d, _ = _unpriced(svc, "OEM drive", "Production", "Servo drive")
        price(d, 60000)
        ok, msg = svc.set_single_source(d, "Proprietary OEM part", {"username": "purch", "id": 3})
        assert ok, msg
        conn = get_db(); before = _stages(conn, d); conn.close()
        price(d, 60000)                     # same value, saved again
        conn = get_db(); after = _stages(conn, d); conn.close()
        assert after == before, (
            "re-pricing stripped the single-source escalation: %s -> %s" % (before, after))

        # ---- R4: an admin may never sign their own request -------------------
        e, _ = _unpriced(svc, "Self test", "IT", "Laptop")
        price(e, 5000)
        admin = {"username": "tech", "role": "super_admin", "id": 9}   # tech RAISED it
        ok, msg = svc.act_on_step(e, admin, "approve")
        assert not ok and msg == "self_approval", (
            "a super_admin signed a request they raised: %s / %s" % (ok, msg))

        # ---- R6: the spare-part ceiling branch must actually execute ---------
        conn = get_db()
        conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty, max_level, "
                     "avg_cost, is_active) VALUES (?,?,?,?,?,1)",
                     ("SP-CEIL", "Ceiling test part", 90, 100, 50.0))
        sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code=?",
                           ("SP-CEIL",)).fetchone()["id"]
        conn.commit(); conn.close()
        f, _ = svc.create_pr({"title": "Overstock", "department": "Maintenance"},
                             [{"item": "Ceiling test part", "qty": 50, "unit_price": 0,
                               "spare_id": sid}], {"username": "tech", "id": 9}, priced=False)
        price(f, 50.0)                      # at target price, but 90 + 50 > 100
        conn = get_db(); dev = svc.deviation_findings(conn, f); lf = _stages(conn, f); conn.close()
        assert dev["lines"][0]["over_ceiling"] is True, (
            "the ceiling branch did not run — it queried a table that does not "
            "exist and the error was swallowed: %s" % dev["lines"][0])
        assert dev["lines"][0]["grade"] == "over_ceiling", dev["lines"][0]
        assert "scd" in lf, "over-ceiling must pull in PD + SCD: %s" % lf

        # ---- R8: the guarantee threshold is an EGP figure --------------------
        conn = get_db()
        conn.execute("INSERT INTO proc_vendors (name, is_active) VALUES (?,1)", ("FX Vendor",))
        conn.commit(); conn.close()
        g, _ = svc.create_pr({"title": "Imported loom", "department": "Production",
                              "vendor": "FX Vendor", "currency": "USD"},
                             [{"item": "Loom", "qty": 1, "unit_price": 20000}],
                             buyer, submit=False)
        conn = get_db()
        conn.execute("UPDATE pr_requests SET status='po_issued', fx_rate=50 WHERE id=?", (g,))
        conn.commit(); conn.close()
        cfo = {"username": "cfo", "role": "cfo", "id": 4}
        ok, msg = svc.authorize_advance(g, 30, "", cfo)
        assert not ok and msg == "guarantee_required", (
            "USD 20,000 is ~1,000,000 EGP and needs a bank guarantee; the raw "
            "figure 20,000 slipped under the 500,000 rule: %s / %s" % (ok, msg))

        print("R7 9,900+14%% VAT ->", " -> ".join(la))
        print("split 9k+9k via the real flow ->", " -> ".join(l2), "(agg %.0f)" % agg)
        print("over-ceiling ->", " -> ".join(lf))
        print("PASS: every defect the audit named is closed on the real flow")
        return True


if __name__ == "__main__":
    run()
