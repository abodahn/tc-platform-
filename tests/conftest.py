"""
Shared pytest fixtures for the TC Platform test suite.

Helper FUNCTIONS live in tests/_support.py (uniquely named so the import is
unambiguous despite the nested tests/e2e/conftest.py). Existing test files
(test_smoke.py, test_maintenance.py) define their own local fixtures and are
unaffected; new backend test files use the fixtures here.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The seeded demo accounts (store, factory, cfo, ceo ...) no longer carry a
# password written into the source — this repository is public, and a literal
# there was a published credential for accounts at the top of the approval
# ladder. app/db.py now reads TC_DEMO_PASSWORD and falls back to an unguessable
# random password per account. Tests that sign in as a demo user need a KNOWN
# one, so set it here, BEFORE config is imported (Config reads env at class-body
# time — setting it later has no effect, a trap that already cost this suite
# once). This value never leaves the test process.
os.environ.setdefault("TC_DEMO_PASSWORD", "Admin@1122")

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
