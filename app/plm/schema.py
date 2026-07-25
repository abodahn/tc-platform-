"""
PLM-lite — schema + seed, merged into the TC Platform DB.

Native `plm_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db. Idempotent + non-destructive: create_and_seed(conn) runs every startup,
creates missing tables and seeds a small clearly-marked DEMO dataset only when
the module is empty.

Styles link to customer orders by `style_ref` (ord_orders.style_ref) — a plain
text join read at display time, so no import and no FK is needed and the demo
orders light up automatically.
"""
from datetime import date, timedelta

SCHEMA = """
-- The single source of style truth. style_ref is the key orders quote.
CREATE TABLE IF NOT EXISTS plm_styles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_ref TEXT NOT NULL,            -- matches ord_orders.style_ref
    name TEXT,
    buyer TEXT,
    season TEXT,
    category TEXT,                      -- product type (T-shirt, Denim, ...)
    fabric TEXT,                        -- headline fabric description
    description TEXT,
    status TEXT DEFAULT 'development',  -- development/sampling/approved/in_production/dropped
    designer TEXT,
    merchandiser TEXT,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_plm_style_ref ON plm_styles(style_ref);
CREATE INDEX IF NOT EXISTS ix_plm_style_status ON plm_styles(status);

-- One immutable tech-pack VERSION of a style. Publishing a new version copies
-- (snapshots) the previous version's content; old versions are never edited.
CREATE TABLE IF NOT EXISTS plm_techpacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    change_note TEXT,
    published_by TEXT,
    published_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_plm_tp_version ON plm_techpacks(style_id, version);

-- Free-text section of one tech-pack version.
CREATE TABLE IF NOT EXISTS plm_techpack_sections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    techpack_id INTEGER NOT NULL,
    seq INTEGER DEFAULT 0,
    title TEXT,
    body TEXT
);
CREATE INDEX IF NOT EXISTS ix_plm_sec_tp ON plm_techpack_sections(techpack_id);

-- Measurement spec line of one tech-pack version (point of measure x size).
CREATE TABLE IF NOT EXISTS plm_techpack_specs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    techpack_id INTEGER NOT NULL,
    seq INTEGER DEFAULT 0,
    pom TEXT,                           -- point of measure, e.g. "Chest width 1cm below armhole"
    size TEXT,
    spec_value REAL DEFAULT 0,          -- cm
    tolerance REAL DEFAULT 0            -- +/- cm
);
CREATE INDEX IF NOT EXISTS ix_plm_spec_tp ON plm_techpack_specs(techpack_id);

-- Style BOM master: the DEFAULT material lines a per-order BOM is seeded from.
CREATE TABLE IF NOT EXISTS plm_bom (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_id INTEGER NOT NULL,
    material TEXT NOT NULL,
    kind TEXT DEFAULT 'fabric',         -- fabric / trim / other
    placement TEXT,
    consumption REAL DEFAULT 0,         -- net per finished unit
    unit TEXT DEFAULT 'm',
    wastage_pct REAL DEFAULT 0,         -- gross = consumption * (1 + wastage_pct/100)
    supplier TEXT,
    colour TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_plm_bom_style ON plm_bom(style_id);

-- One sample round sent to the buyer (proto -> fit -> size set -> SMS -> PP -> TOP).
CREATE TABLE IF NOT EXISTS plm_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    style_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    round_no INTEGER DEFAULT 1,         -- nth send of this stage
    revision_no INTEGER DEFAULT 0,      -- how many times this stage came back rejected/revise
    sent_date TEXT,
    comments TEXT,                      -- buyer comments
    verdict TEXT DEFAULT 'pending',     -- pending / approved / rejected / revise
    decided_at TEXT,
    created_by TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_plm_sample_style ON plm_samples(style_id, stage);
"""


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    conn.commit()          # keep the tables even if the optional index below cannot be built
    _ci_unique_ref(conn)
    if _empty(conn, "plm_styles"):
        _seed(conn)
    conn.commit()


def _ci_unique_ref(conn):
    """style_ref uniqueness has to be CASE-INSENSITIVE **at the database**, not only
    in services.create_style: that check is a SELECT followed by an INSERT, so two
    concurrent creates of "TC-X" and "tc-x" both pass it and the plain unique index
    on style_ref does not stop them. Two rows for one style split its tech pack and
    BOM in half, and bom_for_order()'s LOWER() lookup then seeds a purchase from
    whichever half it happens to read first.

    Kept out of SCHEMA and tolerant of failure on purpose: a database that somehow
    already holds a case-duplicate must not fail the whole module's boot."""
    try:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_plm_style_ref_ci "
                     "ON plm_styles (LOWER(style_ref))")
        conn.commit()
    except Exception:
        conn.rollback()


def _seed(conn):
    """DEMO dataset only. style_refs deliberately match the seeded demo orders
    (TC-KNIT-01 / TC-FL-07 / TC-DEN-03) so a style page shows real linked orders."""
    today = date.today()
    now = today.strftime("%Y-%m-%d %H:%M:%S")
    styles = [
        # style_ref, name, buyer, season, category, fabric, description, status, designer, merch
        ("TC-KNIT-01", "Men's Crew Tee", "EU Buyer A", "SS26", "T-shirt",
         "180gsm single jersey, 100% combed cotton", "Regular fit crew neck, side seamed, twill tape at back neck.",
         "in_production", "N. Farouk", "H. Yilmaz"),
        ("TC-FL-07", "Fleece Hoodie", "UK Retailer", "AW26", "Hoodie / Sweatshirt",
         "320gsm brushed back fleece, 80/20 CO/PE", "Kangaroo pocket, jersey-lined hood, ribbed cuffs and hem.",
         "sampling", "N. Farouk", "M. Said"),
        ("TC-DEN-03", "Denim Short", "US Brand", "SS26", "Denim",
         "10.5oz rigid denim", "5-pocket short, stone + enzyme wash, branded leather patch.",
         "sampling", "L. Demir", "M. Said"),
    ]
    ids = {}
    for s in styles:
        # updated_at is set too: it is the ORDER BY key, and PostgreSQL sorts NULLs
        # FIRST on DESC, which would park the demo styles above every real one.
        cur = conn.execute(
            "INSERT INTO plm_styles (style_ref,name,buyer,season,category,fabric,description,"
            "status,designer,merchandiser,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (*s, "seed", now, now))
        ids[s[0]] = cur.lastrowid

    # --- tech packs: the tee has two versions so the history panel is alive ---
    tee = ids["TC-KNIT-01"]
    v1 = _tp(conn, tee, 1, "Initial tech pack issued to buyer.", str(today - timedelta(days=45)))
    _sections(conn, v1, [
        ("Construction & seams", "Overlock side seams, coverstitch hem 20mm, 1x1 rib neck 20mm."),
        ("Fabric & trims", "180gsm single jersey. Woven main label at back neck, care label side seam."),
        ("Labels, care & packing", "Poly bag per piece, 10 pcs per inner, size ratio S-M-L-XL 1-2-2-1."),
    ])
    _specs(conn, v1, [
        ("Chest width 1cm below armhole", "M", 52.0, 1.0),
        ("Body length from HPS", "M", 71.0, 1.0),
        ("Sleeve length from shoulder", "M", 21.0, 0.5),
    ])
    v2 = _tp(conn, tee, 2, "Buyer fit comments: chest +1cm, body +1cm from M.",
             str(today - timedelta(days=18)))
    _sections(conn, v2, [
        ("Construction & seams", "Overlock side seams, coverstitch hem 20mm, 1x1 rib neck 20mm."),
        ("Fabric & trims", "180gsm single jersey. Woven main label at back neck, care label side seam."),
        ("Labels, care & packing", "Poly bag per piece, 10 pcs per inner, size ratio S-M-L-XL 1-2-2-1."),
        ("Artwork / print / embroidery", "Chest print 200x120mm, water-based ink, placement 80mm below HPS."),
    ])
    _specs(conn, v2, [
        ("Chest width 1cm below armhole", "M", 53.0, 1.0),
        ("Body length from HPS", "M", 72.0, 1.0),
        ("Sleeve length from shoulder", "M", 21.0, 0.5),
    ])
    hood = ids["TC-FL-07"]
    hv1 = _tp(conn, hood, 1, "First issue.", str(today - timedelta(days=12)))
    _sections(conn, hv1, [
        ("Construction & seams", "Flatlock shoulder, 2-needle armhole, ribbed cuff and hem 60mm."),
        ("Fabric & trims", "320gsm brushed fleece, 8mm flat drawcord with metal tips."),
    ])
    _specs(conn, hv1, [("Chest width 1cm below armhole", "L", 61.0, 1.5),
                       ("Body length from HPS", "L", 70.0, 1.5)])

    # --- style BOM masters ---
    _bom(conn, tee, now, [
        ("180gsm single jersey — combed cotton", "fabric", "Body/sleeve", 0.42, "kg", 8.0, "Nile Knitting", "White"),
        ("1x1 rib 200gsm", "fabric", "Neck rib", 0.02, "kg", 10.0, "Nile Knitting", "White"),
        ("Sewing thread 40/2", "trim", "All seams", 0.15, "cone", 3.0, "Coats", "White"),
        ("Woven main label", "trim", "Back neck", 1.0, "pcs", 2.0, "Delta Labels", "—"),
        ("Poly bag + carton share", "other", "Packing", 1.0, "pcs", 1.0, "PackCo", "—"),
    ])
    _bom(conn, hood, now, [
        ("320gsm brushed fleece", "fabric", "Body/sleeve/hood", 1.05, "kg", 9.0, "Delta Knits", "Navy"),
        ("2x2 rib 280gsm", "fabric", "Cuff/hem", 0.08, "kg", 10.0, "Delta Knits", "Navy"),
        ("Flat drawcord 8mm", "trim", "Hood", 1.4, "m", 5.0, "Trim House", "Navy"),
        ("Metal eyelet", "trim", "Hood", 2.0, "pcs", 4.0, "Trim House", "Antique"),
    ])
    _bom(conn, ids["TC-DEN-03"], now, [
        ("10.5oz rigid denim", "fabric", "Body", 1.25, "m", 7.0, "Mahalla Denim", "Indigo"),
        ("Pocketing 110gsm", "fabric", "Pockets", 0.18, "m", 6.0, "Mahalla Denim", "Ecru"),
        ("Zipper 12cm YKK", "trim", "Fly", 1.0, "pcs", 2.0, "YKK", "Antique brass"),
        ("Jeans button 17mm", "trim", "Waistband", 1.0, "pcs", 3.0, "YKK", "Antique brass"),
        ("Rivet 9mm", "trim", "Pockets", 6.0, "pcs", 5.0, "YKK", "Antique brass"),
    ])

    # --- sample rounds ---
    _samples(conn, tee, now, [
        ("proto", 1, 0, str(today - timedelta(days=52)), "Shape approved, chest slightly tight.", "revise"),
        # revision_no = prior rejected/revise rounds OF THIS STAGE, so a first
        # round is always 0 (services.record_sample computes it the same way).
        ("fit", 1, 0, str(today - timedelta(days=30)), "Chest +1cm, body +1cm.", "revise"),
        ("fit", 2, 1, str(today - timedelta(days=20)), "Fit approved.", "approved"),
        ("size_set", 1, 0, str(today - timedelta(days=14)), "Graded measurements OK.", "approved"),
        ("pp", 1, 0, str(today - timedelta(days=8)), "PP approved — proceed to bulk.", "approved"),
    ])
    _samples(conn, hood, now, [
        ("proto", 1, 0, str(today - timedelta(days=10)), "Hood too shallow, drawcord too short.", "rejected"),
        ("proto", 2, 1, str(today - timedelta(days=2)), "Awaiting buyer comments.", "pending"),
    ])
    _samples(conn, ids["TC-DEN-03"], now, [
        ("proto", 1, 0, str(today - timedelta(days=21)), "Approved.", "approved"),
        ("fit", 1, 0, str(today - timedelta(days=9)), "Waist 2cm over spec.", "rejected"),
        ("fit", 2, 1, str(today - timedelta(days=3)), "Awaiting comments.", "pending"),
    ])


def _tp(conn, style_id, version, note, when):
    cur = conn.execute(
        "INSERT INTO plm_techpacks (style_id,version,change_note,published_by,published_at) "
        "VALUES (?,?,?,?,?)", (style_id, version, note, "seed", when))
    return cur.lastrowid


def _sections(conn, tp_id, rows):
    for i, (title, body) in enumerate(rows, start=1):
        conn.execute("INSERT INTO plm_techpack_sections (techpack_id,seq,title,body) VALUES (?,?,?,?)",
                     (tp_id, i, title, body))


def _specs(conn, tp_id, rows):
    for i, (pom, size, val, tol) in enumerate(rows, start=1):
        conn.execute("INSERT INTO plm_techpack_specs (techpack_id,seq,pom,size,spec_value,tolerance) "
                     "VALUES (?,?,?,?,?,?)", (tp_id, i, pom, size, val, tol))


def _bom(conn, style_id, now, rows):
    for r in rows:
        conn.execute(
            "INSERT INTO plm_bom (style_id,material,kind,placement,consumption,unit,wastage_pct,"
            "supplier,colour,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (style_id, *r, now))


def _samples(conn, style_id, now, rows):
    for stage, rnd, rev, sent, comments, verdict in rows:
        conn.execute(
            "INSERT INTO plm_samples (style_id,stage,round_no,revision_no,sent_date,comments,"
            "verdict,decided_at,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (style_id, stage, rnd, rev, sent, comments, verdict,
             (now if verdict != "pending" else None), "seed", now))
