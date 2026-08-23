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
    # The button is useless without the code that opens the dialog, and that is
    # exactly what went wrong: the script was appended to the page's OTHER script
    # block, which lives inside {% if needs_pricing %} and therefore only ever
    # rendered for Purchasing. The store got a button wired to nothing.
    chk("...and the code that opens it, in the same render",
        "function openDlg(" in h and "whDlgBody" in h)
    chk("the dialog itself is on the page", 'id="whDlg"' in h)
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

    # ---- 6. issuing off the shelf, which really moves stock ---------------
    print("\nthe store hands it over instead of buying it")
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO mnt_spare_parts (code, name, uom, stock_qty, "
            "reserved_qty, warehouse, is_active) VALUES "
            "('SP-ISS-1','Needle plate','Pcs',20,5,'Main',1)")
        conn.commit()
        conn.close()
        pid3, _ = svc.create_pr(
            {"title": "Plates", "department": "IT", "currency": "EGP"},
            [{"item": "Needle plate", "unit": "Pcs", "qty": 6, "unit_price": 0}],
            {"username": "wr_req", "id": 77}, priced=False)
        conn = get_db()
        line3 = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                             (pid3,)).fetchone()["id"]
        before = conn.execute(
            "SELECT stock_qty FROM mnt_spare_parts WHERE code='SP-ISS-1'"
        ).fetchone()["stock_qty"]
        conn.close()

    # PARTIAL: 4 of the 6 come off the shelf, 2 still have to be bought.
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        ok4, msg4 = svc.issue_from_stock(
            pid3, {line3: {"code": "SP-ISS-1", "qty": 4}}, signer["warehouse"])
        conn = get_db()
        ln = dict(conn.execute(
            "SELECT qty, issued_qty, issued_from FROM pr_items WHERE id=?",
            (line3,)).fetchone())
        pr3 = dict(conn.execute(
            "SELECT status, stock_outcome FROM pr_requests WHERE id=?",
            (pid3,)).fetchone())
        after = conn.execute(
            "SELECT stock_qty FROM mnt_spare_parts WHERE code='SP-ISS-1'"
        ).fetchone()["stock_qty"]
        mv = [dict(r) for r in conn.execute(
            "SELECT type, qty, before_qty, after_qty FROM mnt_stock_movements "
            "WHERE request_id=? ORDER BY id DESC LIMIT 1", (pid3,)).fetchall()]
        conn.close()
    chk("a partial issue is accepted", ok4 and msg4 == "partly_issued", msg4)
    chk("THE SHELF REALLY MOVED, 20 -> 16", float(after) == 16.0,
        "%s -> %s" % (before, after))
    chk("with a movement recorded against the request",
        mv and mv[0]["type"] == "issue" and float(mv[0]["qty"]) == -4.0, mv[:1])
    chk("the line now asks only for what must be BOUGHT (6 - 4 = 2)",
        float(ln["qty"]) == 2.0, ln["qty"])
    chk("and separately records what was issued, so the drop is explained",
        float(ln["issued_qty"]) == 4.0 and ln["issued_from"] == "SP-ISS-1", ln)
    chk("the request keeps circulating for the shortfall",
        pr3["status"] == "pending", pr3["status"])
    chk("marked as partly met from stock", pr3["stock_outcome"] == "partial",
        pr3["stock_outcome"])

    # FULLY: the remaining 2 come off the shelf and the request is finished.
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        ok5, msg5 = svc.issue_from_stock(
            pid3, {line3: {"code": "SP-ISS-1", "qty": 2}}, signer["warehouse"])
        conn = get_db()
        pr3b = dict(conn.execute(
            "SELECT status, stock_outcome FROM pr_requests WHERE id=?",
            (pid3,)).fetchone())
        pend = conn.execute(
            "SELECT COUNT(*) n FROM pr_steps WHERE pr_id=? AND status='pending'",
            (pid3,)).fetchone()["n"]
        note = [dict(x) for x in conn.execute(
            "SELECT target_user, title FROM notifications "
            "WHERE title LIKE '%stock%'").fetchall()]
        conn.close()
    chk("covering the rest closes it", ok5 and msg5 == "met_from_stock", msg5)
    chk("the request is CLOSED, not left waiting for a buyer",
        pr3b["status"] == "closed", pr3b["status"])
    chk("recorded as met from stock", pr3b["stock_outcome"] == "met_from_stock",
        pr3b["stock_outcome"])
    chk("no rung is left pending for somebody to sign", pend == 0, pend)
    chk("and the requester was told",
        any(n["target_user"] == "wr_req" for n in note),
        sorted(x for x in {n["target_user"] for n in note} if x))

    # NOTHING moves that the storekeeper did not explicitly pick.
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        pid4, _ = svc.create_pr(
            {"title": "Untouched", "department": "IT", "currency": "EGP"},
            [{"item": "Needle plate", "unit": "Pcs", "qty": 3, "unit_price": 0}],
            {"username": "wr_req", "id": 77}, priced=False)
        conn = get_db()
        s_before = conn.execute(
            "SELECT stock_qty FROM mnt_spare_parts WHERE code='SP-ISS-1'"
        ).fetchone()["stock_qty"]
        conn.close()
        ok6, msg6 = svc.issue_from_stock(pid4, {}, signer["warehouse"])
        conn = get_db()
        s_after = conn.execute(
            "SELECT stock_qty FROM mnt_spare_parts WHERE code='SP-ISS-1'"
        ).fetchone()["stock_qty"]
        conn.close()
    chk("issuing with nothing picked moves nothing at all",
        ok6 and msg6 == "nothing_issued" and float(s_before) == float(s_after),
        "%s | %s -> %s" % (msg6, s_before, s_after))

    # And the shelf cannot be taken below what is genuinely free.
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        line4 = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                             (pid4,)).fetchone()["id"]
        conn.execute("UPDATE pr_items SET qty=999 WHERE id=?", (line4,))
        conn.commit()
        free_before = conn.execute(
            "SELECT stock_qty - COALESCE(reserved_qty,0) AS f FROM mnt_spare_parts "
            "WHERE code='SP-ISS-1'").fetchone()["f"]
        conn.close()
        ok7, msg7 = svc.issue_from_stock(
            pid4, {line4: {"code": "SP-ISS-1", "qty": float(free_before) + 1}},
            signer["warehouse"])
    chk("issuing more than is FREE is refused (reserved stock is not available)",
        not ok7 and msg7 == "not_enough_free_stock", msg7)
    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
