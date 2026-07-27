"""
MES services — hourly board, reason-coded downtime, OEE / line efficiency and the
bundle-movement ledger that yields live WIP.

All time figures are MINUTES. All quantities are whole pieces. Every ratio is
guarded: a missing denominator returns 0.0, never a crash and never infinity.
"""
import math
from datetime import date, datetime, timezone

from app.db import get_db
from .constants import (SLOT_MINUTES, HOUR_SLOTS, RAG_GREEN, RAG_AMBER,
                        DOWNTIME_ALERT_MIN, DOWNTIME_REASONS, SECTIONS,
                        MAX_QTY, MAX_SMV, MAX_DOWNTIME_MIN, MAX_FUTURE_DAYS, MAX_ID)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return str(date.today())


def _f(v, d=0.0):
    """Blank/garbage becomes the default. inf and nan are garbage too: nan poisons
    every SUM it touches and silently disables every `x < 0` guard (all nan
    comparisons are False), and inf makes a percentage meaningless."""
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError, AttributeError):
        return d
    return f if math.isfinite(f) else d


def _i(v, d=0):
    """Form fields arrive as strings. A typo must not 500 the entry screen, so
    "1e400" (-> inf -> int() raises OverflowError) comes back as the default."""
    f = _f(v, None)
    return d if f is None else int(f)


def _id(v):
    """A row id sanitised before it can reach SQL. Ids come from URLs — Werkzeug's
    <int:> converter happily matches thirty digits — and from form posts, and an id
    wider than an INTEGER column raises out of the driver: a 500 where a 404 or a
    polite refusal belongs. Out of range becomes -1, an id no row can have, so the
    caller's ordinary existence check turns it into the right message."""
    n = _i(v, 0)
    return n if 0 <= n <= MAX_ID else -1


def _iso(v):
    """A work_date must be a real ISO day. Junk here is not cosmetic: resolve_date()
    falls back to MAX(work_date), and 'not-a-date' sorts above every real date, so
    one bad row would pin every dashboard on a day that does not exist."""
    s = str(v or "").strip()[:10]
    if not s:
        return _today()
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return None


def _too_far_ahead(iso_day):
    """A typo'd YEAR ("2062-07-25" for "2026-07-25") is the commonest date error on
    a shop-floor keyboard, and there is no edit/delete screen to undo it: the row
    would sit in the table for ever, above every real day. Refuse it at the door."""
    return (date.fromisoformat(iso_day) - date.today()).days > MAX_FUTURE_DAYS


def _order_missing(conn, oid):
    """True only when the orders module IS present and the id is not in it. An
    install without ord_orders must still accept production, so a failed lookup
    is treated as 'cannot tell' — never as a refusal."""
    if not oid:
        return False
    try:
        return conn.execute("SELECT id FROM ord_orders WHERE id=?", (oid,)).fetchone() is None
    except Exception:
        # PostgreSQL: the failed lookup leaves the transaction unusable, and the
        # caller is about to INSERT. Discard it (nothing of ours is pending yet) or
        # the save that is supposed to degrade gracefully raises instead.
        conn.rollback()
        return False


def _bell_once(conn, ext_key, severity, title, message, link="/mes"):
    """Bell alert, deduped on notifications.ext_key so a refresh or a re-run of the
    sweep never spams the same line-hour twice."""
    if conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE ext_key=?",
                    (ext_key,)).fetchone()["c"]:
        return False
    conn.execute(
        "INSERT INTO notifications (severity,module,title,message,link,ext_key,created_at) "
        "VALUES (?,?,?,?,?,?,?)", (severity, "mes", title, message, link, ext_key, _now()))
    return True


# ==========================================================================
# Pure arithmetic — the numbers the factory is managed by. No DB, no I/O.
# ==========================================================================
def achievement(actual, target):
    """Hourly achievement % = actual / target x 100. No target = nothing to achieve."""
    t = _f(target)
    return round(_f(actual) / t * 100.0, 1) if t > 0 else 0.0


def rag(pct):
    """Board colour. Thresholds are constants (RAG_GREEN / RAG_AMBER)."""
    p = _f(pct)
    return "green" if p >= RAG_GREEN else ("amber" if p >= RAG_AMBER else "red")


def rag_for(actual, target):
    """Board colour for an hour/line/day. An hour with NO target was never planned,
    so it cannot have missed its plan: it is grey ('none'), not red. Colouring it
    red raises a false alarm on the supervisor's screen and inflates red_hours.

    The threshold is applied to the UNROUNDED ratio: achievement() rounds to 1 dp
    for display, and 94.96% rounded to 95.0 would show green on a board whose own
    legend says green is >= 95%."""
    t = _f(target)
    return rag(_f(actual) / t * 100.0) if t > 0 else "none"


def efficiency(earned_min, operator_min):
    """Line efficiency % = (produced x SMV) / (operators x minutes worked) x 100.

    earned_min   = SUM(actual_qty x SMV)          -> standard minutes produced
    operator_min = SUM(operators x SLOT_MINUTES)  -> man-minutes made available
    This is the number a garment factory is actually managed by; 100% means the
    line delivered exactly its standard work content for the manpower it burned.
    """
    om = _f(operator_min)
    return round(_f(earned_min) / om * 100.0, 1) if om > 0 else 0.0


def oee_parts(planned_min, downtime_min, ideal_min, total_units, good_units):
    """OEE decomposition, all times in LINE-CLOCK minutes, all parts percentages.

        Availability = (planned_time - downtime) / planned_time x 100
        Performance  = (actual_output x ideal_cycle_time) / operating_time x 100
        Quality      = good_units / total_units x 100
        OEE          = Availability x Performance x Quality / 10000

    `ideal_min` is the pre-summed numerator of Performance: SMV is MAN-minutes, so
    one piece should occupy the LINE for SMV/operators clock minutes — the caller
    sums actual_qty x SMV / operators per hour (operators vary through a shift).

    Downtime is clamped to planned_time: a mistyped 999 minutes on a 480-minute day
    must floor Availability at 0, never make it negative.
    """
    planned = max(0.0, _f(planned_min))
    down = min(max(0.0, _f(downtime_min)), planned)
    operating = planned - down
    total = _f(total_units)
    good = max(0.0, _f(good_units))
    a = (operating / planned * 100.0) if planned > 0 else 0.0
    p = (_f(ideal_min) / operating * 100.0) if operating > 0 else 0.0
    q = (good / total * 100.0) if total > 0 else 0.0
    return {"availability": round(a, 1), "performance": round(p, 1), "quality": round(q, 1),
            "oee": round(a * p * q / 10000.0, 1),
            "planned_min": round(planned, 1), "downtime_min": round(down, 1),
            "operating_min": round(operating, 1)}


# ==========================================================================
# Reads
# ==========================================================================
def resolve_date(d=None):
    """The date the board should show: the one asked for, else today, else the last
    day that actually has production (so a demo/idle install is never blank)."""
    # A junk ?date= must not become the board's date: it would query a day that
    # cannot exist, blank every panel and render an invalid value into the picker.
    if d and _iso(d):
        return _iso(d)
    conn = get_db()
    try:
        t = _today()
        if conn.execute("SELECT COUNT(*) AS c FROM mes_hourly WHERE work_date=?",
                        (t,)).fetchone()["c"]:
            return t
        # Only days that have already HAPPENED may be fallen back on. A row booked
        # against a future day (a pre-loaded target, or a typo'd year) is the MAX of
        # the column, and would otherwise pin every dashboard on it permanently.
        r = conn.execute("SELECT MAX(work_date) AS d FROM mes_hourly WHERE work_date<=?",
                         (t,)).fetchone()
        return (r["d"] or t) if r else t
    finally:
        conn.close()


def entry_date(d=None):
    """The day the ENTRY form pre-fills — the requested one, else TODAY.

    Deliberately NOT resolve_date(): that falls back to the last day WITH
    production, so on any morning before the first entry the form would pre-fill an
    earlier day, and the first save of the shift would land on that day's UNIQUE
    (line, date, hour) cell and silently overwrite a closed day's actuals."""
    return _iso(d) or _today()


def list_lines():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, area, status, operators FROM production_lines ORDER BY id").fetchall()]
    finally:
        conn.close()


def list_orders():
    """Order picker. Degrades to [] if the orders module has not seeded yet."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, order_no, buyer, style_name FROM ord_orders "
            "WHERE status NOT IN ('closed','cancelled') ORDER BY ship_date ASC LIMIT 60").fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _rollup(conn, work_date, line_id=None):
    """Per-line roll-up for one day + the raw reason-coded downtime rows."""
    q = ("SELECT h.*, l.name AS line_name FROM mes_hourly h "
         "LEFT JOIN production_lines l ON l.id = h.line_id WHERE h.work_date=?")
    args = [work_date]
    if line_id:
        q += " AND h.line_id=?"
        args.append(line_id)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY h.line_id, h.hour_slot", args).fetchall()]

    dq = "SELECT line_id, reason, SUM(minutes) AS m, COUNT(*) AS n FROM mes_downtime WHERE work_date=?"
    dargs = [work_date]
    if line_id:
        dq += " AND line_id=?"
        dargs.append(line_id)
    dt = [dict(r) for r in conn.execute(dq + " GROUP BY line_id, reason", dargs).fetchall()]
    down_by_line = {}
    for d in dt:
        down_by_line[d["line_id"]] = down_by_line.get(d["line_id"], 0.0) + _f(d["m"])

    acc = {}
    for r in rows:
        a = acc.setdefault(r["line_id"], {
            "line_id": r["line_id"], "line_name": r["line_name"] or ("Line %s" % r["line_id"]),
            "hours": 0, "target": 0, "actual": 0, "reject": 0, "red_hours": 0,
            "earned_min": 0.0, "ideal_min": 0.0, "operator_min": 0.0})
        ops, smv, act = _i(r["operators"]), _f(r["smv"]), _i(r["actual_qty"])
        a["hours"] += 1
        a["target"] += _i(r["target_qty"])
        a["actual"] += act
        a["reject"] += _i(r["reject_qty"])
        # An hour recorded with NO operators earns nothing. It contributes to neither
        # side of efficiency = earned / man-minutes: counting the pieces while the
        # denominator stays 0 would report free output (4 hours at 30 pcs, one of
        # them unmanned, read 133% efficiency) and that number is paid as a bonus.
        if ops > 0:
            a["earned_min"] += act * smv                   # standard (man) minutes produced
            a["ideal_min"] += act * smv / ops              # line-clock minutes owed
            a["operator_min"] += ops * SLOT_MINUTES
        if rag_for(act, r["target_qty"]) == "red":
            a["red_hours"] += 1

    # A line can lose the whole day without a single hourly row. Its minutes are in
    # the day KPI, so it must also be in the table and the totals or the dashboard
    # contradicts itself (KPI 90 min above a table summing to 0).
    for lid in down_by_line:
        if lid in acc:
            continue
        nm = conn.execute("SELECT name FROM production_lines WHERE id=?", (lid,)).fetchone()
        acc[lid] = {"line_id": lid, "line_name": (nm["name"] if nm else None) or ("Line %s" % lid),
                    "hours": 0, "target": 0, "actual": 0, "reject": 0, "red_hours": 0,
                    "earned_min": 0.0, "ideal_min": 0.0, "operator_min": 0.0}

    out = []
    for a in acc.values():
        a["planned_min"] = a["hours"] * SLOT_MINUTES
        a["achievement"] = achievement(a["actual"], a["target"])
        a["rag"] = rag_for(a["actual"], a["target"])
        a["efficiency"] = efficiency(a["earned_min"], a["operator_min"])
        booked = down_by_line.get(a["line_id"], 0.0)
        # good = produced - rejected (rejects are validated as a subset on write)
        a.update(oee_parts(a["planned_min"], booked,
                           a["ideal_min"], a["actual"], a["actual"] - a["reject"]))
        # oee_parts CLAMPS downtime to planned time so Availability cannot go
        # negative — but the reported figure must stay the minutes that were
        # actually booked, or a line down 500 minutes reads as down 60.
        a["downtime_min"] = round(booked, 1)
        # The clamp has to be kept PER LINE for the factory roll-up: summing raw
        # booked minutes lets one line's mistyped 500 on a 60-minute morning wipe
        # out the availability of every other line in the building.
        a["down_clamped"] = round(min(booked, a["planned_min"]), 1)
        a["earned_min"] = round(a["earned_min"], 1)
        a["ideal_min"] = round(a["ideal_min"], 1)
        out.append(a)
    out.sort(key=lambda x: x["line_name"])
    return out, dt


def _totals(lines):
    """Factory totals. Percentages are recomputed from the summed minutes/pieces —
    never averaged, which would weight a 1-hour line like an 8-hour line."""
    t = {k: 0 for k in ("hours", "target", "actual", "reject", "red_hours")}
    for k in ("earned_min", "ideal_min", "operator_min", "planned_min", "downtime_min",
              "down_clamped"):
        t[k] = 0.0
    for ln in lines:
        for k in t:
            t[k] += ln.get(k, 0)
    t["achievement"] = achievement(t["actual"], t["target"])
    t["rag"] = rag_for(t["actual"], t["target"])
    t["efficiency"] = efficiency(t["earned_min"], t["operator_min"])
    booked = t["downtime_min"]
    # Availability is driven by the sum of the PER-LINE clamped losses: a line can
    # only lose the minutes it was planned for, and no line may lose another's.
    t.update(oee_parts(t["planned_min"], t["down_clamped"], t["ideal_min"],
                       t["actual"], t["actual"] - t["reject"]))
    t["downtime_min"] = round(booked, 1)      # report booked, clamp only the maths
    return t


def hourly_board(work_date=None):
    """line x hour grid, RAG coloured — the core screen."""
    work_date = resolve_date(work_date)
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT h.*, l.name AS line_name FROM mes_hourly h "
            "LEFT JOIN production_lines l ON l.id = h.line_id WHERE h.work_date=? "
            "ORDER BY l.name, h.hour_slot", (work_date,)).fetchall()
        grid = {}
        for r in rows:
            r = dict(r)
            g = grid.setdefault(r["line_id"], {
                "line_id": r["line_id"], "line_name": r["line_name"] or ("Line %s" % r["line_id"]),
                "cells": {}, "target": 0, "actual": 0})
            pct = achievement(r["actual_qty"], r["target_qty"])
            g["cells"][r["hour_slot"]] = {"target": _i(r["target_qty"]), "actual": _i(r["actual_qty"]),
                                          "pct": pct, "rag": rag_for(r["actual_qty"], r["target_qty"])}
            g["target"] += _i(r["target_qty"])
            g["actual"] += _i(r["actual_qty"])
        out = []
        for g in grid.values():
            g["achievement"] = achievement(g["actual"], g["target"])
            g["rag"] = rag_for(g["actual"], g["target"])
            out.append(g)
        out.sort(key=lambda x: x["line_name"])
        return {"work_date": work_date, "slots": HOUR_SLOTS, "lines": out}
    finally:
        conn.close()


def line_detail(line_id, work_date=None):
    """One line, one day: OEE breakdown + the hours and the coded losses behind it."""
    line_id = _id(line_id)
    work_date = resolve_date(work_date)
    conn = get_db()
    try:
        ln = conn.execute("SELECT * FROM production_lines WHERE id=?", (line_id,)).fetchone()
        if not ln:
            return None
        lines, dt = _rollup(conn, work_date, line_id)
        stat = lines[0] if lines else _totals([])
        stat.setdefault("line_name", ln["name"])
        hours = [dict(r) for r in conn.execute(
            "SELECT * FROM mes_hourly WHERE line_id=? AND work_date=? ORDER BY hour_slot",
            (line_id, work_date)).fetchall()]
        # mes_hourly.smv is an operational RECORD — what the supervisor says ran
        # that hour — so it keeps whatever was typed. But an hour booked at 24.5
        # while the style is defined at 22.0 silently moves earned minutes, and
        # therefore efficiency and OEE, so the disagreement is shown. One resolve
        # per distinct order on the day, not one per hour.
        from app.services.smv import SMV_TOLERANCE, smv_for
        canon = {}
        for h in hours:
            h["achievement"] = achievement(h["actual_qty"], h["target_qty"])
            h["rag"] = rag_for(h["actual_qty"], h["target_qty"])
            oid = h.get("order_id")
            if oid and oid not in canon:
                canon[oid] = smv_for(conn, order_id=oid)
            ref = (canon.get(oid) or {}).get("smv")
            h["smv_canonical"] = ref
            h["smv_differs"] = bool(ref and _f(h["smv"]) > 0
                                    and abs(_f(h["smv"]) - ref) > SMV_TOLERANCE)
        losses = sorted([{"reason": d["reason"], "minutes": round(_f(d["m"]), 1), "events": d["n"]}
                         for d in dt], key=lambda x: -x["minutes"])
        # A line that was down all day has losses but NO hourly row, so the roll-up
        # never sees it. Take the KPI straight from the losses or the page shows
        # "0 minutes" above a table listing hours of stoppage.
        stat["downtime_min"] = round(sum(x["minutes"] for x in losses), 1)
        return {"line": dict(ln), "work_date": work_date, "stat": stat,
                "hours": hours, "losses": losses}
    finally:
        conn.close()


def wip_by_section():
    """Live WIP = pieces standing in each section, derived from the move ledger:
        origin section starts with the cut qty; every scan moves pieces on.
        wip[s] = cut_at_origin(s) + moved_in(s) - moved_out(s)
    Rejected bundles are excluded entirely — they are scrap, not WIP. The last
    section (dispatch) is finished goods and is reported but not counted as WIP."""
    conn = get_db()
    try:
        bal = {s: 0 for s in SECTIONS}
        for r in conn.execute("SELECT origin_section AS s, SUM(qty) AS q FROM mes_bundles "
                              "WHERE status!='rejected' GROUP BY origin_section").fetchall():
            bal[r["s"]] = bal.get(r["s"], 0) + _i(r["q"])
        for r in conn.execute(
                "SELECT m.to_section AS s, SUM(m.qty) AS q FROM mes_bundle_moves m "
                "JOIN mes_bundles b ON b.id=m.bundle_id WHERE b.status!='rejected' "
                "GROUP BY m.to_section").fetchall():
            bal[r["s"]] = bal.get(r["s"], 0) + _i(r["q"])
        for r in conn.execute(
                "SELECT m.from_section AS s, SUM(m.qty) AS q FROM mes_bundle_moves m "
                "JOIN mes_bundles b ON b.id=m.bundle_id WHERE b.status!='rejected' "
                "GROUP BY m.from_section").fetchall():
            bal[r["s"]] = bal.get(r["s"], 0) - _i(r["q"])
        rows = [{"section": s, "qty": bal.get(s, 0)} for s in SECTIONS]
        total = sum(r["qty"] for r in rows if r["section"] != SECTIONS[-1])
        return {"rows": rows, "wip_units": total}
    finally:
        conn.close()


def list_bundles(order_id=None, status=None, limit=300):
    order_id = _id(order_id) if order_id else None
    conn = get_db()
    try:
        q = "SELECT * FROM mes_bundles WHERE 1=1"
        args = []
        if order_id:
            q += " AND order_id=?"
            args.append(order_id)
        if status:
            q += " AND status=?"
            args.append(status)
        args.append(int(limit))
        rows = [dict(r) for r in conn.execute(q + " ORDER BY id DESC LIMIT ?", args).fetchall()]
        return _decorate_orders(conn, rows)
    finally:
        conn.close()


def recent_moves(limit=25):
    """The scan ledger. This is the audit trail a piece-rate wage dispute is settled
    against, so it is append-only and always shows who scanned what, where, when."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT m.*, b.bundle_no FROM mes_bundle_moves m "
            "JOIN mes_bundles b ON b.id = m.bundle_id ORDER BY m.id DESC LIMIT ?",
            (int(limit),)).fetchall()]
    finally:
        conn.close()


def _decorate_orders(conn, rows):
    """Attach order_no/buyer without a JOIN that breaks when ord_orders is absent."""
    ids = {r.get("order_id") for r in rows if r.get("order_id")}
    look = {}
    for oid in ids:
        try:
            o = conn.execute("SELECT order_no, buyer FROM ord_orders WHERE id=?", (oid,)).fetchone()
        except Exception:
            break
        if o:
            look[oid] = dict(o)
    for r in rows:
        o = look.get(r.get("order_id")) or {}
        r["order_no"] = o.get("order_no")
        r["buyer"] = o.get("buyer")
    return rows


def order_reconciliation():
    """Cut vs sewn per order.

        cut     = pieces bundled (scrap excluded)
        sewn    = pieces RELEASED from the cut room = scanned OUT of the bundle's
                  origin section, minus anything scanned BACK into it
        balance = cut - sewn = the pieces still standing in the cut room

    INVARIANT: 0 <= sewn <= cut. Counting every scan INTO 'sewing' instead counted a
    rework return twice — finishing -> sewing is one click on the scan form — and
    printed Sewn 200 against Cut 100 with a NEGATIVE balance. A piece can only
    re-enter the origin section after leaving it, and a move can never exceed the
    section balance, so out - back stays between 0 and the cut quantity.
    """
    conn = get_db()
    try:
        cut = {r["order_id"]: _i(r["q"]) for r in conn.execute(
            "SELECT order_id, SUM(qty) AS q FROM mes_bundles WHERE status!='rejected' "
            "GROUP BY order_id").fetchall()}
        released = {r["order_id"]: _i(r["q"]) for r in conn.execute(
            "SELECT b.order_id AS order_id, SUM(m.qty) AS q FROM mes_bundle_moves m "
            "JOIN mes_bundles b ON b.id=m.bundle_id WHERE m.from_section=b.origin_section "
            "AND b.status!='rejected' GROUP BY b.order_id").fetchall()}
        back = {r["order_id"]: _i(r["q"]) for r in conn.execute(
            "SELECT b.order_id AS order_id, SUM(m.qty) AS q FROM mes_bundle_moves m "
            "JOIN mes_bundles b ON b.id=m.bundle_id WHERE m.to_section=b.origin_section "
            "AND b.status!='rejected' GROUP BY b.order_id").fetchall()}
        sewn = {oid: released.get(oid, 0) - back.get(oid, 0) for oid in cut}
        rows = [{"order_id": oid, "cut": c, "sewn": sewn.get(oid, 0),
                 "balance": c - sewn.get(oid, 0)} for oid, c in cut.items()]
        rows.sort(key=lambda x: -x["balance"])
        return _decorate_orders(conn, rows)
    finally:
        conn.close()


def dashboard(work_date=None):
    work_date = resolve_date(work_date)
    conn = get_db()
    try:
        lines, dt = _rollup(conn, work_date)
        t = _totals(lines)
        # Day downtime KPI counts EVERY coded loss, including on lines with no hourly
        # entry yet — those minutes are real even though their OEE is undefined.
        allday = conn.execute("SELECT COALESCE(SUM(minutes),0) AS m FROM mes_downtime "
                              "WHERE work_date=?", (work_date,)).fetchone()
        top = {}
        for d in dt:
            top[d["reason"]] = top.get(d["reason"], 0.0) + _f(d["m"])
        top_rows = sorted([{"reason": k, "minutes": round(v, 1)} for k, v in top.items()],
                          key=lambda x: -x["minutes"])
        running = conn.execute("SELECT COUNT(*) AS c FROM production_lines "
                               "WHERE status='running'").fetchone()["c"]
    finally:
        conn.close()
    wip = wip_by_section()
    return {"work_date": work_date, "t": t, "lines": lines,
            "downtime_day": round(_f(allday["m"]), 1), "downtime_top": top_rows[:7],
            "lines_running": running, "wip": wip["rows"], "wip_units": wip["wip_units"],
            "recon": order_reconciliation()[:6]}


# ==========================================================================
# Writes — each returns (ok, message_key)
# ==========================================================================
def save_hourly(data, user):
    """Upsert one board cell. Re-submitting the same (line, date, hour) EDITS it:
    the unique index is what stops a double-click inventing a second hour of output."""
    line_id = _id(data.get("line_id"))
    slot = (data.get("hour_slot") or "").strip()
    work_date = _iso(data.get("work_date"))
    if not work_date:
        return False, "mes.msg.bad_date"
    if _too_far_ahead(work_date):
        return False, "mes.msg.future_date"
    if slot not in HOUR_SLOTS:
        return False, "mes.msg.bad_slot"
    target, actual = _i(data.get("target_qty")), _i(data.get("actual_qty"))
    reject, operators = _i(data.get("reject_qty")), _i(data.get("operators"))
    smv = _f(data.get("smv"))
    if min(target, actual, reject, operators) < 0 or smv < 0:
        return False, "mes.msg.negative"
    # A fat-fingered paste must not reach the table: these columns are INTEGER,
    # which is 4 bytes on PostgreSQL, and one absurd row wrecks every roll-up.
    if max(target, actual, reject, operators) > MAX_QTY or smv > MAX_SMV:
        return False, "mes.msg.too_big"
    # INVARIANT: rejects are a subset of what was produced, so good = actual - reject >= 0.
    if reject > actual:
        return False, "mes.msg.reject_gt_actual"
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM production_lines WHERE id=?", (line_id,)).fetchone():
            return False, "mes.msg.no_line"
        order_id = _id(data.get("order_id")) or None
        if _order_missing(conn, order_id):
            return False, "mes.msg.no_order"
        cell = ("SELECT id FROM mes_hourly WHERE line_id=? AND work_date=? AND hour_slot=?",
                (line_id, work_date, slot))
        ex = conn.execute(*cell).fetchone()
        vals = (order_id, target, actual, reject, operators, smv,
                data.get("notes") or None)

        def _edit(row_id):
            conn.execute("UPDATE mes_hourly SET order_id=?, target_qty=?, actual_qty=?, reject_qty=?, "
                         "operators=?, smv=?, notes=?, updated_at=? WHERE id=?",
                         (*vals, _now(), row_id))

        if ex:
            _edit(ex["id"])
        else:
            try:
                conn.execute("INSERT INTO mes_hourly (line_id,work_date,hour_slot,order_id,target_qty,"
                             "actual_qty,reject_qty,operators,smv,notes,created_by,created_at) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                             (line_id, work_date, slot, *vals, (user or {}).get("username"), _now()))
            except Exception:
                # Lost the race for this cell: a double-clicked Save on two workers has
                # both requests see it missing, and the UNIQUE index refuses the second
                # INSERT. The index did its job — no second hour of output was invented
                # — so finish the save as the EDIT it was always meant to be instead of
                # raising a 500 at the supervisor. rollback() first: on PostgreSQL the
                # failed statement leaves the transaction unusable.
                conn.rollback()
                ex = conn.execute(*cell).fetchone()
                if not ex:
                    raise
                _edit(ex["id"])
        conn.commit()
        return True, "mes.msg.saved"
    finally:
        conn.close()


def add_downtime(data, user):
    line_id = _id(data.get("line_id"))
    reason = (data.get("reason") or "").strip()
    minutes = _f(data.get("minutes"))
    work_date = _iso(data.get("work_date"))
    if not work_date:
        return False, "mes.msg.bad_date"
    if _too_far_ahead(work_date):
        return False, "mes.msg.future_date"
    if reason not in DOWNTIME_REASONS:
        return False, "mes.msg.bad_reason"
    if minutes <= 0:
        return False, "mes.msg.bad_minutes"
    if minutes > MAX_DOWNTIME_MIN:          # a loss cannot outlast the day it happened in
        return False, "mes.msg.too_big"
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM production_lines WHERE id=?", (line_id,)).fetchone():
            return False, "mes.msg.no_line"
        conn.execute("INSERT INTO mes_downtime (line_id,work_date,reason,minutes,"
                     "created_by,created_at) VALUES (?,?,?,?,?,?)",
                     (line_id, work_date, reason, minutes,
                      (user or {}).get("username"), _now()))
        conn.commit()
        return True, "mes.msg.saved"
    finally:
        conn.close()


def create_bundle(data, user):
    qty = _i(data.get("qty"))
    if qty <= 0:
        return False, "mes.msg.bad_qty"
    if qty > MAX_QTY:
        return False, "mes.msg.too_big"
    origin = (data.get("origin_section") or SECTIONS[0]).strip()
    if origin not in SECTIONS:
        return False, "mes.msg.bad_section"
    conn = get_db()
    try:
        order_id = _id(data.get("order_id")) or None
        if _order_missing(conn, order_id):
            return False, "mes.msg.no_order"
        cur = conn.execute(
            "INSERT INTO mes_bundles (order_id,size,color,qty,origin_section,section,status,"
            "created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (order_id, (data.get("size") or "").strip() or None,
             (data.get("color") or "").strip() or None, qty, origin, origin, "created",
             (user or {}).get("username"), _now()))
        bid = cur.lastrowid
        # Number the bundle from its own row id — unique and collision-free. A
        # COUNT(*)+1 scheme repeats numbers after any delete and breaks the UNIQUE.
        # The number is never taken from the caller: a posted duplicate would hit
        # the UNIQUE index and 500.
        conn.execute("UPDATE mes_bundles SET bundle_no=? WHERE id=?", ("BDL-%06d" % bid, bid))
        conn.commit()
        return True, bid
    finally:
        conn.close()


def _section_qty(conn, bundle, section):
    """Pieces physically standing in `section` for this bundle.

    IN  = the cut quantity when this is the bundle's origin section, plus every
          scan into it. OUT = every scan out of it.
    This balance is the ONLY number allowed to authorise a move — reading
    bundle.qty directly is what lets a bundle pass more pieces than were cut.
    """
    # _i(): a legacy row with a NULL qty must refuse the scan, not crash the gun.
    inflow = _i(bundle["qty"]) if section == bundle["origin_section"] else 0
    r = conn.execute("SELECT COALESCE(SUM(qty),0) AS q FROM mes_bundle_moves "
                     "WHERE bundle_id=? AND to_section=?", (bundle["id"], section)).fetchone()
    inflow += _i(r["q"])
    r = conn.execute("SELECT COALESCE(SUM(qty),0) AS q FROM mes_bundle_moves "
                     "WHERE bundle_id=? AND from_section=?", (bundle["id"], section)).fetchone()
    return inflow - _i(r["q"])


def _ledger_status(conn, bundle):
    """The status the SCAN LEDGER implies: completed once the whole quantity has
    reached the last section, in_progress after any scan, created before that.
    Progress is derived, never asserted — that is what keeps WIP honest."""
    qty = _i(bundle["qty"])
    if qty > 0 and _section_qty(conn, bundle, SECTIONS[-1]) >= qty:
        return "completed"
    moved = conn.execute("SELECT COUNT(*) AS c FROM mes_bundle_moves WHERE bundle_id=?",
                         (bundle["id"],)).fetchone()["c"]
    return "in_progress" if moved else "created"


def move_bundle(bundle_id, data, user):
    """Scan pieces from one section to the next.

    INVARIANT: a bundle can never pass more pieces than are standing in the section
    it leaves. The origin section starts with exactly the cut quantity, so the sum
    of everything that ever leaves it is capped at bundle.qty — no phantom output,
    and the sewing wage claim can be reconciled against the cut ticket.
    """
    qty = _i(data.get("qty"))
    to = (data.get("to_section") or "").strip()
    bundle_id = _id(bundle_id)
    conn = get_db()
    try:
        b = conn.execute("SELECT * FROM mes_bundles WHERE id=?", (bundle_id,)).fetchone()
        if not b:
            return False, "mes.msg.no_bundle"
        b = dict(b)
        if b["status"] == "rejected":
            return False, "mes.msg.bundle_rejected"
        frm = (data.get("from_section") or b["section"] or b["origin_section"]).strip()
        if frm not in SECTIONS or to not in SECTIONS:
            return False, "mes.msg.bad_section"
        if frm == to:
            return False, "mes.msg.same_section"
        if qty <= 0:
            return False, "mes.msg.bad_qty"
        if qty > _section_qty(conn, b, frm):
            return False, "mes.msg.exceeds_bundle"
        conn.execute("INSERT INTO mes_bundle_moves (bundle_id,from_section,to_section,qty,operator,"
                     "moved_at) VALUES (?,?,?,?,?,?)",
                     (bundle_id, frm, to, qty,
                      (data.get("operator") or "").strip() or (user or {}).get("username"), _now()))
        conn.execute("UPDATE mes_bundles SET section=?, status=?, updated_at=? WHERE id=?",
                     (to, _ledger_status(conn, b), _now(), bundle_id))
        conn.commit()
        return True, "mes.msg.moved"
    finally:
        conn.close()


def set_bundle_status(bundle_id, status, user):
    from .constants import BUNDLE_STATUS
    if status not in BUNDLE_STATUS:
        return False, "mes.msg.bad_status"
    bundle_id = _id(bundle_id)
    conn = get_db()
    try:
        b = conn.execute("SELECT * FROM mes_bundles WHERE id=?", (bundle_id,)).fetchone()
        if not b:
            return False, "mes.msg.no_bundle"
        b = dict(b)
        # Scrapping is the ONLY status a human decides; progress is earned in the
        # ledger. 'completed' by hand would invent finished pieces, and
        # 'created'/'in_progress' by hand would erase a bundle that really has been
        # scanned to dispatch. Un-scrapping restores the status the scans imply.
        if status != "rejected":
            if b["status"] != "rejected":
                return False, "mes.msg.status_ledger"
            status = _ledger_status(conn, b)
        conn.execute("UPDATE mes_bundles SET status=?, updated_at=? WHERE id=?",
                     (status, _now(), bundle_id))
        conn.commit()
        return True, "mes.msg.saved"
    finally:
        conn.close()


# ==========================================================================
# Exports — the platform-wide (headers, rows) contract, served as CSV or JSON
# ==========================================================================
# Caps are generous on purpose: nobody may open "the hourly register" and get a
# silently truncated month. A busy factory books ~8 lines x 8 hours x 30 days =
# ~2k hourly rows and a few thousand scans a month, so 20k rows is years of
# production and the day cap is a quarter of daily roll-ups.
_EX_ROWS = 20000
_EX_DAYS = 90


def export_dataset(key):
    """key -> (headers, rows), or (None, None) for an unknown key.

    Keys: hourly | downtime | oee-by-line | bundles | bundle-moves | order-recon.
    Percentages and minutes carry the same rounding the screens show, so an
    exported figure can be pasted next to a screenshot and still agree.
    """
    # These three are exactly what a service already returns, and each opens its
    # own connection — so no conn here, or a PostgreSQL pool slot is held twice.
    if key == "bundles":
        return (["Bundle No", "Order No", "Buyer", "Size", "Colour", "Pieces",
                 "Origin Section", "At Section", "Status", "Created By", "Created At"],
                [[b["bundle_no"] or "", b["order_no"] or b["order_id"] or "", b["buyer"] or "",
                  b["size"] or "", b["color"] or "", _i(b["qty"]), b["origin_section"] or "",
                  b["section"] or "", b["status"] or "", b["created_by"] or "",
                  b["created_at"] or ""] for b in list_bundles(limit=_EX_ROWS)])

    if key == "bundle-moves":
        return (["Moved At", "Bundle No", "From Section", "To Section", "Pieces", "Operator"],
                [[m["moved_at"] or "", m["bundle_no"] or "", m["from_section"] or "",
                  m["to_section"] or "", _i(m["qty"]), m["operator"] or ""]
                 for m in recent_moves(limit=_EX_ROWS)])

    if key == "order-recon":
        return (["Order No", "Buyer", "Order ID", "Cut Pcs", "Sewn Pcs", "Balance Pcs"],
                [[r["order_no"] or "", r["buyer"] or "", r["order_id"] or "",
                  _i(r["cut"]), _i(r["sewn"]), _i(r["balance"])]
                 for r in order_reconciliation()])

    conn = get_db()
    try:
        if key == "hourly":
            rows = [dict(r) for r in conn.execute(
                "SELECT h.work_date, h.line_id, l.name AS line_name, h.order_id, h.hour_slot, "
                "h.target_qty, h.actual_qty, h.reject_qty, h.operators, h.smv, h.notes "
                "FROM mes_hourly h LEFT JOIN production_lines l ON l.id = h.line_id "
                "ORDER BY h.work_date DESC, l.name, h.hour_slot LIMIT ?", (_EX_ROWS,)).fetchall()]
            _decorate_orders(conn, rows)
            return (["Date", "Line", "Order No", "Hour", "Target Pcs", "Actual Pcs",
                     "Reject Pcs", "Good Pcs", "Achievement %", "Status", "Operators",
                     "SMV", "Notes"],
                    [[r["work_date"], r["line_name"] or "Line %s" % r["line_id"],
                      r["order_no"] or r["order_id"] or "", r["hour_slot"],
                      _i(r["target_qty"]), _i(r["actual_qty"]), _i(r["reject_qty"]),
                      _i(r["actual_qty"]) - _i(r["reject_qty"]),
                      achievement(r["actual_qty"], r["target_qty"]),
                      rag_for(r["actual_qty"], r["target_qty"]),
                      _i(r["operators"]), round(_f(r["smv"]), 2), r["notes"] or ""]
                     for r in rows])

        if key == "downtime":
            rows = conn.execute(
                "SELECT d.work_date, d.line_id, l.name AS line_name, d.reason, d.minutes, "
                "d.created_by, d.created_at FROM mes_downtime d "
                "LEFT JOIN production_lines l ON l.id = d.line_id "
                "ORDER BY d.work_date DESC, l.name, d.minutes DESC LIMIT ?",
                (_EX_ROWS,)).fetchall()
            return (["Date", "Line", "Reason", "Lost Minutes", "Recorded By", "Recorded At"],
                    [[r["work_date"], r["line_name"] or "Line %s" % r["line_id"],
                      r["reason"] or "", round(_f(r["minutes"]), 1),
                      r["created_by"] or "", r["created_at"] or ""] for r in rows])

        if key == "oee-by-line":
            # Reuses the dashboard's own roll-up per day rather than re-deriving OEE in
            # SQL: two implementations of Availability would eventually disagree, and the
            # screen is the one the factory argues about. Downtime-only days are included
            # (the UNION), because a line down all day has no hourly row.
            days = [r["d"] for r in conn.execute(
                "SELECT DISTINCT work_date AS d FROM mes_hourly "
                "UNION SELECT DISTINCT work_date AS d FROM mes_downtime "
                "ORDER BY d DESC LIMIT ?", (_EX_DAYS,)).fetchall()]
            out = []
            for day in days:
                for a in _rollup(conn, day)[0]:
                    out.append([day, a["line_name"], a["hours"], a["target"], a["actual"],
                                a["reject"], a["achievement"], a["rag"], a["red_hours"],
                                a["efficiency"], a["availability"], a["performance"],
                                a["quality"], a["oee"], a["downtime_min"], a["planned_min"]])
            return (["Date", "Line", "Hours Recorded", "Target Pcs", "Actual Pcs", "Reject Pcs",
                     "Achievement %", "Status", "Red Hours", "Efficiency %", "Availability %",
                     "Performance %", "Quality %", "OEE %", "Downtime Min", "Planned Min"], out)
    finally:
        conn.close()
    return (None, None)


# ==========================================================================
# Alert sweep — RED hours and bleeding lines reach the bell. Never raises.
# ==========================================================================
def alert_sweep(work_date=None):
    try:
        conn = get_db()
    except Exception:
        return
    try:
        wd = (str(work_date)[:10] if work_date else _today())
        for r in conn.execute(
                "SELECT h.id, h.line_id, h.hour_slot, h.target_qty, h.actual_qty, l.name AS line_name "
                "FROM mes_hourly h LEFT JOIN production_lines l ON l.id=h.line_id "
                "WHERE h.work_date=? AND h.target_qty>0", (wd,)).fetchall():
            # rag_for(), not rag(achievement(...)): achievement rounds to 1 dp for
            # display, so 8495/10000 = 84.95% became 85.0 and read "amber" here while
            # the board painted the hour RED. The bell must use the board's own test.
            if rag_for(r["actual_qty"], r["target_qty"]) != "red":
                continue
            pct = achievement(r["actual_qty"], r["target_qty"])
            _bell_once(conn, "mes:red:%s" % r["id"], "warning",
                       "Hour missed: %s" % (r["line_name"] or "Line %s" % r["line_id"]),
                       "%s achieved %.1f%% (%s of %s pcs) in %s." %
                       (r["line_name"] or "Line", pct, r["actual_qty"], r["target_qty"], r["hour_slot"]),
                       "/mes/line/%s?date=%s" % (r["line_id"], wd))
        for r in conn.execute(
                "SELECT d.line_id, SUM(d.minutes) AS m, l.name AS line_name FROM mes_downtime d "
                "LEFT JOIN production_lines l ON l.id=d.line_id WHERE d.work_date=? "
                "GROUP BY d.line_id, l.name", (wd,)).fetchall():
            if _f(r["m"]) < DOWNTIME_ALERT_MIN:
                continue
            _bell_once(conn, "mes:down:%s:%s" % (r["line_id"], wd), "critical",
                       "Line down: %s" % (r["line_name"] or "Line %s" % r["line_id"]),
                       "%.0f lost minutes recorded on %s — above the %.0f-minute threshold." %
                       (_f(r["m"]), wd, DOWNTIME_ALERT_MIN),
                       "/mes/line/%s?date=%s" % (r["line_id"], wd))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
