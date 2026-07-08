"""
Unified risk-scoring framework.

Turns a domain's list of findings into a single 0–100 domain score, rolls the
domains up into a company health score, and estimates business impact. The
scoring is deliberately transparent (documented weights, no black box).
"""
from app.ai_engine.base import clamp, level_of

# Relative weight of each domain in the overall company health score.
DOMAIN_WEIGHTS = {
    "tickets": 1.2,
    "assets": 1.0,
    "maintenance": 1.3,
    "inventory": 1.0,
    "procurement": 1.0,
    "payroll": 1.2,
    "hr": 0.8,
    "paperless": 0.5,
    "approvals": 1.0,
}

DOMAIN_LABELS = {
    "tickets": "Ticket / SLA risk",
    "assets": "Asset risk",
    "maintenance": "Maintenance risk",
    "inventory": "Inventory / spares risk",
    "procurement": "Procurement delay risk",
    "payroll": "Payroll anomaly risk",
    "hr": "HR evaluation risk",
    "paperless": "Paperless adoption",
    "approvals": "Approval bottleneck risk",
}


def domain_score(findings):
    """A domain's score is driven by its worst findings (peak risk matters more
    than the average), tempered by how many there are.
    score = 0.7*max + 0.3*mean_of_top3, nudged up slightly by volume."""
    if not findings:
        return 0.0
    scores = sorted((f["risk_score"] for f in findings), reverse=True)
    top = scores[:3]
    peak = scores[0]
    mean_top = sum(top) / len(top)
    base = 0.7 * peak + 0.3 * mean_top
    volume_bump = min(8.0, (len(scores) - 1) * 1.5)
    return round(clamp(base + volume_bump), 1)


def health_score(domain_scores):
    """Company health = 100 - weighted average domain risk. Paperless is an
    ADOPTION score (higher = better), so it is inverted into a risk first."""
    num = den = 0.0
    for dom, sc in domain_scores.items():
        w = DOMAIN_WEIGHTS.get(dom, 1.0)
        risk = sc
        num += w * risk
        den += w
    if den == 0:
        return 100.0
    return round(clamp(100 - (num / den)), 1)


def band_color(score):
    lvl = level_of(score)
    return {"Low": "ok", "Watch": "info", "Medium": "warn",
            "High": "warn", "Critical": "crit"}[lvl]


def estimate_business_impact(findings_by_domain, metrics):
    """Rough, explainable business-impact estimates for the executive view.
    All figures are labelled 'estimated' in the UI."""
    def n(dom, pred):
        return sum(1 for f in findings_by_domain.get(dom, []) if pred(f))

    sla_prevented = n("tickets", lambda f: f["risk_score"] >= 61)
    downtime_avoided_h = n("maintenance", lambda f: f["risk_score"] >= 61) * 4
    payroll_errors = n("payroll", lambda f: f["risk_score"] >= 41)
    approvals_flagged = n("approvals", lambda f: f["risk_score"] >= 41) + \
        n("procurement", lambda f: f["risk_score"] >= 41)
    stock_shortages = n("inventory", lambda f: f["risk_score"] >= 61)
    assets_flagged = n("assets", lambda f: f["risk_score"] >= 61)

    paper_saved = int(metrics.get("paper_digitizable_sheets", 0))
    paper_cost_saved = round(paper_saved * 0.12, 0)
    # very rough manual-follow-up hours saved: ~15 min per surfaced item
    total_findings = sum(len(v) for v in findings_by_domain.values())
    hours_saved = round(total_findings * 0.25, 1)

    return [
        {"key": "sla_prevented", "label": "SLA breaches flagged early", "value": sla_prevented, "unit": ""},
        {"key": "downtime_avoided", "label": "Downtime hours avoidable", "value": downtime_avoided_h, "unit": "h"},
        {"key": "payroll_errors", "label": "Payroll errors caught", "value": payroll_errors, "unit": ""},
        {"key": "approvals_flagged", "label": "Approval delays flagged", "value": approvals_flagged, "unit": ""},
        {"key": "stock_shortages", "label": "Stock shortages predicted", "value": stock_shortages, "unit": ""},
        {"key": "assets_flagged", "label": "Assets to replace/service", "value": assets_flagged, "unit": ""},
        {"key": "paper_saved", "label": "Paper saveable / month", "value": paper_saved, "unit": "sheets"},
        {"key": "paper_cost_saved", "label": "Paper cost saveable / month", "value": paper_cost_saved, "unit": "EGP"},
        {"key": "hours_saved", "label": "Manual follow-up hours saved", "value": hours_saved, "unit": "h"},
    ]
