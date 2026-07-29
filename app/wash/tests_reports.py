"""
Self-verification for the wash reports declared in app/wash/reports.py.

Run it:  PYTHONIOENCODING=utf-8 python app/wash/tests_reports.py

Plain asserts, no framework, throwaway SQLite database in a temp directory
(config.Config.DB_PATH points at <repo>/platform.db, so overriding it is
mandatory — a test must never write into the repo).

The assertion that matters is the arithmetic; every expected number is
hand-computed in the comment beside it. The load-per-recipe assertions exist to
catch a fan-out: the planned-minutes join is pre-grouped per version, and joining
wsh_steps raw would multiply every batch by its step count.
"""
import csv
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wshrpt_"))
os.chdir(TMP)
REPO = Path(r"D:\TC platform\tc-platform-render")
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                              # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                 # noqa: E402
from app.db import get_db                                  # noqa: E402
from app.routes import reports_hub                         # noqa: E402
from app.services import reporting as R                    # noqa: E402

KEYS = ["wash_batches", "wash_recipe_throughput"]
PERM = "wsh_view"
PAGES = ["/wash/", "/wash/recipes", "/wash/batches", "/wash/labdips"]
DAY = "2026-06-10"
TOKEN = "RPT-B1"

PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


def near(a, b, tol=0.05):
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


app = create_app()
if "reports_hub" not in app.blueprints:      # the orchestrator registers it in app/__init__
    app.register_blueprint(reports_hub.bp)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def wipe():
    conn = get_db()
    conn.execute("DELETE FROM wsh_batches")
    conn.commit()
    conn.close()


def recipe(conn, code, wash_type, step_minutes):
    """A recipe + one approved version whose steps sum to the planned cycle."""
    cur = conn.execute("INSERT INTO wsh_recipes (code, name, wash_type) VALUES (?,?,?)",
                       (code, code + " recipe", wash_type))
    rid = cur.lastrowid
    cur = conn.execute("INSERT INTO wsh_versions (recipe_id, version, status) VALUES (?,?,?)",
                       (rid, 1, "approved"))
    vid = cur.lastrowid
    for i, mins in enumerate(step_minutes, start=1):
        conn.execute("INSERT INTO wsh_steps (version_id, step_no, operation, minutes, "
                     "liquor_ratio, load_kg) VALUES (?,?,?,?,?,?)",
                     (vid, i, "rinse", mins, 6, 100))
    return rid, vid


def batch(conn, no, rid, vid, machine, load, act_min, deviation=None, hour="07:00"):
    conn.execute("INSERT INTO wsh_batches (batch_no, recipe_id, version_id, machine, "
                 "load_kg, act_minutes, act_temp_c, started_at, status, deviation, "
                 "deviation_alerted) VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                 (no, rid, vid, machine, load, act_min, 50, f"{DAY} {hour}", "done",
                  deviation))


def seed_full():
    """Two recipes; W1 has THREE steps (10+20+30 = 60 planned min), W2 has two (5+5 = 10).

    b1 W1 Washer 1 100 kg  66 min          -> time var 100*66/60 - 100 = +10.0 %
    b2 W1 Washer 2  50 kg  90 min, flagged -> time var                 = +50.0 %
    b3 W2 Washer 1 200 kg  10 min          -> time var                 =   0.0 %
    batches 3 ; load 350 kg ; avg cycle (66+90+10)/3 = 55.3 min
    deviations 1 ; on-spec 2/3 = 66.7 %
    per recipe: W1 v1 -> 2 batches, 150 kg (NOT 450: 3 steps must not treble it),
                         avg 78 min vs 60 planned = +30.0 %
                W2 v1 -> 1 batch, 200 kg, avg 10 vs 10 planned = 0.0 %
    """
    conn = get_db()
    r1, v1 = recipe(conn, "RPT-W1", "stone", [10, 20, 30])
    r2, v2 = recipe(conn, "RPT-W2", "rinse", [5, 5])
    batch(conn, TOKEN, r1, v1, "Washer 1", 100, 66)
    batch(conn, "RPT-B2", r1, v1, "Washer 2", 50, 90, "time 90 vs 60 min", "09:00")
    batch(conn, "RPT-B3", r2, v2, "Washer 1", 200, 10, None, "11:00")
    conn.commit()
    conn.close()
    return r1, v1, r2, v2


def nobody_id():
    conn = get_db()
    r = conn.execute("SELECT id FROM users WHERE username='rpt_nobody'").fetchone()
    if not r:
        conn.execute("INSERT INTO users (username, password_hash, full_name, email, role, "
                     "is_active, session_epoch) VALUES (?,?,?,?,?,?,?)",
                     ("rpt_nobody", "x", "No Body", "nobody@tc.test", "normal_user", 1, 0))
        conn.commit()
        r = conn.execute("SELECT id FROM users WHERE username='rpt_nobody'").fetchone()
    conn.close()
    return r["id"]


def client(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


def csv_rows(payload):
    rdr = csv.reader(io.StringIO(payload.decode("utf-8-sig")))
    head = next(rdr, [])
    return [dict(zip(head, r)) for r in rdr]


def kpis(key, args=None):
    with app.app_context():
        res = R.run(R.get(key), args or {})
    return {k["key"]: k["value"] for k in res["kpis"]}, res


with app.app_context():
    NOBODY = nobody_id()

ADMIN = client(1)
NO = client(NOBODY)

print("\n=== Wash reports ===")

for k in KEYS:
    spec = R.get(k)
    ok(f"registered {k}", spec is not None and spec["perm"] == PERM, "" if spec else "missing")

# --- empty ------------------------------------------------------------------
with app.app_context():
    wipe()
for k in KEYS:
    for suffix, mime in (("", None), (".csv", None), (".pdf", "application/pdf")):
        r = ADMIN.get(f"/reporting/{k}{suffix}")
        ok(f"{k}{suffix} empty 200", r.status_code == 200 and (not mime or r.mimetype == mime),
           f"{r.status_code} {r.mimetype}")
kp, _ = kpis("wash_batches")
ok("empty KPIs are 0 not NaN", kp["batches"] == 0 and kp["clean_pct"] == 0, str(kp))

# --- single row + NULL-heavy row --------------------------------------------
with app.app_context():
    conn = get_db()
    r1, v1 = recipe(conn, "RPT-SOLO", "rinse", [30])
    batch(conn, TOKEN, r1, v1, "Washer 1", 120, 33)
    conn.commit()
    conn.close()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} single-row page 200", r.status_code == 200, str(r.status_code))
kp, res = kpis("wash_batches")
ok("single batch: 120 kg", near(kp["load_kg"], 120), str(kp["load_kg"]))
ok("single batch: time var +10.0 (33 vs 30)", near(res["rows"][0]["time_var"], 10.0),
   str(res["rows"][0]["time_var"]))
ok("single batch: on-spec 100 %", near(kp["clean_pct"], 100.0), str(kp["clean_pct"]))
with app.app_context():
    conn = get_db()
    # a batch with no recipe, no version steps and no actuals: every join NULL
    conn.execute("INSERT INTO wsh_batches (batch_no, version_id, started_at) VALUES (?,?,?)",
                 ("RPT-NULL", 999999, f"{DAY} 12:00"))
    conn.commit()
    conn.close()
for k in KEYS:
    r = ADMIN.get(f"/reporting/{k}")
    ok(f"{k} NULL-heavy row page 200", r.status_code == 200, str(r.status_code))
kp, _ = kpis("wash_batches")
ok("NULL row does not corrupt kilos", near(kp["load_kg"], 120), str(kp["load_kg"]))

# --- full seed, hand-computed ----------------------------------------------
with app.app_context():
    wipe()
    seed_full()

kp, res = kpis("wash_batches")
ok("batches 3", near(kp["batches"], 3), str(kp["batches"]))
ok("load 350 kg (100+50+200)", near(kp["load_kg"], 350), str(kp["load_kg"]))
ok("avg cycle 55.3 min ((66+90+10)/3)", near(kp["avg_min"], 55.3), str(kp["avg_min"]))
ok("deviations 1", near(kp["deviations"], 1), str(kp["deviations"]))
ok("on-spec 66.7 % (2 of 3)", near(kp["clean_pct"], 66.7), str(kp["clean_pct"]))
rows = {r["batch_no"]: r for r in res["rows"]}
ok("b1 plan 60 min from its 3 steps", near(rows[TOKEN]["plan_min"], 60), str(rows[TOKEN]["plan_min"]))
ok("b1 time var +10.0 (66 vs 60)", near(rows[TOKEN]["time_var"], 10.0),
   str(rows[TOKEN]["time_var"]))
ok("b2 time var +50.0 (90 vs 60)", near(rows["RPT-B2"]["time_var"], 50.0),
   str(rows["RPT-B2"]["time_var"]))
ok("b3 time var 0.0 (10 vs 10)", near(rows["RPT-B3"]["time_var"], 0.0),
   str(rows["RPT-B3"]["time_var"]))

kp, res = kpis("wash_recipe_throughput")
ok("2 recipe versions run", near(kp["versions"], 2), str(kp["versions"]))
ok("throughput: batches 3", near(kp["batches"], 3), str(kp["batches"]))
ok("throughput: load 350 kg", near(kp["load_kg"], 350), str(kp["load_kg"]))
byr = {r["recipe"]: r for r in res["rows"]}
ok("W1 load 150 kg — 3 steps did NOT treble it", near(byr["RPT-W1"]["load_kg"], 150),
   str(byr["RPT-W1"]["load_kg"]))
ok("W1 batches 2", near(byr["RPT-W1"]["batches"], 2), str(byr["RPT-W1"]["batches"]))
ok("W1 avg 78.0 min ((66+90)/2)", near(byr["RPT-W1"]["avg_min"], 78.0),
   str(byr["RPT-W1"]["avg_min"]))
ok("W1 time var +30.0 (78 vs 60)", near(byr["RPT-W1"]["time_var"], 30.0),
   str(byr["RPT-W1"]["time_var"]))
ok("W2 load 200 kg", near(byr["RPT-W2"]["load_kg"], 200), str(byr["RPT-W2"]["load_kg"]))

body = csv_rows(ADMIN.get("/reporting/wash_batches.csv").data)
byb = {r["Batch"]: r for r in body}
ok("csv: b2 load 50", near(byb["RPT-B2"]["Load (kg)"], 50), byb["RPT-B2"]["Load (kg)"])
ok("csv: b2 deviation text carried", "90" in byb["RPT-B2"]["Deviation"],
   byb["RPT-B2"]["Deviation"])

kp, _ = kpis("wash_batches", {"machine": "Washer 1"})
ok("filter machine=Washer 1: 300 kg (100+200)", near(kp["load_kg"], 300), str(kp["load_kg"]))
kp, _ = kpis("wash_batches", {"wash_type": "stone"})
ok("filter wash_type=stone: 150 kg", near(kp["load_kg"], 150), str(kp["load_kg"]))
kp, _ = kpis("wash_batches", {"period": "custom", "from": DAY, "to": DAY})
ok("date window keeps the same 350 kg", near(kp["load_kg"], 350), str(kp["load_kg"]))

# --- permission on the page AND all three exports ---------------------------
for k in KEYS:
    for url in (f"/reporting/{k}", f"/reporting/{k}.csv", f"/reporting/{k}.xlsx",
                f"/reporting/{k}.pdf"):
        r = NO.get(url)                       # RAW status: no follow_redirects
        leaked = TOKEN.encode() in r.data
        ok(f"no-perm blocked {url}", r.status_code in (302, 403) and not leaked,
           f"{r.status_code} leaked={leaked}")
    r = ADMIN.get(f"/reporting/{k}.xlsx")
    ok(f"{k} xlsx 200", r.status_code == 200 and "spreadsheet" in r.mimetype,
       f"{r.status_code} {r.mimetype}")

# --- i18n -------------------------------------------------------------------
DICTS = {lg: json.loads((REPO / "app/static/i18n" / f"{lg}.json").read_text("utf-8"))
         for lg in ("en", "ar", "tr")}
NEW_KEYS = set()   # this lane adds NO i18n key: the Reports link reuses reports.title
for page in PAGES:
    # the RENDERED page, not the template source: keys built in Jinja only exist
    # once rendered.
    body = ADMIN.get(page).data.decode("utf-8", "replace")
    keys = set(re.findall(r'data-i18n="([^"]+)"', body))
    for lg, d in DICTS.items():
        missing = sorted(k for k in keys if k not in d and k not in NEW_KEYS)
        ok(f"{page} keys resolve in {lg}", not missing, ", ".join(missing[:6]))
for lg in ("en", "ar", "tr"):
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lg,))
        conn.commit()
        conn.close()
    for k in KEYS:
        r = ADMIN.get(f"/reporting/{k}")
        ok(f"{k} page 200 with lang_pref={lg}", r.status_code == 200, str(r.status_code))
        r = ADMIN.get(f"/reporting/{k}.csv")
        ok(f"{k} csv 200 with lang_pref={lg}", r.status_code == 200, str(r.status_code))

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + "; ".join(FAIL))
sys.exit(1 if FAIL else 0)
