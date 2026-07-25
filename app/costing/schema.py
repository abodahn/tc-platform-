"""
BOM + order costing — schema + seed, merged into the TC Platform DB.

Native `cst_*` tables hanging off ord_orders.id (no FKs anywhere in this codebase;
a child table carries order_id + an index). Idempotent + non-destructive:
create_and_seed(conn) runs on every boot, creates what is missing, and seeds a
small clearly-marked DEMO dataset only when the module is empty AND the demo
orders are present — a real order never gets a fabricated BOM.
"""
from datetime import datetime

SCHEMA = """
-- One BOM line for an order: what goes into one garment, and what it costs.
CREATE TABLE IF NOT EXISTS cst_bom_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    seq INTEGER DEFAULT 0,
    item TEXT NOT NULL,
    kind TEXT DEFAULT 'fabric',      -- fabric / trim / other
    colour TEXT,
    consumption REAL DEFAULT 0,      -- per finished garment, in uom
    uom TEXT DEFAULT 'pcs',
    allowance_pct REAL DEFAULT 0,    -- cutting/process wastage; INFLATES the buy
    unit_price REAL,                 -- NULL = not priced yet (never silently 0)
    supplier TEXT,
    notes TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cst_bom_order ON cst_bom_lines(order_id);

-- The cost sheet: everything that is NOT material, as per-unit figures in the
-- order's own currency. Material is derived from the BOM, never typed here.
CREATE TABLE IF NOT EXISTS cst_sheets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE,
    smv REAL DEFAULT 0,              -- standard minute value per garment
    cm_rate REAL DEFAULT 0,          -- cost per minute
    cm_per_unit REAL DEFAULT 0,      -- flat CM, used when smv/rate are not set
    overhead_per_unit REAL DEFAULT 0,
    freight_per_unit REAL DEFAULT 0,
    duty_per_unit REAL DEFAULT 0,
    other_per_unit REAL DEFAULT 0,   -- finance / commission / sundry
    notes TEXT,
    variance_alerted INTEGER DEFAULT 0,
    updated_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cst_sheet_order ON cst_sheets(order_id);

-- An actual cost booked against an order, as a TOTAL in the order currency.
-- Procurement receipts and warehouse issues are read live, not copied here.
CREATE TABLE IF NOT EXISTS cst_actuals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    category TEXT NOT NULL,          -- one of constants.CATEGORIES
    amount REAL DEFAULT 0,
    source TEXT,                     -- payroll run, invoice ref, GL batch...
    notes TEXT,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cst_act_order ON cst_actuals(order_id);
"""


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        conn.rollback()             # PostgreSQL: a failed statement poisons the txn
        return False


# DEMO BOMs keyed by the seeded orders' style_ref. Consumption/allowances/prices
# are realistic mid-market figures (kg for knits, m for denim).
# (item, kind, colour, consumption, uom, allowance_pct, unit_price, supplier)
_DEMO_BOM = {
    "TC-KNIT-01": [
        ("Single jersey 180gsm combed", "fabric", "Navy", 0.24, "kg", 3.0, 5.40, "Nile Knitting"),
        ("1x1 rib collar/cuff", "fabric", "Navy", 0.02, "kg", 5.0, 5.20, "Nile Knitting"),
        ("Sewing thread 40/2", "trim", "Navy", 120, "m", 5.0, 0.0008, "Coats Egypt"),
        ("Woven labels (main + care)", "trim", None, 1, "set", 2.0, 0.020, "Delta Labels"),
        ("Hangtag + string", "trim", None, 1, "set", 2.0, 0.035, "Delta Labels"),
        ("Poly bag", "other", None, 1, "pcs", 3.0, 0.022, "Pack Misr"),
        ("Export carton (24 pcs)", "other", None, 0.025, "ctn", 2.0, 0.85, "Pack Misr"),
    ],
    "TC-FL-07": [
        ("Brushed fleece 320gsm", "fabric", "Charcoal", 0.62, "kg", 4.0, 6.10, "Nile Knitting"),
        ("2x2 rib hem/cuff", "fabric", "Charcoal", 0.05, "kg", 5.0, 5.80, "Nile Knitting"),
        ("Drawcord 8mm", "trim", "Charcoal", 1.4, "m", 3.0, 0.090, "Alex Trims"),
        ("Metal eyelets", "trim", "Antique", 2, "pcs", 3.0, 0.020, "Alex Trims"),
        ("Sewing thread 40/2", "trim", "Charcoal", 200, "m", 5.0, 0.0008, "Coats Egypt"),
        ("Woven labels (main + care)", "trim", None, 1, "set", 2.0, 0.055, "Delta Labels"),
        ("Poly bag", "other", None, 1, "pcs", 3.0, 0.030, "Pack Misr"),
        ("Export carton (12 pcs)", "other", None, 0.02, "ctn", 2.0, 0.95, "Pack Misr"),
    ],
    "TC-DEN-03": [
        ("Denim 10oz stretch", "fabric", "Mid indigo", 0.85, "m", 8.0, 2.60, "Mahalla Weaving"),
        ("Pocketing twill", "fabric", "Ecru", 0.18, "m", 5.0, 0.85, "Mahalla Weaving"),
        ("Sewing thread 30/3", "trim", "Gold", 180, "m", 5.0, 0.0008, "Coats Egypt"),
        ("Zipper 12cm YKK", "trim", "Antique", 1, "pcs", 2.0, 0.160, "Alex Trims"),
        ("Shank button + 6 rivets", "trim", "Antique", 1, "set", 3.0, 0.090, "Alex Trims"),
        ("Woven labels + leather patch", "trim", None, 1, "set", 2.0, 0.050, "Delta Labels"),
        ("Poly bag", "other", None, 1, "pcs", 3.0, 0.024, "Pack Misr"),
        ("Export carton (20 pcs)", "other", None, 0.03, "ctn", 2.0, 0.90, "Pack Misr"),
    ],
}

# (style_ref, smv, cm_rate, overhead, freight, duty, other)
_DEMO_SHEETS = {
    "TC-KNIT-01": (12.5, 0.075, 0.26, 0.11, 0.0, 0.05),
    "TC-FL-07": (28.0, 0.075, 0.55, 0.26, 0.0, 0.12),
    "TC-DEN-03": (22.0, 0.075, 0.42, 0.18, 0.0, 0.09),
}

# Actuals only on the two orders already in production, so the demo shows one
# healthy order and one bleeding one. (category, amount, source)
_DEMO_ACTUALS = {
    "TC-KNIT-01": [
        ("material", 19180.00, "Fabric + trim invoices"),
        ("cm", 11410.00, "Payroll allocation wk 1-6"),
        ("overhead", 3120.00, "Absorbed overhead"),
        ("freight", 1290.00, "Forwarder invoice"),
    ],
    "TC-DEN-03": [
        ("material", 30900.00, "Denim price rise + cutting loss"),
        ("cm", 15900.00, "Payroll + 210 OT hours"),
        ("overhead", 3900.00, "Absorbed overhead"),
        ("freight", 2050.00, "Air-freight part shipment"),
        ("other", 810.00, "LC + bank charges"),
    ],
}


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    # Commit the DDL before any seed INSERT: init_db wraps this call in
    # try/except-rollback, and on PostgreSQL a failed seed would otherwise roll
    # the CREATE TABLEs back too and the tables would never exist on Render.
    conn.commit()

    # Seed only a VIRGIN module. Checking cst_bom_lines alone was not enough: a
    # user who empties the BOM leaves the demo cst_sheets rows behind, and the
    # next boot re-inserts them straight into UNIQUE(order_id) — an IntegrityError
    # inside init_db on every start.
    if not all(_empty(conn, t) for t in ("cst_bom_lines", "cst_sheets", "cst_actuals")):
        return
    try:
        orders = conn.execute(
            "SELECT id, style_ref, qty FROM ord_orders WHERE created_by='seed' "
            "ORDER BY id").fetchall()
    except Exception:
        conn.rollback()             # PostgreSQL: a failed statement poisons the txn
        return                      # orders module not present yet — nothing to cost
    now = _now()
    for o in orders:
        bom = _DEMO_BOM.get(o["style_ref"])
        if not bom:
            continue                # a real order never gets a fabricated BOM
        for i, r in enumerate(bom, start=1):
            conn.execute(
                "INSERT INTO cst_bom_lines (order_id,seq,item,kind,colour,consumption,uom,"
                "allowance_pct,unit_price,supplier,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (o["id"], i, r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], now))
        smv, rate, oh, fr, du, ot = _DEMO_SHEETS[o["style_ref"]]
        conn.execute(
            "INSERT INTO cst_sheets (order_id,smv,cm_rate,overhead_per_unit,freight_per_unit,"
            "duty_per_unit,other_per_unit,updated_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (o["id"], smv, rate, oh, fr, du, ot, "seed", now))
        for cat, amt, src in _DEMO_ACTUALS.get(o["style_ref"], []):
            conn.execute(
                "INSERT INTO cst_actuals (order_id,category,amount,source,created_by,created_at) "
                "VALUES (?,?,?,?,?,?)", (o["id"], cat, amt, src, "seed", now))
