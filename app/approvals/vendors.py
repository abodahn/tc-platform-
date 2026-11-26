# -*- coding: utf-8 -*-
"""
Vendor approval + master data, supplier documents, and the comments thread on
a request.

STATUS. A supplier is pending -> approved -> blocked. Only an APPROVED supplier
may be chosen, awarded a line, get a PO or an advance; a pending one may still
be sent RFQs and quote. A row from before the status column has NULL and reads
from is_active (approved / blocked), so nothing already in use breaks.

FOUR EYES. A buyer creates a supplier, Finance (proc_pay) or a procurement admin
approves it, and never the person who created it or sent it back for approval
(super_admin excepted). Changing a supplier's identity (name, tax ID, commercial
register no.), email or bank details sends an approved supplier back to pending:
that is the classic way to divert a payment. Whoever makes such a change may not
approve that supplier afterwards, whatever its status was.
"""
from datetime import date, timedelta

from app.db import get_db
from app.security import user_has_permission
from app.approvals import constants as C

STATUSES = ("pending", "approved", "blocked")
BANK_FIELDS = ("bank_name", "bank_account_name", "bank_account_no")
MASTER_FIELDS = BANK_FIELDS + ("cr_no", "cr_expiry", "tax_card_expiry")
DATE_FIELDS = ("cr_expiry", "tax_card_expiry")
# the fields whose change sends an approved supplier back for approval
SENSITIVE = ("name", "tax_id", "cr_no", "email") + BANK_FIELDS
EXPIRY = (("cr_expiry", "cr"), ("tax_card_expiry", "tax_card"))
WARN_DAYS = 30
DOC_TYPES = ("cr", "tax_card", "bank_letter", "other")
DOC_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".webp")
MAX_DOC = 3 * 1024 * 1024        # same ceiling as a quotation file
COMMENT_MAX = 1000


def _now():
    from app.approvals.services import _now as n
    return n()


def _uname(user):
    return (user or {}).get("username") or "system"


def _is_super(user):
    return (user or {}).get("role") == "super_admin"


def can_approve(user):
    """Finance or a procurement admin approves / unblocks a supplier."""
    return bool(user) and (_is_super(user) or user_has_permission(
        dict(user), ("proc_admin", "proc_pay")))


def can_block(user):
    return bool(user) and (_is_super(user) or user_has_permission(
        dict(user), ("proc_purchasing", "proc_admin", "proc_pay")))


def can_see_bank(user):
    """Bank details and supplier documents: the people who buy, pay or govern."""
    return can_block(user)


def status_of(row):
    """'pending' | 'approved' | 'blocked' for a proc_vendors row (dict or Row)."""
    if not row:
        return None
    row = dict(row)
    s = (row.get("status") or "").strip()
    if s in STATUSES:
        return s
    return "approved" if row.get("is_active") else "blocked"


def vendor_row(conn, name):
    """The master row for a supplier NAME (trimmed, case-folded), or None."""
    key = C.vendor_key(name)
    if not key:
        return None
    for r in conn.execute("SELECT * FROM proc_vendors").fetchall():
        if C.vendor_key(r["name"]) == key:
            return dict(r)
    return None


def vendor_state(conn, name):
    """Status of the named supplier, or None when it is not on the master."""
    return status_of(vendor_row(conn, name))


def award_refusal(conn, name):
    """Why this supplier may not be chosen / awarded: 'vendor_pending',
    'vendor_blocked' or None. A name not on the master is not refused here —
    issue_po() refuses it ('vendor_not_in_master'), as it always has."""
    s = vendor_state(conn, name)
    return {"pending": "vendor_pending", "blocked": "vendor_blocked"}.get(s)


def mask(v):
    """A bank number for the audit trail: the last 4 digits only."""
    v = str(v or "").strip()
    if not v:
        return "—"
    return "****" + v[-4:] if len(v) > 4 else "****"


def _show(field, v):
    return mask(v) if field == "bank_account_no" else (str(v).strip() if v else "—")


def _valid_date(v):
    v = str(v or "").strip()
    if not v:
        return None, True
    try:
        return date.fromisoformat(v[:10]).isoformat(), True
    except ValueError:
        return None, False


def master_values(data, old=None):
    """{column: value} for the new master fields that were SUBMITTED. A field
    the form did not post keeps its stored value, so a partial save can never
    blank bank details. Returns (values, error_code)."""
    out = {}
    for k in MASTER_FIELDS:
        if k not in data:
            continue
        v = str(data.get(k) or "").strip()[:120] or None
        if k in DATE_FIELDS:
            v, ok = _valid_date(v)
            if not ok:
                return None, "bad_date"
        out[k] = v
    return out, None


def sensitive_changes(old, new):
    """[(field, old_shown, new_shown)] for identity / email / bank fields that changed."""
    out = []
    for k in SENSITIVE:
        if k not in new:
            continue
        a, b = str(old.get(k) or "").strip(), str(new.get(k) or "").strip()
        if a != b:
            out.append((k, _show(k, a), _show(k, b)))
    return out


def doc_warnings(row, today=None):
    """[{'doc': 'cr'|'tax_card', 'state': 'expired'|'soon', 'date': ISO}]."""
    today = today or date.today()
    out = []
    row = dict(row or {})
    for col, doc in EXPIRY:
        d, ok = _valid_date(row.get(col))
        if not d:
            continue
        d = date.fromisoformat(d)
        if d < today:
            out.append({"doc": doc, "state": "expired", "date": d.isoformat()})
        elif d <= today + timedelta(days=WARN_DAYS):
            out.append({"doc": doc, "state": "soon", "date": d.isoformat()})
    return out


def _wf_audit(conn, actor, action, detail, ip=None):
    from app.approvals.services import audit
    audit(conn, None, actor or "system", action, detail, ip)


# --------------------------------------------------------------------------
# status transitions
# --------------------------------------------------------------------------
def approve_vendor(vendor_id, user, ip=None):
    """pending/blocked -> approved. Returns (ok, code)."""
    if not can_approve(user):
        return False, "vendor_approve_forbidden"
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM proc_vendors WHERE id=?", (vendor_id,)).fetchone()
        if not row:
            return False, "not_found"
        row = dict(row)
        if status_of(row) == "approved":
            return True, ""
        me = _uname(user)
        if not _is_super(user) and me in {row.get("created_by"), row.get("submitted_by")}:
            return False, "vendor_self_approve"
        was = status_of(row)
        conn.execute("UPDATE proc_vendors SET status='approved', is_active=1, status_by=?, "
                     "status_at=?, block_reason=NULL WHERE id=?", (me, _now(), vendor_id))
        _wf_audit(conn, me, "vendor_approved",
                  f"Vendor #{vendor_id} {row['name']}: {was} -> approved", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def block_vendor(vendor_id, reason, user, ip=None):
    """-> blocked, with a mandatory reason. Returns (ok, code)."""
    if not can_block(user):
        return False, "vendor_block_forbidden"
    reason = str(reason or "").strip()[:500]
    if not reason:
        return False, "block_reason_required"
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM proc_vendors WHERE id=?", (vendor_id,)).fetchone()
        if not row:
            return False, "not_found"
        was = status_of(row)
        me = _uname(user)
        conn.execute("UPDATE proc_vendors SET status='blocked', is_active=0, status_by=?, "
                     "status_at=?, block_reason=? WHERE id=?", (me, _now(), reason, vendor_id))
        _wf_audit(conn, me, "vendor_blocked",
                  f"Vendor #{vendor_id} {row['name']}: {was} -> blocked — {reason}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def back_to_pending(conn, vendor_id, old, changes, user, ip=None):
    """An approved supplier whose identity / email / bank details changed goes
    back for approval; the audit says old -> new with bank numbers masked. The
    editor is recorded as submitter on EVERY such change (pending or blocked
    too), so they can never approve / unblock the details they typed."""
    me = _uname(user)
    detail = "; ".join(f"{f}: {a} -> {b}" for f, a, b in changes)
    conn.execute("UPDATE proc_vendors SET submitted_by=? WHERE id=?", (me, vendor_id))
    if status_of(old) == "approved":
        conn.execute("UPDATE proc_vendors SET status='pending', status_by=?, "
                     "status_at=? WHERE id=?", (me, _now(), vendor_id))
        _wf_audit(conn, me, "vendor_resubmitted",
                  f"Vendor #{vendor_id} {old.get('name')}: approved -> pending ({detail})", ip)
    else:
        _wf_audit(conn, me, "vendor_details_changed",
                  f"Vendor #{vendor_id} {old.get('name')}: {detail}", ip)


# --------------------------------------------------------------------------
# documents
# --------------------------------------------------------------------------
def check_upload(filename, raw):
    """None when the file is acceptable, else a refusal code."""
    if not filename or not str(filename).lower().endswith(DOC_EXT):
        return "vendor_doc_type"
    if not raw:
        return "vendor_doc_empty"
    if len(raw) > MAX_DOC:
        return "vendor_doc_too_large"
    return None


def add_doc(vendor_id, doc_type, filename, content_type, content_b64, size, user, ip=None):
    if not can_see_bank(user):
        return False, "vendor_block_forbidden"
    doc_type = doc_type if doc_type in DOC_TYPES else "other"
    conn = get_db()
    try:
        row = conn.execute("SELECT name FROM proc_vendors WHERE id=?", (vendor_id,)).fetchone()
        if not row:
            return False, "not_found"
        conn.execute("INSERT INTO proc_vendor_docs (vendor_id, doc_type, filename, content_type, "
                     "size, content_b64, uploaded_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (vendor_id, doc_type, str(filename)[:200], content_type, int(size or 0),
                      content_b64, _uname(user), _now()))
        _wf_audit(conn, _uname(user), "vendor_doc_added",
                  f"Vendor #{vendor_id} {row['name']}: {doc_type} {str(filename)[:200]}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def get_doc(doc_id):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM proc_vendor_docs WHERE id=?", (doc_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _docs_by_vendor(conn):
    out = {}
    for r in conn.execute("SELECT id, vendor_id, doc_type, filename, size, uploaded_by, "
                          "created_at FROM proc_vendor_docs ORDER BY id").fetchall():
        out.setdefault(r["vendor_id"], []).append(dict(r))
    return out


# --------------------------------------------------------------------------
# screens
# --------------------------------------------------------------------------
def decorate(vendors, user, conn=None):
    """Adds vstatus, warnings and docs to each vendor dict; strips bank details
    for a viewer who may not see them."""
    own = conn is None
    conn = conn or get_db()
    try:
        docs = _docs_by_vendor(conn)
    finally:
        if own:
            conn.close()
    bank = can_see_bank(user)
    for v in vendors:
        v["vstatus"] = status_of(v)
        v["warnings"] = doc_warnings(v)
        v["docs"] = docs.get(v.get("id"), []) if bank else []
        if not bank:
            for k in BANK_FIELDS:
                v[k] = None
    return vendors


def page_context(vendors, args, user):
    """Filter + counts for the Vendors page."""
    vendors = decorate(vendors, user)
    counts = {s: sum(1 for v in vendors if v["vstatus"] == s) for s in STATUSES}
    counts["warn"] = sum(1 for v in vendors if v["warnings"])
    st = (args.get("status") or "").strip()
    q = (args.get("q") or "").strip().casefold()
    shown = [v for v in vendors
             if (not st or v["vstatus"] == st or (st == "warn" and v["warnings"]))
             and (not q or any(q in str(v.get(k) or "").casefold()
                               for k in ("name", "category", "email", "contact_person",
                                         "tax_id", "cr_no")))]
    return {"vendors": shown, "v_counts": counts, "v_total": len(vendors),
            "v_status": st if st in STATUSES + ("warn",) else "", "v_q": args.get("q") or "",
            "can_approve_vendor": can_approve(user), "can_block_vendor": can_block(user),
            "can_see_bank": can_see_bank(user), "me": _uname(user)}


def request_flags(bundle):
    """Suppliers on this request (header, lines, quotes, RFQs) that are not
    approved or whose documents are expired / expiring: [{name, status, warnings}]."""
    names = []
    pr = (bundle or {}).get("pr") or {}
    for src in ([pr], (bundle or {}).get("items") or [], (bundle or {}).get("quotes") or [],
                (bundle or {}).get("rfqs") or []):
        for r in src:
            n = str(dict(r).get("vendor") or "").strip()
            if n and C.vendor_key(n) not in {C.vendor_key(x) for x in names}:
                names.append(n)
    if not names:
        return []
    conn = get_db()
    try:
        rows = {C.vendor_key(r["name"]): dict(r)
                for r in conn.execute("SELECT * FROM proc_vendors").fetchall()}
    finally:
        conn.close()
    out = []
    for n in names:
        row = rows.get(C.vendor_key(n))
        if not row:
            continue          # off-master: the existing "not on the vendor master" hint covers it
        st, warn = status_of(row), doc_warnings(row)
        if st != "approved" or warn:
            out.append({"name": row["name"], "status": st, "warnings": warn,
                        "reason": row.get("block_reason") if st == "blocked" else None})
    return out


# --------------------------------------------------------------------------
# comments thread on a request
# --------------------------------------------------------------------------
def _current_steps(conn, pr):
    if (pr.get("status") or "") != "pending":
        return []
    return conn.execute("SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
                        (pr["id"], pr.get("current_seq"))).fetchall()


def _last_hands(conn, pr):
    """Who hears a requester's comment when no rung is waiting (rejected, sent
    back, approved, ordered...): the last signer(s) and the buyer who priced it;
    failing both, the buyers."""
    from app.approvals import services as svc
    rows = conn.execute("SELECT seq, approver_user FROM pr_steps WHERE pr_id=? "
                        "AND approver_user IS NOT NULL AND approver_user<>'' "
                        "ORDER BY seq DESC", (pr["id"],)).fetchall()
    out = {r["approver_user"] for r in rows if r["seq"] == rows[0]["seq"]} if rows else set()
    if pr.get("priced_by"):
        out.add(pr["priced_by"])
    return out or set(svc._perm_holders(conn, "proc_purchasing"))


def can_comment(conn, user, pr):
    """The requester, the request's signers (signed or eligible), buyers and admins."""
    from app.approvals import services as svc
    if not user:
        return False
    u = dict(user)
    me = u.get("username")
    if _is_super(u) or user_has_permission(u, ("proc_admin", "proc_purchasing")):
        return True
    if me and (me == pr.get("requester") or me in svc._requester_people(conn, pr)):
        return True
    steps = conn.execute("SELECT * FROM pr_steps WHERE pr_id=?", (pr["id"],)).fetchall()
    if any(s["approver_user"] == me for s in steps):
        return True
    return any(s["status"] == "pending" and svc.can_act_step(u, s) for s in steps)


def list_comments(pr_id, limit=200):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, author, body, created_at FROM pr_comments WHERE pr_id=? "
            "ORDER BY id DESC LIMIT ?", (pr_id, int(limit))).fetchall()]
    finally:
        conn.close()


def add_comment(pr_id, body, user, ip=None):
    """Post a comment. Returns (ok, code). Notifies: on hold -> whoever held it
    and the current-stage signers; anyone but the requester -> the requester;
    the requester (not on hold) -> the current-stage signers."""
    from app.approvals import services as svc
    body = str(body or "").strip()
    if not body:
        return False, "comment_required"
    if len(body) > COMMENT_MAX:
        return False, "comment_too_long"
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        pr = dict(pr)
        if not can_comment(conn, user, pr):
            return False, "comment_forbidden"
        me = _uname(user)
        conn.execute("INSERT INTO pr_comments (pr_id, author, body, created_at) VALUES (?,?,?,?)",
                     (pr_id, me, body, _now()))
        svc.audit(conn, pr_id, me, "comment", body[:300], ip)
        requester = svc._requester_people(conn, pr)
        signers = set()
        for st in _current_steps(conn, pr):
            signers |= svc._step_notify(conn, pr, st)
        held = (pr.get("hold_reason") or "").strip()
        told = set()
        if held:
            told |= {pr.get("held_by")} | signers
        if me not in requester:
            told |= requester
        elif not held:
            told |= signers or _last_hands(conn, pr)
        svc.notify_users(conn, told - {me, None}, "info",
                         "New comment on %s" % (pr.get("pr_no") or "a request"),
                         "%s: %s" % (me, body[:300]), link=svc._pr_link(pr_id))
        conn.commit()
        return True, ""
    finally:
        conn.close()


def comments_context(pr, user):
    """{"rows": newest first, 'can_post': bool, 'is_requester': bool}."""
    pr = dict(pr or {})
    if not pr.get("id"):
        return {"rows": [], "can_post": False, "is_requester": False}
    from app.approvals import services as svc
    me = (user or {}).get("username")
    conn = get_db()
    try:
        can = can_comment(conn, user, pr)
        mine = bool(me) and (me == pr.get("requester") or me in svc._requester_people(conn, pr))
    finally:
        conn.close()
    return {"rows": list_comments(pr["id"]), "can_post": can,
            "is_requester": mine,
            "max": COMMENT_MAX}
