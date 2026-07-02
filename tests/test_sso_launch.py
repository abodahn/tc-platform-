"""Integration tests for the platform SSO IdP launch route (/sso/launch/<key>).

Verifies: unauthenticated users can't mint tokens; a logged-in user gets a
redirect to <base_url>/sso/login?token=... with a token that actually verifies
for the right audience; and graceful fallback to the raw URL when SSO is off.
"""
import urllib.parse

from app.db import get_db
from app.services import sso
from config import Config
from _support import login_admin

STRONG_SECRET = "integration-test-sso-secret-key-64-bytes-xxxxxxxxxxxxxxxxxxxx"


def _seed_system(app, key="itsm", base="https://itsm.example.com"):
    with app.app_context():
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO systems (key, name_en, base_url, is_integrated, enabled, category) "
            "VALUES (?,?,?,1,1,'itsm')", (key, "ITSM", base))
        conn.commit(); conn.close()


def test_launch_requires_login(client):
    r = client.get("/sso/launch/itsm")
    assert r.status_code in (301, 302)
    assert "/login" in r.headers.get("Location", "")


def test_launch_mints_token_and_redirects(client, app, monkeypatch):
    monkeypatch.setattr(Config, "SSO_SECRET", STRONG_SECRET)
    monkeypatch.setattr(Config, "SSO_ENABLED", True)
    monkeypatch.setattr(Config, "SSO_TOKEN_TTL", 120)
    _seed_system(app)
    login_admin(client)

    r = client.get("/sso/launch/itsm")
    assert r.status_code in (301, 302)
    loc = r.headers["Location"]
    assert loc.startswith("https://itsm.example.com/sso/login?token=")

    # the token in the redirect must verify for audience 'itsm'
    token = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["token"][0]
    claims = sso.verify_token(STRONG_SECRET, token, audience="itsm")
    assert claims["sub"] == Config.ADMIN_USER
    assert claims["role"]  # admin role present


def test_launch_fallback_when_sso_disabled(client, app, monkeypatch):
    monkeypatch.setattr(Config, "SSO_ENABLED", False)
    _seed_system(app, key="assets", base="https://assets.example.com")
    login_admin(client)
    r = client.get("/sso/launch/assets")
    assert r.status_code in (301, 302)
    # no token — degrade to the raw system URL
    assert r.headers["Location"] == "https://assets.example.com"


def test_launch_unknown_system_404(client, app):
    login_admin(client)
    r = client.get("/sso/launch/nope-not-real")
    assert r.status_code == 404
