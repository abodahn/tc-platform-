"""
Export contract test for the planning module — services + both routes.

Runs against a THROWAWAY database (never the repo's platform.db) and asserts the
things a CSV/JSON consumer breaks on: every row is as wide as its headers, no cell
is a dict/list/None, the CSV carries the UTF-8 BOM Excel needs for Arabic, the JSON
count matches the row count, and an unknown key is a 404 rather than a 500.

    python app/planning/tests_export.py
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

import config                                       # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                          # noqa: E402

app = create_app()
FAILS = []

KEYS = ["line-capacity", "order-feasibility", "allocations", "daily-load",
        "operation-bulletin"]


def ck(label, cond, extra=""):
    if cond:
        print(f"  ok    {label}")
    else:
        FAILS.append(f"{label} {extra}".strip())
        print(f"  FAIL  {label} {extra}".rstrip())


with app.app_context():
    from app.db import get_db
    from app.planning import services as svc
    from app.planning.schema import create_and_seed

    conn = get_db()
    create_and_seed(conn)
    admin_id = conn.execute(
        "INSERT INTO users (username,password_hash,full_name,role,is_active,created_at) "
        "VALUES (?,?,?,?,1,?)",
        ("exp_admin", "x", "Export Admin", "super_admin", "2026-01-01 00:00:00")).lastrowid
    conn.commit()
    conn.close()

    print("\n[1] export_dataset(key) shape")
    counts = {}
    for k in KEYS:
        headers, rows = svc.export_dataset(k)
        ck(f"{k}: headers is a list", isinstance(headers, list), f"got {type(headers)}")
        rows = list(rows or [])
        counts[k] = len(rows)
        bad_w = [i for i, r in enumerate(rows) if len(r) != len(headers)]
        ck(f"{k}: every row is {len(headers)} wide ({len(rows)} rows)", not bad_w,
           f"ragged rows {bad_w[:5]}")
        bad_c = [(i, j, v) for i, r in enumerate(rows) for j, v in enumerate(r)
                 if v is None or isinstance(v, (dict, list, tuple, set))]
        ck(f"{k}: no None/dict/list cell", not bad_c, f"{bad_c[:3]}")

    print("\n[2] unknown key")
    ck("export_dataset('nope') -> (None, None)", svc.export_dataset("nope") == (None, None))

with app.test_client() as cl:
    with cl.session_transaction() as sess:
        sess["uid"] = admin_id
        sess["ep"] = 0

    print("\n[3] GET /planning/export/<key>.csv")
    for k in KEYS:
        r = cl.get(f"/planning/export/{k}.csv")
        ck(f"{k}.csv -> 200", r.status_code == 200, f"got {r.status_code}")
        body = r.get_data()
        ck(f"{k}.csv starts with the UTF-8 BOM", body.startswith(b"\xef\xbb\xbf"),
           f"starts {body[:6]!r}")
        # header line + one line per row (a cell may hold a newline, so >= not ==)
        ck(f"{k}.csv has a header line", body.decode("utf-8-sig").splitlines()[0].count(",") >= 1)

    print("\n[4] GET /planning/api/<key>.json")
    for k in KEYS:
        r = cl.get(f"/planning/api/{k}.json")
        ck(f"{k}.json -> 200", r.status_code == 200, f"got {r.status_code}")
        d = json.loads(r.get_data(as_text=True))
        ck(f"{k}.json count == {counts[k]} rows", d["count"] == counts[k] == len(d["rows"]),
           f"count {d['count']} vs {counts[k]}")
        ck(f"{k}.json dataset/columns present",
           d["dataset"] == k and isinstance(d["columns"], list) and d["columns"])

    print("\n[5] unknown key + auth")
    ck("/planning/export/nope.csv -> 404", cl.get("/planning/export/nope.csv").status_code == 404)
    ck("/planning/api/nope.json -> 404", cl.get("/planning/api/nope.json").status_code == 404)

anon = app.test_client()
loc = anon.get("/planning/export/line-capacity.csv")
ck("anonymous export redirects to login",
   loc.status_code == 302 and "/login" in (loc.headers.get("Location") or ""),
   f"got {loc.status_code}")

print("\n" + "=" * 62)
if FAILS:
    print(f"{len(FAILS)} FAILURE(S):")
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("ALL EXPORT CHECKS PASSED")
