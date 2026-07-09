# QA_AUTOMATION_GUIDE

## Framework
- **pytest** for backend/unit/integration + Flask test client for routes/API.
- **Playwright** (`tests/e2e/`) for UI end-to-end.
- **requests/httpx** available for external API checks.
- Optional coverage via `pytest-cov`.

## Layout
```
tests/
  conftest.py            fixtures (app, client, login, seeded DB)
  _support.py            shared helpers
  test_smoke.py          boot, public routes, auth guard, RBAC, CSV export
  test_security_rbac.py  role/permission enforcement
  test_routes_access.py  per-route access matrix
  test_csrf_security.py  CSRF guard
  test_approvals.py      procurement/approval cycle
  test_maintenance.py    CMMS
  test_bi_engine.py / test_bi_routes.py   BI
  test_notifications_feature.py / test_alerts.py / test_auto_ticket.py
  test_sso_token.py / test_sso_launch.py
  test_registry.py / test_overview.py / test_admin_users.py / test_roles.py
  test_signature*.py / test_i18n.py / test_db_dialect.py
  test_qa_intelligence.py   (NEW) AI engine + Intelligence Center routes
  test_qa_ai_features.py    (NEW) Garamento KB/snapshot/offline + endpoint auth
  e2e/                   Playwright: auth, i18n/theme, mobile, notifications, easy-report
```

## Current status
**287 tests passing, 0 failing** (unit + integration; e2e run separately with a browser).

## Commands
| Goal | Command |
|---|---|
| All backend tests | `python -m pytest -q --ignore=tests/e2e` |
| One module | `python -m pytest tests/test_maintenance.py -q` |
| New QA tests | `python -m pytest tests/test_qa_intelligence.py tests/test_qa_ai_features.py -q` |
| Security-focused | `python -m pytest tests/test_security_rbac.py tests/test_csrf_security.py -q` |
| Coverage | `python -m pytest --cov=app --cov-report=html --ignore=tests/e2e` |
| E2E (needs Chrome + playwright) | `python -m pytest tests/e2e -q` |
| One-shot runners | `run_tests.py` · `run_all_tests.bat` (Win) · `run_all_tests.sh` (Linux) |

## Notes
- Tests run against SQLite; the `_PGConn` shim + `test_db_dialect.py` cover the PostgreSQL translation.
- AI/LLM-dependent behavior is tested via the **offline** path (no key needed); live LLM calls are not part of CI.
- Preview server uses an isolated `preview.db` (never the real `platform.db`).
