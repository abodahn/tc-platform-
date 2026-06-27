# TC Platform — Integration Map

This document records exactly how each existing T&C system is integrated, its
**actual** default port (as shipped) versus the **recommended** deployment port,
and its health endpoint. Edit live values in **Admin Center → Integrations**.

## Integrated systems

| # | System | Key | Actual default (as-shipped) | Recommended / seeded | Health endpoint | Iframe-safe |
|---|--------|-----|------------------------------|----------------------|------------------|-------------|
| 1 | ITSM Service Desk (`TC_Garments_ExcelDB_FINAL`) | `itsm` | **5000** | 5000 | `/health` (HTML, auth → redirect still = online) | yes (no XFO) |
| 2 | Asset & Inventory (`enterprise_asset_inventory`) | `assets` | **5000** (run.py) | 5001 | `/api/health` → `{"status":"ok"}` | yes |
| 3 | Server Monitoring (`windows-monitoring-command-center`) | `monitoring` | **8000** | 5002 | `/health` → `{"ok":true}` | yes |
| 4 | CommandTrack Work (`..._TRILINGUAL_v2_3_1`) | `commandtrack` | **8080** | 5003 | none → root `/` (also `/api/notifications/unread`) | yes |

> **Port conflict note:** ITSM and Asset both default to **5000** as shipped.
> For the unified plan, run Asset on **5001** (and Monitoring on 5002,
> CommandTrack on 5003), or simply point TC Platform at each app's real URL in
> Admin → Integrations. Nothing in the existing apps is changed by TC Platform.

## How to align ports

**Option A — change each app to the plan (5000/5001/5002/5003):**
- ITSM: already 5000.
- Asset: edit `run.py` → `app.run(..., port=5001)`.
- Monitoring: edit `central_server/app.py` → `socketio.run(..., port=5002)`.
- CommandTrack: launch with `python app.py --port=5003` (it accepts `--port`).

**Option B — keep app defaults, tell TC Platform the truth:**
- Admin → Integrations → set Base URL to e.g. `http://127.0.0.1:8000` (Monitoring)
  and `http://127.0.0.1:8080` (CommandTrack). Save. Done.

## Health-check semantics

| Result | Meaning |
|--------|---------|
| online | HTTP response < 500 (including 302 login redirects) |
| warning | HTTP 5xx |
| offline | connection refused / timeout / DNS failure |
| unknown | no URL configured for that system |

The platform sends **no credentials** and performs **GET only** — checks are
read-only and safe.

## Embedded mode (the platform concept) — DEFAULT

Opening an integrated module (from the sidebar or the Launcher **Open** button)
loads the **full existing app inside the TC Platform shell** via an iframe — the
platform sidebar and topbar stay in place and the app renders in the content
area at `/module/<key>`. This is the default for all four integrated systems and
works because none of them set `X-Frame-Options`.

Each embedded view has a slim bar with:
- live **status** badge,
- **Refresh** (reload just the app frame),
- **Full screen** (open the app in a new tab),
- **Details** (`/module/<key>?view=details`) — technical/launch info page.

If the app is offline the viewer shows a retry panel instead of a broken frame.

> **Cross-site cookie caveat (production):** embedding works seamlessly when the
> platform and apps share a host (same IP/hostname, different ports — cookies are
> per-host, not per-port). If you put apps on **different hostnames**
> (e.g. `ITSM.local` vs `TCPlatform.local`), browsers treat them as cross-site and
> `SameSite=Lax` session cookies may not flow inside the iframe — use the
> **Full screen** button there, or unify the host, until SSO (Phase 2).

## Launch modes (Admin → Integrations)
- **embed** — show inside the platform (default behaviour described above).
- **new_tab** — the Launcher's secondary ↗ button always offers this.
- **same_tab** — navigate the current tab to the app.

## Brand alignment

The platform design system is derived from CommandTrack's brand tokens:
red `#ED1C24` / `#C8102E`, navy `#07080B` / `#080A0F`, white surfaces,
Segoe UI, 22px radius, signature textile grid — so the whole ecosystem feels
like one product.
