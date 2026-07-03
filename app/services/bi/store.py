"""
Persistence for the BI module: datasets, dashboards, alerts, digests.

Datasets store their rows as JSON (``data_json``) so they survive on both SQLite
(local) and Postgres (Render). A small in-process cache keeps a parsed copy so
re-querying for cross-filter/drill-down doesn't re-parse the JSON each time.
"""
from __future__ import annotations

import json

from app.db import get_db, log_audit  # noqa: F401
from app.db import utcnow

STORE_ROW_CAP = 30000   # rows persisted per dataset (read cap is higher)

# dataset_id -> {"columns":[...], "rows":[...], "profile":{...}, "quality":{...}, "meta":{...}}
_CACHE: dict[int, dict] = {}
_CACHE_MAX = 12


def _cache_put(ds_id, payload):
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[ds_id] = payload


# --- datasets ---------------------------------------------------------------
def save_dataset(name, filename, source, columns, rows, profile, quality, owner):
    rows = rows[:STORE_ROW_CAP]
    data_json = json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False,
                           separators=(",", ":"))
    ts = utcnow()
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO bi_datasets
               (name, filename, source, n_rows, n_cols, columns_json, data_json,
                quality_json, owner, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (name, filename, source, len(rows), len(columns),
             json.dumps(profile, ensure_ascii=False),
             data_json, json.dumps(quality, ensure_ascii=False), owner, ts))
        conn.commit()
        ds_id = cur.lastrowid
    finally:
        conn.close()
    # Cache a COMPLETE meta (same shape as get_dataset) so a freshly-saved
    # dataset served from cache has every field the templates expect (id, n_cols…).
    _cache_put(ds_id, {"columns": columns, "rows": rows, "profile": profile,
                       "quality": quality,
                       "meta": {"id": ds_id, "name": name, "filename": filename,
                                "source": source, "n_rows": len(rows),
                                "n_cols": len(columns), "created_at": ts, "owner": owner}})
    return ds_id


def get_dataset(ds_id):
    """Full payload (columns, rows, profile, quality, meta) or None."""
    ds_id = int(ds_id)
    if ds_id in _CACHE:
        return _CACHE[ds_id]
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM bi_datasets WHERE id=?", (ds_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    data = json.loads(row["data_json"] or '{"columns":[],"rows":[]}')
    payload = {
        "columns": data.get("columns", []),
        "rows": data.get("rows", []),
        "profile": json.loads(row["columns_json"] or "{}"),
        "quality": json.loads(row["quality_json"] or "{}"),
        "meta": {"id": ds_id, "name": row["name"], "filename": row["filename"],
                 "source": row["source"], "n_rows": row["n_rows"], "n_cols": row["n_cols"],
                 "created_at": row["created_at"], "owner": row["owner"]},
    }
    _cache_put(ds_id, payload)
    return payload


def list_datasets(owner=None, limit=100):
    """List datasets. If `owner` is given, only that owner's datasets are
    returned (pass None for an admin/all view)."""
    conn = get_db()
    try:
        base = ("SELECT id, name, filename, source, n_rows, n_cols, owner, created_at "
                "FROM bi_datasets")
        if owner:
            rows = conn.execute(base + " WHERE owner=? ORDER BY id DESC LIMIT ?",
                                (owner, limit)).fetchall()
        else:
            rows = conn.execute(base + " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_dataset(ds_id):
    ds_id = int(ds_id)
    conn = get_db()
    try:
        conn.execute("DELETE FROM bi_dashboards WHERE dataset_id=?", (ds_id,))
        conn.execute("DELETE FROM bi_alerts WHERE dataset_id=?", (ds_id,))
        conn.execute("DELETE FROM bi_datasets WHERE id=?", (ds_id,))
        conn.commit()
    finally:
        conn.close()
    _CACHE.pop(ds_id, None)


# --- dashboards -------------------------------------------------------------
def save_dashboard(name, dataset_id, spec, owner, lang="en", dash_id=None):
    conn = get_db()
    try:
        if dash_id:
            conn.execute(
                "UPDATE bi_dashboards SET name=?, spec_json=?, lang=?, updated_at=? WHERE id=?",
                (name, json.dumps(spec, ensure_ascii=False), lang, utcnow(), int(dash_id)))
            conn.commit()
            new_id = int(dash_id)
        else:
            cur = conn.execute(
                """INSERT INTO bi_dashboards
                   (name, dataset_id, spec_json, owner, is_pinned, lang, created_at, updated_at)
                   VALUES (?,?,?,?,0,?,?,?)""",
                (name, int(dataset_id), json.dumps(spec, ensure_ascii=False), owner,
                 lang, utcnow(), utcnow()))
            conn.commit()
            new_id = cur.lastrowid
    finally:
        conn.close()
    return new_id


def get_dashboard(dash_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM bi_dashboards WHERE id=?", (int(dash_id),)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    d["spec"] = json.loads(d.get("spec_json") or "{}")
    return d


def list_dashboards(owner=None, pinned_only=False, limit=100):
    """List dashboards, optionally scoped to `owner` (None = admin/all view)."""
    conn = get_db()
    try:
        sql = "SELECT id, name, dataset_id, owner, is_pinned, lang, updated_at FROM bi_dashboards"
        clauses, params = [], []
        if pinned_only:
            clauses.append("is_pinned=1")
        if owner:
            clauses.append("owner=?")
            params.append(owner)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY is_pinned DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def pin_dashboard(dash_id, pinned):
    conn = get_db()
    try:
        conn.execute("UPDATE bi_dashboards SET is_pinned=? WHERE id=?",
                     (1 if pinned else 0, int(dash_id)))
        conn.commit()
    finally:
        conn.close()


def delete_dashboard(dash_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM bi_digests WHERE dashboard_id=?", (int(dash_id),))
        conn.execute("DELETE FROM bi_dashboards WHERE id=?", (int(dash_id),))
        conn.commit()
    finally:
        conn.close()


# --- alerts -----------------------------------------------------------------
def create_alert(name, dataset_id, column_name, agg, op, threshold, owner):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO bi_alerts
               (name, dataset_id, column_name, agg, op, threshold, last_state, alerted, owner, created_at)
               VALUES (?,?,?,?,?,?,'ok',0,?,?)""",
            (name, int(dataset_id), column_name, agg, op, float(threshold), owner, utcnow()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_alerts(dataset_id=None):
    conn = get_db()
    try:
        if dataset_id:
            rows = conn.execute("SELECT * FROM bi_alerts WHERE dataset_id=? ORDER BY id DESC",
                                (int(dataset_id),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM bi_alerts ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_alert(alert_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM bi_alerts WHERE id=?", (int(alert_id),))
        conn.commit()
    finally:
        conn.close()


def update_alert_state(alert_id, last_value, last_state, alerted):
    conn = get_db()
    try:
        conn.execute("UPDATE bi_alerts SET last_value=?, last_state=?, alerted=? WHERE id=?",
                     (float(last_value), last_state, 1 if alerted else 0, int(alert_id)))
        conn.commit()
    finally:
        conn.close()


# --- digests ----------------------------------------------------------------
def create_digest(dashboard_id, recipients, cadence, owner):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO bi_digests (dashboard_id, recipients, cadence, owner, created_at)
               VALUES (?,?,?,?,?)""",
            (int(dashboard_id), recipients, cadence, owner, utcnow()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_digests():
    conn = get_db()
    try:
        rows = conn.execute("SELECT * FROM bi_digests ORDER BY id DESC").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def delete_digest(digest_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM bi_digests WHERE id=?", (int(digest_id),))
        conn.commit()
    finally:
        conn.close()


def mark_digest_sent(digest_id):
    conn = get_db()
    try:
        conn.execute("UPDATE bi_digests SET last_sent=? WHERE id=?", (utcnow(), int(digest_id)))
        conn.commit()
    finally:
        conn.close()
