# -*- coding: utf-8 -*-
"""Item control, requester-account rule (owner, 2026-11-28).
    python -m app.approvals.tests_itemctl_req

Proves: a group's listed requester accounts send every request they raise to that
group's controller, whatever department it is filed under (Production, Warehouse,
none) — after the department's own controller, one rung per group and per person,
same skip rules; the list is matched case/space-insensitively, validated on save
(unknown or disabled accounts refused, nothing saved), kept when a save omits it;
backfill picks up the listed requester's open requests (before, at and past the
Warehouse Manager), idempotent, counted by the dry run / settings button; the
settings page shows the field; the new-request preview knows.
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
TMP = Path(tempfile.mkdtemp(prefix="itemctl_req_"))
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
    chemreq = mk("t.chemreq", "normal_user")
    store = mk("t.store", "storekeeper")
    whm = mk("t.whm", "warehouse_manager")
    padmin = mk("t.padmin", "purchasing_manager", '["proc_admin"]')
    ctl_gm = mk("t.ctl", "maintenance_manager")
    ctl_ch = mk("t.chm", "normal_user")
    mk("t.sew", "normal_user")
    mk("t.gone", "normal_user")
    conn.execute("UPDATE users SET is_active=0 WHERE username='t.gone'")

    conn.execute("INSERT INTO proc_items (code, name, unit, category_code, category_name, active) "
                 "VALUES ('00-CHM-0001','Caustic soda','Kg','CHM','Chemicals',1)")
    conn.execute("INSERT INTO proc_items (code, name, unit, category_code, category_name, active) "
                 "VALUES ('00-GEN-0002','Hex bolt','Pcs','GEN','General',1)")
    conn.commit()
    CHM_ID = conn.execute("SELECT id FROM proc_items WHERE code='00-CHM-0001'").fetchone()[0]
    GEN_ID = conn.execute("SELECT id FROM proc_items WHERE code='00-GEN-0002'").fetchone()[0]

    def raise_pr(user, dept, chm=False, title="t"):
        lines = [{"item": "Hex bolt", "item_id": GEN_ID, "qty": 2, "unit_price": 0}]
        if chm:
            lines.append({"item": "Caustic soda", "item_id": CHM_ID, "qty": 5, "unit_price": 0})
        pid, _ = svc.create_pr({"title": title, "department": dept, "currency": "EGP"},
                               lines, user, submit=True, priced=False)
        return pid

    def steps(pid):
        return [dict(r) for r in conn.execute(
            "SELECT * FROM pr_steps WHERE pr_id=? ORDER BY seq, id", (pid,)).fetchall()]

    def stages(pid):
        return [s["stage"] for s in steps(pid)]

    def ics(pid):
        return [(s["assignee"], s["ic_group"]) for s in steps(pid) if s["stage"] == "item_control"]

    def current(pid):
        cur = conn.execute("SELECT current_seq FROM pr_requests WHERE id=?", (pid,)).fetchone()[0]
        return next((s for s in steps(pid) if s["seq"] == cur), None)

    def events(pid, action):
        return [r["detail"] for r in conn.execute(
            "SELECT detail FROM pr_events WHERE pr_id=? AND action=?", (pid, action)).fetchall()]

    def saved(key):
        return conn.execute("SELECT requesters FROM proc_item_control WHERE group_key=?",
                            (key,)).fetchone()[0]

    def set_row(key, **kw):        # a direct write, bypassing save()'s backfill
        for col, val in kw.items():
            conn.execute("UPDATE proc_item_control SET %s=? WHERE group_key=?" % col, (val, key))
        conn.commit()
        IC._CTL_CACHE[0] = 0.0

    def client(u):
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = u["id"], 0, "tok"
        return cli

    def sign(pid, u):
        return svc.act_on_step(pid, u, "approve")

    CH = ("t.chm", "chemicals_stones")
    GM = ("t.ctl", "general_maintenance")
    SW = ("t.sew", "sewing_cutting")
    HEAD = ["warehouse", "item_control", "warehouse_manager"]
    HEAD2 = ["warehouse", "item_control", "item_control", "warehouse_manager"]

    print("\n--- schema --------------------------------------------------------------")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(proc_item_control)").fetchall()]
    ok("proc_item_control has a requesters column", "requesters" in cols)
    IC.ensure_schema(conn)
    ok("ensure_schema re-run: column and the three groups kept",
       "requesters" in [r[1] for r in conn.execute("PRAGMA table_info(proc_item_control)")]
       and len(IC.rows(conn)) == 3)

    print("\n--- open requests of the listed account, before the rule ---------------")
    o1 = raise_pr(chemreq, "Production", title="o1-at-store")
    o2 = raise_pr(chemreq, "Warehouse", title="o2-at-whm")
    sign(o2, store)
    o3 = raise_pr(chemreq, "Production", title="o3-past-whm")
    sign(o3, store)
    sign(o3, whm)
    o3_wait = current(o3)
    o4 = raise_pr(req, "Production", title="o4-other-requester")
    ok("no rung anywhere yet", not any(ics(p) for p in (o1, o2, o3, o4)))
    ok("o3 is past the manager", o3_wait["stage"] not in ("warehouse_manager", "item_control"))
    ok("assign the chemicals controller (no requester list yet)",
       IC.save("chemicals_stones", "t.chm", "", True, padmin) == (True, "saved"))
    ok("...nothing added: no CHM line, nobody listed", not any(ics(p) for p in (o1, o2, o3)))

    print("\n--- save validation -----------------------------------------------------")
    cp = client(padmin)
    r = cp.post("/procurement/item-control/chemicals_stones",
                data={"_csrf": "tok", "username": "t.chm", "aliases": "", "active": "1",
                      "requesters": "t.chemreq, t.nobody"})
    page = cp.get("/procurement/item-control").get_data(as_text=True)
    ok("unknown username refused, named in the flash", r.status_code == 302
       and "Requester accounts must be active users. Not found: t.nobody" in page)
    ok("...nothing saved, nothing added", not saved("chemicals_stones")
       and not any(ics(p) for p in (o1, o2, o3)))
    cp.post("/procurement/item-control/chemicals_stones",
            data={"_csrf": "tok", "username": "t.chm", "aliases": "", "active": "1",
                  "requesters": "TC\\user.name"})
    fl = re.search(r"window\.TC_FLASH = \[(.*?)\];", cp.get("/procurement/item-control")
                   .get_data(as_text=True))
    ok("a refused name with a backslash is not echoed raw into the flash script",
       fl and "Not found: TC?user.name" in fl.group(1) and "\\" not in fl.group(1))
    ok("disabled account refused",
       IC.save("chemicals_stones", "t.chm", "", True, padmin, requesters="t.gone")
       == (False, "bad_requesters:t.gone") and not saved("chemicals_stones"))
    msg = "Requester accounts must be active users. Not found: t.nobody"
    ok("the refusal is translated (ar, tr), the names kept", all(
        M.translate(msg, lang) != msg and M.translate(msg, lang).endswith(" t.nobody")
        for lang in ("ar", "tr")))

    print("\n--- dry run counts the listed account's open requests ------------------")
    set_row("chemicals_stones", requesters="t.chemreq")
    dry = {x["pr_id"]: x for x in IC.run_backfill("chemicals_stones", padmin, dry_run=True)}
    ok("dry run: o1, o2, o3 would get it; o4 (another requester) is not a candidate",
       {p for p, x in dry.items() if x["result"] == "would_add"} == {o1, o2, o3}
       and o4 not in dry)
    ok("...o2 and o3 at their current rung, o1 ahead of it",
       dry[o2]["current"] and dry[o3]["current"] and not dry[o1]["current"])
    ok("...dry run wrote nothing", not any(ics(p) for p in (o1, o2, o3)))
    page = cp.get("/procurement/item-control").get_data(as_text=True)
    m = re.search(r'id="g-chemicals_stones".*?proc\.itemctl\.bf\.btn">[^<]*</span> '
                  r'\(<b data-num="(\d+)">\1</b>\)', page, re.S)
    ok("settings page: 'Add to open requests (3)'", m and m.group(1) == "3")
    ok("...the field shows the list, an add-account picker per card (no datalist), keyed",
       re.search(r'name="requesters" value="t\.chemreq"', page)
       and page.count('class="ictl-req-add" data-for="r-') == 3 and 'list="ictl-users"' not in page
       and '<option value="t.chemreq">' in page
       and "proc.itemctl.req_hint" in page and "proc.itemctl.rule.6" in page)
    ok("the new keys have en/ar/tr text", all(
        all(('"%s"' % k) in (REPO / "app" / "static" / "i18n" / (lang + ".json"))
            .read_text(encoding="utf-8")
            for k in ("proc.itemctl.req", "proc.itemctl.req_add", "proc.itemctl.req_hint",
                      "proc.itemctl.rule.6"))
        for lang in ("en", "ar", "tr")))

    print("\n--- save (case / spaces / duplicates) runs the backfill -----------------")
    r = cp.post("/procurement/item-control/chemicals_stones",
                data={"_csrf": "tok", "username": "t.chm", "aliases": "", "active": "1",
                      "requesters": "  T.CHEMREQ ,\n t.chemreq ; "})
    ok("saved as the canonical username, once", r.status_code == 302
       and saved("chemicals_stones") == "t.chemreq")
    ok("o1 (Production, at the store): chemicals rung before the manager",
       stages(o1)[:3] == HEAD and ics(o1) == [CH] and current(o1)["stage"] == "warehouse")
    ok("o2 (Warehouse, AT the manager): now waits at the chemicals rung",
       current(o2)["stage"] == "item_control" and current(o2)["assignee"] == "t.chm"
       and stages(o2)[:3] == HEAD)
    ok("o3 (past the manager): rung at the current step, then it carries on",
       current(o3)["assignee"] == "t.chm" and current(o3)["seq"] == o3_wait["seq"]
       and sign(o3, ctl_ch) == (True, "advanced") and current(o3)["id"] == o3_wait["id"])
    ok("o4 (not listed): unchanged", not ics(o4))
    again = IC.run_backfill("chemicals_stones", padmin)
    ok("a second run adds nothing (idempotent)",
       not [x for x in again if x["result"] == "added"]
       and all(len(ics(p)) == 1 for p in (o1, o2, o3)))
    ok("a save without the field keeps the list",
       IC.save("chemicals_stones", "t.chm", "", True, padmin) == (True, "saved")
       and saved("chemicals_stones") == "t.chemreq")

    print("\n--- new submissions ------------------------------------------------------")
    a = raise_pr(chemreq, "Production", title="a-prod")
    ok("listed, Production: chemicals rung before the manager",
       stages(a)[:3] == HEAD and ics(a) == [CH])
    ok("listed, no department: chemicals rung", ics(raise_pr(chemreq, "", title="a-nodept")) == [CH])
    ok("listed AND a CHM line: still exactly one chemicals rung",
       ics(raise_pr(chemreq, "Production", chm=True, title="a-chm")) == [CH])
    ok("not listed: unchanged (no rung)", not ics(raise_pr(req, "Production", title="a-other")))
    ok("assign General Maintenance", IC.save("general_maintenance", "t.ctl", "", True, padmin)
       == (True, "saved"))
    b = raise_pr(chemreq, "General Maintenance", title="b-gm")
    ok("listed, General Maintenance: GM rung, then chemicals",
       ics(b) == [GM, CH] and stages(b)[:4] == HEAD2)
    ok("listed, GM, CHM line: GM then ONE chemicals rung",
       ics(raise_pr(chemreq, "General Maintenance", chm=True, title="b-gm-chm")) == [GM, CH])
    ok("...signed in order", sign(b, store) == (True, "advanced")
       and sign(b, ctl_ch) == (False, "forbidden") and sign(b, ctl_gm) == (True, "advanced")
       and sign(b, ctl_ch) == (True, "advanced") and current(b)["stage"] == "warehouse_manager")
    ok("preview: the listed account sees the step for every department",
       "*" in IC.preview_names("t.chemreq") and "*" not in IC.preview_names("t.req"))

    print("\n--- a second group lists the same account ---------------------------------")
    s1 = raise_pr(chemreq, "Production", title="s1-at-store")
    ok("s1: only the chemicals rung", ics(s1) == [CH])
    ok("sewing lists the account, its controller assigned",
       IC.save("sewing_cutting", "t.sew", "", True, padmin, requesters="t.chemreq")
       == (True, "saved"))
    ok("...backfill puts sewing AFTER the existing chemicals rung (group order)",
       ics(s1) == [CH, SW] and stages(s1)[:4] == HEAD2)
    ok("new GM request: GM, chemicals, sewing",
       ics(raise_pr(chemreq, "General Maintenance", title="s2")) == [GM, CH, SW])
    set_row("sewing_cutting", username="t.chm")
    ok("same person for chemicals and sewing -> one rung",
       ics(raise_pr(chemreq, "Production", title="s3")) == [CH])
    set_row("sewing_cutting", username="t.sew", requesters="")

    print("\n--- the controller listed as a requester of his own group --------------")
    ok("list the chemicals controller too",
       IC.save("chemicals_stones", "t.chm", "", True, padmin, requesters="t.chemreq, t.chm")
       == (True, "saved") and saved("chemicals_stones") == "t.chemreq, t.chm")
    own = raise_pr(ctl_ch, "Production", title="own")
    ok("his own request: rung skipped", not ics(own))
    ok("...with a note naming Chemicals & Stones", any(
        "requester is the item controller for Chemicals & Stones" in x
        for x in events(own, "item_control_skipped")))
    ok("...skip_info names the group",
       IC.skip_info(own) == [{"why": "requester", "group": "chemicals_stones"}])
    ok("...backfill reports it as 'requester', writes nothing",
       [(x["result"], x["why"]) for x in IC.run_backfill("chemicals_stones", padmin)
        if x["pr_id"] == own] == [("skipped", "requester")] and not ics(own))
    set_row("chemicals_stones", active=0)
    off = raise_pr(chemreq, "Production", title="off")
    ok("chemicals switched off: a listed request gets no rung, noted as unassigned",
       not ics(off) and IC.skip_info(off) == [{"why": "unassigned", "group": "chemicals_stones"}])

    print("\n--- three skipped groups: skip_info shows all three ----------------------")
    set_row("general_maintenance", username="")
    set_row("sewing_cutting", username="", requesters="t.chemreq")
    three = raise_pr(chemreq, "General Maintenance", title="three-skips")
    ok("GM, chemicals and sewing all skipped and all reported",
       not ics(three) and IC.skip_info(three) == [
           {"why": "unassigned", "group": "general_maintenance"},
           {"why": "unassigned", "group": "chemicals_stones"},
           {"why": "unassigned", "group": "sewing_cutting"}])
    set_row("sewing_cutting", requesters="")

    print("\n--- change requester to a listed account adds the rung --------------------")
    set_row("chemicals_stones", active=1)
    cr = raise_pr(req, "Production", title="cr-keyed-in")
    ok("keyed in by another account: no rung", not ics(cr))
    ok("change the requester to the listed account",
       svc.change_requester(cr, "t.chemreq", padmin, "raised for the chemicals team")[0])
    ok("...the chemicals rung appears before the manager",
       ics(cr) == [CH] and stages(cr)[:3] == HEAD and current(cr)["stage"] == "warehouse")
    ok("...once (a second backfill adds nothing)",
       not [x for x in IC.run_backfill("chemicals_stones", padmin) if x["pr_id"] == cr
            and x["result"] == "added"] and len(ics(cr)) == 1)

conn.close()
print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
