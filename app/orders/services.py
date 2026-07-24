"""
Order + Time & Action services. The critical path is derived: each milestone's
planned_date = ship_date - offset_days, so moving the ship date reflows the whole
plan. A milestone is 'late' when its planned date has passed with no actual, and
'at_risk' when it's within AT_RISK_DAYS. tna_sweep() alerts late milestones on the bell.
"""
from datetime import date, datetime, timedelta

from app.db import get_db
from .constants import DEFAULT_TNA, AT_RISK_DAYS
from .schema import _gen_milestones


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _d(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date() if s else None
    except (ValueError, TypeError):
        return None


def _bell(conn, severity, title, message, link="/orders"):
    conn.execute("INSERT INTO notifications (severity,module,title,message,link,created_at) "
                 "VALUES (?,?,?,?,?,?)", (severity, "orders", title, message, link, _now()))


def ms_status(m, today=None):
    """View status of a milestone: done / late / at_risk / pending."""
    today = today or date.today()
    if m["status"] == "done" or m["actual_date"]:
        return "done"
    pd = _d(m["planned_date"])
    if pd is None:
        return "pending"
    if pd < today:
        return "late"
    if (pd - today).days <= AT_RISK_DAYS:
        return "at_risk"
    return "pending"


def order_health(milestones, today=None):
    today = today or date.today()
    sts = [ms_status(m, today) for m in milestones]
    if "late" in sts:
        return "late"
    if "at_risk" in sts:
        return "at_risk"
    return "on_track"


def days_to(d):
    dt = _d(d)
    return (dt - date.today()).days if dt else None


# --- reads ----------------------------------------------------------------
def list_orders(status=None):
    conn = get_db()
    try:
        q = "SELECT * FROM ord_orders WHERE 1=1"
        args = []
        if status:
            q += " AND status=?"; args.append(status)
        q += " ORDER BY ship_date ASC, id DESC"
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        # attach a health flag per order (one grouped query)
        ms = conn.execute("SELECT order_id,status,actual_date,planned_date FROM ord_milestones").fetchall()
        by_order = {}
        for m in ms:
            by_order.setdefault(m["order_id"], []).append(dict(m))
        for o in rows:
            o["health"] = order_health(by_order.get(o["id"], []))
            o["days_to_ship"] = days_to(o["ship_date"])
        return rows
    finally:
        conn.close()


def get_order(order_id):
    conn = get_db()
    try:
        o = conn.execute("SELECT * FROM ord_orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return None
        ms = [dict(m) for m in conn.execute(
            "SELECT * FROM ord_milestones WHERE order_id=? ORDER BY seq, planned_date", (order_id,)).fetchall()]
        today = date.today()
        for m in ms:
            m["view_status"] = ms_status(m, today)
        o = dict(o)
        o["health"] = order_health(ms, today)
        o["days_to_ship"] = days_to(o["ship_date"])
        done = sum(1 for m in ms if m["view_status"] == "done")
        o["progress"] = round(100 * done / len(ms)) if ms else 0
        return {"order": o, "milestones": ms}
    finally:
        conn.close()


# --- writes ---------------------------------------------------------------
def create_order(data, user):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO ord_orders (buyer,po_no,style_ref,style_name,qty,unit_price,currency,"
            "order_date,ship_date,status,department,line_no,notes,created_by,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (data.get("buyer"), data.get("po_no"), data.get("style_ref"), data.get("style_name"),
             float(data.get("qty") or 0), float(data.get("unit_price") or 0),
             data.get("currency") or "USD", data.get("order_date") or None,
             data.get("ship_date") or None, data.get("status") or "confirmed",
             data.get("department"), data.get("line_no"), data.get("notes"),
             (user or {}).get("username"), _now()))
        oid = cur.lastrowid
        conn.execute("UPDATE ord_orders SET order_no=? WHERE id=?", ("SO-%04d" % (1000 + oid), oid))
        _gen_milestones(conn, oid, data.get("ship_date"))   # build the critical path
        conn.commit()
        return oid
    finally:
        conn.close()


def update_order(order_id, data, user):
    conn = get_db()
    try:
        old = conn.execute("SELECT ship_date FROM ord_orders WHERE id=?", (order_id,)).fetchone()
        fields = ["buyer", "po_no", "style_ref", "style_name", "qty", "unit_price",
                  "currency", "order_date", "ship_date", "status", "department", "line_no", "notes"]
        sets, args = [], []
        for f in fields:
            if f in data:
                sets.append(f"{f}=?"); args.append(data.get(f) or None)
        if not sets:
            return False
        sets.append("updated_at=?"); args.append(_now())
        args.append(order_id)
        conn.execute("UPDATE ord_orders SET " + ", ".join(sets) + " WHERE id=?", args)
        # ship date moved -> reflow the whole critical path (pending milestones only)
        new_ship = data.get("ship_date")
        if "ship_date" in data and new_ship and (not old or old["ship_date"] != new_ship):
            sd = _d(new_ship)
            if sd:
                for m in conn.execute("SELECT id,offset_days FROM ord_milestones WHERE order_id=? "
                                      "AND actual_date IS NULL", (order_id,)).fetchall():
                    conn.execute("UPDATE ord_milestones SET planned_date=?, late_alerted=0 WHERE id=?",
                                 (str(sd - timedelta(days=int(m["offset_days"] or 0))), m["id"]))
        conn.commit()
        return True
    finally:
        conn.close()


def set_milestone(milestone_id, action, actual_date, user):
    conn = get_db()
    try:
        if action == "done":
            conn.execute("UPDATE ord_milestones SET status='done', actual_date=?, late_alerted=0 WHERE id=?",
                         (actual_date or str(date.today()), milestone_id))
        elif action == "undo":
            conn.execute("UPDATE ord_milestones SET status='pending', actual_date=NULL WHERE id=?",
                         (milestone_id,))
        conn.commit()
        return True
    finally:
        conn.close()


# --- sweep + dashboard ----------------------------------------------------
def tna_sweep():
    """Alert (once) on any milestone whose planned date has passed with no actual."""
    try:
        conn = get_db()
    except Exception:
        return
    try:
        today = str(date.today())
        rows = conn.execute(
            "SELECT m.id,m.name,m.planned_date,o.id AS oid,o.order_no,o.buyer FROM ord_milestones m "
            "JOIN ord_orders o ON o.id=m.order_id "
            "WHERE m.actual_date IS NULL AND m.late_alerted=0 AND m.planned_date IS NOT NULL "
            "AND m.planned_date < ? AND o.status NOT IN ('shipped','closed','cancelled')", (today,)).fetchall()
        for r in rows:
            _bell(conn, "warning", f"T&A slippage: {r['order_no']}",
                  f"'{r['name']}' was due {r['planned_date']} on {r['order_no']} ({r['buyer']}) — the ship date is at risk.",
                  f"/orders/{r['oid']}")
            conn.execute("UPDATE ord_milestones SET late_alerted=1 WHERE id=?", (r["id"],))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


def dashboard():
    conn = get_db()
    try:
        today = date.today()
        t = str(today); soon = str(today + timedelta(days=14))
        def one(sql, a=()):
            return conn.execute(sql, a).fetchone()["c"]
        d = {
            "orders_total": one("SELECT COUNT(*) c FROM ord_orders WHERE status NOT IN ('closed','cancelled')"),
            "in_production": one("SELECT COUNT(*) c FROM ord_orders WHERE status='in_production'"),
            "shipping_soon": one("SELECT COUNT(*) c FROM ord_orders WHERE ship_date IS NOT NULL AND ship_date<=? AND ship_date>=? AND status NOT IN ('shipped','closed','cancelled')", (soon, t)),
            "late_milestones": one("SELECT COUNT(*) c FROM ord_milestones m JOIN ord_orders o ON o.id=m.order_id WHERE m.actual_date IS NULL AND m.planned_date IS NOT NULL AND m.planned_date<? AND o.status NOT IN ('shipped','closed','cancelled')", (t,)),
        }
        orders = list_orders()
        d["at_risk_orders"] = [o for o in orders if o["health"] in ("late", "at_risk")]
        d["orders"] = orders
        return d
    finally:
        conn.close()


def tna_board():
    """Cross-order upcoming/overdue milestones — the planner's action list."""
    conn = get_db()
    try:
        today = date.today()
        rows = [dict(r) for r in conn.execute(
            "SELECT m.*, o.order_no, o.buyer, o.ship_date AS o_ship FROM ord_milestones m "
            "JOIN ord_orders o ON o.id=m.order_id "
            "WHERE m.actual_date IS NULL AND o.status NOT IN ('shipped','closed','cancelled') "
            "AND m.planned_date IS NOT NULL ORDER BY m.planned_date ASC LIMIT 60").fetchall()]
        for m in rows:
            m["view_status"] = ms_status(m, today)
            m["days"] = days_to(m["planned_date"])
        return rows
    finally:
        conn.close()
