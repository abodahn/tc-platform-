"""
TC Platform — reusable OFFLINE AI core (stdlib only, no internet, no deps).

Generic statistical/heuristic building blocks shared across modules:
  * anomaly detection (z-score, IQR)
  * linear-trend forecast + days-to-threshold
  * lightweight keyword text classifier
  * SLA-risk scoring

These are transparent, explainable models that run fully on-premise.
"""
from statistics import mean, pstdev


def zscore_anomalies(values, threshold=2.0):
    """Return list of (index, value, z) for points beyond `threshold` sigma."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return []
    mu = mean(vals)
    sd = pstdev(vals)
    if sd == 0:
        return []
    out = []
    for i, v in enumerate(values):
        if v is None:
            continue
        z = (v - mu) / sd
        if abs(z) >= threshold:
            out.append((i, v, round(z, 2)))
    return out


def iqr_outliers(values, k=1.5):
    vals = sorted(v for v in values if v is not None)
    n = len(vals)
    if n < 4:
        return []
    q1 = vals[n // 4]
    q3 = vals[(3 * n) // 4]
    iqr = q3 - q1
    lo, hi = q1 - k * iqr, q3 + k * iqr
    return [v for v in values if v is not None and (v < lo or v > hi)]


def linear_trend(points):
    """Least-squares slope/intercept for y over x=0..n-1. Returns (slope, intercept)."""
    ys = [p for p in points if p is not None]
    n = len(ys)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    xs = list(range(n))
    mx, my = mean(xs), mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0, my
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return slope, my - slope * mx


def forecast_next(points, ahead=1):
    slope, intercept = linear_trend(points)
    n = len([p for p in points if p is not None])
    return slope * (n - 1 + ahead) + intercept


def steps_to_threshold(current, slope, threshold):
    """How many steps until `current` reaches `threshold` given per-step `slope`.
    None if not trending toward it."""
    if slope == 0:
        return None
    steps = (threshold - current) / slope
    return int(steps) if steps > 0 else 0


def classify_text(text, categories):
    """categories: {label: [keywords]}. Returns (label, score) by keyword hits."""
    t = (text or "").lower()
    best, best_score = None, 0
    for label, kws in categories.items():
        score = sum(1 for kw in kws if kw in t)
        if score > best_score:
            best, best_score = label, score
    return best, best_score


def sla_risk(elapsed_hours, target_hours):
    """0-100 risk of breaching an SLA given elapsed vs target time."""
    if not target_hours or target_hours <= 0:
        return 0
    ratio = elapsed_hours / target_hours
    return max(0, min(100, int(round(ratio * 100))))
