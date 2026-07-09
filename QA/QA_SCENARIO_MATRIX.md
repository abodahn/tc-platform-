# QA_SCENARIO_MATRIX

Happy / Bad / Edge scenarios per workflow (manual + automated coverage).

| Workflow | Happy | Bad | Edge |
|---|---|---|---|
| **Login** | Valid creds → dashboard | Wrong pwd, empty fields, disabled user, throttle after repeats | Session timeout, direct-URL access, role redirect |
| **Create user** | Valid user + role created | Duplicate username, invalid email, missing fields | Very long name, Arabic/Turkish name, special chars |
| **RBAC** | Admin sees all | Non-admin hits restricted route → 403 | Custom role + per-user extra_perms boundary |
| **Maintenance ticket** | Create → assign → resolve → close | Missing description, invalid status transition, invalid attachment | Reopen closed, machine missing, huge downtime |
| **Spare issue** | Approve → issue → stock down | Reject request, issue over stock | Negative qty blocked, stock hits min → stockout alert |
| **Purchase request** | Fill → submit → approve ladder → PO | Missing title, reject w/o reason, duplicate | 48k threshold (6 signers), 0 total, huge total, delegation active |
| **Payroll validation** | Old+new match → clean | Broken/mismatched rows | Zero net, duplicate code, missing employee, ±25% variance, deduction spike |
| **HR probation** | Complete evaluation → approve | Incomplete/missing feedback | Overdue due-date, score < pass, failed status |
| **BI upload** | CSV/XLSX → dashboard | Unsupported type, empty file | Huge file, weird headers, single column, all-null column |
| **Reports export** | Filtered export downloads | Export with no permission | Empty result set, large export, special chars in cells |
| **AI recompute** | Snapshot + alerts + health | Non-admin recompute → blocked | Empty domains, all-critical data, no OpenRouter key (assistant fallback) |
| **Multilingual** | EN/AR/TR switch | — | RTL alignment, missing-key detection, long translated labels overflow |
| **Theme/PWA** | Light/dark/auto persist | — | Mobile viewport, offline service worker |
| **CSRF/Security** | Valid token POST | Missing token → 400, SQLi/XSS payloads neutralized | Direct API/object access, job endpoint without token |

**Edge input dictionary:** empty DB, boundary values (0, negatives, max), long text, Arabic/Turkish/special characters, expired dates, future dates, invalid dates, duplicate IDs, concurrent refresh, direct URLs, wrong button order.
