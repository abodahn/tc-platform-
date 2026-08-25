"""An account email that carries a link must actually contain it.

_shell() built the call-to-action button into a local called `cta` and then
never interpolated it into the HTML it returned. Every email with an action —
verify your address, reset your password, your account was approved — went out
saying "use the button below" with no button and no URL anywhere in the body.

Nothing caught it. The send path reported success, Brevo reported Delivered, and
the mail arrived: only the one thing the reader needed was missing. Signup could
not be completed and neither could a password reset, on a platform whose whole
registration flow depends on those two links.

So this checks the BODY, not the delivery:

  - the URL is in an href, so the button works;
  - the URL is also visible as text, because a client that blocks styled links
    still has to show the reader where to go;
  - it survives into the plain-text part, which is what a text-only client and
    most spam filters read;
  - and all of that in all three languages, since the templates branch on lang.

    python app/accounts/tests_email_links.py
"""
import os
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

import config                                                     # noqa: E402
config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="mail_"), "m.db")

from app import create_app                                        # noqa: E402
create_app()

from app.accounts import emails                                   # noqa: E402
from app.services.alerts import _html_to_text                     # noqa: E402

URL = "https://tc-platform.onrender.com/reset-password?token=TESTTOKEN123"
USER = {"full_name": "Ahmed ElGohary", "username": "ahmed"}

# every builder that takes a url, and how to call it
WITH_LINK = [
    ("verify_email", lambda lang: emails.verify_email(USER, URL, lang)),
    ("reset_request", lambda lang: emails.reset_request(USER, URL, 30, lang)),
    ("approved", lambda lang: emails.approved(USER, URL, lang)),
]

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def run():
    print("every account email that carries a link actually contains it")
    for name, build in WITH_LINK:
        for lang in ("en", "ar", "tr"):
            subj, html, text = build(lang)
            plain = text or _html_to_text(html)
            chk("%s [%s] the button links to the URL" % (name, lang),
                ('href="%s"' % URL) in html)
            chk("%s [%s] the URL is also readable as text" % (name, lang),
                URL in html.replace('href="%s"' % URL, ""))
            chk("%s [%s] and survives into the plain-text part" % (name, lang),
                URL in plain)
            chk("%s [%s] the subject is not empty" % (name, lang), bool((subj or "").strip()))

    print("\nand the shell itself")
    # Directly: a shell given a label and a url must emit both. This is the
    # exact defect — the variable was built and then dropped on the floor.
    html = emails._shell("en", "Heading", "<p>Body</p>", "Press me", URL, "note")
    chk("a call-to-action passed to _shell reaches the output",
        "Press me" in html and URL in html)
    # ...and one given no url must not emit an empty button
    bare = emails._shell("en", "Heading", "<p>Body</p>")
    chk("no stray button when there is no call to action",
        "<a href" not in bare)

    print("\nRTL is not an excuse to lose the link")
    _s, ar_html, _t = emails.reset_request(USER, URL, 30, "ar")
    chk("the Arabic email is marked rtl", 'dir="rtl"' in ar_html)
    chk("and still carries the link", URL in ar_html)

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
