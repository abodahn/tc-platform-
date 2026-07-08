"""Asset intelligence — reads the assets register."""
from app.ai_engine.base import finding, safe_rows, days_until, days_since


def predict(conn):
    out = []
    for a in [dict(r) for r in safe_rows(conn, "SELECT * FROM assets")]:
        risk = 0.0
        why = []
        wu = days_until(a.get("warranty_expiry"))
        if wu is not None:
            if wu < 0:
                risk += 30; why.append(f"warranty expired {abs(wu)}d ago")
            elif wu <= 60:
                risk += 18; why.append(f"warranty expires in {wu}d")
        age = days_since(a.get("purchase_date")) or 0
        yrs = age / 365.0
        if yrs >= 5:
            risk += 28; why.append(f"{yrs:.1f} years old")
        elif yrs >= 3:
            risk += 12; why.append(f"{yrs:.1f} years old")
        cost = a.get("purchase_cost") or 0
        maint = a.get("maint_cost_ytd") or 0
        heavy = cost and (maint / cost) >= 0.3
        if heavy:
            risk += 25; why.append(f"maintenance = {maint / cost * 100:.0f}% of asset value")
        cond = a.get("condition")
        if cond == "poor":
            risk += 20; why.append("condition poor")
        elif cond == "fair":
            risk += 8
        if not a.get("owner") or not a.get("location"):
            risk += 8; why.append("incomplete asset record")
        if risk >= 41:
            replace = yrs >= 5 or heavy or cond == "poor"
            out.append(finding(
                "assets", f"Asset {'replace' if replace else 'service'} · {a.get('tag')} {a.get('name')}", risk,
                entity_type="asset", entity_ref=a.get("tag") or str(a.get("id")),
                impact="Unplanned failure, downtime, or a budget shock at end of life.",
                recommendation=("Add to the replacement plan." if replace
                                else "Schedule service / renew the warranty."),
                responsible=a.get("department") or "IT / Assets",
                explanation="; ".join(why) or "Ageing asset.",
                kind="asset_health", horizon="90d", value=round(cost, 2)))
    return out
