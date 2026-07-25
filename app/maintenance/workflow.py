"""
TC Platform — Maintenance "Workflow & Governance".

Admin-editable explanation text plus the handful of operational knobs the
maintenance workflow actually reads. Nothing here invents new behaviour: every
accessor is  DB override -> the constant the code used before.  With no override
rows the numbers returned are exactly the ones services.py / procure_bridge.py
read until now, so the ticket lifecycle, the spare-request ladder, the stock
reservation gates and the auto-reorder bridge behave identically.

A stored value that is blank, non-numeric, NaN/inf, out of range, an unknown role
or a role that cannot sign is treated as ABSENT and the constant wins -- a bad
edit can never empty an approval rung, hand it to somebody who cannot sign, or
silently disable a gate.

Tables use `id INTEGER PRIMARY KEY AUTOINCREMENT` + a UNIQUE natural key rather
than a bare TEXT primary key, because app/db.py's PostgreSQL shim appends
"RETURNING id" to every INSERT for tables outside its own _NO_ID_TABLES set
(db.py is not ours to edit). Uniqueness -- and therefore INSERT OR IGNORE -- is
unchanged.
"""
import math

from app.maintenance.constants import (MAINT_ROLE_LABELS, MAINT_ROLE_PERMS,
                                       TICKET_STATUSES, TICKET_TRANSITIONS)

# --- defaults that used to be literals in the workflow code -----------------
# SLA target scales with priority (critical is far tighter than the base hours).
SLA_FACTOR = {"critical": 0.25, "high": 0.5, "medium": 1.0, "low": 2.0}
# Request value at/above which _pick_matrix_levels switched to the longer ladder.
CRITICAL_COST_DEFAULT = 100.0
# _pick_matrix_levels' own fallback when no matrix rule can be read.
DEFAULT_LEVELS = "maintenance_manager,storekeeper"
# The storekeeper ISSUES approved parts; they were never an approval vote, and
# making them one would both break segregation of duties and (because the picker
# strips them from the levels string) silently delete the rung.
ISSUER_ROLE = "storekeeper"

# key -> (default, kind, min, max). Bounds reject nonsense on read AND on write.
SETTINGS = {
    "response_sla_hours":      (4.0, "num", 0.01, 87600.0),
    "resolution_sla_hours":    (24.0, "num", 0.01, 87600.0),
    "sla_factor_critical":     (SLA_FACTOR["critical"], "num", 0.001, 100.0),
    "sla_factor_high":         (SLA_FACTOR["high"], "num", 0.001, 100.0),
    "sla_factor_medium":       (SLA_FACTOR["medium"], "num", 0.001, 100.0),
    "sla_factor_low":          (SLA_FACTOR["low"], "num", 0.001, 100.0),
    "critical_cost_threshold": (CRITICAL_COST_DEFAULT, "num", 0.0, 1e9),
    "auto_reorder_pr":         (True, "bool", None, None),
    "duplicate_ticket_guard":  (True, "bool", None, None),
}

# Where each knob's default comes from, and who reads it (shown on the page).
SETTING_SOURCE = {
    "response_sla_hours": ("workflow.SETTINGS (4h)", "services.create_ticket"),
    "resolution_sla_hours": ("workflow.SETTINGS (24h)", "services.create_ticket, ai.py"),
    "sla_factor_critical": ("workflow.SLA_FACTOR", "services.create_ticket"),
    "sla_factor_high": ("workflow.SLA_FACTOR", "services.create_ticket"),
    "sla_factor_medium": ("workflow.SLA_FACTOR", "services.create_ticket"),
    "sla_factor_low": ("workflow.SLA_FACTOR", "services.create_ticket"),
    "critical_cost_threshold": ("mnt_approval_matrix.cost_threshold, else 100",
                                "services._pick_matrix_levels"),
    "auto_reorder_pr": ("on", "procure_bridge._enabled"),
    "duplicate_ticket_guard": ("on", "services.create_ticket"),
}

RULE_KEYS = ("std", "crit")

WF_SCHEMA = """
CREATE TABLE IF NOT EXISTS mnt_stage_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT UNIQUE NOT NULL,
    role TEXT, explanation TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mnt_role_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key TEXT UNIQUE NOT NULL,
    explanation TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mnt_status_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT UNIQUE NOT NULL,
    explanation TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mnt_doc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT UNIQUE NOT NULL,
    body TEXT, updated_by TEXT, updated_at TEXT
);
"""

# --- seeded default text (read off the code; admins may rewrite any of it) ---
STATUS_TEXT = {
    "draft": "Saved but not submitted. Nobody is notified and the SLA breach sweep "
             "skips drafts.",
    "submitted": "Raised; the maintenance manager was notified. The response and "
                 "resolution SLA clocks started at creation, scaled by priority.",
    "under_review": "The maintenance manager is triaging: confirm the fault, set the "
                    "priority, decide who repairs it. Stamps reviewed_at.",
    "assigned": "A technician and a work-order number are attached. Stamps assigned_at.",
    "diagnosis": "The technician is inspecting the machine and recording fault, root "
                 "cause and required action. Stamps diag_started_at.",
    "spare_required": "Diagnosis says a spare part is needed; the next step is a "
                      "spare-part request.",
    "waiting_stock": "At least one requested part is not available (on hand minus "
                     "reserved), so nothing can be approved for issue until stock "
                     "arrives. Storekeeper and maintenance manager were notified.",
    "waiting_approval": "A spare-part request is on the approval ladder. Stamps "
                        "approval_started_at.",
    "approved_issue": "Every rung signed and the parts are RESERVED for this ticket, "
                      "waiting for the storekeeper to issue them. Stamps approval_done_at.",
    "rejected": "The ticket or its parts request was refused with a written reason; any "
                "reservation this ticket held was released.",
    "parts_issued": "The storekeeper issued the parts: stock decreased, an issue voucher "
                    "was written, the reservation released and the parts value added to "
                    "the ticket cost. Stamps issued_at.",
    "repair": "The technician is carrying out the repair. Stamps repair_started_at.",
    "testing": "Repair done; the machine is being tested and handed back to production. "
               "Stamps repair_done_at.",
    "resolved": "Tested and working, awaiting the maintenance manager's closure. Stamps "
                "testing_done_at; the SLA breach sweep stops here.",
    "closed": "Closed by the maintenance manager. Downtime is measured from creation to "
              "closure; the machine returns to running; cost, downtime and the breakdown "
              "count roll into the machine ONCE (machine_rolled). Approved-but-unissued "
              "reservations are released.",
    "cancelled": "Abandoned. Terminal - nothing can follow it. Reservations released.",
    "reopened": "The fault came back after closure. The machine goes back to "
                "under_maintenance and the cost/downtime roll-up is NOT repeated. "
                "Stamps reopened_at.",
}

ROLE_TEXT = {
    "maintenance_manager": "Reviews and assigns tickets, closes resolved work, owns "
                           "preventive maintenance, and signs the first rung of the "
                           "spare-request ladder.",
    "maintenance_technician": "Diagnoses the fault, requests spare parts, records the "
                             "repair proof and the test result.",
    "storekeeper": "Runs the spare-parts store: issues approved parts, receives stock, "
                   "adjusts stock. Deliberately the ISSUER and not an approval rung.",
    "production_supervisor": "Raises tickets from the floor and takes the machine back "
                             "after testing.",
    "factory_manager": "Signs the extra rung on critical or high-value spare requests.",
    "production_manager": "Raises tickets from production and can sign approvals.",
    "it_director": "Administers the module: settings, master data, approval matrix.",
    "it_manager": "Administers module settings.",
    "finance_user": "Read-only view of maintenance cost and history.",
    "executive_viewer": "Read-only view of the maintenance dashboards.",
}

STAGE_TEXT = {
    "std_l1": "Level 1 of a normal spare request: the maintenance manager confirms the "
              "part and the quantity are really needed for this ticket. Only the role "
              "that owns this rung may sign it.",
    "crit_l1": "Level 1 of a critical or high-value request: the maintenance manager "
               "confirms the part and the quantity.",
    "crit_l2": "Level 2 of a critical or high-value request: the factory manager "
               "authorises the spend. The request is approved only once EVERY rung has "
               "signed.",
}

DOC_SECTIONS = ("lifecycle", "reservation", "auto_reorder", "sla", "costing")
DOC_TEXT = {
    "lifecycle": "A ticket walks the fixed lifecycle below. Each move is checked against "
                 "the allowed transitions, is written to the audit trail with who and "
                 "when, and stamps its own timestamp column. Only a spare-part need "
                 "diverts the ticket into the approval ladder; everything else goes "
                 "diagnosis -> repair -> testing -> resolved -> closed.",
    "reservation": "available = on hand - reserved. Approving a request reserves the "
                   "parts atomically (one conditional UPDATE per line that only succeeds "
                   "while the part is still available), so two approvals can never claim "
                   "the same physical unit. If any line cannot be reserved the whole "
                   "batch is rolled back to out-of-stock. A reservation is released on "
                   "issue, on rejection and on ticket close, so stock is never stranded.",
    "auto_reorder": "A low spare raises an unpriced purchase requisition automatically "
                    "when auto_reorder_pr is on: when available stock falls to or below "
                    "the reorder level, one PR per spare is sent to Procurement's pricing "
                    "gate and the normal procurement ladder. It is deduplicated against "
                    "any open PR for that spare, and a goods receipt on that PR posts "
                    "straight back into spare stock.",
    "sla": "The response and resolution clocks start when the ticket is created, not at "
           "assignment. Base hours come from the settings below and are multiplied by "
           "the priority factor (critical is the tightest). Any open ticket past its "
           "resolution due time is flagged sla_breach once and the maintenance manager "
           "is notified.",
    "costing": "Receiving stock moves the part's cost by weighted average: "
               "(old_qty x old_avg + received_qty x price) / (old_qty + received_qty). "
               "Issuing parts charges qty x avg_cost to the ticket, and closing the "
               "ticket rolls that cost into the machine's cost-to-date exactly once. "
               "Part cost therefore drives both approval routing and cost reporting.",
}


# --- schema ----------------------------------------------------------------
def ensure(conn):
    """Create the workflow tables and seed the default text. Idempotent:
    INSERT OR IGNORE never overwrites text an admin has since edited."""
    try:
        conn.executescript(WF_SCHEMA)
        conn.commit()
    except Exception:
        conn.rollback()
        return
    for table, keycol, valcol, data in (
            ("mnt_status_meta", "status", "explanation", STATUS_TEXT),
            ("mnt_role_meta", "role_key", "explanation", ROLE_TEXT),
            ("mnt_stage_meta", "stage", "explanation", STAGE_TEXT),
            ("mnt_doc", "section", "body", DOC_TEXT)):
        for k, v in data.items():
            try:
                conn.execute("INSERT OR IGNORE INTO %s (%s,%s) VALUES (?,?)"
                             % (table, keycol, valcol), (k, v))
            except Exception:
                conn.rollback()
    conn.commit()


# --- setting accessors (override -> constant) ------------------------------
def _raw(conn, key):
    """Stored override or None. Blank counts as absent."""
    try:
        r = conn.execute("SELECT value FROM mnt_settings WHERE key=?", (key,)).fetchone()
    except Exception:
        return None                      # table not ready -> constants only
    v = r["value"] if r else None
    return v if (v is not None and str(v).strip() != "") else None


def num_override(conn, key):
    """Valid numeric override or None. Rejects blank, non-numeric, NaN/inf and
    out-of-range values so a bad row can never poison a live calculation."""
    spec = SETTINGS.get(key)
    lo = spec[2] if spec and spec[2] is not None else 0.0
    hi = spec[3] if spec and spec[3] is not None else 1e9
    raw = _raw(conn, key)
    if raw is None:
        return None
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < lo or v > hi:
        return None
    return v


def num(conn, key):
    v = num_override(conn, key)
    return SETTINGS[key][0] if v is None else v


def flag(conn, key):
    """Boolean knob. Anything the UI did not write falls back to the default."""
    raw = _raw(conn, key)
    if raw is None:
        return bool(SETTINGS[key][0])
    s = str(raw).strip().lower()
    if s in ("0", "false", "no", "off"):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return bool(SETTINGS[key][0])


def sla_hours(conn, priority, base_default, key):
    """Base SLA hours x the priority factor. `base_default` keeps the caller's own
    literal as the last-resort fallback."""
    v = num_override(conn, key)
    base = base_default if v is None else v
    return base * sla_factor(conn, priority)


def sla_factor(conn, priority):
    """Priority multiplier. An unknown priority keeps the 1.0 fallback the code
    used before, without touching the database."""
    key = "sla_factor_%s" % (priority or "")
    if key not in SETTINGS:
        return SLA_FACTOR.get(priority, 1.0)
    return num(conn, key)


def dup_guard_on(conn):
    """Duplicate-open-ticket guard. Default ON == the previous unconditional check."""
    return flag(conn, "duplicate_ticket_guard")


def critical_default(conn):
    """The threshold with NO settings override: the critical matrix rule's own
    cost_threshold, else the 100 that _pick_matrix_levels hardcoded."""
    try:
        row = conn.execute("SELECT cost_threshold FROM mnt_approval_matrix WHERE active=1 "
                           "AND part_criticality='critical' LIMIT 1").fetchone()
        m = float(row["cost_threshold"]) if row and row["cost_threshold"] is not None else None
    except Exception:
        m = None
    if m is not None and math.isfinite(m) and m >= 0:
        return m
    return CRITICAL_COST_DEFAULT


def critical_threshold(conn):
    """Request value at/above which the critical ladder is used."""
    v = num_override(conn, "critical_cost_threshold")
    return critical_default(conn) if v is None else v


# --- approval ladder -------------------------------------------------------
def rules(conn):
    """The two matrix rules the picker chooses between, selected exactly as
    services._pick_matrix_levels selects them."""
    crit = conn.execute("SELECT * FROM mnt_approval_matrix WHERE active=1 AND "
                        "part_criticality='critical' LIMIT 1").fetchone()
    std = conn.execute("SELECT * FROM mnt_approval_matrix WHERE active=1 "
                       "ORDER BY id LIMIT 1").fetchone()
    return {"std": std, "crit": crit}


def default_approvers(rule):
    """Approval rungs a matrix rule defines. The issuer is filtered out here, same
    as before, because issuing is not a vote."""
    levels = (rule["levels"] if rule and rule["levels"] else DEFAULT_LEVELS).split(",")
    return [r.strip() for r in levels if r.strip() and r.strip() != ISSUER_ROLE]


def stage_key(rule_key, level):
    return "%s_l%d" % (rule_key, level)


def _can_approve(role):
    """A stage override must name a real role that can actually sign, and must not
    be the issuer -- otherwise the rung would be unsignable (locked) or deleted."""
    role = (role or "").strip()
    if not role or role == ISSUER_ROLE:
        return False
    try:
        from app.security import effective_roles, has_permission
        return role in effective_roles() and has_permission(role, "maint_approve")
    except Exception:
        # RBAC layer unavailable (tooling): fall back to the module's own grants.
        return "maint_approve" in MAINT_ROLE_PERMS.get(role, [])


def stage_rows(conn):
    """{stage: {"role":..., "explanation":...}} — raw, no validation."""
    out = {}
    try:
        for r in conn.execute("SELECT stage, role, explanation FROM mnt_stage_meta").fetchall():
            out[r["stage"]] = {"role": r["role"], "explanation": r["explanation"]}
    except Exception:
        pass
    return out


def stage_role_override(conn, stage, rows=None):
    r = (rows if rows is not None else stage_rows(conn)).get(stage) or {}
    role = (r.get("role") or "").strip()
    return role if _can_approve(role) else None


def approver_roles(conn, rule_key, rule):
    """Effective rungs: the matrix default per level, replaced by a VALID
    stage->role override. An invalid override is ignored, so the rung keeps its
    default signer instead of becoming unsignable."""
    rows = stage_rows(conn)
    return [stage_role_override(conn, stage_key(rule_key, i), rows) or role
            for i, role in enumerate(default_approvers(rule), start=1)]


# --- text accessors --------------------------------------------------------
def _text_map(conn, table, keycol, valcol):
    out = {}
    try:
        for r in conn.execute("SELECT %s k, %s v FROM %s" % (keycol, valcol, table)).fetchall():
            out[r["k"]] = r["v"]
    except Exception:
        pass
    return out


def texts(conn, kind):
    t = _TEXT_TABLES[kind]
    return _text_map(conn, t[0], t[1], t[2])


# kind -> (table, key column, value column, seeded defaults)
_TEXT_TABLES = {
    "status": ("mnt_status_meta", "status", "explanation", STATUS_TEXT),
    "role": ("mnt_role_meta", "role_key", "explanation", ROLE_TEXT),
    "stage": ("mnt_stage_meta", "stage", "explanation", STAGE_TEXT),
    "doc": ("mnt_doc", "section", "body", DOC_TEXT),
}


# --- writes (audited) ------------------------------------------------------
def _write_audit(conn, user, action, entity_type, old, new, comment):
    from app.maintenance.services import audit          # lazy: avoid import cycle
    audit(conn, user, action, entity_type, 0, old, new, comment)


def set_setting(conn, key, value, user):
    """Store a knob override. Returns (ok, msg). Rejects a value that would not
    survive the read-side coercion, so the UI can say so instead of storing junk."""
    if key not in SETTINGS:
        return False, "unknown_setting"
    kind, lo, hi = SETTINGS[key][1], SETTINGS[key][2], SETTINGS[key][3]
    if kind == "bool":
        store = "1" if str(value).strip().lower() in ("1", "true", "yes", "on") else "0"
    else:
        try:
            v = float(str(value).strip())
        except (TypeError, ValueError):
            return False, "not_a_number"
        if not math.isfinite(v) or v < lo or v > hi:
            return False, "out_of_range"
        store = ("%g" % v)
    old = _raw(conn, key)
    conn.execute("INSERT OR IGNORE INTO mnt_settings (key,value) VALUES (?,?)", (key, store))
    conn.execute("UPDATE mnt_settings SET value=? WHERE key=?", (store, key))
    _write_audit(conn, user, "wf_setting_set", "mnt_setting", old, store, key)
    conn.commit()
    return True, ""


def reset_setting(conn, key, user):
    """Delete the override row so the constant default applies again."""
    if key not in SETTINGS:
        return False, "unknown_setting"
    old = _raw(conn, key)
    conn.execute("DELETE FROM mnt_settings WHERE key=?", (key,))
    _write_audit(conn, user, "wf_setting_reset", "mnt_setting", old, None, key)
    conn.commit()
    return True, ""


def set_text(conn, kind, key, explanation, user, role=None):
    """Upsert an explanation (and, for a stage, its signing role). Returns (ok,msg)."""
    t = _TEXT_TABLES.get(kind)
    if not t or not (key or "").strip():
        return False, "unknown_target"
    table, keycol, valcol, _ = t
    key = key.strip()
    role = (role or "").strip()
    # Validate BEFORE writing anything: a rung must never end up unsignable.
    if kind == "stage" and role and not _can_approve(role):
        return False, "role_cannot_sign"
    old = _text_map(conn, table, keycol, valcol).get(key)
    conn.execute("INSERT OR IGNORE INTO %s (%s) VALUES (?)" % (table, keycol), (key,))
    conn.execute("UPDATE %s SET %s=?, updated_by=?, updated_at=? WHERE %s=?"
                 % (table, valcol, keycol),
                 (explanation, (user or {}).get("username"), _now(), key))
    if kind == "stage":
        conn.execute("UPDATE mnt_stage_meta SET role=? WHERE stage=?", (role or None, key))
    _write_audit(conn, user, "wf_text_set", "mnt_%s" % kind, old, explanation, key)
    conn.commit()
    return True, ""


def reset_text(conn, kind, key, user):
    """Delete the row: the seeded default text comes back on the next boot seed,
    and a stage's role override disappears immediately."""
    t = _TEXT_TABLES.get(kind)
    if not t:
        return False, "unknown_target"
    table, keycol, valcol, defaults = t
    old = _text_map(conn, table, keycol, valcol).get(key)
    conn.execute("DELETE FROM %s WHERE %s=?" % (table, keycol), (key,))
    # Put the shipped default straight back so the page never shows a blank
    # explanation just because an admin pressed reset.
    if key in defaults:
        conn.execute("INSERT OR IGNORE INTO %s (%s,%s) VALUES (?,?)"
                     % (table, keycol, valcol), (key, defaults[key]))
    _write_audit(conn, user, "wf_text_reset", "mnt_%s" % kind, old, None, key)
    conn.commit()
    return True, ""


def _now():
    from app.maintenance.services import _now as n      # lazy: avoid import cycle
    return n()


# --- page ------------------------------------------------------------------
def page_data(conn):
    """Everything /maintenance/workflow renders, resolved override -> default."""
    stage_meta = stage_rows(conn)
    rule_rows = rules(conn)
    ladder = []
    for rk in RULE_KEYS:
        rule = rule_rows.get(rk)
        for i, default_role in enumerate(default_approvers(rule), start=1):
            sk = stage_key(rk, i)
            meta = stage_meta.get(sk) or {}
            eff = stage_role_override(conn, sk, stage_meta)
            ladder.append({
                "stage": sk, "rule": rk, "level": i,
                "rule_name": (rule["name"] if rule else "default"),
                "default_role": default_role,
                "role": eff or default_role,
                "stored_role": meta.get("role") or "",
                "overridden": bool(eff) and eff != default_role,
                "invalid": bool((meta.get("role") or "").strip()) and not eff,
                "explanation": meta.get("explanation") or "",
            })
        # the issuer rung is documented, never an approval vote
        ladder.append({
            "stage": "", "rule": rk, "level": len(default_approvers(rule)) + 1,
            "rule_name": (rule["name"] if rule else "default"),
            "default_role": ISSUER_ROLE, "role": ISSUER_ROLE, "stored_role": "",
            "overridden": False, "invalid": False, "issuer": True,
            "explanation": texts(conn, "role").get(ISSUER_ROLE) or "",
        })

    st_text = texts(conn, "status")
    statuses = [{
        "status": s,
        "next": sorted(TICKET_TRANSITIONS.get(s, set())),
        "terminal": not TICKET_TRANSITIONS.get(s),
        "explanation": st_text.get(s) or "",
        "overridden": s in st_text and (st_text.get(s) or "") != STATUS_TEXT.get(s, ""),
    } for s in TICKET_STATUSES]

    role_text = texts(conn, "role")
    try:
        from app.security import effective_roles, role_label
        eff_roles = effective_roles()
    except Exception:
        eff_roles, role_label = {}, (lambda r: r)
    roles_out = []
    for rk in MAINT_ROLE_PERMS:
        perms = [p for p in (eff_roles.get(rk, {}).get("perms") or MAINT_ROLE_PERMS[rk])
                 if p.startswith("maint_") or p == "*"]
        roles_out.append({
            "role_key": rk,
            "label": MAINT_ROLE_LABELS.get(rk) or role_label(rk),
            "perms": perms,
            "explanation": role_text.get(rk) or "",
            "overridden": rk in role_text and (role_text.get(rk) or "") != ROLE_TEXT.get(rk, ""),
            "can_approve": _can_approve(rk),
        })

    doc_text = texts(conn, "doc")
    docs = {s: {"body": doc_text.get(s) or "",
                "overridden": s in doc_text and (doc_text.get(s) or "") != DOC_TEXT.get(s, "")}
            for s in DOC_SECTIONS}

    settings_out = []
    for key, (default, kind, _lo, _hi) in SETTINGS.items():
        eff = flag(conn, key) if kind == "bool" else num(conn, key)
        if key == "critical_cost_threshold":
            # this one's default is itself data (the matrix rule), not a literal
            default, eff = critical_default(conn), critical_threshold(conn)
        src, reader = SETTING_SOURCE.get(key, ("code", ""))
        settings_out.append({
            "key": key, "kind": kind, "default": default, "value": eff,
            "raw": _raw(conn, key), "overridden": eff != default,
            "source": src, "reader": reader,
        })

    # Effective ladder for a worked example, so the page proves what it claims.
    return {
        "settings": settings_out, "ladder": ladder, "statuses": statuses,
        "roles": roles_out, "docs": docs,
        "threshold": critical_threshold(conn),
        "approver_choices": sorted(r["role_key"] for r in roles_out if r["can_approve"]
                                   and r["role_key"] != ISSUER_ROLE),
        "counts": {
            "overrides": sum(1 for s in settings_out if s["overridden"])
                         + sum(1 for l in ladder if l["overridden"]),
            "rungs": sum(1 for l in ladder if not l.get("issuer")),
        },
    }
