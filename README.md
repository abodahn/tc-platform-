# TC Platform
### T&C Digital Operations Platform
**Unified Digital Operations, IT, AI & Business Control Center**

A premium, trilingual (English / العربية / Türkçe), executive-grade internal
portal that unifies all T&C Garments internal systems — Service Desk, Asset
Management, Server Monitoring and Work Management — into one secure ecosystem,
with a roadmap for AI, Finance, Automation, BI, Production and more.

> TC Platform is a **safe integration layer**. It does **not** modify, move or
> reset any existing system or database. Each existing app keeps running on its
> own port; TC Platform adds unified branding, navigation, health, search,
> notifications, RBAC and an executive command center on top.

---

## 1. Features

- **Executive Command Center** — animated KPIs, SLA ring, transformation
  progress, system-health cards, "Today's Focus" and "Critical Attention".
- **Embedded apps (platform concept)** — opening ITSM, Assets, Monitoring or
  CommandTrack loads the **full existing app *inside* the platform shell** (iframe),
  with Refresh / Full-screen / Details controls and an offline retry panel.
- **Application Launcher** — premium cards for every system with live
  online/offline status, port, owner and deep links.
- **Live health checks** — non-intrusive HTTP checks of every integrated system
  (cached on the dashboard, live on the Health page and `/api/status`).
- **Trilingual UI** — English, Arabic (full RTL) and Turkish. Saved per user.
- **Light / Dark / Auto themes** — saved per user.
- **RBAC** — 12 roles, permission-driven menu visibility and route guards.
- **Admin Center** — manage users, edit each system's URL/port/launch-mode,
  review audit logs.
- **Notifications, global search, collapsible sidebar, user menu.**
- **Production Visibility** — a **real working module**: add/edit/delete production
  lines, downtime events and quality issues (persisted to the DB), with a live KPI
  dashboard and role-based editing (`manage_production`).
- **Maintenance & Spare Parts (CMMS/EAM)** — a **full paperless maintenance module**:
  tickets/work orders, machine registry, spare-parts inventory, an atomic
  spare-issue transaction (stock can't go negative), configurable approval matrix,
  preventive maintenance, QR codes, stock movements, audit trail, CSV reports and a
  KPI dashboard — trilingual + RTL, role-based. See
  [`docs/MAINTENANCE_MODULE.md`](docs/MAINTENANCE_MODULE.md). Demo roles
  (`maint`/`tech`/`store`/`supervisor`/`factory`, password `Tc@12345`). Includes a
  phone-first **Floor** view, **QR scan-to-report**, camera photo proof, an installable
  **PWA**, and an **offline AI** predictive-maintenance engine (failure-risk, reorder
  forecasts, root-cause assist) that runs fully on-premise — no cloud, no internet.
- **Reports, Governance, Roadmap, Health** pages and 11 further future-ready modules.

---

## 2. Requirements

- **Python 3.12+** (tested on 3.14, Windows 11 / Windows Server)
- Windows PowerShell (scripts provided) — or any OS with Python

---

## 3. Installation

```powershell
cd "tc-platform"
.\scripts\install_requirements.ps1
```

This creates a `.venv`, installs dependencies, and copies `.env.example` to
`.env`. **Edit `.env`** and set a strong `TC_SECRET_KEY` and `TC_ADMIN_PASSWORD`.

Manual alternative:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

---

## 4. Running

### Option A — platform only (double-click)
**`RUN_TC_PLATFORM.bat`** — starts just the platform and opens the browser at
**http://127.0.0.1:7000**. Integrated apps will show *Offline* until they are running.

### Option B — everything (double-click) ⭐ recommended
**`RUN_ALL_SYSTEMS.bat`** — starts the platform **and all four existing systems**
together on the unified ports, then opens the platform. The apps run **hidden in
the background** (no five popup windows); instead a **single combined log window**
opens showing every system's output, colour-coded by system. Now the modules load
**embedded inside** the platform and show *Online*.

| System | URL |
|--------|-----|
| TC Platform | http://127.0.0.1:7000 |
| ITSM Service Desk | http://127.0.0.1:5000 |
| Asset Management | http://127.0.0.1:5001 |
| Server Monitoring | http://127.0.0.1:5002 |
| CommandTrack | http://127.0.0.1:5003 |

Stop everything: `.\scripts\stop_all_systems.ps1` (closing the log window does **not**
stop the apps — they run in the background; use this script to stop them).
Live logs are written to `tc-platform\logs\` and streamed in the combined window.

### Option C — command line (platform only)
```powershell
.\scripts\start_platform.ps1     # http://127.0.0.1:7000  (Ctrl+C to stop)
```

> The "run everything" launcher installs each app's dependencies into your system
> Python on first run and starts every app **non-destructively** (each uses
> create-if-not-exists on its own database — no data is reset). Existing app
> `.venv` folders created on another machine are ignored.

### Default login
| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `Admin@12345` (or whatever you set in `.env`) |

Demo accounts (password `Tc@12345`): `director` (IT Director),
`agent` (Service Desk Agent), `exec` (Executive Viewer) — use these to see
role-based menu visibility.

---

## 5. Configuring the integrated systems

TC Platform stores each system's URL/port in its own database and the
**Admin Center → Integrations** tab lets you edit them in the browser
(no code changes). Seeded defaults follow the recommended deployment plan:

| System | Seeded URL | Health endpoint |
|--------|-----------|-----------------|
| ITSM Service Desk | `http://127.0.0.1:5000` | `/health` |
| Asset Management | `http://127.0.0.1:5001` | `/api/health` |
| Server Monitoring | `http://127.0.0.1:5002` | `/health` |
| CommandTrack Work | `http://127.0.0.1:5003` | `/` |

> **Important — actual app defaults differ.** Out of the box the existing apps
> run on: ITSM `5000`, Asset `5000`, Monitoring `8000`, CommandTrack `8080`.
> Either change each app's port to match the plan above, **or** update the URLs
> in Admin Center → Integrations. See [`docs/INTEGRATION_MAP.md`](docs/INTEGRATION_MAP.md).

### Changing URLs / ports
- **In the UI:** Admin Center → Integrations → edit Base URL / Port / Health URL → Save.
- **Default host:** set `TC_INTEGRATION_HOST` in `.env` (e.g. `10.100.1.13`)
  *before first run* to seed production URLs automatically.

---

## 6. Production deployment (recommended)

| Item | Value |
|------|-------|
| Server IP | `10.100.1.13` |
| TC Platform | `http://10.100.1.13` (port 80) or `http://TCPlatform` |
| ITSM | `http://10.100.1.13:5000` |
| Asset Management | `http://10.100.1.13:5001` |
| Monitoring | `http://10.100.1.13:5002` |
| CommandTrack | `http://10.100.1.13:5003` |

Steps:
1. Set `TC_ENV=production`, a strong `TC_SECRET_KEY`, `TC_PORT=80`,
   `TC_INTEGRATION_HOST=10.100.1.13`, `TC_DEBUG=false` in `.env`.
2. Run with the bundled **waitress** entry (not the dev server), behind
   **IIS / nginx** for TLS:
   ```powershell
   .\scripts\serve_production.ps1          # sets TC_ENV=production, runs serve.py
   # or:  $env:TC_ENV="production"; python serve.py
   ```
3. Optional local hostnames (Windows `hosts` file or internal DNS):
   `TCPlatform.local`, `ITSM.local`, `ASM.local`, `MON.local`, `CommandTrack.local`.

---

## 7. Health & monitoring

- In-app: **System Health** page (sidebar) and `GET /api/health` / `GET /api/status`.
- From the shell: `.\scripts\health_check.ps1` pings the platform and all systems.

---

## 8. Backups

- **Platform DB:** `.\scripts\backup_platform.ps1` → timestamped copy under `backups/`.
- **Existing systems (before any change):** `.\scripts\backup_existing_projects.ps1`
  copies all four projects (excluding `.venv`/`__pycache__`) into a timestamped
  `_BACKUP_existing_systems_*` folder one level above. **Non-destructive.**

---

## 9. Testing

```powershell
pip install pytest
pytest                       # 17 backend smoke tests

# UI screenshots (optional)
pip install playwright
python -m playwright install chromium
python tests\capture_screenshots.py   # writes to screenshots/
```

See [`docs/TEST_CHECKLIST.md`](docs/TEST_CHECKLIST.md) for the manual acceptance checklist.

---

## 10. Project structure

```
tc-platform/
  run.py                  entry point
  config.py               env-driven configuration
  requirements.txt
  .env.example
  platform.db             SQLite metadata (created on first run)
  app/
    __init__.py           app factory + security headers + error handlers
    db.py                 schema + seed (users, systems, notifications, audit)
    auth.py               login/permission decorators
    security.py           roles & permissions (RBAC)
    navigation.py         sidebar definition
    routes/               auth, main, admin, api blueprints
    services/             health checks + module seed content
    templates/            base, login, dashboard, launcher, modules, admin…
    static/               css (tokens+app), js, i18n (en/ar/tr), img, icons
  scripts/                install / start / stop / health / backup (PowerShell)
  docs/                   architecture, install, admin, user, integration, roadmap
  tests/                  pytest smoke tests + Playwright capture
```

---

## 11. Troubleshooting

| Symptom | Fix |
|--------|-----|
| Systems show **Offline** | The target app isn't running, or its URL/port is wrong. Start the app or fix it in Admin → Integrations. Offline is handled gracefully. |
| Port 7000 already in use | `.\scripts\stop_platform.ps1`, or set `TC_PORT` in `.env`. |
| Can't log in | Password is in `.env` (`TC_ADMIN_PASSWORD`). To reset, stop the app, delete `platform.db`, restart (re-seeds the default admin). **Only affects platform metadata — never the existing systems' data.** |
| Labels show keys like `kpi.x` | A hard refresh (Ctrl+F5) reloads the i18n files. |
| Health page slow | Each offline system waits up to `TC_HEALTH_TIMEOUT` (default 3s). Lower it in `.env`. |

---

## 11b. Branding — use your own logo
The sidebar, login and error pages **auto-use your real logo** the moment you drop a
file here (no code changes, no restart needed):

```
app/static/img/logo.png      ← your T&C logo (PNG recommended; .svg/.jpg/.webp also work)
```

If no `logo.*` is present, the built-in red T&C ribbon SVG mark is used as a fallback.
To also brand the **browser tab + installed-app (PWA) icon**, replace:
`app/static/img/favicon.svg`, `app/static/img/icon-192.png`, `app/static/img/icon-512.png`.

## 12. Security notes

- **Passwords** hashed (Werkzeug PBKDF2); **password policy** (≥8 chars, letter+digit) on creation and self-service change.
- **CSRF protection** on every state-changing request (`_csrf` field or `X-CSRF-Token` header).
- **Sessions** signed, HttpOnly, SameSite=Lax, `Secure` in production, time-limited.
- **Login throttling** — lockout after 5 failed attempts within 15 min.
- **Secret key** taken from `.env`; if unset, a strong key is generated once and persisted to `.secret_key` (never a hardcoded default).
- **Secure headers** (`X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`).
- **Audit logging** of login/logout/admin/production/password/export actions.
- **Permission-gated** menus and routes.
- **Production server**: run `python serve.py` (waitress) or `scripts\serve_production.ps1` — never the dev server.

See [`docs/ADMIN_GUIDE.md`](docs/ADMIN_GUIDE.md) for the full checklist.

---

## 13. Known limitations (Phase 1)

- Login is **platform-local**; cross-system SSO is Phase 2 (existing apps keep their own logins). Inside an embedded app you still sign in to that app the first time.
- Embedded mode relies on the apps sharing a host with the platform (same IP/hostname, any ports). On **different hostnames** the browser may block in-frame cookies — use the **Full screen** button there until SSO. See `docs/INTEGRATION_MAP.md`.
- KPI figures on the dashboard for the existing apps are curated samples; deep data
  adapters land in Phase 3 (the live **online/offline status is real**).
- The remaining future-ready modules show structured, realistic placeholder data
  ready for integration — they are not yet wired to live sources.
- **CSV exports are live** (systems, audit, production, notifications); PDF/Excel are still placeholders.

See [`docs/ROADMAP.md`](docs/ROADMAP.md).
