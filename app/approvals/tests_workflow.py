"""
Procurement Workflow & Governance self-test — runs against a throwaway database.
    python app/approvals/tests_workflow.py

Proves, by execution, that the configurable-workflow addition is a pure ADDITION:
  (a) with NO override rows every behaviour equals the constants, including a
      real end-to-end flow (submit -> ladder -> pricing gate -> RFQ gate ->
      signatures -> PO -> receipt -> invoice -> payment cap);
  (b) an override is actually CONSUMED by the code path, not merely stored;
  (c) a reset DELETES the row and the constant applies again;
  (d) garbage (blank / non-numeric / NaN / inf / negative / out-of-range /
      unknown role) always falls back and never locks a stage or opens a gate;
  (e) the page renders for a viewer and the POSTs are refused (403) for them;
  (f) create_and_seed is idempotent and never overwrites an admin's edited text.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wf_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                          # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                             # noqa: E402

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


def mkuser(conn, username, role):
    conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                 "is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                 (username, "x", username.title(), role))
    conn.commit()
    return dict(conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone())


with app.app_context():
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    from app.approvals import services as svc
    from app.approvals import constants as C

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    admin = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
    store = mkuser(conn, "wf_store", "storekeeper")
    fmgr = mkuser(conn, "wf_fm", "factory_manager")
    viewer = mkuser(conn, "wf_view", "executive_viewer")

    # ----------------------------------------------------------------- (a)
    print("\n-- (a) BACKWARD COMPATIBILITY: no override rows --")
    n_over = conn.execute("SELECT COUNT(*) c FROM proc_settings").fetchone()["c"]
    ok("proc_settings is empty after seeding (absent row == use the constant)", n_over == 0)

    ok("stage->role map equals constants.STAGE_ROLES",
       svc.stage_roles_map() == {k: set(v) for k, v in C.STAGE_ROLES.items()})
    ok("stage_role() per stage equals the constant",
       all(svc.stage_role(s) == C.STAGE_ROLES[s] for s in C.LADDER))
    ok("rfq_quote_min == C.RFQ_QUOTE_MIN (%r)" % C.RFQ_QUOTE_MIN,
       svc.num_setting(conn, "rfq_quote_min") == C.RFQ_QUOTE_MIN)
    ok("rfq_value_threshold == C.RFQ_VALUE_THRESHOLD (%r)" % C.RFQ_VALUE_THRESHOLD,
       svc.num_setting(conn, "rfq_value_threshold") == C.RFQ_VALUE_THRESHOLD)
    ok("payment_tolerance_pct == C.PAYMENT_TOLERANCE_PCT (%r)" % C.PAYMENT_TOLERANCE_PCT,
       svc.num_setting(conn, "payment_tolerance_pct") == C.PAYMENT_TOLERANCE_PCT)
    ok("sod_admin_exempt == C.SOD_ADMIN_EXEMPT (%r)" % C.SOD_ADMIN_EXEMPT,
       svc.bool_setting(conn, "sod_admin_exempt") is C.SOD_ADMIN_EXEMPT)
    # the tolerance arithmetic must be bit-identical to the old literal 0.01
    ok("tolerance maths is bit-identical to the old * 0.01",
       48000.0 * (C.PAYMENT_TOLERANCE_PCT / 100.0) == 48000.0 * 0.01)
    ok("effective ladder equals constants.build_ladder at every band",
       all(svc.dept_ladder(conn, "Production", t) == C.build_ladder(t)
           for t in (0, 1, 9999, 10000, 24999, 25000, 48000, 99999, 100000, 5_000_000)))
    ok("eligible_approvers('warehouse') uses the constant roles (finds wf_store)",
       "wf_store" in svc.eligible_approvers(conn, "warehouse"))
    ok("can_act: storekeeper may sign warehouse, not cfo",
       svc.can_act(store, "warehouse") and not svc.can_act(store, "cfo"))

    # --- one real end-to-end flow, asserting the pre-change outcomes ---
    # ONE DISTINCT PERSON PER RUNG. SOD_ADMIN_EXEMPT is False, so the dual-role
    # rule refuses any account — admin included — a second rung of the same
    # request. The ladder is walked the way a real approval chain walks it, the
    # pattern tests_three_way_match.py uses.
    signer = {stage: mkuser(conn, "wf_sign_" + stage, sorted(roles)[0])
              for stage, roles in C.STAGE_ROLES.items()}
    buyer = signer["purchasing"]

    # DOAM §4.1 tier 4 (500,001 - 2,000,000): the last signature is the CFO's, so
    # this one request still exercises Finance AND the CFO as the final approver.
    # Under the paper form that was 30,000; the DOAM does not involve either at
    # that value (§4.1 tier 2 stops at the directors).
    TOTAL = 600_000.0
    print("\n   end-to-end: unpriced PR -> gates -> signatures -> PO -> payment")
    pr_id, pr_no = svc.create_pr(
        {"title": "WF flow", "department": "Production", "currency": "EGP"},
        [{"item": "Battery", "qty": 1, "unit_price": 0}], store, submit=True)
    steps = [r["stage"] for r in conn.execute(
        "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
    ok("unpriced PR routes the demand stages only %r" % (steps,), steps == C.DEMAND_STAGES)

    okk, msg = svc.act_on_step(pr_id, signer["warehouse"], "approve")
    ok("warehouse signs: %s" % msg, okk)
    okk, msg = svc.act_on_step(pr_id, buyer, "approve")          # purchasing, unpriced
    ok("PRICING GATE blocks the purchasing stage while unpriced (%s)" % msg,
       not okk and msg == "needs_pricing")

    svc.price_pr(pr_id, {}, {"tax_rate": 0}, buyer)              # still 0 -> price the line
    it = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
    svc.price_pr(pr_id, {it: TOTAL}, {"tax_rate": 0}, buyer)
    steps = [r["stage"] for r in conn.execute(
        "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
    ok("pricing reconciles the value ladder to build_ladder(%d) %r" % (TOTAL, steps),
       steps == C.build_ladder(TOTAL))

    okk, msg = svc.act_on_step(pr_id, buyer, "approve")          # priced, no quotes
    ok("RFQ GATE blocks purchasing at %d EGP with no quotes (%s)" % (TOTAL, msg),
       not okk and msg == "needs_quotes")
    need = int(C.quotes_required(TOTAL))
    svc.add_quote(pr_id, {"vendor": "Vendor A", "amount": TOTAL}, buyer)
    svc.add_quote(pr_id, {"vendor": "Vendor B", "amount": TOTAL * 1.03}, buyer)
    okk, msg = svc.act_on_step(pr_id, buyer, "approve")
    ok("two quotes are short of DOAM §4.3's %d in this band (%s)" % (need, msg),
       not okk and msg == "needs_quotes")
    svc.add_quote(pr_id, {"vendor": "Vendor C", "amount": TOTAL * 1.05}, buyer)
    okk, msg = svc.act_on_step(pr_id, buyer, "approve")
    ok("%d DISTINCT vendor quotes satisfy the RFQ gate: %s" % (need, msg), okk)
    okk, msg = svc.act_on_step(pr_id, signer["factory_manager"], "approve")
    ok("factory manager signs: %s" % msg, okk)
    okk, msg = svc.act_on_step(pr_id, signer["scd"], "approve")
    ok("supply chain director signs: %s" % msg, okk)
    okk, msg = svc.act_on_step(pr_id, signer["finance"], "approve")
    ok("finance signs: %s" % msg, okk)
    okk, msg = svc.act_on_step(pr_id, signer["cfo"], "approve")  # -> fully approved
    ok("cfo signs and the PR is fully approved: %s" % msg, okk and msg == "approved")
    ok("PR status is 'approved' with a drafted PO",
       conn.execute("SELECT status, po_no FROM pr_requests WHERE id=?",
                    (pr_id,)).fetchone()["status"] == "approved")
    okk, po = svc.issue_po(pr_id, admin)
    ok("PO issues (Production has no budget row -> never blocked): %s" % po, okk)
    # receive the whole order through the ONE receiving entry point the app has
    svc.receive_items(pr_id, {r["id"]: r["qty"] for r in conn.execute(
        "SELECT id, qty FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()}, admin)
    # Billed at the top of the §7.3.3 match tolerance, so what is under test below
    # is the PAYMENT CAP and not §4.3's advance gate: money leaving before the
    # supplier has billed for it is an advance, which is a different control with
    # its own refusal codes (tests_three_way_match covers that one).
    INVOICED = TOTAL + C.match_tolerance_value(TOTAL)
    svc.add_invoice(pr_id, {"invoice_no": "INV-1", "amount": INVOICED, "tax": 0}, admin)
    okk, msg = svc.add_payment(pr_id, {"amount": TOTAL * 1.02}, admin)   # +2%
    ok("PAYMENT CAP refuses %d on a %d PO (1%% tolerance): %s"
       % (TOTAL * 1.02, TOTAL, msg),
       not okk and msg in ("over_payment", "exceeds_invoiced"))
    okk, msg = svc.add_payment(pr_id, {"amount": TOTAL * 1.0067}, admin)  # inside 1%
    ok("payment inside the 1%% tolerance is accepted: %s" % msg, okk)

    # ----------------------------------------------------------------- (b)
    print("\n-- (b) OVERRIDES ARE CONSUMED BY THE CODE PATH --")
    pr2, _ = svc.create_pr({"title": "WF gate", "department": "Production", "currency": "EGP"},
                           [{"item": "Pump", "qty": 1, "unit_price": 30000}], store, submit=True)
    row2 = conn.execute("SELECT * FROM pr_requests WHERE id=?", (pr2,)).fetchone()
    ok("baseline: RFQ gate blocks this 30 000 PR (no quotes)",
       svc.rfq_gate_check(conn, row2) == (False, "needs_quotes"))
    # DOAM §4.3's sourcing bands — not these two knobs — decide the quote count
    # while the matrix is in force: a settings row must never buy a purchase out
    # of a policy minimum. The knobs are still the rule with the matrix off, so
    # that is where they are asserted, exactly as before; the band that overrides
    # them is asserted straight after.
    _doam = C.DOAM_IN_FORCE
    try:
        C.DOAM_IN_FORCE = False
        svc.set_setting("rfq_value_threshold", "100000", admin)
        ok("override rfq_value_threshold=100000 -> the same PR now passes the gate",
           svc.rfq_gate_check(conn, row2) == (True, ""))
        svc.set_setting("rfq_value_threshold", "1000", admin)
        svc.add_quote(pr2, {"vendor": "Vendor A", "amount": 30000}, admin)
        svc.add_quote(pr2, {"vendor": "Vendor B", "amount": 31000}, admin)
        ok("with 2 quotes and threshold 1000 the gate passes",
           svc.rfq_gate_check(conn, row2) == (True, ""))
        svc.set_setting("rfq_quote_min", "3", admin)
        ok("override rfq_quote_min=3 -> 2 quotes are no longer enough",
           svc.rfq_gate_check(conn, row2) == (False, "needs_quotes"))
    finally:
        C.DOAM_IN_FORCE = _doam
    ok("...and with the DOAM in force the §4.3 band binds instead of the knob "
       "(band=%d quotes at 30 000)" % C.quotes_required(30000),
       C.quotes_required(30000) == 1 and svc.rfq_gate_check(conn, row2) == (True, ""))

    svc.set_stage_meta("warehouse", roles=["factory_manager"], user=admin)
    ok("override stage role: warehouse is now signed by factory_manager only",
       svc.stage_roles_map()["warehouse"] == {"factory_manager"})
    ok("can_act follows the override (storekeeper out, factory_manager in)",
       not svc.can_act(store, "warehouse") and svc.can_act(fmgr, "warehouse"))
    ok("eligible_approvers follows the override too",
       "wf_store" not in svc.eligible_approvers(conn, "warehouse")
       and "wf_fm" in svc.eligible_approvers(conn, "warehouse"))

    svc.set_setting("sod_admin_exempt", "0", admin)
    pr3, _ = svc.create_pr({"title": "WF sod", "department": "Production", "currency": "EGP"},
                           [{"item": "Belt", "qty": 1, "unit_price": 500}], admin, submit=True)
    okk, msg = svc.act_on_step(pr3, admin, "approve")
    ok("override sod_admin_exempt=0 -> the admin can no longer self-approve (%s)" % msg,
       not okk and msg == "self_approval")

    svc.set_setting("payment_tolerance_pct", "10", admin)
    # 611,020 cumulative: past the 1% cap (606,000), inside the 10% one (660,000),
    # and still inside what the supplier invoiced, so only the knob decides it.
    okk, msg = svc.add_payment(pr_id, {"amount": 7_000.0}, admin)
    ok("override payment_tolerance_pct=10 -> a payment refused at 1%% now clears: %s" % msg, okk)

    # ----------------------------------------------------------------- (c)
    print("\n-- (c) RESET removes the override and restores the constant --")
    svc.reset_setting("rfq_quote_min", admin)
    ok("reset_setting deleted the row (not written back as a literal)",
       conn.execute("SELECT COUNT(*) c FROM proc_settings WHERE key='rfq_quote_min'"
                    ).fetchone()["c"] == 0)
    ok("rfq_quote_min is the constant again", svc.num_setting(conn, "rfq_quote_min")
       == C.RFQ_QUOTE_MIN)
    for k in ("rfq_value_threshold", "sod_admin_exempt", "payment_tolerance_pct"):
        svc.reset_setting(k, admin)
    ok("all knobs reset -> every value equals its constant again",
       svc.num_setting(conn, "rfq_value_threshold") == C.RFQ_VALUE_THRESHOLD
       and svc.bool_setting(conn, "sod_admin_exempt") is C.SOD_ADMIN_EXEMPT
       and svc.num_setting(conn, "payment_tolerance_pct") == C.PAYMENT_TOLERANCE_PCT)
    svc.set_stage_meta("warehouse", user=admin, reset_role=True)
    ok("reset_role restores constants.STAGE_ROLES['warehouse']",
       svc.stage_roles_map()["warehouse"] == set(C.STAGE_ROLES["warehouse"]))
    ok("the seeded explanation survived the role reset",
       (conn.execute("SELECT explanation FROM proc_stage_meta WHERE stage='warehouse'"
                     ).fetchone() or {"explanation": ""})["explanation"])

    # ----------------------------------------------------------------- (d)
    print("\n-- (d) GARBAGE IN: every bad value falls back, nothing is disabled --")
    def poke(key, value):
        """Write a raw value straight into the table, bypassing validation."""
        conn.execute("DELETE FROM proc_settings WHERE key=?", (key,))
        conn.execute("INSERT INTO proc_settings (key, value) VALUES (?,?)", (key, value))
        conn.commit()

    for bad in ("", "   ", "abc", "nan", "NaN", "inf", "-inf", "-5", "1e400", "0", "2,5"):
        poke("rfq_quote_min", bad)
        got = svc.num_setting(conn, "rfq_quote_min")
        ok("rfq_quote_min=%r -> constant %r (got %r)" % (bad, C.RFQ_QUOTE_MIN, got),
           got == C.RFQ_QUOTE_MIN)
    for bad in ("", "abc", "nan", "-1", "-0.5"):
        poke("rfq_value_threshold", bad)
        ok("rfq_value_threshold=%r -> constant" % bad,
           svc.num_setting(conn, "rfq_value_threshold") == C.RFQ_VALUE_THRESHOLD)
    for bad in ("", "abc", "nan", "-1", "500", "101"):
        poke("payment_tolerance_pct", bad)
        ok("payment_tolerance_pct=%r -> constant (a 500%% cap can never apply)" % bad,
           svc.num_setting(conn, "payment_tolerance_pct") == C.PAYMENT_TOLERANCE_PCT)
    for bad in ("", "maybe", "2", "null"):
        poke("sod_admin_exempt", bad)
        ok("sod_admin_exempt=%r -> constant %r" % (bad, C.SOD_ADMIN_EXEMPT),
           svc.bool_setting(conn, "sod_admin_exempt") is C.SOD_ADMIN_EXEMPT)
    conn.execute("DELETE FROM proc_settings")
    conn.commit()

    def poke_role(stage, value):
        conn.execute("UPDATE proc_stage_meta SET role=? WHERE stage=?", (value, stage))
        conn.commit()

    for bad in ("no_such_role", "", "   ", ",,,", "SUPER_ADMIN", "'; DROP TABLE users;--"):
        poke_role("finance", bad)
        ok("stage role %r -> ignored, constant roles kept" % bad,
           svc.stage_roles_map()["finance"] == set(C.STAGE_ROLES["finance"]))
    poke_role("finance", "no_such_role,cfo")
    ok("stage role 'no_such_role,cfo' -> only the real role is used",
       svc.stage_roles_map()["finance"] == {"cfo"})
    poke_role("finance", None)
    ok("a garbage role never left the stage unsignable (someone can always act)",
       all(svc.stage_roles_map()[s] for s in C.LADDER))
    ok("set_setting refuses garbage instead of storing it",
       svc.set_setting("rfq_quote_min", "abc", admin)[0] is False
       and svc.set_setting("payment_tolerance_pct", "500", admin)[0] is False
       and svc.set_setting("sod_admin_exempt", "maybe", admin)[0] is False
       and svc.set_setting("nope", "1", admin)[0] is False)
    ok("set_stage_meta refuses an unknown role and an unknown stage",
       svc.set_stage_meta("finance", roles=["no_such_role"], user=admin)[0] is False
       and svc.set_stage_meta("nope", roles=["cfo"], user=admin)[0] is False)
    ok("proc_settings still empty after the refused writes",
       conn.execute("SELECT COUNT(*) c FROM proc_settings").fetchone()["c"] == 0)

    # ----------------------------------------------------------------- (f)
    print("\n-- (f) create_and_seed is idempotent and preserves admin edits --")
    svc.set_doc("sod", "OUR OWN SoD WORDING.", admin)
    svc.set_role_meta("cfo", "OUR OWN CFO WORDING.", admin)
    svc.set_stage_meta("ceo", explanation="OUR OWN CEO WORDING.", user=admin)
    before = {t: conn.execute("SELECT COUNT(*) c FROM " + t).fetchone()["c"]
              for t in ("proc_stage_meta", "proc_role_meta", "proc_doc")}
    for _ in range(3):
        create_and_seed(conn)
        conn.commit()
    after = {t: conn.execute("SELECT COUNT(*) c FROM " + t).fetchone()["c"]
             for t in ("proc_stage_meta", "proc_role_meta", "proc_doc")}
    ok("3x create_and_seed: no duplicate rows %r" % (after,), before == after)
    ok("the admin's edited doc / role / stage text survived every re-seed",
       conn.execute("SELECT body FROM proc_doc WHERE section='sod'").fetchone()["body"]
       == "OUR OWN SoD WORDING."
       and conn.execute("SELECT explanation FROM proc_role_meta WHERE role_key='cfo'"
                        ).fetchone()["explanation"] == "OUR OWN CFO WORDING."
       and conn.execute("SELECT explanation FROM proc_stage_meta WHERE stage='ceo'"
                        ).fetchone()["explanation"] == "OUR OWN CEO WORDING.")
    ok("reset_doc restores the code default text",
       svc.set_doc("sod", reset=True, user=admin)[0]
       and svc.workflow_view("Production")["gates"][2]["body"] == C.DOC_SECTIONS["sod"])

    print("\n-- page model --")
    v = svc.workflow_view("Production")
    ok("workflow_view: 6 stages, 6 gates, %d statuses, 4 knobs"
       % len(v["statuses"]),
       len(v["stages"]) == len(C.LADDER) and len(v["gates"]) == 6
       and len(v["statuses"]) == len(C.PR_STATUSES) and len(v["knobs"]) == len(C.WORKFLOW_SETTINGS))
    ok("every stage carries a non-empty explanation %r"
       % [s["stage"] for s in v["stages"] if not s["explanation"]],
       all(s["explanation"] for s in v["stages"]))
    ok("every status and role carries a meaning %r"
       % ([s["key"] for s in v["statuses"] if not s["body"]]
          + [r["key"] for r in v["roles"] if not r["explanation"]]),
       all(s["body"] for s in v["statuses"]) and all(r["explanation"] for r in v["roles"]))
    ok("the pricing + RFQ gates are attached to the purchasing stage",
       "pricing_gate" in [s for s in v["stages"]
                          if s["stage"] == C.PRICING_GATE_STAGE][0]["gates"])
    ok("the governance change log recorded the edits", len(v["log"]) > 0)
    conn.close()

# ----------------------------------------------------------------------- (e)
print("\n-- (e) routes: viewer reads, non-admin writes are refused --")
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
        s["_csrf_token"] = "tok"
    r = cl.get("/procurement/workflow")
    ok("GET /procurement/workflow as admin -> 200 (got %d)" % r.status_code,
       r.status_code == 200)
    ok("the page renders the ladder and the gate names",
       b"Workflow" in r.data and b"Segregation" in r.data)
    r = cl.post("/procurement/workflow/setting",
                data={"_csrf": "tok", "key": "rfq_quote_min", "value": "3"})
    ok("admin POST setting -> 302 redirect (got %d)" % r.status_code, r.status_code == 302)
    r = cl.post("/procurement/workflow/setting",
                data={"_csrf": "tok", "key": "rfq_quote_min", "reset": "1"})
    ok("admin POST reset -> 302 redirect (got %d)" % r.status_code, r.status_code == 302)

with app.app_context():
    from app.db import get_db
    conn = get_db()
    vid = conn.execute("SELECT id FROM users WHERE username='wf_view'").fetchone()["id"]
    conn.close()

with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = vid
        s["ep"] = 0
        s["_csrf_token"] = "tok"
    r = cl.get("/procurement/workflow")
    ok("GET as a proc_view-only viewer -> 200 (got %d)" % r.status_code, r.status_code == 200)
    ok("the viewer sees no edit form", b"workflow/setting" not in r.data)
    for path, data in (("/procurement/workflow/setting",
                        {"key": "rfq_quote_min", "value": "9"}),
                       ("/procurement/workflow/stage", {"stage": "cfo", "roles": "ceo"}),
                       ("/procurement/workflow/role",
                        {"role_key": "cfo", "explanation": "hax"}),
                       ("/procurement/workflow/doc", {"section": "sod", "body": "hax"})):
        data["_csrf"] = "tok"
        r = cl.post(path, data=data)
        ok("POST %s as viewer -> 403 (got %d)" % (path, r.status_code), r.status_code == 403)

with app.app_context():
    from app.db import get_db
    from app.approvals import services as svc2
    from app.approvals import constants as C2
    conn = get_db()
    ok("the refused viewer POSTs changed nothing",
       svc2.num_setting(conn, "rfq_quote_min") == C2.RFQ_QUOTE_MIN
       and conn.execute("SELECT body FROM proc_doc WHERE section='sod'"
                        ).fetchone() is None)
    conn.close()

print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
