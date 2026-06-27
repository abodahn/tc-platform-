# TC Platform — Architecture

## 1. Purpose & principles

TC Platform is the **unified shell** for T&C Garments' internal systems. It is a
deliberate **integration layer**, not a rewrite. Guiding principles:

- **Non-destructive** — never touch existing apps' files or databases.
- **Single ecosystem** — one identity, navigation, health, search, RBAC.
- **Future-ready** — modules and roles for AI, Finance, Automation, BI, Production.
- **Windows-Server friendly** — pure-Python stack, no native build steps.

## 2. Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Backend | **Flask 3** (app factory + blueprints) | Simple, robust, Windows-friendly |
| Persistence | **SQLite** (`platform.db`) | Zero-config; PostgreSQL-ready via `TC_DATABASE_URL` |
| Auth | Server-side sessions + Werkzeug password hashing | Standard, secure |
| Frontend | Server-rendered Jinja + **vanilla JS** + CSS design tokens | No build pipeline, fast, maintainable |
| Health | `requests` HTTP probes (cached) | Non-intrusive |
| HTTP API | JSON endpoints under `/api` | Live status + self-health |

## 3. Component map

```
run.py ──► app.create_app()
                │
                ├── config.Config            (env-driven)
                ├── app/db.py                 SQLite schema + seed (idempotent)
                ├── app/security.py           ROLES / PERMISSIONS + password policy
                ├── app/auth.py               current_user, login_required, permission_required
                ├── app/csrf.py               CSRF protection (token + before_request guard)
                ├── app/services/notify.py    health-driven notifications (offline -> alert)
                ├── app/navigation.py         sidebar sections (permission-gated)
                ├── app/services/health.py    cached reachability checks
                ├── app/services/seed_content.py  read-only module data
                └── app/routes/
                      auth.py   /login /logout
                      main.py   / /launcher /module/<key> /reports /health /roadmap /prefs /search
                      admin.py  /admin/* (users, integrations, audit)
                      api.py    /api/health /api/status[/<key>]
```

## 4. Data model (platform.db)

| Table | Purpose |
|-------|---------|
| `users` | username, password_hash, role, lang_pref, theme_pref, is_active |
| `systems` | integration registry: key, names (en/ar/tr), urls, port, health_url, launch_mode, criticality, is_integrated |
| `notifications` | severity, module, title, message, is_read |
| `audit_logs` | username, action, detail, ip, created_at |

> The platform DB holds **only metadata**. It never references the existing
> systems' databases.

## 5. Integration layer

Each system is a row in `systems`. A health check is a plain `GET` against
`health_url` (falling back to `base_url`):

- `online` — reachable, HTTP < 500 (login redirects still count as online)
- `warning` — reachable but HTTP 5xx
- `offline` — connection refused / timeout
- `unknown` — no URL configured

Results are cached ~15s for dashboards and fetched live on the Health page and
`/api/status` (polled every 20s by the browser).

## 6. Internationalization

UI strings live in `static/i18n/{en,ar,tr}.json` and are applied client-side via
`data-i18n` attributes. Dynamic DB content (system names/descriptions) carries
`data-loc-en/ar/tr` attributes localized in the same pass. Arabic sets
`dir="rtl"`; the CSS uses logical properties (`inset-inline`, `border-inline`)
so the whole layout mirrors automatically. Preference is saved to `localStorage`
and to the user profile (`/prefs`).

## 7. Theming

CSS custom properties in `tokens.css` define light + dark palettes
(`:root` and `:root[data-theme="dark"]`). `auto` follows the OS via
`prefers-color-scheme`. Preference is persisted like language.

## 8. Security

See `ADMIN_GUIDE.md` §Security. Summary: hashed passwords, signed HttpOnly
time-limited sessions, env-based secrets, audit logging, permission-gated menus
and routes, secure response headers, safe external-link launching
(`rel="noopener"`), internal-only login redirects.

## 9. Extensibility

- **New module:** add a row in `db.py::_seed_systems`, a nav entry in
  `navigation.py`, and (optionally) a table in `MODULE_TABLES` (main.py).
- **New role:** add to `ROLES` in `security.py`.
- **Live data adapter:** add a service under `app/services/` and surface it on
  the relevant module page (Roadmap Phase 3).
- **PostgreSQL:** implement a connection layer keyed on `TC_DATABASE_URL`.
