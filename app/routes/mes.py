"""
Shop-floor MES routes (native platform blueprint at /mes).
Hourly output board with RAG status, reason-coded downtime, OEE / line efficiency
and bundle-WIP tracking — reusing platform auth, RBAC, CSRF, i18n, base and bell.
"""
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort

from app.auth import login_required, permission_required, current_user
from app.mes import services as svc
from app.mes.constants import HOUR_SLOTS, DOWNTIME_REASONS, SECTIONS, BUNDLE_STATUS

bp = Blueprint("mes", __name__, url_prefix="/mes")

_SWEEP_AT = [0.0]
_SWEEP_EVERY = 300  # s


def _u():
    return current_user()


def _sweep_throttled(work_date=None):
    now = time.time()
    if now - _SWEEP_AT[0] > _SWEEP_EVERY:
        _SWEEP_AT[0] = now
        svc.alert_sweep(work_date)


def _back(default):
    """Return to the page the scan was posted from, but only if it is ours —
    Referer is attacker-supplied, and redirect() will happily send the user off-site."""
    ref = request.referrer or ""
    return ref if ref.startswith(request.host_url) else default


def _flash(res):
    """Services return (ok, i18n_key); app.js translates a flash that is a key."""
    ok, msg = res
    flash(msg if isinstance(msg, str) else "mes.msg.saved", "success" if ok else "error")
    return ok


@bp.route("/")
@login_required
@permission_required("mes_view")
def index():
    d = svc.dashboard(request.args.get("date"))
    _sweep_throttled(d["work_date"])
    return render_template("mes/dashboard.html", active="mes", d=d)


@bp.route("/board")
@login_required
@permission_required("mes_view")
def board():
    b = svc.hourly_board(request.args.get("date"))
    _sweep_throttled(b["work_date"])
    return render_template("mes/board.html", active="mes_board", b=b)


@bp.route("/entry")
@login_required
@permission_required("mes_entry")
def entry():
    # entry_date, NOT resolve_date: the form must default to TODAY. Pre-filling the
    # last day that has production would make the first save of a new shift edit
    # that older day's (line, date, hour) cell instead of opening a new one.
    return render_template("mes/entry.html", active="mes_board", slots=HOUR_SLOTS,
                           reasons=DOWNTIME_REASONS, lines=svc.list_lines(),
                           orders=svc.list_orders(), today=svc.entry_date(request.args.get("date")))


@bp.route("/hourly", methods=["POST"])
@login_required
@permission_required("mes_entry")
def hourly_save():
    if _flash(svc.save_hourly(request.form, _u())):
        svc.alert_sweep(request.form.get("work_date"))
    return redirect(url_for("mes.entry", date=request.form.get("work_date") or None))


@bp.route("/downtime", methods=["POST"])
@login_required
@permission_required("mes_entry")
def downtime_save():
    if _flash(svc.add_downtime(request.form, _u())):
        svc.alert_sweep(request.form.get("work_date"))
    return redirect(url_for("mes.entry", date=request.form.get("work_date") or None))


@bp.route("/line/<int:line_id>")
@login_required
@permission_required("mes_view")
def line(line_id):
    d = svc.line_detail(line_id, request.args.get("date"))
    if not d:
        abort(404)
    return render_template("mes/line.html", active="mes", **d)


@bp.route("/bundles")
@login_required
@permission_required("mes_view")
def bundles():
    status = request.args.get("status") or None
    # ["rows"], not the whole dict: the template iterates `wip` as the section rows,
    # and Jinja iterating a dict yields its KEYS — the strip rendered two blank tiles
    # labelled with the truncated key "mes.section.".
    return render_template("mes/bundles.html", active="mes_bundles",
                           rows=svc.list_bundles(status=status), wip=svc.wip_by_section()["rows"],
                           recon=svc.order_reconciliation(), orders=svc.list_orders(),
                           moves=svc.recent_moves(), sections=SECTIONS,
                           statuses=BUNDLE_STATUS, f_status=status)


@bp.route("/export/<key>.csv")
@login_required
@permission_required("mes_view")
def export_csv(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "mes", "csv")
    if resp is None:
        abort(404)
    return resp


@bp.route("/api/<key>.json")
@login_required
@permission_required("mes_view")
def api_json(key):
    from app.services.export import dispatch
    resp = dispatch(svc.export_dataset, key, "mes", "json")
    if resp is None:
        abort(404)
    return resp


@bp.route("/bundles", methods=["POST"])
@login_required
@permission_required("mes_entry")
def bundle_create():
    _flash(svc.create_bundle(request.form, _u()))
    return redirect(url_for("mes.bundles"))


@bp.route("/bundles/<int:bundle_id>/move", methods=["POST"])
@login_required
@permission_required("mes_entry")
def bundle_move(bundle_id):
    _flash(svc.move_bundle(bundle_id, request.form, _u()))
    return redirect(_back(url_for("mes.bundles")))


@bp.route("/bundles/<int:bundle_id>/status", methods=["POST"])
@login_required
@permission_required("mes_entry")
def bundle_status(bundle_id):
    _flash(svc.set_bundle_status(bundle_id, request.form.get("status") or "", _u()))
    return redirect(_back(url_for("mes.bundles")))
