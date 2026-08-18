"""
Engineering Justification Report — DOAM §6 and Table 13/14, form T&C-PUF-09.

    "Every requisition for spares, maintenance, repair, operating supplies, or
     workshop activity, at any value, must carry a signed Engineering
     Justification Report. Procurement does not accept the requisition without
     it, apart from that, all requisitions related to the MIN–MAX setup is
     progressing smoothly."

That last clause is the whole subtlety. Automatic min/max replenishment is
EXEMPT — the reorder point already is the justification, and gating it would
break the maintenance→procurement bridge that keeps spare stock alive. Anything
raised because something broke, or someone decided to improve or replace a part,
is not exempt.

The gate refuses on MISSING FIELDS as well as on a missing report, because a
justification that does not state criticality or the store stock check is exactly
the paperwork this control exists to stop.
"""
from datetime import datetime, timedelta, timezone

from app.db import get_db, utcnow
from app.maintenance import workflow

# DOAM Table 14 — "Request type"
REQUEST_TYPES = ["breakdown", "preventive", "predictive", "improvement", "consumable"]

# DOAM Table 14 — "Criticality"
CRITICALITIES = ["production_critical", "safety", "quality", "routine"]

STATUSES = ["draft", "submitted", "approved", "rejected"]

# Fields DOAM Table 14 makes mandatory. A report missing any of these cannot be
# submitted for the Engineering Head's signature.
REQUIRED_FIELDS = [
    ("machine_id", "asset / machine"),
    ("request_type", "request type"),
    ("description", "description"),
    ("root_cause", "root cause"),
    ("criticality", "criticality"),
    ("downtime_risk", "downtime or risk if not actioned"),
    ("stock_checked_with", "store stock check"),
    ("alternatives", "alternatives considered"),
]

# Departments whose purchases are MRO by nature, so a request raised straight in
# Procurement (no maintenance origin, no spare line) is still caught. Matched
# case-insensitively against pr_requests.department.
MRO_DEPARTMENTS = {"general maintenance", "maintenance", "engineering", "workshop"}

# §7.4.2 — an emergency may proceed, but the report follows within 24 hours.
EMERGENCY_GRACE_HOURS = 24


def emergency_deadline(created_at, supplied=None):
    """When an emergency report becomes overdue: creation + EMERGENCY_GRACE_HOURS.

    The grace period was a constant nothing read — the due date was whatever the
    author typed into a free-text box, so "within 24 hours" was enforced by the
    person it constrains. It is now DERIVED, and a supplied value may only bring
    the deadline FORWARD: a team that wants to hold itself to four hours may,
    nobody gets to grant themselves a week.
    """
    try:
        base = datetime.strptime(str(created_at)[:19], "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        base = datetime.now(timezone.utc).replace(tzinfo=None)
    limit = base + timedelta(hours=EMERGENCY_GRACE_HOURS)
    cap = limit.strftime("%Y-%m-%d %H:%M:%S")
    # PARSED, not string-compared: the browser sends "2026-08-19T10:00" and a
    # hand-typed "1" used to sort below every date and become the deadline.
    # Anything unparseable falls back to the cap.
    supplied = str(supplied or "").strip().replace("T", " ")[:19]
    sup = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            sup = datetime.strptime(supplied, fmt)
            break
        except ValueError:
            pass
    return sup.strftime("%Y-%m-%d %H:%M:%S") if sup and sup < limit else cap


def emergency_overdue(row, now=None):
    """Is this emergency report past its 24-hour deadline and still unsigned?

    The reader that makes emergency_deadline mean something — called by the
    maintenance SLA sweep, which raises the notification.
    """
    if not row or not row["is_emergency"] or row["status"] == "approved":
        return False
    due = str(row["emergency_due_at"] or "")[:19]
    return bool(due) and due < (now or utcnow())[:19]

# The gate is OFF by default, and deliberately so.
#
# Switching it on stops every spares / MRO / maintenance-department requisition
# that has no Engineering-Head-signed report — which is precisely what DOAM §6
# demands, and precisely what would halt purchasing at T&C on the strength of a
# document whose own control block still reads "Draft v1.0" with a blank approval
# line. The report screens, the approval flow and this gate are all built and
# tested; turning it on is one setting once somebody signs the DOAM.
#
# Switch it on from Maintenance → Workflow & Governance → "Engineering
# justification gate (DOAM §6)", which writes the mnt_settings row through
# workflow.set_setting like every other governance knob.
EJR_GATE_SETTING = "ejr_gate"
# .get, not []: renaming the registry key must degrade the gate to "off", not
# raise at import time and take purchasing down with it.
EJR_GATE_DEFAULT = workflow.SETTINGS.get(EJR_GATE_SETTING, (False,))[0]


def gate_enabled(conn):
    """Is the DOAM §6 gate switched on? workflow.flag() is the same
    override -> default read every other governance knob uses, and it already
    resolves an unreadable settings table to the default — a blocking control
    must never switch itself ON because a query raised."""
    return workflow.flag(conn, EJR_GATE_SETTING)


def _missing(row):
    """Which DOAM-mandatory fields are still blank on this report."""
    out = []
    for col, label in REQUIRED_FIELDS:
        val = row[col] if col in row.keys() else None
        if val is None or (isinstance(val, str) and not val.strip()):
            out.append(label)
    # stock_on_hand may legitimately be 0 ("none in store"), so it is checked for
    # presence, not truthiness — 0 is an answer, blank is not.
    if ("stock_on_hand" not in row.keys()) or row["stock_on_hand"] is None:
        out.append("quantity on hand")
    return out


def missing_labels(conn, ejr_id):
    """Which DOAM-mandatory fields this report still lacks, as readable labels.
    Empty list means the report is complete. Used by the detail page so the
    author is told exactly what to fill in rather than just being refused."""
    row = conn.execute("SELECT * FROM mnt_eng_justifications WHERE id=? AND is_active=1",
                       (ejr_id,)).fetchone()
    return _missing(row) if row else []


def ejr_required(pr, items=None):
    """Does DOAM §6 require an Engineering Justification for this request?

    Returns (required, reason). `reason` names the signal so the UI can explain
    itself and the audit trail records WHY the gate applied.
    """
    src = ((pr["source_module"] if "source_module" in pr.keys() else "") or "").strip().lower()
    ref = ((pr["source_ref"] if "source_ref" in pr.keys() else "") or "").strip().lower()

    # The DOAM's own exemption: automatic min/max replenishment. The bridge tags
    # these 'maintenance' + 'spare:<id>'; the reorder level IS the justification.
    if src == "maintenance" and ref.startswith("spare:"):
        return False, "min_max_replenishment"

    # Raised from a maintenance ticket or anything else maintenance-originated.
    if src == "maintenance":
        return True, "maintenance_origin"

    # A line for a stocked spare part, whatever the request calls itself.
    if items:
        for it in items:
            if ("spare_id" in it.keys()) and it["spare_id"]:
                return True, "spare_part_line"

    # Raised directly in Procurement by a maintenance/engineering department.
    dept = ((pr["department"] if "department" in pr.keys() else "") or "").strip().lower()
    if dept in MRO_DEPARTMENTS:
        return True, "mro_department"

    return False, "not_mro"


def ejr_gate_check(conn, pr, items=None):
    """The gate. Returns (ok, reason).

    ok=True means Procurement may proceed. Called at the Purchasing stage, which
    is Procurement's acceptance point — the DOAM's "Procurement does not accept
    the requisition without it".

    Returns ok=True with reason "gate_disabled" while the control is switched
    off, so the caller can tell "this request did not need a report" apart from
    "the DOAM is not in force yet" — those are different facts and an audit trail
    should not conflate them.
    """
    if not gate_enabled(conn):
        return True, "gate_disabled"
    required, why = ejr_required(pr, items)
    if not required:
        return True, why

    ejr_id = pr["ejr_id"] if "ejr_id" in pr.keys() else None
    if not ejr_id:
        return False, "ejr_missing"

    row = conn.execute(
        "SELECT * FROM mnt_eng_justifications WHERE id=? AND is_active=1",
        (ejr_id,)).fetchone()
    if not row:
        return False, "ejr_missing"
    if row["status"] == "rejected":
        return False, "ejr_rejected"
    if row["status"] != "approved":
        # Table 13 step 3: signed BEFORE the requisition proceeds.
        return False, "ejr_not_approved"
    gaps = _missing(row)
    if gaps:
        # Belt and braces: submit() blocks this, but a report edited or imported
        # around the UI must not sail through on its status column alone.
        return False, "ejr_incomplete:" + ", ".join(gaps)
    return True, "ejr_approved"


def create(conn, data, user):
    """Create a draft report. Nothing is enforced yet — a draft is allowed to be
    incomplete so it can be filled in over a shift."""
    now = utcnow()
    n = (conn.execute("SELECT COUNT(*) c FROM mnt_eng_justifications").fetchone()["c"] or 0) + 1
    ejr_no = f"T&C-PUF-09-{now[:4]}-{n:05d}"
    cur = conn.execute(
        "INSERT INTO mnt_eng_justifications (ejr_no, machine_id, location, request_type, "
        "description, root_cause, criticality, downtime_risk, stock_on_hand, "
        "stock_checked_with, alternatives, is_emergency, emergency_due_at, status, "
        "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?)",
        (ejr_no, data.get("machine_id") or None, data.get("location"), data.get("request_type"),
         data.get("description"), data.get("root_cause"), data.get("criticality"),
         data.get("downtime_risk"),
         data.get("stock_on_hand") if data.get("stock_on_hand") not in ("", None) else None,
         data.get("stock_checked_with"), data.get("alternatives"),
         1 if data.get("is_emergency") else 0,
         # DOAM §7.4.2 grace period, applied rather than merely written down.
         emergency_deadline(now, data.get("emergency_due_at"))
         if data.get("is_emergency") else None,
         (user or {}).get("username"), now))
    return cur.lastrowid, ejr_no


def submit(conn, ejr_id):
    """Send the report for the Engineering Head's signature. Refuses while any
    DOAM-mandatory field is blank, which is what stops a token justification."""
    row = conn.execute("SELECT * FROM mnt_eng_justifications WHERE id=? AND is_active=1",
                       (ejr_id,)).fetchone()
    if not row:
        return False, "not_found"
    if row["status"] not in ("draft", "rejected"):
        return False, "already_submitted"
    gaps = _missing(row)
    if gaps:
        return False, "incomplete:" + ", ".join(gaps)
    conn.execute("UPDATE mnt_eng_justifications SET status='submitted', submitted_at=? WHERE id=?",
                 (utcnow(), ejr_id))
    return True, "submitted"


def decide(conn, ejr_id, approve, user, note=None, signature=None):
    """The Engineering Head's technical approval (DOAM Table 13 step 3, L3).

    Self-approval is refused: the DOAM's segregation of duties (§3.4) says no
    person may approve a transaction that names them as requestor, and a report
    is the front end of exactly such a transaction.
    """
    row = conn.execute("SELECT * FROM mnt_eng_justifications WHERE id=? AND is_active=1",
                       (ejr_id,)).fetchone()
    if not row:
        return False, "not_found"
    if row["status"] != "submitted":
        return False, "not_pending"
    who = (user or {}).get("username")
    if approve and who and who == row["created_by"]:
        return False, "self_approval_blocked"
    if approve and _missing(row):
        return False, "incomplete"
    conn.execute(
        "UPDATE mnt_eng_justifications SET status=?, decided_by=?, decided_at=?, "
        "decision_note=?, decided_signature=? WHERE id=?",
        ("approved" if approve else "rejected", who, utcnow(), note, signature, ejr_id))
    return True, "approved" if approve else "rejected"


def listing(conn, status=None, limit=200):
    sql = ("SELECT j.*, m.code mcode, m.name mname FROM mnt_eng_justifications j "
           "LEFT JOIN mnt_machines m ON m.id=j.machine_id WHERE j.is_active=1")
    args = []
    if status:
        sql += " AND j.status=?"
        args.append(status)
    sql += " ORDER BY j.id DESC LIMIT ?"
    args.append(int(limit))
    return conn.execute(sql, tuple(args)).fetchall()


def get(conn, ejr_id):
    return conn.execute(
        "SELECT j.*, m.code mcode, m.name mname, m.location mlocation "
        "FROM mnt_eng_justifications j LEFT JOIN mnt_machines m ON m.id=j.machine_id "
        "WHERE j.id=? AND j.is_active=1", (ejr_id,)).fetchone()


def approved_for_picker(conn, limit=100):
    """Approved reports a requester may attach to a purchase request."""
    return conn.execute(
        "SELECT j.id, j.ejr_no, j.request_type, j.criticality, m.code mcode "
        "FROM mnt_eng_justifications j LEFT JOIN mnt_machines m ON m.id=j.machine_id "
        "WHERE j.is_active=1 AND j.status='approved' ORDER BY j.id DESC LIMIT ?",
        (int(limit),)).fetchall()
