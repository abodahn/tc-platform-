"""Nobody sets the conditions their own request is judged by.

The ladder has always refused a requester their own SIGNATURE. It never covered
the COMMERCIAL acts that decide what the signature is even for, and PR-2026-000035
is what that cost: buyer.tester raised a request and, in the same minute, priced
it himself — setting the value his own request would route on.

An audit of all 25 state-changing services in this module found three that
checked the actor against the requester. Five more should have, and this file is
the reason they now do. Each one lets the requester decide something about their
own request that somebody else is supposed to decide:

    price_pr            the value every threshold is measured against
    add_quote           the "competing" quotes the RFQ rule counts
    choose_quote        which supplier wins
    set_single_source   waiving the competitive-quote rule entirely
    set_fx              the rate that converts a foreign order into EGP

An admin may still act — visibly and audited, exactly as an admin may sign a
stuck rung — so this file checks that too. A control that cannot be overridden
by anybody becomes a request nobody can finish.

THE OTHER HALF of PR-2026-000035 was that pricing a request on arrival used to
LOCK THE WAREHOUSE OUT: warehouse_revise() refused a quantity change once a
request carried money, so the one rung whose job is "is this the right quantity"
became a rubber stamp. That is checked here too — the correction is now allowed
and the ladder is rebuilt from the new total.

    python app/approvals/tests_self_dealing.py
"""
import os
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
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="selfdeal_"), "s.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc

        def role(r):
            conn = get_db()
            try:
                row = conn.execute("SELECT id, username, role FROM users "
                                   "WHERE role=? AND is_active=1 LIMIT 1", (r,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

        def one(sql, *a):
            conn = get_db()
            try:
                return conn.execute(sql, a).fetchone()
            finally:
                conn.close()

        buyer = role("purchasing_manager")
        store = role("storekeeper")
        admin = role("super_admin")
        other = {"username": "second.buyer", "role": "purchasing_manager", "id": 90001}
        plain = {"username": "plain.requester", "role": "normal_user", "id": 90002}
        if not (buyer and store and admin):
            print("  SKIP  this database has no purchasing / store / admin roster")
            return True

        def mine(title="self-dealing", qty=10, cur="EGP"):
            """A request raised BY THE BUYER — the PR-2026-000035 shape."""
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance", "currency": cur},
                [{"item": "SD-PART", "qty": qty, "unit_price": 0}],
                buyer, submit=True, priced=False)
            return pr_id, one("SELECT id FROM pr_items WHERE pr_id=?", pr_id)["id"]

        # ---- the five commercial acts, on the actor's own request -----------
        print("\n1. the requester may not decide what their own request is judged by")

        pr, line = mine()
        ok, msg = svc.price_pr(pr, {line: 500}, {"tax_rate": 0}, buyer)
        chk("he cannot PRICE it — the price sets his own threshold",
            (not ok) and msg == "self_pricing", msg)

        ok, msg = svc.add_quote(pr, {"vendor": "His Own Vendor", "amount": 500}, buyer)
        chk("nor supply the QUOTES the competitive rule counts",
            (not ok) and msg == "self_quote", msg)

        ok, msg = svc.set_single_source(pr, "only possible supplier", buyer)
        chk("nor WAIVE the competitive rule outright",
            (not ok) and msg == "self_single_source", msg)

        prf, linef = mine(title="foreign", cur="USD")
        ok, msg = svc.set_fx(prf, 1.0, buyer)
        chk("nor set the FX RATE his order converts at",
            (not ok) and msg == "self_fx", msg)

        # ---- and the same acts by somebody else -----------------------------
        print("\n2. ...but a different buyer can do every one of them")
        ok, msg = svc.add_quote(pr, {"vendor": "Alpha", "amount": 500}, other)
        chk("a second buyer may quote", ok, msg)
        ok, msg = svc.set_single_source(pr, "only possible supplier", other)
        chk("a second buyer may waive competition", ok, msg)
        ok, msg = svc.set_fx(prf, 50.0, other)
        chk("a second buyer may set the rate", ok, msg)
        ok, msg = svc.price_pr(pr, {line: 500}, {"tax_rate": 0}, other)
        chk("a second buyer may price it", ok, msg)

        # ---- the admin escape hatch -----------------------------------------
        print("\n3. an admin may still act, because a control nobody can override")
        print("   is a request nobody can finish")
        pr2, line2 = mine(title="admin escape")
        ok, msg = svc.price_pr(pr2, {line2: 250}, {"tax_rate": 0}, admin)
        chk("an admin prices a request the buyer raised", ok, msg)

        # ---- the other half: the warehouse cannot be locked out -------------
        print("\n4. pricing on arrival no longer makes the warehouse a rubber stamp")
        pr3, line3 = svc.create_pr(
            {"title": "locked out?", "department": "Maintenance", "currency": "EGP"},
            [{"item": "SD-PART", "qty": 10, "unit_price": 0}],
            plain, submit=True, priced=False)[0], None
        line3 = one("SELECT id FROM pr_items WHERE pr_id=?", pr3)["id"]
        ok, msg = svc.price_pr(pr3, {line3: 500}, {"tax_rate": 0}, buyer)
        chk("the buyer prices it the moment it arrives, at the warehouse rung",
            ok, msg)
        chk("...so it now carries money", float(
            one("SELECT total FROM pr_requests WHERE id=?", pr3)["total"] or 0) == 5000)

        ok, msg = svc.warehouse_revise(pr3, {line3: 4}, {line3: 4}, store)
        chk("the warehouse can STILL correct the quantity", ok, msg)
        chk("the quantity really moved",
            float(one("SELECT qty FROM pr_items WHERE id=?", line3)["qty"]) == 4.0)
        chk("and the total was recomputed from it, not left stale",
            float(one("SELECT total FROM pr_requests WHERE id=?", pr3)["total"]) == 2000.0,
            one("SELECT total FROM pr_requests WHERE id=?", pr3)["total"])

        # ---- and a bigger quantity collects the approvers it now needs -------
        print("\n5. a quantity that raises the value collects the rungs it needs")
        conn = get_db()
        try:
            conn.execute("INSERT INTO proc_budgets (department, period, currency, amount) "
                         "VALUES ('Maintenance','2026','EGP',99000000)")
            conn.commit()
        finally:
            conn.close()
        pr4 = svc.create_pr(
            {"title": "raise it", "department": "Maintenance", "currency": "EGP"},
            [{"item": "SD-PART", "qty": 1, "unit_price": 0}],
            plain, submit=True, priced=False)[0]
        line4 = one("SELECT id FROM pr_items WHERE pr_id=?", pr4)["id"]
        svc.price_pr(pr4, {line4: 5000}, {"tax_rate": 0}, buyer)

        def rungs():
            conn = get_db()
            try:
                return [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq",
                    (pr4,)).fetchall()]
            finally:
                conn.close()

        before = rungs()
        svc.warehouse_revise(pr4, {line4: 150}, {line4: 0}, store)
        after = rungs()
        chk("5,000 routed the demand rungs only", "cfo" not in before, before)
        chk("750,000 pulled in the Plant Director and the CFO",
            "factory_manager" in after and "cfo" in after, after)
        chk("and nothing already on the ladder was dropped",
            all(s in after for s in before), (before, after))

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
