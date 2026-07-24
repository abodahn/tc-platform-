"""
Compliance & social-audit — schema + seed, merged into the TC Platform DB.

Native `cmp_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db (conn.executescript / INSERT OR IGNORE). Idempotent + non-destructive:
create_and_seed(conn) runs every startup, creates missing tables, and seeds a
small clearly-marked DEMO dataset only when the module is empty.
"""
from datetime import date, timedelta

SCHEMA = """
-- One factory audit (buyer / social / quality / sustainability scheme).
CREATE TABLE IF NOT EXISTS cmp_audits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT,
    scheme TEXT NOT NULL,
    audit_type TEXT,               -- initial / periodic / follow-up / unannounced
    site TEXT,                     -- factory / unit
    auditor TEXT,                  -- audit body (e.g. SGS, Intertek, Bureau Veritas)
    buyer TEXT,                    -- requesting customer (if buyer-mandated)
    scheduled_date TEXT,
    conducted_date TEXT,
    valid_until TEXT,              -- expiry — drives the re-audit alarm
    grade TEXT,                    -- BSCI A-E / Pass / Fail / graded
    status TEXT DEFAULT 'planned',
    report_ref TEXT,
    notes TEXT,
    expiry_alerted INTEGER DEFAULT 0,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cmp_audit_valid ON cmp_audits(valid_until);
CREATE INDEX IF NOT EXISTS ix_cmp_audit_status ON cmp_audits(status);

-- Corrective-action-plan finding raised by an audit.
CREATE TABLE IF NOT EXISTS cmp_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_id INTEGER NOT NULL,
    clause TEXT,                   -- referenced standard clause / area
    finding TEXT NOT NULL,
    severity TEXT DEFAULT 'minor',
    owner TEXT,                    -- responsible person
    due_date TEXT,
    status TEXT DEFAULT 'open',
    evidence TEXT,                 -- closure evidence / notes
    overdue_alerted INTEGER DEFAULT 0,
    created_at TEXT, closed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cmp_find_audit ON cmp_findings(audit_id);
CREATE INDEX IF NOT EXISTS ix_cmp_find_due ON cmp_findings(due_date);

-- Certificate / license with an expiry to watch.
CREATE TABLE IF NOT EXISTS cmp_certs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    cert_type TEXT,
    issuer TEXT,
    cert_no TEXT,
    scope TEXT,
    issue_date TEXT,
    expiry_date TEXT,              -- drives the expiry alarm
    status TEXT DEFAULT 'valid',   -- valid / expired / revoked (computed on sweep)
    doc_ref TEXT,
    notes TEXT,
    expiry_alerted INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cmp_cert_expiry ON cmp_certs(expiry_date);
"""


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    today = date.today()
    now = today.strftime("%Y-%m-%d %H:%M:%S")
    if _empty(conn, "cmp_audits"):
        demo = [
            # ref, scheme, type, site, auditor, buyer, scheduled, conducted, valid_until, grade, status
            ("AUD-0001", "amfori BSCI", "periodic", "Main Factory", "SGS", "EU Buyer A",
             None, str(today - timedelta(days=300)), str(today + timedelta(days=40)), "B", "completed"),
            ("AUD-0002", "SMETA (Sedex)", "periodic", "Main Factory", "Intertek", "UK Retailer",
             None, str(today - timedelta(days=200)), str(today + timedelta(days=160)), "Pass", "completed"),
            ("AUD-0003", "WRAP", "follow-up", "Wash Plant", "Bureau Veritas", None,
             str(today + timedelta(days=25)), None, None, None, "scheduled"),
        ]
        for r in demo:
            conn.execute(
                "INSERT INTO cmp_audits (ref,scheme,audit_type,site,auditor,buyer,scheduled_date,"
                "conducted_date,valid_until,grade,status,created_by,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*r, "seed", now))
        a1 = conn.execute("SELECT id FROM cmp_audits WHERE ref='AUD-0001'").fetchone()
        if a1:
            conn.execute(
                "INSERT INTO cmp_findings (audit_id,clause,finding,severity,owner,due_date,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (a1["id"], "Working Hours", "Overtime records exceed 60h in 3 weeks of the sample.",
                 "major", "HR Manager", str(today + timedelta(days=14)), "in_progress", now))
            conn.execute(
                "INSERT INTO cmp_findings (audit_id,clause,finding,severity,owner,due_date,status,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (a1["id"], "Health & Safety", "Two fire extinguishers past inspection date.",
                 "minor", "Admin", str(today - timedelta(days=3)), "open", now))
    if _empty(conn, "cmp_certs"):
        certs = [
            ("OEKO-TEX Standard 100", "OEKO-TEX", "Hohenstein", "OTS-100-2024", "All cotton knits",
             str(today - timedelta(days=330)), str(today + timedelta(days=35))),
            ("WRAP Gold Certificate", "WRAP", "WRAP", "WRAP-2024-XX", "Main Factory",
             str(today - timedelta(days=180)), str(today + timedelta(days=185))),
            ("Fire Safety License", "Fire/Building", "Civil Defence", "FS-2023", "Premises",
             str(today - timedelta(days=400)), str(today - timedelta(days=10))),
        ]
        for c in certs:
            conn.execute(
                "INSERT INTO cmp_certs (name,cert_type,issuer,cert_no,scope,issue_date,expiry_date,"
                "status,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (*c, "valid", now))
