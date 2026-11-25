"""
Imports and framework agreements.

(A) IMPORT ORDERS. A request is an import when pr_requests.order_type is
'export' (the form's "Export / import" choice). Each of its purchase orders can
carry ONE shipment record (bill of lading / air waybill, forwarder, incoterm,
milestone dates) and any number of landed-cost CHARGES (freight, insurance,
duty, clearance, port/storage, other), each in its own currency with its rate to
EGP and the supporting document. The charges are allocated to the order's lines
by value (default) or by quantity, giving a landed unit cost per line. There is
no weight on a request line, so weight allocation is not offered.

Stock valuation does NOT change unless the admin switches
`landed_cost_to_stock` on: only then does post_stock_postings() value a receipt
at the landed unit cost (see stock_unit_cost). Charges recorded after the goods
were booked in do not revalue stock already received.

(B) FRAMEWORK AGREEMENTS. A supplier, a validity window, a currency, a ceiling
(value and/or quantity) and agreed unit prices per item. Purchasing or a
Procurement admin drafts one; a Procurement admin or Finance (proc_pay) who is
NOT the creator activates it. A request line drawn against an active agreement
takes the agreed price and supplier, and the competitive-quotation rule is
waived for THAT line only while the agreement is in date and inside its
ceiling (rfq_waiver, read by services.rfq_requirement). The approval ladder is
never touched.
"""
import logging
from datetime import date

from app.db import get_db
from app.security import user_has_permission
from app.approvals import constants as C
from app.approvals import services as svc

log = logging.getLogger(__name__)

CHARGE_KINDS = ("freight", "insurance", "duty", "clearance", "port", "other")
INCOTERMS = ("EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP")
MILESTONES = ("shipped_at", "port_at", "cleared_at", "delivered_at")
ALLOC_METHODS = ("value", "qty")
EXPIRY_WARN_DAYS = 30
# Requests whose drawn lines no longer hold any of an agreement's ceiling.
_RELEASED = ("cancelled", "rejected")
# A line can be drawn (or released) only while the request is still being decided.
_DRAWABLE = ("draft", "rejected", "pending")


def _today():
    return svc._now()[:10]


def _uname(user):
    return (user or {}).get("username") or "system"


def _admin(user):
    return (user or {}).get("role") == "super_admin" or user_has_permission(user, "proc_admin")


def can_record(user):
    """Purchasing records shipments and charges."""
    return _admin(user) or user_has_permission(user, "proc_purchasing")


def can_correct(user):
    """Finance (proc_pay) may also correct or void a charge."""
    return can_record(user) or user_has_permission(user, "proc_pay")


def is_import(pr):
    return svc._pr_field(pr, "order_type") == "export"


def _iso(raw):
    """'' for blank, the ISO date for a valid one, None for junk."""
    s = str(raw or "").strip()[:10]
    if not s:
        return ""
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _num(raw):
    """A positive finite number, 0 for blank, None for junk or negative."""
    v = svc._money(str(raw or "").strip())
    return v


def _norm(text):
    return " ".join(str(text or "").split()).casefold()


# ==========================================================================
# (A) shipments and landed cost
# ==========================================================================
def _pr(conn, pr_id):
    return conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()


def _po_nos(conn, pr):
    return [p["po_no"] for p in svc._pos_for(conn, pr)]


def save_shipment(pr_id, po_no, data, user, ip=None):
    """Create or update the shipment record of one import PO. Returns (ok, code)."""
    if not can_record(user):
        return False, "forbidden"
    conn = get_db()
    try:
        pr = _pr(conn, pr_id)
        if not pr:
            return False, "not_found"
        if not is_import(pr):
            return False, "not_import"
        if po_no not in _po_nos(conn, pr):
            return False, "no_po"
        vals = {"bl_no": str(data.get("bl_no") or "").strip()[:60],
                "carrier": str(data.get("carrier") or "").strip()[:120],
                "incoterm": str(data.get("incoterm") or "").strip().upper()[:3],
                "notes": str(data.get("notes") or "").strip()[:500],
                "alloc_method": data.get("alloc_method") or "value"}
        if vals["incoterm"] and vals["incoterm"] not in INCOTERMS:
            return False, "bad_incoterm"
        if vals["alloc_method"] not in ALLOC_METHODS:
            return False, "bad_method"
        last = ""
        for m in MILESTONES:
            d = _iso(data.get(m))
            if d is None:
                return False, "bad_date"
            if d and last and d < last:
                return False, "milestone_order"   # arrived before it shipped, etc.
            vals[m] = d or None
            last = d or last
        now, who = svc._now(), _uname(user)
        row = conn.execute("SELECT id FROM pr_shipments WHERE pr_id=? AND po_no=?",
                           (pr_id, po_no)).fetchone()
        cols = list(vals)
        if row:
            conn.execute("UPDATE pr_shipments SET " + ", ".join(c + "=?" for c in cols)
                         + ", updated_by=?, updated_at=? WHERE id=?",
                         [vals[c] for c in cols] + [who, now, row["id"]])
        else:
            conn.execute("INSERT INTO pr_shipments (pr_id, po_no, " + ", ".join(cols)
                         + ", created_by, created_at, updated_by, updated_at) VALUES ("
                         + ",".join("?" * (len(cols) + 6)) + ")",
                         [pr_id, po_no] + [vals[c] for c in cols] + [who, now, who, now])
        svc.audit(conn, pr_id, who, "shipment_updated",
                  "%s: BL/AWB %s, %s" % (po_no, vals["bl_no"] or "-", vals["incoterm"] or "-"), ip)
        conn.commit()
        return True, "saved"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _charge_money(data):
    """(amount, currency, fx, amount_egp) or (None, code)."""
    amount = _num(data.get("amount"))
    if amount is None or amount <= 0:
        return None, "bad_amount"
    cur = C.currency_code(data.get("currency") or "EGP")
    if not cur:
        return None, "bad_currency"
    if cur == "EGP":
        fx = 1.0
    else:
        fx = svc._valid_fx(data.get("fx_rate"))
        if not fx:
            return None, "fx_required"
    return (amount, cur, fx, round(amount * fx, 2)), ""


def add_charge(pr_id, po_no, data, user, filename=None, content_type=None,
               content_b64=None, ip=None):
    """Record one landed-cost charge on an import PO. Returns (ok, code)."""
    if not can_record(user):
        return False, "forbidden"
    kind = str(data.get("kind") or "").strip()
    if kind not in CHARGE_KINDS:
        return False, "bad_kind"
    money, err = _charge_money(data)
    if not money:
        return False, err
    amount, cur, fx, egp = money
    conn = get_db()
    try:
        pr = _pr(conn, pr_id)
        if not pr:
            return False, "not_found"
        if not is_import(pr):
            return False, "not_import"
        if po_no not in _po_nos(conn, pr):
            return False, "no_po"
        now, who = svc._now(), _uname(user)
        conn.execute(
            "INSERT INTO pr_landed_charges (pr_id, po_no, kind, amount, currency, fx_rate, "
            "amount_egp, reference, filename, content_type, content_b64, status, "
            "created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,?)",
            (pr_id, po_no, kind, amount, cur, fx, egp,
             str(data.get("reference") or "").strip()[:80] or None,
             filename, content_type, content_b64, who, now))
        svc.audit(conn, pr_id, who, "landed_charge",
                  "%s: %s %s %s (= %s EGP)" % (po_no, kind, f"{amount:,.2f}", cur, f"{egp:,.2f}"), ip)
        conn.commit()
        return True, "added"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_charge(charge_id):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM pr_landed_charges WHERE id=?", (charge_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def correct_charge(charge_id, data, user, ip=None):
    """Correct the money on a charge (Purchasing or Finance). The old figures go
    to the audit trail. Returns (ok, code, pr_id)."""
    if not can_correct(user):
        return False, "forbidden", None
    money, err = _charge_money(data)
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM pr_landed_charges WHERE id=?", (charge_id,)).fetchone()
        if not r:
            return False, "not_found", None
        if r["status"] != "active":
            return False, "void", r["pr_id"]
        if not money:
            return False, err, r["pr_id"]
        amount, cur, fx, egp = money
        who = _uname(user)
        conn.execute("UPDATE pr_landed_charges SET amount=?, currency=?, fx_rate=?, amount_egp=?, "
                     "updated_by=?, updated_at=? WHERE id=?",
                     (amount, cur, fx, egp, who, svc._now(), charge_id))
        svc.audit(conn, r["pr_id"], who, "landed_charge_corrected",
                  "%s %s: %s %s -> %s %s (= %s EGP)" % (
                      r["po_no"], r["kind"], f"{float(r['amount'] or 0):,.2f}", r["currency"],
                      f"{amount:,.2f}", cur, f"{egp:,.2f}"), ip)
        conn.commit()
        return True, "corrected", r["pr_id"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_charge(charge_id, reason, user, ip=None):
    """Void a charge (never deleted). Returns (ok, code, pr_id)."""
    if not can_correct(user):
        return False, "forbidden", None
    reason = str(reason or "").strip()[:300]
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM pr_landed_charges WHERE id=?", (charge_id,)).fetchone()
        if not r:
            return False, "not_found", None
        if r["status"] != "active":
            return False, "void", r["pr_id"]
        if not reason:
            return False, "reason_required", r["pr_id"]
        who = _uname(user)
        conn.execute("UPDATE pr_landed_charges SET status='void', void_reason=?, updated_by=?, "
                     "updated_at=? WHERE id=?", (reason, who, svc._now(), charge_id))
        svc.audit(conn, r["pr_id"], who, "landed_charge_void",
                  "%s %s %s %s voided: %s" % (r["po_no"], r["kind"],
                                              f"{float(r['amount'] or 0):,.2f}", r["currency"], reason), ip)
        conn.commit()
        return True, "voided", r["pr_id"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def allocate(lines, charges_egp, method="value", fx=1.0):
    """Spread `charges_egp` over `lines` (dicts with id, item, qty, unit_price,
    net_price) by line value or by quantity. Pure. The rounding residue goes to
    the last line so the shares add up to the charges exactly."""
    rows = []
    for ln in lines:
        qty = float(ln.get("qty") or 0)
        unit = svc.net_unit_price(ln) * fx
        rows.append({"id": ln.get("id"), "item": ln.get("item"), "unit": ln.get("unit"),
                     "qty": qty, "unit_egp": round(unit, 4), "value_egp": round(unit * qty, 2)})
    key = "value_egp" if method == "value" else "qty"
    base = sum(r[key] for r in rows)
    if base <= 0:                                  # nothing priced: fall back to quantity
        key, base = "qty", sum(r["qty"] for r in rows)
    total, given = round(float(charges_egp or 0), 2), 0.0
    for n, r in enumerate(rows, start=1):
        share = (round(total - given, 2) if n == len(rows)
                 else round(total * r[key] / base, 2) if base > 0 else 0.0)
        given += share
        r["alloc_egp"] = share
        r["landed_egp"] = round(r["value_egp"] + share, 2)
        r["landed_unit_egp"] = round(r["landed_egp"] / r["qty"], 4) if r["qty"] > 0 else None
    return rows


def landed_view(conn, pr, items=None):
    """One entry per purchase order of an import request: its shipment, its
    charges (void ones included, marked) and the per-line landed cost."""
    pr_id = svc._pr_field(pr, "id")
    pos = svc._pos_for(conn, pr)
    if not pos:
        return []
    if items is None:
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()]
    groups = {C.vendor_key(v): ls for v, ls in svc.po_groups(items, pr).items()}
    ships = {r["po_no"]: dict(r) for r in conn.execute(
        "SELECT * FROM pr_shipments WHERE pr_id=?", (pr_id,)).fetchall()}
    charges = {}
    for r in conn.execute("SELECT id, po_no, kind, amount, currency, fx_rate, amount_egp, reference, "
                          "filename, status, void_reason, created_by, created_at, updated_by, "
                          "updated_at FROM pr_landed_charges WHERE pr_id=? ORDER BY id",
                          (pr_id,)).fetchall():
        charges.setdefault(r["po_no"], []).append(dict(r))
    fx = svc._pr_fx(pr)
    out = []
    for p in pos:
        lines = items if len(pos) == 1 else groups.get(
            C.vendor_key(p.get("vendor") or svc._pr_field(pr, "vendor")), [])
        sh = ships.get(p["po_no"]) or {}
        method = sh.get("alloc_method") if sh.get("alloc_method") in ALLOC_METHODS else "value"
        ch = charges.get(p["po_no"], [])
        total = round(sum(float(c["amount_egp"] or 0) for c in ch if c["status"] == "active"), 2)
        rows = allocate(lines, total, method, fx)
        value = round(sum(r["value_egp"] for r in rows), 2)
        steps = [(m, sh.get(m)) for m in MILESTONES]
        out.append({"po": p, "shipment": sh, "milestones": steps,
                    "next_milestone": next((m for m, d in steps if not d), None),
                    "charges": ch, "charges_egp": total, "method": method, "lines": rows,
                    "po_value_egp": value, "landed_egp": round(value + total, 2),
                    "uplift_pct": round(total * 100.0 / value, 1) if value > 0 else None})
    return out


def stock_unit_cost(pr_id, item_id, price):
    """The EGP unit cost a receipt of this line enters stock at: `price` (what
    post_stock_postings already computed) unless the landed-cost setting is ON,
    the request is an import and its order carries charges. Never raises."""
    try:
        conn = get_db()
        try:
            if not svc.bool_setting(conn, "landed_cost_to_stock"):
                return price
            pr = _pr(conn, pr_id)
            if not pr or not is_import(pr):
                return price
            for po in landed_view(conn, pr):
                if po["charges_egp"] <= 0:
                    continue
                for r in po["lines"]:
                    if r["id"] == item_id and r["landed_unit_egp"]:
                        return r["landed_unit_egp"]
        finally:
            conn.close()
    except Exception:
        log.warning("landed cost unavailable for PR %s line %s", pr_id, item_id, exc_info=True)
    return price


# ==========================================================================
# (B) framework agreements
# ==========================================================================
def agreement_state(a, today=None):
    """draft | active | scheduled | expired | closed — 'scheduled' and 'expired'
    are an active agreement outside its dates, read from the dates, never stored."""
    st = svc._pr_field(a, "status") or "draft"
    if st != "active":
        return st
    today = today or _today()
    if (svc._pr_field(a, "valid_to") or "9999") < today:
        return "expired"
    if (svc._pr_field(a, "valid_from") or "") > today:
        return "scheduled"
    return "active"


def _drawn_rows(conn, agr_id):
    """Every request line still drawn against `agr_id`: carries the link, the
    agreed price, the agreement supplier and currency (a re-priced, re-sourced
    or re-currencied line has silently left the agreement)."""
    a = conn.execute("SELECT vendor, currency FROM proc_agreements WHERE id=?", (agr_id,)).fetchone()
    if not a:
        return []
    out = []
    for r in conn.execute(
            "SELECT i.id, i.pr_id, i.item, i.qty, i.unit_price, i.vendor, al.unit_price AS agreed, "
            "al.id AS line_id, r.pr_no, r.title, r.status, r.vendor AS pr_vendor, r.currency "
            "FROM pr_items i JOIN proc_agreement_lines al ON al.id=i.agreement_line_id "
            "JOIN pr_requests r ON r.id=i.pr_id WHERE al.agreement_id=? "
            "AND COALESCE(r.is_active,1)=1 ORDER BY i.pr_id, i.id", (agr_id,)).fetchall():
        d = dict(r)
        if d["status"] in _RELEASED:
            continue
        if abs(float(d["unit_price"] or 0) - float(d["agreed"] or 0)) > 0.005:
            continue
        if C.vendor_key(d["vendor"] or d["pr_vendor"]) != C.vendor_key(a["vendor"]):
            continue
        if (d["currency"] or "EGP").upper() != (a["currency"] or "EGP").upper():
            continue
        d["value"] = round(float(d["qty"] or 0) * float(d["agreed"] or 0), 2)
        out.append(d)
    return out


def consumption(conn, agr_id, exclude_item=None):
    rows = [r for r in _drawn_rows(conn, agr_id) if r["id"] != exclude_item]
    return {"value": round(sum(r["value"] for r in rows), 2),
            "qty": round(sum(float(r["qty"] or 0) for r in rows), 4), "rows": rows}


def _within_ceiling(a, used_value, used_qty):
    cv, cq = svc._pr_field(a, "ceiling_value"), svc._pr_field(a, "ceiling_qty")
    if cv and used_value > float(cv) + 0.005:
        return False
    if cq and used_qty > float(cq) + 1e-9:
        return False
    return True


def _decorate(conn, a, today=None):
    a = dict(a)
    use = consumption(conn, a["id"])
    a["state"] = agreement_state(a, today)
    a["used_value"], a["used_qty"], a["draws"] = use["value"], use["qty"], use["rows"]
    pcts = []
    if a.get("ceiling_value"):
        pcts.append(use["value"] * 100.0 / float(a["ceiling_value"]))
    if a.get("ceiling_qty"):
        pcts.append(use["qty"] * 100.0 / float(a["ceiling_qty"]))
    a["used_pct"] = round(max(pcts), 1) if pcts else 0.0
    t = date.fromisoformat(today or _today())
    try:
        left = (date.fromisoformat(a["valid_to"]) - t).days
    except (TypeError, ValueError):
        left = None
    a["days_left"] = left
    a["expiring"] = a["state"] == "active" and left is not None and left <= EXPIRY_WARN_DAYS
    return a


def list_agreements(vendor="", status=""):
    conn = get_db()
    try:
        sql, args = "SELECT * FROM proc_agreements", []
        if vendor:
            sql += " WHERE LOWER(TRIM(vendor))=?"
            args.append(_norm(vendor))
        rows = [_decorate(conn, r) for r in conn.execute(sql + " ORDER BY id DESC", args).fetchall()]
        return [r for r in rows if not status or r["state"] == status]
    finally:
        conn.close()


def get_agreement(agr_id):
    conn = get_db()
    try:
        a = conn.execute("SELECT * FROM proc_agreements WHERE id=?", (agr_id,)).fetchone()
        if not a:
            return None
        a = _decorate(conn, a)
        a["lines"] = [dict(r) for r in conn.execute(
            "SELECT * FROM proc_agreement_lines WHERE agreement_id=? ORDER BY id", (agr_id,)).fetchall()]
        return a
    finally:
        conn.close()


def _parse_lines(conn, lines):
    """[(item_id|None, item, unit, unit_price)] or (None, code). A line names a
    catalogue item by its code, or a free item by its name."""
    out = []
    for ln in lines or []:
        name = str(ln.get("item") or "").strip()[:200]
        code = str(ln.get("code") or "").strip()[:60]
        price_raw = str(ln.get("unit_price") or "").strip()
        if not (name or code or price_raw):
            continue                                   # an empty row on the form
        price = _num(price_raw)
        if price is None or price <= 0:
            return None, "bad_line"
        item_id, unit = None, str(ln.get("unit") or "").strip()[:20]
        if code:
            it = conn.execute("SELECT id, name, unit FROM proc_items WHERE LOWER(code)=LOWER(?)",
                              (code,)).fetchone()
            if not it:
                return None, "unknown_code"
            item_id, name, unit = it["id"], it["name"] or name, unit or it["unit"] or ""
        if not name:
            return None, "bad_line"
        out.append((item_id, name, unit or "Pcs", price))
    if not out:
        return None, "lines_required"
    return out, ""


def create_agreement(data, lines, user, ip=None):
    """Draft a framework agreement. Returns (ok, code_or_id)."""
    if not can_record(user):
        return False, "forbidden"
    vendor = str(data.get("vendor") or "").strip()
    vf, vt = _iso(data.get("valid_from")), _iso(data.get("valid_to"))
    if not vf or not vt or vt < vf:
        return False, "bad_dates"
    cur = C.currency_code(data.get("currency") or "EGP")
    if not cur:
        return False, "bad_currency"
    cv, cq = _num(data.get("ceiling_value")), _num(data.get("ceiling_qty"))
    if cv is None or cq is None:
        return False, "bad_ceiling"
    if not cv and not cq:
        return False, "ceiling_required"
    conn = get_db()
    try:
        if not svc.vendor_approved(conn, vendor):
            return False, "vendor_not_approved"
        parsed, err = _parse_lines(conn, lines)
        if not parsed:
            return False, err
        now, who = svc._now(), _uname(user)
        cur_ = conn.execute(
            "INSERT INTO proc_agreements (vendor, title, valid_from, valid_to, currency, "
            "ceiling_value, ceiling_qty, status, notes, created_by, created_at) "
            "VALUES (?,?,?,?,?,?,?,'draft',?,?,?)",
            (vendor, str(data.get("title") or "").strip()[:160] or None, vf, vt, cur,
             cv or None, cq or None, str(data.get("notes") or "").strip()[:500] or None, who, now))
        agr_id = cur_.lastrowid
        ref = svc.doc_no("FA", agr_id)
        conn.execute("UPDATE proc_agreements SET ref=? WHERE id=?", (ref, agr_id))
        for item_id, name, unit, price in parsed:
            conn.execute("INSERT INTO proc_agreement_lines (agreement_id, item_id, item, unit, "
                         "unit_price, created_at) VALUES (?,?,?,?,?,?)",
                         (agr_id, item_id, name, unit, price, now))
        svc._wf_audit(conn, who, "agreement_created", "%s %s (%s -> %s)" % (ref, vendor, vf, vt), ip)
        approvers = [u for u in svc._perm_holders(conn, ("proc_admin", "proc_pay")) if u != who]
        svc.notify_users(conn, approvers, "info", "Framework agreement to approve",
                         "%s with %s is waiting for approval." % (ref, vendor),
                         link="/procurement/agreements/%d" % agr_id)
        conn.commit()
        return True, agr_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def can_approve(user):
    return _admin(user) or user_has_permission(user, "proc_pay")


def approve_agreement(agr_id, user, ip=None):
    """Activate a draft. A Procurement admin or Finance, never its creator."""
    if not can_approve(user):
        return False, "forbidden"
    conn = get_db()
    try:
        a = conn.execute("SELECT * FROM proc_agreements WHERE id=?", (agr_id,)).fetchone()
        if not a:
            return False, "not_found"
        if a["status"] != "draft":
            return False, "not_draft"
        who = _uname(user)
        if a["created_by"] == who:
            return False, "self_approval"
        if not svc.vendor_approved(conn, a["vendor"]):
            return False, "vendor_not_approved"
        if (a["valid_to"] or "") < _today():
            return False, "bad_dates"
        conn.execute("UPDATE proc_agreements SET status='active', approved_by=?, approved_at=? "
                     "WHERE id=? AND status='draft'", (who, svc._now(), agr_id))
        svc._wf_audit(conn, who, "agreement_approved", a["ref"], ip)
        svc.notify_users(conn, [a["created_by"]], "success", "Framework agreement active",
                         "%s with %s is now active." % (a["ref"], a["vendor"]),
                         link="/procurement/agreements/%d" % agr_id)
        conn.commit()
        return True, "approved"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_agreement(agr_id, user, ip=None):
    """Close a draft or active agreement early. Lines already drawn keep their
    price; no further draw and no further quotation waiver."""
    if not (can_record(user) or can_approve(user)):
        return False, "forbidden"
    conn = get_db()
    try:
        a = conn.execute("SELECT * FROM proc_agreements WHERE id=?", (agr_id,)).fetchone()
        if not a:
            return False, "not_found"
        if a["status"] == "closed":
            return False, "closed"
        who = _uname(user)
        conn.execute("UPDATE proc_agreements SET status='closed', closed_by=?, closed_at=? WHERE id=?",
                     (who, svc._now(), agr_id))
        svc._wf_audit(conn, who, "agreement_closed", a["ref"], ip)
        conn.commit()
        return True, "closed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def approval_queue(user, limit=10):
    """Drafts this user may activate (for /my-work)."""
    if not can_approve(user):
        return []
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, ref, vendor, valid_to FROM proc_agreements WHERE status='draft' "
            "AND COALESCE(created_by,'')<>? ORDER BY id LIMIT ?",
            (_uname(user), limit)).fetchall()]
    finally:
        conn.close()


def _line_matches(al, item):
    if al["item_id"]:
        return al["item_id"] == svc._pr_field(item, "item_id")
    return not svc._pr_field(item, "item_id") and _norm(al["item"]) == _norm(svc._pr_field(item, "item"))


def drawable(conn, pr, items):
    """{pr_item_id: [usable agreement lines]} for the request page: active, in
    date, same currency as the request, same item."""
    cur = (svc._pr_field(pr, "currency") or "EGP").upper()
    today = _today()
    rows = conn.execute(
        "SELECT al.*, a.ref, a.vendor, a.currency, a.valid_to FROM proc_agreement_lines al "
        "JOIN proc_agreements a ON a.id=al.agreement_id WHERE a.status='active' "
        "AND a.valid_from<=? AND a.valid_to>=? AND UPPER(a.currency)=?",
        (today, today, cur)).fetchall()
    out = {}
    for it in items:
        hits = [dict(al) for al in rows if _line_matches(al, it)]
        if hits:
            out[svc._pr_field(it, "id")] = hits
    return out


def draw_line(pr_id, pr_item_id, agr_line_id, user, ip=None):
    """Price one request line from an agreement: agreed price + supplier, and
    the line is linked so it is consumed and waived from the quotation count.
    Priced requests go through svc.price_pr, so the value ladder is reconciled
    exactly as for any other price change. Returns (ok, code)."""
    if not can_record(user):
        return False, "forbidden"
    conn = get_db()
    try:
        pr = _pr(conn, pr_id)
        it = conn.execute("SELECT * FROM pr_items WHERE id=? AND pr_id=?", (pr_item_id, pr_id)).fetchone()
        if not pr or not it:
            return False, "not_found"
        if pr["status"] not in _DRAWABLE:
            return False, "locked"
        who = _uname(user)
        if pr["requester"] == who and not _admin(user):
            return False, "self_pricing"            # same rule as the pricing gate
        al = conn.execute("SELECT * FROM proc_agreement_lines WHERE id=?", (agr_line_id,)).fetchone()
        a = al and conn.execute("SELECT * FROM proc_agreements WHERE id=?",
                                (al["agreement_id"],)).fetchone()
        if not al or not a:
            return False, "not_found"
        state = agreement_state(a)
        if state != "active":
            return False, "agreement_" + ("expired" if state == "expired" else "inactive")
        if not _line_matches(al, it):
            return False, "agreement_item"
        if (a["currency"] or "EGP").upper() != (pr["currency"] or "EGP").upper():
            return False, "agreement_currency"
        if not svc.vendor_approved(conn, a["vendor"]):
            return False, "vendor_not_approved"
        use = consumption(conn, a["id"], exclude_item=pr_item_id)
        qty = float(it["qty"] or 0)
        if not _within_ceiling(a, use["value"] + qty * float(al["unit_price"]), use["qty"] + qty):
            return False, "agreement_ceiling"
        price, priced = float(al["unit_price"]), (pr["pricing_status"] or "priced") == "priced"
        if not priced:
            conn.execute("UPDATE pr_items SET unit_price=?, est_cost=?, vendor=?, quote_id=NULL, "
                         "agreement_line_id=? WHERE id=?",
                         (price, round(qty * price, 2), a["vendor"], al["id"], pr_item_id))
            svc.audit(conn, pr_id, who, "agreement_drawn",
                      "Line %s priced from %s" % (it["item"], a["ref"]), ip)
            conn.commit()
            return True, "drawn"
    finally:
        conn.close()
    ok, code = svc.price_pr(pr_id, {pr_item_id: price}, {}, user, ip=ip,
                            vendors={pr_item_id: a["vendor"]})
    if not ok:
        return False, code
    conn = get_db()
    try:
        conn.execute("UPDATE pr_items SET agreement_line_id=? WHERE id=?", (al["id"], pr_item_id))
        svc.audit(conn, pr_id, who, "agreement_drawn",
                  "Line %s priced from %s" % (it["item"], a["ref"]), ip)
        conn.commit()
        return True, "drawn"
    finally:
        conn.close()


def release_line(pr_id, pr_item_id, user, ip=None):
    """Unlink a line from its agreement (its price stays; the quotation rule
    applies to it again). Returns (ok, code)."""
    if not can_record(user):
        return False, "forbidden"
    conn = get_db()
    try:
        pr = _pr(conn, pr_id)
        it = conn.execute("SELECT id, item, agreement_line_id FROM pr_items WHERE id=? AND pr_id=?",
                          (pr_item_id, pr_id)).fetchone()
        if not pr or not it or not it["agreement_line_id"]:
            return False, "not_found"
        if pr["status"] not in _DRAWABLE:
            return False, "locked"
        conn.execute("UPDATE pr_items SET agreement_line_id=NULL WHERE id=?", (pr_item_id,))
        svc.audit(conn, pr_id, _uname(user), "agreement_released", "Line %s" % it["item"], ip)
        conn.commit()
        return True, "released"
    finally:
        conn.close()


def rfq_waiver(conn, pr):
    """The request lines exempt from the competitive-quotation count: drawn
    against an agreement that is active, in date and still inside its ceiling.
    {"lines": [ids], "egp": their EGP value, "all": every line is covered}.
    Fails closed (nothing waived) on any error."""
    out = {"lines": [], "egp": 0.0, "all": False}
    try:
        pr_id = svc._pr_field(pr, "id")
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        agr_ids = {r["agreement_id"] for r in conn.execute(
            "SELECT DISTINCT al.agreement_id FROM pr_items i JOIN proc_agreement_lines al "
            "ON al.id=i.agreement_line_id WHERE i.pr_id=?", (pr_id,)).fetchall()}
        if not agr_ids:
            return out
        fx = svc._pr_fx(pr)
        covered = []
        for aid in agr_ids:
            a = conn.execute("SELECT * FROM proc_agreements WHERE id=?", (aid,)).fetchone()
            if not a or agreement_state(a) != "active":
                continue
            use = consumption(conn, aid)
            if not _within_ceiling(a, use["value"], use["qty"]):
                continue
            covered += [r["id"] for r in use["rows"] if r["pr_id"] == pr_id]
        if not covered:
            return out
        out["lines"] = sorted(covered)
        out["egp"] = round(sum(svc._amount(dict(i)) for i in items if i["id"] in covered) * fx, 2)
        out["all"] = all(i["id"] in covered for i in items)
    except Exception:
        log.warning("agreement waiver unavailable", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"lines": [], "egp": 0.0, "all": False}
    return out


def agreements_xlsx(rows, money=True):
    """The agreements register as an Excel workbook (values only with money)."""
    from app.approvals import xlsx as X
    sh = X._Sheet("Agreements")
    sh.title("Framework agreements", _today())
    cols = [("Reference", None), ("Supplier", None), ("Title", None), ("Valid from", None),
            ("Valid to", None), ("Status", None), ("Currency", None)]
    if money:
        cols += [("Ceiling value", X.MONEY), ("Used value", X.MONEY)]
    cols += [("Ceiling qty", None), ("Used qty", None), ("Used %", "0.0")]
    out = []
    for a in rows:
        r = [a["ref"], a["vendor"], a.get("title") or "", X._date(a["valid_from"]),
             X._date(a["valid_to"]), a["state"], a["currency"]]
        if money:
            r += [a.get("ceiling_value"), a["used_value"]]
        r += [a.get("ceiling_qty"), a["used_qty"], a["used_pct"]]
        out.append(r)
    sh.table(cols, out, wrap=("Title",))
    return sh.save()


def detail_ctx(pr, bundle, user):
    """Everything the request-page partial needs, built once."""
    conn = get_db()
    try:
        items = [dict(i) for i in bundle["items"]]
        money_full = svc.money_view(user, pr, bundle["steps"]) == "full"
        ctx = {"is_import": is_import(pr), "can_record": can_record(user),
               "can_correct": can_correct(user), "money_full": money_full,
               "kinds": CHARGE_KINDS, "incoterms": INCOTERMS, "milestones": MILESTONES,
               "currencies": C.CURRENCIES, "pos": [], "draw": {}, "drawn": {},
               "stock_on": svc.bool_setting(conn, "landed_cost_to_stock")}
        if ctx["is_import"] and pr["status"] in ("po_issued", "in_transit", "partially_received",
                                                  "received", "closed"):
            ctx["pos"] = landed_view(conn, pr, items)
        # Agreement links on the lines, and what Purchasing could still draw.
        ids = [i["agreement_line_id"] for i in items if i.get("agreement_line_id")]
        if ids:
            for r in conn.execute(
                    "SELECT al.id, al.unit_price, a.id AS agreement_id, a.ref FROM proc_agreement_lines al "
                    "JOIN proc_agreements a ON a.id=al.agreement_id WHERE al.id IN (%s)"
                    % ",".join("?" * len(ids)), ids).fetchall():
                ctx["drawn"][r["id"]] = dict(r)
            waived = set(rfq_waiver(conn, pr)["lines"])
            ctx["drawn_lines"] = [dict(i, agr=ctx["drawn"].get(i["agreement_line_id"]),
                                       waived=i["id"] in waived)
                                  for i in items if i.get("agreement_line_id")]
        if ctx["can_record"] and pr["status"] in _DRAWABLE:
            ctx["draw"] = drawable(conn, pr, [i for i in items if not i.get("agreement_line_id")])
        ctx["line_by_id"] = {i["id"]: i for i in items}
        return ctx
    finally:
        conn.close()


def search(conn, fuzzy):
    """Global search hits: agreements by ref/supplier, shipments by BL/AWB."""
    hits = []
    try:
        for a in conn.execute("SELECT id, ref, vendor, title FROM proc_agreements ORDER BY id DESC LIMIT 200"):
            if fuzzy("%s %s %s" % (a["ref"], a["vendor"], a["title"] or "")):
                hits.append({"type": "agreement", "name": "%s · %s" % (a["ref"], a["vendor"]),
                             "sub": "Framework agreement", "url": "/procurement/agreements/%d" % a["id"]})
        for s in conn.execute("SELECT pr_id, po_no, bl_no FROM pr_shipments WHERE COALESCE(bl_no,'')<>'' "
                              "ORDER BY id DESC LIMIT 300"):
            if fuzzy("%s %s" % (s["bl_no"], s["po_no"])):
                hits.append({"type": "shipment", "name": "%s · %s" % (s["bl_no"], s["po_no"]),
                             "sub": "Import shipment", "url": "/procurement/pr/%d#imp-ship" % s["pr_id"]})
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    return hits
