"""
Maintenance ⇄ Procurement bridge — closes the spare-parts replenishment loop.

Forward:  when a spare part's AVAILABLE stock (on-hand minus reserved) falls to
          or below its reorder level, automatically raise a Procurement PR
          (unpriced → lands in Purchasing's pricing gate, then the normal
          approval ladder). One open PR per spare, ever — deduplicated against
          pr_requests.source_ref, so repeated checks never spam Procurement.

Reverse:  Procurement posts spare receipts through its per-line stock outbox;
          this bridge notifies the originating ticket when parts arrive.

Both directions are wrapped defensively: a bridge failure must never block a
stock movement or a goods receipt. Toggle with mnt_settings key
'auto_reorder_pr' ('1' on / '0' off; default on).
"""
from __future__ import annotations

import logging
import time

from app.db import get_db

log = logging.getLogger("tc.maint.bridge")

# PR statuses that still count as "open" for dedup purposes. Anything past
# receiving (or dead) frees the spare for a fresh auto-PR.
_CLOSED_STATUSES = ("rejected", "cancelled", "closed", "received")

# Full sweeps run from page loads (sync_stock_alerts); throttle them so a busy
# dashboard doesn't re-scan on every request. Targeted checks (spare_ids after
# a stock movement) are never throttled.
_SWEEP_INTERVAL = 180  # seconds
_LAST_SWEEP = [0.0]


def _enabled(conn) -> bool:
    row = conn.execute(
        "SELECT value FROM mnt_settings WHERE key='auto_reorder_pr'").fetchone()
    return (row["value"] if row else "1") != "0"


def _open_bridge_refs(conn) -> set:
    """The set of spares that ALREADY have an open replenishment order, keyed
    'spare:<id>'. Covers BOTH bridge-tagged PRs (header source_ref) AND any other
    open PR — manual or ticket-raised — whose lines carry that spare_id. Using only
    the header ref let a manual/ticket PR restocking spare X slip past the dedup, so
    the bridge raised a duplicate auto-PR (double order + double receipt into stock).
    Mirrors the spare_profile 'on order' query. One query per side."""
    ph = ",".join("?" for _ in _CLOSED_STATUSES)
    refs = set()
    for r in conn.execute(
            f"SELECT source_ref FROM pr_requests WHERE source_module='maintenance' "
            f"AND is_active=1 AND status NOT IN ({ph})", _CLOSED_STATUSES).fetchall():
        if r["source_ref"]:
            refs.add(r["source_ref"])
    for r in conn.execute(
            f"SELECT DISTINCT i.spare_id FROM pr_items i JOIN pr_requests p ON p.id=i.pr_id "
            f"WHERE i.spare_id IS NOT NULL AND p.is_active=1 AND p.status NOT IN ({ph})",
            _CLOSED_STATUSES).fetchall():
        if r["spare_id"]:
            refs.add(f"spare:{r['spare_id']}")
    return refs


def _suggested_qty(spare) -> float:
    """Refill to max level; fall back to twice the reorder level, then 10."""
    stock = float(spare["stock_qty"] or 0)
    target = float(spare["max_level"] or 0) or (float(spare["reorder_level"] or 0) * 2) or 10
    return max(1.0, round(target - stock, 2))


def auto_reorder_check(spare_ids=None, conn=None) -> list:
    """Raise auto-PRs for spares at/below reorder level. Returns [(spare_id, pr_no)].

    `spare_ids` limits the sweep (call after a specific stock movement); None
    sweeps every active spare (called from sync_stock_alerts, throttled to one
    scan per _SWEEP_INTERVAL per process). Idempotent."""
    if spare_ids is None:
        now = time.time()
        if now - _LAST_SWEEP[0] < _SWEEP_INTERVAL:
            return []
        _LAST_SWEEP[0] = now
    own = conn is None
    if own:
        conn = get_db()
    created = []
    try:
        if not _enabled(conn):
            return []
        q = ("SELECT * FROM mnt_spare_parts WHERE is_active=1 AND reorder_level > 0 "
             "AND (stock_qty - reserved_qty) <= reorder_level")
        args = ()
        if spare_ids:
            q += " AND id IN (%s)" % ",".join("?" for _ in spare_ids)
            args = tuple(spare_ids)
        spares = conn.execute(q, args).fetchall()
        if not spares:
            return []
        open_refs = _open_bridge_refs(conn)   # one dedup query for the whole batch
        for s in spares:
            try:
                if f"spare:{s['id']}" in open_refs:
                    continue  # one open replenishment PR per spare
                created.append((s["id"], _raise_pr(conn, s)))
            except Exception:
                log.warning("auto-reorder PR failed for spare %s", s["id"], exc_info=True)
        return created
    finally:
        if own:
            conn.close()


def _raise_pr(conn, spare, qty=None, ticket_id=None) -> str:
    """Create + submit the unpriced PR and tag it back to the spare."""
    from app.approvals import services as proc  # lazy: avoid import cycles

    qty = max(_suggested_qty(spare), qty or 0)
    available = float(spare["stock_qty"] or 0) - float(spare["reserved_qty"] or 0)
    header = {
        "title": f"Auto reorder — {spare['name'] or spare['code']}",
        "request_for": "Maintenance spare-part replenishment",
        "department": "Maintenance",
        "vendor": spare["vendor"] or None,
        "notes": (f"Raised automatically: {spare['code']} available {available:g} "
                  f"{spare['uom'] or 'pcs'} at/below reorder level "
                  f"{float(spare['reorder_level'] or 0):g}."),
    }
    if ticket_id is not None:
        header["notes"] = f"Raised for ticket #{ticket_id} out-of-stock spare request: {spare['code']}."
    items = [{
        "item": f"{spare['code']} — {spare['name'] or ''}".strip(" —"),
        "description": spare["spec"] or spare["description"] or "",
        "unit": spare["uom"] or "Pcs",
        "qty": qty,
        "current_stock": float(spare["stock_qty"] or 0),
        "vendor": spare["vendor"] or None,
        # Link the line to the spare INSIDE create_pr (atomic with the insert), so the
        # dedup sees it the instant the PR commits — this closes the create-then-tag
        # race where a second sweep raised a duplicate PR in the untagged window.
        "spare_id": spare["id"],
        # No price on purpose: pricing_status='unpriced' routes the PR through
        # Purchasing's pricing gate (requester price lockout stays intact).
        "unit_price": 0,
    }]
    system_user = {"username": "auto-reorder", "full_name": "Auto Reorder (Maintenance)", "id": None}
    pr_id, pr_no = proc.create_pr(header, items, system_user, submit=True, priced=False)

    # Tag the PR so the goods receipt can find its way back to this spare —
    # both at header level (dedup key) and on the line (line-level posting).
    conn.execute("UPDATE pr_requests SET source_module='maintenance', source_ref=? WHERE id=?",
                 (f"ticket:{ticket_id}" if ticket_id is not None else f"spare:{spare['id']}", pr_id))
    conn.execute("UPDATE pr_items SET spare_id=? WHERE pr_id=?", (spare["id"], pr_id))
    # Tell the maintenance side (role inbox + platform bell via notify()).
    from app.maintenance.services import audit as mnt_audit, notify as mnt_notify
    message = (f"{spare['code']} raised for ticket #{ticket_id} out-of-stock spare request — "
               if ticket_id is not None else f"{spare['code']} reached its reorder level — ")
    mnt_notify(conn, "storekeeper", f"Auto reorder PR raised: {spare['name']}",
               message + f"{pr_no} created for {qty:g} {spare['uom'] or 'pcs'} and sent to Purchasing for pricing.",
               "spare", spare["id"], "info", f"/procurement/pr/{pr_id}")
    if ticket_id is not None:
        mnt_audit(conn, {"username": "auto-reorder"}, "replenishment_pr", "ticket", ticket_id, comment=pr_no)
    mnt_audit(conn, {"username": "auto-reorder"}, "auto_reorder_pr", "spare", spare["id"],
              comment=f"{pr_no} raised for {qty:g} {spare['uom'] or 'pcs'}")
    conn.commit()
    log.info("auto-reorder: %s -> %s (qty %s)", spare["code"], pr_no, qty)
    return pr_no


def raise_for_request(request_id):
    """Raise or link replenishment for short request lines, outside its transaction."""
    try:
        conn = get_db()
        try:
            req = conn.execute("SELECT ticket_id FROM mnt_requests WHERE id=?", (request_id,)).fetchone()
            if not req:
                return
            from app.maintenance.services import audit
            lines = conn.execute("SELECT spare_id,qty_requested,qty_approved FROM mnt_request_items "
                                 "WHERE request_id=? AND spare_id IS NOT NULL", (request_id,)).fetchall()
            for line in lines:
                spare = conn.execute("SELECT * FROM mnt_spare_parts WHERE id=?", (line["spare_id"],)).fetchone()
                if not spare:
                    continue
                available = (spare["stock_qty"] or 0) - (spare["reserved_qty"] or 0)
                shortfall = (line["qty_approved"] or line["qty_requested"]) - available
                if shortfall <= 0:
                    continue
                if f"spare:{spare['id']}" in _open_bridge_refs(conn):
                    prs = conn.execute(
                        "SELECT DISTINCT p.id,p.pr_no FROM pr_requests p LEFT JOIN pr_items i ON i.pr_id=p.id "
                        "WHERE p.is_active=1 AND p.status NOT IN ('rejected','cancelled','closed','received') "
                        "AND (p.source_ref=? OR i.spare_id=?) ORDER BY p.id DESC",
                        (f"spare:{spare['id']}", spare["id"])).fetchall()
                    audit(conn, {"username": "auto-reorder"}, "replenishment_linked", "ticket", req["ticket_id"],
                          comment=", ".join(p["pr_no"] for p in prs))
                    conn.commit()
                else:
                    _raise_pr(conn, spare, qty=shortfall, ticket_id=req["ticket_id"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception:
        log.warning("replenishment failed for request %s", request_id, exc_info=True)


def notify_ticket_parts_arrived(pr_id):
    """Tell the ticket team about a successfully posted spare receipt. Never raises."""
    try:
        conn = get_db()
        try:
            pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        finally:
            conn.close()
        if not pr or not str(pr["source_ref"] or "").startswith("ticket:"):
            return
        from app.maintenance.services import (
            notify as mnt_notify, add_comment, _notify_people, _flush_notes, _usernames_for)
        tid = int(pr["source_ref"].split(":", 1)[1])
        conn = get_db()
        try:
            mnt_notify(conn, "maintenance_technician", f"Parts arrived: {pr['pr_no']}",
                       f"Goods received on {pr['pr_no']} for ticket #{tid} — collect from the warehouse.",
                       "ticket", tid, "info", f"/maintenance/tickets/{tid}")
            ticket = conn.execute("SELECT assigned_to FROM mnt_tickets WHERE id=?", (tid,)).fetchone()
            if ticket:
                _notify_people(conn, _usernames_for(conn, [ticket["assigned_to"]]), "Parts arrived",
                               "Goods received on %s for ticket #%s — collect from the warehouse.",
                               f"/maintenance/tickets/{tid}", args=(pr["pr_no"], tid))
            conn.commit()
            _flush_notes(conn)
        finally:
            conn.close()
        add_comment(tid, {"username": "procurement"},
                    f"📦 Parts arrived — goods received on {pr['pr_no']}"
                    + (f" (PO {pr['po_no']})" if pr["po_no"] else "") + ".")
    except Exception:
        log.warning("parts-arrived notify failed for PR %s", pr_id, exc_info=True)
