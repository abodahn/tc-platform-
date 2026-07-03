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
from app.approvals import constants as C
from app.approvals.constants import (
    build_ladder, stage_label, STAGE_ROLES, PR_STATUSES)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _year():
    return datetime.now(timezone.utc).year


def _age_hours(ts):
    """Hours elapsed since an ISO 'YYYY-MM-DD HH:MM:SS' timestamp (UTC), or None."""
    if not ts:
        return None
    try:
        t = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 3600.0)


def doc_no(prefix, n):
    return f"{prefix}-{_year()}-{n:06d}"


# --------------------------------------------------------------------------
# audit + notifications
# --------------------------------------------------------------------------
def audit(conn, pr_id, actor, action, detail="", ip=None):
    conn.execute(
        "INSERT INTO pr_events (pr_id, actor, action, detail, ip, created_at) "
        "VALUES (?,?,?,?,?,?)", (pr_id, actor, action, detail, ip, _now()))


def bell(conn, severity, title, message, link=None):
    """Broadcast an event on the platform notifications bell (module=procurement)."""
    conn.execute(
        "INSERT INTO notifications (severity, module, title, message, link, created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "procurement", title, message, link, _now()))


def notify_users(conn, usernames, severity, title, message, link=None):
    """Send a per-user bell notification to each username (deduped, skips blanks)."""
    for u in {u for u in usernames if u}:
        conn.execute(
            "INSERT INTO notifications (severity, module, title, message, target_user, link, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (severity, "procurement", title, message, u, link, _now()))


def eligible_approvers(conn, stage):
    """Usernames allowed to act on a stage: everyone holding a qualifying role,
    plus anyone with an active delegation from such a person."""
    roles = STAGE_ROLES.get(stage, set())
    if not roles:
        return []
    ph = ",".join(["?"] * len(roles))
    out = {r["username"] for r in conn.execute(
        f"SELECT username FROM users WHERE is_active=1 AND role IN ({ph})",
        tuple(roles)).fetchall()}
    today = _now()[:10]
    sql = (f"SELECT d.to_user u FROM proc_delegations d JOIN users usr ON usr.username=d.from_user "
           f"WHERE d.is_active=1 AND usr.role IN ({ph}) "
           f"AND (d.from_date IS NULL OR d.from_date<=?) AND (d.to_date IS NULL OR d.to_date>=?)")
    for r in conn.execute(sql, tuple(roles) + (today, today)).fetchall():
        out.add(r["u"])
    return list(out)


def _pr_link(pr_id):
    return f"/procurement/pr/{pr_id}"


# --------------------------------------------------------------------------
# authority
# --------------------------------------------------------------------------
def active_delegator_roles(username):
    """Roles the given user has been delegated (active window) by others."""
    if not username:
        return set()
    conn = get_db()
    try:
        today = _now()[:10]
        rows = conn.execute(
            """SELECT d.from_user, u.role FROM proc_delegations d
               JOIN users u ON u.username = d.from_user
               WHERE d.to_user=? AND d.is_active=1
                 AND (d.from_date IS NULL OR d.from_date<=?)
                 AND (d.to_date IS NULL OR d.to_date>=?)""",
            (username, today, today)).fetchall()
        return {r["role"] for r in rows if r["role"]}
    finally:
        conn.close()


def can_act(user, stage):
    """May this user approve/reject the given ladder stage? True for the stage's
    own roles, for super_admin / proc_admin, or for anyone actively delegated a
    qualifying role by another user."""
    if not user:
        return False
    role = user.get("role")
    if role == "super_admin" or has_permission(role, "proc_admin"):
        return True
    if not has_permission(role, "proc_approve"):
        return False
    allowed = STAGE_ROLES.get(stage, set())
    if role in allowed:
        return True
    return bool(allowed & active_delegator_roles(user.get("username")))


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
        quotes = conn.execute(
            "SELECT id, pr_id, vendor, amount, currency, lead_time_days, warranty, "
            "filename, is_chosen, notes, created_at FROM pr_quotes WHERE pr_id=? "
            "ORDER BY amount", (pr_id,)).fetchall()
    finally:
        conn.close()
    pr_d = dict(pr)
    step_ds = []
    for r in steps:
        s = dict(r)
        # aging only meaningful for the current pending step
        if s.get("status") == "pending" and s.get("seq") == pr_d.get("current_seq") \
           and pr_d.get("status") == "pending":
            hrs = _age_hours(s.get("activated_at") or pr_d.get("submitted_at"))
            if hrs is not None:
                s["age_hours"] = round(hrs, 1)
                s["overdue"] = hrs > C.SLA_HOURS_PER_STAGE
                s["due_soon"] = (not s["overdue"]) and hrs > C.SLA_WARN_HOURS
        step_ds.append(s)
    return {"pr": pr_d, "items": [dict(r) for r in items], "steps": step_ds,
            "events": [dict(r) for r in events], "attachments": [dict(r) for r in atts],
            "quotes": [dict(r) for r in quotes]}


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
                """INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, activated_at, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (pr_id, i, stage, "pending", stage_label(stage), now if i == 1 else None, now))
        conn.execute(
            "UPDATE pr_requests SET status='pending', current_seq=1, submitted_at=?, "
            "rejection_reason=NULL WHERE id=?", (now, pr_id))
        uname = user.get("username") if user else "system"
        audit(conn, pr_id, uname, "submitted",
              f"Routed through {len(ladder)} approvals: "
              f"{' -> '.join(stage_label(s) for s in ladder)}", ip)
        # Notify the first-stage approvers directly that a request needs signing.
        notify_users(conn, eligible_approvers(conn, ladder[0]), "warning",
                     "New request to sign",
                     f"{pr['pr_no']} ({pr['title'] or ''}) needs your {stage_label(ladder[0])} approval.",
                     link=_pr_link(pr_id))
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
            notify_users(conn, [pr["requester"]], "warning", "Request rejected",
                         f"{pr['pr_no']} was rejected at {stage_label(step['stage'])}"
                         + (f": {comment}" if comment else "."), link=_pr_link(pr_id))
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
            conn.execute("UPDATE pr_steps SET activated_at=? WHERE id=?", (now, nxt["id"]))
            notify_users(conn, eligible_approvers(conn, nxt["stage"]), "warning",
                         "New request to sign",
                         f"{pr['pr_no']} needs your {stage_label(nxt['stage'])} approval.",
                         link=_pr_link(pr_id))
            conn.commit()
            return True, "advanced"

        # last step approved -> PR approved, auto-generate a PO number
        po_no = doc_no("PO", pr_id)
        conn.execute(
            "UPDATE pr_requests SET status='approved', current_seq=0, approved_at=?, "
            "po_no=? WHERE id=?", (now, po_no, pr_id))
        audit(conn, pr_id, uname, "fully_approved",
              f"All approvals complete. PO {po_no} drafted.", ip)
        # tell the requester it's approved, and purchasing that a PO is ready
        notify_users(conn, [pr["requester"]], "info", "Request approved",
                     f"{pr['pr_no']} is fully approved. PO {po_no} drafted.", link=_pr_link(pr_id))
        notify_users(conn, eligible_approvers(conn, "purchasing"), "info", "PO ready to issue",
                     f"{pr['pr_no']} approved — issue PO {po_no}.", link=_pr_link(pr_id))
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


# --------------------------------------------------------------------------
# multi-quote comparison
# --------------------------------------------------------------------------
def add_quote(pr_id, data, user, filename=None, content_type=None, content_b64=None, ip=None):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO pr_quotes
               (pr_id, vendor, amount, currency, lead_time_days, warranty,
                filename, content_type, content_b64, notes, uploaded_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pr_id, data.get("vendor"), float(data.get("amount") or 0),
             data.get("currency") or "EGP",
             int(data["lead_time_days"]) if str(data.get("lead_time_days") or "").strip() else None,
             data.get("warranty"), filename, content_type, content_b64, data.get("notes"),
             user.get("username") if user else "system", _now()))
        audit(conn, pr_id, user.get("username") if user else "system", "quote_added",
              f"Quote from {data.get('vendor')} @ {data.get('amount')}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def choose_quote(pr_id, quote_id, user, ip=None):
    """Mark one quote as chosen (clears the others) and adopt its vendor on the PR."""
    conn = get_db()
    try:
        q = conn.execute("SELECT * FROM pr_quotes WHERE id=? AND pr_id=?",
                         (quote_id, pr_id)).fetchone()
        if not q:
            return False, "not_found"
        conn.execute("UPDATE pr_quotes SET is_chosen=0 WHERE pr_id=?", (pr_id,))
        conn.execute("UPDATE pr_quotes SET is_chosen=1 WHERE id=?", (quote_id,))
        conn.execute("UPDATE pr_requests SET vendor=? WHERE id=?", (q["vendor"], pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "quote_chosen",
              f"Selected {q['vendor']} @ {q['amount']}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def get_quote(quote_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM pr_quotes WHERE id=?", (quote_id,)).fetchone()
    finally:
        conn.close()


def quote_comparison(quotes):
    """Given a PR's quotes, return the min amount and potential saving vs the max."""
    amts = [q["amount"] for q in quotes if q.get("amount")]
    if len(amts) < 2:
        return {"lowest": min(amts) if amts else None, "saving": 0}
    return {"lowest": min(amts), "highest": max(amts), "saving": round(max(amts) - min(amts), 2)}


# --------------------------------------------------------------------------
# department budgets
# --------------------------------------------------------------------------
def _period():
    return str(_year())


def list_budgets(period=None):
    conn = get_db()
    try:
        if period:
            rows = conn.execute("SELECT * FROM proc_budgets WHERE period=? ORDER BY department",
                                (period,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM proc_budgets ORDER BY period DESC, department").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_budget(department, amount, period=None, currency="EGP", user=None):
    period = period or _period()
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM proc_budgets WHERE department=? AND period=?",
                           (department, period)).fetchone()
        if row:
            conn.execute("UPDATE proc_budgets SET amount=?, currency=? WHERE id=?",
                         (float(amount or 0), currency, row["id"]))
        else:
            conn.execute(
                "INSERT INTO proc_budgets (department, period, currency, amount, created_at) "
                "VALUES (?,?,?,?,?)", (department, period, currency, float(amount or 0), _now()))
        conn.commit()
        return True, ""
    finally:
        conn.close()


def budget_status(department, period=None, extra=0.0):
    """Return {amount, spent, remaining, pct, over} for a department+period.
    'spent' = committed spend (approved / po_issued / closed) this period; 'extra'
    lets a not-yet-submitted PR test whether it would breach."""
    if not department:
        return None
    period = period or _period()
    conn = get_db()
    try:
        b = conn.execute("SELECT amount, currency FROM proc_budgets WHERE department=? AND period=?",
                         (department, period)).fetchone()
        spent = conn.execute(
            "SELECT COALESCE(SUM(total),0) s FROM pr_requests WHERE department=? "
            "AND status IN ('approved','po_issued','closed') "
            "AND substr(COALESCE(request_date, created_at),1,4)=?", (department, period)).fetchone()["s"]
    finally:
        conn.close()
    if not b:
        return {"amount": None, "spent": spent, "remaining": None, "pct": None,
                "over": False, "currency": "EGP", "period": period}
    amount = b["amount"] or 0
    projected = spent + (extra or 0)
    remaining = amount - projected
    pct = round(100 * projected / amount) if amount else 0
    return {"amount": amount, "spent": spent, "remaining": remaining, "pct": pct,
            "over": projected > amount, "currency": b["currency"] or "EGP", "period": period}


# --------------------------------------------------------------------------
# delegations
# --------------------------------------------------------------------------
def list_delegations(active_only=False):
    conn = get_db()
    try:
        sql = "SELECT * FROM proc_delegations"
        if active_only:
            sql += " WHERE is_active=1"
        sql += " ORDER BY id DESC"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def add_delegation(from_user, to_user, from_date, to_date, note, user=None):
    if not from_user or not to_user or from_user == to_user:
        return False, "invalid"
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO proc_delegations (from_user, to_user, from_date, to_date, note, created_at)
               VALUES (?,?,?,?,?,?)""",
            (from_user, to_user, from_date or None, to_date or None, note, _now()))
        conn.commit()
        return True, ""
    finally:
        conn.close()


def revoke_delegation(deleg_id):
    conn = get_db()
    try:
        conn.execute("UPDATE proc_delegations SET is_active=0 WHERE id=?", (deleg_id,))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# SLA escalation job (call from a scheduler / admin action)
# --------------------------------------------------------------------------
def run_escalations():
    """Flag approval steps that have sat past the SLA and raise a bell alert once.
    Returns the number of steps newly escalated."""
    conn = get_db()
    n = 0
    try:
        prs = conn.execute("SELECT * FROM pr_requests WHERE status='pending'").fetchall()
        for pr in prs:
            step = conn.execute(
                "SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
                (pr["id"], pr["current_seq"])).fetchone()
            if not step or step["escalated"]:
                continue
            hrs = _age_hours(step["activated_at"] or pr["submitted_at"])
            if hrs is not None and hrs > C.SLA_HOURS_PER_STAGE:
                conn.execute("UPDATE pr_steps SET escalated=1, escalated_at=? WHERE id=?",
                             (_now(), step["id"]))
                audit(conn, pr["id"], "system", "escalated",
                      f"{stage_label(step['stage'])} overdue ({round(hrs)}h)")
                targets = eligible_approvers(conn, step["stage"]) or [pr["requester"]]
                notify_users(conn, targets, "warning", "Approval overdue",
                             f"{pr['pr_no']} has waited {round(hrs)}h at {stage_label(step['stage'])}.",
                             link=_pr_link(pr["id"]))
                n += 1
        conn.commit()
    finally:
        conn.close()
    return n


# --------------------------------------------------------------------------
# spend analytics
# --------------------------------------------------------------------------
def analytics_summary():
    """Aggregate procurement KPIs + breakdowns for the analytics dashboard."""
    conn = get_db()
    try:
        prs = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_requests WHERE is_active=1").fetchall()]
        steps = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_steps").fetchall()]
    finally:
        conn.close()

    committed = [p for p in prs if p["status"] in ("approved", "po_issued", "closed")]
    by = lambda key, rows: _group_sum(rows, key, "total")
    spend_dept = by("department", committed)
    spend_vendor = by("vendor", committed)
    # monthly committed spend
    months = {}
    for p in committed:
        m = (p.get("request_date") or p.get("created_at") or "")[:7]
        if m:
            months[m] = round(months.get(m, 0) + (p.get("total") or 0), 2)
    monthly = sorted(months.items())

    # cycle time (submitted -> approved), in days
    durs = []
    for p in prs:
        if p.get("submitted_at") and p.get("approved_at"):
            h = _age_hours(p["submitted_at"])
            a = _age_hours(p["approved_at"])
            if h is not None and a is not None:
                durs.append((h - a) / 24.0)
    avg_cycle = round(sum(durs) / len(durs), 1) if durs else 0

    # bottleneck: avg hours a stage takes to be acted on + rejection counts
    stage_times, stage_rej = {}, {}
    for s in steps:
        if s.get("status") in ("approved", "rejected") and s.get("activated_at") and s.get("acted_at"):
            act = _age_hours(s["activated_at"])
            done = _age_hours(s["acted_at"])
            if act is not None and done is not None:
                stage_times.setdefault(s["stage"], []).append(max(0, act - done))
        if s.get("status") == "rejected":
            stage_rej[s["stage"]] = stage_rej.get(s["stage"], 0) + 1
    stage_avg = {k: round(sum(v) / len(v), 1) for k, v in stage_times.items() if v}
    bottleneck = max(stage_avg, key=stage_avg.get) if stage_avg else None

    status_counts = {}
    for p in prs:
        status_counts[p["status"]] = status_counts.get(p["status"], 0) + 1

    return {
        "total_prs": len(prs),
        "committed_spend": round(sum(p.get("total") or 0 for p in committed), 2),
        "pending_value": round(sum(p.get("total") or 0 for p in prs if p["status"] == "pending"), 2),
        "avg_cycle_days": avg_cycle,
        "status_counts": status_counts,
        "spend_by_dept": spend_dept,
        "spend_by_vendor": dict(sorted(spend_vendor.items(), key=lambda kv: -kv[1])[:8]),
        "monthly": monthly,
        "stage_avg_hours": stage_avg,
        "stage_rejections": stage_rej,
        "bottleneck": stage_label(bottleneck) if bottleneck else None,
    }


def _group_sum(rows, key, val):
    out = {}
    for r in rows:
        k = r.get(key) or "—"
        out[k] = round(out.get(k, 0) + (r.get(val) or 0), 2)
    return out


# --------------------------------------------------------------------------
# auto-PR from a maintenance work order
# --------------------------------------------------------------------------
def ticket_prefill(ticket_id):
    """Return header + item prefill for a new PR seeded from a maintenance ticket."""
    conn = get_db()
    try:
        t = conn.execute("SELECT * FROM mnt_tickets WHERE id=?", (ticket_id,)).fetchone()
    except Exception:
        t = None
    finally:
        conn.close()
    if not t:
        return None
    t = dict(t)
    label = t.get("machine_code") or t.get("ticket_no") or ""
    return {
        "title": f"Repair: {t.get('description') or t.get('ticket_no') or ''}"[:120],
        "request_for": (f"{label} — {t.get('issue_category') or ''}").strip(" —"),
        "department": t.get("department") or "General Maintenance",
        "notes": f"Raised from maintenance ticket {t.get('ticket_no') or ticket_id}.",
        "item": (t.get("issue_category") or "Repair").replace("_", " ").title(),
        "description": t.get("description") or "",
    }
