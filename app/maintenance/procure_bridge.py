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
    """source_refs of ALL still-open bridge PRs, in one query (the previous
    per-spare lookup multiplied round-trips by the number of low spares)."""
    ph = ",".join("?" for _ in _CLOSED_STATUSES)
    rows = conn.execute(
        f"SELECT source_ref FROM pr_requests WHERE source_module='maintenance' "
        f"AND is_active=1 AND status NOT IN ({ph})", _CLOSED_STATUSES).fetchall()
    return {r["source_ref"] for r in rows if r["source_ref"]}


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
        # No price on purpose: pricing_status='unpriced' routes the PR through
        # Purchasing's pricing gate (requester price lockout stays intact).
        "unit_price": 0,
    }]
    system_user = {"username": "auto-reorder", "full_name": "Auto Reorder (Maintenance)", "id": None}
    pr_id, pr_no = proc.create_pr(header, items, system_user, submit=True, priced=False)

    # Tag the PR so the goods receipt can find its way back to this spare.
    conn.execute("UPDATE pr_requests SET source_module='maintenance', source_ref=? WHERE id=?",
                 (f"spare:{spare['id']}", pr_id))
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
    """Reverse leg: push a goods receipt on a bridge PR into maintenance stock.

    `receipts` = {item_id: qty_received_now}. Uses the PR line's unit price so
    receive_stock() keeps the weighted average cost honest. Returns qty posted
    (0 when the PR is not a maintenance bridge PR). Never raises."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr or pr["source_module"] != "maintenance" or \
                not str(pr["source_ref"] or "").startswith("spare:"):
            return 0.0
        spare_id = int(str(pr["source_ref"]).split(":", 1)[1])
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        total_qty = 0.0
        unit_price = 0.0
        for it in items:
            add = receipts.get(str(it["id"])) or receipts.get(it["id"]) or 0
            try:
                add = float(add)
            except (TypeError, ValueError):
                add = 0.0
            if add > 0:
                total_qty += add
                unit_price = float(it["unit_price"] or 0) or unit_price
        if total_qty <= 0:
            return 0.0
        from app.maintenance.services import receive_stock
        ok, err = receive_stock(spare_id, total_qty, unit_price,
                                user or {"username": "auto-reorder"})
        if not ok:
            log.warning("bridge receipt post failed for PR %s spare %s: %s",
                        pr_id, spare_id, err)
            return 0.0
        log.info("bridge: PR %s receipt posted %g into spare %s", pr_id, total_qty, spare_id)
        return total_qty
    except Exception:
        log.warning("bridge receipt post crashed for PR %s", pr_id, exc_info=True)
        return 0.0
    finally:
        conn.close()
