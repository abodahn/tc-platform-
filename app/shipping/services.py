"""
Shipping services — shipments, packing lists, commercial invoices and the
ordered-vs-shipped reconciliation that flags chargeback risk on the bell.

Arithmetic invariants (all covered by tests_selftest.py):
  * weights and dimensions are stored PER CARTON, so every total multiplies by `cartons`;
  * pieces = qty_per_carton x cartons, and BOTH are whole counts — a fractional box or a
    fractional garment is refused on write, because every document prints pieces rounded
    ('{:,.0f}') while the invoice would charge the unrounded value;
  * CBM = L x W x H in METRES x cartons — dimensions are captured in cm, hence /1_000_000;
  * invoice line amount = pieces x the unit price SNAPSHOTTED on the shipment, rounded to
    2dp per line, and the invoice total is the sum of those rounded lines (that is how a
    customs invoice foots — rounding the grand total instead drifts against the buyer's LC);
  * a negative or non-numeric quantity/weight/dimension is REFUSED on write, never coerced
    with abs() or silently zeroed, so the SQL aggregates can trust what is stored.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext

from app.db import get_db

log = logging.getLogger("tc.shipping")
from .constants import (SHIPPED_STATUS, LOCKED_STATUS, QTY_TOLERANCE_PCT, ETD_SOON_DAYS,
                        SHIPMENT_STATUS, CREATE_STATUS, INCOTERMS, MODES)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


_INF = float("inf")
# Integer columns are int4 on PostgreSQL; anything at or beyond that ceiling is a keying
# error long before it is a shipment, and inserting it would blow up inside the driver.
MAX_INT = 2000000000


def _f(v, default=0.0):
    """Form floats arrive as raw strings; a blank or a typo must not 500 or become garbage.
    NaN and Infinity survive float() ('nan', 'inf', '1e400') and then poison every SUM in
    the module — worse, `nan < 0` is False, so they walk straight through the negative
    guards below. They are garbage input, so they take the default like any other typo."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f == _INF or f == -_INF:        # NaN / +-Infinity
        return default
    return f


def _i(v, default=0):
    # int(float('inf')) raises OverflowError, which float()'s own except clause does not
    # catch -- so this goes through _f, which has already rejected inf/nan.
    f = _f(v, None)
    return default if f is None else int(f)


_CENT = Decimal("0.01")


def _money(pieces, price):
    """One invoice line, rounded HALF-UP to 2dp — the commercial convention the buyer's
    finance team and customs both foot with. round() is half-EVEN, and a 3dp garment
    price often lands exactly on the half cent: round(3.855, 2) is 3.85 and
    round(7 * 2.675, 2) is 18.72, both a cent under-declared. Decimal multiplies the
    decimal operands exactly, so the cent lands where the buyer's calculator puts it."""
    with localcontext() as ctx:
        ctx.prec = 40      # inputs are bounded by MAX_INT: 2e9 pcs x 2e9 never exceeds 40 digits
        return float((Decimal(str(pieces)) * Decimal(str(price))).quantize(_CENT, ROUND_HALF_UP))


def _date(v):
    """Three-state, like _order_row: None = not stated, False = garbage, else an ISO date.
    <input type=date> cannot post a bad date but a POST is not a browser, and 'not-a-date'
    would print on a customs document and sort into the ETD window at random."""
    s = str(v or "").strip()[:10]      # str(): a caller may hand us a date object or an int
    if not s:
        return None
    try:
        return date.fromisoformat(s).isoformat()
    except (ValueError, TypeError):
        return False


def _optional(data, key):
    """An optional numeric field. Blank/missing means 'not stated' -> 0; anything
    non-numeric or negative comes back as -1 so the caller can refuse it. Without
    this split, leaving a carton dimension empty would read as a negative number."""
    v = data.get(key)
    if v is None or str(v).strip() == "":
        return 0.0
    return _f(v, -1)


def _bell(conn, severity, title, message, link="/shipping"):
    conn.execute("INSERT INTO notifications (severity,module,title,message,link,created_at) "
                 "VALUES (?,?,?,?,?,?)", (severity, "shipping", title, message, link, _now()))


# --- packing arithmetic (pure, hence directly testable) -------------------
def line_math(c):
    """Derived values for one packing-list line. Guards zero/None/garbage dimensions:
    anything unusable contributes 0 rather than a negative or a crash."""
    n = max(0, _i(c.get("cartons")))
    per = max(0.0, _f(c.get("qty_per_carton")))
    l = max(0.0, _f(c.get("length_cm")))
    w = max(0.0, _f(c.get("width_cm")))
    h = max(0.0, _f(c.get("height_cm")))
    return {
        "pieces": round(per * n, 3),
        "net_total": round(max(0.0, _f(c.get("net_weight"))) * n, 3),
        "gross_total": round(max(0.0, _f(c.get("gross_weight"))) * n, 3),
        # cm -> m on each edge: /100 three times == /1_000_000
        "cbm": round(l * w * h / 1000000.0 * n, 4),
    }


def decorate(rows):
    for r in rows:
        r.update(line_math(r))
    return rows


def totals(rows):
    """Sum decorated lines. Cartons is the physical box count the forwarder books against."""
    return {
        "lines": len(rows),
        "cartons": sum(max(0, _i(r.get("cartons"))) for r in rows),
        "pieces": round(sum(_f(r.get("pieces")) for r in rows), 3),
        "net": round(sum(_f(r.get("net_total")) for r in rows), 3),
        "gross": round(sum(_f(r.get("gross_total")) for r in rows), 3),
        "cbm": round(sum(_f(r.get("cbm")) for r in rows), 4),
    }


def packing_summary(rows):
    """A packing list is the carton lines summarised by style / colour / size."""
    out = {}
    for r in rows:
        key = (r.get("style") or "", r.get("colour") or "", r.get("size") or "")
        g = out.setdefault(key, {"style": key[0], "colour": key[1], "size": key[2],
                                 "cartons": 0, "pieces": 0.0, "net_total": 0.0,
                                 "gross_total": 0.0, "cbm": 0.0})
        g["cartons"] += max(0, _i(r.get("cartons")))
        g["pieces"] += _f(r.get("pieces"))
        g["net_total"] += _f(r.get("net_total"))
        g["gross_total"] += _f(r.get("gross_total"))
        g["cbm"] += _f(r.get("cbm"))
    for g in out.values():
        g["pieces"] = round(g["pieces"], 3)
        g["net_total"] = round(g["net_total"], 3)
        g["gross_total"] = round(g["gross_total"], 3)
        g["cbm"] = round(g["cbm"], 4)
    return [out[k] for k in sorted(out)]


# --- optional warehouse hook ---------------------------------------------
def fg_onhand(order_id):
    """Finished-goods on hand for an order, from the warehouse module when installed.
    Returns None when warehouse is absent so the UI simply omits the comparison."""
    if not order_id:
        return None
    try:
        from app.warehouse import services as wh   # optional sibling module
    except ImportError:
        return None
    try:
        return _f(wh.fg_matrix(order_id).get("totals", {}).get("onhand"))
    except Exception:
        return None


# --- reads ----------------------------------------------------------------
def _enum(v, allowed, default):
    """Statuses, incoterms and modes are typed straight into a <select>, but a POST is
    not a browser: an arbitrary status hides a shipment from every status filter."""
    v = (v or "").strip()
    return v if v in allowed else default


# _order_row is three-state on purpose: a dict (found), None (orders module present but no
# such order -> a link that must not be stored), or False (orders module absent -> we cannot
# tell, so an existing link is left alone). False is falsy, so templates still read it as "no order".
def _order_row(conn, order_id):
    try:
        r = conn.execute("SELECT id,order_no,po_no,buyer,style_ref,style_name,qty,unit_price,"
                         "currency,ship_date,status FROM ord_orders WHERE id=?",
                         (order_id,)).fetchone()
    except Exception:
        return False     # orders module absent — a shipment can still stand alone
    return dict(r) if r else None


def order_options():
    """Open orders for the shipment form picker. Empty list when orders is absent."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id,order_no,buyer,style_ref,style_name,qty,unit_price,currency,ship_date "
            "FROM ord_orders WHERE status NOT IN ('closed','cancelled') "
            "ORDER BY ship_date ASC, id DESC LIMIT 200").fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def list_shipments(status=None, mode=None, order_id=None):
    conn = get_db()
    try:
        q = ("SELECT s.*, COALESCE(SUM(c.cartons),0) AS cartons, "
             "COALESCE(SUM(c.qty_per_carton*c.cartons),0) AS pieces, "
             "COALESCE(SUM(c.length_cm*c.width_cm*c.height_cm/1000000.0*c.cartons),0) AS cbm "
             "FROM shp_shipments s LEFT JOIN shp_cartons c ON c.shipment_id=s.id WHERE 1=1")
        args = []
        if status:
            q += " AND s.status=?"; args.append(status)
        if mode:
            q += " AND s.mode=?"; args.append(mode)
        if order_id:
            q += " AND s.order_id=?"; args.append(order_id)
        q += " GROUP BY s.id ORDER BY s.etd DESC, s.id DESC"
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        for r in rows:
            r["cbm"] = round(_f(r["cbm"]), 4)
            r["pieces"] = round(_f(r["pieces"]), 3)
        return rows
    finally:
        conn.close()


def get_shipment(shipment_id):
    conn = get_db()
    try:
        s = conn.execute("SELECT * FROM shp_shipments WHERE id=?", (shipment_id,)).fetchone()
        if not s:
            return None
        cartons = decorate([dict(r) for r in conn.execute(
            "SELECT * FROM shp_cartons WHERE shipment_id=? ORDER BY id", (shipment_id,)).fetchall()])
        s = dict(s)
        order = _order_row(conn, s.get("order_id")) or None
        t = totals(cartons)
        return {"shipment": s, "cartons": cartons, "totals": t, "order": order,
                "summary": packing_summary(cartons), "fg": fg_onhand(s.get("order_id")),
                "locked": s.get("status") in LOCKED_STATUS}
    finally:
        conn.close()


def invoice(shipment_id):
    """Commercial invoice: the packing summary priced at the snapshotted unit price."""
    b = get_shipment(shipment_id)
    if not b:
        return None
    price = _f(b["shipment"].get("unit_price"))
    lines, total = [], 0.0
    for g in b["summary"]:
        amount = _money(g["pieces"], price)        # 2dp per line; the total sums the lines
        total += amount
        lines.append({**g, "unit_price": price, "amount": amount})
    b["lines"] = lines
    b["invoice_total"] = round(total, 2)
    return b


# --- writes ---------------------------------------------------------------
def create_shipment(data, user):
    """Create a shipment. Buyer, currency and unit price default from the linked order —
    the price is a SNAPSHOT, so repricing the order later cannot rewrite an issued invoice."""
    conn = get_db()
    try:
        oid = _i(data.get("order_id"))
        oid = oid if 0 < oid <= MAX_INT else None
        o = _order_row(conn, oid) if oid else None
        if o is None:
            oid = None       # no such order — never store a dangling link (see _order_row)
        o = o or {}
        # Bounded exactly like the reprice path in update_shipment: unbounded, a price
        # typed as 1e308 prints an invoice total of literally `inf`. Anything blank,
        # garbage, negative or absurd falls back to the order's own price.
        price = _f(data.get("unit_price"), -1)
        if not (0 <= price <= MAX_INT):
            price = _f(o.get("unit_price"))
        if not (0 <= price <= MAX_INT):
            price = 0.0                            # a garbage order price cannot poison it either
        etd, eta, inv_date = (_date(data.get(k)) for k in ("etd", "eta", "invoice_date"))
        cur = conn.execute(
            "INSERT INTO shp_shipments (order_id,buyer,destination,port_loading,incoterm,mode,"
            "carrier,container_no,etd,eta,status,invoice_no,invoice_date,currency,unit_price,"
            "lc_ref,notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, (data.get("buyer") or "").strip() or o.get("buyer"),
             data.get("destination"), data.get("port_loading"),
             _enum(data.get("incoterm"), INCOTERMS, "FOB"),
             _enum(data.get("mode"), MODES, "sea"),
             data.get("carrier"), data.get("container_no"), etd or None,
             # Only a pre-departure status: a shipment born 'dispatched' is locked
             # before a single carton line exists, and could never be packed.
             eta or None, _enum(data.get("status"), CREATE_STATUS, "planned"),
             data.get("invoice_no") or None, inv_date or None,
             (data.get("currency") or "").strip() or o.get("currency") or "USD",
             price, data.get("lc_ref"), data.get("notes"),
             (user or {}).get("username"), _now()))
        sid = cur.lastrowid
        # Number from the row id -- unique and collision-free. A COUNT(*)+1 scheme
        # repeats numbers after any delete and clashes with the seed's id-based numbers.
        conn.execute("UPDATE shp_shipments SET shipment_no=? WHERE id=?",
                     ("SH-%d-%04d" % (date.today().year, sid), sid))
        conn.commit()
        return sid
    finally:
        conn.close()


_ENUM_FIELDS = {"status": SHIPMENT_STATUS, "incoterm": INCOTERMS, "mode": MODES}
_DATE_FIELDS = ("etd", "eta", "invoice_date")


def update_shipment(shipment_id, data, user):
    """Returns (ok, reason). Three rules survive here that the packing-list lock alone
    does not enforce, because each would let an already-issued customs document move:
      * a shipment that has EVER left may go on to another left/cancelled state, but
        never back to planned/packed — otherwise the carton lock is undone by two clicks;
      * a cancelled shipment that never left may be re-opened to planned/packed after a
        mis-click, but it must never jump straight into the shipped totals with its
        packing list still frozen;
      * its invoice unit price is frozen once it has left, for the same reason."""
    conn = get_db()
    try:
        cur = conn.execute("SELECT status,incoterm,mode,unit_price,dispatched_at,order_id "
                           "FROM shp_shipments WHERE id=?", (shipment_id,)).fetchone()
        if not cur:
            return False, "not_found"
        locked = cur["status"] in LOCKED_STATUS
        # Departure is a fact about the past, so it is read from the STAMP, not from the
        # current status: dispatched -> cancelled -> planned would otherwise launder a
        # customs document back into an editable one in two clicks. Falls back to the
        # status so rows written by the seed (or by hand) still count as having left.
        ever_left = bool(cur["dispatched_at"]) or cur["status"] in SHIPPED_STATUS
        fields = ["buyer", "destination", "port_loading", "incoterm", "mode", "carrier",
                  "container_no", "etd", "eta", "status", "invoice_no", "invoice_date",
                  "currency", "lc_ref", "notes"]
        sets, args, refused, stamp = [], [], None, False
        for f in fields:
            if f not in data:
                continue
            # Strip first: create_shipment strips its text, so without this an update
            # could store "   " as a destination and print a blank port of discharge
            # on a customs document while still reading as "set".
            v = data.get(f)
            v = (v.strip() if isinstance(v, str) else v) or None
            if f in _DATE_FIELDS and v is not None:
                v = _date(v)
                if v is False:                          # 'not-a-date' on an export document
                    refused = refused or "bad_date"
                    continue
            if f in _ENUM_FIELDS:
                v = _enum(v, _ENUM_FIELDS[f], cur[f])   # garbage/blank keeps the current value
                if f == "status" and v != cur["status"]:
                    if cur["status"] == "cancelled" and v in SHIPPED_STATUS:
                        refused = "status_locked"       # resurrecting a void shipment
                        continue
                    if v not in LOCKED_STATUS and ever_left:
                        refused = "status_locked"       # it has left; it is never re-opened
                        continue
                    # Back-fill the departure fact BEFORE a status change can hide it:
                    # cancelling a dispatched row that carries no stamp yet (seeded, imported
                    # or hand-written) would otherwise erase the only evidence it ever left.
                    stamp = (v in SHIPPED_STATUS or ever_left) and not cur["dispatched_at"]
            sets.append(f"{f}=?"); args.append(v)
        if stamp:                                       # the moment it actually left
            sets.append("dispatched_at=?"); args.append(_now())
        if "unit_price" in data:                       # explicit reprice before issuing
            # -1 means blank/garbage/negative. Zeroing the price instead would silently
            # turn a customs invoice into a 0.00 declaration, so it is refused.
            price = _f(data.get("unit_price"), -1)
            if price < 0 or price > MAX_INT:
                refused = refused or "bad_price"
            elif price != _f(cur["unit_price"]):       # an unchanged price is not a reprice
                if locked or ever_left:
                    refused = refused or "price_locked"
                else:
                    sets.append("unit_price=?"); args.append(price)
        if not sets:
            return False, refused or "no_change"
        sets.append("updated_at=?"); args.append(_now())
        args.append(shipment_id)
        conn.execute("UPDATE shp_shipments SET " + ", ".join(sets) + " WHERE id=?", args)
        conn.commit()
        left_now = stamp                      # this call is the moment it departed
        order_id = cur["order_id"]
    finally:
        conn.close()
    # Departure must LEAVE THE STORE. wh_fg.shipped_qty existed but nothing ever wrote
    # it, so finished-goods on hand (packed - shipped) drifted permanently upward: every
    # carton stayed "in stock" after it had physically gone. Post-commit and best-effort
    # so a warehouse problem can never block or reverse a real departure.
    if left_now:
        _post_shipped_to_fg(shipment_id, order_id, user)
    return True, refused or "updated"


def _post_shipped_to_fg(shipment_id, order_id, user):
    """Decrement finished goods for every carton line on a shipment that just left."""
    if not order_id:
        return
    try:
        from app.warehouse import services as wh      # optional sibling module
    except ImportError:
        return
    conn = get_db()
    try:
        lines = conn.execute(
            "SELECT style, colour, size, qty_per_carton, cartons FROM shp_cartons "
            "WHERE shipment_id=?", (shipment_id,)).fetchall()
    except Exception:
        return
    finally:
        conn.close()
    for ln in lines:
        qty = _f(ln["qty_per_carton"]) * _f(ln["cartons"])
        if qty <= 0:
            continue
        try:
            ok, msg = wh.fg_move(order_id, ln["style"], ln["colour"], ln["size"],
                                 qty, "ship", user,
                                 notes=f"Shipment {shipment_id} dispatched")
            if not ok:
                # Most likely 'more than packed' — real information, not noise: it means
                # the packing list and the finished-goods store disagree.
                log.warning("FG ship post refused for shipment %s (%s/%s/%s): %s",
                            shipment_id, ln["style"], ln["colour"], ln["size"], msg)
        except Exception:
            log.warning("FG ship post crashed for shipment %s", shipment_id, exc_info=True)


def add_carton(shipment_id, data, user):
    """Add a packing-list line. Returns (ok, reason)."""
    raw_ctn = _f(data.get("cartons"), -1)
    raw_per = _f(data.get("qty_per_carton"), -1)
    cartons, per = int(raw_ctn), int(raw_per)
    # Both are counts of physical things, so both must be whole numbers.
    #   * 10.9 cartons is a keying error, and truncating it would silently drop the
    #     goods in the 0.9 from the packing list;
    #   * 60.5 garments per carton is the same error one level down, and it does not
    #     stay cosmetic: every document prints pieces with '{:,.0f}' (302.5 -> "302")
    #     while the invoice charges the UNROUNDED value (302.5 x 2.50 = 756.25 against
    #     a printed 302 x 2.50 = 755.00), so a customs invoice stops footing against
    #     its own pieces column.
    if (cartons != raw_ctn or per != raw_per
            or not (0 < cartons <= MAX_INT) or not (0 < per <= MAX_INT)):
        return False, "bad_qty"
    net = _optional(data, "net_weight")
    gross = _optional(data, "gross_weight")
    dims = [_optional(data, k) for k in ("length_cm", "width_cm", "height_cm")]
    # Bounded on both sides: a negative is a typo, and a value near the float ceiling
    # multiplies by the carton count into inf and poisons every total on the page.
    if not all(0 <= x <= MAX_INT for x in (net, gross, *dims)):
        return False, "bad_number"
    # Gross is net plus the carton itself, so gross < net is a keying error, not a shipment.
    if gross and gross < net:
        return False, "gross_below_net"
    conn = get_db()
    try:
        s = conn.execute("SELECT status FROM shp_shipments WHERE id=?", (shipment_id,)).fetchone()
        if not s:
            return False, "shipment_not_found"
        if s["status"] in LOCKED_STATUS:
            return False, "shipment_locked"        # the packing list is a customs document
        conn.execute(
            "INSERT INTO shp_cartons (shipment_id,carton_no,style,colour,size,qty_per_carton,"
            "cartons,net_weight,gross_weight,length_cm,width_cm,height_cm,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (shipment_id, data.get("carton_no"), (data.get("style") or "").strip(),
             (data.get("colour") or "").strip(), (data.get("size") or "").strip(),
             per, cartons, net, gross, dims[0], dims[1], dims[2], _now()))
        conn.commit()
        return True, "added"
    finally:
        conn.close()


def delete_carton(carton_id, user):
    """Correct a packing-list typo. Refused once the shipment has left.
    Returns (ok, reason, shipment_id) — the id is there so the caller can go back to the
    right page on refusal instead of trusting the Referer header."""
    conn = get_db()
    try:
        r = conn.execute("SELECT c.shipment_id, s.status FROM shp_cartons c "
                         "JOIN shp_shipments s ON s.id=c.shipment_id WHERE c.id=?",
                         (carton_id,)).fetchone()
        if not r:
            return False, "not_found", None
        if r["status"] in LOCKED_STATUS:
            return False, "shipment_locked", r["shipment_id"]
        conn.execute("DELETE FROM shp_cartons WHERE id=?", (carton_id,))
        conn.commit()
        return True, "deleted", r["shipment_id"]
    finally:
        conn.close()


# --- ordered vs packed vs shipped ----------------------------------------
def reconciliation(order_id=None):
    """Per order: ordered vs packed vs shipped.
      packed  = pieces on every live shipment (planned/packed/dispatched/delivered)
      shipped = pieces on shipments that have actually left (dispatched/delivered)
      over    = packed exceeds the order beyond tolerance — goods the buyer will not pay for
      short   = something has left and even everything packed cannot cover the order
    fulfil_pct is None (not 0) when the order quantity is 0/missing — no division by zero."""
    conn = get_db()
    try:
        q = ("SELECT s.id, s.order_id, s.status, "
             "COALESCE(SUM(c.qty_per_carton*c.cartons),0) AS pcs "
             "FROM shp_shipments s LEFT JOIN shp_cartons c ON c.shipment_id=s.id "
             "WHERE s.order_id IS NOT NULL AND s.status<>'cancelled'")
        args = []
        if order_id:
            q += " AND s.order_id=?"; args.append(order_id)
        q += " GROUP BY s.id, s.order_id, s.status"
        agg = {}
        for r in conn.execute(q, args).fetchall():
            a = agg.setdefault(r["order_id"], {"packed": 0.0, "shipped": 0.0, "shipments": 0})
            a["packed"] += _f(r["pcs"])
            a["shipments"] += 1
            if r["status"] in SHIPPED_STATUS:
                a["shipped"] += _f(r["pcs"])
        if not agg:
            return []
        ids = list(agg)
        try:
            orders = conn.execute(
                "SELECT id,order_no,buyer,style_ref,style_name,qty,status,ship_date FROM ord_orders "
                "WHERE id IN (" + ",".join(["?"] * len(ids)) + ")", ids).fetchall()
        except Exception:
            return []                      # orders module absent — nothing to reconcile against
        rows = []
        for o in orders:
            a = agg[o["id"]]
            ordered = _f(o["qty"])
            tol = ordered * QTY_TOLERANCE_PCT / 100.0
            row = dict(o)
            row.update({
                "packed": round(a["packed"], 3),
                "shipped": round(a["shipped"], 3),
                "shipments": a["shipments"],
                "balance": round(ordered - a["shipped"], 3),
                "fulfil_pct": round(100.0 * a["shipped"] / ordered, 1) if ordered > 0 else None,
                "over": ordered > 0 and a["packed"] > ordered + tol,
                "short": ordered > 0 and a["shipped"] > 0 and a["packed"] < ordered - tol,
            })
            rows.append(row)
        rows.sort(key=lambda r: (not (r["over"] or r["short"]), r["order_no"] or ""))
        return rows
    finally:
        conn.close()


def recon_sweep():
    """Raise one bell alert per order per flag. Idempotent via shp_recon_alerts. Never raises."""
    try:
        rows = [r for r in reconciliation() if r["over"] or r["short"]]
        conn = get_db()
    except Exception:
        return
    try:
        for r in rows:
            for flag in ("short", "over"):
                if not r[flag]:
                    continue
                seen = conn.execute("SELECT id FROM shp_recon_alerts WHERE order_id=? AND flag=?",
                                    (r["id"], flag)).fetchone()
                if seen:
                    continue
                gap = round(abs(_f(r["qty"]) - (r["shipped"] if flag == "short" else r["packed"])), 0)
                if flag == "short":
                    _bell(conn, "warning", f"Short shipment: {r['order_no']}",
                          f"{r['order_no']} ({r['buyer']}) — {r['shipped']:,.0f} of {_f(r['qty']):,.0f} pcs shipped, "
                          f"{gap:,.0f} short. Short shipments are a buyer chargeback.",
                          "/shipping/reconciliation")
                else:
                    _bell(conn, "warning", f"Over shipment: {r['order_no']}",
                          f"{r['order_no']} ({r['buyer']}) — {r['packed']:,.0f} pcs packed against "
                          f"{_f(r['qty']):,.0f} ordered. Over-shipped goods are usually unpaid.",
                          "/shipping/reconciliation")
                conn.execute("INSERT INTO shp_recon_alerts (order_id,flag,created_at) VALUES (?,?,?)",
                             (r["id"], flag, _now()))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# --- dashboard ------------------------------------------------------------
def dashboard():
    conn = get_db()
    try:
        today = date.today()
        t = str(today)
        soon = str(today + timedelta(days=ETD_SOON_DAYS))

        def one(sql, a=()):
            return conn.execute(sql, a).fetchone()["c"]

        live = "s.status<>'cancelled'"
        d = {
            "planned": one("SELECT COUNT(*) c FROM shp_shipments WHERE status IN ('planned','packed')"),
            "dispatched": one("SELECT COUNT(*) c FROM shp_shipments WHERE status='dispatched'"),
            "delivered": one("SELECT COUNT(*) c FROM shp_shipments WHERE status='delivered'"),
            "cartons": one("SELECT COALESCE(SUM(c.cartons),0) c FROM shp_cartons c "
                           "JOIN shp_shipments s ON s.id=c.shipment_id WHERE " + live),
            "pieces": one("SELECT COALESCE(SUM(c.qty_per_carton*c.cartons),0) c FROM shp_cartons c "
                          "JOIN shp_shipments s ON s.id=c.shipment_id WHERE " + live),
            "cbm": one("SELECT COALESCE(SUM(c.length_cm*c.width_cm*c.height_cm/1000000.0*c.cartons),0) c "
                       "FROM shp_cartons c JOIN shp_shipments s ON s.id=c.shipment_id WHERE " + live),
        }
        d["cbm"] = round(_f(d["cbm"]), 2)
        d["pieces"] = round(_f(d["pieces"]), 0)
        d["upcoming"] = [dict(r) for r in conn.execute(
            "SELECT id,shipment_no,buyer,destination,mode,etd,status FROM shp_shipments "
            "WHERE etd IS NOT NULL AND etd>=? AND etd<=? AND status NOT IN ('delivered','cancelled') "
            "ORDER BY etd ASC LIMIT 10", (t, soon)).fetchall()]
    finally:
        conn.close()
    rec = reconciliation()
    d["orders_short"] = sum(1 for r in rec if r["short"])
    d["orders_over"] = sum(1 for r in rec if r["over"])
    d["recon"] = [r for r in rec if r["short"] or r["over"]][:8]
    d["recent"] = list_shipments()[:8]
    return d
