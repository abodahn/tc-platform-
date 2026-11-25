"""
Change who raised a purchase request (e.g. it was keyed in on someone's behalf).

    python scripts/set_pr_requester.py <PR-number> <new_username>            # preview only
    python scripts/set_pr_requester.py <PR-number> <new_username> --apply    # write it

Run it in the Render Shell. Without --apply it only prints what would change.
It refuses when the new requester already signed a step of this request (they
would then have approved their own request). The change is recorded in the
request's audit trail as 'requester_changed'.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import create_app  # noqa: E402
from app.db import get_db  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv
    if len(args) != 2:
        sys.exit(__doc__)
    pr_no, new_name = args[0].strip(), args[1].strip()
    app = create_app()
    with app.app_context():
        from app.security import user_has_permission
        from app.approvals import services as svc
        conn = get_db()
        try:
            pr = conn.execute("SELECT * FROM pr_requests WHERE UPPER(pr_no)=UPPER(?)", (pr_no,)).fetchone()
            if not pr:
                sys.exit("No request %r." % pr_no)
            u = conn.execute("SELECT * FROM users WHERE LOWER(username)=LOWER(?)", (new_name,)).fetchone()
            if not u:
                sys.exit("No user %r. Check the exact username in Admin > Users." % new_name)
            u = dict(u)
            print("request   %s  status=%s  department=%s" % (pr["pr_no"], pr["status"], pr["department"]))
            print("now       requester=%s  (%s)" % (pr["requester"], pr["requester_name"]))
            print("new       requester=%s  (%s)  role=%s  active=%s" % (
                u["username"], u.get("full_name"), u.get("role"), u.get("is_active")))
            if (pr["requester"] or "").lower() == u["username"].lower():
                sys.exit("Nothing to do: %s is already the requester." % u["username"])
            if not u.get("is_active"):
                sys.exit("Refused: %s is not an active user." % u["username"])
            signed = conn.execute(
                "SELECT stage FROM pr_steps WHERE pr_id=? AND status='approved' "
                "AND LOWER(COALESCE(approver_user,''))=LOWER(?)", (pr["id"], u["username"])).fetchall()
            if signed:
                sys.exit("Refused: %s already signed this request (%s) and would be approving "
                         "their own request." % (u["username"], ", ".join(r["stage"] for r in signed)))
            if not user_has_permission(u, "proc_create"):
                print("  note: %s has no proc_create permission (cannot raise requests themselves)."
                      % u["username"])
            if not apply:
                print("\nPreview only. Add --apply to make the change.")
                return
            conn.execute("UPDATE pr_requests SET requester=?, requester_name=?, requester_user_id=? "
                         "WHERE id=?", (u["username"], u.get("full_name") or u["username"], u["id"], pr["id"]))
            svc.audit(conn, pr["id"], "admin-script", "requester_changed",
                      "%s -> %s" % (pr["requester"], u["username"]))
            conn.commit()
            after = conn.execute("SELECT requester, requester_name FROM pr_requests WHERE id=?",
                                 (pr["id"],)).fetchone()
            print("\nDone. requester=%s (%s)" % (after["requester"], after["requester_name"]))
        finally:
            conn.close()


if __name__ == "__main__":
    main()
