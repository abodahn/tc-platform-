# HR Probation Management — Deployment Checklist

The module ships as part of the platform; deployment is the platform's normal
push to Render. No new services, ports, databases or environment variables.

## 1. Pre-deploy
- [ ] Confirm the branch builds and imports (`python -c "from app import create_app; create_app()"`).
- [ ] Confirm no secrets are being committed (public repo): the module adds none.
- [ ] Review the diff: new `app/probation/*`, `app/routes/probation.py`, `templates/probation/*`, i18n keys, nav + RBAC + `init_db` wiring. Additive only.

## 2. Backup
- [ ] **PostgreSQL (Render):** take a database snapshot/backup before deploy (Render dashboard → the DB → Backups), so a rollback can restore data.
- [ ] Keep the previous deploy's commit SHA for a fast revert.

## 3. Migration
- [ ] No manual migration. `init_db()` runs `app.probation.schema.create_and_seed()` on boot: creates `prob_*` tables, seeds the default template + settings, and adds `users.scope_department` / `users.scope_section` (idempotent, non-destructive).
- [ ] It is wrapped in try/except in `init_db()` — a DDL hiccup cannot break the rest of the platform (tables simply retry next boot).

## 4. Environment configuration
- [ ] No new env vars required. Runtime behaviour is set in **/hr/probation/settings** (thresholds, approval flow, reminder offsets, SLA).

## 5. Scheduler / worker
- [ ] Reminders run via a **daily throttled auto-scan** on dashboard load — no cron needed to start.
- [ ] (Optional) For guaranteed daily reminders regardless of traffic, schedule a daily job calling `app.probation.services.scan_reminders()`.

## 6. File storage
- [ ] Attachments use the platform `UPLOAD_DIR` and `MAX_CONTENT_LENGTH`. On Render (ephemeral disk) use a persistent disk or object store if attachment retention is required. Antivirus scanning is a documented integration point (`prob_attachments.scan_status`).

## 7. Security checks
- [ ] Every probation route is `@login_required` + `@permission_required(...)`; case routes add an object-level scope check (`can_view_case`) — verify a non-HR account gets **403** and cannot open another employee's case by changing the id.
- [ ] Confidential HR notes are hidden from non-HR viewers.
- [ ] CSRF: all POST forms include the platform `_csrf` token.
- [ ] Audit: sensitive actions appear in `prob_audit` (no passwords/tokens stored).

## 8. Smoke tests (post-deploy)
- [ ] Sign in → sidebar shows **Human Resources**; open **Probation Dashboard** (200, KPIs render).
- [ ] Open a demo case, submit an evaluation, HR-approve → status **Approved / Confirm**.
- [ ] Switch to **العربية** — labels translate, layout is RTL, tables/badges intact; switch to **Türkçe**.
- [ ] **Reports → Export** downloads a CSV; a case **Print** view renders.
- [ ] Existing modules unaffected (dashboard, Smart Factory, Maintenance, Procurement respond).
- [ ] `pytest tests/test_probation.py -q` passes.

## 9. Rollback plan
- [ ] Revert to the previous commit SHA and redeploy (the module is additive; reverting the code leaves the extra `prob_*` tables harmless and unused).
- [ ] If a data restore is needed, restore the pre-deploy database backup from step 2.
