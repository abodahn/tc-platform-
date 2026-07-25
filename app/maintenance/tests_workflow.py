"""
Maintenance Workflow & Governance — executable proof.

Run:  python app/maintenance/tests_workflow.py

Proves, by execution:
  a) BACKWARD COMPATIBILITY — with no override rows the effective ladder, the
     stage->role map, the SLA numbers and the gates equal the constants, and a
     real end-to-end spare flow ends exactly where it did before.
  b) OVERRIDES TAKE EFFECT — through the code path that consumes them.
  c) RESET removes the row and restores the constant.
  d) GARBAGE IN — blank / non-numeric / NaN / negative / unknown role all fall
     back safely; no stage is ever locked and no control disabled.
  e) The page renders 200 for a viewer; the POSTs are refused (403) for a non-admin.
  f) create_and_seed x3 is idempotent and never overwrites an edited explanation.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wf_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                      # noqa: E402
from app.db import get_db                                       # noqa: E402
from app.maintenance import services as svc                     # noqa: E402
from app.maintenance import workflow as wf                       # noqa: E402
from app.maintenance.schema import create_and_seed               # noqa: E402
from app.maintenance.constants import TICKET_TRANSITIONS         # noqa: E402

app = create_app()
PASS, FAIL = [], []


def ck(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label + ((" -> " + str(extra)) if extra and not cond else ""))


def db():
    return get_db()


def uid_of(username):
    c = db()
    try:
        return c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    finally:
        c.close()


ADMIN = {"id": 1, "username": "admin", "role": "super_admin", "full_name": "Admin"}
MGR = {"id": 2, "username": "maint", "role": "maintenance_manager", "full_name": "Mgr"}
FACT = {"id": 3, "username": "factory", "role": "factory_manager", "full_name": "Factory"}
STORE = {"id": 4, "username": "store", "role": "storekeeper", "full_name": "Store"}


def spare(code):
    c = db()
    try:
        return c.execute("SELECT * FROM mnt_spare_parts WHERE code=?", (code,)).fetchone()
    finally:
        c.close()


def new_ticket(desc, machine_id=None, priority="medium"):
    return svc.create_ticket({"description": desc, "machine_id": machine_id,
                              "priority": priority, "allow_duplicate": True}, MGR)


# =====================================================================
print("\n(a) BACKWARD COMPATIBILITY — no override rows")
# =====================================================================
with app.app_context():
    c = db()
    try:
        ck("no wf override rows in mnt_settings",
           c.execute("SELECT COUNT(*) n FROM mnt_settings WHERE key IN "
                     "('sla_factor_critical','sla_factor_high','sla_factor_medium',"
                     "'sla_factor_low','critical_cost_threshold','duplicate_ticket_guard')"
                     ).fetchone()["n"] == 0)
        ck("no stage role overrides",
           c.execute("SELECT COUNT(*) n FROM mnt_stage_meta WHERE role IS NOT NULL "
                     "AND role<>''").fetchone()["n"] == 0)
        # the constants themselves
        ck("_SLA_FACTOR == the shipped constant",
           svc._SLA_FACTOR == {"critical": 0.25, "high": 0.5, "medium": 1.0, "low": 2.0},
           svc._SLA_FACTOR)
        ck("sla_factor(medium) == 1.0 and (critical) == 0.25",
           wf.sla_factor(c, "medium") == 1.0 and wf.sla_factor(c, "critical") == 0.25)
        ck("sla_factor(unknown priority) == 1.0 fallback",
           wf.sla_factor(c, "banana") == 1.0)
        ck("response SLA == 4h x factor (old literal)",
           wf.sla_hours(c, "medium", 4.0, "response_sla_hours") == 4.0
           and wf.sla_hours(c, "critical", 4.0, "response_sla_hours") == 1.0)
        ck("resolution SLA == 24h x factor (old literal)",
           wf.sla_hours(c, "medium", 24.0, "resolution_sla_hours") == 24.0)
        ck("critical_threshold == 100 (the old hardcoded number)",
           wf.critical_threshold(c) == 100.0, wf.critical_threshold(c))
        ck("duplicate-ticket guard defaults ON", wf.dup_guard_on(c) is True)
        ck("auto_reorder_pr defaults ON", wf.flag(c, "auto_reorder_pr") is True)
        # the ladder, as the picker builds it
        cheap = [{"spare_id": spare("SP-001")["id"], "qty": 1}]        # 0.45 each, high crit
        dear = [{"spare_id": spare("SP-006")["id"], "qty": 1}]         # 145.0, critical
        ck("standard ladder == ['maintenance_manager']",
           svc._pick_matrix_levels(c, cheap) == ["maintenance_manager"],
           svc._pick_matrix_levels(c, cheap))
        ck("critical ladder == ['maintenance_manager','factory_manager']",
           svc._pick_matrix_levels(c, dear) == ["maintenance_manager", "factory_manager"],
           svc._pick_matrix_levels(c, dear))
        ck("stage->role map matches the matrix",
           [l["role"] for l in wf.page_data(c)["ladder"] if not l.get("issuer")]
           == ["maintenance_manager", "maintenance_manager", "factory_manager"])
        ck("lifecycle is rendered from TICKET_TRANSITIONS untouched",
           all(set(s["next"]) == TICKET_TRANSITIONS[s["status"]]
               for s in wf.page_data(c)["statuses"]))
    finally:
        c.close()

# --- a real end-to-end flow: two-rung ladder, reserve, issue, close ---
with app.app_context():
    sp = spare("SP-006")
    stock0, res0 = sp["stock_qty"], sp["reserved_qty"] or 0
    tid, err = new_ticket("E2E: control board dead", machine_id=5, priority="high")
    ck("ticket created", err == "" and tid, err)
    c = db()
    try:
        t = c.execute("SELECT status, response_due, resolution_due, created_at FROM mnt_tickets "
                      "WHERE id=?", (tid,)).fetchone()
        ck("ticket starts 'submitted' with both SLA clocks set",
           t["status"] == "submitted" and t["response_due"] and t["resolution_due"])
        from datetime import datetime
        f = "%Y-%m-%d %H:%M:%S"
        gap_r = (datetime.strptime(t["response_due"], f) - datetime.strptime(t["created_at"], f))
        gap_x = (datetime.strptime(t["resolution_due"], f) - datetime.strptime(t["created_at"], f))
        ck("high-priority response due == 4h x 0.5 == 2h",
           abs(gap_r.total_seconds() - 7200) <= 2, gap_r)
        ck("high-priority resolution due == 24h x 0.5 == 12h",
           abs(gap_x.total_seconds() - 43200) <= 2, gap_x)
    finally:
        c.close()
    rid, err = svc.create_request(tid, [{"spare_id": sp["id"], "qty": 1}], "board dead", "urgent", MGR)
    ck("spare request created", err == "" and rid, err)
    c = db()
    try:
        aps = c.execute("SELECT * FROM mnt_approvals WHERE request_id=? ORDER BY level",
                        (rid,)).fetchall()
        ck("two rungs raised, in matrix order",
           [a["approver_role"] for a in aps] == ["maintenance_manager", "factory_manager"],
           [a["approver_role"] for a in aps])
    finally:
        c.close()
    ok, msg = svc.decide_approval(aps[0]["id"], "approve", "ok", MGR)
    ck("rung 1 signed by its own role", ok, msg)
    ok, msg = svc.decide_approval(aps[1]["id"], "approve", "ok", MGR)
    ck("rung 2 refuses the wrong role (level authority intact)",
       (ok, msg) == (False, "wrong_role"), msg)
    ok, msg = svc.decide_approval(aps[1]["id"], "approve", "ok", FACT)
    ck("rung 2 signed by the factory manager", ok, msg)
    c = db()
    try:
        r = c.execute("SELECT status FROM mnt_requests WHERE id=?", (rid,)).fetchone()["status"]
        s = c.execute("SELECT stock_qty, reserved_qty FROM mnt_spare_parts WHERE id=?",
                      (sp["id"],)).fetchone()
        ck("fully approved -> 'approved' and the part is RESERVED, not consumed",
           r == "approved" and s["stock_qty"] == stock0 and (s["reserved_qty"] or 0) == res0 + 1,
           (r, s["stock_qty"], s["reserved_qty"]))
    finally:
        c.close()
    ok, msg = svc.issue_parts(rid, "Technician A", STORE)
    ck("parts issued", ok, msg)
    c = db()
    try:
        s = c.execute("SELECT stock_qty, reserved_qty FROM mnt_spare_parts WHERE id=?",
                      (sp["id"],)).fetchone()
        t = c.execute("SELECT status, cost FROM mnt_tickets WHERE id=?", (tid,)).fetchone()
        ck("stock down 1, reservation released, cost charged to the ticket",
           s["stock_qty"] == stock0 - 1 and (s["reserved_qty"] or 0) == res0
           and t["cost"] == 145.0 and t["status"] == "parts_issued",
           (s["stock_qty"], s["reserved_qty"], t["cost"], t["status"]))
    finally:
        c.close()
    svc.confirm_receiving(rid, MGR)
    svc.repair_proof(tid, {"action_performed": "replaced board"}, MGR)
    svc.record_test(tid, {"test_result": "pass"}, MGR)
    ok, msg = svc.close_ticket(tid, MGR)
    ck("ticket closes from 'resolved' only, and closed", ok, msg)
    ck("close_ticket refuses a non-resolved ticket",
       svc.close_ticket(tid, MGR) == (False, "must_be_resolved"))

# =====================================================================
print("\n(b) OVERRIDES TAKE EFFECT (through the consuming code path)")
# =====================================================================
with app.app_context():
    c = db()
    try:
        # 1. threshold: drop it below the cheap request's value -> critical ladder
        cheap = [{"spare_id": spare("SP-001")["id"], "qty": 2}]   # 2 x 0.45 = 0.90
        wf.set_setting(c, "critical_cost_threshold", "0.5", ADMIN)
        ck("threshold override routes a cheap request to the critical ladder",
           svc._pick_matrix_levels(c, cheap) == ["maintenance_manager", "factory_manager"],
           svc._pick_matrix_levels(c, cheap))
        # 2. stage -> role: level 2 of the critical ladder signed by someone else
        ok, msg = wf.set_text(c, "stage", "crit_l2", "Now signed by production.", ADMIN,
                              role="production_manager")
        ck("stage role override stored", ok, msg)
        ck("the picker now returns the overridden signer",
           svc._pick_matrix_levels(c, cheap) == ["maintenance_manager", "production_manager"],
           svc._pick_matrix_levels(c, cheap))
        # 3. SLA numbers
        wf.set_setting(c, "response_sla_hours", "10", ADMIN)
        wf.set_setting(c, "sla_factor_high", "3", ADMIN)
        ck("SLA override used by the calculation (10h x 3)",
           wf.sla_hours(c, "high", 4.0, "response_sla_hours") == 30.0,
           wf.sla_hours(c, "high", 4.0, "response_sla_hours"))
        # 4. duplicate guard off
        wf.set_setting(c, "duplicate_ticket_guard", "0", ADMIN)
        ck("duplicate guard reads OFF", wf.dup_guard_on(c) is False)
    finally:
        c.close()
    # the ladder override really drives a new request end-to-end
    tid2, _ = new_ticket("Override run: needles", machine_id=1, priority="low")
    rid2, err = svc.create_request(tid2, [{"spare_id": spare("SP-001")["id"], "qty": 2}],
                                   "needles", "normal", MGR)
    c = db()
    try:
        roles = [a["approver_role"] for a in c.execute(
            "SELECT approver_role FROM mnt_approvals WHERE request_id=? ORDER BY level",
            (rid2,)).fetchall()]
        ck("a fresh request is raised against the overridden ladder",
           roles == ["maintenance_manager", "production_manager"], roles)
    finally:
        c.close()
    # duplicate guard OFF -> a second open ticket on the same machine is allowed
    t_a, e_a = svc.create_ticket({"description": "dup A", "machine_id": 2}, MGR)
    t_b, e_b = svc.create_ticket({"description": "dup B", "machine_id": 2}, MGR)
    ck("with the guard off a second open ticket is accepted",
       e_a == "" and e_b == "" and t_b, (e_a, e_b))

# =====================================================================
print("\n(c) RESET deletes the row and restores the constant")
# =====================================================================
with app.app_context():
    c = db()
    try:
        for k in ("critical_cost_threshold", "response_sla_hours", "sla_factor_high",
                  "duplicate_ticket_guard"):
            wf.reset_setting(c, k, ADMIN)
        left = c.execute("SELECT COUNT(*) n FROM mnt_settings WHERE key IN "
                         "('critical_cost_threshold','response_sla_hours','sla_factor_high',"
                         "'duplicate_ticket_guard')").fetchone()["n"]
        ck("override rows deleted", left == 0, left)
        ck("threshold back to 100", wf.critical_threshold(c) == 100.0)
        ck("SLA back to 4h x 0.5", wf.sla_hours(c, "high", 4.0, "response_sla_hours") == 2.0)
        ck("duplicate guard back ON", wf.dup_guard_on(c) is True)
        # reset the stage: role override gone, ladder back to the matrix
        wf.reset_text(c, "stage", "crit_l2", ADMIN)
        ck("stage reset restores the matrix signer",
           svc._pick_matrix_levels(c, [{"spare_id": spare("SP-006")["id"], "qty": 1}])
           == ["maintenance_manager", "factory_manager"])
        ck("stage reset restores the shipped explanation text",
           wf.texts(c, "stage")["crit_l2"] == wf.STAGE_TEXT["crit_l2"])
        # the guard is genuinely back: same machine, second ticket refused
        t_c, e_c = svc.create_ticket({"description": "dup C", "machine_id": 2}, MGR)
        ck("duplicate guard blocks again after reset",
           t_c is None and e_c.startswith("duplicate_open:"), (t_c, e_c))
    finally:
        c.close()

# =====================================================================
print("\n(d) GARBAGE IN -> constant wins, nothing locks")
# =====================================================================
with app.app_context():
    c = db()
    try:
        crit = [{"spare_id": spare("SP-006")["id"], "qty": 1}]
        for bad in ("", "   ", "abc", "NaN", "nan", "inf", "-inf", "-5", "1e400", None):
            c.execute("DELETE FROM mnt_settings WHERE key='critical_cost_threshold'")
            c.execute("INSERT INTO mnt_settings (key,value) VALUES (?,?)",
                      ("critical_cost_threshold", bad))
            c.commit()
            ck("threshold garbage %r -> 100" % (bad,), wf.critical_threshold(c) == 100.0,
               wf.critical_threshold(c))
        for bad in ("", "abc", "NaN", "0", "-2", "99999999"):
            c.execute("DELETE FROM mnt_settings WHERE key='sla_factor_critical'")
            c.execute("INSERT INTO mnt_settings (key,value) VALUES (?,?)",
                      ("sla_factor_critical", bad))
            c.commit()
            ck("sla factor garbage %r -> 0.25" % (bad,), wf.sla_factor(c, "critical") == 0.25,
               wf.sla_factor(c, "critical"))
        for bad in ("", "maybe", "banana"):
            c.execute("DELETE FROM mnt_settings WHERE key='duplicate_ticket_guard'")
            c.execute("INSERT INTO mnt_settings (key,value) VALUES (?,?)",
                      ("duplicate_ticket_guard", bad))
            c.commit()
            ck("bool garbage %r -> default ON" % (bad,), wf.dup_guard_on(c) is True)
        c.execute("DELETE FROM mnt_settings WHERE key IN "
                  "('critical_cost_threshold','sla_factor_critical','duplicate_ticket_guard')")
        c.commit()
        # a stage role that cannot sign, straight into the table (bypassing set_text)
        for bad in ("", "   ", "not_a_role", "finance_user", "storekeeper", None):
            c.execute("UPDATE mnt_stage_meta SET role=? WHERE stage='crit_l2'", (bad,))
            c.commit()
            got = svc._pick_matrix_levels(c, crit)
            ck("stage role %r -> rung keeps its default signer" % (bad,),
               got == ["maintenance_manager", "factory_manager"], got)
        c.execute("UPDATE mnt_stage_meta SET role=NULL WHERE stage='crit_l2'")
        c.commit()
        # and the write side refuses the same junk instead of storing it
        ck("set_text refuses a role that cannot sign",
           wf.set_text(c, "stage", "crit_l2", "x", ADMIN, role="finance_user")
           == (False, "role_cannot_sign"))
        ck("set_text refuses the issuer as an approval rung",
           wf.set_text(c, "stage", "crit_l2", "x", ADMIN, role="storekeeper")
           == (False, "role_cannot_sign"))
        ck("set_setting refuses a non-number",
           wf.set_setting(c, "response_sla_hours", "abc", ADMIN) == (False, "not_a_number"))
        ck("set_setting refuses out of range",
           wf.set_setting(c, "sla_factor_low", "-1", ADMIN) == (False, "out_of_range"))
        ck("set_setting refuses an unknown key",
           wf.set_setting(c, "rm_rf", "1", ADMIN) == (False, "unknown_setting"))
        ck("page still renders its data with garbage cleared",
           len(wf.page_data(c)["ladder"]) >= 4)
        # nothing above locked a rung: a real request still gets signable rungs
        ck("every rung is still owned by a role that can sign",
           all(wf._can_approve(r) for r in svc._pick_matrix_levels(c, crit)))
    finally:
        c.close()

# =====================================================================
print("\n(e) HTTP — viewer reads, non-admin cannot write")
# =====================================================================
with app.app_context():
    tech_id = uid_of("tech")           # maintenance_technician: maint_view, NO maint_admin
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    r = cl.get("/maintenance/workflow")
    ck("admin GET /maintenance/workflow == 200", r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    ck("page shows the lifecycle and the ladder",
       "mwf.lifecycle.title" in body and "mwf.ladder.title" in body)
    ck("page shows a seeded explanation", "available = on hand - reserved" in body)
    tok = None
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = cl.post("/maintenance/workflow/setting",
                data={"_csrf": tok, "key": "response_sla_hours", "value": "6"})
    ck("admin POST setting == 302", r.status_code == 302, r.status_code)
    with app.app_context():
        c = db()
        try:
            ck("admin POST actually stored the override",
               wf.num(c, "response_sla_hours") == 6.0, wf.num(c, "response_sla_hours"))
            aud = c.execute("SELECT * FROM mnt_audit WHERE action='wf_setting_set' "
                            "ORDER BY id DESC LIMIT 1").fetchone()
            ck("the change is audited with who / what / old / new",
               aud and aud["username"] == "admin" and aud["comment"] == "response_sla_hours"
               and aud["new_value"] == "6", dict(aud) if aud else None)
            wf.reset_setting(c, "response_sla_hours", ADMIN)
        finally:
            c.close()
    r = cl.post("/maintenance/workflow/text",
                data={"_csrf": tok, "kind": "doc", "key": "sla", "explanation": "x"})
    ck("admin POST text == 302", r.status_code == 302, r.status_code)
    with app.app_context():
        c = db()
        try:
            wf.reset_text(c, "doc", "sla", ADMIN)
        finally:
            c.close()

with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = tech_id
        s["ep"] = 0
    r = cl.get("/maintenance/workflow")
    ck("technician (maint_view) GET == 200", r.status_code == 200, r.status_code)
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = cl.post("/maintenance/workflow/setting",
                data={"_csrf": tok, "key": "response_sla_hours", "value": "999"})
    ck("technician POST setting == 403 (not 500)", r.status_code == 403, r.status_code)
    r = cl.post("/maintenance/workflow/text",
                data={"_csrf": tok, "kind": "doc", "key": "sla", "explanation": "hack"})
    ck("technician POST text == 403 (not 500)", r.status_code == 403, r.status_code)
    with app.app_context():
        c = db()
        try:
            ck("nothing the technician sent was stored",
               wf.num(c, "response_sla_hours") == 4.0
               and wf.texts(c, "doc")["sla"] == wf.DOC_TEXT["sla"])
        finally:
            c.close()
    r = cl.post("/maintenance/workflow/setting", data={"key": "response_sla_hours", "value": "1"})
    ck("POST without a CSRF token is refused", r.status_code in (302, 400), r.status_code)

# =====================================================================
print("\n(f) IDEMPOTENCY — create_and_seed x3 keeps admin edits")
# =====================================================================
with app.app_context():
    c = db()
    try:
        wf.set_text(c, "status", "closed", "MY OWN WORDS about closing.", ADMIN)
        wf.set_text(c, "doc", "costing", "MY OWN costing note.", ADMIN)
        wf.set_setting(c, "resolution_sla_hours", "36", ADMIN)
        before = {t: c.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"]
                  for t in ("mnt_stage_meta", "mnt_role_meta", "mnt_status_meta", "mnt_doc")}
        for _ in range(3):
            create_and_seed(c)
        after = {t: c.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"]
                 for t in ("mnt_stage_meta", "mnt_role_meta", "mnt_status_meta", "mnt_doc")}
        ck("no duplicate rows after 3 re-seeds", before == after, (before, after))
        ck("the admin's status explanation survived",
           wf.texts(c, "status")["closed"] == "MY OWN WORDS about closing.")
        ck("the admin's rules text survived",
           wf.texts(c, "doc")["costing"] == "MY OWN costing note.")
        ck("the admin's setting survived", wf.num(c, "resolution_sla_hours") == 36.0)
        ck("seeded text count == 35",
           sum(len(x) for x in (wf.STATUS_TEXT, wf.ROLE_TEXT, wf.STAGE_TEXT, wf.DOC_TEXT)) == 35)
        wf.reset_setting(c, "resolution_sla_hours", ADMIN)
    finally:
        c.close()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
