# TC Platform — Production Deployment Guide

## 1. Run with a real WSGI server (not the dev server)
```powershell
$env:TC_ENV="production"; $env:TC_DEBUG="false"
$env:TC_SECRET_KEY="<long-random-string>"
$env:TC_PORT="8080"          # behind a reverse proxy on 80/443
.\scripts\serve_production.ps1   # waitress (serve.py)
```

## 2. TLS reverse proxy (terminate HTTPS here)
Put IIS or nginx in front and proxy to waitress. nginx example:
```nginx
server {
  listen 443 ssl;
  server_name tcplatform.local;
  ssl_certificate     C:/certs/tcplatform.crt;
  ssl_certificate_key C:/certs/tcplatform.key;
  location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 15m;   # allow photo uploads
  }
}
```
With `TC_ENV=production`, session cookies are flagged `Secure` (HTTPS-only).

## 3. Run as a Windows service (auto-start, restart on crash)
Use **NSSM** (Non-Sucking Service Manager):
```powershell
nssm install TCPlatform "C:\Program Files\Python314\python.exe" "D:\TC platform\tc-platform\serve.py"
nssm set TCPlatform AppDirectory "D:\TC platform\tc-platform"
nssm set TCPlatform AppEnvironmentExtra TC_ENV=production TC_DEBUG=false TC_PORT=8080
nssm start TCPlatform
```
Do the same per integrated app, or run all in dev with `RUN_ALL_SYSTEMS.bat`.

## 4. The four integrated apps (important)
- Their bundled `.venv` folders were created on another machine and are **broken**
  (they reference `C:\Users\ahmed\...`). The launcher uses the **system Python**
  with shared dependencies, which is correct. To give each its own clean venv:
  ```powershell
  cd <app folder>; python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
  ```
- **Same-host requirement for embedding:** the apps load **inside** TC Platform via
  iframe. Keep the platform and the apps on the **same host** (same IP/hostname,
  different ports) so the browser shares cookies. On different hostnames, an embedded
  app's login can loop — users should click **Full screen** (opens the app in its
  own tab). Configure URLs in **Admin → Integrations**.

## 5. Logs
`RUN_ALL_SYSTEMS.bat` writes each system to `tc-platform\logs\*.log` and truncates
them on every start, so they don't grow unbounded. Under NSSM, point
`AppStdout`/`AppStderr` to a rotating location or a folder you clear periodically.

## 6. Backups (before any maintenance)
```powershell
.\scripts\backup_platform.ps1            # platform.db + .env
.\scripts\backup_existing_projects.ps1   # snapshot all 4 apps (non-destructive)
```
Uploaded photos live in `tc-platform\uploads\` — include them in your backup.

## 7. Health & monitoring
- `GET /api/health` (platform) and `GET /api/status` (integrated apps) for load balancers.
- `.\scripts\health_check.ps1` pings everything from the shell.

## 8. Security checklist (see ADMIN_GUIDE.md)
Strong `TC_SECRET_KEY` + `TC_ADMIN_PASSWORD`; `TC_ENV=production`; TLS in front;
change every app's default password (ITSM ships with `1234`); review audit logs.

---

## 9. Deploy to Render (online, PostgreSQL) — FREE tier

This variant runs online on [Render](https://render.com) with a managed PostgreSQL
database. The **same code** runs on SQLite locally and PostgreSQL online (chosen by
`DATABASE_URL`). The 4 LAN apps (ITSM/Asset/Monitoring/CommandTrack) aren't reachable
from the public internet, so online they show *Offline* — the platform shell and the
self-contained modules (Maintenance + AI, Production, Admin, Reports) work fully.

**Free-tier limits (by design):** the web service sleeps after ~15 min idle (first hit
cold-starts ~50s); free PostgreSQL has limited storage and expires ~90 days; **no
persistent disk → uploaded photos are lost on redeploy/restart** (app keeps working).
To keep uploads, upgrade the web service to `starter` and uncomment the `disk:` block in
`render.yaml` (set `TC_DATA_DIR=/var/data`).

### Steps
1. **Push to GitHub:**
   ```bash
   git add -A
   git commit -m "TC Platform — Render deploy variant"
   git branch -M render-deploy
   git remote add origin https://github.com/<you>/tc-platform.git
   git push -u origin render-deploy
   ```
   Confirm `render.yaml` is at the repo root and `platform.db`/`.venv/`/`.env` are NOT committed.
2. **Render ▸ New ▸ Blueprint** → connect the repo → pick `render-deploy`. Render reads
   `render.yaml` and lists the web service + PostgreSQL database.
3. **Enter prompted secrets:** `TC_ADMIN_USER` + a strong `TC_ADMIN_PASSWORD`.
   `TC_SECRET_KEY` is auto-generated; `DATABASE_URL` auto-wired.
4. **Apply.** DB provisions first; web service builds (`pip install`) and starts (`gunicorn`).
5. **Health check** at `/api/health` goes green → live at `https://tc-platform.onrender.com`
   (schema auto-creates + seeds on first boot).
6. **First login:** `TC_ADMIN_USER` / `TC_ADMIN_PASSWORD`.
7. **(Optional) migrate local data:** Render ▸ database ▸ Info ▸ External Database URL, then
   `python scripts/migrate_sqlite_to_postgres.py --sqlite ./platform.db --database-url "postgresql://..."`.
8. **Lock down the DB:** after migrating, remove the `0.0.0.0/0` external rule.

### Test the PostgreSQL path locally (optional)
```bash
docker run -d --name tcpg -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=tc_platform -p 5432:5432 postgres:16
# PowerShell: $env:DATABASE_URL="postgresql://postgres:pass@localhost:5432/tc_platform"
pytest                                   # same suite must pass on Postgres
```

### Env vars on Render
| Variable | Source | Notes |
|---|---|---|
| `DATABASE_URL` | auto `fromDatabase` | managed Postgres connection string |
| `TC_ENV` | `production` | secure cookies on |
| `TC_SECRET_KEY` | `generateValue: true` | stable across restarts |
| `TC_ADMIN_USER`/`TC_ADMIN_PASSWORD` | prompted (`sync:false`) | strong password |
| `TC_DATA_DIR` | `/tmp/tc-data` (free) / `/var/data` (disk) | uploads + backups |
| `PORT` | injected by Render | gunicorn binds `0.0.0.0:$PORT` |
