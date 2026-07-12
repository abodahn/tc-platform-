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
from app.accounts import services as svc

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

        # Accept email OR username OR employee id (existing username logins unchanged).
        row = svc.find_login_user(username)

        if row and check_password_hash(row["password_hash"], password):
            # Correct password — but the account may not be allowed a session yet
            # (unverified / pending approval / suspended / locked). Guide, don't grant.
            reason = svc.login_block_reason(row)
            if reason:
                log_audit(username, "login_blocked", reason, request.remote_addr or "")
                try:
                    svc.audit("login_blocked", target_id=row["id"], target_ref=username, result=reason)
                except Exception:  # noqa: BLE001
                    pass
                flash(reason, "error")
                return render_template("login.html", next=request.args.get("next", ""),
                                       blocked=reason, ident=username)
            _clear(key)
            session.clear()
            session.permanent = True
            session["uid"] = row["id"]
            try:
                ep = row["session_epoch"]
            except Exception:  # noqa: BLE001
                ep = 0
            session["ep"] = ep if ep is not None else 0
            g.pop("user", None)
            svc.on_login_success(row["id"])
            log_audit(row["username"], "login", "Successful login", request.remote_addr or "")
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
