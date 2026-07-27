"""
Smart Factory (/factory) — a PRESENTATION layer over the platform's real modules.

WHY THIS FILE READS OTHER MODULES' TABLES
/factory shipped with its OWN copy of the shop floor: sf_orders, sf_prod_entries,
sf_quality/sf_defects, sf_bundles/sf_fabric_rolls, sf_wash_batches, sf_operators.
The platform then grew the real modules — orders (ord_*), MES (mes_*), quality
(qc_*), warehouse (wh_rolls), wash (wsh_*), people (ppl_*), costing (cst_*) — and
the two ran side by side. That is two shop floors in one database: a bundle
scanned in one is invisible in the other, and the two can report different output
for the same day.

So every page below READS the owning module, through that module's own service
layer wherever one exists, and this file WRITES NOTHING. Entry happens in the
module that owns the record, which is the only way its validation, its permission
and its ledger rules cannot be reached around the back from a /factory form.

Deliberately NOT changed:
  * sf_styles / sf_operations stay live — app/services/smv.py resolves the style
    master and the style bulletin out of them, so they are a source of truth now,
    not a duplicate;
  * every sf_* row already in the database is left exactly where it is — nothing
    is dropped, migrated or rewritten;
  * /factory/approvals still runs on sf_approvals. Re-pointing it would move an
    approval ladder, and that is not a change to make silently; the page is
    labelled in the UI as running on its own /factory data.
"""
from datetime import date, timedelta

from app.db import get_db

# Internal tariffs, used ONLY to put a number on floor waste on one screen. They
# are not the finance ledger and they are not a price: /costing owns the priced
# cost sheet, its BOM, its actuals and its margin. Where the order HAS a real
# minute rate (cst_sheets.cm_rate) that rate is used instead of the fallback.
_RATE = {"labor_per_min": 0.35,      # fallback CM rate, EGP/min
         "water_per_l": 0.006,       # utility tariff, EGP/L
         "chem_per_kg": 3.5}         # utility tariff, EGP/kg
CPM_LOADED = 4.0                     # loaded line cost per minute (EGP)

# An order's AQL is judged by the QMS; this is only the shipping-risk curve the
# /factory intelligence page has always drawn on top of it (2.5% DHU = acceptable).
AQL_DHU_OK = 2.5


def _rows(conn, sql, p=()):
    """Never let a not-yet-created table 500 a /factory page. The rollback is
    mandatory, not tidiness: on PostgreSQL a failed statement aborts the whole
    transaction and every later read on this connection would fail too."""
    try:
        return [dict(r) for r in conn.execute(sql, p).fetchall()]
    except Exception:  # noqa: BLE001
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []


def _one(conn, sql, p=()):
    r = _rows(conn, sql, p)
    return r[0] if r else {}


def _f(v, d=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return d
    return f if f == f and f not in (float("inf"), float("-inf")) else d


def rag(pct):
    return "green" if pct >= 95 else ("amber" if pct >= 80 else "red")


def live_date():
    """The day the floor last reported. /factory is a live board, so it shows the
    most recent day that HAS data instead of an empty page on a quiet morning —
    and every converted page prints the date it is showing, so "live" is never a
    guess about which day these numbers belong to.

    This is MES's own resolve_date(), not a second copy of it, for one reason a
    plain MAX(work_date) gets wrong: a row booked against a FUTURE day (a
    pre-loaded target, or a typo'd year) is the MAX of the column, and would pin
    this board on it permanently — printing "Showing 2099-01-01 — the last day
    the floor reported" over an efficiency nobody measured."""
    from app.mes import services as mes
    return mes.resolve_date()


# ==========================================================================
# Command center / floor board — MES + QMS + wash, one day
# ==========================================================================
def dashboard():
    from app.mes import services as mes
    from app.quality import services as qc

    wd = live_date()
    m = mes.dashboard(wd)
    t = m["t"]
    live = [{"line": l["line_name"], "target": l["target"], "actual": l["actual"],
             "lost": l["downtime_min"], "efficiency": l["achievement"],
             "smv_efficiency": l["efficiency"], "rag": l["rag"]}
            for l in m["lines"]]
    live.sort(key=lambda x: x["efficiency"])

    q = qc.dashboard()
    pareto = [{"code": d["defect_type"], "qty": d["qty"]}
              for d in (q.get("top_defects") or [])]

    conn = get_db()
    try:
        orders = _one(conn, "SELECT COUNT(*) AS c FROM ord_orders "
                            "WHERE status NOT IN ('closed','cancelled')").get("c") or 0
    finally:
        conn.close()

    return {
        "work_date": wd,
        "kpis": {"efficiency": t["achievement"], "produced": t["actual"],
                 "target": t["target"], "dhu": q.get("dhu", 0.0), "rft": q.get("rft", 0.0),
                 # Rejects are the floor's own count (a subset of what came off the
                 # line), not the QMS defective-unit count of a sampled lot.
                 "reject": t["reject"], "orders": orders,
                 "smv_efficiency": t["efficiency"], "downtime": m["downtime_day"]},
        "live": live, "pareto": pareto, "sustain": sustainability(),
    }


# ==========================================================================
# Orders & styles — the platform order book + the CANONICAL SMV
# ==========================================================================
def list_orders(limit=200):
    """The real order book. SMV comes from app/services/smv.py, so this page and
    planning, costing and the MES line page all print the same number."""
    from app.services.smv import sources_for_orders, resolve
    conn = get_db()
    try:
        rows = _rows(conn, "SELECT id, order_no, po_no, buyer, style_ref, style_name, qty, "
                           "ship_date, status FROM ord_orders ORDER BY id DESC LIMIT ?",
                     (int(limit),))
        smv = sources_for_orders(conn, [r["id"] for r in rows])
        for r in rows:
            r["po_no"] = r.get("po_no") or r.get("order_no")
            r["style_code"] = r.get("style_ref")
            r["delivery_date"] = r.get("ship_date")
            s = resolve(smv.get(r["id"]) or [])
            r["smv"] = s["smv"]
            r["smv_conflict"] = s["conflict"]
        return rows
    finally:
        conn.close()


def list_sf_orders():
    """The LEGACY /factory order list. Kept for one caller only: /factory/approvals
    still stores sf_orders ids, and handing it real order ids would silently
    re-label every request already in the table."""
    conn = get_db()
    try:
        return _rows(conn, "SELECT o.*, s.code style_code, s.name style_name, s.smv "
                           "FROM sf_orders o LEFT JOIN sf_styles s ON s.id=o.style_id "
                           "ORDER BY o.id DESC")
    finally:
        conn.close()


def list_lines():
    """production_lines has always been the shared line master — no duplication here."""
    conn = get_db()
    try:
        return _rows(conn, "SELECT id, name, area, status, operators FROM production_lines "
                           "ORDER BY id")
    finally:
        conn.close()


# ==========================================================================
# Production — the MES hourly board
# ==========================================================================
def list_production(limit=200):
    conn = get_db()
    try:
        rows = _rows(conn,
                     "SELECT h.line_id, h.work_date, h.hour_slot, h.target_qty, h.actual_qty, "
                     "h.reject_qty, h.operators, h.smv, l.name AS line_name, "
                     "COALESCE(o.po_no, o.order_no) AS po_no "
                     "FROM mes_hourly h LEFT JOIN production_lines l ON l.id=h.line_id "
                     "LEFT JOIN ord_orders o ON o.id=h.order_id "
                     "ORDER BY h.work_date DESC, h.line_id, h.hour_slot LIMIT ?", (int(limit),))
        for r in rows:
            tgt = r.get("target_qty") or 0
            r["achievement"] = round(100.0 * (r.get("actual_qty") or 0) / tgt, 1) if tgt else 0.0
            r["rag"] = rag(r["achievement"])
        return rows
    finally:
        conn.close()


# ==========================================================================
# Quality — the QMS inspection register
# ==========================================================================
def list_quality(limit=200):
    from app.quality import services as qc
    rows = qc.list_inspections(limit=limit)
    for r in rows:
        r["po_no"] = r.get("order_no") or "—"
    return rows


# ==========================================================================
# Cutting & bundles — the MES bundle ledger + the warehouse roll master
# ==========================================================================
def _rolls(limit=200):
    """Fabric rolls from the WAREHOUSE, which owns roll stock. Read only: nothing
    here reserves, issues or consumes a metre."""
    try:
        from app.warehouse import services as wh
        return (wh.list_rolls() or [])[:limit]
    except Exception:  # noqa: BLE001
        return []


def bundles():
    from app.mes import services as mes
    rows = mes.list_bundles(limit=300)
    wip = mes.wip_by_section()
    by_stage, pieces = {}, 0
    for b in rows:
        by_stage[b["status"]] = by_stage.get(b["status"], 0) + 1
        pieces += int(b.get("qty") or 0)
    return {"bundles": rows, "rolls": _rolls(), "by_stage": by_stage, "pieces": pieces,
            "count": len(rows), "wip": wip["rows"], "wip_units": wip["wip_units"]}


# ==========================================================================
# Laundry / wash — the wash module's executed lots
# ==========================================================================
def _batch_utilities(limit=500):
    """Every executed wash lot with what it actually consumed.

    The wash module measures a LOAD IN KILOGRAMS, not pieces, because that is what
    the recipe and the machine are set by — so the intensities are per kg here and
    the page says so rather than inventing a per-piece figure.

    Water is the METERED number when the machine reported one; otherwise it is the
    recipe version's own bath total scaled to the load that ran. Chemicals and heat
    exist only as the version's derived totals, scaled the same way.
    """
    try:
        from app.wash import services as wsh
    except Exception:  # noqa: BLE001
        return []
    rows = wsh.list_batches(limit=limit)
    # ponytail: one totals() query per DISTINCT version, not per batch — fold into a
    # single GROUP BY if the batch log ever gets long enough to feel it.
    cache, out = {}, []
    conn = get_db()
    try:
        orders = {r["id"]: (r.get("po_no") or r.get("order_no"))
                  for r in _rows(conn, "SELECT id, order_no, po_no FROM ord_orders")}
    finally:
        conn.close()
    for b in rows:
        vid = b.get("version_id")
        if vid not in cache:
            try:
                cache[vid] = wsh.version_totals(vid) if vid else {}
            except Exception:  # noqa: BLE001
                cache[vid] = {}
        v = cache[vid] or {}
        load, ref = _f(b.get("load_kg")), _f(v.get("load_kg"))
        k = (load / ref) if (load > 0 and ref > 0) else 1.0
        metered = _f(b.get("act_water_l"))
        out.append({**b,
                    "po_no": orders.get(b.get("order_id")) or "—",
                    "recipe": "%s%s" % (b.get("code") or b.get("name") or "—",
                                        (" v%s" % b["version"]) if b.get("version") else ""),
                    "water_l": round(metered if metered > 0 else _f(v.get("water_l")) * k, 1),
                    "water_metered": metered > 0,
                    "chem_kg": round(_f(v.get("chem_g")) * k / 1000.0, 2),
                    "heat_lk": round(_f(v.get("heat_lk")) * k, 1),
                    "load_kg": round(load, 1),
                    "off_recipe": bool(b.get("deviation"))})
    return out


def sustainability(rows=None):
    rows = _batch_utilities() if rows is None else rows
    kg = sum(r["load_kg"] for r in rows)
    water = sum(r["water_l"] for r in rows)
    chem = sum(r["chem_kg"] for r in rows)
    heat = sum(r["heat_lk"] for r in rows)
    return {"water_per_kg": round(water / kg, 1) if kg else 0,
            "chem_per_kg": round(chem * 1000 / kg, 1) if kg else 0,     # grams per kg
            "heat_per_kg": round(heat / kg, 1) if kg else 0,            # litre-kelvin per kg
            "load_kg": round(kg, 1), "water": round(water), "chem": round(chem, 1),
            "deviations": sum(1 for r in rows if r["off_recipe"]), "batches": len(rows)}


def wash_list():
    rows = _batch_utilities()
    return {"rows": rows, "agg": sustainability(rows)}


# ==========================================================================
# Workforce — the people module's piece-rate record
# ==========================================================================
def workforce(days=30):
    """Operator scorecards from ppl_piece_rate — the SAME earned-minute figures
    payroll is computed from, not a second calculation of the same thing.
    Efficiency is minute-weighted (total earned / total worked): a mean of daily
    percentages over unequal shifts lies."""
    from app.people import services as ppl
    to_d = date.today()
    from_d = to_d - timedelta(days=max(1, int(days)) - 1)
    s = ppl.incentive_summary(str(from_d), str(to_d))
    rows = []
    for r in s["rows"]:
        eff = _f(r.get("efficiency_pct"))
        rows.append({"code": r.get("employee_code") or "—",
                     "name": r.get("employee_name") or "—",
                     "grade": r.get("department") or "—",
                     "hours": round(_f(r.get("minutes_worked")) / 60.0, 1),
                     "produced": int(_f(r.get("pieces"))), "efficiency": eff, "rag": rag(eff)})
    rows.sort(key=lambda x: x["efficiency"])
    return {"rows": rows, "avg": s["efficiency_pct"], "count": len(rows),
            "from_date": str(from_d), "to_date": str(to_d), "days": days}


# ==========================================================================
# Cost per piece — what the FLOOR did to the price
# ==========================================================================
def costing():
    """Conversion cost per piece from real floor records.

    NOT the order's cost sheet — /costing owns the priced sheet, the BOM, the
    actuals and the margin. What this adds is what the floor did to that price:
    minutes actually run, pieces actually scrapped, utilities actually consumed.
    The minute rate is the order's OWN cst_sheets.cm_rate whenever it is set; only
    when it is not does the internal fallback tariff stand in, and the row says so.

    Downtime is deliberately absent from the per-order figures: mes_downtime is
    booked per LINE and per DAY, and a line runs more than one order — splitting
    those minutes across orders would be an invented number. The factory-level
    cost of lost minutes is on /factory/intelligence, where it is honest.
    """
    from app.services.smv import sources_for_orders, resolve
    conn = get_db()
    try:
        orders = _rows(conn, "SELECT id, order_no, po_no, style_ref, qty, currency "
                             "FROM ord_orders ORDER BY id DESC LIMIT 200")
        smv = sources_for_orders(conn, [o["id"] for o in orders])
        prod = {r["order_id"]: r for r in _rows(
            conn, "SELECT order_id, COALESCE(SUM(actual_qty),0) AS produced, "
                  "COALESCE(SUM(reject_qty),0) AS reject FROM mes_hourly "
                  "WHERE order_id IS NOT NULL GROUP BY order_id")}
        rates = {r["order_id"]: _f(r["cm_rate"]) for r in
                 _rows(conn, "SELECT order_id, cm_rate FROM cst_sheets")}
    finally:
        conn.close()

    wash_by_order = {}
    for b in _batch_utilities():
        w = wash_by_order.setdefault(b.get("order_id"), {"water": 0.0, "chem": 0.0, "kg": 0.0})
        w["water"] += b["water_l"]
        w["chem"] += b["chem_kg"]
        w["kg"] += b["load_kg"]

    out = []
    for o in orders:
        p = prod.get(o["id"]) or {}
        produced, reject = int(_f(p.get("produced"))), int(_f(p.get("reject")))
        w = wash_by_order.get(o["id"]) or {}
        if not produced and not w:
            continue                       # the floor has not touched this order yet
        s = resolve(smv.get(o["id"]) or [])
        smv_v = s["smv"] or 0.0
        cm = rates.get(o["id"]) or 0.0
        priced = cm > 0
        if not priced:
            cm = _RATE["labor_per_min"]
        labor = produced * smv_v * cm
        # A scrapped piece burnt its own minutes twice over — it was made and it is
        # gone. No separate scrap tariff is invented; it costs what it cost to sew.
        reject_c = reject * smv_v * cm
        wash_c = w.get("water", 0.0) * _RATE["water_per_l"] + w.get("chem", 0.0) * _RATE["chem_per_kg"]
        total = labor + reject_c + wash_c
        out.append({"po_no": o.get("po_no") or o.get("order_no"), "style": o.get("style_ref"),
                    "produced": produced, "reject": reject, "smv": smv_v,
                    "rate": round(cm, 4), "priced": priced,
                    "labor": round(labor, 0), "rework": round(reject_c, 0),
                    "wash": round(wash_c, 0), "total": round(total, 0),
                    "cpp": round(total / produced, 2) if produced else 0,
                    "currency": o.get("currency") or ""})
    return {"rows": out, "rates": _RATE}


# ==========================================================================
# Smart intelligence — money-first, all of it off real floor records
# ==========================================================================
def minute_bank():
    """Price the minutes the floor really lost: reason-coded downtime out of
    mes_downtime, and the minutes burnt on pieces the line itself rejected."""
    conn = get_db()
    try:
        lines = {l["id"]: l["name"] for l in _rows(conn, "SELECT id, name FROM production_lines")}
        down = {r["line_id"]: _f(r["m"]) for r in _rows(
            conn, "SELECT line_id, COALESCE(SUM(minutes),0) AS m FROM mes_downtime "
                  "GROUP BY line_id")}
        rej = {r["line_id"]: r for r in _rows(
            conn, "SELECT line_id, COALESCE(SUM(reject_qty),0) AS pcs, "
                  "COALESCE(SUM(reject_qty*smv),0) AS mins FROM mes_hourly GROUP BY line_id")}
    finally:
        conn.close()

    rows, tot_idle, tot_scrap = [], 0.0, 0.0
    for lid in set(list(down) + list(rej)):
        lost = down.get(lid, 0.0)
        r = rej.get(lid) or {}
        scrap_min = _f(r.get("mins"))
        idle, scrap = lost * CPM_LOADED, scrap_min * CPM_LOADED
        tot_idle += idle
        tot_scrap += scrap
        rows.append({"line": lines.get(lid) or ("Line %s" % lid), "lost_min": round(lost, 1),
                     "reject_pcs": int(_f(r.get("pcs"))), "reject_min": round(scrap_min, 1),
                     "idle": round(idle), "reject": round(scrap),
                     "total": round(idle + scrap)})
    rows.sort(key=lambda x: x["total"], reverse=True)
    return {"rows": rows, "total": round(tot_idle + tot_scrap),
            "breakdown": {"idle": round(tot_idle), "reject": round(tot_scrap)}}


def efficiency_bridge():
    """Decompose the day's target→actual gap into named causes, in minutes and
    money. Every input is a real floor record; the SMV used is the one the hours
    were actually recorded with (an operational record is exactly right here —
    what ran, not what was defined)."""
    from app.mes import services as mes
    wd = live_date()
    t = mes.dashboard(wd)["t"]
    target, actual = t["target"], t["actual"]
    smv = (t["earned_min"] / actual) if actual else 0.0
    gap_pcs = max(0, target - actual)
    gap_min = gap_pcs * smv
    lost_min = t["downtime_min"]
    quality_min = t["reject"] * smv
    balance_min = max(0.0, gap_min - lost_min - quality_min)
    comps = [
        {"name": "Downtime / no-feeding", "key": "sf.cause.downtime",
         "minutes": round(lost_min), "money": round(lost_min * CPM_LOADED)},
        {"name": "Quality redo (rejects)", "key": "sf.cause.quality",
         "minutes": round(quality_min), "money": round(quality_min * CPM_LOADED)},
        {"name": "Line balance / bottleneck", "key": "sf.cause.balance",
         "minutes": round(balance_min), "money": round(balance_min * CPM_LOADED)},
    ]
    tot_min = sum(c["minutes"] for c in comps) or 1
    for c in comps:
        c["pct"] = round(100 * c["minutes"] / tot_min, 1)
    return {"work_date": wd, "efficiency": t["achievement"], "gap_pcs": gap_pcs,
            "components": comps, "total_money": round(sum(c["money"] for c in comps))}


def ship_risk():
    """P(pass the buyer's AQL) per order, from the QMS register — the real
    inspections, next to the verdicts Z1.4 has already decided on them."""
    from app.quality import services as qc
    try:
        rows = qc.by_order()
        top = qc.top_defects(1)
    except Exception:  # noqa: BLE001
        return []
    driving = top[0]["defect_type"] if top else "—"
    out = []
    for r in rows:
        dhu = _f(r.get("dhu"))
        risk = max(0.0, min(100.0, (dhu - AQL_DHU_OK) * 18))
        passp = round(100 - risk, 1)
        out.append({"po_no": r.get("order_no") or "—", "dhu": round(dhu, 1), "pass": passp,
                    "rag": "green" if passp >= 90 else "amber" if passp >= 70 else "red",
                    "failed": r.get("failed") or 0, "driving": driving})
    out.sort(key=lambda x: x["pass"])
    return out


# ==========================================================================
# AI insights — rule-based, over the same real numbers
# ==========================================================================
def ai_insights():
    d = dashboard()
    out = []
    for l in d["live"]:
        if l["efficiency"] < 85:
            out.append({"severity": "high", "title": f"Line below target: {l['line']}",
                        "detail": f"Achievement {l['efficiency']}% ({l['actual']}/{l['target']}, "
                                  f"lost {l['lost']}m) on {d['work_date']}.",
                        "action": "Rebalance the line / reassign an operator to the slow operation."})
            break
    if d["kpis"]["dhu"] > 5:
        top = d["pareto"][0]["code"] if d["pareto"] else "stitching"
        out.append({"severity": "medium", "title": f"DHU above 5% ({d['kpis']['dhu']})",
                    "detail": f"Top defect: {top}. Right-first-time is {d['kpis']['rft']}%.",
                    "action": f"Brief the line on {top}; open a root-cause review."})
    if d["sustain"]["deviations"]:
        out.append({"severity": "high",
                    "title": f"Wash batches off recipe ({d['sustain']['deviations']})",
                    "detail": "Time, temperature or load fell outside the recipe tolerance — "
                              "that is where shade variance and rewash come from.",
                    "action": "Review the deviations on /wash and re-check the machine load."})
    wf = workforce()
    if wf["rows"] and wf["rows"][0]["efficiency"] < 75:
        w = wf["rows"][0]
        out.append({"severity": "medium", "title": f"Low operator efficiency: {w['name']}",
                    "detail": f"{w['efficiency']}% over {w['hours']} hours ({w['grade']}).",
                    "action": "Coach or reassign; check the operation-to-skill match."})
    if not out:
        out.append({"severity": "info", "title": "All key metrics nominal",
                    "detail": "No anomalies detected this run.", "action": "—"})
    return out
