# -*- coding: utf-8 -*-
"""Check the implemented ladders against TC-PUR-DOAM-01 v1.1, tier by tier.

The DOAM's bands are written "up to 10,000" then "10,001 to 200,000", so the
boundary itself decides who signs: at exactly 10,000 the Procurement Manager
carries it, and one piastre more pulls in two directors. A >= where the document
means > routes real money to the wrong signature and nothing would ever error,
so every band edge is tested from BOTH sides.
"""
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))  # this checkout, not another worktree
import app.approvals.constants as C   # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))


def final(total, kind="opex"):
    l = C.build_ladder(total, kind)
    return l[-1] if l else None


# ------------------------------------------------- owner's ladder (2026-11-17)
# The DOAM §4.1 / §4.2 value tiers no longer decide WHO signs: the owner made
# every rung always-required, for OPEX and CAPEX alike, with the CEO last and no
# Supply Chain Director or Board on a new request.
NEW = ["warehouse", "warehouse_manager", "purchasing", "factory_manager",
       "control", "cfo", "ceo"]
print("owner's ladder: the same seven signatures at every value, both kinds")
for total in (1, 10_000, 10_000.01, 500_000.01, 2_000_000.01, 5_000_000.01, 25_000_000):
    for kind in ("opex", "capex"):
        got = C.build_ladder(total, kind)
        chk(f"{kind} {total:>13,.2f} -> {len(got)} signatures, ends at {final(total, kind)}",
            got == NEW, got)
chk("neither the Supply Chain Director nor the Board is on any new ladder",
    not ({"scd", "bod"} & set(C.DOAM_LADDER)))

# ---------------------------------------------------------------- safety
print("\nfail-safe behaviour")
chk("a blank/unknown kind falls back to OPEX, never to no approvals",
    C.build_ladder(500_000, "") == C.build_ladder(500_000, "opex")
    and C.build_ladder(500_000, "nonsense") == C.build_ladder(500_000, "opex"))
# The always-on stages are whatever the matrix says costs nothing to reach, so
# these read them from it rather than naming rungs: the set gained the Warehouse
# Manager on 2026-10-13 and will change again. What must hold is that a zero,
# blank or unreadable total still collects EVERY always-on rung — a fat-fingered
# value must never be the cheap way through the gate.
_ALWAYS = [st for st in C.DOAM_LADDER if C.OPEX_MATRIX.get(st, 1) <= 0]
chk("zero and None still require the always-on stages (%s)" % _ALWAYS,
    C.build_ladder(0, "opex") == _ALWAYS
    and C.build_ladder(None, "opex") == _ALWAYS)
chk("a non-numeric total does not crash and does not skip approvals",
    C.build_ladder("abc", "opex") == _ALWAYS)
chk("every stage in every DOAM matrix is a real DOAM ladder stage",
    all(s in C.DOAM_LADDER for m in C.MATRICES.values() for s in m))
chk("every DOAM stage has a label and a DOAM level",
    all(s in C.STAGE_LABELS and s in C.DOAM_LEVEL for s in C.DOAM_LADDER))
chk("every DOAM stage has at least one role that can act on it",
    all(C.STAGE_ROLES.get(s) for s in C.DOAM_LADDER))

# ---------------------------------------------------------------- §4.3
print("\nDOAM §4.3 — sourcing bands")
SOURCING = [
    (10_000,        1),
    (50_000,        1),
    (50_000.01,     3),
    (500_000,       3),
    (500_000.01,    3),
    (2_000_000,     3),
    (2_000_000.01,  3),
]
for total, quotes in SOURCING:
    b = C.sourcing_band(total)
    chk(f"{total:>13,.2f} -> {b['quotes']} quote(s)",
        b["quotes"] == quotes, f"expected {quotes}")
chk("a band carries ONLY the rule this system enforces",
    all(set(b) == {"over", "quotes"} for b in C.SOURCING_BANDS),
    "'mode'/'governance' named off-system procedures and nothing read them")

print("\n  single source must be approved ONE LEVEL ABOVE the value tier (§4.3)")
# Every ladder now ends at the CEO (L1), so one level above is the Board's level
# — and single_source_stage() finds no rung there to add (the Board is retired).
for total, want in ((5_000, "BOD"), (100_000, "BOD"), (6_000_000, "BOD")):
    lvl = C.final_approver_level(total, "opex")
    up = C.level_above(lvl)
    chk(f"{total:>11,} normally {lvl:<3} -> single source needs {up}", up == want, f"expected {want}")
chk("nothing escalates above the Board", C.level_above("BOD") == "BOD")
chk("single source adds no rung to the full ladder (no Board to add)",
    C.single_source_stage(C.build_ladder(5_000, "opex"), "opex") is None)

# ---------------------------------------------------------------- §7.3.3
print("\nDOAM §7.3.3 — match tolerance is 2% or 500 EGP, whichever is GREATER")
chk("on 1,000 the 500 EGP floor binds, not 2%", C.match_tolerance_value(1_000) == 500.0,
    C.match_tolerance_value(1_000))
chk("on 25,000 the 500 EGP floor still binds (2% = 500)",
    C.match_tolerance_value(25_000) == 500.0, C.match_tolerance_value(25_000))
chk("on 100,000 the 2% binds (2,000)", C.match_tolerance_value(100_000) == 2_000.0,
    C.match_tolerance_value(100_000))
chk("the old flat 1% is gone", C.MATCH_TOLERANCE_PCT == 2.0 and C.MATCH_TOLERANCE_ABS == 500.0)
chk("negative/blank amounts do not produce negative slack",
    C.match_tolerance_value(-1_000) == 500.0 and C.match_tolerance_value(None) == 500.0)

# ---------------------------------------------------------------------------
# THE DOAM IS NOW IN FORCE (Ahmed confirmed it is mandatory). These assert the
# switch actually happened, and record exactly what it changed.
# ---------------------------------------------------------------------------
print(str())
print("the DOAM ladder is the one in force")
chk("DOAM_IN_FORCE is set", C.DOAM_IN_FORCE is True)
chk("a bare build_ladder() call now uses the DOAM, not the paper form",
    C.build_ladder(48_000) == C.build_ladder(48_000, "opex"), C.build_ladder(48_000))

chk("a 48,000 request now collects the owner's seven signatures",
    C.build_ladder(48_000) == NEW, C.build_ladder(48_000))
chk("the Financial Director (finance) stays off the ladder",
    "finance" not in C.build_ladder(48_000))

print(str())
print("  demand and value stages follow the ACTIVE ladder")
# DEMAND_STAGES now means "the rungs up to the pricing gate" (every rung is
# always on, so the ladder at zero value is the whole ladder).
chk("demand stages are the rungs up to and including the pricing gate",
    C.DEMAND_STAGES == ["warehouse", "warehouse_manager", "purchasing"], C.DEMAND_STAGES)
chk("no stage is both demand and value-gated",
    not (set(C.DEMAND_STAGES) & set(C.VALUE_STAGES)))
chk("every live value stage is on the ladder at any amount (retired ones aside)",
    all(s in C.build_ladder(1) for s in C.VALUE_STAGES if s not in C.RETIRED_STAGES),
    C.VALUE_STAGES)

print(str())
print("  the paper form stays expressible, so reverting is one flag")
chk("legacy ladder and thresholds still defined",
    bool(C.LEGACY_LADDER) and C.APPROVAL_MATRIX.get("cfo") == 25_000)

print(str())
print("FINAL: " + ("ALL GREEN" if ok else "FAILURES ABOVE"))
sys.exit(0 if ok else 1)
