# QA_BUSINESS_WORKFLOW_MAP

End-to-end business cycles per module (the paths QA validates).

## Maintenance / CMMS
Report ticket → auto/AI triage (priority, causes, spares) → assign technician → diagnose → request spare parts → approval → issue spare (stock movement) → perform work → record downtime/cost → close → PM plans & schedules → reports/export. States enforced by `can_transition`.

## Procurement / Approvals (P2P)
New PR (dropdowns, attachments, market-research pricing, AI draft) → submit → **approval ladder** by amount + department responsibility matrix (warehouse → factory mgr → purchasing → finance → CFO → CEO) → digital-signature sign-off → PO issued → email PO → receive lines → invoice (3-way match) → payment → close. Vendors, budgets, delegations, analytics supporting.

## BI & Analytics
Upload file → profile → auto dashboard (KPIs, charts, insights, forecast) → NL query / Ask-AI → alerts (threshold → bell) → scheduled digests → export.

## AI Prediction & Intelligence Center
Recompute → 8 predictors read live/domain data → risk scoring (domain + company health) → alerts (status workflow + feedback) → recommendations → business-impact → reports export → assistant (grounded NL). Payroll/HR restricted to admins.

## Payroll (via AI Center)
Old + new sheets → compare → detect anomalies (z-score net, zero/neg net, duplicate, missing, deduction spikes, variance) → risk-scored alerts → validation export → review before approval.

## HR Probation (via AI Center)
Probation records → detect overdue/not-started evaluations, low scores, failing risk → alerts → follow-up recommendation.

## Paperless / Go-Green
Per-dept monthly usage → trend/spike detection → digitization + saving estimate → adoption signal.

## Admin
Create/edit/disable user → assign role → manage permissions (roles editor + per-user extra_perms) → integrations (system URLs) → audit logs → Garamento KB editor → settings.

## Auth / SSO
Login (throttle) → session → role-based access → SSO token launch into the 4 external systems → logout.

## Reports & Exports
Pick report → filter → preview → export CSV/Excel/PDF (export = same query as preview).

## Notifications
Source events + AI criticals → bell (per-user targeting, dedup) → mark read → optional email/webhook.
