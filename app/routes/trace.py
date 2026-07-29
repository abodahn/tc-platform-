"""
Traceability, ESG & Digital Product Passport routes (blueprint at /trace).
Supply-chain tiers, material chain of custody, MATERIAL certificates and the
per-order DPP — reusing platform auth, RBAC, CSRF, i18n, base template and bell.
"""
import time

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.trace import services as svc
from app.trace.constants import (TIERS, TIER_KEYS, PARTNER_ROLES, PARTNER_STATUS,
                                 MATERIAL_STANDARDS, DPP_MIN_PCT)

bp = Blueprint("trace", __name__, url_prefix="/trace")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s


def _u():
    return current_user()


def _sweep_throttled():
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.sweep()


@bp.route("/")
@login_required
@permission_required("trc_view")
def index():
    _sweep_throttled()
    return render_template("trace/dashboard.html", active="trace", d=svc.dashboard(),
                           tiers=TIERS, tier_keys=TIER_KEYS, min_pct=DPP_MIN_PCT)


# --- partners -------------------------------------------------------------
@bp.route("/partners")
@login_required
@permission_required("trc_view")
def partners():
    tier = request.args.get("tier") or None
    return render_template("trace/partners.html", active="trace", rows=svc.list_partners(tier),
                           tiers=TIERS, tier_keys=TIER_KEYS, roles=PARTNER_ROLES,
                           statuses=PARTNER_STATUS, f_tier=tier)


@bp.route("/partners", methods=["POST"])
@login_required
@permission_required("trc_manage")
def partner_create():
    if not (request.form.get("name") or "").strip():
        flash("Partner name is required.", "error")
    else:
        svc.create_partner(request.form, _u())
        flash("Supply-chain partner added.", "success")
    return redirect(url_for("trace.partners"))


# --- material lots --------------------------------------------------------
@bp.route("/lots")
@login_required
@permission_required("trc_view")
def lots():
    return render_template("trace/lots.html", active="trace", rows=svc.list_lots(),
                           partners=svc.list_partners(), tiers=TIERS)


@bp.route("/lots", methods=["POST"])
@login_required
@permission_required("trc_manage")
def lot_create():
    if not (request.form.get("material") or "").strip():
        flash("Material is required.", "error")
        return redirect(url_for("trace.lots"))
    lid = svc.create_lot(request.form, _u())
    if not lid:
        flash("That supplier or upstream lot no longer exists — refresh and pick again.", "error")
        return redirect(url_for("trace.lots"))
    flash("Material lot recorded.", "success")
    return redirect(url_for("trace.lot_detail", lot_id=lid))


@bp.route("/lots/<int:lot_id>")
@login_required
@permission_required("trc_view")
def lot_detail(lot_id):
    bundle = svc.get_lot(lot_id)
    if not bundle:
        abort(404)
    return render_template("trace/lot_detail.html", active="trace", tiers=TIERS, **bundle)


# --- material certificates ------------------------------------------------
@bp.route("/certs")
@login_required
@permission_required("trc_view")
def certs():
    standard = request.args.get("standard") or None
    return render_template("trace/certs.html", active="trace", rows=svc.list_certs(standard),
                           standards=MATERIAL_STANDARDS, partners=svc.list_partners(),
                           lots=svc.list_lots(), f_standard=standard)


@bp.route("/certs", methods=["POST"])
@login_required
@permission_required("trc_manage")
def cert_create():
    if not (request.form.get("standard") or "").strip():
        flash("Standard is required.", "error")
    elif svc.create_cert(request.form, _u()):
        flash("Material certificate added.", "success")
    else:
        flash("That partner or lot no longer exists — refresh and pick again.", "error")
    return redirect(url_for("trace.certs"))


@bp.route("/certs/<int:cert_id>/renew", methods=["POST"])
@login_required
@permission_required("trc_manage")
def cert_renew(cert_id):
    if svc.renew_cert(cert_id, request.form.get("valid_until"), _u()):
        flash("Certificate renewed.", "success")
    else:
        # blank/invalid date, unknown certificate, or a revoked one (which only a
        # human decision may reinstate — a renewal must not do it silently).
        flash("Enter a valid new expiry date. A revoked certificate cannot be renewed.", "error")
    return redirect(url_for("trace.certs"))


# --- product passports ----------------------------------------------------
@bp.route("/passports")
@login_required
@permission_required("trc_view")
def passports():
    return render_template("trace/passports.html", active="trc_passports",
                           rows=svc.passport_list(), min_pct=DPP_MIN_PCT)


@bp.route("/passport/<int:order_id>")
@login_required
@permission_required("trc_view")
def passport(order_id):
    bundle = svc.passport(order_id)
    if not bundle:
        abort(404)
    return render_template("trace/passport.html", active="trc_passports", tiers=TIERS,
                           all_lots=svc.list_lots(), min_pct=DPP_MIN_PCT, **bundle)


@bp.route("/passport/<int:order_id>", methods=["POST"])
@login_required
@permission_required("trc_manage")
def passport_save(order_id):
    if not svc.save_passport(order_id, request.form, _u()):
        abort(404)
    flash("Product passport updated.", "success")
    return redirect(url_for("trace.passport", order_id=order_id))


@bp.route("/passport/<int:order_id>/lot", methods=["POST"])
@login_required
@permission_required("trc_manage")
def passport_link_lot(order_id):
    lot_id = request.form.get("lot_id")
    # isdecimal, not isdigit: int('²') raises ValueError even though '²'.isdigit()
    # is True, and this guard is what stops that reaching int() below.
    if not str(lot_id or "").strip().isdecimal():
        flash("Pick a material lot.", "error")
    elif svc.link_order_lot(order_id, int(lot_id), request.form.get("qty_used")):
        flash("Material lot linked to the order.", "success")
    else:
        flash("That lot is already linked to this order, or no longer exists.", "warning")
    return redirect(url_for("trace.passport", order_id=order_id))


@bp.route("/passport/<int:order_id>/lot/<int:link_id>/remove", methods=["POST"])
@login_required
@permission_required("trc_manage")
def passport_unlink_lot(order_id, link_id):
    # order_id is passed through so a link belonging to a DIFFERENT order cannot
    # be deleted by editing the id in the URL.
    if not svc.unlink_order_lot(order_id, link_id):
        abort(404)
    flash("Material lot unlinked.", "success")
    return redirect(url_for("trace.passport", order_id=order_id))


@bp.route("/passport/<int:order_id>/esg", methods=["POST"])
@login_required
@permission_required("trc_manage")
def passport_esg(order_id):
    data = request.form.to_dict()
    data["order_id"] = order_id
    if not svc.record_esg(data, _u()):
        abort(404)
    flash("ESG record saved (recorded data, not a certified footprint).", "success")
    return redirect(url_for("trace.passport", order_id=order_id))


# --- ESG register ---------------------------------------------------------
@bp.route("/esg")
@login_required
@permission_required("trc_view")
def esg():
    return render_template("trace/esg.html", active="trace", rows=svc.list_esg())


@bp.route("/esg", methods=["POST"])
@login_required
@permission_required("trc_manage")
def esg_create():
    if svc.record_esg(request.form, _u()):
        flash("ESG record saved (recorded data, not a certified footprint).", "success")
    else:
        flash("That order no longer exists — the ESG record was not saved.", "error")
    return redirect(url_for("trace.esg"))


# --- dataset export -------------------------------------------------------
# Read-only views of what the pages already show, so trc_view is the right gate:
# anyone who may read the register may download it.
@bp.route("/export/<key>.csv")
@login_required
@permission_required("trc_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "trace", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("trc_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "trace", "json")
    if resp is None:
        abort(404)
    return resp


# Report declarations for the shared engine (app/services/reporting.py) — see the
# note in app/routes/warehouse.py. Plain dicts, no database work at import.
from app.trace import reports as _reports  # noqa: E402,F401
