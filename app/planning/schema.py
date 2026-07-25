"""
Finite-capacity planning — schema + seed, merged into the TC Platform DB.

Native `pln_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db. Idempotent + non-destructive: create_and_seed(conn) runs every startup,
creates missing tables, and seeds a small clearly-marked DEMO dataset only when
the module is empty.

`pln_lines` is the CAPACITY PROFILE of a line, not a second line master — where a
matching row exists in the platform's `production_lines` it is linked by line_id.
"""
from datetime import date, timedelta

from .services import daily_capacity_minutes, plan_end_date

SCHEMA = """
-- Capacity profile of one production line.
-- daily_capacity_minutes = operators x working_minutes x efficiency_pct / 100
CREATE TABLE IF NOT EXISTS pln_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER,                      -- optional link to production_lines.id
    code TEXT,
    name TEXT NOT NULL,
    section TEXT,                         -- Cutting / Sewing / Wash / Finishing ...
    operators INTEGER DEFAULT 0,
    working_minutes INTEGER DEFAULT 0,    -- attended minutes per operator per day
    efficiency_pct REAL DEFAULT 100,      -- a line never delivers 100% of theoretical minutes
    active INTEGER DEFAULT 1,
    notes TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_pln_line_active ON pln_lines(active);

-- Standard minute value of one garment, per order. Editable figure, not a time study.
CREATE TABLE IF NOT EXISTS pln_order_smv (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER UNIQUE NOT NULL,
    smv REAL DEFAULT 0,
    notes TEXT,
    updated_at TEXT
);

-- An order (or part of its quantity) loaded onto a line from start_date.
CREATE TABLE IF NOT EXISTS pln_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    pline_id INTEGER NOT NULL,            -- pln_lines.id
    qty REAL DEFAULT 0,
    smv REAL DEFAULT 0,                   -- SNAPSHOT: a later SMV edit must not silently
                                          -- re-price a plan already committed to a line
    start_date TEXT,
    end_date TEXT,                        -- projected completion, recomputed on write
    status TEXT DEFAULT 'planned',
    notes TEXT,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_pln_alloc_order ON pln_allocations(order_id);
CREATE INDEX IF NOT EXISTS ix_pln_alloc_line ON pln_allocations(pline_id, start_date);

-- Operation bulletin for line balancing (one row per sewing operation of an order).
CREATE TABLE IF NOT EXISTS pln_ops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    seq INTEGER DEFAULT 0,
    name TEXT NOT NULL,
    smv REAL DEFAULT 0,
    operators INTEGER DEFAULT 1,
    machine TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_pln_ops_order ON pln_ops(order_id);
"""

# Demo capacity: code, name, section, operators, working_minutes, efficiency_pct,
# and the production_lines name to link to when that row exists.
_DEMO_LINES = [
    ("CUT-1", "Cutting Line A", "Cutting", 10, 540, 75.0, "Cutting Line A"),
    ("SEW-1", "Sewing Line 1", "Sewing", 28, 540, 65.0, "Sewing Line 1"),
    ("SEW-2", "Sewing Line 2", "Sewing", 24, 540, 60.0, "Sewing Line 2"),
    ("FIN-1", "Finishing Line", "Finishing", 12, 540, 70.0, "Finishing Line"),
]

# Demo operation bulletin for a basic crew-neck tee (sum SMV 10.50 over 25 operators).
_DEMO_OPS = [
    ("Shoulder join", 0.85, 2, "Overlock"),
    ("Neck rib attach", 1.20, 3, "Overlock"),
    ("Neck tape / topstitch", 1.05, 2, "Flatlock"),
    ("Sleeve attach", 1.60, 4, "Overlock"),
    ("Side seam close", 1.80, 4, "Overlock"),
    ("Sleeve hem", 0.90, 2, "Coverstitch"),
    ("Bottom hem", 1.10, 3, "Coverstitch"),
    ("Label / care attach", 0.70, 2, "Lockstitch"),
    ("Trim & inspect", 1.30, 3, "Manual"),
]


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _demo_orders(conn):
    """The orders module's OWN demo orders (created_by='seed'), never real ones.
    `ORDER BY id LIMIT 3` would attach invented SMVs and demo allocations to the
    three oldest REAL customer orders on a live database — wrong capacity numbers on
    real work, plus a 'plan is late' bell for a plan nobody made. Never raises."""
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id,order_no,style_name,qty,ship_date FROM ord_orders "
            "WHERE created_by='seed' ORDER BY id LIMIT 3").fetchall()]
    except Exception:
        return []


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    today = date.today()
    now = today.strftime("%Y-%m-%d %H:%M:%S")

    if _empty(conn, "pln_lines"):
        for code, name, section, ops, mins, eff, pl_name in _DEMO_LINES:
            link = None
            try:
                r = conn.execute("SELECT id FROM production_lines WHERE name=?", (pl_name,)).fetchone()
                link = r["id"] if r else None
            except Exception:
                link = None
            conn.execute(
                "INSERT INTO pln_lines (line_id,code,name,section,operators,working_minutes,"
                "efficiency_pct,active,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (link, code, name, section, ops, mins, eff, 1, "seed", now))

    orders = _demo_orders(conn)
    if orders and _empty(conn, "pln_order_smv"):
        # Realistic SMVs: basic tee ~12.5, fleece hoodie ~24, denim short ~18.5.
        for o, smv in zip(orders, (12.5, 24.0, 18.5)):
            conn.execute("INSERT INTO pln_order_smv (order_id,smv,notes,updated_at) VALUES (?,?,?,?)",
                         (o["id"], smv, "seed", now))

    if orders and _empty(conn, "pln_allocations"):
        lines = {r["code"]: dict(r) for r in conn.execute("SELECT * FROM pln_lines").fetchall()}
        smvs = {r["order_id"]: r["smv"] for r in conn.execute(
            "SELECT order_id,smv FROM pln_order_smv").fetchall()}
        # order index, line code, qty, start offset — deliberately produces one
        # comfortable order, one overlap (SEW-1 double-booked) and one late order.
        plan = [(0, "SEW-1", 12000, 0), (1, "SEW-1", 2000, 5), (2, "SEW-2", 4000, 0)]
        for idx, code, qty, off in plan:
            if idx >= len(orders) or code not in lines:
                continue
            o = orders[idx]
            smv = smvs.get(o["id"]) or 0
            start = today + timedelta(days=off)
            cap = daily_capacity_minutes(lines[code])
            conn.execute(
                "INSERT INTO pln_allocations (order_id,pline_id,qty,smv,start_date,end_date,"
                "status,notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (o["id"], lines[code]["id"], qty, smv, str(start),
                 plan_end_date(start, qty * smv, cap), "planned", "seed", "seed", now))

    if orders and _empty(conn, "pln_ops"):
        oid = orders[0]["id"]
        for i, (name, smv, ops, machine) in enumerate(_DEMO_OPS, start=1):
            conn.execute(
                "INSERT INTO pln_ops (order_id,seq,name,smv,operators,machine,created_at) "
                "VALUES (?,?,?,?,?,?,?)", (oid, i, name, smv, ops, machine, now))

    conn.commit()
