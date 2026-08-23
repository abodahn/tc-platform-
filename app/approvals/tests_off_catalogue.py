"""A typed line Purchasing can actually act on.

A requester may type a line in their own words — that freedom stays. What was
missing was the other half: Purchasing could SEE the result in a report and could
do nothing with it. No way to add the item, no way to say it should not be added.
So the same words got typed again next month.

This proves the loop closes: typed -> queued -> added with the Optima code ->
EVERY line that ever carried those words is linked to the new item, including
lines on requests already submitted. That last part is the point. Linking only
future lines would leave the report still calling it off-catalogue after it had
been added, and the person who typed it would type it again.

    python app/approvals/tests_off_catalogue.py
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="offc_"), "o.db")
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

    TYPED = "Rubber plug for the overlock bed"

    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc

        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
            "is_active, created_at) VALUES ('oc_buyer','x','oc_buyer',"
            "'purchasing_manager',1,'2026-01-01')")
        conn.commit()
        conn.close()

        # Two requesters, three requests, the same words typed on each — which is
        # exactly the shape that makes this worth fixing.
        for who, n in (("oc_req_a", 2), ("oc_req_b", 1)):
            for i in range(n):
                svc.create_pr(
                    {"title": "Line %s %d" % (who, i), "department": "IT",
                     "currency": "EGP"},
                    [{"item": TYPED, "unit": "Pcs", "qty": 3, "unit_price": 0}],
                    {"username": who, "id": 50}, priced=False, submit=False)

    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = {"username": "oc_buyer", "role": "purchasing_manager", "id": 1}
        s["uid"] = 1

    def token(url="/procurement/off-catalogue"):
        m = re.search(r'name="_csrf" value="([^"]+)"',
                      c.get(url).get_data(as_text=True))
        return m.group(1) if m else ""

    # ---- 1. it reaches Purchasing as something to decide -----------------
    print("a typed line reaches Purchasing as a decision, not a list entry")
    r = c.get("/procurement/off-catalogue")
    chk("the queue opens", r.status_code == 200, r.status_code)
    html = r.get_data(as_text=True)
    chk("the typed text is on it", TYPED in html)
    chk("it says how many lines typed it", ">3<" in html or "3" in html)
    chk("it offers an ADD action", 'name="code"' in html)
    chk("and a REJECT action", 'name="note"' in html)

    with app.app_context():
        from app.db import get_db
        from app.approvals import off_catalogue as OC
        conn = get_db()
        rows = OC.pending(conn)
        conn.close()
    mine = [x for x in rows if x["item_text"] == TYPED]
    chk("one row for the text, not one per line", len(mine) == 1,
        [x["item_text"] for x in rows])
    chk("counting all three lines", mine and mine[0]["lines"] == 3,
        mine and mine[0]["lines"])

    # ---- 2. adding it links the requests that already typed it -----------
    print("\nadding it reaches BACK to the requests that already typed it")
    tok = token()
    r = c.post("/procurement/off-catalogue/add", data={
        "text": TYPED, "code": "Spa-Sew-F-616-88", "unit": "pcs",
        "category_name": "Sewing spares", "_csrf": tok})
    chk("the add is accepted", r.status_code in (200, 302), r.status_code)

    with app.app_context():
        from app.db import get_db
        from app.approvals import items_admin as IA
        conn = get_db()
        item = IA.item_by_code(conn, "Spa-Sew-F-616-88")
        linked = conn.execute(
            "SELECT COUNT(*) n FROM pr_items WHERE item_id=?",
            (item["id"] if item else -1,)).fetchone()["n"]
        orphan = conn.execute(
            "SELECT COUNT(*) n FROM pr_items WHERE (item_id IS NULL OR item_id=0) "
            "AND LOWER(TRIM(item))=?", (TYPED.lower(),)).fetchone()["n"]
        conn.close()
    chk("the catalogue row exists, on the Optima code",
        item and item["code"] == "Spa-Sew-F-616-88", item and item["code"])
    chk("named with the requester's own words",
        item and item["name"] == TYPED, item and item["name"])
    chk("the unit was normalised (pcs -> Pcs)", item and item["unit"] == "Pcs",
        item and item["unit"])
    chk("born UNPRICED — value still enters only at the pricing gate",
        item and not item["has_cost"] and not (item["cost_price"] or 0),
        item and (item["has_cost"], item["cost_price"]))
    chk("ALL THREE existing lines now point at it", linked == 3, linked)
    chk("and none is left off-catalogue", orphan == 0, orphan)

    with app.app_context():
        from app.db import get_db
        from app.approvals import off_catalogue as OC
        conn = get_db()
        left = OC.pending(conn)
        notes = [dict(x) for x in conn.execute(
            "SELECT target_user, title, message FROM notifications "
            "WHERE module='procurement' AND title LIKE 'Item request%'").fetchall()]
        conn.close()
    chk("the text has left the queue", TYPED not in [x["item_text"] for x in left],
        [x["item_text"] for x in left])
    chk("and the people who TYPED it were told",
        {n["target_user"] for n in notes} >= {"oc_req_a", "oc_req_b"},
        sorted({n["target_user"] for n in notes}))

    # ---- 3. rejecting records the decision and stops the repeat ----------
    print("\nrejecting is a decision too, and it sticks")
    OTHER = "Some thing nobody should stock"
    with app.app_context():
        from app.approvals import services as svc
        svc.create_pr({"title": "Odd", "department": "IT", "currency": "EGP"},
                      [{"item": OTHER, "unit": "Pcs", "qty": 1, "unit_price": 0}],
                      {"username": "oc_req_a", "id": 50}, priced=False, submit=False)
    tok = token()
    r = c.post("/procurement/off-catalogue/reject", data={
        "text": OTHER, "note": "Use Spa-Sew-F-616-88 instead.", "_csrf": tok})
    chk("the reject is accepted", r.status_code in (200, 302), r.status_code)
    with app.app_context():
        from app.db import get_db
        from app.approvals import off_catalogue as OC
        conn = get_db()
        still = [x["item_text"] for x in OC.pending(conn)]
        with_decided = [x["item_text"] for x in OC.pending(conn, include_decided=True)]
        conn.close()
    chk("it leaves the waiting queue", OTHER not in still, still)
    chk("but is still findable when asked for", OTHER in with_decided, with_decided)

    # a reject with no reason is refused — "no" without "use this instead" is
    # what sends the requester back to typing the same words.
    r = c.post("/procurement/off-catalogue/reject",
               data={"text": OTHER, "note": "", "_csrf": token()})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        note = conn.execute("SELECT note FROM proc_off_catalogue WHERE text_key=?",
                            (OTHER.lower(),)).fetchone()
        conn.close()
    chk("a reject with no reason cannot overwrite the real one",
        note and note["note"] == "Use Spa-Sew-F-616-88 instead.",
        note and note["note"])

    # ---- 4. the ERP still keeps the codes --------------------------------
    print("\nthe platform still never invents a code")
    with app.app_context():
        from app.approvals import services as svc
        svc.create_pr({"title": "Dup", "department": "IT", "currency": "EGP"},
                      [{"item": "Another typed thing", "unit": "Pcs", "qty": 1,
                        "unit_price": 0}],
                      {"username": "oc_req_a", "id": 50}, priced=False, submit=False)
    r = c.post("/procurement/off-catalogue/add", data={
        "text": "Another typed thing", "code": "spa-sew-f-616-88", "_csrf": token()})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        n = conn.execute(
            "SELECT COUNT(*) n FROM proc_items WHERE LOWER(code)='spa-sew-f-616-88'"
        ).fetchone()["n"]
        conn.close()
    chk("a code already held cannot be reused, even in another case", n == 1, n)
    r = c.post("/procurement/off-catalogue/add", data={
        "text": "Another typed thing", "code": "", "_csrf": token()})
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        n2 = conn.execute(
            "SELECT COUNT(*) n FROM proc_items WHERE name='Another typed thing'"
        ).fetchone()["n"]
        conn.close()
    chk("and adding with no code at all creates nothing", n2 == 0, n2)

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
