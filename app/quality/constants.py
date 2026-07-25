"""
Quality (QMS) constants — the AQL sampling tables + their resolver, the stage /
defect vocabulary, and the module RBAC.

The AQL block is ANSI/ASQ Z1.4 (ex MIL-STD-105E) SINGLE SAMPLING, NORMAL
inspection, GENERAL INSPECTION LEVEL II — the plan buyers name in apparel POs.
The resolver lives next to the table it reads (it is pure arithmetic, no DB) so
that schema.py and services.py can both use it without an import cycle.
"""

# --- Table I: lot size -> sample-size code letter (general inspection level II).
# (inclusive upper bound, code letter); the last row is open-ended.
LOT_CODE = [
    (8, "A"), (15, "B"), (25, "C"), (50, "D"), (90, "E"), (150, "F"),
    (280, "G"), (500, "H"), (1200, "J"), (3200, "K"), (10000, "L"),
    (35000, "M"), (150000, "N"), (500000, "P"), (float("inf"), "Q"),
]

CODE_LETTERS = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "P", "Q"]

# --- Table II-A: code letter -> sample size.
SAMPLE_SIZE = {"A": 2, "B": 3, "C": 5, "D": 8, "E": 13, "F": 20, "G": 32, "H": 50,
               "J": 80, "K": 125, "L": 200, "M": 315, "N": 500, "P": 800, "Q": 1250}

# --- Table II-A: acceptance numbers (Ac) for the four AQLs apparel actually uses.
# Re is ALWAYS Ac+1 in this table, so it is derived, never stored twice.
# A letter MISSING from a column is an ARROW in the printed standard:
#   above the numeric band -> "use the first sampling plan BELOW the arrow"
#                             (the sample is too small to discriminate at this AQL),
#   below the numeric band -> "use the first sampling plan ABOVE the arrow"
#                             (the plan has saturated at Ac 21).
# Resolving an arrow changes the SAMPLE SIZE too — that is the whole point of it.
ACCEPT = {
    1.0: {"G": 0, "H": 1, "J": 2, "K": 3, "L": 5, "M": 7, "N": 10, "P": 14, "Q": 21},
    1.5: {"F": 0, "G": 1, "H": 2, "J": 3, "K": 5, "L": 7, "M": 10, "N": 14, "P": 21},
    2.5: {"E": 0, "F": 1, "G": 2, "H": 3, "J": 5, "K": 7, "L": 10, "M": 14, "N": 21},
    4.0: {"D": 0, "E": 1, "F": 2, "G": 3, "H": 5, "J": 7, "K": 10, "L": 14, "M": 21},
}

# All four are exactly representable as floats, so they are safe dict keys.
AQL_LEVELS = [1.0, 1.5, 2.5, 4.0]
DEFAULT_AQL = 2.5


def lot_of(lot_size):
    """Normalise an offered lot size to a usable integer. Single funnel for every
    caller: a lot of 0/blank/negative clamps to 1 (the 100% rule then caps the
    sample), but inf/NaN are NOT a lot size — they would blow up int() deep
    inside the plan lookup, so they are rejected at the door."""
    lot = float(lot_size or 0)
    if lot != lot or lot in (float("inf"), float("-inf")):   # NaN / +-inf
        raise ValueError(f"invalid lot size {lot_size!r}")
    return max(1, int(lot))


def code_letter(lot_size):
    """Lot size -> sample-size code letter. Anything under the table's first row
    (a lot of 0 or 1) still gets 'A'; the 100% rule below then caps the sample."""
    lot = lot_of(lot_size)
    for upper, letter in LOT_CODE:
        if lot <= upper:
            return letter
    return "Q"


def verdict(accept, sample_size, units_inspected, defective_units):
    """Z1.4 lot decision. Accept when defective UNITS <= Ac, reject at Re (= Ac+1).

    Invariant: an INCOMPLETE sample may reject (the evidence is already on the
    table) but may never accept — signing a 200-piece plan off after 12 pieces is
    how a bad lot ships. That is why a short inspection stays 'pending'.
    """
    if float(defective_units or 0) > accept:
        return "fail"
    if float(units_inspected or 0) >= sample_size:
        return "pass"
    return "pending"


def aql_plan(lot_size, aql, defective_units=None):
    """(lot_size, aql) -> the sampling plan actually applied.

    Returns code_letter, sample_size, accept (Ac), reject (Re) and, when
    defective_units is supplied, the pass/fail verdict for a COMPLETED sample.
    """
    aql = float(aql)
    if aql not in ACCEPT:
        raise ValueError(f"unsupported AQL {aql}")
    table = ACCEPT[aql]
    letter = code_letter(lot_size)
    i = CODE_LETTERS.index(letter)
    if letter not in table:                       # arrow — walk to the real plan
        band = [j for j, L in enumerate(CODE_LETTERS) if L in table]
        letter = CODE_LETTERS[band[0] if i < band[0] else band[-1]]
    ac = table[letter]
    n = SAMPLE_SIZE[letter]
    lot = lot_of(lot_size)
    n = min(n, lot)                               # "sample >= lot -> inspect 100%"
    plan = {"lot_size": lot, "aql": aql, "code_letter": letter, "sample_size": n,
            "accept": ac, "reject": ac + 1}
    if defective_units is not None:
        plan["verdict"] = verdict(ac, n, n, defective_units)
    return plan


# --- production vocabulary -------------------------------------------------
# Where the units were counted (the inspection itself).
QC_STAGES = ["cutting", "sewing_inline", "end_line", "finishing", "pre_final", "final"]
# Where a defect was CREATED — deliberately a different list: an end-line
# inspection routinely finds a cutting or fabric fault, and the Pareto is only
# actionable if it points at the section that must fix it.
DEFECT_SECTIONS = ["fabric", "cutting", "sewing", "washing", "finishing", "packing"]

# Free-typed defect names fragment the Pareto into useless singletons, so the
# type is a picklist.
DEFECT_TYPES = [
    "Broken stitch", "Skipped stitch", "Open seam", "Puckering", "Uneven hem",
    "Loose thread", "Stain / soil mark", "Fabric hole", "Shading", "Needle hole",
    "Measurement out of tolerance", "Pressing mark", "Label missing / wrong",
    "Button / snap insecure", "Zipper defect", "Mis-cut panel", "Other",
]

DEFECT_SEVERITY = ["critical", "major", "minor"]
VERDICTS = ["pending", "pass", "fail"]

# Action limit: a section running above this DHU is off-standard and goes on the bell.
DHU_ACTION_LIMIT = 8.0

# --- RBAC — folded into security.py by _merge_module_rbac (orchestrator splice).
QC_PERMISSIONS = ["qc_view", "qc_inspect"]
QC_ROLE_PERMS = {
    "quality_inspector": ["qc_view", "qc_inspect"],
    # Segregation of duty: production reads the numbers, it does not sign its own lots.
    "production_manager": ["qc_view"],
    "compliance_officer": ["qc_view"],
    "executive_viewer": ["qc_view"],
    "it_director": ["qc_view", "qc_inspect"],
}
QC_ROLE_LABELS = {"quality_inspector": "Quality Inspector"}
