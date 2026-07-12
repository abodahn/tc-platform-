"""
Probation Management — service layer (platform raw-SQL).

Ports the standalone app's scoring + workflow engine onto the platform DB,
auth, audit and notification systems. Everything is server-side enforced:
scope filtering, validation, transitions, audit and notifications.
"""
import json
from datetime import date, datetime

from flask import request

from app.db import get_db, utcnow, log_audit
from app.security import user_has_permission, user_permissions
from .constants import (
    Status, Outcome, Recommendation, ActionType, CRITERIA, DEFAULT_MAX_TOTAL,
    DEFAULT_SETTINGS, DEFAULT_REMINDER_OFFSETS,
)


# ==========================================================================
# Settings
# ==========================================================================
def get_setting(key, default=None):
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM prob_settings WHERE key=?", (key,)).fetchone()
    finally:
        conn.close()
    if row is not None:
        return row["value"]
    return default if default is not None else DEFAULT_SETTINGS.get(key)


def get_int(key, default=0):
    try:
        return int(str(get_setting(key, default)).strip())
    except (TypeError, ValueError):
        return default


def all_settings():
    conn = get_db()
    try:
        rows = conn.execute("SELECT key,value FROM prob_settings").fetchall()
    finally:
        conn.close()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in rows})
    return out


def save_settings(form, user):
    allowed = set(DEFAULT_SETTINGS.keys())
    conn = get_db()
    try:
        for k in allowed:
            if k in form:
                v = str(form.get(k)).strip()
                conn.execute(
                    "INSERT OR IGNORE INTO prob_settings (key,value) VALUES (?,?)", (k, v))
                conn.execute("UPDATE prob_settings SET value=? WHERE key=?", (v, k))
        conn.commit()
    finally:
        conn.close()
    _audit(user, "update", "settings", 0, new={"keys": sorted(allowed & set(form.keys()))})


# ==========================================================================
# Audit + notifications
# ==========================================================================
def _audit(user, action, entity_type, entity_id, old=None, new=None,
           employee_ref=None, case_ref=None):
    """Rich module audit + a concise line to the platform audit viewer.
    Never stores passwords/tokens (callers pass only safe field maps)."""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO prob_audit (user_id,username,role,action,entity_type,entity_id,"
            "employee_ref,case_ref,old_json,new_json,ip,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (user.get("id") if user else None, user.get("username") if user else "system",
             user.get("role") if user else None, action, entity_type, entity_id,
             employee_ref, case_ref,
             json.dumps(old, ensure_ascii=False) if old is not None else None,
             json.dumps(new, ensure_ascii=False) if new is not None else None,
             _client_ip(), utcnow()),
        )
        conn.commit()
    finally:
        conn.close()
    try:
        log_audit(user.get("username") if user else "system",
                  f"probation.{action}", f"{entity_type}#{entity_id}", _client_ip())
    except Exception:  # noqa: BLE001
        pass


def _client_ip():
    try:
        return (request.headers.get("X-Forwarded-For", request.remote_addr) or "").split(",")[0].strip()
    except Exception:  # noqa: BLE001
        return ""


def _users_with_perm(conn, perm):
    """Active usernames whose effective permissions include `perm`."""
    rows = conn.execute("SELECT username, role, extra_perms FROM users WHERE is_active=1").fetchall()
    out = []
    for r in rows:
        u = {"role": r["role"], "extra_perms": r["extra_perms"]}
        if user_has_permission(u, perm):
            out.append(r["username"])
    return out


def notify_users(conn, usernames, severity, title, message, link=None,
                 case_id=None, employee_id=None, kind="info", offset_days=None):
    now = utcnow()
    for u in set(filter(None, usernames)):
        conn.execute(
            "INSERT INTO notifications (severity, module, title, message, target_user, link, created_at) "
            "VALUES (?,?,?,?,?,?,?)", (severity, "hr", title, message, u, link, now))
        conn.execute(
            "INSERT INTO prob_notifications (case_id,employee_id,target_user,kind,title,message,"
            "channel,status,offset_days,sent_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (case_id, employee_id, u, kind, title, message, "inapp", "sent", offset_days, now, now))


def notify_roles(conn, perm, severity, title, message, link=None, case_id=None, employee_id=None, kind="info"):
    notify_users(conn, _users_with_perm(conn, perm), severity, title, message,
                 link, case_id, employee_id, kind)


# ==========================================================================
# Scope security  (backend-enforced — never trust the UI)
# ==========================================================================
def is_hr(user):
    return user_has_permission(user, "prob_hr_review") or user_has_permission(user, "prob_admin")


def is_admin(user):
    return user_has_permission(user, "prob_admin") or "*" in user_permissions(user)


def can_report(user):
    return user_has_permission(user, "prob_reports") or is_hr(user)


def _scope_where(user, alias="c"):
    """Return (sql_fragment, params) limiting prob_cases (aliased) to what `user`
    may see. HR/admin see all; managers/section heads see their org scope or
    cases where they are the named manager/section head; others see nothing."""
    if is_hr(user) or is_admin(user) or can_report(user) and not user_has_permission(user, "prob_evaluate"):
        # HR/admin and pure executive-viewers (reports only) see everything
        return "1=1", []
    if not user_has_permission(user, "prob_evaluate"):
        return "1=0", []   # no probation access
    dept = (user.get("scope_department") or "").strip()
    sec = (user.get("scope_section") or "").strip()
    name = (user.get("full_name") or user.get("username") or "").strip()
    clauses, params = [], []
    if dept:
        clauses.append(f"{alias}.department = ?"); params.append(dept)
    if sec:
        clauses.append(f"{alias}.section = ?"); params.append(sec)
    if name:
        clauses.append(f"{alias}.direct_manager = ?"); params.append(name)
        clauses.append(f"{alias}.section_head = ?"); params.append(name)
    if not clauses:
        # a manager with no scope set and no name match sees nothing (safe default)
        return "1=0", []
    return "(" + " OR ".join(clauses) + ")", params


def can_view_case(user, case):
    if is_hr(user) or is_admin(user):
        return True
    if can_report(user) and not user_has_permission(user, "prob_evaluate"):
        return True
    if not user_has_permission(user, "prob_evaluate"):
        return False
    dept = (user.get("scope_department") or "").strip()
    sec = (user.get("scope_section") or "").strip()
    name = (user.get("full_name") or user.get("username") or "").strip()
    return bool(
        (dept and case.get("department") == dept) or
        (sec and case.get("section") == sec) or
        (name and (case.get("direct_manager") == name or case.get("section_head") == name))
    )


# ==========================================================================
# Employees
# ==========================================================================
def list_employees(user, q=None, limit=500):
    where, params = _scope_where(user, alias="e")
    # employees have the same dept/section/manager fields the scope keys off
    sql = ("SELECT * FROM prob_employees e WHERE is_deleted=0 AND active=1 AND (" + where + ")")
    if q:
        sql += " AND (LOWER(e.employee_name) LIKE ? OR LOWER(e.employee_code) LIKE ?)"
        params += [f"%{q.lower()}%", f"%{q.lower()}%"]
    sql += " ORDER BY e.probation_end_date IS NULL, e.probation_end_date LIMIT ?"
    params.append(limit)
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_employee(emp_id):
    conn = get_db()
    try:
        r = conn.execute("SELECT * FROM prob_employees WHERE id=?", (emp_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


# ==========================================================================
# Scoring
# ==========================================================================
def compute_scores(score_map, weight_map=None, max_total=None):
    """score_map {key:int}. Weighted sum + percentage against the template max."""
    weight_map = weight_map or {c["key"]: c.get("weight", 1) for c in CRITERIA}
    total = 0.0
    for key, w in weight_map.items():
        v = score_map.get(key)
        if isinstance(v, int):
            total += v * (w or 1)
    mx = max_total or DEFAULT_MAX_TOTAL
    pct = round(total / mx * 100, 1) if mx else 0.0
    return int(round(total)), pct


def auto_recommendation(pct):
    if pct >= get_int("threshold_pass", 75):
        return Recommendation.PASS
    if pct >= get_int("threshold_review", 60):
        return Recommendation.HR_REVIEW
    return Recommendation.NOT_RECOMMENDED


def validate_for_submit(ev, scores):
    """Return list of (error_key, criterion_label|None). Empty == valid."""
    errors = []
    need_note_at = get_int("require_notes_below", 2)
    smap = {s["criterion_key"]: s for s in scores}
    for c in CRITERIA:
        s = smap.get(c["key"])
        label = c["label_en"]
        if not s or s.get("score") is None:
            errors.append(("prob.err.score_required", label)); continue
        sc = int(s["score"])
        if not (1 <= sc <= 5):
            errors.append(("prob.err.score_range", label))
        if sc <= need_note_at and not (s.get("notes") and str(s["notes"]).strip()):
            errors.append(("prob.err.notes_required", label))
    if not ev.get("final_recommendation"):
        errors.append(("prob.err.recommendation_required", None))
    if not (ev.get("manager_comments") and str(ev["manager_comments"]).strip()):
        errors.append(("prob.err.comments_required", None))
    return errors


# ==========================================================================
# Cases + evaluations
# ==========================================================================
def _next_case_no(conn):
    row = conn.execute("SELECT COUNT(*) AS c FROM prob_cases").fetchone()
    return f"PC-{(row['c'] or 0) + 1:04d}"


def create_case(user, employee_id, template_id=None, stage="initial"):
    """Create a probation case + a draft evaluation for an employee."""
    conn = get_db()
    try:
        emp = conn.execute("SELECT * FROM prob_employees WHERE id=?", (employee_id,)).fetchone()
        if not emp:
            return None, "prob.err.employee_not_found"
        # one active case per employee
        existing = conn.execute(
            "SELECT id FROM prob_cases WHERE employee_id=? AND is_deleted=0 AND status NOT IN (?,?,?,?)",
            (employee_id, Status.APPROVED, Status.REJECTED, Status.CLOSED, Status.CANCELLED)).fetchone()
        if existing:
            return existing["id"], None
        if not template_id:
            t = conn.execute("SELECT id FROM prob_templates WHERE is_default=1 AND is_deleted=0 "
                             "ORDER BY id LIMIT 1").fetchone()
            template_id = t["id"] if t else None
        now = utcnow()
        case_no = _next_case_no(conn)
        conn.execute(
            "INSERT INTO prob_cases (case_no,employee_id,template_id,department,section,job_title,"
            "direct_manager,section_head,hr_rep,employment_type,joining_date,probation_start_date,"
            "probation_end_date,cycle_stage,status,opened_by,opened_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (case_no, employee_id, template_id, emp["department"], emp["section"], emp["designation"],
             emp["direct_manager"], emp["section_head"], emp["hr_rep"], emp["employment_type"],
             emp["joining_date"], emp["probation_start_date"], emp["probation_end_date"],
             stage, Status.DRAFT, user.get("username"), now, now, now))
        conn.commit()
        case = conn.execute("SELECT * FROM prob_cases WHERE case_no=?", (case_no,)).fetchone()
        _create_eval(conn, dict(case), template_id, user, emp)
        conn.commit()
        cid = case["id"]
    finally:
        conn.close()
    _audit(user, ActionType.CREATE, "case", cid, new={"employee_id": employee_id},
           employee_ref=emp["employee_code"], case_ref=case_no)
    return cid, None


def _create_eval(conn, case, template_id, user, emp):
    now = utcnow()
    crit = conn.execute(
        "SELECT * FROM prob_template_criteria WHERE template_id=? AND active=1 ORDER BY sort_order",
        (template_id,)).fetchall()
    max_total = sum((c["weight"] or 1) for c in crit) * 5 or DEFAULT_MAX_TOTAL
    conn.execute(
        "INSERT INTO prob_evaluations (case_id,employee_id,template_id,status,current_step,"
        "evaluation_date,warnings_count,unauthorized_absence_count,penalties_count,annual_leaves_count,"
        "max_score,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (case["id"], case["employee_id"], template_id, Status.DRAFT, "evaluation", _today(),
         emp["warnings_count"], emp["unauthorized_absence_count"], emp["penalties_count"],
         emp["annual_leaves_count"], max_total, 1, now, now))
    ev = conn.execute("SELECT id FROM prob_evaluations WHERE case_id=? ORDER BY id DESC LIMIT 1",
                      (case["id"],)).fetchone()
    for c in crit:
        conn.execute(
            "INSERT INTO prob_scores (evaluation_id,category,criterion_key,label_ar,label_en,label_tr,"
            "weight,score,notes) VALUES (?,?,?,?,?,?,?,?,?)",
            (ev["id"], c["category"], c["criterion_key"], c["label_ar"], c["label_en"], c["label_tr"],
             c["weight"], None, None))
    return ev["id"]


def roster_without_case_count(only_in_probation=True):
    """How many rostered employees have no active probation case yet."""
    conn = get_db()
    try:
        sql = ("SELECT COUNT(*) c FROM prob_employees e WHERE e.is_deleted=0 AND e.active=1 "
               "AND NOT EXISTS (SELECT 1 FROM prob_cases c WHERE c.employee_id=e.id "
               "AND c.is_deleted=0 AND c.status NOT IN (?,?,?,?))")
        params = [Status.APPROVED, Status.REJECTED, Status.CLOSED, Status.CANCELLED]
        if only_in_probation:
            sql += " AND e.probation_end_date IS NOT NULL AND e.probation_end_date >= ?"
            params.append(_today())
        return conn.execute(sql, params).fetchone()["c"]
    finally:
        conn.close()


def bulk_open_cases(user, only_in_probation=True, codes=None):
    """Open a probation case for every rostered employee that does not already
    have an active one — the bridge that makes an imported roster "take effect"
    (they show up on the dashboard and in Cases). `only_in_probation` limits to
    employees whose probation end date is today or later; `codes` (optional)
    limits to a set of employee_codes, e.g. a just-imported batch. Idempotent.
    Returns {opened, skipped_existing, considered}."""
    conn = get_db()
    try:
        sql = ("SELECT id, employee_code FROM prob_employees "
               "WHERE is_deleted=0 AND active=1")
        params = []
        if only_in_probation:
            sql += " AND probation_end_date IS NOT NULL AND probation_end_date >= ?"
            params.append(_today())
        emps = [dict(r) for r in conn.execute(sql, params).fetchall()]
        cased = {r["employee_id"] for r in conn.execute(
            "SELECT DISTINCT employee_id FROM prob_cases WHERE is_deleted=0 "
            "AND status NOT IN (?,?,?,?)",
            (Status.APPROVED, Status.REJECTED, Status.CLOSED, Status.CANCELLED)).fetchall()}
    finally:
        conn.close()
    code_set = set(codes) if codes else None
    considered = opened = skipped = 0
    for e in emps:
        if code_set is not None and e["employee_code"] not in code_set:
            continue
        considered += 1
        if e["id"] in cased:
            skipped += 1
            continue
        try:
            cid, err = create_case(user, e["id"])
            if cid and not err:
                opened += 1
            else:
                skipped += 1
        except Exception:  # noqa: BLE001 — never let one bad row abort the batch
            skipped += 1
    return {"opened": opened, "skipped_existing": skipped, "considered": considered}


def list_cases(user, status=None, department=None, q=None, outcome=None, limit=1000):
    where, params = _scope_where(user, alias="c")
    sql = ("SELECT c.*, e.employee_name, e.employee_code, e.designation "
           "FROM prob_cases c JOIN prob_employees e ON e.id=c.employee_id "
           "WHERE c.is_deleted=0 AND (" + where + ")")
    if status:
        sql += " AND c.status=?"; params.append(status)
    if outcome:
        sql += " AND c.final_outcome=?"; params.append(outcome)
    if department:
        sql += " AND c.department=?"; params.append(department)
    if q:
        sql += " AND (LOWER(e.employee_name) LIKE ? OR LOWER(e.employee_code) LIKE ? OR LOWER(c.case_no) LIKE ?)"
        params += [f"%{q.lower()}%"] * 3
    sql += " ORDER BY c.probation_end_date IS NULL, c.probation_end_date LIMIT ?"
    params.append(limit)
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()
    for r in rows:
        r["days_left"] = _days_left(r.get("probation_end_date"))
        r["overdue"] = r["status"] in Status.ACTIVE and r["days_left"] is not None and r["days_left"] < 0
    return rows


def get_case_full(case_id):
    conn = get_db()
    try:
        c = conn.execute("SELECT * FROM prob_cases WHERE id=? AND is_deleted=0", (case_id,)).fetchone()
        if not c:
            return None
        case = dict(c)
        case["employee"] = dict(conn.execute("SELECT * FROM prob_employees WHERE id=?",
                                             (case["employee_id"],)).fetchone() or {})
        ev = conn.execute("SELECT * FROM prob_evaluations WHERE case_id=? AND is_deleted=0 "
                          "ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
        case["evaluation"] = dict(ev) if ev else None
        case["scores"] = []
        if ev:
            case["scores"] = [dict(s) for s in conn.execute(
                "SELECT * FROM prob_scores WHERE evaluation_id=? ORDER BY id", (ev["id"],)).fetchall()]
        case["actions"] = [dict(a) for a in conn.execute(
            "SELECT * FROM prob_workflow_actions WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()]
        case["comments"] = [dict(x) for x in conn.execute(
            "SELECT * FROM prob_comments WHERE case_id=? AND is_deleted=0 ORDER BY id", (case_id,)).fetchall()]
        case["extensions"] = [dict(x) for x in conn.execute(
            "SELECT * FROM prob_extensions WHERE case_id=? ORDER BY id DESC", (case_id,)).fetchall()]
        case["days_left"] = _days_left(case.get("probation_end_date"))
        return case
    finally:
        conn.close()


# ==========================================================================
# Save evaluation (draft) + workflow transitions
# ==========================================================================
def save_evaluation(case_id, form, user):
    conn = get_db()
    try:
        ev = conn.execute("SELECT * FROM prob_evaluations WHERE case_id=? AND is_deleted=0 "
                          "ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
        if not ev:
            return None, "prob.err.not_found"
        ev = dict(ev)
        if ev["status"] not in Status.EDITABLE_BY_EVALUATOR:
            return None, "prob.err.locked"
        scores = [dict(s) for s in conn.execute(
            "SELECT * FROM prob_scores WHERE evaluation_id=? ORDER BY id", (ev["id"],)).fetchall()]
        score_map, weight_map = {}, {}
        for s in scores:
            key = s["criterion_key"]
            weight_map[key] = s.get("weight") or 1
            raw = form.get(f"score_{key}")
            note = (form.get(f"notes_{key}") or "").strip() or None
            sc = _int_or_none(raw)
            conn.execute("UPDATE prob_scores SET score=?, notes=? WHERE id=?", (sc, note, s["id"]))
            if isinstance(sc, int):
                score_map[key] = sc
        total, pct = compute_scores(score_map, weight_map, ev.get("max_score"))
        fields = {
            "evaluation_date": _date_or_none(form.get("evaluation_date")) or ev.get("evaluation_date"),
            "warnings_count": _int_or_none(form.get("warnings_count")),
            "unauthorized_absence_count": _int_or_none(form.get("unauthorized_absence_count")),
            "penalties_count": _int_or_none(form.get("penalties_count")),
            "annual_leaves_count": _int_or_none(form.get("annual_leaves_count")),
            "strengths": (form.get("strengths") or "").strip() or None,
            "improvement_points": (form.get("improvement_points") or "").strip() or None,
            "training_needs": (form.get("training_needs") or "").strip() or None,
            "manager_comments": (form.get("manager_comments") or "").strip() or None,
            "final_recommendation": (form.get("final_recommendation") or "").strip() or None,
            "employee_acknowledged": 1 if form.get("employee_acknowledged") else 0,
            "total_score": total, "percentage_score": pct,
            "auto_recommendation": auto_recommendation(pct),
            "evaluator_id": user.get("id"), "evaluator_name": user.get("full_name") or user.get("username"),
            "updated_at": utcnow(),
        }
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE prob_evaluations SET {sets} WHERE id=?", list(fields.values()) + [ev["id"]])
        conn.commit()
        eid = ev["id"]
    finally:
        conn.close()
    _audit(user, ActionType.UPDATE, "evaluation", eid, new={"total_score": total, "pct": pct})
    return eid, None


def submit(case_id, user):
    conn = get_db()
    try:
        ctx = _load_ctx(conn, case_id)
        if not ctx:
            return False, [("prob.err.not_found", None)]
        case, ev, scores = ctx
        if ev["status"] not in Status.EDITABLE_BY_EVALUATOR:
            return False, [("prob.err.invalid_state", None)]
        errors = validate_for_submit(ev, scores)
        if errors:
            return False, errors
        two_level = get_setting("approval_flow", "one_level") == "two_level"
        from_status = ev["status"]
        resubmit = from_status == Status.RETURNED
        if two_level and not is_hr(user) and not user_has_permission(user, "prob_admin"):
            to_status, step = Status.PENDING_MANAGER, "manager"
        else:
            to_status, step = Status.PENDING_HR_REVIEW, "hr_review"
        now = utcnow()
        conn.execute("UPDATE prob_evaluations SET status=?, current_step=?, submitted_at=?, updated_at=? "
                     "WHERE id=?", (to_status, step, now, now, ev["id"]))
        conn.execute("UPDATE prob_cases SET status=?, updated_at=? WHERE id=?", (to_status, now, case_id))
        _record(conn, case_id, ev["id"], user,
                ActionType.RESUBMIT if resubmit else ActionType.SUBMIT, from_status, to_status)
        label = _emp_label(case)
        link = f"/hr/probation/cases/{case_id}"
        if to_status == Status.PENDING_MANAGER:
            notify_roles(conn, "prob_evaluate", "info", "Evaluation awaiting manager review",
                         f"An evaluation for {label} was submitted for manager review.",
                         link, case_id, case["employee_id"], "pending")
        else:
            notify_roles(conn, "prob_hr_review", "warning", "Evaluation pending HR review",
                         f"Probation evaluation for {label} awaits the HR decision.",
                         link, case_id, case["employee_id"], "pending")
        conn.commit()
    finally:
        conn.close()
    _audit(user, ActionType.SUBMIT, "case", case_id,
           old={"status": from_status}, new={"status": to_status}, case_ref=case["case_no"])
    return True, []


def manager_forward(case_id, user, comment=None):
    conn = get_db()
    try:
        ctx = _load_ctx(conn, case_id)
        if not ctx:
            return False, [("prob.err.not_found", None)]
        case, ev, _ = ctx
        if ev["status"] != Status.PENDING_MANAGER:
            return False, [("prob.err.invalid_state", None)]
        now = utcnow()
        conn.execute("UPDATE prob_evaluations SET status=?, current_step=?, manager_id=?, updated_at=? "
                     "WHERE id=?", (Status.PENDING_HR_REVIEW, "hr_review", user.get("id"), now, ev["id"]))
        conn.execute("UPDATE prob_cases SET status=?, updated_at=? WHERE id=?",
                     (Status.PENDING_HR_REVIEW, now, case_id))
        _record(conn, case_id, ev["id"], user, ActionType.FORWARD, Status.PENDING_MANAGER,
                Status.PENDING_HR_REVIEW, comment)
        notify_roles(conn, "prob_hr_review", "warning", "Evaluation pending HR review",
                     f"Manager forwarded the evaluation for {_emp_label(case)}.",
                     f"/hr/probation/cases/{case_id}", case_id, case["employee_id"], "pending")
        conn.commit()
    finally:
        conn.close()
    _audit(user, ActionType.FORWARD, "case", case_id, case_ref=case["case_no"])
    return True, []


def hr_decide(case_id, user, decision, reason=None, final_outcome=None, override_reason=None):
    """decision: approve | reject | return.  Locks on approve/reject."""
    conn = get_db()
    try:
        ctx = _load_ctx(conn, case_id)
        if not ctx:
            return False, [("prob.err.not_found", None)]
        case, ev, _ = ctx
        if ev["status"] != Status.PENDING_HR_REVIEW:
            return False, [("prob.err.invalid_state", None)]
        now = utcnow()
        from_status = ev["status"]

        if decision == "return":
            if not (reason and reason.strip()):
                return False, [("prob.err.reason_required", None)]
            conn.execute("UPDATE prob_evaluations SET status=?, current_step=?, hr_decision=?, "
                         "hr_rejection_reason=?, hr_reviewer_id=?, hr_reviewer_name=?, returned_at=?, updated_at=? "
                         "WHERE id=?", (Status.RETURNED, "evaluation", "returned", reason.strip(),
                                        user.get("id"), user.get("full_name") or user.get("username"),
                                        now, now, ev["id"]))
            conn.execute("UPDATE prob_cases SET status=?, updated_at=? WHERE id=?",
                         (Status.RETURNED, now, case_id))
            _snapshot_version(conn, ev, case_id, "returned", user)
            _record(conn, case_id, ev["id"], user, ActionType.RETURN, from_status, Status.RETURNED, reason)
            _notify_evaluator(conn, case, ev, "Evaluation returned for correction",
                              f"HR returned the evaluation for {_emp_label(case)}. Reason: {reason.strip()}", "reject")
            conn.commit()
            to_status = Status.RETURNED
        elif decision == "reject":
            if not (reason and reason.strip()):
                return False, [("prob.err.reason_required", None)]
            conn.execute("UPDATE prob_evaluations SET status=?, current_step=?, hr_decision=?, "
                         "hr_rejection_reason=?, final_recommendation=?, hr_reviewer_id=?, hr_reviewer_name=?, "
                         "rejected_at=?, updated_at=? WHERE id=?",
                         (Status.REJECTED, "completed", "rejected", reason.strip(), Outcome.NOT_CONFIRM,
                          user.get("id"), user.get("full_name") or user.get("username"), now, now, ev["id"]))
            conn.execute("UPDATE prob_cases SET status=?, final_outcome=?, closed_by=?, closed_at=?, updated_at=? "
                         "WHERE id=?", (Status.REJECTED, Outcome.NOT_CONFIRM, user.get("username"), now, now, case_id))
            _record(conn, case_id, ev["id"], user, ActionType.REJECT, from_status, Status.REJECTED, reason)
            _notify_evaluator(conn, case, ev, "Evaluation not confirmed",
                              f"HR did not confirm {_emp_label(case)}. Reason: {reason.strip()}", "reject")
            conn.commit()
            to_status = Status.REJECTED
        else:  # approve
            outcome = final_outcome or ev.get("final_recommendation") or Outcome.CONFIRM
            if outcome not in Outcome.ALL:
                outcome = Outcome.CONFIRM
            # HR override of the recommendation requires a justification
            if ev.get("final_recommendation") and outcome != ev["final_recommendation"] and not (override_reason or "").strip():
                return False, [("prob.err.override_reason_required", None)]
            over = (override_reason or "").strip() or None
            conn.execute("UPDATE prob_evaluations SET status=?, current_step=?, hr_decision=?, "
                         "final_recommendation=?, override_justification=?, hr_reviewer_id=?, hr_reviewer_name=?, "
                         "approved_at=?, updated_at=? WHERE id=?",
                         (Status.APPROVED, "completed", "approved", outcome, over, user.get("id"),
                          user.get("full_name") or user.get("username"), now, now, ev["id"]))
            conn.execute("UPDATE prob_cases SET status=?, final_outcome=?, closed_by=?, closed_at=?, updated_at=? "
                         "WHERE id=?", (Status.APPROVED, outcome, user.get("username"), now, now, case_id))
            if over:
                _audit(user, ActionType.OVERRIDE, "evaluation", ev["id"],
                       new={"final_recommendation": outcome, "justification": over})
            _record(conn, case_id, ev["id"], user, ActionType.APPROVE, from_status, Status.APPROVED, reason)
            _notify_evaluator(conn, case, ev, "Evaluation approved",
                              f"HR approved the probation evaluation for {_emp_label(case)}.", "approve")
            conn.commit()
            to_status = Status.APPROVED
    finally:
        conn.close()
    _audit(user, decision, "case", case_id, old={"status": from_status}, new={"status": to_status},
           case_ref=case["case_no"], employee_ref=case.get("employee_code"))
    return True, []


def admin_reopen(case_id, user, reason):
    if not (reason and reason.strip()):
        return False, [("prob.err.reason_required", None)]
    conn = get_db()
    try:
        ctx = _load_ctx(conn, case_id)
        if not ctx:
            return False, [("prob.err.not_found", None)]
        case, ev, _ = ctx
        now = utcnow()
        from_status = ev["status"]
        _snapshot_version(conn, ev, case_id, f"reopen: {reason.strip()}", user)
        conn.execute("UPDATE prob_evaluations SET status=?, current_step=?, updated_at=? WHERE id=?",
                     (Status.RETURNED, "evaluation", now, ev["id"]))
        conn.execute("UPDATE prob_cases SET status=?, final_outcome=NULL, closed_by=NULL, closed_at=NULL, "
                     "reopen_count=reopen_count+1, updated_at=? WHERE id=?", (Status.RETURNED, now, case_id))
        _record(conn, case_id, ev["id"], user, ActionType.REOPEN, from_status, Status.RETURNED,
                f"[ADMIN REOPEN] {reason.strip()}")
        conn.commit()
    finally:
        conn.close()
    _audit(user, ActionType.REOPEN, "case", case_id, old={"status": from_status},
           new={"status": Status.RETURNED, "reason": reason.strip()}, case_ref=case["case_no"])
    return True, []


def extend_probation(case_id, user, new_end_date, reason):
    if not new_end_date:
        return False, [("prob.err.date_required", None)]
    conn = get_db()
    try:
        case = conn.execute("SELECT * FROM prob_cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            return False, [("prob.err.not_found", None)]
        case = dict(case)
        prev = case.get("probation_end_date")
        extra = _days_between(prev, new_end_date)
        now = utcnow()
        conn.execute("INSERT INTO prob_extensions (case_id,employee_id,previous_end_date,new_end_date,"
                     "extra_days,reason,extended_by,extended_by_name,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (case_id, case["employee_id"], prev, new_end_date, extra, (reason or "").strip(),
                      user.get("username"), user.get("full_name") or user.get("username"), now))
        conn.execute("UPDATE prob_cases SET probation_end_date=?, final_outcome=?, updated_at=? WHERE id=?",
                     (new_end_date, Outcome.EXTEND, now, case_id))
        conn.execute("UPDATE prob_employees SET probation_end_date=?, updated_at=? WHERE id=?",
                     (new_end_date, now, case["employee_id"]))
        _record(conn, case_id, None, user, ActionType.EXTEND, case["status"], case["status"],
                f"Extended to {new_end_date}: {(reason or '').strip()}")
        conn.commit()
    finally:
        conn.close()
    _audit(user, ActionType.EXTEND, "case", case_id,
           old={"end": prev}, new={"end": new_end_date}, case_ref=case["case_no"])
    return True, []


def cancel_case(case_id, user, reason):
    conn = get_db()
    try:
        case = conn.execute("SELECT * FROM prob_cases WHERE id=?", (case_id,)).fetchone()
        if not case:
            return False, [("prob.err.not_found", None)]
        case = dict(case)
        now = utcnow()
        conn.execute("UPDATE prob_cases SET status=?, closed_by=?, closed_at=?, updated_at=? WHERE id=?",
                     (Status.CANCELLED, user.get("username"), now, now, case_id))
        conn.execute("UPDATE prob_evaluations SET status=?, updated_at=? WHERE case_id=?",
                     (Status.CANCELLED, now, case_id))
        _record(conn, case_id, None, user, ActionType.CANCEL, case["status"], Status.CANCELLED,
                (reason or "").strip())
        conn.commit()
    finally:
        conn.close()
    _audit(user, ActionType.CANCEL, "case", case_id, case_ref=case["case_no"])
    return True, []


def add_comment(case_id, user, body, confidential=False):
    body = (body or "").strip()
    if not body:
        return False
    conn = get_db()
    try:
        ev = conn.execute("SELECT id FROM prob_evaluations WHERE case_id=? ORDER BY id DESC LIMIT 1",
                          (case_id,)).fetchone()
        conn.execute("INSERT INTO prob_comments (case_id,evaluation_id,author_id,author_name,author_role,"
                     "body,is_confidential,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (case_id, ev["id"] if ev else None, user.get("id"),
                      user.get("full_name") or user.get("username"), user.get("role"), body,
                      1 if confidential else 0, utcnow()))
        conn.commit()
    finally:
        conn.close()
    return True


# ==========================================================================
# Dashboard + reports
# ==========================================================================
def dashboard(user):
    rows = list_cases(user, limit=100000)
    active = [r for r in rows if r["status"] in Status.ACTIVE]
    total_active = len(active)
    due30 = sum(1 for r in active if r["days_left"] is not None and 0 <= r["days_left"] <= 30)
    due7 = sum(1 for r in active if r["days_left"] is not None and 0 <= r["days_left"] <= 7)
    overdue = sum(1 for r in active if r["overdue"])
    pending_mgr = sum(1 for r in rows if r["status"] in (Status.PENDING_MANAGER, Status.DRAFT, Status.RETURNED))
    pending_hr = sum(1 for r in rows if r["status"] == Status.PENDING_HR_REVIEW)
    confirmed = sum(1 for r in rows if r["final_outcome"] == Outcome.CONFIRM)
    extended = sum(1 for r in rows if r["final_outcome"] == Outcome.EXTEND)
    not_confirmed = sum(1 for r in rows if r["final_outcome"] == Outcome.NOT_CONFIRM)
    closed = sum(1 for r in rows if r["status"] in (Status.APPROVED, Status.REJECTED, Status.CLOSED))
    completion = round(100 * closed / len(rows), 1) if rows else 0
    # avg score of completed evaluations within scope
    avg = _avg_score(user)
    # by-department breakdown
    dept = {}
    for r in rows:
        d = r.get("department") or "—"
        dept.setdefault(d, {"total": 0, "confirmed": 0, "overdue": 0})
        dept[d]["total"] += 1
        if r["final_outcome"] == Outcome.CONFIRM:
            dept[d]["confirmed"] += 1
        if r["overdue"]:
            dept[d]["overdue"] += 1
    by_dept = [{"department": k, **v} for k, v in sorted(dept.items(), key=lambda x: -x[1]["total"])]
    # status distribution
    status_counts = {}
    for r in rows:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
    # Roster visibility: how many imported employees are still awaiting a case.
    # Only meaningful for HR/admin, who can act on the whole roster.
    roster_pending = 0
    if is_hr(user) or is_admin(user):
        try:
            roster_pending = roster_without_case_count(only_in_probation=True)
        except Exception:  # noqa: BLE001
            roster_pending = 0
    return {
        "total_active": total_active, "due30": due30, "due7": due7, "overdue": overdue,
        "pending_mgr": pending_mgr, "pending_hr": pending_hr, "confirmed": confirmed,
        "extended": extended, "not_confirmed": not_confirmed, "completion": completion,
        "avg_score": avg, "by_dept": by_dept[:8], "status_counts": status_counts,
        "total_cases": len(rows), "roster_pending": roster_pending,
    }


def _avg_score(user):
    where, params = _scope_where(user, alias="c")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT AVG(ev.percentage_score) AS a FROM prob_evaluations ev "
            "JOIN prob_cases c ON c.id=ev.case_id "
            "WHERE ev.percentage_score IS NOT NULL AND (" + where + ")", params).fetchone()
        return round(row["a"], 1) if row and row["a"] is not None else 0
    finally:
        conn.close()


def my_pending(user):
    """Cases needing THIS user's action (evaluator draft/returned; HR review)."""
    out = []
    if user_has_permission(user, "prob_evaluate"):
        out += [r for r in list_cases(user) if r["status"] in (Status.DRAFT, Status.RETURNED, Status.PENDING_MANAGER)]
    if is_hr(user):
        out += [r for r in list_cases(user, status=Status.PENDING_HR_REVIEW)]
    seen, uniq = set(), []
    for r in out:
        if r["id"] not in seen:
            seen.add(r["id"]); uniq.append(r)
    return uniq


# ==========================================================================
# Reminders / escalations
# ==========================================================================
def scan_reminders(commit=True):
    """Scan active cases and raise in-app reminders (idempotent per case+kind+offset).
    Returns a summary dict. Safe to call from a scheduler or on-demand."""
    offsets = _offsets()
    sla = get_int("manager_sla_days", 5)
    created = {"due": 0, "overdue": 0, "pending": 0}
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT c.*, e.employee_name, e.employee_code FROM prob_cases c "
            "JOIN prob_employees e ON e.id=c.employee_id WHERE c.is_deleted=0 AND c.status IN (%s)"
            % ",".join(["?"] * len(Status.ACTIVE)), Status.ACTIVE).fetchall()
        for c in rows:
            c = dict(c)
            dl = _days_left(c.get("probation_end_date"))
            label = _emp_label(c)
            link = f"/hr/probation/cases/{c['id']}"
            if dl is not None and dl < 0:
                if _remind_once(conn, c["id"], "overdue", dl):
                    notify_roles(conn, "prob_hr_review", "critical", "Probation overdue",
                                 f"{label} is {abs(dl)} day(s) past the probation end date.",
                                 link, c["id"], c["employee_id"], "overdue")
                    _reminder_row(conn, c, "overdue", dl); created["overdue"] += 1
            elif dl is not None and dl in offsets:
                if _remind_once(conn, c["id"], "due", dl):
                    notify_roles(conn, "prob_hr_review", "warning", "Probation due soon",
                                 f"{label}'s probation ends in {dl} day(s).",
                                 link, c["id"], c["employee_id"], "due")
                    _reminder_row(conn, c, "due", dl); created["due"] += 1
            # pending-manager escalation
            if c["status"] in (Status.DRAFT, Status.PENDING_MANAGER, Status.RETURNED):
                age = _days_between(c.get("updated_at", "")[:10], _today())
                if age is not None and age >= sla and _remind_once(conn, c["id"], "pending", age):
                    notify_roles(conn, "prob_hr_review", "warning", "Evaluation stalled",
                                 f"The evaluation for {label} has been pending {age} day(s).",
                                 link, c["id"], c["employee_id"], "escalation")
                    _reminder_row(conn, c, "pending", age); created["pending"] += 1
        if commit:
            conn.commit()
    finally:
        conn.close()
    return created


def maybe_auto_scan():
    """Run the reminder scan at most once per day, opportunistically (on dashboard
    load). Lets reminders 'just work' without an external cron; a real scheduler can
    also call scan_reminders() directly. Never raises to the caller."""
    try:
        today = date.today().isoformat()
        if get_setting("last_reminder_scan", "") == today:
            return
        scan_reminders()
        conn = get_db()
        try:
            conn.execute("INSERT OR IGNORE INTO prob_settings (key,value) VALUES ('last_reminder_scan',?)", (today,))
            conn.execute("UPDATE prob_settings SET value=? WHERE key='last_reminder_scan'", (today,))
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001
        pass


def notification_history(user, limit=200):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT n.*, c.case_no FROM prob_notifications n LEFT JOIN prob_cases c ON c.id=n.case_id "
            "ORDER BY n.id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ==========================================================================
# Import (idempotent) + export
# ==========================================================================
# Header aliases (exact-normalised match wins; then a safe "contains" fallback).
# Covers the official T&C sheet in BOTH its Arabic ("T&C") and English
# ("English version") header styles. Order the aliases specific -> generic and
# avoid ambiguous short tokens (e.g. never bare "leaves", which appears in the
# absence header too).
_IMPORT_MAP = {
    "employee_code": ["employee code (id)", "employee_code", "كود الموظف", "employee code", "الكود", "code", "emp_code"],
    "employee_name": ["employee name", "employee_name", "الاسم", "اسم الموظف", "full name", "name"],
    "department": ["department", "الادارة", "الإدارة", "dept", "الإدارة/القسم"],
    "section": ["section", "القسم", "الخط", "sub department"],
    "designation": ["designation", "الوظيفة", "job title", "المسمى الوظيفي", "المسمى", "title"],
    "direct_manager": ["direct manager", "direct_manager", "المدير", "المشرف", "manager", "supervisor"],
    "section_head": ["section head", "section_head", "رئيس القسم", "head"],
    "joining_date": ["joining date", "joining_date", "تاريخ التعيين", "hire date", "join date"],
    "probation_end_date": ["probation period end date", "probation_end_date", "تاريخ نهاية فترة الاختبار",
                           "probation end date", "نهاية فترة الاختبار", "نهاية الاختبار", "probation end", "end date"],
    "evaluation_date": ["evaluation date", "تاريخ التقييم", "evaluation_date"],
    "warnings_count": ["no.warnings", "عدد الانذارات", "warnings_count", "warnings", "الانذارات", "انذار"],
    "unauthorized_absence_count": ["no.unauthorized absence leaves", "عدد ايام الغياب بدون اذن",
                                   "unauthorized_absence_count", "unauthorized absence", "الغياب بدون اذن", "absence", "غياب"],
    "penalties_count": ["no.penalties", "الجزاءات", "penalties_count", "penalties", "عدد الجزاءات", "penalty"],
    "annual_leaves_count": ["no. annual leaves", "عدد ايام الاجازات السنويه", "annual_leaves_count",
                            "annual leaves", "الاجازات السنوية", "annual", "اجازات سنوية"],
    "imported_final_recommendation": ["final recommendation", "التوصية النهائية", "imported_final_recommendation",
                                      "recommendation", "التوصية"],
}


def import_rows(rows, user, file_name="import"):
    """rows: list of dicts (already header-mapped or raw). Idempotent by employee_code.
    Returns a report dict {total, created, updated, skipped, errors, details}."""
    report = {"total": 0, "created": 0, "updated": 0, "skipped": 0, "errors": 0, "details": []}
    conn = get_db()
    try:
        now = utcnow()
        for i, raw in enumerate(rows, 1):
            report["total"] += 1
            rec = _map_row(raw)
            code = str(rec.get("employee_code") or "").strip()
            if code.endswith(".0"):        # int codes read as 51324.0
                code = code[:-2]
            if not code:
                report["skipped"] += 1
                report["details"].append({"row": i, "status": "skipped", "reason": "no employee_code"})
                continue
            try:
                existing = conn.execute("SELECT id FROM prob_employees WHERE employee_code=?", (code,)).fetchone()
                vals = (_txt(rec.get("employee_name")), _txt(rec.get("department")), _txt(rec.get("section")),
                        _txt(rec.get("designation")), _txt(rec.get("direct_manager")), _txt(rec.get("section_head")),
                        _date_or_none(rec.get("joining_date")), _date_or_none(rec.get("probation_end_date")),
                        _int_or_none(rec.get("warnings_count")) or 0,
                        _int_or_none(rec.get("unauthorized_absence_count")) or 0,
                        _int_or_none(rec.get("penalties_count")) or 0,
                        _int_or_none(rec.get("annual_leaves_count")) or 0,
                        _txt(rec.get("imported_final_recommendation")))
                if existing:
                    conn.execute(
                        "UPDATE prob_employees SET employee_name=?,department=?,section=?,designation=?,"
                        "direct_manager=?,section_head=?,joining_date=?,probation_end_date=?,warnings_count=?,"
                        "unauthorized_absence_count=?,penalties_count=?,annual_leaves_count=?,"
                        "imported_final_recommendation=?,source_file=?,updated_at=? WHERE id=?",
                        vals + (file_name, now, existing["id"]))
                    report["updated"] += 1
                else:
                    conn.execute(
                        "INSERT INTO prob_employees (employee_name,department,section,designation,direct_manager,"
                        "section_head,joining_date,probation_end_date,warnings_count,unauthorized_absence_count,"
                        "penalties_count,annual_leaves_count,imported_final_recommendation,employee_code,source_file,"
                        "source_row,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        vals + (code, file_name, i, 1, now, now))
                    report["created"] += 1
            except Exception as exc:  # noqa: BLE001
                report["errors"] += 1
                report["details"].append({"row": i, "status": "error", "reason": str(exc)[:120]})
        conn.execute("INSERT INTO prob_import_batches (batch_name,file_name,imported_by,imported_by_name,"
                     "imported_at,total,created,updated,skipped,errors,report_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (file_name, file_name, user.get("username"), user.get("full_name") or user.get("username"),
                      now, report["total"], report["created"], report["updated"], report["skipped"],
                      report["errors"], json.dumps(report["details"][:200], ensure_ascii=False)))
        conn.commit()
    finally:
        conn.close()
    _audit(user, ActionType.IMPORT, "import", 0, new={k: report[k] for k in
           ("total", "created", "updated", "skipped", "errors")})
    return report


def cases_csv(user, **filters):
    import io, csv
    rows = list_cases(user, **{k: v for k, v in filters.items() if v}, limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Case", "Employee Code", "Employee", "Department", "Section", "Job Title",
                "Probation End", "Days Left", "Status", "Outcome"])
    for r in rows:
        w.writerow([_cell(r.get("case_no")), _cell(r.get("employee_code")), _cell(r.get("employee_name")),
                    _cell(r.get("department")), _cell(r.get("section")), _cell(r.get("designation")),
                    _cell(r.get("probation_end_date")), _cell(r.get("days_left")),
                    _cell(Status.LABEL_EN.get(r.get("status"), r.get("status"))),
                    _cell(Outcome.LABEL_EN.get(r.get("final_outcome"), r.get("final_outcome") or ""))])
    return buf.getvalue()


# ==========================================================================
# internal helpers
# ==========================================================================
def _load_ctx(conn, case_id):
    c = conn.execute("SELECT * FROM prob_cases WHERE id=? AND is_deleted=0", (case_id,)).fetchone()
    if not c:
        return None
    case = dict(c)
    emp = conn.execute("SELECT employee_code FROM prob_employees WHERE id=?", (case["employee_id"],)).fetchone()
    case["employee_code"] = emp["employee_code"] if emp else None
    ev = conn.execute("SELECT * FROM prob_evaluations WHERE case_id=? AND is_deleted=0 ORDER BY id DESC LIMIT 1",
                      (case_id,)).fetchone()
    if not ev:
        return None
    ev = dict(ev)
    scores = [dict(s) for s in conn.execute("SELECT * FROM prob_scores WHERE evaluation_id=?",
                                            (ev["id"],)).fetchall()]
    return case, ev, scores


def _record(conn, case_id, eval_id, user, action, from_status, to_status, comment=None):
    conn.execute("INSERT INTO prob_workflow_actions (case_id,evaluation_id,action_by,action_by_name,"
                 "action_type,from_status,to_status,comment,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (case_id, eval_id, user.get("id"), user.get("full_name") or user.get("username"),
                  action, from_status, to_status, comment, utcnow()))


def _snapshot_version(conn, ev, case_id, reason, user):
    scores = [dict(s) for s in conn.execute("SELECT category,criterion_key,score,notes FROM prob_scores "
                                            "WHERE evaluation_id=?", (ev["id"],)).fetchall()]
    snap = {"evaluation": {k: ev.get(k) for k in (
        "total_score", "percentage_score", "final_recommendation", "manager_comments",
        "strengths", "improvement_points", "training_needs", "status")}, "scores": scores}
    conn.execute("INSERT INTO prob_eval_versions (evaluation_id,case_id,version,snapshot_json,reason,"
                 "archived_by,archived_at) VALUES (?,?,?,?,?,?,?)",
                 (ev["id"], case_id, ev.get("version", 1), json.dumps(snap, ensure_ascii=False),
                  reason, user.get("username"), utcnow()))
    conn.execute("UPDATE prob_evaluations SET version=version+1 WHERE id=?", (ev["id"],))


def _notify_evaluator(conn, case, ev, title, message, kind):
    uname = None
    if ev.get("evaluator_id"):
        row = conn.execute("SELECT username FROM users WHERE id=?", (ev["evaluator_id"],)).fetchone()
        uname = row["username"] if row else None
    if uname:
        notify_users(conn, [uname], "info", title, message,
                     f"/hr/probation/cases/{case['id']}", case["id"], case["employee_id"], kind)


def _remind_once(conn, case_id, kind, offset):
    r = conn.execute("SELECT 1 FROM prob_notifications WHERE case_id=? AND kind=? AND offset_days=? LIMIT 1",
                     (case_id, kind, offset)).fetchone()
    return r is None


def _reminder_row(conn, case, kind, offset):
    pass  # the prob_notifications row is written by notify_users (dedup handled by _remind_once)


def _emp_label(case):
    return f"{case.get('employee_name') or ''} ({case.get('employee_code') or case.get('case_no')})".strip()


def _norm_key(s):
    return " ".join(str(s).strip().lower().split()) if s is not None else ""


def _map_row(raw):
    norm = {}
    for k, v in raw.items():
        nk = _norm_key(k)
        if nk and nk not in norm:
            norm[nk] = v
    out = {}
    for field, aliases in _IMPORT_MAP.items():
        val = None
        for a in aliases:                       # 1) exact normalised header match
            na = _norm_key(a)
            if na in norm and norm[na] not in (None, ""):
                val = norm[na]; break
        if val is None:                         # 2) safe "contains" fallback
            for a in aliases:
                na = _norm_key(a)
                if not na:
                    continue
                for hk, hv in norm.items():
                    if na in hk and hv not in (None, ""):
                        val = hv; break
                if val is not None:
                    break
        if val is not None:
            out[field] = val
    return out


def _offsets():
    raw = get_setting("reminder_offsets", ",".join(str(x) for x in DEFAULT_REMINDER_OFFSETS))
    try:
        return [int(x.strip()) for x in str(raw).split(",") if x.strip()]
    except ValueError:
        return list(DEFAULT_REMINDER_OFFSETS)


def _today():
    return date.today().isoformat()


def _days_left(end_iso):
    d = _to_date(end_iso)
    return (d - date.today()).days if d else None


def _days_between(a_iso, b_iso):
    a, b = _to_date(a_iso), _to_date(b_iso)
    return (b - a).days if (a and b) else None


def _to_date(v):
    if v in (None, ""):
        return None
    if isinstance(v, datetime):      # Excel cells come back as datetime — check first
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _date_or_none(v):
    d = _to_date(v)
    return d.isoformat() if d else None


def _int_or_none(v):
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _txt(v):
    """Coerce any cell value (int/float/datetime/str) to trimmed text or None."""
    if v in (None, ""):
        return None
    s = str(v).strip()
    return s or None


def _cell(v):
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s
