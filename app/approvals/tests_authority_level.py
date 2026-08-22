# -*- coding: utf-8 -*-
"""A rung is signed by whoever holds its AUTHORITY LEVEL — not by whoever wears
its role name. Compliance-audit finding, proved on the real flow.

The finding: constants.DOAM_LEVEL says what every rung commits at (warehouse and
purchasing L4, the three directors L2, CFO/MD L1, the Board BOD), and DOAM Table 4
says what each level may commit — but nothing in the authorisation path ever read
it. `grep -rn DOAM_LEVEL app/` returned only constants.py's own routing helpers.
can_act() resolved a signer by ROLE MEMBERSHIP alone, so the two admin-editable
maps that decide role membership could hand out authority nobody granted:

  * Procurement -> Settings (proc_stage_meta, services.set_stage_meta) accepts any
    registered role on any rung. Mapping `storekeeper` onto the CFO rung made a
    storekeeper an L1 signer, and a role invented in Admin -> Roles with
    proc_approve was L1 the moment it was typed into the box;
  * the escalation chain (proc_escalations, services.set_escalation) is checked for
    proc_approve and nothing else, so a chain pointing at a junior role stamped
    that role onto an L1 rung and can_act_step() accepted the signature.

Both are reproduced below and both are refused now. Run:
    python app/approvals/tests_authority_level.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

ok = True


def chk(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="authlvl_"), "auth.db")
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_ENV"] = "development"
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.security import ROLES, effective_roles, refresh_db_roles
        from app.approvals import constants as C, services as svc

        conn = get_db()
        admin = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())

        def mk(username, role):
            conn.execute("INSERT OR IGNORE INTO users (username, password_hash, "
                         "full_name, role, is_active, created_at) "
                         "VALUES (?,?,?,?,1,'2026-01-01 00:00:00')",
                         (username, "x", username.title(), role))
            conn.execute("UPDATE users SET role=?, is_active=1 WHERE username=?",
                         (role, username))
            conn.commit()
            return dict(conn.execute("SELECT * FROM users WHERE username=?",
                                     (username,)).fetchone())

        # The development seed ships one demo user per role. Park them: section 4
        # needs rungs whose only holder is a person this file controls.
        DEMO = ("store", "warehouse", "factory", "purchasing", "finance", "cfo", "ceo")
        conn.execute("UPDATE users SET is_active=0 WHERE username IN (%s)"
                     % ",".join("?" * len(DEMO)), DEMO)
        conn.commit()

        tech = mk("al_tech", "normal_user")            # raises, signs nothing
        store = mk("al_store", "storekeeper")
        store2 = mk("al_store2", "storekeeper")        # signs nothing: the intruder
        buyer = mk("al_buyer", "purchasing_manager")
        pd = mk("al_pd", "factory_manager")
        scd = mk("al_scd", "supply_chain_director")
        fin = mk("al_fin", "financial_director")
        cfo = mk("al_cfo", "cfo")
        ceo = mk("al_ceo", "ceo")

        # ---- 0. the two DOAM tables agree with each other -----------------
        # ROLE_LEVEL is declared, not generated from STAGE_ROLES, so this is a
        # real assertion and not a tautology: it fails the moment somebody maps a
        # role onto a rung it may not commit, or re-tiers a role that signs one.
        print("\n--- 0. DOAM §3.2 levels vs the ladder -------------------------")
        for stage, roles in sorted(C.STAGE_ROLES.items()):
            bad = [r for r in sorted(roles) if not C.holds_authority(r, stage)]
            chk("rung %-16s (%-3s) is mapped only to roles that hold it"
                % (stage, C.DOAM_LEVEL.get(stage)), not bad, bad)
        chk("every declared level is on the DOAM's own scale",
            not [v for v in C.ROLE_LEVEL.values() if v not in C.LEVEL_ORDER],
            sorted({v for v in C.ROLE_LEVEL.values() if v not in C.LEVEL_ORDER}))
        chk("every levelled role is a real platform role",
            not [r for r in C.ROLE_LEVEL if r not in ROLES],
            [r for r in C.ROLE_LEVEL if r not in ROLES])
        # A role that can approve but has no level fails CLOSED — correct, but it
        # would be a rung nobody can sign, so the shipped grants must not have one.
        approvers = sorted(r for r, p in C.PROC_ROLE_PERMS.items() if "proc_approve" in p)
        chk("every shipped approving role carries a DOAM level",
            not [r for r in approvers if C.role_level(r) is None],
            [r for r in approvers if C.role_level(r) is None])

        # ---- 1. the predicate itself --------------------------------------
        print("\n--- 1. holds_authority() ---------------------------------------")
        for role, stage, want in (("cfo", "cfo", True),
                                  ("ceo", "warehouse", True),      # senior signs junior
                                  ("board", "bod", True),
                                  ("storekeeper", "warehouse", True),
                                  ("storekeeper", "cfo", False),   # L4 cannot commit L1
                                  ("factory_manager", "bod", False),
                                  ("purchasing_manager", "finance", False),
                                  ("al_invented_role", "finance", False)):
            chk("%-20s on %-16s -> %s" % (role, stage, want),
                C.holds_authority(role, stage) is want)

        # ---- 2. THE HOLE: an admin maps a storekeeper onto the CFO rung ----
        print("\n--- 2. the stage-role override cannot grant L1 -----------------")
        good, msg = svc.set_stage_meta("cfo", roles=["cfo", "storekeeper"], user=admin)
        chk("Procurement -> Settings accepts the override (it is a real screen)",
            good, msg)
        chk("...and the override really landed, so this section is not vacuous",
            "storekeeper" in svc.stage_roles_map().get("cfo", set()),
            sorted(svc.stage_roles_map().get("cfo", ())))
        chk("a storekeeper is REFUSED on the CFO rung despite the mapping",
            not svc.can_act(store2, "cfo"))
        chk("the CFO himself is unaffected", svc.can_act(cfo, "cfo"))
        chk("the rung does not even notify the storekeepers",
            "al_store2" not in svc.eligible_approvers(conn, "cfo")
            and "al_cfo" in svc.eligible_approvers(conn, "cfo"),
            sorted(svc.eligible_approvers(conn, "cfo")))

        # ...end to end, on the real ladder. 600,000 EGP OPEX = DOAM §4.1 tier 4:
        # warehouse, purchasing, PD, SCD, Financial Director, CFO.
        pr_id, pr_no = svc.create_pr(
            {"title": "Authority level walk", "department": "Production",
             "currency": "EGP"},
            [{"item": "Rotary press bearing housing", "qty": 1, "unit_price": 0}],
            tech, priced=False)
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
        good, msg = svc.price_pr(pr_id, {li: 600000}, {"tax_rate": 0}, buyer)
        chk("Purchasing prices it at 600,000 (%s)" % pr_no, good, msg)
        for v in ("Vendor A", "Vendor B", "Vendor C"):     # §4.3 asks for three
            svc.add_quote(pr_id, {"vendor": v, "amount": 600000}, buyer)
        ladder = [r["stage"] for r in conn.execute(
            "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
        chk("the ladder reaches the CFO rung", ladder[-1] == "cfo", ladder)

        for who, label in ((store, "warehouse"), (buyer, "purchasing"), (pd, "plant"),
                           (scd, "supply chain"), (fin, "finance")):
            good, msg = svc.act_on_step(pr_id, who, "approve")
            chk("%-12s signs their rung" % label, good, msg)

        bad, why = svc.act_on_step(pr_id, store2, "approve")
        chk("the storekeeper CANNOT sign the L1 rung the override gave him (%s)" % why,
            not bad and why == "forbidden", why)
        parked = dict(conn.execute("SELECT status, approver_user FROM pr_steps "
                                   "WHERE pr_id=? AND stage='cfo'", (pr_id,)).fetchone())
        chk("the refused rung is untouched — still pending, still unsigned",
            parked["status"] == "pending" and not parked["approver_user"], parked)
        good, msg = svc.act_on_step(pr_id, cfo, "approve")
        chk("the CFO signs it and the request completes", good, msg)
        row = dict(conn.execute("SELECT status FROM pr_requests WHERE id=?",
                                (pr_id,)).fetchone())
        chk("...so the ladder is not deadlocked, only correctly gated",
            row["status"] == "approved", row)
        svc.set_stage_meta("cfo", reset_role=True, user=admin)

        # ---- 3. unknown level fails CLOSED, and SAYS SO -------------------
        print("\n--- 3. a role with no DOAM level, and the findings page --------")
        conn.execute("INSERT OR IGNORE INTO custom_roles (role_key, label, perms_json, "
                     "created_at) VALUES (?,?,?,?)",
                     ("al_junior_buyer", "Junior Buyer",
                      json.dumps(["proc_view", "proc_approve"]), "2026-01-01"))
        conn.commit()
        refresh_db_roles()
        jb = mk("al_jb", "al_junior_buyer")
        chk("the invented role really exists on the platform",
            "al_junior_buyer" in set(effective_roles()))
        chk("it holds proc_approve, so the permission half alone would let it sign",
            svc.can_sign_role("al_junior_buyer"))
        good, msg = svc.set_stage_meta("bod", roles=["al_junior_buyer"], user=admin)
        chk("an admin can still map it onto the Board rung (nothing is blocked "
            "at configuration time)", good, msg)
        chk("but it cannot sign there: no level recorded -> fail closed",
            not svc.can_act(jb, "bod") and not svc.can_sign_role("al_junior_buyer", "bod"))
        health = {h["stage"]: h for h in svc.ladder_signer_health()}
        chk("the health check names the rung's level", health["bod"]["level"] == "BOD")
        chk("...and names the role that cannot reach it",
            health["bod"]["underlevel"] == ["al_junior_buyer"], health["bod"])
        chk("...and reports the rung as unsignable rather than blocking in silence",
            health["bod"]["ok"] is False and health["bod"]["holders"] == 0)
        from app.routes.governance import findings
        chk("the governance Findings page surfaces it",
            "bod" in {x["stage"] for x in findings()["unsignable"]})
        for key in ("gov.find.level", "gov.find.underlevel"):
            tr = svc.I18N.get(key) or ()
            chk("findings wording %-22s is real EN/AR/TR" % key,
                len(tr) == 3 and all(t.strip() for t in tr) and len(set(tr)) == 3)
        # A platform admin is still able to clear the rung — the override path.
        chk("a platform admin can still sign the parked rung", svc.can_act(admin, "bod"))
        svc.set_stage_meta("bod", reset_role=True, user=admin)

        # ---- 4. the climb walks PAST an under-level superior ---------------
        print("\n--- 4. escalation is not deadlocked by the new check -----------")
        chk("the CFO rung's only eligible person is the CFO himself",
            svc.role_holders(conn, {"cfo"}) == {"al_cfo"},
            sorted(svc.role_holders(conn, {"cfo"})))
        svc.set_escalation("cfo", "storekeeper", user=admin)    # an admin's mistake
        found, why = svc.resolve_escalation(conn, "cfo", "al_cfo")
        chk("the climb refuses to park an L1 rung on a storekeeper (%s)" % why,
            "storekeeper" not in (found or ()), sorted(found or ()))
        chk("...it keeps climbing to somebody who can actually commit it: %s (%s)"
            % (sorted(found or ()), why),
            why == "escalated" and found == {"ceo"})
        chk("whoever it landed on holds the rung's level",
            all(C.holds_authority(r, "cfo") for r in (found or ())))
        svc.set_escalation("cfo", "ceo", user=admin)

        # A delegation is the documented way to cover an absence, and it conveys
        # the DELEGATED role's authority — dated, revocable and audited, unlike
        # the ambient role membership this change closes. It must keep working.
        svc.add_delegation("al_cfo", "al_store2", None, None, "leave cover", admin)
        chk("a CFO delegation still lets the delegate sign the CFO rung",
            svc.can_act(store2, "cfo"))
        svc.revoke_delegation(conn.execute(
            "SELECT id FROM proc_delegations WHERE to_user='al_store2'").fetchone()["id"])
        chk("...and once revoked he is refused again", not svc.can_act(store2, "cfo"))

        # ---- 5. "is this rung already covered?" asks the SAME question -----
        # The near-miss this check exists for. Enforcing the level in can_act()
        # alone is not enough: resolve_escalation() decides whether to climb by
        # asking who holds the rung, and while that question ignored the level an
        # under-level holder made the rung look staffed. The climb stayed put and
        # can_act then refused everyone — a silent permanent deadlock, which is
        # the exact failure the SoD escalation exists to prevent.
        #
        # Reproduced on the real flow: an admin adds the buyer to the CFO rung so
        # he can help chase approvals, and the CFO raises a 600,000 request
        # himself. Measured before services.stage_signers(): esc_role NULL, buyer
        # 'forbidden', CFO 'self_approval', MD 'forbidden', status 'pending'
        # forever.
        print("\n--- 5. an under-level holder must not look like cover ----------")
        svc.set_stage_meta("cfo", roles=["cfo", "purchasing_manager"], user=admin)
        chk("the buyer really is on the CFO rung (the admin's mistake landed)",
            "purchasing_manager" in svc.stage_roles_map().get("cfo", set()),
            sorted(svc.stage_roles_map().get("cfo", ())))
        chk("...and he is under-level for it, so he can never sign it",
            not C.holds_authority("purchasing_manager", "cfo"))
        d_id, d_no = svc.create_pr(
            {"title": "CFO raises his own", "department": "Production",
             "currency": "EGP"},
            [{"item": "Boiler feed pump impeller", "qty": 1, "unit_price": 0}],
            cfo, priced=False)
        d_li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                            (d_id,)).fetchone()["id"]
        good, msg = svc.price_pr(d_id, {d_li: 600000}, {"tax_rate": 0}, buyer)
        chk("Purchasing prices it at 600,000 (%s)" % d_no, good, msg)
        for v in ("Vendor A", "Vendor B", "Vendor C"):
            svc.add_quote(d_id, {"vendor": v, "amount": 600000}, buyer)
        cfo_step = dict(conn.execute("SELECT esc_role, esc_from FROM pr_steps "
                                     "WHERE pr_id=? AND stage='cfo'", (d_id,)).fetchone())
        chk("the CFO rung ESCALATED instead of resting on the under-level buyer",
            cfo_step["esc_role"] == "ceo", cfo_step)
        for who, label in ((store, "warehouse"), (buyer, "purchasing"), (pd, "plant"),
                           (scd, "supply chain"), (fin, "finance")):
            good, msg = svc.act_on_step(d_id, who, "approve")
            chk("%-12s signs their rung" % label, good, msg)
        bad, why = svc.act_on_step(d_id, buyer, "approve")
        chk("the under-level buyer still cannot sign the CFO rung (%s)" % why,
            not bad, why)
        bad, why = svc.act_on_step(d_id, cfo, "approve")
        chk("nor the CFO, who raised it (%s)" % why, not bad, why)
        good, msg = svc.act_on_step(d_id, ceo, "approve")
        chk("the MD the rung climbed to signs it", good, msg)
        row = dict(conn.execute("SELECT status FROM pr_requests WHERE id=?",
                                (d_id,)).fetchone())
        chk("...so the request COMPLETES — no deadlock, which is the whole point",
            row["status"] == "approved", row)
        svc.set_stage_meta("cfo", reset_role=True, user=admin)

        # ---- 6. the shipped configuration lost nobody ----------------------
        print("\n--- 6. no rung lost its signers -------------------------------")
        health = svc.ladder_signer_health()
        chk("no rung on the default configuration has an under-level role",
            not [h for h in health if h["underlevel"]],
            [(h["stage"], h["underlevel"]) for h in health if h["underlevel"]])
        for h in health:
            roles = h["roles"]
            chk("rung %-16s still notifies every holder of its own roles" % h["stage"],
                set(svc.eligible_approvers(conn, h["stage"]))
                == set(svc.role_holders(conn, roles)))
        conn.close()

        print("\n" + ("PASS — a rung is signed by the authority level it commits at"
                      if ok else "FAIL"))
        return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
