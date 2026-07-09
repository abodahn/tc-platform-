# QA — TC Garments Platform

360° quality pack. All findings are grounded in real inspection + the automated
suite (**287 tests passing, 0 failing**). 3 bugs found, 3 fixed.

## Documents
| File | Purpose |
|---|---|
| QA_FINAL_EXECUTIVE_REPORT.md | Start here — verdict + numbers |
| QA_PLATFORM_DISCOVERY_REPORT.md | Full platform map (routes/tables/roles) |
| QA_BUSINESS_WORKFLOW_MAP.md | End-to-end business cycles |
| QA_MASTER_TEST_PLAN.md | Strategy, scope, criteria |
| QA_TEST_CASE_LIBRARY.md / .xlsx | 71 test cases (48 automated) |
| QA_SCENARIO_MATRIX.md | Happy / Bad / Edge per workflow |
| QA_AUTOMATION_GUIDE.md | How the automated framework is run |
| QA_TEST_DATA_GUIDE.md | Seed + edge data |
| QA_CODE_REVIEW_REPORT.md | Code quality findings |
| QA_DATABASE_VALIDATION_REPORT.md | Schema / dual-engine validation |
| QA_UI_UX_CHARACTER_REVIEW.md | i18n/RTL/UI review |
| QA_SECURITY_AUDIT_REPORT.md | Security controls audit |
| QA_BUG_REPORT.md / .xlsx | Defects |
| QA_FIX_PLAN.md | Fix order + follow-ups |
| QA_REGRESSION_REPORT.md | Before/after test results |
| QA_PRODUCTION_READINESS_CHECKLIST.md | Go-live checklist |
| QA_EXECUTION_TRACKER.xlsx | Per-case execution status |

## Run the tests
```
pip install -r requirements-test.txt
python run_tests.py            # all backend tests
python run_tests.py qa         # new AI/Intelligence QA suites
python run_tests.py cov        # + HTML coverage
run_all_tests.bat              # Windows one-shot
./run_all_tests.sh             # Linux/macOS one-shot
```
