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


@bp.route("/request-for")
@login_required
def request_for():
    """What a purchase request is FOR: an asset, a machine, a line or an area.

    Its own endpoint rather than an entry in SOURCES because it is a UNION of
    three registers and the generic path builds one WHERE clause against one
    table. It also differs from the `machines` lookup in two ways that matter
    here: the value is the TEXT that goes on the request (pr_requests.request_for
    is free text, not a foreign key), and the permission is the requester's own —
    gating this on maint_view would hand a blank box to the people who raise most
    of the requests.

    Free typing still works. This offers what the plant already has on file; a
    requester who needs something not in any register types it, exactly as before.
    """
    _guard("proc_create")
    q = (request.args.get("q") or "").strip()
    kind = (request.args.get("kind") or "").strip().lower()
    mtype = (request.args.get("type") or "").strip()
    like = "%" + q.lower() + "%"
    out, seen = [], set()

    def add(label, hint):
        key = (label or "").strip().lower()
        if label and key not in seen:
            seen.add(key)
            out.append({"value": label, "label": label, "hint": hint or ""})

    conn = get_db()
    try:
        # MACHINE TYPE — the first step of the cascade. 5,108 machines is too many
        # to scroll even with a search, but they fall into 154 types, and a person
        # who wants an overlock knows that before they know which overlock.
        if kind == "machine_type":
            try:
                rows = conn.execute(
                    "SELECT type, COUNT(*) AS n FROM mnt_machines "
                    "WHERE is_active=1 AND COALESCE(type,'') <> '' "
                    "AND (? = '' OR LOWER(type) LIKE ?) "
                    "GROUP BY type ORDER BY n DESC, type LIMIT ?",
                    (q.lower(), like, LIMIT)).fetchall()
                for r in rows:
                    add(r["type"], "%d" % r["n"])
            except Exception:
                pass
        elif kind == "machine":
            try:
                where, params = ["is_active=1"], []
                if mtype:
                    where.append("type = ?")
                    params.append(mtype)
                if q:
                    where.append("(LOWER(code) LIKE ? OR LOWER(name) LIKE ? "
                                 "OR LOWER(COALESCE(brand,'')) LIKE ? "
                                 "OR LOWER(COALESCE(model,'')) LIKE ?)")
                    params += [like] * 4
                rows = conn.execute(
                    "SELECT code, name, COALESCE(area,'') AS area FROM mnt_machines "
                    "WHERE " + " AND ".join(where) + " ORDER BY code LIMIT ?",
                    tuple(params) + (LIMIT,)).fetchall()
                for r in rows:
                    add("%s · %s" % (r["code"], r["name"] or ""), r["area"])
            except Exception:
                pass
        elif kind == "line":
            try:
                rows = conn.execute(
                    "SELECT name, COALESCE(area,'') AS area FROM production_lines "
                    "WHERE (? = '' OR LOWER(name) LIKE ?) ORDER BY name LIMIT ?",
                    (q.lower(), like, LIMIT)).fetchall()
                for r in rows:
                    add(r["name"], r["area"])
            except Exception:
                pass
        elif kind == "area":
            try:
                rows = conn.execute(
                    "SELECT DISTINCT area FROM mnt_machines "
                    "WHERE COALESCE(area,'') <> '' AND (? = '' OR LOWER(area) LIKE ?) "
                    "ORDER BY area LIMIT ?", (q.lower(), like, LIMIT)).fetchall()
                for r in rows:
                    add(r["area"], "")
            except Exception:
                pass
    finally:
        conn.close()
    return jsonify({"results": out[:LIMIT]})


@bp.route("/wh-stock")
@login_required
def wh_stock():
    """What the stores actually hold — spares and materials in one search.

    For the warehouse rung, whose whole job is "is this already on the shelf?".
    Two registers answer that and neither is the procurement catalogue: the
    maintenance spare store and the materials warehouse. Both carry stock_qty,
    what is reserved, where it sits, and the unit it is counted in — which is the
    difference between "we have 40" and "we have 40 metres, 30 of them promised
    to another order".
    """
    _guard("proc_view")
    q = (request.args.get("q") or "").strip()
    like = "%" + q.lower() + "%"
    out = []
    conn = get_db()
    try:
        for table, src in (("mnt_spare_parts", "spare"), ("wh_materials", "material")):
            try:
                rows = conn.execute(
                    "SELECT code, name, COALESCE(uom,'') AS uom, "
                    "       COALESCE(stock_qty,0) AS on_hand, "
                    "       COALESCE(reserved_qty,0) AS reserved, "
                    "       COALESCE(warehouse,'') AS wh, COALESCE(bin,'') AS bin "
                    "FROM %s WHERE is_active=1 "
                    "AND (? = '' OR LOWER(code) LIKE ? OR LOWER(name) LIKE ?) "
                    "ORDER BY name LIMIT ?" % table,
                    (q.lower(), like, like, LIMIT)).fetchall()
            except Exception:
                continue            # module not installed: search what else there is
            for r in rows:
                free = float(r["on_hand"] or 0) - float(r["reserved"] or 0)
                out.append({
                    "value": r["code"], "label": "%s · %s" % (r["code"], r["name"] or ""),
                    "source": src, "uom": r["uom"],
                    "on_hand": float(r["on_hand"] or 0),
                    "reserved": float(r["reserved"] or 0),
                    "free": free,
                    "where": (" / ".join(x for x in (r["wh"], r["bin"]) if x)) or "",
                })
    finally:
        conn.close()
    # Most stock first: a storekeeper checking availability wants what is there.
    out.sort(key=lambda x: -x["free"])
    return jsonify({"results": out[:LIMIT]})


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
