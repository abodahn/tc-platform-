"""
Cut room & fabric utilisation — schema + seed, merged into the TC Platform DB.

Native `cut_*` tables hanging off ord_orders.id (this codebase declares no FKs;
a child table carries order_id + an index). Idempotent + non-destructive:
create_and_seed(conn) runs on every boot, creates what is missing, and seeds a
small clearly-marked DEMO dataset only when the module is empty AND the demo
orders exist — a real order never gets fabricated lays.
"""
from datetime import date, datetime, timedelta

SCHEMA = """
-- One LAY (spread): the unit the cut room actually works in. Every fabric number
-- the factory is judged on is derived from these columns -- nothing is stored
-- pre-computed, so correcting a ply count instantly corrects the whole chain.
CREATE TABLE IF NOT EXISTS cut_lays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lay_no TEXT UNIQUE,
    order_id INTEGER,
    marker_ref TEXT,                 -- marker name/ref from the CAD system (Lectra/Gerber)
    marker_length_m REAL DEFAULT 0,
    marker_width_cm REAL DEFAULT 0,  -- the marker's own width
    fabric_width_cm REAL DEFAULT 0,  -- usable fabric width; must be >= marker width
    marker_area_m2 REAL DEFAULT 0,   -- summed pattern-piece area in the marker (from CAD)
    marker_eff_pct REAL DEFAULT 0,   -- entered efficiency, used only when area is unknown
    plies INTEGER DEFAULT 0,
    size_ratio TEXT,                 -- e.g. "S2:M3:L2:XL1" (free text, as the marker names it)
    pieces_per_ply INTEGER DEFAULT 0,-- garments in one ply of the marker
    end_allow_m REAL DEFAULT 0,      -- end loss left at EACH ply end when spreading
    actual_fabric_m REAL DEFAULT 0,  -- measured; 0 = not measured, planned is used instead
    fabric_ref TEXT,                 -- material code (warehouse wh_materials.code when linked)
    colour TEXT,
    shade_lot TEXT,                  -- dye lot: one lay must draw from ONE lot
    cut_date TEXT,
    cutter TEXT,
    status TEXT DEFAULT 'planned',
    variance_alerted INTEGER DEFAULT 0,  -- per-ORDER bell dedup, held on the order's lays
    notes TEXT,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cut_lay_order ON cut_lays(order_id);
CREATE INDEX IF NOT EXISTS ix_cut_lay_status ON cut_lays(status);

-- Which physical rolls a lay ate. Recorded for traceability and shade-lot control
-- ONLY: warehouse owns stock, so writing a row here never moves stock.
CREATE TABLE IF NOT EXISTS cut_lay_rolls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lay_id INTEGER NOT NULL,
    roll_id INTEGER,                 -- wh_rolls.id when the warehouse module is present
    roll_no TEXT,
    shade_lot TEXT,
    meters REAL DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_cut_roll_lay ON cut_lay_rolls(lay_id);
"""


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


# DEMO lays keyed by the seeded orders' style_ref. Figures are realistic
# mid-market apparel: denim short ~0.92 m/garment at 150cm, crew tee ~0.94 m at
# 160cm, hoodie ~1.98 m at 175cm; marker efficiency 85-88%.
# (marker_ref, len_m, marker_w, fabric_w, area_m2, plies, ratio, ppp, end, actual,
#  fabric_ref, colour, shade_lot, status, cut_day_offset)
_DEMO_LAYS = {
    "TC-DEN-03": [
        ("MK-DEN03-A8", 7.30, 150, 152, 9.31, 50, "S2:M3:L2:XL1", 8, 0.06, 371.5,
         "FAB-DEN-IND", "Mid indigo", "IND-330", "cut", -4),
        # the bad one: a shade change mid-spread cost ~6% end loss
        ("MK-DEN03-A8", 7.30, 150, 152, 9.31, 44, "S2:M3:L2:XL1", 8, 0.06, 341.0,
         "FAB-DEN-IND", "Mid indigo", "IND-331", "cut", -2),
    ],
    "TC-KNIT-01": [
        ("MK-KNT01-12", 11.10, 160, 168, 15.63, 40, "S3:M4:L3:XL2", 12, 0.04, 449.0,
         "FAB-JER-NVY", "Navy", "D-5108", "bundled", -6),
    ],
    "TC-FL-07": [
        # not cut yet: actual = 0, so every number falls back to the plan
        ("MK-FL07-8", 15.80, 175, 185, 24.33, 30, "S1:M3:L3:XL1", 8, 0.05, 0,
         "FAB-FLC-GRY", "Grey Melange", "F-9002", "planned", 3),
    ],
}


def _seed_rolls(conn, lay_id, shade_lot, meters, now):
    """Best-effort link to the warehouse rolls of the same shade lot. Greedy fill
    so the roll lines always add up to the lay's consumption. Silently does
    nothing when the warehouse module is absent or has no matching roll."""
    try:
        rolls = conn.execute(
            "SELECT id, roll_no, length_m FROM wh_rolls WHERE shade_lot=? ORDER BY id",
            (shade_lot,)).fetchall()
    except Exception:
        return
    left = meters
    for r in rolls:
        if left <= 0:
            break
        take = min(float(r["length_m"] or 0), left)
        if take <= 0:
            continue
        conn.execute(
            "INSERT INTO cut_lay_rolls (lay_id,roll_id,roll_no,shade_lot,meters,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (lay_id, r["id"], r["roll_no"], shade_lot, round(take, 2), now))
        left -= take


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    # Commit the DDL before any seed INSERT: init_db() wraps this call in
    # try/except+rollback, and on PostgreSQL a failing seed would take the CREATE
    # TABLEs down with it -- the tables would silently never appear on Render.
    conn.commit()
    if not _empty(conn, "cut_lays"):
        return  # already seeded / in use -- never overwrite

    try:
        orders = conn.execute(
            "SELECT id, style_ref FROM ord_orders WHERE style_ref IS NOT NULL").fetchall()
    except Exception:
        return  # orders module not present yet -- the cut room simply starts empty

    today = date.today()
    now = _now()
    for o in orders:
        for lay in _DEMO_LAYS.get(o["style_ref"], []):
            (ref, ln, mw, fw, area, plies, ratio, ppp, end, actual,
             fab, colour, shade, status, day) = lay
            cur = conn.execute(
                "INSERT INTO cut_lays (order_id,marker_ref,marker_length_m,marker_width_cm,"
                "fabric_width_cm,marker_area_m2,plies,size_ratio,pieces_per_ply,end_allow_m,"
                "actual_fabric_m,fabric_ref,colour,shade_lot,cut_date,cutter,status,notes,"
                "created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (o["id"], ref, ln, mw, fw, area, plies, ratio, ppp, end, actual, fab,
                 colour, shade, str(today + timedelta(days=day)), "Cutting In-charge",
                 status, "DEMO seed data", "seed", now))
            lid = cur.lastrowid
            # Number the lay from its own row id -- unique and collision-free.
            # A COUNT(*)+1 scheme repeats numbers after any delete and violates
            # the UNIQUE constraint on lay_no.
            conn.execute("UPDATE cut_lays SET lay_no=? WHERE id=?",
                         (f"CUT-{today.year}-{lid:05d}", lid))
            if actual:
                _seed_rolls(conn, lid, shade, actual, now)
    conn.commit()
