# QA_PRODUCTION_READINESS_CHECKLIST

| # | Item | Status | Note |
|---|---|---|---|
| 1 | Debug mode disabled in production | ✅ Fixed | `DEBUG` now defaults off when `TC_ENV=production`; also ensure `TC_ENV=production` is set on Render. |
| 2 | Secrets out of source | ✅ | All from env; repo scanned clean; `.env`/keys gitignored. |
| 3 | Default/demo passwords | ⚠️ Action | Demo accounts share `Admin@1122`. **Change or disable demo accounts before real users**; set a strong `TC_ADMIN_PASSWORD`. |
| 4 | Admin account secured | ⚠️ Action | Set unique strong admin password via `TC_ADMIN_PASSWORD`. |
| 5 | Stable secret key | ✅ | Set `TC_SECRET_KEY` on Render so sessions survive redeploys. |
| 6 | Database backup | ◑ | `BACKUP_LOOP.ps1` local; wire a Render/managed-DB backup schedule. |
| 7 | Error logging | ✅ | Handlers + optional Sentry (`TC_SENTRY_DSN`). |
| 8 | Audit logging | ✅ | `audit_logs`, `mnt_audit`, `pr_events`, AI actions. |
| 9 | File-upload folder secured | ✅ | Served auth-gated; size + extension limits. |
| 10 | Permissions verified | ✅ | RBAC tests green; AST route scan clean. |
| 11 | Payroll/HR restricted | ✅ | Admin-only in AI Center. |
| 12 | Exports tested | ✅ | CSV/Excel/PDF return 200 + correct type. |
| 13 | Reports tested | ✅ | Reports Center + module reports. |
| 14 | Performance acceptable | ◑ | Smoke-level OK; add composite indexes before large data. |
| 15 | Mobile tested | ✅ | Responsive grids + e2e mobile test. |
| 16 | Browser tested | ◑ | Chromium via Playwright; verify Edge/Firefox for the target org. |
| 17 | Automated tests executed | ✅ | 287 passing. |
| 18 | Critical bugs fixed | ✅ | 0 critical found. |
| 19 | High bugs fixed | ✅ | 1 (BUG-001) fixed. |
| 20 | User guide available | ✅ | In-app Garamento manual + `docs/`. |
| 21 | Admin guide available | ✅ | `docs/ADMIN_GUIDE.md`. |
| 22 | AI key configured (optional) | ◑ | `OPENROUTER_API_KEY` set → AI features online; else graceful offline. Rotate the exposed key. |
| 23 | SSO secret configured | ◑ | Set matching `TC_SSO_SECRET` on platform + 4 systems to enable SSO. |
| 24 | Keep-alive | ✅ | GitHub Action pings `/api/health`. |

**Legend:** ✅ done · ◑ partial/monitor · ⚠️ action required before go-live.

## Must-do before real users
1. Set `TC_ENV=production`, strong `TC_ADMIN_PASSWORD`, `TC_SECRET_KEY` on Render.
2. Change/disable the shared-password demo accounts.
3. Schedule DB backups.
4. Rotate the OpenRouter key.
