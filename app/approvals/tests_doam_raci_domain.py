# -*- coding: utf-8 -*-
"""DOAM Table 4 (L2 domain split) and Table 5 (P/R/A/E) — proved on the REAL flow.

    python app/approvals/tests_doam_raci_domain.py

Every request here is created exactly as the UI creates one: no prices at all
(the requester price lockout), then priced at the pricing gate. That is the only
place these two controls can be proved, because the ladder a request submits
with is the DEMAND ladder — neither L2 director is on it until Purchasing has
entered a value, so a check that hands the service layer a priced request would
be testing a state a real user cannot produce.

Both directions are driven for both controls: the case each must change AND the
case each must leave alone.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "raci.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _stages(conn, pr_id):
    return [r["stage"] for r in conn.execute(
        "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]


def _acts(conn, pr_id):
    return [(r["stage"], r["action_type"]) for r in conn.execute(
        "SELECT stage, action_type FROM pr_steps WHERE pr_id=? ORDER BY seq",
        (pr_id,)).fetchall()]


def _events(conn, pr_id):
    return [(r["action"], r["detail"]) for r in conn.execute(
        "SELECT action, detail FROM pr_events WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]


def _mkuser(conn, username, role):
    from werkzeug.security import generate_password_hash
    conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                 "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                 (username, generate_password_hash("x"), username.title(), role))
    conn.commit()
    return dict(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        tech = {"username": "tech", "id": 9}

        def unpriced(title, dept, item, qty=1, spare_id=None):
            line = {"item": item, "qty": qty, "unit_price": 0}
            if spare_id:
                line["spare_id"] = spare_id
            return svc.create_pr({"title": title, "department": dept}, [line],
                                 tech, priced=False)

        def price(pr_id, unit, tax=0):
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
            conn.close()
            ok, msg = svc.price_pr(pr_id, {li: unit}, {"tax_rate": tax}, buyer)
            assert ok, "pricing failed: %s" % msg

        # =================================================================
        # (B) Table 4 L2 — PD and SC-D are split by DOMAIN, not by amount
        # =================================================================
        print("--- (B) DOAM Table 4: which director owns the commitment ---")

        # 1. A maintenance-origin request above 10,000 -> Plant Director only.
        #    Tagged with link_source(), which is the call the maintenance ->
        #    procurement bridge itself makes when a ticket raises a requisition.
        a, _ = unpriced("Gearbox overhaul parts", "Production", "Gearbox seal kit")
        assert svc.link_source(a, "maintenance", "ticket:41", tech)
        price(a, 18000)
        conn = get_db(); la = _stages(conn, a); conn.close()
        assert "factory_manager" in la, "PD must sign a maintenance commitment: %s" % la
        assert "scd" not in la, (
            "the Supply Chain Director signed a maintenance commitment — Table 4 "
            "gives PD production and maintenance: %s" % la)

        # 2. An inventory replenishment above 10,000 -> Supply Chain Director only.
        b, _ = unpriced("Stores replenishment", "Warehouse", "Pallet wrap")
        price(b, 14000)
        conn = get_db(); lb = _stages(conn, b); conn.close()
        assert "scd" in lb, "SC-D must sign an inventory replenishment: %s" % lb
        assert "factory_manager" not in lb, (
            "the Plant Director signed a stores replenishment — Table 4 gives "
            "SC-D operational and inventory replenishment: %s" % lb)

        # 3. THE CASE THE CONTROL MUST NOT TOUCH: no domain signal at all.
        #    Never drop a signature on a guess — an unreadable request keeps both.
        c, _ = unpriced("Office laptops", "IT", "Laptop")
        price(c, 40000)
        conn = get_db(); lc = _stages(conn, c); conn.close()
        assert "factory_manager" in lc and "scd" in lc, (
            "an IT purchase names neither domain, so BOTH directors must stay: %s" % lc)

        # 4. ...and the other ambiguous shape: signals pointing BOTH ways at once.
        #    The warehouse restocking a maintenance spare is genuinely both an
        #    inventory replenishment and a maintenance commitment.
        conn = get_db()
        conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty, max_level, "
                     "avg_cost, is_active) VALUES (?,?,?,?,?,1)",
                     ("SP-DOM", "Domain test bearing", 5, 5000, 40.0))
        sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code=?",
                           ("SP-DOM",)).fetchone()["id"]
        conn.commit(); conn.close()
        d, _ = unpriced("Spare restock", "Warehouse", "Domain test bearing",
                        qty=10, spare_id=sid)
        price(d, 1500)                      # 15,000 — clears the L2 tier
        conn = get_db(); ld = _stages(conn, d); conn.close()
        assert "factory_manager" in ld and "scd" in ld, (
            "a maintenance spare bought by the warehouse points both ways, so "
            "BOTH directors must stay: %s" % ld)

        # 5. Below the tier nothing changes either way (no L2 rung to split).
        e, _ = unpriced("Small belt", "General Maintenance", "V-belt A31")
        price(e, 900)
        conn = get_db(); le = _stages(conn, e); conn.close()
        assert "factory_manager" not in le and "scd" not in le, (
            "900 EGP is tier 1 — no director signs at all: %s" % le)

        # 6. CAPEX is untouched: §4.2 names PD *and* SC-D by role, not by amount,
        #    so the domain split must not reach it.
        f, _ = svc.create_pr({"title": "New cutter", "department": "General Maintenance",
                              "expenditure_kind": "capex"},
                             [{"item": "Auto cutter", "qty": 1, "unit_price": 0}],
                             tech, priced=False)
        price(f, 300000)
        conn = get_db(); lf = _stages(conn, f); conn.close()
        assert "factory_manager" in lf and "scd" in lf, (
            "the CAPEX ladder is a joint approval — both directors stay: %s" % lf)

        # 7. A department that configured the dropped director KEEPS them: the
        #    department said so deliberately, and the split is an efficiency fix.
        conn = get_db()
        conn.execute("INSERT INTO proc_resp_matrix (department, stage, threshold, seq, "
                     "active) VALUES (?,?,?,?,1)", ("Warehouse", "factory_manager", 0, 1))
        conn.commit(); conn.close()
        g, _ = unpriced("Stores replenishment 2", "Warehouse", "Strapping band")
        price(g, 14000)
        conn = get_db(); lg = _stages(conn, g); conn.close()
        assert "factory_manager" in lg and "scd" in lg, (
            "an explicit department matrix row must survive the domain split: %s" % lg)

        # the reason is on the record, not only in the shape of the ladder
        conn = get_db(); eva = _events(conn, a); conn.close()
        dom = [dt for ac, dt in eva if ac == "l2_domain"]
        assert dom and "Plant Director" in dom[0] and "maintenance_origin" in dom[0], dom

        # =================================================================
        # (A) Table 5 — Review, Approve and Endorse are distinct signatures
        # =================================================================
        print("--- (A) DOAM Table 5: what each signature actually is ---")

        # The ladder that request (a) ended up with: two rungs verifying, the
        # top rung committing. Nobody was added, nobody was reordered.
        conn = get_db(); aa = _acts(conn, a); conn.close()
        assert [s for s, _ in aa] == ["warehouse", "purchasing", "factory_manager"], aa
        assert aa == [("warehouse", "review"), ("purchasing", "review"),
                      ("factory_manager", "approve")], (
            "the top rung of the value ladder is the Approve and every rung below "
            "it a Review: %s" % aa)

        # An unpriced request submits with Purchasing on top, so Purchasing IS
        # the Approve — and pricing moving a director in above it must move the
        # Approve with it. (The pricing gate guarantees that happens before the
        # purchasing rung can be signed, so no rung is signed under a stale letter.)
        h, _ = unpriced("Letter shift", "IT", "Monitor")
        conn = get_db(); before = _acts(conn, h); conn.close()
        assert before == [("warehouse", "review"), ("purchasing", "approve")], before
        price(h, 30000)
        conn = get_db(); after = _acts(conn, h); conn.close()
        assert ("purchasing", "review") in after, (
            "pricing put two directors above Purchasing; Purchasing is no longer "
            "the final authority: %s" % after)
        assert sum(1 for _, act in after if act == "approve") == 1, after

        # E — a rung a CONTROL added ABOVE the value tier endorses; it does not
        # replace the Approve the value ladder already carries.
        i, _ = unpriced("OEM drive", "IT", "Servo drive")
        price(i, 60000)
        okk, msg = svc.set_single_source(i, "Proprietary OEM part",
                                         {"username": "purch", "id": 3})
        assert okk, msg
        conn = get_db(); ai = _acts(conn, i); conn.close()
        assert ai[-1][1] == "endorse", (
            "the §4.3 rung is senior support for a decision taken at the value "
            "tier — Table 5 E, not a second A: %s" % ai)
        assert sum(1 for _, act in ai if act == "approve") == 1, ai
        assert sum(1 for _, act in ai if act == "endorse") == 1, ai

        # ...and the ROW ORDER and SIGNERS are byte-identical to what they were
        # before this change: recording what a signature is may not move who
        # signs or when.
        conn = get_db()
        shape = [(r["seq"], r["stage"]) for r in conn.execute(
            "SELECT seq, stage FROM pr_steps WHERE pr_id=? ORDER BY seq, id",
            (i,)).fetchall()]
        conn.close()
        assert shape == list(enumerate(
            ["warehouse", "purchasing", "factory_manager", "scd", "cfo"],
            start=1)), shape

        # ---- the audit trail: a Review is visibly a different event ----------
        wh = _mkuser_ctx(get_db, "wh_rev", "warehouse_manager")
        pm = _mkuser_ctx(get_db, "pm_app", "purchasing_manager")
        j, _ = unpriced("Trail", "IT", "Keyboard")
        price(j, 5000)
        conn = get_db(); aj = _acts(conn, j); conn.close()
        assert aj == [("warehouse", "review"), ("purchasing", "approve")], aj
        svc.add_quote(j, {"vendor": "Quote Vendor", "amount": 5000}, buyer)  # §4.3 band 1
        okk, msg = svc.act_on_step(j, wh, "approve")
        assert okk, msg
        okk, msg = svc.act_on_step(j, pm, "approve")
        assert okk, msg
        conn = get_db(); evj = _events(conn, j); conn.close()
        acts = [ac for ac, _ in evj]
        assert "reviewed" in acts, (
            "the warehouse signature was a Table 5 Review and the trail must say "
            "so as its own event: %s" % acts)
        assert "approved" in acts, acts
        rev = next(dt for ac, dt in evj if ac == "reviewed")
        app_ = next(dt for ac, dt in evj if ac == "approved")
        assert "(R)" in rev and "verified accuracy" in rev, rev
        assert "(A)" in app_ and "final authority" in app_, app_
        assert rev != app_

        # the publicly verifiable signature record carries it too
        conn = get_db()
        sev = [(r["stage"], r["action"]) for r in conn.execute(
            "SELECT stage, action FROM pr_sign_events WHERE pr_id=? ORDER BY id",
            (j,)).fetchall()]
        conn.close()
        assert sev == [("warehouse", "review"), ("purchasing", "approve")], sev

        # ---- the case the trail must NOT relabel: a rejection stays a rejection
        k, _ = unpriced("Bounce", "IT", "Mouse")
        price(k, 5000)
        okk, msg = svc.act_on_step(k, wh, "reject", "not needed")
        assert okk, msg
        conn = get_db(); evk = _events(conn, k); conn.close()
        assert "rejected" in [ac for ac, _ in evk], evk
        assert "reviewed" not in [ac for ac, _ in evk], evk

        # ---- an existing row keeps its meaning (the migration default) -------
        conn = get_db()
        conn.execute("UPDATE pr_steps SET action_type=NULL WHERE pr_id=?", (k,))
        conn.commit()
        legacy = dict(conn.execute("SELECT * FROM pr_steps WHERE pr_id=? LIMIT 1",
                                   (k,)).fetchone())
        conn.close()
        assert svc.step_action_of(legacy) == "approve", (
            "a row written before the column existed must read as an Approve, "
            "which is what it meant: %r" % legacy.get("action_type"))

        print("maintenance origin   ->", " -> ".join(la))
        print("stores replenishment ->", " -> ".join(lb))
        print("ambiguous (IT)       ->", " -> ".join(lc))
        print("Table 5 on (a)       ->", ", ".join("%s=%s" % x for x in aa))
        print("single source        ->", ", ".join("%s=%s" % x for x in ai))
        print("PASS: Table 4 L2 splits by domain and never on a guess; Table 5 "
              "R / A / E are recorded, printed and auditable.")
        return True


def _mkuser_ctx(get_db, username, role):
    conn = get_db()
    try:
        return _mkuser(conn, username, role)
    finally:
        conn.close()


if __name__ == "__main__":
    run()
