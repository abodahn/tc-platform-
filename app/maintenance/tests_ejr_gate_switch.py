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
        # A second, still-DRAFT report, to prove the requester cannot satisfy the
        # gate with their own unsigned justification.
        draft_id, _ = E.create(conn, {"machine_id": mid, "request_type": "breakdown",
                                      "description": "d", "root_cause": "r",
                                      "criticality": "routine", "downtime_risk": "x",
                                      "stock_on_hand": 1, "stock_checked_with": "store",
                                      "alternatives": "none"}, {"username": "tech"})
        conn.commit()
        conn.close()

        c = spares_pr("Spares with an approved EJR")
        # The ONLY path that can satisfy the gate — the same service the
        # POST /procurement/pr/<id>/engineering-justification route calls. Raw
        # SQL here would leave the unblock path untested and the gate could
        # become unsatisfiable in production without this test noticing.
        chk("a DRAFT report cannot be attached",
            svc.attach_ejr(c, draft_id, buyer) == (False, "ejr_not_approved"))
        okatt, m = svc.attach_ejr(c, ejr_id, buyer)
        chk("attach_ejr cites the approved report (%s)" % m, okatt)
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

        # ---- 7. a BROKEN maintenance module must not freeze all purchasing ---
        # The gate fails closed on a real evaluation error, but "maintenance is
        # not installed" is not an evaluation error: there are then no
        # maintenance requisitions to gate, and refusing Production's stationery
        # over an ImportError is a bigger failure than the one it guards against.
        def plain_pr(title, dept="Production"):
            pr_id, _ = svc.create_pr(
                {"title": title, "department": dept},
                [{"item": "A4 paper", "qty": 10, "unit_price": 0}],
                {"username": "clerk", "id": 8}, priced=False)
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pr_id,)).fetchone()["id"]
            conn.close()
            svc.price_pr(pr_id, {li: 30.0}, {"tax_rate": 0}, buyer)
            svc.set_single_source(pr_id, "Framework stationery supplier", buyer)
            return pr_id

        import importlib
        import sys
        MOD = "app.maintenance.eng_justification"

        e = plain_pr("Stationery while the maintenance module is unimportable")
        sys.modules[MOD] = None          # any import of it now raises ImportError
        try:
            oke, msge = purchasing_signoff(e)
        finally:
            sys.modules.pop(MOD, None)
        chk("an unimportable maintenance module does not block Production (%s)" % msge,
            oke)

        # ...but a genuine evaluation failure still fails CLOSED.
        EJ = importlib.import_module(MOD)
        orig_check = EJ.ejr_gate_check

        def _boom(*a, **k):
            raise ValueError("simulated query failure")
        EJ.ejr_gate_check = _boom
        try:
            f = spares_pr("Spares while the gate itself is broken")
            okf, msgf = purchasing_signoff(f)
        finally:
            EJ.ejr_gate_check = orig_check
        chk("a failing gate evaluation still refuses (%s)" % msgf,
            (not okf) and msgf == "ejr_check_failed:ValueError")

        # A renamed/removed registry key must degrade the gate to OFF, not raise
        # at import time (which is what made the ImportError above reachable).
        saved = wf.SETTINGS.pop("ejr_gate")
        try:
            reloaded = importlib.reload(EJ)
            chk("a missing registry key degrades to off instead of raising",
                reloaded.EJR_GATE_DEFAULT is False)
        except KeyError:
            chk("a missing registry key degrades to off instead of raising", False)
        finally:
            wf.SETTINGS["ejr_gate"] = saved
            importlib.reload(EJ)

        # ---- 8. the emergency deadline is PARSED, not string-compared --------
        base = "2026-08-18 10:00:00"
        cap = "2026-08-19 10:00:00"
        chk("no date supplied -> the 24h cap", E.emergency_deadline(base) == cap)
        chk("a tighter deadline is honoured",
            E.emergency_deadline(base, "2026-08-18 14:00:00") == "2026-08-18 14:00:00")
        chk("the browser's datetime-local format is accepted",
            E.emergency_deadline(base, "2026-08-18T14:00") == "2026-08-18 14:00:00")
        chk("a longer deadline cannot be granted",
            E.emergency_deadline(base, "2099-01-01 00:00:00") == cap)
        chk("junk falls back to the cap, it does not become the deadline",
            E.emergency_deadline(base, "1") == cap)
        # unpadded used to sort ABOVE the cap and be discarded; it is now read
        # as the date it obviously is.
        chk("an unpadded date is parsed, not mis-sorted",
            E.emergency_deadline(base, "2026-8-19 09:00") == "2026-08-19 09:00:00")

        # ---- 9. and something actually READS that deadline -------------------
        conn = get_db()
        E.create(conn, {
            "machine_id": mid, "request_type": "breakdown", "description": "Overdue",
            "root_cause": "r", "criticality": "safety", "downtime_risk": "x",
            "stock_on_hand": 0, "stock_checked_with": "store", "alternatives": "none",
            "is_emergency": True, "emergency_due_at": "2000-01-01 00:00:00"},
            {"username": "tech"})
        conn.commit()
        from app.maintenance import services as msvc
        n1 = msvc.sync_sla_breaches(conn)
        overdue = conn.execute(
            "SELECT COUNT(*) c FROM mnt_notifications WHERE entity_type='ejr'"
        ).fetchone()["c"]
        n2 = msvc.sync_sla_breaches(conn)
        conn.close()
        chk("the SLA sweep flags an overdue emergency report (%s)" % n1, overdue == 1)
        chk("and does not re-notify on the next sweep", n2 == 0)

        # ---- 10. and it can be switched back off the same way ----------------
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
