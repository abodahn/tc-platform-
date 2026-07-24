"""Order + Time & Action constants."""

ORDER_STATUS = ["draft", "confirmed", "in_production", "shipped", "closed", "cancelled"]

# Milestone workflow status (planned vs actual is what drives late/at-risk on read).
MILESTONE_STATUS = ["pending", "done"]

# Default Time & Action critical path — (milestone, days BEFORE the ship date).
# planned_date = ship_date - offset. This is the backbone a garment order runs on.
DEFAULT_TNA = [
    ("Order confirmed", 60),
    ("Fabric & trims booked", 55),
    ("Lab-dip / PP sample approved", 48),
    ("Fabric in-house", 40),
    ("PP meeting", 35),
    ("Cutting start", 30),
    ("Input to sewing line", 25),
    ("Sewing 50%", 16),
    ("Washing / finishing", 10),
    ("Final inspection (AQL)", 4),
    ("Ex-factory / ship", 0),
]

AT_RISK_DAYS = 3   # a pending milestone within this many days (or past) with no actual = watch it
