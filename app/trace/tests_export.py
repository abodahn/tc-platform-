"""
Standalone check of the trace module's dataset export: services.export_dataset()
plus the two routes it feeds. Run it directly:

    python app/trace/tests_export.py

It builds a THROWAWAY SQLite DB in a temp dir. Config.DB_PATH is hardcoded to
<repo>/platform.db, so it is overridden BEFORE create_app() — otherwise this
test writes into the repo's real database.
"""
import csv
import io
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = r"D:\TC platform\tc-platform-render"

TMP = Path(tempfile.mkdtemp(prefix="wf_trace_export_"))
os.chdir(TMP)
sys.path.insert(0, REPO)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                    # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                       # noqa: E402
from app.db import get_db                                        # noqa: E402
from app.trace import services as svc                            # noqa: E402

app = create_app()

KEYS = ["partners", "lots", "certificates", "passports", "esg"]

fail = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fail.append(msg)


print(f"temp db: {config.Config.DB_PATH}")

# --- 1. the contract: (headers, rows), rectangular, no Python objects -------
print("\n[1] export_dataset(key) shape")
for k in KEYS:
    headers, rows = svc.export_dataset(k)
    check(isinstance(headers, list) and headers, f"{k}: headers is a non-empty list")
    rows = list(rows)
    check(all(len(r) == len(headers) for r in rows),
          f"{k}: every one of {len(rows)} rows has {len(headers)} cells")
    bad = [(i, j, type(c).__name__) for i, r in enumerate(rows)
           for j, c in enumerate(r) if c is None or isinstance(c, (dict, list, set))]
    check(not bad, f"{k}: no None/dict/list cells{'' if not bad else ' -> ' + str(bad[:3])}")

headers, rows = svc.export_dataset("no-such-dataset")
check(headers is None and rows is None, "unknown key -> (None, None)")

# --- 2. the routes, as an admin --------------------------------------------
print("\n[2] routes via test_client (admin session)")
conn = get_db()
try:
    u = conn.execute("SELECT id, session_epoch FROM users WHERE username='admin'").fetchone()
finally:
    conn.close()
check(u is not None, "admin user exists in the seeded DB")

with app.test_client() as c:
    with c.session_transaction() as s:
        s["uid"] = u["id"]
        # "ep" is the SESSION EPOCH, not permissions: current_user() clears any
        # session whose ep differs from the user row, so a wrong value logs us out.
        s["ep"] = u["session_epoch"] or 0
    for k in KEYS:
        r = c.get(f"/trace/export/{k}.csv")
        check(r.status_code == 200, f"GET /trace/export/{k}.csv -> {r.status_code}")
        body = r.data
        check(body[:3] == b"\xef\xbb\xbf", f"{k}.csv starts with the UTF-8 BOM")
        h, rws = svc.export_dataset(k)
        first = next(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        check(first == [str(x) for x in h], f"{k}.csv header row == export_dataset headers")

        r = c.get(f"/trace/api/{k}.json")
        check(r.status_code == 200, f"GET /trace/api/{k}.json -> {r.status_code}")
        d = json.loads(r.data)
        check(d["dataset"] == k and d["columns"] == [str(x) for x in h],
              f"{k}.json names the dataset and its {len(h)} columns")
        check(d["count"] == len(list(rws)),
              f"{k}.json count={d['count']} matches export_dataset()")

    r = c.get("/trace/export/no-such-dataset.csv")
    check(r.status_code == 404, f"unknown csv key -> {r.status_code}")
    r = c.get("/trace/api/no-such-dataset.json")
    check(r.status_code == 404, f"unknown json key -> {r.status_code}")

# --- 3. a spot check that the numbers are the real ones --------------------
print("\n[3] content spot checks")
h, rows = svc.export_dataset("certificates")
rows = list(rows)
check(any(r[h.index("Status")] == "expired" for r in rows),
      "certificates: the seeded expired cert exports its DERIVED status")
h, rows = svc.export_dataset("lots")
rows = list(rows)
check(any(str(r[h.index("Made From Lot")]).startswith("LOT-") for r in rows),
      "lots: parent chain exports the parent's LOT REF, not its id")

print("\nRESULT: " + ("ALL GREEN" if not fail else f"{len(fail)} FAILURE(S)"))
for f in fail:
    print("  - " + f)
sys.exit(1 if fail else 0)
