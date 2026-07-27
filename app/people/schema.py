"""
HR Core — schema + seed, merged into the TC Platform DB.

Native `ppl_*` tables. The employee ROSTER is NOT re-created here: this module
reads `prob_employees` (the platform's employee master, code-keyed) and only owns
the event tables that hang off it — attendance, leave, skills, piece-rate.

Idempotent + non-destructive: create_and_seed(conn) runs every startup, creates
missing tables, and seeds a small clearly-marked DEMO dataset only when empty.
"""
from datetime import date, timedelta

from .constants import DEFAULT_ENTITLEMENT

SCHEMA = """
-- One attendance record per employee per calendar day (the unique index is the
-- business rule: a day cannot be marked twice).
CREATE TABLE IF NOT EXISTS ppl_attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,      -- prob_employees.id (no FK by house convention)
    work_date TEXT NOT NULL,           -- ISO YYYY-MM-DD
    status TEXT DEFAULT 'present',     -- present / absent / late / leave / holiday / off
    check_in TEXT, check_out TEXT,     -- HH:MM
    worked_hours REAL DEFAULT 0,
    ot_hours REAL DEFAULT 0,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ppl_att ON ppl_attendance(employee_id, work_date);
CREATE INDEX IF NOT EXISTS ix_ppl_att_date ON ppl_attendance(work_date);

-- Leave request. `balance_applied` is the idempotency latch: the balance is
-- deducted exactly once when it flips 0->1 and restored once when it flips back.
CREATE TABLE IF NOT EXISTS ppl_leave (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    leave_type TEXT DEFAULT 'annual',  -- annual / sick / unpaid / other
    from_date TEXT, to_date TEXT,
    days REAL DEFAULT 0,               -- inclusive calendar days
    reason TEXT,
    status TEXT DEFAULT 'pending',     -- pending / approved / rejected / cancelled
    approver TEXT, decided_at TEXT,
    balance_applied INTEGER DEFAULT 0,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ppl_leave_emp ON ppl_leave(employee_id, leave_type);
CREATE INDEX IF NOT EXISTS ix_ppl_leave_status ON ppl_leave(status);

-- Entitlement ledger, one row per employee/type/year. `taken` is written ONLY by
-- services._apply_balance so approve/restore can never drift.
CREATE TABLE IF NOT EXISTS ppl_leave_balance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    leave_type TEXT NOT NULL,
    year INTEGER NOT NULL,
    entitled REAL DEFAULT 0,
    taken REAL DEFAULT 0,
    updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ppl_bal ON ppl_leave_balance(employee_id, leave_type, year);

-- Skill matrix cell: what this operator can do on this operation.
CREATE TABLE IF NOT EXISTS ppl_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    operation TEXT NOT NULL,
    level INTEGER DEFAULT 1,           -- 1 trainee .. 5 trainer
    efficiency_pct REAL DEFAULT 0,     -- measured efficiency on THIS operation
    updated_by TEXT, updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ppl_skill ON ppl_skills(employee_id, operation);
CREATE INDEX IF NOT EXISTS ix_ppl_skill_op ON ppl_skills(operation);

-- Piece-rate / incentive record for one operator-day-operation. The derived
-- columns are stored so a payroll figure is reproducible after rates change.
CREATE TABLE IF NOT EXISTS ppl_piece_rate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,
    operation TEXT,
    order_id INTEGER,                  -- ord_orders.id when the output is order-linked
    pieces REAL DEFAULT 0,
    smv REAL DEFAULT 0,                -- standard minute value of the operation
    minutes_worked REAL DEFAULT 0,
    rate_per_minute REAL DEFAULT 0,
    threshold_pct REAL DEFAULT 0,      -- snapshot of the qualifying efficiency
    earned_minutes REAL DEFAULT 0,     -- pieces * smv
    efficiency_pct REAL DEFAULT 0,     -- earned_minutes / minutes_worked * 100
    incentive REAL DEFAULT 0,          -- never negative
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ppl_pr_date ON ppl_piece_rate(work_date);
CREATE INDEX IF NOT EXISTS ix_ppl_pr_emp ON ppl_piece_rate(employee_id);
CREATE INDEX IF NOT EXISTS ix_ppl_pr_order ON ppl_piece_rate(order_id);
"""


# Columns added after first release — idempotent ALTERs on every boot so already
# deployed databases pick them up. try/except + rollback is mandatory: PostgreSQL
# aborts the whole transaction on a failed DDL.
_MIGRATIONS = [
    # WHERE the frozen SMV came from. The piece-rate SMV stays a SNAPSHOT so a paid
    # incentive is reproducible; this only records its provenance and NEVER takes
    # part in the calculation — no payable moves.
    "ALTER TABLE ppl_piece_rate ADD COLUMN smv_source TEXT",
]


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _roster(conn, limit=6):
    """The demo seed hangs off the existing employee master; if probation has not
    seeded (or its table is missing) we simply create the tables and stop."""
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, employee_code, employee_name, department, designation FROM prob_employees "
            "WHERE is_deleted=0 AND active=1 ORDER BY id LIMIT ?", (limit,)).fetchall()]
    except Exception:
        return []


def _first_order_id(conn):
    try:
        r = conn.execute("SELECT id FROM ord_orders ORDER BY id LIMIT 1").fetchone()
        return r["id"] if r else None
    except Exception:
        return None


# Demo skills per department — keeps the matrix believable without a big fixture.
_DEMO_SKILLS = {
    "Sewing":    [("Overlock", 5, 108.0), ("Side seam", 4, 95.0),
                  ("Sleeve attach", 4, 92.0), ("Hem bottom", 3, 85.0)],
    "Quality":   [("End-line QC", 5, 100.0), ("Final assembly", 3, 80.0)],
    "Cutting":   [("Cutting", 4, 96.0), ("Bartack", 2, 70.0)],
    "Finishing": [("Packing", 4, 98.0), ("Button attach", 3, 88.0), ("Hem bottom", 2, 72.0)],
}
# pieces per day for the two demo piece-rate operators (one day deliberately
# below the 75% threshold so the "no incentive" case is visible on screen)
_DEMO_PIECES = [900, 840, 600, 960, 1020]


def create_and_seed(conn):
    # single source of both formulas — demo rows must agree with what the service
    # would compute for the same inputs, or re-saving a seeded day silently changes
    # its worked hours (08:00-17:00 was seeded as 8.0 but books 9.0 on save).
    from .services import _hours, compute_incentive
    conn.executescript(SCHEMA)
    for _ddl in _MIGRATIONS:
        try:
            conn.execute(_ddl)
            conn.commit()
        except Exception:
            conn.rollback()
    today = date.today()
    now = today.strftime("%Y-%m-%d %H:%M:%S")
    emps = _roster(conn)
    if not emps:
        conn.commit()
        return

    def iso(n):
        return str(today + timedelta(days=n))

    if _empty(conn, "ppl_leave_balance"):
        for e in emps:
            for lt in ("annual", "sick"):
                conn.execute(
                    "INSERT INTO ppl_leave_balance (employee_id,leave_type,year,entitled,taken,updated_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (e["id"], lt, today.year, DEFAULT_ENTITLEMENT[lt], 0.0, now))

    if _empty(conn, "ppl_attendance"):
        for i, e in enumerate(emps):
            for back in range(1, 13):          # last 12 days, today excluded
                d = today - timedelta(days=back)
                if d.weekday() == 4:           # Friday — factory weekend
                    st, cin, cout, ot = "off", None, None, 0.0
                elif back == 4 and i % 3 == 2:
                    st, cin, cout, ot = "absent", None, None, 0.0
                elif back == 7 and i % 2 == 0:
                    st, cin, cout, ot = "late", "08:35", "17:00", 0.0
                elif back == 9 and i == 1:
                    st, cin, cout, ot = "leave", None, None, 0.0
                else:
                    st, cin, cout, ot = "present", "08:00", "17:00", (1.5 if back % 5 == 0 else 0.0)
                hours = (_hours(cin, cout) or 0.0) if st in ("present", "late") else 0.0
                conn.execute(
                    "INSERT INTO ppl_attendance (employee_id,work_date,status,check_in,check_out,"
                    "worked_hours,ot_hours,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (e["id"], str(d), st, cin, cout, hours, ot, "seed", now))

    if _empty(conn, "ppl_leave"):
        # one already-approved annual leave (balance deducted) + one pending request
        e0 = emps[0]
        e1 = emps[1] if len(emps) > 1 else emps[0]
        conn.execute(
            "INSERT INTO ppl_leave (employee_id,leave_type,from_date,to_date,days,reason,status,"
            "approver,decided_at,balance_applied,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (e1["id"], "annual", iso(-9), iso(-9), 1.0, "Family matter (DEMO)", "approved",
             "HR Officer", now, 1, "seed", now))
        conn.execute(
            "UPDATE ppl_leave_balance SET taken=taken+1 WHERE employee_id=? AND leave_type='annual' "
            "AND year=?", (e1["id"], today.year))
        conn.execute(
            "INSERT INTO ppl_leave (employee_id,leave_type,from_date,to_date,days,reason,status,"
            "balance_applied,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (e0["id"], "annual", iso(11), iso(13), 3.0, "Annual leave (DEMO)", "pending", 0, "seed", now))

    if _empty(conn, "ppl_skills"):
        for e in emps:
            for op, lvl, eff in _DEMO_SKILLS.get(e.get("department") or "", []):
                conn.execute(
                    "INSERT INTO ppl_skills (employee_id,operation,level,efficiency_pct,updated_by,updated_at) "
                    "VALUES (?,?,?,?,?,?)", (e["id"], op, lvl, eff, "seed", now))

    if _empty(conn, "ppl_piece_rate"):
        oid = _first_order_id(conn)
        pr_emps = [e for e in emps if (e.get("department") or "") in ("Sewing", "Finishing")][:2]
        for e in pr_emps:
            op, smv = ("Overlock", 0.55) if e.get("department") == "Sewing" else ("Packing", 0.30)
            for back, pieces in enumerate(_DEMO_PIECES, start=1):
                d = today - timedelta(days=back)
                if d.weekday() == 4:
                    continue
                c = compute_incentive(pieces, smv, 480.0)
                conn.execute(
                    "INSERT INTO ppl_piece_rate (employee_id,work_date,operation,order_id,pieces,smv,"
                    "minutes_worked,rate_per_minute,threshold_pct,earned_minutes,efficiency_pct,"
                    "incentive,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (e["id"], str(d), op, oid, float(pieces), smv, 480.0, c["rate_per_minute"],
                     c["threshold_pct"], c["earned_minutes"], c["efficiency_pct"], c["incentive"],
                     "seed", now))

    conn.commit()
