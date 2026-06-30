# TC Platform — Unification & Hardening Roadmap

Turning the portal from "aggregated systems" into a true unified platform, plus
operational hardening. Phased so each step ships value independently.

## Phase 1 — Observability & Alerting  ✅ (in progress, this PR)
**Goal:** know when something breaks, and tell a human — not just the in-app bell.
- [x] **Error tracking** — global 500 handler logs full tracebacks to
  `DATA_DIR/logs/errors.log`, optional **Sentry** (`TC_SENTRY_DSN`), and emails admins.
- [x] **External alerting** — `app/services/alerts.py` sends **email (SMTP)** and an
  optional **webhook** (WhatsApp / Slack / Teams / n8n) for every *critical*
  notification (incl. a system going offline or any of the 4 systems' alerts).
  No-op until SMTP/webhook is configured, so it's safe to deploy.
- [x] Tests for the alerting service + severity gating.

## Phase 2 — Off-site backups + tested restore  ✅ (this PR, scripts)
**Goal:** survive the PC dying.
- [x] `backup_all.py` mirrors every DB + Excel store to an **off-site folder**
  (`TC_BACKUP_MIRROR` — a OneDrive/network/UNC path) after each local backup.
- [x] `restore_test.py` — restores the latest backup to a temp dir and verifies
  each DB opens and has rows (an untested backup isn't a backup).

## Phase 3 — Consolidated business dashboard  (next)
**Goal:** one screen with real KPIs from all four systems, not just health.
- [ ] Each system exposes `GET /api/integration/summary` → `{kpis:[{label,value,severity}]}`
  (assets & book value; open/breached tickets; servers down + active alerts;
  pending approvals + overdue tasks).
- [ ] Platform pulls + caches them and renders a unified "Business Overview" card grid.
- [ ] Reuses the existing throttled pull + `base_url` plumbing.

## Phase 4 — Single Sign-On (login once → all 4 systems)  (next, larger)
**Goal:** stop the 5-logins problem. Platform becomes the identity provider.
Design (trusted-token SSO, works across Flask + http.server):
- [ ] Platform issues a short-lived **HMAC-signed token** (user, role, exp) using a
  shared `TC_SSO_SECRET`.
- [ ] Each system gains `GET /sso?token=…&next=…` that validates the token, finds or
  **provisions a local user** for that person, sets its own session, redirects.
- [ ] "Open module" links/iframes carry the token, so a click auto-logs-in.
- [ ] Per-user provisioning replaces the shared `Admin@123`.
**Pre-req:** put the 4 systems in Git first (so these auth changes are tracked).

## Phase 5 — Deep data integration  (largest)
**Goal:** the systems share data, not just a frame.
- [ ] Shared **employee directory** + **asset registry** (one source of truth).
- [ ] Cross-links: an ITSM ticket about a machine links to the Asset record.
- [ ] Notifications **deep-link to the exact item**, not just the module.

## Cross-cutting (do alongside)
- [ ] Version-control the 4 systems (fastest risk reducer).
- [ ] Migrate DB off the Render free tier (~90-day expiry).
- [ ] Secure the integration endpoints with a shared token; HTTPS internally.
