"""The warehouse rung fixes the quantity instead of arguing about it.

DOAM §4.1 makes Warehouse a DEMAND rung: the question it answers is "is this
already in stock, and is the quantity right?". Until now answering it meant
approving a figure the store knew was wrong, or rejecting a whole request to have
one number changed. The store now corrects it in place — and only that.

The line the panel draws is the pricing gate's table with everything commercial
removed: no unit price, no supplier, no market research. Those are Purchasing's,
and a warehouse edit must never move commercial value.

THE RULE THIS FILE EXISTS TO HOLD: once a request is PRICED, a quantity change
moves a total that people have already signed, and can change which ladder the
request should have climbed. Before pricing the request is worth zero, so a
quantity is just a quantity. The service refuses after pricing, and the panel is
not drawn.

    python app/approvals/tests_warehouse_review.py
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="whrev_"), "w.db")
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
        from app.approvals import services as svc, constants as C

        conn = get_db()
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            u = "wr_" + stage
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (u, "x", u, role))
            signer[stage] = {"username": u, "role": role}
        conn.commit()
        for st in signer:
            signer[st]["id"] = conn.execute(
                "SELECT id FROM users WHERE username=?",
                (signer[st]["username"],)).fetchone()["id"]
        # Something to find in the stores.
        conn.execute(
            "INSERT OR IGNORE INTO mnt_spare_parts (code, name, uom, stock_qty, "
            "reserved_qty, warehouse, bin, is_active) VALUES "
            "('SP-WR-1','Drive belt A42','Pcs',40,30,'Main','A-12',1)")
        conn.commit()
        conn.close()

        reqr = {"username": "wr_req", "id": 77}
        pid, _no = svc.create_pr(
            {"title": "Belt", "department": "IT", "currency": "EGP"},
            [{"item": "Drive belt A42", "unit": "Pcs", "qty": 10, "unit_price": 0}],
            reqr, priced=False)
        conn = get_db()
        line = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pid,)).fetchone()["id"]
        conn.close()

    def client(who):
        c = app.test_client()
        with c.session_transaction() as s:
            s["user"] = dict(who)
            s["uid"] = who["id"]
        return c

    store = client(signer["warehouse"])
    buyer = client(signer["purchasing"])

    # ---- 1. the panel is drawn for the rung that owns the question --------
    print("the panel belongs to the warehouse rung, and to nobody else")
    h = store.get("/procurement/pr/%d" % pid).get_data(as_text=True)
    chk("the store sees the warehouse panel", "proc.wh_gate" in h)
    chk("with a quantity box on the line", ('name="whqty_%d"' % line) in h)
    chk("and an on-hand box", ('name="whstock_%d"' % line) in h)
    chk("and a Search on WH button", "js-wh-search" in h)
    # Everything commercial stays out of it.
    panel = re.search(r'proc\.wh_gate(.{0,3000}?)</form>', h, re.S)
    body = panel.group(1) if panel else ""
    chk("NO unit price in the panel", "unit_price" not in body)
    chk("NO supplier in the panel", "linevendor" not in body)
    chk("NO market research in the panel", "mrArea" not in body and "li-research" not in body)

    hb = buyer.get("/procurement/pr/%d" % pid).get_data(as_text=True)
    chk("the buyer does not see it — it is not their rung",
        'name="whqty_%d"' % line not in hb)

    # ---- 2. it corrects the quantity, and says so -------------------------
    print("\nthe store corrects the quantity in place")
    tok = re.search(r'name="_csrf" value="([^"]+)"', h).group(1)
    r = store.post("/procurement/pr/%d/warehouse" % pid, data={
        "whqty_%d" % line: "4", "whstock_%d" % line: "6", "_csrf": tok})
    chk("the correction is accepted", r.status_code in (200, 302), r.status_code)

    with app.app_context():
        from app.db import get_db
        conn = get_db()
        row = dict(conn.execute(
            "SELECT qty, current_stock, unit_price FROM pr_items WHERE id=?",
            (line,)).fetchone())
        ev = [dict(x) for x in conn.execute(
            "SELECT action, detail FROM pr_events WHERE pr_id=? AND "
            "action='warehouse_revised'", (pid,)).fetchall()]
        note = [dict(x) for x in conn.execute(
            "SELECT target_user, message FROM notifications "
            "WHERE module='procurement' AND title LIKE 'Quantity corrected%'").fetchall()]
        conn.close()
    chk("the quantity moved 10 -> 4", float(row["qty"]) == 4.0, row["qty"])
    chk("the stock figure was recorded", float(row["current_stock"]) == 6.0,
        row["current_stock"])
    chk("and NO price was touched", float(row["unit_price"] or 0) == 0.0,
        row["unit_price"])
    chk("the change is on the audit trail, by figure",
        ev and "10" in ev[0]["detail"] and "4" in ev[0]["detail"],
        ev[0]["detail"] if ev else None)
    chk("and the REQUESTER was told what changed",
        any(n["target_user"] == "wr_req" for n in note),
        [n["target_user"] for n in note])

    # ---- 3. THE RULE: not after the request carries money ------------------
    print("\nbut never once the request carries a price")
    with app.app_context():
        from app.approvals import services as svc
        svc.price_pr(pid, {line: 250}, {}, signer["purchasing"])
        ok, msg = svc.warehouse_revise(pid, {line: 99}, {}, signer["warehouse"])
    chk("a priced request refuses a quantity change",
        not ok and msg == "already_priced", msg)
    with app.app_context():
        from app.db import get_db
        conn = get_db()
        q = conn.execute("SELECT qty FROM pr_items WHERE id=?", (line,)).fetchone()["qty"]
        conn.close()
    chk("and the quantity really did not move", float(q) == 4.0, q)

    h2 = store.get("/procurement/pr/%d" % pid).get_data(as_text=True)
    chk("the panel is no longer drawn either",
        'name="whqty_%d"' % line not in h2)

    # ---- 4. somebody else's rung cannot use it ----------------------------
    # On a FRESH, unpriced request. Refusing the finance user on the PRICED one
    # above would prove only that the pricing guard fires first — a different
    # rule — and would leave this one untested while looking like it passed.
    print("\nand it is not a back door for anyone else")
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        pid2, _ = svc.create_pr(
            {"title": "Second belt", "department": "IT", "currency": "EGP"},
            [{"item": "Drive belt A42", "unit": "Pcs", "qty": 8, "unit_price": 0}],
            {"username": "wr_req", "id": 77}, priced=False)
        conn = get_db()
        line2 = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                             (pid2,)).fetchone()["id"]
        stage = conn.execute(
            "SELECT stage FROM pr_steps WHERE pr_id=? AND status='pending' "
            "ORDER BY seq LIMIT 1", (pid2,)).fetchone()["stage"]
        conn.close()
        ok2, msg2 = svc.warehouse_revise(pid2, {line2: 1}, {}, signer["finance"])
        ok3, msg3 = svc.warehouse_revise(pid2, {line2: 3}, {}, signer["warehouse"])
    chk("the fresh request really is on the warehouse rung and unpriced",
        stage == "warehouse", stage)
    chk("finance is refused as INELIGIBLE, not for some unrelated reason",
        not ok2 and msg2 == "not_eligible", msg2)
    chk("while the store is allowed on that very same request", ok3, msg3)

    # ---- 5. the stock search finds what the stores hold --------------------
    print("\nSearch on WH looks in the stores, not the catalogue")
    s = store.get("/api/lookup/wh-stock?q=belt")
    res = s.get_json().get("results", []) if s.status_code == 200 else []
    chk("the search answers", s.status_code == 200, s.status_code)
    hit = next((x for x in res if x["value"] == "SP-WR-1"), None)
    chk("it finds the spare", bool(hit), [x["value"] for x in res][:4])
    chk("reporting what is on hand", hit and hit["on_hand"] == 40, hit and hit["on_hand"])
    chk("what is reserved", hit and hit["reserved"] == 30, hit and hit["reserved"])
    chk("and FREE, which is the figure that matters",
        hit and hit["free"] == 10, hit and hit["free"])
    chk("with where to find it", hit and "A-12" in (hit["where"] or ""),
        hit and hit["where"])

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
