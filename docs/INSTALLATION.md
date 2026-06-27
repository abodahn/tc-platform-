# TC Platform — Installation Guide

## Prerequisites
- Windows 11 / Windows Server 2019+ (or any OS with Python)
- Python 3.12+ on PATH (`python --version`)

## 1. Install
```powershell
cd "tc-platform"
.\scripts\install_requirements.ps1
```
Creates `.venv`, installs `requirements.txt`, copies `.env.example` → `.env`.

## 2. Configure `.env`
```ini
TC_ENV=development
TC_SECRET_KEY=<long-random-string>
TC_PORT=7000
TC_ADMIN_USER=admin
TC_ADMIN_PASSWORD=<strong-password>
TC_INTEGRATION_HOST=127.0.0.1     # use 10.100.1.13 in production
TC_HEALTH_TIMEOUT=3
```
> Set `TC_INTEGRATION_HOST` **before first run** so seeded system URLs use the
> right host. After first run, change URLs in Admin → Integrations instead.

## 3. Run
```powershell
.\scripts\start_platform.ps1
# open http://127.0.0.1:7000
```

## 4. First login
`admin` / value of `TC_ADMIN_PASSWORD`. Change it in Admin Center.

## 5. Verify
```powershell
.\scripts\health_check.ps1     # platform should be ONLINE; systems show their real state
pytest                         # optional: 17 smoke tests
```

## 6. Production (waitress)
```powershell
pip install waitress
$env:TC_ENV="production"; $env:TC_DEBUG="false"; $env:TC_PORT="80"
waitress-serve --port=80 --call app:create_app
```
Place behind IIS/nginx for TLS. Configure internal DNS / hosts for
`TCPlatform.local` etc. (see README §6).

## Resetting the platform DB (safe)
Stop the app, delete `platform.db`, restart — it re-seeds the default admin,
systems and sample notifications. **This never affects the existing systems.**

## Troubleshooting
See README §11.
