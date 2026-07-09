"""
Smart Factory (MES) — routes. Merged into the TC Platform, reusing its auth,
RBAC, CSRF, i18n, base template and design.

  /factory              live command center (efficiency, RAG, DHU/RFT, sustainability)
  /factory/production   hourly production entry board
  /factory/quality      quality checks (DHU/RFT + defect pareto)
  /factory/orders       orders & styles (SMV)

Viewing needs `view_dashboard`; shop-floor entry needs `manage_production`.
"""
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   send_file, abort)

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
    return render_template("smartfactory/command_center.html", active="sf_cc", **data)


@bp.route("/production", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def production():
    return render_template("smartfactory/production.html", active="sf_production",
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
    return render_template("smartfactory/quality.html", active="sf_quality",
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
    return render_template("smartfactory/orders.html", active="sf_orders", orders=svc.list_orders())


# ---- Floor board (big live view) ----
@bp.route("/floor")
@login_required
@permission_required("view_dashboard")
def floor():
    return render_template("smartfactory/floor.html", active="sf_floor", **svc.dashboard())


# ---- Cutting & bundles ----
@bp.route("/bundles", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def bundles():
    return render_template("smartfactory/bundles.html", active="sf_bundles",
                           orders=svc.list_orders(), **svc.bundles())


@bp.route("/bundles", methods=["POST"])
@login_required
@permission_required("manage_production")
def bundles_add():
    f = request.form
    try:
        svc.add_bundle(f.get("order_id") or None, f.get("roll_id") or None, f.get("size", "M"),
                       f.get("color", ""), f.get("qty", 0), f.get("operation_at", "cutting"))
        flash("Bundle created.", "success")
    except Exception:  # noqa: BLE001
        flash("Could not create the bundle.", "error")
    return redirect(url_for("smartfactory.bundles"))


# ---- Laundry / wash ----
@bp.route("/wash", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def wash():
    return render_template("smartfactory/wash.html", active="sf_wash",
                           orders=svc.list_orders(), **svc.wash_list())


@bp.route("/wash", methods=["POST"])
@login_required
@permission_required("manage_production")
def wash_add():
    f = request.form
    try:
        svc.add_wash(f.get("order_id") or None, f.get("recipe", ""), f.get("water_l", 0),
                     f.get("energy_kwh", 0), f.get("chemical_kg", 0), f.get("pieces", 0),
                     f.get("shade", ""), f.get("rewash", 0))
        flash("Wash batch recorded.", "success")
    except Exception:  # noqa: BLE001
        flash("Could not record the batch.", "error")
    return redirect(url_for("smartfactory.wash"))


# ---- Workforce / efficiency ----
@bp.route("/workforce")
@login_required
@permission_required("view_dashboard")
def workforce():
    return render_template("smartfactory/workforce.html", active="sf_workforce", **svc.workforce())


# ---- Costing (cost-per-piece) ----
@bp.route("/costing")
@login_required
@permission_required("view_dashboard")
def costing():
    return render_template("smartfactory/costing.html", active="sf_costing", **svc.costing())


# ---- AI insights ----
@bp.route("/ai")
@login_required
@permission_required("view_dashboard")
def ai():
    return render_template("smartfactory/ai.html", active="sf_ai", insights=svc.ai_insights())


# ---- Reports & exports ----
@bp.route("/reports")
@login_required
@permission_required("view_dashboard")
def reports():
    return render_template("smartfactory/reports.html", active="sf_reports")


@bp.route("/reports/<kind>.<fmt>")
@login_required
@permission_required("view_dashboard")
def report_export(kind, fmt):
    import io
    import csv
    datasets = {
        "production": (["Line", "Order", "Hour", "Target", "Actual", "Lost min"],
                       [[r.get("line_name"), r.get("po_no"), r.get("hour_slot"), r.get("target_qty"),
                         r.get("actual_qty"), r.get("lost_min")] for r in svc.list_production(1000)]),
        "quality": (["Line", "Stage", "Inspected", "Defect", "Rework", "Reject"],
                    [[r.get("line_name"), r.get("stage"), r.get("inspected"), r.get("defect"),
                      r.get("rework"), r.get("reject")] for r in svc.list_quality(1000)]),
        "costing": (["PO", "Style", "Produced", "Labor", "Rework", "Downtime", "Wash", "Total", "Cost/pc"],
                    [[r["po_no"], r["style"], r["produced"], r["labor"], r["rework"], r["downtime"],
                      r["wash"], r["total"], r["cpp"]] for r in svc.costing()["rows"]]),
        "wash": (["Batch", "Order", "Recipe", "Water L", "Energy kWh", "Chemical kg", "Pieces", "Shade", "Rewash"],
                 [[r.get("batch_no"), r.get("po_no"), r.get("recipe"), r.get("water_l"), r.get("energy_kwh"),
                   r.get("chemical_kg"), r.get("pieces"), r.get("shade"), r.get("rewash")] for r in svc.wash_list()["rows"]]),
    }
    if kind not in datasets:
        abort(404)
    headers, rows = datasets[kind]

    def _cell(v):
        s = "" if v is None else str(v)
        return "'" + s if s[:1] in ("=", "+", "-", "@") else s

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        for r in rows:
            w.writerow([_cell(c) for c in r])
        return send_file(io.BytesIO(buf.getvalue().encode("utf-8-sig")), mimetype="text/csv",
                         as_attachment=True, download_name=f"sf_{kind}.csv")
    if fmt in ("xlsx", "excel"):
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active; ws.title = kind[:31]; ws.append(headers)
        for r in rows:
            ws.append([_cell(c) for c in r])
        bio = io.BytesIO(); wb.save(bio)
        return send_file(io.BytesIO(bio.getvalue()),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name=f"sf_{kind}.xlsx")
    abort(404)
