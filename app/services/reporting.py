"""
TC Platform — the shared reporting engine.

ONE engine, twelve-plus modules. A module declares its reports as plain data at
import time (no DB work at import — see the deploy constraint in CLAUDE.md) and
gets, for free: date-range presets, per-report filters read from the query
string (so a filtered report is a shareable URL), summary KPIs with
period-over-period change, one chart drawn with the VENDORED Chart.js, a
sortable table with a totals row, CSV / XLSX / PDF export of exactly what is on
screen, per-user saved views, and permission enforcement on every one of those
surfaces.

WHY a declarative spec and not twelve report pages: twelve pages is twelve times
the bugs. The module supplies SQL fragments; the engine composes them, binds
every user-supplied value as a parameter, and never interpolates user input into
SQL.

Query budget for a report page: 1 rows + 1 aggregate (KPIs + totals + row count)
+ 1 aggregate for the prior period (only when a date window is set) + 1 chart
(only when declared) = 2..4, CONSTANT in the number of rows. Aggregation happens
in SQL; nothing loops over the dataset in Python.

CSV and XLSX are delegated to app.services.reports.export() — already written,
already BOM'd for Excel + Arabic. Only the PDF is ours, because a self-describing
PDF has to carry the filter description and the KPIs, which that one does not.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta

from app.db import get_db
from app.services import reports as _reports   # csv/xlsx exporters — do NOT reinvent

# HTML view shows at most this many rows (a page that renders 100k rows is a
# broken page). The full set is always available in the export. Chosen over
# pagination deliberately: one knob, no page-state to carry through 6 surfaces.
ROW_CAP = 500
EXPORT_CAP = 50000          # ceiling on an export, so one click cannot OOM a worker
CHART_LIMIT = 30            # a chart with 200 bars is not a chart

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")

# Date-range presets offered on every report that declares a date column.
PRESETS = ["all", "today", "week", "month", "quarter", "year", "custom"]


# ---------------------------------------------------------------------------
# Declaration helpers — a module builds its spec out of these
# ---------------------------------------------------------------------------
def col(key, en, ar, tr, kind="text", total=None):
    """A table column.

    key   — the SELECT alias it is read from (also the sort key). [a-z_][a-z0-9_]*
    kind  — "text" | "num" | "date". num/date render dir=ltr so a minus sign or a
            date never flips under RTL; num is right-aligned and tabular.
    total — OPTIONAL SQL aggregate for the totals row, e.g. "SUM(t.qty)".
            Evaluated in the same aggregate query as the KPIs (zero extra
            queries). When the spec sets `group`, write it over the grouped
            sub-query alias, e.g. "SUM(qty)".
    """
    _check_ident(key, "column key")
    return {"key": key, "en": en, "ar": ar, "tr": tr, "kind": kind, "total": total}


def kpi(key, en, ar, tr, expr, kind="num", better="up"):
    """A summary number.

    expr   — SQL aggregate over the report's FROM clause, e.g. "SUM(p.total)".
             When the spec sets `group`, it is evaluated over the grouped
             sub-query aliased `t`.
    better — "up" | "down" | "none": which direction of change is good. Drives
             the colour of the period-over-period badge only.
    """
    _check_ident(key, "kpi key")
    return {"key": key, "en": en, "ar": ar, "tr": tr, "expr": expr,
            "kind": kind, "better": better}


def filt(name, en, ar, tr, sql_col, kind="text", op="like", options=None):
    """A per-report filter, read from the query string.

    name    — query-string parameter name. [a-z_][a-z0-9_]*
    sql_col — the column/expression it filters on (module-supplied, never user
              input). The VALUE is always bound as a parameter.
    kind    — "text" | "select" | "num" | "date"
    op      — "like" | "=" | "!=" | ">" | ">=" | "<" | "<="
    options — list of values for kind="select". Either plain strings, or
              (value, en, ar, tr) tuples when the option label needs translating.
    """
    _check_ident(name, "filter name")
    if op not in ("like", "=", "!=", ">", ">=", "<", "<="):
        raise ValueError(f"reporting.filt: bad op {op!r}")
    return {"name": name, "en": en, "ar": ar, "tr": tr, "col": sql_col,
            "kind": kind, "op": op, "options": options or []}


def chart(kind, label_expr, value_expr, group_by, en, ar, tr, limit=CHART_LIMIT):
    """The report's ONE chart. Omit it when the data has no meaningful chart —
    a decorative chart is worse than no chart.

    kind        — "trend" (line, ordered by label ascending)
                | "bar"   (ordered by value descending)
                | "pareto"(bar descending + cumulative % line)
    label_expr  — SQL for the category/date axis, e.g. "date(t.created_at)"
    value_expr  — SQL aggregate for the value, e.g. "COUNT(*)" / "SUM(t.total)"
    group_by    — SQL GROUP BY body, normally the same text as label_expr
    """
    if kind not in ("trend", "bar", "pareto"):
        raise ValueError(f"reporting.chart: bad kind {kind!r}")
    return {"kind": kind, "label_expr": label_expr, "value_expr": value_expr,
            "group_by": group_by, "en": en, "ar": ar, "tr": tr,
            "limit": int(limit)}


def _check_ident(v, what):
    if not _IDENT.match(v or ""):
        raise ValueError(f"reporting: {what} {v!r} must match [a-z_][a-z0-9_]*")


# ---------------------------------------------------------------------------
# Registry — plain data, declared at import time. NOTHING here touches the DB.
# ---------------------------------------------------------------------------
_REGISTRY = {}      # key -> spec
_ORDER = []         # registration order


def register(key, module, module_label, title, title_ar, title_tr, perm,
             select, frm, columns, desc="", desc_ar="", desc_tr="",
             module_label_ar="", module_label_tr="",
             filters=None, date_col=None, kpis=None, chart=None,
             group=None, order=None, base_where=None, row_cap=ROW_CAP,
             icon="i-report"):
    """Declare a report. Call at MODULE IMPORT time from a file your blueprint
    already imports (e.g. the bottom of app/routes/<module>.py, or
    app/<module>/reports.py imported from there) — the hub reads the registry per
    request, so registration order is irrelevant.

    key          — globally unique, "<module>_<name>", [a-z_][a-z0-9_]*
    module       — nav/module key ("mes", "quality", ...); groups the hub
    module_label, module_label_ar, module_label_tr
                 — display name of the module group in the 3 languages. Copy the
                   text your sidebar already uses: look up nav.<module> in
                   app/static/i18n/{en,ar,tr}.json. ar/tr default to the English
                   one. Rendered with data-loc-* so nothing can render as a raw
                   i18n key.
    title/_ar/_tr, desc/_ar/_tr — report name and one-line description, 3 langs
    perm         — the MODULE's own permission. Enforced on the HTML page AND on
                   every export format. A user who cannot open the module cannot
                   read its numbers here.
    select       — SELECT list, aliased to the column keys.
                   "p.pr_no AS pr_no, v.name AS vendor, SUM(l.qty) AS qty"
    frm          — FROM clause body (table + joins), no "FROM" keyword.
                   "pr_requests p LEFT JOIN proc_vendors v ON v.id = p.vendor_id"
    columns      — [col(...)] in display order
    filters      — [filt(...)]; the date range is added automatically from date_col
    date_col     — SQL column for the date range + period comparison, e.g.
                   "p.created_at". None = no date filtering and no KPI comparison.
    kpis         — [kpi(...)], 0..6 of them
    chart        — chart(...) or None
    group        — GROUP BY body when the report is itself an aggregate
    order        — default ORDER BY body, e.g. "p.id DESC"
    base_where   — [str] always-on SQL conditions (no user input, no params)
    row_cap      — HTML row cap for this report (default 500)

    Returns the spec dict. Re-registering the same key replaces it (module reload
    under the reloader must not raise).
    """
    _check_ident(key, "report key")
    spec = {
        "key": key, "module": module,
        "module_label": module_label,
        "module_label_ar": module_label_ar or module_label,
        "module_label_tr": module_label_tr or module_label,
        "title": title, "title_ar": title_ar, "title_tr": title_tr,
        "desc": desc, "desc_ar": desc_ar, "desc_tr": desc_tr,
        "perm": perm, "select": select, "frm": frm,
        "columns": list(columns or []), "filters": list(filters or []),
        "date_col": date_col, "kpis": list(kpis or []), "chart": chart,
        "group": group, "order": order, "base_where": list(base_where or []),
        "row_cap": int(row_cap), "icon": icon,
    }
    if key not in _REGISTRY:
        _ORDER.append(key)
    _REGISTRY[key] = spec
    return spec


def get(key):
    return _REGISTRY.get(key)


def all_reports():
    return [_REGISTRY[k] for k in _ORDER]


def catalog(can):
    """Reports the user may view, grouped by module. `can` is callable(perm)->bool.
    Groups appear in first-registration order."""
    groups, seen = [], {}
    for spec in all_reports():
        if not can(spec["perm"]):
            continue
        g = seen.get(spec["module"])
        if g is None:
            g = {"module": spec["module"],
                 "en": spec["module_label"],
                 "ar": spec["module_label_ar"],
                 "tr": spec["module_label_tr"],
                 "reports": []}
            seen[spec["module"]] = g
            groups.append(g)
        g["reports"].append(spec)
    return groups


def loc(obj, lang, base=""):
    """Pick the localized string off a spec/col/kpi dict for server-side text
    (PDF, exports, <title>). HTML uses data-loc-* so app.js can swap live."""
    if lang in ("ar", "tr"):
        return obj.get(f"{base}_{lang}" if base else lang) or obj.get(base or "en") or ""
    return obj.get(base or "en") or ""


# ---------------------------------------------------------------------------
# Filters + date window
# ---------------------------------------------------------------------------
def _today():
    return date.today()


def _preset_window(preset, args, t):
    """(from_date, to_date, error) for a preset. Preset "all" never reaches here —
    it means no date filtering, because a report must not silently hide rows the
    user did not ask to hide. from>to is an ERROR, not an empty table."""
    if preset == "today":
        return t, t, None
    if preset == "week":
        return t - timedelta(days=t.weekday()), t, None
    if preset == "month":
        return t.replace(day=1), t, None
    if preset == "quarter":
        m = 3 * ((t.month - 1) // 3) + 1
        return t.replace(month=m, day=1), t, None
    if preset == "year":
        return t.replace(month=1, day=1), t, None
    # custom
    f = _parse_date(args.get("from"))
    to = _parse_date(args.get("to"))
    if (args.get("from") or "").strip() and f is None:
        return None, None, "bad_date"
    if (args.get("to") or "").strip() and to is None:
        return None, None, "bad_date"
    if f and to and f > to:
        return None, None, "inverted_range"
    return f, to, None


def _parse_date(v):
    v = (v or "").strip()[:10]
    if not v:
        return None
    try:
        return datetime.strptime(v, "%Y-%m-%d").date()
    except ValueError:
        return None


def _date_clauses(date_col, f, t):
    clauses, params = [], []
    if f:
        clauses.append(f"{date_col} >= ?")
        params.append(f.isoformat())
    if t:
        # inclusive end-of-day: works for DATE and TIMESTAMP columns on both
        # SQLite and PostgreSQL
        clauses.append(f"{date_col} <= ?")
        params.append(t.isoformat() + " 23:59:59")
    return clauses, params


def _filter_clauses(spec, args):
    """Non-date filters. Column names come from the module (trusted); every
    VALUE is bound as a parameter."""
    clauses, params, applied = [], [], []
    for f in spec["filters"]:
        v = (args.get(f["name"]) or "").strip()
        if not v:
            continue
        if f["op"] == "like":
            clauses.append(f"{f['col']} LIKE ?")
            params.append(f"%{v}%")
        else:
            clauses.append(f"{f['col']} {f['op']} ?")
            params.append(v)
        applied.append({"name": f["name"], "value": v,
                        "en": f["en"], "ar": f["ar"], "tr": f["tr"]})
    return clauses, params, applied


def _where(spec, args, window):
    f, t = window
    clauses = list(spec["base_where"])
    params = []
    fc, fp, applied = _filter_clauses(spec, args)
    clauses += fc
    params += fp
    if spec["date_col"]:
        dc, dp = _date_clauses(spec["date_col"], f, t)
        clauses += dc
        params += dp
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params, applied


def prior_window(f, t):
    """The period immediately before [f, t], same length. None when there is no
    window — we never invent a comparison."""
    if not f or not t:
        return None, None
    length = (t - f).days + 1
    return f - timedelta(days=length), f - timedelta(days=1)


# ---------------------------------------------------------------------------
# SQL composition
# ---------------------------------------------------------------------------
def _rows_sql(spec, where, sort, direction, cap):
    order = spec["order"] or ""
    if sort:
        order = f"{sort} {direction}"
    sql = f"SELECT {spec['select']} FROM {spec['frm']} {where}"
    if spec["group"]:
        sql += f" GROUP BY {spec['group']}"
    if order:
        sql += f" ORDER BY {order}"
    return sql + f" LIMIT {int(cap)}"


def _agg_sql(spec, where):
    """ONE query for row count + every KPI + every column total."""
    parts = ["COUNT(*) AS _n"]
    for k in spec["kpis"]:
        parts.append(f"{k['expr']} AS {k['key']}")
    for c in spec["columns"]:
        if c.get("total"):
            parts.append(f"{c['total']} AS _t_{c['key']}")
    sel = ", ".join(parts)
    if spec["group"]:
        inner = (f"SELECT {spec['select']} FROM {spec['frm']} {where} "
                 f"GROUP BY {spec['group']}")
        return f"SELECT {sel} FROM ({inner}) t"
    return f"SELECT {sel} FROM {spec['frm']} {where}"


def _chart_sql(spec, ch, where):
    order = "1 ASC" if ch["kind"] == "trend" else "2 DESC"
    return (f"SELECT {ch['label_expr']} AS label, {ch['value_expr']} AS value "
            f"FROM {spec['frm']} {where} GROUP BY {ch['group_by']} "
            f"ORDER BY {order} LIMIT {int(ch['limit'])}")


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _num(v):
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def qs_with(args, **over):
    """Current query string with some params replaced/removed (None removes).
    Keeps a filtered report a shareable URL."""
    from urllib.parse import urlencode
    d = {}
    for k in args.keys():
        v = args.get(k)
        if v not in (None, ""):
            d[k] = v
    for k, v in over.items():
        if v in (None, ""):
            d.pop(k, None)
        else:
            d[k] = v
    return urlencode(sorted(d.items()))


def run(spec, args, cap=None, today=None):
    """Execute a report. Returns a dict the template and the exporters both use.

    args — a Werkzeug MultiDict or a plain dict (anything with .get and .keys).
    Never raises on bad user input: it returns result["error"], a translatable
    key ("inverted_range" | "bad_date"), and no rows.
    """
    cap = int(cap or spec["row_cap"])
    t = today or _today()
    preset = (args.get("period") or "").strip().lower()
    if not preset:
        preset = "custom" if ((args.get("from") or "").strip()
                              or (args.get("to") or "").strip()) else "all"
    if preset not in PRESETS:
        preset = "all"
    if preset == "all" or not spec["date_col"]:
        f, to, err = None, None, None
        if preset != "all" and not spec["date_col"]:
            preset = "all"
    else:
        f, to, err = _preset_window(preset, args, t)

    sort = (args.get("sort") or "").strip()
    direction = "DESC" if (args.get("dir") or "").strip().lower() == "desc" else "ASC"
    if sort not in {c["key"] for c in spec["columns"]}:
        sort = ""

    out = {
        "spec": spec, "rows": [], "columns": [], "kpis": [], "chart": None,
        "totals": {}, "totals_display": {}, "total": 0, "cap": cap, "capped": False,
        "period": {"preset": preset,
                   "from": f.isoformat() if f else "",
                   "to": to.isoformat() if to else "",
                   "prev_from": "", "prev_to": ""},
        "applied": [], "error": err, "queries": 0,
        "sort": sort, "dir": direction.lower(),
        "qs": qs_with(args),
    }
    out["columns"] = _columns_view(spec, args, sort, direction)
    if err:
        return out

    where, params, applied = _where(spec, args, (f, to))
    out["applied"] = applied

    conn = get_db()
    try:
        agg = conn.execute(_agg_sql(spec, where), tuple(params)).fetchone()
        out["queries"] += 1
        out["total"] = int(agg["_n"] or 0) if agg is not None else 0

        prev = None
        pf, pt = prior_window(f, to)
        if pf and pt:
            out["period"]["prev_from"] = pf.isoformat()
            out["period"]["prev_to"] = pt.isoformat()
            pwhere, pparams, _ = _where(spec, args, (pf, pt))
            prev = conn.execute(_agg_sql(spec, pwhere), tuple(pparams)).fetchone()
            out["queries"] += 1
            if prev is not None and int(prev["_n"] or 0) == 0:
                prev = None          # no prior data -> "no prior period", not 0%

        out["kpis"] = _kpis_view(spec, agg, prev)
        out["totals"] = {c["key"]: (agg[f"_t_{c['key']}"] if agg is not None else None)
                         for c in spec["columns"] if c.get("total")}
        out["totals_display"] = {k: _fmt(v) for k, v in out["totals"].items()}

        rows = conn.execute(_rows_sql(spec, where, sort, direction, cap),
                            tuple(params)).fetchall()
        out["queries"] += 1
        out["rows"] = [_row_dict(r, spec) for r in rows]
        out["capped"] = out["total"] > len(out["rows"])

        if spec["chart"] and out["total"]:
            crows = conn.execute(_chart_sql(spec, spec["chart"], where),
                                 tuple(params)).fetchall()
            out["queries"] += 1
            out["chart"] = _chart_view(spec["chart"], crows)
    finally:
        conn.close()
    return out


def _row_dict(r, spec):
    d = {}
    for c in spec["columns"]:
        try:
            v = r[c["key"]]
        except Exception:            # noqa: BLE001 — column absent from the SELECT
            v = None
        d[c["key"]] = "" if v is None else v
    return d


def _columns_view(spec, args, sort, direction):
    """Columns plus their sort link. Sorting is server-side: with a row cap, a
    client-side sort would sort the visible page and lie about the rest."""
    out = []
    for c in spec["columns"]:
        active = (sort == c["key"])
        nxt = "desc" if (active and direction == "ASC") else "asc"
        out.append({**c,
                    "sorted": (direction.lower() if active else ""),
                    "sort_qs": qs_with(args, sort=c["key"], dir=nxt)})
    return out


def _kpis_view(spec, agg, prev):
    out = []
    for k in spec["kpis"]:
        cur = _num(agg[k["key"]]) if agg is not None else None
        pv = _num(prev[k["key"]]) if prev is not None else None
        delta = None
        if pv is not None and pv != 0 and cur is not None:
            delta = (cur - pv) / abs(pv) * 100.0
        out.append({**k,
                    "value": 0 if cur is None else cur,
                    "display": _fmt(0 if cur is None else cur),
                    "prev": pv,
                    "delta": None if delta is None else round(delta, 1),
                    "has_prior": pv is not None})
    return out


def _chart_view(ch, rows):
    labels = [("" if r["label"] is None else str(r["label"])) for r in rows]
    values = [(_num(r["value"]) or 0) for r in rows]
    view = {"kind": ch["kind"], "labels": labels, "values": values,
            "en": ch["en"], "ar": ch["ar"], "tr": ch["tr"], "cumulative": None}
    if ch["kind"] == "pareto":
        total = sum(values)
        run_ = 0.0
        cum = []
        for v in values:                      # <= CHART_LIMIT points, not the dataset
            run_ += v
            cum.append(round(run_ / total * 100.0, 1) if total else 0)
        view["cumulative"] = cum
    return view


# ---------------------------------------------------------------------------
# Filter description — what the reader must see on a printed report
# ---------------------------------------------------------------------------
def describe(result, lang="en"):
    """One-line, human-readable description of the filters in force."""
    p = result["period"]
    bits = []
    if p["from"] or p["to"]:
        bits.append(f"{p['from'] or '…'} → {p['to'] or '…'}")
    else:
        bits.append("All dates")
    for a in result["applied"]:
        bits.append(f"{loc(a, lang)}: {a['value']}")
    return " · ".join(bits)


# ---------------------------------------------------------------------------
# Export — exactly what is on screen, filters applied
# ---------------------------------------------------------------------------
def export(spec, result, fmt, lang="en"):
    """Return (payload_bytes, mimetype, filename).

    csv/xlsx are produced by app.services.reports.export() (utf-8-sig BOM, Excel
    header styling, formula-injection guard). PDF is ours because it must carry
    the title, the filter description, the KPIs and a generated-at stamp.

    PDF text is English: reportlab ships no Arabic-shaping font in this build and
    a missing glyph prints as a black box, which is worse than English.
    """
    headers = [loc(c, lang) for c in spec["columns"]]
    keys = [c["key"] for c in spec["columns"]]
    rows = result["rows"]
    stem = f"{spec['module']}-{spec['key']}"
    if result["capped"]:
        # An export that silently stops at EXPORT_CAP is a decision made on
        # partial data without the reader knowing. Say so in the FILENAME —
        # never as a marker row inside the data, which would corrupt the sheet
        # for whatever consumes it.
        stem += f"-FIRST-{len(rows)}-OF-{result['total']}"

    if fmt in ("csv", "xlsx"):
        shim = {"label": loc(spec, lang, "title"),
                "columns": list(zip(keys, headers))}
        payload, mime, ext = _reports.export(shim, rows, fmt)
        return payload, mime, f"{stem}.{ext}"

    if fmt != "pdf":
        raise ValueError("bad format")

    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                     TableStyle)

    title = spec["title"]                      # English — see docstring
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.2 * cm, bottomMargin=1.2 * cm,
                            title=f"TC Platform — {title}")
    st = getSampleStyleSheet()
    story = [Paragraph(f"<b>{_esc(title)}</b>", st["Title"])]
    if spec["desc"]:
        story.append(Paragraph(_esc(spec["desc"]), st["Normal"]))
    story.append(Paragraph(f"<b>Filters:</b> {_esc(describe(result, 'en'))}", st["Normal"]))
    count = (f"{len(rows)} of {result['total']} rows (truncated)"
             if result["capped"] else f"{result['total']} rows")
    story.append(Paragraph(
        f"TC Platform · {spec['module_label']} · {count} · "
        f"generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC", st["Normal"]))
    story.append(Spacer(1, 0.35 * cm))

    if result["kpis"]:
        kdata = [[k["en"] for k in result["kpis"]],
                 [_fmt(k["value"]) for k in result["kpis"]],
                 [_delta_text(k) for k in result["kpis"]]]
        kt = Table(kdata)
        kt.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#5A6577")),
            ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 1), (-1, 1), 13),
            ("TEXTCOLOR", (0, 2), (-1, 2), colors.HexColor("#5A6577")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story += [kt, Spacer(1, 0.35 * cm)]

    if not rows:
        story.append(Paragraph("No records match these filters.", st["Normal"]))
    else:
        def trim(v):
            s = "" if v is None else str(v)
            return s if len(s) <= 42 else s[:40] + "…"
        data = [headers] + [[trim(r.get(k)) for k in keys] for r in rows]
        if result["totals"]:
            data.append([("TOTAL" if i == 0 else _fmt(result["totals"].get(k)))
                         for i, k in enumerate(keys)])
        tbl = Table(data, repeatRows=1)
        style = [
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B1222")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D9DEE7")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F6FA")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]
        if result["totals"]:
            style.append(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"))
        tbl.setStyle(TableStyle(style))
        story.append(tbl)
    doc.build(story)
    return buf.getvalue(), "application/pdf", f"{stem}.pdf"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _fmt(v):
    if v is None or v == "":
        return ""
    n = _num(v)
    if n is None:
        return str(v)
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.2f}"


def _delta_text(k):
    if not k["has_prior"]:
        return "no prior period"
    if k["delta"] is None:
        return "prior: " + _fmt(k["prev"])
    return f"{k['delta']:+.1f}% vs prior"


# ---------------------------------------------------------------------------
# Saved views — per user, per report
# ---------------------------------------------------------------------------
# Created lazily on first use so the engine adds ZERO statements to boot (see the
# --preload constraint). create_and_seed() is here for the orchestrator if it
# would rather pay the one CREATE TABLE IF NOT EXISTS at start-up.
_VIEWS_READY = False

_VIEWS_DDL = """CREATE TABLE IF NOT EXISTS report_views (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  report_key TEXT NOT NULL,
  name TEXT NOT NULL,
  qs TEXT,
  created_at TEXT
)"""


def create_and_seed(conn):
    """Idempotent DDL. One statement, no seed. Safe to call at boot or never."""
    global _VIEWS_READY
    try:
        conn.execute(_VIEWS_DDL)
        conn.commit()
        _VIEWS_READY = True
    except Exception:                                    # noqa: BLE001
        conn.rollback()          # PostgreSQL aborts the transaction otherwise


def _ensure_views(conn):
    global _VIEWS_READY
    if not _VIEWS_READY:
        create_and_seed(conn)


def saved_views(user_id, report_key):
    if not user_id:
        return []
    conn = get_db()
    try:
        _ensure_views(conn)
        rows = conn.execute(
            "SELECT id, name, qs FROM report_views "
            "WHERE user_id = ? AND report_key = ? ORDER BY name",
            (user_id, report_key)).fetchall()
        return [{"id": r["id"], "name": r["name"], "qs": r["qs"] or ""} for r in rows]
    except Exception:                                    # noqa: BLE001
        conn.rollback()
        return []
    finally:
        conn.close()


def save_view(user_id, report_key, name, qs):
    """Save (or overwrite by name) a filter combination. Returns True on success."""
    name = (name or "").strip()[:60]
    if not user_id or not name:
        return False
    conn = get_db()
    try:
        _ensure_views(conn)
        conn.execute("DELETE FROM report_views WHERE user_id = ? AND report_key = ? "
                     "AND name = ?", (user_id, report_key, name))
        conn.execute("INSERT INTO report_views (user_id, report_key, name, qs, created_at) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (user_id, report_key, name, (qs or "")[:2000],
                      datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    except Exception:                                    # noqa: BLE001
        conn.rollback()
        return False
    finally:
        conn.close()


def delete_view(user_id, view_id):
    """Delete one of the CALLER'S OWN views. user_id is in the WHERE clause, so a
    guessed id belonging to someone else deletes nothing."""
    if not user_id:
        return False
    conn = get_db()
    try:
        _ensure_views(conn)
        conn.execute("DELETE FROM report_views WHERE id = ? AND user_id = ?",
                     (view_id, user_id))
        conn.commit()
        return True
    except Exception:                                    # noqa: BLE001
        conn.rollback()
        return False
    finally:
        conn.close()
