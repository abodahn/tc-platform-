"""
TC Platform — JSON API.

  GET /api/health          platform self health (public, lightweight)
  GET /api/status          live status of every integrated system (auth)
  GET /api/status/<key>    live status of one system (auth)
"""
import sys
from flask import Blueprint, jsonify

from config import Config
from app.db import get_db
from app.auth import login_required
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
