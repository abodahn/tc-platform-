# AI Prediction & Intelligence Center

A central intelligence layer for the TC Platform. It reads operational data
from every module, scores risk, detects anomalies, recommends corrective
actions, and gives management early warnings — turning a system of record into
a proactive, predictive command center.

Route prefix: `/intelligence` · Sidebar group: **AI Intelligence**

---

## 1. What it does

| Domain | Signals it predicts / detects |
|---|---|
| Tickets / SLA | SLA-breach risk, recurring machine tickets, technician overload |
| Assets | end-of-life, warranty expiry, high maintenance-to-value, poor condition, incomplete records |
| Maintenance | machine-failure risk (breakdowns, downtime, missed PM, stopped) |
| Inventory / spares | stock-out, reorder, over-stock / dead stock, critical-spare shortage |
| Procurement | PR approval delay (aging), value-weighted urgency |
| Approvals | approver bottlenecks across the cycle |
| Payroll | old-vs-new sheet diffs, z-score net outliers, zero/negative net, duplicates, missing employees, deduction jumps |
| HR probation | overdue / not-started evaluations, low scores, failing risk |
| Paperless | month-over-month spikes, high-volume departments, digitization saving |

Each result is an **explainable finding**: a 0–100 risk score, a severity, an
impact ("what happens if ignored"), a recommended action, a responsible owner,
and a plain-language "why".

**Risk levels:** 0–20 Low · 21–40 Watch · 41–60 Medium · 61–80 High · 81–100 Critical.

---

## 2. Pages

- **Command Center** (`/intelligence/`) — company health gauge, per-domain risk
  heatmap, top predicted issues, AI recommendations, departments to watch,
  domain-risk chart, and estimated business impact.
- **Alert Center** (`/intelligence/alerts`) — every predicted risk with filters
  (status / domain / severity), status workflow (New → Acknowledged → In
  Progress → Resolved / Ignored / Escalated), feedback, and CSV/Excel/PDF export.
- **Risk Breakdown** (`/intelligence/breakdown`) — per-domain drill-down.
- **AI Assistant** (`/intelligence/assistant`) — ask natural-language questions
  ("what are the highest risks today?"). Uses the LLM when configured, with a
  rule-based fallback so it always works.
- **Reports** (`/intelligence/reports`) — executive PDF, alerts Excel/CSV, run
  history, feedback accuracy.
- **Settings** (`/intelligence/settings`, admins) — engine options + Recompute.

---

## 3. Architecture

```
app/intelligence/
  schema.py        ai_* tables + domain tables (assets, payroll_rows, hr_probation, paper_usage) + demo seed
  services.py      read queries + alert status / feedback / settings writes
app/ai_engine/
  base.py                    finding(), risk levels, date helpers, safe queries (+ reuses app/ai_core.py)
  risk_scoring.py            domain score, company health, band colours, business-impact estimates
  ticket_predictor.py        asset_predictor.py  maintenance_predictor.py
  inventory_predictor.py     procurement_predictor.py  payroll_anomaly_detector.py
  hr_predictor.py            paperless_predictor.py
  recommendation_engine.py   nlp_assistant.py  report_generator.py
  orchestrator.py            runs all predictors, scores, persists snapshot, mirrors criticals to the bell
app/routes/intelligence.py   blueprint (Command Center, Alerts, Breakdown, Assistant, Reports, Settings)
app/templates/intelligence/  _base.html + command_center / alerts / breakdown / assistant / reports / settings
app/static/css/intelligence.css
```

**Prediction techniques** (practical, explainable, small/medium data):
z-score anomaly detection, linear-trend / moving average, SLA countdown,
frequency-based recurrence, aging analysis, weighted risk scoring, and
old-vs-new comparison. No black-box models; every score lists its reasons.

**Traceability:** every run writes `ai_model_runs`, and derived rows
(`ai_risk_scores`, `ai_predictions`, `ai_alerts`, `ai_recommendations`,
`ai_dashboard_metrics`) carry the `run_uid`. Human actions (status, feedback)
are preserved across runs and audit-logged.

---

## 4. Database tables

`ai_model_runs`, `ai_risk_scores`, `ai_predictions`, `ai_alerts`,
`ai_recommendations`, `ai_feedback`, `ai_dashboard_metrics`,
`ai_assistant_queries`, `ai_training_data_snapshots`, `ai_system_settings`,
plus operational tables `assets`, `payroll_rows`, `hr_probation`, `paper_usage`.

All are `CREATE TABLE IF NOT EXISTS`, seeded only when empty (idempotent),
SQLite-authored and auto-translated for PostgreSQL on Render.

---

## 5. Setup

1. The tables + demo data are created automatically on boot (`init_db`).
2. Open **AI Intelligence → AI Command Center** and press **Recompute now**
   (admins) to generate the first snapshot.
3. **AI Assistant** and the richer LLM answers use the platform's OpenRouter key
   (`OPENROUTER_API_KEY`). Without it, the assistant falls back to the
   rule-based engine and the rest of the module is unaffected.

**Connecting real data** (replacing demo): load your rows into `assets`,
`payroll_rows` (with `sheet_version` = `old`/`new`), `hr_probation`, and
`paper_usage`. Tickets, machines, spares, procurement and production already
read live platform data. Then Recompute.

---

## 6. Permissions

- View (Command Center, Alerts, Breakdown, Assistant, Reports): `view_dashboard`.
- Recompute + Settings: `access_admin`.
- **Sensitive domains (payroll, HR)** are only shown to `access_admin` users in
  the breakdown, alerts and assistant.

---

## 7. Scheduled runs

Trigger `app.ai_engine.orchestrator.run()` from any scheduler (cron, Render
Cron Job, or the platform's job runner). The Settings page exposes an `auto_run`
flag and a prediction horizon for when a scheduler is wired up.

---

## 8. Future enhancement roadmap

- OpenAI / other LLM providers (pluggable via the existing OpenRouter layer).
- Local / on-prem LLM for fully offline natural-language answers.
- OCR document intelligence (scan invoices, contracts, payroll PDFs).
- Voice assistant; WhatsApp + email alert delivery (structure is ready).
- Advanced ML models (scikit-learn survival/regression) once history grows.
- Real-time IoT machine telemetry for live failure prediction.
- Deeper BI integration and predictive production planning.
- Cybersecurity / data-leakage prediction from access logs.
- ERP integration.

---

*Built as a real, explainable enterprise intelligence layer — not a decorative
demo. Every alert is traceable to its data and its reasoning.*
