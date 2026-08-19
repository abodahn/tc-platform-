"""Tests for the consolidated business overview (KPIs pulled from all systems)."""
import app.services.integration as integ


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


def _reset():
    integ._CACHE["at"] = 0.0
    integ._CACHE["data"] = []
    # _LAST_GOOD is a SECOND, per-system cache: when a system is unreachable the
    # overview deliberately serves its last successful KPIs with online=False,
    # so the panel degrades to stale-but-labelled instead of going blank. Good
    # behaviour, but module-level state that outlives a test — and clearing only
    # _CACHE left it populated by whatever ran earlier. This file passed alone
    # and failed in the full suite purely on test order.
    integ._LAST_GOOD.clear()


def test_fetch_overview_pulls_and_shapes(monkeypatch):
    _reset()
    payload = {"kpis": [
        {"label": "Total Assets", "value": 900, "severity": "ok"},
        {"label": "Low Stock", "value": 3, "severity": "warn"},
    ]}
    monkeypatch.setattr(integ.requests, "get", lambda url, timeout=None: _Resp(payload))
    rows = [{"is_integrated": 1, "base_url": "http://fake", "key": "assets", "name_en": "Asset Management"}]
    out = integ.fetch_overview(rows, force=True)
    assert len(out) == 1
    e = out[0]
    assert e["online"] is True and e["key"] == "assets" and e["name"] == "Asset Management"
    assert len(e["kpis"]) == 2
    assert e["kpis"][0]["value"] == 900
    assert e["kpis"][1]["severity"] == "warn"


def test_fetch_overview_handles_down_system(monkeypatch):
    _reset()
    def boom(url, timeout=None):
        raise Exception("connection refused")
    monkeypatch.setattr(integ.requests, "get", boom)
    rows = [{"is_integrated": 1, "base_url": "http://down", "key": "itsm", "name_en": "ITSM"}]
    out = integ.fetch_overview(rows, force=True)
    assert out[0]["online"] is False and out[0]["kpis"] == []


def test_fetch_overview_skips_non_integrated(monkeypatch):
    _reset()
    calls = {"n": 0}
    def spy(url, timeout=None):
        calls["n"] += 1
        return _Resp({"kpis": []})
    monkeypatch.setattr(integ.requests, "get", spy)
    rows = [{"is_integrated": 0, "base_url": "http://x", "key": "finance", "name_en": "Finance"}]
    integ.fetch_overview(rows, force=True)
    assert calls["n"] == 0
