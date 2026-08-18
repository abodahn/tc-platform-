# -*- coding: utf-8 -*-
"""Every DOAM ladder rung must map to a role that exists and that somebody can
be given — proved on the REAL flow, not on the service layer with prices injected.

The audit finding: 'supply_chain_director' was referenced by STAGE_ROLES for the
'scd' rung but existed in no code registry — it was written into custom_roles by
a boot-time seed that never committed and gave up on its first exception. Where
that one write did not land, stage_roles_map() (which filters roles through
effective_roles()) dropped the role, and a tier-2 request sat on a rung only a
platform admin could clear.

Run:  python app/approvals/tests_doam_signers.py
"""
import os
import sys
import tempfile

sys.path.insert(0, r"D:\TC platform\tc-platform-beta")

ok = True


def chk(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "signers.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _unpriced(svc, title, dept, item):
    """A request exactly as the UI makes one: the requester cannot price."""
    return svc.create_pr({"title": title, "department": dept},
                         [{"item": item, "qty": 1, "unit_price": 0}],
                         {"username": "tech", "id": 9}, priced=False)


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.security import (ROLES, BUILTIN_ROLE_KEYS, all_role_choices,
                                  effective_roles, refresh_db_roles)
        from app.approvals import constants as C, services as svc

        # ---- 1. enumerate every DOAM stage --------------------------------
        conn = get_db()
        rmap = svc.stage_roles_map(conn)
        registry, code = set(effective_roles()), set(ROLES)
        print("DOAM ladder — role registration and live signers\n")
        print("  %-16s %-46s %-8s %-7s %s" %
              ("STAGE", "ROLES", "IN-CODE", "IN-REG", "ACTIVE SIGNERS"))
        unsignable = []
        for stage in C.DOAM_LADDER:
            roles = sorted(rmap.get(stage, ()))
            holders = sorted(svc.role_holders(conn, roles))
            if not holders:
                unsignable.append(stage)
            print("  %-16s %-46s %-8s %-7s %s" % (
                stage, ",".join(roles) or "(none)",
                all(r in code for r in roles), all(r in registry for r in roles),
                ", ".join(holders) or "*** NOBODY — DEADLOCK ***"))
        conn.close()
        print()

        # ---- 2. registration: code, not one database ----------------------
        # effective_roles() would also pass on a database whose seed happened to
        # land. ROLES is the code registry every environment shares.
        for stage in C.DOAM_LADDER:
            missing = [r for r in rmap.get(stage, ()) if r not in code]
            chk("stage %-16s roles are registered in code" % stage, not missing,
                "not in security.ROLES: %s" % missing)

        # ---- 3. assignable through the admin UI ---------------------------
        choices = dict(all_role_choices())
        for role in sorted({r for s in C.DOAM_LADDER for r in rmap.get(s, ())}):
            chk("role %-22s is in the Admin -> Users role list" % role, role in choices)
            chk("role %-22s cannot be deleted out of existence" % role,
                role in BUILTIN_ROLE_KEYS)

        # ---- 4. the health check reports the deadlock ---------------------
        health = svc.ladder_signer_health()
        chk("health check covers every DOAM stage",
            {h["stage"] for h in health} >= set(C.DOAM_LADDER))
        chk("health check reports no unregistered role anywhere",
            not [h for h in health if h["unregistered"]],
            [(h["stage"], h["unregistered"]) for h in health if h["unregistered"]])
        chk("health check flags exactly the unstaffed rungs",
            sorted(h["stage"] for h in health
                   if not h["ok"] and h["stage"] in C.DOAM_LADDER) == sorted(unsignable),
            unsignable)
        from app.routes.governance import findings
        chk("the governance Findings page surfaces them",
            sorted(x["stage"] for x in findings()["unsignable"]
                   if x["stage"] in C.DOAM_LADDER) == sorted(unsignable))

        # ---- 5. the REAL flow: a 60,000 EGP tier-2 request ----------------
        buyer = {"username": "purchasing", "role": "purchasing_manager", "id": 1}
        pr_id, _ = _unpriced(svc, "Tier-2 spend", "Production", "Spindle motor")
        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
        conn.close()
        good, msg = svc.price_pr(pr_id, {li: 60000}, {"tax_rate": 0}, buyer)
        chk("pricing the request at the pricing gate succeeds", good, msg)

        conn = get_db()
        ladder = [r["stage"] for r in conn.execute(
            "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
        conn.close()
        chk("60,000 EGP routes through the scd rung (DOAM §4.1 tier 2)",
            "scd" in ladder, ladder)

        # ---- 6. a user given the role can actually sign that rung ---------
        # A FIXTURE in this throwaway database — the fix seeds roles, never
        # memberships, so no real person is given authority by this repository.
        conn = get_db()
        conn.execute("INSERT INTO users (username, password_hash, full_name, role, "
                     "is_active, created_at) VALUES (?,?,?,?,1,?)",
                     ("scd_fixture", "x", "SCD Fixture", "supply_chain_director",
                      "2026-01-01 00:00:00"))
        conn.commit()
        conn.close()
        refresh_db_roles()
        scd = {"username": "scd_fixture", "role": "supply_chain_director", "id": 4242}
        chk("can_act() accepts the role on the scd stage", svc.can_act(scd, "scd"))
        chk("the rung is no longer flagged by the health check",
            next(h for h in svc.ladder_signer_health() if h["stage"] == "scd")["ok"])

        # DOAM §4.3 asks for three competing quotes at this value — record them
        # so the RFQ gate is satisfied and the ladder can actually be walked.
        conn = get_db()
        for v in ("Vendor A", "Vendor B", "Vendor C"):
            conn.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency) "
                         "VALUES (?,?,?,'EGP')", (pr_id, v, 60000))
        conn.commit()
        conn.close()

        for who, role in (("store", "storekeeper"), ("purchasing", "purchasing_manager"),
                          ("factory", "factory_manager")):
            good, msg = svc.act_on_step(pr_id, {"username": who, "role": role, "id": 0}, "approve")
            chk("%-11s signs their rung" % who, good, msg)
        good, msg = svc.act_on_step(pr_id, scd, "approve")
        chk("the Supply Chain Director signs the final tier-2 rung", good, msg)

        conn = get_db()
        row = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        signed = conn.execute("SELECT approver_user FROM pr_steps WHERE pr_id=? AND stage='scd'",
                              (pr_id,)).fetchone()
        conn.close()
        chk("the scd rung carries a real signature, not an admin override",
            signed and signed["approver_user"] == "scd_fixture",
            dict(signed) if signed else None)
        chk("the request completed its ladder", row["status"] != "pending", row["status"])

        print("\n" + ("PASS — every DOAM rung maps to a registered, assignable role"
                      if ok else "FAIL"))
        if unsignable:
            print("STILL UNSTAFFED (registered, but no user holds the role): %s"
                  % ", ".join(unsignable))
            print("  -> reported on /governance/findings; fix by assigning the role"
                  "  in Admin -> Users. Seeding a membership is not this code's job.")
        return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
