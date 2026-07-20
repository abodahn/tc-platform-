"""
TC Platform — Procurement & Approvals database schema + seed.

All tables live alongside the platform metadata (same connection style as the
Maintenance module). Creation is idempotent (CREATE TABLE IF NOT EXISTS) and
seeding runs only when empty, so existing data is never overwritten.
"""
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


SCHEMA = """
-- ===== Vendors (supplier master) =====
CREATE TABLE IF NOT EXISTS proc_vendors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    contact_person TEXT, phone TEXT, email TEXT, address TEXT,
    payment_terms TEXT, category TEXT,
    rating REAL DEFAULT 0,
    notes TEXT, is_active INTEGER DEFAULT 1, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_proc_vendor_name ON proc_vendors(name);

-- ===== Purchase Requests (the digital PR form header) =====
CREATE TABLE IF NOT EXISTS pr_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_no TEXT UNIQUE,
    title TEXT,
    request_for TEXT,                 -- what/where the request is for (e.g. asset)
    requester TEXT,                   -- username of the originator
    requester_name TEXT,
    requester_user_id INTEGER,
    department TEXT,
    request_date TEXT,
    currency TEXT DEFAULT 'EGP',
    vendor TEXT, vendor_id INTEGER,
    payment_condition TEXT, delivery_condition TEXT,
    req_del_date TEXT,
    po_no TEXT,
    asset_code TEXT, asset_id INTEGER,
    total REAL DEFAULT 0,
    status TEXT DEFAULT 'draft',
    current_seq INTEGER DEFAULT 0,    -- seq of the active step (0 = none/draft)
    notes TEXT,
    rejection_reason TEXT,
    created_at TEXT, submitted_at TEXT, approved_at TEXT, closed_at TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_pr_no ON pr_requests(pr_no);
CREATE INDEX IF NOT EXISTS ix_pr_status ON pr_requests(status);
CREATE INDEX IF NOT EXISTS ix_pr_requester ON pr_requests(requester);

-- ===== PR line items =====
CREATE TABLE IF NOT EXISTS pr_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    seq INTEGER DEFAULT 1,
    item TEXT, description TEXT,
    unit TEXT DEFAULT 'Pcs',
    qty REAL DEFAULT 1,
    current_stock REAL DEFAULT 0,
    last_order_qty REAL, last_order_date TEXT,
    vendor TEXT,
    unit_price REAL DEFAULT 0,
    est_cost REAL DEFAULT 0,
    last_order_price REAL,
    notes TEXT,
    FOREIGN KEY (pr_id) REFERENCES pr_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_pr_items_pr ON pr_items(pr_id);

-- ===== Approval ladder steps (one row per rung) =====
CREATE TABLE IF NOT EXISTS pr_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    seq INTEGER,                      -- 1..N order in the ladder
    stage TEXT,                       -- warehouse|factory_manager|purchasing|finance|cfo|ceo
    status TEXT DEFAULT 'pending',    -- pending|approved|rejected|skipped
    approver_user TEXT, approver_name TEXT, approver_role TEXT,
    sig_png TEXT,                     -- data:image/png snapshot of the signature
    comment TEXT,
    acted_at TEXT, created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES pr_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_pr_steps_pr ON pr_steps(pr_id);
CREATE INDEX IF NOT EXISTS ix_pr_steps_stage ON pr_steps(stage);

-- ===== Immutable audit / event trail =====
CREATE TABLE IF NOT EXISTS pr_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    actor TEXT, action TEXT, detail TEXT, ip TEXT,
    created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES pr_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_pr_events_pr ON pr_events(pr_id);

-- ===== Vendor invoices (for 3-way matching) =====
CREATE TABLE IF NOT EXISTS pr_invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    invoice_no TEXT, invoice_date TEXT,
    amount REAL DEFAULT 0, tax REAL DEFAULT 0, currency TEXT DEFAULT 'EGP',
    status TEXT DEFAULT 'received',      -- received | matched | disputed | paid
    match_json TEXT,                     -- cached 3-way-match result
    filename TEXT, content_type TEXT, content_b64 TEXT,
    notes TEXT, created_by TEXT, created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES pr_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_pr_inv_pr ON pr_invoices(pr_id);

-- ===== Payments against a PR / invoice =====
CREATE TABLE IF NOT EXISTS pr_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER, invoice_id INTEGER,
    amount REAL DEFAULT 0, currency TEXT DEFAULT 'EGP',
    method TEXT, reference TEXT, paid_at TEXT,
    notes TEXT, created_by TEXT, created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES pr_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_pr_pay_pr ON pr_payments(pr_id);

-- ===== Attachments (vendor quotations etc.) =====
CREATE TABLE IF NOT EXISTS pr_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    filename TEXT, content_type TEXT, size INTEGER DEFAULT 0,
    content_b64 TEXT,
    uploaded_by TEXT, created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES pr_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_pr_attach_pr ON pr_attachments(pr_id);

-- ===== Competing vendor quotes (multi-quote comparison) =====
CREATE TABLE IF NOT EXISTS pr_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    vendor TEXT, vendor_id INTEGER,
    amount REAL DEFAULT 0, currency TEXT DEFAULT 'EGP',
    lead_time_days INTEGER, warranty TEXT,
    filename TEXT, content_type TEXT, content_b64 TEXT,
    is_chosen INTEGER DEFAULT 0, notes TEXT,
    uploaded_by TEXT, created_at TEXT,
    FOREIGN KEY (pr_id) REFERENCES pr_requests(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_pr_quotes_pr ON pr_quotes(pr_id);

-- ===== Department budgets (per year) =====
CREATE TABLE IF NOT EXISTS proc_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department TEXT, period TEXT,          -- 'YYYY'
    currency TEXT DEFAULT 'EGP', amount REAL DEFAULT 0,
    notes TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_proc_budget ON proc_budgets(department, period);

-- ===== Approval-authority delegations =====
CREATE TABLE IF NOT EXISTS proc_delegations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user TEXT, to_user TEXT,
    from_date TEXT, to_date TEXT, note TEXT,
    is_active INTEGER DEFAULT 1, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_proc_deleg_to ON proc_delegations(to_user);

-- ===== Responsibility (approval) matrix, per department =====
-- One row per (department, stage). A department with rows defines its own
-- ladder (which stages, in what order, above what amount). A department with
-- NO rows falls back to the global default (constants.APPROVAL_MATRIX).
CREATE TABLE IF NOT EXISTS proc_resp_matrix (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    department TEXT NOT NULL,
    stage TEXT NOT NULL,          -- warehouse|factory_manager|purchasing|finance|cfo|ceo
    threshold REAL DEFAULT 0,     -- stage joins the ladder when total >= threshold
    seq INTEGER DEFAULT 0,        -- order within the ladder
    active INTEGER DEFAULT 1,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_resp_dept ON proc_resp_matrix(department);
"""

# Columns added to pr_steps after first release — applied as idempotent ALTERs
# on every boot so already-deployed databases pick them up.
_STEP_MIGRATIONS = [
    ("activated_at", "ALTER TABLE pr_steps ADD COLUMN activated_at TEXT"),
    ("escalated", "ALTER TABLE pr_steps ADD COLUMN escalated INTEGER DEFAULT 0"),
    ("escalated_at", "ALTER TABLE pr_steps ADD COLUMN escalated_at TEXT"),
]

# Columns added to pr_requests after first release (tax + goods receipt + PO email
# + payment tracking).
_PR_MIGRATIONS = [
    ("tax_rate", "ALTER TABLE pr_requests ADD COLUMN tax_rate REAL DEFAULT 0"),
    ("received_at", "ALTER TABLE pr_requests ADD COLUMN received_at TEXT"),
    ("received_by", "ALTER TABLE pr_requests ADD COLUMN received_by TEXT"),
    ("receipt_notes", "ALTER TABLE pr_requests ADD COLUMN receipt_notes TEXT"),
    ("po_sent_at", "ALTER TABLE pr_requests ADD COLUMN po_sent_at TEXT"),
    ("paid_amount", "ALTER TABLE pr_requests ADD COLUMN paid_amount REAL DEFAULT 0"),
    ("payment_status", "ALTER TABLE pr_requests ADD COLUMN payment_status TEXT DEFAULT 'unpaid'"),
    ("due_date", "ALTER TABLE pr_requests ADD COLUMN due_date TEXT"),
    # Pricing gate: existing rows default to 'priced' (they already carry a value,
    # so behaviour is unchanged); new requester-raised PRs are set 'unpriced' until
    # Purchasing prices them. priced_at/priced_by record who entered the pricing.
    ("pricing_status", "ALTER TABLE pr_requests ADD COLUMN pricing_status TEXT DEFAULT 'priced'"),
    ("priced_at", "ALTER TABLE pr_requests ADD COLUMN priced_at TEXT"),
    ("priced_by", "ALTER TABLE pr_requests ADD COLUMN priced_by TEXT"),
    # Cross-module bridge: where a PR was raised from (e.g. maintenance spare
    # auto-reorder -> source_module='maintenance', source_ref='spare:<id>').
    # Lets goods receipts post back into the source system's stock.
    ("source_module", "ALTER TABLE pr_requests ADD COLUMN source_module TEXT"),
    ("source_ref", "ALTER TABLE pr_requests ADD COLUMN source_ref TEXT"),
]

# Verifiable signature events: one immutable row per approve/reject signature.
# `code` is the public verification handle printed on the PR/PO PDF; `doc_hash`
# anchors what was signed (PR no, stage, decision, signer, amount, timestamp).
_SIGN_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS pr_sign_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    pr_id INTEGER,
    seq INTEGER,
    stage TEXT,
    action TEXT,
    signer TEXT,
    signer_name TEXT,
    doc_hash TEXT,
    ip TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_sign_events_pr ON pr_sign_events(pr_id);
"""

# Columns added to pr_items after first release (line-level receiving).
_ITEM_MIGRATIONS = [
    ("received_qty", "ALTER TABLE pr_items ADD COLUMN received_qty REAL DEFAULT 0"),
]


def create_and_seed(conn):
    """Create procurement tables, run column migrations, and seed sample data."""
    conn.executescript(SCHEMA)
    conn.executescript(_SIGN_EVENTS_DDL)
    conn.commit()
    # Idempotent column migrations (safe on already-deployed databases).
    for _col, _ddl in _STEP_MIGRATIONS + _PR_MIGRATIONS + _ITEM_MIGRATIONS:
        try:
            conn.execute(_ddl)
            conn.commit()
        except Exception:
            conn.rollback()
    if conn.execute("SELECT COUNT(*) c FROM proc_vendors").fetchone()["c"] > 0:
        return  # already seeded; never overwrite

    now = _now()

    # --- Vendors (incl. the real one from PR 13849) ---
    vendors = [
        ("High Trak for Trading", "High Trak", "01220595184", "Hightrak6@gmail.com",
         "El Obour, 1st District, Property 43, Apt 8", "On Delivery", "maintenance", 4.5),
        ("Jungheinrich Egypt", "Sales", "0227000000", "info@jungheinrich.com.eg",
         "Cairo", "Advanced Payment", "equipment", 4.7),
        ("Delta Industrial Supplies", "Procurement", "01000000000", "sales@delta-ind.com",
         "10th of Ramadan", "Net 30", "general", 4.0),
    ]
    for name, cp, phone, email, addr, terms, cat, rating in vendors:
        conn.execute(
            """INSERT INTO proc_vendors
               (name, contact_person, phone, email, address, payment_terms, category, rating, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, cp, phone, email, addr, terms, cat, rating, now))

    # --- One demo PR mirroring 13849 (forklift battery), pending at warehouse ---
    from app.approvals.constants import build_ladder, stage_label
    total = 48000.0
    cur = conn.execute(
        """INSERT INTO pr_requests
           (pr_no, title, request_for, requester, requester_name, department,
            request_date, currency, vendor, payment_condition, delivery_condition,
            total, status, current_seq, notes, created_at, submitted_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("PR-DEMO-13849", "Repair forklift battery", "JUNGHEINRICH forklift (Kunas Depo)",
         "store", "Storekeeper", "General Maintenance", now[:10], "EGP",
         "High Trak for Trading", "Advanced Payment", "T&C Warehouse",
         total, "pending", 1, "General maintenance order — battery repair/refurbish.",
         now, now))
    pr_id = cur.lastrowid
    conn.execute(
        """INSERT INTO pr_items
           (pr_id, seq, item, description, unit, qty, current_stock, vendor,
            unit_price, est_cost, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (pr_id, 1, "Repair Battery", "JUNGHEINRICH 2750KG 48V 750A — change 4 cells, "
         "repair 4 cells, insulation, remove deposits, change acid, reactivate.",
         "Pcs", 1, 0, "High Trak for Trading", 48000.0, 48000.0, "6-month warranty"))
    ladder = build_ladder(total)
    for i, stage in enumerate(ladder, start=1):
        conn.execute(
            """INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, activated_at, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (pr_id, i, stage, "pending", stage_label(stage), now if i == 1 else None, now))
    conn.execute(
        "INSERT INTO pr_events (pr_id, actor, action, detail, created_at) VALUES (?,?,?,?,?)",
        (pr_id, "store", "submitted", f"Submitted for {len(ladder)} approvals", now))

    # --- Department budgets for the current year ---
    year = str(datetime.now(timezone.utc).year)
    for dept, amount in [("General Maintenance", 1_500_000), ("Production", 3_000_000),
                         ("IT", 800_000), ("Administration", 600_000)]:
        conn.execute(
            "INSERT INTO proc_budgets (department, period, currency, amount, created_at) "
            "VALUES (?,?,?,?,?)", (dept, year, "EGP", amount, now))
    conn.commit()
