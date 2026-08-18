"""Task 7 — retention as a real date, and the R9 constants that executed nothing.

Every check runs through the REAL entry points the web UI uses:

    svc.create_pr({...}, [{... "unit_price": 0}], user, priced=False)
    svc.price_pr(pr_id, {line_id: unit_price}, {"tax_rate": t}, buyer)

That matters more than anything else here. The requester price lockout means a
UI-created request is always submitted with total = 0 and priced later at the
pricing gate, so a control proved by handing the service layer a price is a
control that cannot fire in production. Nothing below injects a price.
"""
import os
import tempfile
from datetime import datetime, timedelta


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "retention.db")
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
        from app.approvals import schema as S

        tech = {"username": "tech", "id": 9}
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        pm = buyer

        def unpriced(title, dept, item, qty=1, submit=True, **hdr):
            h = {"title": title, "department": dept}
            h.update(hdr)
            return svc.create_pr(h, [{"item": item, "qty": qty, "unit_price": 0}],
                                 tech, priced=False, submit=submit)

        def price(pr_id, unit, tax=0):
            conn = get_db()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                              (pr_id,)).fetchone()["id"]
            conn.close()
            ok, msg = svc.price_pr(pr_id, {li: unit}, {"tax_rate": tax}, buyer)
            assert ok, "pricing failed: %s" % msg

        bid = [0]

        def quotes(pr_id, n=3, amount=1000.0):
            """The competitive quotes the RFQ gate wants, so the checks below fail
            on the rule they are testing and not on that one. Every quote is from
            a DISTINCT vendor — the gate counts suppliers, not rows."""
            conn = get_db()
            for _ in range(n):
                bid[0] += 1
                conn.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, "
                             "currency, created_at) VALUES (?,?,?,?,datetime('now'))",
                             (pr_id, "Vendor %d" % bid[0], amount, "EGP"))
            conn.commit()
            conn.close()

        seat = [0]

        def walk_to(pr_id, stage):
            """Sign rungs until `stage` is the live one — a DIFFERENT signer every
            time (the seat counter never repeats), so neither the self-approval
            nor the dual-role rule is what stops us. Returns the user who should
            sign `stage`."""
            for _ in range(20):
                seat[0] += 1
                i = seat[0]
                conn = get_db()
                cur = [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? AND seq=? AND "
                    "status='pending'",
                    (pr_id, row(pr_id)["current_seq"])).fetchall()]
                conn.close()
                who = {"username": "signer%d" % i, "role": "super_admin", "id": 100 + i}
                if stage in cur:
                    return who
                assert cur, "ran out of ladder before reaching %s" % stage
                ok, msg = svc.act_on_step(pr_id, who, "approve")
                assert ok, "could not walk past %s: %s" % (cur, msg)
            raise AssertionError("never reached %s" % stage)

        def next_signer():
            """A fresh eligible signer. Used where walk_to cannot be: signing is
            the thing that must be refused."""
            seat[0] += 1
            return {"username": "signer%d" % seat[0], "role": "super_admin",
                    "id": 100 + seat[0]}

        def row(pr_id):
            conn = get_db()
            try:
                return conn.execute("SELECT * FROM pr_requests WHERE id=?",
                                    (pr_id,)).fetchone()
            finally:
                conn.close()

        # =================================================================
        # 1. Retention is a DATE ON THE RECORD, stamped at creation
        # =================================================================
        o, _ = unpriced("Opex belts", "Maintenance", "V-belt A42")
        c, _ = unpriced("New line", "Production", "Sewing unit",
                        expenditure_kind="capex")
        ro, rc = row(o), row(c)
        assert ro["retention_until"], "an OPEX request carries no retention date"
        assert rc["retention_until"], "a CAPEX request carries no retention date"
        born = ro["created_at"][:10]
        assert ro["retention_until"] == C.retention_until(born, "opex"), ro["retention_until"]
        assert rc["retention_until"] == C.retention_until(born, "capex"), rc["retention_until"]
        assert int(rc["retention_until"][:4]) - int(ro["retention_until"][:4]) == 5, (
            "CAPEX must be kept 10 years against OPEX's 5: %s vs %s"
            % (rc["retention_until"], ro["retention_until"]))
        print("retention stamped   opex -> %s   capex -> %s"
              % (ro["retention_until"], rc["retention_until"]))

        # the date survives the pricing gate, which rewrites the row
        price(o, 500)
        assert row(o)["retention_until"] == ro["retention_until"], "pricing lost the date"

        # exposed on the record
        st = svc.retention_state(row(c))
        assert st["years"] == 10 and st["expired"] is False, st

        # an edit that turns OPEX into CAPEX must EXTEND retention, never shorten
        # a DRAFT the requester goes back and edits — the only state update_pr
        # accepts, and exactly how a request gets reclassified in the UI
        d, _ = unpriced("Reclassified", "IT", "Server", submit=False)
        five = row(d)["retention_until"]
        ok, msg = svc.update_pr(d, {"title": "Reclassified", "department": "IT",
                                    "expenditure_kind": "capex"},
                                [{"item": "Server", "qty": 1, "unit_price": 0}],
                                tech, can_price=False)
        assert ok, msg
        ten = row(d)["retention_until"]
        assert ten > five, "OPEX -> CAPEX did not extend retention: %s -> %s" % (five, ten)
        ok, msg = svc.update_pr(d, {"title": "Reclassified", "department": "IT",
                                    "expenditure_kind": "opex"},
                                [{"item": "Server", "qty": 1, "unit_price": 0}],
                                tech, can_price=False)
        assert ok, msg
        assert row(d)["retention_until"] == ten, (
            "CAPEX -> OPEX brought the disposal date FORWARD: %s"
            % row(d)["retention_until"])
        print("reclassified opex->capex->opex ->", five, "->", ten, "(never shortened)")

        # =================================================================
        # 2. Deletion is GUARDED, and only a human past the date may act
        # =================================================================
        ok, msg = svc.dispose_pr(o, {"username": "admin"})
        assert not ok and msg.startswith("retained_until:"), (
            "a record inside its retention window was disposed of: %s / %s" % (ok, msg))
        assert row(o)["is_active"] == 1, "the row left the register anyway"

        # the same record, past its date -> a human may now retire it
        conn = get_db()
        conn.execute("UPDATE pr_requests SET retention_until='2000-01-01' WHERE id=?", (o,))
        conn.commit()
        conn.close()
        ok, msg = svc.dispose_pr(o, {"username": "admin"})
        assert ok, "an expired record could not be retired: %s" % msg
        assert row(o)["is_active"] == 0, "dispose_pr did not retire the record"
        assert row(o)["pr_no"], "dispose_pr DESTROYED the row - it must only retire it"
        ok, msg = svc.dispose_pr(o, {"username": "admin"})
        assert not ok and msg == "already_disposed", (ok, msg)
        print("guard: inside window refused, past window retired (row kept on disk)")

        # a record with no stamped date is KEPT, not disposed of (fails closed)
        conn = get_db()
        conn.execute("UPDATE pr_requests SET retention_until=NULL WHERE id=?", (c,))
        conn.commit()
        conn.close()
        ok, msg = svc.dispose_pr(c, {"username": "admin"})
        assert not ok and msg.startswith("retained_until:"), (
            "an unstamped record was treated as disposable: %s / %s" % (ok, msg))
        # ...and the backfill stamps it rather than leaving it unguardable
        conn = get_db()
        n = S.backfill_retention(conn)
        conn.close()
        assert n >= 1 and row(c)["retention_until"], "backfill left a NULL retention date"
        print("backfill stamped %d legacy row(s)" % n)

        # the REPORT of what is eligible exists; there is no purge job
        from app.services import reporting as R
        # `import app.approvals.reports` would rebind the name `app` in this
        # scope to the PACKAGE and shadow the Flask app — from/import does not.
        from app.approvals import reports as _reports    # noqa: F401
        assert R.get("proc_retention"), "no retention report is registered"

        # =================================================================
        # 3. R9 - BUSINESS_CASE_OVER now blocks a signature
        # =================================================================
        big, _ = unpriced("Boiler house", "Production", "Boiler")
        price(big, 12_000_000)                 # > 10,000,000, via the REAL gate
        conn = get_db()
        lb = _stages(conn, big)
        conn.close()
        assert "bod" in lb, "a 12M request must reach the Board: %s" % lb
        quotes(big, 3, 12_000_000)
        pm = walk_to(big, "purchasing")
        ok, msg = svc.act_on_step(big, pm, "approve")
        assert not ok and msg == "business_case_required", (
            "12,000,000 was signed off with no business case: %s / %s" % (ok, msg))
        ok, msg = svc.set_doam_document(big, "business_case", "no", pm)
        assert not ok and msg == "too_short", "a two-letter business case was accepted"
        ok, msg = svc.set_doam_document(
            big, "business_case",
            "Replaces the 1998 boiler; payback 3.1 years on fuel alone, and the "
            "current unit fails its pressure certificate in March.", pm)
        assert ok, msg
        ok, msg = svc.act_on_step(big, pm, "approve")
        assert ok, "the business case did not unblock the signature: %s" % msg
        # ...and the Board rung is the SECOND place it is enforced: strip the
        # business case (as a re-price upward effectively does) and the Board
        # signature is refused too.
        conn = get_db()
        conn.execute("UPDATE pr_requests SET business_case=NULL WHERE id=?", (big,))
        conn.commit()
        conn.close()
        bod = walk_to(big, "bod")
        ok, msg = svc.act_on_step(big, bod, "approve")
        assert not ok and msg == "business_case_required", (
            "the Board signed a 12M request with no business case: %s / %s"
            % (ok, msg))
        print("business case: 12,000,000 blocked, then signed once recorded")

        # the tier is judged on the COMMITTED (tax-inclusive) EGP figure
        vat, _ = unpriced("Just under", "Production", "Press")
        price(vat, 9_900_000, tax=14)          # net 9.9M -> committed 11,286,000
        quotes(vat, 3, 9_900_000)
        ok, msg = svc.act_on_step(vat, walk_to(vat, "purchasing"), "approve")
        assert not ok and msg == "business_case_required", (
            "9.9M + 14%% VAT commits 11,286,000 and must need a business case: "
            "%s / %s" % (ok, msg))
        print("business case: 9,900,000 + 14% VAT (11,286,000 committed) also blocked")

        # an ordinary request is untouched by the new gate
        small, _ = unpriced("Filters", "Quality", "Air filter")
        price(small, 8000)
        quotes(small, 1, 8000)
        ok, msg = svc.act_on_step(small, walk_to(small, "purchasing"), "approve")
        assert ok, "an ordinary 8,000 request was blocked by a 10M rule: %s" % msg

        # =================================================================
        # 4. R9 - deviation_grade()['memo'] and ['quotes'] now execute
        # =================================================================
        conn = get_db()
        conn.execute("INSERT INTO proc_items (code, name, cost_price, has_cost, active) "
                     "VALUES (?,?,?,1,1)", ("IT-TARGET", "Target part", 100.0))
        iid = conn.execute("SELECT id FROM proc_items WHERE code=?",
                           ("IT-TARGET",)).fetchone()["id"]
        conn.commit()
        conn.close()

        over, _ = svc.create_pr({"title": "Over target", "department": "Quality"},
                                [{"item": "Target part", "qty": 10, "unit_price": 0,
                                  "item_id": iid}], tech, priced=False)
        price(over, 140.0)                     # 40% over the 100.00 ERP target
        conn = get_db()
        dev = svc.deviation_findings(conn, over)
        conn.close()
        assert dev["memo"] and dev["quotes"], dev
        pm = walk_to(over, "purchasing")

        # (a) the 'quotes' flag. This order is worth 1,400 EGP, which sits in the
        # bottom sourcing band — ONE quotation. Give it exactly that, and the
        # value rule is satisfied. It is still refused, because DOAM 4.4 puts an
        # off-plan PRICE on three quotes whatever the order is worth, and that
        # flag had no reader at all until now.
        quotes(over, 1, 1400.0)
        assert C.quotes_required(1400.0) == 1, "the value band wants one quote here"
        ok, msg = svc.act_on_step(over, pm, "approve")
        assert not ok and msg == "needs_quotes", (
            "an off-plan price must require 3 quotes at ANY value - one quote "
            "satisfied the 1,400 EGP band and nothing else looked: %s / %s"
            % (ok, msg))
        quotes(over, 2, 1400.0)                # now three distinct vendors

        # (b) the 'memo' flag
        ok, msg = svc.act_on_step(over, pm, "approve")
        assert not ok and msg == "deviation_memo_required", (
            "an order priced 40%% over target was signed with no justification "
            "memo - the 'memo' flag had no reader: %s / %s" % (ok, msg))
        ok, msg = svc.set_doam_document(
            over, "deviation_memo",
            "Sole remaining stockist after the March fire; 40% over the ERP cost "
            "reflects the current market, and the line stops without it.", pm)
        assert ok, msg
        ok, msg = svc.act_on_step(over, pm, "approve")
        assert ok, "three quotes + a memo should clear the 4.4 gate: %s" % msg
        print("deviation: 3 quotes demanded at 1,400 EGP, then a memo, then signed")

        # an ON-PLAN order of the same value is dragged in by neither rule
        onplan, _ = svc.create_pr({"title": "On target", "department": "Quality"},
                                  [{"item": "Target part", "qty": 10,
                                    "unit_price": 0, "item_id": iid}],
                                  tech, priced=False)
        price(onplan, 100.0)
        quotes(onplan, 1, 1000)
        ok, msg = svc.act_on_step(onplan, walk_to(onplan, "purchasing"), "approve")
        assert ok, "an on-plan order was blocked by the deviation controls: %s" % msg

        # =================================================================
        # 5. R9 - EMERGENCY_GRACE_HOURS is applied, not merely written down
        # =================================================================
        from app.maintenance import eng_justification as E
        conn = get_db()
        eid, _ = E.create(conn, {"machine_id": None, "request_type": "breakdown",
                                 "description": "Motor seized",
                                 "root_cause": "Bearing",
                                 "criticality": "production_critical",
                                 "downtime_risk": "Line down", "stock_on_hand": 0,
                                 "stock_checked_with": "store",
                                 "alternatives": "none", "is_emergency": True,
                                 "emergency_due_at": "2099-01-01 00:00:00"}, tech)
        conn.commit()
        er = conn.execute("SELECT created_at, emergency_due_at FROM "
                          "mnt_eng_justifications WHERE id=?", (eid,)).fetchone()
        conn.close()
        cap = E.emergency_deadline(er["created_at"])
        assert er["emergency_due_at"] == cap, (
            "an author gave themselves until 2099; the DOAM allows %d hours: %s"
            % (E.EMERGENCY_GRACE_HOURS, er["emergency_due_at"]))
        assert E.emergency_deadline(er["created_at"], "2020-01-01 00:00:00") \
            == "2020-01-01 00:00:00", "a TIGHTER self-imposed deadline must be kept"
        print("emergency grace: 2099 capped to", cap)

        # =================================================================
        # 6. R9 - the two deletions
        # =================================================================
        assert not hasattr(C, "COST_OBJECTS"), "COST_OBJECTS is back"
        assert all(set(b) == {"over", "quotes"} for b in C.SOURCING_BANDS), C.SOURCING_BANDS

        # =================================================================
        # 8. The repair round -- the four defects the verifier found
        # =================================================================
        # ---- 8a. re-pricing must not walk past the 4.4 gate ------------------
        # The memo and the raised quote requirement were checked ONLY on the
        # Purchasing rung, but price_pr stays open while a request circulates:
        # price on plan, collect Purchasing's signature, then re-price 40% over
        # and the order reached 'approved' carrying neither document.
        rp, _ = svc.create_pr({"title": "Re-priced later", "department": "Quality"},
                              [{"item": "Target part", "qty": 10, "unit_price": 0,
                                "item_id": iid}], tech, priced=False)
        price(rp, 100.0)                       # exactly on the 100.00 ERP target
        quotes(rp, 1, 1000.0)                  # the 1,000 EGP band wants one
        pur = walk_to(rp, "purchasing")
        ok, msg = svc.act_on_step(rp, pur, "approve")
        assert ok, "an on-plan 1,000 EGP order should sign at purchasing: %s" % msg
        price(rp, 140.0)                       # ...now 40% over, mid-ladder
        conn = get_db()
        dev_rp = svc.deviation_findings(conn, rp)
        after_pur = _stages(conn, rp)
        conn.close()
        assert dev_rp["memo"] and dev_rp["quotes"], dev_rp
        above = [st for st in after_pur if st not in ("warehouse", "purchasing")]
        assert above, "re-pricing added no rung above Purchasing: %s" % after_pur
        nxt = walk_to(rp, above[0])
        ok, msg = svc.act_on_step(rp, nxt, "approve")
        assert not ok and msg == "needs_quotes", (
            "a re-price to 40%% over target after Purchasing signed must still "
            "demand three quotes: %s / %s" % (ok, msg))
        quotes(rp, 2, 1400.0)
        ok, msg = svc.act_on_step(rp, nxt, "approve")
        assert not ok and msg == "deviation_memo_required", (
            "the order reached the next rung with no justification memo -- the "
            "4.4 gate only ever ran on the Purchasing rung: %s / %s" % (ok, msg))
        ok, msg = svc.set_doam_document(
            rp, "deviation_memo",
            "Supplier re-quoted after the tariff change; 40% over the ERP cost "
            "is the market and the line stops without the part.", pur)
        assert ok, msg
        ok, msg = svc.act_on_step(rp, nxt, "approve")
        assert ok, "the memo did not unblock the rung above Purchasing: %s" % msg
        # ...and the gate does not fire on the rungs BELOW Purchasing, where the
        # buyer who owns both documents has not been asked for them yet.
        low, _ = svc.create_pr({"title": "Off plan, low rung", "department": "Quality"},
                               [{"item": "Target part", "qty": 10, "unit_price": 0,
                                 "item_id": iid}], tech, priced=False)
        price(low, 140.0)
        conn = get_db(); l_low = _stages(conn, low); conn.close()
        assert l_low[0] != "purchasing", l_low
        ok, msg = svc.act_on_step(low, walk_to(low, l_low[0]), "approve")
        assert ok, ("a rung below Purchasing was blocked on a document only "
                    "Purchasing can supply: %s" % msg)
        print("re-price: on-plan signed, re-priced 40% over -> quotes then memo "
              "demanded on the rung above Purchasing")

        # ---- 8b. keep-until is the LAST day kept, not the first day disposable
        today = svc._now()[:10]
        conn = get_db()
        conn.execute("UPDATE pr_requests SET retention_until=? WHERE id=?", (today, c))
        conn.commit(); conn.close()
        ok, msg = svc.dispose_pr(c, {"username": "admin"})
        assert not ok and msg == "retained_until:" + today, (
            "a record labelled 'keep until %s' was disposable ON %s -- one day "
            "short of the stated period: %s / %s" % (today, today, ok, msg))
        yday = (datetime.strptime(today, "%Y-%m-%d")
                - timedelta(days=1)).strftime("%Y-%m-%d")
        conn = get_db()
        conn.execute("UPDATE pr_requests SET retention_until=? WHERE id=?", (yday, c))
        conn.commit(); conn.close()
        ok, msg = svc.dispose_pr(c, {"username": "admin"})
        assert ok, "the day after the keep-until date it must be disposable: %s" % msg
        print("retention: refused ON %s, allowed the day after %s" % (today, yday))

        # ---- 8c. a retired record is out of the register for signing too -----
        ret, _ = unpriced("Retired mid-flight", "Quality", "Air filter")
        price(ret, 900)
        quotes(ret, 1, 900)
        conn = get_db()
        conn.execute("UPDATE pr_requests SET retention_until='2001-01-01' WHERE id=?",
                     (ret,))
        conn.commit(); conn.close()
        ok, msg = svc.dispose_pr(ret, {"username": "admin"})
        assert ok, msg
        ok, msg = svc.act_on_step(ret, next_signer(), "approve")
        assert not ok and msg == "retired", (
            "a record retired from the register was signed anyway: %s / %s" % (ok, msg))
        assert row(ret)["status"] == "pending", "the retired record advanced"
        print("retired record: signature refused at the approval chokepoint")

        # ---- 8d. the DOAM 6 panel -- the same dead helper, one function down -
        conn = get_db()
        conn.execute("INSERT INTO mnt_machines (code, name, department, criticality) "
                     "VALUES (?,?,?,?)", ("MC-EJR", "Gearbox line", "Maintenance", "high"))
        mid = conn.execute("SELECT id FROM mnt_machines WHERE code=?",
                           ("MC-EJR",)).fetchone()["id"]
        eid2, _ = E.create(conn, {"machine_id": mid, "request_type": "breakdown",
                                  "description": "Gearbox seal weeping oil",
                                  "root_cause": "Seal hardened past service life",
                                  "criticality": "production_critical",
                                  "downtime_risk": "Line stops within a shift",
                                  "stock_on_hand": 0, "stock_checked_with": "store",
                                  "alternatives": "No equivalent seal in stores"}, tech)
        ok, msg = E.submit(conn, eid2)
        assert ok, msg
        ok, msg = E.decide(conn, eid2, True, {"username": "enghead"})
        assert ok, msg
        conn.commit(); conn.close()
        mro, _ = unpriced("Gearbox seal", "Maintenance", "Gearbox seal")
        price(mro, 400)
        quotes(mro, 1, 400)
        mro_buyer = walk_to(mro, "purchasing")
        _http_ejr(app, svc, mro, mro_buyer, eid2)

        # =================================================================
        # 7. ...and all of it is REACHABLE from the browser
        # =================================================================
        # Every control above is entered or satisfied through the request page.
        # This section renders that page and drives the two new POSTs, because a
        # gate whose document cannot be typed in is a gate that only blocks:
        # the 4.4 panel had been raising NameError into a bare `except` on every
        # single request, so it had NEVER rendered.
        _http(app, over, big, C)

        print("PASS: retention is a date with a guard and a report; every R9 "
              "constant is now wired or gone")
        return True


def _http(app, off_plan_pr, huge_pr, C):
    import json
    import re

    import io
    import os
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
        # a record whose retention has already run out, so the retire control shows
        old_pr = conn.execute("SELECT id FROM pr_requests WHERE is_active=1 "
                              "ORDER BY id LIMIT 1").fetchone()["id"]
        conn.execute("UPDATE pr_requests SET retention_until='2001-01-01' WHERE id=?",
                     (old_pr,))
        conn.commit()
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
            used = set(re.findall(r'data-i18n(?:-ph)?="([^"]+)"', html))
            # Scoped to the keys THIS task adds — the page carries plenty of
            # keys other lanes own, and policing those is not this check's job.
            mine = {k for k in used
                    if k.startswith(("proc.ret_", "proc.memo_", "proc.bcase_"))}
            assert mine, "none of the new controls rendered at all"
            missing = sorted(k for k in mine if k not in common)
            assert not missing, "i18n keys missing from en/ar/tr: %s" % missing
            return html

        html = page(old_pr)
        assert "proc.ret_until" in html, "the retention date is not on the record"
        assert "/dispose" in html, "an expired record offers no way for a human to act"

        html = page(off_plan_pr)
        assert "proc.dev_title" in html, (
            "the DOAM 4.4 panel did not render - it raised into a bare except "
            "and reported 'nothing to show'")
        assert "proc.memo_ok" in html or "proc.memo_req" in html, "no memo control"

        html = page(huge_pr)
        assert "proc.bcase_ok" in html or "proc.bcase_req" in html, (
            "a 12,000,000 request shows no business-case control")

        tok = re.search(r'name="_csrf" value="([^"]*)"', html).group(1)
        r = cl.post("/procurement/pr/%d/doam-doc" % huge_pr,
                    data={"_csrf": tok, "field": "notes", "text": "x" * 40},
                    follow_redirects=True)
        assert "Unknown document" in r.get_data(as_text=True), (
            "the document field allow-list is open - it is interpolated into SQL")
        r = cl.post("/procurement/pr/%d/dispose" % huge_pr,
                    data={"_csrf": tok}, follow_redirects=True)
        assert "still inside its retention period" in r.get_data(as_text=True), (
            "the HTTP route disposed of a record inside its retention window")
        r = cl.post("/procurement/pr/%d/dispose" % old_pr,
                    data={"_csrf": tok}, follow_redirects=True)
        assert "Record retired from the register" in r.get_data(as_text=True), (
            "an expired record could not be retired through the UI")
    print("http: panels render, every key resolves in en/ar/tr, "
          "retire refused inside the window and accepted outside it")


def _http_ejr(app, svc, pr_id, buyer, ejr_id):
    """DOAM 6 through the browser, with the gate switched on the way an admin
    switches it on.

    _ejr_context() had the identical missing-import-into-a-bare-except bug as
    the 4.4 panel, so it always returned its all-false default and the panel
    never rendered. With the gate on, that left Purchasing refused on every
    spares requisition with no way to clear it from the UI.
    """
    import re

    with app.app_context():
        from app.db import get_db
        conn = get_db()
        u = conn.execute("SELECT id, session_epoch FROM users WHERE role='super_admin' "
                         "AND is_active=1 ORDER BY id LIMIT 1").fetchone()
        conn.close()

    with app.test_client() as cl:
        with cl.session_transaction() as sess:
            sess["uid"] = u["id"]
            sess["ep"] = u["session_epoch"] or 0

        def page():
            r = cl.get("/procurement/pr/%d" % pr_id)
            assert r.status_code == 200, r.status_code
            return r.get_data(as_text=True)

        html = page()
        tok = re.search(r'name="_csrf" value="([^"]*)"', html).group(1)

        # switch the gate ON through the real settings screen
        r = cl.post("/maintenance/workflow/setting",
                    data={"_csrf": tok, "key": "ejr_gate", "value": "1"},
                    follow_redirects=True)
        assert r.status_code == 200, r.status_code
        with app.app_context():
            from app.db import get_db
            from app.maintenance import eng_justification as E
            conn = get_db()
            on = E.gate_enabled(conn)
            conn.close()
        assert on, "the settings screen did not switch the DOAM 6 gate on"

        # with the gate on, Purchasing is blocked...
        ok, msg = svc.act_on_step(pr_id, buyer, "approve")
        assert not ok and msg == "ejr_missing", (ok, msg)

        # ...and the page must offer the way to clear it
        html = page()
        assert "proc.ejr." in html, (
            "the DOAM 6 panel did not render -- _ejr_context called get_db with "
            "no import and the bare except reported 'no report required'")
        assert "/engineering-justification" in html, (
            "no attach-report form: Purchasing is blocked with no browser path")
        assert 'value="%d"' % ejr_id in html, (
            "the approved report is not in the picker")

        r = cl.post("/procurement/pr/%d/engineering-justification" % pr_id,
                    data={"_csrf": tok, "ejr_id": str(ejr_id)},
                    follow_redirects=True)
        assert "cited on this request" in r.get_data(as_text=True), \
            r.get_data(as_text=True)[:400]

        ok, msg = svc.act_on_step(pr_id, buyer, "approve")
        assert ok, "citing the report from the browser did not clear the gate: %s" % msg
        assert "proc.ejr.ok" in page(), "the cited report is not shown on the record"

        # leave the gate as it was found
        cl.post("/maintenance/workflow/setting",
                data={"_csrf": tok, "key": "ejr_gate", "reset": "1"},
                follow_redirects=True)
    print("http: DOAM 6 gate switched on from the settings screen, panel renders, "
          "report cited from the browser, purchasing signature cleared")


if __name__ == "__main__":
    run()
