"""
TC Platform — Maintenance module database schema + seed.

All CMMS tables live alongside the platform metadata in platform.db (same
SQLite connection style). Creation is idempotent (CREATE TABLE IF NOT EXISTS)
and seeding only runs when empty, so existing data is never overwritten.
"""
from datetime import datetime, timedelta, timezone


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


SCHEMA = """
-- ===== Machines =====
CREATE TABLE IF NOT EXISTS mnt_machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT, type TEXT, brand TEXT, model TEXT, serial TEXT,
    department TEXT, area TEXT, line_no TEXT, location TEXT,
    install_date TEXT, warranty_status TEXT, vendor TEXT,
    criticality TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'running',
    qr_token TEXT, image TEXT,
    last_pm_date TEXT, next_pm_date TEXT,
    total_downtime_min INTEGER DEFAULT 0,
    breakdowns INTEGER DEFAULT 0,
    cost_to_date REAL DEFAULT 0,
    -- Needle system (DPX17, DCX27 ...): the join to the RT-07 broken-needle record.
    needle_system TEXT,
    -- Cycle counter off the machine, and when it was read. Usage-based PM needs both.
    meter_reading REAL, meter_reading_at TEXT,
    -- Provenance for the register import: the FIRMA NO card slot the machine was
    -- filed under (a slot is re-issued, so it is NOT the key), and whether the
    -- record appears in the current 2023 register (lets "current fleet only" be a
    -- filter instead of a decision taken at import time).
    legacy_card_no TEXT, in_register_2023 INTEGER DEFAULT 0,
    remarks TEXT, is_active INTEGER DEFAULT 1, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_mnt_machine_code ON mnt_machines(code);
CREATE INDEX IF NOT EXISTS ix_mnt_machine_status ON mnt_machines(status);
CREATE INDEX IF NOT EXISTS ix_mnt_machine_dept ON mnt_machines(department);

-- ===== Spare parts =====
CREATE TABLE IF NOT EXISTS mnt_spare_parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT, description TEXT, category TEXT,
    compatible_types TEXT, brand TEXT, spec TEXT,
    uom TEXT DEFAULT 'pcs',
    stock_qty REAL DEFAULT 0, reserved_qty REAL DEFAULT 0, min_level REAL DEFAULT 0,
    reorder_level REAL DEFAULT 0, max_level REAL DEFAULT 0,
    warehouse TEXT, bin TEXT,
    avg_cost REAL DEFAULT 0, last_price REAL DEFAULT 0,
    vendor TEXT, lead_time_days INTEGER DEFAULT 0,
    criticality TEXT DEFAULT 'medium',
    image TEXT, qr_token TEXT,
    is_active INTEGER DEFAULT 1, remarks TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_mnt_spare_code ON mnt_spare_parts(code);
CREATE INDEX IF NOT EXISTS ix_mnt_spare_cat ON mnt_spare_parts(category);

CREATE TABLE IF NOT EXISTS mnt_spare_compat (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spare_id INTEGER, machine_id INTEGER,
    FOREIGN KEY (spare_id) REFERENCES mnt_spare_parts(id) ON DELETE CASCADE,
    FOREIGN KEY (machine_id) REFERENCES mnt_machines(id) ON DELETE CASCADE
);

-- ===== Tickets / work orders =====
CREATE TABLE IF NOT EXISTS mnt_tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_no TEXT UNIQUE,
    work_order_no TEXT,
    requester TEXT, requester_user_id INTEGER,
    department TEXT, area TEXT, line_no TEXT,
    machine_id INTEGER, machine_code TEXT,
    issue_category TEXT, description TEXT,
    priority TEXT DEFAULT 'medium', severity TEXT DEFAULT 'moderate',
    safety_impact INTEGER DEFAULT 0, production_stopped INTEGER DEFAULT 0,
    est_downtime_min INTEGER DEFAULT 0, shift TEXT, remarks TEXT,
    status TEXT DEFAULT 'submitted',
    assigned_to TEXT, assigned_team TEXT,
    response_due TEXT, resolution_due TEXT,
    -- repair / test / closure summary
    action_performed TEXT, old_part_returned INTEGER DEFAULT 0,
    machine_running TEXT, test_result TEXT, safety_check TEXT,
    final_notes TEXT, rejection_reason TEXT,
    -- SLA timestamps
    created_at TEXT, reviewed_at TEXT, assigned_at TEXT,
    diag_started_at TEXT, diag_done_at TEXT, parts_req_at TEXT,
    approval_started_at TEXT, approval_done_at TEXT,
    issued_at TEXT, received_at TEXT, repair_started_at TEXT,
    repair_done_at TEXT, testing_done_at TEXT, closed_at TEXT, reopened_at TEXT,
    total_downtime_min INTEGER DEFAULT 0, sla_breach INTEGER DEFAULT 0,
    cost REAL DEFAULT 0, is_active INTEGER DEFAULT 1,
    FOREIGN KEY (machine_id) REFERENCES mnt_machines(id)
);
CREATE INDEX IF NOT EXISTS ix_mnt_ticket_no ON mnt_tickets(ticket_no);
CREATE INDEX IF NOT EXISTS ix_mnt_ticket_status ON mnt_tickets(status);
CREATE INDEX IF NOT EXISTS ix_mnt_ticket_prio ON mnt_tickets(priority);
CREATE INDEX IF NOT EXISTS ix_mnt_ticket_created ON mnt_tickets(created_at);
CREATE INDEX IF NOT EXISTS ix_mnt_ticket_dept ON mnt_tickets(department);

CREATE TABLE IF NOT EXISTS mnt_diagnosis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER, technician TEXT,
    fault_found TEXT, root_cause TEXT, diagnosis TEXT, required_action TEXT,
    spare_needed INTEGER DEFAULT 0, temp_fix INTEGER DEFAULT 0,
    can_run_partial INTEGER DEFAULT 0, safety_risk INTEGER DEFAULT 0,
    est_repair_min INTEGER DEFAULT 0, notes TEXT, created_at TEXT,
    FOREIGN KEY (ticket_id) REFERENCES mnt_tickets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mnt_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER, username TEXT, body TEXT, created_at TEXT,
    FOREIGN KEY (ticket_id) REFERENCES mnt_tickets(id) ON DELETE CASCADE
);

-- ===== Spare part requests =====
CREATE TABLE IF NOT EXISTS mnt_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_no TEXT UNIQUE,
    ticket_id INTEGER, work_order_no TEXT,
    machine_id INTEGER, technician TEXT, reason TEXT,
    urgency TEXT DEFAULT 'normal', old_part_returned INTEGER DEFAULT 0,
    status TEXT DEFAULT 'submitted', notes TEXT,
    created_at TEXT, decided_at TEXT,
    FOREIGN KEY (ticket_id) REFERENCES mnt_tickets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_mnt_req_no ON mnt_requests(request_no);
CREATE INDEX IF NOT EXISTS ix_mnt_req_status ON mnt_requests(status);

CREATE TABLE IF NOT EXISTS mnt_request_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER, spare_id INTEGER, part_code TEXT, part_name TEXT,
    qty_requested REAL DEFAULT 0, uom TEXT,
    qty_approved REAL DEFAULT 0, qty_issued REAL DEFAULT 0,
    available_at_request REAL DEFAULT 0, notes TEXT,
    FOREIGN KEY (request_id) REFERENCES mnt_requests(id) ON DELETE CASCADE,
    FOREIGN KEY (spare_id) REFERENCES mnt_spare_parts(id)
);

CREATE TABLE IF NOT EXISTS mnt_approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER, level INTEGER DEFAULT 1,
    approver_role TEXT, approver_user TEXT,
    status TEXT DEFAULT 'pending', comment TEXT,
    created_at TEXT, decided_at TEXT,
    FOREIGN KEY (request_id) REFERENCES mnt_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_mnt_appr_status ON mnt_approvals(status);

-- ===== Stock movements & vouchers =====
CREATE TABLE IF NOT EXISTS mnt_stock_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_no TEXT UNIQUE, type TEXT, spare_id INTEGER,
    qty REAL DEFAULT 0, before_qty REAL DEFAULT 0, after_qty REAL DEFAULT 0,
    ticket_id INTEGER, request_id INTEGER, machine_id INTEGER,
    performed_by TEXT, approved_by TEXT, notes TEXT, created_at TEXT,
    FOREIGN KEY (spare_id) REFERENCES mnt_spare_parts(id)
);
CREATE INDEX IF NOT EXISTS ix_mnt_move_spare ON mnt_stock_movements(spare_id);
CREATE INDEX IF NOT EXISTS ix_mnt_move_type ON mnt_stock_movements(type);

CREATE TABLE IF NOT EXISTS mnt_vouchers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    voucher_no TEXT UNIQUE, request_id INTEGER, ticket_id INTEGER,
    spare_id INTEGER, part_code TEXT, part_name TEXT, qty_issued REAL DEFAULT 0,
    issued_by TEXT, received_by TEXT, warehouse TEXT, bin TEXT,
    machine_code TEXT, remarks TEXT, created_at TEXT, received_at TEXT
);

-- ===== Preventive maintenance =====
CREATE TABLE IF NOT EXISTS mnt_pm_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id INTEGER, title TEXT, frequency TEXT DEFAULT 'monthly',
    interval_days INTEGER DEFAULT 30, assigned_to TEXT,
    last_done TEXT, next_due TEXT, active INTEGER DEFAULT 1, created_at TEXT,
    FOREIGN KEY (machine_id) REFERENCES mnt_machines(id) ON DELETE CASCADE
);

-- Where a machine physically stands. The archive resolved 115 places into a
-- tree (site -> building -> floor -> department -> line -> position); until now
-- `area` and `line_no` on a machine were free text, so "HALL A", "Hall-A" and
-- "A HOLÜ" were three different places. `parent_code` is the tree, and both
-- labels are kept because the floor speaks Turkish and the reports do not.
CREATE TABLE IF NOT EXISTS mnt_locations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE, level TEXT, site TEXT, building TEXT, building_en TEXT,
    floor TEXT, department TEXT, line_no TEXT, position_code TEXT,
    label_tr TEXT, label_en TEXT, parent_code TEXT,
    machines_observed INTEGER DEFAULT 0, mentions INTEGER DEFAULT 0,
    confidence TEXT, notes TEXT, is_active INTEGER DEFAULT 1, created_at TEXT
);

-- Needle cost and consumption per machine MODEL (not per machine): what a needle
-- costs, how many a machine carries, and how often they are replaced. This is
-- what turns "we spend a lot on needles" into a number per model.
-- `currency` is stored verbatim — the source says "UNKNOWN - not stated" for
-- every row, and inventing EGP or USD here would be a fabricated figure on a
-- cost report.
CREATE TABLE IF NOT EXISTS mnt_needle_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model TEXT, model_tokens TEXT, machine_type_tr TEXT, machine_type_ar TEXT,
    brand TEXT, supplier TEXT, machines_qty REAL,
    needle_unit_price REAL, needles_per_machine REAL,
    replacements_per_period REAL, period_cost_total REAL,
    currency TEXT, confidence TEXT, created_at TEXT
);

-- Engineering Justification Report — DOAM §6, form T&C-PUF-09, retained 3 years.
--
-- "Every requisition for spares, maintenance, repair, operating supplies, or
-- workshop activity, at any value, must carry a signed Engineering Justification
-- Report. Procurement does not accept the requisition without it."
--
-- The eight columns between machine_id and alternatives are DOAM Table 14's
-- mandatory contents, one per field. They are columns rather than a free-text
-- note precisely so the gate can refuse an incomplete report: a justification
-- that does not state criticality or the store stock check is the kind of
-- paperwork that gets waved through, which is what this control exists to stop.
CREATE TABLE IF NOT EXISTS mnt_eng_justifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ejr_no TEXT UNIQUE,
    machine_id INTEGER,
    location TEXT,
    request_type TEXT,          -- breakdown | preventive | predictive | improvement | consumable
    description TEXT,
    root_cause TEXT,
    criticality TEXT,           -- production_critical | safety | quality | routine
    downtime_risk TEXT,
    stock_on_hand REAL,
    stock_checked_with TEXT,    -- who in Stores confirmed it
    alternatives TEXT,          -- repair vs replace, local vs import
    -- §7.4.2: an emergency or breakdown purchase may proceed, but the report is
    -- still required and must be documented within 24 hours. Recording the
    -- deadline is what makes "within 24 hours" auditable rather than aspirational.
    is_emergency INTEGER DEFAULT 0,
    emergency_due_at TEXT,
    status TEXT DEFAULT 'draft',   -- draft | submitted | approved | rejected
    created_by TEXT, created_at TEXT,
    submitted_at TEXT,
    -- The Engineering Head's technical approval (DOAM Table 13 step 3, an L3
    -- authority). decided_signature holds the digital-signature image reference
    -- so the printed report carries the same signature as a procurement PDF.
    decided_by TEXT, decided_at TEXT, decision_note TEXT, decided_signature TEXT,
    is_active INTEGER DEFAULT 1,
    FOREIGN KEY (machine_id) REFERENCES mnt_machines(id)
);

CREATE TABLE IF NOT EXISTS mnt_pm_checklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER, item TEXT, required INTEGER DEFAULT 1,
    photo_required INTEGER DEFAULT 0, sort INTEGER DEFAULT 0,
    FOREIGN KEY (plan_id) REFERENCES mnt_pm_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mnt_pm_work_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pm_no TEXT UNIQUE, plan_id INTEGER, machine_id INTEGER,
    scheduled_date TEXT, status TEXT DEFAULT 'scheduled',
    assigned_to TEXT, completed_at TEXT, notes TEXT, created_at TEXT,
    FOREIGN KEY (plan_id) REFERENCES mnt_pm_plans(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS mnt_pm_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pm_wo_id INTEGER, item TEXT, result TEXT, notes TEXT,
    FOREIGN KEY (pm_wo_id) REFERENCES mnt_pm_work_orders(id) ON DELETE CASCADE
);

-- ===== Approval matrix / notifications / audit / settings / attachments =====
CREATE TABLE IF NOT EXISTS mnt_approval_matrix (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT, request_type TEXT DEFAULT 'spare_issue',
    department TEXT, machine_criticality TEXT, part_criticality TEXT,
    qty_threshold REAL DEFAULT 0, cost_threshold REAL DEFAULT 0,
    priority TEXT, levels TEXT, escalation_hours INTEGER DEFAULT 24,
    active INTEGER DEFAULT 1, created_at TEXT
);

CREATE TABLE IF NOT EXISTS mnt_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_role TEXT, target_user_id INTEGER,
    title TEXT, message TEXT, entity_type TEXT, entity_id INTEGER,
    severity TEXT DEFAULT 'info', action_url TEXT,
    is_read INTEGER DEFAULT 0, created_at TEXT
);

CREATE TABLE IF NOT EXISTS mnt_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT, role TEXT, action TEXT,
    entity_type TEXT, entity_id INTEGER,
    old_value TEXT, new_value TEXT, comment TEXT, ip TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_mnt_audit_entity ON mnt_audit(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS mnt_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT, entity_id INTEGER, kind TEXT,
    filename TEXT, original_name TEXT, content_type TEXT, size INTEGER,
    uploaded_by TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS mnt_settings (
    key TEXT PRIMARY KEY, value TEXT
);
"""


# Columns added after first release — applied as idempotent ALTERs on every boot
# so already-deployed databases pick them up (safe: each wrapped in try/except).
_MIGRATIONS = [
    ("mnt_spare_parts", "reserved_qty",
     "ALTER TABLE mnt_spare_parts ADD COLUMN reserved_qty REAL DEFAULT 0"),
    # Guards the reopen->close-again path: the machine's cost/downtime/breakdown
    # counters are rolled up from a ticket exactly once.
    ("mnt_tickets", "machine_rolled",
     "ALTER TABLE mnt_tickets ADD COLUMN machine_rolled INTEGER DEFAULT 0"),
    # --- Machine register import (app/maintenance/machine_import.py). Five ALTERs,
    # no index, no other boot work: gunicorn runs --preload, so everything here
    # happens BEFORE the port binds over a slow external PostgreSQL link.
    ("mnt_machines", "needle_system",
     "ALTER TABLE mnt_machines ADD COLUMN needle_system TEXT"),
    ("mnt_machines", "meter_reading",
     "ALTER TABLE mnt_machines ADD COLUMN meter_reading REAL"),
    ("mnt_machines", "meter_reading_at",
     "ALTER TABLE mnt_machines ADD COLUMN meter_reading_at TEXT"),
    # The FIRMA NO card slot. Its own field because it must stay searchable and it
    # is NOT the identity — one card carries several serials over its life.
    ("mnt_machines", "legacy_card_no",
     "ALTER TABLE mnt_machines ADD COLUMN legacy_card_no TEXT"),
    # Keeps the owner's "load everything" decision reversible: filter, don't drop.
    ("mnt_machines", "in_register_2023",
     "ALTER TABLE mnt_machines ADD COLUMN in_register_2023 INTEGER DEFAULT 0"),
]


def create_and_seed(conn):
    """Create maintenance tables, run column migrations, and seed sample data."""
    conn.executescript(SCHEMA)
    # Make the CREATE TABLEs durable BEFORE anything below can roll back. On
    # PostgreSQL executescript leaves them uncommitted, and the migration loop's
    # `except: conn.rollback()` rolls back the WHOLE open transaction, not just the
    # statement that failed — on a fresh database that would take the tables with
    # it. Costs one COMMIT and takes no lock.
    conn.commit()
    for _tbl, _col, _ddl in _MIGRATIONS:
        # PROBE BEFORE ALTER. On PostgreSQL, ALTER TABLE ... ADD COLUMN takes an
        # ACCESS EXCLUSIVE lock on the table BEFORE it discovers the column is
        # already there, so re-running a migration that has nothing left to do is
        # NOT free: it queues for the heaviest lock there is. gunicorn runs
        # --preload, so that queueing happens before the port binds, which is the
        # failure 4327e5c's lock_timeout='5s' was added to survive. A SELECT takes
        # only ACCESS SHARE and never waits on readers, so an already-migrated
        # database now issues ZERO ALTERs — the table name and column name in each
        # tuple are module constants, never user input.
        try:
            conn.execute(f"SELECT {_col} FROM {_tbl} LIMIT 1")
            continue
        except Exception:
            conn.rollback()   # PostgreSQL aborts the transaction on a failed query
        try:
            conn.execute(_ddl)
            conn.commit()
        except Exception:
            conn.rollback()
    # Settings that must exist on ALREADY-DEPLOYED databases too (the seed body
    # below is skipped once data exists). INSERT OR IGNORE keeps admin edits.
    try:
        conn.execute("INSERT OR IGNORE INTO mnt_settings (key,value) VALUES (?,?)",
                     ("auto_reorder_pr", "1"))
        conn.commit()
    except Exception:
        conn.rollback()
    # Workflow & Governance tables + default explanation text. Must run BEFORE the
    # "already seeded" bail-out below so deployed databases get them too.
    # INSERT OR IGNORE inside, so an admin's edited text is never overwritten.
    from app.maintenance.workflow import ensure as _wf_ensure
    _wf_ensure(conn)
    if conn.execute("SELECT COUNT(*) c FROM mnt_machines").fetchone()["c"] > 0:
        return  # already seeded; never overwrite

    now = _now()
    year = datetime.now(timezone.utc).year

    # --- Machines (5) ---
    machines = [
        ("M-001", "Single Needle Lockstitch", "Sewing Machine", "Juki", "DDL-9000",
         "SN-J9000-101", "Production", "Sewing Hall A", "L1", "Floor 1 - Bay 3",
         "critical", "running"),
        ("M-002", "Overlock 5-Thread", "Overlock", "Brother", "MA4-B551",
         "SN-B551-204", "Production", "Sewing Hall A", "L2", "Floor 1 - Bay 5",
         "high", "running"),
        ("M-003", "Cutting Machine Straight Knife", "Cutting", "Eastman", "629X",
         "SN-E629-009", "Cutting", "Cutting Room", "C1", "Floor 0 - Cutting",
         "critical", "stopped"),
        ("M-004", "Steam Press / Finishing", "Finishing", "Veit", "8363",
         "SN-V8363-077", "Finishing", "Finishing Hall", "F1", "Floor 2 - Finishing",
         "medium", "running"),
        ("M-005", "Embroidery 12-Head", "Embroidery", "Tajima", "TFMX-IIC1512",
         "SN-T1512-330", "Embroidery", "Embroidery Hall", "E1", "Floor 1 - Bay 9",
         "high", "under_maintenance"),
    ]
    mids = {}
    for code, name, mtype, brand, model, serial, dept, area, line, loc, crit, st in machines:
        cur = conn.execute(
            """INSERT INTO mnt_machines
               (code,name,type,brand,model,serial,department,area,line_no,location,
                criticality,status,qr_token,install_date,warranty_status,vendor,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, name, mtype, brand, model, serial, dept, area, line, loc, crit, st,
             "MQR" + code.replace("-", ""), "2023-02-15", "Out of warranty",
             brand + " Türkiye", now))
        mids[code] = cur.lastrowid

    # --- Spare parts (10) ---
    spares = [
        ("SP-001", "Needle DBx1 #11", "needle_textile", "pcs", 240, 100, 150, 600, 0.45, "high"),
        ("SP-002", "Rotary Hook (Juki DDL)", "mechanical", "pcs", 8, 4, 6, 20, 28.0, "critical"),
        ("SP-003", "Drive Belt M-type", "belt", "pcs", 14, 6, 10, 40, 6.5, "high"),
        ("SP-004", "Bobbin Case", "mechanical", "pcs", 35, 15, 20, 80, 4.2, "medium"),
        ("SP-005", "Straight Knife Blade 8in", "cutter_blade", "pcs", 3, 5, 8, 30, 12.0, "critical"),
        ("SP-006", "Servo Motor Control Board", "control_plc", "pcs", 2, 1, 2, 6, 145.0, "critical"),
        ("SP-007", "Proximity Sensor PNP", "sensor", "pcs", 11, 5, 8, 25, 9.8, "high"),
        ("SP-008", "Pneumatic Cylinder 32mm", "pneumatic", "pcs", 6, 3, 5, 15, 22.5, "medium"),
        ("SP-009", "Silicone Lubricant 1L", "lubrication", "l", 18, 8, 12, 40, 7.0, "low"),
        ("SP-010", "Looper Set (Overlock)", "mechanical", "set", 5, 3, 4, 12, 33.0, "high"),
    ]
    sids = {}
    for code, name, cat, uom, qty, mn, ro, mx, cost, crit in spares:
        cur = conn.execute(
            """INSERT INTO mnt_spare_parts
               (code,name,category,uom,stock_qty,min_level,reorder_level,max_level,
                avg_cost,last_price,criticality,warehouse,bin,qr_token,vendor,lead_time_days,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (code, name, cat, uom, qty, mn, ro, mx, cost, cost, crit,
             "Main Store", "A-" + code[-2:], "PQR" + code.replace("-", ""),
             "Textile Spares Co.", 7, now))
        sids[code] = cur.lastrowid
        # opening-balance movement
        conn.execute(
            """INSERT INTO mnt_stock_movements
               (movement_no,type,spare_id,qty,before_qty,after_qty,performed_by,notes,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (f"STK-{year}-{cur.lastrowid:06d}", "opening", cur.lastrowid, qty, 0, qty,
             "system", "Opening balance (seed)", now))

    # --- Compatibility ---
    compat = [("SP-001", "M-001"), ("SP-001", "M-002"), ("SP-002", "M-001"),
              ("SP-003", "M-001"), ("SP-003", "M-002"), ("SP-004", "M-001"),
              ("SP-005", "M-003"), ("SP-006", "M-005"), ("SP-007", "M-003"),
              ("SP-008", "M-004"), ("SP-010", "M-002")]
    for sp, mc in compat:
        conn.execute("INSERT INTO mnt_spare_compat (spare_id,machine_id) VALUES (?,?)",
                     (sids[sp], mids[mc]))

    # --- Approval matrix (1 default rule) ---
    conn.execute(
        """INSERT INTO mnt_approval_matrix
           (name,request_type,part_criticality,cost_threshold,levels,escalation_hours,active,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("Default spare issue", "spare_issue", None, 100.0,
         "maintenance_manager,storekeeper", 24, 1, now))
    conn.execute(
        """INSERT INTO mnt_approval_matrix
           (name,request_type,part_criticality,cost_threshold,levels,escalation_hours,active,created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        ("Critical/expensive spare issue", "spare_issue", "critical", 100.0,
         "maintenance_manager,factory_manager,storekeeper", 12, 1, now))

    # --- Settings ---
    for k, v in [("reopen_window_days", "7"), ("response_sla_hours", "4"),
                 ("resolution_sla_hours", "24"), ("currency", "TRY"),
                 # Maintenance -> Procurement bridge: auto-raise a PR when a spare
                 # falls to/below its reorder level ('1' on / '0' off).
                 ("auto_reorder_pr", "1")]:
        conn.execute("INSERT OR IGNORE INTO mnt_settings (key,value) VALUES (?,?)", (k, v))

    # --- Tickets (3) ---
    # 1) freshly submitted, machine stopped
    conn.execute(
        """INSERT INTO mnt_tickets
           (ticket_no,requester,department,area,line_no,machine_id,machine_code,
            issue_category,description,priority,severity,safety_impact,production_stopped,
            est_downtime_min,shift,status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"MNT-{year}-000001", "Production Supervisor", "Cutting", "Cutting Room", "C1",
         mids["M-003"], "M-003", "mechanical",
         "Straight knife blade is dull and tearing fabric; machine stopped.",
         "critical", "major", 1, 1, 120, "A", "submitted", now))
    # 2) in diagnosis
    t2 = conn.execute(
        """INSERT INTO mnt_tickets
           (ticket_no,work_order_no,requester,department,area,line_no,machine_id,machine_code,
            issue_category,description,priority,severity,production_stopped,status,
            assigned_to,created_at,reviewed_at,assigned_at,diag_started_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"MNT-{year}-000002", f"WO-{year}-000002", "Line Operator", "Production",
         "Sewing Hall A", "L1", mids["M-001"], "M-001", "mechanical",
         "Skipped stitches and abnormal noise from hook area.", "high", "moderate", 0,
         "diagnosis", "Technician A", now, now, now, now)).lastrowid
    conn.execute(
        """INSERT INTO mnt_diagnosis (ticket_id,technician,fault_found,root_cause,diagnosis,
           required_action,spare_needed,est_repair_min,created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (t2, "Technician A", "Worn rotary hook", "wear_tear",
         "Rotary hook shows wear causing skipped stitches.",
         "Replace rotary hook and re-time the machine.", 1, 90, now))
    # 3) closed (history)
    closed_at = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """INSERT INTO mnt_tickets
           (ticket_no,work_order_no,requester,department,machine_id,machine_code,issue_category,
            description,priority,severity,status,assigned_to,total_downtime_min,cost,
            created_at,closed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (f"MNT-{year}-000003", f"WO-{year}-000003", "Section Head", "Finishing",
         mids["M-004"], "M-004", "electrical",
         "Steam press heater not reaching temperature.", "medium", "minor", "closed",
         "Technician B", 75, 28.0, closed_at, closed_at))

    # --- PM plans (2) ---
    for mc, title, freq, days in [("M-001", "Monthly lubrication & hook check", "monthly", 30),
                                  ("M-005", "Quarterly head alignment", "quarterly", 90)]:
        nd = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
        pid = conn.execute(
            """INSERT INTO mnt_pm_plans (machine_id,title,frequency,interval_days,assigned_to,
               last_done,next_due,active,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (mids[mc], title, freq, days, "Technician A", now[:10], nd, 1, now)).lastrowid
        for i, item in enumerate(["Clean and lubricate moving parts", "Check belt tension",
                                  "Inspect hook / needle area", "Verify safety guards"]):
            conn.execute(
                "INSERT INTO mnt_pm_checklist (plan_id,item,required,photo_required,sort) VALUES (?,?,?,?,?)",
                (pid, item, 1, 1 if i == 2 else 0, i))

    conn.commit()
