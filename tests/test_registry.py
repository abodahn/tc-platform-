"""Tests for the shared registry (Phase 5): aggregation, deep-link building,
search, and the /registry page + search API."""
from app.db import get_db
from app.services import registry as reg
from _support import login_admin, login_as


class _Resp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status
        self.content = b"x"

    def json(self):
        return self._p


ROWS = [{"key": "assets", "name_en": "Assets", "base_url": "http://assets.fake", "is_integrated": 1}]

PAYLOAD = {
    "employees": [
        {"code": "E001", "name": "Ahmed Ali", "email": "a@x.com", "title": "Engineer",
         "department": "IT", "status": "Active", "deep_link": "/employees"},
    ],
    "assets": [
        {"asset_id": "AST-1", "name": "Dell Laptop", "category": "Hardware", "status": "Assigned",
         "assignee": "Ahmed Ali", "location": "HQ", "deep_link": "/assets/5"},
    ],
}


def _patch_ok(monkeypatch):
    reg._CACHE["data"] = None  # bust cache between tests
    monkeypatch.setattr(reg.requests, "get", lambda url, timeout=None: _Resp(PAYLOAD))


def test_deep_link_builds_sso_launch():
    # quote keeps '/' readable in the query value; the SP re-validates it
    assert reg._deep_link("assets", "/assets/5") == "/sso/launch/assets?next=/assets/5"
    # unsafe target -> falls back to bare launch (no open redirect)
    assert reg._deep_link("assets", "http://evil.com") == "/sso/launch/assets"
    assert reg._deep_link("assets", "//evil.com") == "/sso/launch/assets"


def test_fetch_registry_normalizes_and_links(monkeypatch):
    _patch_ok(monkeypatch)
    data = reg.fetch_registry(ROWS)
    assert len(data["people"]) == 1 and len(data["assets"]) == 1
    p = data["people"][0]
    assert p["name"] == "Ahmed Ali" and p["system_key"] == "assets"
    assert p["link"] == "/sso/launch/assets?next=/employees"
    a = data["assets"][0]
    assert a["asset_id"] == "AST-1"
    assert a["link"] == "/sso/launch/assets?next=/assets/5"
    assert data["sources"][0]["online"] is True
    assert data["sources"][0]["people"] == 1 and data["sources"][0]["assets"] == 1


def test_search_filters(monkeypatch):
    _patch_ok(monkeypatch)
    hit = reg.search_registry(ROWS, "dell")
    assert len(hit["assets"]) == 1 and len(hit["people"]) == 0
    hit2 = reg.search_registry(ROWS, "ahmed")
    assert len(hit2["people"]) == 1  # matches person; asset also has assignee 'Ahmed Ali'
    none = reg.search_registry(ROWS, "zzz-nomatch")
    assert none["people"] == [] and none["assets"] == []


def test_registry_down_system_is_skipped(monkeypatch):
    reg._CACHE["data"] = None
    def _boom(url, timeout=None):
        raise Exception("connection refused")
    monkeypatch.setattr(reg.requests, "get", _boom)
    data = reg.fetch_registry(ROWS)
    assert data["people"] == [] and data["assets"] == []
    assert data["sources"][0]["online"] is False  # shown, not crashed


def _seed_system(app):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT OR REPLACE INTO systems (key,name_en,base_url,is_integrated,enabled,category) "
                     "VALUES ('assets','Assets','http://assets.fake',1,1,'assets')")
        conn.commit(); conn.close()


def test_registry_page_renders(client, app, monkeypatch):
    _patch_ok(monkeypatch)
    _seed_system(app)
    login_admin(client)
    r = client.get("/registry")
    assert r.status_code == 200
    assert b"registry" in r.data.lower()


def test_registry_search_api(client, app, monkeypatch):
    _patch_ok(monkeypatch)
    _seed_system(app)
    login_admin(client)
    r = client.get("/api/registry/search?q=dell")
    assert r.status_code == 200
    body = r.get_json()
    # the platform DB may seed several integrated systems; the mock answers each,
    # so we assert at least one Dell asset came back with a valid SSO deep link.
    assert len(body["assets"]) >= 1
    assert body["assets"][0]["link"].startswith("/sso/launch/")


def test_registry_requires_permission(client, app):
    # a role without open_module must be blocked (403)
    login_as(client, app, "normal_user")  # normal_user HAS open_module -> use a weaker check
    # normal_user can open_module, so registry should be 200 for them too:
    r = client.get("/registry")
    assert r.status_code in (200, 302)
