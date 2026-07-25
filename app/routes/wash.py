"""
Wash / finishing recipe library routes (native platform blueprint at /wash).
Versioned recipes with steps + chemical dosing, batch traceability back to the
exact version that ran, lab-dip shade sign-off and an internal impact indicator.
"""
from urllib.parse import urlparse

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.auth import login_required, permission_required, current_user
from app.wash import services as svc
from app.wash.constants import (WASH_TYPES, OPERATIONS, VERSION_STATUS,
                                LABDIP_VERDICT, BATCH_STATUS, MACHINES)

bp = Blueprint("wash", __name__, url_prefix="/wash")

# Every id in a path below is <int(max=2147483647):...>, never a bare <int:>.
# Flask's bare converter accepts an UNBOUNDED integer, and a row id past 2^31-1
# cannot exist in an INTEGER column — binding one raised OverflowError on SQLite /
# "integer out of range" on PostgreSQL, i.e. a 500 instead of a 404. Bounding the
# converter makes routing reject it, so no service ever sees the value.


def _u():
    return current_user()


# Every service reason code, so an operator never sees a raw identifier like
# "batch_version_mismatch" flashed at them.
_MSG = {"negative_value": "Temperatures, times, loads and doses cannot be negative.",
        "value_out_of_range": "That value is far too large to be a real measurement.",
        "version_frozen": "This version is frozen — create a new version to change it.",
        "operation_required": "Pick an operation.",
        "bad_operation": "That is not a known wash operation.",
        "bad_status": "That is not a valid version status.",
        "bad_verdict": "That is not a valid shade verdict.",
        "no_steps": "This version has no steps yet — add the cycle before approving it.",
        "cannot_reopen": "This version is frozen — batches record it. Create a new version instead.",
        "labdip_not_approved": "This version has no approved lab dip — bulk approval is blocked.",
        "recipe_not_found": "That recipe no longer exists.",
        "version_not_found": "That recipe version no longer exists.",
        "step_not_found": "That step no longer exists.",
        "batch_not_found": "That batch no longer exists.",
        "labdip_not_found": "That lab dip no longer exists.",
        "batch_version_mismatch": "That batch ran a different version — its swatch is evidence "
                                  "for that version, not this one.",
        "source_not_found": "That version does not belong to this recipe.",
        "name_required": "The chemical needs a name."}


def _why(err):
    return _MSG.get(err, err or "")


def _back(default):
    """Return to the page the form was posted from — but Referer is a header the
    CLIENT controls, so a bare redirect(request.referrer) forwards the operator to
    any site that names itself there. Off-site referrers fall back to `default`."""
    ref = request.referrer or ""
    return ref if ref and urlparse(ref).netloc in ("", urlparse(request.host_url).netloc) \
        else default


def _orders():
    """Orders for the optional order link. The orders module may not be present."""
    try:
        from app.orders import services as ord_svc
        return ord_svc.list_orders()
    except Exception:
        return []


@bp.route("/")
@login_required
@permission_required("wsh_view")
def index():
    return render_template("wash/dashboard.html", active="wash", d=svc.dashboard())


@bp.route("/recipes")
@login_required
@permission_required("wsh_view")
def recipes():
    wt = request.args.get("wash_type") or None
    st = request.args.get("status") or None
    return render_template("wash/recipes.html", active="wash_recipes",
                           rows=svc.list_recipes(wt, st), wash_types=WASH_TYPES,
                           statuses=VERSION_STATUS, f_type=wt, f_status=st,
                           orders=_orders())


@bp.route("/recipes", methods=["POST"])
@login_required
@permission_required("wsh_manage")
def recipe_create():
    if not (request.form.get("name") or "").strip():
        flash("Recipe name is required.", "error")
        return redirect(url_for("wash.recipes"))
    rid = svc.create_recipe(request.form, _u())
    if not rid:
        flash("That recipe code is already used — pick another or leave it blank.", "error")
        return redirect(url_for("wash.recipes"))
    flash("Recipe created — draft v1 is ready for its steps.", "success")
    return redirect(url_for("wash.recipe_detail", recipe_id=rid))


@bp.route("/recipes/<int(max=2147483647):recipe_id>")
@login_required
@permission_required("wsh_view")
def recipe_detail(recipe_id):
    bundle = svc.get_recipe(recipe_id, request.args.get("v"))
    if not bundle:
        abort(404)
    return render_template("wash/recipe_detail.html", active="wash_recipes", **bundle,
                           operations=OPERATIONS, statuses=VERSION_STATUS,
                           verdicts=LABDIP_VERDICT)


@bp.route("/recipes/<int(max=2147483647):recipe_id>/version", methods=["POST"])
@login_required
@permission_required("wsh_manage")
def version_new(recipe_id):
    vid, err = svc.new_version(recipe_id, request.form.get("source_version_id"), _u())
    if err:
        flash("Could not create the version: " + _why(err), "error")
        return redirect(url_for("wash.recipe_detail", recipe_id=recipe_id))
    flash("New draft version created — the previous version is untouched.", "success")
    return redirect(url_for("wash.recipe_detail", recipe_id=recipe_id, v=vid))


@bp.route("/versions/<int(max=2147483647):version_id>/step", methods=["POST"])
@login_required
@permission_required("wsh_manage")
def step_add(version_id):
    ok, err = svc.add_step(version_id, request.form, _u())
    flash("Step added." if ok else "Could not add the step: " + _why(err),
          "success" if ok else "error")
    return redirect(_back(url_for("wash.recipes")))


@bp.route("/chemical", methods=["POST"])
@login_required
@permission_required("wsh_manage")
def chemical_add():
    # step_id comes from the form's step picker, so one plain form serves every step.
    ok, err = svc.add_chemical(request.form.get("step_id"), request.form, _u())
    flash("Chemical added." if ok else "Could not add the chemical: " + _why(err),
          "success" if ok else "error")
    return redirect(_back(url_for("wash.recipes")))


@bp.route("/versions/<int(max=2147483647):version_id>/status", methods=["POST"])
@login_required
@permission_required("wsh_approve")
def version_status(version_id):
    ok, err = svc.set_version_status(version_id, request.form.get("status") or "", _u())
    if ok:
        flash("Version status updated.", "success")
    elif (err or "").startswith("already_"):
        # A double-click or a refresh-repost is deliberately a no-op, not an error.
        flash("That status is already recorded.", "info")
    else:
        flash("Could not change the status: " + _why(err), "error")
    return redirect(_back(url_for("wash.recipes")))


@bp.route("/batches")
@login_required
@permission_required("wsh_view")
def batches():
    return render_template("wash/batches.html", active="wash_batches",
                           rows=svc.list_batches(), versions=svc.list_versions(),
                           machines=MACHINES, batch_statuses=BATCH_STATUS, orders=_orders())


@bp.route("/batches", methods=["POST"])
@login_required
@permission_required("wsh_manage")
def batch_create():
    bid, err = svc.create_batch(request.form, _u())
    if err:
        flash("Could not record the batch: " + _why(err), "error")
    else:
        flash("Batch recorded against the recipe version.", "success")
    return redirect(url_for("wash.batches"))


@bp.route("/labdips")
@login_required
@permission_required("wsh_view")
def labdips():
    v = request.args.get("verdict") or None
    return render_template("wash/labdips.html", active="wash_labdips",
                           rows=svc.list_labdips(v), versions=svc.list_versions(),
                           batches=svc.list_batches(50), verdicts=LABDIP_VERDICT, f_verdict=v)


@bp.route("/labdips", methods=["POST"])
@login_required
@permission_required("wsh_manage")
def labdip_create():
    _, err = svc.create_labdip(request.form, _u())
    flash("Lab dip logged." if not err else "Could not log the lab dip: " + _why(err),
          "error" if err else "success")
    return redirect(url_for("wash.labdips"))


@bp.route("/labdips/<int(max=2147483647):dip_id>/verdict", methods=["POST"])
@login_required
@permission_required("wsh_approve")
def labdip_verdict(dip_id):
    ok, err = svc.set_labdip_verdict(dip_id, request.form.get("verdict") or "",
                                     request.form.get("notes"), _u())
    if ok:
        flash("Shade verdict recorded.", "success")
    elif (err or "").startswith("already_"):
        # Re-saving the same verdict is deliberately a no-op, not an error worth red.
        flash("That verdict is already recorded.", "info")
    else:
        flash("Could not record the verdict: " + _why(err), "error")
    return redirect(_back(url_for("wash.labdips")))


# --- exports: same keys, two wire formats (Excel download / machine-readable) ---
@bp.route("/export/<key>.csv")
@login_required
@permission_required("wsh_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "wash", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("wsh_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "wash", "json")
    if resp is None:
        abort(404)
    return resp
