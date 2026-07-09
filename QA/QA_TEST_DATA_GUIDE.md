# QA_TEST_DATA_GUIDE

## How test data is created
All demo/test data is **seeded idempotently on boot** by `init_db()` (only when a table is empty) and by each module's `create_and_seed`. Nothing is fabricated at report time.

- **Users/roles:** super-admin (`admin`), plus demo accounts for every role — `director, agent, exec, maint, tech, store, supervisor, factory, warehouse, purchasing, finance, cfo, ceo` (shared demo password `Admin@1122`).
- **Systems:** the 4 integrated systems + future modules registry.
- **Maintenance:** machines, spare parts, tickets (incl. critical/aged), PM plans.
- **Procurement:** vendors, budgets, responsibility matrix seed; PRs created via tests/UI.
- **Production:** lines, downtime, quality.
- **AI operational domains (with deliberate edge cases):**
  - `assets` — 30 rows incl. expired warranties, >5-year-old, high maintenance-cost, poor condition, incomplete records.
  - `payroll_rows` — old (June) + new (July) sheets with **injected anomalies**: zero net, huge deduction, +9000 spike, a duplicate employee code, and one employee dropped from the new sheet.
  - `hr_probation` — overdue, not-started, low-score, passed.
  - `paper_usage` — 4 months per dept with HR/Finance July **spikes**.

## Edge/bad data covered
Missing fields, duplicate IDs, invalid/expired/future dates, negative & zero values, long text, Arabic/Turkish names, special characters, high-risk SLA tickets, broken payroll formulas/values.

## Fixtures
- `tests/conftest.py` — app/client fixtures, DB reset, login helpers.
- `tests/fixtures/test_data_seed.py` — programmatic seed entry point (calls the app seeders + adds extra edge records) for standalone data loading.

## Safety
- Local dev uses `platform.db`; the **preview** server uses an isolated `preview.db`.
- On Render (PostgreSQL) the same seeders run; production should load **real** data into `assets`, `payroll_rows`, `hr_probation`, `paper_usage` and then Recompute.
