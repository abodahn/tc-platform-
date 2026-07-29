"""
Shipments & export-document routes (native platform blueprint at /shipping).
Closes the order lifecycle: cartons -> packing list -> commercial invoice ->
ordered-vs-shipped reconciliation, on platform auth, RBAC, CSRF, i18n and the bell.
"""
import time

from flask import (Blueprint, render_template, request, redirect, url_for, flash, abort)

from app.auth import login_required, permission_required, current_user
from app.shipping import services as svc
from app.shipping.constants import (INCOTERMS, MODES, SHIPMENT_STATUS, CREATE_STATUS,
                                    QTY_TOLERANCE_PCT)

bp = Blueprint("shipping", __name__, url_prefix="/shipping")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s


def _u():
    return current_user()


def _sweep_throttled():
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.recon_sweep()


@bp.route("/")
@login_required
@permission_required("shp_view")
def index():
    _sweep_throttled()
    return render_template("shipping/dashboard.html", active="shipping", d=svc.dashboard())


@bp.route("/shipments")
@login_required
@permission_required("shp_view")
def shipments():
    status = request.args.get("status") or None
    mode = request.args.get("mode") or None
    return render_template("shipping/list.html", active="shipping",
                           rows=svc.list_shipments(status, mode), statuses=SHIPMENT_STATUS,
                           modes=MODES, f_status=status, f_mode=mode)


@bp.route("/shipments/new")
@login_required
@permission_required("shp_manage")
def shipment_new():
    return render_template("shipping/shipment_form.html", active="shipping",
                           orders=svc.order_options(), incoterms=INCOTERMS, modes=MODES,
                           statuses=CREATE_STATUS)   # never offer a status that locks at birth


@bp.route("/shipments", methods=["POST"])
@login_required
@permission_required("shp_manage")
def shipment_create():
    if not (request.form.get("destination") or "").strip():
        flash("Destination is required.", "error")
        return redirect(url_for("shipping.shipment_new"))
    sid = svc.create_shipment(request.form, _u())
    flash("Shipment created.", "success")
    return redirect(url_for("shipping.detail", shipment_id=sid))


@bp.route("/shipments/<int:shipment_id>")
@login_required
@permission_required("shp_view")
def detail(shipment_id):
    b = svc.get_shipment(shipment_id)
    if not b:
        abort(404)
    return render_template("shipping/detail.html", active="shipping", **b,
                           incoterms=INCOTERMS, modes=MODES, statuses=SHIPMENT_STATUS)


@bp.route("/shipments/<int:shipment_id>/update", methods=["POST"])
@login_required
@permission_required("shp_manage")
def shipment_update(shipment_id):
    ok, msg = svc.update_shipment(shipment_id, request.form, _u())
    if msg == "not_found":
        abort(404)
    note = {"status_locked": "That status change was refused: a shipment that has left cannot "
                             "go back to planned or packed, and a cancelled one cannot be "
                             "re-dispatched. Its packing list is a customs document.",
            "price_locked": "The new invoice unit price was refused — this shipment has "
                            "already left, so its price is final.",
            "bad_price": "The invoice unit price must be a number of 0 or more.",
            "bad_date": "A date was not a real calendar date (YYYY-MM-DD) and was not saved."}
    # Both messages, because a refused field does not roll back the fields that saved.
    if ok:
        flash("Shipment updated.", "success")
    if msg in note:
        flash(note[msg], "error")
    return redirect(url_for("shipping.detail", shipment_id=shipment_id))


@bp.route("/shipments/<int:shipment_id>/cartons", methods=["POST"])
@login_required
@permission_required("shp_manage")
def carton_add(shipment_id):
    ok, msg = svc.add_carton(shipment_id, request.form, _u())
    flash("Carton line added." if ok else {
        "bad_qty": "Pieces per carton and number of cartons must both be whole numbers "
                   "above zero — a fraction of a garment or of a box is a keying error.",
        "bad_number": "Weights and dimensions cannot be negative.",
        "gross_below_net": "Gross weight cannot be less than net weight.",
        "shipment_locked": "This shipment has been dispatched — its packing list is final.",
        "shipment_not_found": "Shipment not found.",
    }.get(msg, msg), "success" if ok else "error")
    return redirect(url_for("shipping.detail", shipment_id=shipment_id))


@bp.route("/cartons/<int:carton_id>/delete", methods=["POST"])
@login_required
@permission_required("shp_manage")
def carton_delete(carton_id):
    ok, msg, sid = svc.delete_carton(carton_id, _u())
    if ok:
        flash("Carton line removed.", "success")
    else:
        flash("This shipment has been dispatched — its packing list is final."
              if msg == "shipment_locked" else "Carton line not found.", "error")
    # Redirect to the shipment we just touched, never to the Referer header — that is
    # attacker-controlled and an off-site redirect.
    return redirect(url_for("shipping.detail", shipment_id=sid) if sid
                    else url_for("shipping.index"))


@bp.route("/shipments/<int:shipment_id>/packing-list")
@login_required
@permission_required("shp_view")
def packing_list(shipment_id):
    b = svc.get_shipment(shipment_id)
    if not b:
        abort(404)
    return render_template("shipping/packing_list.html", active="shipping", **b)


@bp.route("/shipments/<int:shipment_id>/invoice")
@login_required
@permission_required("shp_view")
def invoice(shipment_id):
    b = svc.invoice(shipment_id)
    if not b:
        abort(404)
    return render_template("shipping/invoice.html", active="shipping", **b)


@bp.route("/reconciliation")
@login_required
@permission_required("shp_view")
def reconciliation():
    _sweep_throttled()
    return render_template("shipping/reconciliation.html", active="shipping",
                           rows=svc.reconciliation(), tolerance=QTY_TOLERANCE_PCT)


@bp.route("/export/<key>.csv")
@login_required
@permission_required("shp_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "shipping", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("shp_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "shipping", "json")
    if resp is None:
        abort(404)
    return resp


# Report declarations for the shared engine (app/services/reporting.py) — see the
# note in app/routes/warehouse.py. Plain dicts, no database work at import.
from app.shipping import reports as _reports  # noqa: E402,F401
