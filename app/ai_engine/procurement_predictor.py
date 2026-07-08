"""Procurement + approval-cycle intelligence — reads pr_requests / pr_steps."""
from app.ai_engine.base import finding, safe_rows, days_since


def predict(conn):
    out = []
    # Pending PRs aging in the approval cycle
    for p in [dict(r) for r in safe_rows(
            conn, "SELECT id,pr_no,title,department,total,current_seq,submitted_at "
                  "FROM pr_requests WHERE status='pending'")]:
        age = days_since(p.get("submitted_at")) or 0
        risk = 25 + min(50, age * 5)
        if (p.get("total") or 0) >= 50000:
            risk += 10
        if risk >= 41:
            out.append(finding(
                "procurement", f"PR approval delay · {p.get('pr_no')}", risk,
                entity_type="pr", entity_ref=p.get("pr_no") or str(p.get("id")),
                impact="Delivery slips; possible production or maintenance delay.",
                recommendation="Escalate to the current approver or set a delegate.",
                responsible=p.get("department") or "Procurement",
                explanation=f"Pending {age}d at approval stage {p.get('current_seq')}"
                            f" · value {p.get('total')}.",
                kind="approval_delay", horizon="7d", link="/procurement/list"))
    # Approver bottlenecks (one person holding many approvals)
    for r in [dict(x) for x in safe_rows(
            conn, "SELECT approver_name, COUNT(*) c FROM pr_steps WHERE status='pending' "
                  "AND approver_name IS NOT NULL AND approver_name<>'' "
                  "GROUP BY approver_name HAVING COUNT(*)>=3 ORDER BY c DESC LIMIT 5")]:
        out.append(finding(
            "approvals", f"Approval bottleneck · {r['approver_name']} ({r['c']})", min(85, 35 + r["c"] * 7),
            entity_type="approver", entity_ref=r["approver_name"],
            impact="Multiple requests stalled on a single approver.",
            recommendation="Notify the approver or assign a delegate.",
            responsible=r["approver_name"], explanation=f"{r['c']} approvals pending with this person.",
            kind="bottleneck", link="/procurement/list"))
    return out
