"""
TC Platform — Production Visibility (a real working module).

Full CRUD for production lines, downtime events and quality issues, persisted
to the platform database, with a live KPI dashboard. Viewing requires
`open_module`; creating/editing/deleting requires `manage_production`.
"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, abort)

from app.db import get_db, log_audit, utcnow
from app.auth import permission_required, current_user
from app.security import has_permission
from app.services import seed_content as sc

bp = Blueprint("production", __name__, url_prefix="/production")

STATUSES = ("running", "idle", "maintenance", "down")
DT_CATEGORIES = ("mechanical", "electrical", "material", "changeover", "quality", "other")
SEVERITIES = ("low", "medium", "high")
Q_STATUSES = ("open", "investigating", "resolved")


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _efficiency(target, actual):
    if target and target > 0:
        return round(actual / target * 100)
    return None


def _audit(action, detail):
    log_audit(current_user()["username"], action, detail, request.remote_addr or "")


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@bp.route("/")
@permission_required("open_module")
def index():
    conn = get_db()
    try:
        lines = conn.execute("SELECT * FROM production_lines ORDER BY area, name").fetchall()
        downtime = conn.execute(
            """SELECT d.*, l.name AS line_name FROM production_downtime d
               LEFT JOIN production_lines l ON l.id = d.line_id
               ORDER BY d.id DESC LIMIT 50""").fetchall()
        quality = conn.execute(
            """SELECT q.*, l.name AS line_name FROM production_quality q
               LEFT JOIN production_lines l ON l.id = q.line_id
               ORDER BY q.id DESC LIMIT 50""").fetchall()
    finally:
        conn.close()

    effs = [_efficiency(l["target_output"], l["actual_output"]) for l in lines]
    effs = [e for e in effs if e is not None]
    kpis = {
        "lines_total": len(lines),
        "lines_running": sum(1 for l in lines if l["status"] == "running"),
        "lines_down": sum(1 for l in lines if l["status"] in ("down", "maintenance")),
        "avg_efficiency": round(sum(effs) / len(effs)) if effs else 0,
        "downtime_minutes": sum(d["minutes"] or 0 for d in downtime),
        "quality_open": sum(1 for q in quality if q["status"] != "resolved"),
        "total_output": sum(l["actual_output"] or 0 for l in lines),
        "total_target": sum(l["target_output"] or 0 for l in lines),
    }
    # attach efficiency to each line for the table
    line_rows = []
    for l in lines:
        d = dict(l)
        d["efficiency"] = _efficiency(l["target_output"], l["actual_output"])
        line_rows.append(d)

    about = sc.MODULE_ABOUT.get("production")
    user = current_user()
    can_edit = bool(user) and has_permission(user["role"], "manage_production")
    return render_template("production.html", lines=line_rows, downtime=downtime,
                           quality=quality, kpis=kpis, about=about,
                           statuses=STATUSES, dt_categories=DT_CATEGORIES,
                           severities=SEVERITIES, q_statuses=Q_STATUSES,
                           can_edit=can_edit, active="production")


# --------------------------------------------------------------------------
# Lines CRUD
# --------------------------------------------------------------------------
@bp.route("/lines/add", methods=["POST"])
@permission_required("manage_production")
def add_line():
    f = request.form
    name = (f.get("name") or "").strip()
    if not name:
        flash("prod_name_required", "error")
        return redirect(url_for("production.index") + "#lines")
    status = f.get("status") if f.get("status") in STATUSES else "running"
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO production_lines
               (name, area, status, shift, target_output, actual_output, operators, notes, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (name, (f.get("area") or "").strip(), status, (f.get("shift") or "A").strip(),
             _int(f.get("target_output")), _int(f.get("actual_output")),
             _int(f.get("operators")), (f.get("notes") or "").strip(), utcnow()))
        conn.commit()
        _audit("production_line_add", f"Added line '{name}'")
        flash("prod_saved", "success")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#lines")


@bp.route("/lines/<int:lid>/edit", methods=["POST"])
@permission_required("manage_production")
def edit_line(lid):
    f = request.form
    name = (f.get("name") or "").strip()
    status = f.get("status") if f.get("status") in STATUSES else "running"
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM production_lines WHERE id=?", (lid,)).fetchone()
        if not row:
            abort(404)
        conn.execute(
            """UPDATE production_lines SET name=?, area=?, status=?, shift=?,
               target_output=?, actual_output=?, operators=?, notes=?, updated_at=?
               WHERE id=?""",
            (name, (f.get("area") or "").strip(), status, (f.get("shift") or "A").strip(),
             _int(f.get("target_output")), _int(f.get("actual_output")),
             _int(f.get("operators")), (f.get("notes") or "").strip(), utcnow(), lid))
        conn.commit()
        _audit("production_line_edit", f"Edited line #{lid} '{name}'")
        flash("prod_saved", "success")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#lines")


@bp.route("/lines/<int:lid>/delete", methods=["POST"])
@permission_required("manage_production")
def delete_line(lid):
    conn = get_db()
    try:
        conn.execute("DELETE FROM production_lines WHERE id=?", (lid,))
        conn.commit()
        _audit("production_line_delete", f"Deleted line #{lid}")
        flash("prod_deleted", "success")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#lines")


# --------------------------------------------------------------------------
# Downtime
# --------------------------------------------------------------------------
@bp.route("/downtime/add", methods=["POST"])
@permission_required("manage_production")
def add_downtime():
    f = request.form
    cat = f.get("category") if f.get("category") in DT_CATEGORIES else "other"
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO production_downtime (line_id, reason, category, minutes, occurred_at, created_at)
               VALUES (?,?,?,?,?,?)""",
            (_int(f.get("line_id")) or None, (f.get("reason") or "").strip(), cat,
             _int(f.get("minutes")), utcnow(), utcnow()))
        conn.commit()
        _audit("production_downtime_add", f"Logged downtime ({cat})")
        flash("prod_saved", "success")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#downtime")


@bp.route("/downtime/<int:did>/delete", methods=["POST"])
@permission_required("manage_production")
def delete_downtime(did):
    conn = get_db()
    try:
        conn.execute("DELETE FROM production_downtime WHERE id=?", (did,))
        conn.commit()
        _audit("production_downtime_delete", f"Deleted downtime #{did}")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#downtime")


# --------------------------------------------------------------------------
# Quality
# --------------------------------------------------------------------------
@bp.route("/quality/add", methods=["POST"])
@permission_required("manage_production")
def add_quality():
    f = request.form
    sev = f.get("severity") if f.get("severity") in SEVERITIES else "low"
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO production_quality (line_id, issue, severity, quantity, status, created_at)
               VALUES (?,?,?,?,?,?)""",
            (_int(f.get("line_id")) or None, (f.get("issue") or "").strip(), sev,
             _int(f.get("quantity")), "open", utcnow()))
        conn.commit()
        _audit("production_quality_add", "Logged quality issue")
        flash("prod_saved", "success")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#quality")


@bp.route("/quality/<int:qid>/status", methods=["POST"])
@permission_required("manage_production")
def quality_status(qid):
    new = request.form.get("status")
    if new not in Q_STATUSES:
        abort(400)
    conn = get_db()
    try:
        conn.execute("UPDATE production_quality SET status=? WHERE id=?", (new, qid))
        conn.commit()
        _audit("production_quality_status", f"Quality #{qid} -> {new}")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#quality")


@bp.route("/quality/<int:qid>/delete", methods=["POST"])
@permission_required("manage_production")
def delete_quality(qid):
    conn = get_db()
    try:
        conn.execute("DELETE FROM production_quality WHERE id=?", (qid,))
        conn.commit()
        _audit("production_quality_delete", f"Deleted quality #{qid}")
    finally:
        conn.close()
    return redirect(url_for("production.index") + "#quality")
