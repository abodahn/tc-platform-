"""
TC Platform — JSON API.

  GET  /api/health             platform self health (public, lightweight)
  GET  /api/status             live status of every integrated system (auth)
  GET  /api/status/<key>       live status of one system (auth)
  POST /api/integrations/sync  update integrated-system URLs (admin creds; used
                               by GO_ONLINE.ps1 to push fresh tunnel URLs)
"""
import sys
from flask import Blueprint, jsonify, request, current_app
from werkzeug.security import check_password_hash

from config import Config
from app.db import get_db
from app.auth import login_required
from app.security import has_permission
from app.services import health as health_svc

bp = Blueprint("api", __name__, url_prefix="/api")


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
        # Whether the app is actually SERVING pages, which is not the same
        # question as whether a probe can reach the database: the schema
        # bootstrap may still be pending, in which case every data page returns
        # the 503 "database unavailable" screen. Ops needs both facts.
        "schema_ready": bool(getattr(current_app, "_db_ready", False)),
        # Why the bootstrap failed, if it did. Without this the only symptom is
        # schema_ready:false and no way to find the cause from outside.
        "bootstrap_error": getattr(current_app, "_db_boot_error", None),
        "engine": engine,
        "python": sys.version.split()[0],
        "env": Config.ENV,
    })


@bp.route("/integrations/sync", methods=["POST"])
def integrations_sync():
    """Update the integrated systems' public URLs from a trusted caller.

    Authenticated with platform admin credentials in the JSON body (not a
    session, so it is CSRF-exempt). Used by GO_ONLINE.ps1 to push the freshly
    generated Cloudflare tunnel URLs so the dashboard health updates without any
    manual editing. Body:
        {"username": "...", "password": "...",
         "systems": {"itsm": "https://...", "assets": "https://...", ...}}
    Only base URLs are needed; the health path is derived automatically.
    """
    from app.db import _INTEGRATION_ENDPOINTS

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    systems = data.get("systems") or {}

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
        ).fetchone()
        if not row or not check_password_hash(row["password_hash"], password):
            return jsonify({"ok": False, "error": "invalid_credentials"}), 401
        if not has_permission(row["role"], "manage_integrations"):
            return jsonify({"ok": False, "error": "forbidden"}), 403

        updated = []
        for key, base in systems.items():
            base = (base or "").strip().rstrip("/")
            ep = _INTEGRATION_ENDPOINTS.get(key)
            if not base or not ep:
                continue
            _port, hpath = ep
            health_url = base + "/" if hpath in ("", "/") else base + hpath
            conn.execute(
                "UPDATE systems SET base_url = ?, health_url = ?, enabled = 1 WHERE key = ?",
                (base, health_url, key))
            updated.append(key)
        conn.commit()
        return jsonify({"ok": True, "updated": updated})
    finally:
        conn.close()


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


@bp.route("/overview")
@login_required
def overview():
    """Consolidated business KPIs pulled from all four systems (cached)."""
    from app.services.integration import fetch_overview
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT key, name_en, base_url, is_integrated FROM systems "
            "WHERE enabled=1 ORDER BY sort_order").fetchall()]
    finally:
        conn.close()
    return jsonify({"systems": fetch_overview(rows)})
