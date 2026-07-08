"""Inventory / spare-parts intelligence — reads mnt_spare_parts."""
from app.ai_engine.base import finding, safe_rows


def predict(conn):
    out = []
    for s in [dict(r) for r in safe_rows(conn, "SELECT * FROM mnt_spare_parts WHERE is_active=1")]:
        stock = s.get("stock_qty") or 0
        reorder = s.get("reorder_level") or 0
        mn = s.get("min_level") or 0
        mx = s.get("max_level") or 0
        crit = s.get("criticality")
        risk = 0.0
        why = []
        kind = "stock"
        if mn > 0 and stock <= mn:
            risk += 45; why.append(f"stock {stock} ≤ min {mn}"); kind = "stockout"
        elif reorder > 0 and stock <= reorder:
            risk += 30; why.append(f"stock {stock} ≤ reorder {reorder}"); kind = "reorder"
        if crit == "critical" and stock <= max(reorder, mn):
            risk += 25; why.append("critical spare")
        elif crit == "high" and stock <= max(reorder, mn):
            risk += 12
        if mx > 0 and stock > mx * 1.5:
            risk = max(risk, 30); why.append(f"overstock (> max {mx})"); kind = "overstock"
        if risk >= 41:
            lead = s.get("lead_time_days") or 0
            reorder_kind = kind in ("stockout", "reorder")
            out.append(finding(
                "inventory", f"Spare {kind} · {s.get('code')} {s.get('name')}", risk,
                entity_type="spare", entity_ref=s.get("code") or str(s.get("id")),
                impact=("Maintenance delayed with a machine idle waiting for parts."
                        if reorder_kind else "Cash tied up in dead / excess stock."),
                recommendation=(f"Raise a purchase request now (lead time {lead}d)."
                                if reorder_kind else "Reduce future orders; redistribute stock."),
                responsible="Warehouse / Procurement",
                explanation="; ".join(why), kind=kind,
                horizon=(f"{lead}d" if lead else ""), link="/maintenance/spares"))
    return out
