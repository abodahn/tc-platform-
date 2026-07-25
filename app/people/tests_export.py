"""
HR Core export test — shape of every dataset + both routes end to end.
Run:  python app/people/tests_export.py

Isolated throwaway DB (Config.DB_PATH is overridden before create_app, or the
repo's own platform.db would be seeded by a test run).
"""
import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ppl_exp_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                      # noqa: E402

app = create_app()
KEYS = ["attendance", "leave-requests", "leave-balances", "skill-matrix",
        "incentive", "incentive-summary"]
OK = []


def check(label, cond):
    assert cond, "FAILED: " + label
    OK.append(label)
    print("  ok  " + label)


with app.app_context():
    from app.db import get_db
    from app.people.schema import create_and_seed
    from app.people import services as svc

    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    admin = dict(conn.execute(
        "SELECT id, session_epoch FROM users WHERE role='super_admin' "
        "ORDER BY id LIMIT 1").fetchone())
    conn.close()

    # --- 1. service contract ---------------------------------------------
    counts = {}
    for key in KEYS:
        headers, rows = svc.export_dataset(key)
        check("%s: headers is a list of names" % key,
              isinstance(headers, list) and headers
              and all(isinstance(h, str) and h for h in headers))
        rows = list(rows)
        counts[key] = len(rows)
        check("%s: %d row(s), every row matches the header width" % (key, len(rows)),
              all(len(r) == len(headers) for r in rows))
        bad = [(i, j, v) for i, r in enumerate(rows) for j, v in enumerate(r)
               if v is None or isinstance(v, (dict, list, tuple, set))]
        check("%s: no None / dict / list cells" % key, not bad)

    check("the seeded demo data actually produced rows in every dataset",
          all(counts[k] > 0 for k in KEYS))

    check("unknown key returns (None, None)",
          svc.export_dataset("no-such-dataset") == (None, None))

    # --- 2. routes --------------------------------------------------------
    client = app.test_client()
    with client.session_transaction() as s:
        s["uid"] = admin["id"]
        s["ep"] = admin["session_epoch"] or 0

    for key in KEYS:
        r = client.get("/people/export/%s.csv" % key)
        check("GET /people/export/%s.csv -> 200" % key, r.status_code == 200)
        body = r.data
        check("%s.csv starts with the UTF-8 BOM (Excel + Arabic)" % key,
              body[:3] == b"\xef\xbb\xbf")
        parsed = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        # header row + one row per record; csv.reader drops the trailing newline
        check("%s.csv parses to %d data row(s)" % (key, counts[key]),
              len(parsed) - 1 == counts[key])

        r = client.get("/people/api/%s.json" % key)
        check("GET /people/api/%s.json -> 200" % key, r.status_code == 200)
        d = json.loads(r.data)
        check("%s.json count == %d and matches its rows" % (key, counts[key]),
              d["count"] == counts[key] == len(d["rows"]) and d["dataset"] == key)
        check("%s.json objects are keyed by the CSV headers" % key,
              all(set(o) == set(d["columns"]) for o in d["rows"]))

    check("unknown CSV key -> 404",
          client.get("/people/export/no-such-dataset.csv").status_code == 404)
    check("unknown JSON key -> 404",
          client.get("/people/api/no-such-dataset.json").status_code == 404)

# A signed-out browser must not be able to pull payroll data. Deliberately OUTSIDE
# the app_context above: a test request reuses an already-pushed app context, so
# current_user()'s per-context g cache would still hold the admin from the requests
# above and every anonymous request would look authorised.
anon = app.test_client()
check("export requires a login (no session -> redirected to /login)",
      anon.get("/people/export/incentive.csv").status_code == 302)

print("\nALL %d CHECKS PASSED  (%s)"
      % (len(OK), ", ".join("%s=%d" % (k, counts[k]) for k in KEYS)))
