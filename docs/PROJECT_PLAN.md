# TC Platform — Master Plan: Features, Add-ons & Fixes
*Consolidated roadmap & status · reviewed June 2026*

This is the single source of truth for everything in TC Platform: what is **done
and verified**, what needs **fixing**, and the **planned features/add-ons** with
priorities and effort. Effort = S (hours), M (1–3 days), L (a week+).

---

## 0. Final results — current state (verified)

| Area | Status |
|---|---|
| Platform shell (login, RBAC 17 roles, trilingual EN/AR/TR + RTL, light/dark/auto, collapsible sidebar, search, notifications, audit) | ✅ Done |
| Security (CSRF, session hardening, login lockout, password policy + self-service, persistent secret key, WSGI/waitress) | ✅ Done |
| 4 systems integrated + **embedded** in the shell, live health checks | ✅ Done |
| **Production Visibility** — real CRUD module | ✅ Done |
| **Maintenance & Spare Parts (CMMS)** — full workflow, 20 tables, QR, photos, PWA, mobile Floor, CSV reports | ✅ Done |
| **Maintenance offline AI** — 9 features (risk, prescriptive, triage, assignment, MTTR, reorder, repeated-failure, root-cause, alternatives) + low-stock alerts | ✅ Done |
| One-click run-all (`RUN_ALL_SYSTEMS.bat`) + combined log | ✅ Done |
| Tests | ✅ 45 passing |

**Health snapshot:** 71 routes · 59 source files (~7,850 lines) · 28 DB tables · app boots clean.

---

## 1. Fixes / hardening (do first)

| # | Item | Why | Effort |
|---|---|---|---|
| F1 | Repair the 4 existing apps' broken `.venv`s (they point to another machine's Python) or document system-Python launch | RUN_ALL relies on system Python; venvs are dead | S |
| F2 | Replace dashboard **sample KPIs** for the 4 apps with a clear "demo data" tag until live adapters land | avoid mistaking samples for live numbers | S |
| F3 | Embedded-app **cross-host cookie** caveat — enforce same-host deploy or document Full-screen fallback | iframe login loops on different hostnames | S |
| F4 | Add a `413 Request Entity Too Large` friendly page for oversized photo uploads | currently raw error | S |
| F5 | PM "missed/overdue" auto-status job + reopen-window enforcement test coverage | edge cases not fully exercised | M |
| F6 | Rotate/limit `logs/*.log` from RUN_ALL (size cap) | unbounded growth | S |
| F7 | Production deploy guide: TLS reverse proxy + Windows service wrapper (NSSM) | currently manual | M |

---

## 2. Maintenance module — remaining add-ons

| # | Item | Effort |
|---|---|---|
| M1 | **Excel import** (machines, spare parts, opening stock) with validation preview | M |
| M2 | **PDF reports / printable work order & issue voucher** | M |
| M3 | **Multi-warehouse** + stock transfer between locations | M |
| M4 | **Purchase Order module** (out-of-stock → PO → receive → stock-in), Procurement/Finance approval | L |
| M5 | **Full month-grid maintenance calendar** + drag-to-reschedule PM | M |
| M6 | **Auto-generate PM work orders** on a schedule (cron/APScheduler) | M |
| M7 | **Attachments everywhere** (machine docs, PM checklist photos, voucher scans) | S |
| M8 | **Kanban drag-and-drop** status changes (currently click-through) | M |

---

## 3. Bring the other modules to life (real, not placeholder)

These are currently structured placeholders; convert to real CRUD like Production/Maintenance.

| # | Module | Scope | Effort |
|---|---|---|---|
| R1 | **Finance Digital** | invoices, approvals, CAPEX/OPEX, cost-saving tracker (DB-backed) | L |
| R2 | **Automation & RPA** | real pipeline tracker with hours-saved rollups | M |
| R3 | **HR Digital Services** | requests, onboarding/offboarding checklists, approvals | L |
| R4 | **Procurement Center** | PR → approval → PO (ties to Maintenance M4) | L |
| R5 | **BI Dashboards** | real Power BI link registry + embed | S |
| R6 | **Governance & Compliance** | risk register, CAB, access reviews (DB-backed) | M |
| R7 | **Knowledge Base** | real articles + search (feeds AI KB assist) | M |

---

## 4. Platform-wide integration (the "real platform" leap)

| # | Item | Why | Effort |
|---|---|---|---|
| P1 | **SSO across the 4 apps** | the #1 gap — one login for everything | L |
| P2 | **Live data adapters** (real ticket/asset/server/task counts on the Command Center) | dashboard becomes truthful & live | L |
| P3 | **Unified notification ingestion** (apps push events → platform bell) | shared, actionable alerts | M |
| P4 | **Unified reporting / data warehouse** (cross-system exports) | executive reporting | L |
| P5 | **PostgreSQL** option for production scale | beyond SQLite | M |

---

## 5. AI roadmap (offline-first, on-premise)

All pure-Python/statistical unless marked **LLM**. No internet required.

### 5a. Quick offline wins
| # | Feature | Module | Effort |
|---|---|---|---|
| AI1 | **AI Daily Executive Summary** (NLG) on the Command Center | Platform | S |
| AI2 | **Monitoring anomaly detection** (CPU/RAM/disk) + **capacity forecast** | Monitoring | M |
| AI3 | **ITSM auto-classification + SLA-breach risk** | ITSM | M |
| AI4 | **Asset health score + warranty/refresh forecast + dirty-data detection** | Assets | M |
| AI5 | **Project overdue-risk + workload balancing** | CommandTrack | M |
| AI6 | **Finance spend anomaly + monthly cost forecast** | Finance | M |
| AI7 | **Smart global search** (TF-IDF fuzzy across tickets/assets/machines/parts) | Platform | M |
| AI8 | Maintenance extras: downtime/cost anomaly, PM-interval optimizer | Maintenance | S |

### 5b. Real natural-language AI — still offline (needs a local model)
| # | Feature | Approach | Effort |
|---|---|---|---|
| AI9 | **On-prem LLM assistant** ("ask your data", helpdesk bot, NL reports) via **local Ollama** (Llama/Mistral) | LLM, offline | L |
| AI10 | **Semantic search & KB answers** via local embeddings | LLM, offline | M |

---

## 6. Recommended phasing

**Phase A — Stabilize ✅ DONE:** F1/F3/F6 (deploy guide) · F2 demo-KPI tag · F4 413 page ·
F5 PM computed status · F7 `docs/DEPLOYMENT.md` · M7 machine attachments ·
**AI1 Daily Executive Summary** (Command Center) · **AI8** downtime anomaly + PM optimizer.
**Phase B — Operational AI ✅ DONE:** **AI7** platform-wide smart search (fuzzy) ·
**M1** Excel import (machines/spares, template + validation) · **M2** PDF reports (reportlab) ·
**AI3** SLA-breach risk on maintenance tickets + reusable `app/ai_core.py` engine.
*(AI2 Monitoring / AI3 ITSM over the external apps' live data are delivered as the
reusable engine + applied to platform-owned data now; they plug into Monitoring/ITSM
once live data adapters land in Phase D.)*
**Phase C — Real modules (2–3 sprints):** R1 Finance, R4 Procurement + M4 PO, R7 KB.
**Phase D — Unify (2–3 sprints):** P1 SSO, P2 live adapters, P3 notifications, P5 PostgreSQL.
**Phase E — Advanced AI (1–2 sprints):** AI9/AI10 local LLM assistant + semantic search; M5/M6 PM automation.

---

## 7. Acceptance status vs. original brief

✅ Unified premium portal · trilingual + RTL · light/dark · RBAC · app launcher ·
embedded apps · health checks · admin · audit · CSRF/security · **2 real modules
(Production, Maintenance)** · **offline AI in maintenance** · mobile PWA · QR ·
one-click run.
⏳ Doing/next: SSO, live KPIs, the remaining real modules, platform-wide AI, PO / Excel import / PDF.

> Bottom line: the **platform foundation and the flagship Maintenance CMMS (with
> offline AI) are production-grade and verified.** The roadmap above turns the
> remaining placeholder modules into real systems and extends AI across the whole
> platform — all achievable as self-contained, offline-friendly increments.
