"""
QA automated tests — Garamento assistant + AI feature endpoints.

Offline-safe: verifies the DB-backed knowledge base, the live-data snapshot,
graceful offline behaviour when no OpenRouter key is set, and permission gating
on the AI endpoints. No external API calls.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app          # noqa: E402
from config import Config           # noqa: E402


@pytest.fixture()
def app():
    a = create_app()
    a.config["TESTING"] = True
    from app.routes import auth as auth_routes
    auth_routes._fails.clear()
    return a


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


def _csrf(client):
    client.get("/login")
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def login(client, user="admin", pwd=None):
    return client.post("/login", data={"username": user, "password": pwd or Config.ADMIN_PASSWORD,
                                        "_csrf": _csrf(client)})


# ---------------- Knowledge base store ----------------
def test_kb_seed_and_crud(app):
    from app.services import garamento_kb as kb
    with app.app_context():
        topics = kb.list_all()
        assert len(topics) >= 1                      # seeded from built-in defaults
        key = kb.save(topic_key="", title="QA Temp Topic",
                      keywords="qa, temp, unit-test", body="1. step one\n2. step two")
        assert kb.get_one(key)["title"] == "QA Temp Topic"
        hits = kb.retrieve("qa temp")
        assert any(t["id"] == key for t in hits)
        kb.delete(key)
        assert kb.get_one(key) is None


def test_kb_active_topics_fallback(app):
    from app.services import garamento_kb as kb
    with app.app_context():
        assert len(kb.active_topics()) >= 1          # never empty (DB or built-in fallback)


# ---------------- Live-data snapshot ----------------
def test_snapshot_returns_text(app):
    from app.services import garamento_data as gd
    with app.app_context():
        snap = gd.snapshot({"username": "admin", "full_name": "Platform Administrator"})
        assert isinstance(snap, str)                 # counts block or "" — never raises


# ---------------- Graceful offline behaviour (no key) ----------------
def test_chat_offline_graceful(app, monkeypatch):
    from app.services import garamento as g
    monkeypatch.setattr(Config, "OPENROUTER_API_KEY", "", raising=False)
    with app.app_context():
        r = g.chat([{"role": "user", "content": "hi"}])
        assert r["ok"] is False and r.get("offline") and r["reply"]


def test_draft_pr_offline_graceful(app, monkeypatch):
    from app.services import garamento as g
    monkeypatch.setattr(Config, "OPENROUTER_API_KEY", "", raising=False)
    with app.app_context():
        r = g.draft_pr("need 100 zippers", departments=["Sewing"], units=["Pcs"])
        assert r["ok"] is False and r.get("offline")


# ---------------- Endpoint auth gating ----------------
def test_polish_requires_login(client):
    r = client.post("/garamento/polish", json={"text": "x", "kind": "title"})
    # login_required → redirect; or CSRF/JSON guard. Never a 200 success.
    assert r.status_code in (301, 302, 400, 401, 403)


def test_market_research_requires_permission(client):
    r = client.post("/garamento/market-research", json={"item": "x"})
    assert r.status_code in (301, 302, 400, 401, 403)


def test_hello_endpoint_authed(client):
    login(client)
    r = client.get("/garamento/hello")
    assert r.status_code == 200
    assert "enabled" in r.get_json()
