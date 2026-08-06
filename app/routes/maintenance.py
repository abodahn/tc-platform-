"""
TC Platform — Maintenance & Spare Parts (CMMS) blueprint.

Thin controllers over app.maintenance.services. Every page requires auth and
the right maintenance permission; mutating actions enforce finer-grained perms.
"""
import csv
import io

from flask import (Blueprint, render_template, request, redirect, url_for,
                   abort, flash, Response, jsonify, send_file)

from config import Config
from app.db import get_db
from app.auth import login_required, current_user
from app.security import has_permission
from app.maintenance import services as svc
from app.maintenance import constants as C
from app.maintenance import ai as ai_engine
from app.maintenance import workflow as wf

bp = Blueprint("maintenance", __name__, url_prefix="/maintenance")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _u():
    return current_user()


def _require(perm):
    if not has_permission(_u()["role"], perm):
        abort(403)


def _can(perm):
    return has_permission(_u()["role"], perm)


def _db():
    return get_db()


def _one(sql, args=()):
    conn = _db()
    try:
        row = conn.execute(sql, args).fetchone()
    finally:
        conn.close()
    return row


def _all(sql, args=()):
    conn = _db()
    try:
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return rows


# Status -> badge class (reuses platform badge styles)
_BADGE = {
    "submitted": "b-open", "under_review": "b-in_progress", "assigned": "b-info",
    "diagnosis": "b-in_progress", "spare_required": "b-warning", "waiting_stock": "b-warning",
    "waiting_approval": "b-warning", "approved_issue": "b-info", "rejected": "b-critical",
    "parts_issued": "b-info", "repair": "b-in_progress", "testing": "b-info",
    "resolved": "b-live", "closed": "b-done", "cancelled": "b-unknown", "reopened": "b-warning",
    "draft": "b-unknown",
    # request / approval
    "in_stock": "b-live", "out_of_stock": "b-critical", "approved": "b-live",
    "issued": "b-info", "received": "b-done", "pending": "b-warning", "returned": "b-warning",
    # machine
    "running": "b-running", "stopped": "b-down", "under_maintenance": "b-maintenance",
    "waiting_spare": "b-warning", "under_testing": "b-info", "decommissioned": "b-unknown",
    # priority
    "critical": "b-critical", "high": "b-warning", "medium": "b-info", "low": "b-unknown",
    # pm
    "scheduled": "b-info", "due_soon": "b-warning", "overdue": "b-critical",
    "in_progress": "b-in_progress", "completed": "b-done", "missed": "b-critical",
}


from markupsafe import Markup, escape  # noqa: E402


def _humanize(value):
    return str(value).replace("_", " ").title()


@bp.app_template_filter("mtext")
def mtext(value):
    """Plain humanized text (used for <option> fallback text)."""
    if value is None:
        return "—"
    return _humanize(value)


@bp.app_template_filter("mhuman")
def mhuman(value):
    """Translatable enum label: a span the i18n engine localizes via mx.<value>.
    Unknown values fall back to humanized text in any language (see app.js)."""
    if value is None:
        return "—"
    v = escape(str(value))
    return Markup(f'<span data-i18n="mx.{v}">{_humanize(value)}</span>')


@bp.app_template_filter("mbadge")
def mbadge(value):
    return _BADGE.get(value, "b-unknown")


@bp.app_context_processor
def _inject():
    return {"mbadge": lambda v: _BADGE.get(v, "b-unknown"), "C": C, "mcan": _can}


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
def dashboard():
    _require("maint_view")
    conn = _db()
    try:
        svc.sync_stock_alerts(conn)  # refresh low/out-of-stock alerts on the bell
        svc.sync_sla_breaches(conn)  # flag tickets past their resolution SLA

        def c(sql, a=()):
            return conn.execute(sql, a).fetchone()["c"]
        kpis = {
            "open_tickets": c("SELECT COUNT(*) c FROM mnt_tickets WHERE status NOT IN ('closed','cancelled','rejected')"),
            "critical": c("SELECT COUNT(*) c FROM mnt_tickets WHERE priority='critical' AND status NOT IN ('closed','cancelled')"),
            "machines_stopped": c("SELECT COUNT(*) c FROM mnt_machines WHERE status='stopped'"),
            "under_maint": c("SELECT COUNT(*) c FROM mnt_machines WHERE status='under_maintenance'"),
            "waiting_spare": c("SELECT COUNT(*) c FROM mnt_tickets WHERE status IN ('waiting_stock','spare_required','waiting_approval')"),
            "pending_appr": c("SELECT COUNT(*) c FROM mnt_approvals WHERE status='pending'"),
            "low_stock": c("SELECT COUNT(*) c FROM mnt_spare_parts WHERE stock_qty <= reorder_level AND stock_qty > 0"),
            "out_stock": c("SELECT COUNT(*) c FROM mnt_spare_parts WHERE stock_qty <= 0"),
            "pm_overdue": c("SELECT COUNT(*) c FROM mnt_pm_plans WHERE active=1 AND next_due < date('now')"),
            "downtime_month": conn.execute("SELECT COALESCE(SUM(total_downtime_min),0) c FROM mnt_tickets WHERE status='closed'").fetchone()["c"],
            "cost_month": conn.execute("SELECT COALESCE(SUM(cost),0) c FROM mnt_tickets").fetchone()["c"],
        }
        # MTTR (avg downtime of closed) & breakdown count
        mttr = conn.execute("SELECT AVG(total_downtime_min) a FROM mnt_tickets WHERE status='closed' AND total_downtime_min>0").fetchone()["a"]
        kpis["mttr"] = round(mttr) if mttr else 0
        kpis["sla"] = 92  # computed placeholder until enough history

        by_status = conn.execute("SELECT status, COUNT(*) c FROM mnt_tickets GROUP BY status ORDER BY c DESC").fetchall()
        by_priority = conn.execute("SELECT priority, COUNT(*) c FROM mnt_tickets GROUP BY priority").fetchall()
        downtime_machine = conn.execute(
            "SELECT machine_code, SUM(total_downtime_min) d FROM mnt_tickets WHERE total_downtime_min>0 "
            "GROUP BY machine_code ORDER BY d DESC LIMIT 6").fetchall()
        machines_raw = conn.execute("SELECT * FROM mnt_machines ORDER BY status, code").fetchall()
        machines = []
        for mm in machines_raw:
            sc_, band = svc.machine_health(conn, mm)
            d = dict(mm); d["health"] = sc_; d["health_band"] = band
            machines.append(d)
        machines.sort(key=lambda x: x["health"])  # worst first
        recent = conn.execute(
            "SELECT * FROM mnt_tickets WHERE is_active=1 ORDER BY id DESC LIMIT 8").fetchall()
        low = conn.execute("SELECT * FROM mnt_spare_parts WHERE stock_qty <= reorder_level ORDER BY stock_qty LIMIT 6").fetchall()
        kpis["ai_at_risk"] = sum(1 for r in ai_engine.risk_ranking(conn) if r["band"] == "high")
    finally:
        conn.close()
    return render_template("maintenance/dashboard.html", kpis=kpis, by_status=by_status,
                           by_priority=by_priority, downtime_machine=downtime_machine,
                           machines=machines, recent=recent, low=low, active="maint_dashboard")


# --------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------
@bp.route("/ai/triage")
@login_required
def ai_triage():
    """Offline AI triage: suggest priority/severity/tags from the description.
    GET (read-only, no state change) so it needs no CSRF token."""
    _require("maint_ticket_create")
    desc = request.args.get("desc", "")
    crit = "medium"
    mid = request.args.get("machine_id")
    if mid:
        m = _one("SELECT criticality FROM mnt_machines WHERE id=?", (mid,))
        if m:
            crit = m["criticality"]
    result = ai_engine.triage(desc, crit, request.args.get("stopped") == "1",
                              request.args.get("safety") == "1")
    return jsonify(result)


@bp.route("/ai")
@login_required
def ai_insights():
    """Offline AI / predictive maintenance — runs fully on-premise."""
    _require("maint_view")
    conn = _db()
    try:
        risks = ai_engine.risk_ranking(conn)
        reorder = ai_engine.reorder_recommendations(conn)
        repeated = ai_engine.repeated_failures(conn)
        anomalies = ai_engine.downtime_anomalies(conn)
        pm_opt = ai_engine.pm_optimizer(conn)
        sla = [t for t in ai_engine.open_tickets_sla(conn) if t["risk"] >= 50]
    finally:
        conn.close()
    high = [r for r in risks if r["band"] == "high"]
    return render_template("maintenance/ai.html", risks=risks, reorder=reorder,
                           repeated=repeated, anomalies=anomalies, pm_opt=pm_opt, sla=sla,
                           high=high, active="maint_ai")


@bp.route("/floor")
@login_required
def floor():
    """Phone-first 'My Work' screen for technicians/operators on the factory floor."""
    _require("maint_view")
    u = _u()
    name = u.get("full_name") or u.get("username")
    conn = _db()
    try:
        mine = conn.execute(
            "SELECT * FROM mnt_tickets WHERE is_active=1 AND assigned_to=? "
            "AND status NOT IN ('closed','cancelled','rejected') ORDER BY "
            "CASE priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, id DESC",
            (name,)).fetchall()
        open_tickets = conn.execute(
            "SELECT * FROM mnt_tickets WHERE is_active=1 AND status NOT IN ('closed','cancelled','rejected') "
            "ORDER BY id DESC LIMIT 12").fetchall()
    finally:
        conn.close()
    return render_template("maintenance/floor.html", mine=mine, open_tickets=open_tickets,
                           active="maint_floor")


@bp.route("/easy")
@login_required
def easy_report():
    """Picture-only 'Easy Report' for low-literacy floor workers (no reading/typing).
    Posts to the normal ticket_new endpoint, so it creates a standard ticket."""
    _require("maint_ticket_create")
    machines = _all("SELECT id,code,name,type,area FROM mnt_machines WHERE is_active=1 ORDER BY code")
    return render_template("maintenance/easy_report.html", machines=machines, active="maint_floor")


@bp.route("/scan")
@login_required
def scan():
    """QR scan -> open a pre-filled new ticket for the scanned machine.
    ?code=<machine code or qr token> resolves via manual entry / fallback."""
    _require("maint_view")
    code = (request.args.get("code") or "").strip()
    if code:
        m = _one("SELECT id FROM mnt_machines WHERE code=? OR qr_token=?", (code, code))
        if m:
            return redirect(url_for("maintenance.ticket_new", machine=m["id"]))
        flash("m_machine_not_found", "error")
    return render_template("maintenance/scan.html", active="maint_floor")


@bp.route("/tickets")
@login_required
def tickets():
    _require("maint_view")
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    q = "SELECT * FROM mnt_tickets WHERE is_active=1"
    args = []
    if status:
        q += " AND status=?"; args.append(status)
    if priority:
        q += " AND priority=?"; args.append(priority)
    q += " ORDER BY id DESC"
    rows = _all(q, args)
    return render_template("maintenance/tickets.html", tickets=rows, status=status,
                           priority=priority, active="maint_tickets")


@bp.route("/tickets/new", methods=["GET", "POST"])
@login_required
def ticket_new():
    _require("maint_ticket_create")
    if request.method == "POST":
        f = request.form
        if not (f.get("description") or "").strip():
            flash("m_desc_required", "error")
        else:
            machine = None
            if f.get("machine_id"):
                machine = _one("SELECT code,department,area,line_no FROM mnt_machines WHERE id=?", (f.get("machine_id"),))
            data = {
                "requester": f.get("requester"), "department": f.get("department") or (machine["department"] if machine else None),
                "area": f.get("area") or (machine["area"] if machine else None),
                "line_no": f.get("line_no") or (machine["line_no"] if machine else None),
                "machine_id": f.get("machine_id") or None,
                "machine_code": (machine["code"] if machine else f.get("machine_code")),
                "issue_category": f.get("issue_category"), "description": f.get("description"),
                "priority": f.get("priority", "medium"), "severity": f.get("severity", "moderate"),
                "safety_impact": f.get("safety_impact") == "on",
                "production_stopped": f.get("production_stopped") == "on",
                "est_downtime_min": f.get("est_downtime_min"), "shift": f.get("shift"),
                "remarks": f.get("remarks"),
            }
            data["allow_duplicate"] = f.get("allow_duplicate") == "on"
            tid, err = svc.create_ticket(data, _u(), request.remote_addr)
            if err and err.startswith("duplicate_open:"):
                flash("This machine already has an open ticket (%s). Tick "
                      "'create anyway' if this is a separate fault." % err.split(":", 1)[1], "error")
            elif err:
                flash("m_desc_required", "error")
            else:
                svc.save_attachments(request.files.getlist("photos"), "ticket", tid, "issue", _u())
                flash("m_ticket_created", "success")
                return redirect(url_for("maintenance.ticket_detail", tid=tid))
    machines = _all("SELECT id,code,name,department,area,line_no FROM mnt_machines WHERE is_active=1 ORDER BY code")
    prefill = request.args.get("machine", "")
    return render_template("maintenance/ticket_new.html", machines=machines, prefill=prefill,
                           active="maint_new")


@bp.route("/tickets/<int:tid>")
@login_required
def ticket_detail(tid):
    _require("maint_view")
    t = _one("SELECT * FROM mnt_tickets WHERE id=?", (tid,))
    if not t:
        abort(404)
    conn = _db()
    try:
        diag = conn.execute("SELECT * FROM mnt_diagnosis WHERE ticket_id=? ORDER BY id DESC", (tid,)).fetchall()
        comments = conn.execute("SELECT * FROM mnt_comments WHERE ticket_id=? ORDER BY id", (tid,)).fetchall()
        audit = conn.execute("SELECT * FROM mnt_audit WHERE entity_type='ticket' AND entity_id=? ORDER BY id", (tid,)).fetchall()
        reqs = conn.execute("SELECT * FROM mnt_requests WHERE ticket_id=? ORDER BY id DESC", (tid,)).fetchall()
        req_items, approvals = {}, {}
        for r in reqs:
            req_items[r["id"]] = conn.execute("SELECT * FROM mnt_request_items WHERE request_id=?", (r["id"],)).fetchall()
            approvals[r["id"]] = conn.execute("SELECT * FROM mnt_approvals WHERE request_id=? ORDER BY level", (r["id"],)).fetchall()
        machine = conn.execute("SELECT * FROM mnt_machines WHERE id=?", (t["machine_id"],)).fetchone() if t["machine_id"] else None
        spares = conn.execute("SELECT id,code,name,stock_qty,uom FROM mnt_spare_parts WHERE is_active=1 ORDER BY code").fetchall()
        attachments = conn.execute(
            "SELECT * FROM mnt_attachments WHERE entity_type='ticket' AND entity_id=? ORDER BY id", (tid,)).fetchall()
        rc_suggest = ai_engine.suggest_root_cause(conn, t["machine_id"], t["issue_category"]) if t["machine_id"] else None
        tech_rec = ai_engine.recommend_technician(conn, t["issue_category"])
        mttr = ai_engine.estimate_repair_time(conn, t["machine_id"], t["issue_category"])
        sla_risk = ai_engine.ticket_sla_risk(conn, t)
        # cross-module mesh: purchase requests raised from THIS ticket
        try:
            proc_prs = conn.execute(
                "SELECT id, pr_no, status, total, currency, po_no FROM pr_requests "
                "WHERE source_module='maintenance' AND source_ref=? AND is_active=1 "
                "ORDER BY id DESC", (f"ticket:{tid}",)).fetchall()
        except Exception:
            proc_prs = []
    finally:
        conn.close()
    return render_template("maintenance/ticket_detail.html", t=t, diag=diag, comments=comments,
                           audit=audit, reqs=reqs, req_items=req_items, approvals=approvals,
                           machine=machine, spares=spares, attachments=attachments,
                           rc_suggest=rc_suggest, tech_rec=tech_rec, mttr=mttr, sla_risk=sla_risk,
                           proc_prs=proc_prs, active="maint_tickets")


@bp.route("/tickets/<int:tid>/assign", methods=["POST"])
@login_required
def ticket_assign(tid):
    _require("maint_manage")
    f = request.form
    svc.review_assign(tid, f.get("technician"), f.get("priority"),
                      f.get("response_due"), f.get("resolution_due"), _u(), request.remote_addr)
    flash("m_assigned", "success")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/reject", methods=["POST"])
@login_required
def ticket_reject(tid):
    _require("maint_manage")
    ok, msg = svc.reject_ticket(tid, request.form.get("reason"), _u(), request.remote_addr)
    flash("m_rejected" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/diagnosis", methods=["POST"])
@login_required
def ticket_diagnosis(tid):
    _require("maint_technician")
    f = request.form
    svc.add_diagnosis(tid, {
        "fault_found": f.get("fault_found"), "root_cause": f.get("root_cause"),
        "diagnosis": f.get("diagnosis"), "required_action": f.get("required_action"),
        "spare_needed": f.get("spare_needed") == "on", "temp_fix": f.get("temp_fix") == "on",
        "can_run_partial": f.get("can_run_partial") == "on", "safety_risk": f.get("safety_risk") == "on",
        "est_repair_min": f.get("est_repair_min"), "notes": f.get("notes"),
    }, _u(), request.remote_addr)
    flash("m_saved", "success")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/request-parts", methods=["POST"])
@login_required
def ticket_request_parts(tid):
    _require("maint_technician")
    # must have a diagnosis first
    if not _one("SELECT id FROM mnt_diagnosis WHERE ticket_id=?", (tid,)):
        flash("m_need_diagnosis", "error")
        return redirect(url_for("maintenance.ticket_detail", tid=tid))
    f = request.form
    try:
        spare_id = int(f.get("spare_id"))
        qty = float(f.get("qty") or 0)
    except (TypeError, ValueError):
        flash("m_invalid_qty", "error")
        return redirect(url_for("maintenance.ticket_detail", tid=tid))
    if qty <= 0:
        flash("m_invalid_qty", "error")
        return redirect(url_for("maintenance.ticket_detail", tid=tid))
    svc.create_request(tid, [{"spare_id": spare_id, "qty": qty, "notes": f.get("notes")}],
                       f.get("reason"), f.get("urgency"), _u(), request.remote_addr)
    flash("m_request_created", "success")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/repair", methods=["POST"])
@login_required
def ticket_repair(tid):
    _require("maint_technician")
    f = request.form
    svc.repair_proof(tid, {
        "action_performed": f.get("action_performed"),
        "old_part_returned": f.get("old_part_returned") == "on",
        "machine_running": f.get("machine_running"), "final_notes": f.get("final_notes"),
    }, _u(), request.remote_addr)
    svc.save_attachments(request.files.getlist("photos"), "ticket", tid, "repair", _u())
    flash("m_saved", "success")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/test", methods=["POST"])
@login_required
def ticket_test(tid):
    _require("maint_view")  # supervisor/manager confirm
    f = request.form
    svc.record_test(tid, {"test_result": f.get("test_result"), "safety_check": f.get("safety_check"),
                          "machine_running": f.get("machine_running")}, _u(), request.remote_addr)
    flash("m_saved", "success")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/close", methods=["POST"])
@login_required
def ticket_close(tid):
    _require("maint_manage")
    ok, msg = svc.close_ticket(tid, _u(), request.remote_addr)
    flash("m_closed" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/reopen", methods=["POST"])
@login_required
def ticket_reopen(tid):
    _require("maint_manage")
    svc.reopen_ticket(tid, _u(), request.remote_addr)
    flash("m_saved", "success")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/tickets/<int:tid>/attach", methods=["POST"])
@login_required
def ticket_attach(tid):
    _require("maint_view")
    kind = request.form.get("kind", "photo")
    n = svc.save_attachments(request.files.getlist("photos"), "ticket", tid, kind, _u())
    flash("m_photo_added" if n else "m_no_photo", "success" if n else "error")
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


@bp.route("/attachment/<int:aid>")
@login_required
def attachment(aid):
    _require("maint_view")
    a = _one("SELECT * FROM mnt_attachments WHERE id=?", (aid,))
    if not a:
        abort(404)
    path = Config.UPLOAD_DIR / "maintenance" / a["filename"]
    if not path.exists():
        abort(404)
    return send_file(str(path), mimetype=a["content_type"] or "application/octet-stream",
                     download_name=a["original_name"] or a["filename"])


@bp.route("/tickets/<int:tid>/comment", methods=["POST"])
@login_required
def ticket_comment(tid):
    _require("maint_view")
    body = (request.form.get("body") or "").strip()
    if body:
        svc.add_comment(tid, _u(), body)
    return redirect(url_for("maintenance.ticket_detail", tid=tid))


# --------------------------------------------------------------------------
# Machines
# --------------------------------------------------------------------------
MACHINE_LIST_CAP = 500


@bp.route("/machines", methods=["GET", "POST"])
@login_required
def machines():
    _require("maint_view")
    if request.method == "POST":
        _require("maint_admin")
        f = request.form
        code = (f.get("code") or "").strip()
        if not code:
            flash("m_code_required", "error")
        elif _one("SELECT id FROM mnt_machines WHERE code=?", (code,)):
            flash("m_code_exists", "error")
        else:
            conn = _db()
            try:
                conn.execute(
                    """INSERT INTO mnt_machines (code,name,type,brand,model,serial,department,
                       area,line_no,location,criticality,status,qr_token,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                    (code, f.get("name"), f.get("type"), f.get("brand"), f.get("model"),
                     f.get("serial"), f.get("department"), f.get("area"), f.get("line_no"),
                     f.get("location"), f.get("criticality", "medium"), f.get("status", "running"),
                     "MQR" + code.replace("-", "")))
                conn.commit()
                flash("m_saved", "success")
            finally:
                conn.close()
        return redirect(url_for("maintenance.machines"))
    # The register import loads ~5,100 machines. Rendering all of them was a 2.3 MB
    # page on every visit, and the two provenance columns it added (legacy_card_no,
    # in_register_2023) were unreachable — this is where they earn their keep:
    # `q` searches the card number as well as the serial, `fleet=current` is the
    # "show me only the current fleet" filter the owner's decision depends on.
    q = (request.args.get("q") or "").strip()
    fleet = request.args.get("fleet") or ""
    where, args = ["is_active=1"], []
    if fleet == "current":
        where.append("in_register_2023=1")
    if q:
        where.append("(LOWER(code) LIKE ? OR LOWER(name) LIKE ? OR LOWER(serial) LIKE ? "
                     "OR LOWER(legacy_card_no) LIKE ? OR LOWER(brand) LIKE ? "
                     "OR LOWER(model) LIKE ?)")
        args += ["%" + q.lower() + "%"] * 6
    w = " AND ".join(where)
    total = _one("SELECT COUNT(*) n FROM mnt_machines WHERE " + w, tuple(args))["n"]
    # ponytail: hard cap, no paging. Narrow with the search box; add pages the day
    # someone actually wants to walk 5,000 machines a screen at a time.
    rows = _all("SELECT * FROM mnt_machines WHERE " + w + " ORDER BY code LIMIT %d"
                % MACHINE_LIST_CAP, tuple(args))
    return render_template("maintenance/machines.html", machines=rows, total=total,
                           q=q, fleet=fleet, cap=MACHINE_LIST_CAP, active="maint_machines")


@bp.route("/machines/<int:mid>")
@login_required
def machine_profile(mid):
    _require("maint_view")
    m = _one("SELECT * FROM mnt_machines WHERE id=?", (mid,))
    if not m:
        abort(404)
    conn = _db()
    try:
        tickets_ = conn.execute("SELECT * FROM mnt_tickets WHERE machine_id=? ORDER BY id DESC", (mid,)).fetchall()
        pm = conn.execute("SELECT * FROM mnt_pm_plans WHERE machine_id=?", (mid,)).fetchall()
        spares = conn.execute(
            "SELECT sp.* FROM mnt_spare_parts sp JOIN mnt_spare_compat sc ON sc.spare_id=sp.id "
            "WHERE sc.machine_id=?", (mid,)).fetchall()
        moves = conn.execute("SELECT * FROM mnt_stock_movements WHERE machine_id=? ORDER BY id DESC LIMIT 20", (mid,)).fetchall()
        health, health_band = svc.machine_health(conn, m)
        attachments = conn.execute(
            "SELECT * FROM mnt_attachments WHERE entity_type='machine' AND entity_id=? ORDER BY id", (mid,)).fetchall()
    finally:
        conn.close()
    return render_template("maintenance/machine_profile.html", m=m, tickets=tickets_, pm=pm,
                           spares=spares, moves=moves, health=health, health_band=health_band,
                           attachments=attachments, active="maint_machines")


@bp.route("/machines/<int:mid>/attach", methods=["POST"])
@login_required
def machine_attach(mid):
    _require("maint_view")
    n = svc.save_attachments(request.files.getlist("photos"), "machine", mid, "doc", _u())
    flash("m_photo_added" if n else "m_no_photo", "success" if n else "error")
    return redirect(url_for("maintenance.machine_profile", mid=mid))


# --------------------------------------------------------------------------
# Spare parts
# --------------------------------------------------------------------------
@bp.route("/spares", methods=["GET", "POST"])
@login_required
def spares():
    _require("maint_view")
    if request.method == "POST":
        _require("maint_store")
        f = request.form
        code = (f.get("code") or "").strip()
        if not code:
            flash("m_code_required", "error")
        elif _one("SELECT id FROM mnt_spare_parts WHERE code=?", (code,)):
            flash("m_code_exists", "error")
        else:
            conn = _db()
            try:
                conn.execute(
                    """INSERT INTO mnt_spare_parts (code,name,category,uom,stock_qty,min_level,
                       reorder_level,max_level,avg_cost,criticality,warehouse,bin,qr_token,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
                    (code, f.get("name"), f.get("category", "other"), f.get("uom", "pcs"),
                     float(f.get("stock_qty") or 0), float(f.get("min_level") or 0),
                     float(f.get("reorder_level") or 0), float(f.get("max_level") or 0),
                     float(f.get("avg_cost") or 0), f.get("criticality", "medium"),
                     f.get("warehouse"), f.get("bin"), "PQR" + code.replace("-", "")))
                conn.commit()
                flash("m_saved", "success")
            finally:
                conn.close()
        return redirect(url_for("maintenance.spares"))
    rows = _all("SELECT * FROM mnt_spare_parts WHERE is_active=1 ORDER BY code")
    return render_template("maintenance/spares.html", spares=rows, active="maint_spares")


@bp.route("/spares/<int:sid>")
@login_required
def spare_profile(sid):
    _require("maint_view")
    sp = _one("SELECT * FROM mnt_spare_parts WHERE id=?", (sid,))
    if not sp:
        abort(404)
    conn = _db()
    try:
        moves = conn.execute("SELECT * FROM mnt_stock_movements WHERE spare_id=? ORDER BY id DESC LIMIT 50", (sid,)).fetchall()
        machines_ = conn.execute(
            "SELECT m.* FROM mnt_machines m JOIN mnt_spare_compat sc ON sc.machine_id=m.id "
            "WHERE sc.spare_id=?", (sid,)).fetchall()
        alternatives = ai_engine.alternative_parts(conn, sid)
        # cross-module mesh: open replenishment PRs for this spare (auto-reorder
        # header link OR any PR line referencing it), still in flight.
        try:
            open_prs = conn.execute(
                "SELECT DISTINCT p.id, p.pr_no, p.status, p.po_no FROM pr_requests p "
                "LEFT JOIN pr_items i ON i.pr_id = p.id "
                "WHERE p.is_active=1 AND p.status NOT IN "
                "('rejected','cancelled','closed','received') "
                "AND (p.source_ref=? OR i.spare_id=?) ORDER BY p.id DESC LIMIT 5",
                (f"spare:{sid}", sid)).fetchall()
        except Exception:
            open_prs = []
    finally:
        conn.close()
    return render_template("maintenance/spare_profile.html", sp=sp, moves=moves,
                           machines=machines_, alternatives=alternatives,
                           open_prs=open_prs, active="maint_spares")


@bp.route("/spares/<int:sid>/adjust", methods=["POST"])
@login_required
def spare_adjust(sid):
    _require("maint_store")
    ok, msg = svc.adjust_stock(sid, request.form.get("new_qty") or 0,
                               request.form.get("reason"), _u(), request.remote_addr)
    flash("m_saved" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.spare_profile", sid=sid))


@bp.route("/spares/<int:sid>/receive", methods=["POST"])
@login_required
def spare_receive(sid):
    _require("maint_store")
    ok, msg = svc.receive_stock(sid, request.form.get("qty") or 0,
                                request.form.get("price"), _u(), request.remote_addr)
    flash("m_saved" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.spare_profile", sid=sid))


# --------------------------------------------------------------------------
# Requests center + approvals
# --------------------------------------------------------------------------
@bp.route("/requests")
@login_required
def requests():
    _require("maint_view")
    conn = _db()
    try:
        rows = conn.execute("SELECT * FROM mnt_requests ORDER BY id DESC").fetchall()
        items, alts = {}, {}
        for r in rows:
            its = conn.execute("SELECT * FROM mnt_request_items WHERE request_id=?", (r["id"],)).fetchall()
            items[r["id"]] = its
            if r["status"] == "out_of_stock":
                # suggest in-stock substitutes for each unavailable line
                sug = []
                for it in its:
                    if it["spare_id"]:
                        sug += ai_engine.alternative_parts(conn, it["spare_id"], r["machine_id"], limit=3)
                # de-dupe by id
                seen, uniq = set(), []
                for a in sug:
                    if a["id"] not in seen:
                        seen.add(a["id"]); uniq.append(a)
                alts[r["id"]] = uniq
    finally:
        conn.close()
    return render_template("maintenance/requests.html", reqs=rows, items=items, alts=alts,
                           active="maint_requests")


@bp.route("/requests/<int:rid>/issue", methods=["POST"])
@login_required
def request_issue(rid):
    _require("maint_store")
    ok, msg = svc.issue_parts(rid, request.form.get("received_by"), _u(), request.remote_addr)
    flash("m_issued" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.requests"))


@bp.route("/requests/<int:rid>/receive", methods=["POST"])
@login_required
def request_receive(rid):
    _require("maint_technician")
    ok, msg = svc.confirm_receiving(rid, _u(), request.remote_addr)
    flash("m_received" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.requests"))


@bp.route("/approvals")
@login_required
def approvals():
    _require("maint_approve")
    conn = _db()
    try:
        rows = conn.execute(
            """SELECT a.*, r.request_no, r.ticket_id, t.machine_code
               FROM mnt_approvals a JOIN mnt_requests r ON r.id=a.request_id
               LEFT JOIN mnt_tickets t ON t.id=r.ticket_id
               WHERE a.status='pending' ORDER BY a.id""").fetchall()
        items = {}
        for a in rows:
            items[a["request_id"]] = conn.execute(
                "SELECT * FROM mnt_request_items WHERE request_id=?", (a["request_id"],)).fetchall()
        history = conn.execute(
            "SELECT a.*, r.request_no FROM mnt_approvals a JOIN mnt_requests r ON r.id=a.request_id "
            "WHERE a.status!='pending' ORDER BY a.id DESC LIMIT 30").fetchall()
    finally:
        conn.close()
    return render_template("maintenance/approvals.html", approvals=rows, items=items,
                           history=history, active="maint_approvals")


@bp.route("/approvals/<int:aid>/decide", methods=["POST"])
@login_required
def approval_decide(aid):
    _require("maint_approve")
    decision = request.form.get("decision")
    # admins may sign any level; everyone else only the level matching their role
    ok, msg = svc.decide_approval(aid, decision, request.form.get("comment"), _u(),
                                  request.remote_addr, is_admin=_can("maint_admin"))
    flash("m_saved" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.approvals"))


# --------------------------------------------------------------------------
# Preventive maintenance + calendar
# --------------------------------------------------------------------------
@bp.route("/pm")
@login_required
def pm():
    _require("maint_view")
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).date()
    soon = today + timedelta(days=7)

    def _pm_status(next_due):
        try:
            d = datetime.strptime((next_due or "")[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return "scheduled"
        if d < today:
            return "overdue"
        if d <= soon:
            return "due_soon"
        return "scheduled"

    conn = _db()
    try:
        raw = conn.execute(
            "SELECT p.*, m.code mcode, m.name mname FROM mnt_pm_plans p "
            "JOIN mnt_machines m ON m.id=p.machine_id ORDER BY p.next_due").fetchall()
        plans = []
        for p in raw:
            d = dict(p); d["pm_status"] = _pm_status(p["next_due"]); plans.append(d)
        checklists = {}
        for p in plans:
            checklists[p["id"]] = conn.execute(
                "SELECT * FROM mnt_pm_checklist WHERE plan_id=? ORDER BY sort", (p["id"],)).fetchall()
        wos = conn.execute(
            "SELECT w.*, m.code mcode FROM mnt_pm_work_orders w JOIN mnt_machines m ON m.id=w.machine_id "
            "ORDER BY w.id DESC LIMIT 30").fetchall()
    finally:
        conn.close()
    return render_template("maintenance/pm.html", plans=plans, checklists=checklists,
                           wos=wos, active="maint_pm")


@bp.route("/pm/<int:pid>/generate", methods=["POST"])
@login_required
def pm_generate(pid):
    _require("maint_manage")
    conn = _db()
    try:
        p = conn.execute("SELECT * FROM mnt_pm_plans WHERE id=?", (pid,)).fetchone()
        if p:
            n = conn.execute("SELECT COUNT(*) c FROM mnt_pm_work_orders").fetchone()["c"] + 1
            conn.execute(
                """INSERT INTO mnt_pm_work_orders (pm_no,plan_id,machine_id,scheduled_date,status,
                   assigned_to,created_at) VALUES (?,?,?,?,?,?,datetime('now'))""",
                (svc.doc_no("PM", n), pid, p["machine_id"], p["next_due"], "scheduled",
                 p["assigned_to"]))
            conn.commit()
            flash("m_saved", "success")
    finally:
        conn.close()
    return redirect(url_for("maintenance.pm"))


@bp.route("/pm/wo/<int:wid>/complete", methods=["POST"])
@login_required
def pm_complete(wid):
    _require("maint_technician")
    conn = _db()
    try:
        conn.execute("UPDATE mnt_pm_work_orders SET status='completed', completed_at=datetime('now'), "
                     "notes=? WHERE id=?", (request.form.get("notes"), wid))
        conn.commit()
        flash("m_saved", "success")
    finally:
        conn.close()
    return redirect(url_for("maintenance.pm"))


@bp.route("/calendar")
@login_required
def calendar():
    _require("maint_view")
    pm_plans = _all("SELECT p.*, m.code mcode FROM mnt_pm_plans p JOIN mnt_machines m ON m.id=p.machine_id "
                    "WHERE p.active=1 ORDER BY p.next_due")
    return render_template("maintenance/calendar.html", pm_plans=pm_plans, active="maint_calendar")


# --------------------------------------------------------------------------
# Stock movements
# --------------------------------------------------------------------------
@bp.route("/stock")
@login_required
def stock():
    _require("maint_view")
    rows = _all(
        "SELECT mv.*, sp.code part_code, sp.name part_name FROM mnt_stock_movements mv "
        "LEFT JOIN mnt_spare_parts sp ON sp.id=mv.spare_id ORDER BY mv.id DESC LIMIT 200")
    return render_template("maintenance/stock.html", moves=rows, active="maint_stock")


# --------------------------------------------------------------------------
# Reports + CSV export
# --------------------------------------------------------------------------
M_REPORTS = {
    "tickets": ("Maintenance tickets", ["ticket_no", "machine_code", "priority", "status",
                "assigned_to", "department", "total_downtime_min", "created_at"],
                "SELECT ticket_no,machine_code,priority,status,assigned_to,department,"
                "total_downtime_min,created_at FROM mnt_tickets ORDER BY id DESC"),
    "downtime": ("Machine downtime", ["machine_code", "tickets", "downtime_min"],
                 "SELECT machine_code, COUNT(*) tickets, SUM(total_downtime_min) downtime_min "
                 "FROM mnt_tickets GROUP BY machine_code ORDER BY downtime_min DESC"),
    "consumption": ("Spare parts consumption", ["part_code", "part_name", "qty_issued"],
                    "SELECT part_code, part_name, SUM(qty_issued) qty_issued FROM mnt_vouchers "
                    "GROUP BY part_code, part_name ORDER BY qty_issued DESC"),
    "inventory": ("Inventory balance", ["code", "name", "category", "stock_qty", "min_level",
                  "reorder_level", "avg_cost"],
                  "SELECT code,name,category,stock_qty,min_level,reorder_level,avg_cost "
                  "FROM mnt_spare_parts ORDER BY code"),
    "lowstock": ("Low stock", ["code", "name", "stock_qty", "reorder_level"],
                 "SELECT code,name,stock_qty,reorder_level FROM mnt_spare_parts "
                 "WHERE stock_qty <= reorder_level ORDER BY stock_qty"),
    "approvals": ("Approval history", ["request_id", "level", "approver_role", "approver_user",
                  "status", "comment", "decided_at"],
                  "SELECT request_id,level,approver_role,approver_user,status,comment,decided_at "
                  "FROM mnt_approvals ORDER BY id DESC"),
    "stock": ("Stock movements", ["movement_no", "type", "spare_id", "qty", "before_qty",
              "after_qty", "performed_by", "created_at"],
              "SELECT movement_no,type,spare_id,qty,before_qty,after_qty,performed_by,created_at "
              "FROM mnt_stock_movements ORDER BY id DESC"),
}


@bp.route("/reports")
@login_required
def reports():
    _require("maint_view")
    return render_template("maintenance/reports.html", reports=M_REPORTS, active="maint_reports")


@bp.route("/reports/export/<key>.csv")
@login_required
def report_export(key):
    _require("maint_view")
    spec = M_REPORTS.get(key)
    if not spec:
        abort(404)
    label, headers, sql = spec
    rows = _all(sql)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        w.writerow([r[h] for h in headers])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=mnt_{key}.csv"})


@bp.route("/reports/pdf/<key>.pdf")
@login_required
def report_pdf(key):
    _require("maint_view")
    spec = M_REPORTS.get(key)
    if not spec:
        abort(404)
    label, headers, sql = spec
    rows = _all(sql)
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        abort(503)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title=f"TC Platform - {label}")
    styles = getSampleStyleSheet()
    elems = [Paragraph(f"TC Platform &mdash; {label}", styles["Title"]), Spacer(1, 8)]
    data = [headers] + [[("" if r[h] is None else str(r[h])) for h in headers] for r in rows[:500]]
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#07080b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elems.append(table)
    doc.build(elems)
    buf.seek(0)
    return Response(buf.read(), mimetype="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=mnt_{key}.pdf"})


# --------------------------------------------------------------------------
# Settings / master data
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Excel import (machines / spare parts) with template + validation
# --------------------------------------------------------------------------
IMPORT_SPECS = {
    "machines": {
        "headers": ["code", "name", "type", "brand", "department", "area", "line_no",
                    "criticality", "status"],
        "example": ["M-100", "Sample Sewing Machine", "Sewing", "Juki", "Production",
                    "Hall A", "L9", "high", "running"],
    },
    "spares": {
        "headers": ["code", "name", "category", "uom", "stock_qty", "min_level",
                    "reorder_level", "max_level", "avg_cost", "criticality"],
        "example": ["SP-100", "Sample Needle", "needle_textile", "pcs", 100, 40, 60, 300, 0.5, "high"],
    },
}


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_page():
    """GET: the import screen. POST: the machine-register CSV upsert (front door A;
    scripts/import_machines.py is front door B — both call the SAME
    app.maintenance.machine_import.parse_machines + upsert_machines, so they
    cannot drift). The .xlsx tabs still POST to import_run below, untouched."""
    _require("maint_admin")
    from app.maintenance.machine_import import (parse_machines, upsert_machines,
                                                machine_stats)
    kind = request.args.get("kind", "machines")
    reg_result = reg_parsed = None
    if request.method == "POST":
        kind = "register"
        file = request.files.get("file")
        if not file or not file.filename:
            flash("Choose a file to import.", "error")
        else:
            try:
                rows, reg_parsed = parse_machines(io.BytesIO(file.read()))
            except Exception as exc:
                rows, reg_parsed = [], {"error": f"{type(exc).__name__}: {exc}",
                                        "rejects": [], "flagged": []}
            if reg_parsed.get("error"):
                flash(reg_parsed["error"], "error")
            else:
                conn = _db()
                try:
                    reg_result = upsert_machines(conn, rows, _u(),
                                                 source=file.filename[:120])
                    svc.audit(conn, _u(), "import", "machines", 0, None,
                              f"+{reg_result['added']}",
                              comment=f"updated {reg_result['updated']}, "
                                      f"unchanged {reg_result['unchanged']}",
                              ip=request.remote_addr)
                    conn.commit()
                except Exception as exc:
                    # upsert_machines already rolled back — the whole file is one
                    # transaction, so nothing landed half-way. Say so instead of
                    # returning a 500: re-running the import is safe.
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    flash(f"Import failed ({type(exc).__name__}). Nothing was written "
                          f"— fix the file and run it again.", "error")
                finally:
                    conn.close()
                if reg_result is not None:
                    flash(f"{reg_result['added']} added, {reg_result['updated']} updated, "
                          f"{reg_result['unchanged']} unchanged, "
                          f"{len(reg_parsed.get('rejects') or [])} rejected.", "success")
    reg_stats = None
    if kind == "register":
        conn = _db()
        try:
            reg_stats = machine_stats(conn)
        finally:
            conn.close()
    return render_template("maintenance/import.html", kind=kind, result=None,
                           reg_result=reg_result, reg_parsed=reg_parsed,
                           reg_stats=reg_stats, active="maint_settings")


@bp.route("/import/template/<kind>.xlsx")
@login_required
def import_template(kind):
    _require("maint_admin")
    spec = IMPORT_SPECS.get(kind)
    if not spec:
        abort(404)
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = kind
    ws.append(spec["headers"])
    ws.append(spec["example"])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return Response(buf.read(),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename=tc_{kind}_template.xlsx"})


@bp.route("/import/<kind>", methods=["POST"])
@login_required
def import_run(kind):
    _require("maint_admin")
    spec = IMPORT_SPECS.get(kind)
    if not spec:
        abort(404)
    f = request.files.get("file")
    if not f or not (f.filename or "").lower().endswith(".xlsx"):
        flash("m_import_bad_file", "error")
        return redirect(url_for("maintenance.import_page", kind=kind))
    import openpyxl
    try:
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    except Exception:
        flash("m_import_bad_file", "error")
        return redirect(url_for("maintenance.import_page", kind=kind))
    rows = list(wb.active.iter_rows(values_only=True))
    headers = spec["headers"]
    added, skipped, errors = 0, 0, []
    conn = _db()
    try:
        table = "mnt_machines" if kind == "machines" else "mnt_spare_parts"
        for i, row in enumerate(rows[1:], start=2):  # skip header row
            d = dict(zip(headers, row))
            code = str(d.get("code") or "").strip()
            if not code:
                errors.append(f"Row {i}: missing code")
                continue
            if conn.execute(f"SELECT 1 FROM {table} WHERE code=?", (code,)).fetchone():
                skipped += 1
                continue
            try:
                if kind == "machines":
                    conn.execute(
                        "INSERT INTO mnt_machines (code,name,type,brand,department,area,line_no,"
                        "criticality,status,qr_token,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                        (code, d.get("name"), d.get("type"), d.get("brand"), d.get("department"),
                         d.get("area"), str(d.get("line_no") or ""),
                         (d.get("criticality") or "medium"), (d.get("status") or "running"),
                         "MQR" + code.replace("-", "")))
                else:
                    qty = float(d.get("stock_qty") or 0)
                    cur = conn.execute(
                        "INSERT INTO mnt_spare_parts (code,name,category,uom,stock_qty,min_level,"
                        "reorder_level,max_level,avg_cost,criticality,qr_token,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,datetime('now'))",
                        (code, d.get("name"), (d.get("category") or "other"), (d.get("uom") or "pcs"),
                         qty, float(d.get("min_level") or 0), float(d.get("reorder_level") or 0),
                         float(d.get("max_level") or 0), float(d.get("avg_cost") or 0),
                         (d.get("criticality") or "medium"), "PQR" + code.replace("-", "")))
                    if qty:
                        # Number the movement from its OWN row id (collision-free), like
                        # _move_stock. The old COUNT(*)+1 scheme repeats a number after any
                        # id gap (a rolled-back insert advances the sequence on Postgres) and
                        # violates the UNIQUE(movement_no) constraint -> failed import.
                        mv = conn.execute(
                            "INSERT INTO mnt_stock_movements (type,spare_id,qty,before_qty,"
                            "after_qty,performed_by,notes,created_at) VALUES (?,?,?,?,?,?,?,datetime('now'))",
                            ("opening", cur.lastrowid, qty, 0, qty,
                             _u()["username"], "Imported opening balance"))
                        conn.execute("UPDATE mnt_stock_movements SET movement_no=? WHERE id=?",
                                     (svc.doc_no("STK", mv.lastrowid), mv.lastrowid))
                added += 1
            except (ValueError, TypeError) as exc:
                errors.append(f"Row {i}: {type(exc).__name__}")
        conn.commit()
        svc.audit(conn, _u(), "import", kind, 0, None, f"+{added}", comment=f"skipped {skipped}",
                  ip=request.remote_addr)
        conn.commit()
    finally:
        conn.close()
    return render_template("maintenance/import.html", kind=kind,
                           result={"added": added, "skipped": skipped, "errors": errors},
                           active="maint_settings")


# --------------------------------------------------------------------------
# Workflow & Governance — the documented, admin-configurable workflow
# --------------------------------------------------------------------------
@bp.route("/workflow")
@login_required
def workflow():
    _require("maint_view")
    conn = _db()
    try:
        # The stored prose is picked SERVER-SIDE for this reader's language; static
        # labels stay data-i18n and are swapped client-side from the same lang_pref.
        d = wf.page_data(conn, lang=(_u().get("lang_pref") or "en"))
    finally:
        conn.close()
    return render_template("maintenance/workflow.html", d=d, wf=wf, active="maint_workflow")


@bp.route("/workflow/setting", methods=["POST"])
@login_required
def workflow_setting():
    _require("maint_admin")
    key = request.form.get("key") or ""
    conn = _db()
    try:
        if request.form.get("reset"):
            ok, msg = wf.reset_setting(conn, key, _u())
        else:
            ok, msg = wf.set_setting(conn, key, request.form.get("value"), _u())
    finally:
        conn.close()
    flash("m_saved" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.workflow"))


@bp.route("/workflow/text", methods=["POST"])
@login_required
def workflow_text():
    """One editor for every explanation block (and a stage's signing role)."""
    _require("maint_admin")
    kind, key = request.form.get("kind") or "", request.form.get("key") or ""
    conn = _db()
    try:
        if request.form.get("reset"):
            ok, msg = wf.reset_text(conn, kind, key, _u())
        else:
            # A language absent from the form stays untouched (set_text skips None),
            # so saving one language never blanks the other two.
            ok, msg = wf.set_text(conn, kind, key, request.form.get("explanation"), _u(),
                                  role=request.form.get("role"),
                                  ar=request.form.get("explanation_ar"),
                                  tr=request.form.get("explanation_tr"))
    finally:
        conn.close()
    flash("m_saved" if ok else msg, "success" if ok else "error")
    return redirect(url_for("maintenance.workflow"))


@bp.route("/settings")
@login_required
def settings():
    _require("maint_admin")
    conn = _db()
    try:
        matrix = conn.execute("SELECT * FROM mnt_approval_matrix ORDER BY id").fetchall()
        sett = conn.execute("SELECT * FROM mnt_settings ORDER BY key").fetchall()
        counts = {
            "machines": conn.execute("SELECT COUNT(*) c FROM mnt_machines").fetchone()["c"],
            "spares": conn.execute("SELECT COUNT(*) c FROM mnt_spare_parts").fetchone()["c"],
            "tickets": conn.execute("SELECT COUNT(*) c FROM mnt_tickets").fetchone()["c"],
        }
    finally:
        conn.close()
    return render_template("maintenance/settings.html", matrix=matrix, settings=sett,
                           counts=counts, active="maint_settings")


# --------------------------------------------------------------------------
# QR code generation
# --------------------------------------------------------------------------
@bp.route("/qr/<kind>/<int:eid>.png")
@login_required
def qr(kind, eid):
    _require("maint_view")
    try:
        import qrcode
    except ImportError:
        abort(503)
    if kind == "machine":
        target = url_for("maintenance.ticket_new", machine=eid, _external=True)
    elif kind == "spare":
        target = url_for("maintenance.spare_profile", sid=eid, _external=True)
    elif kind == "ticket":
        target = url_for("maintenance.ticket_detail", tid=eid, _external=True)
    else:
        abort(404)
    img = qrcode.make(target)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")
