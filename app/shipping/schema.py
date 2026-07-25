"""
Shipments & export documents — schema + seed, merged into the TC Platform DB.

Native `shp_*` tables. DDL is SQLite-authored and translated for PostgreSQL by
app.db. Idempotent + non-destructive: create_and_seed(conn) runs at every boot,
creates missing tables and seeds a small clearly-marked DEMO dataset only when
the module is empty. The demo shipments attach to the seeded demo orders when
ord_orders is present, and stand alone (order_id NULL) when it is not.
"""
from datetime import date, timedelta

SCHEMA = """
-- One export shipment against a customer order — the order's last mile.
CREATE TABLE IF NOT EXISTS shp_shipments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_no TEXT,
    order_id INTEGER,                  -- ord_orders.id; bare int, no FK (house rule)
    buyer TEXT,
    destination TEXT,                  -- port / city of discharge
    port_loading TEXT,
    incoterm TEXT DEFAULT 'FOB',
    mode TEXT DEFAULT 'sea',
    carrier TEXT,                      -- forwarder / shipping line / airline
    container_no TEXT,                 -- container no (sea) or AWB / CMR (air, road)
    etd TEXT, eta TEXT,
    status TEXT DEFAULT 'planned',
    dispatched_at TEXT,                -- stamped the first time the shipment leaves; survives a
                                       -- later cancel, so "has it ever left" cannot be erased
                                       -- by flipping the status through 'cancelled'
    invoice_no TEXT,
    invoice_date TEXT,
    currency TEXT DEFAULT 'USD',
    -- SNAPSHOT of the order price at creation: an issued invoice must not move when the
    -- order is repriced. DOUBLE PRECISION, not REAL: PostgreSQL's REAL is a 4-byte float
    -- (~7 significant digits), which would round the money column on every store.
    unit_price DOUBLE PRECISION DEFAULT 0,
    lc_ref TEXT,                       -- letter of credit / payment reference
    notes TEXT,
    created_by TEXT, created_at TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_shp_order ON shp_shipments(order_id);
CREATE INDEX IF NOT EXISTS ix_shp_etd ON shp_shipments(etd);
CREATE INDEX IF NOT EXISTS ix_shp_status ON shp_shipments(status);

-- One packing-list line: N identical cartons of one style/colour/size.
-- Weights and dimensions are PER CARTON (that is how a packing list is written).
CREATE TABLE IF NOT EXISTS shp_cartons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shipment_id INTEGER NOT NULL,
    carton_no TEXT,                    -- carton number/range as printed on the boxes
    style TEXT, colour TEXT, size TEXT,
    qty_per_carton REAL DEFAULT 0,
    cartons INTEGER DEFAULT 0,
    net_weight REAL DEFAULT 0,         -- kg per carton
    gross_weight REAL DEFAULT 0,       -- kg per carton
    length_cm REAL DEFAULT 0,
    width_cm REAL DEFAULT 0,
    height_cm REAL DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_shp_carton_ship ON shp_cartons(shipment_id);

-- One bell alert per order per flag — what keeps the reconciliation sweep idempotent.
CREATE TABLE IF NOT EXISTS shp_recon_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL,
    flag TEXT NOT NULL,                -- short | over
    created_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_shp_recon ON shp_recon_alerts(order_id, flag);
"""


# Columns added after the table first existed — idempotent ALTERs so an already-created
# database picks them up (same pattern as app/approvals/schema.py).
_MIGRATIONS = [
    ("dispatched_at", "ALTER TABLE shp_shipments ADD COLUMN dispatched_at TEXT"),
]


def _empty(conn, t):
    try:
        return conn.execute(f"SELECT COUNT(*) AS c FROM {t}").fetchone()["c"] == 0
    except Exception:
        return False


def _order(conn, order_no):
    """Look up a seeded demo order. Returns None when the orders module is absent."""
    try:
        r = conn.execute("SELECT id,buyer,style_ref,style_name,unit_price,currency "
                         "FROM ord_orders WHERE order_no=?", (order_no,)).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


# DEMO shipments: (order_no, fallback buyer, dest, loading, incoterm, mode, carrier,
#                  container, etd offset, eta offset, status, invoice_no, lc, price)
# SO-1001 is deliberately SHORT shipped and SO-1003 deliberately OVER shipped so the
# reconciliation page and its chargeback flags are visibly alive on a fresh install.
_DEMO = [
    ("SO-1001", "EU Buyer A", "Hamburg, DE", "Alexandria, EG", "FOB", "sea",
     "Maersk / Kuehne+Nagel", "MSKU-7741208", -3, 21, "dispatched",
     "INV-2601", "LC-DEU-88123", 3.85),
    ("SO-1003", "US Brand", "New York, US", "Alexandria, EG", "CIF", "sea",
     "CMA CGM / DSV", "CMAU-3390142", -1, 26, "dispatched",
     "INV-2602", "TT-30D", 6.40),
    ("SO-1002", "UK Retailer", "Felixstowe, UK", "Alexandria, EG", "DDP", "air",
     "Turkish Cargo", "235-88104417", 9, 12, "planned", None, "LC-UK-55010", 9.20),
]

# carton lines per demo shipment index:
# (carton_no, style, colour, size, qty/ctn, cartons, net kg, gross kg, L, W, H cm)
_DEMO_CARTONS = {
    0: [("1-50", "TC-KNIT-01", "Black", "M", 60, 50, 12.5, 13.4, 60, 40, 30),
        ("51-95", "TC-KNIT-01", "Black", "L", 60, 45, 13.1, 14.0, 60, 40, 30),
        ("96-145", "TC-KNIT-01", "White", "M", 60, 50, 12.5, 13.4, 60, 40, 30),
        ("146-190", "TC-KNIT-01", "White", "L", 60, 45, 13.1, 14.0, 60, 40, 30)],
    1: [("1-120", "TC-DEN-03", "Indigo", "32", 40, 120, 18.0, 19.2, 60, 40, 35),
        ("121-231", "TC-DEN-03", "Stone", "34", 40, 111, 18.4, 19.6, 60, 40, 35)],
    2: [("1-125", "TC-FL-07", "Navy", "L", 24, 125, 15.6, 16.8, 60, 40, 40)],
}


def create_and_seed(conn):
    conn.executescript(SCHEMA)
    # Commit the DDL before anything that may fail: on PostgreSQL a failed statement
    # aborts the whole transaction, and _order() below probes a table that may not exist.
    conn.commit()
    for _col, _ddl in _MIGRATIONS:
        try:
            conn.execute(_ddl)
            conn.commit()
        except Exception:
            conn.rollback()
    if _empty(conn, "shp_shipments"):
        today = date.today()
        now = today.strftime("%Y-%m-%d %H:%M:%S")
        for i, (ono, buyer, dest, pol, inco, mode, carrier, cont,
                etd_off, eta_off, status, inv, lc, price) in enumerate(_DEMO):
            o = _order(conn, ono)
            cur = conn.execute(
                "INSERT INTO shp_shipments (order_id,buyer,destination,port_loading,incoterm,"
                "mode,carrier,container_no,etd,eta,status,dispatched_at,invoice_no,invoice_date,"
                "currency,unit_price,lc_ref,created_by,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ((o or {}).get("id"), (o or {}).get("buyer") or buyer, dest, pol, inco, mode,
                 carrier, cont, str(today + timedelta(days=etd_off)),
                 str(today + timedelta(days=eta_off)), status,
                 # a demo shipment that has already left carries its departure stamp
                 str(today + timedelta(days=etd_off)) if status in ("dispatched", "delivered") else None,
                 inv,
                 str(today + timedelta(days=etd_off)) if inv else None,
                 (o or {}).get("currency") or "USD",
                 (o or {}).get("unit_price") or price, lc, "seed", now))
            sid = cur.lastrowid
            conn.execute("UPDATE shp_shipments SET shipment_no=? WHERE id=?",
                         ("SH-%d-%04d" % (today.year, sid), sid))
            for c in _DEMO_CARTONS.get(i, []):
                conn.execute(
                    "INSERT INTO shp_cartons (shipment_id,carton_no,style,colour,size,"
                    "qty_per_carton,cartons,net_weight,gross_weight,length_cm,width_cm,"
                    "height_cm,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (sid, *c, now))
    conn.commit()
