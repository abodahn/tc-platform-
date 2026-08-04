# -*- coding: utf-8 -*-
"""
New-item request self-test — throwaway database, SYNTHETIC data only.
    python app/approvals/tests_item_request.py

THIS REPO IS PUBLIC. The real ERP item master carries confidential supplier cost
prices, so nothing here reads it: every code, name and category below is made up
at runtime and lives only in a tempfile database.

What it proves, by execution:
  1  THE REGRESSION: a plain free-text PR line still creates, submits and walks
     the whole approval ladder to approved. Requesting an item is an EXTRA path,
     never a gate.
  2  happy path: request -> Purchasing approve with an ERP code -> proc_items
     gains EXACTLY one row -> the PR line's item_id is now set -> the requester
     was notified.
  3  a duplicate CODE is refused with the existing item shown and NOTHING is
     written; a similar NAME warns but is still allowed (a judgement call).
  4  reject: reason recorded, requester notified, PR line untouched and still
     free text, PR status unchanged.
  5  permissions, on RAW status codes: a requester cannot open the queue or POST
     an approval, and a refused POST writes nothing.
  6  THE COMMERCIAL LOCKOUT: no route added here returns a cost to a requester,
     a POSTed price field is ignored, and _can_price is still hardcoded False.
  7  re-import safety: an ERP file containing the platform-created code UPDATES
     that row instead of creating a second one.
  8  boot: create_and_seed x3 adds no rows and leaves proc_item_requests empty.
  9  the pages render 200 in en / ar / tr with every data-i18n key resolvable.
"""
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="nir_"))
os.chdir(TMP)
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                      # noqa: E402
from app.approvals import services as svc                       # noqa: E402
from app.approvals import item_requests as ir                   # noqa: E402
from app.approvals.catalogue import upsert_items                # noqa: E402

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


with app.app_context():
    from app.db import get_db
    from flask import g

    conn = get_db()

    # Flask reuses ONE app context for every test request here, and
    # auth.current_user() caches per context — drop it before each request or
    # the second client is served the first client's user.
    def get(cl, url):
        g.pop("user", None)
        return cl.get(url)

    def post(cl, url, **kw):
        g.pop("user", None)
        return cl.post(url, **kw)

    def csrf_of(cl):
        get(cl, "/procurement/")
        with cl.session_transaction() as s:
            return s.get("_csrf_token") or ""

    def n_items():
        return conn.execute("SELECT COUNT(*) c FROM proc_items").fetchone()["c"]

    def n_reqs():
        return conn.execute("SELECT COUNT(*) c FROM proc_item_requests").fetchone()["c"]

    # ----------------------------------------------------------------------
    # Users. id=1 is the seeded admin (holds everything, so it plays
    # Purchasing); 900 is a plain requester with proc_create only.
    # ----------------------------------------------------------------------
    conn.execute("INSERT OR IGNORE INTO users (id, username, full_name, role, "
                 "is_active, password_hash) VALUES (?,?,?,?,?,?)",
                 (900, "plainuser", "Plain User", "normal_user", 1, "x"))
    conn.execute("UPDATE users SET extra_perms=? WHERE id=900",
                 (json.dumps(["proc_view", "proc_create"]),))
    conn.commit()
    admin = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
    plainu = dict(conn.execute("SELECT * FROM users WHERE id=900").fetchone())

    buyer = app.test_client()
    with buyer.session_transaction() as s:
        s["uid"], s["ep"] = 1, 0
    BCSRF = csrf_of(buyer)

    plain = app.test_client()
    with plain.session_transaction() as s:
        s["uid"], s["ep"] = 900, 0
    PCSRF = csrf_of(plain)

    ok("the requester really does hold proc_create but NOT proc_purchasing",
       get(plain, "/procurement/new").status_code == 200)

    # A tiny SYNTHETIC catalogue, so the duplicate-code and similar-name checks
    # have something real to hit. Made-up codes, no costs on file.
    upsert_items(conn, [
        {"code": "SYN-100", "name": "Blue widget", "unit": "Pcs",
         "category_code": "01", "category_name": "Widgets",
         "cost_price": 0, "has_cost": 0},
        {"code": "SYN-200", "name": "Safety goggles clear", "unit": "Pcs",
         "category_code": "02", "category_name": "Safety",
         "cost_price": 0, "has_cost": 0},
    ], {"username": "tester"}, source="synthetic")
    ok("synthetic catalogue seeded (2 items)", n_items() == 2)

    print("\n--- 1. THE REGRESSION: a free-text line still runs the ladder ---")
    requester = {"id": 900, "username": "plainuser", "full_name": "Plain User"}
    pr_id, pr_no = svc.create_pr(
        {"title": "Off-catalogue request", "department": "Production",
         "currency": "EGP", "notes": ""},
        [{"item": "Hydraulic seal kit ZX", "description": "typed by hand",
          "unit": "Pcs", "qty": 2, "current_stock": 0}],
        requester, ip="127.0.0.1", submit=False)
    ok(f"a free-text PR is created ({pr_no})", bool(pr_id))
    line = conn.execute("SELECT id, item, item_id FROM pr_items WHERE pr_id=?",
                        (pr_id,)).fetchone()
    ok("its line carries NO catalogue link, exactly as before",
       line["item_id"] is None and line["item"] == "Hydraulic seal kit ZX")
    sub_ok, sub_msg = svc.submit_pr(pr_id, requester, ip="127.0.0.1")
    ok(f"it submits into the ladder ({sub_msg or 'ok'})", sub_ok)
    guard = 0
    while guard < 12:
        guard += 1
        pr = conn.execute("SELECT status, pricing_status FROM pr_requests WHERE id=?",
                          (pr_id,)).fetchone()
        if pr["status"] != "pending":
            break
        if (pr["pricing_status"] or "priced") == "unpriced":
            svc.price_pr(pr_id,
                         {str(r["id"]): 40.0 for r in conn.execute(
                             "SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchall()},
                         {"currency": "EGP", "vendor": "Delta Industrial Supplies"},
                         admin, ip="127.0.0.1")
        a_ok, a_msg = svc.act_on_step(pr_id, admin, "approve", "ok", ip="127.0.0.1")
        if not a_ok:
            print("      (ladder stopped: " + str(a_msg) + ")")
            break
    final = conn.execute("SELECT status FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    ok(f"the whole ladder still runs on an off-catalogue line -> {final['status']}",
       final["status"] in ("approved", "po_issued"))
    ok("...and it never needed a new-item request to exist", n_reqs() == 0)

    print("\n--- 2. happy path: request -> approve with the ERP code ---------")
    r = get(plain, "/procurement/item-request?name=Hydraulic%20seal%20kit%20ZX")
    ok("requester GET /procurement/item-request -> 200 (raw)", r.status_code == 200)
    ask_page = r.get_data(as_text=True)
    ok("the form is prefilled with the text they typed on the PR line",
       "Hydraulic seal kit ZX" in ask_page)
    ok("the form has NO price/cost input of any kind",
       not re.search(r'<(input|select|textarea)[^>]*name="[^"]*(price|cost|amount|total)',
                     ask_page, re.I))

    before_items, before_reqs = n_items(), n_reqs()
    r = post(plain, "/procurement/item-request", data={
        "_csrf": PCSRF, "name": "Hydraulic seal kit ZX", "unit": "Pcs",
        "category_code": "01", "reason": "Press #4 leaks; the kit is not in the list.",
        # A price POSTed by hand: there is no column for it, so it is ignored.
        "unit_price": "999", "cost_price": "999", "price": "999"})
    ok("requester POST /procurement/item-request -> 302 (raw)", r.status_code == 302)
    ok("exactly one request row was created", n_reqs() == before_reqs + 1)
    ok("and NO catalogue item yet — Purchasing decide that", n_items() == before_items)
    req = dict(conn.execute("SELECT * FROM proc_item_requests ORDER BY id DESC "
                            "LIMIT 1").fetchone())
    req_id = req["id"]
    ok("the request stores name / unit / category / reason / requester",
       req["name"] == "Hydraulic seal kit ZX" and req["unit"] == "Pcs"
       and req["category_code"] == "01" and req["requested_by"] == "plainuser"
       and "Press #4" in (req["reason"] or ""))
    ok("it starts pending, with no code assigned", req["status"] == "pending"
       and not req["assigned_code"])
    cols = {c.lower() for c in req.keys()}
    ok(f"proc_item_requests has NO price column at all {sorted(cols)}",
       not any(w in c for c in cols for w in ("price", "cost", "amount", "total")))
    ok("the POSTed 999 landed nowhere",
       conn.execute("SELECT COUNT(*) c FROM proc_item_requests WHERE reason LIKE '%999%'"
                    ).fetchone()["c"] == 0)
    ok("Purchasing were notified that something is waiting",
       conn.execute("SELECT COUNT(*) c FROM notifications WHERE module='procurement' "
                    "AND title='New item requested'").fetchone()["c"] >= 1)

    # ---- approve, with the ERP code typed by the approver --------------
    before_items = n_items()
    r = post(buyer, f"/procurement/item-requests/{req_id}/approve",
             data={"_csrf": BCSRF, "code": "SYN-300"})
    ok("Purchasing POST approve -> 302 (raw)", r.status_code == 302)
    ok("proc_items gained EXACTLY one row", n_items() == before_items + 1)
    new_item = dict(conn.execute("SELECT * FROM proc_items WHERE code='SYN-300'"
                                 ).fetchone())
    ok("the new row carries the code the approver TYPED (nothing auto-generated)",
       new_item["code"] == "SYN-300" and new_item["name"] == "Hydraulic seal kit ZX")
    ok("it is marked platform-created so the next ERP import can tell it apart",
       new_item["source"] == ir.SOURCE)
    ok("LOCKOUT: it is born with NO price on file (has_cost=0, cost 0)",
       int(new_item["has_cost"]) == 0 and float(new_item["cost_price"] or 0) == 0)
    ok("the PR line that raised it is now LINKED to the catalogue row",
       conn.execute("SELECT item_id FROM pr_items WHERE id=?",
                    (line["id"],)).fetchone()["item_id"] == new_item["id"])
    ok("the request is closed as approved, carrying the code and the item id",
       conn.execute("SELECT status, assigned_code, item_id, decided_by FROM "
                    "proc_item_requests WHERE id=?", (req_id,)).fetchone()["status"]
       == "approved")
    ok("the requester was notified",
       conn.execute("SELECT COUNT(*) c FROM notifications WHERE target_user='plainuser' "
                    "AND title='New item approved'").fetchone()["c"] == 1)
    ok("the approved PR is otherwise untouched — same status as before",
       conn.execute("SELECT status FROM pr_requests WHERE id=?",
                    (pr_id,)).fetchone()["status"] == final["status"])

    print("\n--- 3. duplicate CODE refused / similar NAME only warns ----------")
    dup_id, _ = ir.create_item_request(
        {"name": "Bolt M6 stainless", "unit": "Pcs", "reason": "for the guards"},
        requester)
    before_items, before_reqs = n_items(), n_reqs()
    r = post(buyer, f"/procurement/item-requests/{dup_id}/approve",
             data={"_csrf": BCSRF, "code": "SYN-100"})     # already in the catalogue
    dup_body = r.get_data(as_text=True)
    ok("approving onto an existing code -> 200 (raw), not a redirect",
       r.status_code == 200)
    ok("the screen says the code is taken", "nir.dupe_code" in dup_body)
    ok("...and SHOWS the item that already holds it",
       "SYN-100" in dup_body and "Blue widget" in dup_body)
    ok("NOTHING was written by the refusal (no item, no decision)",
       n_items() == before_items
       and conn.execute("SELECT status FROM proc_item_requests WHERE id=?",
                        (dup_id,)).fetchone()["status"] == "pending")
    okc, msgc, _x = ir.approve_item_request(dup_id, "   ", admin)
    ok("an empty code is refused too, and writes nothing",
       (not okc) and msgc == "code_required" and n_items() == before_items)
    okc, msgc, exc = ir.approve_item_request(dup_id, "  syn-100  ", admin)
    ok("a code that differs only in CASE (or padding) is refused as a duplicate",
       (not okc) and msgc == "duplicate_code" and n_items() == before_items)
    ok("...and the refusal names the code as it is ON FILE, not as it was typed",
       (exc or {}).get("code") == "SYN-100")
    ok("the existing item is NOT touched by any refusal",
       dict(conn.execute("SELECT name, source FROM proc_items WHERE code='SYN-100'"
                         ).fetchone())["name"] == "Blue widget")

    # similar NAME: "Safety goggles clear" is on file as SYN-200.
    sim_id, _ = ir.create_item_request(
        {"name": "Goggles safety clear lens", "unit": "Pcs",
         "reason": "line 3 needs eye protection"}, requester)
    queue = get(buyer, "/procurement/item-requests").get_data(as_text=True)
    ok("the queue WARNS about the near-duplicate before the decision",
       "nir.similar" in queue and "SYN-200" in queue)
    near = ir.similar_items(conn, "Goggles safety clear lens")
    ok("the similar-name search finds it by shared words",
       any(s["code"] == "SYN-200" for s in near))
    ok("the similar list carries NO cost field",
       all(set(s) == {"id", "code", "name", "unit"} for s in near))
    before_items = n_items()
    ok_s, msg_s, _e = ir.approve_item_request(sim_id, "SYN-201", admin)
    ok("a similar name is a WARNING, not a block — the approval goes through",
       ok_s and msg_s == "approved_similar" and n_items() == before_items + 1)
    ok("...and the near-duplicates are recorded on the request for the audit trail",
       "SYN-200" in (conn.execute("SELECT decision_note FROM proc_item_requests "
                                  "WHERE id=?", (sim_id,)).fetchone()["decision_note"] or ""))

    print("\n--- 3b. two people asking for the same thing is ONE decision -----")
    a_id, _ = ir.create_item_request({"name": "Teflon tape 12mm", "unit": "Roll",
                                      "reason": "maintenance"}, requester)
    b_id, _ = ir.create_item_request({"name": "  teflon TAPE 12mm ", "unit": "Roll",
                                      "reason": "utilities"},
                                     {"username": "someone_else"})
    rows = {r["id"]: r for r in ir.list_requests("pending")}
    ok("the queue flags both rows as also-requested, instead of hiding one",
       rows[a_id]["also"] == 1 and rows[b_id]["also"] == 1)

    print("\n--- 3d. the text link survives the whitespace people really type --")
    # The picker prefills the request with the EXACT text on the line, and people
    # type double spaces and tabs. _norm collapses those; SQL TRIM does not — so
    # this used to link nothing at all and the line stayed off-catalogue for ever.
    ws_pr, _ = svc.create_pr(
        {"title": "Whitespace line", "department": "Production", "currency": "EGP"},
        [{"item": "Bolt  M6  wide gap", "unit": "Pcs", "qty": 1}],
        requester, ip="127.0.0.1", submit=False)
    ws_id, _ = ir.create_item_request({"name": "Bolt  M6  wide gap", "unit": "Pcs",
                                       "reason": "guards"}, requester)
    ir.approve_item_request(ws_id, "SYN-410", admin)
    ws_item = conn.execute("SELECT id FROM proc_items WHERE code='SYN-410'").fetchone()
    ok("a line typed with double spaces is still linked on approval",
       conn.execute("SELECT item_id FROM pr_items WHERE pr_id=?",
                    (ws_pr,)).fetchone()["item_id"] == ws_item["id"])

    print("\n--- 4. reject: reason recorded, PR line untouched ----------------")
    rej_id, _ = ir.create_item_request(
        {"name": "Gold-plated spanner", "unit": "Pcs", "reason": "why not"}, requester)
    pr2_id, pr2_no = svc.create_pr(
        {"title": "Second off-catalogue request", "department": "Production",
         "currency": "EGP"},
        [{"item": "Gold-plated spanner", "unit": "Pcs", "qty": 1}],
        requester, ip="127.0.0.1", submit=False)
    svc.submit_pr(pr2_id, requester, ip="127.0.0.1")
    pr2_before = conn.execute("SELECT status, current_seq FROM pr_requests WHERE id=?",
                              (pr2_id,)).fetchone()
    before_items = n_items()
    okr, msgr = ir.reject_item_request(rej_id, "", admin)
    ok("a rejection with no reason is refused", (not okr) and msgr == "reason_required")
    okr, msgr = ir.reject_item_request(rej_id, "Use SYN-100, same part.", admin)
    rej = dict(conn.execute("SELECT * FROM proc_item_requests WHERE id=?",
                            (rej_id,)).fetchone())
    ok("reject succeeds and records the reason", okr and rej["status"] == "rejected"
       and rej["decision_note"] == "Use SYN-100, same part.")
    ok("no catalogue item was created", n_items() == before_items)
    ok("the requester was notified",
       conn.execute("SELECT COUNT(*) c FROM notifications WHERE target_user='plainuser' "
                    "AND title='New item not added'").fetchone()["c"] == 1)
    l2 = conn.execute("SELECT item, item_id FROM pr_items WHERE pr_id=?",
                      (pr2_id,)).fetchone()
    ok("the PR line is untouched and STILL free text",
       l2["item_id"] is None and l2["item"] == "Gold-plated spanner")
    pr2_after = conn.execute("SELECT status, current_seq FROM pr_requests WHERE id=?",
                             (pr2_id,)).fetchone()
    ok(f"the PR itself is unchanged ({pr2_no} still {pr2_after['status']})",
       pr2_after["status"] == pr2_before["status"]
       and pr2_after["current_seq"] == pr2_before["current_seq"])
    a_ok2, _ = svc.act_on_step(pr2_id, admin, "approve", "still fine", ip="127.0.0.1")
    ok("...and it still walks the ladder after the rejection", a_ok2)

    print("\n--- 4b. a request whose PR vanishes leaves no broken row ---------")
    ghost_id, _ = ir.create_item_request(
        {"name": "Ghost bracket", "unit": "Pcs", "reason": "x",
         "pr_id": "999999", "line_no": "3"}, requester)
    okg, msgg, _e = ir.approve_item_request(ghost_id, "SYN-900", admin)
    ok("approving a request whose PR no longer exists still succeeds", okg)
    ok("the request row is complete, not half-written",
       conn.execute("SELECT status, assigned_code, item_id FROM proc_item_requests "
                    "WHERE id=?", (ghost_id,)).fetchone()["assigned_code"] == "SYN-900")
    ok("re-deciding an already-decided request is refused",
       ir.approve_item_request(ghost_id, "SYN-901", admin)[1] == "not_pending"
       and ir.reject_item_request(ghost_id, "no", admin)[1] == "not_pending")

    print("\n--- 5. permissions, on RAW status codes --------------------------")
    before_items, before_reqs = n_items(), n_reqs()
    pend_id, _ = ir.create_item_request({"name": "Perm probe", "unit": "Pcs",
                                         "reason": "x"}, requester)
    before_items = n_items()
    r = get(plain, "/procurement/item-requests")
    ok("requester GET the approval queue -> raw 403", r.status_code == 403)
    r = post(plain, f"/procurement/item-requests/{pend_id}/approve",
             data={"_csrf": PCSRF, "code": "SYN-999"})
    ok("requester POST an approval -> raw 403", r.status_code == 403)
    r = post(plain, f"/procurement/item-requests/{pend_id}/reject",
             data={"_csrf": PCSRF, "note": "nope"})
    ok("requester POST a rejection -> raw 403", r.status_code == 403)
    ok("a non-purchasing user creates NO catalogue item this way",
       n_items() == before_items
       and conn.execute("SELECT COUNT(*) c FROM proc_items WHERE code='SYN-999'"
                        ).fetchone()["c"] == 0)
    ok("and the request is still pending — the refused POSTs wrote NOTHING",
       conn.execute("SELECT status FROM proc_item_requests WHERE id=?",
                    (pend_id,)).fetchone()["status"] == "pending")
    ok("Purchasing CAN open the queue -> raw 200",
       get(buyer, "/procurement/item-requests").status_code == 200)

    print("\n--- 6. THE COMMERCIAL LOCKOUT -----------------------------------")
    from app.routes import approvals as approutes
    ok("_can_price is still hardcoded False", approutes._can_price() is False)
    conn.execute("UPDATE proc_items SET cost_price=1234.56, has_cost=1 WHERE code='SYN-300'")
    conn.commit()
    leak_urls = ["/procurement/item-request",
                 "/procurement/item-request?name=Hydraulic%20seal%20kit%20ZX",
                 "/procurement/new"]
    for url in leak_urls:
        body = get(plain, url).get_data(as_text=True)
        ok(f"no cost reaches the requester on {url}",
           "1234.56" not in body and "1234.5" not in body)
    ok("the requester's own page never renders a catalogue cost label",
       "cat.last_cost" not in get(plain, "/procurement/item-request").get_data(as_text=True))
    src = (REPO / "app" / "approvals" / "item_requests.py").read_text(encoding="utf-8")
    sql = re.findall(r'"([^"]*(?:SELECT|UPDATE)[^"]*)"', src, re.I)
    ok(f"no SQL in item_requests.py ever reads a cost {[q for q in sql if 'cost' in q]}",
       not any(re.search(r"cost|price", q, re.I) for q in sql))
    conn.execute("UPDATE proc_items SET cost_price=0, has_cost=0 WHERE code='SYN-300'")
    conn.commit()

    print("\n--- 7. re-import safety: the ERP file MERGES, never duplicates ---")
    before_items = n_items()
    res = upsert_items(conn, [
        {"code": "SYN-300", "name": "Hydraulic seal kit ZX (ERP)", "unit": "Pcs",
         "category_code": "01", "category_name": "Widgets",
         "cost_price": 55.0, "has_cost": 1},
    ], {"username": "tester"}, source="erp_export.xls")
    dupes = conn.execute("SELECT COUNT(*) c FROM proc_items WHERE code='SYN-300'"
                         ).fetchone()["c"]
    merged = dict(conn.execute("SELECT * FROM proc_items WHERE code='SYN-300'").fetchone())
    ok(f"the re-import UPDATES the platform-created row ({res})",
       res["updated"] == 1 and res["added"] == 0)
    ok("there is still exactly ONE row on that code", dupes == 1 and n_items() == before_items)
    ok("and it now carries the ERP's name and source",
       merged["name"] == "Hydraulic seal kit ZX (ERP)" and merged["source"] == "erp_export.xls")

    print("\n--- 8. boot cost: create_and_seed x3 adds nothing -----------------")
    from app.approvals.schema import create_and_seed
    b_items, b_reqs = n_items(), n_reqs()
    b_prs = conn.execute("SELECT COUNT(*) c FROM pr_requests").fetchone()["c"]
    for _ in range(3):
        create_and_seed(conn)
    ok("proc_items unchanged", n_items() == b_items)
    ok("pr_requests unchanged",
       conn.execute("SELECT COUNT(*) c FROM pr_requests").fetchone()["c"] == b_prs)
    ok("proc_item_requests unchanged and NEVER seeded", n_reqs() == b_reqs)
    fresh = get_db()
    try:
        create_and_seed(fresh)
        ok("on a database that already has data, the table is still empty of seed rows",
           fresh.execute("SELECT COUNT(*) c FROM proc_item_requests WHERE requested_by "
                         "IN ('system','seed')").fetchone()["c"] == 0)
    finally:
        fresh.close()

    print("\n--- 3c. the off-catalogue report ---------------------------------")
    from app.services import reporting as R
    spec = R.get("proc_off_catalogue")
    ok("the report is registered on the SHARED engine, not a bespoke page",
       spec is not None and spec["perm"] == "proc_view")
    res = R.run(spec, {})
    ok(f"it runs with no error ({res['queries']} queries)", not res["error"])
    texts = {r["item_text"] for r in res["rows"]}
    ok("it lists the free-text lines that never got a catalogue link",
       "Gold-plated spanner" in texts)
    ok("...and DROPS the line that was linked by an approval",
       "Hydraulic seal kit ZX" not in texts)
    row = [r for r in res["rows"] if r["item_text"] == "Gold-plated spanner"][0]
    ok("each row counts the lines, the requests and the asks already raised",
       int(row["lines"]) >= 1 and int(row["requests"]) >= 1 and int(row["raised"]) >= 1)
    ok("no money column: this report cannot leak a price",
       not any(w in c["key"] for c in spec["columns"]
               for w in ("price", "cost", "value", "total", "egp")))
    rr = get(buyer, "/reporting/proc_off_catalogue")
    ok("GET /reporting/proc_off_catalogue -> 200 (raw)", rr.status_code == 200)
    ok("the report exports through the shared engine too",
       get(buyer, "/reporting/proc_off_catalogue.csv").status_code == 200)
    ok("filters and the date window work (no crash on a grouped report)",
       not R.run(spec, {"item": "spanner", "period": "year"})["error"])

    print("\n--- 9. pages 200 in en / ar / tr, every data-i18n key resolves ----")
    DICT = {}
    for lang in ("en", "ar", "tr"):
        with open(REPO / "app" / "static" / "i18n" / f"{lang}.json", encoding="utf-8") as fh:
            DICT[lang] = json.load(fh)

    pages = {"refused": dup_body}
    for lang in ("en", "ar", "tr"):
        conn.execute("UPDATE users SET lang_pref=? WHERE id IN (1, 900)", (lang,))
        conn.commit()
        for name, cl, url in (("ask", plain, "/procurement/item-request"),
                              ("sent", plain, "/procurement/item-request?sent=1"),
                              ("new", plain, "/procurement/new"),
                              ("queue", buyer, "/procurement/item-requests"),
                              ("queue_all", buyer, "/procurement/item-requests?status=all"),
                              ("queue_rej", buyer,
                               "/procurement/item-requests?status=rejected")):
            r = get(cl, url)
            ok(f"GET {url} [{lang}] -> 200 (raw)", r.status_code == 200)
            pages[name] = r.get_data(as_text=True)

    keys = set()
    for html in pages.values():
        keys |= set(re.findall(r'data-i18n="([^"]+)"', html))
    mine = {k for k in keys if k.startswith("nir.")}
    ok(f"the pages render {len(mine)} new nir.* keys", len(mine) >= 20)
    missing = {k for k in mine if k not in ir.I18N and k not in DICT["en"]}
    ok(f"every new key has EN/AR/TR ready to merge {sorted(missing)}", not missing)
    for lang, idx in (("en", 0), ("ar", 1), ("tr", 2)):
        gap = {k for k in mine if k not in DICT[lang] and k not in ir.I18N}
        ok(f"every new key resolves in {lang} {sorted(gap)[:5]}", not gap)
    ok("every I18N entry carries a real EN, AR and TR string",
       all(len(v) == 3 and all(str(x).strip() for x in v) for v in ir.I18N.values()))
    ok("the Arabic strings are actually Arabic script",
       all(re.search(r"[؀-ۿ]", v[1]) for v in ir.I18N.values()))
    ok("the Turkish strings differ from the English",
       all(v[2].strip() != v[0].strip() for v in ir.I18N.values()))
    reused = {k for k in keys if not k.startswith("nir.")}
    for lang in ("en", "ar", "tr"):
        gap = {k for k in reused if k not in DICT[lang]}
        ok(f"every PRE-EXISTING key on these pages still resolves in {lang} "
           f"{sorted(gap)[:5]}", not gap)
    ok("the picker offers the new path from its 'no match' message",
       "item-request" in pages["new"] and "ITEM_ASK" in pages["new"])
    ok("...in a NEW TAB, so a half-typed request cannot be lost",
       'target="_blank"' in pages["new"])
    ok("the queue explains that the approver types the ERP code",
       "nir.code_hint" in pages["queue"])

    conn.close()

print("\n" + "=" * 62)
print(f"  {sum(PASS)}/{len(PASS)} checks passed")
print("=" * 62)
sys.exit(0 if all(PASS) else 1)
