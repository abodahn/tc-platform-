# TC Platform — Test Checklist

## Automated
- [x] `pytest` — 17 backend smoke tests pass (boot, auth guard, login success/fail,
      all pages load, status API, 404 on unknown module, RBAC 403, search).
- [x] `python tests/capture_screenshots.py` — 9 screenshots captured.

## Manual acceptance
| # | Check | Expected | Status |
|---|-------|----------|--------|
| 1 | Platform starts on 7000 | `start_platform.ps1` serves http://127.0.0.1:7000 | ✅ |
| 2 | Login works | admin / password → Command Center | ✅ |
| 3 | Wrong password | re-shows login, stays unauthenticated | ✅ |
| 4 | Dashboard premium | hero, animated KPIs, SLA ring, health cards | ✅ |
| 5 | Four apps appear as modules | ITSM, Assets, Monitoring, CommandTrack | ✅ |
| 6 | Online/offline status | each integrated app shows a live badge | ✅ |
| 7 | Language EN/AR/TR | all UI labels switch | ✅ |
| 8 | Arabic RTL | layout mirrors, sidebar flips, text right-aligned | ✅ |
| 9 | Light/Dark/Auto | theme switches and persists | ✅ |
| 10 | Sidebar collapse | toggles and remembers state | ✅ |
| 11 | App launcher | cards open systems / module details | ✅ |
| 12 | Admin edits URLs/ports | Integrations tab saves changes | ✅ |
| 13 | Health page | platform + integrations status, response times | ✅ |
| 14 | Broken/offline URL | handled gracefully (Offline, no crash) | ✅ |
| 15 | Role visibility | `agent`/`exec` see fewer menus; admin blocked (403) for non-admins | ✅ |
| 16 | Responsive | mobile/tablet/desktop/4K via max-width grid | ✅ |
| 17 | No console errors | clean console on dashboard | ✅ |
| 18 | Existing systems intact | no files/DBs of existing apps modified | ✅ |
| 19 | Global search | finds apps/modules by name | ✅ |
| 20 | Notifications | bell shows items; mark-all-read clears dot | ✅ |

## Screenshots captured (`screenshots/`)
`01_login`, `02_dashboard_light`, `03_dashboard_dark`,
`04_dashboard_arabic_rtl`, `05_launcher`, `06_admin`, `07_health`,
`08_module_ai_hub`, `09_mobile_dashboard`.

## Known limitations
See README §13 and ROADMAP.md (SSO, live data adapters, report exports are later phases).
