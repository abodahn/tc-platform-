"""Maintenance / machine-failure intelligence — reads mnt_machines."""
from app.ai_engine.base import finding, safe_rows, days_since


def predict(conn):
    out = []
    for m in [dict(r) for r in safe_rows(conn, "SELECT * FROM mnt_machines")]:
        risk = 0.0
        why = []
        bd = m.get("breakdowns") or 0
        if bd >= 5:
            risk += 35; why.append(f"{bd} breakdowns on record")
        elif bd >= 2:
            risk += 18; why.append(f"{bd} breakdowns on record")
        dt = m.get("total_downtime_min") or 0
        if dt >= 600:
            risk += 25; why.append(f"{int(dt)} min total downtime")
        elif dt >= 180:
            risk += 12; why.append(f"{int(dt)} min total downtime")
        crit = m.get("criticality")
        if crit == "critical":
            risk += 15; why.append("critical machine")
        elif crit == "high":
            risk += 8
        pm = days_since(m.get("last_pm_date"))
        if pm is not None and pm > 180:
            risk += 15; why.append(f"no preventive maintenance for {pm}d")
        if m.get("status") == "stopped":
            risk += 20; why.append("currently stopped")
        if risk >= 41:
            out.append(finding(
                "maintenance", f"Machine failure risk · {m.get('code')} {m.get('name')}", risk,
                entity_type="machine", entity_ref=m.get("code") or str(m.get("id")),
                impact="Production disruption and emergency-repair cost.",
                recommendation="Schedule preventive maintenance and pre-stage likely spares.",
                responsible=m.get("department") or "Maintenance",
                explanation="; ".join(why), kind="machine_failure", horizon="30d",
                link="/maintenance/machines"))
    return out
