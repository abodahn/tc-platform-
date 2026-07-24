"""
Order + Time & Action — schema + seed, merged into the TC Platform DB.
Native ord_* tables. Idempotent create_and_seed(conn); demo data only when empty.
"""
from datetime import date, timedelta

from .constants import DEFAULT_TNA

SCHEMA = """
-- A customer order (the spine styles, T&A and costing hang off).
CREATE TABLE IF NOT EXISTS ord_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no TEXT,
    buyer TEXT,
    po_no TEXT,
    style_ref TEXT,
    style_name TEXT,
    qty REAL DEFAULT 0,
    unit_price REAL DEFAULT 0,
    currency TEXT DEFAULT 'USD',
    order_date TEXT,
    ship_date TEXT,                 -- target ex-factory; anchors the critical path
    status TEXT DEFAULT 'draft',
    department TEXT, line_no TEXT,
    notes TEXT,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_ord_ship ON ord_orders(ship_date);
CREATE INDEX IF NOT EXISTS ix_ord_status ON ord_orders(status);

-- One Time & Action milestone on an order's critical path.
CREATE TABLE IF NOT EXISTS ord_milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    seq INTEGER DEFAULT 0,
    name TEXT NOT NULL,
    offset_days INTEGER DEFAULT 0,  -- days before ship_date (from the template)
    planned_date TEXT,
    actual_date TEXT,
    status TEXT DEFAULT 'pending',  -- pending / done (late/at-risk computed on read)
    owner TEXT,
    late_alerted INTEGER DEFAULT 0,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_ordms_order ON ord_milestones(order_id);
CREATE INDEX IF NOT EXISTS ix_ordms_planned ON ord_milestones(planned_date);
"""


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _gen_milestones(conn, order_id, ship_date):
    """(Re)build the T&A milestones from the default template + ship date."""
    conn.execute("DELETE FROM ord_milestones WHERE order_id=? AND actual_date IS NULL", (order_id,))
    sd = None
    try:
        sd = date.fromisoformat(str(ship_date)[:10]) if ship_date else None
    except (ValueError, TypeError):
        sd = None
    for i, (name, off) in enumerate(DEFAULT_TNA, start=1):
        exists = conn.execute("SELECT id FROM ord_milestones WHERE order_id=? AND name=?",
                              (order_id, name)).fetchone()
        if exists:
            continue
        planned = str(sd - timedelta(days=off)) if sd else None
        conn.execute(
            "INSERT INTO ord_milestones (order_id,seq,name,offset_days,planned_date,status) "
            "VALUES (?,?,?,?,?,'pending')", (order_id, i, name, off, planned))


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    if _empty(conn, "ord_orders"):
        today = date.today()
        now = today.strftime("%Y-%m-%d %H:%M:%S")
        demo = [
            ("SO-1001", "EU Buyer A", "PO-88123", "TC-KNIT-01", "Men's Crew Tee",
             12000, 3.85, "USD", str(today - timedelta(days=20)), str(today + timedelta(days=22)), "in_production"),
            ("SO-1002", "UK Retailer", "PO-55010", "TC-FL-07", "Fleece Hoodie",
             6500, 9.20, "USD", str(today - timedelta(days=8)), str(today + timedelta(days=48)), "confirmed"),
            ("SO-1003", "US Brand", "PO-42219", "TC-DEN-03", "Denim Short",
             9000, 6.40, "USD", str(today - timedelta(days=40)), str(today + timedelta(days=6)), "in_production"),
        ]
        for r in demo:
            cur = conn.execute(
                "INSERT INTO ord_orders (order_no,buyer,po_no,style_ref,style_name,qty,unit_price,"
                "currency,order_date,ship_date,status,department,created_by,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*r, "Production", "seed", now))
            oid = cur.lastrowid
            _gen_milestones(conn, oid, r[9])
            # mark the earliest milestones done on the two in-production orders so the
            # board shows real progress (and SO-1003, shipping in 6 days, shows late risk)
            if r[10] == "in_production":
                ms = conn.execute("SELECT id,planned_date FROM ord_milestones WHERE order_id=? "
                                  "ORDER BY seq LIMIT 5", (oid,)).fetchall()
                for m in ms[:3]:
                    conn.execute("UPDATE ord_milestones SET status='done', actual_date=? WHERE id=?",
                                 (m["planned_date"], m["id"]))
