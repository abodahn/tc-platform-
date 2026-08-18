"""DOAM §3.4 — "...OR AGREED FORECAST", proved on the REAL flow.

The sales-order half of the clause was tightened (tests_so_gate.py) and that
closed a real hole, but it also removed the only route forecast-driven buying
had: a garment factory that buys fabric ahead of a confirmed client order had no
compliant path at all. This file drives the other half of the clause through the
entry points the web UI uses — create_pr(..., priced=False) with the requester
price lockout, submit_pr(), price_pr() at the pricing gate, and the real POST
routes of the forecast screen.

Both directions of every control, because a control that always blocks is as
broken as one that never does:

    no reference            -> REFUSED (cost_object_required)
    junk sales order        -> REFUSED (so_unknown)
    junk forecast reference -> REFUSED (fc_unknown)
    real, open sales order  -> ALLOWED
    DRAFT forecast          -> REFUSED (fc_unapproved)
    forecast out of date    -> REFUSED (fc_expired)
    agreed, in-date forecast-> ALLOWED, and prices through the gate

    a signer who is not the planning authority -> may NOT agree a forecast
    the Supply Chain Director                  -> may

Run:  python app/approvals/tests_forecast_gate.py
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "fcgate.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _day(offset):
    return (datetime.now(timezone.utc) + timedelta(days=offset)).strftime("%Y-%m-%d")


FABRIC = "Cotton twill fabric 100% CO"


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        from app.approvals import pdf

        tech = {"username": "tech", "id": 9}
        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        scd = {"username": "scd", "role": "supply_chain_director", "id": 21}
        wm = {"username": "wm", "role": "warehouse_manager", "id": 22}

        def raise_pr(title, **header):
            """A request exactly as the UI makes one: no prices anywhere."""
            h = {"title": title, "department": "Production"}
            h.update(header)
            pr_id, _ = svc.create_pr(h, [{"item": title, "qty": 500,
                                          "unit_price": 0}],
                                     tech, submit=False, priced=False)
            return pr_id

        def submit(title, **header):
            pr_id = raise_pr(title, **header)
            ok, msg = svc.submit_pr(pr_id, tech)
            return pr_id, ok, msg

        # ---------------------------------------------------------------
        # 1. Nothing has changed for the half that already worked.
        # ---------------------------------------------------------------
        _, ok, msg = submit(FABRIC)
        assert not ok and msg == "cost_object_required", (ok, msg)

        _, ok, msg = submit(FABRIC, so_no="x")
        assert not ok and msg == "so_unknown", (ok, msg)

        conn = get_db()
        live = conn.execute(
            "SELECT order_no FROM ord_orders WHERE status NOT IN ('closed','cancelled') "
            "ORDER BY id LIMIT 1").fetchone()
        assert live, "no open sales order on file — the fixture cannot run"
        LIVE_SO = live["order_no"]
        conn.close()

        _, ok, msg = submit(FABRIC, so_no=LIVE_SO)
        assert ok, "a real open sales order was refused: %s" % msg

        _, ok, msg = submit("Bearing 6204 for sewing line 3")
        assert ok, "MRO was dragged into the gate: %s" % msg

        # ---------------------------------------------------------------
        # 2. A forecast reference that is not in the register is not a
        #    cost object — the field is as validated as the SO field.
        # ---------------------------------------------------------------
        for junk in ("x", "-", "n/a", "FC-9999"):
            _, ok, msg = submit(FABRIC, forecast_ref=junk)
            assert not ok and msg == "fc_unknown", (
                "%r satisfied the forecast gate: %s / %s" % (junk, ok, msg))

        # ---------------------------------------------------------------
        # 3. Recording a forecast is not agreeing one.
        # ---------------------------------------------------------------
        ok, ref = svc.create_forecast(
            {"ref": "FC-2026-Q1", "description": "Basic 5-pocket denim programme",
             "period": "2026 Q1", "valid_from": _day(-10), "valid_to": _day(80)},
            tech)
        assert ok and ref == "FC-2026-Q1", (ok, ref)

        _, ok, msg = submit(FABRIC, forecast_ref="FC-2026-Q1")
        assert not ok and msg == "fc_unapproved", (
            "a DRAFT forecast bought fabric: %s / %s" % (ok, msg))

        # ---------------------------------------------------------------
        # 4. WHO may agree one. DOAM Table 4 L2 — "SC-D owns operational and
        #    inventory replenishment" — so the authority is the one that signs
        #    the 'scd' rung, checked with the same can_act() the ladder uses.
        # ---------------------------------------------------------------
        conn = get_db()
        fc_id = conn.execute("SELECT id FROM proc_forecasts WHERE ref=?",
                             ("FC-2026-Q1",)).fetchone()["id"]
        conn.close()

        ok, msg = svc.agree_forecast(fc_id, tech)
        assert not ok and msg == "not_authorised", (
            "a requester agreed their own forecast: %s / %s" % (ok, msg))
        ok, msg = svc.agree_forecast(fc_id, buyer)
        assert not ok and msg == "not_authorised", (
            "Purchasing agreed the plan it then buys against: %s / %s" % (ok, msg))
        ok, msg = svc.agree_forecast(fc_id, wm)
        assert not ok and msg == "not_authorised", (
            "a warehouse manager (a signer, but not the planning authority) "
            "agreed a forecast: %s / %s" % (ok, msg))

        ok, msg = svc.agree_forecast(fc_id, scd)
        assert ok, "the Supply Chain Director could not agree a forecast: %s" % msg

        conn = get_db()
        row = conn.execute("SELECT * FROM proc_forecasts WHERE id=?", (fc_id,)).fetchone()
        conn.close()
        assert row["status"] == "agreed" and row["agreed_by"] == "scd", dict(row)
        assert row["agreed_role"] == "supply_chain_director", dict(row)
        assert row["agreed_at"], "an agreed forecast must record WHEN"

        # ---------------------------------------------------------------
        # 5. ...and now the same fabric request goes through, and prices.
        # ---------------------------------------------------------------
        good, ok, msg = submit(FABRIC, forecast_ref="FC-2026-Q1")
        assert ok, "an AGREED, in-date forecast was refused: %s" % msg

        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (good,)).fetchone()["id"]
        conn.close()
        ok, msg = svc.price_pr(good, {li: 60}, {"tax_rate": 14}, buyer)
        assert ok, "pricing a forecast-backed request failed: %s" % msg

        # ---------------------------------------------------------------
        # 6. Validity is not decoration: an out-of-date forecast is refused,
        #    even though it is agreed and signed by the right person.
        # ---------------------------------------------------------------
        ok, ref = svc.create_forecast(
            {"ref": "FC-2025-Q4", "description": "Last season", "period": "2025 Q4",
             "valid_from": _day(-200), "valid_to": _day(-30)}, tech)
        assert ok, ref
        conn = get_db()
        old_id = conn.execute("SELECT id FROM proc_forecasts WHERE ref=?",
                              ("FC-2025-Q4",)).fetchone()["id"]
        conn.close()
        ok, msg = svc.agree_forecast(old_id, scd)
        assert ok, msg

        _, ok, msg = submit(FABRIC, forecast_ref="FC-2025-Q4")
        assert not ok and msg == "fc_expired", (
            "an expired forecast still bought fabric: %s / %s" % (ok, msg))

        # A forecast that has not STARTED yet is equally no cost object.
        ok, ref = svc.create_forecast(
            {"ref": "FC-2027-Q1", "valid_from": _day(60), "valid_to": _day(150)}, tech)
        assert ok, ref
        conn = get_db()
        fut = conn.execute("SELECT id FROM proc_forecasts WHERE ref=?",
                           ("FC-2027-Q1",)).fetchone()["id"]
        conn.close()
        assert svc.agree_forecast(fut, scd)[0]
        _, ok, msg = submit(FABRIC, forecast_ref="FC-2027-Q1")
        assert not ok and msg == "fc_expired", (ok, msg)

        # The picker only ever offers the ones that would actually work.
        active = [f["ref"] for f in svc.list_forecasts(active_only=True)]
        assert active == ["FC-2026-Q1"], active
        assert len(svc.list_forecasts()) == 3

        # A blank end date defaults to one production quarter, not "forever".
        ok, ref = svc.create_forecast({"ref": "FC-DEFAULT"}, tech)
        assert ok, ref
        conn = get_db()
        d = conn.execute("SELECT valid_from, valid_to FROM proc_forecasts WHERE ref=?",
                         ("FC-DEFAULT",)).fetchone()
        conn.close()
        assert d["valid_from"] == _day(0) and d["valid_to"] == _day(90), dict(d)

        # A date the gate could not compare would be a hole, so it is parsed.
        for bad in ({"ref": "FC-BAD-1", "valid_from": "2026-13-45"},
                    {"ref": "FC-BAD-2", "valid_from": "tomorrow"},
                    {"ref": "FC-BAD-3", "valid_from": _day(10), "valid_to": _day(1)}):
            ok, msg = svc.create_forecast(bad, tech)
            assert not ok and msg == "bad_dates", (bad, ok, msg)
        assert not svc.create_forecast({"ref": "  "}, tech)[0]
        assert svc.create_forecast({"ref": "FC-2026-Q1"}, tech) == (False, "duplicate_ref")

        # ---------------------------------------------------------------
        # 7. The golden thread (§5): the forecast reference reaches every
        #    downstream document, exactly as a sales order does.
        # ---------------------------------------------------------------
        conn = get_db()
        prrow = dict(conn.execute("SELECT * FROM pr_requests WHERE id=?", (good,)).fetchone())
        conn.close()
        assert prrow["forecast_ref"] == "FC-2026-Q1", prrow["forecast_ref"]
        pairs = pdf._cost_object_pairs(prrow)
        assert ("Agreed forecast", "FC-2026-Q1") in pairs, pairs
        # ...and it is the SAME helper the PR, PO, GRN and debit note all print,
        # so it cannot print on one document and be missing from the next.
        import inspect
        calls = inspect.getsource(pdf).count("_cost_object_pairs(pr)") - 1   # -1 = the def
        assert calls == 4, "PR, PO, GRN and debit note = 4 call sites, found %d" % calls
        # An empty cost object still prints nothing at all.
        assert pdf._cost_object_pairs({"so_no": None, "cost_center": None,
                                       "asset_code": None, "forecast_ref": None}) == []

        # ---------------------------------------------------------------
        # 8. The refusals are readable, in all three languages, and the
        #    request form and register screen are wired to the same service.
        # ---------------------------------------------------------------
        for lang in ("en", "ar", "tr"):
            ui = svc.labels(lang)["ui"]
            for key in ("fc_unknown_flash", "fc_unapproved_flash", "fc_expired_flash"):
                assert ui.get(key), "%s missing in %s" % (key, lang)
        from app.routes import approvals as rt
        with app.test_request_context("/procurement/new"):
            for code in ("fc_unknown", "fc_unapproved", "fc_expired"):
                text = rt._submit_error(code)
                assert "(" + code + ")" not in text, code

        # The register screen: a real GET, a real POST, and the real agree POST.
        conn = get_db()
        for uname, role in (("scd", "supply_chain_director"),
                            ("wm2", "warehouse_manager")):
            conn.execute("INSERT INTO users (username, full_name, role, is_active, "
                         "password_hash) VALUES (?,?,?,1,'x')", (uname, uname, role))
        conn.commit()
        ids = {r["username"]: r["id"] for r in conn.execute(
            "SELECT id, username FROM users WHERE username IN ('scd','wm2')").fetchall()}
        conn.close()

        client = app.test_client()

        def as_user(uid):
            with client.session_transaction() as s:
                s["uid"] = uid
                s["ep"] = 0
                s["_csrf_token"] = "tok"
            # This file runs inside ONE app context and Flask reuses it for every
            # test request, so auth.current_user()'s per-context cache would hand
            # every request whatever the first one saw (here: None -> a redirect
            # to the login page instead of the register).
            from flask import g
            g.pop("user", None)

        as_user(ids["wm2"])
        assert client.get("/procurement/forecasts").status_code == 200
        r = client.post("/procurement/forecasts",
                        data={"_csrf": "tok", "ref": "FC-UI-1",
                              "description": "Raised on the screen",
                              "period": "2026 Q2", "valid_from": _day(-1),
                              "valid_to": _day(60)})
        assert r.status_code in (302, 303), r.status_code
        conn = get_db()
        ui_fc = conn.execute("SELECT * FROM proc_forecasts WHERE ref=?",
                             ("FC-UI-1",)).fetchone()
        conn.close()
        assert ui_fc and ui_fc["status"] == "draft", "the screen did not record a draft"

        # A warehouse manager holds proc_approve, so the route lets them in —
        # and the service still refuses, because they are not the SC-D.
        r = client.post("/procurement/forecasts/%d/agree" % ui_fc["id"],
                        data={"_csrf": "tok"})
        conn = get_db()
        still = conn.execute("SELECT status FROM proc_forecasts WHERE id=?",
                             (ui_fc["id"],)).fetchone()["status"]
        conn.close()
        assert still == "draft", "the screen let a non-planning role agree a forecast"

        as_user(ids["scd"])
        r = client.post("/procurement/forecasts/%d/agree" % ui_fc["id"],
                        data={"_csrf": "tok"})
        conn = get_db()
        now_st = conn.execute("SELECT status, agreed_by FROM proc_forecasts WHERE id=?",
                              (ui_fc["id"],)).fetchone()
        conn.close()
        assert now_st["status"] == "agreed" and now_st["agreed_by"] == "scd", dict(now_st)

        # ...and the request form now offers it. (The SC-D approves, they do not
        # raise requests — proc_create belongs to the requester.)
        as_user(ids["wm2"])
        html = client.get("/procurement/new").get_data(as_text=True)
        assert 'name="forecast_ref"' in html, "the request form has no forecast field"
        assert "FC-UI-1" in html and "FC-2025-Q4" not in html, (
            "the picker must offer agreed, in-date forecasts and nothing else")

        # Every data-i18n key on the new screen and the new form field must exist
        # in all THREE dictionaries — app.js prints a key it cannot find raw, in
        # every language, English included.
        import json
        import re
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        keys = set()
        for tpl in ("forecasts.html", "new.html", "detail.html"):
            html_src = (root / "app" / "templates" / "approvals" / tpl).read_text(
                encoding="utf-8")
            keys |= {k for k in re.findall(r'data-i18n="([^"]+)"', html_src)
                     if k.startswith("proc.fc_")}
        keys.add("nav.proc_forecasts")
        assert len(keys) >= 20, keys
        for lang in ("en", "ar", "tr"):
            dic = json.loads((root / "app" / "static" / "i18n" / ("%s.json" % lang))
                             .read_text(encoding="utf-8"))
            missing = sorted(k for k in keys if not (dic.get(k) or "").strip())
            assert not missing, "%s.json is missing %s" % (lang, missing)

        print("PASS: no reference / junk SO / junk forecast / draft forecast / "
              "expired forecast REFUSED; open sales order and agreed in-date "
              "forecast ALLOWED; only the Supply Chain Director may agree one")
        return True


if __name__ == "__main__":
    run()
