"""
HR Core services — attendance, leave (single-writer balance ledger), skill
matrix, and the earned-minute piece-rate/incentive model.

The employee roster is READ from prob_employees and never written here.
Every money/quantity path states its invariant next to the formula.
"""
import math
from datetime import date, datetime, timedelta, timezone

from app.db import get_db
from .constants import (ATT_STATUS, WORKING_STATUSES, PRESENT_STATUSES,
                        OFF_FLOOR_STATUSES, STANDARD_DAY_HOURS, LEAVE_TYPES,
                        UNBALANCED_LEAVE_TYPES, MAX_LEAVE_DAYS,
                        DEFAULT_ENTITLEMENT, CAPABLE_MIN_LEVEL, ROSTER_PAGE_LIMIT,
                        INCENTIVE_THRESHOLD_PCT, DEFAULT_RATE_PER_MINUTE,
                        MAX_EFFICIENCY_PCT, ABSENTEEISM_ALERT_PCT)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _d(s):
    """Parse YYYY-MM-DD (possibly with a time suffix) to date, or None."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def normalize_date(v=None):
    """Canonical ISO day for a page/query parameter — blank or garbage gives today.

    INVARIANT: the day a page RENDERS, the day it PRE-FILLS from and the day its
    form POSTS back must be the identical string. '2026-3-5' and '2026-03-05' are
    the same calendar day but only one of them matches a stored work_date, so a
    hand-edited ?d=2026-3-5 would render a blank day-entry page for a day that is
    already marked — and one click of Save then overwrites every absence with a
    blank 'present'. Every READ entry point parses the day through here; the write
    path (save_attendance_day) still refuses an unparseable day outright rather
    than defaulting it to today."""
    return str(_d(v) or date.today())


def _f(v, default=0.0):
    """Coerce a form value to float. Blank/None/garbage -> default, never a 500.
    INVARIANT: inf and nan never leave this function — '1e999' or 'inf' typed into
    a pieces/OT/efficiency box would otherwise be stored as an infinite quantity
    and poison every SUM built on it."""
    try:
        s = str(v).strip()
        if s in ("", "None"):
            return default
        f = float(s)
    except (TypeError, ValueError, OverflowError):
        return default
    return f if math.isfinite(f) else default


def _i(v, default=0):
    """Same contract as _f, as an int. int(float('inf')) raises OverflowError, so
    the non-finite screen above has to happen first or a form field turns into a 500."""
    f = _f(v, None)
    if f is None:
        return default
    try:
        return int(f)
    except (TypeError, ValueError, OverflowError):
        return default


def _pct(part, whole):
    """Percentage with the division-by-zero case pinned to 0 (no rows = no rate)."""
    return round(100.0 * part / whole, 1) if whole else 0.0


def _bell(conn, severity, title, message, link="/people"):
    conn.execute("INSERT INTO notifications (severity,module,title,message,link,created_at) "
                 "VALUES (?,?,?,?,?,?)", (severity, "people", title, message, link, _now()))


def _bell_once(conn, ext_key, severity, title, message, link="/people"):
    """Bell alert deduplicated on the shared notifications.ext_key — no extra
    'alerted' column needed for once-per-day alerts."""
    if conn.execute("SELECT id FROM notifications WHERE ext_key=?", (ext_key,)).fetchone():
        return False
    conn.execute("INSERT INTO notifications (severity,module,title,message,link,ext_key,created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (severity, "people", title, message, link, ext_key, _now()))
    return True


# ==========================================================================
# Roster (READ-ONLY view of prob_employees — the platform employee master)
# ==========================================================================
_EMP_COLS = ("e.id, e.employee_code, e.employee_name, e.department, e.section, e.designation")


def _employee_ids(conn):
    """Every id on the employee master. A row that is not on the roster must never
    receive an attendance, skill or payroll record — it would be invisible in every
    listing (they all inner-join the roster) yet still counted by the alert sweep."""
    try:
        return {r["id"] for r in conn.execute("SELECT id FROM prob_employees").fetchall()}
    except Exception:
        return set()


def _employee_exists(conn, employee_id):
    try:
        return bool(conn.execute("SELECT id FROM prob_employees WHERE id=?",
                                 (employee_id,)).fetchone())
    except Exception:
        return False


def _employees(conn, department=None, limit=ROSTER_PAGE_LIMIT):
    sql = ("SELECT " + _EMP_COLS + " FROM prob_employees e "
           "WHERE e.is_deleted=0 AND e.active=1")
    args = []
    if department:
        sql += " AND e.department=?"; args.append(department)
    sql += " ORDER BY e.department, e.employee_code LIMIT ?"
    args.append(limit)
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception:
        return []      # probation module absent/unseeded — degrade to an empty roster


def employees(department=None, limit=ROSTER_PAGE_LIMIT):
    conn = get_db()
    try:
        return _employees(conn, department, limit)
    finally:
        conn.close()


def departments():
    conn = get_db()
    try:
        return [r["department"] for r in conn.execute(
            "SELECT DISTINCT department FROM prob_employees WHERE is_deleted=0 AND active=1 "
            "AND department IS NOT NULL AND department<>'' ORDER BY department").fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


# ==========================================================================
# Attendance
# ==========================================================================
def _hours(cin, cout):
    """Worked hours between two HH:MM stamps. An out time before the in time is a
    night shift that crossed midnight, so it wraps +24h. None if either is blank."""
    def mins(t):
        try:
            h, m = str(t).strip().split(":")[:2]
            h, m = int(h), int(m)
        except (ValueError, TypeError, AttributeError):
            return None
        # a clock stamp outside a real 24h day ("99:99") is garbage, not a shift
        return h * 60 + m if 0 <= h <= 23 and 0 <= m <= 59 else None
    a, b = mins(cin), mins(cout)
    if a is None or b is None:
        return None
    diff = b - a
    if diff < 0:
        diff += 24 * 60
    return round(diff / 60.0, 2)


def save_attendance_day(work_date, rows, user):
    """Bulk day entry. One row per employee for `work_date`, upserted on the
    (employee_id, work_date) unique index so re-posting the day corrects it
    instead of duplicating. Returns the number of rows written."""
    wd = _d(work_date)
    if not wd:
        return 0
    wd = str(wd)
    who = (user or {}).get("username")
    conn = get_db()
    try:
        valid = _employee_ids(conn)
        n = 0
        for r in rows or []:
            emp = _i(r.get("employee_id"))
            if not emp or emp not in valid:
                continue           # forged / stale employee id — never write an orphan row
            st = (r.get("status") or "present").strip()
            if st not in ATT_STATUS:
                st = "present"
            cin = (r.get("check_in") or "").strip() or None
            cout = (r.get("check_out") or "").strip() or None
            if st in OFF_FLOOR_STATUSES:
                # absent / on leave / holiday / off = not on the floor. The day-entry
                # form keeps the previously saved clock pair in its inputs, so flipping
                # the status would otherwise re-post those times and pay 9 h + OT for a
                # day the operator never worked. Zero the whole shift, stamps included.
                cin = cout = None
                worked, ot = 0.0, 0.0
            else:
                worked = _hours(cin, cout)
                if worked is None:
                    # no clock pair — a day on the floor is the standard day, else 0
                    worked = STANDARD_DAY_HOURS if st in PRESENT_STATUSES else 0.0
                # Overtime is a paid quantity, so it is bounded at both ends: a
                # negative entry is a typo (never a credit) and a calendar day only
                # holds 24 hours, so worked + OT can never exceed it. Without the
                # upper bound "800" fat-fingered into one OT box lands whole in the
                # factory overtime KPI.
                ot = min(max(0.0, _f(r.get("ot_hours"))), max(0.0, 24.0 - worked))
            ex = conn.execute("SELECT id FROM ppl_attendance WHERE employee_id=? AND work_date=?",
                              (emp, wd)).fetchone()
            if ex:
                conn.execute(
                    "UPDATE ppl_attendance SET status=?, check_in=?, check_out=?, worked_hours=?, "
                    "ot_hours=?, updated_at=? WHERE id=?",
                    (st, cin, cout, worked, ot, _now(), ex["id"]))
            else:
                conn.execute(
                    "INSERT INTO ppl_attendance (employee_id,work_date,status,check_in,check_out,"
                    "worked_hours,ot_hours,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (emp, wd, st, cin, cout, worked, ot, who, _now()))
            n += 1
        conn.commit()
    finally:
        conn.close()
    if n:
        absence_sweep(wd)
    return n


def list_attendance(work_date=None, employee_id=None, department=None, limit=300):
    conn = get_db()
    try:
        sql = ("SELECT a.*, e.employee_code, e.employee_name, e.department, e.section "
               "FROM ppl_attendance a JOIN prob_employees e ON e.id=a.employee_id WHERE 1=1")
        args = []
        if work_date:
            # same day, whatever the caller spelled it like (see normalize_date)
            sql += " AND a.work_date=?"; args.append(str(_d(work_date) or work_date)[:10])
        if employee_id:
            sql += " AND a.employee_id=?"; args.append(_i(employee_id))
        if department:
            sql += " AND e.department=?"; args.append(department)
        sql += " ORDER BY a.work_date DESC, e.employee_code LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def attendance_for(work_date, employee_ids):
    """Existing rows for EXACTLY the employees the day-entry form is about to list,
    keyed by employee id.

    INVARIANT: the pre-fill must cover every line the form posts back. Deriving it
    from a separately ordered+limited listing (list_attendance) breaks the moment
    the roster outgrows either limit: a line that is listed but not pre-filled
    posts back as a blank 'present' and silently overwrites a marked absence."""
    ids = [i for i in (_i(x) for x in (employee_ids or [])) if i]
    if not ids:
        return {}
    wd = _d(work_date)
    if not wd:
        return {}          # an unparseable day pre-fills nothing and saves nothing
    wd = str(wd)
    conn = get_db()
    try:
        out = {}
        for i in range(0, len(ids), 400):      # stay well inside SQLite's bound-param cap
            chunk = ids[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            sql = ("SELECT * FROM ppl_attendance WHERE work_date=? AND employee_id IN ("
                   + marks + ")")
            for r in conn.execute(sql, [wd] + chunk).fetchall():
                out[r["employee_id"]] = dict(r)
        return out
    except Exception:
        return {}
    finally:
        conn.close()


def attendance_stats(from_date, to_date, department=None):
    """Attendance % and absenteeism % over a date range.

        scheduled = rows with a WORKING status (holiday/off are not scheduled)
        attendance %  = (present + late) / scheduled x 100
        absenteeism % = absent / scheduled x 100
    Both are 0 when nothing is scheduled — never a ZeroDivisionError."""
    a, b = _d(from_date), _d(to_date)
    if not a or not b or b < a:
        return {"scheduled": 0, "present": 0, "absent": 0, "late": 0, "leave": 0,
                "ot_hours": 0.0, "attendance_pct": 0.0, "absenteeism_pct": 0.0, "by_dept": []}
    conn = get_db()
    try:
        sql = ("SELECT e.department AS dept, a.status AS st, COUNT(*) AS c, "
               "SUM(COALESCE(a.ot_hours,0)) AS ot FROM ppl_attendance a "
               "JOIN prob_employees e ON e.id=a.employee_id "
               "WHERE a.work_date>=? AND a.work_date<=?")
        args = [str(a), str(b)]
        if department:
            sql += " AND e.department=?"; args.append(department)
        sql += " GROUP BY e.department, a.status"
        try:
            rows = conn.execute(sql, args).fetchall()
        except Exception:
            rows = []
        tot = {"present": 0, "absent": 0, "late": 0, "leave": 0, "holiday": 0, "off": 0}
        ot_total = 0.0
        by = {}
        for r in rows:
            st = r["st"] or "present"
            c = int(r["c"] or 0)
            tot[st] = tot.get(st, 0) + c
            ot_total += float(r["ot"] or 0)
            d = by.setdefault(r["dept"] or "—", {"department": r["dept"] or "—", "scheduled": 0,
                                                 "present": 0, "absent": 0})
            if st in WORKING_STATUSES:
                d["scheduled"] += c
            if st in PRESENT_STATUSES:
                d["present"] += c
            if st == "absent":
                d["absent"] += c
        scheduled = sum(tot.get(s, 0) for s in WORKING_STATUSES)
        present = sum(tot.get(s, 0) for s in PRESENT_STATUSES)
        for d in by.values():
            d["attendance_pct"] = _pct(d["present"], d["scheduled"])
            d["absenteeism_pct"] = _pct(d["absent"], d["scheduled"])
        return {
            "scheduled": scheduled, "present": present, "absent": tot.get("absent", 0),
            "late": tot.get("late", 0), "leave": tot.get("leave", 0),
            "ot_hours": round(ot_total, 2),
            "attendance_pct": _pct(present, scheduled),
            "absenteeism_pct": _pct(tot.get("absent", 0), scheduled),
            "by_dept": sorted(by.values(), key=lambda x: x["department"]),
        }
    finally:
        conn.close()


def absence_sweep(work_date=None):
    """Raise one bell alert per day when that day's absenteeism exceeds the
    threshold. Deduplicated on notifications.ext_key. Never raises."""
    wd = str(_d(work_date) or date.today())
    try:
        conn = get_db()
    except Exception:
        return
    try:
        rows = conn.execute(
            "SELECT status AS st, COUNT(*) AS c FROM ppl_attendance WHERE work_date=? GROUP BY status",
            (wd,)).fetchall()
        counts = {r["st"]: int(r["c"] or 0) for r in rows}
        scheduled = sum(counts.get(s, 0) for s in WORKING_STATUSES)
        absent = counts.get("absent", 0)
        pct = _pct(absent, scheduled)
        if scheduled and pct > ABSENTEEISM_ALERT_PCT:
            _bell_once(conn, f"ppl_abs_{wd}", "warning", f"Absenteeism {pct}% on {wd}",
                       f"{absent} of {scheduled} scheduled staff absent on {wd} — above the "
                       f"{ABSENTEEISM_ALERT_PCT}% threshold. Line plans need rebalancing.",
                       "/people/attendance?d=" + wd)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# ==========================================================================
# Leave
# ==========================================================================
def leave_days(from_date, to_date):
    """Inclusive calendar days between two dates. 0 when unparseable or reversed."""
    a, b = _d(from_date), _d(to_date)
    if not a or not b or b < a:
        return 0.0
    return float((b - a).days + 1)


def _balance_row(conn, employee_id, leave_type, year):
    """Fetch-or-create the ledger row for (employee, type, year)."""
    sql = "SELECT * FROM ppl_leave_balance WHERE employee_id=? AND leave_type=? AND year=?"
    r = conn.execute(sql, (employee_id, leave_type, year)).fetchone()
    if r:
        return dict(r)
    conn.execute("INSERT INTO ppl_leave_balance (employee_id,leave_type,year,entitled,taken,updated_at) "
                 "VALUES (?,?,?,?,?,?)",
                 (employee_id, leave_type, year, DEFAULT_ENTITLEMENT.get(leave_type, 0.0), 0.0, _now()))
    return dict(conn.execute(sql, (employee_id, leave_type, year)).fetchone())


def _apply_balance(conn, employee_id, leave_type, year, delta):
    """The ONLY writer of ppl_leave_balance.taken. `delta` is signed: +days when an
    approval consumes entitlement, -days when an approved leave is reversed.
    INVARIANT: taken never goes below 0 (a double-restore cannot mint days)."""
    b = _balance_row(conn, employee_id, leave_type, year)
    taken = round(max(0.0, (b["taken"] or 0.0) + delta), 2)
    conn.execute("UPDATE ppl_leave_balance SET taken=?, updated_at=? WHERE id=?",
                 (taken, _now(), b["id"]))
    return taken


def get_balance(employee_id, leave_type, year=None):
    conn = get_db()
    try:
        emp = _i(employee_id)
        if not _employee_exists(conn, emp):
            # never open an entitlement ledger for somebody who is not on the roster:
            # the row would be invisible in every listing (they all join the roster)
            # yet would report a full 21-day entitlement to whoever asked for it.
            return {"employee_id": emp, "leave_type": leave_type,
                    "year": year or date.today().year,
                    "entitled": 0.0, "taken": 0.0, "remaining": 0.0}
        b = _balance_row(conn, emp, leave_type, year or date.today().year)
        conn.commit()
        ent, tk = float(b["entitled"] or 0), float(b["taken"] or 0)
        return {"employee_id": b["employee_id"], "leave_type": b["leave_type"], "year": b["year"],
                "entitled": ent, "taken": tk, "remaining": round(ent - tk, 2)}
    finally:
        conn.close()


def list_balances(employee_id=None, year=None):
    conn = get_db()
    try:
        sql = ("SELECT b.*, e.employee_code, e.employee_name, e.department "
               "FROM ppl_leave_balance b JOIN prob_employees e ON e.id=b.employee_id WHERE b.year=?")
        args = [int(year or date.today().year)]
        if employee_id:
            sql += " AND b.employee_id=?"; args.append(_i(employee_id))
        sql += " ORDER BY e.employee_code, b.leave_type"
        out = []
        for r in conn.execute(sql, args).fetchall():
            d = dict(r)
            d["remaining"] = round(float(d["entitled"] or 0) - float(d["taken"] or 0), 2)
            out.append(d)
        return out
    except Exception:
        return []
    finally:
        conn.close()


def list_leave(status=None, employee_id=None, limit=200):
    conn = get_db()
    try:
        sql = ("SELECT l.*, e.employee_code, e.employee_name, e.department "
               "FROM ppl_leave l JOIN prob_employees e ON e.id=l.employee_id WHERE 1=1")
        args = []
        if status:
            sql += " AND l.status=?"; args.append(status)
        if employee_id:
            sql += " AND l.employee_id=?"; args.append(_i(employee_id))
        sql += (" ORDER BY CASE l.status WHEN 'pending' THEN 0 ELSE 1 END, "
                "l.from_date DESC, l.id DESC LIMIT ?")
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def request_leave(data, user):
    """Raise a leave request. Returns (ok, leave_id | reason).
    RULE: days requested may not exceed the remaining balance for that type —
    unpaid leave is the only type with no entitlement and so is never checked."""
    emp = _i((data or {}).get("employee_id"))
    lt = ((data or {}).get("leave_type") or "annual").strip().lower()
    if lt not in LEAVE_TYPES:
        lt = "other"
    fd, td = _d((data or {}).get("from_date")), _d((data or {}).get("to_date"))
    days = leave_days(fd, td)
    if not emp:
        return False, "employee_required"
    if days <= 0:
        return False, "bad_dates"
    if days > MAX_LEAVE_DAYS:
        return False, "range_too_long"
    year = fd.year
    conn = get_db()
    try:
        if not _employee_exists(conn, emp):
            return False, "employee_not_found"
        # One employee cannot hold two LIVE requests over the same calendar days:
        # approving both would charge the balance twice for a single absence.
        if conn.execute("SELECT id FROM ppl_leave WHERE employee_id=? "
                        "AND status IN ('pending','approved') AND from_date<=? AND to_date>=?",
                        (emp, str(td), str(fd))).fetchone():
            return False, "overlaps_existing"
        if lt not in UNBALANCED_LEAVE_TYPES:
            b = _balance_row(conn, emp, lt, year)
            remaining = round(float(b["entitled"] or 0) - float(b["taken"] or 0), 2)
            if days > remaining:
                conn.commit()          # keep the ledger row that was just created
                return False, "insufficient_balance"
        cur = conn.execute(
            "INSERT INTO ppl_leave (employee_id,leave_type,from_date,to_date,days,reason,status,"
            "balance_applied,created_by,created_at) VALUES (?,?,?,?,?,?,'pending',0,?,?)",
            (emp, lt, str(fd), str(td), days,
             data.get("reason"), (user or {}).get("username"), _now()))
        lid = cur.lastrowid
        e = conn.execute("SELECT employee_name FROM prob_employees WHERE id=?", (emp,)).fetchone()
        _bell(conn, "info", "Leave request pending approval",
              f"{(e['employee_name'] if e else 'Employee')} requested {days:g} day(s) {lt} leave "
              f"({fd} → {td}).", "/people/leave?status=pending")
        conn.commit()
        return True, lid
    finally:
        conn.close()


def decide_leave(leave_id, action, user):
    """approve / reject / cancel a leave request. Returns (ok, reason).

    IDEMPOTENCY: the balance is deducted exactly once, latched by
    ppl_leave.balance_applied. Approving an already-approved request is refused,
    and rejecting/cancelling an approved one restores the balance exactly once."""
    conn = get_db()
    try:
        lv = conn.execute("SELECT * FROM ppl_leave WHERE id=?", (_i(leave_id),)).fetchone()
        if not lv:
            return False, "not_found"
        lv = dict(lv)
        days = float(lv["days"] or 0)
        lt = lv["leave_type"] or "annual"
        year = (_d(lv["from_date"]) or date.today()).year
        who = (user or {}).get("username")
        if action == "approve":
            if lv["status"] == "approved":
                return False, "already_approved"       # the classic double-deduct guard
            if lv["status"] in ("rejected", "cancelled"):
                return False, "already_decided"
            applied = 0
            if lt not in UNBALANCED_LEAVE_TYPES:
                # re-check at decision time: other leave may have been approved since
                b = _balance_row(conn, lv["employee_id"], lt, year)
                remaining = round(float(b["entitled"] or 0) - float(b["taken"] or 0), 2)
                if days > remaining:
                    conn.commit()
                    return False, "insufficient_balance"
                _apply_balance(conn, lv["employee_id"], lt, year, days)
                applied = 1
            conn.execute("UPDATE ppl_leave SET status='approved', approver=?, decided_at=?, "
                         "balance_applied=? WHERE id=?", (who, _now(), applied, lv["id"]))
            conn.commit()
            return True, "approved"
        if action in ("reject", "cancel"):
            target = "rejected" if action == "reject" else "cancelled"
            if lv["status"] == target:
                return False, "already_decided"
            if lv["balance_applied"]:
                _apply_balance(conn, lv["employee_id"], lt, year, -days)   # restore, once
            conn.execute("UPDATE ppl_leave SET status=?, approver=?, decided_at=?, balance_applied=0 "
                         "WHERE id=?", (target, who, _now(), lv["id"]))
            conn.commit()
            return True, target
        return False, "bad_action"
    finally:
        conn.close()


# ==========================================================================
# Skill matrix
# ==========================================================================
def set_skill(employee_id, operation, level, efficiency_pct, user):
    """Upsert one matrix cell. Level is clamped to 1..5 and efficiency to >= 0."""
    emp = _i(employee_id)
    op = (operation or "").strip()
    if not emp or not op:
        return False, "employee_and_operation_required"
    lvl = min(5, max(1, _i(level, 1)))
    eff = max(0.0, _f(efficiency_pct))
    conn = get_db()
    try:
        if not _employee_exists(conn, emp):
            return False, "employee_not_found"
        ex = conn.execute("SELECT id FROM ppl_skills WHERE employee_id=? AND operation=?",
                          (emp, op)).fetchone()
        if ex:
            conn.execute("UPDATE ppl_skills SET level=?, efficiency_pct=?, updated_by=?, updated_at=? "
                         "WHERE id=?", (lvl, eff, (user or {}).get("username"), _now(), ex["id"]))
        else:
            conn.execute("INSERT INTO ppl_skills (employee_id,operation,level,efficiency_pct,"
                         "updated_by,updated_at) VALUES (?,?,?,?,?,?)",
                         (emp, op, lvl, eff, (user or {}).get("username"), _now()))
        conn.commit()
        return True, "saved"
    finally:
        conn.close()


def best_operators(operation, min_level=CAPABLE_MIN_LEVEL, limit=10):
    """Ranked operators capable of `operation` — the input to skill-based line
    allocation (planning/MES call this). Ranked by measured efficiency then level,
    both descending. Level 1 is a trainee and is excluded by default."""
    op = (operation or "").strip()
    if not op:
        return []
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT s.employee_id, s.operation, s.level, s.efficiency_pct, "
            "e.employee_code, e.employee_name, e.department, e.section "
            "FROM ppl_skills s JOIN prob_employees e ON e.id=s.employee_id "
            "WHERE s.operation=? AND s.level>=? AND e.is_deleted=0 AND e.active=1 "
            "ORDER BY s.efficiency_pct DESC, s.level DESC, e.employee_code LIMIT ?",
            (op, _i(min_level, CAPABLE_MIN_LEVEL), limit)).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def skill_matrix(department=None, limit=ROSTER_PAGE_LIMIT):
    """employee x operation grid + per-employee coverage (how many operations they
    can run and their average level) — the other half of line allocation.
    Same roster cap as every other page: a lower limit would silently hide rated
    operators from the allocation grid with nothing on screen to say so."""
    conn = get_db()
    try:
        emps = _employees(conn, department=department, limit=limit)
        if not emps:
            return {"operations": [], "rows": []}
        ids = [e["id"] for e in emps]
        skills = []
        try:
            for i in range(0, len(ids), 400):   # stay inside SQLite's bound-param cap
                chunk = ids[i:i + 400]
                marks = ",".join("?" for _ in chunk)
                skills += conn.execute(
                    "SELECT * FROM ppl_skills WHERE employee_id IN (" + marks + ")",
                    chunk).fetchall()
        except Exception:
            skills = []
        ops = sorted({s["operation"] for s in skills})
        by_emp = {}
        for s in skills:
            by_emp.setdefault(s["employee_id"], {})[s["operation"]] = {
                "level": s["level"], "eff": s["efficiency_pct"]}
        rows = []
        for e in emps:
            cells = by_emp.get(e["id"], {})
            lv = [c["level"] or 0 for c in cells.values()]
            e = dict(e)
            e["cells"] = cells
            e["ops_count"] = len(cells)
            e["avg_level"] = round(sum(lv) / len(lv), 1) if lv else 0.0
            rows.append(e)
        return {"operations": ops, "rows": rows}
    finally:
        conn.close()


# ==========================================================================
# Piece-rate / incentive  (earned-minute model)
# ==========================================================================
def compute_incentive(pieces, smv, minutes_worked,
                      rate_per_minute=None, threshold_pct=None):
    """The earned-minute incentive model — the fair garment standard.

        earned_minutes = pieces x SMV
        efficiency %   = earned_minutes / minutes_worked x 100
        threshold_min  = minutes_worked x threshold % / 100
        incentive      = max(0, earned_minutes - threshold_min) x rate_per_minute

    INVARIANTS:
      * incentive is NEVER negative — below the threshold an operator earns 0,
        there is no clawback;
      * with no minutes worked there is no shift to pay against, so every derived
        figure is 0 (this also removes the division by zero);
      * negative pieces/SMV/minutes are impossible physical quantities and are
        clamped to 0 so a typo can never create a payable.
    """
    p = max(0.0, _f(pieces))
    s = max(0.0, _f(smv))
    mw = max(0.0, _f(minutes_worked))
    # blank / None / non-numeric falls back to the scheme default; an explicit 0 rate
    # is a real choice (no incentive scheme on that line) and is honoured.
    rate = max(0.0, _f(rate_per_minute, DEFAULT_RATE_PER_MINUTE))
    thr = max(0.0, _f(threshold_pct, INCENTIVE_THRESHOLD_PCT))
    earned = round(p * s, 2)
    if mw <= 0:
        return {"earned_minutes": 0.0, "efficiency_pct": 0.0, "incentive": 0.0,
                "minutes_worked": 0.0, "rate_per_minute": rate, "threshold_pct": thr}
    eff = round(100.0 * earned / mw, 2)
    threshold_min = mw * thr / 100.0
    incentive = round(max(0.0, earned - threshold_min) * rate, 2)
    return {"earned_minutes": earned, "efficiency_pct": eff, "incentive": incentive,
            "minutes_worked": mw, "rate_per_minute": rate, "threshold_pct": thr}


def add_piece_rate(data, user):
    """Record one operator-day-operation output. Returns (ok, id | reason)."""
    emp = _i((data or {}).get("employee_id"))
    wd = _d((data or {}).get("work_date")) or date.today()
    smv = _f((data or {}).get("smv"))
    mw = _f((data or {}).get("minutes_worked"))
    if not emp:
        return False, "employee_required"
    if smv <= 0:
        return False, "bad_smv"
    if mw <= 0:
        return False, "bad_minutes"          # no shift = nothing to pay against
    c = compute_incentive(data.get("pieces"), smv, mw,
                          data.get("rate_per_minute"), data.get("threshold_pct"))
    # An implausible output is a typo, not a record-breaking shift, and this is the
    # trust boundary of a payroll figure — refuse it so the supervisor fixes the
    # number instead of a clamp quietly deciding what they meant.
    if c["efficiency_pct"] > MAX_EFFICIENCY_PCT:
        return False, "implausible_output"
    order_id = _i(data.get("order_id")) or None
    op = (data.get("operation") or "").strip() or None
    pieces = max(0.0, _f(data.get("pieces")))
    conn = get_db()
    try:
        if not _employee_exists(conn, emp):
            return False, "employee_not_found"     # never create a payable for a ghost
        # A re-submitted form (double click, browser retry, back-and-save) must not pay
        # the same shift twice. An IDENTICAL row — same operator, day, operation, order,
        # pieces AND minutes — is a duplicate post, never a second real shift; a second
        # entry with a different quantity or operation still records normally.
        if conn.execute(
                "SELECT id FROM ppl_piece_rate WHERE employee_id=? AND work_date=? "
                "AND COALESCE(operation,'')=? AND COALESCE(order_id,0)=? "
                "AND pieces=? AND minutes_worked=?",
                (emp, str(wd), op or "", order_id or 0, pieces, mw)).fetchone():
            return False, "duplicate_entry"
        cur = conn.execute(
            "INSERT INTO ppl_piece_rate (employee_id,work_date,operation,order_id,pieces,smv,"
            "minutes_worked,rate_per_minute,threshold_pct,earned_minutes,efficiency_pct,incentive,"
            "created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (emp, str(wd), op, order_id, pieces, smv, mw,
             c["rate_per_minute"], c["threshold_pct"],
             c["earned_minutes"], c["efficiency_pct"], c["incentive"],
             (user or {}).get("username"), _now()))
        conn.commit()
        return True, cur.lastrowid
    finally:
        conn.close()


def list_piece_rate(from_date=None, to_date=None, employee_id=None, limit=200):
    conn = get_db()
    try:
        sql = ("SELECT p.*, e.employee_code, e.employee_name, e.department, o.order_no "
               "FROM ppl_piece_rate p JOIN prob_employees e ON e.id=p.employee_id "
               "LEFT JOIN ord_orders o ON o.id=p.order_id WHERE 1=1")
        args = []
        if from_date:
            sql += " AND p.work_date>=?"; args.append(str(_d(from_date) or from_date)[:10])
        if to_date:
            sql += " AND p.work_date<=?"; args.append(str(_d(to_date) or to_date)[:10])
        if employee_id:
            sql += " AND p.employee_id=?"; args.append(_i(employee_id))
        sql += " ORDER BY p.work_date DESC, p.id DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def incentive_summary(from_date, to_date):
    """Period totals + the operator ranking.
    The headline efficiency is MINUTE-WEIGHTED (total earned / total worked), not
    a mean of daily percentages — averaging percentages over unequal shifts lies."""
    conn = get_db()
    try:
        try:
            rows = conn.execute(
                "SELECT p.employee_id, e.employee_code, e.employee_name, e.department, "
                "SUM(COALESCE(p.earned_minutes,0)) AS em, SUM(COALESCE(p.minutes_worked,0)) AS mw, "
                "SUM(COALESCE(p.pieces,0)) AS pcs, SUM(COALESCE(p.incentive,0)) AS inc, "
                # DAYS worked, not records: a second operation on the same shift is a
                # second row, and COUNT(*) would report a one-day operator as two days.
                "COUNT(DISTINCT p.work_date) AS days FROM ppl_piece_rate p "
                "JOIN prob_employees e ON e.id=p.employee_id "
                "WHERE p.work_date>=? AND p.work_date<=? "
                "GROUP BY p.employee_id, e.employee_code, e.employee_name, e.department",
                (str(_d(from_date) or from_date)[:10],
                 str(_d(to_date) or to_date)[:10])).fetchall()
        except Exception:
            rows = []
        out, t_em, t_mw, t_inc, t_pcs = [], 0.0, 0.0, 0.0, 0.0
        for r in rows:
            em, mw = float(r["em"] or 0), float(r["mw"] or 0)
            t_em += em; t_mw += mw
            t_inc += float(r["inc"] or 0); t_pcs += float(r["pcs"] or 0)
            out.append({"employee_id": r["employee_id"], "employee_code": r["employee_code"],
                        "employee_name": r["employee_name"], "department": r["department"],
                        "pieces": round(float(r["pcs"] or 0), 0), "earned_minutes": round(em, 1),
                        "minutes_worked": round(mw, 1), "days": int(r["days"] or 0),
                        "efficiency_pct": round(100.0 * em / mw, 1) if mw else 0.0,
                        "incentive": round(float(r["inc"] or 0), 2)})
        out.sort(key=lambda x: x["efficiency_pct"], reverse=True)
        return {"rows": out, "pieces": round(t_pcs, 0), "earned_minutes": round(t_em, 1),
                "minutes_worked": round(t_mw, 1), "incentive": round(t_inc, 2),
                "efficiency_pct": round(100.0 * t_em / t_mw, 1) if t_mw else 0.0}
    finally:
        conn.close()


# ==========================================================================
# Dashboard
# ==========================================================================
def dashboard():
    today = str(date.today())
    stats = attendance_stats(str(date.today() - timedelta(days=29)), today)
    inc = incentive_summary(str(date.today() - timedelta(days=29)), today)
    conn = get_db()
    try:
        def one(sql, a=()):
            try:
                return conn.execute(sql, a).fetchone()["c"] or 0
            except Exception:
                return 0
        d = {
            "headcount": one("SELECT COUNT(*) c FROM prob_employees WHERE is_deleted=0 AND active=1"),
            "present_today": one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=? "
                                 "AND status IN ('present','late')", (today,)),
            "absent_today": one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=? "
                                "AND status='absent'", (today,)),
            "leave_today": one("SELECT COUNT(*) c FROM ppl_attendance WHERE work_date=? "
                               "AND status='leave'", (today,)),
            "pending_leave": one("SELECT COUNT(*) c FROM ppl_leave WHERE status='pending'"),
            "rated_ops": one("SELECT COUNT(*) c FROM ppl_skills"),
            "attendance_pct": stats["attendance_pct"],
            "absenteeism_pct": stats["absenteeism_pct"],
            "ot_hours": stats["ot_hours"],
            "avg_efficiency": inc["efficiency_pct"],
            "incentive_total": inc["incentive"],
            "by_dept": stats["by_dept"],
            "top_operators": inc["rows"][:8],
        }
        d["pending"] = list_leave(status="pending", limit=8)
        return d
    finally:
        conn.close()
