"""Two DOAM controls compared a foreign-currency figure against an EGP one.

The value ladder converts (egp_commitment multiplies by fx_rate). The §4.4
deviation grader and the §7.3.3 match tolerance did not, and the audit proved
both on real requests:

  * grading — 1,200 EGP x 200 = 240,000 EGP grades price_over_15 and pulls in
    the Managing Director. USD 24 at fx 50 is the SAME 240,000 EGP and graded
    on_plan, adding nothing.
  * tolerance — MATCH_TOLERANCE_ABS is 500 EGP, applied raw in the PR's
    currency. On a USD PO at fx 50 that is 25,000 EGP, fifty times the clause,
    and a 45% over-invoice returned "matched" with no flags.

Both fail in ONE direction. Every currency in C.CURRENCIES trades above the
pound, so a missing conversion always makes the foreign figure look smaller —
under-collecting approvals, over-granting tolerance, never the reverse. That is
why these checks assert the EGP and foreign cases produce the SAME outcome
rather than merely asserting the foreign one is non-zero.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "fx.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        # A catalogue item with an EGP target of 1,000.
        conn = get_db()
        conn.execute("INSERT INTO proc_items (code, name, cost_price, has_cost, active) "
                     "VALUES (?,?,?,1,1)", ("FX-TGT", "Servo drive", 1000.0))
        iid = conn.execute("SELECT id FROM proc_items WHERE code=?",
                           ("FX-TGT",)).fetchone()["id"]
        conn.commit(); conn.close()

        def graded(currency, fx, unit_price):
            """Grade one line through the real flow and return its grade."""
            pid, _ = svc.create_pr(
                {"title": "Servo drive", "department": "General Maintenance",
                 "currency": currency},
                [{"item": "Servo drive", "qty": 1, "unit_price": 0, "item_id": iid}],
                {"username": "tech", "id": 9}, priced=False)
            conn = get_db()
            if currency != "EGP":
                conn.execute("UPDATE pr_requests SET fx_rate=? WHERE id=?", (fx, pid))
                conn.commit()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pid,)).fetchone()["id"]
            conn.close()
            meta = {} if currency == "EGP" else {"fx_rate": fx}
            ok, msg = svc.price_pr(pid, {li: unit_price}, meta, buyer)
            assert ok, "pricing failed for %s: %s" % (currency, msg)
            conn = get_db()
            dev = svc.deviation_findings(conn, pid)
            rungs = [r["stage"] for r in conn.execute(
                "SELECT stage FROM pr_steps WHERE pr_id=? AND origin='deviation'",
                (pid,)).fetchall()]
            conn.close()
            return dev["lines"][0], rungs

        # 1,200 EGP is 20% over a 1,000 target -> more than 15% over.
        egp_line, egp_rungs = graded("EGP", 1.0, 1200.0)
        # USD 24 at 50 EGP/USD is 1,200 EGP — the SAME money.
        usd_line, usd_rungs = graded("USD", 50.0, 24.0)

        assert egp_line["grade"] == "price_over_15", egp_line
        assert usd_line["grade"] == egp_line["grade"], (
            "the same money graded differently by currency: EGP %s vs USD %s"
            % (egp_line["grade"], usd_line["grade"]))
        assert abs((usd_line["pct_over"] or 0) - (egp_line["pct_over"] or 0)) < 0.5, (
            "percentage over target differs by currency: %s vs %s"
            % (egp_line["pct_over"], usd_line["pct_over"]))
        assert usd_rungs == egp_rungs and usd_rungs, (
            "the foreign order bought different signatures: EGP %s vs USD %s"
            % (egp_rungs, usd_rungs))

        # A genuinely on-target foreign price must still be on plan — the fix
        # must not make everything escalate.
        on_target, on_rungs = graded("USD", 50.0, 20.0)      # 20 x 50 = 1,000
        assert on_target["grade"] == "on_plan", on_target
        assert on_rungs == [], on_rungs

        # ---- §7.3.3 tolerance is an EGP floor -------------------------------
        assert C.match_tolerance_value(1000.0) == 500.0, "EGP unchanged"
        assert abs(C.match_tolerance_value(1000.0, fx_rate=50.0) - 20.0) < 1e-9, (
            "500 EGP at fx 50 is 10 USD, so on a 1,000 USD order the 2%% half "
            "(20) is the greater: got %s" % C.match_tolerance_value(1000.0, fx_rate=50.0))
        # the percentage half is a ratio and must not move with the rate
        assert C.match_tolerance_value(100000.0, fx_rate=50.0) == 2000.0
        # a nonsense rate must fall back to the identity, never to zero slack
        for bad in (0, -1, None, "x"):
            assert C.match_tolerance_value(1000.0, fx_rate=bad) == 500.0, bad

        print("EGP 1,200 ->", egp_line["grade"], egp_rungs)
        print("USD    24 ->", usd_line["grade"], usd_rungs, "(same money, same outcome)")
        print("PASS: grading and match tolerance are currency-correct")
        return True


if __name__ == "__main__":
    run()
