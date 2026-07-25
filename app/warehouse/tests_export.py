"""
Warehouse export self-check — run directly:  python app/warehouse/tests_export.py

Config.DB_PATH is hardcoded to <repo>/platform.db, so this points it at a throwaway
temp DB first: a test that seeds the repo's own database pollutes the working tree.

Checks every export key twice — the service contract (shape, no Python objects
leaking into a cell) and both HTTP routes end to end with an admin session, because
a clean (headers, rows) tuple still ships broken if the CSV loses its BOM (Excel
then renders Arabic as mojibake) or the JSON count disagrees with the row count.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wh_export_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                        # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"
from app import create_app                           # noqa: E402
from app.db import get_db                            # noqa: E402
from app.warehouse import services as svc            # noqa: E402

KEYS = ["materials", "rolls", "movements", "finished-goods", "issues-by-order"]

app = create_app()


def seed_an_issue():
    """The demo seed never issues anything, so issues-by-order would be vacuously
    green on 0 rows. Cut some fabric for a real order first."""
    conn = get_db()
    try:
        o = conn.execute("SELECT id FROM ord_orders ORDER BY id LIMIT 1").fetchone()
        m = conn.execute("SELECT id FROM wh_materials WHERE code='FAB-JER-WHT'").fetchone()
    finally:
        conn.close()
    assert o and m, "expected the seeded demo order + material"
    ok, msg, _ = svc.issue_to_order(o["id"], m["id"], 120, {"username": "admin"})
    assert ok, f"issue failed: {msg}"
    print(f"  issued 120 m to order {o['id']} ({msg})")


def check_service():
    counts = {}
    for key in KEYS:
        headers, rows = svc.export_dataset(key)
        assert isinstance(headers, list) and headers, f"{key}: bad headers"
        assert all(isinstance(h, str) and h for h in headers), f"{key}: header not a string"
        rows = list(rows)
        for i, r in enumerate(rows):
            assert not isinstance(r, (str, bytes)), f"{key} row {i}: row is a string"
            assert len(r) == len(headers), \
                f"{key} row {i}: {len(r)} cells for {len(headers)} headers"
            for j, cell in enumerate(r):
                assert cell is not None, f"{key} row {i} col {headers[j]}: None"
                assert not isinstance(cell, (dict, list, tuple, set)), \
                    f"{key} row {i} col {headers[j]}: {type(cell).__name__}"
        counts[key] = len(rows)
        print(f"  export_dataset({key!r}): {len(headers)} cols x {len(rows)} rows OK")
    assert svc.export_dataset("nope") == (None, None), "unknown key must give (None, None)"
    print("  export_dataset('nope') -> (None, None) OK")
    return counts


def admin_session(client):
    """Log in as the seeded admin the way the app itself does: uid + the user's
    session_epoch in "ep" (a stale epoch makes current_user() clear the session, which
    would show up here as a 302 to the login page, not as a permission error)."""
    conn = get_db()
    try:
        u = conn.execute("SELECT id, username, session_epoch FROM users "
                         "WHERE username='admin'").fetchone()
    finally:
        conn.close()
    assert u, "no admin user seeded"
    with client.session_transaction() as s:
        s["uid"] = u["id"]
        s["ep"] = u["session_epoch"] or 0
    return u["username"]


def check_routes(counts):
    client = app.test_client()
    who = admin_session(client)
    print(f"  session as admin {who!r}")
    for key, n in counts.items():
        r = client.get(f"/warehouse/export/{key}.csv")
        assert r.status_code == 200, f"csv {key}: HTTP {r.status_code}"
        body = r.data
        assert body.startswith(b"\xef\xbb\xbf"), f"csv {key}: missing UTF-8 BOM"
        lines = body.decode("utf-8-sig").strip().splitlines()
        print(f"  GET /warehouse/export/{key}.csv -> 200, BOM, {len(lines)} lines")

        r = client.get(f"/warehouse/api/{key}.json")
        assert r.status_code == 200, f"json {key}: HTTP {r.status_code}"
        d = json.loads(r.data)
        assert d["dataset"] == key and d["count"] == n, \
            f"json {key}: count {d['count']} != {n}"
        assert len(d["rows"]) == n, f"json {key}: {len(d['rows'])} rows != {n}"
        if n:
            assert set(d["rows"][0]) == set(d["columns"]), f"json {key}: key/column mismatch"
        print(f"  GET /warehouse/api/{key}.json  -> 200, count={d['count']} OK")

    for path in ("/warehouse/export/nope.csv", "/warehouse/api/nope.json"):
        r = client.get(path)
        assert r.status_code == 404, f"{path}: HTTP {r.status_code}, expected 404"
        print(f"  GET {path} -> 404 OK")

    # The pages carrying the new download links: a bad url_for() there is a 500 on a
    # page 261 people open, and no route test would ever see it.
    for path in ("/warehouse/", "/warehouse/materials", "/warehouse/rolls",
                 "/warehouse/fg", "/warehouse/issue"):
        r = client.get(path)
        assert r.status_code == 200, f"{path}: HTTP {r.status_code}"
        assert b"/warehouse/export/" in r.data, f"{path}: export link missing"
        print(f"  GET {path} -> 200, export link present OK")


if __name__ == "__main__":
    print(f"temp db: {config.Config.DB_PATH}")
    print("setup:")
    seed_an_issue()
    print("service contract:")
    counts = check_service()
    assert counts["issues-by-order"], "issues-by-order came back empty — nothing was tested"
    print("routes:")
    check_routes(counts)
    print("ALL GREEN")
