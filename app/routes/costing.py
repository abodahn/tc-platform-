"""
BOM + order costing routes (/costing). Estimate vs actual per customer order,
reusing platform auth, RBAC, CSRF, i18n, base template and the bell.
"""
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.auth import login_required, permission_required, current_user
from app.costing import services as svc
from app.costing.constants import BOM_KINDS, CATEGORIES, UOMS, VARIANCE_ALERT_PCT

bp = Blueprint("costing", __name__, url_prefix="/costing")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s


def _u():
    return current_user()


def _sweep_throttled():
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.variance_sweep()


def _back(order_id):
    return redirect(url_for("costing.sheet", order_id=order_id))


_BOM_ERRORS = {
    "item_required": "Material/item name is required.",
    "bad_consumption": "Consumption per unit must be a number greater than zero.",
    "bad_allowance": "Allowance % must be a number and cannot be negative.",
    "bad_price": "Unit price must be a number and cannot be negative.",
}


@bp.route("/")
@login_required
@permission_required("cost_view")
def index():
    _sweep_throttled()
    return render_template("costing/dashboard.html", active="costing",
                           d=svc.dashboard(), alert_pct=VARIANCE_ALERT_PCT)


@bp.route("/orders")
@login_required
@permission_required("cost_view")
def orders():
    only = request.args.get("costed") == "1"
    sort = (request.args.get("sort") or "risk").strip()
    q = (request.args.get("q") or "").strip()
    rows = svc.list_costed(only_costed=only)

    if q:
        needle = q.lower()
        rows = [o for o in rows if needle in " ".join(str(o.get(k) or "").lower() for k in
                ("order_no", "buyer", "style_name", "style_ref"))]

    # PROBLEMS FIRST is the default, and it is the whole point of the page. A
    # costing manager opens this to find the orders quietly losing money, and
    # ship-date order buries them. Rank: money overspent against the quote,
    # then thinnest delivered margin. Orders with no actual booked yet cannot be
    # judged, so they sort last rather than looking healthy.
    def overspend(o):
        return (o["variance"]["total"]["value"] or 0) if o["has_actual"] else float("-inf")

    def thinnest(o):
        p = o["margin"]["act_pct"] if o["has_actual"] else o["margin"]["est_pct"]
        return p if p is not None else 999.0

    if sort == "margin":
        rows.sort(key=thinnest)
    elif sort == "ship":
        rows.sort(key=lambda o: (o.get("ship_date") or "9999-99-99", o.get("order_no") or ""))
    elif sort == "order":
        rows.sort(key=lambda o: (o.get("order_no") or ""))
    else:
        sort = "risk"
        rows.sort(key=lambda o: (-overspend(o), thinnest(o)))

    # One honest summary line. Every figure below counts only orders that HAVE an
    # actual booked, because mixing quoted-only orders into an "overspend" total
    # would overstate or understate it depending on which happened to be costed.
    booked = [o for o in rows if o["has_actual"]]
    over = [o for o in booked if o["variance"]["total"]["flag"] == "unfavourable"]
    losing = [o for o in booked if (o["margin"]["act_pct"] or 0) < 0]
    thin = [o for o in booked if o["margin"]["act_pct"] is not None
            and 0 <= o["margin"]["act_pct"] < VARIANCE_ALERT_PCT]
    summary = {
        "orders": len(rows),
        "costed": sum(1 for o in rows if o.get("costed")),
        "booked": len(booked),
        "estimate": round(sum(o["estimate"]["total"] for o in booked), 2),
        "actual": round(sum(o["actual"]["total"] for o in booked), 2),
        "overspend": round(sum(o["variance"]["total"]["value"] for o in over), 2),
        "over_n": len(over),
        "losing_n": len(losing),
        "thin_n": len(thin),
    }
    return render_template("costing/orders.html", active="cost_orders",
                           rows=rows, f_costed=only, sort=sort, q=q, summary=summary,
                           alert_pct=VARIANCE_ALERT_PCT)


@bp.route("/order/<int:order_id>")
@login_required
@permission_required("cost_view")
def sheet(order_id):
    bundle = svc.cost_sheet(order_id)
    if not bundle:
        abort(404)
    return render_template("costing/sheet.html", active="cost_orders", **bundle,
                           kinds=BOM_KINDS, uoms=UOMS, categories=CATEGORIES,
                           alert_pct=VARIANCE_ALERT_PCT)


@bp.route("/order/<int:order_id>/bom", methods=["POST"])
@login_required
@permission_required("cost_manage")
def bom_add(order_id):
    ok, msg = svc.add_bom_line(order_id, request.form, _u())
    if msg == "no_order":
        abort(404)
    flash("BOM line added." if ok else _BOM_ERRORS.get(msg, "Could not add the BOM line."),
          "success" if ok else "error")
    return _back(order_id)


@bp.route("/bom/<int:line_id>", methods=["POST"])
@login_required
@permission_required("cost_manage")
def bom_update(line_id):
    ok, res = svc.update_bom_line(line_id, request.form)
    if not ok:
        flash(_BOM_ERRORS.get(res, "BOM line not found."), "error")
        return redirect(request.referrer or url_for("costing.index"))
    flash("BOM line removed." if request.form.get("action") == "delete" else "BOM line updated.",
          "success")
    return _back(res)


@bp.route("/order/<int:order_id>/sheet", methods=["POST"])
@login_required
@permission_required("cost_manage")
def sheet_save(order_id):
    ok, msg = svc.save_sheet(order_id, request.form, _u())
    if msg == "no_order":
        abort(404)
    flash("Cost sheet saved." if ok else
          {"bad_number": "One of the cost figures is not a number.",
           "negative": "Cost figures cannot be negative."}.get(msg, "Could not save the cost sheet."),
          "success" if ok else "error")
    return _back(order_id)


@bp.route("/order/<int:order_id>/actual", methods=["POST"])
@login_required
@permission_required("cost_manage")
def actual_add(order_id):
    ok, msg = svc.add_actual(order_id, request.form, _u())
    if msg == "no_order":
        abort(404)
    flash("Actual cost booked." if ok else
          {"bad_category": "Pick a valid cost category.",
           "bad_amount": "Amount must be a number greater than zero."}
          .get(msg, "Could not book the actual cost."), "success" if ok else "error")
    return _back(order_id)


@bp.route("/actual/<int:actual_id>/delete", methods=["POST"])
@login_required
@permission_required("cost_manage")
def actual_delete(actual_id):
    ok, res = svc.delete_actual(actual_id)
    if not ok:
        flash("Entry not found.", "error")
        return redirect(request.referrer or url_for("costing.index"))
    flash("Actual cost entry removed.", "success")
    return _back(res)


@bp.route("/export/<key>.csv")
@login_required
@permission_required("cost_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "costing", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("cost_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "costing", "json")
    if resp is None:
        abort(404)
    return resp


@bp.route("/order/<int:order_id>/link-pr", methods=["POST"])
@login_required
@permission_required("cost_manage")
def link_pr(order_id):
    ok, msg = svc.link_pr_to_order(order_id, request.form.get("pr_ref"), _u())
    if msg == "no_order":
        abort(404)
    flash("Purchase requisition linked to this order." if ok else
          {"no_pr": "Enter a PR number.",
           "pr_not_found": "No purchase requisition with that number.",
           "pr_owned": "That purchase requisition already belongs to another module — "
                       "unlink it there first.",
           "no_procurement": "Procurement module is not available."}
          .get(msg, "Could not link the purchase requisition."), "success" if ok else "error")
    return _back(order_id)


# Report declarations for the shared reporting engine (/reporting). Imported for
# its import-time side effect: it registers the specs and touches no database.
from app.costing import reports as _reports  # noqa: E402,F401
