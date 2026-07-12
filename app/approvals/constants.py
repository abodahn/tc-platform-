"""
TC Platform — Procurement & Approvals constants: the approval ladder, the
amount-threshold routing matrix, statuses, and the permission/role maps that
extend platform RBAC. Single source of truth for the procurement workflow.

Ladder (matches the paper Purchase Request Form signature blocks):
    Requester -> Warehouse -> Factory Manager -> Purchasing -> Finance -> CFO -> CEO

The requester is the originator (they "sign" by submitting). The stages after
them are the approval ladder; which stages apply depends on the PR total via
APPROVAL_MATRIX, so a small PR needs fewer signatures than a large one.
"""

# --- Request lifecycle ------------------------------------------------------
PR_STATUSES = [
    "draft",       # being filled by the requester
    "pending",     # in the approval ladder, waiting on the current stage
    "approved",    # every required stage approved
    "rejected",    # a stage rejected it (bounces back to the requester)
    "po_issued",   # a Purchase Order has been generated from it
    "partially_received",  # some line items received, not all
    "received",    # goods/services received (delivery confirmed)
    "closed",      # delivered / completed
    "cancelled",   # withdrawn by the requester or an admin
]

PAYMENT_STATUSES = ["unpaid", "partial", "paid"]

# A step (one rung of the ladder) is one of:
STEP_STATUSES = ["pending", "approved", "rejected", "skipped"]

CURRENCIES = ["EGP", "USD", "EUR", "TRY"]
UNITS = ["Pcs", "Set", "Box", "Roll", "Meter", "Kg", "Liter", "Service", "Lot"]
PAYMENT_CONDITIONS = ["Advanced Payment", "On Delivery", "Net 15", "Net 30",
                      "Net 60", "Cash", "Cheque", "Installments"]
DELIVERY_CONDITIONS = ["T&C Warehouse", "On-site / Factory floor", "Vendor premises",
                       "Courier / Shipping", "Ex-Works (EXW)", "FOB", "CIF"]
# Default department list (admins can add more via the responsibility matrix
# settings; any department already used by a budget or matrix is merged in too).
DEPARTMENTS = ["General Maintenance", "Production", "Cutting", "Sewing", "Finishing",
               "Embroidery", "Quality", "Warehouse", "IT", "Finance",
               "Human Resources", "Administration", "Procurement"]

# --- The approval ladder ----------------------------------------------------
# Ordered stage keys (excluding the requester, who is the originator).
LADDER = ["warehouse", "factory_manager", "purchasing", "finance", "cfo", "ceo"]

# Human labels for each stage (EN — AR/TR carried by i18n on the client).
STAGE_LABELS = {
    "requester": "Requester",
    "warehouse": "Warehouse",
    "factory_manager": "Factory Manager",
    "purchasing": "Purchasing",
    "finance": "Finance",
    "cfo": "CFO",
    "ceo": "CEO",
}

# Which platform roles may act on each stage. super_admin (and anyone with the
# proc_admin permission) can act on ANY stage — handled in the service layer.
STAGE_ROLES = {
    "warehouse": {"storekeeper", "warehouse_manager"},
    "factory_manager": {"factory_manager"},
    "purchasing": {"purchasing_manager"},
    "finance": {"finance_manager", "finance_user"},
    "cfo": {"cfo"},
    "ceo": {"ceo"},
}

# --- Amount-threshold routing (EGP-equivalent) ------------------------------
# A stage is included in a PR's ladder only if the PR total is >= its threshold.
# Warehouse / Factory / Purchasing are always required (threshold 0). Finance,
# CFO and CEO join as the amount grows. Tunable here without touching logic.
# Example: total 48,000 -> warehouse, factory_manager, purchasing, finance, cfo
# = 5 approvers + the requester = 6 signatures, matching the real paper form.
APPROVAL_MATRIX = {
    "warehouse": 0,
    "factory_manager": 0,
    "purchasing": 0,
    "finance": 10_000,
    "cfo": 25_000,
    "ceo": 100_000,
}


def build_ladder(total):
    """Return the ordered list of stage keys required for a PR of `total`."""
    try:
        t = float(total or 0)
    except (TypeError, ValueError):
        t = 0.0
    return [s for s in LADDER if t >= APPROVAL_MATRIX.get(s, 0)]


# --- Pricing gate (controlled Procure-to-Pay) -------------------------------
# A purchase request is raised WITHOUT any commercial value: the requester only
# states what they need (item, qty, unit, spec). Pricing is entered later, by
# Purchasing, at the purchasing stage — that's the "pricing gate". Only then do
# the value-based financial approvals (Finance / CFO / CEO) join the ladder.
PRICING_STATUSES = ["unpriced", "priced"]

# Stages that are always required regardless of value (threshold 0): these form
# the "demand approval" part of the ladder that runs BEFORE pricing.
DEMAND_STAGES = [s for s in LADDER if APPROVAL_MATRIX.get(s, 0) <= 0]

# Value-gated stages (threshold > 0): they only join the ladder once Purchasing
# has priced the request and the total clears their threshold.
VALUE_STAGES = [s for s in LADDER if APPROVAL_MATRIX.get(s, 0) > 0]

# The stage at which Purchasing enters pricing (the gate). A request cannot pass
# this stage until it has been priced.
PRICING_GATE_STAGE = "purchasing"

# Commercial fields the requester must NEVER set (enforced server-side): they are
# stripped on create/edit for anyone without proc_purchasing, and only Purchasing
# can fill them through the pricing action.
REQUESTER_BLOCKED_HEADER_FIELDS = ("tax_rate", "payment_condition")
REQUESTER_BLOCKED_ITEM_FIELDS = ("unit_price", "est_cost")


def value_stages_for(total, matrix=None):
    """Ordered value-gated stages a PR of `total` requires under `matrix`
    (defaults to the global APPROVAL_MATRIX)."""
    m = matrix or APPROVAL_MATRIX
    try:
        t = float(total or 0)
    except (TypeError, ValueError):
        t = 0.0
    return [s for s in VALUE_STAGES if (s in m if matrix else True) and t >= m.get(s, 0)]


# --- Parallel approval groups ----------------------------------------------
# Stages that sit in the SAME set here run in parallel (same ladder rung): all of
# them must approve before the request advances, and any single one can reject.
# Empty by default = strictly sequential (the current, tested behaviour). Put a
# set like {"finance", "factory_manager"} here to make those two sign at once.
PARALLEL_GROUPS = []


def rungs_from_stages(stages):
    """Group an ordered stage list into rungs; stages in the same PARALLEL_GROUPS
    set share a rung (run in parallel). With no groups every rung has one stage."""
    rungs, seen = [], set()
    for s in stages:
        if s in seen:
            continue
        group = next((g for g in PARALLEL_GROUPS if s in g), None)
        if group:
            rung = [x for x in stages if x in group]
            rungs.append(rung)
            seen.update(rung)
        else:
            rungs.append([s])
            seen.add(s)
    return rungs


def ladder_rungs(total):
    """Return the default (global-matrix) ladder as ordered rungs."""
    return rungs_from_stages(build_ladder(total))


# --- SLA: how long a single approval stage may sit before it's "overdue" ----
# Drives the aging badge on the ladder and the escalation job. Tunable here.
SLA_HOURS_PER_STAGE = 48       # a stage older than this is overdue
SLA_WARN_HOURS = 24            # amber "due soon" threshold


# --- Permissions (added to platform RBAC) ----------------------------------
PROC_PERMISSIONS = [
    "proc_view",       # see the module, lists, own requests
    "proc_create",     # raise purchase requests
    "proc_approve",    # act on an approval stage the user is eligible for
    "proc_purchasing", # purchasing actions: issue PO, manage vendors
    "proc_admin",      # settings, approval matrix, act on any stage, vendors
]

# New roles introduced by this module -> the procurement permissions they hold.
PROC_ROLE_PERMS = {
    "purchasing_manager": ["proc_view", "proc_create", "proc_approve", "proc_purchasing"],
    "finance_manager": ["proc_view", "proc_create", "proc_approve"],
    "cfo": ["proc_view", "proc_approve"],
    "ceo": ["proc_view", "proc_approve"],
    "warehouse_manager": ["proc_view", "proc_create", "proc_approve"],
    # grants for roles that already exist on the platform:
    "storekeeper": ["proc_view", "proc_create", "proc_approve"],
    "factory_manager": ["proc_view", "proc_create", "proc_approve"],
    "finance_user": ["proc_view", "proc_approve"],
    "production_manager": ["proc_view", "proc_create"],
    "production_supervisor": ["proc_view", "proc_create"],
    "maintenance_manager": ["proc_view", "proc_create"],
    "it_director": ["proc_view", "proc_admin"],
    "it_manager": ["proc_view", "proc_create"],
    "executive_viewer": ["proc_view"],
    "normal_user": ["proc_view", "proc_create"],
}

# Labels for the brand-new roles (shown in the admin role dropdown).
PROC_ROLE_LABELS = {
    "purchasing_manager": "Purchasing Manager",
    "finance_manager": "Finance Manager",
    "cfo": "Chief Financial Officer",
    "ceo": "Chief Executive Officer",
    "warehouse_manager": "Warehouse Manager",
}


def stage_label(stage):
    return STAGE_LABELS.get(stage, stage.replace("_", " ").title())
