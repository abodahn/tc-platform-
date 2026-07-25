"""
Cut room & fabric utilisation routes (native platform blueprint at /cutroom).
Lays, marker efficiency, fabric utilisation and the cut-vs-order reconciliation —
reusing platform auth, RBAC, CSRF, i18n, base template and the notification bell.
"""
import time

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.cutroom import services as svc
from app.cutroom.constants import (LAY_STATUS, MARKER_EFF_GOOD, DEFAULT_END_ALLOW_M,
                                   FABRIC_VARIANCE_ALERT_PCT)

bp = Blueprint("cutroom", __name__, url_prefix="/cutroom")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s


def _u():
    return current_user()


def _sweep_throttled():
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.variance_sweep()


@bp.route("/")
@login_required
@permission_required("cut_view")
def index():
    _sweep_throttled()
    return render_template("cutroom/dashboard.html", active="cutroom", d=svc.dashboard(),
                           eff_good=MARKER_EFF_GOOD, var_alert=FABRIC_VARIANCE_ALERT_PCT)


@bp.route("/lays")
@login_required
@permission_required("cut_view")
def lays():
    order_id = request.args.get("order_id", type=int)
    status = request.args.get("status") or None
    return render_template("cutroom/lays.html", active="cut_lays",
                           rows=svc.list_lays(order_id, status), statuses=LAY_STATUS,
                           orders=svc.order_options(), f_order=order_id, f_status=status,
                           eff_good=MARKER_EFF_GOOD)


@bp.route("/lays/new")
@login_required
@permission_required("cut_manage")
def lay_new():
    return render_template("cutroom/lay_form.html", active="cut_lays", statuses=LAY_STATUS,
                           orders=svc.order_options(), end_allow=DEFAULT_END_ALLOW_M,
                           f_order=request.args.get("order_id", type=int))


@bp.route("/lays", methods=["POST"])
@login_required
@permission_required("cut_manage")
def lay_create():
    if not request.form.get("order_id", type=int):
        flash("Pick the order this lay is cut for.", "error")
        return redirect(url_for("cutroom.lay_new"))
    lid = svc.create_lay(request.form, _u())
    flash("Lay recorded.", "success")
    return redirect(url_for("cutroom.lay_detail", lay_id=lid))


@bp.route("/lays/<int:lay_id>")
@login_required
@permission_required("cut_view")
def lay_detail(lay_id):
    bundle = svc.get_lay(lay_id)
    if not bundle:
        abort(404)
    return render_template("cutroom/lay_detail.html", active="cut_lays", **bundle,
                           statuses=LAY_STATUS, eff_good=MARKER_EFF_GOOD,
                           wh_rolls=svc.warehouse_rolls(bundle["lay"].get("shade_lot")))


@bp.route("/lays/<int:lay_id>", methods=["POST"])
@login_required
@permission_required("cut_manage")
def lay_update(lay_id):
    svc.update_lay(lay_id, request.form)
    flash("Lay updated.", "success")
    return redirect(url_for("cutroom.lay_detail", lay_id=lay_id))


@bp.route("/lays/<int:lay_id>/rolls", methods=["POST"])
@login_required
@permission_required("cut_manage")
def lay_roll_add(lay_id):
    ok, msg = svc.add_lay_roll(lay_id, request.form)
    flash("Roll booked to the lay." if ok else "Enter the metres taken from the roll.",
          "success" if ok else "error")
    return redirect(url_for("cutroom.lay_detail", lay_id=lay_id))


@bp.route("/orders/<int:order_id>")
@login_required
@permission_required("cut_view")
def order(order_id):
    s = svc.order_summary(order_id)
    if not s:
        abort(404)
    return render_template("cutroom/order.html", active="cutroom", s=s,
                           eff_good=MARKER_EFF_GOOD, var_alert=FABRIC_VARIANCE_ALERT_PCT)
