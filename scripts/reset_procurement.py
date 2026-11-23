"""Reset procurement to day one: empty every request/PO/GRN/invoice/payment table and
restart document numbering, so the next request is PR-<year>-000001.
Dry run by default. Works on SQLite (on-prem) and PostgreSQL (DATABASE_URL, e.g. the
Render shell). Take a normal database backup first and run it in a quiet window.

    python scripts/reset_procurement.py                              # dry run: shows what would go
    python scripts/reset_procurement.py --apply --i-understand       # does it, in ONE transaction
    python scripts/reset_procurement.py --restore 20261122_0930      # puts the rows back
    python scripts/reset_procurement.py --db path/to/copy.db ...     # SQLite file other than platform.db

--apply, in one transaction (any error rolls back all of it):
  1. copies every row it deletes or changes into zz_backup_<YYYYMMDD_HHMM>_<table>
     (CREATE TABLE AS SELECT, same database) - that is what --restore reads. They
     hold copies of the uploaded files: DROP them once the restore window has passed;
  2. deletes the 18 pr_* transaction tables in child-first order, plus the procurement
     bell rows (notifications, mnt_notifications) and procurement AI alerts/predictions;
  3. restarts the id counters of the 18 tables (numbers come from row ids, see
     app/approvals/services.py doc_no) -> next PR/PO/GRN/DN/RFQ is ...-000001.
     PostgreSQL: every table touched is locked first (ACCESS EXCLUSIVE, so no request
     can land mid-reset) and counters restart with TRUNCATE ... RESTART IDENTITY, which
     a rollback undoes (setval would not).
     Invoice numbers are the vendor's own, nothing to reset;
  4. retags old stock references (wh_movements.ref, wh_rolls.grn_ref equal to an old
     PR/PO number -> 'TEST-PR-..'; other text such as a supplier PO is left alone),
     because the warehouse door and receipt corrections look stock up by that exact
     text and would match the NEW request that reuses the number. Quantities are not
     touched;
  5. unlinks item-master requests from old PR lines (proc_item_requests.pr_id/line_no);
  6. pauses maintenance auto-reorder (mnt_settings auto_reorder_pr='0'), otherwise the
     next page load raises a request for every low spare and the list is not empty.
     Turn it back on in Maintenance settings when ready.
Kept: vendors, item master, budgets, matrix, settings, delegations, forecasts, users,
roles, audit_logs, warehouse/spare stock and their ledgers, maintenance audit text.

Stock is NOT reversed, and there is deliberately no --reverse-stock flag. Test receipts
raised wh_materials/wh_rolls and mnt_spare_parts stock AND moved avg_cost through the
moving-average formula (warehouse/services.py _apply_avg_cost). That average cannot be
recomputed: it is path-dependent, a zero-price receipt records the then-average as its
unit cost, and opening averages from imports have no movement row. Spare receipts carry
no PR number at all, and received rolls may since have been cut or issued. Deleting
those movements would leave stock or cost silently wrong. The dry run prints what the
receipts added; set day-one stock with a physical count / stock adjustment instead.
The pr_stock_postings rows (the only exact map) are kept in the backup tables.

Attachments, quotes and invoice files live in the database (content_b64 columns), not
on disk, so there are no files to delete; the backup tables keep them.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Child-first. Most of these have no FK to pr_requests, so every one is deleted explicitly.
PR_TABLES = ["pr_rfq_excluded", "pr_item_issues", "pr_stock_postings", "pr_grn_quarantine",
             "pr_returns", "pr_grn", "pr_payments", "pr_invoices", "pr_po_revisions",
             "pr_purchase_orders", "pr_quote_lines", "pr_quotes", "pr_rfqs", "pr_attachments", "pr_sign_events",
             "pr_events", "pr_steps", "pr_items", "pr_requests"]
_PROC = "domain IN ('procurement','approvals')"
DELETES = [("notifications", "module='procurement' OR link LIKE '/procurement/pr/%'"),
           ("mnt_notifications", "action_url LIKE '/procurement/pr/%'"),
           ("ai_alerts", _PROC), ("ai_predictions", _PROC)] + [(t, "1=1") for t in PR_TABLES]
# only refs that ARE an old PR/PO number - a supplier/customer PO typed at the door stays as is
_OLD_NO = ("UPPER(TRIM({0})) IN (SELECT UPPER(pr_no) FROM pr_requests WHERE pr_no IS NOT NULL "
           "UNION SELECT UPPER(po_no) FROM pr_requests WHERE po_no IS NOT NULL "
           "UNION SELECT UPPER(po_no) FROM pr_purchase_orders WHERE po_no IS NOT NULL)")
# (table, key column, columns changed, which rows, SET clause)
UPDATES = [("wh_movements", "id", ["ref"], _OLD_NO.format("ref"), "ref='TEST-'||ref"),
           ("wh_rolls", "id", ["grn_ref"], _OLD_NO.format("grn_ref"), "grn_ref='TEST-'||grn_ref"),
           ("proc_item_requests", "id", ["pr_id", "line_no"],
            "pr_id IS NOT NULL OR line_no IS NOT NULL", "pr_id=NULL, line_no=NULL"),
           ("mnt_settings", "key", ["value"],
            "key='auto_reorder_pr' AND COALESCE(value,'1')<>'0'", "value='0'")]
KEPT = ["proc_vendors", "proc_items", "proc_off_catalogue", "proc_budgets", "proc_resp_matrix",
        "proc_settings", "proc_stage_meta", "proc_role_meta", "proc_escalations", "proc_doc",
        "proc_delegations", "proc_forecasts", "proc_item_requests", "users", "custom_roles",
        "audit_logs", "mnt_eng_justifications", "wh_materials", "wh_movements", "wh_rolls",
        "mnt_spare_parts", "mnt_stock_movements"]


def _pg():
    from app.db import _is_pg
    return _is_pg()


def tables(conn):
    sql = ("SELECT table_name AS name FROM information_schema.tables "
           "WHERE table_schema=current_schema()") if _pg() else \
        "SELECT name FROM sqlite_master WHERE type='table'"
    return {r["name"] for r in conn.execute(sql).fetchall()}


def _n(conn, table, where="1=1"):
    return conn.execute("SELECT COUNT(*) AS c FROM %s WHERE %s" % (table, where)).fetchone()["c"]


def _bk(stamp, table):
    return "zz_backup_%s_%s" % (stamp, table)


def _lock(conn, have):
    """PostgreSQL: lock every table the reset reads or writes, inside the caller's
    transaction, so no app write lands between backup, delete and renumber. Gives up
    after the session's lock_timeout (5s, app/db.py) and the caller rolls back."""
    if _pg():
        touched = dict.fromkeys(t for t in [u[0] for u in UPDATES] + [d[0] for d in DELETES]
                                if t in have)
        conn.execute("LOCK TABLE %s IN ACCESS EXCLUSIVE MODE" % ", ".join(touched))


def _cols(conn, table):
    return [d[0] for d in conn.execute("SELECT * FROM %s WHERE 1=0" % table).description]


def report(conn):
    """Print the plan. Changes nothing. Returns {table: rows} that --apply would delete."""
    have = tables(conn)
    plan = {t: _n(conn, t, w) for t, w in DELETES if t in have}
    print("WOULD DELETE (rows):")
    for t, w in DELETES:
        if t in plan:
            print("  %-20s %6d%s" % (t, plan[t], "" if w == "1=1" else "   where " + w))
    if "pr_requests" in have:
        r = conn.execute("SELECT MIN(pr_no) AS a, MAX(pr_no) AS b, MIN(po_no) AS c, "
                         "MAX(po_no) AS d FROM pr_requests").fetchone()
        print("  PR numbers %s .. %s;  PO numbers %s .. %s" % (r["a"], r["b"], r["c"], r["d"]))
    files = sum(_n(conn, t, "content_b64 IS NOT NULL") for t in
                ("pr_attachments", "pr_quotes", "pr_invoices") if t in have)
    print("  uploaded files: %d, all stored in the database (content_b64); none on disk" % files)
    print("WOULD CHANGE (rows, values kept in backup):")
    for t, _k, cols, w, s in UPDATES:
        if t in have:
            print("  %-20s %6d   SET %s" % (t, _n(conn, t, w), s))
    print("KEPT (rows):  " + ", ".join("%s %d" % (t, _n(conn, t)) for t in KEPT if t in have))
    print("STOCK added by procurement - left as is (set day-one stock by count/adjustment):")
    if "wh_movements" in have:
        for r in conn.execute(
                "SELECT m.code, m.name, w.type, SUM(w.qty) AS q, COUNT(*) AS n FROM wh_movements w "
                "JOIN wh_materials m ON m.id=w.material_id WHERE " + _OLD_NO.format("w.ref") +
                " GROUP BY m.code, m.name, w.type ORDER BY m.code").fetchall():
            print("  material %-12s %-30s %-20s %+g (%d movements)" % (
                r["code"], (r["name"] or "")[:30], r["type"], r["q"], r["n"]))
    if {"pr_stock_postings", "mnt_spare_parts"} <= have:
        for r in conn.execute(
                "SELECT s.code, s.name, SUM(p.qty) AS q FROM pr_stock_postings p JOIN "
                "mnt_spare_parts s ON s.id=p.target_id WHERE p.target='spare' AND "
                "p.status='posted' GROUP BY s.code, s.name ORDER BY s.code").fetchall():
            print("  spare    %-12s %-30s receipts %+g" % (r["code"], (r["name"] or "")[:30], r["q"]))
    if {"pr_item_issues", "mnt_spare_parts"} <= have:
        for r in conn.execute(
                "SELECT s.code, SUM(i.qty) AS q FROM pr_item_issues i JOIN mnt_spare_parts s "
                "ON s.id=i.spare_id WHERE i.reversed_at IS NULL GROUP BY s.code").fetchall():
            print("  spare    %-12s issued off the shelf against PRs -%g" % (r["code"], r["q"]))
    return plan


def apply(conn, stamp=None):
    """Back up, delete, reset numbering - one transaction. Returns the backup table names."""
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M")
    pg = _pg()
    conn.commit()
    if not pg:
        # IMMEDIATE: hold the write lock from the first count, not from the first write;
        # sqlite3 would otherwise autocommit the CREATE TABLEs
        conn.execute("BEGIN IMMEDIATE")
    try:
        have = tables(conn)
        if any(t.startswith(_bk(stamp, "")) for t in have):
            raise SystemExit("backup tables for %s already exist - wait a minute and re-run" % stamp)
        _lock(conn, have)
        made, done = [], []
        for t, k, cols, w, s in UPDATES:
            n = _n(conn, t, w) if t in have else 0
            if n:
                conn.execute("CREATE TABLE %s AS SELECT * FROM %s WHERE %s" % (_bk(stamp, t), t, w))
                conn.execute("UPDATE %s SET %s WHERE %s" % (t, s, w))
                made.append(_bk(stamp, t))
                done.append("changed %s %d" % (t, n))
        for t, w in DELETES:
            if t in have and _n(conn, t, w):
                conn.execute("CREATE TABLE %s AS SELECT * FROM %s WHERE %s" % (_bk(stamp, t), t, w))
                n = _n(conn, t, w)
                conn.execute("DELETE FROM %s WHERE %s" % (t, w))
                made.append(_bk(stamp, t))
                done.append("deleted %s %d" % (t, n))
        reset = [t for t in PR_TABLES if t in have]
        if pg:   # tables are empty here; unlike setval, TRUNCATE is undone by a rollback
            conn.execute("TRUNCATE %s RESTART IDENTITY" % ", ".join(reset))
        elif "sqlite_sequence" in have:
            conn.execute("DELETE FROM sqlite_sequence WHERE name IN (%s)"
                         % ",".join("?" * len(reset)), reset)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    print("APPLIED: " + "; ".join(done))
    print("numbering restarted for %d tables - next request is PR-%d-000001"
          % (len(reset), datetime.now().year))
    print("auto-reorder is paused (mnt_settings auto_reorder_pr=0); re-enable it in Maintenance settings.")
    print("backup (%s): %s" % (stamp, ", ".join(made) or "nothing to back up"))
    return made


def restore(conn, stamp):
    """Copy the zz_backup_<stamp>_* rows back (parents first). Drops nothing."""
    pg = _pg()
    conn.commit()
    if not pg:
        conn.execute("BEGIN IMMEDIATE")
    try:
        have = tables(conn)
        _lock(conn, have)
        if not any(t.startswith(_bk(stamp, "")) for t in have):
            raise SystemExit("no backup tables named %s*" % _bk(stamp, ""))
        busy = [t for t in PR_TABLES if t in have and _n(conn, t)]
        if busy:
            # new rows reuse the restored ids/numbers; refuse rather than merge two histories
            raise SystemExit("refused: procurement already has new rows in " + ", ".join(busy))
        done = []
        for t, _w in reversed(DELETES):
            b = _bk(stamp, t)
            if b in have and t in have:
                cols = ",".join(c for c in _cols(conn, b) if c in set(_cols(conn, t)))
                conn.execute("INSERT INTO %s (%s) SELECT %s FROM %s" % (t, cols, cols, b))
                done.append("%s %d" % (t, _n(conn, b)))
        for t, k, cols, _w, _s in UPDATES:
            b = _bk(stamp, t)
            if b in have and t in have:
                conn.execute("UPDATE %s SET %s WHERE %s IN (SELECT %s FROM %s)" % (
                    t, ", ".join("%s=(SELECT b.%s FROM %s b WHERE b.%s=%s.%s)" % (c, c, b, k, t, k)
                                 for c in cols), k, k, b))
                done.append("%s %d reverted" % (t, _n(conn, b)))
        if pg:   # explicit ids do not move a SERIAL; SQLite's AUTOINCREMENT follows on its own
            for t in PR_TABLES:
                if t in have:
                    conn.execute("SELECT setval(pg_get_serial_sequence(?, 'id'), "
                                 "COALESCE((SELECT MAX(id) FROM %s), 0) + 1, false)" % t, (t,))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    print("RESTORED from %s: %s" % (stamp, "; ".join(done)))
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reset procurement to day one (dry run by default).")
    ap.add_argument("--apply", action="store_true", help="delete (needs --i-understand)")
    ap.add_argument("--i-understand", action="store_true", dest="sure",
                    help="confirms --apply: every request, PO, GRN, invoice and payment goes")
    ap.add_argument("--restore", metavar="YYYYMMDD_HHMM", help="copy a backup back")
    ap.add_argument("--db", metavar="PATH", help="SQLite file (default: platform.db)")
    a = ap.parse_args(argv)
    if a.db:
        if not os.path.isfile(a.db):
            ap.error("no such database file: %s" % a.db)
        import config
        if config.Config.DATABASE_URL:   # get_db() would ignore --db and open PostgreSQL
            ap.error("--db given but DATABASE_URL is set; unset it to work on a SQLite file")
        config.Config.DB_PATH = a.db
    from app.db import get_db
    if a.apply and not a.sure:
        ap.error("--apply also needs --i-understand")
    print("database: %s" % ("PostgreSQL (DATABASE_URL)" if _pg() else
                            __import__("config").Config.DB_PATH))
    conn = get_db()
    try:
        if a.restore:
            restore(conn, a.restore)
            return
        report(conn)
        if not a.apply:
            print("dry run - nothing changed. Re-run with --apply --i-understand to reset.")
            return
        apply(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
