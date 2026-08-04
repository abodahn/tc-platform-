# -*- coding: utf-8 -*-
"""
Procurement item-catalogue self-test — throwaway database, SYNTHETIC data only.
    python app/approvals/tests_catalogue.py

THIS REPO IS PUBLIC. The real item master carries confidential supplier cost
prices, so nothing here reads it: every workbook below is generated in a
tempfile from made-up codes and made-up prices, and the .xls case is a
hand-assembled BIFF stream rather than a fixture.

What it proves, by execution:
  1  parser: header found not assumed; category bands carried down; blank /
     filter-echo / repeated-header rows skipped; an unmappable unit REPORTED,
     never defaulted; malformed rows rejected with a reason.
  2  .xls, .xlsx and .csv parse to the SAME items.
  3  upsert: idempotent; a changed cost updates; a hand-edited name survives a
     blank cell; codes stay unique.
  4  has_cost keeps a genuine 0.00 apart from "no price on file".
  5  THE REGRESSION: a free-text (off-catalogue) PR line still creates, submits
     and walks the whole approval ladder exactly as before.
  6  LOCKOUT: the search endpoint carries no cost, and no route added here
     leaks one to a requester.
  7  permissions: a non-proc_admin gets a raw 403 on the catalogue screen and
     its POST, and nothing is written.
  8  boot: create_and_seed x3 leaves proc_items EMPTY.
  9  the pages render 200 in en / ar / tr with every data-i18n key resolvable.
"""
import io
import json
import os
import re
import struct
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cat_"))
os.chdir(TMP)
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                          # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                             # noqa: E402
from app.approvals.catalogue import (parse_workbook, upsert_items, norm_unit,  # noqa: E402
                                     catalogue_stats, I18N)

app = create_app()
PASS = []


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


# ---------------------------------------------------------------------------
# Synthetic workbook — same logical content in three formats.
# Deliberately awkward: noise row, two filter-echo rows, a header that is NOT on
# row 3, blank rows, two category bands, a repeated header, an unmappable unit,
# a row with no code, a row with no name, a genuine 0.00 and a blank cost.
# ---------------------------------------------------------------------------
NOISE = ["1", "2", "3", "4", "5"]
FILTER1 = ["From Category:   To Category: zz", "", "", "", ""]
FILTER2 = ["From Item:                          To Item: zzzz", "", "", "", ""]
HEADER = ["Code", "Item Name", "Unit", "Sales Pric", "Cost Price"]
BLANK = ["", "", "", "", ""]

ROWS = [
    NOISE, FILTER1, FILTER2, ["", "", "", "", ""], HEADER, BLANK,
    ["Category: 01 Widgets", "", "", "", ""],
    ["WID-001", "Blue widget", "02 Piece", "0.00000", "12.50000"],
    ["WID-002", "Red widget", "07 CONE", "0.00000", "0.00000"],    # no price on file
    BLANK,
    HEADER,                                                        # repeated header
    ["Category: 02 Fasteners", "", "", "", ""],
    ["FAS-001", "M6 bolt", "01 KG", "0.00000", "3.25000"],
    ["FAS-002", "M8 bolt", "99 Gizmos", "0.00000", "4.00000"],     # unmappable unit
    ["", "Orphan with no code", "02 Piece", "0.00000", "1.00000"], # rejected
    ["FAS-003", "", "02 Piece", "0.00000", "1.00000"],             # rejected
    ["FAS-004", "Washer", "12 KTN", "0.00000", ""],                # blank cost
]


def write_csv(path):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(ROWS)


def write_xlsx(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in ROWS:
        ws.append(row)
    wb.save(path)


def write_xls(path):
    """A minimal BIFF2 stream. xlrd reads it; no writer dependency is needed,
    and no fixture derived from the real file goes anywhere near the repo."""
    def rec(t, data):
        return struct.pack("<HH", t, len(data)) + data

    def label(r, c, s):
        b = str(s).encode("latin-1", "replace")
        return rec(0x0004, struct.pack("<HH", r, c) + b"\x00\x00\x00"
                   + bytes([len(b)]) + b)

    out = rec(0x0009, struct.pack("<HH", 2, 0x0010))
    for r, row in enumerate(ROWS):
        for c, val in enumerate(row):
            if str(val) != "":
                out += label(r, c, val)
    out += rec(0x000A, b"")
    path.write_bytes(out)


CSV_F, XLSX_F, XLS_F = TMP / "items.csv", TMP / "items.xlsx", TMP / "items.xls"
write_csv(CSV_F)
write_xlsx(XLSX_F)
write_xls(XLS_F)


print("\n--- 1. parser -------------------------------------------------")
items, stats = parse_workbook(CSV_F)
by_code = {i["code"]: i for i in items}
ok("no parse error", not stats["error"])
ok("4 items survive (2 rejected rows + 1 unmapped unit dropped)", len(items) == 4)
ok("category band carried down onto its items",
   by_code["WID-001"]["category_code"] == "01"
   and by_code["WID-001"]["category_name"] == "Widgets"
   and by_code["FAS-001"]["category_code"] == "02"
   and by_code["FAS-001"]["category_name"] == "Fasteners")
ok("header found below the noise + filter rows (not assumed at a fixed row)",
   "WID-001" in by_code and "Code" not in by_code)
ok("blank rows counted, not imported", stats["blanks"] >= 3)
ok("filter-echo rows skipped", stats["filter_rows"] == 2)
ok("the repeated header row is skipped", stats["header_rows"] == 1)
ok("numbered units normalised (02 Piece->Pcs, 07 CONE->Cone, 01 KG->Kg, 12 KTN->Carton)",
   by_code["WID-001"]["unit"] == "Pcs" and by_code["WID-002"]["unit"] == "Cone"
   and by_code["FAS-001"]["unit"] == "Kg" and by_code["FAS-004"]["unit"] == "Carton")
ok("an unmappable unit is REPORTED, never defaulted to Pcs",
   stats["unmapped_units"].get("99 Gizmos") == 1 and "FAS-002" not in by_code)
ok("the unmapped-unit row is rejected WITH a reason",
   any(r["code"] == "FAS-002" and "unmapped unit" in r["reason"] for r in stats["rejects"]))
ok("a row with no code is rejected with a reason",
   any(r["reason"] == "missing code" for r in stats["rejects"]))
ok("a row with no name is rejected with a reason",
   any(r["code"] == "FAS-003" and r["reason"] == "missing item name"
       for r in stats["rejects"]))
ok("nothing is dropped silently: every skipped data row has a reject reason",
   len(stats["rejects"]) == 3)
ok("a file with no header row is refused, not half-imported",
   parse_workbook(io.BytesIO(b"a,b,c\n1,2,3\n"))[1]["error"] is not None)

print("\n--- 2. same result from .xls, .xlsx and .csv -------------------")
i_csv, s_csv = parse_workbook(CSV_F)
i_xlsx, s_xlsx = parse_workbook(XLSX_F)
i_xls, s_xls = parse_workbook(XLS_F)
ok(".xlsx parses identically to .csv", i_xlsx == i_csv)
ok(".xls parses identically to .csv", i_xls == i_csv)
ok("rejects match across all three formats",
   [r["reason"] for r in s_xls["rejects"]] == [r["reason"] for r in s_csv["rejects"]]
   == [r["reason"] for r in s_xlsx["rejects"]])
ok("format detected from CONTENT, not the extension",
   parse_workbook(io.BytesIO(XLSX_F.read_bytes()))[0] == i_csv)

print("\n--- 4. has_cost: genuine 0.00 vs no price on file --------------")
ok("blank cost -> has_cost 0 (no price on file)", by_code["FAS-004"]["has_cost"] == 0)
ok("a real price -> has_cost 1", by_code["WID-001"]["has_cost"] == 1
   and abs(by_code["WID-001"]["cost_price"] - 12.5) < 1e-9)
ok("ERP 0.00000 reads as 'no price on file' by default (the owner's 5,673 rows)",
   by_code["WID-002"]["has_cost"] == 0)
z = {i["code"]: i for i in parse_workbook(CSV_F, zero_is_missing=False)[0]}
ok("with zero_is_missing=False a genuine 0.00 is DISTINGUISHABLE from a blank",
   z["WID-002"]["has_cost"] == 1 and z["WID-002"]["cost_price"] == 0.0
   and z["FAS-004"]["has_cost"] == 0)
ok("unmappable unit stays unmappable (norm_unit returns None, never a default)",
   norm_unit("99 Gizmos") is None and norm_unit("") is None and norm_unit("cone") == "Cone")


with app.app_context():
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    from app.approvals import services as svc
    from app.approvals import constants as C

    print("\n--- 8. boot: create_and_seed leaves proc_items EMPTY -----------")
    conn = get_db()
    for _ in range(3):
        create_and_seed(conn)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) c FROM proc_items").fetchone()["c"]
    ok("create_and_seed x3 -> proc_items has 0 rows (19k never seeded at boot)", n == 0)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(proc_items)").fetchall()}
    ok("proc_items carries every required column",
       {"id", "code", "name", "unit", "category_code", "category_name", "cost_price",
        "has_cost", "source", "active", "updated_by", "updated_at"} <= cols)
    idx = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='proc_items'")}
    # ONE index, covering (active, category_code, category_name). There is
    # deliberately no index on name: the search matches `%q%`, which no b-tree
    # can serve, so a name index is write cost for nothing.
    ok("the category index exists and covers category_name",
       "ix_proc_items_cat" in idx and "ix_proc_items_name" not in idx)
    ok("...and it is a COVERING index for the category list",
       any("COVERING INDEX ix_proc_items_cat" in str(r[-1]) for r in conn.execute(
           "EXPLAIN QUERY PLAN SELECT category_code, category_name FROM proc_items "
           "WHERE active=1 GROUP BY category_code, category_name").fetchall()))
    ok("pr_items.item_id migration applied",
       "item_id" in {r[1] for r in conn.execute("PRAGMA table_info(pr_items)").fetchall()})

    print("\n--- 3. upsert -------------------------------------------------")
    c1 = upsert_items(conn, items, {"username": "tester"})
    ok(f"first import adds every item ({c1})", c1 == {"added": 4, "updated": 0,
                                                      "unchanged": 0, "rejected": 0})
    c2 = upsert_items(conn, items, {"username": "tester"})
    ok(f"IDEMPOTENT: same file twice -> 0 added, 0 updated ({c2})",
       c2["added"] == 0 and c2["updated"] == 0 and c2["unchanged"] == 4)
    ok("codes stay unique after two imports",
       conn.execute("SELECT COUNT(*) c FROM proc_items").fetchone()["c"] == 4
       and conn.execute("SELECT COUNT(DISTINCT code) c FROM proc_items").fetchone()["c"] == 4)

    changed = [dict(i) for i in items]
    for i in changed:
        if i["code"] == "WID-001":
            i["cost_price"], i["has_cost"] = 19.99, 1
    c3 = upsert_items(conn, changed, {"username": "tester"})
    row = conn.execute("SELECT cost_price, has_cost FROM proc_items WHERE code='WID-001'").fetchone()
    ok(f"a changed cost updates exactly one row ({c3})",
       c3["updated"] == 1 and abs(float(row["cost_price"]) - 19.99) < 1e-9)

    conn.execute("UPDATE proc_items SET name='Blue widget (admin corrected)' WHERE code='WID-001'")
    conn.commit()
    blanked = [dict(i) for i in changed]
    for i in blanked:
        if i["code"] == "WID-001":
            i["name"] = ""                      # the export shipped an empty cell
    upsert_items(conn, blanked, {"username": "tester"})
    ok("a blank cell NEVER clobbers an admin-edited name",
       conn.execute("SELECT name FROM proc_items WHERE code='WID-001'").fetchone()["name"]
       == "Blue widget (admin corrected)")

    st = catalogue_stats(conn)
    ok("catalogue_stats splits priced from 'no price on file'",
       st["total"] == 4 and st["priced"] == 2 and st["unpriced"] == 2)
    ok("has_cost=0 rows keep cost_price 0 (rendered as 'no price on file', not 0.00)",
       conn.execute("SELECT COUNT(*) c FROM proc_items WHERE has_cost=0 AND cost_price<>0"
                    ).fetchone()["c"] == 0)

    # ----------------------------------------------------------------
    # Route-level tests
    # ----------------------------------------------------------------
    # This whole file runs in ONE app context and Flask reuses it for every test
    # request, so auth.current_user()'s per-context cache would hand the second
    # client the FIRST client's user. Drop it before every request.
    from flask import g

    def get(cl, url):
        g.pop("user", None)
        return cl.get(url)

    def post(cl, url, **kw):
        g.pop("user", None)
        return cl.post(url, **kw)

    client = app.test_client()
    with client.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    with client.session_transaction() as s:
        CSRF = s.get("_csrf_token") or ""
    if not CSRF:
        get(client, "/procurement/")
        with client.session_transaction() as s:
            CSRF = s.get("_csrf_token") or ""

    print("\n--- upload front door A == CLI front door B --------------------")
    # Front door A (this upload) must land on exactly the state front door B
    # (the CLI's parse_workbook + upsert_items, driven above) already produced:
    # the SAME file through the SAME code has nothing new to add.
    r_up = post(client, "/procurement/catalogue/import",
                data={"_csrf": CSRF, "file": (io.BytesIO(XLS_F.read_bytes()),
                                              "items.xls")},
                content_type="multipart/form-data")
    up_body = r_up.get_data(as_text=True)
    ok("admin upload -> 200 (raw)", r_up.status_code == 200)
    ok("it adds nothing the CLI path already imported (one parser, two doors)",
       conn.execute("SELECT COUNT(*) c FROM proc_items").fetchone()["c"] == 4)
    ok("the screen reports added / updated / unchanged / rejected",
       "cat.added" in up_body and "cat.rejected" in up_body)
    ok("and shows every reject reason", "unmapped unit: 99 Gizmos" in up_body
       and "missing item name" in up_body)
    ok("and names the unit it refused to guess", "99 Gizmos" in up_body)
    ok("a NON-blank cell in the file does win over an earlier hand edit",
       conn.execute("SELECT name FROM proc_items WHERE code='WID-001'"
                    ).fetchone()["name"] == "Blue widget")

    print("\n--- 6. LOCKOUT: no cost through the picker ---------------------")
    r = get(client, "/procurement/api/items?q=WID")
    body = r.get_data(as_text=True)
    j = json.loads(body)
    ok("GET /procurement/api/items -> 200 (raw)", r.status_code == 200)
    ok("it does find catalogue items", len(j["results"]) >= 1)
    ok("the search response exposes code / name / unit / category only",
       all(set(x) == {"id", "code", "name", "unit", "category_code", "category"}
           for x in j["results"]))
    ok("NO cost field anywhere in the search response body",
       not re.search(r"cost|price", body, re.I))
    ok("the real cost is not in the body either", "12.5" not in body and "3.25" not in body)
    ok("results are capped and paginated (limit clamped, has_more reported)",
       len(json.loads(get(client, "/procurement/api/items?q=-&limit=9999"
                          ).get_data(as_text=True))["results"]) <= 20
       and "has_more" in j)
    ok("a bare 1-character query returns nothing (no 19k dump)",
       json.loads(get(client, "/procurement/api/items?q=W").get_data(as_text=True))["results"] == [])
    ok("category filter works", len(json.loads(get(
        client, "/procurement/api/items?q=WID&cat=02").get_data(as_text=True))["results"]) == 0)
    ok("_can_price is still hardcoded False (commercial lockout untouched)",
       __import__("app.routes.approvals", fromlist=["x"])._can_price() is False)

    print("\n--- 5. THE REGRESSION: a free-text line still runs the ladder ---")
    admin = conn.execute("SELECT * FROM users WHERE id=1").fetchone()
    requester = {"id": 999, "username": "freetexter", "full_name": "Free Texter"}
    pr_id, pr_no = svc.create_pr(
        {"title": "Off-catalogue request", "department": "Production",
         "currency": "EGP", "notes": ""},
        [{"item": "Something nobody catalogued", "description": "typed by hand",
          "unit": "Pcs", "qty": 3, "current_stock": 0}],
        requester, ip="127.0.0.1", submit=False)
    ok(f"a free-text PR is created ({pr_no})", bool(pr_id))
    line = conn.execute("SELECT item, unit, item_id, spare_id FROM pr_items WHERE pr_id=?",
                        (pr_id,)).fetchone()
    ok("its line stores NO catalogue link and behaves exactly as before",
       line["item_id"] is None and line["spare_id"] is None
       and line["item"] == "Something nobody catalogued" and line["unit"] == "Pcs")
    okk, msg = svc.submit_pr(pr_id, requester, ip="127.0.0.1")
    ok(f"it submits into the ladder ({msg or 'ok'})", okk)
    steps = conn.execute("SELECT stage, status FROM pr_steps WHERE pr_id=? ORDER BY seq",
                         (pr_id,)).fetchall()
    ok("the demand ladder is built (warehouse -> factory -> purchasing)",
       [s["stage"] for s in steps][:3] == ["warehouse", "factory_manager", "purchasing"])
    admin_actor = dict(admin)
    guard = 0
    while guard < 12:
        guard += 1
        pr = conn.execute("SELECT status, pricing_status FROM pr_requests WHERE id=?",
                          (pr_id,)).fetchone()
        if pr["status"] != "pending":
            break
        if (pr["pricing_status"] or "priced") == "unpriced":
            svc.price_pr(pr_id, {str(line_id["id"]): 40.0 for line_id in
                                 conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                                              (pr_id,)).fetchall()},
                         {"currency": "EGP", "vendor": "Delta Industrial Supplies"},
                         admin_actor, ip="127.0.0.1")
        a_ok, a_msg = svc.act_on_step(pr_id, admin_actor, "approve", "ok", ip="127.0.0.1")
        if not a_ok:
            print("      (ladder stopped: " + str(a_msg) + ")")
            break
    final = conn.execute("SELECT status, total FROM pr_requests WHERE id=?", (pr_id,)).fetchone()
    ok(f"the whole ladder still runs on an off-catalogue line -> {final['status']}",
       final["status"] in ("approved", "po_issued"))

    print("\n--- 5b. catalogue-linked line + the Purchasing cost reference ---")
    linked = conn.execute("SELECT id, code FROM proc_items WHERE code='WID-001'").fetchone()
    nocost = conn.execute("SELECT id, code FROM proc_items WHERE code='FAS-004'").fetchone()
    cat_pr_id, cat_pr_no = svc.create_pr(
        {"title": "Catalogue-linked request", "department": "Production", "currency": "EGP"},
        [{"item": "WID-001 — Blue widget", "unit": "Pcs", "qty": 2,
          "item_id": str(linked["id"])},
         {"item": "FAS-004 — Washer", "unit": "Carton", "qty": 1,
          "item_id": str(nocost["id"])}],
        requester, ip="127.0.0.1", submit=False)
    rows = conn.execute("SELECT item_id FROM pr_items WHERE pr_id=? ORDER BY seq",
                        (cat_pr_id,)).fetchall()
    ok(f"a picked catalogue item stores its link ({cat_pr_no})",
       [r["item_id"] for r in rows] == [linked["id"], nocost["id"]])
    ok("the linked line still carries NO price (the requester never sets one)",
       conn.execute("SELECT SUM(unit_price) s FROM pr_items WHERE pr_id=?",
                    (cat_pr_id,)).fetchone()["s"] == 0)
    svc.submit_pr(cat_pr_id, requester, ip="127.0.0.1")

    print("\n--- 7. permissions on the admin screen -------------------------")
    conn.execute("INSERT OR IGNORE INTO users (id, username, full_name, role, is_active, "
                 "password_hash) VALUES (?,?,?,?,?,?)",
                 (900, "plainuser", "Plain User", "normal_user", 1, "x"))
    conn.commit()
    before = conn.execute("SELECT COUNT(*) c FROM proc_items").fetchone()["c"]
    plain = app.test_client()
    with plain.session_transaction() as s:
        s["uid"] = 900
        s["ep"] = 0
    get(plain, "/procurement/")
    with plain.session_transaction() as s:
        PCSRF = s.get("_csrf_token") or ""
    r_get = get(plain, "/procurement/catalogue")
    ok("non-admin GET /procurement/catalogue -> raw 403", r_get.status_code == 403)
    r_post = post(plain, "/procurement/catalogue/import",
                  data={"_csrf": PCSRF, "file": (io.BytesIO(CSV_F.read_bytes()),
                                                 "items.csv")},
                  content_type="multipart/form-data")
    ok("non-admin POST /procurement/catalogue/import -> raw 403", r_post.status_code == 403)
    ok("and NOTHING was written by the refused POST",
       conn.execute("SELECT COUNT(*) c FROM proc_items").fetchone()["c"] == before)

    print("\n--- 9. pages 200 in en / ar / tr, every data-i18n key resolves --")
    DICT = {}
    for lang in ("en", "ar", "tr"):
        with open(REPO / "app" / "static" / "i18n" / f"{lang}.json", encoding="utf-8") as fh:
            DICT[lang] = json.load(fh)

    pages = {}
    for lang in ("en", "ar", "tr"):
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
        conn.commit()
        for name, url in (("catalogue", "/procurement/catalogue"),
                          ("new", "/procurement/new"),
                          ("detail", f"/procurement/pr/{pr_id}"),
                          ("pricing", f"/procurement/pr/{cat_pr_id}")):
            r = get(client, url)
            ok(f"GET {url} [{lang}] -> 200 (raw)", r.status_code == 200)
            pages[name] = r.get_data(as_text=True)

    pages["import_result"] = up_body       # the POST response: counts + rejects
    keys = set()
    for html in pages.values():
        keys |= set(re.findall(r'data-i18n="([^"]+)"', html))
    mine = {k for k in keys if k.startswith("cat.")}
    ok(f"the pages render {len(mine)} new cat.* keys", len(mine) >= 20)
    ok("the admin screen renders the import + reject keys",
       {"cat.title", "cat.import_h", "cat.run", "cat.added", "cat.rejected",
        "cat.rejects_h", "cat.unmapped_h", "cat.no_price"}
       <= set(re.findall(r'data-i18n="([^"]+)"', pages["catalogue"] + up_body)))
    ok("the request form renders the picker keys",
       {"cat.filter", "cat.all", "cat.pick_hint"}
       <= set(re.findall(r'data-i18n="([^"]+)"', pages["new"])))
    ok("the pricing gate renders the cost REFERENCE, labelled as reference only",
       {"cat.last_cost", "cat.ref_only", "cat.no_price"}
       <= set(re.findall(r'data-i18n="([^"]+)"', pages["pricing"])))
    ok("the reference shows 'no price on file' for the has_cost=0 line, never 0.00",
       "cat.no_price" in pages["pricing"])
    ok("Purchasing still TYPES the price — it is not pre-filled from the catalogue",
       re.search(r'name="price_\d+"[^>]*value="0"', pages["pricing"]) is not None
       and "12.50" in pages["pricing"])          # shown as reference text only
    missing = {k for k in mine if k not in I18N and k not in DICT["en"]}
    ok(f"every new key has EN/AR/TR ready to merge {sorted(missing) or ''}", not missing)
    ok("every I18N entry carries a real EN, AR and TR string",
       all(len(v) == 3 and all(str(x).strip() for x in v) for v in I18N.values()))
    ok("the Arabic strings are actually Arabic script",
       all(re.search(r"[؀-ۿ]", v[1]) for v in I18N.values()))
    ok("the Turkish strings differ from the English",
       all(v[2].strip() != v[0].strip() for v in I18N.values()))
    reused = {k for k in keys if not k.startswith("cat.")}
    unresolved = {k for k in reused if k not in DICT["en"]}
    ok(f"every PRE-EXISTING key on these pages still resolves in en {sorted(unresolved)[:5]}",
       not unresolved)
    for lang in ("ar", "tr"):
        gap = {k for k in reused if k not in DICT[lang]}
        ok(f"...and in {lang} {sorted(gap)[:5]}", not gap)
    ok("the requester's own PR page carries no catalogue cost label",
       "cat.last_cost" not in pages["new"])

    conn.close()

print("\n" + "=" * 62)
print(f"  {sum(PASS)}/{len(PASS)} checks passed")
print("=" * 62)
sys.exit(0 if all(PASS) else 1)
