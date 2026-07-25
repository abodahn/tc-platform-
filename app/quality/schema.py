"""
Digital QMS — schema + seed, merged into the TC Platform DB.
Native qc_* tables. Idempotent create_and_seed(conn); a small clearly-marked
DEMO dataset is written only when the module is empty. Never destructive.
"""
from datetime import date, timedelta

from .constants import aql_plan, verdict as aql_verdict

SCHEMA = """
-- One AQL inspection of one offered lot.
CREATE TABLE IF NOT EXISTS qc_inspections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT,
    order_id INTEGER,                 -- ord_orders.id (no FK — this codebase declares none)
    stage TEXT,                       -- where the units were counted
    lot_size REAL DEFAULT 0,          -- offered lot; drives the code letter
    aql REAL DEFAULT 2.5,
    -- The plan is FROZEN on the row: a buyer re-reading this decision two years
    -- later must see the plan that was actually applied, not today's lookup.
    code_letter TEXT,
    sample_size REAL DEFAULT 0,
    accept_no REAL DEFAULT 0,         -- Ac
    reject_no REAL DEFAULT 0,         -- Re (= Ac + 1)
    units_inspected REAL DEFAULT 0,
    defective_units REAL DEFAULT 0,   -- units with >=1 defect; Z1.4 judges UNITS, not defects
    verdict TEXT DEFAULT 'pending',   -- computed from the plan, never typed in
    inspector TEXT,
    inspection_date TEXT,
    notes TEXT,
    fail_alerted INTEGER DEFAULT 0,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_qc_insp_order ON qc_inspections(order_id);
CREATE INDEX IF NOT EXISTS ix_qc_insp_verdict ON qc_inspections(verdict);
CREATE INDEX IF NOT EXISTS ix_qc_insp_date ON qc_inspections(inspection_date);

-- One defect line found during an inspection (several per unit is normal).
CREATE TABLE IF NOT EXISTS qc_defects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inspection_id INTEGER NOT NULL,
    defect_type TEXT NOT NULL,
    section TEXT,                     -- where the fault was CREATED (the Pareto axis)
    qty REAL DEFAULT 1,
    severity TEXT DEFAULT 'minor',    -- critical / major / minor
    notes TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_qc_def_insp ON qc_defects(inspection_id);
CREATE INDEX IF NOT EXISTS ix_qc_def_type ON qc_defects(defect_type);
"""

# DEMO ONLY — realistic garment inspections so the dashboards are not blank.
# (stage, lot, aql, units, defective, days_ago, notes, [(defect, section, qty, severity)...])
_DEMO = [
    # Lot sizes are chosen so the demo units ARE the full sample: a demo row that
    # "passes" on a part-inspected sample would contradict the module's own rule.
    ("final", 8000, 2.5, 200, 6, 2, "Buyer final — carton pull",
     [("Broken stitch", "sewing", 3, "major"), ("Loose thread", "finishing", 2, "minor"),
      ("Stain / soil mark", "finishing", 2, "major"), ("Measurement out of tolerance", "cutting", 1, "major")]),
    ("sewing_inline", 3000, 2.5, 125, 9, 3, "Line 4 inline — hourly patrol",
     [("Skipped stitch", "sewing", 5, "major"), ("Open seam", "sewing", 4, "critical"),
      ("Puckering", "sewing", 3, "minor")]),
    ("finishing", 6500, 2.5, 200, 4, 5, "Post-press audit",
     [("Stain / soil mark", "washing", 3, "major"), ("Loose thread", "finishing", 2, "minor"),
      ("Pressing mark", "finishing", 1, "minor")]),
    ("end_line", 9000, 2.5, 200, 11, 1, "End-of-line 100% follow-up triggered",
     [("Open seam", "sewing", 6, "critical"), ("Broken stitch", "sewing", 4, "major"),
      ("Uneven hem", "sewing", 3, "major"), ("Shading", "fabric", 2, "major")]),
    ("cutting", 9000, 4.0, 200, 5, 6, "Cut panel audit — bundle sample",
     [("Mis-cut panel", "cutting", 3, "major"), ("Fabric hole", "fabric", 2, "critical"),
      ("Shading", "fabric", 2, "major")]),
    ("pre_final", 6500, 2.5, 0, 0, 0, "Booked — inspection not started", []),
]


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    # Commit the DDL before any seed INSERT: init_db() wraps this call in
    # try/except-rollback, and on PostgreSQL a failing seed would otherwise roll
    # the tables back out too, silently.
    conn.commit()
    if not _empty(conn, "qc_inspections"):
        return
    today = date.today()
    now = today.strftime("%Y-%m-%d %H:%M:%S")
    orders = conn.execute("SELECT id FROM ord_orders ORDER BY id LIMIT 3").fetchall()
    oids = [o["id"] for o in orders]                     # may be empty — orders are optional
    for i, (stage, lot, aql, units, defective, ago, note, defects) in enumerate(_DEMO):
        p = aql_plan(lot, aql)
        # Verdict from the units ACTUALLY counted, not from an assumed full sample.
        v = aql_verdict(p["accept"], p["sample_size"], units, defective)
        cur = conn.execute(
            "INSERT INTO qc_inspections (order_id,stage,lot_size,aql,code_letter,sample_size,"
            "accept_no,reject_no,units_inspected,defective_units,verdict,inspector,inspection_date,"
            "notes,fail_alerted,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oids[i % len(oids)] if oids else None, stage, lot, aql, p["code_letter"],
             p["sample_size"], p["accept"], p["reject"], units, defective,
             v, "QC Inspector", str(today - timedelta(days=ago)),
             note, 1, "seed", now))          # fail_alerted=1: don't bell demo rows on first boot
        iid = cur.lastrowid
        conn.execute("UPDATE qc_inspections SET ref=? WHERE id=?", ("QC-%05d" % iid, iid))
        for dtype, section, qty, sev in defects:
            conn.execute(
                "INSERT INTO qc_defects (inspection_id,defect_type,section,qty,severity,created_at) "
                "VALUES (?,?,?,?,?,?)", (iid, dtype, section, qty, sev, now))
