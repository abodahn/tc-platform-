"""
Traceability / ESG / Digital Product Passport constants.

Scope split (read this before adding anything here):
  * app/compliance owns FACTORY audits and FACTORY certificates (BSCI, WRAP,
    SMETA, fire licence...) — i.e. "is our site compliant".
  * this module owns MATERIAL and PRODUCT provenance — who made the fibre, the
    yarn, the fabric; which certificates cover THAT material; and what a
    Digital Product Passport for an order would contain.
Do not duplicate factory certificates here.
"""

# --- supply-chain tiers ---------------------------------------------------
# Industry-standard tiering. Tier 1 is us / the CMT; each higher number is one
# step further upstream. `deepest tier reached` is the traceability score.
TIERS = {
    1: "Tier 1 — CMT / garment assembly",
    2: "Tier 2 — fabric mill (knit / weave)",
    3: "Tier 3 — spinner / dyehouse / wet processing",
    4: "Tier 4 — fibre farm / raw material",
}
TIER_KEYS = [1, 2, 3, 4]

PARTNER_ROLES = ["CMT", "Fabric mill", "Spinner", "Dyehouse", "Printer",
                 "Fibre farm", "Recycler", "Trims supplier", "Laundry", "Other"]

PARTNER_STATUS = ["active", "pending_approval", "suspended", "inactive"]

# --- material certificates (NOT factory certificates) ---------------------
MATERIAL_STANDARDS = [
    "GOTS", "OEKO-TEX Standard 100", "GRS", "RCS", "OCS",
    "ZDHC", "bluesign", "RWS", "FSC", "Higg FSLM", "Other",
]

# Certificates within this many days of expiry raise a bell alert (once).
EXPIRY_WARN_DAYS = 60

# --- Digital Product Passport ---------------------------------------------
# The ESPR/DPP data points we hold the factory to. The completeness score is
# simply how many of these are present — it is a TODO list, not a rating.
DPP_POINTS = [
    ("lots_linked",   "trc.dpp.lots_linked"),    # >=1 material lot linked to the order
    ("composition",   "trc.dpp.composition"),    # every linked lot states a fibre composition
    ("supplier",      "trc.dpp.supplier"),       # every linked lot names its supplier
    # tier3 is a MEMBERSHIP test (a tier-3 node exists), tier4 a DEPTH test.
    # `deepest >= 3` would score this point for a chain that skips the spinner
    # entirely, i.e. two of the ten points for one single tier-4 node.
    ("tier3",         "trc.dpp.tier3"),          # a tier-3 spinner/dyehouse is named
    ("tier4",         "trc.dpp.tier4"),          # chain reaches tier 4 (fibre origin)
    ("certificates",  "trc.dpp.certificates"),   # >=1 valid material certificate on the chain
    ("origin",        "trc.dpp.origin"),         # country of origin recorded
    ("care",          "trc.dpp.care"),           # care instructions recorded
    ("recycling",     "trc.dpp.recycling"),      # end-of-life / recycling info recorded
    ("footprint",     "trc.dpp.footprint"),      # recorded ESG footprint for the order
]
DPP_TOTAL = len(DPP_POINTS)

# A passport below this % on an order shipping within DPP_ALERT_DAYS is
# "materially incomplete" and raises a bell alert.
DPP_MIN_PCT = 70
DPP_ALERT_DAYS = 30

# Hard stop on the ancestry walk. The `seen` set already makes a cycle safe;
# this second guard keeps a pathological (or maliciously deep) chain bounded.
MAX_CHAIN_DEPTH = 25

# --- RBAC — merged into the platform catalogue by app.security ------------
TRC_PERMISSIONS = [
    "trc_view",    # view partners, lots, material certificates and passports
    "trc_manage",  # create/edit partners, lots, certificates, passports, ESG records
]

TRC_ROLE_PERMS = {
    "compliance_officer": ["trc_view", "trc_manage"],
    "production_manager": ["trc_view"],
    "executive_viewer": ["trc_view"],
    "sustainability_officer": ["trc_view", "trc_manage"],
}

TRC_ROLE_LABELS = {
    "sustainability_officer": "Sustainability & Traceability Officer",
}

PERMISSION_LABELS = {
    "trc_view": "Traceability: view chain, certificates & passports",
    "trc_manage": "Traceability: manage partners, lots, certificates & ESG data",
}
