# -*- coding: utf-8 -*-
"""HR Director rung: Human Resources requests are signed by the HR Director first.
    python -m app.approvals.tests_hr_head

Owner decision 2026-11-23. Proves:
  * an HR request starts at the hr_head rung; the store, its manager and the
    buyer cannot sign it; proc_admin cannot either;
  * the HR director is told "New request to sign" on submit, and the list's
    "Waiting for" column names them while the request sits there;
  * the HR director signs it and the request moves to the Warehouse rung;
  * a non-HR request never gets the rung; an HR request the HR director raised
    starts at the Warehouse rung (self-approval is absolute);
  * super_admin may sign it in place (audited 'signed_in_place'), a delegate
    of the HR director may sign it;
  * re-pricing keeps exactly one hr_head rung, first;
  * "hr", "HR ", "H.R" and the Arabic name all match.
"""
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="hr_head_"))
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)

import config                                             # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


with app.app_context():
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    from app.approvals import services as svc
    from app.approvals import constants as C
    from werkzeug.security import generate_password_hash

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    def mk(u, role, extra=None):
        conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                     "role, is_active, created_at, extra_perms) "
                     "VALUES (?,?,?,?,1,'2026-01-01',?)",
                     (u, generate_password_hash("x"), u.title(), role, extra))
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone())

    hr_user = mk("hrh_staff", "normal_user")
    hrd = mk("hrh_director", "hr_director")
    store = mk("hrh_store", "storekeeper")
    whm = mk("hrh_whm", "warehouse_manager")
    buyer = mk("hrh_buyer", "purchasing_manager")
    buyer_admin = mk("hrh_padmin", "purchasing_manager", '["proc_admin"]')
    root = mk("hrh_root", "super_admin")
    deleg = mk("hrh_deleg", "normal_user", '["proc_approve"]')

    def raise_pr(user, dept, title):
        pr_id, _ = svc.create_pr({"title": title, "department": dept, "currency": "EGP"},
                                 [{"item": "Binder " + title, "qty": 2, "unit_price": 0}],
                                 user, submit=True, priced=False)
        return pr_id

    def steps(pr_id):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pr_id,)).fetchall()]

    def current(pr_id):
        r = conn.execute("SELECT s.* FROM pr_steps s JOIN pr_requests p ON p.id=s.pr_id "
                         "WHERE s.pr_id=? AND s.seq=p.current_seq AND s.status='pending'",
                         (pr_id,)).fetchone()
        return dict(r) if r else None

    print("\n--- registration --------------------------------------------------")
    ok("hr_director is a platform role with view/create/approve",
       all(svc.user_has_permission(hrd, p) for p in ("proc_view", "proc_create", "proc_approve")))
    ok("hr_director holds the hr_head rung's DOAM level",
       C.holds_authority("hr_director", "hr_head"))
    ok("hr_head ranks first", C.ladder_rank("hr_head") < C.ladder_rank("warehouse"))
    ok("ladder_signer_health reports the rung as staffed",
       any(r["stage"] == "hr_head" and r["ok"] for r in svc.ladder_signer_health()))
    ok("Arabic / Turkish labels exist",
       svc.labels("ar")["stage"]["hr_head"] == "مدير الموارد البشرية"
       and svc.labels("tr")["stage"]["hr_head"] == "İK Direktörü")

    print("\n--- an HR request starts at the HR Director -----------------------")
    pr1 = raise_pr(hr_user, "Human Resources", "A")
    st = current(pr1)
    ok("first pending rung is hr_head (%s)" % (st or {}).get("stage"),
       st and st["stage"] == "hr_head" and st["seq"] == 1)
    ok("...followed by the warehouse rung", [s["stage"] for s in steps(pr1)][1] == "warehouse")
    ok("the HR director got 'New request to sign'", conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE target_user='hrh_director' "
        "AND title='New request to sign' AND link=?", ("/procurement/pr/%d" % pr1,)).fetchone()[0] == 1)
    ok("...and the storekeeper did not (yet)", conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE target_user='hrh_store' AND link=?",
        ("/procurement/pr/%d" % pr1,)).fetchone()[0] == 0)
    w = svc.waiting_on([p for p in svc.list_prs() if p["id"] == pr1]).get(pr1) or {}
    ok("waiting_on names the HR director (%s)" % w,
       w.get("kind") == "sign" and w.get("stage") == "HR Director"
       and w.get("who") == ["Hrh_Director"])
    for who, u in (("storekeeper", store), ("warehouse manager", whm),
                   ("purchasing", buyer), ("proc_admin", buyer_admin)):
        good, msg = svc.act_on_step(pr1, u, "approve")
        ok("%s cannot sign it (%s)" % (who, msg), not good and msg == "forbidden")
    good, msg = svc.act_on_step(pr1, hrd, "approve")
    ok("the HR director signs it (%s)" % msg, good)
    ok("current rung is now warehouse", (current(pr1) or {}).get("stage") == "warehouse")
    good, msg = svc.act_on_step(pr1, store, "approve")
    ok("the store signs the next rung as before (%s)" % msg, good)

    print("\n--- who does NOT get the rung --------------------------------------")
    pr2 = raise_pr(hr_user, "Production", "B")
    ok("a Production request has no hr_head rung",
       "hr_head" not in [s["stage"] for s in steps(pr2)]
       and current(pr2)["stage"] == "warehouse")
    pr3 = raise_pr(hrd, "Human Resources", "C")
    ok("an HR request raised by the HR director starts at warehouse",
       "hr_head" not in [s["stage"] for s in steps(pr3)]
       and current(pr3)["stage"] == "warehouse")

    print("\n--- department spellings -------------------------------------------")
    for dept in ("hr", "HR ", "H.R", "human  resources"):
        p = raise_pr(hr_user, dept, "D" + dept.strip())
        ok("department %r gets the rung" % dept, current(p)["stage"] == "hr_head")
    ok("the Arabic name matches", C.is_hr_department(" الموارد البشرية "))
    ok("a near miss does not", not C.is_hr_department("Human Resources Projects"))

    print("\n--- super_admin in place, delegation -------------------------------")
    pr4 = raise_pr(hr_user, "Human Resources", "E")
    good, msg = svc.act_on_step(pr4, root, "approve")
    ok("super_admin signs hr_head in place (%s)" % msg, good)
    ok("...audited as signed_in_place", conn.execute(
        "SELECT COUNT(*) FROM pr_events WHERE pr_id=? AND action='signed_in_place'",
        (pr4,)).fetchone()[0] == 1)
    pr5 = raise_pr(hr_user, "Human Resources", "F")
    ok("before a delegation the delegate is refused",
       svc.act_on_step(pr5, deleg, "approve")[1] == "forbidden")
    svc.add_delegation("hrh_director", "hrh_deleg", None, None, "leave cover", root)
    good, msg = svc.act_on_step(pr5, deleg, "approve")
    ok("the HR director's delegate signs it (%s)" % msg, good)

    print("\n--- CAPEX and re-pricing ---------------------------------------------")
    pr6, _ = svc.create_pr({"title": "G", "department": "Human Resources", "currency": "EGP",
                            "expenditure_kind": "capex"},
                           [{"item": "Badge printer", "qty": 1, "unit_price": 0}],
                           hr_user, submit=True, priced=False)
    ok("a CAPEX HR request also starts at hr_head", current(pr6)["stage"] == "hr_head")
    ok("...and it is a Review on the CAPEX ladder", svc.step_action_of(current(pr6)) == "review")

    pr7 = raise_pr(hr_user, "Human Resources", "H")
    line = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr7,)).fetchone()["id"]
    meta = {"vendor": "Vendor A", "currency": "EGP", "tax_rate": 0,
            "payment_condition": "Net 30"}

    def one_hr_first(pr_id):
        rows = steps(pr_id)
        hr = [r for r in rows if r["stage"] == "hr_head"]
        return len(hr) == 1 and hr[0]["seq"] == min(r["seq"] for r in rows)

    good, msg = svc.price_pr(pr7, {line: 5000}, meta, buyer)
    ok("priced while at the HR rung (%s)" % msg, good)
    ok("...exactly one hr_head, first, still current",
       one_hr_first(pr7) and current(pr7)["stage"] == "hr_head")
    for u in (hrd, store, whm):
        svc.act_on_step(pr7, u, "approve")
    ok("signed up to purchasing", current(pr7)["stage"] == "purchasing")
    good, msg = svc.price_pr(pr7, {line: 900000}, meta, buyer)
    ok("re-priced upwards (%s)" % msg, good)
    ok("...exactly one hr_head, first, still signed", one_hr_first(pr7) and conn.execute(
        "SELECT status FROM pr_steps WHERE pr_id=? AND stage='hr_head'",
        (pr7,)).fetchone()["status"] == "approved")

print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
