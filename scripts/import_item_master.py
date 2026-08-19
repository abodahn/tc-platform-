"""
Import the ERP item master into proc_items from the monthly stock workbook.

WHICH SHEET, AND WHY IT MATTERS
The workbook has 32 sheets and only ONE of them is the master. The obvious
candidate, "Inventory", holds 5,834 item rows but every price cell in it is
#REF! — broken formula references, in both the original and the copy named
"...VALUES_ONLY". Importing that would have filled the catalogue with items
carrying no cost, which is worse than useless here: DOAM §4.4 grades a purchase
against proc_items.cost_price, so a catalogue with no prices leaves the control
correct in code and inert on the data.

"List Of Item" is the real master: 14,471 rows, no duplicate codes, and 60% of
them carrying a genuine cost price. It is laid out differently from every other
sheet (code in column A, not column D), which is why a scan keyed on the other
sheets' shape reports it as nearly empty.

The remaining 30 sheets are issue and movement logs. Their "codes" are values
like "4", "Cilnt" and "AMR-EAGLE" — importing them would add ~26,000 rows of
noise to the picker a buyer has to search.

WHAT IS AND IS NOT IMPORTED
  code        column A, the ERP's own key. Never parsed, only carried.
  name        column B
  unit        column C, e.g. "03 Meter" — the ERP's numbered form, kept verbatim
  cost_price  column E. Sales Price (column D) is deliberately IGNORED: §4.4
              grades against what we PAY, and importing a sales price as a
              purchase target would escalate every purchase that beat it.
  has_cost    0 when the row has no cost. NOT a zero price — a zero target makes
              price_deviation_pct() return None and the line grades "on plan",
              which is the honest answer for "we do not know what this costs".
  category    from the "Category: NN Name" band rows above each block.

Idempotent: re-running updates existing codes and inserts new ones. Nothing is
deleted, so an item that leaves the workbook keeps its history in past requests.

    python scripts/import_item_master.py "C:\\path\\to\\25-Stock Monthly.xlsx"
    python scripts/import_item_master.py <file> --dry-run
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import openpyxl                                    # noqa: E402
from app import create_app                         # noqa: E402
from app.db import get_db, utcnow                  # noqa: E402

SHEET = "List Of Item"
SOURCE = "erp-stock-workbook"


def read_items(path):
    """Yield {code, name, unit, cost, category} from the master sheet."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if SHEET not in wb.sheetnames:
        wb.close()
        sys.exit("REFUSED: %r has no %r sheet. This importer reads the item "
                 "master, not the movement logs." % (os.path.basename(path), SHEET))
    ws = wb[SHEET]
    category = None
    for row in ws.iter_rows(min_row=5, values_only=True):
        code = str(row[0] or "").strip()
        if code.lower().startswith("category:"):
            category = code.split(":", 1)[1].strip()
            continue
        if not code or code.lower() == "code":
            continue
        name = str(row[1] or "").strip()
        if not name:
            continue                     # a code with no name is not an item
        cost = None
        try:
            c = float(row[4]) if row[4] is not None else 0.0
            cost = c if c > 0 else None
        except (TypeError, ValueError):
            cost = None
        yield {"code": code, "name": name,
               "unit": str(row[2] or "").strip() or None,
               "cost": cost, "category": category}
    wb.close()


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        sys.exit(__doc__.strip().split("\n\n")[-1])
    path = args[0]
    if not os.path.exists(path):
        sys.exit("No such file: %s" % path)

    rows = list(read_items(path))
    if not rows:
        sys.exit("REFUSED: no item rows found — the sheet layout is not what "
                 "this importer expects. Nothing was written.")
    priced = sum(1 for r in rows if r["cost"])
    print("  read %d items from %r (%d with a cost price, %d without)"
          % (len(rows), SHEET, priced, len(rows) - priced))
    if dry:
        for r in rows[:5]:
            print("    %-24s %-34s %-10s %s" % (r["code"][:24], r["name"][:34],
                                                r["unit"] or "", r["cost"] or ""))
        print("  --dry-run: nothing written")
        return

    app = create_app()
    with app.app_context():
        conn = get_db()
        now = utcnow()
        ins = upd = 0
        try:
            for r in rows:
                hit = conn.execute("SELECT id FROM proc_items WHERE code=?",
                                   (r["code"],)).fetchone()
                cat_code = (r["category"] or "").split(" ", 1)[0] or None
                if hit:
                    conn.execute(
                        "UPDATE proc_items SET name=?, unit=?, cost_price=?, has_cost=?, "
                        "category_code=?, category_name=?, source=?, active=1, "
                        "updated_by=?, updated_at=? WHERE id=?",
                        (r["name"], r["unit"], r["cost"] or 0, 1 if r["cost"] else 0,
                         cat_code, r["category"], SOURCE, "import", now, hit["id"]))
                    upd += 1
                else:
                    conn.execute(
                        "INSERT INTO proc_items (code, name, unit, cost_price, has_cost, "
                        "category_code, category_name, source, active, updated_by, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                        (r["code"], r["name"], r["unit"], r["cost"] or 0,
                         1 if r["cost"] else 0, cat_code, r["category"], SOURCE,
                         "import", now))
                    ins += 1
            conn.commit()
            total = conn.execute("SELECT COUNT(*) c FROM proc_items").fetchone()["c"]
            withc = conn.execute("SELECT COUNT(*) c FROM proc_items WHERE has_cost=1"
                                 ).fetchone()["c"]
        finally:
            conn.close()

    print("  inserted %d, updated %d" % (ins, upd))
    print("  proc_items now holds %d items, %d with a cost price" % (total, withc))
    print()
    print("  DOAM \u00a74.4 grades a purchase against cost_price, so the %d items"
          % (total - withc))
    print("  WITHOUT one still cannot be graded — they report as unassessable")
    print("  rather than silently passing as on-plan.")


if __name__ == "__main__":
    main()
