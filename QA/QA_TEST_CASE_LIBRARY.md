# QA_TEST_CASE_LIBRARY

71 representative test cases across all modules. Full set + status also in QA_TEST_CASE_LIBRARY.xlsx. Cases marked Automated=Yes are covered by the pytest suite (287 passing); Manual cases are executed via the browser using QA_SCENARIO_MATRIX.

| ID | Module | Feature | Type | Expected | Automated |
|---|---|---|---|---|---|
| TC-AUTH-0001 | Auth | Login | Happy | Redirect to dashboard; session set | Yes |
| TC-AUTH-0002 | Auth | Login | Negative | Rejected; error message; no session | Yes |
| TC-AUTH-0003 | Auth | Login | Negative | Validation error | Yes |
| TC-AUTH-0004 | Auth | Login throttle | Security | Throttled / locked temporarily | Yes |
| TC-AUTH-0005 | Auth | Auth guard | Security | Redirect to login | Yes |
| TC-AUTH-0006 | Auth | Logout | Happy | Session cleared; back to login | Yes |
| TC-AUTH-0007 | Auth | Role redirect | Permission | 403 / bounce | Yes |
| TC-ADMIN-0001 | Admin | Create user | Happy | User created; appears in list | Yes |
| TC-ADMIN-0002 | Admin | Duplicate username | Negative | Rejected | Manual |
| TC-ADMIN-0003 | Admin | Roles editor | Happy | Persisted; RBAC reflects change | Yes |
| TC-ADMIN-0004 | Admin | Per-user extra perms | Permission | User gains access | Yes |
| TC-ADMIN-0005 | Admin | Audit log | Security | Entry in audit_logs | Yes |
| TC-SEC-0001 | Security | CSRF | Security | 400 / rejected | Yes |
| TC-SEC-0002 | Security | SQL injection | Security | No injection; parameterized | Manual |
| TC-SEC-0003 | Security | XSS | Security | Escaped on render | Manual |
| TC-SEC-0004 | Security | Payroll/HR restriction | Permission | Sensitive rows hidden | Yes |
| TC-SEC-0005 | Security | Job endpoint | Security | 403 | Manual |
| TC-ITSM-0001 | Maintenance | Create ticket | Happy | Ticket created; on board | Yes |
| TC-ITSM-0002 | Maintenance | Missing required | Negative | Validation error | Manual |
| TC-ITSM-0003 | Maintenance | Assign | Happy | Assignee saved | Manual |
| TC-ITSM-0004 | Maintenance | Status transition | Workflow | Valid transitions only | Yes |
| TC-ITSM-0005 | Maintenance | Invalid transition | Negative | Rejected unless force | Yes |
| TC-ITSM-0006 | Maintenance | AI triage | Happy | Priority + causes + parts | Manual |
| TC-ITSM-0007 | Maintenance | Reports export | Export | CSV/Excel downloads | Manual |
| TC-ASSET-0001 | Assets | Asset risk scoring | Edge | Flags EOL/warranty | Yes |
| TC-INV-0001 | Inventory | Low stock detection | Happy | Stockout/reorder finding | Yes |
| TC-INV-0002 | Inventory | Overstock | Edge | Overstock finding | Yes |
| TC-PROC-0001 | Procurement | Create PR | Happy | PR pending; ladder built | Yes |
| TC-PROC-0002 | Procurement | Missing field | Negative | Validation error | Manual |
| TC-PROC-0003 | Procurement | Approval ladder | Workflow | Correct signer sequence | Yes |
| TC-PROC-0004 | Procurement | Reject w/ reason | Negative | Status rejected; reason stored | Yes |
| TC-PROC-0005 | Procurement | Responsibility matrix | Workflow | Route uses matrix | Yes |
| TC-PROC-0006 | Procurement | Market research | Happy | Approx price + sources | Manual |
| TC-PROC-0007 | Procurement | Draft from text | Happy | Form filled | Manual |
| TC-PROC-0008 | Procurement | Approval delay risk | Edge | Approval-delay alert | Yes |
| TC-PAY-0001 | Payroll | Old vs new compare | Happy | Diffs detected | Yes |
| TC-PAY-0002 | Payroll | Zero-net anomaly | Edge | Flagged critical | Yes |
| TC-PAY-0003 | Payroll | Duplicate employee | Negative | Duplicate flagged | Yes |
| TC-PAY-0004 | Payroll | Missing employee | Edge | Missing flagged | Yes |
| TC-PAY-0005 | Payroll | Access restriction | Permission | Hidden | Yes |
| TC-HR-0001 | HR | Overdue evaluation | Edge | Overdue flagged | Yes |
| TC-HR-0002 | HR | Low score risk | Edge | Failing risk flagged | Yes |
| TC-PAPER-0001 | Paperless | Consumption spike | Edge | Spike flagged + saving est | Yes |
| TC-AI-0001 | AI Center | Recompute | Happy | Snapshot written; health/alerts | Yes |
| TC-AI-0002 | AI Center | Risk levels | Edge | Correct bands | Yes |
| TC-AI-0003 | AI Center | Alert status flow | Workflow | Status persists; audit | Manual |
| TC-AI-0004 | AI Center | Feedback loop | Happy | Stored; resolves if marked | Manual |
| TC-AI-0005 | AI Center | Assistant (offline) | Negative | Rule-based fallback answer | Manual |
| TC-AI-0006 | AI Center | Export report | Export | Valid files 200 | Yes |
| TC-BI-0001 | BI | Upload file | Happy | Dataset + dashboard | Yes |
| TC-BI-0002 | BI | Ask your data | Happy | Answer + chart suggestion | Manual |
| TC-BI-0003 | BI | Alerts→bell | Workflow | Breach → notification | Yes |
| TC-REPORT-0001 | Reports | Filter + preview | Happy | Rows shown | Manual |
| TC-REPORT-0002 | Reports | Export CSV | Export | 200 text/csv; header Key,Name | Yes |
| TC-REPORT-0003 | Reports | Export = DB parity | Database | Identical rows | Manual |
| TC-REPORT-0004 | Reports | Empty report | Edge | Graceful empty state | Manual |
| TC-DASH-0001 | Dashboard | KPI accuracy | Happy | KPIs match DB counts | Manual |
| TC-DASH-0002 | Dashboard | Empty data | Edge | No crash; zero states | Manual |
| TC-I18N-0001 | Multilingual | EN labels | Happy | LTR; English text | Yes |
| TC-I18N-0002 | Multilingual | AR labels + RTL | Happy | RTL; Arabic text | Yes |
| TC-I18N-0003 | Multilingual | TR labels | Happy | Turkish text | Yes |
| TC-I18N-0004 | Multilingual | Missing key detection | Edge | 0 missing keys | Yes |
| TC-UI-0001 | UI/UX | Theme switch | Happy | Persisted; readable | Manual |
| TC-UI-0002 | UI/UX | Mobile responsive | Happy | No overflow; usable | Yes |
| TC-UI-0003 | UI/UX | Empty/loading/error states | Edge | Proper messaging | Manual |
| TC-DB-0001 | Database | Dialect translation | Database | Same behavior | Yes |
| TC-DB-0002 | Database | Idempotent schema | Database | No duplicate/err | Yes |
| TC-DB-0003 | Database | executemany on PG | Database | Rows inserted | Yes |
| TC-API-0001 | API | Health | Happy | 200 JSON ok | Yes |
| TC-API-0002 | API | Status auth | Security | Gated | Manual |
| TC-SSO-0001 | SSO | Token issue+verify | Security | Valid short-lived token | Yes |
