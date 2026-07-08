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
from urllib.parse import urlsplit, urlunsplit

from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from app.security import DEFAULT_ROLE

# Shared password for the seeded role/approver demo accounts. Accounts still on an
# older default are migrated to this on boot (see _ensure_demo_users); accounts an
# admin has given a custom password are left untouched.
DEMO_PASSWORD = "Admin@1122"
_OLD_DEMO_PASSWORDS = ("Tc@12345",)


# --------------------------------------------------------------------------
# Dialect detection
# --------------------------------------------------------------------------
def _is_pg():
    url = (getattr(Config, "DATABASE_URL", "") or "").strip()
    return url.startswith(("postgres://", "postgresql://"))


# Render external Postgres region domains. Used to auto-recover when DATABASE_URL
# is a cross-region *internal* host that cannot resolve. Oregon first (Render's
# default region, and the common mismatch source).
_RENDER_REGIONS = ("oregon", "virginia", "ohio", "frankfurt", "singapore")


def _add_ssl(url):
    """Managed cloud Postgres (Render, Neon, Supabase, RDS, ...) requires SSL.
    Add sslmode=require for any non-local FQDN host that doesn't already set it.
    Bare internal hosts (e.g. Render's dpg-...-a, no domain) and localhost are
    left untouched (internal/local connections don't need it forced)."""
    if "sslmode=" in url:
        return url
    host = (urlsplit(url).hostname or "").lower()
    is_local = host in ("", "localhost", "127.0.0.1", "::1")
    is_bare = "." not in host  # internal short hostname with no domain
    if not is_local and not is_bare:
        return url + ("&" if "?" in url else "?") + "sslmode=require"
    return url


def _pg_url():
    """Primary normalised connection URL (scheme fixed, SSL added for render)."""
    url = "postgresql://" + Config.DATABASE_URL.strip().split("://", 1)[1]
    return _add_ssl(url)


def _swap_host(url, new_host):
    """Return url with its host replaced, preserving user:pass, port, path, query."""
    p = urlsplit(url)
    userinfo = ""
    if p.username:
        userinfo = p.username + (f":{p.password}" if p.password else "") + "@"
    netloc = f"{userinfo}{new_host}" + (f":{p.port}" if p.port else "")
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


def _pg_candidates():
    """Connection URLs to try, in order. When DATABASE_URL is a Render *internal*
    host (dpg-xxxx-a, no domain) — which only resolves inside the database's own
    region — also offer the *external* host for each region, so a cross-region
    deploy self-heals automatically without changing any environment variable."""
    primary = _pg_url()
    candidates = [primary]
    host = urlsplit(primary).hostname or ""
    if host.startswith("dpg-") and "." not in host:
        for region in _RENDER_REGIONS:
            ext_host = f"{host}.{region}-postgres.render.com"
            candidates.append(_add_ssl(_swap_host(primary, ext_host)))
    return candidates


# Tables whose primary key is NOT `id` (so we never append RETURNING id).
_NO_ID_TABLES = {"mnt_settings", "ai_system_settings"}


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
    # psycopg2 does %-style interpolation whenever a params tuple is given, so any
    # LITERAL % in the SQL (e.g. a hardcoded LIKE 'Low stock:%') must be doubled to
    # %% or it raises "unsupported format character". Do this BEFORE turning ? into
    # %s so the real placeholders we add are not escaped. (No-op for SQLite.)
    sql = sql.replace("%", "%%")
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

    def executemany(self, sql, seq_of_params):
        """sqlite3-compatible executemany. psycopg2 has its own executemany, but
        our per-statement SQL translation (?-> %s, INSERT OR IGNORE, RETURNING)
        lives in execute(), so we reuse it row by row. Fine for seed/bulk inserts."""
        cur = None
        for params in (seq_of_params or []):
            cur = self.execute(sql, params)
        return cur

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


_RESOLVED_PG_URL = None  # cached working URL once discovered (host probing is one-time)


def get_db():
    """Return a connection. PostgreSQL when DATABASE_URL is set, else SQLite.

    For PostgreSQL it auto-discovers a reachable host: it tries the configured
    URL first, then — if that is an unreachable Render internal host — the
    external host for each region, caching whichever connects. This makes a
    cross-region deploy work without editing the DATABASE_URL env var."""
    global _RESOLVED_PG_URL
    if _is_pg():
        import psycopg2
        if _RESOLVED_PG_URL:
            try:
                return _PGConn(psycopg2.connect(_RESOLVED_PG_URL, connect_timeout=10))
            except psycopg2.OperationalError:
                _RESOLVED_PG_URL = None  # previously-good host failed; re-probe
        last_err = None
        for cand in _pg_candidates():
            try:
                conn = _PGConn(psycopg2.connect(cand, connect_timeout=8))
                _RESOLVED_PG_URL = cand
                return conn
            except psycopg2.OperationalError as exc:
                last_err = exc
        raise last_err
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
    created_at TEXT,
    ext_key    TEXT,                  -- dedup key for alerts pulled from the 4 systems (module:source_id)
    alerted    INTEGER DEFAULT 0,     -- 1 once an external alert (email/webhook) has been sent
    auto_ticket_ref TEXT              -- ITSM ticket_no if this alert was auto-ticketed
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT,
    action     TEXT,
    detail     TEXT,
    ip         TEXT,
    created_at TEXT
);

-- Admin-managed roles: overlay/extend the code-defined roles without a deploy.
-- perms_json is a JSON array of permission keys (or ["*"] for all). is_builtin=1
-- marks an override of a code role (delete reverts to the code default).
CREATE TABLE IF NOT EXISTS custom_roles (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key   TEXT UNIQUE NOT NULL,
    label      TEXT,
    perms_json TEXT,
    is_builtin INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
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

-- ===== BI / Analytics (dynamic dashboards from uploaded or live data) =====
CREATE TABLE IF NOT EXISTS bi_datasets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    filename     TEXT,
    source       TEXT DEFAULT 'upload',     -- upload | system:<key>
    n_rows       INTEGER DEFAULT 0,
    n_cols       INTEGER DEFAULT 0,
    columns_json TEXT,                       -- column profile (types, stats)
    data_json    TEXT,                       -- {columns:[...], rows:[[...]]}
    quality_json TEXT,                       -- data-quality report
    owner        TEXT,
    created_at   TEXT
);
CREATE TABLE IF NOT EXISTS bi_dashboards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    dataset_id   INTEGER,
    spec_json    TEXT,                       -- {kpis:[...], charts:[...]}
    owner        TEXT,
    is_pinned    INTEGER DEFAULT 0,
    lang         TEXT DEFAULT 'en',
    created_at   TEXT,
    updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS bi_alerts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT,
    dataset_id   INTEGER,
    column_name  TEXT,
    agg          TEXT DEFAULT 'sum',         -- sum|avg|count|min|max|last
    op           TEXT DEFAULT '>',           -- > | < | >= | <= | ==
    threshold    REAL DEFAULT 0,
    last_value   REAL,
    last_state   TEXT DEFAULT 'ok',          -- ok | breach
    alerted      INTEGER DEFAULT 0,
    owner        TEXT,
    created_at   TEXT
);
CREATE TABLE IF NOT EXISTS bi_digests (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dashboard_id INTEGER,
    recipients   TEXT,                       -- comma-separated emails
    cadence      TEXT DEFAULT 'weekly',      -- daily | weekly | monthly
    last_sent    TEXT,
    owner        TEXT,
    created_at   TEXT
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
        ("bi", "BI & Analytics", "الذكاء والتحليلات", "İş Zekâsı ve Analitik",
         "Drop any file and get an instant dashboard: KPIs, charts, insights, forecasts — offline.",
         "أسقط أي ملف واحصل على لوحة فورية: مؤشرات ورسوم ورؤى وتنبؤات — بدون إنترنت.",
         "Herhangi bir dosyayı bırakın, anında pano alın: KPI, grafik, içgörü, tahmin — çevrimdışı.",
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
    """Idempotently ensure the role/approver demo accounts exist (password
    DEMO_PASSWORD = Admin@1122). Runs on every init. Creates any missing account,
    and migrates any account still on an OLD default password to DEMO_PASSWORD —
    but never overwrites a password an admin has since customised."""
    demo = [
        ("director", "IT Director", "it_director"),
        ("agent", "Service Desk Agent", "service_desk_agent"),
        ("exec", "Executive Viewer", "executive_viewer"),
        ("maint", "Maintenance Manager", "maintenance_manager"),
        ("tech", "Maintenance Technician", "maintenance_technician"),
        ("store", "Storekeeper", "storekeeper"),
        ("supervisor", "Production Supervisor", "production_supervisor"),
        ("factory", "Factory Manager", "factory_manager"),
        # ---- Procurement approval ladder signers ----
        ("warehouse", "Warehouse Manager", "warehouse_manager"),
        ("purchasing", "Purchasing Manager", "purchasing_manager"),
        ("finance", "Finance Manager", "finance_manager"),
        ("cfo", "Chief Financial Officer", "cfo"),
        ("ceo", "Chief Executive Officer", "ceo"),
    ]
    for uname, name, role in demo:
        row = conn.execute("SELECT id, password_hash FROM users WHERE username=?",
                           (uname,)).fetchone()
        if not row:
            conn.execute(
                """INSERT INTO users (username, password_hash, full_name, role, created_at)
                   VALUES (?,?,?,?,?)""",
                (uname, generate_password_hash(DEMO_PASSWORD), name, role, utcnow()))
        elif row["password_hash"] and any(
                _pw_matches(row["password_hash"], old) for old in _OLD_DEMO_PASSWORDS):
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(DEMO_PASSWORD), row["id"]))


def _pw_matches(pw_hash, plain):
    try:
        return check_password_hash(pw_hash, plain)
    except Exception:
        return False


def ensure_user_signatures(conn):
    """Give every user a ready-to-use signature (rendered from their name) so it
    can be stamped on any paper. Only fills accounts that don't already have one;
    a user can still replace it in their profile. Safe no-op if Pillow/font is
    unavailable (returns without touching anything)."""
    try:
        from app.services.signature import generate_png, DEFAULT_STYLE
    except Exception:
        return
    rows = conn.execute(
        "SELECT id, full_name, username FROM users "
        "WHERE sig_png IS NULL OR sig_png = ''").fetchall()
    for r in rows:
        png = generate_png(r["full_name"] or r["username"])
        if png:
            conn.execute(
                "UPDATE users SET sig_png=?, sig_name=?, sig_style=?, sig_updated_at=? WHERE id=?",
                (png, r["full_name"] or r["username"], DEFAULT_STYLE, utcnow(), r["id"]))
    conn.commit()


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


# The four externally-hosted integrated systems: key -> (port, health_path).
# Used to derive base/health URLs when re-pointing to public addresses.
_INTEGRATION_ENDPOINTS = {
    "itsm":         (5000, "/health"),
    "assets":       (5001, "/api/health"),
    "monitoring":   (5002, "/health"),
    "commandtrack": (5003, "/"),
}


def apply_integration_overrides(conn):
    """Re-point the four integrated systems to PUBLIC URLs so a cloud deploy can
    reach them. Sources, in priority: a per-system URL (Config.SYSTEM_URLS, e.g.
    TC_URL_ITSM), else TC_INTEGRATION_HOST when it is a real host (not localhost).
    The correct health path is appended automatically. Systems with nothing
    configured keep their stored value (so Admin -> Integrations edits survive)."""
    host = (Config.INTEGRATION_HOST or "").strip()
    scheme = (getattr(Config, "INTEGRATION_SCHEME", "http") or "http").strip()
    host_is_real = host and host not in ("127.0.0.1", "localhost", "0.0.0.0", "::1")
    overrides = getattr(Config, "SYSTEM_URLS", {}) or {}
    for key, (port, health_path) in _INTEGRATION_ENDPOINTS.items():
        base = (overrides.get(key) or "").strip().rstrip("/")
        if not base and host_is_real:
            base = f"{scheme}://{host}:{port}"
        if not base:
            continue  # nothing configured; leave the stored value untouched
        health = base + "/" if health_path in ("", "/") else base + health_path
        conn.execute("UPDATE systems SET base_url=?, health_url=? WHERE key=?",
                     (base, health, key))


def init_db():
    """Create tables and seed first-run data. Idempotent and non-destructive."""
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        # Commit the schema NOW. On PostgreSQL the CREATE TABLEs above are still
        # inside an open transaction; the first failing ALTER below (a column
        # that already exists) triggers conn.rollback(), which would silently
        # roll back any newly created tables with it — new tables would then
        # never materialize on Render (SQLite is immune: executescript commits
        # up front). Committing here makes the migrations independent.
        conn.commit()
        # Migration: ensure the notifications dedup column exists on databases
        # created before it was added (harmless if it already exists).
        try:
            conn.execute("ALTER TABLE notifications ADD COLUMN ext_key TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            conn.execute("ALTER TABLE notifications ADD COLUMN alerted INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            conn.execute("ALTER TABLE notifications ADD COLUMN auto_ticket_ref TEXT")
            conn.commit()
        except Exception:
            conn.rollback()
        # Per-user targeting + deep link for module notifications (e.g. procurement
        # approvals): target_user NULL = broadcast; else only that user sees it.
        for _col, _ddl in (
            ("target_user", "ALTER TABLE notifications ADD COLUMN target_user TEXT"),
            ("link", "ALTER TABLE notifications ADD COLUMN link TEXT"),
        ):
            try:
                conn.execute(_ddl)
                conn.commit()
            except Exception:
                conn.rollback()
        # Migration: per-user digital signature (style, rendered PNG, name, time),
        # per-user extra permissions (JSON array) and notification preferences.
        for _col, _ddl in (
            ("sig_style", "ALTER TABLE users ADD COLUMN sig_style TEXT"),
            ("sig_png", "ALTER TABLE users ADD COLUMN sig_png TEXT"),
            ("sig_name", "ALTER TABLE users ADD COLUMN sig_name TEXT"),
            ("sig_updated_at", "ALTER TABLE users ADD COLUMN sig_updated_at TEXT"),
            ("extra_perms", "ALTER TABLE users ADD COLUMN extra_perms TEXT"),
            ("notif_prefs", "ALTER TABLE users ADD COLUMN notif_prefs TEXT"),
        ):
            try:
                conn.execute(_ddl)
                conn.commit()
            except Exception:
                conn.rollback()
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
                    (uname, generate_password_hash(DEMO_PASSWORD), name, role, utcnow()),
                )
        _ensure_demo_users(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM systems").fetchone()["c"] == 0:
            _seed_systems(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM notifications").fetchone()["c"] == 0:
            _seed_notifications(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM production_lines").fetchone()["c"] == 0:
            _seed_production(conn)
        # Re-point integrated systems to public URLs when configured via env vars
        # (runs every startup so a redeploy with new URLs takes effect).
        apply_integration_overrides(conn)
        # Env-managed admin password: when TC_ADMIN_PASSWORD is explicitly set,
        # keep the super-admin's password in sync with it so you can rotate it via
        # the Render env var + a redeploy (no DB access needed).
        import os as _os
        if _os.getenv("TC_ADMIN_PASSWORD"):
            conn.execute("UPDATE users SET password_hash=? WHERE username=?",
                         (generate_password_hash(Config.ADMIN_PASSWORD), Config.ADMIN_USER))
        conn.commit()
        # Maintenance & Spare Parts (CMMS) module — create + seed if empty
        from app.maintenance.schema import create_and_seed as _mnt_create_and_seed
        _mnt_create_and_seed(conn)
        # Procurement & Approvals cycle module — create + seed if empty
        from app.approvals.schema import create_and_seed as _proc_create_and_seed
        _proc_create_and_seed(conn)
        # Garamento knowledge base (editable how-to manual) — create + seed if empty
        try:
            from app.services.garamento_kb import ensure_and_seed as _gm_kb_seed
            _gm_kb_seed(conn)
        except Exception:
            conn.rollback()
        # AI Prediction & Intelligence Center — tables + demo data
        try:
            from app.intelligence.schema import create_and_seed as _ai_create_and_seed
            _ai_create_and_seed(conn)
        except Exception:
            conn.rollback()
        # Auto-provision a signature for every user who doesn't have one yet, so it
        # can be stamped on any paper without each person drawing one manually.
        try:
            ensure_user_signatures(conn)
        except Exception:
            conn.rollback()
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
