# -*- coding: utf-8 -*-
"""Item control, chemicals rule and open requests (2026-11-28).
    python -m app.approvals.tests_itemctl_chm

Proves:
  * the CHM token rule (catalogue code or item text; not inside a letter run);
  * a request from any department with a CHM line is signed by the Chemicals &
    Stones controller after the store and before the Warehouse Manager;
  * a General Maintenance request with a CHM line carries TWO Item control rungs,
    department first, each signable only by its own assignee (or their delegate),
    and moves through both; the same person for both groups -> one rung; the
    requester who is the chemicals controller -> that rung skipped with a note;
  * backfill() on open requests: waiting at the store (added ahead), waiting AT
    the Warehouse Manager (added at the current seq, request now waits at Item
    control, the manager's prompt cleared), past the Warehouse Manager / draft /
    approved / requester / controller-already-signed (left alone, reported),
    idempotent; the department rung is placed before an existing chemicals rung;
  * save() runs it; the settings page shows the dry-run count and runs it for
    proc_admin only (CSRF, 403 for others) with the skipped reasons listed;
  * a re-price after backfill keeps exactly one rung per controller in place;
  * reassigning a group never gives one person both rungs of a request.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:                                            # noqa: BLE001
    pass

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="itemctl_chm_"))
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
app.before_request_funcs.setdefault(None, []).insert(0, lambda: g.pop("user", None) and None)

with app.app_context():
    from app.db import get_db
    from app.approvals import services as svc
    from app.approvals import item_control as IC
    from app.approvals import messages as M
    from werkzeug.security import generate_password_hash

    conn = get_db()

    def mk(u, role, extra=None):
        conn.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, "
                     "created_at, extra_perms) VALUES (?,?,?,?,1,'2026-01-01',?)",
                     (u, generate_password_hash("x"), "Test " + u, role, extra))
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE username=?", (u,)).fetchone())

    req = mk("t.req", "normal_user")
    store = mk("t.store", "storekeeper")
    whm = mk("t.whm", "warehouse_manager")
    mk("t.whm2", "warehouse_manager")
    buyer = mk("t.buyer", "purchasing_manager")
    padmin = mk("t.padmin", "purchasing_manager", '["proc_admin"]')
    root = mk("t.root", "super_admin")
    ctl_gm = mk("t.ctl", "maintenance_manager")
    ctl_ch = mk("t.chm", "storekeeper")          # can sign a store rung (the "signed" case)
    deleg = mk("t.deleg", "normal_user", '["proc_approve"]')

    conn.execute("INSERT INTO proc_items (code, name, unit, category_code, category_name, active) "
                 "VALUES ('00-CHM-0001','Caustic soda','Kg','CHM','Chemicals',1)")
    conn.execute("INSERT INTO proc_items (code, name, unit, category_code, category_name, active) "
                 "VALUES ('00-GEN-0002','Hex bolt','Pcs','GEN','General',1)")
    conn.commit()
    CHM_ID = conn.execute("SELECT id FROM proc_items WHERE code='00-CHM-0001'").fetchone()[0]
    GEN_ID = conn.execute("SELECT id FROM proc_items WHERE code='00-GEN-0002'").fetchone()[0]

    def raise_pr(user, dept, chm=True, title="t", submit=True, free_text=None):
        lines = [{"item": "Hex bolt", "item_id": GEN_ID, "qty": 2, "unit_price": 0}]
        if chm:
            lines.append({"item": "Caustic soda", "item_id": CHM_ID, "qty": 5, "unit_price": 0})
        if free_text:
            lines.append({"item": free_text, "qty": 1, "unit_price": 0})
        pid, _ = svc.create_pr({"title": title, "department": dept, "currency": "EGP"},
                               lines, user, submit=submit, priced=False)
        return pid

    def steps(pid):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pid,)).fetchall()]

    def stages(pid):
        return [s["stage"] for s in steps(pid)]

    def ics(pid):
        return [(s["assignee"], s["ic_group"], s["status"]) for s in steps(pid)
                if s["stage"] == "item_control"]

    def current(pid):
        cur = conn.execute("SELECT current_seq FROM pr_requests WHERE id=?", (pid,)).fetchone()[0]
        return next((s for s in steps(pid) if s["seq"] == cur), None)

    def events(pid, action):
        return [r["detail"] for r in conn.execute(
            "SELECT detail FROM pr_events WHERE pr_id=? AND action=?", (pid, action)).fetchall()]

    def unread(user, pid):
        return conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE target_user=? AND link=? AND "
            "title='New request to sign' AND COALESCE(is_read,0)=0",
            (user, "/procurement/pr/%d" % pid)).fetchone()[0]

    def ic_told(user, pid):
        return conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE target_user=? AND link=? AND "
            "message LIKE '%Item control approval%'", (user, "/procurement/pr/%d" % pid)).fetchone()[0]

    def client(u):
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = u["id"], 0, "tok"
        return cli

    def sign(pid, u):
        return svc.act_on_step(pid, u, "approve")

    print("\n--- the CHM token rule -------------------------------------------------")
    for text, want in (("00-CHM-0001", True), ("CHM-12", True), ("CHM 5", True), ("chm", True),
                       ("00-chm-7", True), ("CHM5", True), ("(CHM)", True), ("ACHME", False),
                       ("CHMOD", False), ("XCHM-1", False), ("", False), (None, False),
                       ("Caustic soda", False)):
        ok("is_chm(%r) is %s" % (text, want), IC.is_chm(text) is want)

    print("\n--- open requests raised before anyone is assigned ----------------------")
    b1 = raise_pr(req, "Production", title="b1-at-store")
    b2 = raise_pr(req, "Production", title="b2-at-whm")
    sign(b2, store)
    b3 = raise_pr(req, "Production", title="b3-past-whm")
    sign(b3, store)
    sign(b3, whm)
    b4 = raise_pr(req, "Production", title="b4-draft", submit=False)
    b5 = raise_pr(req, "Production", title="b5-approved")
    conn.execute("UPDATE pr_requests SET status='approved' WHERE id=?", (b5,))
    conn.commit()
    b6 = raise_pr(req, "Production", title="b6-ctl-signed")
    ok("the chemicals controller signs b6's store rung", sign(b6, ctl_ch) == (True, "advanced"))
    b9 = raise_pr(ctl_ch, "Production", title="b9-own")
    b8 = raise_pr(req, "General Maintenance", chm=False, title="b8-gm-at-whm")
    sign(b8, store)
    b10 = raise_pr(req, "General Maintenance", title="b10-gm-chm")
    bft = raise_pr(req, "Production", chm=False, free_text="Acid 00-CHM-0007", title="bft")
    plain = raise_pr(req, "Production", chm=False, title="plain")
    ok("has_chm_items: catalogue CHM line", IC.has_chm_items(conn, b1))
    ok("has_chm_items: free-text line naming a CHM code", IC.has_chm_items(conn, bft))
    ok("has_chm_items: no CHM line", not IC.has_chm_items(conn, plain))
    ok("nothing assigned: no item_control rung anywhere",
       not any("item_control" in stages(p) for p in (b1, b2, b3, b6, b8, b10)))
    ok("b2 waits at warehouse_manager, its managers were told",
       current(b2)["stage"] == "warehouse_manager" and unread("t.whm", b2) >= 1)
    before = {p: stages(p) for p in (b1, b2, b3, b4, b5, b6, b9, plain)}

    print("\n--- settings page: dry-run count, refusal, run ---------------------------")
    # an assignment made before the CHM rule existed: no save, so no auto-run
    conn.execute("UPDATE proc_item_control SET username='t.chm' WHERE group_key='chemicals_stones'")
    conn.commit()
    dry = {r["pr_id"]: r for r in IC.run_backfill("chemicals_stones", padmin, dry_run=True)}
    ok("dry run: b1, b2, bft, b10 would get it",
       {p for p, r in dry.items() if r["result"] == "would_add"} == {b1, b2, bft, b10})
    ok("dry run: b3 past WHM, b6 signed, b9 requester",
       (dry[b3]["why"], dry[b6]["why"], dry[b9]["why"]) == ("past_whm", "signed", "requester"))
    ok("dry run: draft, approved and non-CHM requests are not even candidates",
       not {b4, b5, plain, b8} & set(dry))
    ok("dry run wrote nothing", all(stages(p) == s for p, s in before.items()))
    page = client(padmin).get("/procurement/item-control").get_data(as_text=True)
    m = re.search(r'id="g-chemicals_stones".*?proc\.itemctl\.bf\.btn">[^<]*</span> '
                  r'\(<b data-num>(\d+)</b>\)', page, re.S)
    ok("settings page: 'Add to open requests (4)' on Chemicals & Stones", m and m.group(1) == "4")
    ok("...no button for a group nobody may sign",
       not re.search(r'id="g-sewing_cutting".*?</section>', page, re.S).group(0).count("bf.btn"))
    for u in (req, buyer, ctl_ch):
        r = client(u).post("/procurement/item-control/chemicals_stones/backfill", data={"_csrf": "tok"})
        ok("backfill refused to %s (403)" % u["username"], r.status_code == 403)
    r = client(padmin).post("/procurement/item-control/chemicals_stones/backfill")
    ok("backfill without the CSRF token is refused (400)", r.status_code == 400)
    ok("...and nothing changed", "item_control" not in stages(b1))
    cp = client(padmin)
    r = cp.post("/procurement/item-control/chemicals_stones/backfill", data={"_csrf": "tok"})
    ok("proc_admin runs it (302 to the group)", r.status_code == 302
       and r.headers["Location"].endswith("#g-chemicals_stones"))
    page = cp.get("/procurement/item-control").get_data(as_text=True)
    ok("flash: added / skipped 4 / 3",
       "Item control added to open requests. Added / skipped: 4 / 3" in page)
    ok("skipped list names each request and reason", all(
        k in page for k in ("proc.itemctl.bf.why.past_whm", "proc.itemctl.bf.why.signed",
                            "proc.itemctl.bf.why.requester"))
       and all(conn.execute("SELECT pr_no FROM pr_requests WHERE id=?", (p,)).fetchone()[0] in page
               for p in (b3, b6, b9)))
    ok("the flash is translated", M.translate("Item control added to open requests. Added / "
                                              "skipped: 4 / 3", "ar").endswith(" 4 / 3")
       and M.translate("Item control added to open requests. Added / skipped: 4 / 3", "tr")
       != "Item control added to open requests. Added / skipped: 4 / 3")

    print("\n--- backfill: what it did -------------------------------------------------")
    st = stages(b1)
    ok("b1 (at the store): warehouse, item_control, warehouse_manager",
       st[:3] == ["warehouse", "item_control", "warehouse_manager"]
       and current(b1)["stage"] == "warehouse")
    ok("...the rung carries the chemicals controller and group, is a Review",
       ics(b1) == [("t.chm", "chemicals_stones", "pending")]
       and next(s for s in steps(b1) if s["stage"] == "item_control")["action_type"] == "review")
    ok("...audited naming the group", any("Chemicals & Stones" in d
                                          for d in events(b1, "item_control_added")))
    ok("...the controller is not told yet (not their turn)", ic_told("t.chm", b1) == 0)
    cur = current(b2)
    ok("b2 (AT the Warehouse Manager): now waits at item_control, same seq",
       cur["stage"] == "item_control" and cur["assignee"] == "t.chm"
       and stages(b2)[:3] == ["warehouse", "item_control", "warehouse_manager"])
    whm_step = next(s for s in steps(b2) if s["stage"] == "warehouse_manager")
    ok("...the manager's rung moved one seq on, clock reset",
       whm_step["seq"] == cur["seq"] + 1 and whm_step["activated_at"] is None)
    ok("...the manager's 'needs your signature' prompt is cleared", unread("t.whm", b2) == 0)
    ok("...the controller is told", unread("t.chm", b2) == 1)
    ok("...it is in the controller's queue, not the manager's",
       b2 in [p["id"] for p in svc.my_queue(ctl_ch)] and b2 not in [p["id"] for p in svc.my_queue(whm)])
    ok("...the manager cannot sign it now", sign(b2, whm) == (False, "forbidden"))
    ok("...the controller signs, then the manager", sign(b2, ctl_ch) == (True, "advanced")
       and current(b2)["stage"] == "warehouse_manager" and sign(b2, whm) == (True, "advanced"))
    ok("b3 (past the manager), b6, b9, draft, approved: untouched",
       all(stages(p) == before[p] for p in (b3, b4, b5, b6, b9)))
    ok("free-text CHM line got the rung too", "item_control" in stages(bft))
    again = IC.run_backfill("chemicals_stones", padmin)
    ok("a second run adds nothing (idempotent)",
       not [r for r in again if r["result"] == "added"]
       and stages(b1).count("item_control") == 1 and stages(b10).count("item_control") == 1)

    print("\n--- re-price after backfill -------------------------------------------------")
    line = conn.execute("SELECT id FROM pr_items WHERE pr_id=? ORDER BY id", (b1,)).fetchall()
    meta = {"vendor": "Vendor A", "currency": "EGP", "tax_rate": 0, "payment_condition": "Net 30"}
    good, msg = svc.price_pr(b1, {line[0]["id"]: 900000, line[1]["id"]: 10}, meta, buyer)
    s1 = stages(b1)
    ok("priced at the store rung (%s)" % msg, good)
    ok("...still one item_control rung, after warehouse and before warehouse_manager",
       s1.count("item_control") == 1 and s1.index("warehouse") < s1.index("item_control")
       < s1.index("warehouse_manager") and ics(b1) == [("t.chm", "chemicals_stones", "pending")])

    print("\n--- save() runs it: the department rung goes first ---------------------")
    ok("b10 has only the chemicals rung so far", ics(b10) == [("t.chm", "chemicals_stones", "pending")])
    ok("b8's page still says Item control was skipped", "proc.itemctl.skip.unassigned"
       in client(root).get("/procurement/pr/%d" % b8).get_data(as_text=True))
    ok("assign General Maintenance", IC.save("general_maintenance", "t.ctl", "", True, padmin)
       == (True, "saved"))
    ok("b8 (GM, at the manager) now waits at the GM controller",
       current(b8)["stage"] == "item_control" and current(b8)["assignee"] == "t.ctl"
       and unread("t.ctl", b8) == 1 and unread("t.whm", b8) == 0)
    ok("...and its page no longer says the step was skipped", "proc.itemctl.skip.unassigned"
       not in client(root).get("/procurement/pr/%d" % b8).get_data(as_text=True))
    ok("b10: department rung placed BEFORE the chemicals rung",
       ics(b10) == [("t.ctl", "general_maintenance", "pending"),
                    ("t.chm", "chemicals_stones", "pending")]
       and stages(b10)[:4] == ["warehouse", "item_control", "item_control", "warehouse_manager"])
    sign(b10, store)
    ok("b10: the chemicals controller cannot sign the GM rung",
       sign(b10, ctl_ch) == (False, "forbidden"))
    ok("...the GM controller signs it", sign(b10, ctl_gm) == (True, "advanced"))
    ok("...and cannot sign the chemicals rung", sign(b10, ctl_gm)[0] is False)
    ok("delegation from the chemicals controller",
       svc.add_delegation("t.chm", "t.deleg", None, None, "leave", user=padmin)[0])
    ok("...the delegate signs the chemicals rung", sign(b10, deleg) == (True, "advanced")
       and current(b10)["stage"] == "warehouse_manager")
    conn.execute("UPDATE proc_delegations SET is_active=0 WHERE from_user='t.chm'")  # back from leave
    conn.commit()

    print("\n--- new submissions ------------------------------------------------------")
    c = raise_pr(req, "Production", title="c-new")
    ok("Production request with a CHM line: chemicals rung before warehouse_manager",
       stages(c)[:3] == ["warehouse", "item_control", "warehouse_manager"]
       and ics(c) == [("t.chm", "chemicals_stones", "pending")])
    ok("a request without CHM in an uncontrolled department: no rung",
       "item_control" not in stages(raise_pr(req, "Production", chm=False, title="c-plain")))
    d = raise_pr(req, "General Maintenance", title="d-two")
    ok("GM request with a CHM line: TWO rungs, GM first",
       stages(d)[:4] == ["warehouse", "item_control", "item_control", "warehouse_manager"]
       and ics(d) == [("t.ctl", "general_maintenance", "pending"),
                      ("t.chm", "chemicals_stones", "pending")])
    ok("...distinct seqs, both Reviews", len({s["seq"] for s in steps(d)}) == len(steps(d))
       and all(s["action_type"] == "review" for s in steps(d) if s["stage"] == "item_control"))
    ok("store signs", sign(d, store) == (True, "advanced"))
    ok("...the GM controller is told, the chemicals one not yet",
       unread("t.ctl", d) == 1 and ic_told("t.chm", d) == 0)
    ok("...chemicals controller refused the first rung", sign(d, ctl_ch) == (False, "forbidden"))
    ok("...GM controller signs", sign(d, ctl_gm) == (True, "advanced"))
    ok("...now the chemicals controller is told",
       ic_told("t.chm", d) == 1)
    ok("...it is in their queue, not the GM controller's",
       d in [p["id"] for p in svc.my_queue(ctl_ch)] and d not in [p["id"] for p in svc.my_queue(ctl_gm)])
    ok("...the super admin may sign it in place", svc.can_act_step(root, current(d)))
    ok("...chemicals controller signs, then the manager",
       sign(d, ctl_ch) == (True, "advanced") and current(d)["stage"] == "warehouse_manager"
       and sign(d, whm) == (True, "advanced"))
    page = client(root).get("/procurement/pr/%d" % d).get_data(as_text=True)
    ok("request page shows both signatures", page.count("Test t.ctl") >= 1
       and page.count("Test t.chm") >= 1)

    own = raise_pr(ctl_ch, "General Maintenance", title="own")
    ok("chemicals controller raises a GM request with CHM: only the GM rung",
       ics(own) == [("t.ctl", "general_maintenance", "pending")])
    ok("...with a note naming Chemicals & Stones", any(
        "requester is the item controller for Chemicals & Stones" in x
        for x in events(own, "item_control_skipped")))
    ok("...shown on the request page with the group",
       IC.skip_info(own) == [{"why": "requester", "group": "chemicals_stones"}]
       and "proc.itemctl.skip.requester" in client(root).get("/procurement/pr/%d" % own)
       .get_data(as_text=True))

    print("\n--- one person for both groups ------------------------------------------")
    keep = raise_pr(req, "General Maintenance", title="keep")      # t.ctl + t.chm pending
    ok("reassign chemicals to the GM controller", IC.save("chemicals_stones", "t.ctl", "", True,
                                                          padmin) == (True, "saved"))
    ok("...a request where they already hold the GM rung keeps its chemicals signer",
       ics(keep) == [("t.ctl", "general_maintenance", "pending"),
                     ("t.chm", "chemicals_stones", "pending")]
       and any("already hold the other Item control step" in x
               for x in events(keep, "item_control_kept")))
    ok("...a chemicals-only request moves to them",
       ics(c) == [("t.ctl", "chemicals_stones", "pending")])
    same = raise_pr(req, "General Maintenance", title="same")
    ok("same person for both groups -> ONE rung", ics(same) == [("t.ctl", "general_maintenance",
                                                                 "pending")])
    ok("...and backfill does not add a second one", not [
        r for r in IC.run_backfill("chemicals_stones", padmin) if r["pr_id"] == same])
    ok("reassign chemicals back", IC.save("chemicals_stones", "t.chm", "", True, padmin)
       == (True, "saved"))

conn.close()
print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
