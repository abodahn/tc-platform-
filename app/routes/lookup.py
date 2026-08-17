"""
TC Platform — /api/lookup: type-ahead sources for the searchable dropdowns.

The big pickers in this app are backed by tables nobody can scroll: 19k
catalogue items, a 5k-machine fleet, the user directory. Rendering those into
<option> tags makes the page enormous and the dropdown useless. tc-combobox.js
calls these endpoints instead, with ?q=, and shows what comes back.

Response shape, identical for every source:

    {"results": [{"value": ..., "label": "...", "hint": "...", "data": {...}}]}

`value` is what the form POSTs, and it is deliberately whatever the <select>
being replaced already posted — id for items/machines/spares/orders, username
for users, name for vendors and departments — so no route or handler changes.
`data` carries optional extra fields the page needs (the new-ticket form fills
department/area/line from the chosen machine); the combobox copies them onto
the <option> as data-* attributes.

Each source is gated on the same permission its owning module uses, and no
source exposes a cost, a price, a serial number or an email address.
"""
from flask import Blueprint, abort, jsonify, request

from app.auth import login_required, current_user
from app.db import get_db
from app.security import user_has_permission

bp = Blueprint("lookup", __name__, url_prefix="/api/lookup")

LIMIT = 50          # a type-ahead never needs more; the user types instead
MAX_Q = 80

# kind -> permission, projection, base filter, searched columns, order.
# The SQL here is ALL literal — only ?-parameters ever carry user input.
SOURCES = {
    "items": {
        "perm": "proc_view",
        "sql": "SELECT id AS value, code || ' · ' || COALESCE(name,'') AS label, "
               "COALESCE(unit,'') AS hint FROM proc_items",
        "where": "active=1",
        "search": ("code", "name", "category_name"),
        "order": "code",
    },
    "vendors": {
        "perm": "proc_view",
        "sql": "SELECT name AS value, name AS label, COALESCE(category,'') AS hint, "
               "id AS vendor_id FROM proc_vendors",
        "where": "is_active=1",
        "search": ("name", "category"),
        "order": "name",
        "data": ("vendor_id",),
    },
    "machines": {
        "perm": "maint_view",
        "sql": "SELECT id AS value, code || ' · ' || COALESCE(name,'') AS label, "
               "COALESCE(area,'') AS hint, COALESCE(department,'') AS dept, "
               "COALESCE(area,'') AS area, COALESCE(line_no,'') AS line "
               "FROM mnt_machines",
        "where": "is_active=1",
        "search": ("code", "name", "type", "brand", "model", "area"),
        "order": "code",
        "data": ("dept", "area", "line"),
    },
    "spares": {
        "perm": "maint_view",
        "sql": "SELECT id AS value, code || ' · ' || COALESCE(name,'') AS label, "
               "COALESCE(category,'') AS hint, COALESCE(uom,'') AS uom, "
               "stock_qty AS stock FROM mnt_spare_parts",
        "where": "is_active=1",
        "search": ("code", "name", "category", "brand"),
        "order": "code",
        "data": ("uom", "stock"),
    },
    "users": {
        "perm": "view_dashboard",
        "sql": "SELECT username AS value, COALESCE(full_name, username) AS label, "
               "COALESCE(role,'') AS hint FROM users",
        "where": "is_active=1",
        "search": ("username", "full_name"),
        "order": "username",
    },
    "orders": {
        "perm": "view_dashboard",
        "sql": "SELECT id AS value, COALESCE(order_no,'') || ' · ' || COALESCE(buyer,'') AS label, "
               "COALESCE(style_ref,'') AS hint FROM ord_orders",
        "where": "1=1",
        "search": ("order_no", "buyer", "po_no", "style_ref", "style_name"),
        "order": "id DESC",
    },
}


def _like(q):
    """Lowered, %-wrapped LIKE pattern with the wildcards in the user's own text
    escaped. LOWER() on both sides because Postgres LIKE is case-SENSITIVE and
    SQLite's is not — without this the same search behaves differently on the
    two databases this app runs on."""
    q = q[:MAX_Q].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + q.lower() + "%"


def _guard(perm):
    user = current_user()
    if not user_has_permission(user, perm):
        abort(403)


@bp.route("/departments")
@login_required
def departments():
    """Department master (constants + whatever budgets/matrices already use).
    Not a table of its own, so it does not go through the generic path."""
    _guard("view_dashboard")
    from app.approvals.services import list_departments
    q = (request.args.get("q") or "").strip().lower()
    names = [d for d in list_departments() if not q or q in d.lower()]
    return jsonify({"results": [{"value": d, "label": d, "hint": ""}
                                for d in names[:LIMIT]]})


@bp.route("/<kind>")
@login_required
def lookup(kind):
    src = SOURCES.get(kind)
    if not src:
        abort(404)
    _guard(src["perm"])

    q = (request.args.get("q") or "").strip()
    where, params = [src["where"]], []
    if q:
        # An empty q is NOT "match everything" — it falls through with no search
        # clause and the LIMIT below returns the first page, never the table.
        pattern = _like(q)
        where.append("(" + " OR ".join(
            "LOWER(%s) LIKE ? ESCAPE '\\'" % c for c in src["search"]) + ")")
        params += [pattern] * len(src["search"])

    sql = "%s WHERE %s ORDER BY %s LIMIT ?" % (
        src["sql"], " AND ".join(where), src["order"])
    conn = get_db()
    try:
        rows = conn.execute(sql, tuple(params) + (LIMIT,)).fetchall()
    except Exception:
        # A module whose tables have not been created yet (fresh database, or a
        # deployment where that module never ran) must degrade to "no matches",
        # not a 500 that breaks the whole form.
        rows = []
    finally:
        conn.close()

    extra = src.get("data") or ()
    out = []
    for r in rows:
        row = {"value": r["value"], "label": (r["label"] or "").strip(" ·"),
               "hint": r["hint"] or ""}
        if extra:
            row["data"] = {k: r[k] for k in extra}
        out.append(row)
    return jsonify({"results": out})
