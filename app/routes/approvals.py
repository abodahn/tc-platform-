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
                   abort, flash, jsonify, send_file, current_app)

from app.auth import login_required, permission_required, current_user, user_can
# Module scope on purpose. This module used to import get_db inside each
# function that needed it, and twice a helper called it without that line: the
# NameError went into a bare `except` and the feature silently reported
# "nothing to show". One import here is the fix that a third caller cannot undo.
from app.db import get_db
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
    return render_template("approvals/analytics.html", active="proc_analytics",
                           a=svc.analytics_summary())


@bp.route("/export/<key>.csv")
@login_required
@permission_required("proc_view")
def export_csv(key):
    """Download a procurement report as CSV (register / spend / orders / payments)."""
    import csv  # local import keeps this addition self-contained
    headers, rows = svc.export_dataset(key)
    if headers is None:
        abort(404)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    # utf-8-sig (BOM) so Excel detects the encoding and renders Arabic text.
    data = ("\ufeff" + buf.getvalue()).encode("utf-8")
    return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True,
                     download_name=f"procurement_{key}.csv")


@bp.route("/list")
@login_required
@permission_required("proc_view")
def listing():
    status = request.args.get("status", "all")
    mine = request.args.get("mine") == "1"
    user = _u()
    prs = svc.list_prs(status=status, requester=(user["username"] if mine else None))
    return render_template("approvals/list.html", active="proc_list",
                           prs=prs, status=status, mine=mine, statuses=C.PR_STATUSES)


# --------------------------------------------------------------------------
# New request
# --------------------------------------------------------------------------
# Picker copy for the request form. The item rows are built by JavaScript AFTER
# app.js has swapped every data-i18n attribute, so a data-i18n here would never
# be translated — these are resolved server-side from the reader's language,
# the same approach the Workflow & Governance page uses for its prose.
_PICK_I18N = {
    "en": {"ph": "Search item or type your own",
           "browse": "Browse the item catalogue",
           "hint": "Type at least 2 letters, or choose a category to browse.",
           "none": "No match in the catalogue — you can type your own item.",
           "ask": "Request a new item"},
    "ar": {"ph": "ابحث عن صنف أو اكتب صنفك",
           "browse": "استعرض كتالوج الأصناف",
           "hint": "اكتب حرفين على الأقل، أو اختر فئة للاستعراض.",
           "none": "لا يوجد صنف مطابق في الكتالوج — يمكنك كتابة صنفك.",
           "ask": "طلب صنف جديد"},
    "tr": {"ph": "Kalem ara veya kendi kalemini yaz",
           "browse": "Kalem kataloğuna göz at",
           "hint": "En az 2 harf yazın veya göz atmak için bir kategori seçin.",
           "none": "Katalogda eşleşme yok — kendi kaleminizi yazabilirsiniz.",
           "ask": "Yeni kalem talebi"},
}


@bp.route("/new", methods=["GET"])
@login_required
@permission_required("proc_create")
def new():
    prefill = {}
    ticket_id = request.args.get("from_ticket")
    if ticket_id and ticket_id.isdigit():
        prefill = svc.ticket_prefill(int(ticket_id)) or {}
    return render_template("approvals/new.html", active="proc_new",
                           vendors=svc.list_vendors(), units=C.UNITS,
                           sales_orders=svc.list_sales_orders(),
                           forecasts=svc.list_forecasts(active_only=True),
                           currencies=C.CURRENCIES, payments=C.PAYMENT_CONDITIONS,
                           deliveries=C.DELIVERY_CONDITIONS, departments=svc.list_departments(),
                           matrix=C.ACTIVE_MATRIX, ladder=C.LADDER,
                           dept_matrices=svc.all_dept_matrices(),
                           stage_labels=C.STAGE_LABELS, prefill=prefill,
                           item_categories=svc.item_categories(),
                           pick_i18n=_PICK_I18N.get(
                               ((_u() or {}).get("lang_pref") or "en"), _PICK_I18N["en"]),
                           can_price=_can_price())


def _can_price():
    """NOBODY prices a request at creation/edit time — not Purchasing, not even
    a super admin. A requester states WHAT they need; the commercial value
    enters the system exactly once, at the Purchasing pricing gate
    (svc.price_pr), where the RFQ rule and the value ladder are enforced.
    Hardcoded False on purpose: this is a governance rule, not a permission."""
    return False


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
        # DOAM §4.2 — capital expenditure follows a different ladder. Anything
        # not explicitly marked capital is operating expenditure.
        # Normalised, not lower-cased: "capitol", "1", a blank and a capex
        # carrying an invisible character all used to store opex — the
        # weaker §4.2 ladder — with nothing saying so. The helper reports
        # whether it recognised the value; _submit_error refuses the
        # submit when it did not, so a downgrade cannot happen silently.
        "expenditure_kind": C.normalise_expenditure_kind(
            f.get("expenditure_kind"))[0],
        "_kind_recognised": C.normalise_expenditure_kind(
            f.get("expenditure_kind"))[1],
        # DOAM §5 — the cost object this spend belongs to.
        "so_no": f.get("so_no", "").strip(),
        "cost_center": f.get("cost_center", "").strip(),
        # DOAM §3.4 — "...OR AGREED FORECAST": the other reference the clause
        # accepts, for material bought ahead of a confirmed client order.
        "forecast_ref": f.get("forecast_ref", "").strip(),
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
    notes = f.getlist("item_notes[]"); spares = f.getlist("spare_id[]")
    item_ids = f.getlist("item_id[]"); vendors = f.getlist("vendor[]")
    for i in range(len(names)):
        if not (names[i] or "").strip():
            continue
        items.append({
            "item": names[i].strip(),
            "description": descs[i].strip() if i < len(descs) else "",
            "unit": units[i] if i < len(units) else "Pcs",
            "qty": qtys[i] if i < len(qtys) else 0,
            "current_stock": stocks[i] if i < len(stocks) else 0,
            # One requisition, several suppliers: each line may name its own.
            # Left blank it stays blank here and services.py falls back to the
            # header vendor — the behaviour every existing request relies on.
            "vendor": vendors[i].strip() if i < len(vendors) else "",
            # Commercial lockout: unit price is forced to 0 for requesters, no
            # matter what the form (or a hand-crafted request) sends.
            "unit_price": (prices[i] if i < len(prices) else 0) if can_price else 0,
            "notes": notes[i] if i < len(notes) else "",
            # cross-module mesh: the picked spare part (mnt_spare_parts.id) —
            # its goods receipt will post straight back into that spare's stock
            "spare_id": spares[i] if i < len(spares) else "",
            # OPTIONAL catalogue link (proc_items.id). Blank on a free-text line,
            # which stays a first-class way to raise a request.
            "item_id": item_ids[i] if i < len(item_ids) else "",
        })
    return items


@bp.route("/api/spares")
@login_required
@permission_required("proc_view")
def api_spares():
    """Live spare-part search for the PR form: type-ahead over the maintenance
    spare-parts warehouse. Returns code/name/unit and REAL current availability
    so requesters stop typing stock numbers by hand. No costs are exposed."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    from app.db import get_db
    conn = get_db()
    try:
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT id, code, name, uom, stock_qty, reserved_qty, reorder_level "
            "FROM mnt_spare_parts WHERE is_active=1 AND (code LIKE ? OR name LIKE ?) "
            "ORDER BY code LIMIT 10", (like, like)).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    return jsonify({"results": [{
        "id": r["id"], "code": r["code"], "name": r["name"] or "",
        "unit": r["uom"] or "Pcs",
        "available": round(float(r["stock_qty"] or 0) - float(r["reserved_qty"] or 0), 2),
        "low": (float(r["stock_qty"] or 0) - float(r["reserved_qty"] or 0))
               <= float(r["reorder_level"] or 0),
    } for r in rows]})


@bp.route("/api/items")
@login_required
@permission_required("proc_view")
def api_items():
    """Type-ahead over the procurement item catalogue for the PR line picker.

    Returns code / name / unit / category ONLY. There is no cost field here and
    there must never be one: a requester can call this endpoint, and the
    commercial value enters the platform exactly once, at the Purchasing pricing
    gate. Capped and paginated — 19k rows never leave in one response."""
    results, has_more = svc.search_items(
        request.args.get("q"), request.args.get("cat"),
        offset=request.args.get("offset", 0, type=int) or 0)
    return jsonify({"results": results, "has_more": has_more})


# --------------------------------------------------------------------------
# New-item requests — the missing door into the catalogue
#
# WHY A SEPARATE PAGE AND NOT A PANEL ON THE PR FORM: the requester is half way
# through typing a purchase request that has NO id yet — it exists only in the
# browser. Any POST from that form (or a navigation to a request form) throws
# the draft away, and an in-page modal means re-implementing a form, its
# validation and its error states in JavaScript. So the picker's "no match"
# message links here with target="_blank": the draft sits untouched in its own
# tab, the request is a normal server-rendered form that works with no JS at
# all, and the requester closes the tab and carries on. The PR line stays free
# text and submits exactly as it does today — this path never gates anything.
# --------------------------------------------------------------------------
@bp.route("/item-request", methods=["GET"])
@login_required
@permission_required("proc_create")
def item_request_new():
    from app.approvals import item_requests as ir
    u = _u() or {}
    return render_template("approvals/item_request_new.html", active="proc_item_requests",
                           units=C.UNITS, item_categories=svc.item_categories(),
                           prefill={"name": (request.args.get("name") or "")[:200],
                                    "unit": (request.args.get("unit") or "")[:40],
                                    "pr_id": request.args.get("pr", ""),
                                    "line_no": request.args.get("line", "")},
                           sent=request.args.get("sent") == "1",
                           mine=ir.my_requests(u.get("username")))


@bp.route("/item-request", methods=["POST"])
@login_required
@permission_required("proc_create")
def item_request_create():
    """A requester asks for an item. NO price is read from this form and none is
    stored — proc_item_requests has no price column, so a POSTed one is ignored."""
    from app.approvals import item_requests as ir
    cat = (request.form.get("category_code") or "").strip()
    names = dict(svc.item_categories())
    data = dict(request.form)
    data["category_name"] = names.get(cat, "")
    req_id, err = ir.create_item_request(data, _u(), ip=_ip())
    if err:
        flash("Type the name of the item you need.", "error")
        return redirect(url_for("approvals.item_request_new",
                                name=request.form.get("name", "")))
    return redirect(url_for("approvals.item_request_new", sent=1))


@bp.route("/item-requests", methods=["GET"])
@login_required
@permission_required("proc_purchasing")
def item_requests():
    from app.approvals import item_requests as ir
    status = request.args.get("status", "pending")
    return render_template("approvals/item_requests.html", active="proc_item_requests",
                           status=status, requests=ir.list_requests(status),
                           counts=ir.counts(), refused=None)


def _queue(refused=None, code=200):
    from app.approvals import item_requests as ir
    status = request.args.get("status", "pending")
    return render_template("approvals/item_requests.html", active="proc_item_requests",
                           status=status, requests=ir.list_requests(status),
                           counts=ir.counts(), refused=refused), code


@bp.route("/item-requests/<int:req_id>/approve", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def item_request_approve(req_id):
    """Approve by TYPING the ERP code. Refuses a code the catalogue already
    holds and re-renders the queue showing the item that holds it — nothing is
    written. A similar NAME only warns: telling one part from another is the
    judgement Purchasing are here to make, not something a LIKE query decides."""
    from app.approvals import item_requests as ir
    ok, msg, existing = ir.approve_item_request(
        req_id, request.form.get("code"), _u(), ip=_ip())
    if not ok:
        if msg == "duplicate_code":
            return _queue(refused={"code": (request.form.get("code") or "").strip(),
                                   "item": existing, "req_id": req_id})
        flash({"code_required": "Type the ERP code before approving.",
               "not_pending": "That request has already been decided.",
               "not_found": "That request no longer exists."}.get(msg, msg), "error")
        return redirect(url_for("approvals.item_requests"))
    flash("Item added to the catalogue." + (
        "  Similar items were already on file — check it is not the same part "
        "under another name." if msg == "approved_similar" else ""), "success")
    return redirect(url_for("approvals.item_requests"))


@bp.route("/item-requests/<int:req_id>/reject", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def item_request_reject(req_id):
    from app.approvals import item_requests as ir
    ok, msg = ir.reject_item_request(req_id, request.form.get("note"), _u(), ip=_ip())
    flash({"reason_required": "Give a reason so the requester knows what to do next.",
           "not_pending": "That request has already been decided.",
           "not_found": "That request no longer exists.",
           "rejected": "Request rejected. The purchase-request line is untouched."
           }.get(msg, msg), "success" if ok else "error")
    return redirect(url_for("approvals.item_requests"))


# --------------------------------------------------------------------------
# Item master — the store's door. Browse, add, edit, retire, one row at a time.
# The bulk import below is a different door for a different job.
# --------------------------------------------------------------------------
@bp.route("/items", methods=["GET"])
@login_required
@permission_required("proc_catalogue")
def items():
    from app.db import get_db
    from app.approvals import items_admin as IA
    q = (request.args.get("q") or "").strip()
    cat = (request.args.get("cat") or "").strip()
    active = request.args.get("active", "1")
    if active not in ("0", "1", "all"):
        active = "1"
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    per = 60
    conn = get_db()
    try:
        rows, total = IA.search_items(conn, q, cat, active, limit=per,
                                      offset=(page - 1) * per)
        cats = IA.categories(conn)
        edit_id = request.args.get("edit", type=int)
        editing = IA.get_item(conn, edit_id) if edit_id else None
        history = IA.item_history(conn, edit_id) if edit_id else []
    finally:
        conn.close()
    return render_template("approvals/items.html", active="procurement",
                           rows=rows, total=total, page=page, per=per,
                           q=q, cat=cat, cats=cats, active_filter=active,
                           editing=editing, history=history,
                           can_cost=user_can("proc_purchasing") or user_can("proc_admin"),
                           pages=max(1, (total + per - 1) // per))


@bp.route("/items/new", methods=["POST"])
@login_required
@permission_required("proc_catalogue")
def items_new():
    from app.db import get_db
    from app.approvals import items_admin as IA
    conn = get_db()
    try:
        ok, msg = IA.create_item(conn, request.form, _u(), _ip())
    finally:
        conn.close()
    flash(("Item %s added." % msg) if ok else
          {"code_required": "An item needs the code the ERP issued for it.",
           "name_required": "An item needs a name.",
           "duplicate_code": "That code is already in the catalogue — search for "
                             "it instead of adding it twice."}.get(msg, msg),
          "success" if ok else "error")
    return redirect(url_for("approvals.items", q=msg if ok else
                            (request.form.get("code") or "").strip()))


@bp.route("/items/<int:item_id>", methods=["POST"])
@login_required
@permission_required("proc_catalogue")
def items_edit(item_id):
    from app.db import get_db
    from app.approvals import items_admin as IA
    conn = get_db()
    try:
        ok, msg = IA.update_item(conn, item_id, request.form, _u(), _ip())
    finally:
        conn.close()
    flash({"updated": "Saved. Purchasing have been told.",
           "unchanged": "Nothing was changed.",
           "not_found": "That item no longer exists.",
           "code_required": "An item needs the code the ERP issued for it.",
           "name_required": "An item needs a name.",
           "duplicate_code": "Another item already carries that code."}.get(msg, msg),
          "success" if ok else "error")
    return redirect(url_for("approvals.items", q=(request.form.get("code") or "").strip()))


@bp.route("/items/<int:item_id>/active", methods=["POST"])
@login_required
@permission_required("proc_catalogue")
def items_active(item_id):
    from app.db import get_db
    from app.approvals import items_admin as IA
    conn = get_db()
    try:
        ok, msg = IA.set_item_active(conn, item_id, request.form.get("active"),
                                     _u(), _ip())
    finally:
        conn.close()
    flash({"retired": "Retired. It stays on every document that already used it.",
           "restored": "Back in use.",
           "unchanged": "Nothing was changed.",
           "not_found": "That item no longer exists."}.get(msg, msg),
          "success" if ok else "error")
    return redirect(request.referrer or url_for("approvals.items"))


# --------------------------------------------------------------------------
# Item catalogue — admin import (front door A; the CLI is front door B)
# --------------------------------------------------------------------------
@bp.route("/catalogue", methods=["GET"])
@login_required
@permission_required("proc_admin")
def catalogue():
    from app.db import get_db
    from app.approvals.catalogue import catalogue_stats
    conn = get_db()
    try:
        stats = catalogue_stats(conn)
    finally:
        conn.close()
    return render_template("approvals/catalogue.html", active="procurement",
                           stats=stats, result=None, parsed=None)


@bp.route("/catalogue/import", methods=["POST"])
@login_required
@permission_required("proc_admin")
def catalogue_import():
    """Upload an ERP item export and upsert it. Same parser + same upsert as
    scripts/import_items.py, so the two front doors cannot drift."""
    from app.db import get_db
    from app.approvals.catalogue import parse_workbook, upsert_items, catalogue_stats
    file = request.files.get("file")
    result = parsed = None
    if not file or not file.filename:
        flash("Choose a file to import.", "error")
    else:
        try:
            items, parsed = parse_workbook(io.BytesIO(file.read()))
        except Exception as exc:
            items, parsed = [], {"error": f"{type(exc).__name__}: {exc}", "rejects": [],
                                 "unmapped_units": {}}
        if parsed.get("error"):
            flash(parsed["error"], "error")
        else:
            conn = get_db()
            try:
                result = upsert_items(conn, items, _u(),
                                      source=file.filename[:120])
            except Exception as exc:
                # A second import running at the same time (a double-clicked
                # Import button is enough) hits the UNIQUE on code and raises.
                # On PostgreSQL that failure also aborts the transaction, so
                # roll back and say so instead of returning a 500 page: the
                # upsert is by code, so simply running the import again
                # reconciles whatever landed before the error.
                try:
                    conn.rollback()
                except Exception:
                    pass
                result = None
                flash(f"Import failed part-way ({type(exc).__name__}). Nothing was "
                      f"corrupted — run the import again to finish it.", "error")
            finally:
                conn.close()
            if result is not None:
                flash(f"{result['added']} added, {result['updated']} updated, "
                      f"{result['unchanged']} unchanged, "
                      f"{len(parsed.get('rejects') or [])} rejected.", "success")
    conn = get_db()
    try:
        stats = catalogue_stats(conn)
    finally:
        conn.close()
    return render_template("approvals/catalogue.html", active="procurement",
                           stats=stats, result=result, parsed=parsed)


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
    # Submitted separately (below) so a refusal — e.g. the requester is the only
    # possible signer of a rung and nothing sits above that role — reaches them
    # as a message instead of leaving a silent draft.
    pr_id, pr_no = svc.create_pr(header, items, _u(), ip=_ip(), submit=False)
    # Cross-module mesh: a PR raised from a maintenance ticket keeps a
    # persistent two-way link (ticket page lists it; goods receipt notifies
    # the ticket). The hidden from_ticket field is set by the prefill flow.
    ft = (request.form.get("from_ticket") or "").strip()
    if ft.isdigit():
        from app.db import get_db
        conn = get_db()
        try:
            trow = conn.execute("SELECT id, ticket_no FROM mnt_tickets WHERE id=?",
                                (int(ft),)).fetchone()
        except Exception:
            trow = None
        finally:
            conn.close()
        if trow:
            svc.link_source(pr_id, "maintenance", f"ticket:{trow['id']}", _u(), ip=_ip())
    n = _save_attachments(pr_id, _u())
    flash(f"Purchase request {pr_no} created." + (f" {n} file(s) attached." if n else ""),
          "success")
    if submit:
        ok, msg = svc.submit_pr(pr_id, _u(), ip=_ip())
        if not ok:
            flash(_submit_error(msg), "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


def _submit_error(msg):
    """Human-readable reason a submit was refused, in the reader's language for the
    one case the reader has to act on (no superior above their own role)."""
    ui = svc.labels((_u() or {}).get("lang_pref") or "en")["ui"]
    if msg == "no_eligible_approver":
        return ui["esc_blocked_flash"]
    if msg in ("cost_object_required", "so_unknown", "so_closed",
               "fc_unknown", "fc_expired", "fc_unapproved"):
        return ui[msg + "_flash"]
    return f"Could not submit ({msg})."


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
               "tax_rate": pr.get("tax_rate"),
               "expenditure_kind": pr.get("expenditure_kind"),
               "so_no": pr.get("so_no"), "cost_center": pr.get("cost_center"),
               "forecast_ref": pr.get("forecast_ref")}
    return render_template("approvals/new.html", active="proc_list",
                           vendors=svc.list_vendors(), units=C.UNITS,
                           sales_orders=svc.list_sales_orders(),
                           forecasts=svc.list_forecasts(active_only=True),
                           currencies=C.CURRENCIES, payments=C.PAYMENT_CONDITIONS,
                           deliveries=C.DELIVERY_CONDITIONS, departments=svc.list_departments(),
                           matrix=C.ACTIVE_MATRIX, ladder=C.LADDER,
                           dept_matrices=svc.all_dept_matrices(),
                           stage_labels=C.STAGE_LABELS, prefill=prefill,
                           editing=pr, edit_items=bundle["items"],
                           item_categories=svc.item_categories(),
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
        ok, msg = svc.submit_pr(pr_id, _u(), ip=_ip())
        flash("Saved and submitted for approval." if ok else _submit_error(msg),
              "success" if ok else "error")
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
    # Escalation stamp -> one readable sentence per step, in the reader's language
    # (the stored value is a role-key list, so it cannot use data-i18n).
    _lang = (user or {}).get("lang_pref") or "en"
    _ui = svc.labels(_lang)["ui"]
    for s in bundle["steps"]:
        if s.get("esc_from"):
            s["esc_why"] = (_ui["esc_from_to"] % {
                "from": svc.role_names(s["esc_from"], _lang),
                "to": svc.role_names(s["esc_role"], _lang)}) \
                if s.get("esc_role") else _ui["esc_stuck"]
    # The current rung may hold several parallel steps; find the one this user
    # can act on (if any), and the labels of everyone currently on the rung.
    actionable = None
    current_stages = []
    if pr["status"] == "pending":
        cur = [s for s in bundle["steps"]
               if s["seq"] == pr["current_seq"] and s["status"] == "pending"]
        current_stages = [s["stage"] for s in cur]
        # can_act_step, not can_act: an escalated rung is signed by the superior.
        actionable = next((s for s in cur if svc.can_act_step(user, s)), None)
    # Later approver waiting their turn (can't act until earlier rungs finish)?
    queued = False
    if pr["status"] == "pending" and not actionable:
        for s in bundle["steps"]:
            if s["status"] == "pending" and s["seq"] != pr["current_seq"] \
               and svc.can_act_step(user, s):
                queued = True
                break
    has_sig = bool((user or {}).get("sig_png"))
    # DOAM Table 5 — the English source wording behind the proc.act.* i18n keys,
    # so the action box says what THIS signature is before the client swaps in
    # the reader's language (and still says it if the key is ever missing).
    _act = (actionable or {}).get("action") or "approve"
    act_why = C.STEP_ACTION_WHY[_act]
    act_sign = "%s & sign" % C.STEP_ACTION_LABELS[_act]
    # DOAM Table 4 L2 — PD owns production/maintenance, SC-D owns operational and
    # inventory replenishment. Only shown when one of them was actually left off.
    # The sentence names the director who is actually ON the ladder, not the
    # classifier's verdict: dept_ladder applies the split to the DOAM floor and
    # then merges the department responsibility matrix back on top, which can
    # leave the OTHER director on. The verdict is only consulted to confirm the
    # split is WHY one of them is missing (rather than a short ladder), and it
    # rides along on the bundle get_pr already built — no second connection.
    _on = {s["stage"] for s in bundle["steps"]}
    l2_dom = ("plant" if "factory_manager" in _on and "scd" not in _on else
              "supply_chain" if "scd" in _on and "factory_manager" not in _on else None)
    if not bundle.get("l2_domain"):
        l2_dom = None
    # Same wording as the proc.l2.* i18n keys, so the pre-swap render and the
    # English dictionary do not drift apart.
    l2_note = ("DOAM Table 4: the Plant Director owns production and maintenance "
               "commitments, so he is the only L2 signature on this request. The "
               "Supply Chain Director does not sign it." if l2_dom == "plant" else
               "DOAM Table 4: the Supply Chain Director owns operational and "
               "inventory replenishment, so he is the only L2 signature on this "
               "request. The Plant Director does not sign it." if l2_dom else "")
    cur_label = " + ".join(C.stage_label(s) for s in current_stages) if current_stages else None
    can_purchasing = user_can("proc_purchasing")
    is_priced = (pr.get("pricing_status") or "priced") == "priced"
    # Cross-module mesh: live warehouse availability for spare-linked lines and
    # a clickable origin (the maintenance ticket / spare this PR came from).
    spare_live = {}
    sids = [it.get("spare_id") for it in bundle["items"] if it.get("spare_id")]
    if sids:
        from app.db import get_db
        conn = get_db()
        try:
            ph = ",".join("?" for _ in sids)
            for r in conn.execute(
                    f"SELECT id, stock_qty, reserved_qty, uom FROM mnt_spare_parts "
                    f"WHERE id IN ({ph})", tuple(sids)).fetchall():
                spare_live[r["id"]] = {
                    "available": round(float(r["stock_qty"] or 0) - float(r["reserved_qty"] or 0), 2),
                    "uom": r["uom"] or ""}
        except Exception:
            spare_live = {}
        finally:
            conn.close()
    source_link = None
    src = str(pr.get("source_ref") or "")
    if pr.get("source_module") == "maintenance" and ":" in src:
        kind, _, sid = src.partition(":")
        if sid.isdigit():
            if kind == "ticket":
                source_link = {"label": f"Maintenance ticket #{sid}",
                               "url": url_for("maintenance.ticket_detail", tid=int(sid))}
            elif kind == "spare":
                source_link = {"label": f"Spare part #{sid} (auto-reorder)",
                               "url": url_for("maintenance.spare_profile", sid=int(sid))}
    # Catalogue cost REFERENCE for the pricing gate — Purchasing only. Built
    # solely when the reader holds proc_purchasing, so it never reaches a
    # requester's render context at all.
    item_costs = (svc.item_costs([it.get("item_id") for it in bundle["items"]])
                  if can_purchasing else {})
    # Purchasing can enter pricing while the PR is still being decided.
    needs_pricing = (can_purchasing and not is_priced
                     and pr["status"] in ("draft", "rejected", "pending"))
    # Requesters don't see commercial figures until Purchasing has priced the PR.
    show_commercial = is_priced or can_purchasing
    return render_template("approvals/detail.html", active="proc_list",
                           b=bundle, pr=pr, actionable=actionable, has_sig=has_sig,
                           act_why=act_why, act_sign=act_sign,
                           l2_domain=l2_dom, l2_note=l2_note,
                           stage_label=C.stage_label, queued=queued,
                           current_stage_label=cur_label, amounts=svc.pr_amounts(pr),
                           match=svc.three_way_match(pr_id),
                           quote_cmp=svc.quote_comparison(bundle.get("quotes", [])),
                           budget=svc.budget_status(pr.get("department")),
                           can_purchasing=can_purchasing, is_priced=is_priced,
                           # Offering a form the viewer cannot submit is worse
                           # than hiding it: Purchasing no longer releases payment.
                           can_pay=user_can("proc_pay"),
                           # DOAM §4.3: only someone who could sign Finance/CFO/MD
                           # is offered the advance-authorisation control.
                           can_advance=any(svc.can_act(user, s)
                                           for s in ("finance", "cfo", "ceo")),
                           needs_pricing=needs_pricing, show_commercial=show_commercial,
                           currencies=C.CURRENCIES, payments=C.PAYMENT_CONDITIONS,
                           rfq_min=C.RFQ_QUOTE_MIN, rfq_threshold=C.RFQ_VALUE_THRESHOLD,
                           # Who to ask for a price, defaulted to the vendors the
                           # lines already name (the header vendor covers a line
                           # that names none).
                           rfq_vendors=[v for v in svc.po_groups(bundle["items"], pr) if v],
                           rfq_required=(is_priced and float(pr.get("total") or 0) >= C.RFQ_VALUE_THRESHOLD),
                           rfq_locked=(pr["status"] in ("approved", "po_issued", "partially_received",
                                                        "received", "closed", "cancelled")),
                           # DOAM §6: whether this request needs an engineering
                           # justification, which one it cites, and the approved
                           # reports Purchasing can choose from. Computed here so
                           # the page only shows the control when it applies —
                           # a picker on an IT stationery order is noise.
                           **_ejr_context(pr_id, pr),
                           # DOAM §4.4: how far each line departs from its target
                           # price / stock ceiling. Fails soft — a grading error
                           # must never take the request page down with it.
                           deviation=_deviation_context(pr_id, pr),
                           # Audit 3.4-b9b: the record's own retention date, and
                           # whether it has passed. Shown on every request, not
                           # only the eligible ones — "kept until 2031" is the
                           # fact an auditor asks for.
                           retention=svc.retention_state(pr),
                           # DOAM §4.1 t7 / §4.2 t4 — the business-case panel is
                           # rendered only for a request that has actually
                           # reached the tier, on the SAME committed EGP figure
                           # the gate refuses on.
                           business_case_required=(
                               is_priced and svc.egp_commitment(pr) > C.BUSINESS_CASE_OVER),
                           can_dispose=user_can("proc_admin"),
                           spare_live=spare_live, source_link=source_link,
                           item_costs=item_costs,
                           # Escalation stamps are DB-stored role keys: their names
                           # and the explanatory sentences are resolved server-side.
                           L=svc.labels(_lang),
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
        # The RFQ this quotation came back against, when it answers one.
        "rfq_id": f.get("rfq_id", "").strip(),
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


def _deviation_context(pr_id, pr):
    """DOAM §4.4 grading for one request, or None when there is nothing to show
    (an unpriced request, or every line on plan)."""
    if (pr.get("pricing_status") or "priced") != "priced":
        return None
    try:
        conn = get_db()
        try:
            dev = svc.deviation_findings(conn, pr_id)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — fail soft, but never in silence
        current_app.logger.warning("deviation panel unavailable for PR %s: %s",
                                   pr_id, exc)
        return None
    off = [l for l in dev["lines"] if l["grade"] != "on_plan"]
    # DOAM §3.4 — the coverage check is shown whether or not it found anything:
    # "we netted this and it is clean" is a result, and the lines that could not
    # be netted at all have to be visible or the reader assumes they were.
    cov = [l for l in dev["lines"] if l.get("coverage")]
    if not off and not dev["unassessable"] and not cov and not dev["coverage_blind"]:
        return None
    dev["off_plan"] = off
    dev["coverage"] = cov
    return dev


def _ejr_context(pr_id, pr):
    """DOAM §6 state for one request: is a report required, which is cited, and
    what approved reports are available. Fails soft — a maintenance-module error
    must not take down the procurement page."""
    out = {"ejr_required": False, "ejr_reason": "", "ejr_row": None,
           "ejr_choices": [], "ejr_gate_on": False}
    try:
        from app.maintenance import eng_justification as ejr
        conn = get_db()
        try:
            items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
            required, why = ejr.ejr_required(pr, items)
            out["ejr_required"] = required
            out["ejr_reason"] = why
            out["ejr_gate_on"] = ejr.gate_enabled(conn)
            cited = pr.get("ejr_id") if isinstance(pr, dict) else None
            if cited:
                out["ejr_row"] = ejr.get(conn, cited)
            if required:
                out["ejr_choices"] = ejr.approved_for_picker(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — fail soft, but never in silence
        current_app.logger.warning("EJR panel unavailable for PR %s: %s", pr_id, exc)
    return out


@bp.route("/pr/<int:pr_id>/engineering-justification", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def attach_ejr(pr_id):
    """Cite an approved Engineering Justification Report (DOAM §6, T&C-PUF-09).

    Purchasing does this, not the requester: the DOAM makes Procurement the party
    that refuses a requisition without a report, so Procurement is the party that
    records which report satisfies it.
    """
    if not svc.get_pr(pr_id):
        abort(404)
    try:
        ejr_id = int(request.form.get("ejr_id") or 0)
    except (TypeError, ValueError):
        ejr_id = 0
    if not ejr_id:
        flash("Choose an approved engineering justification.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    ok, msg = svc.attach_ejr(pr_id, ejr_id, _u(), ip=_ip())
    if ok:
        flash(f"Engineering justification {msg} cited on this request.", "success")
    else:
        flash({"ejr_not_found": "That engineering justification no longer exists.",
               "ejr_not_approved": "That report is not signed yet. Engineering must "
                                   "approve it before Procurement can accept the request."
               }.get(msg, f"Could not attach the report ({msg})."), "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/single-source", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def single_source(pr_id):
    """Purchasing waives the competitive-quote (RFQ) rule with a justification."""
    if not svc.get_pr(pr_id):
        abort(404)
    ok, msg = svc.set_single_source(pr_id, request.form.get("reason", ""), _u(), ip=_ip())
    if ok:
        flash("Single-source justification saved — competitive quotes waived.", "success")
    else:
        flash({"reason_required": "A justification is required to waive competitive quotes.",
               "locked": "Sourcing is locked — this request is already approved or closed."
               }.get(msg, f"Could not save the justification ({msg})."), "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/rfq", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def issue_rfq(pr_id):
    """Issue one Request for Quotation per chosen vendor. Asking for a price is
    not a commitment: the request's status, total and ladder are untouched."""
    if not svc.get_pr(pr_id):
        abort(404)
    f = request.form
    vendors = f.getlist("vendors") + [f.get("vendor_other", "")]
    ok, res = svc.issue_rfqs(pr_id, vendors, _u(), reply_by=f.get("reply_by", "").strip(),
                             notes=f.get("notes", "").strip(), ip=_ip())
    flash("%d request(s) for quotation issued." % len(res) if ok
          else {"no_vendors": "Choose at least one vendor to send the RFQ to.",
                "locked": "Sourcing is locked — this request is already approved, "
                          "ordered, closed or cancelled.",
                "unavailable": "Requests for quotation are not available on this "
                               "database yet.",
                "not_found": "Request not found."}.get(res, f"Could not issue the RFQ ({res})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/rfq.pdf")
@login_required
@permission_required("proc_view")
def rfq_pdf(pr_id):
    """?rfq=<id> prints THAT supplier's request for quotation; without it, the
    first one. One document per supplier, each carrying its own lines and no
    prices at all."""
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    rfqs = bundle.get("rfqs") or []
    want = request.args.get("rfq", type=int)
    rfq = next((r for r in rfqs if r["id"] == want), None) if want else (rfqs[0] if rfqs else None)
    if not rfq:
        abort(404)
    try:
        data = pdfgen.rfq_pdf(bundle, rfq)
    except Exception:
        return jsonify(error="PDF support unavailable."), 500
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=False,
                     download_name=f"{rfq.get('rfq_no') or 'RFQ'}.pdf")


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
    return render_template("approvals/budgets.html", active="proc_budgets",
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
# Agreed production forecasts — DOAM §3.4, the second half of the sales-order
# gate. Anyone who may raise a request may DRAFT one; only the DOAM planning
# authority (Table 4 L2, SC-D) may AGREE it, and only an agreed forecast that is
# inside its validity dates lets direct material be requisitioned against it.
# --------------------------------------------------------------------------
@bp.route("/forecasts", methods=["GET"])
@login_required
@permission_required("proc_view")
def forecasts():
    return render_template("approvals/forecasts.html", active="proc_forecasts",
                           forecasts=svc.list_forecasts(),
                           can_add=user_can("proc_create"),
                           can_agree=svc.can_act(_u(), "scd"))


@bp.route("/forecasts", methods=["POST"])
@login_required
@permission_required("proc_create")
def add_forecast():
    ok, msg = svc.create_forecast(request.form, _u())
    flash(f"Forecast {msg} recorded as a draft — it buys nothing until the "
          f"Supply Chain Director agrees it." if ok
          else f"Could not record the forecast ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.forecasts"))


@bp.route("/forecasts/<int:fc_id>/agree", methods=["POST"])
@login_required
@permission_required("proc_approve")
def agree_forecast(fc_id):
    ok, msg = svc.agree_forecast(fc_id, _u())
    if ok:
        text = (f"Forecast {msg} agreed — direct material may now be "
                f"requisitioned against it while it is in date.")
    elif msg == "not_authorised":
        text = ("Only the Supply Chain Director (or a Procurement admin) may "
                "agree a forecast.")
    elif msg in ("own_forecast", "fc_expired"):
        # Translated, because these are the refusals the reader has to act on:
        # find a second signatory, or register a forecast for this period.
        text = svc.labels((_u() or {}).get("lang_pref") or "en")["ui"][
            "own_forecast_flash" if msg == "own_forecast" else "fc_lapsed_flash"]
    else:
        text = f"Could not agree the forecast ({msg})."
    flash(text, "success" if ok else "error")
    return redirect(url_for("approvals.forecasts"))


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
        # Fallback page only — both pickers search /api/lookup/users.
        users = [dict(r) for r in conn.execute(
            "SELECT username, full_name, role FROM users WHERE is_active=1 "
            "ORDER BY username LIMIT 25").fetchall()]
    finally:
        conn.close()
    return render_template("approvals/delegations.html", active="proc_delegations",
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
               "needs_quotes": "Competitive quotes required — attach at least 2 vendor "
                               "quotes for this order value, or record a single-source "
                               "justification in the Vendor quotes panel.",
               "self_approval": "You cannot approve your own request — raising it is "
                                "your signature.",
               "dual_role": "You already signed another stage of this request — a "
                            "different approver must take this one.",
               "deviation_memo_required": "This order is off plan (over target price, over "
                                          "the stock ceiling, or above the net requirement "
                                          "after inventory netting — §3.4). DOAM §4.4 "
                                          "requires a written justification memo before it "
                                          "is signed — record it in the Coverage check / "
                                          "Deviation from plan panel.",
               "business_case_required": "Above 10,000,000 EGP the DOAM requires a written "
                                         "business case alongside Board approval. Record it "
                                         "before signing.",
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
            "currency": f.get("currency", "").strip(),
            "fx_rate": f.get("fx_rate", "").strip()}   # EGP-equivalent rate for non-EGP PRs
    ok, msg = svc.price_pr(pr_id, prices, meta, _u(), ip=_ip())
    flash("Pricing saved — the request now carries its commercial value and any "
          "value-based approvals have joined the ladder." if ok
          else {"locked": "This request can no longer be priced.",
                "fx_required": "Foreign-currency request: enter the EGP exchange rate "
                               "before pricing, so the value approvals route on the true "
                               "EGP equivalent."}.get(
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
    flash("Submitted for approval." if ok else _submit_error(msg),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/issue-po", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def issue_po(pr_id):
    # An override must be ASKED FOR, not inherited from a role. Passing
    # force=user_can("proc_admin") meant the three-way match, the payment cap,
    # the budget check and the DOAM §4.3 advance authorisation were all silently
    # off for every admin on every request — a bypass nobody chose and nobody
    # could see. Now the caller has to tick "override" and the reason is audited.
    _override = user_can("proc_admin") and request.form.get("override") == "1"
    ok, res = svc.issue_po(pr_id, _u(), ip=_ip(), force=_override)
    flash(f"Purchase Order {res} issued." if ok else
          {"over_budget": "The department budget for this period is exceeded — "
                          "an administrator must issue this PO (or raise the budget).",
           }.get(res, f"Could not issue PO ({res})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/revise-po", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def revise_po(pr_id):
    """Open a numbered revision on an issued PO (reason required, history kept)."""
    ok, res = svc.revise_po(pr_id, request.form.get("reason", ""), _u(), ip=_ip())
    flash(f"PO revision opened — the order now reads Rev {res}." if ok else
          {"reason_required": "Enter the reason for the revision — it is kept in "
                              "the PO history.",
           "not_revisable": "Only an issued (or partially received) Purchase Order "
                            "can be revised.",
           }.get(res, f"Could not revise the PO ({res})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/fx", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def set_fx(pr_id):
    """Purchasing sets the EGP conversion rate for a foreign-currency request."""
    ok, res = svc.set_fx(pr_id, request.form.get("fx_rate", ""), _u(), ip=_ip())
    flash("FX rate saved — approval thresholds now route on the EGP equivalent."
          if ok else
          {"bad_rate": "Enter a valid FX rate greater than zero.",
           "locked": "The FX rate is locked once the request is approved.",
           }.get(res, f"Could not set the FX rate ({res})."),
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


_RECEIVE_ERR = {
    "not_receivable": "This request is not open for receiving — a receipt can only "
                      "be booked on an approved, issued or received purchase order.",
    "nothing_received": "Enter an accepted or a rejected quantity on at least one line.",
    "not_found": "That purchase request no longer exists.",
}


@bp.route("/pr/<int:pr_id>/receive", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def receive(pr_id):
    f = request.form
    ids = f.getlist("recv_item_id[]")

    def _col(name):
        """{item_id: qty} from one parallel column of the receipt form."""
        vals = f.getlist(name)
        out = {}
        for i, iid in enumerate(ids):
            try:
                q = float(vals[i]) if i < len(vals) and vals[i] else 0
            except ValueError:
                q = 0
            if q > 0:
                out[iid] = q
        return out

    receipts, rejects = _col("recv_qty[]"), _col("rej_qty[]")
    if not receipts and not rejects:
        flash("Enter an accepted or a rejected quantity on at least one line.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    reason = f.get("reject_reason", "").strip()
    if rejects and not reason:
        flash("A rejected quantity needs a reason.", "error")
        return redirect(url_for("approvals.detail", pr_id=pr_id))
    ok, msg = svc.receive_items(pr_id, receipts, _u(),
                                notes=f.get("notes", "").strip() or None, ip=_ip(),
                                rejects=rejects, reject_reason=reason or None)
    flash({"received": "Delivery fully confirmed.", "partial": "Partial receipt recorded."}.get(msg, "Receipt recorded.")
          + (" Rejected goods returned to the supplier under a debit note." if rejects else "")
          if ok else _RECEIVE_ERR.get(msg, f"Could not record receipt ({msg})."),
          "success" if ok else "error")
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
        "po_id": f.get("po_id"),
    }, _u(), filename=fn, content_type=ct, content_b64=b64, ip=_ip())
    # The two per-order refusals are described in AR and TR alongside the payment
    # gates they belong with, so they must not arrive in English only.
    ui = svc.labels((_u() or {}).get("lang_pref") or "en")["ui"]
    flash("Invoice recorded and matched." if ok else
          ui.get(str(msg) + "_flash") or
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
# DOAM §7.3.4 "FIND owns payment", Table 4 L2 "FIN-D owns payment control".
# This was proc_purchasing, so the buyer who committed the spend also released
# the money and Finance could not — the separation the clause exists to create,
# exactly inverted. proc_pay is held by the Financial Director, Finance Manager
# and CFO; super_admin keeps it as the break-glass path.
@permission_required("proc_pay")
def add_payment(pr_id):
    if not svc.get_pr(pr_id):
        abort(404)
    f = request.form
    ok, res = svc.add_payment(pr_id, {
        "amount": f.get("amount"), "method": f.get("method", "").strip(),
        "reference": f.get("reference", "").strip(), "paid_at": f.get("paid_at", "").strip(),
        "invoice_id": f.get("invoice_id"), "notes": f.get("notes", "").strip(),
        "po_id": f.get("po_id"),
    }, _u(), ip=_ip(), force=(user_can("proc_admin") and f.get("override") == "1"))
    # Refusals in the reader's language, the way _submit_error does it: these
    # gates are described in AR and TR on /procurement/workflow, so the sentence
    # that fires must not arrive in English only. labels() already falls back to
    # the English wording for any key a language has not translated.
    ui = svc.labels((_u() or {}).get("lang_pref") or "en")["ui"]
    flash(f"Payment recorded ({res})." if ok else
          ui.get(str(res) + "_flash") or f"Could not record payment ({res}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/advance", methods=["POST"])
@login_required
@permission_required("proc_approve")
def authorize_advance(pr_id):
    """DOAM §4.3 — Finance / CFO / MD authorises an advance before it is paid."""
    if not svc.get_pr(pr_id):
        abort(404)
    f = request.form
    ok, msg = svc.authorize_advance(pr_id, f.get("advance_pct"),
                                    f.get("bank_guarantee_ref", "").strip(),
                                    _u(), ip=_ip())
    flash("Advance authorised." if ok else
          {"bad_pct": "Enter the advance as a percentage of the PO value (1–100).",
           "no_po": "There is no Purchase Order yet to advance against.",
           "vendor_not_approved": "This supplier is not on the approved vendor list, "
                                  "so no advance may be authorised (DOAM §4.3).",
           "guarantee_required": "An advance above 25% on an order over 500,000 EGP "
                                 "needs a bank guarantee reference.",
           "not_authorised": "An advance of this size needs a higher authority: up to "
                             "25% the Financial Director, above that the CFO or the "
                             "Managing Director.",
           }.get(msg, f"Could not authorise the advance ({msg})."),
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


@bp.route("/pr/<int:pr_id>/doam-doc", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def doam_doc(pr_id):
    """Record the §4.1/§4.2 business case or the §4.4 justification memo.

    Purchasing owns both, for the same reason it owns the pricing gate: both are
    conditions attached to the COMMERCIAL value, which only exists once
    Purchasing has priced the request."""
    field = request.form.get("field", "")
    ok, msg = svc.set_doam_document(pr_id, field, request.form.get("text", ""),
                                    _u(), ip=_ip())
    flash(("Business case recorded." if field == "business_case"
           else "Justification memo recorded.") if ok
          else {"too_short": "Write the actual reasoning — a few words cannot "
                             "satisfy a DOAM document requirement.",
                "bad_field": "Unknown document.",
                "locked": "This request is closed."}.get(msg, f"Could not save ({msg})."),
          "success" if ok else "error")
    return redirect(url_for("approvals.detail", pr_id=pr_id))


@bp.route("/pr/<int:pr_id>/dispose", methods=["POST"])
@login_required
@permission_required("proc_admin")
def dispose(pr_id):
    """Retire ONE record whose retention period has expired. There is no bulk
    action and no scheduled job on purpose: the report says what is eligible,
    a person decides, and the guard refuses anything still inside its window."""
    ok, msg = svc.dispose_pr(pr_id, _u(), ip=_ip())
    if ok:
        flash(f"Record retired from the register (retention expired {msg}).", "success")
    elif msg.startswith("retained_until:"):
        flash("This record is still inside its retention period — it must be kept "
              f"until {msg.split(':', 1)[1]}.", "error")
    else:
        flash({"already_disposed": "This record has already been retired."}
              .get(msg, f"Could not retire this record ({msg})."), "error")
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
    """?po=<id> prints THAT vendor's order; without it, the primary one. A
    request buying from several suppliers issues one document each, and each
    carries only its own vendor's lines and prices.

    Delivery statuses are on the list because the order genuinely exists at
    them and reprinting it after a delivery is ordinary: on a split request the
    per-vendor table is the ONLY route to the second and third suppliers'
    documents, and the narrower gate made them unreachable the moment the first
    supplier delivered."""
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    if bundle["pr"]["status"] not in ("approved", "po_issued", "partially_received",
                                      "received", "closed"):
        abort(400, "The Purchase Order is available only after full approval.")
    pos = bundle.get("pos") or []
    want = request.args.get("po", type=int)
    po = next((p for p in pos if p["id"] == want), None) if want else (pos[0] if pos else None)
    if want and not po:
        abort(404)
    try:
        data = pdfgen.po_pdf(bundle, po)
    except Exception:
        return jsonify(error="PDF support unavailable."), 500
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=False,
                     download_name=f"{(po or {}).get('po_no') or bundle['pr'].get('po_no', 'PO')}.pdf")


@bp.route("/pr/<int:pr_id>/grn.pdf")
@login_required
@permission_required("proc_view")
def grn_pdf(pr_id):
    """?grn=<id> prints THAT goods-received note; without it, the latest one.
    Each receipt event has its own pre-allocated number, so partial deliveries no
    longer reprint one document under one number."""
    bundle = svc.get_pr(pr_id)
    if not bundle:
        abort(404)
    if bundle["pr"]["status"] not in ("partially_received", "received", "closed"):
        abort(400, "The Goods Received Note is available once a delivery is recorded.")
    grns = bundle.get("grns") or []
    want = request.args.get("grn", type=int)
    grn = next((g for g in grns if g["id"] == want), None) if want else (grns[-1] if grns else None)
    if want and not grn:
        abort(404)
    try:
        data = pdfgen.grn_pdf(bundle, grn)
    except Exception:
        return jsonify(error="PDF support unavailable."), 500
    name = (grn or {}).get("grn_no") or pdfgen.grn_doc_no(bundle["pr"].get("pr_no"))
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=False, download_name=f"{name}.pdf")


@bp.route("/return/<int:ret_id>/debit-note.pdf")
@login_required
@permission_required("proc_view")
def debit_note_pdf(ret_id):
    """The debit note raised when goods were rejected on receipt and returned."""
    ret = svc.get_return(ret_id)
    if not ret:
        abort(404)
    bundle = svc.get_pr(ret["pr_id"])
    if not bundle:
        abort(404)
    try:
        data = pdfgen.debit_note_pdf(bundle, ret)
    except Exception:
        return jsonify(error="PDF support unavailable."), 500
    return send_file(io.BytesIO(data), mimetype="application/pdf",
                     as_attachment=False,
                     download_name=f"{ret.get('dn_no') or 'DN'}.pdf")


@bp.route("/return/<int:ret_id>/settle", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def settle_return(ret_id):
    ok, msg = svc.settle_return(ret_id, _u(), ip=_ip())
    flash(f"Debit note {msg} settled — the supplier's dues are cleared." if ok
          else {"already_settled": "That debit note is already settled.",
                "not_found": "Debit note not found."}.get(msg, f"Could not settle ({msg})."),
          "success" if ok else "error")
    return redirect(request.referrer or url_for("approvals.index"))


@bp.route("/quarantine/<int:q_id>/<decision>", methods=["POST"])
@login_required
@permission_required("proc_purchasing")
def resolve_quarantine(q_id, decision):
    ok, msg = svc.resolve_quarantine(q_id, decision, _u(), ip=_ip())
    flash({"return": "Over-delivery marked for return to the supplier.",
           "accept": "Over-delivery accepted as a free issue (stock unchanged).",
           }.get(msg, f"Could not resolve the quarantine ({msg}).")
          if ok else {"already_resolved": "That quarantine record is already decided.",
                      }.get(msg, f"Could not resolve the quarantine ({msg})."),
          "success" if ok else "error")
    return redirect(request.referrer or url_for("approvals.index"))


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
    return render_template("approvals/vendors.html", active="proc_vendors",
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
    lang = (_u() or {}).get("lang_pref") or "en"
    return render_template("approvals/settings.html", active="proc_settings",
                           departments=depts, department=dept,
                           matrix=svc.get_dept_matrix(dept), ladder=C.LADDER,
                           # ACTIVE_MATRIX, not APPROVAL_MATRIX. The template
                           # iterates C.LADDER, which rebinds to the 8-stage DOAM
                           # ladder when the manual is in force, while
                           # APPROVAL_MATRIX is the 6-stage paper form — so
                           # 'scd' and 'bod' were missing keys and the {:,.0f}
                           # format on an Undefined raised, taking the whole
                           # screen to a 500 for every admin including
                           # super_admin. The responsibility matrix could not be
                           # opened at all.
                           stage_labels=C.STAGE_LABELS, default_matrix=C.ACTIVE_MATRIX,
                           is_builtin=(dept in C.DEPARTMENTS),
                           has_custom=(dept in svc.all_dept_matrices()),
                           # Escalation chain: role names and the section's own
                           # wording are resolved server-side (see svc.labels).
                           L=svc.labels(lang), chain=svc.escalation_rows(lang),
                           role_choices=svc.role_choices(lang))


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
        rows = [{"stage": s, "threshold": C.ACTIVE_MATRIX.get(s, 0)} for s in C.LADDER]
    ok, msg = svc.set_dept_matrix(dept, rows, _u())
    flash(f"Responsibility matrix saved for {dept}." if ok else f"Could not save ({msg}).",
          "success" if ok else "error")
    return redirect(url_for("approvals.settings", department=dept))


@bp.route("/settings/escalation", methods=["POST"])
@login_required
@permission_required("proc_admin")
def save_escalation():
    """Save the escalation chain (who signs one level up when the requester is the
    only eligible signer for a rung). POST-only, proc_admin, audited per row like
    every other governance edit. A blank superior means 'nobody above'."""
    f = request.form
    dept = (f.get("department") or "").strip()
    saved, errs = 0, []
    for role_key in f.getlist("role_key"):
        ok, msg = svc.set_escalation(role_key, f.get("sup_" + role_key),
                                     user=_u(), ip=_ip())
        if ok:
            saved += 1
        else:
            errs.append(f"{role_key}: {_ESC_ERRORS.get(msg, msg)}")
    if errs:
        flash(" ".join(errs), "error")
    else:
        flash(f"Escalation chain saved ({saved} role(s)).", "success")
    return redirect(url_for("approvals.settings", department=dept or None))


_ESC_ERRORS = {
    "self_superior": "a role cannot be its own superior.",
    "unknown_role": "unknown role.",
}


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


# --------------------------------------------------------------------------
# Workflow & Governance — the cycle explained, and every rule that drives it.
# Reading it needs proc_view (any procurement user should be able to look up how
# their own approval cycle works); every edit needs proc_admin and is audited.
# --------------------------------------------------------------------------
@bp.route("/workflow", methods=["GET"])
@login_required
@permission_required("proc_view")
def workflow():
    # The explanation prose lives in the database, so it cannot be swapped by the
    # client-side data-i18n pass — the reader's own language picks the column here.
    lang = (_u() or {}).get("lang_pref") or "en"
    return render_template("approvals/workflow.html", active="proc_workflow",
                           v=svc.workflow_view(request.args.get("department"), lang),
                           can_edit=user_can("proc_admin"))


def _back(dept=None):
    return redirect(url_for("approvals.workflow", department=dept or None))


_WF_ERRORS = {
    "bad_value": "That value isn’t valid for this setting.",
    "out_of_range": "That value is outside the allowed range.",
    "unknown_setting": "Unknown setting.",
    "unknown_stage": "Unknown stage.",
    "unknown_role": "Unknown role.",
    "unknown_section": "Unknown section.",
    "not_a_signing_stage": "The requester signs by submitting — no approver role applies.",
    "empty": "Enter some text first.",
}


def _wf_flash(ok, msg, done):
    flash(done if ok else _WF_ERRORS.get(msg, "Could not save."),
          "success" if ok else "error")


@bp.route("/workflow/stage", methods=["POST"])
@login_required
@permission_required("proc_admin")
def workflow_stage():
    """Save one stage: its signing roles and/or its explanation. `reset` names the
    part to clear ('role' or 'explanation') — clearing DELETES the override so the
    code default applies again."""
    f = request.form
    stage, reset = f.get("stage") or "", f.get("reset") or ""
    ok, msg = svc.set_stage_meta(
        stage,
        roles=None if reset else f.getlist("roles"),
        explanation=None if reset else f.get("explanation"),
        explanation_ar=None if reset else f.get("explanation_ar"),
        explanation_tr=None if reset else f.get("explanation_tr"),
        user=_u(), ip=_ip(),
        reset_role=(reset == "role"), reset_explanation=(reset == "explanation"))
    _wf_flash(ok, msg, "Reset to the default." if reset else "Stage saved.")
    return _back(f.get("department"))


@bp.route("/workflow/role", methods=["POST"])
@login_required
@permission_required("proc_admin")
def workflow_role():
    f = request.form
    reset = f.get("reset") == "1"
    ok, msg = svc.set_role_meta(f.get("role_key"), f.get("explanation"),
                                user=_u(), ip=_ip(), reset=reset,
                                explanation_ar=f.get("explanation_ar"),
                                explanation_tr=f.get("explanation_tr"))
    _wf_flash(ok, msg, "Reset to the default." if reset else "Role description saved.")
    return _back(f.get("department"))


@bp.route("/workflow/doc", methods=["POST"])
@login_required
@permission_required("proc_admin")
def workflow_doc():
    f = request.form
    reset = f.get("reset") == "1"
    ok, msg = svc.set_doc(f.get("section"), f.get("body"),
                          user=_u(), ip=_ip(), reset=reset,
                          body_ar=f.get("body_ar"), body_tr=f.get("body_tr"))
    _wf_flash(ok, msg, "Reset to the default." if reset else "Text saved.")
    return _back(f.get("department"))


@bp.route("/workflow/setting", methods=["POST"])
@login_required
@permission_required("proc_admin")
def workflow_setting():
    f = request.form
    key = f.get("key") or ""
    if f.get("reset") == "1":
        ok, msg = svc.reset_setting(key, user=_u(), ip=_ip())
        _wf_flash(ok, msg, "Reset to the code default.")
    else:
        ok, msg = svc.set_setting(key, f.get("value"), user=_u(), ip=_ip())
        _wf_flash(ok, msg, "Setting saved — it applies to the next action.")
    return _back(f.get("department"))


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


# Report declarations for the shared reporting engine (/reporting). Imported for
# its import-time side effect: it registers the specs and touches no database.
from app.approvals import reports as _reports  # noqa: E402,F401
