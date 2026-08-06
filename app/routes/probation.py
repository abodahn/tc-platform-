"""
HR — Probation Management routes (native platform blueprint at /hr/probation).

Uses the platform auth, RBAC, CSRF, i18n, base template and audit/notification
systems. Every protected operation is guarded by a permission AND, where the
record is employee-scoped, by an object-level scope check (no IDOR).
"""
import re
from datetime import date as _date
from functools import lru_cache

from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   abort, send_file, Response)

from app.auth import login_required, permission_required, current_user, user_can
from app.tabular import ACCEPT, TableError, read_sheets
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
        fs = request.files.get("file")
        try:
            rows, sparse = _parse_upload(fs)
        except TableError as exc:
            flash(f"Could not read the file — {exc}", "error")
        else:
            report = svc.import_rows(rows, _u(), (fs.filename or "import"))
            if sparse:
                # Rows we deliberately did not import, folded into the tally so
                # they cannot vanish from BOTH the database and the count.
                report["total"] += len(sparse)
                report["skipped"] += len(sparse)
                report["details"] = list(report["details"]) + sparse
            msg = (f"Import done — created {report['created']}, updated {report['updated']}, "
                   f"skipped {report['skipped']}, errors {report['errors']}.")
            # Bridge the roster into live probation cases so the import "takes
            # effect" on the dashboard: a case for every rostered employee still
            # within their probation window (idempotent). The checkbox ships
            # CHECKED, so an ABSENT field means the user unchecked it — defaulting
            # to "1" here ignored that opt-out and opened the cases anyway.
            if request.form.get("open_cases", "") not in ("", "0", "off"):
                res = svc.bulk_open_cases(_u(), only_in_probation=True)
                report["cases_opened"] = res["opened"]
                report["cases_considered"] = res["considered"]
                if res["opened"]:
                    msg += f" Opened {res['opened']} probation case(s)."
            flash(msg, "success")
    return render_template("probation/import.html", active="prob_import", report=report,
                           accept=ACCEPT)


# Header aliases, taken from the SAME table import_rows maps with. A separate
# token list here is what broke the roster: it knew "الادارة" but not "الإدارة",
# and "الوظيفة" but not "المسمى الوظيفي", so an Arabic header row scored too low
# to be recognised and was imported as an employee whose code was the word
# "employee name" — while the real Arabic rows landed under the English sheet's
# column order. One vocabulary, no drift.
#
# Each alias is kept as its WORD LIST, because a header cell must NAME a column,
# not merely contain the letters of one. `alias in cell` made a department called
# "Barcode" name the employee-code column (one of the aliases is the bare word
# "code"), and with two more loose matches nearby the DATA ROW was promoted to
# header — every row under it was then remapped onto that row's own values as
# keys, with a green "0 errors" flash. Whole words only.
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)       # '_' separates too: employee_code


def _words(s):
    return _WORDS.findall(svc._norm_key(s))


_ALIASES = {f: [w for w in (_words(a) for a in al) if w]
            for f, al in svc._IMPORT_MAP.items()}
_DATE_FIELDS = ("joining_date", "probation_end_date", "evaluation_date")

# Naming the employee-code column is what promotes a row to header, so THAT
# match is held to a stricter rule than the rest: the alias must be essentially
# the whole cell — only these generic qualifiers may sit around it. Whole-word
# matching alone still let a department called "Code Room" name the code column.
# A column header is "Employee Code (ID)" or "الكود"; a department is "Code Room".
_CODE_QUALIFIERS = frozenset(
    "employee emp staff personnel worker id ident no num number "
    "الموظف موظف العامل رقم".split())


def _c(x):
    return "" if x is None else str(x).strip()


def _has_seq(words, alias):
    n = len(alias)
    return any(words[i:i + n] == alias for i in range(len(words) - n + 1))


def _names_code_column(w):
    for a in _ALIASES["employee_code"]:
        n = len(a)
        for i in range(len(w) - n + 1):
            if w[i:i + n] == a and all(x in _CODE_QUALIFIERS for x in w[:i] + w[i + n:]):
                return True
    return False


@lru_cache(maxsize=4096)
def _cell_fields(cell):
    """The roster fields this ONE cell names. Cached: the same department and
    job-title strings repeat on every row of a 5,000-row roster."""
    w = _words(cell)
    if not w:
        return frozenset()
    fields = {f for f, aliases in _ALIASES.items() if any(_has_seq(w, a) for a in aliases)}
    if "employee_code" in fields and not _names_code_column(w):
        fields.discard("employee_code")
    return frozenset(fields)


def _hdr_fields(cells):
    """The roster fields this row's cells NAME (not the values they carry)."""
    fields = set()
    for cell in cells:
        fields |= _cell_fields(cell)
    return fields


def _is_header(cells):
    """A header row names the employee-code column and at least two more.

    Naming the code column is the discriminator: a data row can easily contain
    header WORDS — "Employee Services" / "Department Head" is a real job title,
    "Barcode" and "Code Room" are real departments — but none of them NAMES a
    code column (see _names_code_column), so it can no longer hijack the mapping
    and silently remap every row beneath it onto its own values.
    """
    fields = _hdr_fields(cells)
    return "employee_code" in fields and len(fields) >= 3


def _rows_from_grid(grid, sheet=""):
    """Data rows below the header row of ONE sheet, as {header: value} dicts.
    Returns (rows, skipped_details).

    The header is not necessarily the first row — title/logo rows sit above it —
    so a row that is one switches the mapping for the rows beneath it. The
    caller must run this per sheet: the real file carries an Arabic 'T&C' sheet
    and an 'English version' sheet whose layouts differ, and reading sheet 2
    through sheet 1's column map is the exact corruption read_sheets exists to
    prevent.
    """
    headers, out, skipped = None, [], []
    for n, r in enumerate(grid, 1):
        cells = [_c(x) for x in r]
        nonempty = [c for c in cells if c]
        if not nonempty:
            continue                       # blank spacer row
        if _is_header(cells):
            headers = cells
            continue
        if headers is None:
            continue                       # title / logo rows above the header
        if len(nonempty) < 2:
            # One filled cell under a header is a footer or a note far more
            # often than a roster row — and import_rows' UPDATE writes EVERY
            # column, so importing it would blank a real employee's name and
            # dates. It stays out of the database, but it is no longer invisible
            # either: it used to vanish from the tally as well.
            skipped.append({"row": f"{sheet}{n}" if sheet else n, "status": "skipped",
                            "reason": f"only one filled cell — not imported: {nonempty[0][:60]}"})
            continue
        out.append({headers[i]: (r[i] if i < len(r) else None)
                    for i in range(len(headers)) if headers[i]})
    return out, skipped


# 07.07.2026 — what Turkish (and German) Excel writes, and the same machine that
# produces the cp1254 semicolon CSV this surface now accepts. svc._to_date tries
# neither dd.mm.yyyy nor mm.dd.yyyy, so joining_date and probation_end_date
# landed NULL while the flash said "created N, errors 0" in green — and
# bulk_open_cases filters on probation_end_date IS NOT NULL, so ZERO cases open
# and nobody reaches the dashboard. Two dots and a 4-digit year only: a version
# string like "1.2.3" must not become a date.
_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$")


def _iso(day, month, year):
    try:
        return _date(year, month, day).isoformat()
    except ValueError:
        return None


def _fix_dotted_dates(rows):
    """Rewrite dd.mm.yyyy date cells to ISO, in place, one column at a time.

    03.04.2026 is genuinely ambiguous — 3 April read dd.mm, 4 March read mm.dd —
    so the default is CHOSEN, not guessed: dd.mm, because this office runs
    Turkish and Arabic Windows. Where the file proves itself otherwise the file
    wins: a middle number above 12 can only be a day, so such a column is read
    mm.dd, and any single value carrying that proof outranks its column.
    Only columns whose header names a date field are touched.
    """
    keys = [k for k in dict.fromkeys(k for r in rows for k in r)
            if _cell_fields(_c(k)).intersection(_DATE_FIELDS)]
    for key in keys:
        seen = []
        for r in rows:
            m = _DOTTED.match(_c(r.get(key)))
            if m:
                seen.append((r, int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if not seen:
            continue
        day_first = any(a > 12 for _r, a, _b, _y in seen) or \
            not any(b > 12 for _r, _a, b, _y in seen)
        for r, a, b, y in seen:
            first = True if a > 12 else False if b > 12 else day_first
            iso = _iso(a, b, y) if first else _iso(b, a, y)
            if iso:                        # an impossible date is left as it was
                r[key] = iso
    return rows


def _parse_upload(fs):
    """Accept the official probation workbook in its real shape: any format
    app.tabular reads (CSV in any delimiter/encoding, .xls, .xlsx/.xlsm),
    multiple sheets (Arabic 'T&C' + 'English version'), a header row that is not
    necessarily the first row, and Arabic OR English headers. Returns
    (rows, skipped_details).

    EVERY sheet is read, each through ITS OWN header — the code-keyed upsert in
    import_rows dedupes the overlap between the two sheets."""
    if not fs or not fs.filename:
        raise TableError("no file was selected")
    sheets, _meta = read_sheets(fs)
    rows, skipped = [], []
    multi = len(sheets) > 1
    for name, grid in sheets:
        r, sk = _rows_from_grid(grid, f"{name}!" if (multi and name) else "")
        rows += r
        skipped += sk
    _fix_dotted_dates(rows)
    if not rows:
        # A readable file with nothing we recognise used to report
        # "created 0, updated 0, skipped 0" as a SUCCESS. Say what is missing.
        raise TableError("no employee rows were found. The sheet needs a header row "
                         "naming at least the employee code, the employee name and "
                         "one more column (Arabic or English headers both work)")
    return rows, skipped


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
