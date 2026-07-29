"""
TC Platform — the generic report hub.

Six routes serve EVERY module's reports. A module adds a report by calling
app.services.reporting.register(...) at import time; it writes no route, no
template and no exporter, and it cannot forget the permission check because the
check lives here.

Permission: each report declares the module's own permission and it is enforced
identically on the HTML page and on all three export formats — a user who cannot
open a module cannot read its numbers through an export URL.
"""
from flask import (Blueprint, abort, redirect, render_template, request,
                   send_file, url_for)
import io

from app.auth import current_user, login_required
from app.security import user_has_permission
from app.services import reporting

bp = Blueprint("reports_hub", __name__, url_prefix="/reporting")


def _spec(key):
    """The report, or 404 if unknown / 403 if the user lacks its permission."""
    spec = reporting.get(key)
    if not spec:
        abort(404)
    if not user_has_permission(current_user(), spec["perm"]):
        abort(403)
    return spec


def _lang():
    u = current_user() or {}
    return (u.get("lang_pref") or "en") if isinstance(u, dict) else "en"


@bp.route("/")
@login_required
def hub():
    groups = reporting.catalog(
        lambda p: user_has_permission(current_user(), p))
    return render_template("reports/hub.html", groups=groups, active="reports")


@bp.route("/<key>")
@login_required
def view(key):
    spec = _spec(key)
    result = reporting.run(spec, request.args)
    views = reporting.saved_views((current_user() or {}).get("id"), key)
    return render_template("reports/report.html", spec=spec, result=result,
                           views=views, args=request.args, active="reports")


@bp.route("/<key>.<fmt>")
@login_required
def export(key, fmt):
    if fmt not in ("csv", "xlsx", "pdf"):
        abort(404)
    spec = _spec(key)                       # same permission as the HTML page
    result = reporting.run(spec, request.args, cap=reporting.EXPORT_CAP)
    if result["error"]:
        abort(400)
    payload, mime, filename = reporting.export(spec, result, fmt, _lang())
    return send_file(io.BytesIO(payload), mimetype=mime,
                     as_attachment=True, download_name=filename)


@bp.route("/<key>/views", methods=["POST"])
@login_required
def save_view(key):
    spec = _spec(key)
    reporting.save_view(current_user()["id"], spec["key"],
                        request.form.get("name"), request.form.get("qs"))
    qs = (request.form.get("qs") or "").strip()
    return redirect(url_for("reports_hub.view", key=key) + (f"?{qs}" if qs else ""))


@bp.route("/<key>/views/<int:vid>/delete", methods=["POST"])
@login_required
def delete_view(key, vid):
    spec = _spec(key)
    reporting.delete_view(current_user()["id"], vid)
    return redirect(url_for("reports_hub.view", key=spec["key"]))
