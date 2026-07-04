"""
TC Platform — external alerting (email + webhook) and error reporting.

Turns critical notifications into real-world alerts so a server going down at
2 a.m. reaches a human, not just the in-app bell. Everything degrades to a safe
no-op (just logs) until SMTP or a webhook is configured, so it's safe to deploy
before the channels are set up.
"""
import json
import logging
import smtplib
import ssl
import traceback
from email.message import EmailMessage

from config import Config

log = logging.getLogger("tc.alerts")

_RANK = {"info": 0, "warning": 1, "critical": 2}


def should_alert(severity: str) -> bool:
    """True if this severity is at/above the configured alert threshold."""
    sev = (severity or "info").strip().lower()
    floor = (Config.ALERT_MIN_SEVERITY or "critical").strip().lower()
    return _RANK.get(sev, 0) >= _RANK.get(floor, 2)


def email_configured() -> bool:
    return bool(Config.SMTP_HOST and Config.ALERT_EMAILS)


def send_email(subject: str, body: str) -> bool:
    """Send a plain-text email to the configured recipients. Returns success."""
    if not email_configured():
        log.info("ALERT (email not configured): %s", subject)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = Config.SMTP_FROM
        msg["To"] = ", ".join(Config.ALERT_EMAILS)
        msg.set_content(body)
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as s:
            if Config.SMTP_TLS:
                s.starttls(context=ssl.create_default_context())
            if Config.SMTP_USER:
                s.login(Config.SMTP_USER, Config.SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("send_email failed: %s", exc)
        return False


def send_email_to(recipients, subject: str, body: str) -> bool:
    """Send a plain-text email to specific recipients (e.g. approvers). No-op when
    SMTP isn't configured or there are no valid recipients."""
    recips = [r for r in (recipients or []) if r and "@" in r]
    if not (Config.SMTP_HOST and recips):
        log.info("EMAIL (skipped — not configured / no recipients): %s", subject)
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = Config.SMTP_FROM
        msg["To"] = ", ".join(recips)
        msg.set_content(body)
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as s:
            if Config.SMTP_TLS:
                s.starttls(context=ssl.create_default_context())
            if Config.SMTP_USER:
                s.login(Config.SMTP_USER, Config.SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("send_email_to failed: %s", exc)
        return False


def post_webhook(payload: dict) -> bool:
    """POST a JSON payload to the configured webhook (WhatsApp/Slack/Teams/n8n)."""
    if not Config.ALERT_WEBHOOK_URL:
        return False
    try:
        import requests
        requests.post(Config.ALERT_WEBHOOK_URL, json=payload, timeout=8)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("post_webhook failed: %s", exc)
        return False


def _link():
    return (Config.PUBLIC_BASE_URL + "/") if Config.PUBLIC_BASE_URL else "the TC Platform"


def notify_external(notif: dict) -> bool:
    """Send one notification out via every configured channel."""
    sev = (notif.get("severity") or "info").upper()
    title = notif.get("title") or "Notification"
    message = notif.get("message") or ""
    module = notif.get("module") or ""
    subject = f"[TC Platform][{sev}] {title}"
    body = (f"{title}\n\n{message}\n\n"
            f"Source: {module or 'platform'}\n"
            f"Open: {_link()}\n")
    sent = send_email(subject, body)
    hooked = post_webhook({"severity": sev, "title": title, "message": message,
                           "module": module, "text": f"*[{sev}]* {title}\n{message}"})
    return sent or hooked


def dispatch_pending_alerts() -> int:
    """Find unsent notifications at/above the alert threshold and send them once.

    Uses the notifications.alerted flag so each alert goes out exactly once.
    Returns the number of alerts dispatched. Safe no-op if channels are off
    (then nothing is marked, so they'll go out once you configure a channel).
    """
    from app.db import get_db
    channels_on = email_configured() or bool(Config.ALERT_WEBHOOK_URL)
    if not channels_on:
        return 0
    floor = (Config.ALERT_MIN_SEVERITY or "critical").strip().lower()
    sevs = [s for s, r in _RANK.items() if r >= _RANK.get(floor, 2)]
    placeholders = ",".join("?" for _ in sevs)
    conn = get_db()
    sent = 0
    try:
        rows = conn.execute(
            f"SELECT id, severity, module, title, message FROM notifications "
            f"WHERE alerted = 0 AND severity IN ({placeholders}) "
            f"ORDER BY id DESC LIMIT 20", tuple(sevs)).fetchall()
        for r in rows:
            ok = notify_external({"severity": r["severity"], "module": r["module"],
                                  "title": r["title"], "message": r["message"]})
            # mark as handled regardless, so we don't spam on transient failures
            conn.execute("UPDATE notifications SET alerted = 1 WHERE id = ?", (r["id"],))
            if ok:
                sent += 1
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("dispatch_pending_alerts failed: %s", exc)
    finally:
        conn.close()
    return sent


def report_error(exc: BaseException, path: str = "") -> None:
    """Log a 500-level error to disk and email it to admins."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        logdir = Config.DATA_DIR / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        with open(logdir / "errors.log", "a", encoding="utf-8") as f:
            f.write(f"\n=== ERROR at {path} ===\n{tb}\n")
    except Exception:  # noqa: BLE001
        pass
    send_email(f"[TC Platform][ERROR] 500 at {path or 'unknown'}",
               f"An unhandled error occurred at {path}:\n\n{tb}")
