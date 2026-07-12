"""
Probation Management — schema + seed, merged into the TC Platform DB.

Native `prob_*` tables (no separate database). DDL is SQLite-authored and
translated for PostgreSQL by app.db.executescript. Idempotent + non-destructive:
`create_and_seed(conn)` runs every startup, creates missing tables, seeds the
default evaluation template (the 8 official criteria), module settings, and a
small, clearly-marked DEMO dataset only when the module is empty.
"""
from datetime import date, timedelta

from .constants import CRITERIA, CATEGORIES, DEFAULT_SETTINGS, DEFAULT_MAX_TOTAL

SCHEMA = """
-- Employee roster (code-keyed; populated by import). The probation module's
-- reference to the "central employee record" — joined to platform identity by code.
CREATE TABLE IF NOT EXISTS prob_employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_code TEXT UNIQUE NOT NULL,
    employee_name TEXT,
    department TEXT, section TEXT, designation TEXT,
    direct_manager TEXT, section_head TEXT, hr_rep TEXT,
    employment_type TEXT,
    joining_date TEXT, probation_start_date TEXT, probation_end_date TEXT,
    warnings_count INTEGER DEFAULT 0, unauthorized_absence_count INTEGER DEFAULT 0,
    penalties_count INTEGER DEFAULT 0, annual_leaves_count INTEGER DEFAULT 0,
    imported_final_recommendation TEXT, source_file TEXT, source_row INTEGER,
    active INTEGER DEFAULT 1, is_deleted INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_emp_dept ON prob_employees(department, section);
CREATE INDEX IF NOT EXISTS ix_prob_emp_end ON prob_employees(probation_end_date);

-- Evaluation templates (configurable) + their criteria (snapshot-safe copies)
CREATE TABLE IF NOT EXISTS prob_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, description TEXT,
    is_active INTEGER DEFAULT 1, is_default INTEGER DEFAULT 0,
    scope_department TEXT, scope_job_level TEXT,
    score_min INTEGER DEFAULT 1, score_max INTEGER DEFAULT 5,
    version INTEGER DEFAULT 1, is_deleted INTEGER DEFAULT 0,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS prob_template_criteria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER,
    category TEXT, criterion_key TEXT,
    label_ar TEXT, label_en TEXT, label_tr TEXT,
    desc_ar TEXT, desc_en TEXT, desc_tr TEXT,
    weight REAL DEFAULT 1, score_min INTEGER DEFAULT 1, score_max INTEGER DEFAULT 5,
    guidance TEXT, sort_order INTEGER DEFAULT 0, active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_prob_tmplcrit ON prob_template_criteria(template_id);

-- Probation case (one per employee probation period)
CREATE TABLE IF NOT EXISTS prob_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_no TEXT UNIQUE, employee_id INTEGER, template_id INTEGER,
    department TEXT, section TEXT, job_title TEXT,
    direct_manager TEXT, section_head TEXT, hr_rep TEXT, employment_type TEXT,
    joining_date TEXT, probation_start_date TEXT, probation_end_date TEXT,
    cycle_stage TEXT DEFAULT 'initial',
    status TEXT DEFAULT 'draft', final_outcome TEXT,
    opened_by TEXT, opened_at TEXT, closed_by TEXT, closed_at TEXT,
    reopen_count INTEGER DEFAULT 0, is_deleted INTEGER DEFAULT 0,
    notes TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_case_emp ON prob_cases(employee_id);
CREATE INDEX IF NOT EXISTS ix_prob_case_status ON prob_cases(status);
CREATE INDEX IF NOT EXISTS ix_prob_case_end ON prob_cases(probation_end_date);
CREATE INDEX IF NOT EXISTS ix_prob_case_dept ON prob_cases(department, section);

-- Evaluation (current assessment for a case; prior versions archived)
CREATE TABLE IF NOT EXISTS prob_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER, employee_id INTEGER, template_id INTEGER,
    evaluator_id INTEGER, evaluator_name TEXT,
    manager_id INTEGER, section_head_id INTEGER,
    hr_reviewer_id INTEGER, hr_reviewer_name TEXT,
    status TEXT DEFAULT 'draft', current_step TEXT DEFAULT 'evaluation',
    evaluation_date TEXT,
    warnings_count INTEGER, unauthorized_absence_count INTEGER,
    penalties_count INTEGER, annual_leaves_count INTEGER,
    total_score INTEGER, max_score INTEGER DEFAULT 40, percentage_score REAL,
    auto_recommendation TEXT, final_recommendation TEXT, override_justification TEXT,
    hr_decision TEXT, hr_rejection_reason TEXT,
    manager_comments TEXT, strengths TEXT, improvement_points TEXT, training_needs TEXT,
    employee_acknowledged INTEGER DEFAULT 0, version INTEGER DEFAULT 1,
    submitted_at TEXT, approved_at TEXT, rejected_at TEXT, returned_at TEXT,
    pdf_path TEXT, excel_path TEXT, is_deleted INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_eval_case ON prob_evaluations(case_id);
CREATE INDEX IF NOT EXISTS ix_prob_eval_status ON prob_evaluations(status);
CREATE INDEX IF NOT EXISTS ix_prob_eval_emp ON prob_evaluations(employee_id);

-- Per-criterion scores (label snapshot so history is stable)
CREATE TABLE IF NOT EXISTS prob_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER, category TEXT, criterion_key TEXT,
    label_ar TEXT, label_en TEXT, label_tr TEXT,
    weight REAL DEFAULT 1, score INTEGER, notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_score_eval ON prob_scores(evaluation_id);

-- Version history snapshots (on return / reopen)
CREATE TABLE IF NOT EXISTS prob_eval_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    evaluation_id INTEGER, case_id INTEGER, version INTEGER,
    snapshot_json TEXT, reason TEXT, archived_by TEXT, archived_at TEXT
);

-- Workflow transitions (approval actions)
CREATE TABLE IF NOT EXISTS prob_workflow_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER, evaluation_id INTEGER,
    action_by INTEGER, action_by_name TEXT, action_type TEXT,
    from_status TEXT, to_status TEXT, comment TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_wf_case ON prob_workflow_actions(case_id);

-- Probation extensions
CREATE TABLE IF NOT EXISTS prob_extensions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER, employee_id INTEGER,
    previous_end_date TEXT, new_end_date TEXT, extra_days INTEGER,
    reason TEXT, extended_by TEXT, extended_by_name TEXT, created_at TEXT
);

-- Comments (workflow) + confidential HR-only notes
CREATE TABLE IF NOT EXISTS prob_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER, evaluation_id INTEGER,
    author_id INTEGER, author_name TEXT, author_role TEXT,
    body TEXT, is_confidential INTEGER DEFAULT 0, is_deleted INTEGER DEFAULT 0, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_cmt_case ON prob_comments(case_id);

-- Access-controlled attachments
CREATE TABLE IF NOT EXISTS prob_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER, evaluation_id INTEGER,
    file_name TEXT, stored_name TEXT, file_type TEXT, file_size INTEGER,
    is_confidential INTEGER DEFAULT 0, scan_status TEXT DEFAULT 'not_scanned',
    uploaded_by TEXT, uploaded_by_name TEXT, uploaded_at TEXT, is_deleted INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_prob_att_case ON prob_attachments(case_id);

-- Notification / reminder history (mirrors into the platform bell)
CREATE TABLE IF NOT EXISTS prob_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER, employee_id INTEGER, target_user TEXT,
    kind TEXT, title TEXT, message TEXT, channel TEXT DEFAULT 'inapp',
    status TEXT DEFAULT 'sent', offset_days INTEGER, sent_at TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_notif_target ON prob_notifications(target_user);
CREATE INDEX IF NOT EXISTS ix_prob_notif_dedup ON prob_notifications(case_id, kind, offset_days);

-- Rich audit trail (before/after; never stores secrets)
CREATE TABLE IF NOT EXISTS prob_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER, username TEXT, role TEXT, action TEXT,
    entity_type TEXT, entity_id INTEGER, employee_ref TEXT, case_ref TEXT,
    old_json TEXT, new_json TEXT, ip TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_prob_audit_time ON prob_audit(created_at);
CREATE INDEX IF NOT EXISTS ix_prob_audit_entity ON prob_audit(entity_type, entity_id);

-- Import batches (idempotent import reports)
CREATE TABLE IF NOT EXISTS prob_import_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_name TEXT, file_name TEXT, imported_by TEXT, imported_by_name TEXT, imported_at TEXT,
    total INTEGER DEFAULT 0, created INTEGER DEFAULT 0, updated INTEGER DEFAULT 0,
    skipped INTEGER DEFAULT 0, errors INTEGER DEFAULT 0, report_json TEXT
);

-- Module settings (key/value)
CREATE TABLE IF NOT EXISTS prob_settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""


def _d(n):
    return (date.today() + timedelta(days=n)).isoformat()


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:  # noqa: BLE001
        return False


def create_and_seed(conn):
    from app.db import utcnow
    conn.executescript(SCHEMA)
    conn.commit()
    now = utcnow()

    # --- settings ---
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO prob_settings (key,value) VALUES (?,?)", (k, v))
    conn.commit()

    # --- default template + its 8 criteria ---
    if _empty(conn, "prob_templates"):
        conn.execute(
            "INSERT INTO prob_templates (name,description,is_active,is_default,score_min,score_max,"
            "version,created_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("Standard Probation Evaluation",
             "Official 8-criterion probation form (4 professional + 4 behaviour).",
             1, 1, 1, 5, 1, "system", now, now),
        )
        conn.commit()
    trow = conn.execute("SELECT id FROM prob_templates WHERE is_default=1 ORDER BY id LIMIT 1").fetchone()
    tmpl_id = trow["id"] if trow else None
    if tmpl_id and _empty(conn, "prob_template_criteria"):
        for i, c in enumerate(CRITERIA):
            conn.execute(
                "INSERT INTO prob_template_criteria (template_id,category,criterion_key,"
                "label_ar,label_en,label_tr,desc_ar,desc_en,desc_tr,weight,score_min,score_max,"
                "sort_order,active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tmpl_id, c["category"], c["key"], c["label_ar"], c["label_en"], c["label_tr"],
                 c.get("desc_ar"), c.get("desc_en"), c.get("desc_tr"), c.get("weight", 1),
                 1, 5, i, 1),
            )
        conn.commit()

    # --- DEMO data (clearly marked, non-production) ---
    if _empty(conn, "prob_employees"):
        demo = [
            # code, name, dept, section, designation, manager, section_head, join(-days), end(+days), type
            ("TC-1001", "Mahmoud Adel", "Sewing", "Line 3", "Machine Operator",
             "Khaled Nasser", "Ayman Fathi", -75, 15, "Full-time"),
            ("TC-1002", "Sara Ibrahim", "Quality", "End-line", "QC Inspector",
             "Mona Sabry", "Ayman Fathi", -84, 6, "Full-time"),
            ("TC-1003", "Yusuf Kaya", "Cutting", "Cutting A", "Cutter",
             "Khaled Nasser", "Ayman Fathi", -95, -5, "Full-time"),
            ("TC-1004", "Amina Hassan", "Finishing", "Packing", "Packer",
             "Mona Sabry", "Ayman Fathi", -40, 50, "Full-time"),
        ]
        for code, name, dept, sec, desig, mgr, head, jd, ed, etype in demo:
            conn.execute(
                "INSERT OR IGNORE INTO prob_employees (employee_code,employee_name,department,section,"
                "designation,direct_manager,section_head,hr_rep,employment_type,joining_date,"
                "probation_start_date,probation_end_date,warnings_count,unauthorized_absence_count,"
                "penalties_count,annual_leaves_count,source_file,active,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (code, name, dept, sec, desig, mgr, head, "HR Officer", etype, _d(jd),
                 _d(jd), _d(ed), 0, 0, 0, 0, "DEMO", 1, now, now),
            )
        conn.commit()

    # One demo open case + draft evaluation so the dashboard isn't empty.
    if _empty(conn, "prob_cases"):
        emp = conn.execute("SELECT * FROM prob_employees WHERE employee_code='TC-1001'").fetchone()
        if emp:
            conn.execute(
                "INSERT INTO prob_cases (case_no,employee_id,template_id,department,section,job_title,"
                "direct_manager,section_head,hr_rep,employment_type,joining_date,probation_start_date,"
                "probation_end_date,cycle_stage,status,opened_by,opened_at,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("PC-0001", emp["id"], tmpl_id, emp["department"], emp["section"], emp["designation"],
                 emp["direct_manager"], emp["section_head"], emp["hr_rep"], emp["employment_type"],
                 emp["joining_date"], emp["probation_start_date"], emp["probation_end_date"],
                 "initial", "draft", "system", now, now, now),
            )
            conn.commit()
            case = conn.execute("SELECT id FROM prob_cases WHERE case_no='PC-0001'").fetchone()
            conn.execute(
                "INSERT INTO prob_evaluations (case_id,employee_id,template_id,status,current_step,"
                "evaluation_date,max_score,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (case["id"], emp["id"], tmpl_id, "draft", "evaluation", _d(0),
                 DEFAULT_MAX_TOTAL, 1, now, now),
            )
            conn.commit()
            ev = conn.execute("SELECT id FROM prob_evaluations WHERE case_id=?", (case["id"],)).fetchone()
            for c in CRITERIA:
                conn.execute(
                    "INSERT INTO prob_scores (evaluation_id,category,criterion_key,label_ar,label_en,"
                    "label_tr,weight,score,notes) VALUES (?,?,?,?,?,?,?,?,?)",
                    (ev["id"], c["category"], c["key"], c["label_ar"], c["label_en"], c["label_tr"],
                     c.get("weight", 1), None, None),
                )
            conn.commit()
