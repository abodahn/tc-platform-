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
    F  stock LANDS after pricing-> flagged at the Purchasing rung: the rung and
                                   the retained numbers are created there, not
                                   only at pricing time
    G  a second PR still in the -> flagged (the everyday duplicate); the FIRST
       ladder                      request stays clean, so two simultaneous
                                   requests do not each block the other
    H  free-text line naming a  -> flagged: omitting the type-ahead's hidden
       real spare                  spare_id is not a way out of the check
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
        # The requester must be somebody who can actually reach POST
        # /procurement/new. maintenance_technician does not hold proc_create and
        # gets a 403 there, so a file claiming to use the UI's entry points may
        # not raise its requests as one.
        tech = {"username": "store", "role": "storekeeper", "id": 9}
        from app.approvals import constants as C
        assert "proc_create" in C.PROC_ROLE_PERMS.get(tech["role"], []), (
            "%s cannot POST /procurement/new, so nothing below goes through the "
            "entry point the web UI uses" % tech["role"])
        seat = [0]
        # DOAM Table 4 puts spend with no approved budget behind it at L1, and a
        # throwaway database has no budgets at all. Fund the department so this
        # file measures the §3.4 coverage cap and nothing else — the same reason
        # sourcing() is deferred until after each ladder is read.
        svc.set_budget("Maintenance", 10_000_000)

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

        # ── F. the stock lands AFTER pricing ──────────────────────────────────
        # The commonest real trigger, because stock moves every day while a
        # request sits in the ladder. Grading used to run only inside price_pr
        # while the memo gate recomputed from live stock at the Purchasing rung,
        # so the buyer was blocked for a memo, deviation_findings asked for a
        # Plant Director, and NO rung and NO audit row were ever created — the
        # memo half of §4.4 fired and the signature half silently did not.
        f_sid = spare("SP-COV-F", "Bobbin case", stock=0, reorder=0,
                      maxlvl=100000, cost=25.0)
        f = raise_pr("Priced while empty", "Bobbin case", 50, f_sid)
        price(f, 25.0)
        assert not deviation_rungs(f), (
            "priced against an empty shelf: nothing to escalate yet (%s)"
            % deviation_rungs(f))
        f_ladder = stages(f)
        # A QUOTE, not a single-source note: waiving competition appends a
        # factory_manager rung of its own under §4.3, and a rung that would have
        # been there anyway proves nothing about §3.4.
        ok, msg = svc.add_quote(f, {"vendor": "OEM Spares Ltd", "amount": 1250,
                                    "currency": "EGP"}, buyer)
        assert ok, msg
        sign_until(f, "purchasing")
        # 400 units are delivered while the request is mid-ladder.
        conn = get_db()
        conn.execute("UPDATE mnt_spare_parts SET stock_qty=400 WHERE id=?", (f_sid,))
        conn.commit(); conn.close()
        ok, msg = sign_one(f)
        assert not ok and msg == "deviation_memo_required", (
            "the goods arrived and the buyer still signed: %s / %s" % (ok, msg))
        assert deviation_rungs(f) == ["factory_manager"], (
            "the gate demanded the memo but never created the signature §3.4 "
            "exists to add: %s -> %s (deviation rungs %s)"
            % (f_ladder, stages(f), deviation_rungs(f)))
        conn = get_db()
        fev = conn.execute("SELECT detail FROM pr_events WHERE pr_id=? AND "
                           "action='deviation_approval'", (f,)).fetchone()
        conn.close()
        assert fev and "on hand 400" in fev["detail"], (
            "nothing was retained about the netting that blocked the buyer: %s"
            % (fev and fev["detail"]))
        ok, msg = svc.set_doam_document(
            f, "deviation_memo", "Delivery landed late; order kept for the "
            "shutdown buffer.", buyer)
        assert ok, msg
        ok, msg = sign_one(f)
        assert ok, "memo written and the sign-off is still refused: %s" % msg
        assert state(f)[1] == "factory_manager", (
            "the rung added at the Purchasing gate must be the next one to "
            "sign: %s" % (state(f),))

        # ── G. two requests in the ladder, neither of them a PO yet ───────────
        # A pending PR is a planned order. procure_bridge already treats one as
        # an open replenishment and dedups the auto-reorder on it; the coverage
        # check used to count only po_issued/partially_received, so the everyday
        # duplicate sailed through. Only requests raised BEFORE this one net
        # against it, so the two do not each declare the other the duplicate.
        g_sid = spare("SP-COV-G", "Take-up lever", stock=0, reorder=0,
                      maxlvl=100000, cost=25.0)
        g1 = raise_pr("First request", "Take-up lever", 50, g_sid)
        g2 = raise_pr("Second request", "Take-up lever", 50, g_sid)
        price(g1, 25.0)
        price(g2, 25.0)
        assert findings(g1)["lines"][0]["coverage"]["net_need"] == 50, (
            "the FIRST request is the real requirement and must stay clean: %s"
            % findings(g1)["lines"][0]["coverage"])
        assert not deviation_rungs(g1), deviation_rungs(g1)
        g2cov = findings(g2)["lines"][0]["coverage"]
        assert g2cov["on_order"] == 50 and g2cov["net_need"] == 0, (
            "a second request for a part already on order must net to zero: %s"
            % g2cov)
        assert deviation_rungs(g2) == ["factory_manager"], (
            "the commonest duplicate of all bought no extra signature: %s"
            % deviation_rungs(g2))
        sourcing(g1)
        sign_until(g1, "purchasing")
        ok, msg = sign_one(g1)
        assert ok, (
            "the first request was blocked by the duplicate raised after it: %s"
            % msg)

        # ── H. free text instead of the type-ahead ────────────────────────────
        # spare_id is a hidden field the requester's own browser posts. Skipping
        # the type-ahead used to return coverage=None / on_plan / no memo — the
        # free pass costs one keystroke, which is the same objection this
        # control raises against pr_items.current_stock.
        h_sid = spare("SP-COV-H", "Presser foot", stock=400, reorder=20,
                      maxlvl=100000, cost=25.0)
        h = raise_pr("Typed by hand", "Presser foot", 50)     # no spare_id
        h_before = stages(h)
        price(h, 25.0)
        h_dev = findings(h)
        hcov = h_dev["lines"][0]["coverage"]
        assert hcov and hcov["net_need"] == 0 and hcov["by_name"], (
            "omitting the hidden field switched the check off: %s" % hcov)
        assert h_dev["coverage_blind"] == 0, h_dev
        assert deviation_rungs(h) == ["factory_manager"], (
            "a hand-typed line for a part with 400 on the shelf must cost the "
            "same signature as the linked one: %s -> %s" % (h_before, stages(h)))
        assert h_dev["memo"], "the memo is owed on the hand-typed line too"
        # …and an AMBIGUOUS name is still not guessed at. Two active spares share
        # the name, so the line stays unassessed rather than netted at random.
        spare("SP-COV-I1", "Guide bar", stock=400, reorder=0, maxlvl=100000, cost=25.0)
        spare("SP-COV-I2", "Guide bar", stock=400, reorder=0, maxlvl=100000, cost=25.0)
        i = raise_pr("Ambiguous name", "Guide bar", 50)
        price(i, 25.0)
        i_dev = findings(i)
        assert i_dev["lines"][0]["coverage"] is None and i_dev["coverage_blind"] == 1, (
            "two parts answer to that name — netting against one of them is a "
            "guess, not a check: %s" % i_dev["lines"][0])
        assert not deviation_rungs(i), deviation_rungs(i)

        # ── the browser really can post both shapes ───────────────────────────
        _http(app, spare("SP-COV-J", "Cam follower", stock=400, reorder=20,
                         maxlvl=100000, cost=25.0))

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
        print("F stock lands after pricing ->", " -> ".join(f_ladder), "=>",
              " -> ".join(stages(f)), "+ memo refused until written")
        print("G duplicate PR in ladder    -> first clean, second",
              deviation_rungs(g2))
        print("H free text, no spare_id    ->", " -> ".join(stages(h)),
              "(matched by name); ambiguous name stays unassessed")
        print("PASS: §3.4 net-requirement cap fires on the real flow, both ways")
        return True


def _http(app, sid):
    """The claim above is that every request goes through the entry points the
    web UI uses. Prove it rather than assert it: sign in as a real seeded user
    who holds proc_create and POST /procurement/new exactly as the form does —
    unit_price[]=0 (the requester price lockout) and the spare_id[] hidden field
    the type-ahead fills. Then post the SAME line with that hidden field left
    empty, which is all it takes to skip the type-ahead, and confirm the netting
    still sees it."""
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        conn = get_db()
        u = conn.execute("SELECT id, username, session_epoch FROM users "
                         "WHERE role='storekeeper' AND is_active=1 "
                         "ORDER BY id LIMIT 1").fetchone()
        conn.close()
    assert u, "no seeded storekeeper — the role that raises spares requests"

    with app.test_client() as cl:
        with cl.session_transaction() as s:
            s["uid"] = u["id"]
            s["ep"] = u["session_epoch"] or 0
            s["_csrf_token"] = "tok"

        def post(title, with_link):
            r = cl.post("/procurement/new", data={
                "_csrf": "tok", "title": title, "department": "Maintenance",
                "item[]": "Cam follower", "description[]": "", "unit[]": "Pcs",
                "qty[]": "50", "current_stock[]": "0",
                "unit_price[]": "0",                 # the requester lockout
                "spare_id[]": str(sid) if with_link else "",
                "item_id[]": ""}, follow_redirects=False)
            assert r.status_code in (301, 302), (
                "POST /procurement/new was refused (%s) — this user cannot "
                "raise a request at all" % r.status_code)
            with app.app_context():
                from app.db import get_db as _g
                c = _g()
                try:
                    row = c.execute("SELECT id, status FROM pr_requests "
                                    "WHERE title=?", (title,)).fetchone()
                    assert row, "the POST did not create a request"
                    # A request that silently stayed a DRAFT has no ladder at
                    # all, so "no deviation rung" would mean nothing.
                    assert row["status"] == "pending", (
                        "%s was not submitted (status %s)" % (title, row["status"]))
                    return row["id"]
                finally:
                    c.close()

        for title, with_link in (("HTTP linked line", True),
                                 ("HTTP free-text line", False)):
            pr_id = post(title, with_link)
            with app.app_context():
                from app.db import get_db as _g
                c = _g()
                try:
                    li = c.execute("SELECT id, unit_price, spare_id FROM pr_items "
                                   "WHERE pr_id=?", (pr_id,)).fetchone()
                    assert float(li["unit_price"] or 0) == 0, (
                        "the requester priced the line: %s" % li["unit_price"])
                    assert bool(li["spare_id"]) is with_link, (
                        "spare_id came back %r for with_link=%s"
                        % (li["spare_id"], with_link))
                finally:
                    c.close()
                ok, msg = svc.price_pr(pr_id, {li["id"]: 25.0}, {}, {
                    "username": "buyer", "role": "purchasing_manager", "id": 1})
                assert ok, msg
                c = _g()
                try:
                    dev = svc.deviation_findings(c, pr_id)
                    rungs = [r["stage"] for r in c.execute(
                        "SELECT stage FROM pr_steps WHERE pr_id=? AND "
                        "origin='deviation'", (pr_id,)).fetchall()]
                finally:
                    c.close()
            cov = dev["lines"][0]["coverage"]
            assert cov and cov["net_need"] == 0 and dev["memo"], (
                "%s: 400 on the shelf and the netting missed it (%s)"
                % (title, cov))
            assert rungs == ["factory_manager"], (
                "%s bought no extra signature: %s" % (title, rungs))
    print("HTTP  POST /procurement/new both with and without the hidden "
          "spare_id[] -> both netted, both escalated")


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
