"""
TC Platform — live snapshot for Garamento.

Builds a compact, current status block (counts only — no sensitive detail) that
is injected into the chat system prompt so Garamento can answer real questions
like "how many critical tickets are open?" or "what's waiting on me?". Every
metric is fetched defensively: a missing table or column is skipped, never
raised, so chat keeps working on any deployment.
"""
from __future__ import annotations


def _scalar(conn, sql, params=()):
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        try:
            return row[0]
        except Exception:  # noqa: BLE001
            return row["c"]
    except Exception:  # noqa: BLE001
        return None


def snapshot(user=None):
    """Return a short text block of current platform status, or "" on failure."""
    try:
        from app.db import get_db
    except Exception:  # noqa: BLE001
        return ""
    username = (user or {}).get("username") if user else None
    lines = []
    conn = None
    try:
        conn = get_db()

        # --- Maintenance ---
        open_t = _scalar(conn, "SELECT COUNT(*) c FROM mnt_tickets WHERE status NOT IN ('closed','cancelled','rejected')")
        crit_t = _scalar(conn, "SELECT COUNT(*) c FROM mnt_tickets WHERE priority='critical' AND status NOT IN ('closed','cancelled','rejected')")
        stopped = _scalar(conn, "SELECT COUNT(*) c FROM mnt_machines WHERE status='stopped'")
        mine_t = None
        if username:
            name = (user.get("full_name") or username)
            mine_t = _scalar(conn, "SELECT COUNT(*) c FROM mnt_tickets WHERE assigned_to=? AND status NOT IN ('closed','cancelled','rejected')", (name,))
        m = []
        if open_t is not None:
            m.append(f"{open_t} open" + (f" ({crit_t} critical)" if crit_t else ""))
        if stopped:
            m.append(f"{stopped} machine(s) stopped")
        if mine_t:
            m.append(f"{mine_t} assigned to you")
        if m:
            lines.append("Maintenance tickets: " + ", ".join(m) + ".")

        # --- Procurement ---
        pend = _scalar(conn, "SELECT COUNT(*) c FROM pr_requests WHERE status='pending'")
        draft = _scalar(conn, "SELECT COUNT(*) c FROM pr_requests WHERE status='draft'")
        on_me = None
        if username:
            on_me = _scalar(conn, "SELECT COUNT(DISTINCT pr_id) c FROM pr_steps WHERE approver_user=? AND status='pending'", (username,))
        p = []
        if pend is not None:
            p.append(f"{pend} pending approval")
        if on_me:
            p.append(f"{on_me} awaiting your approval")
        if draft:
            p.append(f"{draft} draft(s)")
        if p:
            lines.append("Purchase requests: " + ", ".join(p) + ".")

        # --- Production ---
        try:
            rows = conn.execute("SELECT status, COUNT(*) c FROM production_lines GROUP BY status").fetchall()
            by = {r["status"]: r["c"] for r in rows}
            if by:
                lines.append("Production lines: " + ", ".join(f"{v} {k}" for k, v in by.items()) + ".")
        except Exception:  # noqa: BLE001
            pass

        # --- Notifications (for this user) ---
        if username:
            unread = _scalar(conn, "SELECT COUNT(*) c FROM notifications WHERE is_read=0 AND (target_user IS NULL OR target_user=?)", (username,))
            if unread:
                lines.append(f"Unread notifications: {unread}.")
    except Exception:  # noqa: BLE001
        return ""
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    if not lines:
        return ""
    return ("LIVE PLATFORM STATUS (current counts — use these to answer questions about "
            "what's open or pending; if asked for specifics beyond these numbers, point "
            "the user to the relevant module):\n- " + "\n- ".join(lines))
