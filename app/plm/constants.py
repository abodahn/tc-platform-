"""PLM-lite constants — style lifecycle, sample progression, BOM vocabulary, RBAC."""

# Style lifecycle. A style is developed, sampled, approved, then produced.
STYLE_STATUS = ["development", "sampling", "approved", "in_production", "dropped"]

# Statuses that mean "this style is cleared for bulk". Gated by the PP sample rule.
PRODUCTION_STATUSES = ("approved", "in_production")

# The standard apparel sample progression. PP is the buyer's last gate before bulk.
SAMPLE_STAGES = ["proto", "fit", "size_set", "sms", "pp", "top"]
PP_STAGE = "pp"

SAMPLE_VERDICT = ["pending", "approved", "rejected", "revise"]

BOM_KINDS = ["fabric", "trim", "other"]
BOM_UNITS = ["m", "yd", "kg", "pcs", "cone", "set", "dozen"]

PRODUCT_CATEGORIES = [
    "T-shirt", "Polo", "Hoodie / Sweatshirt", "Jacket", "Trouser", "Short",
    "Denim", "Dress", "Shirt", "Legging", "Underwear", "Workwear", "Other",
]

# Default tech-pack sections created with version 1 (title only — body is typed in).
DEFAULT_SECTIONS = [
    "Construction & seams",
    "Fabric & trims",
    "Artwork / print / embroidery",
    "Labels, care & packing",
    "Wash / finishing",
]

# --- RBAC (merged into the platform catalogue by app.security) ---------------
PLM_PERMISSIONS = [
    "plm_view",     # see styles, tech packs, BOM and samples
    "plm_manage",   # create styles, publish tech-pack versions, BOM lines, samples
    "plm_approve",  # release a style to production (separate authority on purpose)
]

PLM_ROLE_PERMS = {
    "product_developer": ["plm_view", "plm_manage"],
    "merchandiser": ["plm_view", "plm_manage"],
    "production_manager": ["plm_view", "plm_approve"],
    "compliance_officer": ["plm_view"],
    "executive_viewer": ["plm_view"],
}

PLM_ROLE_LABELS = {
    "product_developer": "Product Developer",
    "merchandiser": "Merchandiser",
}
