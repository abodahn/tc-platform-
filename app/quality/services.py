"""
Digital QMS services — the AQL decision is applied here, plus the DHU / RFT /
Pareto maths and the roll-ups every screen reads.

The formulas, stated once because every KPI below derives from them:
    DHU            = total defects found      / units inspected * 100
                     ("defects per hundred units" — a unit may carry several)
    Defective rate = defective units          / units inspected * 100
    RFT            = (units - defective units) / units inspected * 100
                     (right-first-time / first-pass yield; the complement of the
                      DEFECTIVE RATE, not of DHU)
    Pareto cum %   = running sum of each defect type's share of total defects,
                     ranked by qty descending
Because one unit can carry several defects, DHU >= defective rate always.
"""
from datetime import date, datetime

from app.db import get_db
from .constants import (DEFECT_SECTIONS, DEFECT_SEVERITY, DEFECT_TYPES, DHU_ACTION_LIMIT,
                        QC_STAGES, aql_plan, verdict as aql_verdict)


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _bell(conn, severity, title, message, link="/quality"):
    conn.execute("INSERT INTO notifications (severity,module,title,message,link,created_at) "
                 "VALUES (?,?,?,?,?,?)", (severity, "quality", title, message, link, _now()))


# --- validation -----------------------------------------------------------
def counts_of(units_inspected, defective_units):
    """The two counted numbers, validated once for every write path.
    Returns (units, defective) or raises ValueError(reason)."""
    try:
        units = float(units_inspected or 0)
        defective = float(defective_units or 0)
    except (TypeError, ValueError):
        raise ValueError("bad_number")
    if units != units or defective != defective:            # NaN
        raise ValueError("bad_number")
    if units < 0 or defective < 0:
        raise ValueError("negative")
    if defective > units:
        raise ValueError("defectives_exceed_units")         # a lot can't be worse than 100% bad
    return units, defective


# --- the maths ------------------------------------------------------------
def metrics(units_inspected, defective_units, total_defects):
    """DHU, defective rate and RFT for one set of counts. Zero units returns
    zeros — an inspection exists before anything has been counted, and a KPI
    strip must not be a ZeroDivisionError."""
    u = float(units_inspected or 0)
    if u <= 0:
        return {"units": 0.0, "defects": float(total_defects or 0), "dhu": 0.0,
                "defect_rate": 0.0, "rft": 0.0}
    # Defectives are validated on write, but a rate is meaningless outside 0..100%
    # so a legacy/hand-edited row cannot make the KPI strip read RFT -400%.
    d = min(max(0.0, float(defective_units or 0)), u)
    return {"units": u, "defects": float(total_defects or 0),
            "dhu": round(float(total_defects or 0) / u * 100, 2),
            "defect_rate": round(d / u * 100, 2),
            "rft": round((u - d) / u * 100, 2)}


def pareto(defect_rows):
    """Defect types ranked by qty with a running cumulative % — the few causes
    that drive most defects. Ties break on name so the ranking is stable."""
    agg = {}
    for r in defect_rows:
        agg[r["defect_type"]] = agg.get(r["defect_type"], 0.0) + float(r["qty"] or 0)
    total = sum(agg.values())
    out, run = [], 0.0
    for rank, (name, qty) in enumerate(sorted(agg.items(), key=lambda kv: (-kv[1], kv[0])), 1):
        run += qty
        out.append({"rank": rank, "defect_type": name, "qty": qty,
                    "pct": round(qty / total * 100, 2) if total else 0.0,
                    "cum_pct": round(run / total * 100, 2) if total else 0.0})
    return out


# --- reads ----------------------------------------------------------------
def list_inspections(order_id=None, stage=None, vrd=None, limit=None):
    conn = get_db()
    try:
        q = ("SELECT i.*, o.order_no, o.buyer, "
             "(SELECT COALESCE(SUM(d.qty),0) FROM qc_defects d WHERE d.inspection_id=i.id) AS defects "
             "FROM qc_inspections i LEFT JOIN ord_orders o ON o.id=i.order_id WHERE 1=1")
        args = []
        if order_id:
            # coerce: the filter arrives as a query-string str, and PostgreSQL
            # will not compare an integer column against text.
            try:
                args.append(int(order_id))
            except (TypeError, ValueError):
                return []                      # nonsense order filter matches nothing
            q += " AND i.order_id=?"
        if stage:
            q += " AND i.stage=?"; args.append(stage)
        if vrd:
            q += " AND i.verdict=?"; args.append(vrd)
        q += " ORDER BY i.inspection_date DESC, i.id DESC"
        if limit:
            q += " LIMIT ?"; args.append(int(limit))
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        for r in rows:
            r.update(metrics(r["units_inspected"], r["defective_units"], r["defects"]))
        return rows
    finally:
        conn.close()


def get_inspection(inspection_id):
    conn = get_db()
    try:
        i = conn.execute(
            "SELECT i.*, o.order_no, o.buyer, o.style_name FROM qc_inspections i "
            "LEFT JOIN ord_orders o ON o.id=i.order_id WHERE i.id=?", (inspection_id,)).fetchone()
        if not i:
            return None
        defects = [dict(d) for d in conn.execute(
            "SELECT * FROM qc_defects WHERE inspection_id=? ORDER BY qty DESC, id",
            (inspection_id,)).fetchall()]
        i = dict(i)
        total = sum(float(d["qty"] or 0) for d in defects)
        return {"inspection": i, "defects": defects, "pareto": pareto(defects),
                "m": metrics(i["units_inspected"], i["defective_units"], total)}
    finally:
        conn.close()


def order_options():
    """Live orders for the inspection form's picker (lot size prefills from qty)."""
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, order_no, buyer, style_name, qty FROM ord_orders "
            "WHERE status NOT IN ('closed','cancelled') ORDER BY ship_date ASC, id DESC "
            "LIMIT 100").fetchall()]
    finally:
        conn.close()


# --- writes ---------------------------------------------------------------
def create_inspection(data, user):
    """Create an inspection. The plan (code letter / sample / Ac / Re) and the
    verdict are COMPUTED from the AQL engine and frozen on the row.
    Raises ValueError(reason) on unusable input — the counts get exactly the same
    guards here as on update, or a bad lot enters the books at creation instead."""
    try:
        lot = float(data.get("lot_size") or 0)
        aql = float(data.get("aql") or 2.5)
    except (TypeError, ValueError):
        raise ValueError("bad_number")
    p = aql_plan(lot, aql)                       # raises on a NaN/inf lot or an unsupported AQL
    units, defective = counts_of(data.get("units_inspected"), data.get("defective_units"))
    stage = data.get("stage") or "final"
    if stage not in QC_STAGES:                   # the roll-ups group on it; keep it a picklist
        raise ValueError("bad_stage")
    v = aql_verdict(p["accept"], p["sample_size"], units, defective)
    conn = get_db()
    try:
        oid = int(data.get("order_id")) if (data.get("order_id") or "").isdigit() else None
        # Never store a link to an order that does not exist: the register renders
        # it as a hyperlink and by_order() would silently drop the inspection.
        if oid and not conn.execute("SELECT id FROM ord_orders WHERE id=?", (oid,)).fetchone():
            oid = None
        cur = conn.execute(
            "INSERT INTO qc_inspections (order_id,stage,lot_size,aql,code_letter,sample_size,"
            "accept_no,reject_no,units_inspected,defective_units,verdict,inspector,inspection_date,"
            "notes,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid,
             stage, p["lot_size"], aql, p["code_letter"], p["sample_size"],
             p["accept"], p["reject"], units, defective, v,
             data.get("inspector") or (user or {}).get("username"),
             data.get("inspection_date") or str(date.today()), data.get("notes"),
             (user or {}).get("username"), _now()))
        iid = cur.lastrowid
        conn.execute("UPDATE qc_inspections SET ref=? WHERE id=?", ("QC-%05d" % iid, iid))
        if v == "fail":
            _alert_fail(conn, iid)
        conn.commit()
        return iid
    finally:
        conn.close()


def record_result(inspection_id, units_inspected, defective_units, user, notes=None):
    """Enter/correct the counted result. Recomputes the verdict from the FROZEN
    plan — the verdict is never accepted from the form."""
    conn = get_db()
    try:
        i = conn.execute("SELECT * FROM qc_inspections WHERE id=?", (inspection_id,)).fetchone()
        if not i:
            return False, "not_found"
        try:
            units, defective = counts_of(units_inspected, defective_units)
        except ValueError as e:
            return False, str(e)
        v = aql_verdict(float(i["accept_no"] or 0), float(i["sample_size"] or 0), units, defective)
        conn.execute("UPDATE qc_inspections SET units_inspected=?, defective_units=?, verdict=?, "
                     "notes=COALESCE(?,notes), updated_at=? WHERE id=?",
                     (units, defective, v, notes or None, _now(), inspection_id))
        if v == "fail":
            _alert_fail(conn, inspection_id)
        else:
            # Re-arm: a lot corrected off 'fail' must bell again if it fails later.
            conn.execute("UPDATE qc_inspections SET fail_alerted=0 WHERE id=?", (inspection_id,))
        conn.commit()
        return True, v
    finally:
        conn.close()


def add_defect(inspection_id, data, user):
    """Add a defect line. Defect QTY is not defective UNITS — one unit can carry
    three defects — so this deliberately does not touch defective_units."""
    try:
        qty = float(data.get("qty") or 1)
    except (TypeError, ValueError):
        return False, "bad_qty"
    if not (0 < qty < float("inf")):        # also rejects NaN, which fails every comparison
        return False, "bad_qty"
    # Picklists, enforced server-side: free text fragments the Pareto into
    # singletons, and an unknown section grouping is a roll-up nobody can act on.
    dtype = (data.get("defect_type") or "").strip()
    if dtype not in DEFECT_TYPES:
        return False, "no_type"
    section = data.get("section") or None
    if section and section not in DEFECT_SECTIONS:
        return False, "bad_section"
    severity = data.get("severity") or "minor"
    if severity not in DEFECT_SEVERITY:
        return False, "bad_severity"
    conn = get_db()
    try:
        if not conn.execute("SELECT id FROM qc_inspections WHERE id=?", (inspection_id,)).fetchone():
            return False, "not_found"
        conn.execute(
            "INSERT INTO qc_defects (inspection_id,defect_type,section,qty,severity,notes,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (inspection_id, dtype, section, qty, severity, data.get("notes"), _now()))
        conn.commit()
        return True, "added"
    finally:
        conn.close()


def _alert_fail(conn, inspection_id):
    """A failed AQL lot is a shipment risk — bell it once (fail_alerted)."""
    i = conn.execute("SELECT * FROM qc_inspections WHERE id=? AND fail_alerted=0",
                     (inspection_id,)).fetchone()
    if not i:
        return
    o = conn.execute("SELECT order_no, buyer FROM ord_orders WHERE id=?",
                     (i["order_id"],)).fetchone() if i["order_id"] else None
    who = f"{o['order_no']} ({o['buyer']})" if o else (i["stage"] or "lot")
    _bell(conn, "critical", f"AQL FAIL — {i['ref'] or 'inspection'}",
          f"{who}: {i['defective_units']:g} defective in {i['units_inspected']:g} inspected "
          f"(AQL {i['aql']:g}, Ac {i['accept_no']:g}/Re {i['reject_no']:g}) — lot rejected.",
          f"/quality/inspections/{inspection_id}")
    conn.execute("UPDATE qc_inspections SET fail_alerted=1 WHERE id=?", (inspection_id,))


# --- roll-ups -------------------------------------------------------------
# Denominator discipline: DHU/RFT by section group on the INSPECTION stage —
# that is where the units were counted. A defect line's own `section` says where
# the fault was created, and that is what the Pareto ranks.
def _defects_by(conn, column):
    # units_inspected>0 mirrors the denominator below EXACTLY. Defects logged on a
    # lot whose units were never counted have no denominator, so counting them
    # would inflate that section's DHU out of thin air (and bell a false alarm).
    return {r["k"]: float(r["defects"] or 0) for r in conn.execute(
        f"SELECT i.{column} AS k, COALESCE(SUM(d.qty),0) AS defects FROM qc_defects d "
        f"JOIN qc_inspections i ON i.id=d.inspection_id WHERE i.units_inspected>0 "
        f"GROUP BY i.{column}").fetchall()}


def by_section():
    conn = get_db()
    try:
        dfx = _defects_by(conn, "stage")
        out = []
        for r in conn.execute(
                "SELECT stage, COUNT(*) AS inspections, COALESCE(SUM(units_inspected),0) AS units, "
                "COALESCE(SUM(defective_units),0) AS defective, "
                "SUM(CASE WHEN verdict='fail' THEN 1 ELSE 0 END) AS failed "
                "FROM qc_inspections WHERE units_inspected>0 GROUP BY stage").fetchall():
            row = {"section": r["stage"], "inspections": r["inspections"], "failed": r["failed"]}
            row.update(metrics(r["units"], r["defective"], dfx.get(r["stage"], 0.0)))
            out.append(row)
        out.sort(key=lambda x: -x["dhu"])
        return out
    finally:
        conn.close()


def by_order():
    conn = get_db()
    try:
        dfx = _defects_by(conn, "order_id")
        out = []
        for r in conn.execute(
                "SELECT i.order_id, o.order_no, o.buyer, COUNT(*) AS inspections, "
                "COALESCE(SUM(i.units_inspected),0) AS units, "
                "COALESCE(SUM(i.defective_units),0) AS defective, "
                "SUM(CASE WHEN i.verdict='fail' THEN 1 ELSE 0 END) AS failed "
                "FROM qc_inspections i JOIN ord_orders o ON o.id=i.order_id "
                "WHERE i.units_inspected>0 GROUP BY i.order_id, o.order_no, o.buyer").fetchall():
            row = {"order_id": r["order_id"], "order_no": r["order_no"], "buyer": r["buyer"],
                   "inspections": r["inspections"], "failed": r["failed"]}
            row.update(metrics(r["units"], r["defective"], dfx.get(r["order_id"], 0.0)))
            out.append(row)
        out.sort(key=lambda x: -x["dhu"])
        return out
    finally:
        conn.close()


def top_defects(limit=10):
    conn = get_db()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT defect_type, section, qty FROM qc_defects").fetchall()]
        return pareto(rows)[:limit]
    finally:
        conn.close()


def trend(limit=20):
    """Most recent inspections with their DHU/RFT — the quality trend line."""
    return list_inspections(limit=limit)


def dashboard():
    conn = get_db()
    try:
        def one(sql, a=()):
            return conn.execute(sql, a).fetchone()["c"] or 0
        total_units = one("SELECT COALESCE(SUM(units_inspected),0) c FROM qc_inspections")
        total_defective = one("SELECT COALESCE(SUM(defective_units),0) c FROM qc_inspections")
        # Same denominator discipline as by_section(): only defects on lots whose
        # units were actually counted belong in a rate.
        total_defects = one("SELECT COALESCE(SUM(d.qty),0) c FROM qc_defects d "
                            "JOIN qc_inspections i ON i.id=d.inspection_id "
                            "WHERE i.units_inspected>0")
        passed = one("SELECT COUNT(*) c FROM qc_inspections WHERE verdict='pass'")
        failed = one("SELECT COUNT(*) c FROM qc_inspections WHERE verdict='fail'")
        decided = passed + failed
        d = {
            "inspections": one("SELECT COUNT(*) c FROM qc_inspections"),
            "pending": one("SELECT COUNT(*) c FROM qc_inspections WHERE verdict='pending'"),
            "passed": passed, "failed": failed,
            # Pass rate is over DECIDED lots only — pending inspections are not failures.
            "pass_rate": round(passed / decided * 100, 1) if decided else 0.0,
            "critical_defects": one("SELECT COALESCE(SUM(qty),0) c FROM qc_defects WHERE severity='critical'"),
        }
        d.update(metrics(total_units, total_defective, total_defects))
        d["sections"] = by_section()
        d["top_defects"] = top_defects(8)
        d["trend"] = trend(10)
        return d
    finally:
        conn.close()


# --- the sweep ------------------------------------------------------------
def dhu_sweep():
    """Bell a warning for every section running above the DHU action limit.
    Deduplicated to one alert per section per day — this runs on every dashboard
    load and would otherwise flood the bell. Never raises."""
    try:
        conn = get_db()
    except Exception:
        return
    try:
        today = str(date.today())
        for s in by_section():
            if s["dhu"] <= DHU_ACTION_LIMIT or not s["section"]:
                continue
            title = f"DHU above limit: {s['section'].replace('_', ' ')}"
            if conn.execute("SELECT id FROM notifications WHERE module='quality' AND title=? "
                            "AND created_at>=?", (title, today)).fetchone():
                continue
            _bell(conn, "warning", title,
                  f"{s['section'].replace('_', ' ')} is running at DHU {s['dhu']} "
                  f"(limit {DHU_ACTION_LIMIT}) over {s['inspections']} inspections — "
                  f"RFT {s['rft']}%. Root-cause the top defect.", "/quality")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        conn.close()
