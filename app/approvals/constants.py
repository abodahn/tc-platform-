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


# --- Segregation of Duties (SoD) --------------------------------------------
# Independence rules enforced by the approval engine (services.act_on_step):
#   (a) self-approval block — the requester of a PR may never sign any of its
#       approval stages (raising the request IS their signature);
#   (b) dual-role block — one person may not sign two DIFFERENT stages of the
#       same request, not even when a delegation makes them eligible.
# SOD_ADMIN_EXEMPT: platform admins (super_admin, or any role holding the
# proc_admin permission) bypass both rules so a small team can still walk a
# request through the whole ladder. Set to False later for strict mode, where
# admins are subject to SoD exactly like everyone else.
SOD_ADMIN_EXEMPT = True


# --- SoD escalation chain (one level up the org chart) ----------------------
# Rule (a) above creates a deadlock the moment the requester is the ONLY person
# eligible to sign a rung: the Warehouse Manager raises a request, the warehouse
# rung is his, and nobody else holds a warehouse role -> the request waits for a
# signature that can never legally arrive. The engine then escalates that rung
# ONE LEVEL UP: the superior of the blocked role signs it instead.
#
# Seeded into proc_escalations (INSERT OR IGNORE) so a Procurement admin can edit
# the chain afterwards without a code change. A role with NO row, or a row whose
# superior_role is blank, means "nobody above" — the terminal case.
DEFAULT_ESCALATION = {
    "storekeeper": "warehouse_manager",
    "warehouse_manager": "factory_manager",
    "factory_manager": "ceo",
    "purchasing_manager": "cfo",
    "finance_user": "finance_manager",
    "finance_manager": "cfo",
    "cfo": "ceo",
    "ceo": "",                       # the top of the chart: nobody above
}

# Hard stop on the climb. An admin can configure a cycle (A -> B -> A); the
# resolver already carries a visited-set, and this bound is the second belt so a
# pathological chain can never spin on a money path.
ESCALATION_MAX_DEPTH = 8


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


# --- RFQ / competitive-quotation control (P3) -------------------------------
# Orders at/above RFQ_VALUE_THRESHOLD (EGP-equivalent total) must carry at
# least RFQ_QUOTE_MIN competing vendor quotes before the Purchasing stage can
# sign off — unless Purchasing records a single-source justification on the PR
# (pr_requests.single_source_reason). Enforced by services.rfq_gate_check.
RFQ_QUOTE_MIN = 2            # competitive quotes required
RFQ_VALUE_THRESHOLD = 25000  # EGP-equivalent total at/above which the rule applies


# --- Payment cap tolerance --------------------------------------------------
# Rounding/bank-charge slack allowed on the payment caps in services.add_payment
# (cumulative paid vs the PO grand total, and vs the invoiced gross total). The
# hard floor of 1 currency unit inside add_payment is separate and stays fixed.
# NOTE: services.three_way_match uses its own 1% for the MATCH verdict; that one
# is deliberately NOT configurable (see docs/still_hardcoded in the workflow page).
PAYMENT_TOLERANCE_PCT = 1.0


# ===========================================================================
# Workflow & Governance — the admin-configurable surface
# ===========================================================================
# Every knob below is read as "DB override (proc_settings) -> constant here".
# An ABSENT row means "use the constant", so a database with no override rows
# behaves exactly as the code always has.
#   kind: num | int | bool  — how a stored string is coerced
#   min/max: inclusive bounds. A stored value that is blank, non-numeric, NaN,
#            or outside the bounds is IGNORED and the constant is used, so a bad
#            entry can never disable a control or open a money gate.
WORKFLOW_SETTINGS = {
    # A competitive-quote minimum below 1 would silently switch the RFQ gate off,
    # so 1 is the floor rather than 0.
    "rfq_quote_min": {"kind": "int", "min": 1, "default": RFQ_QUOTE_MIN},
    # 0 is legitimate here: "every purchase needs competitive quotes".
    "rfq_value_threshold": {"kind": "num", "min": 0, "default": RFQ_VALUE_THRESHOLD},
    "sod_admin_exempt": {"kind": "bool", "default": SOD_ADMIN_EXEMPT},
    # Capped at 100%: a fat-fingered 5000 would let someone pay 51x the PO.
    "payment_tolerance_pct": {"kind": "num", "min": 0, "max": 100,
                              "default": PAYMENT_TOLERANCE_PCT},
}

# Default explanation text per ladder stage. Seeded into proc_stage_meta and
# used as the fallback whenever an admin resets a stage's explanation.
STAGE_EXPLAIN = {
    "requester": (
        "Any user with “Raise purchase requests” opens the request and states what is "
        "needed: item, quantity, unit, specification and the department it is for. "
        "Submitting IS the requester's signature, which is why they may never sign an "
        "approval stage of their own request. Requesters cannot enter money either — "
        "unit price, estimated cost, tax rate and payment condition are stripped from "
        "anything they submit and are filled in later by Purchasing."),
    "warehouse": (
        "Warehouse checks the store before any money is committed: is the item already "
        "on the shelf, how much was last ordered and at what price. This is a demand "
        "stage — always required, whatever the request is worth."),
    "factory_manager": (
        "The Factory Manager confirms the request is operationally necessary and "
        "correctly specified for the asset, machine or line it is raised for. Demand "
        "stage — always required, whatever the request is worth."),
    "purchasing": (
        "Purchasing owns the commercial side: choose the vendor, enter the pricing, set "
        "the exchange rate on a foreign-currency request, collect competing quotes (or "
        "record a single-source justification) and — once every signature is in — issue "
        "the Purchase Order. Two gates fire at this stage: the request cannot leave it "
        "unpriced, and a high-value request cannot leave it without competitive quotes."),
    "finance": (
        "Finance checks the priced request against the department budget, the tax and the "
        "payment terms before the money is committed. Value stage — it joins the ladder "
        "only when the EGP-equivalent total reaches its threshold."),
    "cfo": (
        "The CFO authorises significant committed spend and confirms it is funded. "
        "Value stage — it joins the ladder only when the EGP-equivalent total reaches "
        "its threshold."),
    "ceo": (
        "The CEO is the final authority on major spend. Value stage — it joins the "
        "ladder only when the EGP-equivalent total reaches its threshold. When this "
        "last signature lands the request becomes Approved and a Purchase Order number "
        "is drafted automatically."),
}

# Default explanation per role that signs somewhere in the cycle.
ROLE_EXPLAIN = {
    "storekeeper": (
        "Holds the store. Signs the Warehouse stage: confirms live stock, last order "
        "quantity and last order price, and receives the goods against the PO."),
    "warehouse_manager": (
        "Accountable for the store as a whole. Signs the Warehouse stage and is the "
        "escalation point when a storekeeper is unavailable."),
    "factory_manager": (
        "Accountable for the plant. Signs the Factory Manager stage: confirms the "
        "request is operationally justified and correctly specified."),
    "purchasing_manager": (
        "Runs procurement. Prices requests, sets FX rates, collects and compares vendor "
        "quotes, records single-source justifications, signs the Purchasing stage and "
        "issues Purchase Orders."),
    "finance_manager": (
        "Accountable for financial control. Signs the Finance stage, owns the department "
        "budgets, registers vendor invoices and records payments."),
    "finance_user": (
        "Finance team member. Signs the Finance stage and handles invoice registration "
        "and payment recording day to day."),
    "cfo": (
        "Chief Financial Officer. Signs the CFO stage for significant committed spend "
        "and is the authority on budget breaches."),
    "ceo": (
        "Chief Executive Officer. Signs the CEO stage — the final authority on the "
        "largest purchases."),
}

# Default body per free-text documentation block (proc_doc sections).
DOC_SECTIONS = {
    "overview": (
        "A purchase request starts as pure demand: the requester says WHAT is needed, "
        "never what it costs. It is then routed up a ladder of signatures. Warehouse, "
        "Factory Manager and Purchasing always sign. Purchasing enters the pricing, and "
        "only then do the value-based approvals (Finance, CFO, CEO) join the ladder — "
        "each one from its own amount upwards, compared on the EGP-equivalent total so a "
        "foreign-currency request is converted first. Every approver stamps their saved "
        "digital signature, and every signature is recorded as a separately verifiable "
        "event. When the last stage approves, a Purchase Order is drafted automatically; "
        "Purchasing issues it (subject to the department budget), the goods are received "
        "line by line, the vendor invoice is registered, the 3-way match runs, and only "
        "then can a payment be recorded — capped by what was ordered and what was billed."),
    "pricing_gate": (
        "The Purchasing stage cannot be approved while the request is still unpriced. "
        "This is what stops a zero-value request slipping past the value-based Finance / "
        "CFO / CEO approvals. When pricing is entered on a request that is already "
        "circulating, the value rungs are recalculated against the new total: the ones "
        "now required are appended, and value rungs that no longer qualify and have not "
        "been reached yet are removed. Stages that are already approved, rejected or "
        "currently active are never touched, so an in-flight signature is never "
        "disturbed. A non-EGP request must also carry a real exchange rate before it can "
        "be priced, otherwise its value would route on the raw foreign figure."),
    "rfq": (
        "A priced request whose EGP-equivalent total reaches the RFQ threshold must carry "
        "at least the minimum number of quotes from DISTINCT vendors before Purchasing "
        "can sign off — two quotes typed against the same supplier do not satisfy the "
        "rule. Purchasing may waive it by recording a single-source justification on the "
        "request (OEM-only part, proprietary spare, genuine emergency); the justification "
        "is stored on the request and audited. Below the threshold, or while the request "
        "is still unpriced, this gate does not fire."),
    "sod": (
        "Two independence rules run on every signature. (1) Self-approval: the requester "
        "of a request may never sign any of its approval stages — raising it is already "
        "their signature. (2) Dual role: one person may not sign two DIFFERENT stages of "
        "the same request, not even when a delegation makes them eligible for both; each "
        "rung must be an independent pair of eyes. A rejected request that is resubmitted "
        "gets a brand-new set of steps, so old history never blocks a fresh cycle. While "
        "“admins exempt” is on, super_admin and any role holding Procurement-admin bypass "
        "both rules so a small team can still walk a request through the whole ladder; "
        "switch it off for strict mode, where admins are bound exactly like everyone else."),
    "sod_escalation": (
        "Nobody ever approves their own request — but when the requester is the ONLY person "
        "who may sign a rung, that rung would wait for a signature that can never legally "
        "arrive. The engine then escalates it ONE LEVEL UP the org chart: the superior of the "
        "blocked role signs that rung instead. It fires only when there is genuinely nobody "
        "else — if a second holder of the role exists, that colleague signs and nothing about "
        "the request changes. The climb never lands on the requester: when the superior is the "
        "originator too it keeps going up, and a chain an admin has configured into a loop is "
        "detected and treated as “nobody above”. An escalated rung is stamped on the request, "
        "shown in the approval trail and printed on the PR and PO documents, so an auditor sees "
        "the deviation without opening the app. When the climb runs out — the CEO raising a "
        "request that needs the CEO signature — the request is REFUSED at submit with a clear "
        "reason and Procurement admins are notified, so a delegation or an admin override can "
        "be arranged instead of the request quietly rotting in a queue. Which stages exist, "
        "their order, the amount thresholds and every gate are untouched by this: escalation "
        "only changes WHO signs a rung. The chain itself is editable per role in Procurement "
        "settings; a role with no superior configured means nobody above it."),
    "budget_gate": (
        "Issuing the Purchase Order is blocked when the department has an explicit budget "
        "row for the current year AND its committed spend already exceeds it. Committed "
        "spend counts requests dated in that year with status approved, PO issued, "
        "partially received, received or closed. A department with no budget row "
        "configured is never blocked. An admin can force the PO through; the override is "
        "written to the audit trail."),
    "three_way_match": (
        "Before payment, ORDERED (the PO grand total and quantities) is compared with "
        "RECEIVED (goods-receipt quantities and their value at the order price) and "
        "INVOICED (registered vendor invoices, gross of tax). Short delivery is flagged "
        "but does NOT block payment — paying for what was actually delivered on a partial "
        "receipt is legitimate. Over-billing does block: invoiced above the PO total, or "
        "invoiced pre-tax above the value of what was received. The comparison allows a "
        "tolerance of 1% of the PO total or 1 currency unit, whichever is larger."),
    "payment_cap": (
        "A payment can only be recorded once a Purchase Order exists — never against a "
        "draft, pending or cancelled request. Cumulative payments may not exceed the PO "
        "grand total, and once invoices exist they may not exceed the invoiced gross "
        "total either, so in practice the cap is the LOWER of the two, each with the "
        "payment tolerance applied. A payment is also refused while the 3-way match shows "
        "over-billing. An admin can override any of these and the override is audited."),
}

# One-line meaning per PR status (stored as proc_doc sections 'status.<key>').
STATUS_MEANING = {
    "draft": "Being filled in by the requester — not yet in the approval ladder.",
    "pending": "In the ladder, waiting on the signature of the current stage.",
    "approved": "Every required stage has signed; a PO number has been drafted.",
    "rejected": "A stage rejected it with a reason; it bounces back to the requester, "
                "who can correct and resubmit (which rebuilds the ladder from scratch).",
    "po_issued": "The Purchase Order has been issued to the vendor; payment may begin.",
    "partially_received": "Some ordered lines have been received, not all of them.",
    "received": "Delivery confirmed for every line.",
    "closed": "Completed and archived — no further action expected.",
    "cancelled": "Withdrawn by the requester, Purchasing or an admin before completion.",
}
