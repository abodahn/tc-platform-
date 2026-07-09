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
