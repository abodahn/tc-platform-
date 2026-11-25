"""Procurement routes for import orders (shipment + landed cost) and framework
agreements. Registers on the existing approvals blueprint; logic lives in
app/approvals/imports.py."""
import base64
import io

from flask import abort, redirect, render_template, request, send_file, url_for

from app.auth import login_required, permission_required, user_can
from app.approvals import imports as imp
from app.approvals import services as svc
from app.routes.approvals import bp, flash, _u, _ip, _ATTACH_EXT, _MAX_ATTACH

# Refusal code -> the English sentence flashed (translated via messages.py).
MSG = {
    "not_found": "Not found or no longer available.",
    "error": "That could not be done. Reload the page and try again.",
    "forbidden": "You are not allowed to do this.",
    "not_import": "This request is not an import order. Shipments and landed costs are only for import orders.",
    "no_po": "Choose one of this request's purchase orders.",
    "bad_incoterm": "Choose an incoterm from the list.",
    "bad_method": "Choose how to allocate the charges: by value or by quantity.",
    "bad_date": "A date is not valid. Use the date picker.",
    "milestone_order": "The milestone dates are out of order: shipped, then at port, then cleared, then delivered.",
    "bad_kind": "Choose the type of charge.",
    "bad_amount": "Enter the amount as a positive number.",
    "bad_currency": "Choose a currency from the list.",
    "fx_required": "Enter the exchange rate to EGP for a charge in a foreign currency.",
    "void": "This charge was already voided.",
    "reason_required": "Write why the charge is being voided.",
    "bad_file": "The document must be a PDF, image, Excel or CSV file of 3 MB or less.",
    "bad_dates": "Enter valid from and valid to dates. The end cannot be before the start or already past.",
    "bad_ceiling": "Enter the ceiling as a positive number.",
    "ceiling_required": "Give the agreement a ceiling value, a ceiling quantity, or both.",
    "vendor_not_approved": "Choose an active supplier from the approved vendor list.",
    "lines_required": "Add at least one agreement line with its agreed unit price.",
    "bad_line": "Each agreement line needs an item and an agreed unit price above zero.",
    "unknown_code": "An item code is not in the catalogue. Check the code or type the item as free text.",
    "not_draft": "Only a draft agreement can be approved.",
    "self_approval": "You created this agreement, so someone else must approve it.",
    "closed": "This agreement is already closed.",
    "locked": "This request can no longer be priced from an agreement.",
    "self_pricing": "You raised this request, so you cannot price it.",
    "agreement_expired": "The agreement has expired. Price this line through the normal quotation process.",
    "agreement_inactive": "The agreement is not active. Price this line through the normal quotation process.",
    "agreement_item": "That agreement line is for a different item.",
    "agreement_currency": "The agreement is in a different currency from this request.",
    "agreement_ceiling": "This line would take the agreement over its ceiling. Price it through the normal quotation process.",
}
# price_pr's own refusal under the same code means the REQUEST needs a rate.
DRAW_MSG = dict(MSG, fx_required="Set this request's exchange rate to EGP before pricing it.")
OK = {
    "saved": "Shipment saved.",
    "added": "Charge recorded.",
    "corrected": "Charge corrected.",
    "voided": "Charge voided.",
    "drawn": "Line priced from the agreement.",
    "released": "Line released from the agreement. The quotation rule applies to it again.",
    "created": "Agreement saved as a draft. A Procurement admin or Finance must approve it.",
    "approved": "Agreement approved and active.",
    "closed": "Agreement closed.",
}


def _say(ok, code, table=MSG):
    if ok:
        flash(OK.get(code, OK["saved"]), "success")
    else:
        flash(table.get(str(code).split(":")[0], MSG["error"]), "error")


def _back(pr_id, anchor="imp-ship"):
    return redirect(url_for("approvals.detail", pr_id=pr_id) + "#" + anchor)


@bp.app_template_global("proc_imports_ctx")
def proc_imports_ctx(pr, bundle):
    return imp.detail_ctx(pr, bundle, _u())


# --------------------------------------------------------------------------
# Import orders: shipment + landed-cost charges
# --------------------------------------------------------------------------
@bp.route("/pr/<int:pr_id>/shipment", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_admin")
def imp_shipment(pr_id):
    ok, code = imp.save_shipment(pr_id, request.form.get("po_no", ""), request.form, _u(), ip=_ip())
    _say(ok, code)
    return _back(pr_id)


@bp.route("/pr/<int:pr_id>/charge", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_admin")
def imp_charge(pr_id):
    fname = ctype = b64 = None
    f = request.files.get("document")
    if f and f.filename:
        raw = f.read()
        if (not f.filename.lower().endswith(_ATTACH_EXT) or not raw or len(raw) > _MAX_ATTACH):
            _say(False, "bad_file")
            return _back(pr_id)
        fname, ctype = f.filename[:200], f.mimetype or "application/octet-stream"
        b64 = base64.b64encode(raw).decode("ascii")
    ok, code = imp.add_charge(pr_id, request.form.get("po_no", ""), request.form, _u(),
                              filename=fname, content_type=ctype, content_b64=b64, ip=_ip())
    _say(ok, code)
    return _back(pr_id)


@bp.route("/charge/<int:charge_id>/correct", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_pay", "proc_admin")
def imp_charge_correct(charge_id):
    ok, code, pr_id = imp.correct_charge(charge_id, request.form, _u(), ip=_ip())
    if pr_id is None:
        abort(404)
    _say(ok, code)
    return _back(pr_id)


@bp.route("/charge/<int:charge_id>/void", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_pay", "proc_admin")
def imp_charge_void(charge_id):
    ok, code, pr_id = imp.void_charge(charge_id, request.form.get("reason"), _u(), ip=_ip())
    if pr_id is None:
        abort(404)
    _say(ok, code)
    return _back(pr_id)


@bp.route("/charge/<int:charge_id>/file")
@login_required
@permission_required("proc_view")
def imp_charge_file(charge_id):
    row = imp.get_charge(charge_id)
    if not row or not row.get("content_b64"):
        abort(404)
    bundle = svc.get_pr(row["pr_id"])
    if not bundle or svc.money_view(_u(), bundle["pr"], bundle["steps"]) != "full":
        abort(403)
    try:
        raw = base64.b64decode(row["content_b64"])
    except Exception:
        abort(404)
    return send_file(io.BytesIO(raw), mimetype=row["content_type"] or "application/octet-stream",
                     as_attachment=True, download_name=row["filename"] or "charge")


# --------------------------------------------------------------------------
# Framework agreements: drawing a request line
# --------------------------------------------------------------------------
def _int(name):
    try:
        return int(request.form.get(name) or 0)
    except (TypeError, ValueError):
        return 0


@bp.route("/pr/<int:pr_id>/agreement/draw", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_admin")
def imp_draw(pr_id):
    ok, code = imp.draw_line(pr_id, _int("item_id"), _int("line_id"), _u(), ip=_ip())
    _say(ok, code, DRAW_MSG)
    return _back(pr_id, "imp-agr")


@bp.route("/pr/<int:pr_id>/agreement/release", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_admin")
def imp_release(pr_id):
    ok, code = imp.release_line(pr_id, _int("item_id"), _u(), ip=_ip())
    _say(ok, code)
    return _back(pr_id, "imp-agr")


# --------------------------------------------------------------------------
# Framework agreements: register
# --------------------------------------------------------------------------
@bp.route("/agreements", methods=["GET"])
@login_required
@permission_required("proc_view")
def agreements():
    user = _u()
    vendor, status = request.args.get("vendor", "").strip(), request.args.get("status", "").strip()
    return render_template("approvals/agreements.html", active="proc_agreements",
                           rows=imp.list_agreements(vendor, status), vendor=vendor, status=status,
                           vendors=svc.list_vendors(), money_full=svc.money_view(user) == "full",
                           can_add=imp.can_record(user), today=imp._today())


@bp.route("/agreements.xlsx", methods=["GET"])
@login_required
@permission_required("proc_view")
def agreements_xlsx():
    from app.approvals import xlsx as X
    rows = imp.list_agreements(request.args.get("vendor", "").strip(),
                               request.args.get("status", "").strip())
    data = imp.agreements_xlsx(rows, money=svc.money_view(_u()) == "full")
    return send_file(io.BytesIO(data), mimetype=X.MIME, as_attachment=True,
                     download_name="framework_agreements.xlsx")


@bp.route("/agreements", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_admin")
def agreement_create():
    f = request.form
    lines = [{"code": c, "item": i, "unit": u, "unit_price": p} for c, i, u, p in zip(
        f.getlist("code"), f.getlist("item"), f.getlist("unit"), f.getlist("unit_price"))]
    ok, res = imp.create_agreement(f, lines, _u(), ip=_ip())
    if not ok:
        _say(False, res)
        return redirect(url_for("approvals.agreements") + "#agr-new")
    _say(True, "created")
    return redirect(url_for("approvals.agreement_detail", agr_id=res))


@bp.route("/agreements/<int:agr_id>", methods=["GET"])
@login_required
@permission_required("proc_view")
def agreement_detail(agr_id):
    a = imp.get_agreement(agr_id)
    if not a:
        abort(404)
    user = _u()
    return render_template("approvals/agreement_detail.html", active="proc_agreements", a=a,
                           money_full=svc.money_view(user) == "full",
                           can_approve=(imp.can_approve(user) and a["status"] == "draft"
                                        and a["created_by"] != (user or {}).get("username")),
                           can_close=((imp.can_record(user) or imp.can_approve(user))
                                      and a["status"] != "closed"),
                           can_purchasing=user_can("proc_purchasing"))


@bp.route("/agreements/<int:agr_id>/approve", methods=["POST"])
@login_required
@permission_required("proc_admin", "proc_pay")
def agreement_approve(agr_id):
    ok, code = imp.approve_agreement(agr_id, _u(), ip=_ip())
    _say(ok, code)
    return redirect(url_for("approvals.agreement_detail", agr_id=agr_id))


@bp.route("/agreements/<int:agr_id>/close", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_admin", "proc_pay")
def agreement_close(agr_id):
    ok, code = imp.close_agreement(agr_id, _u(), ip=_ip())
    _say(ok, code)
    return redirect(url_for("approvals.agreement_detail", agr_id=agr_id))
