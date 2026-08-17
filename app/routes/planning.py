"""
Finite-capacity production planning routes (blueprint at /planning).
Line capacity, SMV per order, line loading vs available minutes, feasibility
against the order ship date, what-if and line balancing.
"""
from urllib.parse import urlparse

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.planning import services as svc
from app.planning.constants import BOARD_DAYS, SECTIONS

bp = Blueprint("planning", __name__, url_prefix="/planning")


def _u():
    return current_user()


def _pk(v):
    """Flask's <int:> converter accepts an unbounded integer, but neither SQLite nor
    PostgreSQL can bind one above 2**63-1 — /planning/lines/99999999999999999999
    raised OverflowError and returned 500 instead of 404. Out of range cannot exist,
    so map it to 0 and let the normal not-found path handle it. (Same fix as plm.)"""
    return v if 0 < v <= 9223372036854775807 else 0


def _referrer_or(default):
    """Back to the page the form was posted from — but Referer is a client-controlled
    header, so a bare redirect(request.referrer) would forward the user to any site
    that names itself there. Off-site referrers fall back to `default`."""
    ref = request.referrer or ""
    return ref if ref and urlparse(ref).netloc in ("", urlparse(request.host_url).netloc) else default


@bp.route("/")
@login_required
@permission_required("pln_view")
def index():
    days = request.args.get("days") or BOARD_DAYS
    try:
        days = max(3, min(60, int(days)))
    except (TypeError, ValueError):
        days = BOARD_DAYS
    d = svc.dashboard(days)

    # The KPI tiles counted "5 orders late" and then gave the planner no way to
    # SEE those five. dashboard() already separates them, so each tile now filters
    # the order book below it.
    show = (request.args.get("show") or "all").strip()
    BUCKETS = {
        "late": lambda o: o["status"] == "late",
        "at_risk": lambda o: o["status"] == "at_risk",
        "unplanned": lambda o: o["status"] in ("unplanned", "no_capacity"),
        "behind": lambda o: o["vs"]["status"] == "behind",
        "smv": lambda o: o["smv_resolved"]["conflict"],
    }
    orders = d["orders"]
    if show in BUCKETS:
        orders = [o for o in orders if BUCKETS[show](o)]
    else:
        show = "all"

    q = (request.args.get("q") or "").strip()
    if q:
        n = q.lower()
        orders = [o for o in orders if n in " ".join(
            str(o.get(k) or "").lower() for k in ("order_no", "buyer", "style_name", "style_ref"))]

    # URGENCY FIRST, always. A planner opens this to find what will miss its ship
    # date; the previous order was whatever the query returned. Late before
    # at-risk before everything else, then the most days late, then the nearest
    # ship date — so the order that needs a decision today is the top row.
    RANK = {"late": 0, "no_capacity": 1, "at_risk": 2, "unplanned": 3,
            "on_time": 4, "no_ship_date": 5}
    sort = (request.args.get("sort") or "urgency").strip()
    if sort == "ship":
        orders = sorted(orders, key=lambda o: (o.get("ship_date") or "9999-99-99",))
    elif sort == "order":
        orders = sorted(orders, key=lambda o: (o.get("order_no") or ""))
    else:
        sort = "urgency"
        orders = sorted(orders, key=lambda o: (
            RANK.get(o["status"], 9),
            -(o.get("days_late") or 0),
            o.get("ship_date") or "9999-99-99"))

    return render_template("planning/index.html", active="planning", d=d,
                           orders=orders, show=show, sort=sort, q=q, days=days,
                           shown=len(orders), total=len(d["orders"]))


@bp.route("/lines")
@login_required
@permission_required("pln_view")
def lines():
    return render_template("planning/lines.html", active="pln_lines",
                           rows=svc.list_lines(), sections=SECTIONS)


@bp.route("/lines", methods=["POST"])
@login_required
@permission_required("pln_plan")
def line_create():
    if not (request.form.get("name") or "").strip():
        flash("Line name is required.", "error")
    else:
        svc.create_line(request.form, _u())
        flash("Line capacity added.", "success")
    return redirect(url_for("planning.lines"))


@bp.route("/lines/<int:pline_id>", methods=["POST"])
@login_required
@permission_required("pln_plan")
def line_update(pline_id):
    if not svc.update_line(_pk(pline_id), request.form, _u()):
        flash("Line not found, or nothing to change.", "error")
    else:
        flash("Line capacity updated.", "success")
    return redirect(url_for("planning.lines"))


@bp.route("/orders/<int:order_id>")
@login_required
@permission_required("pln_view")
def order_detail(order_id):
    f = svc.feasibility(_pk(order_id))
    if not f:
        abort(404)
    return render_template("planning/order.html", active="planning", f=f,
                           lines=svc.list_lines(active_only=True))


@bp.route("/orders/<int:order_id>/smv", methods=["POST"])
@login_required
@permission_required("pln_plan")
def smv_set(order_id):
    order_id = _pk(order_id)
    if not svc.order_exists(order_id):
        abort(404)
    ok, reason = svc.set_smv(order_id, request.form.get("smv"), request.form.get("notes"))
    flash("SMV updated." if ok else "SMV must be a number of 0 or more.",
          "success" if ok else "error")
    return redirect(url_for("planning.order_detail", order_id=order_id))


@bp.route("/allocate")
@login_required
@permission_required("pln_plan")
def allocate():
    return render_template("planning/allocate.html", active="planning",
                           orders=svc.list_orders(), lines=svc.list_lines(active_only=True),
                           wi=None)


@bp.route("/whatif", methods=["POST"])
@login_required
@permission_required("pln_view")
def whatif():
    """Non-persisting preview — 'what breaks if I move this?'."""
    wi = svc.what_if(request.form.get("order_id"), request.form.get("pline_id"),
                     request.form.get("start_date"), request.form.get("qty"),
                     request.form.get("smv"))
    return render_template("planning/allocate.html", active="planning",
                           orders=svc.list_orders(), lines=svc.list_lines(active_only=True),
                           wi=wi, form=request.form)


@bp.route("/allocations", methods=["POST"])
@login_required
@permission_required("pln_plan")
def allocation_create():
    ok, res = svc.create_allocation(request.form, _u())
    if ok:
        flash("Order loaded onto the line.", "success")
        # svc._i, not int(): the service accepts "12.5"/"" and commits, and a bare
        # int() would then 500 on the redirect AFTER the allocation was written.
        return redirect(url_for("planning.order_detail",
                                order_id=svc._i(request.form.get("order_id"))))
    flash({"bad_qty": "Quantity must be greater than zero.",
           "no_smv": "Set the order SMV before loading it onto a line.",
           "no_capacity": "That line has no capacity (check operators, minutes and efficiency).",
           "line_not_found": "Line not found.",
           "order_not_found": "Order not found.",
           "duplicate": "That exact allocation is already on the plan — nothing was added twice.",
           "unplannable": "This allocation needs more than a year on that line — "
                          "split the quantity or add capacity."}.get(res, "Allocation failed."),
          "error")
    return redirect(url_for("planning.allocate"))


@bp.route("/allocations/<int:alloc_id>/delete", methods=["POST"])
@login_required
@permission_required("pln_plan")
def allocation_delete(alloc_id):
    svc.delete_allocation(_pk(alloc_id))
    flash("Allocation removed from the plan.", "success")
    return redirect(_referrer_or(url_for("planning.index")))


@bp.route("/export/<key>.csv")
@login_required
@permission_required("pln_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "planning", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("pln_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "planning", "json")
    if resp is None:
        abort(404)
    return resp


@bp.route("/balance/<int:order_id>")
@login_required
@permission_required("pln_view")
def balance(order_id):
    b = svc.balance(_pk(order_id))
    if not b:
        abort(404)
    return render_template("planning/balance.html", active="planning", b=b)


@bp.route("/balance/<int:order_id>/op", methods=["POST"])
@login_required
@permission_required("pln_plan")
def op_add(order_id):
    order_id = _pk(order_id)
    if not svc.order_exists(order_id):
        abort(404)
    ok, reason = svc.add_op(order_id, request.form)
    flash("Operation added." if ok else
          {"bad_smv": "SMV must be a number of 0 or more."}.get(
              reason, "Operation name is required."),
          "success" if ok else "error")
    return redirect(url_for("planning.balance", order_id=order_id))


@bp.route("/ops/<int:op_id>/delete", methods=["POST"])
@login_required
@permission_required("pln_plan")
def op_delete(op_id):
    svc.delete_op(_pk(op_id))
    flash("Operation removed.", "success")
    return redirect(_referrer_or(url_for("planning.index")))


# Report declarations for the shared reporting engine (/reporting). Imported for
# its import-time side effect: it registers the specs and touches no database.
from app.planning import reports as _reports  # noqa: E402,F401
