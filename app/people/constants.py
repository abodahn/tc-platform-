"""
HR Core constants — attendance statuses, leave types & entitlements, the sewing
operation list the skill matrix is built on, the incentive parameters, and RBAC.

Pure data only: app.security imports this at module-import time.
"""

# --- attendance ----------------------------------------------------------
ATT_STATUS = ["present", "absent", "late", "leave", "holiday", "off"]

# A scheduled working day — the DENOMINATOR of attendance % and absenteeism %.
# 'holiday' and 'off' are not scheduled, so they never dilute either figure.
WORKING_STATUSES = ("present", "absent", "late", "leave")
# Bodies actually on the floor. 'late' still counts as attended (it is paid time).
PRESENT_STATUSES = ("present", "late")
# Definitively NOT on the floor: these days can never carry worked hours or
# overtime, whatever the day-entry form still has in its clock-time inputs.
# 'holiday' and 'off' belong here too — the day-entry form keeps the previously
# saved clock pair and OT in its inputs, so flipping a worked day to a holiday
# would otherwise re-post 9 h + OT against a day nobody was scheduled. Somebody
# who really did work a holiday is marked present/late, which is what makes the
# hours land in the attendance and overtime figures honestly.
OFF_FLOOR_STATUSES = ("absent", "leave", "holiday", "off")

STANDARD_DAY_HOURS = 8.0        # used when a day has no check-in/out pair

# The day-entry page lists this many employees and must pre-fill every one of
# them, or an unlisted-but-recorded day posts back as a blank 'present' and
# overwrites what was actually marked.
ROSTER_PAGE_LIMIT = 500

# --- leave ---------------------------------------------------------------
LEAVE_TYPES = ["annual", "sick", "unpaid", "other"]
# Unpaid leave carries no entitlement, so it is never balance-checked or deducted.
UNBALANCED_LEAVE_TYPES = ("unpaid",)
LEAVE_STATUS = ["pending", "approved", "rejected", "cancelled"]
# A single request longer than a year is a typo, not a leave plan. Without this
# cap an unpaid request (which carries no entitlement to check) can book a
# six-figure day count straight into the ledger.
MAX_LEAVE_DAYS = 365.0

# Default yearly entitlement in days, created on first use per employee/type.
DEFAULT_ENTITLEMENT = {"annual": 21.0, "sick": 7.0, "other": 3.0, "unpaid": 0.0}

# --- skill matrix --------------------------------------------------------
# Sewing/finishing operations a garment operator is rated on. Free text is
# allowed on write; this is the picker list.
OPERATIONS = [
    "Overlock", "Shoulder join", "Sleeve attach", "Side seam", "Collar attach",
    "Hem bottom", "Pocket attach", "Waistband attach", "Belt loops",
    "Buttonhole", "Button attach", "Bartack", "Final assembly",
    "Cutting", "End-line QC", "Packing",
]

SKILL_LEVELS = [1, 2, 3, 4, 5]      # 1 trainee .. 5 trainer
# Level 1 is a trainee under supervision — not deployable on a live line, so
# skill-based allocation only ever proposes level >= 2.
CAPABLE_MIN_LEVEL = 2

# --- piece-rate / incentive ---------------------------------------------
# Earned-minute model. Incentive is paid only on minutes earned ABOVE the
# threshold efficiency, so an operator below standard earns 0 (never negative).
INCENTIVE_THRESHOLD_PCT = 75.0
DEFAULT_RATE_PER_MINUTE = 0.30      # currency per earned minute above threshold
INCENTIVE_CURRENCY = "EGP"
# A sewing operator peaks around 130%; 4x standard is physically unreachable, so an
# entry above this is a typo — almost always an extra zero in the piece count. It has
# to be refused rather than clamped: at 0.30/min a single 9,000-piece slip on a 480
# minute shift pays out 1,484,892 instead of 40.50.
MAX_EFFICIENCY_PCT = 400.0

# --- alerting ------------------------------------------------------------
# Absenteeism on a single day above this % raises one bell alert for that day.
ABSENTEEISM_ALERT_PCT = 10.0

# --- RBAC (merged into the platform catalogue by app.security) ------------
PPL_PERMISSIONS = [
    "ppl_view",     # see HR core dashboards, attendance, leave, skills, incentive
    "ppl_manage",   # record attendance, skills, piece-rate; raise leave requests
    "ppl_approve",  # approve / reject / cancel leave (separate authority)
]

PPL_ROLE_PERMS = {
    "hr_probation_admin": ["ppl_view", "ppl_manage", "ppl_approve"],
    "hr_officer": ["ppl_view", "ppl_manage", "ppl_approve"],
    "hr_user": ["ppl_view", "ppl_manage"],
    "department_manager": ["ppl_view", "ppl_manage", "ppl_approve"],
    "section_head": ["ppl_view", "ppl_manage", "ppl_approve"],
    "production_manager": ["ppl_view", "ppl_manage"],
    "executive_viewer": ["ppl_view"],
}

PPL_ROLE_LABELS = {}    # reuses roles probation/security already define

PERMISSION_LABELS = {
    "ppl_view": "HR Core: view attendance, leave, skills & incentive",
    "ppl_manage": "HR Core: record attendance, skills & piece-rate",
    "ppl_approve": "HR Core: approve or reject leave requests",
}
