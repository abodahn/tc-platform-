"""
Smart Factory (MES) — routes. Merged into the TC Platform, reusing its auth,
RBAC, CSRF, i18n, base template and design.

  /factory              live command center (efficiency, RAG, DHU/RFT, sustainability)
  /factory/production   hourly production entry board
  /factory/quality      quality checks (DHU/RFT + defect pareto)
  /factory/orders       orders & styles (SMV)

Viewing needs `view_dashboard`; shop-floor entry needs `manage_production`.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash

from app.auth import login_required, permission_required, current_user
from app.smartfactory import services as svc

bp = Blueprint("smartfactory", __name__, url_prefix="/factory")


def _u():
    u = current_user()
    return (u.get("full_name") or u.get("username")) if u else ""


@bp.route("/")
@login_required
@permission_required("view_dashboard")
def command_center():
    data = svc.dashboard()
    return render_template("smartfactory/command_center.html", active="factory", **data)


@bp.route("/production", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def production():
    return render_template("smartfactory/production.html", active="factory",
                           rows=svc.list_production(), lines=svc.list_lines(),
                           orders=svc.list_orders(),
                           slots=["08:00-09:00", "09:00-10:00", "10:00-11:00", "11:00-12:00",
                                  "13:00-14:00", "14:00-15:00", "15:00-16:00"])


@bp.route("/production", methods=["POST"])
@login_required
@permission_required("manage_production")
def production_add():
    f = request.form
    try:
        svc.add_production(int(f.get("line_id")), f.get("order_id") or None, f.get("hour_slot", ""),
                           f.get("target_qty", 0), f.get("actual_qty", 0), f.get("lost_min", 0),
                           f.get("operator", ""), _u())
        flash("Production entry saved.", "success")
    except Exception:  # noqa: BLE001
        flash("Could not save the entry.", "error")
    return redirect(url_for("smartfactory.production"))


@bp.route("/quality", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def quality():
    data = svc.dashboard()
    return render_template("smartfactory/quality.html", active="factory",
                           rows=svc.list_quality(), lines=svc.list_lines(),
                           orders=svc.list_orders(), pareto=data["pareto"])


@bp.route("/quality", methods=["POST"])
@login_required
@permission_required("manage_production")
def quality_add():
    f = request.form
    defects = []
    for code in ("SKIP-STITCH", "BROKEN-STITCH", "SHADE-VAR", "MEASUREMENT", "OTHER"):
        qty = f.get(f"def_{code}")
        if qty and int(qty or 0) > 0:
            cat = "wash" if code == "SHADE-VAR" else ("measurement" if code == "MEASUREMENT" else "stitching")
            defects.append({"code": code, "category": cat, "qty": int(qty)})
    try:
        svc.add_quality(int(f.get("line_id")), f.get("order_id") or None, f.get("stage", "endline"),
                        f.get("inspected", 0), f.get("defect", 0), f.get("rework", 0),
                        f.get("reject", 0), _u(), defects)
        flash("Quality check recorded.", "success")
    except Exception:  # noqa: BLE001
        flash("Could not record the check.", "error")
    return redirect(url_for("smartfactory.quality"))


@bp.route("/orders")
@login_required
@permission_required("view_dashboard")
def orders():
    return render_template("smartfactory/orders.html", active="factory", orders=svc.list_orders())
