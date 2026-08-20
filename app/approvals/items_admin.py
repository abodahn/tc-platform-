# -*- coding: utf-8 -*-
"""
TC Platform — item master maintenance, the store's own door into the catalogue.

The workbook import (catalogue.py) upserts thousands of rows from the ERP in one
shot. This module is the opposite: one row, one person, one deliberate change,
every change on the record.

WHY THE STORE. The store handles the physical item every day and is the only
function that notices the unit is wrong, that a name describes a part nobody has
stocked for two years, or that two codes are the same thing spelled differently.
Purchasing keep the door too — they own item and supplier data — but the store
is the one looking at the shelf.

WHAT IS DELIBERATELY NOT HERE: cost price. Value enters this platform exactly
once, at the Purchasing pricing gate, and a second editable door on cost would
mean a request could be approved against a number nobody negotiated. The screen
SHOWS the cost so the store can see what it is looking at; only the pricing gate
and an ERP import can change it.

NOTHING IS EVER DELETED. A code that has been purchased appears on old requests,
purchase orders and receipts; deleting the row would leave those documents
pointing at nothing. Items are retired and can be restored.

THE ERP KEEPS THE CODES. Nothing here generates a code — the code required on
creation is the one the ERP already issued. That is what keeps the two systems
describing the same part, and it is the same rule new-item requests follow.
"""
from app.approvals.catalogue import norm_unit

ITEM_FIELDS = ("code", "name", "unit", "category_code", "category_name")

# The audience for the "just so you know" notification. Purchasing own item and
# supplier data, so they are the people surprised if the master moves under them.
_TELL_ROLES = ("purchasing_manager", "proc_admin", "super_admin", "admin")


def _clean(raw, limit=160):
    return (str(raw or "").strip())[:limit]


def search_items(conn, q="", category="", active="1", limit=60, offset=0):
    """One page of the catalogue, with the total that matched.

    The master runs to five figures, so this never returns it whole — the caller
    always pages. The count comes back alongside so the screen can say how many
    matched rather than how many happened to fit on the page.
    """
    where, args = [], []
    if str(active) in ("0", "1"):
        where.append("active=?")
        args.append(int(active))
    category = _clean(category, 80)
    if category:
        where.append("(category_code=? OR category_name=?)")
        args += [category, category]
    q = _clean(q, 80)
    if q:
        where.append("(code LIKE ? OR name LIKE ?)")
        args += ["%" + q + "%", "%" + q + "%"]
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute("SELECT COUNT(*) AS n FROM proc_items" + sql_where,
                         args).fetchone()["n"]
    # A storekeeper who knows the code types the code, so a prefix hit on the
    # code sorts above a substring hit buried in a name.
    rows = conn.execute(
        "SELECT id, code, name, unit, category_code, category_name, cost_price, "
        "has_cost, source, active, updated_by, updated_at FROM proc_items"
        + sql_where +
        " ORDER BY CASE WHEN code LIKE ? THEN 0 ELSE 1 END, code "
        "LIMIT ? OFFSET ?",
        args + [((q + "%") if q else "\x00"), int(limit), int(offset)]).fetchall()
    return [dict(r) for r in rows], int(total)


def get_item(conn, item_id):
    r = conn.execute("SELECT * FROM proc_items WHERE id=?", (item_id,)).fetchone()
    return dict(r) if r else None


def item_by_code(conn, code):
    r = conn.execute("SELECT * FROM proc_items WHERE code=?",
                     (_clean(code, 60),)).fetchone()
    return dict(r) if r else None


def categories(conn):
    rows = conn.execute(
        "SELECT DISTINCT category_name AS c FROM proc_items "
        "WHERE category_name IS NOT NULL AND category_name<>'' ORDER BY c").fetchall()
    return [r["c"] for r in rows]


def item_history(conn, item_id, limit=40):
    """Every change to this row, newest first, out of the platform audit log.

    The detail column carries the item id as `#<id> ` so one LIKE finds them all
    without a second table that would have to be kept in step.
    """
    rows = conn.execute(
        "SELECT username, action, detail, created_at FROM audit_logs "
        "WHERE action LIKE 'catalogue\\_%' ESCAPE '\\' AND detail LIKE ? "
        "ORDER BY id DESC LIMIT ?",
        ("#%d %%" % int(item_id), int(limit))).fetchall()
    return [dict(r) for r in rows]


def _tell(conn, user, action, item, summary, ip=""):
    """Record the change, then tell the people who own item data.

    Informational only. Nothing here asks anyone to approve anything, so the
    severity is 'info' and the link goes to the item, not to an approval queue.
    """
    from app.approvals.services import notify_users
    from app.db import log_audit
    code = item.get("code") or "?"
    detail = "#%s %s — %s" % (item.get("id"), code, summary)
    actor = (user or {}).get("username") or "system"
    try:
        log_audit(actor, action, detail, ip)
    except Exception:
        pass                    # the change is the point; the log is best-effort
    verb = {"catalogue_created": "added",
            "catalogue_edited": "edited",
            "catalogue_retired": "retired",
            "catalogue_restored": "restored"}.get(action, "changed")
    marks = ",".join("?" for _ in _TELL_ROLES)
    who = [r["username"] for r in conn.execute(
        "SELECT username FROM users WHERE is_active=1 AND role IN (%s)" % marks,
        list(_TELL_ROLES)).fetchall() if r["username"] != actor]
    if who:
        notify_users(conn, who, "info",
                     "Item %s %s" % (code, verb),
                     "%s — %s. By %s." % (code, summary, actor),
                     link="/procurement/items?q=" + code)


def create_item(conn, form, user, ip=""):
    """Add one item by hand. Returns (ok, code_or_reason)."""
    data = {f: _clean(form.get(f)) for f in ITEM_FIELDS}
    if not data["code"]:
        return False, "code_required"
    if not data["name"]:
        return False, "name_required"
    if item_by_code(conn, data["code"]):
        return False, "duplicate_code"
    data["unit"] = norm_unit(data["unit"]) or "Pcs"
    from app.approvals.services import _now
    # Born with has_cost = 0 on purpose: the row exists, its price does not yet.
    # Same birth condition as an approved new-item request.
    conn.execute(
        "INSERT INTO proc_items (code, name, unit, category_code, category_name, "
        "cost_price, has_cost, source, active, updated_by, updated_at) "
        "VALUES (?,?,?,?,?,0,0,'manual',1,?,?)",
        (data["code"], data["name"], data["unit"], data["category_code"],
         data["category_name"], (user or {}).get("username"), _now()))
    conn.commit()
    row = item_by_code(conn, data["code"])
    _tell(conn, user, "catalogue_created", row,
          "added by hand (%s%s)" % (row["unit"],
                                    ", " + row["category_name"] if row["category_name"] else ""),
          ip)
    conn.commit()
    return True, row["code"]


def update_item(conn, item_id, form, user, ip=""):
    """Edit one item. Returns (ok, reason).

    Only fields that actually CHANGED are reported. A save with no edits raises
    no audit row and no notification — otherwise the bell fills with "changed
    nothing" and people stop reading it.
    """
    before = get_item(conn, item_id)
    if not before:
        return False, "not_found"
    data = {f: _clean(form.get(f)) for f in ITEM_FIELDS}
    if not data["code"]:
        return False, "code_required"
    if not data["name"]:
        return False, "name_required"
    data["unit"] = norm_unit(data["unit"]) or before["unit"] or "Pcs"
    clash = item_by_code(conn, data["code"])
    if clash and int(clash["id"]) != int(item_id):
        return False, "duplicate_code"
    changed = [(f, before.get(f) or "", data[f]) for f in ITEM_FIELDS
               if (before.get(f) or "") != data[f]]
    if not changed:
        return True, "unchanged"
    from app.approvals.services import _now
    conn.execute(
        "UPDATE proc_items SET code=?, name=?, unit=?, category_code=?, "
        "category_name=?, updated_by=?, updated_at=? WHERE id=?",
        (data["code"], data["name"], data["unit"], data["category_code"],
         data["category_name"], (user or {}).get("username"), _now(), item_id))
    conn.commit()
    summary = "; ".join("%s %s → %s" % (f.replace("_", " "), old or "—", new or "—")
                        for f, old, new in changed)
    _tell(conn, user, "catalogue_edited", get_item(conn, item_id), summary, ip)
    conn.commit()
    return True, "updated"


def set_item_active(conn, item_id, active, user, ip=""):
    """Retire an item, or bring it back. Returns (ok, reason)."""
    row = get_item(conn, item_id)
    if not row:
        return False, "not_found"
    want = 1 if str(active) in ("1", "true", "True", "on", "yes") else 0
    if int(row["active"] or 0) == want:
        return True, "unchanged"
    from app.approvals.services import _now
    conn.execute("UPDATE proc_items SET active=?, updated_by=?, updated_at=? WHERE id=?",
                 (want, (user or {}).get("username"), _now(), item_id))
    conn.commit()
    _tell(conn, user, "catalogue_restored" if want else "catalogue_retired",
          row, "back in use" if want else "retired from the catalogue", ip)
    conn.commit()
    return True, "restored" if want else "retired"
