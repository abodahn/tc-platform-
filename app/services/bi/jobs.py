"""
BI background jobs: evaluate KPI threshold alerts and send scheduled digests.

Alerts reuse the platform's existing notification + alerting pipeline: on an
ok->breach transition we insert a notification row (module='bi'); the bell shows
it and dispatch_pending_alerts() emails/webhooks it. No new alert plumbing.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

from app.db import get_db, utcnow
from app.services import alerts as alert_svc
from config import Config

from . import analyze, insights, store

log = logging.getLogger("tc.bi.jobs")

_OPS = {">": lambda a, b: a > b, "<": lambda a, b: a < b,
        ">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b,
        "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
_CADENCE_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}


def _insert_notification(severity, title, message, ext_key):
    conn = get_db()
    try:
        # dedup: skip if an unread bi notification with this key already exists
        existing = conn.execute(
            "SELECT id FROM notifications WHERE ext_key=? AND is_read=0", (ext_key,)).fetchone()
        if existing:
            return False
        conn.execute(
            """INSERT INTO notifications (severity, module, title, message, created_at,
               is_read, ext_key, alerted) VALUES (?,?,?,?,?,0,?,0)""",
            (severity, "bi", title, message, utcnow(), ext_key))
        conn.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        log.warning("bi notification insert failed: %s", exc)
        return False
    finally:
        conn.close()


def evaluate_alerts():
    """Check every BI alert; raise a notification on a fresh breach. Returns the
    number of new breach notifications created."""
    created = 0
    for a in store.list_alerts():
        ds = store.get_dataset(a["dataset_id"])
        if not ds:
            continue
        try:
            val = analyze.kpi_value(ds["columns"], ds["rows"], a["column_name"], a["agg"])
        except Exception:
            continue
        op = _OPS.get(a["op"], _OPS[">"])
        breached = op(val, float(a["threshold"]))
        state = "breach" if breached else "ok"
        fresh = breached and a.get("last_state") != "breach"
        if fresh:
            sev = "critical"
            title = f"BI alert: {a['name'] or a['column_name']}"
            msg = (f"{a['agg']} of {a['column_name']} = {round(val, 2)} "
                   f"{a['op']} {a['threshold']} (dataset {ds['meta'].get('name', a['dataset_id'])})")
            if _insert_notification(sev, title, msg, ext_key=f"bi_alert:{a['id']}"):
                created += 1
        store.update_alert_state(a["id"], val, state, fresh)
    if created:
        try:
            alert_svc.dispatch_pending_alerts()
        except Exception:
            pass
    return created


def _send_email_to(recipients, subject, body):
    if not (Config.SMTP_HOST and recipients):
        log.info("BI digest (email not configured): %s", subject)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = Config.SMTP_FROM
        msg["To"] = ", ".join(recipients)
        msg.set_content(body)
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as s:
            if Config.SMTP_TLS:
                s.starttls(context=ssl.create_default_context())
            if Config.SMTP_USER:
                s.login(Config.SMTP_USER, Config.SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("BI digest send failed: %s", exc)
        return False


def _digest_body(dash, ds, lang="en"):
    lines = [f"{dash['name']}", "=" * len(dash["name"]), ""]
    spec = dash.get("spec", {})
    for k in spec.get("kpis", []):
        lines.append(f"  • {k['label']}: {k.get('value')}")
    lines.append("")
    lines.append("Insights:")
    for ins in insights.generate(ds["profile"], ds["columns"], ds["rows"], lang):
        lines.append(f"  - {ins['text']}")
    base = (Config.PUBLIC_BASE_URL or "").rstrip("/")
    if base:
        lines += ["", f"Open in TC Platform: {base}/bi/dashboard/{dash['id']}"]
    return "\n".join(lines)


def send_digest(digest_id):
    """Send one digest now. Returns True on send."""
    digests = {d["id"]: d for d in store.list_digests()}
    d = digests.get(int(digest_id))
    if not d:
        return False
    dash = store.get_dashboard(d["dashboard_id"])
    if not dash:
        return False
    ds = store.get_dataset(dash["dataset_id"])
    if not ds:
        return False
    recipients = [e.strip() for e in (d["recipients"] or "").split(",") if e.strip()]
    ok = _send_email_to(recipients, f"[TC Platform] {dash['name']} — dashboard digest",
                        _digest_body(dash, ds, dash.get("lang", "en")))
    if ok:
        store.mark_digest_sent(d["id"])
    return ok


def _due(last_sent, cadence):
    days = _CADENCE_DAYS.get(cadence, 7)
    if not last_sent:
        return True
    try:
        prev = datetime.fromisoformat(str(last_sent).replace("Z", "").strip()[:19])
    except Exception:
        return True
    try:
        now = datetime.fromisoformat(str(utcnow()).replace("Z", "").strip()[:19])
    except Exception:
        now = datetime.utcnow()
    return (now - prev).total_seconds() >= days * 86400


def run_due_digests():
    """For a scheduler/cron: send any digest whose cadence has elapsed."""
    sent = 0
    for d in store.list_digests():
        if _due(d.get("last_sent"), d.get("cadence", "weekly")):
            if send_digest(d["id"]):
                sent += 1
    return sent
