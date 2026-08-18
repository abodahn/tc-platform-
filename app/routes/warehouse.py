"""
Warehouse routes (native platform blueprint at /warehouse).
Raw material at roll level (FIFO + shade-lot picking), quantity-tracked trims,
and finished goods as a style x colour x size matrix — on platform auth, RBAC,
CSRF, i18n, base template and the notification bell.
"""
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.auth import login_required, permission_required, current_user
from app.warehouse import services as svc
from app.warehouse.constants import MATERIAL_KINDS, ROLL_STATUS, SIZES

bp = Blueprint("warehouse", __name__, url_prefix="/warehouse")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s — no scheduler in this codebase; page loads drive the sweep


def _u():
    return current_user()


def _sweep_throttled():
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.stock_sweep()


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


@bp.route("/")
@login_required
@permission_required("wh_view")
def index():
    _sweep_throttled()
    return render_template("warehouse/dashboard.html", active="warehouse", d=svc.dashboard())


@bp.route("/materials")
@login_required
@permission_required("wh_view")
def materials():
    kind = request.args.get("kind") or None
    low = request.args.get("low") == "1"
    return render_template("warehouse/materials.html", active="wh_materials",
                           rows=svc.list_materials(kind, low), kinds=MATERIAL_KINDS,
                           f_kind=kind, f_low=low)


@bp.route("/materials", methods=["POST"])
@login_required
@permission_required("wh_manage")
def material_create():
    ok, msg = svc.create_material(request.form, _u())
    flash("Material added." if ok else f"Could not add material ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("warehouse.materials"))


@bp.route("/rolls")
@login_required
@permission_required("wh_view")
def rolls():
    material_id = _int(request.args.get("material_id"))
    shade = request.args.get("shade_lot") or None
    status = request.args.get("status") or None
    color = request.args.get("color") or None
    return render_template("warehouse/rolls.html", active="wh_rolls",
                           rows=svc.list_rolls(material_id, color, shade, status),
                           materials=svc.list_materials(), lots=svc.shade_lots(material_id),
                           statuses=ROLL_STATUS, f_material=material_id, f_shade=shade,
                           f_status=status, f_color=color)


@bp.route("/rolls/<int:roll_id>/hold", methods=["POST"])
@login_required
@permission_required("wh_manage")
def roll_hold(roll_id):
    hold = request.form.get("hold") == "1"
    ok, msg = svc.set_roll_hold(roll_id, hold, _u(), request.form.get("notes"))
    flash(f"Roll status: {msg}." if ok else f"Could not update roll ({msg}).",
          "success" if ok else "error")
    return redirect(request.referrer or url_for("warehouse.rolls"))


@bp.route("/rolls/<int:roll_id>/return", methods=["POST"])
@login_required
@permission_required("wh_manage")
def roll_return(roll_id):
    """Book the end-bit back onto the roll it came off after cutting."""
    ok, msg = svc.return_to_store(roll_id, request.form.get("qty"), _u(),
                                 _int(request.form.get("order_id")))
    flash("Returned to store." if ok else f"Return rejected ({msg}).",
          "success" if ok else "error")
    return redirect(request.referrer or url_for("warehouse.rolls"))


@bp.route("/receive")
@login_required
@permission_required("wh_manage")
def receive():
    return render_template("warehouse/receive.html", active="wh_receive",
                           materials=svc.list_materials())


@bp.route("/receive", methods=["POST"])
@login_required
@permission_required("wh_manage")
def receive_post():
    mid = _int(request.form.get("material_id"))
    mat = svc.get_material(mid) if mid else None
    if not mat:
        flash("Pick a material first.", "error")
        return redirect(url_for("warehouse.receive"))
    roll = bool(mat.get("roll_tracked"))
    form = request.form
    # Same rule as the procurement receiving tab: a stock-in that names a PO is
    # booked ON that PO — capped at the outstanding quantity, excess quarantined,
    # its own GRN number allocated. post_stock=False because we book the stock
    # ourselves right after, keeping the roll/lot detail the bridge cannot carry.
    line = svc.po_line_for(form.get("grn_ref"), mid)
    asked = svc.qty_of(form.get("length_m") if roll else form.get("qty"))
    quarantined = 0.0
    if line and asked > 0:
        from app.approvals import services as psvc
        ok, msg = psvc.receive_items(line["pr_id"], {line["item_id"]: asked}, _u(),
                                     notes=form.get("notes") or None, post_stock=False)
        if not ok:
            flash(f"Receipt rejected on the purchase order ({msg}).", "error")
            return redirect(url_for("warehouse.receive"))
        # ponytail: PO booked before the stock move, so a failing move (only
        # realistic case: a duplicate operator-typed roll number) leaves the PR
        # ahead of the shelf. The reverse order would leave uncontrolled free
        # stock, which is the defect being fixed. Two-phase it if that ever bites.
        quarantined = max(0.0, asked - line["outstanding"])
        asked = min(asked, line["outstanding"])
        if asked <= 0:                       # the whole delivery was over-ordered
            flash(f"Nothing outstanding on that order — {quarantined:g} held in "
                  f"quarantine, not added to stock.", "warning")
            return redirect(url_for("warehouse.materials"))
        form = form.copy()
        form["length_m" if roll else "qty"] = str(asked)
    if roll:
        ok, msg = svc.receive_roll(mid, form, _u())
        flash(f"Roll {msg} received." if ok else f"Receipt rejected ({msg}).",
              "success" if ok else "error")
    else:
        ok, msg = svc.receive_qty(mid, form.get("qty"), form.get("unit_cost"), _u(),
                                  grn_ref=form.get("grn_ref"))
        flash("Goods received." if ok else f"Receipt rejected ({msg}).",
              "success" if ok else "error")
    if ok and quarantined > 0:
        flash(f"{quarantined:g} over the ordered quantity was quarantined, not "
              f"added to stock.", "warning")
    return redirect(url_for("warehouse.rolls" if roll else "warehouse.materials"))


@bp.route("/adjust", methods=["POST"])
@login_required
@permission_required("wh_manage")
def adjust():
    mid = _int(request.form.get("material_id"))
    ok, msg = svc.adjust_stock(mid, request.form.get("delta"), request.form.get("reason"),
                               _u(), _int(request.form.get("roll_id")))
    flash("Stock adjusted." if ok else f"Adjustment rejected ({msg}).",
          "success" if ok else "error")
    return redirect(request.referrer or url_for("warehouse.materials"))


@bp.route("/issue")
@login_required
@permission_required("wh_view")
def issue():
    mid = _int(request.args.get("material_id"))
    required = request.args.get("required")
    shade = request.args.get("shade_lot") or None
    plan = svc.plan_pick(mid, required, shade, request.args.get("min_width_cm")) \
        if (mid and required) else None
    return render_template("warehouse/issue.html", active="wh_issue",
                           materials=svc.list_materials(), orders=svc.list_orders_lite(),
                           lots=svc.shade_lots(mid), plan=plan, allocs=svc.open_allocations(),
                           f_material=mid, f_required=required, f_shade=shade,
                           f_width=request.args.get("min_width_cm"),
                           f_order=_int(request.args.get("order_id")))


@bp.route("/issue", methods=["POST"])
@login_required
@permission_required("wh_manage")
def issue_post():
    mid = _int(request.form.get("material_id"))
    oid = _int(request.form.get("order_id"))
    required = request.form.get("required")
    shade = request.form.get("shade_lot") or None
    width = request.form.get("min_width_cm")
    if not (mid and oid):
        flash("Pick a material and an order.", "error")
        return redirect(url_for("warehouse.issue"))
    action = request.form.get("action") or "issue"
    if action == "reserve":
        ok, msg, plan = svc.reserve_for_order(oid, mid, required, _u(), shade, width)
    else:
        ok, msg, plan = svc.issue_to_order(oid, mid, required, _u(), shade, width,
                                           request.form.get("notes"))
    if ok:
        flash("Material reserved." if action == "reserve" else f"Material issued ({msg}).",
              "success")
    elif msg == "shortfall":
        flash("Not enough material — short by %s. Nothing was issued."
              % (plan or {}).get("shortfall", "?"), "error")
    else:
        flash(f"Issue rejected ({msg}).", "error")
    return redirect(url_for("warehouse.issue", material_id=mid, order_id=oid))


@bp.route("/allocations/<int:alloc_id>/release", methods=["POST"])
@login_required
@permission_required("wh_manage")
def allocation_release(alloc_id):
    ok, msg = svc.release_allocation(alloc_id, _u())
    flash("Reservation released." if ok else f"Could not release ({msg}).",
          "success" if ok else "error")
    return redirect(request.referrer or url_for("warehouse.issue"))


@bp.route("/fg")
@login_required
@permission_required("wh_view")
def fg():
    oid = _int(request.args.get("order_id"))
    return render_template("warehouse/fg.html", active="wh_fg", m=svc.fg_matrix(oid),
                           orders=svc.list_orders_lite(), sizes=SIZES, f_order=oid)


@bp.route("/fg", methods=["POST"])
@login_required
@permission_required("wh_manage")
def fg_post():
    oid = _int(request.form.get("order_id"))
    ok, msg = svc.fg_move(oid, request.form.get("style_code"), request.form.get("color"),
                          request.form.get("size"), request.form.get("qty"),
                          request.form.get("action") or "pack", _u())
    flash("Finished goods updated." if ok else f"Rejected ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("warehouse.fg", order_id=oid))


# --- exports (same wire format as every other module: see app/services/export.py) ---
@bp.route("/export/<key>.csv")
@login_required
@permission_required("wh_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "warehouse", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("wh_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "warehouse", "json")
    if resp is None:
        abort(404)
    return resp


# Report declarations for the shared engine (app/services/reporting.py). Imported
# here, at the bottom, purely so importing this blueprint registers them; the
# module builds plain dicts and touches no database at import time.
from app.warehouse import reports as _reports  # noqa: E402,F401
