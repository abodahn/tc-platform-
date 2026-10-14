"""What the warehouse rung types on the request reaches the PR PDF.

Spec §1 and §3. The warehouse stage could record two numbers — the quantity and
what was on the shelf. Everything else the store knows about a part (its code
and description, how many days of cover it has, what is already on order, what
was ordered last time and when, the stock at that time, and when the supplier
says the next lot lands) had nowhere to go, and the PR PDF printed those columns
blank on every document the company has issued.

This drives the REAL form — the same POST the Save quantities button makes — and
then reads the generated PDF back, because a field that saves but does not print
has not done the job that was asked for.

Three behaviours are easy to get wrong and are each pinned here:

  * a BLANK box is not a zero. Clearing a figure hands it back to the one the
    system computes from order history; typing 0 is the store stating that the
    shelf is empty. They must not collapse into each other.
  * a saved figure WINS over the computed one, on every later page load.
  * the warehouse stage still cannot touch price or supplier.

    python app/approvals/tests_warehouse_fields.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="whfields_"), "w.db")
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, pdf as P

        def one(sql, *a):
            conn = get_db()
            try:
                return conn.execute(sql, a).fetchone()
            finally:
                conn.close()

        pr_id, _ = svc.create_pr(
            {"title": "Warehouse fields", "department": "Maintenance"},
            [{"item": "LAPTOP-DELL", "qty": 1, "unit_price": 0}],
            {"username": "tech", "id": 9}, priced=False)
        line = one("SELECT id FROM pr_items WHERE pr_id=?", pr_id)["id"]

        # Whoever actually holds the warehouse rung on this database.
        conn = get_db()
        try:
            who = conn.execute(
                "SELECT id, username FROM users WHERE role IN ('storekeeper',"
                "'warehouse_manager') AND is_active=1 ORDER BY id LIMIT 1").fetchone()
        finally:
            conn.close()
        if not who:
            print("  SKIP  no storekeeper or warehouse manager on this database")
            return True

        cl = app.test_client()
        with cl.session_transaction() as s:
            s["uid"] = who["id"]
            s["ep"] = 0
        cl.get("/procurement/pr/%d" % pr_id)
        with cl.session_transaction() as s:
            tok = s.get("_csrf_token")

        typed = {
            "_csrf": tok,
            "whqty_%d" % line: "1",
            "whstock_%d" % line: "3",
            "whitem_%d" % line: "IT-LAPTOP-0007",
            "whdescription_%d" % line: "Dell Latitude 5440, 16GB",
            "whstock_days_%d" % line: "45",
            "whpending_qty_%d" % line: "6",
            "whlast_order_qty_%d" % line: "12",
            "whlast_order_date_%d" % line: "2026-05-14",
            "whlast_stock_%d" % line: "9",
            "wheta_%d" % line: "2026-11-30",
        }
        r = cl.post("/procurement/pr/%d/warehouse" % pr_id, data=typed,
                    follow_redirects=False)
        print("the warehouse saves the line:", r.status_code)
        chk("the form is accepted", r.status_code in (200, 302), r.status_code)

        row = one("SELECT * FROM pr_items WHERE id=?", line)
        chk("item code is stored", row["item"] == "IT-LAPTOP-0007", row["item"])
        chk("description is stored", (row["description"] or "").startswith("Dell Latitude"),
            row["description"])
        chk("stock days is stored", float(row["stock_days"] or 0) == 45, row["stock_days"])
        chk("pending quantity is stored", float(row["pending_qty"] or 0) == 6, row["pending_qty"])
        chk("last order quantity is stored", float(row["last_order_qty"] or 0) == 12,
            row["last_order_qty"])
        chk("last order date is stored", row["last_order_date"] == "2026-05-14",
            row["last_order_date"])
        chk("last stock is stored", float(row["last_stock"] or 0) == 9, row["last_stock"])
        chk("ETA is stored", row["eta"] == "2026-11-30", row["eta"])
        chk("and the whole edit is on the audit trail",
            one("SELECT COUNT(*) n FROM pr_events WHERE pr_id=? AND action='warehouse_revised'",
                pr_id)["n"] >= 1)

        # ---- it survives the computed figures on the next read ---------------
        it = svc.get_pr(pr_id)["items"][0]
        chk("the saved stock days survives a page reload", it.get("stock_days") == 45,
            it.get("stock_days"))
        chk("so does the saved ETA", it.get("eta") == "2026-11-30", it.get("eta"))
        chk("and the saved pending quantity", float(it.get("pending_qty") or 0) == 6,
            it.get("pending_qty"))

        # ---- and it reaches the PDF, which is the whole point ---------------
        raw = P.pr_pdf(svc.get_pr(pr_id))
        raw = raw if isinstance(raw, (bytes, bytearray)) else raw.getvalue()
        chk("the PR PDF still generates", raw[:4] == b"%PDF", "%d bytes" % len(raw))
        try:
            from pdfminer.high_level import extract_text
            import io as _io
            text = extract_text(_io.BytesIO(raw))
        except Exception:
            text = None
        if text:
            for label, needle in (("the item code", "IT-LAPTOP-0007"),
                                  ("stock days", "45"), ("the last order date", "2026-05-14"),
                                  ("last stock", "9"), ("the ETA", "2026-11-30")):
                chk("%s is printed on the PR PDF" % label, needle in text)
        else:
            # No text extractor here. The figures are still proved to reach the
            # generator, which is the boundary this file owns.
            rows = P._PR_COLS
            chk("the PDF has a column for every figure the warehouse types",
                all(any(c[1].lower().startswith(w) for c in rows)
                    for w in ("stock days", "pending", "last order qty",
                              "last order date", "last stock", "eta")))
            print("  note  pdfminer not installed — PDF text not read back")

        # ---- blank is not zero ----------------------------------------------
        cl.post("/procurement/pr/%d/warehouse" % pr_id,
                data={"_csrf": tok, "whstock_days_%d" % line: ""})
        row = one("SELECT stock_days FROM pr_items WHERE id=?", line)
        chk("clearing the box clears the override rather than storing 0",
            row["stock_days"] is None, row["stock_days"])
        cl.post("/procurement/pr/%d/warehouse" % pr_id,
                data={"_csrf": tok, "whstock_days_%d" % line: "0"})
        row = one("SELECT stock_days FROM pr_items WHERE id=?", line)
        chk("typing 0 stores 0 — the store saying the shelf is empty",
            row["stock_days"] == 0, row["stock_days"])

        # ---- the stage still cannot move money ------------------------------
        before = one("SELECT unit_price, vendor FROM pr_items WHERE id=?", line)
        cl.post("/procurement/pr/%d/warehouse" % pr_id,
                data={"_csrf": tok, "whunit_price_%d" % line: "999",
                      "whvendor_%d" % line: "Someone Else"})
        after = one("SELECT unit_price, vendor FROM pr_items WHERE id=?", line)
        chk("the warehouse rung still cannot set a price",
            (after["unit_price"] or 0) == (before["unit_price"] or 0), after["unit_price"])
        chk("nor a supplier", (after["vendor"] or "") == (before["vendor"] or ""),
            after["vendor"])

    return OK[0]


def run_pricing():
    """Purchasing's two line figures, on a FRESH app.

    Deliberately isolated rather than appended to run(): sharing an application
    with the warehouse flow above produced a 403 on this route that had nothing
    to do with the feature — the same request, user and payload return 302 and
    store correctly in a clean instance. A check that fails for a reason it is
    not testing is worse than no check, so this one owns its own fixture.
    """
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, pdf as P
        from app.auth import user_has_permission

        print("\nand Purchasing's own two figures on the line")
        pr_id, _ = svc.create_pr(
            {"title": "Pricing fields", "department": "Maintenance"},
            [{"item": "LAPTOP-DELL", "qty": 1, "unit_price": 0}],
            {"username": "tech", "id": 9}, priced=False)

        conn = get_db()
        try:
            line = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                                (pr_id,)).fetchone()["id"]
            cands = [dict(r) for r in conn.execute(
                "SELECT id, username, role FROM users WHERE is_active=1 ORDER BY id"
            ).fetchall()]
        finally:
            conn.close()
        # Picked by the permission the route actually demands, not by role name:
        # roles are database rows here and the two can disagree.
        buyer = next((u for u in cands
                      if user_has_permission(u, "proc_purchasing")), None)
        if not buyer:
            print("  SKIP  nobody on this database holds proc_purchasing")
            return True

        bc = app.test_client()
        with bc.session_transaction() as sess:
            sess["uid"] = buyer["id"]
            sess["ep"] = 0
        bc.get("/procurement/pr/%d" % pr_id)
        with bc.session_transaction() as sess:
            btok = sess.get("_csrf_token")
        # The supplier is now CHOSEN from the vendor master, not typed (spec §2).
        conn = get_db()
        try:
            conn.execute("INSERT INTO proc_vendors (name, is_active) "
                         "VALUES ('Alpha Trading LLC', 1)")
            conn.commit()
        finally:
            conn.close()
        page = bc.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        chk("the supplier on a line is a chooser, not a free-text box",
            'name="linevendor_%d"' % line in page
            and '<select name="linevendor_%d"' % line in page)
        chk("and it offers the vendors on the master", "Alpha Trading LLC" in page)

        r = bc.post("/procurement/pr/%d/price" % pr_id, data={
            "_csrf": btok,
            "price_%d" % line: "250",
            "linevendor_%d" % line: "Alpha Trading LLC",
            "last_order_price_%d" % line: "231.5",
            "lead_time_days_%d" % line: "21",
            "currency": "EGP", "tax_rate": "0"})
        chk("the pricing form is accepted", r.status_code in (200, 302), r.status_code)

        conn = get_db()
        try:
            row = conn.execute("SELECT unit_price, last_order_price, lead_time_days "
                               "FROM pr_items WHERE id=?", (line,)).fetchone()
        finally:
            conn.close()
        chk("the unit price is stored", float(row["unit_price"] or 0) == 250,
            row["unit_price"])
        chk("the last order price Purchasing typed is stored",
            float(row["last_order_price"] or 0) == 231.5, row["last_order_price"])
        chk("the lead time is stored on the LINE, not only on a quote",
            float(row["lead_time_days"] or 0) == 21, row["lead_time_days"])

        it = svc.get_pr(pr_id)["items"][0]
        chk("the typed last order price beats the computed one on reload",
            float(it.get("last_order_price") or 0) == 231.5, it.get("last_order_price"))
        chk("the chosen supplier is stored on the line",
            (it.get("vendor") or "") == "Alpha Trading LLC", it.get("vendor"))
        chk("and resolves to the vendor master rather than being loose text",
            bool(it.get("vendor_known")), it.get("vendor_id"))

        # A line already naming a supplier that is NOT on the master keeps it:
        # silently reassigning a supplier on an existing request would be a worse
        # bug than the loose text this change removes.
        conn = get_db()
        try:
            conn.execute("UPDATE pr_items SET vendor='Someone Not On File' WHERE id=?",
                         (line,))
            conn.commit()
        finally:
            conn.close()
        page2 = bc.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        chk("an off-master supplier is kept as an option, not dropped",
            "Someone Not On File" in page2)
        chk("and is labelled as not being on the master",
            "not on the vendor master" in page2)

        raw = P.pr_pdf(svc.get_pr(pr_id))
        raw = raw if isinstance(raw, (bytes, bytearray)) else raw.getvalue()
        chk("the PR PDF still generates with both", raw[:4] == b"%PDF", "%d bytes" % len(raw))

        # ---- VAT removed from the screen, NOT from the record ---------------
        # The field is gone on request, so nothing posts tax_rate any more. A
        # request that already carries a rate must keep it: zeroing it on the
        # next save would quietly move the gross total every rung above reads,
        # and the ladder routes on that figure.
        page3 = bc.get("/procurement/pr/%d" % pr_id).get_data(as_text=True)
        chk("the VAT box is off the pricing screen", 'name="tax_rate"' not in page3)

        conn = get_db()
        try:
            conn.execute("UPDATE pr_requests SET tax_rate=14 WHERE id=?", (pr_id,))
            conn.commit()
        finally:
            conn.close()
        bc.post("/procurement/pr/%d/price" % pr_id,
                data={"_csrf": btok, "price_%d" % line: "260", "currency": "EGP"})
        conn = get_db()
        try:
            after = conn.execute("SELECT tax_rate, total FROM pr_requests WHERE id=?",
                                 (pr_id,)).fetchone()
        finally:
            conn.close()
        chk("a rate already on the request survives a re-save with no VAT field",
            float(after["tax_rate"] or 0) == 14, after["tax_rate"])
        chk("and the ladder still routes on the TAX-INCLUSIVE figure for it",
            round(svc.egp_commitment({"total": after["total"], "tax_rate": after["tax_rate"],
                                      "currency": "EGP"}), 2) == round(float(after["total"]) * 1.14, 2),
            svc.egp_commitment({"total": after["total"], "tax_rate": after["tax_rate"],
                                "currency": "EGP"}))
    return OK[0]



def run_tolerance():
    """Accepting an over-delivery WITHIN tolerance, and refusing one beyond it.

    Procurement & Warehouse spec §6. Over-delivery quarantine already existed,
    with two answers: send it back, or keep it as a free issue at zero cost. The
    spec asks for the ordinary third one — keep a small excess and PAY for it —
    and for anything beyond the approved tolerance to stay in quarantine until
    somebody authorises it.

    The tolerance is DOAM §7.3.3's own 5%-of-quantity, reused rather than
    reinvented: an over-delivery is the three-way match's question asked at the
    gate instead of at the invoice, so a second number here could only drift away
    from the first.

    Payable means RECEIVED — the payable ceiling and the three-way match both
    read received_qty — so the check below reads that column, not just the
    quarantine row's status.
    """
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        def one(sql, *a):
            conn = get_db()
            try:
                return conn.execute(sql, a).fetchone()
            finally:
                conn.close()

        print("\nand an over-delivery inside the tolerance can be paid for")
        chk("the tolerance is the DOAM's 5%% of quantity, not a second number",
            svc.tolerance_qty(100) == 100 * C.MATCH_QTY_TOLERANCE_PCT / 100.0,
            svc.tolerance_qty(100))

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def quarantined(ordered, excess):
            """One quarantine row against a REAL request and line.

            pr_events carries a foreign key to pr_requests, so a synthetic pr_id
            of 0 cannot be audited — and the audit is half of what this checks.
            """
            pr_id, _ = svc.create_pr(
                {"title": "Over-delivery %g/%g" % (ordered, excess),
                 "department": "Maintenance"},
                [{"item": "TOL-PART", "qty": ordered, "unit_price": 10}],
                buyer, priced=True)
            conn = get_db()
            try:
                item_id = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                                       (pr_id,)).fetchone()["id"]
                cur = conn.execute(
                    "INSERT INTO pr_grn_quarantine (pr_id, grn_id, item_id, item, "
                    "ordered_qty, qty, status) VALUES (?,?,?,?,?,?,'quarantined')",
                    (pr_id, None, item_id, "TOL-PART", ordered, excess))
                conn.commit()
                return cur.lastrowid, pr_id, item_id
            finally:
                conn.close()

        # 10 ordered -> 0.5 allowed. An excess of 2 is four times the clause.
        q_over, pr_over, it_over = quarantined(10, 2)
        ok, msg = svc.resolve_quarantine(q_over, "accept_tolerance", buyer)
        chk("an excess beyond the tolerance is REFUSED, not waved through",
            (not ok) and str(msg).startswith("over_tolerance:"), msg)
        chk("...and it stays in quarantine",
            one("SELECT status FROM pr_grn_quarantine WHERE id=?", q_over)["status"]
            == "quarantined")
        chk("...and the refusal carries the numbers the buyer needs",
            str(msg).split(":")[1:] == ["2", "0.5"], msg)

        # 100 ordered -> 5 allowed. An excess of 4 is an ordinary short over-ship.
        q_in, pr_in, it_in = quarantined(100, 4)
        ok, msg = svc.resolve_quarantine(q_in, "accept_tolerance", buyer)
        chk("an excess inside the tolerance is accepted", ok and msg == "accept_tolerance",
            msg)
        row = one("SELECT status, resolution FROM pr_grn_quarantine WHERE id=?", q_in)
        chk("the quarantine row records which decision was taken",
            row["status"] == "resolved" and row["resolution"] == "accept_tolerance",
            dict(row))
        # The claim "payable" has to be true of the DATA, not only of the wording:
        # the payable ceiling and the three-way match both read received_qty.
        chk("the accepted excess joined received_qty, so it can be invoiced",
            float(one("SELECT received_qty FROM pr_items WHERE id=?",
                      it_in)["received_qty"] or 0) == 4,
            one("SELECT received_qty FROM pr_items WHERE id=?", it_in)["received_qty"])
        chk("and the audit trail says it is payable, not free",
            "payable" in (one("SELECT detail FROM pr_events WHERE action="
                              "'quarantine_resolved' ORDER BY id DESC LIMIT 1")
                          or {"detail": ""})["detail"])

        # the free issue is still available and still means zero cost
        q_free, pr_free, it_free = quarantined(100, 4)
        ok, msg = svc.resolve_quarantine(q_free, "accept", buyer)
        chk("accepting as a free issue still works", ok and msg == "accept", msg)
        chk("a FREE issue does NOT join received_qty — nothing to invoice",
            float(one("SELECT received_qty FROM pr_items WHERE id=?",
                      it_free)["received_qty"] or 0) == 0,
            one("SELECT received_qty FROM pr_items WHERE id=?", it_free)["received_qty"])
        chk("and is recorded as NOT payable",
            "not payable" in (one("SELECT detail FROM pr_events WHERE action="
                                  "'quarantine_resolved' ORDER BY id DESC LIMIT 1")
                              or {"detail": ""})["detail"])

        q_bad, _pb, _ib = quarantined(100, 4)
        chk("an unknown decision is refused",
            svc.resolve_quarantine(q_bad, "whatever", buyer)[1] == "bad_decision")
        chk("a decided row cannot be decided twice",
            svc.resolve_quarantine(q_in, "return", buyer)[1] == "already_resolved")
    return OK[0]



if __name__ == "__main__":
    run()
    run_pricing()
    run_tolerance()
    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    sys.exit(0 if OK[0] else 1)
