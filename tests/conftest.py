"""
Shared pytest fixtures for the TC Platform test suite.

Helper FUNCTIONS live in tests/_support.py (uniquely named so the import is
unambiguous despite the nested tests/e2e/conftest.py). Existing test files
(test_smoke.py, test_maintenance.py) define their own local fixtures and are
unaffected; new backend test files use the fixtures here.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app          # noqa: E402


@pytest.fixture()
def app():
    a = create_app()
    a.config["TESTING"] = True
    return a


@pytest.fixture()
def client(app):
    # reset the in-memory login throttle so tests are independent
    try:
        from app.routes import auth as auth_routes
        auth_routes._fails.clear()
    except Exception:
        pass
    with app.test_client() as c:
        yield c
