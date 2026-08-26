"""Self-verification for the PLM reports (app/plm/reports.py).

Run it:  PYTHONIOENCODING=utf-8 python app/plm/tests_reports.py

Plain asserts, no framework, throwaway SQLite database in a temp directory
(config.Config.DB_PATH is hardcoded to <repo>/platform.db, so overriding it is
mandatory — the test must never write into the repo). Every number asserted
below is hand-computed in the comment above it.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="plmrpt_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                            # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

import json                                              # noqa: E402
import re                                                # noqa: E402
from datetime import date, timedelta                     # noqa: E402

from app import create_app                               # noqa: E402
from app.db import get_db                                # noqa: E402
from app.routes import reports_hub                       # noqa: E402
from app.services import reporting as R                  # noqa: E402

REPO = Path(r"D:\TC platform\tc-platform-render")
KEYS = ["plm_sample_pass", "plm_style_pipeline"]
PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


def near(a, b, tol=0.01):
    return a is not None and abs(float(a) - float(b)) <= tol


app = create_app()
# create_app() registers this blueprint itself; registering it twice is a
# ValueError at import, which killed this module before its first check.
if "reports_hub" not in app.blueprints:
    app.register_blueprint(reports_hub.bp)

TODAY = date.today()
D1, D2, D3 = (str(TODAY - timedelta(days=2)), str(TODAY - timedelta(days=1)), str(TODAY))


def wipe(conn):
    for t in ("plm_samples", "plm_techpack_specs", "plm_techpack_sections",
              "plm_techpacks", "plm_bom", "plm_styles"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


with app.app_context():
    conn = get_db()
    wipe(conn)
    if not conn.execute("SELECT id FROM users WHERE username='plm_nobody'").fetchone():
        conn.execute("INSERT INTO users (username,password_hash,full_name,email,role,"
                     "is_active,session_epoch) VALUES (?,?,?,?,?,?,?)",
                     ("plm_nobody", "x", "No Body", "plm_nobody@x.tc", "normal_user", 1, 0))
    NOBODY = conn.execute("SELECT id FROM users WHERE username='plm_nobody'").fetchone()["id"]
    conn.commit()
    conn.close()


def client(uid=1):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


print("\n=== plm reports ===")

# ---------------------------------------------------------------------------
# 1. Empty dataset
# ---------------------------------------------------------------------------
c = client()
for k in KEYS:
    r = c.get(f"/reporting/{k}")
    ok(f"empty dataset: {k} page 200", r.status_code == 200, f"status={r.status_code}")
with app.app_context():
    for k in KEYS:
        res = R.run(R.get(k), {})
        ok(f"empty dataset: {k} KPIs are 0, not an error",
           res["total"] == 0 and not res["error"]
           and all(near(x["value"], 0) for x in res["kpis"]))

# ---------------------------------------------------------------------------
# 2. Single row
# ---------------------------------------------------------------------------
with app.app_context():
    conn = get_db()
    conn.execute("INSERT INTO plm_styles (style_ref,name,buyer,season,category,status,"
                 "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                 ("TST-1", "Solo Tee", "Buyer X", "SS26", "T-shirt", "sampling",
                  str(TODAY), str(TODAY)))
    s1 = conn.execute("SELECT id FROM plm_styles WHERE style_ref='TST-1'").fetchone()["id"]
    conn.execute("INSERT INTO plm_samples (style_id,stage,round_no,revision_no,sent_date,"
                 "verdict,created_at) VALUES (?,?,?,?,?,?,?)",
                 (s1, "proto", 1, 0, D1, "approved", str(TODAY)))
    conn.commit()
    conn.close()
    res = R.run(R.get("plm_sample_pass"), {})
    k = {x["key"]: x for x in res["kpis"]}
    # one approved round -> pass 100%, first-round pass 100%, no division by zero
    ok("single row: pass% = 100 and first-round pass% = 100",
       res["total"] == 1 and near(k["pass_pct"]["value"], 100.0)
       and near(k["first_pass_pct"]["value"], 100.0),
       f"pass={k['pass_pct']['value']} first={k['first_pass_pct']['value']}")
    res = R.run(R.get("plm_style_pipeline"), {})
    ok("single row: pipeline shows 1 style with 1 round",
       res["total"] == 1 and res["rows"][0]["rounds"] == 1 and res["rows"][0]["versions"] == 0)

# ---------------------------------------------------------------------------
# 3. Full fixture + hand-computed arithmetic
# ---------------------------------------------------------------------------
with app.app_context():
    conn = get_db()
    wipe(conn)
    for ref, name, buyer, season, status in (
            ("TST-1", "Crew Tee", "Buyer X", "SS26", "sampling"),
            ("TST-2", "Denim Short", "Buyer Y", "SS26", "approved"),
            ("TST-3", "Fleece Hoodie", "Buyer X", "AW26", "development")):
        conn.execute("INSERT INTO plm_styles (style_ref,name,buyer,season,category,status,"
                     "merchandiser,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (ref, name, buyer, season, "Other", status, "M. Said",
                      str(TODAY), str(TODAY)))
    sid = {r["style_ref"]: r["id"] for r in
           conn.execute("SELECT id, style_ref FROM plm_styles").fetchall()}
    for ref, stage, rnd, sent, verdict in (
            ("TST-1", "proto", 1, D1, "approved"),
            ("TST-1", "fit", 1, D2, "revise"),
            ("TST-1", "fit", 2, D3, "approved"),
            ("TST-1", "pp", 1, D3, "pending"),
            ("TST-2", "proto", 1, D1, "rejected"),
            ("TST-2", "proto", 2, D2, "rejected")):
        conn.execute("INSERT INTO plm_samples (style_id,stage,round_no,revision_no,sent_date,"
                     "verdict,created_at) VALUES (?,?,?,?,?,?,?)",
                     (sid[ref], stage, rnd, 0, sent, verdict, str(TODAY)))
    for ref, versions in (("TST-1", 2), ("TST-2", 1)):
        for v in range(1, versions + 1):
            conn.execute("INSERT INTO plm_techpacks (style_id,version,change_note,"
                         "published_by,published_at) VALUES (?,?,?,?,?)",
                         (sid[ref], v, "note", "test", str(TODAY)))
    conn.commit()
    conn.close()

with app.app_context():
    res = R.run(R.get("plm_sample_pass"), {})
    k = {x["key"]: x for x in res["kpis"]}
    rows = {r["style_ref"]: r for r in res["rows"]}
    # TST-1: 4 rounds, 2 approved, 1 revise, 1 pending -> decided 3 -> pass 2/3 = 66.67%
    # TST-2: 2 rounds, 0 approved, 2 rejected          -> decided 2 -> pass 0%
    # TST-3 has no sample round at all and must NOT appear (inner join)
    ok("samples: 2 styles have rounds (the third is excluded)", res["total"] == 2,
       f"total={res['total']}")
    ok("samples: TST-1 = 4 rounds, 2 approved, 1 failed, 1 pending, pass 66.67",
       near(rows["TST-1"]["rounds"], 4) and near(rows["TST-1"]["approved"], 2)
       and near(rows["TST-1"]["failed"], 1) and near(rows["TST-1"]["pending"], 1)
       and near(rows["TST-1"]["pass_pct"], 66.6667), f"{rows.get('TST-1')}")
    ok("samples: TST-2 pass% = 0 (2 rejected, none approved)",
       near(rows["TST-2"]["pass_pct"], 0.0), f"{rows.get('TST-2')}")
    ok("samples: 6 rounds sent in total", near(k["rounds"]["value"], 6))
    # overall pass = approved 2 / (approved 2 + failed 3) = 40%
    ok("samples: overall pass% = 2/5 = 40", near(k["pass_pct"]["value"], 40.0),
       f"={k['pass_pct']['value']}")
    # first round only: approved = TST-1 proto -> 1 ; decided = TST-1 proto, TST-1 fit r1,
    # TST-2 proto r1 = 3  (TST-1 pp r1 is pending, so it is not decided) -> 33.33%
    ok("samples: first-round pass% = 1/3 = 33.33",
       near(k["first_pass_pct"]["value"], 33.3333), f"={k['first_pass_pct']['value']}")
    ok("samples: failed = 3, pending = 1",
       near(k["failed"]["value"], 3) and near(k["pending"]["value"], 1))
    ok("samples: worst style sorts first", res["rows"][0]["style_ref"] == "TST-2",
       f"{[r['style_ref'] for r in res['rows']]}")
    ch = res["chart"]
    # pareto: TST-2 2 failed (66.7% cumulative), TST-1 1 failed (100%)
    ok("samples chart: pareto 2 then 1, cumulative 66.7 then 100",
       ch and ch["labels"] == ["TST-2", "TST-1"] and ch["values"] == [2, 1]
       and near(ch["cumulative"][0], 66.7, 0.1) and near(ch["cumulative"][1], 100.0),
       f"{ch and (ch['labels'], ch['values'], ch['cumulative'])}")

    f = R.run(R.get("plm_sample_pass"), {"stage": "fit"})
    # only TST-1 has fit rounds: 2 rounds, 1 approved, 1 revise -> pass 50%
    ok("samples filter stage=fit -> 1 style, 2 rounds, pass 50",
       f["total"] == 1 and near(f["rows"][0]["rounds"], 2)
       and near(f["rows"][0]["pass_pct"], 50.0), f"{f['rows']}")
    f = R.run(R.get("plm_sample_pass"), {"buyer": "Buyer Y"})
    ok("samples filter buyer -> 1 style", f["total"] == 1)

    res = R.run(R.get("plm_style_pipeline"), {})
    k = {x["key"]: x for x in res["kpis"]}
    rows = {r["style_ref"]: r for r in res["rows"]}
    ok("pipeline: 3 styles", res["total"] == 3, f"total={res['total']}")
    # TST-1: 2 tech-pack versions, 4 sample rounds, 1 pending
    ok("pipeline: TST-1 = 2 versions, 4 rounds, 1 pending",
       rows["TST-1"]["versions"] == 2 and rows["TST-1"]["rounds"] == 4
       and rows["TST-1"]["pending"] == 1, f"{rows.get('TST-1')}")
    ok("pipeline: TST-3 has no versions and no rounds",
       rows["TST-3"]["versions"] == 0 and rows["TST-3"]["rounds"] == 0)
    # approved + in_production = 1 (TST-2) ; development + sampling = 2
    ok("pipeline: cleared for bulk = 1, in development/sampling = 2",
       near(k["bulk"]["value"], 1) and near(k["developing"]["value"], 2))
    ok("pipeline: dropped = 0", near(k["dropped"]["value"], 0))
    ch = res["chart"]
    ok("pipeline chart: one bar per status, 3 styles in total",
       ch and sum(ch["values"]) == 3 and len(ch["labels"]) == 3,
       f"{ch and list(zip(ch['labels'], ch['values']))}")
    f = R.run(R.get("plm_style_pipeline"), {"status": "approved"})
    ok("pipeline filter status=approved -> 1 style", f["total"] == 1)

# ---------------------------------------------------------------------------
# 4. Permission — page AND every export format
# ---------------------------------------------------------------------------
nob = client(NOBODY)
for k in KEYS:
    r = nob.get(f"/reporting/{k}")
    ok(f"perm: {k} page denied without plm_view",
       r.status_code in (302, 403) and b"TST-1" not in r.get_data(),
       f"status={r.status_code}")
    for fmt in ("csv", "xlsx", "pdf"):
        r = nob.get(f"/reporting/{k}.{fmt}")
        ok(f"perm: {k}.{fmt} denied without plm_view",
           r.status_code in (302, 403) and b"TST-1" not in r.get_data(),
           f"status={r.status_code}")

c = client()
for k in KEYS:
    for fmt, mime in (("csv", "text/csv"), ("xlsx", "application/"), ("pdf", "application/pdf")):
        r = c.get(f"/reporting/{k}.{fmt}")
        ok(f"export: {k}.{fmt} 200 for a permitted user",
           r.status_code == 200 and mime in r.mimetype and len(r.get_data()) > 0,
           f"status={r.status_code} mime={r.mimetype}")
csv_body = c.get("/reporting/plm_sample_pass.csv").get_data().decode("utf-8-sig")
ok("export: CSV carries the computed rows", "TST-1" in csv_body and "TST-2" in csv_body)

# ---------------------------------------------------------------------------
# 5. i18n — module page and report pages in en / ar / tr
# ---------------------------------------------------------------------------
DICT = {lg: json.loads((REPO / "app/static/i18n" / f"{lg}.json").read_text(encoding="utf-8"))
        for lg in ("en", "ar", "tr")}
HUB_KEYS = set(re.findall(
    r'data-i18n="([^"]+)"',
    (REPO / "app/templates/reports/report.html").read_text(encoding="utf-8")
    + (REPO / "app/templates/reports/hub.html").read_text(encoding="utf-8")))

for lg in ("en", "ar", "tr"):
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lg,))
        conn.commit()
        conn.close()
    c = client()
    r = c.get("/plm/")
    keys = set(re.findall(r'data-i18n="([^"]+)"', r.get_data(as_text=True)))
    missing = sorted(x for x in keys if x not in DICT[lg])
    ok(f"i18n[{lg}]: /plm dashboard 200 and every data-i18n key resolves",
       r.status_code == 200 and not missing, f"missing={missing}")
    for key in KEYS:
        r = c.get(f"/reporting/{key}")
        keys = set(re.findall(r'data-i18n="([^"]+)"', r.get_data(as_text=True)))
        mine = sorted(x for x in keys if x not in DICT[lg] and x not in HUB_KEYS)
        ok(f"i18n[{lg}]: {key} page 200, no unresolved key of ours",
           r.status_code == 200 and not mine, f"status={r.status_code} missing={mine}")

with app.app_context():
    conn = get_db()
    conn.execute("UPDATE users SET lang_pref='en' WHERE id=1")
    conn.commit()
    conn.close()

for key in KEYS:
    spec = R.get(key)
    holes = []
    for lg in ("ar", "tr"):
        if not spec[f"title_{lg}"] or not spec[f"desc_{lg}"]:
            holes.append(f"{key}.title/desc.{lg}")
        for c_ in spec["columns"] + spec["kpis"] + spec["filters"]:
            if not c_.get(lg):
                holes.append(f"{key}.{c_.get('key') or c_.get('name')}.{lg}")
        if spec["chart"] and not spec["chart"].get(lg):
            holes.append(f"{key}.chart.{lg}")
    ok(f"i18n: {key} has AR + TR for every label", not holes, f"{holes}")

print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
