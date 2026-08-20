"""The three defects the beta DOAM validation found, each closed and proved.

All three were reachable from the screen and none was visible in the code:

  1. /procurement/settings returned HTTP 500 for every admin, so the
     responsibility matrix could not be opened at all.
  2. The §4.3 single-source rung was derived once and never re-read, so
     re-pricing upward left the waiver signature BELOW the value tier — waiving
     competition bought no extra approval.
  3. The §4.2 expenditure type was an unvalidated string, so every unrecognised
     value silently took OPEX, the weaker ladder, and a reclassification after
     rejection was recorded only as "edited".
"""
import os
import tempfile


def _app():
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "valfix.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    return app


def _rungs(conn, pid):
    return [(r["stage"], r["origin"]) for r in conn.execute(
        "SELECT stage, COALESCE(origin,'ladder') AS origin FROM pr_steps "
        "WHERE pr_id=? ORDER BY seq", (pid,)).fetchall()]


def run():
    app = _app()
    ok = True

    def chk(label, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))

    # ---- 1. the governance screens must OPEN -------------------------------
    # C.LADDER rebinds to the 8-stage DOAM ladder while APPROVAL_MATRIX is the
    # 6-stage paper form; a page iterating one and reading the other formats an
    # Undefined and 500s. Reading a template's own data source is the only way
    # to catch this — the route has no error until it renders.
    print("the responsibility-matrix and governance screens")
    c = app.test_client()
    with c.session_transaction() as s:
        s["user"] = {"username": "admin", "role": "super_admin", "id": 1}
        s["uid"] = 1
    for url in ("/procurement/settings", "/procurement/workflow",
                "/governance/rules", "/governance/forms"):
        r = c.get(url)
        chk(url, r.status_code == 200, r.status_code)
    html = c.get("/procurement/settings").get_data(as_text=True)
    from app.approvals import constants as C
    for stage in C.LADDER:
        chk("settings lists %s" % stage, stage in html)
    chk("and shows a real threshold, not 0",
        "{:,.0f}".format(C.ACTIVE_MATRIX["finance"]) in html)

    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}
        tech = {"username": "tech", "id": 9}

        # ---- 2. single source must track the value tier ---------------------
        print("\n§4.3 single source follows the value, not the pricing history")
        pid, _ = svc.create_pr({"title": "OEM drive", "department": "IT"},
                               [{"item": "Servo drive", "qty": 1, "unit_price": 0}],
                               tech, priced=False)
        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pid,)).fetchone()["id"]
        conn.close()
        svc.price_pr(pid, {li: 30000}, {}, buyer)
        svc.set_single_source(pid, "Proprietary OEM part", {"username": "purch", "id": 3})
        conn = get_db(); low = _rungs(conn, pid); conn.close()
        low_ss = [s for s, o in low if o == "single_source"]
        chk("at 30,000 the waiver adds a rung", bool(low_ss), low)

        svc.price_pr(pid, {li: 600000}, {}, buyer)      # the same request, repriced
        conn = get_db(); high = _rungs(conn, pid); conn.close()
        high_ss = [s for s, o in high if o == "single_source"]
        ladder = [s for s, o in high if o != "single_source"]
        chk("re-pricing to 600,000 MOVES the waiver rung", high_ss != low_ss,
            "%s -> %s" % (low_ss, high_ss))
        chk("and it sits ABOVE the value ladder, which is the whole point",
            high_ss and high_ss[0] not in ladder, "%s vs %s" % (high_ss, ladder))

        # ---- 3. the expenditure type may not downgrade silently --------------
        print("\n§4.2 an unrecognised expenditure type is not a silent OPEX")
        for raw, want, known in [("capex", "capex", True), ("Capital", "capex", True),
                                 ("capital expenditure", "capex", True),
                                 ("capex​", "capex", True),
                                 ("opex", "opex", True),
                                 ("capitol", "opex", False), ("1", "opex", False),
                                 ("", "opex", False), (None, "opex", False)]:
            got, rec = C.normalise_expenditure_kind(raw)
            chk("%-22r -> %s (recognised=%s)" % (raw, want, known),
                got == want and rec == known, "%s/%s" % (got, rec))

        pid2, _ = svc.create_pr({"title": "Press", "department": "IT",
                                 "expenditure_kind": "capex"},
                                [{"item": "Press", "qty": 1, "unit_price": 0}],
                                tech, priced=False, submit=False)
        svc.update_pr(pid2, {"title": "Press", "department": "IT",
                             "expenditure_kind": "opex"},
                      [{"item": "Press", "qty": 1, "unit_price": 0}],
                      tech, can_price=False)
        conn = get_db()
        actions = [r["action"] for r in conn.execute(
            "SELECT action FROM pr_events WHERE pr_id=?", (pid2,)).fetchall()]
        kind = conn.execute("SELECT expenditure_kind FROM pr_requests WHERE id=?",
                            (pid2,)).fetchone()["expenditure_kind"]
        conn.close()
        chk("changing CAPEX to OPEX is audited, not just 'edited'",
            "expenditure_kind_changed" in actions, actions)
        chk("and the change is applied", kind == "opex", kind)

    print("\n" + ("RESULT: ALL GREEN" if ok else "RESULT: FAILURES ABOVE"))
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if run() else 1)
