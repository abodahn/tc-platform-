"""
Order + Time & Action routes (native platform blueprint at /orders).
The delivery spine: customer orders + a derived critical-path calendar with
slippage alerts on the platform bell. Reuses view_dashboard / manage_production perms.
"""
import time

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.orders import services as svc
from app.orders.constants import ORDER_STATUS

bp = Blueprint("orders", __name__, url_prefix="/orders")

_SWEEP_AT = [0.0]


def _u():
    return current_user()


def _sweep():
    now = time.time()
    if now - _SWEEP_AT[0] > 300:
        _SWEEP_AT[0] = now
        svc.tna_sweep()


@bp.route("/")
@login_required
@permission_required("view_dashboard")
def index():
    _sweep()
    return render_template("orders/dashboard.html", active="orders", d=svc.dashboard())


@bp.route("/list")
@login_required
@permission_required("view_dashboard")
def listing():
    status = request.args.get("status") or None
    return render_template("orders/list.html", active="orders",
                           rows=svc.list_orders(status), statuses=ORDER_STATUS, f_status=status)


@bp.route("/tna")
@login_required
@permission_required("view_dashboard")
def tna():
    _sweep()
    return render_template("orders/tna.html", active="orders_tna", rows=svc.tna_board())


@bp.route("/new")
@login_required
@permission_required("manage_production")
def new():
    return render_template("orders/order_form.html", active="orders", statuses=ORDER_STATUS)


@bp.route("/", methods=["POST"])
@login_required
@permission_required("manage_production")
def create():
    if not (request.form.get("buyer") or "").strip():
        flash("Buyer is required.", "error")
        return redirect(url_for("orders.new"))
    oid = svc.create_order(request.form, _u())
    flash("Order created — critical path generated.", "success")
    return redirect(url_for("orders.detail", order_id=oid))


@bp.route("/<int:order_id>")
@login_required
@permission_required("view_dashboard")
def detail(order_id):
    bundle = svc.get_order(order_id)
    if not bundle:
        abort(404)
    return render_template("orders/order_detail.html", active="orders", **bundle, statuses=ORDER_STATUS)


@bp.route("/<int:order_id>/update", methods=["POST"])
@login_required
@permission_required("manage_production")
def update(order_id):
    svc.update_order(order_id, request.form, _u())
    flash("Order updated.", "success")
    return redirect(url_for("orders.detail", order_id=order_id))


@bp.route("/milestone/<int:milestone_id>", methods=["POST"])
@login_required
@permission_required("manage_production")
def milestone(milestone_id):
    svc.set_milestone(milestone_id, request.form.get("action") or "done",
                      request.form.get("actual_date"), _u())
    return redirect(request.referrer or url_for("orders.index"))
