"""R1: the DOAM §6 engineering-justification gate must be switchable ON.

Proved through the REAL flow, following tests_doam_hardening.py: create_pr with
the requester price lockout (priced=False, total 0), then price_pr, then the
Purchasing sign-off — which is where ejr_gate_check actually runs. The gate is
flipped through workflow.set_setting, NOT raw SQL, because "the row can be
hand-typed into SQLite" was exactly the previous defect.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "ejrgate.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    ok_all = True

    def chk(label, cond):
        nonlocal ok_all
        ok_all = ok_all and bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label)

    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        from app.maintenance import workflow as wf, eng_justification as E

        admin = {"username": "admin", "role": "super_admin", "id": 1}
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 2}

        def spares_pr(title):
            """A spares request exactly as the UI makes one: no prices at all."""
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance"},
                [{"item": "Bearing 6204", "qty": 2, "unit_price": 0}],
                {"username": "tech", "id": 9}, priced=False)
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pr_id,)).fetchone()["id"]
            conn.close()
            okp, msg = svc.price_pr(pr_id, {li: 120.0}, {"tax_rate": 0}, buyer)
            assert okp, "pricing failed: %s" % msg
            # The RFQ band gate sits in front of the engineering gate at the same
            # stage; record the price basis so the refusal we read back is the
            # engineering one and not "needs_quotes".
            oks, msg = svc.set_single_source(pr_id, "Sole stockist of this bearing", buyer)
            assert oks, "single-source failed: %s" % msg
            return pr_id

        def purchasing_signoff(pr_id):
            """Walk the ladder to the Purchasing stage — Procurement's acceptance
            point, and where ejr_gate_check runs — and return ITS result. Each rung
            is signed by a different username so the dual-role SoD rule never
            masks the answer we are actually testing."""
            for n in range(1, 12):
                conn = get_db()
                stages = [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? AND seq="
                    "(SELECT current_seq FROM pr_requests WHERE id=?) AND status='pending'",
                    (pr_id, pr_id)).fetchall()]
                conn.close()
                if not stages:
                    return False, "no_active_step"
                signer = {"username": "sig%d" % n, "role": "super_admin", "id": 100 + n}
                r = svc.act_on_step(pr_id, signer, "approve")
                if "purchasing" in stages:
                    return r
                if not r[0]:
                    return r
            return False, "ladder_did_not_reach_purchasing"

        # ---- 1. the setting is REGISTERED, so the normal path can write it ----
        chk("'ejr_gate' is a declared boolean setting",
            wf.SETTINGS.get("ejr_gate") == (False, "bool", None, None))
        chk("it ships OFF", E.EJR_GATE_DEFAULT is False)
        conn = get_db()
        chk("a fresh database enforces nothing", E.gate_enabled(conn) is False)
        conn.close()

        # ---- 2. gate OFF -> a spares requisition passes ----------------------
        a = spares_pr("Spares, gate off")
        oka, msga = purchasing_signoff(a)
        chk("gate off: Purchasing signs off a spares request (%s)" % msga, oka)

        # ---- 3. switch it ON through the settings service, not raw SQL -------
        conn = get_db()
        okset, m = wf.set_setting(conn, "ejr_gate", "1", admin)
        conn.close()
        chk("workflow.set_setting accepts 'ejr_gate' (%s)" % (m or "ok"), okset)
        conn = get_db()
        chk("gate_enabled now reads True", E.gate_enabled(conn) is True)
        conn.close()

        # ---- 4. gate ON -> the same request is refused -----------------------
        b = spares_pr("Spares, gate on")
        okb, msgb = purchasing_signoff(b)
        chk("gate on: the same request is refused for a missing EJR (%s)" % msgb,
            (not okb) and msgb == "ejr_missing")

        # ---- 5. an APPROVED justification lets it through ---------------------
        conn = get_db()
        conn.execute("INSERT INTO mnt_machines (code, name, is_active) VALUES (?,?,1)",
                     ("MC-EJR", "Gate test machine"))
        mid = conn.execute("SELECT id FROM mnt_machines WHERE code=?",
                           ("MC-EJR",)).fetchone()["id"]
        ejr_id, _no = E.create(conn, {
            "machine_id": mid, "request_type": "breakdown",
            "description": "Bearing failed on the main drive",
            "root_cause": "Seal worn, contamination ingress",
            "criticality": "production_critical",
            "downtime_risk": "Line stops within the shift",
            "stock_on_hand": 0, "stock_checked_with": "storekeeper",
            "alternatives": "No equivalent bearing in store",
        }, {"username": "tech"})
        conn.commit()
        chk("submit() accepts a complete report",
            E.submit(conn, ejr_id) == (True, "submitted"))
        conn.commit()
        chk("the Engineering Head signs it",
            E.decide(conn, ejr_id, True, {"username": "eng_head"}) == (True, "approved"))
        conn.commit()
        conn.close()

        c = spares_pr("Spares with an approved EJR")
        conn = get_db()
        conn.execute("UPDATE pr_requests SET ejr_id=? WHERE id=?", (ejr_id, c))
        conn.commit()
        conn.close()
        okc, msgc = purchasing_signoff(c)
        chk("gate on + approved EJR attached: Purchasing signs off (%s)" % msgc, okc)

        # ---- 6. the DOAM's own exemption still holds while the gate is ON ----
        d, _ = svc.create_pr(
            {"title": "Auto reorder", "department": "Maintenance"},
            [{"item": "Bearing 6204", "qty": 5, "unit_price": 0}],
            {"username": "tech", "id": 9}, priced=False)
        # tagged exactly as procure_bridge tags an automatic reorder
        svc.link_source(d, "maintenance", "spare:7")
        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (d,)).fetchone()["id"]
        conn.close()
        svc.price_pr(d, {li: 120.0}, {"tax_rate": 0}, buyer)
        svc.set_single_source(d, "Reorder against the stocked part", buyer)
        okd, msgd = purchasing_signoff(d)
        chk("min/max replenishment stays exempt with the gate on (%s)" % msgd, okd)

        # ---- 7. and it can be switched back off the same way -----------------
        conn = get_db()
        wf.reset_setting(conn, "ejr_gate", admin)
        chk("reset_setting returns it to the shipped default",
            E.gate_enabled(conn) is False)
        conn.close()

    print("PASS: the gate ships off, and the admin screen's setting path turns it on"
          if ok_all else "FAIL")
    return ok_all


if __name__ == "__main__":
    raise SystemExit(0 if run() else 1)
