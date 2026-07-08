"""
Orchestrator — runs every predictor, scores the risk, and persists an auditable
snapshot into the ai_* tables. Also mirrors critical alerts into the platform
notification bell. Safe to run on demand or from a scheduled job.
"""
import json
import uuid
from datetime import datetime, timedelta

from app.ai_engine import (ticket_predictor, asset_predictor, maintenance_predictor,
                           inventory_predictor, procurement_predictor,
                           payroll_anomaly_detector, hr_predictor, paperless_predictor,
                           recommendation_engine as rec)
from app.ai_engine import risk_scoring as rs
from app.ai_engine.base import level_of

_PREDICTORS = [
    ("tickets", ticket_predictor),
    ("assets", asset_predictor),
    ("maintenance", maintenance_predictor),
    ("inventory", inventory_predictor),
    ("procurement", procurement_predictor),   # emits tickets->procurement + approvals
    ("payroll", payroll_anomaly_detector),
    ("hr", hr_predictor),
    ("paperless", paperless_predictor),
]

_ALERT_THRESHOLD = 41    # Medium and above become alerts


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _due_from_horizon(horizon):
    days = 7
    try:
        if horizon and horizon.endswith("d"):
            days = max(1, int(horizon[:-1]))
    except Exception:  # noqa: BLE001
        days = 7
    return (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d")


def run(conn=None, username="system"):
    own = conn is None
    if own:
        from app.db import get_db
        conn = get_db()
    try:
        from app.intelligence.schema import create_and_seed
        create_and_seed(conn)

        # 1) Collect findings from every predictor (each isolated).
        all_findings = []
        for _name, mod in _PREDICTORS:
            try:
                all_findings.extend(mod.predict(conn) or [])
            except Exception:  # noqa: BLE001
                continue

        by_domain = {}
        for f in all_findings:
            by_domain.setdefault(f["domain"], []).append(f)

        # 2) Domain + health scores.
        domain_scores = {d: rs.domain_score(fs) for d, fs in by_domain.items()}
        for d in rs.DOMAIN_WEIGHTS:
            domain_scores.setdefault(d, 0.0)
        health = rs.health_score(domain_scores)

        run_uid = uuid.uuid4().hex[:12]
        now = _now()

        # 3) Metrics (counts + business impact).
        paper_digitizable = 0
        for f in by_domain.get("paperless", []):
            paper_digitizable += int((f.get("value") or 0) * 0.6)
        metrics_ctx = {"paper_digitizable_sheets": paper_digitizable}
        impact = rs.estimate_business_impact(by_domain, metrics_ctx)

        # 4) Persist snapshot — replace derived rows, keep human-touched alerts.
        conn.execute("DELETE FROM ai_risk_scores")
        for d, sc in domain_scores.items():
            top = max(by_domain.get(d, []), key=lambda x: x["risk_score"], default=None)
            conn.execute(
                "INSERT INTO ai_risk_scores (run_uid,domain,score,level,headline,detail_json,computed_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (run_uid, d, sc, level_of(sc), (top["title"] if top else ""),
                 json.dumps({"label": rs.DOMAIN_LABELS.get(d, d), "n": len(by_domain.get(d, []))}), now))

        conn.execute("DELETE FROM ai_predictions")
        for f in sorted(all_findings, key=lambda x: x["risk_score"], reverse=True)[:60]:
            conn.execute(
                "INSERT INTO ai_predictions (run_uid,domain,entity_type,entity_ref,kind,value,horizon,confidence,detail_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (run_uid, f["domain"], f["entity_type"], f["entity_ref"], f["kind"],
                 f.get("value"), f.get("horizon"), f.get("confidence"),
                 json.dumps({"title": f["title"], "risk": f["risk_score"], "why": f["explanation"]}), now))

        conn.execute("DELETE FROM ai_dashboard_metrics")
        for m in impact:
            conn.execute(
                "INSERT INTO ai_dashboard_metrics (run_uid,metric_key,value,label,unit,computed_at) VALUES (?,?,?,?,?,?)",
                (run_uid, m["key"], m["value"], m["label"], m["unit"], now))
        conn.execute(
            "INSERT INTO ai_dashboard_metrics (run_uid,metric_key,value,label,unit,computed_at) VALUES (?,?,?,?,?,?)",
            (run_uid, "health_score", health, "Company health score", "/100", now))

        conn.execute("DELETE FROM ai_recommendations")
        for i, r in enumerate(rec.top_recommendations(all_findings, k=12)):
            conn.execute(
                "INSERT INTO ai_recommendations (run_uid,domain,title,detail,priority,created_at) VALUES (?,?,?,?,?,?)",
                (run_uid, r["domain"], r["title"], r["action"], 100 - i, now))

        # 5) Alerts: regenerate untouched ('new') ones; refresh human-touched ones.
        conn.execute("DELETE FROM ai_alerts WHERE status='new'")
        n_alerts = 0
        counter = 0
        for f in sorted(all_findings, key=lambda x: x["risk_score"], reverse=True):
            if f["risk_score"] < _ALERT_THRESHOLD:
                continue
            counter += 1
            existing = conn.execute(
                "SELECT id FROM ai_alerts WHERE domain=? AND entity_ref=? AND title=? AND status<>'new'",
                (f["domain"], f["entity_ref"], f["title"])).fetchone()
            if existing:
                conn.execute(
                    "UPDATE ai_alerts SET risk_score=?, severity=?, recommendation=?, explanation=?, updated_at=? WHERE id=?",
                    (f["risk_score"], f["severity"], f["recommendation"], f["explanation"], now, existing["id"]))
                continue
            alert_uid = f"AIA-{run_uid[:5]}-{counter:03d}"
            conn.execute(
                "INSERT INTO ai_alerts (alert_uid,run_uid,domain,entity_type,entity_ref,title,risk_score,"
                "severity,impact,recommendation,responsible,status,escalation_level,link,explanation,created_at,due_date,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'new', 0, ?,?,?,?,?)",
                (alert_uid, run_uid, f["domain"], f["entity_type"], f["entity_ref"], f["title"],
                 f["risk_score"], f["severity"], f["impact"], f["recommendation"], f["responsible"],
                 f.get("link") or "/intelligence/alerts", f["explanation"], now,
                 _due_from_horizon(f.get("horizon")), now))
            n_alerts += 1

        # 6) Mirror the most severe fresh alerts into the notification bell (deduped).
        _push_bell(conn, run_uid, now)

        conn.execute(
            "INSERT INTO ai_model_runs (run_uid,started_at,finished_at,n_alerts,n_predictions,health_score,status,note) "
            "VALUES (?,?,?,?,?,?, 'ok', ?)",
            (run_uid, now, _now(), n_alerts, min(60, len(all_findings)), health,
             f"{len(all_findings)} findings across {len(by_domain)} domains by {username}"))
        conn.commit()
        return {"run_uid": run_uid, "health": health, "domain_scores": domain_scores,
                "n_alerts": n_alerts, "n_findings": len(all_findings), "impact": impact}
    finally:
        if own:
            conn.close()


def _push_bell(conn, run_uid, now):
    """Insert up to 5 critical AI alerts into the notifications bell, deduped by ext_key."""
    try:
        crit = conn.execute(
            "SELECT alert_uid,domain,title,risk_score FROM ai_alerts "
            "WHERE status='new' AND severity='critical' ORDER BY risk_score DESC LIMIT 5").fetchall()
        for a in crit:
            ext = "ai:" + a["alert_uid"]
            dup = conn.execute("SELECT 1 FROM notifications WHERE ext_key=?", (ext,)).fetchone()
            if dup:
                continue
            conn.execute(
                "INSERT INTO notifications (severity,module,title,message,created_at,ext_key,link) "
                "VALUES ('critical','intelligence',?,?,?,?,?)",
                (f"AI risk: {a['title']}"[:120],
                 f"Predicted risk score {a['risk_score']} in {a['domain']}. Review in the Intelligence Center.",
                 now, ext, "/intelligence/alerts"))
    except Exception:  # noqa: BLE001
        pass
