"""
TC Platform — Procurement & Approvals service layer.

All workflow logic lives here so routes stay thin: creating a PR, building the
threshold-driven approval ladder, stamping an approver's digital signature,
advancing/rejecting, and auto-generating the Purchase Order on final approval.
Each mutating action writes an immutable audit event and surfaces the right
platform-bell notification.
"""
import hashlib
import secrets
from datetime import datetime, timezone

from app.db import get_db
from app.security import has_permission
from app.approvals import constants as C
from app.approvals.constants import (
    build_ladder, ladder_rungs, rungs_from_stages, stage_label, STAGE_ROLES,
    PR_STATUSES, LADDER, APPROVAL_MATRIX, DEPARTMENTS,
    VALUE_STAGES, DEMAND_STAGES, PRICING_GATE_STAGE)


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
    """Send a per-user bell notification to each username (deduped, skips blanks),
    and, when SMTP is configured, an email to those who allow it."""
    targets = {u for u in usernames if u}
    for u in targets:
        conn.execute(
            "INSERT INTO notifications (severity, module, title, message, target_user, link, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (severity, "procurement", title, message, u, link, _now()))
    try:
        _email_targets(conn, targets, title, message, link)
    except Exception:
        pass  # email is best-effort; never block the workflow


def _email_enabled(prefs):
    """Per-user email preference (default ON if unset)."""
    if not prefs:
        return True
    try:
        import json
        p = json.loads(prefs) if isinstance(prefs, str) else prefs
        return bool(p.get("email", True))
    except Exception:
        return True


def _email_targets(conn, usernames, title, message, link):
    from config import Config
    if not getattr(Config, "SMTP_HOST", "") or not usernames:
        return
    ph = ",".join(["?"] * len(usernames))
    rows = conn.execute(
        f"SELECT email, notif_prefs FROM users WHERE username IN ({ph}) AND is_active=1",
        tuple(usernames)).fetchall()
    recips = [r["email"] for r in rows if r["email"] and _email_enabled(r["notif_prefs"])]
    if not recips:
        return
    base = (getattr(Config, "PUBLIC_URL", "") or "").rstrip("/")
    url = (base + link) if (base and link) else (link or "")
    from app.services.alerts import send_email_to
    body = f"{message}\n\n{('Open: ' + url) if url else 'Open the TC Platform to review.'}\n\n— TC Platform"
    send_email_to(recips, f"[TC Platform] {title}", body)


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


def can_act(user, stage, _deleg=None):
    """May this user approve/reject the given ladder stage? True for the stage's
    own roles, for super_admin / proc_admin, or for anyone actively delegated a
    qualifying role by another user. Pass `_deleg` (a prefetched set from
    active_delegator_roles) when checking many stages in a loop so the
    delegation lookup runs one query instead of one per stage."""
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
    if _deleg is None:
        _deleg = active_delegator_roles(user.get("username"))
    return bool(allowed & _deleg)


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
        invoices = conn.execute(
            "SELECT id, pr_id, invoice_no, invoice_date, amount, tax, currency, status, "
            "filename, notes, created_by, created_at FROM pr_invoices WHERE pr_id=? "
            "ORDER BY id", (pr_id,)).fetchall()
        payments = conn.execute(
            "SELECT id, pr_id, invoice_id, amount, currency, method, reference, paid_at, "
            "notes, created_at FROM pr_payments WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()
        try:
            sig_evs = conn.execute(
                "SELECT seq, stage, code FROM pr_sign_events WHERE pr_id=? ORDER BY id",
                (pr_id,)).fetchall()
        except Exception:
            sig_evs = []
    finally:
        conn.close()
    pr_d = dict(pr)
    # latest verification code per (seq, stage) — printed on the PDF signature grid
    codes = {(e["seq"], e["stage"]): e["code"] for e in sig_evs}
    step_ds = []
    for r in steps:
        s = dict(r)
        s["verify_code"] = codes.get((s.get("seq"), s.get("stage")))
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
            "quotes": [dict(r) for r in quotes],
            "invoices": [dict(r) for r in invoices], "payments": [dict(r) for r in payments]}


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
    """PRs currently waiting on a stage this user is allowed to act on.

    Batched: one query for the PRs, one for ALL their pending steps, and one
    delegation lookup — regardless of how many PRs are pending. (The previous
    per-PR connection loop multiplied database round-trips by the queue size,
    which is what made procurement pages crawl once auto-reorder PRs landed.)"""
    if not user:
        return []
    prs = list_prs(status="pending")
    if not prs:
        return []
    ids = [pr["id"] for pr in prs]
    conn = get_db()
    try:
        ph = ",".join("?" for _ in ids)
        step_rows = conn.execute(
            f"SELECT * FROM pr_steps WHERE status='pending' AND pr_id IN ({ph})",
            tuple(ids)).fetchall()
    finally:
        conn.close()
    by_pr = {}
    for s in step_rows:
        by_pr.setdefault(s["pr_id"], []).append(s)
    deleg = active_delegator_roles(user.get("username"))
    out = []
    for pr in prs:
        cur = [s for s in by_pr.get(pr["id"], []) if s["seq"] == pr["current_seq"]]
        mine = next((s for s in cur if can_act(user, s["stage"], _deleg=deleg)), None)
        if mine:
            pr = dict(pr)
            pr["_stage"] = mine["stage"]
            pr["_stage_label"] = stage_label(mine["stage"])
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
# --------------------------------------------------------------------------
# Responsibility (approval) matrix — per department
# --------------------------------------------------------------------------
def dept_ladder(conn, department, total):
    """Ordered stage list required for a PR of `total` in `department`. Uses the
    department's custom responsibility matrix if one exists, else the global
    default (constants.APPROVAL_MATRIX)."""
    try:
        t = float(total or 0)
    except (TypeError, ValueError):
        t = 0.0
    rows = conn.execute(
        "SELECT stage, threshold FROM proc_resp_matrix "
        "WHERE department=? AND active=1 ORDER BY seq, id", (department or "",)).fetchall()
    if rows:
        return [r["stage"] for r in rows if t >= float(r["threshold"] or 0)]
    return build_ladder(t)


def list_departments():
    """Departments for the dropdown: constant defaults + any already used by a
    budget or a responsibility matrix, de-duplicated, order preserved."""
    conn = get_db()
    try:
        used = [r["department"] for r in conn.execute(
            "SELECT DISTINCT department FROM proc_budgets WHERE department IS NOT NULL "
            "UNION SELECT DISTINCT department FROM proc_resp_matrix WHERE department IS NOT NULL"
        ).fetchall()]
    finally:
        conn.close()
    out = []
    for d in list(DEPARTMENTS) + used:
        d = (d or "").strip()
        if d and d not in out:
            out.append(d)
    return out


def all_dept_matrices():
    """{department: {stage: threshold}} for departments with a custom matrix —
    drives the live approval-route preview on the new-request form."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT department, stage, threshold FROM proc_resp_matrix WHERE active=1").fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["department"], {})[r["stage"]] = float(r["threshold"] or 0)
    return out


def get_dept_matrix(department):
    """For the settings UI: {stage: {included, threshold, seq}} across the whole
    canonical LADDER (a department's custom rows override the defaults)."""
    conn = get_db()
    try:
        rows = {r["stage"]: r for r in conn.execute(
            "SELECT stage, threshold, seq FROM proc_resp_matrix WHERE department=? AND active=1",
            (department or "",)).fetchall()}
    finally:
        conn.close()
    out = {}
    for i, stage in enumerate(LADDER):
        if stage in rows:
            seq = rows[stage]["seq"]
            out[stage] = {"included": True, "threshold": float(rows[stage]["threshold"] or 0),
                          "seq": seq if seq is not None else i}
        else:
            out[stage] = {"included": False, "threshold": float(APPROVAL_MATRIX.get(stage, 0)),
                          "seq": i}
    return out


def set_dept_matrix(department, stage_rows, user=None):
    """Replace a department's responsibility matrix. `stage_rows` = ordered list
    of {stage, threshold} for the INCLUDED stages. Empty list clears it (the
    department reverts to the global default)."""
    department = (department or "").strip()
    if not department:
        return False, "no_department"
    conn = get_db()
    try:
        conn.execute("DELETE FROM proc_resp_matrix WHERE department=?", (department,))
        now = _now()
        for i, r in enumerate(stage_rows):
            stage = r.get("stage")
            if stage not in LADDER:
                continue
            conn.execute(
                "INSERT INTO proc_resp_matrix (department, stage, threshold, seq, active, updated_at) "
                "VALUES (?,?,?,?,1,?)", (department, stage, float(r.get("threshold") or 0), i, now))
        conn.commit()
        return True, ""
    finally:
        conn.close()


def delete_dept_matrix(department):
    """Remove a department's custom responsibility matrix. A custom-added
    department disappears from the list; a built-in one reverts to the global
    default route. Returns (ok, was_builtin)."""
    department = (department or "").strip()
    if not department:
        return False, False
    was_builtin = department in DEPARTMENTS
    conn = get_db()
    try:
        conn.execute("DELETE FROM proc_resp_matrix WHERE department=?", (department,))
        conn.commit()
    finally:
        conn.close()
    return True, was_builtin


def rename_dept(old, new):
    """Rename a custom department everywhere it is referenced (matrix + budgets).
    Built-in departments (defined in code) cannot be renamed. Returns (ok, msg)."""
    old = (old or "").strip()
    new = (new or "").strip()[:60]
    if not old or not new:
        return False, "empty"
    if old == new:
        return True, ""
    if old in DEPARTMENTS:
        return False, "builtin"
    if new in list_departments():
        return False, "exists"
    conn = get_db()
    try:
        conn.execute("UPDATE proc_resp_matrix SET department=? WHERE department=?", (new, old))
        try:
            conn.execute("UPDATE proc_budgets SET department=? WHERE department=?", (new, old))
        except Exception:  # noqa: BLE001 — budgets table/column optional
            pass
        conn.commit()
        return True, ""
    finally:
        conn.close()


def create_pr(header, items, user, ip=None, submit=True, priced=None):
    """Create a PR (+items). When submit=True, build the ladder and route it.

    `priced` records whether the request already carries commercial pricing:
    when None it is inferred (a request with a positive total is 'priced', a
    zero-value requester-raised request is 'unpriced' and must be priced by
    Purchasing at the pricing gate). Returns (pr_id, pr_no)."""
    conn = get_db()
    try:
        total = round(sum(_amount(it) for it in items), 2)
        if priced is None:
            priced = total > 0
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
        try:
            conn.execute("UPDATE pr_requests SET tax_rate=?, pricing_status=? WHERE id=?",
                         (float(header.get("tax_rate") or 0),
                          "priced" if priced else "unpriced", pr_id))
        except Exception:
            pass
        audit(conn, pr_id, uname, "created",
              f"PR {pr_no} created" + (f" (total {total})" if priced else " (pricing pending)"), ip)
        conn.commit()
    finally:
        conn.close()
    if submit:
        submit_pr(pr_id, user, ip)
    return pr_id, pr_no


def update_pr(pr_id, header, items, user, ip=None, can_price=True):
    """Replace a DRAFT (or rejected) PR's header + line items. Only the owner /
    an admin should reach this. Returns (ok, msg).

    `can_price=False` (a requester editing): the edit replaces the lines with
    zero prices, so any pricing Purchasing had entered is gone — the PR MUST
    drop back to 'unpriced' and pass the pricing gate again. Without this, an
    edit-after-reject kept pricing_status='priced' on a now-zero total, which
    skipped the pricing gate AND the value-based Finance/CFO/CEO rungs."""
    conn = get_db()
    try:
        pr = conn.execute(
            "SELECT status, requester, tax_rate, payment_condition, pricing_status "
            "FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("draft", "rejected"):
            return False, "not_editable"
        total = round(sum(_amount(it) for it in items), 2)
        # Requesters can't set commercial terms: keep whatever Purchasing entered.
        tax_rate = float(header.get("tax_rate") or 0) if can_price \
            else float(pr["tax_rate"] or 0)
        pay_cond = header.get("payment_condition") if can_price \
            else pr["payment_condition"]
        conn.execute(
            """UPDATE pr_requests SET title=?, request_for=?, department=?, currency=?,
               vendor=?, payment_condition=?, delivery_condition=?, req_del_date=?,
               asset_code=?, notes=?, tax_rate=?, total=? WHERE id=?""",
            (header.get("title"), header.get("request_for"), header.get("department"),
             header.get("currency") or "EGP", header.get("vendor"),
             pay_cond, header.get("delivery_condition"),
             header.get("req_del_date"), header.get("asset_code"), header.get("notes"),
             tax_rate, total, pr_id))
        if not can_price and (pr["pricing_status"] or "priced") == "priced":
            # the lines were replaced unpriced -> back through the pricing gate
            conn.execute("UPDATE pr_requests SET pricing_status='unpriced', "
                         "priced_at=NULL, priced_by=NULL WHERE id=?", (pr_id,))
        conn.execute("DELETE FROM pr_items WHERE pr_id=?", (pr_id,))
        for i, it in enumerate(items, start=1):
            conn.execute(
                """INSERT INTO pr_items (pr_id, seq, item, description, unit, qty,
                   current_stock, vendor, unit_price, est_cost, notes)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (pr_id, i, it.get("item"), it.get("description"), it.get("unit") or "Pcs",
                 float(it.get("qty") or 0), float(it.get("current_stock") or 0),
                 it.get("vendor") or header.get("vendor"),
                 float(it.get("unit_price") or 0), round(_amount(it), 2), it.get("notes")))
        audit(conn, pr_id, user.get("username") if user else "system", "edited",
              f"Draft updated (total {total})", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def pr_amounts(pr):
    """Return {subtotal, tax_rate, tax, grand} for a PR dict/row."""
    subtotal = float(pr.get("total") or 0) if isinstance(pr, dict) else float(pr["total"] or 0)
    rate = float((pr.get("tax_rate") if isinstance(pr, dict) else pr["tax_rate"]) or 0)
    tax = round(subtotal * rate / 100.0, 2)
    return {"subtotal": round(subtotal, 2), "tax_rate": rate, "tax": tax,
            "grand": round(subtotal + tax, 2)}


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
        # Department-aware ladder: use the department's responsibility matrix if
        # it has one, else the global default. Each rung = parallel stages.
        rungs = rungs_from_stages(dept_ladder(conn, pr["department"], pr["total"]))
        now = _now()
        for i, rung in enumerate(rungs, start=1):
            for stage in rung:
                conn.execute(
                    """INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, activated_at, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (pr_id, i, stage, "pending", stage_label(stage), now if i == 1 else None, now))
        conn.execute(
            "UPDATE pr_requests SET status='pending', current_seq=1, submitted_at=?, "
            "rejection_reason=NULL WHERE id=?", (now, pr_id))
        uname = user.get("username") if user else "system"
        flat = [s for rung in rungs for s in rung]
        audit(conn, pr_id, uname, "submitted",
              f"Routed through {len(flat)} approvals: "
              f"{' -> '.join(' + '.join(stage_label(s) for s in rung) for rung in rungs)}", ip)
        # Notify every approver on the first rung that a request needs signing.
        targets = set()
        for stage in rungs[0]:
            targets |= set(eligible_approvers(conn, stage))
        first_label = " + ".join(stage_label(s) for s in rungs[0])
        notify_users(conn, list(targets), "warning", "New request to sign",
                     f"{pr['pr_no']} ({pr['title'] or ''}) needs your {first_label} approval.",
                     link=_pr_link(pr_id))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Pricing gate (Purchasing enters the commercial value)
# --------------------------------------------------------------------------
def _reconcile_value_ladder(conn, pr_id, department, total):
    """After a pending PR is priced, bring its value-based rungs (Finance / CFO /
    CEO) in line with the new total: append the ones now required that aren't in
    the ladder yet, and drop any not-yet-reached value rungs that no longer
    qualify. Never touches steps that are approved, rejected, or currently active,
    so an in-flight approval is never disturbed."""
    row = conn.execute("SELECT current_seq FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    cur_seq = (row["current_seq"] or 0) if row else 0
    # Reuse the tested department-aware ladder, keep only the value stages.
    target = [s for s in dept_ladder(conn, department, total) if s in VALUE_STAGES]
    existing = conn.execute(
        "SELECT id, seq, stage, status FROM pr_steps WHERE pr_id=? ORDER BY seq",
        (pr_id,)).fetchall()
    have = {r["stage"] for r in existing}
    max_seq = max([r["seq"] for r in existing] or [0])
    now = _now()
    # 1) prune future, still-pending value rungs that no longer qualify
    for r in existing:
        if r["stage"] in VALUE_STAGES and r["stage"] not in target \
           and r["status"] == "pending" and r["seq"] > cur_seq:
            conn.execute("DELETE FROM pr_steps WHERE id=?", (r["id"],))
    # 2) append any newly-required value rungs (in ladder order) after the last seq
    nxt = max_seq
    for s in target:
        if s not in have:
            nxt += 1
            conn.execute(
                "INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, created_at) "
                "VALUES (?,?,?,?,?,?)", (pr_id, nxt, s, "pending", stage_label(s), now))


def price_pr(pr_id, prices, meta, user, ip=None):
    """Purchasing enters commercial pricing for a request — the pricing gate.

    `prices` maps pr_items.id -> unit_price; `meta` may carry tax_rate,
    payment_condition, vendor and currency (all Purchasing-owned). The line
    costs and PR total are recomputed, the PR is marked 'priced', and — if it is
    already circulating — the value-based approval rungs the new total requires
    are added to the ladder. Returns (ok, msg)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("approved", "po_issued", "partially_received",
                             "received", "closed", "cancelled"):
            return False, "locked"
        items = conn.execute(
            "SELECT id, qty, unit_price FROM pr_items WHERE pr_id=? ORDER BY seq",
            (pr_id,)).fetchall()
        total = 0.0
        for it in items:
            up = prices.get(it["id"], prices.get(str(it["id"])))
            try:
                up = float(up)
            except (TypeError, ValueError):
                up = float(it["unit_price"] or 0)   # keep existing if not supplied
            up = max(up, 0.0)
            est = round(float(it["qty"] or 0) * up, 2)
            conn.execute("UPDATE pr_items SET unit_price=?, est_cost=? WHERE id=?",
                         (up, est, it["id"]))
            total += est
        total = round(total, 2)

        sets = ["total=?", "pricing_status='priced'", "priced_at=?", "priced_by=?"]
        params = [total, _now(), (user or {}).get("username")]
        tax_rate = meta.get("tax_rate")
        if tax_rate is not None and str(tax_rate).strip() != "":
            sets.append("tax_rate=?"); params.append(float(tax_rate or 0))
        for col in ("payment_condition", "vendor", "currency"):
            val = meta.get(col)
            if val:
                sets.append(f"{col}=?"); params.append(val)
        params.append(pr_id)
        conn.execute("UPDATE pr_requests SET " + ", ".join(sets) + " WHERE id=?", params)

        if pr["status"] == "pending":
            _reconcile_value_ladder(conn, pr_id, pr["department"], total)

        cur = meta.get("currency") or pr["currency"]
        audit(conn, pr_id, (user or {}).get("username"), "priced",
              f"Pricing entered by Purchasing — total {total:,.2f} {cur}", ip)
        notify_users(conn, [pr["requester"]], "info", "Request priced",
                     f"{pr['pr_no']} has been priced by Purchasing and is moving "
                     f"through the approval ladder.", link=_pr_link(pr_id))
        conn.commit()
        return True, "priced"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# approve / reject
# --------------------------------------------------------------------------
def _record_sign_event(conn, pr, step, decision, uname, signer_name, now, ip=None):
    """Immutable, publicly-verifiable record of one signature. The doc_hash
    anchors exactly what was signed; `code` is the handle printed on the PDF
    (…/procurement/verify/<code>). Best-effort: never blocks the decision."""
    try:
        code = secrets.token_urlsafe(9)
        payload = "|".join(str(x) for x in (
            pr["pr_no"], step["seq"], step["stage"], decision, uname,
            f"{float(pr['total'] or 0):.2f}", pr["currency"] or "", now))
        doc_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO pr_sign_events
               (code, pr_id, seq, stage, action, signer, signer_name, doc_hash, ip, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (code, pr["id"], step["seq"], step["stage"], decision, uname,
             signer_name, doc_hash, ip, now))
        return code
    except Exception:
        return None


def sign_events(pr_id):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM pr_sign_events WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()
    finally:
        conn.close()


def verify_sign_code(code):
    """Public verification: look a signature event up by its printed code.
    Returns (event, pr) or (None, None)."""
    if not code or len(str(code)) > 40:
        return None, None
    conn = get_db()
    try:
        ev = conn.execute("SELECT * FROM pr_sign_events WHERE code=?", (str(code),)).fetchone()
        if not ev:
            return None, None
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (ev["pr_id"],)).fetchone()
        return ev, pr
    finally:
        conn.close()


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
        # current rung may hold several parallel steps; pick the one this user
        # is eligible for (a co-approver only acts on their own stage).
        cur_steps = conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
            (pr_id, pr["current_seq"])).fetchall()
        if not cur_steps:
            return False, "no_active_step"
        step = next((s for s in cur_steps if can_act(user, s["stage"])), None)
        if not step:
            return False, "forbidden"

        # Pricing gate: the purchasing stage cannot be signed off until Purchasing
        # has entered the commercial value. Approving it unpriced would let a
        # zero-value request slip past the value-based Finance / CFO / CEO rungs.
        if decision == "approve" and step["stage"] == PRICING_GATE_STAGE \
           and (pr["pricing_status"] or "priced") != "priced":
            return False, "needs_pricing"

        uname = user.get("username")
        sig_png, sig_name = _user_sig(uname)
        now = _now()

        if decision == "reject":
            conn.execute(
                "UPDATE pr_steps SET status='rejected', approver_user=?, approver_name=?, "
                "approver_role=?, comment=?, sig_png=?, acted_at=? WHERE id=?",
                (uname, sig_name or user.get("full_name") or uname, user.get("role"),
                 comment, sig_png, now, step["id"]))
            _record_sign_event(conn, pr, step, "reject", uname,
                               sig_name or user.get("full_name") or uname, now, ip)
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
        _record_sign_event(conn, pr, step, "approve", uname,
                           sig_name or user.get("full_name") or uname, now, ip)
        audit(conn, pr_id, uname, "approved",
              f"{stage_label(step['stage'])} approved" + (f": {comment}" if comment else ""), ip)

        # Parallel rung: if co-approvers at this seq are still pending, wait.
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
            (pr_id, step["seq"])).fetchone()["c"]
        if remaining > 0:
            conn.commit()
            return True, "partial"

        # Whole rung approved -> advance to the next rung (activate all its steps).
        nxt_seq = conn.execute(
            "SELECT MIN(seq) m FROM pr_steps WHERE pr_id=? AND seq>? AND status='pending'",
            (pr_id, step["seq"])).fetchone()["m"]
        if nxt_seq is not None:
            conn.execute("UPDATE pr_requests SET current_seq=? WHERE id=?", (nxt_seq, pr_id))
            conn.execute("UPDATE pr_steps SET activated_at=? WHERE pr_id=? AND seq=?",
                         (now, pr_id, nxt_seq))
            nxt_stages = [r["stage"] for r in conn.execute(
                "SELECT stage FROM pr_steps WHERE pr_id=? AND seq=?", (pr_id, nxt_seq)).fetchall()]
            targets = set()
            for s in nxt_stages:
                targets |= set(eligible_approvers(conn, s))
            notify_users(conn, list(targets), "warning", "New request to sign",
                         f"{pr['pr_no']} needs your "
                         f"{' + '.join(stage_label(s) for s in nxt_stages)} approval.",
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


def issue_po(pr_id, user, ip=None, force=False):
    """Mark an approved PR's PO as issued (purchasing action). When an explicit
    department budget exists and this PO would leave it exceeded, issuing is
    blocked unless an admin overrides (audited). Departments with no budget row
    configured are never blocked."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("approved",):
            return False, "not_approved"
    finally:
        conn.close()
    if not force:
        b = budget_status(pr["department"])   # PR already counted in 'spent' (approved)
        if b and b.get("amount") is not None and b.get("over"):
            return False, "over_budget"
    conn = get_db()
    try:
        if force:
            audit(conn, pr_id, (user or {}).get("username"), "po_override",
                  "PO issued with admin override (budget exceeded)", ip)
        po_no = pr["po_no"] or doc_no("PO", pr_id)
        conn.execute("UPDATE pr_requests SET status='po_issued', po_no=? WHERE id=?",
                     (po_no, pr_id))
        audit(conn, pr_id, user.get("username"), "po_issued", f"PO {po_no} issued", ip)
        bell(conn, "info", "Purchase Order issued", f"{po_no} issued for {pr['pr_no']}.")
        conn.commit()
        return True, po_no
    finally:
        conn.close()


def receive_goods(pr_id, user, notes=None, ip=None):
    """Confirm delivery/goods-receipt for an issued PO. po_issued -> received."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("po_issued", "approved"):
            return False, "not_receivable"
        now = _now()
        conn.execute(
            "UPDATE pr_requests SET status='received', received_at=?, received_by=?, "
            "receipt_notes=? WHERE id=?",
            (now, user.get("username") if user else "system", notes, pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "received",
              "Goods/services received" + (f": {notes}" if notes else ""), ip)
        notify_users(conn, [pr["requester"]], "info", "Delivery confirmed",
                     f"{pr['pr_no']} was received.", link=_pr_link(pr_id))
        # Whole-PR receive: mark every line fully received so the maintenance
        # bridge (and line reports) see the delivered quantities.
        items = conn.execute("SELECT id, qty, received_qty FROM pr_items WHERE pr_id=?",
                             (pr_id,)).fetchall()
        remaining = {it["id"]: max(0.0, float(it["qty"] or 0) - float(it["received_qty"] or 0))
                     for it in items}
        for iid, qty in remaining.items():
            if qty > 0:
                conn.execute("UPDATE pr_items SET received_qty=qty WHERE id=?", (iid,))
        conn.commit()
        _post_bridge_receipt(pr_id, remaining, user)
        return True, ""
    finally:
        conn.close()


def receive_items(pr_id, receipts, user, notes=None, ip=None):
    """Record a line-level goods receipt. `receipts` = {item_id: qty_received_now}.
    Adds to each line's received_qty (capped at ordered), then sets the PR status to
    'received' (all lines fulfilled) or 'partially_received'."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("po_issued", "approved", "partially_received"):
            return False, "not_receivable"
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        now = _now()
        any_recv = False
        for it in items:
            add = receipts.get(str(it["id"])) or receipts.get(it["id"]) or 0
            try:
                add = float(add)
            except (TypeError, ValueError):
                add = 0
            if add <= 0:
                continue
            ordered = float(it["qty"] or 0)
            if ordered <= 0:
                continue    # never book receipts against a zero-quantity line
            already = float(it["received_qty"] or 0)
            new_total = min(ordered, already + add)
            conn.execute("UPDATE pr_items SET received_qty=? WHERE id=?", (new_total, it["id"]))
            any_recv = True
        if not any_recv:
            return False, "nothing_received"
        # recompute fulfilment
        items = conn.execute("SELECT qty, received_qty FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        fully = all(float(i["received_qty"] or 0) >= float(i["qty"] or 0) for i in items)
        new_status = "received" if fully else "partially_received"
        conn.execute(
            "UPDATE pr_requests SET status=?, received_at=?, received_by=?, "
            "receipt_notes=COALESCE(?, receipt_notes) WHERE id=?",
            (new_status, now if fully else pr["received_at"],
             user.get("username") if user else "system", notes, pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "goods_received",
              ("Fully received" if fully else "Partial receipt")
              + (f": {notes}" if notes else ""), ip)
        if fully:
            notify_users(conn, [pr["requester"]], "info", "Delivery confirmed",
                         f"{pr['pr_no']} fully received.", link=_pr_link(pr_id))
        conn.commit()
        _post_bridge_receipt(pr_id, receipts, user)
        return True, ("received" if fully else "partial")
    finally:
        conn.close()


def _post_bridge_receipt(pr_id, receipts, user):
    """Post a goods receipt back into the source system's stock (maintenance
    spare auto-reorder PRs). Post-commit, best-effort: never blocks receiving."""
    try:
        from app.maintenance.procure_bridge import post_receipt_to_stock
        post_receipt_to_stock(pr_id, receipts, user)
    except Exception:
        pass


def _due_date(payment_condition, base_date):
    """Compute an invoice due date from the payment terms (Net 15/30/60)."""
    import re
    from datetime import datetime, timedelta
    m = re.search(r"net\s*(\d+)", (payment_condition or "").lower())
    if not m:
        return None
    try:
        d = datetime.strptime(str(base_date)[:10], "%Y-%m-%d")
        return (d + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    except Exception:
        return None


def add_invoice(pr_id, data, user, filename=None, content_type=None, content_b64=None, ip=None):
    """Record a vendor invoice, then (re)run the 3-way match for the PR."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        # invoices only make sense once the request is an actual order
        if pr["status"] not in ("approved", "po_issued", "partially_received",
                                "received", "closed"):
            return False, "not_invoicable"
        inv_no = (data.get("invoice_no") or "").strip()
        if inv_no:
            dup = conn.execute(
                "SELECT id FROM pr_invoices WHERE pr_id=? AND invoice_no=?",
                (pr_id, inv_no)).fetchone()
            if dup:
                return False, "duplicate_invoice"
        now = _now()
        conn.execute(
            """INSERT INTO pr_invoices (pr_id, invoice_no, invoice_date, amount, tax,
               currency, status, filename, content_type, content_b64, notes, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pr_id, data.get("invoice_no"), data.get("invoice_date") or now[:10],
             float(data.get("amount") or 0), float(data.get("tax") or 0),
             data.get("currency") or pr["currency"] or "EGP", "received",
             filename, content_type, content_b64, data.get("notes"),
             user.get("username") if user else "system", now))
        # set a due date from the payment terms if not already set
        due = _due_date(pr["payment_condition"], data.get("invoice_date") or now[:10])
        if due and not pr["due_date"]:
            conn.execute("UPDATE pr_requests SET due_date=? WHERE id=?", (due, pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "invoice_added",
              f"Invoice {data.get('invoice_no')} @ {data.get('amount')}", ip)
        conn.commit()
    finally:
        conn.close()
    three_way_match(pr_id)   # refresh cached match
    return True, ""


def three_way_match(pr_id):
    """Compare ORDERED (PO) vs RECEIVED (GRN) vs INVOICED. Returns a dict with
    per-check verdicts and flags, and caches it on the latest invoice."""
    bundle = get_pr(pr_id)
    if not bundle:
        return None
    pr, items = bundle["pr"], bundle["items"]
    amt = pr_amounts(pr)
    ordered_grand = amt["grand"]
    ordered_qty = sum(float(i["qty"] or 0) for i in items)
    received_qty = sum(float(i["received_qty"] or 0) for i in items)
    received_value = round(sum(float(i["received_qty"] or 0) * float(i["unit_price"] or 0)
                               for i in items), 2)
    invoiced = round(sum(float(iv["amount"] or 0) + float(iv["tax"] or 0)
                         for iv in bundle["invoices"]), 2)          # gross (with tax)
    invoiced_net = round(sum(float(iv["amount"] or 0) for iv in bundle["invoices"]), 2)  # pre-tax

    tol = max(1.0, ordered_grand * 0.01)      # 1% (or 1 unit) tolerance
    flags = []
    qty_ok = received_qty >= ordered_qty - 1e-6
    if not qty_ok:
        flags.append(f"Short delivery: received {received_qty:g} of {ordered_qty:g} ordered")
    price_ok = invoiced <= ordered_grand + tol
    if invoiced > ordered_grand + tol:
        flags.append(f"Over-billing: invoiced {invoiced:,.2f} vs PO {ordered_grand:,.2f}")
    # compare like-for-like: invoice net (pre-tax) vs received goods value (pre-tax)
    receipt_inv_ok = (not bundle["invoices"]) or invoiced_net <= received_value + tol
    if bundle["invoices"] and invoiced_net > received_value + tol:
        flags.append(f"Invoiced more than received ({invoiced_net:,.2f} vs received value {received_value:,.2f})")

    matched = bool(bundle["invoices"]) and qty_ok and price_ok and receipt_inv_ok
    result = {
        "ordered_grand": ordered_grand, "ordered_qty": ordered_qty,
        "received_qty": received_qty, "received_value": received_value,
        "invoiced": invoiced, "qty_ok": qty_ok, "price_ok": price_ok,
        "receipt_inv_ok": receipt_inv_ok, "flags": flags,
        "status": "matched" if matched else ("mismatch" if bundle["invoices"] else "pending"),
        "has_invoice": bool(bundle["invoices"]),
    }
    # cache on the most recent invoice + mark matched/disputed
    if bundle["invoices"]:
        conn = get_db()
        try:
            last = bundle["invoices"][-1]["id"]
            conn.execute("UPDATE pr_invoices SET match_json=?, status=? WHERE id=?",
                         (_json_dumps(result), "matched" if matched else "disputed", last))
            conn.commit()
        finally:
            conn.close()
    return result


def _json_dumps(d):
    import json
    try:
        return json.dumps(d)
    except Exception:
        return "{}"


def get_invoice(inv_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM pr_invoices WHERE id=?", (inv_id,)).fetchone()
    finally:
        conn.close()


def add_payment(pr_id, data, user, ip=None, force=False):
    """Record a payment against the PR/invoice and roll up the payment status.

    Business gates (bypassable only with `force` = admin override, audited):
      * payments start once a PO exists (po_issued and later) — never on a
        draft/pending/cancelled request;
      * the 3-way match must not show over-billing (invoice > PO, or invoice >
        received value) — "payment block on mismatch";
      * total paid may not exceed the PO grand total (+1% tolerance)."""
    # run the match first (own connections) — before opening ours
    match = three_way_match(pr_id)
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("po_issued", "partially_received", "received", "closed"):
            return False, "not_payable"
        amount = float(data.get("amount") or 0)
        if amount <= 0:
            return False, "bad_amount"
        # over-billing block (short delivery alone does NOT block: paying for
        # what WAS received on a partial delivery is legitimate)
        if match and match["has_invoice"] and not force \
           and (not match["price_ok"] or not match["receipt_inv_ok"]):
            return False, "match_blocked"
        grand = pr_amounts(pr)["grand"]
        tol = max(1.0, grand * 0.01)
        already = float(pr["paid_amount"] or 0)
        if not force and grand > 0 and already + amount > grand + tol:
            return False, "over_payment"
        now = _now()
        if force:
            audit(conn, pr_id, (user or {}).get("username"), "payment_override",
                  "Payment recorded with admin override (match/limit checks bypassed)", ip)
        conn.execute(
            """INSERT INTO pr_payments (pr_id, invoice_id, amount, currency, method,
               reference, paid_at, notes, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (pr_id, data.get("invoice_id") or None, amount, pr["currency"] or "EGP",
             data.get("method"), data.get("reference"), data.get("paid_at") or now[:10],
             data.get("notes"), user.get("username") if user else "system", now))
        paid = float(pr["paid_amount"] or 0) + amount
        grand = pr_amounts(pr)["grand"]
        pstatus = "paid" if paid >= grand - 0.01 else ("partial" if paid > 0 else "unpaid")
        conn.execute("UPDATE pr_requests SET paid_amount=?, payment_status=? WHERE id=?",
                     (round(paid, 2), pstatus, pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "payment",
              f"Paid {amount:,.2f} ({pstatus})", ip)
        if pstatus == "paid":
            notify_users(conn, [pr["requester"]], "info", "Payment complete",
                         f"{pr['pr_no']} is fully paid.", link=_pr_link(pr_id))
        conn.commit()
        return True, pstatus
    finally:
        conn.close()


def close_pr(pr_id, user, ip=None):
    conn = get_db()
    try:
        pr = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("received", "po_issued", "partially_received"):
            return False, "not_closable"
        conn.execute("UPDATE pr_requests SET status='closed', closed_at=? WHERE id=?",
                     (_now(), pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "closed", "Closed", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def email_po_to_vendor(pr_id, user, pdf_bytes=None, ip=None):
    """Email the Purchase Order (PDF attached) to the vendor's address on file."""
    bundle = get_pr(pr_id)
    if not bundle:
        return False, "not_found"
    pr = bundle["pr"]
    if pr["status"] not in ("approved", "po_issued", "received", "closed"):
        return False, "not_approved"
    # find the vendor's email
    conn = get_db()
    try:
        v = conn.execute("SELECT email FROM proc_vendors WHERE name=?", (pr.get("vendor"),)).fetchone()
    finally:
        conn.close()
    vendor_email = v["email"] if v else None
    if not vendor_email:
        return False, "no_vendor_email"
    if pdf_bytes is None:
        try:
            from app.approvals import pdf as _pdf
            pdf_bytes = _pdf.po_pdf(bundle)
        except Exception:
            pdf_bytes = None
    amt = pr_amounts(pr)
    body = (f"Dear {pr.get('vendor')},\n\n"
            f"Please find attached Purchase Order {pr.get('po_no')} "
            f"(ref {pr.get('pr_no')}) from T&C Garments.\n\n"
            f"Total: {amt['grand']:,.2f} {pr.get('currency') or ''}\n"
            f"Payment: {pr.get('payment_condition') or '-'}\n"
            f"Delivery: {pr.get('delivery_condition') or '-'}\n\n"
            f"Regards,\nT&C Garments — Purchasing")
    from app.services.alerts import send_email_to
    atts = [(f"{pr.get('po_no', 'PO')}.pdf", pdf_bytes, "application/pdf")] if pdf_bytes else None
    ok = send_email_to([vendor_email], f"Purchase Order {pr.get('po_no')} — T&C Garments",
                       body, attachments=atts)
    conn = get_db()
    try:
        conn.execute("UPDATE pr_requests SET po_sent_at=? WHERE id=?", (_now(), pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "po_emailed",
              f"PO emailed to {vendor_email}" + ("" if ok else " (SMTP not configured)"), ip)
        conn.commit()
    finally:
        conn.close()
    return (True, "sent") if ok else (True, "logged")   # logged = SMTP not set yet


def cancel_pr(pr_id, user, ip=None, is_purchasing=False, is_admin=False):
    """Withdraw a request. Authority: the requester may cancel their OWN request
    while it is still in flight (draft/pending/rejected); Purchasing may cancel
    anything not yet approved (incl. bridge auto-PRs); only an admin may cancel
    after approval — and never once goods have been received."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("closed", "cancelled"):
            return False, "already_closed"
        if pr["status"] in ("partially_received", "received"):
            return False, "already_received"     # goods in the door — close it, don't erase it
        uname = (user or {}).get("username")
        is_owner = uname and pr["requester"] == uname
        if pr["status"] in ("approved", "po_issued"):
            if not is_admin:
                return False, "needs_admin"      # committed spend: admin decision only
        elif not (is_owner or is_purchasing or is_admin):
            return False, "forbidden"
        conn.execute("UPDATE pr_requests SET status='cancelled', current_seq=0 WHERE id=?",
                     (pr_id,))
        audit(conn, pr_id, uname, "cancelled", "Request cancelled", ip)
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
        pr = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if pr and pr["status"] in ("approved", "po_issued", "partially_received",
                                   "received", "closed", "cancelled"):
            return False, "locked"   # the vendor is fixed once the PR is approved
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
            "AND status IN ('approved','po_issued','partially_received','received','closed') "
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
