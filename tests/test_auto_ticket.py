"""Tests for auto-ticketing critical alerts into ITSM."""
import app.services.auto_ticket as at
from app.db import get_db
from config import Config


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


ROWS = [{"key": "itsm", "base_url": "http://itsm.fake", "is_integrated": 1, "name_en": "ITSM"}]


def _seed(app, severity, module, title="x"):
    with app.app_context():
        conn = get_db()
        conn.execute(
            "INSERT INTO notifications (severity, module, title, message, created_at) VALUES (?,?,?,?,?)",
            (severity, module, title, "msg", "2026-06-30"))
        conn.commit(); conn.close()


def test_disabled_does_nothing(app, monkeypatch):
    monkeypatch.setattr(Config, "AUTO_TICKET_ENABLED", False)
    assert at.auto_create_tickets(ROWS, force=True) == 0


def test_creates_ticket_for_monitoring_critical_and_dedups(app, monkeypatch):
    monkeypatch.setattr(Config, "AUTO_TICKET_ENABLED", True)
    monkeypatch.setattr(Config, "AUTO_TICKET_TARGET", "itsm")
    monkeypatch.setattr(Config, "AUTO_TICKET_SOURCE_MODULES", "monitoring")
    monkeypatch.setattr(Config, "AUTO_TICKET_MIN_SEVERITY", "critical")
    calls = []
    monkeypatch.setattr(at.requests, "post",
                        lambda url, timeout=None, json=None: calls.append(json) or _Resp({"ticket_no": "TCK-9001"}))
    _seed(app, "critical", "monitoring", "Server DB-01 offline")

    n1 = at.auto_create_tickets(ROWS, force=True)
    n2 = at.auto_create_tickets(ROWS, force=True)   # dedup: already ticketed
    assert n1 >= 1
    assert n2 == 0
    assert any("Server DB-01 offline" == c["title"] for c in calls)
    # the notification now carries the ticket ref
    with app.app_context():
        conn = get_db()
        ref = conn.execute("SELECT auto_ticket_ref FROM notifications WHERE title='Server DB-01 offline'").fetchone()
        conn.close()
    assert ref["auto_ticket_ref"] == "TCK-9001"


def test_ignores_wrong_module_and_low_severity(app, monkeypatch):
    monkeypatch.setattr(Config, "AUTO_TICKET_ENABLED", True)
    monkeypatch.setattr(Config, "AUTO_TICKET_TARGET", "itsm")
    monkeypatch.setattr(Config, "AUTO_TICKET_SOURCE_MODULES", "monitoring")
    monkeypatch.setattr(Config, "AUTO_TICKET_MIN_SEVERITY", "critical")
    calls = []
    monkeypatch.setattr(at.requests, "post",
                        lambda url, timeout=None, json=None: calls.append(json) or _Resp({"ticket_no": "TCK-X"}))
    _seed(app, "critical", "itsm", "ITSM own SLA breach")     # wrong source module
    _seed(app, "warning", "monitoring", "minor monitoring warn")  # below severity
    at.auto_create_tickets(ROWS, force=True)
    titles = [c["title"] for c in calls]
    assert "ITSM own SLA breach" not in titles
    assert "minor monitoring warn" not in titles
