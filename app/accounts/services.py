"""
Accounts (Auth) — service layer.

Secure, enumeration-safe account lifecycle on the platform's raw-SQL DB, session
auth, RBAC, audit and SMTP. Tokens are cryptographically random and stored only
as SHA-256 hashes (single-use + expiring). Passwords use werkzeug hashing and are
never stored/logged in plaintext. Every sensitive action writes an auth_events row.
"""
import hashlib
import json
import re
import secrets
import time
import unicodedata
from datetime import datetime, timedelta

from flask import request
from werkzeug.security import generate_password_hash, check_password_hash

from config import Config
from app.accounts import mail_reasons
from app.db import get_db, utcnow, log_audit
from . import constants as C
from . import emails

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
MOBILE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{6,19}$")


# ==========================================================================
# Settings
# ==========================================================================
def get_setting(key, default=None):
    conn = get_db()
    try:
        r = conn.execute("SELECT value FROM auth_settings WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    if r is not None:
        return r["value"]
    return default if default is not None else C.DEFAULT_SETTINGS.get(key)


def get_int(key, default=0):
    try:
        return int(str(get_setting(key, default)).strip())
    except (TypeError, ValueError):
        return default


def get_bool(key, default=False):
    v = str(get_setting(key, "1" if default else "0")).strip().lower()
    return v in ("1", "true", "yes", "on")


def all_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key,value FROM auth_settings").fetchall()
    finally:
        conn.close()
    out = dict(C.DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return out


def save_settings(form, user):
    allowed = set(C.DEFAULT_SETTINGS.keys())
    conn = get_db()
    try:
        for k in allowed:
            if k in form:
                v = str(form.get(k)).strip()
                conn.execute("INSERT OR IGNORE INTO auth_settings (key,value) VALUES (?,?)", (k, v))
                conn.execute("UPDATE auth_settings SET value=? WHERE key=?", (v, k))
        conn.commit()
    finally:
        conn.close()
    audit(C.AuthEvent.ROLE_ASSIGN, actor=user, result="settings_saved")


# ==========================================================================
# helpers
# ==========================================================================
def norm_email(e):
    return (e or "").strip().lower()


def _client_ip():
    try:
        return (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()
    except Exception:  # noqa: BLE001
        return ""


def _ua():
    try:
        return (request.headers.get("User-Agent", "") or "")[:300]
    except Exception:  # noqa: BLE001
        return ""


def _request_id():
    try:
        return request.headers.get("X-Request-Id", "") or ""
    except Exception:  # noqa: BLE001
        return ""


def base_url():
    """Absolute base URL for links. HTTPS forced in production."""
    u = (Config.PUBLIC_URL or Config.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not u:
        try:
            u = request.host_url.rstrip("/")
        except Exception:  # noqa: BLE001
            u = ""
    if Config.IS_PRODUCTION and u.startswith("http://"):
        u = "https://" + u[len("http://"):]
    return u


# ==========================================================================
# Audit
# ==========================================================================
def mail(to_email, built, event, target_id=None, meta=None):
    """Send, and record what actually happened. Returns (ok, reason).

    Every account email goes through here so the audit trail can answer the
    one question support is always asked: "they signed up and got nothing —
    why?" Before this, the send was wrapped in a bare except and the event was
    written as sent regardless, so the log said an email had gone out at the
    moment it had not.

    Never raises: an email that cannot be sent must not roll back the account
    change that triggered it. The failure is recorded instead of thrown.
    """
    reason, ok = None, False
    if not Config.SMTP_HOST:
        reason = "smtp_not_configured"
    else:
        try:
            ok = bool(emails.send(to_email, built))
            if not ok:
                # send_html_to swallows the exception and returns False, so the
                # cause is in the app log; this at least says it was refused.
                reason = "smtp_refused"
        except Exception as exc:  # noqa: BLE001
            reason = type(exc).__name__
    audit(event, target_id=target_id, target_ref=to_email,
          result="ok" if ok else (reason or "failed"),
          meta=dict(meta or {}, delivered=ok))
    return ok, reason


def audit(event, actor=None, target_id=None, target_ref=None, result="ok", meta=None):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO auth_events (event,actor_id,actor_name,target_user_id,target_ref,result,"
            "ip,user_agent,request_id,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (event, (actor or {}).get("id"), (actor or {}).get("username"),
             target_id, target_ref, result, _client_ip(), _ua(), _request_id(),
             json.dumps(meta, ensure_ascii=False) if meta else None, utcnow()))
        conn.commit()
    finally:
        conn.close()
    try:
        log_audit((actor or {}).get("username") or "anonymous", f"auth.{event}",
                  f"{result}:{target_ref or target_id or ''}"[:180], _client_ip())
    except Exception:  # noqa: BLE001
        pass


# ==========================================================================
# Rate limiting (DB-backed, fixed window + progressive block)
# ==========================================================================
def rate_check(bucket, max_count, window_sec, block_sec=0):
    """Returns (allowed, retry_after_sec). Counts an attempt when allowed."""
    conn = get_db()
    try:
        now = time.time()
        row = conn.execute("SELECT * FROM auth_ratelimit WHERE bucket=? ORDER BY id DESC LIMIT 1",
                           (bucket,)).fetchone()
        if not row:
            conn.execute("INSERT INTO auth_ratelimit (bucket,count,window_start,last_at) VALUES (?,?,?,?)",
                         (bucket, 1, str(now), str(now)))
            conn.commit()
            return True, 0
        blocked_until = float(row["blocked_until"] or 0)
        if blocked_until and now < blocked_until:
            return False, int(blocked_until - now)
        window_start = float(row["window_start"] or 0)
        if now - window_start >= window_sec:
            conn.execute("UPDATE auth_ratelimit SET count=1, window_start=?, last_at=?, blocked_until=NULL "
                         "WHERE id=?", (str(now), str(now), row["id"]))
            conn.commit()
            return True, 0
        count = (row["count"] or 0) + 1
        if count > max_count:
            bu = now + (block_sec or window_sec)
            conn.execute("UPDATE auth_ratelimit SET count=?, last_at=?, blocked_until=? WHERE id=?",
                         (count, str(now), str(bu), row["id"]))
            conn.commit()
            return False, int(bu - now)
        conn.execute("UPDATE auth_ratelimit SET count=?, last_at=? WHERE id=?", (count, str(now), row["id"]))
        conn.commit()
        return True, 0
    finally:
        conn.close()


# ==========================================================================
# Password policy
# ==========================================================================
def password_policy():
    return {
        "min_length": get_int("pw_min_length", C.PW_MIN_LENGTH),
        "upper": get_bool("pw_require_upper", C.PW_REQUIRE_UPPER),
        "lower": get_bool("pw_require_lower", C.PW_REQUIRE_LOWER),
        "digit": get_bool("pw_require_digit", C.PW_REQUIRE_DIGIT),
        "special": get_bool("pw_require_special", C.PW_REQUIRE_SPECIAL),
        "history": get_int("pw_history_count", C.PW_HISTORY_COUNT),
    }


def validate_password(pw, ctx=None):
    """Return a list of i18n error keys. ctx: {email, employee_id, name}."""
    pw = pw or ""
    pol = password_policy()
    errs = []
    if len(pw) < pol["min_length"]:
        errs.append("acc.err.pw_short")
    if pw != pw.strip():
        errs.append("acc.err.pw_spaces")
    if pol["upper"] and not re.search(r"[A-Z]", pw):
        errs.append("acc.err.pw_upper")
    if pol["lower"] and not re.search(r"[a-z]", pw):
        errs.append("acc.err.pw_lower")
    if pol["digit"] and not re.search(r"[0-9]", pw):
        errs.append("acc.err.pw_digit")
    if pol["special"] and not any(c in C.SPECIALS for c in pw):
        errs.append("acc.err.pw_special")
    low = pw.lower()
    if low in C.COMMON_PASSWORDS or _too_simple(low):
        errs.append("acc.err.pw_weak")
    ctx = ctx or {}
    for field in ("email", "employee_id", "name"):
        val = str(ctx.get(field) or "").strip().lower()
        if field == "email":
            val = val.split("@")[0]
        parts = [val] + val.split() if field == "name" else [val]
        for p in parts:
            if p and len(p) >= 3 and p in low:
                errs.append("acc.err.pw_contains_pii")
                break
        else:
            continue
        break
    return list(dict.fromkeys(errs))


def _too_simple(low):
    if len(set(low)) <= 2:
        return True
    seqs = "0123456789abcdefghijklmnopqrstuvwxyzqwertyuiopasdfghjklzxcvbnm"
    return any(low[i:i + 5] in seqs for i in range(max(0, len(low) - 4)))


def password_strength(pw):
    """0..4 + label key (client also computes; server is source of truth)."""
    pw = pw or ""
    score = 0
    if len(pw) >= 12:
        score += 1
    if len(pw) >= 16:
        score += 1
    classes = sum(bool(re.search(p, pw)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^\w]"))
    score += min(2, max(0, classes - 1))
    if pw.lower() in C.COMMON_PASSWORDS or _too_simple(pw.lower()):
        score = 0
    score = min(4, score)
    return score, ["acc.pw.very_weak", "acc.pw.weak", "acc.pw.fair", "acc.pw.good", "acc.pw.strong"][score]


def _pw_in_history(conn, user_id, pw, current_hash=None):
    n = password_policy()["history"]
    if current_hash and check_password_hash(current_hash, pw):
        return True
    rows = conn.execute("SELECT password_hash FROM auth_password_history WHERE user_id=? "
                        "ORDER BY id DESC LIMIT ?", (user_id, n)).fetchall()
    return any(check_password_hash(r["password_hash"], pw) for r in rows)


def _push_history(conn, user_id, pw_hash):
    conn.execute("INSERT INTO auth_password_history (user_id,password_hash,created_at) VALUES (?,?,?)",
                 (user_id, pw_hash, utcnow()))
    keep = password_policy()["history"]
    old = conn.execute("SELECT id FROM auth_password_history WHERE user_id=? ORDER BY id DESC",
                       (user_id,)).fetchall()
    for r in old[keep:]:
        conn.execute("DELETE FROM auth_password_history WHERE id=?", (r["id"],))


# ==========================================================================
# Token service  (random, hashed, single-use, expiring)
# ==========================================================================
def _hash_token(raw):
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def issue_token(conn, user_id, token_type, ttl_min):
    # invalidate prior live tokens of the same type for this user
    conn.execute("UPDATE auth_tokens SET invalidated_at=? WHERE user_id=? AND token_type=? "
                 "AND used_at IS NULL AND invalidated_at IS NULL", (utcnow(), user_id, token_type))
    raw = secrets.token_urlsafe(32)
    exp = (datetime.utcnow() + timedelta(minutes=ttl_min)).isoformat(timespec="seconds")
    conn.execute("INSERT INTO auth_tokens (user_id,token_type,token_hash,issued_at,expires_at,ip,user_agent) "
                 "VALUES (?,?,?,?,?,?,?)", (user_id, token_type, _hash_token(raw), utcnow(), exp,
                                           _client_ip(), _ua()))
    return raw


def peek_token(raw, token_type):
    """Return (user_row, reason). reason in {ok, invalid, expired, used}. Does NOT consume."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND token_type=?",
                           (_hash_token(raw), token_type)).fetchone()
        if not row:
            return None, "invalid"
        if row["invalidated_at"]:
            return None, "used"
        if row["used_at"]:
            return None, "used"
        if _expired(row["expires_at"]):
            return None, "expired"
        u = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        return (dict(u) if u else None), ("ok" if u else "invalid")
    finally:
        conn.close()


def consume_token(conn, raw, token_type):
    """Atomically mark a valid token used; return (user_id, reason)."""
    row = conn.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND token_type=?",
                       (_hash_token(raw), token_type)).fetchone()
    if not row:
        return None, "invalid"
    if row["invalidated_at"] or row["used_at"]:
        return None, "used"
    if _expired(row["expires_at"]):
        return None, "expired"
    conn.execute("UPDATE auth_tokens SET used_at=? WHERE id=? AND used_at IS NULL", (utcnow(), row["id"]))
    return row["user_id"], "ok"


def _expired(iso):
    try:
        return datetime.utcnow() > datetime.fromisoformat(str(iso))
    except Exception:  # noqa: BLE001
        return True


# ==========================================================================
# Org options
# ==========================================================================
def org_options():
    conn = get_db()
    try:
        rows = conn.execute("SELECT kind,value FROM auth_org_options WHERE active=1 ORDER BY kind,sort,value").fetchall()
    finally:
        conn.close()
    out = {k: [] for k in C.ORG_KINDS}
    for r in rows:
        out.setdefault(r["kind"], []).append(r["value"])
    return out


# ==========================================================================
# Registration
# ==========================================================================
def register(form):
    """Validate + create a pending account, issue a verification token, email it.
    Returns (ok, errors_dict, neutral_msg_key). Never grants admin. Enumeration-safe:
    a duplicate returns a neutral success to the applicant, an internal audit notes it."""
    f = {k: (form.get(k) or "").strip() for k in
         ("first_name", "last_name", "email", "employee_id", "mobile", "company",
          "department", "job_title", "location", "manager", "preferred_language")}
    pw = form.get("password") or ""
    pw2 = form.get("confirm_password") or ""
    errors = {}

    if not get_bool("registration_enabled", True):
        return False, {"_": "acc.err.registration_disabled"}, None

    if not f["first_name"]:
        errors["first_name"] = "acc.err.required"
    if not f["last_name"]:
        errors["last_name"] = "acc.err.required"
    email = norm_email(f["email"])
    if not EMAIL_RE.match(email):
        errors["email"] = "acc.err.email_invalid"
    else:
        allow = (get_setting("email_domain_allowlist", "") or "").strip()
        if allow:
            doms = [d.strip().lower() for d in allow.split(",") if d.strip()]
            if email.split("@")[-1] not in doms:
                errors["email"] = "acc.err.email_domain"
    if not f["employee_id"]:
        errors["employee_id"] = "acc.err.required"
    if f["mobile"] and not MOBILE_RE.match(f["mobile"]):
        errors["mobile"] = "acc.err.mobile_invalid"
    if not f["company"]:
        errors["company"] = "acc.err.required"
    if not f["department"]:
        errors["department"] = "acc.err.required"
    if not f["job_title"]:
        errors["job_title"] = "acc.err.required"
    if get_bool("require_terms", True) and not form.get("accept_terms"):
        errors["accept_terms"] = "acc.err.accept_terms"
    if get_bool("require_privacy", True) and not form.get("accept_privacy"):
        errors["accept_privacy"] = "acc.err.accept_privacy"
    if pw != pw2:
        errors["confirm_password"] = "acc.err.pw_mismatch"
    name = f"{f['first_name']} {f['last_name']}".strip()
    pw_errs = validate_password(pw, {"email": email, "employee_id": f["employee_id"], "name": name})
    if pw_errs:
        errors["password"] = pw_errs[0]

    if errors:
        return False, errors, None

    lang = f["preferred_language"] if f["preferred_language"] in ("en", "ar", "tr") else "en"
    mode = get_setting("registration_mode", C.RegistrationMode.EMAIL_THEN_ADMIN)

    conn = get_db()
    try:
        # invite-only gate
        if mode == C.RegistrationMode.INVITE_ONLY:
            inv = conn.execute("SELECT id FROM auth_invites WHERE LOWER(email)=? AND used_at IS NULL",
                               (email,)).fetchone()
            if not inv:
                audit(C.AuthEvent.REGISTER, result="invite_required", target_ref=email)
                return True, {}, "acc.msg.registration_submitted"   # neutral (don't reveal invite policy)

        # duplicate detection — neutral response, internal audit
        dupe = conn.execute("SELECT id FROM users WHERE LOWER(username)=? OR LOWER(email)=? OR employee_id=?",
                            (email, email, f["employee_id"])).fetchone()
        if dupe:
            audit(C.AuthEvent.REGISTER, result="duplicate", target_id=dupe["id"], target_ref=email)
            return True, {}, "acc.msg.registration_submitted"

        default_role = get_setting("default_role", "normal_user") or "normal_user"
        now = utcnow()
        conn.execute(
            "INSERT INTO users (username,password_hash,full_name,email,role,lang_pref,theme_pref,is_active,"
            "created_at,employee_id,mobile,company,department,job_title,location,manager,account_status,"
            "registration_source,last_password_change_at,terms_accepted_at,privacy_accepted_at,session_epoch) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (email, generate_password_hash(pw), name, email, default_role, lang, "light", 0, now,
             f["employee_id"], f["mobile"] or None, f["company"], f["department"], f["job_title"],
             f["location"] or None, f["manager"] or None, C.AccountStatus.PENDING_VERIFICATION,
             "self_signup", now, now, now, 0))
        conn.commit()
        u = conn.execute("SELECT * FROM users WHERE LOWER(username)=?", (email,)).fetchone()
        uid = u["id"]
        _push_history(conn, uid, u["password_hash"])
        raw = issue_token(conn, uid, C.TokenType.VERIFY_EMAIL, get_int("verify_ttl_min", C.VERIFY_TTL_MIN))
        conn.commit()
    finally:
        conn.close()

    url = f"{base_url()}/verify-email?token={raw}"
    audit(C.AuthEvent.REGISTER, target_id=uid, target_ref=email, result="ok", meta={"mode": mode})
    mail(email, emails.verify_email({"full_name": name}, url, lang),
         C.AuthEvent.VERIFY_SENT, target_id=uid, meta={"mode": mode})
    return True, {}, "acc.msg.registration_submitted"


# ==========================================================================
# Email verification
# ==========================================================================
def verify_email(raw):
    """Consume a verify token, advance the account. Returns a state key."""
    conn = get_db()
    try:
        uid, reason = consume_token(conn, raw, C.TokenType.VERIFY_EMAIL)
        if reason != "ok":
            conn.commit()
            audit(C.AuthEvent.VERIFY_FAIL, result=reason)
            return {"invalid": "verify_invalid", "expired": "verify_expired",
                    "used": "verify_used"}.get(reason, "verify_invalid")
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            conn.commit()
            return "verify_invalid"
        if u["account_status"] == C.AccountStatus.ACTIVE:
            conn.commit()
            return "verify_already"
        mode = get_setting("registration_mode", C.RegistrationMode.EMAIL_THEN_ADMIN)
        now = utcnow()
        needs_approval = mode in (C.RegistrationMode.EMAIL_THEN_ADMIN, C.RegistrationMode.ADMIN_ONLY)
        if needs_approval:
            conn.execute("UPDATE users SET email_verified_at=?, account_status=? WHERE id=?",
                         (now, C.AccountStatus.PENDING_APPROVAL, uid))
        else:
            conn.execute("UPDATE users SET email_verified_at=?, account_status=?, is_active=1 WHERE id=?",
                         (now, C.AccountStatus.ACTIVE, uid))
        conn.commit()
    finally:
        conn.close()
    lang = (u["lang_pref"] or "en")
    try:
        emails.send(u["email"], emails.registration_received(dict(u), needs_approval, lang))
    except Exception:  # noqa: BLE001
        pass
    if needs_approval:
        _notify_admins_pending(dict(u))
    audit(C.AuthEvent.VERIFY_OK, target_id=uid, target_ref=u["email"],
          result="pending_approval" if needs_approval else "active")
    return "verify_pending_approval" if needs_approval else "verify_active"


def resend_verification(identifier):
    """Neutral. Rate-limited + cooldown. Re-issues + resends if a pending account matches."""
    ident = norm_email(identifier)
    ok_ip, _ = rate_check(f"resend:ip:{_client_ip()}", C.RESEND_MAX_PER_HOUR, 3600, 3600)
    ok_id, _ = rate_check(f"resend:{ident}", C.RESEND_MAX_PER_HOUR, 3600, 3600)
    if not (ok_ip and ok_id):
        audit(C.AuthEvent.RATE_LIMITED, result="resend", target_ref=ident)
        return "neutral"
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM users WHERE (LOWER(email)=? OR LOWER(username)=?) "
                         "AND account_status=?", (ident, ident, C.AccountStatus.PENDING_VERIFICATION)).fetchone()
        if u:
            raw = issue_token(conn, u["id"], C.TokenType.VERIFY_EMAIL, get_int("verify_ttl_min", C.VERIFY_TTL_MIN))
            conn.commit()
            url = f"{base_url()}/verify-email?token={raw}"
            try:
                emails.send(u["email"], emails.verify_email(dict(u), url, u["lang_pref"] or "en"))
            except Exception:  # noqa: BLE001
                pass
            audit(C.AuthEvent.RESEND_VERIFY, target_id=u["id"], target_ref=ident)
    finally:
        conn.close()
    return "neutral"


# ==========================================================================
# Forgot / reset password
# ==========================================================================
def forgot_password(identifier):
    """Always neutral. Rate-limited. Issues a reset token to an eligible LOCAL
    account; SSO accounts get a safe guidance email instead."""
    ident = norm_email(identifier)
    ok_ip, _ = rate_check(f"forgot:ip:{_client_ip()}", get_int("forgot_max_per_hour", C.FORGOT_MAX_PER_HOUR) * 3, 3600, 1800)
    ok_id, _ = rate_check(f"forgot:{ident}", get_int("forgot_max_per_hour", C.FORGOT_MAX_PER_HOUR), 3600, 1800)
    audit(C.AuthEvent.FORGOT_REQUEST, target_ref=ident, result="requested")
    if not (ok_ip and ok_id):
        audit(C.AuthEvent.RATE_LIMITED, result="forgot", target_ref=ident)
        return  # still neutral to the caller
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM users WHERE LOWER(email)=? OR LOWER(username)=? OR employee_id=?",
                         (ident, ident, identifier.strip())).fetchone()
        if not u:
            return
        u = dict(u)
        # SSO-only accounts (no local password use) get guidance instead of a reset link
        if Config.SSO_ENABLED and str(u.get("registration_source") or "") == "sso":
            try:
                emails.send(u["email"], emails.reset_sso_notice(u, u["lang_pref"] or "en"))
            except Exception:  # noqa: BLE001
                pass
            audit(C.AuthEvent.RESET_SENT, target_id=u["id"], target_ref=ident, result="sso_notice")
            return
        if u["account_status"] not in (C.AccountStatus.ACTIVE, C.AccountStatus.LOCKED):
            return  # don't issue resets for pending/suspended/rejected — neutral
        raw = issue_token(conn, u["id"], C.TokenType.PASSWORD_RESET, get_int("reset_ttl_min", C.RESET_TTL_MIN))
        conn.commit()
        url = f"{base_url()}/reset-password?token={raw}"
        try:
            emails.send(u["email"], emails.reset_request(u, url, get_int("reset_ttl_min", C.RESET_TTL_MIN),
                                                         u["lang_pref"] or "en"))
        except Exception:  # noqa: BLE001
            pass
        audit(C.AuthEvent.RESET_SENT, target_id=u["id"], target_ref=ident)
    finally:
        conn.close()


def reset_token_state(raw):
    """For the GET page: (state, user_or_none). state in ok/invalid/expired/used."""
    u, reason = peek_token(raw, C.TokenType.PASSWORD_RESET)
    return reason, u


def reset_password(raw, pw, pw2):
    """Consume a reset token, apply the new password, invalidate sessions & tokens,
    record history, email confirmation. Returns (ok, error_keys)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM auth_tokens WHERE token_hash=? AND token_type=?",
                           (_hash_token(raw), C.TokenType.PASSWORD_RESET)).fetchone()
        if not row or row["used_at"] or row["invalidated_at"] or _expired(row["expires_at"]):
            return False, ["acc.err.reset_link"]
        u = conn.execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if not u:
            return False, ["acc.err.reset_link"]
        u = dict(u)
        if pw != pw2:
            return False, ["acc.err.pw_mismatch"]
        errs = validate_password(pw, {"email": u.get("email"), "employee_id": u.get("employee_id"),
                                      "name": u.get("full_name")})
        if errs:
            return False, errs
        if _pw_in_history(conn, u["id"], pw, u.get("password_hash")):
            return False, ["acc.err.pw_reuse"]
        # apply
        new_hash = generate_password_hash(pw)
        conn.execute("UPDATE users SET password_hash=?, last_password_change_at=?, failed_login_count=0, "
                     "locked_until=NULL, session_epoch=COALESCE(session_epoch,0)+1, "
                     "account_status=CASE WHEN account_status='locked' THEN 'active' ELSE account_status END "
                     "WHERE id=?", (new_hash, utcnow(), u["id"]))
        _push_history(conn, u["id"], new_hash)
        conn.execute("UPDATE auth_tokens SET used_at=? WHERE id=?", (utcnow(), row["id"]))
        # invalidate all other live reset tokens
        conn.execute("UPDATE auth_tokens SET invalidated_at=? WHERE user_id=? AND token_type=? "
                     "AND used_at IS NULL AND invalidated_at IS NULL",
                     (utcnow(), u["id"], C.TokenType.PASSWORD_RESET))
        conn.commit()
    finally:
        conn.close()
    try:
        emails.send(u["email"], emails.password_changed(u, u.get("lang_pref") or "en"))
    except Exception:  # noqa: BLE001
        pass
    audit(C.AuthEvent.RESET_OK, target_id=u["id"], target_ref=u.get("email"))
    audit(C.AuthEvent.PASSWORD_CHANGED, target_id=u["id"], target_ref=u.get("email"))
    return True, []


# ==========================================================================
# Login helpers (used by routes/auth.py — additive, does not replace login)
# ==========================================================================
def find_login_user(identifier):
    ident = (identifier or "").strip()
    low = ident.lower()
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM users WHERE LOWER(username)=? OR LOWER(email)=? OR employee_id=?",
                            (low, low, ident)).fetchone()
    finally:
        conn.close()


def login_block_reason(u):
    """Return an i18n guidance key if the authenticated account may not start a
    session (status/lock), else None. Only called AFTER the password matched."""
    if u is None:
        return None
    status = u["account_status"] if "account_status" in u.keys() else "active"
    locked_until = u["locked_until"] if "locked_until" in u.keys() else None
    if locked_until and not _expired_ok(locked_until):
        return "acc.login.locked"
    return {
        C.AccountStatus.PENDING_VERIFICATION: "acc.login.unverified",
        C.AccountStatus.EMAIL_VERIFIED: "acc.login.pending_approval",
        C.AccountStatus.PENDING_APPROVAL: "acc.login.pending_approval",
        C.AccountStatus.SUSPENDED: "acc.login.suspended",
        C.AccountStatus.REJECTED: "acc.login.rejected",
        C.AccountStatus.LOCKED: "acc.login.locked",
        C.AccountStatus.ARCHIVED: "acc.login.suspended",
    }.get(status) if status and status != C.AccountStatus.ACTIVE else None


def _expired_ok(iso):
    try:
        return datetime.utcnow() > datetime.fromisoformat(str(iso))
    except Exception:  # noqa: BLE001
        return True


def on_login_success(uid):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET last_login_at=?, failed_login_count=0, locked_until=NULL WHERE id=?",
                     (utcnow(), uid))
        conn.commit()
    finally:
        conn.close()


def current_session_epoch(uid):
    conn = get_db()
    try:
        r = conn.execute("SELECT session_epoch FROM users WHERE id=?", (uid,)).fetchone()
        return (r["session_epoch"] if r and r["session_epoch"] is not None else 0)
    finally:
        conn.close()


# ==========================================================================
# Administration  (routes enforce the users_* permission; these do the work)
# ==========================================================================
_ACC_COLS = ("id,username,full_name,email,employee_id,mobile,company,department,job_title,location,"
             "manager,role,account_status,registration_source,scope_department,scope_section,created_at,"
             "email_verified_at,approved_by,approved_at,rejected_by,rejected_at,rejection_reason,"
             "last_login_at,locked_until,failed_login_count,lang_pref")


def list_registrations(status=None, q=None, limit=500):
    conn = get_db()
    try:
        sql = f"SELECT {_ACC_COLS} FROM users WHERE 1=1"
        p = []
        if status and status != "all":
            sql += " AND account_status=?"; p.append(status)
        elif not status:
            sql += " AND account_status IN (?,?,?)"
            p += [C.AccountStatus.PENDING_VERIFICATION, C.AccountStatus.EMAIL_VERIFIED,
                  C.AccountStatus.PENDING_APPROVAL]
        if q:
            sql += (" AND (LOWER(full_name) LIKE ? OR LOWER(email) LIKE ? OR LOWER(employee_id) LIKE ? "
                    "OR LOWER(company) LIKE ? OR LOWER(department) LIKE ?)")
            p += [f"%{q.lower()}%"] * 5
        sql += " ORDER BY created_at DESC LIMIT ?"; p.append(limit)
        return [dict(r) for r in conn.execute(sql, p).fetchall()]
    finally:
        conn.close()


def registration_mail_log(limit=200, only_problems=False, q=None):
    """Recent registration/verification attempts and whether an email went out.

    Reads auth_events rather than the users table, because the interesting rows
    are the ones that created NO user: a duplicate address, an invite-only
    refusal. Those are invisible in a list of accounts, which is exactly why
    "they signed up and nothing happened" has been unanswerable.
    """
    conn = get_db()
    try:
        sql = ("SELECT id, event, target_ref, target_user_id, result, ip, created_at "
               "FROM auth_events WHERE event IN (?,?,?,?)")
        p = [C.AuthEvent.REGISTER, C.AuthEvent.VERIFY_SENT,
             C.AuthEvent.RESEND_VERIFY, C.AuthEvent.RATE_LIMITED]
        if only_problems:
            sql += " AND result != ?"; p.append("ok")
        if q:
            sql += " AND LOWER(target_ref) LIKE ?"; p.append("%%%s%%" % q.strip().lower())
        sql += " ORDER BY id DESC LIMIT ?"; p.append(limit)
        rows = [dict(r) for r in conn.execute(sql, p).fetchall()]
    finally:
        conn.close()
    for r in rows:
        r["label"], r["meaning"], r["todo"] = mail_reasons.explain(r["result"])
        r["delivered"] = mail_reasons.delivered(r["result"])
    return rows


def status_counts():
    conn = get_db()
    try:
        rows = conn.execute("SELECT account_status s, COUNT(*) c FROM users GROUP BY account_status").fetchall()
        return {r["s"]: r["c"] for r in rows}
    finally:
        conn.close()


def get_account(uid):
    conn = get_db()
    try:
        r = conn.execute(f"SELECT {_ACC_COLS} FROM users WHERE id=?", (uid,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def security_history(uid, limit=100):
    conn = get_db()
    try:
        rows = conn.execute("SELECT event,actor_name,result,ip,created_at,meta_json FROM auth_events "
                            "WHERE target_user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _role_ok(admin, role):
    from app.security import effective_roles
    if role not in effective_roles():
        return False
    if role == "super_admin" and (admin or {}).get("role") != "super_admin":
        return False   # only a super admin may grant super admin
    return True


def approve(admin, uid, role=None, scope_department=None, scope_section=None):
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return False, "not_found"
        u = dict(u)
        role = role or get_setting("default_role", "normal_user") or "normal_user"
        if not _role_ok(admin, role):
            return False, "forbidden_role"
        now = utcnow()
        conn.execute("UPDATE users SET account_status=?, is_active=1, role=?, scope_department=?, "
                     "scope_section=?, approved_by=?, approved_at=?, rejected_by=NULL, rejected_at=NULL, "
                     "rejection_reason=NULL, email_verified_at=COALESCE(email_verified_at,?) WHERE id=?",
                     (C.AccountStatus.ACTIVE, role, scope_department or None, scope_section or None,
                      (admin or {}).get("username"), now, now, uid))
        conn.commit()
    finally:
        conn.close()
    audit(C.AuthEvent.APPROVE, actor=admin, target_id=uid, target_ref=u["email"], meta={"role": role})
    mail(u["email"], emails.approved(u, f"{base_url()}/login", u.get("lang_pref") or "en"),
         C.AuthEvent.APPROVE, target_id=uid, meta={"stage": "approved_notice"})
    audit(C.AuthEvent.ROLE_ASSIGN, actor=admin, target_id=uid,
          meta={"role": role, "dept": scope_department, "section": scope_section})
    return True, None


def reject(admin, uid, reason):
    if not (reason and reason.strip()):
        return False, "reason_required"
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return False, "not_found"
        u = dict(u)
        now = utcnow()
        conn.execute("UPDATE users SET account_status=?, is_active=0, rejected_by=?, rejected_at=?, "
                     "rejection_reason=? WHERE id=?",
                     (C.AccountStatus.REJECTED, (admin or {}).get("username"), now, reason.strip(), uid))
        conn.commit()
    finally:
        conn.close()
    try:
        emails.send(u["email"], emails.rejected(u, reason.strip(), u.get("lang_pref") or "en"))
    except Exception:  # noqa: BLE001
        pass
    audit(C.AuthEvent.REJECT, actor=admin, target_id=uid, target_ref=u["email"])
    return True, None


def _set_status(admin, uid, status, is_active, event):
    conn = get_db()
    try:
        u = conn.execute("SELECT email,username FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return False, "not_found"
        # bump session_epoch so a suspended/locked user's live sessions end
        conn.execute("UPDATE users SET account_status=?, is_active=?, "
                     "session_epoch=COALESCE(session_epoch,0)+1 WHERE id=?", (status, is_active, uid))
        conn.commit()
    finally:
        conn.close()
    audit(event, actor=admin, target_id=uid, target_ref=u["email"] if u else None)
    return True, None


def suspend(admin, uid):
    return _set_status(admin, uid, C.AccountStatus.SUSPENDED, 0, C.AuthEvent.SUSPEND)


def reactivate(admin, uid):
    return _set_status(admin, uid, C.AccountStatus.ACTIVE, 1, C.AuthEvent.REACTIVATE)


def unlock(admin, uid):
    conn = get_db()
    try:
        u = conn.execute("SELECT email,account_status FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return False, "not_found"
        new_status = C.AccountStatus.ACTIVE if u["account_status"] == C.AccountStatus.LOCKED else u["account_status"]
        conn.execute("UPDATE users SET locked_until=NULL, failed_login_count=0, account_status=? WHERE id=?",
                     (new_status, uid))
        conn.commit()
    finally:
        conn.close()
    audit(C.AuthEvent.UNLOCK, actor=admin, target_id=uid, target_ref=u["email"])
    return True, None


def assign_role(admin, uid, role, scope_department=None, scope_section=None):
    if not _role_ok(admin, role):
        return False, "forbidden_role"
    conn = get_db()
    try:
        u = conn.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return False, "not_found"
        conn.execute("UPDATE users SET role=?, scope_department=?, scope_section=? WHERE id=?",
                     (role, scope_department or None, scope_section or None, uid))
        conn.commit()
    finally:
        conn.close()
    audit(C.AuthEvent.ROLE_ASSIGN, actor=admin, target_id=uid, target_ref=u["email"],
          meta={"role": role, "dept": scope_department, "section": scope_section})
    return True, None


def admin_initiate_reset(admin, uid):
    conn = get_db()
    try:
        u = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return False, "not_found"
        u = dict(u)
        raw = issue_token(conn, uid, C.TokenType.PASSWORD_RESET, get_int("reset_ttl_min", C.RESET_TTL_MIN))
        conn.commit()
    finally:
        conn.close()
    try:
        emails.send(u["email"], emails.reset_request(u, f"{base_url()}/reset-password?token={raw}",
                                                     get_int("reset_ttl_min", C.RESET_TTL_MIN),
                                                     u.get("lang_pref") or "en"))
    except Exception:  # noqa: BLE001
        pass
    audit(C.AuthEvent.RESET_SENT, actor=admin, target_id=uid, target_ref=u["email"], result="admin_initiated")
    return True, None


def _notify_admins_pending(u):
    """Alert account approvers (in-app bell) that a registration awaits approval."""
    conn = get_db()
    try:
        approvers = _users_with_perm(conn, "users_approve")
        now = utcnow()
        label = f"{u.get('full_name') or u.get('email')}"
        for uname in approvers:
            conn.execute("INSERT INTO notifications (severity, module, title, message, target_user, link, created_at) "
                         "VALUES (?,?,?,?,?,?,?)",
                         ("warning", "accounts", "New registration awaiting approval",
                          f"{label} verified their email and awaits administrator approval.",
                          uname, "/admin/registrations", now))
        conn.commit()
    finally:
        conn.close()


def _users_with_perm(conn, perm):
    from app.security import user_has_permission
    rows = conn.execute("SELECT username, role, extra_perms FROM users WHERE is_active=1").fetchall()
    return [r["username"] for r in rows
            if user_has_permission({"role": r["role"], "extra_perms": r["extra_perms"]}, perm)]
