"""
Aggregation engine: turn a chart spec + active cross-filters into Chart.js-ready
series. Also provides outlier detection (IQR) and a simple linear forecast.

All pure standard library. The route layer calls chart_data() per chart.
"""
from __future__ import annotations

import statistics

from .profiler import find_column, parse_date, parse_number

_AGGS = ("sum", "avg", "count", "min", "max")


# --- filtering --------------------------------------------------------------
def apply_filters(columns, rows, filters):
    """filters: list of {column, op, value}. op in =,!=,>,<,>=,<=,contains.
    A cross-filter (click a bar) is just {column, op:'=', value}."""
    if not filters:
        return rows
    idx = {name: i for i, name in enumerate(columns)}
    out = rows
    for f in filters:
        col = f.get("column")
        if col not in idx:
            continue
        i = idx[col]
        op = f.get("op", "=")
        val = f.get("value")
        out = [r for r in out if _match(r[i] if i < len(r) else None, op, val)]
    return out


def _match(cell, op, val):
    if op in (">", "<", ">=", "<="):
        a, b = parse_number(cell), parse_number(val)
        if a is None or b is None:
            return False
        return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]
    s = "" if cell is None else str(cell).strip()
    v = "" if val is None else str(val).strip()
    if op == "contains":
        return v.lower() in s.lower()
    if op == "!=":
        return s.lower() != v.lower()
    return s.lower() == v.lower()  # '='


# --- aggregation primitives -------------------------------------------------
def _agg(nums, how):
    nums = [n for n in nums if n is not None]
    if how == "count":
        return float(len(nums))
    if not nums:
        return 0.0
    if how == "sum":
        return sum(nums)
    if how == "avg":
        return statistics.fmean(nums)
    if how == "min":
        return min(nums)
    if how == "max":
        return max(nums)
    if how == "last":
        return nums[-1]
    return sum(nums)


def kpi_value(columns, rows, column, how="sum"):
    if how == "count" or not column:
        return float(len(rows))
    idx = {name: i for i, name in enumerate(columns)}
    if column not in idx:
        return 0.0
    i = idx[column]
    nums = [parse_number(r[i] if i < len(r) else None) for r in rows]
    return _agg(nums, how)


def _date_granularity(dates):
    ds = sorted(d for d in dates if d)
    if len(ds) < 2:
        return "day"
    span_days = (_ord(ds[-1]) - _ord(ds[0]))
    distinct = len(set(ds))
    if span_days > 720 or distinct > 60:
        return "month"
    return "day"


def _ord(iso):
    from datetime import date
    y, m, d = (int(x) for x in iso.split("-")[:3])
    return date(y, m, d).toordinal()


def _bucket(iso, gran):
    return iso[:7] if gran == "month" else iso


# --- the main dispatcher ----------------------------------------------------
def chart_data(prof, columns, rows, spec, filters=None):
    """Return Chart.js-ready data for one chart spec against (optionally
    filtered) rows: {kind, labels, datasets, points, forecast, anomalies}."""
    rows = apply_filters(columns, rows, filters or [])
    kind = spec.get("type", "bar")
    if kind == "scatter":
        return _scatter(columns, rows, spec)
    if kind == "histogram":
        return _histogram(prof, columns, rows, spec)
    return _grouped(prof, columns, rows, spec, kind)


def _grouped(prof, columns, rows, spec, kind):
    idx = {name: i for i, name in enumerate(columns)}
    dim = spec.get("dim")
    measure = spec.get("measure")
    how = spec.get("agg", "sum")
    if how not in _AGGS + ("last",):
        how = "sum"
    limit = int(spec.get("limit", 12) or 12)
    if dim not in idx:
        return {"kind": kind, "labels": [], "datasets": [], "empty": True}
    di = idx[dim]
    dcol = find_column(prof, dim)
    is_date = bool(dcol) and dcol["type"] == "date"

    groups = {}   # label -> list of measure numbers (or [1] per row for count)
    mi = idx.get(measure) if measure else None
    if is_date:
        dates = [parse_date(r[di] if di < len(r) else None) for r in rows]
        gran = _date_granularity([d for d in dates if d])
    for r in rows:
        raw = r[di] if di < len(r) else None
        if raw is None or str(raw).strip() == "":
            key = "(blank)"
        elif is_date:
            d = parse_date(raw)
            key = _bucket(d, gran) if d else "(blank)"
        else:
            key = str(raw).strip()
        # For a count aggregation every row counts, regardless of whether the
        # (optional) measure cell is null — so don't parse_number it away.
        mval = 1.0 if how == "count" else (parse_number(r[mi]) if (mi is not None) else 1.0)
        groups.setdefault(key, []).append(mval)

    items = [(k, _agg(v, how)) for k, v in groups.items()]
    if is_date:
        items.sort(key=lambda kv: kv[0])
    else:
        items.sort(key=lambda kv: kv[1], reverse=True)
        if len(items) > limit:
            head = items[:limit]
            other = sum(v for _, v in items[limit:])
            items = head + [("Other", other)]

    labels = [k for k, _ in items]
    data = [round(v, 3) for _, v in items]
    out = {"kind": kind, "labels": labels,
           "datasets": [{"label": _measure_label(measure, how), "data": data}],
           "dim": dim, "measure": measure, "agg": how, "is_date": is_date}

    # anomalies + forecast only make sense on ordered/line series
    if kind == "line":
        out["anomalies"] = outliers(data)
        if is_date and len(data) >= 4:
            fc = forecast(data)
            if fc:
                out["forecast"] = fc
    return out


def _measure_label(measure, how):
    if how == "count" or not measure:
        return "Count"
    return f"{how.capitalize()} of {measure}"


def _scatter(columns, rows, spec):
    idx = {name: i for i, name in enumerate(columns)}
    xc, yc = spec.get("x"), spec.get("y")
    if xc not in idx or yc not in idx:
        return {"kind": "scatter", "points": [], "empty": True}
    xi, yi = idx[xc], idx[yc]
    pts = []
    for r in rows:
        x = parse_number(r[xi] if xi < len(r) else None)
        y = parse_number(r[yi] if yi < len(r) else None)
        if x is not None and y is not None:
            pts.append({"x": round(x, 4), "y": round(y, 4)})
    return {"kind": "scatter", "points": pts[:2000], "x": xc, "y": yc}


def _histogram(prof, columns, rows, spec, bins=10):
    idx = {name: i for i, name in enumerate(columns)}
    col = spec.get("dim") or spec.get("measure")
    if col not in idx:
        return {"kind": "histogram", "labels": [], "datasets": [], "empty": True}
    i = idx[col]
    nums = [parse_number(r[i] if i < len(r) else None) for r in rows]
    nums = [x for x in nums if x is not None]
    if not nums:
        return {"kind": "histogram", "labels": [], "datasets": [], "empty": True}
    lo, hi = min(nums), max(nums)
    if lo == hi:
        return {"kind": "bar", "labels": [str(round(lo, 2))],
                "datasets": [{"label": col, "data": [len(nums)]}]}
    width = (hi - lo) / bins
    counts = [0] * bins
    for x in nums:
        b = min(bins - 1, int((x - lo) / width))
        counts[b] += 1
    labels = [f"{round(lo + k * width, 1)}–{round(lo + (k + 1) * width, 1)}" for k in range(bins)]
    return {"kind": "bar", "labels": labels,
            "datasets": [{"label": f"{col} distribution", "data": counts}], "measure": col}


# --- statistics helpers -----------------------------------------------------
def outliers(data):
    """Return [{index, value}] flagged by the 1.5*IQR rule."""
    vals = [v for v in data if v is not None]
    if len(vals) < 6:
        return []
    s = sorted(vals)
    q1 = statistics.median(s[: len(s) // 2])
    q3 = statistics.median(s[(len(s) + 1) // 2:])
    iqr = q3 - q1
    if iqr <= 0:
        return []
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [{"index": i, "value": v} for i, v in enumerate(data)
            if v is not None and (v < lo or v > hi)]


def forecast(data, horizon=None):
    """Linear-regression projection of the next `horizon` points. Returns
    {method, points:[...]} or None."""
    ys = [float(v) for v in data if v is not None]
    n = len(ys)
    if n < 4:
        return None
    horizon = horizon or max(1, min(6, n // 4))
    xs = list(range(n))
    try:
        slope, intercept = statistics.linear_regression(xs, ys)
    except Exception:
        # manual least squares fallback
        mx = statistics.fmean(xs)
        my = statistics.fmean(ys)
        denom = sum((x - mx) ** 2 for x in xs)
        if denom == 0:
            return None
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
        intercept = my - slope * mx
    pts = [round(slope * (n - 1 + k) + intercept, 3) for k in range(1, horizon + 1)]
    return {"method": "linear", "points": pts, "horizon": horizon}


def correlations(prof, columns, rows, top=3):
    """Strongest linear relationships between numeric columns (|r| >= 0.5)."""
    idx = {name: i for i, name in enumerate(columns)}
    num_cols = [c["name"] for c in prof["columns"] if c["type"] == "number"]
    series = {}
    for name in num_cols:
        i = idx[name]
        series[name] = [parse_number(r[i] if i < len(r) else None) for r in rows]
    found = []
    for a in range(len(num_cols)):
        for b in range(a + 1, len(num_cols)):
            na, nb = num_cols[a], num_cols[b]
            pairs = [(x, y) for x, y in zip(series[na], series[nb])
                     if x is not None and y is not None]
            if len(pairs) < 5:
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            try:
                r = statistics.correlation(xs, ys)
            except Exception:
                continue
            if abs(r) >= 0.5:
                found.append({"a": na, "b": nb, "r": round(r, 2)})
    found.sort(key=lambda d: -abs(d["r"]))
    return found[:top]
