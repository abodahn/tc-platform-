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

-- ===== Workflow & Governance (admin-configurable, additive) =====
-- Every table here is an OVERRIDE store: an absent row means "use the constant
-- in constants.py". A database with no rows behaves exactly as the code always
-- has, which is why nothing about the ladder/gates changes on upgrade.
--
-- Each carries a surrogate `id` even though the natural key is unique, because
-- app/db.py appends "RETURNING id" to every INSERT for tables outside its own
-- allow-list — a bare TEXT-primary-key table would fail on PostgreSQL.

-- Module-wide knobs (rfq_quote_min, rfq_value_threshold, sod_admin_exempt,
-- payment_tolerance_pct). Deliberately NOT seeded: absent == use the constant.
CREATE TABLE IF NOT EXISTS proc_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    value TEXT,
    updated_by TEXT, updated_at TEXT
);

-- Per-stage role override + explanation. role NULL/blank = use STAGE_ROLES.
-- role may hold a comma-separated list ("storekeeper,warehouse_manager").
CREATE TABLE IF NOT EXISTS proc_stage_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT UNIQUE NOT NULL,
    role TEXT,
    explanation TEXT,
    updated_by TEXT, updated_at TEXT
);

-- What each procurement role is responsible for.
CREATE TABLE IF NOT EXISTS proc_role_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key TEXT UNIQUE NOT NULL,
    explanation TEXT,
    updated_by TEXT, updated_at TEXT
);

-- Escalation chain: who signs one level up when the ONLY person eligible for a
-- rung is the requester (nobody ever approves their own request). Seeded once
-- with constants.DEFAULT_ESCALATION via INSERT OR IGNORE, so an owner edit is
-- never overwritten on a later boot. A role with no row, or a blank
-- superior_role, means "nobody above".
CREATE TABLE IF NOT EXISTS proc_escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key TEXT UNIQUE NOT NULL,
    superior_role TEXT,
    updated_by TEXT, updated_at TEXT
);

-- Free-text blocks: 'overview', one per gate, and 'status.<pr_status>'.
CREATE TABLE IF NOT EXISTS proc_doc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT UNIQUE NOT NULL,
    body TEXT,
    updated_by TEXT, updated_at TEXT
);

-- ===== Procurement item catalogue (the ERP item master) =====
-- Shipped EMPTY and NEVER seeded: gunicorn runs --preload, so create_and_seed
-- happens before the port binds over a slow external PostgreSQL link. The
-- 19k rows arrive later, once, through the admin upload screen or the CLI.
CREATE TABLE IF NOT EXISTS proc_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,        -- opaque key from the ERP; never parsed
    name TEXT,
    unit TEXT,
    category_code TEXT, category_name TEXT,
    cost_price REAL DEFAULT 0,
    has_cost INTEGER DEFAULT 0,       -- 0 = no price on file (never render 0.00)
    source TEXT,
    active INTEGER DEFAULT 1,
    updated_by TEXT, updated_at TEXT
);
-- ONE index. A second one on (name) was measured and dropped: every search
-- matches the name with a LEADING wildcard (`%q%`), which no b-tree can serve,
-- so EXPLAIN never chose it — it only cost write time on a 19k import. This one
-- carries category_name as a third column purely so the category list the PR
-- form loads on every render is an index-ONLY scan (29 ms -> 13 ms).
CREATE INDEX IF NOT EXISTS ix_proc_items_cat
    ON proc_items(active, category_code, category_name);

-- ===== New-item requests — the ONE door into the catalogue from the floor ====
-- Without this the master decays: an item nobody catalogued was typed as free
-- text and never came back. The requester asks; PURCHASING approve and type the
-- ERP code (nothing here generates one). NEVER seeded, and no price column of
-- any kind: a requester states WHAT they need, and the commercial value enters
-- exactly once, at the Purchasing pricing gate.
CREATE TABLE IF NOT EXISTS proc_item_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER, line_no INTEGER,   -- SOFT links: the PR is usually still
                                      -- being typed, so both are normally NULL,
                                      -- and a deleted PR leaves no broken row
    name TEXT NOT NULL,
    unit TEXT, category_code TEXT, category_name TEXT,
    reason TEXT,
    requested_by TEXT, requested_at TEXT,
    status TEXT DEFAULT 'pending',    -- pending | approved | rejected
    decided_by TEXT, decided_at TEXT, decision_note TEXT,
    assigned_code TEXT,               -- the ERP code the approver typed
    item_id INTEGER                   -- proc_items.id created on approval
);
CREATE INDEX IF NOT EXISTS ix_proc_item_req ON proc_item_requests(status, id);
"""

# Columns added to pr_steps after first release — applied as idempotent ALTERs
# on every boot so already-deployed databases pick them up.
_STEP_MIGRATIONS = [
    ("activated_at", "ALTER TABLE pr_steps ADD COLUMN activated_at TEXT"),
    ("escalated", "ALTER TABLE pr_steps ADD COLUMN escalated INTEGER DEFAULT 0"),
    ("escalated_at", "ALTER TABLE pr_steps ADD COLUMN escalated_at TEXT"),
    # Level-2 escalation stamp (step older than 2x the stage SLA): set once by
    # run_escalations, doubles as its own dedupe flag like escalated_at above.
    ("escalated2_at", "ALTER TABLE pr_steps ADD COLUMN escalated2_at TEXT"),
    # SoD escalation (a DIFFERENT thing from the SLA escalation above, which is
    # about a stage sitting too long): the requester was the only person eligible
    # for this rung, so it was re-pointed one level up the org chart at
    # ladder-build time. esc_role = the role(s) that sign it now (blank string =
    # the climb found nobody, i.e. it needs a delegation or an admin);
    # esc_from = the role(s) it was escalated FROM. Both NULL = a normal rung.
    ("esc_role", "ALTER TABLE pr_steps ADD COLUMN esc_role TEXT"),
    ("esc_from", "ALTER TABLE pr_steps ADD COLUMN esc_from TEXT"),
    # WHY this rung exists. Without it the post-pricing reconcile cannot tell a
    # value-derived rung from a control rung, so re-saving pricing deleted the
    # §4.3 single-source and §4.4 deviation escalations with no trace.
    ("origin", "ALTER TABLE pr_steps ADD COLUMN origin TEXT DEFAULT 'ladder'"),
    # DOAM Table 5 — WHAT the signature on this rung is: review | approve |
    # endorse. Until this column existed a rung had two outcomes (approve /
    # reject) and the RACI letters the DOAM assigns per activity could not be
    # evidenced from system records at all. Defaults to 'approve', so every row
    # written before this migration keeps exactly the meaning it was given.
    ("action_type", "ALTER TABLE pr_steps ADD COLUMN action_type TEXT DEFAULT 'approve'"),
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
    # RFQ control (P3): Purchasing's recorded justification for waiving the
    # competitive-quote rule on a high-value PR (single/sole-source purchase).
    ("single_source_reason", "ALTER TABLE pr_requests ADD COLUMN single_source_reason TEXT"),
    # DOAM §6 — the Engineering Justification Report (T&C-PUF-09) this requisition
    # carries. NULL is normal: most requests are not spares/MRO and need none.
    # app.maintenance.eng_justification.ejr_gate_check decides when it is required.
    ("ejr_id", "ALTER TABLE pr_requests ADD COLUMN ejr_id INTEGER"),
    # DOAM §4.2 — operating vs capital expenditure. NULL/blank reads as 'opex',
    # which is what an unmarked request is; the CAPEX ladder is a different set
    # of signatures for the same money, so this column decides which one applies.
    ("expenditure_kind",
     "ALTER TABLE pr_requests ADD COLUMN expenditure_kind TEXT DEFAULT 'opex'"),
    # DOAM §3.4 — the 30-day aggregate this request was actually routed on, when
    # related requests existed. NULL means it routed on its own value.
    ("agg_total", "ALTER TABLE pr_requests ADD COLUMN agg_total REAL"),
    # DOAM §4.3 — advance-payment authorisation, recorded on the request.
    ("advance_pct", "ALTER TABLE pr_requests ADD COLUMN advance_pct REAL"),
    ("advance_auth_by", "ALTER TABLE pr_requests ADD COLUMN advance_auth_by TEXT"),
    ("advance_auth_stage", "ALTER TABLE pr_requests ADD COLUMN advance_auth_stage TEXT"),
    ("advance_auth_at", "ALTER TABLE pr_requests ADD COLUMN advance_auth_at TEXT"),
    ("bank_guarantee_ref", "ALTER TABLE pr_requests ADD COLUMN bank_guarantee_ref TEXT"),
    # DOAM §5 — the golden thread: the controlling cost object. `asset_code`
    # already carries the asset, so only the sales order and the cost centre are
    # new. Every downstream document (PO, GRN, invoice) is keyed to the PR, so
    # carrying it here carries it along the whole chain.
    ("so_no", "ALTER TABLE pr_requests ADD COLUMN so_no TEXT"),
    ("cost_center", "ALTER TABLE pr_requests ADD COLUMN cost_center TEXT"),
    # Foreign-currency support: fx_rate converts the PR total (in `currency`)
    # to EGP so the EGP-based approval thresholds route honestly. po_rev counts
    # PO revisions (0 = original order; history kept in pr_po_revisions).
    ("fx_rate", "ALTER TABLE pr_requests ADD COLUMN fx_rate REAL DEFAULT 1"),
    ("po_rev", "ALTER TABLE pr_requests ADD COLUMN po_rev INTEGER DEFAULT 0"),
    # DOAM Table 4 — the budget verdict this request was ROUTED on, and how far
    # past the budget it took the department. 'budgeted' | 'no_budget' |
    # 'over_budget'; NULL on rows that predate the check (they routed on the
    # §4.1 budgeted ladder regardless, which is what NULL should read as).
    ("budget_state", "ALTER TABLE pr_requests ADD COLUMN budget_state TEXT"),
    ("budget_over_by", "ALTER TABLE pr_requests ADD COLUMN budget_over_by REAL"),
    # Records retention (audit 3.4-b9b). The date this record may FIRST be
    # disposed of, stamped when it is created from constants.retention_until():
    # ten years for capital expenditure, five for everything else. Existing rows
    # are backfilled from created_at just below, so an already-deployed database
    # is not left with a column of NULLs that no rule can act on.
    ("retention_until", "ALTER TABLE pr_requests ADD COLUMN retention_until TEXT"),
    # DOAM §4.1 tier 7 / §4.2 tier 4 — the written business case a request above
    # BUSINESS_CASE_OVER must carry. NULL is normal; almost nothing is that big.
    ("business_case", "ALTER TABLE pr_requests ADD COLUMN business_case TEXT"),
    # DOAM §4.4 / T&C-PUF-12 — the justification memo an off-plan order must
    # carry (over the stock ceiling, or priced over target). NULL is normal.
    ("deviation_memo", "ALTER TABLE pr_requests ADD COLUMN deviation_memo TEXT"),
    # DOAM 3.4 — the OTHER cost object the clause allows: an agreed production
    # forecast, cited instead of a client sales order. Sits beside so_no and
    # travels the same golden thread onto the PO, the GRN and the invoice.
    ("forecast_ref", "ALTER TABLE pr_requests ADD COLUMN forecast_ref TEXT"),
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

# PO revision history: one immutable row per revision. `snapshot_json` freezes
# the order lines + total + PO number as they stood when the revision was
# opened, so what changed between Rev N-1 and Rev N is always reconstructable.
_PO_REV_DDL = """
CREATE TABLE IF NOT EXISTS pr_po_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    rev_no INTEGER,
    reason TEXT,
    po_no TEXT,
    snapshot_json TEXT,
    created_by TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_po_rev_pr ON pr_po_revisions(pr_id);
"""

# Goods Received Notes. A GRN is a CONTROLLED, PRE-NUMBERED document: one row per
# receipt EVENT, its number allocated when the goods are booked in — not derived at
# print time, which reprinted the same number for every partial delivery.
# pr_grn_quarantine holds what a supplier delivered OVER the ordered quantity: the
# excess is never added to stock, it is parked here with an explicit state until
# someone decides to accept or return it.
_GRN_DDL = """
CREATE TABLE IF NOT EXISTS pr_grn (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grn_no TEXT UNIQUE,
    pr_id INTEGER,
    seq INTEGER,
    lines_json TEXT,
    accepted_qty REAL DEFAULT 0,
    quarantined_qty REAL DEFAULT 0,
    notes TEXT,
    received_by TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_grn_pr ON pr_grn(pr_id);
CREATE TABLE IF NOT EXISTS pr_grn_quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    grn_id INTEGER,
    item_id INTEGER,
    item TEXT,
    ordered_qty REAL,
    accepted_qty REAL,
    qty REAL,
    status TEXT DEFAULT 'quarantined',
    resolution TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    created_by TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_grn_quar_pr ON pr_grn_quarantine(pr_id);

-- Return to vendor + its debit note. ONE row is both documents, because they are
-- one event: goods rejected on receipt go back to the supplier, and the debit
-- note is that return's financial face. `dn_no` is allocated from the row id the
-- same way a GRN number is, so every return carries its own document number.
-- status 'open' = the supplier still owes it back; that open value is what the
-- vendor's outstanding dues are, and what the payable ceiling drops by.
CREATE TABLE IF NOT EXISTS pr_returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dn_no TEXT UNIQUE,
    pr_id INTEGER,
    grn_id INTEGER,
    vendor TEXT, vendor_id INTEGER,
    lines_json TEXT,
    qty REAL DEFAULT 0,
    net REAL DEFAULT 0, tax REAL DEFAULT 0, total REAL DEFAULT 0,
    currency TEXT DEFAULT 'EGP',
    reason TEXT,
    status TEXT DEFAULT 'open',       -- open | settled
    settled_by TEXT, settled_at TEXT,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_pr_returns_pr ON pr_returns(pr_id);
CREATE INDEX IF NOT EXISTS ix_pr_returns_vendor ON pr_returns(vendor, status);

-- ===== Purchase orders — ONE DOCUMENT PER VENDOR =====
-- A requisition may buy from several suppliers (pr_items.vendor). Each supplier
-- gets its OWN order: a document carrying only that supplier's lines, its own
-- totals and its own number, because a PO is sent to the supplier and must never
-- show them a competitor's prices.
-- pr_requests.po_no is NOT replaced: it keeps the FIRST (primary) order's number,
-- so every existing screen, PDF link, report and the maintenance bridge resolve
-- unchanged, and a request issued before this table existed is read through the
-- fallback in services._pos_for() rather than migrated.
CREATE TABLE IF NOT EXISTS pr_purchase_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_id INTEGER,
    po_no TEXT,
    vendor TEXT,
    currency TEXT DEFAULT 'EGP',
    subtotal REAL DEFAULT 0, tax REAL DEFAULT 0, grand REAL DEFAULT 0,
    rev INTEGER DEFAULT 0,
    status TEXT DEFAULT 'issued',     -- issued (the only state today)
    issued_at TEXT, issued_by TEXT
);
CREATE INDEX IF NOT EXISTS ix_pr_po_pr ON pr_purchase_orders(pr_id);
CREATE INDEX IF NOT EXISTS ix_pr_po_no ON pr_purchase_orders(po_no);

-- ===== Agreed production forecasts (DOAM 3.4 sales-order gate, second half) =====
-- "No direct production material may be requisitioned without a valid client
-- sales order reference OR AGREED FORECAST." The sales-order half is validated
-- against ord_orders; this table is the other half, and it is a REGISTER, not a
-- free-text box: a forecast only satisfies the gate once someone with the DOAM
-- authority for planning (Table 4 L2, SC-D) has agreed it, and only while it is
-- inside its validity dates. status: draft -> agreed.
CREATE TABLE IF NOT EXISTS proc_forecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT UNIQUE NOT NULL,
    description TEXT,
    period TEXT,                      -- the production period it covers, e.g. "2026 Q1"
    valid_from TEXT, valid_to TEXT,   -- YYYY-MM-DD; the gate checks today between them
    status TEXT DEFAULT 'draft',      -- draft | agreed
    agreed_by TEXT, agreed_by_name TEXT, agreed_role TEXT, agreed_at TEXT,
    created_by TEXT, created_at TEXT
);
"""

# Trilingual prose (added after first release). The English column stays exactly
# as it is — it is the source of truth and the fallback; Arabic and Turkish sit
# beside it, NULL until seeded/edited. Same guarded-ALTER pattern as above.
_PROSE_MIGRATIONS = [
    ("explanation_ar", "ALTER TABLE proc_stage_meta ADD COLUMN explanation_ar TEXT"),
    ("explanation_tr", "ALTER TABLE proc_stage_meta ADD COLUMN explanation_tr TEXT"),
    ("explanation_ar", "ALTER TABLE proc_role_meta ADD COLUMN explanation_ar TEXT"),
    ("explanation_tr", "ALTER TABLE proc_role_meta ADD COLUMN explanation_tr TEXT"),
    ("body_ar", "ALTER TABLE proc_doc ADD COLUMN body_ar TEXT"),
    ("body_tr", "ALTER TABLE proc_doc ADD COLUMN body_tr TEXT"),
]

# ===== Requests for Quotation — ONE DOCUMENT PER VENDOR, sent BEFORE a price ==
# The PR says what is needed, the PO commits a supplier; this is the document in
# between, and it is what PRODUCES the competitive quotations DOAM §4.3 requires.
# It carries the goods and the quantities and NO money at all: the supplier
# writes the prices in and sends it back, and that answer is recorded as a
# pr_quotes row pointing at the RFQ it answers (pr_quotes.rfq_id below).
# Its own number from doc_no(), so it is a controlled document like the PR, the
# PO and the GRN. Issuing one commits NOTHING — no status, total or ladder on
# pr_requests is touched by it.
_RFQ_DDL = """
CREATE TABLE IF NOT EXISTS pr_rfqs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rfq_no TEXT UNIQUE,
    pr_id INTEGER,
    vendor TEXT,
    reply_by TEXT,
    status TEXT DEFAULT 'sent',       -- sent | answered
    sent_at TEXT,
    created_by TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_pr_rfqs_pr ON pr_rfqs(pr_id);
"""

# Columns added to pr_quotes after first release: the RFQ a returned quotation
# answers. NULL is normal and stays legal — a quote handed over without an RFQ
# (or recorded before this column existed) is still a quote.
_QUOTE_MIGRATIONS = [
    ("rfq_id", "ALTER TABLE pr_quotes ADD COLUMN rfq_id INTEGER"),
]

# Columns added to pr_invoices / pr_payments after first release: WHICH purchase
# order (pr_purchase_orders.id) the money belongs to. Before a requisition could
# name a supplier per line, one requisition was one supplier and the request
# itself was the answer; once it can buy from three, an invoice with no order on
# it can be billed and paid up to the combined value of the other two suppliers'
# orders. NULL stays legal and means "the request as a whole" — every invoice and
# payment recorded before this column existed, and every single-supplier request,
# which is bounded by the request's own total exactly as it always was.
_MONEY_MIGRATIONS = [
    ("po_id", "ALTER TABLE pr_invoices ADD COLUMN po_id INTEGER"),
    ("po_id", "ALTER TABLE pr_payments ADD COLUMN po_id INTEGER"),
]

# Columns added to pr_items after first release (line-level receiving).
_ITEM_MIGRATIONS = [
    ("received_qty", "ALTER TABLE pr_items ADD COLUMN received_qty REAL DEFAULT 0"),
    # Cross-module mesh: a PR line may reference a real maintenance spare part
    # (mnt_spare_parts.id). Picking one auto-fills live stock on the form, and
    # the goods receipt posts the received quantity straight into that spare's
    # stock at the line's unit price — for ANY PR, not only bridge auto-PRs.
    ("spare_id", "ALTER TABLE pr_items ADD COLUMN spare_id INTEGER"),
    # Catalogue mesh: a PR line may reference a proc_items row. OPTIONAL — a
    # free-text line leaves it NULL and behaves exactly as it always has.
    ("item_id", "ALTER TABLE pr_items ADD COLUMN item_id INTEGER"),
]


def seed_governance(conn):
    """Seed the default workflow explanation text (stages, roles, gate docs and
    status meanings) in all three languages. INSERT OR IGNORE for English, so an
    admin's edited text is never overwritten by a later boot, and a row the admin
    RESET (deleted) is restored to the code default. Returns the number of rows
    the seed set covers.

    Runs on EVERY boot — before the sample-data guard below — so an already
    deployed database picks the text up without being re-seeded from scratch."""
    from app.approvals.constants import (STAGE_EXPLAIN, ROLE_EXPLAIN,
                                         DOC_SECTIONS, STATUS_MEANING,
                                         DEFAULT_ESCALATION)
    n = 0
    # Escalation chain: seeded ONCE, then ONE statement per boot instead of 8
    # no-op INSERTs — gunicorn runs --preload, so every boot statement is a
    # round trip on a slow external PostgreSQL link. Rows are never deleted (an
    # owner expresses "nobody above" as a BLANK superior, not a missing row), so
    # a non-empty table means the seed has run and no owner edit is restored.
    # Guarded + rollback: a failed statement aborts the whole PostgreSQL
    # transaction, which would take the rest of the seed down with it.
    try:
        if not conn.execute("SELECT 1 FROM proc_escalations LIMIT 1").fetchone():
            for role, superior in DEFAULT_ESCALATION.items():
                conn.execute("INSERT OR IGNORE INTO proc_escalations "
                             "(role_key, superior_role) VALUES (?,?)", (role, superior))
                n += 1
    except Exception:
        conn.rollback()
    for stage, text in STAGE_EXPLAIN.items():
        conn.execute("INSERT OR IGNORE INTO proc_stage_meta (stage, explanation) VALUES (?,?)",
                     (stage, text))
        n += 1
    for role, text in ROLE_EXPLAIN.items():
        conn.execute("INSERT OR IGNORE INTO proc_role_meta (role_key, explanation) VALUES (?,?)",
                     (role, text))
        n += 1
    docs = dict(DOC_SECTIONS)
    docs.update({f"status.{k}": v for k, v in STATUS_MEANING.items()})
    for section, body in docs.items():
        conn.execute("INSERT OR IGNORE INTO proc_doc (section, body) VALUES (?,?)",
                     (section, body))
        n += 1
    seed_translations(conn)
    conn.commit()
    return n


def seed_translations(conn):
    """Fill the Arabic/Turkish prose columns from app/approvals/i18n_text.py.

    Only ever fills a column that is NULL — an admin's translated text is left
    exactly as it is, on this boot and every later one. Returns the number of
    columns actually written.

    NULL and '' are deliberately different: NULL means "never translated" (a
    database deployed before these columns existed, or a freshly inserted row)
    and the seed may fill it; '' means an admin emptied that box on purpose so
    his readers fall back to the English, which is exactly what the editor hint
    promises. Seeding a blank would give him the shipped translation back on the
    next Render restart."""
    from app.approvals import i18n_text as T
    filled = 0
    for table, keycol, col, defaults in (
            ("proc_stage_meta", "stage", "explanation", T.STAGE),
            ("proc_role_meta", "role_key", "explanation", T.ROLE),
            ("proc_doc", "section", "body", T.DOC)):
        for lang in ("ar", "tr"):
            for key, text in defaults.get(lang, {}).items():
                try:
                    cur = conn.execute(
                        f"UPDATE {table} SET {col}_{lang}=? WHERE {keycol}=? "
                        f"AND {col}_{lang} IS NULL", (text, key))
                    filled += cur.rowcount if (cur.rowcount or 0) > 0 else 0
                except Exception:
                    conn.rollback()      # column not there yet -> English only
                    return filled
    conn.commit()
    return filled


def backfill_retention(conn):
    """Stamp retention_until on rows created before the column existed.

    ADDITIVE and idempotent: it only ever fills NULLs, never rewrites a date that
    is already there. Without it the retention control would be true for new
    requests and silently absent for the whole existing register, which is the
    half of a records rule that gets an audit finding written.

    Returns the number of rows stamped.

    ponytail: one UPDATE per legacy row, because "created_at + N years" has no
    portable spelling across SQLite and PostgreSQL and the year-boundary maths
    lives in Python. It runs ONCE — every later boot's SELECT matches nothing
    and costs a single query. If a register ever grows large enough for that
    first pass to matter, replace it with a dialect-branched single UPDATE.
    """
    from app.approvals.constants import retention_until
    try:
        rows = conn.execute(
            "SELECT id, created_at, request_date, expenditure_kind FROM pr_requests "
            "WHERE retention_until IS NULL OR TRIM(retention_until) = ''").fetchall()
    except Exception:
        conn.rollback()
        return 0                     # column not there yet -> nothing to backfill
    n = 0
    for r in rows:
        born = r["created_at"] or r["request_date"] or _now()
        conn.execute("UPDATE pr_requests SET retention_until=? WHERE id=?",
                     (retention_until(born, r["expenditure_kind"]), r["id"]))
        n += 1
    if n:
        conn.commit()
    return n


def drop_legacy_doam_role_rows(conn):
    """Delete the rows the removed seed_doam_roles() left behind.

    The DOAM authority roles are registered in code now. security.load_db_roles()
    lets a custom_roles row REPLACE the code definition, so those five leftover
    rows SHADOW it: on any database the old seed reached, financial_director has
    the seed's five permissions and never the proc_pay the code grants it — the
    drift the seed was removed to stop, frozen into the environments that matter.

    Only rows still carrying the seed's exact permission list are deleted; an
    admin who has since edited one keeps their version. Idempotent: after the
    first boot the DELETE matches nothing.
    """
    seeded = '["open_module", "proc_approve", "proc_view", "view_dashboard", "view_reports"]'
    try:
        cur = conn.execute(
            "DELETE FROM custom_roles WHERE role_key IN "
            "('supply_chain_director','plant_director','financial_director',"
            "'managing_director','board') AND perms_json = ?", (seeded,))
        conn.commit()
    except Exception:
        conn.rollback()    # custom_roles belongs to the admin module; not here yet
        return 0
    return cur.rowcount if (cur.rowcount or 0) > 0 else 0


def create_and_seed(conn):
    """Create procurement tables, run column migrations, and seed sample data."""
    conn.executescript(SCHEMA)
    conn.executescript(_SIGN_EVENTS_DDL)
    conn.executescript(_PO_REV_DDL)
    conn.executescript(_GRN_DDL)
    conn.executescript(_RFQ_DDL)
    conn.commit()
    # Idempotent column migrations (safe on already-deployed databases).
    for _col, _ddl in (_STEP_MIGRATIONS + _PR_MIGRATIONS + _ITEM_MIGRATIONS
                       + _QUOTE_MIGRATIONS + _MONEY_MIGRATIONS + _PROSE_MIGRATIONS):
        try:
            conn.execute(_ddl)
            conn.commit()
        except Exception:
            conn.rollback()
    backfill_retention(conn)
    # The DOAM authority roles used to be seeded into custom_roles here. They
    # are now registered in code (constants.PROC_ROLE_PERMS -> security's RBAC
    # merge), which is the registry can_act() reads: a role that exists only as
    # a row this seed never committed is missing wherever the write did not land.
    # The rows it already wrote shadow that code definition, so clear them once.
    drop_legacy_doam_role_rows(conn)
    seed_governance(conn)
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
            """INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role,
               activated_at, created_at, action_type)
               VALUES (?,?,?,?,?,?,?,?)""",
            # DOAM Table 5: the top of the ladder commits (A), everyone below it
            # verifies (R) — the same rule services.stamp_step_actions applies.
            (pr_id, i, stage, "pending", stage_label(stage), now if i == 1 else None,
             now, "approve" if i == len(ladder) else "review"))
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
    # The demo PR above is a raw INSERT (not create_pr), so stamp it too rather
    # than leaving a fresh database with one unguardable row until the next boot.
    backfill_retention(conn)
