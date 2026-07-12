"""
HR Probation Management — integration tests.

Self-contained: builds the app against a throwaway SQLite DB, seeds via init_db,
and exercises access control, the workflow, scoring, scope isolation, validation
and a regression check that existing modules still respond.

Run:  pytest tests/test_probation.py -q
(These tests do not touch the real platform DB or any network.)
"""
import os
import re
import tempfile

import pytest

os.environ.setdefault("TC_ENV", "development")
os.environ.setdefault("TC_ADMIN_PASSWORD", "Test@1234")


@pytest.fixture(scope="module")
def app():
    tmp = os.path.join(tempfile.gettempdir(), "prob_pytest.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    import config as cfg
    cfg.Config.DB_PATH = tmp
    from app import create_app
    application = create_app()
    # a low-privilege user (role lacks prob_view)
    with application.app_context():
        from app.db import get_db
        from werkzeug.security import generate_password_hash
        c = get_db()
        c.execute("INSERT INTO users (username,password_hash,full_name,role,created_at) "
                  "VALUES (?,?,?,?,datetime('now'))",
                  ("low", generate_password_hash("Low@1234"), "Low", "normal_user"))
        c.commit(); c.close()
    return application


def _csrf(client):
    html = client.get("/login").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def _login(client, u, p):
    client.post("/login", data={"username": u, "password": p, "_csrf": _csrf(client), "next": "/"},
                follow_redirects=True)


def _tok(client, path="/hr/probation/"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


# -------------------------------------------------------------------- access
def test_requires_login(app):
    r = app.test_client().get("/hr/probation/")
    assert r.status_code == 302  # redirect to login


def test_permission_denied_for_low_priv(app):
    c = app.test_client(); _login(c, "low", "Low@1234")
    assert c.get("/hr/probation/").status_code == 403


def test_all_pages_render_for_admin(app):
    c = app.test_client(); _login(c, "admin", "Test@1234")
    for p in ["/", "/cases", "/mine", "/initiate", "/review", "/reminders",
              "/reports", "/import", "/settings"]:
        r = c.get("/hr/probation" + p)
        assert r.status_code == 200, p
        assert "Traceback" not in r.get_data(as_text=True)


# ------------------------------------------------------------------ workflow
def test_full_workflow_confirm(app):
    c = app.test_client(); _login(c, "admin", "Test@1234")
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        emp = conn.execute("SELECT id FROM prob_employees WHERE employee_code='TC-1004'").fetchone()["id"]
        conn.close()
    # create case
    c.post("/hr/probation/cases/new", data={"employee_id": emp, "_csrf": _tok(c, "/hr/probation/initiate")},
           follow_redirects=True)
    lst = c.get("/hr/probation/cases").get_data(as_text=True)
    cid = re.findall(r'/hr/probation/cases/(\d+)', lst)[-1]
    # submit with full scores
    tok = _tok(c, f"/hr/probation/cases/{cid}")
    form = {f"score_{k}": "4" for k in ["productivity", "work_quality", "learning_speed",
            "resource_efficiency", "attendance", "responsiveness", "cooperation", "safety_hygiene"]}
    form.update({"_csrf": tok, "manager_comments": "Good", "final_recommendation": "confirm"})
    c.post(f"/hr/probation/cases/{cid}/submit", data=form)
    # HR approve
    tok = _tok(c, f"/hr/probation/cases/{cid}")
    c.post(f"/hr/probation/cases/{cid}/decide",
           data={"decision": "approve", "final_outcome": "confirm", "_csrf": tok})
    page = c.get(f"/hr/probation/cases/{cid}").get_data(as_text=True)
    assert "Approved" in page and "Confirm Employment" in page


def test_empty_submit_is_blocked(app):
    from app.probation import services as svc
    with app.app_context():
        HR = {"id": 1, "username": "admin", "full_name": "Admin", "role": "super_admin"}
        emp = svc.list_employees(HR)
        cid, _ = svc.create_case(HR, [e for e in emp if e["employee_code"] == "TC-1003"][0]["id"])
        ok, errors = svc.submit(cid, HR)
        assert not ok and len(errors) >= 9  # 8 scores + recommendation + comments


# ------------------------------------------------------------------- scoring
def test_scoring_and_thresholds(app):
    from app.probation import services as svc
    with app.app_context():
        total, pct = svc.compute_scores({k: 4 for k in
            ["productivity", "work_quality", "learning_speed", "resource_efficiency",
             "attendance", "responsiveness", "cooperation", "safety_hygiene"]})
        assert total == 32 and pct == 80.0
        assert svc.auto_recommendation(80) == "pass"
        assert svc.auto_recommendation(65) == "hr_review"
        assert svc.auto_recommendation(40) == "not_recommended"


# --------------------------------------------------------------------- scope
def test_manager_scope_isolation(app):
    from app.probation import services as svc
    with app.app_context():
        HR = {"id": 1, "username": "admin", "role": "super_admin"}
        MGR = {"id": 9, "username": "kn", "full_name": "Khaled Nasser",
               "role": "department_manager", "scope_department": "Sewing", "scope_section": None}
        assert len(svc.list_employees(MGR)) < len(svc.list_employees(HR))
        # a manager cannot view a case outside scope
        for e in svc.list_employees(HR):
            if e["department"] != "Sewing":
                cid, _ = svc.create_case(HR, e["id"])
                case = svc.get_case_full(cid)
                assert svc.can_view_case(MGR, case) is False
                break


# --------------------------------------------------------------- regression
def test_existing_modules_still_work(app):
    c = app.test_client(); _login(c, "admin", "Test@1234")
    assert c.get("/").status_code == 200
    assert c.get("/factory/").status_code in (200, 308)
    assert c.get("/maintenance/").status_code in (200, 308)
