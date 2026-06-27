"""
TC Platform — lightweight CSRF protection (no extra dependency).

A per-session token is required on every state-changing request
(POST/PUT/PATCH/DELETE), supplied either as a form field `_csrf` or an
`X-CSRF-Token` header. Safe methods are never checked. Tokens are compared in
constant time.
"""
import secrets

from flask import session, request, g, abort

_FIELD = "_csrf"
_HEADER = "X-CSRF-Token"
_SAFE = {"GET", "HEAD", "OPTIONS", "TRACE"}
# Endpoints exempt from CSRF (read-only JSON APIs / health probes).
_EXEMPT = {"api.health", "api.status", "api.status_one"}


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
        if not expected or not secrets.compare_digest(str(sent), str(expected)):
            abort(400, description="Invalid or missing CSRF token.")

    @app.context_processor
    def _inject():
        return {"csrf_token": get_token()}
