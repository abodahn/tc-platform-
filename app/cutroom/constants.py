"""Cut room constants — lay statuses, industry benchmarks, alert thresholds, RBAC."""

# Lifecycle of a lay (spread): planned -> spread -> cut -> bundled. `cancelled`
# lays are kept for audit but excluded from every number.
LAY_STATUS = ["planned", "spread", "cut", "bundled", "cancelled"]

# Marker efficiency benchmark for woven/knit apparel markers: the industry band is
# 85-92%. Below this the marker is loose and fabric is being paid for and thrown
# away. This is the number the cut room is judged against.
MARKER_EFF_GOOD = 85.0

# Fabric consumption above the BOM plan by more than this % is unfavourable
# enough to wake somebody up. Fabric is 60-70% of garment cost, so 3% of fabric
# is ~2% of the whole order value — well past a rounding error.
FABRIC_VARIANCE_ALERT_PCT = 3.0

# End allowance left at each ply end when spreading (metres). A calibration knob:
# real spreading machines and real operators leave more than the theory says.
DEFAULT_END_ALLOW_M = 0.05

# --- RBAC (merged into the platform catalogue by app.security) --------------
CUT_PERMISSIONS = [
    "cut_view",    # see lays, utilisation and the order reconciliation
    "cut_manage",  # create/edit lays and record roll consumption
]

CUT_ROLE_PERMS = {
    "cut_incharge": ["cut_view", "cut_manage"],   # cutting in-charge / cut room supervisor
    "production_manager": ["cut_view", "cut_manage"],
    "executive_viewer": ["cut_view"],
}

CUT_ROLE_LABELS = {
    "cut_incharge": "Cutting In-charge",
}
