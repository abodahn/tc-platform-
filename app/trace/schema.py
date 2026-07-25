"""
Traceability / ESG / DPP — schema + seed, merged into the TC Platform DB.

Native `trc_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db. Idempotent + non-destructive: create_and_seed(conn) runs every startup,
creates missing tables, and seeds a small clearly-marked DEMO dataset only when
the module is empty.
"""
from datetime import date, timedelta

SCHEMA = """
-- A supply-chain partner at a given tier. Tier 1 = CMT, 4 = fibre origin.
CREATE TABLE IF NOT EXISTS trc_partners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tier INTEGER DEFAULT 2,
    country TEXT,
    role TEXT,                     -- CMT / Fabric mill / Spinner / Dyehouse / Fibre farm ...
    certifications TEXT,           -- free-text summary; the auditable rows live in trc_certs
    contact TEXT,
    contact_email TEXT,
    status TEXT DEFAULT 'active',
    notes TEXT,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trc_partner_tier ON trc_partners(tier);

-- One material lot = one physical batch of material. `parent_lot_id` points at
-- the lot this one was MADE FROM (fabric -> yarn -> fibre). That single column
-- is what makes fibre-to-product traceability possible; walking it upward is
-- services.lot_chain().
CREATE TABLE IF NOT EXISTS trc_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_ref TEXT,
    material TEXT,                 -- e.g. 'Single jersey 180gsm'
    fibre_composition TEXT,        -- e.g. '100% Organic Cotton' / '60% Cotton 40% rPET'
    partner_id INTEGER,            -- who produced THIS lot (trc_partners.id)
    parent_lot_id INTEGER,         -- the lot it came from (NULL = chain ends here)
    qty REAL DEFAULT 0,
    uom TEXT DEFAULT 'kg',
    country_of_origin TEXT,
    received_date TEXT,
    notes TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trc_lot_parent ON trc_lots(parent_lot_id);
CREATE INDEX IF NOT EXISTS ix_trc_lot_partner ON trc_lots(partner_id);

-- MATERIAL certificate (GOTS/OEKO-TEX/GRS/RCS/ZDHC/bluesign...) attached to a
-- partner or to a specific lot. FACTORY certificates (WRAP, BSCI, fire licence)
-- belong to app/compliance -> cmp_certs; keep the split.
CREATE TABLE IF NOT EXISTS trc_certs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    standard TEXT NOT NULL,
    cert_no TEXT,
    issuer TEXT,                   -- certification body (Control Union, Hohenstein ...)
    scope TEXT,                    -- what the certificate actually covers
    partner_id INTEGER,
    lot_id INTEGER,
    valid_from TEXT,
    valid_until TEXT,              -- drives the expiry alarm + derived status
    status TEXT DEFAULT 'valid',   -- valid / expired / revoked (dates are authoritative)
    doc_ref TEXT,
    notes TEXT,
    expiry_alerted INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trc_cert_valid ON trc_certs(valid_until);
CREATE INDEX IF NOT EXISTS ix_trc_cert_partner ON trc_certs(partner_id);
CREATE INDEX IF NOT EXISTS ix_trc_cert_lot ON trc_certs(lot_id);

-- Which material lots went into which customer order. A lot is legitimately
-- split across orders, so this is a link table, not a column on trc_lots.
-- order_id is a bare INTEGER (no FK — house convention, see app/orders).
CREATE TABLE IF NOT EXISTS trc_order_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    lot_id INTEGER NOT NULL,
    qty_used REAL DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trc_ol_order ON trc_order_lots(order_id);
CREATE INDEX IF NOT EXISTS ix_trc_ol_lot ON trc_order_lots(lot_id);

-- The Digital Product Passport header for one order: the data points that are
-- NOT derivable from the lot chain. Everything else on the passport is assembled
-- at read time by services.passport().
CREATE TABLE IF NOT EXISTS trc_passports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    country_of_origin TEXT,
    care_instructions TEXT,
    recycling_info TEXT,           -- end-of-life / take-back / recyclability
    recycled_content_pct REAL,
    notes TEXT,
    incomplete_alerted INTEGER DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trc_pass_order ON trc_passports(order_id);

-- RECORDED ESG consumption. This is metered/invoiced data divided by pieces —
-- it is NOT a certified LCA and must never be presented as one.
CREATE TABLE IF NOT EXISTS trc_esg (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER,              -- NULL = a period record for the whole site
    period TEXT,                   -- 'YYYY-MM' for period records
    label TEXT,
    energy_kwh REAL DEFAULT 0,
    water_m3 REAL DEFAULT 0,
    waste_kg REAL DEFAULT 0,
    garments REAL DEFAULT 0,       -- denominator for the per-garment intensities
    source TEXT,                   -- where the number came from (meter, invoice...)
    notes TEXT,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_trc_esg_order ON trc_esg(order_id);
"""


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _order_id(conn, order_no):
    """Demo orders may not exist (fresh DB, or orders seeded after us) — never crash."""
    try:
        r = conn.execute("SELECT id FROM ord_orders WHERE order_no=?", (order_no,)).fetchone()
        return r["id"] if r else None
    except Exception:
        return None


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    today = date.today()
    now = today.strftime("%Y-%m-%d %H:%M:%S")

    if _empty(conn, "trc_partners"):
        # --- DEMO supply chain (a real Egyptian/Turkish knit + denim chain) ---
        partners = [
            # name, tier, country, role, certifications, contact, email, status
            ("T&C Garments — Main CMT", 1, "Egypt", "CMT", "WRAP, amfori BSCI",
             "Production Office", "production@tcgarments.com", "active"),
            ("Nile Knit Mills", 2, "Egypt", "Fabric mill", "GOTS, OEKO-TEX Standard 100",
             "Sales Desk", "sales@nileknit.example", "active"),
            ("Delta Denim Weaving", 2, "Egypt", "Fabric mill", "OEKO-TEX Standard 100",
             "Mr. Fathy", "fathy@deltadenim.example", "active"),
            ("Anatolia Fleece Mill", 2, "Turkey", "Fabric mill", "GRS",
             "Export Dept.", "export@anatoliafleece.example", "active"),
            ("Alexandria Spinning Co.", 3, "Egypt", "Spinner", "GOTS, OCS",
             "Ms. Hoda", "hoda@alexspin.example", "active"),
            ("Mahalla Dyehouse", 3, "Egypt", "Dyehouse", "ZDHC, OEKO-TEX Standard 100",
             "Wet Process Mgr", "wet@mahalladye.example", "active"),
            ("Izmir rPET Recycler", 3, "Turkey", "Recycler", "GRS",
             "Ms. Elif", "elif@izmirrpet.example", "active"),
            ("Upper Egypt Cotton Growers Co-op", 4, "Egypt", "Fibre farm", "GOTS (organic cotton)",
             "Co-op Secretary", "coop@uecotton.example", "active"),
        ]
        for p in partners:
            conn.execute(
                "INSERT INTO trc_partners (name,tier,country,role,certifications,contact,"
                "contact_email,status,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (*p, now))

    pid = {}
    for r in conn.execute("SELECT id,name FROM trc_partners").fetchall():
        pid[r["name"]] = r["id"]

    if _empty(conn, "trc_lots"):
        # Seeded parent-first so each lot can point at the one before it.
        # Chain A (cotton tee) is complete to tier 4; chain B (fleece) stops at
        # tier 3; chain C (denim) has NO upstream — that spread is deliberate,
        # it makes the completeness score visibly different per order.
        lots = [
            # lot_ref, material, composition, partner, parent_ref, qty, uom, origin, days_ago
            ("LOT-FIB-0001", "Raw seed cotton, Giza 94", "100% Organic Cotton",
             "Upper Egypt Cotton Growers Co-op", None, 8200, "kg", "Egypt", 150),
            ("LOT-YRN-0101", "Combed ring-spun yarn 30/1", "100% Organic Cotton",
             "Alexandria Spinning Co.", "LOT-FIB-0001", 6400, "kg", "Egypt", 110),
            ("LOT-DYE-0201", "Reactive-dyed yarn, navy", "100% Organic Cotton",
             "Mahalla Dyehouse", "LOT-YRN-0101", 6100, "kg", "Egypt", 80),
            ("LOT-FAB-0301", "Single jersey 180 gsm", "100% Organic Cotton",
             "Nile Knit Mills", "LOT-DYE-0201", 5800, "kg", "Egypt", 55),
            ("LOT-RPT-0401", "rPET chip, post-consumer bottles", "100% Recycled Polyester",
             "Izmir rPET Recycler", None, 3000, "kg", "Turkey", 120),
            ("LOT-FAB-0501", "Brushed fleece 280 gsm", "60% Cotton / 40% Recycled Polyester",
             "Anatolia Fleece Mill", "LOT-RPT-0401", 2400, "kg", "Turkey", 60),
            ("LOT-FAB-0601", "Denim twill 11 oz", "98% Cotton / 2% Elastane",
             "Delta Denim Weaving", None, 4100, "kg", "Egypt", 45),
        ]
        ref_id = {}
        for ref, mat, comp, partner, parent_ref, qty, uom, origin, ago in lots:
            cur = conn.execute(
                "INSERT INTO trc_lots (lot_ref,material,fibre_composition,partner_id,parent_lot_id,"
                "qty,uom,country_of_origin,received_date,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ref, mat, comp, pid.get(partner), ref_id.get(parent_ref), qty, uom, origin,
                 str(today - timedelta(days=ago)), now))
            ref_id[ref] = cur.lastrowid

    lid = {}
    for r in conn.execute("SELECT id,lot_ref FROM trc_lots").fetchall():
        lid[r["lot_ref"]] = r["id"]

    if _empty(conn, "trc_certs"):
        certs = [
            # standard, cert_no, issuer, scope, partner, lot_ref, from_days_ago, until_days
            ("GOTS", "CU-GOTS-880143", "Control Union", "Organic cotton — farm group",
             "Upper Egypt Cotton Growers Co-op", None, 300, 65),
            ("GOTS", "CU-GOTS-880988", "Control Union", "Organic cotton yarn — scope certificate",
             "Alexandria Spinning Co.", None, 280, 85),
            ("OEKO-TEX Standard 100", "OTS100-24-EG-0417", "Hohenstein", "Dyed knit fabric, product class II",
             "Nile Knit Mills", "LOT-FAB-0301", 200, 165),
            ("ZDHC", "ZDHC-MRSL-L3-2141", "ZDHC Gateway", "Wet processing chemical conformance",
             "Mahalla Dyehouse", None, 150, 25),          # expiring — feeds the bell + watchlist
            ("GRS", "CU-GRS-771204", "Control Union", "Recycled polyester chip",
             "Izmir rPET Recycler", "LOT-RPT-0401", 240, 120),
            ("GRS", "CU-GRS-771930", "Control Union", "Recycled-content fleece fabric",
             "Anatolia Fleece Mill", "LOT-FAB-0501", 210, 140),
            ("RCS", "CU-RCS-660012", "Control Union", "Recycled content claim — trims",
             "Delta Denim Weaving", None, 420, -12),      # already expired
        ]
        for std, no, issuer, scope, partner, lot_ref, ago, until in certs:
            conn.execute(
                "INSERT INTO trc_certs (standard,cert_no,issuer,scope,partner_id,lot_id,"
                "valid_from,valid_until,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (std, no, issuer, scope, pid.get(partner), lid.get(lot_ref),
                 str(today - timedelta(days=ago)), str(today + timedelta(days=until)),
                 "valid", now))

    # COMMIT THE MODULE'S OWN WORK BEFORE TOUCHING ord_orders.
    # Everything below reads the orders module, which may not exist yet (init
    # order is decided elsewhere). On PostgreSQL a "relation does not exist"
    # error ABORTS the whole transaction: every later statement fails and the
    # closing COMMIT degrades to a ROLLBACK — so the tables created and the rows
    # seeded above were silently thrown away, and every /trace page 500'd on a
    # missing table. Committing here makes this module's schema independent of
    # who boots first; the order-dependent seeds below are best-effort.
    conn.commit()

    # --- link the demo lots to the demo orders (skipped if orders absent) ---
    if _empty(conn, "trc_order_lots"):
        for order_no, lot_ref, qty in (("SO-1001", "LOT-FAB-0301", 3200),
                                       ("SO-1002", "LOT-FAB-0501", 1900),
                                       ("SO-1003", "LOT-FAB-0601", 2600)):
            oid = _order_id(conn, order_no)
            if oid and lid.get(lot_ref):
                conn.execute(
                    "INSERT INTO trc_order_lots (order_id,lot_id,qty_used,created_at) "
                    "VALUES (?,?,?,?)", (oid, lid[lot_ref], qty, now))

    if _empty(conn, "trc_passports"):
        # SO-1001 fully documented, SO-1002 partly, SO-1003 not at all — so the
        # completeness KPI has something honest to show.
        oid = _order_id(conn, "SO-1001")
        if oid:
            conn.execute(
                "INSERT INTO trc_passports (order_id,country_of_origin,care_instructions,"
                "recycling_info,recycled_content_pct,created_at) VALUES (?,?,?,?,?,?)",
                (oid, "Egypt", "Machine wash 30°C, do not bleach, tumble dry low, iron medium.",
                 "Single-fibre garment — recyclable through cotton mechanical recycling. "
                 "Take-back accepted at the buyer's store network.", 0, now))
        oid = _order_id(conn, "SO-1002")
        if oid:
            conn.execute(
                "INSERT INTO trc_passports (order_id,country_of_origin,recycled_content_pct,created_at) "
                "VALUES (?,?,?,?)", (oid, "Egypt", 40, now))

    if _empty(conn, "trc_esg"):
        oid = _order_id(conn, "SO-1001")
        if oid:
            conn.execute(
                "INSERT INTO trc_esg (order_id,label,energy_kwh,water_m3,waste_kg,garments,source,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (oid, "SO-1001 dyeing + sewing + finishing", 9600, 620, 420, 12000,
                 "Sub-meters + water invoice", now))
        conn.execute(
            "INSERT INTO trc_esg (period,label,energy_kwh,water_m3,waste_kg,garments,source,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (str(today)[:7], "Site total — current month", 412000, 21800, 16400, 480000,
             "Utility invoices", now))

    conn.commit()
