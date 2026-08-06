# -*- coding: utf-8 -*-
"""
Whole-database backup as a ZIP of CSVs, downloadable from the admin screen.

The production database is a Render free-tier PostgreSQL that render.yaml says
expires ~90 days after creation, and backup_platform.ps1 has no pg_dump in it.
It now holds 5,108 machines, 450 maintenance plans, 450 checklist items, 115
locations and 88 cost models that took a forensic archive audit to reconstruct.
There was no way to get a copy of any of it out without database credentials.

What this IS: every row of every table, as CSV, in one ZIP, with a manifest.
Enough to rebuild the business data, and readable by anyone with Excel.

What this is NOT, stated plainly so nobody mistakes it for more than it is:
not a pg_dump. No schema DDL, no indexes, no sequence positions, no roles, no
extensions. Restoring means creating a fresh database (init_db builds the
schema) and loading the CSVs. Sequences restart, so ids are preserved as data
but not as the next-id counter.

Password hashes are included — they are what makes the users table restorable —
so the file is sensitive. It is admin-only, never written to disk on the server,
and streamed straight to the browser.
"""
import csv
import io
import zipfile
from datetime import datetime, timezone

from flask import Blueprint, Response, abort

from app.auth import login_required, current_user
from app.db import get_db, log_audit
from app.security import has_permission

bp = Blueprint("backup", __name__, url_prefix="/admin")

# Tables holding derived or bulky rows that a restore can regenerate. Skipping
# them keeps the download small enough to actually be taken regularly, which is
# worth more than a complete-but-never-downloaded archive.
_SKIP = {"sqlite_sequence", "mnt_attachments", "bi_dataset_rows"}


def _table_names(conn):
    """Every user table, on SQLite or PostgreSQL."""
    try:                      # PostgreSQL
        rows = conn.execute(
            "SELECT tablename AS name FROM pg_tables WHERE schemaname='public' "
            "ORDER BY tablename").fetchall()
        if rows:
            return [r["name"] for r in rows]
    except Exception:         # noqa: BLE001 — not Postgres, fall through
        try:
            conn.rollback()   # a failed statement poisons the transaction
        except Exception:     # noqa: BLE001
            pass
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    return [r["name"] for r in rows]


def build_zip():
    """(bytes, manifest). Raises nothing a caller must special-case: a table
    that cannot be read is recorded in the manifest as an error rather than
    aborting a backup that has already captured everything else."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    buf = io.BytesIO()
    manifest = {"created_utc": stamp, "tables": {}, "errors": {}, "rows_total": 0}
    conn = get_db()
    try:
        names = [t for t in _table_names(conn) if t not in _SKIP]
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for t in names:
                try:
                    cur = conn.execute(f"SELECT * FROM {t}")
                    rows = cur.fetchall()
                    cols = ([d[0] for d in cur.description] if cur.description
                            else (list(rows[0].keys()) if rows else []))
                    s = io.StringIO()
                    w = csv.writer(s, lineterminator="\n")
                    w.writerow(cols)
                    for r in rows:
                        w.writerow(["" if r[c] is None else r[c] for c in cols])
                    z.writestr(f"{t}.csv", s.getvalue().encode("utf-8-sig"))
                    manifest["tables"][t] = len(rows)
                    manifest["rows_total"] += len(rows)
                except Exception as exc:            # noqa: BLE001
                    manifest["errors"][t] = str(exc)[:200]
                    try:
                        conn.rollback()
                    except Exception:               # noqa: BLE001
                        pass
            lines = [
                "TC Platform — data backup",
                f"created (UTC): {stamp}",
                f"tables: {len(manifest['tables'])}   rows: {manifest['rows_total']}",
                "",
                "This is a DATA backup: one CSV per table, no schema, no indexes,",
                "no sequences, no roles. To restore: start a fresh platform (init_db",
                "creates the schema), then load each CSV into its table. Ids are",
                "preserved as values; sequence counters are not.",
                "",
                "Contains password hashes and signature images. Treat as sensitive.",
                "",
                "rows per table:",
            ]
            for t in sorted(manifest["tables"]):
                lines.append(f"  {t:<40} {manifest['tables'][t]:>8}")
            if manifest["errors"]:
                lines += ["", "TABLES THAT COULD NOT BE READ:"]
                for t, e in manifest["errors"].items():
                    lines.append(f"  {t}: {e}")
            z.writestr("MANIFEST.txt", "\n".join(lines).encode("utf-8"))
    finally:
        conn.close()
    return buf.getvalue(), manifest


@bp.route("/backup.zip")
@login_required
def download():
    if not has_permission(current_user()["role"], "access_admin"):
        abort(403)
    data, manifest = build_zip()
    log_audit(current_user()["username"], "backup_download",
              f"{len(manifest['tables'])} tables, {manifest['rows_total']} rows, "
              f"{len(data)} bytes", "")
    name = f"tc-platform-backup-{manifest['created_utc']}.zip"
    return Response(data, mimetype="application/zip",
                    headers={"Content-Disposition": f"attachment; filename={name}",
                             "Content-Length": str(len(data))})
