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
    g.user = dict(row) if row else None
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
