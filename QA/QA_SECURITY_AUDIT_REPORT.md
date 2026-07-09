# QA_SECURITY_AUDIT_REPORT

**Scope:** authentication, authorization, session, CSRF, injection, secrets, transport, uploads, sensitive data, audit.

## Result: PASS (no critical or high security defects). One hardening fix applied (DEBUG default).

| Control | Status | Evidence |
|---|---|---|
| Authentication | ✅ | Session-based (`session["uid"]`), password hashing via Werkzeug, in-memory login throttle on `auth._fails`. |
| Authorization (RBAC) | ✅ | `permission_required(perm)` on protected routes; 22 permissions / 22 roles + DB overlay + per-user extra_perms. AST scan confirms all non-public routes gated. Covered by `test_security_rbac.py`, `test_routes_access.py`, `test_roles.py`. |
| Sensitive modules (payroll/HR) | ✅ | AI Intelligence payroll/HR domains gated behind `access_admin` in breakdown, alerts and assistant. |
| CSRF | ✅ | Global `before_request` guard; form `_csrf` or `X-CSRF-Token`; safe methods + read-only/job APIs exempt; constant-time compare. Covered by `test_csrf_security.py`. |
| SQL injection | ✅ | Parameterized queries throughout; 4 dynamic-SQL sites use code-defined column/table whitelists (reviewed). |
| XSS | ✅ | Jinja auto-escaping; client renderers (Garamento, BI ask) escape via `esc()` before insertion; markdown renderer allowlists only bold/code/links (http(s) only). |
| Secrets management | ✅ | All secrets from env (`OPENROUTER_API_KEY`, `TC_SECRET_KEY`, `TC_SSO_SECRET`, SMTP, DB URL). None in source. `.env`/keys gitignored. Repo scanned: no `sk-…` or literal passwords. |
| Transport / cookies | ✅ | `SESSION_COOKIE_HTTPONLY`, `SAMESITE=Lax`, `SECURE` = production. Security headers: X-Content-Type-Options nosniff, X-Frame-Options SAMEORIGIN, Referrer-Policy. |
| Debug exposure | ✅ (fixed) | `DEBUG` now defaults OFF in production (was defaulting ON). Under gunicorn the Werkzeug debugger is not served regardless. |
| File uploads | ✅ | Extension allowlist, `MAX_CONTENT_LENGTH` cap, served auth-gated (never public dir), size guard → 413 handler. |
| Job/automation endpoints | ✅ | `bi.jobs_run` requires admin session or `TC_BI_JOB_TOKEN` (constant-time); `api.integrations_sync` credential-checked. |
| Export injection | ✅ | CSV/Excel exporters prefix `= + - @` cells to prevent formula injection. |
| Audit logging | ✅ | `audit_logs` + `mnt_audit`; admin, roles, integrations, AI recompute/alert actions logged. |
| SSO | ✅ | Short-lived signed tokens, shared secret ≥16 chars enforced, fails closed. Covered by `test_sso_token.py`, `test_sso_launch.py`. |

## Recommendations (non-blocking hardening)
1. Add a Content-Security-Policy header.
2. Rotate the OpenRouter key that was shared in chat (operational, not code).
3. Consider rate-limiting the AI/OpenRouter-backed endpoints to bound cost.
4. Move remaining `print()` to the structured logger.
