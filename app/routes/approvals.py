"""
TC Platform — Procurement & Approvals blueprint (thin controllers).

Digital Purchase Request lifecycle: raise -> route through the threshold-driven
approval ladder -> each approver stamps their digital signature -> auto-generate
the Purchase Order. Every page requires auth + the right procurement permission;
approval actions are additionally gated by the per-stage authority check.
"""
import base64
import io

from flask import (Blueprint, render_template, request, redirect, url_for,
                   abort, flash, jsonify, send_file)

from app.auth import login_required, permission_required, current_user, user_can
from app.approvals import services as svc
from app.approvals import pdf as pdfgen
from app.approvals import constants as C

bp = Blueprint("approvals", __name__, url_prefix="/procurement")

_MAX_ATTACH = 3 * 1024 * 1024  # 3 MB per quotation file
_ATTACH_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xlsx", ".csv")


def _u():
    return current_user()


def _ip():
    return request.headers.get("X-Forwarded-For", request.remote_addr or "")


def _save_attachments(pr_id, user):
    """Persist any files posted under name='attachments' on the PR form.
    Returns the count saved; silently skips empty / oversized / unsupported files."""
    saved = 0
    for file in request.files.getlist("attachments"):
        if not file or not file.filename:
            continue
        if not file.filename.lower().endswith(_ATTACH_EXT):
            continue
        raw = file.read()
        if not raw or len(raw) > _MAX_ATTACH:
            continue
        b64 = base64.b64encode(raw).decode("ascii")
        svc.add_attachment(pr_id, file.filename,
                           file.mimetype or "application/octet-stream",
                           b64, len(raw), user, ip=_ip())
        saved += 1
    return saved


# --------------------------------------------------------------------------
# Landing — KPIs + my approval queue + recent requests
# --------------------------------------------------------------------------
@bp.route("/")
@login_required
@permission_required("proc_view")
def index():
    user = _u()
    try:  # opportunistically flag overdue approvals (no cron needed)
        svc.run_escalations()
    except Exception:
        pass
    return render_template(
        "approvals/index.html", active="procurement",
        kpi=svc.counts(user), queue=svc.my_queue(user),
        recent=svc.list_prs(limit=12),
        can_create=user_can("proc_create"))


@bp.route("/analytics")
@login_required
@permission_required("proc_view")
def analytics():
    return render_template("approvals/analytics.html", active="procurement",
                           a=svc.analytics_summary())


@bp.route("/list")
@login_required
@permission_required("proc_view")
def listing():
    status = request.args.get("status", "all")
    mine = request.args.get("mine") == "1"
    user = _u()
    prs = svc.list_prs(status=status, requester=(user["username"] if mine else None))
    return render_template("approvals/list.html", active="procurement",
                           prs=prs, status=status, mine=mine, statuses=C.PR_STATUSES)


# --------------------------------------------------------------------------
# New request
# --------------------------------------------------------------------------
@bp.route("/new", methods=["GET"])
@login_required
@permission_required("proc_create")
def new():
    prefill = {}
    ticket_id = request.args.get("from_ticket")
    if ticket_id and ticket_id.isdigit():
        prefill = svc.ticket_prefill(int(ticket_id)) or {}
    return render_template("approvals/new.html", active="procurement",
                           vendors=svc.list_vendors(), units=C.UNITS,
                           currencies=C.CURRENCIES, payments=C.PAYMENT_CONDITIONS,
                           deliveries=C.DELIVERY_CONDITIONS, departments=svc.list_departments(),
                           matrix=C.APPROVAL_MATRIX, ladder=C.LADDER,
                           dept_matrices=svc.all_dept_matrices(),
                           stage_labels=C.STAGE_LABELS, prefill=prefill,
                           can_price=_can_price())


def _can_price():
    """Only Purchasing (proc_purchasing) may set commercial values. Requesters
    state WHAT they need; pricing is entered later, at the pricing gate."""
    return user_can("proc_purchasing")


def _parse_header(f, can_price=False):
    h = {
        "title": f.get("title", "").strip(),
        "request_for": f.get("request_for", "").strip(),
        "department": f.get("department", "").strip(),
        "request_date": f.get("request_date", "").strip(),
        "currency": f.get("currency", "EGP"),
        "vendor": f.get("vendor", "").strip(),               # a *suggested* vendor is allowed
        "payment_condition": f.get("payment_condition", "").strip(),
        "delivery_condition": f.get("delivery_condition", "").strip(),
        "req_del_date": f.get("req_del_date", "").strip(),
        "asset_code": f.get("asset_code", "").strip(),
        "notes": f.get("notes", "").strip(),
        "tax_rate": f.get("tax_rate", "").strip() or 0,
    }
    if not can_price:
        # Server-side commercial lockout (defence-in-depth — independent of the UI):
        # a requester can never set the payment terms or tax, whatever is POSTed.
        h["payment_condition"] = ""
        h["tax_rate"] = 0
    return h


def _parse_items(f, can_price=False):
    items = []
    names = f.getlist("item[]"); descs = f.getlist("description[]")
    units = f.getlist("unit[]"); qtys = f.getlist("qty[]")
    stocks = f.getlist("current_stock[]"); prices = f.getlist("unit_price[]")
    notes = f.getlist("item_notes[]")
    for i in range(len(names)):
        if not (names[i] or "").strip():
            continue
        items.append({
            "item": names[i].strip(),
            "description": descs[i].strip() if i < len(descs) else "",
            "unit": units[i] if i < len(units) else "Pcs",
            "qty": qtys[i] if i < len(qtys) else 0,
            "current_stock": stocks[i] if i < len(stocks) else 0,
            # Commercial lockout: unit price is forced to 0 for requesters, no
            # matter what the form (or a hand-crafted request) sends.
            "unit_price": (prices[i] if i < len(prices) else 0) if can_price else 0,
            "notes": notes[i] if i < len(notes) else "",
        })
    return items


@bp.route("/new", methods=["POST"])
@login_required
@permission_required("proc_create")
def create():
    can_price = _can_price()
    header = _parse_header(request.form, can_price)
    items = _parse_items(request.form, can_price)
    if not header["title"] or not items:
        flash("A title and at least one line item are required.", "error")
        return redirect(url_for("approvals.new"))
    submit = request.form.get("action") != "draft"
    # priced=None -> inferred from the total: a requester's locked (zero-value)
    # request is 'unpriced' and routed to the pricing gate; a Purchasing-priced
    # one is 'priced'.
    pr_id, pr_no = svc.create_pr(header, items, _u(), ip=_ip(), submit=submit)
    n = _save_attachments(pr_id, _u())
    flash(f"Purchase request {pr_no} created." + (f" {n} file(s) attached." if n else ""),
          "success")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/edit", methods=["GET"])
@login_required
@permission_required("proc_create")
def edit(pr_id):
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    pr = bundle["pr"]
    if pr["status"] not in ("draft", "rejected"):
        flash("Only draft or rejected requests can be edited.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    if pr["requester"] != (_u() or {}).get("username") and not user_can("proc_admin"):
        abort(403)
    prefill = {"title": pr.get("title"), "request_for": pr.get("request_for"),
               "department": pr.get("department"), "notes": pr.get("notes"),
               "vendor": pr.get("vendor"), "payment_condition": pr.get("payment_condition"),
               "delivery_condition": pr.get("delivery_condition"), "currency": pr.get("currency"),
               "req_del_date": pr.get("req_del_date"), "asset_code": pr.get("asset_code"),
               "tax_rate": pr.get("tax_rate")}
    return render_template("approvals/new.html", active="procurement",
                           vendors=svc.list_vendors(), units=C.UNITS,
                           currencies=C.CURRENCIES, payments=C.PAYMENT_CONDITIONS,
                           deliveries=C.DELIVERY_CONDITIONS, departments=svc.list_departments(),
                           matrix=C.APPROVAL_MATRIX, ladder=C.LADDER,
                           dept_matrices=svc.all_dept_matrices(),
                           stage_labels=C.STAGE_LABELS, prefill=prefill,
                           editing=pr, edit_items=bundle["items"],
                           can_price=_can_price())


@bp.route("/pr/<int:pr_id>/edit", methods=["POST"])
@login_required
@permission_required("proc_create")
def edit_save(pr_id):
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    if bundle["pr"]["requester"] != (_u() or {}).get("username") and not user_can("proc_admin"):
        abort(403)
    can_price = _can_price()
    header = _parse_header(request.form, can_price)
    items = _parse_items(request.form, can_price)
    if not header["title"] or not items:
        flash("A title and at least one line item are required.", "error")
        return redirect(url_for("approvals.edit", pr_id=pr_id))
    ok, msg = svc.update_pr(pr_id, header, items, _u(), ip=_ip(), can_price=can_price)
    if not ok:
        flash(f"Could not save ({msg}).", "error")
        return redirect(url_for("approvals.edit", pr_id=pr_id))
    _save_attachments(pr_id, _u())
    if request.form.get("action") != "draft":
        svc.submit_pr(pr_id, _u(), ip=_ip())
        flash("Saved and submitted for approval.", "success")
    else:
        flash("Draft saved.", "success")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


# --------------------------------------------------------------------------
# Detail + actions
# --------------------------------------------------------------------------
@bp.route("/pr/<int:pr_id>")
@login_required
@permission_required("proc_view")
def detail(pr_id):
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    user = _u()
    pr = bundle["pr"]
    # The current rung may hold several parallel steps; find the one this user
    # can act on (if any), and the labels of everyone currently on the rung.
    actionable = None
    current_stages = []
    if pr["status"] == "pending":
        cur = [s for s in bundle["steps"]
               if s["seq"] == pr["current_seq"] and s["status"] == "pending"]
        current_stages = [s["stage"] for s in cur]
        actionable = next((s for s in cur if svc.can_act(user, s["stage"])), None)
    # Later approver waiting their turn (can't act until earlier rungs finish)?
    queued = False
    if pr["status"] == "pending" and not actionable:
        for s in bundle["steps"]:
            if s["status"] == "pending" and s["seq"] != pr["current_seq"] \
               and svc.can_act(user, s["stage"]):
                queued = True
                break
    has_sig = bool((user or {}).get("sig_png"))
    cur_label = " + ".join(C.stage_label(s) for s in current_stages) if current_stages else None
    can_purchasing = user_can("proc_purchasing")
    is_priced = (pr.get("pricing_status") or "priced") == "priced"
    # Purchasing can enter pricing while the PR is still being decided.
    needs_pricing = (can_purchasing and not is_priced
                     and pr["status"] in ("draft", "rejected", "pending"))
    # Requesters don't see commercial figures until Purchasing has priced the PR.
    show_commercial = is_priced or can_purchasing
    return render_template("approvals/detail.html", active="procurement",
                           b=bundle, pr=pr, actionable=actionable, has_sig=has_sig,
                           stage_label=C.stage_label, queued=queued,
                           current_stage_label=cur_label, amounts=svc.pr_amounts(pr),
                           match=svc.three_way_match(pr_id),
                           quote_cmp=svc.quote_comparison(bundle.get("quotes", [])),
                           budget=svc.budget_status(pr.get("department")),
                           can_purchasing=can_purchasing, is_priced=is_priced,
                           needs_pricing=needs_pricing, show_commercial=show_commercial,
                           currencies=C.CURRENCIES, payments=C.PAYMENT_CONDITIONS,
                           is_owner=(pr["requester"] == (user or {}).get("username")))


# --------------------------------------------------------------------------
# Multi-quote comparison
# --------------------------------------------------------------------------
@bp.route("/pr/<int:pr_id>/quote", methods=["POST"])
@login_required
@permission_required("proc_create")
def add_quote(pr_id):
    if not svc.get_pr(pr_id):
        abort(404)
    f = request.form
    fn = ct = b64 = None
    file = request.files.get("file")
    if file and file.filename:
        if not file.filename.lower().endswith(_ATTACH_EXT):
            flash("Unsupported quote file type.", "error")
            return redirect(url_for("approvals.detail", pr_id=pr_id))
        raw = file.read()
        if len(raw) > _MAX_ATTACH:
            flash("Quote file too large (max 3 MB).", "error")
            return redirect(url_for("approvals.detail", pr_id=pr_id))
        fn, ct, b64 = file.filename, (file.mimetype or "application/octet-stream"), \
            base64.b64encode(raw).decode("ascii")
    if not f.get("vendor") or not f.get("amount"):
        flash("Vendor and amount are required for a quote.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    svc.add_quote(pr_id, {
        "vendor": f.get("vendor", "").strip(), "amount": f.get("amount"),
        "currency": f.get("currency", "EGP"), "lead_time_days": f.get("lead_time_days"),
        "warranty": f.get("warranty", "").strip(), "notes": f.get("notes", "").strip(),
    }, _u(), filename=fn, content_type=ct, content_b64=b64, ip=_ip())
    flash("Quote added.", "success")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/quote/<int:quote_id>/choose", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def choose_quote(pr_id, quote_id):
    ok, msg = svc.choose_quote(pr_id, quote_id, _u(), ip=_ip())
    flash("Quote selected." if ok else f"Could not select quote ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/quote/<int:quote_id>/file")
@login_required
@permission_required("proc_view")
def quote_file(quote_id):
    q = svc.get_quote(quote_id)
    if not q or not q["content_b64"]:
        abort(404)
    try:
        raw = base64.b64decode(q["content_b64"])
    except Exception:
        abort(404)
    return send_file(io.BytesIO(raw), mimetype=q["content_type"] or "application/octet-stream",
                     as_attachment=True, download_name=q["filename"] or "quote")


# --------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------
@bp.route("/budgets", methods=["GET"])
@login_required
@permission_required("proc_view")
def budgets():
    rows = svc.list_budgets()
    for b in rows:
        st = svc.budget_status(b["department"], b["period"])
        b["spent"] = st["spent"] if st else 0
        b["remaining"] = st["remaining"] if st else None
        b["pct"] = st["pct"] if st else 0
    return render_template("approvals/budgets.html", active="procurement",
                           budgets=rows, can_manage=user_can("proc_admin"))


@bp.route("/budgets", methods=["POST"])
@login_required
@permission_required("proc_admin")
def save_budget():
    f = request.form
    if not f.get("department") or not f.get("amount"):
        flash("Department and amount are required.", "error")
    else:
        svc.set_budget(f.get("department").strip(), f.get("amount"),
                       period=f.get("period", "").strip() or None,
                       currency=f.get("currency", "EGP"), user=_u())
        flash("Budget saved.", "success")
    return redirect(url_for("approvals.budgets"))


# --------------------------------------------------------------------------
# Delegations
# --------------------------------------------------------------------------
@bp.route("/delegations", methods=["GET"])
@login_required
@permission_required("proc_view")
def delegations():
    from app.db import get_db
    conn = get_db()
    try:
        users = [dict(r) for r in conn.execute(
            "SELECT username, full_name, role FROM users WHERE is_active=1 ORDER BY username").fetchall()]
    finally:
        conn.close()
    return render_template("approvals/delegations.html", active="procurement",
                           delegations=svc.list_delegations(), users=users,
                           can_manage=user_can("proc_admin"))


@bp.route("/delegations", methods=["POST"])
@login_required
@permission_required("proc_admin")
def add_delegation():
    f = request.form
    ok, msg = svc.add_delegation(f.get("from_user"), f.get("to_user"),
                                 f.get("from_date"), f.get("to_date"),
                                 f.get("note", "").strip(), user=_u())
    flash("Delegation added." if ok else f"Could not add delegation ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.delegations"))


@bp.route("/delegations/<int:deleg_id>/revoke", methods=["POST"])
@login_required
@permission_required("proc_admin")
def revoke_delegation(deleg_id):
    svc.revoke_delegation(deleg_id)
    flash("Delegation revoked.", "success")
    return redirect(url_for("approvals.delegations"))


@bp.route("/jobs/escalate", methods=["POST"])
@login_required
@permission_required("proc_admin")
def escalate_now():
    n = svc.run_escalations()
    flash(f"{n} overdue approval(s) escalated." if n else "No overdue approvals.", "success")
    return redirect(url_for("approvals.index"))


@bp.route("/pr/<int:pr_id>/approve", methods=["POST"])
@login_required
@permission_required("proc_approve")
def approve(pr_id):
    ok, msg = svc.act_on_step(pr_id, _u(), "approve",
                              comment=request.form.get("comment", "").strip() or None,
                              ip=_ip())
    if not ok:
        flash({"forbidden": "You are not authorised for this approval stage.",
               "not_pending": "This request is not awaiting approval.",
               "needs_pricing": "Enter the pricing before approving the Purchasing stage — "
                                "the request has no commercial value yet.",
               "no_active_step": "No active approval step."}.get(msg, f"Could not approve ({msg})."),
              "error")
    else:
        flash({"advanced": "Approved — routed to the next approver.",
               "partial": "Approved & signed. Waiting on the co-approver at this stage.",
               "approved": "Final approval complete. Purchase Order drafted."}.get(msg, "Approved."),
              "success")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/reject", methods=["POST"])
@login_required
@permission_required("proc_approve")
def reject(pr_id):
    reason = request.form.get("comment", "").strip()
    ok, msg = svc.act_on_step(pr_id, _u(), "reject", comment=reason, ip=_ip())
    flash("Request rejected and returned to the requester." if ok
          else f"Could not reject ({msg}).", "warning" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/price", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def price(pr_id):
    """Purchasing enters the commercial value at the pricing gate."""
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    f = request.form
    prices = {}
    for it in bundle["items"]:
        raw = f.get("price_%s" % it["id"])
        if raw is not None and str(raw).strip() != "":
            prices[it["id"]] = raw
    meta = {"tax_rate": f.get("tax_rate", "").strip(),
            "payment_condition": f.get("payment_condition", "").strip(),
            "vendor": f.get("vendor", "").strip(),
            "currency": f.get("currency", "").strip()}
    ok, msg = svc.price_pr(pr_id, prices, meta, _u(), ip=_ip())
    flash("Pricing saved — the request now carries its commercial value and any "
          "value-based approvals have joined the ladder." if ok
          else {"locked": "This request can no longer be priced."}.get(
              msg, f"Could not save pricing ({msg})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/submit", methods=["POST"])
@login_required
@permission_required("proc_create")
def submit(pr_id):
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    # only the owner (or an admin) may put a request into circulation
    if bundle["pr"]["requester"] != (_u() or {}).get("username") and not user_can("proc_admin"):
        abort(403)
    ok, msg = svc.submit_pr(pr_id, _u(), ip=_ip())
    flash("Submitted for approval." if ok else f"Could not submit ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/issue-po", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def issue_po(pr_id):
    ok, res = svc.issue_po(pr_id, _u(), ip=_ip(), force=user_can("proc_admin"))
    flash(f"Purchase Order {res} issued." if ok else
          {"over_budget": "The department budget for this period is exceeded — "
                          "an administrator must issue this PO (or raise the budget).",
           }.get(res, f"Could not issue PO ({res})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/email-po", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def email_po(pr_id):
    ok, res = svc.email_po_to_vendor(pr_id, _u(), ip=_ip())
    if not ok:
        flash({"no_vendor_email": "No email on file for this vendor — add one in Vendors.",
               "not_approved": "The PO isn't ready to send yet."}.get(res, f"Could not send ({res})."),
              "error")
    elif res == "sent":
        flash("Purchase Order emailed to the vendor.", "success")
    else:
        flash("Email logged (set the SMTP env vars to actually send).", "warning")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/receive", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def receive(pr_id):
    f = request.form
    ids = f.getlist("recv_item_id[]")
    qtys = f.getlist("recv_qty[]")
    receipts = {}
    for i, iid in enumerate(ids):
        try:
            q = float(qtys[i]) if i < len(qtys) and qtys[i] else 0
        except ValueError:
            q = 0
        if q > 0:
            receipts[iid] = q
    if not receipts:
        flash("Enter a received quantity on at least one line.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    ok, msg = svc.receive_items(pr_id, receipts, _u(),
                                notes=f.get("notes", "").strip() or None, ip=_ip())
    flash({"received": "Delivery fully confirmed.", "partial": "Partial receipt recorded."}.get(msg, "Receipt recorded.")
          if ok else f"Could not record receipt ({msg}).", "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/invoice", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def add_invoice(pr_id):
    if not svc.get_pr(pr_id):
        abort(404)
    f = request.form
    if not f.get("invoice_no") or not f.get("amount"):
        flash("Invoice number and amount are required.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    fn = ct = b64 = None
    file = request.files.get("file")
    if file and file.filename:
        if not file.filename.lower().endswith(_ATTACH_EXT):
            flash("Unsupported invoice file type.", "error")
            return redirect(url_for("approvals.detail", pr_id=pr_id))
        raw = file.read()
        if len(raw) > _MAX_ATTACH:
            flash("Invoice file too large (max 3 MB).", "error")
            return redirect(url_for("approvals.detail", pr_id=pr_id))
        fn, ct, b64 = file.filename, (file.mimetype or "application/octet-stream"), \
            base64.b64encode(raw).decode("ascii")
    ok, msg = svc.add_invoice(pr_id, {
        "invoice_no": f.get("invoice_no", "").strip(), "invoice_date": f.get("invoice_date", "").strip(),
        "amount": f.get("amount"), "tax": f.get("tax"), "notes": f.get("notes", "").strip(),
    }, _u(), filename=fn, content_type=ct, content_b64=b64, ip=_ip())
    flash("Invoice recorded and matched." if ok else
          {"not_invoicable": "Invoices can be recorded once the request is approved / ordered.",
           "duplicate_invoice": "An invoice with this number is already recorded on this request.",
           }.get(msg, f"Could not record invoice ({msg})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/invoice/<int:inv_id>/file")
@login_required
@permission_required("proc_view")
def invoice_file(inv_id):
    iv = svc.get_invoice(inv_id)
    if not iv or not iv["content_b64"]:
        abort(404)
    try:
        raw = base64.b64decode(iv["content_b64"])
    except Exception:
        abort(404)
    return send_file(io.BytesIO(raw), mimetype=iv["content_type"] or "application/octet-stream",
                     as_attachment=True, download_name=iv["filename"] or "invoice")


@bp.route("/pr/<int:pr_id>/payment", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def add_payment(pr_id):
    if not svc.get_pr(pr_id):
        abort(404)
    f = request.form
    ok, res = svc.add_payment(pr_id, {
        "amount": f.get("amount"), "method": f.get("method", "").strip(),
        "reference": f.get("reference", "").strip(), "paid_at": f.get("paid_at", "").strip(),
        "invoice_id": f.get("invoice_id"), "notes": f.get("notes", "").strip(),
    }, _u(), ip=_ip(), force=user_can("proc_admin"))
    flash(f"Payment recorded ({res})." if ok else
          {"not_payable": "Payments start once the Purchase Order is issued.",
           "match_blocked": "Payment blocked: the 3-way match shows over-billing "
                            "(invoice exceeds the PO or the received value). Resolve "
                            "the mismatch first — an administrator can override.",
           "over_payment": "This payment would exceed the PO total. Check the amount — "
                           "an administrator can override if intentional.",
           }.get(res, f"Could not record payment ({res})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/close", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def close(pr_id):
    ok, msg = svc.close_pr(pr_id, _u(), ip=_ip())
    flash("Request closed." if ok else f"Could not close ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/cancel", methods=["POST"])
@login_required
@permission_required("proc_create")
def cancel(pr_id):
    ok, msg = svc.cancel_pr(pr_id, _u(), ip=_ip(),
                            is_purchasing=user_can("proc_purchasing"),
                            is_admin=user_can("proc_admin"))
    flash("Request cancelled." if ok else
          {"forbidden": "Only the requester (or Purchasing/an admin) can cancel this request.",
           "needs_admin": "This request is already approved — only an administrator can cancel it.",
           "already_received": "Goods were already received against this order — close it instead of cancelling.",
           }.get(msg, f"Could not cancel ({msg})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


# --------------------------------------------------------------------------
# PDFs
# --------------------------------------------------------------------------
@bp.route("/pr/<int:pr_id>/pdf")
@login_required
@permission_required("proc_view")
def pr_pdf(pr_id):
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    try:
        data = pdfgen.pr_pdf(bundle)
    except Exception:
        return jsonify(error="PDF support unavailable."), 500
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=False,
                     download_name=f"{bundle['pr'].get('pr_no', 'PR')}.pdf")


@bp.route("/pr/<int:pr_id>/po.pdf")
@login_required
@permission_required("proc_view")
def po_pdf(pr_id):
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    if bundle["pr"]["status"] not in ("approved", "po_issued", "closed"):
        abort(400, "The Purchase Order is available only after full approval.")
    try:
        data = pdfgen.po_pdf(bundle)
    except Exception:
        return jsonify(error="PDF support unavailable."), 500
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=False,
                     download_name=f"{bundle['pr'].get('po_no', 'PO')}.pdf")


# --------------------------------------------------------------------------
# Attachments (vendor quotations)
# --------------------------------------------------------------------------
@bp.route("/pr/<int:pr_id>/attach", methods=["POST"])
@login_required
@permission_required("proc_create")
def attach(pr_id):
    if not svc.get_pr(pr_id):
        abort(404)
    file = request.files.get("file")
    if not file or not file.filename:
        flash("No file selected.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    name = file.filename
    if not name.lower().endswith(_ATTACH_EXT):
        flash("Unsupported file type.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    raw = file.read()
    if len(raw) > _MAX_ATTACH:
        flash("File too large (max 3 MB).", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    b64 = base64.b64encode(raw).decode("ascii")
    svc.add_attachment(pr_id, name, file.mimetype or "application/octet-stream",
                       b64, len(raw), _u(), ip=_ip())
    flash("Quotation attached.", "success")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/attachment/<int:att_id>")
@login_required
@permission_required("proc_view")
def download_attachment(att_id):
    row = svc.get_attachment(att_id)
    if not row:
        abort(404)
    try:
        raw = base64.b64decode(row["content_b64"])
    except Exception:
        abort(404)
    return send_file(io.BytesIO(raw), mimetype=row["content_type"] or "application/octet-stream",
                     as_attachment=True, download_name=row["filename"] or "attachment")


# --------------------------------------------------------------------------
# Vendors
# --------------------------------------------------------------------------
@bp.route("/vendors", methods=["GET"])
@login_required
@permission_required("proc_view")
def vendors():
    return render_template("approvals/vendors.html", active="procurement",
                           vendors=svc.list_vendors(active_only=False),
                           can_manage=user_can("proc_purchasing"))


@bp.route("/vendors", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def create_vendor():
    data = {k: request.form.get(k, "").strip() for k in
            ("name", "contact_person", "phone", "email", "address",
             "payment_terms", "category", "rating", "notes")}
    if not data["name"]:
        flash("Vendor name is required.", "error")
    else:
        svc.create_vendor(data, _u(), ip=_ip())
        flash("Vendor saved.", "success")
    return redirect(url_for("approvals.vendors"))


# --------------------------------------------------------------------------
# Settings — responsibility (approval) matrix per department
# --------------------------------------------------------------------------
@bp.route("/settings", methods=["GET"])
@login_required
@permission_required("proc_admin")
def settings():
    depts = svc.list_departments()
    dept = request.args.get("department") or (depts[0] if depts else "")
    return render_template("approvals/settings.html", active="procurement",
                           departments=depts, department=dept,
                           matrix=svc.get_dept_matrix(dept), ladder=C.LADDER,
                           stage_labels=C.STAGE_LABELS, default_matrix=C.APPROVAL_MATRIX,
                           is_builtin=(dept in C.DEPARTMENTS),
                           has_custom=(dept in svc.all_dept_matrices()))


@bp.route("/settings", methods=["POST"])
@login_required
@permission_required("proc_admin")
def save_settings():
    f = request.form
    dept = (f.get("new_department") or "").strip() or (f.get("department") or "").strip()
    if not dept:
        flash("Choose or name a department.", "error")
        return redirect(url_for("approvals.settings"))
    rows = []
    for stage in C.LADDER:                       # canonical order preserved
        if f.get(f"inc_{stage}") == "1":
            try:
                thr = float(f.get(f"thr_{stage}") or 0)
            except ValueError:
                thr = 0
            rows.append({"stage": stage, "threshold": thr})
    # A brand-new department (Add form, no stages ticked) seeds with the default
    # ladder so it persists and is immediately usable; admins tune it afterwards.
    if not rows and f.get("new_department"):
        rows = [{"stage": s, "threshold": C.APPROVAL_MATRIX.get(s, 0)} for s in C.LADDER]
    ok, msg = svc.set_dept_matrix(dept, rows, _u())
    flash(f"Responsibility matrix saved for {dept}." if ok else f"Could not save ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.settings", department=dept))


@bp.route("/settings/delete", methods=["POST"])
@login_required
@permission_required("proc_admin")
def delete_settings():
    dept = (request.form.get("department") or "").strip()
    ok, was_builtin = svc.delete_dept_matrix(dept)
    if ok:
        flash(f"“{dept}” reset to the default approval route." if was_builtin
              else f"Removed “{dept}”.", "success")
    else:
        flash("Choose a department first.", "error")
    return redirect(url_for("approvals.settings"))


@bp.route("/settings/rename", methods=["POST"])
@login_required
@permission_required("proc_admin")
def rename_settings():
    old = (request.form.get("department") or "").strip()
    new = (request.form.get("rename_to") or "").strip()
    ok, msg = svc.rename_dept(old, new)
    if ok:
        flash(f"Renamed to “{new}”.", "success")
        return redirect(url_for("approvals.settings", department=new))
    reason = {"builtin": "Built-in departments can’t be renamed.",
              "exists": "A department with that name already exists.",
              "empty": "Enter a new name."}.get(msg, "Could not rename.")
    flash(reason, "error")
    return redirect(url_for("approvals.settings", department=old))


# ---------------------------------------------------------------------------
# Public signature verification — no login on purpose: the code printed on a
# PR/PO PDF must let anyone (an auditor, a vendor) confirm the document is
# genuine. Codes are unguessable (secrets.token_urlsafe) and the page reveals
# only what the PDF itself already shows.
# ---------------------------------------------------------------------------
@bp.route("/verify/<code>")
def verify_signature(code):
    ev, pr = svc.verify_sign_code(code)
    return render_template("approvals/verify.html", ev=ev, pr=pr), (200 if ev else 404)
