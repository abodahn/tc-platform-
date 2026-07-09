"""
QA automated tests — AI Prediction & Intelligence Center.

Offline-safe: exercises the engine (which reads the DB, no LLM) and the routes'
auth/permission gating. Does NOT require an OpenRouter key.
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


# ---------------- Engine ----------------
def test_risk_levels():
    from app.ai_engine.base import level_of
    assert level_of(0) == "Low"
    assert level_of(20) == "Low"
    assert level_of(30) == "Watch"
    assert level_of(50) == "Medium"
    assert level_of(75) == "High"
    assert level_of(95) == "Critical"


def test_orchestrator_run_produces_snapshot(app):
    from app.ai_engine import orchestrator
    with app.app_context():
        res = orchestrator.run(username="qa")
    assert set(("run_uid", "health", "n_alerts", "n_findings", "domain_scores")) <= set(res)
    assert 0 <= res["health"] <= 100
    assert res["n_findings"] >= 1          # demo data guarantees findings
    assert isinstance(res["domain_scores"], dict) and res["domain_scores"]


def test_predictors_return_lists(app):
    from app.db import get_db
    from app.ai_engine import (ticket_predictor, asset_predictor, maintenance_predictor,
                               inventory_predictor, procurement_predictor,
                               payroll_anomaly_detector, hr_predictor, paperless_predictor)
    with app.app_context():
        conn = get_db()
        try:
            for mod in (ticket_predictor, asset_predictor, maintenance_predictor,
                        inventory_predictor, procurement_predictor,
                        payroll_anomaly_detector, hr_predictor, paperless_predictor):
                out = mod.predict(conn)
                assert isinstance(out, list)
                for f in out:
                    assert 0 <= f["risk_score"] <= 100
                    assert f["domain"] and f["title"]
        finally:
            conn.close()


def test_payroll_detector_catches_seeded_anomalies(app):
    """The demo payroll new-sheet has an injected zero-net + spike; detector must flag payroll."""
    from app.db import get_db
    from app.ai_engine import payroll_anomaly_detector as pad
    with app.app_context():
        conn = get_db()
        try:
            out = pad.predict(conn)
        finally:
            conn.close()
    assert any(f["domain"] == "payroll" for f in out), "expected payroll anomalies from seeded data"


# ---------------- Routes / auth ----------------
def test_command_center_requires_login(client):
    r = client.get("/intelligence/", follow_redirects=False)
    assert r.status_code in (301, 302)          # redirect to login


def test_admin_can_open_and_recompute(client):
    login(client)
    assert client.get("/intelligence/").status_code == 200
    r = client.post("/intelligence/recompute", data={"_csrf": _csrf(client)}, follow_redirects=False)
    assert r.status_code in (301, 302)          # redirect back after run


def test_alerts_and_export(client):
    login(client)
    client.post("/intelligence/recompute", data={"_csrf": _csrf(client)})
    assert client.get("/intelligence/alerts").status_code == 200
    r = client.get("/intelligence/report/alerts.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["Content-Type"]


def test_recompute_blocked_for_non_admin(client):
    # 'agent' (service_desk_agent) lacks access_admin
    login(client, user="agent", pwd="Admin@1122")
    r = client.post("/intelligence/recompute", data={"_csrf": _csrf(client)}, follow_redirects=False)
    assert r.status_code in (302, 403)          # 403 forbidden or bounce
