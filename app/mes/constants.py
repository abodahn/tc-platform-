"""
MES constants — hour slots, RAG thresholds, downtime reason codes, WIP sections,
and this module's RBAC (folded into the platform catalogue by app.security).

Pure data only: app.security imports this at import time, so no Flask/db imports.
"""

# One clock hour per board cell. Every time figure in this module is in MINUTES.
SLOT_MINUTES = 60

# A standard sewing shift. The board is a line x slot grid over exactly these.
HOUR_SLOTS = [
    "08:00-09:00", "09:00-10:00", "10:00-11:00", "11:00-12:00",
    "12:00-13:00", "13:00-14:00", "14:00-15:00", "15:00-16:00",
]

# Sanity ceilings on operator input. These are trust-boundary guards, not business
# limits: the qty columns are INTEGER (4 bytes on PostgreSQL), so an absurd paste is
# either a hard 500 or a roll-up that reads in the billions. The biggest real shift
# is a few thousand pieces, the slowest real garment a few hundred SMV, and a loss
# cannot outlast the day it is booked against.
MAX_QTY = 1_000_000
MAX_SMV = 1_000.0
MAX_DOWNTIME_MIN = 1440.0
# Production may be pre-loaded for a planned day, but a work_date beyond this is a
# typo'd year. There is no edit or delete screen, so it is refused on the way in.
MAX_FUTURE_DAYS = 365

# Widest row id an INTEGER column holds. Werkzeug's <int:> URL converter accepts a
# number of ANY length, and a value past this raises out of the DB driver as a 500
# instead of the 404 it should be.
MAX_ID = 2_147_483_647

# Achievement RAG on actual/target %. Green = on plan, amber = recoverable this
# shift, red = the hour is lost and the supervisor is told now, not tomorrow.
RAG_GREEN = 95.0
RAG_AMBER = 85.0

# A line losing more than this many minutes in one day escalates to the bell.
DOWNTIME_ALERT_MIN = 60.0

# Reason codes. Un-coded downtime is unactionable, so "other" always needs a note.
DOWNTIME_REASONS = [
    "machine_breakdown", "no_feeding", "power",
    "quality_rework", "no_operator", "changeover", "other",
]

# Cut-to-dispatch flow a bundle travels. Order is the stage sequence; the last
# entry is finished goods and is therefore NOT counted as work-in-progress.
SECTIONS = ["cutting", "sewing", "washing", "finishing", "packing", "dispatch"]

BUNDLE_STATUS = ["created", "in_progress", "completed", "rejected"]

# ==========================================================================
# RBAC — merged into the platform catalogue by app.security._merge_module_rbac
# ==========================================================================
MES_PERMISSIONS = [
    "mes_view",    # see the hourly board, OEE, WIP
    "mes_entry",   # record hourly output, downtime, bundles and bundle scans
]

MES_ROLE_PERMS = {
    "production_manager": ["mes_view", "mes_entry"],
    "line_supervisor": ["mes_view", "mes_entry"],   # new role: the hourly-board owner
    "executive_viewer": ["mes_view"],
    "compliance_officer": ["mes_view"],
}

MES_ROLE_LABELS = {
    "line_supervisor": "Line Supervisor",
}

PERMISSION_LABELS = {
    "mes_view": "MES: view hourly board, OEE & WIP",
    "mes_entry": "MES: record hourly output, downtime & bundle scans",
}
