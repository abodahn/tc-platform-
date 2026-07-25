"""
PLM-lite routes (native platform blueprint at /plm).
Style master, versioned tech packs, style BOM and buyer sample rounds — reusing
platform auth, RBAC, CSRF, i18n, base template and the notification bell.
"""
from urllib.parse import urlparse

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.plm import services as svc
from app.plm.constants import (STYLE_STATUS, SAMPLE_STAGES, SAMPLE_VERDICT,
                               BOM_KINDS, BOM_UNITS, PRODUCT_CATEGORIES)

bp = Blueprint("plm", __name__, url_prefix="/plm")


def _u():
    return current_user()


def _back(style_id):
    return redirect(url_for("plm.style_detail", style_id=style_id))


def _pk(v):
    """Flask's <int:> converter accepts an unbounded integer, but SQLite cannot
    bind one above 2**63-1 — /plm/styles/99999999999999999999 raised OverflowError
    and returned 500 instead of 404. Out of range simply cannot exist, so map it
    to 0 and let the normal not-found path handle it."""
    return v if 0 < v <= 9223372036854775807 else 0


def _referrer_or(default):
    """Return to the page the form was posted from — but Referer is a header the
    client controls, so a bare redirect(request.referrer) sends the user to any
    site that names itself there. Off-site referrers fall back to `default`."""
    ref = request.referrer or ""
    return ref if ref and urlparse(ref).netloc in ("", urlparse(request.host_url).netloc) else default


@bp.route("/")
@login_required
@permission_required("plm_view")
def index():
    return render_template("plm/dashboard.html", active="plm", d=svc.dashboard())


@bp.route("/styles")
@login_required
@permission_required("plm_view")
def styles():
    status = request.args.get("status") or None
    buyer = request.args.get("buyer") or None
    q = (request.args.get("q") or "").strip() or None
    return render_template("plm/styles.html", active="plm_styles",
                           rows=svc.list_styles(status, buyer, q), statuses=STYLE_STATUS,
                           buyer_list=svc.buyers(), f_status=status, f_buyer=buyer, f_q=q)


@bp.route("/styles/new")
@login_required
@permission_required("plm_manage")
def style_new():
    return render_template("plm/style_form.html", active="plm_styles",
                           categories=PRODUCT_CATEGORIES)


@bp.route("/styles", methods=["POST"])
@login_required
@permission_required("plm_manage")
def style_create():
    sid, err = svc.create_style(request.form, _u())
    if err:
        flash("plm.msg." + err, "error")
        return redirect(url_for("plm.style_new"))
    flash("Style created.", "success")
    return _back(sid)


@bp.route("/styles/<int:style_id>")
@login_required
@permission_required("plm_view")
def style_detail(style_id):
    bundle = svc.get_style(_pk(style_id), request.args.get("v"))
    if not bundle:
        abort(404)
    return render_template("plm/style_detail.html", active="plm_styles", **bundle,
                           statuses=STYLE_STATUS, stages=SAMPLE_STAGES, verdicts=SAMPLE_VERDICT,
                           kinds=BOM_KINDS, units=BOM_UNITS)


@bp.route("/styles/<int:style_id>/update", methods=["POST"])
@login_required
@permission_required("plm_manage")
def style_update(style_id):
    # The form always posts every editable field, so False here means the style
    # itself is gone — never report a save that did not happen.
    if not svc.update_style(_pk(style_id), request.form, _u()):
        abort(404)
    flash("Style updated.", "success")
    return _back(style_id)


@bp.route("/styles/<int:style_id>/status", methods=["POST"])
@login_required
@permission_required("plm_approve")
def style_status(style_id):
    ok, msg = svc.set_style_status(_pk(style_id), request.form.get("status") or "", _u())
    flash("plm.msg." + msg if not ok else "Style status updated.", "error" if not ok else "success")
    return _back(style_id)


@bp.route("/styles/<int:style_id>/techpack", methods=["POST"])
@login_required
@permission_required("plm_manage")
def techpack_publish(style_id):
    tid, err = svc.publish_version(_pk(style_id), request.form.get("change_note"), _u())
    if err:
        flash("plm.msg." + err, "error")
    else:
        flash("New tech-pack version published.", "success")
    return _back(style_id)


@bp.route("/techpack/<int:techpack_id>")
@login_required
@permission_required("plm_view")
def techpack(techpack_id):
    bundle = svc.get_techpack(_pk(techpack_id))
    if not bundle:
        abort(404)
    return render_template("plm/techpack.html", active="plm_styles", **bundle)


@bp.route("/techpack/<int:techpack_id>/section", methods=["POST"])
@login_required
@permission_required("plm_manage")
def section_add(techpack_id):
    ok, err = svc.add_section(_pk(techpack_id), request.form.get("title"), request.form.get("body"))
    flash("plm.msg." + err if not ok else "Section added.", "error" if not ok else "success")
    return redirect(_referrer_or(url_for("plm.techpack", techpack_id=techpack_id)))


@bp.route("/techpack/<int:techpack_id>/spec", methods=["POST"])
@login_required
@permission_required("plm_manage")
def spec_add(techpack_id):
    ok, err = svc.add_spec(_pk(techpack_id), request.form)
    flash("plm.msg." + err if not ok else "Measurement added.", "error" if not ok else "success")
    return redirect(_referrer_or(url_for("plm.techpack", techpack_id=techpack_id)))


@bp.route("/styles/<int:style_id>/bom", methods=["POST"])
@login_required
@permission_required("plm_manage")
def bom_add(style_id):
    ok, err = svc.add_bom_line(_pk(style_id), request.form, _u())
    flash("plm.msg." + err if not ok else "BOM line added.", "error" if not ok else "success")
    return _back(style_id)


@bp.route("/styles/<int:style_id>/sample", methods=["POST"])
@login_required
@permission_required("plm_manage")
def sample_add(style_id):
    ok, err = svc.record_sample(_pk(style_id), request.form, _u())
    flash("plm.msg." + err if not ok else "Sample round recorded.", "error" if not ok else "success")
    return _back(style_id)


@bp.route("/export/<key>.csv")
@login_required
@permission_required("plm_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "plm", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("plm_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "plm", "json")
    if resp is None:
        abort(404)
    return resp


@bp.route("/samples/<int:sample_id>/verdict", methods=["POST"])
@login_required
@permission_required("plm_manage")
def sample_verdict(sample_id):
    ok, err = svc.set_verdict(_pk(sample_id), request.form.get("verdict") or "",
                              request.form.get("comments"), _u())
    flash("plm.msg." + err if not ok else "Verdict recorded.", "error" if not ok else "success")
    return redirect(_referrer_or(url_for("plm.index")))
