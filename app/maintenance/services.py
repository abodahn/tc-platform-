"""
TC Platform — Maintenance service layer.

All workflow logic + transactions live here so routes stay thin. The critical
spare-part issue is fully transactional (rolls back on any failure) and stock
can never go negative. Status transitions are guarded.
"""
import uuid
from datetime import datetime, timezone

from werkzeug.utils import secure_filename

from config import Config
from app.db import get_db
from app.maintenance.constants import TICKET_TRANSITIONS

YEAR = None  # set lazily per call to avoid import-time clock reads in tooling


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _year():
    return datetime.now(timezone.utc).year


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
           severity="info", action_url=None):
    conn.execute(
        """INSERT INTO mnt_notifications (target_role,title,message,entity_type,
           entity_id,severity,action_url,is_read,created_at) VALUES (?,?,?,?,?,?,?,0,?)""",
        (target_role, title, message, entity_type, entity_id, severity, action_url, _now()))
    # also surface critical/warning items on the platform bell
    if severity in ("critical", "warning"):
        conn.execute(
            "INSERT INTO notifications (severity,module,title,message,created_at) "
            "VALUES (?,?,?,?,?)", (severity, "maintenance", title, message, _now()))


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


def set_ticket_status(conn, ticket_id, target, user, ip=None, force=False, comment=None):
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
def _move_stock(conn, spare_id, mtype, qty, user, ticket_id=None, request_id=None,
                machine_id=None, notes=None, approved_by=None):
    """Apply a signed stock delta and record a movement. Returns (ok, msg)."""
    sp = conn.execute("SELECT stock_qty FROM mnt_spare_parts WHERE id=?", (spare_id,)).fetchone()
    if not sp:
        return False, "spare_not_found"
    before = sp["stock_qty"] or 0
    after = before + qty
    if after < 0:
        return False, "stock_would_go_negative"
    conn.execute("UPDATE mnt_spare_parts SET stock_qty=? WHERE id=?", (after, spare_id))
    n = conn.execute("SELECT COUNT(*) c FROM mnt_stock_movements").fetchone()["c"] + 1
    conn.execute(
        """INSERT INTO mnt_stock_movements (movement_no,type,spare_id,qty,before_qty,
           after_qty,ticket_id,request_id,machine_id,performed_by,approved_by,notes,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (doc_no("STK", n), mtype, spare_id, qty, before, after, ticket_id, request_id,
         machine_id, user.get("username") if user else "system", approved_by, notes, _now()))
    return True, ""


# --- ticket lifecycle ------------------------------------------------------
def create_ticket(data, user, ip=None, submit=True):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO mnt_tickets
               (requester,requester_user_id,department,area,line_no,machine_id,machine_code,
                issue_category,description,priority,severity,safety_impact,production_stopped,
                est_downtime_min,shift,remarks,status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data.get("requester") or (user.get("full_name") or user.get("username")),
             user.get("id"), data.get("department"), data.get("area"), data.get("line_no"),
             data.get("machine_id") or None, data.get("machine_code"),
             data.get("issue_category"), data.get("description"),
             data.get("priority", "medium"), data.get("severity", "moderate"),
             1 if data.get("safety_impact") else 0,
             1 if data.get("production_stopped") else 0,
             int(data.get("est_downtime_min") or 0), data.get("shift"),
             data.get("remarks"), "submitted" if submit else "draft", _now()))
        tid = cur.lastrowid
        conn.execute("UPDATE mnt_tickets SET ticket_no=? WHERE id=?", (doc_no("MNT", tid), tid))
        # if machine reported as production-stopped, reflect machine status
        if data.get("machine_id") and data.get("production_stopped"):
            conn.execute("UPDATE mnt_machines SET status='stopped' WHERE id=?",
                         (data.get("machine_id"),))
        audit(conn, user, "ticket_create", "ticket", tid, None, doc_no("MNT", tid), ip=ip)
        if submit:
            notify(conn, "maintenance_manager", "New maintenance ticket",
                   f"{doc_no('MNT', tid)} on {data.get('machine_code') or 'a machine'}",
                   "ticket", tid, "warning", f"/maintenance/tickets/{tid}")
        conn.commit()
        return tid
    finally:
        conn.close()


def review_assign(ticket_id, technician, priority, response_due, resolution_due, user, ip=None):
    conn = get_db()
    try:
        ok, msg = set_ticket_status(conn, ticket_id, "assigned", user, ip, force=True)
        wo = doc_no("WO", ticket_id)
        conn.execute(
            "UPDATE mnt_tickets SET assigned_to=?, work_order_no=?, priority=COALESCE(?,priority),"
            " response_due=?, resolution_due=?, reviewed_at=COALESCE(reviewed_at,?) WHERE id=?",
            (technician, wo, priority or None, response_due, resolution_due, _now(), ticket_id))
        audit(conn, user, "ticket_assign", "ticket", ticket_id, None, technician, ip=ip)
        notify(conn, "maintenance_technician", "Work order assigned",
               f"{doc_no('MNT', ticket_id)} assigned to you", "ticket", ticket_id,
               "info", f"/maintenance/tickets/{ticket_id}")
        conn.commit()
        return True, ""
    finally:
        conn.close()


def reject_ticket(ticket_id, reason, user, ip=None):
    if not (reason or "").strip():
        return False, "reason_required"
    conn = get_db()
    try:
        conn.execute("UPDATE mnt_tickets SET rejection_reason=? WHERE id=?", (reason, ticket_id))
        ok, msg = set_ticket_status(conn, ticket_id, "rejected", user, ip, force=True, comment=reason)
        audit(conn, user, "ticket_reject", "ticket", ticket_id, None, reason, comment=reason, ip=ip)
        conn.commit()
        return ok, msg
    finally:
        conn.close()


def add_diagnosis(ticket_id, d, user, ip=None):
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
    rule = None
    if critical or cost >= 100:
        rule = conn.execute("SELECT levels FROM mnt_approval_matrix WHERE active=1 AND "
                            "part_criticality='critical' LIMIT 1").fetchone()
    if not rule:
        rule = conn.execute("SELECT levels FROM mnt_approval_matrix WHERE active=1 "
                            "ORDER BY id LIMIT 1").fetchone()
    levels = (rule["levels"] if rule else "maintenance_manager,storekeeper").split(",")
    # storekeeper is the issuer, not an approval vote
    return [r.strip() for r in levels if r.strip() and r.strip() != "storekeeper"]


def create_request(ticket_id, items, reason, urgency, user, ip=None):
    """items: list of dicts {spare_id, qty, notes}."""
    if not items:
        return None, "no_items"
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

        any_out = False
        for it in items:
            sp = conn.execute("SELECT code,name,uom,stock_qty FROM mnt_spare_parts WHERE id=?",
                             (it["spare_id"],)).fetchone()
            if not sp:
                continue
            avail = sp["stock_qty"] or 0
            if it["qty"] > avail:
                any_out = True
            conn.execute(
                """INSERT INTO mnt_request_items (request_id,spare_id,part_code,part_name,
                   qty_requested,uom,available_at_request,notes) VALUES (?,?,?,?,?,?,?,?)""",
                (rid, it["spare_id"], sp["code"], sp["name"], it["qty"], sp["uom"],
                 avail, it.get("notes")))

        conn.execute("UPDATE mnt_tickets SET parts_req_at=? WHERE id=?", (_now(), ticket_id))

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
            notify(conn, first_role, "Spare request awaiting approval",
                   f"{doc_no('SPR', rid)} needs your approval", "request", rid,
                   "warning", f"/maintenance/approvals")
        audit(conn, user, "request_create", "request", rid, None, doc_no("SPR", rid), ip=ip)
        conn.commit()
        return rid, ""
    finally:
        conn.close()


def decide_approval(approval_id, decision, comment, user, ip=None):
    conn = get_db()
    try:
        ap = conn.execute("SELECT * FROM mnt_approvals WHERE id=?", (approval_id,)).fetchone()
        if not ap:
            return False, "not_found"
        rid = ap["request_id"]
        if decision == "reject":
            if not (comment or "").strip():
                return False, "reason_required"
            conn.execute("UPDATE mnt_approvals SET status='rejected',comment=?,decided_at=?,"
                         "approver_user=? WHERE id=?",
                         (comment, _now(), user.get("username"), approval_id))
            conn.execute("UPDATE mnt_requests SET status='rejected',decided_at=? WHERE id=?",
                         (_now(), rid))
            req = conn.execute("SELECT ticket_id FROM mnt_requests WHERE id=?", (rid,)).fetchone()
            if req:
                set_ticket_status(conn, req["ticket_id"], "spare_required", user, ip, force=True,
                                  comment=comment)
            notify(conn, "maintenance_technician", "Spare request rejected",
                   f"Request {rid}: {comment}", "request", rid, "warning")
            audit(conn, user, "approval_reject", "request", rid, None, "rejected",
                  comment=comment, ip=ip)
            conn.commit()
            return True, ""

        if decision == "return":
            conn.execute("UPDATE mnt_approvals SET status='returned',comment=?,decided_at=?,"
                         "approver_user=? WHERE id=?",
                         (comment, _now(), user.get("username"), approval_id))
            conn.execute("UPDATE mnt_requests SET status='returned' WHERE id=?", (rid,))
            notify(conn, "maintenance_technician", "Spare request returned",
                   f"Request {rid} returned for clarification", "request", rid, "info")
            audit(conn, user, "approval_return", "request", rid, None, "returned",
                  comment=comment, ip=ip)
            conn.commit()
            return True, ""

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
            conn.execute("UPDATE mnt_requests SET status='approved',decided_at=? WHERE id=?",
                         (_now(), rid))
            req = conn.execute("SELECT ticket_id FROM mnt_requests WHERE id=?", (rid,)).fetchone()
            if req:
                set_ticket_status(conn, req["ticket_id"], "approved_issue", user, ip, force=True)
            notify(conn, "storekeeper", "Spare request approved - ready to issue",
                   f"Request {rid} approved", "request", rid, "warning",
                   "/maintenance/requests")
        audit(conn, user, "approval_approve", "request", rid, None, "approved", comment=comment, ip=ip)
        conn.commit()
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
        if req["status"] != "approved":
            return False, "not_approved"
        items = conn.execute("SELECT * FROM mnt_request_items WHERE request_id=?",
                            (request_id,)).fetchall()
        ticket = conn.execute("SELECT id,machine_code FROM mnt_tickets WHERE id=?",
                             (req["ticket_id"],)).fetchone()
        # validate all first (fail fast, no partial writes)
        for it in items:
            qty = it["qty_approved"] or it["qty_requested"]
            if qty <= 0:
                return False, "invalid_qty"
            sp = conn.execute("SELECT stock_qty FROM mnt_spare_parts WHERE id=?",
                             (it["spare_id"],)).fetchone()
            if not sp or (sp["stock_qty"] or 0) < qty:
                return False, "insufficient_stock"
        # apply
        for it in items:
            qty = it["qty_approved"] or it["qty_requested"]
            ok, msg = _move_stock(conn, it["spare_id"], "issue", -qty, user,
                                  ticket_id=req["ticket_id"], request_id=request_id,
                                  machine_id=req["machine_id"],
                                  notes=f"Issued for {req['request_no']}",
                                  approved_by=user.get("username"))
            if not ok:
                conn.rollback()
                return False, msg
            conn.execute("UPDATE mnt_request_items SET qty_issued=? WHERE id=?", (qty, it["id"]))
            n = conn.execute("SELECT COUNT(*) c FROM mnt_vouchers").fetchone()["c"] + 1
            conn.execute(
                """INSERT INTO mnt_vouchers (voucher_no,request_id,ticket_id,spare_id,part_code,
                   part_name,qty_issued,issued_by,received_by,machine_code,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (doc_no("ISS", n), request_id, req["ticket_id"], it["spare_id"],
                 it["part_code"], it["part_name"], qty, user.get("username"),
                 received_by, ticket["machine_code"] if ticket else None, _now()))
        conn.execute("UPDATE mnt_requests SET status='issued',decided_at=? WHERE id=?",
                     (_now(), request_id))
        if req["ticket_id"]:
            set_ticket_status(conn, req["ticket_id"], "parts_issued", user, ip, force=True)
        notify(conn, "maintenance_technician", "Spare parts issued",
               f"Parts issued for {req['request_no']} - confirm receiving",
               "request", request_id, "info", f"/maintenance/requests")
        audit(conn, user, "parts_issue", "request", request_id, None, "issued", ip=ip)
        conn.commit()
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
    conn = get_db()
    try:
        conn.execute(
            "UPDATE mnt_tickets SET action_performed=?, old_part_returned=?, machine_running=?, "
            "final_notes=?, repair_done_at=? WHERE id=?",
            (d.get("action_performed"), 1 if d.get("old_part_returned") else 0,
             d.get("machine_running"), d.get("final_notes"), _now(), ticket_id))
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
    conn = get_db()
    try:
        conn.execute("UPDATE mnt_tickets SET test_result=?, safety_check=?, machine_running=? WHERE id=?",
                     (d.get("test_result"), d.get("safety_check"),
                      d.get("machine_running"), ticket_id))
        set_ticket_status(conn, ticket_id, "resolved", user, ip, force=True)
        audit(conn, user, "test_record", "ticket", ticket_id, None, "resolved", ip=ip)
        notify(conn, "maintenance_manager", "Ticket resolved - awaiting closure",
               f"{doc_no('MNT', ticket_id)} resolved", "ticket", ticket_id, "info",
               f"/maintenance/tickets/{ticket_id}")
        conn.commit()
        return True, ""
    finally:
        conn.close()


def close_ticket(ticket_id, user, ip=None):
    conn = get_db()
    try:
        t = conn.execute("SELECT status,machine_id,created_at,cost FROM mnt_tickets WHERE id=?",
                        (ticket_id,)).fetchone()
        if not t:
            return False, "not_found"
        if t["status"] != "resolved":
            return False, "must_be_resolved"
        # compute downtime from creation to now (minutes)
        try:
            created = datetime.strptime(t["created_at"], "%Y-%m-%d %H:%M:%S")
            downtime = int((datetime.now(timezone.utc).replace(tzinfo=None) - created).total_seconds() // 60)
        except Exception:
            downtime = 0
        conn.execute("UPDATE mnt_tickets SET status='closed', closed_at=?, total_downtime_min=? WHERE id=?",
                     (_now(), max(downtime, 0), ticket_id))
        if t["machine_id"]:
            conn.execute(
                "UPDATE mnt_machines SET status='running', breakdowns=breakdowns+1, "
                "total_downtime_min=total_downtime_min+?, cost_to_date=cost_to_date+?, "
                "last_pm_date=last_pm_date WHERE id=?",
                (max(downtime, 0), t["cost"] or 0, t["machine_id"]))
        audit(conn, user, "ticket_close", "ticket", ticket_id, t["status"], "closed", ip=ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def reopen_ticket(ticket_id, user, ip=None):
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
        for s in spares:
            sid = s["id"]
            stock = s["stock_qty"] or 0
            reorder = s["reorder_level"] or 0
            existing = conn.execute(
                "SELECT id, title FROM mnt_notifications WHERE entity_type='spare' AND entity_id=? "
                "AND is_read=0 AND (title LIKE 'Out of stock:%' OR title LIKE 'Low stock:%')",
                (sid,)).fetchone()
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
    finally:
        if own:
            conn.close()


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


def receive_stock(spare_id, qty, price, user, ip=None):
    conn = get_db()
    try:
        ok, msg = _move_stock(conn, spare_id, "purchase_receiving", abs(float(qty)), user,
                              notes="Stock receiving")
        if not ok:
            conn.rollback()
            return False, msg
        if price:
            conn.execute("UPDATE mnt_spare_parts SET last_price=? WHERE id=?", (price, spare_id))
        audit(conn, user, "stock_receive", "spare", spare_id, None, qty, ip=ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()
