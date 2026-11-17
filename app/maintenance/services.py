"""
TC Platform — Maintenance service layer.

All workflow logic + transactions live here so routes stay thin. The critical
spare-part issue is fully transactional (rolls back on any failure) and stock
can never go negative. Status transitions are guarded.
"""
import logging
import math
import uuid
from datetime import datetime, timedelta, timezone

from werkzeug.utils import secure_filename

from config import Config
from app.db import get_db
from app.maintenance.constants import TICKET_TRANSITIONS, PM_FREQUENCY_DAYS
from app.maintenance import workflow as wf

log = logging.getLogger("tc.maintenance")

YEAR = None  # set lazily per call to avoid import-time clock reads in tooling

# Statuses that count as "actively being worked" -- a machine shouldn't collect a
# SECOND ticket for the same in-progress breakdown. Excludes 'resolved' (repair
# already done, only awaiting closure) so a genuinely new fault isn't blocked.
OPEN_TICKET_STATUSES = (
    "submitted", "under_review", "assigned", "diagnosis",
    "spare_required", "waiting_stock", "waiting_approval", "approved_issue",
    "parts_issued", "repair", "testing", "reopened",
)
# SLA target scales with priority (critical is far tighter than the base hours).
# The numbers now live in workflow.SLA_FACTOR so Workflow & Governance can show
# and override them; this name stays as the module's constant reference.
_SLA_FACTOR = wf.SLA_FACTOR


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _setting(conn, key, default=None):
    r = conn.execute("SELECT value FROM mnt_settings WHERE key=?", (key,)).fetchone()
    return r["value"] if (r and r["value"] not in (None, "")) else default


def _parse_dt(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None


def _year():
    return datetime.now(timezone.utc).year


def _minutes_between(a, b):
    start, end = _parse_dt(a), _parse_dt(b)
    if start is None or end is None:
        return None
    return max(0, int((end - start).total_seconds() // 60))


def repair_minutes(row):
    minutes = _minutes_between(row["repair_started_at"], row["repair_done_at"])
    return minutes if minutes and minutes > 0 else row["total_downtime_min"] or None


def complete_pm_wo(wid, notes, user, ip=None):
    conn = get_db()
    try:
        now = _now()
        claimed = conn.execute(
            "UPDATE mnt_pm_work_orders SET status='completed', completed_at=?, notes=? "
            "WHERE id=? AND COALESCE(status,'')<>'completed'", (now, notes, wid))
        if claimed.rowcount != 1:
            return False, "already_completed"
        plan = conn.execute(
            "SELECT p.* FROM mnt_pm_plans p JOIN mnt_pm_work_orders w ON w.plan_id=p.id "
            "WHERE w.id=?", (wid,)).fetchone()
        next_due = None
        if plan:
            interval = plan["interval_days"]
            if interval is None or interval <= 0:
                interval = PM_FREQUENCY_DAYS.get(plan["frequency"], 30)
            completed = _parse_dt(now).date()
            next_due = (completed + timedelta(days=interval)).isoformat()
            conn.execute("UPDATE mnt_pm_plans SET last_done=?, next_due=? WHERE id=?",
                         (completed.isoformat(), next_due, plan["id"]))
        audit(conn, user, "pm_complete", "pm_wo", wid, None, next_due, ip=ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def doc_no(prefix, n):
    return f"{prefix}-{_year()}-{n:06d}"


# --- audit + notifications -------------------------------------------------
def audit(conn, user, action, entity_type, entity_id,
          old=None, new=None, comment=None, ip=None):
    conn.execute(
        """INSERT INTO mnt_audit (username,role,action,entity_type,entity_id,
           old_value,new_value,comment,ip,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user.get("username") if user else "system", user.get("role") if user else "",
         action, entity_type, entity_id,
         str(old) if old is not None else None,
         str(new) if new is not None else None, comment, ip, _now()))


def notify(conn, target_role, title, message, entity_type, entity_id,
           severity="info", action_url=None, exclude=None):
    conn.execute(
        """INSERT INTO mnt_notifications (target_role,title,message,entity_type,
           entity_id,severity,action_url,is_read,created_at) VALUES (?,?,?,?,?,?,?,0,?)""",
        (target_role, title, message, entity_type, entity_id, severity, action_url, _now()))
    # also surface critical/warning items on the platform bell
    if severity in ("critical", "warning"):
        # role-targeted, one row per user (own read state); nobody left -> super_admin
        usernames = ([u for u in _role_usernames(conn, target_role) if u != exclude]
                     or _role_usernames(conn, "super_admin"))
        if usernames:
            _PENDING_NOTES.setdefault(conn, []).append(
                (usernames, title, message, (), action_url, severity, False))


def _usernames_for(conn, names):
    usernames = []
    for name in dict.fromkeys(name.strip() for name in (names or []) if name and name.strip()):
        exact = conn.execute(
            "SELECT username FROM users WHERE is_active=1 AND username=?", (name,)).fetchone()
        if exact:
            usernames.append(exact["username"])
            continue
        matches = conn.execute(
            "SELECT username FROM users WHERE is_active=1 AND full_name=?", (name,)).fetchall()
        if len(matches) == 1:
            usernames.append(matches[0]["username"])
    return list(dict.fromkeys(usernames))


def _role_usernames(conn, role):
    if not (role or "").strip():
        return []
    return [row["username"] for row in conn.execute(
        "SELECT username FROM users WHERE is_active=1 AND role=?", (role,)).fetchall()]


def _requester_usernames(conn, ticket_id):
    ticket = conn.execute(
        "SELECT requester_user_id, requester FROM mnt_tickets WHERE id=?",
        (ticket_id,)).fetchone()
    if not ticket:
        return []
    if ticket["requester_user_id"] is not None:
        user = conn.execute("SELECT username FROM users WHERE id=? AND is_active=1",
                            (ticket["requester_user_id"],)).fetchone()
        return [user["username"]] if user else []
    return _usernames_for(conn, [ticket["requester"]])


_PENDING_NOTES = {}


def _notify_people(conn, usernames, title, message, link, args=(), actor=None):
    """Queue a personal bell notice; _flush_notes sends it AFTER the workflow commits.
    Writing it mid-transaction risked a failed INSERT rolling back the workflow on
    PostgreSQL (the shared connection rolls back the whole transaction on error)."""
    usernames = list(dict.fromkeys(name for name in usernames
                                   if name != (actor or {}).get("username")))
    if usernames:
        _PENDING_NOTES.setdefault(conn, []).append((usernames, title, message, args, link, "info", True))


def _flush_notes(conn):
    notes = _PENDING_NOTES.pop(conn, [])
    if not notes:
        return
    try:
        from app.approvals.services import notify_users
        from app.maintenance.messages import translate
        # a user already getting the personal notice skips the same-titled role copy
        personal = {(u, n[1]) for n in notes if n[6] for u in n[0]}
        for usernames, title, message, args, link, severity, email in notes:
            if not email:
                usernames = [u for u in usernames if (u, title) not in personal]
                if not usernames:
                    continue
            placeholders = ",".join("?" for _ in usernames)
            groups = {}
            for row in conn.execute(
                    f"SELECT username, lang_pref FROM users WHERE username IN ({placeholders})",
                    tuple(usernames)).fetchall():
                groups.setdefault(row["lang_pref"] or "en", []).append(row["username"])
            for lang, group in groups.items():
                text = translate(message, lang)
                if args:
                    text = text % tuple(args)
                if email:
                    notify_users(conn, group, severity, translate(title, lang), text,
                                 link=link, module="maintenance")
                else:
                    for username in group:
                        conn.execute(
                            "INSERT INTO notifications "
                            "(severity, module, title, message, target_user, link, created_at) "
                            "VALUES (?,?,?,?,?,?,?)",
                            (severity, "maintenance", translate(title, lang), text,
                             username, link, _now()))
        conn.commit()
    except Exception:
        try:
            conn.rollback()  # only the notices are lost; the workflow is already committed
        except Exception:
            pass


# --- status helpers --------------------------------------------------------
_STATUS_STAMP = {
    "under_review": "reviewed_at", "assigned": "assigned_at",
    "diagnosis": "diag_started_at", "waiting_approval": "approval_started_at",
    "approved_issue": "approval_done_at", "parts_issued": "issued_at",
    "repair": "repair_started_at", "testing": "repair_done_at",
    "resolved": "testing_done_at", "closed": "closed_at", "reopened": "reopened_at",
}


def can_transition(current, target):
    return target in TICKET_TRANSITIONS.get(current, set())


def _as_ticket_id(value):
    """Coerce a ticket id to int, or None when it isn't one.

    These services are called cross-module, and create_ticket / create_request
    return a (id, err) TUPLE. Handing that whole tuple on used to travel all the
    way into the driver as a bind parameter and surface as the meaningless
    "Error binding parameter 1: type 'tuple' is not supported". A shared service
    given the wrong type must refuse with a reason code like every other refusal
    here. Only the type is checked -- no status, stock or approval rule changes.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def set_ticket_status(conn, ticket_id, target, user, ip=None, force=False, comment=None):
    ticket_id = _as_ticket_id(ticket_id)
    if ticket_id is None:
        return False, "bad_ticket_id"
    row = conn.execute("SELECT status FROM mnt_tickets WHERE id=?", (ticket_id,)).fetchone()
    if not row:
        return False, "ticket_not_found"
    current = row["status"]
    if current == target:
        return True, ""
    if not force and not can_transition(current, target):
        return False, "invalid_transition"
    sets = ["status=?"]
    vals = [target]
    stamp = _STATUS_STAMP.get(target)
    if stamp:
        sets.append(f"{stamp}=?")
        vals.append(_now())
    vals.append(ticket_id)
    conn.execute(f"UPDATE mnt_tickets SET {', '.join(sets)} WHERE id=?", vals)
    audit(conn, user, "ticket_status", "ticket", ticket_id, current, target, comment, ip)
    return True, ""


# --- inventory helpers -----------------------------------------------------
def available_qty(conn, spare_id):
    """Physical stock minus what's already committed (approved-but-not-yet-issued
    requests). This is the number that should gate any new commitment -- reading
    raw stock_qty is what let two requests each claim the last part."""
    sp = conn.execute("SELECT stock_qty, reserved_qty FROM mnt_spare_parts WHERE id=?",
                      (spare_id,)).fetchone()
    if not sp:
        return 0.0
    return (sp["stock_qty"] or 0) - (sp["reserved_qty"] or 0)


def _reserve(conn, spare_id, qty):
    """Add/release a reservation (qty may be negative to release)."""
    # CASE, not SQLite's two-argument MAX(0,...) — PostgreSQL's max() is an aggregate
    # and rejects it, so on Render this statement raised and every reservation was
    # lost. app/warehouse/services.py::_release carries the same fix.
    conn.execute(
        "UPDATE mnt_spare_parts SET reserved_qty = CASE "
        "WHEN COALESCE(reserved_qty,0)+? < 0 THEN 0 "
        "ELSE COALESCE(reserved_qty,0)+? END WHERE id=?", (qty, qty, spare_id))


def _move_stock(conn, spare_id, mtype, qty, user, ticket_id=None, request_id=None,
                machine_id=None, notes=None, approved_by=None, unit_cost=None):
    """Apply a signed stock delta and record a movement. Returns (ok, msg)."""
    sp = conn.execute("SELECT stock_qty FROM mnt_spare_parts WHERE id=?", (spare_id,)).fetchone()
    if not sp:
        return False, "spare_not_found"
    before = sp["stock_qty"] or 0
    after = before + qty
    if after < 0:
        return False, "stock_would_go_negative"
    conn.execute("UPDATE mnt_spare_parts SET stock_qty=? WHERE id=?", (after, spare_id))
    # Number the movement from its own row id -- unique and collision-free. The
    # old COUNT(*)+1 scheme repeated numbers after any delete and clashed with the
    # seed's id-based numbers (violating the UNIQUE constraint).
    cur = conn.execute(
        """INSERT INTO mnt_stock_movements (type,spare_id,qty,before_qty,
           after_qty,ticket_id,request_id,machine_id,performed_by,approved_by,notes,created_at,unit_cost)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mtype, spare_id, qty, before, after, ticket_id, request_id,
         machine_id, user.get("username") if user else "system", approved_by, notes, _now(), unit_cost))
    conn.execute("UPDATE mnt_stock_movements SET movement_no=? WHERE id=?",
                 (doc_no("STK", cur.lastrowid), cur.lastrowid))
    return True, ""


# --- ticket lifecycle ------------------------------------------------------
def create_ticket(data, user, ip=None, submit=True):
    """Returns (ticket_id, err). err '' on success; 'description_required' or
    'duplicate_open:<ticket_no>' otherwise (pass data['allow_duplicate']=True to
    override the duplicate guard)."""
    conn = get_db()
    try:
        if not (data.get("description") or "").strip():
            return None, "description_required"
        # Guard: don't let one machine collect several open tickets for the same
        # breakdown. If it already has an open ticket, point back to it.
        # (the guard itself is switchable from Workflow & Governance; default ON,
        # so with no override row this is the same unconditional check as before)
        mid = data.get("machine_id") or None
        # Looked up whether or not the override is set, because a control that
        # can be overridden from the screen has to leave a trace saying it was:
        # the override is now offered on the form after a refusal, and "a second
        # open ticket appeared on this machine" should be answerable afterwards.
        overridden = None
        if mid and submit and wf.dup_guard_on(conn):
            dup = conn.execute(
                "SELECT ticket_no FROM mnt_tickets WHERE machine_id=? AND is_active=1 "
                "AND status IN (%s) ORDER BY id DESC LIMIT 1" % ",".join("?" * len(OPEN_TICKET_STATUSES)),
                (mid, *OPEN_TICKET_STATUSES)).fetchone()
            if dup:
                if not data.get("allow_duplicate"):
                    return None, "duplicate_open:%s" % dup["ticket_no"]
                # Only when something was actually overridden. Ticking the box on
                # a machine whose other ticket has since been closed overrides
                # nothing, and recording it would be a false signal.
                overridden = dup["ticket_no"]

        priority = data.get("priority", "medium")
        cur = conn.execute(
            """INSERT INTO mnt_tickets
               (requester,requester_user_id,department,area,line_no,machine_id,machine_code,
                issue_category,description,priority,severity,safety_impact,production_stopped,
                est_downtime_min,shift,remarks,status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data.get("requester") or (user.get("full_name") or user.get("username")),
             user.get("id"), data.get("department"), data.get("area"), data.get("line_no"),
             mid, data.get("machine_code"),
             data.get("issue_category"), data.get("description"),
             priority, data.get("severity", "moderate"),
             1 if data.get("safety_impact") else 0,
             1 if data.get("production_stopped") else 0,
             int(data.get("est_downtime_min") or 0), data.get("shift"),
             data.get("remarks"), "submitted" if submit else "draft", _now()))
        tid = cur.lastrowid
        # Start the SLA clock at creation (scaled by priority) -- previously the
        # response/resolution due dates were only set at assignment, so a fresh
        # critical ticket had no clock and sla_breach was never computed.
        try:
            # Same arithmetic as before (base hours x priority factor); each of the
            # three numbers is now an admin-overridable setting whose default is the
            # old literal, and a bad stored value falls back to that literal.
            resp_h = wf.sla_hours(conn, priority, 4.0, "response_sla_hours")
            reso_h = wf.sla_hours(conn, priority, 24.0, "resolution_sla_hours")
            base = datetime.now(timezone.utc).replace(tzinfo=None)
            conn.execute(
                "UPDATE mnt_tickets SET ticket_no=?, response_due=?, resolution_due=? WHERE id=?",
                (doc_no("MNT", tid),
                 (base + timedelta(hours=resp_h)).strftime("%Y-%m-%d %H:%M:%S"),
                 (base + timedelta(hours=reso_h)).strftime("%Y-%m-%d %H:%M:%S"), tid))
        except Exception:
            conn.execute("UPDATE mnt_tickets SET ticket_no=? WHERE id=?", (doc_no("MNT", tid), tid))
        # if machine reported as production-stopped, reflect machine status
        if mid and data.get("production_stopped"):
            conn.execute("UPDATE mnt_machines SET status='stopped' WHERE id=?", (mid,))
        audit(conn, user, "ticket_create", "ticket", tid, None, doc_no("MNT", tid), ip=ip)
        if overridden:
            audit(conn, user, "duplicate_override", "ticket", tid,
                  overridden, doc_no("MNT", tid),
                  comment="raised while %s was still open" % overridden, ip=ip)
        if submit:
            notify(conn, "maintenance_manager", "New maintenance ticket",
                   f"{doc_no('MNT', tid)} on {data.get('machine_code') or 'a machine'}",
                   "ticket", tid, "warning", f"/maintenance/tickets/{tid}")
        conn.commit()
        _flush_notes(conn)
        return tid, ""
    finally:
        conn.close()


def review_assign(ticket_id, technician, priority, response_due, resolution_due, user, ip=None):
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return False, "bad_ticket_id"
    conn = get_db()
    try:
        ok, msg = set_ticket_status(conn, ticket_id, "assigned", user, ip, force=True)
        wo = doc_no("WO", ticket_id)
        conn.execute(
            "UPDATE mnt_tickets SET assigned_to=?, work_order_no=?, priority=COALESCE(?,priority),"
            " response_due=COALESCE(?,response_due), resolution_due=COALESCE(?,resolution_due), "
            "reviewed_at=COALESCE(reviewed_at,?) WHERE id=?",
            (technician, wo, priority or None, response_due or None, resolution_due or None, _now(), ticket_id))
        audit(conn, user, "ticket_assign", "ticket", ticket_id, None, technician, ip=ip)
        notify(conn, "maintenance_technician", "Work order assigned",
               f"{doc_no('MNT', ticket_id)} assigned to you", "ticket", ticket_id,
               "info", f"/maintenance/tickets/{ticket_id}")
        _notify_people(conn, _usernames_for(conn, [technician]), "Work order assigned",
                       "%s assigned to you",
                       f"/maintenance/tickets/{ticket_id}",
                       args=(doc_no('MNT', ticket_id),), actor=user)
        conn.commit()
        _flush_notes(conn)
        return True, ""
    finally:
        conn.close()


def _release_ticket_reservations(conn, ticket_id):
    """Free any parts reserved by this ticket's still-approved (unissued) requests,
    and mark those requests cancelled -- so cancelling/rejecting a ticket can't
    strand reserved stock as permanently unavailable."""
    reqs = conn.execute("SELECT id FROM mnt_requests WHERE ticket_id=? AND status='approved'",
                        (ticket_id,)).fetchall()
    for r in reqs:
        for it in conn.execute("SELECT spare_id, qty_approved FROM mnt_request_items "
                                "WHERE request_id=? AND spare_id IS NOT NULL", (r["id"],)).fetchall():
            _reserve(conn, it["spare_id"], -(it["qty_approved"] or 0))
        conn.execute("UPDATE mnt_requests SET status='cancelled' WHERE id=?", (r["id"],))


def reject_ticket(ticket_id, reason, user, ip=None):
    if not (reason or "").strip():
        return False, "reason_required"
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return False, "bad_ticket_id"
    conn = get_db()
    try:
        conn.execute("UPDATE mnt_tickets SET rejection_reason=? WHERE id=?", (reason, ticket_id))
        _release_ticket_reservations(conn, ticket_id)
        ok, msg = set_ticket_status(conn, ticket_id, "rejected", user, ip, force=True, comment=reason)
        audit(conn, user, "ticket_reject", "ticket", ticket_id, None, reason, comment=reason, ip=ip)
        conn.commit()
        return ok, msg
    finally:
        conn.close()


def add_diagnosis(ticket_id, d, user, ip=None):
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return False, "bad_ticket_id"
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO mnt_diagnosis (ticket_id,technician,fault_found,root_cause,diagnosis,
               required_action,spare_needed,temp_fix,can_run_partial,safety_risk,est_repair_min,notes,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ticket_id, user.get("full_name") or user.get("username"), d.get("fault_found"),
             d.get("root_cause"), d.get("diagnosis"), d.get("required_action"),
             1 if d.get("spare_needed") else 0, 1 if d.get("temp_fix") else 0,
             1 if d.get("can_run_partial") else 0, 1 if d.get("safety_risk") else 0,
             int(d.get("est_repair_min") or 0), d.get("notes"), _now()))
        conn.execute("UPDATE mnt_tickets SET diag_done_at=? WHERE id=?", (_now(), ticket_id))
        target = "spare_required" if d.get("spare_needed") else "repair"
        set_ticket_status(conn, ticket_id, target, user, ip, force=True)
        audit(conn, user, "diagnosis_add", "ticket", ticket_id, None, d.get("root_cause"), ip=ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --- spare part requests + approvals ---------------------------------------
def _pick_matrix_levels(conn, items):
    """Choose an approval-matrix rule and return non-store approver roles."""
    cost = 0.0
    critical = False
    for it in items:
        sp = conn.execute("SELECT avg_cost,criticality FROM mnt_spare_parts WHERE id=?",
                          (it["spare_id"],)).fetchone()
        if sp:
            cost += (sp["avg_cost"] or 0) * it["qty"]
            if sp["criticality"] == "critical":
                critical = True
    # Rule choice is unchanged; the 100 is now wf.critical_threshold(), whose
    # default is the critical matrix rule's own cost_threshold (100 as seeded).
    rule, rule_key = None, "std"
    if critical or cost >= wf.critical_threshold(conn):
        rule = conn.execute("SELECT levels FROM mnt_approval_matrix WHERE active=1 AND "
                            "part_criticality='critical' LIMIT 1").fetchone()
        if rule:
            rule_key = "crit"
    if not rule:
        rule = conn.execute("SELECT levels FROM mnt_approval_matrix WHERE active=1 "
                            "ORDER BY id LIMIT 1").fetchone()
    # Same rungs as the matrix defines (storekeeper is the issuer, not a vote),
    # with any valid per-stage role override applied.
    return wf.approver_roles(conn, rule_key, rule)


def _route_request(conn, rid, ticket_id, items, user, ip=None, any_out=None):
    if any_out is None:
        any_out = any(it["qty_requested"] > available_qty(conn, it["spare_id"])
                  for it in conn.execute("SELECT spare_id,qty_requested FROM mnt_request_items WHERE request_id=?",
                                         (rid,)).fetchall())
    if any_out:
        conn.execute("UPDATE mnt_requests SET status='out_of_stock' WHERE id=?", (rid,))
        set_ticket_status(conn, ticket_id, "waiting_stock", user, ip, force=True)
        notify(conn, "storekeeper", "Out-of-stock spare request",
               f"{doc_no('SPR', rid)} needs parts not in stock", "request", rid,
               "warning", f"/maintenance/requests")
        notify(conn, "maintenance_manager", "Out-of-stock spare request",
               f"{doc_no('SPR', rid)} requires purchase", "request", rid, "warning")
    else:
        # generate approval steps
        roles = _pick_matrix_levels(conn, items)
        for i, role in enumerate(roles, start=1):
            conn.execute(
                "INSERT INTO mnt_approvals (request_id,level,approver_role,status,created_at) "
                "VALUES (?,?,?,?,?)", (rid, i, role, "pending", _now()))
        conn.execute("UPDATE mnt_requests SET status='waiting_approval' WHERE id=?", (rid,))
        set_ticket_status(conn, ticket_id, "waiting_approval", user, ip, force=True)
        first_role = roles[0] if roles else "maintenance_manager"
        people = [name for name in _role_usernames(conn, first_role)
                  if name != user.get("username")]
        notify(conn, first_role, "Spare request awaiting approval",
               f"{doc_no('SPR', rid)} needs your approval", "request", rid,
               "info" if people else "warning", "/maintenance/approvals",
               exclude=user.get("username"))
        _notify_people(conn, people, "Spare request awaiting approval",
                       "%s needs your approval", "/maintenance/approvals",
                       args=(doc_no('SPR', rid),), actor=user)
    return any_out


def _insert_request_items(conn, rid, items):
    any_out = False
    for it in items:
        sp = conn.execute("SELECT code,name,uom,stock_qty FROM mnt_spare_parts WHERE id=?",
                         (it["spare_id"],)).fetchone()
        if not sp:
            continue
        # Available = physical stock minus parts already committed to other
        # approved requests, so two requests can't both claim the last one.
        avail = available_qty(conn, it["spare_id"])
        if it["qty"] > avail:
            any_out = True
        conn.execute(
            """INSERT INTO mnt_request_items (request_id,spare_id,part_code,part_name,
               qty_requested,uom,available_at_request,notes) VALUES (?,?,?,?,?,?,?,?)""",
            (rid, it["spare_id"], sp["code"], sp["name"], it["qty"], sp["uom"],
             avail, it.get("notes")))
    return any_out


def create_request(ticket_id, items, reason, urgency, user, ip=None):
    """items: list of dicts {spare_id, qty, notes}."""
    if not items:
        return None, "no_items"
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return None, "bad_ticket_id"
    conn = get_db()
    try:
        t = conn.execute("SELECT machine_id,machine_code,work_order_no FROM mnt_tickets WHERE id=?",
                         (ticket_id,)).fetchone()
        cur = conn.execute(
            """INSERT INTO mnt_requests (ticket_id,work_order_no,machine_id,technician,reason,
               urgency,status,created_at) VALUES (?,?,?,?,?,?,?,?)""",
            (ticket_id, t["work_order_no"] if t else None, t["machine_id"] if t else None,
             user.get("full_name") or user.get("username"), reason, urgency or "normal",
             "submitted", _now()))
        rid = cur.lastrowid
        conn.execute("UPDATE mnt_requests SET request_no=? WHERE id=?", (doc_no("SPR", rid), rid))

        any_out = _insert_request_items(conn, rid, items)

        conn.execute("UPDATE mnt_tickets SET parts_req_at=? WHERE id=?", (_now(), ticket_id))

        any_out = _route_request(conn, rid, ticket_id, items, user, ip, any_out=any_out)
        audit(conn, user, "request_create", "request", rid, None, doc_no("SPR", rid), ip=ip)
        conn.commit()
        _flush_notes(conn)
        if any_out:
            try:
                from app.maintenance.procure_bridge import raise_for_request
                raise_for_request(rid)
            except Exception:
                pass
        return rid, ""
    finally:
        conn.close()


def _reserve_request(conn, rid):
    """Reserve every line atomically, releasing this batch if any line is short."""
    reserved = []
    for it in conn.execute("SELECT spare_id,qty_approved FROM mnt_request_items "
                           "WHERE request_id=? AND spare_id IS NOT NULL", (rid,)).fetchall():
        need = it["qty_approved"] or 0
        if need <= 0:
            continue
        cur = conn.execute(
            "UPDATE mnt_spare_parts SET reserved_qty=COALESCE(reserved_qty,0)+? "
            "WHERE id=? AND (COALESCE(stock_qty,0)-COALESCE(reserved_qty,0))>=?",
            (need, it["spare_id"], need))
        if cur.rowcount != 1:
            for sid, qty in reserved:
                _reserve(conn, sid, -qty)
            return False
        reserved.append((it["spare_id"], need))
    return True


def resubmit_request(request_id, items, reason, user, ip=None):
    conn = get_db()
    try:
        if not items:
            return False, "no_items"
        req = conn.execute("SELECT r.ticket_id, t.status ticket_status FROM mnt_requests r "
                           "LEFT JOIN mnt_tickets t ON t.id=r.ticket_id WHERE r.id=?",
                           (request_id,)).fetchone()
        if req and req["ticket_status"] in ("closed", "cancelled", "rejected"):
            return False, "ticket_closed"
        # Claim first: a double-submitted form must not rebuild the ladder twice.
        if not req or conn.execute("UPDATE mnt_requests SET reason=?,status='submitted' "
                                   "WHERE id=? AND status='returned'",
                                   (reason, request_id)).rowcount != 1:
            conn.rollback()
            return False, "not_returned"
        conn.execute("DELETE FROM mnt_request_items WHERE request_id=?", (request_id,))
        any_out = _insert_request_items(conn, request_id, items)
        conn.execute("UPDATE mnt_approvals SET status='cancelled' WHERE request_id=? "
                     "AND status IN ('pending','returned')", (request_id,))
        any_out = _route_request(conn, request_id, req["ticket_id"], items, user, ip, any_out=any_out)
        audit(conn, user, "request_resubmit", "request", request_id, comment=reason, ip=ip)
        conn.commit()
        _flush_notes(conn)
        if any_out:
            try:
                from app.maintenance.procure_bridge import raise_for_request
                raise_for_request(request_id)
            except Exception:
                pass
        return True, ""
    finally:
        conn.close()


def _resume_waiting_requests(spare_id, user):
    """Resume eligible requests after stock commits; failures never undo the receipt."""
    try:
        conn = get_db()
        try:
            requests = conn.execute(
                "SELECT r.*,t.assigned_to FROM mnt_requests r JOIN mnt_tickets t ON t.id=r.ticket_id "
                "WHERE r.status='out_of_stock' AND t.status IN ('waiting_stock','spare_required') "
                "AND EXISTS (SELECT 1 FROM mnt_request_items i WHERE i.request_id=r.id AND i.spare_id=?) "
                "ORDER BY r.created_at,r.id", (spare_id,)).fetchall()
            still_short = []
            for req in requests:
                rid, tid = req["id"], req["ticket_id"]
                lines = conn.execute("SELECT spare_id,qty_requested,qty_approved FROM mnt_request_items "
                                     "WHERE request_id=?", (rid,)).fetchall()
                items = [dict(spare_id=i["spare_id"], qty=i["qty_requested"]) for i in lines]
                # qty_approved is only written by the FINAL approval, so it tells a request
                # that went short at sign-off (reserve now) from one short at creation or
                # resubmission (still needs its ladder; old rungs of a resubmit don't count).
                if lines and all((i["qty_approved"] or 0) > 0 for i in lines):
                    if not _reserve_request(conn, rid):
                        still_short.append(rid)
                        continue
                    cur = conn.execute("UPDATE mnt_requests SET status='approved',decided_at=? "
                                       "WHERE id=? AND status='out_of_stock'", (_now(), rid))
                    if cur.rowcount != 1:
                        for i in conn.execute("SELECT spare_id,qty_approved FROM mnt_request_items WHERE request_id=?", (rid,)).fetchall():
                            _reserve(conn, i["spare_id"], -(i["qty_approved"] or 0))
                        continue
                    set_ticket_status(conn, tid, "approved_issue", user, force=True)
                    notify(conn, "storekeeper", "Spare request approved - ready to issue",
                           f"{req['request_no']} approved", "request", rid, "warning", "/maintenance/requests")
                    _notify_people(conn, _requester_usernames(conn, tid) + _usernames_for(conn, [req["assigned_to"]]),
                                   "Spare request resumed", "%s: stock arrived, the request is back in the flow",
                                   f"/maintenance/tickets/{tid}", args=(req["request_no"],))
                elif items and not conn.execute(
                        "SELECT 1 FROM mnt_approvals WHERE request_id=? AND status='pending'",
                        (rid,)).fetchone() and all(i["qty"] <= available_qty(conn, i["spare_id"]) for i in items):
                    cur = conn.execute("UPDATE mnt_requests SET status='submitted' WHERE id=? AND status='out_of_stock'", (rid,))
                    if cur.rowcount != 1:
                        continue
                    _route_request(conn, rid, tid, items, user, None)
                else:
                    still_short.append(rid)
                    continue
                audit(conn, user, "request_resumed", "request", rid)
            conn.commit()
            _flush_notes(conn)
            # A receipt that did not cover the request (e.g. the linked PR was smaller
            # than the need) must not leave it with nothing on order.
            from app.maintenance.procure_bridge import raise_for_request
            for rid in still_short:
                raise_for_request(rid)
        except Exception:
            _PENDING_NOTES.pop(conn, None)
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception:
        log.warning("request resume failed for spare %s", spare_id, exc_info=True)


def return_parts(ticket_id, spare_id, qty, reason, user, ip=None):
    if not (reason or "").strip():
        return False, "reason_required"
    try:
        qty = float(qty)
    except (TypeError, ValueError):
        return False, "bad_qty"
    if not 0 < qty < float("inf"):
        return False, "bad_qty"
    if (ticket_id := _as_ticket_id(ticket_id)) is None or (spare_id := _as_ticket_id(spare_id)) is None:
        return False, "not_found"
    conn = get_db()
    try:
        # No-op write locks the ticket row, so two returns can't both pass the
        # issued-quantity check below (SQLite takes its write lock here too).
        conn.execute("UPDATE mnt_tickets SET cost=cost WHERE id=?", (ticket_id,))
        t = conn.execute("SELECT * FROM mnt_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not t or t["status"] in ('closed','cancelled','rejected'):
            conn.rollback()
            return False, "ticket_closed"
        issued = conn.execute("SELECT -COALESCE(SUM(qty),0) n FROM mnt_stock_movements "
                              "WHERE ticket_id=? AND spare_id=? AND type IN ('issue','return')",
                              (ticket_id, spare_id)).fetchone()["n"]
        if qty > issued:
            conn.rollback()
            return False, "exceeds_issued"
        sp = conn.execute("SELECT stock_qty,avg_cost FROM mnt_spare_parts WHERE id=?", (spare_id,)).fetchone()
        cost_row = conn.execute("SELECT -SUM(qty*COALESCE(unit_cost,?)) value,-SUM(qty) qty "
                                # net of earlier returns: what is still out, at what it cost
                                "FROM mnt_stock_movements WHERE ticket_id=? AND spare_id=? "
                                "AND type IN ('issue','return')",
                                (sp["avg_cost"] or 0, ticket_id, spare_id)).fetchone()
        cost = cost_row["value"] / cost_row["qty"] if cost_row["qty"] else (sp["avg_cost"] or 0)
        comment = f"Returned from {t['ticket_no']}: {reason}"
        ok, msg = _move_stock(conn, spare_id, 'return', qty, user, ticket_id=ticket_id,
                              machine_id=t["machine_id"], notes=comment, unit_cost=cost)
        if not ok:
            conn.rollback()
            return ok, msg
        before = sp["stock_qty"] or 0
        avg = (before * (sp["avg_cost"] or 0) + qty * cost) / (before + qty)
        conn.execute("UPDATE mnt_spare_parts SET avg_cost=? WHERE id=?", (round(avg, 4), spare_id))
        value = round(qty * cost, 2)
        conn.execute("UPDATE mnt_tickets SET cost=CASE WHEN COALESCE(cost,0)-? < 0 THEN 0 "
                     "ELSE COALESCE(cost,0)-? END WHERE id=?", (value, value, ticket_id))
        audit(conn, user, 'parts_return', 'ticket', ticket_id, comment=comment, ip=ip)
        audit(conn, user, 'stock_return', 'spare', spare_id, None, qty, comment=comment, ip=ip)
        conn.commit()
        _resume_waiting_requests(spare_id, user)
        return True, ""
    finally:
        conn.close()


def decide_approval(approval_id, decision, comment, user, ip=None, is_admin=False):
    conn = get_db()
    try:
        ap = conn.execute("SELECT * FROM mnt_approvals WHERE id=?", (approval_id,)).fetchone()
        if not ap:
            return False, "not_found"
        rid = ap["request_id"]
        req = conn.execute("SELECT status FROM mnt_requests WHERE id=?", (rid,)).fetchone()
        if (req and req["status"] in ('rejected', 'returned', 'cancelled')) or conn.execute(
                "SELECT 1 FROM mnt_approvals WHERE request_id=? AND status IN ('rejected','returned')",
                (rid,)).fetchone():
            return False, "request_closed"
        # Idempotency: a decided row can never be decided again. Re-approving an
        # already-approved request used to re-run the reservation loop and
        # inflate reserved_qty (double-click / refresh-repost = broken stock).
        if ap["status"] != "pending":
            return False, "already_decided"
        # Level authority: only the role this rung belongs to may sign it
        # (admins exempt). Without this, any approver could sign every level,
        # collapsing the multi-level matrix into one signature.
        is_admin = is_admin or (user or {}).get("role") == "super_admin"
        if not is_admin and ap["approver_role"] and \
                (user or {}).get("role") != ap["approver_role"]:
            return False, "wrong_role"
        # Rungs sign in order: level 2 can't sign while level 1 is still pending.
        if conn.execute("SELECT 1 FROM mnt_approvals WHERE request_id=? AND status='pending' "
                        "AND level < ?", (rid, ap["level"] or 1)).fetchone():
            return False, "earlier_level_pending"
        if decision == "reject":
            if not (comment or "").strip():
                return False, "reason_required"
            conn.execute("UPDATE mnt_approvals SET status='rejected',comment=?,decided_at=?,"
                         "approver_user=? WHERE id=?",
                         (comment, _now(), user.get("username"), approval_id))
            conn.execute("UPDATE mnt_approvals SET status='cancelled' WHERE request_id=? AND status='pending'", (rid,))
            conn.execute("UPDATE mnt_requests SET status='rejected',decided_at=? WHERE id=?",
                         (_now(), rid))
            req = conn.execute("SELECT ticket_id FROM mnt_requests WHERE id=?", (rid,)).fetchone()
            if req:
                set_ticket_status(conn, req["ticket_id"], "spare_required", user, ip, force=True,
                                  comment=comment)
                ticket_id = req["ticket_id"]
                _notify_people(conn, _requester_usernames(conn, ticket_id), "Spare request rejected",
                               "%s: %s", f"/maintenance/tickets/{ticket_id}",
                               args=(doc_no('SPR', rid), comment), actor=user)
            notify(conn, "maintenance_technician", "Spare request rejected",
                   f"Request {rid}: {comment}", "request", rid, "warning")
            audit(conn, user, "approval_reject", "request", rid, None, "rejected",
                  comment=comment, ip=ip)
            conn.commit()
            _flush_notes(conn)
            return True, ""

        if decision == "return":
            conn.execute("UPDATE mnt_approvals SET status='returned',comment=?,decided_at=?,"
                         "approver_user=? WHERE id=?",
                         (comment, _now(), user.get("username"), approval_id))
            conn.execute("UPDATE mnt_requests SET status='returned' WHERE id=?", (rid,))
            notify(conn, "maintenance_technician", "Spare request returned",
                   f"Request {rid} returned for clarification", "request", rid, "info")
            req = conn.execute("SELECT ticket_id FROM mnt_requests WHERE id=?", (rid,)).fetchone()
            if req:
                ticket_id = req["ticket_id"]
                _notify_people(conn, _requester_usernames(conn, ticket_id), "Spare request returned",
                               "%s returned for clarification",
                               f"/maintenance/tickets/{ticket_id}",
                               args=(doc_no('SPR', rid),), actor=user)
            audit(conn, user, "approval_return", "request", rid, None, "returned",
                  comment=comment, ip=ip)
            conn.commit()
            _flush_notes(conn)
            return True, ""

        any_out = False
        # approve
        conn.execute("UPDATE mnt_approvals SET status='approved',comment=?,decided_at=?,"
                     "approver_user=? WHERE id=?",
                     (comment, _now(), user.get("username"), approval_id))
        pending = conn.execute("SELECT COUNT(*) c FROM mnt_approvals WHERE request_id=? "
                              "AND status='pending'", (rid,)).fetchone()["c"]
        if pending == 0:
            # fully approved -> set approved qty = requested, ready for storekeeper to issue
            conn.execute("UPDATE mnt_request_items SET qty_approved=qty_requested WHERE request_id=?",
                         (rid,))
            req = conn.execute("SELECT ticket_id FROM mnt_requests WHERE id=?", (rid,)).fetchone()
            short = not _reserve_request(conn, rid)
            if short:
                any_out = True
                conn.execute("UPDATE mnt_requests SET status='out_of_stock',decided_at=? WHERE id=?",
                             (_now(), rid))
                if req:
                    set_ticket_status(conn, req["ticket_id"], "waiting_stock", user, ip, force=True)
                notify(conn, "storekeeper", "Approved request is now out of stock",
                       f"Request {rid} was approved but stock ran out meanwhile - replenish first",
                       "request", rid, "warning", "/maintenance/requests")
                notify(conn, "maintenance_manager", "Approved request needs purchase",
                       f"Request {rid}: parts no longer available", "request", rid, "warning")
            else:
                conn.execute("UPDATE mnt_requests SET status='approved',decided_at=? WHERE id=?",
                             (_now(), rid))
                if req:
                    set_ticket_status(conn, req["ticket_id"], "approved_issue", user, ip, force=True)
                    ticket_id = req["ticket_id"]
                    _notify_people(conn, _requester_usernames(conn, ticket_id), "Spare request approved",
                                   "%s approved", f"/maintenance/tickets/{ticket_id}",
                                   args=(doc_no('SPR', rid),), actor=user)
                notify(conn, "storekeeper", "Spare request approved - ready to issue",
                       f"Request {rid} approved", "request", rid, "warning",
                       "/maintenance/requests")
        else:
            next_rung = conn.execute(
                "SELECT approver_role FROM mnt_approvals WHERE request_id=? AND status='pending' "
                "ORDER BY level, id LIMIT 1", (rid,)).fetchone()
            if next_rung:
                _notify_people(conn, _role_usernames(conn, next_rung["approver_role"]),
                               "Spare request awaiting approval",
                               "%s needs your approval", "/maintenance/approvals",
                               args=(doc_no('SPR', rid),), actor=user)
        audit(conn, user, "approval_approve", "request", rid, None, "approved", comment=comment, ip=ip)
        conn.commit()
        _flush_notes(conn)
        if any_out:
            try:
                from app.maintenance.procure_bridge import raise_for_request
                raise_for_request(rid)
            except Exception:
                pass
        return True, ""
    finally:
        conn.close()


def issue_parts(request_id, received_by, user, ip=None):
    """THE critical transaction. Validates approval + stock, issues every item,
    writes vouchers + movements, decreases stock, updates statuses. Atomic."""
    conn = get_db()
    try:
        req = conn.execute("SELECT * FROM mnt_requests WHERE id=?", (request_id,)).fetchone()
        if not req:
            return False, "not_found"
        cur = conn.execute("UPDATE mnt_requests SET status='issued',decided_at=? "
                           "WHERE id=? AND status='approved'", (_now(), request_id))
        if cur.rowcount != 1:
            conn.rollback()
            return False, "not_approved"
        items = conn.execute("SELECT * FROM mnt_request_items WHERE request_id=?",
                            (request_id,)).fetchall()
        ticket = conn.execute("SELECT id,machine_code FROM mnt_tickets WHERE id=?",
                             (req["ticket_id"],)).fetchone()
        # validate all first (fail fast, no partial writes)
        for it in items:
            qty = it["qty_approved"] or it["qty_requested"]
            if qty <= 0:
                conn.rollback()
                return False, "invalid_qty"
            sp = conn.execute("SELECT stock_qty FROM mnt_spare_parts WHERE id=?",
                             (it["spare_id"],)).fetchone()
            if not sp or (sp["stock_qty"] or 0) < qty:
                conn.rollback()
                return False, "insufficient_stock"
        # apply
        parts_cost = 0.0
        for it in items:
            qty = it["qty_approved"] or it["qty_requested"]
            sp = conn.execute("SELECT avg_cost FROM mnt_spare_parts WHERE id=?",
                              (it["spare_id"],)).fetchone()
            ok, msg = _move_stock(conn, it["spare_id"], "issue", -qty, user,
                                  ticket_id=req["ticket_id"], request_id=request_id,
                                  machine_id=req["machine_id"],
                                  notes=f"Issued for {req['request_no']}",
                                  approved_by=user.get("username"), unit_cost=(sp["avg_cost"] or 0))
            if not ok:
                conn.rollback()
                return False, msg
            _reserve(conn, it["spare_id"], -qty)     # release the reservation we're now fulfilling
            parts_cost += qty * ((sp["avg_cost"] if sp else 0) or 0)
            conn.execute("UPDATE mnt_request_items SET qty_issued=? WHERE id=?", (qty, it["id"]))
            vc = conn.execute(
                """INSERT INTO mnt_vouchers (request_id,ticket_id,spare_id,part_code,
                   part_name,qty_issued,issued_by,received_by,machine_code,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (request_id, req["ticket_id"], it["spare_id"],
                 it["part_code"], it["part_name"], qty, user.get("username"),
                 received_by, ticket["machine_code"] if ticket else None, _now()))
            conn.execute("UPDATE mnt_vouchers SET voucher_no=? WHERE id=?",
                         (doc_no("ISS", vc.lastrowid), vc.lastrowid))
        # Roll the value of the issued parts into the ticket cost. close_ticket
        # then rolls the ticket cost into the machine's cost_to_date, so spare
        # spend actually flows into maintenance cost reporting.
        if req["ticket_id"] and parts_cost:
            conn.execute("UPDATE mnt_tickets SET cost=COALESCE(cost,0)+? WHERE id=?",
                         (round(parts_cost, 2), req["ticket_id"]))
        if req["ticket_id"]:
            set_ticket_status(conn, req["ticket_id"], "parts_issued", user, ip, force=True)
        notify(conn, "maintenance_technician", "Spare parts issued",
               f"Parts issued for {req['request_no']} - confirm receiving",
               "request", request_id, "info", f"/maintenance/requests")
        audit(conn, user, "parts_issue", "request", request_id, None, "issued", ip=ip)
        conn.commit()
        # Stock just dropped: raise auto-reorder PRs for the spares we issued
        # (targeted — not a full sweep; deduped; never blocks the issue).
        try:
            from app.maintenance.procure_bridge import auto_reorder_check
            auto_reorder_check(spare_ids=[it["spare_id"] for it in items])
        except Exception:
            pass
        return True, ""
    except Exception as exc:  # pragma: no cover - safety rollback
        conn.rollback()
        return False, f"error:{type(exc).__name__}"
    finally:
        conn.close()


def confirm_receiving(request_id, user, ip=None):
    conn = get_db()
    try:
        req = conn.execute("SELECT ticket_id,status FROM mnt_requests WHERE id=?",
                          (request_id,)).fetchone()
        if not req or req["status"] != "issued":
            return False, "not_issued"
        conn.execute("UPDATE mnt_requests SET status='received' WHERE id=?", (request_id,))
        conn.execute("UPDATE mnt_vouchers SET received_at=? WHERE request_id=?", (_now(), request_id))
        if req["ticket_id"]:
            conn.execute("UPDATE mnt_tickets SET received_at=? WHERE id=?", (_now(), req["ticket_id"]))
            set_ticket_status(conn, req["ticket_id"], "repair", user, ip, force=True)
        audit(conn, user, "parts_received", "request", request_id, None, "received", ip=ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --- repair / test / close -------------------------------------------------
def repair_proof(ticket_id, d, user, ip=None):
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return False, "bad_ticket_id"
    conn = get_db()
    try:
        now = _now()
        rate = wf.num(conn, "labour_rate_per_hour")
        # No-op UPDATE claims the row: a double submit (already in testing) charges labour once.
        if rate > 0 and conn.execute(
                "UPDATE mnt_tickets SET status=status WHERE id=? AND status='repair'",
                (ticket_id,)).rowcount == 1:
            t = conn.execute(
                "SELECT repair_started_at,diag_started_at,diag_done_at,reopened_at "
                "FROM mnt_tickets WHERE id=?", (ticket_id,)).fetchone()
            try:
                hours = float(d.get("labour_hours"))
            except (ValueError, TypeError):
                hours = 0
            if math.isfinite(hours) and hours > 0:
                hours = min(hours, 1000)
            else:
                minutes = _minutes_between(t["repair_started_at"], now) if t else 0
                minutes = minutes or 0
                if t:
                    reopened = _parse_dt(t["reopened_at"])
                    diag_start = _parse_dt(t["diag_started_at"])
                    diag_end = _parse_dt(t["diag_done_at"])
                    if diag_start and diag_end and (not t["reopened_at"] or
                            (reopened and diag_start >= reopened and diag_end >= reopened)):
                        minutes += _minutes_between(t["diag_started_at"], t["diag_done_at"]) or 0
                hours = minutes / 60
            conn.execute("UPDATE mnt_tickets SET cost=COALESCE(cost,0)+? WHERE id=?",
                         (round(hours * rate, 2), ticket_id))
            audit(conn, user, "labour_cost", "ticket", ticket_id, None, f"{hours:.2f}h x {rate}", ip=ip)
        conn.execute(
            "UPDATE mnt_tickets SET action_performed=?, old_part_returned=?, machine_running=?, "
            "final_notes=?, repair_done_at=? WHERE id=?",
            (d.get("action_performed"), 1 if d.get("old_part_returned") else 0,
             d.get("machine_running"), d.get("final_notes"), now, ticket_id))
        set_ticket_status(conn, ticket_id, "testing", user, ip, force=True)
        audit(conn, user, "repair_complete", "ticket", ticket_id, None, "testing", ip=ip)
        notify(conn, "production_supervisor", "Repair complete - testing required",
               f"{doc_no('MNT', ticket_id)} ready for test", "ticket", ticket_id, "info",
               f"/maintenance/tickets/{ticket_id}")
        conn.commit()
        return True, ""
    finally:
        conn.close()


def record_test(ticket_id, d, user, ip=None):
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return False, "bad_ticket_id"
    failed = ((d.get("test_result") or "").strip().lower() in ('fail', 'failed') or
              (d.get("safety_check") or "").strip().lower() in ('not ok', 'fail', 'failed'))
    reason = (d.get("reason") or "").strip()
    if failed and not reason:
        return False, "reason_required"
    conn = get_db()
    try:
        conn.execute("UPDATE mnt_tickets SET test_result=?, safety_check=?, machine_running=? WHERE id=?",
                     (d.get("test_result"), d.get("safety_check"), d.get("machine_running"), ticket_id))
        ok, msg = set_ticket_status(conn, ticket_id, "repair" if failed else "resolved",
                                    user, ip, force=not failed)
        if not ok:
            conn.rollback()
            return ok, msg
        link = f"/maintenance/tickets/{ticket_id}"
        if failed:
            ticket = conn.execute("SELECT ticket_no,assigned_to FROM mnt_tickets WHERE id=?", (ticket_id,)).fetchone()
            audit(conn, user, "test_failed", "ticket", ticket_id, comment=reason, ip=ip)
            notify(conn, "maintenance_technician", "Repair test failed",
                   f"{ticket['ticket_no']} failed its test: {reason}", "ticket", ticket_id, "warning", link,
                   exclude=(user or {}).get("username"))
            _notify_people(conn, _usernames_for(conn, [ticket["assigned_to"]]), "Repair test failed",
                           "%s failed its test: %s", link, args=(ticket["ticket_no"], reason), actor=user)
        else:
            audit(conn, user, "test_record", "ticket", ticket_id, None, "resolved", ip=ip)
            notify(conn, "maintenance_manager", "Ticket resolved - awaiting closure",
                   f"{doc_no('MNT', ticket_id)} resolved", "ticket", ticket_id, "info", link)
        conn.commit()
        _flush_notes(conn)
        return ok, msg
    finally:
        conn.close()


def close_ticket(ticket_id, user, ip=None):
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return False, "bad_ticket_id"
    conn = get_db()
    try:
        t = conn.execute("SELECT status,machine_id,created_at,cost,production_stopped,machine_rolled, "
                         "testing_done_at,repair_done_at,cost_rolled "
                         "FROM mnt_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not t:
            return False, "not_found"
        if t["status"] != "resolved":
            return False, "must_be_resolved"
        # Closing the work order cancels any approved-but-never-issued part
        # requests: their reservations would otherwise strand stock as
        # unavailable forever (the release used to happen only on reject).
        _release_ticket_reservations(conn, ticket_id)
        now = _now()
        end = t["testing_done_at"] or t["repair_done_at"] or now
        downtime = _minutes_between(t["created_at"], end)
        if downtime is None:
            downtime = _minutes_between(t["created_at"], now) or 0
        cost = round(t["cost"] or 0, 2)
        conn.execute("UPDATE mnt_tickets SET status='closed', closed_at=?, total_downtime_min=? WHERE id=?",
                     (now, downtime, ticket_id))
        _notify_people(conn, _requester_usernames(conn, ticket_id), "Ticket closed",
                       "%s has been closed", f"/maintenance/tickets/{ticket_id}",
                       args=(doc_no('MNT', ticket_id),), actor=user)
        if t["machine_id"] and not (t["machine_rolled"] or 0):
            # Only charge the machine's downtime meter when production was actually
            # stopped -- otherwise administrative waiting time (approvals, parts)
            # would inflate machine downtime for a fault that never halted the line.
            # machine_rolled guards the reopen->close-again path: without it every
            # re-close added the SAME ticket's cost, downtime and breakdown to the
            # machine a second time.
            machine_downtime = downtime if t["production_stopped"] else 0
            conn.execute(
                "UPDATE mnt_machines SET status='running', breakdowns=breakdowns+1, "
                "total_downtime_min=total_downtime_min+?, cost_to_date=cost_to_date+? WHERE id=?",
                (machine_downtime, cost, t["machine_id"]))
            conn.execute("UPDATE mnt_tickets SET machine_rolled=1, cost_rolled=? WHERE id=?",
                         (cost, ticket_id))
        elif t["machine_id"]:
            delta = round(cost - (t["cost_rolled"] if t["cost_rolled"] is not None else cost), 2)
            if delta != 0:
                conn.execute("UPDATE mnt_machines SET cost_to_date=cost_to_date+? WHERE id=?",
                             (delta, t["machine_id"]))
            conn.execute("UPDATE mnt_tickets SET cost_rolled=? WHERE id=?", (cost, ticket_id))
            conn.execute("UPDATE mnt_machines SET status='running' WHERE id=?", (t["machine_id"],))
        audit(conn, user, "ticket_close", "ticket", ticket_id, t["status"], "closed", ip=ip)
        conn.commit()
        _flush_notes(conn)
        from app.maintenance.mes_bridge import record_ticket_downtime
        record_ticket_downtime(ticket_id)
        return True, ""
    finally:
        conn.close()


def reopen_ticket(ticket_id, user, ip=None):
    if (ticket_id := _as_ticket_id(ticket_id)) is None:
        return False, "bad_ticket_id"
    conn = get_db()
    try:
        ok, msg = set_ticket_status(conn, ticket_id, "reopened", user, ip, force=True)
        if ok and (m := conn.execute("SELECT machine_id FROM mnt_tickets WHERE id=?",
                                      (ticket_id,)).fetchone())["machine_id"]:
            conn.execute("UPDATE mnt_machines SET status='under_maintenance' WHERE id=?",
                         (m["machine_id"],))
        audit(conn, user, "ticket_reopen", "ticket", ticket_id, None, "reopened", ip=ip)
        conn.commit()
        return ok, msg
    finally:
        conn.close()


def add_comment(ticket_id, user, body):
    conn = get_db()
    try:
        conn.execute("INSERT INTO mnt_comments (ticket_id,username,body,created_at) VALUES (?,?,?,?)",
                     (ticket_id, user.get("username"), body, _now()))
        conn.commit()
    finally:
        conn.close()


# --- stock operations ------------------------------------------------------
def adjust_stock(spare_id, new_qty, reason, user, ip=None):
    if not (reason or "").strip():
        return False, "reason_required"
    conn = get_db()
    try:
        sp = conn.execute("SELECT stock_qty FROM mnt_spare_parts WHERE id=?", (spare_id,)).fetchone()
        if not sp:
            return False, "not_found"
        delta = float(new_qty) - (sp["stock_qty"] or 0)
        ok, msg = _move_stock(conn, spare_id, "adjustment", delta, user, notes=reason)
        if not ok:
            conn.rollback()
            return False, msg
        audit(conn, user, "stock_adjust", "spare", spare_id, sp["stock_qty"], new_qty,
              comment=reason, ip=ip)
        conn.commit()
        try:
            from app.maintenance.procure_bridge import auto_reorder_check
            auto_reorder_check(spare_ids=[spare_id])
        except Exception:
            pass
        return True, ""
    finally:
        conn.close()


def sync_stock_alerts(conn=None):
    """Raise deduplicated low-stock / out-of-stock notifications (bell + role
    inbox); auto-resolve when a part is restocked. Safe to call on each load."""
    own = conn is None
    if own:
        conn = get_db()
    try:
        spares = conn.execute(
            "SELECT id,code,name,stock_qty,reorder_level,uom FROM mnt_spare_parts WHERE is_active=1"
        ).fetchall()
        alerts = {}
        for r in conn.execute(
                "SELECT id, title, entity_id FROM mnt_notifications WHERE entity_type='spare' "
                "AND is_read=0 AND (title LIKE 'Out of stock:%' OR title LIKE 'Low stock:%') "
                "ORDER BY id"):
            alerts.setdefault(r["entity_id"], r)
        for s in spares:
            sid = s["id"]
            stock = s["stock_qty"] or 0
            reorder = s["reorder_level"] or 0
            existing = alerts.get(sid)
            url = f"/maintenance/spares/{sid}"
            if stock <= 0:
                if not (existing and existing["title"].startswith("Out of stock:")):
                    if existing:  # upgrade an old "low" alert to "out"
                        conn.execute("UPDATE mnt_notifications SET is_read=1 WHERE id=?", (existing["id"],))
                    notify(conn, "storekeeper", f"Out of stock: {s['name']}",
                           f"{s['code']} is out of stock.", "spare", sid, "critical", url)
            elif stock <= reorder:
                if not existing:
                    notify(conn, "storekeeper", f"Low stock: {s['name']}",
                           f"{s['code']} at {stock} {s['uom']} (reorder level {reorder}).",
                           "spare", sid, "warning", url)
            else:
                if existing:  # restocked -> resolve
                    conn.execute("UPDATE mnt_notifications SET is_read=1 WHERE id=?", (existing["id"],))
        conn.commit()
        _flush_notes(conn)
        # Close the replenishment loop: anything at/below reorder gets a deduped
        # auto-PR to Procurement (also catches reservation-driven availability drops).
        try:
            from app.maintenance.procure_bridge import auto_reorder_check
            auto_reorder_check(conn=conn)
        except Exception:
            pass
    finally:
        if own:
            conn.close()


def sync_sla_breaches(conn=None):
    """Flag open tickets whose resolution SLA has elapsed. Sets sla_breach=1 and
    raises a one-time notification. Safe to call on each dashboard load -- the
    SLA fields existed but nothing ever evaluated them."""
    own = conn is None
    if own:
        conn = get_db()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        rows = conn.execute(
            "SELECT id, ticket_no, resolution_due, machine_code FROM mnt_tickets "
            "WHERE is_active=1 AND sla_breach=0 AND resolution_due IS NOT NULL "
            "AND status NOT IN ('draft','closed','cancelled','rejected','resolved')").fetchall()
        flagged = 0
        for t in rows:
            due = _parse_dt(t["resolution_due"])
            if due and now > due:
                conn.execute("UPDATE mnt_tickets SET sla_breach=1 WHERE id=?", (t["id"],))
                notify(conn, "maintenance_manager", "SLA breached",
                       f"{t['ticket_no']} on {t['machine_code'] or 'a machine'} passed its "
                       f"resolution SLA.", "ticket", t["id"], "critical",
                       f"/maintenance/tickets/{t['id']}")
                flagged += 1

        # DOAM §7.4.2 — an emergency purchase may proceed, but its justification
        # follows within 24 hours. emergency_deadline() writes that date; this is
        # what READS it. Deduped against the notification already raised, so it
        # stays safe on every dashboard load without a new column.
        from app.maintenance.eng_justification import (EMERGENCY_GRACE_HOURS,
                                                       emergency_overdue)
        for j in conn.execute(
                "SELECT id, ejr_no, is_emergency, status, emergency_due_at "
                "FROM mnt_eng_justifications WHERE is_active=1 AND is_emergency=1 "
                "AND status <> 'approved' AND emergency_due_at IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM mnt_notifications n "
                "WHERE n.entity_type='ejr' AND n.entity_id=mnt_eng_justifications.id)"
                ).fetchall():
            if emergency_overdue(j):
                notify(conn, "maintenance_manager", "Emergency justification overdue",
                       f"{j['ejr_no']} passed its {EMERGENCY_GRACE_HOURS}-hour documentation "
                       f"deadline and is still unsigned.", "ejr", j["id"], "critical",
                       f"/maintenance/justifications/{j['id']}")
                flagged += 1

        if flagged:
            conn.commit()
            _flush_notes(conn)
        return flagged
    finally:
        if own:
            conn.close()


def _health_from(status, breakdowns, open_c, crit_c, pm_overdue):
    """The scoring rule itself, with every input already supplied."""
    score = 100
    score -= {"stopped": 30, "waiting_spare": 22, "under_maintenance": 14,
              "under_testing": 6}.get(status, 0)
    score -= min((open_c or 0) * 8, 24)
    score -= min((crit_c or 0) * 12, 24)
    score -= min((breakdowns or 0) * 2, 20)
    score -= min((pm_overdue or 0) * 10, 20)
    score = max(0, min(100, score))
    return score, ("good" if score >= 75 else ("warn" if score >= 45 else "crit"))


def machine_health_bulk(conn, machines):
    """{machine_id: (score, band)} for MANY machines in TWO queries.

    machine_health() costs two queries per machine. That was invisible with a
    handful of machines and fatal with 5,108: the maintenance dashboard issued
    10,216 queries per page load. On local SQLite that is ~2 seconds; on Render's
    EXTERNAL PostgreSQL, where every query is a network round trip, it is many
    minutes — gunicorn kills the worker at 120s and Render serves 502. The page
    did not get slower, the fleet got bigger.
    """
    ids = [m["id"] for m in machines]
    if not ids:
        return {}
    tickets, overdue = {}, {}
    # Aggregate over the WHOLE table once; cheaper and simpler than an IN list
    # of 5,000 ids, and the row counts here are small.
    for r in conn.execute(
            "SELECT machine_id, COUNT(*) c, "
            "SUM(CASE WHEN priority='critical' THEN 1 ELSE 0 END) crit "
            "FROM mnt_tickets WHERE status NOT IN ('closed','cancelled','rejected') "
            "GROUP BY machine_id").fetchall():
        tickets[r["machine_id"]] = (r["c"] or 0, r["crit"] or 0)
    for r in conn.execute(
            "SELECT machine_id, COUNT(*) c FROM mnt_pm_plans "
            "WHERE active=1 AND next_due < date('now') GROUP BY machine_id").fetchall():
        overdue[r["machine_id"]] = r["c"] or 0
    out = {}
    for m in machines:
        c, crit = tickets.get(m["id"], (0, 0))
        out[m["id"]] = _health_from(m["status"], m["breakdowns"], c, crit,
                                    overdue.get(m["id"], 0))
    return out


def machine_health(conn, machine):
    """0-100 health score from live signals: status, open/critical tickets,
    breakdown history and PM overdue. Returns (score, band)."""
    score = 100
    status = machine["status"]
    score -= {"stopped": 30, "waiting_spare": 22, "under_maintenance": 14,
              "under_testing": 6}.get(status, 0)
    row = conn.execute(
        "SELECT COUNT(*) c, SUM(CASE WHEN priority='critical' THEN 1 ELSE 0 END) crit "
        "FROM mnt_tickets WHERE machine_id=? AND status NOT IN ('closed','cancelled','rejected')",
        (machine["id"],)).fetchone()
    score -= min((row["c"] or 0) * 8, 24)
    score -= min((row["crit"] or 0) * 12, 24)
    score -= min((machine["breakdowns"] or 0) * 2, 20)
    pm_overdue = conn.execute(
        "SELECT COUNT(*) c FROM mnt_pm_plans WHERE machine_id=? AND active=1 AND next_due < date('now')",
        (machine["id"],)).fetchone()["c"]
    score -= min(pm_overdue * 10, 20)
    score = max(0, min(100, score))
    band = "good" if score >= 75 else ("warn" if score >= 45 else "crit")
    return score, band


def save_attachment(file_storage, entity_type, entity_id, kind, user):
    """Securely store an uploaded photo/file and record its metadata.
    Returns the stored filename or None if rejected."""
    if not file_storage or not file_storage.filename:
        return None
    name = file_storage.filename
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in Config.ALLOWED_UPLOAD_EXT:
        return None
    safe = f"{uuid.uuid4().hex}.{ext}"
    dest_dir = Config.UPLOAD_DIR / "maintenance"
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / safe
    file_storage.save(str(path))
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO mnt_attachments (entity_type,entity_id,kind,filename,original_name,
               content_type,size,uploaded_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (entity_type, entity_id, kind, safe, secure_filename(name),
             file_storage.mimetype, size, user.get("username") if user else None, _now()))
        conn.commit()
    finally:
        conn.close()
    return safe


def save_attachments(files, entity_type, entity_id, kind, user, limit=5):
    saved = 0
    for f in (files or [])[:limit]:
        if save_attachment(f, entity_type, entity_id, kind, user):
            saved += 1
    return saved


def _notify_spare_arrived(spare_id, qty, skip_ticket=None):
    """Stock arrived for a spare: tell whoever is waiting on it. Never raises.

    Requests that could resume have already left out_of_stock before this runs."""
    try:
        conn = get_db()
        try:
            spare = conn.execute("SELECT code FROM mnt_spare_parts WHERE id=?",
                                 (spare_id,)).fetchone()
            tickets = conn.execute(
                "SELECT DISTINCT t.id, t.assigned_to FROM mnt_requests r "
                "JOIN mnt_request_items ri ON ri.request_id=r.id "
                "JOIN mnt_tickets t ON t.id=r.ticket_id "
                "WHERE ri.spare_id=? AND r.status='out_of_stock' "
                "AND t.status IN ('waiting_stock','spare_required')", (spare_id,)).fetchall()
            for t in tickets:
                if t["id"] == skip_ticket:
                    continue
                tid, title = t["id"], "Spare part arrived"
                message = (f"{spare['code']} received ({float(qty):g}) — ticket #{tid} is "
                           "waiting on it; raise or re-approve the spare request.")
                link = f"/maintenance/tickets/{tid}"
                for role in ("maintenance_technician", "maintenance_manager"):
                    notify(conn, role, title, message, "ticket", tid, "info", link)
                _notify_people(conn, _usernames_for(conn, [t["assigned_to"]]), title,
                               "%s received (%s) — ticket #%s is waiting on it; raise or re-approve the spare request.",
                               link, args=(spare["code"], f"{float(qty):g}", tid))
            conn.commit()
            _flush_notes(conn)
        except Exception:
            _PENDING_NOTES.pop(conn, None)   # rolled back: drop the queued notices too
            conn.rollback()     # never leave a shared PG connection aborted
            raise
        finally:
            conn.close()
    except Exception:
        log.warning("spare-arrived notification failed for spare %s", spare_id, exc_info=True)


def receive_stock(spare_id, qty, price, user, ip=None, skip_ticket=None):
    conn = get_db()
    try:
        try:
            qty = float(qty)
        except (TypeError, ValueError):
            return False, "bad_qty"
        if qty <= 0:
            # A receipt adds stock, full stop. abs() used to silently turn a
            # mistyped negative into a positive receive.
            return False, "bad_qty"
        # Coerce price safely: the manual receive form sends a raw string. A blank
        # means "no price update"; a literal "0" must NOT drag avg_cost to zero, and a
        # non-numeric entry must not 500. (The bridge already passes a real float.)
        try:
            price = float(price) if str(price).strip() not in ("", "None") else 0.0
        except (TypeError, ValueError):
            price = 0.0
        sp = conn.execute("SELECT stock_qty, avg_cost FROM mnt_spare_parts WHERE id=?",
                          (spare_id,)).fetchone()
        if not sp:
            return False, "not_found"
        before = sp["stock_qty"] or 0
        ok, msg = _move_stock(conn, spare_id, "purchase_receiving", qty, user,
                              notes="Stock receiving")
        if not ok:
            conn.rollback()
            return False, msg
        if price:
            # Moving weighted-average cost: (old_qty*old_avg + recv_qty*price) /
            # (old_qty+recv_qty). Previously only last_price moved, so avg_cost --
            # which drives cost-based approval routing and ticket costing -- was
            # frozen at the seed value forever.
            price = float(price)
            old_avg = sp["avg_cost"] or 0
            total = before + qty
            new_avg = ((before * old_avg) + (qty * price)) / total if total > 0 else price
            conn.execute("UPDATE mnt_spare_parts SET last_price=?, avg_cost=? WHERE id=?",
                         (price, round(new_avg, 4), spare_id))
        audit(conn, user, "stock_receive", "spare", spare_id, None, qty, ip=ip)
        conn.commit()
        _resume_waiting_requests(spare_id, user)
        _notify_spare_arrived(spare_id, qty, skip_ticket=skip_ticket)
        return True, ""
    finally:
        conn.close()
