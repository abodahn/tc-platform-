"""
TC Platform — /push: register this device for Web Push (see app/services/push.py).

All routes need a signed-in user; the POSTs go through the app-wide CSRF check
(X-CSRF-Token header, as every other fetch() in the app).
"""
from flask import Blueprint, jsonify, request, session

from app.auth import login_required, current_user
from app.csrf import get_token
from app.db import get_db
from app.services import push

bp = Blueprint("push", __name__, url_prefix="/push")


def _unavailable():
    return jsonify(ok=False, error="push_unavailable"), 503


@bp.route("/key")
@login_required
def key():
    if not push.available():
        return _unavailable()
    # csrf: the service worker re-subscribes on pushsubscriptionchange and has no
    # page to read the meta tag from. Same-origin JSON, so no wider exposure.
    return jsonify(ok=True, key=push.vapid_keys()["public"], csrf=get_token())


@bp.route("/subscribe", methods=["POST"])
@login_required
def subscribe():
    if not push.available():
        return _unavailable()
    if request.content_length and request.content_length > 4096:
        return jsonify(ok=False, error="invalid"), 400
    parsed = push.validate_subscription(request.get_json(silent=True))
    if not parsed:
        return jsonify(ok=False, error="invalid"), 400
    push.note_origin(request.url_root)
    conn = get_db()
    try:
        # remembered so sign-out can unbind this device (auth.logout)
        session["push_sid"] = push.subscribe(conn, current_user()["username"], *parsed,
                                             user_agent=request.headers.get("User-Agent", ""))
    finally:
        conn.close()
    return jsonify(ok=True)


@bp.route("/unsubscribe", methods=["POST"])
@login_required
def unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint") if isinstance(data, dict) else None
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 1024:
        return jsonify(ok=False, error="invalid"), 400
    conn = get_db()
    try:
        removed = push.unsubscribe(conn, current_user()["username"], endpoint)
    finally:
        conn.close()
    return jsonify(ok=True, removed=removed)


@bp.route("/test", methods=["POST"])
@login_required
def send_test():
    if not push.available():
        return _unavailable()
    user = current_user()
    n = push.send_test(user["username"], user.get("lang_pref") or "en")
    if not n:
        return jsonify(ok=False, error="no_devices"), 404
    return jsonify(ok=True, devices=n)
