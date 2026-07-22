"""
Maintenance ⇄ Procurement bridge — closes the spare-parts replenishment loop.

Forward:  when a spare part's AVAILABLE stock (on-hand minus reserved) falls to
          or below its reorder level, automatically raise a Procurement PR
          (unpriced → lands in Purchasing's pricing gate, then the normal
          approval ladder). One open PR per spare, ever — deduplicated against
          pr_requests.source_ref, so repeated checks never spam Procurement.

Reverse:  when Procurement records a goods receipt on a bridge-raised PR, the
          received quantity is posted straight back into maintenance stock via
          receive_stock() (which keeps the weighted average cost correct).

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


def _raise_pr(conn, spare) -> str:
    """Create + submit the unpriced PR and tag it back to the spare."""
    from app.approvals import services as proc  # lazy: avoid import cycles

    qty = _suggested_qty(spare)
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
                 (f"spare:{spare['id']}", pr_id))
    conn.execute("UPDATE pr_items SET spare_id=? WHERE pr_id=?", (spare["id"], pr_id))
    # Tell the maintenance side (role inbox + platform bell via notify()).
    from app.maintenance.services import audit as mnt_audit, notify as mnt_notify
    mnt_notify(conn, "storekeeper", f"Auto reorder PR raised: {spare['name']}",
               f"{spare['code']} reached its reorder level — {pr_no} created for "
               f"{qty:g} {spare['uom'] or 'pcs'} and sent to Purchasing for pricing.",
               "spare", spare["id"], "info", f"/procurement/pr/{pr_id}")
    mnt_audit(conn, {"username": "auto-reorder"}, "auto_reorder_pr", "spare", spare["id"],
              comment=f"{pr_no} raised for {qty:g} {spare['uom'] or 'pcs'}")
    conn.commit()
    log.info("auto-reorder: %s -> %s (qty %s)", spare["code"], pr_no, qty)
    return pr_no


def post_receipt_to_stock(pr_id, receipts, user=None) -> float:
    """Reverse leg: push a goods receipt into maintenance spare stock.

    `receipts` = {item_id: qty_received_now}. Generalised (cross-module mesh):
      * ANY received line carrying pr_items.spare_id posts its quantity into
        that spare at the line's unit price (weighted-average cost kept honest)
        — manual PRs, ticket-raised PRs and bridge auto-PRs alike;
      * legacy fallback: an older bridge PR (source_ref 'spare:<id>') whose
        lines predate the spare_id column posts the summed receipt as before;
      * a ticket-sourced PR (source_ref 'ticket:<id>') notifies the maintenance
        team and drops a parts-arrived comment on the ticket.
    Returns total qty posted. Never raises."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return 0.0
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        src = str(pr["source_ref"] or "")
        has_line_links = any(it["spare_id"] for it in items)
        if not has_line_links and not (pr["source_module"] == "maintenance"):
            return 0.0    # nothing here belongs to the spare-parts warehouse

        def _qty(it):
            add = receipts.get(str(it["id"])) or receipts.get(it["id"]) or 0
            try:
                return max(0.0, float(add))
            except (TypeError, ValueError):
                return 0.0

        from app.maintenance.services import receive_stock, notify as mnt_notify, add_comment
        posted = 0.0
        actor = user or {"username": "auto-reorder"}
        if has_line_links:
            # line-level: each spare-linked line goes to ITS OWN spare part
            for it in items:
                add = _qty(it)
                if add <= 0 or not it["spare_id"]:
                    continue
                ok, err = receive_stock(int(it["spare_id"]), add,
                                        float(it["unit_price"] or 0), actor)
                if ok:
                    posted += add
                else:
                    log.warning("mesh receipt post failed PR %s line %s: %s",
                                pr_id, it["id"], err)
        elif src.startswith("spare:"):
            # legacy whole-PR fallback (bridge PRs created before spare_id)
            spare_id = int(src.split(":", 1)[1])
            total_qty, unit_price = 0.0, 0.0
            for it in items:
                add = _qty(it)
                if add > 0:
                    total_qty += add
                    unit_price = float(it["unit_price"] or 0) or unit_price
            if total_qty > 0:
                ok, err = receive_stock(spare_id, total_qty, unit_price, actor)
                if ok:
                    posted = total_qty
                else:
                    log.warning("bridge receipt post failed for PR %s spare %s: %s",
                                pr_id, spare_id, err)

        # Parts arrived for a maintenance ticket -> tell the team on the ticket.
        if src.startswith("ticket:") and any(_qty(it) > 0 for it in items):
            try:
                tid = int(src.split(":", 1)[1])
                c2 = get_db()
                try:
                    mnt_notify(c2, "maintenance_technician",
                               f"Parts arrived: {pr['pr_no']}",
                               f"Goods received on {pr['pr_no']} for ticket #{tid} — "
                               f"collect from the warehouse.",
                               "ticket", tid, "info", f"/maintenance/tickets/{tid}")
                    c2.commit()
                finally:
                    c2.close()
                add_comment(tid, {"username": "procurement"},
                            f"\U0001F4E6 Parts arrived — goods received on {pr['pr_no']}"
                            + (f" (PO {pr['po_no']})" if pr["po_no"] else "") + ".")
            except Exception:
                log.warning("parts-arrived notify failed for PR %s", pr_id, exc_info=True)

        if posted:
            log.info("mesh: PR %s receipt posted %g into spare stock", pr_id, posted)
        return posted
    except Exception:
        log.warning("bridge receipt post crashed for PR %s", pr_id, exc_info=True)
        return 0.0
    finally:
        conn.close()
