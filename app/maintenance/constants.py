"""
TC Platform — Maintenance module constants: statuses, valid transitions,
categories, and permission/role maps. Single source of truth for the workflow.
"""

# --- Ticket lifecycle -------------------------------------------------------
TICKET_STATUSES = [
    "draft", "submitted", "under_review", "assigned", "diagnosis",
    "spare_required", "waiting_stock", "waiting_approval", "approved_issue",
    "rejected", "parts_issued", "repair", "testing", "resolved", "closed",
    "cancelled", "reopened",
]

# Allowed status transitions (status -> set of next statuses). Admin/manager
# overrides are handled in the service layer; this guards normal flow.
TICKET_TRANSITIONS = {
    "draft": {"submitted", "cancelled"},
    "submitted": {"under_review", "assigned", "rejected", "cancelled"},
    "under_review": {"assigned", "rejected", "cancelled"},
    "assigned": {"diagnosis", "cancelled", "reopened"},
    "diagnosis": {"spare_required", "repair", "testing", "cancelled"},
    "spare_required": {"waiting_stock", "waiting_approval", "repair"},
    "waiting_stock": {"waiting_approval", "spare_required"},
    "waiting_approval": {"approved_issue", "rejected", "waiting_stock"},
    "approved_issue": {"parts_issued"},
    "parts_issued": {"repair"},
    "repair": {"testing", "spare_required"},
    "testing": {"resolved", "repair"},
    "resolved": {"closed", "reopened"},
    "closed": {"reopened"},
    "rejected": {"submitted", "cancelled"},
    "cancelled": set(),
    "reopened": {"under_review", "assigned", "diagnosis"},
}

# Kanban board columns: (i18n key, English title, statuses shown in the column).
# The FIRST status of a column is its PRIMARY one -- the status a card dropped
# into that column moves to. Lives here (not in the template) so the drag-drop
# endpoint and the board render from the same list.
TICKET_BOARD = [
    ("m.col_new", "New", ["submitted", "under_review", "reopened"]),
    ("m.col_assigned", "Assigned / Diagnosis", ["assigned", "diagnosis"]),
    ("m.col_spare", "Spare & Approval",
     ["spare_required", "waiting_stock", "waiting_approval", "approved_issue"]),
    ("m.col_repair", "Repair / Testing", ["parts_issued", "repair", "testing"]),
    ("m.col_done", "Resolved / Closed", ["resolved", "closed", "rejected", "cancelled"]),
]

PRIORITIES = ["critical", "high", "medium", "low"]
SEVERITIES = ["critical", "major", "moderate", "minor"]

# --- Spare part request -----------------------------------------------------
REQUEST_STATUSES = [
    "draft", "submitted", "checking_stock", "in_stock", "out_of_stock",
    "waiting_approval", "approved", "rejected", "returned", "issued",
    "received", "cancelled",
]

APPROVAL_STATUSES = [
    "pending", "approved", "rejected", "returned", "cancelled",
    "escalated", "delegated",
]

URGENCIES = ["urgent", "high", "normal", "low"]

# --- Stock movement ---------------------------------------------------------
MOVEMENT_TYPES = [
    "opening", "purchase_receiving", "adjustment", "issue", "return",
    "transfer", "scrap", "correction",
]

# --- Machine ----------------------------------------------------------------
MACHINE_STATUSES = [
    "running", "stopped", "under_maintenance", "waiting_spare",
    "under_testing", "decommissioned",
]
CRITICALITY = ["critical", "high", "medium", "low"]

ROOT_CAUSES = [
    "mechanical", "electrical", "pneumatic", "hydraulic", "sensor", "motor",
    "belt", "plc_control", "operator_misuse", "wear_tear", "lack_of_pm",
    "external", "unknown",
]

ISSUE_CATEGORIES = [
    "mechanical", "electrical", "pneumatic", "hydraulic", "sensor", "motor",
    "belt", "plc_control", "quality", "safety", "other",
]

# --- Spare part -------------------------------------------------------------
SPARE_CATEGORIES = [
    "mechanical", "electrical", "pneumatic", "hydraulic", "sensor", "motor",
    "belt", "bearing", "needle_textile", "cutter_blade", "lubrication",
    "control_plc", "safety", "other",
]
UOM = ["pcs", "set", "m", "kg", "l", "roll", "box"]

# --- Preventive maintenance -------------------------------------------------
PM_FREQUENCIES = ["daily", "weekly", "monthly", "quarterly", "yearly"]
PM_STATUSES = ["scheduled", "due_soon", "overdue", "in_progress", "completed",
               "missed", "cancelled"]

# --- Permissions (added to platform RBAC) ----------------------------------
MAINT_PERMISSIONS = [
    "maint_view",          # see the module, dashboard, lists
    "maint_ticket_create", # raise tickets
    "maint_technician",    # diagnosis, request parts, repair updates
    "maint_manage",        # review/assign/close, manage PM
    "maint_approve",       # approvals center actions
    "maint_store",         # inventory + issue/receive/adjust
    "maint_admin",         # settings, master data, approval matrix
]

# Map new maintenance roles -> the maintenance permissions they hold.
MAINT_ROLE_PERMS = {
    "maintenance_manager": ["maint_view", "maint_ticket_create", "maint_manage",
                            "maint_approve", "maint_technician"],
    "maintenance_technician": ["maint_view", "maint_ticket_create", "maint_technician"],
    "storekeeper": ["maint_view", "maint_store", "maint_approve"],
    "production_supervisor": ["maint_view", "maint_ticket_create"],
    "factory_manager": ["maint_view", "maint_approve", "maint_manage"],
    # grants for existing platform roles:
    "production_manager": ["maint_view", "maint_ticket_create", "maint_approve"],
    "it_director": ["maint_view", "maint_admin", "maint_manage"],
    "it_manager": ["maint_view", "maint_admin"],
    "finance_user": ["maint_view"],
    "executive_viewer": ["maint_view"],
}

# Labels for the new roles (shown in admin role dropdown).
MAINT_ROLE_LABELS = {
    "maintenance_manager": "Maintenance Manager",
    "maintenance_technician": "Maintenance Technician",
    "storekeeper": "Storekeeper / Warehouse",
    "production_supervisor": "Production Supervisor",
    "factory_manager": "Factory Manager",
}
