"""
Quality export self-check — every export key, direct and over HTTP.

Runs against a THROWAWAY SQLite DB in a temp dir: Config.DB_PATH is hardcoded to
the repo, so it is overridden before create_app() or this test would seed demo
rows into the live repo database.

    python app/quality/tests_export.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wf_")); os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"; os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"; os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
import config                                                    # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"
from app import create_app                                       # noqa: E402
from app.quality import services as svc                          # noqa: E402
from app.routes import quality as qroutes                        # noqa: E402

KEYS = ["inspections", "defects", "defect-pareto", "dhu-by-section", "dhu-by-order"]
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'}  {label}" + ("" if ok else f"   got {got!r} want {want!r}"))
    if not ok:
        fails.append(label)


app = create_app()
if "quality" not in app.blueprints:          # not spliced into app/__init__ yet
    app.register_blueprint(qroutes.bp)

print("== 1. export_dataset() shape ==")
counts = {}
with app.app_context():
    for k in KEYS:
        headers, rows = svc.export_dataset(k)
        rows = list(rows)
        counts[k] = len(rows)
        check(f"{k}: headers is a list", isinstance(headers, list), True)
        check(f"{k}: headers are all strings", all(isinstance(h, str) for h in headers), True)
        check(f"{k}: every row matches the header width",
              sorted({len(r) for r in rows}) or [len(headers)], [len(headers)])
        bad = [(i, j, v) for i, r in enumerate(rows) for j, v in enumerate(r)
               if v is None or isinstance(v, (dict, list, tuple, set))]
        check(f"{k}: no None/dict/list cells", bad, [])
        print(f"  ..    {k}: {len(rows)} rows x {len(headers)} cols")
    check("unknown key -> (None, None)", svc.export_dataset("nope"), (None, None))

print("== 2. both routes over HTTP, as admin ==")
with app.app_context():
    from app.db import get_db                                     # noqa: E402
    c = get_db()
    admin = c.execute("SELECT id, session_epoch FROM users WHERE username='admin'").fetchone()
    c.close()
cl = app.test_client()
with cl.session_transaction() as s:
    s["uid"] = admin["id"]
    s["ep"] = admin["session_epoch"] or 0

for k in KEYS:
    r = cl.get(f"/quality/export/{k}.csv")
    check(f"GET /quality/export/{k}.csv", r.status_code, 200)
    check(f"{k}.csv starts with the UTF-8 BOM", r.data[:3], b"\xef\xbb\xbf")
    check(f"{k}.csv is a text/csv attachment",
          r.mimetype == "text/csv" and "attachment" in (r.headers.get("Content-Disposition") or ""),
          True)
    j = cl.get(f"/quality/api/{k}.json")
    check(f"GET /quality/api/{k}.json", j.status_code, 200)
    body = json.loads(j.data)
    check(f"{k}.json count matches export_dataset()", body["count"], counts[k])
    check(f"{k}.json rows are keyed by the CSV headers",
          all(set(row) <= set(body["columns"]) for row in body["rows"]), True)

check("GET an unknown csv key -> 404", cl.get("/quality/export/nope.csv").status_code, 404)
check("GET an unknown json key -> 404", cl.get("/quality/api/nope.json").status_code, 404)

# The Export links are url_for()s: a wrong endpoint or key 500s the page, not the link.
check("dashboard still renders with its export links", cl.get("/quality/").status_code, 200)
check("register still renders with its export links",
      cl.get("/quality/inspections").status_code, 200)

print("== 3. the numbers agree with the module's own reads ==")
with app.app_context():
    d = svc.dashboard()
    _, insp = svc.export_dataset("inspections")
    check("inspections export covers every inspection", len(list(insp)), d["inspections"])
    hs, sec = svc.export_dataset("dhu-by-section")
    dhu_col = hs.index("DHU")
    check("by-section DHU matches the dashboard panel",
          [r[dhu_col] for r in sec], [s["dhu"] for s in d["sections"]])
    hp, par = svc.export_dataset("defect-pareto")
    par = list(par)
    check("pareto cumulative % ends at 100 (or is empty)",
          par[-1][hp.index("Cumulative %")] if par else 100.0, 100.0)

print("\n" + ("ALL GREEN" if not fails else f"{len(fails)} FAILURES: {fails}"))
sys.exit(1 if fails else 0)
