"""
TC Platform — persistence layer with a dialect-aware compatibility shim.

Runs on **SQLite** locally (default) and **PostgreSQL** on Render — chosen at
runtime by Config.DATABASE_URL. The rest of the codebase keeps using
`conn.execute("... ?", params)` unchanged; this layer rewrites SQL for Postgres
(placeholders, INSERT OR IGNORE, date()/datetime(), AUTOINCREMENT) and emulates
sqlite3 behaviour (RETURNING-id lastrowid, hybrid index/key rows).
"""
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from app.security import DEFAULT_ROLE

# Password for the seeded role/approver demo accounts (director, agent, store,
# factory, warehouse, purchasing, finance, cfo, ceo ...).
#
# THIS REPOSITORY IS PUBLIC. A literal here is a published credential for accounts
# that sit at the TOP OF THE APPROVAL LADDER — anyone reading GitHub could have
# signed in as cfo or ceo. It now comes from TC_DEMO_PASSWORD, and when that is
# unset each account is created with an INDEPENDENT random password that is never
# printed and never written down: the accounts still exist so the role structure
# is intact, but nobody can sign in as them until an admin sets a password.
#
# _ensure_demo_users is additionally skipped entirely in production (see below),
# so a live deployment never grows demo accounts in the first place.
DEMO_PASSWORD = (os.getenv("TC_DEMO_PASSWORD", "") or "").strip()
# Passwords previously shipped in this file. They are listed ONLY so an existing
# account still on one of them can be migrated off it — never to set one.
_OLD_DEMO_PASSWORDS = ("Tc@12345", "Admin@1122")


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
    elif host.startswith("dpg-") and host.endswith("-postgres.render.com"):
        # DATABASE_URL is Render's EXTERNAL host, which leaves Render's network
        # and comes back through the public internet — every round trip pays for
        # it, and a page makes many. The bare short host in front of it is the
        # same database over the private network, so try that FIRST. Off Render
        # it simply does not resolve (fast NXDOMAIN) and we fall through to the
        # external URL, which is why this is safe to do unconditionally.
        candidates.insert(0, _swap_host(primary, host.split(".")[0]))
    return candidates


# Tables whose primary key is NOT `id` (so we never append RETURNING id).
_NO_ID_TABLES = {"mnt_settings", "ai_system_settings", "prob_settings", "auth_settings"}


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


def _pg_session_guards(raw):
    """Stop a statement from waiting on a lock forever.

    ALTER TABLE ... ADD COLUMN takes an ACCESS EXCLUSIVE lock, and PostgreSQL
    waits for it INDEFINITELY by default. Render does zero-downtime deploys, so
    while a new instance boots the OLD one is still serving and holding
    transactions on those same tables. gunicorn runs with --preload, meaning
    create_app() -> init_db() -> the guarded ALTERs execute BEFORE the port is
    bound: one blocked ALTER and the process never binds, Render sees "no open
    ports detected" and kills the deploy, with no traceback to explain it
    because nothing crashed — it was waiting.

    lock_timeout makes such a statement fail fast instead. Every migration site
    already wraps its DDL in try/except + rollback and treats failure as
    "already applied", so a timed-out ALTER is skipped and boot continues.

    Deliberately NOT statement_timeout: that would also kill legitimate long
    reads (report exports), which is a different problem with a different answer.
    SQLite is untouched — it has no such lock semantics here.
    """
    try:
        cur = raw.cursor()
        cur.execute("SET lock_timeout = '5s'")
        cur.close()
        raw.commit()
    except Exception:
        try:
            raw.rollback()
        except Exception:
            pass


class _PGConn:
    """sqlite3-compatible wrapper around a psycopg2 connection.

    When `pooled` is True, close() returns the underlying connection to the
    process-wide pool (after a rollback, so state is discarded exactly like a
    real close) instead of tearing down the TCP+TLS session. That handshake —
    repeated for every get_db() call, dozens of times per page — was the main
    production latency cost on Render."""
    def __init__(self, conn, pooled=False, fresh=True):
        self._c = conn
        self._pooled = pooled
        self._closed = False
        # close() is a no-op while this connection is the request's shared one;
        # the request teardown calls release() to actually hand it back.
        self._shared = False
        # lock_timeout is a SESSION setting: it survives for the life of the
        # connection. Re-applying it on every checkout of a pooled connection
        # bought nothing and cost two network round trips (SET + COMMIT) on
        # every single get_db() call — with hundreds of call sites, that was a
        # large part of the per-page latency.
        if fresh:
            _pg_session_guards(conn)

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
        try:
            cur.execute(sql2, params)
        except Exception:
            # PostgreSQL aborts the whole transaction on any statement error:
            # every later statement fails with InFailedSqlTransaction until a
            # rollback. This codebase is full of "try the query, carry on if it
            # fails" (migrations, optional columns), which was harmless when
            # each get_db() had its own connection that got discarded. Now the
            # connection is shared for the request, so one swallowed error would
            # break every remaining query on the page. Rollback restores exactly
            # the old behaviour; it only ever runs on the error path.
            if self._shared:
                try:
                    self._c.rollback()
                except Exception:
                    pass
            raise
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

    def release(self):
        """Really close/return, ignoring _shared. Used by the request teardown."""
        self._shared = False
        self.close()

    def close(self):
        if self._closed or self._shared:
            return
        self._closed = True
        if not self._pooled:
            self._c.close()
            return
        try:
            self._c.rollback()          # discard uncommitted state, like a real close
            _pg_pool_put(self._c)
        except Exception:
            try:
                _pg_pool_put(self._c, broken=True)
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


_RESOLVED_PG_URL = None  # cached working URL once discovered (host probing is one-time)

# ---------------------------------------------------------------------------
# PostgreSQL connection pool (per worker process). Every get_db() used to open
# a brand-new TCP+TLS session to the database — at tens of milliseconds each,
# and dozens of calls per page, that dominated page latency in production.
# The pool keeps warm connections; close() returns them (see _PGConn.close).
# ---------------------------------------------------------------------------
import threading as _threading

import time as _time

_PG_POOL = None
_PG_POOL_PID = None
_PG_POOL_LOCK = _threading.Lock()

# Monotonic time of the last known-good use, keyed by id() of the raw psycopg2
# connection. psycopg2's connection is a C type that rejects custom attributes,
# so the timestamp cannot live on the object itself. Entries are dropped when a
# connection is retired, and the pool is capped, so this stays small.
# ponytail: id()-keyed dict, bounded by pool size; a WeakKeyDictionary would be
# tidier if psycopg2 connections ever support weak references.
_PG_LAST_OK = {}
_LIVENESS_IDLE_S = 30


def _pg_pool(url):
    """Lazily create the pool for the resolved URL, once per PROCESS."""
    global _PG_POOL, _PG_POOL_PID
    pid = os.getpid()
    if _PG_POOL is None or _PG_POOL_PID != pid:
        with _PG_POOL_LOCK:
            if _PG_POOL is None or _PG_POOL_PID != pid:
                # gunicorn runs with --preload, so create_app() -> init_db()
                # builds a pool in the MASTER and then every worker is forked
                # from it — inheriting the same TCP sockets. Two processes
                # talking over one Postgres session interleave their traffic and
                # corrupt each other. Detect the pid change and build a fresh
                # pool. The inherited one is abandoned, never closed: closing it
                # here would tear down sockets another process is still using.
                from psycopg2.pool import ThreadedConnectionPool
                maxconn = int(os.getenv("TC_PG_POOL_MAX", "10") or 10)
                _PG_POOL = ThreadedConnectionPool(1, max(2, maxconn), url,
                                                  connect_timeout=10)
                _PG_POOL_PID = pid
                _PG_LAST_OK.clear()
    return _PG_POOL


def _pg_pool_put(raw, broken=False):
    if _PG_POOL is not None:
        if broken:
            _PG_LAST_OK.pop(id(raw), None)
        else:
            _PG_LAST_OK[id(raw)] = _time.monotonic()
        _PG_POOL.putconn(raw, close=broken)
    else:  # pool torn down (shouldn't happen) — just close
        try:
            raw.close()
        except Exception:
            pass


def _pg_pool_conn(url):
    """Checkout from the pool, discarding dead/idle-killed connections.

    Returns (raw, fresh) — `fresh` is True when the connection has NOT been
    validated as an already-configured session, so the caller knows it still
    needs its session guards applied.

    The liveness probe is a full network round trip, so it only runs on a
    connection that has been sitting idle long enough for the server to have
    plausibly reaped it. Probing one that was handed back moments ago cost the
    very round trip the pool exists to avoid."""
    pool = _pg_pool(url)
    for _ in range(3):
        raw = pool.getconn()
        if getattr(raw, "closed", 0):
            pool.putconn(raw, close=True)
            _PG_LAST_OK.pop(id(raw), None)
            continue
        last_ok = _PG_LAST_OK.get(id(raw))
        if last_ok is not None and (_time.monotonic() - last_ok) < _LIVENESS_IDLE_S:
            return raw, False           # recently healthy; session already set up
        try:
            cur = raw.cursor()
            cur.execute("SELECT 1")
            cur.close()
            raw.rollback()
            # Known-good, but we have no record of its session state, so let the
            # caller re-apply the guards.
            _PG_LAST_OK[id(raw)] = _time.monotonic()
            return raw, True
        except Exception:
            pool.putconn(raw, close=True)
            _PG_LAST_OK.pop(id(raw), None)
    return None, True  # pool kept handing us corpses — caller falls back to direct


def get_db():
    """Return a connection, reusing ONE per request.

    Every call used to open (or at least re-validate and re-configure) a
    connection. There are ~700 get_db() call sites and a single page hits a good
    many of them, each paying several network round trips to a database that is
    not local. Holding one connection for the life of the request removes all of
    those but the first. close() on the shared connection is a no-op; the app's
    teardown handler calls release() to hand it back to the pool.

    Only PostgreSQL is shared: SQLite is a local file with nothing to amortise,
    and its connection object cannot carry the extra flag. Background threads
    and CLI use run outside an app context and still get their own connection.
    """
    if not _is_pg():
        return _open_db()
    try:
        from flask import g, has_app_context
    except Exception:
        return _open_db()
    if not has_app_context():
        return _open_db()
    conn = getattr(g, "_tc_db", None)
    if conn is not None and not conn._closed:
        return conn
    conn = _open_db()
    conn._shared = True
    g._tc_db = conn
    return conn


def release_request_db(exc=None):
    """Return the request's shared connection to the pool. Called from teardown."""
    try:
        from flask import g
    except Exception:
        return
    conn = g.pop("_tc_db", None) if hasattr(g, "pop") else None
    if conn is None:
        return
    try:
        if exc is not None:
            conn.rollback()
    except Exception:
        pass
    try:
        conn.release()
    except Exception:
        pass


def _open_db():
    """Open a NEW connection. PostgreSQL when DATABASE_URL is set, else SQLite.

    For PostgreSQL it auto-discovers a reachable host: it tries the configured
    URL first, then — if that is an unreachable Render internal host — the
    external host for each region, caching whichever connects. This makes a
    cross-region deploy work without editing the DATABASE_URL env var."""
    global _RESOLVED_PG_URL, _PG_POOL
    if _is_pg():
        import psycopg2
        if _RESOLVED_PG_URL:
            # fast path: warm pooled connection (no TLS handshake per call)
            try:
                raw, fresh = _pg_pool_conn(_RESOLVED_PG_URL)
                if raw is not None:
                    return _PGConn(raw, pooled=True, fresh=fresh)
            except Exception:
                pass  # pool exhausted/broken — fall through to a direct connect
            try:
                return _PGConn(psycopg2.connect(_RESOLVED_PG_URL, connect_timeout=10))
            except psycopg2.OperationalError:
                _RESOLVED_PG_URL = None  # previously-good host failed; re-probe
                with _PG_POOL_LOCK:
                    if _PG_POOL is not None:
                        try:
                            _PG_POOL.closeall()
                        except Exception:
                            pass
                        _PG_POOL = None
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


def _ensure_extra_systems(conn):
    """Idempotently register add-on systems introduced after the initial four
    (the base seed only runs on an empty table). Runs every boot. Probation is a
    BUILT-IN online module (served by this platform at /hr/probation), so it is
    registered like BI/Production: internal (is_integrated=0, no external URL),
    and the launcher opens it directly. No VM / IP / tunnel needed."""
    extra = [
        # key, names(en/ar/tr), desc(en/ar/tr), category, icon, owner, criticality, order
        ("probation",
         "Probation Evaluation", "تقييم فترة الاختبار", "Deneme Süresi Değerlendirmesi",
         "Employee probation evaluation: cases, 8-criterion scoring, confirm/extend workflow.",
         "تقييم فترة اختبار الموظفين: الحالات وتقييم 8 معايير وسير عمل التثبيت/التمديد.",
         "Çalışan deneme değerlendirmesi: 8 kriterli puanlama, onay/uzatma iş akışı.",
         "hr", "users", "Human Resources", "high", 45),
    ]
    for (key, name_en, name_ar, name_tr, desc_en, desc_ar, desc_tr, category,
         icon, owner, criticality, order) in extra:
        conn.execute(
            """INSERT OR IGNORE INTO systems
               (key,name_en,name_ar,name_tr,desc_en,desc_ar,desc_tr,category,base_url,
                health_url,port,icon,owner,criticality,is_integrated,sort_order)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (key, name_en, name_ar, name_tr, desc_en, desc_ar, desc_tr, category,
             None, None, None, icon, owner, criticality, 0, order))
    # Converge the earlier external (VM IP :5005) registration to the built-in
    # online module — clears the dead LAN URL so the tile opens /hr/probation and
    # stops health-checking a private IP. Leaves any custom admin URL untouched.
    conn.execute(
        "UPDATE systems SET base_url=NULL, health_url=NULL, port=NULL, is_integrated=0 "
        "WHERE key='probation' AND (base_url LIKE '%:5005' OR base_url LIKE '%10.100.1.13%')")


def _report_published_passwords(conn):
    """Find accounts still using a password this public repo once contained.

    These are real, exploitable logins — 'ceo' and 'cfo' sit at the top of the
    procurement approval ladder. Reporting is the default because rotating them
    unannounced would lock out whoever is using them mid-cycle; set
    TC_ROTATE_PUBLISHED_PASSWORDS=1 to have the next boot rotate them instead.
    Either way the operator finds out, which is better than silence.
    """
    import logging
    log = logging.getLogger("tc.security")
    rotate = (os.getenv("TC_ROTATE_PUBLISHED_PASSWORDS", "") or "").strip().lower() in ("1", "true", "yes")
    try:
        rows = conn.execute("SELECT id, username, password_hash FROM users").fetchall()
    except Exception:
        return
    hit = [r for r in rows if r["password_hash"]
           and any(_pw_matches(r["password_hash"], old) for old in _OLD_DEMO_PASSWORDS)]
    if not hit:
        return
    names = ", ".join(r["username"] for r in hit)
    if rotate:
        for r in hit:
            try:
                conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                             (generate_password_hash(secrets.token_urlsafe(24)), r["id"]))
            except Exception:
                continue
        try:
            conn.commit()
        except Exception:
            pass
        log.warning("SECURITY: rotated %d account(s) off a password published in this "
                    "repository (%s). They now have unguessable passwords and must be "
                    "reset by an admin before use.", len(hit), names)
    else:
        log.warning("SECURITY: %d account(s) still use a password that was published in "
                    "this PUBLIC repository (%s). Anyone who read the source could sign "
                    "in as them. Change these passwords, or set "
                    "TC_ROTATE_PUBLISHED_PASSWORDS=1 to have the next boot rotate them.",
                    len(hit), names)


def _new_demo_password():
    """The password a demo account is created with.

    TC_DEMO_PASSWORD when set, otherwise an INDEPENDENT random one per account
    that is never printed or stored anywhere in clear. The account exists so the
    role structure is complete, but it cannot be signed into until an admin sets
    a password — which is the safe default for a public repository.
    """
    return DEMO_PASSWORD or secrets.token_urlsafe(24)


def _ensure_demo_users(conn):
    """Idempotently ensure the role/approver demo accounts exist. Creates any
    missing account and migrates any account still on a PREVIOUSLY SHIPPED default
    password off it — never overwriting a password an admin has customised.

    IN PRODUCTION no account is CREATED — a live deployment with real staff must
    not grow a 'cfo' and a 'ceo' login nobody made deliberately.

    But production still has the accounts this repository already published, so
    it does one thing: it REPORTS any account still on a password that was once
    committed here, naming them in the log. It does not rotate them by default,
    because those accounts may be in daily use and a surprise lockout on the
    approval ladder is its own outage. Set TC_ROTATE_PUBLISHED_PASSWORDS=1 to
    rotate them to unguessable values on the next boot — after which an admin
    sets real passwords through Admin > Registrations.
    """
    production = (getattr(Config, "ENV", "") or "").lower() == "production"
    if production:
        _report_published_passwords(conn)
        return
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
                (uname, generate_password_hash(_new_demo_password()), name, role, utcnow()))
        elif row["password_hash"] and any(
                _pw_matches(row["password_hash"], old) for old in _OLD_DEMO_PASSWORDS):
            # Still on a password this repository once published: rotate it off,
            # to TC_DEMO_PASSWORD if set, otherwise to an unguessable random one.
            conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                         (generate_password_hash(_new_demo_password()), row["id"]))


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
            # HR org-scope for probation managers/section heads (match employee dept/section)
            ("scope_department", "ALTER TABLE users ADD COLUMN scope_department TEXT"),
            ("scope_section", "ALTER TABLE users ADD COLUMN scope_section TEXT"),
            # Account management (self-registration, verification, approval lifecycle).
            # account_status DEFAULTs to 'active' so every EXISTING user keeps logging in.
            ("employee_id", "ALTER TABLE users ADD COLUMN employee_id TEXT"),
            ("mobile", "ALTER TABLE users ADD COLUMN mobile TEXT"),
            ("company", "ALTER TABLE users ADD COLUMN company TEXT"),
            ("department", "ALTER TABLE users ADD COLUMN department TEXT"),
            ("job_title", "ALTER TABLE users ADD COLUMN job_title TEXT"),
            ("location", "ALTER TABLE users ADD COLUMN location TEXT"),
            ("manager", "ALTER TABLE users ADD COLUMN manager TEXT"),
            ("account_status", "ALTER TABLE users ADD COLUMN account_status TEXT DEFAULT 'active'"),
            ("email_verified_at", "ALTER TABLE users ADD COLUMN email_verified_at TEXT"),
            ("registration_source", "ALTER TABLE users ADD COLUMN registration_source TEXT"),
            ("approved_by", "ALTER TABLE users ADD COLUMN approved_by TEXT"),
            ("approved_at", "ALTER TABLE users ADD COLUMN approved_at TEXT"),
            ("rejected_by", "ALTER TABLE users ADD COLUMN rejected_by TEXT"),
            ("rejected_at", "ALTER TABLE users ADD COLUMN rejected_at TEXT"),
            ("rejection_reason", "ALTER TABLE users ADD COLUMN rejection_reason TEXT"),
            ("last_password_change_at", "ALTER TABLE users ADD COLUMN last_password_change_at TEXT"),
            ("failed_login_count", "ALTER TABLE users ADD COLUMN failed_login_count INTEGER DEFAULT 0"),
            ("locked_until", "ALTER TABLE users ADD COLUMN locked_until TEXT"),
            ("last_login_at", "ALTER TABLE users ADD COLUMN last_login_at TEXT"),
            ("terms_accepted_at", "ALTER TABLE users ADD COLUMN terms_accepted_at TEXT"),
            ("privacy_accepted_at", "ALTER TABLE users ADD COLUMN privacy_accepted_at TEXT"),
            ("session_epoch", "ALTER TABLE users ADD COLUMN session_epoch INTEGER DEFAULT 0"),
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
            # Demo users that show RBAC menu visibility on an evaluation build.
            # NOT created in production: a live deployment gets its real people
            # through Admin > Registrations, and an unexplained "factory" or
            # "store" login nobody created is a standing invitation. Each is
            # given an independent unguessable password (see _new_demo_password)
            # unless TC_DEMO_PASSWORD is set — this repository is public, so a
            # literal here is a published credential.
            if (getattr(Config, "ENV", "") or "").lower() != "production":
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
                        (uname, generate_password_hash(_new_demo_password()), name, role, utcnow()),
                    )
        _ensure_demo_users(conn)
        if conn.execute("SELECT COUNT(*) AS c FROM systems").fetchone()["c"] == 0:
            _seed_systems(conn)
        _ensure_extra_systems(conn)   # add-on standalone systems (idempotent, every boot)
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
        # Smart Factory (MES) — create + seed if empty (reuses production_lines)
        try:
            from app.smartfactory.schema import create_and_seed as _sf_create_and_seed
            _sf_create_and_seed(conn)
        except Exception:
            conn.rollback()
        # AI Prediction & Intelligence Center — tables + demo data
        try:
            from app.intelligence.schema import create_and_seed as _ai_create_and_seed
            _ai_create_and_seed(conn)
        except Exception:
            conn.rollback()
        # HR — Probation Management module — tables + default template + demo data
        try:
            from app.probation.schema import create_and_seed as _prob_create_and_seed
            _prob_create_and_seed(conn)
        except Exception:
            conn.rollback()
        # Accounts (Auth & Account Management) — auth_* tables + settings + org options
        try:
            from app.accounts.schema import create_and_seed as _acc_create_and_seed
            _acc_create_and_seed(conn)
        except Exception:
            conn.rollback()
        # Compliance & social-audit — cmp_* tables + demo audits/CAPs/certificates
        try:
            from app.compliance.schema import create_and_seed as _cmp_create_and_seed
            _cmp_create_and_seed(conn)
        except Exception:
            conn.rollback()
        # Orders + Time & Action — ord_* tables + demo orders/critical-path
        try:
            from app.orders.schema import create_and_seed as _ord_create_and_seed
            _ord_create_and_seed(conn)
        except Exception:
            conn.rollback()
        # --- Manufacturing / supply-chain modules -------------------------
        # ORDER MATTERS: masters first (styles), then material, then the modules
        # whose demo seeds reference orders/material. Each is independently
        # guarded so one failing seed can never abort the rest of boot.
        for _mod in ("plm", "warehouse", "costing", "planning", "cutroom",
                     "mes", "quality", "wash", "trace", "shipping", "people"):
            try:
                _m = __import__(f"app.{_mod}.schema", fromlist=["create_and_seed"])
                _m.create_and_seed(conn)
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
