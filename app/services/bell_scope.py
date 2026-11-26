"""
TC Platform — whose bell a notification row belongs in.

A row addressed to a person (target_user) is theirs alone, as before. A
BROADCAST row (target_user NULL) used to ring every account in the company: a
low-stock alert, an ITSM ticket pulled from the service desk, "system offline".
Now a broadcast reaches only the OWNERS of its module — the people who hold that
module's own management / operations permission — and super_admin. A module not
in the map below reaches super_admin only, so a new broadcaster is quiet by
default instead of loud.

Read state: an addressed row keeps using notifications.is_read. A broadcast is
shared by several owners, so one owner reading it is recorded per person in
notification_reads and no longer hides it from the others. notifications.is_read
on a broadcast still means "resolved for everyone" (notify.py's auto-resolve of
a "system offline" alert sets it), so it still hides the row for all.

A scope-locked account (security.system_scope: itsm_user, ...) additionally
sees only rows of its own systems (or with no module), here, so the bell, the
count, the feed, /my-work and mark-read share one definition.

Everything is one SQL WHERE fragment built from the reader's permissions — no
per-row Python filtering — so the bell's LIMIT 30 query stays cheap. The unread
count stops at UNREAD_CAP (the badge shows "99+").

Owners (final list): IT alerts (system offline, pulled ITSM / Assets /
Monitoring / CommandTrack alerts, AI risk alerts, owner-less BI alerts, and any
integrated system an admin adds later) -> manage_integrations only (it_manager,
it_director) — not the read-only health viewers. warehouse -> wh_manage;
maintenance -> maint_manage; orders -> pln_plan (production, not IT); quality ->
qc_inspect + pln_plan (inspectors, and production who must fix the defect);
the rest as mapped below. Unmapped -> super_admin only.
"""
from app.security import system_scope, user_has_permission

_IT = ("manage_integrations",)
UNREAD_CAP = 100

# notifications.module -> the permission(s) that make a user an owner of that
# module's broadcasts (holding ANY of them is enough).
MODULE_OWNERS = {
    # manufacturing modules (their _bell() helpers broadcast)
    "maintenance": ("maint_manage",),                 # rows are addressed already; legacy broadcasts
    "warehouse": ("wh_manage",),                      # low / out-of-stock sweep: storekeepers + managers
    "orders": ("pln_plan",),                          # T&A slippage: production planning, not IT
    "planning": ("pln_plan",),
    "costing": ("cost_manage",),
    "cutroom": ("cut_manage",),
    "mes": ("mes_entry",),
    "plm": ("plm_manage", "plm_approve"),
    "quality": ("qc_inspect", "pln_plan"),            # AQL fail / DHU: QC + production fixes it
    "shipping": ("shp_manage",),
    "trace": ("trc_manage",),
    "wash": ("wsh_manage",),
    "compliance": ("cmp_manage",),
    "people": ("ppl_manage",),
    # addressed-row modules: only a legacy broadcast row would ever need this
    "procurement": ("proc_admin",),
    "accounts": ("users_approve",),
    "hr": ("prob_hr_review",),
    # AI / BI / system alerts -> the IT owners
    "intelligence": _IT,                              # app/ai_engine/orchestrator.py risk alerts
    "bi": _IT,                                        # BI alert without an owner (owned ones are addressed)
    # systems.key values: notify.py pulls each integrated system's own alerts and
    # raises "system offline" under the system's key; db.py seeds itsm/monitoring/
    # assets/commandtrack samples.
    "itsm": _IT, "assets": _IT, "monitoring": _IT, "commandtrack": _IT,
    "ai_hub": _IT, "finance": _IT, "automation": _IT, "production": _IT,
    "saplite": _IT, "governance": _IT, "kb": _IT, "reports": _IT,
}


def owned_modules(user):
    """None = every broadcast (super_admin); else the module keys this user owns."""
    if (user or {}).get("role") == "super_admin":
        return None
    return sorted(m for m, perms in MODULE_OWNERS.items() if user_has_permission(user, perms))


def visible_where(user, alias="notifications"):
    """(sql, params): the rows this user may see in their bell."""
    me = (user or {}).get("username")
    mods = owned_modules(user)
    if mods is None:
        sql, params = f"({alias}.target_user = ? OR {alias}.target_user IS NULL)", [me]
    else:
        ors, params = [f"{alias}.target_user = ?"], [me]
        if mods:
            ors.append(f"({alias}.target_user IS NULL AND {alias}.module IN "
                       f"({','.join('?' * len(mods))}))")
            params += mods
        if user_has_permission(user, _IT):
            # an integrated system an admin registered later is IT's too
            ors.append(f"({alias}.target_user IS NULL AND {alias}.module IN "
                       f"(SELECT key FROM systems WHERE is_integrated = 1))")
        sql = "(" + " OR ".join(ors) + ")"
    scope = system_scope(user) if user else None
    if scope is not None:
        keys = sorted(scope)
        inside = f" OR {alias}.module IN ({','.join('?' * len(keys))})" if keys else ""
        sql += f" AND (COALESCE({alias}.module, '') = ''{inside})"
        params += keys
    return sql, params


def _read_by_me(alias):
    return (f"EXISTS (SELECT 1 FROM notification_reads r "
            f"WHERE r.notification_id = {alias}.id AND r.username = ?)")


def unread_where(user, alias="notifications"):
    """(sql, params): visible AND unread for this user."""
    vis, params = visible_where(user, alias)
    sql = (f"{vis} AND {alias}.is_read = 0 AND NOT ({alias}.target_user IS NULL "
           f"AND {_read_by_me(alias)})")
    return sql, params + [(user or {}).get("username")]


def bell_rows(conn, user, limit=30):
    """The newest visible rows as dicts, is_read reflecting THIS reader."""
    vis, params = visible_where(user, "n")
    rows = conn.execute(
        f"SELECT n.*, CASE WHEN n.target_user IS NULL AND {_read_by_me('n')} "
        f"THEN 1 ELSE 0 END AS my_read FROM notifications n WHERE {vis} "
        f"ORDER BY n.id DESC LIMIT {int(limit)}",
        [(user or {}).get("username")] + params).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["is_read"] = 1 if (d.pop("my_read", 0) or d.get("is_read")) else 0
        out.append(d)
    return out


def unread_count(conn, user):
    """Unread rows for this reader, capped at UNREAD_CAP (the badge says 99+), so
    the count never walks every broadcast ever raised on each page render."""
    sql, params = unread_where(user)
    return conn.execute(
        f"SELECT COUNT(*) AS c FROM (SELECT notifications.id FROM notifications WHERE {sql} "
        f"ORDER BY notifications.id DESC LIMIT {UNREAD_CAP}) capped", params).fetchone()["c"]


def mark_read(conn, user, nid=None, now=None):
    """Mark one row (nid) or every unread visible row read — for this reader only.
    Addressed rows: is_read. Broadcasts: a notification_reads row. Caller commits."""
    from app.db import utcnow
    me = (user or {}).get("username")
    now = now or utcnow()
    one = " AND id = ?" if nid else ""
    extra = [int(nid)] if nid else []
    sql, params = unread_where(user)
    conn.execute(f"UPDATE notifications SET is_read = 1 WHERE target_user = ? AND {sql}{one}",
                 [me] + params + extra)
    conn.execute(
        f"INSERT OR IGNORE INTO notification_reads (notification_id, username, read_at) "
        f"SELECT id, ?, ? FROM notifications WHERE target_user IS NULL AND {sql}{one}",
        [me, now] + params + extra)
