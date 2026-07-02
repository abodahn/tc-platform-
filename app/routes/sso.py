"""
TC Platform — SSO Identity Provider (IdP) routes.

The platform is the single front door. Once a user is logged in here, opening
an integrated system should NOT ask them to log in again. This blueprint mints
a short-lived, audience-bound signed token for the logged-in platform user and
redirects the browser to that system's Service-Provider endpoint
(``<base_url>/sso/login?token=...``), which verifies the token and establishes
its own local session.

Security posture
----------------
* The token is signed with the shared ``TC_SSO_SECRET`` (HMAC-SHA256) and lives
  only ~2 minutes. It is bound to one system via the ``aud`` claim, so a token
  minted to open ITSM cannot be replayed against Assets.
* Fails **closed and gracefully**: if SSO is disabled or the secret is unusable,
  the module simply opens at its normal URL and the user logs in manually — no
  hard error, no lockout.
* Every launch is written to the audit log (who opened which system, when).
"""
from __future__ import annotations

import logging
import urllib.parse

from flask import Blueprint, redirect, request, abort

from app.auth import login_required, current_user
from app.db import get_db, log_audit
from app.services import sso as sso_lib
from config import Config

log = logging.getLogger("tc.sso")

bp = Blueprint("sso", __name__)


def _system_by_key(key):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM systems WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()


def sso_target_url(key: str, base_url: str) -> str:
    """Return the URL to open system ``key``. When SSO is on, this is the
    platform launch route (which mints a token and hands off). When SSO is off
    or the system has no base URL, it's the system's own URL (manual login)."""
    if Config.SSO_ENABLED and base_url:
        return f"/sso/launch/{urllib.parse.quote(key)}"
    return base_url or ""


@bp.route("/sso/launch/<key>")
@login_required
def launch(key):
    """Mint a token for the current platform user and hand off to the system."""
    row = _system_by_key(key)
    if not row or not row["enabled"] or not row["is_integrated"]:
        abort(404)
    base = (row["base_url"] or "").rstrip("/")
    if not base:
        abort(404)

    user = current_user()

    # Graceful fallback — SSO off / secret unusable: just open the system as-is.
    if not Config.SSO_ENABLED:
        return redirect(base)

    try:
        token = sso_lib.make_token(
            Config.SSO_SECRET,
            user["username"],
            audience=key,
            name=(user.get("full_name") or user["username"]),
            email=(user.get("email") or ""),
            role=user["role"],
            ttl=Config.SSO_TOKEN_TTL,
        )
    except sso_lib.SSOError as exc:
        # Never block the user — degrade to manual login.
        log.warning("SSO token mint failed for system=%s user=%s: %s",
                    key, user.get("username"), exc)
        return redirect(base)

    try:
        log_audit(user["username"], "sso_launch", f"system={key}",
                  request.headers.get("X-Forwarded-For", request.remote_addr or ""))
    except Exception:  # audit must never break the handoff
        pass

    sp_path = Config.SSO_SP_PATH if Config.SSO_SP_PATH.startswith("/") else "/" + Config.SSO_SP_PATH
    url = f"{base}{sp_path}?token={urllib.parse.quote(token)}"
    # Deep link: carry a target path so the system lands on a specific record
    # (e.g. ?next=/assets/123). Only local paths are forwarded; the SP validates
    # again before redirecting, so this can't be turned into an open redirect.
    nxt = (request.args.get("next") or "").strip()
    if nxt.startswith("/") and not nxt.startswith("//") and "://" not in nxt and "\\" not in nxt:
        url += f"&next={urllib.parse.quote(nxt)}"
    return redirect(url)
