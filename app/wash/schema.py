"""
Wash / finishing recipe library — schema + seed, merged into the TC Platform DB.

Native `wsh_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db (conn.executescript / INSERT OR IGNORE). Idempotent + non-destructive:
create_and_seed(conn) runs every startup, creates missing tables, and seeds a
small clearly-marked DEMO dataset only when the module is empty.
"""
from datetime import date, datetime, timedelta

SCHEMA = """
-- A recipe FAMILY. Steps and chemicals never hang off this row: they belong to
-- a version, so an approved version stays untouched once bulk has run on it.
CREATE TABLE IF NOT EXISTS wsh_recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE,
    name TEXT NOT NULL,
    style_ref TEXT,
    wash_type TEXT DEFAULT 'rinse',
    order_id INTEGER,              -- optional link to ord_orders.id (no FK, house style)
    notes TEXT,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wsh_recipe_order ON wsh_recipes(order_id);

-- One version of a recipe. A new version SNAPSHOTS its source's steps and
-- chemical lines into fresh rows -- it never shares them.
CREATE TABLE IF NOT EXISTS wsh_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT DEFAULT 'draft',   -- draft / approved / retired
    created_by TEXT, created_at TEXT,
    approved_by TEXT, approved_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wsh_ver_recipe ON wsh_versions(recipe_id, version);

-- One ordered step of a version's cycle.
CREATE TABLE IF NOT EXISTS wsh_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    step_no INTEGER DEFAULT 1,
    operation TEXT NOT NULL,
    temp_c REAL DEFAULT 0,
    minutes REAL DEFAULT 0,
    liquor_ratio REAL DEFAULT 0,   -- litres of bath per kg of goods; 0 = dry step, no bath
    load_kg REAL DEFAULT 0,        -- machine load for this bath
    notes TEXT
);
CREATE INDEX IF NOT EXISTS ix_wsh_step_ver ON wsh_steps(version_id, step_no);

-- A dosing line on a step. Either g/L of bath or % on weight of goods (or both).
CREATE TABLE IF NOT EXISTS wsh_chemicals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    gpl REAL DEFAULT 0,            -- grams per litre of bath
    owg_pct REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_wsh_chem_step ON wsh_chemicals(step_id);

-- An executed lot. version_id (NOT recipe_id) is the traceability key: it is
-- what lets the same shade be re-run months later after the recipe moved on.
CREATE TABLE IF NOT EXISTS wsh_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_no TEXT UNIQUE,
    recipe_id INTEGER,
    version_id INTEGER NOT NULL,
    order_id INTEGER,
    machine TEXT,
    load_kg REAL DEFAULT 0,
    operator TEXT,
    started_at TEXT, ended_at TEXT,
    act_minutes REAL DEFAULT 0,    -- actual cycle time achieved
    act_temp_c REAL DEFAULT 0,     -- actual peak bath temperature
    act_water_l REAL DEFAULT 0,    -- metered water, when the machine reports it
    shade TEXT,
    status TEXT DEFAULT 'done',
    deviation TEXT,                -- human summary of out-of-tolerance actuals
    deviation_alerted INTEGER DEFAULT 0,
    notes TEXT,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wsh_batch_ver ON wsh_batches(version_id);
CREATE INDEX IF NOT EXISTS ix_wsh_batch_order ON wsh_batches(order_id);

-- Shade / lab-dip sign-off against a version (optionally against one batch).
CREATE TABLE IF NOT EXISTS wsh_labdips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    batch_id INTEGER,
    reference TEXT,                -- buyer/lab reference of the physical swatch
    verdict TEXT DEFAULT 'pending',
    approver TEXT, verdict_date TEXT,
    notes TEXT,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_wsh_dip_ver ON wsh_labdips(version_id, verdict);
"""


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _order_ids(conn):
    """style_ref -> ord_orders.id, probed ONCE before any seed INSERT.

    The orders module may not exist yet (its create_and_seed may run after this
    one). On PostgreSQL a missing table aborts the whole transaction, so the
    rollback that clears it has to happen while there is nothing to lose — i.e.
    right after the DDL commit, not part-way through the inserts."""
    try:
        rows = conn.execute("SELECT id, style_ref FROM ord_orders ORDER BY id").fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return {}
    out = {}
    for r in rows:
        out.setdefault(r["style_ref"], r["id"])
    return out


def _add_version(conn, recipe_id, version, status, steps, now, approved=False):
    cur = conn.execute(
        "INSERT INTO wsh_versions (recipe_id,version,status,created_by,created_at,"
        "approved_by,approved_at) VALUES (?,?,?,?,?,?,?)",
        (recipe_id, version, status, "seed", now,
         "seed" if approved else None, now if approved else None))
    vid = cur.lastrowid
    for i, (op, temp, mins, lr, load, chems) in enumerate(steps, start=1):
        scur = conn.execute(
            "INSERT INTO wsh_steps (version_id,step_no,operation,temp_c,minutes,"
            "liquor_ratio,load_kg) VALUES (?,?,?,?,?,?,?)",
            (vid, i, op, temp, mins, lr, load))
        for cname, gpl, owg in chems:
            conn.execute("INSERT INTO wsh_chemicals (step_id,name,gpl,owg_pct) VALUES (?,?,?,?)",
                         (scur.lastrowid, cname, gpl, owg))
    return vid


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    conn.commit()          # DDL landed before any probe that might abort the tx
    if not _empty(conn, "wsh_recipes"):
        return

    today = date.today()
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    yr = today.year
    orders = _order_ids(conn)

    # --- DEMO recipe 1: stone wash, two versions (v1 retired, v2 approved) ---
    # Shows the whole point of the module: v1's steps survive v2 untouched.
    cur = conn.execute(
        "INSERT INTO wsh_recipes (code,name,style_ref,wash_type,order_id,notes,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("WR-STN-01", "Stone wash — medium contrast", "TC-DEN-03", "stone",
         orders.get("TC-DEN-03"), "DEMO seed data.", "seed", now))
    r1 = cur.lastrowid
    # Pumice is abrasive media, not a dosing chemical — it is not a chemical line,
    # so it does not distort the chemical-intensity index.
    v1_steps = [
        ("desize",     60, 15, 8, 120, [("Desizing enzyme", 0.8, 0)]),
        ("stone",      45, 45, 6, 120, [("Anti-back-stain", 1.0, 0)]),
        ("neutralise", 40, 10, 8, 120, [("Acetic acid", 0.5, 0)]),
        ("softener",   40, 15, 8, 120, [("Cationic softener", 0, 2.0)]),
        ("dry",        70, 45, 0, 120, []),
    ]
    _add_version(conn, r1, 1, "retired", v1_steps, now)
    v2_steps = [
        ("desize",     55, 12, 6, 120, [("Desizing enzyme", 0.8, 0)]),
        ("stone",      45, 35, 5, 120, [("Anti-back-stain", 1.0, 0)]),
        ("neutralise", 40, 10, 6, 120, [("Acetic acid", 0.5, 0)]),
        ("softener",   40, 15, 6, 120, [("Cationic softener", 0, 2.0)]),
        ("dry",        70, 40, 0, 120, []),
    ]
    v2 = _add_version(conn, r1, 2, "approved", v2_steps, now, approved=True)
    conn.execute("INSERT INTO wsh_labdips (version_id,reference,verdict,approver,verdict_date,"
                 "notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (v2, "LD-DEN03-A", "approved", "seed", str(today - timedelta(days=12)),
                  "Shade matched standard within tolerance.", "seed", now))

    # --- DEMO recipe 2: enzyme light, approved ---
    cur = conn.execute(
        "INSERT INTO wsh_recipes (code,name,style_ref,wash_type,order_id,notes,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("WR-ENZ-02", "Enzyme light — soft hand", "TC-DEN-03", "enzyme",
         orders.get("TC-DEN-03"), "DEMO seed data.", "seed", now))
    r2 = cur.lastrowid
    e_steps = [
        ("rinse",    30, 10, 8, 100, []),
        ("enzyme",   50, 30, 6, 100, [("Neutral cellulase", 0, 1.5)]),
        ("neutralise", 40, 10, 6, 100, [("Soda ash", 0.4, 0)]),
        ("softener", 40, 15, 6, 100, [("Silicone softener", 0, 1.8)]),
        ("dry",      65, 35, 0, 100, []),
    ]
    v_enz = _add_version(conn, r2, 1, "approved", e_steps, now, approved=True)
    conn.execute("INSERT INTO wsh_labdips (version_id,reference,verdict,approver,verdict_date,"
                 "notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (v_enz, "LD-ENZ-01", "approved", "seed", str(today - timedelta(days=30)),
                  "Approved for bulk.", "seed", now))

    # --- DEMO recipe 3: rinse only, still draft with a lab dip waiting ---
    cur = conn.execute(
        "INSERT INTO wsh_recipes (code,name,style_ref,wash_type,order_id,notes,created_by,created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("WR-RNS-03", "Rinse wash — dark indigo", "TC-KNIT-01", "rinse",
         orders.get("TC-KNIT-01"), "DEMO seed data.", "seed", now))
    r3 = cur.lastrowid
    v_rns = _add_version(conn, r3, 1, "draft", [
        ("rinse",    30, 12, 8, 80, [("Anti-back-stain", 1.0, 0)]),
        ("softener", 40, 12, 8, 80, [("Cationic softener", 0, 1.5)]),
        ("extract",   0,  5, 0, 80, []),
        ("dry",      65, 30, 0, 80, []),
    ], now)
    conn.execute("INSERT INTO wsh_labdips (version_id,reference,verdict,notes,created_by,created_at) "
                 "VALUES (?,?,?,?,?,?)",
                 (v_rns, "LD-RNS-03", "pending", "Awaiting buyer shade sign-off.", "seed", now))

    # --- DEMO batches against the APPROVED versions ---
    # deviation_alerted is pre-set to 1 so seeding never floods the bell at boot.
    demo_batches = [
        # version, order style, machine, load, operator, act_min, act_temp, shade, days_ago, deviation
        (v2, "TC-DEN-03", "Washer 1", 118, "OP-1004", 112, 55, "Standard", 1, None),
        (v2, "TC-DEN-03", "Washer 2", 120, "OP-1011", 140, 55, "Standard", 0,
         "time 140 vs 112 min (25%)"),
        (v_enz, "TC-DEN-03", "Washer 3", 98, "OP-1007", 100, 52, "Light", 0, None),
    ]
    for vid, style, mach, load, op, mins, temp, shade, ago, dev in demo_batches:
        d = today - timedelta(days=ago)
        rid = conn.execute("SELECT recipe_id FROM wsh_versions WHERE id=?", (vid,)).fetchone()["recipe_id"]
        bcur = conn.execute(
            "INSERT INTO wsh_batches (recipe_id,version_id,order_id,machine,load_kg,operator,"
            "started_at,ended_at,act_minutes,act_temp_c,shade,status,deviation,deviation_alerted,"
            "created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rid, vid, orders.get(style), mach, load, op, f"{d} 07:30", f"{d} 09:30",
             mins, temp, shade, "done", dev, 1, "seed", now))
        conn.execute("UPDATE wsh_batches SET batch_no=? WHERE id=?",
                     (f"WB-{yr}-{bcur.lastrowid:05d}", bcur.lastrowid))
    conn.commit()
