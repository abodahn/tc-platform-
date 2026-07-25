"""PLM export self-check — run directly: python app/plm/tests_export.py

Asserts every export key returns a clean rectangle (no dict/list/None cells) and
that both routes serve it: CSV with the UTF-8 BOM Excel needs for Arabic/Turkish,
JSON with a matching count. Unknown keys must 404, not 500.

Config.DB_PATH is hardcoded to the repo, so it is redirected to a temp DB first —
running this must never touch platform.db.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="plm_export_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                  # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                     # noqa: E402
from app.db import get_db                                      # noqa: E402
from app.plm import services as svc                            # noqa: E402

KEYS = ["styles", "techpack-versions", "measurement-specs", "bom", "samples", "summary"]

app = create_app()
fails = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        fails.append(msg)


def _admin_session(c):
    """Sign in without a password: uid + ep is what app.auth reads off the session."""
    conn = get_db()
    try:
        r = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
    finally:
        conn.close()
    with c.session_transaction() as s:
        s["uid"] = r["id"]
        s["ep"] = 0
    return r["id"]


with app.app_context():
    print("--- export_dataset() shape ---")
    for k in KEYS:
        headers, rows = svc.export_dataset(k)
        check(isinstance(headers, list) and headers, f"{k}: headers is a non-empty list")
        rows = list(rows)
        bad_len = [i for i, r in enumerate(rows) if len(r) != len(headers)]
        check(not bad_len, f"{k}: all {len(rows)} row(s) have {len(headers)} cells")
        bad_cell = [(i, j) for i, r in enumerate(rows) for j, v in enumerate(r)
                    if v is None or isinstance(v, (dict, list, tuple, set))]
        check(not bad_cell, f"{k}: no None/dict/list cells (rows={len(rows)})")

    h, r = svc.export_dataset("no-such-key")
    check(h is None and r is None, "unknown key -> (None, None)")

print("\n--- routes ---")
with app.test_client() as c:
    with app.app_context():
        uid = _admin_session(c)
    print(f"  (admin uid={uid})")
    for k in KEYS:
        rv = c.get(f"/plm/export/{k}.csv")
        check(rv.status_code == 200, f"GET /plm/export/{k}.csv -> {rv.status_code}")
        check(rv.data.startswith(b"\xef\xbb\xbf"), f"{k}.csv starts with the UTF-8 BOM")

        rv = c.get(f"/plm/api/{k}.json")
        check(rv.status_code == 200, f"GET /plm/api/{k}.json -> {rv.status_code}")
        try:
            d = json.loads(rv.data)
        except ValueError:
            d = {}
            check(False, f"{k}.json is valid JSON")
        with app.app_context():
            hs, rows = svc.export_dataset(k)
        check(d.get("count") == len(list(rows)) and d.get("columns") == hs
              and len(d.get("rows") or []) == d.get("count"),
              f"{k}.json count={d.get('count')} matches export_dataset()")

    check(c.get("/plm/export/no-such-key.csv").status_code == 404,
          "unknown CSV key -> 404")
    check(c.get("/plm/api/no-such-key.json").status_code == 404,
          "unknown JSON key -> 404")

print("\n--- i18n ---")
from app.plm.i18n import I18N                                  # noqa: E402
tr = I18N.get("plm.action.export")
# the Arabic value is not printed: this console is cp1252 and would raise on it
check(bool(tr) and len(tr) == 3 and all(str(t).strip() for t in tr),
      "plm.action.export has non-blank EN/AR/TR")

print("\nRESULT: " + ("ALL GREEN" if not fails else f"{len(fails)} FAILED: " + "; ".join(fails)))
sys.exit(1 if fails else 0)
