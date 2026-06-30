"""
Auto-ticketing: turn critical alerts into Service Desk (ITSM) tickets.

When monitoring (or another configured source system) reports a critical alert,
open an ITSM incident automatically via ITSM's /api/integration/ticket endpoint.
Deduped two ways: we only act on notifications without an auto_ticket_ref, and
the ITSM endpoint is idempotent per external_ref. OFF unless TC_AUTO_TICKET_ENABLED.
"""
from __future__ import annotations

import logging
import time

import requests

from app.db import get_db
from config import Config

log = logging.getLogger("tc.autoticket")

_RANK = {"info": 0, "warning": 1, "critical": 2}
_LAST = [0.0]
_MIN_INTERVAL = 20  # seconds between actual passes (the feed is polled often)


def _target_base(system_rows) -> str | None:
    for r in system_rows:
        try:
            if r["key"] == Config.AUTO_TICKET_TARGET and r["base_url"]:
                return str(r["base_url"]).rstrip("/")
        except Exception:
            pass
    return None


def auto_create_tickets(system_rows, timeout: float = 4.0, force: bool = False) -> int:
    """Open ITSM tickets for new critical alerts from the configured source
    systems. Returns the number of tickets created."""
    if not Config.AUTO_TICKET_ENABLED:
        return 0
    now = time.time()
    if not force and (now - _LAST[0] < _MIN_INTERVAL):
        return 0
    _LAST[0] = now

    base = _target_base(system_rows)
    if not base:
        return 0
    floor = _RANK.get(Config.AUTO_TICKET_MIN_SEVERITY, 2)
    sevs = [s for s, r in _RANK.items() if r >= floor]
    modules = [m.strip() for m in Config.AUTO_TICKET_SOURCE_MODULES.split(",") if m.strip()]
    if not sevs or not modules:
        return 0

    conn = get_db()
    created = 0
    try:
        sev_ph = ",".join("?" for _ in sevs)
        mod_ph = ",".join("?" for _ in modules)
        rows = conn.execute(
            f"SELECT id, severity, module, title, message FROM notifications "
            f"WHERE auto_ticket_ref IS NULL AND severity IN ({sev_ph}) AND module IN ({mod_ph}) "
            f"ORDER BY id DESC LIMIT 20", tuple(sevs) + tuple(modules)).fetchall()
        for n in rows:
            ext = f"notif:{n['id']}"
            try:
                resp = requests.post(base + "/api/integration/ticket", timeout=timeout, json={
                    "title": n["title"], "description": n["message"] or n["title"],
                    "severity": n["severity"], "external_ref": ext,
                    "category": "Infrastructure", "requester": Config.AUTO_TICKET_REQUESTER})
                if resp.status_code == 200:
                    tno = (resp.json() or {}).get("ticket_no") or ext
                    conn.execute("UPDATE notifications SET auto_ticket_ref=? WHERE id=?", (str(tno), n["id"]))
                    created += 1
            except Exception as exc:  # one failure must not block the rest
                log.warning("auto-ticket failed for notification %s: %s", n["id"], exc)
        conn.commit()
    finally:
        conn.close()
    return created
