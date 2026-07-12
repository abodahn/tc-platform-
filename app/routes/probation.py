"""
HR — Probation Management routes (native platform blueprint at /hr/probation).

Uses the platform auth, RBAC, CSRF, i18n, base template and audit/notification
systems. Every protected operation is guarded by a permission AND, where the
record is employee-scoped, by an object-level scope check (no IDOR).
"""
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   abort, send_file, Response)

from app.auth import login_required, permission_required, current_user, user_can
from app.probation import services as svc
from app.probation.constants import (
    Status, Outcome, Recommendation, CRITERIA, CATEGORIES, RATING_SCALE,
    FINAL_RECOMMENDATIONS,
)

bp = Blueprint("probation", __name__, url_prefix="/hr/probation")


# ---- helpers -------------------------------------------------------------
def _u():
    return current_user()


def _case_or_404(case_id):
    case = svc.get_case_full(case_id)
    if not case:
        abort(404)
    return case


def _require_view(case):
    """Object-level scope guard — prevents IDOR via URL/id fiddling."""
    if not svc.can_view_case(_u(), case):
        abort(403)


def _flash_errors(errors):
    # errors: list of (i18n_key, label|None). Concise English flash (platform convention).
    from app.probation.constants import Status as _S  # noqa
    human = {
        "prob.err.score_required": "Missing score",
        "prob.err.score_range": "Score out of range",
        "prob.err.notes_required": "A note is required for low scores",
        "prob.err.recommendation_required": "Final recommendation is required",
        "prob.err.comments_required": "Manager comments are required",
        "prob.err.reason_required": "A reason is required",
        "prob.err.override_reason_required": "An override justification is required",
        "prob.err.invalid_state": "This action is not allowed in the current status",
        "prob.err.locked": "This evaluation is locked",
        "prob.err.not_found": "Record not found",
        "prob.err.date_required": "A date is required",
    }
    msgs = []
    for key, label in errors:
        m = human.get(key, key)
        msgs.append(f"{m}: {label}" if label else m)
    flash(" · ".join(msgs[:6]) + (f" (+{len(msgs)-6} more)" if len(msgs) > 6 else ""), "error")


# ==========================================================================
# Dashboard / lists
# ==========================================================================
@bp.route("/")
@login_required
@permission_required("prob_view")
def dashboard():
    svc.maybe_auto_scan()   # daily throttled reminder scan (no external cron needed)
    return render_template("probation/dashboard.html", active="prob_dashboard",
                           d=svc.dashboard(_u()), Status=Status, Outcome=Outcome)


@bp.route("/mine")
@login_required
@permission_required("prob_view")
def mine():
    return render_template("probation/cases.html", active="prob_mine",
                           rows=svc.my_pending(_u()), title_key="prob.nav.mine",
                           Status=Status, Outcome=Outcome, filters={}, mine=True)


@bp.route("/cases")
@login_required
@permission_required("prob_view")
def cases():
    f = {"status": request.args.get("status") or None,
         "department": request.args.get("department") or None,
         "outcome": request.args.get("outcome") or None,
         "q": request.args.get("q") or None}
    rows = svc.list_cases(_u(), **f)
    return render_template("probation/cases.html", active="prob_cases", rows=rows,
                           title_key="prob.nav.cases", Status=Status, Outcome=Outcome,
                           filters=f, mine=False)


# ==========================================================================
# Initiate / employees
# ==========================================================================
@bp.route("/initiate")
@login_required
@permission_required("prob_hr_review")
def initiate():
    return render_template("probation/initiate.html", active="prob_initiate",
                           employees=svc.list_employees(_u(), q=request.args.get("q")),
                           q=request.args.get("q", ""))


@bp.route("/initiate/open-cases", methods=["POST"])
@login_required
@permission_required("prob_hr_review")
def open_cases_bulk():
    """Open probation cases for the rostered employees who don't have one yet."""
    only_in_prob = request.form.get("scope", "in_probation") != "all"
    res = svc.bulk_open_cases(_u(), only_in_probation=only_in_prob)
    if res["opened"]:
        flash(f"Opened {res['opened']} probation case(s). "
              f"{res['skipped_existing']} already had one.", "success")
    else:
        flash("No new cases to open — every eligible employee already has a case.", "info")
    return redirect(url_for("probation.cases"))


@bp.route("/cases/new", methods=["POST"])
@login_required
@permission_required("prob_hr_review")
def case_new():
    emp_id = request.form.get("employee_id")
    try:
        cid, err = svc.create_case(_u(), int(emp_id))
    except (TypeError, ValueError):
        cid, err = None, "prob.err.not_found"
    if err or not cid:
        flash("Could not create the probation case.", "error")
        return redirect(url_for("probation.initiate"))
    flash("Probation case created.", "success")
    return redirect(url_for("probation.case_detail", case_id=cid))


# ==========================================================================
# Case detail + evaluation
# ==========================================================================
@bp.route("/cases/<int:case_id>")
@login_required
@permission_required("prob_view")
def case_detail(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    # Hide confidential HR-only comments from non-HR viewers
    if not svc.is_hr(_u()):
        case["comments"] = [c for c in case["comments"] if not c.get("is_confidential")]
    return render_template("probation/case_detail.html", active="prob_cases", case=case,
                           Status=Status, Outcome=Outcome, Recommendation=Recommendation,
                           CATEGORIES=CATEGORIES, RATING_SCALE=RATING_SCALE,
                           final_recos=FINAL_RECOMMENDATIONS,
                           can_evaluate=user_can("prob_evaluate"), is_hr=svc.is_hr(_u()),
                           is_admin=svc.is_admin(_u()))


@bp.route("/cases/<int:case_id>/save", methods=["POST"])
@login_required
@permission_required("prob_evaluate")
def case_save(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    _, err = svc.save_evaluation(case_id, request.form, _u())
    flash("Draft saved." if not err else "Could not save (evaluation is locked).",
          "success" if not err else "error")
    return redirect(url_for("probation.case_detail", case_id=case_id))


@bp.route("/cases/<int:case_id>/submit", methods=["POST"])
@login_required
@permission_required("prob_evaluate")
def case_submit(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    # persist any edits on the same request, then submit
    svc.save_evaluation(case_id, request.form, _u())
    ok, errors = svc.submit(case_id, _u())
    if ok:
        flash("Evaluation submitted for review.", "success")
    else:
        _flash_errors(errors)
    return redirect(url_for("probation.case_detail", case_id=case_id))


@bp.route("/cases/<int:case_id>/forward", methods=["POST"])
@login_required
@permission_required("prob_evaluate")
def case_forward(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    ok, errors = svc.manager_forward(case_id, _u(), request.form.get("comment"))
    flash("Forwarded to HR." if ok else "Could not forward.", "success" if ok else "error")
    return redirect(url_for("probation.case_detail", case_id=case_id))


# ==========================================================================
# HR review & approval
# ==========================================================================
@bp.route("/review")
@login_required
@permission_required("prob_hr_review")
def review():
    rows = svc.list_cases(_u(), status=Status.PENDING_HR_REVIEW)
    return render_template("probation/cases.html", active="prob_review", rows=rows,
                           title_key="prob.nav.review", Status=Status, Outcome=Outcome,
                           filters={}, mine=False)


@bp.route("/cases/<int:case_id>/decide", methods=["POST"])
@login_required
@permission_required("prob_hr_review")
def case_decide(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    decision = request.form.get("decision", "approve")
    ok, errors = svc.hr_decide(
        case_id, _u(), decision,
        reason=request.form.get("reason"),
        final_outcome=request.form.get("final_outcome"),
        override_reason=request.form.get("override_reason"))
    if ok:
        flash({"approve": "Evaluation approved.", "reject": "Recorded: not confirmed.",
               "return": "Returned for correction."}.get(decision, "Decision recorded."), "success")
    else:
        _flash_errors(errors)
    return redirect(url_for("probation.case_detail", case_id=case_id))


@bp.route("/cases/<int:case_id>/reopen", methods=["POST"])
@login_required
@permission_required("prob_admin")
def case_reopen(case_id):
    case = _case_or_404(case_id)
    ok, errors = svc.admin_reopen(case_id, _u(), request.form.get("reason", ""))
    if ok:
        flash("Case reopened for correction.", "success")
    else:
        _flash_errors(errors)
    return redirect(url_for("probation.case_detail", case_id=case_id))


@bp.route("/cases/<int:case_id>/extend", methods=["POST"])
@login_required
@permission_required("prob_hr_review")
def case_extend(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    ok, errors = svc.extend_probation(case_id, _u(),
                                      request.form.get("new_end_date"), request.form.get("reason"))
    if ok:
        flash("Probation extended.", "success")
    else:
        _flash_errors(errors)
    return redirect(url_for("probation.case_detail", case_id=case_id))


@bp.route("/cases/<int:case_id>/cancel", methods=["POST"])
@login_required
@permission_required("prob_hr_review")
def case_cancel(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    svc.cancel_case(case_id, _u(), request.form.get("reason"))
    flash("Case cancelled.", "success")
    return redirect(url_for("probation.case_detail", case_id=case_id))


@bp.route("/cases/<int:case_id>/comment", methods=["POST"])
@login_required
@permission_required("prob_view")
def case_comment(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    confidential = bool(request.form.get("confidential")) and svc.is_hr(_u())
    svc.add_comment(case_id, _u(), request.form.get("body"), confidential)
    return redirect(url_for("probation.case_detail", case_id=case_id))


@bp.route("/cases/<int:case_id>/print")
@login_required
@permission_required("prob_view")
def case_print(case_id):
    case = _case_or_404(case_id)
    _require_view(case)
    return render_template("probation/print.html", case=case, Status=Status, Outcome=Outcome,
                           CATEGORIES=CATEGORIES)


# ==========================================================================
# Reports & analytics
# ==========================================================================
@bp.route("/reports")
@login_required
@permission_required("prob_reports")
def reports():
    return render_template("probation/reports.html", active="prob_reports",
                           d=svc.dashboard(_u()), Status=Status, Outcome=Outcome)


@bp.route("/reports/cases.csv")
@login_required
@permission_required("prob_reports")
def reports_csv():
    csv_data = svc.cases_csv(_u(),
                             status=request.args.get("status"),
                             department=request.args.get("department"),
                             outcome=request.args.get("outcome"))
    return Response(csv_data.encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=probation_cases.csv"})


# ==========================================================================
# Reminders & due dates
# ==========================================================================
@bp.route("/reminders")
@login_required
@permission_required("prob_hr_review")
def reminders():
    return render_template("probation/reminders.html", active="prob_reminders",
                           history=svc.notification_history(_u()), d=svc.dashboard(_u()))


@bp.route("/reminders/run", methods=["POST"])
@login_required
@permission_required("prob_hr_review")
def reminders_run():
    r = svc.scan_reminders()
    flash(f"Reminder scan complete — due {r['due']}, overdue {r['overdue']}, stalled {r['pending']}.", "success")
    return redirect(url_for("probation.reminders"))


# ==========================================================================
# Import
# ==========================================================================
@bp.route("/import", methods=["GET", "POST"])
@login_required
@permission_required("prob_import")
def import_data():
    report = None
    if request.method == "POST":
        rows = _parse_upload(request.files.get("file"))
        if rows is None:
            flash("Unsupported file. Upload a .csv or .xlsx exported from the HR sheet.", "error")
        else:
            report = svc.import_rows(rows, _u(), (request.files["file"].filename or "import"))
            msg = (f"Import done — created {report['created']}, updated {report['updated']}, "
                   f"skipped {report['skipped']}, errors {report['errors']}.")
            # Bridge the roster into live probation cases so the import "takes
            # effect" on the dashboard. Default ON; opens a case for each imported
            # employee still within their probation window (idempotent).
            if request.form.get("open_cases", "1") not in ("", "0", "off", None):
                res = svc.bulk_open_cases(_u(), only_in_probation=True)
                report["cases_opened"] = res["opened"]
                report["cases_considered"] = res["considered"]
                if res["opened"]:
                    msg += f" Opened {res['opened']} probation case(s)."
            flash(msg, "success")
    return render_template("probation/import.html", active="prob_import", report=report)


_HDR_TOKENS = ("employee", "code", "كود", "الاسم", "name", "department",
               "الادارة", "designation", "الوظيفة")


def _c(x):
    return "" if x is None else str(x).strip()


def _rows_from_grid(grid):
    """Find the header row (first row within the first 12 with >=3 non-empty cells
    that carries a known header token — tolerates a title/logo row above it), then
    return the data rows below as {header: value} dicts."""
    if not grid:
        return []
    hdr = None
    for i, r in enumerate(grid[:12]):
        nonempty = [_c(x) for x in r if _c(x)]
        if len(nonempty) >= 3 and any(any(t in c.lower() for t in _HDR_TOKENS) for c in nonempty):
            hdr = i
            break
    if hdr is None:
        return []
    headers = [_c(h) for h in grid[hdr]]
    out = []
    for r in grid[hdr + 1:]:
        if not any(x is not None and str(x).strip() for x in r):
            continue
        out.append({headers[i]: (r[i] if i < len(r) else None)
                    for i in range(len(headers)) if headers[i]})
    return out


def _parse_upload(fs):
    """Accept the official probation workbook in its real shape: multiple sheets
    (Arabic 'T&C' + 'English version'), a header row that is not necessarily the
    first row, and Arabic OR English headers. Reads EVERY sheet; the code-keyed
    upsert in import_rows dedupes any overlap between sheets."""
    if not fs or not fs.filename:
        return None
    name = fs.filename.lower()
    try:
        if name.endswith(".csv"):
            import csv, io
            text = fs.read().decode("utf-8-sig", errors="replace")
            return _rows_from_grid([tuple(r) for r in csv.reader(io.StringIO(text))])
        if name.endswith((".xlsx", ".xlsm")):
            from openpyxl import load_workbook
            wb = load_workbook(fs, read_only=True, data_only=True)
            rows = []
            for ws in wb.worksheets:
                rows += _rows_from_grid(list(ws.iter_rows(values_only=True)))
            return rows
    except Exception:  # noqa: BLE001
        return None
    return None


# ==========================================================================
# Settings
# ==========================================================================
@bp.route("/settings", methods=["GET", "POST"])
@login_required
@permission_required("prob_admin")
def settings():
    if request.method == "POST":
        svc.save_settings(request.form, _u())
        flash("Settings saved.", "success")
        return redirect(url_for("probation.settings"))
    return render_template("probation/settings.html", active="prob_settings",
                           settings=svc.all_settings(), criteria=CRITERIA, CATEGORIES=CATEGORIES)
