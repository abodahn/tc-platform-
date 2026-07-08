"""Paperless / digital-transformation intelligence — reads paper_usage."""
from app.ai_engine.base import finding, safe_rows, linear_trend


def predict(conn):
    out = []
    rows = [dict(r) for r in safe_rows(
        conn, "SELECT department,month,sheets,cost,digitizable FROM paper_usage ORDER BY department, month")]
    by = {}
    for r in rows:
        by.setdefault(r["department"], []).append(r)
    for dept, series in by.items():
        sheets = [s["sheets"] or 0 for s in series]
        if not sheets:
            continue
        slope, _ = linear_trend(sheets)
        last = sheets[-1]
        prev = sheets[-2] if len(sheets) > 1 else last
        risk = 0.0
        why = []
        if prev and (last - prev) / prev >= 0.25:
            risk += 45; why.append(f"paper up {((last - prev) / prev * 100):.0f}% last month")
        elif slope > 0:
            risk += 20; why.append("rising paper-usage trend")
        if last >= 3000:
            risk += 15; why.append(f"high volume ({last} sheets/month)")
        if any(s.get("digitizable") for s in series):
            why.append("has digitizable forms")
        if risk >= 41:
            saveable = int(last * 0.6)
            out.append(finding(
                "paperless", f"Paper consumption · {dept}", risk,
                entity_type="department", entity_ref=dept,
                impact=f"~{saveable} sheets/mo (~{saveable * 0.12:.0f} EGP) avoidable.",
                recommendation="Digitize the top forms in this department's approval flow.",
                responsible=dept, explanation="; ".join(why),
                kind="paper_spike", value=last))
    return out
