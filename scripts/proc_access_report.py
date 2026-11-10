"""
Procurement access report for EVERY user and request.  READ-ONLY.

    python scripts/proc_access_report.py

1. Every active user: role, and whether it can see / create / sign requests.
   Flags a user whose name looks like a warehouse or requester account but
   whose role cannot do that job.
2. Every requester: how many requests are drafts, waiting (and at which step),
   or finished. Flags a waiting step that no active user can sign.
Changes nothing.
"""
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import create_app  # noqa: E402
from app.db import get_db  # noqa: E402

WAREHOUSE_HINTS = ("warehouse", "whm", "store", "wh.")
REQUEST_HINTS = ("request", "req.")


def main():
    app = create_app()
    with app.app_context():
        from app.security import has_permission
        from app.approvals import services as svc
        from app.approvals import constants as C
        conn = get_db()
        try:
            users = [dict(r) for r in conn.execute(
                "SELECT username, role FROM users WHERE is_active=1 ORDER BY username").fetchall()]
            roles_map = svc.stage_roles_map(conn)
            prs = [dict(r) for r in conn.execute(
                "SELECT id, requester, status, current_seq FROM pr_requests WHERE is_active=1").fetchall()]
            cur_stage = {}
            for s in conn.execute("SELECT pr_id, seq, stage, esc_role FROM pr_steps "
                                  "WHERE status='pending'").fetchall():
                cur_stage[(s["pr_id"], s["seq"])] = dict(s)
        finally:
            conn.close()

        def signs(role):
            return [s for s, rs in roles_map.items() if role in rs and C.holds_authority(role, s)]

        print("USERS (%d active)" % len(users))
        print("  %-28s %-20s view create sign  stages" % ("username", "role"))
        problems, hidden = [], 0
        for u in users:
            name, role = u["username"], u["role"]
            v, c, a = (has_permission(role, p) for p in ("proc_view", "proc_create", "proc_approve"))
            st = signs(role) if a else []
            low = name.lower()
            flag = None
            if any(h in low for h in WAREHOUSE_HINTS) and "warehouse" not in st:
                flag = "%s looks like a warehouse user but role %s cannot sign the warehouse step"
            elif any(h in low for h in REQUEST_HINTS) and not c:
                flag = "%s looks like a requester but role %s cannot create requests"
            if flag:
                problems.append(flag % (name, role))
            if not (v or c or a or flag):
                hidden += 1                 # no procurement access and nothing odd: skip
                continue
            print("  %-28s %-20s %-4s %-6s %-5s %s" % (
                name, role, "yes" if v else "-", "yes" if c else "-", "yes" if a else "-",
                ", ".join(st) or "-"))

        if hidden:
            print("  (+ %d users with no procurement access not listed)" % hidden)
        print("\nREQUESTS BY REQUESTER")
        by_req = defaultdict(Counter)
        for pr in prs:
            if pr["status"] == "pending":
                step = cur_stage.get((pr["id"], pr["current_seq"]))
                key = "waiting at %s" % (step["stage"] if step else "?")
                if step:
                    who = ({x.strip() for x in step["esc_role"].split(",") if x.strip()}
                           if step["esc_role"] else roles_map.get(step["stage"], set()))
                    if not any(u["role"] in who or u["role"] == "super_admin" for u in users):
                        problems.append("request #%s waits at %s but no active user has role %s"
                                        % (pr["id"], step["stage"], "/".join(sorted(who))))
            else:
                key = pr["status"]
            by_req[pr["requester"]][key] += 1
        if not by_req:
            print("  (no requests yet)")
        for req in sorted(by_req):
            print("  %-28s %s" % (req, ", ".join("%s %d" % kv for kv in sorted(by_req[req].items()))))

        print("\nPROBLEMS")
        for p in problems:
            print("  >> " + p)
        if not problems:
            print("  none found")
        print("\nAny user with view=yes sees EVERY request in Procurement -> All requests.")


if __name__ == "__main__":
    main()
