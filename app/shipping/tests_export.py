"""
Shipping dataset-export test — services + both routes, against a throwaway DB.
Run:  python app/shipping/tests_export.py
Asserts the shape contract (headers a list, every row the same width, no dict/list/None
cell), that the CSV carries the Excel BOM, that the JSON count matches the row count,
that the invoice-lines export foots against the invoice page, and that an unknown key
is a 404 rather than a 500.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="shp_exp_"))
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

KEYS = ["shipments", "packing-lines", "invoice-lines", "reconciliation"]


def ck(name, cond):
    RUN[0] += 1
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILS.append(name)


with app.app_context():
    from app.db import get_db
    from app.shipping.schema import create_and_seed
    from app.shipping import services as svc

    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    admin_id = conn.execute(
        "INSERT INTO users (username,password_hash,full_name,role,is_active,created_at) "
        "VALUES (?,?,?,?,1,?)",
        ("exp_admin", "x", "Export Admin", "super_admin", "2026-01-01")).lastrowid
    conn.commit()
    SID = conn.execute("SELECT id FROM shp_shipments ORDER BY id").fetchone()["id"]
    conn.close()

    print("\n[1] export_dataset() shape contract")
    counts = {}
    for k in KEYS:
        headers, rows = svc.export_dataset(k)
        rows = list(rows)
        counts[k] = len(rows)
        ck("%s: headers is a list of str" % k,
           isinstance(headers, list) and headers
           and all(isinstance(h, str) for h in headers))
        ck("%s: every row has %d cells" % (k, len(headers or [])),
           all(len(r) == len(headers) for r in rows))
        bad = [(i, c) for i, r in enumerate(rows) for c in r
               if c is None or isinstance(c, (dict, list, tuple, set))]
        ck("%s: no None / dict / list cell (%d rows)" % (k, len(rows)), not bad)

    ck("unknown key -> (None, None)", svc.export_dataset("no-such-key") == (None, None))

    print("\n[2] the seeded data actually came through")
    ck("shipments has the 3 seeded rows (%d)" % counts["shipments"], counts["shipments"] == 3)
    ck("packing-lines has the 7 seeded carton lines (%d)" % counts["packing-lines"],
       counts["packing-lines"] == 7)
    ck("invoice-lines groups to fewer rows than carton lines (%d)" % counts["invoice-lines"],
       0 < counts["invoice-lines"] <= counts["packing-lines"])

    print("\n[3] arithmetic matches the documents on screen")
    hp, rp = svc.export_dataset("packing-lines")
    ip = {h: i for i, h in enumerate(hp)}
    ln = [r for r in rp if r[ip["Cartons"]] == 50][0]      # seed line 1-50: 60/ctn, 60x40x30
    ck("pieces = 60 x 50 = 3000", ln[ip["Pieces"]] == 3000)
    ck("net total = 12.5 x 50 = 625.0", ln[ip["Net kg Total"]] == 625.0)
    ck("cbm = 0.6*0.4*0.3*50 = 3.6", ln[ip["CBM"]] == 3.6)

    hi, ri = svc.export_dataset("invoice-lines")
    ii = {h: i for i, h in enumerate(hi)}
    b = svc.invoice(SID)
    page = round(b["invoice_total"], 2)
    mine = round(sum(r[ii["Amount"]] for r in ri
                     if r[ii["Shipment No"]] == b["shipment"]["shipment_no"]), 2)
    ck("invoice-lines foots against the invoice page (%.2f vs %.2f)" % (mine, page),
       mine == page)

    hr, rr = svc.export_dataset("reconciliation")
    ir = {h: i for i, h in enumerate(hr)}
    ck("reconciliation flag column only ever says Short/Over/In tolerance",
       all(r[ir["Flag"]] in ("Short", "Over", "In tolerance") for r in rr))

print("\n[4] routes through the test client")
cl = app.test_client()
ck("anonymous CSV export redirects to login",
   cl.get("/shipping/export/shipments.csv").status_code == 302)
with cl.session_transaction() as sess:
    sess["uid"] = admin_id
    sess["ep"] = 0

for k in KEYS:
    r = cl.get("/shipping/export/%s.csv" % k)
    body = r.get_data()
    ck("GET /shipping/export/%s.csv -> 200 (%s)" % (k, r.status_code), r.status_code == 200)
    ck("%s.csv starts with the utf-8 BOM" % k, body[:3] == b"\xef\xbb\xbf")
    ck("%s.csv body has %d data lines" % (k, counts[k]),
       len(body.decode("utf-8-sig").strip().splitlines()) == counts[k] + 1)

    j = cl.get("/shipping/api/%s.json" % k)
    ck("GET /shipping/api/%s.json -> 200 (%s)" % (k, j.status_code), j.status_code == 200)
    d = json.loads(j.get_data(as_text=True))
    ck("%s.json count == %d and dataset/columns are set" % (k, counts[k]),
       d["count"] == counts[k] == len(d["rows"]) and d["dataset"] == k and d["columns"])

ck("unknown CSV key -> 404", cl.get("/shipping/export/nope.csv").status_code == 404)
ck("unknown JSON key -> 404", cl.get("/shipping/api/nope.json").status_code == 404)

print("\n%d checks, %d failed" % (RUN[0], len(FAILS)))
for f in FAILS:
    print("  FAILED: " + f)
sys.exit(1 if FAILS else 0)
