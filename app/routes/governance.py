"""
TC Platform — Governance review surface (/governance).

The platform ships 60+ permissions across ~30 roles and, until now, no page a
human could read to answer "who can do what". This blueprint is that page. It is
READ-ONLY: it computes everything from app.security, app.navigation, the module
constants and the users table, and changes nothing.

Five views (tabs):
  index          role x permission matrix, filterable by group and role
  users          per-user extra permissions (users.extra_perms) — invisible today
  responsibility RACI-style view/change/approve per module, derived from the
                 _view/_manage/_approve/_admin naming convention
  rules          procurement ladder + thresholds + escalation chain, SoD state,
                 maintenance ladder, and who holds each role right now
  findings       the five governance findings the page raises by itself

Permission gate: access_admin.
  Chosen over view_reports because view_reports is held by ~14 roles including
  executive_viewer, project_manager and service_desk_agent. This page enumerates
  the whole authorisation model AND lists the routes with no permission check —
  that is an attack map, not a report. access_admin is held by super_admin and
  it_director only, which is the right blast radius. Every route below, both HTML
  and both export formats, carries the same gate.

Boot cost: none. No DDL, no seed, no import-time query. Every number is computed
per request; the two source scans are memoised in-process after first use because
the source of a running worker cannot change.
"""
import io
import os
import re
import inspect
import json

from flask import Blueprint, render_template, request, abort, current_app, Response

from app.auth import login_required, permission_required
from app.db import get_db
from app.security import (PERMISSIONS, BUILTIN_ROLE_KEYS, effective_roles,
                          permission_catalogue, permission_label, permission_desc)
from app.services.export import csv_file

bp = Blueprint("governance", __name__, url_prefix="/governance")

PERM = "access_admin"          # see the module docstring for the justification

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ===========================================================================
# Roles, holders, users
# ===========================================================================
def role_rows():
    """Every effective role (code roles + DB custom_roles), ordered: super_admin
    first, then the built-ins, then the admin-created ones."""
    eff = effective_roles()
    out = []
    for key, val in eff.items():
        perms = list(val.get("perms") or [])
        out.append({
            "key": key,
            "label": val.get("label") or key,
            "perms": set(perms),
            "star": "*" in perms,
            "custom": key not in BUILTIN_ROLE_KEYS,
        })
    out.sort(key=lambda r: (r["key"] != "super_admin", r["custom"], r["key"]))
    return out


def active_users():
    """Active users with everything needed to compute effective permissions."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, username, full_name, role, extra_perms FROM users "
            "WHERE is_active = 1 ORDER BY username").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _extra_list(raw):
    """users.extra_perms is a JSON array of permission keys (or junk)."""
    if not raw:
        return []
    try:
        val = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return []
    return sorted(p for p in val if isinstance(p, str)) if isinstance(val, list) else []


def role_holders(users=None):
    """{role_key: count of active users}. Roles with zero holders are a
    governance hole; roles with exactly one are a continuity risk."""
    counts = {}
    for u in (users if users is not None else active_users()):
        counts[u["role"]] = counts.get(u["role"], 0) + 1
    return counts


def permission_holders(users=None, roles=None, via_star=True):
    """{permission: count of active users who effectively hold it}, counting
    role grants, '*' and per-user extra_perms alike.

    via_star=False counts only EXPLICIT grants — a permission named on the role
    or on the user. It deliberately ignores '*'. Without that distinction the
    "granted but held by nobody" finding is dead on arrival: one active
    super_admin holds every permission through '*', so the check can never fire
    on a live platform, and the page would report "nothing found" over a
    permission whose only holder is the root account."""
    users = users if users is not None else active_users()
    by_role = {r["key"]: r for r in (roles if roles is not None else role_rows())}
    counts = {p: 0 for p in PERMISSIONS}
    for u in users:
        role = by_role.get(u["role"])
        if role and role["star"]:
            held = set(PERMISSIONS) if via_star else set()
        else:
            held = set(role["perms"]) if role else set()
        extra = set(_extra_list(u.get("extra_perms")))
        if "*" in extra:
            held = set(PERMISSIONS) if via_star else held
            extra = extra - {"*"}
        held |= extra
        for p in held:
            if p in counts:
                counts[p] += 1
    return counts


def cell(role, perm):
    """One matrix cell: 'star' (granted via '*'), 'yes' or 'no'."""
    if role["star"]:
        return "star"
    return "yes" if perm in role["perms"] else "no"


# ===========================================================================
# Responsibility matrix — derived from the permission NAMES, not a hand list
# ===========================================================================
MODULE_PREFIXES = [
    ("maint_", "Maintenance"), ("proc_", "Procurement"), ("prob_", "HR / Probation"),
    ("users_", "Accounts / Users"), ("ppl_", "People"), ("wh_", "Warehouse"),
    ("shp_", "Shipping"), ("trc_", "Traceability"), ("pln_", "Planning"),
    ("cost_", "Costing"), ("plm_", "PLM"), ("cut_", "Cut room"),
    ("mes_", "Shop floor"), ("wsh_", "Wash"), ("qc_", "Quality"),
    ("cmp_", "Compliance"),
]

# The convention the platform actually follows.
CANONICAL_SUFFIX = {"view": "view", "manage": "change",
                    "approve": "approve", "admin": "admin"}

# Permissions whose suffix is NOT one of the four canonical ones. Every entry
# here is shown to the reader on the page as an explicit exception, so the RACI
# never silently guesses. A suffix absent from BOTH maps stays unclassified.
SUFFIX_EXCEPTIONS = {
    "entry": "change", "create": "change", "inspect": "change", "plan": "change",
    "store": "change", "technician": "change", "ticket_create": "change",
    "purchasing": "change", "evaluate": "change", "import": "change",
    "suspend": "change", "unlock": "change", "assign_role": "change",
    "initiate_password_reset": "change",
    "reject": "approve", "hr_review": "approve",
    "reports": "view", "view_security_audit": "view",
}


# The ten core platform permissions predate the convention entirely, so nothing
# can be derived from their names. Left unmapped they made the single most
# authority-dense row of the responsibility matrix — the one carrying
# access_admin and manage_users — render as four empty cells, which reads as
# "nobody administers this platform". Mapped by hand, and still listed as
# exceptions below so the reader sees that these are declared, not derived.
CORE_KIND = {
    "view_dashboard": "view", "open_module": "view", "view_reports": "view",
    "export_reports": "view", "view_system_health": "view",
    "manage_production": "change",
    "manage_users": "admin", "manage_settings": "admin",
    "manage_integrations": "admin", "access_admin": "admin",
}


def classify(perm):
    """(module, suffix, kind, canonical) for a permission name.
    kind is view|change|approve|admin|None; canonical says whether the
    _view/_manage/_approve/_admin convention held on its own."""
    for prefix, module in MODULE_PREFIXES:
        if perm.startswith(prefix):
            suffix = perm[len(prefix):]
            if suffix in CANONICAL_SUFFIX:
                return module, suffix, CANONICAL_SUFFIX[suffix], True
            return module, suffix, SUFFIX_EXCEPTIONS.get(suffix), False
    return "Platform (core)", perm, CORE_KIND.get(perm), False


def responsibility():
    """Per module: which roles can VIEW / CHANGE / APPROVE / ADMIN it, plus the
    permissions the convention could not classify."""
    roles = role_rows()
    modules, exceptions = {}, []
    for perm in PERMISSIONS:
        module, suffix, kind, canonical = classify(perm)
        entry = modules.setdefault(module, {"module": module, "perms": [],
                                            "view": {}, "change": {},
                                            "approve": {}, "admin": {},
                                            "unclassified": []})
        entry["perms"].append(perm)
        if not canonical:
            exceptions.append({"perm": perm, "module": module, "suffix": suffix,
                               "kind": kind})
        if kind is None:
            entry["unclassified"].append(perm)
            continue
        for r in roles:
            if cell(r, perm) != "no":
                entry[kind].setdefault(r["key"], r["label"])
    for entry in modules.values():
        for kind in ("view", "change", "approve", "admin"):
            entry[kind] = sorted(entry[kind].items())
    order = [m for _, m in MODULE_PREFIXES] + ["Platform (core)"]
    rows = [modules[m] for m in order if m in modules]
    return rows, exceptions


# ===========================================================================
# The rules currently buried inside individual modules
# ===========================================================================
def procurement_rules():
    """Ladder, thresholds, SoD state and the escalation chain. Every lookup is
    guarded: a build without the procurement module must not 500 this page."""
    out = {"ok": False}
    try:
        from app.approvals import constants as C
        from app.approvals import services as psvc
    except Exception:
        return out
    out["ok"] = True
    out["stages"] = [{
        "stage": s,
        "label": C.stage_label(s),
        "threshold": C.APPROVAL_MATRIX.get(s, 0),
        "roles": sorted(C.STAGE_ROLES.get(s, ())),
        "kind": "demand" if C.APPROVAL_MATRIX.get(s, 0) <= 0 else "value",
    } for s in C.LADDER]
    out["examples"] = [(amount, C.build_ladder(amount))
                       for amount in (5_000, 12_000, 48_000, 250_000)]
    out["rfq_min"] = C.RFQ_QUOTE_MIN
    out["rfq_threshold"] = C.RFQ_VALUE_THRESHOLD
    out["sla_hours"] = C.SLA_HOURS_PER_STAGE
    out["sod_constant"] = C.SOD_ADMIN_EXEMPT
    out["sod_live"] = C.SOD_ADMIN_EXEMPT
    out["escalation"] = dict(C.DEFAULT_ESCALATION)
    out["escalation_source"] = "constants"
    conn = get_db()
    try:
        try:
            out["sod_live"] = psvc.bool_setting(conn, "sod_admin_exempt")
        except Exception:
            conn.rollback()
        try:
            chain = psvc.escalation_map(conn)
            if chain:
                out["escalation"] = chain
                out["escalation_source"] = "proc_escalations"
        except Exception:
            conn.rollback()
    finally:
        conn.close()
    out["sod_overridden"] = out["sod_live"] != out["sod_constant"]
    return out


def maintenance_rules():
    """The maintenance spare-request approval ladder, straight out of
    mnt_approval_matrix."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT name, request_type, part_criticality, cost_threshold, levels, "
            "escalation_hours, active FROM mnt_approval_matrix ORDER BY id").fetchall()
    except Exception:
        conn.rollback()
        rows = []
    finally:
        conn.close()
    return [{"name": r["name"], "request_type": r["request_type"],
             "criticality": r["part_criticality"] or "any",
             "threshold": r["cost_threshold"],
             "levels": [x.strip() for x in (r["levels"] or "").split(",") if x.strip()],
             "escalation_hours": r["escalation_hours"],
             "active": bool(r["active"])} for r in rows]


# ===========================================================================
# Static source scans (memoised — a running worker's source cannot change)
# ===========================================================================
# Every way this codebase enforces a permission on a route. permission_required
# is the decorator; _require / _can are the local helpers in the maintenance
# blueprint; user_can is the inline check used in a handful of handlers; and
# has_permission / user_has_permission take the user first, so the permission is
# the SECOND argument (bi.jobs_run guards itself that way).
_GUARD_RE = re.compile(
    r"""(?:permission_required|require_permission|_require|_can|user_can)\s*\(\s*["']([a-z_]+)["']"""
    # greedy on purpose: the arg list cannot contain a bracket, so this lands on
    # the LAST quoted token in the call — the permission, not user["role"].
    r"""|(?:user_has_permission|has_permission)\s*\([^()]*["']([a-z_]+)["']""")
_REF_RE = re.compile(r"""["']([a-z][a-z_]*)["']""")

# Any call that performs an authorisation check, whatever permission it names.
# Used to recognise a route that delegates its check to a helper instead of
# spelling the permission out in a decorator.
_AUTHZ_CALL_RE = re.compile(
    r"(?:permission_required|require_permission|user_has_permission"
    r"|has_permission|user_can|require_role)\s*\(")
_CALLED_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_DENY_RE = re.compile(r"abort\(\s*40[13]")

_SCAN = {}


def _delegates_guard(fn, src):
    """True when the route DOES check a permission, just not with a literal
    permission name in a decorator. Two shapes, both real in this codebase:

      * it calls an authorisation function itself with a computed permission —
        the generic report hub filters its catalogue that way, so the page only
        ever lists reports the caller may open;
      * it calls a helper defined in the SAME module that both checks a
        permission AND refuses (aborts 401/403) — every /reporting/<key> route
        does this through _spec().

    The abort is required on the helper path on purpose: a helper that merely
    *reads* a permission to decide which tiles to render (main._factory_pulse)
    is not a gate, and clearing its callers would hide a genuinely open route.

    Without this, five correctly-guarded routes were reported as "no permission
    check", and a false positive on the most serious list of this page is worse
    than a missing one — it is the list an auditor acts on. One level deep, same
    module only: a guard further away than that genuinely is enforcement the
    reader cannot see from the route, and stays reported."""
    if _AUTHZ_CALL_RE.search(src):
        return True
    scope = getattr(fn, "__globals__", None) or {}
    own = getattr(fn, "__name__", "")
    for name in set(_CALLED_RE.findall(src)):
        if name == own:
            continue
        helper = scope.get(name)
        if not callable(helper) or getattr(helper, "__module__", None) != fn.__module__:
            continue
        try:
            hsrc = inspect.getsource(inspect.unwrap(helper))
        except Exception:
            continue
        if _AUTHZ_CALL_RE.search(hsrc) and _DENY_RE.search(hsrc):
            return True
    return False


def route_scan():
    """[{endpoint, rule, methods, perms, login, indirect}] for every registered
    route.

    Static: it reads each view function's own source (decorators included), plus
    one level into helpers defined in the same module (`indirect`). A route
    guarded only inside a service in ANOTHER module still reads as unguarded —
    which is exactly the finding an auditor wants raised, not hidden."""
    if "routes" in _SCAN:
        return _SCAN["routes"]
    rows = []
    for rule in current_app.url_map.iter_rules():
        view = current_app.view_functions.get(rule.endpoint)
        if view is None or rule.endpoint == "static":
            continue
        fn = inspect.unwrap(view)
        try:
            src = inspect.getsource(fn)
        except Exception:
            src = ""
        hits = {g for match in _GUARD_RE.findall(src) for g in match if g}
        perms = sorted(hits & set(PERMISSIONS))
        rows.append({
            "endpoint": rule.endpoint,
            "rule": str(rule),
            "methods": ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"})),
            "perms": perms,
            "login": "login_required" in src,
            "indirect": bool(src) and not perms and _delegates_guard(fn, src),
        })
    rows.sort(key=lambda r: (bool(r["perms"]), r["indirect"], r["login"], r["endpoint"]))
    _SCAN["routes"] = rows
    return rows


# The files that DECLARE the catalogue (the permission list, its labels and its
# descriptions). They mention every permission by definition, so counting them
# made "referenced elsewhere in the code" true for all 64 and the finding
# meaningless — it could never say "declared and then never used".
_CATALOGUE_FILES = {"security.py", "constants.py"}


def source_mentions():
    """{permission: True} for every permission whose name appears literally
    anywhere under app/ (python or template) OUTSIDE its own declaration. Used
    to tell a permission nobody references at all from one referenced only in UI
    code."""
    if "mentions" in _SCAN:
        return _SCAN["mentions"]
    found = set()
    wanted = set(PERMISSIONS)
    for root, dirs, files in os.walk(_APP_DIR):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "static")]
        for name in files:
            if not name.endswith((".py", ".html")) or name in _CATALOGUE_FILES:
                continue
            try:
                with open(os.path.join(root, name), encoding="utf-8") as fh:
                    text = fh.read()
            except Exception:
                continue
            for token in set(_REF_RE.findall(text)):
                if token in wanted:
                    found.add(token)
    _SCAN["mentions"] = found
    return found


# ===========================================================================
# Findings
# ===========================================================================
def findings():
    roles = role_rows()
    users = active_users()
    holders = role_holders(users)
    p_holders = permission_holders(users, roles)
    explicit = permission_holders(users, roles, via_star=False)

    granted = set()
    for r in roles:
        granted |= (set(PERMISSIONS) if r["star"] else r["perms"] & set(PERMISSIONS))

    # 1 — granted by a role, held by nobody active
    unheld = []
    # 1b — held ONLY through a blanket '*' grant. Technically someone can do it;
    # in governance terms nobody owns it, and the root account doing the work of
    # a business role is the hole an auditor is looking for.
    star_only = []
    for perm in PERMISSIONS:
        if perm not in granted:
            continue
        if p_holders.get(perm, 0) == 0:
            unheld.append({"perm": perm, "label": permission_label(perm),
                           "roles": sorted(r["key"] for r in roles
                                           if cell(r, perm) != "no")})
        elif explicit.get(perm, 0) == 0:
            star_only.append({"perm": perm, "label": permission_label(perm),
                              "holders": p_holders.get(perm, 0),
                              "roles": sorted(r["key"] for r in roles
                                              if not r["star"] and perm in r["perms"])})

    # 2 — roles with no active holder, and the single-holder continuity risk
    empty_roles = [{"key": r["key"], "label": r["label"], "custom": r["custom"],
                    "perms": 0 if r["star"] else len(r["perms"])}
                   for r in roles if holders.get(r["key"], 0) == 0]
    single_roles = [{"key": r["key"], "label": r["label"],
                     "user": next((u["username"] for u in users
                                   if u["role"] == r["key"]), "")}
                    for r in roles if holders.get(r["key"], 0) == 1]

    # 3 — dead authority: registered but no route enforces it
    enforced = set()
    for r in route_scan():
        enforced |= set(r["perms"])
    mentioned = source_mentions()
    dead = [{"perm": p, "label": permission_label(p),
             "mentioned": p in mentioned,
             "roles": sum(1 for r in roles if cell(r, p) != "no")}
            for p in PERMISSIONS if p not in enforced]

    # 4 — routes with no permission check at all (a route that delegates its
    # check to a helper in the same module is guarded, not open — see
    # _delegates_guard)
    open_routes = [r for r in route_scan() if not r["perms"] and not r["indirect"]]
    indirect_routes = [r for r in route_scan() if r["indirect"]]

    # 5 — extra_perms that grant beyond the user's role
    by_role = {r["key"]: r for r in roles}
    escalations = []
    for u in users:
        extra = _extra_list(u.get("extra_perms"))
        if not extra:
            continue
        role = by_role.get(u["role"])
        base = set(PERMISSIONS) if (role and role["star"]) else set(role["perms"] if role else ())
        beyond = sorted(p for p in extra if p not in base)
        if beyond:
            escalations.append({"username": u["username"],
                                "full_name": u.get("full_name") or "",
                                "role": u["role"],
                                "role_label": role["label"] if role else u["role"],
                                "beyond": beyond})

    return {"unheld": unheld, "star_only": star_only, "empty_roles": empty_roles,
            "single_roles": single_roles, "dead": dead, "open_routes": open_routes,
            "indirect_routes": indirect_routes, "escalations": escalations,
            "route_total": len(route_scan())}


def extra_perm_users():
    """Active users carrying a per-user grant — the view an auditor asks for
    first and the platform had nowhere to show."""
    roles = {r["key"]: r for r in role_rows()}
    out = []
    for u in active_users():
        extra = _extra_list(u.get("extra_perms"))
        if not extra:
            continue
        role = roles.get(u["role"])
        base = set(PERMISSIONS) if (role and role["star"]) else set(role["perms"] if role else ())
        out.append({"username": u["username"], "full_name": u.get("full_name") or "",
                    "role": u["role"], "role_label": role["label"] if role else u["role"],
                    "extra": extra,
                    "beyond": [p for p in extra if p not in base],
                    "redundant": [p for p in extra if p in base]})
    return out


# ===========================================================================
# Views
# ===========================================================================
def _tab(name, **ctx):
    return render_template(f"governance/{name}.html", active="governance",
                           tab=name, **ctx)


@bp.route("/")
@login_required
@permission_required("access_admin")
def index():
    roles = role_rows()
    catalogue = permission_catalogue()
    f_group = request.args.get("group") or ""
    f_role = request.args.get("role") or ""
    groups = [(g, items) for g, items in catalogue.items()
              if not f_group or g == f_group]
    shown = [r for r in roles if not f_role or r["key"] == f_role]
    holders = role_holders()
    return _tab("matrix", roles=roles, shown=shown, groups=groups,
                all_groups=list(catalogue.keys()), holders=holders,
                f_group=f_group, f_role=f_role, cell=cell,
                perm_desc=permission_desc,
                n_perms=len(PERMISSIONS), n_roles=len(roles))


@bp.route("/users")
@login_required
@permission_required("access_admin")
def users_view():
    rows = extra_perm_users()
    return _tab("users", rows=rows, perm_label=permission_label,
                n_users=len(active_users()))


@bp.route("/responsibility")
@login_required
@permission_required("access_admin")
def responsibility_view():
    modules, exceptions = responsibility()
    return _tab("responsibility", modules=modules, exceptions=exceptions)


@bp.route("/rules")
@login_required
@permission_required("access_admin")
def rules_view():
    users = active_users()
    holders = role_holders(users)
    roles = role_rows()
    return _tab("rules", proc=procurement_rules(), maint=maintenance_rules(),
                roles=roles, holders=holders,
                first_holder={r["key"]: next((u["username"] for u in users
                                              if u["role"] == r["key"]), "")
                              for r in roles})


@bp.route("/findings")
@login_required
@permission_required("access_admin")
def findings_view():
    return _tab("findings", f=findings())


# ===========================================================================
# Export — CSV + PDF, so the matrix can be signed and filed
# ===========================================================================
def export_dataset(key):
    """(headers, rows) for one governance dataset — the platform-wide export
    convention, so this reads the same way as the other twelve modules."""
    if key == "matrix":
        roles = role_rows()
        holders = role_holders()
        headers = ["role_key", "role_label", "type", "active_holders"] + list(PERMISSIONS)
        rows = []
        for r in roles:
            state = {"star": "*", "yes": "Y", "no": ""}
            rows.append([r["key"], r["label"], "custom" if r["custom"] else "built-in",
                         holders.get(r["key"], 0)]
                        + [state[cell(r, p)] for p in PERMISSIONS])
        return headers, rows

    if key == "extra_perms":
        headers = ["username", "full_name", "role", "role_label",
                   "extra_permission", "beyond_role"]
        rows = [[u["username"], u["full_name"], u["role"], u["role_label"], p,
                 "YES" if p in u["beyond"] else "no"]
                for u in extra_perm_users() for p in u["extra"]]
        return headers, rows

    if key == "responsibility":
        modules, exceptions = responsibility()
        headers = ["module", "can_view", "can_change", "can_approve", "can_administer",
                   "unclassified_permissions"]
        rows = [[m["module"],
                 "; ".join(k for k, _ in m["view"]),
                 "; ".join(k for k, _ in m["change"]),
                 "; ".join(k for k, _ in m["approve"]),
                 "; ".join(k for k, _ in m["admin"]),
                 "; ".join(m["unclassified"])] for m in modules]
        return headers, rows

    if key == "holders":
        holders = role_holders()
        headers = ["role_key", "role_label", "type", "active_holders", "risk"]
        rows = []
        for r in role_rows():
            n = holders.get(r["key"], 0)
            rows.append([r["key"], r["label"], "custom" if r["custom"] else "built-in", n,
                         "NO HOLDER" if n == 0 else ("single holder" if n == 1 else "")])
        return headers, rows

    if key == "findings":
        f = findings()
        headers = ["finding", "subject", "detail"]
        rows = []
        for x in f["unheld"]:
            rows.append(["permission granted but held by no active user", x["perm"],
                         "granted by: " + ", ".join(x["roles"])])
        for x in f["star_only"]:
            rows.append(["permission held only through a blanket '*' grant", x["perm"],
                         f"{x['holders']} holder(s), all via '*'; named on roles: "
                         + (", ".join(x["roles"]) or "none")])
        for x in f["empty_roles"]:
            rows.append(["role with no active users", x["key"],
                         f"{x['perms']} permissions granted"])
        for x in f["single_roles"]:
            rows.append(["role with a single holder (continuity risk)", x["key"], x["user"]])
        for x in f["dead"]:
            rows.append(["permission no route enforces", x["perm"],
                         ("referenced elsewhere in the code" if x["mentioned"]
                          else "not referenced anywhere") + f"; on {x['roles']} roles"])
        for x in f["open_routes"]:
            rows.append(["route with no permission check", x["rule"],
                         f"{x['endpoint']} [{x['methods']}] login_required="
                         + ("yes" if x["login"] else "NO")])
        for x in f["escalations"]:
            rows.append(["user extra_perms exceed their role", x["username"],
                         f"role {x['role']} + " + ", ".join(x["beyond"])])
        return headers, rows

    return None, None


_EXPORT_TITLES = {
    "matrix": "Role / Permission Matrix",
    "extra_perms": "Per-user Extra Permissions",
    "responsibility": "Responsibility Matrix by Module",
    "holders": "Role Holders",
    "findings": "Governance Findings",
}


@bp.route("/export/<key>.csv")
@login_required
@permission_required("access_admin")
def export_csv(key):
    headers, rows = export_dataset(key)
    if headers is None:
        abort(404)
    return csv_file(headers, rows, f"governance-{key}.csv")


@bp.route("/export/<key>.pdf")
@login_required
@permission_required("access_admin")
def export_pdf(key):
    headers, rows = export_dataset(key)
    if headers is None:
        abort(404)
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.platypus import (SimpleDocTemplate, Table, TableStyle,
                                        Paragraph, Spacer)
        from reportlab.lib.styles import getSampleStyleSheet
    except ImportError:
        abort(503)
    title = _EXPORT_TITLES.get(key, key)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4),
                            title=f"TC Platform - {title}")
    styles = getSampleStyleSheet()
    elems = [Paragraph(f"TC Platform &mdash; Governance: {title}", styles["Title"]),
             Paragraph("Reviewed by: ______________________   "
                       "Signature: ______________________   "
                       "Date: ______________", styles["Normal"]),
             Spacer(1, 8)]
    # The matrix is 60+ columns wide; a PDF cannot render that legibly, so the
    # PDF carries one row per role/permission grant and the CSV keeps the grid.
    if key == "matrix":
        headers = ["role_key", "role_label", "active_holders", "permission", "granted"]
        holders = role_holders()
        rows = [[r["key"], r["label"], holders.get(r["key"], 0), p,
                 "via *" if cell(r, p) == "star" else "yes"]
                for r in role_rows() for p in PERMISSIONS if cell(r, p) != "no"]
    data = [[str(h) for h in headers]]
    for r in rows[:1500]:
        data.append(["" if v is None else str(v) for v in r])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#07080b")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f6fa")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elems.append(table)
    doc.build(elems)
    buf.seek(0)
    return Response(buf.read(), mimetype="application/pdf",
                    headers={"Content-Disposition":
                             f"attachment; filename=governance-{key}.pdf"})
