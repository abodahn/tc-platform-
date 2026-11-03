"""
Create named accounts with one role and a starting password.

    python scripts/create_named_users.py <role> <username> [<username> ...]

e.g. role normal_user (raise purchase requests only) or storekeeper (signs the
Warehouse step). Usernames are passed in, never listed here: the repo is public.

Every account gets the SAME starting password, which you supply, and is
flagged to change it on first login: until the person picks their own, every
page sends them to My profile -> Change password.

No password is stored in this file. Set it in the environment:
    PowerShell:  $env:TC_START_PASSWORD = '...'; python scripts/create_named_users.py storekeeper a.b c.d
    bash:        TC_START_PASSWORD='...' python scripts/create_named_users.py storekeeper a.b c.d

Idempotent: an account that already exists is left completely alone (its
password and role are NOT touched) and is reported as skipped.
Works on SQLite (local) and PostgreSQL (Render) via app.db.get_db().
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from werkzeug.security import generate_password_hash  # noqa: E402

from app import create_app  # noqa: E402
from app.db import get_db, utcnow  # noqa: E402


def main():
    pw = os.environ.get("TC_START_PASSWORD") or ""
    if not pw:
        sys.exit("Set TC_START_PASSWORD first (see the top of this file).")
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    role, usernames = sys.argv[1], [u.strip().lower() for u in sys.argv[2:]]
    app = create_app()          # runs the additive migrations, incl. must_change_password
    created, skipped = [], []
    with app.app_context():
        from app.security import effective_roles
        if role not in effective_roles():
            sys.exit("Unknown role %r." % role)
        conn = get_db()
        try:
            for username in usernames:
                if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    skipped.append(username)
                    continue
                conn.execute(
                    "INSERT INTO users (username,password_hash,full_name,role,is_active,"
                    "account_status,must_change_password,created_at) VALUES (?,?,?,?,1,'active',1,?)",
                    (username, generate_password_hash(pw), username, role, utcnow()))
                created.append((username, role))
            conn.commit()
        finally:
            conn.close()
    for u, r in created:
        print("  created  %-20s %s" % (u, r))
    for u in skipped:
        print("  skipped  %-20s (already exists - not changed)" % u)
    print("\n%d created, %d skipped. Each must change the password at first login." %
          (len(created), len(skipped)))


if __name__ == "__main__":
    main()
