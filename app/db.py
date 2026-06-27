"""
TC Platform — persistence layer with a dialect-aware compatibility shim.

Runs on **SQLite** locally (default) and **PostgreSQL** on Render — chosen at
runtime by Config.DATABASE_URL. The rest of the codebase keeps using
`conn.execute("... ?", params)` unchanged; this layer rewrites SQL for Postgres
(placeholders, INSERT OR IGNORE, date()/datetime(), AUTOINCREMENT) and emulates
sqlite3 behaviour (RETURNING-id lastrowid, hybrid index/key rows).
"""
import re
import sqlite3
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash

from config import Config
from app.security import DEFAULT_ROLE


# --------------------------------------------------------------------------
# Dialect detection
# --------------------------------------------------------------------------
def _is_pg():
    url = (getattr(Config, "DATABASE_URL", "") or "").strip()
    return url.startswith(("postgres://", "postgresql://"))


def _pg_url():
    url = Config.DATABASE_URL.strip()
    url = "postgresql://" + url.split("://", 1)[1]  # normalise scheme for psycopg2
    # Render's *external* database host (…-a.<region>-postgres.render.com) requires
    # SSL. The internal host accepts it too, so adding sslmode=require is safe for
    # both and lets the External Database URL be used region-independently.
    if "render.com" in url and "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url


# Tables whose primary key is NOT `id` (so we never append RETURNING id).
_NO_ID_TABLES = {"mnt_settings"}


def translate_ddl(sql, dialect):
    """Translate the SQLite SCHEMA strings for PostgreSQL (testable/reversible)."""
    if dialect != "pg":
        return sql
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", sql, flags=re.I)
    return sql


def _translate_sql(sql):
    """Rewrite a single SQLite statement for PostgreSQL. Returns (sql, was_ignore)."""
    # date/datetime helpers (order: most specific first)
    sql = re.sub(r"date\('now'\s*,\s*\?\)",
                 "to_char((now() AT TIME ZONE 'UTC') + (?)::interval, 'YYYY-MM-DD')", sql, flags=re.I)
    sql = re.sub(r"datetime\('now'\)",
                 "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')", sql, flags=re.I)
    sql = re.sub(r"date\('now'\)",
                 "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD')", sql, flags=re.I)
    was_ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE", sql, flags=re.I))
    sql = re.sub(r"INSERT\s+OR\s+IGNORE", "INSERT", sql, flags=re.I)
    sql = sql.replace("?", "%s")  # bound-param placeholder (no literal ? in this codebase)
    return sql, was_ignore


class _PGRow(dict):
    """psycopg2 RealDictRow-like row that ALSO supports integer indexing."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _PGCursor:
    def __init__(self, raw):
        self._raw = raw
        self.lastrowid = None

    def _wrap(self, row):
        return _PGRow(row) if row is not None else None

    def fetchone(self):
        return self._wrap(self._raw.fetchone())

    def fetchall(self):
        return [self._wrap(r) for r in self._raw.fetchall()]

    def __iter__(self):
        return (self._wrap(r) for r in self._raw)


class _PGConn:
    """sqlite3-compatible wrapper around a psycopg2 connection."""
    def __init__(self, conn):
        self._c = conn

    def execute(self, sql, params=()):
        import psycopg2.extras
        sql2, was_ignore = _translate_sql(sql)
        stripped = sql2.lstrip()
        is_insert = stripped[:6].lower() == "insert"
        returning = False
        if is_insert:
            sql2 = sql2.rstrip().rstrip(";")
            if was_ignore and "on conflict" not in sql2.lower():
                sql2 += " ON CONFLICT DO NOTHING"
            m = re.match(r"insert\s+into\s+([a-zA-Z_][\w]*)", stripped, flags=re.I)
            table = (m.group(1).lower() if m else "")
            if table not in _NO_ID_TABLES and "returning" not in sql2.lower():
                sql2 += " RETURNING id"
                returning = True
        cur = self._c.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql2, params)
        wrapped = _PGCursor(cur)
        if returning:
            try:
                row = cur.fetchone()
                wrapped.lastrowid = row["id"] if row else None
            except Exception:
                wrapped.lastrowid = None
        return wrapped

    def executescript(self, sql):
        cur = self._c.cursor()
        cur.execute(translate_ddl(sql, "pg"))
        return cur

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def close(self):
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def get_db():
    """Return a connection. PostgreSQL when DATABASE_URL is set, else SQLite."""
    if _is_pg():
        import psycopg2
        return _PGConn(psycopg2.connect(_pg_url()))
    conn = sqlite3.connect(Config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT,
    email         TEXT,
    role          TEXT NOT NULL DEFAULT 'normal_user',
    lang_pref     TEXT DEFAULT 'en',
    theme_pref    TEXT DEFAULT 'light',
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS systems (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    key           TEXT UNIQUE NOT NULL,
    name_en       TEXT, name_ar TEXT, name_tr TEXT,
    desc_en       TEXT, desc_ar TEXT, desc_tr TEXT,
    category      TEXT,
    base_url      TEXT,
    health_url    TEXT,
    port          INTEGER,
    auth_mode     TEXT DEFAULT 'independent',
    icon          TEXT,
    owner         TEXT,
    criticality   TEXT DEFAULT 'medium',
    launch_mode   TEXT DEFAULT 'new_tab',
    is_integrated INTEGER DEFAULT 1,
    enabled       INTEGER DEFAULT 1,
    sort_order    INTEGER DEFAULT 100
);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    severity   TEXT DEFAULT 'info',   -- info | warning | critical
    module     TEXT,
    title      TEXT,
    message    TEXT,
    is_read    INTEGER DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT,
    action     TEXT,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT
);

-- ===== Production Visibility (a real working module) =====
CREATE TABLE IF NOT EXISTS production_lines (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    area        TEXT,
    status      TEXT DEFAULT 'running',   -- running | idle | maintenance | down
    shift       TEXT DEFAULT 'A',
    target_output  INTEGER DEFAULT 0,
    actual_output  INTEGER DEFAULT 0,
    operators   INTEGER DEFAULT 0,
    notes       TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS production_downtime (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id     INTEGER,
    reason      TEXT,
    category    TEXT DEFAULT 'mechanical', -- mechanical|electrical|material|changeover|quality|other
    minutes     INTEGER DEFAULT 0,
    occurred_at TEXT,
    created_at  TEXT,
    FOREIGN KEY (line_id) REFERENCES production_lines(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS production_quality (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    line_id     INTEGER,
    issue       TEXT,
    severity    TEXT DEFAULT 'low',        -- low | medium | high
    quantity    INTEGER DEFAULT 0,
    status      TEXT DEFAULT 'open',       -- open | investigating | resolved
    created_at  TEXT,
    FOREIGN KEY (line_id) REFERENCES production_lines(id) ON DELETE CASCADE
);
"""


# ---------------------------------------------------------------------------
# Seed data: the four real integrated systems + future-ready module registry.
# Ports default to the recommended deployment plan (5000/5001/5002/5003).
# The actual app defaults differ (Monitoring=8000, CommandTrack=8080) — admins
# edit these in the Admin Center. See docs/INTEGRATION_MAP.md.
# ---------------------------------------------------------------------------
def _seed_systems(conn):
    host = Config.INTEGRATION_HOST
    systems = [
        # key, names(en/ar/tr), desc(en/ar/tr), category, port, health_path, icon, owner, criticality, integrated, order
        ("itsm",
         "ITSM Service Desk", "مكتب خدمة ITSM", "ITSM Servis Masası",
         "Ticketing, SLA, knowledge base and IT service management.",
         "التذاكر واتفاقيات مستوى الخدمة وقاعدة المعرفة.",
         "Talepler, SLA ve bilgi tabanı.",
         "service_desk", 5000, "/health", "headset", "IT Service Desk", "critical", 1, 10),
        ("assets",
         "Asset Management", "إدارة الأصول", "Varlık Yönetimi",
         "Assets, inventory, custody, procurement, maintenance and depreciation.",
         "الأصول والمخزون والعهدة والمشتريات والصيانة.",
         "Varlıklar, envanter, zimmet ve bakım.",
         "assets", 5001, "/api/health", "boxes", "Asset Governance", "high", 1, 20),
        ("monitoring",
         "Server Monitoring", "مراقبة الخوادم", "Sunucu İzleme",
         "Real-time Windows server monitoring: CPU, RAM, disk, services, alerts.",
         "مراقبة خوادم ويندوز في الوقت الفعلي.",
         "Gerçek zamanlı Windows sunucu izleme.",
         "monitoring", 5002, "/health", "activity", "Infrastructure", "critical", 1, 30),
        ("commandtrack",
         "CommandTrack Work", "إدارة الأعمال CommandTrack", "CommandTrack İş Yönetimi",
         "Projects, tasks, kanban, sprints, approvals and weekly planning.",
         "المشاريع والمهام والكانبان والاعتمادات.",
         "Projeler, görevler, kanban ve onaylar.",
         "work", 5003, "/", "kanban", "PMO", "high", 1, 40),
        # ---- Future-ready internal modules (served by TC Platform itself) ----
        ("ai_hub", "AI Transformation Hub", "مركز التحول بالذكاء الاصطناعي", "AI Dönüşüm Merkezi",
         "AI use-case portfolio, ROI and implementation phases.",
         "محفظة حالات استخدام الذكاء الاصطناعي والعائد.",
         "AI kullanım senaryoları ve ROI.",
         "ai", None, None, "sparkles", "Digital Transformation", "medium", 0, 50),
        ("finance", "Finance Digital Transformation", "التحول الرقمي المالي", "Finans Dijital Dönüşümü",
         "Invoice workflow, approvals, CAPEX/OPEX and cost-saving tracker.",
         "سير عمل الفواتير والاعتمادات وتتبع التوفير.",
         "Fatura iş akışı ve maliyet takibi.",
         "finance", None, None, "wallet", "Finance", "high", 0, 60),
        ("automation", "Automation & RPA Center", "مركز الأتمتة و RPA", "Otomasyon ve RPA Merkezi",
         "Automation pipeline, effort saved and process backlog.",
         "خط أنابيب الأتمتة والجهد الموفر.",
         "Otomasyon hattı ve süreç listesi.",
         "automation", None, None, "robot", "Automation", "medium", 0, 70),
        ("bi", "BI Dashboards", "لوحات BI", "BI Panoları",
         "Power BI portal: executive, finance, assets and production dashboards.",
         "بوابة Power BI للوحات التنفيذية والمالية.",
         "Power BI portalı ve panolar.",
         "bi", None, None, "chart", "Analytics", "medium", 0, 80),
        ("production", "Production Visibility", "رؤية الإنتاج", "Üretim Görünürlüğü",
         "Production lines, downtime, efficiency and quality tracking.",
         "خطوط الإنتاج والتوقف والكفاءة والجودة.",
         "Üretim hatları ve verimlilik.",
         "production", None, None, "factory", "Manufacturing", "high", 0, 90),
        ("hr", "HR Digital Services", "الخدمات الرقمية للموارد البشرية", "İK Dijital Hizmetleri",
         "Employee requests, onboarding, policies and HR approvals.",
         "طلبات الموظفين والتعيين والسياسات.",
         "Çalışan talepleri ve İK onayları.",
         "hr", None, None, "users", "Human Resources", "medium", 0, 100),
        ("procurement", "Procurement Center", "مركز المشتريات", "Satın Alma Merkezi",
         "Purchase requests, approvals, vendors and PO tracking.",
         "طلبات الشراء والاعتمادات والموردين.",
         "Satın alma talepleri ve onaylar.",
         "procurement", None, None, "cart", "Procurement", "medium", 0, 110),
        ("saplite", "SAP Lite Support", "دعم SAP Lite", "SAP Lite Destek",
         "Lightweight SAP support, requests and process notes.",
         "دعم SAP المبسط والطلبات.",
         "Hafif SAP desteği ve talepler.",
         "saplite", None, None, "server", "ERP", "low", 0, 120),
        ("governance", "Governance & Compliance", "الحوكمة والامتثال", "Yönetişim ve Uyum",
         "SLA governance, policies, audits, CAB, risk and DR readiness.",
         "حوكمة SLA والسياسات والمراجعات والمخاطر.",
         "SLA yönetişimi, politikalar ve risk.",
         "governance", None, None, "shield", "Governance", "high", 0, 130),
        ("kb", "Knowledge Base", "قاعدة المعرفة", "Bilgi Tabanı",
         "Unified knowledge articles and how-to guides.",
         "مقالات المعرفة الموحدة والأدلة.",
         "Birleşik bilgi makaleleri.",
         "kb", None, None, "book", "Knowledge", "low", 0, 140),
        ("reports", "Reports Center", "مركز التقارير", "Raporlar Merkezi",
         "Executive and operational reporting across all modules.",
         "التقارير التنفيذية والتشغيلية.",
         "Yönetici ve operasyonel raporlar.",
         "reports", None, None, "report", "Analytics", "medium", 0, 150),
    ]
    for s in systems:
        (key, name_en, name_ar, name_tr, desc_en, desc_ar, desc_tr, category,
         port, health_path, icon, owner, criticality, integrated, order) = s
        base_url = f"http://{host}:{port}" if port else None
        health_url = (base_url + health_path) if (port and health_path) else None
        conn.execute(
            """INSERT OR IGNORE INTO systems
               (key,name_en,name_ar,name_tr,desc_en,desc_ar,desc_tr,category,base_url,
                health_url,port,icon,owner,criticality,is_integrated,sort_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, name_en, name_ar, name_tr, desc_en, desc_ar, desc_tr, category,
             base_url, health_url, port, icon, owner, criticality, integrated, order),
        )


def _ensure_demo_users(conn):
    """Idempotently ensure the role-demo accounts exist (password Tc@12345).
    Runs on every init so existing installs also get the maintenance roles.
    Never touches the admin account or any user that already exists."""
    demo = [
        ("director", "IT Director", "it_director"),
        ("agent", "Service Desk Agent", "service_desk_agent"),
        ("exec", "Executive Viewer", "executive_viewer"),
        ("maint", "Maintenance Manager", "maintenance_manager"),
        ("tech", "Maintenance Technician", "maintenance_technician"),
        ("store", "Storekeeper", "storekeeper"),
        ("supervisor", "Production Supervisor", "production_supervisor"),
        ("factory", "Factory Manager", "factory_manager"),
    ]
    for uname, name, role in demo:
        conn.execute(
            """INSERT OR IGNORE INTO users (username, password_hash, full_name, role, created_at)
               VALUES (?,?,?,?,?)""",
            (uname, generate_password_hash("Tc@12345"), name, role, utcnow()))


def _seed_notifications(conn):
    samples = [
        ("critical", "itsm", "SLA breach imminent", "Ticket INC-1042 will breach SLA in 18 minutes."),
        ("warning", "monitoring", "High CPU usage", "FILE-SRV-01 CPU sustained above 90% for 5 minutes."),
        ("warning", "assets", "Low stock", "Toner cartridges below reorder level (3 remaining)."),
        ("info", "commandtrack", "Approval pending", "Project 'Finance Portal' awaits your approval."),
        ("critical", "monitoring", "Server offline", "Heartbeat lost from DB-SRV-02."),
        ("info", "assets", "Warranty expiry", "12 assets have warranties expiring within 30 days."),
    ]
    for sev, mod, title, msg in samples:
        conn.execute(
            "INSERT INTO notifications (severity, module, title, message, created_at) VALUES (?,?,?,?,?)",
            (sev, mod, title, msg, utcnow()),
        )


def _seed_production(conn):
    lines = [
        # name, area, status, shift, target, actual, operators
        ("Cutting Line A", "Cutting", "running", "A", 1200, 1150, 8),
        ("Sewing Line 1", "Sewing", "running", "A", 900, 820, 14),
        ("Sewing Line 2", "Sewing", "maintenance", "A", 900, 0, 0),
        ("Finishing Line", "Finishing", "running", "B", 1000, 880, 10),
        ("Packing Line", "Packing", "idle", "B", 1500, 0, 4),
    ]
    line_ids = {}
    for name, area, status, shift, tgt, act, ops in lines:
        cur = conn.execute(
            """INSERT INTO production_lines
               (name, area, status, shift, target_output, actual_output, operators, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (name, area, status, shift, tgt, act, ops, utcnow()))
        line_ids[name] = cur.lastrowid
    downtime = [
        ("Sewing Line 2", "Needle bar replacement", "mechanical", 95),
        ("Finishing Line", "Material shortage", "material", 30),
        ("Sewing Line 1", "Style changeover", "changeover", 22),
    ]
    for lname, reason, cat, mins in downtime:
        conn.execute(
            """INSERT INTO production_downtime (line_id, reason, category, minutes, occurred_at, created_at)
               VALUES (?,?,?,?,?,?)""",
            (line_ids.get(lname), reason, cat, mins, utcnow(), utcnow()))
    quality = [
        ("Sewing Line 1", "Stitch skipping on collar", "medium", 18, "investigating"),
        ("Finishing Line", "Color shade variation", "high", 40, "open"),
        ("Cutting Line A", "Fabric edge fray", "low", 12, "resolved"),
    ]
    for lname, issue, sev, qty, st in quality:
        conn.execute(
            """INSERT INTO production_quality (line_id, issue, severity, quantity, status, created_at)
               VALUES (?,?,?,?,?,?)""",
            (line_ids.get(lname), issue, sev, qty, st, utcnow()))


def init_db():
    """Create tables and seed first-run data. Idempotent and non-destructive."""
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        # Seed super admin only if no users exist
        existing = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        if existing == 0:
            conn.execute(
                """INSERT INTO users (username, password_hash, full_name, email, role,
                   lang_pref, theme_pref, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (Config.ADMIN_USER, generate_password_hash(Config.ADMIN_PASSWORD),
                 "Platform Administrator", "admin@tcgarments.com", "super_admin",
                 "en", "light", utcnow()),
            )
            # A couple of demo users to show RBAC menu visibility
            for uname, name, role in [
                ("director", "IT Director", "it_director"),
                ("agent", "Service Desk Agent", "service_desk_agent"),
                ("exec", "Executive Viewer", "executive_viewer"),
                ("maint", "Maintenance Manager", "maintenance_manager"),
                ("tech", "Maintenance Technician", "maintenance_technician"),
                ("store", "Storekeeper", "storekeeper"),
                ("supervisor", "Production Supervisor", "production_supervisor"),
                ("factory", "Factory Manager", "factory_manager"),
            ]:
                conn.execute(
                    """INSERT INTO users (username, password_hash, full_name, role, created_at)
                       VALUES (?,?,?,?,?)""",
                    (uname, generate_password_hash("Tc@12345"), name, role, utcnow()),
                )
        _ensure_demo_users(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM systems").fetchone()["c"] == 0:
            _seed_systems(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM notifications").fetchone()["c"] == 0:
            _seed_notifications(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM production_lines").fetchone()["c"] == 0:
            _seed_production(conn)
        conn.commit()
        # Maintenance & Spare Parts (CMMS) module — create + seed if empty
        from app.maintenance.schema import create_and_seed as _mnt_create_and_seed
        _mnt_create_and_seed(conn)
    finally:
        conn.close()


def log_audit(username, action, detail="", ip=""):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO audit_logs (username, action, detail, ip, created_at) VALUES (?,?,?,?,?)",
            (username, action, detail, ip, utcnow()),
        )
        conn.commit()
    finally:
        conn.close()
