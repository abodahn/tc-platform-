"""
TC Platform — Flask application factory.

Wires up configuration, security headers, the database, and blueprints.
"""
import shutil
import threading
import time
from pathlib import Path

from flask import Flask, render_template, request

from config import Config
from app.db import init_db


def create_app():
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY
    app.permanent_session_lifetime = Config.SESSION_MINUTES * 60

    # Error tracking via Sentry (optional — only if TC_SENTRY_DSN is set).
    if Config.SENTRY_DSN:
        try:
            import sentry_sdk
            from sentry_sdk.integrations.flask import FlaskIntegration
            sentry_sdk.init(dsn=Config.SENTRY_DSN, integrations=[FlaskIntegration()],
                            traces_sample_rate=0.0, environment=Config.ENV)
        except Exception as exc:  # noqa: BLE001
            app.logger.warning("Sentry init skipped: %s", exc)

    # Session cookie hardening + upload limit
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=Config.IS_PRODUCTION,  # HTTPS-only in production
        MAX_CONTENT_LENGTH=Config.MAX_CONTENT_LENGTH,
    )
    Config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Branding: if a logo file is dropped in the PROJECT ROOT (next to run.py),
    # adopt it into static/img so the whole UI uses it. Easiest place for users.
    try:
        img_dir = Path(app.static_folder) / "img"
        for ext in ("png", "svg", "jpg", "jpeg", "webp"):
            src = Config.BASE_DIR / f"logo.{ext}"
            if src.exists():
                shutil.copyfile(src, img_dir / f"logo.{ext}")
                break
    except Exception:
        pass

    # Ensure metadata DB exists and is seeded (non-destructive, idempotent).
    # NON-FATAL: if the database is briefly unreachable at boot (e.g. cold-start
    # ordering on Render), the web service must still start so the health check
    # passes — then we retry the schema bootstrap on the first request that
    # successfully reaches the database.
    app._db_ready = False
    try:
        init_db()
        app._db_ready = True
        from app.security import refresh_db_roles
        refresh_db_roles()               # load admin-managed roles overlay
    except Exception as exc:  # noqa: BLE001
        app.logger.warning("init_db deferred (database not ready yet): %s", exc)

    # Retrying the schema bootstrap on EVERY request is what turned a database
    # blip into a total outage: init_db runs ~620 statements and each connection
    # attempt blocks for connect_timeout seconds, so with 2 workers x 4 threads
    # all eight slots stalled, gunicorn's 120s timeout killed the workers, and
    # Render served 502 for everything — including the login page, which needs
    # no database at all.
    #
    # Now: ONE thread may retry, at most once every _DB_RETRY_SECONDS, and it
    # never blocks the other seven. Anything that does not need the database
    # (static assets, the health probe) is served throughout, and a request that
    # does need it gets an honest 503 page immediately instead of hanging.
    # Recovery is automatic — the next retry after Postgres returns brings the
    # app back with no redeploy.
    _DB_RETRY_SECONDS = 30
    app._db_next_try = 0.0
    _db_retry_lock = threading.Lock()
    _DB_FREE_PATHS = ("/api/health", "/healthz", "/favicon.ico")

    @app.before_request
    def _ensure_db_ready():
        if getattr(app, "_db_ready", False):
            return
        path = request.path or ""
        if path.startswith("/static/") or path in _DB_FREE_PATHS:
            return
        now = time.monotonic()
        if now >= app._db_next_try and _db_retry_lock.acquire(blocking=False):
            try:
                app._db_next_try = now + _DB_RETRY_SECONDS   # set BEFORE trying,
                init_db()                                    # so a slow failure
                app._db_ready = True                         # cannot be retried
                from app.security import refresh_db_roles    # by the next thread
                refresh_db_roles()
                app.logger.warning("database reachable again — schema bootstrap done")
            except Exception as exc:  # noqa: BLE001
                app.logger.warning("database still unreachable: %s", exc)
            finally:
                _db_retry_lock.release()
        if not getattr(app, "_db_ready", False):
            return render_template("db_unavailable.html"), 503

    # CSRF protection for all state-changing requests
    from app.csrf import init_csrf
    init_csrf(app)

    # --- Security headers (defence in depth) ---
    @app.after_request
    def set_secure_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-XSS-Protection", "0")
        return resp

    # --- Blueprints ---
    from app.routes.auth import bp as auth_bp
    from app.routes.main import bp as main_bp
    from app.routes.admin import bp as admin_bp
    from app.routes.api import bp as api_bp
    from app.routes.production import bp as production_bp
    from app.routes.maintenance import bp as maintenance_bp
    from app.routes.sso import bp as sso_bp
    from app.routes.bi import bp as bi_bp
    from app.routes.approvals import bp as approvals_bp
    from app.routes.garamento import bp as garamento_bp
    from app.routes.intelligence import bp as intelligence_bp
    from app.routes.smartfactory import bp as smartfactory_bp
    from app.routes.probation import bp as probation_bp
    from app.routes.accounts import bp as accounts_bp
    from app.routes.compliance import bp as compliance_bp
    from app.routes.orders import bp as orders_bp
    from app.routes.plm import bp as plm_bp
    from app.routes.warehouse import bp as warehouse_bp
    from app.routes.costing import bp as costing_bp
    from app.routes.planning import bp as planning_bp
    from app.routes.cutroom import bp as cutroom_bp
    from app.routes.mes import bp as mes_bp
    from app.routes.quality import bp as quality_bp
    from app.routes.wash import bp as wash_bp
    from app.routes.trace import bp as trace_bp
    from app.routes.shipping import bp as shipping_bp
    from app.routes.people import bp as people_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(production_bp)
    app.register_blueprint(maintenance_bp)
    app.register_blueprint(sso_bp)
    app.register_blueprint(bi_bp)
    app.register_blueprint(approvals_bp)
    app.register_blueprint(garamento_bp)
    app.register_blueprint(intelligence_bp)
    app.register_blueprint(smartfactory_bp)
    app.register_blueprint(probation_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(compliance_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(plm_bp)
    app.register_blueprint(warehouse_bp)
    app.register_blueprint(costing_bp)
    app.register_blueprint(planning_bp)
    app.register_blueprint(cutroom_bp)
    app.register_blueprint(mes_bp)
    app.register_blueprint(quality_bp)
    app.register_blueprint(wash_bp)
    app.register_blueprint(trace_bp)
    app.register_blueprint(shipping_bp)
    app.register_blueprint(people_bp)

    # Cross-cutting surfaces: the shared reporting hub (every module's reports are
    # served by these six generic routes) and the governance matrix. Both import
    # cleanly with no database work, so they add nothing to boot time — which
    # matters here, because --preload runs create_app() before the port is bound.
    from app.routes.reports_hub import bp as reports_hub_bp
    from app.routes.governance import bp as governance_bp
    app.register_blueprint(reports_hub_bp)
    app.register_blueprint(governance_bp)
    from app.backup import bp as backup_bp
    app.register_blueprint(backup_bp)

    # --- Error handlers ---
    from flask import render_template

    @app.errorhandler(400)
    def bad_request(e):
        msg = getattr(e, "description", "Bad request.")
        return render_template("error.html", code=400, message=msg), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               message="You do not have permission to view this page."), 403

    @app.errorhandler(413)
    def too_large(e):
        mb = Config.MAX_CONTENT_LENGTH // (1024 * 1024)
        return render_template("error.html", code=413,
                               message=f"The file is too large (max {mb} MB). Please upload a smaller photo."), 413

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404,
                               message="The page you are looking for was not found."), 404

    @app.errorhandler(500)
    @app.errorhandler(Exception)
    def server_error(e):
        # Let Flask's own handlers deal with HTTP errors (404/403/...); only
        # treat genuine unhandled exceptions as 500s to log + alert.
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e
        try:
            from flask import request
            from app.services.alerts import report_error
            report_error(e, getattr(request, "path", ""))
        except Exception:  # noqa: BLE001
            app.logger.exception("error while reporting 500")
        return render_template("error.html", code=500,
                               message="Something went wrong. The team has been notified."), 500

    return app
