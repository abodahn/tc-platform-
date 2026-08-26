"""An administrator can find out why a registration produced no email.

Twice in one afternoon a signup produced silence and looked like a fault, and
both times the system was right: the address was already registered, so
register() returned a neutral success and sent nothing. That neutrality is
deliberate — a different answer would let anyone discover who works here by
trying addresses — and it must not change.

What was missing is the other half. The applicant is told nothing; the
ADMINISTRATOR has to be told everything, or "I signed up and got nothing" is
unanswerable by the person whose job is answering it.

So this checks both halves at once:

  - the applicant still cannot tell a duplicate from a success;
  - an admin can, on /admin/registrations/email-log, with the reason and the
    remedy in words;
  - a duplicate leaves NO user row, which is why the registrations list alone
    could never have shown this;
  - the log is behind users_view, so it does not become the enumeration oracle
    the neutral response exists to prevent;
  - and VERIFY_SENT records whether an email actually went, rather than being
    written unconditionally the way it used to be.

    python app/accounts/tests_mail_log.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.pop("DATABASE_URL", None)
os.environ["TC_ENV"] = "development"
os.environ["TC_SMTP_HOST"] = ""            # no relay: sends must be RECORDED as failed

import config                                                    # noqa: E402
config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="maillog_"), "m.db")

from app import create_app                                       # noqa: E402
from app.db import get_db                                        # noqa: E402

app = create_app()
OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def signup(cl, email, emp):
    page = cl.get("/signup").get_data(as_text=True)
    m = re.search(r'name="_csrf"\s+value="([^"]+)"', page)
    return cl.post("/signup", data={
        "_csrf": m.group(1) if m else "",
        "first_name": "Log", "last_name": "Probe", "email": email, "employee_id": emp,
        "company": "T&C", "department": "IT", "job_title": "Tester",
        "preferred_language": "en", "password": "Kq8#vRm2Ztx4", "confirm_password": "Kq8#vRm2Ztx4",
        "accept_terms": "on", "accept_privacy": "on"})


def users_named(email):
    with app.app_context():
        conn = get_db()
        try:
            return conn.execute("SELECT COUNT(*) c FROM users WHERE LOWER(email)=?",
                                (email.lower(),)).fetchone()["c"]
        finally:
            conn.close()


def run():
    with app.test_client() as cl:
        print("the applicant is told nothing either way — and that must not change")
        first = signup(cl, "log.probe@tcgarments.com", "ZZ-LOG-1")
        again = signup(cl, "log.probe@tcgarments.com", "ZZ-LOG-2")   # same email, new id
        a, b = first.get_data(as_text=True), again.get_data(as_text=True)
        errs = lambda h: sorted(set(re.findall(r'data-i18n="(acc\.err\.[a-z_]+)"', h)))
        chk("a new registration is accepted", not errs(a), errs(a))
        chk("a duplicate is ALSO accepted, with no error", not errs(b), errs(b))
        chk("and neither page says which it was",
            ("already" not in b.lower()) and ("exists" not in b.lower()))
        chk("the duplicate created no second account", users_named("log.probe@tcgarments.com") == 1,
            users_named("log.probe@tcgarments.com"))

        print("\nthe administrator can see what really happened")
        with cl.session_transaction() as s:
            s["uid"] = 1; s["ep"] = 0
        r = cl.get("/admin/registrations/email-log")
        html = r.get_data(as_text=True)
        chk("the log opens", r.status_code == 200, r.status_code)
        chk("the duplicate is named as a duplicate", "Already registered" in html)
        chk("the address is shown", "log.probe@tcgarments.com" in html)
        chk("and it says what to do about it", "Forgot password" in html)

        print("\nan email that could not be sent is recorded as not sent")
        # SMTP is deliberately unset above, so the first signup's verification
        # could not have gone. The audit must say so rather than claim success.
        chk("the log reports email being switched off", "Email is switched off" in html,
            "looked for the smtp_not_configured explanation")
        with app.app_context():
            conn = get_db()
            try:
                rows = [dict(x) for x in conn.execute(
                    "SELECT event, result FROM auth_events WHERE event='verify_sent'").fetchall()]
            finally:
                conn.close()
        chk("verify_sent is not written as 'ok' when nothing was sent",
            rows and all(x["result"] != "ok" for x in rows), rows[:2])

        print("\nand it is not a new way to enumerate accounts")
        with cl.session_transaction() as s:
            s.clear()
        anon = cl.get("/admin/registrations/email-log")
        chk("signed out, the log is refused", anon.status_code in (301, 302, 401, 403),
            anon.status_code)
        chk("and the address does not leak in whatever is returned",
            "log.probe@tcgarments.com" not in anon.get_data(as_text=True))

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
