"""
TC Platform — delivery follow-up routes on the procurement blueprint: the
buyer's promised date per purchase order, the open-orders / late-deliveries
list, and cancelling ONE supplier's order on a split request.

Registered on approvals.bp (imported at the very end of app/routes/approvals.py),
so every URL lives under /procurement and every message leaves through the same
translating flash().
"""
from flask import render_template, request, redirect, url_for

from app.auth import login_required, permission_required, user_can
from app.approvals import delivery as dl
from app.routes.approvals import bp, flash, _u, _ip


@bp.app_template_global()
def delivery_today():
    return dl._today().isoformat()


@bp.app_template_global()
def delivery_late():
    """{pr_id: {"days", "promised"}} for every request still owed goods."""
    try:
        return dl.late_map()
    except Exception:                                        # noqa: BLE001
        return {}


@bp.app_template_global()
def delivery_pos(pr_id):
    """Per-order delivery state for the request page."""
    try:
        return dl.po_states(pr_id, is_admin=user_can("proc_admin"))
    except Exception:                                        # noqa: BLE001
        return []


@bp.app_template_global()
def delivery_dead(pr_id):
    """Line ids of a supplier's order cancelled alone: not receivable."""
    from app.db import get_db
    conn = get_db()
    try:
        return dl.cancelled_item_ids(conn, pr_id)
    except Exception:                                        # noqa: BLE001
        return set()
    finally:
        conn.close()


_PROMISE_ERR = {
    "bad_date": "Enter the promised delivery date as a valid date.",
    "not_found": "That purchase order no longer exists.",
    "po_cancelled": "That purchase order is cancelled.",
    "not_open": "This request is no longer waiting for delivery.",
}

_CANCEL_ERR = {
    "reason_required": "Write why this purchase order is being cancelled.",
    "not_found": "That purchase order no longer exists.",
    "not_split": "This request has only one purchase order — cancel the request itself instead.",
    "already_cancelled": "This purchase order is already cancelled.",
    "not_cancellable": "Only an order that is still waiting for delivery can be cancelled.",
    "already_received": "Goods were already received or returned against this order, so it "
                        "cannot be cancelled. Close the request short instead.",
    "has_payments": "Money was already invoiced or paid against this order, so it cannot be "
                    "cancelled.",
    "needs_admin": "This is the last open order on the request, so cancelling it cancels the "
                   "whole request — only an administrator can do that.",
}

_CANCEL_OK = {
    "cancelled": "Purchase order cancelled.",
    "emailed": "Purchase order cancelled and the cancellation emailed to the supplier.",
    "no_smtp": "Purchase order cancelled. The cancellation email was not sent — email "
               "(SMTP) is not set up on this server, so tell the supplier yourself.",
    "no_email": "Purchase order cancelled. The supplier has no email address on file, so "
                "tell them yourself.",
    "send_failed": "Purchase order cancelled, but the cancellation email could not be sent — "
                   "tell the supplier yourself.",
}


def _back(pr_id):
    return redirect(url_for("approvals.detail", pr_id=pr_id) if pr_id
                    else url_for("approvals.deliveries"))


@bp.route("/po/<int:po_id>/promised", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def po_promised(po_id):
    ok, code, pr_id = dl.set_promised(po_id, request.form.get("promised_date"), _u(), ip=_ip())
    if ok:
        flash("Promised delivery date saved.", "success")
    else:
        flash(_PROMISE_ERR.get(code, "That purchase order no longer exists."), "error")
    return _back(pr_id)


@bp.route("/po/<int:po_id>/cancel", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def po_cancel(po_id):
    ok, code, pr_id = dl.cancel_po(po_id, request.form.get("reason"), _u(), ip=_ip(),
                                   email_vendor=request.form.get("email_vendor") == "1",
                                   is_admin=user_can("proc_admin"))
    if ok:
        flash(_CANCEL_OK.get(code, "Purchase order cancelled."),
              "success" if code in ("cancelled", "emailed") else "warning")
    else:
        flash(_CANCEL_ERR.get(code, "That purchase order no longer exists."), "error")
    return _back(pr_id)


@bp.route("/deliveries")
@login_required
@permission_required("proc_purchasing")
def deliveries():
    late_only = request.args.get("late") == "1"
    from app.db import get_db
    conn = get_db()
    try:
        rows = dl.open_orders(conn)
    finally:
        conn.close()
    n_late = sum(1 for r in rows if r["days_late"] > 0)
    if late_only:
        rows = [r for r in rows if r["days_late"] > 0]
    from app.approvals.guide import t
    return render_template("approvals/deliveries.html", active="proc_deliveries",
                           rows=rows, late_only=late_only, n_late=n_late,
                           page_title=t((_u() or {}).get("lang_pref") or "en",
                                        "proc.delivery.list_title"))
