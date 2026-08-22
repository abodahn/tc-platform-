# -*- coding: utf-8 -*-
"""
Approval aging self-test — throwaway database.
    python app/approvals/tests_aging.py

The question this feature exists to answer is a buyer's: "what is stuck, and who
is sitting on it?" So the checks are driven the way a buyer produces the state —
requests raised and submitted through services.create_pr / submit_pr, then read
back through the real /procurement/aging route with a real session — not by
writing pr_steps rows by hand.

The ONE thing that cannot be produced honestly is the passage of time: nothing
waits four days for a self-test. So `age_rung` winds pr_steps.activated_at and
pr_requests.submitted_at backwards, which is exactly the state the clock would
have left behind, and nothing else about the request is touched.

Covered: both clocks (rung vs request), the ordering, the department and rung
filters, the two admin thresholds including the refusal of a value that would
bell everyone, the overdue bell reaching the people who can actually sign, and
the property the whole notification is worth nothing without — the same person
is never told about the same rung twice, however often the job runs.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="aging_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                               # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from flask import g, url_for                                # noqa: E402
from markupsafe import escape                               # noqa: E402

from app import create_app                                  # noqa: E402

app = create_app()
PASS, FAIL = [], []


def ok(label, cond, extra=""):
    PASS.append(bool(cond))
    if not cond:
        FAIL.append(label + ((" | " + str(extra)) if extra else ""))
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


I18N_KEY = re.compile(r'data-i18n(?:-ph|-title)?="([^"]+)"')


def main():
    with app.app_context():
        from app.db import get_db
        from app.approvals.schema import create_and_seed
        from app.approvals import services as svc
        from app.approvals import constants as C
        from app.approvals import aging as AG
        from app.navigation import NAV

        conn = get_db()
        create_and_seed(conn)
        conn.commit()

        def mkuser(username, role):
            from werkzeug.security import generate_password_hash
            conn.execute(
                "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                (username, generate_password_hash("x"), username.title(), role))
            conn.commit()
            return dict(conn.execute("SELECT * FROM users WHERE username=?",
                                     (username,)).fetchone())

        def client_as(user):
            c = app.test_client()
            with c.session_transaction() as s:
                s["uid"] = user["id"]
                s["ep"] = 0
            return c

        def GET(cl, url):
            """One request through the real stack.

            `g` is cleared first because this whole file runs inside ONE app
            context and Flask reuses it for a test request instead of pushing a
            fresh one — so auth.current_user's per-request cache would otherwise
            hand the next request the PREVIOUS reader, and a permission check
            would pass for the wrong person."""
            g.pop("user", None)
            return cl.get(url)

        def age_rung(pr_id, rung_days, extra_days=0):
            """Wind this request's clocks back. rung_days on the ACTIVE rung, and
            rung_days+extra_days since it was submitted — the two are different
            numbers on purpose, because the screen reports both."""
            def stamp(days):
                return (datetime.now(timezone.utc)
                        - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE pr_steps SET activated_at=? WHERE pr_id=? AND status='pending' "
                "AND seq=(SELECT current_seq FROM pr_requests WHERE id=?)",
                (stamp(rung_days), pr_id, pr_id))
            conn.execute("UPDATE pr_requests SET submitted_at=? WHERE id=?",
                         (stamp(rung_days + extra_days), pr_id))
            conn.commit()

        def row_for(view, pr_no):
            for r in view["rows"]:
                if r["pr_no"] == pr_no:
                    return r
            return None

        def alerts_for(pr_id):
            return {r["target_user"] for r in conn.execute(
                "SELECT target_user FROM notifications WHERE link=? "
                "AND title LIKE 'Approval waiting%'", (svc._pr_link(pr_id),)).fetchall()}

        def alert_rows(pr_id):
            return conn.execute(
                "SELECT COUNT(*) c FROM notifications WHERE link=? "
                "AND title LIKE 'Approval waiting%'",
                (svc._pr_link(pr_id),)).fetchone()["c"]

        requester = mkuser("ag_req", "production_manager")
        reader = mkuser("ag_reader", "purchasing_manager")     # has proc_view
        outsider = mkuser("ag_outsider", "hr_officer")         # has not

        print("\n--- 0. the two knobs exist and are admin-editable -------------")
        ok("aging_warn_days and aging_overdue_days are WORKFLOW_SETTINGS",
           "aging_warn_days" in C.WORKFLOW_SETTINGS
           and "aging_overdue_days" in C.WORKFLOW_SETTINGS)
        ok("both are ints with a floor of 1 (0 would make every rung overdue)",
           all(C.WORKFLOW_SETTINGS[k]["kind"] == "int"
               and C.WORKFLOW_SETTINGS[k]["min"] == 1
               for k in ("aging_warn_days", "aging_overdue_days")))
        ok("warn comes before overdue by default (%s < %s)"
           % (C.WORKFLOW_SETTINGS["aging_warn_days"]["default"],
              C.WORKFLOW_SETTINGS["aging_overdue_days"]["default"]),
           C.WORKFLOW_SETTINGS["aging_warn_days"]["default"]
           < C.WORKFLOW_SETTINGS["aging_overdue_days"]["default"])
        ok("the governance page still lists every knob (no orphan)",
           len(svc.workflow_view("Production")["knobs"]) == len(C.WORKFLOW_SETTINGS))

        WARN = int(svc.num_setting(conn, "aging_warn_days"))
        OVER = int(svc.num_setting(conn, "aging_overdue_days"))

        print("\n--- 1. three real requests, aged by the clock only ------------")
        pr_a, no_a = svc.create_pr(
            {"title": "Pallet truck", "department": "Production", "currency": "EGP"},
            [{"item": "Pallet truck", "qty": 1, "unit_price": 20000}],
            requester, submit=True)
        pr_b, no_b = svc.create_pr(
            {"title": "Cutting blades", "department": "Cutting", "currency": "EGP"},
            [{"item": "Blades", "qty": 20, "unit_price": 250}],
            requester, submit=True)
        pr_c, no_c = svc.create_pr(
            {"title": "Office chair", "department": "Production", "currency": "EGP"},
            [{"item": "Office chair", "qty": 1, "unit_price": 900}],
            requester, submit=True)
        ok("all three entered the ladder",
           all(conn.execute("SELECT status FROM pr_requests WHERE id=?",
                            (p,)).fetchone()["status"] == "pending"
               for p in (pr_a, pr_b, pr_c)))
        age_rung(pr_a, OVER + 2, extra_days=2)     # overdue, and older than its rung
        age_rung(pr_b, WARN + 1)                   # amber
        # pr_c is left alone: submitted seconds ago, waiting on nobody yet.

        v = AG.aging_view()
        ra, rb, rc = row_for(v, no_a), row_for(v, no_b), row_for(v, no_c)
        ok("every pending request is listed once", ra and rb and rc)
        ok("...on the rung it is ACTUALLY sitting on, not a rung ahead",
           all(r["seq"] == conn.execute(
               "SELECT current_seq FROM pr_requests WHERE pr_no=?",
               (r["pr_no"],)).fetchone()["current_seq"] for r in v["rows"]))
        ok("days on the current rung is measured from activated_at (%.1f)"
           % ra["days_on_rung"], abs(ra["days_on_rung"] - (OVER + 2)) < 0.05)
        ok("days since submitted is a DIFFERENT, longer clock (%.1f vs %.1f)"
           % (ra["days_since_submit"], ra["days_on_rung"]),
           abs(ra["days_since_submit"] - (OVER + 4)) < 0.05)
        ok("a request that just moved onto its rung reads ~0 days (%.2f)"
           % rc["days_on_rung"], rc["days_on_rung"] < 0.05)
        ok("bands: overdue / warn / on time", (ra["level"], rb["level"], rc["level"])
           == ("overdue", "warn", "ok"), (ra["level"], rb["level"], rc["level"]))
        ok("the longest wait is first", v["rows"][0]["pr_no"] == no_a,
           v["rows"][0]["pr_no"])
        ok("the value held up is the tax-inclusive EGP commitment",
           abs(ra["value_egp"] - svc.egp_commitment(dict(conn.execute(
               "SELECT * FROM pr_requests WHERE id=?", (pr_a,)).fetchone()))) < 0.01)
        ok("it names roles that can sign, not just the stage",
           bool(ra["signers"]["role_names"]) and bool(ra["signers"]["people"]),
           ra["signers"])
        ok("the counters agree with the rows",
           v["n"] == len(v["rows"])
           and v["n_overdue"] == sum(1 for r in v["rows"] if r["level"] == "overdue")
           and v["n_warn"] == sum(1 for r in v["rows"] if r["level"] == "warn"))

        print("\n--- 2. the overdue bell reaches the people who can sign -------")
        step_a = dict(conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? AND status='pending' AND seq="
            "(SELECT current_seq FROM pr_requests WHERE id=?)", (pr_a, pr_a)).fetchone())
        signers = set(svc.eligible_approvers(
            conn, step_a["stage"], roles=svc._csv_set(step_a["esc_role"]) or None))
        ok("the overdue rung has someone who can sign it", bool(signers), signers)
        told = AG.run_aging_alerts()
        ok("the job reported telling %d people" % told, told == len(signers), signers)
        ok("exactly the eligible signers were belled, nobody else",
           alerts_for(pr_a) == signers, alerts_for(pr_a) ^ signers)
        ok("the amber request rang no bell — the screen is where 'due soon' lives",
           alert_rows(pr_b) == 0)
        ok("nor did the one nobody has waited on", alert_rows(pr_c) == 0)
        sev = {r["severity"] for r in conn.execute(
            "SELECT severity FROM notifications WHERE link=? AND title LIKE 'Approval waiting%'",
            (svc._pr_link(pr_a),)).fetchall()}
        ok("severity is the platform's warn level (sev-warning is a real style)",
           sev == {"warning"}, sev)

        before = alert_rows(pr_a)
        ok("a second run tells nobody anything (%d new)" % AG.run_aging_alerts(),
           alert_rows(pr_a) == before)
        for _ in range(3):
            AG.run_aging_alerts()
        ok("...and neither does a third, fourth or fifth",
           alert_rows(pr_a) == before)
        ok("one row per signer, not one per run",
           before == len(signers), (before, len(signers)))

        print("\n--- 3. the screen itself, through the route -------------------")
        c = client_as(reader)
        r = GET(c, "/procurement/aging")
        ok("GET /procurement/aging -> 200", r.status_code == 200, r.status_code)
        html = r.get_data(as_text=True)
        ok("the overdue request is on the page", no_a in html)
        ok("so is the amber one", no_b in html)
        ok("oldest first on screen too", html.index(no_a) < html.index(no_b))
        ok("it shows who can sign", any(p in html for p in ra["signers"]["people"]),
           ra["signers"]["people"])
        ok("it shows the live thresholds in the reader's own sentence",
           str(WARN) in html and str(OVER) in html)
        ok("the row links to the request itself", ("/procurement/pr/%d" % pr_a) in html)
        ok("opening the screen did not re-bell anyone", alert_rows(pr_a) == before)

        print("\n--- 4. the filters -------------------------------------------")
        html = GET(c, "/procurement/aging?dept=Cutting").get_data(as_text=True)
        ok("department filter keeps Cutting", no_b in html)
        ok("...and drops Production", no_a not in html and no_c not in html)
        ok("the department dropdown still offers Production (a filter you can undo)",
           ">Production<" in html)
        vv = AG.aging_view(department="Cutting")
        ok("the service agrees: one row, and it is the Cutting one",
           [x["pr_no"] for x in vv["rows"]] == [no_b])
        stage_a = step_a["stage"]
        vs = AG.aging_view(stage=stage_a)
        ok("rung filter returns only rows on that rung",
           vs["rows"] and all(x["stage"] == stage_a for x in vs["rows"]))
        other = [s["key"] for s in v["stages"] if s["key"] != stage_a]
        if other:
            ok("...and a different rung does not return them",
               all(x["stage"] != stage_a
                   for x in AG.aging_view(stage=other[0])["rows"]))
        ok("an unknown filter value returns nothing rather than everything",
           AG.aging_view(department="Atlantis")["rows"] == [])

        print("\n--- 5. the thresholds actually move the line ------------------")
        okk, msg = svc.set_setting("aging_overdue_days", OVER + 5, requester)
        ok("an admin raises the overdue line (%s)" % msg, okk)
        moved = row_for(AG.aging_view(), no_a)
        ok("the same request is no longer overdue (%s)" % moved["level"],
           moved["level"] != "overdue")
        ok("and the overdue counter fell with it",
           AG.aging_view()["n_overdue"] == 0)
        ok("a raised line silences the bell too", AG.run_aging_alerts() == 0)
        okk, msg = svc.set_setting("aging_warn_days", OVER + 6, requester)
        ok("warn is stored as given — ordering is the admin's to keep (%s)" % msg, okk)
        svc.reset_setting("aging_warn_days", requester)
        okk, msg = svc.set_setting("aging_overdue_days", 0, requester)
        ok("0 days is refused, so nobody can bell the whole plant hourly (%s)" % msg,
           not okk and msg == "out_of_range")
        okk, msg = svc.set_setting("aging_overdue_days", "soon", requester)
        ok("so is a value that is not a number (%s)" % msg, not okk)
        svc.reset_setting("aging_overdue_days", requester)
        ok("reset puts the code default back in force",
           int(svc.num_setting(conn, "aging_overdue_days")) == OVER
           and row_for(AG.aging_view(), no_a)["level"] == "overdue")

        print("\n--- 6. a signer who arrives LATER is told; the rest are not ---")
        newcomer = mkuser("ag_late_store", "storekeeper")
        del newcomer
        fresh = set(svc.eligible_approvers(
            conn, step_a["stage"], roles=svc._csv_set(step_a["esc_role"]) or None))
        added = fresh - signers
        ok("the new role holder is now eligible for that rung", added == {"ag_late_store"},
           added)
        told = AG.run_aging_alerts()
        ok("only the newcomer is told (%d)" % told, told == len(added))
        ok("the people already told still have exactly one notice each",
           alert_rows(pr_a) == before + len(added))
        ok("and the newcomer has one", "ag_late_store" in alerts_for(pr_a))

        print("\n--- 7. it is a proc_view screen ------------------------------")
        ok("a user without proc_view is refused",
           GET(client_as(outsider), "/procurement/aging").status_code == 403)
        ok("signed out is refused too",
           GET(app.test_client(), "/procurement/aging").status_code in (302, 401, 403))

        print("\n--- 8. sidebar wiring and trilingual copy --------------------")
        entries = [it for sec in NAV for it in sec["items"] if it[0] == "proc_aging"]
        ok("the sidebar declares proc_aging exactly once", len(entries) == 1)
        it = entries[0]
        ok("...pointing at this blueprint, behind proc_view",
           it[3] == "proc_aging.index" and it[4] == "proc_view", it)
        with app.test_request_context():
            ok("...and the endpoint resolves to /procurement/aging",
               url_for("proc_aging.index") == "/procurement/aging")
        ok("the nav label has an I18N entry of its own", it[1] in AG.I18N)

        ok("every I18N entry carries all three languages, none blank",
           all(isinstance(v3, tuple) and len(v3) == 3 and all(str(s).strip() for s in v3)
               for v3 in AG.I18N.values()))
        arabic = re.compile(r"[؀-ۿ]")
        bad_ar = [k for k, (en, ar, tr) in AG.I18N.items() if not arabic.search(ar)]
        ok("the Arabic column is Arabic, not English copied over", not bad_ar,
           ", ".join(bad_ar[:5]))
        bad_tr = [k for k, (en, ar, tr) in AG.I18N.items() if tr.strip() == en.strip()]
        ok("the Turkish column is Turkish", not bad_tr, ", ".join(bad_tr[:5]))
        for lg in ("en", "ar", "tr"):
            conn.execute("UPDATE users SET lang_pref=? WHERE id=?", (lg, reader["id"]))
            conn.commit()
            rr = GET(c, "/procurement/aging")
            ok("[%s] the screen renders" % lg, rr.status_code == 200, rr.status_code)
            body = rr.get_data(as_text=True)
            # escape(): the sentence names "Workflow & Governance", and Jinja
            # writes that ampersand as &amp;.
            ok("[%s] the threshold sentence is in that language" % lg,
               str(escape(AG.t("aging.rule", lg).format(warn=WARN, overdue=OVER)))
               in body)

        shipped = {}
        for lg in ("en", "ar", "tr"):
            with open(REPO / "app" / "static" / "i18n" / ("%s.json" % lg),
                      encoding="utf-8") as fh:
                shipped[lg] = json.load(fh)
        keys = sorted(set(I18N_KEY.findall(GET(c, "/procurement/aging")
                                           .get_data(as_text=True))))
        # A key this module OWNS lives in AG.I18N until release merges it into the
        # shared dictionaries; a key it merely reuses must already be there, in all
        # three languages, or the page prints the raw key to a reader.
        missing = [k for k in keys
                   if k not in AG.I18N and any(k not in shipped[lg]
                                               for lg in ("en", "ar", "tr"))]
        ok("all %d data-i18n keys on the page are owned here or already shipped"
           % len(keys), not missing, ", ".join(missing[:6]))

        conn.close()

    print("\n================ %d passed, %d failed ================"
          % (len(PASS) - len(FAIL), len(FAIL)))
    for f in FAIL:
        print("  FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
