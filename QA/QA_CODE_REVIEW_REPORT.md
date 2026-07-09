# QA_CODE_REVIEW_REPORT

**Method:** AST scan of all route handlers for auth decorators, grep for anti-patterns (debug flags, hardcoded secrets, string-formatted SQL, print statements, TODO/FIXME), and manual review of dynamic-SQL and permission call sites.

## Summary

The codebase is clean and modular (app-factory + blueprints, services separated from routes, dialect shim for SQLite/PostgreSQL). No critical code-quality defects. Three real issues were found and **all fixed** (see QA_BUG_REPORT).

## Findings

| # | Severity | Area | Finding | Status |
|---|---|---|---|---|
| CR-1 | Medium | config.py | `DEBUG` defaulted to `True` even when `TC_ENV=production` (would expose verbose errors). | **Fixed** — now defaults to `ENV != production`. |
| CR-2 | Low | main.py | Reports Center refactor left a stale smoke test / no user-facing link broken; contract moved to `/reports/<key>.<fmt>`. | **Fixed** — test aligned to current contract. |
| CR-3 | Low | ticket_detail.html | `data-i18n="m.approvals"` key missing → rendered raw. | **Fixed** — key added to en/ar/tr. |

## Verified NOT vulnerable / clean

- **Auth decorators:** AST scan of every route → only `service_worker` (PWA `/sw.js`, public by design) and `bi.jobs_run` (protected by admin session **or** `TC_BI_JOB_TOKEN` via constant-time compare) lack the decorator. Both correct.
- **SQL injection:** 4 dynamic-SQL sites reviewed — all build **column names from code-defined whitelists** (`_STATUS_STAMP`, fixed field lists, literal table names) with **parameterized values**. No user input reaches SQL structure.
- **Secrets:** no hardcoded API keys/passwords; all read from environment; `.env`, `.secret_key`, `.tc_sso_secret`, `*.db` gitignored.
- **Separation of concerns:** business logic lives in `app/services/*`, `app/*/services.py`, `app/ai_engine/*`; templates are presentation-only.
- **Error handling:** app-factory registers 400/403/404/413/500 handlers; DB bootstrap is non-fatal with retry.

## Observations (low priority, not blocking)

- ~11 `print()` calls remain in non-critical paths; prefer the app logger. (Enhancement.)
- No Content-Security-Policy header (X-Frame-Options / nosniff / Referrer-Policy are set). CSP is a hardening enhancement for a future pass.
- `pr_items`/some child tables rely on application-level integrity rather than DB FKs on PostgreSQL; acceptable given the service layer, tracked in DB validation report.
