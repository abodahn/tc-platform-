"""
Finite-capacity planning services.

The three formulas this module exists for (all guarded against divide-by-zero):

  daily_capacity_minutes = operators x working_minutes x efficiency_pct / 100
  required_minutes       = qty x SMV
  required_days          = ceil(required_minutes / daily_capacity_minutes)

An allocation is spread greedily from its start date: each calendar day consumes
up to the line's daily capacity until the required minutes are exhausted. Two
allocations overlapping on one line therefore push that day past 100% — which is
exactly the overload the board must show.
"""
import math
from datetime import date, datetime, timedelta

from app.db import get_db
from app.mes.constants import SLOT_MINUTES
from app.services.smv import resolve, smv_for, source_of, sources_for_orders
from .constants import (ACTUALS_DAYS, AT_RISK_DAYS, BOARD_DAYS, MAX_PLAN_DAYS,
                        OVERLOAD_PCT, VARIANCE_DAYS)


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _d(s):
    """Parse a YYYY-MM-DD (possibly with time) value to date, or None."""
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date() if s else None
    except (ValueError, TypeError):
        return None


def _f(v, default=0.0):
    """Coerce a form value to float; blank/None/garbage -> default, never raises.
    'inf' / '1e400' / 'nan' are garbage too: float() accepts them happily and the
    infinity then reaches int() (OverflowError -> HTTP 500) or poisons every
    capacity number downstream. Only a finite number is a quantity."""
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError, AttributeError):
        return default
    return f if math.isfinite(f) else default


def _i(v, default=0):
    return int(_f(v, default))


def _bell(conn, severity, title, message, link="/planning"):
    conn.execute("INSERT INTO notifications (severity,module,title,message,link,created_at) "
                 "VALUES (?,?,?,?,?,?)", (severity, "planning", title, message, link, _now()))


# --- pure capacity maths --------------------------------------------------
def daily_capacity_minutes(line):
    """operators x working_minutes x efficiency_pct / 100 — the minutes a line can
    actually deliver in a day. Zero operators / zero minutes / zero efficiency all
    legitimately give 0, which every caller must treat as 'cannot plan here'."""
    ops = _f((line or {}).get("operators"))
    mins = _f((line or {}).get("working_minutes"))
    eff = _f((line or {}).get("efficiency_pct"))
    if ops <= 0 or mins <= 0 or eff <= 0:
        return 0.0
    cap = ops * mins * eff / 100.0
    # Three finite numbers can still multiply to infinity (1e308 operators). An
    # infinite capacity is not a fast line, it is a typo: divisions by it collapse
    # to a zero-day plan that finishes before it starts.
    return round(cap, 2) if math.isfinite(cap) else 0.0


def required_minutes(qty, smv):
    q, s = _f(qty), _f(smv)
    return round(q * s, 2) if q > 0 and s > 0 else 0.0


def required_days(minutes, cap):
    """ceil(minutes / capacity). None when the line has no capacity to divide by,
    and None for an infinite requirement (qty x SMV can overflow to inf even from
    two finite numbers) — int(inf) raises, and a plan we cannot count is not a plan.
    INVARIANT: any work at all takes at least one day."""
    m, c = _f(minutes, -1.0), _f(cap, -1.0)
    if c <= 0 or m <= 0:
        return None
    return max(1, int(math.ceil(m / c)))


def spread_minutes(start, minutes, cap):
    """Day-by-day consumption of an allocation: [(date, minutes_that_day), ...].
    INVARIANT: the per-day minutes sum back to `minutes` (the last day takes the
    remainder), so nothing is silently lost or double-counted on the board.
    The day count is required_days() exactly — derived, never accumulated, so
    float drift can never add a phantom day."""
    s, c = _d(start), _f(cap)
    m = _f(minutes)
    n = required_days(m, c)
    if s is None or n is None:
        return []
    try:
        if n > MAX_PLAN_DAYS:
            # Runaway guard: a mis-typed capacity (1 minute/day) must not build a
            # million rows. The board never draws more than 60 days anyway, and
            # plan_end_date REFUSES such an allocation rather than reporting the
            # truncated last day as a finish date.
            return [(s + timedelta(days=i), round(c, 2)) for i in range(MAX_PLAN_DAYS)]
        return [(s + timedelta(days=i), round(c if i < n - 1 else m - c * (n - 1), 2))
                for i in range(n)]
    except OverflowError:
        # A start date near date.max (9999-12-31) runs the calendar out. Unplannable,
        # not a crash.
        return []


def plan_end_date(start, minutes, cap):
    """Projected completion date as a YYYY-MM-DD string, or None if unplannable.
    A plan longer than MAX_PLAN_DAYS is unplannable, NOT a finish date at the
    guard: reporting day 365 for work that needs 555 days is a false promise."""
    s, n = _d(start), required_days(minutes, cap)
    if s is None or n is None or n > MAX_PLAN_DAYS:
        return None
    try:
        return str(s + timedelta(days=n - 1))
    except OverflowError:
        return None          # start date near date.max: the calendar runs out


def balance_metrics(ops):
    """Line balancing over an operation bulletin.
      bottleneck_time      = max(operation SMV / operators assigned)   [minutes/piece]
      theoretical_output/h = 60 / bottleneck_time
      line_efficiency %    = sum(manned SMV) / (total_operators x bottleneck_time) x 100
    An operation with 0 operators cannot be a bottleneck (it is not manned), so it
    contributes SMV but no cycle time — otherwise one unstaffed row divides by zero.
    INVARIANT: line_efficiency is a real percentage, 0..100. Only MANNED work can
    appear in the numerator: an unstaffed row adds nothing to the denominator, so
    counting its SMV would invent efficiency (5 SMV on 0 operators once read 600%)."""
    rows = []
    total_smv = 0.0      # every row, for display
    manned_smv = 0.0     # only rows with operators — the efficiency numerator
    total_ops = 0
    unmanned = 0
    bottleneck = 0.0
    bname = None
    pitches = []
    for o in ops or []:
        smv = max(_f(o.get("smv")), 0.0)   # a negative SMV is meaningless, never a credit
        n = _i(o.get("operators"))
        n = n if n > 0 else 0
        # full precision here, rounded only for display: a rounded-down pitch shrinks
        # the denominator and would let efficiency creep over 100%
        pitch = smv / n if n > 0 and smv > 0 else 0.0
        total_smv += smv
        total_ops += n
        if n > 0:
            manned_smv += smv
        elif smv > 0:
            unmanned += 1
        if pitch > bottleneck:
            bottleneck, bname = pitch, o.get("name")
        pitches.append(pitch)
        rows.append({**o, "pitch": round(pitch, 4)})
    out_hr = round(60.0 / bottleneck, 1) if bottleneck > 0 else 0.0
    denom = total_ops * bottleneck
    eff = round(manned_smv / denom * 100.0, 1) if denom > 0 else 0.0
    for r, p in zip(rows, pitches):
        # a row at the bottleneck pitch is the constraint; everything below it waits
        r["is_bottleneck"] = bottleneck > 0 and p >= bottleneck
    return {"rows": rows, "total_smv": round(total_smv, 2), "total_operators": total_ops,
            "bottleneck_time": round(bottleneck, 4), "bottleneck_op": bname,
            "output_per_hour": out_hr, "line_efficiency": eff, "unmanned_ops": unmanned}


# --- what the floor actually did: the MES feedback loop -------------------
def measured(rows):
    """Roll mes_hourly rows up into what a line or order ACTUALLY delivered. Pure.

        pieces       = SUM(actual_qty)              every hour, manned or not
        good         = pieces - SUM(reject_qty)     what can actually be SHIPPED
        earned_min   = SUM(actual_qty x smv)        MANNED hours only
        operator_min = SUM(operators x 60)          MANNED hours only
        efficiency % = earned_min / operator_min x 100

    INVARIANT, the same one balance_metrics keeps: an hour with NO operators
    earns nothing. It contributes to neither side of the ratio — counting its
    pieces against a zero denominator is how 5 SMV on 0 operators once read 600%.

    `efficiency` is None, never 0.0, when there is nothing to measure. "No
    actuals yet" and "the line ran at 0%" are different facts and a planner must
    never be shown the second when the first is true.

    Planning multiplies theoretical minutes BY this percentage, so the number it
    hands back is capped at 100: capacity above theoretical does not exist and
    planning it would promise a buyer minutes the factory does not have.
    `efficiency_raw` keeps the uncapped figure, because a line genuinely running
    at 108% of standard is information, not an error.
    """
    pieces = rejects = hours = manned = 0
    earned = op_min = 0.0
    days = set()
    for r in rows or []:
        act = _i(r.get("actual_qty"))
        ops = _i(r.get("operators"))
        smv = max(_f(r.get("smv")), 0.0)
        hours += 1
        pieces += act
        rejects += _i(r.get("reject_qty"))
        if r.get("work_date"):
            days.add(str(r["work_date"])[:10])
        if ops > 0:
            manned += 1
            earned += act * smv
            op_min += ops * SLOT_MINUTES
    raw = round(earned / op_min * 100.0, 1) if op_min > 0 else None
    return {"has_data": hours > 0, "hours": hours, "manned_hours": manned,
            "pieces": pieces, "rejects": rejects,
            # mes_hourly.reject_qty is a SUBSET of actual_qty ("good = actual -
            # reject", mes/schema.py) and the MES's own OEE already nets it off.
            # A plan owes SHIPPABLE garments, so schedule progress is measured on
            # good pieces — crediting a rejected garment against the ship date
            # reports an order on plan while the buyer is still short.
            "good": max(pieces - rejects, 0),
            "earned_min": round(earned, 1), "operator_min": round(op_min, 1),
            "days": len(days),
            "efficiency_raw": raw,
            "efficiency": None if raw is None else round(min(100.0, max(0.0, raw)), 1)}


def _mes_rows(conn, order_id=None, line_id=None, since=None, order_ids=None):
    """Raw hourly rows from the MES. Degrades to [] when the MES module has not
    created its tables yet (rollback is mandatory: PostgreSQL aborts the whole
    transaction on a failed statement)."""
    q = ("SELECT work_date, actual_qty, reject_qty, operators, smv, order_id, line_id "
         "FROM mes_hourly WHERE 1=1")
    args = []
    if order_id is not None:
        q += " AND order_id=?"; args.append(order_id)
    if order_ids is not None:
        # An empty list must match NOTHING, never fall through to the whole table.
        ids = [_i(i) for i in order_ids if i]
        if not ids:
            return []
        q += " AND order_id IN (%s)" % ",".join("?" * len(ids)); args.extend(ids)
    if line_id is not None:
        q += " AND line_id=?"; args.append(line_id)
    if since:
        q += " AND work_date>=?"; args.append(str(since))
    try:
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _since(days):
    return str(date.today() - timedelta(days=max(1, _i(days, ACTUALS_DAYS)) - 1))


def _measured_by_line(conn, days=ACTUALS_DAYS):
    """{production_lines.id: measured(...)} over the window — ONE query for the
    whole board, never one per line."""
    by_line = {}
    for r in _mes_rows(conn, since=_since(days)):
        by_line.setdefault(r["line_id"], []).append(r)
    return {lid: measured(rows) for lid, rows in by_line.items()}


def order_actuals(order_id):
    """Everything the floor has ever booked against one order (not windowed: an
    order runs for weeks and its progress is cumulative)."""
    conn = get_db()
    try:
        return measured(_mes_rows(conn, order_id=_i(order_id)))
    finally:
        conn.close()


def plan_vs_actual(order, allocs, act, today=None):
    """Planned vs produced for one order — pure, no DB.

    expected_to_date is what the plan owed by the END OF YESTERDAY: only COMPLETE
    days count. A shift still running is not a shortfall, and claiming it is would
    paint every order behind at 9am. Each allocation contributes
    (its elapsed minutes / its total minutes) x its quantity, so a split order and
    a part-loaded order both come out right.

    produced_qty is GOOD pieces (actual - reject), because that is what the plan
    owes: a rejected garment consumed the line's minutes but cannot be shipped.

    Returns status 'no_actuals' when the floor has booked nothing at all — never
    '0 produced, 100% behind' off no data.
    """
    today = today or date.today()
    planned_qty = round(sum(_f(a.get("qty")) for a in allocs or []), 2)
    expected = 0.0
    per_day = 0.0
    for a in allocs or []:
        cap = daily_capacity_minutes(a)
        mins = required_minutes(a.get("qty"), a.get("smv"))
        spread = spread_minutes(a.get("start_date"), mins, cap)
        if not spread or mins <= 0:
            continue                      # zero-capacity / unplannable: owes nothing yet
        qty = _f(a.get("qty"))
        elapsed = sum(m for d, m in spread if d < today)
        expected += min(elapsed / mins, 1.0) * qty
        per_day += qty / len(spread)      # planned pieces per day on this allocation
    expected = round(expected, 1)
    out = {"planned_qty": planned_qty, "expected_qty": expected,
           "produced_qty": (act or {}).get("good", 0),
           "pieces_per_day": round(per_day, 2),
           "efficiency": (act or {}).get("efficiency"),
           "variance_qty": None, "variance_days": None, "status": "no_actuals"}
    if not (act or {}).get("has_data"):
        return out                        # nothing measured — say so, do not compute 0%
    var_q = round(out["produced_qty"] - expected, 1)
    if per_day > 0:
        var_d = round(var_q / per_day, 1)
        status = ("ahead" if var_d >= VARIANCE_DAYS else
                  "behind" if var_d <= -VARIANCE_DAYS else "on_plan")
    else:
        # No daily rate to convert pieces into days (nothing plannable is loaded —
        # zero-capacity line, or the floor is running an order nobody planned).
        # Report the piece variance and keep the days column honestly empty.
        var_d = None
        status = "ahead" if var_q > 0 else ("behind" if var_q < 0 else "on_plan")
    out.update({"variance_qty": var_q, "variance_days": var_d, "status": status})
    return out


# --- lines ----------------------------------------------------------------
def list_lines(active_only=False, days=ACTUALS_DAYS):
    """Capacity profiles + what the MES says each line ACTUALLY ran over the last
    `days`. The measured figure is attached, never applied: efficiency_pct is the
    planner's own input and only the planner may change it (lines.html offers it
    as a one-click suggestion)."""
    conn = get_db()
    try:
        q = "SELECT * FROM pln_lines"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY section, code, id"
        rows = [dict(r) for r in conn.execute(q).fetchall()]
        act = _measured_by_line(conn, days)
        for r in rows:
            r["capacity"] = daily_capacity_minutes(r)
            # pln_lines is a capacity PROFILE; the MES books against the platform's
            # production_lines. Unlinked profile -> no actuals, not zero actuals.
            r["actual"] = act.get(r.get("line_id")) or measured([])
            r["measured_efficiency"] = r["actual"]["efficiency"]
            r["efficiency_gap"] = (None if r["measured_efficiency"] is None else
                                   round(r["measured_efficiency"] - _f(r.get("efficiency_pct")), 1))
            r["capacity_measured"] = (daily_capacity_minutes(
                {**r, "efficiency_pct": r["measured_efficiency"]})
                if r["measured_efficiency"] is not None else None)
        return rows
    finally:
        conn.close()


def create_line(data, user):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO pln_lines (code,name,section,operators,working_minutes,efficiency_pct,"
            "active,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (data.get("code") or None, (data.get("name") or "Line").strip(), data.get("section"),
             _i(data.get("operators")), _i(data.get("working_minutes")),
             _f(data.get("efficiency_pct"), 100.0), 1, data.get("notes"), _now()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_line(pline_id, data, user):
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM pln_lines WHERE id=?", (pline_id,)).fetchone():
            return False        # never flash "capacity updated" for a line that is not there
        fields = {"code": str, "name": str, "section": str, "notes": str,
                  "operators": _i, "working_minutes": _i, "efficiency_pct": _f,
                  "active": _i}
        sets, args = [], []
        for f, cast in fields.items():
            if f not in data:
                continue
            if f == "name" and not (data.get("name") or "").strip():
                continue        # name is NOT NULL — a blank field must not null it
            sets.append(f"{f}=?")
            args.append(cast(data.get(f)) if cast is not str else (data.get(f) or None))
        if not sets:
            return False
        sets.append("updated_at=?"); args.append(_now())
        args.append(pline_id)
        conn.execute("UPDATE pln_lines SET " + ", ".join(sets) + " WHERE id=?", args)
        # Capacity changed -> reflow the projected finish of every open allocation on
        # this line. The board spreads load from the LIVE capacity while feasibility
        # reads the STORED end_date, so without this the two disagree the moment
        # anyone edits operators, minutes or efficiency.
        if {"operators", "working_minutes", "efficiency_pct"} & set(data.keys()):
            ln = conn.execute("SELECT * FROM pln_lines WHERE id=?", (pline_id,)).fetchone()
            cap = daily_capacity_minutes(dict(ln)) if ln else 0.0
            for a in conn.execute("SELECT id,qty,smv,start_date FROM pln_allocations "
                                  "WHERE pline_id=? AND status!='cancelled'", (pline_id,)).fetchall():
                conn.execute(
                    "UPDATE pln_allocations SET end_date=? WHERE id=?",
                    (plan_end_date(a["start_date"], required_minutes(a["qty"], a["smv"]), cap),
                     a["id"]))
        conn.commit()
        return True
    finally:
        conn.close()


# --- orders + SMV ---------------------------------------------------------
def _orders(conn, order_id=None):
    """Orders with their planning SMV attached. Degrades to [] if ord_orders is absent."""
    try:
        q = ("SELECT o.id,o.order_no,o.buyer,o.style_ref,o.style_name,o.qty,o.ship_date,o.status,"
             "s.smv FROM ord_orders o LEFT JOIN pln_order_smv s ON s.order_id=o.id")
        args = []
        # `is not None`, never truthiness: a garbage/blank order id coerces to 0 and
        # a falsy test would silently drop the filter and return the WHOLE order book,
        # so the caller would plan (or display) an arbitrary order instead of failing.
        if order_id is not None:
            q += " WHERE o.id=?"; args.append(order_id)
        else:
            # COALESCE, not a bare NOT IN: `NULL NOT IN (...)` is NULL, not TRUE, so an
            # order whose status was never set would silently vanish from the whole
            # planning module instead of showing up as work nobody has planned.
            q += " WHERE COALESCE(o.status,'') NOT IN ('closed','cancelled')"
        q += " ORDER BY o.ship_date ASC, o.id DESC"
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    except Exception:
        return []


def list_orders():
    conn = get_db()
    try:
        return _orders(conn)
    finally:
        conn.close()


def order_exists(order_id):
    """True when the id is a real order — the existence guard the write paths use."""
    conn = get_db()
    try:
        return bool(_orders(conn, _i(order_id)))
    finally:
        conn.close()


def set_smv(order_id, smv, notes=None):
    """Editable standard minute value per order. Rejects garbage and negatives —
    a bad SMV silently poisons every capacity number downstream."""
    v = _f(smv, -1.0)
    if v < 0:
        return False, "bad_smv"
    if not order_exists(order_id):
        return False, "order_not_found"
    conn = get_db()
    try:
        ex = conn.execute("SELECT id FROM pln_order_smv WHERE order_id=?", (order_id,)).fetchone()
        if ex:
            conn.execute("UPDATE pln_order_smv SET smv=?, notes=COALESCE(?,notes), updated_at=? "
                         "WHERE order_id=?", (v, notes, _now(), order_id))
        else:
            conn.execute("INSERT INTO pln_order_smv (order_id,smv,notes,updated_at) VALUES (?,?,?,?)",
                         (order_id, v, notes, _now()))
        conn.commit()
        return True, "ok"
    finally:
        conn.close()


def _smv_override(raw, order):
    """The SMV a commit/preview should use: blank -> the order's SMV, otherwise the
    typed value (<=0 for anything unparseable, so the caller refuses it)."""
    if raw is None or str(raw).strip() == "":
        return _f((order or {}).get("smv"))
    return _f(raw, -1.0)


# --- allocations ----------------------------------------------------------
def _alloc_rows(conn, pline_id=None, order_id=None):
    q = ("SELECT a.*, l.name AS line_name, l.code AS line_code, l.operators, l.working_minutes, "
         "l.efficiency_pct FROM pln_allocations a JOIN pln_lines l ON l.id=a.pline_id "
         "WHERE a.status!='cancelled'")
    args = []
    # same falsy trap as _orders: filter on `is not None`, or id 0 returns everything
    if pline_id is not None:
        q += " AND a.pline_id=?"; args.append(pline_id)
    if order_id is not None:
        q += " AND a.order_id=?"; args.append(order_id)
    return [dict(r) for r in conn.execute(q + " ORDER BY a.start_date, a.id", args).fetchall()]


def _load_by_day(conn, pline_id):
    """{'YYYY-MM-DD': loaded_minutes} for one line, from its persisted allocations.
    Spread in Python: a factory has tens of lines and hundreds of open allocations,
    so one pass beats a per-day SQL round trip."""
    out = {}
    for a in _alloc_rows(conn, pline_id=pline_id):
        cap = daily_capacity_minutes(a)
        for d, m in spread_minutes(a["start_date"], required_minutes(a["qty"], a["smv"]), cap):
            out[str(d)] = round(out.get(str(d), 0.0) + m, 2)
    return out


def list_allocations(order_id=None):
    conn = get_db()
    try:
        rows = _alloc_rows(conn, order_id=order_id)
        for a in rows:
            a["minutes"] = required_minutes(a["qty"], a["smv"])
            a["capacity"] = daily_capacity_minutes(a)
            a["days"] = required_days(a["minutes"], a["capacity"])
        return rows
    finally:
        conn.close()


def create_allocation(data, user):
    """Load part (or all) of an order's quantity onto a line. Snapshots the SMV,
    stores the projected completion, then alerts the bell if the order is now late
    or the line is overloaded. Returns (ok, reason_or_id)."""
    order_id = _i(data.get("order_id"))
    pline_id = _i(data.get("pline_id"))
    qty = _f(data.get("qty"))
    start = _d(data.get("start_date")) or date.today()
    if qty <= 0:
        return False, "bad_qty"
    conn = get_db()
    try:
        line = conn.execute("SELECT * FROM pln_lines WHERE id=?", (pline_id,)).fetchone()
        if not line:
            return False, "line_not_found"
        orders = _orders(conn, order_id)
        if not orders:
            return False, "order_not_found"
        o = orders[0]
        # A BLANK override means "use the order's SMV"; a typed-but-unparseable one
        # ('abc', '1e400') must be refused, not silently replaced by the order's SMV —
        # the plan would then be priced at a number the planner never typed.
        smv = _smv_override(data.get("smv"), o)
        if smv <= 0:
            return False, "no_smv"
        cap = daily_capacity_minutes(dict(line))
        if cap <= 0:
            return False, "no_capacity"
        mins = required_minutes(qty, smv)
        end = plan_end_date(start, mins, cap)
        if end is None:
            return False, "unplannable"
        # Double-submit guard: the same order, line, start date and quantity is a
        # resubmitted form, not a second batch — accepting it would book the line's
        # minutes twice. A genuine split uses a different quantity or start date.
        if conn.execute("SELECT id FROM pln_allocations WHERE order_id=? AND pline_id=? "
                        "AND start_date=? AND qty=? AND status!='cancelled'",
                        (order_id, pline_id, str(start), qty)).fetchone():
            return False, "duplicate"
        # The SMV stays a SNAPSHOT — a later edit must not re-price a committed
        # plan. All that is added is WHERE this frozen number came from, so the
        # plan is auditable against the sources it was made from.
        src = source_of(smv, smv_for(conn, order_id=order_id,
                                     style_ref=o.get("style_ref"))["sources"])
        cur = conn.execute(
            "INSERT INTO pln_allocations (order_id,pline_id,qty,smv,smv_source,start_date,"
            "end_date,status,notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, pline_id, qty, smv, src, str(start), end, "planned", data.get("notes"),
             (user or {}).get("username"), _now()))
        aid = cur.lastrowid
        conn.commit()
        _alert(conn, o, dict(line), start, end)
        conn.commit()
        return True, aid
    finally:
        conn.close()


def _alert(conn, order, line, start, end):
    """Bell alerts for the two things a planner must not miss: an allocation that
    lands after the ship date, and a day where the line is promised more minutes
    than it has. Best-effort — an alert failure must never lose the allocation."""
    try:
        ship = _d(order.get("ship_date"))
        fin = _d(end)
        if ship and fin and fin > ship:
            _bell(conn, "critical", f"Plan is late: {order.get('order_no')}",
                  f"{order.get('order_no')} ({order.get('buyer')}) is planned to finish {end}, "
                  f"{(fin - ship).days} day(s) after the {ship} ship date.",
                  f"/planning/orders/{order.get('id')}")
        cap = daily_capacity_minutes(line)
        if cap > 0:
            load = _load_by_day(conn, line["id"])
            over = [(d, m) for d, m in load.items()
                    if str(start) <= d <= str(end) and 100.0 * m / cap > OVERLOAD_PCT]
            if over:
                worst = max(over, key=lambda x: x[1])
                _bell(conn, "warning", f"Line overloaded: {line.get('name')}",
                      f"{line.get('name')} is loaded to {round(100.0 * worst[1] / cap)}% on "
                      f"{worst[0]} ({len(over)} overloaded day(s)) — move work or add capacity.")
    except Exception:
        pass


def delete_allocation(alloc_id):
    """Un-plan: mark cancelled rather than deleting, so the plan history survives."""
    conn = get_db()
    try:
        conn.execute("UPDATE pln_allocations SET status='cancelled' WHERE id=?", (alloc_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# --- the load board -------------------------------------------------------
def board(days=BOARD_DAYS):
    """Lines x days grid: loaded vs available minutes, load % and overload flag."""
    days = max(1, min(_i(days, BOARD_DAYS) or BOARD_DAYS, 60))
    today = date.today()
    dates = [today + timedelta(days=i) for i in range(days)]
    conn = get_db()
    try:
        # A DEACTIVATED line that still carries committed work stays on the board:
        # dropping it would hide real minutes (and a real overload) while
        # feasibility keeps counting the very same allocation as planned.
        lines = [dict(r) for r in conn.execute(
            "SELECT * FROM pln_lines WHERE active=1 OR id IN (SELECT pline_id FROM "
            "pln_allocations WHERE status!='cancelled') ORDER BY section, code, id").fetchall()]
        grid, overloaded, idle_min, loaded_min, cap_min = [], 0, 0.0, 0.0, 0.0
        for ln in lines:
            cap = daily_capacity_minutes(ln)
            load = _load_by_day(conn, ln["id"])
            cells = []
            for d in dates:
                m = load.get(str(d), 0.0)
                pct = round(100.0 * m / cap, 1) if cap > 0 else 0.0
                # overload from the raw minutes, never from the rounded %: 100.04%
                # rounds to 100.0 and would hide a line that is genuinely over.
                cells.append({"date": str(d), "minutes": m, "pct": pct,
                              "over": cap > 0 and m > cap * OVERLOAD_PCT / 100.0,
                              "no_cap": cap <= 0})
            if not ln.get("active") and not any(c["minutes"] for c in cells):
                continue        # retired line with nothing left on it — drop the row
            for c in cells:
                if c["over"]:
                    overloaded += 1
                loaded_min += min(c["minutes"], cap) if cap > 0 else 0.0
                cap_min += cap
                idle_min += max(cap - c["minutes"], 0.0)
            grid.append({"line": ln, "capacity": cap, "cells": cells})
        util = round(100.0 * loaded_min / cap_min, 1) if cap_min > 0 else 0.0
        return {"dates": [str(d) for d in dates], "grid": grid, "days": days,
                "overloaded_cells": overloaded, "idle_minutes": round(idle_min),
                "utilisation": util}
    finally:
        conn.close()


# --- feasibility ----------------------------------------------------------
def _verdict(finish, ship, has_allocs):
    """(status, days_late) — the single place the on_time/at_risk/late line is drawn.
    days_late is positive when the plan finishes AFTER the ship date."""
    if not has_allocs:
        return "unplanned", None
    if finish is None:
        return "no_capacity", None          # allocated to a line with zero capacity
    if ship is None:
        return "no_ship_date", None
    dl = (finish - ship).days
    if dl > 0:
        return "late", dl
    if dl >= -AT_RISK_DAYS:
        return "at_risk", dl
    return "on_time", dl


def _order_feasibility(o, allocs):
    """Pure roll-up of one order's allocations (no DB access)."""
    planned_qty = round(sum(_f(a["qty"]) for a in allocs), 2)
    ends = [_d(a["end_date"]) for a in allocs if _d(a["end_date"])]
    finish = max(ends) if ends else None
    status, days_late = _verdict(finish, _d(o.get("ship_date")), bool(allocs))
    # unplanned_qty clamps at 0, so an over-plan (the same quantity booked twice on
    # two lines) would look exactly like a finished plan. Report the excess
    # explicitly — the line minutes are really consumed by it.
    return {"planned_qty": planned_qty,
            "unplanned_qty": round(max(_f(o.get("qty")) - planned_qty, 0.0), 2),
            "over_qty": round(max(planned_qty - _f(o.get("qty")), 0.0), 2),
            "finish": str(finish) if finish else None,
            "days_late": days_late, "status": status}


def feasibility(order_id):
    """Projected completion of an order's allocations vs its ship date."""
    conn = get_db()
    try:
        orders = _orders(conn, order_id)
        if not orders:
            return None
        o = orders[0]
        allocs = _alloc_rows(conn, order_id=order_id)
        for a in allocs:
            a["minutes"] = required_minutes(a["qty"], a["smv"])
            a["capacity"] = daily_capacity_minutes(a)
            a["days"] = required_days(a["minutes"], a["capacity"])
        # The canonical SMV and every stored one that disagrees with it. The
        # order's OWN planning SMV (o["smv"]) is what the plan is priced with and
        # is deliberately untouched here — this only says where it came from and
        # what else the factory believes.
        smv = smv_for(conn, order_id=order_id, style_ref=o.get("style_ref"))
        act = measured(_mes_rows(conn, order_id=order_id))
        return {"order": o, "allocations": allocs, "smv": smv, "actual": act,
                "vs": plan_vs_actual(o, allocs, act),
                "required_minutes": required_minutes(o.get("qty"), o.get("smv")),
                **_order_feasibility(o, allocs)}
    finally:
        conn.close()


def what_if(order_id, pline_id, start_date, qty, smv=None):
    """'What breaks if I move this?' — the resulting load, projected finish and
    feasibility of a PROPOSED allocation. Persists nothing."""
    conn = get_db()
    try:
        line = conn.execute("SELECT * FROM pln_lines WHERE id=?", (_i(pline_id),)).fetchone()
        orders = _orders(conn, _i(order_id))
        if not line:
            return {"ok": False, "reason": "line_not_found"}
        if not orders:
            return {"ok": False, "reason": "order_not_found"}
        line, o = dict(line), orders[0]
        q = _f(qty)
        v = _smv_override(smv, o)      # same rule as the commit, or the preview lies
        if q <= 0:
            return {"ok": False, "reason": "bad_qty"}
        if v <= 0:
            return {"ok": False, "reason": "no_smv"}
        cap = daily_capacity_minutes(line)
        if cap <= 0:
            return {"ok": False, "reason": "no_capacity", "line": line, "order": o}
        start = _d(start_date) or date.today()
        mins = required_minutes(q, v)
        # the preview must refuse exactly what create_allocation refuses, or a
        # planner previews a finish date the commit then rejects
        if plan_end_date(start, mins, cap) is None:
            return {"ok": False, "reason": "unplannable"}
        proposed = spread_minutes(start, mins, cap)
        existing = _load_by_day(conn, line["id"])
        rows, peak, over_days = [], 0.0, 0
        for d, m in proposed:
            tot = round(existing.get(str(d), 0.0) + m, 2)
            pct = round(100.0 * tot / cap, 1)
            peak = max(peak, pct)
            over = tot > cap * OVERLOAD_PCT / 100.0    # raw minutes, not the rounded %
            if over:
                over_days += 1
            rows.append({"date": str(d), "minutes": tot, "pct": pct, "over": over})
        finish = proposed[-1][0]
        status, days_late = _verdict(finish, _d(o.get("ship_date")), True)
        return {"ok": True, "order": o, "line": line, "qty": q, "smv": v, "capacity": cap,
                "minutes": mins, "days": len(proposed), "start": str(start),
                "finish": str(finish), "days_late": days_late, "status": status,
                "peak_pct": round(peak, 1), "overload_days": over_days, "rows": rows}
    finally:
        conn.close()


# --- line balancing -------------------------------------------------------
def balance(order_id):
    conn = get_db()
    try:
        orders = _orders(conn, order_id)
        if not orders:
            return None
        ops = [dict(r) for r in conn.execute(
            "SELECT * FROM pln_ops WHERE order_id=? ORDER BY seq, id", (order_id,)).fetchall()]
        return {"order": orders[0], **balance_metrics(ops)}
    finally:
        conn.close()


def add_op(order_id, data):
    name = (data.get("name") or "").strip()
    if not name:
        return False, "name_required"
    # a negative SMV would subtract work from the bulletin and skew the balance
    if _f(data.get("smv")) < 0:
        return False, "bad_smv"
    if not order_exists(order_id):
        return False, "order_not_found"
    conn = get_db()
    try:
        nxt = conn.execute("SELECT COALESCE(MAX(seq),0) AS c FROM pln_ops WHERE order_id=?",
                           (order_id,)).fetchone()["c"]
        conn.execute("INSERT INTO pln_ops (order_id,seq,name,smv,operators,machine,created_at) "
                     "VALUES (?,?,?,?,?,?,?)",
                     (order_id, _i(data.get("seq"), 0) or (int(nxt) + 1), name,
                      _f(data.get("smv")), max(_i(data.get("operators"), 1), 0),
                      data.get("machine"), _now()))
        conn.commit()
        return True, "ok"
    finally:
        conn.close()


def delete_op(op_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM pln_ops WHERE id=?", (op_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# --- dashboard ------------------------------------------------------------
def dashboard(days=BOARD_DAYS):
    b = board(days)
    conn = get_db()
    try:
        # one pass over all open allocations — never one query per order
        by_order = {}
        for a in _alloc_rows(conn):
            by_order.setdefault(a["order_id"], []).append(a)
        orders = [{**o, **_order_feasibility(o, by_order.get(o["id"], []))}
                  for o in _orders(conn)]
        # Two bulk passes, never one query per order: what the floor produced, and
        # every stored SMV that disagrees with the canonical one.
        ids = [o["id"] for o in orders]
        act_by_order = {}
        for r in _mes_rows(conn, order_ids=ids):
            act_by_order.setdefault(r["order_id"], []).append(r)
        srcs = sources_for_orders(conn, ids)
        for o in orders:
            act = measured(act_by_order.get(o["id"], []))
            o["actual"] = act
            o["vs"] = plan_vs_actual(o, by_order.get(o["id"], []), act)
            o["smv_resolved"] = resolve(srcs.get(o["id"], []))
    finally:
        conn.close()
    late = [o for o in orders if o["status"] == "late"]
    at_risk = [o for o in orders if o["status"] == "at_risk"]
    unplanned = [o for o in orders if o["status"] in ("unplanned", "no_capacity")]
    behind = [o for o in orders if o["vs"]["status"] == "behind"]
    conflicts = [o for o in orders if o["smv_resolved"]["conflict"]]
    return {"board": b, "orders": orders, "late": late, "at_risk": at_risk,
            "unplanned": unplanned, "behind": behind, "smv_conflicts": conflicts,
            "kpi": {"lines": len(b["grid"]), "utilisation": b["utilisation"],
                    "overloaded": b["overloaded_cells"],
                    "idle_hours": round(b["idle_minutes"] / 60.0),
                    "late": len(late), "at_risk": len(at_risk), "unplanned": len(unplanned),
                    "behind": len(behind), "smv_conflicts": len(conflicts)}}


# --- exports --------------------------------------------------------------
# Generous, because these are the sheets a planner rebuilds by hand today. Only
# the operation bulletin is capped: it is the one table that grows per order
# without a status to close it (~20 rows per style, so this is hundreds of styles).
_OPS_LIMIT = 20000


def _order_labels():
    """order_id -> row(order_no, buyer, ship_date) for labelling export rows.

    Not _orders(): an allocation or a bulletin can belong to a CLOSED order, which
    the planning screens deliberately hide — in an export that must still carry its
    order number rather than a bare id. Degrades to {} if ord_orders is absent."""
    conn = get_db()
    try:
        return {r["id"]: dict(r) for r in conn.execute(
            "SELECT id,order_no,buyer,ship_date FROM ord_orders").fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def export_dataset(key):
    """key -> (headers, rows), or (None, None) for an unknown key.

    Keys: line-capacity | order-feasibility | allocations | daily-load |
    operation-bulletin. Each one is built from the very function the matching page
    renders, so a download can never disagree with the screen it came from."""
    if key == "line-capacity":
        return (["Code", "Name", "Section", "Operators", "Minutes / Day",
                 "Efficiency %", "Capacity / Day (min)", "Status", "Notes"],
                [[r.get("code") or "", r.get("name") or "", r.get("section") or "",
                  _i(r.get("operators")), _i(r.get("working_minutes")),
                  round(_f(r.get("efficiency_pct")), 2), round(_f(r.get("capacity")), 2),
                  "active" if r.get("active") else "inactive", r.get("notes") or ""]
                 for r in list_lines()])

    if key == "order-feasibility":
        # dashboard() merges _order_feasibility over each order, so `status` here is
        # the FEASIBILITY verdict (on_time/at_risk/late/unplanned), not the order status.
        return (["Order No", "Buyer", "Style", "Order Qty", "SMV", "Planned Qty",
                 "Unplanned Qty", "Over-planned Qty", "Projected Finish", "Ship Date",
                 "Days Late", "Feasibility"],
                [[o.get("order_no") or "", o.get("buyer") or "",
                  o.get("style_name") or o.get("style_ref") or "",
                  round(_f(o.get("qty")), 3), round(_f(o.get("smv")), 3),
                  round(_f(o.get("planned_qty")), 3), round(_f(o.get("unplanned_qty")), 3),
                  round(_f(o.get("over_qty")), 3), o.get("finish") or "",
                  o.get("ship_date") or "",
                  o["days_late"] if o.get("days_late") is not None else "",
                  o.get("status") or ""]
                 for o in dashboard()["orders"]])

    if key == "allocations":
        # The whole open plan, uncapped on purpose: cancelled rows are already
        # excluded, so this is exactly the work currently promised to the lines.
        labels = _order_labels()
        rows = []
        for a in list_allocations():
            o = labels.get(a["order_id"]) or {}
            rows.append([o.get("order_no") or a["order_id"], o.get("buyer") or "",
                         a.get("line_code") or a.get("line_name") or "",
                         a.get("line_name") or "", round(_f(a.get("qty")), 3),
                         round(_f(a.get("smv")), 3), round(_f(a.get("minutes")), 2),
                         round(_f(a.get("capacity")), 2),
                         a["days"] if a.get("days") is not None else "",
                         a.get("start_date") or "", a.get("end_date") or "",
                         o.get("ship_date") or "", a.get("status") or "",
                         a.get("created_by") or ""])
        return (["Order No", "Buyer", "Line", "Line Name", "Qty", "SMV",
                 "Minutes", "Capacity / Day (min)", "Days", "Start Date",
                 "Projected Finish", "Ship Date", "Status", "Planned By"], rows)

    if key == "daily-load":
        # The load board, one row per line-day — the grid people screenshot. Same
        # horizon as the page (board() caps it), so the numbers tie back exactly.
        b = board()
        rows = []
        for g in b["grid"]:
            ln = g["line"]
            for c in g["cells"]:
                rows.append([ln.get("code") or "", ln.get("name") or "",
                             ln.get("section") or "", c["date"],
                             round(_f(g.get("capacity")), 2), round(_f(c.get("minutes")), 2),
                             "" if c["no_cap"] else c["pct"],
                             "yes" if c["over"] else ""])
        return (["Code", "Line", "Section", "Date", "Capacity / Day (min)",
                 "Loaded (min)", "Load %", "Overloaded"], rows)

    if key == "operation-bulletin":
        labels = _order_labels()
        conn = get_db()
        try:
            ops = [dict(r) for r in conn.execute(
                "SELECT * FROM pln_ops ORDER BY order_id, seq, id LIMIT ?",
                (_OPS_LIMIT,)).fetchall()]
        finally:
            conn.close()
        by_order = {}
        for o in ops:
            by_order.setdefault(o["order_id"], []).append(o)
        rows = []
        for oid, group in by_order.items():
            # balance_metrics, not a hand-rolled pitch: the bottleneck flag and the
            # balancing efficiency must be the same numbers /balance/<id> shows.
            m = balance_metrics(group)
            label = (labels.get(oid) or {}).get("order_no") or oid
            for r in m["rows"]:
                rows.append([label, _i(r.get("seq")), r.get("name") or "",
                             r.get("machine") or "", round(_f(r.get("smv")), 3),
                             _i(r.get("operators")), round(_f(r.get("pitch")), 4),
                             "yes" if r.get("is_bottleneck") else "",
                             m["line_efficiency"]])
        return (["Order No", "Seq", "Operation", "Machine", "SMV", "Operators",
                 "Min / Piece", "Bottleneck", "Line Efficiency %"], rows)

    return (None, None)
