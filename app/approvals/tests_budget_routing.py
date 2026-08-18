"""DOAM Table 4 — budgeted versus unbudgeted spend, proved on the REAL flow.

§4.1 is titled "OPEX Ladder (BUDGETED)", so its tiers describe PLANNED spend.
Table 4, L1: "GM / CFO — major commitments within board-approved budgets AND
UNBUDGETED ITEMS UP TO THE L1 LIMIT." A request with no approved budget behind
it is therefore an L1 commitment whatever its size, and so is one that pushes a
department past the budget it has.

Every request below is created the way the UI creates one — no prices at all,
total = 0 — and priced afterwards at the pricing gate. That is the first moment
a real commitment value exists, so it is the only place this control can be
tested honestly.

Both outcomes are driven, because a control that always fires is as broken as
one that never does:
  * the same 5,000 EGP routes on the tier-1 ladder INSIDE a budget and picks up
    the CFO OUTSIDE one;
  * a request that tips its department past its budget escalates and records by
    how much;
  * giving the department a budget with room in it takes the rung back off;
  * and an unbudgeted request is never refused — it is signed all the way
    through, one signature higher.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "budget.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _stages(conn, pr_id):
    return [r["stage"] for r in conn.execute(
        "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]


def _unpriced(svc, title, dept, item, qty=1):
    """A request exactly as the UI makes one: no prices at all."""
    return svc.create_pr({"title": title, "department": dept},
                         [{"item": item, "qty": qty, "unit_price": 0}],
                         {"username": "tech", "id": 9}, priced=False)


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def price(pr_id, unit, tax=0):
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
            conn.close()
            ok, msg = svc.price_pr(pr_id, {li: unit}, {"tax_rate": tax}, buyer)
            assert ok, "pricing failed: %s" % msg

        def look(pr_id):
            """(ladder, budget_state, budget_over_by, the note the approver reads)"""
            conn = get_db()
            try:
                r = conn.execute("SELECT budget_state, budget_over_by, status FROM "
                                 "pr_requests WHERE id=?", (pr_id,)).fetchone()
                note = conn.execute("SELECT detail FROM pr_events WHERE pr_id=? AND "
                                    "action='unbudgeted' ORDER BY id DESC LIMIT 1",
                                    (pr_id,)).fetchone()
                return (_stages(conn, pr_id), r["budget_state"], r["budget_over_by"],
                        r["status"], note["detail"] if note else "")
            finally:
                conn.close()

        # ---- inside an approved budget: the §4.1 ladder, unchanged -----------
        svc.set_budget("Quality", 1_000_000)
        a, _ = _unpriced(svc, "Filters", "Quality", "Air filter")
        price(a, 5000)
        la, sa, oa, _st, _n = look(a)
        assert sa == "budgeted", "5,000 inside a 1,000,000 budget is budgeted: %r" % sa
        assert oa is None, "nothing is over: %r" % oa
        assert "cfo" not in la, "a budgeted 5,000 is tier 1 and must NOT reach L1: %s" % la

        # ---- the SAME value with no budget set: escalates to L1 --------------
        b, _ = _unpriced(svc, "Belts", "Cutting", "V-belt A42")
        price(b, 5000)
        lb, sb, ob, stb, nb = look(b)
        assert sb == "no_budget", "Cutting has no budget row: %r" % sb
        assert "cfo" in lb, (
            "§4.1 is the BUDGETED ladder — 5,000 with no approved budget behind "
            "it is an L1 commitment under Table 4: %s" % lb)
        assert la != lb, "the same 5,000 must route differently in and out of budget"
        assert "UNBUDGETED" in nb and "no approved" in nb, (
            "the approver must be told WHY the CFO is on this ladder: %r" % nb)

        # ---- tips the department past its budget: escalates, and says by how much
        svc.set_budget("Finishing", 10_000)
        prior, _ = _unpriced(svc, "Bearings", "Finishing", "Bearing 6204")
        price(prior, 8000)
        lp, sp, _op, _stp, _np = look(prior)
        assert sp == "budgeted", "8,000 of a 10,000 budget still fits: %r" % sp
        assert "cfo" not in lp, "and routes tier 1: %s" % lp
        conn = get_db()                       # it is signed off -> committed spend
        conn.execute("UPDATE pr_requests SET status='approved' WHERE id=?", (prior,))
        conn.commit(); conn.close()

        c, _ = _unpriced(svc, "Lamps", "Finishing", "Desk lamp")
        price(c, 5000)                        # 8,000 committed + 5,000 = 13,000
        lc, sc, oc, _stc, nc = look(c)
        assert sc == "over_budget", "13,000 against a 10,000 budget: %r" % sc
        assert abs((oc or 0) - 3000) < 0.01, "over by 3,000, got %r" % oc
        assert "cfo" in lc, "spend past the budget is an L1 commitment: %s" % lc
        assert "3,000.00" in nc, "the note must state the overage: %r" % nc

        # ---- give the department a budget with room: the rung comes back off --
        svc.set_budget("Cutting", 50_000)
        price(b, 5000)                        # same price, re-saved
        lb2, sb2, _ob2, _stb2, _nb2 = look(b)
        assert sb2 == "budgeted", "Cutting now has room for it: %r" % sb2
        assert "cfo" not in lb2, (
            "a control that never lets go is a control nobody keeps: %s" % lb2)

        # ---- and it is NOT refused: the unbudgeted request signs all the way --
        # §4.3's lowest sourcing band still wants one recorded quote; that gate is
        # not this control's business, so satisfy it and let the ladder run.
        svc.add_quote(c, {"vendor": "Lamp Co", "amount": 5000}, buyer)
        for i, admin in enumerate(({"username": "boss%d" % n, "role": "super_admin",
                                    "id": 100 + n} for n in range(1, 6))):
            ok, msg = svc.act_on_step(c, admin, "approve")
            if msg == "not_pending":
                break
            assert ok, "the DOAM permits unbudgeted spend up to the L1 limit — it " \
                       "must not be blocked, but rung %d refused it: %s" % (i + 1, msg)
        _lc2, _sc2, _oc2, stc2, _nc2 = look(c)
        assert stc2 == "approved", (
            "unbudgeted spend needs higher authority, not refusal: status %r" % stc2)

        # ---- the other routing moment: a draft Purchasing priced BEFORE submit -
        # (the UI's create -> price -> submit path, where the value already
        #  exists when the ladder is first built).
        def priced_draft(title, dept, item, unit):
            pid, _ = svc.create_pr({"title": title, "department": dept},
                                   [{"item": item, "qty": 1, "unit_price": unit}],
                                   buyer, submit=False)
            ok, msg = svc.submit_pr(pid, {"username": "tech", "id": 9})
            assert ok, "submit failed: %s" % msg
            return pid

        d = priced_draft("Gauges", "Embroidery", "Pressure gauge", 7000)
        ld, sd, _od, _std, nd = look(d)
        assert sd == "no_budget" and "cfo" in ld, (
            "submit-time routing must run the same check: %r / %s" % (sd, ld))
        assert "UNBUDGETED" in nd, "and record why at submit too: %r" % nd

        svc.set_budget("Embroidery", 100_000)
        e = priced_draft("Gauges 2", "Embroidery", "Vacuum gauge", 7000)
        le, se, _oe, _ste, _ne = look(e)
        assert se == "budgeted" and "cfo" not in le, (
            "with a budget in place the same submit routes tier 1: %r / %s" % (se, le))

        print("budgeted   5,000 Quality   ->", " -> ".join(la))
        print("unbudgeted 5,000 Cutting   ->", " -> ".join(lb))
        print("over budget 5,000 Finishing->", " -> ".join(lc), "(over by %.0f)" % oc)
        print("re-priced after a budget was set ->", " -> ".join(lb2))
        print("priced-then-submitted 7,000 Embroidery ->", " -> ".join(ld),
              "| with a budget:", " -> ".join(le))
        print("PASS: DOAM Table 4 routes budgeted and unbudgeted spend differently, "
              "on the real flow, and refuses neither")
        return True


if __name__ == "__main__":
    run()
