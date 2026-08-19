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

# username, role, full name, what they do in the cycle.
# Thresholds below are the DOAM §4.1 OPEX ladder now in force — NOT the older
# 10k/25k/100k figures this cast was first written against, which would send you
# looking for a CFO signature on a request that no longer needs one.
CAST = [
    # --- Procurement: DOAM §4.1 / §4.2 ladder, in signing order ---------------
    ("req.tester",   "normal_user",           "Requester Tester",    "Raises a request. Cannot see or enter any price"),
    ("wh.tester",    "warehouse_manager",     "Warehouse Tester",    "Warehouse rung — always required on OPEX"),
    ("buyer.tester", "purchasing_manager",    "Purchasing Tester",   "PRICING GATE: enters the commercial value, runs sourcing"),
    ("pd.tester",    "plant_director",        "Plant Director",      "Joins above 10,000 — production and maintenance spend"),
    ("scd.tester",   "supply_chain_director", "Supply Chain Dir",    "Joins above 10,000 — inventory replenishment"),
    ("fin.tester",   "financial_director",    "Financial Director",  "Joins above 200,000. ALSO RELEASES PAYMENT (DOAM 7.3.4)"),
    ("cfo.tester",   "cfo",                   "CFO Tester",          "Joins above 500,000. Authorises advances over 25%"),
    ("md.tester",    "managing_director",     "Managing Director",   "Joins above 2,000,000. CAPEX above 250,000"),
    ("bod.tester",   "board",                 "Board Tester",        "Joins above 5,000,000. CAPEX above 2,000,000"),

    # --- Maintenance: ticket -> spares -> work order -------------------------
    ("tech.tester",  "maintenance_technician","Technician Tester",   "Raises tickets, diagnoses, requests spare parts"),
    ("mm.tester",    "maintenance_manager",   "Maintenance Manager", "Assigns tickets, approves spares, closes work orders"),
    ("store.tester", "storekeeper",           "Storekeeper Tester",  "Issues spare parts, receives goods against a PO"),
]



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
