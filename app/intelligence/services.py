"""Read/query helpers + write actions for the Intelligence Center UI."""
from app.db import get_db, utcnow
from app.ai_engine import risk_scoring as rs
from app.ai_engine.base import level_of

_OPEN_STATUSES = ("new", "acknowledged", "in_progress", "escalated")
ALERT_STATUSES = ["new", "acknowledged", "in_progress", "resolved", "ignored", "escalated"]
FEEDBACK_VERDICTS = ["helpful", "not_helpful", "correct", "incorrect", "resolved", "false_alarm"]
SENSITIVE_DOMAINS = {"payroll", "hr"}


def _rows(conn, sql, p=()):
    try:
        return [dict(r) for r in conn.execute(sql, p).fetchall()]
    except Exception:  # noqa: BLE001
        return []


def dashboard_data():
    conn = get_db()
    try:
        scores = _rows(conn, "SELECT * FROM ai_risk_scores ORDER BY score DESC")
        for s in scores:
            s["color"] = rs.band_color(s["score"])
            s["label"] = rs.DOMAIN_LABELS.get(s["domain"], s["domain"])
        metrics = {m["metric_key"]: m for m in _rows(conn, "SELECT * FROM ai_dashboard_metrics")}
        recs = _rows(conn, "SELECT * FROM ai_recommendations ORDER BY priority DESC")
        run = _rows(conn, "SELECT * FROM ai_model_runs ORDER BY id DESC LIMIT 1")
        open_alerts = _rows(
            conn, "SELECT * FROM ai_alerts WHERE status IN ('new','acknowledged','in_progress','escalated') "
                  "ORDER BY risk_score DESC")
        # impact metrics (exclude health_score which is shown separately)
        impact = _rows(conn, "SELECT * FROM ai_dashboard_metrics WHERE metric_key<>'health_score' ORDER BY id")
        health = metrics.get("health_score", {}).get("value")
        if health is None:
            health = rs.health_score({s["domain"]: s["score"] for s in scores}) if scores else None
        # counts
        sev = {"critical": 0, "warning": 0, "info": 0}
        for a in open_alerts:
            sev[a.get("severity", "info")] = sev.get(a.get("severity", "info"), 0) + 1
        # departments to watch (aggregate open alerts by responsible)
        agg = {}
        for a in open_alerts:
            who = (a.get("responsible") or "Unassigned").strip() or "Unassigned"
            x = agg.setdefault(who, {"dept": who, "count": 0, "peak": 0.0, "sum": 0.0})
            x["count"] += 1
            x["peak"] = max(x["peak"], a["risk_score"])
            x["sum"] += a["risk_score"]
        dept_watch = []
        for x in agg.values():
            x["score"] = round(0.6 * x["peak"] + 0.4 * x["sum"] / x["count"], 1)
            x["level"] = level_of(x["score"])
            dept_watch.append(x)
        dept_watch.sort(key=lambda z: z["score"], reverse=True)
        return {
            "scores": scores, "metrics": metrics, "impact": impact, "recommendations": recs,
            "top_alerts": open_alerts[:10], "open_count": len(open_alerts), "sev": sev,
            "dept_watch": dept_watch[:10], "health": health,
            "last_run": run[0] if run else None,
        }
    finally:
        conn.close()


def list_alerts(status=None, domain=None, severity=None, limit=500, allow_sensitive=True):
    conn = get_db()
    try:
        sql = "SELECT * FROM ai_alerts WHERE 1=1"
        args = []
        if status and status != "all":
            if status == "open":
                sql += " AND status IN ('new','acknowledged','in_progress','escalated')"
            else:
                sql += " AND status=?"; args.append(status)
        if domain and domain != "all":
            sql += " AND domain=?"; args.append(domain)
        if severity and severity != "all":
            sql += " AND severity=?"; args.append(severity)
        if not allow_sensitive:
            sql += " AND domain NOT IN ('payroll','hr')"
        sql += " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, risk_score DESC LIMIT ?"
        args.append(limit)
        return _rows(conn, sql, tuple(args))
    finally:
        conn.close()


def get_alert(alert_id):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM ai_alerts WHERE id=?", (alert_id,)).fetchone()
        fb = _rows(conn, "SELECT * FROM ai_feedback WHERE alert_id=? ORDER BY id DESC", (alert_id,))
        return (dict(r) if r else None), fb
    finally:
        conn.close()


def set_alert_status(alert_id, status):
    if status not in ALERT_STATUSES:
        return False
    conn = get_db()
    try:
        esc = 1 if status == "escalated" else 0
        conn.execute("UPDATE ai_alerts SET status=?, escalation_level=escalation_level+?, updated_at=? WHERE id=?",
                     (status, esc, utcnow(), alert_id))
        conn.commit()
        return True
    finally:
        conn.close()


def add_feedback(alert_id, username, verdict, note=""):
    if verdict not in FEEDBACK_VERDICTS:
        return False
    conn = get_db()
    try:
        conn.execute("INSERT INTO ai_feedback (alert_id,username,verdict,note,created_at) VALUES (?,?,?,?,?)",
                     (alert_id, username, verdict, note[:400], utcnow()))
        if verdict in ("resolved", "false_alarm"):
            conn.execute("UPDATE ai_alerts SET status=?, updated_at=? WHERE id=?",
                         ("resolved" if verdict == "resolved" else "ignored", utcnow(), alert_id))
        conn.commit()
        return True
    finally:
        conn.close()


def breakdown(allow_sensitive=True):
    conn = get_db()
    try:
        scores = _rows(conn, "SELECT * FROM ai_risk_scores ORDER BY score DESC")
        out = []
        for s in scores:
            if not allow_sensitive and s["domain"] in SENSITIVE_DOMAINS:
                continue
            s["color"] = rs.band_color(s["score"])
            s["label"] = rs.DOMAIN_LABELS.get(s["domain"], s["domain"])
            s["alerts"] = _rows(conn, "SELECT * FROM ai_alerts WHERE domain=? ORDER BY risk_score DESC LIMIT 40", (s["domain"],))
            out.append(s)
        return out
    finally:
        conn.close()


def runs(limit=30):
    conn = get_db()
    try:
        return _rows(conn, "SELECT * FROM ai_model_runs ORDER BY id DESC LIMIT ?", (limit,))
    finally:
        conn.close()


def feedback_summary():
    conn = get_db()
    try:
        return _rows(conn, "SELECT verdict, COUNT(*) c FROM ai_feedback GROUP BY verdict ORDER BY c DESC")
    finally:
        conn.close()


def get_setting(key, default=""):
    conn = get_db()
    try:
        r = conn.execute("SELECT svalue FROM ai_system_settings WHERE skey=?", (key,)).fetchone()
        return r["svalue"] if r else default
    finally:
        conn.close()


def set_setting(key, value):
    conn = get_db()
    try:
        conn.execute("INSERT OR IGNORE INTO ai_system_settings (skey,svalue) VALUES (?,?)", (key, str(value)))
        conn.execute("UPDATE ai_system_settings SET svalue=? WHERE skey=?", (str(value), key))
        conn.commit()
    finally:
        conn.close()


def log_query(username, query):
    conn = get_db()
    try:
        conn.execute("INSERT INTO ai_assistant_queries (username,query,created_at) VALUES (?,?,?)",
                     (username, (query or "")[:500], utcnow()))
        conn.commit()
    finally:
        conn.close()
