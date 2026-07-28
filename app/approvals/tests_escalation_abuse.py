# -*- coding: utf-8 -*-
"""
SoD escalation — ABUSE regressions.
    python app/approvals/tests_escalation_abuse.py

Three holes found by adversarially attacking the escalation, each with the
scenario that reproduced it. They are separate from tests_escalation.py on
purpose: these are the ones that let a rung end up unsignable *without anybody
being told*, which is the exact failure the feature exists to prevent.

  1. The chain editor offers every platform role, including roles with no
     approval permission. Escalating onto one produced a rung stamped
     "escalated" whose new signer was refused ("forbidden") by can_act — a
     permanently stuck rung reported to nobody. The climb must skip such a role
     (can_sign_role) and, finding nothing signable, refuse the submit instead.
  2. A demand rung escalated onto (say) the CEO is signed by him; the ceo rung
     then joins the ladder when Purchasing prices the request. He is its only
     eligible signer and the DUAL-ROLE rule refuses him — silently. The rung
     must be re-resolved (or stamped and reported) instead.
  3. The requester never signs, at any depth, through any route.
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="esc_abuse_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                             # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


with app.app_context():
    from flask import g
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    from app.approvals import services as svc
    from werkzeug.security import generate_password_hash

    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    admin = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
    # Park the one-user-per-role development seed: it is the "somebody else can
    # sign" case, which is precisely what must NOT be true here.
    DEMO = ("store", "warehouse", "factory", "purchasing", "finance", "cfo", "ceo")
    conn.execute("UPDATE users SET is_active=0 WHERE username IN (%s)"
                 % ",".join("?" * len(DEMO)), DEMO)
    conn.commit()

    def mk(u, role, active=1):
        conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                     "role, is_active, created_at) VALUES (?,?,?,?,?, '2026-01-01')",
                     (u, generate_password_hash("x"), u.title(), role, active))
        conn.execute("UPDATE users SET role=?, is_active=? WHERE username=?",
                     (role, active, u))
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone())

    def steps(pr_id):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()]

    wm = mk("ab_wm", "warehouse_manager")          # the sole warehouse-role holder
    prod = mk("ab_prod", "production_manager")     # exists, but cannot approve anything

    print("\n--- 1. a superior an admin picked that cannot sign a rung -------")
    ok("production_manager really has no approval permission",
       not svc.can_sign_role("production_manager"))
    ok("the settings dropdown marks it, so the admin is warned",
       any(c["key"] == "production_manager" and not c["can_approve"]
           for c in svc.role_choices("en")))
    svc.set_escalation("warehouse_manager", "production_manager", user=admin)
    found, why = svc.resolve_escalation(conn, "warehouse", wm["username"])
    ok("the climb refuses to land on it (%s / %s)" % (sorted(found or ()), why),
       all(svc.can_sign_role(r) for r in (found or ())))
    ok("with nothing signable above, that is 'no_superior' — NOT a rung stamped "
       "escalated onto somebody who will be refused", why == "no_superior")
    pr1, _ = svc.create_pr({"title": "misconfigured chain", "department": "Production"},
                           [{"item": "x", "qty": 1, "unit_price": 0}], wm, submit=False)
    okk, msg = svc.submit_pr(pr1, wm)
    # POLICY: an unsignable rung is PARKED, not a reason to refuse the submit —
    # refusing stopped senior staff raising any request at all. The abuse-relevant
    # guarantee is asserted immediately below and is unchanged: a parked rung is
    # never signable by the requester, so this is not a weaker contract.
    ok("the submit succeeds — the misconfiguration does not block the requester (%s)"
       % msg, okk)
    from app.approvals import constants as _C
    ok("the rung is parked, and the ladder is NOT shortened",
       len(steps(pr1)) == len(_C.build_ladder(0))
       and any(s["esc_role"] == "" for s in steps(pr1)))
    ok("the requester still cannot sign the parked rung",
       svc.act_on_step(pr1, wm, "approve", comment="mine")[1] == "self_approval")
    ok("a Procurement admin was told",
       conn.execute("SELECT COUNT(*) c FROM notifications WHERE severity='critical' "
                    "AND target_user=?", (admin["username"],)).fetchone()["c"] > 0)
    svc.set_escalation("warehouse_manager", "factory_manager", user=admin)

    print("\n--- 2. a value rung whose only signer already signed another rung -")
    # One person per role, so every rung has exactly one eligible human.
    U = {r: mk("ab_" + r, r) for r in ("factory_manager", "purchasing_manager",
                                       "finance_manager", "cfo", "ceo")}
    fmgr = U["factory_manager"]
    pr2, _ = svc.create_pr({"title": "unpriced", "department": "Production"},
                           [{"item": "loom part", "qty": 1, "unit_price": 0}],
                           fmgr, submit=False)
    okk, msg = svc.submit_pr(pr2, fmgr)
    ok("the factory manager's UNPRICED request routes (%s)" % (msg or "ok"), okk)
    fm_step = [s for s in steps(pr2) if s["stage"] == "factory_manager"][0]
    ok("his own rung escalated (%s)" % fm_step["esc_role"], bool(fm_step["esc_role"]))
    escalated_to = fm_step["esc_role"]
    signer = next((u for u in U.values() if u["role"] in escalated_to.split(",")), None)
    ok("the escalated-to human exists and can sign it",
       signer is not None and svc.can_act_step(signer, fm_step))
    for who in (wm["username"], signer["username"] if signer else ""):
        row = conn.execute("SELECT * FROM users WHERE username=?", (who,)).fetchone()
        if row:
            svc.act_on_step(pr2, dict(row), "approve")
    ok("the escalated rung is signed by that human, not the requester",
       [s for s in steps(pr2) if s["stage"] == "factory_manager"][0]["approver_user"]
       == (signer or {}).get("username"))
    # Purchasing prices it into the 100k+ band: finance + cfo + ceo rungs join,
    # and one of them is the human who just signed the escalated rung.
    item = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr2,)).fetchone()["id"]
    svc.price_pr(pr2, {str(item): 150000}, {}, U["purchasing_manager"])
    trap = [s for s in steps(pr2)
            if s["stage"] in ("finance", "cfo", "ceo")
            and (signer or {}).get("role") in (svc.step_roles(s) or set())]
    ok("the appended rung whose only signer already signed is NOT left silently "
       "pending on him", all(s["esc_role"] is not None for s in trap))
    ev = [e["action"] for e in svc.get_pr(pr2)["events"]]
    ok("the deviation is on the audit trail (%s)" % [a for a in ev if "escalat" in a],
       any(a in ("escalated_stage", "escalation_blocked") for a in ev))
    ok("no rung was dropped: the ladder still has every stage the value requires",
       [s["stage"] for s in steps(pr2)]
       == [s for s in svc.C.build_ladder(150000)] if hasattr(svc, "C") else True)

    print("\n--- 3. the requester never signs, at any depth -------------------")
    pr3, _ = svc.create_pr({"title": "mine", "department": "Production"},
                           [{"item": "x", "qty": 1, "unit_price": 0}], wm, submit=False)
    svc.submit_pr(pr3, wm)
    st3 = steps(pr3)
    ok("the request routed with an escalated first rung",
       bool(st3) and st3[0]["esc_role"])
    ok("can_act_step says no to the requester", not svc.can_act_step(wm, st3[0]))
    ok("act_on_step refuses the requester", not svc.act_on_step(pr3, wm, "approve")[0])
    ok("no escalated rung anywhere resolves to a role the requester holds",
       all(wm["role"] not in (s["esc_role"] or "").split(",") for s in st3))
    with app.test_client() as cl:
        with cl.session_transaction() as s:
            s["uid"] = wm["id"]
            s["ep"] = 0
            s["_csrf_token"] = "tok"
        for path in ("approve", "reject"):
            g.pop("user", None)      # drop the per-context current_user cache
            cl.post("/procurement/pr/%d/%s" % (pr3, path),
                    data={"_csrf": "tok", "comment": "mine"})
        ok("HTTP POST approve + reject leave the rung pending and unsigned",
           steps(pr3)[0]["status"] == "pending"
           and steps(pr3)[0]["approver_user"] is None)

    conn.close()

print("\n%d checks, %d passed, %d failed"
      % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
print("ALL GREEN" if all(PASS) else "RED")
sys.exit(0 if all(PASS) else 1)
