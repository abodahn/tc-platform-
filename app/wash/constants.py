"""
Wash / finishing recipe library — constants (single source of truth).

Also declares this module's RBAC (permissions, role->perms, role labels) which
`app.security` folds into the platform catalogue via `_merge_module_rbac`.
"""

# Denim/garment laundry families a recipe belongs to.
WASH_TYPES = ["rinse", "stone", "enzyme", "bleach", "acid", "overdye", "silicone", "raw"]

# Operations a recipe step can be. A step with liquor_ratio = 0 is a dry step
# (dry / cure / extract) and consumes no bath.
OPERATIONS = ["desize", "rinse", "stone", "enzyme", "bleach", "neutralise",
              "softener", "tint", "overdye", "extract", "dry", "cure"]

VERSION_STATUS = ["draft", "approved", "retired"]
LABDIP_VERDICT = ["pending", "approved", "rejected", "resubmit"]
BATCH_STATUS = ["done", "running", "rewash", "hold"]

MACHINES = ["Washer 1", "Washer 2", "Washer 3", "Sample Washer", "Tumble Dryer 1"]

# --- deviation tolerance (batch actuals vs the recipe version's nominals) ----
# Beyond any of these the batch is flagged and the bell is rung once.
DEV_TIME_PCT = 15.0   # total cycle minutes
DEV_TEMP_C = 5.0      # peak bath temperature
DEV_LOAD_PCT = 10.0   # machine load kg

# --- internal environmental indicator --------------------------------------
# NOT a certified EIM score. Three intensity ratios, each shown as a percentage
# of an internal reference load, capped at 100 and averaged with equal weight.
# References are typical heavy-process values for a garment laundry.
AMBIENT_C = 20.0                # only heating ABOVE ambient counts
REF_WATER_L_PER_KG = 60.0       # litres of bath water per kg of goods
REF_HEAT_LK_PER_KG = 1500.0     # litre-kelvin (volume x temp rise) per kg of goods
REF_CHEM_G_PER_KG = 120.0       # grams of chemical per kg of goods
IMPACT_LOW_MAX = 33             # score <= 33 low impact, <= 66 medium, else high
IMPACT_MED_MAX = 66

# ==========================================================================
# RBAC — merged into the platform catalogue by app.security
# ==========================================================================
WSH_PERMISSIONS = [
    "wsh_view",     # view recipes, batches, lab dips
    "wsh_manage",   # create/edit recipes, versions, steps, batches, lab dips
    "wsh_approve",  # lab-dip verdicts + approving a version for bulk
]

WSH_ROLE_PERMS = {
    "wash_supervisor": ["wsh_view", "wsh_manage"],
    "wash_lab_technician": ["wsh_view", "wsh_manage", "wsh_approve"],
    "production_manager": ["wsh_view", "wsh_manage", "wsh_approve"],
    "compliance_officer": ["wsh_view"],
    "executive_viewer": ["wsh_view"],
}

WSH_ROLE_LABELS = {
    "wash_supervisor": "Wash Supervisor",
    "wash_lab_technician": "Wash Lab Technician",
}

PERMISSION_LABELS = {
    "wsh_view": "Wash: view recipes, batches & lab dips",
    "wsh_manage": "Wash: manage recipes, versions & batches",
    "wsh_approve": "Wash: lab-dip verdict & approve a version for bulk",
}
