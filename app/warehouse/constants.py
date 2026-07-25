"""Warehouse constants — material kinds, roll/allocation statuses, movement types, RBAC."""

# ONE header table (wh_materials) carries both roll-tracked fabric and quantity-only
# trims; `roll_tracked` is the behaviour switch and `kind` is only a label. Splitting
# fabric and trims into two tables would duplicate every list query, the reorder sweep,
# the receipt path and the ledger for zero gain — the columns are identical.
MATERIAL_KINDS = ["fabric", "trim", "accessory"]

# Stored on wh_rolls. There is deliberately NO 'reserved' status: a roll is routinely
# PART reserved (30m committed out of 60m), so a reservation is a quantity (reserved_m),
# not a state. Likewise a return from the cutting room is a 'return' MOVEMENT against
# the same roll, not a status — otherwise the ledger stops reconciling with remaining_m.
ROLL_STATUS = ["available", "partial", "consumed", "quarantine"]

# Preferred column order for the finished-goods matrix; any other size found in the
# data is appended after these (sizes are DATA — never columns in the schema).
SIZES = ["XS", "S", "M", "L", "XL", "XXL", "3XL"]

# A roll narrower than the marker is not a substitute, so width is a filter, not a
# preference; this is the tolerance allowed on the requested minimum width (cm).
WIDTH_TOLERANCE_CM = 0.5

WH_PERMISSIONS = [
    "wh_view",     # dashboard, materials, rolls, FG matrix
    "wh_manage",   # receive, issue, reserve, adjust, pack/ship
]

WH_ROLE_PERMS = {
    # existing platform / module roles that need the material store
    "storekeeper": ["wh_view", "wh_manage"],
    "warehouse_manager": ["wh_view", "wh_manage"],
    "production_manager": ["wh_view", "wh_manage"],
    "production_supervisor": ["wh_view"],
    "factory_manager": ["wh_view", "wh_manage"],
    "purchasing_manager": ["wh_view"],
    "finance_user": ["wh_view"],
    "executive_viewer": ["wh_view"],
    "it_director": ["wh_view", "wh_manage"],
}

WH_ROLE_LABELS = {
    "warehouse_manager": "Warehouse Manager",
}
