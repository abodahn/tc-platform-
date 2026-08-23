"""
TC Platform — Procurement & Approvals service layer.

All workflow logic lives here so routes stay thin: creating a PR, building the
threshold-driven approval ladder, stamping an approver's digital signature,
advancing/rejecting, and auto-generating the Purchase Order on final approval.
Each mutating action writes an immutable audit event and surfaces the right
platform-bell notification.
"""
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from app.db import get_db
from app.security import has_permission, effective_roles
from app.approvals import constants as C
from app.approvals.constants import (
    build_ladder, ladder_rungs, rungs_from_stages, stage_label, STAGE_ROLES,
    PR_STATUSES, LADDER, APPROVAL_MATRIX, DEPARTMENTS,
    VALUE_STAGES, DEMAND_STAGES, PRICING_GATE_STAGE)

log = logging.getLogger("tc.procurement")

# Wording for the two facts ladder_signer_health() now returns about a rung whose
# configured roles do not hold its DOAM authority level. Kept here rather than in
# app/static/i18n/*.json, which this module does not own; the same pattern as
# app/approvals/catalogue.py. Read by /governance/findings.
I18N = {
    "gov.find.level": ("Authority level required",
                       "مستوى الصلاحية المطلوب",
                       "Gereken yetki seviyesi"),
    "gov.find.underlevel": (
        "Below the authority level this rung commits at, so it cannot sign:",
        "أقل من مستوى الصلاحية المطلوب لهذه الدرجة، لذلك لا يمكنه التوقيع:",
        "Bu basamağın taahhüt ettiği yetki seviyesinin altında olduğu için imzalayamaz:"),
}


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _year():
    return datetime.now(timezone.utc).year


def _age_hours(ts):
    """Hours elapsed since an ISO 'YYYY-MM-DD HH:MM:SS' timestamp (UTC), or None."""
    if not ts:
        return None
    try:
        t = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 3600.0)


def doc_no(prefix, n):
    return f"{prefix}-{_year()}-{n:06d}"


# --------------------------------------------------------------------------
# audit + notifications
# --------------------------------------------------------------------------
def audit(conn, pr_id, actor, action, detail="", ip=None):
    conn.execute(
        "INSERT INTO pr_events (pr_id, actor, action, detail, ip, created_at) "
        "VALUES (?,?,?,?,?,?)", (pr_id, actor, action, detail, ip, _now()))


def bell(conn, severity, title, message, link=None):
    """Broadcast an event on the platform notifications bell (module=procurement)."""
    conn.execute(
        "INSERT INTO notifications (severity, module, title, message, link, created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "procurement", title, message, link, _now()))


def notify_users(conn, usernames, severity, title, message, link=None):
    """Send a per-user bell notification to each username (deduped, skips blanks),
    and, when SMTP is configured, an email to those who allow it."""
    targets = {u for u in usernames if u}
    for u in targets:
        conn.execute(
            "INSERT INTO notifications (severity, module, title, message, target_user, link, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (severity, "procurement", title, message, u, link, _now()))
    try:
        _email_targets(conn, targets, title, message, link)
    except Exception:
        pass  # email is best-effort; never block the workflow


def _email_enabled(prefs):
    """Per-user email preference (default ON if unset)."""
    if not prefs:
        return True
    try:
        import json
        p = json.loads(prefs) if isinstance(prefs, str) else prefs
        return bool(p.get("email", True))
    except Exception:
        return True


def _email_targets(conn, usernames, title, message, link):
    from config import Config
    if not getattr(Config, "SMTP_HOST", "") or not usernames:
        return
    ph = ",".join(["?"] * len(usernames))
    rows = conn.execute(
        f"SELECT email, notif_prefs FROM users WHERE username IN ({ph}) AND is_active=1",
        tuple(usernames)).fetchall()
    recips = [r["email"] for r in rows if r["email"] and _email_enabled(r["notif_prefs"])]
    if not recips:
        return
    base = (getattr(Config, "PUBLIC_URL", "") or "").rstrip("/")
    url = (base + link) if (base and link) else (link or "")
    from app.services.alerts import send_email_to
    body = f"{message}\n\n{('Open: ' + url) if url else 'Open the TC Platform to review.'}\n\n— TC Platform"
    send_email_to(recips, f"[TC Platform] {title}", body)


# --------------------------------------------------------------------------
# Workflow & Governance — DB override -> constants.py default
# --------------------------------------------------------------------------
# Every accessor below reads a tiny override table on each call (no cache), so a
# setting an admin saves takes effect on the next request with no restart. When
# the row is absent, blank, or fails coercion, the constant is used — which is
# exactly why a database with NO override rows behaves as the module always has.
def _raw_setting(conn, key):
    """Stored string for a knob, or None. Tolerates a pre-migration database
    (table not created yet) so the engine falls back to the constant instead of
    raising on a money path."""
    try:
        r = conn.execute("SELECT value FROM proc_settings WHERE key=?", (key,)).fetchone()
    except Exception:
        return None
    return r["value"] if r else None


def num_setting(conn, key):
    """Numeric knob: the override coerced against its spec, else the constant.
    Blank, non-numeric, NaN, infinity and out-of-bounds all fall back, so a bad
    entry can never disable a gate or widen a payment cap."""
    spec = C.WORKFLOW_SETTINGS.get(key) or {}
    default = spec.get("default")
    raw = _raw_setting(conn, key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):     # NaN / +-inf
        return default
    lo, hi = spec.get("min"), spec.get("max")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        return default
    return int(v) if spec.get("kind") == "int" else v


_TRUE_WORDS = {"1", "true", "yes", "on"}
_FALSE_WORDS = {"0", "false", "no", "off"}


def bool_setting(conn, key):
    """Boolean knob. Only an unambiguous word counts; anything else (blank,
    "maybe", a number) falls back to the constant rather than silently turning a
    control off."""
    raw = _raw_setting(conn, key)
    s = str(raw).strip().lower() if raw is not None else ""
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return bool((C.WORKFLOW_SETTINGS.get(key) or {}).get("default"))


def stage_roles_map(conn=None):
    """{stage: set(role_keys)} — constants.STAGE_ROLES with any admin override
    from proc_stage_meta applied. `role` may hold a comma-separated list.

    Unknown role keys are dropped, and if nothing valid remains the constant is
    kept: a typo must never leave a stage that nobody but an admin can sign."""
    own = conn is None
    if own:
        conn = get_db()
    try:
        try:
            rows = conn.execute("SELECT stage, role FROM proc_stage_meta "
                                "WHERE role IS NOT NULL AND role<>''").fetchall()
        except Exception:
            rows = []                       # pre-migration database -> constants
    finally:
        if own:
            conn.close()
    out = {k: set(v) for k, v in STAGE_ROLES.items()}
    valid = set(effective_roles())
    for r in rows:
        if r["stage"] not in LADDER:
            continue                        # only ladder stages are ever signed
        picked = {x.strip() for x in str(r["role"] or "").split(",") if x.strip()} & valid
        if picked:
            out[r["stage"]] = picked
    return out


def stage_role(stage):
    """Effective roles that sign `stage` (override -> constant)."""
    return stage_roles_map().get(stage, set())


def role_holders(conn, roles):
    """Usernames who may sign on behalf of any of `roles`: everyone actively
    holding one, plus anyone with an active delegation from such a person.
    Two queries regardless of how many roles are passed."""
    roles = {r for r in (roles or ()) if r}
    if not roles:
        return set()
    ph = ",".join(["?"] * len(roles))
    out = {r["username"] for r in conn.execute(
        f"SELECT username FROM users WHERE is_active=1 AND role IN ({ph})",
        tuple(roles)).fetchall()}
    today = _now()[:10]
    sql = (f"SELECT d.to_user u FROM proc_delegations d JOIN users usr ON usr.username=d.from_user "
           f"WHERE d.is_active=1 AND usr.role IN ({ph}) "
           f"AND (d.from_date IS NULL OR d.from_date<=?) AND (d.to_date IS NULL OR d.to_date>=?)")
    for r in conn.execute(sql, tuple(roles) + (today, today)).fetchall():
        out.add(r["u"])
    return out


def stage_signers(conn, stage, roles):
    """Usernames who may ACTUALLY sign `stage`: role_holders() minus anyone whose
    role sits below the rung's DOAM §3.2 authority level.

    THE one definition of "who can sign this rung", because two of them is how
    the ladder deadlocks. can_act() refuses an under-level signer, so any caller
    that asks "is this rung covered?" with the unfiltered set gets yes for a rung
    nobody may commit — and the SoD climb, which exists precisely to break that,
    concludes a colleague can sign and stays put. Measured before this existed:
    the CFO rung mapped to {cfo, purchasing_manager} (an admin adding the buyer
    so he could help chase approvals), the CFO raises a 600,000 request himself,
    and the rung parked forever — buyer 'forbidden', CFO 'self_approval', nothing
    escalated, status 'pending' with no error anywhere.

    Roles are filtered, not people: authority is a property of the role, and a
    delegation deliberately conveys the DELEGATED role's authority."""
    return role_holders(conn, [r for r in (roles or ()) if C.holds_authority(r, stage)])


def ladder_signer_health(conn=None):
    """Every ladder rung with the roles that sign it and whether anyone can.

    A rung whose roles have no active holder is a SILENT deadlock, not an error:
    resolve_escalation() treats "nobody eligible at all" as unchanged, so the
    request simply sits there until a platform admin notices and signs it. This
    reports the condition before a request lands on it.

    Covers the DOAM ladder and any legacy rung not in it, so the answer stays
    right whichever ladder DOAM_IN_FORCE selects. Per rung:
      roles         the role keys that may sign it (admin override applied)
      level         the DOAM §3.2 authority level the rung commits at
      underlevel    those roles that do NOT hold it (they cannot sign the rung)
      unregistered  those roles that are in no role registry at all
      holders       count of active users (incl. delegates) who hold one
      ok            False = nobody can sign this rung
    """
    own = conn is None
    if own:
        conn = get_db()
    try:
        rmap = stage_roles_map(conn)
        registry = set(effective_roles())
        stages = list(C.DOAM_LADDER) + [s for s in C.LADDER if s not in C.DOAM_LADDER]
        out = []
        for stage in stages:
            roles = sorted(rmap.get(stage, ()))
            # A role mapped to the rung but BELOW its DOAM authority level cannot
            # sign it (can_act applies the same filter), so it must not be counted
            # as a holder — a rung nobody may commit would otherwise read as
            # staffed and the deadlock would be silent again. Same for a role with
            # no level recorded at all, which fails closed by design.
            holders = stage_signers(conn, stage, roles)
            out.append({"stage": stage, "label": C.stage_label(stage),
                        "roles": roles,
                        "level": C.DOAM_LEVEL.get(stage),
                        "underlevel": [r for r in roles
                                       if not C.holds_authority(r, stage)],
                        "unregistered": [r for r in roles if r not in registry],
                        "holders": len(holders),
                        "ok": bool(holders)})
        return out
    finally:
        if own:
            conn.close()


def eligible_approvers(conn, stage, roles=None):
    """Usernames allowed to act on a stage: everyone holding a qualifying role,
    plus anyone with an active delegation from such a person.

    `roles` overrides the stage's configured roles — pass a step's escalated role
    set so a notification reaches whoever actually has to sign that rung.

    Roles below the rung's DOAM authority level are dropped, the same filter
    can_act() applies, so a rung never sends a signature request to somebody who
    would be refused when they opened it."""
    if roles is None:
        roles = stage_roles_map(conn).get(stage, set())
    return list(stage_signers(conn, stage, roles))


def _proc_admins(conn):
    """Usernames who can act on any stage / fix a stuck ladder: super_admin plus
    every role holding the proc_admin permission."""
    roles = [k for k in effective_roles()
             if k == "super_admin" or has_permission(k, "proc_admin")]
    if not roles:
        return []
    ph = ",".join(["?"] * len(roles))
    return [r["username"] for r in conn.execute(
        f"SELECT username FROM users WHERE is_active=1 AND role IN ({ph})",
        tuple(roles)).fetchall()]


def _pr_link(pr_id):
    return f"/procurement/pr/{pr_id}"


# --------------------------------------------------------------------------
# authority
# --------------------------------------------------------------------------
def active_delegator_roles(username):
    """Roles the given user has been delegated (active window) by others."""
    if not username:
        return set()
    conn = get_db()
    try:
        today = _now()[:10]
        rows = conn.execute(
            """SELECT d.from_user, u.role FROM proc_delegations d
               JOIN users u ON u.username = d.from_user
               WHERE d.to_user=? AND d.is_active=1
                 AND (d.from_date IS NULL OR d.from_date<=?)
                 AND (d.to_date IS NULL OR d.to_date>=?)""",
            (username, today, today)).fetchall()
        return {r["role"] for r in rows if r["role"]}
    finally:
        conn.close()


def can_act(user, stage, _deleg=None, _roles=None):
    """May this user approve/reject the given ladder stage? True for the stage's
    own roles, for super_admin / proc_admin, or for anyone actively delegated a
    qualifying role by another user. Pass `_deleg` (a prefetched set from
    active_delegator_roles) and `_roles` (a prefetched stage_roles_map) when
    checking many stages in a loop so those lookups run one query in total
    instead of one per stage."""
    if not user:
        return False
    role = user.get("role")
    if role == "super_admin" or has_permission(role, "proc_admin"):
        return True
    if not has_permission(role, "proc_approve"):
        return False
    if _roles is None:
        _roles = stage_roles_map()
    # DOAM §3.2 / Table 4: being MAPPED to a rung is not the same as holding the
    # authority that rung commits. A role signs here only if it carries at least
    # the rung's level, which is what stops an admin stage override (or a role
    # invented in Admin -> Roles with proc_approve) from buying L1 authority with
    # a role name. Filtering `allowed` covers the delegated path in the same
    # line: a delegation conveys the DELEGATED role's authority, which is what a
    # delegation is, and it is dated, revocable and audited — unlike the ambient
    # membership this closes.
    allowed = {r for r in _roles.get(stage, ()) if C.holds_authority(r, stage)}
    if role in allowed:
        return True
    if _deleg is None:
        _deleg = active_delegator_roles(user.get("username"))
    return bool(allowed & _deleg)


# --------------------------------------------------------------------------
# Segregation-of-duties escalation: WHO signs a rung whose only eligible
# signer is the originator. Nothing here changes WHICH rungs exist, their
# order, the amount thresholds or any gate.
# --------------------------------------------------------------------------
def escalation_map(conn=None):
    """{role_key: superior_role} as CONFIGURED. proc_escalations is authoritative:
    a role with no row (or a blank superior) has nobody above it. Only a database
    that predates the table falls back to constants.DEFAULT_ESCALATION, so an
    owner who deliberately blanked a superior is never second-guessed."""
    own = conn is None
    if own:
        conn = get_db()
    try:
        rows = conn.execute("SELECT role_key, superior_role FROM proc_escalations").fetchall()
    except Exception:
        # MANDATORY rollback: PostgreSQL aborts the whole transaction on a failed
        # statement, and this runs on submit_pr's shared connection — without it a
        # pre-migration database would fail every later write of the submit too.
        try:
            conn.rollback()
        except Exception:
            pass
        return dict(C.DEFAULT_ESCALATION)     # pre-migration database
    finally:
        if own:
            conn.close()
    return {r["role_key"]: (r["superior_role"] or "").strip() for r in rows}


def _sole_signers(holders, requester, skip=None):
    """Usernames who are the ONLY person able to sign some OTHER rung of the same
    ladder (`holders` = {stage: eligible usernames}).

    An escalation must not land on one of them: they would sign the escalated
    rung and the dual-role rule would then refuse them their own, which just
    moves the same deadlock one rung down instead of clearing it."""
    out = set()
    for stage, people in (holders or {}).items():
        if stage == skip:
            continue
        free = set(people) - {requester}
        if len(free) == 1:
            out |= free
    return out


def can_sign_role(role_key, stage=None):
    """Can a holder of this role sign an approval rung AT ALL? An escalation that
    lands on a role without proc_approve produces a rung whose "new signer" is
    refused ("forbidden") by can_act — a silent permanent deadlock, exactly what
    this feature exists to prevent — so such a role is never a candidate. Mirrors
    can_act's own predicate.

    With `stage` given it mirrors the WHOLE predicate, authority level included:
    an escalation chain an admin pointed at a junior role must not stamp that
    role onto an L1 rung, and the climb walks past it to somebody who really can
    commit that money rather than parking on a signature can_act would refuse.
    Called without a stage (the settings dropdown) it answers the permission
    half only, which is the question that screen asks."""
    if role_key == "super_admin" or has_permission(role_key, "proc_admin"):
        return True
    if not has_permission(role_key, "proc_approve"):
        return False
    return stage is None or C.holds_authority(role_key, stage)


def resolve_escalation(conn, stage, requester, _roles=None, _chain=None,
                       _holders=None, _busy=()):
    """Decide who signs `stage` on a request raised by `requester`.

    Returns (roles, reason):
      (None, "")             nothing changes — either somebody OTHER than the
                             requester can already sign this stage, or nobody at
                             all is eligible for it (an unstaffed stage: exactly
                             today's behaviour, an admin signs or it waits);
      ({roles}, "escalated") nobody who normally signs the stage may sign it on
                             THIS request (they are the requester, or `_busy` — they
                             already signed another rung and the dual-role rule
                             refuses them), so the rung climbs one or more levels up;
      (set(), "no_superior") same, and the climb ran out — nobody can sign this rung.

    The climb never resolves to the requester at any depth, and a cycle an admin
    configured (A -> B -> A) is broken by the visited set and the depth bound.

    `_holders` ({stage: eligible usernames} for the whole ladder) and `_busy`
    (people who already signed a rung of this request) keep the climb off anyone
    the DUAL-ROLE rule would then refuse — a candidate is only accepted if it has
    an eligible person who is neither the requester nor spoken for elsewhere on
    the same ladder. Without that, warehouse_manager -> factory_manager hands the
    warehouse rung to the sole factory manager, who is then refused his own rung
    ("dual_role") and the request is stuck exactly as before.
    """
    roles = set((_roles if _roles is not None else stage_roles_map(conn)).get(stage) or ())
    if not roles:
        return None, ""
    people = (_holders or {}).get(stage)
    if people is None:
        people = stage_signers(conn, stage, roles)
    if not people:
        return None, ""                       # nobody eligible at all: unchanged
    if people - {requester} - set(_busy):
        return None, ""                       # a colleague can sign: unchanged
    # Nobody who normally signs this rung is allowed to: they are the originator,
    # or they already signed another rung of THIS request and the dual-role rule
    # will refuse them. `_busy` is empty on a fresh submit, so that path behaves
    # exactly as before; it is only non-empty for a value rung appended after
    # pricing, which is where the collision an earlier escalation created shows up.
    busy = set(_busy) | _sole_signers(_holders, requester, skip=stage)
    chain = escalation_map(conn) if _chain is None else _chain
    seen, level = set(roles), set(roles)
    for _ in range(C.ESCALATION_MAX_DEPTH):
        nxt = {chain.get(r) or "" for r in level} - {""} - seen
        if not nxt:
            break
        found = {r for r in nxt if can_sign_role(r, stage)
                 and role_holders(conn, {r}) - {requester} - busy}
        if found:
            return found, "escalated"
        seen |= nxt
        level = nxt
    return set(), "no_superior"


def _csv_set(value):
    return {x.strip() for x in str(value or "").split(",") if x.strip()}


def step_roles(step, _roles=None):
    """Effective signing roles for ONE ladder step: the escalated superior role(s)
    when the rung was escalated, else the stage's configured roles. A step whose
    esc_role is the empty string (the climb found nobody) keeps the stage's own
    roles, so an admin can still sign it."""
    esc = _csv_set(_pr_field(step, "esc_role"))
    if esc:
        return esc
    stage = _pr_field(step, "stage")
    return set((_roles if _roles is not None else stage_roles_map()).get(stage) or ())


def can_act_step(user, step, _deleg=None, _roles=None):
    """can_act for a CONCRETE ladder step — identical to can_act except that an
    escalated rung is signed by the superior role instead of the stage's own."""
    stage = _pr_field(step, "stage")
    esc = _csv_set(_pr_field(step, "esc_role"))
    if not esc:
        return can_act(user, stage, _deleg=_deleg, _roles=_roles)
    roles = dict(_roles if _roles is not None else stage_roles_map())
    roles[stage] = esc
    return can_act(user, stage, _deleg=_deleg, _roles=roles)


def _esc_columns(conn, stage, requester, _roles, _chain, _holders=None, _busy=()):
    """(esc_role, esc_from, reason) to store on a new pr_steps row for `stage`."""
    found, why = resolve_escalation(conn, stage, requester, _roles=_roles, _chain=_chain,
                                    _holders=_holders, _busy=_busy)
    if not why:
        return None, None, ""
    origin = ",".join(sorted((_roles or {}).get(stage) or ()))
    return ",".join(sorted(found)), origin, why


def _user_sig(username):
    """Return (sig_png, sig_name) for a user, or (None, None)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT sig_png, sig_name, full_name FROM users WHERE username=?",
            (username,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None, None
    return row["sig_png"], (row["sig_name"] or row["full_name"] or username)


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------
def _amount(item):
    """Line total: prefer est_cost, else qty * unit_price."""
    est = item.get("est_cost")
    if est not in (None, "", 0, "0"):
        try:
            return float(est)
        except (TypeError, ValueError):
            pass
    try:
        return float(item.get("qty") or 0) * float(item.get("unit_price") or 0)
    except (TypeError, ValueError):
        return 0.0


def get_pr(pr_id):
    """Return the full PR bundle: header, items, steps, events, attachments meta."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return None
        items = conn.execute(
            "SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()
        steps = conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()
        events = conn.execute(
            "SELECT * FROM pr_events WHERE pr_id=? ORDER BY id DESC", (pr_id,)).fetchall()
        atts = conn.execute(
            "SELECT id, filename, content_type, size, uploaded_by, created_at "
            "FROM pr_attachments WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()
        _qcols = ("id, pr_id, vendor, amount, currency, lead_time_days, warranty, "
                  "filename, is_chosen, notes, created_at")
        try:
            quotes = conn.execute(
                "SELECT " + _qcols + ", rfq_id FROM pr_quotes WHERE pr_id=? "
                "ORDER BY amount", (pr_id,)).fetchall()
        except Exception:
            # Database predating the RFQ link column. The request page is the
            # hottest read in the module; it must never depend on a migration
            # having landed.
            conn.rollback()
            quotes = conn.execute(
                "SELECT " + _qcols + " FROM pr_quotes WHERE pr_id=? ORDER BY amount",
                (pr_id,)).fetchall()
        rfqs = rfqs_for(conn, pr_id)
        invoices = conn.execute(
            "SELECT id, pr_id, invoice_no, invoice_date, amount, tax, currency, status, "
            "filename, notes, created_by, created_at FROM pr_invoices WHERE pr_id=? "
            "ORDER BY id", (pr_id,)).fetchall()
        payments = conn.execute(
            "SELECT id, pr_id, invoice_id, amount, currency, method, reference, paid_at, "
            "notes, created_at FROM pr_payments WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()
        try:
            sig_evs = conn.execute(
                "SELECT seq, stage, code FROM pr_sign_events WHERE pr_id=? ORDER BY id",
                (pr_id,)).fetchall()
        except Exception:
            sig_evs = []
        try:
            grns = [dict(r) for r in conn.execute(
                "SELECT * FROM pr_grn WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
            quarantine = [dict(r) for r in conn.execute(
                "SELECT * FROM pr_grn_quarantine WHERE pr_id=? ORDER BY id",
                (pr_id,)).fetchall()]
        except Exception:
            grns, quarantine = [], []    # database predating the GRN tables
        pos = _pos_for(conn, pr)     # one per vendor; falls back to pr.po_no
        returns_ok = True
        try:
            returns = [dict(r) for r in conn.execute(
                "SELECT * FROM pr_returns WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
        except Exception as exc:
            # Empty is NOT the same answer as unreadable: the payable ceiling is
            # derived from this list, so swallowing the failure quietly removed
            # the cap instead of reporting it. three_way_match reads the flag.
            log.warning("pr_returns unavailable for PR %s: %s", pr_id, exc)
            returns, returns_ok = [], False
    finally:
        conn.close()
    pr_d = dict(pr)
    # latest verification code per (seq, stage) — printed on the PDF signature grid
    codes = {(e["seq"], e["stage"]): e["code"] for e in sig_evs}
    step_ds = []
    for r in steps:
        s = dict(r)
        s["verify_code"] = codes.get((s.get("seq"), s.get("stage")))
        # DOAM Table 5 letter for this rung, normalised so the page and the PDF
        # never have to cope with a NULL from a row written pre-migration.
        s["action"] = step_action_of(s)
        s["action_code"] = C.STEP_ACTION_CODE[s["action"]]
        s["action_label"] = "%s (%s)" % (C.STEP_ACTION_LABELS[s["action"]],
                                         s["action_code"])
        # aging only meaningful for the current pending step
        if s.get("status") == "pending" and s.get("seq") == pr_d.get("current_seq") \
           and pr_d.get("status") == "pending":
            hrs = _age_hours(s.get("activated_at") or pr_d.get("submitted_at"))
            if hrs is not None:
                s["age_hours"] = round(hrs, 1)
                s["overdue"] = hrs > C.SLA_HOURS_PER_STAGE
                s["due_soon"] = (not s["overdue"]) and hrs > C.SLA_WARN_HOURS
        step_ds.append(s)
    # DOAM Table 4 L2 — the verdict, from the rows already in hand. The request
    # page needs it to decide whether the split is WHY a director is missing;
    # computing it here costs nothing, where l2_domain_of() would open a second
    # connection and re-query the same two tables on every view.
    return {"pr": pr_d, "items": [dict(r) for r in items], "steps": step_ds,
            "l2_domain": C.l2_domain(pr_d.get("department"), pr_d.get("source_module"),
                                     any(_pr_field(r, "spare_id") for r in items)),
            "events": [dict(r) for r in events], "attachments": [dict(r) for r in atts],
            "quotes": [dict(r) for r in quotes], "rfqs": rfqs,
            "invoices": [dict(r) for r in invoices], "payments": [dict(r) for r in payments],
            "grns": grns, "quarantine": quarantine, "returns": returns,
            "returns_ok": returns_ok, "pos": pos}


def list_prs(status=None, requester=None, limit=500):
    conn = get_db()
    try:
        sql = "SELECT * FROM pr_requests WHERE is_active=1"
        args = []
        if status and status != "all":
            sql += " AND status=?"
            args.append(status)
        if requester:
            sql += " AND requester=?"
            args.append(requester)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def my_queue(user):
    """PRs currently waiting on a stage this user is allowed to act on.

    Batched: one query for the PRs, one for ALL their pending steps, and one
    delegation lookup — regardless of how many PRs are pending. (The previous
    per-PR connection loop multiplied database round-trips by the queue size,
    which is what made procurement pages crawl once auto-reorder PRs landed.)"""
    if not user:
        return []
    prs = list_prs(status="pending")
    if not prs:
        return []
    ids = [pr["id"] for pr in prs]
    conn = get_db()
    try:
        ph = ",".join("?" for _ in ids)
        step_rows = conn.execute(
            f"SELECT * FROM pr_steps WHERE status='pending' AND pr_id IN ({ph})",
            tuple(ids)).fetchall()
        roles_map = stage_roles_map(conn)      # one read, reused for every PR below
    finally:
        conn.close()
    by_pr = {}
    for s in step_rows:
        by_pr.setdefault(s["pr_id"], []).append(s)
    deleg = active_delegator_roles(user.get("username"))
    out = []
    for pr in prs:
        cur = [s for s in by_pr.get(pr["id"], []) if s["seq"] == pr["current_seq"]]
        mine = next((s for s in cur if can_act_step(user, s, _deleg=deleg,
                                                    _roles=roles_map)), None)
        if mine:
            pr = dict(pr)
            pr["_stage"] = mine["stage"]
            pr["_stage_label"] = stage_label(mine["stage"])
            out.append(pr)
    return out


def counts(user=None):
    """Small KPI bundle for the module landing page."""
    conn = get_db()
    try:
        def c(sql, a=()):
            return conn.execute(sql, a).fetchone()["c"]
        data = {
            "total": c("SELECT COUNT(*) c FROM pr_requests WHERE is_active=1"),
            "pending": c("SELECT COUNT(*) c FROM pr_requests WHERE status='pending'"),
            "approved": c("SELECT COUNT(*) c FROM pr_requests WHERE status IN ('approved','po_issued','closed')"),
            "rejected": c("SELECT COUNT(*) c FROM pr_requests WHERE status='rejected'"),
            "value_pending": c("SELECT COALESCE(SUM(total),0) c FROM pr_requests WHERE status='pending'"),
        }
    finally:
        conn.close()
    data["my_queue"] = len(my_queue(user)) if user else 0
    return data


# --------------------------------------------------------------------------
# create / submit
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Responsibility (approval) matrix — per department
# --------------------------------------------------------------------------
def pr_l2_domain(conn, pr_id):
    """DOAM Table 4 L2 — which director owns THIS request: ("plant" |
    "supply_chain" | None, reason).

    Reads only master data the request already carries: the department, the
    source module it was raised from, and whether any line is a stocked spare.
    None = it cannot be told apart, and the caller must keep both directors.

    Never raises: a missing column on an old database, or a table the deploy has
    not reached, means "cannot be told apart", which is the today behaviour.
    """
    try:
        pr = conn.execute(
            "SELECT department, source_module FROM pr_requests WHERE id=?",
            (pr_id,)).fetchone()
        if not pr:
            return None, "no_request"
        spare = conn.execute(
            "SELECT 1 FROM pr_items WHERE pr_id=? AND spare_id IS NOT NULL LIMIT 1",
            (pr_id,)).fetchone() is not None
    except Exception:                     # noqa: BLE001 — see docstring
        return None, "unreadable"
    dom = C.l2_domain(_pr_field(pr, "department"), _pr_field(pr, "source_module"),
                      spare)
    if not dom:
        return None, "ambiguous"
    src = str(_pr_field(pr, "source_module") or "").strip().lower()
    # Every branch below is reachable and the last one is total: with item text
    # out of the signal set, a non-None domain can only have come from the
    # source module, a spare line or the department.
    why = ("maintenance_origin" if src in C.PD_SOURCE_MODULES else
           "spare_part_line" if spare and dom == "plant" else
           "costing_origin" if src in C.SCD_SOURCE_MODULES else "department")
    return dom, why


def l2_domain_of(pr_id):
    """pr_l2_domain() on its own connection, for the request page."""
    conn = get_db()
    try:
        return pr_l2_domain(conn, pr_id)
    finally:
        conn.close()


def dept_ladder(conn, department, total, kind="opex", pr_id=None):
    """Ordered stage list required for a PR of `total` in `department`. Uses the
    department's custom responsibility matrix if one exists, else the DOAM ladder
    selected by `kind` ("opex" §4.1 / "capex" §4.2).

    `pr_id`, when given, also applies the DOAM Table 4 L2 domain split: PD owns
    production and maintenance, SC-D owns operational and inventory
    replenishment, so only one of them signs a request whose domain is clear.
    Called WITHOUT it (the governance screens, "what would a 120,000 request
    look like") the ladder is the amount-only one it has always been.

    A department override still wins, because a department that has set its own
    matrix has deliberately said so — but it is only consulted for OPEX. Capital
    expenditure is a company-level authority in the DOAM, not a departmental one,
    so a department cannot quietly give itself a shorter ladder for machinery.

    DOAM Table 4's unbudgeted escalation is NOT here: like §4.3 single-source it
    is a control rung appended after the value ladder (apply_budget_state), so
    the L1 signature is the last one collected rather than landing mid-ladder
    ahead of the directors."""
    try:
        t = float(total or 0)
    except (TypeError, ValueError):
        t = 0.0
    kind = (kind or "opex").strip().lower()
    floor = build_ladder(t, kind)
    if kind == "capex":
        return floor
    # Table 4 L2 split, applied to the FLOOR only: a department that has put the
    # dropped director in its own matrix below gets them back, because that
    # department configured the signature deliberately.
    if pr_id:
        _dom, _why = pr_l2_domain(conn, pr_id)
        _split = C.apply_l2_domain(floor, _dom)
        if _split != floor:
            _dropped = [s for s in floor if s not in _split]
            _note = (
                "DOAM Table 4 L2: %s signs and %s does not — %s owns %s, and this "
                "request is %s (%s). Both directors used to join on amount alone."
                % (stage_label(C.L2_DOMAIN_STAGE[_dom]), stage_label(_dropped[0]),
                   stage_label(C.L2_DOMAIN_STAGE[_dom]),
                   "production and maintenance commitments" if _dom == "plant"
                   else "operational and inventory replenishment",
                   "a maintenance/production commitment" if _dom == "plant"
                   else "an inventory replenishment", _why))
            # Same ladder rebuilt twice (re-pricing) must not stack duplicates.
            if not conn.execute(
                    "SELECT 1 FROM pr_events WHERE pr_id=? AND action='l2_domain' "
                    "AND detail=? LIMIT 1", (pr_id, _note)).fetchone():
                audit(conn, pr_id, "system", "l2_domain", _note)
        floor = _split
    rows = conn.execute(
        "SELECT stage, threshold FROM proc_resp_matrix "
        "WHERE department=? AND active=1 ORDER BY seq, id", (department or "",)).fetchall()
    if not rows:
        return floor
    dept = [r["stage"] for r in rows if t >= float(r["threshold"] or 0)]
    if not C.DOAM_IN_FORCE:
        return dept              # pre-DOAM behaviour: the matrix fully replaces
    # DOAM in force: the ladder is a FLOOR, not a default. A department matrix may
    # ADD signatures, never remove one the DOAM requires.
    #
    # This is defence in depth, and it is needed: a single row (department,
    # 'warehouse', threshold 0) used to collapse a whole department to ONE
    # signature — which also dropped 'purchasing', the pricing-gate stage, so the
    # pricing, RFQ and engineering gates were skipped along with it. Enforcing the
    # floor HERE means an existing bad row in the database cannot do that either.
    merged = list(floor) + [s for s in dept if s not in floor]
    order = {s: i for i, s in enumerate(C.DOAM_LADDER)}
    return sorted(merged, key=lambda s: order.get(s, len(order)))


def list_departments():
    """Departments for the dropdown: constant defaults + any already used by a
    budget or a responsibility matrix, de-duplicated, order preserved."""
    conn = get_db()
    try:
        used = [r["department"] for r in conn.execute(
            "SELECT DISTINCT department FROM proc_budgets WHERE department IS NOT NULL "
            "UNION SELECT DISTINCT department FROM proc_resp_matrix WHERE department IS NOT NULL"
        ).fetchall()]
    finally:
        conn.close()
    out = []
    for d in list(DEPARTMENTS) + used:
        d = (d or "").strip()
        if d and d not in out:
            out.append(d)
    return out


def all_dept_matrices():
    """{department: {stage: threshold}} for departments with a custom matrix —
    drives the live approval-route preview on the new-request form."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT department, stage, threshold FROM proc_resp_matrix WHERE active=1").fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["department"], {})[r["stage"]] = float(r["threshold"] or 0)
    return out


def get_dept_matrix(department):
    """For the settings UI: {stage: {included, threshold, seq}} across the whole
    canonical LADDER (a department's custom rows override the defaults)."""
    conn = get_db()
    try:
        rows = {r["stage"]: r for r in conn.execute(
            "SELECT stage, threshold, seq FROM proc_resp_matrix WHERE department=? AND active=1",
            (department or "",)).fetchall()}
    finally:
        conn.close()
    out = {}
    for i, stage in enumerate(LADDER):
        if stage in rows:
            seq = rows[stage]["seq"]
            out[stage] = {"included": True, "threshold": float(rows[stage]["threshold"] or 0),
                          "seq": seq if seq is not None else i}
        else:
            out[stage] = {"included": False, "threshold": float(APPROVAL_MATRIX.get(stage, 0)),
                          "seq": i}
    return out


def set_dept_matrix(department, stage_rows, user=None):
    """Replace a department's responsibility matrix. `stage_rows` = ordered list
    of {stage, threshold} for the INCLUDED stages. Empty list clears it (the
    department reverts to the global default)."""
    department = (department or "").strip()
    if not department:
        return False, "no_department"
    rows = [r for r in stage_rows if r.get("stage") in LADDER]
    if C.DOAM_IN_FORCE and rows:
        # The DOAM ladder is a floor. A department may add signatures or demand
        # them earlier (a LOWER threshold), never drop a stage the matrix
        # requires or push its threshold higher. Refused at the write, and
        # enforced again at read time in dept_ladder() — a matrix saved before
        # this rule existed must not keep working either.
        given = {r["stage"]: float(r.get("threshold") or 0) for r in rows}
        weakened = []
        for stage, floor_at in C.OPEX_MATRIX.items():
            if stage not in given:
                weakened.append("%s is required by the DOAM and is not in the matrix"
                                % stage_label(stage))
            elif given[stage] > floor_at:
                weakened.append("{} starts at {:,.0f} in the DOAM but {:,.0f} here".format(
                    stage_label(stage), floor_at, given[stage]))
        if weakened:
            return False, "below_doam_floor: " + "; ".join(weakened)
    conn = get_db()
    try:
        conn.execute("DELETE FROM proc_resp_matrix WHERE department=?", (department,))
        now = _now()
        for i, r in enumerate(rows):
            conn.execute(
                "INSERT INTO proc_resp_matrix (department, stage, threshold, seq, active, updated_at) "
                "VALUES (?,?,?,?,1,?)",
                (department, r["stage"], float(r.get("threshold") or 0), i, now))
        conn.commit()
    finally:
        conn.close()
    # Who changed a department's approval routing is exactly the kind of thing an
    # auditor asks about, and it was not being recorded at all.
    try:
        from app.db import log_audit
        log_audit((user or {}).get("username") or "system", "proc_resp_matrix",
                  "%s: %s" % (department, ", ".join(
                      "%s@%g" % (r["stage"], float(r.get("threshold") or 0)) for r in rows) or "cleared"))
    except Exception:
        pass
    return True, ""


def delete_dept_matrix(department):
    """Remove a department's custom responsibility matrix. A custom-added
    department disappears from the list; a built-in one reverts to the global
    default route. Returns (ok, was_builtin)."""
    department = (department or "").strip()
    if not department:
        return False, False
    was_builtin = department in DEPARTMENTS
    conn = get_db()
    try:
        conn.execute("DELETE FROM proc_resp_matrix WHERE department=?", (department,))
        conn.commit()
    finally:
        conn.close()
    return True, was_builtin


def rename_dept(old, new):
    """Rename a custom department everywhere it is referenced (matrix + budgets).
    Built-in departments (defined in code) cannot be renamed. Returns (ok, msg)."""
    old = (old or "").strip()
    new = (new or "").strip()[:60]
    if not old or not new:
        return False, "empty"
    if old == new:
        return True, ""
    if old in DEPARTMENTS:
        return False, "builtin"
    if new in list_departments():
        return False, "exists"
    conn = get_db()
    try:
        conn.execute("UPDATE proc_resp_matrix SET department=? WHERE department=?", (new, old))
        try:
            conn.execute("UPDATE proc_budgets SET department=? WHERE department=?", (new, old))
        except Exception:  # noqa: BLE001 — budgets table/column optional
            pass
        conn.commit()
        return True, ""
    finally:
        conn.close()


def _real_spares(ids):
    """{id: [every text the MASTER holds for it]} for those of `ids` that are
    rows which actually exist in the maintenance spares master.

    `pr_items.spare_id` is a hidden form field with no foreign key behind it, so
    every control that treats "linked to a spare" as master data has to resolve
    the link first — an unchecked integer is requester-supplied text, not master
    data. The master's own wording comes back with it because knowing which
    words came from the master and which the requester typed is the whole
    difference (see cost_object_check): the maintenance bridge copies the
    spare's name into the PR title and its spec into the line description, and
    a sewing-machine spare is legitimately called "thread guide bracket, for the
    washing line feeder".

    Own connection on purpose: the maintenance module may not be installed, and
    on PostgreSQL a failed statement aborts the CALLER's transaction."""
    want = {int(i) for i in ids if str(i or "").strip().lstrip("-").isdigit()}
    if not want:
        return {}
    conn = get_db()
    try:
        return {int(r["id"]): [r["code"], r["name"], r["spec"], r["description"]]
                for r in conn.execute(
                    "SELECT id, code, name, spec, description FROM mnt_spare_parts "
                    "WHERE id IN (%s)" % ",".join("?" * len(want)),
                    tuple(sorted(want))).fetchall()}
    except Exception:
        return {}
    finally:
        conn.close()


def create_pr(header, items, user, ip=None, submit=True, priced=None):
    """Create a PR (+items). When submit=True, build the ladder and route it.

    `priced` records whether the request already carries commercial pricing:
    when None it is inferred (a request with a positive total is 'priced', a
    zero-value requester-raised request is 'unpriced' and must be priced by
    Purchasing at the pricing gate). Returns (pr_id, pr_no)."""
    # Resolved BEFORE the first write: spare_id arrives as a hidden form field
    # and is stored with no foreign key, so a fabricated id used to persist and
    # then switch the DOAM cost-object gate off. A link that does not resolve to
    # a real spare is dropped, not kept.
    real_spares = _real_spares([it.get("spare_id") for it in items])
    conn = get_db()
    try:
        total = round(sum(_amount(it) for it in items), 2)
        if priced is None:
            priced = total > 0
        now = _now()
        uname = user.get("username") if user else "system"
        cur = conn.execute(
            """INSERT INTO pr_requests
               (pr_no, title, request_for, request_for_kind, requester,
                requester_name, requester_user_id,
                department, request_date, currency, vendor, payment_condition,
                delivery_condition, req_del_date, asset_code, total, status,
                current_seq, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (None, header.get("title"), header.get("request_for"),
             header.get("request_for_kind"), uname,
             user.get("full_name") or uname if user else uname,
             user.get("id") if user else None,
             header.get("department"), header.get("request_date") or now[:10],
             header.get("currency") or "EGP", header.get("vendor"),
             header.get("payment_condition"), header.get("delivery_condition"),
             header.get("req_del_date"), header.get("asset_code"),
             total, "draft", 0, header.get("notes"), now))
        pr_id = cur.lastrowid
        pr_no = doc_no("PR", pr_id)
        conn.execute("UPDATE pr_requests SET pr_no=? WHERE id=?", (pr_no, pr_id))
        fill_units_from_catalogue(conn, items)
        for i, it in enumerate(items, start=1):
            conn.execute(
                """INSERT INTO pr_items
                   (pr_id, seq, item, description, unit, qty, current_stock, vendor,
                    unit_price, est_cost, notes, spare_id, item_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pr_id, i, it.get("item"), it.get("description"), it.get("unit") or "Pcs",
                 float(it.get("qty") or 0), float(it.get("current_stock") or 0),
                 it.get("vendor") or header.get("vendor"),
                 float(it.get("unit_price") or 0), round(_amount(it), 2), it.get("notes"),
                 (int(it["spare_id"])
                  if str(it.get("spare_id") or "").strip().isdigit()
                  and int(it["spare_id"]) in real_spares else None),
                 int(it["item_id"]) if str(it.get("item_id") or "").strip().isdigit() else None))
        # §4.2 — an unrecognised value must not quietly take the WEAKER ladder.
        kind, _known = C.normalise_expenditure_kind(header.get("expenditure_kind"))
        try:
            # retention_until is stamped HERE, at creation, from the same two
            # numbers the controlled-forms register prints as prose: 10 years for
            # capital expenditure, 5 for everything else. A record that carries
            # its own disposal date is a control; a page that says "5 years" is
            # a claim.
            conn.execute("UPDATE pr_requests SET tax_rate=?, pricing_status=?, "
                         "expenditure_kind=?, so_no=?, cost_center=?, "
                         "forecast_ref=?, retention_until=? WHERE id=?",
                         (float(header.get("tax_rate") or 0),
                          "priced" if priced else "unpriced", kind,
                          header.get("so_no") or None,
                          header.get("cost_center") or None,
                          header.get("forecast_ref") or None,
                          C.retention_until(now, kind), pr_id))
        except Exception as exc:  # noqa: BLE001
            # Fails safe (an unstamped row reads as retained) but not silently:
            # this one statement also carries tax_rate, expenditure_kind, so_no
            # and cost_center, and the only other signal was a report KPI.
            logging.getLogger(__name__).warning(
                "PR %s header update failed: %s", pr_id, exc)
        audit(conn, pr_id, uname, "created",
              f"PR {pr_no} created" + (f" (total {total})" if priced else " (pricing pending)"), ip)
        conn.commit()
    finally:
        conn.close()
    if submit:
        submit_pr(pr_id, user, ip)
    return pr_id, pr_no


def link_source(pr_id, module, ref, user=None, ip=None):
    """Persistently tag WHERE a PR came from (e.g. module='maintenance',
    ref='ticket:12'). Powers two-way links (ticket page shows its PRs, the PR
    shows its origin) and the parts-arrived notification on goods receipt."""
    conn = get_db()
    try:
        conn.execute("UPDATE pr_requests SET source_module=?, source_ref=? WHERE id=?",
                     (module, ref, pr_id))
        audit(conn, pr_id, (user or {}).get("username") or "system", "linked",
              f"Linked to {module} {ref}", ip)
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


def update_pr(pr_id, header, items, user, ip=None, can_price=True):
    """Replace a DRAFT (or rejected) PR's header + line items. Only the owner /
    an admin should reach this. Returns (ok, msg).

    `can_price=False` (a requester editing): the edit replaces the lines with
    zero prices, so any pricing Purchasing had entered is gone — the PR MUST
    drop back to 'unpriced' and pass the pricing gate again. Without this, an
    edit-after-reject kept pricing_status='priced' on a now-zero total, which
    skipped the pricing gate AND the value-based Finance/CFO/CEO rungs."""
    real_spares = _real_spares([it.get("spare_id") for it in items])  # see create_pr
    conn = get_db()
    try:
        pr = conn.execute(
            "SELECT status, requester, tax_rate, payment_condition, pricing_status, "
            "created_at, expenditure_kind FROM pr_requests WHERE id=?",
            (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("draft", "rejected"):
            return False, "not_editable"
        total = round(sum(_amount(it) for it in items), 2)
        # Requesters can't set commercial terms: keep whatever Purchasing entered.
        tax_rate = float(header.get("tax_rate") or 0) if can_price \
            else float(pr["tax_rate"] or 0)
        pay_cond = header.get("payment_condition") if can_price \
            else pr["payment_condition"]
        kind, _known = C.normalise_expenditure_kind(header.get("expenditure_kind"))
        # A re-file after rejection could change capex to opex and drop the
        # Managing Director, and pr_events said only "edited". Which ladder a
        # request routes on is exactly the kind of change an auditor asks about.
        _was = (_pr_field(pr, "expenditure_kind") or "opex")
        if _was != kind:
            audit(conn, pr_id, (user or {}).get("username") or "system",
                  "expenditure_kind_changed",
                  f"Expenditure type changed from {_was.upper()} to {kind.upper()} — "
                  f"this changes which DOAM ladder the request routes on.", ip)
        conn.execute(
            """UPDATE pr_requests SET title=?, request_for=?, request_for_kind=?,
               department=?, currency=?,
               vendor=?, payment_condition=?, delivery_condition=?, req_del_date=?,
               asset_code=?, notes=?, tax_rate=?, expenditure_kind=?, so_no=?,
               cost_center=?, forecast_ref=?, total=? WHERE id=?""",
            (header.get("title"), header.get("request_for"),
             header.get("request_for_kind"), header.get("department"),
             header.get("currency") or "EGP", header.get("vendor"),
             pay_cond, header.get("delivery_condition"),
             header.get("req_del_date"), header.get("asset_code"), header.get("notes"),
             tax_rate, kind, header.get("so_no") or None,
             header.get("cost_center") or None,
             header.get("forecast_ref") or None, total, pr_id))
        # An edit can flip OPEX -> CAPEX, which lengthens retention from 5 years
        # to 10. Extend it, NEVER shorten it: a record whose kind changes the
        # other way has already been kept under the longer promise, and quietly
        # bringing a disposal date forward is exactly the move a retention rule
        # exists to prevent.
        _ru = C.retention_until(pr["created_at"] if "created_at" in pr.keys()
                                else None, kind)
        conn.execute("UPDATE pr_requests SET retention_until=? WHERE id=? AND "
                     "(retention_until IS NULL OR retention_until < ?)",
                     (_ru, pr_id, _ru))
        if not can_price and (pr["pricing_status"] or "priced") == "priced":
            # the lines were replaced unpriced -> back through the pricing gate
            conn.execute("UPDATE pr_requests SET pricing_status='unpriced', "
                         "priced_at=NULL, priced_by=NULL WHERE id=?", (pr_id,))
        conn.execute("DELETE FROM pr_items WHERE pr_id=?", (pr_id,))
        fill_units_from_catalogue(conn, items)
        for i, it in enumerate(items, start=1):
            conn.execute(
                """INSERT INTO pr_items (pr_id, seq, item, description, unit, qty,
                   current_stock, vendor, unit_price, est_cost, notes, spare_id, item_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (pr_id, i, it.get("item"), it.get("description"), it.get("unit") or "Pcs",
                 float(it.get("qty") or 0), float(it.get("current_stock") or 0),
                 it.get("vendor") or header.get("vendor"),
                 float(it.get("unit_price") or 0), round(_amount(it), 2), it.get("notes"),
                 (int(it["spare_id"])
                  if str(it.get("spare_id") or "").strip().isdigit()
                  and int(it["spare_id"]) in real_spares else None),
                 int(it["item_id"]) if str(it.get("item_id") or "").strip().isdigit() else None))
        audit(conn, pr_id, user.get("username") if user else "system", "edited",
              f"Draft updated (total {total})", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def pr_amounts(pr):
    """Return {subtotal, tax_rate, tax, grand} for a PR dict/row."""
    subtotal = float(pr.get("total") or 0) if isinstance(pr, dict) else float(pr["total"] or 0)
    rate = float((pr.get("tax_rate") if isinstance(pr, dict) else pr["tax_rate"]) or 0)
    tax = round(subtotal * rate / 100.0, 2)
    return {"subtotal": round(subtotal, 2), "tax_rate": rate, "tax": tax,
            "grand": round(subtotal + tax, 2)}


def egp_commitment(pr):
    """EGP-equivalent TOTAL COMMITTED VALUE — tax included.

    DOAM §3.4: "All limits ... represent the total value of a commitment." The
    purchase order the company signs is gross (pdf.py prints Grand Total =
    subtotal + VAT), so routing on the ex-VAT subtotal left a live gap under
    EVERY threshold: at 14% VAT a net 9,900 order commits 11,286 but routed in
    the "up to 10,000" tier. The same gap sat under 200,000, 500,000, 2,000,000
    and 5,000,000 — and the tax rate is entered by Purchasing at the very gate
    that sets the price, so it was the buyer's to exploit.

    egp_total() (net) is deliberately kept for everything that reports or
    reconciles a subtotal; only APPROVAL ROUTING moves to this one.
    """
    if pr is None:
        return 0.0
    def _g(key):
        try:
            return pr.get(key) if isinstance(pr, dict) else pr[key]
        except (KeyError, IndexError):
            return None
    # Built from _g, not pr_amounts(pr): a row selected before this change
    # exists without a tax_rate column, and routing must degrade to the net
    # figure rather than raise on the way to deciding who signs.
    try:
        subtotal = float(_g("total") or 0)
    except (TypeError, ValueError):
        subtotal = 0.0
    try:
        rate_pct = float(_g("tax_rate") or 0)
    except (TypeError, ValueError):
        rate_pct = 0.0
    grand = round(subtotal + subtotal * rate_pct / 100.0, 2)
    cur = str(_g("currency") or "EGP").strip().upper()
    if cur == "EGP":
        return round(grand, 2)
    try:
        rate = float(_g("fx_rate") or 1)
    except (TypeError, ValueError):
        rate = 1.0
    return round(grand * (rate if rate > 0 else 1.0), 2)


def fill_units_from_catalogue(conn, items):
    """Give every PICKED line the unit its catalogue row carries.

    A requester states what they need and how many; the unit of measure is master
    data, not their opinion, so the form locks it and _parse_items drops whatever
    was posted. That leaves a line with no unit — and a blank unit used to become
    a flat "Pcs", which on a line of fabric or dye is simply wrong and would be
    ordered that way.

    So a line linked to a catalogue item takes THAT item's unit.

    A FREE-TEXT line still falls back to "Pcs" at the INSERT below, as it always
    has. That is a placeholder, not a fact — on a line of fabric or dye it is
    wrong until Purchasing correct it at the pricing gate. It is left alone here
    because a NULL unit would reach receiving, the purchase order and the
    three-way match, none of which expect one, and widening this fix that far is
    a bigger change than the lock it exists to support.
    """
    want = {}
    for it in items:
        iid = str(it.get("item_id") or "").strip()
        if iid.isdigit() and not str(it.get("unit") or "").strip():
            want.setdefault(int(iid), []).append(it)
    if not want:
        return items
    marks = ",".join("?" for _ in want)
    for r in conn.execute(
            "SELECT id, unit FROM proc_items WHERE id IN (%s)" % marks,
            list(want)).fetchall():
        for it in want.get(r["id"], []):
            if r["unit"]:
                it["unit"] = r["unit"]
    return items


def stamp_step_actions(conn, pr_id):
    """DOAM Table 5 — stamp WHAT each pending rung's signature is (R/A/E).
    Caller commits.

    Must run after EVERY change to the ladder's shape. Where §4.1 names ONE
    authority per tier the letter follows the ladder's top rung, so it moves when
    the ladder does: an unpriced request submits with Purchasing on top and
    Purchasing is the Approve; pricing pulls a director in above it and
    Purchasing becomes the Review. The pricing gate guarantees that reshuffle
    happens before the purchasing rung can be signed, so no rung is ever signed
    under a letter the ladder later contradicts. Where the DOAM names a JOINT
    approval instead (§4.2 PD + CFO, §4.1 tier 5 MD *and* CFO) the letter is the
    stage's, not the position's — see C.step_action.

    Only PENDING rows are RE-stamped. A signature already given said what it
    said, and rewriting it afterwards is exactly the thing an audit trail exists
    to prevent. The one exception is a signed row carrying no readable letter at
    all — written before action_type existed — which is a blank being filled
    rather than a signature being rewritten. See the loop.
    """
    rows = conn.execute(
        "SELECT id, stage, status, COALESCE(origin,'ladder') AS origin FROM pr_steps "
        "WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()
    ladder = [r for r in rows if r["origin"] not in C.CONTROL_ORIGINS]
    top = ladder[-1]["id"] if ladder else None
    top_stage = ladder[-1]["stage"] if ladder else None
    # §4.2 assigns the CAPEX letters by ROLE, so which ladder this is decides
    # them. Read through normalise_expenditure_kind, the same reader that chose
    # the ladder — a raw column value that routed as OPEX must not be lettered
    # as CAPEX, or the two would describe different requests.
    _k = conn.execute("SELECT expenditure_kind FROM pr_requests WHERE id=?",
                      (pr_id,)).fetchone()
    kind = C.normalise_expenditure_kind(_k["expenditure_kind"] if _k else None)[0]
    acts = {r["id"]: C.step_action(r["origin"], r["id"] == top, r["stage"],
                                   top_stage, kind) for r in rows}
    # A ladder whose every rung reads as a Review commits nothing — the document
    # would evidence a purchase nobody approved. A department responsibility
    # matrix can produce exactly that on the CAPEX ladder (name only reviewing
    # stages), so the top rung carries the A rather than the request printing
    # with none.
    if top and "approve" not in acts.values():
        acts[top] = "approve"
    # Read what each row currently carries, so a blank can be told from a letter.
    have = {r["id"]: str(r["action_type"] or "").strip().lower()
            for r in conn.execute(
                "SELECT id, action_type FROM pr_steps WHERE pr_id=?",
                (pr_id,)).fetchall()}
    for r in rows:
        readable = have.get(r["id"]) in C.STEP_ACTIONS
        if r["status"] != "pending" and readable:
            continue
        # A signed row that carries NO readable letter was never stamped — a row
        # written before action_type existed. Filling a blank is not rewriting a
        # signature, and the alternative is worse: with the fallback now being
        # the weaker letter, such a request would print every rung as a Review
        # and evidence a purchase nobody approved. A row that DOES carry a letter
        # is never touched once signed, which is the rule above.
        conn.execute("UPDATE pr_steps SET action_type=? WHERE id=?",
                     (acts[r["id"]], r["id"]))


def step_action_of(step):
    """The Table 5 action a step carries, tolerant of a pre-migration row.

    Absent and unreadable are answered differently on purpose — see the two
    constants. A row that predates the column meant Approve, and still does. A
    row holding a value nobody can parse gets the weaker letter, because this
    decides what a document CLAIMS about a named person.
    """
    raw = _pr_field(step, "action_type")
    act = str(raw or "").strip().lower()
    if act in C.STEP_ACTIONS:
        return act
    return C.STEP_ACTION_DEFAULT if not act else C.STEP_ACTION_UNREADABLE


def submit_pr(pr_id, user, ip=None):
    """Build the approval ladder and move the PR into 'pending'. Idempotent-ish:
    only acts on draft/rejected PRs. Returns (ok, msg)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("draft", "rejected"):
            return False, "not_submittable"
        # DOAM §5 — direct materials must name the sales order they are for,
        # before they enter the ladder. Without it the spend has no cost object
        # and the order's margin can never be closed out.
        # A sales order that is closed, cancelled or simply not a real order is
        # refused here too — a free-text "n/a" is not a cost object.
        _co_ok, _co_need = cost_object_check(conn, pr_id, pr)
        if not _co_ok:
            return False, _co_need
        # Department-aware ladder: use the department's responsibility matrix if
        # it has one, else the global default. Each rung = parallel stages.
        # Thresholds are EGP-based, so a foreign-currency PR routes on its
        # EGP-equivalent total (total * fx_rate), never the raw foreign figure.
        # DOAM §3.4 — route on the 30-day aggregate when related purchases exist,
        # so three 9,000 requests cannot each duck a 10,000 threshold. The
        # aggregate can only ever LENGTHEN the ladder (max of the two).
        _own = egp_commitment(pr)          # DOAM §3.4: tax-inclusive
        _agg, _sibs = aggregated_total(conn, pr_id, pr, _own)
        _route_on = max(_own, _agg)
        # DOAM Table 4 — budgeted or not, decided HERE, while the ladder is being
        # built, not afterwards on the request page. Measured on this request's
        # own committed value: the aggregate is a §3.4 ROUTING figure, and the
        # siblings already sitting in 'spent' would otherwise be counted twice.
        #
        # An UNPRICED request is skipped, exactly as §3.4's aggregate effectively
        # is: it carries no commitment value yet, so there is nothing to weigh
        # against the budget, and apply_budget_state asks again the moment
        # Purchasing prices it. Not a hole — the purchasing rung cannot be signed
        # while a request is unpriced, so nothing reaches a decision unasked.
        _bud = (budget_check(conn, pr["department"], _own, pr_id=pr_id)
                if (_pr_field(pr, "pricing_status") or "priced") == "priced"
                else {"unbudgeted": False, "state": None, "over_by": None, "status": None})
        _stages = dept_ladder(conn, pr["department"], _route_on,
                              _pr_field(pr, "expenditure_kind"), pr_id=pr_id)
        # DOAM §4.3 — a single-source award is signed one level above the value
        # tier. Applied here as well as in set_single_source(), because the
        # justification can be recorded on the draft before it is ever submitted.
        # `_origin` carries WHICH control put a stage on the ladder through to the
        # INSERT below. Defaulting them all to 'ladder' stamped the Table 5 letter
        # of a value rung onto a control rung, and put them outside the scope the
        # controls use to take their own rungs back off again.
        _origin = {}
        if str(_pr_field(pr, "single_source_reason") or "").strip():
            _extra = C.single_source_stage(_stages, _pr_field(pr, "expenditure_kind"))
            if _extra:
                _stages = _stages + [_extra]
                _origin[_extra] = "single_source"
        # DOAM Table 4 — §4.1 is the BUDGETED ladder, so spend with no approved
        # plan behind it takes the L1 signature on top, whatever its value.
        if _bud["unbudgeted"]:
            _ux = C.unbudgeted_stage(_stages, _pr_field(pr, "expenditure_kind"))
            if _ux:
                _stages = _stages + [_ux]
                _origin[_ux] = "unbudgeted"
        rungs = rungs_from_stages(_stages)
        # SoD escalation, resolved BEFORE anything is written: a rung whose only
        # eligible signer is the requester climbs one level up the org chart. A
        # rung with nobody above it can never be signed, so the request is refused
        # here rather than parked in a queue forever.
        _roles = stage_roles_map(conn)
        _chain = escalation_map(conn)
        flat_stages = [s for rung in rungs for s in rung]
        # Who may sign each rung of THIS ladder, resolved once (2 queries a stage):
        # the climb reads it so an escalation never lands on the only person able
        # to sign another rung — signing two rungs is refused by the dual-role
        # rule, so that would just move the deadlock one rung down.
        _holders = {s: stage_signers(conn, s, _roles.get(s) or ()) for s in flat_stages}
        esc, blocked = {}, []
        for stage in flat_stages:
            e_role, e_from, why = _esc_columns(conn, stage, pr["requester"], _roles,
                                               _chain, _holders=_holders)
            if why == "escalated":
                esc[stage] = (e_role, e_from)
            elif why == "no_superior":
                blocked.append(stage)
        if blocked:
            # A rung the climb cannot resolve is PARKED, not a reason to refuse the
            # whole submit. Refusing looked tidy but was far worse in practice: on a
            # one-holder-per-role chart — and with no active cfo/ceo user, which is
            # the live roster — it stopped senior staff raising ANY request at all.
            # That trades one stuck rung for a person who cannot work, which is not
            # a trade worth making.
            #
            # Parking keeps every guarantee that matters: the rung is written with
            # esc_role='' so step_roles() falls back to the stage's own roles, which
            # means the REQUESTER is still refused by the self-approval check and only
            # an admin (or a delegate, once one exists) can clear it. Nothing is
            # silently stuck either — every proc_admin gets a CRITICAL alert naming
            # the rung and the three ways to unblock it. This is exactly what
            # _reconcile_value_ladder already does for a value rung that joins after
            # pricing, so submit and reconcile now behave the same way.
            labels = ", ".join(stage_label(s) for s in blocked)
            for s in blocked:
                esc.setdefault(s, ("", ""))
            audit(conn, pr_id, user.get("username") if user else "system",
                  "escalation_parked",
                  f"Submitted with {labels} parked: the requester is the only eligible "
                  f"signer for it, and no superior above that role has anyone free to "
                  f"sign it (either nobody is configured above it, or every superior is "
                  f"the requester or the only possible signer of another rung of this "
                  f"same request). An admin or a delegate must clear that rung.", ip)
            notify_users(conn, _proc_admins(conn), "critical",
                         "Approval rung needs a signer",
                         f"{pr['pr_no']} is circulating, but {labels} has no one who may "
                         f"sign it: {pr['requester']} raised the request and is the only "
                         f"eligible signer. Delegate the authority, add a second role "
                         f"holder, or sign as admin.",
                         link=_pr_link(pr_id))
        # clear any prior steps (resubmit after rejection)
        conn.execute("DELETE FROM pr_steps WHERE pr_id=?", (pr_id,))
        now = _now()
        for i, rung in enumerate(rungs, start=1):
            for stage in rung:
                e_role, e_from = esc.get(stage, (None, None))
                conn.execute(
                    """INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role,
                       activated_at, created_at, esc_role, esc_from, origin)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (pr_id, i, stage, "pending", stage_label(stage),
                     now if i == 1 else None, now, e_role, e_from,
                     _origin.get(stage, "ladder")))
                if e_role:
                    audit(conn, pr_id, user.get("username") if user else "system",
                          "escalated_stage",
                          f"{stage_label(stage)}: signing escalated from "
                          f"{e_from or '—'} to {e_role} — the requester holds the "
                          f"normal signing role.", ip)
        stamp_step_actions(conn, pr_id)          # DOAM Table 5 (R / A / E)
        conn.execute(
            "UPDATE pr_requests SET status='pending', current_seq=1, submitted_at=?, "
            "rejection_reason=NULL, agg_total=?, budget_state=?, budget_over_by=? WHERE id=?",
            (now, _agg if _sibs else None, _bud["state"], _bud["over_by"], pr_id))
        if _sibs and _agg > _own:
            # Record it on the request, not just in a log: the approver about to
            # sign needs to see WHY this rung is on their desk for a small value.
            refs = ", ".join(s["pr_no"] or ("#%s" % s["id"]) for s in _sibs)
            audit(conn, pr_id, user.get("username") if user else "system",
                  "aggregated",
                  f"DOAM §3.4: routed on the {C.AGGREGATION_WINDOW_DAYS}-day "
                  f"aggregate of {_agg:,.2f} EGP (this request {_own:,.2f}) — "
                  f"related open requests for the same items: {refs}.", ip)
        uname = user.get("username") if user else "system"
        if _bud["unbudgeted"]:
            # On the request, not only in a log: the approver about to sign is
            # owed the reason the DOAM pulled their signature in.
            audit(conn, pr_id, uname, "unbudgeted",
                  unbudgeted_note(_bud, pr["department"],
                                  _pr_field(pr, "expenditure_kind")), ip)
        flat = [s for rung in rungs for s in rung]
        audit(conn, pr_id, uname, "submitted",
              f"Routed through {len(flat)} approvals: "
              f"{' -> '.join(' + '.join(stage_label(s) for s in rung) for rung in rungs)}", ip)
        # Notify every approver on the first rung that a request needs signing —
        # the ESCALATED role when that rung was re-pointed, so the notification
        # reaches whoever actually has to sign it.
        targets = set()
        for stage in rungs[0]:
            e_role = (esc.get(stage) or (None, None))[0]
            targets |= set(eligible_approvers(conn, stage,
                                              roles=_csv_set(e_role) or None))
        first_label = " + ".join(stage_label(s) for s in rungs[0])
        notify_users(conn, list(targets), "warning", "New request to sign",
                     f"{pr['pr_no']} ({pr['title'] or ''}) needs your {first_label} approval.",
                     link=_pr_link(pr_id))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Pricing gate (Purchasing enters the commercial value)
# --------------------------------------------------------------------------
def _item_keys(rows):
    """Normalised identity for a set of PR lines: the catalogue id when the line
    came from the catalogue, else the item name folded to lowercase. Two lines
    for "Bearing 6204" and "bearing 6204 " are the same purchase being split."""
    keys = set()
    for r in rows:
        cat = _pr_field(r, "item_id") or _pr_field(r, "spare_id")
        if cat:
            keys.add("cat:%s" % cat)
        name = str(_pr_field(r, "item") or "").strip().lower()
        if name:
            keys.add("name:%s" % " ".join(name.split()))
    return keys


def aggregated_total(conn, pr_id, pr, own_egp=None):
    """DOAM §3.4 — related purchases inside a 30-day window are aggregated, so
    splitting an order cannot buy a lower approval level.

    "Related" is read narrowly and defensibly: the SAME DEPARTMENT buying at
    least one of the SAME ITEMS. A broader rule (everything a department buys in
    a month) would drag genuinely unrelated purchases up to the Board and the
    control would be switched off within a week, which protects nothing.

    Returns (aggregate_egp, [siblings]) where each sibling is
    {"pr_no", "id", "egp"}. The PR's own value is included in the aggregate."""
    own = egp_commitment(pr) if own_egp is None else own_egp
    mine = _item_keys(conn.execute(
        "SELECT item, item_id, spare_id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall())
    if not mine:
        return own, []
    since = (datetime.now(timezone.utc)
             - timedelta(days=C.AGGREGATION_WINDOW_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT id, pr_no, total, currency, fx_rate, tax_rate FROM pr_requests "
        "WHERE department=? AND id<>? AND created_at>=? "
        "AND COALESCE(expenditure_kind,'opex')=? "
        "AND status NOT IN ('draft','cancelled','rejected')",
        (_pr_field(pr, "department") or "", pr_id, since,
         # Capital and operating spend are not the same purchase being split —
         # a machine and a box of consumables share nothing but a department.
         (_pr_field(pr, "expenditure_kind") or "opex"))).fetchall()
    if not rows:
        return own, []
    by_id = {r["id"]: r for r in rows}
    lines = conn.execute(
        "SELECT pr_id, item, item_id, spare_id FROM pr_items WHERE pr_id IN (%s)"
        % ",".join("?" * len(by_id)), tuple(by_id)).fetchall()
    related = {}
    for ln in lines:
        pid = ln["pr_id"]
        if pid in related or not (_item_keys([ln]) & mine):
            continue
        related[pid] = egp_commitment(by_id[pid])
    total = round(own + sum(related.values()), 2)
    sibs = [{"id": pid, "pr_no": by_id[pid]["pr_no"], "egp": amt}
            for pid, amt in related.items()]
    return total, sibs


def _reconcile_value_ladder(conn, pr_id, department, total):
    """After a pending PR is priced, bring its value-based rungs (Finance / CFO /
    CEO) in line with the new total: append the ones now required that aren't in
    the ladder yet, and drop any not-yet-reached value rungs that no longer
    qualify. Never touches steps that are approved, rejected, or currently active,
    so an in-flight approval is never disturbed."""
    row = conn.execute(
        "SELECT id, current_seq, currency, fx_rate, requester, pr_no, department, "
        "expenditure_kind, tax_rate FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    cur_seq = (row["current_seq"] or 0) if row else 0
    # Read the OPEX/CAPEX flag here rather than making both callers pass it —
    # it lives on the row we are already fetching.
    kind = _pr_field(row, "expenditure_kind") or "opex"
    # Thresholds are EGP-based: convert `total` (PR currency) to its EGP
    # equivalent using the row's currency/fx_rate. Reading them HERE (instead of
    # making each caller convert) keeps the call sites unchanged — price_pr and
    # set_fx pass the raw total, and any currency/fx_rate they just UPDATEd in
    # this same transaction is already visible to this SELECT.
    total_egp = egp_commitment({"total": total,
                                "tax_rate": row["tax_rate"] if row else 0,
                                "currency": row["currency"] if row else "EGP",
                                "fx_rate": row["fx_rate"] if row else 1})
    # DOAM §3.4, and this is where it actually bites: every request the UI
    # creates is submitted at zero (the requester price lockout), so the
    # aggregate computed at submit carried nothing. Pricing is the first moment
    # a real value exists, so the 30-day aggregate must be recomputed HERE or
    # the anti-split control never fires on a real request.
    _agg, _sibs = aggregated_total(conn, pr_id, row, total_egp)
    _route_on = max(total_egp, _agg)
    if _sibs and _agg > total_egp:
        conn.execute("UPDATE pr_requests SET agg_total=? WHERE id=?", (_agg, pr_id))
        audit(conn, pr_id, "system", "aggregated",
              f"DOAM §3.4: repriced and routed on the {C.AGGREGATION_WINDOW_DAYS}-day "
              f"aggregate of {_agg:,.2f} EGP (this request {total_egp:,.2f}) — related: "
              + ", ".join(x["pr_no"] or ("#%s" % x["id"]) for x in _sibs) + ".")
    target = [s for s in dept_ladder(conn, department, _route_on, kind, pr_id=pr_id)
              if s in VALUE_STAGES]
    # DOAM Table 4's rung is re-decided from scratch on every re-price, so drop
    # the not-yet-reached one HERE and let apply_budget_state (which both callers
    # run immediately after this) put it back on top of the freshly reconciled
    # value rungs. Without this the control rung keeps the low seq it was first
    # written at, so the CFO is asked to sign before the directors below it and
    # the ladder becomes a function of pricing HISTORY rather than of (value,
    # budget state). Signed / active rungs are never touched (seq > cur_seq).
    conn.execute("DELETE FROM pr_steps WHERE pr_id=? AND origin='unbudgeted' "
                 "AND status='pending' AND seq>?", (pr_id, cur_seq))
    # §4.3's single-source rung has exactly the same problem, and it was left
    # behind when Table 4's was fixed. "Approved one level above the value tier"
    # is a function of the CURRENT value, and it was computed once at the waiver
    # and never re-read. Measured through the screen: price 30,000, waive
    # competition, re-price to 600,000 — the waiver rung was still the CFO, a
    # signature 600,000 requires anyway, so waiving competition bought ZERO
    # extra approval and the Board was never asked. Dropped here and re-derived
    # by apply_single_source_state below, against the ladder as it now stands.
    conn.execute("DELETE FROM pr_steps WHERE pr_id=? AND origin='single_source' "
                 "AND status='pending' AND seq>?", (pr_id, cur_seq))
    existing = conn.execute(
        "SELECT id, seq, stage, status, esc_role, approver_user, "
        "COALESCE(origin,'ladder') AS origin FROM pr_steps "
        "WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()
    # Every rung still here is one the value ladder may count on: §4.3 and §4.4
    # rungs are only ever ADDED and never removed, and the one control rung that
    # IS removable (Table 4's) was just dropped above if it is still ahead of the
    # request. What survives that DELETE has been signed or is being signed, so
    # the signature it stands for genuinely exists. Filtering by origin here
    # instead would append a SECOND cfo rung behind a deviation one.
    have = {r["stage"] for r in existing}
    max_seq = max([r["seq"] for r in existing] or [0])
    now = _now()
    # 1) prune future, still-pending value rungs that no longer qualify
    for r in existing:
        if r["stage"] in VALUE_STAGES and r["stage"] not in target \
           and r["status"] == "pending" and r["seq"] > cur_seq:
            # A rung placed by a CONTROL (§4.3 single source, §4.4 deviation)
            # is not a value rung and must survive a re-price. Without this,
            # anyone able to price a request could strip its escalations by
            # saving the same price again, and nothing was written down.
            if r["origin"] != "ladder":
                continue
            conn.execute("DELETE FROM pr_steps WHERE id=?", (r["id"],))
            audit(conn, pr_id, "system", "ladder_rung_removed",
                  f"{stage_label(r['stage'])} dropped: the repriced value "
                  f"{_route_on:,.2f} EGP no longer requires it.")
    # 2) append any newly-required value rungs (in ladder order) after the last seq
    #    — with the same SoD escalation the submit-time ladder gets, because a
    #    value rung that only the requester could sign would deadlock identically.
    #    A rung the climb cannot resolve is still appended (removing it would drop
    #    a required financial approval) but stamped esc_role='' and reported to the
    #    Procurement admins, who can delegate or sign it.
    nxt = max_seq
    requester = row["requester"] if row else None
    _roles = stage_roles_map(conn)
    _chain = escalation_map(conn)
    # Same dual-role guard the submit-time climb uses, over the ladder as it will
    # stand: the rungs that survive above plus the ones being appended. `_busy`
    # adds whoever ALREADY signed a rung of this request — the dual-role rule can
    # never let them sign another one, so escalating onto them would deadlock.
    _live = [r["stage"] for r in existing
             if not (r["stage"] in VALUE_STAGES and r["stage"] not in target
                     and r["status"] == "pending" and r["seq"] > cur_seq)]
    _esc = {r["stage"]: r["esc_role"] for r in existing}
    _holders = {s: stage_signers(conn, s, _csv_set(_esc.get(s)) or _roles.get(s) or ())
                for s in dict.fromkeys(_live + list(target))}
    _busy = {r["approver_user"] for r in existing
             if r["status"] in ("approved", "rejected") and r["approver_user"]}
    for s in target:
        if s not in have:
            nxt += 1
            e_role, e_from, why = _esc_columns(conn, s, requester, _roles, _chain,
                                               _holders=_holders, _busy=_busy)
            conn.execute(
                "INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, "
                "created_at, esc_role, esc_from, origin) VALUES (?,?,?,?,?,?,?,?,?)",
                (pr_id, nxt, s, "pending", stage_label(s), now,
                 "" if why == "no_superior" else e_role, e_from, "ladder"))
            if why == "escalated":
                audit(conn, pr_id, "system", "escalated_stage",
                      f"{stage_label(s)}: signing escalated from {e_from or '—'} to "
                      f"{e_role} — nobody who normally signs it may on this request "
                      f"(the originator, or they already signed another rung).")
            elif why == "no_superior":
                audit(conn, pr_id, "system", "escalation_blocked",
                      f"{stage_label(s)} joined the ladder after pricing, but nobody who "
                      f"normally signs it may on this request (the originator, or they "
                      f"already signed another rung) and no superior above that role has "
                      f"anyone free to sign it — a delegation or an admin is needed.")
                notify_users(conn, _proc_admins(conn), "critical",
                             "Approval stage cannot be signed",
                             f"{(row['pr_no'] if row else pr_id)}: {stage_label(s)} was "
                             f"added by pricing, but nobody who normally signs it may on "
                             f"this request ({requester} raised it, or the signer already "
                             f"signed another rung) and no superior above that role has "
                             f"anyone free to sign it.", link=_pr_link(pr_id))
    # The ladder's shape just changed, so the Table 5 letters move with it: the
    # rung that WAS the top (and so the Approve) is now a Review below the
    # director pricing pulled in.
    stamp_step_actions(conn, pr_id)


def price_pr(pr_id, prices, meta, user, ip=None, vendors=None):
    """Purchasing enters commercial pricing for a request — the pricing gate.

    `vendors` maps pr_items.id -> the supplier for THAT line. It is set here
    rather than on the request form because naming a supplier is a sourcing
    decision, not a statement of need — the requester says what they want, and
    Purchasing decide who supplies it at the same moment they record what that
    supplier charges. A blank leaves the line on the request's header vendor,
    which is how every request raised before per-line vendors still behaves.

    `prices` maps pr_items.id -> unit_price; `meta` may carry tax_rate,
    payment_condition, vendor and currency (all Purchasing-owned). The line
    costs and PR total are recomputed, the PR is marked 'priced', and — if it is
    already circulating — the value-based approval rungs the new total requires
    are added to the ladder. Returns (ok, msg)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("approved", "po_issued", "partially_received",
                             "received", "closed", "cancelled"):
            return False, "locked"
        # Foreign-currency guard (fail fast, before any write): a non-EGP PR must carry
        # a REAL exchange rate at pricing time. Otherwise egp_total() falls back to 1:1
        # and the entire value ladder (Finance/CFO/CEO) + the RFQ gate route on the raw
        # foreign figure — a 20,000 USD (~1,000,000 EGP) order would clear a mid-level
        # ladder. EGP requests are unaffected.
        eff_cur = str(meta.get("currency") or pr["currency"] or "EGP").strip().upper()
        if eff_cur != "EGP":
            _mfx = meta.get("fx_rate")
            try:
                _mfx = float(_mfx) if (_mfx is not None and str(_mfx).strip() != "") else 0.0
            except (TypeError, ValueError):
                _mfx = 0.0
            try:
                _pfx = float(pr["fx_rate"] or 1)
            except (TypeError, ValueError):
                _pfx = 1.0
            eff_fx = _mfx if _mfx > 0 else _pfx
            if eff_fx <= 0 or abs(eff_fx - 1.0) < 1e-9:   # 1.0 == the unset default
                return False, "fx_required"
        items = conn.execute(
            "SELECT id, qty, unit_price FROM pr_items WHERE pr_id=? ORDER BY seq",
            (pr_id,)).fetchall()
        total = 0.0
        for it in items:
            up = prices.get(it["id"], prices.get(str(it["id"])))
            try:
                up = float(up)
            except (TypeError, ValueError):
                up = float(it["unit_price"] or 0)   # keep existing if not supplied
            up = max(up, 0.0)
            est = round(float(it["qty"] or 0) * up, 2)
            conn.execute("UPDATE pr_items SET unit_price=?, est_cost=? WHERE id=?",
                         (up, est, it["id"]))
            # The supplier for this line, if Purchasing named one. A key that was
            # not posted at all leaves the line alone; a key posted BLANK clears
            # it back to the header vendor, which is how a buyer un-splits a
            # request they had split across suppliers.
            if vendors:
                lv = vendors.get(it["id"], vendors.get(str(it["id"])))
                if lv is not None:
                    conn.execute("UPDATE pr_items SET vendor=? WHERE id=?",
                                 (str(lv).strip()[:120] or None, it["id"]))
            total += est
        total = round(total, 2)

        sets = ["total=?", "pricing_status='priced'", "priced_at=?", "priced_by=?"]
        params = [total, _now(), (user or {}).get("username")]
        tax_rate = meta.get("tax_rate")
        if tax_rate is not None and str(tax_rate).strip() != "":
            sets.append("tax_rate=?"); params.append(float(tax_rate or 0))
        # FX rate (EGP per unit of the PR currency) — Purchasing may set it while
        # pricing so the value ladder below routes on the true EGP equivalent.
        fx_rate = meta.get("fx_rate")
        if fx_rate is not None and str(fx_rate).strip() != "":
            try:
                fx_val = float(fx_rate)
            except (TypeError, ValueError):
                fx_val = 0.0
            if fx_val > 0:
                sets.append("fx_rate=?"); params.append(fx_val)
        for col in ("payment_condition", "vendor", "currency"):
            val = meta.get(col)
            if val:
                sets.append(f"{col}=?"); params.append(val)
        params.append(pr_id)
        conn.execute("UPDATE pr_requests SET " + ", ".join(sets) + " WHERE id=?", params)
        # The lines follow the header vendor Purchasing just corrected — the ones
        # still carrying the OLD one, that is; a line deliberately pointed at
        # another supplier keeps its own. create_pr() seeds every line from the
        # header (the REQUESTER's suggestion), and the purchase order, its PDF
        # and the email now all resolve the supplier from the line. Without this
        # the order went to the company the requester guessed at instead of the
        # one Purchasing chose — worst on the maintenance auto-reorder bridge,
        # whose requests are unpriced by design and all pass through here.
        if meta.get("vendor") and meta["vendor"] != pr["vendor"]:
            conn.execute("UPDATE pr_items SET vendor=? WHERE pr_id=? AND "
                         "(vendor IS NULL OR vendor='' OR vendor=?)",
                         (meta["vendor"], pr_id, pr["vendor"]))

        if pr["status"] == "pending":
            _reconcile_value_ladder(conn, pr_id, pr["department"], total)
            # DOAM §4.4 — deviation grading runs HERE, immediately after the
            # value ladder is reconciled: the prices being graded are the ones
            # just entered, and the extra rungs sit on top of the value rungs
            # rather than being reshuffled by the reconcile that follows.
            apply_deviation_stages(conn, pr_id, pr)
            # DOAM Table 4 — budgeted or unbudgeted, decided on the value that
            # was just entered. LAST, so the L1 signature this may add is the
            # senior one at the top of the ladder rather than a rung the
            # directors sign after.
            apply_budget_state(conn, pr_id, ip=ip)
            apply_single_source_state(conn, pr_id, ip=ip)

        cur = meta.get("currency") or pr["currency"]
        audit(conn, pr_id, (user or {}).get("username"), "priced",
              f"Pricing entered by Purchasing — total {total:,.2f} {cur}", ip)
        notify_users(conn, [pr["requester"]], "info", "Request priced",
                     f"{pr['pr_no']} has been priced by Purchasing and is moving "
                     f"through the approval ladder.", link=_pr_link(pr_id))
        conn.commit()
        return True, "priced"
    finally:
        conn.close()


# --------------------------------------------------------------------------
# approve / reject
# --------------------------------------------------------------------------
def _record_sign_event(conn, pr, step, decision, uname, signer_name, now, ip=None):
    """Immutable, publicly-verifiable record of one signature. The doc_hash
    anchors exactly what was signed; `code` is the handle printed on the PDF
    (…/procurement/verify/<code>). Best-effort: never blocks the decision."""
    try:
        code = secrets.token_urlsafe(9)
        payload = "|".join(str(x) for x in (
            pr["pr_no"], step["seq"], step["stage"], decision, uname,
            f"{float(pr['total'] or 0):.2f}", pr["currency"] or "", now))
        doc_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        conn.execute(
            """INSERT INTO pr_sign_events
               (code, pr_id, seq, stage, action, signer, signer_name, doc_hash, ip, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (code, pr["id"], step["seq"], step["stage"], decision, uname,
             signer_name, doc_hash, ip, now))
        return code
    except Exception:
        return None


def sign_events(pr_id):
    conn = get_db()
    try:
        return conn.execute(
            "SELECT * FROM pr_sign_events WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()
    finally:
        conn.close()


def verify_sign_code(code):
    """Public verification: look a signature event up by its printed code.
    Returns (event, pr) or (None, None)."""
    if not code or len(str(code)) > 40:
        return None, None
    conn = get_db()
    try:
        ev = conn.execute("SELECT * FROM pr_sign_events WHERE code=?", (str(code),)).fetchone()
        if not ev:
            return None, None
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (ev["pr_id"],)).fetchone()
        return ev, pr
    finally:
        conn.close()


def _at_or_past_pricing_gate(conn, pr_id, step):
    """True on Purchasing's rung and on every rung ABOVE it.

    The document gates (quotes, §4.4 memo) run from here up, not from the
    bottom: Purchasing owns both documents, so a rung below theirs could only
    block on something its signer cannot supply. Running them only ON the
    Purchasing rung was the other half of the mistake — price_pr stays open
    while a request circulates, so a request priced on plan, signed, then
    re-priced 40% over reached 'approved' with no memo and no third quote.
    """
    if step["stage"] == PRICING_GATE_STAGE:
        return True
    return bool(conn.execute(
        "SELECT 1 FROM pr_steps WHERE pr_id=? AND stage=? AND status='approved' "
        "LIMIT 1", (pr_id, PRICING_GATE_STAGE)).fetchone())


def act_on_step(pr_id, user, decision, comment=None, ip=None):
    """Approve or reject the PR's current pending step.
    decision in {'approve','reject'}. Returns (ok, msg)."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] != "pending":
            return False, "not_pending"
        # A record retired from the register (dispose_pr) is out of the register
        # for every purpose, signing included — it was still signable all the
        # way to 'approved'. Only an explicit 0 blocks: a NULL is a pre-migration
        # row, not a retirement.
        if _pr_field(pr, "is_active", 1) == 0:
            return False, "retired"
        # current rung may hold several parallel steps; pick the one this user
        # is eligible for (a co-approver only acts on their own stage).
        cur_steps = conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
            (pr_id, pr["current_seq"])).fetchall()
        if not cur_steps:
            return False, "no_active_step"
        _roles = stage_roles_map(conn)     # reuse this transaction's connection
        # can_act_step, not can_act: a rung the ladder escalated (because the
        # requester is its only normal signer) is signed by the superior role.
        step = next((s for s in cur_steps if can_act_step(user, s, _roles=_roles)), None)
        if not step:
            return False, "forbidden"

        uname = user.get("username")

        # Segregation of Duties (P6). Two independence rules:
        #   (a) self-approval — the requester may never sign a stage of their own
        #       request: raising it IS their signature, so signing it again would
        #       collapse originator and approver into one person;
        #   (b) dual-role — one person may not sign two DIFFERENT stages of the
        #       same request (e.g. warehouse, then factory via a delegation): each
        #       rung must be an independent pair of eyes. Steps of a rejected +
        #       resubmitted PR are rebuilt from scratch, so old history rows never
        #       trip this check.
        # Platform admins (super_admin / proc_admin — the same predicate can_act
        # uses) are exempt while the 'sod_admin_exempt' setting is on (DB override
        # -> C.SOD_ADMIN_EXEMPT), so a small team can still operate the full
        # ladder. `_is_admin` is tested FIRST so the setting is only read for the
        # handful of admin signatures — same result, one fewer query for everyone.
        role = user.get("role")
        _is_admin = role == "super_admin" or has_permission(role, "proc_admin")
        # DOAM §3.4: "No person may approve a transaction that also names that
        # person as requestor or beneficiary." That one is absolute — it is the
        # single control standing between one account and a self-authorised
        # purchase, so NO setting and no admin role waives it. Only the
        # dual-role rule below stays waivable, because a genuinely small team
        # can have one person legitimately holding two ladder roles.
        # ...but only for an APPROVAL. The clause governs approving; rejecting
        # your own request is withdrawing it, which is legitimate and harmless.
        # Blocking that too stranded the request with nobody able to close it.
        if decision == "approve" and pr["requester"] == uname:
            return False, "self_approval"
        if not (_is_admin and bool_setting(conn, "sod_admin_exempt")):
            other = conn.execute(
                "SELECT 1 FROM pr_steps WHERE pr_id=? AND id!=? AND approver_user=? "
                "AND status IN ('approved','rejected') LIMIT 1",
                (pr_id, step["id"], uname)).fetchone()
            if other:
                return False, "dual_role"

        # Pricing gate: the purchasing stage cannot be signed off until Purchasing
        # has entered the commercial value. Approving it unpriced would let a
        # zero-value request slip past the value-based Finance / CFO / CEO rungs.
        if decision == "approve" and step["stage"] == PRICING_GATE_STAGE \
           and (pr["pricing_status"] or "priced") != "priced":
            return False, "needs_pricing"

        # RFQ gate: high-value orders need competitive quotes (or a recorded
        # single-source justification) before Purchasing signs off.
        if decision == "approve" and _at_or_past_pricing_gate(conn, pr_id, step):
            ok_rfq, rfq_msg = rfq_gate_check(conn, pr)
            if not ok_rfq:
                return False, rfq_msg

        # Engineering gate (DOAM §6): a spares / MRO / workshop requisition needs
        # an Engineering-Head-signed justification before Procurement accepts it,
        # AT ANY VALUE. Enforced here because the Purchasing stage IS Procurement's
        # acceptance point. Automatic min/max replenishment is exempt — the DOAM
        # says so, and gating it would break the auto-reorder bridge.
        #
        # An EVALUATION that raises fails CLOSED: a control that stops working
        # silently because a query raised is worse than one that blocks.
        # A missing maintenance module is a different fact — no maintenance
        # module means no maintenance requisitions to gate, and freezing every
        # department's purchasing over an ImportError is not a safer failure,
        # it is a bigger one.
        if decision == "approve" and step["stage"] == PRICING_GATE_STAGE:
            try:
                from app.maintenance.eng_justification import ejr_gate_check
                items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
                ok_ejr, ejr_msg = ejr_gate_check(conn, pr, items)
            except ImportError:
                ok_ejr, ejr_msg = True, "maintenance_module_absent"
            except Exception as exc:  # noqa: BLE001
                return False, f"ejr_check_failed:{type(exc).__name__}"
            if not ok_ejr:
                return False, ejr_msg

        # DOAM §4.4 — the justification memo (T&C-PUF-12). An order graded
        # off-plan collects extra signatures AND has to carry the memo those
        # signers are meant to read. Checked at Procurement's acceptance point,
        # beside the other document gates.
        if decision == "approve" and _at_or_past_pricing_gate(conn, pr_id, step):
            # …and the SAME reading of stock has to buy the SIGNATURES, not only
            # the memo. Grading ran once, at pricing, while this gate recomputes
            # from stock as it is now — so a delivery landing after pricing left
            # the buyer blocked for a memo, deviation_findings asking for a
            # Plant Director, and no rung and no audit row ever created. Re-run
            # the escalation here so both halves of §4.4 read the same numbers
            # at the same moment. Add-only and idempotent; committed straight
            # away because the escalation is a fact whether or not this
            # signature then goes through — the blocked case is precisely the
            # one where the rung must survive.
            if (pr["pricing_status"] or "priced") == "priced":
                apply_deviation_stages(conn, pr_id, pr)
                conn.commit()
            ok_memo, memo_msg = deviation_memo_gate(conn, pr_id, pr)
            if not ok_memo:
                return False, memo_msg

        # DOAM §4.1 tier 7 / §4.2 tier 4 — above BUSINESS_CASE_OVER the Board
        # signs "with a business case". Checked TWICE, and both are load-bearing:
        # at the Purchasing rung so an oversized request never even circulates
        # without one, and again at the Board rung because a request can be
        # RE-PRICED upward after Purchasing has already signed (price_pr stays
        # open while the request is pending) — the second check is the one that
        # catches the 5M order that became 12M on its way up the ladder.
        if decision == "approve" and step["stage"] in (PRICING_GATE_STAGE, "bod"):
            ok_bc, bc_msg = business_case_gate(pr)
            if not ok_bc:
                return False, bc_msg

        sig_png, sig_name = _user_sig(uname)
        now = _now()

        if decision == "reject":
            conn.execute(
                "UPDATE pr_steps SET status='rejected', approver_user=?, approver_name=?, "
                "approver_role=?, comment=?, sig_png=?, acted_at=? WHERE id=?",
                (uname, sig_name or user.get("full_name") or uname, user.get("role"),
                 comment, sig_png, now, step["id"]))
            _record_sign_event(conn, pr, step, "reject", uname,
                               sig_name or user.get("full_name") or uname, now, ip)
            conn.execute(
                "UPDATE pr_requests SET status='rejected', rejection_reason=? WHERE id=?",
                (comment or f"Rejected at {stage_label(step['stage'])}", pr_id))
            audit(conn, pr_id, uname, "rejected",
                  f"{stage_label(step['stage'])} rejected: {comment or ''}", ip)
            notify_users(conn, [pr["requester"]], "warning", "Request rejected",
                         f"{pr['pr_no']} was rejected at {stage_label(step['stage'])}"
                         + (f": {comment}" if comment else "."), link=_pr_link(pr_id))
            conn.commit()
            return True, "rejected"

        # approve — DOAM Table 5: WHICH of R / A / E this signature is was decided
        # when the rung was written, and the trail records it as its own event
        # ('reviewed' / 'approved' / 'endorsed'), so a Review can be told from an
        # Approve by a query and not only by reading the sentence.
        act = step_action_of(step)
        conn.execute(
            "UPDATE pr_steps SET status='approved', approver_user=?, approver_name=?, "
            "approver_role=?, comment=?, sig_png=?, acted_at=? WHERE id=?",
            (uname, sig_name or user.get("full_name") or uname, user.get("role"),
             comment, sig_png, now, step["id"]))
        _record_sign_event(conn, pr, step, act, uname,
                           sig_name or user.get("full_name") or uname, now, ip)
        audit(conn, pr_id, uname, C.STEP_ACTION_PAST[act],
              f"{stage_label(step['stage'])} {C.STEP_ACTION_PAST[act]} "
              f"({C.STEP_ACTION_CODE[act]}) — {C.STEP_ACTION_WHY[act]}"
              + (f" {comment}" if comment else ""), ip)

        # Parallel rung: if co-approvers at this seq are still pending, wait.
        remaining = conn.execute(
            "SELECT COUNT(*) c FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
            (pr_id, step["seq"])).fetchone()["c"]
        if remaining > 0:
            conn.commit()
            return True, "partial"

        # Whole rung approved -> advance to the next rung (activate all its steps).
        nxt_seq = conn.execute(
            "SELECT MIN(seq) m FROM pr_steps WHERE pr_id=? AND seq>? AND status='pending'",
            (pr_id, step["seq"])).fetchone()["m"]
        if nxt_seq is not None:
            conn.execute("UPDATE pr_requests SET current_seq=? WHERE id=?", (nxt_seq, pr_id))
            conn.execute("UPDATE pr_steps SET activated_at=? WHERE pr_id=? AND seq=?",
                         (now, pr_id, nxt_seq))
            nxt_rows = conn.execute(
                "SELECT * FROM pr_steps WHERE pr_id=? AND seq=?", (pr_id, nxt_seq)).fetchall()
            nxt_stages = [r["stage"] for r in nxt_rows]
            targets = set()
            for r in nxt_rows:
                targets |= set(eligible_approvers(
                    conn, r["stage"], roles=_csv_set(_pr_field(r, "esc_role")) or None))
            notify_users(conn, list(targets), "warning", "New request to sign",
                         f"{pr['pr_no']} needs your "
                         f"{' + '.join(stage_label(s) for s in nxt_stages)} approval.",
                         link=_pr_link(pr_id))
            conn.commit()
            return True, "advanced"

        # last step approved -> PR approved, auto-generate a PO number
        po_no = doc_no("PO", pr_id)
        conn.execute(
            "UPDATE pr_requests SET status='approved', current_seq=0, approved_at=?, "
            "po_no=? WHERE id=?", (now, po_no, pr_id))
        audit(conn, pr_id, uname, "fully_approved",
              f"All approvals complete. PO {po_no} drafted.", ip)
        # tell the requester it's approved, and purchasing that a PO is ready
        notify_users(conn, [pr["requester"]], "info", "Request approved",
                     f"{pr['pr_no']} is fully approved. PO {po_no} drafted.", link=_pr_link(pr_id))
        notify_users(conn, eligible_approvers(conn, "purchasing"), "info", "PO ready to issue",
                     f"{pr['pr_no']} approved — issue PO {po_no}.", link=_pr_link(pr_id))
        conn.commit()
        return True, "approved"
    finally:
        conn.close()


def _unbudgeted_authority_given(pr_id, pr):
    """True when this request was ROUTED as unbudgeted and the L1 rung Table 4
    added for it has actually been signed.

    The note the L1 approver read says the spend "is not refused — it needs
    higher authority, not refusal". Refusing the PO afterwards contradicts the
    signature the control itself asked for. A request that reached 'approved'
    without ever being routed as unbudgeted (budget_state NULL or 'budgeted' —
    e.g. the budget was cut after approval) never got that signature, so the
    original block still applies to it.
    """
    if (_pr_field(pr, "budget_state") or "") not in ("no_budget", "over_budget"):
        return False
    conn = get_db()
    try:
        return conn.execute(
            "SELECT 1 FROM pr_steps WHERE pr_id=? AND origin='unbudgeted' "
            "AND status='approved' LIMIT 1", (pr_id,)).fetchone() is not None
    finally:
        conn.close()


def po_groups(items, pr):
    """Order lines grouped by the supplier whose document they belong on.

    A line with no vendor of its own belongs to the request's header vendor —
    which is every line of every request raised before per-line vendors existed.
    Insertion-ordered, so the FIRST line's vendor owns the primary PO (the number
    pr_requests.po_no keeps).

    Grouped on C.vendor_key (trimmed, case-folded) so 'Alphatex' and 'ALPHATEX'
    are one supplier and one document, as issue_rfqs already treats them; the
    first spelling seen is the one printed. Keys stay the DISPLAY name — the RFQ
    vendor list on the detail page reads them straight."""
    groups, first = {}, {}
    for it in items:
        d = it if isinstance(it, dict) else dict(it)
        v = (d.get("vendor") or _pr_field(pr, "vendor") or "").strip()
        name = first.setdefault(C.vendor_key(v), v)
        groups.setdefault(name, []).append(d)
    return groups


def _issue_po_rows(conn, pr_id, pr, base_no, user):
    """Write one pr_purchase_orders row per distinct vendor on the request.

    The first vendor keeps `base_no` — the number drafted at full approval and
    already quoted in the bell, the audit trail and every link — so a
    single-vendor request is numbered exactly as it is today. Later vendors hang
    off the same number (-2, -3 …): unique by construction, where a second
    id-based sequence through doc_no() would collide with the PO-YYYY-<pr id>
    numbers already in the register.

    Idempotent: rows already written are returned untouched."""
    existing = conn.execute(
        "SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()
    if existing:
        return [dict(r) for r in existing]
    items = conn.execute("SELECT * FROM pr_items WHERE pr_id=? ORDER BY seq, id",
                         (pr_id,)).fetchall()
    groups = po_groups(items, pr) or {(_pr_field(pr, "vendor") or ""): []}
    try:
        rate = float(_pr_field(pr, "tax_rate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    cur_code, now = _pr_field(pr, "currency") or "EGP", _now()
    uname = (user or {}).get("username")
    rev = int(_pr_field(pr, "po_rev") or 0)
    head_sub = round(float(_pr_field(pr, "total") or 0), 2)
    rows = list(groups.items())
    # Single vendor -> the header total itself, so the document is byte-for-byte
    # what it prints today; only a split re-adds the lines per supplier.
    subs = [head_sub if len(rows) == 1 else round(sum(_amount(l) for l in lines), 2)
            for _v, lines in rows]
    taxes = [round(s * rate / 100.0, 2) for s in subs]
    # The orders must sum to the request they came from: three lines of 100.05 at
    # 14% round to 342.18 across three documents where pr_amounts() reports
    # 342.17, so the three suppliers' orders were a piastre more than the
    # requisition that authorised them. The residual goes to the PRIMARY order —
    # and only when the split genuinely ties to the request total, so no order is
    # ever given tax its own subtotal does not carry.
    if len(subs) > 1 and abs(sum(subs) - head_sub) < 0.005:
        taxes[0] = round(round(head_sub * rate / 100.0, 2) - sum(taxes[1:]), 2)
    out = []
    for n, ((vendor, _lines), sub, tax) in enumerate(zip(rows, subs, taxes), start=1):
        po_no = base_no if n == 1 else f"{base_no}-{n}"
        row = {"pr_id": pr_id, "po_no": po_no, "vendor": vendor or None,
               "currency": cur_code, "subtotal": sub, "tax": tax,
               "grand": round(sub + tax, 2), "rev": rev, "status": "issued",
               "issued_at": now, "issued_by": uname}
        c = conn.execute(
            """INSERT INTO pr_purchase_orders
               (pr_id, po_no, vendor, currency, subtotal, tax, grand, rev, status,
                issued_at, issued_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pr_id, po_no, row["vendor"], cur_code, sub, tax, row["grand"], rev,
             "issued", now, uname))
        row["id"] = c.lastrowid
        out.append(row)
    return out


def _pos_for(conn, pr):
    """Every purchase order issued for a request — the read-through helper.

    FALLBACK, not a migration: a request issued before the per-vendor split has
    no rows here and its one order lives in pr_requests.po_no, so it is
    synthesised on read. Historic rows are never rewritten."""
    pr_id = _pr_field(pr, "id")
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id",
            (pr_id,)).fetchall()]
    except Exception:
        conn.rollback()          # database predating the table
        rows = []
    if rows:
        return rows
    po_no = (_pr_field(pr, "po_no") or "").strip()
    if not po_no:
        return []
    amt = pr_amounts(dict(pr))
    return [{"id": None, "pr_id": pr_id, "po_no": po_no,
             "vendor": _pr_field(pr, "vendor"), "currency": _pr_field(pr, "currency"),
             "subtotal": amt["subtotal"], "tax": amt["tax"], "grand": amt["grand"],
             "rev": int(_pr_field(pr, "po_rev") or 0),
             "status": "issued" if _pr_field(pr, "status") != "approved" else "drafted",
             "issued_at": None, "issued_by": None}]


def split_order(conn, pr, po_id):
    """The purchase order an invoice or a payment on a SPLIT requisition belongs
    to. Returns (order_row, error).

    (None, None) means the requisition is NOT split — one supplier, so the
    request IS the order and every existing request-level cap already bounds it
    exactly as it did before per-line vendors existed. The caller then skips the
    per-order caps entirely and behaves bit-for-bit as before.

    (None, "po_required") means it IS split and the caller did not say which
    supplier's order the money is for. Fail closed: without it, one supplier can
    be billed and paid up to the combined value of every other supplier's
    order."""
    pos = _pos_for(conn, pr)
    if len(pos) < 2:
        return None, None
    try:
        want = int(po_id)
    except (TypeError, ValueError):
        return None, "po_required"
    po = next((p for p in pos if p.get("id") == want), None)
    return (po, None) if po else (None, "po_required")


def po_exposure(conn, pr, po):
    """What ONE purchase order has been billed and paid, and the most it may be.

    The same arithmetic three_way_match applies to a requisition, restricted to
    one order's vendor: ordered is the order's own grand total, received is its
    own lines, and the debit notes are the ones raised against its own supplier.
    Only a split requisition needs it — see split_order()."""
    pr_id, po_id = _pr_field(pr, "id"), po.get("id")
    vendor = (po.get("vendor") or "").strip()
    # Line matching uses the same supplier identity po_groups() grouped on and
    # the PDF filters on: a line counted INTO this order's subtotal must count as
    # received against it. The debit-note lookup below stays on the exact stored
    # name — it is a money ceiling, and SQL's LOWER() folds differently from
    # Python's for non-ASCII names.
    vkey = C.vendor_key(vendor)
    head = _pr_field(pr, "vendor")
    try:
        rate = float(_pr_field(pr, "tax_rate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    items = conn.execute(
        "SELECT vendor, received_qty, unit_price FROM pr_items WHERE pr_id=?",
        (pr_id,)).fetchall()
    received = round(sum(float(i["received_qty"] or 0) * float(i["unit_price"] or 0)
                         for i in items
                         if C.vendor_key(i["vendor"] or head) == vkey), 2)
    received_grand = round(received * (1 + rate / 100.0), 2)

    def _sum(sql, *args):
        row = conn.execute(sql, args).fetchone()
        try:
            return round(float(row["s"] or 0), 2) if row else 0.0
        except (TypeError, ValueError):
            return 0.0

    invoiced = _sum("SELECT COALESCE(SUM(COALESCE(amount,0)+COALESCE(tax,0)),0) AS s "
                    "FROM pr_invoices WHERE pr_id=? AND po_id=?", pr_id, po_id)
    paid = _sum("SELECT COALESCE(SUM(COALESCE(amount,0)),0) AS s "
                "FROM pr_payments WHERE pr_id=? AND po_id=?", pr_id, po_id)
    # Keyed on the NAME, the way a PR names its supplier and the way
    # _record_return writes it.
    debit_open = _sum("SELECT COALESCE(SUM(COALESCE(total,0)),0) AS s FROM pr_returns "
                      "WHERE pr_id=? AND status='open' AND COALESCE(TRIM(vendor),'')=?",
                      pr_id, vendor)
    ordered = round(float(po.get("grand") or 0), 2)
    return {"ordered": ordered, "invoiced": invoiced, "paid": paid,
            "received_grand": received_grand, "debit_open": debit_open,
            "payable": round(max(0.0, min(received_grand, ordered - debit_open)), 2)}


def issue_po(pr_id, user, ip=None, force=False):
    """Mark an approved PR's PO as issued (purchasing action). When an explicit
    department budget exists and this PO would leave it exceeded, issuing is
    blocked unless an admin overrides (audited). Departments with no budget row
    configured are never blocked."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("approved",):
            return False, "not_approved"
    finally:
        conn.close()
    if not force and not _unbudgeted_authority_given(pr_id, pr):
        b = budget_status(pr["department"])   # PR already counted in 'spent' (approved)
        if b and b.get("amount") is not None and b.get("over"):
            return False, "over_budget"
    conn = get_db()
    try:
        if force:
            audit(conn, pr_id, (user or {}).get("username"), "po_override",
                  "PO issued with admin override (budget exceeded)", ip)
        pos = _issue_po_rows(conn, pr_id, pr, pr["po_no"] or doc_no("PO", pr_id), user)
        # The PRIMARY order's number stays on pr_requests.po_no: it is what every
        # screen, PDF link, report and the maintenance bridge resolve against.
        po_no = pos[0]["po_no"]
        conn.execute("UPDATE pr_requests SET status='po_issued', po_no=? WHERE id=?",
                     (po_no, pr_id))
        each = ", ".join(f"{p['po_no']} ({p['vendor'] or '—'})" for p in pos)
        audit(conn, pr_id, user.get("username"), "po_issued",
              f"PO {po_no} issued" if len(pos) == 1
              else f"{len(pos)} purchase orders issued, one per vendor: {each}", ip)
        bell(conn, "info", "Purchase Order issued",
             f"{po_no} issued for {pr['pr_no']}." if len(pos) == 1
             else f"{len(pos)} purchase orders issued for {pr['pr_no']}, "
                  f"one per vendor: {each}.")
        conn.commit()
        return True, po_no
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Foreign currency (FX) + PO revisions
# --------------------------------------------------------------------------
def egp_total(pr):
    """EGP-equivalent total for a PR dict/row: total * fx_rate when the PR is in
    a foreign currency, else the total unchanged. Defensive on missing columns
    and bad values so ladder routing never crashes on a legacy row."""
    def _g(key):
        try:
            return pr.get(key) if isinstance(pr, dict) else pr[key]
        except (KeyError, IndexError):
            return None
    if pr is None:
        return 0.0
    try:
        total = float(_g("total") or 0)
    except (TypeError, ValueError):
        total = 0.0
    cur = str(_g("currency") or "EGP").strip().upper()
    if cur == "EGP":
        return round(total, 2)
    try:
        rate = float(_g("fx_rate") or 1)
    except (TypeError, ValueError):
        rate = 1.0
    if rate <= 0:
        rate = 1.0
    return round(total * rate, 2)


def set_fx(pr_id, rate, user, ip=None):
    """Purchasing sets the EGP conversion rate for a foreign-currency request so
    the EGP-based value thresholds route on the true equivalent. Locked once the
    request is approved or beyond (the signed ladder must never shift under a
    finished approval). While the PR is still pending, the value rungs (Finance /
    CFO / CEO) are reconciled against the NEW EGP-equivalent total — the rate is
    UPDATEd first, so _reconcile_value_ladder reads it in-transaction and the
    raw `total` it receives converts with the fresh rate. Returns (ok, msg)."""
    try:
        rate = float(rate)
    except (TypeError, ValueError):
        return False, "bad_rate"
    if rate <= 0:
        return False, "bad_rate"
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("approved", "po_issued", "partially_received",
                            "received", "closed", "cancelled"):
            return False, "locked"
        conn.execute("UPDATE pr_requests SET fx_rate=? WHERE id=?", (rate, pr_id))
        uname = (user or {}).get("username")
        audit(conn, pr_id, uname, "fx_set",
              f"FX rate set to {rate:g} ({pr['currency'] or 'EGP'} -> EGP)", ip)
        if pr["status"] == "pending":
            _reconcile_value_ladder(conn, pr_id, pr["department"], pr["total"])
            # The EGP figure just moved, so the budget verdict can have moved too.
            apply_budget_state(conn, pr_id, ip=ip)
            apply_single_source_state(conn, pr_id, ip=ip)
        conn.commit()
        return True, "fx_set"
    finally:
        conn.close()


def revise_po(pr_id, reason, user, ip=None):
    """Open a numbered revision on an issued Purchase Order: freeze the current
    order lines + total + PO number into pr_po_revisions (immutable history),
    bump po_rev, audit the reason and ring the bell. Only an issued or partially
    received order can be revised. Returns (True, new_rev) or (False, msg)."""
    reason = (reason or "").strip()
    if not reason:
        return False, "reason_required"
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("po_issued", "partially_received"):
            return False, "not_revisable"
        items = conn.execute(
            "SELECT id, item, qty, unit_price, est_cost FROM pr_items "
            "WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()
        snapshot = _json_dumps({
            "items": [dict(r) for r in items],
            "total": float(pr["total"] or 0),
            "po_no": pr["po_no"],
        })
        new_rev = int(pr["po_rev"] or 0) + 1
        uname = (user or {}).get("username")
        conn.execute(
            """INSERT INTO pr_po_revisions
               (pr_id, rev_no, reason, po_no, snapshot_json, created_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (pr_id, new_rev, reason, pr["po_no"], snapshot, uname, _now()))
        conn.execute("UPDATE pr_requests SET po_rev=? WHERE id=?", (new_rev, pr_id))
        audit(conn, pr_id, uname, "po_revised", f"PO revision {new_rev}: {reason}", ip)
        bell(conn, "info", "PO revised",
             f"{pr['po_no']} (ref {pr['pr_no']}) revised — Rev {new_rev}: {reason}")
        conn.commit()
        return True, new_rev
    finally:
        conn.close()


def _record_grn(conn, pr_id, pr, lines, user, notes=None, reject_reason=None):
    """Allocate and persist ONE Goods Received Note for ONE receipt event.

    `lines` = [{item_id, item, ordered, accepted, quarantined}] for the lines that
    moved on THIS delivery. The number comes from the row id, so two partial
    receipts on the same PR get two different numbers — the old behaviour derived
    it from the PR number at print time and reprinted GRN-YYYY-NNNNNN forever.
    Returns (grn_id, grn_no)."""
    seq = (conn.execute("SELECT COUNT(*) c FROM pr_grn WHERE pr_id=?",
                        (pr_id,)).fetchone()["c"] or 0) + 1
    uname = (user or {}).get("username") or "system"
    # A rejected quantity rides in lines_json, not in a rollup column: the GRN PDF
    # and the detail page both read the lines anyway, and the money side of a
    # rejection lives on the debit note, which is its own document.
    cur = conn.execute(
        """INSERT INTO pr_grn (pr_id, seq, lines_json, accepted_qty, quarantined_qty,
           notes, received_by, created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (pr_id, seq, _json_dumps(lines),
         round(sum(l["accepted"] for l in lines), 6),
         round(sum(l["quarantined"] for l in lines), 6),
         notes, uname, _now()))
    grn_id = cur.lastrowid
    grn_no = doc_no("GRN", grn_id)
    conn.execute("UPDATE pr_grn SET grn_no=? WHERE id=?", (grn_no, grn_id))

    excess = [l for l in lines if l["quarantined"] > 0]
    for l in excess:
        conn.execute(
            """INSERT INTO pr_grn_quarantine (pr_id, grn_id, item_id, item, ordered_qty,
               accepted_qty, qty, status, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,'quarantined',?,?)""",
            (pr_id, grn_id, l["item_id"], l["item"], l["ordered"], l["accepted"],
             l["quarantined"], uname, _now()))
    if excess:
        detail = ", ".join(f"{l['item']} +{l['quarantined']:g}" for l in excess)
        audit(conn, pr_id, uname, "over_delivery_quarantined",
              f"{grn_no}: over-delivery held in quarantine — {detail}")
        bell(conn, "warning", "Over-delivery quarantined",
             f"{grn_no} ({pr['pr_no']}): {detail}. Not added to stock — accept or "
             f"return it.", link=_pr_link(pr_id))
        notify_users(conn, [pr["requester"], uname],
                     "warning", "Over-delivery quarantined",
                     f"{pr['pr_no']}: the supplier delivered more than ordered "
                     f"({detail}). Held in quarantine on {grn_no}.", link=_pr_link(pr_id))

    # Rejected AND surplus: goods the order never covered, refused at the door and
    # sent straight back. Recorded here rather than on a debit note because there
    # is nothing to debit — the supplier was never owed for them — and recorded
    # 'resolved' because the decision was taken on the spot: they left with the
    # driver, so offering "accept as free issue" would post stock that is gone.
    surplus = [l for l in lines if float(l.get("rejected_surplus") or 0) > 0]
    for l in surplus:
        conn.execute(
            """INSERT INTO pr_grn_quarantine (pr_id, grn_id, item_id, item, ordered_qty,
               accepted_qty, qty, status, resolution, resolved_by, resolved_at,
               created_by, created_at)
               VALUES (?,?,?,?,?,?,?,'resolved','rejected_surplus',?,?,?,?)""",
            (pr_id, grn_id, l["item_id"], l["item"], l["ordered"], l["accepted"],
             l["rejected_surplus"], uname, _now(), uname, _now()))
    if surplus:
        detail = ", ".join(f"{l['item']} {float(l['rejected_surplus']):g}"
                           for l in surplus)
        audit(conn, pr_id, uname, "surplus_rejected",
              f"{grn_no}: rejected over-delivery returned with the driver — {detail}"
              + (f" ({reject_reason})" if reject_reason else "")
              + ". No debit note: the order did not cover it.")
        bell(conn, "warning", "Surplus delivery rejected",
             f"{grn_no} ({pr['pr_no']}): {detail} was above the ordered quantity and "
             f"was refused. Not stocked, not payable, no debit note.",
             link=_pr_link(pr_id))
    return grn_id, grn_no


def _record_return(conn, pr_id, pr, lines, reason, user, grn_id=None):
    """Return-to-vendor + debit note for the lines REJECTED on one receipt.

    `lines` = [{item_id, item, qty, unit_price}] — the rejected quantities only.
    One row is both documents because they are one event: the goods go back and
    the supplier is debited for them. The debit-note number is allocated from the
    row id, exactly like a GRN number, so every return carries a distinct one.

    ONE ROW PER SUPPLIER, split exactly the way _issue_po_rows splits the order:
    the vendor is resolved from the REJECTED LINES, never from the header. Taking
    it from the header debited the header's supplier for goods a different
    supplier shipped — real money off the wrong payable, and the wrong ceiling in
    three_way_match. A request with one supplier still writes exactly one row.
    Returns [(return_id, dn_no), ...] — the primary first."""
    uname = (user or {}).get("username") or "system"
    try:
        rate = float(pr["tax_rate"] or 0)
    except (KeyError, IndexError, TypeError, ValueError):
        rate = 0.0
    # Stripped, so the grouping key matches the one po_groups/_issue_po_rows use.
    head_vendor = (pr["vendor"] or "").strip() or None
    # Each rejected line belongs to the supplier its ORDER line names; a line
    # naming none belongs to the header vendor, which is every line of every
    # request raised before per-line vendors existed.
    groups = {}
    for l in lines:
        row = conn.execute("SELECT vendor FROM pr_items WHERE id=?",
                           (l.get("item_id"),)).fetchone()
        vendor = ((row["vendor"] if row else None) or head_vendor or "").strip() or None
        groups.setdefault(vendor, []).append(l)
    out = []
    for vendor, glines in groups.items():
        qty = round(sum(float(l["qty"]) for l in glines), 6)
        net = round(sum(float(l["qty"]) * float(l["unit_price"] or 0) for l in glines), 2)
        tax = round(net * rate / 100.0, 2)
        # The register id belongs to the HEADER's supplier and to no other. Every
        # reader (vendor_dues, the debit note, the payable ceiling) keys on the
        # NAME, so an unknown id is honest where a borrowed one is a lie.
        vendor_id = pr["vendor_id"] if vendor == head_vendor else None
        cur = conn.execute(
            """INSERT INTO pr_returns (pr_id, grn_id, vendor, vendor_id, lines_json, qty,
               net, tax, total, currency, reason, status, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,'open',?,?)""",
            (pr_id, grn_id, vendor, vendor_id, _json_dumps(glines), qty,
             net, tax, round(net + tax, 2), pr["currency"] or "EGP", reason, uname, _now()))
        ret_id = cur.lastrowid
        dn_no = doc_no("DN", ret_id)
        conn.execute("UPDATE pr_returns SET dn_no=? WHERE id=?", (dn_no, ret_id))
        detail = ", ".join(f"{l['item']} {float(l['qty']):g}" for l in glines)
        audit(conn, pr_id, uname, "goods_rejected",
              f"{dn_no}: returned to {vendor or 'the supplier'} — {detail}"
              + (f" ({reason})" if reason else ""))
        bell(conn, "warning", "Goods rejected — debit note raised",
             f"{dn_no} ({pr['pr_no']}): {detail} returned to "
             f"{vendor or 'the supplier'}. Payable value reduced by "
             f"{round(net + tax, 2):,.2f}.", link=_pr_link(pr_id))
        notify_users(conn, [pr["requester"], uname], "warning", "Goods rejected on receipt",
                     f"{pr['pr_no']}: {detail} rejected and returned to "
                     f"{vendor or 'the supplier'}. Debit note {dn_no}"
                     + (f" — {reason}" if reason else "") + ".", link=_pr_link(pr_id))
        out.append((ret_id, dn_no))
    return out


def get_return(ret_id):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM pr_returns WHERE id=?", (ret_id,)).fetchone()
        return dict(r) if r else None
    except Exception:
        return None          # pre-migration database: no returns table yet
    finally:
        conn.close()


def settle_return(ret_id, user, ip=None):
    """Mark a debit note settled — the supplier has credited it (or replaced the
    goods). Procurement's half of the story ends here: the dues drop off the
    vendor's outstanding balance and the payable ceiling rises back.

    Deliberately NOT a payment or a journal entry: what the credit was actually
    applied against is a finance-system question."""
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM pr_returns WHERE id=?", (ret_id,)).fetchone()
        if not r:
            return False, "not_found"
        if r["status"] != "open":
            return False, "already_settled"
        uname = (user or {}).get("username") or "system"
        conn.execute("UPDATE pr_returns SET status='settled', settled_by=?, "
                     "settled_at=? WHERE id=?", (uname, _now(), ret_id))
        audit(conn, r["pr_id"], uname, "debit_note_settled",
              f"{r['dn_no']} settled by the supplier ({float(r['total'] or 0):,.2f})", ip)
        conn.commit()
        return True, r["dn_no"]
    finally:
        conn.close()


def vendor_dues(conn=None):
    """Open debit-note value per vendor NAME — what each supplier owes back for
    goods returned. Keyed by name because that is how a PR names its supplier.

    Pass a live `conn` to borrow it: list_vendors does, so rendering the Vendors
    screen stays one connection rather than two round trips on PostgreSQL."""
    own = conn is None
    conn = conn or get_db()
    try:
        rows = conn.execute(
            "SELECT vendor, COUNT(*) n, SUM(total) t FROM pr_returns "
            "WHERE status='open' GROUP BY vendor").fetchall()
    except Exception:
        return {}                # database predating the returns table
    finally:
        if own:
            conn.close()
    return {r["vendor"]: {"count": r["n"], "amount": round(float(r["t"] or 0), 2)}
            for r in rows if r["vendor"]}


def resolve_quarantine(q_id, decision, user, ip=None):
    """Decide a quarantined over-delivery: 'return' it to the supplier or 'accept'
    it as a free issue.

    The two decisions EXECUTE differently, they are not one UPDATE with different
    text. 'accept' keeps the goods, so they must land on the shelf — at zero unit
    cost, since the PO is not re-opened and nobody is invoicing for them. Recording
    'accept' and posting nothing left the quantity physically in the building and in
    no stock record, permanently."""
    if decision not in ("return", "accept"):
        return False, "bad_decision"
    conn = get_db()
    try:
        q = conn.execute("SELECT * FROM pr_grn_quarantine WHERE id=?", (q_id,)).fetchone()
        if not q:
            return False, "not_found"
        if q["status"] != "quarantined":
            return False, "already_resolved"
        uname = (user or {}).get("username") or "system"
        conn.execute(
            "UPDATE pr_grn_quarantine SET status='resolved', resolution=?, "
            "resolved_by=?, resolved_at=? WHERE id=?", (decision, uname, _now(), q_id))
        audit(conn, q["pr_id"], uname, "quarantine_resolved",
              f"Over-delivery of {q['item']} ({q['qty']:g}) — {decision}"
              + (" (free issue, posted to stock at zero cost)" if decision == "accept" else ""),
              ip)
        conn.commit()
        pr_id, item_id, qty = q["pr_id"], q["item_id"], float(q["qty"] or 0)
    finally:
        conn.close()
    # Post-commit and best-effort, exactly like _post_bridge_receipt: a store that
    # will not take the free issue must not undo the decision.
    if decision == "accept" and qty > 0:
        _post_bridge_receipt(pr_id, {item_id: qty}, user, free=True)
    return True, decision


def receive_items(pr_id, receipts, user, notes=None, ip=None, post_stock=True,
                  rejects=None, reject_reason=None):
    """Record a line-level goods receipt. `receipts` = {item_id: qty_ACCEPTED_now},
    `rejects` = {item_id: qty_REJECTED_now}. Adds the accepted quantity to each
    line's received_qty (capped at ordered), then sets the PR status to 'received'
    (all lines fulfilled) or 'partially_received'.

    A REJECTED quantity is the other half of the same delivery and is deliberately
    the mirror image of an accepted one: it never touches received_qty, so it never
    counts as received, never reaches `effective` (the only thing that posts to
    stock), and leaves the line outstanding. It goes back to the supplier as a
    return with its own debit note (_record_return), and that debit note is what
    reduces the payable ceiling in three_way_match/add_payment — but only for the
    part of it the ORDER covers. A rejection beyond the ordered quantity is
    surplus the supplier sent uninvited: it is refused and quarantined, never
    debited, because there is nothing on this PO to debit it against.

    Anything delivered ABOVE the ordered quantity is NOT added to stock and NOT
    thrown away: it is written to pr_grn_quarantine with an explicit state and a
    notification. Every call allocates its own GRN number.

    post_stock=False when the CALLER books the stock itself (the warehouse door
    keeps roll/lot data the bridge cannot carry) — the PR bookkeeping, the GRN and
    the quarantine still happen here so both doors obey one rule."""
    rejects = rejects or {}
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        # 'received' belongs here: a supplier sending MORE after the order is
        # closed out is the commonest over-delivery there is, and refusing it left
        # the operator with no door at all — the goods reached the shelf through
        # /warehouse/receive with no GRN and no quarantine. Outstanding is 0, so
        # the arithmetic below yields accepted=0 / over=everything and the whole
        # excess routes into quarantine without touching stock.
        if pr["status"] not in ("po_issued", "approved", "partially_received", "received"):
            return False, "not_receivable"
        items = conn.execute("SELECT * FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        now = _now()
        any_recv = False
        # What actually entered the building, per line, AFTER capping at the
        # ordered quantity. This — not the raw form input — is what may be
        # posted onward into warehouse stock, otherwise typing "6" twice on a
        # 10-piece order books 10 but would inflate the spare stock by 12.
        effective = {}
        grn_lines = []
        reject_lines = []

        def _qty(src, iid):
            v = src.get(str(iid)) or src.get(iid) or 0
            try:
                return max(0.0, float(v))
            except (TypeError, ValueError):
                return 0.0

        for it in items:
            add = _qty(receipts, it["id"])
            rej = _qty(rejects, it["id"])
            if add <= 0 and rej <= 0:
                continue
            ordered = float(it["qty"] or 0)
            if ordered <= 0:
                continue    # never book receipts against a zero-quantity line
            already = float(it["received_qty"] or 0)
            new_total = min(ordered, already + add)
            accepted = round(max(0.0, new_total - already), 6)
            # The excess is what the truncation used to swallow. It is recorded,
            # never stocked: `effective` (what goes to the store) stays capped.
            over = round(max(0.0, (already + add) - ordered), 6)
            if accepted > 0:
                effective[it["id"]] = accepted
            # A rejection is split at the SAME ordered ceiling the acceptance is.
            # A debit note is money taken off THIS order, so only the part of the
            # rejection the order actually covers can be debited: 10 ordered, 13
            # delivered, 3 faulty means the supplier still delivered the 10 that
            # were bought and owes nothing back. Debiting the surplus withheld
            # payment for goods that WERE accepted, and made the same physical
            # delivery pay two different amounts depending on which box the clerk
            # typed in. The surplus goes where "they sent what we did not order"
            # already goes — quarantine, deliberately outside the payable sums.
            within = round(min(rej, max(0.0, ordered - already - accepted)), 6)
            surplus = round(rej - within, 6)
            conn.execute("UPDATE pr_items SET received_qty=? WHERE id=?", (new_total, it["id"]))
            grn_lines.append({"item_id": it["id"], "item": it["item"], "ordered": ordered,
                              "accepted": accepted, "quarantined": over, "rejected": rej,
                              "rejected_surplus": surplus})
            if within > 0:
                reject_lines.append({"item_id": it["id"], "item": it["item"], "qty": within,
                                     "unit": it["unit"], "unit_price": float(it["unit_price"] or 0)})
            any_recv = True
        if not any_recv:
            return False, "nothing_received"
        # recompute fulfilment
        items = conn.execute("SELECT qty, received_qty FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()
        fully = all(float(i["received_qty"] or 0) >= float(i["qty"] or 0) for i in items)
        new_status = "received" if fully else "partially_received"
        conn.execute(
            "UPDATE pr_requests SET status=?, received_at=?, received_by=?, "
            "receipt_notes=COALESCE(?, receipt_notes) WHERE id=?",
            (new_status, now if fully else pr["received_at"],
             user.get("username") if user else "system", notes, pr_id))
        grn_id, grn_no = _record_grn(conn, pr_id, pr, grn_lines, user, notes,
                                     reject_reason)
        if reject_lines:
            _record_return(conn, pr_id, pr, reject_lines, reject_reason, user, grn_id)
        audit(conn, pr_id, user.get("username") if user else "system", "goods_received",
              ("Fully received" if fully else "Partial receipt") + f" — {grn_no}"
              + (f": {notes}" if notes else ""), ip)
        if fully and effective:      # nothing accepted = a pure over-delivery, not a confirmation
            notify_users(conn, [pr["requester"]], "info", "Delivery confirmed",
                         f"{pr['pr_no']} fully received ({grn_no}).", link=_pr_link(pr_id))
        conn.commit()
        if post_stock:
            _post_bridge_receipt(pr_id, effective, user)
        return True, ("received" if fully else "partial")
    finally:
        conn.close()


def _post_bridge_receipt(pr_id, receipts, user, free=False):
    """Post a goods receipt back into whichever store owns the goods. Post-commit,
    best-effort: never blocks receiving.

    TWO independent stores, so TWO independent try blocks — nesting them would let a
    maintenance-side failure skip the material store entirely:
      * maintenance spare parts (spare auto-reorder PRs), and
      * the raw-material store (fabric rolls / trims). Without this second call every
        fabric and trim receipt booked in procurement was invisible to /warehouse —
        procurement said 'received' while material stock never moved.
    Each callee already swallows its own errors, so anything reaching us here is
    unexpected and is logged rather than silently dropped.

    free=True books the quantity at zero unit cost — an accepted over-delivery is a
    free issue: it goes on the shelf, it does not move the weighted-average cost."""
    try:
        from app.maintenance.procure_bridge import post_receipt_to_stock
        post_receipt_to_stock(pr_id, receipts, user, free=free)
    except Exception:
        log.warning("maintenance receipt bridge failed for PR %s", pr_id, exc_info=True)
    try:
        from app.warehouse.services import post_receipt_to_material
        post_receipt_to_material(pr_id, receipts, user, free=free)
    except Exception:
        log.warning("material receipt bridge failed for PR %s", pr_id, exc_info=True)


def _due_date(payment_condition, base_date):
    """Compute an invoice due date from the payment terms (Net 15/30/60)."""
    import re
    from datetime import datetime, timedelta
    m = re.search(r"net\s*(\d+)", (payment_condition or "").lower())
    if not m:
        return None
    try:
        d = datetime.strptime(str(base_date)[:10], "%Y-%m-%d")
        return (d + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")
    except Exception:
        return None


def add_invoice(pr_id, data, user, filename=None, content_type=None, content_b64=None, ip=None):
    """Record a vendor invoice, then (re)run the 3-way match for the PR.

    On a requisition SPLIT across suppliers the invoice must say WHICH order it
    bills (`data["po_id"]`), and it may not bill that order for more than the
    order is worth. Without that, one supplier's invoice could be accepted up to
    the combined value of every other supplier's order — the request-level
    ceiling bounds the three together, not one against its own. A single-supplier
    request has no order to choose and is bounded exactly as it always was."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        # invoices only make sense once the request is an actual order
        if pr["status"] not in ("approved", "po_issued", "partially_received",
                                "received", "closed"):
            return False, "not_invoicable"
        po, po_err = split_order(conn, pr, data.get("po_id"))
        if po_err:
            return False, po_err
        if po is not None:
            ex = po_exposure(conn, pr, po)
            gross = round(float(data.get("amount") or 0) + float(data.get("tax") or 0), 2)
            tol = C.match_tolerance_value(ex["ordered"], fx_rate=_pr_fx(pr)) \
                if C.DOAM_IN_FORCE else max(1.0, ex["ordered"] * 0.01)
            if ex["invoiced"] + gross > ex["ordered"] + tol:
                return False, "exceeds_po"
        inv_no = (data.get("invoice_no") or "").strip()
        if inv_no:
            # Dedup on (vendor, invoice_no) across ALL PRs — the same supplier invoice
            # number must not be booked (and paid) twice, even against a different PR.
            dup = conn.execute(
                "SELECT v.id FROM pr_invoices v JOIN pr_requests p ON p.id=v.pr_id "
                "WHERE v.invoice_no=? AND COALESCE(TRIM(p.vendor),'')=COALESCE(TRIM(?),'')",
                (inv_no, pr["vendor"])).fetchone()
            if dup:
                return False, "duplicate_invoice"
        now = _now()
        conn.execute(
            """INSERT INTO pr_invoices (pr_id, po_id, invoice_no, invoice_date, amount, tax,
               currency, status, filename, content_type, content_b64, notes, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (pr_id, (po or {}).get("id"),
             data.get("invoice_no"), data.get("invoice_date") or now[:10],
             float(data.get("amount") or 0), float(data.get("tax") or 0),
             data.get("currency") or pr["currency"] or "EGP", "received",
             filename, content_type, content_b64, data.get("notes"),
             user.get("username") if user else "system", now))
        # set a due date from the payment terms if not already set
        due = _due_date(pr["payment_condition"], data.get("invoice_date") or now[:10])
        if due and not pr["due_date"]:
            conn.execute("UPDATE pr_requests SET due_date=? WHERE id=?", (due, pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "invoice_added",
              f"Invoice {data.get('invoice_no')} @ {data.get('amount')}", ip)
        conn.commit()
    finally:
        conn.close()
    three_way_match(pr_id)   # refresh cached match
    return True, ""


def three_way_match(pr_id):
    """Compare ORDERED (PO) vs RECEIVED (GRN) vs INVOICED. Returns a dict with
    per-check verdicts and flags, and caches it on the latest invoice."""
    bundle = get_pr(pr_id)
    if not bundle:
        return None
    pr, items = bundle["pr"], bundle["items"]
    amt = pr_amounts(pr)
    ordered_grand = amt["grand"]
    ordered_qty = sum(float(i["qty"] or 0) for i in items)
    received_qty = sum(float(i["received_qty"] or 0) for i in items)
    received_value = round(sum(float(i["received_qty"] or 0) * float(i["unit_price"] or 0)
                               for i in items), 2)
    invoiced = round(sum(float(iv["amount"] or 0) + float(iv["tax"] or 0)
                         for iv in bundle["invoices"]), 2)          # gross (with tax)
    invoiced_net = round(sum(float(iv["amount"] or 0) for iv in bundle["invoices"]), 2)  # pre-tax

    # DOAM §7.3.3: "within tolerance of 2% of value or 500 EGP ... whichever is
    # greater". The floor is the binding half on a small invoice — on 1,000 EGP,
    # 2% is 20 and the DOAM allows 500. The old flat 1% with a 1-unit floor was
    # therefore both too tight on small invoices and too loose on large ones.
    # MATCH_TOLERANCE_ABS is 500 EGP, but every figure here is in the PR's own
    # currency, so applying it raw gave a USD order a 500 USD tolerance — on a
    # 1,000 USD PO at fx 50 that is 25,000 EGP, fifty times the rule, and a
    # 1,450 USD invoice on a 1,000 USD PO came back "matched" with no flags.
    # Convert the EGP floor into the PR's currency instead of converting every
    # amount: one division, and the percentage half needs no conversion because
    # a ratio is currency-agnostic.
    _fx = _pr_fx(pr)
    tol = C.match_tolerance_value(ordered_grand, fx_rate=_fx) if C.DOAM_IN_FORCE \
        else max(1.0, ordered_grand * 0.01)
    # ...and the QUANTITY half of the same clause ("or 5% of quantity"), which
    # until now was a constant with no reader at all: the match compared value
    # only, so 4 units short of 100 and 40 units short of 100 were both simply
    # "short", and neither had a number the payment side could act on.
    qty_pct = C.MATCH_QTY_TOLERANCE_PCT if C.DOAM_IN_FORCE else 0.0
    qty_tol = ordered_qty * qty_pct / 100.0    # display only — see qty_ok below
    flags = []
    # The shortfall is stated in BOTH units and money: the units are what the
    # tolerance is measured in, the money is what add_payment caps against.
    short_qty = round(max(0.0, ordered_qty - received_qty), 6)
    received_grand = round(received_value * (1 + amt["tax_rate"] / 100.0), 2)
    short_value = round(max(0.0, ordered_grand - received_grand), 2)
    # Goods rejected on receipt and sent back: an open debit note is value the
    # supplier owes us, so it comes off what this PO can still be paid.
    #
    # min(), NOT a subtraction from received_grand: a rejection on receipt shows
    # up TWICE — the quantity is missing from received_qty AND it carries a debit
    # note — and they are two views of the same units, so subtracting both would
    # deduct the rejection twice. The ceiling is the tighter of "what actually
    # arrived and stayed" and "the order less what the supplier owes back"; a
    # return raised AFTER a line was fully received is caught by the second half.
    debit_open = round(sum(float(r.get("total") or 0) for r in (bundle.get("returns") or [])
                           if (r.get("status") or "open") == "open"), 2)
    payable = round(max(0.0, min(received_grand, ordered_grand - debit_open)), 2)
    # PER LINE, never on the sum of quantities. "5% of quantity" is a per-line
    # notion, and summing across lines makes it meaningless: 1,000 metres of
    # thread arriving in full swamps two 5,000 EGP machines that never did, and
    # the order reads "matched" with 10/11ths of its value missing.
    qty_ok = all(float(i["received_qty"] or 0)
                 >= float(i["qty"] or 0) * (1 - qty_pct / 100.0) - 1e-9
                 for i in items)
    if short_qty > 0:
        flags.append(
            f"Short delivery: received {received_qty:g} of {ordered_qty:g} ordered "
            f"- short {short_qty:g} ({short_value:,.2f}); "
            + (f"every line within the {qty_pct:g}% quantity tolerance"
               if qty_ok else
               f"a line is beyond the {qty_pct:g}% quantity tolerance")
            # `payable`, not received_grand: an open debit note lowers the
            # ceiling below the received value, and this is the number
            # add_payment actually enforces (plus the payment tolerance).
            + f"; payment is capped at {payable:,.2f}")
    price_ok = invoiced <= ordered_grand + tol
    if invoiced > ordered_grand + tol:
        flags.append(f"Over-billing: invoiced {invoiced:,.2f} vs PO {ordered_grand:,.2f}")
    # compare like-for-like: invoice net (pre-tax) vs received goods value (pre-tax)
    receipt_inv_ok = (not bundle["invoices"]) or invoiced_net <= received_value + tol
    if bundle["invoices"] and invoiced_net > received_value + tol:
        flags.append(f"Invoiced more than received ({invoiced_net:,.2f} vs received value {received_value:,.2f})")

    if debit_open > 0:
        flags.append(
            f"Goods returned to the supplier: open debit notes {debit_open:,.2f}; "
            f"payment is capped at {payable:,.2f}")
    # A control that could not evaluate itself must say so rather than pass. The
    # returns register is the only input to the payable ceiling, so if get_pr
    # could not read it, debit_open above is 0 by ignorance, not by fact.
    returns_ok = bundle.get("returns_ok", True)
    if not returns_ok:
        flags.append("The return-to-vendor register could not be read: the payable "
                     "ceiling is NOT evaluated on this order.")
    matched = (bool(bundle["invoices"]) and qty_ok and price_ok and receipt_inv_ok
               and returns_ok)
    result = {
        "ordered_grand": ordered_grand, "ordered_qty": ordered_qty,
        "received_qty": received_qty, "received_value": received_value,
        "received_grand": received_grand, "short_qty": short_qty,
        "debit_open": debit_open, "payable": payable, "returns_ok": returns_ok,
        "short_value": short_value, "qty_tol": qty_tol, "tol": tol,
        "invoiced": invoiced, "qty_ok": qty_ok, "price_ok": price_ok,
        "receipt_inv_ok": receipt_inv_ok, "flags": flags,
        "status": "matched" if matched else ("mismatch" if bundle["invoices"] else "pending"),
        "has_invoice": bool(bundle["invoices"]),
    }
    # cache on the most recent invoice + mark matched/disputed
    if bundle["invoices"]:
        conn = get_db()
        try:
            last = bundle["invoices"][-1]["id"]
            conn.execute("UPDATE pr_invoices SET match_json=?, status=? WHERE id=?",
                         (_json_dumps(result), "matched" if matched else "disputed", last))
            conn.commit()
        finally:
            conn.close()
    return result


def _json_dumps(d):
    import json
    try:
        return json.dumps(d)
    except Exception:
        return "{}"


def get_invoice(inv_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM pr_invoices WHERE id=?", (inv_id,)).fetchone()
    finally:
        conn.close()


def list_sales_orders(limit=300):
    """Live sales-order numbers for the PR form's picker (DOAM §5). Reads the
    orders module directly; returns [] if it is not installed, because a missing
    picker must not stop anyone raising a request."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT DISTINCT order_no FROM ord_orders WHERE order_no IS NOT NULL "
            "AND order_no<>'' AND status NOT IN (%s) ORDER BY id DESC LIMIT ?"
            % ",".join("?" * len(C.SO_CLOSED_STATUSES)),
            tuple(C.SO_CLOSED_STATUSES) + (limit,)).fetchall()
        return [r["order_no"] for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def sales_order_state(so_no):
    """Is `so_no` a real, open sales order? "open" / "closed" / "unknown", or
    None when there is nothing to check it against.

    The PR form's sales-order field is a free-text datalist: the picker offers
    real orders, but "x", "-" or "n/a" typed over it used to satisfy the DOAM's
    "valid client sales order reference" just as well. It is validated here.

    None (skip the check) covers exactly ONE case: the orders module is not
    installed, so the query raises. An EMPTY register is not that case — it is
    the state of a freshly deployed production database, which is precisely
    when the golden thread most needs protecting, and since the forecast
    register landed there IS something a requester can cite instead. With
    neither an order nor a forecast on file, a direct-material purchase has no
    cost object and is refused.

    Reads on its OWN connection, never the caller's: submit_pr is mid
    transaction when this runs, and on PostgreSQL a failed statement aborts the
    whole transaction — the bare except would then hide the cause and every
    later write in submit_pr would fail with "transaction is aborted".
    """
    ref = str(so_no or "").strip().lower()
    if not ref:
        return None
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT status FROM ord_orders WHERE LOWER(TRIM(order_no))=?",
            (ref,)).fetchall()
    except Exception:
        return None
    finally:
        conn.close()
    if not rows:
        return "unknown"
    closed = set(C.SO_CLOSED_STATUSES)
    # Any live row wins: the same order number can legitimately appear more than
    # once (split shipments), and one open line is an open order.
    return "open" if any(str(r["status"] or "").strip().lower() not in closed
                         for r in rows) else "closed"


# --------------------------------------------------------------------------
# DOAM §3.4 — the OTHER half of the sales-order gate: "...OR AGREED FORECAST".
# A forecast is a REGISTER entry, not a free-text box. It satisfies the gate
# only while it is BOTH agreed (signed off by the DOAM planning authority) and
# inside its validity dates — an expired or still-draft forecast is no cost
# object at all, which is the whole point of the word "agreed".
# --------------------------------------------------------------------------
FORECAST_DEFAULT_DAYS = 90       # one production quarter — see agree_forecast()


def forecast_state(ref):
    """Is `ref` an agreed production forecast that is in force today?
    Returns "active" | "expired" | "unapproved" | "unknown".

    Unlike sales_order_state() there is no "skip the check" answer: the register
    lives in THIS module, so if the query fails there is no register, and with no
    register there is no reference a requester could legitimately give.

    Own connection, for the same reason as sales_order_state()."""
    r = str(ref or "").strip().lower()
    if not r:
        return "unknown"
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT status, valid_from, valid_to FROM proc_forecasts "
            "WHERE LOWER(TRIM(ref))=?", (r,)).fetchone()
    except Exception:
        return "unknown"
    finally:
        conn.close()
    if not row:
        return "unknown"
    if str(row["status"] or "").strip().lower() != "agreed":
        return "unapproved"
    today = _now()[:10]
    if not (str(row["valid_from"] or "0000-01-01") <= today
            <= str(row["valid_to"] or "9999-12-31")):
        return "expired"
    return "active"


def list_forecasts(active_only=False):
    """The forecast register. `active_only` returns just the refs that would
    satisfy the gate today — that is what the request form's picker offers."""
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM proc_forecasts ORDER BY id DESC").fetchall()]
    except Exception:
        return []
    finally:
        conn.close()
    today = _now()[:10]
    for f in rows:
        f["active"] = (str(f.get("status") or "").lower() == "agreed"
                       and str(f.get("valid_from") or "0000-01-01") <= today
                       and str(f.get("valid_to") or "9999-12-31") >= today)
    return [f for f in rows if f["active"]] if active_only else rows


def create_forecast(data, user):
    """Record a forecast as a DRAFT. Anyone who may raise a request may draft
    one; it buys nothing until it is agreed. Returns (ok, ref_or_error)."""
    # Stored UPPER because every lookup is LOWER(TRIM(ref)): without it the
    # UNIQUE constraint (case-sensitive) would accept 'FC-Q1' and 'fc-q1' as two
    # rows and a citation would resolve to whichever the scan reached first —
    # the document could then say "agreed" while the register says "draft".
    ref = str(data.get("ref") or "").strip().upper()[:80]
    if not ref:
        return False, "ref_required"
    # The dates decide when this stops being a cost object, so they are parsed,
    # not trusted: the form posts <input type="date">, a hand-rolled POST can
    # post anything, and a date the gate cannot compare is a hole.
    vf = str(data.get("valid_from") or "").strip() or _now()[:10]
    vt = str(data.get("valid_to") or "").strip()
    try:
        start = datetime.strptime(vf, "%Y-%m-%d")
        # Written back NORMALISED, never as posted: strptime accepts '2026-8-1'
        # but the gate compares these as TEXT, and '2026-8-1' sorts above
        # '2026-08-18' — an expired window would read "active".
        vf = start.strftime("%Y-%m-%d")
        # Default validity: FORECAST_DEFAULT_DAYS from the start date. A forecast
        # with no end date would never expire, and "agreed forever" is not agreed.
        vt = vt or (start + timedelta(days=FORECAST_DEFAULT_DAYS)).strftime("%Y-%m-%d")
        end = datetime.strptime(vt, "%Y-%m-%d")
        if end < start:
            return False, "bad_dates"
        vt = end.strftime("%Y-%m-%d")
    except ValueError:
        return False, "bad_dates"
    conn = get_db()
    try:
        # Checked explicitly so a real write failure is not mislabelled as a
        # duplicate. LOWER(TRIM(...)) = the lookup the gate uses.
        if conn.execute("SELECT 1 FROM proc_forecasts WHERE LOWER(TRIM(ref))=?",
                        (ref.lower(),)).fetchone():
            return False, "duplicate_ref"
        conn.execute(
            "INSERT INTO proc_forecasts (ref, description, period, valid_from, "
            "valid_to, status, created_by, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (ref, str(data.get("description") or "").strip()[:500],
             str(data.get("period") or "").strip()[:80], vf, vt, "draft",
             (user or {}).get("username"), _now()))
        conn.commit()
        return True, ref
    except Exception:
        conn.rollback()
        return False, "write_failed"
    finally:
        conn.close()


def agree_forecast(fc_id, user):
    """Sign a forecast off so it can carry direct-material spend.

    WHO: the DOAM planning authority. Table 4 L2 — "SC-D owns operational and
    inventory replenishment" — so this is exactly the authority that signs the
    'scd' rung of the approval ladder, and it is checked with the SAME can_act()
    the ladder uses rather than a new permission: supply_chain_director, plus
    the standing super_admin / proc_admin override that can_act() already grants.

    Separation of duties: the drafter may not be the agreer. Anyone who may
    raise a request may draft a forecast, and can_act("scd") carries the
    standing super_admin / proc_admin override — without this guard one holder
    of proc_admin could draft a forecast, agree it themselves and then raise
    unlimited direct-material spend against it with no client order and no
    second signature. Same shape as the SoD escalation in submit_pr.
    Returns (ok, msg)."""
    if not can_act(user, "scd"):
        return False, "not_authorised"
    conn = get_db()
    try:
        row = conn.execute("SELECT ref, status, created_by, valid_to FROM "
                           "proc_forecasts WHERE id=?", (fc_id,)).fetchone()
        if not row:
            return False, "not_found"
        if str(row["status"] or "").lower() == "agreed":
            return False, "already_agreed"
        # Signing a window that has already closed produces a row marked
        # 'agreed' that can never satisfy the gate — a signature meaning nothing.
        if str(row["valid_to"] or "9999-12-31") < _now()[:10]:
            return False, "fc_expired"
        if str(row["created_by"] or "") == (user or {}).get("username"):
            return False, "own_forecast"
        conn.execute(
            "UPDATE proc_forecasts SET status='agreed', agreed_by=?, "
            "agreed_by_name=?, agreed_role=?, agreed_at=? WHERE id=?",
            ((user or {}).get("username"),
             (user or {}).get("full_name") or (user or {}).get("username"),
             (user or {}).get("role"), _now(), fc_id))
        conn.commit()
        return True, row["ref"]
    finally:
        conn.close()


def cost_object_check(conn, pr_id, pr):
    """DOAM §5 / Table 12 — does this request carry the cost object it must?
    Returns (ok, reason) where `reason` is the refusal code — "so_unknown",
    "so_closed", "fc_unknown", "fc_expired", "fc_unapproved" or
    "cost_object_required" — and None when the request is fine.

    DOAM §3.4 allows EITHER a valid client sales order OR an agreed forecast.
    BOTH cited references are validated, whichever else is present: either one
    is persisted, shown on the request and printed on the PO, the GRN and the
    debit note, so junk in one field is refused even when the other is fine.
    The sales order stays decisive when both are valid. Cite one or the other."""
    fc = str(_pr_field(pr, "forecast_ref") or "").strip()
    if fc:
        fc_state = forecast_state(fc)
        if fc_state != "active":
            return False, "fc_" + fc_state
    so = str(_pr_field(pr, "so_no") or "").strip()
    if so:
        state = sales_order_state(so)
        if state in ("open", None):
            return True, None
        return False, "so_closed" if state == "closed" else "so_unknown"
    if fc:
        return True, None        # already checked above
    texts = [_pr_field(pr, "title"), _pr_field(pr, "request_for")]
    rows = conn.execute(
        "SELECT i.item, i.description, i.spare_id, c.category_name FROM pr_items i "
        "LEFT JOIN proc_items c ON c.id = i.item_id WHERE i.pr_id=?",
        (pr_id,)).fetchall()
    # Master data beats free text. Every line linked to a MAINTENANCE SPARE is
    # MRO under Table 12 (asset / cost centre), whatever the wording says — and
    # sewing-machine spares are called things like "thread guide" and "elastic
    # feeder". Without this the maintenance auto-reorder would raise a request
    # and then be told to find a sales order it must not carry.
    #
    # But spare_id is a HIDDEN FORM FIELD the requester posts, with no foreign
    # key behind it, so "linked to a spare" has to mean a row that really exists
    # in the spares master — otherwise spare_id[]=999999 is a client-side off
    # switch for the whole gate. And the free text must not ride along with the
    # link: the type-ahead only clears spare_id when item[] is retyped, so
    # picking any spare and putting "cotton twill for style 4471" in
    # description[] turned the exemption on from the browser with no devtools.
    #
    # ponytail: only when EVERY line resolves to a real spare; a mixed request is
    # judged on its text, which is the safe way round.
    real = _real_spares([r["spare_id"] for r in rows])
    if rows and all(r["spare_id"] in real for r in rows):
        # Judge an all-spare request on the requester's OWN words, with the
        # master-data names struck out of them. "Thread guide bracket" is the
        # spare's name, so the bridge's own title and line survive; "Cotton
        # twill fabric for style 4471" typed into item[] or description[] beside
        # a picked spare does not. This also closes the title, which an earlier
        # version of this branch never read at all.
        free = [_pr_field(pr, "title"), _pr_field(pr, "request_for")]
        for r in rows:
            free += [r["item"], r["description"]]
        blob = " ".join(str(t or "") for t in free).lower()
        # Longest first, so striking "thread guide bracket" is not pre-empted by
        # a shorter master string that overlaps it.
        for master in sorted((str(t or "").strip().lower()
                              for texts in real.values() for t in texts),
                             key=len, reverse=True):
            if master:
                blob = blob.replace(master, " ")
        if not C.cost_object_required([blob]):
            return True, None
    for r in rows:
        texts += [r["item"], r["description"], r["category_name"]]
    # An EMPTY order register still refuses, deliberately — see tests_so_gate.
    # I nearly relaxed this on the theory that a register with nothing in it
    # cannot validate anything and so blocks all purchasing for no benefit. That
    # reasoning is out of date: since the forecast register landed, a requester
    # with no sales order has a legitimate alternative to cite, so an empty
    # register is not a dead end and the refusal is not a trap. The only escape
    # is the orders module being ABSENT altogether, which sales_order_state
    # already handles by returning None.
    need = C.cost_object_required(texts)
    return (need is None), ("cost_object_required" if need else None)


def _pr_fx(pr):
    """EGP per unit of the PR's currency, as a positive float.

    One place, because getting it wrong is silent and one-directional: every
    currency in CURRENCIES trades above the pound, so a missing conversion always
    makes a foreign figure look SMALLER than it is — under-collecting approvals
    and over-granting tolerance, never the reverse. An EGP request, a missing
    rate and a nonsense rate all return 1.0, which is the identity and therefore
    the safe default."""
    if pr is None:
        return 1.0
    def _g(key):
        try:
            return pr.get(key) if isinstance(pr, dict) else pr[key]
        except (KeyError, IndexError):
            return None
    if str(_g("currency") or "EGP").strip().upper() == "EGP":
        return 1.0
    try:
        fx = float(_g("fx_rate") or 1.0)
    except (TypeError, ValueError):
        return 1.0
    return fx if fx > 0 else 1.0


def deviation_findings(conn, pr_id):
    """DOAM §4.4 — grade every line of a PR against its target price, its
    maximum stock ceiling and its NET REQUIREMENT. Returns
    {"lines": [...], "stages": [...], "memo": bool, "quotes": bool,
     "unassessable": int, "coverage_blind": int} where `stages` are the EXTRA
    approvals the deviations require, de-duplicated across lines.

    Three facts per line, all read from the masters the buyer cannot edit: the
    target price (proc_items.cost_price, the ERP cost), the ceiling
    (mnt_spare_parts.max_level) and the coverage (stock on hand + quantity
    already on order). A line with none of them is reported, not graded — the
    control must not pretend to have checked something it could not see.

    DOAM §3.4 / Table 11 (T&C-PUF-10 "Coverage Check") — "procurement quantity
    is capped at the net requirement after inventory netting". Per line:

        nettable  = max(0, on_hand - reserved - reorder_level) + on_order
        net_need  = max(0, requested - nettable)
        excess    = requested - net_need          -> "a quantity above plan"

    Three deliberate choices in that formula, each of which changes the answer:

      * pr_items.current_stock is NOT the source. It is a snapshot the requester
        types and can edit, and a control whose input the controlled party owns
        is not a control. mnt_spare_parts is.
      * pr_items.spare_id is not trusted to be PRESENT either. It is a hidden
        field the requester's own form posts, set only when the type-ahead is
        used, so skipping the type-ahead would buy the free pass of being
        "unassessable" — the same objection, one keystroke away. A line with no
        link is therefore matched to the spares master by EXACT code or name,
        and only when exactly one active spare matches; the panel says the line
        was matched by name rather than netted from a link.
      * RESERVED stock and the REORDER LEVEL are not free to net against.
        Reserved quantity is already committed to a work order, and the reorder
        level is the buffer the stocking policy says to hold — netting against
        either would flag every legitimate replenishment. The maintenance
        auto-reorder raises its PR precisely when available <= reorder_level, so
        without this term the control would escalate 100% of MRO replenishment
        and jam the lane it is supposed to police.
      * A line with no stock record is UNASSESSABLE, never zero-stock and never
        fully covered. Assuming zero stock would rubber-stamp every free-text
        line; assuming coverage would block them all."""
    rows = conn.execute(
        "SELECT i.id, i.item, i.description, i.qty, i.unit_price, i.item_id, "
        "i.spare_id FROM pr_items i WHERE i.pr_id=? ORDER BY i.seq",
        (pr_id,)).fetchall()
    if not rows:
        return {"lines": [], "stages": [], "memo": False, "quotes": False,
                "unassessable": 0, "coverage_blind": 0}
    # The item-master targets below are EGP; pr_items.unit_price is in the PR's
    # own currency. Read the rate once here so the comparison is like for like.
    _fx_rate = _pr_fx(conn.execute(
        "SELECT currency, fx_rate FROM pr_requests WHERE id=?", (pr_id,)).fetchone())
    cat = {}
    iids = [r["item_id"] for r in rows if r["item_id"]]
    if iids:
        cat = {r["id"]: r for r in conn.execute(
            "SELECT id, cost_price, has_cost FROM proc_items WHERE id IN (%s)"
            % ",".join("?" * len(iids)), tuple(iids)).fetchall()}
    spares = {}
    sids = [r["spare_id"] for r in rows if r["spare_id"]]
    if sids:
        # mnt_spare_parts, not "mnt_spares", and the cost column is avg_cost.
        # The wrong names inside a bare `except Exception: spares = {}` meant the
        # quantity-over-ceiling half of §4.4 silently never ran — no error, no
        # log, just a control that always found nothing. The except is narrowed
        # so a genuinely absent maintenance module still degrades to price-only
        # grading, but a query mistake is logged instead of swallowed.
        try:
            spares = {r["id"]: r for r in conn.execute(
                "SELECT id, stock_qty, reserved_qty, reorder_level, max_level, "
                "avg_cost FROM mnt_spare_parts WHERE id IN (%s)"
                % ",".join("?" * len(sids)), tuple(sids)).fetchall()}
        except Exception as exc:
            spares = {}
            logging.getLogger(__name__).warning(
                "DOAM 4.4: spare-part ceilings unavailable, grading on price only (%s)", exc)

    # Lines that carry no spare_id: match them to the master by exact code or
    # name. Omitting the hidden field is completely unguarded otherwise, and
    # "not assessable" must never be cheaper than "assessed". Exact match only,
    # and only when ONE active spare matches — an ambiguous name stays
    # unassessed rather than netted against the wrong part.
    line_sid = {r["id"]: r["spare_id"] for r in rows}
    named_lines = set()
    free = {}
    for r in rows:
        if r["spare_id"]:
            continue
        for t in (r["item"], r["description"]):
            t = str(t or "").strip().lower()
            if t:
                free.setdefault(t, []).append(r["id"])
    if free:
        keys = list(free)
        ph = ",".join("?" * len(keys))
        hits = {}
        try:
            for s in conn.execute(
                    "SELECT id, code, name, stock_qty, reserved_qty, reorder_level, "
                    "max_level, avg_cost FROM mnt_spare_parts WHERE is_active=1 "
                    "AND (LOWER(TRIM(code)) IN (%s) OR LOWER(TRIM(name)) IN (%s))"
                    % (ph, ph), tuple(keys) * 2).fetchall():
                for t in (str(s["code"] or "").strip().lower(),
                          str(s["name"] or "").strip().lower()):
                    if t in free:
                        hits.setdefault(t, []).append(s)
        except Exception as exc:
            hits = {}
            logging.getLogger(__name__).warning(
                "DOAM 3.4: name matching against the spares master unavailable (%s)", exc)
        for r in rows:
            if r["spare_id"]:
                continue
            for t in (r["item"], r["description"]):
                cands = hits.get(str(t or "").strip().lower()) or []
                if len(cands) == 1:
                    spares[cands[0]["id"]] = cands[0]
                    line_sid[r["id"]] = cands[0]["id"]
                    named_lines.add(r["id"])
                    break

    # Quantity already on order and not yet received, per spare. An issued PO is
    # a commitment that has not landed yet, so it covers the requirement just as
    # shelf stock does — this is the half of netting that catches the duplicate
    # order raised while the first one is still in transit. A PR that is still
    # in the ladder counts too: it is a planned order (an MRP scheduled receipt),
    # and it is the commonest duplicate of all — the definition matches
    # procure_bridge's "open PR", which already dedups the auto-reorder on it.
    # Only requests raised BEFORE this one are netted against it, so two
    # simultaneous requests do not each declare the other a duplicate; the later
    # one is. The current request is excluded so a re-price never nets a line
    # against itself.
    on_order = {}
    sids_all = sorted({v for v in line_sid.values() if v})
    if sids_all and spares:
        try:
            for r in conn.execute(
                    "SELECT i.spare_id AS sid, "
                    "SUM(COALESCE(i.qty,0) - COALESCE(i.received_qty,0)) AS q "
                    "FROM pr_items i JOIN pr_requests p ON p.id = i.pr_id "
                    "WHERE i.spare_id IN (%s) AND i.pr_id <> ? AND p.is_active=1 "
                    "AND (p.status IN ('po_issued','partially_received') "
                    "     OR (p.status IN ('pending','approved') AND i.pr_id < ?)) "
                    "GROUP BY i.spare_id" % ",".join("?" * len(sids_all)),
                    tuple(sids_all) + (pr_id, pr_id)).fetchall():
                on_order[r["sid"]] = max(float(r["q"] or 0), 0.0)
        except Exception as exc:
            on_order = {}
            logging.getLogger(__name__).warning(
                "DOAM 3.4: open-order netting unavailable (%s)", exc)

    lines, stages, memo, quotes, blind, cov_blind = [], [], False, False, 0, 0
    for r in rows:
        c = cat.get(r["item_id"])
        s = spares.get(line_sid.get(r["id"]))
        target = None
        if c is not None and c["has_cost"]:
            target = float(c["cost_price"] or 0)
        elif s is not None and (s["avg_cost"] or 0) > 0:
            target = float(s["avg_cost"])
        ceiling = float(s["max_level"]) if s is not None and (s["max_level"] or 0) > 0 else None
        over_ceiling = bool(
            ceiling is not None
            and float(s["stock_qty"] or 0) + float(r["qty"] or 0) > ceiling)
        qty = float(r["qty"] or 0)
        cov = None
        if s is None:
            cov_blind += 1                      # no stock record: not nettable
        else:
            oo = on_order.get(line_sid.get(r["id"]), 0.0)
            nettable = max(float(s["stock_qty"] or 0)
                           - float(s["reserved_qty"] or 0)
                           - float(s["reorder_level"] or 0), 0.0) + oo
            need = max(qty - nettable, 0.0)
            cov = {"qty": qty, "on_hand": float(s["stock_qty"] or 0),
                   "reserved": float(s["reserved_qty"] or 0), "on_order": oo,
                   "net_need": round(need, 3), "excess": round(qty - need, 3),
                   "by_name": r["id"] in named_lines}
        over_plan = bool(cov and cov["excess"] > 1e-9)
        # The target is an EGP figure off the item master; the unit price is in
        # the PR's currency. Comparing them raw made the grader FX-blind and it
        # failed in one direction only — every currency here has fx > 1, so a
        # foreign price always looked smaller than it is and never graded over
        # target. Proved: 1,200 EGP x 200 grades price_over_15 and pulls in the
        # Managing Director, while USD 24 at fx 50 — the identical 240,000 EGP —
        # graded on_plan and added nothing.
        pct = C.price_deviation_pct(float(r["unit_price"] or 0) * _fx_rate, target)
        g = C.deviation_grade(pct, over_ceiling, over_plan)
        if target is None and ceiling is None:
            blind += 1
        lines.append({"item": r["item"], "qty": qty,
                      "unit_price": float(r["unit_price"] or 0), "target": target,
                      "pct_over": round(pct, 1) if pct is not None else None,
                      "ceiling": ceiling, "over_ceiling": over_ceiling,
                      "coverage": cov, "over_plan": over_plan,
                      "spare_id": line_sid.get(r["id"]),
                      "grade": g["grade"]})
        for st in g["stages"]:
            if st not in stages:
                stages.append(st)
        memo = memo or g["memo"]
        quotes = quotes or g["quotes"]
    # Keep ladder order, not discovery order: an extra rung must still be
    # collected after the ones below it.
    stages = [s for s in C.DOAM_LADDER if s in stages]
    return {"lines": lines, "stages": stages, "memo": memo, "quotes": quotes,
            "unassessable": blind, "coverage_blind": cov_blind}


def _master_moved_by_requester(conn, pr_id, pr, dev):
    """§3.4 nets against the spares master, and on this platform the storekeeper
    who raises a maintenance request is the same person who may adjust the
    stock on that part (`maint_store` and `proc_create` sit together in the
    seeded storekeeper role). So the input to the cap is movable by the party
    the cap controls — the same objection §4.4 raises against
    pr_items.current_stock, one table over.

    The formula stays as it is; the FACT goes on the request, where the signers
    read it: a manual stock adjustment made by this PR's own requester, on a
    part this PR is buying, inside the last 30 days. Written whether or not the
    cap fired — the interesting case is precisely the one where it did not.

    Ceiling: only stock movements are attributable. `reorder_level` has no edit
    route at all (it is set when the part is created) and mnt_spare_parts
    records no creator, so a part CREATED with a generous buffer by the
    requester cannot be named here without a created_by column in the
    maintenance schema — a change in that module, not this one."""
    sids = sorted({l["spare_id"] for l in dev["lines"] if l.get("spare_id")})
    who = _pr_field(pr, "requester")
    if not (sids and who):
        return
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        # 'adjustment' only: receipts and issues are documented by the flow that
        # made them, while a manual adjustment is the discretionary write. Flag
        # every maintenance movement and the note fires on every request, which
        # is the same as not firing at all.
        rows = conn.execute(
            "SELECT s.code, m.before_qty, m.after_qty, m.created_at "
            "FROM mnt_stock_movements m JOIN mnt_spare_parts s ON s.id = m.spare_id "
            "WHERE m.spare_id IN (%s) AND m.type='adjustment' AND m.performed_by=? "
            "AND m.created_at >= ? ORDER BY m.created_at"
            % ",".join("?" * len(sids)), tuple(sids) + (who, since)).fetchall()
    except Exception as exc:                            # noqa: BLE001
        logging.getLogger(__name__).warning(
            "DOAM 3.4: spare movement history unavailable, self-edit not checked (%s)", exc)
        return
    if not rows:
        return
    detail = ("DOAM §3.4 segregation — the requester (%s) adjusted the stock this "
              "coverage check nets against, inside the last 30 days: %s. The netted "
              "figures below come from a master this requester can move." % (
                  who, "; ".join(
                      "%s %g -> %g on %s" % (r["code"], float(r["before_qty"] or 0),
                                             float(r["after_qty"] or 0), str(r["created_at"])[:10])
                      for r in rows)))
    if not conn.execute(
            "SELECT 1 FROM pr_events WHERE pr_id=? AND action='coverage_self_edit' "
            "AND detail=?", (pr_id, detail)).fetchone():
        audit(conn, pr_id, "system", "coverage_self_edit", detail)


def apply_deviation_stages(conn, pr_id, pr):
    """Append the §4.4 extra rungs a priced request has earned. Caller commits.
    Only ever ADDS, and only rungs not already on the ladder — a deviation can
    make an order need more signatures, never fewer."""
    dev = deviation_findings(conn, pr_id)
    _master_moved_by_requester(conn, pr_id, pr, dev)
    if not dev["stages"]:
        return dev
    rows = conn.execute("SELECT seq, stage FROM pr_steps WHERE pr_id=? ORDER BY seq",
                        (pr_id,)).fetchall()
    have = {r["stage"] for r in rows}
    seq = max([r["seq"] for r in rows] or [0])
    _roles = stage_roles_map(conn)
    _chain = escalation_map(conn)
    added = []
    for st in dev["stages"]:
        if st in have:
            continue
        seq += 1
        e_role, e_from, why = _esc_columns(conn, st, pr["requester"], _roles, _chain)
        conn.execute(
            "INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, created_at, "
            "esc_role, esc_from, origin) VALUES (?,?,?,?,?,?,?,?,?)",
            (pr_id, seq, st, "pending", stage_label(st), _now(),
             "" if why == "no_superior" else e_role, e_from, "deviation"))
        added.append(st)
    grades = ", ".join(sorted({l["grade"] for l in dev["lines"]
                               if l["grade"] != "on_plan"}))
    # T&C-PUF-10 is a form retained for a year, and the coverage numbers are
    # computed from stock as it is TODAY — read the panel next year and it will
    # say something different. So the netting that actually triggered the
    # escalation is written into the audit trail, where it stays with the
    # request. This is the retained form, and it is retained whether or not a
    # rung was ADDED: when the stage §4.4 asks for is already on the ladder
    # nothing is appended, and logging only on `added` meant the deviation that
    # blocked a sign-off left no record of itself at all.
    cov = "".join(
        " Coverage check — %s: requested %g, on hand %g, on order %g, "
        "net need %g, excess %g." % (
            l["item"], l["coverage"]["qty"], l["coverage"]["on_hand"],
            l["coverage"]["on_order"], l["coverage"]["net_need"],
            l["coverage"]["excess"])
        for l in dev["lines"] if l.get("over_plan"))
    detail = "DOAM §4.4 deviation (%s): %s%s" % (
        grades,
        ("added %s on top of the value ladder."
         % ", ".join(stage_label(s) for s in added)) if added else
        ("%s already on the ladder — no rung added."
         % ", ".join(stage_label(s) for s in dev["stages"])),
        cov)
    # This runs again at every rung from Purchasing up, so an unchanged reading
    # must not file the same form twice.
    if not conn.execute(
            "SELECT 1 FROM pr_events WHERE pr_id=? AND action='deviation_approval' "
            "AND detail=?", (pr_id, detail)).fetchone():
        audit(conn, pr_id, "system", "deviation_approval", detail)
    if added:
        stamp_step_actions(conn, pr_id)     # the new rungs are Table 5 endorsements
    return dev


def vendor_approved(conn, name):
    """DOAM §4.3 — "No advance to a supplier off the approved vendor list."
    The approved list IS the active vendor master; an inactive or unknown
    supplier is off it."""
    if not str(name or "").strip():
        return False
    row = conn.execute("SELECT is_active FROM proc_vendors WHERE name=?",
                       (name,)).fetchone()
    return bool(row and row["is_active"])


def advance_required_authority(pr, pct):
    """The DOAM §4.3 rule for this request: which stages may authorise an advance
    of `pct`, and whether a bank guarantee is required. Split out so the gate,
    the authorising call and the UI all read the SAME rule.

    The 500,000 guarantee threshold is an EGP figure, so the PO value must be
    converted first. Passing the raw PR total let a USD 20,000 order (about
    1,000,000 EGP) read as 20,000 and walk past the guarantee rule entirely."""
    return C.advance_rule(egp_commitment(pr), pct)


def authorize_advance(pr_id, pct, guarantee_ref, user, ip=None):
    """Record the DOAM §4.3 authorisation for an advance payment. Returns
    (ok, msg). Refuses when the signer does not hold the required authority, the
    supplier is off the approved list, or a required bank guarantee is missing."""
    try:
        pct = float(pct or 0)
    except (TypeError, ValueError):
        return False, "bad_pct"
    if not 0 < pct <= 100:
        return False, "bad_pct"
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("po_issued", "partially_received", "received"):
            return False, "no_po"        # there is no PO value to advance against
        if not vendor_approved(conn, pr["vendor"]):
            return False, "vendor_not_approved"
        rule = advance_required_authority(pr, pct)
        guarantee_ref = str(guarantee_ref or "").strip()[:120]
        if rule["guarantee"] and not guarantee_ref:
            return False, "guarantee_required"
        # Who is signing. can_act() already encodes delegation, proc_admin and
        # the permission check — a second role test here would drift from it.
        _roles = stage_roles_map(conn)
        stage = next((s for s in rule["stages"] if can_act(user, s, _roles=_roles)), None)
        if not stage:
            return False, "not_authorised"
        conn.execute(
            "UPDATE pr_requests SET advance_pct=?, advance_auth_by=?, "
            "advance_auth_stage=?, advance_auth_at=?, bank_guarantee_ref=? WHERE id=?",
            (pct, (user or {}).get("username") or "system", stage or "proc_admin",
             _now(), guarantee_ref or None, pr_id))
        audit(conn, pr_id, (user or {}).get("username") or "system", "advance_authorised",
              f"DOAM §4.3: advance of {pct:.1f}% authorised by "
              f"{stage_label(stage) if stage else 'Procurement admin'}"
              + (f", bank guarantee {guarantee_ref}" if guarantee_ref else ""), ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def advance_gate_check(conn, pr, amount):
    """Is this payment an advance, and if so is it authorised? Returns
    (ok, msg). An advance is a payment made before the supplier has invoiced —
    that is the money genuinely at risk, and the only money §4.3 governs."""
    invoiced = conn.execute(
        # GROSS (amount + tax): it is compared against paid_amount, which is
        # gross. Netting the tax out made every VAT-bearing payment look like an
        # unauthorised advance and refused it with the wrong reason.
        "SELECT COALESCE(SUM(COALESCE(amount,0)+COALESCE(tax,0)),0) AS t "
        "FROM pr_invoices WHERE pr_id=?",
        (pr["id"],)).fetchone()["t"]
    already = float(_pr_field(pr, "paid_amount") or 0)
    if float(invoiced or 0) >= already + amount - 0.01:
        return True, ""                  # covered by invoices — not an advance
    # The PERCENTAGE is currency-agnostic (paid / total, same units), so it uses
    # the PR's own grand total; the RULE below needs EGP for its 500,000 test.
    grand = pr_amounts(pr)["grand"]
    pct = ((already + amount) / grand * 100.0) if grand > 0 else 100.0
    if not vendor_approved(conn, _pr_field(pr, "vendor")):
        return False, "advance_vendor_not_approved"
    authorised = float(_pr_field(pr, "advance_pct") or 0)
    if authorised <= 0:
        return False, "advance_not_authorised"
    if pct > authorised + 0.01:
        return False, "advance_exceeds_authorised"
    rule = C.advance_rule(egp_commitment(pr), pct)
    if rule["guarantee"] and not str(_pr_field(pr, "bank_guarantee_ref") or "").strip():
        return False, "advance_guarantee_required"
    return True, ""


def add_payment(pr_id, data, user, ip=None, force=False):
    """Record a payment against the PR/invoice and roll up the payment status.

    Business gates (bypassable only with `force` = admin override, audited):
      * payments start once a PO exists (po_issued and later) — never on a
        draft/pending/cancelled request;
      * the 3-way match must not show over-billing (invoice > PO, or invoice >
        received value) — "payment block on mismatch";
      * total paid may not exceed the PO grand total (+ the payment tolerance,
        setting 'payment_tolerance_pct' -> C.PAYMENT_TOLERANCE_PCT, default 1%);
      * on a SHORT DELIVERY the cap drops to the value actually received. Paying
        for what arrived is legitimate; paying the whole PO for part of it is
        not, and the DOAM asks for the shortfall to be stated, not waved past;
      * on a requisition SPLIT across suppliers the payment says which order it
        settles (`data["po_id"]`) and the three caps above are applied a second
        time to THAT order alone — computed on the requisition they bound three
        suppliers together, so one could be paid the other two's money."""
    # run the match first (own connections) — before opening ours
    match = three_way_match(pr_id)
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("po_issued", "partially_received", "received", "closed"):
            return False, "not_payable"
        amount = float(data.get("amount") or 0)
        if amount <= 0:
            return False, "bad_amount"
        # WHICH supplier's order this money is for. Only a SPLIT requisition has
        # a choice to make; with one supplier this is (None, None) and every cap
        # below is bit-for-bit what it was. Resolved before any write so the
        # read-through in split_order cannot roll one back.
        po, po_err = split_order(conn, pr, data.get("po_id"))
        if po_err and not force:
            return False, po_err
        # over-billing block (short delivery alone does not block the payment,
        # it caps it — see the received-value cap further down)
        if match and match["has_invoice"] and not force \
           and (not match["price_ok"] or not match["receipt_inv_ok"]):
            return False, "match_blocked"
        # DOAM §4.3 — money leaving before the supplier has invoiced is an
        # advance, and needs its own authority, an approved vendor, and a bank
        # guarantee on large orders. Checked here rather than in the UI so it
        # holds for the API and for anything else that posts a payment.
        adv_ok, adv_msg = advance_gate_check(conn, pr, amount)
        # "No advance to a supplier off the approved vendor list" is written
        # without exception, so that one holds even for an admin override — the
        # others stay overridable like every other gate here.
        if not adv_ok and (not force or adv_msg == "advance_vendor_not_approved"):
            return False, adv_msg
        grand = pr_amounts(pr)["grand"]
        # Configurable slack on the caps. Written as x * (pct/100.0), NOT
        # x * pct / 100.0: with the default 1.0 the first form is bit-for-bit the
        # old `x * 0.01`, the second can differ in the last bit.
        _tol_pct = num_setting(conn, "payment_tolerance_pct") / 100.0
        tol = max(1.0, grand * _tol_pct)
        already = float(pr["paid_amount"] or 0)
        if not force and grand > 0 and already + amount > grand + tol:
            return False, "over_payment"
        # You cannot pay more than you have been BILLED: once invoices exist, cap
        # cumulative payment at the invoiced (gross) total — not just the PO total.
        # (No-invoice advance payments are unchanged, governed by the PO cap above.)
        if match and match["has_invoice"] and not force:
            billed = float(match.get("invoiced") or 0)
            if billed > 0 and already + amount > billed + max(1.0, billed * _tol_pct):
                return False, "exceeds_invoiced"
        # DOAM 7.3.3 short delivery — the third cap, and the one that was missing:
        # goods short, invoice for the full order, every other gate green, and the
        # company paid the whole PO for part of a delivery. Cumulative payment is
        # capped at the GROSS value of what was actually received. Only bites when
        # something is genuinely short, so a fully received order and an advance
        # (no invoice yet, governed above) are untouched. Same payment tolerance
        # as its two siblings above, for rounding and bank charges.
        #
        # Goods REJECTED on receipt ride the same cap: they never entered
        # received_qty, so they are already short here, and their debit note
        # tightens the ceiling further whenever the return was raised after the
        # line had been received in full (match["payable"] does that arithmetic).
        if match and match["has_invoice"] and not force \
           and (float(match.get("short_qty") or 0) > 0
                or float(match.get("debit_open") or 0) > 0):
            rcv = float(match.get("payable", match.get("received_grand")) or 0)
            if already + amount > rcv + max(1.0, rcv * _tol_pct):
                return False, "exceeds_received"
        # The same three caps AGAIN, this time against ONE supplier's own order.
        # The three above are computed on the requisition, which is the right
        # answer while a requisition is one supplier — and the wrong one the
        # moment it is three, because they bound the three together: Gammaknit's
        # 350 order could be invoiced and paid 3,350 without any of them firing.
        # Reuses the existing refusal codes, so the message the payer reads is
        # the one that already describes the cap that stopped them.
        if po is not None and not force:
            ex = po_exposure(conn, pr, po)
            done = ex["paid"]
            if ex["ordered"] > 0 and done + amount > ex["ordered"] + max(
                    1.0, ex["ordered"] * _tol_pct):
                return False, "over_payment"
            if ex["invoiced"] > 0 and done + amount > ex["invoiced"] + max(
                    1.0, ex["invoiced"] * _tol_pct):
                return False, "exceeds_invoiced"
            if (ex["received_grand"] < ex["ordered"] - 0.01 or ex["debit_open"] > 0) \
               and done + amount > ex["payable"] + max(1.0, ex["payable"] * _tol_pct):
                return False, "exceeds_received"
        now = _now()
        if force:
            audit(conn, pr_id, (user or {}).get("username"), "payment_override",
                  "Payment recorded with admin override (match/limit checks bypassed)", ip)
        conn.execute(
            """INSERT INTO pr_payments (pr_id, po_id, invoice_id, amount, currency, method,
               reference, paid_at, notes, created_by, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pr_id, (po or {}).get("id"),
             data.get("invoice_id") or None, amount, pr["currency"] or "EGP",
             data.get("method"), data.get("reference"), data.get("paid_at") or now[:10],
             data.get("notes"), user.get("username") if user else "system", now))
        paid = float(pr["paid_amount"] or 0) + amount
        grand = pr_amounts(pr)["grand"]
        # A PO with goods returned against it can never reach its own grand total,
        # so "fully paid" is measured against the payable value once a debit note
        # exists. Without a debit note this is bit-for-bit the old comparison.
        target = float(match.get("payable") or 0) if (
            match and float(match.get("debit_open") or 0) > 0) else grand
        pstatus = "paid" if paid >= target - 0.01 else ("partial" if paid > 0 else "unpaid")
        conn.execute("UPDATE pr_requests SET paid_amount=?, payment_status=? WHERE id=?",
                     (round(paid, 2), pstatus, pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "payment",
              f"Paid {amount:,.2f} ({pstatus})", ip)
        if pstatus == "paid":
            notify_users(conn, [pr["requester"]], "info", "Payment complete",
                         f"{pr['pr_no']} is fully paid.", link=_pr_link(pr_id))
        conn.commit()
        return True, pstatus
    finally:
        conn.close()


def close_pr(pr_id, user, ip=None):
    conn = get_db()
    try:
        pr = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] not in ("received", "po_issued", "partially_received"):
            return False, "not_closable"
        conn.execute("UPDATE pr_requests SET status='closed', closed_at=? WHERE id=?",
                     (_now(), pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "closed", "Closed", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def email_po_to_vendor(pr_id, user, pdf_bytes=None, ip=None):
    """Email each Purchase Order (PDF attached) to ITS OWN vendor's address.

    One send per order, never one broadcast: a request split across suppliers
    has one document per supplier, and each supplier may only ever receive the
    document carrying their own lines and prices. `pdf_bytes` (if supplied) is
    the primary order's PDF, as before."""
    bundle = get_pr(pr_id)
    if not bundle:
        return False, "not_found"
    pr = bundle["pr"]
    if pr["status"] not in ("approved", "po_issued", "received", "closed"):
        return False, "not_approved"
    pos = bundle.get("pos") or []
    if not pos:
        return False, "no_vendor_email"
    # find each vendor's email
    conn = get_db()
    try:
        emails = {}
        for p in pos:
            v = conn.execute("SELECT email FROM proc_vendors WHERE name=?",
                             (p.get("vendor"),)).fetchone()
            if v and v["email"]:
                emails[p["po_no"]] = v["email"]
    finally:
        conn.close()
    if not emails:
        return False, "no_vendor_email"
    from app.services.alerts import send_email_to
    from app.approvals import pdf as _pdf
    sent, log_lines = False, []
    for i, p in enumerate(pos):
        vendor_email = emails.get(p["po_no"])
        if not vendor_email:
            log_lines.append(f"{p['po_no']}: no email on file for {p.get('vendor') or '—'}")
            continue
        blob = pdf_bytes if (i == 0 and pdf_bytes is not None) else None
        if blob is None:
            try:
                blob = _pdf.po_pdf(bundle, p)
            except Exception:
                blob = None
        body = (f"Dear {p.get('vendor') or pr.get('vendor')},\n\n"
                f"Please find attached Purchase Order {p['po_no']} "
                f"(ref {pr.get('pr_no')}) from T&C Garments.\n\n"
                f"Total: {float(p.get('grand') or 0):,.2f} {p.get('currency') or pr.get('currency') or ''}\n"
                f"Payment: {pr.get('payment_condition') or '-'}\n"
                f"Delivery: {pr.get('delivery_condition') or '-'}\n\n"
                f"Regards,\nT&C Garments — Purchasing")
        atts = [(f"{p['po_no']}.pdf", blob, "application/pdf")] if blob else None
        ok = send_email_to([vendor_email], f"Purchase Order {p['po_no']} — T&C Garments",
                           body, attachments=atts)
        sent = sent or ok
        log_lines.append(f"{p['po_no']} -> {vendor_email}"
                         + ("" if ok else " (SMTP not configured)"))
    conn = get_db()
    try:
        conn.execute("UPDATE pr_requests SET po_sent_at=? WHERE id=?", (_now(), pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "po_emailed",
              "PO emailed — " + "; ".join(log_lines), ip)
        conn.commit()
    finally:
        conn.close()
    return (True, "sent") if sent else (True, "logged")   # logged = SMTP not set yet


def cancel_pr(pr_id, user, ip=None, is_purchasing=False, is_admin=False):
    """Withdraw a request. Authority: the requester may cancel their OWN request
    while it is still in flight (draft/pending/rejected); Purchasing may cancel
    anything not yet approved (incl. bridge auto-PRs); only an admin may cancel
    after approval — and never once goods have been received."""
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("closed", "cancelled"):
            return False, "already_closed"
        if pr["status"] in ("partially_received", "received"):
            return False, "already_received"     # goods in the door — close it, don't erase it
        uname = (user or {}).get("username")
        is_owner = uname and pr["requester"] == uname
        if pr["status"] in ("approved", "po_issued"):
            if not is_admin:
                return False, "needs_admin"      # committed spend: admin decision only
        elif not (is_owner or is_purchasing or is_admin):
            return False, "forbidden"
        conn.execute("UPDATE pr_requests SET status='cancelled', current_seq=0 WHERE id=?",
                     (pr_id,))
        audit(conn, pr_id, uname, "cancelled", "Request cancelled", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Records retention (audit 3.4-b9b)
# --------------------------------------------------------------------------
# The DOAM's retention rule used to exist only as the strings "5 yrs" / "10 yrs"
# printed in the controlled-forms register. Three things make it a control:
#   1. every request is stamped with retention_until at creation (create_pr);
#   2. NOTHING may remove a record while that date is in the future — the guard
#      below is the single chokepoint, and it fails CLOSED on a missing date;
#   3. a REPORT (reports.proc_retention) lists what has passed its date, and a
#      human disposes of records one at a time through dispose_pr.
# There is deliberately NO unattended purge job. An automated process that
# destroys financial records is a far larger risk than keeping them too long:
# over-retention is a storage cost, an erroneous purge is unrecoverable.


def retention_state(pr):
    """{'until', 'years', 'expired'} for one pr_requests row or dict.

    `expired` means "past its retention date and therefore ELIGIBLE for
    disposal" — never "should be deleted". A row with no date reads as NOT
    expired: an unknown retention date is a reason to keep a record, not to
    remove it.
    """
    kind = _pr_field(pr, "expenditure_kind") or "opex"
    until = str(_pr_field(pr, "retention_until") or "").strip()
    if not until:
        # Never stamped (created before the column existed and not yet
        # backfilled): derive it for DISPLAY so the page is not blank, and treat
        # the record as retained.
        until = C.retention_until(_pr_field(pr, "created_at")
                                  or _pr_field(pr, "request_date"), kind)
        return {"until": until, "years": C.retention_years(kind), "expired": False}
    return {"until": until, "years": C.retention_years(kind),
            # `until` is the last day KEPT, as the page words it, so the record
            # only becomes disposable the day AFTER. ISO dates compare as text.
            "expired": until < _now()[:10]}


def retention_guard(conn, pr_id):
    """(ok, msg) — may this record be removed from the register?

    THE chokepoint. Any future delete/purge/archive path must call this rather
    than writing its own comparison; a rule copied into two callers is a rule
    that will disagree with itself.
    """
    pr = conn.execute("SELECT id, created_at, request_date, expenditure_kind, "
                      "retention_until FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    if not pr:
        return False, "not_found"
    st = retention_state(pr)
    if not st["expired"]:
        return False, "retained_until:" + st["until"]
    return True, st["until"]


def dispose_pr(pr_id, user, ip=None):
    """Retire a record that has passed its retention date. Returns (ok, msg).

    Deliberately a SOFT retirement: is_active=0, which every listing and report
    in this module already filters on, so the record leaves the register while
    the row, its lines, its signatures and its audit trail stay on disk. That is
    what "a human acts" should mean for a financial record — reversible, logged,
    and one record at a time.
    """
    conn = get_db()
    try:
        ok, msg = retention_guard(conn, pr_id)
        if not ok:
            return False, msg
        row = conn.execute("SELECT pr_no, is_active FROM pr_requests WHERE id=?",
                           (pr_id,)).fetchone()
        if not row["is_active"]:
            return False, "already_disposed"
        conn.execute("UPDATE pr_requests SET is_active=0 WHERE id=?", (pr_id,))
        audit(conn, pr_id, (user or {}).get("username") or "system", "disposed",
              f"Record retired from the register — retention expired {msg}", ip)
        conn.commit()
        return True, msg
    finally:
        conn.close()


# --------------------------------------------------------------------------
# DOAM documents that are CONDITIONS, not attachments
# --------------------------------------------------------------------------
def set_doam_document(pr_id, field, text, user, ip=None):
    """Record the business case (§4.1 t7 / §4.2 t4) or the §4.4 justification
    memo (T&C-PUF-12) on a request. Returns (ok, msg).

    One writer for both because they are the same shape: a piece of written
    reasoning the DOAM makes a PRECONDITION of a signature. `field` is checked
    against a fixed allow-list — it is interpolated into the SQL, so it may
    never come from a request unvalidated.
    """
    if field not in ("business_case", "deviation_memo"):
        return False, "bad_field"
    body = str(text or "").strip()
    if len(body) < 20:
        # A memo is a justification, not a checkbox. Twenty characters is the
        # smallest thing that cannot be typed by accident; it does not pretend
        # to judge quality, only to stop "ok" from clearing a Board-level rule.
        return False, "too_short"
    conn = get_db()
    try:
        pr = conn.execute("SELECT pr_no, status FROM pr_requests WHERE id=?",
                          (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("closed", "cancelled"):
            return False, "locked"
        conn.execute(f"UPDATE pr_requests SET {field}=? WHERE id=?", (body, pr_id))
        audit(conn, pr_id, (user or {}).get("username") or "system", field,
              ("Business case recorded" if field == "business_case"
               else "DOAM §4.4 justification memo recorded"), ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def business_case_gate(pr):
    """DOAM §4.1 tier 7 / §4.2 tier 4 — "above 10,000,000: Board approval, WITH a
    business case". Returns (ok, msg).

    Compared on egp_commitment(), the same tax-inclusive EGP figure the ladder
    routes on: the business case is a condition of the tier, so it must be
    triggered by the number that decides the tier. Using the raw net total would
    let a 9.9M + VAT order (10.9M committed) past a 10M rule.
    """
    if egp_commitment(pr) <= C.BUSINESS_CASE_OVER:
        return True, ""
    if str(_pr_field(pr, "business_case") or "").strip():
        return True, ""
    return False, "business_case_required"


def deviation_memo_gate(conn, pr_id, pr):
    """DOAM §4.4 — every off-plan grade in the table requires a JUSTIFICATION
    MEMO (T&C-PUF-12) as well as the extra signatures. deviation_grade() has
    always returned that "memo" flag and nothing has ever read it, so the extra
    approvers were being collected while the document they are supposed to be
    reading was never asked for. Returns (ok, msg)."""
    if (_pr_field(pr, "pricing_status") or "priced") != "priced":
        return True, ""              # unpriced: the pricing gate rules first
    if str(_pr_field(pr, "deviation_memo") or "").strip():
        return True, ""
    if not deviation_findings(conn, pr_id)["memo"]:
        return True, ""
    return False, "deviation_memo_required"


# --------------------------------------------------------------------------
# vendors
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Item catalogue (proc_items) — read side
# --------------------------------------------------------------------------
_ITEM_PAGE = 20        # hard cap: 19k rows must never be serialised in one go


def search_items(q, category=None, limit=_ITEM_PAGE, offset=0):
    """Type-ahead over the catalogue. Returns (results, has_more).

    COMMERCIAL LOCKOUT: cost_price is deliberately NOT selected here. Any
    requester can reach this, and a requester states WHAT they need, never what
    it costs — the cost reference is Purchasing-only, at the pricing gate.
    """
    q = (q or "").strip()
    cat = (category or "").strip()
    if len(q) < 2 and not cat:
        return [], False
    limit = max(1, min(int(limit or _ITEM_PAGE), _ITEM_PAGE))
    offset = max(0, int(offset or 0))
    where, params = ["active=1"], []
    if q:
        # code matches from the START (it is an opaque key people type in full);
        # name matches anywhere, which is how a human searches a description.
        where.append("(code LIKE ? OR name LIKE ?)")
        params += [q + "%", "%" + q + "%"]
    if cat:
        where.append("category_code=?")
        params.append(cat)
    sql = ("SELECT id, code, name, unit, category_code, category_name FROM proc_items "
           "WHERE " + " AND ".join(where) + " ORDER BY code LIMIT ? OFFSET ?")
    conn = get_db()
    try:
        rows = conn.execute(sql, tuple(params) + (limit + 1, offset)).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    has_more = len(rows) > limit
    return [{"id": r["id"], "code": r["code"], "name": r["name"] or "",
             "unit": r["unit"] or "Pcs", "category_code": r["category_code"] or "",
             "category": r["category_name"] or ""} for r in rows[:limit]], has_more


def item_categories():
    """[(code, name)] for the picker's category filter. Empty until an import."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT category_code cc, category_name cn FROM proc_items WHERE active=1 "
            "GROUP BY category_code, category_name ORDER BY category_code").fetchall()
        return [(r["cc"] or "", r["cn"] or "") for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def item_costs(item_ids):
    """{pr_items.item_id: {'cost': float, 'has_cost': int, 'code': str}} — the last
    known catalogue cost, for the Purchasing pricing gate ONLY. Callers must gate
    this on proc_purchasing; it is never handed to a requester."""
    ids = [int(i) for i in (item_ids or []) if str(i).strip().isdigit()]
    if not ids:
        return {}
    conn = get_db()
    try:
        ph = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, code, cost_price, has_cost FROM proc_items WHERE id IN ({ph})",
            tuple(ids)).fetchall()
        return {r["id"]: {"cost": float(r["cost_price"] or 0),
                          "has_cost": int(r["has_cost"] or 0),
                          "code": r["code"]} for r in rows}
    except Exception:
        return {}
    finally:
        conn.close()


def list_vendors(active_only=True):
    """Every vendor, each carrying its OUTSTANDING DUES — the open debit-note value
    the supplier owes back for rejected goods. Attached here, on one grouped query,
    so the figure appears wherever vendor information is already rendered instead
    of only on the one PR that raised it."""
    conn = get_db()
    try:
        sql = "SELECT * FROM proc_vendors"
        if active_only:
            sql += " WHERE is_active=1"
        sql += " ORDER BY name"
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
        dues = vendor_dues(conn)
        for v in rows:
            d = dues.get(v.get("name")) or {}
            v["dues_count"], v["dues"] = d.get("count", 0), d.get("amount", 0.0)
        return rows
    finally:
        conn.close()


def create_vendor(data, user, ip=None):
    conn = get_db()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO proc_vendors
               (name, contact_person, phone, email, address, payment_terms, category,
                rating, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (data.get("name"), data.get("contact_person"), data.get("phone"),
             data.get("email"), data.get("address"), data.get("payment_terms"),
             data.get("category"), float(data.get("rating") or 0), data.get("notes"), _now()))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# attachments (vendor quotations)
# --------------------------------------------------------------------------
def add_attachment(pr_id, filename, content_type, content_b64, size, user, ip=None):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO pr_attachments
               (pr_id, filename, content_type, size, content_b64, uploaded_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (pr_id, filename, content_type, size, content_b64,
             user.get("username") if user else "system", _now()))
        audit(conn, pr_id, user.get("username") if user else "system",
              "attachment", f"Attached {filename}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def get_attachment(att_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM pr_attachments WHERE id=?", (att_id,)).fetchone()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Requests for Quotation — the document that PRODUCES the competitive quotes
# --------------------------------------------------------------------------
def issue_rfqs(pr_id, vendors, user, reply_by=None, notes=None, ip=None):
    """Issue ONE Request for Quotation per vendor and return the rows written.

    A request for INFORMATION, not a commitment: nothing here touches the
    request's status, total, vendor or approval ladder — asking three suppliers
    what something costs must leave the requisition exactly as it was.

    Each RFQ takes its own number from doc_no(), so it is a controlled document
    like the PR and the PO. Vendors are de-duplicated within the call; asking the
    same supplier again later is a legitimate re-issue and gets its own number.

    Sourcing CLOSES once the request is approved, ordered or cancelled — the
    same rule the page applies when it hides the form. Checked here, not only in
    the template, because a direct POST reaches the endpoint whatever the page
    shows, and it wrote controlled RFQ numbers against cancelled requests.
    """
    names, seen = [], set()
    for v in (vendors or []):
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            names.append(v)
    if not names:
        return False, "no_vendors"
    conn = get_db()
    try:
        pr = conn.execute("SELECT pr_no, status FROM pr_requests WHERE id=?",
                          (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("approved", "po_issued", "partially_received",
                            "received", "closed", "cancelled"):
            return False, "locked"
        uname = (user or {}).get("username") or "system"
        now = _now()
        out = []
        # Every READ path in this feature degrades on a database predating the
        # table (rfqs_for -> [], get_pr -> rfqs: [], add_quote -> no link); the
        # write was the one place left that answered a missing table with a 500.
        try:
            for vendor in names:
                cur = conn.execute(
                    """INSERT INTO pr_rfqs (pr_id, vendor, reply_by, status, sent_at,
                       created_by, notes) VALUES (?,?,?,'sent',?,?,?)""",
                    (pr_id, vendor, (reply_by or None), now, uname, (notes or None)))
                rfq_id = cur.lastrowid
                rfq_no = doc_no("RFQ", rfq_id)
                conn.execute("UPDATE pr_rfqs SET rfq_no=? WHERE id=?", (rfq_no, rfq_id))
                out.append({"id": rfq_id, "rfq_no": rfq_no, "vendor": vendor})
            audit(conn, pr_id, uname, "rfq_issued",
                  "%d request(s) for quotation issued: %s" % (
                      len(out), ", ".join("%s (%s)" % (r["rfq_no"], r["vendor"])
                                          for r in out)), ip)
            conn.commit()
        except Exception:
            conn.rollback()
            return False, "unavailable"
        return True, out
    finally:
        conn.close()


def rfqs_for(conn, pr_id):
    """Every RFQ issued for a request. Empty on a database predating the table."""
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pr_rfqs WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
    except Exception:
        conn.rollback()
        return []


# --------------------------------------------------------------------------
# multi-quote comparison
# --------------------------------------------------------------------------
def add_quote(pr_id, data, user, filename=None, content_type=None, content_b64=None, ip=None):
    conn = get_db()
    try:
        # The RFQ this quotation answers, when it answers one. Validated against
        # THIS request, so a stray id cannot attach a quote to another PR's RFQ.
        # Unresolvable (bad id, or a database predating the table) -> no link,
        # and the INSERT below is then byte-for-byte the one that always ran.
        rfq_id = None
        if str(data.get("rfq_id") or "").strip():
            try:
                row = conn.execute("SELECT id FROM pr_rfqs WHERE id=? AND pr_id=?",
                                   (int(data["rfq_id"]), pr_id)).fetchone()
                rfq_id = row["id"] if row else None
            except Exception:
                conn.rollback()
                rfq_id = None
        conn.execute(
            """INSERT INTO pr_quotes
               (pr_id, vendor, amount, currency, lead_time_days, warranty,
                filename, content_type, content_b64, notes, uploaded_by, created_at"""
            + (", rfq_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)" if rfq_id
               else ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"),
            (pr_id, data.get("vendor"), float(data.get("amount") or 0),
             data.get("currency") or "EGP",
             int(data["lead_time_days"]) if str(data.get("lead_time_days") or "").strip() else None,
             data.get("warranty"), filename, content_type, content_b64, data.get("notes"),
             user.get("username") if user else "system", _now())
            + ((rfq_id,) if rfq_id else ()))
        if rfq_id:
            conn.execute("UPDATE pr_rfqs SET status='answered' WHERE id=?", (rfq_id,))
        audit(conn, pr_id, user.get("username") if user else "system", "quote_added",
              f"Quote from {data.get('vendor')} @ {data.get('amount')}"
              + (f" (answers RFQ #{rfq_id})" if rfq_id else ""), ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def choose_quote(pr_id, quote_id, user, ip=None):
    """Mark one quote as chosen (clears the others) and adopt its vendor on the PR."""
    conn = get_db()
    try:
        q = conn.execute("SELECT * FROM pr_quotes WHERE id=? AND pr_id=?",
                         (quote_id, pr_id)).fetchone()
        if not q:
            return False, "not_found"
        pr = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if pr and pr["status"] in ("approved", "po_issued", "partially_received",
                                   "received", "closed", "cancelled"):
            return False, "locked"   # the vendor is fixed once the PR is approved
        conn.execute("UPDATE pr_quotes SET is_chosen=0 WHERE pr_id=?", (pr_id,))
        conn.execute("UPDATE pr_quotes SET is_chosen=1 WHERE id=?", (quote_id,))
        conn.execute("UPDATE pr_requests SET vendor=? WHERE id=?", (q["vendor"], pr_id))
        audit(conn, pr_id, user.get("username") if user else "system", "quote_chosen",
              f"Selected {q['vendor']} @ {q['amount']}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def get_quote(quote_id):
    conn = get_db()
    try:
        return conn.execute("SELECT * FROM pr_quotes WHERE id=?", (quote_id,)).fetchone()
    finally:
        conn.close()


def quote_comparison(quotes):
    """Given a PR's quotes, return the min amount and potential saving vs the max."""
    amts = [q["amount"] for q in quotes if q.get("amount")]
    if len(amts) < 2:
        return {"lowest": min(amts) if amts else None, "saving": 0}
    return {"lowest": min(amts), "highest": max(amts), "saving": round(max(amts) - min(amts), 2)}


def _pr_field(pr, key, default=None):
    """Read a column off a PR row (dict, sqlite3.Row or _PGRow) tolerantly —
    a missing column (pre-migration database) returns `default`, never raises."""
    if pr is None:
        return default
    try:
        if isinstance(pr, dict):
            return pr.get(key, default)
        return pr[key]
    except (KeyError, IndexError):
        return default


def rfq_gate_check(conn, pr):
    """RFQ / competitive-quotation gate for the Purchasing sign-off (pure check,
    no writes). A PRICED request whose total is at/above the rfq_value_threshold
    setting (override -> C.RFQ_VALUE_THRESHOLD) must carry at least
    rfq_quote_min (override -> C.RFQ_QUOTE_MIN) competing vendor quotes — unless
    Purchasing has recorded a single-source justification on the PR.

    `pr` is the pr_requests row (dict or Row). Returns (ok, msg):
    (False, "needs_quotes") when the rule blocks the sign-off, else (True, "")."""
    if (_pr_field(pr, "pricing_status") or "priced") != "priced":
        return True, ""          # unpriced: the pricing gate rules first
    # Threshold is EGP-based: compare the EGP-EQUIVALENT total (fx-converted),
    # not the raw foreign figure — a 60,000 TRY (~3,000 EGP) order must not
    # trigger the competitive-quote rule meant for 25,000+ EGP purchases.
    try:
        total = float(egp_commitment(pr))
    except (TypeError, ValueError):
        total = 0.0
    # DOAM §4.3 bands when the matrix is in force: 1 quote up to 50,000, three to
    # 500,000, three plus negotiation to 2,000,000, formal tender above that. The
    # flat "2 quotes at 25,000" setting was the pre-DOAM rule and stays as the
    # fallback so a database with the matrix switched off behaves as it always did.
    if C.DOAM_IN_FORCE:
        band = C.sourcing_band(total)
        required_quotes = band["quotes"]
        # The lowest band is "One quotation — buyer records the price basis". It
        # is ONE, not none: returning early here let a 48,000 EGP purchase be
        # signed with no recorded price basis at all, which is the opposite of
        # what the band says. A genuinely uncontested buy still has to carry a
        # single-source justification, which is checked just below.
        required_quotes = max(1, required_quotes)
    else:
        if total < num_setting(conn, "rfq_value_threshold"):
            return True, ""      # below the competitive-quote threshold
        required_quotes = num_setting(conn, "rfq_quote_min")
    # DOAM §4.4 — an off-plan PRICE (anything over the target) requires three
    # competitive quotes whatever the order is worth. deviation_grade() has
    # always returned that flag and nothing read it, so a small order priced 40%
    # over target satisfied the value band's single quote and nothing else. Only
    # ever RAISES the requirement; a band that already demands three stays three.
    # Fails soft: a grading error must not invent a quote requirement.
    try:
        if deviation_findings(conn, _pr_field(pr, "id"))["quotes"]:
            required_quotes = max(required_quotes, 3)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            "DOAM 4.4: deviation quote requirement unavailable (%s)", exc)
    if str(_pr_field(pr, "single_source_reason") or "").strip():
        return True, ""          # justified single/sole-source purchase
    # "Competitive" means distinct VENDORS, not just distinct quote rows — two quotes
    # typed against the same supplier must not satisfy the rule.
    try:
        n = conn.execute("SELECT COUNT(DISTINCT NULLIF(TRIM(vendor),'')) c FROM pr_quotes "
                         "WHERE pr_id=?", (_pr_field(pr, "id"),)).fetchone()["c"]
    except Exception:
        n = conn.execute("SELECT COUNT(*) c FROM pr_quotes WHERE pr_id=?",
                         (_pr_field(pr, "id"),)).fetchone()["c"]
    if int(n or 0) < required_quotes:
        return False, "needs_quotes"
    return True, ""


def attach_ejr(pr_id, ejr_id, user, ip=None):
    """Cite an APPROVED Engineering Justification Report on a requisition.

    Without this the DOAM §6 gate is unsatisfiable: it refuses any spares/MRO
    request that has no report, and nothing could put one there — so switching
    the gate on blocked maintenance purchasing outright. The report must already
    be approved, because the whole control is that Engineering signs BEFORE
    Procurement accepts (Table 13 step 3); letting a draft be attached would let
    the requester supply their own justification and move on.
    """
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, ejr_no, status FROM mnt_eng_justifications "
            "WHERE id=? AND is_active=1", (ejr_id,)).fetchone()
        if not row:
            return False, "ejr_not_found"
        if row["status"] != "approved":
            return False, "ejr_not_approved"
        conn.execute("UPDATE pr_requests SET ejr_id=? WHERE id=?", (ejr_id, pr_id))
        audit(conn, pr_id, (user or {}).get("username") or "system", "ejr_attached",
              f"Engineering Justification {row['ejr_no']} cited", ip)
        conn.commit()
        return True, row["ejr_no"]
    except Exception:
        conn.rollback()
        return False, "error"
    finally:
        conn.close()


def _append_single_source_rung(conn, pr_id, pr):
    """Add the DOAM §4.3 escalation rung to a PR already in flight. Caller
    commits. Idempotent: a PR whose justification is edited twice does not
    collect two extra signatures."""
    rows = conn.execute("SELECT seq, stage FROM pr_steps WHERE pr_id=? ORDER BY seq",
                        (pr_id,)).fetchall()
    stages = [r["stage"] for r in rows]
    kind = _pr_field(pr, "expenditure_kind")
    extra = C.single_source_stage(stages, kind)
    if not extra:
        return None
    seq = max([r["seq"] for r in rows] or [0]) + 1
    _roles = stage_roles_map(conn)
    e_role, e_from, why = _esc_columns(conn, extra, pr["requester"], _roles,
                                       escalation_map(conn))
    conn.execute(
        "INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, created_at, "
        "esc_role, esc_from, origin) VALUES (?,?,?,?,?,?,?,?,?)",
        (pr_id, seq, extra, "pending", stage_label(extra), _now(),
         "" if why == "no_superior" else e_role, e_from, "single_source"))
    audit(conn, pr_id, "system", "single_source_escalation",
          f"Competition waived — DOAM §4.3 adds {stage_label(extra)} as the "
          f"approval one level above the value tier.")
    stamp_step_actions(conn, pr_id)     # a level-above rung is a Table 5 endorsement
    return extra


def set_single_source(pr_id, reason, user, ip=None):
    """Purchasing records why competitive quotes are waived for this PR (single/
    sole-source purchase: only OEM vendor, proprietary part, emergency, ...).
    Satisfies rfq_gate_check without C.RFQ_QUOTE_MIN quotes. Returns (ok, msg)."""
    reason = (reason or "").strip()
    if not reason:
        return False, "reason_required"
    reason = reason[:1000]       # keep the justification a sane size
    conn = get_db()
    try:
        pr = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
        if not pr:
            return False, "not_found"
        if pr["status"] in ("approved", "po_issued", "partially_received",
                            "received", "closed", "cancelled"):
            return False, "locked"   # sourcing is fixed once the PR is approved
        conn.execute("UPDATE pr_requests SET single_source_reason=? WHERE id=?",
                     (reason, pr_id))
        audit(conn, pr_id, (user or {}).get("username") or "system", "single_source",
              f"Single-source justification recorded: {reason}", ip)
        # DOAM §4.3 — waiving competition costs one extra signature, one level
        # above the value tier. A draft picks this up when it is submitted; a PR
        # already in flight needs the rung appended now, or the waiver would be
        # recorded with no consequence at all.
        # Only on the FIRST waiver: the extra signature is owed for waiving
        # competition, not for each time the wording is edited.
        if pr["status"] == "pending" and not str(
                _pr_field(pr, "single_source_reason") or "").strip():
            _append_single_source_rung(conn, pr_id, pr)
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# department budgets
# --------------------------------------------------------------------------
def _period():
    return str(_year())


def list_budgets(period=None):
    conn = get_db()
    try:
        if period:
            rows = conn.execute("SELECT * FROM proc_budgets WHERE period=? ORDER BY department",
                                (period,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM proc_budgets ORDER BY period DESC, department").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_budget(department, amount, period=None, currency="EGP", user=None):
    period = period or _period()
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM proc_budgets WHERE department=? AND period=?",
                           (department, period)).fetchone()
        if row:
            conn.execute("UPDATE proc_budgets SET amount=?, currency=? WHERE id=?",
                         (float(amount or 0), currency, row["id"]))
        else:
            conn.execute(
                "INSERT INTO proc_budgets (department, period, currency, amount, created_at) "
                "VALUES (?,?,?,?,?)", (department, period, currency, float(amount or 0), _now()))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# The SQL twin of egp_commitment(): tax-inclusive, fx-converted EGP. `spent` and
# the request being weighed against it MUST be the same kind of number — summing
# raw `total` counted a 100 USD @ 50 commitment as 100 EGP, i.e. a fiftieth of
# itself, and compared an ex-VAT sum against a tax-inclusive request.
_EGP_GROSS_SQL = ("COALESCE(total,0) * COALESCE(NULLIF(fx_rate,0),1) "
                  "* (1 + COALESCE(tax_rate,0)/100.0)")


def budget_status(department, period=None, extra=0.0, conn=None):
    """Return {amount, spent, remaining, pct, over} for a department+period.
    'spent' = committed spend (approved / po_issued / closed) this period; 'extra'
    lets a not-yet-submitted PR test whether it would breach.

    `conn` reuses a caller's OPEN connection. Routing calls this in the middle of
    the transaction that writes the ladder, and on SQLite a second connection
    would read the pre-transaction snapshot (or block on the writer's lock), so
    the answer has to come from the same connection the ladder is being built on.
    """
    if not department:
        return None
    period = period or _period()
    own_conn = conn is None
    conn = conn or get_db()
    try:
        b = conn.execute("SELECT amount, currency FROM proc_budgets WHERE department=? AND period=?",
                         (department, period)).fetchone()
        spent = conn.execute(
            "SELECT COALESCE(SUM(" + _EGP_GROSS_SQL + "),0) s FROM pr_requests WHERE department=? "
            "AND status IN ('approved','po_issued','partially_received','received','closed') "
            "AND substr(COALESCE(request_date, created_at),1,4)=?", (department, period)).fetchone()["s"]
    finally:
        if own_conn:
            conn.close()
    if not b:
        return {"amount": None, "spent": spent, "remaining": None, "pct": None,
                "over": False, "currency": "EGP", "period": period}
    amount = b["amount"] or 0
    projected = spent + (extra or 0)
    remaining = amount - projected
    pct = round(100 * projected / amount) if amount else 0
    return {"amount": amount, "spent": spent, "remaining": remaining, "pct": pct,
            "over": projected > amount, "currency": b["currency"] or "EGP", "period": period}


# --- DOAM Table 4: budgeted vs unbudgeted, decided at ROUTING time -----------
BUDGET_STATES = ("budgeted", "no_budget", "over_budget")


def budget_check(conn, department, egp_total, period=None, pr_id=None):
    """Is this commitment inside the department's approved budget for the period?

    Answered with budget_status() — the same proc_budgets row and the same
    'spent' sum the request page already shows — so there is exactly one piece
    of budget arithmetic in this module. All this adds is the routing verdict:

        budgeted      a budget exists and this request still fits inside it
        no_budget     no budget row for this department + period at all
        over_budget   a budget exists and this request pushes past it

    Returns {"unbudgeted", "state", "over_by", "status"}. `over_by` is how far
    past the budget the request takes the department (None when the department
    simply has no budget — there is no line to be over).
    """
    if not department:
        # No cost owner means no approved plan behind the spend, and a blank
        # department must not be the cheap way past this gate.
        return {"unbudgeted": True, "state": "no_budget", "over_by": None, "status": None}
    # The other requests already circulating priced against this same budget are
    # LIVE commitment too, and 'spent' cannot see them (they are not approved
    # yet). Weighing only this request's own value made the control splittable:
    # three concurrent 8,000 requests against a 10,000 budget each read
    # "budgeted" and none picked up the L1 rung. Counted only for the ROUTING
    # verdict — budget_status()'s displayed 'spent' still means settled spend.
    live = 0.0
    if pr_id:
        live = conn.execute(
            "SELECT COALESCE(SUM(" + _EGP_GROSS_SQL + "),0) s FROM pr_requests "
            "WHERE department=? AND status='pending' AND pricing_status='priced' "
            "AND id<>? AND substr(COALESCE(request_date, created_at),1,4)=?",
            (department, pr_id, period or _period())).fetchone()["s"] or 0.0
    st = budget_status(department, period, extra=(egp_total or 0) + live, conn=conn)
    if not st or st.get("amount") is None:
        return {"unbudgeted": True, "state": "no_budget", "over_by": None, "status": st}
    if st.get("over"):
        return {"unbudgeted": True, "state": "over_budget",
                "over_by": round(-float(st["remaining"] or 0), 2), "status": st}
    return {"unbudgeted": False, "state": "budgeted", "over_by": None, "status": st}


def apply_single_source_state(conn, pr_id, pr=None, ip=None):
    """DOAM §4.3 — re-derive the single-source escalation against the value the
    request now carries. Caller commits.

    "Single source (any value): written justification, approved ONE LEVEL ABOVE
    THE VALUE TIER." One level above WHAT depends on the value, so the rung has
    to move when the value does. It was derived once when competition was waived
    and never revisited, which made the escalation a function of pricing history
    rather than of the money being committed — and re-pricing upward quietly
    turned the waiver into no extra approval at all.

    Runs at the pricing gate for the same reason apply_budget_state does: every
    request the UI creates is submitted at zero, so the value that decides this
    does not exist until Purchasing prices it.
    """
    pr = pr or conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    if not pr or not str(_pr_field(pr, "single_source_reason") or "").strip():
        return None
    if (_pr_field(pr, "status") or "") != "pending":
        return None
    rows = conn.execute("SELECT seq, stage, COALESCE(origin,'ladder') AS origin "
                        "FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()
    stages = [r["stage"] for r in rows]
    kind = _pr_field(pr, "expenditure_kind")
    extra = C.single_source_stage(stages, kind)
    if not extra:
        return None                      # the ladder already reaches that high
    seq = max([r["seq"] for r in rows] or [0]) + 1
    _roles = stage_roles_map(conn)
    e_role, e_from, why = _esc_columns(conn, extra, _pr_field(pr, "requester"),
                                       _roles, escalation_map(conn))
    conn.execute(
        "INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, created_at, "
        "esc_role, esc_from, origin) VALUES (?,?,?,?,?,?,?,?,?)",
        (pr_id, seq, extra, "pending", stage_label(extra), _now(),
         "" if why == "no_superior" else e_role, e_from, "single_source"))
    audit(conn, pr_id, "system", "single_source_escalation",
          f"Competition waived — DOAM §4.3 puts the approval one level above the "
          f"value tier, which at this value is {stage_label(extra)}.", ip)
    # The ladder's shape changed, so the Table 5 letters have to move with it —
    # the same call _append_single_source_rung and apply_budget_state make after
    # their own INSERT. Without it this rung kept the column default (A) instead
    # of the E a control rung carries, so a waiver re-derived at the pricing gate
    # printed a SECOND final authority beside the value tier's own.
    stamp_step_actions(conn, pr_id)
    return extra


def apply_budget_state(conn, pr_id, pr=None, ip=None):
    """DOAM Table 4, applied to a request that now has a real value: record
    whether it is budgeted, and if it is not, add the L1 rung. Caller commits.

    Called from the PRICING gate (and from set_fx, which moves the EGP figure
    the same way), because the requester price lockout means every request the
    UI creates is submitted at zero — a budget test at submit would be weighing
    nothing. Exactly why §3.4's aggregate is recomputed there too.

    Idempotent, and never lengthens the ladder twice: unbudgeted_stage() returns
    None once the ladder already reaches L1. Appended LAST, after the value and
    §4.4 deviation rungs, because it is the senior signature.

    Returns the budget_check verdict.
    """
    pr = pr or conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    if not pr:
        return {"unbudgeted": False, "state": None, "over_by": None, "status": None}
    department = _pr_field(pr, "department")
    kind = _pr_field(pr, "expenditure_kind") or "opex"
    bud = budget_check(conn, department, egp_commitment(pr), pr_id=pr_id)
    was = _pr_field(pr, "budget_state") or ""
    conn.execute("UPDATE pr_requests SET budget_state=?, budget_over_by=? WHERE id=?",
                 (bud["state"], bud["over_by"], pr_id))
    if bud["unbudgeted"] and was != bud["state"]:
        # On the request, not only in a log: the approver about to sign is owed
        # the reason the DOAM put their signature on this ladder.
        audit(conn, pr_id, "system", "unbudgeted",
              unbudgeted_note(bud, department, kind), ip)
    if _pr_field(pr, "status") != "pending":
        return bud
    if not bud["unbudgeted"]:
        # It fits the budget now (the price was corrected down, or Finance
        # loaded the budget) — so take the rung back off. Scoped to rows this
        # control itself added and only while they are still ahead of the
        # request: the §4.3 / §4.4 rungs, and anything already signed or being
        # signed, are untouchable, which is the R3 invariant re-pricing must not
        # break. A control that never lets go is a control nobody keeps.
        gone = conn.execute(
            "SELECT stage FROM pr_steps WHERE pr_id=? AND origin='unbudgeted' "
            "AND status='pending' AND seq>?",
            (pr_id, _pr_field(pr, "current_seq") or 0)).fetchall()
        if gone:
            conn.execute("DELETE FROM pr_steps WHERE pr_id=? AND origin='unbudgeted' "
                         "AND status='pending' AND seq>?",
                         (pr_id, _pr_field(pr, "current_seq") or 0))
            audit(conn, pr_id, "system", "ladder_rung_removed",
                  "DOAM Table 4: the request is inside the approved budget again, "
                  "so " + ", ".join(stage_label(r["stage"]) for r in gone) +
                  " is no longer required for it.", ip)
            stamp_step_actions(conn, pr_id)   # the Approve moves back down a rung
        return bud
    rows = conn.execute("SELECT seq, stage FROM pr_steps WHERE pr_id=? ORDER BY seq",
                        (pr_id,)).fetchall()
    extra = C.unbudgeted_stage([r["stage"] for r in rows], kind)
    if not extra:
        return bud                     # the ladder already reaches L1
    e_role, e_from, why = _esc_columns(conn, extra, _pr_field(pr, "requester"),
                                       stage_roles_map(conn), escalation_map(conn))
    conn.execute(
        "INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, created_at, "
        "esc_role, esc_from, origin) VALUES (?,?,?,?,?,?,?,?,?)",
        (pr_id, max([r["seq"] for r in rows] or [0]) + 1, extra, "pending",
         stage_label(extra), _now(),
         "" if why == "no_superior" else e_role, e_from, "unbudgeted"))
    audit(conn, pr_id, "system", "unbudgeted_escalation",
          f"DOAM Table 4 adds {stage_label(extra)} ({C.UNBUDGETED_LEVEL}) — "
          f"unbudgeted spend is an {C.UNBUDGETED_LEVEL} commitment.", ip)
    stamp_step_actions(conn, pr_id)         # an L1 rung above the tier endorses
    return bud


def unbudgeted_note(bud, department, kind=None):
    """The sentence written onto the request so the approver reads WHY an extra
    signature is on their desk. English here; the request page renders its own
    translated wording from budget_state / budget_over_by."""
    st = bud.get("status") or {}
    period = st.get("period") or _period()
    cur = st.get("currency") or "EGP"
    stage = C.unbudgeted_stage(C.build_ladder(0, kind), kind) or "cfo"
    tail = (f"DOAM Table 4 puts unbudgeted spend at {C.UNBUDGETED_LEVEL} "
            f"(up to the {C.UNBUDGETED_LEVEL} limit), so {stage_label(stage)} is "
            f"added to the ladder. The spend is not refused — it needs higher "
            f"authority, not refusal.")
    if bud.get("state") == "over_budget":
        return (f"UNBUDGETED: this request takes {department} "
                f"{float(bud.get('over_by') or 0):,.2f} {cur} past its approved "
                f"{period} budget of {float(st.get('amount') or 0):,.2f} {cur} "
                f"(already committed {float(st.get('spent') or 0):,.2f}). " + tail)
    return (f"UNBUDGETED: no approved {period} budget exists for "
            f"{department or '—'}. §4.1 is the BUDGETED ladder. " + tail)


# --------------------------------------------------------------------------
# delegations
# --------------------------------------------------------------------------
def list_delegations(active_only=False):
    conn = get_db()
    try:
        sql = "SELECT * FROM proc_delegations"
        if active_only:
            sql += " WHERE is_active=1"
        sql += " ORDER BY id DESC"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def add_delegation(from_user, to_user, from_date, to_date, note, user=None):
    if not from_user or not to_user or from_user == to_user:
        return False, "invalid"
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO proc_delegations (from_user, to_user, from_date, to_date, note, created_at)
               VALUES (?,?,?,?,?,?)""",
            (from_user, to_user, from_date or None, to_date or None, note, _now()))
        conn.commit()
        return True, ""
    finally:
        conn.close()


def revoke_delegation(deleg_id):
    conn = get_db()
    try:
        conn.execute("UPDATE proc_delegations SET is_active=0 WHERE id=?", (deleg_id,))
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --------------------------------------------------------------------------
# SLA escalation job (call from a scheduler / admin action)
# --------------------------------------------------------------------------
def run_escalations():
    """Escalate stuck approvals, once per level per step (P7).

    Level 1 (unchanged): the active step is older than SLA_HOURS_PER_STAGE ->
      flag it (escalated / escalated_at) and warn the stage's approvers.
    Level 2: the same step is older than 2x the SLA and escalated2_at is not
      set yet -> stamp escalated2_at, raise a CRITICAL bell broadcast and
      notify the stage's approvers plus every active super_admin.
    Pricing stall: a pending, still-unpriced PR submitted more than
      SLA_HOURS_PER_STAGE (48h) ago -> remind Purchasing once per PR (the
      audit trail's 'pricing_reminder' event doubles as the dedupe flag).

    Each level dedupes on its own stamp (escalated_at / escalated2_at / the
    audit row), so repeated scheduler runs never re-alert. Returns the total
    number of newly raised escalations + reminders."""
    conn = get_db()
    n = 0
    try:
        prs = conn.execute("SELECT * FROM pr_requests WHERE status='pending'").fetchall()
        for pr in prs:
            # ---- pricing-stall reminder (independent of the ladder position) ----
            if (pr["pricing_status"] or "priced") == "unpriced":
                sub_hrs = _age_hours(pr["submitted_at"])
                if sub_hrs is not None and sub_hrs > C.SLA_HOURS_PER_STAGE:
                    prior = conn.execute(
                        "SELECT 1 FROM pr_events WHERE pr_id=? AND action='pricing_reminder' "
                        "LIMIT 1", (pr["id"],)).fetchone()
                    if not prior:
                        audit(conn, pr["id"], "system", "pricing_reminder",
                              f"Unpriced for {round(sub_hrs)}h — Purchasing reminded")
                        notify_users(conn, eligible_approvers(conn, "purchasing"),
                                     "warning", "Pricing pending",
                                     f"{pr['pr_no']} has waited {round(sub_hrs)}h without "
                                     f"commercial pricing — enter the value so it can move on.",
                                     link=_pr_link(pr["id"]))
                        n += 1

            step = conn.execute(
                "SELECT * FROM pr_steps WHERE pr_id=? AND seq=? AND status='pending'",
                (pr["id"], pr["current_seq"])).fetchone()
            if not step:
                continue
            hrs = _age_hours(step["activated_at"] or pr["submitted_at"])
            if hrs is None:
                continue
            # ---- level 1: overdue (once, keyed on escalated/escalated_at) ----
            if not step["escalated"] and hrs > C.SLA_HOURS_PER_STAGE:
                conn.execute("UPDATE pr_steps SET escalated=1, escalated_at=? WHERE id=?",
                             (_now(), step["id"]))
                audit(conn, pr["id"], "system", "escalated",
                      f"{stage_label(step['stage'])} overdue ({round(hrs)}h)")
                targets = eligible_approvers(
                    conn, step["stage"],
                    roles=_csv_set(_pr_field(step, "esc_role")) or None) or [pr["requester"]]
                notify_users(conn, targets, "warning", "Approval overdue",
                             f"{pr['pr_no']} has waited {round(hrs)}h at {stage_label(step['stage'])}.",
                             link=_pr_link(pr["id"]))
                n += 1
            # ---- level 2: > 2x SLA (once, keyed on escalated2_at) ----
            if step["escalated2_at"] is None and hrs > 2 * C.SLA_HOURS_PER_STAGE:
                conn.execute("UPDATE pr_steps SET escalated2_at=? WHERE id=?",
                             (_now(), step["id"]))
                audit(conn, pr["id"], "system", "escalated2",
                      f"{stage_label(step['stage'])} critically overdue ({round(hrs)}h, >2x SLA)")
                admins = [r["username"] for r in conn.execute(
                    "SELECT username FROM users WHERE is_active=1 AND role='super_admin'"
                ).fetchall()]
                targets = set(eligible_approvers(
                    conn, step["stage"],
                    roles=_csv_set(_pr_field(step, "esc_role")) or None)) | set(admins)
                bell(conn, "critical", "Approval overdue (level 2)",
                     f"{pr['pr_no']} stuck {round(hrs)}h at {stage_label(step['stage'])} — "
                     f"more than twice the stage SLA.", link=_pr_link(pr["id"]))
                notify_users(conn, list(targets), "critical", "Approval overdue (level 2)",
                             f"{pr['pr_no']} has waited {round(hrs)}h at "
                             f"{stage_label(step['stage'])} — management attention needed.",
                             link=_pr_link(pr["id"]))
                n += 1
        conn.commit()
    finally:
        conn.close()
    return n


# --------------------------------------------------------------------------
# spend analytics
# --------------------------------------------------------------------------
def analytics_summary():
    """Aggregate procurement KPIs + breakdowns for the analytics dashboard."""
    conn = get_db()
    try:
        prs = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_requests WHERE is_active=1").fetchall()]
        steps = [dict(r) for r in conn.execute(
            "SELECT * FROM pr_steps").fetchall()]
    finally:
        conn.close()

    committed = [p for p in prs if p["status"] in ("approved", "po_issued", "closed")]
    by = lambda key, rows: _group_sum(rows, key, "total")
    spend_dept = by("department", committed)
    spend_vendor = by("vendor", committed)
    # monthly committed spend
    months = {}
    for p in committed:
        m = (p.get("request_date") or p.get("created_at") or "")[:7]
        if m:
            months[m] = round(months.get(m, 0) + (p.get("total") or 0), 2)
    monthly = sorted(months.items())

    # cycle time (submitted -> approved), in days
    durs = []
    for p in prs:
        if p.get("submitted_at") and p.get("approved_at"):
            h = _age_hours(p["submitted_at"])
            a = _age_hours(p["approved_at"])
            if h is not None and a is not None:
                durs.append((h - a) / 24.0)
    avg_cycle = round(sum(durs) / len(durs), 1) if durs else 0

    # bottleneck: avg hours a stage takes to be acted on + rejection counts
    stage_times, stage_rej = {}, {}
    for s in steps:
        if s.get("status") in ("approved", "rejected") and s.get("activated_at") and s.get("acted_at"):
            act = _age_hours(s["activated_at"])
            done = _age_hours(s["acted_at"])
            if act is not None and done is not None:
                stage_times.setdefault(s["stage"], []).append(max(0, act - done))
        if s.get("status") == "rejected":
            stage_rej[s["stage"]] = stage_rej.get(s["stage"], 0) + 1
    stage_avg = {k: round(sum(v) / len(v), 1) for k, v in stage_times.items() if v}
    bottleneck = max(stage_avg, key=stage_avg.get) if stage_avg else None

    status_counts = {}
    for p in prs:
        status_counts[p["status"]] = status_counts.get(p["status"], 0) + 1

    return {
        "total_prs": len(prs),
        "committed_spend": round(sum(p.get("total") or 0 for p in committed), 2),
        "pending_value": round(sum(p.get("total") or 0 for p in prs if p["status"] == "pending"), 2),
        "avg_cycle_days": avg_cycle,
        "status_counts": status_counts,
        "spend_by_dept": spend_dept,
        "spend_by_vendor": dict(sorted(spend_vendor.items(), key=lambda kv: -kv[1])[:8]),
        "monthly": monthly,
        "stage_avg_hours": stage_avg,
        "stage_rejections": stage_rej,
        "bottleneck": stage_label(bottleneck) if bottleneck else None,
    }


# Statuses that represent committed (approved-or-later) spend for reporting.
_SPEND_STATUSES = ("approved", "po_issued", "partially_received", "received", "closed")


def export_dataset(key):
    """Rows for a named CSV report: returns (headers, rows) or (None, None).

    Keys: register | spend_dept | spend_vendor | open_orders | payments.
    Headers are plain-English column titles; rows are lists in header order,
    ready for csv.writer (None values serialise as empty cells).
    """
    conn = get_db()
    try:
        if key == "register":
            rows = conn.execute(
                "SELECT pr_no, title, department, requester, vendor, status, "
                "pricing_status, currency, total, tax_rate, request_date, "
                "approved_at, po_no, payment_status, paid_amount "
                "FROM pr_requests WHERE is_active=1 ORDER BY id").fetchall()
            return (["PR No", "Title", "Department", "Requester", "Vendor",
                     "Status", "Pricing", "Currency", "Total", "Tax %",
                     "Request Date", "Approved At", "PO No", "Payment Status",
                     "Paid Amount"],
                    [[r["pr_no"], r["title"], r["department"], r["requester"],
                      r["vendor"], r["status"], r["pricing_status"], r["currency"],
                      r["total"], r["tax_rate"], r["request_date"], r["approved_at"],
                      r["po_no"], r["payment_status"], r["paid_amount"]]
                     for r in rows])

        ph = ",".join("?" * len(_SPEND_STATUSES))
        if key == "spend_dept":
            rows = conn.execute(
                "SELECT COALESCE(NULLIF(department,''),'—') d, COUNT(*) n, "
                "ROUND(SUM(COALESCE(total,0)),2) t "
                "FROM pr_requests WHERE is_active=1 AND status IN (" + ph + ") "
                "GROUP BY d ORDER BY t DESC", _SPEND_STATUSES).fetchall()
            return (["Department", "PRs", "Total"],
                    [[r["d"], r["n"], r["t"]] for r in rows])

        if key == "spend_vendor":
            rows = conn.execute(
                "SELECT COALESCE(NULLIF(vendor,''),'(unassigned)') v, COUNT(*) n, "
                "ROUND(SUM(COALESCE(total,0)),2) t "
                "FROM pr_requests WHERE is_active=1 AND status IN (" + ph + ") "
                "GROUP BY v ORDER BY t DESC", _SPEND_STATUSES).fetchall()
            return (["Vendor", "PRs", "Total"],
                    [[r["v"], r["n"], r["t"]] for r in rows])

        if key == "open_orders":
            rows = conn.execute(
                "SELECT p.pr_no, p.po_no, p.vendor, p.department, p.total, "
                "p.currency, p.payment_status, "
                "COALESCE(SUM(i.received_qty),0) rq, COALESCE(SUM(i.qty),0) oq "
                "FROM pr_requests p LEFT JOIN pr_items i ON i.pr_id = p.id "
                "WHERE p.is_active=1 AND p.status IN (?,?) "
                "GROUP BY p.id ORDER BY p.id",
                ("po_issued", "partially_received")).fetchall()
            return (["PR No", "PO No", "Vendor", "Department", "Total",
                     "Currency", "Received", "Payment Status"],
                    [[r["pr_no"], r["po_no"], r["vendor"], r["department"],
                      r["total"], r["currency"],
                      "{:g}/{:g}".format(r["rq"] or 0, r["oq"] or 0),
                      r["payment_status"]] for r in rows])

        if key == "payments":
            rows = conn.execute(
                "SELECT y.paid_at, p.pr_no, p.vendor, y.amount, y.currency, "
                "y.method, y.reference "
                "FROM pr_payments y JOIN pr_requests p ON p.id = y.pr_id "
                "ORDER BY y.paid_at, y.id").fetchall()
            return (["Paid At", "PR No", "Vendor", "Amount", "Currency",
                     "Method", "Reference"],
                    [[r["paid_at"], r["pr_no"], r["vendor"], r["amount"],
                      r["currency"], r["method"], r["reference"]]
                     for r in rows])
    finally:
        conn.close()
    return (None, None)


def _group_sum(rows, key, val):
    out = {}
    for r in rows:
        k = r.get(key) or "—"
        out[k] = round(out.get(k, 0) + (r.get(val) or 0), 2)
    return out


# --------------------------------------------------------------------------
# auto-PR from a maintenance work order
# --------------------------------------------------------------------------
def ticket_prefill(ticket_id):
    """Return header + item prefill for a new PR seeded from a maintenance ticket."""
    conn = get_db()
    try:
        t = conn.execute("SELECT * FROM mnt_tickets WHERE id=?", (ticket_id,)).fetchone()
    except Exception:
        t = None
    finally:
        conn.close()
    if not t:
        return None
    t = dict(t)
    label = t.get("machine_code") or t.get("ticket_no") or ""
    return {
        "title": f"Repair: {t.get('description') or t.get('ticket_no') or ''}"[:120],
        "request_for": (f"{label} — {t.get('issue_category') or ''}").strip(" —"),
        "department": t.get("department") or "General Maintenance",
        "notes": f"Raised from maintenance ticket {t.get('ticket_no') or ticket_id}.",
        "item": (t.get("issue_category") or "Repair").replace("_", " ").title(),
        "description": t.get("description") or "",
        # mesh: carried as a hidden form field so create() persists the link
        "ticket_id": t.get("id"),
        "ticket_no": t.get("ticket_no"),
    }


# ==========================================================================
# Workflow & Governance — editors + the page model
# ==========================================================================
# Writers are deliberately small and each one audits "who: key old -> new".
# A RESET always DELETES the override (or NULLs the overriding column) — it never
# writes the constant's current value, otherwise a future change to the code
# default would be masked by a frozen copy of today's default.
def _wf_audit(conn, actor, action, detail, ip=None):
    """Governance audit row. pr_id NULL = a module-level change, not a request;
    surfaced by governance_log() on the workflow page."""
    audit(conn, None, actor or "system", action, detail, ip)


def governance_log(limit=25):
    """Recent workflow/governance changes (newest first)."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT actor, action, detail, created_at FROM pr_events "
            "WHERE pr_id IS NULL AND action LIKE ? ORDER BY id DESC LIMIT ?",
            ("wf%", int(limit))).fetchall()]
    finally:
        conn.close()


def set_setting(key, value, user=None, ip=None):
    """Store a module knob. Refuses a value that would be ignored at read time,
    so the admin sees an error instead of a silently unchanged setting.
    Returns (ok, msg)."""
    spec = C.WORKFLOW_SETTINGS.get(key)
    if not spec:
        return False, "unknown_setting"
    raw = str(value if value is not None else "").strip()
    if spec["kind"] == "bool":
        if raw.lower() not in _TRUE_WORDS | _FALSE_WORDS:
            return False, "bad_value"
        raw = "1" if raw.lower() in _TRUE_WORDS else "0"
    else:
        try:
            v = float(raw)
        except (TypeError, ValueError):
            return False, "bad_value"
        if v != v or v in (float("inf"), float("-inf")):
            return False, "bad_value"
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            return False, "out_of_range"
        raw = str(int(v)) if spec["kind"] == "int" else f"{v:g}"
    conn = get_db()
    try:
        old = _raw_setting(conn, key)
        now, uname = _now(), (user or {}).get("username")
        if old is None:
            conn.execute("INSERT INTO proc_settings (key, value, updated_by, updated_at) "
                         "VALUES (?,?,?,?)", (key, raw, uname, now))
        else:
            conn.execute("UPDATE proc_settings SET value=?, updated_by=?, updated_at=? "
                         "WHERE key=?", (raw, uname, now, key))
        _wf_audit(conn, uname, "wf_setting",
                  f"{key}: {old if old is not None else 'default'} -> {raw}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def reset_setting(key, user=None, ip=None):
    """Delete a knob's override so the constant applies again. Returns (ok, msg)."""
    if key not in C.WORKFLOW_SETTINGS:
        return False, "unknown_setting"
    conn = get_db()
    try:
        old = _raw_setting(conn, key)
        conn.execute("DELETE FROM proc_settings WHERE key=?", (key,))
        _wf_audit(conn, (user or {}).get("username"), "wf_setting_reset",
                  f"{key}: {old if old is not None else 'default'} -> default", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def escalation_rows(lang="en"):
    """The escalation chain as the settings editor renders it: one row per role
    that signs somewhere in the ladder (plus any role already configured), with
    its superior resolved and labelled for `lang`."""
    conn = get_db()
    try:
        chain = escalation_map(conn)
        roles_map = stage_roles_map(conn)
    finally:
        conn.close()
    L = _labels(lang if lang in _LANGS else "en")
    keys = set(chain)
    for v in roles_map.values():
        keys |= set(v)
    out = []
    for k in sorted(keys):
        sup = chain.get(k) or ""
        out.append({
            "role_key": k, "label": L["role"].get(k, k),
            "superior": sup, "superior_label": L["role"].get(sup, sup),
            "is_default": sup == (C.DEFAULT_ESCALATION.get(k) or ""),
            "signs": [L["stage"].get(s, s) for s in LADDER if k in roles_map.get(s, set())],
        })
    return out


def role_names(csv, lang="en"):
    """'storekeeper,warehouse_manager' -> the readable role names for `lang`.
    Used for the escalation stamp, whose value is a stored role key list."""
    L = labels(lang)
    return ", ".join(L["role"].get(r, r) for r in sorted(_csv_set(csv)))


def role_choices(lang="en"):
    """[{key, label, can_approve}] for every real platform role, labelled for
    `lang` — the superior dropdown on the settings page. `can_approve` is False for
    a role that may not sign an approval rung at all: picking it as a superior
    would make the escalation land nowhere, so the option is marked (and the
    resolver skips it — see can_sign_role)."""
    L = labels(lang)
    return sorted(({"key": k, "label": L["role"].get(k) or v.get("label") or k,
                    "can_approve": can_sign_role(k)}
                   for k, v in effective_roles().items()),
                  key=lambda r: r["label"].lower())


def set_escalation(role_key, superior_role, user=None, ip=None):
    """Store one rung of the escalation chain. Returns (ok, msg).

    Refuses an unknown role on either side and a role set as its own superior. A
    blank superior is legitimate and means "nobody above" — that is how the top of
    the chart is expressed, and it makes the terminal case explicit rather than
    silently climbing somewhere unexpected."""
    role_key = (role_key or "").strip()
    superior_role = (superior_role or "").strip()
    known = effective_roles()
    if role_key not in known:
        return False, "unknown_role"
    if superior_role and superior_role not in known:
        return False, "unknown_role"
    if superior_role and superior_role == role_key:
        return False, "self_superior"
    conn = get_db()
    try:
        now, uname = _now(), (user or {}).get("username")
        old = conn.execute("SELECT superior_role FROM proc_escalations WHERE role_key=?",
                           (role_key,)).fetchone()
        if old is None:
            conn.execute("INSERT INTO proc_escalations (role_key, superior_role, "
                         "updated_by, updated_at) VALUES (?,?,?,?)",
                         (role_key, superior_role, uname, now))
        else:
            conn.execute("UPDATE proc_escalations SET superior_role=?, updated_by=?, "
                         "updated_at=? WHERE role_key=?",
                         (superior_role, uname, now, role_key))
        _wf_audit(conn, uname, "wf_escalation",
                  f"escalation {role_key}: "
                  f"{(old['superior_role'] if old else None) or 'none'} -> "
                  f"{superior_role or 'none'}", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def _stage_meta_row(conn, stage):
    try:
        row = conn.execute("SELECT * FROM proc_stage_meta WHERE stage=?",
                           (stage,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# --- Trilingual prose -----------------------------------------------------
# Each prose column has two siblings: <col>_ar and <col>_tr (see
# app/approvals/schema.py _PROSE_MIGRATIONS). English stays in <col>.
_LANGS = ("en", "ar", "tr")
_SUFFIX = {"en": "", "ar": "_ar", "tr": "_tr"}


def _labels(lang):
    """Every short LABEL the workflow page names, resolved for `lang`.

    Server-side, not data-i18n: the keys these would need live in
    app/static/i18n/*.json, which this module does not own, and app.js prints a
    key it cannot find RAW on the page ("Warehouse" -> "proc.stage.warehouse")
    in every language, English included. See app/approvals/i18n_text.py LABELS.

    Role labels follow the same rule as an admin-edited explanation: a role an
    admin RENAMED keeps the admin's name in every language; a role still on its
    code-default name is translated."""
    from app.approvals import i18n_text as T
    from app.security import ROLES as _CODE_ROLES

    def pick(m, key, en):
        return (m.get(lang) or {}).get(key) or en

    live = effective_roles()
    roles = {}
    for k, v in live.items():
        label = v.get("label") or k
        default = (_CODE_ROLES.get(k) or {}).get("label")
        roles[k] = label if (default and label != default) else pick(T.ROLE_LABEL, k, label)
    return {
        "stage": {k: pick(T.STAGE_LABEL, k, v) for k, v in C.STAGE_LABELS.items()},
        "gate": {k: pick(T.GATE_LABEL, k, v) for k, v in T.GATE_LABEL["en"].items()},
        "status": {k: pick(T.STATUS_LABEL, k, v) for k, v in T.STATUS_LABEL["en"].items()},
        "setting": {k: pick(T.SETTING_LABEL, k, v) for k, v in T.SETTING_LABEL["en"].items()},
        "setting_warn": {k: pick(T.SETTING_WARN, k, v)
                         for k, v in T.SETTING_WARN["en"].items()},
        "role": roles,
        "ui": dict(T.UI["en"], **(T.UI.get(lang) or {})),
    }


def labels(lang="en"):
    """Public alias for _labels: every short stage / role / status / UI string
    resolved SERVER-SIDE for one reader. Used by the PR page and the settings page
    for text that has no data-i18n key in app/static/i18n (which this module does
    not own) — app.js prints an unknown key raw, in every language."""
    return _labels(lang if lang in _LANGS else "en")


def _prose_columns(col, texts, limit):
    """{column: trimmed text} for the languages actually SUBMITTED.

    A language that was not submitted at all (absent / None) is not written, so
    saving one language can never blank another. A language submitted EMPTY is
    written as '' — not skipped and not NULL — because '' is how "the admin
    cleared this box on purpose" is recorded: readers fall back to the English
    (see _localise) and the boot seed refuses to refill a non-NULL column (see
    schema.seed_translations). Reset is still the way back to the code default."""
    out = {}
    for lang, sfx in _SUFFIX.items():
        v = (texts or {}).get(lang)
        if isinstance(v, str):
            out[col + sfx] = v.strip()[:limit]
    return out


def _localise(row, col, lang, def_en, def_lang):
    """Pick one prose field for `lang`, server-side.

    Order: DB <col>_<lang> -> DB <col> (English) -> code default in <lang> ->
    code default in English -> ''. Blank/whitespace counts as absent, so a panel
    is never rendered empty. Returns (text, is_override), where is_override means
    the text shown is not one of the code defaults, i.e. an admin wrote it."""
    row = row or {}
    def_lang = (def_lang or "").strip()
    def_en = (def_en or "").strip()
    for c in ([col] if lang == "en" else [col + _SUFFIX.get(lang, ""), col]):
        txt = (row.get(c) or "").strip()
        if txt:
            return txt, txt not in (def_lang, def_en)
    return def_lang or def_en, False


def _prose(row, col, key, def_en, defaults, lang):
    """One prose field resolved for `lang`, plus the three editable values.
    `defaults` is a {lang: {key: text}} map from app/approvals/i18n_text.py.

    The editor shows what is STORED. Only a column that is NULL (never written)
    is pre-filled with the code default — a column holding '' was cleared on
    purpose and must render as an empty box, or the next save would silently put
    the shipped translation back."""
    text, custom = _localise(row, col, lang, def_en, (defaults.get(lang) or {}).get(key))
    edit = {}
    for lg in _LANGS:
        stored = (row or {}).get(col + _SUFFIX[lg])
        edit[lg] = (stored.strip() if isinstance(stored, str)
                    else (def_en if lg == "en"
                          else (defaults.get(lg) or {}).get(key) or "") or "")
    return {"text": text, "custom": custom, "edit": edit,
            "updated_by": (row or {}).get("updated_by"),
            "updated_at": (row or {}).get("updated_at")}


def set_stage_meta(stage, roles=None, explanation=None, user=None, ip=None,
                   reset_role=False, reset_explanation=False,
                   explanation_ar=None, explanation_tr=None):
    """Save a stage's signing roles and/or its explanation. `roles` is a list of
    role keys (stored comma-separated). Only role keys that exist on the platform
    are accepted, so an override can never leave a stage unsignable.

    explanation / explanation_ar / explanation_tr are independent: a language not
    submitted at all is left untouched, so editing the Arabic never blanks the
    English or the Turkish. A language submitted EMPTY is stored as '' — the
    admin cleared it on purpose, its readers fall back to the English, and the
    boot seed will not refill it.

    reset_role / reset_explanation NULL the respective column (the "delete the
    override" case); when both end up empty the row is removed entirely.
    Returns (ok, msg)."""
    if stage not in C.STAGE_LABELS:
        return False, "unknown_stage"
    if roles and stage not in LADDER:
        return False, "not_a_signing_stage"     # the requester signs by submitting
    valid = set(effective_roles())
    picked = [r for r in (roles or []) if r in valid]
    if roles and not picked:
        return False, "unknown_role"
    conn = get_db()
    try:
        row = _stage_meta_row(conn, stage)
        old_role = (row or {}).get("role") or "default"
        new_role = None if reset_role else (",".join(sorted(picked)) if picked
                                            else (row or {}).get("role"))
        # Per-language explanation: submitted text wins, blank keeps what is
        # stored, reset clears all three so the code defaults apply again.
        submitted = _prose_columns("explanation", {"en": explanation,
                                                   "ar": explanation_ar,
                                                   "tr": explanation_tr}, 4000)
        expl = {}
        for lang, sfx in _SUFFIX.items():
            col = "explanation" + sfx
            expl[col] = (None if reset_explanation
                         else submitted.get(col, (row or {}).get(col)))
        now, uname = _now(), (user or {}).get("username")
        cols = ["role"] + list(expl)
        vals = [new_role] + [expl[c] for c in expl]
        if row is None:
            names = ", ".join(cols)
            qs = ",".join("?" * len(cols))
            conn.execute(f"INSERT INTO proc_stage_meta (stage, {names}, updated_by, "
                         f"updated_at) VALUES (?,{qs},?,?)",
                         (stage, *vals, uname, now))
        elif new_role is None and all(v is None for v in expl.values()):
            # Nothing stored at all (reset NULLs every column) -> drop the row and
            # let the code defaults apply. A column holding '' is a deliberate
            # clear, NOT an empty row, so it must survive.
            conn.execute("DELETE FROM proc_stage_meta WHERE stage=?", (stage,))
        else:
            sets = ", ".join(f"{c}=?" for c in cols)
            conn.execute(f"UPDATE proc_stage_meta SET {sets}, updated_by=?, updated_at=? "
                         f"WHERE stage=?", (*vals, uname, now, stage))
        detail = f"stage {stage} role: {old_role} -> {new_role or 'default'}"
        changed = [lg for lg, sfx in _SUFFIX.items()
                   if ((row or {}).get("explanation" + sfx) or "")
                   != (expl["explanation" + sfx] or "")]
        if changed:
            detail += ("; explanation reset to default" if reset_explanation
                       else "; explanation edited (%s)" % ", ".join(sorted(changed)))
        _wf_audit(conn, uname, "wf_stage", detail, ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def set_role_meta(role_key, explanation=None, user=None, ip=None, reset=False,
                  explanation_ar=None, explanation_tr=None):
    """Save (or reset) what a role is responsible for, per language. Only the
    languages actually submitted are written; the others keep what they hold. A
    language submitted empty is stored as '' (cleared on purpose -> its readers
    see the English, and the boot seed leaves it alone). Returns (ok, msg)."""
    role_key = (role_key or "").strip()
    if not role_key or role_key not in set(effective_roles()) | set(C.ROLE_EXPLAIN):
        return False, "unknown_role"
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM proc_role_meta WHERE role_key=?",
                           (role_key,)).fetchone()
        now, uname = _now(), (user or {}).get("username")
        if reset:
            conn.execute("DELETE FROM proc_role_meta WHERE role_key=?", (role_key,))
            _wf_audit(conn, uname, "wf_role_reset", f"role {role_key}: -> default text", ip)
        else:
            cols = _prose_columns("explanation", {"en": explanation,
                                                  "ar": explanation_ar,
                                                  "tr": explanation_tr}, 4000)
            if not cols:
                return False, "empty"
            if row:
                sets = ", ".join(f"{c}=?" for c in cols)
                conn.execute(f"UPDATE proc_role_meta SET {sets}, updated_by=?, updated_at=? "
                             f"WHERE role_key=?", (*cols.values(), uname, now, role_key))
            else:
                names = ", ".join(cols)
                qs = ",".join("?" * len(cols))
                conn.execute(f"INSERT INTO proc_role_meta (role_key, {names}, updated_by, "
                             f"updated_at) VALUES (?,{qs},?,?)",
                             (role_key, *cols.values(), uname, now))
            _wf_audit(conn, uname, "wf_role",
                      f"role {role_key}: explanation edited ({', '.join(sorted(cols))})", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


def _doc_default(section):
    """Code default for a documentation block: a gate/overview body, or a PR
    status meaning ('status.<key>')."""
    if section.startswith("status."):
        return C.STATUS_MEANING.get(section[7:])
    return C.DOC_SECTIONS.get(section)


def set_doc(section, body=None, user=None, ip=None, reset=False,
            body_ar=None, body_tr=None):
    """Save (or reset) one free-text block, per language. Only the languages
    actually submitted are written; the others keep what they hold. A language
    submitted empty is stored as '' (cleared on purpose -> its readers see the
    English, and the boot seed leaves it alone). Returns (ok, msg)."""
    section = (section or "").strip()
    if _doc_default(section) is None:
        return False, "unknown_section"      # only the known blocks are editable
    conn = get_db()
    try:
        now, uname = _now(), (user or {}).get("username")
        if reset:
            conn.execute("DELETE FROM proc_doc WHERE section=?", (section,))
            _wf_audit(conn, uname, "wf_doc_reset", f"doc {section}: -> default text", ip)
        else:
            cols = _prose_columns("body", {"en": body, "ar": body_ar,
                                           "tr": body_tr}, 8000)
            if not cols:
                return False, "empty"
            row = conn.execute("SELECT id FROM proc_doc WHERE section=?", (section,)).fetchone()
            if row:
                sets = ", ".join(f"{c}=?" for c in cols)
                conn.execute(f"UPDATE proc_doc SET {sets}, updated_by=?, updated_at=? "
                             f"WHERE section=?", (*cols.values(), uname, now, section))
            else:
                names = ", ".join(cols)
                qs = ",".join("?" * len(cols))
                conn.execute(f"INSERT INTO proc_doc ({names}, section, updated_by, updated_at) "
                             f"VALUES ({qs},?,?,?)", (*cols.values(), section, uname, now))
            _wf_audit(conn, uname, "wf_doc",
                      f"doc {section}: edited ({', '.join(sorted(cols))})", ip)
        conn.commit()
        return True, ""
    finally:
        conn.close()


# --- the page model -------------------------------------------------------
def _effective_matrix(department):
    """(ordered [(stage, threshold)], source) actually applied to `department`.
    A department with its own responsibility matrix uses it; everyone else uses
    the global default from constants.APPROVAL_MATRIX."""
    if department in all_dept_matrices():
        m = get_dept_matrix(department)
        rows = sorted(((s, v) for s, v in m.items() if v["included"]),
                      key=lambda kv: kv[1]["seq"])
        return [(s, v["threshold"]) for s, v in rows], "department"
    # ACTIVE_MATRIX, not APPROVAL_MATRIX: the default thresholds shown to an
    # admin must be the ones the request will actually route on.
    return [(s, float(C.ACTIVE_MATRIX.get(s, 0))) for s in LADDER], "default"


def _stage_gates(stage, is_last):
    """Which gates fire at a stage — derived, so it cannot drift from the engine."""
    gates = ["sod"]                                    # every signature
    if stage == PRICING_GATE_STAGE:
        gates = ["pricing_gate", "rfq"] + gates        # act_on_step checks both here
    if is_last:
        gates.append("budget_gate")                    # PO issue follows this signature
    return gates


def workflow_view(department=None, lang="en"):
    """Everything the Workflow & Governance page renders, read from the database
    so the page can never drift from the engine that enforces it.

    `lang` ('en' | 'ar' | 'tr', normally the reader's users.lang_pref) selects the
    prose column SERVER-SIDE — database text cannot use the client-side data-i18n
    swap. See _localise for the exact fallback order."""
    lang = lang if lang in _LANGS else "en"
    depts = list_departments()
    department = department or (depts[0] if depts else "")
    matrix, thr_source = _effective_matrix(department)
    conn = get_db()
    try:
        # SELECT * so a database whose _ar/_tr ALTER has not run yet still reads.
        smeta = {r["stage"]: dict(r) for r in conn.execute(
            "SELECT * FROM proc_stage_meta").fetchall()}
        rmeta = {r["role_key"]: dict(r) for r in conn.execute(
            "SELECT * FROM proc_role_meta").fetchall()}
        docs = {r["section"]: dict(r) for r in conn.execute(
            "SELECT * FROM proc_doc").fetchall()}
        knob_rows = {r["key"]: dict(r) for r in conn.execute(
            "SELECT key, value, updated_by, updated_at FROM proc_settings").fetchall()}
        roles_map = stage_roles_map(conn)
        knobs = []
        for key, spec in C.WORKFLOW_SETTINGS.items():
            row = knob_rows.get(key) or {}
            eff = (bool_setting(conn, key) if spec["kind"] == "bool"
                   else num_setting(conn, key))
            stored = row.get("value")
            knobs.append({
                "key": key, "kind": spec["kind"], "default": spec["default"],
                "stored": stored, "effective": eff,
                "is_override": stored is not None,
                # a stored value the coercion rejected: shown so nobody has to
                # wonder why their edit "did nothing"
                "ignored": stored is not None and not _same_value(spec, stored, eff),
                "min": spec.get("min"), "max": spec.get("max"),
                "updated_by": row.get("updated_by"), "updated_at": row.get("updated_at"),
            })
    finally:
        conn.close()

    all_roles = effective_roles()

    from app.approvals import i18n_text as T

    L = _labels(lang)            # short labels, resolved server-side (see _labels)

    stages = []
    for i, (stage, thr) in enumerate(matrix, start=1):
        roles = sorted(roles_map.get(stage, set()))
        default_roles = sorted(STAGE_ROLES.get(stage, set()))
        p = _prose(smeta.get(stage), "explanation", stage,
                   C.STAGE_EXPLAIN.get(stage), T.STAGE, lang)
        txt, custom, by, at = p["text"], p["custom"], p["updated_by"], p["updated_at"]
        stages.append({
            "seq": i, "stage": stage, "label": L["stage"].get(stage) or stage_label(stage),
            "roles": roles, "default_roles": default_roles,
            "role_override": roles != default_roles,
            # a role without proc_approve could only be signed by an admin — worth
            # showing rather than silently producing a stuck request
            "roles_cannot_approve": [r for r in roles
                                     if not has_permission(r, "proc_approve")],
            "threshold": thr, "always": thr <= 0,
            "gates": _stage_gates(stage, i == len(matrix)),
            "explanation": txt, "explanation_custom": custom,
            "edit": p["edit"],
            "updated_by": by, "updated_at": at,
        })

    role_keys = set(C.ROLE_EXPLAIN)          # documented even if no stage points at one
    for v in roles_map.values():
        role_keys |= set(v)
    roles = []
    for k in sorted(role_keys):
        p = _prose(rmeta.get(k), "explanation", k, C.ROLE_EXPLAIN.get(k), T.ROLE, lang)
        perms = list((all_roles.get(k) or {}).get("perms") or [])
        roles.append({
            "key": k, "label": L["role"].get(k) or (all_roles.get(k) or {}).get("label", k),
            "exists": k in all_roles,
            "perms": ["*"] if "*" in perms else [p2 for p2 in perms if p2.startswith("proc_")],
            "can_approve": has_permission(k, "proc_approve"),
            "signs": [s for s in LADDER if k in roles_map.get(s, set())],
            "explanation": p["text"], "explanation_custom": p["custom"],
            "edit": p["edit"],
            "updated_by": p["updated_by"], "updated_at": p["updated_at"],
        })

    def _doc(section):
        p = _prose(docs.get(section), "body", section, _doc_default(section), T.DOC, lang)
        return {"section": section, "body": p["text"], "custom": p["custom"],
                "edit": p["edit"], "updated_by": p["updated_by"],
                "updated_at": p["updated_at"]}

    return {
        "lang": lang,
        "department": department, "departments": depts,
        "threshold_source": thr_source,
        "stages": stages, "roles": roles, "knobs": knobs,
        "overview": _doc("overview"),
        "gates": [_doc(s) for s in ("pricing_gate", "rfq", "sod",
                                    "budget_gate", "three_way_match", "payment_cap")],
        "statuses": [dict(_doc("status." + s), key=s) for s in PR_STATUSES],
        # SoD escalation: the prose plus the chain exactly as configured, so the
        # page documents the rule AND the live wiring rather than a drawing.
        "escalation": dict(_doc("sod_escalation"), chain=escalation_rows(lang)),
        "role_choices": sorted(
            ({"key": k, "label": L["role"].get(k) or v["label"],
              "can_approve": has_permission(k, "proc_approve")}
             for k, v in all_roles.items()), key=lambda r: r["label"].lower()),
        # Every short label already resolved for this reader's language.
        "L": L,
        "sla_hours": C.SLA_HOURS_PER_STAGE, "sla_warn": C.SLA_WARN_HOURS,
        # What three_way_match actually applies, so the governance page never
        # states a tolerance the code does not use.
        "match_tolerance_pct": C.MATCH_TOLERANCE_PCT if C.DOAM_IN_FORCE else 1.0,
        "match_tolerance_abs": C.MATCH_TOLERANCE_ABS if C.DOAM_IN_FORCE else 1.0,
        "match_qty_tolerance_pct": C.MATCH_QTY_TOLERANCE_PCT if C.DOAM_IN_FORCE else 0.0,
        "log": governance_log(),
    }


def _same_value(spec, stored, effective):
    """Did the stored override survive coercion? Used only to badge a rejected
    value in the UI, never to decide behaviour."""
    if spec["kind"] == "bool":
        return (str(stored).strip().lower() in _TRUE_WORDS) == bool(effective)
    try:
        return float(str(stored).strip()) == float(effective)
    except (TypeError, ValueError):
        return False


def visible_fields(conn=None):
    """Which optional request fields are switched on, as {key: bool}.

    One read for the whole set, so a page renders from a single snapshot rather
    than asking the database once per field. Absent rows mean "as it always
    was": every field defaults to visible.
    """
    own = conn is None
    conn = conn or get_db()
    try:
        return {key: bool_setting(conn, key) for key, _l, _w in C.OPTIONAL_FIELDS}
    finally:
        if own:
            conn.close()
