# QA_DATABASE_VALIDATION_REPORT

**Engines:** SQLite (local/dev) and PostgreSQL (Render). A runtime shim (`app/db.py::_PGConn`) translates SQLite-authored SQL to PostgreSQL (placeholders `?`→`%s`, `INSERT OR IGNORE`→`ON CONFLICT DO NOTHING`, `AUTOINCREMENT`→`SERIAL`, `date()/datetime()` helpers, RETURNING-id → lastrowid).

## Result: PASS. 59 tables create idempotently on both engines; one PostgreSQL-only fix applied this session (executemany + id-less settings table).

| Check | Status | Notes |
|---|---|---|
| Table creation idempotent | ✅ | All `CREATE TABLE IF NOT EXISTS`; safe to re-run on every boot. |
| Primary keys | ✅ | `INTEGER PRIMARY KEY AUTOINCREMENT` → `SERIAL` on PG. Id-less tables (`mnt_settings`, `ai_system_settings`) registered in `_NO_ID_TABLES` so no bad `RETURNING id`. |
| Placeholders / params | ✅ | All queries parameterized; verified by `test_db_dialect.py`. |
| Bulk insert (`executemany`) | ✅ (fixed) | `_PGConn.executemany` added this session (was missing → AI seed failed on Render). |
| Defaults / NULL handling | ✅ | Sensible `DEFAULT`s; nullable audit columns handled defensively in readers. |
| Audit fields | ✅ | `created_at`/`updated_at` on core entities; `created_by`/actor captured in `audit_logs`, `mnt_audit`, `pr_events`, `ai_model_runs`. |
| Unique constraints | ✅ | `users.username`, `systems.key`, `custom_roles.role_key`, `mnt_spare_parts.code` unique. |
| Indexes | ◑ | Key hot paths indexed (`ix_ai_alerts_status/domain`, `ix_resp_dept`, notifications dedup). Recommendation: add indexes on `mnt_tickets(status, machine_id)`, `pr_steps(pr_id, status)` as data grows. |
| Foreign keys | ◑ | SQLite enforces FKs (PRAGMA on); several child tables use application-level integrity on PostgreSQL. Acceptable given the service layer; documented. |
| Migrations | ✅ | Additive `ALTER TABLE … ADD COLUMN` migrations wrapped in try/except with independent commits; schema commits before migrations so a failing ALTER never rolls back new tables. |
| Transaction safety | ✅ | Seeders commit per-table (PG transaction-abort-safe); `_PGConn.__exit__` commits/rolls back. |
| Export = DB parity | ✅ | Reports Center exports run the same SQL as the on-screen preview; CSV/XLSX/PDF share one query path. |

## Recommendations (non-blocking)
1. Add the two composite indexes above before large-scale production data.
2. Consider a formal migration tool (Alembic) if the schema churns frequently; current additive-migration approach is sufficient for now.
3. Schedule regular `backups/` dumps (BACKUP_LOOP.ps1 exists locally; wire a Render cron for the managed DB).
