"""
Shared pytest fixtures for the TC Platform test suite.

Helper FUNCTIONS live in tests/_support.py (uniquely named so the import is
unambiguous despite the nested tests/e2e/conftest.py). Existing test files
(test_smoke.py, test_maintenance.py) define their own local fixtures and are
unaffected; new backend test files use the fixtures here.
"""
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# config.Config.DB_PATH defaults to <repo>/platform.db, so any suite that does
# not override it writes a database into the working tree. Repoint it at a
# per-run tempdir HERE — conftest is imported before every test module, and
# app.db reads Config.DB_PATH at connect time, so this covers the whole
# directory. Suites that set their own DB_PATH still win.
import config                      # noqa: E402

TMP_DIR = Path(tempfile.mkdtemp(prefix="tc_tests_"))
config.Config.DB_PATH = TMP_DIR / "platform.db"

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
