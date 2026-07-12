# Authentication & Account Management — Implementation Report

An additive module on the existing TC Platform: self‑service **registration**,
**email verification**, **forgot / reset password**, an **account approval
workflow**, an **admin registration console**, security controls, trilingual UI +
emails, and a full audit trail — built into the current app without changing how
existing users authenticate.

## 1. Existing architecture discovered
- **Stack:** Flask (app‑factory + blueprints), raw SQL via `app/db.py` (`_PGConn` shim; SQLite dev / PostgreSQL on Render). Jinja2 + `base.html`; design tokens in `static/css/*`; i18n = `static/i18n/{en,ar,tr}.json` + `data-i18n`.
- **Auth:** session‑based (`session["uid"]`), `werkzeug` pbkdf2 hashing, `routes/auth.py` (username login), in‑memory brute‑force lockout, global CSRF (`app/csrf.py`), secure cookies (HttpOnly/SameSite/Secure‑in‑prod) + security headers.
- **RBAC:** static `PERMISSIONS`/`ROLES` + DB `custom_roles` overlay + per‑user `extra_perms` in `app/security.py`; safest default role `normal_user`.
- **Email:** `app/services/alerts.py` SMTP via `Config.SMTP_*` (+ `PUBLIC_URL`).

## 2. Files added
- `app/accounts/__init__.py`, `constants.py` (status lifecycle, modes, token types, password policy, RBAC), `schema.py` (auth_* tables + seed), `services.py` (engine), `emails.py` (branded trilingual emails).
- `app/routes/accounts.py` (public routes).
- `app/templates/accounts/` — `_auth_base.html`, `signup.html`, `forgot.html`, `reset.html`, `verify.html`.
- `app/templates/admin_registrations.html`, `admin_registration_detail.html`.
- `app/static/js/accounts.js` (strength meter, show/hide, Caps Lock, double‑submit guard).
- `tests/test_accounts.py`; this doc; `AUTH_MODULE_IMPLEMENTATION.md`.

## 3. Files modified
- `app/db.py` — additive `users` columns (ALTER‑migration loop) + `auth_settings` in `_NO_ID_TABLES` + `create_and_seed` wiring.
- `app/security.py` — merge `users_*` permissions/roles + "Accounts / Users" group.
- `app/routes/auth.py` — login accepts **email or username**, status guidance, session epoch. `app/auth.py` — `current_user()` session‑epoch guard.
- `app/routes/admin.py` — registration‑management routes.
- `app/templates/login.html` — Create Account / Forgot Password links, show/hide, Caps Lock, blocked‑status guidance, removed the default‑admin hint.
- `app/navigation.py` — Admin → Account Registrations. `app/static/css/app.css` — auth styles. `static/i18n/*` — 141 `acc.*` keys (EN/AR/TR parity). `.env.example` — SMTP/PUBLIC_URL.

## 4. Database changes (additive, non‑destructive)
- **`users` +cols:** employee_id, mobile, company, department, job_title, location, manager, account_status(DEFAULT 'active'), email_verified_at, registration_source, approved_by/at, rejected_by/at, rejection_reason, last_password_change_at, failed_login_count, locked_until, last_login_at, terms_accepted_at, privacy_accepted_at, session_epoch. **Existing users default to `active` and keep logging in.**
- **New tables:** `auth_tokens` (SHA‑256‑hashed single‑use tokens), `auth_events` (security audit), `auth_ratelimit`, `auth_password_history`, `auth_org_options`, `auth_invites`, `auth_settings`.
- Applied by `init_db()` on boot (idempotent, isolated in try/except). Indexes on token_hash, event time/target, ratelimit bucket, org kind.

## 5. Routes / APIs added
- Public: `GET/POST /signup`, `GET /verify-email`, `GET/POST /resend-verification`, `GET/POST /forgot-password`, `GET/POST /reset-password`.
- Admin (each server‑side `@permission_required("users_*")`): `/admin/registrations`, `/admin/registrations/<id>`, `…/approve|reject|suspend|reactivate|unlock|reset|role`, `/admin/registrations/export.csv`.
- Login (`/login`) upgraded in place (email‑or‑username, status guidance).

## 6. Environment variables
`TC_SMTP_HOST, TC_SMTP_PORT, TC_SMTP_USER, TC_SMTP_PASS, TC_SMTP_FROM, TC_SMTP_TLS` (email), `TC_PUBLIC_URL` (absolute HTTPS links). Registration mode, password policy and token TTLs are **runtime settings** (`auth_settings`), editable by an admin — not env. **No secret is hardcoded**; email is a no‑op if SMTP is unset (app still runs).

## 7. Security controls implemented
Argon/pbkdf2 hashing (werkzeug); CSRF on all POST; session‑fixation reset + **session‑epoch invalidation** on reset/suspend; **enumeration prevention** (identical neutral responses for forgot/signup/duplicate); **cryptographic single‑use, expiring, SHA‑256‑hashed tokens** with old‑token invalidation; **DB‑backed rate limiting** (forgot/verify/resend) + existing login lockout; password policy (12+/classes/no‑PII/deny‑list/history) + no plaintext ever stored or logged; **safe‑redirect** validation (internal only); SSO accounts get safe guidance instead of a local reset; server‑side authorization on every admin action (never UI‑only); header‑injection‑safe emails; secure cookies + security headers (pre‑existing); no tokens/passwords/stack traces exposed.

## 8. Test results
`tests/test_accounts.py` + a full HTTP verification pass: **all green** — signup→verify→approve→login, duplicate/weak/mismatch blocked, verify invalid/reused states, forgot enumeration‑safe, reset single‑use, CSRF blocked, open‑redirect blocked, admin permission enforced (403 for non‑privileged), and **existing username login unbroken**.

## 9. Remaining limitations (documented, non‑blocking)
- Breached‑password check is an **offline deny‑list** (full HIBP k‑anonymity is a network integration point).
- In‑memory login lockout is per‑process (unchanged); the new forgot/verify limiter is DB‑backed and shared.
- Invitation‑only mode has schema + gating; an admin invite UI can be added on request.
- Terms/Privacy are acceptance checkboxes; link the hosted documents when available.

## 10. Deployment steps
1. Set `TC_SMTP_*` and `TC_PUBLIC_URL` on Render (Environment). 2. Push `render-deploy` (Render auto‑builds; `init_db()` applies the additive migrations on boot). 3. Smoke‑test `/signup` → verify → admin approve → login. 4. Confirm existing users still sign in.

## 11. Rollback procedure
The module is **additive**. To roll back: redeploy the previous commit SHA — the extra `auth_*` tables and `users` columns are harmless and unused by the old code (no data loss). If a full data restore is needed, restore the pre‑deploy PostgreSQL backup. No destructive migration is ever run.
