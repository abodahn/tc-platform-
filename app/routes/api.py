"""
TC Platform — JSON API.

  GET /api/health          platform self health (public, lightweight)
  GET /api/status          live status of every integrated system (auth)
  GET /api/status/<key>    live status of one system (auth)
  GET /api/diag?token=...  TEMPORARY deployment diagnostics (remove after fix)
"""
import sys
import traceback
from flask import Blueprint, jsonify, request

from config import Config
from app.db import get_db
from app.auth import login_required
from app.services import health as health_svc

bp = Blueprint("api", __name__, url_prefix="/api")

# Temporary token to gate the diagnostic endpoint (not a real secret).
_DIAG_TOKEN = "tcdiag2026"


@bp.route("/diag")
def diag():
    """TEMPORARY: surface the exact deployment problem as JSON. Each step is
    isolated so one failure does not hide the others. Returns no secrets.
    Visit /api/diag?token=tcdiag2026 . REMOVE this route once the app is healthy."""
    if request.args.get("token") != _DIAG_TOKEN:
        return jsonify({"error": "add ?token=tcdiag2026"}), 403

    url = (Config.DATABASE_URL or "")
    masked = ""
    if "@" in url:
        masked = url.split("@", 1)[1]  # host/db part only, never user:pass
    engine = "postgresql" if url.startswith(("postgres://", "postgresql://")) else "sqlite"
    out = {
        "engine": engine,
        "db_host_part": masked,
        "sslmode_in_url": "sslmode=" in url,
        "python": sys.version.split()[0],
        "env": Config.ENV,
        "steps": {},
    }

    def step(name, fn):
        try:
            out["steps"][name] = {"ok": True, "result": fn()}
        except Exception:
            out["steps"][name] = {"ok": False, "error": traceback.format_exc()[-1500:]}

    # 1) raw connect + trivial query
    def _connect():
        c = get_db()
        try:
            c.execute("SELECT 1").fetchone()
            return "connected"
        finally:
            c.close()
    step("connect", _connect)

    # 2) list existing tables (works on both engines)
    def _tables():
        c = get_db()
        try:
            if engine == "postgresql":
                rows = c.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' ORDER BY table_name").fetchall()
                return [r["table_name"] for r in rows]
            rows = c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            return [r["name"] for r in rows]
        finally:
            c.close()
    step("tables", _tables)

    # 3) the exact query the login/current_user path runs
    def _users():
        c = get_db()
        try:
            n = c.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
            c.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (1,)).fetchone()
            return f"users table OK, count={n}"
        finally:
            c.close()
    step("users_query", _users)

    # 4) (re)run schema bootstrap and report any error
    def _init():
        from app.db import init_db
        init_db()
        return "init_db ok"
    step("init_db", _init)

    # 5) render the login template exactly as the page does
    def _render():
        from flask import render_template
        html = render_template("login.html", next="")
        return f"login.html rendered ({len(html)} bytes)"
    step("render_login", _render)

    # report the host auto-discovery actually connected to (may differ from the
    # configured env var when an internal host was auto-upgraded to external)
    try:
        from app import db as _db
        resolved = _db._RESOLVED_PG_URL or ""
        out["resolved_host_part"] = resolved.split("@", 1)[1] if "@" in resolved else ""
    except Exception:
        out["resolved_host_part"] = ""

    return jsonify(out)


@bp.route("/health")
def health():
    """Platform self-health (public, no auth). Always returns HTTP 200 so the
    Render health check passes; reports DB connectivity in the payload.
    Engine-agnostic: a tiny SELECT 1 works on both SQLite and PostgreSQL."""
    db_ok = False
    try:
        conn = get_db()
        try:
            conn.execute("SELECT 1").fetchone()
            db_ok = True
        finally:
            conn.close()
    except Exception:
        db_ok = False
    engine = "postgresql" if (Config.DATABASE_URL or "").startswith(("postgres://", "postgresql://")) else "sqlite"
    return jsonify({
        "ok": True,
        "service": "tc-platform",
        "status": "online" if db_ok else "degraded",
        "database": "connected" if db_ok else "unreachable",
        "engine": engine,
        "python": sys.version.split()[0],
        "env": Config.ENV,
    })


@bp.route("/status")
@login_required
def status():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM systems WHERE enabled=1 AND is_integrated=1 ORDER BY sort_order"
        ).fetchall()
    finally:
        conn.close()
    return jsonify(health_svc.check_all(rows, use_cache=False))


@bp.route("/status/<key>")
@login_required
def status_one(key):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM systems WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    if not row:
        return jsonify({"error": "not_found"}), 404
    return jsonify(health_svc.check_system(row, use_cache=False))
