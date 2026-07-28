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
from pathlib import Path

import pytest

os.environ.setdefault("TC_ENV", "development")
os.environ.setdefault("TC_ADMIN_PASSWORD", "Test@1234")

# db.py seeds the super-admin from Config.ADMIN_*, and Config reads the env at
# class-body time (i.e. at import — which conftest.py already triggered before
# this module was imported). So take the credentials from Config rather than
# hardcoding them, or the admin logins here 302 whenever the harness exported a
# different TC_ADMIN_PASSWORD.
from config import Config as _Cfg   # noqa: E402
ADMIN_USER, ADMIN_PASSWORD = _Cfg.ADMIN_USER, _Cfg.ADMIN_PASSWORD


@pytest.fixture(scope="module")
def app():
    import config as cfg
    original_db_path = cfg.Config.DB_PATH
    # A Path, not a str: routes/main.py health() calls Config.DB_PATH.exists(),
    # so a str here 500s /health for every later test in the same process. Fresh
    # dir per run so a leftover locked file can't break collection either.
    cfg.Config.DB_PATH = Path(tempfile.mkdtemp(prefix="prob_pytest_")) / "platform.db"
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
    yield application
    # Config is process-global: leave it as we found it so the suites that run
    # after this one in the same pytest process still hit their own database.
    cfg.Config.DB_PATH = original_db_path


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
    c = app.test_client(); _login(c, ADMIN_USER, ADMIN_PASSWORD)
    for p in ["/", "/cases", "/mine", "/initiate", "/review", "/reminders",
              "/reports", "/import", "/settings"]:
        r = c.get("/hr/probation" + p)
        assert r.status_code == 200, p
        assert "Traceback" not in r.get_data(as_text=True)


# ------------------------------------------------------------------ workflow
def test_full_workflow_confirm(app):
    c = app.test_client(); _login(c, ADMIN_USER, ADMIN_PASSWORD)
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
    """The real scope rule for a line/department manager (services.can_view_case):

        visible  =  case.department == my scope_department
                 OR case.section    == my scope_section
                 OR I am the case's named direct_manager
                 OR I am the case's named section_head

    Anything else is invisible. Named-manager access is deliberate (a manager
    evaluates the people who report to them even when payroll files them under
    another department), so the isolation half has to be proven with a case that
    names SOMEONE ELSE as both manager and section head.
    """
    from app.probation import services as svc
    with app.app_context():
        HR = {"id": 1, "username": "admin", "role": "super_admin"}
        MGR = {"id": 9, "username": "kn", "full_name": "Khaled Nasser",
               "role": "department_manager", "scope_department": "Sewing", "scope_section": None}
        everyone = svc.list_employees(HR)
        mine = svc.list_employees(MGR)
        assert len(mine) < len(everyone)
        # the manager is a manager, not HR/admin/report-viewer — otherwise the
        # scope branches below are never even reached
        assert svc.is_hr(MGR) is False and svc.is_admin(MGR) is False
        assert svc.can_report(MGR) is False

        by_code = {e["employee_code"]: e for e in everyone}

        def case_for(code):
            cid, err = svc.create_case(HR, by_code[code]["id"])
            assert cid and not err, (code, err)
            return svc.get_case_full(cid)

        # (1) in my department -> visible
        own = case_for("TC-1001")
        assert own["department"] == MGR["scope_department"]
        assert svc.can_view_case(MGR, own) is True

        # (2) another department, but I am the named direct_manager -> visible
        reports_to_me = case_for("TC-1003")
        assert reports_to_me["department"] != MGR["scope_department"]
        assert reports_to_me["direct_manager"] == MGR["full_name"]
        assert svc.can_view_case(MGR, reports_to_me) is True

        # (3) another department, I am the named section_head -> visible
        under_my_section = dict(reports_to_me, department="Finishing", section="Packing",
                                direct_manager="Mona Sabry", section_head=MGR["full_name"])
        assert svc.can_view_case(MGR, under_my_section) is True

        # (4) THE ISOLATION HALF — the assertion with the security value.
        # Another department, another named manager, another named section head:
        # this manager must NOT see the case, and it must never appear in their
        # employee list either. Do not relax this.
        theirs = case_for("TC-1002")
        assert theirs["department"] != MGR["scope_department"]
        assert theirs["section"] not in (MGR["scope_section"], MGR["scope_department"])
        assert theirs["direct_manager"] != MGR["full_name"]
        assert theirs["section_head"] != MGR["full_name"]
        assert svc.can_view_case(MGR, theirs) is False
        assert by_code["TC-1002"]["id"] not in {e["id"] for e in mine}

        # (4b) the scope_section leg, both directions: a manager scoped to a
        # SECTION (no department, name matches nobody) sees the case in that
        # section and only that one. No seed row needed — can_view_case reads
        # department/section/direct_manager/section_head and nothing else.
        SEC_MGR = dict(MGR, id=11, username="sm", full_name="Nobody At All",
                       scope_department=None, scope_section=theirs["section"])
        assert own["section"] != theirs["section"]
        assert svc.can_view_case(SEC_MGR, theirs) is True
        assert svc.can_view_case(SEC_MGR, own) is False

        # (5) a manager whose name matches nothing and whose scope matches
        # nothing sees nothing at all
        OUTSIDER = dict(MGR, id=10, username="zz", full_name="Nobody At All",
                        scope_department="Warehouse", scope_section=None)
        for case in (own, reports_to_me, under_my_section, theirs):
            assert svc.can_view_case(OUTSIDER, case) is False


# --------------------------------------------------------------- regression
def test_existing_modules_still_work(app):
    c = app.test_client(); _login(c, ADMIN_USER, ADMIN_PASSWORD)
    assert c.get("/").status_code == 200
    assert c.get("/factory/").status_code in (200, 308)
    assert c.get("/maintenance/").status_code in (200, 308)
