"""
TC Platform — BI & Analytics routes (Phase 6).

Upload any CSV/Excel file (or load a sample) and get an instant, interactive
dashboard: auto KPIs + charts, plain-language insights, anomaly flags, a
forecast, a smart-search bar, cross-filter drill-down, click-through into the
source systems (SSO), save/pin, one-click export, KPI threshold alerts wired to
the notification bell, and scheduled email digests. All computed offline.
"""
from __future__ import annotations

import io
import logging
import os

from flask import (Blueprint, render_template, request, redirect, url_for,
                   jsonify, abort, send_file, Response)

import hmac

from app.auth import login_required, permission_required, current_user, user_can
from app.db import log_audit
from app.services.bi import (analyze, insights, jobs, profiler, query,
                             reader, recommender, store)

log = logging.getLogger("tc.bi")
bp = Blueprint("bi", __name__, url_prefix="/bi")

_ALLOWED_EXT = (".csv", ".tsv", ".txt", ".xlsx", ".xlsm")


def _me():
    u = current_user()
    return u["username"] if u else "system"


def _get_dataset_or_403(ds_id):
    """Load a dataset, enforcing ownership: a non-admin may only reach datasets
    they created (admins reach any). 404 if missing, 403 if not permitted."""
    ds = store.get_dataset(ds_id)
    if not ds:
        abort(404)
    if not user_can("access_admin"):
        owner = ds["meta"].get("owner")
        if owner and owner != _me():
            abort(403)
    return ds


# --- pages ------------------------------------------------------------------
@bp.route("/")
@permission_required("open_module")
def index():
    # Admins see everything; other users see only what they created.
    scope = None if user_can("access_admin") else _me()
    return render_template("bi/workspace.html", active="bi",
                           datasets=store.list_datasets(owner=scope),
                           dashboards=store.list_dashboards(owner=scope, limit=24))


@bp.route("/dataset/<int:ds_id>")
@permission_required("open_module")
def dataset(ds_id):
    ds = _get_dataset_or_403(ds_id)
    dash = recommender.build_dashboard(ds["profile"], ds["meta"].get("name") or "Dashboard")
    return render_template("bi/dashboard.html", active="bi", dataset=ds["meta"],
                           dashboard=dash, quality=ds["quality"], saved=None,
                           columns=ds["profile"]["columns"])


@bp.route("/dashboard/<int:dash_id>")
@permission_required("open_module")
def dashboard(dash_id):
    d = store.get_dashboard(dash_id)
    if not d:
        abort(404)
    ds = store.get_dataset(d["dataset_id"])
    if not ds:
        abort(404)
    return render_template("bi/dashboard.html", active="bi", dataset=ds["meta"],
                           dashboard=d["spec"], quality=ds["quality"], saved=d,
                           columns=ds["profile"]["columns"])


# --- upload / sample --------------------------------------------------------
@bp.route("/upload", methods=["POST"])
@permission_required("open_module")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="No file selected."), 400
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in _ALLOWED_EXT:
        return jsonify(error=f"Unsupported type {ext}. Use CSV or Excel."), 400
    try:
        data = f.read()
        cols, rows = reader.read_bytes(data, f.filename)
    except reader.ReadError as exc:
        return jsonify(error=str(exc)), 400
    except Exception as exc:  # noqa: BLE001
        log.warning("upload parse failed: %s", exc)
        return jsonify(error="Could not read the file."), 400
    prof = profiler.profile(cols, rows)
    qual = profiler.data_quality(prof, rows)
    name = os.path.splitext(os.path.basename(f.filename))[0][:80] or "Dataset"
    ds_id = store.save_dataset(name, f.filename, "upload", cols, rows, prof, qual, _me())
    log_audit(_me(), "bi_upload", f"dataset={ds_id} rows={len(rows)} cols={len(cols)}")
    return jsonify(dataset_id=ds_id, name=name, n_rows=len(rows), n_cols=len(cols),
                   redirect=url_for("bi.dataset", ds_id=ds_id))


@bp.route("/sample", methods=["POST"])
@permission_required("open_module")
def sample():
    csv_text = _SAMPLE_CSV
    cols, rows = reader.read_bytes(csv_text.encode("utf-8"), "sample_sales.csv")
    prof = profiler.profile(cols, rows)
    qual = profiler.data_quality(prof, rows)
    ds_id = store.save_dataset("Sample — Sales", "sample_sales.csv", "sample",
                               cols, rows, prof, qual, _me())
    return jsonify(dataset_id=ds_id, redirect=url_for("bi.dataset", ds_id=ds_id))


# --- data APIs (drive the dashboard) ---------------------------------------
@bp.route("/api/dataset/<int:ds_id>")
@permission_required("open_module")
def api_dataset(ds_id):
    ds = _get_dataset_or_403(ds_id)
    dash = recommender.build_dashboard(ds["profile"], ds["meta"].get("name") or "Dashboard")
    return jsonify(meta=ds["meta"], columns=ds["profile"]["columns"],
                   quality=ds["quality"], dashboard=dash)


@bp.route("/api/dataset/<int:ds_id>/insights")
@permission_required("open_module")
def api_insights(ds_id):
    ds = _get_dataset_or_403(ds_id)
    lang = request.args.get("lang", "en")
    return jsonify(insights=insights.generate(ds["profile"], ds["columns"], ds["rows"], lang))


@bp.route("/api/dataset/<int:ds_id>/chart", methods=["POST"])
@permission_required("open_module")
def api_chart(ds_id):
    ds = _get_dataset_or_403(ds_id)
    body = request.get_json(silent=True) or {}
    spec = body.get("spec") or {}
    filters = body.get("filters") or []
    data = analyze.chart_data(ds["profile"], ds["columns"], ds["rows"], spec, filters)
    return jsonify(data)


@bp.route("/api/dataset/<int:ds_id>/kpis", methods=["POST"])
@permission_required("open_module")
def api_kpis(ds_id):
    ds = _get_dataset_or_403(ds_id)
    body = request.get_json(silent=True) or {}
    kpis = body.get("kpis") or []
    filters = body.get("filters") or []
    rows = analyze.apply_filters(ds["columns"], ds["rows"], filters)
    out = []
    for k in kpis:
        val = analyze.kpi_value(ds["columns"], rows, k.get("column"), k.get("agg", "sum"))
        out.append({"id": k.get("id"), "value": round(val, 3)})
    return jsonify(kpis=out, n_rows=len(rows))


@bp.route("/api/dataset/<int:ds_id>/query", methods=["POST"])
@permission_required("open_module")
def api_query(ds_id):
    ds = _get_dataset_or_403(ds_id)
    body = request.get_json(silent=True) or {}
    spec = query.parse(ds["profile"], body.get("q", ""))
    if not spec:
        return jsonify(matched=False)
    data = analyze.chart_data(ds["profile"], ds["columns"], ds["rows"], spec,
                              body.get("filters") or [])
    return jsonify(matched=True, spec=spec, data=data)


@bp.route("/api/dataset/<int:ds_id>/ask", methods=["POST"])
@permission_required("open_module")
def api_ask(ds_id):
    """AI 'ask your data': natural-language question over the dataset."""
    ds = _get_dataset_or_403(ds_id)
    body = request.get_json(silent=True) or {}
    from app.services import garamento as g
    result = g.bi_ask(
        question=body.get("q", ""),
        columns=ds["columns"],
        rows=ds["rows"],
        stats=(ds.get("profile") or {}).get("columns"),
        total_rows=len(ds["rows"] or []),
    )
    status = 200 if result.get("ok") else (503 if result.get("offline") else 200)
    return jsonify(result), status


# --- dashboard persistence --------------------------------------------------
@bp.route("/dashboard/save", methods=["POST"])
@permission_required("open_module")
def dashboard_save():
    body = request.get_json(silent=True) or {}
    dataset_id = body.get("dataset_id")
    spec = body.get("spec") or {}
    name = (body.get("name") or spec.get("name") or "Dashboard")[:120]
    lang = body.get("lang", "en")
    dash_id = body.get("dash_id")
    if not dataset_id and not dash_id:
        return jsonify(error="dataset_id required"), 400
    new_id = store.save_dashboard(name, dataset_id, spec, _me(), lang, dash_id)
    log_audit(_me(), "bi_dashboard_save", f"dashboard={new_id}")
    return jsonify(id=new_id, redirect=url_for("bi.dashboard", dash_id=new_id))


@bp.route("/dashboard/<int:dash_id>/pin", methods=["POST"])
@permission_required("open_module")
def dashboard_pin(dash_id):
    body = request.get_json(silent=True) or {}
    store.pin_dashboard(dash_id, bool(body.get("pinned", True)))
    return jsonify(ok=True)


@bp.route("/dashboard/<int:dash_id>/delete", methods=["POST"])
@permission_required("open_module")
def dashboard_delete(dash_id):
    store.delete_dashboard(dash_id)
    return jsonify(ok=True)


@bp.route("/dataset/<int:ds_id>/delete", methods=["POST"])
@permission_required("open_module")
def dataset_delete(ds_id):
    _get_dataset_or_403(ds_id)   # ownership/existence check before delete
    store.delete_dataset(ds_id)
    return jsonify(ok=True)


# --- export -----------------------------------------------------------------
@bp.route("/export/<int:ds_id>.xlsx")
@permission_required("open_module")
def export_xlsx(ds_id):
    ds = _get_dataset_or_403(ds_id)
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws.append([_safe_cell(c) for c in ds["columns"]])
    for r in ds["rows"][:20000]:
        ws.append([_safe_cell("" if v is None else v) for v in r])
    s2 = wb.create_sheet("Summary")
    dash = recommender.build_dashboard(ds["profile"], ds["meta"].get("name") or "Dashboard")
    s2.append(["KPI", "Value"])
    for k in dash["kpis"]:
        s2.append([_safe_cell(k["label"]), k.get("value")])
    s2.append([])
    s2.append(["Insights"])
    for ins in insights.generate(ds["profile"], ds["columns"], ds["rows"], "en"):
        s2.append([_safe_cell(ins["text"])])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"{ds['meta'].get('name', 'dataset')}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.route("/export/<int:ds_id>.pdf")
@permission_required("open_module")
def export_pdf(ds_id):
    ds = _get_dataset_or_403(ds_id)
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import cm
        from reportlab.pdfgen import canvas
    except Exception:
        return jsonify(error="PDF support unavailable."), 500
    dash = recommender.build_dashboard(ds["profile"], ds["meta"].get("name") or "Dashboard")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 2 * cm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(2 * cm, y, f"{ds['meta'].get('name', 'Dashboard')} — BI summary")
    y -= 1 * cm
    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, y, f"{ds['meta'].get('n_rows', 0)} rows · {ds['meta'].get('n_cols', 0)} columns · TC Platform")
    y -= 0.8 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Key figures")
    y -= 0.6 * cm
    c.setFont("Helvetica", 11)
    for k in dash["kpis"]:
        c.drawString(2.4 * cm, y, f"• {k['label']}: {k.get('value')}")
        y -= 0.55 * cm
    y -= 0.3 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Insights")
    y -= 0.6 * cm
    c.setFont("Helvetica", 11)
    for ins in insights.generate(ds["profile"], ds["columns"], ds["rows"], "en"):
        for line in _wrap(ins["text"], 90):
            if y < 2 * cm:
                c.showPage()
                y = h - 2 * cm
                c.setFont("Helvetica", 11)
            c.drawString(2.4 * cm, y, f"- {line}")
            y -= 0.55 * cm
    c.showPage()
    c.save()
    buf.seek(0)
    return send_file(buf, as_attachment=True,
                     download_name=f"{ds['meta'].get('name', 'dashboard')}.pdf",
                     mimetype="application/pdf")


def _safe_cell(v):
    """Neutralise CSV/spreadsheet formula injection: a cell beginning with one of
    = + - @ (or a leading control char) is prefixed with a quote so the
    spreadsheet treats it as text, not a formula."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for wd in words:
        if len(cur) + len(wd) + 1 > width:
            lines.append(cur)
            cur = wd
        else:
            cur = (cur + " " + wd).strip()
    if cur:
        lines.append(cur)
    return lines or [""]


# --- alerts -----------------------------------------------------------------
@bp.route("/api/dataset/<int:ds_id>/alerts")
@permission_required("open_module")
def api_alerts(ds_id):
    return jsonify(alerts=store.list_alerts(ds_id))


@bp.route("/alert", methods=["POST"])
@permission_required("open_module")
def alert_create():
    b = request.get_json(silent=True) or {}
    try:
        aid = store.create_alert(b.get("name") or b.get("column_name"), b["dataset_id"],
                                 b["column_name"], b.get("agg", "sum"), b.get("op", ">"),
                                 float(b.get("threshold", 0)), _me())
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=str(exc)), 400
    return jsonify(id=aid, ok=True)


@bp.route("/alert/<int:alert_id>/delete", methods=["POST"])
@permission_required("open_module")
def alert_delete(alert_id):
    store.delete_alert(alert_id)
    return jsonify(ok=True)


# --- digests ----------------------------------------------------------------
@bp.route("/digest", methods=["POST"])
@permission_required("open_module")
def digest_create():
    b = request.get_json(silent=True) or {}
    try:
        did = store.create_digest(b["dashboard_id"], b.get("recipients", ""),
                                  b.get("cadence", "weekly"), _me())
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=str(exc)), 400
    return jsonify(id=did, ok=True)


@bp.route("/digest/<int:digest_id>/send", methods=["POST"])
@permission_required("open_module")
def digest_send(digest_id):
    ok = jobs.send_digest(digest_id)
    return jsonify(ok=ok, configured=bool(os.getenv("TC_SMTP_HOST")))


@bp.route("/digest/<int:digest_id>/delete", methods=["POST"])
@permission_required("open_module")
def digest_delete(digest_id):
    store.delete_digest(digest_id)
    return jsonify(ok=True)


# --- scheduler hook (cron / watchdog) --------------------------------------
@bp.route("/jobs/run", methods=["POST"])
def jobs_run():
    """Evaluate alerts + send due digests. Auth: admin session OR a matching
    TC_BI_JOB_TOKEN (so an external scheduler can call it headless)."""
    token = os.getenv("TC_BI_JOB_TOKEN", "")
    sent_token = request.args.get("token") or request.headers.get("X-BI-Token") or ""
    user = current_user()
    from app.security import has_permission
    is_admin = bool(user) and has_permission(user["role"], "access_admin")
    token_ok = bool(token) and bool(sent_token) and hmac.compare_digest(str(token), str(sent_token))
    if not (is_admin or token_ok):
        abort(403)
    breaches = jobs.evaluate_alerts()
    digests = jobs.run_due_digests()
    return jsonify(breaches=breaches, digests_sent=digests)


# a tiny built-in sample so users can try BI with one click
_SAMPLE_CSV = """Date,Region,Product,Channel,Units,Revenue
2026-01-05,North,Shirt,Online,12,540
2026-01-18,South,Pants,Retail,8,720
2026-02-03,East,Jacket,Online,5,900
2026-02-20,West,Hat,Retail,20,300
2026-03-06,North,Pants,Online,15,1350
2026-03-22,South,Shirt,Retail,9,405
2026-04-09,East,Jacket,Online,7,1260
2026-04-25,West,Shirt,Retail,18,810
2026-05-08,North,Hat,Online,25,375
2026-05-24,South,Jacket,Retail,6,1080
2026-06-07,East,Pants,Online,14,1260
2026-06-21,West,Shirt,Retail,22,990
2026-07-05,North,Jacket,Online,9,1620
2026-07-19,South,Hat,Retail,30,450
2026-08-02,East,Shirt,Online,16,720
2026-08-18,West,Pants,Retail,11,990
2026-09-01,North,Jacket,Online,12,2160
2026-09-15,South,Shirt,Retail,14,630
2026-10-03,East,Hat,Online,28,420
2026-10-20,West,Jacket,Retail,10,1800
"""
