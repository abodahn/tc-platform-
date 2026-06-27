"""
TC Platform — Maintenance OFFLINE AI / predictive-analytics engine.

100% on-premise: pure Python (stdlib only), no internet, no cloud, no external
API. It turns the maintenance history you already capture (breakdowns, downtime,
MTBF, PM compliance, consumption) into:

  * machine failure-risk prediction + predicted days-to-next-failure
  * prescriptive recommendations (what to do)
  * spare-part consumption forecast + reorder recommendations
  * repeated-failure / anomaly detection
  * root-cause suggestion from a machine's own history

These are transparent statistical/heuristic models — explainable and safe to run
on a factory server or even an offline laptop.
"""
from datetime import datetime, timezone

from app import ai_core
from app.maintenance.services import machine_health

_FMT = "%Y-%m-%d %H:%M:%S"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts[:19], _FMT)
    except (ValueError, TypeError):
        try:
            return datetime.strptime(ts[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return None


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------
# Machine failure-risk prediction (predictive + prescriptive)
# --------------------------------------------------------------------------
def predict_machine_risk(conn, machine):
    mid = machine["id"]
    now = _now()
    tickets = conn.execute(
        "SELECT created_at, priority, status FROM mnt_tickets WHERE machine_id=? "
        "ORDER BY created_at", (mid,)).fetchall()
    dates = [d for d in (_parse(t["created_at"]) for t in tickets) if d]
    n_fail = len(dates)
    recent_90 = sum(1 for d in dates if (now - d).days <= 90)
    open_crit = sum(1 for t in tickets
                    if t["priority"] == "critical" and t["status"] not in ("closed", "cancelled", "rejected"))

    # Mean Time Between Failures (days) from observed failure timestamps
    mtbf_days = None
    if n_fail >= 2:
        span = (dates[-1] - dates[0]).days
        if span > 0:
            mtbf_days = round(span / (n_fail - 1), 1)
    days_since = (now - dates[-1]).days if dates else None

    health, _band = machine_health(conn, machine)
    risk = 100 - health  # start from the inverse of current health

    drivers = []
    if recent_90 >= 2:
        risk += min(recent_90 * 7, 21)
        drivers.append(("recent_failures", recent_90))
    if open_crit:
        risk += 15
        drivers.append(("open_critical", open_crit))

    predicted_days = None
    if mtbf_days and days_since is not None:
        predicted_days = int(round(mtbf_days - days_since))
        if predicted_days <= 0:
            risk += 22
        elif predicted_days <= 14:
            risk += 12
        drivers.append(("mtbf", mtbf_days))

    pm_overdue = conn.execute(
        "SELECT COUNT(*) c FROM mnt_pm_plans WHERE machine_id=? AND active=1 AND next_due < date('now')",
        (mid,)).fetchone()["c"]
    if pm_overdue:
        risk += 10
        drivers.append(("pm_overdue", pm_overdue))

    if machine["criticality"] == "critical":
        risk += 8

    risk = _clamp(int(round(risk)))
    band = "high" if risk >= 66 else ("medium" if risk >= 33 else "low")

    # predicted next failure date
    next_failure = None
    if mtbf_days and dates:
        from datetime import timedelta
        next_failure = (dates[-1] + timedelta(days=mtbf_days)).strftime("%Y-%m-%d")

    rec = _recommendation(conn, machine, risk, predicted_days, pm_overdue, open_crit, recent_90)
    confidence = "high" if n_fail >= 4 else ("medium" if n_fail >= 2 else "low")

    return {
        "machine_id": mid, "code": machine["code"], "name": machine["name"],
        "risk": risk, "band": band, "health": health,
        "mtbf_days": mtbf_days, "days_since": days_since,
        "predicted_days": predicted_days, "next_failure": next_failure,
        "failures_total": n_fail, "failures_90d": recent_90,
        "open_critical": open_crit, "pm_overdue": pm_overdue,
        "confidence": confidence, "recommendation": rec, "drivers": drivers,
    }


def _recommendation(conn, machine, risk, predicted_days, pm_overdue, open_crit, recent_90):
    """Prescriptive 'what to do' — returns (key, params) for i18n rendering."""
    if pm_overdue:
        return ("rec_run_pm", {})
    if open_crit:
        return ("rec_resolve_critical", {})
    if predicted_days is not None and predicted_days <= 14:
        return ("rec_failure_soon", {"days": max(predicted_days, 0)})
    if recent_90 >= 3:
        rc = top_root_cause(conn, machine["id"])
        return ("rec_repeated", {"cause": rc[0][0] if rc else "unknown"})
    if risk >= 66:
        return ("rec_inspect", {})
    return ("rec_monitor", {})


def risk_ranking(conn, limit=None):
    rows = conn.execute("SELECT * FROM mnt_machines WHERE is_active=1").fetchall()
    out = [predict_machine_risk(conn, m) for m in rows]
    out.sort(key=lambda x: x["risk"], reverse=True)
    return out[:limit] if limit else out


# --------------------------------------------------------------------------
# Downtime/cost anomaly detection + PM-interval optimizer
# --------------------------------------------------------------------------
def downtime_anomalies(conn):
    rows = conn.execute(
        "SELECT ticket_no, machine_code, total_downtime_min FROM mnt_tickets "
        "WHERE status='closed' AND total_downtime_min>0 ORDER BY id").fetchall()
    vals = [r["total_downtime_min"] for r in rows]
    out = []
    for i, _v, z in ai_core.zscore_anomalies(vals, 2.0):
        out.append({"ticket_no": rows[i]["ticket_no"], "machine_code": rows[i]["machine_code"],
                    "downtime": rows[i]["total_downtime_min"], "z": z})
    return out


def _mtbf_days(conn, machine_id):
    dates = [d for d in (_parse(r["created_at"]) for r in conn.execute(
        "SELECT created_at FROM mnt_tickets WHERE machine_id=? ORDER BY created_at",
        (machine_id,))) if d]
    if len(dates) >= 2:
        span = (dates[-1] - dates[0]).days
        if span > 0:
            return round(span / (len(dates) - 1), 1)
    return None


def pm_optimizer(conn):
    """Recommend PM-interval changes where machines fail faster than their PM cycle."""
    out = []
    plans = conn.execute(
        "SELECT p.*, m.code mcode, m.id mid FROM mnt_pm_plans p JOIN mnt_machines m ON m.id=p.machine_id "
        "WHERE p.active=1").fetchall()
    for p in plans:
        mtbf = _mtbf_days(conn, p["mid"])
        interval = p["interval_days"] or 30
        if mtbf and mtbf < interval * 0.8:
            out.append({"mcode": p["mcode"], "title": p["title"], "current": interval,
                        "suggested": max(7, int(mtbf * 0.7)), "reason": "failing_faster",
                        "mtbf": mtbf})
    return out


# --------------------------------------------------------------------------
# Spare consumption forecast + reorder recommendation
# --------------------------------------------------------------------------
def consumption_forecast(conn, spare):
    sid = spare["id"]
    issues = conn.execute(
        "SELECT qty, created_at FROM mnt_stock_movements WHERE spare_id=? AND type='issue'",
        (sid,)).fetchall()
    dates = [d for d in (_parse(i["created_at"]) for i in issues) if d]
    total = sum(abs(i["qty"] or 0) for i in issues)
    now = _now()
    if dates:
        span_days = max((now - min(dates)).days, 30)
    else:
        span_days = 30
    per_day = total / span_days if total else 0.0
    per_month = round(per_day * 30, 1)
    stock = spare["stock_qty"] or 0
    runout_days = int(stock / per_day) if per_day > 0 else None

    lead = spare["lead_time_days"] or 7
    need = False
    reason = None
    if stock <= (spare["reorder_level"] or 0):
        need, reason = True, "at_reorder"
    elif runout_days is not None and runout_days <= lead:
        need, reason = True, "runout_before_lead"

    target = spare["max_level"] or (spare["reorder_level"] or 0) * 2 or 10
    suggested = max(0, round(target - stock))

    return {
        "spare_id": sid, "code": spare["code"], "name": spare["name"],
        "stock": stock, "reorder_level": spare["reorder_level"],
        "per_month": per_month, "runout_days": runout_days, "lead_time": lead,
        "need_reorder": need, "reason": reason, "suggested_qty": suggested,
        "criticality": spare["criticality"],
    }


def reorder_recommendations(conn):
    rows = conn.execute("SELECT * FROM mnt_spare_parts WHERE is_active=1").fetchall()
    recs = [consumption_forecast(conn, s) for s in rows]
    recs = [r for r in recs if r["need_reorder"]]
    crit_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recs.sort(key=lambda r: (crit_order.get(r["criticality"], 9), r["stock"]))
    return recs


# --------------------------------------------------------------------------
# Repeated-failure / anomaly detection
# --------------------------------------------------------------------------
def repeated_failures(conn, window_days=90, threshold=3):
    rows = conn.execute(
        "SELECT machine_id, machine_code, COUNT(*) c FROM mnt_tickets "
        "WHERE machine_id IS NOT NULL AND created_at >= date('now', ?) "
        # PostgreSQL requires every non-aggregated column in GROUP BY and forbids
        # output aliases in HAVING (SQLite allows both). machine_code is functionally
        # dependent on machine_id, so grouping by both yields identical rows.
        "GROUP BY machine_id, machine_code HAVING COUNT(*) >= ? ORDER BY c DESC",
        (f"-{window_days} day", threshold)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# SLA-breach risk for open tickets
# --------------------------------------------------------------------------
def _resolution_sla_hours(conn):
    s = conn.execute("SELECT value FROM mnt_settings WHERE key='resolution_sla_hours'").fetchone()
    try:
        return float(s["value"]) if s and s["value"] else 24.0
    except (ValueError, TypeError):
        return 24.0


def ticket_sla_risk(conn, ticket, resolution_hours=None):
    if ticket["status"] in ("closed", "cancelled", "rejected"):
        return 0
    created = _parse(ticket["created_at"])
    if not created:
        return 0
    hrs = resolution_hours if resolution_hours is not None else _resolution_sla_hours(conn)
    elapsed = (_now() - created).total_seconds() / 3600.0
    # criticality pulls the clock forward (critical tickets breach "sooner")
    factor = {"critical": 1.5, "high": 1.2, "medium": 1.0, "low": 0.8}.get(ticket["priority"], 1.0)
    return ai_core.sla_risk(elapsed * factor, hrs)


def open_tickets_sla(conn, limit=None):
    hrs = _resolution_sla_hours(conn)
    rows = conn.execute(
        "SELECT id,ticket_no,machine_code,created_at,status,priority FROM mnt_tickets "
        "WHERE is_active=1 AND status NOT IN ('closed','cancelled','rejected')").fetchall()
    out = []
    for t in rows:
        r = ticket_sla_risk(conn, t, hrs)
        out.append({"id": t["id"], "ticket_no": t["ticket_no"], "machine_code": t["machine_code"],
                    "priority": t["priority"], "status": t["status"], "risk": r, "breached": r >= 100})
    out.sort(key=lambda x: -x["risk"])
    return out[:limit] if limit else out


# --------------------------------------------------------------------------
# Alternative / substitute spare parts (when a part is low or out of stock)
# --------------------------------------------------------------------------
def alternative_parts(conn, spare_id, machine_id=None, limit=5):
    """Suggest in-stock compatible substitutes: same category, preferring parts
    that share a compatible machine with the requested part (or the machine)."""
    sp = conn.execute("SELECT id, category FROM mnt_spare_parts WHERE id=?", (spare_id,)).fetchone()
    if not sp:
        return []
    target_machines = {r["machine_id"] for r in conn.execute(
        "SELECT machine_id FROM mnt_spare_compat WHERE spare_id=?", (spare_id,))}
    if machine_id:
        target_machines.add(machine_id)
    cands = conn.execute(
        "SELECT * FROM mnt_spare_parts WHERE id!=? AND is_active=1 AND stock_qty>0 AND category=?",
        (spare_id, sp["category"])).fetchall()
    out = []
    for c in cands:
        cm = {r["machine_id"] for r in conn.execute(
            "SELECT machine_id FROM mnt_spare_compat WHERE spare_id=?", (c["id"],))}
        shares = bool(target_machines & cm)
        out.append({"id": c["id"], "code": c["code"], "name": c["name"],
                    "stock": c["stock_qty"], "uom": c["uom"], "shares_machine": shares,
                    "score": (3 if shares else 0) + 1})
    out.sort(key=lambda x: (-x["score"], -x["stock"]))
    return out[:limit]


# --------------------------------------------------------------------------
# Root-cause suggestion (AI assist on the diagnosis form)
# --------------------------------------------------------------------------
def top_root_cause(conn, machine_id, limit=2):
    rows = conn.execute(
        "SELECT d.root_cause, COUNT(*) c FROM mnt_diagnosis d "
        "JOIN mnt_tickets t ON t.id=d.ticket_id WHERE t.machine_id=? AND d.root_cause IS NOT NULL "
        "GROUP BY d.root_cause ORDER BY c DESC LIMIT ?", (machine_id, limit)).fetchall()
    return [(r["root_cause"], r["c"]) for r in rows]


# --------------------------------------------------------------------------
# AI Smart Triage — keyword classifier for priority / severity / tags
# --------------------------------------------------------------------------
_SAFETY_WORDS = ("fire", "smoke", "spark", "shock", "injury", "injured", "burn",
                 "burning", "gas", "electrocut", "hazard", "danger")
_STOP_WORDS = ("stopped", "not working", "no power", "won't start", "wont start",
               "dead", "down", "halt", "seized", "broken", "broke", "snapped",
               "jam", "jammed")
_TAG_WORDS = {
    "noise": "noise", "vibrat": "vibration", "overheat": "overheat", "hot ": "overheat",
    "smoke": "smoke", "leak": "leak", "oil": "oil", "belt": "belt", "needle": "needle",
    "motor": "motor", "sensor": "sensor", "electric": "electrical", "spark": "electrical",
    "stitch": "quality", "blade": "cutter", "jam": "jam", "bearing": "bearing",
    "pneumat": "pneumatic", "hydraul": "hydraulic", "plc": "plc_control",
}


def triage(description, machine_criticality="medium", production_stopped=False,
           safety_impact=False):
    """Offline NLP-lite classifier: suggest priority/severity + extract tags."""
    text = (description or "").lower()
    score = 0
    reasons = []
    if safety_impact:
        score += 4; reasons.append("safety_flag")
    if production_stopped:
        score += 3; reasons.append("prod_stopped")
    score += {"critical": 3, "high": 2, "medium": 1, "low": 0}.get(machine_criticality, 1)
    if any(w in text for w in _SAFETY_WORDS):
        score += 4; reasons.append("safety_words")
    if any(w in text for w in _STOP_WORDS):
        score += 2; reasons.append("stoppage_words")
    tags = sorted({tag for kw, tag in _TAG_WORDS.items() if kw in text})

    if score >= 7:
        priority = "critical"
    elif score >= 4:
        priority = "high"
    elif score >= 2:
        priority = "medium"
    else:
        priority = "low"
    severity = {"critical": "critical", "high": "major", "medium": "moderate",
                "low": "minor"}[priority]
    return {"priority": priority, "severity": severity, "tags": tags,
            "score": score, "reasons": reasons}


# --------------------------------------------------------------------------
# AI Smart Assignment — recommend technician by workload + skill match
# --------------------------------------------------------------------------
def recommend_technician(conn, category=None):
    cands = set()
    for r in conn.execute("SELECT DISTINCT assigned_to FROM mnt_tickets "
                          "WHERE assigned_to IS NOT NULL AND assigned_to!=''"):
        cands.add(r["assigned_to"])
    for u in conn.execute("SELECT full_name FROM users WHERE role='maintenance_technician' AND is_active=1"):
        if u["full_name"]:
            cands.add(u["full_name"])
    if not cands:
        cands = {"Technician A"}
    out = []
    for name in cands:
        load = conn.execute(
            "SELECT COUNT(*) c FROM mnt_tickets WHERE assigned_to=? AND status "
            "NOT IN ('closed','cancelled','rejected')", (name,)).fetchone()["c"]
        skill = 0
        if category:
            skill = conn.execute(
                "SELECT COUNT(*) c FROM mnt_tickets WHERE assigned_to=? AND issue_category=? "
                "AND status IN ('closed','resolved')", (name, category)).fetchone()["c"]
        out.append({"name": name, "open_load": load, "skill": skill,
                    "score": skill * 3 - load * 2})
    out.sort(key=lambda x: (-x["score"], x["open_load"], x["name"]))
    return out


# --------------------------------------------------------------------------
# AI Repair-time (MTTR) estimate from similar past tickets
# --------------------------------------------------------------------------
def estimate_repair_time(conn, machine_id, category=None):
    cascade = [
        ("machine", "SELECT AVG(total_downtime_min) a, COUNT(*) n FROM mnt_tickets "
                    "WHERE machine_id=? AND status='closed' AND total_downtime_min>0", (machine_id,)),
    ]
    if category:
        cascade.append(("category", "SELECT AVG(total_downtime_min) a, COUNT(*) n FROM mnt_tickets "
                        "WHERE issue_category=? AND status='closed' AND total_downtime_min>0", (category,)))
    cascade.append(("global", "SELECT AVG(total_downtime_min) a, COUNT(*) n FROM mnt_tickets "
                    "WHERE status='closed' AND total_downtime_min>0", ()))
    for basis, sql, args in cascade:
        row = conn.execute(sql, args).fetchone()
        if row["a"] and (row["n"] or 0) >= 1:
            return {"minutes": int(round(row["a"])), "basis": basis, "n": row["n"]}
    row = conn.execute("SELECT AVG(est_repair_min) a, COUNT(*) n FROM mnt_diagnosis "
                       "WHERE est_repair_min>0").fetchone()
    if row["a"]:
        return {"minutes": int(round(row["a"])), "basis": "diagnosis", "n": row["n"]}
    return {"minutes": None, "basis": "none", "n": 0}


def suggest_root_cause(conn, machine_id, category=None):
    """Suggest likely root cause: machine history first, then same-category fleet."""
    hist = top_root_cause(conn, machine_id, limit=2)
    if hist:
        return {"scope": "machine", "causes": hist}
    if category:
        rows = conn.execute(
            "SELECT d.root_cause, COUNT(*) c FROM mnt_diagnosis d "
            "JOIN mnt_tickets t ON t.id=d.ticket_id WHERE t.issue_category=? AND d.root_cause IS NOT NULL "
            "GROUP BY d.root_cause ORDER BY c DESC LIMIT 2", (category,)).fetchall()
        if rows:
            return {"scope": "category", "causes": [(r["root_cause"], r["c"]) for r in rows]}
    return {"scope": "none", "causes": []}
