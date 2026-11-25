"""
TC Platform — Procurement Analytics workspace routes (thin controllers).

Registered on the approvals blueprint (one import line at the end of
app/routes/approvals.py), so every URL lives under /procurement/analytics and
the existing /procurement/analytics address keeps working.

Who sees what. Everyone with proc_view opens the workspace. Money — spend,
savings, open commitments, budgets, prices — is shown only when
services.money_view(user) == "full" (Purchasing, Finance, procurement admins,
super admin); everybody else gets the same page built on counts and times, and
any money measure they ask the builder or an export for is refused (403 / an
error message), never silently zeroed.
"""
import csv
import io
import re
from calendar import monthrange
from urllib.parse import parse_qs

from flask import abort, jsonify, redirect, render_template, request, send_file, url_for

from app.auth import login_required, permission_required
from app.approvals import analytics as an
from app.approvals import services as svc
from app.routes.approvals import bp, flash, _u

PIN_LIMIT = 6


def _lang():
    return (_u() or {}).get("lang_pref") or "en"


def _full():
    return svc.money_view(_u()) == "full"


def _T(lang):
    return lambda key: an.t(key, lang)


# --------------------------------------------------------------------------
# drill-down: a chart value -> the request list, filtered to it
# --------------------------------------------------------------------------
def _list_base(f):
    lo, hi = an.date_range(f)
    p = {"from": lo, "to": hi, "dept": f["dept"], "vendor": f["vendor"], "cat": f["cat"],
         "cc": f["cc"], "status": f["status"]}
    return {k: v for k, v in p.items() if v}


def _dim_params(dim, raw):
    """The request-list arguments that select one value of a dimension, or
    None when the list cannot filter by it (Others, not set, currency)."""
    if raw is None or raw == "" or raw == an.UNASSIGNED or not dim:
        return None
    simple = {"supplier": "vendor", "department": "dept", "category": "cat",
              "cost_centre": "cc", "status": "status", "kind": "kind",
              "item": "q", "requester": "q"}
    if dim in simple:
        return {simple[dim]: str(raw)[:100]}
    try:
        if dim == "month":
            y, m = int(raw[:4]), int(raw[5:7])
            return {"from": "%04d-%02d-01" % (y, m), "to": "%04d-%02d-%02d" % (y, m, monthrange(y, m)[1])}
        if dim == "quarter":
            y, q = int(raw[:4]), int(raw[-1])
            m1, m3 = 3 * q - 2, 3 * q
            return {"from": "%04d-%02d-01" % (y, m1), "to": "%04d-%02d-%02d" % (y, m3, monthrange(y, m3)[1])}
    except (TypeError, ValueError, IndexError):
        return None
    return None


def _spec(cfg, rows, f, lang, title=""):
    """Chart-ready JSON for one result: translated labels, series, and the
    drill-down arguments of every label / series."""
    sh = an.shape(cfg, rows)
    d1, d2, m = cfg["d1"], cfg.get("d2") or "", cfg["m"]
    mlabel = an.t("proc.analytics.m." + m, lang)
    series = [{"label": an.label(d2, s, lang) if d2 else mlabel, "data": data}
              for s, data in zip(sh["series_keys"], sh["data"])]
    return {
        "title": title, "chart": cfg["chart"], "measure": m, "unit": an.unit_of(m),
        "money": m in an.money_measures(), "two": bool(d2),
        "labels": [an.label(d1, k, lang) for k in sh["keys"]],
        "series": series, "n": sh["n"],
        "dp": [_dim_params(d1, k) for k in sh["keys"]],
        "sp": [_dim_params(d2, s) for s in sh["series_keys"]] if d2 else None,
        "base": _list_base(f),
        "cols": [an.t("proc.analytics.dim." + d1, lang)]
                + ([an.t("proc.analytics.dim." + d2, lang)] if d2 else [])
                + [mlabel + (" — " + an.t("proc.analytics.agg." + cfg["agg"], lang)
                             if cfg["agg"] not in ("sum", "count") else "")]
                + ([] if d2 else [an.t("proc.analytics.col.n", lang)]),
    }


def _run(cfg, f, lang, title="", conn=None):
    return _spec(cfg, an.query(cfg, f, conn=conn), f, lang, title)


def _flat(spec):
    """The table a spec shows, as plain rows (exports)."""
    out = []
    for i, lab in enumerate(spec["labels"]):
        if spec["two"]:
            for s in spec["series"]:
                out.append([lab, s["label"], s["data"][i]])
        else:
            out.append([lab, spec["series"][0]["data"][i], (spec["n"] or [None] * len(spec["labels"]))[i]])
    return out


# --------------------------------------------------------------------------
# file exports
# --------------------------------------------------------------------------
def _safe(v):
    """CSV formula-injection guard: user text starting = + - @ stays text."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _send(fmt, name, headers, rows, title=""):
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        w.writerows([[_safe(v) for v in r] for r in rows])
        data = ("﻿" + buf.getvalue()).encode("utf-8")   # BOM: Excel reads Arabic
        return send_file(io.BytesIO(data), mimetype="text/csv", as_attachment=True,
                         download_name=name + ".csv")
    from app.approvals.xlsx import _Sheet, MIME, MONEY
    sh = _Sheet(re.sub(r"[\\/?*\[\]:]", " ", title or name))   # Excel refuses these in a sheet name
    if title:
        sh.line(title, bold=True, size=13)
    numeric = [any(isinstance(r[j], float) for r in rows) for j in range(len(headers))]
    sh.table([(h, MONEY if numeric[j] else None) for j, h in enumerate(headers)], rows)
    return send_file(io.BytesIO(sh.save()), mimetype=MIME, as_attachment=True,
                     download_name=name + ".xlsx")


def _err(code, lang):
    return an.t("proc.analytics.err." + code, lang)


# --------------------------------------------------------------------------
# the dashboard (served by approvals.analytics, the original URL)
# --------------------------------------------------------------------------
def dashboard_page():
    user, lang, full = _u(), _lang(), _full()
    f = an.parse_filters(request.args)
    try:
        rise = float(request.args.get("rise") or 10)
    except ValueError:
        rise = 10.0
    rise = max(0.0, min(1000.0, rise))
    m = "spend" if full else "requests"
    T = _T(lang)
    from app.db import get_db
    conn = get_db()
    try:
        k = an.kpis(f, conn=conn)
        sfx = "" if full else "_n"
        charts = [
            ("trend", T("proc.analytics.c.trend" + sfx), {"d1": "month", "m": m, "chart": "line", "top": 24}),
            ("supplier", T("proc.analytics.c.supplier" + sfx), {"d1": "supplier", "m": m, "chart": "hbar", "top": 10}),
            ("dept", T("proc.analytics.c.dept" + sfx), {"d1": "department", "m": m, "chart": "bar", "top": 12}),
            ("cat", T("proc.analytics.c.cat" + sfx), {"d1": "category", "m": m, "chart": "doughnut", "top": 8}),
            ("kind", T("proc.analytics.c.kind"), {"d1": "kind", "m": m, "chart": "doughnut", "top": 4}),
            ("status", T("proc.analytics.c.status"), {"d1": "status", "m": "requests", "chart": "doughnut", "top": 12}),
        ]
        specs = []
        for key, title, raw in charts:
            cfg, _e = an.parse_config(raw, money=full)
            spec = _run(cfg, f, lang, title, conn)
            spec["key"] = key
            spec["qs"] = dict(an.config_args(cfg), **an.filter_args(f))
            specs.append(spec)
        stages = an.stage_times(f, conn=conn)
        suppliers = an.supplier_performance(f, conn=conn)
        savings = an.savings_totals(f, conn=conn) if full else None
        year, budget = an.budget_vs_actual(f, conn=conn) if full else (None, [])
        prices = an.price_history(f, rise, request.args.get("item", "")[:200], conn=conn) if full else None
        options = an.filter_options(conn)
    finally:
        conn.close()
    for s in stages:
        s["label"] = an.stage_name(s["stage"], lang)
    base = _list_base(f)

    def mini(key, title, labels, series, chart, unit="", dp=None, money=False, colors=None):
        return {"key": key, "title": title, "labels": labels, "chart": chart, "unit": unit,
                "series": series, "n": None, "two": len(series) > 1, "dp": dp, "sp": None,
                "base": base, "money": money, "colors": colors, "cols": None}
    timed = [s for s in stages if s["hours"] is not None]     # a stage nobody signed yet has no time
    extra = {"stages": mini("stages", T("proc.analytics.c.stages"), [s["label"] for s in timed],
                            [{"label": T("proc.analytics.col.hours"), "data": [s["hours"] for s in timed]}],
                            "hbar", "h", colors=["hot"] + ["base"] * max(0, len(timed) - 1)),
             "ontime": mini("ontime", T("proc.analytics.c.suppliers"), [r["supplier"] for r in suppliers],
                            [{"label": T("proc.analytics.col.ontime"), "data": [r["ontime"] for r in suppliers]}],
                            "hbar", "%", dp=[{"vendor": r["supplier"]} for r in suppliers])}
    if full:
        extra["savings"] = mini("savings", T("proc.analytics.c.savings"),
                                [T("proc.analytics.q." + q) for q in ("first", "hi", "lo", "chosen")],
                                [{"label": "EGP", "data": [savings["firstq"], savings["hi"], savings["lo"],
                                                           savings["chosen"]]}], "bar", "EGP", money=True,
                                colors=["muted", "muted", "good", "base"])
        extra["budget"] = mini("budget", T("proc.analytics.c.budget") + " — " + str(year),
                               [r["dept"] or T("proc.analytics.none") for r in budget],
                               [{"label": T("proc.analytics.budget"), "data": [r["budget"] for r in budget]},
                                {"label": T("proc.analytics.committed"), "data": [r["actual"] for r in budget]}],
                               "bar", "EGP", dp=[{"dept": r["dept"]} if r["dept"] else None for r in budget],
                               money=True, colors=["muted", "base"])
        extra["price"] = mini("price", prices["item"] or T("proc.analytics.c.price"),
                              [p["month"] for p in prices["points"]],
                              [{"label": T("proc.analytics.col.price"), "data": [p["price"] for p in prices["points"]]}],
                              "line", "EGP", dp=[{"q": prices["item"][:100]} if prices["item"] else None
                                                 for _p in prices["points"]], money=True)
    pinned = []
    for r in an.visible_reports(user, full):
        if not r.get("pinned") or len(pinned) >= PIN_LIMIT:
            continue
        cfg, e = an.parse_config((r["config"] or {}).get("cfg") or {}, money=full)
        if e:
            continue
        rf = an.parse_filters((r["config"] or {}).get("filters") or {})
        spec = _run(cfg, rf, lang, r["name"])
        spec["rid"] = r["id"]
        pinned.append(spec)
    if not full:
        k = {key: v for key, v in k.items() if key not in ("spend", "open_commit", "savings", "missed", "vs_first")}
    return render_template(
        "approvals/analytics.html", active="proc_analytics", T=T, lang=lang,
        f=f, fargs=an.filter_args(f), rng=an.date_range(f), options=options,
        statuses=an.STATUSES, periods=an.PERIODS, show_money=full,
        k=k, specs=specs, stages=stages, suppliers=suppliers, savings=savings,
        year=year, budget=budget, prices=prices, rise=rise, pinned=pinned, extra=extra,
        list_base=_list_base(f), an=an,
        reset_url=url_for("approvals.analytics"), keep_extra={"rise": request.args.get("rise")} if request.args.get("rise") else {})


# --------------------------------------------------------------------------
# builder
# --------------------------------------------------------------------------
@bp.route("/analytics/builder")
@login_required
@permission_required("proc_view")
def analytics_builder():
    user, lang, full = _u(), _lang(), _full()
    reports = an.visible_reports(user, full)
    editing = None
    args = request.args
    rid = request.args.get("rid", type=int)
    if rid:
        editing = next((r for r in reports if r["id"] == rid), None)
        if not editing:
            flash("Report not found.", "error")
            return redirect(url_for("approvals.analytics_builder"))
        if "d1" not in request.args:
            saved = editing["config"] or {}
            args = dict((saved.get("filters") or {}), **(saved.get("cfg") or {}))
    f = an.parse_filters(args)
    cfg, err = an.parse_config(args, money=full)
    if err:
        cfg, _e = an.parse_config({}, money=full)
    spec = _run(cfg, f, lang)
    from app.db import get_db
    conn = get_db()
    try:
        options = an.filter_options(conn)
    finally:
        conn.close()
    T = _T(lang)
    measures = [m for m in an.MEASURES if full or m not in an.money_measures()]
    return render_template(
        "approvals/analytics_builder.html", active="proc_analytics", T=T, lang=lang,
        f=f, fargs=an.filter_args(f), options=options, statuses=an.STATUSES,
        periods=an.PERIODS, cfg=cfg, err=err and _err(err, lang), spec=spec,
        dims=an.DIMS, measures=measures, aggs={m: an.MEASURES[m][1] for m in measures},
        charts=an.CHARTS, sorts=an.SORTS, line_dims=sorted(an.LINE_DIMS),
        share_only=[m for m, v in an.MEASURES.items() if v[2] == "share"],
        mine=[r for r in reports if r["owner"] == user["username"]],
        shared=[r for r in reports if r["owner"] != user["username"]],
        editing=editing if editing and editing["owner"] == user["username"] else None,
        viewing=editing, show_money=full, an=an,
        reset_url=url_for("approvals.analytics_builder", **an.config_args(cfg)),
        keep_extra=dict(an.config_args(cfg), **({"rid": rid} if rid else {})))


@bp.route("/analytics/run.<fmt>")
@login_required
@permission_required("proc_view")
def analytics_run(fmt):
    """One builder result as JSON (live preview), CSV or Excel."""
    if fmt not in ("json", "csv", "xlsx"):
        abort(404)
    lang, full = _lang(), _full()
    f = an.parse_filters(request.args)
    cfg, err = an.parse_config(request.args, money=full)
    if err:
        if fmt == "json":
            return jsonify({"error": _err(err, lang), "code": err}), (403 if err == "money_hidden" else 400)
        abort(403 if err == "money_hidden" else 400)
    spec = _run(cfg, f, lang)
    if fmt == "json":
        return jsonify(spec)
    title = an.t("proc.analytics.m." + cfg["m"], lang) + " — " + " / ".join(
        an.t("proc.analytics.dim." + d, lang) for d in (cfg["d1"], cfg.get("d2")) if d)
    return _send(fmt, "procurement_%s_by_%s" % (cfg["m"], cfg["d1"]), spec["cols"], _flat(spec), title)


_DATASETS = ("stages", "suppliers", "savings", "budget", "price_alerts")


@bp.route("/analytics/data/<key>.<fmt>")
@login_required
@permission_required("proc_view")
def analytics_data(key, fmt):
    """The dashboard's non-builder tables as CSV / Excel."""
    if key not in _DATASETS or fmt not in ("csv", "xlsx"):
        abort(404)
    lang, full = _lang(), _full()
    T = _T(lang)
    if key in ("savings", "budget", "price_alerts") and not full:
        abort(403)
    f = an.parse_filters(request.args)
    if key == "stages":
        rows = an.stage_times(f)
        heads = [T("proc.analytics.col.stage"), T("proc.analytics.col.hours"), T("proc.analytics.col.steps"),
                 T("proc.analytics.col.rejected"), T("proc.analytics.col.waiting")]
        body = [[an.stage_name(r["stage"], lang), r["hours"], r["steps"], r["rejected"], r["waiting"]] for r in rows]
    elif key == "suppliers":
        rows = an.supplier_performance(f, top=500)
        heads = ([T("proc.analytics.col.supplier")] + ([T("proc.analytics.col.spend")] if full else [])
                 + [T("proc.analytics.col.pos"), T("proc.analytics.col.lead"), T("proc.analytics.col.ontime") + " %",
                    T("proc.analytics.col.overdue"), T("proc.analytics.col.reject") + " %",
                    T("proc.analytics.col.price_var") + " %"])
        body = [[r["supplier"]] + ([r["spend"]] if full else [])
                + [r["pos"], r["lead"], r["ontime"], r["overdue"], r["reject"], r["price_var"]] for r in rows]
    elif key == "savings":
        s = an.savings_totals(f)
        heads = [T("proc.analytics.col.value"), "EGP"]
        body = [[T("proc.analytics.q.first"), s["firstq"]], [T("proc.analytics.q.hi"), s["hi"]],
                [T("proc.analytics.q.lo"), s["lo"]], [T("proc.analytics.q.chosen"), s["chosen"]],
                [T("proc.analytics.k.savings"), round(s["hi"] - s["chosen"], 2)]]
    elif key == "budget":
        year, rows = an.budget_vs_actual(f)
        heads = [T("proc.analytics.f.dept"), T("proc.analytics.year"), T("proc.analytics.budget") + " (EGP)",
                 T("proc.analytics.committed") + " (EGP)", T("proc.analytics.used") + " %"]
        body = [[r["dept"] or "—", year, r["budget"], r["actual"], r["pct"]] for r in rows]
    else:
        try:
            rise = float(request.args.get("rise") or 10)
        except ValueError:
            rise = 10.0
        rows = an.price_history(f, rise, limit=1000)["alerts"]
        heads = [T("proc.analytics.col.item"), T("proc.analytics.col.month"), T("proc.analytics.col.prev"),
                 T("proc.analytics.col.month"), T("proc.analytics.col.last"), T("proc.analytics.col.rise") + " %"]
        body = [[r["item"], r["prev_month"], r["prev"], r["month"], r["last"], r["rise"]] for r in rows]
    return _send(fmt, "procurement_" + key, heads, body, T("proc.analytics.title"))


# --------------------------------------------------------------------------
# saved reports
# --------------------------------------------------------------------------
_SAVE_ERRORS = {
    "name_required": "Give the report a name.",
    "money_hidden": "This report shows money, which your role does not see.",
    "share_denied": "Only Purchasing, Finance and procurement admins can share reports.",
    "not_found": "Report not found.",
    "not_owner": "Only the person who created this report can change it.",
    "bad_dim": "Choose what to group the report by.",
    "bad_measure": "Choose what the report should measure.",
    "needs_request_dim": "This measure is counted per request, so it cannot be split by item or category.",
}


def _back(default):
    nxt = request.form.get("next") or ""
    return nxt if nxt.startswith("/procurement/analytics") and "//" not in nxt else default


@bp.route("/analytics/reports", methods=["POST"])
@login_required
@permission_required("proc_view")
def analytics_save():
    user, full = _u(), _full()
    qs = {k: v[0] for k, v in parse_qs(request.form.get("qs", "")[:4000]).items() if v}
    cfg, err = an.parse_config(qs, money=full)
    rid = request.form.get("rid", type=int) if request.form.get("mode") != "new" else None
    if err:
        flash(_SAVE_ERRORS.get(err, "Report not found."), "error")
        return redirect(url_for("approvals.analytics_builder", **qs))
    ok, code, rid = an.save_report(user, request.form.get("name", ""), cfg, an.parse_filters(qs),
                                   shared=request.form.get("shared") == "1", rid=rid, full_money=full)
    if not ok:
        flash(_SAVE_ERRORS.get(code, "Report not found."), "error")
        return redirect(url_for("approvals.analytics_builder", **qs))
    flash("Report saved.", "success")
    return redirect(url_for("approvals.analytics_builder", rid=rid))


@bp.route("/analytics/reports/<int:rid>/delete", methods=["POST"])
@login_required
@permission_required("proc_view")
def analytics_delete(rid):
    ok, code = an.delete_report(_u(), rid)
    flash("Report deleted." if ok else _SAVE_ERRORS.get(code, "Report not found."),
          "success" if ok else "error")
    return redirect(_back(url_for("approvals.analytics_builder")))


@bp.route("/analytics/reports/<int:rid>/pin", methods=["POST"])
@login_required
@permission_required("proc_view")
def analytics_pin(rid):
    ok, code = an.toggle_pin(_u(), rid, _full())
    if not ok:
        flash(_SAVE_ERRORS.get(code, "Report not found."), "error")
    else:
        flash("Report pinned to the analytics dashboard." if code == "pinned" else "Report unpinned.", "success")
    return redirect(_back(url_for("approvals.analytics_builder")))
