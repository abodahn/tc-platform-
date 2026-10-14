"""
Seed a Procurement / Approvals test cast — one user per stage of the approval
ladder — so the Purchase-to-Pay flow (requester price lockout + Purchasing
pricing gate + value-based Finance/CFO/CEO approvals) can be walked end to end.

The procurement ROLES already exist in the platform RBAC (added by the
procurement module); this only creates USERS mapped to them. It is idempotent:
re-running updates each user's role and reactivates them, and never touches any
other account.

No credential is stored in this file. The password comes from the environment:
    TC_SEED_PASSWORD   -> used for every seeded user (set this yourself)
If it is not set, a strong random password is generated and printed ONCE.

Run locally:
    python scripts/seed_procurement_users.py
Run on Render (production) — open the service Shell, then:
    TC_SEED_PASSWORD='Choose-A-Strong-One' python scripts/seed_procurement_users.py

Works on both SQLite (local) and PostgreSQL (Render) via app.db.get_db().
"""
import os
import sys
import secrets
import string

# Make the app importable when run from the repo root or the scripts/ folder.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from app import create_app  # noqa: E402
from app.db import get_db  # noqa: E402

from app.test_cast import CAST, seed  # noqa: E402  (one cast, shared with Admin)



def _guard_weak_password_on_production(pw):
    """A trivial password is fine on a local test database and nowhere else.

    This cast includes the Board, the Managing Director and the Financial
    Director — the accounts that sign the largest commitments and release money.
    A four-digit password on those roles in a real database is not a test
    shortcut, it is an open door, so this refuses outright when the target is
    PostgreSQL (which is what production uses) rather than local SQLite.
    """
    from config import Config
    url = (getattr(Config, "DATABASE_URL", "") or os.environ.get("DATABASE_URL") or "")
    is_pg = url.startswith(("postgres://", "postgresql://"))
    weak = len(pw) < 8 or pw.isdigit() or pw.lower() in {"password", "test", "admin"}
    if is_pg and weak:
        sys.exit(
            "REFUSED: the target database is PostgreSQL, which is what "
            "production uses, and TC_SEED_PASSWORD is trivial. This cast "
            "includes the Board, the Managing Director and the Financial "
            "Director. Use a strong password, or seed a local SQLite "
            "database instead.")
    if weak:
        print("  ! Weak password accepted for a LOCAL SQLite database only.")
        print("  ! Do not reuse these accounts anywhere else.")
        print("")


def _password():
    pw = os.environ.get("TC_SEED_PASSWORD")
    if pw:
        return pw, False
    alphabet = string.ascii_letters + string.digits + "!@#$%*?"
    pw = "".join(secrets.choice(alphabet) for _ in range(14))
    return pw, True


def main():
    password, generated = _password()
    _guard_weak_password_on_production(password)
    app = create_app()
    with app.app_context():
        conn = get_db()
        try:
            # reset_existing=True: this script's documented behaviour — re-apply
            # each role, reactivate, and set the password on every cast account.
            created, updated, _skipped = seed(conn, password, reset_existing=True)
        finally:
            conn.close()

    print("=" * 68)
    print(" Procurement test cast seeded")
    print("=" * 68)
    print(" %-16s %-20s %s" % ("USERNAME", "ROLE", "STAGE / PURPOSE"))
    print(" " + "-" * 66)
    for username, role, _fn, desc in CAST:
        print(" %-16s %-20s %s" % (username, role, desc))
    print(" " + "-" * 66)
    print(" created: %d   updated: %d" % (len(created), len(updated)))
    if generated:
        print("\n  PASSWORD (generated — shown once, save it now): %s" % password)
        print("  Tip: set TC_SEED_PASSWORD to choose your own next time.")
    else:
        print("\n  PASSWORD: (the TC_SEED_PASSWORD you provided)")
    print("\n  Log in with any username above + that password, then open")
    print("  /procurement to walk the flow. Change these passwords after testing.")
    print("=" * 68)


if __name__ == "__main__":
    main()
