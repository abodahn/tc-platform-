"""
TC Platform — Admin Center.

Manage users, edit integrated-system URLs/ports/launch modes, and review
audit logs. All write actions are audit-logged.
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)
from werkzeug.security import generate_password_hash

from app.db import get_db, log_audit, utcnow
from app.auth import permission_required, current_user
from app.security import all_role_choices, ROLES, validate_password

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@permission_required("access_admin")
def index():
    conn = get_db()
    try:
        users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        systems = conn.execute("SELECT * FROM systems ORDER BY sort_order").fetchall()
        audit = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 50").fetchall()
        counts = {
            "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
            "systems": conn.execute("SELECT COUNT(*) c FROM systems").fetchone()["c"],
            "integrated": conn.execute("SELECT COUNT(*) c FROM systems WHERE is_integrated=1").fetchone()["c"],
            "audit": conn.execute("SELECT COUNT(*) c FROM audit_logs").fetchone()["c"],
        }
    finally:
        conn.close()
    return render_template("admin.html", users=users, systems=systems, audit=audit,
                           roles=all_role_choices(), counts=counts, active="admin")


@bp.route("/users/add", methods=["POST"])
@permission_required("manage_users")
def add_user():
    f = request.form
    username = (f.get("username") or "").strip()
    password = f.get("password") or ""
    full_name = (f.get("full_name") or "").strip()
    email = (f.get("email") or "").strip()
    role = f.get("role") or "normal_user"
    if not username or not password or role not in ROLES:
        flash("user_invalid", "error")
        return redirect(url_for("admin.index") + "#users")
    ok, msg = validate_password(password)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("admin.index") + "#users")
    conn = get_db()
    try:
        exists = conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if exists:
            flash("user_exists", "error")
        else:
            conn.execute(
                """INSERT INTO users (username,password_hash,full_name,email,role,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (username, generate_password_hash(password), full_name, email, role, utcnow()))
            conn.commit()
            log_audit(current_user()["username"], "user_create",
                      f"Created user {username} ({role})", request.remote_addr or "")
            flash("user_added", "success")
    finally:
        conn.close()
    return redirect(url_for("admin.index") + "#users")


@bp.route("/users/<int:uid>/toggle", methods=["POST"])
@permission_required("manage_users")
def toggle_user(uid):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            abort(404)
        if row["username"] == current_user()["username"]:
            flash("cannot_disable_self", "error")
        else:
            newval = 0 if row["is_active"] else 1
            conn.execute("UPDATE users SET is_active=? WHERE id=?", (newval, uid))
            conn.commit()
            log_audit(current_user()["username"], "user_toggle",
                      f"Set {row['username']} active={newval}", request.remote_addr or "")
    finally:
        conn.close()
    return redirect(url_for("admin.index") + "#users")


@bp.route("/systems/<int:sid>/edit", methods=["POST"])
@permission_required("manage_integrations")
def edit_system(sid):
    f = request.form
    base_url = (f.get("base_url") or "").strip()
    health_url = (f.get("health_url") or "").strip()
    port = f.get("port")
    launch_mode = f.get("launch_mode") or "new_tab"
    enabled = 1 if f.get("enabled") == "on" else 0
    owner = (f.get("owner") or "").strip()
    try:
        port = int(port) if port else None
    except ValueError:
        port = None
    if launch_mode not in ("new_tab", "same_tab", "embed"):
        launch_mode = "new_tab"
    conn = get_db()
    try:
        row = conn.execute("SELECT key FROM systems WHERE id=?", (sid,)).fetchone()
        if not row:
            abort(404)
        conn.execute(
            """UPDATE systems SET base_url=?, health_url=?, port=?, launch_mode=?,
               enabled=?, owner=? WHERE id=?""",
            (base_url or None, health_url or None, port, launch_mode, enabled, owner, sid))
        conn.commit()
        log_audit(current_user()["username"], "system_edit",
                  f"Edited integration {row['key']} -> {base_url}", request.remote_addr or "")
        flash("system_saved", "success")
    finally:
        conn.close()
    return redirect(url_for("admin.index") + "#integrations")
