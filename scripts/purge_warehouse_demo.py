"""Remove the warehouse DEMO seed rows (materials, rolls, finished goods and their seed
ledger rows) from a live database. Dry run by default; --apply deletes.

    python scripts/purge_warehouse_demo.py            # shows what would go
    python scripts/purge_warehouse_demo.py --apply    # deletes, in one transaction

A demo row is removed ONLY when nothing real ever touched it: notes = 'DEMO seed data',
every ledger row on it is a seed row (ref 'SEED'), no reservation, and no cutting lay or
bundle points at its rolls. Anything else is listed as kept, with the reason.
Works on SQLite (on-prem) and PostgreSQL (DATABASE_URL, e.g. the Render shell).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import get_db  # noqa: E402

DEMO = "DEMO seed data"


def _ids(rows):
    return [r["id"] for r in rows]


def _count(conn, sql, args):
    try:
        return conn.execute(sql, args).fetchone()["c"]
    except Exception:  # optional module table absent
        conn.rollback()
        return 0


def _in(ids):
    return ",".join("?" * len(ids))


def plan(conn):
    """-> (materials, rolls, fg) id lists to delete, and [(label, reason)] kept."""
    kept, mats, rolls = [], [], []
    for m in conn.execute("SELECT id, code FROM wh_materials WHERE notes=?", (DEMO,)).fetchall():
        rids = _ids(conn.execute("SELECT id FROM wh_rolls WHERE material_id=?", (m["id"],)).fetchall())
        q = "material_id=?" + (" OR roll_id IN (%s)" % _in(rids) if rids else "")
        args = (m["id"], *rids)
        why = None
        if _count(conn, "SELECT COUNT(*) c FROM wh_movements WHERE (%s) "
                        "AND COALESCE(ref,'')<>'SEED'" % q, args):
            why = "has real stock movements"
        elif _count(conn, "SELECT COUNT(*) c FROM wh_allocations WHERE material_id=?", (m["id"],)):
            why = "has reservations"
        elif _count(conn, "SELECT COUNT(*) c FROM wh_counts WHERE material_id=?", (m["id"],)):
            why = "has stock counts"
        elif _count(conn, "SELECT COUNT(*) c FROM wh_issue_requests WHERE material_id=?", (m["id"],)):
            why = "has internal-issue requests"
        elif rids and (_count(conn, "SELECT COUNT(*) c FROM cut_lay_rolls WHERE roll_id IN (%s)"
                                    % _in(rids), rids)
                       or _count(conn, "SELECT COUNT(*) c FROM sf_bundles WHERE roll_id IN (%s)"
                                       % _in(rids), rids)):
            why = "its rolls are used by cutting/bundles"
        if why:
            kept.append((m["code"], why))
        else:
            mats.append(m["id"])
            rolls += rids
    fg = []
    for f in conn.execute("SELECT id, style_code, color, size FROM wh_fg WHERE notes=?",
                          (DEMO,)).fetchall():
        if _count(conn, "SELECT COUNT(*) c FROM wh_movements WHERE fg_id=? "
                        "AND COALESCE(ref,'')<>'SEED'", (f["id"],)):
            kept.append(("FG %s/%s/%s" % (f["style_code"], f["color"], f["size"]),
                         "has real FG movements"))
        else:
            fg.append(f["id"])
    return mats, rolls, fg, kept


def purge(conn, mats, rolls, fg):
    if rolls:
        conn.execute("DELETE FROM wh_movements WHERE roll_id IN (%s)" % _in(rolls), rolls)
        conn.execute("DELETE FROM wh_rolls WHERE id IN (%s)" % _in(rolls), rolls)
    if mats:
        conn.execute("DELETE FROM wh_movements WHERE material_id IN (%s)" % _in(mats), mats)
        conn.execute("DELETE FROM wh_materials WHERE id IN (%s)" % _in(mats), mats)
    if fg:
        conn.execute("DELETE FROM wh_movements WHERE fg_id IN (%s)" % _in(fg), fg)
        conn.execute("DELETE FROM wh_fg WHERE id IN (%s)" % _in(fg), fg)


def main(apply):
    conn = get_db()
    try:
        mats, rolls, fg, kept = plan(conn)
        print("demo materials to remove: %d (rolls %d), demo FG rows to remove: %d"
              % (len(mats), len(rolls), len(fg)))
        for label, why in kept:
            print("  KEPT %s: %s" % (label, why))
        if not apply:
            print("dry run - nothing deleted. Re-run with --apply to delete.")
            return
        purge(conn, mats, rolls, fg)
        conn.commit()
        print("deleted.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
