# QA_MASTER_TEST_PLAN

## 1. Scope
The full TC Garments platform: core, Maintenance/CMMS, Procurement/Approvals, BI, Production, Admin/RBAC, Auth/SSO, Reports/Exports, Garamento AI, AI Prediction & Intelligence Center, i18n (EN/AR/TR + RTL), theming, PWA.

## 2. Objectives
Confirm the platform is functionally correct, secure, permission-enforced, data-consistent, multilingual, responsive, and production-ready before go-live.

## 3. Test types in scope
Functional, Regression, Integration, System, UAT, Security, RBAC/permission, API, Database, UI/UX, Multilingual, RTL/LTR, Browser, Mobile, Export, File-upload, Notification, PWA, Performance (smoke), Error-handling, Data-validation, Workflow, Edge, Negative, Accessibility (basic).

## 4. Entry criteria
- App boots; DB seeds; login works.
- Test env reachable; dependencies installed.

## 5. Exit criteria
- 0 open Critical/High defects.
- Automated suite green.
- Manual scenario matrix executed; Medium/Low defects triaged.

## 6. Test environment
- **Local:** Python 3.14, SQLite, `pytest`, Playwright (e2e), preview server (port 7050).
- **Cloud:** Render, PostgreSQL, gunicorn. Keys via env (`OPENROUTER_API_KEY`, `TC_SECRET_KEY`, `TC_SSO_SECRET`).

## 7. Test data strategy
Idempotent demo seed on boot (users/roles, systems, notifications, production, maintenance, procurement, AI domains) + edge-case seed (payroll anomalies, expired warranties, high-risk tickets). See QA_TEST_DATA_GUIDE. Preview uses an **isolated** `preview.db`.

## 8. Severity
- **Critical:** system down, data loss, security breach, wrong payroll, unauthorized access.
- **High:** main workflow blocked.
- **Medium:** partial/incorrect feature.
- **Low:** UI/spelling/alignment.

## 9. Priority
P1 must-fix pre-release · P2 should-fix · P3 post-release · P4 enhancement.

## 10. Execution process
1. Automated suite (`pytest`) each change. 2. Manual scenario matrix per module. 3. Log defects (QA_BUG_REPORT). 4. Fix by severity. 5. Retest + regression. 6. Sign-off checklist.

## 11. Regression strategy
Re-run full `pytest` after every fix; re-verify touched module in the browser; confirm cross-module workflows (login, RBAC, CRUD, exports, dashboards, language/theme).

## 12. Automation strategy
- Backend/unit/integration: `pytest` (287 tests).
- E2E UI: Playwright (`tests/e2e`).
- API: Flask test client + `requests`.
- Coverage: `pytest-cov` (optional).
- Runners: `run_tests.py`, `run_all_tests.bat`, `run_all_tests.sh`.

## 13. Final approval checklist
See QA_PRODUCTION_READINESS_CHECKLIST.
