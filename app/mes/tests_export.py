"""
Verification of the MES dataset exports (app/mes/services.export_dataset + routes).

    python app/mes/tests_export.py

Checks the contract, not the numbers (tests_adversarial.py owns the arithmetic):
every key returns a list of headers and rectangular rows of plain scalars, both
routes serve 200 with a BOM'd CSV / parsable JSON of the same row count, and an
unknown key is a 404 rather than a 500. Runs against a throwaway DB in the OS
temp dir — never the repo platform.db.
"""
import json, os, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="mes_exp_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                  # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                     # noqa: E402
from app.mes import services as svc                            # noqa: E402
from app.mes.schema import create_and_seed                     # noqa: E402

KEYS = ["hourly", "downtime", "oee-by-line", "bundles", "bundle-moves", "order-recon"]
FAILS = []
RUN = [0]


def ok(name, cond, detail=""):
    RUN[0] += 1
    d = str(detail)
    if cond:
        print("  PASS  " + name + ("  -> " + d if d else ""))
    else:
        FAILS.append(name)
        print("  FAIL  " + name + "  -> " + d)


app = create_app()
with app.app_context():
    from app.db import get_db
    conn = get_db()
    create_and_seed(conn)
    u = conn.execute("SELECT id, session_epoch FROM users WHERE username='admin'").fetchone()
    uid, ep = u["id"], (u["session_epoch"] or 0)
    conn.close()

    counts = {}
    for key in KEYS:
        headers, rows = svc.export_dataset(key)
        rows = list(rows or [])
        counts[key] = len(rows)
        ok("%s: headers is a non-empty list" % key,
           isinstance(headers, list) and headers and all(isinstance(h, str) for h in headers),
           "%s cols" % (len(headers) if isinstance(headers, list) else headers))
        ok("%s: every row is rectangular" % key,
           all(len(r) == len(headers) for r in rows),
           "%s rows x %s" % (len(rows), len(headers)))
        bad = [(i, j, v) for i, r in enumerate(rows) for j, v in enumerate(r)
               if v is None or isinstance(v, (dict, list, tuple, set, bytes))]
        ok("%s: no None / dict / list cell" % key, not bad, bad[:3])

    ok("unknown key returns (None, None)", svc.export_dataset("nope") == (None, None))

client = app.test_client()
with client.session_transaction() as s:
    s["uid"] = uid
    s["ep"] = ep

for key in KEYS:
    r = client.get("/mes/export/%s.csv" % key)
    body = r.get_data()
    ok("GET /mes/export/%s.csv -> 200" % key, r.status_code == 200, r.status_code)
    ok("%s.csv starts with the UTF-8 BOM" % key, body[:3] == b"\xef\xbb\xbf", body[:6])
    # header line + one line per row (embedded newlines are only possible in Notes,
    # which the seed leaves empty — a mismatch here would mean a quoted cell, not a bug)
    lines = body.decode("utf-8-sig").strip().splitlines()
    ok("%s.csv has header + %s data lines" % (key, counts[key]),
       len(lines) == counts[key] + 1, len(lines))

    r = client.get("/mes/api/%s.json" % key)
    ok("GET /mes/api/%s.json -> 200" % key, r.status_code == 200, r.status_code)
    j = json.loads(r.get_data(as_text=True))
    ok("%s.json count == %s and dataset/columns echoed" % (key, counts[key]),
       j["count"] == counts[key] == len(j["rows"]) and j["dataset"] == key and j["columns"],
       "%s / %s cols" % (j["count"], len(j["columns"])))

for path in ("/mes/export/nope.csv", "/mes/api/nope.json"):
    ok("GET %s -> 404" % path, client.get(path).status_code == 404,
       client.get(path).status_code)

# The exports are reads: mes_view must be enough, and nothing less must pass.
with app.app_context():
    from app.db import get_db
    conn = get_db()
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                 "session_epoch) VALUES ('mes_exp_nobody','x','No One','operator',1,0)")
    conn.commit()
    nobody = conn.execute("SELECT id FROM users WHERE username='mes_exp_nobody'").fetchone()["id"]
    conn.close()

c2 = app.test_client()
with c2.session_transaction() as s:
    s["uid"] = nobody
    s["ep"] = 0
codes = {p: c2.get(p).status_code for p in ("/mes/export/hourly.csv", "/mes/api/hourly.json")}
ok("a user without mes_view is refused both export routes (403)",
   set(codes.values()) == {403}, codes)

print("\n%s checks, %s failed" % (RUN[0], len(FAILS)))
if FAILS:
    for f in FAILS:
        print("  - " + f)
    sys.exit(1)
print("ALL GREEN")
