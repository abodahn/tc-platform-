# TC Platform — Roadmap

| Phase | Theme | Status | Scope |
|-------|-------|--------|-------|
| **1** | Unified Portal | ✅ Done | Unified portal, app launcher, design system, health checks, trilingual UI, RBAC, admin, notifications, search. |
| **2** | Unified Login & RBAC | 🔄 In progress | SSO-style login across all apps; shared identity and role mapping. |
| **3** | Notifications & APIs | ⏳ Planned | Shared notification center fed by each app's API; deeper data adapters (live ticket/asset/server/project counts). |
| **4** | Unified Reporting | ⏳ Planned | Cross-system executive dashboards; CSV/PDF/Excel exports. |
| **5** | AI & Automation | ⏳ Planned | AI assistant, ticket classification, SLA risk prediction, automation orchestration. |
| **6** | Production & Finance Tower | ⏳ Planned | Live production visibility (IoT/shopfloor) and finance digital control tower. |

## Phase 1 — delivered
- Executive Command Center with animated KPIs, SLA ring, transformation progress.
- Application Launcher with **live online/offline** status for all four systems.
- Trilingual EN/AR/TR with full RTL; Light/Dark/Auto themes (saved per user).
- 12-role RBAC with permission-gated menus and routes.
- Admin Center: users, editable integrations, audit logs.
- Health page + `/api/health` + `/api/status`; PowerShell health/backup scripts.
- 12 future-ready modules with structured placeholder data.
- Backend smoke tests + Playwright screenshot capture.

## Delivered beyond Phase 1
- **Production Visibility is now a real working module** (not a placeholder): full
  add/edit/delete for production lines, downtime events and quality issues,
  persisted to the platform database, with a live KPI dashboard and role-based
  editing (`manage_production`). This is the template for converting the other
  future-ready modules into real tools.
- **Run-all launcher** starting the platform + all four systems on unified ports.
- **Redesigned sidebar**: collapsible sections, icon-rail collapse with tooltips.

## Near-term backlog (towards Phase 2/3)
- SSO bridge (shared session/token with the four apps).
- Live KPI adapters replacing curated dashboard samples.
- Notification ingestion from app webhooks/APIs.
- PostgreSQL persistence option (`TC_DATABASE_URL`).
- Per-user notification filtering and sound preferences.
