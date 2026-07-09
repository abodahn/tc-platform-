# QA_REGRESSION_REPORT

**Command:** `pytest -q --ignore=tests/e2e` (unit + integration; e2e requires a live browser).
**Environment:** local, SQLite, Python 3.14, pytest 9.1.1.

## Result

| Run | Passed | Failed | Notes |
|---|---|---|---|
| Baseline (before QA fixes) | 270 | 1 | `test_smoke.py::test_csv_export` (stale URL after Reports Center refactor). |
| **After fixes** | **271** | **0** | Full green. |

271 collected items, all passing (progress: 72/72/72/55 = 100%). Exit code 0. Only warning is an unrelated urllib3/chardet version notice from the `requests` package.

## Regression coverage (existing suite, by area)
- **Auth & session:** `test_smoke.py`, `test_security_rbac.py`
- **RBAC / roles / permissions:** `test_security_rbac.py`, `test_roles.py`, `test_routes_access.py`, `test_admin_users.py`
- **CSRF:** `test_csrf_security.py`
- **Procurement / approvals:** `test_approvals.py`
- **Maintenance / CMMS:** `test_maintenance.py`
- **BI:** `test_bi_engine.py`, `test_bi_routes.py`
- **Notifications / alerts / auto-ticket:** `test_notifications_feature.py`, `test_alerts.py`, `test_auto_ticket.py`
- **SSO:** `test_sso_token.py`, `test_sso_launch.py`
- **Registry / overview:** `test_registry.py`, `test_overview.py`
- **Signature:** `test_signature.py`, `test_signature_autoprovision.py`
- **i18n:** `test_i18n.py`
- **DB dialect (SQLite↔PostgreSQL):** `test_db_dialect.py`
- **New this pass:** `tests/test_qa_intelligence.py`, `tests/test_qa_ai_features.py` (see Automation Guide).

## Confirmation
The three fixes (CSV export test contract, DEBUG default, `m.approvals` key) did not break any other module — the full suite is green. Manual re-verification in the browser confirmed the AI Intelligence Center, Alert Center, Assistant, and exports still function after the i18n rewrite.
