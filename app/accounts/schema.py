"""
Accounts (Auth) — schema + seed, merged into the platform DB.

Native `auth_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db.executescript. `create_and_seed(conn)` is idempotent + non-destructive:
creates missing tables, seeds `auth_settings` and the org-dropdown options
(sourced from existing data, never hardcoded in the frontend). The `users`
column additions are applied by the ALTER loop in app.db.init_db.
"""
from .constants import DEFAULT_SETTINGS

SCHEMA = """
-- Single-use, hashed verification / password-reset tokens
CREATE TABLE IF NOT EXISTS auth_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    token_type TEXT,                 -- verify_email | password_reset
    token_hash TEXT,                 -- sha256(raw token); raw never stored/logged
    issued_at TEXT, expires_at TEXT, used_at TEXT, invalidated_at TEXT,
    ip TEXT, user_agent TEXT, meta TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_tok_hash ON auth_tokens(token_hash);
CREATE INDEX IF NOT EXISTS ix_auth_tok_user ON auth_tokens(user_id, token_type);

-- Security audit trail (result + ua + request id; never stores secrets)
CREATE TABLE IF NOT EXISTS auth_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event TEXT, actor_id INTEGER, actor_name TEXT,
    target_user_id INTEGER, target_ref TEXT, result TEXT,
    ip TEXT, user_agent TEXT, request_id TEXT, meta_json TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_ev_time ON auth_events(created_at);
CREATE INDEX IF NOT EXISTS ix_auth_ev_target ON auth_events(target_user_id);
CREATE INDEX IF NOT EXISTS ix_auth_ev_event ON auth_events(event);

-- DB-backed rate limiter (shared across workers, survives restart)
CREATE TABLE IF NOT EXISTS auth_ratelimit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket TEXT, count INTEGER DEFAULT 0,
    window_start TEXT, last_at TEXT, blocked_until TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_rl_bucket ON auth_ratelimit(bucket);

-- Password history (block reuse of the last N)
CREATE TABLE IF NOT EXISTS auth_password_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, password_hash TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_pwh_user ON auth_password_history(user_id);

-- Org dropdown options (company/department/location/job_title/manager)
CREATE TABLE IF NOT EXISTS auth_org_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT, value TEXT, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_auth_org_kind ON auth_org_options(kind, active);

-- Invitations (invite-only mode)
CREATE TABLE IF NOT EXISTS auth_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT, role TEXT, invited_by TEXT, created_at TEXT,
    used_at TEXT, expires_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_auth_inv_email ON auth_invites(email);

-- Key/value module settings
CREATE TABLE IF NOT EXISTS auth_settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:  # noqa: BLE001
        return False


def _distinct(conn, sql):
    try:
        return [r[0] for r in conn.execute(sql).fetchall() if r[0] and str(r[0]).strip()]
    except Exception:  # noqa: BLE001
        return []


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    conn.commit()

    # settings
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO auth_settings (key,value) VALUES (?,?)", (k, v))
    conn.commit()

    # org dropdown options — seed from existing data (roster/users), not hardcoded
    if _empty(conn, "auth_org_options"):
        seed = {
            "company": ["T&C Garments S.A.E."],
            "department": _distinct(conn, "SELECT DISTINCT department FROM prob_employees "
                                          "WHERE department IS NOT NULL ORDER BY department LIMIT 60"),
            "location": _distinct(conn, "SELECT DISTINCT section FROM prob_employees "
                                        "WHERE section IS NOT NULL ORDER BY section LIMIT 80"),
            "job_title": _distinct(conn, "SELECT DISTINCT designation FROM prob_employees "
                                         "WHERE designation IS NOT NULL ORDER BY designation LIMIT 80"),
            "manager": _distinct(conn, "SELECT DISTINCT direct_manager FROM prob_employees "
                                       "WHERE direct_manager IS NOT NULL ORDER BY direct_manager LIMIT 60"),
        }
        for kind, values in seed.items():
            for i, v in enumerate(dict.fromkeys(values)):     # de-dupe, keep order
                conn.execute("INSERT INTO auth_org_options (kind,value,active,sort) VALUES (?,?,?,?)",
                             (kind, str(v).strip(), 1, i))
        conn.commit()
