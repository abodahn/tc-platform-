"""
Accounts (Auth) — public self-service routes.

  /signup                registration
  /verify-email          email verification (token)
  /resend-verification   request a fresh verification link (neutral)
  /forgot-password       request a reset link (enumeration-safe, rate-limited)
  /reset-password        set a new password from a reset token

All are additive and available while logged out; they never change how existing
users authenticate. CSRF is enforced globally on POST.
"""
from flask import (Blueprint, render_template, request, redirect, url_for, flash,
                   session)

from app.auth import current_user
from app.accounts import services as svc

bp = Blueprint("accounts", __name__)


def _authed_redirect():
    if current_user():
        return redirect(url_for("main.dashboard"))
    return None


def _form_keep(form):
    """Preserve entered values after a validation error — never passwords."""
    keep = {}
    for k in ("first_name", "last_name", "email", "employee_id", "mobile", "company",
              "department", "job_title", "location", "manager", "preferred_language",
              "accept_terms", "accept_privacy"):
        keep[k] = form.get(k, "")
    return keep


# ==========================================================================
# Sign up
# ==========================================================================
@bp.route("/signup", methods=["GET", "POST"])
def signup():
    r = _authed_redirect()
    if r:
        return r
    if request.method == "POST":
        ok, errors, msg = svc.register(request.form)
        if ok:
            return render_template("accounts/signup.html", submitted=True,
                                   org=svc.org_options(), values={}, errors={}, msg=msg)
        return render_template("accounts/signup.html", submitted=False,
                               org=svc.org_options(), values=_form_keep(request.form),
                               errors=errors, policy=svc.password_policy())
    return render_template("accounts/signup.html", submitted=False, org=svc.org_options(),
                           values={}, errors={}, policy=svc.password_policy())


# ==========================================================================
# Email verification
# ==========================================================================
@bp.route("/verify-email")
def verify_email():
    token = request.args.get("token", "")
    state = svc.verify_email(token) if token else "verify_invalid"
    return render_template("accounts/verify.html", state=state)


@bp.route("/resend-verification", methods=["GET", "POST"])
def resend_verification():
    if request.method == "POST":
        svc.resend_verification(request.form.get("identifier", ""))
        return render_template("accounts/verify.html", state="resend_sent")
    return render_template("accounts/verify.html", state="resend_form")


# ==========================================================================
# Forgot password  (always neutral)
# ==========================================================================
@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    r = _authed_redirect()
    if r:
        return r
    if request.method == "POST":
        svc.forgot_password(request.form.get("identifier", ""))
        return render_template("accounts/forgot.html", sent=True)
    return render_template("accounts/forgot.html", sent=False)


# ==========================================================================
# Reset password
# ==========================================================================
@bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    token = request.args.get("token") or request.form.get("token") or ""
    if request.method == "POST":
        ok, errors = svc.reset_password(token, request.form.get("password", ""),
                                        request.form.get("confirm_password", ""))
        if ok:
            flash("acc.msg.password_reset_ok", "success")
            return render_template("accounts/reset.html", state="done")
        # link errors -> show the appropriate dead-end state; policy errors -> re-show form
        if errors == ["acc.err.reset_link"]:
            return render_template("accounts/reset.html", state="invalid")
        return render_template("accounts/reset.html", state="form", token=token,
                               errors=errors, policy=svc.password_policy())
    # GET — validate the token first
    reason, _u = svc.reset_token_state(token) if token else ("invalid", None)
    state = {"ok": "form", "invalid": "invalid", "expired": "expired", "used": "used"}.get(reason, "invalid")
    return render_template("accounts/reset.html", state=state, token=token,
                           errors=[], policy=svc.password_policy())
