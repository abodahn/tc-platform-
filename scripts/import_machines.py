# -*- coding: utf-8 -*-
"""
Import the machine register into maintenance (front door B).

    python scripts/import_machines.py "C:\\path\\mnt_machines.csv" [--dry-run]

Front door A is the admin screen at /maintenance/import (the "Machine register"
tab). Both call the SAME app.maintenance.machine_import.parse_machines +
upsert_machines, so the local build and production can never end up with
different import logic.

--dry-run parses and reports and writes NOTHING — run it first, always.
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in argv
    if not args:
        print(__doc__)
        return 2
    path = args[0]
    if not os.path.exists(path):
        print(f"not found: {path}")
        return 2

    from app.maintenance.machine_import import parse_machines, upsert_machines

    t0 = time.time()
    rows, stats = parse_machines(path)
    t_parse = time.time() - t0
    if stats.get("error"):
        print(f"ERROR: {stats['error']}")
        return 1

    print(f"parsed {stats['machines']} machines from {stats['rows']} rows in "
          f"{t_parse:.1f}s ({stats['no_serial']} keyed by card number, "
          f"{stats['in_register']} in the current 2023 register)")
    if stats["rejects"]:
        print(f"rejected {len(stats['rejects'])} rows:")
        by_reason = {}
        for r in stats["rejects"]:
            by_reason[r["reason"]] = by_reason.get(r["reason"], 0) + 1
        for reason, n in sorted(by_reason.items(), key=lambda kv: -kv[1]):
            print(f"    {n:>6}  {reason}")
        for r in stats["rejects"][:10]:
            print(f"    e.g. row {r['row']} {r['code']!r}: {r['reason']}")
    if dry:
        print("--dry-run: nothing written.")
        return 0

    from app import create_app
    app = create_app()
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        try:
            t1 = time.time()
            counts = upsert_machines(conn, rows, {"username": "cli"},
                                     source=os.path.basename(path)[:120])
        finally:
            conn.close()
    print(f"added {counts['added']}, updated {counts['updated']}, "
          f"unchanged {counts['unchanged']}, rejected {counts['rejected']} "
          f"in {time.time() - t1:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
