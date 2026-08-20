"""Per-line vendors must not break a request raised before they existed.

Production has requests mid-flight. Every one of them carries a header vendor
and NO vendor on any line, and `po_no` is read downstream by receiving,
invoicing and the three-way match. So the question this file answers is not
"does the new shape work" — tests_po_per_vendor and tests_rfq already answer
that — but "does the OLD shape still open, print, receive and pay, keeping the
same PO number it was already issued under".

Nothing here is injected: the request is created the way the UI makes one
(priced=False, unit_price 0), priced at the gate, signed up the real ladder.

    python app/approvals/tests_legacy_compat.py
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="legacy_"), "compat.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    ok_all = [True]

    def chk(label, cond, extra=""):
        ok_all[0] &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))

    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C, pdf as P

        conn = get_db()
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            uname = "lg_" + stage
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (uname, "x", uname, role))
            signer[stage] = {"username": uname, "role": role}
        conn.commit()
        for stage in signer:
            signer[stage]["id"] = conn.execute(
                "SELECT id FROM users WHERE username=?",
                (signer[stage]["username"],)).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO proc_vendors (name,is_active) VALUES (?,1)",
                     ("Legacy Vendor",))
        conn.commit()
        conn.close()
        buyer = signer["purchasing"]
        reqr = {"username": "lg_req", "id": 77}

        def stage_now(pr_id):
            conn = get_db()
            r = conn.execute("SELECT stage FROM pr_steps WHERE pr_id=? AND "
                             "status='pending' ORDER BY seq LIMIT 1", (pr_id,)).fetchone()
            conn.close()
            return r["stage"] if r else None

        # ---- the old shape: header vendor, no line vendor anywhere ----------
        # MRO spares, so the §3.4 cost-object gate is satisfied by the asset /
        # cost-centre route and this file stays about vendors, not sales orders.
        LINES = [("Bearing 6204", "Pcs", 400, 120.0),
                 ("Drive belt A42", "Pcs", 50, 300.0)]
        pr_id, _ = svc.create_pr(
            {"title": "Legacy request", "department": "Production",
             "currency": "EGP", "vendor": "Legacy Vendor"},
            [{"item": i, "unit": u, "qty": q, "unit_price": 0} for i, u, q, _ in LINES],
            reqr, priced=False)
        # A request raised today backfills the header vendor onto every line.
        # A request raised BEFORE this change has NULL there and no backfill ever
        # ran, so that is the row shape production actually holds — reproduce it
        # exactly rather than testing the convenience of the new writer.
        conn = get_db()
        lids = [r["id"] for r in conn.execute(
            "SELECT id FROM pr_items WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
        conn.execute("UPDATE pr_items SET vendor=NULL WHERE pr_id=?", (pr_id,))
        conn.commit()
        vends = [r["vendor"] for r in conn.execute(
            "SELECT COALESCE(vendor,'') AS vendor FROM pr_items WHERE pr_id=?",
            (pr_id,)).fetchall()]
        conn.close()
        chk("a pre-existing request carries NO line vendor",
            all(v == "" for v in vends), vends)

        # the grouping every document now goes through
        bundle = svc.get_pr(pr_id)
        groups = svc.po_groups(bundle["items"], bundle["pr"])
        chk("and still groups into exactly ONE document",
            len(groups) == 1, list(groups))
        chk("under the HEADER vendor, not blank",
            list(groups) == ["Legacy Vendor"], list(groups))

        total = sum(q * u for _i, _m, q, u in LINES)
        quoted = [False]
        ok, msg = svc.price_pr(pr_id, {li: u for li, (_i, _m, _q, u) in zip(lids, LINES)},
                               {}, buyer)
        chk("prices at the gate", ok, msg)
        for _ in range(15):
            st = stage_now(pr_id)
            if st is None:
                break
            ok, msg = svc.act_on_step(pr_id, signer[st], "approve")
            if not ok and msg == "needs_quotes":
                if quoted[0]:
                    break            # quotes added and still refused: report it
                quoted[0] = True
                for n, mult in (("A", 1.0), ("B", 1.06), ("C", 1.11)):
                    q_ok, q_msg = svc.add_quote(
                        pr_id, {"vendor": "Legacy Alt %s" % n,
                                "amount": total * mult}, buyer)
                    if not q_ok:
                        msg = "add_quote refused: %s" % q_msg
                        break
                continue
            if not ok:
                break
            if msg == "approved":
                break
        chk("walks the whole ladder to approved", ok, msg)

        ok, msg = svc.issue_po(pr_id, buyer)
        chk("issues its PO", ok, msg)
        conn = get_db()
        pr = conn.execute("SELECT po_no, status FROM pr_requests WHERE id=?",
                          (pr_id,)).fetchone()
        pos = conn.execute("SELECT po_no, vendor FROM pr_purchase_orders WHERE pr_id=? "
                           "ORDER BY id", (pr_id,)).fetchall()
        conn.close()
        chk("exactly one PO row, not one per line", len(pos) == 1, [tuple(p) for p in pos])
        chk("and pr_requests.po_no is the SAME number receiving reads",
            pos and pos[0]["po_no"] == pr["po_no"], "%s vs %s" %
            (pos[0]["po_no"] if pos else None, pr["po_no"]))
        chk("numbered plainly, with no -2 suffix on a single-vendor request",
            pr["po_no"] and not str(pr["po_no"]).endswith("-2"), pr["po_no"])

        # ---- it must still PRINT --------------------------------------------
        b = svc.get_pr(pr_id)
        for name, fn in (("PR", lambda: P.pr_pdf(b)), ("PO", lambda: P.po_pdf(b))):
            try:
                raw = fn()
                chk("%s PDF prints" % name, raw[:4] == b"%PDF" and len(raw) > 2000,
                    "%d bytes" % len(raw))
            except Exception as e:
                chk("%s PDF prints" % name, False, repr(e))

        # ---- and still receive, invoice and pay -----------------------------
        ok, msg = svc.receive_items(pr_id, {lids[0]: 400, lids[1]: 50}, signer["warehouse"])
        chk("goods receive against it", ok, msg)
        ok, msg = svc.add_invoice(pr_id, {"invoice_no": "LGC-1", "amount": total}, buyer)
        chk("invoice posts", ok, msg)
        ok, msg = svc.add_payment(pr_id, {"amount": total}, signer["finance"])
        chk("payment clears the three-way match", ok, msg)

        # ---- an RFQ on a legacy request falls back to the header vendor ------
        pr2, _ = svc.create_pr(
            {"title": "Legacy sourcing", "department": "Production",
             "currency": "EGP", "vendor": "Legacy Vendor"},
            [{"item": "Drive belt A42", "unit": "Pcs", "qty": 4, "unit_price": 0}],
            reqr, priced=False)
        ok, res = svc.issue_rfqs(pr2, ["Legacy Vendor"], buyer)
        chk("RFQ issues on a legacy request too", ok, res)

    print("\n" + ("RESULT: ALL GREEN — legacy requests unaffected"
                  if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
