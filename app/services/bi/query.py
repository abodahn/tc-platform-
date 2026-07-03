"""
Smart-search bar: parse a short natural phrase into a chart spec — no LLM.

Handles the common shapes people actually type:
  "top 10 customers by revenue"      -> bar, dim=customers, measure=revenue
  "revenue by region"                -> bar, measure=revenue, dim=region
  "sales over time" / "sales by month"-> line on the date column
  "average score by team"            -> bar, agg=avg
  "distribution of price"            -> histogram
  "share by category"                -> doughnut
  "height vs weight"                 -> scatter
Column names are matched fuzzily; types disambiguate measure vs dimension, so
word order doesn't matter.
"""
from __future__ import annotations

import re

from .profiler import columns_by_type

_AGG_WORDS = {
    "sum": "sum", "total": "sum", "avg": "avg", "average": "avg", "mean": "avg",
    "count": "count", "number": "count", "min": "min", "minimum": "min",
    "lowest": "min", "max": "max", "maximum": "max", "highest": "max",
}
_TIME_WORDS = ("over time", "by month", "by day", "by date", "trend", "timeline",
               "by year", "over the", "monthly", "daily")


def _match_column(prof, phrase, prefer=None):
    """Best column whose name overlaps `phrase`. `prefer` filters to a type list."""
    phrase = (phrase or "").strip().lower()
    if not phrase:
        return None
    best, best_score = None, 0
    for c in prof["columns"]:
        if prefer and c["type"] not in prefer:
            continue
        name = c["name"].strip().lower()
        if not name:
            continue
        score = 0
        if name == phrase:
            score = 100 + len(name)
        elif name in phrase:
            score = 50 + len(name)
        elif phrase in name:
            score = 30 + len(phrase)
        else:
            ntok = set(name.split())
            ptok = set(phrase.split())
            common = ntok & ptok
            if common:
                score = 10 * len(common)
        if score > best_score:
            best, best_score = c, score
    return best if best_score > 0 else None


def parse(prof, text):
    """Return a chart spec dict, or None if nothing recognisable was found."""
    if not text or not text.strip():
        return None
    q = text.strip().lower()
    by = columns_by_type(prof)
    nums = by["number"]
    dates = by["date"]

    # explicit aggregate word
    agg = None
    for w, a in _AGG_WORDS.items():
        if re.search(rf"\b{re.escape(w)}\b", q):
            agg = a
            break

    # top N
    limit = 12
    m = re.search(r"\btop\s+(\d{1,3})\b", q)
    is_top = bool(m)
    if m:
        limit = max(1, min(100, int(m.group(1))))
        q = re.sub(r"\btop\s+\d{1,3}\b", " ", q)

    # scatter: "x vs y"
    if " vs " in q or " versus " in q:
        parts = re.split(r"\s+(?:vs|versus)\s+", q, maxsplit=1)
        x = _match_column(prof, parts[0], prefer=["number"])
        y = _match_column(prof, parts[1], prefer=["number"]) if len(parts) > 1 else None
        if x and y and x["name"] != y["name"]:
            return {"type": "scatter", "title": f"{x['name']} vs {y['name']}",
                    "x": x["name"], "y": y["name"]}

    # distribution / histogram
    if "distribution" in q or "histogram" in q or "spread of" in q:
        col = _match_column(prof, q, prefer=["number"])
        if col:
            return {"type": "histogram", "title": f"Distribution of {col['name']}", "dim": col["name"]}

    # over-time trend
    wants_time = any(w in q for w in _TIME_WORDS)
    measure = None
    dim = None

    if " by " in q:
        left, right = q.split(" by ", 1)
        a = _match_column(prof, left)
        b = _match_column(prof, right)
        # assign by type: numeric -> measure, other -> dim
        for col in (a, b):
            if col is None:
                continue
            if col["type"] == "number" and measure is None:
                measure = col
            elif dim is None:
                dim = col
        if is_top:  # "top N <dim> by <measure>": the ranked thing (left) is the dim
            dim = _match_column(prof, left, prefer=["category", "boolean", "text", "date"]) or dim
            measure = _match_column(prof, right, prefer=["number"]) or measure
    else:
        # single phrase: try measure, else a dimension to count by
        measure = _match_column(prof, q, prefer=["number"])
        dim = _match_column(prof, q, prefer=["category", "boolean", "date"])

    if wants_time and dates:
        dim = dates[0]
        if measure is None:
            measure = nums[0] if nums else None
        return {"type": "line",
                "title": f"{(agg or 'sum').capitalize()} of {measure['name'] if measure else 'records'} over time",
                "dim": dim["name"], "measure": measure["name"] if measure else None,
                "agg": agg or "sum"}

    if dim is not None:
        kind = "doughnut" if ("share" in q or "breakdown" in q or "proportion" in q) else "bar"
        a = agg or ("sum" if measure else "count")
        title = (f"{a.capitalize()} of {measure['name']} by {dim['name']}"
                 if measure else f"Records by {dim['name']}")
        return {"type": kind, "title": title, "dim": dim["name"],
                "measure": measure["name"] if measure else None, "agg": a, "limit": limit}

    if measure is not None:
        # no dimension -> a distribution of the measure is the useful default
        return {"type": "histogram", "title": f"Distribution of {measure['name']}", "dim": measure["name"]}

    return None
