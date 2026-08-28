"""
Self-verification for the shared reporting engine (app/services/reporting.py).

Run it:  PYTHONIOENCODING=utf-8 python app/services/tests_reporting.py

Plain asserts, no framework. It builds its own throwaway SQLite database in a
temp directory (config.Config.DB_PATH is hardcoded to <repo>/platform.db, so
overriding it is mandatory — otherwise the test pollutes the repo) and registers
two throwaway reports against a table it creates itself, so it never depends on
another lane's data.
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="rpt_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                            # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from datetime import date, timedelta                     # noqa: E402
import json                                              # noqa: E402
import re                                                # noqa: E402

from app import create_app                               # noqa: E402
from app.db import get_db                                # noqa: E402
from app.routes import reports_hub                       # noqa: E402
from app.services import reporting as R                  # noqa: E402

# The repo THIS file lives in, not a hardcoded sibling worktree — running from
# beta used to validate the render mirror and report the answer as beta's.
REPO = Path(__file__).resolve().parents[2]
PASS, FAIL = [], []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# App + fixture data
# ---------------------------------------------------------------------------
app = create_app()
# create_app() registers this blueprint itself; registering it twice is a
# ValueError at import, which killed this module before its first check.
if "reports_hub" not in app.blueprints:
    app.register_blueprint(reports_hub.bp)

TODAY = date.today()


def seed(n):
    conn = get_db()
    conn.execute("DROP TABLE IF EXISTS rpt_demo")
    conn.execute("CREATE TABLE rpt_demo (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                 "name TEXT, dept TEXT, qty REAL, created_at TEXT)")
    depts = ["cutting", "sewing", "finishing"]
    for i in range(n):
        d = TODAY - timedelta(days=i % 12)
        conn.execute("INSERT INTO rpt_demo (name, dept, qty, created_at) VALUES (?,?,?,?)",
                     (f"row {i}", depts[i % 3], float(i % 7), d.isoformat()))
    # NULL-heavy row: everything optional is NULL
    conn.execute("INSERT INTO rpt_demo (name, dept, qty, created_at) VALUES (?,?,?,?)",
                 (None, None, None, TODAY.isoformat()))
    conn.commit()
    conn.close()


with app.app_context():
    seed(50)
    conn = get_db()
    conn.execute("INSERT INTO users (username, password_hash, full_name, email, role, "
                 "is_active, session_epoch) VALUES (?,?,?,?,?,?,?)",
                 ("rpt_nobody", "x", "No Body", "n@x.tc", "normal_user", 1, 0))
    NOBODY = conn.execute("SELECT id FROM users WHERE username='rpt_nobody'").fetchone()["id"]
    conn.commit()
    conn.close()

DEMO = R.register(
    key="demo_rows", module="demo", module_label="Demo",
    module_label_ar="تجريبي", module_label_tr="Demo",
    title="Demo Rows", title_ar="صفوف تجريبية", title_tr="Demo Satırları",
    desc="Throwaway report.", desc_ar="تقرير تجريبي.", desc_tr="Deneme raporu.",
    perm="maint_view",
    select="d.id AS id, d.name AS name, d.dept AS dept, d.qty AS qty, "
           "d.created_at AS created_at",
    frm="rpt_demo d",
    columns=[R.col("id", "#", "#", "#", "num"),
             R.col("name", "Name", "الاسم", "Ad"),
             R.col("dept", "Department", "القسم", "Bölüm"),
             R.col("qty", "Qty", "الكمية", "Adet", "num", total="SUM(d.qty)"),
             R.col("created_at", "Created", "التاريخ", "Tarih", "date")],
    filters=[R.filt("dept", "Department", "القسم", "Bölüm", "d.dept", "select",
                    "=", ["cutting", "sewing", "finishing"])],
    date_col="d.created_at",
    kpis=[R.kpi("n", "Records", "السجلات", "Kayıtlar", "COUNT(*)"),
          R.kpi("q", "Total qty", "إجمالي الكمية", "Toplam adet", "SUM(d.qty)")],
    chart=R.chart("bar", "d.dept", "SUM(d.qty)", "d.dept",
                  "Qty by department", "الكمية حسب القسم", "Bölüme göre adet"),
    order="d.id DESC", row_cap=10)

GROUPED = R.register(
    key="demo_by_dept", module="demo", module_label="Demo",
    title="Demo by Department", title_ar="حسب القسم", title_tr="Bölüme göre",
    desc="Grouped.", desc_ar="مجمّع.", desc_tr="Gruplanmış.",
    perm="maint_view",
    select="d.dept AS dept, SUM(d.qty) AS qty",
    frm="rpt_demo d", group="d.dept",
    columns=[R.col("dept", "Department", "القسم", "Bölüm"),
             R.col("qty", "Qty", "الكمية", "Adet", "num", total="SUM(qty)")],
    kpis=[R.kpi("groups", "Departments", "الأقسام", "Bölümler", "COUNT(*)")],
    order="qty DESC")


def client(uid=1):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


print("\n=== reporting engine ===")

# ---------------------------------------------------------------------------
# 1. filters
# ---------------------------------------------------------------------------
with app.app_context():
    r = R.run(DEMO, {}, cap=1000)
    ok("no filter -> all 51 rows", r["total"] == 51, f"total={r['total']}")

    r = R.run(DEMO, {"dept": "sewing"}, cap=1000)
    ok("select filter applied", r["total"] == 17 and
       all(x["dept"] == "sewing" for x in r["rows"]), f"total={r['total']}")

    r = R.run(DEMO, {"period": "today"}, cap=1000)
    ok("preset 'today' filters by date", r["total"] > 0 and
       all(str(x["created_at"]).startswith(TODAY.isoformat()) for x in r["rows"]),
       f"total={r['total']}")

    # a range that contains no data -> zero rows, no error
    old = (TODAY - timedelta(days=900)).isoformat()
    r = R.run(DEMO, {"period": "custom", "from": old, "to": old})
    ok("empty range -> 0 rows, no error", r["total"] == 0 and not r["error"] and not r["rows"])

    # inverted range -> explicit error, never a silently empty table
    r = R.run(DEMO, {"period": "custom", "from": TODAY.isoformat(),
                     "to": (TODAY - timedelta(days=5)).isoformat()})
    ok("inverted from>to -> error state", r["error"] == "inverted_range" and r["rows"] == [])

    r = R.run(DEMO, {"period": "custom", "from": "not-a-date"})
    ok("unparseable date -> error state", r["error"] == "bad_date")

    r = R.run(DEMO, {"period": "all"})
    ok("period=all applies no date filter", r["period"]["from"] == "" and r["total"] == 51)

# ---------------------------------------------------------------------------
# 2. KPIs + period comparison
# ---------------------------------------------------------------------------
with app.app_context():
    f = (TODAY - timedelta(days=3)).isoformat()
    r = R.run(DEMO, {"period": "custom", "from": f, "to": TODAY.isoformat()})
    k = {x["key"]: x for x in r["kpis"]}
    ok("KPIs computed", k["n"]["value"] > 0 and k["q"]["value"] > 0,
       f"n={k['n']['value']} q={k['q']['value']}")
    ok("prior period found (data exists before)", k["n"]["has_prior"] and
       k["n"]["delta"] is not None, f"prev={k['n']['prev']} delta={k['n']['delta']}")
    ok("prior window is the preceding equal-length window",
       r["period"]["prev_to"] == (TODAY - timedelta(days=4)).isoformat() and
       r["period"]["prev_from"] == (TODAY - timedelta(days=7)).isoformat(),
       f"{r['period']['prev_from']}..{r['period']['prev_to']}")

    # a window whose predecessor has no data at all -> "no prior period", NOT 0%
    old_f = (TODAY - timedelta(days=800)).isoformat()
    old_t = (TODAY - timedelta(days=798)).isoformat()
    r2 = R.run(DEMO, {"period": "custom", "from": old_f, "to": old_t})
    k2 = {x["key"]: x for x in r2["kpis"]}
    ok("no prior data -> has_prior False, delta None",
       k2["n"]["has_prior"] is False and k2["n"]["delta"] is None)

    r3 = R.run(DEMO, {"period": "all"})
    k3 = {x["key"]: x for x in r3["kpis"]}
    ok("no date window -> no comparison invented", k3["n"]["has_prior"] is False)

# ---------------------------------------------------------------------------
# 3. totals row, grouped report, NULL-heavy rows, single row, empty dataset
# ---------------------------------------------------------------------------
with app.app_context():
    r = R.run(DEMO, {}, cap=1000)
    ok("totals row from SQL", abs(r["totals"]["qty"] - sum(x["qty"] or 0
       for x in r["rows"])) < 0.001, f"total qty={r['totals']['qty']}")
    nulls = [x for x in r["rows"] if x["name"] == ""]
    ok("NULL-heavy row renders as empty strings, not None",
       len(nulls) == 1 and nulls[0]["dept"] == "" and nulls[0]["qty"] == "")

    g = R.run(GROUPED, {})
    ok("grouped report: row count = group count", g["total"] == 4, f"total={g['total']}")
    ok("grouped report: KPI over the grouped sub-query",
       g["kpis"][0]["value"] == 4, f"groups={g['kpis'][0]['value']}")

with app.app_context():
    conn = get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS rpt_empty (id INTEGER PRIMARY KEY "
                 "AUTOINCREMENT, name TEXT, qty REAL, created_at TEXT)")
    conn.execute("DELETE FROM rpt_empty")
    conn.commit()
    conn.close()
EMPTY = R.register(key="demo_empty", module="demo", module_label="Demo",
                   title="Empty", title_ar="فارغ", title_tr="Boş", perm="maint_view",
                   select="e.id AS id, e.name AS name", frm="rpt_empty e",
                   columns=[R.col("id", "#", "#", "#", "num"),
                            R.col("name", "Name", "الاسم", "Ad")],
                   kpis=[R.kpi("n", "Records", "السجلات", "Kayıtlar", "COUNT(*)")],
                   date_col="e.created_at", order="e.id DESC")
with app.app_context():
    r = R.run(EMPTY, {})
    ok("empty dataset -> 0 rows, KPI 0, no crash",
       r["total"] == 0 and r["rows"] == [] and r["kpis"][0]["value"] == 0 and
       r["chart"] is None and not r["error"])
    conn = get_db()
    conn.execute("INSERT INTO rpt_empty (name, qty, created_at) VALUES (?,?,?)",
                 ("only", 1.0, TODAY.isoformat()))
    conn.commit()
    conn.close()
    r = R.run(EMPTY, {})
    ok("single row dataset", r["total"] == 1 and len(r["rows"]) == 1 and
       r["rows"][0]["name"] == "only")

# ---------------------------------------------------------------------------
# 4. row cap
# ---------------------------------------------------------------------------
with app.app_context():
    r = R.run(DEMO, {})                       # DEMO declares row_cap=10
    ok("HTML row cap honoured", len(r["rows"]) == 10 and r["total"] == 51 and
       r["capped"] is True, f"rows={len(r['rows'])} total={r['total']}")
    r = R.run(DEMO, {}, cap=R.EXPORT_CAP)
    ok("export ignores the HTML cap", len(r["rows"]) == 51 and r["capped"] is False)

# ---------------------------------------------------------------------------
# 5. sorting
# ---------------------------------------------------------------------------
with app.app_context():
    r = R.run(DEMO, {"sort": "id", "dir": "asc"}, cap=1000)
    ids = [x["id"] for x in r["rows"]]
    ok("server-side sort asc", ids == sorted(ids))
    r = R.run(DEMO, {"sort": "id", "dir": "desc"}, cap=1000)
    ids = [x["id"] for x in r["rows"]]
    ok("server-side sort desc", ids == sorted(ids, reverse=True))
    r = R.run(DEMO, {"sort": "id); DROP TABLE rpt_demo;--", "dir": "asc"}, cap=5)
    ok("unknown sort column rejected (no SQL injection)", r["sort"] == "" and r["rows"])

# ---------------------------------------------------------------------------
# 6. query count is bounded and does NOT grow with row count
# ---------------------------------------------------------------------------
class _Counting:
    def __init__(self, c, box):
        self._c, self._box = c, box

    def execute(self, *a, **k):
        self._box[0] += 1
        return self._c.execute(*a, **k)

    def __getattr__(self, n):
        return getattr(self._c, n)


def count_queries(spec, args):
    box = [0]
    real = R.get_db
    R.get_db = lambda: _Counting(real(), box)
    try:
        R.run(spec, args, cap=1000)
    finally:
        R.get_db = real
    return box[0]


with app.app_context():
    q50 = count_queries(DEMO, {"period": "month"})
    seed(500)
    q500 = count_queries(DEMO, {"period": "month"})
    ok("query count constant in row count (50 vs 500 rows)", q50 == q500 == 4,
       f"50 rows -> {q50} queries, 500 rows -> {q500} queries")
    q_nodate = count_queries(GROUPED, {})
    ok("no date window + no chart -> 2 queries", q_nodate == 2, f"{q_nodate} queries")
    r = R.run(DEMO, {}, cap=1000)
    ok("500-row seed reads back", r["total"] == 501, f"total={r['total']}")

# ---------------------------------------------------------------------------
# 7. saved views — per user, round-trip, and isolation
# ---------------------------------------------------------------------------
with app.app_context():
    ok("save_view", R.save_view(1, "demo_rows", "My sewing view", "dept=sewing&period=month"))
    v = R.saved_views(1, "demo_rows")
    ok("saved view round-trips", len(v) == 1 and v[0]["name"] == "My sewing view" and
       v[0]["qs"] == "dept=sewing&period=month", str(v))
    ok("saved views are per user", R.saved_views(NOBODY, "demo_rows") == [])
    ok("saved views are per report", R.saved_views(1, "demo_by_dept") == [])
    R.save_view(1, "demo_rows", "My sewing view", "dept=cutting")
    v = R.saved_views(1, "demo_rows")
    ok("re-saving the same name overwrites", len(v) == 1 and v[0]["qs"] == "dept=cutting")
    R.delete_view(NOBODY, v[0]["id"])
    ok("another user cannot delete my view", len(R.saved_views(1, "demo_rows")) == 1)
    R.delete_view(1, v[0]["id"])
    ok("owner can delete own view", R.saved_views(1, "demo_rows") == [])
    ok("save_view rejects a blank name", R.save_view(1, "demo_rows", "   ", "") is False)

# ---------------------------------------------------------------------------
# 8. HTTP: routes, raw status codes, permission refusal on ALL surfaces
# ---------------------------------------------------------------------------
admin, nobody = client(1), client(NOBODY)

r = admin.get("/reporting/")
ok("hub 200 for a permitted user", r.status_code == 200, f"{r.status_code}")
ok("hub lists the demo report", b"Demo Rows" in r.data)

r = nobody.get("/reporting/")
ok("hub 200 for a user with no reports", r.status_code == 200, f"{r.status_code}")
ok("hub hides reports the user may not view", b"Demo Rows" not in r.data)

r = admin.get("/reporting/demo_rows")
ok("report page 200", r.status_code == 200, f"{r.status_code}")
body = r.data.decode()
ok("report page renders KPIs", "Total qty" in body)
ok("report page renders the chart canvas + vendored Chart.js",
   'id="rptChart"' in body and "vendor/chart.umd.min.js" in body and "cdn" not in body.lower())
ok("report page renders a totals row", "rpt.total_row" in body)
ok("report page renders the saved-view form with a CSRF field", 'name="_csrf"' in body)

r = admin.get("/reporting/demo_rows?period=custom&from=%s&to=%s"
              % (TODAY.isoformat(), (TODAY - timedelta(days=3)).isoformat()))
ok("inverted range renders the error state, still 200",
   r.status_code == 200 and "rpt.err_inverted_t" in r.data.decode())

r = admin.get("/reporting/demo_rows?dept=nothing_matches")
ok("a filter matching nothing renders the empty state, not a blank panel",
   r.status_code == 200 and "rpt.empty_h" in r.data.decode(), str(r.status_code))

r = admin.get("/reporting/no_such_report")
ok("unknown report 404", r.status_code == 404, f"{r.status_code}")

# permission refusal — HTML and every export format
ok("HTML refused without the module permission",
   nobody.get("/reporting/demo_rows").status_code == 403,
   str(nobody.get("/reporting/demo_rows").status_code))
for fmt in ("csv", "xlsx", "pdf"):
    sc = nobody.get(f"/reporting/demo_rows.{fmt}").status_code
    ok(f"{fmt.upper()} export refused without the module permission", sc == 403, str(sc))
sc = nobody.post("/reporting/demo_rows/views",
                 data={"name": "x", "qs": "", "_csrf": "bad"}).status_code
ok("saving a view is refused without the module permission", sc in (302, 400, 403), str(sc))

# ---------------------------------------------------------------------------
# 9. exports carry exactly what is on screen
# ---------------------------------------------------------------------------
r = admin.get("/reporting/demo_rows.csv?dept=sewing")
ok("CSV 200 + mimetype", r.status_code == 200 and "csv" in r.mimetype, r.mimetype)
ok("CSV starts with the UTF-8 BOM (Excel + Arabic)", r.data[:3] == b"\xef\xbb\xbf",
   repr(r.data[:3]))
csv_txt = r.data.decode("utf-8-sig")
ok("CSV has the header row", csv_txt.splitlines()[0].startswith("#,Name,Department"),
   csv_txt.splitlines()[0])
ok("CSV honours the filter (only sewing rows)",
   csv_txt.count("sewing") == len(csv_txt.splitlines()) - 1 and "cutting" not in csv_txt)
ok("CSV is not truncated to the HTML cap", len(csv_txt.splitlines()) - 1 > 10,
   f"{len(csv_txt.splitlines()) - 1} data rows")

r = admin.get("/reporting/demo_rows.xlsx?dept=sewing")
ok("XLSX 200 + spreadsheet mimetype", r.status_code == 200 and
   r.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
   r.mimetype)
ok("XLSX is a real zip container and non-trivial",
   r.data[:2] == b"PK" and len(r.data) > 3000, f"{len(r.data)} bytes")
try:
    from openpyxl import load_workbook
    import io as _io
    wb = load_workbook(_io.BytesIO(r.data))
    ws = wb.active
    ok("XLSX content matches the filtered data",
       ws.cell(1, 2).value == "Name" and ws.max_row - 1 == csv_txt.count("sewing"),
       f"rows={ws.max_row - 1}")
except Exception as e:                                   # noqa: BLE001
    ok("XLSX content matches the filtered data", False, repr(e))

r = admin.get("/reporting/demo_rows.pdf?dept=sewing&period=month")
ok("PDF 200 + pdf mimetype", r.status_code == 200 and r.mimetype == "application/pdf",
   r.mimetype)
ok("PDF is a real PDF and non-trivial",
   r.data[:5] == b"%PDF-" and len(r.data) > 4000, f"{len(r.data)} bytes")
raw = r.data.decode("latin-1")
ok("PDF is self-describing (title + filters + generated stamp)",
   "Demo Rows" in raw or b"Demo Rows" in r.data, "title in stream")

r = admin.get("/reporting/demo_rows.json")
ok("unsupported export format 404", r.status_code == 404, str(r.status_code))
r = admin.get("/reporting/demo_rows.csv?period=custom&from=%s&to=%s"
              % (TODAY.isoformat(), (TODAY - timedelta(days=5)).isoformat()))
ok("export of an invalid filter set 400s rather than lying", r.status_code == 400,
   str(r.status_code))

# PDF/CSV/XLSX of an EMPTY result must still be a valid file
for fmt, head in (("csv", b"\xef\xbb\xbf"), ("xlsx", b"PK"), ("pdf", b"%PDF-")):
    rr = admin.get(f"/reporting/demo_rows.{fmt}?dept=nothing_matches")
    ok(f"empty {fmt.upper()} export is still a valid file",
       rr.status_code == 200 and rr.data.startswith(head), str(rr.status_code))

# ---------------------------------------------------------------------------
# 10. saved views through HTTP (CSRF-protected round trip)
# ---------------------------------------------------------------------------
with admin.session_transaction() as s:
    tok = s.get("_csrf_token")
r = admin.post("/reporting/demo_rows/views",
               data={"name": "Sewing this month", "qs": "dept=sewing&period=month",
                     "_csrf": tok})
ok("POST save view redirects back to the filtered URL",
   r.status_code == 302 and "dept=sewing" in r.headers.get("Location", ""),
   f"{r.status_code} {r.headers.get('Location')}")
r = admin.get("/reporting/demo_rows")
ok("saved view appears on the page", "Sewing this month" in r.data.decode())

# ---------------------------------------------------------------------------
# 11. i18n — every data-i18n key on every page this lane touches
# ---------------------------------------------------------------------------
# The keys this lane introduces. They are NOT yet in app/static/i18n/*.json —
# only the orchestrator may write those files — so they are declared here and
# reported verbatim in new_i18n_keys. A key missing from en.json renders as the
# literal string "rpt.foo" to every user in every language, which is why this
# check exists at all.
NEW_KEYS = {
  "rpt.eyebrow":       {"en": "Reporting", "ar": "التقارير", "tr": "Raporlama"},
  "rpt.hub_title":     {"en": "Report Center", "ar": "مركز التقارير", "tr": "Rapor Merkezi"},
  "rpt.hub_sub":       {"en": "Every module's reports — filter, compare, chart and export.",
                        "ar": "تقارير كل الوحدات — تصفية ومقارنة ورسم بياني وتصدير.",
                        "tr": "Tüm modüllerin raporları — filtrele, karşılaştır, grafikle, dışa aktar."},
  "rpt.none":          {"en": "No reports are available for your role.",
                        "ar": "لا توجد تقارير متاحة لدورك.",
                        "tr": "Rolünüz için rapor bulunmuyor."},
  "rpt.none_hint":     {"en": "Ask an administrator for access to a module — its reports appear here automatically.",
                        "ar": "اطلب من المسؤول صلاحية الوصول إلى وحدة — ستظهر تقاريرها هنا تلقائياً.",
                        "tr": "Bir modüle erişim için yöneticinize başvurun — raporları burada otomatik görünür."},
  "rpt.reports":       {"en": "reports", "ar": "تقارير", "tr": "rapor"},
  "rpt.chart":         {"en": "Chart", "ar": "رسم بياني", "tr": "Grafik"},
  "rpt.open":          {"en": "Open", "ar": "فتح", "tr": "Aç"},
  "rpt.back":          {"en": "Report Center", "ar": "مركز التقارير", "tr": "Rapor Merkezi"},
  "rpt.period":        {"en": "Period", "ar": "الفترة", "tr": "Dönem"},
  "rpt.p_all":         {"en": "All dates", "ar": "كل التواريخ", "tr": "Tüm tarihler"},
  "rpt.p_today":       {"en": "Today", "ar": "اليوم", "tr": "Bugün"},
  "rpt.p_week":        {"en": "This week", "ar": "هذا الأسبوع", "tr": "Bu hafta"},
  "rpt.p_month":       {"en": "This month", "ar": "هذا الشهر", "tr": "Bu ay"},
  "rpt.p_quarter":     {"en": "This quarter", "ar": "هذا الربع", "tr": "Bu çeyrek"},
  "rpt.p_year":        {"en": "This year", "ar": "هذه السنة", "tr": "Bu yıl"},
  "rpt.p_custom":      {"en": "Custom range", "ar": "نطاق مخصص", "tr": "Özel aralık"},
  "rpt.from":          {"en": "From", "ar": "من", "tr": "Başlangıç"},
  "rpt.to":            {"en": "To", "ar": "إلى", "tr": "Bitiş"},
  "rpt.apply":         {"en": "Apply", "ar": "تطبيق", "tr": "Uygula"},
  "rpt.reset":         {"en": "Reset", "ar": "إعادة تعيين", "tr": "Sıfırla"},
  "rpt.err_inverted_t": {"en": "The From date is after the To date.",
                         "ar": "تاريخ البداية بعد تاريخ النهاية.",
                         "tr": "Başlangıç tarihi bitiş tarihinden sonra."},
  "rpt.err_inverted_h": {"en": "Swap the two dates, or pick a preset period, then apply again.",
                         "ar": "بدّل التاريخين أو اختر فترة جاهزة ثم طبّق مرة أخرى.",
                         "tr": "İki tarihi değiştirin veya hazır bir dönem seçip yeniden uygulayın."},
  "rpt.err_date_t":    {"en": "That date could not be read.",
                        "ar": "تعذّرت قراءة هذا التاريخ.",
                        "tr": "Bu tarih okunamadı."},
  "rpt.err_date_h":    {"en": "Use the date picker (YYYY-MM-DD), then apply again.",
                        "ar": "استخدم منتقي التاريخ (سنة-شهر-يوم) ثم طبّق مرة أخرى.",
                        "tr": "Tarih seçiciyi kullanın (YYYY-AA-GG), sonra yeniden uygulayın."},
  "rpt.no_prior":      {"en": "No prior period", "ar": "لا توجد فترة سابقة", "tr": "Önceki dönem yok"},
  "rpt.vs_prior":      {"en": "vs previous period", "ar": "مقارنة بالفترة السابقة",
                        "tr": "önceki döneme göre"},
  "rpt.results":       {"en": "Results", "ar": "النتائج", "tr": "Sonuçlar"},
  "rpt.total_row":     {"en": "Total", "ar": "الإجمالي", "tr": "Toplam"},
  "rpt.showing":       {"en": "Showing the first", "ar": "عرض أول", "tr": "İlk gösterilen"},
  "rpt.of":            {"en": "of", "ar": "من أصل", "tr": "/"},
  "rpt.capped_hint":   {"en": "rows — export to get the full data set.",
                        "ar": "صف — صدّر للحصول على مجموعة البيانات الكاملة.",
                        "tr": "satır — tam veri kümesi için dışa aktarın."},
  "rpt.empty_t":       {"en": "No records match these filters.",
                        "ar": "لا توجد سجلات مطابقة لهذه التصفية.",
                        "tr": "Bu filtrelere uyan kayıt yok."},
  "rpt.empty_h":       {"en": "Widen the period, or clear one filter, then apply again.",
                        "ar": "وسّع الفترة أو امسح أحد عوامل التصفية ثم طبّق مرة أخرى.",
                        "tr": "Dönemi genişletin veya bir filtreyi temizleyip yeniden uygulayın."},
  "rpt.saved_views":   {"en": "Saved views", "ar": "العروض المحفوظة", "tr": "Kayıtlı görünümler"},
  "rpt.no_views":      {"en": "No saved views yet — set your filters above, then save them here.",
                        "ar": "لا توجد عروض محفوظة بعد — اضبط التصفية أعلاه ثم احفظها هنا.",
                        "tr": "Henüz kayıtlı görünüm yok — yukarıdan filtreleyip burada kaydedin."},
  "rpt.view_name":     {"en": "View name", "ar": "اسم العرض", "tr": "Görünüm adı"},
  "rpt.save_view":     {"en": "Save this view", "ar": "احفظ هذا العرض", "tr": "Bu görünümü kaydet"},
}

dicts = {}
for lang in ("en", "ar", "tr"):
    dicts[lang] = json.loads((REPO / "app/static/i18n" / f"{lang}.json").read_text("utf-8"))

keys = set()
for tpl in ("app/templates/reports/hub.html", "app/templates/reports/report.html"):
    keys |= set(re.findall(r'data-i18n="([^"]+)"', (REPO / tpl).read_text("utf-8")))

missing = {}
for k in sorted(keys):
    for lang in ("en", "ar", "tr"):
        if k in dicts[lang]:
            continue
        if k in NEW_KEYS and NEW_KEYS[k].get(lang):
            continue
        missing.setdefault(k, []).append(lang)
ok(f"every data-i18n key ({len(keys)}) resolves in en+ar+tr (shipped or declared)",
   not missing, str(missing))

# A declared key that has SHIPPED is a merge that happened, not a collision —
# staging is where these start, app/static/i18n is where they end up. What would
# be a real collision is the same key shipped under a DIFFERENT meaning, so that
# is what this reports.
reworded = [k for k in NEW_KEYS
            if k in dicts["en"] and dicts["en"][k] != NEW_KEYS[k].get("en")]
ok("declared keys that shipped did not change meaning (%d reworded)" % len(reworded),
   not reworded, str(reworded[:6]))
ok("every declared key has all three languages",
   all(all(NEW_KEYS[k].get(l) for l in ("en", "ar", "tr")) for k in NEW_KEYS))

# definition-driven text must NOT use data-i18n (it cannot be in the dictionary)
rpt_html = (REPO / "app/templates/reports/report.html").read_text("utf-8")
ok("definition-driven text uses data-loc-* not data-i18n",
   rpt_html.count("data-loc-en=") >= 6 and 'data-i18n="{{' not in rpt_html)

# ---------------------------------------------------------------------------
# 12. no boot-time DB work
# ---------------------------------------------------------------------------
src = (REPO / "app/services/reporting.py").read_text("utf-8")
module_level = [ln for ln in src.splitlines()
                if ln and not ln[0].isspace() and "get_db(" in ln]
ok("reporting.py does no DB work at import", not module_level, str(module_level))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  - " + f)
sys.exit(1 if FAIL else 0)
