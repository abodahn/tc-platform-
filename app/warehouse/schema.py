"""
Warehouse — schema + seed, merged into the TC Platform DB.

Native `wh_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db. create_and_seed(conn) runs on every boot: it creates whatever is missing
and seeds a small, clearly-marked DEMO dataset ONLY when the module is empty.
It never deletes or overwrites anything.
"""
from datetime import date, datetime, timedelta

SCHEMA = """
-- Material master. ONE row per purchasable material+colour, roll-tracked or not.
-- This is also the counter row the atomic reservation locks; wh_rolls hangs off it
-- only when roll_tracked=1 (fabric, elastic tape...).
CREATE TABLE IF NOT EXISTS wh_materials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    name TEXT,
    kind TEXT DEFAULT 'fabric',        -- label only: fabric / trim / accessory
    roll_tracked INTEGER DEFAULT 0,    -- behaviour switch (elastic tape is a TRIM on rolls)
    color TEXT,
    composition TEXT,
    width_cm REAL DEFAULT 0,           -- nominal; the roll's own width is what gates a cut
    supplier TEXT,
    uom TEXT DEFAULT 'm',
    stock_qty REAL DEFAULT 0,          -- == SUM(wh_rolls.remaining_m) when roll_tracked=1
    reserved_qty REAL DEFAULT 0,       -- committed but not yet issued (quantity materials)
    min_level REAL DEFAULT 0,
    reorder_level REAL DEFAULT 0,
    avg_cost REAL DEFAULT 0,           -- moving weighted average, per uom
    last_price REAL DEFAULT 0,
    warehouse TEXT, bin TEXT,
    low_alerted INTEGER DEFAULT 0,     -- bell dedup; cleared when the material recovers
    is_active INTEGER DEFAULT 1,
    notes TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wh_mat_kind ON wh_materials(kind);

-- A physical fabric roll. Consumed PARTIALLY, so remaining_m is the live number and
-- length_m stays as received (the supplier label) for reconciliation.
CREATE TABLE IF NOT EXISTS wh_rolls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no TEXT UNIQUE,
    material_id INTEGER NOT NULL,
    supplier_lot TEXT,
    shade_lot TEXT,                    -- dye lot: one cut lay must draw from ONE lot
    grade TEXT DEFAULT 'A',
    width_cm REAL DEFAULT 0,
    length_m REAL DEFAULT 0,           -- as received; immutable
    remaining_m REAL DEFAULT 0,
    reserved_m REAL DEFAULT 0,
    unit_cost REAL DEFAULT 0,
    status TEXT DEFAULT 'available',   -- available / partial / consumed / quarantine
    grn_ref TEXT,                      -- goods-receipt / PR reference
    warehouse TEXT, bin TEXT,
    received_at TEXT, notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_wh_roll_mat ON wh_rolls(material_id);
CREATE INDEX IF NOT EXISTS ix_wh_roll_shade ON wh_rolls(shade_lot);
CREATE INDEX IF NOT EXISTS ix_wh_roll_status ON wh_rolls(status);

-- Append-only signed ledger. Every quantity change in this module writes exactly one
-- row here; qty is SIGNED (negative = out) and before/after snapshot the counter so
-- the ledger audits itself without a replay. type: opening / purchase_receiving /
-- issue / return / adjustment / transfer / fg_pack / fg_ship.
CREATE TABLE IF NOT EXISTS wh_movements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movement_no TEXT UNIQUE,
    type TEXT,
    material_id INTEGER, roll_id INTEGER, fg_id INTEGER,
    order_id INTEGER,                  -- consumption per customer order (costing reads this)
    qty REAL DEFAULT 0,
    before_qty REAL DEFAULT 0, after_qty REAL DEFAULT 0,
    unit_cost REAL DEFAULT 0,
    ref TEXT, performed_by TEXT, notes TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wh_mov_mat ON wh_movements(material_id);
CREATE INDEX IF NOT EXISTS ix_wh_mov_order ON wh_movements(order_id);

-- Which rolls (or how much quantity) are committed to which order. A header-level
-- reserved number alone cannot be released roll-accurately, so the pick is persisted.
CREATE TABLE IF NOT EXISTS wh_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    material_id INTEGER NOT NULL,
    roll_id INTEGER,                   -- NULL for quantity-tracked material
    qty REAL DEFAULT 0,
    status TEXT DEFAULT 'reserved',    -- reserved / issued / released
    created_by TEXT, created_at TEXT, closed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wh_alloc_order ON wh_allocations(order_id);
CREATE INDEX IF NOT EXISTS ix_wh_alloc_status ON wh_allocations(status);

-- Finished goods: ONE ROW per style x colour x size. Sizes are DATA, never columns —
-- a column-per-size table needs an ALTER every time a buyer adds a size range.
CREATE TABLE IF NOT EXISTS wh_fg (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,
    style_code TEXT, color TEXT, size TEXT,
    packed_qty REAL DEFAULT 0,         -- cumulative in
    shipped_qty REAL DEFAULT 0,        -- cumulative out; on hand = packed - shipped
    uom TEXT DEFAULT 'pcs',
    warehouse TEXT, bin TEXT,
    notes TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wh_fg_order ON wh_fg(order_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wh_fg_sku ON wh_fg(order_id, style_code, color, size);
"""


def _now():
    # duplicated from services on purpose — schema must not import services
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _opening(conn, mtype, qty, material_id=None, roll_id=None, fg_id=None,
             order_id=None, unit_cost=0, notes=None, before=0):
    """Seed-side ledger row. Numbered from its own id, exactly like services.doc_no —
    a COUNT(*)+1 scheme repeats numbers after any id gap and breaks UNIQUE(movement_no)."""
    cur = conn.execute(
        "INSERT INTO wh_movements (type,material_id,roll_id,fg_id,order_id,qty,before_qty,"
        "after_qty,unit_cost,ref,performed_by,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mtype, material_id, roll_id, fg_id, order_id, qty, before, before + qty, unit_cost,
         "SEED", "seed", notes, _now()))
    conn.execute("UPDATE wh_movements SET movement_no=? WHERE id=?",
                 (f"WHM-{date.today().year}-{cur.lastrowid:06d}", cur.lastrowid))


# --- demo data (garment factory, deliberately small and obviously demo) -----
_MATERIALS = [
    # code, name, kind, roll_tracked, color, composition, width_cm, supplier, uom, reorder, avg_cost
    ("FAB-JER-WHT", "Cotton Single Jersey 180gsm", "fabric", 1, "White", "100% Cotton",
     180, "Nile Textiles", "m", 500, 3.20),
    ("FAB-JER-NVY", "Cotton Single Jersey 180gsm", "fabric", 1, "Navy", "100% Cotton",
     180, "Nile Textiles", "m", 400, 3.35),
    ("FAB-FLC-GRY", "Brushed Fleece 320gsm", "fabric", 1, "Grey Melange", "80% Cotton / 20% PES",
     185, "Delta Knits", "m", 300, 5.10),
    ("FAB-DEN-IND", "Denim 12oz", "fabric", 1, "Indigo", "98% Cotton / 2% Elastane",
     150, "Misr Denim", "m", 250, 6.75),
    # a TRIM that is nonetheless roll-tracked — this is why roll_tracked is its own flag
    ("TRM-ELS-30", "Elastic tape 30mm", "trim", 1, "White", "Polyester / Rubber",
     3, "Cairo Trims", "m", 800, 0.42),
    ("TRM-BTN-18L", "Button 18L 4-hole", "trim", 0, "Navy", "Polyester",
     0, "Cairo Trims", "pcs", 20000, 0.03),
    ("TRM-THR-40", "Sewing thread 40/2", "trim", 0, "White", "Spun Polyester",
     0, "Coats", "cone", 200, 1.15),
    ("ACC-LBL-MAIN", "Main woven label", "accessory", 0, "—", "Damask",
     0, "Label House", "pcs", 10000, 0.02),
]

# roll seeds: material code, shade lot, supplier lot, width, label length, remaining, status
_ROLLS = [
    ("FAB-JER-WHT", "D-4471", "NT-2291", 180, 320, 320, "available"),
    ("FAB-JER-WHT", "D-4471", "NT-2291", 180, 310, 145, "partial"),
    ("FAB-JER-WHT", "D-4472", "NT-2305", 180, 300, 300, "available"),
    ("FAB-JER-NVY", "D-5108", "NT-2312", 180, 280, 280, "available"),
    ("FAB-JER-NVY", "D-5108", "NT-2312", 180, 295, 295, "available"),
    ("FAB-FLC-GRY", "F-9002", "DK-771", 185, 260, 260, "available"),
    ("FAB-FLC-GRY", "F-9003", "DK-780", 185, 250, 60, "partial"),
    ("FAB-DEN-IND", "IND-330", "MD-118", 150, 240, 240, "available"),
    ("FAB-DEN-IND", "IND-331", "MD-121", 150, 230, 230, "quarantine"),   # shade out of band
    ("TRM-ELS-30", "E-77", "CT-9", 3, 1000, 1000, "available"),
]

_TRIM_OPENING = {"TRM-BTN-18L": 46000, "TRM-THR-40": 340, "ACC-LBL-MAIN": 18000}

_FG_COLORS = {"White": [("S", 400, 400), ("M", 900, 600), ("L", 850, 600),
                        ("XL", 500, 0), ("XXL", 180, 0)],
              "Navy": [("S", 300, 0), ("M", 700, 0), ("L", 660, 0), ("XL", 400, 0)]}


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    # Commit the DDL before any seed INSERT. init_db() wraps this call in
    # try/except+rollback, and on PostgreSQL a failing seed would roll the CREATE
    # TABLEs back with it — the tables would silently never appear on Render.
    conn.commit()
    if not _empty(conn, "wh_materials"):
        return  # already seeded / in use — never overwrite

    today = date.today()
    now = _now()
    ids = {}
    for (code, name, kind, rt, color, comp, width, sup, uom, reorder, cost) in _MATERIALS:
        cur = conn.execute(
            "INSERT INTO wh_materials (code,name,kind,roll_tracked,color,composition,width_cm,"
            "supplier,uom,stock_qty,reserved_qty,min_level,reorder_level,avg_cost,last_price,"
            "warehouse,bin,is_active,notes,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,1,?,?)",
            (code, name, kind, rt, color, comp, width, sup, uom, round(reorder / 2), reorder,
             cost, cost, "RM Store", None, "DEMO seed data", now))
        ids[code] = cur.lastrowid

    # rolls — the opening movement books the COUNTED remaining length, not the label
    # length: an opening balance is a stock take, so the ledger must start from what is
    # physically on the shelf or SUM(movements) never reconciles with stock_qty.
    for i, (code, shade, lot, width, length, remaining, status) in enumerate(_ROLLS):
        mid = ids[code]
        cost = dict((m[0], m[10]) for m in _MATERIALS)[code]
        recv = str(today - timedelta(days=60 - i * 5))
        cur = conn.execute(
            "INSERT INTO wh_rolls (material_id,supplier_lot,shade_lot,grade,width_cm,length_m,"
            "remaining_m,reserved_m,unit_cost,status,grn_ref,warehouse,bin,received_at,notes) "
            "VALUES (?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)",
            (mid, lot, shade, "A", width, length, remaining, cost, status, "GRN-DEMO",
             "RM Store", f"A-{i + 1:02d}", recv, "DEMO seed data"))
        rid = cur.lastrowid
        conn.execute("UPDATE wh_rolls SET roll_no=? WHERE id=?",
                     (f"ROLL-{today.year}-{rid:06d}", rid))
        _opening(conn, "opening", remaining, material_id=mid, roll_id=rid,
                 unit_cost=cost, notes="Opening stock take")

    # header stock = sum of the rolls on it (the invariant this module never breaks)
    for code, mid in ids.items():
        tot = conn.execute("SELECT COALESCE(SUM(remaining_m),0) AS s FROM wh_rolls "
                           "WHERE material_id=?", (mid,)).fetchone()["s"]
        if tot:
            conn.execute("UPDATE wh_materials SET stock_qty=? WHERE id=?", (tot, mid))

    for code, qty in _TRIM_OPENING.items():
        mid = ids[code]
        conn.execute("UPDATE wh_materials SET stock_qty=? WHERE id=?", (qty, mid))
        _opening(conn, "opening", qty, material_id=mid, notes="Opening stock take")

    _seed_fg(conn, today, now)


def _seed_fg(conn, today, now):
    """Finished goods for the seeded demo orders, if the orders module has any."""
    try:
        orders = conn.execute("SELECT id, order_no, style_ref FROM ord_orders "
                              "ORDER BY id LIMIT 2").fetchall()
    except Exception:
        return  # orders module not present — FG simply starts empty
    for o in orders or []:
        style = o["style_ref"] or o["order_no"] or "STYLE"
        for color, rows in _FG_COLORS.items():
            for size, packed, shipped in rows:
                cur = conn.execute(
                    "INSERT INTO wh_fg (order_id,style_code,color,size,packed_qty,shipped_qty,"
                    "uom,warehouse,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (o["id"], style, color, size, packed, shipped, "pcs", "FG Store",
                     "DEMO seed data", now, now))
                fid = cur.lastrowid
                _opening(conn, "fg_pack", packed, fg_id=fid, order_id=o["id"])
                if shipped:
                    _opening(conn, "fg_ship", -shipped, fg_id=fid, order_id=o["id"],
                             before=packed)
