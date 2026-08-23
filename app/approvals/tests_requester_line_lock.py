"""A requester says WHAT and HOW MANY. Nothing else on the line is theirs.

Unit of measure, stock on hand and the supplier are commercial and warehouse
facts the floor is not placed to assert — asking for them produced guesses that
Purchasing then had to undo, and a wrong unit on a purchase order is a real
ordering error. So on a requester's line the item picker, the description and the
quantity stay open, and unit, current stock and vendor are locked.

THE SCREEN IS THE COURTESY; THE PARSER IS THE CONTROL. The three boxes are greyed
so nobody wastes time typing into them, but the lock that matters is server-side
in _parse_items: a hand-crafted POST carrying a unit, a stock figure or a
supplier has them dropped, exactly as unit_price has always been dropped.

Two traps this file exists to hold:

  * The lock is keyed on CAN_BUY, not can_price. _can_price() is hardcoded False
    for EVERYONE — a governance rule that nobody prices a request at creation
    time, not even a super admin — so keying on it would have greyed these boxes
    for Purchasing too.
  * The boxes are `readonly`, never `disabled`. A disabled field posts nothing,
    and the line editor reads its rows back as PARALLEL ARRAYS, so one skipped
    value would shift every field on every line below it onto the wrong row.

    python app/approvals/tests_requester_line_lock.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="lock_"), "l.db")
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
        conn = get_db()
        for u, role in (("lk_req", "production_manager"),
                        ("lk_buy", "purchasing_manager")):
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (u, "x", u, role))
        conn.commit()
        ids = {r["username"]: r["id"] for r in conn.execute(
            "SELECT id, username FROM users WHERE username IN ('lk_req','lk_buy')"
        ).fetchall()}
        conn.execute(
            "INSERT OR IGNORE INTO proc_items (code, name, unit, category_name, "
            "cost_price, has_cost, source, active, updated_at) VALUES "
            "('LK-1','Cotton twill 240gsm','Meter','Fabric',0,0,'erp',1,'2026-01-01')")
        conn.commit()
        item_id = conn.execute("SELECT id FROM proc_items WHERE code='LK-1'"
                               ).fetchone()["id"]
        conn.close()

    def client(u, role):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = {"username": u, "role": role, "id": ids[u]}
            s["uid"] = ids[u]
        return c

    req = client("lk_req", "production_manager")
    buy = client("lk_buy", "purchasing_manager")

    # ---- 1. the flag the lock hangs on ---------------------------------
    print("the lock is keyed on purchasing rights, not on the pricing rule")
    hr = req.get("/procurement/new").get_data(as_text=True)
    hb = buy.get("/procurement/new").get_data(as_text=True)

    def flag(html, name):
        m = re.search(r"var %s\s*=\s*(\w+)" % name, html)
        return m.group(1) if m else "MISSING"

    chk("requester  CAN_BUY is false", flag(hr, "CAN_BUY") == "false",
        flag(hr, "CAN_BUY"))
    chk("purchasing CAN_BUY is true", flag(hb, "CAN_BUY") == "true",
        flag(hb, "CAN_BUY"))
    chk("can_price stays false for BOTH — it is a governance rule, not a role",
        flag(hr, "CAN_PRICE") == "false" and flag(hb, "CAN_PRICE") == "false",
        (flag(hr, "CAN_PRICE"), flag(hb, "CAN_PRICE")))
    chk("the greyed boxes explain themselves on hover",
        "LOCK_HINT" in hr and re.search(r'var LOCK_HINT\s*=\s*"[^"]+"', hr) is not None)
    # Read the row-builder SOURCE, not the rendered page: the markup is assembled
    # by JS at runtime, so the page carries both branches and counting classes in
    # the HTML would prove nothing either way.
    branches = re.findall(r"class=.li-locked.[^']*", hr)
    chk("all three locked boxes are in the row builder", len(branches) == 3,
        len(branches))
    chk("every one of them is readonly", all("readonly" in b for b in branches),
        branches[:1])
    chk("and none is disabled — a disabled field posts nothing, which would "
        "shift every line below it onto the wrong row",
        not any("disabled" in b for b in branches))

    # ---- 2. THE CONTROL: the parser drops what the screen locked ---------
    print("\nwhat a requester posts for those three fields is dropped")
    tok = re.search(r'name="_csrf" value="([^"]+)"', hr).group(1)
    r = req.post("/procurement/new", data={
        "title": "Lock test", "department": "IT", "currency": "EGP",
        "item[]": "Some fabric", "description[]": "heavy twill",
        "unit[]": "Kg", "qty[]": "5", "current_stock[]": "99",
        "vendor[]": "Sneaky Vendor", "unit_price[]": "1234",
        "item_notes[]": "", "spare_id[]": "", "item_id[]": "", "_csrf": tok})
    chk("the request is accepted", r.status_code in (200, 302), r.status_code)

    with app.app_context():
        from app.db import get_db
        conn = get_db()
        row = dict(conn.execute(
            "SELECT item, description, unit, qty, current_stock, vendor, unit_price "
            "FROM pr_items ORDER BY id DESC LIMIT 1").fetchone())
        conn.close()
    chk("a posted UNIT is ignored", row["unit"] in ("", "Pcs"),
        "%r (posted 'Kg')" % row["unit"])
    chk("a posted STOCK figure is ignored", float(row["current_stock"] or 0) == 0,
        "%r (posted 99)" % row["current_stock"])
    chk("a posted VENDOR is ignored", not (row["vendor"] or "").strip(),
        "%r (posted 'Sneaky Vendor')" % row["vendor"])
    chk("a posted PRICE is ignored, as it always was",
        float(row["unit_price"] or 0) == 0, "%r (posted 1234)" % row["unit_price"])
    chk("the DESCRIPTION is kept — it is what the requester is here to say",
        row["description"] == "heavy twill", row["description"])
    chk("the QUANTITY is kept", float(row["qty"]) == 5.0, row["qty"])

    # ---- 3. Purchasing keep the line ------------------------------------
    print("\nand Purchasing still set all three")
    tokb = re.search(r'name="_csrf" value="([^"]+)"', hb).group(1)
    r = buy.post("/procurement/new", data={
        "title": "Buyer line", "department": "IT", "currency": "EGP",
        "item[]": "Some fabric", "description[]": "heavy twill",
        "unit[]": "Kg", "qty[]": "7", "current_stock[]": "12",
        "vendor[]": "Real Vendor", "item_notes[]": "", "spare_id[]": "",
        "item_id[]": "", "_csrf": tokb})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        b = dict(conn.execute(
            "SELECT unit, current_stock, vendor FROM pr_items ORDER BY id DESC LIMIT 1"
        ).fetchone())
        conn.close()
    chk("the buyer's unit is kept", b["unit"] == "Kg", b["unit"])
    chk("the buyer's stock figure is kept", float(b["current_stock"]) == 12.0,
        b["current_stock"])
    chk("the buyer's vendor is kept", b["vendor"] == "Real Vendor", b["vendor"])

    # ---- 4. a PICKED line takes the catalogue's own unit ------------------
    print("\na line picked from the catalogue carries the catalogue's unit")
    hr2 = req.get("/procurement/new").get_data(as_text=True)
    tok2 = re.search(r'name="_csrf" value="([^"]+)"', hr2).group(1)
    req.post("/procurement/new", data={
        "title": "Picked line", "department": "IT", "currency": "EGP",
        "item[]": "Cotton twill 240gsm", "description[]": "", "unit[]": "",
        "qty[]": "500", "current_stock[]": "0", "vendor[]": "",
        "item_notes[]": "", "spare_id[]": "", "item_id[]": str(item_id),
        "_csrf": tok2})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        pk = dict(conn.execute(
            "SELECT item, unit, item_id FROM pr_items ORDER BY id DESC LIMIT 1"
        ).fetchone())
        conn.close()
    chk("the line is linked to the catalogue item", str(pk["item_id"]) == str(item_id),
        pk["item_id"])
    chk("and took the catalogue's unit, not a flat 'Pcs'",
        pk["unit"] == "Meter", "%r (catalogue says Meter)" % pk["unit"])

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
