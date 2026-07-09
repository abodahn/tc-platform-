"""Smart Factory (MES) — read/aggregate services + write helpers."""
from datetime import date

from app.db import get_db, utcnow


def _rows(conn, sql, p=()):
    try:
        return [dict(r) for r in conn.execute(sql, p).fetchall()]
    except Exception:  # noqa: BLE001
        return []


def rag(pct):
    return "green" if pct >= 95 else ("amber" if pct >= 80 else "red")


def dashboard():
    """Live command-center data: KPIs, per-line RAG, defect pareto, sustainability."""
    conn = get_db()
    try:
        entries = _rows(conn, "SELECT * FROM sf_prod_entries")
        lines = {r["id"]: r for r in _rows(conn, "SELECT id,name,area FROM production_lines")}
        by_line = {}
        for e in entries:
            b = by_line.setdefault(e["line_id"], {"target": 0, "actual": 0, "lost": 0})
            b["target"] += e["target_qty"] or 0
            b["actual"] += e["actual_qty"] or 0
            b["lost"] += e["lost_min"] or 0
        live = []
        for lid, b in by_line.items():
            eff = round(100 * b["actual"] / b["target"], 1) if b["target"] else 0
            live.append({"line": (lines.get(lid) or {}).get("name", f"Line {lid}"),
                         "target": b["target"], "actual": b["actual"], "lost": b["lost"],
                         "efficiency": eff, "rag": rag(eff)})
        live.sort(key=lambda x: x["efficiency"])

        tgt = sum(b["target"] for b in by_line.values())
        act = sum(b["actual"] for b in by_line.values())
        eff = round(100 * act / tgt, 1) if tgt else 0

        q = _rows(conn, "SELECT COALESCE(SUM(inspected),0) i, COALESCE(SUM(defect),0) d, "
                        "COALESCE(SUM(reject),0) rj, COALESCE(SUM(rework),0) rw FROM sf_quality")
        qi = q[0]["i"] if q else 0
        qd = q[0]["d"] if q else 0
        dhu = round(100 * qd / qi, 2) if qi else 0
        rft = round(100 * (qi - qd) / qi, 2) if qi else 0

        pareto = _rows(conn, "SELECT code, SUM(qty) qty FROM sf_defects GROUP BY code ORDER BY SUM(qty) DESC LIMIT 8")
        wash = _rows(conn, "SELECT COALESCE(SUM(water_l),0) w, COALESCE(SUM(energy_kwh),0) e, "
                           "COALESCE(SUM(chemical_kg),0) c, COALESCE(SUM(pieces),0) p, COALESCE(SUM(rewash),0) rw FROM sf_wash_batches")
        w = wash[0] if wash else {"w": 0, "e": 0, "c": 0, "p": 0, "rw": 0}
        pieces = w["p"] or 0
        sustain = {
            "water_per_pc": round(w["w"] / pieces, 1) if pieces else 0,
            "energy_per_pc": round(w["e"] / pieces, 2) if pieces else 0,
            "chemical_per_pc": round(w["c"] * 1000 / pieces, 1) if pieces else 0,  # grams/pc
            "rewash": w["rw"] or 0,
        }
        orders = conn.execute("SELECT COUNT(*) c FROM sf_orders").fetchone()["c"]

        return {
            "kpis": {"efficiency": eff, "produced": act, "target": tgt, "dhu": dhu, "rft": rft,
                     "reject": (q[0]["rj"] if q else 0), "orders": orders},
            "live": live, "pareto": pareto, "sustain": sustain,
        }
    finally:
        conn.close()


def list_orders():
    conn = get_db()
    try:
        return _rows(conn, "SELECT o.*, s.code style_code, s.name style_name, s.smv "
                           "FROM sf_orders o LEFT JOIN sf_styles s ON s.id=o.style_id ORDER BY o.id DESC")
    finally:
        conn.close()


def list_lines():
    conn = get_db()
    try:
        return _rows(conn, "SELECT id,name,area FROM production_lines ORDER BY id")
    finally:
        conn.close()


def list_production(limit=200):
    conn = get_db()
    try:
        return _rows(conn, "SELECT e.*, l.name line_name, o.po_no FROM sf_prod_entries e "
                           "LEFT JOIN production_lines l ON l.id=e.line_id "
                           "LEFT JOIN sf_orders o ON o.id=e.order_id ORDER BY e.id DESC LIMIT ?", (limit,))
    finally:
        conn.close()


def add_production(line_id, order_id, hour_slot, target, actual, lost, operator, user=""):
    conn = get_db()
    try:
        conn.execute("INSERT INTO sf_prod_entries (line_id,order_id,hour_slot,target_qty,actual_qty,lost_min,operator,entry_date,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (line_id, order_id or None, hour_slot, int(target or 0), int(actual or 0),
                      int(lost or 0), operator, date.today().isoformat(), utcnow()))
        conn.commit()
    finally:
        conn.close()


def list_quality(limit=200):
    conn = get_db()
    try:
        return _rows(conn, "SELECT q.*, l.name line_name FROM sf_quality q "
                           "LEFT JOIN production_lines l ON l.id=q.line_id ORDER BY q.id DESC LIMIT ?", (limit,))
    finally:
        conn.close()


# ---------------- Cutting & bundles ----------------
def bundles():
    conn = get_db()
    try:
        rows = _rows(conn, "SELECT b.*, o.po_no, r.roll_no FROM sf_bundles b "
                           "LEFT JOIN sf_orders o ON o.id=b.order_id "
                           "LEFT JOIN sf_fabric_rolls r ON r.id=b.roll_id ORDER BY b.id DESC LIMIT 300")
        rolls = _rows(conn, "SELECT * FROM sf_fabric_rolls ORDER BY id DESC")
        by_stage = {}
        pieces = 0
        for b in rows:
            by_stage[b["status"]] = by_stage.get(b["status"], 0) + 1
            pieces += b["qty"] or 0
        return {"bundles": rows, "rolls": rolls, "by_stage": by_stage, "pieces": pieces, "count": len(rows)}
    finally:
        conn.close()


def add_bundle(order_id, roll_id, size, color, qty, operation_at):
    conn = get_db()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM sf_bundles").fetchone()["c"] + 2001
        conn.execute("INSERT INTO sf_bundles (bundle_no,order_id,roll_id,size,color,qty,operation_at,status,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (f"BND-{n}", order_id or None, roll_id or None, size, color, int(qty or 0), operation_at, "cut", utcnow()))
        conn.commit()
    finally:
        conn.close()


# ---------------- Laundry / wash ----------------
def wash_list():
    conn = get_db()
    try:
        rows = _rows(conn, "SELECT w.*, o.po_no FROM sf_wash_batches w LEFT JOIN sf_orders o ON o.id=w.order_id ORDER BY w.id DESC")
        tot_p = sum(r["pieces"] or 0 for r in rows) or 0
        agg = {
            "water": sum(r["water_l"] or 0 for r in rows), "energy": sum(r["energy_kwh"] or 0 for r in rows),
            "chem": sum(r["chemical_kg"] or 0 for r in rows), "rewash": sum(r["rewash"] or 0 for r in rows),
            "pieces": tot_p,
            "water_pc": round(sum(r["water_l"] or 0 for r in rows) / tot_p, 1) if tot_p else 0,
            "energy_pc": round(sum(r["energy_kwh"] or 0 for r in rows) / tot_p, 2) if tot_p else 0,
        }
        return {"rows": rows, "agg": agg}
    finally:
        conn.close()


def add_wash(order_id, recipe, water, energy, chemical, pieces, shade, rewash):
    conn = get_db()
    try:
        n = conn.execute("SELECT COUNT(*) c FROM sf_wash_batches").fetchone()["c"] + 9001
        conn.execute("INSERT INTO sf_wash_batches (batch_no,order_id,recipe,water_l,energy_kwh,chemical_kg,pieces,shade,rewash,status,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (f"WB-{n}", order_id or None, recipe, float(water or 0), float(energy or 0),
                      float(chemical or 0), int(pieces or 0), shade, int(rewash or 0), "done", utcnow()))
        conn.commit()
    finally:
        conn.close()


# ---------------- Workforce / operator efficiency ----------------
def workforce():
    """Operator scorecards from production entries (efficiency = actual/target)."""
    conn = get_db()
    try:
        ops = {o["code"]: o for o in _rows(conn, "SELECT * FROM sf_operators")}
        entries = _rows(conn, "SELECT operator, SUM(target_qty) t, SUM(actual_qty) a, COUNT(*) h "
                              "FROM sf_prod_entries WHERE operator IS NOT NULL AND operator<>'' GROUP BY operator")
        out = []
        for e in entries:
            eff = round(100 * (e["a"] or 0) / e["t"], 1) if e["t"] else 0
            o = ops.get(e["operator"], {})
            out.append({"code": e["operator"], "name": o.get("name", e["operator"]), "grade": o.get("grade", "-"),
                        "hours": e["h"], "produced": e["a"] or 0, "efficiency": eff, "rag": rag(eff)})
        out.sort(key=lambda x: x["efficiency"])
        avg = round(sum(x["efficiency"] for x in out) / len(out), 1) if out else 0
        return {"rows": out, "avg": avg, "count": len(out)}
    finally:
        conn.close()


# ---------------- Costing (cost-per-piece) ----------------
_RATE = {"labor_per_min": 0.35, "rework_per_pc": 1.2, "downtime_per_min": 0.9,
         "water_per_l": 0.006, "energy_per_kwh": 0.12, "chem_per_kg": 3.5}


def costing():
    conn = get_db()
    try:
        orders = _rows(conn, "SELECT o.*, s.smv, s.code style_code FROM sf_orders o LEFT JOIN sf_styles s ON s.id=o.style_id")
        out = []
        for o in orders:
            oid = o["id"]
            prod = conn.execute("SELECT COALESCE(SUM(actual_qty),0) a, COALESCE(SUM(lost_min),0) l FROM sf_prod_entries WHERE order_id=?", (oid,)).fetchone()
            produced = prod["a"] or 0
            downtime = prod["l"] or 0
            rework = conn.execute("SELECT COALESCE(SUM(rework),0) r FROM sf_quality WHERE order_id=?", (oid,)).fetchone()["r"] or 0
            wash = conn.execute("SELECT COALESCE(SUM(water_l),0) w, COALESCE(SUM(energy_kwh),0) e, COALESCE(SUM(chemical_kg),0) c FROM sf_wash_batches WHERE order_id=?", (oid,)).fetchone()
            smv = o["smv"] or 0
            labor = produced * smv * _RATE["labor_per_min"]
            rework_c = rework * _RATE["rework_per_pc"]
            downtime_c = downtime * _RATE["downtime_per_min"]
            wash_c = (wash["w"] or 0) * _RATE["water_per_l"] + (wash["e"] or 0) * _RATE["energy_per_kwh"] + (wash["c"] or 0) * _RATE["chem_per_kg"]
            total = labor + rework_c + downtime_c + wash_c
            cpp = round(total / produced, 2) if produced else 0
            out.append({"po_no": o["po_no"], "style": o["style_code"], "produced": produced,
                        "labor": round(labor, 0), "rework": round(rework_c, 0), "downtime": round(downtime_c, 0),
                        "wash": round(wash_c, 0), "total": round(total, 0), "cpp": cpp})
        return {"rows": out, "rates": _RATE}
    finally:
        conn.close()


# ---------------- Money-first + explainability (best-in-market differentiators) ----------------
CPM_LOADED = 4.0     # loaded line cost per minute (EGP) — configurable
REJECT_PER_PC = 6.0  # scrap cost per rejected piece


def _avg_smv(conn):
    r = conn.execute("SELECT AVG(smv) a FROM sf_styles WHERE smv>0").fetchone()
    return (r["a"] or 25) if r else 25


def minute_bank():
    """Price every idle, rework and reject minute — 'money left on the floor'."""
    conn = get_db()
    try:
        lines = {l["id"]: l for l in _rows(conn, "SELECT id,name FROM production_lines")}
        prod = {}
        for e in _rows(conn, "SELECT line_id, SUM(lost_min) lm FROM sf_prod_entries GROUP BY line_id"):
            prod[e["line_id"]] = e["lm"] or 0
        ql = {}
        for q in _rows(conn, "SELECT line_id, SUM(rework) rw, SUM(reject) rj FROM sf_quality GROUP BY line_id"):
            ql[q["line_id"]] = (q["rw"] or 0, q["rj"] or 0)
        rows = []
        tot_idle = tot_rw = tot_rj = 0
        for lid in set(list(prod) + list(ql)):
            lost = prod.get(lid, 0)
            rw, rj = ql.get(lid, (0, 0))
            idle = lost * CPM_LOADED
            rwc = rw * _RATE["rework_per_pc"]
            rjc = rj * REJECT_PER_PC
            tot_idle += idle; tot_rw += rwc; tot_rj += rjc
            rows.append({"line": (lines.get(lid) or {}).get("name", f"Line {lid}"),
                         "lost_min": lost, "idle": round(idle), "rework": round(rwc),
                         "reject": round(rjc), "total": round(idle + rwc + rjc)})
        rows.sort(key=lambda x: x["total"], reverse=True)
        return {"rows": rows, "total": round(tot_idle + tot_rw + tot_rj),
                "breakdown": {"idle": round(tot_idle), "rework": round(tot_rw), "reject": round(tot_rj)}}
    finally:
        conn.close()


def efficiency_bridge():
    """Decompose the target→actual gap into named, quantified causes (minutes + money)."""
    conn = get_db()
    try:
        pe = conn.execute("SELECT COALESCE(SUM(target_qty),0) t, COALESCE(SUM(actual_qty),0) a, COALESCE(SUM(lost_min),0) l FROM sf_prod_entries").fetchone()
        target, actual, lost = pe["t"] or 0, pe["a"] or 0, pe["l"] or 0
        q = conn.execute("SELECT COALESCE(SUM(rework),0) rw, COALESCE(SUM(reject),0) rj FROM sf_quality").fetchone()
        rework, reject = q["rw"] or 0, q["rj"] or 0
        smv = _avg_smv(conn)
        eff = round(100 * actual / target, 1) if target else 0
        gap_pcs = max(0, target - actual)
        gap_min = gap_pcs * smv
        lost_time_min = lost
        quality_min = (rework + reject) * smv
        balance_min = max(0, gap_min - lost_time_min - quality_min)
        comps = [
            {"name": "Downtime / no-feeding", "minutes": round(lost_time_min), "money": round(lost_time_min * CPM_LOADED)},
            {"name": "Quality redo (rework + reject)", "minutes": round(quality_min), "money": round(quality_min * CPM_LOADED)},
            {"name": "Line balance / bottleneck", "minutes": round(balance_min), "money": round(balance_min * CPM_LOADED)},
        ]
        tot_min = sum(c["minutes"] for c in comps) or 1
        for c in comps:
            c["pct"] = round(100 * c["minutes"] / tot_min, 1)
        return {"efficiency": eff, "gap_pcs": gap_pcs, "components": comps,
                "total_money": round(sum(c["money"] for c in comps))}
    finally:
        conn.close()


def ship_risk():
    """Per-order P(pass buyer AQL) estimate from live quality (AQL 2.5 acceptable ~2.5% DHU)."""
    conn = get_db()
    try:
        rows = _rows(conn, "SELECT q.order_id, o.po_no, SUM(q.inspected) insp, SUM(q.defect) defq "
                           "FROM sf_quality q LEFT JOIN sf_orders o ON o.id=q.order_id GROUP BY q.order_id")
        top = _rows(conn, "SELECT code, SUM(qty) qty FROM sf_defects GROUP BY code ORDER BY SUM(qty) DESC LIMIT 1")
        driving = top[0]["code"] if top else "—"
        out = []
        for r in rows:
            insp = r["insp"] or 0
            dhu = (100 * (r["defq"] or 0) / insp) if insp else 0
            risk = max(0, min(100, (dhu - 2.5) * 18))
            passp = round(100 - risk, 1)
            out.append({"po_no": r["po_no"] or "—", "dhu": round(dhu, 1), "pass": passp,
                        "rag": "green" if passp >= 90 else "amber" if passp >= 70 else "red",
                        "driving": driving})
        out.sort(key=lambda x: x["pass"])
        return out
    finally:
        conn.close()


# ---------------- AI insights (rule-based, LLM-ready) ----------------
def ai_insights():
    d = dashboard()
    out = []
    for l in d["live"]:
        if l["efficiency"] < 85:
            out.append({"severity": "high", "title": f"Line below target: {l['line']}",
                        "detail": f"Efficiency {l['efficiency']}% ({l['actual']}/{l['target']}, lost {l['lost']}m). Likely a bottleneck operation.",
                        "action": "Rebalance the line / reassign an operator to the slow operation."})
            break
    if d["kpis"]["dhu"] > 5:
        top = d["pareto"][0]["code"] if d["pareto"] else "stitching"
        out.append({"severity": "medium", "title": f"DHU above 5% ({d['kpis']['dhu']})",
                    "detail": f"Top defect: {top}. Right-first-time is {d['kpis']['rft']}%.",
                    "action": f"Brief the line on {top}; open a root-cause review."})
    if d["sustain"]["rewash"] >= 15:
        out.append({"severity": "high", "title": f"High rewash in laundry ({d['sustain']['rewash']})",
                    "detail": "Rewash wastes water, energy and time and risks shade variance.",
                    "action": "Review the wash recipe & shade band; check load consistency."})
    wf = workforce()
    if wf["rows"] and wf["rows"][0]["efficiency"] < 75:
        w = wf["rows"][0]
        out.append({"severity": "medium", "title": f"Low operator efficiency: {w['name']}",
                    "detail": f"{w['efficiency']}% over {w['hours']} hours (grade {w['grade']}).",
                    "action": "Coach or reassign; check the operation-to-skill match."})
    if not out:
        out.append({"severity": "info", "title": "All key metrics nominal", "detail": "No anomalies detected this run.", "action": "—"})
    return out


def add_quality(line_id, order_id, stage, inspected, defect, rework, reject, inspector, defects=None):
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO sf_quality (line_id,order_id,stage,inspected,defect,rework,reject,inspector,created_at) "
                           "VALUES (?,?,?,?,?,?,?,?,?)",
                           (line_id, order_id or None, stage, int(inspected or 0), int(defect or 0),
                            int(rework or 0), int(reject or 0), inspector, utcnow()))
        cid = cur.lastrowid
        for d in (defects or []):
            if d.get("code") and int(d.get("qty") or 0) > 0:
                conn.execute("INSERT INTO sf_defects (check_id,code,category,qty) VALUES (?,?,?,?)",
                             (cid, d["code"][:40], d.get("category", "stitching")[:40], int(d["qty"])))
        conn.commit()
    finally:
        conn.close()
