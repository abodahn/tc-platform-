"""Ticket / SLA intelligence — reads mnt_tickets."""
from app.ai_engine.base import finding, safe_rows, days_until, days_since

_OPEN = "status NOT IN ('closed','cancelled','rejected')"


def predict(conn):
    out = []
    tickets = [dict(r) for r in safe_rows(conn, f"SELECT * FROM mnt_tickets WHERE {_OPEN}")]
    for t in tickets:
        pr = (t.get("priority") or "medium")
        risk = {"critical": 70, "high": 55, "medium": 35, "low": 20}.get(pr, 30)
        why = [f"{pr} priority, still open"]
        du = days_until(t.get("response_due"))
        if du is not None:
            if du < 0:
                risk += 30; why.append(f"SLA due date passed {abs(du)}d ago")
            elif du <= 1:
                risk += 20; why.append("SLA due within 1 day")
            elif du <= 3:
                risk += 10; why.append("SLA due within 3 days")
        age = days_since(t.get("created_at")) or 0
        if age > 7:
            risk += 10; why.append(f"open for {age} days")
        if str(t.get("machine_running")) in ("0", "false", "False"):
            risk += 8; why.append("machine not running")
        if risk >= 45:
            out.append(finding(
                "tickets", f"SLA risk · {t.get('ticket_no')} ({pr})", risk,
                entity_type="ticket", entity_ref=t.get("ticket_no") or str(t.get("id")),
                impact="SLA breach and prolonged downtime if not actioned.",
                recommendation="Escalate and assign now; notify the responsible team.",
                responsible=t.get("assigned_to") or t.get("department") or "Maintenance",
                explanation="; ".join(why), kind="sla_risk", horizon="7d",
                link="/maintenance/tickets"))
    # recurring tickets on the same machine
    for r in [dict(x) for x in safe_rows(
            conn, "SELECT machine_id, COUNT(*) c FROM mnt_tickets WHERE machine_id IS NOT NULL "
                  "GROUP BY machine_id HAVING COUNT(*)>=4 ORDER BY c DESC LIMIT 5")]:
        c = r["c"]
        out.append(finding(
            "tickets", f"Recurring tickets on machine #{r['machine_id']} ({c})", min(90, 40 + c * 6),
            entity_type="machine", entity_ref=str(r["machine_id"]),
            impact="Chronic problem; the root cause is likely unresolved.",
            recommendation="Open a root-cause analysis; consider PM or replacement.",
            responsible="Maintenance", explanation=f"{c} tickets logged for this machine.",
            kind="recurrence"))
    # technician workload
    for r in [dict(x) for x in safe_rows(
            conn, f"SELECT assigned_to, COUNT(*) c FROM mnt_tickets WHERE {_OPEN} "
                  "AND assigned_to IS NOT NULL AND assigned_to<>'' GROUP BY assigned_to "
                  "HAVING COUNT(*)>=5 ORDER BY c DESC LIMIT 5")]:
        out.append(finding(
            "tickets", f"Technician overload · {r['assigned_to']} ({r['c']} open)", min(85, 35 + r["c"] * 5),
            entity_type="user", entity_ref=r["assigned_to"],
            impact="Slower resolution and burnout risk.",
            recommendation="Rebalance the workload or add capacity.",
            responsible="Maintenance", explanation=f"{r['c']} open tickets assigned.", kind="workload"))
    return out
