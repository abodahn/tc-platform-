"""
AI Prediction & Intelligence Center — routes.

Command Center, Alert Center, Module Breakdown, Assistant, Reports, Settings
and Model-run history. Viewing needs `view_dashboard`; recompute/settings need
`access_admin`; sensitive domains (payroll, HR) are only shown to admins.
"""
import io

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, abort)

from app.auth import login_required, permission_required, current_user, user_can
from app.db import get_db, log_audit
from app.intelligence import services as isvc
from app.ai_engine import orchestrator, nlp_assistant, report_generator as rg
from app.ai_engine.risk_scoring import DOMAIN_LABELS

bp = Blueprint("intelligence", __name__, url_prefix="/intelligence")


def _u():
    u = current_user()
    return u["username"] if u else "system"


def _sensitive_ok():
    return user_can("access_admin")


@bp.route("/")
@permission_required("view_dashboard")
def command_center():
    data = isvc.dashboard_data()
    return render_template("intelligence/command_center.html", active="ai_center",
                           can_admin=user_can("access_admin"), **data)


@bp.route("/recompute", methods=["POST"])
@permission_required("access_admin")
def recompute():
    try:
        res = orchestrator.run(username=_u())
        log_audit(_u(), "ai_recompute",
                  f"health={res['health']} alerts={res['n_alerts']} findings={res['n_findings']}",
                  request.remote_addr or "")
        flash(f"AI run complete — health {res['health']}/100, {res['n_alerts']} alerts.", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"AI run failed: {exc}", "error")
    return redirect(request.referrer or url_for("intelligence.command_center"))


@bp.route("/alerts")
@permission_required("view_dashboard")
def alerts():
    status = request.args.get("status", "open")
    domain = request.args.get("domain", "all")
    severity = request.args.get("severity", "all")
    rows = isvc.list_alerts(status=status, domain=domain, severity=severity,
                            allow_sensitive=_sensitive_ok())
    return render_template("intelligence/alerts.html", active="ai_alerts", alerts=rows,
                           status=status, domain=domain, severity=severity,
                           statuses=isvc.ALERT_STATUSES, verdicts=isvc.FEEDBACK_VERDICTS,
                           domains=list(DOMAIN_LABELS.items()),
                           can_admin=user_can("access_admin"))


@bp.route("/alerts/<int:alert_id>/status", methods=["POST"])
@permission_required("view_dashboard")
def alert_status(alert_id):
    status = (request.form.get("status") or "").strip()
    if isvc.set_alert_status(alert_id, status):
        log_audit(_u(), "ai_alert_status", f"#{alert_id} -> {status}", request.remote_addr or "")
        flash(f"Alert marked {status}.", "success")
    else:
        flash("Invalid status.", "error")
    return redirect(request.referrer or url_for("intelligence.alerts"))


@bp.route("/alerts/<int:alert_id>/feedback", methods=["POST"])
@permission_required("view_dashboard")
def alert_feedback(alert_id):
    verdict = (request.form.get("verdict") or "").strip()
    note = request.form.get("note", "")
    if isvc.add_feedback(alert_id, _u(), verdict, note):
        log_audit(_u(), "ai_feedback", f"#{alert_id}: {verdict}", request.remote_addr or "")
        flash("Thanks — feedback recorded.", "success")
    else:
        flash("Invalid feedback.", "error")
    return redirect(request.referrer or url_for("intelligence.alerts"))


@bp.route("/breakdown")
@permission_required("view_dashboard")
def breakdown():
    rows = isvc.breakdown(allow_sensitive=_sensitive_ok())
    return render_template("intelligence/breakdown.html", active="ai_breakdown",
                           domains=rows, sensitive_hidden=not _sensitive_ok())


@bp.route("/assistant")
@permission_required("view_dashboard")
def assistant():
    return render_template("intelligence/assistant.html", active="ai_assistant")


@bp.route("/assistant/ask", methods=["POST"])
@permission_required("view_dashboard")
def assistant_ask():
    data = request.get_json(silent=True) or {}
    q = (data.get("q") or "").strip()
    isvc.log_query(_u(), q)
    conn = get_db()
    try:
        res = nlp_assistant.answer(q, conn, user=current_user())
    finally:
        conn.close()
    # hide sensitive rows from non-admins
    if not _sensitive_ok():
        res["scores"] = [s for s in res.get("scores", []) if s["domain"] not in isvc.SENSITIVE_DOMAINS]
        res["alerts"] = [a for a in res.get("alerts", []) if a["domain"] not in isvc.SENSITIVE_DOMAINS]
    return jsonify(res)


@bp.route("/reports")
@permission_required("view_dashboard")
def reports():
    return render_template("intelligence/reports.html", active="ai_reports",
                           runs=isvc.runs(), feedback=isvc.feedback_summary(),
                           can_export=user_can("export_reports") or user_can("view_reports"))


@bp.route("/report/alerts.<fmt>")
@permission_required("view_dashboard")
def report_alerts(fmt):
    rows = isvc.list_alerts(status="all", allow_sensitive=_sensitive_ok())
    if fmt == "csv":
        return send_file(io.BytesIO(rg.alerts_csv(rows)), mimetype="text/csv",
                         as_attachment=True, download_name="ai_alerts.csv")
    if fmt in ("xlsx", "excel"):
        return send_file(io.BytesIO(rg.alerts_xlsx(rows)),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name="ai_alerts.xlsx")
    if fmt == "pdf":
        data = isvc.dashboard_data()
        pdf = rg.risk_pdf("AI Risk Report", data["scores"], rows, health=data.get("health"))
        return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                         as_attachment=True, download_name="ai_risk_report.pdf")
    abort(404)


@bp.route("/settings", methods=["GET", "POST"])
@permission_required("access_admin")
def settings():
    if request.method == "POST":
        isvc.set_setting("auto_run", "1" if request.form.get("auto_run") else "0")
        isvc.set_setting("horizon_days", request.form.get("horizon_days") or "30")
        log_audit(_u(), "ai_settings", "updated", request.remote_addr or "")
        flash("AI settings saved.", "success")
        return redirect(url_for("intelligence.settings"))
    return render_template("intelligence/settings.html", active="ai_settings",
                           auto_run=isvc.get_setting("auto_run", "1") == "1",
                           horizon_days=isvc.get_setting("horizon_days", "30"),
                           runs=isvc.runs())
