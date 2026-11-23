# -*- coding: utf-8 -*-
"""proc_admin signs only the rungs its own role holds; super_admin signs any.
    python -m app.approvals.tests_no_admin_sign

Regression for a production incident: a purchasing user who also held the
proc_admin permission signed the Warehouse Manager rung of a purchase request,
because can_act() let proc_admin sign ANY rung. Now:
  * a purchasing user with proc_admin is refused that rung;
  * a super_admin may sign any rung in place of its holder (audited as
    'signed_in_place'), even two rungs of one request, but never their own;
  * the real Warehouse Manager signs it;
  * an active delegate of the Warehouse Manager signs it;
  * an admin who DOES hold the Warehouse Manager role signs it.
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="no_admin_sign_"))
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)

import config                                             # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


with app.app_context():
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    from app.approvals import services as svc
    from werkzeug.security import generate_password_hash

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    def mk(u, role, extra=None):
        conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                     "role, is_active, created_at, extra_perms) "
                     "VALUES (?,?,?,?,1,'2026-01-01',?)",
                     (u, generate_password_hash("x"), u.title(), role, extra))
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone())

    requester = mk("nas_req", "normal_user")
    store = mk("nas_store", "storekeeper")
    whm = mk("nas_whm", "warehouse_manager")
    buyer_admin = mk("nas_buyer", "purchasing_manager", '["proc_admin"]')
    super_admin = mk("nas_root", "super_admin")
    deleg = mk("nas_deleg", "storekeeper")

    def at_wh_manager_rung(title):
        """A request whose live rung is the Warehouse Manager's."""
        pr_id, _ = svc.create_pr({"title": title, "department": "Production",
                                  "currency": "EGP"},
                                 [{"item": "Spindle " + title, "qty": 1, "unit_price": 0}],
                                 requester, submit=True, priced=False)
        okk, msg = svc.act_on_step(pr_id, store, "approve")
        assert okk, "the storekeeper could not sign the store rung: %s" % msg
        st = conn.execute("SELECT s.* FROM pr_steps s JOIN pr_requests p ON p.id=s.pr_id "
                          "WHERE s.pr_id=? AND s.seq=p.current_seq AND s.status='pending'",
                          (pr_id,)).fetchone()
        assert st["stage"] == "warehouse_manager", st["stage"]
        return pr_id, dict(st)

    def signed_in_place(pr_id):
        return conn.execute("SELECT COUNT(*) FROM pr_events WHERE pr_id=? "
                            "AND action='signed_in_place'", (pr_id,)).fetchone()[0]

    print("\n--- proc_admin without the rung role is refused ------------------")
    pr1, step1 = at_wh_manager_rung("A")
    ok("the purchasing user with proc_admin really holds proc_admin",
       svc.user_has_permission(buyer_admin, "proc_admin"))
    ok("can_act_step says no to a purchasing user with proc_admin",
       not svc.can_act_step(buyer_admin, step1))
    okk, msg = svc.act_on_step(pr1, buyer_admin, "approve")
    ok("a purchasing user with proc_admin is refused the Warehouse Manager rung (%s)" % msg,
       not okk and msg == "forbidden")
    okk, msg = svc.act_on_step(pr1, buyer_admin, "reject", comment="not mine")
    ok("...and cannot reject it either (%s)" % msg, not okk and msg == "forbidden")
    ok("the rung is still pending and unsigned",
       conn.execute("SELECT status, approver_user FROM pr_steps WHERE id=?",
                    (step1["id"],)).fetchone()["status"] == "pending")

    print("\n--- the real Warehouse Manager signs it --------------------------")
    okk, msg = svc.act_on_step(pr1, whm, "approve")
    ok("the warehouse manager signs (%s)" % msg, okk)
    ok("the signature is his",
       conn.execute("SELECT approver_user FROM pr_steps WHERE id=?",
                    (step1["id"],)).fetchone()["approver_user"] == "nas_whm")
    ok("...and no in-place audit row for a role holder", signed_in_place(pr1) == 0)

    print("\n--- super_admin signs any rung in place of its holder, audited ---")
    pr4, step4 = at_wh_manager_rung("D")
    ok("can_act_step says yes to a super_admin", svc.can_act_step(super_admin, step4))
    okk, msg = svc.act_on_step(pr4, super_admin, "approve")
    ok("a super_admin signs the Warehouse Manager rung (%s)" % msg, okk)
    ok("the signature is the super_admin's",
       conn.execute("SELECT approver_user FROM pr_steps WHERE id=?",
                    (step4["id"],)).fetchone()["approver_user"] == "nas_root")
    ok("a 'signed_in_place' audit row records it", signed_in_place(pr4) == 1)
    stamp = conn.execute("SELECT in_place_of FROM pr_steps WHERE id=?",
                         (step4["id"],)).fetchone()["in_place_of"] or ""
    ok("the step names who it stood in for (%s)" % stamp, "nas_whm" in stamp or "Warehouse" in stamp)
    ok("the audit row is a warning naming that holder", "IN PLACE OF" in (conn.execute(
        "SELECT detail FROM pr_events WHERE pr_id=? AND action='signed_in_place'",
        (pr4,)).fetchone()["detail"] or ""))
    ok("the bypassed Warehouse Manager is notified", conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE target_user='nas_whm' "
        "AND title='Signed in your place'").fetchone()[0] >= 1)

    pr5, _ = svc.create_pr({"title": "E", "department": "Production", "currency": "EGP"},
                           [{"item": "Spindle E", "qty": 1, "unit_price": 0}],
                           requester, submit=True, priced=False)
    okk, msg = svc.act_on_step(pr5, super_admin, "approve")
    ok("a super_admin signs the store rung (%s)" % msg, okk)
    okk, msg = svc.act_on_step(pr5, super_admin, "approve")
    ok("...and the Warehouse Manager rung of the same request (%s)" % msg, okk)
    ok("both in-place signatures are audited", signed_in_place(pr5) == 2)

    pr6, _ = svc.create_pr({"title": "F", "department": "Production", "currency": "EGP"},
                           [{"item": "Spindle F", "qty": 1, "unit_price": 0}],
                           super_admin, submit=True, priced=False)
    okk, msg = svc.act_on_step(pr6, super_admin, "approve")
    ok("a super_admin who raised the request is refused (%s)" % msg,
       not okk and msg == "self_approval")
    ok("...and no in-place audit row is written", signed_in_place(pr6) == 0)

    print("\n--- a delegate of the Warehouse Manager signs it -----------------")
    pr2, step2 = at_wh_manager_rung("B")
    ok("before the delegation the delegate is refused",
       svc.act_on_step(pr2, deleg, "approve")[1] == "forbidden")
    svc.add_delegation("nas_whm", "nas_deleg", None, None, "leave cover", super_admin)
    okk, msg = svc.act_on_step(pr2, deleg, "approve")
    ok("with an active delegation the delegate signs (%s)" % msg, okk)
    ok("...proc_admin is still refused on a delegated ladder, super_admin is not",
       not svc.can_act(buyer_admin, "warehouse_manager")
       and svc.can_act(super_admin, "warehouse_manager"))

    print("\n--- an admin who DOES hold the role signs it ---------------------")
    whm_admin = mk("nas_whm_admin", "warehouse_manager", '["proc_admin"]')
    pr3, _ = at_wh_manager_rung("C")
    okk, msg = svc.act_on_step(pr3, whm_admin, "approve")
    ok("a warehouse manager who also holds proc_admin signs (%s)" % msg, okk)

print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
