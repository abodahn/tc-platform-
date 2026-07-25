"""
Wash dataset-export test — services + both routes, against a throwaway DB.
Run:  python app/wash/tests_export.py

Asserts the shape contract (headers a list, every row the same width, no dict/list/None
cell), that the exports FOOT against the pages they come from (the step sheet's doses sum
to the version total, the impact roll-up matches the recipe sheet), that the CSV carries
the Excel BOM, that the JSON count matches the row count, and that an unknown key is a
404 rather than a 500.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wsh_exp_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                    # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                       # noqa: E402

app = create_app()
FAILS = []
RUN = [0]

KEYS = ["recipes", "recipe-steps", "batches", "lab-dips", "version-impact"]


def ck(name, cond):
    RUN[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)


counts = {}

with app.app_context():
    from app.db import get_db
    from app.wash.schema import create_and_seed
    from app.wash import services as svc

    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    admin_id = conn.execute(
        "INSERT INTO users (username,password_hash,full_name,role,is_active,created_at) "
        "VALUES (?,?,?,?,1,?)",
        ("wsh_exp_admin", "x", "Export Admin", "super_admin", "2026-01-01")).lastrowid
    conn.commit()

    print("\n[1] shape contract — every key")
    for k in KEYS:
        h, r = svc.export_dataset(k)
        counts[k] = len(r)
        ck("%s: headers is a non-empty list of str" % k,
           isinstance(h, list) and h and all(isinstance(x, str) for x in h))
        ck("%s: %d rows, every row %d wide" % (k, len(r), len(h)),
           all(len(row) == len(h) for row in r))
        ck("%s: no dict/list/None cell" % k,
           all(not isinstance(c, (dict, list)) and c is not None for row in r for c in row))
        ck("%s: header names are unique (they become JSON field names)" % k,
           len(set(h)) == len(h))

    ck("unknown key -> (None, None)", svc.export_dataset("nope") == (None, None))

    print("\n[2] the seeded library is actually in there")
    hr, rr = svc.export_dataset("recipes")
    ir = {h: i for i, h in enumerate(hr)}
    codes = [r[ir["Code"]] for r in rr]
    ck("recipes lists the 3 seeded families", sorted(codes) ==
       ["WR-ENZ-02", "WR-RNS-03", "WR-STN-01"])
    stn = next(r for r in rr if r[ir["Code"]] == "WR-STN-01")
    ck("WR-STN-01 shows 2 versions, bulk standard v2, latest v2 approved",
       [stn[ir["Versions"]], stn[ir["Bulk Standard"]], stn[ir["Latest Version"]],
        stn[ir["Latest Status"]]] == [2, 2, 2, "approved"])
    rns = next(r for r in rr if r[ir["Code"]] == "WR-RNS-03")
    ck("the draft recipe's empty bulk standard is '' not None", rns[ir["Bulk Standard"]] == "")

    print("\n[3] the step sheet foots against the version totals")
    hs, rs = svc.export_dataset("recipe-steps")
    isx = {h: i for i, h in enumerate(hs)}
    v2 = next(v for v in svc.list_versions() if v["code"] == "WR-STN-01" and v["version"] == 2)
    t = svc.version_totals(v2["id"])
    mine = [r for r in rs if r[isx["Code"]] == "WR-STN-01" and r[isx["Version"]] == 2]
    # The dry step has no chemical line, so it is one row with a blank dose — the
    # export must still show it or the cycle would be missing its 40 minutes.
    ck("WR-STN-01 v2 exports 5 rows, one per step (incl. the chemical-free dry step)",
       len(mine) == 5)
    ck("step-sheet minutes sum to the page's cycle_min (%g)" % t["cycle_min"],
       round(sum(float(r[isx["Minutes"]]) for r in mine), 2) == t["cycle_min"])
    ck("step-sheet Bath L sums to the page's water_l (%g)" % t["water_l"],
       round(sum(float(r[isx["Bath L"]]) for r in mine), 2) == t["water_l"])
    ck("step-sheet Dose g sums to the page's chem_g (%g)" % t["chem_g"],
       round(sum(float(r[isx["Dose g"]]) for r in mine if r[isx["Dose g"]] != ""), 1)
       == round(t["chem_g"], 1))
    ck("the dry step's dose cell is blank, not a misleading 0",
       all(r[isx["Dose g"]] == "" for r in mine if r[isx["Operation"]] == "dry"))
    ck("the retired v1 is exported too — it is what old batches ran",
       any(r[isx["Code"]] == "WR-STN-01" and r[isx["Version"]] == 1 for r in rs))

    print("\n[4] batches, lab dips and the impact roll-up")
    hb, rb = svc.export_dataset("batches")
    ib = {h: i for i, h in enumerate(hb)}
    ck("batches exports the 3 seeded lots with their batch_no", len(rb) == 3 and
       all(str(r[ib["Batch No"]]).startswith("WB-") for r in rb))
    ck("the off-recipe lot carries its deviation text",
       sum(1 for r in rb if "time 140" in str(r[ib["Deviation"]])) == 1)
    ck("an in-tolerance lot's deviation cell is '' not None",
       sum(1 for r in rb if r[ib["Deviation"]] == "") == 2)

    hd, rd = svc.export_dataset("lab-dips")
    idp = {h: i for i, h in enumerate(hd)}
    ck("lab-dips matches the page row for row", len(rd) == len(svc.list_labdips()))
    ck("the pending dip has a blank approver and date, the approved ones do not",
       sorted(r[idp["Verdict"]] for r in rd) == ["approved", "approved", "pending"] and
       all((r[idp["Approver"]] == "") == (r[idp["Verdict"]] == "pending") for r in rd))

    hi, ri = svc.export_dataset("version-impact")
    ii = {h: i for i, h in enumerate(hi)}
    ck("version-impact has one row per version (4)", len(ri) == 4)
    row = next(r for r in ri if r[ii["Code"]] == "WR-STN-01" and r[ii["Version"]] == 2)
    imp = svc.impact(t)
    ck("its cycle/water/chem/load match version_totals() exactly",
       [row[ii["Cycle Minutes"]], row[ii["Water L"]], row[ii["Chemical g"]],
        row[ii["Load kg"]]] == [t["cycle_min"], t["water_l"], t["chem_g"], t["load_kg"]])
    ck("its impact score and band match impact() (%s / %s)" % (imp["score"], imp["band"]),
       [row[ii["Impact Score"]], row[ii["Impact Band"]]] == [imp["score"], imp["band"]])
    ck("v2 counts its 2 batches and its 1 approved lab dip",
       [row[ii["Batches"]], row[ii["Approved Lab Dips"]]] == [2, 1])
    ck("a version nothing has run counts 0 batches, never blank",
       all(isinstance(r[ii["Batches"]], int) for r in ri))

print("\n[5] routes through the test client")
cl = app.test_client()
ck("anonymous CSV export redirects to login",
   cl.get("/wash/export/recipes.csv").status_code == 302)
with cl.session_transaction() as sess:
    sess["uid"] = admin_id
    sess["ep"] = 0

for k in KEYS:
    r = cl.get("/wash/export/%s.csv" % k)
    body = r.get_data()
    ck("GET /wash/export/%s.csv -> 200 (%s)" % (k, r.status_code), r.status_code == 200)
    ck("%s.csv starts with the utf-8 BOM" % k, body[:3] == b"\xef\xbb\xbf")
    ck("%s.csv body has %d data lines" % (k, counts[k]),
       len(body.decode("utf-8-sig").strip().splitlines()) == counts[k] + 1)

    j = cl.get("/wash/api/%s.json" % k)
    ck("GET /wash/api/%s.json -> 200 (%s)" % (k, j.status_code), j.status_code == 200)
    d = json.loads(j.get_data(as_text=True))
    ck("%s.json count == %d and dataset/columns are set" % (k, counts[k]),
       d["count"] == counts[k] == len(d["rows"]) and d["dataset"] == k and d["columns"])

ck("unknown CSV key -> 404", cl.get("/wash/export/nope.csv").status_code == 404)
ck("unknown JSON key -> 404", cl.get("/wash/api/nope.json").status_code == 404)

print("\n%d checks, %d failed" % (RUN[0], len(FAILS)))
for f in FAILS:
    print("  FAILED: " + f)
sys.exit(1 if FAILS else 0)
