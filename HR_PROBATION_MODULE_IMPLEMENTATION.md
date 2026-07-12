# HR Probation Management — Implementation

A native module of the TC Mega Platform (not a separate app). One login, one
user/role system, one database, the platform's CSRF, i18n, audit, notification
and design system. Reachable at **`/hr/probation`** under the sidebar **Human
Resources** section.

## 1. Architecture & integration approach
- **Framework:** Flask app-factory + blueprints, exactly like the rest of the platform.
- **Blueprint:** `app/routes/probation.py` (`url_prefix="/hr/probation"`), registered in `app/__init__.py`.
- **Business logic:** `app/probation/` — `constants.py` (statuses, roles, the 8 official criteria, scoring, RBAC), `schema.py` (raw-SQL tables + seed), `services.py` (scoring, workflow, scope, dashboard, reminders, import, export).
- **DB:** the platform DB via `app/db.py` (`get_db`, SQLite dev / PostgreSQL on Render through the `_PGConn` shim). All tables `prob_*`. Schema is created idempotently by `create_and_seed(conn)` wired into `init_db()`.
- **Auth/RBAC:** the platform's `app/auth.py` + `app/security.py`. Module permissions/roles are declared in `constants.py` and folded into the catalogue via `_merge_module_rbac`. No second auth system, no separate port.
- **UI:** `app/templates/probation/*.html` extend the platform `base.html` and use its design tokens, sidebar, header, notifications, language switcher and dark/light themes. Trilingual via `data-i18n` keys in `static/i18n/{en,ar,tr}.json`.
- **Source of truth:** business rules (criteria, scoring, workflow) ported verbatim from the standalone Probation package; its technical stack (SQLAlchemy, own users table, port 5005) was **not** copied.

## 2. Main features delivered
- Probation **cases** linked to a code-keyed employee roster (imported; no duplication of platform identity).
- Configurable **evaluation** (8 criteria, 1–5, weighted; auto total/%, auto recommendation) with save-draft, submit, return-for-correction, version snapshots on return/reopen, print view.
- **Workflow:** one-level (manager → HR) or two-level (section head → manager → HR); HR outcomes Confirm / Extend / Do-not-confirm; return; admin reopen (audited); cancel.
- **Dashboards & reports:** active, due-in-30/7, overdue, pending manager/HR, confirmed/extended/not-confirmed, completion, average score, by-department, status distribution; CSV/Excel export; print-ready case summary.
- **Reminders & escalations:** configurable pre-due offsets (30/14/7/3/1), manager-SLA escalation to HR, overdue alerts — as in-app notifications with a history log; daily throttled auto-scan + on-demand run.
- **Roster import** (CSV/XLSX, idempotent by employee code, with a migrated/updated/skipped/error report).
- **Attachments-ready + comments** (public workflow comments vs confidential HR-only notes).
- **Audit** of every sensitive action (rich before/after in `prob_audit` + a line to the platform audit viewer).
- **Trilingual EN/AR/TR** with RTL for Arabic.

## 3. Roles & permissions
Permissions: `prob_view`, `prob_evaluate`, `prob_hr_review`, `prob_reports`, `prob_import`, `prob_admin`.

| Role | Permissions |
|---|---|
| `hr_probation_admin` | all probation perms |
| `hr_officer` | view, HR review, reports |
| `department_manager` | view, evaluate (own scope) |
| `section_head` | view, evaluate (own scope) |
| `executive_viewer` | view, reports (read-only) |
| `hr_user` | view, reports |
| `super_admin` | everything (`*`) |

**Scope:** managers/section heads see only cases in their `scope_department`/`scope_section` (two new nullable columns on `users`) or where they are the named manager/section head. Enforced **server-side** in every list and via an object-level `can_view_case` check on every case route (IDOR-safe). Confidential HR notes are stripped for non-HR viewers.

## 4. Workflow
```
Draft ─submit─▶ [Pending Manager]* ─▶ Pending HR Review ─▶ Approved (Confirm|Extend)
   ▲                                          │                └▶ Rejected (Not confirmed)
   └──────── Returned for correction ◀────────┘
Admin reopen (audited) can move a locked case back to Returned. Cancel closes a case.
* two-level flow only
```
Every transition writes a `prob_workflow_actions` row + an audit entry + notifications to the relevant role. Approve/reject lock the case.

## 5. Data model (tables, all `prob_*`)
`employees`, `templates`, `template_criteria`, `cases`, `evaluations`, `scores`,
`eval_versions`, `workflow_actions`, `extensions`, `comments`, `attachments`,
`notifications`, `audit`, `import_batches`, `settings`. FKs by id, indexes on
employee/status/end-date/department, soft-delete (`is_deleted`) where relevant.

## 6. Migration steps
No manual SQL. `init_db()` runs `create_and_seed()` every start (idempotent, non-destructive): creates missing tables, seeds the default template + 8 criteria + settings, and adds `users.scope_department` / `users.scope_section`. On Postgres the DDL is translated by `app.db.executescript`. Optional legacy import: see the User Guide → Import, or the roster importer (`services.import_rows`).

## 7. Configuration
No new environment variables required. Runtime settings live in `prob_settings` (editable at **/hr/probation/settings**): `threshold_pass` (75), `threshold_review` (60), `approval_flow`, `require_notes_below` (2), `reminder_offsets` (30,14,7,3,1), `manager_sla_days` (5), `default_probation_days` (90).

## 8. Notification scheduler
Reminders raise **in-app** notifications (the platform bell) + a `prob_notifications` history row (recipient, time, status, kind, case). A **daily throttled auto-scan** runs on dashboard load, so no external cron is required; a real scheduler may also call `app.probation.services.scan_reminders()` on a daily cron. **Email** is only used if the platform already has secure email configured; nothing external is sent in dev/test.

## 9. Testing completed
`tests/test_probation.py` (pytest): login required (302), permission denied for a non-HR user (403), all 9 pages render 200, full workflow (create→submit→approve→confirmed), empty-submit blocked, scoring & thresholds, manager scope isolation + IDOR guard, and a regression check that the dashboard/factory/maintenance still respond. Verified additionally via direct HTTP runs during build.

## 10. Known limitations / follow-ups
- Attachments have schema + access model + upload plumbing; antivirus scanning is a documented integration point (`scan_status` field), not yet wired to a live scanner.
- Export is CSV/Excel + a print-to-PDF case summary (no separate server-side PDF renderer for lists).
- Template editing UI shows the default template read-only; per-department template CRUD is scaffolded (tables support it) and can be exposed on request.
- Reminder scheduling is opportunistic (daily on dashboard load) unless a real cron calls `scan_reminders()`.
