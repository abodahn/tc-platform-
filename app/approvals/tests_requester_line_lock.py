"""A request form says WHAT and HOW MANY. Nothing else on the line is set there.

A request line asks two questions: WHAT is needed, and HOW MANY. That is the
whole line. Which catalogue item it is, in what unit, what is on the shelf and
which supplier will provide it are not drawn on the form at all — for anyone,
buyer and super admin included. They are commercial and warehouse facts, and a
request form is where a need is stated, not where sourcing is decided.

They were greyed first, and that was not enough: a box a person can never fill is
still a box they read, hesitate over, and ask about. So they are gone from the
line, and they move to where the knowledge is:

  * UNIT comes from the catalogue when the line was picked — master data, not
    anybody's opinion. A free-text line falls back to "Pcs" until corrected.
  * SUPPLIER is chosen at the PRICING GATE, on the request page, at the same
    moment Purchasing record what that supplier charges. Section 3 proves it.
  * STOCK ON HAND belongs to the warehouse rung, which reads it from the shelf.

THE SCREEN IS THE COURTESY; THE PARSER IS THE CONTROL. The fields are off the
screen, but the lock that matters is server-side:
the request routes call _parse_items with can_buy=False for everybody, so a
hand-crafted POST carrying a unit, a stock figure or a supplier has them dropped,
exactly as unit_price has always been dropped.

Two traps this file exists to hold:

  * NOT keyed on can_price. That was the obvious flag and it is wrong:
    _can_price() is hardcoded False for EVERYONE — a governance rule that nobody
    prices a request at creation time, not even a super admin — so a lock built
    on it would look right while meaning something else entirely.
  * The fields are HIDDEN, never absent. The line editor reads its rows back as
    PARALLEL ARRAYS, so a field missing from a row would shift every value on
    every line below it onto the wrong row — and _parse_items iterates item[],
    so removing that one outright would have dropped every line of every
    request and submitted an empty basket.

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

    # The three boxes are locked for EVERY user on a request form now, buyer and
    # super admin included: a request form states a need, and sourcing is a
    # different decision taken at a different moment.
    chk("requester  CAN_BUY is false", flag(hr, "CAN_BUY") == "false",
        flag(hr, "CAN_BUY"))
    chk("purchasing CAN_BUY is true (the flag still distinguishes them)",
        flag(hb, "CAN_BUY") == "true", flag(hb, "CAN_BUY"))
    chk("can_price stays false for BOTH — it is a governance rule, not a role",
        flag(hr, "CAN_PRICE") == "false" and flag(hb, "CAN_PRICE") == "false",
        (flag(hr, "CAN_PRICE"), flag(hb, "CAN_PRICE")))
    chk("the hint text is still rendered for the fields that remain",
        "LOCK_HINT" in hr and re.search(r'var LOCK_HINT\s*=\s*"[^"]+"', hr) is not None)
    # Read the row-builder SOURCE, not the rendered page: the markup is assembled
    # by JS at runtime, so counting classes in the HTML would prove nothing.
    row = re.search(r"class=.li-row request-line.>'(.*?)</div>';", hr, re.S)
    chk("the request line is built", row is not None)
    body = row.group(1) if row else ""
    def field(nm):
        m = re.search(r"name=\"%s\"[^>]*" % re.escape(nm), body)
        return m.group(0) if m else ""
    for nm in ("item[]", "unit[]", "current_stock[]", "vendor[]"):
        f = field(nm)
        chk("%-16s is HIDDEN, not drawn on the line" % nm,
            f and 'type="hidden"' in f, f[:60] or "ABSENT")
        # Present but hidden, never absent: the rows are read back as PARALLEL
        # ARRAYS, so a field missing from one row would shift every value on
        # every line below it onto the wrong row.
        chk("%-16s is still POSTED, so the arrays stay aligned" % nm, bool(f))
    for nm in ("description[]", "qty[]"):
        f = field(nm)
        chk("%-16s is VISIBLE — what, and how many" % nm,
            f and 'type="hidden"' not in f, f[:60] or "ABSENT")
    chk("no catalogue picker is drawn on a request line",
        "li-pick" not in body and "sp-drop" not in body)

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
    # The REQUEST FORM locks these for everybody, the buyer included. A request
    # form states a need; it is not where sourcing happens, and a buyer who fills
    # it in there is guessing a supplier before anyone has quoted.
    chk("even the BUYER cannot set a unit on a request form",
        b["unit"] in ("", "Pcs"), "%r (posted 'Kg')" % b["unit"])
    chk("nor a stock figure", float(b["current_stock"] or 0) == 0,
        "%r (posted 12)" % b["current_stock"])
    chk("nor a line supplier", not (b["vendor"] or "").strip()
        or b["vendor"] != "Real Vendor", "%r (posted 'Real Vendor')" % b["vendor"])

    # ...they set the supplier at the PRICING GATE, which is the moment they
    # choose one, and the same moment they record what that supplier charges.
    print("\nthe buyer sets the line's supplier at the SOURCING gate instead")
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO proc_vendors (name,is_active) "
                     "VALUES ('Real Vendor',1)")
        conn.commit()
        pr_id = conn.execute("SELECT id FROM pr_requests ORDER BY id DESC LIMIT 1"
                             ).fetchone()["id"]
        line = conn.execute("SELECT id FROM pr_items WHERE pr_id=? LIMIT 1",
                            (pr_id,)).fetchone()["id"]
        conn.close()
    page = buy.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
    chk("the request page offers a supplier box for the line",
        ("linevendor_%d" % line) in page)
    tokp = re.search(r'name="_csrf" value="([^"]+)"', page).group(1)
    buy.post("/procurement/pr/%d/price" % pr_id, data={
        "price_%d" % line: "12", "linevendor_%d" % line: "Real Vendor",
        "currency": "EGP", "_csrf": tokp})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        after = dict(conn.execute(
            "SELECT vendor, unit_price FROM pr_items WHERE id=?", (line,)).fetchone())
        conn.close()
    chk("and setting it there works", after["vendor"] == "Real Vendor",
        after["vendor"])
    chk("alongside the price that supplier quoted",
        float(after["unit_price"]) == 12.0, after["unit_price"])

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
