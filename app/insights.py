"""
TC Platform — offline AI executive insights (Daily Briefing).

Synthesizes a plain-language situation report from real, platform-owned data
(maintenance + production). 100% on-premise, no internet. Returns translatable
structured insights (number + i18n label) so it renders in EN/AR/TR.
"""
from app.db import get_db
from app.maintenance import ai as mai


def executive_summary():
    conn = get_db()
    try:
        def c(sql, a=()):
            return conn.execute(sql, a).fetchone()["c"]

        risks = mai.risk_ranking(conn)
        high = [r for r in risks if r["band"] == "high"]
        stopped = c("SELECT COUNT(*) c FROM mnt_machines WHERE status='stopped'")
        open_crit = c("SELECT COUNT(*) c FROM mnt_tickets WHERE priority='critical' "
                      "AND status NOT IN ('closed','cancelled','rejected')")
        low = c("SELECT COUNT(*) c FROM mnt_spare_parts WHERE stock_qty<=reorder_level AND stock_qty>0")
        out = c("SELECT COUNT(*) c FROM mnt_spare_parts WHERE stock_qty<=0")
        pend = c("SELECT COUNT(*) c FROM mnt_approvals WHERE status='pending'")
        lines_down = c("SELECT COUNT(*) c FROM production_lines WHERE status IN ('down','maintenance')")

        insights = []

        def add(cond, sev, icon, n, key):
            if cond:
                insights.append({"sev": sev, "icon": icon, "n": n, "key": key})

        add(stopped, "crit", "factory", stopped, "sum_stopped")
        add(len(high), "crit", "alert", len(high), "sum_at_risk")
        add(out, "crit", "boxes", out, "sum_out_stock")
        add(open_crit, "warn", "headset", open_crit, "sum_open_crit")
        add(low, "warn", "boxes", low, "sum_low_stock")
        add(lines_down, "warn", "factory", lines_down, "sum_lines_down")
        add(pend, "info", "check", pend, "sum_pending_appr")

        focus = None
        if high:
            focus = {"code": high[0]["code"], "rec_key": "m." + high[0]["recommendation"][0]}

        if any(i["sev"] == "crit" for i in insights):
            headline = "sum_attention"
        elif any(i["sev"] == "warn" for i in insights):
            headline = "sum_watch"
        else:
            headline = "sum_healthy"
            insights.append({"sev": "good", "icon": "check", "n": "", "key": "sum_all_good"})

        return {"headline": headline, "insights": insights, "focus": focus}
    finally:
        conn.close()
