# QA_PLATFORM_DISCOVERY_REPORT

**Platform:** TC Garments Digital Operations Platform (Flask · SQLite dev / PostgreSQL on Render)
**Scan method:** live introspection of `create_app()` URL map, DB schema, and `app.security` role/permission tables + static code scan.

## 1. Blueprints / modules (12) — 176 routes total (99 POST, 77 GET)

| Blueprint | Routes | Module |
|---|---|---|
| maintenance | 45 | Maintenance / CMMS (tickets, machines, spares, PM, stock, approvals, reports) |
| approvals | 38 | Procurement & Approval cycle (PR→PO→receive→invoice→pay, vendors, budgets, delegations, responsibility matrix) |
| bi | 24 | BI & Analytics (upload→dashboard, charts, insights, alerts, digests, ask) |
| main | 21 | Core (dashboard, launcher, modules, registry, reports, profile, health, roadmap, prefs, search, notifications) |
| admin | 12 | Admin Center (users, roles, permissions, integrations, audit, Garamento KB) |
| intelligence | 11 | AI Prediction & Intelligence Center (command center, alerts, breakdown, assistant, reports, settings) |
| production | 9 | Production visibility (lines, downtime, quality) |
| garamento | 7 | AI assistant + market research + draft/triage/polish + KB |
| api | 5 | Health/status/integrations-sync (machine endpoints) |
| auth | 2 | Login / logout |
| sso | 1 | SSO IdP launch |
| static | 1 | Static assets |

## 2. Database tables (59)

- **Core:** users, systems, notifications, audit_logs, custom_roles
- **Production:** production_lines, production_downtime, production_quality
- **BI:** bi_datasets, bi_dashboards, bi_alerts, bi_digests
- **Maintenance (CMMS):** mnt_machines, mnt_spare_parts, mnt_spare_compat, mnt_tickets, mnt_diagnosis, mnt_comments, mnt_requests, mnt_request_items, mnt_approvals, mnt_approval_matrix, mnt_stock_movements, mnt_vouchers, mnt_pm_plans, mnt_pm_checklist, mnt_pm_work_orders, mnt_pm_results, mnt_notifications, mnt_audit, mnt_attachments, mnt_settings
- **Procurement / P2P:** proc_vendors, pr_requests, pr_items, pr_steps, pr_events, pr_invoices, pr_payments, pr_attachments, pr_quotes, proc_budgets, proc_delegations, proc_resp_matrix
- **Digital signature:** users.sig_* columns
- **Garamento:** garamento_topics
- **AI Intelligence:** ai_alerts, ai_predictions, ai_risk_scores, ai_recommendations, ai_model_runs, ai_feedback, ai_dashboard_metrics, ai_assistant_queries, ai_training_data_snapshots, ai_system_settings
- **AI operational domains:** assets, payroll_rows, hr_probation, paper_usage

## 3. Roles (22) & Permissions (22)

**Permissions:** view_dashboard, open_module, manage_users, manage_settings, view_reports, export_reports, view_system_health, manage_integrations, access_admin, manage_production, maint_view, maint_ticket_create, maint_technician, maint_manage, maint_approve, maint_store, maint_admin, proc_view, proc_create, proc_approve, proc_purchasing, proc_admin.

**Roles:** super_admin (`*`), it_director, it_manager, service_desk_agent, asset_manager, monitoring_admin, project_manager, finance_user, hr_user, production_manager, executive_viewer, normal_user, maintenance_manager, maintenance_technician, storekeeper, production_supervisor, factory_manager, purchasing_manager, finance_manager, cfo, ceo, warehouse_manager. Roles are also DB-overridable via `custom_roles` + per-user `extra_perms`.

## 4. Cross-cutting subsystems

- **Auth:** session-based (`session["uid"]`), `login_required` / `permission_required` decorators, in-memory login throttle.
- **CSRF:** global `before_request` guard (form `_csrf` or `X-CSRF-Token`); read-only JSON APIs + job endpoints exempted.
- **SSO:** platform is IdP; short-lived signed tokens; shared `TC_SSO_SECRET`.
- **i18n:** EN/AR/TR flat JSON (`app/static/i18n/*.json`), `data-i18n`/`-ph`/`-title` attributes, RTL on Arabic. **Parity verified: 0 missing keys across all three.**
- **Exports:** CSV (csv), Excel (openpyxl), PDF (reportlab) with formula-injection guard.
- **Background/AI:** Garamento (OpenRouter, degrades offline), AI engine orchestrator, BI job endpoint (token/admin gated), keep-alive GitHub Action.
- **DB dialect shim:** `_PGConn` translates SQLite SQL → PostgreSQL at runtime.

## 5. Existing automated tests (discovered)

26 unit/integration test files (`tests/test_*.py`) + 5 Playwright e2e (`tests/e2e/`), with `conftest.py`, fixtures and screenshot helper. Coverage spans auth, RBAC, CSRF, routes access, approvals, maintenance, BI, notifications, SSO, signature, i18n, DB-dialect, admin users, roles.
