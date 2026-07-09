"""
Smart Factory — smart approval engine + cost intelligence.

Cost-threshold-driven multi-level approvals for factory decisions (rework, wash
re-runs, spare purchases, overtime, order changes, production holds), with an
explainable AI recommendation, delegation-ready role routing, digital sign-off
and a full audit trail. Reuses the platform DB shim.
"""
from app.db import get_db, utcnow

KINDS = {
    "rework": "Rework / repair", "wash_rerun": "Wash re-run", "spare_purchase": "Spare-part purchase",
    "overtime": "Overtime", "order_change": "Order change", "production_hold": "Production hold",
}
OPEN = ("pending", "escalated")


def _rows(conn, sql, p=()):
    try:
        return [dict(r) for r in conn.execute(sql, p).fetchall()]
    except Exception:  # noqa: BLE001
        return []


def _cfg(conn, kind):
    r = conn.execute("SELECT * FROM sf_cost_config WHERE kind=?", (kind,)).fetchone()
    if r:
        return dict(r)
    return {"kind": kind, "currency": "EGP", "auto_below": 0, "l1_below": 1000, "l2_below": 5000,
            "l1_role": "production_supervisor", "l2_role": "factory_manager", "l3_role": "executive"}


def _ladder(cfg, cost):
    """Levels required for this cost. Empty => auto-approve."""
    if cost < (cfg["auto_below"] or 0):
        return []
    levels = [(1, cfg["l1_role"], cfg["auto_below"] or 0)]
    if cost >= (cfg["l1_below"] or 0) and (cfg["l1_below"] or 0) > 0:
        levels.append((2, cfg["l2_role"], cfg["l1_below"]))
    if cost >= (cfg["l2_below"] or 0) and (cfg["l2_below"] or 0) > 0:
        levels.append((3, cfg["l3_role"], cfg["l2_below"]))
    return levels


def ai_reco(kind, cost, cfg, qty):
    """Explainable, rule-based recommendation + risk score (0-100)."""
    l2 = cfg.get("l2_below") or 5000
    risk = min(100, int(20 + 70 * (cost / l2))) if l2 else 40
    per_pc = (cost / qty) if qty else 0
    why = []
    if cost < (cfg.get("auto_below") or 0):
        return "auto_approve", max(5, risk // 3), "Within auto-approve tolerance; no sign-off needed."
    if kind == "rework":
        # rework is worth it only if repair is cheaper than scrap+remake (rule of thumb ~ per-pc)
        if per_pc and per_pc <= 8:
            why.append(f"repair ~{per_pc:.1f}/pc is cheaper than scrap+remake"); reco = "approve"
        else:
            why.append(f"repair ~{per_pc:.1f}/pc is high — compare with scrap"); reco = "review"
    elif kind == "wash_rerun":
        why.append("wash re-run wastes water/energy — confirm shade band first"); reco = "review"
    elif kind == "spare_purchase":
        why.append("keeps machines running; check stock + lead time"); reco = "approve"
    elif kind == "overtime":
        why.append("only if it protects OTIF / delivery date"); reco = "review"
    else:
        reco = "review"; why.append("verify budget and delivery impact")
    if cost >= l2:
        reco = "escalate"; why.append("high value — verify against the order budget")
    return reco, risk, "; ".join(why)


def submit(kind, title, description, cost, order_id=None, line_id=None, qty=0, requested_by="", department=""):
    conn = get_db()
    try:
        cost = float(cost or 0)
        cfg = _cfg(conn, kind)
        reco, risk, reason = ai_reco(kind, cost, cfg, int(qty or 0))
        ladder = _ladder(cfg, cost)
        n = conn.execute("SELECT COUNT(*) c FROM sf_approvals").fetchone()["c"] + 3001
        ref = f"SFA-{n}"
        status = "auto_approved" if not ladder else "pending"
        total = len(ladder)
        cur = conn.execute(
            "INSERT INTO sf_approvals (ref_no,kind,title,description,cost_amount,currency,order_id,line_id,qty,"
            "requested_by,department,status,current_level,total_levels,ai_recommendation,ai_risk,ai_reason,created_at,decided_at,decided_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref, kind, title, description, cost, cfg["currency"], order_id or None, line_id or None, int(qty or 0),
             requested_by, department, status, 1, total, reco, risk, reason, utcnow(),
             utcnow() if status == "auto_approved" else None, "system" if status == "auto_approved" else None))
        aid = cur.lastrowid
        for lvl, role, thr in ladder:
            conn.execute("INSERT INTO sf_approval_steps (approval_id,level,role,threshold,status) VALUES (?,?,?,?,?)",
                         (aid, lvl, role, thr, "pending"))
        conn.commit()
        return ref, status
    finally:
        conn.close()


def decide(approval_id, decision, approver, comment=""):
    conn = get_db()
    try:
        a = conn.execute("SELECT * FROM sf_approvals WHERE id=?", (approval_id,)).fetchone()
        if not a or a["status"] not in OPEN:
            return False
        a = dict(a)
        step = conn.execute("SELECT * FROM sf_approval_steps WHERE approval_id=? AND level=? AND status='pending'",
                            (approval_id, a["current_level"])).fetchone()
        now = utcnow()
        if decision == "reject":
            if step:
                conn.execute("UPDATE sf_approval_steps SET status='rejected', approver=?, decided_at=?, comment=? WHERE id=?",
                             (approver, now, comment, step["id"]))
            conn.execute("UPDATE sf_approvals SET status='rejected', decided_at=?, decided_by=?, decision_reason=? WHERE id=?",
                         (now, approver, comment, approval_id))
        else:
            if step:
                conn.execute("UPDATE sf_approval_steps SET status='approved', approver=?, decided_at=?, comment=? WHERE id=?",
                             (approver, now, comment, step["id"]))
            if a["current_level"] < a["total_levels"]:
                conn.execute("UPDATE sf_approvals SET current_level=current_level+1 WHERE id=?", (approval_id,))
            else:
                conn.execute("UPDATE sf_approvals SET status='approved', decided_at=?, decided_by=? WHERE id=?",
                             (now, approver, approval_id))
        conn.commit()
        return True
    finally:
        conn.close()


def list_approvals(status="all", limit=300):
    conn = get_db()
    try:
        sql = "SELECT a.*, o.po_no FROM sf_approvals a LEFT JOIN sf_orders o ON o.id=a.order_id"
        args = []
        if status == "open":
            sql += " WHERE a.status IN ('pending','escalated')"
        elif status and status != "all":
            sql += " WHERE a.status=?"; args.append(status)
        sql += " ORDER BY a.id DESC LIMIT ?"; args.append(limit)
        rows = _rows(conn, sql, tuple(args))
        for r in rows:
            r["steps"] = _rows(conn, "SELECT * FROM sf_approval_steps WHERE approval_id=? ORDER BY level", (r["id"],))
        return rows
    finally:
        conn.close()


def dashboard():
    conn = get_db()
    try:
        counts = {s: conn.execute("SELECT COUNT(*) c FROM sf_approvals WHERE status=?", (s,)).fetchone()["c"]
                  for s in ("pending", "approved", "rejected", "auto_approved")}
        pend_val = conn.execute("SELECT COALESCE(SUM(cost_amount),0) v FROM sf_approvals WHERE status='pending'").fetchone()["v"] or 0
        return {"counts": counts, "pending_value": round(pend_val, 0)}
    finally:
        conn.close()


def kinds_config():
    conn = get_db()
    try:
        return _rows(conn, "SELECT * FROM sf_cost_config ORDER BY id")
    finally:
        conn.close()
