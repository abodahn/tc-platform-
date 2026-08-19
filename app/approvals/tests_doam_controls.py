"""DOAM §3.4 split-purchase aggregation and §4.3 single-source escalation.

Both were previously DEFINED and unreachable — a matrix and a level_above()
with no call sites. These checks run through create_pr/submit_pr/set_single_source,
the same entry points the web form uses, so they fail if the wiring is removed.
"""
import os
import tempfile


def _app():
    # Config.DB_PATH is the switch the app actually reads — an env var here would
    # silently run these checks against the live beta database.
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "doamctl.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _stages(conn, pr_id):
    return [r["stage"] for r in conn.execute(
        "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        user = {"username": "splitter", "full_name": "Splitter", "id": 1}
        bearing = [{"item": "Bearing 6204", "qty": 1, "unit_price": 9000}]

        # ---- §3.4: three 9,000 requests must not each duck the 10,000 rung ----
        a, _ = svc.create_pr({"title": "Bearings 1", "department": "Maintenance"},
                             bearing, user)
        b, _ = svc.create_pr({"title": "Bearings 2", "department": "Maintenance"},
                             [{"item": "bearing 6204 ", "qty": 1, "unit_price": 9000}],
                             user)
        # An unrelated purchase in the same department must NOT be dragged in.
        c, _ = svc.create_pr({"title": "Gloves", "department": "Maintenance"},
                             [{"item": "Nitrile gloves", "qty": 1, "unit_price": 9000}],
                             user)

        conn = get_db()
        first, second, other = _stages(conn, a), _stages(conn, b), _stages(conn, c)
        agg = conn.execute("SELECT agg_total FROM pr_requests WHERE id=?",
                           (b,)).fetchone()["agg_total"]
        conn.close()

        assert "factory_manager" not in first, \
            "the first 9k request has nothing to aggregate with: %s" % first
        assert agg and abs(agg - 18000) < 0.01, "aggregate should be 18,000, got %r" % agg
        assert "factory_manager" in second, \
            "the second 9k request must route on the 18k aggregate: %s" % second
        assert other == first, \
            "an unrelated item must not be aggregated: %s vs %s" % (other, first)

        # ---- §4.3: waiving competition costs one extra signature ----
        d, _ = svc.create_pr({"title": "OEM controller", "department": "Production"},
                             [{"item": "Servo controller", "qty": 1, "unit_price": 60000}],
                             {"username": "buyer", "full_name": "Buyer", "id": 2})
        conn = get_db()
        before = _stages(conn, d)
        conn.close()

        ok, msg = svc.set_single_source(d, "Proprietary OEM part, no alternative supplier",
                                        {"username": "purch", "id": 3})
        assert ok, "set_single_source failed: %s" % msg
        conn = get_db()
        after = _stages(conn, d)
        conn.close()

        assert len(after) == len(before) + 1, \
            "single source must add exactly one rung: %s -> %s" % (before, after)
        added = after[-1]
        top_level = C.DOAM_LEVEL.get(before[-1], "L4")
        assert C.LEVEL_ORDER.index(C.DOAM_LEVEL[added]) > C.LEVEL_ORDER.index(top_level), \
            "%s is not above %s" % (added, before[-1])

        # Idempotent: recording the justification twice must not buy two rungs.
        svc.set_single_source(d, "Proprietary OEM part — reworded", {"username": "purch", "id": 3})
        conn = get_db()
        again = _stages(conn, d)
        conn.close()
        assert again == after, "a second justification added another rung: %s" % again

        print("split 9k+9k ->", " -> ".join(second), "(aggregate %.0f)" % agg)
        print("single source ->", " -> ".join(before), "+", added)
        print("PASS: aggregation and single-source escalation are wired")
        return True


if __name__ == "__main__":
    run()
