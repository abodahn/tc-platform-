"""
Compliance services — audits, CAP findings, certificates + the expiry/overdue
sweep that raises platform-bell alerts before an audit or licence lapses.
"""
from datetime import date, datetime, timedelta

from app.db import get_db
from .constants import EXPIRY_WARN_DAYS


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _today():
    return date.today()


def _d(s):
    """Parse a YYYY-MM-DD (possibly with time) string to date, or None."""
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _bell(conn, severity, title, message, link="/compliance"):
    """Surface an alert on the platform notification bell (reuses the shared
    `notifications` table the header reads)."""
    conn.execute(
        "INSERT INTO notifications (severity,module,title,message,link,created_at) "
        "VALUES (?,?,?,?,?,?)", (severity, "compliance", title, message, link, _now()))


# --- reads ----------------------------------------------------------------
def list_audits(scheme=None, status=None):
    conn = get_db()
    try:
        q = "SELECT * FROM cmp_audits WHERE 1=1"
        args = []
        if scheme:
            q += " AND scheme=?"; args.append(scheme)
        if status:
            q += " AND status=?"; args.append(status)
        q += " ORDER BY COALESCE(valid_until, scheduled_date, conducted_date) ASC, id DESC"
        return [dict(r) for r in conn.execute(q, args).fetchall()]
    finally:
        conn.close()


def get_audit(audit_id):
    conn = get_db()
    try:
        a = conn.execute("SELECT * FROM cmp_audits WHERE id=?", (audit_id,)).fetchone()
        if not a:
            return None
        findings = conn.execute(
            "SELECT * FROM cmp_findings WHERE audit_id=? ORDER BY "
            "CASE status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, due_date ASC",
            (audit_id,)).fetchall()
        return {"audit": dict(a), "findings": [dict(f) for f in findings]}
    finally:
        conn.close()


def list_certs():
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM cmp_certs ORDER BY expiry_date ASC").fetchall()]
    finally:
        conn.close()


def days_left(d):
    dt = _d(d)
    return (dt - _today()).days if dt else None


# --- writes ---------------------------------------------------------------
def create_audit(data, user):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO cmp_audits (scheme,audit_type,site,auditor,buyer,scheduled_date,"
            "conducted_date,valid_until,grade,status,report_ref,notes,created_by,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (data.get("scheme") or "Other", data.get("audit_type"), data.get("site"),
             data.get("auditor"), data.get("buyer"), data.get("scheduled_date") or None,
             data.get("conducted_date") or None, data.get("valid_until") or None,
             data.get("grade"), data.get("status") or "planned", data.get("report_ref"),
             data.get("notes"), (user or {}).get("username"), _now()))
        aid = cur.lastrowid
        conn.execute("UPDATE cmp_audits SET ref=? WHERE id=?", ("AUD-%04d" % aid, aid))
        conn.commit()
        return aid
    finally:
        conn.close()


def update_audit(audit_id, data, user):
    conn = get_db()
    try:
        fields = ["audit_type", "site", "auditor", "buyer", "scheduled_date",
                  "conducted_date", "valid_until", "grade", "status", "report_ref", "notes"]
        sets, args = [], []
        for f in fields:
            if f in data:
                sets.append(f"{f}=?"); args.append(data.get(f) or None)
        if "valid_until" in data:            # renewed/changed expiry -> re-arm the alarm
            sets.append("expiry_alerted=0")
        if not sets:
            return False
        sets.append("updated_at=?"); args.append(_now())
        args.append(audit_id)
        conn.execute("UPDATE cmp_audits SET " + ", ".join(sets) + " WHERE id=?", args)
        conn.commit()
        return True
    finally:
        conn.close()


def add_finding(audit_id, data, user):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO cmp_findings (audit_id,clause,finding,severity,owner,due_date,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (audit_id, data.get("clause"), data.get("finding"), data.get("severity") or "minor",
             data.get("owner"), data.get("due_date") or None, "open", _now()))
        conn.commit()
        return True
    finally:
        conn.close()


def set_finding_status(finding_id, status, evidence, user):
    conn = get_db()
    try:
        closed = _now() if status in ("closed", "verified") else None
        conn.execute(
            "UPDATE cmp_findings SET status=?, evidence=COALESCE(?,evidence), closed_at=?, "
            "overdue_alerted=CASE WHEN ? IN ('closed','verified') THEN overdue_alerted ELSE 0 END "
            "WHERE id=?", (status, evidence, closed, status, finding_id))
        conn.commit()
        return True
    finally:
        conn.close()


def create_cert(data, user):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO cmp_certs (name,cert_type,issuer,cert_no,scope,issue_date,expiry_date,"
            "status,doc_ref,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (data.get("name") or "Certificate", data.get("cert_type"), data.get("issuer"),
             data.get("cert_no"), data.get("scope"), data.get("issue_date") or None,
             data.get("expiry_date") or None, "valid", data.get("doc_ref"), data.get("notes"), _now()))
        conn.commit()
        return True
    finally:
        conn.close()


def renew_cert(cert_id, expiry_date, user):
    conn = get_db()
    try:
        conn.execute("UPDATE cmp_certs SET expiry_date=?, status='valid', expiry_alerted=0, updated_at=? "
                     "WHERE id=?", (expiry_date or None, _now(), cert_id))
        conn.commit()
        return True
    finally:
        conn.close()


# --- the sweep: raise bell alerts before things lapse ---------------------
def expiry_sweep():
    """Alert (once) on audits/certs within EXPIRY_WARN_DAYS of expiry or expired,
    and on newly-overdue CAP findings. Idempotent via *_alerted flags. Never raises."""
    warn = EXPIRY_WARN_DAYS
    try:
        conn = get_db()
    except Exception:
        return
    try:
        today = _today()
        # audits — completed ones with a validity date approaching/passed, not yet alerted
        for a in conn.execute(
                "SELECT id,ref,scheme,valid_until FROM cmp_audits "
                "WHERE valid_until IS NOT NULL AND expiry_alerted=0 AND status!='cancelled'").fetchall():
            dl = days_left(a["valid_until"])
            if dl is None:
                continue
            if dl < 0:
                _bell(conn, "critical", f"Audit expired: {a['scheme']}",
                      f"{a['ref']} ({a['scheme']}) expired on {a['valid_until']} — re-audit overdue.")
                conn.execute("UPDATE cmp_audits SET expiry_alerted=1 WHERE id=?", (a["id"],))
            elif dl <= warn:
                _bell(conn, "warning", f"Audit expiring: {a['scheme']}",
                      f"{a['ref']} ({a['scheme']}) expires in {dl} days ({a['valid_until']}) — book the re-audit.")
                conn.execute("UPDATE cmp_audits SET expiry_alerted=1 WHERE id=?", (a["id"],))
        # certificates
        for c in conn.execute(
                "SELECT id,name,expiry_date FROM cmp_certs "
                "WHERE expiry_date IS NOT NULL AND expiry_alerted=0 AND status!='revoked'").fetchall():
            dl = days_left(c["expiry_date"])
            if dl is None:
                continue
            if dl < 0:
                _bell(conn, "critical", f"Certificate expired: {c['name']}",
                      f"{c['name']} expired on {c['expiry_date']}.")
                conn.execute("UPDATE cmp_certs SET status='expired', expiry_alerted=1 WHERE id=?", (c["id"],))
            elif dl <= warn:
                _bell(conn, "warning", f"Certificate expiring: {c['name']}",
                      f"{c['name']} expires in {dl} days ({c['expiry_date']}).")
                conn.execute("UPDATE cmp_certs SET expiry_alerted=1 WHERE id=?", (c["id"],))
        # overdue corrective actions
        for f in conn.execute(
                "SELECT id,finding,due_date FROM cmp_findings "
                "WHERE due_date IS NOT NULL AND overdue_alerted=0 AND status IN ('open','in_progress')").fetchall():
            dl = days_left(f["due_date"])
            if dl is not None and dl < 0:
                _bell(conn, "warning", "Corrective action overdue",
                      f"CAP due {f['due_date']} is overdue: {(f['finding'] or '')[:80]}")
                conn.execute("UPDATE cmp_findings SET overdue_alerted=1 WHERE id=?", (f["id"],))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()


# --- dashboard KPIs -------------------------------------------------------
def dashboard():
    conn = get_db()
    try:
        today = _today()
        soon = str(today + timedelta(days=EXPIRY_WARN_DAYS))
        t = str(today)
        def one(sql, args=()):
            return conn.execute(sql, args).fetchone()["c"]
        d = {
            "audits_total": one("SELECT COUNT(*) c FROM cmp_audits"),
            "audits_upcoming": one("SELECT COUNT(*) c FROM cmp_audits WHERE status IN ('planned','scheduled')"),
            "audits_expiring": one("SELECT COUNT(*) c FROM cmp_audits WHERE valid_until IS NOT NULL AND valid_until<=? AND valid_until>=? AND status!='cancelled'", (soon, t)),
            "audits_expired": one("SELECT COUNT(*) c FROM cmp_audits WHERE valid_until IS NOT NULL AND valid_until<? AND status!='cancelled'", (t,)),
            "caps_open": one("SELECT COUNT(*) c FROM cmp_findings WHERE status IN ('open','in_progress')"),
            "caps_overdue": one("SELECT COUNT(*) c FROM cmp_findings WHERE status IN ('open','in_progress') AND due_date IS NOT NULL AND due_date<?", (t,)),
            "caps_critical": one("SELECT COUNT(*) c FROM cmp_findings WHERE status IN ('open','in_progress') AND severity IN ('critical','zero_tolerance')"),
            "certs_valid": one("SELECT COUNT(*) c FROM cmp_certs WHERE status='valid'"),
            "certs_expiring": one("SELECT COUNT(*) c FROM cmp_certs WHERE expiry_date IS NOT NULL AND expiry_date<=? AND expiry_date>=? AND status!='revoked'", (soon, t)),
            "certs_expired": one("SELECT COUNT(*) c FROM cmp_certs WHERE expiry_date IS NOT NULL AND expiry_date<? AND status!='revoked'", (t,)),
        }
        d["watch"] = _watchlist(conn, soon, t)
        d["open_caps"] = [dict(r) for r in conn.execute(
            "SELECT f.*, a.ref AS audit_ref, a.scheme FROM cmp_findings f JOIN cmp_audits a ON a.id=f.audit_id "
            "WHERE f.status IN ('open','in_progress') ORDER BY f.due_date ASC LIMIT 8").fetchall()]
        return d
    finally:
        conn.close()


def _watchlist(conn, soon, t):
    """Audits + certs expiring/expired, merged and sorted by soonest — the 'act now' list."""
    out = []
    for a in conn.execute(
            "SELECT ref,scheme,valid_until AS exp FROM cmp_audits WHERE valid_until IS NOT NULL "
            "AND valid_until<=? AND status!='cancelled' ORDER BY valid_until ASC LIMIT 12", (soon,)).fetchall():
        out.append({"kind": "audit", "label": f"{a['scheme']} ({a['ref']})", "exp": a["exp"],
                    "days": (_d(a["exp"]) - _today()).days if _d(a["exp"]) else None})
    for c in conn.execute(
            "SELECT name,expiry_date AS exp FROM cmp_certs WHERE expiry_date IS NOT NULL "
            "AND expiry_date<=? AND status!='revoked' ORDER BY expiry_date ASC LIMIT 12", (soon,)).fetchall():
        out.append({"kind": "certificate", "label": c["name"], "exp": c["exp"],
                    "days": (_d(c["exp"]) - _today()).days if _d(c["exp"]) else None})
    out.sort(key=lambda x: (x["days"] is None, x["days"] if x["days"] is not None else 0))
    return out[:12]
