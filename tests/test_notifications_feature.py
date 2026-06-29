"""Tests for the notification feed, mark-read, and the 4-system aggregation."""
import app.services.notify as notify
from app.db import get_db
from _support import login_admin, get_csrf


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


def test_feed_shape(client, app, monkeypatch):
    # don't hit the real LAN systems during the test
    monkeypatch.setattr("app.routes.main.sync_system_notifications", lambda *a, **k: 0)
    login_admin(client)
    r = client.get("/notifications/feed")
    assert r.status_code == 200
    j = r.get_json()
    assert {"unread", "max_id", "items"}.issubset(j.keys())
    assert isinstance(j["items"], list)
    if j["items"]:
        assert {"id", "severity", "title", "message", "created_at", "is_read"}.issubset(j["items"][0].keys())


def test_mark_one_then_all_read(client, app, monkeypatch):
    monkeypatch.setattr("app.routes.main.sync_system_notifications", lambda *a, **k: 0)
    login_admin(client)
    tok = get_csrf(client)
    r1 = client.post("/notifications/read", json={"id": 1}, headers={"X-CSRF-Token": tok})
    assert r1.status_code == 200 and r1.get_json().get("ok") is True
    r2 = client.post("/notifications/read", headers={"X-CSRF-Token": tok})
    assert r2.status_code == 200
    r3 = client.get("/notifications/feed")
    assert r3.get_json()["unread"] == 0


def test_aggregation_pulls_tags_and_dedups(app, monkeypatch):
    payload = {"items": [
        {"id": 7771, "severity": "critical", "title": "SLA breach", "message": "x", "created_at": "2026-06-29"},
        {"id": 7772, "severity": "warning", "title": "New ticket", "message": "y", "created_at": "2026-06-29"},
    ]}
    monkeypatch.setattr(notify.requests, "get", lambda url, timeout=None: _Resp(payload))
    rows = [{"is_integrated": 1, "base_url": "http://fake.local", "key": "itsm", "name_en": "IT Service Desk"}]
    with app.app_context():
        conn = get_db()
        conn.execute("DELETE FROM notifications WHERE ext_key IN ('itsm:7771','itsm:7772')")
        conn.commit(); conn.close()
        n1 = notify.sync_system_notifications(rows, force=True)
        n2 = notify.sync_system_notifications(rows, force=True)  # second pull = dedup
        conn = get_db()
        got = conn.execute("SELECT module, title, severity, ext_key FROM notifications "
                           "WHERE ext_key IN ('itsm:7771','itsm:7772')").fetchall()
        conn.close()
    assert n1 == 2, "first pull should insert both"
    assert n2 == 0, "second pull should dedup to zero"
    assert {g["ext_key"] for g in got} == {"itsm:7771", "itsm:7772"}
    assert all(g["module"] == "itsm" for g in got)
    assert any(g["title"].startswith("[IT Service Desk]") for g in got), "source system must be tagged in title"


def test_aggregation_skips_unreachable_system(app, monkeypatch):
    def boom(url, timeout=None):
        raise Exception("connection refused")
    monkeypatch.setattr(notify.requests, "get", boom)
    rows = [{"is_integrated": 1, "base_url": "http://down.local", "key": "assets", "name_en": "Asset"}]
    with app.app_context():
        n = notify.sync_system_notifications(rows, force=True)
    assert n == 0  # graceful skip, no exception


def test_aggregation_ignores_non_integrated(app, monkeypatch):
    called = {"n": 0}

    def spy(url, timeout=None):
        called["n"] += 1
        return _Resp({"items": []})
    monkeypatch.setattr(notify.requests, "get", spy)
    rows = [{"is_integrated": 0, "base_url": "http://x", "key": "finance", "name_en": "Finance"}]
    with app.app_context():
        notify.sync_system_notifications(rows, force=True)
    assert called["n"] == 0, "non-integrated systems must not be polled"
