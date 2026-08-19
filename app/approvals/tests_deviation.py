"""DOAM §4.4 — Purchase Order approval by deviation.

The grades come from the document's own table. What is checked here is that a
deviation actually costs extra SIGNATURES on a real request, not that a helper
returns a label: the whole point of §4.4 is that "an order that would push stock
above the ceiling, or a price above target, cannot pass on the buyer's signature
alone".
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "deviation.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        # --- the grades, straight off the document's table --------------------
        assert C.deviation_grade(0)["grade"] == "on_plan"
        assert C.deviation_grade(3)["grade"] == "price_5"
        assert C.deviation_grade(5)["grade"] == "price_5", "5% is 'up to 5%'"
        assert C.deviation_grade(5.1)["grade"] == "price_15"
        assert C.deviation_grade(15)["grade"] == "price_15", "15% is '5 to 15%'"
        assert C.deviation_grade(15.1)["grade"] == "price_over_15"
        assert C.deviation_grade(20)["stages"] == ["finance", "ceo"]
        assert C.deviation_grade(None, over_ceiling=True)["stages"] == \
            ["factory_manager", "scd"]
        # No target on file must not be graded as infinitely over.
        assert C.price_deviation_pct(500, 0) is None
        assert C.price_deviation_pct(500, None) is None
        assert C.deviation_grade(C.price_deviation_pct(500, 0))["grade"] == "on_plan"

        # --- a real request, priced 20% over target ---------------------------
        conn = get_db()
        conn.execute("INSERT INTO proc_items (code, name, cost_price, has_cost, active) "
                     "VALUES (?,?,?,1,1)", ("BRG-6204", "Bearing 6204", 1000.0))
        item_id = conn.execute("SELECT id FROM proc_items WHERE code=?",
                               ("BRG-6204",)).fetchone()["id"]
        conn.commit()
        conn.close()

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        pr_id, _ = svc.create_pr(
            {"title": "Bearings", "department": "Maintenance"},
            [{"item": "Bearing 6204", "qty": 10, "unit_price": 0, "item_id": item_id}],
            {"username": "tech", "id": 2}, priced=False)

        conn = get_db()
        before = [r["stage"] for r in conn.execute(
            "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
        line_id = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                               (pr_id,)).fetchone()["id"]
        conn.close()

        ok, msg = svc.price_pr(pr_id, {line_id: 1200.0}, {}, buyer)   # 20% over target
        assert ok, "pricing failed: %s" % msg

        conn = get_db()
        after = [r["stage"] for r in conn.execute(
            "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
        dev = svc.deviation_findings(conn, pr_id)
        conn.close()

        assert dev["lines"][0]["grade"] == "price_over_15", dev["lines"][0]
        assert abs(dev["lines"][0]["pct_over"] - 20.0) < 0.05, dev["lines"][0]
        assert "ceo" in after, \
            "a price 20%% over target must pull in the Managing Director: %s" % after
        assert "ceo" not in before, "the MD was already there before pricing: %s" % before
        assert dev["memo"] and dev["quotes"], dev

        # --- a line with no target on file is reported, never invented --------
        blind, _ = svc.create_pr(
            {"title": "Custom bracket", "department": "Maintenance"},
            [{"item": "One-off bracket", "qty": 1, "unit_price": 5000}],
            {"username": "tech", "id": 2})
        conn = get_db()
        bdev = svc.deviation_findings(conn, blind)
        conn.close()
        assert bdev["unassessable"] == 1, bdev
        assert bdev["stages"] == [], "an ungradeable line must not add signatures"

        print("20%% over target ->", " -> ".join(before), "=>", " -> ".join(after))
        print("PASS: deviation grading adds real signatures and never guesses a target")
        return True


if __name__ == "__main__":
    run()
