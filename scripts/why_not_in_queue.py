"""
Why doesn't a user see a purchase request waiting for them?  READ-ONLY.

    python scripts/why_not_in_queue.py <signer_username> <requester_username>

Takes the requester's 5 latest requests and, for each one, says whether it is
waiting for the signer, and if it isn't, why. Changes nothing.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import create_app  # noqa: E402
from app.db import get_db  # noqa: E402


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    signer_name, requester = sys.argv[1].strip().lower(), sys.argv[2].strip().lower()
    app = create_app()
    with app.app_context():
        from app.security import has_permission
        from app.approvals import services as svc
        from app.approvals import constants as C
        conn = get_db()
        try:
            u = conn.execute("SELECT * FROM users WHERE LOWER(username)=?", (signer_name,)).fetchone()
            if not u:
                sys.exit("No user %r." % signer_name)
            user = dict(u)
            role = user.get("role")
            print("signer    %s  role=%s  active=%s" % (user["username"], role, user.get("is_active")))
            print("          proc_view=%s  proc_approve=%s" % (
                has_permission(role, "proc_view"), has_permission(role, "proc_approve")))
            if not has_permission(role, "proc_approve"):
                print("  >> role %s has no proc_approve: it can sign NOTHING."
                      " Run: python scripts/sync_role_perms.py" % role)
            roles_map = svc.stage_roles_map(conn)
            print("          stages this role may sign: %s" % (", ".join(
                s for s, rs in roles_map.items()
                if role in rs and C.holds_authority(role, s)) or "none"))

            prs = conn.execute("SELECT * FROM pr_requests WHERE LOWER(requester)=? AND is_active=1 "
                               "ORDER BY id DESC LIMIT 5", (requester,)).fetchall()
            if not prs:
                print("\nNo requests by %r (check the spelling of the username)." % requester)
                return
            for pr in prs:
                pr = dict(pr)
                ref = pr.get("pr_no") or "#%s" % pr["id"]
                print("\nrequest %s  status=%s  department=%s" % (ref, pr["status"], pr.get("department")))
                if pr["status"] == "draft":
                    print("  >> still a DRAFT: the requester never pressed Submit for approval.")
                    continue
                if pr["status"] != "pending":
                    print("  >> not waiting for anyone (status %s)." % pr["status"])
                    continue
                steps = conn.execute("SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq",
                                     (pr["id"],)).fetchall()
                print("  ladder: %s" % " > ".join(
                    "%s%s" % (s["stage"], "*" if s["seq"] == pr["current_seq"] else "") for s in steps))
                cur = [s for s in steps if s["seq"] == pr["current_seq"] and s["status"] == "pending"]
                for s in cur:
                    who = s["esc_role"] if s["esc_role"] else ",".join(sorted(roles_map.get(s["stage"], ())))
                    ok = svc.can_act_step(user, s, _roles=roles_map)
                    print("  now at %-18s signed by roles: %s" % (s["stage"], who))
                    print("  >> %s" % ("WAITING FOR %s - it is in their queue." % user["username"] if ok
                                       else "%s cannot sign this step (role %s is not one of those)."
                                       % (user["username"], role)))
        finally:
            conn.close()


if __name__ == "__main__":
    main()
