# -*- coding: utf-8 -*-
"""DOAM §3.4 — the requirement cap (T&C-PUF-10 "Coverage Check").

    "Procurement quantity is capped at the net requirement after inventory
     netting. A quantity above plan ... requires a justification memo and a
     higher approval per Section 4.4."

Everything here goes through the entry points the web UI actually uses — a
request created with the requester price lockout (total = 0), priced at the
pricing gate, then signed rung by rung — because a control that only fires when
a test injects a state the UI cannot produce is not a control.

Both directions are proved, because a check that always blocks is as broken as
one that never does:
    A  fully covered by stock   -> flagged: the Purchasing sign-off is REFUSED
                                   until the memo exists, and a Plant Director
                                   rung is added on top of the value ladder
    B  genuine shortfall        -> passes clean, no memo, no extra signature
    C  covered by an open PO    -> flagged (the duplicate order still in transit)
    D  no stock record          -> unassessable: reported, adds nothing, assumes
                                   nothing in either direction
    E  at the reorder level     -> passes clean (the auto-reorder lane keeps
                                   moving instead of being escalated 100% of
                                   the time, which is the same as broken)
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "coverage.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        tech = {"username": "tech", "id": 9}
        seat = [0]

        def stages(pr_id):
            conn = get_db()
            try:
                return [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq",
                    (pr_id,)).fetchall()]
            finally:
                conn.close()

        def findings(pr_id):
            conn = get_db()
            try:
                return svc.deviation_findings(conn, pr_id)
            finally:
                conn.close()

        def state(pr_id):
            conn = get_db()
            try:
                r = conn.execute("SELECT status, current_seq FROM pr_requests WHERE id=?",
                                 (pr_id,)).fetchone()
                st = conn.execute("SELECT stage FROM pr_steps WHERE pr_id=? AND seq=?",
                                  (pr_id, r["current_seq"])).fetchone()
                return r["status"], (st["stage"] if st else None)
            finally:
                conn.close()

        def spare(code, name, stock, reorder, maxlvl, cost, reserved=0):
            conn = get_db()
            conn.execute(
                "INSERT INTO mnt_spare_parts (code, name, stock_qty, reserved_qty, "
                "reorder_level, max_level, avg_cost, is_active) VALUES (?,?,?,?,?,?,?,1)",
                (code, name, stock, reserved, reorder, maxlvl, cost))
            sid = conn.execute("SELECT id FROM mnt_spare_parts WHERE code=?",
                               (code,)).fetchone()["id"]
            conn.commit(); conn.close()
            return sid

        def raise_pr(title, item, qty, sid=None):
            """Exactly what the UI posts: quantities, no prices anywhere."""
            line = {"item": item, "qty": qty, "unit_price": 0}
            if sid:
                line["spare_id"] = sid
            return svc.create_pr({"title": title, "department": "Maintenance"},
                                 [line], tech, priced=False)[0]

        def price(pr_id, unit):
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pr_id,)).fetchone()["id"]
            conn.close()
            ok, msg = svc.price_pr(pr_id, {li: unit}, {}, buyer)
            assert ok, "pricing failed: %s" % msg

        def sourcing(pr_id):
            """§4.3 sourcing band: EVERY priced request needs a quote or a
            single-source note before Purchasing may sign. Pre-existing gate,
            satisfied AFTER the ladder is measured because it appends a rung of
            its own — otherwise it would be mistaken for §3.4's."""
            ok, msg = svc.set_single_source(pr_id, "Sole OEM supplier for this part.",
                                            buyer)
            assert ok, msg

        def deviation_rungs(pr_id):
            """The rungs apply_deviation_stages() put there, by origin — not by
            name. A stage the VALUE ladder or the single-source rule would have
            collected anyway proves nothing about §3.4."""
            conn = get_db()
            try:
                return [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? AND origin='deviation' "
                    "ORDER BY seq", (pr_id,)).fetchall()]
            finally:
                conn.close()

        def sign_one(pr_id):
            """One rung, signed by a fresh authorised person (no self-approval,
            no dual role). Returns (ok, msg) exactly as the web POST would."""
            seat[0] += 1
            return svc.act_on_step(
                pr_id, {"username": "boss%d" % seat[0], "role": "super_admin",
                        "id": 100 + seat[0]}, "approve")

        def sign_until(pr_id, stage):
            """Sign rungs until `stage` is the live one. Returns the stage list."""
            for _ in range(10):
                status, cur = state(pr_id)
                if cur == stage or status != "pending":
                    return status, cur
                ok, msg = sign_one(pr_id)
                assert ok, "could not reach %s: refused at %s (%s)" % (stage, cur, msg)
            raise AssertionError("ladder never reached %s" % stage)

        # ── A. fully covered by stock ─────────────────────────────────────────
        # 400 on the shelf, reorder buffer 20, nothing reserved, nothing on
        # order. Somebody asks for 50 more: the net requirement is ZERO.
        a_sid = spare("SP-COV-A", "Needle plate", stock=400, reorder=20,
                      maxlvl=100000, cost=25.0)
        a = raise_pr("Covered by stock", "Needle plate", 50, a_sid)
        a_before = stages(a)
        price(a, 25.0)                     # AT target: price is not the trigger
        a_dev, a_after = findings(a), stages(a)
        cov = a_dev["lines"][0]["coverage"]
        assert cov["net_need"] == 0 and cov["excess"] == 50, cov
        assert a_dev["lines"][0]["grade"] == "over_plan", a_dev["lines"][0]
        assert deviation_rungs(a) == ["factory_manager"], (
            "50 units against a net requirement of 0 must cost the higher "
            "signature §3.4 points to: %s -> %s (deviation rungs %s)"
            % (a_before, a_after, deviation_rungs(a)))
        assert "factory_manager" not in a_before, a_before
        assert a_dev["memo"], "§3.4 demands the justification memo"

        # T&C-PUF-10 is retained for a year; the panel recomputes from TODAY's
        # stock, so the netting that triggered the escalation is written into
        # the audit trail where it stays with the request.
        conn = get_db()
        ev = conn.execute("SELECT detail FROM pr_events WHERE pr_id=? AND "
                          "action='deviation_approval'", (a,)).fetchone()
        conn.close()
        assert ev and "on hand 400" in ev["detail"] and "net need 0" in ev["detail"], (
            "the coverage numbers were not retained: %s" % (ev and ev["detail"]))

        # …and the memo is not decoration: Purchasing cannot sign without it.
        sourcing(a)
        sign_until(a, "purchasing")
        ok, msg = sign_one(a)
        assert not ok and msg == "deviation_memo_required", (
            "the buyer signed off an order for goods already on the shelf: "
            "%s / %s" % (ok, msg))
        ok, msg = svc.set_doam_document(
            a, "deviation_memo",
            "Bulk buy agreed with the supplier ahead of the shutdown.", buyer)
        assert ok, msg
        ok, msg = sign_one(a)
        assert ok, "the memo was written and the sign-off is still refused: %s" % msg
        assert state(a)[1] == "factory_manager", (
            "after Purchasing the request must sit on the Plant Director rung "
            "the deviation added: %s" % (state(a),))

        # ── B. a genuine shortfall passes clean ───────────────────────────────
        # Nothing on the shelf above the buffer, nothing on order: the whole
        # quantity is a real requirement and must cost nothing extra.
        b_sid = spare("SP-COV-B", "Rotary hook", stock=5, reorder=20,
                      maxlvl=100000, cost=25.0)
        b = raise_pr("Real shortfall", "Rotary hook", 50, b_sid)
        b_before = stages(b)
        price(b, 25.0)
        b_dev, b_after = findings(b), stages(b)
        bcov = b_dev["lines"][0]["coverage"]
        assert bcov["net_need"] == 50 and bcov["excess"] == 0, bcov
        assert b_dev["lines"][0]["grade"] == "on_plan", b_dev["lines"][0]
        assert b_after == b_before and not deviation_rungs(b), (
            "a genuine shortfall must not buy a single extra signature: "
            "%s -> %s" % (b_before, b_after))
        assert not b_dev["memo"], "no deviation, no memo"
        sourcing(b)
        sign_until(b, "purchasing")
        ok, msg = sign_one(b)
        assert ok, "a real shortfall was blocked by the coverage cap: %s" % msg

        # ── C. covered by a PO already in transit ─────────────────────────────
        c_sid = spare("SP-COV-C", "Feed dog", stock=0, reorder=0,
                      maxlvl=100000, cost=25.0)
        first = raise_pr("Original order", "Feed dog", 60, c_sid)
        price(first, 25.0)
        assert not deviation_rungs(first), (
            "the FIRST order is a real shortfall and must be clean: %s"
            % deviation_rungs(first))
        sourcing(first)
        for _ in range(10):                       # sign it all the way through
            if state(first)[0] != "pending":
                break
            ok, msg = sign_one(first)
            assert ok, "the first order stalled at %s (%s)" % (state(first)[1], msg)
        ok, msg = svc.issue_po(first, buyer)      # the real PO button
        assert ok, msg
        assert state(first)[0] == "po_issued", state(first)

        c = raise_pr("Duplicate order", "Feed dog", 60, c_sid)
        c_before = stages(c)
        price(c, 25.0)
        c_dev, c_after = findings(c), stages(c)
        ccov = c_dev["lines"][0]["coverage"]
        assert ccov["on_order"] == 60 and ccov["net_need"] == 0, ccov
        assert c_dev["lines"][0]["grade"] == "over_plan", c_dev["lines"][0]
        assert deviation_rungs(c) == ["factory_manager"], (
            "a second order for goods already in transit must escalate: "
            "%s -> %s (deviation rungs %s)" % (c_before, c_after, deviation_rungs(c)))

        # only what has NOT arrived may be netted: 45 of the 60 land
        conn = get_db()
        fid = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                           (first,)).fetchone()["id"]
        conn.close()
        ok, msg = svc.receive_items(first, {fid: 45}, buyer, notes="partial")
        assert ok, msg
        assert state(first)[0] == "partially_received", state(first)
        ccov2 = findings(c)["lines"][0]["coverage"]
        assert ccov2["on_order"] == 15, (
            "only what has NOT arrived is still on order: %s" % ccov2)
        assert ccov2["on_hand"] == 45, (
            "the 45 that landed are on the shelf, counted once and once only: "
            "%s" % ccov2)

        # ── D. no stock record: unassessable, never guessed ───────────────────
        d = raise_pr("One-off bracket", "Custom bracket", 12)   # free text, no spare
        d_before = stages(d)
        price(d, 300.0)
        d_dev, d_after = findings(d), stages(d)
        assert d_dev["lines"][0]["coverage"] is None, d_dev["lines"][0]
        assert d_dev["coverage_blind"] == 1, d_dev
        assert d_dev["lines"][0]["grade"] == "on_plan", (
            "an unassessable line must not be graded as a deviation: %s"
            % d_dev["lines"][0])
        assert d_after == d_before and not deviation_rungs(d), (
            "a line that could not be netted must add NO signatures: %s -> %s"
            % (d_before, d_after))
        assert not d_dev["memo"], "no memo may be demanded on a guess"
        sourcing(d)
        sign_until(d, "purchasing")
        ok, msg = sign_one(d)
        assert ok, "an unassessable line blocked the sign-off: %s" % msg

        # ── E. the maintenance auto-reorder lane still moves ──────────────────
        # available (30) is AT the reorder level, so nothing is free to net
        # against: refilling to the max level is on plan, not above it.
        e_sid = spare("SP-COV-E", "Looper", stock=30, reorder=30, maxlvl=100,
                      cost=25.0)
        e = raise_pr("Auto reorder — Looper", "Looper", 70, e_sid)
        e_before = stages(e)
        price(e, 25.0)
        e_dev, e_after = findings(e), stages(e)
        assert e_dev["lines"][0]["coverage"]["net_need"] == 70, \
            e_dev["lines"][0]["coverage"]
        assert e_dev["lines"][0]["grade"] == "on_plan", e_dev["lines"][0]
        assert e_after == e_before and not deviation_rungs(e), (
            "replenishment at the reorder point is exactly what the stocking "
            "policy asks for and must not be escalated: %s -> %s"
            % (e_before, e_after))

        # ── the numbers have to be readable on the request itself ─────────────
        _render(app, a, d)

        print("A covered  50 / need 0  ->", " -> ".join(a_before), "=>",
              " -> ".join(a_after), "+ memo refused until written")
        print("B short    50 / need 50 ->", " -> ".join(b_after), "(unchanged, signed)")
        print("C transit  60 / need 0  ->", " -> ".join(c_before), "=>",
              " -> ".join(c_after))
        print("D no stock record       ->", " -> ".join(d_after),
              "(unassessable, unchanged, signed)")
        print("E at reorder point      ->", " -> ".join(e_after), "(unchanged)")
        print("PASS: §3.4 net-requirement cap fires on the real flow, both ways")
        return True


def _render(app, flagged_pr, blind_pr):
    """The coverage numbers have to be readable on the request page, and every
    string on it has to exist in en/ar/tr or it renders as the raw key."""
    import io
    import json
    import os
    import re

    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    langs = [set(json.load(io.open(os.path.join(repo, "app", "static", "i18n",
                                                l + ".json"), encoding="utf-8")))
             for l in ("en", "ar", "tr")]
    common = set.intersection(*langs)

    with app.app_context():
        from app.db import get_db
        conn = get_db()
        u = conn.execute("SELECT id, session_epoch FROM users WHERE role='super_admin' "
                         "AND is_active=1 ORDER BY id LIMIT 1").fetchone()
        conn.close()
    assert u, "no super_admin to render as"

    with app.test_client() as cl:
        with cl.session_transaction() as sess:
            sess["uid"] = u["id"]
            sess["ep"] = u["session_epoch"] or 0

        def page(pr_id):
            r = cl.get("/procurement/pr/%d" % pr_id)
            assert r.status_code == 200, (pr_id, r.status_code)
            html = r.get_data(as_text=True)
            used = {k for k in re.findall(r'data-i18n(?:-ph)?="([^"]+)"', html)
                    if k.startswith(("proc.cov_", "proc.dev_"))}
            missing = sorted(k for k in used if k not in common)
            assert not missing, "i18n keys missing from en/ar/tr: %s" % missing
            return html

        html = page(flagged_pr)
        for key in ("proc.cov_title", "proc.cov_req", "proc.cov_hand",
                    "proc.cov_order", "proc.cov_need", "proc.cov_excess",
                    "proc.dev_over_plan"):
            assert key in html, "%s never reached the request page" % key
        assert "proc.memo_ok" in html or "proc.memo_req" in html, "no memo control"

        html = page(blind_pr)
        assert "proc.cov_blind" in html, (
            "a line that could not be netted is invisible on the page, which "
            "reads as 'checked and fine'")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    run()
