"""
HR Core routes (native platform blueprint at /people) — attendance, leave,
skill matrix and piece-rate incentive. The employee roster comes from the
existing platform employee master (prob_employees); this module never forks it.
"""
import time
from datetime import date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.auth import login_required, permission_required, current_user
from app.people import services as svc
from app.people.constants import (ATT_STATUS, LEAVE_TYPES, LEAVE_STATUS, OPERATIONS,
                                  SKILL_LEVELS, INCENTIVE_THRESHOLD_PCT,
                                  DEFAULT_RATE_PER_MINUTE, INCENTIVE_CURRENCY)

bp = Blueprint("people", __name__, url_prefix="/people")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s

# Friendly text for the (ok, reason) codes the services return.
_MSG = {
    "employee_required": "Pick an employee.",
    "employee_not_found": "That employee is not on the roster.",
    "employee_and_operation_required": "Employee and operation are both required.",
    "bad_dates": "Check the dates — the end date cannot be before the start date.",
    "insufficient_balance": "Not enough leave balance for that request.",
    "already_approved": "Already approved — the balance was deducted once.",
    "already_decided": "That request has already been decided.",
    "not_found": "Request not found.",
    "bad_action": "Unknown action.",
    "bad_smv": "SMV must be greater than zero.",
    "bad_minutes": "Minutes worked must be greater than zero.",
    "duplicate_entry": "That exact output is already recorded for this operator and day.",
    "range_too_long": "That range is longer than a year — check the dates.",
    "overlaps_existing": "That employee already has leave covering those days.",
    "implausible_output": "That output implies over 400% efficiency — check the piece count.",
}


def _u():
    return current_user()


def _sweep_throttled():
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.absence_sweep()


def _range():
    """from/to query params, defaulting to the last 30 days."""
    to_d = request.args.get("to") or str(date.today())
    from_d = request.args.get("from") or str(date.today() - timedelta(days=29))
    return from_d, to_d


@bp.route("/")
@login_required
@permission_required("ppl_view")
def index():
    _sweep_throttled()
    return render_template("people/dashboard.html", active="people", d=svc.dashboard(),
                           currency=INCENTIVE_CURRENCY)


# --- attendance -----------------------------------------------------------
@bp.route("/attendance")
@login_required
@permission_required("ppl_view")
def attendance():
    # ONE parser for the day: the page title, the pre-fill lookup and the hidden
    # work_date the form posts back all have to be the identical string, or a
    # non-canonical ?d= (2026-3-5) renders a blank page over a day that is already
    # marked and Save overwrites every absence with 'present'.
    day = svc.normalize_date(request.args.get("d"))
    dept = request.args.get("dept") or None
    # the pre-fill MUST cover every employee the form below posts back, or an
    # unlisted-but-recorded day returns as a blank 'present' and overwrites it —
    # so it is keyed off the very list being rendered, not a separate query
    emps = svc.employees(department=dept)
    existing = svc.attendance_for(day, [e["id"] for e in emps])
    from_d, to_d = _range()
    return render_template("people/attendance.html", active="ppl_attendance",
                           day=day, f_dept=dept, statuses=ATT_STATUS,
                           employees=emps,
                           existing=existing, departments=svc.departments(),
                           stats=svc.attendance_stats(from_d, to_d, dept),
                           from_d=from_d, to_d=to_d,
                           rows=svc.list_attendance(department=dept, limit=200))


@bp.route("/attendance", methods=["POST"])
@login_required
@permission_required("ppl_manage")
def attendance_save():
    day = request.form.get("work_date") or str(date.today())
    rows = []
    for k in request.form:
        if not k.startswith("st_"):
            continue
        eid = k[3:]
        rows.append({"employee_id": eid, "status": request.form.get(k),
                     "check_in": request.form.get("in_" + eid),
                     "check_out": request.form.get("out_" + eid),
                     "ot_hours": request.form.get("ot_" + eid)})
    n = svc.save_attendance_day(day, rows, _u())
    flash(f"Attendance saved for {n} employee(s) on {day}." if n else "Nothing to save.",
          "success" if n else "warning")
    return redirect(url_for("people.attendance", d=day, dept=request.form.get("dept") or None))


# --- leave ----------------------------------------------------------------
@bp.route("/leave")
@login_required
@permission_required("ppl_view")
def leave():
    status = request.args.get("status") or None
    return render_template("people/leave.html", active="ppl_leave",
                           rows=svc.list_leave(status=status), f_status=status,
                           statuses=LEAVE_STATUS, leave_types=LEAVE_TYPES,
                           employees=svc.employees(), balances=svc.list_balances())


@bp.route("/leave", methods=["POST"])
@login_required
@permission_required("ppl_manage")
def leave_request():
    ok, msg = svc.request_leave(request.form, _u())
    flash("Leave request submitted — pending approval." if ok else _MSG.get(msg, msg),
          "success" if ok else "error")
    return redirect(url_for("people.leave"))


@bp.route("/leave/<int:leave_id>/decide", methods=["POST"])
@login_required
@permission_required("ppl_approve")
def leave_decide(leave_id):
    action = request.form.get("action") or "approve"
    ok, msg = svc.decide_leave(leave_id, action, _u())
    flash(f"Leave {msg}." if ok else _MSG.get(msg, msg), "success" if ok else "error")
    # never bounce back to the Referer header — it is attacker-controlled, which
    # turns an approval into an open redirect off the platform. Come back to
    # the filter the approver was looking at, and only if it is a real one.
    back = request.form.get("back_status")
    return redirect(url_for("people.leave", status=back if back in LEAVE_STATUS else None))


# --- skill matrix ---------------------------------------------------------
@bp.route("/skills")
@login_required
@permission_required("ppl_view")
def skills():
    dept = request.args.get("dept") or None
    op = request.args.get("op") or None
    return render_template("people/skills.html", active="ppl_skills",
                           matrix=svc.skill_matrix(department=dept), f_dept=dept, f_op=op,
                           departments=svc.departments(), operations=OPERATIONS,
                           levels=SKILL_LEVELS, employees=svc.employees(),
                           best=svc.best_operators(op) if op else [])


@bp.route("/skills", methods=["POST"])
@login_required
@permission_required("ppl_manage")
def skill_save():
    ok, msg = svc.set_skill(request.form.get("employee_id"), request.form.get("operation"),
                            request.form.get("level"), request.form.get("efficiency_pct"), _u())
    flash("Skill updated." if ok else _MSG.get(msg, msg), "success" if ok else "error")
    return redirect(url_for("people.skills", dept=request.form.get("dept") or None))


# --- piece-rate / incentive ----------------------------------------------
@bp.route("/incentive")
@login_required
@permission_required("ppl_view")
def incentive():
    from_d, to_d = _range()
    return render_template("people/incentive.html", active="ppl_incentive",
                           summary=svc.incentive_summary(from_d, to_d),
                           rows=svc.list_piece_rate(from_d, to_d), from_d=from_d, to_d=to_d,
                           employees=svc.employees(), operations=OPERATIONS,
                           threshold=INCENTIVE_THRESHOLD_PCT, rate=DEFAULT_RATE_PER_MINUTE,
                           currency=INCENTIVE_CURRENCY)


@bp.route("/incentive", methods=["POST"])
@login_required
@permission_required("ppl_manage")
def incentive_add():
    ok, msg = svc.add_piece_rate(request.form, _u())
    flash("Production recorded." if ok else _MSG.get(msg, msg), "success" if ok else "error")
    return redirect(url_for("people.incentive"))


# --- exports (same wire format as every other module) ---------------------
@bp.route("/export/<key>.csv")
@login_required
@permission_required("ppl_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "people", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("ppl_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "people", "json")
    if resp is None:
        abort(404)
    return resp


# Declares this module's reports with the shared reporting engine. Import only —
# it runs no query and touches no database (see app/people/reports.py).
from app.people import reports as _reports        # noqa: E402,F401
