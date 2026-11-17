# -*- coding: utf-8 -*-
"""The owner's approval ladder (decided 2026-11-17), proved on the real flow.

Every NEW request, OPEX and CAPEX alike, at any value, signs:
    warehouse -> warehouse_manager -> purchasing -> factory_manager
      -> control (Financial Control) -> cfo -> ceo (always last)
No Supply Chain Director, no Board, no amount gating. A request already
circulating with the old steps keeps them.

    python -m app.approvals.tests_ladder_v2
"""
import io
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]
EXPECT = ["warehouse", "warehouse_manager", "purchasing", "factory_manager",
          "control", "cfo", "ceo"]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="ladder2_"), "l.db")
    from app import create_app
    return create_app()


USERS = {  # username -> role (fixtures in a throwaway database)
    "lv_req": "normal_user", "lv_store": "storekeeper", "lv_whm": "warehouse_manager",
    "lv_buyer": "purchasing_manager", "lv_pd": "plant_director",
    "lv_fc": "financial_controller", "lv_cfo": "cfo", "lv_ceo": "ceo",
}


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.security import refresh_db_roles, user_has_permission
        from app.approvals import constants as C, services as svc, pdf as P
        from app.approvals import i18n_text as T

        conn = get_db()
        for u, role in USERS.items():
            conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                         "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                         (u, "x", u.upper(), role))
        conn.commit()
        conn.close()
        refresh_db_roles()
        U = {u: {"username": u, "role": r, "id": 0} for u, r in USERS.items()}

        def steps(pid):
            conn = get_db()
            try:
                return [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq, id",
                    (pid,)).fetchall()]
            finally:
                conn.close()

        def make(title, price, kind="opex"):
            return svc.create_pr(
                {"title": title, "department": "Production", "currency": "EGP",
                 "vendor": "Ladder Vendor", "expenditure_kind": kind},
                [{"item": "Needle DBx1", "unit": "Pcs", "qty": 1, "unit_price": price}],
                U["lv_req"], priced=price > 0)[0]

        print("\n1. the ladder itself: every rung, any value, both kinds")
        for total, kind in ((1_000, "opex"), (20_000_000, "opex"),
                            (500_000, "capex"), (0, None)):
            chk("build_ladder(%s, %s)" % (total, kind),
                C.build_ladder(total, kind) == EXPECT, C.build_ladder(total, kind))
        stale = {"warehouse": 0, "warehouse_manager": 0, "purchasing": 0,
                 "factory_manager": 10_000, "control": 1e9, "cfo": 500_000,
                 "ceo": 2_000_000}
        chk("a stale amount matrix cannot bring gating back",
            C.build_ladder(1_000, "opex", stale) == EXPECT)
        chk("no amount knob is offered on the governance screen",
            not [k for k in C.WORKFLOW_SETTINGS if k.startswith("thr_")])
        chk("scd / bod definitions kept for historic requests",
            C.stage_label("scd") == "Supply Chain Director"
            and C.stage_label("bod") == "Board of Directors"
            and "scd" in C.STAGE_ROLES and "bod" in C.DOAM_LEVEL)

        print("\n2. real requests: 1,000 OPEX, 20,000,000 OPEX, CAPEX")
        small = make("Small", 0)
        chk("1,000 OPEX at submit", steps(small) == EXPECT, steps(small))
        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (small,)).fetchone()["id"]
        conn.close()
        good, msg = svc.price_pr(small, {li: 1000}, {"tax_rate": 0}, U["lv_buyer"])
        chk("pricing at 1,000 succeeds", good, msg)
        chk("1,000 OPEX after pricing", steps(small) == EXPECT, steps(small))
        big = make("Big", 20_000_000)
        chk("20,000,000 OPEX", steps(big) == EXPECT, steps(big))
        cap = make("Capex", 500_000, "capex")
        chk("CAPEX", steps(cap) == EXPECT, steps(cap))
        chk("no bod / scd on any new request",
            not ({"bod", "scd"} & set(steps(small) + steps(big) + steps(cap))))

        print("\n3. the Financial Controller role")
        chk("same procurement permissions as the CFO",
            C.PROC_ROLE_PERMS["financial_controller"] == C.PROC_ROLE_PERMS["cfo"])
        chk("granted through the live RBAC as well",
            all(user_has_permission(U["lv_fc"], p) for p in C.PROC_ROLE_PERMS["cfo"]))
        chk("same authority level as the CFO",
            C.role_level("financial_controller") == C.role_level("cfo") == "L1")
        chk("can sign Financial Control", svc.can_act(U["lv_fc"], "control"))
        chk("cannot sign the CFO rung", not svc.can_act(U["lv_fc"], "cfo"))
        chk("escalates to the CFO", C.DEFAULT_ESCALATION.get("financial_controller") == "cfo")

        print("\n4. walking the 1,000 request: order, control before cfo, ceo last")
        conn = get_db()
        conn.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency) "
                     "VALUES (?,?,?,'EGP')", (small, "Ladder Vendor", 1000))
        conn.commit()
        conn.close()
        for who in ("lv_store", "lv_whm", "lv_buyer", "lv_pd"):
            good, msg = svc.act_on_step(small, U[who], "approve")
            chk("%s signs" % who, good, msg)
        good, msg = svc.act_on_step(small, U["lv_cfo"], "approve")
        chk("the CFO cannot sign before Financial Control", not good, msg)
        conn = get_db()
        n = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE target_user=?",
                         ("lv_fc",)).fetchone()["n"]
        conn.close()
        chk("the Financial Controller was notified it is their turn", n >= 1, n)
        for who in ("lv_fc", "lv_cfo"):
            good, msg = svc.act_on_step(small, U[who], "approve")
            chk("%s signs" % who, good, msg)
        conn = get_db()
        st = conn.execute("SELECT status FROM pr_requests WHERE id=?", (small,)).fetchone()["status"]
        conn.close()
        chk("still pending until the CEO signs", st == "pending", st)
        good, msg = svc.act_on_step(small, U["lv_ceo"], "approve")
        chk("the CEO signs last", good, msg)
        conn = get_db()
        st = conn.execute("SELECT status FROM pr_requests WHERE id=?", (small,)).fetchone()["status"]
        conn.close()
        chk("completing the CEO approves the request", st == "approved", st)

        print("\n5. one person cannot sign Financial Control AND CFO")
        sod = make("SoD", 1000)
        conn = get_db()
        conn.execute("INSERT INTO pr_quotes (pr_id, vendor, amount, currency) "
                     "VALUES (?,?,?,'EGP')", (sod, "Ladder Vendor", 1000))
        # the Financial Controller also holds the CFO's authority by delegation
        conn.execute("INSERT INTO proc_delegations (from_user, to_user, is_active, "
                     "created_at) VALUES ('lv_cfo','lv_fc',1,'2026-01-01')")
        conn.commit()
        conn.close()
        for who in ("lv_store", "lv_whm", "lv_buyer", "lv_pd", "lv_fc"):
            good, msg = svc.act_on_step(sod, U[who], "approve")
            chk("%s signs" % who, good, msg)
        chk("the delegate is eligible for the CFO rung on paper",
            svc.can_act(U["lv_fc"], "cfo"))
        good, msg = svc.act_on_step(sod, U["lv_fc"], "approve")
        chk("...but is refused it: segregation of duties", not good and msg == "dual_role", msg)
        conn = get_db()
        conn.execute("UPDATE proc_delegations SET is_active=0")
        conn.commit()
        conn.close()

        print("\n6. a request already in flight keeps its old steps")
        old = make("In flight", 60_000)
        old_ladder = ["warehouse", "warehouse_manager", "purchasing",
                      "factory_manager", "scd", "cfo", "ceo", "bod"]
        conn = get_db()
        conn.execute("DELETE FROM pr_steps WHERE pr_id=?", (old,))
        for i, s in enumerate(old_ladder, start=1):
            conn.execute("INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, "
                         "created_at, origin) VALUES (?,?,?,?,?,'2026-11-01','ladder')",
                         (old, i, s, "pending", C.stage_label(s)))
        conn.execute("UPDATE pr_requests SET current_seq=1 WHERE id=?", (old,))
        conn.commit()
        conn.close()
        good, msg = svc.act_on_step(old, U["lv_store"], "approve")
        chk("the store signs the old request", good, msg)
        chk("its stored ladder is untouched (scd and bod still there)",
            steps(old) == old_ladder, steps(old))
        b = svc.get_pr(old)
        chk("it still renders through get_pr", b and len(b["steps"]) == len(old_ladder))
        raw = P.pr_pdf(b)
        chk("its PDF still renders", raw[:4] == b"%PDF")

        print("\n7. re-pricing an old request moves it onto the new ladder")
        conn = get_db()
        li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (old,)).fetchone()["id"]
        conn.close()
        good, msg = svc.price_pr(old, {li: 70_000}, {"tax_rate": 0}, U["lv_buyer"])
        chk("re-pricing succeeds", good, msg)
        chk("scd / bod dropped, control added before cfo, ceo last",
            steps(old) == EXPECT, steps(old))

        def seed_old(pid, rungs, cur):
            """Replace pid's steps with `rungs` [(stage, status, origin)], current = seq `cur`."""
            conn = get_db()
            conn.execute("DELETE FROM pr_steps WHERE pr_id=?", (pid,))
            for i, (s, st, origin) in enumerate(rungs, start=1):
                conn.execute("INSERT INTO pr_steps (pr_id, seq, stage, status, approver_role, "
                             "approver_user, created_at, origin) "
                             "VALUES (?,?,?,?,?,?,'2026-11-01',?)",
                             (pid, i, s, st, C.stage_label(s),
                              "lv_old_%d" % i if st == "approved" else None, origin))
            conn.execute("UPDATE pr_requests SET current_seq=? WHERE id=?", (cur, pid))
            conn.commit()
            conn.close()

        def reprice(pid, price):
            conn = get_db()
            row = conn.execute("SELECT tax_rate FROM pr_requests WHERE id=?", (pid,)).fetchone()
            li = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pid,)).fetchone()["id"]
            conn.close()
            return svc.price_pr(pid, {li: price}, {"tax_rate": row["tax_rate"] or 0},
                                U["lv_buyer"])

        def approved(pid):
            conn = get_db()
            try:
                return [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? AND status='approved' "
                    "ORDER BY seq", (pid,)).fetchall()]
            finally:
                conn.close()

        print("\n7b. a legacy deviation 'finance' rung never lands after the CEO")
        dev = make("Deviation legacy", 50_000)
        seed_old(dev, [("warehouse", "pending", "ladder"), ("purchasing", "pending", "ladder"),
                       ("finance", "pending", "deviation"), ("ceo", "pending", "deviation")], 1)
        good, msg = svc.act_on_step(dev, U["lv_store"], "approve")
        chk("the store signs it", good, msg)
        good, msg = reprice(dev, 55_000)
        chk("re-pricing succeeds", good, msg)
        got = steps(dev)
        chk("CEO is last, finance sorts before control/cfo",
            got[-1] == "ceo" and got.index("finance") < got.index("control")
            < got.index("cfo"), got)

        print("\n7c. a late re-price at the same terms never appends a rung below a signed one")
        old_six = ["warehouse", "warehouse_manager", "purchasing", "factory_manager", "cfo", "ceo"]
        for parked in ("cfo", "ceo"):
            late = make("Late %s" % parked, 40_000)
            cur = old_six.index(parked) + 1
            seed_old(late, [(s, "approved" if i < cur else "pending", "ladder")
                            for i, s in enumerate(old_six, start=1)], cur)
            before = approved(late)
            good, msg = reprice(late, 40_000)
            chk("parked at %s: same-price re-price succeeds" % parked, good, msg)
            chk("parked at %s: no signature withdrawn" % parked, approved(late) == before,
                approved(late))
            chk("parked at %s: stored steps kept (no control after %s)" % (parked, parked),
                steps(late) == old_six, steps(late))
        late = make("Late changed", 40_000)
        seed_old(late, [(s, "approved" if i < 5 else "pending", "ladder")
                        for i, s in enumerate(old_six, start=1)], 5)
        good, msg = reprice(late, 45_000)
        chk("changed price rewinds and gains control before cfo, ceo last",
            good and steps(late) == ["warehouse", "warehouse_manager", "purchasing",
                                     "factory_manager", "control", "cfo", "ceo"],
            (msg, steps(late)))

        print("\n7d. boot refreshes untouched old workflow texts, never admin-edited ones")
        from app.approvals import schema as SC
        from app.approvals.superseded_text import SUPERSEDED
        old_cfo = SUPERSEDED["proc_stage_meta"]["explanation"]["cfo"]
        old_cfo_ar = SUPERSEDED["proc_stage_meta"]["explanation_ar"]["cfo"]
        old_doc = SUPERSEDED["proc_doc"]["body"]["overview"]
        conn = get_db()
        conn.execute("UPDATE proc_stage_meta SET explanation=?, explanation_ar=? WHERE stage='cfo'",
                     (old_cfo, old_cfo_ar))
        conn.execute("UPDATE proc_stage_meta SET explanation=? WHERE stage='ceo'",
                     ("Admin wording for the CEO",))
        conn.execute("UPDATE proc_doc SET body=? WHERE section='overview'", (old_doc,))
        conn.commit()
        SC.seed_governance(conn)
        row = conn.execute("SELECT explanation, explanation_ar FROM proc_stage_meta "
                           "WHERE stage='cfo'").fetchone()
        chk("old default stage text (en + ar) replaced by the new default",
            row["explanation"] == C.STAGE_EXPLAIN["cfo"]
            and row["explanation_ar"] == T.STAGE["ar"]["cfo"], row["explanation"][:60])
        chk("old default doc text replaced",
            conn.execute("SELECT body FROM proc_doc WHERE section='overview'")
            .fetchone()["body"] == dict(C.DOC_SECTIONS)["overview"])
        chk("admin-edited text left alone",
            conn.execute("SELECT explanation FROM proc_stage_meta WHERE stage='ceo'")
            .fetchone()["explanation"] == "Admin wording for the CEO")
        chk("second boot rewrites nothing", SC.refresh_superseded_text(conn) == 0)
        conn.close()

        print("\n8. the Control rung on the screen and on paper")
        chk("labels in en / ar / tr",
            C.stage_label("control") == "Financial Control"
            and T.STAGE_LABEL["ar"]["control"] == "الكنترول المالي"
            and T.STAGE_LABEL["tr"]["control"] == "Finansal Kontrol")
        chk("role label in ar / tr",
            T.ROLE_LABEL["ar"]["financial_controller"] == "المراقب المالي"
            and T.ROLE_LABEL["tr"]["financial_controller"] == "Finansal Kontrolör")
        dicts = {}
        for lg in ("en", "ar", "tr"):
            import json
            dicts[lg] = json.load(io.open(str(Path(C.__file__).resolve().parents[1]
                                              / "static" / "i18n" / (lg + ".json")),
                                          encoding="utf-8"))
        chk("i18n JSON key sets identical",
            set(dicts["en"]) == set(dicts["ar"]) == set(dicts["tr"]))
        chk("proc.stage.control in every dictionary",
            dicts["ar"].get("proc.stage.control") == "الكنترول المالي")

        try:
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() or "" for p in
                             PdfReader(io.BytesIO(P.pr_pdf(svc.get_pr(big)))).pages)
            chk("PR PDF signature block names Financial Control",
                "FinancialControl" in re.sub(r"\s+", "", text))
            chk("PR PDF has no Board / Supply Chain box",
                "Board" not in text and "SupplyChain" not in re.sub(r"\s+", "", text))
        except ImportError:
            chk("pypdf available", False, "not installed")

    # Outside the app context above: inside it the signed-in user is cached for
    # the whole block, so a language switch between requests is not seen.
    client = app.test_client()
    with client.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
        s["_csrf_token"] = "tok"
    for lg, want in (("en", "Financial Control"), ("ar", "الكنترول المالي")):
        with app.app_context():
            from app.db import get_db
            conn = get_db()
            conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lg,))
            conn.commit()
            conn.close()
        r = client.get("/procurement/pr/%d" % big)
        chk("PR detail [%s] -> 200 and names the Control rung" % lg,
            r.status_code == 200 and want in r.data.decode("utf-8"), r.status_code)
        r = client.get("/procurement/workflow")
        chk("workflow page [%s] names the Control rung" % lg,
            r.status_code == 200 and want in r.data.decode("utf-8"), r.status_code)

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
