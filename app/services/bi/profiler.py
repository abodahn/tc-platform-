"""
Profile a table: infer each column's type and compute statistics, then grade
data quality. Pure standard library (statistics module) — no pandas/numpy.

Column types: number | date | boolean | category | text.

The value parsers (parse_number / parse_date) are the single source of truth
for coercion and are reused by the aggregation engine, so a column typed here as
"number" aggregates the same way everywhere.
"""
from __future__ import annotations

import re
import statistics
from datetime import date, datetime

from app.tabular import to_number

_TRUE = {"true", "yes", "y", "1", "on"}
_FALSE = {"false", "no", "n", "0", "off"}
_BOOLS = _TRUE | _FALSE

_DATE_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d %b %Y", "%b %d, %Y",
    "%d %B %Y", "%m/%d/%y", "%d/%m/%y",
]
# Symbols peeled off before the digits are parsed. The COMMA is deliberately no
# longer here: on the Turkish/European exports app/tabular.py now accepts it is
# the DECIMAL point, and stripping it read '12,50' as 1250 — a hundred times too
# high, silently, with the column still typed "number", so every KPI tile, chart,
# insight, threshold alert and xlsx/PDF export carried the wrong figure. Which of
# '.'/',' is the decimal separator is app.tabular.to_number's job, not ours.
_CURRENCY = "$€£₺¥%  "


# What is allowed to reach to_number at all: digits, separators, an optional sign
# and an optional exponent. to_number strips every OTHER character as noise, so
# without this gate '2026-03-01' would parse as 20260301, 'M-1' as 1 and
# 'Line 3' as 3 — and _infer_type would type dates, IDs and category labels as
# "number". The gate keeps parse_number exactly as strict as it has always been.
_NUMERIC = re.compile(r"^[+-]?[\d.,]*\d[\d.,]*(?:[eE][+-]?\d+)?$")


def parse_number(v):
    """Float, or None if the value is not a number. None-contract unchanged.

    Symbol handling lives here; the digits are converted by app.tabular.to_number
    so a number reads the same in BI as it does on every other import screen.
    """
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if not s:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):  # accounting negatives (1,234)
        neg, s = True, s[1:-1].strip()
    for ch in _CURRENCY:
        s = s.replace(ch, "")
    s = s.strip()
    if not _NUMERIC.match(s):
        return None
    # Two commas cannot both be a decimal point, so '1,234,567' is unambiguously
    # US thousands grouping. to_number returns None for it (see `escalate`), and
    # a revenue column in millions would otherwise drop out of "number" typing
    # entirely, taking the whole dashboard with it.
    if s.count(",") > 1 and "." not in s:
        s = s.replace(",", "")
    f = to_number(s)
    if f is None:
        return None
    return -f if neg else f


def parse_date(v):
    """Return an ISO date string (YYYY-MM-DD) or None. Bare integers are NOT
    treated as dates (they're numbers/years)."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (int, float)):
        return None
    s = str(v).strip()
    if not s or s.isdigit():
        return None
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _column_values(rows, idx):
    return [r[idx] if idx < len(r) else None for r in rows]


def _fraction(vals, fn):
    non_null = [v for v in vals if v is not None and str(v).strip() != ""]
    if not non_null:
        return 0.0, 0
    ok = sum(1 for v in non_null if fn(v) is not None)
    return ok / len(non_null), len(non_null)


def _infer_type(vals):
    present = [v for v in vals if v is not None and str(v).strip() != ""]
    if not present:
        return "text"
    lowered = [str(v).strip().lower() for v in present]
    if all(x in _BOOLS for x in lowered):
        return "boolean"
    num_frac, _ = _fraction(vals, parse_number)
    if num_frac >= 0.85:
        return "number"
    date_frac, _ = _fraction(vals, parse_date)
    if date_frac >= 0.7:
        return "date"
    n_unique = len(set(lowered))
    n = len(present)
    if n_unique <= max(20, int(n * 0.05)) and n_unique / n < 0.6:
        return "category"
    return "text"


def _profile_column(name, idx, vals):
    ctype = _infer_type(vals)
    present = [v for v in vals if v is not None and str(v).strip() != ""]
    n_missing = len(vals) - len(present)
    col = {
        "name": name, "index": idx, "type": ctype,
        "n_missing": n_missing,
        "n_unique": len(set(str(v).strip().lower() for v in present)),
        "samples": [str(v) for v in present[:3]],
    }
    if ctype == "number":
        nums = [parse_number(v) for v in present]
        nums = [x for x in nums if x is not None]
        if nums:
            col["min"] = min(nums)
            col["max"] = max(nums)
            col["sum"] = sum(nums)
            col["mean"] = statistics.fmean(nums)
            col["median"] = statistics.median(nums)
            col["stdev"] = statistics.pstdev(nums) if len(nums) > 1 else 0.0
    elif ctype == "date":
        dates = [parse_date(v) for v in present]
        dates = sorted(d for d in dates if d)
        if dates:
            col["min_date"] = dates[0]
            col["max_date"] = dates[-1]
    elif ctype in ("category", "boolean"):
        counts = {}
        for v in present:
            k = str(v).strip()
            counts[k] = counts.get(k, 0) + 1
        col["top"] = sorted(counts.items(), key=lambda kv: -kv[1])[:12]
    return col


def profile(columns, rows):
    """Return {'columns': [colprofile,...], 'n_rows': int}."""
    cols = []
    for idx, name in enumerate(columns):
        cols.append(_profile_column(name, idx, _column_values(rows, idx)))
    return {"columns": cols, "n_rows": len(rows)}


def data_quality(prof, rows):
    """Return {'score': 0-100, 'issues': [{column, kind, detail, severity}]}."""
    issues = []
    n = prof["n_rows"] or 1
    penalty = 0
    for c in prof["columns"]:
        miss = c["n_missing"]
        if miss:
            pct = round(100 * miss / n)
            sev = "high" if pct >= 30 else ("medium" if pct >= 10 else "low")
            if pct >= 3:
                issues.append({"column": c["name"], "kind": "missing",
                               "detail": f"{pct}% missing ({miss})", "severity": sev})
                penalty += min(15, pct // 4)
        if c["type"] != "number" and c["n_unique"] <= 1 and (n - miss) > 0:
            issues.append({"column": c["name"], "kind": "constant",
                           "detail": "only one distinct value", "severity": "low"})
            penalty += 3
    # full-row duplicates
    seen, dup = set(), 0
    for r in rows:
        key = tuple("" if v is None else str(v) for v in r)
        if key in seen:
            dup += 1
        else:
            seen.add(key)
    if dup:
        pct = round(100 * dup / n)
        issues.append({"column": "(rows)", "kind": "duplicate",
                       "detail": f"{dup} duplicate rows ({pct}%)",
                       "severity": "medium" if pct >= 5 else "low"})
        penalty += min(15, pct)
    score = max(0, 100 - penalty)
    issues.sort(key=lambda i: {"high": 0, "medium": 1, "low": 2}[i["severity"]])
    return {"score": score, "issues": issues[:12]}


# convenience accessors ------------------------------------------------------
def columns_by_type(prof):
    out = {"number": [], "date": [], "category": [], "boolean": [], "text": []}
    for c in prof["columns"]:
        out.setdefault(c["type"], []).append(c)
    return out


def find_column(prof, name):
    if name is None:
        return None
    key = str(name).strip().lower()
    for c in prof["columns"]:
        if c["name"].strip().lower() == key:
            return c
    return None
