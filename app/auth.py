"""
TC Platform — authentication & authorization helpers.
"""
from functools import wraps

from flask import session, redirect, url_for, request, abort, g

from app.db import get_db
from app.security import has_permission, role_label, user_has_permission


def current_user():
    """Return the logged-in user row (dict) or None. Cached on g per request."""
    if "user" in g:
        return g.user
    uid = session.get("uid")
    if not uid:
        g.user = None
        return None
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (uid,)).fetchone()
    finally:
        conn.close()
    g.user = None
    if row:
        # Session-epoch guard: a password reset / suspend / admin action bumps the
        # user's session_epoch, which ends their previously-issued sessions on the
        # next request. Sessions issued before this feature have no "ep" (==0) and
        # existing users default to session_epoch 0, so nobody is logged out on upgrade.
        try:
            row_ep = row["session_epoch"]
        except Exception:  # noqa: BLE001
            row_ep = 0
        row_ep = row_ep if row_ep is not None else 0
        if session.get("ep", 0) != row_ep:
            session.clear()
            return None
        g.user = dict(row)
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def permission_required(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("auth.login", next=request.path))
            if not user_has_permission(user, permission):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def user_can(permission):
    user = current_user()
    return bool(user) and user_has_permission(user, permission)


def user_role_label():
    user = current_user()
    return role_label(user["role"]) if user else ""
