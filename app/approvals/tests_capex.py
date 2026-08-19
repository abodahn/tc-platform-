"""DOAM §4.2 — the CAPEX flag must reach the ladder.

The bug this guards: the CAPEX matrix existed and was tested, but nothing on a
real request said "this is capital", so a machine purchase routed down the OPEX
ladder — Warehouse signing for a lathe, and the Managing Director never asked.
Every check here runs through the SAME entry points the web form uses
(create_pr -> submit_pr), not build_ladder directly, because the defect was in
the wiring, not the matrix.
"""
import os
import tempfile


def _app():
    # Config.DB_PATH is the switch the app actually reads — an env var here would
    # silently run these checks against the live beta database.
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "capex.db")
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

        user = {"username": "tester", "full_name": "Tester", "id": 1}
        items = [{"item": "X", "qty": 1, "unit_price": 300000}]

        # Thread is a direct material, so DOAM §5 makes the OPEX fixture name the
        # sales order it is costed against (audit 3.4-b2) — this test is about
        # OPEX vs CAPEX routing, not about the golden thread.
        conn = get_db()
        live = conn.execute("SELECT order_no FROM ord_orders WHERE status NOT IN "
                            "('closed','cancelled') ORDER BY id LIMIT 1").fetchone()
        conn.close()
        assert live, "no open sales order on file — the fixture cannot run"

        capex_id, _ = svc.create_pr(
            {"title": "Sewing machine", "department": "Production",
             "expenditure_kind": "capex"}, items, user)
        opex_id, _ = svc.create_pr(
            {"title": "Thread cones", "department": "Production",
             "so_no": live["order_no"]}, items, user)

        conn = get_db()
        capex = _stages(conn, capex_id)
        opex = _stages(conn, opex_id)
        kind = conn.execute("SELECT expenditure_kind FROM pr_requests WHERE id=?",
                            (capex_id,)).fetchone()["expenditure_kind"]
        conn.close()

        assert kind == "capex", "the flag did not persist: %r" % kind
        assert capex != opex, "CAPEX and OPEX route identically — the flag is not reaching the ladder"
        # §4.2: capital has no Warehouse rung; it starts at Purchasing.
        assert "warehouse" not in capex, "CAPEX must not route through Warehouse: %s" % capex
        assert "warehouse" in opex, "OPEX at 300k must still start at Warehouse: %s" % opex
        # §4.2: 300,000 is above the MD threshold (250,000) and below the board's.
        assert "ceo" in capex, "Managing Director must sign 300k capital: %s" % capex
        assert "bod" not in capex, "the Board is not required at 300k: %s" % capex
        # Every level signs capital — Finance and the Plant Director included,
        # which the OPEX ladder only reaches at much higher values.
        for stage in ("purchasing", "factory_manager", "scd", "finance", "cfo"):
            assert stage in capex, "%s must sign capital: %s" % (stage, capex)
        # A department responsibility matrix must not shorten a capital ladder.
        conn = get_db()
        conn.execute("INSERT INTO proc_resp_matrix (department, stage, threshold, seq, active) "
                     "VALUES (?,?,?,?,1)", ("Production", "warehouse", 0, 1))
        conn.commit()
        short_capex = svc.dept_ladder(conn, "Production", 300000, "capex")
        short_opex = svc.dept_ladder(conn, "Production", 300000, "opex")
        conn.close()
        assert short_capex == capex, \
            "a department matrix overrode the capital ladder: %s" % short_capex
        # This read `== ["warehouse"]`, encoding the OLD behaviour where a
        # department matrix REPLACED the ladder outright. That was the hole the
        # compliance audit found: one row cut a department to a single signature
        # and skipped the pricing, RFQ and engineering gates with it. The matrix
        # is now a floor a department may add to, never cut below.
        for stage in C.build_ladder(300000, "opex"):
            assert stage in short_opex, \
                "the department matrix dropped %s from the OPEX ladder: %s" % (
                    stage, short_opex)

        print("CAPEX  300k ->", " -> ".join(capex))
        print("OPEX   300k ->", " -> ".join(opex))
        print("PASS: the expenditure flag reaches the ladder end to end")
        return True


if __name__ == "__main__":
    run()
