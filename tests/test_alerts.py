"""Tests for external alerting (email/webhook) and 500 error reporting."""
import app.services.alerts as alerts
from app.db import get_db
from config import Config


def test_should_alert_respects_threshold(monkeypatch):
    monkeypatch.setattr(Config, "ALERT_MIN_SEVERITY", "critical")
    assert alerts.should_alert("critical") is True
    assert alerts.should_alert("warning") is False
    monkeypatch.setattr(Config, "ALERT_MIN_SEVERITY", "warning")
    assert alerts.should_alert("warning") is True
    assert alerts.should_alert("info") is False


def test_send_email_is_safe_noop_when_unconfigured(monkeypatch):
    monkeypatch.setattr(Config, "SMTP_HOST", "")
    monkeypatch.setattr(Config, "ALERT_EMAILS", [])
    assert alerts.send_email("subject", "body") is False  # no crash, just False


def test_dispatch_sends_once_then_dedups(app, monkeypatch):
    sent = []
    monkeypatch.setattr(alerts, "email_configured", lambda: True)
    monkeypatch.setattr(alerts, "send_email", lambda subj, body: (sent.append(subj) or True))
    monkeypatch.setattr(alerts, "post_webhook", lambda payload: False)
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO notifications (severity, module, title, message, created_at, alerted) "
                     "VALUES ('critical','monitoring','E2E Server down','heartbeat lost','2026-06-29',0)")
        conn.commit(); conn.close()
        n1 = alerts.dispatch_pending_alerts()
        n2 = alerts.dispatch_pending_alerts()
    assert n1 >= 1, "first pass should dispatch the new critical alert"
    assert n2 == 0, "second pass should dedup (already alerted)"
    assert any("E2E Server down" in s for s in sent)


def test_dispatch_noop_without_channels(app, monkeypatch):
    monkeypatch.setattr(alerts, "email_configured", lambda: False)
    monkeypatch.setattr(Config, "ALERT_WEBHOOK_URL", "")
    with app.app_context():
        assert alerts.dispatch_pending_alerts() == 0


def test_webhook_used_when_configured(app, monkeypatch):
    posted = []
    monkeypatch.setattr(alerts, "email_configured", lambda: False)
    monkeypatch.setattr(Config, "ALERT_WEBHOOK_URL", "https://hook.example/x")
    monkeypatch.setattr(alerts, "post_webhook", lambda payload: (posted.append(payload) or True))
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO notifications (severity, module, title, message, created_at, alerted) "
                     "VALUES ('critical','itsm','Webhook test','x','2026-06-29',0)")
        conn.commit(); conn.close()
        alerts.dispatch_pending_alerts()
    assert any(p.get("title") == "Webhook test" for p in posted)


def test_report_error_writes_log(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(Config, "SMTP_HOST", "")  # no email attempt
    try:
        raise ValueError("kaboom-xyz")
    except ValueError as e:
        alerts.report_error(e, "/some/path")
    logfile = tmp_path / "logs" / "errors.log"
    assert logfile.exists()
    text = logfile.read_text(encoding="utf-8")
    assert "kaboom-xyz" in text and "/some/path" in text
