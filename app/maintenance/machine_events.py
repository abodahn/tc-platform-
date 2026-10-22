"""
TC Platform — machine events from TC Terminal (the sewing-line terminals).

Each terminal reports what happens on its machine: the line chief opens a fault, the
mechanic starts and finishes the repair, Egemen shows parts awaited, the chief ends a
fault without a repair. The events reach POST /api/maintenance/machine-event through the
LAN bridge on the monitoring server. One fault on a terminal (its fault_uid) is one
maintenance ticket, moved only with the service functions the screens use, so the audit
trail, the SLA clock and the notifications stay as they are. The maintenance manager
still closes the ticket here: a finished repair stops at 'resolved'.

A fault that finds its machine already holding an open ticket (raised by hand, or by an
earlier fault) joins it but may only comment on it: a terminal never cancels, resolves or
reassigns a ticket it did not open. The parts approval chain is never skipped either.

Every event carries an event_uid and is claimed before it is applied, so a resend (the
bridge retries after a timeout, possibly while the first copy still runs) never applies it
twice, and a copy that failed half way finishes what it still owes on the next try.
Machines, people and fault types are matched through mnt_ext_map (machine:<Egemen MakineID>
-> mnt_machines.code, person:<PersonelId> -> users.username, fault:<ArizaTipiId> -> an
ISSUE_CATEGORIES value). An unknown one is still ticketed and gets a row with an empty
local_value, so an admin sees what is left to link.
"""
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.db import get_db
from app.maintenance import services as svc
from app.maintenance.constants import ISSUE_CATEGORIES

EVENTS = ("fault_opened", "repair_started", "parts_waiting_on", "parts_waiting_off",
          "repair_finished", "fault_closed")
_SAID = {"repair_started": "repair started", "parts_waiting_on": "waiting for parts",
         "parts_waiting_off": "parts arrived", "repair_finished": "repair finished",
         "fault_closed": "fault ended"}
_UID = re.compile(r"[A-Za-z0-9_-]{8,64}")
_CODE = re.compile(r"[A-Za-z0-9_-]{1,20}")
_EARLY = ("draft", "submitted", "under_review", "reopened")
_PARTS = ("waiting_stock", "waiting_approval", "approved_issue", "parts_issued")   # the platform's own chain
_STAMPS = ("created_at", "repair_started_at", "repair_done_at")
_STALE = timedelta(minutes=10)   # a claim this old was left by a request that died
_RETRY_NOW = "2000-01-01 00:00:00"


# --- trust boundary --------------------------------------------------------
def _text(v, n):
    """Plain text: no control characters (PostgreSQL refuses NUL) and no lone surrogates,
    so a bad name can never turn into an event that fails on every resend."""
    if not isinstance(v, str):
        return ""
    v = v.encode("utf-8", "replace").decode("utf-8")
    return "".join(c for c in v if c >= " " or c in "\t\n").strip()[:n]


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 2 ** 31 else None


def validate(p):
    """The JSON body -> (event dict, "") or (None, reason). Nothing else in it is trusted."""
    if not isinstance(p, dict):
        return None, "bad_json"
    if p.get("event") not in EVENTS:
        return None, "bad_event"
    for k in ("event_uid", "fault_uid"):
        if not isinstance(p.get(k), str) or not _UID.fullmatch(p[k]):
            return None, "bad_" + k
    if not isinstance(p.get("machine_code"), str) or not _CODE.fullmatch(p["machine_code"]):
        return None, "bad_machine_code"
    person = p.get("person") if isinstance(p.get("person"), dict) else {}
    fault = p.get("fault_type") if isinstance(p.get("fault_type"), dict) else {}
    return {
        "event": p["event"], "event_uid": p["event_uid"], "fault_uid": p["fault_uid"],
        "machine_code": p["machine_code"], "machine_name": _text(p.get("machine_name"), 80),
        "occurred_at": _text(p.get("occurred_at"), 40), "clock_ok": p.get("clock_ok") is True,
        "machine_stopped": p.get("machine_stopped") is True, "note": _text(p.get("note"), 500),
        # repair_finished: the terminal says whether Egemen still shows the fault afterwards
        "fault_open_after": p.get("fault_open_after") is True,
        "person_id": _int(person.get("id")), "person_name": _text(person.get("name"), 80),
        "fault_type_id": _int(fault.get("id")), "fault_type_name": _text(fault.get("name"), 120),
    }, ""


def _naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc(ev):
    """The time on the machine as the platform stores it (UTC text), or None when it cannot be
    trusted: a board clock not yet set by NTP, no offset, the future, or over a week ago."""
    if not ev["clock_ok"]:
        return None
    try:
        t = datetime.fromisoformat(ev["occurred_at"])
        if t.tzinfo is None:
            return None
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, OverflowError):
        return None
    now = _naive_utc_now()
    if not now - timedelta(days=7) <= t <= now + timedelta(minutes=2):
        return None
    return t.strftime("%Y-%m-%d %H:%M:%S")


# --- mapping ---------------------------------------------------------------
def _mapped(conn, key, name):
    """local_value of an ext_map row, or None. An unknown key gets an empty row for an admin."""
    r = conn.execute("SELECT local_value, is_active FROM mnt_ext_map WHERE ext_key=?", (key,)).fetchone()
    if r is None:   # INSERT OR IGNORE: a failed INSERT would abort the whole PostgreSQL transaction
        conn.execute("INSERT OR IGNORE INTO mnt_ext_map (ext_key, ext_name, created_at) VALUES (?,?,?)",
                     (key, name or None, svc._now()))
        return None
    if not r["is_active"]:
        return None
    return (r["local_value"] or "").strip() or None


def _context(conn, ev):
    """(machine row or {}, actor dict, issue category) for this event."""
    machine = {}
    code = _mapped(conn, "machine:" + ev["machine_code"], ev["machine_name"])
    if code:
        m = conn.execute("SELECT id, code, department, area, line_no FROM mnt_machines "
                         "WHERE code=? AND is_active=1", (code,)).fetchone()
        machine = dict(m) if m else {}
    name = ev["person_name"] or "TC Terminal"
    user = None
    if ev["person_id"] is not None:
        username = _mapped(conn, "person:%d" % ev["person_id"], ev["person_name"])
        if username:
            user = conn.execute("SELECT id, username, full_name, role FROM users "
                                "WHERE username=? AND is_active=1", (username,)).fetchone()
    actor = ({"id": user["id"], "username": user["username"],
              "full_name": user["full_name"] or user["username"], "role": user["role"]} if user else
             {"id": None, "username": ("tcterm:" + name)[:60], "full_name": name, "role": "tc_terminal"})
    category = "other"
    if ev["fault_type_id"] is not None:
        c = _mapped(conn, "fault:%d" % ev["fault_type_id"], ev["fault_type_name"])
        category = c if c in ISSUE_CATEGORIES else "other"
    return machine, actor, category


# --- small writes ----------------------------------------------------------
def _write(sql, args):
    conn = get_db()
    try:
        conn.execute(sql, args)
        conn.commit()
    finally:
        conn.close()


def _link(uid, tid, opened):
    """Tie this event (so this fault) to its ticket at once, before anything else can fail."""
    _write("UPDATE mnt_machine_events SET ticket_id=?, opened=? WHERE event_uid=?", (tid, 1 if opened else 0, uid))


def _stamp(tid, column, ev):
    """The real time on the machine, not the time the event arrived (it may have waited offline)."""
    when = _utc(ev)
    if when and column in _STAMPS:
        _write(f"UPDATE mnt_tickets SET {column}=? WHERE id=?", (when, tid))


def _machine_status(machine, status, tid=None):
    """'running' only when no other ticket on the machine is still being worked (tid is this one)."""
    if not machine.get("id"):
        return
    conn = get_db()
    try:
        if status == "running" and conn.execute(
                "SELECT 1 FROM mnt_tickets WHERE machine_id=? AND id<>? AND is_active=1 AND status IN (%s) LIMIT 1"
                % ",".join("?" * len(svc.OPEN_TICKET_STATUSES)),
                (machine["id"], tid or 0, *svc.OPEN_TICKET_STATUSES)).fetchone():
            return
        conn.execute("UPDATE mnt_machines SET status=? WHERE id=?", (status, machine["id"]))
        conn.commit()
    finally:
        conn.close()


def _ticket(tid):
    conn = get_db()
    try:
        r = conn.execute("SELECT status, assigned_to, response_due, resolution_due, description "
                         "FROM mnt_tickets WHERE id=?", (tid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def _walk(tid, steps, actor, comment, cancel=False):
    """Legal steps one by one, unforced: the transition table still decides."""
    conn = get_db()
    try:
        if cancel:   # what cancelling on the screen frees too: parts reserved for this ticket
            svc._release_ticket_reservations(conn, tid)
        for s in steps:
            ok, _ = svc.set_ticket_status(conn, tid, s, actor, comment=comment)
            if not ok:
                break
        conn.commit()
    finally:
        conn.close()


def _assign(tid, t, actor):
    # the ticket's own due dates go back in: review_assign writes exactly what it is given
    svc.review_assign(tid, actor["full_name"], None, t["response_due"], t["resolution_due"], actor)


def _resolve(tid, actor, machine, test):
    svc.record_test(tid, test, actor)
    _machine_status(machine, "running", tid)
    return tid, "resolved"


# --- the cycle -------------------------------------------------------------
def _ref(ev):
    return "tcterm:%s:%s" % (ev["machine_code"], ev["fault_uid"])


def _open(ev, uid, machine, actor, category):
    """-> (ticket id, "opened" | "joined_open_ticket")."""
    what = ev["fault_type_name"] or "Fault"
    # the fault's reference rides on the ticket: if this copy dies between creating the ticket and
    # linking it, the resend finds the ticket here instead of opening a second one
    marker = "Opened automatically by TC Terminal [%s]" % _ref(ev)
    conn = get_db()
    try:
        r = conn.execute("SELECT id FROM mnt_tickets WHERE remarks=? AND is_active=1 ORDER BY id DESC LIMIT 1",
                         (marker,)).fetchone()
    finally:
        conn.close()
    if r:
        _link(uid, r["id"], opened=True)
        _stamp(r["id"], "created_at", ev)
        return r["id"], "opened"
    stopped = ev["machine_stopped"]
    data = {
        "requester": actor["full_name"], "department": machine.get("department"),
        "area": machine.get("area"), "line_no": machine.get("line_no"),
        "machine_id": machine.get("id"),
        "machine_code": machine.get("code") or "EGEMEN-" + ev["machine_code"],
        "issue_category": category,
        "description": " - ".join(x for x in (what, ev["note"]) if x)
                       + " (TC Terminal, machine %s %s)" % (ev["machine_code"], ev["machine_name"]),
        "priority": "high" if stopped else "medium", "severity": "major" if stopped else "moderate",
        "production_stopped": stopped, "remarks": marker,
    }
    tid, err = svc.create_ticket(data, actor)
    if err.startswith("duplicate_open:"):   # the machine already has an open ticket: this fault joins it
        conn = get_db()
        try:
            r = conn.execute("SELECT id FROM mnt_tickets WHERE ticket_no=?", (err.split(":", 1)[1],)).fetchone()
        finally:
            conn.close()
        if not r:
            return None, "no_ticket"
        _link(uid, r["id"], opened=False)
        svc.add_comment(r["id"], actor, "TC Terminal: %s reported on this machine (%s)" % (what, ev["machine_code"]))
        return r["id"], "joined_open_ticket"
    if err:
        raise RuntimeError(err)   # 500: the bridge keeps the event and sends it again
    _link(uid, tid, opened=True)
    _stamp(tid, "created_at", ev)
    return tid, "opened"


def _apply(ev, uid, tid, owner, closed_before, retry, machine, actor, category):
    e, name = ev["event"], actor["full_name"]
    if e == "fault_opened":
        if tid:   # this fault already has its ticket: a resend that failed half way, or reported twice
            if retry:
                if owner:
                    _stamp(tid, "created_at", ev)   # what the failed copy may still owe
                return tid, "opened" if owner else "joined_open_ticket"
            svc.add_comment(tid, actor, "TC Terminal: reported again")
            return tid, "comment"
        if closed_before:   # its end arrived first (out of order): nothing left to repair
            return None, "already_closed"
        return _open(ev, uid, machine, actor, category)
    if tid is not None and not owner and e in ("repair_started", "repair_finished") \
            and _ticket(tid)["status"] in ("resolved", "closed", "cancelled", "rejected"):
        tid = None   # the ticket it joined has ended while this fault still holds the machine: it gets its own
    if tid is None:
        if e not in ("repair_started", "repair_finished"):
            return None, "no_ticket"   # a fault from before the link: nothing to move
        if closed_before:   # its end arrived first (out of order): nothing left to repair
            return None, "already_closed"
        tid, how = _open(ev, uid, machine, actor, category)
        if tid is None:
            return None, "no_ticket"
        owner = how == "opened"
    if not owner:   # joined someone else's ticket: a comment, never a move
        svc.add_comment(tid, actor, "TC Terminal: %s by %s" % (_SAID[e], name))
        return tid, "comment"
    t = _ticket(tid)
    status = t["status"]

    if e == "repair_started":
        if status in _EARLY:
            _assign(tid, t, actor)
            status = "assigned"
        steps = {"assigned": ("diagnosis", "repair"), "diagnosis": ("repair",),
                 "spare_required": ("repair",), "parts_issued": ("repair",), "repair": ()}.get(status)
        if steps is None:   # parts in their approval chain, or already repaired: the platform keeps control
            svc.add_comment(tid, actor, "TC Terminal: repair started by %s" % name)
            return tid, "comment"
        _walk(tid, steps, actor, "TC Terminal: repair started by %s" % name)
        if not t["assigned_to"] and status != "assigned":
            _write("UPDATE mnt_tickets SET assigned_to=? WHERE id=?", (name, tid))
        _stamp(tid, "repair_started_at", ev)
        _machine_status(machine, "under_maintenance")
        return tid, "repair"

    if e == "parts_waiting_on":
        if status in ("diagnosis", "repair", "spare_required"):
            _walk(tid, ("spare_required",), actor, "TC Terminal: waiting for parts")
            _machine_status(machine, "waiting_spare")
            return tid, "spare_required"
        svc.add_comment(tid, actor, "TC Terminal: waiting for parts")
        return tid, "comment"

    if e == "parts_waiting_off":
        if status == "spare_required":
            _walk(tid, ("repair",), actor, "TC Terminal: parts arrived")
            _machine_status(machine, "under_maintenance")
            return tid, "repair"
        svc.add_comment(tid, actor, "TC Terminal: no longer waiting for parts")
        return tid, "comment"

    if e == "repair_finished":
        if status == "resolved":   # a resend after it got this far: only the machine is left
            _machine_status(machine, "running", tid)
            return tid, "resolved"
        if status == "testing":
            if ev["fault_open_after"]:
                return tid, "testing"
            return _resolve(tid, actor, machine, {"test_result": "Pass", "machine_running": "Yes"})
        if status in _PARTS or status in ("closed", "cancelled", "rejected"):
            svc.add_comment(tid, actor, "TC Terminal: repair finished by %s" % name)
            return tid, "comment"
        if not t["assigned_to"]:
            _assign(tid, t, actor)
        svc.repair_proof(tid, {"action_performed": "Repaired on TC Terminal by %s" % name,
                               "machine_running": "Yes",
                               "final_notes": ev["fault_type_name"] or t["description"]}, actor)
        _stamp(tid, "repair_done_at", ev)
        if ev["fault_open_after"]:   # Egemen still shows the fault: the line chief's end confirms it
            _machine_status(machine, "under_testing")
            return tid, "testing"
        return _resolve(tid, actor, machine, {"test_result": "Pass", "machine_running": "Yes"})

    # fault_closed
    if status == "testing":
        return _resolve(tid, actor, machine, {"test_result": "Pass", "safety_check": "OK", "machine_running": "Yes"})
    if status in ("repair", "spare_required"):   # ended elsewhere while the repair ran: it is done
        svc.repair_proof(tid, {"action_performed": "Fault ended on TC Terminal by %s" % name,
                               "machine_running": "Yes", "final_notes": t["description"]}, actor)
        return _resolve(tid, actor, machine, {"test_result": "Pass", "safety_check": "OK", "machine_running": "Yes"})
    if status in ("submitted", "under_review", "assigned", "diagnosis"):
        # ended by the line chief with no repair: not a breakdown, so cancelled rather than closed
        _walk(tid, ("cancelled",), actor, "Ended on TC Terminal by %s without a repair" % name, cancel=True)
        _machine_status(machine, "running", tid)
        return tid, "cancelled"
    if status in ("cancelled", "resolved"):   # a resend after it got this far: only the machine is left
        _machine_status(machine, "running", tid)
        return tid, status
    svc.add_comment(tid, actor, "TC Terminal: fault ended by %s" % name)
    return tid, "comment"


def _claim(conn, uid, fault_ref, ev):
    """Claim the event before applying it. -> (claim, None), or (None, (http code, answer))."""
    now = svc._now()
    claim = "pending:" + uuid.uuid4().hex
    conn.execute("INSERT OR IGNORE INTO mnt_machine_events (event_uid, fault_ref, event, machine_code, "
                 "occurred_at, received_at, result) VALUES (?,?,?,?,?,?,?)",
                 (uid, fault_ref, ev["event"], ev["machine_code"], ev["occurred_at"], now, claim))
    conn.commit()
    r = conn.execute("SELECT result, ticket_id, received_at FROM mnt_machine_events WHERE event_uid=?",
                     (uid,)).fetchone()
    if r["result"] == claim:
        return claim, None
    if not (r["result"] or "").startswith("pending:"):
        return None, (200, _answer(r["ticket_id"], "duplicate", True))
    since = svc._parse_dt(r["received_at"])
    if since and since > _naive_utc_now() - _STALE:
        return None, (409, {"ok": False, "error": "in_progress"})   # the bridge sends it again later
    conn.execute("UPDATE mnt_machine_events SET result=?, received_at=? WHERE event_uid=? AND result=?",
                 (claim, now, uid, r["result"]))   # left by a request that died: take it over
    conn.commit()
    r = conn.execute("SELECT result FROM mnt_machine_events WHERE event_uid=?", (uid,)).fetchone()
    return (claim, None) if r["result"] == claim else (None, (409, {"ok": False, "error": "in_progress"}))


def _free(uid, claim):
    """Let the next resend take the event over at once (the ticket link it made is kept)."""
    _write("UPDATE mnt_machine_events SET result=?, received_at=? WHERE event_uid=? AND result=?",
           ("pending:retry", _RETRY_NOW, uid, claim))


def handle(ev):
    """Apply one validated event. -> (http code, JSON answer) for the bridge."""
    uid, fault_ref = ev["event_uid"], _ref(ev)
    conn = get_db()
    try:
        claim, early = _claim(conn, uid, fault_ref, ev)
    finally:
        conn.close()
    if early:
        return early
    try:
        return _run(ev, uid, fault_ref, claim)
    except Exception:
        _free(uid, claim)
        raise


def _run(ev, uid, fault_ref, claim):
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, event_uid, event, ticket_id, opened, result, received_at "
                            "FROM mnt_machine_events WHERE fault_ref=? ORDER BY id", (fault_ref,)).fetchall()
        mine = conn.execute("SELECT id FROM mnt_machine_events WHERE event_uid=?", (uid,)).fetchone()["id"]
        fresh = _naive_utc_now() - _STALE
        if any(r["id"] < mine and (r["result"] or "").startswith("pending:")
               and (svc._parse_dt(r["received_at"]) or fresh) > fresh for r in rows):
            # an earlier event of the same fault is still being applied: one at a time, in order
            conn.close()
            _free(uid, claim)
            return 409, {"ok": False, "error": "in_progress"}
        tid = next((r["ticket_id"] for r in reversed(rows) if r["ticket_id"]), None)
        owner = any(r["opened"] for r in rows)
        closed_before = any(r["event"] == "fault_closed" and r["event_uid"] != uid for r in rows)
        retry = any(r["event_uid"] == uid and r["ticket_id"] for r in rows)
        machine, actor, category = _context(conn, ev)
        conn.commit()   # the placeholder rows; nothing stays open while the services write
    finally:
        conn.close()
    tid, result = _apply(ev, uid, tid, owner, closed_before, retry, machine, actor, category)
    _write("UPDATE mnt_machine_events SET ticket_id=COALESCE(ticket_id, ?), result=?, payload=? WHERE event_uid=?",
           (tid, result, json.dumps(ev, ensure_ascii=False)[:4000], uid))
    return 200, _answer(tid, result, False)


def _answer(tid, result, duplicate):
    ticket_no = status = None
    if tid:
        conn = get_db()
        try:
            r = conn.execute("SELECT ticket_no, status FROM mnt_tickets WHERE id=?", (tid,)).fetchone()
        finally:
            conn.close()
        if r:
            ticket_no, status = r["ticket_no"], r["status"]
    return {"ok": True, "duplicate": duplicate, "result": result, "ticket_no": ticket_no, "status": status}
