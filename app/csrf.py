"""
TC Platform — lightweight CSRF protection (no extra dependency).

A per-session token is required on every state-changing request
(POST/PUT/PATCH/DELETE), supplied either as a form field `_csrf` or an
`X-CSRF-Token` header. Safe methods are never checked. Tokens are compared in
constant time.
"""
import secrets

from flask import session, request, g, abort, redirect, url_for, flash

_FIELD = "_csrf"
_HEADER = "X-CSRF-Token"
_SAFE = {"GET", "HEAD", "OPTIONS", "TRACE"}
# Endpoints exempt from CSRF (read-only JSON APIs / health probes, and the
# credential-authenticated integrations sync used by GO_ONLINE.ps1).
_EXEMPT = {"api.health", "api.status", "api.status_one", "api.integrations_sync"}


def get_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_hex(32)
        session["_csrf_token"] = token
    return token


def init_csrf(app):
    @app.before_request
    def _protect():
        # Ensure a token always exists for templates to render.
        get_token()
        if request.method in _SAFE:
            return
        if request.endpoint in _EXEMPT:
            return
        sent = request.form.get(_FIELD) or request.headers.get(_HEADER) or ""
        expected = session.get("_csrf_token", "")
        if expected and secrets.compare_digest(str(sent), str(expected)):
            return
        # Token missing or mismatched. In practice this is almost always an
        # expired or rotated session (e.g. after a redeploy) rather than an
        # attack. For a normal browser navigation (a form POST, which sends
        # `Accept: text/html`) bounce the user to login with a clear message
        # instead of a dead-end 400 page. Programmatic callers (fetch/XHR/API,
        # whose Accept is */* or application/json) still get a hard 400 so they
        # can handle it in code.
        if "text/html" in (request.headers.get("Accept") or ""):
            try:
                flash("Your session expired. Please sign in again, then retry.", "warning")
            except Exception:
                pass
            return redirect(url_for("auth.login"))
        abort(400, description="Invalid or missing CSRF token.")

    @app.context_processor
    def _inject():
        return {"csrf_token": get_token()}
