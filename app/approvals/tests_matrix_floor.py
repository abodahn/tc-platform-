"""The DOAM ladder is a FLOOR that a department matrix cannot cut below.

Found by the compliance audit, and it was not theoretical: a single row
(department, 'warehouse', threshold 0) collapsed a whole department to ONE
signature — and because it dropped 'purchasing', which is PRICING_GATE_STAGE, it
took the pricing gate, the RFQ gate and the engineering-justification gate with
it. A five-million-pound request would have needed one warehouse signature.

Two defences, both checked here: the write refuses to save a weakened matrix,
and the READ enforces the floor anyway, so a row saved before the rule existed
cannot keep working.
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "floor.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        BIG = 5_000_000.0

        # --- the write path refuses to weaken the ladder ----------------------
        ok, msg = svc.set_dept_matrix("Production", [{"stage": "warehouse", "threshold": 0}],
                                      {"username": "admin"})
        assert not ok, "a one-rung matrix was accepted"
        assert msg.startswith("below_doam_floor"), msg
        assert C.stage_label("purchasing") in msg, \
            "the refusal should name what is missing, by its human label: %s" % msg

        # Raising a threshold above the DOAM's is also a weakening.
        raised = [{"stage": s, "threshold": (v if s != "finance" else 900_000)}
                  for s, v in C.OPEX_MATRIX.items()]
        ok, msg = svc.set_dept_matrix("Production", raised, {"username": "admin"})
        assert not ok and C.stage_label("finance") in msg, msg

        # --- a matrix that only ADDS is accepted ------------------------------
        stronger = [{"stage": s, "threshold": v} for s, v in C.OPEX_MATRIX.items()]
        stronger[stronger.index(next(r for r in stronger if r["stage"] == "finance"))] = \
            {"stage": "finance", "threshold": 50_000}      # Finance signs EARLIER
        ok, msg = svc.set_dept_matrix("Production", stronger, {"username": "admin"})
        assert ok, "a strictly stronger matrix was refused: %s" % msg

        conn = get_db()
        ladder = svc.dept_ladder(conn, "Production", 120_000, "opex")
        conn.close()
        assert "finance" in ladder, \
            "the department asked for Finance at 50k; at 120k it must be there: %s" % ladder

        # --- the READ enforces the floor even against a legacy bad row --------
        conn = get_db()
        conn.execute("DELETE FROM proc_resp_matrix WHERE department=?", ("Production",))
        conn.execute("INSERT INTO proc_resp_matrix (department, stage, threshold, seq, active) "
                     "VALUES (?,?,?,?,1)", ("Production", "warehouse", 0, 1))
        conn.commit()
        ladder = svc.dept_ladder(conn, "Production", BIG, "opex")
        conn.close()

        assert ladder != ["warehouse"], \
            "a legacy row still collapses the ladder to one signature: %s" % ladder
        for stage in C.build_ladder(BIG, "opex"):
            assert stage in ladder, "%s was dropped from a %s request: %s" % (stage, BIG, ladder)
        assert C.PRICING_GATE_STAGE in ladder, \
            "the pricing gate stage is missing, so the pricing/RFQ/engineering gates never run"
        assert ladder.index("warehouse") < ladder.index("purchasing"), \
            "the merged ladder lost its authority order: %s" % ladder

        # --- CAPEX is company-level: no department matrix applies at all ------
        conn = get_db()
        cap = svc.dept_ladder(conn, "Production", BIG, "capex")
        conn.close()
        assert cap == C.build_ladder(BIG, "capex"), cap

        print("refused:", msg)
        print("legacy bad row ->", " -> ".join(ladder))
        print("PASS: the DOAM ladder is a floor a department cannot cut below")
        return True


if __name__ == "__main__":
    run()
