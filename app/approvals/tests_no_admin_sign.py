# -*- coding: utf-8 -*-
"""Admins sign only the rungs their own role holds.
    python -m app.approvals.tests_no_admin_sign

Regression for a production incident: a purchasing user who also held the
proc_admin permission signed the Warehouse Manager rung of a purchase request,
because can_act() let super_admin / proc_admin sign ANY rung. Now:
  * a purchasing user with proc_admin, and a super_admin, are refused that rung;
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

    print("\n--- admins who do not hold the rung are refused ------------------")
    pr1, step1 = at_wh_manager_rung("A")
    ok("the purchasing user with proc_admin really holds proc_admin",
       svc.user_has_permission(buyer_admin, "proc_admin"))
    for who, label in ((buyer_admin, "a purchasing user with proc_admin"),
                       (super_admin, "a super_admin")):
        ok("can_act_step says no to %s" % label, not svc.can_act_step(who, step1))
        okk, msg = svc.act_on_step(pr1, who, "approve")
        ok("%s is refused the Warehouse Manager rung (%s)" % (label, msg),
           not okk and msg == "forbidden")
        okk, msg = svc.act_on_step(pr1, who, "reject", comment="not mine")
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

    print("\n--- a delegate of the Warehouse Manager signs it -----------------")
    pr2, step2 = at_wh_manager_rung("B")
    ok("before the delegation the delegate is refused",
       svc.act_on_step(pr2, deleg, "approve")[1] == "forbidden")
    svc.add_delegation("nas_whm", "nas_deleg", None, None, "leave cover", super_admin)
    okk, msg = svc.act_on_step(pr2, deleg, "approve")
    ok("with an active delegation the delegate signs (%s)" % msg, okk)
    ok("...and the admins are still refused on a delegated ladder",
       not svc.can_act(buyer_admin, "warehouse_manager")
       and not svc.can_act(super_admin, "warehouse_manager"))

    print("\n--- an admin who DOES hold the role signs it ---------------------")
    whm_admin = mk("nas_whm_admin", "warehouse_manager", '["proc_admin"]')
    pr3, _ = at_wh_manager_rung("C")
    okk, msg = svc.act_on_step(pr3, whm_admin, "approve")
    ok("a warehouse manager who also holds proc_admin signs (%s)" % msg, okk)

print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
