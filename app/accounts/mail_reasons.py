# -*- coding: utf-8 -*-
"""Why a registration produced no email — in words an administrator can act on.

Support's recurring question is "they signed up and got nothing". Twice today
that looked like a fault and twice it was the system behaving correctly: the
address was already registered, so register() returned a neutral success and
sent nothing, deliberately, because a different answer would let anyone discover
who works here by trying addresses.

That silence is right for the applicant and wrong for the administrator. This
maps what the audit trail recorded to a sentence that says what happened and
what to do about it — visible only behind users_view, so the enumeration
protection is untouched.
"""

# auth_events.result -> (short label, what it means, what to do)
REASONS = {
    "duplicate": (
        "Already registered",
        "This email address or employee ID already belongs to an account, so no "
        "email was sent. The applicant sees the same neutral confirmation as a "
        "successful registration — on purpose, so nobody can find out who has an "
        "account by trying addresses.",
        "Search the account list for the address. If the existing account is "
        "stuck pending, approve or reject it; if it is the same person, tell them "
        "to use Forgot password instead of signing up again.",
    ),
    "invite_required": (
        "No invite",
        "Registration is set to invite-only and there is no unused invite for "
        "this address. Nothing was created and no email was sent.",
        "Issue an invite for the address, or change the registration mode in "
        "Account settings.",
    ),
    "smtp_not_configured": (
        "Email is switched off",
        "The account was created, but this deployment has no SMTP host set, so "
        "nothing could be sent. The applicant is waiting for an email that will "
        "never arrive.",
        "Set TC_SMTP_HOST, TC_SMTP_PORT, TC_SMTP_USER, TC_SMTP_PASS, TC_SMTP_FROM "
        "and TC_PUBLIC_URL, then use Resend verification.",
    ),
    "smtp_refused": (
        "The mail server refused it",
        "The account was created and the email was built, but the mail relay "
        "would not accept or deliver it. A relay commonly refuses an address it "
        "has already seen hard-bounce, and a wrong SMTP key looks the same.",
        "Check the relay's own log for this address, then use Resend "
        "verification once the cause is fixed.",
    ),
    "rate_limited": (
        "Too many attempts",
        "The address or the network it came from asked too many times in the "
        "hour, so the request was throttled before anything was sent.",
        "Wait for the window to pass, or resend the verification from here.",
    ),
}

# anything not listed is an exception class name from the send attempt
FALLBACK = (
    "Sending failed",
    "The account was created but the email raised %s while being sent. The "
    "applicant has no way to continue on their own.",
    "Check the application log for the full error, then use Resend verification.",
)


def explain(result):
    """(label, meaning, what_to_do) for an auth_events.result value."""
    key = (result or "").strip()
    if key in REASONS:
        return REASONS[key]
    if key in ("ok", ""):
        return ("Sent", "The email was handed to the mail server successfully.", "")
    label, meaning, todo = FALLBACK
    return (label, meaning % key, todo)


def delivered(result):
    return (result or "").strip() == "ok"
