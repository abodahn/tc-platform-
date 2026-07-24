"""
TC Platform — main application routes.

Command Center, App Launcher, module pages (integrated + future-ready),
Reports, Health, Roadmap, and user preference persistence.
"""
import csv
import io
import sys
import platform as pyplatform
from datetime import date as _date, timedelta as _timedelta

from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, jsonify, abort, g, Response, flash, send_file)

from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from app.db import get_db, log_audit, utcnow
from app.auth import login_required, permission_required, current_user, user_can
from app.security import (has_permission, role_label, ROLES, validate_password,
                          user_has_permission, system_scope)
from app.navigation import NAV
from app.services import health as health_svc
from app.services import seed_content as sc
from app.services import reports as reports_svc
from app.services.notify import sync_health_notifications, sync_system_notifications

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


def _unread_notifications(username=None):
    """Bell feed: broadcast notifications (target_user IS NULL) plus any addressed
    to this user. Older rows created before the target_user column are broadcast."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM notifications WHERE target_user IS NULL OR target_user = ? "
            "ORDER BY id DESC LIMIT 30", (username,)).fetchall()
        unread = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE is_read = 0 "
            "AND (target_user IS NULL OR target_user = ?)", (username,)).fetchone()["c"]
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


# Blueprints a scope-locked user may reach: infra + their own system's launch
# goes through main/sso (self-gated). Feature blueprints are bounced. `api` and
# `garamento` stay open so the global header polling / assistant widget keep
# working on the pages they CAN see.
_SCOPE_OK_BLUEPRINTS = {None, "main", "auth", "sso", "accounts", "api", "garamento"}


@bp.before_app_request
def _enforce_system_scope():
    """Belt-and-suspenders for scope-locked roles (e.g. itsm_user): block every
    out-of-scope feature blueprint outright, so a hand-typed URL like /bi or
    /production can't bypass the hidden nav. Redirects them to their own system."""
    scope = system_scope(current_user())
    if scope is None:
        return
    if request.endpoint == "static" or request.blueprint in _SCOPE_OK_BLUEPRINTS:
        return
    return redirect(url_for("main.dashboard"))


@bp.app_context_processor
def inject_globals():
    user = current_user()
    notifs, unread = ([], 0)
    visible_nav = []
    if user:
        notifs, unread = _unread_notifications(user["username"])
        from app.navigation import WIP_KEYS
        is_admin = user_has_permission(user, "access_admin")
        scope = system_scope(user)   # None = unrestricted; a set = only those keys
        if scope is not None:
            # Scope-locked users only see bell alerts for their own system(s).
            notifs = [n for n in notifs if not n["module"] or n["module"] in scope]
            unread = sum(1 for n in notifs if not n["is_read"])
        for section in NAV:
            items = [it for it in section["items"]
                     if user_has_permission(user, it[4]) and it[0] not in WIP_KEYS
                     and (scope is None or it[0] in scope)]
            if items:
                visible_nav.append({"section": section["section"], "items": items})
        # Admins additionally see the not-yet-built modules under "In Progress".
        if is_admin and not scope:
            wip = [it for section in NAV for it in section["items"]
                   if user_has_permission(user, it[4]) and it[0] in WIP_KEYS]
            if wip:
                visible_nav.append({"section": "nav.in_progress", "items": wip})
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
    # Scope-locked users (e.g. itsm_user) never see the command center — send
    # them straight to their system (single scope) or the filtered launcher.
    scope = system_scope(current_user())
    if scope:
        if len(scope) == 1:
            return redirect(url_for("main.module", key=next(iter(scope))))
        return redirect(url_for("main.launcher"))
    systems = _systems()
    statuses = health_svc.check_all(systems)
    sync_health_notifications(systems, statuses)
    online = sum(1 for s in statuses.values() if s["status"] == "online")
    total_int = sum(1 for s in systems if s["is_integrated"])

    # Executive KPI tiles — pulled LIVE from each system's integration summary
    # (the same source the Business Overview panel uses), so the Command Center
    # matches what each system shows. A system that is offline contributes 0 and
    # its outage is shown by the health tiles. Transformation-progress tiles have
    # no integrated source yet and stay as curated roadmap placeholders.
    from app.services.integration import fetch_overview
    overview = fetch_overview(systems)

    def _kpi(key, contains, default=0):
        # reads live OR last-known-good ("stale") values — anything with kpis
        for e in overview:
            if e.get("key") == key and e.get("kpis"):
                for k in e.get("kpis", []):
                    if contains in str(k.get("label", "")).lower():
                        v = k.get("value")
                        if isinstance(v, (int, float)):
                            return v
        return default

    itsm_total = _kpi("itsm", "total tickets")
    itsm_breach = _kpi("itsm", "breached")
    sla_health = round(100 * (itsm_total - itsm_breach) / itsm_total) if itsm_total else 100

    kpis = {
        # --- live operational (from the integrated systems) ---
        "open_tickets": _kpi("itsm", "open ticket"),
        "critical_tickets": itsm_breach,
        "sla_health": sla_health,
        "assets_total": _kpi("assets", "total asset"),
        "assets_maintenance": _kpi("assets", "maintenance"),   # 0 until assets emits it
        "low_stock": _kpi("assets", "low stock"),
        "servers_online": online,
        "servers_total": total_int,
        "cpu_alerts": _kpi("monitoring", "alert"),
        "active_projects": _kpi("commandtrack", "open task"),
        "overdue_tasks": _kpi("commandtrack", "overdue"),
        "pending_approvals": _kpi("commandtrack", "pending approval"),
        # --- digital-transformation roadmap KPIs (no integrated source yet) ---
        "finance_progress": 62,
        "automation_progress": 48,
        "ai_progress": 35,
        "production_readiness": 40,
        "cost_saving": "₺ 2.6M",
    }
    _, unread = _unread_notifications((current_user() or {}).get("username"))
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
    scope = system_scope(current_user())
    systems = [s for s in _systems() if scope is None or s["key"] in scope]
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
    # Scope-locked users can only open their own system(s).
    scope = system_scope(current_user())
    if scope is not None and key not in scope:
        abort(403)
    # Production Visibility, BI and Probation are real working modules with their
    # own blueprints — open them directly (fully online, no external host needed).
    if key == "production":
        return redirect(url_for("production.index"))
    if key == "bi":
        return redirect(url_for("bi.index"))
    if key == "probation":
        return redirect(url_for("probation.dashboard"))

    row = _system_by_key(key)
    if not row or not row["enabled"]:
        abort(404)

    # Integrated apps -> embedded viewer (the platform concept) by default.
    # ?view=details shows the technical/launch info page instead.
    if row["is_integrated"]:
        status = health_svc.check_system(row)
        # Single Sign-On: when enabled, the iframe/open links go through the
        # platform's launch route (which mints a token and hands off), so the
        # user is signed straight into the system. Falls back to the raw URL.
        from app.routes.sso import sso_target_url
        embed_url = sso_target_url(key, row["base_url"])
        if request.args.get("view") == "details":
            return render_template("modules/integrated.html", system=row,
                                   status=status, active=key, embed_url=embed_url)
        return render_template("modules/embedded.html", system=row, status=status,
                               active=key, embed_url=embed_url)

    # Future-ready modules -> explanation + structured placeholder tables
    table = MODULE_TABLES.get(key)
    about = sc.MODULE_ABOUT.get(key)
    return render_template("modules/generic.html", system=row, table=table,
                           about=about, active=key)


# --------------------------------------------------------------------------
# Reports & Exports Center
# --------------------------------------------------------------------------
@bp.route("/reports")
@permission_required("view_reports")
def reports():
    """Catalogue of live reports the current user may view, grouped by module."""
    groups = reports_svc.catalog(user_can)
    return render_template("reports.html", groups=groups, active="reports")


@bp.route("/reports/<key>")
@permission_required("view_reports")
def report_view(key):
    """A single report: filters + on-screen preview (first 300 rows) + exports."""
    spec = reports_svc.get(key)
    if not spec or not user_can(spec["perm"]):
        abort(404)
    result = reports_svc.run(key, request.args, limit=300)
    return render_template("report_view.html", spec=spec, rows=result["rows"],
                           count=result["count"], args=request.args, active="reports",
                           can_export=user_can("export_reports"))


@bp.route("/reports/<key>.<fmt>")
@permission_required("export_reports")
def report_export(key, fmt):
    """Export a report honouring the current filters as CSV / Excel / PDF."""
    spec = reports_svc.get(key)
    if not spec or not user_can(spec["perm"]) or fmt not in ("csv", "xlsx", "pdf"):
        abort(404)
    result = reports_svc.run(key, request.args, limit=20000)
    try:
        payload, mime, ext = reports_svc.export(spec, result["rows"], fmt)
    except Exception:  # noqa: BLE001 — e.g. reportlab/openpyxl missing
        abort(500)
    log_audit(current_user()["username"], "report_export",
              f"{key}.{fmt} ({result['count']} rows)", request.remote_addr or "")
    return send_file(io.BytesIO(payload), as_attachment=True,
                     download_name=f"tc_{key}.{ext}", mimetype=mime)


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
# Shared registry (Phase 5) — one directory of people + assets across systems,
# each row deep-links (via SSO) straight to its record in the owning system.
# --------------------------------------------------------------------------
@bp.route("/registry")
@permission_required("open_module")
def registry():
    if system_scope(current_user()) is not None:   # scope-locked roles can't see the cross-system registry
        abort(403)
    from app.services.registry import fetch_registry
    data = fetch_registry(_systems())
    return render_template("registry.html", registry=data, active="registry")


@bp.route("/api/registry/search")
@login_required
def api_registry_search():
    from app.services.registry import search_registry
    q = request.args.get("q", "")
    return jsonify(search_registry(_systems(), q))


# --------------------------------------------------------------------------
# Profile + self-service password change
# --------------------------------------------------------------------------
@bp.route("/profile")
@login_required
def profile():
    return render_template("profile.html", active="profile")


# --------------------------------------------------------------------------
# Digital signature — generate from a typed name (4 styles) and save one.
# The rendered PNG is stored on the user so it can be reused for sign-offs.
# --------------------------------------------------------------------------
_SIG_STYLES = {"great-vibes", "dancing-script", "sacramento", "satisfy"}


@bp.route("/profile/signature", methods=["POST"])
@login_required
def save_signature():
    user = current_user()
    data = request.get_json(silent=True) or {}
    style = (data.get("style") or "").strip().lower()[:40]
    name = (data.get("name") or "").strip()[:120]
    png = data.get("png") or ""
    if style and style not in _SIG_STYLES:
        return jsonify(error="unknown style"), 400
    if png and not png.startswith("data:image/png;base64,"):
        return jsonify(error="invalid image"), 400
    if len(png) > 400_000:                      # ~300 KB rendered PNG ceiling
        return jsonify(error="signature image too large"), 413
    if not (style and name and png):
        return jsonify(error="name, style and image are all required"), 400
    conn = get_db()
    try:
        conn.execute("UPDATE users SET sig_style=?, sig_name=?, sig_png=?, sig_updated_at=? WHERE id=?",
                     (style, name, png, utcnow(), user["id"]))
        conn.commit()
    finally:
        conn.close()
    log_audit(user["username"], "signature_save", f"style={style}", request.remote_addr or "")
    return jsonify(ok=True)


@bp.route("/profile/signature/quick", methods=["POST"])
@login_required
def quick_signature():
    """One-click signature: render the current user's name server-side (Great
    Vibes) and save it. The simplest way to get a usable signature on all papers."""
    user = current_user()
    from app.services.signature import generate_png, DEFAULT_STYLE
    name = (user.get("full_name") or user.get("username") or "").strip()
    png = generate_png(name)
    if not png:
        return jsonify(error="Could not generate a signature."), 500
    conn = get_db()
    try:
        conn.execute("UPDATE users SET sig_style=?, sig_name=?, sig_png=?, sig_updated_at=? WHERE id=?",
                     (DEFAULT_STYLE, name, png, utcnow(), user["id"]))
        conn.commit()
    finally:
        conn.close()
    log_audit(user["username"], "signature_quick", "", request.remote_addr or "")
    return jsonify(ok=True, png=png, name=name)


@bp.route("/profile/notifications", methods=["POST"])
@login_required
def save_notif_prefs():
    """Save the current user's notification preferences (currently: email on/off)."""
    import json as _json
    user = current_user()
    data = request.get_json(silent=True) or {}
    prefs = {"email": bool(data.get("email", True))}
    conn = get_db()
    try:
        conn.execute("UPDATE users SET notif_prefs=? WHERE id=?",
                     (_json.dumps(prefs), user["id"]))
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, prefs=prefs)


@bp.route("/profile/signature/clear", methods=["POST"])
@login_required
def clear_signature():
    user = current_user()
    conn = get_db()
    try:
        conn.execute("UPDATE users SET sig_style=NULL, sig_name=NULL, sig_png=NULL, sig_updated_at=NULL WHERE id=?",
                     (user["id"],))
        conn.commit()
    finally:
        conn.close()
    log_audit(user["username"], "signature_clear", "", request.remote_addr or "")
    return jsonify(ok=True)


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
        me = (current_user() or {}).get("username")
        if nid:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE id = ?", (nid,))
        else:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE is_read = 0 "
                         "AND (target_user IS NULL OR target_user = ?)", (me,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"ok": True})


@bp.route("/notifications/feed")
@login_required
def notifications_feed():
    """Lightweight JSON feed the top bar polls so brand-new notifications can
    chime, pop up and update the bell live without a page reload. Also pulls the
    four systems' own notifications into the feed (throttled), so any alert from
    ITSM / Assets / Monitoring / CommandTrack shows up here too."""
    try:
        sync_system_notifications(_systems())
    except Exception:
        pass
    try:
        from app.services.alerts import dispatch_pending_alerts
        dispatch_pending_alerts()   # email/webhook any new critical alerts (once)
    except Exception:
        pass
    try:
        from app.services.auto_ticket import auto_create_tickets
        auto_create_tickets(_systems())   # open ITSM tickets for new critical alerts (if enabled)
    except Exception:
        pass
    notifs, unread = _unread_notifications((current_user() or {}).get("username"))
    items = [{
        "id": n["id"], "severity": n["severity"], "module": n["module"],
        "title": n["title"], "message": n["message"],
        "created_at": n["created_at"], "is_read": n["is_read"],
        "link": (n["link"] if "link" in n.keys() else None),
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
        if user_has_permission(user, "proc_view"):
            for pr in conn.execute(
                    "SELECT id,pr_no,title,status FROM pr_requests WHERE is_active=1 "
                    "ORDER BY id DESC LIMIT 300"):
                if fuzzy(f"{pr['pr_no']} {pr['title']}"):
                    results.append({"type": "pr", "name": f"{pr['pr_no']} · {pr['title'] or ''}",
                                    "sub": "Purchase request",
                                    "url": url_for("approvals.detail", pr_id=pr["id"])})
        if has_permission(user["role"], "open_module"):
            for p in conn.execute("SELECT name,area FROM production_lines"):
                if fuzzy(f"{p['name']} {p['area']}"):
                    results.append({"type": "production", "name": p["name"], "sub": "Production line",
                                    "url": url_for("production.index")})

        # --- records from the newer modules -------------------------------
        # Each block is guarded: a deployment whose migrations have not created a
        # module's tables yet must never break the platform search box.
        def _scan(sql, hay, make):
            try:
                rows = conn.execute(sql).fetchall()
            except Exception:
                return
            for r in rows:
                try:
                    if fuzzy(hay(r)):
                        results.append(make(r))
                except Exception:
                    continue

        if user_has_permission(user, "view_dashboard"):
            _scan("SELECT id,order_no,buyer,style_name,style_ref FROM ord_orders "
                  "ORDER BY id DESC LIMIT 300",
                  lambda r: f"{r['order_no']} {r['buyer']} {r['style_name'] or ''} {r['style_ref'] or ''}",
                  lambda r: {"type": "order",
                             "name": f"{r['order_no']} · {r['buyer'] or ''}",
                             "sub": r["style_name"] or "Order",
                             "url": url_for("orders.detail", order_id=r["id"])})
        if user_has_permission(user, "cmp_view"):
            _scan("SELECT id,ref,scheme,site FROM cmp_audits ORDER BY id DESC LIMIT 200",
                  lambda r: f"{r['ref']} {r['scheme']} {r['site'] or ''}",
                  lambda r: {"type": "audit",
                             "name": f"{r['ref']} · {r['scheme']}",
                             "sub": "Compliance audit",
                             "url": url_for("compliance.audit_detail", audit_id=r["id"])})
    finally:
        conn.close()
    return jsonify({"q": q, "results": results[:25]})


# ---------------------------------------------------------------------------
# My Work & Action Centre — one page with everything waiting on THIS user:
# approvals to sign, maintenance part-requests to decide, probation evaluations
# due, own PRs and maintenance tickets, and the latest unread alerts. Every
# section is permission-gated and defensively wrapped so one module's failure
# never blanks the page.
# ---------------------------------------------------------------------------
@bp.route("/my-work")
@login_required
def my_work():
    user = current_user()
    data = {"sign_queue": [], "maint_approvals": [], "prob_pending": [],
            "my_prs": [], "my_tickets": [], "alerts": [],
            "cmp_caps": [], "cmp_expiring": [], "late_milestones": []}

    if user_has_permission(user, "proc_view"):
        try:
            from app.approvals import services as proc
            data["sign_queue"] = proc.my_queue(user)[:15]
            data["my_prs"] = [p for p in proc.list_prs(requester=user["username"], limit=10)
                              if p.get("status") not in ("closed", "cancelled")][:8]
        except Exception:
            pass

    if user_has_permission(user, "maint_approve"):
        try:
            conn = get_db()
            try:
                data["maint_approvals"] = conn.execute(
                    "SELECT a.id, a.level, a.approver_role, a.created_at, r.request_no, "
                    "r.id AS request_id FROM mnt_approvals a "
                    "JOIN mnt_requests r ON r.id = a.request_id "
                    "WHERE a.status='pending' ORDER BY a.id DESC LIMIT 10").fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    if user_has_permission(user, "maint_view"):
        try:
            conn = get_db()
            try:
                data["my_tickets"] = conn.execute(
                    "SELECT id, ticket_no, machine_code, status, priority, created_at "
                    "FROM mnt_tickets WHERE requester=? AND is_active=1 "
                    "AND status NOT IN ('closed','cancelled','rejected') "
                    "ORDER BY id DESC LIMIT 8", (user["username"],)).fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    if user_has_permission(user, "prob_view"):
        try:
            from app.probation import services as prob
            data["prob_pending"] = prob.my_pending(user)[:10]
        except Exception:
            pass

    # Compliance: corrective actions that are open past their due date, and
    # certificates about to lapse — both are "act now or a shipment is at risk".
    if user_has_permission(user, "cmp_view"):
        try:
            conn = get_db()
            try:
                today = _date.today().isoformat()
                soon = (_date.today() + _timedelta(days=30)).isoformat()
                data["cmp_caps"] = conn.execute(
                    "SELECT f.id, f.finding, f.severity, f.due_date, f.audit_id, a.ref, a.scheme "
                    "FROM cmp_findings f JOIN cmp_audits a ON a.id = f.audit_id "
                    "WHERE f.status IN ('open','in_progress') AND f.due_date IS NOT NULL "
                    "AND f.due_date <= ? ORDER BY f.due_date ASC LIMIT 8", (soon,)).fetchall()
                data["cmp_expiring"] = conn.execute(
                    "SELECT id, name, expiry_date FROM cmp_certs "
                    "WHERE expiry_date IS NOT NULL AND expiry_date <= ? AND status != 'revoked' "
                    "ORDER BY expiry_date ASC LIMIT 6", (soon,)).fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    # Orders: Time & Action milestones whose planned date has passed with no
    # actual — the earliest signal that a ship date is about to slip.
    if user_has_permission(user, "view_dashboard"):
        try:
            conn = get_db()
            try:
                data["late_milestones"] = conn.execute(
                    "SELECT m.id, m.name, m.planned_date, o.id AS order_id, o.order_no, o.buyer "
                    "FROM ord_milestones m JOIN ord_orders o ON o.id = m.order_id "
                    "WHERE m.actual_date IS NULL AND m.planned_date IS NOT NULL "
                    "AND m.planned_date < ? "
                    "AND o.status NOT IN ('shipped','closed','cancelled') "
                    "ORDER BY m.planned_date ASC LIMIT 8",
                    (_date.today().isoformat(),)).fetchall()
            finally:
                conn.close()
        except Exception:
            pass

    try:
        notifs, _unread = _unread_notifications(user["username"])
        scope = system_scope(user)
        if scope is not None:
            notifs = [n for n in notifs if (n["module"] or "") in scope or not n["module"]]
        data["alerts"] = [n for n in notifs if not n["is_read"]][:8]
    except Exception:
        pass

    total_actions = (len(data["sign_queue"]) + len(data["maint_approvals"])
                     + len(data["prob_pending"]) + len(data["late_milestones"])
                     + len(data["cmp_caps"]) + len(data["cmp_expiring"]))
    return render_template("my_work.html", user=user, total_actions=total_actions, **data)
