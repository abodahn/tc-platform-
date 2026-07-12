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
from datetime import datetime, timezone

# Make the app importable when run from the repo root or the scripts/ folder.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from werkzeug.security import generate_password_hash  # noqa: E402
from app import create_app  # noqa: E402
from app.db import get_db  # noqa: E402

# username, role, full name, what they do in the ladder
CAST = [
    ("req.tester",       "normal_user",       "Requester Tester",   "Raises a request (NO pricing — commercial fields are hidden)"),
    ("wh.tester",        "warehouse_manager", "Warehouse Tester",   "Warehouse approval (demand stage)"),
    ("fm.tester",        "factory_manager",   "Factory Mgr Tester", "Factory Manager approval (demand stage)"),
    ("buyer.tester",     "purchasing_manager","Purchasing Tester",  "PURCHASING — enters pricing at the gate  <-- the new part"),
    ("fin.tester",       "finance_manager",   "Finance Tester",     "Finance approval (joins when total >= 10,000)"),
    ("cfo.tester",       "cfo",               "CFO Tester",         "CFO approval (joins when total >= 25,000)"),
    ("ceo.tester",       "ceo",               "CEO Tester",         "CEO approval (joins when total >= 100,000)"),
]


def _password():
    pw = os.environ.get("TC_SEED_PASSWORD")
    if pw:
        return pw, False
    alphabet = string.ascii_letters + string.digits + "!@#$%*?"
    pw = "".join(secrets.choice(alphabet) for _ in range(14))
    return pw, True


def main():
    password, generated = _password()
    app = create_app()
    created, updated = [], []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with app.app_context():
        conn = get_db()
        # Portable probe for the optional account_status column (added by the auth
        # module). SELECT works on both SQLite and PostgreSQL; a failure means the
        # column is absent, so roll back to clear any aborted-transaction state.
        try:
            conn.execute("SELECT account_status FROM users LIMIT 1").fetchone()
            has_status = True
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            has_status = False
        try:
            for username, role, full_name, _desc in CAST:
                row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
                pwhash = generate_password_hash(password)
                if row:
                    if has_status:
                        conn.execute("UPDATE users SET role=?, is_active=1, account_status='active', "
                                     "password_hash=? WHERE username=?", (role, pwhash, username))
                    else:
                        conn.execute("UPDATE users SET role=?, is_active=1, password_hash=? "
                                     "WHERE username=?", (role, pwhash, username))
                    updated.append(username)
                else:
                    if has_status:
                        conn.execute(
                            "INSERT INTO users (username,password_hash,full_name,email,role,is_active,"
                            "account_status,created_at) VALUES (?,?,?,?,?,1,'active',?)",
                            (username, pwhash, full_name, username + "@tcgarments.com", role, now))
                    else:
                        conn.execute(
                            "INSERT INTO users (username,password_hash,full_name,email,role,is_active,"
                            "created_at) VALUES (?,?,?,?,?,1,?)",
                            (username, pwhash, full_name, username + "@tcgarments.com", role, now))
                    created.append(username)
            conn.commit()
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
