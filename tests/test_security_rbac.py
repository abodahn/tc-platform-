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
    # all_role_choices() merges DB-defined roles over the static ones by design
    # (the roles manager, and the five DOAM authority roles seeded with the
    # approvals schema). So the invariant is CONTAINMENT, not equality: every
    # built-in role must be offered. Asserting equality made this test depend on
    # whether an earlier test in the run had seeded a database — it passed alone
    # and failed in the full suite.
    keys = [k for k, _ in choices]
    missing = [k for k in ROLES if k not in keys]
    assert not missing, "built-in roles missing from the choices: %s" % missing
    assert len(keys) == len(set(keys)), "a role is offered twice"
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
