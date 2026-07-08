"""
AI engine — shared building blocks.

A "finding" is the common currency every predictor returns: an explainable,
scored observation with a recommended action. The orchestrator turns findings
into rows in ai_alerts / ai_predictions / ai_risk_scores.
"""
from datetime import datetime

# Reuse the platform's offline statistical core (no external deps).
from app.ai_core import (zscore_anomalies, iqr_outliers, linear_trend,  # noqa: F401
                         forecast_next, steps_to_threshold)


def clamp(v, lo=0.0, hi=100.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    return max(lo, min(hi, v))


# 0–20 Low · 21–40 Watch · 41–60 Medium · 61–80 High · 81–100 Critical
def level_of(score):
    s = clamp(score)
    if s <= 20:
        return "Low"
    if s <= 40:
        return "Watch"
    if s <= 60:
        return "Medium"
    if s <= 80:
        return "High"
    return "Critical"


def severity_of(score):
    """Map a 0–100 risk score to the platform's alert severity vocabulary."""
    s = clamp(score)
    if s >= 61:
        return "critical"
    if s >= 41:
        return "warning"
    return "info"


def days_until(date_str):
    if not date_str:
        return None
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
        return (d - datetime.utcnow()).days
    except Exception:  # noqa: BLE001
        return None


def days_since(date_str):
    du = days_until(date_str)
    return None if du is None else -du


def finding(domain, title, score, **kw):
    """Build a normalized finding dict. `explanation` should say WHY the score
    was given; `recommendation` WHAT to do; `impact` what happens if ignored."""
    score = round(clamp(score), 1)
    return {
        "domain": domain,
        "title": str(title)[:200],
        "risk_score": score,
        "severity": severity_of(score),
        "level": level_of(score),
        "entity_type": kw.get("entity_type", ""),
        "entity_ref": str(kw.get("entity_ref", ""))[:120],
        "impact": kw.get("impact", ""),
        "recommendation": kw.get("recommendation", ""),
        "responsible": kw.get("responsible", ""),
        "link": kw.get("link", ""),
        "explanation": kw.get("explanation", ""),
        "kind": kw.get("kind", "risk"),
        "value": kw.get("value"),
        "horizon": kw.get("horizon", ""),
        "confidence": kw.get("confidence", "medium"),
    }


def safe_rows(conn, sql, params=()):
    """Run a query, returning [] on any error (missing table/column)."""
    try:
        return conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001
        return []


def safe_scalar(conn, sql, params=(), default=0):
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return default
        try:
            return row[0]
        except Exception:  # noqa: BLE001
            return row["c"]
    except Exception:  # noqa: BLE001
        return default
