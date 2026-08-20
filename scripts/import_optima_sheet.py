# -*- coding: utf-8 -*-
"""Import the store's Optima sheet (code optima | item | desc) into the catalogue.

    python scripts/import_optima_sheet.py "<file.xlsx>"            # dry run
    python scripts/import_optima_sheet.py "<file.xlsx>" --apply    # write it

THE SHEET. "شيت صرف واضافه بنود كل مخزن" holds TWO independent lists pasted side
by side. Only the first — columns A, B, C, headed `code optima | item | desc` —
is the item master. Columns D/E/G are a second store's list that does not line up
row-wise with the first (row 1800's A/B/C describe a main shaft while its D/E/G
describe a sensor), so reading across a row would invent items that do not exist.
This importer reads A, B and C and nothing else.

THE MAPPING.
    code = A, the Optima code. A row without one is not an item.
    name = C, the description.
           When C is empty, B is used INSTEAD — but only when B is a real name.
           In 387 rows B is simply column A repeated, and a code is not a name;
           those fall through and the row is rejected rather than creating an
           item called "1479400".

MERGE, NEVER REPLACE. Upsert is by code, through the same upsert_items() the
admin import screen uses, so the two doors cannot drift. Anything already in the
catalogue and absent from this sheet is left exactly as it is — nothing is
retired, nothing is deleted.

NO PRICES. The sheet states none, and upsert_items treats a blank as "do not
overwrite", so an item that already carries a cost keeps it and a new one is born
unpriced. Value enters the platform at the Purchasing pricing gate, not here.
"""
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

HEADER_CODE = "code optima"


def _s(v):
    return str(v).strip() if v is not None else ""


def read_rows(path):
    """(items, stats). Reads columns A/B/C only, from the row after the header."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]

    rows = list(ws.iter_rows(min_row=1, max_col=3, values_only=True))
    start = 0
    for i, r in enumerate(rows[:5]):
        if r and HEADER_CODE in _s(r[0]).lower():
            start = i + 1
            break
    else:
        # No header found: treat everything as data rather than silently
        # dropping the first line, and say so in the report.
        start = 0

    items, stats = [], {"rows": 0, "no_code": 0, "no_name": 0,
                        "name_from_desc": 0, "name_from_item": 0, "dupes": 0}
    seen = {}
    for r in rows[start:]:
        a, b, c = (_s(r[0]) if len(r) > 0 else "",
                   _s(r[1]) if len(r) > 1 else "",
                   _s(r[2]) if len(r) > 2 else "")
        if not (a or b or c):
            continue
        stats["rows"] += 1
        if not a:
            stats["no_code"] += 1
            continue
        if c:
            name, src = c, "name_from_desc"
        elif b and b.casefold() != a.casefold():
            name, src = b, "name_from_item"
        else:
            stats["no_name"] += 1
            continue
        stats[src] += 1
        name = re.sub(r"\s+", " ", name).strip()[:200]
        if a.casefold() in seen:
            # The sheet repeats a code. Last row wins, matching how the upsert
            # itself would resolve them, but count it so the report is honest.
            stats["dupes"] += 1
            items[seen[a.casefold()]]["name"] = name
            continue
        seen[a.casefold()] = len(items)
        # cost_price stays None: the sheet states no price, and None is what
        # upsert_items reads as "leave whatever is already there".
        items.append({"code": a[:60], "name": name, "unit": "",
                      "category_code": "", "category_name": "",
                      "cost_price": None})
    return items, stats


def main():
    args = [a for a in sys.argv[1:]]
    apply = "--apply" in args
    paths = [a for a in args if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    path = paths[0]
    if not os.path.exists(path):
        print("No such file: %s" % path)
        return 2

    items, stats = read_rows(path)
    print("READ  %s" % os.path.basename(path))
    print("  data rows seen              %5d" % stats["rows"])
    print("  usable items                %5d" % len(items))
    print("    name taken from desc (C)  %5d" % stats["name_from_desc"])
    print("    name taken from item (B)  %5d" % stats["name_from_item"])
    print("  rejected, no Optima code    %5d" % stats["no_code"])
    print("  rejected, no usable name    %5d" % stats["no_name"])
    print("  duplicate codes in sheet    %5d  (last row wins)" % stats["dupes"])

    import config
    os.environ.pop("DATABASE_URL", None) if not os.environ.get("KEEP_DB_URL") else None
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.db import get_db
        from app.approvals.catalogue import upsert_items
        conn = get_db()
        try:
            have = {r["code"].casefold() for r in
                    conn.execute("SELECT code FROM proc_items").fetchall()}
            before = conn.execute("SELECT COUNT(*) n FROM proc_items").fetchone()["n"]
            new = sum(1 for it in items if it["code"].casefold() not in have)
            print()
            print("AGAINST THE CATALOGUE (%d items on file)" % before)
            print("  would ADD                   %5d" % new)
            print("  would UPDATE or leave as-is %5d" % (len(items) - new))
            print("  untouched, not in the sheet %5d" % (before - (len(items) - new)))

            if not apply:
                print("\nDRY RUN — nothing written. Re-run with --apply to write it.")
                return 0

            res = upsert_items(conn, items, {"username": "optima_sheet_import"},
                               source=os.path.basename(path)[:120])
            conn.commit()
            after = conn.execute("SELECT COUNT(*) n FROM proc_items").fetchone()["n"]
            print()
            print("APPLIED")
            print("  added      %5d" % res["added"])
            print("  updated    %5d" % res["updated"])
            print("  unchanged  %5d" % res["unchanged"])
            print("  rejected   %5d" % res["rejected"])
            print("  catalogue  %5d -> %d items" % (before, after))
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
