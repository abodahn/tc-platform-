# -*- coding: utf-8 -*-
"""
TC Platform — delivery follow-up on issued purchase orders.

Two things the module could not do before:

1. A DATE THE GOODS ARE OWED BY. Each issued order (pr_purchase_orders row)
   carries `promised_date`: the issue date plus the supplier's quoted lead time
   (the awarded line quote, else the chosen quote, else Purchasing's own lead
   time on the line), falling back to the requester's "required by" date. The
   buyer may correct it. An order past that date, still owed goods and not
   cancelled is OVERDUE, and the buyers plus the requester are told once, then
   weekly while it stays late (overdue_notified_at is the dedupe stamp).

2. CANCEL ONE SUPPLIER'S ORDER on a split request, without touching the other
   suppliers' orders — only while nothing has been received, returned, invoiced
   or paid against it. Its lines stop being receivable, its value leaves the
   budget commitment (budget_released, the column close_pr already uses), and
   once every order is cancelled the request is cancelled exactly as cancel_pr
   leaves it.

Triggered with no cron: run_overdue_alerts() rides on the notification-feed
background pass every signed-in browser already drives (never on a page load,
since it may send email) — throttled here, per process.

Orders issued before promised_date existed are dated lazily (_backfill) from
their quote lead time and issue date the first time an open-orders read sees
them. Requests older still, with no pr_purchase_orders rows at all (their one
order synthesised from pr_requests.po_no), stay out of this follow-up.
"""
import time
from datetime import date, datetime, timedelta

from app.db import get_db
from app.approvals import constants as C
from app.approvals import services as svc

OPEN_STATUSES = ("po_issued", "in_transit", "partially_received")
RENOTIFY_DAYS = 7
_SWEEP_EVERY = 600          # seconds between overdue sweeps per process
_LAST = [float("-inf")]


def _d(value):
    """'YYYY-MM-DD...' -> date, else None."""
    try:
        return datetime.strptime(str(value or "").strip()[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _today():
    return _d(svc._now())


# --------------------------------------------------------------------------
# Required-by date on the request form
# --------------------------------------------------------------------------
def req_date_refusal(value, existing=None):
    """English refusal for a bad "required by" date, or None. Optional; a date
    already stored on the request may stay as it is even once it has passed."""
    v = (value or "").strip()
    if not v:
        return None
    d = _d(v)
    if not d or len(v) != 10:
        return "Enter the required-by date as a valid date."
    if existing and v == str(existing or "")[:10]:
        return None
    if d < _today():
        return "The required-by date cannot be in the past."
    return None


# --------------------------------------------------------------------------
# Promised date at PO issue
# --------------------------------------------------------------------------
def _lead_days(conn, pr, vendor, single):
    """Longest quoted lead time among this order's lines: the order is complete
    when its slowest line lands. None when nobody quoted one."""
    head = svc._pr_field(pr, "vendor")
    vk = C.vendor_key(vendor)
    best = None
    for r in conn.execute(
            "SELECT i.vendor, i.lead_time_days ld, q.lead_time_days qd FROM pr_items i "
            "LEFT JOIN pr_quotes q ON q.id = i.quote_id WHERE i.pr_id=?",
            (svc._pr_field(pr, "id"),)).fetchall():
        if not single and C.vendor_key(r["vendor"] or head) != vk:
            continue
        d = r["qd"] if r["qd"] is not None else r["ld"]
        if d is not None:
            best = max(best or 0.0, float(d))
    if best is None:
        for q in conn.execute(
                "SELECT vendor, lead_time_days d FROM pr_quotes WHERE pr_id=? AND is_chosen=1 "
                "AND lead_time_days IS NOT NULL ORDER BY id DESC",
                (svc._pr_field(pr, "id"),)).fetchall():
            if single or C.vendor_key(q["vendor"]) == vk:
                best = float(q["d"])
                break
    return best


def stamp_promised(conn, pr, pos):
    """Write promised_date on freshly issued order rows (inside issue_po's
    transaction). Rows that already carry one are left alone."""
    single = len(pos) == 1
    for p in pos:
        if not p.get("id") or p.get("promised_date"):
            continue
        days = _lead_days(conn, pr, p.get("vendor"), single)
        base = _d(p.get("issued_at")) or _today()
        due = base + timedelta(days=days) if days is not None \
            else _d(svc._pr_field(pr, "req_del_date"))
        if due:
            promised = due.isoformat()
            conn.execute("UPDATE pr_purchase_orders SET promised_date=? WHERE id=?",
                         (promised, p["id"]))
            p["promised_date"] = promised


# --------------------------------------------------------------------------
# Which lines belong to which order
# --------------------------------------------------------------------------
def _lines_of(items, pr, po, single):
    if single:
        return list(items)
    head = svc._pr_field(pr, "vendor")
    vk = C.vendor_key(po.get("vendor"))
    return [i for i in items if C.vendor_key(i["vendor"] or head) == vk]


def cancelled_item_ids(conn, pr_id):
    """Lines that belong only to a cancelled order of this request."""
    try:
        pos = [dict(r) for r in conn.execute(
            "SELECT vendor, status FROM pr_purchase_orders WHERE pr_id=?", (pr_id,)).fetchall()]
    except Exception:
        conn.rollback()
        return set()
    dead = {C.vendor_key(p["vendor"]) for p in pos if p["status"] == "cancelled"}
    dead -= {C.vendor_key(p["vendor"]) for p in pos if p["status"] != "cancelled"}
    if not dead:
        return set()
    head = conn.execute("SELECT vendor FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    head = head["vendor"] if head else None
    return {r["id"] for r in conn.execute(
        "SELECT id, vendor FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        if C.vendor_key(r["vendor"] or head) in dead}


def _outstanding(lines):
    return any(float(i["qty"] or 0) > 0 and
               round(float(i["received_qty"] or 0), 6) < round(float(i["qty"] or 0), 6)
               for i in lines)


# --------------------------------------------------------------------------
# Open orders and lateness
# --------------------------------------------------------------------------
def _backfill(conn, pr_id=None):
    """Date open orders issued before promised_date existed. stamp_promised only
    writes NULL rows, so a buyer's own date is never touched.
    shortcut: an order with no lead time and no required-by date is re-tried on
    every read; mark it as tried if that ever shows up as slow."""
    sql = ("SELECT DISTINCT o.pr_id FROM pr_purchase_orders o JOIN pr_requests p "
           "ON p.id = o.pr_id WHERE p.is_active=1 AND p.status IN "
           "('po_issued','in_transit','partially_received') AND o.promised_date IS NULL "
           "AND COALESCE(o.status,'issued') <> 'cancelled'")
    args = ()
    if pr_id is not None:
        sql += " AND o.pr_id=?"
        args = (pr_id,)
    try:
        todo = [r["pr_id"] for r in conn.execute(sql, args).fetchall()]
        for pid in todo:
            pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pid,)).fetchone()
            stamp_promised(conn, pr, [dict(r) for r in conn.execute(
                "SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", (pid,)).fetchall()])
        if todo:
            conn.commit()
    except Exception:
        conn.rollback()


def open_orders(conn, pr_id=None):
    """Every issued order still owed goods: request open, order not cancelled,
    at least one of its lines short. Late ones first, then by promised date."""
    _backfill(conn, pr_id)
    sql = ("SELECT o.*, p.pr_no, p.title, p.requester, p.status AS pr_status, "
           "p.vendor AS head_vendor, p.source_module, p.source_ref "
           "FROM pr_purchase_orders o JOIN pr_requests p ON p.id = o.pr_id "
           "WHERE p.is_active=1 AND p.status IN ('po_issued','in_transit','partially_received') "
           "AND COALESCE(o.status,'issued') <> 'cancelled'")
    args = ()
    if pr_id is not None:
        sql += " AND o.pr_id=?"
        args = (pr_id,)
    try:
        pos = [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception:
        conn.rollback()
        return []
    if not pos:
        return []
    ids = sorted({p["pr_id"] for p in pos})
    items, counts = {}, {}
    for r in conn.execute(
            "SELECT id, pr_id, vendor, qty, received_qty FROM pr_items WHERE pr_id IN (%s)"
            % ",".join("?" * len(ids)), ids).fetchall():
        items.setdefault(r["pr_id"], []).append(r)
    for r in conn.execute(
            "SELECT pr_id, COUNT(*) n FROM pr_purchase_orders WHERE pr_id IN (%s) GROUP BY pr_id"
            % ",".join("?" * len(ids)), ids).fetchall():
        counts[r["pr_id"]] = r["n"]
    today = _today()
    out = []
    for p in pos:
        pr = {"id": p["pr_id"], "vendor": p["head_vendor"]}
        if not _outstanding(_lines_of(items.get(p["pr_id"], []), pr, p,
                                      counts.get(p["pr_id"], 1) == 1)):
            continue
        due = _d(p.get("promised_date"))
        p["days_late"] = (today - due).days if due and due < today else 0
        p["days_left"] = (due - today).days if due and due >= today else None
        out.append(p)
    out.sort(key=lambda p: (-p["days_late"], p.get("promised_date") or "9999", p["id"]))
    return out


def late_map(conn=None):
    """{pr_id: {"days": worst days late, "promised": earliest open promise}}."""
    own = conn is None
    conn = conn or get_db()
    try:
        out = {}
        for p in open_orders(conn):
            m = out.setdefault(p["pr_id"], {"days": 0, "promised": None})
            m["days"] = max(m["days"], p["days_late"])
            if p.get("promised_date") and (not m["promised"] or p["promised_date"] < m["promised"]):
                m["promised"] = p["promised_date"]
        return out
    finally:
        if own:
            conn.close()


def recv_queue(conn, limit=10):
    """/my-work's "orders awaiting delivery": overdue first, then soonest due,
    then oldest — each with its earliest open promised date."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, pr_no, title, status, po_no FROM pr_requests WHERE is_active=1 AND status "
        "IN ('po_issued','in_transit','partially_received') ORDER BY id ASC").fetchall()]
    late = late_map(conn)
    for r in rows:
        m = late.get(r["id"]) or {}
        r["days_late"], r["promised"] = m.get("days", 0), m.get("promised")
    rows.sort(key=lambda r: (-r["days_late"], r["promised"] or "9999", r["id"]))
    return rows[:limit]


def po_states(pr_id, is_admin=False):
    """Per-order delivery state for the request page. is_admin: the user may
    also cancel the LAST live order (which cancels the request)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return []
        pos = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
        if not pos:
            return []
        items = conn.execute("SELECT id, vendor, qty, received_qty FROM pr_items WHERE pr_id=?",
                             (pr_id,)).fetchall()
        live = {p["id"]: p for p in open_orders(conn, pr_id)}
        single = len(pos) == 1
        n_live = sum(1 for p in pos if p.get("status") != "cancelled")
        for p in pos:
            lines = _lines_of(items, pr, p, single)
            p["cancelled"] = p.get("status") == "cancelled"
            p["delivered"] = not p["cancelled"] and not _outstanding(lines)
            p["days_late"] = live.get(p["id"], {}).get("days_late", 0)
            p["can_cancel"] = (not single and not p["cancelled"]
                               and (is_admin or n_live > 1)
                               and _cancel_refusal(conn, pr, p, lines) is None)
        return pos
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Overdue alerts
# --------------------------------------------------------------------------
def run_overdue_alerts():
    """Tell the buyers and the requester about every overdue order: once when it
    turns late, again every RENOTIFY_DAYS while it stays late. Returns the count."""
    conn = get_db()
    n = 0
    try:
        today = _today()
        buyers = None
        for p in open_orders(conn):
            if p["days_late"] <= 0:
                continue
            last = _d(p.get("overdue_notified_at"))
            if last and (today - last).days < RENOTIFY_DAYS:
                continue
            # Compare-and-set: two workers sweeping at once alert once.
            cur = conn.execute(
                "UPDATE pr_purchase_orders SET overdue_notified_at=? WHERE id=? "
                "AND COALESCE(overdue_notified_at,'')=?",
                (svc._now(), p["id"], p.get("overdue_notified_at") or ""))
            if cur.rowcount != 1:
                continue
            if buyers is None:
                buyers = set(svc._perm_holders(conn, "proc_purchasing"))
            pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (p["pr_id"],)).fetchone()
            vendor = p.get("vendor") or p.get("head_vendor") or "—"
            svc.audit(conn, p["pr_id"], "system", "po_overdue",
                      f"{p['po_no']} ({vendor}) overdue {p['days_late']} day(s) — "
                      f"promised {p['promised_date']}")
            svc.notify_users(conn, buyers | svc._requester_people(conn, pr), "warning",
                             "Delivery overdue",
                             f"{p['po_no']} from {vendor} for {p['pr_no']} is "
                             f"{p['days_late']} day(s) late (promised {p['promised_date']}).",
                             link=svc._pr_link(p["pr_id"]))
            n += 1
        conn.commit()
    finally:
        conn.close()
    return n


def maybe_run_overdue_alerts():
    """Throttled entry point for page loads and the notification poller."""
    now = time.monotonic()
    if now - _LAST[0] < _SWEEP_EVERY:
        return 0
    _LAST[0] = now
    try:
        return run_overdue_alerts()
    except Exception:                                        # noqa: BLE001
        return 0


# --------------------------------------------------------------------------
# Buyer edits the promised date
# --------------------------------------------------------------------------
def set_promised(po_id, value, user, ip=None):
    d = _d(value)
    if not d or len(str(value or "").strip()) != 10:
        return False, "bad_date", None
    conn = get_db()
    try:
        po = conn.execute("SELECT o.*, p.status AS pr_status FROM pr_purchase_orders o "
                          "JOIN pr_requests p ON p.id=o.pr_id WHERE o.id=?", (po_id,)).fetchone()
        if not po:
            return False, "not_found", None
        if po["status"] == "cancelled":
            return False, "po_cancelled", po["pr_id"]
        if po["pr_status"] not in OPEN_STATUSES:
            return False, "not_open", po["pr_id"]
        old = po["promised_date"]
        new = d.isoformat()
        if old == new:
            return True, "same", po["pr_id"]
        # A new promise re-arms the reminder: the next lapse is news again.
        conn.execute("UPDATE pr_purchase_orders SET promised_date=?, overdue_notified_at=NULL "
                     "WHERE id=?", (new, po_id))
        svc.audit(conn, po["pr_id"], (user or {}).get("username"), "po_promised",
                  f"{po['po_no']}: promised delivery date {old or '—'} -> {new}", ip)
        conn.commit()
        return True, "saved", po["pr_id"]
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Cancel one supplier's order
# --------------------------------------------------------------------------
def _cancel_refusal(conn, pr, po, lines):
    if po.get("status") == "cancelled":
        return "already_cancelled"
    if pr["status"] not in OPEN_STATUSES:
        return "not_cancellable"
    if any(float(i["received_qty"] or 0) > 0 for i in lines):
        return "already_received"
    vk = C.vendor_key(po.get("vendor") or pr["vendor"])
    if any(C.vendor_key(r["vendor"] or pr["vendor"]) == vk for r in conn.execute(
            "SELECT vendor FROM pr_returns WHERE pr_id=?", (pr["id"],)).fetchall()):
        return "already_received"     # rejected at the door: a debit note hangs on it
    # Net of reversals, the way cancel_pr reads payments. Money booked against
    # no order (an admin override) could be this supplier's, so it blocks too.
    for tbl, amt in (("pr_payments", "COALESCE(amount,0)"),
                     ("pr_invoices", "COALESCE(amount,0)+COALESCE(tax,0)")):
        row = conn.execute(f"SELECT COALESCE(SUM({amt}),0) s FROM {tbl} "
                           "WHERE pr_id=? AND (po_id=? OR po_id IS NULL)",
                           (pr["id"], po["id"])).fetchone()
        if abs(float(row["s"] or 0)) > 0.005:
            return "has_payments"
    return None


def cancel_po(po_id, reason, user, ip=None, email_vendor=True, is_admin=False):
    """Cancel ONE supplier's order on a split request. Returns (ok, code, pr_id);
    on success code is cancelled / emailed / no_smtp / no_email / send_failed.
    Cancelling the LAST live order cancels the request, which cancel_pr keeps
    for an admin once a PO is out — so it needs is_admin here too."""
    reason = (reason or "").strip()
    conn = get_db()
    try:
        po = conn.execute("SELECT * FROM pr_purchase_orders WHERE id=?", (po_id,)).fetchone()
        if not po:
            return False, "not_found", None
        po = dict(po)
        pr_id = po["pr_id"]
        if not reason:
            return False, "reason_required", pr_id
        # Same lock cancel_pr takes before it reads payments.
        conn.execute("UPDATE pr_requests SET paid_amount=paid_amount WHERE id=?", (pr_id,))
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        pos = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
        if len(pos) < 2:
            return False, "not_split", pr_id      # one order IS the request: cancel_pr, admin-gated
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        lines = _lines_of(items, pr, po, False)
        bad = _cancel_refusal(conn, pr, po, lines)
        if bad:
            return False, bad, pr_id
        remaining = [p for p in pos if p["id"] != po_id and p.get("status") != "cancelled"]
        if not remaining and not is_admin:
            return False, "needs_admin", pr_id
        uname = (user or {}).get("username")
        now = svc._now()
        if conn.execute("UPDATE pr_purchase_orders SET status='cancelled', cancelled_at=?, "
                        "cancelled_by=?, cancel_reason=? WHERE id=? AND COALESCE(status,'issued')"
                        "<>'cancelled'", (now, uname, reason[:500], po_id)).rowcount != 1:
            return False, "already_cancelled", pr_id
        vendor = po.get("vendor") or pr["vendor"] or "—"
        released = round(sum(float(i["qty"] or 0) * svc.net_unit_price(i) for i in lines), 2)
        if not remaining:
            # The last order gone: the request ends exactly as cancel_pr leaves it.
            conn.execute("UPDATE pr_requests SET status='cancelled', current_seq=0 WHERE id=?",
                         (pr_id,))
            detail = f"{po['po_no']} ({vendor}) cancelled — every order is now cancelled, so the request is cancelled. Reason: {reason[:300]}"
        else:
            conn.execute("UPDATE pr_requests SET budget_released=COALESCE(budget_released,0)+? "
                         "WHERE id=?", (released, pr_id))
            dead = cancelled_item_ids(conn, pr_id)
            if pr["status"] == "partially_received" and not _outstanding(
                    [i for i in items if i["id"] not in dead]):
                conn.execute("UPDATE pr_requests SET status='received', received_at=? WHERE id=?",
                             (now, pr_id))
            detail = (f"{po['po_no']} ({vendor}) cancelled; budget released {released:,.2f}. "
                      f"Reason: {reason[:300]}")
        svc.audit(conn, pr_id, uname, "po_cancelled", detail, ip)
        told = (svc._requester_people(conn, pr) | set(svc._perm_holders(conn, "proc_purchasing"))
                | set(svc._perm_holders(conn, "proc_receive")))
        svc.notify_users(conn, told - {uname}, "warning", "Purchase order cancelled",
                         f"{po['po_no']} from {vendor} on {pr['pr_no']} was cancelled by {uname}"
                         f" — do not receive goods against it. Reason: {reason[:300]}",
                         link=svc._pr_link(pr_id))
        email_to = None
        if email_vendor and pr["po_sent_at"]:
            v = conn.execute("SELECT email FROM proc_vendors WHERE name=?",
                             (po.get("vendor"),)).fetchone()
            email_to = (v["email"] if v else None) or ""
        conn.commit()
    finally:
        conn.close()
    if email_to is None:
        return True, "cancelled", pr_id
    if not email_to:
        return True, "no_email", pr_id
    from config import Config
    if not getattr(Config, "SMTP_HOST", ""):
        return True, "no_smtp", pr_id
    from app.services.alerts import send_email_to
    body = (f"Dear {vendor},\n\n"
            f"Please note that Purchase Order {po['po_no']} (ref {pr['pr_no']}) from "
            f"T&C Garments is CANCELLED. Please do not deliver or invoice against it.\n\n"
            f"يرجى العلم بأن أمر الشراء رقم {po['po_no']} قد تم إلغاؤه. "
            f"برجاء عدم التوريد أو إصدار فاتورة عليه.\n\n"
            f"Regards,\nT&C Garments — Purchasing")
    ok = send_email_to([email_to], f"CANCELLED: Purchase Order {po['po_no']} — T&C Garments", body)
    conn = get_db()
    try:
        svc.audit(conn, pr_id, uname, "po_cancel_emailed",
                  f"{po['po_no']} cancellation -> {email_to}" + ("" if ok else " (not sent)"), ip)
        conn.commit()
    finally:
        conn.close()
    return True, ("emailed" if ok else "send_failed"), pr_id
