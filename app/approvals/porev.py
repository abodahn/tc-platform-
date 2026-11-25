"""Purchase-order lane (DEPLOY 4).

(A) A REAL PO revision: a buyer changes line quantities, unit prices and the
    delivery date of ONE supplier's order, with a reason. A decrease or a date
    change applies at once; an increase beyond the 'po_rev_tolerance_pct'
    setting is parked until the value ladder dept_ladder() gives the new total
    has signed it (pr_po_rev_steps), the same authority and refusals as the
    request's own rungs. Applying writes the live rows (pr_items,
    pr_requests.total and that order's pr_purchase_orders row), so the budget,
    receiving caps, three-way match and payment caps read the revised numbers
    without a change of their own.
(B) Bulk signing: each request still goes through act_on_step, one by one.
(C) Copy a previous request into a new form (what was asked for, never money).
(D) Warehouse low stock -> what is already on order, and a pre-filled request.
"""
import json
import math
from datetime import datetime

from app.db import get_db
from app.approvals import constants as C
from app.approvals import services as svc

REVISABLE = ("po_issued", "in_transit", "partially_received")
# A request still in play for an item: drafts too, so a reorder is not raised twice.
OPEN_STATUSES = ("draft", "pending", "approved", "po_issued", "in_transit",
                 "partially_received")
# pr_sign_events.seq for a revision rung: far above any ladder seq, so the
# request PDF's (seq, stage) -> verify-code map never picks one up.
_SIGN_SEQ_BASE = 1000


def _f(v, default=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _num(raw):
    """A typed number, or None when it is not one (blank is None too)."""
    s = str(raw if raw is not None else "").strip().replace(",", "")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------------
# (A) PO revisions
# --------------------------------------------------------------------------
def _po_lines(items, pr, po, pos):
    """The request lines printed on `po` (the vendor identity po_groups uses)."""
    if len(pos) < 2:
        return list(items)
    want, head = C.vendor_key(po.get("vendor")), pr["vendor"]
    return [i for i in items if C.vendor_key(i["vendor"] or head) == want]


def _order_figures(conn, pr_id, po, pos):
    """(subtotal, tax, grand) of ONE order from the lines as they stand, with
    the arithmetic _issue_po_rows used to write it.
    shortcut: on a split request with a header discount, changing one order
    moves the discount share of the others, whose rows stay as issued;
    re-derive every order if that combination is ever used."""
    pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    rate = _f(pr["tax_rate"])
    head_sub = round(_f(pr["total"]), 2)
    if len(pos) < 2:
        tax = round(head_sub * rate / 100.0, 2)
        return head_sub, tax, round(head_sub + tax, 2)
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()]
    groups = svc.po_groups(items, pr)
    keys = [C.vendor_key(v) for v in groups]
    subs = [round(sum(svc._amount(l) for l in lines), 2) for lines in groups.values()]
    if _f(pr["disc_amount"]) and sum(subs):
        f = head_sub / sum(subs)
        subs = [round(s * f, 2) for s in subs]
        subs[0] = round(head_sub - sum(subs[1:]), 2)
    taxes = [round(s * rate / 100.0, 2) for s in subs]
    if abs(sum(subs) - head_sub) < 0.005:
        taxes[0] = round(round(head_sub * rate / 100.0, 2) - sum(taxes[1:]), 2)
    want = C.vendor_key(po.get("vendor"))
    i = keys.index(want) if want in keys else 0
    return subs[i], taxes[i], round(subs[i] + taxes[i], 2)


def _orders(conn, pr, user):
    """The request's order rows. A request issued before per-vendor orders has
    none (its one order lives in pr_requests.po_no): the row is written now,
    from the lines as they stand, so the revision has a row to number."""
    pos = svc._pos_for(conn, pr)
    if pos and pos[0].get("id") is None:
        pos = svc._issue_po_rows(conn, pr["id"], pr, pr["po_no"], user)
        conn.commit()          # the order exists whatever the revision then does
    return pos


def _apply_lines(conn, pr_id, lines):
    """Write the revised quantities / prices and re-derive the request total."""
    for sid, ch in lines.items():
        row = conn.execute("SELECT unit_price FROM pr_items WHERE id=? AND pr_id=?",
                           (int(sid), pr_id)).fetchone()
        if not row:
            continue
        qty, up = float(ch["qty"]), float(ch["unit_price"])
        conn.execute("UPDATE pr_items SET qty=?, unit_price=?, est_cost=? WHERE id=?",
                     (qty, up, round(qty * up, 2), int(sid)))
        if abs(up - _f(row["unit_price"])) > 1e-9:
            # no longer the awarded quotation's figure (same rule as price_pr)
            conn.execute("UPDATE pr_items SET quote_id=NULL WHERE id=?", (int(sid),))
    net = svc._reprice(conn, pr_id)
    if net is None:
        items = conn.execute("SELECT qty, unit_price, est_cost, disc_amount FROM pr_items "
                             "WHERE pr_id=?", (pr_id,)).fetchall()
        net = round(sum(svc._amount(dict(i)) for i in items), 2)
    conn.execute("UPDATE pr_requests SET total=? WHERE id=?", (net, pr_id))


def _money_out(conn, pr_id, po, pos):
    """What has already been invoiced or paid against this order."""
    if len(pos) > 1:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        ex = svc.po_exposure(conn, pr, po)
        return max(ex["invoiced"], ex["paid"])
    inv = conn.execute(
        "SELECT COALESCE(SUM(COALESCE(amount,0)+COALESCE(tax,0)),0) AS s FROM pr_invoices "
        "WHERE pr_id=? AND COALESCE(status,'')<>'void'", (pr_id,)).fetchone()["s"]
    paid = conn.execute("SELECT COALESCE(SUM(COALESCE(amount,0)),0) AS s FROM pr_payments "
                        "WHERE pr_id=?", (pr_id,)).fetchone()["s"]
    return max(_f(inv), _f(paid))


def _validate_lines(conn, pr_id, lines):
    """Re-check a change set against the lines as they stand NOW (goods may
    have arrived since it was proposed). Returns an error code or None."""
    for sid, ch in lines.items():
        row = conn.execute("SELECT received_qty FROM pr_items WHERE id=? AND pr_id=?",
                           (int(sid), pr_id)).fetchone()
        if not row:
            return "bad_line"
        if float(ch["qty"]) + 1e-9 < _f(row["received_qty"]):
            return "qty_below_received"
    return None


def _signers(conn, pr, step, exclude=()):
    people = svc.eligible_approvers(conn, step["stage"],
                                    roles=svc._csv_set(step["esc_role"]) or None)
    return set(people) - {pr["requester"]} - set(exclude)


def _rev_link(pr_id):
    return svc._pr_link(pr_id) + "#porev"


def _finish(conn, pr, po, pos, rev_no, changes, approved_grand=None):
    """Make a revision live on ONE order: its row is re-derived from the lines
    and numbered; on a single-order request pr_requests.po_rev follows.
    `approved_grand` None = the new total was signed, so it becomes the value
    later revisions are measured against; an unsigned revision keeps the old
    one, so small raises cannot creep past the tolerance one at a time."""
    sub, tax, grand = _order_figures(conn, pr["id"], po, pos)
    if approved_grand is None:
        approved_grand = grand
    conn.execute(
        "UPDATE pr_purchase_orders SET subtotal=?, tax=?, grand=?, rev=?, "
        "approved_grand=?, delivery_date=COALESCE(?, delivery_date) WHERE id=?",
        (sub, tax, grand, rev_no, approved_grand, changes.get("delivery_date"), po["id"]))
    if len(pos) == 1:
        conn.execute("UPDATE pr_requests SET po_rev=? WHERE id=?", (rev_no, pr["id"]))
    return grand


def _announce(conn, pr, po, rev_no, old_grand, new_grand, cur, reason, uname, ip):
    svc.audit(conn, pr["id"], uname, "po_revised",
              f"{po['po_no']} Rev {rev_no}: {old_grand:,.2f} -> {new_grand:,.2f} {cur} — {reason}",
              ip)
    svc.notify_users(conn, svc._requester_people(conn, pr)
                     | set(svc._perm_holders(conn, "proc_receive")), "info", "PO revised",
                     f"{po['po_no']} (ref {pr['pr_no']}) revised — Rev {rev_no}: {reason}",
                     link=_rev_link(pr["id"]))


def propose_revision(pr_id, po_id, posted_lines, delivery_date, reason, user,
                     ip=None, email=False):
    """A buyer revises ONE order. `posted_lines` = {item_id: {"qty", "unit_price"}}
    (a blank box keeps the line's value). Returns (True, "applied"|"pending")
    or (False, code)."""
    reason = (reason or "").strip()
    if not reason:
        return False, "reason_required"
    uname = (user or {}).get("username")
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        bad = svc._refuse_self_commercial(pr, user, "revise")
        if bad:
            return False, bad
        if pr["status"] not in REVISABLE:
            return False, "not_revisable"
        if conn.execute("SELECT 1 FROM pr_po_revisions WHERE pr_id=? AND status='pending'",
                        (pr_id,)).fetchone():
            return False, "rev_pending"
        pos = _orders(conn, pr, user)
        try:
            want = int(po_id) if po_id not in (None, "") else None
        except (TypeError, ValueError):
            want = None
        po = (next((p for p in pos if p["id"] == want), None) if want
              else (pos[0] if len(pos) == 1 else None))
        if not po:
            return False, "po_required"
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq, id",
                             (pr_id,)).fetchall()
        mine = _po_lines(items, pr, po, pos)
        lines, diff = {}, []
        posted_lines = posted_lines or {}
        for it in mine:
            got = posted_lines.get(it["id"]) or posted_lines.get(str(it["id"])) or {}
            old_q, old_p = _f(it["qty"]), _f(it["unit_price"])
            q, p = _num(got.get("qty")), _num(got.get("unit_price"))
            if str(got.get("qty") or "").strip() and q is None:
                return False, "bad_qty"
            if str(got.get("unit_price") or "").strip() and (p is None or p < 0):
                return False, "bad_price"
            q = old_q if q is None else q
            p = old_p if p is None else p
            if q <= 0:
                return False, "bad_qty"
            if q + 1e-9 < _f(it["received_qty"]):
                return False, "qty_below_received"
            if abs(q - old_q) > 1e-9 or abs(p - old_p) > 1e-9:
                lines[str(it["id"])] = {"qty": q, "unit_price": p}
                diff.append({"item": it["item"] or it["description"] or "",
                             "qty": [old_q, q], "unit_price": [old_p, p]})
        changes = {"lines": lines, "diff": diff, "email": bool(email)}
        d = (delivery_date or "").strip()
        old_d = po.get("delivery_date") or pr["req_del_date"] or ""
        if d and d != old_d:
            try:
                datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                return False, "bad_date"
            changes["delivery_date"] = d
            changes["delivery"] = [old_d, d]
        if not lines and "delivery_date" not in changes:
            return False, "no_change"

        old_grand = round(_f(po.get("grand")), 2)
        approved = round(_f(po.get("approved_grand"), old_grand)
                         if po.get("approved_grand") is not None else old_grand, 2)
        # Work the new figures out on the live rows, then decide whether they stay.
        _apply_lines(conn, pr_id, lines)
        new_grand = _order_figures(conn, pr_id, po, pos)[2]
        if new_grand + 0.005 < _money_out(conn, pr_id, po, pos):
            conn.rollback()
            return False, "below_billed"
        tol = _f(svc.num_setting(conn, "po_rev_tolerance_pct"))
        stages = []
        if new_grand > approved * (1 + tol / 100.0) + 0.005:
            after = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
            stages = [s for s in svc.dept_ladder(conn, pr["department"],
                                                 svc.egp_commitment(dict(after)),
                                                 pr["expenditure_kind"] or "opex",
                                                 pr_id=pr_id)
                      if s in C.VALUE_STAGES]
        rev_no = (max(int(po.get("rev") or 0), int(pr["po_rev"] or 0)) if len(pos) == 1
                  else int(po.get("rev") or 0)) + 1
        snapshot = svc._json_dumps({
            "items": [{k: it[k] for k in ("id", "item", "qty", "unit_price", "est_cost")}
                      for it in mine],
            "total": _f(pr["total"]), "grand": old_grand, "po_no": po["po_no"],
            "delivery_date": old_d})
        now = svc._now()
        cur = po.get("currency") or pr["currency"] or "EGP"
        if stages:
            conn.rollback()          # nothing changes until the increase is signed
            c = conn.execute(
                """INSERT INTO pr_po_revisions (pr_id, rev_no, reason, po_no, snapshot_json,
                   created_by, created_at, po_id, status, changes_json, old_grand,
                   new_grand, stages) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pr_id, rev_no, reason, po["po_no"], snapshot, uname, now, po["id"],
                 "pending", svc._json_dumps(changes), old_grand, new_grand,
                 ",".join(stages)))
            rev_id = c.lastrowid
            roles, chain = svc.stage_roles_map(conn), svc.escalation_map(conn)
            for n, s in enumerate(stages, start=1):
                esc, _from, why = svc._esc_columns(conn, s, pr["requester"], roles, chain,
                                                   _busy={uname})
                conn.execute(
                    "INSERT INTO pr_po_rev_steps (rev_id, pr_id, seq, stage, esc_role, status, "
                    "created_at) VALUES (?,?,?,?,?,?,?)",
                    (rev_id, pr_id, n, s, esc if why == "escalated" else None, "pending", now))
            svc.audit(conn, pr_id, uname, "po_revision_requested",
                      f"{po['po_no']} Rev {rev_no} raises the order from {old_grand:,.2f} to "
                      f"{new_grand:,.2f} {cur} (approved {approved:,.2f}) — waiting for "
                      + " -> ".join(C.stage_label(s) for s in stages) + f": {reason}", ip)
            first = conn.execute("SELECT * FROM pr_po_rev_steps WHERE rev_id=? AND seq=1",
                                 (rev_id,)).fetchone()
            svc.notify_users(conn, _signers(conn, pr, first, {uname}), "warning",
                             "PO revision to sign",
                             f"{po['po_no']} (ref {pr['pr_no']}) Rev {rev_no} raises the order "
                             f"to {new_grand:,.2f} {cur}: {reason}", link=_rev_link(pr_id))
            conn.commit()
            return True, "pending"
        conn.execute(
            """INSERT INTO pr_po_revisions (pr_id, rev_no, reason, po_no, snapshot_json,
               created_by, created_at, po_id, status, changes_json, old_grand, new_grand,
               decided_by, decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pr_id, rev_no, reason, po["po_no"], snapshot, uname, now, po["id"], "applied",
             svc._json_dumps(changes), old_grand, new_grand, uname, now))
        _finish(conn, pr, po, pos, rev_no, changes, approved)
        _announce(conn, pr, po, rev_no, old_grand, new_grand, cur, reason, uname, ip)
        conn.commit()
        po_id = po["id"]
    finally:
        conn.close()
    if email:
        svc.email_po_to_vendor(pr_id, user, ip=ip, po_id=po_id)
    return True, "applied"


def sign_revision(rev_id, user, decision, comment=None, ip=None):
    """Sign the current rung of a pending revision. The refusals a ladder
    signature meets: the rung's authority, never the requester, never the
    buyer who raised the revision, one rung per person."""
    if decision not in ("approve", "reject"):
        return False, "bad_decision"
    uname = (user or {}).get("username")
    comment = (comment or "").strip() or None
    conn = get_db()
    try:
        rev = conn.execute("SELECT * FROM pr_po_revisions WHERE id=?", (rev_id,)).fetchone()
        if not rev:
            return False, "not_found"
        if (rev["status"] or "applied") != "pending":
            return False, "not_pending"
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (rev["pr_id"],)).fetchone()
        step = conn.execute("SELECT * FROM pr_po_rev_steps WHERE rev_id=? AND status='pending' "
                            "ORDER BY seq LIMIT 1", (rev_id,)).fetchone()
        if not pr or not step:
            return False, "no_active_step"
        if not svc.can_act_step(user, step):
            return False, "forbidden"
        if decision == "approve":
            if pr["requester"] == uname and not svc.bool_setting(conn, "allow_self_approval"):
                return False, "self_approval"
            if rev["created_by"] == uname:
                return False, "self_revision"
        if (user or {}).get("role") != "super_admin" and conn.execute(
                "SELECT 1 FROM pr_po_rev_steps WHERE rev_id=? AND id<>? AND approver_user=? "
                "AND status IN ('approved','rejected')", (rev_id, step["id"], uname)).fetchone():
            return False, "dual_role"
        sig_png, sig_name = svc._user_sig(uname)
        signer = sig_name or (user or {}).get("full_name") or uname
        now = svc._now()
        cur = conn.execute(
            "UPDATE pr_po_rev_steps SET status=?, approver_user=?, approver_name=?, comment=?, "
            "sig_png=?, acted_at=? WHERE id=? AND status='pending'",
            ("approved" if decision == "approve" else "rejected", uname, signer, comment,
             sig_png, now, step["id"]))
        if cur.rowcount == 0:
            conn.rollback()
            return False, "not_pending"
        svc._record_sign_event(conn, pr, {"seq": _SIGN_SEQ_BASE * int(rev["rev_no"] or 0)
                                          + step["seq"], "stage": step["stage"]},
                               decision, uname, signer, now, ip)
        label = C.stage_label(step["stage"])
        if decision == "reject":
            conn.execute("UPDATE pr_po_revisions SET status='rejected', decided_by=?, "
                         "decided_at=? WHERE id=?", (uname, now, rev_id))
            conn.execute("UPDATE pr_po_rev_steps SET status='skipped' WHERE rev_id=? "
                         "AND status='pending'", (rev_id,))
            svc.audit(conn, pr["id"], uname, "po_revision_rejected",
                      f"{rev['po_no']} Rev {rev['rev_no']} rejected at {label}"
                      + (f": {comment}" if comment else "") + " — the order is unchanged.", ip)
            svc.notify_users(conn, {rev["created_by"]} - {None}, "warning",
                             "PO revision rejected",
                             f"{rev['po_no']} Rev {rev['rev_no']} was rejected at {label}"
                             + (f": {comment}" if comment else "."), link=_rev_link(pr["id"]))
            conn.commit()
            return True, "rejected"
        nxt = conn.execute("SELECT * FROM pr_po_rev_steps WHERE rev_id=? AND status='pending' "
                           "ORDER BY seq LIMIT 1", (rev_id,)).fetchone()
        svc.audit(conn, pr["id"], uname, "po_revision_signed",
                  f"{rev['po_no']} Rev {rev['rev_no']}: {label} approved"
                  + (f" — {comment}" if comment else ""), ip)
        if nxt:
            svc.notify_users(conn, _signers(conn, pr, nxt, {rev["created_by"], uname}),
                             "warning", "PO revision to sign",
                             f"{rev['po_no']} (ref {pr['pr_no']}) Rev {rev['rev_no']} needs your "
                             f"{C.stage_label(nxt['stage'])} approval.", link=_rev_link(pr["id"]))
            conn.commit()
            return True, "advanced"
        # Last rung: the change goes live — re-checked against the order as it is now.
        if pr["status"] not in REVISABLE:
            conn.rollback()
            return False, "not_revisable"
        changes = json.loads(rev["changes_json"] or "{}")
        lines = changes.get("lines") or {}
        bad = _validate_lines(conn, pr["id"], lines)
        if bad:
            conn.rollback()
            return False, bad
        pos = svc._pos_for(conn, pr)
        po = next((p for p in pos if p["id"] == rev["po_id"]), None)
        if not po:
            conn.rollback()
            return False, "po_required"
        _apply_lines(conn, pr["id"], lines)
        grand = _finish(conn, pr, po, pos, int(rev["rev_no"]), changes)
        conn.execute("UPDATE pr_po_revisions SET status='applied', new_grand=?, decided_by=?, "
                     "decided_at=? WHERE id=?", (grand, uname, now, rev_id))
        _announce(conn, pr, po, int(rev["rev_no"]), _f(rev["old_grand"]), grand,
                  po.get("currency") or pr["currency"] or "EGP", rev["reason"], uname, ip)
        conn.commit()
        email, pr_id, po_id, buyer = (bool(changes.get("email")), pr["id"], po["id"],
                                      rev["created_by"])
    finally:
        conn.close()
    if email:
        svc.email_po_to_vendor(pr_id, {"username": buyer}, ip=ip, po_id=po_id)
    return True, "applied"


def cancel_revision(rev_id, user, ip=None):
    """Purchasing withdraws a revision still waiting for signatures."""
    conn = get_db()
    try:
        rev = conn.execute("SELECT * FROM pr_po_revisions WHERE id=?", (rev_id,)).fetchone()
        if not rev:
            return False, "not_found"
        if (rev["status"] or "applied") != "pending":
            return False, "not_pending"
        uname, now = (user or {}).get("username"), svc._now()
        conn.execute("UPDATE pr_po_revisions SET status='cancelled', decided_by=?, decided_at=? "
                     "WHERE id=?", (uname, now, rev_id))
        conn.execute("UPDATE pr_po_rev_steps SET status='skipped' WHERE rev_id=? "
                     "AND status='pending'", (rev_id,))
        svc.audit(conn, rev["pr_id"], uname, "po_revision_cancelled",
                  f"{rev['po_no']} Rev {rev['rev_no']} withdrawn — the order is unchanged.", ip)
        conn.commit()
        return True, "cancelled"
    finally:
        conn.close()


def revision_pr(rev_id):
    """The request a revision belongs to, or None."""
    conn = get_db()
    try:
        row = conn.execute("SELECT pr_id FROM pr_po_revisions WHERE id=?", (rev_id,)).fetchone()
        return row["pr_id"] if row else None
    finally:
        conn.close()


def revision_view(pr_id, user):
    """What the request page's PO panel needs: the orders with their lines, the
    revision history (newest first, with before/after) and any pending one."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return None
        pos = svc._pos_for(conn, pr)
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()]
        revs = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_po_revisions WHERE pr_id=? ORDER BY id DESC", (pr_id,)).fetchall()]
        steps = [dict(r) for r in conn.execute(
            "SELECT id, rev_id, seq, stage, esc_role, status, approver_user, approver_name, "
            "comment, acted_at FROM pr_po_rev_steps WHERE pr_id=? ORDER BY seq",
            (pr_id,)).fetchall()]
        tol = _f(svc.num_setting(conn, "po_rev_tolerance_pct"))
    finally:
        conn.close()
    for p in pos:
        p["lines"] = _po_lines(items, pr, p, pos)
        p["delivery"] = p.get("delivery_date") or pr["req_del_date"] or ""
    pending, uname = None, (user or {}).get("username")
    for r in revs:
        try:
            r["changes"] = json.loads(r.get("changes_json") or "{}")
        except ValueError:
            r["changes"] = {}
        r["status"] = r.get("status") or "applied"
        r["steps"] = [s for s in steps if s["rev_id"] == r["id"]]
        if r["status"] == "pending":
            cur = next((s for s in r["steps"] if s["status"] == "pending"), None)
            r["current"] = cur
            r["can_sign"] = bool(cur and svc.can_act_step(user, cur)
                                 and uname not in (r["created_by"], pr["requester"]))
            pending = r
    return {"pos": pos, "revs": revs, "pending": pending, "tolerance": tol,
            "revisable": pr["status"] in REVISABLE}


def rev_queue(user):
    """Pending PO revisions whose current rung this user may sign."""
    if not user:
        return []
    conn = get_db()
    try:
        revs = [dict(r) for r in conn.execute(
            "SELECT r.id, r.pr_id, r.rev_no, r.po_no, r.reason, r.created_by, r.old_grand, "
            "r.new_grand, p.pr_no, p.title, p.requester, p.currency FROM pr_po_revisions r "
            "JOIN pr_requests p ON p.id=r.pr_id WHERE r.status='pending' ORDER BY r.id"
        ).fetchall()]
        if not revs:
            return []
        ph = ",".join("?" for _ in revs)
        steps = conn.execute(
            f"SELECT * FROM pr_po_rev_steps WHERE status='pending' AND rev_id IN ({ph}) "
            "ORDER BY seq", tuple(r["id"] for r in revs)).fetchall()
        roles = svc.stage_roles_map(conn)
    finally:
        conn.close()
    first = {}
    for s in steps:
        first.setdefault(s["rev_id"], s)
    uname = user.get("username")
    out = []
    for r in revs:
        s = first.get(r["id"])
        if s and uname not in (r["created_by"], r["requester"]) \
                and svc.can_act_step(user, s, _roles=roles):
            r["_stage"] = s["stage"]
            out.append(r)
    return out


# --------------------------------------------------------------------------
# (B) Bulk signing
# --------------------------------------------------------------------------
def bulk_sign(pr_ids, user, comment=None, ip=None):
    """Approve several requests, each through act_on_step exactly as if it had
    been signed on its own page. Returns [{id, pr_no, ok, code}] in the order
    given; duplicates and junk ids are dropped."""
    ids = []
    for raw in pr_ids or ():
        try:
            pid = int(raw)
        except (TypeError, ValueError):
            continue
        if pid not in ids:
            ids.append(pid)
    if not ids:
        return []
    conn = get_db()
    try:
        ph = ",".join("?" for _ in ids)
        nos = {r["id"]: r["pr_no"] for r in conn.execute(
            f"SELECT id, pr_no FROM pr_requests WHERE id IN ({ph})", tuple(ids)).fetchall()}
    finally:
        conn.close()
    out = []
    for pid in ids:
        if pid not in nos:
            out.append({"id": pid, "pr_no": "#%d" % pid, "ok": False, "code": "not_found"})
            continue
        ok, msg = svc.act_on_step(pid, user, "approve", comment=comment, ip=ip)
        out.append({"id": pid, "pr_no": nos[pid], "ok": ok, "code": msg})
    return out


# --------------------------------------------------------------------------
# (C) Copy a request  /  (D) low stock -> request
# --------------------------------------------------------------------------
_COPY_LINE_FIELDS = ("item", "description", "unit", "qty", "item_id", "spare_id")


def copy_prefill(pr_id):
    """(header, lines) for a new request copied from `pr_id`: what was asked
    for — never what it cost, who sells it, its quotes, files or signatures."""
    bundle = svc.get_pr(pr_id)
    if not bundle:
        return None, []
    pr = bundle["pr"]
    head = {k: pr.get(k) for k in ("title", "department", "cost_center", "priority")}
    lines = [{k: it.get(k) for k in _COPY_LINE_FIELDS} for it in bundle["items"]]
    return head, lines


def _open_lines(conn):
    ph = ",".join("?" for _ in OPEN_STATUSES)
    return conn.execute(
        "SELECT i.item, i.qty, i.received_qty, r.id AS pr_id, r.pr_no, r.status, "
        "pi.code AS cat_code FROM pr_items i JOIN pr_requests r ON r.id=i.pr_id "
        "LEFT JOIN proc_items pi ON pi.id=i.item_id "
        f"WHERE r.is_active=1 AND r.status IN ({ph})", OPEN_STATUSES).fetchall()


def _on_order(lines, code):
    """(quantity still to come, [open requests]) for one material code — a line
    matches on its typed code or on the code of the catalogue item it links."""
    key = (code or "").strip().lower()
    qty, prs = 0.0, {}
    for ln in lines:
        if key and key in ((ln["item"] or "").strip().lower(),
                           (ln["cat_code"] or "").strip().lower()):
            qty += max(_f(ln["qty"]) - _f(ln["received_qty"]), 0.0)
            prs[ln["pr_id"]] = {"id": ln["pr_id"], "pr_no": ln["pr_no"], "status": ln["status"]}
    return round(qty, 3), list(prs.values())


def _with_orders(rows, lines):
    for r in rows:
        r["on_order"], r["open_prs"] = _on_order(lines, r["code"])
        r["suggested"] = round(max(_f(r["reorder_level"]) - _f(r["available"])
                                   - r["on_order"], 0.0), 3)
    return rows


def low_stock():
    """The warehouse's short materials with what is already on order, the open
    requests for them and the quantity a new request should ask for."""
    try:
        from app.warehouse.services import list_materials
        rows = list_materials(low_only=True)
    except Exception:          # noqa: BLE001 — no warehouse module: nothing to show
        return []
    if not rows:
        return []
    conn = get_db()
    try:
        return _with_orders(rows, _open_lines(conn))
    finally:
        conn.close()


def stock_prefill(material_id):
    """(header, lines, open_requests) for a request raised from one material."""
    try:
        from app.warehouse.services import list_materials
        row = next((r for r in list_materials() if r["id"] == material_id), None)
    except Exception:          # noqa: BLE001
        row = None
    if row is None:
        return None, [], []
    conn = get_db()
    try:
        _with_orders([row], _open_lines(conn))
        cat = conn.execute("SELECT id FROM proc_items WHERE code=?", (row["code"],)).fetchone()
    finally:
        conn.close()
    head = {"title": ("%s — %s" % (row["code"], row.get("name") or "")).strip(" —")[:120]}
    line = {"item": row["code"], "description": row.get("name") or "",
            "unit": row.get("uom") if row.get("uom") in C.PR_UNITS else None,
            "qty": row["suggested"] or 1, "item_id": cat["id"] if cat else None}
    return head, [line], row["open_prs"]
