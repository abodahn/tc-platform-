"""
TC Platform — health-driven notifications.

Turns real health-check results into notifications: when an integrated system
is offline, raise a single (deduplicated) critical notification; when it comes
back online, auto-resolve it. Keeps the notification bell honest.
"""
from app.db import get_db, utcnow

_MARKER = "System offline:"  # title prefix used to find/dedupe auto-notifications


def sync_health_notifications(system_rows, statuses):
    conn = get_db()
    try:
        for row in system_rows:
            if not row["is_integrated"]:
                continue
            key = row["key"]
            status = (statuses.get(key) or {}).get("status")
            existing = conn.execute(
                "SELECT id FROM notifications WHERE module=? AND severity='critical' "
                "AND is_read=0 AND title LIKE ?", (key, _MARKER + "%")).fetchone()

            if status == "offline" and not existing:
                conn.execute(
                    "INSERT INTO notifications (severity, module, title, message, created_at) "
                    "VALUES ('critical', ?, ?, ?, ?)",
                    (key, f"{_MARKER} {row['name_en']}",
                     f"{row['name_en']} is not reachable at {row['base_url'] or 'its address'}.",
                     utcnow()))
            elif status in ("online", "warning") and existing:
                # auto-resolve: mark the offline alert as read
                conn.execute("UPDATE notifications SET is_read=1 WHERE id=?", (existing["id"],))
        conn.commit()
    finally:
        conn.close()
