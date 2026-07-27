"""
Smart Factory (/factory) — routes.

/factory is a PRESENTATION layer: every page here reads the module that OWNS the
record (MES, QMS, wash, orders, warehouse, people, costing) and writes nothing.
See app/smartfactory/services.py for why.

The four POST endpoints that used to write /factory's own copy of the floor are
kept — a bookmarked or cached form must not 404 — but they now say where the
record is made and redirect there. Nothing is written on the way past, because
writing a real module's table from here would step around that module's own
validation and its own permission, and a supervisor with /factory rights would be
recording MES output without MES rights.

  /factory              command center (MES day roll-up + QMS + wash)
  /factory/floor        the big live board
  /factory/production   the MES hourly board          (entry: /mes/entry)
  /factory/quality      the QMS inspection register   (entry: /quality)
  /factory/bundles      the MES bundle ledger + warehouse rolls (entry: /mes/bundles)
  /factory/wash         executed wash lots            (entry: /wash/batches)
  /factory/orders       the platform order book + canonical SMV
  /factory/workforce    piece-rate scorecards from /people
  /factory/costing      floor conversion cost per piece
  /factory/intelligence money-first explainability
  /factory/approvals    STILL its own sf_approvals data — labelled as such
  /factory/reports      exports of the above

Viewing needs `view_dashboard`; the (now redirecting) entry routes still need
`manage_production`, exactly as before.
"""
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   send_file, abort)

from app.auth import login_required, permission_required, current_user
from app.smartfactory import services as svc
from app.smartfactory import approvals as appr

bp = Blueprint("smartfactory", __name__, url_prefix="/factory")

# Where each /factory form's record is actually made now.
_MOVED = {
    "production": ("mes.entry", "Hourly production is recorded on the MES board."),
    "quality": ("quality.new", "Inspections are recorded in the Quality module."),
    "bundles": ("mes.bundles", "Bundles are created and scanned in the MES bundle ledger."),
    "wash": ("wash.batches", "Wash batches are recorded against a recipe version in Wash."),
}


def _u():
    u = current_user()
    return (u.get("full_name") or u.get("username")) if u else ""


def _moved(what):
    """Tell the user where the record is made, then send them there. Deliberately
    NOT a silent write to the owning module: that module's own screen is where its
    rules, its permission and its validation live."""
    endpoint, msg = _MOVED[what]
    flash(msg + " Nothing was saved here — please enter it there.", "error")
    return redirect(url_for(endpoint))


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
                           rows=svc.list_production())


@bp.route("/production", methods=["POST"])
@login_required
@permission_required("manage_production")
def production_add():
    return _moved("production")


@bp.route("/quality", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def quality():
    data = svc.dashboard()
    return render_template("smartfactory/quality.html", active="sf_quality",
                           rows=svc.list_quality(), pareto=data["pareto"], kpis=data["kpis"])


@bp.route("/quality", methods=["POST"])
@login_required
@permission_required("manage_production")
def quality_add():
    return _moved("quality")


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
    return render_template("smartfactory/bundles.html", active="sf_bundles", **svc.bundles())


@bp.route("/bundles", methods=["POST"])
@login_required
@permission_required("manage_production")
def bundles_add():
    return _moved("bundles")


# ---- Laundry / wash ----
@bp.route("/wash", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def wash():
    return render_template("smartfactory/wash.html", active="sf_wash", **svc.wash_list())


@bp.route("/wash", methods=["POST"])
@login_required
@permission_required("manage_production")
def wash_add():
    return _moved("wash")


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


# ---- Smart Intelligence: money-first + explainability + ship-risk ----
@bp.route("/intelligence")
@login_required
@permission_required("view_dashboard")
def intelligence():
    return render_template("smartfactory/intelligence.html", active="sf_intel",
                           bridge=svc.efficiency_bridge(), bank=svc.minute_bank(), ship=svc.ship_risk())


# ---- Smart approval engine + cost intelligence ----
# NOT converted: this is a self-contained cost-approval ladder on sf_approvals, and
# re-pointing it at the platform's procurement ladder would move approval
# behaviour. Its order picker therefore stays on the LEGACY sf_orders list — the
# rows already in sf_approvals hold sf_orders ids, and handing it real order ids
# would silently re-label every request in the table. The page is labelled in the
# UI as running on its own /factory data.
@bp.route("/approvals", methods=["GET"])
@login_required
@permission_required("view_dashboard")
def approvals():
    status = request.args.get("status", "open")
    return render_template("smartfactory/approvals.html", active="sf_approvals",
                           rows=appr.list_approvals(status), status=status,
                           dash=appr.dashboard(), kinds=appr.KINDS, orders=svc.list_sf_orders(),
                           lines=svc.list_lines(), can_decide=(True))


@bp.route("/approvals", methods=["POST"])
@login_required
@permission_required("manage_production")
def approvals_submit():
    f = request.form
    try:
        ref, st = appr.submit(f.get("kind", "rework"), f.get("title", ""), f.get("description", ""),
                              f.get("cost_amount", 0), f.get("order_id") or None, f.get("line_id") or None,
                              f.get("qty", 0), _u(), f.get("department", ""))
        flash(f"Request {ref} — {st.replace('_', ' ')}.", "success")
    except Exception:  # noqa: BLE001
        flash("Could not submit the request.", "error")
    return redirect(url_for("smartfactory.approvals"))


@bp.route("/approvals/<int:aid>/decide", methods=["POST"])
@login_required
@permission_required("manage_production")
def approvals_decide(aid):
    f = request.form
    ok = appr.decide(aid, f.get("decision", "approve"), _u(), f.get("comment", ""))
    flash("Decision recorded." if ok else "Already decided or not found.", "success" if ok else "error")
    return redirect(url_for("smartfactory.approvals"))


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
    """Same four downloads, now of the REAL records. The owning modules export
    their own datasets too (/mes/export, /quality/export, /wash/export); these stay
    because they are the cross-module cut /factory presents on one screen."""
    import io
    import csv
    datasets = {
        "production": (["Date", "Line", "Order", "Hour", "Target", "Actual", "Reject",
                        "Operators", "SMV"],
                       [[r.get("work_date"), r.get("line_name"), r.get("po_no"), r.get("hour_slot"),
                         r.get("target_qty"), r.get("actual_qty"), r.get("reject_qty"),
                         r.get("operators"), r.get("smv")] for r in svc.list_production(1000)]),
        "quality": (["Ref", "Order", "Stage", "Units", "Defective", "Defects", "DHU", "RFT",
                     "Verdict"],
                    [[r.get("ref"), r.get("po_no"), r.get("stage"), r.get("units_inspected"),
                      r.get("defective_units"), r.get("defects"), r.get("dhu"), r.get("rft"),
                      r.get("verdict")] for r in svc.list_quality(1000)]),
        "costing": (["PO", "Style", "Produced", "Reject", "SMV", "Rate/min", "Rate source",
                     "Labour", "Scrap", "Wash", "Total", "Cost/pc"],
                    [[r["po_no"], r["style"], r["produced"], r["reject"], r["smv"], r["rate"],
                      "cost sheet" if r["priced"] else "default tariff",
                      r["labor"], r["rework"], r["wash"], r["total"], r["cpp"]]
                     for r in svc.costing()["rows"]]),
        "wash": (["Batch", "Order", "Recipe", "Load kg", "Water L", "Metered", "Chemical kg",
                  "Heat L.K", "Shade", "Deviation"],
                 [[r.get("batch_no"), r.get("po_no"), r.get("recipe"), r.get("load_kg"),
                   r.get("water_l"), "yes" if r.get("water_metered") else "recipe",
                   r.get("chem_kg"), r.get("heat_lk"), r.get("shade"), r.get("deviation")]
                  for r in svc.wash_list()["rows"]]),
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
