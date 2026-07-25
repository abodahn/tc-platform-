"""
Cut room export self-test — the three datasets and both routes.

Seeds warehouse + costing too, so lay-rolls has rows and order-summary has a real
plan/variance to emit (an export that only ever ran against the degraded path would
not prove much).
Run:  python app/cutroom/tests_export.py
"""
import json
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

import config                                        # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                           # noqa: E402
from app.db import get_db                            # noqa: E402
from app.cutroom import services as svc              # noqa: E402

KEYS = ["lay-register", "order-summary", "lay-rolls"]
OK = []


def check(name, cond):
    OK.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name)


app = create_app()

with app.app_context():
    conn = get_db()
    from app.warehouse.schema import create_and_seed as wh_seed
    from app.costing.schema import create_and_seed as cst_seed
    from app.cutroom.schema import create_and_seed as cut_seed
    wh_seed(conn)
    cst_seed(conn)
    cut_seed(conn)
    conn.commit()
    admin = conn.execute(
        "SELECT id, COALESCE(session_epoch,0) AS ep FROM users WHERE role='super_admin' "
        "AND is_active=1 ORDER BY id").fetchone()
    conn.close()

print("\n[1] export_dataset — shape of every row")
with app.app_context():
    for k in KEYS:
        headers, rows = svc.export_dataset(k)
        rows = list(rows)
        check(f"{k}: headers is a non-empty list", isinstance(headers, list) and headers)
        check(f"{k}: has rows ({len(rows)})", len(rows) > 0)
        check(f"{k}: every row has len == len(headers) ({len(headers)})",
              all(len(r) == len(headers) for r in rows))
        check(f"{k}: no cell is None/dict/list",
              all(not isinstance(c, (dict, list)) and c is not None for r in rows for c in r))
    check("unknown key -> (None, None)", svc.export_dataset("nope") == (None, None))

print("\n[2] routes — CSV + JSON with an admin session")
check("an active admin exists to sign in as", admin is not None)
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = admin["ep"]
    for k in KEYS:
        with app.app_context():
            headers, rows = svc.export_dataset(k)
            n = len(list(rows))
        r = c.get(f"/cutroom/export/{k}.csv")
        check(f"{k}: CSV 200", r.status_code == 200)
        check(f"{k}: CSV starts with the UTF-8 BOM", r.data[:3] == b"\xef\xbb\xbf")
        check(f"{k}: CSV header line matches the dataset headers",
              r.data.decode("utf-8-sig").splitlines()[0].split(",")[0] == headers[0])

        j = c.get(f"/cutroom/api/{k}.json")
        check(f"{k}: JSON 200", j.status_code == 200)
        d = json.loads(j.data)
        check(f"{k}: JSON count == {n}", d["count"] == n == len(d["rows"]))
        check(f"{k}: JSON columns == headers", d["columns"] == [str(h) for h in headers])
        check(f"{k}: JSON dataset name echoed", d["dataset"] == k)

    check("unknown CSV key -> 404", c.get("/cutroom/export/nope.csv").status_code == 404)
    check("unknown JSON key -> 404", c.get("/cutroom/api/nope.json").status_code == 404)

print("\n[3] the numbers actually landed in the file")
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = admin["ep"]
    body = c.get("/cutroom/export/order-summary.csv").data.decode("utf-8-sig")
    check("order-summary CSV carries the denim variance (3.21)", "3.21" in body)
    rolls = json.loads(c.get("/cutroom/api/lay-rolls.json").data)["rows"]
    check("lay-rolls rows carry metres and a shade lot",
          all(r["Shade Lot"] and r["Meters"] for r in rolls))

    # The pages that carry the new links: a bad url_for() would 500 the whole page,
    # not just the button.
    for path, key in (("/cutroom/lays", "lay-register"), ("/cutroom/", "order-summary")):
        p = c.get(path)
        check(f"{path} renders with the export link", p.status_code == 200
              and f"/cutroom/export/{key}.csv" in p.data.decode("utf-8"))

print("\n%d/%d checks passed" % (sum(1 for _, o in OK if o), len(OK)))
sys.exit(0 if all(o for _, o in OK) else 1)
