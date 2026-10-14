"""The test cast: one account per rung of the approval ladder, plus the
maintenance roles, so the purchase cycle can be walked end to end.

Shared by scripts/seed_procurement_users.py (command line) and the
Admin -> Users "Create test accounts" button, so the two can never disagree
about who the cast is or which role each account carries.

No credential lives here: the password is always supplied by the caller.
"""
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

# username, role, full name, what they do in the cycle.
# Thresholds below are the DOAM §4.1 OPEX ladder now in force — NOT the older
# 10k/25k/100k figures this cast was first written against, which would send you
# looking for a CFO signature on a request that no longer needs one.
CAST = [
    # --- Procurement: DOAM §4.1 / §4.2 ladder, in signing order ---------------
    ("req.tester",   "normal_user",           "Requester Tester",    "Raises a request. Cannot see or enter any price"),
    # Two warehouse rungs now, not one. The store rung is the STOREKEEPER's and
    # the review rung above it is the MANAGER's, so one account can no longer
    # walk both — which is the entire point of splitting them.
    ("wh.tester",    "warehouse_manager",     "Warehouse Tester",    "Warehouse MANAGER rung — reviews and corrects what the store recorded"),
    ("whm.tester",   "warehouse_manager",     "Warehouse Mgr Tester","Second Warehouse Manager — needed when wh.tester raised the request"),
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
    ("store.tester", "storekeeper",           "Storekeeper Tester",  "STORE rung — records stock on hand, issues parts, receives against a PO"),
]


def _has_status_column(conn):
    """The auth module adds users.account_status. Probe portably: a SELECT works
    on SQLite and PostgreSQL alike, and a failure is rolled back so PostgreSQL
    is not left in an aborted transaction."""
    try:
        conn.execute("SELECT account_status FROM users LIMIT 1").fetchone()
        return True
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False


def seed(conn, password, reset_existing=False):
    """Create every missing cast account with `password` and commit.
    Returns (created, updated, skipped) lists of usernames.

    reset_existing=False — the admin button. An account that already exists is
    left exactly as it is (role, password, active flag), so a second click can
    never reset a password somebody is already using.

    reset_existing=True — the command-line script's documented behaviour:
    re-applies each role, reactivates the account and sets the password.
    """
    has_status = _has_status_column(conn)
    pwhash = generate_password_hash(password)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    created, updated, skipped = [], [], []
    for username, role, full_name, _desc in CAST:
        row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if row and not reset_existing:
            skipped.append(username)
        elif row:
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
    return created, updated, skipped
