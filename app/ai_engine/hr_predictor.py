"""HR / probation-evaluation intelligence — reads hr_probation."""
from app.ai_engine.base import finding, safe_rows, days_until


def predict(conn):
    out = []
    for h in [dict(r) for r in safe_rows(conn, "SELECT * FROM hr_probation")]:
        st = (h.get("status") or "pending")
        risk = 0.0
        why = []
        du = days_until(h.get("due_date"))
        if st == "pending":
            if du is not None and du < 0:
                risk += 45; why.append(f"evaluation overdue by {abs(du)}d")
            elif du is not None and du <= 7:
                risk += 25; why.append(f"due in {du}d and not started")
            else:
                risk += 12; why.append("not yet evaluated")
            if not h.get("evaluated_at"):
                why.append("no manager feedback yet")
        sc = h.get("score")
        if sc is not None and sc < 60:
            risk += 40; why.append(f"score {sc} (below pass mark)")
        if st == "failed":
            risk = max(risk, 70); why.append("marked failed")
        if risk >= 41:
            out.append(finding(
                "hr", f"Probation risk · {h.get('employee_code')} {h.get('name')}", risk,
                entity_type="probation", entity_ref=h.get("employee_code"),
                impact="Missed evaluation window or a wrong confirmation decision.",
                recommendation="Follow up with the manager; escalate to HR if overdue.",
                responsible=h.get("manager") or "HR",
                explanation="; ".join(why), kind="probation_risk", horizon="14d"))
    return out
