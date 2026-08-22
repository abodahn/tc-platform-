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
import re
from datetime import date as _date, datetime as _dt, timezone as _tz

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
# APPENDED, never reordered: 'Pcs' stays the first entry and the default, so
# every existing PR line is untouched. The tail comes from the ERP item master
# (Cone for 2,205 thread items, Yard, Packet, Sheet, Carton, Drum, Barrel) —
# mangling those into 'Pcs' would put a wrong unit on a purchase order.
UNITS = ["Pcs", "Set", "Box", "Roll", "Meter", "Kg", "Liter", "Service", "Lot",
         "Cone", "Yard", "Packet", "Sheet", "Carton", "Drum", "Barrel"]
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
#
# ORDER IS THE SIGNING ORDER, AND THE LAST INCLUDED STAGE IS THE FINAL APPROVER.
# That is why Purchasing sits BEFORE the directors: DOAM tier 1 (up to 10,000)
# must end on the Procurement Manager's signature, while tier 2 must end on a
# director's. Putting the buyer first satisfies both without special cases, and
# it matches the real sequence — the buyer sources and prices, then management
# commits. `scd` and `bod` are new; every pre-existing stage keeps its relative
# position, so requests already in flight are unaffected (their steps are rows
# already written to the database).
# The ladder in use TODAY, derived from T&C's real paper form: at 48,000 EGP it
# produces warehouse + factory_manager + purchasing + finance + cfo, which with
# the requester is the six signatures on the paper. DOAM v1.1 disagrees with that
# form — at 48,000 it wants five signatures and involves NEITHER Finance NOR the
# CFO (§4.1 tier 2 approves at Plant / Supply Chain Director). Since the DOAM is
# still "Draft v1.0" with a blank approval line, the paper form stays
# authoritative and the DOAM ladder is opt-in. See build_ladder().
LEGACY_LADDER = ["warehouse", "factory_manager", "purchasing", "finance", "cfo", "ceo"]

# The DOAM ladder. Purchasing sits BEFORE the directors because the last included
# stage is the final approver: tier 1 (up to 10,000) must end on the Procurement
# Manager's signature and tier 2 on a director's, and this order satisfies both
# without special cases. It also matches the real sequence — the buyer sources
# and prices, then management commits.
DOAM_LADDER = ["warehouse", "purchasing", "factory_manager", "scd",
               "finance", "cfo", "ceo", "bod"]

# What the rest of the codebase means by "the ladder". Unchanged on purpose:
# nothing about who signs moves until someone signs the DOAM.
LADDER = LEGACY_LADDER

# Human labels for each stage (EN — AR/TR carried by i18n on the client).
# Wording follows the DOAM's own role names so a signature block in the system
# reads the same as the signature block on the paper form.
STAGE_LABELS = {
    "requester": "Requester",
    "warehouse": "Warehouse",
    "purchasing": "Procurement Manager",
    "factory_manager": "Plant Director",
    "scd": "Supply Chain Director",
    "finance": "Financial Director",
    "cfo": "CFO",
    "ceo": "Managing Director",
    "bod": "Board of Directors",
}

# DOAM §3.2 authority levels. Carried so the ladder can reason about LEVELS
# rather than named individuals — which is what "approved one level above the
# value tier" (single-source, §4.3) needs in order to mean anything.
DOAM_LEVEL = {
    "warehouse": "L4",
    "purchasing": "L4",
    "factory_manager": "L2",
    "scd": "L2",
    "finance": "L2",
    "cfo": "L1",
    "ceo": "L1",
    "bod": "BOD",
}
LEVEL_ORDER = ["L4", "L3", "L2", "L1", "BOD"]

# --- DOAM Table 5: WHAT a signature is, not just that one was collected ------
# "P (Prepare) Initiates the activity ... R (Review) Verifies accuracy, budget,
#  and policy compliance before approval. A (Approve) Final authority to commit
#  ... E (Endorse) Senior support of a decision at another level formally
#  approves."
#
# P is the requester: raising the request IS their signature, and they never
# appear in pr_steps. The three that DO land on a ladder rung are below, and
# which one a rung carries is decided by the rung's PLACE in the ladder, not by
# who fills it — that is the only reading of Table 5 that survives a ladder
# whose shape changes when the request is priced:
#
#   A  the top rung of the VALUE ladder — DOAM §4.1/§4.2 name it the authority
#      that commits the money, whatever value tier the request lands in;
#   R  every rung below it, which verifies and passes it up;
#   E  a rung a CONTROL added ABOVE the value tier (§4.3 single source, Table 4
#      unbudgeted, §4.4 deviation). That is Table 5's "senior support of a
#      decision at another level" word for word: the decision was taken at the
#      value tier, and a senior signs in support of it.
#
# Nothing here changes WHO signs or in what order — it only records what the
# signature was, which is what makes the section-7 RACI evidenceable.
STEP_ACTIONS = ["review", "approve", "endorse"]
# ABSENT is not the same as UNREADABLE, and they must not resolve the same way.
#
# ABSENT — NULL or empty — means the row predates the action_type column. The
# migration added that column with DEFAULT 'approve', so the platform has already
# decided what a pre-migration rung meant, and every such row in the database
# carries 'approve' because the ALTER wrote it there. Reading a stray NULL as
# anything else would describe those requests differently from the data.
STEP_ACTION_DEFAULT = "approve"
# UNREADABLE — a non-empty value that is not one of STEP_ACTIONS ('sign', 'yes',
# a truncated write, a letter from some future version) — is a different thing: a
# value the system holds and cannot interpret. That must NOT become the STRONGEST
# claim available. This function decides what a printed document asserts about a
# named person, and printing "approved" over somebody on the strength of a string
# nobody can parse is the one failure direction that actually matters. A guess
# goes DOWNWARDS; understating authority is merely unhelpful.
STEP_ACTION_UNREADABLE = "review"
# RACI letter per action, for the signature block and the printed PDF.
STEP_ACTION_CODE = {"review": "R", "approve": "A", "endorse": "E"}
# English labels; AR/TR are client-side i18n keys (proc.act.*).
STEP_ACTION_LABELS = {"review": "Review", "approve": "Approve", "endorse": "Endorse"}
# Past tense, for the audit trail. The pr_events.action key differs per action
# too ('reviewed' / 'approved' / 'endorsed'), so a Review is distinguishable
# from an Approve by a query, not only by reading the sentence.
STEP_ACTION_PAST = {"review": "reviewed", "approve": "approved", "endorse": "endorsed"}
STEP_ACTION_WHY = {
    "review": "DOAM Table 5 R — verified accuracy, budget and policy compliance "
              "before approval.",
    "approve": "DOAM Table 5 A — final authority to commit at this value tier.",
    "endorse": "DOAM Table 5 E — senior support, one level above the tier that "
               "took the decision.",
}
# Rung origins that are a CONTROL escalation rather than the value ladder. Kept
# beside the actions because that is the only thing that reads it.
CONTROL_ORIGINS = ("single_source", "unbudgeted", "deviation")

# ...and where the DOAM does NOT decide the letter by place. Two of its ladders
# name a JOINT approval — two authorities that commit the money together — and a
# purely positional rule can only ever call the last of them the Approve, so the
# co-approver printed as "REVIEW (R)" on the request and the order an auditor
# reads. He did not review it; he committed it.
#
#   §4.2 CAPEX — "review SCD + FIND, approve PD + CFO", then "+ MD", then Board.
#       The REVIEWERS are fixed by name on that ladder and every other rung is an
#       approving authority the tier added, so the letters are read off the names
#       (Purchasing is the buyer preparing the commercial terms — it verifies).
#   §4.1 OPEX tier 5 — "MD *and* CFO". Every other OPEX tier names ONE authority,
#       which is the top rung, so only this tier needs naming.
#
# Nothing here moves WHO signs or in what order; build_ladder is untouched.
CAPEX_REVIEW_STAGES = ("purchasing", "scd", "finance")
# top rung of the value ladder -> the stages that commit ALONGSIDE it.
OPEX_JOINT_APPROVERS = {"ceo": ("cfo",)}


def step_action(origin, is_top_of_value_ladder, stage=None, top_stage=None, kind=None):
    """Table 5 letter for one rung. `origin` is pr_steps.origin.

    `stage`/`top_stage`/`kind` are what make the letter the DOAM's rather than
    the rung's position. Omitted, the old positional reading applies — that is
    the fallback for a caller with no ladder in hand, never the rule.
    """
    if (origin or "ladder") in CONTROL_ORIGINS:
        return "endorse"
    if stage and str(kind or "").strip().lower() == "capex":
        return "review" if stage in CAPEX_REVIEW_STAGES else "approve"
    if is_top_of_value_ladder:
        return "approve"
    if stage and stage in OPEX_JOINT_APPROVERS.get(top_stage or "", ()):
        return "approve"
    return "review"


# --- DOAM Table 4 L2: the two directors own DIFFERENT things -----------------
# "FIN-D owns payment control; SC-D owns operational and inventory
#  replenishment; PD owns production and maintenance commitments."
#
# OPEX_MATRIX puts factory_manager (PD) and scd (SC-D) at the SAME threshold, so
# above 10,000 both joined every ladder on amount alone. That is not unsafe — it
# collects a signature nobody asked for, never one fewer — so this split is an
# EFFICIENCY fix and is written to fail towards BOTH signatures:
#
#   * only ever drops ONE of the two, never both, and only for OPEX (the CAPEX
#     ladder in §4.2 is a joint PD + CFO approval with SC-D reviewing — both are
#     mandatory there by name, not by amount);
#   * only when the signals point ONE way. A request carrying a maintenance
#     signal AND a supply signal is genuinely both, and keeps both signers;
#   * only on MASTER DATA the requester does not type: the source module, a
#     stocked-spare line, the department. Item text is NOT a signal here;
#   * a department that has explicitly configured the dropped stage in its own
#     responsibility matrix keeps it — the department said so on purpose.
#
# Departments whose spend is a maintenance/production commitment by nature.
# Same set the §6 engineering gate uses, kept here rather than imported so a
# missing maintenance module cannot change who signs a purchase.
PD_DEPARTMENTS = {"general maintenance", "maintenance", "engineering", "workshop",
                  "utilities", "maintenance & engineering"}
# Departments whose spend IS the inventory-replenishment function.
SCD_DEPARTMENTS = {"warehouse", "stores", "store", "logistics", "supply chain",
                   "planning", "materials"}
# Source modules that name the domain outright.
PD_SOURCE_MODULES = {"maintenance"}      # ticket-raised or spare auto-reorder
SCD_SOURCE_MODULES = {"costing"}         # order material buy raised from costing


def l2_domain(department, source_module, has_spare_line):
    """Which L2 director owns this commitment: "plant", "supply_chain", or None.

    None means "cannot be told apart" — the caller must then keep BOTH, which is
    exactly today's behaviour. Never guesses.

    Item TEXT is deliberately not a signal. The obvious candidate was the direct
    materials list Table 12 drives (cost_object_required), on the reasoning that
    a fabric/yarn/trims buy is the replenishment SC-D owns. It is a keyword
    heuristic over free text the requester types, and it fires on any material
    word — so Production, Cutting, Sewing or Finishing buying fabric, the
    commonest purchase in the plant, scored supply-only and DROPPED the Plant
    Director on wording. That heuristic is fine deciding "does this need a cost
    object", where being wrong asks for more data; it must not decide "does this
    director sign", where being wrong removes a signature.
    """
    dept = str(department or "").strip().lower()
    src = str(source_module or "").strip().lower()
    plant = src in PD_SOURCE_MODULES or bool(has_spare_line) or dept in PD_DEPARTMENTS
    supply = src in SCD_SOURCE_MODULES or dept in SCD_DEPARTMENTS
    if plant == supply:
        return None                      # both signals, or neither -> both sign
    return "plant" if plant else "supply_chain"


# The stage each domain keeps, and therefore the other one it drops.
L2_DOMAIN_STAGE = {"plant": "factory_manager", "supply_chain": "scd"}


def apply_l2_domain(stages, domain):
    """Drop the L2 director this request's domain does not belong to.

    Safe by construction: it removes at most one stage, only when BOTH L2
    directors are on the ladder, and only for a domain that was positively
    identified."""
    keep = L2_DOMAIN_STAGE.get(domain or "")
    if not keep:
        return list(stages)
    drop = next((s for s in L2_DOMAIN_STAGE.values() if s != keep), None)
    if keep not in stages or drop not in stages:
        return list(stages)              # not the both-directors case: leave it
    return [s for s in stages if s != drop]


# Which platform roles may act on each stage. super_admin (and anyone with the
# proc_admin permission) can act on ANY stage — handled in the service layer.
#
# If two DOAM roles are the same person at T&C (Managing Director and CEO, or
# Financial Director and CFO), map both stages to that one role here rather than
# editing any logic — that is the whole answer to "are they the same person".
STAGE_ROLES = {
    "warehouse": {"storekeeper", "warehouse_manager"},
    "purchasing": {"purchasing_manager"},
    "factory_manager": {"factory_manager", "plant_director"},
    "scd": {"supply_chain_director"},
    "finance": {"finance_manager", "finance_user", "financial_director"},
    "cfo": {"cfo"},
    "ceo": {"ceo", "managing_director"},
    "bod": {"board"},
}

# --- DOAM §3.2: the authority level a ROLE carries -------------------------
# DOAM_LEVEL above says what a RUNG demands. This says what a SIGNER brings.
# Both halves are needed: without this one the ladder only ever asked WHICH ROLE
# signs, so whoever an admin mapped onto the CFO rung held L1 authority by the
# act of being mapped — a storekeeper included, and a role invented in
# Admin -> Roles with proc_approve just the same.
#
# A person's level is their role's. Users carry exactly one role and no table
# holds a per-user level, so the role IS the grant; keys here are platform role
# keys. This is a DECLARATION of T&C's org in the DOAM's own terms, deliberately
# NOT generated from STAGE_ROLES — a table derived from the thing it checks
# cannot disagree with it. The two are cross-checked instead: tests_authority_
# level.py asserts every role mapped to a rung declares at least that rung's
# level, so re-tiering a role here immediately stops it signing what it may no
# longer commit, and the mismatch is caught rather than silently applied.
#
# A role that is NOT listed holds no DOAM authority: it fails CLOSED at the rung,
# and the rung is reported by services.ladder_signer_health() (and so on
# /governance/findings) instead of quietly waiting for a signature that will be
# refused. Procurement authority is granted here, in the document's terms, not
# by handing out proc_approve.
#
# super_admin / proc_admin are absent on purpose: the platform-admin override is
# checked BEFORE this in can_act(), stays audited, and must keep working.
ROLE_LEVEL = {
    "board": "BOD",
    # L1 — commits major spend, and unbudgeted spend to the L1 limit (Table 4).
    "ceo": "L1",
    "managing_director": "L1",
    "cfo": "L1",
    # L2 — the three directors of Table 4: FIN-D payment control, SC-D
    # operational and inventory replenishment, PD production and maintenance.
    "financial_director": "L2",
    "supply_chain_director": "L2",
    "plant_director": "L2",
    "factory_manager": "L2",
    # These two sign the Financial Director's rung at T&C (STAGE_ROLES['finance']),
    # so those accounts carry its level. That is a statement about T&C's staffing,
    # not about the DOAM: the day Finance says a finance_user is L4, this line
    # changes and the finance rung stops accepting them — which is the point of
    # writing the levels down separately.
    "finance_manager": "L2",
    "finance_user": "L2",
    # L4 — operational / supervisory: prepares, verifies, and buys within tier 1.
    "purchasing_manager": "L4",
    "warehouse_manager": "L4",
    "storekeeper": "L4",
}


def role_level(role):
    """The DOAM §3.2 authority level a platform role carries, or None when it
    carries none. None is not L4 — it is "no authority recorded"."""
    return ROLE_LEVEL.get(role)


def holds_authority(role, stage):
    """May a holder of `role` commit what `stage` commits?

    True when the role's declared level is at or above the rung's. Fails CLOSED
    on an unknown level: a role nobody has placed in the DOAM must not inherit
    L1 by being typed into a stage override. The refusal is visible —
    ladder_signer_health() reports the rung — rather than silent.
    """
    need = DOAM_LEVEL.get(stage)
    if need is None:
        return True                     # a rung the DOAM does not level
    have = ROLE_LEVEL.get(role)
    if have is None:
        return False
    try:
        return LEVEL_ORDER.index(have) >= LEVEL_ORDER.index(need)
    except ValueError:                  # a level not on the DOAM's own scale
        return False


# --- Amount-threshold routing (EGP-equivalent) ------------------------------
# A stage joins the ladder when the total EXCEEDS its threshold; a threshold of
# 0 means always required. Exceeds, not "reaches", because the DOAM's bands are
# written as "up to 10,000" then "10,001 to 200,000" — so 10,000 is tier 1 and
# 10,000.01 is tier 2. Using >= here would put an exact 10,000 in the wrong tier.
#
# Only stages PRESENT in a matrix can ever be included, which is how CAPEX
# leaves out Warehouse: a capital purchase is not a stores replenishment.

# DOAM §4.1 — OPEX ladder (budgeted)
#   up to 10,000          PRM                          (L4)
#   10,001 -    200,000   Plant / Supply Chain Director (L2)
#   200,001 -   500,000   Financial Director            (L2)
#   500,001 - 2,000,000   MD *or* CFO                   (L1)  -> CFO carries it
#   2,000,001 - 5,000,000 MD *and* CFO                  (L1)  -> both included
#   5,000,001 - 10,000,000 Board
#   above 10,000,000      Board, with a business case
OPEX_MATRIX = {
    "warehouse": 0,
    "purchasing": 0,
    "factory_manager": 10_000,
    "scd": 10_000,
    "finance": 200_000,
    "cfo": 500_000,
    "ceo": 2_000_000,
    "bod": 5_000_000,
}

# DOAM §4.2 — CAPEX ladder. Every tier is a joint approval, which this engine
# expresses by including both stages: each must sign before the request moves.
#   up to 250,000          review SCD + FIND, approve PD + CFO
#   250,001 - 2,000,000    + MD
#   2,000,001 - 10,000,000 Board
#   above 10,000,000       Board, with a business case
CAPEX_MATRIX = {
    "purchasing": 0,
    "scd": 0,
    "finance": 0,
    "factory_manager": 0,
    "cfo": 0,
    "ceo": 250_000,
    "bod": 2_000_000,
}

EXPENDITURE_KINDS = ["opex", "capex"]
MATRICES = {"opex": OPEX_MATRIX, "capex": CAPEX_MATRIX}

# The thresholds actually in force today, from the paper form. Read by the
# per-department override table and by every existing call site. Note the
# comparison for these is >= (a total of exactly 10,000 DOES pull in Finance),
# which is the behaviour the form and the tests have always had; the DOAM's
# bands are exclusive instead ("up to 10,000", then "10,001 to ..."), which is
# why the two ladders cannot share one comparison.
APPROVAL_MATRIX = {
    "warehouse": 0,
    "factory_manager": 0,
    "purchasing": 0,
    "finance": 10_000,
    "cfo": 25_000,
    "ceo": 100_000,
}

# Above this, the DOAM requires a written business case in addition to Board
# approval (§4.1 tier 7, §4.2 tier 4).
BUSINESS_CASE_OVER = 10_000_000


# THE DOAM IS THE POLICY IN FORCE. Ahmed confirmed it is mandatory, so the
# ladders in §4.1 and §4.2 now govern every request; the paper form's thresholds
# stay in APPROVAL_MATRIX / LEGACY_LADDER so reverting is this one flag rather
# than an archaeology exercise through the history.
#
# What this changed, concretely: a 48,000 EGP request used to collect Warehouse,
# Plant Director, Purchasing, Finance and the CFO. Under DOAM §4.1 tier 2 it
# collects Warehouse, Purchasing, Plant Director and the Supply Chain Director —
# Finance and the CFO are NOT involved at that value, and above 5,000,000 the
# Board is.
DOAM_IN_FORCE = True


def normalise_expenditure_kind(raw):
    """Read a declared expenditure type. Returns ("opex"|"capex", recognised).

    §4.2 routes capital purchases up a different and LONGER ladder, so the safe
    failure is not simply "default to opex" — opex is the WEAKER ladder, and an
    unrecognised value silently taking it is a downgrade nobody sees. Measured:
    "capitol", "1", "" and a capex with a zero-width space all stored opex, and
    a 900,000 capital request re-filed after rejection lost the Managing
    Director with nothing in the audit trail naming the change.

    So the value is normalised generously — case, spaces, invisible characters,
    and the words a person actually types — and the caller is TOLD when the
    input was not recognised, so it can refuse or flag rather than quietly
    choose the cheaper ladder.
    """
    txt = str(raw if raw is not None else "")
    # strip zero-width and bidi marks before anything else: a pasted value
    # carrying one looks identical on screen and matches nothing.
    txt = re.sub(r"[​-‏‪-‮﻿]", "", txt)
    txt = txt.strip().lower().replace("_", " ").replace("-", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    if txt in ("capex", "capital", "capital expenditure", "capex capital",
               "cap ex", "capitalexpenditure", "asset", "fixed asset"):
        return "capex", True
    if txt in ("opex", "operating", "operational", "operating expenditure",
               "op ex", "revenue", "expense"):
        return "opex", True
    return "opex", False        # unrecognised -> weaker ladder, but SAY SO


def build_ladder(total, kind=None):
    """Return the ordered list of stage keys required for a request of `total`.

    `kind` selects the DOAM ladder: "opex" (§4.1) or "capex" (§4.2). With no kind
    the request is treated as OPEX, which is what an unmarked request is.

    An unrecognised kind falls back to DOAM OPEX rather than to no approvals: a
    blank or fat-fingered value must never be the cheap path through the gate.
    """
    try:
        t = float(total or 0)
    except (TypeError, ValueError):
        t = 0.0
    if not DOAM_IN_FORCE and kind is None:
        # The pre-DOAM paper form: inclusive thresholds, legacy stage order.
        return [s for s in LEGACY_LADDER if t >= APPROVAL_MATRIX.get(s, 0)]
    matrix = MATRICES.get((kind or "opex").strip().lower(), OPEX_MATRIX)
    return [s for s in DOAM_LADDER
            if s in matrix and (matrix[s] <= 0 or t > matrix[s])]


def final_approver_level(total, kind="opex"):
    """The DOAM authority level that carries the final signature."""
    ladder = build_ladder(total, kind)
    return DOAM_LEVEL.get(ladder[-1], "L4") if ladder else "L4"


def level_above(level):
    """The next level up that SOMEBODY CAN ACTUALLY SIGN AT, for DOAM §4.3
    single-source ("approved one level above the value tier").

    The DOAM defines L3 (functional heads) but no ladder stage sits at L3 — the
    Procurement Manager is L4 and the directors are L2. Returning a bare "one
    higher" would hand back L3 for a tier-1 purchase, an escalation target with
    no approver, and the request would wait forever with nothing to show for it.
    So this walks up to the next level that a stage is mapped to. Caps at BOD;
    there is nothing above the Board.
    """
    staffed = {DOAM_LEVEL[s] for s in _ACTIVE_LADDER if s in DOAM_LEVEL}
    try:
        start = LEVEL_ORDER.index(level)
    except ValueError:
        return "BOD"
    for nxt in LEVEL_ORDER[start + 1:]:
        if nxt in staffed:
            return nxt
    return "BOD"


def single_source_stage(ladder, kind=None):
    """DOAM §4.3 — a single-source award is "approved one level above the value
    tier". Returns the ONE extra stage to append to `ladder`, or None when the
    ladder already reaches that high (nothing to escalate to).

    Appending at the end is correct because DOAM_LADDER is ordered by ascending
    authority: the extra signature is the last one collected, and it belongs to
    somebody senior to everyone already on the ladder."""
    if not ladder:
        return None
    matrix = MATRICES.get((kind or "opex").strip().lower(), OPEX_MATRIX)
    order = [s for s in DOAM_LADDER if s in matrix and s not in ladder]
    if not order:
        return None
    want = level_above(DOAM_LEVEL.get(ladder[-1], "L4"))
    for s in order:
        if DOAM_LEVEL.get(s) == want:
            return s
    # Nobody sits at that exact level for this expenditure type (the CAPEX
    # ladder has no L4 rung, for instance). Escalating to the lowest stage that
    # is still ABOVE the ladder's top is the honest reading of "one level above";
    # silently skipping the escalation would be the wrong way to fail.
    top = LEVEL_ORDER.index(DOAM_LEVEL.get(ladder[-1], "L4"))
    for s in order:
        if LEVEL_ORDER.index(DOAM_LEVEL.get(s, "L4")) > top:
            return s
    return None


# --- Budgeted vs unbudgeted spend (DOAM Table 4) ----------------------------
# §4.1 is titled "OPEX Ladder (BUDGETED)", so every tier in OPEX_MATRIX above
# describes PLANNED spend. Table 4 puts the other kind somewhere specific:
#
#   L1  GM / CFO — major commitments within board-approved budgets AND
#                  UNBUDGETED ITEMS UP TO THE L1 LIMIT.
#
# So money spent against no approved plan is not a tier-1 purchase that happens
# to be small — it is an L1 commitment whatever its size. It is NOT refused: the
# DOAM permits unbudgeted spend up to the L1 limit, it just prices it in
# signatures.
UNBUDGETED_LEVEL = "L1"


def unbudgeted_stage(ladder, kind=None):
    """The ONE extra stage an unbudgeted request needs, or None when `ladder`
    already reaches L1 (there is nothing left to escalate to).

    Same shape as single_source_stage(): a request can be single-source AND
    unbudgeted, and each control asks for at most one extra signature."""
    want = LEVEL_ORDER.index(UNBUDGETED_LEVEL)
    if any(LEVEL_ORDER.index(DOAM_LEVEL.get(s, "L4")) >= want for s in (ladder or [])):
        return None
    matrix = MATRICES.get((kind or "opex").strip().lower(), OPEX_MATRIX)
    for s in DOAM_LADDER:
        if s in matrix and DOAM_LEVEL.get(s) == UNBUDGETED_LEVEL:
            return s
    return None


# --- Pricing gate (controlled Procure-to-Pay) -------------------------------
# A purchase request is raised WITHOUT any commercial value: the requester only
# states what they need (item, qty, unit, spec). Pricing is entered later, by
# Purchasing, at the purchasing stage — that's the "pricing gate". Only then do
# the value-based financial approvals (Finance / CFO / CEO) join the ladder.
PRICING_STATUSES = ["unpriced", "priced"]

# Stages always required regardless of value (threshold 0): the "demand
# approval" part of the ladder, which runs BEFORE pricing.
#
# These MUST derive from whichever ladder is in force. Leaving them on the paper
# form while build_ladder() followed the DOAM would put a stage in the demand
# list that the DOAM makes value-gated, and the pricing gate would then wait for
# a signature that the ladder never asks for — a request stuck with no error.
_ACTIVE_LADDER = DOAM_LADDER if DOAM_IN_FORCE else LEGACY_LADDER
_ACTIVE_MATRIX = OPEX_MATRIX if DOAM_IN_FORCE else APPROVAL_MATRIX

# "The ladder", for everything that is not routing: the governance screens, the
# stage-role override admin, the workflow view. This is re-bound (it was defined
# as LEGACY_LADDER above, before DOAM_IN_FORCE is known) rather than defined
# once, so the DOAM stages are visible in one place instead of two.
#
# It matters: stage_roles_map() drops any override for a stage not in LADDER, so
# while this pointed at the paper form nobody could assign a signing role to the
# Supply Chain Director or the Board — two stages the live ladder actually uses.
LADDER = _ACTIVE_LADDER
ACTIVE_MATRIX = _ACTIVE_MATRIX

DEMAND_STAGES = [s for s in _ACTIVE_LADDER if _ACTIVE_MATRIX.get(s, 1) <= 0]

# Value-gated stages: they join only once Purchasing has priced the request and
# the total clears their threshold.
VALUE_STAGES = [s for s in _ACTIVE_LADDER
                if s in _ACTIVE_MATRIX and _ACTIVE_MATRIX[s] > 0]

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
# Ships OFF. On, one platform admin could sign two different rungs of the same
# request — verified end to end: a single account carried a 300k CAPEX request
# through purchasing, PD, SCD, FIND, CFO, MD and the Board. A site that really
# is one or two people can switch it on deliberately; it must not be the
# default, because the default is what an auditor finds in production.
SOD_ADMIN_EXEMPT = False


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
    "proc_pay",        # release payments to suppliers (DOAM 7.3.4)
    "proc_catalogue",  # maintain the item master (NOT cost price — pricing gate)
    "proc_admin",      # settings, approval matrix, act on any stage, vendors
]

# New roles introduced by this module -> the procurement permissions they hold.
PROC_ROLE_PERMS = {
    "purchasing_manager": ["proc_view", "proc_create", "proc_approve", "proc_purchasing",
                           "proc_catalogue"],
    "finance_manager": ["proc_view", "proc_create", "proc_approve", "proc_pay"],
    "cfo": ["proc_view", "proc_approve", "proc_pay"],
    "ceo": ["proc_view", "proc_approve"],
    # proc_catalogue is the item master. The store is the only function that
    # handles the physical item every day, so it is the only one that notices the
    # unit is wrong or the name describes a part that has not been stocked for two
    # years. Purchasing keep it too — they own item and supplier data — but the
    # grant does NOT extend to cost price, which stays with the pricing gate.
    "warehouse_manager": ["proc_view", "proc_create", "proc_approve", "proc_catalogue"],
    # DOAM §3.2 authority roles that STAGE_ROLES routes to. Registered HERE, in
    # code, and not only seeded into custom_roles at boot: can_act() resolves a
    # role through effective_roles(), and stage_roles_map() drops any role that
    # is not in it. A role that lives only as a database row is therefore absent
    # from the registry in every environment where that one write did not land —
    # which is how a 60,000 EGP request reached the 'scd' rung with no eligible
    # signer and no error. Same grants as cfo/ceo: they approve, they do not buy.
    "supply_chain_director": ["proc_view", "proc_approve"],
    "plant_director": ["proc_view", "proc_approve"],
    # DOAM §7.3.4 "FIND owns payment", and Table 4 L2 "FIN-D owns payment
    # control". Releasing payment was gated on proc_purchasing, so the buyer who
    # committed the spend could also pay it and Finance could not — the exact
    # separation the clause exists to create, inverted. proc_pay moves it.
    "financial_director": ["proc_view", "proc_approve", "proc_pay"],
    "managing_director": ["proc_view", "proc_approve"],
    "board": ["proc_view", "proc_approve"],
    # grants for roles that already exist on the platform:
    "storekeeper": ["proc_view", "proc_create", "proc_approve", "proc_catalogue"],
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
    "supply_chain_director": "Supply Chain Director",
    "plant_director": "Plant Director",
    "financial_director": "Financial Director",
    "managing_director": "Managing Director",
    "board": "Board of Directors",
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

# --- DOAM §4.3 sourcing bands ----------------------------------------------
# The flat "2 quotes at 25,000" rule above is what the system enforced before
# the DOAM; it is kept because the settings table and its tests read it, but
# rfq_required_for() below is the rule that now applies. Each band gives the
# minimum number of quotes and the governance step the DOAM attaches to it.
#   up to 50,000          one quotation (spot buy), buyer records price basis
#   50,001 -   500,000    three quotations, PD/SCD reviews the comparison
#   500,001 - 2,000,000   three quotations plus negotiation, Procurement Committee
#   above 2,000,000       formal tender, Tender Committee recommends
# DOAM Annex, Table 20 — the register of controlled forms. Each entry says what
# the form is FOR, how long it is kept, and — the part that matters for an audit
# — WHERE in this system the form actually lives. `route` None means the form is
# not produced by this system yet, and the register says so rather than leaving a
# blank that reads as "covered".
CONTROLLED_FORMS = [
    ("T&C-PUF-01", "Purchase Requisition", "Initiate a need (SO or cost centre linked)",
     "5 yrs", "/procurement/new"),
    ("T&C-PUF-02", "Purchase Order", "Commit a supplier", "5 yrs", "/procurement/list"),
    # Produced FROM the request: one RFQ per vendor, numbered RFQ-YYYY-NNNNNN,
    # printed with the price columns empty for the supplier to fill in. The old
    # /procurement/rfqs this pointed at never existed — a register entry naming a
    # 404 reads to an auditor as a form that is produced when it is not.
    ("T&C-PUF-03", "Request for Quotation", "Solicit supplier prices", "5 yrs",
     "/procurement/list"),
    ("T&C-PUF-04", "Quote Comparison", "Compare bids and justify award", "5 yrs",
     "/procurement/list"),
    ("T&C-PUF-05", "Purchasing Register", "Sequential log of PRs and POs", "5 yrs",
     "/procurement/list"),
    # Both are produced FROM the request, like the RFQ two rows up: receiving
    # and the three-way match happen on the request page. The /procurement/
    # receiving and /procurement/invoices landings named here never existed.
    ("T&C-PUF-06", "Goods Receipt Note", "Confirm receipt and condition", "5 yrs",
     "/procurement/list"),
    ("T&C-PUF-07", "Three-Way Match", "Reconcile PO, GRN and invoice", "5 yrs",
     "/procurement/list"),
    ("T&C-PUF-08", "CAPEX Request", "Capital request and business case", "10 yrs",
     "/procurement/new"),
    ("T&C-PUF-09", "Engineering Justification", "Justify spares and MRO", "3 yrs",
     "/maintenance/justifications"),
    # No longer a paper form with no digital equivalent: the netting runs on
    # every priced request and prints on the request page (§3.4 coverage check).
    # Scope is stated honestly — the netting reads mnt_spare_parts, so MRO
    # spares are covered and direct materials (wh_materials, which carries its
    # own stock_qty/reserved_qty/reorder_level) are NOT: there is no pr_items
    # link to a material, so those lines report "not assessable". Claiming
    # unqualified coverage here would read to an auditor as a produced form.
    ("T&C-PUF-10", "Coverage Check", "Net requirement after netting (MRO spares)",
     "1 yr", "/procurement/list"),
    ("T&C-PUF-11", "Intercompany Reconciliation", "Taypa PO versus requirement",
     "1 yr", None),
    ("T&C-PUF-12", "Justification Memo", "Over-plan quantity or over-target price",
     "3 yrs", "/procurement/list"),
    ("T&C-PUF-13", "Return to Vendor", "Rejected-goods handling", "1 yr", None),
    ("T&C-PUF-14", "Buyer Daily Work Program", "Daily buyer routine", "1 yr", None),
    ("T&C-PUF-15", "Supplier Registration", "Onboard and pre-qualify a supplier",
     "Active +3", "/procurement/vendors"),
    ("T&C-PUF-16", "Conflict of Interest", "Declare a related-party interest",
     "3 yrs", None),
]

# The form code a printed document carries, so a filed PDF can be traced back to
# the register entry that governs its retention.
FORM_CODES = {"pr": "T&C-PUF-01", "po": "T&C-PUF-02", "rfq": "T&C-PUF-03",
              "grn": "T&C-PUF-06", "dn": "T&C-PUF-13", "capex": "T&C-PUF-08"}

# DOAM Annex / audit 3.4-b9b — RETENTION, as a date on the record rather than a
# sentence on a page. The register above says "5 yrs" and "10 yrs" in prose; a
# prose retention period is not a control, it is a claim. Every requisition is
# stamped with the day it may first be disposed of, computed from the SAME two
# numbers the register prints: capital expenditure is kept ten years, everything
# else five.
RETENTION_YEARS = {"capex": 10, "opex": 5}


def retention_years(kind):
    """Years a request of this expenditure kind must be kept. An unrecognised or
    blank kind reads as OPEX, exactly like build_ladder — and OPEX is the SHORTER
    period, so the fallback is checked again wherever the kind can change: a
    request that becomes CAPEX must have its retention EXTENDED, never left."""
    return RETENTION_YEARS.get(str(kind or "").strip().lower(), RETENTION_YEARS["opex"])


def retention_until(created_at, kind):
    """The date (YYYY-MM-DD) this record leaves its retention window.

    Date arithmetic by year number, not by adding 365*n days: five years from a
    29 February lands on 28 February, which is what a records officer means.
    """
    day = str(created_at or "")[:10]
    try:
        d = _date.fromisoformat(day)
    except ValueError:
        d = _dt.now(_tz.utc).date()
    y = d.year + retention_years(kind)
    try:
        return d.replace(year=y).isoformat()
    except ValueError:                      # 29 Feb -> 28 Feb in a non-leap year
        return d.replace(year=y, day=28).isoformat()


# DOAM §5 — the golden thread. Every document from requisition to payment
# carries the controlling cost object, so cost, margin and stock can be read per
# order and per client. Table 12 says which cost object applies to what:
#
#   fabric, yarn, trims, chemicals, packaging   -> sales order   MANDATORY
#   subcontract or wash for a specific order    -> sales order   MANDATORY
#   spares, MRO, maintenance, workshop          -> asset / cost centre
#   facility services, IT, utilities            -> cost centre
#   capital assets                              -> asset / project
# There is deliberately no COST_OBJECTS list here. It existed as
#   COST_OBJECTS = ["sales_order", "asset", "cost_center"]
# and NOTHING read it — `grep -rn COST_OBJECTS` returned only its own
# definition. The rule it looked like it enforced is already enforced, by
# name and by keyword, in cost_object_required() below and in
# services.cost_object_check(): those two decide when a sales order is
# mandatory and refuse the submission when one is missing. A bare list of
# the three cost-object NAMES adds nothing a reader could execute, so it is
# gone rather than left looking like a control.

# Category keywords that make a sales-order reference mandatory. Matched against
# the catalogue category and the line text, lowercased — the ERP's category
# names are not under this system's control, so a keyword match is the only
# thing that survives an import that renames "Trims" to "TRIM & ACCESSORIES".
#
# The original twelve were a substring match over six words of Table 12's prose,
# and an audit walked fourteen of seventeen real apparel direct materials
# straight past it with no sales order: zippers, sewing thread, buttons,
# interlining, greige, denim, care labels, hangtags, cartons, polybags — even a
# line literally reading "Direct materials". Trims, fasteners, labelling and
# packaging are the bulk of what a garment factory buys against an order, so the
# list now covers them by name.
#
# Matching is WHOLE-WORD (plus a plural), not substring. That is what keeps the
# widening safe in the other direction: "wash" no longer fires on flat WASHERS
# or the WASHROOM, "thread" no longer fires on THREADED rod, "print" no longer
# fires on the PRINTER. Multi-word entries are matched as written.
SO_MANDATORY_KEYWORDS = (
    # base materials
    "fabric", "greige", "griege", "grey goods", "denim", "twill", "poplin",
    "jersey", "knit", "woven", "interlining", "fusible", "lining", "wadding",
    "padding", "yarn", "thread", "sewing thread",
    # trims, fasteners, closures
    "trim", "accessory", "accessories", "zipper", "zip fastener", "button",
    "snap fastener", "snap button", "buckle", "eyelet", "velcro", "elastic",
    "drawcord", "drawstring", "webbing", "twill tape",
    # labelling
    "label", "care label", "size label", "hangtag", "hang tag", "swing tag",
    # packaging
    "packaging", "packing material", "carton", "polybag", "poly bag", "hanger",
    # wet process / outsourced operations
    "chemical", "dye", "dyestuff", "wash", "washing", "print", "printing",
    "embroider", "embroidery", "embroidered", "subcontract", "sub-contract",
    "cmt", "cut make trim",
    # trims and materials a merchandiser types but the prose of Table 12 never
    # spells out. Singular stems the (?:s|es)? rule cannot reach on its own, and
    # multi-word forms chosen over the bare stem where the bare stem is also a
    # maintenance word ("piping" is pipework, "packing" is gland packing).
    "rib", "taffeta", "elastane", "spandex", "grosgrain", "bias binding",
    "binding tape", "piping cord", "snap", "shoulder pad", "hook and eye",
    "buckram", "sequin", "bra cup", "sliver", "roll goods", "tissue paper",
    "silica gel", "neck board", "back board", "collar bone", "butterfly",
    "gum tape",
    # the requester saying it in so many words
    "direct material", "raw material",
)

# Same list in the other two languages this system is written in. Every refusal
# message below is translated into Arabic and Turkish, so AR/TR requesters are
# expected by design — and an English-only trigger means the same purchase is
# refused in English and waved through in Arabic.
#
# These are matched as PLAIN SUBSTRINGS, not whole words: Arabic prefixes the
# article and conjunctions straight onto the noun (قماش -> القماش، وأقمشة) and
# Turkish agglutinates its suffixes with consonant mutation (iplik -> ipliği),
# so \b is both wrong and inert here. ASCII-folded spellings are listed beside
# the diacritic ones because keyboards without Turkish layout are normal here.
SO_MANDATORY_KEYWORDS_AR = (
    "قماش", "أقمشة", "اقمشة", "خيط", "خيوط", "غزل", "بطانة", "حشو",
    "سوستة", "سحاب", "زرار", "أزرار", "ازرار", "كبسون", "مطاط",
    "تيكيت", "ليبل", "بطاقة تعليق", "إكسسوار", "اكسسوار",
    "تغليف", "كرتون", "بوليباج", "شماعة",
    "صباغة", "صبغة", "غسيل", "طباعة", "تطريز",
    "خامات", "خامة", "دانتيل", "شريط لاصق",
)
SO_MANDATORY_KEYWORDS_TR = (
    "kumaş", "kumas", "iplik", "ipliğ", "iplig", "dokuma", "örgü", "orgu",
    "astar", "elyaf", "pamuklu",
    "fermuar", "düğme", "dugme", "çıtçıt", "citcit", "toka", "lastik bant",
    "etiket", "aksesuar", "askı kartı", "aski karti",
    "ambalaj", "koli", "poşet", "poset", "askılık",
    "boya", "boyama", "yıkama", "yikama", "baskı", "baski", "nakış", "nakis",
    "dikiş ipliği", "dikis iplik", "hammadde",
)

# Phrases that LOOK like a trim but are maintenance / IT / facility stock. They
# are struck out of the text before matching, so a workshop ordering BUTTON HEAD
# screws or a stores desk ordering a LABEL PRINTER ribbon is not sent away to
# find a sales order it has no business carrying. Anything else in the same line
# still matches — this removes the phrase, not the check.
#
# Everything here is an unambiguous MRO noun: nobody sews a snap ring onto a
# shirt. Genuinely ambiguous wording lives in SO_EXEMPT_IF_MRO below instead.
SO_EXEMPT_PHRASES = (
    "button head", "label printer", "labelling machine", "labeling machine",
    "print head", "printhead", "thread tap", "threading tap", "thread gauge",
    "snap ring", "snap gauge", "snap-on", "brake pad", "mouse pad",
    "power cord", "extension cord", "cord grip", "butterfly valve",
    "butterfly nut", "rib joint plier",
)

# ...and the wording that is a trim OR a control part depending on what else is
# on the line. Struck out ONLY when the line also carries a maintenance /
# electrical / IT context word, because "Push button 4-hole 18L for shirts" is a
# garment button order in the auditor's own words with one word bolted on the
# front, and an unconditional strike made the exemption list the way around the
# very string it was tested on.
SO_EXEMPT_IF_MRO = ("push button", "push-button", "pushbutton")

_MRO_CONTEXT = re.compile(
    r"\b(switch|panel|valve|screw|bolt|relay|contactor|electric|electrical|"
    r"wiring|machine|motor|control|socket|plc|sensor|lamp|indicator|"
    r"emergency|enclosure|cabinet|printer|maintenance|spare)\b")

_SO_EXEMPT_RE = re.compile("|".join(re.escape(p) for p in SO_EXEMPT_PHRASES))
_SO_EXEMPT_MRO_RE = re.compile("|".join(re.escape(p) for p in SO_EXEMPT_IF_MRO))
_SO_RE = re.compile(r"\b(?:%s)(?:s|es)?\b"
                    % "|".join(re.escape(k) for k in SO_MANDATORY_KEYWORDS))
# Arabic and Turkish: substring match, for the reasons above.
_SO_RE_INTL = re.compile("|".join(
    re.escape(k) for k in SO_MANDATORY_KEYWORDS_AR + SO_MANDATORY_KEYWORDS_TR))
# Shapes rather than words. A textile weight/count with its unit, and a style
# number, are both loud garment signals that no keyword list can enumerate:
# "150gsm" has no word boundary in front of "gsm", and "Style 4471 material buy"
# says nothing a bare stem could safely catch ("material handling" is MRO).
_SO_SHAPE_RE = re.compile(
    r"\b\d+\s*(?:gsm|g/?m2|denier|dtex|tex)\b|\bstyle\s*#?\s*\d")

# Sales-order statuses that are NOT a live cost object. A requisition may not be
# raised against one: the order is finished or gone, so nothing can be costed to
# it. Shipped orders stay acceptable — late trims and rework are real.
SO_CLOSED_STATUSES = ("closed", "cancelled")


# Machine-part nouns. Deliberately NOT the machinery words in _MRO_CONTEXT: a
# part is a discrete component, so "guide" can qualify "thread", whereas
# "machine" must not — "denim rolls for the cutting machine" is a real material
# buy. "hook" is absent on purpose: "hook and loop" and "hook and eye" are
# trims, and including it let Velcro through with no sales order.
_MRO_PART_WORDS = (
    "guide", "foot", "holder", "feeder", "roller", "blade", "spring", "head",
    "plate", "case", "seal", "bearing", "needle", "stand", "tension", "cutter",
    "applicator", "pump", "gear", "pulley", "bushing", "nozzle", "filter",
    "looper", "bobbin", "presser", "gauge", "shaft", "clamp", "knife", "lever",
    "cam", "reel", "bracket", "arm", "guard", "cover", "housing",
)
_MRO_PART_RE = re.compile(r"^(?:%s)s?$" % "|".join(_MRO_PART_WORDS))
_MATERIAL_WORD_RE = re.compile(r"^(?:%s)(?:s|es)?$"
                               % "|".join(re.escape(k) for k in SO_MANDATORY_KEYWORDS
                                          if " " not in k))
_WORD_RE = re.compile(r"[a-z0-9%/#.-]+")


def _every_material_run_is_a_part(text):
    """True when every maximal run of material words is immediately followed by
    a machine-part noun — i.e. each one is a compound like "thread guide" rather
    than a material being bought.

    False when there are no material words at all, so the caller still falls
    through to its normal check; this only ever EXEMPTS, never adds a gate."""
    words = _WORD_RE.findall(text)
    if not words:
        return False
    seen_material = False
    i, n = 0, len(words)
    while i < n:
        if not _MATERIAL_WORD_RE.match(words[i]):
            i += 1
            continue
        seen_material = True
        j = i
        while j < n and _MATERIAL_WORD_RE.match(words[j]):
            j += 1
        # The run is words[i:j]. A part noun must sit immediately after it, or
        # one word later — "elastic tape roller" puts a noun between the two
        # ("tape" is half of the multi-word keyword "twill tape", so it is not
        # a material word on its own). ONE word of slack only: two is enough for
        # "cotton twill fabric for the guide" to smuggle a material buy in
        # behind a part noun.
        if j < n and _MRO_PART_RE.match(words[j]):
            i = j + 1
        elif j + 1 < n and _MRO_PART_RE.match(words[j + 1]):
            i = j + 2
        else:
            return False
    return seen_material


def cost_object_required(texts):
    """Does this requisition need a sales-order reference? `texts` is every
    category / item string on the request. Returns "sales_order" when Table 12
    makes it mandatory, else None (asset or cost centre, requester's choice).

    ponytail: a keyword heuristic, not an enforced control — it is only as good
    as its vocabulary, in three languages. The structural fix is mandatory
    server-validated catalogue linkage on PR lines; until that lands, describe
    this leg as a heuristic in the DOAM compliance statement."""
    blob = " ".join(str(t or "").lower() for t in texts)
    if _SO_RE_INTL.search(blob) or _SO_SHAPE_RE.search(blob):
        return "sales_order"
    clean = _SO_EXEMPT_RE.sub(" ", blob)
    if _MRO_CONTEXT.search(blob):
        clean = _SO_EXEMPT_MRO_RE.sub(" ", clean)
    # A machine PART whose name contains a material word is still a machine
    # part: "thread guide", "zipper foot", "denim needle", "yarn tension
    # spring" are sewing-machine components, and gating them demanded a sales
    # order a technician has no business citing — measured, 11 of 15 realistic
    # spare names, and a cost centre did not clear it either. They could not be
    # bought at all.
    #
    # The rule is COMPOUND-NOUN adjacency, not "a part noun appears somewhere".
    # A maximal run of material words is exempt only when a part noun follows it
    # immediately, which is what makes it a compound. That distinction is the
    # whole control: "elastic tape roller" is a part (the run ends in a part
    # noun), while "guide for the feeder plus cotton twill fabric" is a material
    # buy smuggled onto a spare line (the run "cotton twill fabric" is followed
    # by nothing). Exempting on a bare part noun anywhere let exactly that
    # through, and the sales-order gate's own test caught it.
    if _every_material_run_is_a_part(clean):
        return None
    return "sales_order" if _SO_RE.search(clean) else None


# DOAM §4.4 — Purchase Order Approval by Deviation. Beyond the value ladder, an
# order is graded by how far it departs from the approved plan, and each grade
# adds signatures ON TOP of the value ladder:
#
#   on plan                     qty within ceiling, price <= target   nothing
#   over the stock ceiling      qty pushes stock past max_level       PD + SCD, memo
#   price up to  5% over target                                       3 quotes, memo
#   price  5-15% over target                                          FIN-D, memo, quotes
#   price   >15% over target                                          FIN-D + MD, memo
#
# The document's middle quantity row ("over plan, within the stock ceiling") is
# graded by the §3.4 COVERAGE CHECK: "procurement quantity is capped at the net
# requirement after inventory netting". The plan quantity is not invented — it is
# computed per line from the stock master (see services.deviation_findings), and
# a line with no stock record is still reported "not assessable" rather than
# guessed either way.
DEVIATION_PRICE_BANDS = [
    (5.0,  [],                  "price_5"),      # standard approvers + 3 quotes
    (15.0, ["finance"],         "price_15"),
    (None, ["finance", "ceo"],  "price_over_15"),
]


def price_deviation_pct(unit_price, target):
    """How far above target this price sits, in percent. None when there is no
    target on file — an unpriced catalogue line cannot be graded, and guessing a
    target of zero would grade every purchase as infinitely over."""
    try:
        target = float(target or 0)
        unit_price = float(unit_price or 0)
    except (TypeError, ValueError):
        return None
    if target <= 0:
        return None
    return (unit_price - target) / target * 100.0


# DOAM §3.4 — "A quantity above plan ... requires a justification memo and a
# higher approval per Section 4.4." One authority above the value tier is the
# Plant Director: one rung lighter than pushing stock past its ceiling outright,
# which the row below it already costs PD + SCD.
DEVIATION_OVER_PLAN_STAGES = ["factory_manager"]


def deviation_grade(pct_over, over_ceiling=False, over_plan=False):
    """The §4.4 grade for one line. Returns
    {"grade", "stages", "memo", "quotes"} — `stages` are EXTRA approvals on top
    of the value ladder.

    A quantity finding names the GRADE, because that is the row the document
    calls out as the trigger — but it no longer swallows the price stages. An
    order that is both above the net requirement and 20% over target needs both
    sets of eyes, and the old early return dropped the Financial Director from
    exactly that case."""
    grade = {"grade": "on_plan", "stages": [], "memo": False, "quotes": False}
    if pct_over is not None and pct_over > 0:
        for ceiling, stages, name in DEVIATION_PRICE_BANDS:
            if ceiling is None or pct_over <= ceiling:
                grade = {"grade": name, "stages": list(stages), "memo": True,
                         "quotes": True}
                break
    if not (over_ceiling or over_plan):
        return grade
    qty_stages = (["factory_manager", "scd"] if over_ceiling
                  else list(DEVIATION_OVER_PLAN_STAGES))
    grade["grade"] = "over_ceiling" if over_ceiling else "over_plan"
    grade["stages"] = qty_stages + [s for s in grade["stages"] if s not in qty_stages]
    grade["memo"] = True
    return grade


# DOAM §4.3 — "Advance up to 25% of PO value: FIN-D. Above 30%: CFO or MD, with
# a bank guarantee when the order exceeds 500,000 EGP. No advance to a supplier
# off the approved vendor list."
#
# The document leaves 25–30% unnamed. Anything above the Financial Director's
# stated ceiling is treated as needing the higher authority: reading the gap the
# other way would let 30% of a large order out of the door on the lower
# signature, which is plainly not what the clause is protecting against.
ADVANCE_FIND_MAX_PCT = 25.0
ADVANCE_GUARANTEE_OVER = 500_000.0


def advance_rule(po_value, advance_pct):
    """Who must authorise an advance of `advance_pct` on a PO of `po_value`, and
    whether a bank guarantee is required. Returns
    {"stages": [...], "level": "L2"|"L1", "guarantee": bool}."""
    try:
        pct = float(advance_pct or 0)
    except (TypeError, ValueError):
        pct = 0.0
    try:
        value = float(po_value or 0)
    except (TypeError, ValueError):
        value = 0.0
    if pct <= ADVANCE_FIND_MAX_PCT:
        return {"stages": ["finance"], "level": DOAM_LEVEL.get("finance", "L2"),
                "guarantee": False}
    return {"stages": ["cfo", "ceo"], "level": DOAM_LEVEL.get("cfo", "L1"),
            "guarantee": value > ADVANCE_GUARANTEE_OVER}


# DOAM §3.4 — "Splitting a purchase to stay within a lower approval level is
# prohibited. Related purchases within a 30-day window are aggregated."
AGGREGATION_WINDOW_DAYS = 30

# DOAM §4.3 sourcing bands. `quotes` is the ONLY field here, and that is the
# point: the bands used to carry "mode" (spot / compare / negotiate / tender) and
# "governance" (buyer_records_basis / director_reviews / procurement_committee /
# tender_committee) and NOTHING read either one — `grep -rn` found the two keys
# only in this literal and in one assertion in tests_doam_ladder.py. They named
# procedures that happen OFF this system: a negotiation round and a tender
# committee are meetings, not states a Flask app can hold or refuse. Keeping them
# as dict keys made the band look like it enforced four rules when it enforced
# one. The DOAM wording stays here, in the comment, where non-executing text
# belongs:
#     up to 50,000        one quotation, buyer records the price basis
#     50,001 - 500,000    three quotations, director reviews
#     500,001 - 2,000,000 three quotations + negotiation, procurement committee
#     above 2,000,000     formal tender, tender committee
# `quotes` is the half of that this system can and does enforce — see
# services.rfq_gate_check, which blocks the Purchasing signature without them.
SOURCING_BANDS = [
    {"over": 0,          "quotes": 1},
    {"over": 50_000,     "quotes": 3},
    {"over": 500_000,    "quotes": 3},
    {"over": 2_000_000,  "quotes": 3},
]


def sourcing_band(total):
    """The DOAM §4.3 band for an EGP-equivalent total. Bands are keyed on
    'exceeds', matching the document's 'up to 50,000' / '50,001 to ...' wording."""
    try:
        t = float(total or 0)
    except (TypeError, ValueError):
        t = 0.0
    band = SOURCING_BANDS[0]
    for b in SOURCING_BANDS:
        if b["over"] <= 0 or t > b["over"]:
            band = b
    return band


def quotes_required(total):
    """Minimum competitive quotes for a total, per DOAM §4.3."""
    return sourcing_band(total)["quotes"]


# --- DOAM §7.3.3 three-way match tolerance ---------------------------------
# "within tolerance of 2% of value or 500 EGP ... or 5% of quantity, whichever
# is greater." The old code used a flat 1% on value only, hardcoded. "Whichever
# is greater" is the important half: on a small invoice the 500 EGP floor is the
# binding number, and on a large one the percentage is.
MATCH_TOLERANCE_PCT = 2.0
MATCH_TOLERANCE_ABS = 500.0      # EGP
MATCH_QTY_TOLERANCE_PCT = 5.0


def match_tolerance_value(amount, fx_rate=1.0):
    """Slack allowed on a value comparison of `amount`, IN THE CURRENCY OF
    `amount` — not always EGP.

    The percentage half is a ratio and needs no conversion. The 500 floor is an
    EGP figure from §7.3.3, so on a foreign-currency order it must be divided by
    the rate: 500 EGP at fx 50 is 10 USD, not 500 USD. Passing it through raw
    handed a USD order fifty times the tolerance the clause allows, and a 45%
    over-invoice came back "matched"."""
    try:
        a = abs(float(amount or 0))
    except (TypeError, ValueError):
        a = 0.0
    try:
        fx = float(fx_rate or 1.0)
    except (TypeError, ValueError):
        fx = 1.0
    if fx <= 0:
        fx = 1.0
    return max(a * MATCH_TOLERANCE_PCT / 100.0, MATCH_TOLERANCE_ABS / fx)


# --- Payment cap tolerance --------------------------------------------------
# Rounding/bank-charge slack allowed on the payment caps in services.add_payment
# (cumulative paid vs the PO grand total, and vs the invoiced gross total). The
# hard floor of 1 currency unit inside add_payment is separate and stays fixed.
# NOTE: the MATCH verdict uses match_tolerance_value() and
# MATCH_QTY_TOLERANCE_PCT above, not this one, and those two are deliberately NOT
# admin-configurable — they are the DOAM's numbers (see the workflow page).
PAYMENT_TOLERANCE_PCT = 1.0


# --- Supplier-name identity -------------------------------------------------
def vendor_key(v):
    """The identity of a supplier NAME, for grouping and for matching.

    Trimmed and case-folded, because every place that answers "is this line on
    this order?" must answer it the same way. They did not: po_groups() stripped,
    the PO's PDF filter compared raw, and the per-order exposure cap stripped but
    kept case — so 'Alphatex ' with a trailing space grouped into the Alphatex
    order and then vanished off its own document, and 'ALPHATEX' became a second
    order to the same company. Lives here because pdf.py and services.py both
    need it and constants.py is the leaf module both already import.

    NOT a display value: the first spelling seen stays the name that is printed.
    """
    return (v or "").strip().lower()


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
    # --- optional fields on the request form and the request page ------------
    # Not every plant uses all of these, and a field nobody fills is a field
    # people learn to skip past. Each defaults to VISIBLE, so a database with no
    # override rows shows exactly what it always showed.
    #
    # Hiding is display only. No gate is weakened by a switch here — see
    # OPTIONAL_FIELDS below for what each one still costs.
    "show_expenditure_kind": {"kind": "bool", "default": True},
    "show_sales_order": {"kind": "bool", "default": True},
    "show_forecast_ref": {"kind": "bool", "default": True},
    "show_cost_center": {"kind": "bool", "default": True},
    "show_delivery_condition": {"kind": "bool", "default": True},
    # --- approval aging (app/approvals/aging.py) -----------------------------
    # DAYS, not hours. SLA_HOURS_PER_STAGE above is the escalation engine's unit
    # and stays that way; a buyer chasing a signature counts in days, and the
    # aging screen is written for the buyer.
    #
    # Defaults: amber on day 2 (a rung that sat over a weekend), red on day 4
    # (SLA_HOURS_PER_STAGE is 48h, so by day 4 the escalation has already fired
    # once and nobody moved). Both are floored at 1: an overdue count of 0 makes
    # every rung overdue the instant it activates, which would bell every signer
    # in the plant on the first run and teach them to ignore it.
    "aging_warn_days": {"kind": "int", "min": 1, "default": 2},
    "aging_overdue_days": {"kind": "int", "min": 1, "default": 4},
}

# The optional fields an admin may hide, and the HONEST consequence of hiding
# each. The settings screen prints `warn` next to the switch: a control that
# still applies to a field nobody can fill is a request that cannot be
# submitted, and the admin should read that before flipping it, not afterwards.
OPTIONAL_FIELDS = (
    ("show_expenditure_kind", "Expenditure type",
     "Every request is then treated as OPEX. CAPEX routes through a stricter "
     "ladder (DOAM 4.2), so hiding this gives capital spend the weaker one."),
    ("show_sales_order", "Sales order",
     "DOAM 3.4 requires a valid sales order OR an agreed forecast on any request "
     "that is not maintenance spares. Hide BOTH and those requests are refused "
     "at submission — the gate is not switched off by hiding its field."),
    ("show_forecast_ref", "Agreed forecast",
     "The other half of the DOAM 3.4 cost object. Safe to hide on its own if the "
     "plant works to sales orders; not safe to hide together with Sales order."),
    ("show_cost_center", "Cost centre",
     "The route maintenance and facility spend uses to answer DOAM 3.4 instead "
     "of a sales order. Hiding it does not stop spare-part requests, which are "
     "recognised from the parts master."),
    ("show_delivery_condition", "Delivery condition",
     "Prints on the purchase order. Nothing routes on it."),
)

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
    "scd": (
        "The Supply Chain Director owns operational and inventory replenishment: confirms "
        "the request really is a replenishment need, correctly sourced and correctly timed "
        "against stock. Value stage on operating spend — it joins the ladder only when the "
        "EGP-equivalent total exceeds its threshold — while on a capital request it signs "
        "whatever the value. When a request is positively identified as a production or "
        "maintenance commitment, the Factory Manager stage carries that level instead and "
        "this stage is dropped; when it is both, or cannot be told apart, both directors "
        "sign. By default only the Supply Chain Director role signs here, and it approves "
        "only — it does not price, buy or pay."),
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
    "bod": (
        "The Board of Directors is the highest authority on the ladder: it signs the "
        "largest commitments and there is nothing above it to escalate to. Value stage — "
        "it joins the ladder only when the EGP-equivalent total exceeds its threshold, "
        "which is lower for a capital request than for operating spend. Above 10,000,000 "
        "EGP committed the Board cannot approve until a written business case is recorded "
        "on the request; the same condition is checked earlier at Purchasing, so a request "
        "that size never circulates without one. By default only the Board role signs "
        "here, and it approves only — it does not price, buy or pay. When this last "
        "signature lands the request becomes Approved and a Purchase Order number is "
        "drafted automatically."),
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
    "plant_director": (
        "Accountable for production and maintenance commitments. Signs the Factory "
        "Manager stage: when a request is positively identified as a plant commitment "
        "this director carries that level and the Supply Chain Director stage is dropped. "
        "Holds view and approve only — it does not raise requests, price them, buy or pay."),
    "supply_chain_director": (
        "Accountable for operational and inventory replenishment. The only role mapped to "
        "the Supply Chain Director stage: when a request is positively identified as a "
        "replenishment this director carries that level and the Factory Manager stage is "
        "dropped. Holds view and approve only — it does not raise requests, price them, "
        "buy or pay."),
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
    "financial_director": (
        "Accountable for payment control. Signs the Finance stage — the priced request "
        "against the department budget, the tax and the payment terms — and releases "
        "payments to vendors, which is why that right sits here and not with the buyer "
        "who committed the spend. Holds view, approve and pay; it cannot price a request "
        "or issue a Purchase Order."),
    "cfo": (
        "Chief Financial Officer. Signs the CFO stage for significant committed spend "
        "and is the authority on budget breaches."),
    "ceo": (
        "Chief Executive Officer. Signs the CEO stage — the final authority on the "
        "largest purchases."),
    "managing_director": (
        "Managing Director. Signs the CEO stage — the same rung as the Chief Executive Officer, since both "
        "roles are mapped to it and either signature satisfies it. The last signature on "
        "major spend below Board level. Holds view and approve only — it commits the "
        "money, it does not buy or pay."),
    "board": (
        "The Board of Directors. Signs the Board stage on the largest commitments, and "
        "cannot approve one above 10,000,000 EGP until a written business case is "
        "recorded on the request. Holds view and approve only — no purchasing and no "
        "payment rights."),
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
        "INVOICED (registered vendor invoices, gross of tax). Over-billing blocks "
        "payment: invoiced above the PO total, or invoiced pre-tax above the value of "
        "what was received. Short delivery does NOT block the payment, it CAPS it — the "
        "shortfall is stated in units and in money, and cumulative payment may not "
        "exceed the value actually received, so paying for what was delivered on a "
        "partial receipt stays legitimate while paying the whole PO for part of it does "
        "not. The comparison allows the DOAM tolerances: 2% of the PO total or 500 EGP "
        "on value, whichever is larger, and 5% on quantity."),
    "payment_cap": (
        "A payment can only be recorded once a Purchase Order exists — never against a "
        "draft, pending or cancelled request. Cumulative payments may not exceed the PO "
        "grand total; once invoices exist they may not exceed the invoiced gross total "
        "either; and on a short delivery they may not exceed the gross value of what was "
        "actually received. In practice the cap is the LOWEST of the three that apply, "
        "each with the payment tolerance applied. A payment is also refused while the "
        "3-way match shows over-billing. An admin can override any of these and the "
        "override is audited."),
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
