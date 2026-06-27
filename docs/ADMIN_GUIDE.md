# TC Platform — Administrator Guide

## Accessing the Admin Center
Sign in as a user whose role has `access_admin` (Super Admin, IT Director).
Sidebar → **Admin Center** (`/admin/`). Three tabs: Users, Integrations, Audit Logs.

## Users
- **Add user:** username, full name, email, role, password → Add.
- **Enable/Disable:** toggle a user's access (you cannot disable yourself).
- Passwords are hashed (PBKDF2) — never stored in plain text.

### Roles & permissions
Roles are defined in `app/security.py`. Permissions gate both **menu visibility**
and **route access**.

| Role | Key permissions |
|------|-----------------|
| Super Admin | all |
| IT Director | dashboard, modules, reports, export, health, integrations, admin |
| IT Manager | dashboard, modules, reports, export, health, integrations |
| Service Desk Agent | dashboard, modules, reports, health |
| Asset Manager | dashboard, modules, reports, export |
| Monitoring Admin | dashboard, modules, health, reports |
| Project Manager | dashboard, modules, reports, export |
| Finance / HR / Production | dashboard, modules, reports |
| Executive Viewer | dashboard, modules, reports, health |
| Normal User | dashboard, modules |

Permissions catalogue: `view_dashboard`, `open_module`, `manage_users`,
`manage_settings`, `view_reports`, `export_reports`, `view_system_health`,
`manage_integrations`, `access_admin`.

## Integrations
For each system you can edit **Base URL, Port, Health URL, Launch mode, Owner,
Enabled**. Changes take effect immediately and are audit-logged. See
`INTEGRATION_MAP.md` for the recommended values and the actual app defaults.

- To **hide** a module from everyone, untick *Enabled*.
- To **repoint** a system (e.g. moved to 10.100.1.13), edit its Base URL.

## Audit Logs
Every login, failed login, logout, user change and integration change is recorded
with username, action, detail, IP and timestamp. Review under the Audit tab.

## Branding / language / theme
- Brand tokens live in `static/css/tokens.css` (red `#ED1C24`, navy `#07080B`).
- UI languages in `static/i18n/{en,ar,tr}.json`.
- Users pick language + theme from the top bar; choices persist to their profile.

## Security features (built in)
- **CSRF protection** on all POST/PUT/DELETE (token via `_csrf` field or `X-CSRF-Token` header).
- **Login lockout** after 5 failed attempts in 15 minutes (per username+IP, 10-min cooldown).
- **Password policy** (≥8 chars, at least one letter and one digit) on creation and change.
- **Self-service password change** at **Profile** (top-right user menu).
- **Session cookies**: HttpOnly, SameSite=Lax, `Secure` when `TC_ENV=production`.
- **Persistent secret key**: generated to `.secret_key` if `TC_SECRET_KEY` is unset.

## Security checklist
- [ ] Strong `TC_SECRET_KEY` and `TC_ADMIN_PASSWORD` in `.env` (not committed).
- [ ] Change the default admin password after first login (Profile → Change password).
- [ ] Change ITSM's weak default (`1234`) and other apps' defaults.
- [ ] `TC_DEBUG=false` and `TC_ENV=production` in production.
- [ ] Serve with `serve.py` / `serve_production.ps1` (waitress), behind a TLS reverse proxy.
- [ ] Periodic `backup_platform.ps1`; back up existing systems with
      `backup_existing_projects.ps1` before any maintenance.
- [ ] Review audit logs regularly (now also logs password changes and report exports).

## Backups & recovery
- Platform metadata: `scripts/backup_platform.ps1` → `backups/<timestamp>/`.
- Existing systems: `scripts/backup_existing_projects.ps1` (non-destructive copy).
- Recovery: stop app, restore `platform.db` from a backup folder, restart.
