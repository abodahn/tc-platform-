"""Unit tests for the RBAC model and password policy (app/security.py)."""
import pytest

from app.security import (ROLES, PERMISSIONS, has_permission, validate_password,
                          role_label, all_role_choices, DEFAULT_ROLE)


def test_every_role_grants_only_known_permissions():
    for key, spec in ROLES.items():
        perms = spec.get("perms") or []
        assert perms, f"role {key} has no permissions"
        for p in perms:
            assert p == "*" or p in PERMISSIONS, f"role {key} references unknown permission {p!r}"


def test_super_admin_has_everything():
    for p in PERMISSIONS:
        assert has_permission("super_admin", p), f"super_admin missing {p}"


def test_normal_user_is_limited():
    assert has_permission("normal_user", "view_dashboard")
    assert has_permission("normal_user", "open_module")
    assert not has_permission("normal_user", "manage_users")
    assert not has_permission("normal_user", "access_admin")


def test_unknown_role_is_denied_everything():
    for p in PERMISSIONS:
        assert not has_permission("does_not_exist", p)


def test_default_role_exists():
    assert DEFAULT_ROLE in ROLES


def test_role_labels_and_choices():
    for key in ROLES:
        assert role_label(key)
    choices = all_role_choices()
    assert len(choices) == len(ROLES)
    assert all(isinstance(c, tuple) and len(c) == 2 for c in choices)


@pytest.mark.parametrize("pw,ok", [
    ("abcd1234", True),     # 8 chars, letter+digit
    ("Strong1Pass", True),
    ("short1", False),      # too short
    ("allletters", False),  # no digit
    ("12345678", False),    # no letter
    ("", False),
])
def test_password_policy(pw, ok):
    assert validate_password(pw)[0] is ok
