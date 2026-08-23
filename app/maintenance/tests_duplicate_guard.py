"""The duplicate guard, from the screen rather than from the service.

The guard itself was never the problem. It refused correctly, said so in a
flash, and told the reporter to tick 'create anyway' — a checkbox that existed
in create_ticket(), in the route that reads request.form, and on no screen in
the application. So the refusal was a dead end: the only instruction it gave
could not be followed. Worse, the re-rendered form dropped everything typed,
so the reporter lost the description, the category and the photos as well.

Worst of the three, the Easy Report — the picture-only flow for floor workers
who do not read — posts by fetch() and treated any 200 as "sent". A refusal
re-rendered the form, which is a 200, so it showed a green tick for a ticket
that was never created. Nobody would ever have known: the machine stays broken
and the report that was supposed to say so does not exist.

This walks all three through the real routes:

  1. a second ticket on a machine with one open is refused, and the form comes
     back carrying what was typed, naming the open ticket, and offering the
     override;
  2. ticking the override creates the ticket;
  3. the Easy Report gets 409 and the ticket number, not a 200 it would read as
     success.

    python app/maintenance/tests_duplicate_guard.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

# These print Arabic. A cp1252 console (Git Bash) raises
# UnicodeEncodeError part-way through and every check below the
# first Arabic line silently never runs.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.pop("DATABASE_URL", None)
os.environ["TC_ENV"] = "development"

import config                                                     # noqa: E402
config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="dupguard_"), "dup.db")

from app import create_app                                        # noqa: E402
from app.db import get_db                                         # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def machine_id():
    """A machine of our own, with no open ticket on it.

    Reusing a seeded one made the FIRST post a duplicate — the guard was already
    holding a demo ticket — and every count after that was off by one against a
    fixture that had never worked, not against the code.
    """
    with app.app_context():
        conn = get_db()
        try:
            n = conn.execute("SELECT COUNT(*) c FROM mnt_machines "
                             "WHERE code LIKE 'DUP-GUARD-%'").fetchone()["c"]
            cur = conn.execute(
                "INSERT INTO mnt_machines (code, name, is_active) VALUES (?,?,1)",
                ("DUP-GUARD-%d" % (n + 1), "Duplicate guard fixture"))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def tickets_on(mid):
    with app.app_context():
        conn = get_db()
        try:
            return conn.execute(
                "SELECT COUNT(*) n FROM mnt_tickets WHERE machine_id=? AND is_active=1",
                (mid,)).fetchone()["n"]
        finally:
            conn.close()


def open_ticket_no(mid):
    with app.app_context():
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT ticket_no FROM mnt_tickets WHERE machine_id=? AND is_active=1 "
                "ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
            return row["ticket_no"] if row else None
        finally:
            conn.close()


def audit_rows(action):
    with app.app_context():
        conn = get_db()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM mnt_audit WHERE action=? ORDER BY id", (action,)).fetchall()]
        finally:
            conn.close()


def guard_is_on():
    with app.app_context():
        conn = get_db()
        try:
            from app.maintenance import workflow as wf
            return wf.dup_guard_on(conn)
        finally:
            conn.close()


def run():
    mid = machine_id()
    print("the duplicate guard is on by default:", guard_is_on())
    chk("the guard is actually on, or this whole file proves nothing", guard_is_on())

    typed = {
        "machine_id": str(mid),
        "requester": "Fixture Reporter",
        "shift": "C",
        "department": "Sewing",
        "area": "Line 3",
        "line_no": "3",
        "issue_category": "electrical",
        "priority": "high",
        "severity": "major",
        "description": "needle bar seized on the second head",
        "est_downtime_min": "45",
        "safety_impact": "on",
        "remarks": "smelled burning",
    }

    with app.test_client() as cl:
        with cl.session_transaction() as s:
            s["uid"] = 1                      # admin, seeded by create_app
            s["ep"] = 0
        # This app has its own CSRF (app/csrf.py), not Flask-WTF's, so the token
        # has to be minted by a GET and posted back — disabling WTF_CSRF does
        # nothing here, and a missing token is a 400 that looks like a refusal.
        cl.get("/maintenance/tickets/new")
        with cl.session_transaction() as s:
            typed["_csrf"] = s.get("_csrf_token")

        before = tickets_on(mid)
        r = cl.post("/maintenance/tickets/new", data=typed, follow_redirects=False)
        chk("the first ticket is created", r.status_code in (301, 302), r.status_code)
        chk("and it is in the database", tickets_on(mid) == before + 1)

        print("\na second report on the same machine")
        # From the database, not from the page: scraping the first MNT- on the
        # page would take the notification bell's copy, which is the very thing
        # this section has to prove the banner is not being confused with.
        dup_no = open_ticket_no(mid)
        r = cl.post("/maintenance/tickets/new", data=typed)
        body = r.get_data(as_text=True)
        chk("is refused, and the form comes back", r.status_code == 200, r.status_code)
        chk("no second ticket was created", tickets_on(mid) == before + 1,
            tickets_on(mid))

        print("\nand the form that comes back is usable")
        chk("the override checkbox is now on the screen",
            'name="allow_duplicate"' in body)
        chk("it starts unticked, so nothing is bypassed by accident",
            'name="allow_duplicate" checked' not in body)
        # Anchored to the banner's own markup. "MNT-" appears on this page
        # anyway — the notification bell carries the ticket number from the
        # first creation — so a looser check passes with no banner at all.
        chk("the open ticket is named, in the banner and not just the bell",
            ('>%s</a>' % dup_no) in body, dup_no)
        chk("the description is still there",
            "needle bar seized on the second head" in body)
        chk("so are the remarks", "smelled burning" in body)
        chk("so is the downtime", 'value="45"' in body)
        # ANDing two independently-true substrings proved nothing: "selected"
        # is always on the page from the priority and severity defaults. This is
        # the one repopulated field with no other assertion behind it, and the
        # one whose silent reset files the fault against the wrong machine.
        chk("the machine is still selected",
            bool(re.search(r'<option value="%d"[^>]*\bselected' % mid, body)))
        chk("priority is still high, not back to the default",
            'data-i18n="mx.high" selected' in body)
        chk("severity is still major", 'data-i18n="mx.major" selected' in body)
        chk("the safety tick survived", 'name="safety_impact" checked' in body)
        chk("and the reporter is told the photos were not kept",
            'data-i18n="m.dup_photos"' in body)

        print("\nticking the override lets it through")
        again = dict(typed)
        again["allow_duplicate"] = "on"
        r = cl.post("/maintenance/tickets/new", data=again, follow_redirects=False)
        chk("the ticket is created", r.status_code in (301, 302), r.status_code)
        chk("and there are now two on this machine", tickets_on(mid) == before + 2,
            tickets_on(mid))

        # A control that can be overridden from a screen has to say when it was.
        rows = audit_rows("duplicate_override")
        chk("the override is written to the audit trail", len(rows) == 1, len(rows))
        if rows:
            chk("naming the ticket that was already open",
                (rows[0]["old_value"] or "").startswith("MNT-"), rows[0]["old_value"])
            chk("and who did it", bool(rows[0]["username"]), rows[0]["username"])

        print("\nbut only when something was really overridden")
        n_audit = len(audit_rows("duplicate_override"))
        fresh_mid = machine_id()
        first = dict(typed, machine_id=str(fresh_mid), allow_duplicate="on")
        r = cl.post("/maintenance/tickets/new", data=first, follow_redirects=False)
        chk("a first ticket with the box ticked still goes through",
            r.status_code in (301, 302), r.status_code)
        chk("and is NOT recorded as an override, because nothing was overridden",
            len(audit_rows("duplicate_override")) == n_audit,
            len(audit_rows("duplicate_override")))

        print("\nthe Easy Report is told the truth, not shown a green tick")
        easy = dict(typed)
        easy["source"] = "easy"
        easy["description"] = "[Easy] Electrical"
        n_before = tickets_on(mid)
        r = cl.post("/maintenance/tickets/new", data=easy)
        chk("a duplicate from the picture-only flow answers 409, not 200",
            r.status_code == 409, r.status_code)
        chk("nothing was created", tickets_on(mid) == n_before, tickets_on(mid))
        payload = r.get_json(silent=True) or {}
        chk("and it names the ticket already open, so the screen can show it",
            bool(payload.get("ticket_no")), payload)

        print("\nand a SUCCESS from that flow is a fact, not a page")
        easy_mid = machine_id()
        good = dict(typed, source="easy", machine_id=str(easy_mid),
                    description="[Easy] Electrical")
        r = cl.post("/maintenance/tickets/new", data=good)
        chk("a created ticket answers 201, not a redirect", r.status_code == 201,
            r.status_code)
        body = r.get_json(silent=True) or {}
        chk("carrying the ticket number the screen checks for",
            (body.get("ticket_no") or "").startswith("MNT-"), body)
        chk("and the ticket really exists", tickets_on(easy_mid) == 1)

    print("\na session that expired must not look like a success")
    # The green tick used to appear for ANY 200. A POST whose session has been
    # invalidated redirects to /login, fetch follows it, and the login page is a
    # 200 — so a floor worker was told a ticket was raised by the login screen.
    with app.test_client() as cl2:
        with cl2.session_transaction() as s:
            s["uid"] = 1
            s["ep"] = 999999          # stale epoch: every request is bounced
        expired_mid = machine_id()
        r = cl2.post("/maintenance/tickets/new",
                     data=dict(typed, source="easy", machine_id=str(expired_mid),
                               description="[Easy] Electrical"),
                     follow_redirects=True)
        chk("the expired POST does NOT answer 201", r.status_code != 201,
            r.status_code)
        chk("and no ticket was created", tickets_on(expired_mid) == 0,
            tickets_on(expired_mid))
        chk("so the screen has nothing to mistake for a ticket number",
            not (r.get_json(silent=True) or {}).get("ticket_no"))

    with app.test_client() as cl:
        with cl.session_transaction() as s:
            s["uid"] = 1
            s["ep"] = 0
        print("\nthe fresh form is unchanged for everybody else")
        r = cl.get("/maintenance/tickets/new")
        fresh = r.get_data(as_text=True)
        chk("no override control on a form nobody has been refused on",
            "allow_duplicate" not in fresh)
        chk("no duplicate notice either", 'data-i18n="m.dup_title"' not in fresh)
        chk("priority still defaults to medium",
            'data-i18n="mx.medium" selected' in fresh)
        chk("severity still defaults to moderate",
            'data-i18n="mx.moderate" selected' in fresh)

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
