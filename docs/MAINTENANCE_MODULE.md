# TC Platform — Maintenance & Spare Parts (CMMS) Module

A paperless factory maintenance & spare-parts control module (CMMS/EAM) fully
integrated into TC Platform — same auth, RBAC, theme, day/night, trilingual
EN/AR/TR + RTL, notifications, audit and design system.

Open it from the sidebar: **Maintenance & Spare Parts**.

## Pages (sidebar)
Maintenance Dashboard · New Maintenance Ticket · Tickets & Work Orders (Kanban) ·
Machine Registry · Machine Profile · Spare Parts Inventory · Spare Part Profile ·
Spare Part Requests · Approvals Center · Preventive Maintenance · Maintenance
Calendar · Stock Movements · Reports & Analytics · Settings / Master Data.

## Roles (demo accounts, password `Tc@12345`)
| Login | Role | Can |
|-------|------|-----|
| `maint` | Maintenance Manager | review/assign/close tickets, approve requests, manage PM |
| `tech` | Maintenance Technician | diagnosis, request parts, repair, confirm receiving |
| `store` | Storekeeper | issue parts, receive/adjust stock, inventory |
| `supervisor` | Production Supervisor | create tickets, confirm machine working |
| `factory` | Factory Manager | approve critical/expensive requests, executive view |
| `admin` | Super Admin | everything incl. Settings / master data |

Permissions: `maint_view, maint_ticket_create, maint_technician, maint_manage,
maint_approve, maint_store, maint_admin` — enforced on every page and action.

## The full workflow (paperless, audited)
1. **Report** — Supervisor/operator opens a ticket (`MNT-YYYY-000001`), picks the
   machine (auto-fills dept/area/line), priority, safety, production-stopped.
2. **Review & assign** — Manager assigns a technician → ticket becomes a work
   order (`WO-…`), with response/resolution targets.
3. **Diagnosis** — Technician records fault, root cause, action, and whether a
   spare is needed.
4. **Spare request** — Technician requests a part (`SPR-…`); the system checks
   stock instantly.
5. **Stock check** — In stock → approval cycle; out of stock → request marked
   *Out of Stock*, ticket → *Waiting Spare Parts*, store + manager notified.
6. **Approval** — Configurable matrix: normal part = Manager; critical/expensive =
   Manager + Factory Manager. Approve / Reject (justification required) / Return,
   with an approval timeline.
7. **Issue (atomic)** — Storekeeper issues: validates approval + stock, writes an
   issue voucher (`ISS-…`) and a stock movement (`STK-…`), decreases stock, and
   updates statuses — **all in one transaction that rolls back on any failure.
   Stock can never go negative.**
8. **Receive** — Technician confirms receiving → ticket → *Repair*.
9. **Repair proof** — Technician records action, machine-running status, old-part
   return → *Testing*.
10. **Test & close** — Test result recorded → *Resolved*; Manager closes →
    *Closed*; machine set back to *Running*, downtime + breakdown count updated.
    Reopen allowed within the configurable window.

Status transitions are guarded (e.g. Submitted can't jump to Closed; a rejected
request can't be issued). Every step is in the **audit trail** and shown on the
ticket timeline.

## Numbering
Tickets `MNT-YYYY-000001`, work orders `WO-…`, requests `SPR-…`, issue vouchers
`ISS-…`, stock movements `STK-…`, PM work orders `PM-…`.

## QR codes
Every machine, spare part and ticket has a QR (Machine Profile / Spare Profile /
Ticket pages). A machine QR opens the **New Ticket** form pre-filled with that
machine. Use the browser print button for printable labels.

## Reports (live CSV)
Tickets, machine downtime, spare consumption, inventory balance, low stock,
approval history, stock movements — under **Reports & Analytics** (and several
also export from their list pages).

## Seed data
5 machines (M-001…M-005), 10 spare parts (SP-001…SP-010) with opening stock,
3 tickets, 2 PM plans, 2 approval-matrix rules. Seeding is non-destructive and
only runs when the tables are empty.

## Try the main scenario in the UI
1. Sign in as `supervisor` → **New Maintenance Ticket** → machine **M-001** →
   describe the issue → Submit.
2. Sign in as `maint` → open the ticket → **Assign** a technician.
3. Sign in as `tech` → **Add diagnosis** (tick *spare needed*) → **Request spare
   parts** (e.g. SP-004).
4. Sign in as `maint` (and `factory` if the part is critical) → **Approvals
   Center** → Approve.
5. Sign in as `store` → **Spare Part Requests** → **Issue** (stock drops).
6. Sign in as `tech` → **Confirm receiving** → **Complete repair**.
7. Record **test** → sign in as `maint` → **Close**. Watch the dashboard, stock
   movements and audit timeline update.

## Tests
`pytest tests/test_maintenance.py` runs the full end-to-end scenario plus the
rejection, out-of-stock, issue-before-approval and negative-stock guards.

## Best-in-class & easy-for-workers features
Built to match leading CMMS apps while staying simple for non-technical floor staff:

- **My Work / Floor** (`/maintenance/floor`) — phone-first screen with huge touch
  buttons: **Report a Problem**, **Scan Machine QR**, and the technician's assigned
  work as big tap cards. First item in the sidebar.
- **QR scan to report** (`/maintenance/scan`) — device camera (BarcodeDetector)
  scans a machine's QR sticker → jumps into a pre-filled new ticket; manual
  machine-code entry as fallback.
- **Camera photo proof** — attach fault photos on the New Ticket form, repair
  photos on the repair step, and any photo on the ticket. Stored securely
  (type/size validated, random filenames), served **auth-gated** (never public),
  shown as a thumbnail gallery.
- **Machine health score (0-100)** — computed live from status, open/critical
  tickets, breakdowns and overdue PM; dashboard ranking (worst-first) + machine
  profile KPI.
- **Installable PWA** — manifest + service worker (`/sw.js`) + app icons; "Add to
  Home Screen" and the shell survives flaky factory Wi‑Fi (network-first cache).
- **Fully trilingual enums** — every status, priority, category and dropdown option
  renders in EN/AR/TR with RTL, not just the page chrome.

## Offline AI / Predictive Maintenance (no cloud, no internet)
`/maintenance/ai` — an **on-premise** engine (`app/maintenance/ai.py`, pure Python,
stdlib only — runs on the factory server or even an offline laptop):

- **Failure-risk prediction** — a 0-100 risk per machine from current health, MTBF
  (mean time between failures computed from its own ticket history), recent failure
  rate, open critical issues and overdue PM; with **predicted next-failure date** and
  a confidence level.
- **Prescriptive recommendations** — explainable "what to do" per machine (run PM,
  resolve critical, inspect, pre-stock spares, investigate recurring cause).
- **Spare consumption forecast + reorder recommendations** — predicted run-out date
  and suggested order quantity from issue history vs lead time.
- **Repeated-failure / anomaly detection** — machines failing ≥3× in 90 days.
- **Root-cause AI assist** — on the diagnosis form, suggests the most likely cause
  from that machine's (or its category's) history.
- **Smart triage** — as the reporter types the issue, a keyword NLP classifier
  suggests priority/severity and auto-extracts tags (one-tap Apply). Safety/stoppage
  words and machine criticality drive the score.
- **Smart assignment** — recommends the best technician by current workload + skill
  match (how many similar-category tickets they've resolved); pre-fills the assign box.
- **Repair-time (MTTR) estimate** — predicts the fix duration for a ticket from
  similar past tickets (machine → category → global cascade), shown on the ticket.
- **Alternative-parts suggester** — when a part is low/out of stock, recommends
  in-stock compatible substitutes (same category, preferring parts that share a
  compatible machine). Shown on the spare profile and on out-of-stock requests.
- **Low-stock / out-of-stock alerts** — deduplicated notifications pushed to the
  bell (warning when at/below reorder, critical when zero), auto-resolved on restock.

Surfaced on the maintenance dashboard (at-risk banner) and as inline assist. Fully
transparent statistical models — no black box, no data leaves the building.

## Roadmap (next)
Excel import, PDF reports, full month-grid calendar, multi-warehouse + purchase
orders, and IoT/sensor condition data feeding the offline AI for true
condition-based prediction. SSO and live cross-system KPIs remain platform-level.
