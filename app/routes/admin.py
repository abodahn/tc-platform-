"""
TC Platform — Admin Center.

Manage users, edit integrated-system URLs/ports/launch modes, and review
audit logs. All write actions are audit-logged.
"""
import json

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)
from werkzeug.security import generate_password_hash

from app.db import get_db, log_audit, utcnow
from app.auth import permission_required, current_user
from app.security import (all_role_choices, ROLES, validate_password,
                          has_permission, role_label, PERMISSIONS,
                          effective_roles, refresh_db_roles, permission_catalogue,
                          BUILTIN_ROLE_KEYS)

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@permission_required("access_admin")
def index():
    conn = get_db()
    try:
        users = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        systems = conn.execute("SELECT * FROM systems ORDER BY sort_order").fetchall()
        audit = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 300").fetchall()
        audit_actions = [r["action"] for r in conn.execute(
            "SELECT DISTINCT action FROM audit_logs ORDER BY action").fetchall() if r["action"]]
        counts = {
            "users": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
            "systems": conn.execute("SELECT COUNT(*) c FROM systems").fetchone()["c"],
            "integrated": conn.execute("SELECT COUNT(*) c FROM systems WHERE is_integrated=1").fetchone()["c"],
            "audit": conn.execute("SELECT COUNT(*) c FROM audit_logs").fetchone()["c"],
        }
        user_by_role = {r["role"]: r["c"] for r in conn.execute(
            "SELECT role, COUNT(*) c FROM users GROUP BY role").fetchall()}
    finally:
        conn.close()
    # Full role list (code + admin-managed) for the Roles tab
    eff = effective_roles()
    roles_list = []
    for key in sorted(eff.keys()):
        perms = eff[key]["perms"]
        roles_list.append({
            "key": key, "label": eff[key]["label"], "perms": perms,
            "n_perms": ("all" if "*" in perms else len(perms)),
            "is_builtin": key in BUILTIN_ROLE_KEYS,
            "users": user_by_role.get(key, 0),
        })
    return render_template("admin.html", users=users, systems=systems, audit=audit,
                           roles=all_role_choices(), counts=counts, active="admin",
                           roles_list=roles_list, perm_groups=permission_catalogue(),
                           audit_actions=audit_actions)


@bp.route("/roles/save", methods=["POST"])
@permission_required("manage_users")
def save_role():
    """Create or update an admin-managed role from the Roles editor."""
    f = request.form
    key = (f.get("role_key") or "").strip().lower().replace(" ", "_")[:40]
    label = (f.get("label") or "").strip()[:80]
    perms = [p for p in f.getlist("perms[]") if p in PERMISSIONS]
    if not key or not label:
        flash("role_invalid", "error")
        return redirect(url_for("admin.index") + "#roles")
    if key == "super_admin":
        flash("role_reserved", "error")
        return redirect(url_for("admin.index") + "#roles")
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM custom_roles WHERE role_key=?", (key,)).fetchone()
        is_builtin = 1 if key in BUILTIN_ROLE_KEYS else 0
        pj = json.dumps(perms)
        if row:
            conn.execute("UPDATE custom_roles SET label=?, perms_json=?, updated_at=? WHERE role_key=?",
                         (label, pj, utcnow(), key))
        else:
            conn.execute(
                "INSERT INTO custom_roles (role_key,label,perms_json,is_builtin,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)", (key, label, pj, is_builtin, utcnow(), utcnow()))
        conn.commit()
    finally:
        conn.close()
    refresh_db_roles()
    log_audit(current_user()["username"], "role_save",
              f"{key}: {len(perms)} perms", request.remote_addr or "")
    flash("role_saved", "success")
    return redirect(url_for("admin.index") + "#roles")


@bp.route("/roles/<role_key>/delete", methods=["POST"])
@permission_required("manage_users")
def delete_role(role_key):
    """Delete an admin-managed role. If it overrides a code role, this reverts to
    the code default. Blocked while users are still assigned to it."""
    conn = get_db()
    try:
        in_use = conn.execute("SELECT COUNT(*) c FROM users WHERE role=?", (role_key,)).fetchone()["c"]
        is_code_only = role_key in BUILTIN_ROLE_KEYS and not conn.execute(
            "SELECT 1 FROM custom_roles WHERE role_key=?", (role_key,)).fetchone()
        if is_code_only:
            flash("role_builtin_locked", "error")
        elif in_use and role_key not in BUILTIN_ROLE_KEYS:
            flash("role_in_use", "error")   # custom role still assigned -> reassign first
        else:
            conn.execute("DELETE FROM custom_roles WHERE role_key=?", (role_key,))
            conn.commit()
            refresh_db_roles()
            log_audit(current_user()["username"], "role_delete", role_key, request.remote_addr or "")
            flash("role_deleted", "success")
    finally:
        conn.close()
    return redirect(url_for("admin.index") + "#roles")


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


@bp.route("/users/<int:uid>/edit", methods=["POST"])
@permission_required("manage_users")
def edit_user(uid):
    """Update an existing user's name, email and role, and optionally reset their
    password. Guards against an admin accidentally removing their own admin access."""
    f = request.form
    full_name = (f.get("full_name") or "").strip()
    email = (f.get("email") or "").strip()
    role = f.get("role") or "normal_user"
    new_password = f.get("password") or ""
    # extra per-user permissions (granted on top of the role)
    extra = [p for p in f.getlist("perms[]") if p in PERMISSIONS]
    extra_json = json.dumps(extra)
    from app.security import effective_roles as _eff
    if role not in _eff():
        flash("user_invalid", "error")
        return redirect(url_for("admin.index") + "#users")
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            abort(404)
        # Don't let the current admin strip their own admin access and get locked out
        # (consider both the role and any extra per-user grants).
        grants_admin = (has_permission(role, "access_admin")
                        or "access_admin" in extra or "*" in extra)
        if row["username"] == current_user()["username"] and not grants_admin:
            flash("cannot_demote_self", "error")
            return redirect(url_for("admin.index") + "#users")
        if new_password:
            ok, msg = validate_password(new_password)
            if not ok:
                flash(msg, "error")
                return redirect(url_for("admin.index") + "#users")
            conn.execute(
                "UPDATE users SET full_name=?, email=?, role=?, extra_perms=?, password_hash=? WHERE id=?",
                (full_name, email, role, extra_json, generate_password_hash(new_password), uid))
        else:
            conn.execute("UPDATE users SET full_name=?, email=?, role=?, extra_perms=? WHERE id=?",
                         (full_name, email, role, extra_json, uid))
        conn.commit()
        log_audit(current_user()["username"], "user_edit",
                  f"Edited {row['username']}: name={full_name}, role={role}, "
                  f"+{len(extra)} extra perms" + (", password reset" if new_password else ""),
                  request.remote_addr or "")
        flash("user_saved", "success")
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
        # Convenience: if only the Base URL is provided, auto-derive the Health URL
        # from the known endpoint path for this system, so an admin can paste just
        # one address per system (e.g. a Cloudflare tunnel URL).
        if base_url and not health_url:
            from app.db import _INTEGRATION_ENDPOINTS
            ep = _INTEGRATION_ENDPOINTS.get(row["key"])
            if ep:
                _p, hpath = ep
                health_url = base_url.rstrip("/") + ("/" if hpath in ("", "/") else hpath)
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
