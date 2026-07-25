"""
Shop-floor MES — schema + demo seed, merged into the TC Platform DB.

Native `mes_*` tables. Reuses the platform's existing `production_lines` as the
line master (line_id) and `ord_orders` as the order spine (bare INTEGER, no FK —
house style). Complements app/smartfactory: /factory keeps its own legacy hourly
form; this module owns the reason-coded, OEE-capable hourly record and the
bundle/WIP ledger. DDL is SQLite-authored and translated for PostgreSQL by app.db.
Idempotent + non-destructive: create_and_seed(conn) runs every boot, creates what
is missing and seeds a small clearly-marked DEMO day only when the module is empty.
"""
from datetime import date

SCHEMA = """
-- One (line, date, hour) cell of the hourly production board.
CREATE TABLE IF NOT EXISTS mes_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER NOT NULL,       -- production_lines.id
    order_id INTEGER,               -- ord_orders.id (nullable, no FK by house style)
    work_date TEXT NOT NULL,        -- ISO YYYY-MM-DD
    hour_slot TEXT NOT NULL,        -- one of constants.HOUR_SLOTS
    target_qty INTEGER DEFAULT 0,
    actual_qty INTEGER DEFAULT 0,   -- total pieces off the line this hour
    reject_qty INTEGER DEFAULT 0,   -- subset of actual_qty; good = actual - reject
    operators INTEGER DEFAULT 0,    -- operators present on the line that hour
    smv REAL DEFAULT 0,             -- standard minutes per piece of the style running
    notes TEXT,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
-- The unique cell is the anti-double-count rule: re-submitting an hour EDITS it.
CREATE UNIQUE INDEX IF NOT EXISTS ux_mes_hourly ON mes_hourly(line_id, work_date, hour_slot);
CREATE INDEX IF NOT EXISTS ix_mes_hourly_date ON mes_hourly(work_date);

-- Reason-coded lost minutes. Coded loss is what makes OEE actionable.
CREATE TABLE IF NOT EXISTS mes_downtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER NOT NULL,
    work_date TEXT NOT NULL,        -- loss is attributed per DAY: that is the window
                                    -- Availability is computed over (planned hours x 60)
    reason TEXT NOT NULL,           -- one of constants.DOWNTIME_REASONS
    minutes REAL DEFAULT 0,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_mes_dt_line ON mes_downtime(line_id, work_date);

-- A cut bundle. qty is the ceiling every movement is checked against.
CREATE TABLE IF NOT EXISTS mes_bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_no TEXT UNIQUE,
    order_id INTEGER,
    size TEXT, color TEXT,
    qty INTEGER DEFAULT 0,          -- pieces cut into this bundle
    origin_section TEXT DEFAULT 'cutting',
    section TEXT DEFAULT 'cutting', -- last scanned destination (display only; WIP comes from moves)
    status TEXT DEFAULT 'created',  -- created / in_progress / completed / rejected
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_mes_bundle_order ON mes_bundles(order_id, status);

-- One scan: pieces handed from one section to the next. Append-only ledger.
CREATE TABLE IF NOT EXISTS mes_bundle_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id INTEGER NOT NULL,
    from_section TEXT NOT NULL,
    to_section TEXT NOT NULL,
    qty INTEGER DEFAULT 0,
    operator TEXT,
    moved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_mes_move_bundle ON mes_bundle_moves(bundle_id);
CREATE INDEX IF NOT EXISTS ix_mes_move_to ON mes_bundle_moves(to_section);
"""


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _demo_lines(conn):
    """Two real lines to hang the demo day on: prefer a sewing + a finishing line,
    fall back to whatever the line master has. Returns [] on an empty factory."""
    try:
        rows = conn.execute("SELECT id, name, operators FROM production_lines ORDER BY id").fetchall()
    except Exception:
        conn.rollback()          # PostgreSQL: a failed query poisons the transaction
        return []
    rows = [dict(r) for r in rows]
    pick = [r for r in rows if "Sewing Line 1" in (r["name"] or "")]
    pick += [r for r in rows if "Finishing" in (r["name"] or "")]
    return (pick or rows)[:2]


def _demo_order(conn):
    try:
        r = conn.execute("SELECT id FROM ord_orders WHERE status='in_production' "
                         "ORDER BY id LIMIT 1").fetchone()
        if not r:
            r = conn.execute("SELECT id FROM ord_orders ORDER BY id LIMIT 1").fetchone()
        return r["id"] if r else None
    except Exception:
        conn.rollback()        # orders module not seeded yet — demo still works
        return None


# DEMO day, hand-checked so the dashboards read like a real shift:
#   line A: 14 operators, SMV 24.5 (denim short) -> ~34 pcs/h at 100% -> target 30
#   line B: 10 operators, SMV 12.0 (finishing)   -> ~50 pcs/h at 100% -> target 45
# One RED hour each, and line B breaks the 60-minute downtime alert threshold.
_DEMO_A = {"target": 30, "operators": 14, "smv": 24.5,
           "actual": [28, 29, 26, 31, 24, 29], "reject": [1, 0, 1, 1, 1, 0],
           "downtime": [("no_feeding", 25.0), ("changeover", 15.0)]}
_DEMO_B = {"target": 45, "operators": 10, "smv": 12.0,
           "actual": [40, 42, 31, 43, 41, 42], "reject": [1, 1, 2, 1, 0, 1],
           "downtime": [("machine_breakdown", 45.0), ("power", 20.0)]}


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    # Commit the DDL NOW. On PostgreSQL the CREATE TABLEs are still inside the open
    # transaction, and the demo lookups below query FOREIGN tables (production_lines,
    # ord_orders) inside try/except: a missing one aborts the transaction, which would
    # take the brand-new mes_* tables down with it and leave every /mes page 500ing.
    conn.commit()
    today = str(date.today())
    now = today + " 06:00:00"

    if _empty(conn, "mes_hourly"):
        from .constants import HOUR_SLOTS
        lines = _demo_lines(conn)
        oid = _demo_order(conn)
        for ln, dm in zip(lines, (_DEMO_A, _DEMO_B)):
            for i, slot in enumerate(HOUR_SLOTS[:len(dm["actual"])]):
                conn.execute(
                    "INSERT INTO mes_hourly (line_id,order_id,work_date,hour_slot,target_qty,"
                    "actual_qty,reject_qty,operators,smv,created_by,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (ln["id"], oid, today, slot, dm["target"], dm["actual"][i],
                     dm["reject"][i], dm["operators"], dm["smv"], "seed", now))
            for reason, mins in dm["downtime"]:
                conn.execute(
                    "INSERT INTO mes_downtime (line_id,work_date,reason,minutes,"
                    "created_by,created_at) VALUES (?,?,?,?,?,?)",
                    (ln["id"], today, reason, mins, "seed", now))

    if _empty(conn, "mes_bundles"):
        oid = _demo_order(conn)
        # 6 bundles x 60 pcs. Four have reached sewing, two of those finishing —
        # so the WIP board shows a real cut > sewn > finished cascade.
        sizes = [("S", "Indigo"), ("M", "Indigo"), ("M", "Indigo"),
                 ("L", "Indigo"), ("L", "Black"), ("XL", "Black")]
        ids = []
        for n, (size, color) in enumerate(sizes, start=1):
            cur = conn.execute(
                "INSERT INTO mes_bundles (bundle_no,order_id,size,color,qty,origin_section,"
                "section,status,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("BDL-%06d" % n, oid, size, color, 60, "cutting", "cutting", "created", "seed", now))
            ids.append(cur.lastrowid)
        for bid in ids[:4]:
            conn.execute("INSERT INTO mes_bundle_moves (bundle_id,from_section,to_section,qty,"
                         "operator,moved_at) VALUES (?,?,?,?,?,?)",
                         (bid, "cutting", "sewing", 60, "OP-1004", now))
            conn.execute("UPDATE mes_bundles SET section='sewing', status='in_progress', "
                         "updated_at=? WHERE id=?", (now, bid))
        for bid in ids[:2]:
            conn.execute("INSERT INTO mes_bundle_moves (bundle_id,from_section,to_section,qty,"
                         "operator,moved_at) VALUES (?,?,?,?,?,?)",
                         (bid, "sewing", "finishing", 60, "OP-2011", now))
            conn.execute("UPDATE mes_bundles SET section='finishing', updated_at=? WHERE id=?",
                         (now, bid))
    conn.commit()
