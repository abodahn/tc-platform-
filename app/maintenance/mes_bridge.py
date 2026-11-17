"""Connect CMMS maintenance events to the shared production line master.
The floor board reads stopped machines without changing maintenance state.
Closed production-stopping tickets export one capped MES downtime row;
the ticket retains the row identity across reopening and closing again.
TC Terminal faults reach MES only through this path (machine_events.py writes
no mes_downtime), so a terminal-raised ticket is counted once, here.
A machine's line_no joins production_lines by name (case/space-insensitive);
no match means show-only, no MES row.
"""
from app.db import get_db


def down_machines(limit=200):
    """Active machines not running for maintenance, with their latest open ticket; never raises."""
    conn = None
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT m.id,m.code,m.name,m.line_no,m.status, "
            "(SELECT p.name FROM production_lines p "
            "WHERE LOWER(TRIM(p.name))=LOWER(TRIM(m.line_no)) "
            "ORDER BY p.id LIMIT 1) AS line_name, "
            "t.id AS ticket_id,t.ticket_no FROM mnt_machines m "
            "LEFT JOIN mnt_tickets t ON t.id=(SELECT MAX(t2.id) FROM mnt_tickets t2 "
            "WHERE t2.machine_id=m.id AND t2.is_active=1 "
            "AND t2.status NOT IN ('closed','cancelled','rejected','draft')) "
            "WHERE m.is_active=1 AND m.status IN "
            "('stopped','under_maintenance','waiting_spare','under_testing') "
            "ORDER BY m.line_no,m.code LIMIT ?", (max(0, int(limit)),)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def record_ticket_downtime(ticket_id):
    """Export a closed production stop exactly once, in one transaction."""
    conn = None
    try:
        from app.mes.constants import MAX_DOWNTIME_MIN
        from app.maintenance.services import _now
        conn = get_db()
        t = conn.execute(
            "SELECT t.status,t.production_stopped,t.total_downtime_min,t.created_at, "
            "t.line_no,t.machine_id,t.ticket_no,t.mes_downtime_id,m.line_no AS machine_line_no "
            "FROM mnt_tickets t LEFT JOIN mnt_machines m ON m.id=t.machine_id "
            "WHERE t.id=?", (ticket_id,)).fetchone()
        if (not t or t["status"] != "closed" or not t["production_stopped"]
                or float(t["total_downtime_min"] or 0) <= 0
                or t["mes_downtime_id"] is not None):
            return
        line = (t["line_no"] or "").strip() or (t["machine_line_no"] or "").strip()
        if not line:
            return
        p = conn.execute(
            "SELECT id FROM production_lines WHERE LOWER(TRIM(name))=LOWER(TRIM(?)) "
            "ORDER BY id LIMIT 1", (line,)).fetchone()
        if not p:
            return
        minutes = min(float(t["total_downtime_min"]), MAX_DOWNTIME_MIN)
        work_date = t["created_at"][:10]
        claim = conn.execute(
            "UPDATE mnt_tickets SET mes_downtime_id=0 WHERE id=? AND mes_downtime_id IS NULL",
            (ticket_id,))
        if claim.rowcount != 1:
            return
        cur = conn.execute(
            "INSERT INTO mes_downtime (line_id,work_date,reason,minutes,created_by,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (p["id"], work_date, "machine_breakdown", minutes, "cmms:" + t["ticket_no"], _now()))
        conn.execute("UPDATE mnt_tickets SET mes_downtime_id=? WHERE id=?",
                     (cur.lastrowid, ticket_id))
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
