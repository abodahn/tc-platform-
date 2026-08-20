"""The floor describes it; Purchasing give it its Optima code.

A requester is not placed to know a unit of measure, a catalogue category, or
what a part is called in Optima — asking them produced guesses Purchasing then
had to undo. So the requester's side is one field: what they need, in their own
words. Purchasing type the Optima code, and the unit and category alongside it,
and approving builds the catalogue row from those two halves.

    python app/approvals/tests_optima_link.py
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="optima_"), "o.db")
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
        from app.approvals import item_requests as ir

        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
            "is_active, created_at) VALUES ('op_buyer','x','op_buyer',"
            "'purchasing_manager',1,'2026-01-01')")
        conn.commit()
        conn.close()

        # ---- the requester says only WHAT they need ------------------------
        print("the requester describes, and that is all they are asked for")
        req_id, err = ir.create_item_request(
            {"name": "Rubber plug for the overlock bed",
             "reason": "Line 3 is missing one"},
            {"username": "op_req", "id": 9})
        chk("a request can be raised with a description alone", req_id and not err, err)

        conn = get_db()
        row = dict(conn.execute("SELECT * FROM proc_item_requests WHERE id=?",
                                (req_id,)).fetchone())
        conn.close()
        chk("no unit was guessed by the floor", not (row["unit"] or ""), repr(row["unit"]))
        chk("no category was guessed by the floor",
            not (row["category_name"] or ""), repr(row["category_name"]))
        chk("and there is no column here that could carry a price",
            not any("cost" in k or "price" in k for k in row.keys()),
            sorted(row.keys()))

        # ---- Purchasing supply the Optima code and the master data ---------
        print("\nPurchasing give it its code, and the two halves become one row")
        ok, msg, _existing = ir.approve_item_request(
            req_id, "Spa-Sew-F-616-88", {"username": "op_buyer", "id": 1},
            unit="pcs", category_name="Sewing spares")
        chk("Purchasing approve with the Optima code", ok, msg)

        conn = get_db()
        req = dict(conn.execute("SELECT * FROM proc_item_requests WHERE id=?",
                                (req_id,)).fetchone())
        item = conn.execute("SELECT * FROM proc_items WHERE id=?",
                            (req["item_id"],)).fetchone()
        item = dict(item) if item else None
        conn.close()

        chk("the catalogue row was created", bool(item))
        chk("it carries the OPTIMA code", item and item["code"] == "Spa-Sew-F-616-88",
            item and item["code"])
        chk("and the requester's own words as its name",
            item and item["name"] == "Rubber plug for the overlock bed",
            item and item["name"])
        # norm_unit, not _norm: _norm lower-cases for comparison and would store
        # "pcs" where the rest of the catalogue holds "Pcs".
        chk("the unit Purchasing typed is stored as the catalogue spells it",
            item and item["unit"] == "Pcs", item and item["unit"])
        chk("the category Purchasing typed was applied",
            item and item["category_name"] == "Sewing spares",
            item and item["category_name"])
        chk("the request is linked to the item it created",
            req["item_id"] == item["id"] and req["assigned_code"] == "Spa-Sew-F-616-88",
            (req["item_id"], req["assigned_code"]))
        chk("born unpriced — value still enters only at the pricing gate",
            item and not item["has_cost"] and not (item["cost_price"] or 0),
            item and (item["has_cost"], item["cost_price"]))

        # ---- the code stays Optima's to issue -------------------------------
        print("\nand the platform still never invents a code")
        req2, _ = ir.create_item_request(
            {"name": "Second plug", "reason": "spare"}, {"username": "op_req", "id": 9})
        ok2, msg2, clash = ir.approve_item_request(
            req2, "Spa-Sew-F-616-88", {"username": "op_buyer", "id": 1}, unit="Pcs")
        chk("a code already in the catalogue is refused", not ok2 and msg2 == "duplicate_code",
            msg2)
        chk("and the refusal names the item that already holds it",
            clash and clash["code"] == "Spa-Sew-F-616-88", clash and clash["code"])
        ok3, msg3, _ = ir.approve_item_request(
            req2, "", {"username": "op_buyer", "id": 1})
        chk("approving with no code at all is refused",
            not ok3 and msg3 == "code_required", msg3)

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
