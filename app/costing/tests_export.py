"""
Costing export self-test — dataset contract + both routes, on a throwaway DB.
    python app/costing/tests_export.py

Asserts the wire format, not the numbers (tests_selftest.py owns the money
invariants): every row is header-length, no cell is a dict/list/None, the CSV
carries the UTF-8 BOM Excel needs for Arabic, the JSON count matches, and an
unknown key is a 404 rather than an empty file.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cst_exp_"))
os.chdir(TMP)                                       # Config.DB_PATH is repo-relative
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                       # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                          # noqa: E402

app = create_app()
PASS = []
KEYS = ["order-margin", "cost-breakdown", "bom-lines", "actuals"]


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


with app.app_context():
    from app.db import get_db
    from app.costing.schema import create_and_seed
    from app.costing import services as svc

    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    admin = conn.execute(
        "SELECT id, COALESCE(session_epoch,0) ep FROM users WHERE role='super_admin' "
        "AND is_active=1 ORDER BY id").fetchone()
    conn.close()
    ok("an admin user exists to exercise the routes with", admin is not None)

    print("\n-- export_dataset() contract --")
    counts = {}
    for key in KEYS:
        headers, rows = svc.export_dataset(key)
        rows = list(rows)
        counts[key] = len(rows)
        ok("%s: headers is a list (%d cols), %d rows"
           % (key, len(headers or []), len(rows)), isinstance(headers, list) and headers)
        ok("%s: every row matches header width" % key,
           all(len(r) == len(headers) for r in rows))
        bad = [(i, c) for i, r in enumerate(rows) for c in r
               if c is None or isinstance(c, (dict, list, set, tuple))]
        ok("%s: no None / dict / list cells" % key, not bad)
        if bad:
            print("        offenders:", bad[:3])
    ok("unknown key -> (None, None)", svc.export_dataset("no-such-thing") == (None, None))

print("\n-- routes --")
with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = admin["ep"]
    for key in KEYS:
        r = c.get("/costing/export/%s.csv" % key)
        body = r.get_data()
        ok("GET /costing/export/%s.csv -> %d, BOM present" % (key, r.status_code),
           r.status_code == 200 and body.startswith(b"\xef\xbb\xbf"))
        r = c.get("/costing/api/%s.json" % key)
        d = json.loads(r.get_data()) if r.status_code == 200 else {}
        ok("GET /costing/api/%s.json -> %d, count %s == %d"
           % (key, r.status_code, d.get("count"), counts[key]),
           r.status_code == 200 and d.get("count") == counts[key]
           and d.get("dataset") == key)
    ok("unknown csv key -> 404", c.get("/costing/export/nope.csv").status_code == 404)
    ok("unknown json key -> 404", c.get("/costing/api/nope.json").status_code == 404)

    # The list pages build the download links with url_for(), so a mistyped key or
    # endpoint is a 500 on the page itself, not a broken download.
    for path in ("/costing/", "/costing/orders"):
        r = c.get(path)
        ok("GET %s -> %d with the export links rendered" % (path, r.status_code),
           r.status_code == 200 and b"/costing/export/" in r.get_data())

print("\n%d/%d checks passed" % (sum(PASS), len(PASS)))
sys.exit(0 if all(PASS) else 1)
