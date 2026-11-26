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
proc_item_control, and copied onto the rung (pr_steps.assignee) when the request
is submitted. Only NEW submissions get the rung; a request already in the ladder
keeps the ladder it was built with.

A rung nobody may sign is never written: when the department has no active
controller, or the requester IS the controller (raising the request is their
signature, DOAM §3.4 — same rule as the HR Director rung), submit_pr leaves the
rung off and writes an 'item_control_skipped' note the request page shows.
"""
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
    if not g.get("active") or not g.get("username"):
        return g, None, "unassigned"
    u = _active(conn, g["username"])
    if not u:
        return g, None, "inactive"
    if u["username"].lower() == str(requester or "").lower():
        return g, None, "requester"
    return g, u["username"], ""


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
            "SELECT r.department FROM pr_steps s JOIN pr_requests r ON r.id=s.pr_id "
            "WHERE s.stage=? AND s.status='pending' AND r.status='pending'", (STAGE,)).fetchall()
        for p in pend:
            g = group_for(conn, p["department"], _rows=out)
            if g:
                g["pending"] += 1
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
        return True, "saved"
    finally:
        conn.close()


def _waiting(conn, group):
    """Pending item-control rungs on pending requests of this group's departments."""
    return [p for p in conn.execute(
        "SELECT s.id, s.seq, s.assignee, r.id AS pr_id, r.pr_no, r.title, r.requester, "
        "r.department, r.current_seq FROM pr_steps s JOIN pr_requests r ON r.id=s.pr_id "
        "WHERE s.stage=? AND s.status='pending' AND r.status='pending'", (STAGE,)).fetchall()
        if group_for(conn, p["department"], _rows=[group])]


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
    """{why, group} when the latest submission of this request left the rung off
    (the 'item_control_skipped' note is written just before 'submitted')."""
    conn = get_db()
    try:
        ev = conn.execute("SELECT detail, action FROM pr_events WHERE pr_id=? AND action IN "
                          "('item_control_skipped','submitted') ORDER BY id DESC LIMIT 2",
                          (pr_id,)).fetchall()
        if not (len(ev) == 2 and ev[0]["action"] == "submitted"
                and ev[1]["action"] == "item_control_skipped"):
            return None
        pr = conn.execute("SELECT department FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        g = group_for(conn, pr["department"] if pr else "")
    finally:
        conn.close()
    note = ev[1]["detail"] or ""
    why = next((k for k, v in SKIP_NOTES.items() if note.startswith(v.split("%s")[0])),
               "unassigned")
    return {"why": why, "group": g["group_key"] if g else ""}


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
}
