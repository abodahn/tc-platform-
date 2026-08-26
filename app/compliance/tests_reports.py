"""Self-verification for the Compliance reports (app/compliance/reports.py).

Run it:  PYTHONIOENCODING=utf-8 python app/compliance/tests_reports.py

Plain asserts, no framework, throwaway SQLite database in a temp directory
(config.Config.DB_PATH is hardcoded to <repo>/platform.db, so overriding it is
mandatory — the test must never write into the repo). Every number asserted
below is hand-computed in the comment above it.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="cmprpt_"))
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
KEYS = ["compliance_findings", "compliance_expiry"]
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
NOW = str(TODAY) + " 09:00:00"


def d(n):
    return str(TODAY + timedelta(days=n))


def wipe(conn):
    for t in ("cmp_findings", "cmp_audits", "cmp_certs"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()


with app.app_context():
    conn = get_db()
    wipe(conn)
    if not conn.execute("SELECT id FROM users WHERE username='cmp_nobody'").fetchone():
        conn.execute("INSERT INTO users (username,password_hash,full_name,email,role,"
                     "is_active,session_epoch) VALUES (?,?,?,?,?,?,?)",
                     ("cmp_nobody", "x", "No Body", "cmp_nobody@x.tc", "normal_user", 1, 0))
    NOBODY = conn.execute("SELECT id FROM users WHERE username='cmp_nobody'").fetchone()["id"]
    conn.commit()
    conn.close()


def client(uid=1):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


print("\n=== compliance reports ===")

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
    conn.execute("INSERT INTO cmp_audits (ref,scheme,status,valid_until,created_at) "
                 "VALUES (?,?,?,?,?)", ("T-A0", "WRAP", "completed", d(30), NOW))
    a0 = conn.execute("SELECT id FROM cmp_audits WHERE ref='T-A0'").fetchone()["id"]
    conn.execute("INSERT INTO cmp_findings (audit_id,clause,finding,severity,owner,due_date,"
                 "status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                 (a0, "Working Hours", "Solo finding", "minor", "HR", d(5), "open", NOW))
    conn.commit()
    conn.close()
    res = R.run(R.get("compliance_findings"), {})
    k = {x["key"]: x for x in res["kpis"]}
    # one open, not-yet-due finding -> open 1, overdue 0, closure 0%
    ok("single row: 1 finding open, 0 overdue, closure 0%",
       res["total"] == 1 and near(k["open"]["value"], 1) and near(k["overdue"]["value"], 0)
       and near(k["closure_pct"]["value"], 0.0), f"{[ (x['key'], x['value']) for x in res['kpis']]}")
    res = R.run(R.get("compliance_expiry"), {})
    ok("single row: expiry watchlist has the audit, not expired",
       res["total"] == 1 and near(res["rows"][0]["expired"], 0), f"{res['rows']}")

# ---------------------------------------------------------------------------
# 3. Full fixture + hand-computed arithmetic
# ---------------------------------------------------------------------------
with app.app_context():
    conn = get_db()
    wipe(conn)
    for ref, scheme, status, valid in (("T-A1", "WRAP", "completed", d(30)),
                                       ("T-A2", "SA8000", "completed", d(-10)),
                                       ("T-A3", "SLCP", "scheduled", None),
                                       ("T-A4", "GOTS", "cancelled", d(5))):
        conn.execute("INSERT INTO cmp_audits (ref,scheme,audit_type,site,auditor,status,"
                     "valid_until,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (ref, scheme, "periodic", "Main Factory", "SGS", status, valid, NOW))
    aid = {r["ref"]: r["id"] for r in
           conn.execute("SELECT id, ref FROM cmp_audits").fetchall()}
    for ref, clause, sev, status, due, closed in (
            ("T-A1", "Working Hours", "major", "in_progress", d(-3), None),
            ("T-A1", "Health & Safety", "minor", "open", d(10), None),
            ("T-A1", "Working Hours", "critical", "closed", d(-20), NOW),
            ("T-A2", None, "zero_tolerance", "open", None, None)):
        conn.execute("INSERT INTO cmp_findings (audit_id,clause,finding,severity,owner,"
                     "due_date,status,created_at,closed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (aid[ref], clause, f"finding on {clause}", sev, "Owner", due,
                      status, NOW, closed))
    for name, cert_no, expiry, status in (("T-C1", "C-1", d(20), "valid"),
                                          ("T-C2", "C-2", d(-5), "valid"),
                                          ("T-C3", "C-3", d(1), "revoked"),
                                          ("T-C4", "C-4", None, "valid")):
        conn.execute("INSERT INTO cmp_certs (name,cert_type,issuer,cert_no,scope,expiry_date,"
                     "status,created_at) VALUES (?,?,?,?,?,?,?,?)",
                     (name, "WRAP", "Issuer", cert_no, "Premises", expiry, status, NOW))
    conn.commit()
    conn.close()

with app.app_context():
    res = R.run(R.get("compliance_findings"), {})
    k = {x["key"]: x for x in res["kpis"]}
    # 4 findings: open|in_progress = 3 ; overdue (open AND due < today) = 1 (the
    # in_progress one due 3 days ago; the critical one is closed so it is not overdue)
    # open critical/zero-tolerance = 1 ; closed|verified = 1 -> closure 1/4 = 25%
    ok("findings: 4 rows", res["total"] == 4, f"total={res['total']}")
    ok("findings: open = 3", near(k["open"]["value"], 3))
    ok("findings: overdue = 1 (a CLOSED past-due finding is not overdue)",
       near(k["overdue"]["value"], 1), f"={k['overdue']['value']}")
    ok("findings: open critical = 1 (the closed 'critical' does not count)",
       near(k["critical"]["value"], 1), f"={k['critical']['value']}")
    ok("findings: closure% = 1/4 = 25", near(k["closure_pct"]["value"], 25.0),
       f"={k['closure_pct']['value']}")
    ok("findings: overdue totals row = 1", near(res["totals"]["overdue"], 1))
    ok("findings: open items sort before closed ones",
       res["rows"][-1]["status"] == "closed",
       f"{[r['status'] for r in res['rows']]}")
    ch = res["chart"]
    # pareto by area: Working Hours 2, Health & Safety 1, (no clause) 1 -> 4 findings
    ok("findings chart: Working Hours is the top area with 2 of 4",
       ch and ch["labels"][0] == "Working Hours" and ch["values"][0] == 2
       and sum(ch["values"]) == 4, f"{ch and list(zip(ch['labels'], ch['values']))}")

    f = R.run(R.get("compliance_findings"), {"status": "open"})
    ok("findings filter status=open -> 2 rows", f["total"] == 2, f"total={f['total']}")
    f = R.run(R.get("compliance_findings"), {"severity": "major"})
    ok("findings filter severity=major -> 1 row", f["total"] == 1)
    f = R.run(R.get("compliance_findings"), {"scheme": "SA8000"})
    ok("findings filter scheme=SA8000 -> 1 row", f["total"] == 1)

    res = R.run(R.get("compliance_expiry"), {})
    k = {x["key"]: x for x in res["kpis"]}
    names = [r["name"] for r in res["rows"]]
    # tracked = 2 audits with a validity date and not cancelled (T-A1, T-A2)
    #         + 2 certificates with an expiry and not revoked (T-C1, T-C2) = 4
    # T-A3 (no valid_until), T-A4 (cancelled), T-C3 (revoked), T-C4 (no expiry) excluded
    ok("expiry: 4 items tracked", res["total"] == 4, f"total={res['total']} rows={names}")
    ok("expiry: cancelled audit, revoked cert and undated rows are excluded",
       "GOTS" not in names and "T-C3" not in names and "T-C4" not in names
       and "SLCP" not in names, f"{names}")
    # expired = T-A2 (10 days ago) + T-C2 (5 days ago) = 2 ; still valid = 2
    ok("expiry: expired = 2, still valid = 2",
       near(k["expired"]["value"], 2) and near(k["valid"]["value"], 2),
       f"expired={k['expired']['value']} valid={k['valid']['value']}")
    ok("expiry: totals row expired = 2", near(res["totals"]["expired"], 2))
    ok("expiry: soonest expiry first (the 10-day-expired audit)",
       res["rows"][0]["expires"] == d(-10) and res["rows"][0]["kind"] == "audit",
       f"{[(r['kind'], r['name'], r['expires']) for r in res['rows']]}")
    f = R.run(R.get("compliance_expiry"), {"kind": "certificate"})
    ok("expiry filter kind=certificate -> 2 rows", f["total"] == 2, f"total={f['total']}")
    f = R.run(R.get("compliance_expiry"), {"expires_before": str(TODAY)})
    ok("expiry filter expires_before=today -> the 2 expired items", f["total"] == 2)

# ---------------------------------------------------------------------------
# 4. Permission — page AND every export format
# ---------------------------------------------------------------------------
nob = client(NOBODY)
for k in KEYS:
    r = nob.get(f"/reporting/{k}")
    ok(f"perm: {k} page denied without cmp_view",
       r.status_code in (302, 403) and b"SA8000" not in r.get_data(),
       f"status={r.status_code}")
    for fmt in ("csv", "xlsx", "pdf"):
        r = nob.get(f"/reporting/{k}.{fmt}")
        ok(f"perm: {k}.{fmt} denied without cmp_view",
           r.status_code in (302, 403) and b"SA8000" not in r.get_data(),
           f"status={r.status_code}")

c = client()
for k in KEYS:
    for fmt, mime in (("csv", "text/csv"), ("xlsx", "application/"), ("pdf", "application/pdf")):
        r = c.get(f"/reporting/{k}.{fmt}")
        ok(f"export: {k}.{fmt} 200 for a permitted user",
           r.status_code == 200 and mime in r.mimetype and len(r.get_data()) > 0,
           f"status={r.status_code} mime={r.mimetype}")
csv_body = c.get("/reporting/compliance_expiry.csv").get_data().decode("utf-8-sig")
ok("export: CSV carries the computed rows", "T-C1" in csv_body and "SA8000" in csv_body)

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
    r = c.get("/compliance/")
    keys = set(re.findall(r'data-i18n="([^"]+)"', r.get_data(as_text=True)))
    missing = sorted(x for x in keys if x not in DICT[lg])
    ok(f"i18n[{lg}]: /compliance dashboard 200 and every data-i18n key resolves",
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
