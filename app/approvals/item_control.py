"""
TC Platform — the Item control rung (IT lead, 2026-11-26).

Requests from three departments get one extra signature AFTER the store
(warehouse) and BEFORE the Warehouse Manager: the department's own item
controller confirms the items and quantities before the store's manager signs.

  General Maintenance   -> one person
  Chemicals & Stones    -> another person
  Sewing & Cutting      -> a third person

Unlike every other rung it is signed by ASSIGNMENT, not by role: the person is
picked per department group on /procurement/item-control (proc_admin), stored in
proc_item_control, and copied onto the rung (pr_steps.assignee, with the group in
pr_steps.ic_group) when the request is submitted.

Chemicals (2026-11-28): a request with any CHM line (catalogue code or item text
carrying the token, e.g. "00-CHM-0001") is also signed by the Chemicals & Stones
controller, whatever its department. A General Maintenance request that orders a
chemical therefore carries TWO Item control rungs — the department's controller
first, then the chemicals controller — both after the store and before the
Warehouse Manager. Same group or same person -> one rung. Each rung is told
apart by its own assignee / ic_group.

Open requests (the ones submitted before a controller or the CHM rule existed)
get the rung through backfill(): run on every save of a group with an active
controller and from the settings page's "Add to open requests" button, BF_BATCH
requests per run (the button counts the rest). It never touches a signed rung. A
request already past the Warehouse Manager (owner, 2026-11-28) still gets it: the
rung goes in AT the current step, so the request waits for the controller and
then carries on with the rest of its ladder (a request on hold there is left for
a run after the hold is lifted). While a person is the
assignee of a pending Item control rung, act_on_step keeps them off the request's
other rungs that somebody else can sign, so their own rung stays signable.

A rung nobody may sign is never written: when the department has no active
controller, or the requester IS the controller (raising the request is their
signature, DOAM §3.4 — same rule as the HR Director rung), submit_pr leaves the
rung off and writes an 'item_control_skipped' note the request page shows.
"""
import logging
import re
import time
from datetime import datetime, timezone

from app.db import get_db
from app.approvals import constants as C

STAGE = C.ITEM_CONTROL_STAGE

# (group key, label, built-in department names). Matched after _norm(), so case,
# spaces, "&" / "and" and Arabic letter variants do not matter. Admins add more
# names per group on the settings page (proc_item_control.aliases).
GROUPS = (
    ("general_maintenance", "General Maintenance",
     ("General Maintenance", "Maintenance", "Gen. Maintenance", "الصيانة العامة", "الصيانة")),
    ("chemicals_stones", "Chemicals & Stones",
     ("Chemicals & Stones", "Chemical & Stones", "Chemicals & Stone", "Chemical & Stone",
      "Chemicals", "Stones", "الكيماويات والأحجار", "كيماويات وأحجار")),
    ("sewing_cutting", "Sewing & Cutting",
     ("Sewing & Cutting", "Cutting & Sewing", "Sewing", "Cutting", "Cut & Sew", "Sew & Cut",
      "Sewing / Cutting", "الخياطة والقص", "الخياطة", "القص")),
)
_BUILTIN = {k: names for k, _, names in GROUPS}
ALIAS_MAX = 20          # extra names per group
ALIAS_LEN = 60

DDL = """
CREATE TABLE IF NOT EXISTS proc_item_control (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_key TEXT UNIQUE NOT NULL,
    label TEXT,
    aliases TEXT,
    username TEXT,
    active INTEGER DEFAULT 1,
    updated_by TEXT, updated_at TEXT
);
"""

_ACTIVE_USER = "is_active=1 AND COALESCE(account_status,'active')='active'"
CHEM_GROUP = "chemicals_stones"
# CHM as a token: not inside a run of letters ("00-CHM-0001", "CHM 5" yes; "ACHME" no).
_CHM = re.compile(r"(?<![^\W\d_])CHM(?![^\W\d_])", re.I)
_log = logging.getLogger(__name__)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _f(row, key):
    """row[key] for a dict or a DB row; None when the column is absent."""
    if row is None:
        return None
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _norm(text):
    """Department name -> comparison key: lower case, Arabic letter variants and
    diacritics folded, '&' / '+' read as 'and', punctuation and spacing ignored."""
    t = str(text or "").lower()
    t = re.sub("[ً-ْـ]", "", t)
    t = t.translate(str.maketrans("أإآةى", "اااهي"))
    t = t.replace("&", " and ").replace("+", " and ")
    return " ".join(re.sub(r"[\W_]+", " ", t).split())


def _split(aliases):
    return [a.strip() for a in re.split(r"[,\n;]", str(aliases or "")) if a.strip()]


def ensure_schema(conn):
    """Create the table and seed the three groups (unassigned). Additive and
    idempotent: an existing row — an admin's assignment — is never touched."""
    try:
        conn.executescript(DDL)
        conn.commit()
        have = {r["group_key"] for r in conn.execute(
            "SELECT group_key FROM proc_item_control").fetchall()}
        for key, label, _ in GROUPS:
            if key not in have:
                conn.execute("INSERT INTO proc_item_control (group_key, label, aliases, active) "
                             "VALUES (?,?,?,1)", (key, label, ""))
        conn.commit()
    except Exception:                                        # noqa: BLE001
        conn.rollback()


def rows(conn):
    """Every department group, in seed order, as dicts. [] before the migration."""
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM proc_item_control ORDER BY id").fetchall()]
    except Exception:                                        # noqa: BLE001
        conn.rollback()     # PostgreSQL aborts the transaction on a failed read
        return []


def _names(row):
    return ({_norm(row.get("label"))} | {_norm(n) for n in _BUILTIN.get(row["group_key"], ())}
            | {_norm(a) for a in _split(row.get("aliases"))}) - {""}


def group_for(conn, department, _rows=None):
    """The group row whose names include `department`, or None."""
    key = _norm(department)
    if not key:
        return None
    return next((r for r in (rows(conn) if _rows is None else _rows) if key in _names(r)), None)


def _active(conn, username):
    if not username:
        return None
    return conn.execute("SELECT username, full_name FROM users WHERE LOWER(username)=LOWER(?) "
                        "AND " + _ACTIVE_USER, (username,)).fetchone()


def is_chm(text):
    """True when `text` carries the CHM token (a chemicals item code)."""
    return bool(_CHM.search(str(text or "")))


def _chm_ids(conn, pr_id=None):
    """Ids of requests with a CHM line — one request, or every pending one."""
    where, args = ("i.pr_id=?", [pr_id]) if pr_id else (
        "i.pr_id IN (SELECT id FROM pr_requests WHERE status='pending')", [])
    return {r["pr_id"] for r in conn.execute(
        "SELECT i.pr_id, i.item, p.code FROM pr_items i LEFT JOIN proc_items p ON p.id=i.item_id "
        "WHERE " + where + " AND (UPPER(COALESCE(p.code,'')) LIKE ? OR UPPER(COALESCE(i.item,'')) "
        "LIKE ?)", args + ["%CHM%", "%CHM%"]).fetchall() if is_chm(r["code"]) or is_chm(r["item"])}


def has_chm_items(conn, pr_id):
    """Does any line of this request link to a CHM catalogue item or name one?"""
    return bool(pr_id) and pr_id in _chm_ids(conn, pr_id)


def _resolve_group(conn, g, requester):
    if not g.get("active") or not g.get("username"):
        return g, None, "unassigned"
    u = _active(conn, g["username"])
    if not u:
        return g, None, "inactive"
    if u["username"].lower() == str(requester or "").lower():
        return g, None, "requester"
    return g, u["username"], ""


def resolve_all(conn, department, requester, pr_id=None):
    """[group, assignee, why] per Item control rung this request gets, in ladder
    order: the department's group (resolve()), then Chemicals & Stones when a line
    is a CHM item. Same group, or the same person, -> one entry."""
    g, who, why = resolve(conn, department, requester)
    out = [[g, who, why]] if g else []
    if (pr_id and not (g and g["group_key"] == CHEM_GROUP)
            and has_chm_items(conn, pr_id)):
        c = next((r for r in rows(conn) if r["group_key"] == CHEM_GROUP), None)
        if c:
            _, cw, cy = _resolve_group(conn, c, requester)
            if not (cw and who and cw.lower() == who.lower()):
                out.append([c, cw, cy])
    return out


def resolve(conn, department, requester):
    """(group, assignee, why) for a request about to be submitted.

    why: ''               -> the rung applies, `assignee` signs it;
         'not_controlled' -> the department is not one of the groups (no rung);
         'unassigned'     -> a controlled department with nobody assigned (or the
                             group switched off);
         'inactive'       -> the assigned account is disabled;
         'requester'      -> the requester IS the controller."""
    g = group_for(conn, department)
    if not g:
        return None, None, "not_controlled"
    return _resolve_group(conn, g, requester)


SKIP_NOTES = {
    "unassigned": "Item control skipped: no item controller is assigned for %s. "
                  "A procurement admin can assign one under Procurement > Item control.",
    "inactive": "Item control skipped: the item controller assigned for %s has a disabled "
                "account. A procurement admin can assign another person.",
    "requester": "Item control skipped: the requester is the item controller for %s — "
                 "raising the request is their signature.",
    "dual_role": "Item control skipped (dual role): the item controller for %s is the only "
                 "person who can sign an earlier step of this request, and one person may "
                 "not sign two steps.",
}


def skip_note(group, why):
    return SKIP_NOTES[why] % (group.get("label") or group.get("group_key"))


def insert_stage(stages):
    """`stages` with the rung in its LADDER_RANK place (after the store)."""
    out = [s for s in stages if s != STAGE]
    rank = C.ladder_rank(STAGE)
    at = next((i for i, s in enumerate(out) if C.ladder_rank(s) > rank), len(out))
    return out[:at] + [STAGE] + out[at:]


def expand_rungs(rungs, n):
    """`rungs` with n Item control rungs instead of one (rungs_from_stages keeps a
    stage once): the extra ones follow the first, one stage each."""
    if n <= 1:
        return rungs
    out = []
    for r in rungs:
        out.append(r)
        if STAGE in r:
            out.extend([[STAGE] for _ in range(n - 1)])
    return out


def delegates(conn, username):
    """Active accounts the controller delegated their signing to, today."""
    if not username:
        return set()
    today = _now()[:10]
    return {r["to_user"] for r in conn.execute(
        "SELECT d.to_user FROM proc_delegations d JOIN users u ON u.username=d.to_user "
        "WHERE LOWER(d.from_user)=LOWER(?) AND d.is_active=1 AND u.is_active=1 "
        "AND COALESCE(u.account_status,'active')='active' "
        "AND (d.from_date IS NULL OR d.from_date<=?) AND (d.to_date IS NULL OR d.to_date>=?)",
        (username, today, today)).fetchall()}


def delegators(username):
    """Lower-cased usernames who delegated their signing to `username` today —
    one query, so a queue can test every item-control rung without a lookup each."""
    if not username:
        return set()
    today = _now()[:10]
    conn = get_db()
    try:
        return {str(r["from_user"]).lower() for r in conn.execute(
            "SELECT from_user FROM proc_delegations WHERE LOWER(to_user)=LOWER(?) "
            "AND is_active=1 AND (from_date IS NULL OR from_date<=?) "
            "AND (to_date IS NULL OR to_date>=?)", (username, today, today)).fetchall()}
    finally:
        conn.close()


def signers(conn, assignee):
    """Who may sign a rung assigned to `assignee`: them (if active) + delegates."""
    if not assignee:
        return set()
    u = _active(conn, assignee)
    return ({u["username"]} if u else set()) | delegates(conn, assignee)


def can_sign(user, step, _deleg=None, _ic_from=None):
    """May `user` sign this item-control step? The assignee, an active delegate
    of the assignee, or super_admin (in place, audited by act_on_step). Needs no
    proc_approve: the assignment IS the authority. `_deleg` is the caller's
    prefetched active_delegator_roles(user); an empty set means the user holds
    no delegation at all, so the lookup is skipped. `_ic_from` is a prefetched
    delegators(user): when given, no query runs at all."""
    if not user or (user.get("account_status") or "active") != "active":
        return False
    if user.get("role") == "super_admin":
        return True
    assignee = str(_f(step, "assignee") or "")
    un = str(user.get("username") or "")
    if not assignee or not un:
        return False
    if un.lower() == assignee.lower():
        return True
    if _ic_from is not None:
        return assignee.lower() in _ic_from
    if _deleg is not None and not _deleg:
        return False
    conn = get_db()
    try:
        return un.lower() in {d.lower() for d in delegates(conn, assignee)}
    finally:
        conn.close()


def can_view(user, pr_id):
    """The controller (and whoever signed the rung) may open the request even when
    the requester-scope rule would hide it from them."""
    un = (user or {}).get("username")
    if not un or not pr_id:
        return False
    conn = get_db()
    try:
        return bool(conn.execute(
            "SELECT 1 FROM pr_steps WHERE pr_id=? AND stage=? AND (LOWER(COALESCE(assignee,''))"
            "=LOWER(?) OR LOWER(COALESCE(approver_user,''))=LOWER(?)) LIMIT 1",
            (pr_id, STAGE, un, un)).fetchone())
    except Exception:                                        # noqa: BLE001
        conn.rollback()
        return False
    finally:
        conn.close()


# The access hook asks this on every request of a user without proc_view, so the
# (tiny) set is cached rather than queried each time.
# shortcut: cached 30 s per worker, so another worker sees a new assignment up to
# 30 s late; move to a shared cache if assignments ever need to apply instantly.
_CTL_TTL = 30.0
_CTL_CACHE = [0.0, frozenset()]


def _controllers():
    if time.monotonic() - _CTL_CACHE[0] > _CTL_TTL:
        conn = get_db()
        try:
            # current group owners + whoever still holds a waiting rung (one kept
            # on reassignment), so neither loses the request page.
            names = frozenset(str(r["username"]).lower() for r in conn.execute(
                "SELECT username FROM proc_item_control WHERE active=1 "
                "AND username IS NOT NULL UNION SELECT assignee FROM pr_steps "
                "WHERE stage=? AND status='pending' AND assignee IS NOT NULL",
                (STAGE,)).fetchall())
        except Exception:                                    # noqa: BLE001
            conn.rollback()
            names = frozenset()
        finally:
            conn.close()
        _CTL_CACHE[:] = [time.monotonic(), names]
    return _CTL_CACHE[1]


def is_controller(username):
    """True when `username` is assigned to an active department group or holds a
    waiting item-control rung."""
    return bool(username) and str(username).lower() in _controllers()


def signable_now(user, pr_ids):
    """True when one of `pr_ids` is waiting on an item-control rung `user` may sign."""
    ids = [int(p) for p in pr_ids if str(p).isdigit()][:50]
    if not ids:
        return False
    conn = get_db()
    try:
        ph = ",".join("?" * len(ids))
        steps = conn.execute(
            "SELECT s.* FROM pr_steps s JOIN pr_requests r ON r.id=s.pr_id "
            "WHERE s.pr_id IN (%s) AND s.stage=? AND s.status='pending' "
            "AND r.status='pending' AND s.seq=r.current_seq" % ph,
            tuple(ids) + (STAGE,)).fetchall()
    except Exception:                                        # noqa: BLE001
        conn.rollback()
        steps = []
    finally:
        conn.close()
    return any(can_sign(user, s) for s in steps)


def view(lang="en"):
    """Rows for the settings page, with the assignee's display name."""
    conn = get_db()
    try:
        out = rows(conn)
        for r in out:
            u = _active(conn, r.get("username"))
            r["user_name"] = (u["full_name"] or u["username"]) if u else None
            r["user_inactive"] = bool(r.get("username")) and not u
            r["builtin"] = list(_BUILTIN.get(r["group_key"], ()))
            r["alias_list"] = _split(r.get("aliases"))
            r["pending"] = 0
        pend = conn.execute(
            "SELECT r.department, s.ic_group FROM pr_steps s JOIN pr_requests r ON r.id=s.pr_id "
            "WHERE s.stage=? AND s.status='pending' AND r.status='pending'", (STAGE,)).fetchall()
        for p in pend:
            g = _rung_group(conn, p, out)
            if g:
                g["pending"] += 1
        for r in out:     # bf_n None = nobody may sign the rung: no button
            bf = backfill(conn, r, None, dry_run=True) if r.get("active") and r["user_name"] else None
            r["bf_n"] = None if bf is None else sum(1 for x in bf if x["result"] == "would_add")
        return out
    except Exception:                                        # noqa: BLE001
        conn.rollback()
        return []
    finally:
        conn.close()


def save(group_key, username, aliases, active, actor, ip=None):
    """Assign a group's controller / names / on-off. Returns (ok, code).
    Re-points the group's PENDING rungs to the new person, so a request is not
    left waiting on somebody who has moved on (signed rungs are history and are
    never touched)."""
    from app.approvals import services as svc
    username = str(username or "").strip()
    names = []
    for a in _split(aliases):
        a = a[:ALIAS_LEN]
        if _norm(a) and _norm(a) not in {_norm(x) for x in names}:
            names.append(a)
    if len(names) > ALIAS_MAX:
        return False, "too_many_aliases"
    conn = get_db()
    try:
        all_rows = rows(conn)
        g = next((r for r in all_rows if r["group_key"] == group_key), None)
        if not g:
            return False, "unknown_group"
        taken = set().union(*[_names(r) for r in all_rows if r["group_key"] != group_key])
        if any(_norm(a) in taken for a in names):
            return False, "alias_taken"
        before = g.get("username") or ""
        if username and username.lower() == before.lower():
            username = before          # unchanged: an alias-only save keeps even a disabled one
        elif username:
            u = _active(conn, username)
            if not u:
                return False, "bad_user"
            username = u["username"]
        # Unassigning or switching off would strand the rungs already waiting on
        # this person (nobody else may sign them): hand them over first.
        if before and (not username or not active) and any(
                str(p["assignee"] or "").lower() == before.lower() for p in _waiting(conn, g)):
            return False, "pending_waiting"
        who = (actor or {}).get("username") or "system"
        conn.execute("UPDATE proc_item_control SET username=?, aliases=?, active=?, "
                     "updated_by=?, updated_at=? WHERE id=?",
                     (username or None, ", ".join(names), 1 if active else 0, who, _now(), g["id"]))
        svc._wf_audit(conn, who, "item_control_set",
                      "%s: %s -> %s%s; names: %s" % (g["label"], before or "—", username or "—",
                                                     "" if active else " (switched off)",
                                                     ", ".join(names) or "—"), ip)
        if username and active and username.lower() != before.lower():
            _repoint(conn, dict(g, aliases=", ".join(names)), username, who, ip)
        conn.commit()
        _CTL_CACHE[0] = 0.0                     # this worker sees it at once
        if username and active:
            # the open requests of this group get the rung too (each its own
            # transaction); the assignment itself is already saved either way
            try:
                backfill(conn, next(r for r in rows(conn) if r["group_key"] == group_key),
                         who, ip)
            except Exception:                                # noqa: BLE001
                conn.rollback()
                _log.exception("item control backfill after save failed (%s)", group_key)
        return True, "saved"
    finally:
        conn.close()


def _rung_group(conn, step, groups):
    """The group (from `groups`) an Item control rung belongs to: its ic_group, or
    for a rung written before that column, the request's department group."""
    key = _f(step, "ic_group")
    if key:
        return next((g for g in groups if g["group_key"] == key), None)
    return group_for(conn, _f(step, "department"), _rows=groups)


def _waiting(conn, group):
    """Pending item-control rungs of this group on pending requests."""
    return [p for p in conn.execute(
        "SELECT s.id, s.seq, s.assignee, s.ic_group, r.id AS pr_id, r.pr_no, r.title, "
        "r.requester, r.department, r.current_seq FROM pr_steps s JOIN pr_requests r "
        "ON r.id=s.pr_id WHERE s.stage=? AND s.status='pending' AND r.status='pending'",
        (STAGE,)).fetchall()
        if _rung_group(conn, p, [group])]


def _repoint(conn, group, username, actor, ip=None):
    """Move this group's waiting rungs to `username` (not on a request they raised)."""
    from app.approvals import services as svc
    for p in _waiting(conn, group):
        if str(p["requester"] or "").lower() == username.lower():
            svc.audit(conn, p["pr_id"], actor, "item_control_kept",
                      "Item control not moved to %s: they raised this request." % username, ip)
            continue
        if conn.execute("SELECT 1 FROM pr_steps WHERE pr_id=? AND LOWER(COALESCE(approver_user,''))"
                        "=LOWER(?) AND status IN ('approved','rejected') LIMIT 1",
                        (p["pr_id"], username)).fetchone():
            svc.audit(conn, p["pr_id"], actor, "item_control_kept",
                      "Item control not moved to %s: they already signed another step of this "
                      "request, and one person may not sign two steps." % username, ip)
            continue
        if conn.execute("SELECT 1 FROM pr_steps WHERE pr_id=? AND stage=? AND id<>? AND "
                        "LOWER(COALESCE(assignee,''))=LOWER(?) LIMIT 1",
                        (p["pr_id"], STAGE, p["id"], username)).fetchone():
            svc.audit(conn, p["pr_id"], actor, "item_control_kept",
                      "Item control not moved to %s: they already hold the other Item control "
                      "step of this request, and one person may not sign two steps." % username, ip)
            continue
        conn.execute("UPDATE pr_steps SET assignee=? WHERE id=?", (username, p["id"]))
        svc.audit(conn, p["pr_id"], actor, "item_control_reassigned",
                  "Item control moved from %s to %s." % (p["assignee"] or "—", username), ip)
        if p["seq"] == p["current_seq"]:
            svc.notify_users(conn, [username], "warning", "New request to sign",
                             "%s (%s) needs your Item control approval."
                             % (p["pr_no"], p["title"] or ""), link=svc._pr_link(p["pr_id"]))


def preview_names(username=None):
    """Normalised department names that currently get the rung — for the
    new-request ladder preview. A group the viewer controls is left out: their
    own request skips it."""
    conn = get_db()
    try:
        out = []
        for r in rows(conn):
            if not r.get("active") or not r.get("username"):
                continue
            if username and r["username"].lower() == str(username).lower():
                continue
            if _active(conn, r["username"]):
                out.extend(sorted(_names(r)))
        return out
    finally:
        conn.close()


def picker_users():
    """(username, name) of every account that may be assigned (active, not suspended)."""
    conn = get_db()
    try:
        return [(r["username"], r["full_name"] or r["username"]) for r in conn.execute(
            "SELECT username, full_name FROM users WHERE " + _ACTIVE_USER +
            " ORDER BY LOWER(COALESCE(full_name, username))").fetchall()]
    finally:
        conn.close()


def alert_no_signer(conn, pr):
    """Proc admins: this request waits at Item control and its assignee cannot sign
    (account disabled). The fix is a new assignee on the settings page, which moves
    the waiting rungs — not a role holder or a delegation."""
    from app.approvals import services as svc
    svc.notify_users(conn, svc._proc_admins(conn), "critical", "Approval rung has no signer",
                     "%s is waiting at Item control, but its assigned item controller cannot "
                     "sign. Assign another person under Procurement > Item control: the "
                     "waiting requests move to them." % pr["pr_no"],
                     link="/procurement/item-control")


def assignee_name(username):
    if not username:
        return ""
    conn = get_db()
    try:
        u = conn.execute("SELECT full_name, username FROM users WHERE LOWER(username)=LOWER(?)",
                         (username,)).fetchone()
        return (u["full_name"] or u["username"]) if u else username
    finally:
        conn.close()


def skip_info(pr_id):
    """[{why, group}] for each rung the latest submission of this request left off
    (the 'item_control_skipped' notes are written just before 'submitted'); [] when
    none was."""
    conn = get_db()
    try:
        ev = conn.execute("SELECT detail, action FROM pr_events WHERE pr_id=? AND action IN "
                          "('item_control_skipped','submitted') ORDER BY id DESC LIMIT 3",
                          (pr_id,)).fetchall()
        if not ev or ev[0]["action"] != "submitted":
            return []
        notes = []
        for e in ev[1:]:
            if e["action"] != "item_control_skipped":
                break
            notes.insert(0, e["detail"] or "")
        if not notes:
            return []
        groups = rows(conn)
        pr = conn.execute("SELECT department FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        dept = group_for(conn, pr["department"] if pr else "", _rows=groups)
        # a rung added since (backfill) answers that note
        have = {(s["ic_group"] or (dept or {}).get("group_key")) for s in conn.execute(
            "SELECT ic_group FROM pr_steps WHERE pr_id=? AND stage=?", (pr_id, STAGE)).fetchall()}
    finally:
        conn.close()
    out = []
    for note in notes:
        why = next((k for k, v in SKIP_NOTES.items() if note.startswith(v.split("%s")[0])),
                   "unassigned")
        g = next((r for r in groups if (" %s" % r.get("label")) in note), dept)
        if not (g and g["group_key"] in have):
            out.append({"why": why, "group": g["group_key"] if g else ""})
    return out


# Why backfill() left a request out (shown on the settings page).
BF_REASONS = {
    "requester": "The requester is this group's item controller.",
    "signed": "The item controller already signed another step of this request.",
    "dual_role": "The item controller is the only person who can sign another step of it.",
    "held": "On hold. It is added after the hold is lifted: press the button again then.",
    "error": "Could not be updated; try again.",
}


def _plan(conn, pr, steps, g, who, groups, cache):
    """What backfill() would do to one pending request: None when it already has
    this group's rung (or one for this person), else (why, anchor) — why '' means
    add the rung in front of `anchor` (None = after the last rung)."""
    from app.approvals import services as svc
    key, wl = g["group_key"], who.lower()
    dept = group_for(conn, pr["department"], _rows=groups)
    dept_key = dept["group_key"] if dept else None
    for s in steps:
        if s["stage"] == STAGE and ((s.get("ic_group") or dept_key) == key
                                    or str(s.get("assignee") or "").lower() == wl):
            return None
    if str(pr["requester"] or "").lower() == wl:
        return "requester", None
    if any(str(s.get("approver_user") or "").lower() == wl
           and s["status"] in ("approved", "rejected") for s in steps):
        return "signed", None
    rank, cur = C.ladder_rank(STAGE), pr["current_seq"] or 0
    # in front of the first rung still to come that ranks above it (the Warehouse
    # Manager, or the current rung once that one has signed: owner, 2026-11-28);
    # the department's controller signs before the chemicals one
    anchor = next((s for s in steps if s["status"] == "pending" and s["seq"] >= cur and (
        C.ladder_rank(s["stage"]) > rank or (key != CHEM_GROUP and s["stage"] == STAGE
                                             and s.get("ic_group") == CHEM_GROUP))), None)
    if any(s["status"] == "pending" and svc._step_signers(conn, pr, s, cache) == {who}
           for s in steps):
        return "dual_role", None
    # a held request at the current step: only the current rung's signers may lift
    # the hold, so putting the controller there would strand it
    if anchor and anchor["seq"] == cur and (svc._pr_field(pr, "hold_reason") or "").strip():
        return "held", None
    return "", anchor


def _insert(conn, pr, steps, anchor, g, who, actor, ip):
    """Write the rung in front of `anchor` (shifting it and every later rung one
    seq on) or after the last rung. Returns True when it is now the current rung.
    Signed rungs sit below current_seq <= anchor seq, so the shift never moves one."""
    from app.approvals import services as svc
    pid, cur = pr["id"], pr["current_seq"] or 0
    if anchor:
        seq = anchor["seq"]
        conn.execute("UPDATE pr_steps SET seq=seq+1 WHERE pr_id=? AND seq>=? AND status='pending'",
                     (pid, seq))
    else:
        seq = max([s["seq"] for s in steps] or [0]) + 1
    now = svc._now()
    is_cur = seq == cur
    if is_cur:      # the rung that was waiting waits again later: restart its clock
        conn.execute("UPDATE pr_steps SET activated_at=NULL, escalated=0, escalated_at=NULL, "
                     "escalated2_at=NULL WHERE pr_id=? AND seq=? AND status='pending'",
                     (pid, seq + 1))
    conn.execute(
        "INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, activated_at, "
        "created_at, origin, assignee, ic_group) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (pid, seq, STAGE, "pending", svc.stage_label(STAGE), now if is_cur else None, now,
         "ladder", who, g["group_key"]))
    svc.stamp_step_actions(conn, pid)
    late = any(s["status"] == "approved" and C.ladder_rank(s["stage"]) > C.ladder_rank(STAGE)
               for s in steps)
    svc.audit(conn, pid, actor or "system", "item_control_added",
              ("Item control for %s added to this open request after the Warehouse Manager "
               "had already signed: %s must sign it before the request continues%s." if late else
               "Item control for %s added to this open request: %s signs it after the store "
               "and before the Warehouse Manager%s.") % (
                  g.get("label") or g["group_key"], who,
                  "; the request now waits for their signature" if is_cur else ""), ip)
    if is_cur:
        svc._clear_sign_prompts(conn, pr, [s for s in steps if s["seq"] == seq
                                           and s["status"] == "pending"])
        svc._prompt_rung(conn, pr, conn.execute(     # via _step_notify: an away controller's delegate
            "SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND stage=?", (pid, seq, STAGE)).fetchall(),
            "%s (%s) needs your Item control approval." % (pr["pr_no"], pr["title"] or ""))
    return is_cur


BF_BATCH = 20   # adds per call: each costs ~20 round trips, and it runs inside a web request


def backfill(conn, group_row, actor, ip=None, dry_run=False, limit=BF_BATCH):
    """Add this group's Item control rung to the OPEN requests that should carry
    it — pending ones of the group's departments, and for Chemicals & Stones every
    pending one with a CHM line — but do not yet. Drafts get it at submit; closed
    requests are history. Idempotent; each request is its own transaction, so the
    caller commits its own work first.

    Returns [{pr_id, pr_no, result: 'added'|'would_add'|'skipped', why, current}]
    (no entry for a request that already has the rung). [] when the group has no
    active controller: nobody could sign the rung. Stops after `limit` adds (None =
    all); the rest are what the next dry run counts."""
    g = dict(group_row)
    u = _active(conn, g.get("username")) if g.get("active") else None
    if not u:
        return []
    who, key = u["username"], g["group_key"]
    groups = rows(conn)
    chm = _chm_ids(conn) if key == CHEM_GROUP else set()
    cand = [dict(r) for r in conn.execute(
        "SELECT id, pr_no, title, requester, department, current_seq, status, hold_reason "
        "FROM pr_requests WHERE status='pending' ORDER BY id").fetchall()
        if r["id"] in chm or group_for(conn, r["department"], _rows=[g])]
    steps = {}
    for i in range(0, len(cand), 500):
        part = [p["id"] for p in cand[i:i + 500]]
        for s in conn.execute("SELECT * FROM pr_steps WHERE pr_id IN (%s) ORDER BY seq, id"
                              % ",".join("?" * len(part)), tuple(part)).fetchall():
            steps.setdefault(s["pr_id"], []).append(dict(s))
    cache, out, added = {}, [], 0
    for pr in cand:
        plan = _plan(conn, pr, steps.get(pr["id"], []), g, who, groups, cache)
        if plan is None:
            continue
        res = {"pr_id": pr["id"], "pr_no": pr["pr_no"], "why": plan[0],
               "result": "skipped" if plan[0] else ("would_add" if dry_run else "added"),
               "current": bool(plan[1]) and plan[1]["seq"] == pr["current_seq"]}
        if plan[0] or dry_run:
            out.append(res)
            continue
        if limit is not None and added >= limit:
            break                   # the rest: the next press (the button shows how many)
        try:
            # claim the request first (PG row lock / SQLite write lock): a signature
            # or another backfill landing between the re-read and the write would
            # otherwise shift the ladder from a stale snapshot
            if conn.execute("UPDATE pr_requests SET current_seq=current_seq WHERE id=? AND "
                            "status='pending'", (pr["id"],)).rowcount != 1:
                conn.rollback()
                continue
            fresh = dict(conn.execute("SELECT * FROM pr_requests WHERE id=?",
                                      (pr["id"],)).fetchone())
            st = [dict(s) for s in conn.execute(
                "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pr["id"],)).fetchall()]
            plan = _plan(conn, fresh, st, g, who, groups, cache)
            if plan is None or plan[0]:
                conn.rollback()                              # release the claim
                if plan is None:
                    continue
                res.update(result="skipped", why=plan[0])
            else:
                res["current"] = _insert(conn, fresh, st, plan[1], g, who, actor, ip)
                n = conn.execute("SELECT COUNT(*) AS n FROM pr_steps WHERE pr_id=? AND stage=? "
                                 "AND LOWER(COALESCE(assignee,''))=LOWER(?)",
                                 (pr["id"], STAGE, who)).fetchone()["n"]
                now_at = conn.execute("SELECT status FROM pr_steps WHERE pr_id=? AND seq=?",
                                      (pr["id"], fresh["current_seq"])).fetchall()
                if n != 1 or not now_at or any(r["status"] != "pending" for r in now_at):
                    raise RuntimeError("ladder check failed after insert")
                conn.commit()
                added += 1
        except Exception:                                    # noqa: BLE001
            conn.rollback()
            _log.exception("item control backfill failed on request %s", pr["id"])
            res.update(result="skipped", why="error")
        out.append(res)
    return out


def still_to_add(group_key):
    """How many open requests the next backfill run of this group would add to."""
    return sum(1 for r in run_backfill(group_key, None, dry_run=True) or ()
               if r["result"] == "would_add")


def run_backfill(group_key, actor, ip=None, dry_run=False, limit=BF_BATCH):
    """backfill() for one group by key, on its own connection. None = no such group."""
    conn = get_db()
    try:
        g = next((r for r in rows(conn) if r["group_key"] == group_key), None)
        if not g:
            return None
        return backfill(conn, g, (actor or {}).get("username") or "system", ip, dry_run, limit)
    finally:
        conn.close()


# Settings-page messages, merged into messages.MESSAGES (translated flash()).
MESSAGES = {
    "Item control saved.": {
        "ar": "تم حفظ مراقبة الأصناف.",
        "tr": "Malzeme kontrolü kaydedildi."},
    "Pick an active user as the item controller.": {
        "ar": "اختار مستخدم شغّال كمسؤول مراقبة الأصناف.",
        "tr": "Malzeme kontrol sorumlusu olarak aktif bir kullanıcı seçin."},
    "One of those department names already belongs to another group.": {
        "ar": "واحد من أسماء الأقسام دي تبع مجموعة تانية بالفعل.",
        "tr": "Bu departman adlarından biri zaten başka bir gruba ait."},
    "Up to 20 extra department names per group.": {
        "ar": "أقصى حاجة 20 اسم قسم إضافي لكل مجموعة.",
        "tr": "Grup başına en fazla 20 ek departman adı."},
    "Requests are waiting on this person at Item control. Pick another person to take them over first.": {
        "ar": "فيه طلبات مستنية الشخص ده في مراقبة الأصناف. اختار حد تاني ياخدها الأول.",
        "tr": "Malzeme kontrolünde bu kişiyi bekleyen talepler var. Önce bunları devralacak başka birini seçin."},
    "That user is the Item control signer of this request, so they cannot become its requester.": {
        "ar": "المستخدم ده هو اللي هيوقّع مراقبة الأصناف على الطلب ده، فمينفعش يبقى مقدّم الطلب.",
        "tr": "Bu kullanıcı bu talebin Malzeme Kontrolü imzacısı, bu yüzden talep sahibi olamaz."},
    "That department group does not exist.": {
        "ar": "مجموعة الأقسام دي مش موجودة.",
        "tr": "Bu departman grubu yok."},
    "Item control still has to be added to more open requests. Press \"Add to open requests\" again. Still to add:": {
        "ar": "لسه فيه طلبات مفتوحة محتاجة تتضاف لها مراقبة الأصناف. دوس \"ضيفها للطلبات المفتوحة\" تاني. لسه فاضل:",
        "tr": "Malzeme kontrolü daha fazla açık talebe eklenmeli. \"Açık taleplere ekle\" düğmesine tekrar basın. Eklenecek kalan:"},
    "Item control added to open requests. Added / skipped:": {
        "ar": "اتضافت مراقبة الأصناف للطلبات المفتوحة. اتضاف / اتساب:",
        "tr": "Malzeme kontrolü açık taleplere eklendi. Eklenen / atlanan:"},
}
