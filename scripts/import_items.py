# -*- coding: utf-8 -*-
"""
Import the ERP item master into the procurement catalogue (front door B).

    python scripts/import_items.py "C:\\path\\List Of Item.xls" [--dry-run]

Front door A is the admin upload screen at /procurement/catalogue. Both call the
SAME app.approvals.catalogue.parse_workbook + upsert_items, so the local /
on-prem build and production can never end up with different import logic.
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

    from app.approvals.catalogue import parse_workbook, upsert_items

    t0 = time.time()
    items, stats = parse_workbook(path)
    t_parse = time.time() - t0
    if stats.get("error"):
        print(f"ERROR: {stats['error']}")
        return 1

    print(f"parsed {stats['items']} items from {stats['rows']} rows in {t_parse:.1f}s "
          f"({stats['bands']} categories, {stats['blanks']} blank, "
          f"{stats['filter_rows']} filter, {stats['header_rows']} repeated header)")
    if stats["unmapped_units"]:
        print("UNMAPPED UNITS (rows NOT imported — map them in "
              "app/approvals/catalogue.py::UNIT_MAP):")
        for unit, n in sorted(stats["unmapped_units"].items(), key=lambda kv: -kv[1]):
            print(f"    {unit!r}: {n} rows")
    if stats["rejects"]:
        print(f"rejected {len(stats['rejects'])} rows:")
        for r in stats["rejects"][:20]:
            print(f"    row {r['row']} {r['code']!r}: {r['reason']}")
        if len(stats["rejects"]) > 20:
            print(f"    ... and {len(stats['rejects']) - 20} more")
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
            counts = upsert_items(conn, items, {"username": "cli"},
                                  source=os.path.basename(path)[:120])
        finally:
            conn.close()
    t_write = time.time() - t1
    print(f"added {counts['added']}, updated {counts['updated']}, "
          f"unchanged {counts['unchanged']}, "
          f"rejected {len(stats['rejects']) + counts['rejected']}")
    print(f"write {t_write:.1f}s · total {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
