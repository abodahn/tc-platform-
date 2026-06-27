"""
TC Platform — authentication routes (login / logout).

Includes an in-memory rate limiter / lockout to slow brute-force attempts.
"""
import time

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, g)
from werkzeug.security import check_password_hash

from app.db import get_db, log_audit
from app.auth import current_user

bp = Blueprint("auth", __name__)

# --- Login throttling (in-memory; resets on restart) ---
MAX_FAILS = 5            # failures allowed within the window
WINDOW_SECONDS = 15 * 60  # rolling window
LOCKOUT_SECONDS = 10 * 60  # cooldown once tripped
_fails = {}             # key -> [timestamps]


def _key():
    uname = (request.form.get("username") or "").strip().lower()
    return f"{uname}|{request.remote_addr or '?'}"


def _is_locked(key):
    now = time.time()
    hits = [t for t in _fails.get(key, []) if now - t < WINDOW_SECONDS]
    _fails[key] = hits
    if len(hits) >= MAX_FAILS:
        # locked until the oldest relevant hit ages past the lockout window
        if now - hits[-1] < LOCKOUT_SECONDS:
            return True
    return False


def _record_fail(key):
    _fails.setdefault(key, []).append(time.time())


def _clear(key):
    _fails.pop(key, None)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("main.dashboard"))

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        nxt = request.form.get("next") or url_for("main.dashboard")

        key = _key()
        if _is_locked(key):
            log_audit(username or "(blank)", "login_locked",
                      "Too many failed attempts", request.remote_addr or "")
            flash("too_many_attempts", "error")
            return render_template("login.html", next=request.args.get("next", ""))

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ? AND is_active = 1", (username,)
            ).fetchone()
        finally:
            conn.close()

        if row and check_password_hash(row["password_hash"], password):
            _clear(key)
            session.clear()
            session.permanent = True
            session["uid"] = row["id"]
            g.pop("user", None)
            log_audit(username, "login", "Successful login", request.remote_addr or "")
            # Only allow internal redirects
            if not nxt.startswith("/"):
                nxt = url_for("main.dashboard")
            return redirect(nxt)

        _record_fail(key)
        log_audit(username or "(blank)", "login_failed", "Invalid credentials",
                  request.remote_addr or "")
        flash("invalid_credentials", "error")

    return render_template("login.html", next=request.args.get("next", ""))


@bp.route("/logout")
def logout():
    user = current_user()
    if user:
        log_audit(user["username"], "logout", "User logged out", request.remote_addr or "")
    session.clear()
    g.pop("user", None)
    return redirect(url_for("auth.login"))
