"""Turns findings into prioritized, de-duplicated recommendations."""


def top_recommendations(findings, k=10):
    recs = []
    seen = set()
    for f in sorted(findings, key=lambda x: x["risk_score"], reverse=True):
        action = f.get("recommendation")
        if not action:
            continue
        key = (f["domain"], action)
        if key in seen:
            continue
        seen.add(key)
        recs.append({
            "domain": f["domain"],
            "title": f["title"],
            "action": action,
            "risk_score": f["risk_score"],
            "level": f["level"],
            "responsible": f.get("responsible", ""),
        })
        if len(recs) >= k:
            break
    return recs


def top_issues(findings, k=10):
    return [{
        "domain": f["domain"], "title": f["title"], "risk_score": f["risk_score"],
        "level": f["level"], "severity": f["severity"], "responsible": f.get("responsible", ""),
    } for f in sorted(findings, key=lambda x: x["risk_score"], reverse=True)[:k]]


def departments_to_watch(findings, k=10):
    """Aggregate risk by responsible party / department."""
    agg = {}
    for f in findings:
        who = (f.get("responsible") or "Unassigned").strip() or "Unassigned"
        a = agg.setdefault(who, {"dept": who, "count": 0, "peak": 0.0, "sum": 0.0})
        a["count"] += 1
        a["peak"] = max(a["peak"], f["risk_score"])
        a["sum"] += f["risk_score"]
    rows = []
    for a in agg.values():
        a["score"] = round(0.6 * a["peak"] + 0.4 * (a["sum"] / a["count"]), 1)
        rows.append(a)
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:k]
