"""
TC Platform — main application routes.

Command Center, App Launcher, module pages (integrated + future-ready),
Reports, Health, Roadmap, and user preference persistence.
"""
import csv
import io
import sys
import platform as pyplatform

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, jsonify, abort, g, Response, flash)

from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from app.db import get_db, log_audit
from app.auth import login_required, permission_required, current_user, user_can
from app.security import has_permission, role_label, ROLES, validate_password
from app.navigation import NAV
from app.services import health as health_svc
from app.services import seed_content as sc
from app.services.notify import sync_health_notifications

bp = Blueprint("main", __name__)


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _systems():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM systems WHERE enabled = 1 ORDER BY sort_order").fetchall()
    finally:
        conn.close()
    return rows


def _system_by_key(key):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM systems WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return row


def _unread_notifications():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY id DESC LIMIT 30").fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE is_read = 0").fetchone()["c"]
    finally:
        conn.close()
    return rows, unread


# --------------------------------------------------------------------------
# Context processor — inject shared data into every template
# --------------------------------------------------------------------------
def _brand_logo_file():
    """Use a real uploaded logo if present (img/logo.png|jpg|svg|webp), else the
    built-in SVG mark. Drop your file at app/static/img/logo.<ext> to use it."""
    from flask import current_app
    import os
    base = os.path.join(current_app.static_folder, "img")
    for name in ("logo.png", "logo.svg", "logo.jpg", "logo.jpeg", "logo.webp"):
        if os.path.exists(os.path.join(base, name)):
            return "img/" + name
    return "img/logo-mark.svg"


@bp.app_context_processor
def inject_globals():
    user = current_user()
    notifs, unread = ([], 0)
    visible_nav = []
    if user:
        notifs, unread = _unread_notifications()
        for section in NAV:
            items = [it for it in section["items"]
                     if has_permission(user["role"], it[4])]
            if items:
                visible_nav.append({"section": section["section"], "items": items})
    return {
        "cu": user,
        "cu_role_label": role_label(user["role"]) if user else "",
        "nav": visible_nav,
        "notifications": notifs,
        "unread_count": unread,
        "can": user_can,
        "app_name": "TC Platform",
        "app_subtitle": "Unified Digital Operations, IT, AI & Business Control Center",
        "brand_logo": _brand_logo_file(),
    }


# --------------------------------------------------------------------------
# Command Center (executive dashboard)
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    systems = _systems()
    statuses = health_svc.check_all(systems)
    sync_health_notifications(systems, statuses)
    online = sum(1 for s in statuses.values() if s["status"] == "online")
    total_int = sum(1 for s in systems if s["is_integrated"])

    # Executive KPI tiles (live where available, curated placeholders otherwise)
    kpis = {
        "open_tickets": 128,
        "sla_health": 94,
        "critical_tickets": 6,
        "assets_total": 1742,
        "assets_maintenance": 23,
        "low_stock": 9,
        "servers_online": online,
        "servers_total": total_int,
        "cpu_alerts": 2,
        "active_projects": 17,
        "overdue_tasks": 11,
        "pending_approvals": 5,
        "finance_progress": 62,
        "automation_progress": 48,
        "ai_progress": 35,
        "production_readiness": 40,
        "cost_saving": "₺ 2.6M",
    }
    _, unread = _unread_notifications()
    from app.insights import executive_summary
    briefing = executive_summary()
    return render_template("dashboard.html",
                           systems=systems, statuses=statuses, kpis=kpis, briefing=briefing,
                           roadmap=sc.ROADMAP, active="command_center")


# --------------------------------------------------------------------------
# Application Launcher
# --------------------------------------------------------------------------
@bp.route("/launcher")
@permission_required("open_module")
def launcher():
    systems = _systems()
    statuses = health_svc.check_all(systems)
    return render_template("launcher.html", systems=systems, statuses=statuses,
                           active="launcher")


# --------------------------------------------------------------------------
# Generic module dispatcher (integrated apps + future-ready modules)
# --------------------------------------------------------------------------
MODULE_TABLES = {
    "ai_hub": {
        "intro": "module.ai_intro",
        "columns": ["Use Case", "Department", "Business Problem", "Expected Saving",
                    "Status", "Owner", "Priority", "Phase"],
        "rows": sc.AI_USE_CASES,
    },
    "automation": {
        "intro": "module.automation_intro",
        "columns": ["Process", "Department", "Manual Effort", "Frequency", "Tool",
                    "Status", "Time Saving", "Risk", "Owner"],
        "rows": sc.AUTOMATION_PROCESSES,
    },
    "finance": {
        "intro": "module.finance_intro",
        "columns": ["Initiative", "Type", "Expected Saving", "Status", "Owner", "Notes"],
        "rows": sc.FINANCE_ITEMS,
    },
    "bi": {
        "intro": "module.bi_intro",
        "columns": ["Dashboard", "Owner", "Refresh", "Status", "Last Update"],
        "rows": sc.BI_DASHBOARDS,
    },
    "production": {
        "intro": "module.production_intro",
        "columns": ["Line", "Status", "Efficiency", "Quality Issues", "Maint. Requests", "Risk"],
        "rows": sc.PRODUCTION_LINES,
    },
    "hr": {
        "intro": "module.hr_intro",
        "columns": ["Service", "Status", "Description"],
        "rows": sc.HR_SERVICES,
    },
    "procurement": {
        "intro": "module.procurement_intro",
        "columns": ["PR #", "Items", "Department", "Status", "Value"],
        "rows": sc.PROCUREMENT_ITEMS,
    },
    "governance": {
        "intro": "module.governance_intro",
        "columns": ["Area", "Status", "Description"],
        "rows": sc.GOVERNANCE_ITEMS,
    },
    "kb": {
        "intro": "module.kb_intro",
        "columns": ["Article", "Category", "Summary"],
        "rows": [("Reset your TC Platform password", "Account", "Step-by-step password reset"),
                 ("How to open a service desk ticket", "ITSM", "Submitting and tracking tickets"),
                 ("Requesting a new asset", "Assets", "Procurement and assignment flow"),
                 ("Reading the monitoring dashboard", "Monitoring", "Understanding server status"),
                 ("Submitting a project task", "Work", "Creating tasks in CommandTrack")],
    },
    "saplite": {
        "intro": "module.saplite_intro",
        "columns": ["Request", "Module", "Status"],
        "rows": [("Master data update", "MM", "Open"),
                 ("Cost center mapping", "CO", "In progress"),
                 ("Vendor onboarding", "MM", "Planned")],
    },
}


@bp.route("/module/<key>")
@permission_required("open_module")
def module(key):
    # Production Visibility is a real working module with its own blueprint.
    if key == "production":
        return redirect(url_for("production.index"))

    row = _system_by_key(key)
    if not row or not row["enabled"]:
        abort(404)

    # Integrated apps -> embedded viewer (the platform concept) by default.
    # ?view=details shows the technical/launch info page instead.
    if row["is_integrated"]:
        status = health_svc.check_system(row)
        if request.args.get("view") == "details":
            return render_template("modules/integrated.html", system=row,
                                   status=status, active=key)
        return render_template("modules/embedded.html", system=row, status=status,
                               active=key)

    # Future-ready modules -> explanation + structured placeholder tables
    table = MODULE_TABLES.get(key)
    about = sc.MODULE_ABOUT.get(key)
    return render_template("modules/generic.html", system=row, table=table,
                           about=about, active=key)


# --------------------------------------------------------------------------
# Reports Center
# --------------------------------------------------------------------------
@bp.route("/reports")
@permission_required("view_reports")
def reports():
    return render_template("reports.html", reports=sc.REPORTS,
                           exports=CSV_EXPORTS, active="reports")


# Real CSV exports. Each entry: key -> (label, headers, SQL).
CSV_EXPORTS = {
    "systems": ("Systems registry",
                ["key", "name_en", "category", "base_url", "health_url", "port",
                 "owner", "criticality", "is_integrated", "enabled"],
                "SELECT key,name_en,category,base_url,health_url,port,owner,"
                "criticality,is_integrated,enabled FROM systems ORDER BY sort_order"),
    "audit": ("Audit log",
              ["id", "username", "action", "detail", "ip", "created_at"],
              "SELECT id,username,action,detail,ip,created_at FROM audit_logs ORDER BY id DESC"),
    "production_lines": ("Production lines",
                         ["name", "area", "status", "shift", "target_output",
                          "actual_output", "operators", "updated_at"],
                         "SELECT name,area,status,shift,target_output,actual_output,"
                         "operators,updated_at FROM production_lines ORDER BY area,name"),
    "production_downtime": ("Production downtime",
                            ["line", "reason", "category", "minutes", "occurred_at"],
                            "SELECT l.name AS line, d.reason, d.category, d.minutes, d.occurred_at "
                            "FROM production_downtime d LEFT JOIN production_lines l ON l.id=d.line_id "
                            "ORDER BY d.id DESC"),
    "production_quality": ("Production quality",
                           ["line", "issue", "severity", "quantity", "status", "created_at"],
                           "SELECT l.name AS line, q.issue, q.severity, q.quantity, q.status, q.created_at "
                           "FROM production_quality q LEFT JOIN production_lines l ON l.id=q.line_id "
                           "ORDER BY q.id DESC"),
    "notifications": ("Notifications",
                      ["id", "severity", "module", "title", "message", "is_read", "created_at"],
                      "SELECT id,severity,module,title,message,is_read,created_at "
                      "FROM notifications ORDER BY id DESC"),
}


@bp.route("/reports/export/<key>.csv")
@permission_required("export_reports")
def export_csv(key):
    spec = CSV_EXPORTS.get(key)
    if not spec:
        abort(404)
    label, headers, sql = spec
    conn = get_db()
    try:
        rows = conn.execute(sql).fetchall()
    finally:
        conn.close()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([r[h] for h in headers])
    log_audit(current_user()["username"], "report_export",
              f"Exported {key}.csv ({len(rows)} rows)", request.remote_addr or "")
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=tc_{key}.csv"})


# --------------------------------------------------------------------------
# Technical Health page
# --------------------------------------------------------------------------
@bp.route("/sw.js")
def service_worker():
    """Serve the service worker from root so its scope covers the whole app."""
    from flask import send_from_directory, current_app
    resp = send_from_directory(current_app.static_folder, "sw.js",
                               mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@bp.route("/health")
@permission_required("view_system_health")
def health():
    systems = _systems()
    statuses = health_svc.check_all(systems, use_cache=False)
    sync_health_notifications(systems, statuses)
    info = {
        "python": sys.version.split()[0],
        "platform": pyplatform.platform(),
        "env": Config.ENV,
        "db_path": str(Config.DB_PATH),
        "db_ok": Config.DB_PATH.exists(),
        "backup_dir": str(Config.BACKUP_DIR),
        "backup_ok": Config.BACKUP_DIR.exists(),
    }
    return render_template("health.html", systems=systems, statuses=statuses,
                           info=info, active="health")


# --------------------------------------------------------------------------
# Roadmap
# --------------------------------------------------------------------------
@bp.route("/roadmap")
@login_required
def roadmap():
    return render_template("roadmap.html", roadmap=sc.ROADMAP, active="roadmap")


# --------------------------------------------------------------------------
# Profile + self-service password change
# --------------------------------------------------------------------------
@bp.route("/profile")
@login_required
def profile():
    return render_template("profile.html", active="profile")


@bp.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    user = current_user()
    f = request.form
    current = f.get("current_password") or ""
    new = f.get("new_password") or ""
    confirm = f.get("confirm_password") or ""

    conn = get_db()
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
        if not row or not check_password_hash(row["password_hash"], current):
            flash("pw_current_wrong", "error")
            return redirect(url_for("main.profile"))
        if new != confirm:
            flash("pw_mismatch", "error")
            return redirect(url_for("main.profile"))
        ok, msg = validate_password(new)
        if not ok:
            flash(msg, "error")
            return redirect(url_for("main.profile"))
        conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                     (generate_password_hash(new), user["id"]))
        conn.commit()
        log_audit(user["username"], "password_change", "User changed own password",
                  request.remote_addr or "")
        flash("pw_changed", "success")
    finally:
        conn.close()
    return redirect(url_for("main.profile"))


# --------------------------------------------------------------------------
# User preferences (theme + language) — persisted to profile
# --------------------------------------------------------------------------
@bp.route("/prefs", methods=["POST"])
@login_required
def prefs():
    user = current_user()
    data = request.get_json(silent=True) or request.form
    theme = data.get("theme")
    lang = data.get("lang")
    fields, values = [], []
    if theme in ("light", "dark", "auto"):
        fields.append("theme_pref = ?"); values.append(theme)
    if lang in ("en", "ar", "tr"):
        fields.append("lang_pref = ?"); values.append(lang)
    if fields:
        values.append(user["id"])
        conn = get_db()
        try:
            conn.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
            conn.commit()
        finally:
            conn.close()
        g.pop("user", None)
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Notifications: mark read
# --------------------------------------------------------------------------
@bp.route("/notifications/read", methods=["POST"])
@login_required
def mark_notifications_read():
    # With an `id` (JSON or form) mark just that one read (clicking a single
    # notification); without one, mark all unread read ("Mark all read").
    nid = None
    if request.is_json:
        nid = (request.get_json(silent=True) or {}).get("id")
    nid = nid or request.form.get("id")
    try:
        nid = int(nid) if nid not in (None, "") else None
    except (TypeError, ValueError):
        nid = None
    conn = get_db()
    try:
        if nid:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (nid,))
        else:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0")
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/notifications/feed")
@login_required
def notifications_feed():
    """Lightweight JSON feed the top bar polls so brand-new notifications can
    chime, pop up and update the bell live without a page reload."""
    notifs, unread = _unread_notifications()
    items = [{
        "id": n["id"], "severity": n["severity"], "module": n["module"],
        "title": n["title"], "message": n["message"],
        "created_at": n["created_at"], "is_read": n["is_read"],
    } for n in notifs]
    max_id = max((it["id"] for it in items), default=0)
    return jsonify({"unread": unread, "max_id": max_id, "items": items})


# --------------------------------------------------------------------------
# Global search (phase 1: platform metadata + app names)
# --------------------------------------------------------------------------
@bp.route("/search")
@login_required
def search():
    """Platform-wide smart search (offline): modules + maintenance machines, spare
    parts and tickets + production lines. Substring + fuzzy (difflib) matching."""
    import difflib
    q = (request.args.get("q") or "").strip().lower()
    results = []
    if not q:
        return jsonify({"q": q, "results": results})

    user = current_user()

    def fuzzy(hay):
        hay = (hay or "").lower()
        if q in hay:
            return True
        # typo tolerance on individual words
        return any(difflib.SequenceMatcher(None, q, w).ratio() >= 0.82 for w in hay.split())

    # 1) modules / apps
    for row in _systems():
        hay = " ".join(filter(None, [row["name_en"], row["name_ar"], row["name_tr"],
                                     row["desc_en"], row["key"], row["category"]]))
        if fuzzy(hay):
            results.append({"type": "module", "name": row["name_en"],
                            "sub": row["category"], "url": url_for("main.module", key=row["key"])})

    conn = get_db()
    try:
        if has_permission(user["role"], "maint_view"):
            for m in conn.execute("SELECT id,code,name FROM mnt_machines WHERE is_active=1"):
                if fuzzy(f"{m['code']} {m['name']}"):
                    results.append({"type": "machine", "name": f"{m['code']} · {m['name']}",
                                    "sub": "Machine", "url": url_for("maintenance.machine_profile", mid=m["id"])})
            for s in conn.execute("SELECT id,code,name FROM mnt_spare_parts WHERE is_active=1"):
                if fuzzy(f"{s['code']} {s['name']}"):
                    results.append({"type": "spare", "name": f"{s['code']} · {s['name']}",
                                    "sub": "Spare part", "url": url_for("maintenance.spare_profile", sid=s["id"])})
            for t in conn.execute("SELECT id,ticket_no,machine_code,description FROM mnt_tickets WHERE is_active=1 ORDER BY id DESC LIMIT 200"):
                if fuzzy(f"{t['ticket_no']} {t['machine_code']} {t['description']}"):
                    results.append({"type": "ticket", "name": f"{t['ticket_no']} · {t['machine_code']}",
                                    "sub": "Ticket", "url": url_for("maintenance.ticket_detail", tid=t["id"])})
        if has_permission(user["role"], "open_module"):
            for p in conn.execute("SELECT name,area FROM production_lines"):
                if fuzzy(f"{p['name']} {p['area']}"):
                    results.append({"type": "production", "name": p["name"], "sub": "Production line",
                                    "url": url_for("production.index")})
    finally:
        conn.close()
    return jsonify({"q": q, "results": results[:25]})
