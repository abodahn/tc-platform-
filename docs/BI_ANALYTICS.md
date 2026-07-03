# TC Platform — BI & Analytics (Phase 6)

_Instant, interactive dashboards from any file — computed entirely offline._

The BI section is no longer a placeholder. Drop a CSV or Excel file and the
platform builds a full dashboard in seconds: auto KPIs and charts, plain-language
insights in three languages, anomaly flags, a forecast, a smart-search bar,
cross-filter drill-down, save/pin, one-click export, KPI threshold alerts wired
to the notification bell, and scheduled email digests.

**No new dependencies, no AI model, no internet.** The whole engine is pure
Python standard library plus `openpyxl` (Excel) and `reportlab` (PDF) — both
already in the project — with `statistics` doing the maths. Charts render from a
locally-vendored copy of Chart.js. It runs the same on the Render cloud instance
and on the on-prem laptop, online or off.

---

## What you get (the ⭐ features)

| Feature | What it does |
|---|---|
| **Instant auto-dashboard** | Drop a file → columns are typed, KPIs and charts chosen automatically, rendered in ~2s. |
| **Auto-insights (EN/AR/TR)** | Plain-language takeaways: trend, peak, Pareto 80/20, biggest category, an anomaly, a correlation, a data-quality nudge. |
| **Anomaly spotlight** | Outliers/spikes flagged directly on the trend line (IQR rule). |
| **Forecast** | A dashed "projected next periods" line on time series (linear regression). |
| **Smart search** | Type "top 10 product by revenue" → it builds the chart. Pattern-based, no LLM. |
| **Data-quality check** | Missing values, duplicates, constant columns flagged on upload, with a 0–100 score. |
| **Cross-filter drill-down** | Click a bar/slice → the whole dashboard filters to it; chips show the active filters. |
| **Save / pin / templates** | Save dashboards, pin them, reopen from the workspace. |
| **Export** | One-click Excel (data + summary) and PDF (KPIs + insights); PNG per chart. |
| **KPI threshold alerts** | Set a limit on any number → a breach fires into the notification bell + email/webhook (reuses the existing alert pipeline). |
| **Scheduled email digest** | Email a dashboard summary on a daily/weekly/monthly cadence (reuses SMTP). |
| **Trilingual + RTL** | The whole UI and the insights render in English, Arabic and Turkish. |

---

## Architecture

```
Upload (CSV/Excel)                 or   a saved dataset
      │                                       │
      ▼                                       ▼
reader → profiler ─────────────► recommender ──► auto dashboard (KPIs + charts)
(types, stats,  │                                   │
 data quality)  ├──► insights (EN/AR/TR)            ├──► analyze.chart_data ──► Chart.js (offline)
                └──► analyze (forecast, outliers,    │        ▲
                     correlations, aggregation)      │        └── cross-filter / smart search
                                                     ▼
                                     store (SQLite / Postgres) — datasets, dashboards, alerts, digests
                                                     │
                              jobs.evaluate_alerts ──┴── jobs.run_due_digests
                                     │                        │
                              notification bell          SMTP digest
                              + email/webhook
```

Engine modules live in `app/services/bi/`:

| Module | Role |
|---|---|
| `reader.py` | Read CSV/TSV (stdlib `csv`) and Excel (`openpyxl`) → `(columns, rows)`. Caps rows to protect memory. |
| `profiler.py` | Infer column types (number/date/category/boolean/text), compute stats, grade data quality. |
| `analyze.py` | Aggregation engine (chart data, cross-filter), IQR outliers, linear forecast, correlations. |
| `recommender.py` | Profile → a proposed dashboard (which KPIs, which charts). |
| `insights.py` | Deterministic NLG in EN/AR/TR. |
| `query.py` | Smart-search string → chart spec (type-based measure/dimension disambiguation). |
| `store.py` | Persistence for datasets/dashboards/alerts/digests (+ in-process cache). |
| `jobs.py` | Alert evaluation → bell; scheduled digest send. |

Routes: `app/routes/bi.py` (`/bi` workspace, `/bi/dataset/<id>`, the `/bi/api/...`
data endpoints, export, alerts, digests). UI: `app/templates/bi/` +
`app/static/js/bi.js` + vendored `app/static/vendor/chart.umd.min.js`.

Data model (all SQLite + Postgres portable): `bi_datasets`, `bi_dashboards`,
`bi_alerts`, `bi_digests`.

---

## Scheduling alerts & digests

Alert evaluation and digest sending run on demand and via one endpoint so an
external scheduler can drive them headless:

```
POST /bi/jobs/run          # evaluate all alerts + send any due digests
```

Auth: an admin session, **or** a matching `TC_BI_JOB_TOKEN` (set the env var,
then call with `?token=...` or header `X-BI-Token`). Point the local
`TUNNEL_WATCHDOG`/autostart or a Render cron job at it (e.g. every 15 min) to get
live alerting and scheduled digests. Without a scheduler, alerts still evaluate
whenever the endpoint is hit and digests can be sent with **Send now**.

Email uses the existing `TC_SMTP_*` settings; if SMTP isn't configured, alerts
still appear in the bell and digests report "not configured yet".

---

## Limits & notes

- Row cap: 50,000 read / 30,000 persisted per dataset (protects memory and DB
  size). Larger files are truncated with the row count shown.
- Datasets are stored as JSON in the DB so they survive on Postgres (Render) and
  SQLite (local); an in-process cache avoids re-parsing on every query.
- The smart-search parser handles common shapes ("top N X by Y", "X over time",
  "avg X by Y", "distribution of X", "X vs Y", "share by X"); it isn't full NL —
  unrecognised phrases show a hint.
- Click-through to a source-system record (via SSO deep links) activates when a
  dataset is sourced from a system; uploaded files use cross-filter drill-down.

---

## Files

Engine `app/services/bi/*.py` · routes `app/routes/bi.py` · UI
`app/templates/bi/{workspace,dashboard}.html`, `app/static/js/bi.js`,
`app/static/vendor/chart.umd.min.js` · schema in `app/db.py` (`bi_*` tables).
Tests: `tests/test_bi_engine.py` (pure engine), `tests/test_bi_routes.py`
(routes/store/jobs, isolated DB).
