"""
Recommend a dashboard from a data profile: pick the KPIs and charts a human
analyst would reach for first, using column-type rules.

Rules of thumb
--------------
* KPIs: row count, plus the sum/avg of the most meaningful numeric columns.
* A date column + a numeric measure -> a trend line (with forecast + anomalies).
* A low-cardinality category + a measure -> a bar/pie of measure by category.
* The primary numeric column -> a distribution (histogram).
* Two correlated numerics -> a scatter.
Capped to a clean set so the first view isn't overwhelming.
"""
from __future__ import annotations

from . import analyze
from .profiler import columns_by_type

_SUM_HINTS = ("revenue", "sales", "amount", "cost", "price", "value", "total",
              "qty", "quantity", "spend", "budget", "profit", "book", "salary",
              "income", "expense", "count", "stock", "balance")
_AVG_HINTS = ("percent", "pct", "rate", "ratio", "score", "avg", "average",
              "utilization", "efficiency", "%", "age", "rating")
_ID_HINTS = ("id", "code", "number", "no", "ref", "key", "uuid", "guid")


def _looks_like_id(col, n_rows):
    name = col["name"].lower()
    idish = any(name == h or name.endswith("_" + h) or name.endswith(" " + h)
                or name.startswith(h + "_") for h in _ID_HINTS)
    unique = n_rows and col.get("n_unique", 0) >= 0.95 * n_rows
    return idish and unique


def _agg_for(col):
    name = col["name"].lower()
    if any(h in name for h in _AVG_HINTS):
        return "avg"
    return "sum"


def _fmt_for(col):
    name = col["name"].lower()
    if any(h in name for h in ("percent", "pct", "rate", "%", "ratio", "utilization", "efficiency")):
        return "percent"
    if any(h in name for h in ("cost", "price", "revenue", "amount", "value", "budget",
                               "salary", "income", "expense", "profit", "spend", "book", "balance")):
        return "currency"
    return "number"


def _measures(prof):
    n = prof["n_rows"]
    nums = [c for c in columns_by_type(prof)["number"] if not _looks_like_id(c, n)]
    # rank: prefer columns whose name hints at a real measure, then by spread
    def score(c):
        name = c["name"].lower()
        hinted = any(h in name for h in _SUM_HINTS + _AVG_HINTS)
        spread = abs(c.get("max", 0) - c.get("min", 0))
        return (1 if hinted else 0, spread)
    return sorted(nums, key=score, reverse=True)


def build_dashboard(prof, name="Auto dashboard"):
    by = columns_by_type(prof)
    n = prof["n_rows"]
    measures = _measures(prof)
    primary = measures[0] if measures else None
    dates = by["date"]
    cats = [c for c in by["category"] if 2 <= c.get("n_unique", 0) <= 30]
    cats += [c for c in by["boolean"] if c not in cats]

    kpis = [{"id": "k_rows", "label": "Total records", "column": None,
             "agg": "count", "format": "number", "value": float(n)}]
    for c in measures[:4]:
        agg = _agg_for(c)
        val = c.get("sum") if agg == "sum" else c.get("mean")
        kpis.append({"id": f"k_{c['index']}", "label": f"{agg.capitalize()} of {c['name']}",
                     "column": c["name"], "agg": agg, "format": _fmt_for(c),
                     "value": round(val, 3) if val is not None else 0})

    charts = []
    cid = 0

    def nxt():
        nonlocal cid
        cid += 1
        return f"c{cid}"

    # 1) trend over time
    if dates and primary:
        charts.append({"id": nxt(), "type": "line",
                       "title": f"{_agg_for(primary).capitalize()} of {primary['name']} over time",
                       "dim": dates[0]["name"], "measure": primary["name"],
                       "agg": _agg_for(primary)})

    # 2) measure by top categories (bar), + a pie for the first category share
    for i, c in enumerate(cats[:2]):
        if primary:
            charts.append({"id": nxt(), "type": "bar",
                           "title": f"{_agg_for(primary).capitalize()} of {primary['name']} by {c['name']}",
                           "dim": c["name"], "measure": primary["name"],
                           "agg": _agg_for(primary), "limit": 12})
        else:
            charts.append({"id": nxt(), "type": "bar",
                           "title": f"Records by {c['name']}",
                           "dim": c["name"], "measure": None, "agg": "count", "limit": 12})

    if cats:
        c = cats[0]
        charts.append({"id": nxt(), "type": "doughnut",
                       "title": f"Share by {c['name']}",
                       "dim": c["name"],
                       "measure": primary["name"] if primary else None,
                       "agg": _agg_for(primary) if primary else "count", "limit": 8})

    # 3) distribution of the primary measure
    if primary:
        charts.append({"id": nxt(), "type": "histogram",
                       "title": f"Distribution of {primary['name']}",
                       "dim": primary["name"]})

    # 4) scatter of the two strongest-correlated numerics (only if a clear pair)
    # (correlations need the data; the route can add this. Here we add a scatter
    #  of the top two measures as a sensible default when there's no date.)
    if not dates and len(measures) >= 2:
        charts.append({"id": nxt(), "type": "scatter",
                       "title": f"{measures[0]['name']} vs {measures[1]['name']}",
                       "x": measures[0]["name"], "y": measures[1]["name"]})

    return {"name": name, "kpis": kpis, "charts": charts[:6]}
