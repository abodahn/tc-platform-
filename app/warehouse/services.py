"""
Warehouse services — raw material (roll-level fabric + quantity trims) and
finished goods, with the stock discipline the maintenance store earned the hard way:

  * ONE writer.  _move_stock() is the only function in this module that changes a
    quantity anywhere. It always writes exactly one matching movement row, it
    refuses to leave any counter negative, and it numbers the movement from its own
    row id (COUNT(*)+1 repeats numbers after any id gap and breaks the UNIQUE).
  * AVAILABILITY, never raw stock.  available = on hand - reserved.
  * ATOMIC conditional reserve.  The availability test lives in the UPDATE's WHERE
    clause, so it is re-evaluated under the row lock the UPDATE itself takes.
  * RELEASE on every exit path (issue, release, and the rollback of a partly
    reserved batch) — a stranded reservation makes real stock permanently invisible.
"""
import logging
from datetime import datetime
from math import isfinite

from app.db import get_db
from .constants import SIZES, WIDTH_TOLERANCE_CM

log = logging.getLogger("tc.warehouse")

# Reservation targets: (table, reserved column, on-hand column).
_RES = {"roll": ("wh_rolls", "reserved_m", "remaining_m"),
        "material": ("wh_materials", "reserved_qty", "stock_qty")}

_EPS = 1e-9


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def doc_no(prefix, n):
    return f"{prefix}-{datetime.utcnow().year}-{n:06d}"


def _f(v, default=0.0):
    """float() with the two values a stock counter must never accept: NaN and
    Infinity. float('nan') passes every >0 / <0 guard in this module (all NaN
    comparisons are False) and SQLite stores it as NULL — a posted qty of 'nan'
    silently ERASED 46 000 buttons before this guard existed. '1e400' overflows
    to inf and poisons the ledger permanently. Both are rejected here, once,
    because every quantity in this module is coerced through _f."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if isfinite(f) else default


def _price(v):
    """Coerce a submitted unit price. A blank / None / non-numeric / non-finite
    entry means 'no price update' and must NOT drag the weighted average to zero
    (or to infinity); a literal 0 is treated the same way for the same reason."""
    s = "" if v is None else str(v).strip()
    if s in ("", "None"):
        return 0.0
    return max(0.0, _f(s))


def _user(user):
    return (user or {}).get("username") or "system"


def _bell(conn, severity, title, message, link="/warehouse"):
    conn.execute(
        "INSERT INTO notifications (severity,module,title,message,link,created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "warehouse", title, message, link, _now()))


def _order_exists(order_id):
    """A reservation, issue or FG pack against an order id that does not exist strands
    the cost forever: costing only iterates real ord_orders rows, so it would never be
    reported anywhere. Own connection so it is safe to call before opening a write conn."""
    conn = get_db()
    try:
        return conn.execute("SELECT id FROM ord_orders WHERE id=?",
                            (order_id,)).fetchone() is not None
    except Exception:
        return False          # orders module absent -> do not block the warehouse
    finally:
        conn.close()


# --- THE single writer -----------------------------------------------------
def _roll_status(current, remaining, length):
    """Derived here and nowhere else. A route that could set 'consumed' while metres
    are still on the roll would hide real stock from the picker with no ledger trace."""
    if current == "quarantine":
        return current                       # a quarantine hold survives movements
    if remaining <= _EPS:
        return "consumed"
    return "partial" if remaining < length - _EPS else "available"


def _move_stock(conn, mtype, qty, user, material_id=None, roll_id=None, fg_id=None,
                order_id=None, unit_cost=0, ref=None, notes=None):
    """Apply a signed delta to exactly ONE target and record the movement.

    Targets: a finished-goods SKU (fg_id), a roll (roll_id — moves the roll AND its
    material header in the same call so the two can never diverge), or a material
    header alone (material_id, quantity-tracked trims). Returns (ok, msg)."""
    qty = _f(qty)
    if qty == 0:
        return False, "zero_qty"

    if fg_id:
        fg = conn.execute("SELECT packed_qty, shipped_qty FROM wh_fg WHERE id=?",
                          (fg_id,)).fetchone()
        if not fg:
            return False, "fg_not_found"
        before = _f(fg["packed_qty"]) - _f(fg["shipped_qty"])
        after = before + qty
        if after < -_EPS:
            return False, "fg_would_go_negative"
        col = "packed_qty" if qty > 0 else "shipped_qty"
        conn.execute(f"UPDATE wh_fg SET {col}=COALESCE({col},0)+?, updated_at=? WHERE id=?",
                     (abs(qty), _now(), fg_id))
    elif roll_id:
        r = conn.execute("SELECT material_id,length_m,remaining_m,reserved_m,status FROM wh_rolls "
                         "WHERE id=?", (roll_id,)).fetchone()
        if not r:
            return False, "roll_not_found"
        # The ROLL owns its material — never the caller. A form posting
        # roll_id=<fabric A roll>&material_id=<fabric B> would otherwise cut A's
        # metres out of B's header and break header == SUM(rolls) on both.
        material_id = r["material_id"]
        m = conn.execute("SELECT stock_qty, reserved_qty FROM wh_materials WHERE id=?",
                         (material_id,)).fetchone()
        if not m:
            return False, "material_not_found"
        before = _f(r["remaining_m"])
        after = before + qty
        if after < -_EPS:
            return False, "roll_would_go_negative"
        hdr = _f(m["stock_qty"]) + qty
        if hdr < -_EPS:
            return False, "stock_would_go_negative"
        # AVAILABILITY, not raw stock — the same rule the reserve obeys. Reserved
        # metres are already planned into a cut lay; writing them off here leaves
        # reserved_m > remaining_m, which shows as NEGATIVE free stock and leaves a
        # reservation that can never be issued. An issue is unaffected: it releases
        # its own reservation before it calls us. Release first, then correct.
        if after < _f(r["reserved_m"]) - _EPS:
            return False, "reserved_blocks"
        after = round(max(0.0, after), 6)
        conn.execute("UPDATE wh_rolls SET remaining_m=?, status=? WHERE id=?",
                     (after, _roll_status(r["status"], after, _f(r["length_m"])), roll_id))
        conn.execute("UPDATE wh_materials SET stock_qty=? WHERE id=?",
                     (round(max(0.0, hdr), 6), material_id))
    elif material_id:
        m = conn.execute("SELECT stock_qty, reserved_qty, roll_tracked FROM wh_materials "
                         "WHERE id=?", (material_id,)).fetchone()
        if not m:
            return False, "material_not_found"
        # A roll-tracked material's header is DERIVED from its rolls. Moving it on
        # its own (a header stock-take, a quantity receipt against fabric) invents
        # metres no roll holds, and the picker can never issue them.
        if m["roll_tracked"]:
            return False, "roll_required"
        before = _f(m["stock_qty"])
        after = before + qty
        if after < -_EPS:
            return False, "stock_would_go_negative"
        if after < _f(m["reserved_qty"]) - _EPS:
            return False, "reserved_blocks"        # see the roll branch above
        conn.execute("UPDATE wh_materials SET stock_qty=? WHERE id=?",
                     (round(max(0.0, after), 6), material_id))
    else:
        return False, "no_target"

    cur = conn.execute(
        "INSERT INTO wh_movements (type,material_id,roll_id,fg_id,order_id,qty,before_qty,"
        "after_qty,unit_cost,ref,performed_by,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (mtype, material_id, roll_id, fg_id, order_id, round(qty, 6), round(before, 6),
         round(before + qty, 6), _f(unit_cost), ref, _user(user), notes, _now()))
    conn.execute("UPDATE wh_movements SET movement_no=? WHERE id=?",
                 (doc_no("WHM", cur.lastrowid), cur.lastrowid))
    return True, ""


# --- reservations ----------------------------------------------------------
def _try_reserve(conn, target, row_id, qty):
    """Atomically reserve `qty` on a roll or material IF that much is free.

    The availability test is inside the WHERE clause, so the UPDATE re-evaluates it
    under its own row lock — safe on PostgreSQL READ COMMITTED, where a
    SELECT-then-UPDATE lets two pickers both claim the same metres. We confirm by
    re-reading in the SAME transaction rather than with cur.rowcount, because the
    PostgreSQL cursor shim (app/db.py _PGCursor) exposes no rowcount attribute."""
    table, res, have = _RES[target]
    row = conn.execute(f"SELECT {res} AS r FROM {table} WHERE id=?", (row_id,)).fetchone()
    if not row:
        return False
    before = _f(row["r"])
    conn.execute(
        f"UPDATE {table} SET {res}=COALESCE({res},0)+? "
        f"WHERE id=? AND (COALESCE({have},0)-COALESCE({res},0))>=?", (qty, row_id, qty))
    after = _f(conn.execute(f"SELECT {res} AS r FROM {table} WHERE id=?",
                            (row_id,)).fetchone()["r"])
    if after <= before + _EPS:
        return False
    if target == "roll":
        # Mirror the commitment onto the material header, so reserved_qty carries the
        # SUM of its rolls' reserved_m. Availability is read from the HEADER everywhere
        # else (materials list, reorder sweep, dashboard "materials short"); leaving it
        # at 0 for fabric showed 575 m "available" on a roll set with only 175 m free
        # and the reorder alert never fired on cloth that was already committed.
        conn.execute("UPDATE wh_materials SET reserved_qty=COALESCE(reserved_qty,0)+? "
                     "WHERE id=(SELECT material_id FROM wh_rolls WHERE id=?)", (qty, row_id))
    return True


def _claim(conn, alloc_id, new_status):
    """Move an allocation OUT of 'reserved' exactly once, and say whether we were the
    one who moved it. The guard is in the UPDATE's WHERE clause, not in a preceding
    SELECT: under PostgreSQL READ COMMITTED two transactions both read 'reserved',
    and a read-then-write would then release (or issue) the same metres twice —
    silently voiding a rival order's claim on stock that is still physically there."""
    cur = conn.execute(
        "UPDATE wh_allocations SET status=?, closed_at=? WHERE id=? AND status='reserved'",
        (new_status, _now(), alloc_id))
    # app/db.py's PostgreSQL cursor shim does not re-export rowcount; the psycopg2
    # cursor it wraps does. If neither can tell us, fall back to a re-read (which is
    # only as strong as the old behaviour, never weaker).
    rc = getattr(cur, "rowcount", None)
    if rc is None:
        rc = getattr(getattr(cur, "_raw", None), "rowcount", None)
    if rc is None:
        row = conn.execute("SELECT status FROM wh_allocations WHERE id=?",
                           (alloc_id,)).fetchone()
        rc = 1 if row and row["status"] == new_status else 0
    return rc == 1


def _release(conn, target, row_id, qty):
    """Release a reservation. CASE, not SQLite's two-argument MAX(0,...) — PostgreSQL's
    max() is an aggregate and rejects it (the maintenance copy of this line is broken
    on Render for exactly that reason)."""
    table, res, _ = _RES[target]
    conn.execute(
        f"UPDATE {table} SET {res} = CASE WHEN COALESCE({res},0)-? < 0 THEN 0 "
        f"ELSE COALESCE({res},0)-? END WHERE id=?", (qty, qty, row_id))
    if target == "roll":
        conn.execute(                       # keep the header mirror in step (see _try_reserve)
            "UPDATE wh_materials SET reserved_qty = CASE WHEN COALESCE(reserved_qty,0)-? < 0 "
            "THEN 0 ELSE COALESCE(reserved_qty,0)-? END "
            "WHERE id=(SELECT material_id FROM wh_rolls WHERE id=?)", (qty, qty, row_id))


# --- reads -----------------------------------------------------------------
def list_materials(kind=None, low_only=False):
    conn = get_db()
    try:
        sql = "SELECT * FROM wh_materials WHERE is_active=1"
        args = []
        if kind:
            sql += " AND kind=?"; args.append(kind)
        if low_only:
            sql += " AND reorder_level>0 AND (COALESCE(stock_qty,0)-COALESCE(reserved_qty,0))<=reorder_level"
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY kind, code", args).fetchall()]
        for r in rows:
            r["available"] = round(_f(r["stock_qty"]) - _f(r["reserved_qty"]), 3)
            r["short"] = bool(_f(r["reorder_level"]) > 0 and r["available"] <= _f(r["reorder_level"]))
        return rows
    finally:
        conn.close()


def get_material(material_id):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM wh_materials WHERE id=?", (material_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def list_rolls(material_id=None, color=None, shade_lot=None, status=None):
    conn = get_db()
    try:
        sql = ("SELECT r.*, m.code AS mat_code, m.name AS mat_name, m.color AS mat_color, "
               "m.uom FROM wh_rolls r JOIN wh_materials m ON m.id=r.material_id WHERE 1=1")
        args = []
        if material_id:
            sql += " AND r.material_id=?"; args.append(material_id)
        if color:
            sql += " AND m.color=?"; args.append(color)
        if shade_lot:
            sql += " AND r.shade_lot=?"; args.append(shade_lot)
        if status:
            sql += " AND r.status=?"; args.append(status)
        rows = [dict(r) for r in conn.execute(
            sql + " ORDER BY r.received_at ASC, r.id ASC", args).fetchall()]
        for r in rows:
            r["free_m"] = round(_f(r["remaining_m"]) - _f(r["reserved_m"]), 3)
        return rows
    finally:
        conn.close()


def shade_lots(material_id=None):
    conn = get_db()
    try:
        sql = "SELECT DISTINCT shade_lot FROM wh_rolls WHERE shade_lot IS NOT NULL AND remaining_m>0"
        args = []
        if material_id:
            sql += " AND material_id=?"; args.append(material_id)
        return [r["shade_lot"] for r in conn.execute(sql + " ORDER BY shade_lot", args).fetchall()]
    finally:
        conn.close()


def list_movements(limit=60):
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT v.*, m.code AS mat_code, m.uom, r.roll_no FROM wh_movements v "
            "LEFT JOIN wh_materials m ON m.id=v.material_id "
            "LEFT JOIN wh_rolls r ON r.id=v.roll_id ORDER BY v.id DESC LIMIT ?",
            (int(limit),)).fetchall()]
    finally:
        conn.close()


def open_allocations():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT a.*, m.code AS mat_code, m.uom, r.roll_no, r.shade_lot "
            "FROM wh_allocations a JOIN wh_materials m ON m.id=a.material_id "
            "LEFT JOIN wh_rolls r ON r.id=a.roll_id WHERE a.status='reserved' "
            "ORDER BY a.id DESC LIMIT 60").fetchall()]
    finally:
        conn.close()


def list_orders_lite():
    """Live customer orders for the pick/issue and FG dropdowns. The orders module may
    not be installed — an empty list just means 'no order to attribute to'."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, order_no, buyer, style_ref FROM ord_orders "
            "WHERE status NOT IN ('shipped','closed','cancelled') "
            "ORDER BY ship_date ASC, id DESC LIMIT 100").fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


# --- FIFO + shade-band picking ---------------------------------------------
def plan_pick(material_id, required, shade_lot=None, min_width_cm=0):
    """Which rolls to cut for `required` metres — read-only, writes nothing.

    Oldest first (FIFO), but a cut lay must draw from ONE dye lot: mixed shade inside
    a garment is a visible defect and a rejected shipment, not a rounding error. So we
    prefer the OLDEST shade lot that can cover the whole requirement on its own, and
    only fall back to straight FIFO across lots (flagged `mixed_lots`) when none can.
    Width is a hard filter, not a preference — a roll narrower than the marker is not
    a substitute. Returns {'rolls', 'picked', 'shortfall', 'shade_lot', 'mixed_lots'}."""
    required = max(0.0, _f(required))
    out = {"rolls": [], "picked": 0.0, "shortfall": required, "required": required,
           "shade_lot": shade_lot, "mixed_lots": False, "material_id": material_id,
           "roll_tracked": True}
    if required <= 0 or not material_id:
        out["shortfall"] = required
        return out
    conn = get_db()
    try:
        mat = conn.execute("SELECT roll_tracked, stock_qty, reserved_qty FROM wh_materials "
                           "WHERE id=?", (material_id,)).fetchone()
        if not mat:
            return out
        if not mat["roll_tracked"]:
            # A quantity-tracked material (buttons, thread, labels) has no rolls to
            # plan: the header IS the pool, so the plan is simply its availability.
            # Falling through to the roll query returned a 100% shortfall for a
            # material with 46 000 pieces on the shelf, and the issue screen then
            # refused to offer the button at all.
            out["roll_tracked"] = False
            avail = max(0.0, _f(mat["stock_qty"]) - _f(mat["reserved_qty"]))
            out["picked"] = round(min(required, avail), 3)
            out["shortfall"] = round(max(0.0, required - avail), 3)
            return out
        rows = conn.execute(
            "SELECT id, roll_no, shade_lot, width_cm, remaining_m, reserved_m, grade, bin "
            "FROM wh_rolls WHERE material_id=? AND status IN ('available','partial') "
            "ORDER BY received_at ASC, id ASC", (material_id,)).fetchall()
    finally:
        conn.close()

    cands = []
    for r in rows:
        free = round(_f(r["remaining_m"]) - _f(r["reserved_m"]), 6)
        if free <= _EPS:
            continue
        if min_width_cm and _f(r["width_cm"]) < _f(min_width_cm) - WIDTH_TOLERANCE_CM:
            continue
        if shade_lot and (r["shade_lot"] or "") != shade_lot:
            continue
        cands.append({"roll_id": r["id"], "roll_no": r["roll_no"], "shade_lot": r["shade_lot"],
                      "width_cm": _f(r["width_cm"]), "grade": r["grade"], "bin": r["bin"],
                      "free_m": free})

    chosen = cands
    if not shade_lot:
        groups = {}                       # FIFO order preserved by insertion order
        for c in cands:
            groups.setdefault(c["shade_lot"], []).append(c)
        for lot, rolls in groups.items():
            if sum(x["free_m"] for x in rolls) + _EPS >= required:
                chosen, out["shade_lot"] = rolls, lot
                break
        else:
            out["mixed_lots"] = len(groups) > 1   # no single lot covers it

    need = required
    for c in chosen:
        if need <= _EPS:
            break
        # round DOWN to what is really on the roll AND to what is really needed:
        # round(x,3) can land above either (round(1.2346,3)=1.235), which would show —
        # then reserve and cut — more than the marker asked for, and would let the
        # reservation's WHERE guard reject a plan it had just displayed.
        take = min(round(min(c["free_m"], need), 3), c["free_m"], need)
        if take <= 0:
            continue
        out["rolls"].append(dict(c, take_m=take))
        need = round(need - take, 6)
    out["picked"] = round(required - max(0.0, need), 3)
    out["shortfall"] = round(max(0.0, need), 3)
    return out


# --- reserve / issue / release --------------------------------------------
def reserve_for_order(order_id, material_id, required, user, shade_lot=None,
                      min_width_cm=0):
    """Commit material to a customer order. Returns (ok, msg, plan).

    All-or-nothing on purpose: a short lay is a defect, not a partial win, so a
    shortfall reserves NOTHING and reports the gap. Anything reserved before a race
    is detected is released again — a stranded reservation makes real stock
    permanently invisible and triggers phantom reorder alerts."""
    # Book to the same 3 dp the allocation row and the pick plan use. Reserving the raw
    # figure left the difference stuck forever: 100.0004 reserved against an allocation
    # of 100.0 issued and released 100.0, and the 0.0004 stayed on the counter as stock
    # nobody could ever see again. Sub-millimetre requirements round to 0 and are then
    # rejected below — which is also what makes a 0.0004 m "issue" impossible (it used
    # to plan nothing, report success, and then cut every open reservation on the order).
    required = round(_f(required), 3)
    if required <= 0:
        return False, "bad_qty", None
    if not order_id:
        return False, "order_required", None   # wh_allocations.order_id is NOT NULL
    # The order must actually EXIST. Reserving/issuing against a bogus id silently
    # strands the cost: costing iterates real ord_orders rows so it would never see it,
    # and no order page would ever show it. Siblings (costing, quality) already check.
    if not _order_exists(order_id):
        return False, "order_not_found", None
    mat = get_material(material_id)
    if not mat:
        return False, "material_not_found", None

    if not mat.get("roll_tracked"):
        plan = plan_pick(material_id, required)      # header availability, no rolls
        conn = get_db()
        try:
            if not _try_reserve(conn, "material", material_id, required):
                conn.rollback()
                return False, "shortfall", plan
            aid = _alloc(conn, order_id, material_id, None, required, user)
            conn.commit()
            plan.update(picked=required, shortfall=0.0, alloc_ids=[aid])
            return True, "reserved", plan
        finally:
            conn.close()

    plan = plan_pick(material_id, required, shade_lot, min_width_cm)
    # A plan that commits no roll is never a success, whatever the rounded shortfall
    # says — it would return ok with an EMPTY alloc_ids list, and issue_reserved reads
    # an empty filter as 'issue everything on this order'.
    if plan["shortfall"] > _EPS or not plan["rolls"]:
        return False, "shortfall", plan
    conn = get_db()
    try:
        alloc_ids = []
        for r in plan["rolls"]:
            if not _try_reserve(conn, "roll", r["roll_id"], r["take_m"]):
                # Another picker got there first. Undo the WHOLE batch — the metres
                # AND their allocation rows — in one rollback. Releasing the metres
                # but leaving the rows committed strands an allocation that is no
                # longer backed by a reservation: issue_reserved() would then cut it
                # anyway and _release() would silently zero a rival order's claim.
                conn.rollback()
                return False, "raced", plan
            alloc_ids.append(_alloc(conn, order_id, material_id, r["roll_id"], r["take_m"], user))
        conn.commit()
        plan["alloc_ids"] = alloc_ids
        return True, "reserved", plan
    finally:
        conn.close()


def _alloc(conn, order_id, material_id, roll_id, qty, user):
    cur = conn.execute(
        "INSERT INTO wh_allocations (order_id,material_id,roll_id,qty,status,created_by,created_at) "
        "VALUES (?,?,?,?,'reserved',?,?)",
        (order_id, material_id, roll_id, round(_f(qty), 3), _user(user), _now()))
    return cur.lastrowid


def issue_reserved(order_id, user, alloc_ids=None, notes=None):
    """Turn reservations into real consumption. Releases each reservation as it is
    fulfilled — the release must happen on EVERY exit path or stock stays invisible."""
    conn = get_db()
    try:
        sql = "SELECT * FROM wh_allocations WHERE status='reserved' AND order_id=?"
        args = [order_id]
        if alloc_ids is not None:
            # An EMPTY id list must match NOTHING. `if alloc_ids:` fell through to the
            # unfiltered query, so a caller that reserved nothing issued EVERY open
            # reservation on the order — 300 m of white jersey cut off the books by a
            # 0.0004 m request against a different material.
            if not alloc_ids:
                return False, "nothing_reserved"
            sql += " AND id IN (%s)" % ",".join("?" for _ in alloc_ids)
            args += list(alloc_ids)
        rows = conn.execute(sql, args).fetchall()
        if not rows:
            return False, "nothing_reserved"
        issued = 0.0
        for a in rows:
            qty = _f(a["qty"])
            # Claim the allocation BEFORE moving any metres: the claim is what makes
            # a concurrent second issue of the same reservation impossible.
            if not _claim(conn, a["id"], "issued"):
                conn.rollback()
                return False, "already_closed"
            if a["roll_id"]:
                r = conn.execute("SELECT status FROM wh_rolls WHERE id=?",
                                 (a["roll_id"],)).fetchone()
                # A quarantine hold beats an older reservation. Cutting a roll whose
                # shade was rejected after it was reserved puts out-of-band cloth into
                # the garment — the exact defect the hold exists to stop.
                if r and r["status"] == "quarantine":
                    conn.rollback()
                    return False, "roll_quarantined"
            cost = _unit_cost(conn, a["material_id"], a["roll_id"])
            _release(conn, "roll" if a["roll_id"] else "material",
                     a["roll_id"] or a["material_id"], qty)
            ok, msg = _move_stock(conn, "issue", -qty, user, material_id=a["material_id"],
                                  roll_id=a["roll_id"], order_id=a["order_id"],
                                  unit_cost=cost, ref=f"ALLOC-{a['id']}", notes=notes)
            if not ok:
                conn.rollback()
                return False, msg
            issued += qty
        conn.commit()
        return True, f"issued {round(issued, 3):g}"
    finally:
        conn.close()


def issue_to_order(order_id, material_id, required, user, shade_lot=None,
                   min_width_cm=0, notes=None):
    """Cutting-room counter path: reserve then immediately issue. Returns (ok, msg, plan)."""
    ok, msg, plan = reserve_for_order(order_id, material_id, required, user,
                                      shade_lot, min_width_cm)
    if not ok:
        return ok, msg, plan
    ok2, msg2 = issue_reserved(order_id, user, alloc_ids=plan.get("alloc_ids"), notes=notes)
    return ok2, msg2, plan


def release_allocation(alloc_id, user):
    """Give reserved material back (order re-planned or cancelled)."""
    conn = get_db()
    try:
        a = conn.execute("SELECT * FROM wh_allocations WHERE id=? AND status='reserved'",
                         (alloc_id,)).fetchone()
        if not a:
            return False, "not_reserved"
        if not _claim(conn, alloc_id, "released"):   # someone else got there first
            conn.rollback()
            return False, "not_reserved"
        _release(conn, "roll" if a["roll_id"] else "material",
                 a["roll_id"] or a["material_id"], _f(a["qty"]))
        conn.commit()
        return True, "released"
    finally:
        conn.close()


def _unit_cost(conn, material_id, roll_id=None):
    """Roll cost when we know the roll, else the material's moving average."""
    if roll_id:
        r = conn.execute("SELECT unit_cost FROM wh_rolls WHERE id=?", (roll_id,)).fetchone()
        if r and _f(r["unit_cost"]) > 0:
            return _f(r["unit_cost"])
    m = conn.execute("SELECT avg_cost FROM wh_materials WHERE id=?", (material_id,)).fetchone()
    return _f(m["avg_cost"]) if m else 0.0


def issued_for_order(order_id):
    """Quantity and value of material issued to an order — the costing module's entry
    point. Value is priced at the cost recorded ON THE MOVEMENT, not today's average,
    so a later receipt cannot silently rewrite what an order already consumed."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT v.material_id, m.code, m.name, m.uom, "
            "  SUM(-v.qty) AS qty, SUM(-v.qty * COALESCE(v.unit_cost,0)) AS value "
            "FROM wh_movements v JOIN wh_materials m ON m.id=v.material_id "
            "WHERE v.order_id=? AND v.qty<0 AND v.type IN ('issue','transfer') "
            "GROUP BY v.material_id, m.code, m.name, m.uom "
            "ORDER BY value DESC", (order_id,)).fetchall()
        lines = [dict(r) for r in rows]
        return {"order_id": order_id,
                "qty": round(sum(_f(l["qty"]) for l in lines), 3),
                "value": round(sum(_f(l["value"]) for l in lines), 2),
                "lines": lines}
    finally:
        conn.close()


# --- receiving -------------------------------------------------------------
def _apply_avg_cost(conn, material_id, before_stock, qty, price):
    """Moving weighted average, computed from the PRE-receipt stock. Reading stock
    after the movement puts (before+qty) on both sides and freezes avg_cost forever."""
    if not price:
        return                       # blank/0 price = 'no price update'
    total = _f(before_stock) + _f(qty)
    new_avg = ((_f(before_stock) * _f(_avg(conn, material_id))) + (_f(qty) * price)) / total \
        if total > 0 else price
    conn.execute("UPDATE wh_materials SET last_price=?, avg_cost=? WHERE id=?",
                 (price, round(new_avg, 4), material_id))


def _avg(conn, material_id):
    r = conn.execute("SELECT avg_cost FROM wh_materials WHERE id=?", (material_id,)).fetchone()
    return _f(r["avg_cost"]) if r else 0.0


def receive_qty(material_id, qty, price, user, grn_ref=None, notes=None):
    """Receive a quantity-tracked material (trims, thread, labels). Returns (ok, msg)."""
    qty = _f(qty, -1)
    if qty <= 0:
        return False, "bad_qty"          # a receipt adds stock, full stop — no abs()
    price = _price(price)
    conn = get_db()
    try:
        m = conn.execute("SELECT stock_qty, roll_tracked FROM wh_materials WHERE id=?",
                         (material_id,)).fetchone()
        if not m:
            return False, "material_not_found"
        before = _f(m["stock_qty"])
        ok, msg = _move_stock(conn, "purchase_receiving", qty, user, material_id=material_id,
                              unit_cost=price or _avg(conn, material_id),
                              ref=grn_ref, notes=notes or "Goods receipt")
        if not ok:
            conn.rollback()
            return False, msg
        _apply_avg_cost(conn, material_id, before, qty, price)
        conn.execute("UPDATE wh_materials SET low_alerted=0 WHERE id=?", (material_id,))
        conn.commit()
        return True, "received"
    finally:
        conn.close()


def receive_roll(material_id, data, user):
    """Receive one physical roll. Returns (ok, roll_no) or (False, msg).

    The roll is created empty and its length booked through _move_stock, so the very
    first metre on the shelf already has a ledger row behind it."""
    length = _f(data.get("length_m"), -1)
    if length <= 0:
        return False, "bad_qty"
    price = _price(data.get("unit_cost"))
    conn = get_db()
    try:
        m = conn.execute("SELECT stock_qty, width_cm, roll_tracked FROM wh_materials "
                         "WHERE id=?", (material_id,)).fetchone()
        if not m:
            return False, "material_not_found"
        if not m["roll_tracked"]:
            return False, "not_roll_tracked"
        # roll_no is UNIQUE and operator-typed: check it here or the INSERT raises
        # an IntegrityError straight out of the service and 500s the receipt page.
        want_no = (data.get("roll_no") or "").strip()
        if want_no and conn.execute("SELECT id FROM wh_rolls WHERE roll_no=?",
                                    (want_no,)).fetchone():
            return False, "duplicate_roll_no"
        before = _f(m["stock_qty"])
        cur = conn.execute(
            "INSERT INTO wh_rolls (material_id,supplier_lot,shade_lot,grade,width_cm,length_m,"
            "remaining_m,reserved_m,unit_cost,status,grn_ref,warehouse,bin,received_at,notes) "
            "VALUES (?,?,?,?,?,?,0,0,?,'available',?,?,?,?,?)",
            (material_id, data.get("supplier_lot") or None, data.get("shade_lot") or None,
             data.get("grade") or "A", _f(data.get("width_cm")) or _f(m["width_cm"]), length,
             price, data.get("grn_ref") or None, data.get("warehouse") or None,
             data.get("bin") or None, data.get("received_at") or _now()[:10],
             data.get("notes") or None))
        rid = cur.lastrowid
        # The auto number is built from the row id, but an operator may already have
        # TYPED that exact number on an earlier roll — then this UPDATE raises an
        # IntegrityError straight out of the service and 500s the receipt page (the
        # delivery is on the floor and the system refuses to book it). Step past it.
        roll_no, bump = want_no or doc_no("ROLL", rid), 0
        while not want_no and conn.execute("SELECT id FROM wh_rolls WHERE roll_no=?",
                                           (roll_no,)).fetchone():
            bump += 1
            roll_no = f"{doc_no('ROLL', rid)}-{bump}"
        conn.execute("UPDATE wh_rolls SET roll_no=? WHERE id=?", (roll_no, rid))
        ok, msg = _move_stock(conn, "purchase_receiving", length, user, material_id=material_id,
                              roll_id=rid, unit_cost=price, ref=data.get("grn_ref"),
                              notes=data.get("notes") or "Roll received")
        if not ok:
            conn.rollback()
            return False, msg
        _apply_avg_cost(conn, material_id, before, length, price)
        conn.execute("UPDATE wh_materials SET low_alerted=0 WHERE id=?", (material_id,))
        conn.commit()
        return True, roll_no
    finally:
        conn.close()


def return_to_store(roll_id, qty, user, order_id=None, notes=None):
    """Book an end-bit back onto the SAME roll after cutting. Never create a second
    roll for the balance — the movement ledger would stop reconciling with remaining_m."""
    qty = _f(qty, -1)
    if qty <= 0:
        return False, "bad_qty"
    conn = get_db()
    try:
        r = conn.execute("SELECT material_id, length_m, remaining_m FROM wh_rolls WHERE id=?",
                         (roll_id,)).fetchone()
        if not r:
            return False, "roll_not_found"
        if _f(r["remaining_m"]) + qty > _f(r["length_m"]) + _EPS:
            return False, "exceeds_roll_length"   # you cannot return more than was cut
        ok, msg = _move_stock(conn, "return", qty, user, material_id=r["material_id"],
                              roll_id=roll_id, order_id=order_id,
                              notes=notes or "Returned from cutting")
        if not ok:
            conn.rollback()
            return False, msg
        conn.commit()
        return True, "returned"
    finally:
        conn.close()


def adjust_stock(material_id, delta, reason, user, roll_id=None):
    """Stock-take correction. Signed; refuses to push any counter below zero."""
    delta = _f(delta)
    if delta == 0:
        return False, "zero_qty"
    conn = get_db()
    try:
        if roll_id:
            # The ROLL owns its material, exactly as in _move_stock. Trusting a posted
            # material_id here would clear low_alerted on whatever material the form
            # named — suppressing a real shortage alert on it and leaving the roll's
            # own material flagged, so it never alerts again.
            r = conn.execute("SELECT material_id FROM wh_rolls WHERE id=?", (roll_id,)).fetchone()
            material_id = r["material_id"] if r else None
        ok, msg = _move_stock(conn, "adjustment", delta, user, material_id=material_id,
                              roll_id=roll_id or None, notes=reason or "Adjustment")
        if not ok:
            conn.rollback()
            return False, msg
        if delta > 0:
            conn.execute("UPDATE wh_materials SET low_alerted=0 WHERE id=?", (material_id,))
        conn.commit()
        return True, "adjusted"
    finally:
        conn.close()


def set_roll_hold(roll_id, hold, user, notes=None):
    """Quarantine a roll (shade out of band, fault) or release the hold. Quantity is
    untouched, so this is the one status write that is not a movement."""
    conn = get_db()
    try:
        r = conn.execute("SELECT length_m, remaining_m, status FROM wh_rolls WHERE id=?",
                         (roll_id,)).fetchone()
        if not r:
            return False, "roll_not_found"
        status = "quarantine" if hold else _roll_status(
            "", _f(r["remaining_m"]), _f(r["length_m"]))
        conn.execute("UPDATE wh_rolls SET status=?, notes=COALESCE(?,notes) WHERE id=?",
                     (status, notes, roll_id))
        conn.commit()
        return True, status
    finally:
        conn.close()


def create_material(data, user):
    code = (data.get("code") or "").strip().upper()
    if not code:
        return False, "code_required"
    conn = get_db()
    try:
        if conn.execute("SELECT id FROM wh_materials WHERE code=?", (code,)).fetchone():
            return False, "duplicate_code"
        conn.execute(
            "INSERT INTO wh_materials (code,name,kind,roll_tracked,color,composition,width_cm,"
            "supplier,uom,stock_qty,reserved_qty,min_level,reorder_level,avg_cost,warehouse,"
            "bin,is_active,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,0,0,?,?,0,?,?,1,?,?)",
            (code, data.get("name") or code, data.get("kind") or "fabric",
             1 if data.get("roll_tracked") else 0, data.get("color"), data.get("composition"),
             _f(data.get("width_cm")), data.get("supplier"), data.get("uom") or "m",
             _f(data.get("min_level")), _f(data.get("reorder_level")),
             data.get("warehouse") or "RM Store", data.get("bin"), data.get("notes"), _now()))
        conn.commit()
        return True, code
    finally:
        conn.close()


# --- finished goods --------------------------------------------------------
def fg_move(order_id, style_code, color, size, qty, action, user, notes=None):
    """Pack into (action='pack') or ship out of (action='ship') a style x colour x size.
    On-hand is packed - shipped, so shipping more than is packed is refused."""
    if action not in ("pack", "ship"):
        return False, "bad_action"
    qty = _f(qty, -1)
    if qty <= 0:
        return False, "bad_qty"
    style, color, size = (style_code or "").strip(), (color or "").strip(), (size or "").strip()
    if not (style and color and size):
        return False, "sku_required"
    # ux_wh_fg_sku cannot dedupe rows whose order_id is NULL (SQL treats NULLs as
    # distinct), so an order-less pack made a NEW row every time and the SKU lookup
    # never found it again — the cartons were in the store and unshippable.
    if not order_id:
        return False, "order_required"
    if not _order_exists(order_id):
        return False, "order_not_found"   # unshippable finished goods against a ghost order
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM wh_fg WHERE order_id=? AND style_code=? AND color=? "
                           "AND size=?", (order_id, style, color, size)).fetchone()
        if not row:
            if action != "pack":
                return False, "fg_not_found"
            cur = conn.execute(
                "INSERT INTO wh_fg (order_id,style_code,color,size,packed_qty,shipped_qty,uom,"
                "warehouse,created_at,updated_at) VALUES (?,?,?,?,0,0,'pcs','FG Store',?,?)",
                (order_id, style, color, size, _now(), _now()))
            fid = cur.lastrowid
        else:
            fid = row["id"]
        ok, msg = _move_stock(conn, "fg_pack" if action == "pack" else "fg_ship",
                              qty if action == "pack" else -qty, user, fg_id=fid,
                              order_id=order_id, notes=notes)
        if not ok:
            conn.rollback()
            return False, msg
        conn.commit()
        return True, action
    finally:
        conn.close()


def fg_matrix(order_id=None):
    """Style x colour rows against size columns — the pivot lives here, not in the
    schema. Returns {'sizes', 'rows', 'totals'}."""
    conn = get_db()
    try:
        sql = "SELECT * FROM wh_fg"
        args = []
        if order_id:
            sql += " WHERE order_id=?"; args.append(order_id)
        rows = [dict(r) for r in conn.execute(
            sql + " ORDER BY style_code, color, size", args).fetchall()]
    finally:
        conn.close()

    found = {r["size"] for r in rows}
    sizes = [s for s in SIZES if s in found] + sorted(found - set(SIZES))
    grid, totals = {}, {"packed": 0.0, "shipped": 0.0, "onhand": 0.0}
    for r in rows:
        key = (r["order_id"], r["style_code"], r["color"])
        g = grid.setdefault(key, {"order_id": r["order_id"], "style_code": r["style_code"],
                                  "color": r["color"], "cells": {},
                                  "packed": 0.0, "shipped": 0.0, "onhand": 0.0})
        packed, shipped = _f(r["packed_qty"]), _f(r["shipped_qty"])
        g["cells"][r["size"]] = {"packed": packed, "shipped": shipped,
                                 "onhand": round(packed - shipped, 3)}
        g["packed"] += packed; g["shipped"] += shipped
        g["onhand"] = round(g["packed"] - g["shipped"], 3)
        totals["packed"] += packed; totals["shipped"] += shipped
    totals["onhand"] = round(totals["packed"] - totals["shipped"], 3)
    return {"sizes": sizes, "rows": list(grid.values()), "totals": totals}


# --- shortage sweep + dashboard -------------------------------------------
def stock_sweep():
    """Bell-alert ONCE per material that has fallen to/below its reorder level, on
    AVAILABLE stock (on hand minus reserved). low_alerted is cleared by any receipt or
    positive adjustment, so the same material can alert again next time it runs short.
    Never raises — a sweep must not be able to break a page load."""
    try:
        conn = get_db()
    except Exception:
        return
    try:
        # Re-arm first: clear the flag on anything whose AVAILABILITY has recovered,
        # whatever put it back — a receipt, an end-bit returned from cutting, a
        # released reservation, a cancelled order. Clearing it only on the receipt
        # and positive-adjustment paths meant a material that recovered any other way
        # NEVER alerted again, and the next real shortage passed in silence.
        conn.execute(
            "UPDATE wh_materials SET low_alerted=0 WHERE low_alerted=1 AND "
            "(reorder_level<=0 OR (COALESCE(stock_qty,0)-COALESCE(reserved_qty,0))>reorder_level)")
        for m in conn.execute(
                "SELECT id,code,name,uom,stock_qty,reserved_qty,reorder_level FROM wh_materials "
                "WHERE is_active=1 AND low_alerted=0 AND reorder_level>0 AND "
                "(COALESCE(stock_qty,0)-COALESCE(reserved_qty,0))<=reorder_level").fetchall():
            avail = _f(m["stock_qty"]) - _f(m["reserved_qty"])
            sev = "critical" if avail <= 0 else "warning"
            head = "Out of stock" if avail <= 0 else "Low stock"
            _bell(conn, sev, f"{head}: {m['code']}",
                  f"{m['name']} ({m['code']}) available {avail:g} {m['uom'] or ''} "
                  f"against a reorder level of {_f(m['reorder_level']):g}.",
                  "/warehouse/materials?low=1")
            conn.execute("UPDATE wh_materials SET low_alerted=1 WHERE id=?", (m["id"],))
        conn.commit()
    except Exception:
        log.warning("warehouse stock sweep failed", exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def dashboard():
    conn = get_db()
    try:
        def one(sql, args=()):
            return _f(conn.execute(sql, args).fetchone()["c"])
        d = {
            "materials": int(one("SELECT COUNT(*) c FROM wh_materials WHERE is_active=1")),
            "rolls_available": int(one("SELECT COUNT(*) c FROM wh_rolls WHERE status IN "
                                       "('available','partial') AND remaining_m>0")),
            "total_m": round(one("SELECT COALESCE(SUM(remaining_m),0) c FROM wh_rolls"), 1),
            "reserved_m": round(one("SELECT COALESCE(SUM(reserved_m),0) c FROM wh_rolls"), 1),
            "shade_lots": int(one("SELECT COUNT(DISTINCT shade_lot) c FROM wh_rolls "
                                  "WHERE shade_lot IS NOT NULL AND remaining_m>0")),
            "low_count": int(one("SELECT COUNT(*) c FROM wh_materials WHERE is_active=1 AND "
                                 "reorder_level>0 AND (COALESCE(stock_qty,0)-"
                                 "COALESCE(reserved_qty,0))<=reorder_level")),
            "quarantine": int(one("SELECT COUNT(*) c FROM wh_rolls WHERE status='quarantine'")),
            "fg_onhand": round(one("SELECT COALESCE(SUM(packed_qty)-SUM(shipped_qty),0) c "
                                   "FROM wh_fg"), 0),
            "fg_packed": round(one("SELECT COALESCE(SUM(packed_qty),0) c FROM wh_fg"), 0),
            "stock_value": round(one("SELECT COALESCE(SUM(stock_qty*avg_cost),0) c "
                                     "FROM wh_materials WHERE is_active=1"), 2),
        }
    finally:
        conn.close()
    d["low"] = list_materials(low_only=True)[:10]
    d["recent"] = list_movements(limit=12)
    return d


# --- procurement bridge (reverse leg) --------------------------------------
def _resolve_material(conn, text):
    """Map a PR line's item text back to a material. Procurement's pr_items has no
    warehouse link column (spare_id is hard-typed to maintenance), and that file is not
    ours to change — so the material CODE at the head of the line is the join key, the
    same convention the maintenance bridge writes ('<code> — <name>')."""
    s = str(text or "").strip()
    if not s:
        return None
    cands, seen = [], set()
    for c in (s, s.split("—")[0].strip(), s.split(" - ")[0].strip(), s.split(" ")[0].strip()):
        if c and c not in seen:
            seen.add(c)
            cands.append(c)
    for c in cands:
        r = conn.execute("SELECT id, roll_tracked, code FROM wh_materials "
                         "WHERE UPPER(code)=UPPER(?) AND is_active=1", (c,)).fetchone()
        if r:
            return dict(r)
    return None


def post_receipt_to_material(pr_id, receipts, user=None):
    """Reverse leg of the procurement bridge: push a goods receipt into material stock.

    `receipts` = {pr_items.id: qty_received_NOW} — the EFFECTIVE, already-capped delta
    procurement computes, not the raw form input. Roll-tracked materials get one roll
    per receipt line (the shade lot is labelled by the warehouse on arrival); quantity
    materials are added to the header. Returns total qty posted. NEVER raises — a
    bridge failure must not break a goods receipt."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return 0.0
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()

        def _col(row, name):
            try:
                return row[name]
            except (KeyError, IndexError):
                return None

        # Anything belonging to the spare-parts store is not ours — the maintenance
        # bridge already posted it, and posting twice would double the stock.
        if _col(pr, "source_module") == "maintenance" or any(_col(i, "spare_id") for i in items):
            return 0.0

        pr_no = _col(pr, "pr_no") or f"PR-{pr_id}"
        posted = 0.0
        for it in items:
            add = receipts.get(str(it["id"])) or receipts.get(it["id"]) or 0
            add = max(0.0, _f(add))
            if add <= 0:
                continue
            mat = _resolve_material(conn, _col(it, "item"))
            if not mat:
                continue                      # not a warehouse material — silently skip
            price = _f(_col(it, "unit_price"))
            if mat["roll_tracked"]:
                ok, msg = receive_roll(mat["id"], {
                    "length_m": add, "unit_cost": price, "grn_ref": pr_no,
                    "width_cm": 0, "notes": f"Goods receipt {pr_no} — label shade lot on arrival",
                }, user)
            else:
                ok, msg = receive_qty(mat["id"], add, price, user, grn_ref=pr_no,
                                      notes=f"Goods receipt {pr_no}")
            if ok:
                posted += add
            else:
                log.warning("warehouse receipt post failed PR %s line %s: %s",
                            pr_id, it["id"], msg)
        if posted:
            log.info("warehouse: PR %s receipt posted %g into material stock", pr_id, posted)
        return posted
    except Exception:
        log.warning("warehouse receipt post crashed for PR %s", pr_id, exc_info=True)
        return 0.0
    finally:
        conn.close()


# --- exports ---------------------------------------------------------------
_MOVEMENT_EXPORT_LIMIT = 5000   # newest first; a stock ledger grows forever, and a
                                # store keeper reconciling this month never scrolls past
                                # a few hundred rows. Raise it here if a full-year audit
                                # ever needs the whole ledger in one file.


def _txt(v):
    """Empty cell, never the string 'None' — the CSV goes straight into Excel."""
    return "" if v is None else v


def _order_labels():
    """{order_id: order_no} for labelling exports. Own connection, and tolerant of the
    orders module being absent, exactly like _order_exists — a failed statement on
    PostgreSQL poisons the transaction it runs in, so this must not share the caller's."""
    conn = get_db()
    try:
        return {r["id"]: r["order_no"] for r in
                conn.execute("SELECT id, order_no FROM ord_orders").fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def export_dataset(key):
    """Rows for a named export: returns (headers, rows) or (None, None).

    Keys: materials | rolls | movements | finished-goods | issues-by-order.
    Availability is exported alongside raw stock because availability is the number
    this module makes decisions on, and a CSV that only showed on-hand would have
    people committing reserved fabric all over again in Excel."""
    if key == "materials":
        rows = list_materials()
        return (["Code", "Name", "Kind", "Colour", "Composition", "Width (cm)", "Supplier",
                 "UoM", "On Hand", "Reserved", "Available", "Reorder Level", "Min Level",
                 "Avg Cost", "Stock Value", "Roll Tracked", "Short", "Warehouse", "Bin"],
                [[_txt(r["code"]), _txt(r["name"]), _txt(r["kind"]), _txt(r["color"]),
                  _txt(r["composition"]), round(_f(r["width_cm"]), 3), _txt(r["supplier"]),
                  _txt(r["uom"]), round(_f(r["stock_qty"]), 3), round(_f(r["reserved_qty"]), 3),
                  round(_f(r["available"]), 3), round(_f(r["reorder_level"]), 3),
                  round(_f(r["min_level"]), 3), round(_f(r["avg_cost"]), 2),
                  round(_f(r["stock_qty"]) * _f(r["avg_cost"]), 2),
                  "yes" if r["roll_tracked"] else "no", "yes" if r["short"] else "no",
                  _txt(r["warehouse"]), _txt(r["bin"])]
                 for r in rows])

    if key == "rolls":
        rows = list_rolls()
        return (["Roll No", "Material Code", "Material", "Material Colour", "Shade Lot",
                 "Supplier Lot", "Grade", "Width (cm)", "Length (m)", "Remaining (m)",
                 "Reserved (m)", "Free (m)", "Status", "Unit Cost", "Remaining Value",
                 "GRN Ref", "Warehouse", "Bin", "Received On"],
                [[_txt(r["roll_no"]), _txt(r["mat_code"]), _txt(r["mat_name"]),
                  _txt(r["mat_color"]), _txt(r["shade_lot"]), _txt(r["supplier_lot"]),
                  _txt(r["grade"]), round(_f(r["width_cm"]), 3), round(_f(r["length_m"]), 3),
                  round(_f(r["remaining_m"]), 3), round(_f(r["reserved_m"]), 3),
                  round(_f(r["free_m"]), 3), _txt(r["status"]), round(_f(r["unit_cost"]), 2),
                  round(_f(r["remaining_m"]) * _f(r["unit_cost"]), 2), _txt(r["grn_ref"]),
                  _txt(r["warehouse"]), _txt(r["bin"]), _txt(r["received_at"])]
                 for r in rows])

    if key == "movements":
        labels = _order_labels()
        rows = list_movements(limit=_MOVEMENT_EXPORT_LIMIT)
        return (["Movement No", "Date", "Type", "Material Code", "Roll No", "Order",
                 "Quantity", "Balance Before", "Balance After", "UoM", "Unit Cost",
                 "Value", "Reference", "Performed By", "Notes"],
                [[_txt(r["movement_no"]), _txt(r["created_at"]), _txt(r["type"]),
                  _txt(r["mat_code"]), _txt(r["roll_no"]),
                  _txt(labels.get(r["order_id"]) or r["order_id"]),
                  round(_f(r["qty"]), 3), round(_f(r["before_qty"]), 3),
                  round(_f(r["after_qty"]), 3), _txt(r["uom"]),
                  round(_f(r["unit_cost"]), 2),
                  round(_f(r["qty"]) * _f(r["unit_cost"]), 2),
                  _txt(r["ref"]), _txt(r["performed_by"]), _txt(r["notes"])]
                 for r in rows])

    if key == "finished-goods":
        labels = _order_labels()
        conn = get_db()
        try:
            # Flat one-row-per-SKU, not the screen's pivot: a size range differs per buyer,
            # so dynamic columns would give every order a different CSV shape.
            rows = conn.execute(
                "SELECT order_id, style_code, color, size, packed_qty, shipped_qty, uom, "
                "warehouse, bin, updated_at FROM wh_fg "
                "ORDER BY order_id, style_code, color, size").fetchall()
        finally:
            conn.close()
        return (["Order", "Order ID", "Style", "Colour", "Size", "Packed", "Shipped",
                 "In Store", "UoM", "Warehouse", "Bin", "Updated At"],
                [[_txt(labels.get(r["order_id"]) or r["order_id"]), _txt(r["order_id"]),
                  _txt(r["style_code"]), _txt(r["color"]), _txt(r["size"]),
                  round(_f(r["packed_qty"]), 3), round(_f(r["shipped_qty"]), 3),
                  round(_f(r["packed_qty"]) - _f(r["shipped_qty"]), 3), _txt(r["uom"]),
                  _txt(r["warehouse"]), _txt(r["bin"]), _txt(r["updated_at"])]
                 for r in rows])

    if key == "issues-by-order":
        labels = _order_labels()
        conn = get_db()
        try:
            # Same shape and same pricing rule as issued_for_order() — the cost recorded
            # ON THE MOVEMENT, so a later receipt cannot rewrite what an order consumed —
            # only across every order at once, which is what costing gets asked for.
            rows = conn.execute(
                "SELECT v.order_id, m.code, m.name, m.uom, "
                "  SUM(-v.qty) AS qty, SUM(-v.qty * COALESCE(v.unit_cost,0)) AS value "
                "FROM wh_movements v JOIN wh_materials m ON m.id=v.material_id "
                "WHERE v.order_id IS NOT NULL AND v.qty<0 AND v.type IN ('issue','transfer') "
                "GROUP BY v.order_id, m.code, m.name, m.uom "
                "ORDER BY v.order_id, value DESC").fetchall()
        finally:
            conn.close()
        return (["Order", "Order ID", "Material Code", "Material", "UoM",
                 "Quantity Issued", "Value Issued"],
                [[_txt(labels.get(r["order_id"]) or r["order_id"]), _txt(r["order_id"]),
                  _txt(r["code"]), _txt(r["name"]), _txt(r["uom"]),
                  round(_f(r["qty"]), 3), round(_f(r["value"]), 2)]
                 for r in rows])

    return (None, None)
