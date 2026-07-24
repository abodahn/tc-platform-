"""Compliance module constants — audit schemes, statuses, severities, RBAC perms."""

# Buyer / social / quality / sustainability schemes a garment exporter is audited on.
SCHEMES = [
    "amfori BSCI", "SMETA (Sedex)", "WRAP", "SA8000", "SLCP",
    "ISO 9001", "ISO 14001", "ISO 45001",
    "OEKO-TEX", "GOTS", "GRS", "Higg FEM", "Buyer COC", "Other",
]

AUDIT_STATUS = ["planned", "scheduled", "in_progress", "completed", "cancelled"]

# Finding / Corrective-Action-Plan severity (SMETA/BSCI style).
FINDING_SEVERITY = ["observation", "minor", "major", "critical", "zero_tolerance"]
FINDING_STATUS = ["open", "in_progress", "closed", "verified"]

CERT_TYPES = ["OEKO-TEX", "GOTS", "GRS", "WRAP", "ISO 9001", "ISO 14001",
              "ISO 45001", "amfori BSCI", "SA8000", "Fire/Building", "Other"]

# Alert window: audits/certs within this many days of expiry raise a bell alert.
EXPIRY_WARN_DAYS = 60

# RBAC — registered in security.py PERMISSIONS + granted to a compliance_officer role.
CMP_PERMS = {
    "cmp_view": "Compliance: view audits, findings & certificates",
    "cmp_manage": "Compliance: manage audits, CAPs & certificates",
}
