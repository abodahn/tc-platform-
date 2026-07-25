"""
BOM + order costing services.

Estimate vs ACTUAL, per customer order. The estimate is a BOM (material) plus
five entered per-unit figures; the actual is pulled from what the platform
already knows — order-linked purchase requisitions and, when the warehouse
module exists, material actually issued to the order.

Money conventions, applied everywhere:
  * TOTALS are rounded to 2 dp.
  * PER-UNIT money keeps 4 dp — a care label at $0.0122/pc rounds to $0.01 and
    the BOM stops adding up.
  * QUANTITIES keep 4 dp (metres and kilos are fractional).
  * A blank/garbage form number becomes None, never 0.0. A missing unit price is
    reported as an unpriced line so an incomplete estimate is visible, not
    flattering.
"""
import math
from datetime import datetime

from app.db import get_db
from .constants import CATEGORIES, VARIANCE_ALERT_PCT

# Generous ceiling on the line-level exports. A BOM is ~8 lines per order, so this
# is thousands of orders — big enough that nobody meets it in practice, small
# enough that a runaway table cannot build a 200 MB CSV in memory.
_EXPORT_LIMIT = 20000


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _num(v):
    """Form value -> float, or None for blank/None/garbage. Never 0.0 by accident.

    'nan' and 'inf' are garbage too: float() accepts them, and a single NaN
    consumption makes every comparison below False, sails past the > 0 guards and
    turns the material total, the estimate, the margin and the dashboard KPIs
    into nan for good. The browser's type=number blocks them; a POST does not.
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def _f(v, default=0.0):
    """As _num, but for the places where 0 is the genuinely correct default
    (an allowance of 'blank' really is 0% wastage)."""
    n = _num(v)
    return default if n is None else n


def _bell(conn, severity, title, message, link="/costing"):
    conn.execute("INSERT INTO notifications (severity,module,title,message,link,created_at) "
                 "VALUES (?,?,?,?,?,?)", (severity, "costing", title, message, link, _now()))


def order_ref(order_id):
    """The platform's source_ref convention: '<kind>:<numeric id>'."""
    return "order:%d" % int(order_id)


# --- the two formulas everything else hangs off --------------------------
def required_qty(order_qty, consumption, allowance_pct):
    """THE material-plan spine:

        required = order qty  x  consumption per garment  x  (1 + allowance% / 100)

    The allowance is cutting/process wastage, so it INFLATES the buy. Dropping a
    3% allowance on 0.24 kg/pc over 12 000 pcs loses 86 kg of fabric nobody
    ordered — the classic reason a cost sheet and a store issue never agree.
    """
    return round(_f(order_qty) * _f(consumption) * (1.0 + _f(allowance_pct) / 100.0), 4)


def margin(revenue, cost):
    """(value, pct) of revenue - cost. A zero/None selling price yields pct None —
    never a ZeroDivisionError and never a fake 100%."""
    rev = _f(revenue)
    value = round(rev - _f(cost), 2)
    return value, (round(value / rev * 100.0, 2) if rev else None)


def _variance(est, act):
    """actual - estimate. Positive = we spent more than we quoted = unfavourable."""
    e, a = _f(est), _f(act)
    value = round(a - e, 2)
    # half-a-cent deadband so float noise never paints a green order red
    flag = "unfavourable" if value > 0.005 else ("favourable" if value < -0.005 else "on_plan")
    return {"value": value, "pct": (round(value / e * 100.0, 2) if e else None), "flag": flag}


def cm_unit(sheet):
    """CM per unit = SMV x cost-per-minute when both are set (how a garment
    factory actually quotes it), else the flat figure. Deliberately NOT a time
    study — the SMV is an editable number, not a measurement engine."""
    smv, rate = _f(sheet.get("smv")), _f(sheet.get("cm_rate"))
    if smv > 0 and rate > 0:
        return round(smv * rate, 4)
    return round(_f(sheet.get("cm_per_unit")), 4)


def _line_view(line, order_qty):
    """A BOM row + its required quantity and line cost."""
    out = dict(line)
    price = _num(line["unit_price"])
    out["required_qty"] = required_qty(order_qty, line["consumption"], line["allowance_pct"])
    out["unpriced"] = price is None
    out["line_cost"] = round(out["required_qty"] * price, 2) if price is not None else 0.0
    return out


def _material(lines):
    """Material total = sum of the ROUNDED line costs, so the printed lines add
    up to the printed total (summing then rounding drifts by cents)."""
    return round(sum(l["line_cost"] for l in lines), 2), sum(1 for l in lines if l["unpriced"])


_SHEET_DEFAULTS = {"smv": 0, "cm_rate": 0, "cm_per_unit": 0, "overhead_per_unit": 0,
                   "freight_per_unit": 0, "duty_per_unit": 0, "other_per_unit": 0,
                   "notes": None, "variance_alerted": 0}


def _estimate(order_qty, material_total, sheet):
    """Per-category estimate totals in the order currency."""
    q = _f(order_qty)
    est = {"material": material_total,
           "cm": round(cm_unit(sheet) * q, 2),
           "overhead": round(_f(sheet.get("overhead_per_unit")) * q, 2),
           "freight": round(_f(sheet.get("freight_per_unit")) * q, 2),
           "duty": round(_f(sheet.get("duty_per_unit")) * q, 2),
           "other": round(_f(sheet.get("other_per_unit")) * q, 2)}
    est["total"] = round(sum(est[c] for c in CATEGORIES), 2)
    est["per_unit"] = round(est["total"] / q, 4) if q else None
    return est


# --- what the platform already knows -------------------------------------
def _procurement(conn, orders):
    """Received / invoiced / paid on PRs tagged source_module='costing',
    source_ref='order:<id>'. Only PRs in the ORDER's own currency roll into the
    money — pr_requests.total is never converted in place and there is no
    order-currency FX rate anywhere, so a foreign-currency PR is reported
    separately rather than added at a made-up rate.
    `orders` is a list of rows carrying id + currency; keyed by order id."""
    ccy = {o["id"]: o["currency"] for o in orders}
    out = {oid: {"received": 0.0, "invoiced": 0.0, "paid": 0.0,
                 "mixed_ccy": [], "prs": []} for oid in ccy}
    if not ccy:
        return out
    refs = {order_ref(o): o for o in ccy}
    try:
        prs = conn.execute(
            "SELECT id, pr_no, source_ref, status, currency, vendor, total, "
            "COALESCE(paid_amount,0) AS paid_amount, payment_status "
            "FROM pr_requests WHERE source_module='costing' AND is_active=1").fetchall()
    except Exception:
        # PostgreSQL aborts the whole transaction on a failed statement, and the
        # caller keeps reading on this same connection — roll back or the cost
        # sheet 500s the moment procurement is absent.
        try:
            conn.rollback()
        except Exception:
            pass
        return out                       # procurement not installed — no actuals from it
    for p in prs:
        oid = refs.get(p["source_ref"])
        if oid is None:
            continue
        recv = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(received_qty,0)*COALESCE(unit_price,0)),0) AS v "
            "FROM pr_items WHERE pr_id=?", (p["id"],)).fetchone()["v"] or 0
        inv = conn.execute(
            "SELECT COALESCE(SUM(COALESCE(amount,0)+COALESCE(tax,0)),0) AS v "
            "FROM pr_invoices WHERE pr_id=?", (p["id"],)).fetchone()["v"] or 0
        same = (p["currency"] or "").strip().upper() == (ccy[oid] or "").strip().upper()
        row = out[oid]
        row["prs"].append({"id": p["id"], "pr_no": p["pr_no"], "status": p["status"],
                           "currency": p["currency"], "vendor": p["vendor"],
                           "total": round(_f(p["total"]), 2), "received": round(_f(recv), 2),
                           "invoiced": round(_f(inv), 2), "paid": round(_f(p["paid_amount"]), 2),
                           "same_ccy": same})
        if same:
            row["received"] = round(row["received"] + _f(recv), 2)
            row["invoiced"] = round(row["invoiced"] + _f(inv), 2)
            row["paid"] = round(row["paid"] + _f(p["paid_amount"]), 2)
        elif p["currency"] not in row["mixed_ccy"]:
            row["mixed_ccy"].append(p["currency"])
    return out


def _wh_issued_fn():
    """The warehouse module is being built in parallel — import it defensively so
    its absence degrades to 'no issue data', never a broken costing page."""
    try:
        from app.warehouse.services import issued_for_order
        return issued_for_order
    except Exception:
        return None


def _wh_value(fn, order_id):
    if fn is None:
        return None
    try:
        v = fn(order_id)
    except Exception:
        return None
    if isinstance(v, dict):
        v = v.get("value", v.get("total"))
    n = _num(v)
    return round(n, 2) if n is not None else None


def _material_actual(issued, proc, manual):
    """DOUBLE-COUNTING GUARD — the one rule that makes this number trustworthy.

    Fabric bought on an order-linked PR and then issued from the warehouse to the
    same order is ONE cost seen twice. So the material actual comes from exactly
    ONE source, in this priority, never a sum:
      1. warehouse issues      — what was really consumed (the truest actual)
      2. procurement receipts  — what really arrived, when there is no issue data
      3. manual 'material' entries — cash/subcontract buys the platform never saw
    The basis is returned with the number so it is never anonymous on screen.
    """
    # Take the HIGHEST single source, never the first non-zero one. Warehouse issues
    # are INCREMENTAL (they grow metre by metre as the order is cut) while procurement
    # receipts and manual entries are COMPLETE figures. Preferring "issued" the moment
    # it is non-zero therefore let the first metre issued replace the entire material
    # actual: on the demo data the actual dropped 19,180.00 -> 3.20 and the margin
    # "improved" from 24% to 65%, so a real overrun would be erased and the variance
    # alert would never fire. max() keeps the one-source rule (no double-counting)
    # while making it impossible to UNDER-report.
    cands = {"issued": round(issued or 0, 2), "procured": round(proc or 0, 2),
             "manual": round(manual or 0, 2)}
    basis = max(cands, key=lambda k: cands[k])
    if cands[basis] <= 0:
        return 0.0, "none"
    # Name it honestly when issues are still catching up with a bigger known figure.
    if basis != "issued" and cands["issued"] > 0:
        basis += " (issues partial)"
    return cands[max(cands, key=lambda k: cands[k])], basis


# --- reads ----------------------------------------------------------------
def _sheet_of(row):
    s = dict(_SHEET_DEFAULTS)
    if row:
        s.update(dict(row))
    return s


def _actuals_by_order(conn, order_ids):
    out = {oid: {c: 0.0 for c in CATEGORIES} for oid in order_ids}
    if not order_ids:
        return out
    for r in conn.execute("SELECT order_id, category, COALESCE(SUM(amount),0) AS v "
                          "FROM cst_actuals GROUP BY order_id, category").fetchall():
        if r["order_id"] in out and r["category"] in out[r["order_id"]]:
            out[r["order_id"]][r["category"]] = round(_f(r["v"]), 2)
    return out


def _bom_by_order(conn, orders):
    """All BOM lines for the given orders, grouped and costed in Python so the
    list page and the detail page round identically."""
    qty = {o["id"]: o["qty"] for o in orders}
    out = {oid: [] for oid in qty}
    if not qty:
        return out
    for r in conn.execute("SELECT * FROM cst_bom_lines ORDER BY order_id, seq, id").fetchall():
        if r["order_id"] in out:
            out[r["order_id"]].append(_line_view(r, qty[r["order_id"]]))
    return out


def _summarise(order, lines, sheet, actual_cats, proc, issued):
    """Estimate, actual, variance and both margins for one order."""
    qty = _f(order["qty"])
    material_total, unpriced = _material(lines)
    est = _estimate(qty, material_total, sheet)

    mat_actual, basis = _material_actual(issued, proc["received"], actual_cats["material"])
    act = {c: actual_cats[c] for c in CATEGORIES}
    act["material"] = mat_actual
    act["total"] = round(sum(act[c] for c in CATEGORIES), 2)
    act["per_unit"] = round(act["total"] / qty, 4) if qty else None

    var = {c: _variance(est[c], act[c]) for c in CATEGORIES}
    var["total"] = _variance(est["total"], act["total"])

    revenue = round(qty * _f(order["unit_price"]), 2)
    est_v, est_p = margin(revenue, est["total"])
    act_v, act_p = margin(revenue, act["total"])
    return {
        "estimate": est, "actual": act, "variance": var,
        "material": {"total": material_total, "unpriced": unpriced,
                     "per_unit": round(material_total / qty, 4) if qty else None,
                     "actual_basis": basis},
        "margin": {"revenue": revenue, "est_value": est_v, "est_pct": est_p,
                   "act_value": act_v, "act_pct": act_p},
        "has_actual": act["total"] > 0,
    }


def list_costed(only_costed=False):
    """Every order with its estimate, actual, variance and margin. BOM, sheets,
    actuals and PR headers are fetched once for all orders (not per order) — only
    the per-PR receipt/invoice sums and the warehouse call are repeated, and both
    scale with linked PRs, not with the order count."""
    conn = get_db()
    try:
        orders = [dict(r) for r in conn.execute(
            "SELECT * FROM ord_orders ORDER BY ship_date ASC, id DESC").fetchall()]
        ids = [o["id"] for o in orders]
        bom = _bom_by_order(conn, orders)
        sheets = {r["order_id"]: dict(r) for r in
                  conn.execute("SELECT * FROM cst_sheets").fetchall()}
        acts = _actuals_by_order(conn, ids)
        procs = _procurement(conn, orders)
        rows = []
        wh = _wh_issued_fn()
        for o in orders:
            s = _summarise(o, bom[o["id"]], _sheet_of(sheets.get(o["id"])), acts[o["id"]],
                           procs[o["id"]], _wh_value(wh, o["id"]))
            o.update(s)
            o["costed"] = bool(bom[o["id"]]) or o["id"] in sheets
            if only_costed and not o["costed"]:
                continue
            rows.append(o)
        return rows
    finally:
        conn.close()


def cost_sheet(order_id):
    """The full bundle for one order's cost-sheet page, or None."""
    conn = get_db()
    try:
        o = conn.execute("SELECT * FROM ord_orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return None
        order = dict(o)
        lines = [_line_view(r, order["qty"]) for r in conn.execute(
            "SELECT * FROM cst_bom_lines WHERE order_id=? ORDER BY seq, id", (order_id,)).fetchall()]
        sheet = _sheet_of(conn.execute("SELECT * FROM cst_sheets WHERE order_id=?",
                                       (order_id,)).fetchone())
        acts = _actuals_by_order(conn, [order_id])[order_id]
        proc = _procurement(conn, [order])[order_id]
        issued = _wh_value(_wh_issued_fn(), order_id)
        bundle = _summarise(order, lines, sheet, acts, proc, issued)
        bundle.update({
            "order": order, "bom": lines, "sheet": sheet, "proc": proc,
            "issued": issued, "cm_per_unit": cm_unit(sheet),
            "entries": [dict(r) for r in conn.execute(
                "SELECT * FROM cst_actuals WHERE order_id=? ORDER BY id DESC",
                (order_id,)).fetchall()],
        })
        return bundle
    finally:
        conn.close()


def dashboard():
    rows = list_costed()
    costed = [r for r in rows if r["costed"]]
    with_actual = [r for r in costed if r["has_actual"]]
    unfav = [r for r in with_actual if r["variance"]["total"]["flag"] == "unfavourable"]
    worst = max(unfav, key=lambda r: r["variance"]["total"]["pct"] or 0, default=None)
    return {
        "orders_costed": len(costed),
        "orders_unfavourable": len(unfav),
        "est_total": round(sum(r["estimate"]["total"] for r in costed), 2),
        "act_total": round(sum(r["actual"]["total"] for r in with_actual), 2),
        "worst": worst,
        "unpriced_lines": sum(r["material"]["unpriced"] for r in costed),
        "rows": costed,
    }


# --- writes ---------------------------------------------------------------
def _rearm(conn, order_id):
    """A changed PLAN re-arms the variance alarm. Booking an actual deliberately
    does not — otherwise every entry on an over-running order rings the bell."""
    conn.execute("UPDATE cst_sheets SET variance_alerted=0 WHERE order_id=?", (order_id,))


def _order_exists(conn, order_id):
    try:
        return conn.execute("SELECT id FROM ord_orders WHERE id=?",
                            (order_id,)).fetchone() is not None
    except Exception:
        return False


def _bom_numbers(data):
    """The three numbers on a BOM line, validated once for both the add and the
    edit path. Returns (consumption, allowance_pct, unit_price|None, error|None).

    A negative allowance would SHRINK the buy (below -100% it goes negative and
    the line becomes a credit); a negative price credits the cost sheet outright.
    Both are money bugs, and both are only blocked client-side by min=0.
    """
    cons = _num(data.get("consumption"))
    if cons is None or cons <= 0:
        return None, None, None, "bad_consumption"
    raw_allow = data.get("allowance_pct")
    allow = _num(raw_allow)
    if allow is None and str(raw_allow or "").strip():
        return None, None, None, "bad_allowance"    # typed, and not a number
    allow = 0.0 if allow is None else allow
    if allow < 0:
        return None, None, None, "bad_allowance"
    raw_price = data.get("unit_price")
    price = _num(raw_price)
    if price is None and str(raw_price or "").strip():
        return None, None, None, "bad_price"        # typed, and not a number
    if price is not None and price < 0:
        return None, None, None, "bad_price"
    return cons, allow, price, None


def add_bom_line(order_id, data, user):
    item = (data.get("item") or "").strip()
    if not item:
        return False, "item_required"
    cons, allow, price, err = _bom_numbers(data)
    if err:
        return False, err
    conn = get_db()
    try:
        if not _order_exists(conn, order_id):
            return False, "no_order"
        seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 AS s FROM cst_bom_lines "
                           "WHERE order_id=?", (order_id,)).fetchone()["s"]
        conn.execute(
            "INSERT INTO cst_bom_lines (order_id,seq,item,kind,colour,consumption,uom,"
            "allowance_pct,unit_price,supplier,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, seq, item, data.get("kind") or "fabric", data.get("colour") or None,
             cons, data.get("uom") or "pcs", allow, price, data.get("supplier") or None,
             data.get("notes") or None, _now()))
        _rearm(conn, order_id)
        conn.commit()
        return True, "added"
    finally:
        conn.close()


def update_bom_line(line_id, data):
    """Edit or delete one BOM line. Returns (ok, order_id_or_msg)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT order_id FROM cst_bom_lines WHERE id=?", (line_id,)).fetchone()
        if not row:
            return False, "not_found"
        oid = row["order_id"]
        if (data.get("action") or "").strip() == "delete":
            conn.execute("DELETE FROM cst_bom_lines WHERE id=?", (line_id,))
        else:
            cons, allow, price, err = _bom_numbers(data)
            if err:
                return False, err
            conn.execute(
                "UPDATE cst_bom_lines SET item=COALESCE(?,item), kind=?, colour=?, consumption=?, "
                "uom=?, allowance_pct=?, unit_price=?, supplier=?, updated_at=? WHERE id=?",
                ((data.get("item") or "").strip() or None, data.get("kind") or "fabric",
                 data.get("colour") or None, cons, data.get("uom") or "pcs",
                 allow, price, data.get("supplier") or None, _now(), line_id))
        _rearm(conn, oid)
        conn.commit()
        return True, oid
    finally:
        conn.close()


def save_sheet(order_id, data, user):
    """Upsert the non-material cost figures. Blank stays blank (0 for a figure
    that is genuinely zero is fine; a typo'd letter must not become 0 silently),
    so every field goes through _num and an unparseable entry is rejected."""
    fields = ["smv", "cm_rate", "cm_per_unit", "overhead_per_unit",
              "freight_per_unit", "duty_per_unit", "other_per_unit"]
    vals = {}
    for f in fields:
        raw = data.get(f)
        n = _num(raw)
        if n is None and str(raw or "").strip():
            return False, "bad_number"      # something was typed and it was not a number
        if n is not None and n < 0:
            return False, "negative"
        vals[f] = 0.0 if n is None else n
    conn = get_db()
    try:
        if not _order_exists(conn, order_id):
            return False, "no_order"
        existing = conn.execute("SELECT id FROM cst_sheets WHERE order_id=?", (order_id,)).fetchone()
        args = [vals[f] for f in fields]
        if existing:
            conn.execute(
                "UPDATE cst_sheets SET " + ", ".join(f + "=?" for f in fields) +
                ", notes=?, variance_alerted=0, updated_by=?, updated_at=? WHERE order_id=?",
                args + [data.get("notes") or None, (user or {}).get("username"), _now(), order_id])
        else:
            conn.execute(
                "INSERT INTO cst_sheets (order_id," + ",".join(fields) +
                ",notes,updated_by,created_at) VALUES (?," + ",".join("?" for _ in fields) + ",?,?,?)",
                [order_id] + args + [data.get("notes") or None,
                                     (user or {}).get("username"), _now()])
        conn.commit()
        return True, "saved"
    finally:
        conn.close()


def add_actual(order_id, data, user):
    cat = (data.get("category") or "").strip()
    if cat not in CATEGORIES:
        return False, "bad_category"
    amt = _num(data.get("amount"))
    if amt is None or amt <= 0:
        return False, "bad_amount"
    conn = get_db()
    try:
        if not _order_exists(conn, order_id):
            return False, "no_order"
        conn.execute(
            "INSERT INTO cst_actuals (order_id,category,amount,source,notes,created_by,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (order_id, cat, round(amt, 2), data.get("source") or None, data.get("notes") or None,
             (user or {}).get("username"), _now()))
        conn.commit()
        return True, "added"
    finally:
        conn.close()


def delete_actual(actual_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT order_id FROM cst_actuals WHERE id=?", (actual_id,)).fetchone()
        if not row:
            return False, "not_found"
        conn.execute("DELETE FROM cst_actuals WHERE id=?", (actual_id,))
        conn.commit()
        return True, row["order_id"]
    finally:
        conn.close()


def link_pr_to_order(order_id, pr_ref, user=None):
    """Tag an existing purchase requisition as belonging to this order, using
    procurement's own link_source() and the platform convention
    source_module='costing', source_ref='order:<id>'. Accepts a PR number or id.
    Procurement is imported defensively so costing loads without it."""
    ref = str(pr_ref or "").strip()
    if not ref:
        return False, "no_pr"
    conn = get_db()
    try:
        if not _order_exists(conn, order_id):
            return False, "no_order"
        row = conn.execute("SELECT id, source_module, source_ref FROM pr_requests "
                           "WHERE pr_no=? OR CAST(id AS TEXT)=?", (ref, ref)).fetchone()
    except Exception:
        return False, "no_procurement"
    finally:
        conn.close()
    if not row:
        return False, "pr_not_found"
    # link_source() is a blind UPDATE of the single source_module/source_ref pair.
    # Re-pointing a PR that maintenance (or any other module) already owns would
    # silently destroy that module's link, so only an unowned or already-costing
    # PR may be taken. Moving a PR between two costing orders stays allowed.
    if row["source_module"] and row["source_module"] != "costing":
        return False, "pr_owned"
    try:
        from app.approvals.services import link_source
    except Exception:
        return False, "no_procurement"
    return (True, "linked") if link_source(row["id"], "costing", order_ref(order_id), user) \
        else (False, "link_failed")


# --- exports --------------------------------------------------------------
def _s(v):
    """Any DB/derived value -> a clean cell. Never None, never an object."""
    return "" if v is None else v


def _money(v, dp=2):
    """Money/quantity cell. Blank stays blank so a missing price never exports as
    0.00 — an unpriced BOM line must look unpriced in Excel too, exactly as it
    does on screen."""
    n = _num(v)
    return "" if n is None else round(n, dp)


def export_dataset(key):
    """Rows for a named export: returns (headers, rows) or (None, None).

    Keys: order-margin | cost-breakdown | bom-lines | actuals.

    Per-unit money keeps 4 dp on purpose (see the module docstring): a trim at
    $0.0122/pc rounded to 2 dp makes the exported BOM stop adding up to the
    exported total. Totals are 2 dp, quantities 3 dp.
    """
    if key == "order-margin":
        # The register everyone screenshots. Reuses list_costed() so the CSV and
        # the /costing/orders table can never disagree.
        rows = []
        for o in list_costed():
            v = o["variance"]["total"]
            rows.append([
                _s(o["order_no"]), _s(o["buyer"]),
                _s(o["style_name"] or o["style_ref"]), _s(o["ship_date"]),
                _s(o["currency"]), _money(o["qty"], 3), _money(o["unit_price"], 4),
                _money(o["margin"]["revenue"]), _money(o["estimate"]["per_unit"], 4),
                _money(o["estimate"]["total"]),
                _money(o["actual"]["total"]) if o["has_actual"] else "",
                _s(o["material"]["actual_basis"]),
                _money(v["value"]) if o["has_actual"] else "",
                _money(v["pct"]) if o["has_actual"] else "",
                v["flag"] if o["has_actual"] else "",
                _money(o["margin"]["est_value"]), _money(o["margin"]["est_pct"]),
                _money(o["margin"]["act_value"]) if o["has_actual"] else "",
                _money(o["margin"]["act_pct"]) if o["has_actual"] else "",
                o["material"]["unpriced"], "yes" if o["costed"] else "no",
            ])
        return (["Order No", "Buyer", "Style", "Ship Date", "Currency", "Qty",
                 "Price/Unit", "Revenue", "Est Cost/Unit", "Estimate", "Actual",
                 "Actual Basis", "Variance", "Variance %", "Variance Flag",
                 "Margin Est", "Margin Est %", "Margin Act", "Margin Act %",
                 "Unpriced BOM Lines", "Costed"], rows)

    if key == "cost-breakdown":
        # Long format (one row per order x category) — the shape a pivot table
        # wants, and the breakdown the cost sheet shows category by category.
        rows = []
        for o in list_costed(only_costed=True):
            for c in CATEGORIES:
                v = o["variance"][c]
                rows.append([_s(o["order_no"]), _s(o["buyer"]), _s(o["currency"]), c,
                             _money(o["estimate"][c]), _money(o["actual"][c]),
                             _money(v["value"]), _money(v["pct"]), v["flag"]])
        return (["Order No", "Buyer", "Currency", "Category", "Estimate",
                 "Actual", "Variance", "Variance %", "Flag"], rows)

    conn = get_db()
    try:
        if key == "bom-lines":
            rows = conn.execute(
                "SELECT b.*, o.order_no, o.buyer, o.currency, o.style_name, o.style_ref, "
                "o.qty AS order_qty FROM cst_bom_lines b JOIN ord_orders o ON o.id=b.order_id "
                "ORDER BY o.order_no, b.seq, b.id LIMIT ?", (_EXPORT_LIMIT,)).fetchall()
            out = []
            for r in rows:
                # _line_view() is what the cost-sheet page renders, so the exported
                # required qty and line cost cannot drift from the screen.
                v = _line_view(r, r["order_qty"])
                out.append([
                    _s(v["order_no"]), _s(v["buyer"]),
                    _s(v["style_name"] or v["style_ref"]), _s(v["seq"]), _s(v["item"]),
                    _s(v["kind"]), _s(v["colour"]), _money(v["consumption"], 4),
                    _s(v["uom"]), _money(v["allowance_pct"], 2),
                    _money(v["required_qty"], 3), _money(v["unit_price"], 4),
                    "" if v["unpriced"] else _money(v["line_cost"]),
                    "no" if v["unpriced"] else "yes", _s(v["currency"]),
                    _s(v["supplier"]), _s(v["notes"]),
                ])
            return (["Order No", "Buyer", "Style", "Seq", "Item", "Kind", "Colour",
                     "Consumption/Unit", "UOM", "Allowance %", "Required Qty",
                     "Unit Price", "Line Cost", "Priced", "Currency", "Supplier",
                     "Notes"], out)

        if key == "actuals":
            rows = conn.execute(
                "SELECT a.*, o.order_no, o.buyer, o.currency FROM cst_actuals a "
                "JOIN ord_orders o ON o.id=a.order_id ORDER BY a.id LIMIT ?",
                (_EXPORT_LIMIT,)).fetchall()
            return (["Order No", "Buyer", "Category", "Amount", "Currency",
                     "Source", "Booked By", "Booked At", "Notes"],
                    [[_s(r["order_no"]), _s(r["buyer"]), _s(r["category"]),
                      _money(r["amount"]), _s(r["currency"]), _s(r["source"]),
                      _s(r["created_by"]), _s(r["created_at"]), _s(r["notes"])]
                     for r in rows])
    finally:
        conn.close()
    return (None, None)


# --- the sweep: ring the bell when an order eats its own margin -----------
def variance_sweep():
    """Alert once per costed order whose actual total exceeds the estimate by
    more than VARIANCE_ALERT_PCT. Re-armed only by a plan change (see _rearm).
    Never raises — a broken sweep must not take the dashboard down."""
    try:
        rows = list_costed(only_costed=True)
    except Exception:
        return
    try:
        conn = get_db()
    except Exception:
        return
    try:
        # Only orders WITH a cost sheet are swept: the sheet row is where the
        # alerted flag lives, so a BOM-only order would re-alert every sweep.
        state = {r["order_id"]: r["variance_alerted"] for r in conn.execute(
            "SELECT order_id, variance_alerted FROM cst_sheets").fetchall()}
        for r in rows:
            if state.get(r["id"]) != 0 or not r["has_actual"]:
                continue
            v = r["variance"]["total"]
            if v["pct"] is None or v["pct"] <= VARIANCE_ALERT_PCT:
                continue
            mat = r["variance"]["material"]
            extra = (" Material alone is %+.1f%% (%s basis)." %
                     (mat["pct"], r["material"]["actual_basis"])) if mat["pct"] is not None else ""
            _bell(conn, "critical" if v["pct"] > 3 * VARIANCE_ALERT_PCT else "warning",
                  "Cost overrun: %s" % (r["order_no"] or r["id"]),
                  "Actual %s vs estimate %s (%+.1f%%) on %s — quoted margin %s%%, now %s%%.%s" % (
                      "{:,.2f}".format(r["actual"]["total"]),
                      "{:,.2f}".format(r["estimate"]["total"]), v["pct"],
                      r["order_no"] or r["id"],
                      r["margin"]["est_pct"] if r["margin"]["est_pct"] is not None else "n/a",
                      r["margin"]["act_pct"] if r["margin"]["act_pct"] is not None else "n/a",
                      extra),
                  "/costing/order/%d" % r["id"])
            conn.execute("UPDATE cst_sheets SET variance_alerted=1 WHERE order_id=?", (r["id"],))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
