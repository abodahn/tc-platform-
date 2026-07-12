# HR Probation Management — User Guide

For HR officers, managers, section heads and approvers. Open it from the sidebar:
**Human Resources → Probation Dashboard** (or go to `/hr/probation`). Sign in with
your normal platform account — there is no separate login. Switch language
(EN / العربية / Türkçe) from the top-right at any time; Arabic is right-to-left.

## Who can do what
- **HR Officer / HR Probation Admin** — initiate cases, review, take the final decision, run reminders, reports, settings.
- **Direct Manager / Section Head** — evaluate the employees in your scope only.
- **Executive Viewer** — read-only dashboards and reports.

## 1. See the picture — Dashboard
KPIs for active cases, due in 30 / 7 days, overdue, pending manager, pending HR, and outcomes (confirmed / extended / not confirmed), plus by-department and status charts.

## 2. Start a probation evaluation (HR)
1. **Human Resources → Initiate Evaluation**.
2. Find the employee (search by name/code). If the person isn't listed, **Import Roster** first (see §7).
3. Click **Open case** — this creates the case and a blank evaluation and takes you to it.

## 3. Evaluate an employee (Manager / Section Head)
1. Open **My Evaluations** (or the case from **Probation Cases**).
2. For each of the 8 criteria pick a score **1–5**. A **note is required** for any score of 2 or below.
3. Fill **Strengths / Improvement / Training**, choose the **Final recommendation**, and write **Manager comments** (required).
4. **Save draft** any time. When ready, **Submit for review** — the score, %, and an automatic recommendation are calculated and the case moves to HR (or to the manager first in a two-level flow).

## 4. HR review & decision
On a case that is **Pending HR Review**, HR sees the **HR review & decision** panel:
- **Approve** — pick the outcome **Confirm employment / Extend probation / Do not confirm**. Changing the recommendation requires an override justification. Approving **locks** the case.
- **Return for correction** — send it back to the evaluator with a reason.
- **Do not confirm** — record a not-confirmed outcome (reason required).

## 5. Extend, cancel, reopen
- **Extend probation** — set a new end date + reason; the employee's end date updates and an extension is logged.
- **Cancel case** — withdraw a case that's no longer needed.
- **Reopen** (HR Admin only) — move a locked case back for correction; a reason is mandatory and the action is fully audited.

## 6. Track & report
- **Probation Cases** — search and filter by status, department and outcome; click a row to open the case. **Export** to Excel/CSV.
- **Reports & Analytics** — outcomes, completion rate, average score, by-department (respecting your data scope).
- Each case has a **Print** view for a clean PDF (Print → Save as PDF).

## 7. Import the roster (HR)
**Import Roster** → upload a `.csv` or `.xlsx`. Matching is by **employee code**, so re-importing **updates** people and never creates duplicates. Recognised columns: `employee_code, employee_name, department, section, designation, direct_manager, section_head, joining_date, probation_end_date` (common Arabic headers are also recognised). You get a report of created / updated / skipped / errors.

## 8. Reminders & due dates (HR)
**Reminders & Due Dates** shows due-soon, overdue and pending counts plus the notification history. Reminders are raised automatically each day (before the due date, on manager delays, and when overdue) and appear in your 🔔 bell. Use **Run reminder scan now** to refresh on demand.

## 9. Settings (HR Admin)
**Module Settings** — confirm/HR-review score thresholds, one- or two-level approval flow, the “require a note below” score, reminder day-offsets, and the manager SLA before escalation. The default 8-criterion template is shown for reference.

## Notes on confidentiality
- Managers and section heads see **only** their own employees. Executives see read-only summaries without confidential comments.
- HR can mark a comment **HR-only (confidential)** — it is hidden from non-HR viewers.
- Everything sensitive is recorded in the audit trail.
