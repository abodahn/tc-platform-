# -*- coding: utf-8 -*-
"""Check the implemented ladders against TC-PUR-DOAM-01 v1.1, tier by tier.

The DOAM's bands are written "up to 10,000" then "10,001 to 200,000", so the
boundary itself decides who signs: at exactly 10,000 the Procurement Manager
carries it, and one piastre more pulls in two directors. A >= where the document
means > routes real money to the wrong signature and nothing would ever error,
so every band edge is tested from BOTH sides.
"""
import sys

sys.path.insert(0, r"D:\TC platform\tc-platform-beta")
import app.approvals.constants as C   # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))


def final(total, kind="opex"):
    l = C.build_ladder(total, kind)
    return l[-1] if l else None


# ---------------------------------------------------------------- OPEX §4.1
print("DOAM §4.1 — OPEX ladder: who carries the final signature")
OPEX = [
    #  total,        expected final approver stage,   DOAM tier
    (1,              "purchasing",     "1  PRM (L4)"),
    (10_000,         "purchasing",     "1  PRM, at the band edge"),
    (10_000.01,      "scd",            "2  PD/SCD (L2), one piastre over"),
    (200_000,        "scd",            "2  PD/SCD, at the band edge"),
    (200_000.01,     "finance",        "3  Financial Director (L2)"),
    (500_000,        "finance",        "3  FIND, at the band edge"),
    (500_000.01,     "cfo",            "4  MD or CFO (L1)"),
    (2_000_000,      "cfo",            "4  CFO, at the band edge"),
    (2_000_000.01,   "ceo",            "5  MD and CFO (L1)"),
    (5_000_000,      "ceo",            "5  MD+CFO, at the band edge"),
    (5_000_000.01,   "bod",            "6  Board"),
    (10_000_000,     "bod",            "6  Board, at the band edge"),
    (25_000_000,     "bod",            "7  Board + business case"),
]
for total, want, tier in OPEX:
    got = final(total, "opex")
    chk(f"tier {tier:<34} {total:>13,.2f} -> {C.STAGE_LABELS.get(got, got)}",
        got == want, f"expected {want}, got {got}")

print("\n  joint approval at tier 5 means BOTH sign (DOAM: 'MD and CFO')")
t5 = C.build_ladder(3_000_000, "opex")
chk("CFO and MD are both in the tier-5 ladder", {"cfo", "ceo"} <= set(t5), t5)
chk("Board is NOT pulled in below 5,000,000", "bod" not in t5, t5)

print("\n  tier 1 is short: a small buy does not collect directors")
t1 = C.build_ladder(5_000, "opex")
chk("tier 1 ladder is warehouse + purchasing only", t1 == ["warehouse", "purchasing"], t1)

# ---------------------------------------------------------------- CAPEX §4.2
print("\nDOAM §4.2 — CAPEX ladder (every tier is a joint approval)")
CAPEX = [
    (100_000,      {"purchasing", "scd", "finance", "factory_manager", "cfo"}, "1  SCD+FIND review, PD+CFO approve"),
    (250_000,      {"purchasing", "scd", "finance", "factory_manager", "cfo"}, "1  at the band edge"),
    (250_000.01,   {"purchasing", "scd", "finance", "factory_manager", "cfo", "ceo"}, "2  + MD"),
    (2_000_000,    {"purchasing", "scd", "finance", "factory_manager", "cfo", "ceo"}, "2  at the band edge"),
    (2_000_000.01, {"purchasing", "scd", "finance", "factory_manager", "cfo", "ceo", "bod"}, "3  Board"),
]
for total, want, tier in CAPEX:
    got = set(C.build_ladder(total, "capex"))
    chk(f"tier {tier:<34} {total:>13,.2f} -> {len(got)} signatures",
        got == want, f"missing {want - got or '-'} extra {got - want or '-'}")

chk("CAPEX never routes through Warehouse (not a stores replenishment)",
    all("warehouse" not in C.build_ladder(v, "capex") for v in (1, 10**6, 10**8)))

print("\n  the same money is treated differently as CAPEX than as OPEX")
chk("300,000 OPEX ends at Financial Director", final(300_000, "opex") == "finance")
chk("300,000 CAPEX ends at Managing Director", final(300_000, "capex") == "ceo")

# ---------------------------------------------------------------- safety
print("\nfail-safe behaviour")
chk("a blank/unknown kind falls back to OPEX, never to no approvals",
    C.build_ladder(500_000, "") == C.build_ladder(500_000, "opex")
    and C.build_ladder(500_000, "nonsense") == C.build_ladder(500_000, "opex"))
chk("zero and None still require the always-on stages",
    C.build_ladder(0, "opex") == ["warehouse", "purchasing"]
    and C.build_ladder(None, "opex") == ["warehouse", "purchasing"])
chk("a non-numeric total does not crash and does not skip approvals",
    C.build_ladder("abc", "opex") == ["warehouse", "purchasing"])
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
for total, want in ((5_000, "L2"), (100_000, "L1"), (300_000, "L1"),
                    (1_000_000, "BOD"), (6_000_000, "BOD")):
    lvl = C.final_approver_level(total, "opex")
    up = C.level_above(lvl)
    chk(f"{total:>11,} normally {lvl:<3} -> single source needs {up}", up == want, f"expected {want}")
chk("nothing escalates above the Board", C.level_above("BOD") == "BOD")

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

paper = ["warehouse", "factory_manager", "purchasing", "finance", "cfo"]
now = C.build_ladder(48_000)
print("     48,000 EGP was : %d approvers  %r" % (len(paper), paper))
print("     48,000 EGP now : %d approvers  %r" % (len(now), now))
chk("Finance and the CFO no longer sign a 48,000 request (tier 2 stops at L2)",
    "finance" not in now and "cfo" not in now, now)
chk("the Supply Chain Director carries it instead", now[-1] == "scd", now)
chk("above 5,000,000 the Board is required", C.build_ladder(6_000_000)[-1] == "bod")

print(str())
print("  demand and value stages follow the ACTIVE ladder")
chk("demand stages == the ladder at zero value",
    C.build_ladder(0) == C.DEMAND_STAGES, (C.build_ladder(0), C.DEMAND_STAGES))
chk("no stage is both demand and value-gated",
    not (set(C.DEMAND_STAGES) & set(C.VALUE_STAGES)))
chk("every value stage is reachable at some amount",
    all(s in C.build_ladder(10**9) for s in C.VALUE_STAGES), C.VALUE_STAGES)

print(str())
print("  the paper form stays expressible, so reverting is one flag")
chk("legacy ladder and thresholds still defined",
    bool(C.LEGACY_LADDER) and C.APPROVAL_MATRIX.get("cfo") == 25_000)

print(str())
print("FINAL: " + ("ALL GREEN" if ok else "FAILURES ABOVE"))
sys.exit(0 if ok else 1)
