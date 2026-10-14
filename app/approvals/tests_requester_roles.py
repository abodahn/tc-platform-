"""Every approver may also raise a request — and still never approve it.

Asked for 2026-10-14 ("anyone can be a requester"): the CEO, the CFO and the
Plant Director could only approve. Granting them proc_create is safe ONLY if
their own request then goes somewhere other than back to them, so this file
checks both halves:

  * every approver role now holds proc_create and can open the request form
  * a director's own rung is re-pointed one level up the escalation chain
    (Plant Director / Supply Chain Director -> CEO, CFO -> CEO), and the
    director cannot sign it
  * a database seeded BEFORE those chain rows existed receives them at boot,
    while an owner's edit — including a deliberately blank superior — is
    never overwritten
  * the CEO is the top of the chart: the CEO's own top rung is signed by
    nobody automatically, and the CEO still cannot sign it

    python app/approvals/tests_requester_roles.py
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="reqroles_"), "r.db")
    from app import create_app
    return create_app()


APPROVER_ROLES = ("ceo", "cfo", "plant_director", "supply_chain_director",
                  "managing_director", "board", "financial_director", "finance_user")


def run():
    app = _app()
    from werkzeug.security import generate_password_hash
    from app.db import get_db

    def mkuser(conn, username, role):
        conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                     "is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                     (username, generate_password_hash("x"), username, role))
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE username=?",
                                 (username,)).fetchone())

    with app.app_context():
        from app.security import has_permission
        from app.approvals import constants as C
        from app.approvals import services as svc
        from app.approvals.schema import seed_governance

        # ---- 1. the permission ----------------------------------------------
        print("\n1. every approver role may raise a request")
        for r in APPROVER_ROLES:
            chk("%-22s holds proc_create" % r, has_permission(r, "proc_create"))
        chk("...and none of them gained pricing or buying",
            not any(has_permission(r, "proc_purchasing") for r in APPROVER_ROLES))

        conn = get_db()
        try:
            # ---- 2. the chain reaches databases seeded before it ------------
            print("\n2. an existing database receives the new chain rows, and")
            print("   nothing an owner set is overwritten")
            conn.execute("DELETE FROM proc_escalations WHERE role_key IN "
                         "('plant_director','supply_chain_director')")
            conn.execute("UPDATE proc_escalations SET superior_role='ceo' "
                         "WHERE role_key='purchasing_manager'")       # owner's edit
            conn.execute("UPDATE proc_escalations SET superior_role='' "
                         "WHERE role_key='storekeeper'")              # owner's blank
            conn.commit()
            seed_governance(conn)
            conn.commit()
            chain = svc.escalation_map(conn)
            chk("plant_director -> ceo was added", chain.get("plant_director") == "ceo",
                chain.get("plant_director"))
            chk("supply_chain_director -> ceo was added",
                chain.get("supply_chain_director") == "ceo", chain.get("supply_chain_director"))
            chk("the owner's edit survived (purchasing_manager -> ceo)",
                chain.get("purchasing_manager") == "ceo", chain.get("purchasing_manager"))
            chk("the owner's deliberate blank survived (storekeeper -> nobody)",
                chain.get("storekeeper") == "", repr(chain.get("storekeeper")))
            conn.execute("UPDATE proc_escalations SET superior_role=? WHERE role_key=?",
                         (C.DEFAULT_ESCALATION["purchasing_manager"], "purchasing_manager"))
            conn.execute("UPDATE proc_escalations SET superior_role=? WHERE role_key=?",
                         (C.DEFAULT_ESCALATION["storekeeper"], "storekeeper"))
            conn.commit()

            # an understaffed site, like the real one: one person per role
            seeded = [r["username"] for r in conn.execute(
                "SELECT username FROM users WHERE role IN (%s) AND is_active=1"
                % ",".join("?" * 9),
                ("storekeeper", "warehouse_manager", "purchasing_manager",
                 "factory_manager", "plant_director", "supply_chain_director",
                 "cfo", "ceo", "managing_director")).fetchall()]
            if seeded:
                conn.execute("UPDATE users SET is_active=0 WHERE username IN (%s)"
                             % ",".join("?" * len(seeded)), seeded)
                conn.commit()
            pd = mkuser(conn, "rq_pd", "plant_director")
            scd = mkuser(conn, "rq_scd", "supply_chain_director")
            ceo = mkuser(conn, "rq_ceo", "ceo")
            cfo = mkuser(conn, "rq_cfo", "cfo")
            buyer = mkuser(conn, "rq_buyer", "purchasing_manager")
            mkuser(conn, "rq_store", "storekeeper")
            mkuser(conn, "rq_whm", "warehouse_manager")
            conn.execute("INSERT INTO proc_budgets (department, period, currency, amount) "
                         "VALUES ('Maintenance','2026','EGP',999000000)")
            conn.commit()
        finally:
            conn.close()

        def raise_and_price(user, amount, title):
            pr_id = svc.create_pr(
                {"title": title, "department": "Maintenance", "currency": "EGP"},
                [{"item": "RQ-PART", "qty": 1, "unit_price": 0}],
                user, submit=True, priced=False)[0]
            c = get_db()
            try:
                line = c.execute("SELECT id FROM pr_items WHERE pr_id=?",
                                 (pr_id,)).fetchone()["id"]
            finally:
                c.close()
            ok_, msg = svc.price_pr(pr_id, {line: amount}, {"tax_rate": 0}, buyer)
            c = get_db()
            try:
                steps = [dict(r) for r in c.execute(
                    "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id",
                    (pr_id,)).fetchall()]
            finally:
                c.close()
            return pr_id, ok_, msg, steps

        # ---- 3. a director's own request goes up, not back ------------------
        print("\n3. the Plant Director raises a 50,000 request")
        pr_id, okp, msgp, steps = raise_and_price(pd, 50_000, "plant director's own")
        chk("he can raise it, and it prices", pr_id and okp, msgp)
        own = [s for s in steps if s["stage"] in ("factory_manager", "scd")]
        chk("it carries a director rung", own, [s["stage"] for s in steps])
        for s in own:
            chk("%s rung is re-pointed to the CEO (esc_role=%r)" % (s["stage"], s["esc_role"]),
                (s["esc_role"] or "") == "ceo")
            chk("...the Plant Director cannot sign it", not svc.can_act_step(pd, s))
            chk("...the CEO can", svc.can_act_step(ceo, s))

        # ---- 4. the CFO --------------------------------------------------------
        print("\n4. the CFO raises a 600,000 request")
        pr_id, okp, msgp, steps = raise_and_price(cfo, 600_000, "cfo's own")
        chk("he can raise it, and it prices", pr_id and okp, msgp)
        cfo_rung = [s for s in steps if s["stage"] == "cfo"]
        chk("it carries the CFO rung", cfo_rung, [s["stage"] for s in steps])
        for s in cfo_rung:
            chk("the CFO rung is re-pointed to the CEO (esc_role=%r)" % s["esc_role"],
                (s["esc_role"] or "") == "ceo")
            chk("...the CFO cannot sign it", not svc.can_act_step(cfo, s))
            chk("...the CEO can", svc.can_act_step(ceo, s))

        # ---- 5. the top of the chart ----------------------------------------
        print("\n5. the CEO raises a 3,000,000 request (nobody above the CEO)")
        pr_id, okp, msgp, steps = raise_and_price(ceo, 3_000_000, "ceo's own")
        chk("the CEO can raise it, and it prices", pr_id and okp, msgp)
        top = [s for s in steps if s["stage"] == "ceo"]
        chk("it carries the CEO rung", top, [s["stage"] for s in steps])
        # can_act_step answers "does this role sign this stage" — and the CEO
        # role does sign the CEO stage, so it says yes. The control that stops a
        # self-approval is in act_on_step (DOAM §3.4, absolute), so walk the
        # request up to that rung and try it for real.
        for s in top:
            c = get_db()
            try:
                c.execute("UPDATE pr_steps SET status='approved', approver_user='walked' "
                          "WHERE pr_id=? AND seq<?", (pr_id, s["seq"]))
                c.execute("UPDATE pr_requests SET current_seq=? WHERE id=?",
                          (s["seq"], pr_id))
                c.commit()
                admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' "
                                       "AND is_active=1 LIMIT 1").fetchone())
            finally:
                c.close()
            ok_, msg = svc.act_on_step(pr_id, ceo, "approve")
            chk("the CEO is REFUSED on his own top rung", (not ok_) and msg == "self_approval",
                msg)
            print("        (esc_role=%r: nobody sits above the CEO, so this rung is"
                  " signed by an admin, a Managing Director, or a delegation)" % s["esc_role"])
            # 3,000,000 also needs competing quotes (RFQ gate) — the buyer's job,
            # skipped by the walk above. Supply them so only the CEO rung is tested.
            from app.approvals import constants as _C
            for i in range(max(int(_C.RFQ_QUOTE_MIN), 3)):
                svc.add_quote(pr_id, {"vendor": "RQ Vendor %d" % i, "amount": 3_000_000 + i},
                              buyer)
            ok_, msg = svc.act_on_step(pr_id, admin, "approve")
            chk("...and an admin can sign it, so it is never stranded", ok_, msg)

    # ---- 6. the form itself --------------------------------------------------
    print("\n6. the request form opens for them")
    with app.app_context():
        c = get_db()
        try:
            ids = {r["username"]: r["id"] for r in c.execute(
                "SELECT id, username FROM users WHERE username IN "
                "('rq_pd','rq_ceo','rq_cfo')").fetchall()}
        finally:
            c.close()
    for name, uid in sorted(ids.items()):
        cl = app.test_client()            # outside any app context: fresh g per request
        with cl.session_transaction() as s:
            s["uid"] = uid
            s["ep"] = 0
        r = cl.get("/procurement/new")
        chk("%s opens /procurement/new" % name, r.status_code == 200, r.status_code)

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
