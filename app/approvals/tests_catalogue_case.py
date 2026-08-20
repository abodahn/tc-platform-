"""An item code is one part, however the file happens to spell it.

The UNIQUE on proc_items.code is case-sensitive, so "AB-100" and "ab-100" are
two rows to the database and one part to a human. The new-item door already
refused a code differing only in case, and its docstring named the consequence
of the other door not doing the same:

    "the next ERP export carries the code in its own case, upsert_items matches
     by exact code, and the master ends up with two rows that never merge"

That is not hypothetical any more — the store's Optima sheet did it twice on its
first import. This file holds the fix on both doors.

    python app/approvals/tests_catalogue_case.py
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="catcase_"), "c.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    ok_all = [True]

    def chk(label, cond, extra=""):
        ok_all[0] &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label
              + ((" | " + str(extra)) if extra else ""))

    with app.app_context():
        from app.db import get_db
        from app.approvals.catalogue import upsert_items
        from app.approvals import item_requests as ir

        who = {"username": "case_test"}
        conn = get_db()

        # ---- the bulk door ------------------------------------------------
        print("the import door")
        r1 = upsert_items(conn, [{"code": "AB-100", "name": "Bearing housing",
                                  "unit": "Pcs", "cost_price": 40.0}], who, source="first")
        conn.commit()
        chk("a new code is added", r1["added"] == 1, r1)

        # the same part, spelled the way the next export happens to spell it
        r2 = upsert_items(conn, [{"code": "ab-100", "name": "Bearing housing, long",
                                  "unit": "Pcs", "cost_price": None}], who, source="second")
        conn.commit()
        chk("the SAME code in another case is not a second item",
            r2["added"] == 0, r2)

        rows = conn.execute(
            "SELECT id, code, name, cost_price FROM proc_items "
            "WHERE LOWER(code)='ab-100' ORDER BY id").fetchall()
        chk("exactly one row holds that code", len(rows) == 1,
            [r["code"] for r in rows])
        chk("and the update actually landed on it",
            rows and rows[0]["name"] == "Bearing housing, long",
            rows and rows[0]["name"])
        chk("the stored code keeps ITS OWN spelling — other documents cite it",
            rows and rows[0]["code"] == "AB-100", rows and rows[0]["code"])
        chk("a blank cost still did not clobber the stored one",
            rows and abs(float(rows[0]["cost_price"] or 0) - 40.0) < 1e-9,
            rows and rows[0]["cost_price"])

        # ---- and it is idempotent ------------------------------------------
        r3 = upsert_items(conn, [{"code": "ab-100", "name": "Bearing housing, long",
                                  "unit": "Pcs", "cost_price": None}], who, source="third")
        conn.commit()
        chk("re-importing the same file changes nothing",
            r3["added"] == 0 and r3["updated"] == 0 and r3["unchanged"] == 1, r3)

        # ---- the new-item door ---------------------------------------------
        print("\nthe new-item door")
        taken = ir._code_taken(conn, "Ab-100")
        chk("a code differing only in case is already taken",
            taken and taken["code"] == "AB-100", taken and taken.get("code"))
        conn.close()

        req_id, err = ir.create_item_request(
            {"name": "Another bearing housing", "reason": "spare"},
            {"username": "case_req", "id": 7})
        ok, msg, clash = ir.approve_item_request(
            req_id, "aB-100", {"username": "case_buyer", "id": 1}, unit="Pcs")
        chk("approving with it is refused, not duplicated",
            not ok and msg == "duplicate_code", msg)
        chk("and the refusal shows the row that holds it",
            clash and clash["code"] == "AB-100", clash and clash.get("code"))

        # ---- the whole catalogue is clean -----------------------------------
        conn = get_db()
        dupes = conn.execute(
            "SELECT COUNT(*) n FROM (SELECT 1 FROM proc_items "
            "GROUP BY LOWER(code) HAVING COUNT(*) > 1)").fetchone()["n"]
        conn.close()
        chk("no two rows differ only by letter case", dupes == 0, dupes)

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
