"""Admin-managed roles: create/edit/delete + the DB overlay over code roles."""
import pytest

from _support import login_admin, get_csrf


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "roles.db")
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True
    c = a.test_client()
    login_admin(c)
    return a, c


def test_permission_catalogue():
    from app.security import permission_catalogue
    cat = permission_catalogue()
    assert cat["Platform"] and cat["Maintenance"] and cat["Procurement"]


def test_create_custom_role(app_client):
    a, c = app_client
    r = c.post("/admin/roles/save", data={
        "_csrf": get_csrf(c), "role_key": "warehouse_lead", "label": "Warehouse Lead",
        "perms[]": ["view_dashboard", "open_module", "proc_view", "proc_approve"],
    }, follow_redirects=True)
    assert r.status_code == 200
    with a.app_context():
        from app.security import has_permission, refresh_db_roles, all_role_choices
        refresh_db_roles()
        assert has_permission("warehouse_lead", "proc_approve") is True
        assert has_permission("warehouse_lead", "manage_users") is False
        assert any(k == "warehouse_lead" for k, _ in all_role_choices())


def test_edit_builtin_role_override(app_client):
    a, c = app_client
    # give finance_user access_admin via an override
    c.post("/admin/roles/save", data={
        "_csrf": get_csrf(c), "role_key": "finance_user", "label": "Finance User",
        "perms[]": ["view_dashboard", "open_module", "access_admin"],
    }, follow_redirects=True)
    with a.app_context():
        from app.security import has_permission, refresh_db_roles
        refresh_db_roles()
        assert has_permission("finance_user", "access_admin") is True


def test_super_admin_is_protected(app_client):
    a, c = app_client
    c.post("/admin/roles/save", data={
        "_csrf": get_csrf(c), "role_key": "super_admin", "label": "Hacked",
        "perms[]": ["view_dashboard"],
    }, follow_redirects=True)
    with a.app_context():
        from app.security import has_permission
        # still all-powerful; the save was rejected
        assert has_permission("super_admin", "manage_users") is True


def test_delete_custom_role(app_client):
    a, c = app_client
    c.post("/admin/roles/save", data={
        "_csrf": get_csrf(c), "role_key": "temp_role", "label": "Temp",
        "perms[]": ["view_dashboard"],
    }, follow_redirects=True)
    c.post("/admin/roles/temp_role/delete", data={"_csrf": get_csrf(c)}, follow_redirects=True)
    with a.app_context():
        from app.security import all_role_choices, refresh_db_roles
        refresh_db_roles()
        assert not any(k == "temp_role" for k, _ in all_role_choices())


def test_delete_blocked_while_in_use(app_client):
    a, c = app_client
    from _support import ensure_user
    c.post("/admin/roles/save", data={
        "_csrf": get_csrf(c), "role_key": "used_role", "label": "Used",
        "perms[]": ["view_dashboard"],
    }, follow_redirects=True)
    ensure_user(a, "someone", "used_role")
    c.post("/admin/roles/used_role/delete", data={"_csrf": get_csrf(c)}, follow_redirects=True)
    with a.app_context():
        from app.security import all_role_choices, refresh_db_roles
        refresh_db_roles()
        # still present because a user is assigned
        assert any(k == "used_role" for k, _ in all_role_choices())
