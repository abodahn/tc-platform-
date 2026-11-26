"""Routes for the PO lane (app/approvals/porev.py): real PO revisions, bulk
signing from the approval queue, and the pre-filled new-request form (copy a
request / raise one from warehouse low stock). Registered on the procurement
blueprint; imported once at the end of app/routes/approvals.py.
"""
from flask import abort, redirect, render_template, request, url_for

from app.auth import current_user, login_required, permission_required, user_can
from app.approvals import porev
from app.approvals.messages import translate as _translate_msg
from app.routes.approvals import APPROVE_REFUSALS, _code, _ip, _u, bp, flash

# Refusals of a revision, keyed by porev's codes. English is the MESSAGES key.
_REV_ERR = {
    "reason_required": "Enter the reason for the revision — it is kept in the PO history.",
    "not_revisable": "Only an issued (or partially received) Purchase Order can be revised.",
    "rev_pending": "This request already has a PO revision waiting for signatures. "
                   "Finish or withdraw it first.",
    "po_required": "Choose which supplier's purchase order you are revising.",
    "po_cancelled": "That purchase order is cancelled, so it cannot be revised.",
    "bad_qty": "Enter each quantity as a number greater than zero.",
    "bad_price": "Enter each unit price as a number of zero or more.",
    "qty_below_received": "A quantity cannot go below what has already been received.",
    "bad_date": "Enter the delivery date as a date.",
    "no_change": "Nothing was changed — the purchase order stays as it is.",
    "below_billed": "The revised order would be worth less than what has already been "
                    "invoiced or paid against it.",
    "self_revise": "You raised this request, so you cannot revise its purchase order.",
    "split_discount": "This request's header discount is shared by several suppliers' "
                      "orders, so one order cannot be revised on its own — it would "
                      "change the others' money.",
    "not_found": "Request not found.",
}
_REV_OK = {
    "applied": "PO revised — the new revision is live and numbered on the PO.",
    "pending": "The revision raises the order above its approved value, so it is "
               "waiting for approval. The PO stays as it is until it is signed.",
}
_SIGN_ERR = {
    "forbidden": "You are not authorised for this approval stage.",
    "not_pending": "This revision is no longer waiting for a signature.",
    "no_active_step": "No active approval step.",
    "self_approval": "You cannot approve your own request — raising it is your signature.",
    "self_revision": "You raised this revision, so a different approver must sign it.",
    "dual_role": "You already signed another stage of this request — a different "
                 "approver must take this one.",
    "qty_below_received": "A quantity cannot go below what has already been received.",
    "not_revisable": "Only an issued (or partially received) Purchase Order can be revised.",
    "po_required": "Choose which supplier's purchase order you are revising.",
    "bad_line": "A line of this revision no longer exists on the request.",
}
_SIGN_OK = {
    "advanced": "Approved — routed to the next approver.",
    "applied": "Final approval complete — the PO revision is now live.",
    "rejected": "Revision rejected — the purchase order is unchanged.",
}
# act_on_step's codes for the bulk summary: the approve route's own sentences
# (APPROVE_REFUSALS), so its Arabic and Turkish already exist in MESSAGES.
_BULK_ERR = dict(APPROVE_REFUSALS, **{
    "on_hold": "This request is on hold. Open it and resume it before signing.",
    "not_found": "Request not found.",
})
_BULK_OK = {
    "advanced": "Approved — routed to the next approver.",
    "partial": "Approved & signed. Waiting on the co-approver at this stage.",
    "approved": "Final approval complete. Purchase Order drafted.",
}


def _lang():
    return (_u() or {}).get("lang_pref") or "en"


def _back(pr_id):
    return redirect(url_for("approvals.detail", pr_id=pr_id) + "#porev")


@bp.route("/pr/<int:pr_id>/po-revision", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def porev_propose(pr_id):
    """Revise ONE order: quantities, unit prices, delivery date, with a reason."""
    f = request.form
    lines = {}
    for key, val in f.items():
        for prefix, field in (("qty_", "qty"), ("price_", "unit_price")):
            if key.startswith(prefix) and key[len(prefix):].isdigit():
                lines.setdefault(key[len(prefix):], {})[field] = val
    ok, res = porev.propose_revision(pr_id, f.get("po_id"), lines, f.get("delivery_date"),
                                     f.get("reason"), _u(), ip=_ip(),
                                     email=f.get("email") == "1")
    flash(_REV_OK.get(res, "PO revised.") if ok else
          _REV_ERR.get(_code(res)) or APPROVE_REFUSALS.get(_code(res),
                                                       f"Could not revise the PO ({res})."),
          "success" if ok else "error")
    return _back(pr_id)


def _rev_pr(rev_id):
    view = porev.revision_pr(rev_id)
    if view is None:
        abort(404)
    return view


@bp.route("/po-revision/<int:rev_id>/sign", methods=["POST"])
@login_required
@permission_required("proc_approve", "proc_admin")
def porev_sign(rev_id):
    pr_id = _rev_pr(rev_id)
    decision = "reject" if request.form.get("decision") == "reject" else "approve"
    ok, res = porev.sign_revision(rev_id, _u(), decision, request.form.get("comment"), ip=_ip())
    flash(_SIGN_OK.get(res, "Signed.") if ok else
          _SIGN_ERR.get(_code(res)) or APPROVE_REFUSALS.get(
              _code(res), f"Could not sign the revision ({res})."),
          ("warning" if res == "rejected" else "success") if ok else "error")
    return _back(pr_id)


@bp.route("/po-revision/<int:rev_id>/cancel", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def porev_cancel(rev_id):
    pr_id = _rev_pr(rev_id)
    ok, res = porev.cancel_revision(rev_id, _u(), ip=_ip())
    flash("Revision withdrawn — the purchase order is unchanged." if ok else
          _SIGN_ERR.get(_code(res), f"Could not withdraw the revision ({res})."),
          "success" if ok else "error")
    return _back(pr_id)


@bp.route("/bulk-sign", methods=["POST"])
@login_required
@permission_required("proc_approve", "proc_admin")
def bulk_sign():
    """Sign the ticked requests with the signer's saved signature. Every one is
    still checked on its own by act_on_step; the page lists what happened."""
    user = _u()
    if not ((user or {}).get("has_sig") or (user or {}).get("sig_png")):
        flash("Save your digital signature in your profile before signing several "
              "requests at once.", "error")
        return redirect(url_for("main.profile") + "#signature")
    ids = request.form.getlist("pr_id")
    if not ids:
        flash("Tick at least one request to sign.", "error")
        return redirect(url_for("approvals.index") + "#my-queue")
    if len(set(ids)) > porev.BULK_MAX:
        flash("Up to 50 requests are signed at a time — only the first 50 ticked were "
              "signed. Sign the rest from the queue again.", "warning")
    lang = _lang()
    results = porev.bulk_sign(ids, user, comment=request.form.get("comment", "").strip() or None,
                              ip=_ip())
    for r in results:
        r["text"] = _translate_msg(
            _BULK_OK.get(r["code"], "Approved.") if r["ok"] else
            _BULK_ERR.get(_code(r["code"]), "Could not approve (%s)." % r["code"]), lang)
    return render_template("approvals/porev_bulk_result.html", active="procurement",
                           results=results, signed=sum(1 for r in results if r["ok"]))


def form_prefill(copy_id, stock_id, spare_id=""):
    """(response, header, lines) for /new?copy=<pr id>, /new?stock=<material id>
    or /new?spare=<maintenance spare id>. `response` is never set today (a
    missing record is a 404); kept so the caller can still refuse the form."""
    if copy_id.isdigit():
        if not user_can("proc_view"):
            abort(403)
        from app.approvals import services as _svc
        # A plain requester copies only their own requests (they cannot see others').
        if not _svc.can_view_pr(_u(), _svc.pr_for_args({"pr_id": int(copy_id)})):
            abort(404)
        head, lines = porev.copy_prefill(int(copy_id))
        if head is None:
            abort(404)
        return None, head, lines
    head, lines, open_prs = (porev.spare_prefill(int(spare_id)) if spare_id.isdigit()
                             else porev.stock_prefill(int(stock_id)))
    if head is None:
        abort(404)
    if open_prs:
        # A warning, not a block: the open request may be a stale draft, or not
        # enough. The reader decides; the numbers are plain text (no proc_view needed).
        flash("An open purchase request for this item already exists — check it before "
              "raising another one: "
              + ", ".join(p["pr_no"] or "#%s" % p["id"] for p in open_prs), "warning")
    return None, head, lines


# Template helpers for the partials (approvals/_porev_*.html).
@bp.app_template_global()
def porev_view(pr_id):
    return porev.revision_view(pr_id, current_user())


@bp.app_template_global()
def porev_rev_queue():
    return porev.rev_queue(current_user())


@bp.app_template_global()
def porev_stage(stage):
    """A rung's name in the reader's language (the DOAM wording in English)."""
    from app.approvals import constants as C, services as svc
    lang = _lang()
    return (svc.labels(lang)["stage"].get(stage) if lang != "en" else None)         or C.stage_label(stage)


@bp.app_template_global()
def porev_low_stock():
    return porev.low_stock()


@bp.app_template_global()
def porev_spare(spare_id):
    return porev.spare_low(spare_id)
