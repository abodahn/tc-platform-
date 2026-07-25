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
    return render_template("orders/order_detail.html", active="orders", **bundle,
                           statuses=ORDER_STATUS, x=_order_360(order_id, bundle["order"]))


def _order_360(order_id, order):
    """One cross-module snapshot of an order: margin, material, cut, quality, WIP,
    shipment, traceability.

    The order page used to show only its own milestones, so a planner had to open six
    modules to answer "how is this order actually doing". Every block is imported
    defensively and independently — a module that is absent, or whose query fails,
    simply drops out of the panel instead of 500-ing the order page."""
    x = {}

    def block(name, fn):
        try:
            v = fn()
            if v:
                x[name] = v
        except Exception:
            pass          # module absent or its query failed — omit the block, never break

    def _cost():
        from app.costing import services as cs
        s = cs.cost_sheet(order_id) or {}
        m, est, act = s.get("margin") or {}, s.get("estimate") or {}, s.get("actual") or {}
        # Report the ESTIMATED margin: until actuals land, act_pct reads 100% because
        # actual cost is still 0, which would look like a triumph instead of "no data".
        return {"margin_pct": m.get("est_pct"), "margin_value": m.get("est_value"),
                "est_total": est.get("total"), "act_total": act.get("total"),
                "has_actual": bool(s.get("has_actual")),
                "variance": (round(act.get("total", 0) - est.get("total", 0), 2)
                             if s.get("has_actual") else None)}
    block("costing", _cost)

    def _cut():
        from app.cutroom import services as cu
        k = cu.order_summary(order_id) or {}
        # scalars only — order_summary also carries the whole order row and every lay
        return {f: k.get(f) for f in ("pieces_cut", "cut_pct", "marker_eff_pct",
                                      "utilisation_pct", "waste_pct", "variance_pct",
                                      "fabric_used_m", "lay_count", "balance")}
    block("cut", _cut)

    def _qc():
        from app.quality import services as q
        rows = q.list_inspections(order_id=order_id) or []
        return {"total": len(rows),
                "failed": sum(1 for r in rows if str(r.get("verdict")) == "fail"),
                "passed": sum(1 for r in rows if str(r.get("verdict")) == "pass")}
    block("qc", _qc)

    def _mat():
        from app.warehouse import services as wh
        return wh.issued_for_order(order_id)
    block("material", _mat)

    def _plan():
        from app.planning import services as pl
        allocs = pl.list_allocations(order_id=order_id) or []
        return {"allocations": len(allocs), "rows": allocs[:4]} if allocs else None
    block("planning", _plan)

    def _ship():
        from app.shipping import services as sh
        rows = sh.reconciliation(order_id=order_id) or []      # a LIST, one row per order
        if not rows:
            return None
        r = rows[0]
        return {f: r[f] for f in ("packed", "shipped", "balance", "fulfil_pct",
                                  "short", "over", "shipments") if f in r.keys()}
    block("shipping", _ship)

    def _trace():
        from app.trace import services as tr
        p = tr.passport(order_id) or {}
        comp = p.get("completeness") or {}          # {'pct','present','total','points'}
        pct = comp.get("pct") if isinstance(comp, dict) else comp
        if pct is None:
            return None
        return {"completeness": pct, "present": comp.get("present") if isinstance(comp, dict) else None,
                "total": comp.get("total") if isinstance(comp, dict) else None}
    block("trace", _trace)

    return x


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
