"""
Traceability / ESG / Digital Product Passport services.

Three jobs: hold the supply-chain graph (partners + material lots linked by
`parent_lot_id`), hold MATERIAL certificates against it (factory certificates
stay in app/compliance), and assemble a Digital Product Passport per order with
a completeness score that says what is still MISSING.

Nothing here is a certified footprint or a certified claim — it is recorded data.
"""
import math
from datetime import date, datetime, timedelta, timezone

from app.db import get_db
from .constants import (EXPIRY_WARN_DAYS, MAX_CHAIN_DEPTH, DPP_POINTS, DPP_TOTAL,
                        DPP_MIN_PCT, DPP_ALERT_DAYS)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return date.today()


def _d(s):
    """Parse a YYYY-MM-DD (possibly with time) string to date, or None."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _f(v, default=0.0):
    """Coerce a form value to float. A blank/garbage entry must not 500 a page.
    inf/nan are rejected too: float('1e400') is inf, and one inf or nan in a
    column poisons every later sum, average and comparison silently."""
    try:
        s = str(v).strip()
        if s in ("", "None"):
            return default
        n = float(s)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def _qty(v):
    """A quantity / consumption figure. Negative is not a quantity: a -1000 kWh
    row would be SUMMED into the order total and silently understate the
    recorded footprint, and -500 kg of fabric is not a lot."""
    return max(0.0, _f(v))


def _pct(v):
    """A percentage claim printed on a product passport. 500% recycled content
    is a false green claim, so the stored value is bounded to 0..100."""
    return min(100.0, max(0.0, _f(v)))


# An id wider than a signed 64-bit integer cannot be BOUND as a parameter at all:
# SQLite raises OverflowError and PostgreSQL a DataError, so /trace/lots/<25 digits>
# used to 500 instead of 404. Out of range = no such row.
_MAX_ID = 2 ** 63 - 1


def _int(v):
    """A form/URL-supplied row id -> positive int within the DB integer range, or
    None. Never raises on junk (a URL like /trace/partners?tier=abc must not 500).

    isDECIMAL, not isdigit: str.isdigit() is True for '²' and '⁵' (superscripts
    are digits to Unicode) but int('²') raises ValueError, so /trace/partners?tier=²
    used to 500. isdecimal() is True for exactly the characters int() accepts —
    which still includes the Arabic-Indic digits this factory's staff may type."""
    s = str(v if v is not None else "").strip()
    if not s.isdecimal():
        return None
    n = int(s)
    return n if 0 < n <= _MAX_ID else None


def _iso(s):
    """Normalise a date to 'YYYY-MM-DD', or None when it is not a real date.
    A column that drives both an alarm and a SQL date comparison MUST be ISO:
    '31/12/2026' sorts above every ISO date, so a junk-dated certificate was
    counted as valid on the dashboard while its badge read 'unknown'."""
    d = _d(s)
    return str(d) if d else None


def _exists(conn, table, row_id):
    """Does this row id exist? `table` is always a literal from this module —
    never user input — so the f-string is safe; the id stays parametrised."""
    row_id = _int(row_id)
    if not row_id:
        return False
    return conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (row_id,)).fetchone() is not None


def _bell(conn, severity, title, message, link="/trace"):
    conn.execute(
        "INSERT INTO notifications (severity,module,title,message,link,created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "trace", title, message, link, _now()))


def days_left(d):
    dt = _d(d)
    return (dt - _today()).days if dt else None


def cert_status(row):
    """Status is DERIVED from BOTH dates — a stored 'valid' on a lapsed date lies.

    Order matters. A lapsed certificate is lapsed whatever its start date claims;
    a certificate whose validity has NOT STARTED yet is 'pending', never 'valid' —
    a GOTS scope certificate that begins next January does not cover the goods
    shipping this month, and printing 'valid' for it puts a false claim on a
    product passport. Only 'revoked' is a decision the dates cannot override."""
    if (row.get("status") or "") == "revoked":
        return "revoked"
    dl = days_left(row.get("valid_until"))
    if dl is None:
        return "unknown"
    if dl < 0:
        return "expired"
    start = _d(row.get("valid_from"))
    if start and start > _today():
        return "pending"
    return "expiring" if dl <= EXPIRY_WARN_DAYS else "valid"


# --- partners -------------------------------------------------------------
def list_partners(tier=None):
    conn = get_db()
    try:
        q = "SELECT * FROM trc_partners WHERE 1=1"
        args = []
        if tier:
            t = _int(tier)
            if t is None:
                return []              # ?tier=abc filters to nothing; it must not 500
            q += " AND tier=?"; args.append(t)
        q += " ORDER BY tier ASC, name ASC"
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()


def create_partner(data, user):
    conn = get_db()
    try:
        tier = int(_f(data.get("tier"), 2)) or 2
        tier = min(4, max(1, tier))          # tiers outside 1..4 are meaningless
        cur = conn.execute(
            "INSERT INTO trc_partners (name,tier,country,role,certifications,contact,"
            "contact_email,status,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (data.get("name") or "Partner", tier, data.get("country"), data.get("role"),
             data.get("certifications"), data.get("contact"), data.get("contact_email"),
             data.get("status") or "active", data.get("notes"), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# --- material lots + the ancestry walk ------------------------------------
_LOT_SELECT = ("SELECT l.*, p.name AS partner_name, p.tier AS tier, p.country AS partner_country, "
               "p.role AS partner_role FROM trc_lots l LEFT JOIN trc_partners p ON p.id=l.partner_id ")


def list_lots(partner_id=None):
    conn = get_db()
    try:
        q = _LOT_SELECT + "WHERE 1=1"
        args = []
        if partner_id:
            p = _int(partner_id)
            if p is None:
                return []
            q += " AND l.partner_id=?"; args.append(p)
        q += " ORDER BY l.id DESC"
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()


def _chain(conn, lot_id):
    """Walk a lot's ancestry to the deepest tier. Returns (rows, truncated).

    rows[0] is the lot itself, each next row is its parent. CYCLE-SAFE: a lot
    already visited stops the walk (bad data can point A->B->A and used to hang
    the request forever), and MAX_CHAIN_DEPTH bounds a pathologically long chain.
    `truncated` is True only when the walk was CUT SHORT by one of those guards —
    a missing/dangling parent row just ends the chain normally.
    """
    seen, rows, cur_id, depth = set(), [], _int(lot_id), 0
    while cur_id and cur_id not in seen and depth < MAX_CHAIN_DEPTH:
        seen.add(cur_id)
        depth += 1
        r = conn.execute(_LOT_SELECT + "WHERE l.id=?", (cur_id,)).fetchone()
        if not r:
            return rows, False        # dangling parent id — chain simply ends
        row = dict(r)
        row["depth"] = depth - 1
        rows.append(row)
        cur_id = row.get("parent_lot_id")
    return rows, bool(cur_id)


def lot_chain(lot_id):
    conn = get_db()
    try:
        return _chain(conn, lot_id)[0]
    finally:
        conn.close()


def _deepest_tier(nodes):
    tiers = [n.get("tier") for n in nodes if n.get("tier")]
    return max(tiers) if tiers else None


def get_lot(lot_id):
    conn = get_db()
    try:
        lot_id = _int(lot_id)
        rows, truncated = _chain(conn, lot_id)
        if not rows:
            return None
        certs = _certs_for(conn, rows)
        return {"lot": rows[0], "chain": rows, "truncated": truncated,
                "deepest_tier": _deepest_tier(rows), "certs": certs,
                "orders": [dict(r) for r in conn.execute(
                    "SELECT ol.*, o.order_no, o.buyer, o.style_name FROM trc_order_lots ol "
                    "LEFT JOIN ord_orders o ON o.id=ol.order_id WHERE ol.lot_id=?",
                    (lot_id,)).fetchall()] if _has_orders(conn) else []}
    finally:
        conn.close()


def create_lot(data, user):
    """Record a material lot. Returns the new id, or None when the form names a
    parent lot / supplier that does not exist — a dangling parent_lot_id would
    silently truncate the chain of custody with no warning anywhere."""
    conn = get_db()
    try:
        parent = _int(data.get("parent_lot_id"))
        partner = _int(data.get("partner_id"))
        if (data.get("parent_lot_id") and not _exists(conn, "trc_lots", parent)) or \
           (data.get("partner_id") and not _exists(conn, "trc_partners", partner)):
            return None
        cur = conn.execute(
            "INSERT INTO trc_lots (lot_ref,material,fibre_composition,partner_id,parent_lot_id,"
            "qty,uom,country_of_origin,received_date,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (data.get("lot_ref") or None, data.get("material"), data.get("fibre_composition"),
             partner, parent, _qty(data.get("qty")), data.get("uom") or "kg",
             data.get("country_of_origin"), data.get("received_date") or None,
             data.get("notes"), _now()))
        lot_id = cur.lastrowid
        if not (data.get("lot_ref") or "").strip():
            # Number from the row id, never COUNT(*)+1 — that repeats after a delete.
            conn.execute("UPDATE trc_lots SET lot_ref=? WHERE id=?", ("LOT-%05d" % lot_id, lot_id))
        conn.commit()
        return lot_id
    finally:
        conn.close()


# --- material certificates ------------------------------------------------
def _certs_for(conn, nodes):
    """Certificates covering any lot or any partner in this chain."""
    lot_ids = [n["id"] for n in nodes if n.get("id")]
    partner_ids = [n["partner_id"] for n in nodes if n.get("partner_id")]
    if not lot_ids and not partner_ids:
        return []
    clauses, args = [], []
    if lot_ids:
        clauses.append("c.lot_id IN (%s)" % ",".join("?" * len(lot_ids))); args += lot_ids
    if partner_ids:
        clauses.append("c.partner_id IN (%s)" % ",".join("?" * len(partner_ids))); args += partner_ids
    rows = conn.execute(
        "SELECT c.*, p.name AS partner_name FROM trc_certs c "
        "LEFT JOIN trc_partners p ON p.id=c.partner_id WHERE " + " OR ".join(clauses) +
        " ORDER BY c.valid_until ASC", args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["derived_status"] = cert_status(d)
        d["days"] = days_left(d.get("valid_until"))
        out.append(d)
    return out


def list_certs(standard=None):
    conn = get_db()
    try:
        q = ("SELECT c.*, p.name AS partner_name, l.lot_ref FROM trc_certs c "
             "LEFT JOIN trc_partners p ON p.id=c.partner_id "
             "LEFT JOIN trc_lots l ON l.id=c.lot_id WHERE 1=1")
        args = []
        if standard:
            q += " AND c.standard=?"; args.append(standard)
        q += " ORDER BY c.valid_until ASC"
        out = []
        for r in conn.execute(q, args).fetchall():
            d = dict(r)
            d["derived_status"] = cert_status(d)
            d["days"] = days_left(d.get("valid_until"))
            out.append(d)
        return out
    finally:
        conn.close()


def create_cert(data, user):
    """Register a MATERIAL certificate. Refused (False) if it names a partner or
    lot that does not exist — a certificate covering nothing is worse than none."""
    conn = get_db()
    try:
        partner, lot = _int(data.get("partner_id")), _int(data.get("lot_id"))
        if (data.get("partner_id") and not _exists(conn, "trc_partners", partner)) or \
           (data.get("lot_id") and not _exists(conn, "trc_lots", lot)):
            return False
        conn.execute(
            "INSERT INTO trc_certs (standard,cert_no,issuer,scope,partner_id,lot_id,valid_from,"
            "valid_until,status,doc_ref,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (data.get("standard") or "Other", data.get("cert_no"), data.get("issuer"),
             data.get("scope"), partner, lot,
             # Junk dates are stored as NULL ('unknown'), never as text that would
             # out-sort every real date in the dashboard's valid/expired counts.
             _iso(data.get("valid_from")), _iso(data.get("valid_until")),
             "valid", data.get("doc_ref"), data.get("notes"), _now()))
        conn.commit()
        return True
    finally:
        conn.close()


def renew_cert(cert_id, valid_until, user):
    """Extend a certificate's validity. Refused (False) unless the new date is a
    real date: a blank or unparseable one used to be written straight through,
    and a NULL valid_until drops the certificate out of the expiry sweep FOREVER
    (the sweep only looks at rows that have one) while the badge reads 'unknown'
    instead of 'expired'. A revoked certificate is a human decision — renewing
    must not quietly turn it back into a valid claim.

    Two more refusals, both of them alert bugs the dates alone did not catch:
      * a new date in the PAST is not a renewal. /trace/certs pre-fills the box
        with the certificate's CURRENT expiry, so one click on an expired row
        reported "Certificate renewed", cleared the expiry stamp and made the
        next sweep raise the SAME critical alert again — once per click.
      * an UNCHANGED date changes nothing, so it must not re-arm the alarm
        either; it duplicated the 60-day warning on every click."""
    cert_id = _int(cert_id)
    new_until = _iso(valid_until)
    if cert_id is None or new_until is None or _d(new_until) < _today():
        return False
    conn = get_db()
    try:
        row = conn.execute("SELECT status,valid_until FROM trc_certs WHERE id=?",
                           (cert_id,)).fetchone()
        if not row or (row["status"] or "") == "revoked":
            return False
        if new_until == (row["valid_until"] or ""):
            return True                  # nothing to do — and nothing to re-alert
        # Re-arm the alarm: a renewed certificate must be able to alert again.
        conn.execute("UPDATE trc_certs SET valid_until=?, status='valid', expiry_alerted=0, "
                     "updated_at=? WHERE id=?", (new_until, _now(), cert_id))
        conn.commit()
        return True
    finally:
        conn.close()


# --- order <-> lot linkage ------------------------------------------------
def _has_orders(conn):
    try:
        conn.execute("SELECT 1 FROM ord_orders LIMIT 1").fetchone()
        return True
    except Exception:
        return False


def _order_exists(conn, order_id):
    return bool(_has_orders(conn)) and _exists(conn, "ord_orders", _int(order_id))


def link_order_lot(order_id, lot_id, qty_used):
    conn = get_db()
    try:
        lot_id, order_id = _int(lot_id), _int(order_id)
        # Both ends must be real: a link to a ghost lot id shows up on the
        # passport as a linked lot that traces to nothing at all.
        if not lot_id or not _exists(conn, "trc_lots", lot_id) or not _order_exists(conn, order_id):
            return False
        dup = conn.execute("SELECT id FROM trc_order_lots WHERE order_id=? AND lot_id=?",
                           (order_id, lot_id)).fetchone()
        if dup:
            return False                      # double-apply must not duplicate the link
        conn.execute("INSERT INTO trc_order_lots (order_id,lot_id,qty_used,created_at) "
                     "VALUES (?,?,?,?)", (order_id, lot_id, _qty(qty_used), _now()))
        conn.commit()
        return True
    finally:
        conn.close()


def unlink_order_lot(order_id, link_id):
    """Remove a mis-linked lot. Deletes the LINK row only — never the lot — and
    only when that link really belongs to the order in the URL (otherwise a
    stale or hand-edited form deletes another order's chain of custody)."""
    conn = get_db()
    try:
        link_id, order_id = _int(link_id), _int(order_id)
        if not link_id or not order_id:
            return False
        row = conn.execute("SELECT id FROM trc_order_lots WHERE id=? AND order_id=?",
                           (link_id, order_id)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM trc_order_lots WHERE id=?", (link_id,))
        # Unlinking is the ONLY write in this module that LOWERS completeness, so
        # it re-arms the incomplete-passport alarm. Without it an order that was
        # alerted once, then completed, then broken again by an unlink stayed
        # silent forever — incomplete_alerted was still 1 from the first alert.
        conn.execute("UPDATE trc_passports SET incomplete_alerted=0 WHERE order_id=?", (order_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# --- ESG (recorded, not certified) ----------------------------------------
def _intensities(tot):
    """Per-garment intensity = recorded total / pieces. No pieces = no intensity;
    returning None is the honest answer, 0 would read as 'we use no water'."""
    g = tot.get("garments") or 0
    if g <= 0:
        return {"energy_per_pc": None, "water_l_per_pc": None, "waste_g_per_pc": None}
    return {
        "energy_per_pc": round((tot.get("energy_kwh") or 0) / g, 3),
        "water_l_per_pc": round(((tot.get("water_m3") or 0) * 1000) / g, 2),   # m3 -> litres
        "waste_g_per_pc": round(((tot.get("waste_kg") or 0) * 1000) / g, 1),   # kg -> grams
    }


def _esg_totals(rows):
    """Sum the consumption columns, but take the LARGEST pieces figure as the
    denominator: several records for one order describe the SAME pieces at
    different stages, so summing garments would halve every intensity."""
    tot = {"energy_kwh": 0.0, "water_m3": 0.0, "waste_kg": 0.0, "garments": 0.0}
    for r in rows:
        tot["energy_kwh"] += r.get("energy_kwh") or 0
        tot["water_m3"] += r.get("water_m3") or 0
        tot["waste_kg"] += r.get("waste_kg") or 0
        tot["garments"] = max(tot["garments"], r.get("garments") or 0)
    tot.update(_intensities(tot))
    return tot


def _esg_for_order(conn, order_id):
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM trc_esg WHERE order_id=? ORDER BY id DESC", (order_id,)).fetchall()]
    tot = _esg_totals(rows)
    tot["rows"] = rows
    return tot


def list_esg(limit=100):
    conn = get_db()
    try:
        q = "SELECT e.* FROM trc_esg e ORDER BY e.id DESC LIMIT ?"
        rows = [dict(r) for r in conn.execute(q, (limit,)).fetchall()]
        if _has_orders(conn):
            for r in rows:
                if r.get("order_id"):
                    o = conn.execute("SELECT order_no FROM ord_orders WHERE id=?",
                                     (r["order_id"],)).fetchone()
                    r["order_no"] = o["order_no"] if o else None
        for r in rows:
            r.update(_intensities(r))
        return rows
    finally:
        conn.close()


def record_esg(data, user):
    """Record metered consumption. order_id may be NULL (a site-level period
    record), but if one IS given it must be a real order — an ESG row against a
    ghost order is invisible everywhere and never counted again."""
    conn = get_db()
    try:
        oid = _int(data.get("order_id"))
        if data.get("order_id") and not _order_exists(conn, oid):
            return False
        figures = (_qty(data.get("energy_kwh")), _qty(data.get("water_m3")),
                   _qty(data.get("waste_kg")), _qty(data.get("garments")))
        # A double-clicked form must not double the recorded footprint: the
        # consumption columns are SUMMED per order while the denominator is the
        # LARGEST garments figure, so a duplicated row doubles every per-garment
        # intensity printed on a passport. An identical row seconds old is a
        # resubmit, not a second meter reading; an hour later it is real data.
        recent = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
        dup = conn.execute(
            "SELECT id FROM trc_esg WHERE " +
            ("order_id IS NULL" if oid is None else "order_id=?") +
            " AND COALESCE(period,'')=? AND COALESCE(label,'')=? AND energy_kwh=? "
            "AND water_m3=? AND waste_kg=? AND garments=? AND created_at>=?",
            ([] if oid is None else [oid]) +
            [data.get("period") or "", data.get("label") or "", *figures, recent]).fetchone()
        if dup:
            return True                       # already recorded — a no-op, not an error
        conn.execute(
            "INSERT INTO trc_esg (order_id,period,label,energy_kwh,water_m3,waste_kg,garments,"
            "source,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (oid, data.get("period") or None, data.get("label"), *figures,
             data.get("source"), data.get("notes"), _now()))
        conn.commit()
        return True
    finally:
        conn.close()


# --- the Digital Product Passport ----------------------------------------
def _completeness(header, nodes, certs, deepest, esg, linked):
    """% of the required DPP data points present. The MISSING list is the point:
    it is the factory's to-do list before the ESPR passport is filed."""
    # Which tiers are actually NAMED in the chain. tier3 must be a membership
    # test, not `deepest >= 3`: a chain that jumps from the fabric mill (2) straight
    # to the cotton farm (4) has no spinner/dyehouse in it at all, yet `>=3` scored
    # BOTH tier points from that single fact — 20% of the passport for one node, and
    # the missing list never told the factory to go and identify the spinner.
    chain_tiers = {n.get("tier") for n in nodes if n.get("tier")}
    ok = {
        "lots_linked": bool(linked),
        "composition": bool(nodes) and all((n.get("fibre_composition") or "").strip() for n in nodes),
        "supplier": bool(nodes) and all(n.get("partner_id") for n in nodes),
        "tier3": 3 in chain_tiers,
        # tier 4 is the top of the scale, so anything >=4 is the fibre origin.
        "tier4": bool(deepest) and deepest >= 4,
        "certificates": any(c.get("derived_status") in ("valid", "expiring") for c in certs),
        "origin": bool((header.get("country_of_origin") or "").strip()),
        "care": bool((header.get("care_instructions") or "").strip()),
        "recycling": bool((header.get("recycling_info") or "").strip()),
        "footprint": bool((esg.get("garments") or 0) > 0 and
                          ((esg.get("energy_kwh") or 0) or (esg.get("water_m3") or 0)
                           or (esg.get("waste_kg") or 0))),
    }
    points = [{"key": k, "i18n": i18n, "ok": bool(ok.get(k))} for k, i18n in DPP_POINTS]
    present = sum(1 for p in points if p["ok"])
    return {"pct": round(100 * present / DPP_TOTAL), "present": present,
            "total": DPP_TOTAL, "points": points,
            "missing": [p["i18n"] for p in points if not p["ok"]]}


def _passport(conn, order_id):
    """Assemble the read-only passport summary for one order. Returns None if the
    order does not exist (or the orders module is not installed)."""
    order = None
    order_id = _int(order_id)
    if order_id and _has_orders(conn):
        r = conn.execute("SELECT * FROM ord_orders WHERE id=?", (order_id,)).fetchone()
        order = dict(r) if r else None
    if not order:
        return None
    h = conn.execute("SELECT * FROM trc_passports WHERE order_id=? ORDER BY id LIMIT 1",
                     (order_id,)).fetchone()
    header = dict(h) if h else {}
    linked = [dict(r) for r in conn.execute(
        "SELECT ol.id AS link_id, ol.qty_used, ol.lot_id FROM trc_order_lots ol "
        "WHERE ol.order_id=? ORDER BY ol.id", (order_id,)).fetchall()]

    chains, nodes, seen_nodes = [], [], set()
    truncated = False
    for l in linked:
        rows, cut = _chain(conn, l["lot_id"])
        truncated = truncated or cut
        if rows:
            chains.append({"link_id": l["link_id"], "qty_used": l["qty_used"], "rows": rows})
            for n in rows:
                if n["id"] not in seen_nodes:
                    seen_nodes.add(n["id"])
                    nodes.append(n)
    certs = _certs_for(conn, nodes)
    deepest = _deepest_tier(nodes)
    esg = _esg_for_order(conn, order_id)
    composition = []
    for n in nodes:
        c = (n.get("fibre_composition") or "").strip()
        if c and c not in composition:
            composition.append(c)
    origins = []
    for n in nodes:
        c = (n.get("country_of_origin") or n.get("partner_country") or "").strip()
        if c and c not in origins:
            origins.append(c)
    return {
        "order": order, "header": header, "chains": chains, "nodes": nodes,
        "certs": certs, "deepest_tier": deepest, "truncated": truncated,
        "composition": composition, "material_origins": origins, "esg": esg,
        "completeness": _completeness(header, nodes, certs, deepest, esg, linked),
    }


def passport(order_id):
    conn = get_db()
    try:
        return _passport(conn, order_id)
    finally:
        conn.close()


def save_passport(order_id, data, user):
    """Upsert the DPP header for an order. Safe to call twice (no duplicate row).
    Refused (False) for an order that does not exist — the GET 404s, so a POST
    to the same URL must not quietly create a passport for nothing."""
    conn = get_db()
    try:
        order_id = _int(order_id)
        if not _order_exists(conn, order_id):
            return False
        row = conn.execute("SELECT id FROM trc_passports WHERE order_id=? ORDER BY id LIMIT 1",
                           (order_id,)).fetchone()
        # Only the columns the declaration form actually posts. `notes` is NOT one
        # of them, and writing `data.get("notes") or None` for a field that is never
        # submitted silently NULLed it on every save.
        vals = (data.get("country_of_origin") or None, data.get("care_instructions") or None,
                data.get("recycling_info") or None, _pct(data.get("recycled_content_pct")))
        if row:
            # Re-arm the incompleteness alarm: the data just changed.
            conn.execute("UPDATE trc_passports SET country_of_origin=?, care_instructions=?, "
                         "recycling_info=?, recycled_content_pct=?, incomplete_alerted=0, "
                         "updated_at=? WHERE id=?", (*vals, _now(), row["id"]))
        else:
            conn.execute("INSERT INTO trc_passports (order_id,country_of_origin,care_instructions,"
                         "recycling_info,recycled_content_pct,created_at) VALUES (?,?,?,?,?,?)",
                         (order_id, *vals, _now()))
        conn.commit()
        return True
    finally:
        conn.close()


def passport_list(limit=60):
    """One row per live order with its completeness, most urgent first. ponytail:
    N+1 chain walks by design — a few hundred orders is nothing; add a cached pct
    column if it ever is.

    Two SQL traps this query used to fall into, both silent:
      * `status NOT IN (...)` is NULL for an order whose status is NULL (the
        orders edit form writes NULL for a blank field), so that order dropped
        out of the register and out of the sweep with no trace.
      * `ORDER BY ship_date` puts NULLs FIRST on SQLite and LAST on PostgreSQL.
        With the list capped, dev and production returned DIFFERENT orders from
        the same data, and a yard of undated orders pushed every real one off the
        dashboard — which then reported an average completeness of 0%.
    """
    conn = get_db()
    try:
        if not _has_orders(conn):
            return []
        out = []
        for o in conn.execute(
                "SELECT id FROM ord_orders WHERE COALESCE(status,'') NOT IN ('cancelled','closed') "
                "ORDER BY COALESCE(NULLIF(ship_date,''),'9999-12-31') ASC, id DESC LIMIT ?",
                (limit,)).fetchall():
            p = _passport(conn, o["id"])
            if not p:
                continue
            out.append({"order": p["order"], "pct": p["completeness"]["pct"],
                        "missing": len(p["completeness"]["missing"]),
                        "lots": len(p["nodes"]), "deepest_tier": p["deepest_tier"]})
        return out
    finally:
        conn.close()


# --- sweep: material-certificate expiry + incomplete passports near ship ---
def sweep():
    """Bell alerts. Never raises.

    A certificate gets AT MOST TWO alerts in its life: one warning when it enters
    the 60-day window, and one critical when it actually lapses. The lapse must
    survive the warning — expiry_alerted=1 alone used to swallow it, so the one
    event that matters (the material claim is no longer covered) was never raised
    for any certificate that had already been warned about. `status='expired'` is
    the stamp that makes the second alert idempotent; renew_cert clears both.
    """
    try:
        conn = get_db()
    except Exception:
        return
    try:
        for c in conn.execute(
                "SELECT id,standard,cert_no,valid_until,expiry_alerted FROM trc_certs "
                "WHERE valid_until IS NOT NULL AND COALESCE(status,'')<>'revoked' "
                "AND (expiry_alerted=0 OR COALESCE(status,'')<>'expired')").fetchall():
            dl = days_left(c["valid_until"])
            if dl is None:
                continue
            label = f"{c['standard']}{(' ' + c['cert_no']) if c['cert_no'] else ''}"
            if dl < 0:
                _bell(conn, "critical", f"Material certificate expired: {c['standard']}",
                      f"{label} expired on {c['valid_until']} — the material claim is no longer covered.",
                      "/trace/certs")
                conn.execute("UPDATE trc_certs SET status='expired', expiry_alerted=1 WHERE id=?", (c["id"],))
            elif dl <= EXPIRY_WARN_DAYS and not (c["expiry_alerted"] or 0):
                _bell(conn, "warning", f"Material certificate expiring: {c['standard']}",
                      f"{label} expires in {dl} days ({c['valid_until']}) — request the renewal now.",
                      "/trace/certs")
                conn.execute("UPDATE trc_certs SET expiry_alerted=1 WHERE id=?", (c["id"],))
        # Bank the certificate alerts before touching the orders module. They are
        # the alerts that matter and they are already decided; without this commit
        # any failure in the passport pass below rolls the whole sweep back and the
        # expiry warnings are lost (on PostgreSQL a missing ord_orders aborts the
        # transaction, so the rollback is silent and permanent).
        conn.commit()

        # Passports that are materially incomplete on an order about to ship.
        if _has_orders(conn):
            horizon = str(_today() + timedelta(days=DPP_ALERT_DAYS))
            for o in conn.execute(
                    "SELECT id,order_no,ship_date FROM ord_orders WHERE ship_date IS NOT NULL "
                    "AND ship_date<>'' AND ship_date<=? "
                    # NULL status => `NOT IN` is NULL => the order silently never
                    # gets an incomplete-passport alarm. COALESCE keeps it in.
                    "AND COALESCE(status,'') NOT IN ('shipped','closed','cancelled')",
                    (horizon,)).fetchall():
                row = conn.execute("SELECT id,incomplete_alerted FROM trc_passports WHERE order_id=? "
                                   "ORDER BY id LIMIT 1", (o["id"],)).fetchone()
                if row and (row["incomplete_alerted"] or 0):
                    continue
                p = _passport(conn, o["id"])
                if not p or p["completeness"]["pct"] >= DPP_MIN_PCT:
                    continue
                _bell(conn, "warning", f"Product passport incomplete: {o['order_no']}",
                      f"{o['order_no']} ships {o['ship_date']} with a "
                      f"{p['completeness']['pct']}% passport ({len(p['completeness']['missing'])} "
                      f"data points missing).", f"/trace/passport/{o['id']}")
                if row:
                    conn.execute("UPDATE trc_passports SET incomplete_alerted=1 WHERE id=?", (row["id"],))
                else:
                    # Create the header row purely so the alarm flag has a home.
                    conn.execute("INSERT INTO trc_passports (order_id,incomplete_alerted,created_at) "
                                 "VALUES (?,?,?)", (o["id"], 1, _now()))
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
        def one(sql, args=()):
            return conn.execute(sql, args).fetchone()["c"]
        t = str(_today())
        soon = str(_today() + timedelta(days=EXPIRY_WARN_DAYS))
        # `status!='revoked'` is NULL — not TRUE — for a row whose status is NULL,
        # so an imported certificate with no status used to vanish from every KPI
        # while the certificates page still showed it. COALESCE keeps the counts
        # equal to the badges the user reads on /trace/certs.
        # `valid_from<=?` mirrors the 'pending' branch of cert_status(); create_cert
        # is the only writer and stores _iso() (ISO date or NULL), so the text
        # comparison is a real date comparison.
        live = "COALESCE(status,'')<>'revoked' AND valid_until IS NOT NULL"
        started = " AND (valid_from IS NULL OR valid_from<=?)"
        d = {
            "partners_total": one("SELECT COUNT(*) c FROM trc_partners"),
            "lots_total": one("SELECT COUNT(*) c FROM trc_lots"),
            "lots_traced": one("SELECT COUNT(*) c FROM trc_lots WHERE parent_lot_id IS NOT NULL"),
            "certs_valid": one("SELECT COUNT(*) c FROM trc_certs WHERE " + live +
                               " AND valid_until>?" + started, (soon, t)),
            "certs_expiring": one("SELECT COUNT(*) c FROM trc_certs WHERE " + live +
                                  " AND valid_until<=? AND valid_until>=?" + started, (soon, t, t)),
            "certs_expired": one("SELECT COUNT(*) c FROM trc_certs WHERE " + live +
                                 " AND valid_until<?", (t,)),
        }
        d["by_tier"] = {r["tier"]: r["c"] for r in conn.execute(
            "SELECT tier, COUNT(*) AS c FROM trc_partners GROUP BY tier").fetchall()}
        # The watchlist is an ACTION list, so it is bounded on both sides: without
        # the lower bound the ten oldest expired certificates in the register sit
        # in it forever and push the one expiring next week off the panel.
        since = str(_today() - timedelta(days=EXPIRY_WARN_DAYS))
        watch = []
        for r in conn.execute(
            "SELECT c.standard, c.cert_no, c.valid_until, p.name AS partner_name FROM trc_certs c "
            "LEFT JOIN trc_partners p ON p.id=c.partner_id WHERE COALESCE(c.status,'')<>'revoked' "
            "AND c.valid_until IS NOT NULL AND c.valid_until<=? AND c.valid_until>=? "
            "ORDER BY c.valid_until ASC LIMIT 10", (soon, since)).fetchall():
            w = dict(r)
            w["days"] = days_left(w.get("valid_until"))
            watch.append(w)
        d["watch"] = watch
        conn.close()
        conn = None
        rows = passport_list(limit=40)
        d["passports"] = rows[:8]
        d["passports_total"] = len(rows)
        d["avg_completeness"] = round(sum(r["pct"] for r in rows) / len(rows)) if rows else 0
        d["passports_weak"] = sum(1 for r in rows if r["pct"] < DPP_MIN_PCT)
        return d
    finally:
        if conn is not None:
            conn.close()


# --- dataset export (shared CSV/JSON contract) -----------------------------
def _txt(v):
    """Text cell. Never None: the CSV writer would print an empty cell but the
    JSON would carry null, and the two views of one dataset must agree."""
    return "" if v is None else str(v)


def _num(v):
    """Numeric cell to 3dp (this module carries quantities and consumption, never
    money). Blank stays blank — but 0 is a real reading (a month of 0 waste) and
    must stay a 0, not become an empty cell."""
    if v is None or v == "":
        return ""
    return round(_f(v), 3)


def export_dataset(key):
    """key -> (headers, rows) for the shared exporter, or (None, None).

    Keys: partners | lots | certificates | passports | esg.

    Every dataset is built from the module's own read functions, so a download
    can never disagree with the page it came from — the certificate status here
    is the same DERIVED status the badge shows, not the stored column.

    partners / lots / certificates are exported COMPLETE and uncapped: these hold
    one row per real supplier, physical material batch and certificate, and a
    truncated chain of custody is not a weaker audit document, it is a wrong one.
    The two order-driven datasets are capped (see below) because each row costs an
    ancestry walk.
    """
    if key == "partners":
        return (["Partner", "Tier", "Country", "Role", "Certifications Held",
                 "Contact", "Contact Email", "Status", "Notes"],
                [[_txt(p.get("name")), p.get("tier") or "", _txt(p.get("country")),
                  _txt(p.get("role")), _txt(p.get("certifications")),
                  _txt(p.get("contact")), _txt(p.get("contact_email")),
                  _txt(p.get("status")), _txt(p.get("notes"))]
                 for p in list_partners()])

    if key == "lots":
        lots = list_lots()
        # The parent's REFERENCE, not its id: the export is read by people and by
        # brand auditors, and "LOT-YRN-0101" is the chain link they can follow.
        ref = {l["id"]: (l.get("lot_ref") or l["id"]) for l in lots}
        return (["Lot Ref", "Material", "Fibre Composition", "Supplier", "Tier",
                 "Qty", "Unit", "Country of Origin", "Received", "Made From Lot"],
                [[_txt(l.get("lot_ref") or l["id"]), _txt(l.get("material")),
                  _txt(l.get("fibre_composition")), _txt(l.get("partner_name")),
                  l.get("tier") or "", _num(l.get("qty")), _txt(l.get("uom")),
                  _txt(l.get("country_of_origin")), _txt(l.get("received_date")),
                  _txt(ref.get(l.get("parent_lot_id"), ""))]
                 for l in lots])

    if key == "certificates":
        return (["Standard", "Certificate No", "Issuer", "Scope", "Partner",
                 "Lot Ref", "Valid From", "Valid Until", "Status", "Days Left",
                 "Document Ref"],
                [[_txt(c.get("standard")), _txt(c.get("cert_no")), _txt(c.get("issuer")),
                  _txt(c.get("scope")), _txt(c.get("partner_name")), _txt(c.get("lot_ref")),
                  _txt(c.get("valid_from")), _txt(c.get("valid_until")),
                  _txt(c.get("derived_status")),
                  "" if c.get("days") is None else c["days"], _txt(c.get("doc_ref"))]
                 for c in list_certs()])

    if key == "passports":
        # 500, not the register page's 60: this is the compliance roll-up someone
        # takes into a buyer meeting, so it must cover the whole live order book.
        return (["Order No", "Buyer", "Style", "Ship Date", "Chain Nodes",
                 "Deepest Tier", "Completeness %", "Missing Data Points", "Below Target"],
                [[_txt(r["order"].get("order_no")), _txt(r["order"].get("buyer")),
                  _txt(r["order"].get("style_name")), _txt(r["order"].get("ship_date")),
                  r["lots"], r["deepest_tier"] or "", r["pct"], r["missing"],
                  "yes" if r["pct"] < DPP_MIN_PCT else "no"]
                 for r in passport_list(limit=500)])

    if key == "esg":
        return (["Scope", "Order No", "Period", "Energy kWh", "Water m3", "Waste kg",
                 "Garments", "kWh per Garment", "Water L per Garment",
                 "Waste g per Garment", "Source"],
                [[_txt(r.get("label") or r.get("period")), _txt(r.get("order_no")),
                  _txt(r.get("period")), _num(r.get("energy_kwh")), _num(r.get("water_m3")),
                  _num(r.get("waste_kg")), _num(r.get("garments")),
                  _num(r.get("energy_per_pc")), _num(r.get("water_l_per_pc")),
                  _num(r.get("waste_g_per_pc")), _txt(r.get("source"))]
                 for r in list_esg(limit=2000)])

    return (None, None)
