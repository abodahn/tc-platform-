"""
TC Platform — health-driven notifications.

Turns real health-check results into notifications: when an integrated system
is offline, raise a single (deduplicated) critical notification; when it comes
back online, auto-resolve it. Keeps the notification bell honest.
"""
import time

import requests

from app.db import get_db, utcnow

_MARKER = "System offline:"  # title prefix used to find/dedupe auto-notifications

# Throttle for pulling notifications from the four systems (seconds). The feed
# endpoint is polled by every open browser, so we only actually hit the systems
# this often, process-wide.
_SYNC_MIN_INTERVAL = 25
_LAST_SYNC = [0.0]
_LAST_HEALTH = [0.0]
_VALID_SEV = {"info", "warning", "critical"}


def health_sync_due():
    """Cheap predicate the bell poller checks BEFORE probing the four systems.

    The guard inside sync_health_notifications alone is not enough: the caller has
    to run health.check_all() to produce ``statuses``, and that is the expensive
    half (up to _ALL_DEADLINE per poll, per browser). This lets the caller skip
    the probe too. It does not stamp — sync_health_notifications does that.
    """
    return time.time() - _LAST_HEALTH[0] >= _SYNC_MIN_INTERVAL


def sync_health_notifications(system_rows, statuses, force=False):
    """Raise a critical "system offline" notification once per outage, and mark it
    read again the moment the system answers.

    Throttled like sync_system_notifications: the bell feed calls this on every
    poll of every open browser, and each call is four SELECTs plus a commit.
    ``force=True`` for /health, which probes uncached on purpose.
    """
    now = time.time()
    if not force and (now - _LAST_HEALTH[0] < _SYNC_MIN_INTERVAL):
        return
    _LAST_HEALTH[0] = now
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


def sync_system_notifications(system_rows, timeout=2.5, force=False):
    """Pull each integrated system's own notifications into the platform feed.

    Every system exposes a normalized read-only endpoint at
    ``<base_url>/api/integration/notifications`` returning ``{"items": [ {id, severity,
    title, message, created_at}, ... ]}``. We merge new items into the platform
    ``notifications`` table tagged with the source system (``module`` = key) and
    prefix the title with the system name so it's always clear where an alert
    came from. Dedup is by ``ext_key`` = ``"<key>:<source id>"``.

    Throttled to once per ``_SYNC_MIN_INTERVAL`` seconds process-wide (the feed
    is polled by every browser). Returns the number of new notifications added.
    """
    now = time.time()
    if not force and (now - _LAST_SYNC[0] < _SYNC_MIN_INTERVAL):
        return 0
    _LAST_SYNC[0] = now

    inserted = 0
    conn = get_db()
    try:
        for row in system_rows:
            try:
                if not row["is_integrated"] or not row["base_url"]:
                    continue
            except Exception:
                continue
            key = row["key"]
            name = row["name_en"] or key
            url = str(row["base_url"]).rstrip("/") + "/api/integration/notifications"
            try:
                resp = requests.get(url, timeout=timeout)
                if resp.status_code != 200:
                    continue
                data = resp.json()
            except Exception:
                # endpoint missing / system down / bad JSON -> just skip it
                continue
            items = data.get("items") if isinstance(data, dict) else data
            if not isinstance(items, list):
                continue
            for it in items[:50]:
                if not isinstance(it, dict):
                    continue
                sev = str(it.get("severity") or "info").strip().lower()
                if sev not in _VALID_SEV:
                    sev = "info"
                title = (str(it.get("title") or "Notification")).strip()[:200]
                msg = (str(it.get("message") or "")).strip()[:1000]
                created = str(it.get("created_at") or utcnow())[:40]
                src_id = it.get("id")
                src_id = str(src_id) if src_id not in (None, "") else (title + "|" + msg)[:120]
                ext = f"{key}:{src_id}"
                if conn.execute("SELECT id FROM notifications WHERE ext_key=?", (ext,)).fetchone():
                    continue
                conn.execute(
                    "INSERT INTO notifications (severity, module, title, message, created_at, ext_key) "
                    "VALUES (?,?,?,?,?,?)",
                    (sev, key, f"[{name}] {title}", msg, created, ext))
                inserted += 1
        conn.commit()
    finally:
        conn.close()
    return inserted
