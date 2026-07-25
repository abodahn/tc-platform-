"""
Cut room services — lays, marker efficiency, fabric utilisation and the
cut-vs-order reconciliation.

We do NOT nest markers (that is CAD: Lectra/Gerber). We measure what the cut room
actually did with the fabric, because fabric is 60-70% of garment cost and every
point of marker efficiency or end loss is money that is already spent.

Every formula guards its own denominator and returns None -- never 0 -- when the
number is undefined, so a missing input can never masquerade as a good result.
"""
from datetime import date, datetime

from app.db import get_db
from .constants import FABRIC_VARIANCE_ALERT_PCT


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


# No lay involves a trillion metres, plies or pieces. Anything past this is a
# typo or an attack, never a spread, and multiplying two of them overflows to inf.
_MAX_INPUT = 1e12


def _f(v, default=0.0):
    """Form fields arrive as raw strings: '', None and 'abc' must all degrade to
    the default instead of raising inside a money/quantity formula.

    float() also happily parses 'nan', 'inf' and '1e400', and every quantity here
    is a physical measurement that cannot be negative. Left unfiltered, one
    crafted POST puts inf in a REAL column, and from then on every sum, KPI and
    JSON export of the whole module reads inf/NaN (NaN is not even valid JSON) —
    and int(inf)/int(nan) raises, so the pages 500. The comparison rejects nan
    for free, because every comparison against nan is False."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if 0.0 <= f <= _MAX_INPUT else default


def _i(v, default=0):
    return int(_f(v, default))


def _pct(num, den, nd=2):
    """num/den as a percentage, or None when the denominator is unusable."""
    return round(num / den * 100.0, nd) if den else None


def _cell(v):
    """Export cell: None -> "" so the CSV cell is blank and the JSON field a plain
    string. Half the metrics here are legitimately undefined (every denominator is
    guarded) and a null in a spreadsheet reads as a bug."""
    return "" if v is None else v


def _bell(conn, severity, title, message, link="/cutroom"):
    conn.execute(
        "INSERT INTO notifications (severity,module,title,message,link,created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "cutroom", title, message, link, _now()))


# --- the numbers ----------------------------------------------------------
def lay_metrics(lay):
    """Every fabric number derived from one lay row. Pure: no DB, no rounding of
    inputs, safe against 0 / None / '' / non-numeric in every field.

    pieces_cut     = plies x pieces_per_ply
    theoretical_m  = marker_length x plies          (the floor: the marker itself)
    planned_m      = (marker_length + end_allow) x plies   (end loss at each ply end)
    fabric_used_m  = measured actual when >0, else planned
    cons_per_gmt   = fabric_used / pieces_cut
    marker_eff_%   = pattern-piece area / (marker_length x marker_width) x 100
    utilisation_%  = theoretical / fabric_used x 100
    waste_%        = 100 - utilisation      (identical to (used-theo)/used x 100)
    """
    plies = _i(lay.get("plies"))
    ppp = _i(lay.get("pieces_per_ply"))
    ml = _f(lay.get("marker_length_m"))
    mw = _f(lay.get("marker_width_cm")) / 100.0        # markers are entered in cm
    end = _f(lay.get("end_allow_m"))

    pieces = plies * ppp
    theoretical = ml * plies
    planned = (ml + end) * plies
    actual = _f(lay.get("actual_fabric_m"))
    used = actual if actual > 0 else planned

    # Marker efficiency from CAD area when we have it, otherwise whatever the cut
    # room typed in. An area larger than the marker rectangle is a data error, so
    # it is shown as >100% rather than silently clamped to a plausible-looking 100.
    area = _f(lay.get("marker_area_m2"))
    rect = ml * mw
    eff = _pct(area, rect, 1) if (area > 0 and rect > 0) else None
    if eff is None:
        entered = _f(lay.get("marker_eff_pct"))
        eff = round(entered, 1) if entered > 0 else None

    util = _pct(theoretical, used)
    return {
        "pieces_cut": pieces,
        "theoretical_m": round(theoretical, 2),
        "planned_m": round(planned, 2),
        "fabric_used_m": round(used, 2),
        "measured": actual > 0,
        "cons_per_gmt": round(used / pieces, 4) if pieces else None,
        "marker_eff_pct": eff,
        "utilisation_pct": util,
        "waste_pct": round(100.0 - util, 2) if util is not None else None,
    }


def _view(lay):
    out = dict(lay)          # sqlite3.Row has no .get(); metrics needs a real mapping
    out.update(lay_metrics(out))
    return out


def _summarise(order, lays, planned_cpg):
    """Roll a list of lay views up to one order: cut-vs-ordered reconciliation and
    fabric variance against the BOM plan.

    balance     = order_qty - pieces_cut   (>0 short-cut, <0 over-cut -- both cost money)
    actual_cpg  = total fabric used / total pieces cut
    variance_%  = (actual_cpg - planned_cpg) / planned_cpg x 100   (>0 = unfavourable)
    variance_m  = (actual_cpg - planned_cpg) x pieces_cut          (metres already burnt)
    """
    pieces = sum(l["pieces_cut"] for l in lays)
    used = round(sum(l["fabric_used_m"] for l in lays), 2)
    theo = round(sum(l["theoretical_m"] for l in lays), 2)
    qty = _f((order or {}).get("qty"))
    actual_cpg = round(used / pieces, 4) if pieces else None

    # Marker efficiency is averaged WEIGHTED BY FABRIC USED: a 500 m lay and a
    # 20 m lay are not equal opinions about how well the fabric was nested.
    wsum = sum(l["fabric_used_m"] for l in lays if l["marker_eff_pct"] is not None)
    weff = sum(l["marker_eff_pct"] * l["fabric_used_m"]
               for l in lays if l["marker_eff_pct"] is not None)

    var_pct = var_m = None
    if planned_cpg and actual_cpg is not None:
        var_pct = round((actual_cpg - planned_cpg) / planned_cpg * 100.0, 2)
        var_m = round((actual_cpg - planned_cpg) * pieces, 2)
    util = _pct(theo, used)
    return {
        "order": order, "order_id": (order or {}).get("id"), "lays": lays,
        "lay_count": len(lays),
        "pieces_cut": pieces, "fabric_used_m": used, "theoretical_m": theo,
        "order_qty": qty,
        "cut_pct": _pct(pieces, qty, 1),
        "balance": round(qty - pieces, 2) if qty else None,   # >0 short, <0 over
        "actual_cpg": actual_cpg,
        "planned_cpg": planned_cpg,
        "variance_pct": var_pct,
        "variance_m": var_m,
        "flag": None if var_pct is None else ("unfavourable" if var_pct > 0 else "favourable"),
        "marker_eff_pct": round(weff / wsum, 1) if wsum else None,
        "utilisation_pct": util,
        "waste_pct": round(100.0 - util, 2) if util is not None else None,
    }


# --- plan side (costing BOM), read defensively ----------------------------
def _planned_cpg_map(conn, order_ids):
    """Planned fabric consumption per garment, in METRES, from each order's BOM.

    Only metre-denominated FABRIC lines count: knit BOMs are written in kg and a
    kg-vs-metre comparison is not a variance, it is a wrong number. The BOM's own
    allowance_pct inflates the plan, which is right -- it is the cutting wastage
    the costing sheet already paid for, so the comparison is like for like.
        planned_cpg = MAX over fabric lines of  consumption x (1 + allowance_pct/100)
    MAX, not SUM: the plan must match what the lays actually consumed, and lays are
    spread from the SHELL fabric. Adding the pocketing/lining lines would compare a
    two-fabric plan against a one-fabric actual and report a fake saving.
    Returns {} when the costing module is not installed: variance simply goes dark.
    """
    out = {}
    if not order_ids:
        return out
    try:
        rows = conn.execute("SELECT order_id, consumption, allowance_pct FROM cst_bom_lines "
                            "WHERE kind='fabric' AND LOWER(uom)='m'").fetchall()
    except Exception:
        # PostgreSQL: a failed statement poisons the whole transaction, so without
        # this the caller's later writes (variance_sweep's bell + dedup UPDATE) all
        # fail silently on any deployment where costing is not installed.
        try:
            conn.rollback()
        except Exception:
            pass
        return out
    for r in rows:
        oid = r["order_id"]
        if oid in order_ids:
            cpg = round(_f(r["consumption"]) * (1.0 + _f(r["allowance_pct"]) / 100.0), 4)
            out[oid] = max(out.get(oid, 0.0), cpg)
    return out


def warehouse_rolls(shade_lot=None):
    """Rolls to pick from, when the warehouse module is installed. Recording a roll
    on a lay is TRACEABILITY ONLY -- warehouse owns stock movement, and issuing the
    same metres from two modules is how stock stops reconciling."""
    try:
        from app.warehouse import services as wh_svc
    except ImportError:
        return []
    try:
        return wh_svc.list_rolls(shade_lot=shade_lot or None)[:200]
    except Exception:
        return []


# --- reads ----------------------------------------------------------------
def list_lays(order_id=None, status=None):
    conn = get_db()
    try:
        q = ("SELECT l.*, o.order_no, o.buyer, o.style_ref FROM cut_lays l "
             "LEFT JOIN ord_orders o ON o.id=l.order_id WHERE 1=1")
        args = []
        if order_id:
            q += " AND l.order_id=?"; args.append(order_id)
        if status:
            q += " AND l.status=?"; args.append(status)
        q += " ORDER BY COALESCE(l.cut_date,'') DESC, l.id DESC"
        return [_view(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()


def get_lay(lay_id):
    conn = get_db()
    try:
        r = conn.execute(
            "SELECT l.*, o.order_no, o.buyer, o.style_ref, o.style_name, o.qty AS order_qty "
            "FROM cut_lays l LEFT JOIN ord_orders o ON o.id=l.order_id WHERE l.id=?",
            (lay_id,)).fetchone()
        if not r:
            return None
        lay = _view(r)
        rolls = [dict(x) for x in conn.execute(
            "SELECT * FROM cut_lay_rolls WHERE lay_id=? ORDER BY id", (lay_id,)).fetchall()]
        booked = round(sum(_f(x["meters"]) for x in rolls), 2)
        planned = _planned_cpg_map(conn, {lay["order_id"]}).get(lay["order_id"])
        cpg = lay["cons_per_gmt"]
        return {"lay": lay, "rolls": rolls, "rolls_m": booked,
                # unbooked metres: fabric the lay consumed that no roll accounts for
                "rolls_gap_m": round(lay["fabric_used_m"] - booked, 2),
                "planned_cpg": planned,
                "variance_pct": (round((cpg - planned) / planned * 100.0, 2)
                                 if (planned and cpg is not None) else None)}
    finally:
        conn.close()


def order_summary(order_id):
    """Cut-vs-ordered reconciliation for one order, or None if the order is gone."""
    conn = get_db()
    try:
        o = conn.execute("SELECT * FROM ord_orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return None
        # COALESCE, not a bare !=: in SQL, NULL!='cancelled' is NULL, i.e. FALSE, so a
        # lay with no status would drop out of the reconciliation entirely while still
        # showing in the register -- its fabric silently uncounted.
        lays = [_view(r) for r in conn.execute(
            "SELECT * FROM cut_lays WHERE order_id=? AND COALESCE(status,'')!='cancelled' "
            "ORDER BY COALESCE(cut_date,''), id", (order_id,)).fetchall()]
        return _summarise(dict(o), lays, _planned_cpg_map(conn, {order_id}).get(order_id))
    finally:
        conn.close()


def order_rollup():
    """One row per order that has lays. Batched: 3 queries whatever the order count."""
    conn = get_db()
    try:
        return _rollup(conn)
    finally:
        conn.close()


def _rollup(conn):
    lays = [_view(r) for r in conn.execute(
        "SELECT * FROM cut_lays WHERE COALESCE(status,'')!='cancelled'").fetchall()]
    by_order = {}
    for l in lays:
        by_order.setdefault(l["order_id"], []).append(l)
    ids = set(by_order)
    if not ids:
        return []
    orders = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM ord_orders").fetchall()}
    planned = _planned_cpg_map(conn, ids)
    rows = [_summarise(orders.get(oid, {"id": oid}), ls, planned.get(oid))
            for oid, ls in by_order.items()]
    rows.sort(key=lambda r: (r["variance_pct"] is None, -(r["variance_pct"] or 0)))
    return rows


# --- writes ---------------------------------------------------------------
_EDITABLE = ["order_id", "marker_ref", "marker_length_m", "marker_width_cm", "fabric_width_cm",
             "marker_area_m2", "marker_eff_pct", "plies", "size_ratio", "pieces_per_ply",
             "end_allow_m", "actual_fabric_m", "fabric_ref", "colour", "shade_lot",
             "cut_date", "cutter", "status", "notes"]
_NUMERIC = {"marker_length_m", "marker_width_cm", "fabric_width_cm", "marker_area_m2",
            "marker_eff_pct", "end_allow_m", "actual_fabric_m", "plies", "pieces_per_ply",
            "order_id"}


def _clean(field, value):
    """Numerics never reach the DB as a string: a typo would otherwise be stored and
    then silently coerced to 0.0 by every read."""
    if field in _NUMERIC:
        return _i(value) if field in ("plies", "pieces_per_ply", "order_id") else _f(value)
    return (value or None)


def _rearm(conn, order_id):
    """Any change to a lay re-opens the fabric-variance alarm for its order."""
    if order_id:
        conn.execute("UPDATE cut_lays SET variance_alerted=0 WHERE order_id=?", (order_id,))


def create_lay(data, user):
    conn = get_db()
    try:
        cols = [f for f in _EDITABLE if f in data]
        vals = [_clean(f, data.get(f)) for f in cols]
        cols += ["created_by", "created_at"]
        vals += [(user or {}).get("username"), _now()]
        cur = conn.execute(
            "INSERT INTO cut_lays (" + ",".join(cols) + ") VALUES (" +
            ",".join("?" * len(cols)) + ")", vals)
        lid = cur.lastrowid
        # Number from the row id -- collision-free, unlike COUNT(*)+1 after a delete.
        conn.execute("UPDATE cut_lays SET lay_no=? WHERE id=?",
                     (f"CUT-{date.today().year}-{lid:05d}", lid))
        _rearm(conn, _i(data.get("order_id")))
        conn.commit()
        return lid
    finally:
        conn.close()


def update_lay(lay_id, data):
    """Partial: only fields present in `data` are written.
    False = no such lay, so the caller can 404 instead of confirming a write that
    never touched a row."""
    conn = get_db()
    try:
        row = conn.execute("SELECT order_id FROM cut_lays WHERE id=?", (lay_id,)).fetchone()
        if not row:
            return False
        sets, args = [], []
        for f in _EDITABLE:
            if f in data:
                sets.append(f"{f}=?"); args.append(_clean(f, data.get(f)))
        if not sets:
            return True                     # nothing sent: a no-op, not a missing lay
        sets.append("updated_at=?"); args.append(_now())
        args.append(lay_id)
        conn.execute("UPDATE cut_lays SET " + ", ".join(sets) + " WHERE id=?", args)
        _rearm(conn, row["order_id"])
        if "order_id" in data:              # moved: BOTH orders' numbers changed
            _rearm(conn, _i(data.get("order_id")))
        conn.commit()
        return True
    finally:
        conn.close()


def add_lay_roll(lay_id, data):
    """Book a roll against a lay. Traceability only: no stock is moved here."""
    meters = _f(data.get("meters"))
    if meters <= 0:
        return False, "bad_meters"
    conn = get_db()
    try:
        lay = conn.execute("SELECT id, shade_lot FROM cut_lays WHERE id=?", (lay_id,)).fetchone()
        if not lay:
            return False, "lay_not_found"
        conn.execute(
            "INSERT INTO cut_lay_rolls (lay_id,roll_id,roll_no,shade_lot,meters,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (lay_id, _i(data.get("roll_id")) or None, (data.get("roll_no") or None),
             (data.get("shade_lot") or lay["shade_lot"]), round(meters, 3), _now()))
        conn.commit()
        return True, "ok"
    finally:
        conn.close()


# --- the sweep: fabric variance is the biggest margin lever we have -------
def variance_sweep():
    """Bell-alert (once per order) when measured fabric consumption runs more than
    FABRIC_VARIANCE_ALERT_PCT above the BOM plan. Idempotent via cut_lays.
    variance_alerted, which any edit to any lay of that order resets. Never raises."""
    try:
        conn = get_db()
    except Exception:
        return
    try:
        for r in _rollup(conn):
            oid = r["order_id"]
            if not oid or r["variance_pct"] is None or r["variance_pct"] <= FABRIC_VARIANCE_ALERT_PCT:
                continue
            done = conn.execute(
                "SELECT COALESCE(MAX(variance_alerted),0) AS c FROM cut_lays WHERE order_id=?",
                (oid,)).fetchone()["c"]
            if done:
                continue
            o = r["order"] or {}
            _bell(conn, "warning", f"Fabric over-consumption: {o.get('order_no') or oid}",
                  f"Cut room is using {r['actual_cpg']} m/garment against a plan of "
                  f"{r['planned_cpg']} m — {r['variance_pct']}% unfavourable, "
                  f"{r['variance_m']} m burnt on {r['pieces_cut']} pieces cut.",
                  link=f"/cutroom/orders/{oid}")
            conn.execute("UPDATE cut_lays SET variance_alerted=1 WHERE order_id=?", (oid,))
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
        rows = _rollup(conn)
        lays = [l for r in rows for l in r["lays"]]
        used = round(sum(l["fabric_used_m"] for l in lays), 2)
        theo = round(sum(l["theoretical_m"] for l in lays), 2)
        wsum = sum(l["fabric_used_m"] for l in lays if l["marker_eff_pct"] is not None)
        weff = sum(l["marker_eff_pct"] * l["fabric_used_m"]
                   for l in lays if l["marker_eff_pct"] is not None)
        # Overall variance = extra metres on the orders that HAVE a plan, over what
        # those same orders should have used. Orders with no metre BOM are excluded
        # from both sides so the percentage stays honest.
        planned_m = sum(r["planned_cpg"] * r["pieces_cut"]
                        for r in rows if r["planned_cpg"] and r["pieces_cut"])
        actual_m = sum(r["fabric_used_m"] for r in rows if r["planned_cpg"] and r["pieces_cut"])
        util = _pct(theo, used)
        return {
            "lays_total": len(lays),
            "pieces_cut": sum(l["pieces_cut"] for l in lays),
            "fabric_used_m": used,
            "marker_eff_pct": round(weff / wsum, 1) if wsum else None,
            "utilisation_pct": util,
            "waste_pct": round(100.0 - util, 2) if util is not None else None,
            "variance_pct": _pct(actual_m - planned_m, planned_m),
            "variance_m": round(actual_m - planned_m, 2) if planned_m else None,
            "orders_short": sum(1 for r in rows if (r["balance"] or 0) > 0),
            "orders_over": sum(1 for r in rows if (r["balance"] or 0) < 0),
            "open_lays": sum(1 for l in lays if l.get("status") in ("planned", "spread")),
            "rows": rows,
            # the lays leaking the most fabric — where to walk to right now
            "worst": sorted([l for l in lays if l["waste_pct"] is not None],
                            key=lambda l: -l["waste_pct"])[:8],
        }
    finally:
        conn.close()


def export_dataset(key):
    """Rows for a named export: returns (headers, rows) or (None, None).

    Keys: lay-register | lay-rolls | order-summary.

    Numbers are emitted exactly as lay_metrics/_summarise already rounded them, so a
    CSV and the screen it was taken from can never disagree — a cut room arguing with
    finance over a 4th decimal is the whole reason this export exists.
    """
    if key == "lay-register":
        # No LIMIT: this is the register people currently screenshot, and the /lays
        # page already renders every row, so the export is never the heavier read.
        return (["Lay No", "Cut Date", "Order No", "Buyer", "Style Ref", "Marker Ref",
                 "Size Ratio", "Plies", "Pieces/Ply", "Pieces Cut", "Marker Length (m)",
                 "Marker Width (cm)", "Fabric Width (cm)", "End Allowance (m)",
                 "Theoretical (m)", "Planned (m)", "Fabric Used (m)", "Measured",
                 "m/garment", "Marker Eff %", "Utilisation %", "End Loss %",
                 "Fabric Ref", "Colour", "Shade Lot", "Cutter", "Status"],
                [[_cell(l["lay_no"]), _cell(l["cut_date"]), _cell(l["order_no"]),
                  _cell(l["buyer"]), _cell(l["style_ref"]), _cell(l["marker_ref"]),
                  _cell(l["size_ratio"]), l["plies"], l["pieces_per_ply"], l["pieces_cut"],
                  _cell(l["marker_length_m"]), _cell(l["marker_width_cm"]),
                  _cell(l["fabric_width_cm"]), _cell(l["end_allow_m"]),
                  l["theoretical_m"], l["planned_m"], l["fabric_used_m"],
                  "yes" if l["measured"] else "no",
                  _cell(l["cons_per_gmt"]), _cell(l["marker_eff_pct"]),
                  _cell(l["utilisation_pct"]), _cell(l["waste_pct"]),
                  _cell(l["fabric_ref"]), _cell(l["colour"]), _cell(l["shade_lot"]),
                  _cell(l["cutter"]), _cell(l["status"])]
                 for l in list_lays()])

    if key == "order-summary":
        return (["Order No", "Buyer", "Style Ref", "Lays", "Order Qty", "Pieces Cut",
                 "Cut %", "Balance", "Plan m/garment", "Actual m/garment", "Variance %",
                 "Variance (m)", "Flag", "Fabric Used (m)", "Theoretical (m)",
                 "Utilisation %", "End Loss %", "Marker Eff %"],
                [[_cell((r["order"] or {}).get("order_no")),
                  _cell((r["order"] or {}).get("buyer")),
                  _cell((r["order"] or {}).get("style_ref")),
                  r["lay_count"], _cell(r["order_qty"]), r["pieces_cut"],
                  _cell(r["cut_pct"]), _cell(r["balance"]), _cell(r["planned_cpg"]),
                  _cell(r["actual_cpg"]), _cell(r["variance_pct"]), _cell(r["variance_m"]),
                  _cell(r["flag"]), r["fabric_used_m"], r["theoretical_m"],
                  _cell(r["utilisation_pct"]), _cell(r["waste_pct"]),
                  _cell(r["marker_eff_pct"])]
                 for r in order_rollup()])

    if key == "lay-rolls":
        conn = get_db()
        try:
            # Generous cap: roll lines are the one table that grows per physical roll,
            # and 50k rows is already several years of spreading.
            rows = conn.execute(
                "SELECT r.roll_no, r.shade_lot, r.meters, r.created_at, r.roll_id, "
                "l.lay_no, l.fabric_ref, l.colour, l.cut_date, o.order_no "
                "FROM cut_lay_rolls r JOIN cut_lays l ON l.id=r.lay_id "
                "LEFT JOIN ord_orders o ON o.id=l.order_id "
                "ORDER BY l.id, r.id LIMIT 50000").fetchall()
            return (["Lay No", "Cut Date", "Order No", "Fabric Ref", "Colour",
                     "Roll No", "Warehouse Roll ID", "Shade Lot", "Meters", "Booked At"],
                    [[_cell(r["lay_no"]), _cell(r["cut_date"]), _cell(r["order_no"]),
                      _cell(r["fabric_ref"]), _cell(r["colour"]), _cell(r["roll_no"]),
                      _cell(r["roll_id"]), _cell(r["shade_lot"]),
                      round(_f(r["meters"]), 3), _cell(r["created_at"])]
                     for r in rows])
        finally:
            conn.close()

    return (None, None)


def order_options():
    """Orders to hang a lay on. Live orders first — you do not cut a closed order."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, order_no, buyer, style_ref, style_name, qty FROM ord_orders "
            "WHERE status NOT IN ('closed','cancelled') ORDER BY ship_date ASC, id DESC"
        ).fetchall()]
    finally:
        conn.close()
