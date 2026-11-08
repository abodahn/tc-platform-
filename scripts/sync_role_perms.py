"""
Give every built-in role back the permissions the code grants it.

    python scripts/sync_role_perms.py            # apply
    python scripts/sync_role_perms.py --check    # only show what would change

Why: saving a built-in role in Admin -> Roles stores a full copy of its
permissions in custom_roles, and that copy REPLACES the code's list. A copy
saved before a module existed (e.g. Procurement) never receives that module's
permissions, so every user on the role gets 403 on pages the role should open.

Only ADDS missing permissions; never removes one and never touches custom
(non-built-in) roles or users. Permissions are per role, so every existing and
future user on a role gets the fix. Safe to run again after any deploy that
adds a module. Works on SQLite (local) and PostgreSQL (Render).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import create_app  # noqa: E402
from app.db import get_db, utcnow  # noqa: E402


def main():
    check = "--check" in sys.argv
    app = create_app()
    with app.app_context():
        from app.security import ROLES, refresh_db_roles
        conn = get_db()
        changed = 0
        try:
            for row in conn.execute("SELECT role_key, perms_json FROM custom_roles "
                                    "ORDER BY role_key").fetchall():
                key = row["role_key"]
                if key not in ROLES or key == "super_admin":
                    continue                    # custom role: the code has no list for it
                have = set(json.loads(row["perms_json"] or "[]"))
                if "*" in have:
                    continue
                add = sorted(set(ROLES[key]["perms"]) - have)
                if not add:
                    print("  ok       %-24s" % key)
                    continue
                changed += 1
                print("  %-8s %-24s + %s" % ("missing" if check else "fixed", key, ", ".join(add)))
                if not check:
                    conn.execute("UPDATE custom_roles SET perms_json=?, updated_at=? WHERE role_key=?",
                                 (json.dumps(sorted(have | set(add))), utcnow(), key))
            if not check:
                conn.commit()
        finally:
            conn.close()
        if not check:
            refresh_db_roles()
    print("\n%d role(s) %s. Roles never saved in Admin -> Roles already use the code's list."
          % (changed, "need fixing (run without --check)" if check else "fixed"))


if __name__ == "__main__":
    main()
