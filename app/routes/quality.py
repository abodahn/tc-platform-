"""
Digital QMS routes (native platform blueprint at /quality).
AQL sampling decisions, defect capture, and the DHU / RFT / Pareto roll-ups —
reusing platform auth, RBAC, CSRF, i18n, base template and the bell.
"""
import time

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.quality import services as svc
from app.quality.constants import (AQL_LEVELS, DEFAULT_AQL, DEFECT_SECTIONS, DEFECT_SEVERITY,
                                   DEFECT_TYPES, DHU_ACTION_LIMIT, QC_STAGES, VERDICTS)

bp = Blueprint("quality", __name__, url_prefix="/quality")

_SWEEP_AT = [0.0]


def _u():
    return current_user()


def _sweep():
    now = time.time()
    if now - _SWEEP_AT[0] > 300:
        _SWEEP_AT[0] = now
        svc.dhu_sweep()


@bp.route("/")
@login_required
@permission_required("qc_view")
def index():
    _sweep()
    return render_template("quality/dashboard.html", active="quality", d=svc.dashboard(),
                           by_order=svc.by_order(), limit=DHU_ACTION_LIMIT)


@bp.route("/inspections")
@login_required
@permission_required("qc_view")
def inspections():
    order_id = request.args.get("order_id") or ""
    order_id = order_id if order_id.isdigit() else None
    stage = request.args.get("stage") or None
    vrd = request.args.get("verdict") or None
    return render_template(
        "quality/inspections.html", active="quality_inspections",
        rows=svc.list_inspections(order_id, stage, vrd), orders=svc.order_options(),
        stages=QC_STAGES, verdicts=VERDICTS,
        f_order=order_id, f_stage=stage, f_verdict=vrd)


@bp.route("/inspections/new")
@login_required
@permission_required("qc_inspect")
def new():
    return render_template("quality/inspection_form.html", active="quality_inspections",
                           orders=svc.order_options(), stages=QC_STAGES, aqls=AQL_LEVELS,
                           default_aql=DEFAULT_AQL)


@bp.route("/inspections", methods=["POST"])
@login_required
@permission_required("qc_inspect")
def create():
    try:
        lot = float(request.form.get("lot_size") or 0)
        aql = float(request.form.get("aql") or DEFAULT_AQL)
    except ValueError:
        flash("Lot size and AQL must be numbers.", "error")
        return redirect(url_for("quality.new"))
    # `lot < 1` alone lets NaN and inf straight through — both are False against <.
    if not (lot >= 1) or lot == float("inf") or aql not in AQL_LEVELS:
        flash("Enter a lot size of at least 1 and a supported AQL.", "error")
        return redirect(url_for("quality.new"))
    try:
        iid = svc.create_inspection(request.form, _u())
    except ValueError as e:
        flash({"bad_number": "Units and defectives must be numbers.",
               "negative": "Units and defectives cannot be negative.",
               "defectives_exceed_units": "Defective units cannot exceed units inspected.",
               "bad_stage": "Choose an inspection stage from the list."}
              .get(str(e), "Could not create the inspection."), "error")
        return redirect(url_for("quality.new"))
    flash("Inspection created — sampling plan applied.", "success")
    return redirect(url_for("quality.detail", inspection_id=iid))


@bp.route("/inspections/<int:inspection_id>")
@login_required
@permission_required("qc_view")
def detail(inspection_id):
    bundle = svc.get_inspection(inspection_id)
    if not bundle:
        abort(404)
    return render_template("quality/inspection_detail.html", active="quality_inspections",
                           **bundle, sections=DEFECT_SECTIONS, severities=DEFECT_SEVERITY,
                           defect_types=DEFECT_TYPES)


@bp.route("/inspections/<int:inspection_id>/result", methods=["POST"])
@login_required
@permission_required("qc_inspect")
def result(inspection_id):
    ok, msg = svc.record_result(inspection_id, request.form.get("units_inspected"),
                                request.form.get("defective_units"), _u(),
                                request.form.get("notes"))
    if not ok:
        flash({"bad_number": "Units and defectives must be numbers.",
               "negative": "Units and defectives cannot be negative.",
               "defectives_exceed_units": "Defective units cannot exceed units inspected.",
               "not_found": "Inspection not found."}.get(msg, "Could not save the result."), "error")
    else:
        flash(f"Result saved — AQL verdict: {msg}.", "error" if msg == "fail" else "success")
    return redirect(url_for("quality.detail", inspection_id=inspection_id))


@bp.route("/export/<key>.csv")
@login_required
@permission_required("qc_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "quality", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("qc_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "quality", "json")
    if resp is None:
        abort(404)
    return resp


@bp.route("/inspections/<int:inspection_id>/defect", methods=["POST"])
@login_required
@permission_required("qc_inspect")
def defect(inspection_id):
    ok, msg = svc.add_defect(inspection_id, request.form, _u())
    if not ok:
        flash({"bad_qty": "Defect quantity must be greater than zero.",
               "no_type": "Choose a defect type from the list.",
               "bad_section": "Choose a section from the list.",
               "bad_severity": "Choose a severity from the list.",
               "not_found": "Inspection not found."}.get(msg, "Could not add the defect."), "error")
    else:
        flash("Defect recorded.", "success")
    return redirect(url_for("quality.detail", inspection_id=inspection_id))
