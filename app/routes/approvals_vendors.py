"""
Procurement routes for the vendors lane: vendor approval / block, supplier
documents, and the comments thread on a request. Registered on the existing
approvals blueprint (imported once at the end of app/routes/approvals.py).
"""
import base64
import io
import json
import os

from flask import abort, redirect, request, send_file, url_for

from app.auth import login_required, permission_required
from app.approvals import services as svc
from app.approvals import vendors as V
from app.approvals.vendors_text import QUOTE_ERRORS
from app.routes.approvals import bp, flash, _u, _ip, _QUOTE_ERRORS

# The existing choose / award / re-award routes flash _QUOTE_ERRORS[code].
_QUOTE_ERRORS.update(QUOTE_ERRORS)


def _ui():
    return svc.labels((_u() or {}).get("lang_pref") or "en")["ui"]


def _flash_code(ok, code, ok_key):
    ui = _ui()
    flash(ui[ok_key] if ok else ui.get(str(code) + "_flash") or "%s (%s)" % (
        ui.get("vendor_block_forbidden_flash"), code), "success" if ok else "error")


@bp.route("/vendors/<int:vendor_id>/approve", methods=["POST"])
@login_required
@permission_required("proc_pay", "proc_admin")
def approve_vendor(vendor_id):
    ok, code = V.approve_vendor(vendor_id, _u(), ip=_ip())
    _flash_code(ok, code, "vendor_approved_ok_flash")
    return redirect(url_for("approvals.vendors"))


@bp.route("/vendors/<int:vendor_id>/block", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_pay", "proc_admin")
def block_vendor(vendor_id):
    ok, code = V.block_vendor(vendor_id, request.form.get("reason"), _u(), ip=_ip())
    _flash_code(ok, code, "vendor_blocked_ok_flash")
    return redirect(url_for("approvals.vendors"))


@bp.route("/vendors/<int:vendor_id>/doc", methods=["POST"])
@login_required
@permission_required("proc_purchasing", "proc_pay", "proc_admin")
def add_vendor_doc(vendor_id):
    file = request.files.get("file")
    name = file.filename if file else ""
    raw = file.read() if file and name else b""
    bad = V.check_upload(name, raw)
    if bad:
        _flash_code(False, bad, "vendor_doc_saved_flash")
        return redirect(url_for("approvals.vendors"))
    ok, code = V.add_doc(vendor_id, request.form.get("doc_type"), name,
                         file.mimetype or "application/octet-stream",
                         base64.b64encode(raw).decode("ascii"), len(raw), _u(), ip=_ip())
    _flash_code(ok, code, "vendor_doc_saved_flash")
    return redirect(url_for("approvals.vendors"))


@bp.route("/vendor-doc/<int:doc_id>")
@login_required
@permission_required("proc_purchasing", "proc_pay", "proc_admin")
def vendor_doc(doc_id):
    row = V.get_doc(doc_id)
    if not row:
        abort(404)
    try:
        raw = base64.b64decode(row["content_b64"] or "")
    except Exception:
        abort(404)
    return send_file(io.BytesIO(raw), mimetype=row["content_type"] or "application/octet-stream",
                     as_attachment=True, download_name=row["filename"] or "document")


@bp.route("/pr/<int:pr_id>/comment", methods=["POST"])
@login_required
@permission_required("proc_view")
def add_comment(pr_id):
    ok, code = V.add_comment(pr_id, request.form.get("body"), _u(), ip=_ip())
    if code == "not_found":
        abort(404)
    _flash_code(ok, code, "comment_saved_flash")
    return redirect(url_for("approvals.detail", pr_id=pr_id) + "#prComments")


# --- template helpers ------------------------------------------------------
_KEYS = {}


@bp.app_template_global()
def proc_i18n_has(key):
    """True when `key` is in the English dictionary. app.js prints a missing
    key RAW, so a data-i18n built from a stored code (an audit action, a
    status) is only emitted when the key exists."""
    if "keys" not in _KEYS:
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "static", "i18n", "en.json")
        try:
            with open(path, encoding="utf-8") as fh:
                _KEYS["keys"] = set(json.load(fh))
        except Exception:
            _KEYS["keys"] = set()
    return key in _KEYS["keys"]


@bp.app_template_global()
def proc_vendor_flags(bundle):
    try:
        return V.request_flags(bundle)
    except Exception:
        return []


@bp.app_template_global()
def proc_comments(pr):
    return V.comments_context(pr, _u())
