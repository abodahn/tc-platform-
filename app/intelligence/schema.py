"""
AI Prediction & Intelligence Center — database schema + demo data.

Creates the traceable/auditable ``ai_*`` tables and the operational domain
tables the predictors read (assets, payroll, HR probation, paper usage). All
DDL is SQLite-authored and translated for PostgreSQL by app.db.executescript,
so it runs unchanged on Render. Seeding is idempotent (only when a table is
empty) and non-destructive.
"""
from datetime import datetime, timedelta


SCHEMA = """
-- ===== AI traceability / audit tables =====
CREATE TABLE IF NOT EXISTS ai_model_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid     TEXT,
    started_at  TEXT,
    finished_at TEXT,
    n_alerts    INTEGER DEFAULT 0,
    n_predictions INTEGER DEFAULT 0,
    health_score REAL,
    status      TEXT DEFAULT 'ok',
    note        TEXT
);

CREATE TABLE IF NOT EXISTS ai_risk_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid     TEXT,
    domain      TEXT NOT NULL,
    score       REAL DEFAULT 0,
    level       TEXT,
    headline    TEXT,
    detail_json TEXT,
    computed_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_predictions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid     TEXT,
    domain      TEXT NOT NULL,
    entity_type TEXT,
    entity_ref  TEXT,
    kind        TEXT,
    value       REAL,
    horizon     TEXT,
    confidence  TEXT,
    detail_json TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS ai_alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_uid     TEXT,
    run_uid       TEXT,
    domain        TEXT NOT NULL,
    entity_type   TEXT,
    entity_ref    TEXT,
    title         TEXT,
    risk_score    REAL DEFAULT 0,
    severity      TEXT,
    impact        TEXT,
    recommendation TEXT,
    responsible   TEXT,
    status        TEXT DEFAULT 'new',
    escalation_level INTEGER DEFAULT 0,
    link          TEXT,
    explanation   TEXT,
    created_at    TEXT,
    due_date      TEXT,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_ai_alerts_status ON ai_alerts(status);
CREATE INDEX IF NOT EXISTS ix_ai_alerts_domain ON ai_alerts(domain);

CREATE TABLE IF NOT EXISTS ai_recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid     TEXT,
    domain      TEXT,
    title       TEXT,
    detail      TEXT,
    priority    INTEGER DEFAULT 0,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS ai_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_id    INTEGER,
    username    TEXT,
    verdict     TEXT,
    note        TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS ai_dashboard_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid     TEXT,
    metric_key  TEXT,
    value       REAL,
    label       TEXT,
    unit        TEXT,
    computed_at TEXT
);

CREATE TABLE IF NOT EXISTS ai_assistant_queries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT,
    query       TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS ai_training_data_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uid     TEXT,
    domain      TEXT,
    n_rows      INTEGER,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS ai_system_settings (
    skey        TEXT PRIMARY KEY,
    svalue      TEXT
);

-- ===== Operational domain tables the predictors read =====
CREATE TABLE IF NOT EXISTS assets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tag         TEXT,
    name        TEXT,
    category    TEXT,
    department  TEXT,
    owner       TEXT,
    location    TEXT,
    purchase_cost REAL DEFAULT 0,
    purchase_date TEXT,
    warranty_expiry TEXT,
    status      TEXT DEFAULT 'in_use',
    condition   TEXT DEFAULT 'good',
    last_service_at TEXT,
    maint_cost_ytd REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payroll_rows (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    period      TEXT,
    sheet_version TEXT,          -- 'old' | 'new'
    employee_code TEXT,
    name        TEXT,
    department  TEXT,
    basic       REAL DEFAULT 0,
    allowances  REAL DEFAULT 0,
    deductions  REAL DEFAULT 0,
    net         REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS hr_probation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_code TEXT,
    name        TEXT,
    department  TEXT,
    manager     TEXT,
    start_date  TEXT,
    due_date    TEXT,
    status      TEXT DEFAULT 'pending',  -- pending | completed | passed | failed
    score       REAL,
    feedback    TEXT,
    evaluated_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    department  TEXT,
    month       TEXT,
    sheets      INTEGER DEFAULT 0,
    cost        REAL DEFAULT 0,
    digitizable INTEGER DEFAULT 0
);
"""

DEPTS = ["Cutting", "Sewing", "Finishing", "Embroidery", "Quality", "Warehouse",
         "IT", "Finance", "Human Resources", "Maintenance"]


def _d(days):
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")


def _empty(conn, table):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] == 0
    except Exception:  # noqa: BLE001
        return False


def _seed_assets(conn):
    cats = [("Laptop", 22000), ("Desktop", 15000), ("Printer", 9000),
            ("Server", 120000), ("Scanner", 6000), ("Network Switch", 18000)]
    rows = []
    for i in range(30):
        cat, base = cats[i % len(cats)]
        dept = DEPTS[i % len(DEPTS)]
        age_days = 200 + (i * 90) % 2600           # spread 0.5–7 years
        cost = base * (0.9 + (i % 5) * 0.05)
        # some warranties already expired, some expiring soon, some fine
        warr = _d(-30 + ((i * 137) % 900) - 300)
        cond = ["good", "good", "fair", "poor"][i % 4]
        maint = round(cost * (0.02 + (i % 6) * 0.03), 2)
        rows.append((f"TC-AST-{1000+i}", f"{cat} #{i+1}", cat, dept,
                     f"user{i+1}", f"{dept} area", round(cost, 2), _d(-age_days),
                     warr, "in_use" if i % 7 else "spare", cond, _d(-((i*53) % 400)), maint))
    conn.executemany(
        "INSERT INTO assets (tag,name,category,department,owner,location,purchase_cost,"
        "purchase_date,warranty_expiry,status,condition,last_service_at,maint_cost_ytd) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def _seed_payroll(conn):
    """Two sheets (old June, new July). Most rows identical; a few deliberate
    anomalies so the detector has something real to find."""
    names = [("E{0:03d}".format(i), f"Employee {i}", DEPTS[i % len(DEPTS)]) for i in range(1, 41)]
    old, new = [], []
    for code, name, dept in names:
        basic = 6000 + (int(code[1:]) % 10) * 850
        allow = round(basic * 0.18, 2)
        ded = round(basic * 0.11, 2)
        net = round(basic + allow - ded, 2)
        old.append(("2026-06", "old", code, name, dept, basic, allow, ded, net))
        # new sheet: mostly same, but inject anomalies
        n_basic, n_allow, n_ded = basic, allow, ded
        idx = int(code[1:])
        if idx == 7:      n_net = round(basic + allow - ded + 9000, 2); n_allow = allow + 9000   # spike
        elif idx == 13:   n_ded = round(basic * 0.55, 2); n_net = round(basic + allow - n_ded, 2)  # huge deduction
        elif idx == 21:   n_net = 0.0                                                              # zero net
        else:             n_net = round(n_basic + n_allow - n_ded, 2)
        new.append(("2026-07", "new", code, name, dept, n_basic, n_allow, n_ded, n_net))
    # a duplicated employee and a missing one in the new sheet
    new.append(("2026-07", "new", "E007", "Employee 7", "Sewing", 6800, 1224, 748, 7276))  # duplicate code
    new = [r for r in new if r[2] != "E031"]                                                # E031 dropped
    conn.executemany(
        "INSERT INTO payroll_rows (period,sheet_version,employee_code,name,department,"
        "basic,allowances,deductions,net) VALUES (?,?,?,?,?,?,?,?,?)", old + new)


def _seed_hr(conn):
    rows = []
    for i in range(1, 25):
        dept = DEPTS[i % len(DEPTS)]
        start = _d(-((i * 11) % 150) - 20)
        due = _d(30 - ((i * 11) % 150) + 60)          # some overdue, some soon
        if i % 6 == 0:    status, score, fb = "pending", None, ""            # not started
        elif i % 5 == 0:  status, score, fb = "completed", 58, "Below expectations on attendance."
        elif i % 4 == 0:  status, score, fb = "passed", 82, "Strong performer."
        else:             status, score, fb = "pending", None, ""
        rows.append((f"E{i:03d}", f"Employee {i}", dept, f"Manager {dept}", start, due,
                     status, score, fb, _d(-3) if status in ("completed", "passed") else None))
    conn.executemany(
        "INSERT INTO hr_probation (employee_code,name,department,manager,start_date,due_date,"
        "status,score,feedback,evaluated_at) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def _seed_paper(conn):
    months = ["2026-04", "2026-05", "2026-06", "2026-07"]
    rows = []
    for dept in DEPTS:
        base = 1200 + (hash(dept) % 5) * 900
        for k, m in enumerate(months):
            # gentle downward trend except a couple of departments that spike
            trend = base * (1 - 0.05 * k)
            if dept in ("Human Resources", "Finance") and m == "2026-07":
                trend = base * 1.4                      # spike
            sheets = int(max(200, trend))
            rows.append((dept, m, sheets, round(sheets * 0.12, 2), 1 if dept in ("HR", "Finance", "Quality") else 0))
    conn.executemany(
        "INSERT INTO paper_usage (department,month,sheets,cost,digitizable) VALUES (?,?,?,?,?)", rows)


def create_and_seed(conn):
    """Create AI + domain tables and seed demo data where empty. Idempotent."""
    conn.executescript(SCHEMA)
    conn.commit()
    if _empty(conn, "assets"):
        _seed_assets(conn)
    if _empty(conn, "payroll_rows"):
        _seed_payroll(conn)
    if _empty(conn, "hr_probation"):
        _seed_hr(conn)
    if _empty(conn, "paper_usage"):
        _seed_paper(conn)
    # default settings
    for k, v in (("auto_run", "1"), ("horizon_days", "30")):
        try:
            conn.execute("INSERT OR IGNORE INTO ai_system_settings (skey,svalue) VALUES (?,?)", (k, v))
        except Exception:  # noqa: BLE001
            pass
    conn.commit()
