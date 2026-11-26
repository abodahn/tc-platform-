# -*- coding: utf-8 -*-
"""Item control rung: maintenance / chemicals & stones / sewing & cutting requests
are signed by that department's assigned item controller after the store.
    python -m app.approvals.tests_itemctl

Proves:
  * the rung sits after Warehouse and before Warehouse Manager, carrying the
    assigned person, for each of the three groups and their name variants;
    other departments never get it;
  * only the assigned person signs it (store, warehouse manager, buyer, proc
    admin and the other groups' controllers are refused with a translated
    reason); a delegate of the controller and the super admin (in place) can;
  * the controller is told, sees it in their queue, /procurement, /my-work and
    the "Waiting for" column, and can open it even as a plain requester or
    without proc_view; an unrelated requester cannot;
  * no controller / disabled controller / requester == controller -> the rung
    is left off with an audit note the request page shows;
  * one person cannot sign both Item control and Warehouse Manager;
  * requests already in the ladder keep their ladder; re-pricing keeps the rung
    in place; reassigning moves the waiting rungs to the new person;
  * the settings page is proc_admin only and CSRF protected.
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="itemctl_"))
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


from flask import g                                       # noqa: E402
# One app context for the whole run, so g (current_user's cache) would outlive a
# request. Dropped FIRST, before the item-control access hook reads the user.
app.before_request_funcs.setdefault(None, []).insert(0, lambda: g.pop("user", None) and None)

with app.app_context():
    from app.db import get_db
    from app.approvals import services as svc
    from app.approvals import constants as C
    from app.approvals import item_control as IC
    from app.approvals import messages as M
    from app.routes.approvals import APPROVE_REFUSALS
    from werkzeug.security import generate_password_hash

    conn = get_db()

    def mk(u, role, extra=None):
        conn.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, "
                     "created_at, extra_perms) VALUES (?,?,?,?,1,'2026-01-01',?)",
                     (u, generate_password_hash("x"), "Test " + u, role, extra))
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone())

    req = mk("ic.req", "normal_user")
    other = mk("ic.other", "normal_user")
    store = mk("ic.store", "storekeeper")
    whm = mk("ic.whm", "warehouse_manager")
    buyer = mk("ic.buyer", "purchasing_manager")
    padmin = mk("ic.padmin", "purchasing_manager", '["proc_admin"]')
    root = mk("ic.root", "super_admin")
    ctl_m = mk("ic.ctl_m", "maintenance_manager")       # proc_view, no proc_approve
    ctl_c = mk("ic.ctl_c", "department_manager")        # no proc_view at all
    ctl_s = mk("ic.ctl_s", "normal_user")
    ctl_x = mk("ic.ctl_x", "production_manager")
    whm2 = mk("ic.whm2", "warehouse_manager")
    deleg = mk("ic.deleg", "normal_user", '["proc_approve"]')

    def raise_pr(user, dept, title="t"):
        pid, _ = svc.create_pr({"title": title, "department": dept, "currency": "EGP"},
                               [{"item": "Bolt " + title, "qty": 2, "unit_price": 0}],
                               user, submit=True, priced=False)
        return pid

    def steps(pid):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pid,)).fetchall()]

    def stages(pid):
        return [s["stage"] for s in steps(pid)]

    def current(pid):
        cur = conn.execute("SELECT current_seq FROM pr_requests WHERE id=?", (pid,)).fetchone()[0]
        return next((s for s in steps(pid) if s["seq"] == cur), None)

    def events(pid, action):
        return [r["detail"] for r in conn.execute(
            "SELECT detail FROM pr_events WHERE pr_id=? AND action=?", (pid, action)).fetchall()]

    def client(u):
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = u["id"], 0, "tok"
        return cli

    print("\n--- schema and settings page ------------------------------------------")
    ok("three department groups seeded, nobody assigned",
       [r["group_key"] for r in IC.rows(conn)] == [k for k, _, _ in IC.GROUPS]
       and not any(r["username"] for r in IC.rows(conn)))
    ok("pr_steps has the assignee column",
       "assignee" in [c[0] for c in conn.execute("SELECT * FROM pr_steps WHERE 1=0").description])
    ok("rung sits between warehouse and warehouse_manager in LADDER_RANK",
       C.LADDER_RANK.index("warehouse") < C.LADDER_RANK.index("item_control")
       < C.LADDER_RANK.index("warehouse_manager") and C.DOAM_LEVEL["item_control"] == "L4")
    ok("Chemicals & Stones is in the department picker",
       "Chemicals & Stones" in svc.list_departments())
    for u, code in ((req, 403), (store, 403), (ctl_m, 403)):
        r = client(u).get("/procurement/item-control")
        ok("settings page refused to %s (%s)" % (u["username"], r.status_code), r.status_code == code)
    r = client(padmin).get("/procurement/item-control")
    page = r.get_data(as_text=True)
    ok("proc_admin opens the settings page", r.status_code == 200
       and all(("proc.itemctl.g." + k) in page for k, _, _ in IC.GROUPS))
    ok("super admin opens the settings page", client(root).get("/procurement/item-control").status_code == 200)
    r = client(padmin).post("/procurement/item-control/general_maintenance",
                            data={"username": "ic.ctl_m", "active": "1"})
    ok("save without the CSRF token is refused (400)", r.status_code == 400)
    r = client(req).post("/procurement/item-control/general_maintenance",
                         data={"_csrf": "tok", "username": "ic.req", "active": "1"})
    ok("save by a non-admin is refused (403)", r.status_code == 403)
    ok("...and changed nothing", not IC.group_for(conn, "General Maintenance")["username"])
    r = client(padmin).post("/procurement/item-control/general_maintenance",
                            data={"_csrf": "tok", "username": "ic.ctl_m", "active": "1",
                                  "aliases": "Mech. Workshop"})
    ok("proc_admin assigns General Maintenance (302)", r.status_code == 302
       and IC.group_for(conn, "General Maintenance")["username"] == "ic.ctl_m")
    ok("an extra department name matches the group",
       IC.group_for(conn, "mech workshop")["group_key"] == "general_maintenance")
    ok("an unknown user is refused", IC.save("sewing_cutting", "nobody.here", "", True, padmin)
       == (False, "bad_user"))
    ok("a name owned by another group is refused",
       IC.save("sewing_cutting", "ic.ctl_s", "Maintenance", True, padmin) == (False, "alias_taken"))
    ok("an unknown group is refused", IC.save("x", "ic.ctl_s", "", True, padmin)[1] == "unknown_group")
    ok("every settings message is translated", all(
        M.translate(m, "ar") != m and M.translate(m, "tr") != m for m in IC.MESSAGES))

    print("\n--- department matching ------------------------------------------------")
    for dept, key in (("General Maintenance", "general_maintenance"), ("maintenance ", "general_maintenance"),
                      ("Chemicals & Stones", "chemicals_stones"), ("chemical and stones", "chemicals_stones"),
                      ("Sewing & Cutting", "sewing_cutting"), ("CUT & SEW", "sewing_cutting"),
                      ("Sewing", "sewing_cutting"), ("Cutting", "sewing_cutting"),
                      ("الصيانة العامة", "general_maintenance")):
        g_ = IC.group_for(conn, dept)
        ok("%r -> %s" % (dept, key), g_ and g_["group_key"] == key)
    ok("'Production' and 'IT' match no group",
       IC.group_for(conn, "Production") is None and IC.group_for(conn, "IT") is None)

    print("\n--- in flight before the assignment keeps its ladder --------------------")
    IC.save("sewing_cutting", "", "", True, padmin)
    early = raise_pr(req, "Sewing", "early")
    ok("unassigned Sewing request: no item_control rung", "item_control" not in stages(early))
    ok("...with a 'no item controller' audit note",
       any("no item controller is assigned" in d for d in events(early, "item_control_skipped")))
    ok("...and the request page says so", "proc.itemctl.skip.unassigned"
       in client(root).get("/procurement/pr/%d" % early).get_data(as_text=True))
    before = stages(early)
    IC.save("sewing_cutting", "ic.ctl_s", "", True, padmin)
    ok("assigning later does not change a request already in the ladder", stages(early) == before)

    print("\n--- maintenance request: the rung, its signer and the refusals ----------")
    pm = raise_pr(req, "General Maintenance", "pump")
    st = steps(pm)
    ok("ladder: warehouse, item_control, warehouse_manager, ...",
       [s["stage"] for s in st][:3] == ["warehouse", "item_control", "warehouse_manager"])
    ic_step = next(s for s in st if s["stage"] == "item_control")
    ok("item_control rung carries ic.ctl_m and is a Review",
       ic_step["assignee"] == "ic.ctl_m" and ic_step["action_type"] == "review")
    ok("store cannot sign it early: the store signs first", current(pm)["stage"] == "warehouse")
    ok("the controller is not offered the store rung",
       svc.act_on_step(pm, ctl_m, "approve") == (False, "forbidden"))
    ok("store signs warehouse", svc.act_on_step(pm, store, "approve") == (True, "advanced"))
    ok("now at item_control", current(pm)["stage"] == "item_control")
    ok("controller is told 'New request to sign'", conn.execute(
        "SELECT 1 FROM notifications WHERE target_user='ic.ctl_m' AND title='New request to sign' "
        "AND link=?", ("/procurement/pr/%d" % pm,)).fetchone() is not None)
    ok("it is in the controller's queue",
       [p["id"] for p in svc.my_queue(ctl_m)] == [pm] and svc.my_queue(ctl_m)[0]["_stage"] == "item_control")
    ok("...not in the warehouse manager's", pm not in [p["id"] for p in svc.my_queue(whm)])
    pr_row = dict(conn.execute("SELECT * FROM pr_requests WHERE id=?", (pm,)).fetchone())
    ok("'Waiting for' names the controller",
       svc.waiting_on([pr_row])[pm]["who"] == ["Test ic.ctl_m"])
    for u in (whm, buyer, padmin, ctl_s, other, req):
        ok("%s cannot sign item_control" % u["username"],
           svc.act_on_step(pm, u, "approve")[1] in ("forbidden", "self_approval"))
    en = APPROVE_REFUSALS["forbidden"]
    ok("the refusal is translated (ar/tr)",
       M.translate(en, "ar") != en and M.translate(en, "tr") != en)
    cm = client(ctl_m)
    ok("controller's /procurement lists it", "PR-" in cm.get("/procurement/").get_data(as_text=True)
       and pr_row["pr_no"] in cm.get("/procurement/").get_data(as_text=True))
    ok("controller's /my-work lists it", pr_row["pr_no"] in cm.get("/my-work").get_data(as_text=True))
    r = cm.get("/procurement/pr/%d" % pm)
    ok("controller (plain requester scope) opens the request with the sign box",
       r.status_code == 200 and "prDecision" in r.get_data(as_text=True))
    ok("an unrelated requester cannot open it (404)",
       client(other).get("/procurement/pr/%d" % pm).status_code == 404)
    r = cm.post("/procurement/pr/%d/approve" % pm, data={"_csrf": "tok", "comment": "ok"})
    ok("controller signs through the page without proc_approve (302)", r.status_code == 302)
    ic_step = next(s for s in steps(pm) if s["stage"] == "item_control")
    ok("...signed by ic.ctl_m, now at warehouse_manager",
       ic_step["status"] == "approved" and ic_step["approver_user"] == "ic.ctl_m"
       and current(pm)["stage"] == "warehouse_manager")
    ok("controller still opens the request they signed",
       cm.get("/procurement/pr/%d" % pm).status_code == 200)
    ok("controller cannot sign the warehouse_manager rung",
       svc.act_on_step(pm, ctl_m, "approve") == (False, "forbidden"))
    r = cm.post("/procurement/pr/%d/approve" % pm, data={"_csrf": "tok"})
    ok("...nor through the page (no proc_approve granted for it: 403)", r.status_code == 403)
    ok("store/warehouse manager continue", svc.act_on_step(pm, whm, "approve") == (True, "advanced"))

    print("\n--- re-pricing keeps the rung in place ---------------------------------")
    pp = raise_pr(req, "Maintenance", "price")
    line = conn.execute("SELECT id FROM pr_items WHERE pr_id=?", (pp,)).fetchone()["id"]
    meta = {"vendor": "Vendor A", "currency": "EGP", "tax_rate": 0, "payment_condition": "Net 30"}
    good, msg = svc.price_pr(pp, {line: 900000}, meta, buyer)
    ok("priced at the store rung (%s)" % msg, good)
    s2 = stages(pp)
    ok("item_control still once, right after warehouse",
       s2.count("item_control") == 1 and s2.index("item_control") == s2.index("warehouse") + 1
       and s2.index("item_control") < s2.index("warehouse_manager"))

    print("\n--- controller without proc_view (chemicals) ---------------------------")
    IC.save("chemicals_stones", "ic.ctl_c", "", True, padmin)
    pc = raise_pr(req, "Chemical and Stones", "dye")
    ok("chemicals request gets ic.ctl_c", next(s for s in steps(pc) if s["stage"] == "item_control")
       ["assignee"] == "ic.ctl_c")
    svc.act_on_step(pc, store, "approve")
    cc = client(ctl_c)
    ok("controller without proc_view opens the request (200)",
       cc.get("/procurement/pr/%d" % pc).status_code == 200)
    ok("...but not an unrelated one (404)", cc.get("/procurement/pr/%d" % pm).status_code == 404)
    ok("...and sees it on /my-work", conn.execute("SELECT pr_no FROM pr_requests WHERE id=?",
                                                  (pc,)).fetchone()[0]
       in cc.get("/my-work").get_data(as_text=True))
    ok("a user with no proc_view and no assignment is still refused /procurement",
       client(mk("ic.dm2", "department_manager")).get("/procurement/").status_code == 403)
    conn.execute("UPDATE users SET sig_png='data:image/png;base64,AAAA' WHERE username='ic.ctl_c'")
    conn.commit()
    r = cc.post("/procurement/bulk-sign", data={"_csrf": "tok", "pr_id": str(pc)})
    ok("controller bulk-signs it (200)", r.status_code == 200
       and next(s for s in steps(pc) if s["stage"] == "item_control")["status"] == "approved")

    print("\n--- skipped: inactive controller, requester is the controller ------------")
    conn.execute("UPDATE users SET is_active=0 WHERE username='ic.ctl_c'")
    conn.commit()
    pi = raise_pr(req, "Chemicals & Stones", "inactive")
    ok("disabled controller -> no rung, audit note",
       "item_control" not in stages(pi) and any("disabled account" in d for d in
                                                 events(pi, "item_control_skipped")))
    ok("...request page shows the reason", "proc.itemctl.skip.inactive"
       in client(root).get("/procurement/pr/%d" % pi).get_data(as_text=True))
    conn.execute("UPDATE users SET is_active=1 WHERE username='ic.ctl_c'")
    conn.commit()
    ps = raise_pr(ctl_s, "Sewing & Cutting", "own")
    ok("controller's own request -> no rung, audit note",
       "item_control" not in stages(ps) and any("requester is the item controller" in d
                                                 for d in events(ps, "item_control_skipped")))
    ok("other departments never get the rung",
       "item_control" not in stages(raise_pr(req, "IT", "it"))
       and "item_control" not in stages(raise_pr(req, "Production", "prod")))
    IC.save("chemicals_stones", "ic.ctl_c", "", False, padmin)
    ok("group switched off -> skipped as unassigned",
       "item_control" not in stages(raise_pr(req, "Chemicals", "off")))

    print("\n--- dual role, delegation, super admin, reassignment --------------------")
    IC.save("sewing_cutting", "ic.whm2", "", True, padmin)
    pd = raise_pr(req, "Cutting", "dual")
    svc.act_on_step(pd, store, "approve")
    ok("warehouse manager as controller signs item_control",
       svc.act_on_step(pd, whm2, "approve") == (True, "advanced"))
    ok("...and is refused the warehouse_manager rung (dual_role)",
       svc.act_on_step(pd, whm2, "approve") == (False, "dual_role"))
    ok("the other warehouse manager signs it", svc.act_on_step(pd, whm, "approve") == (True, "advanced"))

    pg = raise_pr(req, "General Maintenance", "deleg")
    svc.act_on_step(pg, store, "approve")
    ok("before a delegation the delegate is refused",
       svc.act_on_step(pg, deleg, "approve") == (False, "forbidden"))
    ok("delegation from the controller saved",
       svc.add_delegation("ic.ctl_m", "ic.deleg", None, None, "leave", user=padmin)[0])
    ok("the controller's delegate signs item_control",
       svc.act_on_step(pg, deleg, "approve") == (True, "advanced"))

    pa = raise_pr(req, "General Maintenance", "root")
    svc.act_on_step(pa, store, "approve")
    ok("super admin signs item_control in place",
       svc.act_on_step(pa, root, "approve") == (True, "advanced"))
    ok("...recorded in place of the controller", "Test ic.ctl_m" in (next(
        s for s in steps(pa) if s["stage"] == "item_control")["in_place_of"] or ""))

    pr_ = raise_pr(req, "General Maintenance", "move")
    svc.act_on_step(pr_, store, "approve")
    ok("reassigning the group", IC.save("general_maintenance", "ic.ctl_x", "Mech. Workshop",
                                        True, padmin) == (True, "saved"))
    ok("...moves the waiting rung to the new person", next(
        s for s in steps(pr_) if s["stage"] == "item_control")["assignee"] == "ic.ctl_x")
    ok("...leaves signed rungs alone", next(
        s for s in steps(pm) if s["stage"] == "item_control")["approver_user"] == "ic.ctl_m")
    ok("...the new person is told and can sign", conn.execute(
        "SELECT 1 FROM notifications WHERE target_user='ic.ctl_x' AND link=?",
        ("/procurement/pr/%d" % pr_,)).fetchone() is not None
       and svc.act_on_step(pr_, ctl_x, "approve") == (True, "advanced"))
    ok("...the old one no longer can (new request)", (lambda p: (
        svc.act_on_step(p, store, "approve"),
        svc.act_on_step(p, ctl_m, "approve"))[1] == (False, "forbidden"))(
        raise_pr(req, "General Maintenance", "after")))
    ok("workflow page shows the rung for a controlled department",
       [s["stage"] for s in svc.workflow_view("General Maintenance")["stages"]][:3]
       == ["warehouse", "item_control", "warehouse_manager"])
    ok("stage label in all three languages", svc.labels("ar")["stage"]["item_control"] == "مراقبة الأصناف"
       and svc.labels("tr")["stage"]["item_control"] == "Malzeme Kontrolü"
       and C.stage_label("item_control") == "Item control")

conn.close()
print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
