"""Planning module constants — sections, statuses, planning horizon, RBAC perms."""

# Factory sections a capacity line can belong to (garment flow order).
SECTIONS = ["Knitting", "Dyeing", "Cutting", "Sewing", "Wash", "Finishing", "Packing"]

# Days shown on the load board by default (a planner works two weeks out).
BOARD_DAYS = 14

# An order finishing within this many days of its ship date is "at risk", not "on time".
AT_RISK_DAYS = 3

# Safety stop for the day-spread loop: a mis-typed capacity (1 minute/day) must not
# spin forever. An allocation that needs more than a year is reported, not planned.
MAX_PLAN_DAYS = 365

# Load % above this is an overload — the line is promised more minutes than it has.
OVERLOAD_PCT = 100

# Window of MES history a line's MEASURED efficiency is averaged over. Two weeks:
# long enough that one bad shift does not move it, short enough that it still
# describes the line as it runs today.
ACTUALS_DAYS = 14

# An order is "ahead"/"behind" only past this much schedule variance. Anything
# smaller is a rounding of half a shift, not news a planner should act on.
VARIANCE_DAYS = 0.5

# RBAC — merged into the platform catalogue by app.security._merge_module_rbac.
PLN_PERMISSIONS = [
    "pln_view",   # see the load board, line capacity and order feasibility
    "pln_plan",   # maintain lines/SMV, allocate orders, edit the operation bulletin
]

PLN_ROLE_PERMS = {
    "production_planner": ["pln_view", "pln_plan"],   # new role: owns the plan
    "production_manager": ["pln_view", "pln_plan"],
    "executive_viewer": ["pln_view"],
    "project_manager": ["pln_view"],
}

PLN_ROLE_LABELS = {
    "production_planner": "Production Planner",
}
