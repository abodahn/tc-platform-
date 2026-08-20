"""The store's item-master door: full control, minus the one thing it must not have.

The request was "the warehouse sees the list of items and can do anything to
them, and any edit sends a notification for information". Everything here is
driven through the ROUTES a storekeeper actually posts to, not the service layer
underneath, because the question is whether a storekeeper can do it — which is a
question about permissions and the screen, not about Python functions.

The control this file exists to hold: cost price is NOT editable here. Value
enters the platform once, at the Purchasing pricing gate. A second door on cost
would let a request be approved against a number nobody negotiated.

    python app/approvals/tests_items_admin.py
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="items_"), "items.db")
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
        for uname, role in (("st_keeper", "storekeeper"),
                            ("st_buyer", "purchasing_manager"),
                            ("st_req", "production_manager")):
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (uname, "x", uname, role))
        conn.execute(
            "INSERT OR IGNORE INTO proc_items (code, name, unit, category_code, "
            "category_name, cost_price, has_cost, source, active, updated_at) "
            "VALUES ('EXIST-1','Old bearing','Pcs','MRO','Spares',250.0,1,'erp',1,'2026-01-01')")
        conn.commit()
        keeper = dict(conn.execute(
            "SELECT id, username, role FROM users WHERE username='st_keeper'").fetchone())
        conn.close()

    def client_as(username, role, uid=None):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = {"username": username, "role": role, "id": uid or 1}
            s["uid"] = uid or 1
        return c

    def token(c, url="/procurement/items"):
        html = c.get(url).get_data(as_text=True)
        m = re.search(r'name="_csrf" value="([^"]+)"', html)
        return m.group(1) if m else ""

    # ---- 1. the storekeeper can open it at all --------------------------
    print("the store can reach the item master")
    c = client_as("st_keeper", "storekeeper", keeper["id"])
    r = c.get("/procurement/items")
    chk("a storekeeper opens /procurement/items", r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    chk("and the existing item is listed", "EXIST-1" in html)
    chk("the screen offers an add form", "items.add" in html or "Add an item" in html)

    print("\nand somebody with no catalogue right cannot")
    c_req = client_as("st_req", "production_manager", 3)
    r = c_req.get("/procurement/items")
    chk("a requester is refused the item master", r.status_code in (302, 403), r.status_code)
    tok_req = ""
    r = c_req.post("/procurement/items/new",
                   data={"code": "SNEAK-1", "name": "Sneaked in", "_csrf": tok_req})
    chk("and cannot post a new item either", r.status_code in (302, 400, 403), r.status_code)
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) n FROM proc_items WHERE code='SNEAK-1'").fetchone()["n"]
        conn.close()
    chk("nothing was written by the refused request", n == 0, n)

    # ---- 2. full control: add, edit, retire, restore ---------------------
    print("\nfull control over an item")
    tok = token(c)
    r = c.post("/procurement/items/new", data={
        "code": "NEW-77", "name": "Needle bar", "unit": "pcs",
        "category_code": "MRO", "category_name": "Spares", "_csrf": tok})
    chk("adds an item", r.status_code == 302, r.status_code)
    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        row = IA.item_by_code(conn, "NEW-77")
        conn.close()
    chk("the row exists", bool(row), row and row["name"])
    chk("the unit was normalised (pcs -> Pcs)", row and row["unit"] == "Pcs",
        row and row["unit"])
    chk("born UNPRICED — value enters at the pricing gate, not here",
        row and not row["has_cost"] and not (row["cost_price"] or 0),
        row and (row["has_cost"], row["cost_price"]))

    r = c.post("/procurement/items/new", data={
        "code": "NEW-77", "name": "Duplicate", "_csrf": tok})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) n FROM proc_items WHERE code='NEW-77'").fetchone()["n"]
        conn.close()
    chk("the same code cannot be added twice", n == 1, n)

    r = c.post("/procurement/items/%d" % row["id"], data={
        "code": "NEW-77", "name": "Needle bar, long", "unit": "Pcs",
        "category_code": "MRO", "category_name": "Spares", "_csrf": tok})
    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        after = IA.get_item(conn, row["id"])
        conn.close()
    chk("edits the name", after and after["name"] == "Needle bar, long", after and after["name"])

    r = c.post("/procurement/items/%d/active" % row["id"], data={"active": "0", "_csrf": tok})
    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        after = IA.get_item(conn, row["id"])
        conn.close()
    chk("retires it", after and not after["active"], after and after["active"])
    chk("but does NOT delete it — old documents still point at this code", bool(after))

    c.post("/procurement/items/%d/active" % row["id"], data={"active": "1", "_csrf": tok})
    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        after = IA.get_item(conn, row["id"])
        conn.close()
    chk("restores it", after and after["active"] == 1)

    # ---- 3. THE CONTROL: cost price is not editable here ------------------
    print("\ncost price stays with the pricing gate")
    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        before = IA.item_by_code(conn, "EXIST-1")
        conn.close()
    c.post("/procurement/items/%d" % before["id"], data={
        "code": "EXIST-1", "name": "Old bearing", "unit": "Pcs",
        "category_code": "MRO", "category_name": "Spares",
        # every spelling a hand-crafted post might use:
        "cost_price": "1", "cost": "1", "price": "1", "has_cost": "1",
        "_csrf": tok})
    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        after = IA.item_by_code(conn, "EXIST-1")
        conn.close()
    chk("a posted cost_price is ignored, not applied",
        float(after["cost_price"] or 0) == float(before["cost_price"] or 0),
        "%s -> %s" % (before["cost_price"], after["cost_price"]))
    chk("and has_cost is untouched", after["has_cost"] == before["has_cost"])

    # ---- 4. every change is announced, for information only ---------------
    print("\nevery change tells the people who own item data")
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        notes = [dict(r) for r in conn.execute(
            "SELECT severity, title, message, target_user, link FROM notifications "
            "WHERE module='procurement' AND title LIKE 'Item %' ORDER BY id").fetchall()]
        audits = [dict(r) for r in conn.execute(
            "SELECT action, detail FROM audit_logs WHERE action LIKE 'catalogue%' "
            "ORDER BY id").fetchall()]
        conn.close()
    chk("notifications were raised", len(notes) >= 3, len(notes))
    chk("they went to the buyer, who owns item data",
        any(n["target_user"] == "st_buyer" for n in notes),
        sorted({n["target_user"] for n in notes}))
    chk("NOT back to the person who made the change",
        not any(n["target_user"] == "st_keeper" for n in notes))
    chk("severity is informational, not a task",
        all(n["severity"] == "info" for n in notes),
        sorted({n["severity"] for n in notes}))
    chk("each links to the item it is about",
        all("/procurement/items?q=" in (n["link"] or "") for n in notes))
    kinds = {a["action"] for a in audits}
    chk("the audit log records add, edit, retire and restore",
        {"catalogue_created", "catalogue_edited", "catalogue_retired",
         "catalogue_restored"} <= kinds, sorted(kinds))
    edited = [a["detail"] for a in audits if a["action"] == "catalogue_edited"]
    chk("an edit records WHAT changed, old value and new",
        any("→" in d for d in edited), edited[:1])

    # a save that changes nothing must stay silent
    before_n = len(notes)
    c.post("/procurement/items/%d" % row["id"], data={
        "code": "NEW-77", "name": "Needle bar, long", "unit": "Pcs",
        "category_code": "MRO", "category_name": "Spares", "_csrf": tok})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        now_n = conn.execute(
            "SELECT COUNT(*) n FROM notifications WHERE module='procurement' "
            "AND title LIKE 'Item %'").fetchone()["n"]
        conn.close()
    chk("a save that changed nothing raises no notification",
        now_n == before_n, "%d -> %d" % (before_n, now_n))

    # ---- 5. searching the master ------------------------------------------
    print("\nfinding one row in a master of thousands")
    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        rows, total = IA.search_items(conn, "NEW-77")
        _, all_active = IA.search_items(conn, "")
        by_name, _ = IA.search_items(conn, "bearing")
        page2, _ = IA.search_items(conn, "", limit=1, offset=1)
        conn.close()
    chk("search by code finds it", total == 1 and rows[0]["code"] == "NEW-77", total)
    chk("search by name finds it", any("EXIST-1" == r["code"] for r in by_name),
        [r["code"] for r in by_name])
    chk("a blank search still pages rather than returning everything",
        len(page2) == 1, len(page2))
    chk("the count reflects the whole match, not the page", all_active >= 2, all_active)

    # ---- 6. it is reachable ------------------------------------------------
    print("\nreachable from the rest of the platform")
    from app.navigation import NAV
    nav_keys = [it[0] for sec in NAV for it in sec["items"]]
    chk("the sidebar carries it", "proc_items" in nav_keys)
    s = c.get("/search?q=NEW-77")
    if s.status_code == 200:
        hits = s.get_json().get("results", [])
        chk("global search returns the item",
            any(h.get("type") == "item" and "NEW-77" in h.get("name", "") for h in hits),
            [h.get("name") for h in hits][:3])
    else:
        chk("global search endpoint answered", False, s.status_code)

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
