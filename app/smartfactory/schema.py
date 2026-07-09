"""
Smart Factory (MES) — schema + demo seed, merged into the TC Platform DB.

Reuses the platform's existing `production_lines` as the line master; adds the
garment-MES tables (styles, orders, operations/SMV, hourly production entries,
quality checks + defects, denim wash batches). DDL is SQLite-authored and
translated for PostgreSQL by app.db.executescript. Idempotent + non-destructive.
"""
from datetime import date, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS sf_styles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE, name TEXT, buyer TEXT, season TEXT,
    smv REAL DEFAULT 0, product_type TEXT DEFAULT 'Denim', created_at TEXT
);
CREATE TABLE IF NOT EXISTS sf_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no TEXT UNIQUE, style_id INTEGER, buyer TEXT, qty INTEGER DEFAULT 0,
    color TEXT, delivery_date TEXT, status TEXT DEFAULT 'in_production', created_at TEXT
);
CREATE TABLE IF NOT EXISTS sf_operations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_id INTEGER, seq INTEGER DEFAULT 0, name TEXT, smv REAL DEFAULT 0, machine_type TEXT
);
CREATE TABLE IF NOT EXISTS sf_prod_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER, order_id INTEGER, hour_slot TEXT,
    target_qty INTEGER DEFAULT 0, actual_qty INTEGER DEFAULT 0, lost_min INTEGER DEFAULT 0,
    operator TEXT, entry_date TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_sf_prod_line ON sf_prod_entries(line_id, entry_date);
CREATE TABLE IF NOT EXISTS sf_quality (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id INTEGER, order_id INTEGER, stage TEXT DEFAULT 'endline',
    inspected INTEGER DEFAULT 0, defect INTEGER DEFAULT 0, rework INTEGER DEFAULT 0,
    reject INTEGER DEFAULT 0, inspector TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sf_defects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id INTEGER, code TEXT, category TEXT DEFAULT 'stitching', qty INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sf_wash_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT UNIQUE, order_id INTEGER, recipe TEXT, water_l REAL DEFAULT 0,
    energy_kwh REAL DEFAULT 0, chemical_kg REAL DEFAULT 0, pieces INTEGER DEFAULT 0,
    shade TEXT, rewash INTEGER DEFAULT 0, status TEXT DEFAULT 'done', created_at TEXT
);
CREATE TABLE IF NOT EXISTS sf_operators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE, name TEXT, line_id INTEGER, grade TEXT DEFAULT 'B',
    hourly_cost REAL DEFAULT 0, active INTEGER DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sf_fabric_rolls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    roll_no TEXT UNIQUE, order_id INTEGER, lot TEXT, shade TEXT, length_m REAL DEFAULT 0,
    width_cm REAL DEFAULT 0, grade TEXT DEFAULT 'A', status TEXT DEFAULT 'received', created_at TEXT
);
CREATE TABLE IF NOT EXISTS sf_bundles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_no TEXT UNIQUE, order_id INTEGER, roll_id INTEGER, size TEXT, color TEXT,
    qty INTEGER DEFAULT 0, operation_at TEXT DEFAULT 'cutting', status TEXT DEFAULT 'cut', created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_sf_bundles_order ON sf_bundles(order_id, status);
"""

_SLOTS = ["08:00-09:00", "09:00-10:00", "10:00-11:00", "11:00-12:00", "13:00-14:00", "14:00-15:00"]


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

    if _empty(conn, "sf_styles"):
        styles = [("D-501", "Slim Denim Jean", "Denim Co.", 24.5), ("D-777", "Straight Fit Jean", "EuroWear", 22.0),
                  ("J-100", "Denim Jacket", "BlueLine", 38.0)]
        for code, name, buyer, smv in styles:
            conn.execute("INSERT OR IGNORE INTO sf_styles (code,name,buyer,season,smv,product_type,created_at) "
                         "VALUES (?,?,?,?,?,?,?)", (code, name, buyer, "SS26", smv, "Denim", now))
        conn.commit()

    style_ids = {r["code"]: r["id"] for r in conn.execute("SELECT id,code FROM sf_styles").fetchall()}
    if _empty(conn, "sf_operations"):
        ops = ["Attach pocket", "Join side seam", "Attach waistband", "Hem bottom", "Belt loops", "Final assembly"]
        for sid in style_ids.values():
            for seq, nm in enumerate(ops, 1):
                conn.execute("INSERT INTO sf_operations (style_id,seq,name,smv,machine_type) VALUES (?,?,?,?,?)",
                             (sid, seq, nm, round(2 + seq * 0.6, 2), "SNLS"))
        conn.commit()

    if _empty(conn, "sf_orders"):
        data = [("PO-2601", "D-501", "Denim Co.", 5000, "Indigo", 12, "in_production"),
                ("PO-2602", "D-777", "EuroWear", 8000, "Black", 25, "in_production"),
                ("PO-2603", "J-100", "BlueLine", 3000, "Light Blue", 40, "washing")]
        for po, style, buyer, qty, color, dd, status in data:
            conn.execute("INSERT OR IGNORE INTO sf_orders (po_no,style_id,buyer,qty,color,delivery_date,status,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?)", (po, style_ids.get(style), buyer, qty, color, _d(dd), status, now))
        conn.commit()

    # Lines come from the platform's existing production_lines (Sewing lines used for MES entries).
    sew = conn.execute("SELECT id,name FROM production_lines WHERE name LIKE '%Sewing%' ORDER BY id").fetchall()
    if not sew:
        sew = conn.execute("SELECT id,name FROM production_lines ORDER BY id LIMIT 2").fetchall()
    order1 = conn.execute("SELECT id FROM sf_orders WHERE po_no='PO-2601'").fetchone()
    order1_id = order1["id"] if order1 else None

    if _empty(conn, "sf_prod_entries") and sew:
        for li, ln in enumerate(sew[:2]):
            for si, slot in enumerate(_SLOTS):
                tgt = 120
                act = tgt - 12 * ((si + li) % 4)          # some hours miss target -> amber/red
                conn.execute("INSERT INTO sf_prod_entries (line_id,order_id,hour_slot,target_qty,actual_qty,lost_min,operator,entry_date,created_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?)",
                             (ln["id"], order1_id, slot, tgt, max(0, act), (si % 3) * 8, f"OP-{100+si}", date.today().isoformat(), now))
        conn.commit()

    if _empty(conn, "sf_quality") and sew:
        for ln in sew[:2]:
            cur = conn.execute("INSERT INTO sf_quality (line_id,order_id,stage,inspected,defect,rework,reject,inspector,created_at) "
                               "VALUES (?,?,?,?,?,?,?,?,?)", (ln["id"], order1_id, "endline", 500, 42, 30, 6, "qc", now))
            cid = cur.lastrowid
            for code, cat, qty in [("SKIP-STITCH", "stitching", 18), ("BROKEN-STITCH", "stitching", 12),
                                   ("SHADE-VAR", "wash", 8), ("MEASUREMENT", "measurement", 4)]:
                conn.execute("INSERT INTO sf_defects (check_id,code,category,qty) VALUES (?,?,?,?)", (cid, code, cat, qty))
        conn.commit()

    if _empty(conn, "sf_wash_batches"):
        wb = conn.execute("SELECT id FROM sf_orders WHERE po_no='PO-2603'").fetchone()
        conn.execute("INSERT OR IGNORE INTO sf_wash_batches (batch_no,order_id,recipe,water_l,energy_kwh,chemical_kg,pieces,shade,rewash,status,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("WB-9001", wb["id"] if wb else None, "Stone + Enzyme", 4200, 310, 46, 600, "Light Blue", 12, "done", now))
        conn.execute("INSERT OR IGNORE INTO sf_wash_batches (batch_no,order_id,recipe,water_l,energy_kwh,chemical_kg,pieces,shade,rewash,status,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("WB-9002", wb["id"] if wb else None, "Bleach + Softener", 5100, 360, 58, 550, "Bleached", 22, "done", now))
        conn.commit()

    if _empty(conn, "sf_operators") and sew:
        grades = ["A", "B", "C"]
        for i in range(24):
            ln = sew[i % len(sew)]
            conn.execute("INSERT OR IGNORE INTO sf_operators (code,name,line_id,grade,hourly_cost,active) VALUES (?,?,?,?,?,1)",
                         (f"OP-{1001+i}", f"Operator {i+1}", ln["id"], grades[i % 3], 18 + (i % 3) * 4))
        conn.commit()

    if _empty(conn, "sf_fabric_rolls"):
        for i in range(6):
            conn.execute("INSERT OR IGNORE INTO sf_fabric_rolls (roll_no,order_id,lot,shade,length_m,width_cm,grade,status,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)",
                         (f"ROLL-{500+i}", order1_id, f"LOT-{10+i%3}", ["Indigo", "Indigo", "Dark"][i % 3],
                          round(80 + i * 6.5, 1), 150, ["A", "A", "B"][i % 3], "inspected", now))
        conn.commit()

    if _empty(conn, "sf_bundles"):
        roll = conn.execute("SELECT id FROM sf_fabric_rolls ORDER BY id LIMIT 1").fetchone()
        sizes = ["S", "M", "L", "XL"]
        stages = ["cut", "cut", "sewing", "sewing", "done"]
        for i in range(20):
            conn.execute("INSERT OR IGNORE INTO sf_bundles (bundle_no,order_id,roll_id,size,color,qty,operation_at,status,created_at) "
                         "VALUES (?,?,?,?,?,?,?,?,?)",
                         (f"BND-{2000+i}", order1_id, roll["id"] if roll else None, sizes[i % 4], "Indigo",
                          20, ["cutting", "sewing", "finishing"][i % 3], stages[i % 5], now))
        conn.commit()
