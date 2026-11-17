"""Repricing preserves demand approvals and recollects commercial signatures.

    python app/approvals/tests_reprice_withdraws.py
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="reprice_"), "w.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, constants as C

        conn = get_db()
        signer = {}
        for stage, roles in C.STAGE_ROLES.items():
            role = sorted(roles)[0]
            u = "rp_" + stage
            conn.execute(
                "INSERT INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (u, "x", u, role))
            signer[stage] = {"username": u, "role": role}
        conn.commit()
        for st in signer:
            signer[st]["id"] = conn.execute(
                "SELECT id FROM users WHERE username=?",
                (signer[st]["username"],)).fetchone()["id"]
        conn.close()

        svc.set_budget("IT", 2000000)
        reqr = {"username": "rp_req", "id": 77}
        pid, _ = svc.create_pr(
            {"title": "Reprice signatures", "department": "IT", "currency": "EGP"},
            [{"item": "Office equipment", "unit": "Pcs", "qty": 1, "unit_price": 0}],
            reqr, submit=False, priced=False)
        ok, msg = svc.submit_pr(pid, reqr)
        assert ok, msg
        conn = get_db()
        line = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pid,)).fetchone()["id"]
        conn.close()
        meta = {"vendor": "Vendor A", "currency": "EGP", "tax_rate": 0,
                "payment_condition": "Net 30"}
        ok, msg = svc.price_pr(pid, {line: 600000}, meta, signer["purchasing"])
        assert ok, msg
        for vendor in ("Vendor A", "Vendor B", "Vendor C"):
            ok, msg = svc.add_quote(pid, {"vendor": vendor, "amount": 600000},
                                    signer["purchasing"])
            assert ok, msg

        def look():
            conn = get_db()
            try:
                pr = dict(conn.execute("SELECT * FROM pr_requests WHERE id=?", (pid,)).fetchone())
                steps = [dict(r) for r in conn.execute(
                    "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq", (pid,)).fetchall()]
                return pr, steps
            finally:
                conn.close()

        _, steps = look()
        pivot = min(s["seq"] for s in steps if s["stage"] == C.PRICING_GATE_STAGE)
        first_above = min(s["seq"] for s in steps if s["seq"] > pivot)
        for _ in range(20):
            pr, steps = look()
            assert pr["status"] == "pending", "Need an unsigned rung above the signed ones"
            if pr["current_seq"] > first_above:
                break
            step = next(s for s in steps if s["seq"] == pr["current_seq"]
                        and s["status"] == "pending")
            ok, msg = svc.act_on_step(pid, signer[step["stage"]], "approve")
            assert ok, msg
        else:
            assert False, "Approval ladder did not advance"

        original_pr, original = look()
        ok, msg = svc.price_pr(pid, {line: 600000}, meta, signer["purchasing"])
        assert ok, msg
        pr, steps = look()
        assert [(s["id"], s["status"]) for s in steps] == [
            (s["id"], s["status"]) for s in original]
        assert pr["current_seq"] == original_pr["current_seq"]

        reset = [s for s in original if s["seq"] > pivot and s["status"] == "approved"]
        assert reset
        conn = get_db()
        for step in reset:
            conn.execute("UPDATE pr_steps SET escalated=1, escalated_at=?, escalated2_at=? WHERE id=?",
                         ("2026-01-01", "2026-01-01", step["id"]))
        reopened = min(reset, key=lambda s: s["seq"])
        # A second eligible signer must hear about the reopened rung; the original
        # signer gets the re-approval notice instead.
        conn.execute("INSERT INTO users (username, password_hash, full_name, role, is_active) VALUES (?,?,?,?,1)",
                     ("rp_alternate", "x", "Alternate", signer[reopened["stage"]]["role"]))
        before_notice = conn.execute("SELECT COALESCE(MAX(id),0) AS id FROM notifications").fetchone()["id"]
        conn.commit()
        conn.close()
        ok, msg = svc.price_pr(pid, {line: 610000}, meta, signer["purchasing"])
        assert ok, msg
        pr, steps = look()
        by_id = {s["id"]: s for s in steps}
        for step in original:
            if step["seq"] <= pivot:
                assert by_id[step["id"]]["status"] == "approved"
        for step in reset:
            assert by_id[step["id"]]["status"] == "pending"
            assert by_id[step["id"]]["approver_user"] is None
            assert by_id[step["id"]]["sig_png"] is None
            assert by_id[step["id"]]["escalated"] == 0
            assert by_id[step["id"]]["escalated_at"] is None
            assert by_id[step["id"]]["escalated2_at"] is None
        assert pr["current_seq"] == min(s["seq"] for s in reset)
        conn = get_db()
        assert conn.execute("SELECT id FROM notifications WHERE id>? AND target_user=? AND title=?",
                            (before_notice, "rp_alternate", "New request to sign")).fetchone()
        assert not conn.execute("SELECT id FROM notifications WHERE id>? AND target_user=? AND title=?",
                                (before_notice, reopened["approver_user"], "New request to sign")).fetchone()
        assert conn.execute("SELECT id FROM pr_events WHERE pr_id=? AND "
                            "action='signatures_recollected'", (pid,)).fetchone()
        conn.close()
    print("OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
