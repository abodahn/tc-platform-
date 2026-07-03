"""
TC Platform — Procurement & Approvals service layer.

All workflow logic lives here so routes stay thin: creating a PR, building the
threshold-driven approval ladder, stamping an approver's digital signature,
advancing/rejecting, and auto-generating the Purchase Order on final approval.
Each mutating action writes an immutable audit event and surfaces the right
platform-bell notification.
"""
from datetime import datetime, timezone

from app.db import get_db
from app.security import has_permission
from app.approvals.constants import (
    build_ladder, stage_label, STAGE_ROLES, PR_STATUSES)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _year():
    return datetime.now(timezone.utc).year


def doc_no(prefix, n):
    return f"{prefix}-{_year()}-{n:06d}"


# --------------------------------------------------------------------------
# audit + notifications
# --------------------------------------------------------------------------
def audit(conn, pr_id, actor, action, detail="", ip=None):
    conn.execute(
        "INSERT INTO pr_events (pr_id, actor, action, detail, ip, created_at) "
        "VALUES (?,?,?,?,?,?)", (pr_id, actor, action, detail, ip, _now()))


def bell(conn, severity, title, message):
    """Surface an event on the platform notifications bell (module=procurement)."""
    conn.execute(
        "INSERT INTO notifications (severity, module, title, message, created_at) "
        "VALUES (?,?,?,?,?)", (severity, "procurement", title, message, _now()))


# --------------------------------------------------------------------------
# authority
# --------------------------------------------------------------------------
def can_act(user, stage):
    """May this user approve/reject the given ladder stage?"""
    if not user:
        return False
    role = user.get("role")
    if role == "super_admin" or has_permission(role, "proc_admin"):
        return True
    if not has_permission(role, "proc_approve"):
        return False
    return role in STAGE_ROLES.get(stage, set())


def _user_sig(username):
    """Return (sig_png, sig_name) for a user, or (None, None)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT sig_png, sig_name, full_name FROM users WHERE username=?",
            (username,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None, None
    return row["sig_png"], (row["sig_name"] or row["full_name"] or username)


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------
def _amount(item):
    """Line total: prefer est_cost, else qty * unit_price."""
    est = item.get("est_cost")
    if est not in (None, "", 0, "0"):
        try:
            return float(est)
        except (TypeError, ValueError):
            pass
    try:
        return float(item.get("qty") or 0) * float(item.get("unit_price") or 0)
    except (TypeError, ValueError):
        return 0.0


def get_pr(pr_id):
    """Return the full PR bundle: header, items, steps, events, attachments meta."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return None
        items = conn.execute(
            "SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()
        steps = conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()
        events = conn.execute(
            "SELECT * FROM pr_events WHERE pr_id=? ORDER BY id DESC", (pr_id,)).fetchall()
        atts = conn.execute(
            "SELECT id, filename, content_type, size, uploaded_by, created_at "
            "FROM pr_attachments WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()
        return {"pr": dict(pr), "items": [dict(r) for r in items],
                "steps": [dict(r) for r in steps], "events": [dict(r) for r in events],
                "attachments": [dict(r) for r in atts]}
    finally:
        conn.close()


def list_prs(status=None, requester=None, limit=500):
    conn = get_db()
    try:
        sql = "SELECT * FROM pr_requests WHERE is_active=1"
        args = []
        if status and status != "all":
            sql += " AND status=?"
            args.append(status)
        if requester:
            sql += " AND requester=?"
            args.append(requester)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def my_queue(user):
    """PRs currently waiting on a stage this user is allowed to act on."""
    if not user:
        return []
    out = []
    for pr in list_prs(status="pending"):
        conn = get_db()
        try:
            step = conn.execute(
                "SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
                (pr["id"], pr["current_seq"])).fetchone()
        finally:
            conn.close()
        if step and can_act(user, step["stage"]):
            pr = dict(pr)
            pr["_stage"] = step["stage"]
            pr["_stage_label"] = stage_label(step["stage"])
            out.append(pr)
    return out


def counts(user=None):
    """Small KPI bundle for the module landing page."""
    conn = get_db()
    try:
        def c(sql, a=()):
            return conn.execute(sql, a).fetchone()["c"]
        data = {
            "total": c("SELECT COUNT(*) c FROM pr_requests WHERE is_active=1"),
            "pending": c("SELECT COUNT(*) c FROM pr_requests WHERE status='pending'"),
            "approved": c("SELECT COUNT(*) c FROM pr_requests WHERE status IN ('approved','po_issued','closed')"),
            "rejected": c("SELECT COUNT(*) c FROM pr_requests WHERE status='rejected'"),
            "value_pending": c("SELECT COALESCE(SUM(total),0) c FROM pr_requests WHERE status='pending'"),
        }
    finally:
        conn.close()
    data["my_queue"] = len(my_queue(user)) if user else 0
    return data


# --------------------------------------------------------------------------
# create / submit
# --------------------------------------------------------------------------
def create_pr(header, items, user, ip=None, submit=True):
    """Create a PR (+items). When submit=True, build the ladder and route it.
    Returns (pr_id, pr_no)."""
    conn = get_db()
    try:
        total = round(sum(_amount(it) for it in items), 2)
        now = _now()
        uname = user.get("username") if user else "system"
        cur = conn.execute(
            """INSERT INTO pr_requests
               (pr_no, title, request_for, requester, requester_name, requester_user_id,
                department, request_date, currency, vendor, payment_condition,
                delivery_condition, req_del_date, asset_code, total, status,
                current_seq, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (None, header.get("title"), header.get("request_for"), uname,
             user.get("full_name") or uname if user else uname,
             user.get("id") if user else None,
             header.get("department"), header.get("request_date") or now[:10],
             header.get("currency") or "EGP", header.get("vendor"),
             header.get("payment_condition"), header.get("delivery_condition"),
             header.get("req_del_date"), header.get("asset_code"),
             total, "draft", 0, header.get("notes"), now))
        pr_id = cur.lastrowid
        pr_no = doc_no("PR", pr_id)
        conn.execute("UPDATE pr_requests SET pr_no=? WHERE id=?", (pr_no, pr_id))
        for i, it in enumerate(items, start=1):
            conn.execute(
                """INSERT INTO pr_items
                   (pr_id, seq, item, description, unit, qty, current_stock, vendor,
                    unit_price, est_cost, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (pr_id, i, it.get("item"), it.get("description"), it.get("unit") or "Pcs",
                 float(it.get("qty") or 0), float(it.get("current_stock") or 0),
                 it.get("vendor") or header.get("vendor"),
                 float(it.get("unit_price") or 0), round(_amount(it), 2), it.get("notes")))
        audit(conn, pr_id, uname, "created", f"PR {pr_no} created (total {total})", ip)
        conn.commit()
    finally:
        conn.close()
    if submit:
        submit_pr(pr_id, user, ip)
    return pr_id, pr_no


def submit_pr(pr_id, user, ip=None):
    """Build the approval ladder and move the PR into 'pending'. Idempotent-ish:
    only acts on draft/rejected PRs. Returns (ok, msg)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("draft", "rejected"):
            return False, "not_submittable"
        # clear any prior steps (resubmit after rejection)
        conn.execute("DELETE FROM pr_steps WHERE pr_id=?", (pr_id,))
        ladder = build_ladder(pr["total"])
        now = _now()
        for i, stage in enumerate(ladder, start=1):
            conn.execute(
                """INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, created_at)
                   VALUES (?,?,?,?,?,?)""",
                (pr_id, i, stage, "pending", stage_label(stage), now))
        conn.execute(
            "UPDATE pr_requests SET status='pending', current_seq=1, submitted_at=?, "
            "rejection_reason=NULL WHERE id=?", (now, pr_id))
        uname = user.get("username") if user else "system"
        audit(conn, pr_id, uname, "submitted",
              f"Routed through {len(ladder)} approvals: "
              f"{' -> '.join(stage_label(s) for s in ladder)}", ip)
        bell(conn, "info", "New purchase request",
             f"{pr['pr_no']} ({pr['title'] or ''}) awaits {stage_label(ladder[0])} approval.")
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# approve / reject
# --------------------------------------------------------------------------
def act_on_step(pr_id, user, decision, comment=None, ip=None):
    """Approve or reject the PR's current pending step.
    decision in {'approve','reject'}. Returns (ok, msg)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] != "pending":
            return False, "not_pending"
        step = conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
            (pr_id, pr["current_seq"])).fetchone()
        if not step:
            return False, "no_active_step"
        if not can_act(user, step["stage"]):
            return False, "forbidden"

        uname = user.get("username")
        sig_png, sig_name = _user_sig(uname)
        now = _now()

        if decision == "reject":
            conn.execute(
                "UPDATE pr_steps SET status='rejected', approver_user=?, approver_name=?, "
                "approver_role=?, comment=?, sig_png=?, acted_at=? WHERE id=?",
                (uname, sig_name or user.get("full_name") or uname, user.get("role"),
                 comment, sig_png, now, step["id"]))
            conn.execute(
                "UPDATE pr_requests SET status='rejected', rejection_reason=? WHERE id=?",
                (comment or f"Rejected at {stage_label(step['stage'])}", pr_id))
            audit(conn, pr_id, uname, "rejected",
                  f"{stage_label(step['stage'])} rejected: {comment or ''}", ip)
            bell(conn, "warning", "Purchase request rejected",
                 f"{pr['pr_no']} rejected at {stage_label(step['stage'])}.")
            conn.commit()
            return True, "rejected"

        # approve
        conn.execute(
            "UPDATE pr_steps SET status='approved', approver_user=?, approver_name=?, "
            "approver_role=?, comment=?, sig_png=?, acted_at=? WHERE id=?",
            (uname, sig_name or user.get("full_name") or uname, user.get("role"),
             comment, sig_png, now, step["id"]))
        audit(conn, pr_id, uname, "approved",
              f"{stage_label(step['stage'])} approved" + (f": {comment}" if comment else ""), ip)

        nxt = conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? AND seq>? ORDER BY seq LIMIT 1",
            (pr_id, step["seq"])).fetchone()
        if nxt:
            conn.execute("UPDATE pr_requests SET current_seq=? WHERE id=?",
                         (nxt["seq"], pr_id))
            bell(conn, "info", "Approval advanced",
                 f"{pr['pr_no']} now awaits {stage_label(nxt['stage'])} approval.")
            conn.commit()
            return True, "advanced"

        # last step approved -> PR approved, auto-generate a PO number
        po_no = doc_no("PO", pr_id)
        conn.execute(
            "UPDATE pr_requests SET status='approved', current_seq=0, approved_at=?, "
            "po_no=? WHERE id=?", (now, po_no, pr_id))
        audit(conn, pr_id, uname, "fully_approved",
              f"All approvals complete. PO {po_no} drafted.", ip)
        bell(conn, "info", "Purchase request approved",
             f"{pr['pr_no']} fully approved — PO {po_no} ready to issue.")
        conn.commit()
        return True, "approved"
    finally:
        conn.close()


def issue_po(pr_id, user, ip=None):
    """Mark an approved PR's PO as issued (purchasing action)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("approved",):
            return False, "not_approved"
        po_no = pr["po_no"] or doc_no("PO", pr_id)
        conn.execute("UPDATE pr_requests SET status='po_issued', po_no=? WHERE id=?",
                     (po_no, pr_id))
        audit(conn, pr_id, user.get("username"), "po_issued", f"PO {po_no} issued", ip)
        bell(conn, "info", "Purchase Order issued", f"{po_no} issued for {pr['pr_no']}.")
        conn.commit()
        return True, po_no
    finally:
        conn.close()


def cancel_pr(pr_id, user, ip=None):
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("closed", "cancelled"):
            return False, "already_closed"
        conn.execute("UPDATE pr_requests SET status='cancelled', current_seq=0 WHERE id=?",
                     (pr_id,))
        audit(conn, pr_id, user.get("username"), "cancelled", "Request cancelled", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# vendors
# --------------------------------------------------------------------------
def list_vendors(active_only=True):
    conn = get_db()
    try:
        sql = "SELECT * FROM proc_vendors"
        if active_only:
            sql += " WHERE is_active=1"
        sql += " ORDER BY name"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def create_vendor(data, user, ip=None):
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO proc_vendors
               (name, contact_person, phone, email, address, payment_terms, category,
                rating, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (data.get("name"), data.get("contact_person"), data.get("phone"),
             data.get("email"), data.get("address"), data.get("payment_terms"),
             data.get("category"), float(data.get("rating") or 0), data.get("notes"), _now()))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# attachments (vendor quotations)
# --------------------------------------------------------------------------
def add_attachment(pr_id, filename, content_type, content_b64, size, user, ip=None):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO pr_attachments
               (pr_id, filename, content_type, size, content_b64, uploaded_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (pr_id, filename, content_type, size, content_b64,
             user.get("username") if user else "system", _now()))
        audit(conn, pr_id, user.get("username") if user else "system",
              "attachment", f"Attached {filename}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def get_attachment(att_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM pr_attachments WHERE id=?", (att_id,)).fetchone()
    finally:
        conn.close()
