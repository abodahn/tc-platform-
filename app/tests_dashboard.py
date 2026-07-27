"""
Executive dashboard (Command Center) — factory-pulse verification.

Run standalone:  python app/tests_dashboard.py
Config.DB_PATH is redirected to a temp dir so the repo's platform.db is never touched.
"""
import os
import re
import sys
import json
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="dash_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                     # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                        # noqa: E402
from app.db import get_db                         # noqa: E402
from app.routes import main as main_routes        # noqa: E402

# Which permission each tile is gated on — the invariant test 2 asserts.
TILE_PERM = {
    "orders_late": "view_dashboard", "orders_week": "view_dashboard",
    "mat_short": "wh_view", "qc_rate": "qc_view", "qc_failed": "qc_view",
    "lines_below": "mes_view", "spares_out": "maint_view",
    "pr_sign": "proc_view", "certs_expiring": "cmp_view",
}

PASS, FAIL = [], []


class _ConnProxy:
    """sqlite3.Connection attributes are read-only, so intercepting execute()
    needs a wrapper object rather than monkey-patching the connection."""

    def __init__(self, conn, on_execute):
        object.__setattr__(self, "_c", conn)
        object.__setattr__(self, "_on", on_execute)

    def execute(self, sql, params=()):
        return self._on(self._c, sql, params)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_c"), name)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))


def client(app, uid=1):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


def count_queries(app, path, uid=1):
    """Count SQL statements executed while rendering `path`, by wrapping get_db."""
    n = [0]
    real = main_routes.get_db

    def on_execute(conn, sql, params):
        n[0] += 1
        return conn.execute(sql, params)

    main_routes.get_db = lambda: _ConnProxy(real(), on_execute)
    try:
        client(app, uid).get(path)
    finally:
        main_routes.get_db = real
    return n[0]


print("\n=== 1. seed DB: 200 for super_admin, pulse present ===")
app = create_app()
c = client(app)
r = c.get("/")
check("dashboard 200 (super_admin)", r.status_code == 200, f"status={r.status_code}")
html = r.get_data(as_text=True)
seen = set(re.findall(r'data-exec="([a-z_]+)"', html))
check("pulse tiles rendered", len(seen) >= 5, f"tiles={sorted(seen)}")
check("no raw traceback", "Traceback" not in html)

print("\n=== 2. low-permission user sees NO block it lacks permission for ===")
conn = get_db()
try:
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active) "
                 "VALUES (?,?,?,?,1)", ("lane2_low", "x", "Low Perms", "normal_user"))
    conn.commit()
    low_id = conn.execute("SELECT id FROM users WHERE username='lane2_low'").fetchone()["id"]
finally:
    conn.close()
r2 = client(app, low_id).get("/")
check("dashboard 200 (normal_user)", r2.status_code == 200, f"status={r2.status_code}")
h2 = r2.get_data(as_text=True)
low_seen = set(re.findall(r'data-exec="([a-z_]+)"', h2))
from app.security import user_has_permission              # noqa: E402
conn = get_db()
try:
    low_user = dict(conn.execute("SELECT * FROM users WHERE id=?", (low_id,)).fetchone())
finally:
    conn.close()
leaked = sorted(k for k in low_seen if not user_has_permission(low_user, TILE_PERM[k]))
check("every tile shown is one this user may open", not leaked, f"leaked={leaked}")
# spot check: normal_user has none of these module permissions, so their markup
# must be absent from the page entirely.
absent = {"mat_short", "qc_rate", "qc_failed", "lines_below", "spares_out", "certs_expiring"}
check("no markup for modules normal_user cannot open", not (low_seen & absent),
      f"present={sorted(low_seen & absent)}")
check("normal_user keeps view_dashboard tiles", "orders_late" in low_seen, f"tiles={sorted(low_seen)}")

print("\n=== 3. empty database degrades quietly (no fabricated zeros) ===")
EMPTY = Path(tempfile.mkdtemp(prefix="dash_empty_"))
config.Config.DB_PATH = EMPTY / "platform.db"
empty_app = create_app()
econn = get_db()
try:
    for t in ("ord_milestones", "ord_orders", "wh_materials", "qc_defects", "qc_inspections",
              "mes_hourly", "mnt_spare_compat", "mnt_request_items", "mnt_stock_movements",
              "mnt_spare_parts", "pr_requests", "cmp_certs"):
        try:
            econn.execute(f"DELETE FROM {t}")
        except Exception:
            econn.rollback()
    econn.commit()
finally:
    econn.close()
r3 = client(empty_app).get("/")
h3 = r3.get_data(as_text=True)
check("empty DB dashboard 200", r3.status_code == 200, f"status={r3.status_code}")
check("empty DB: no traceback", "Traceback" not in h3)
check("empty DB: no tile at all", not re.search(r'data-exec="', h3))
check("empty DB: quiet empty state shown", 'id="factoryPulseEmpty"' in h3)
config.Config.DB_PATH = TMP / "platform.db"

print("\n=== 4. a broken module query degrades ONE block only ===")
orig_get_db = main_routes.get_db


def _poison(conn, sql, params):
    if "wh_materials" in sql:
        raise RuntimeError("simulated warehouse failure")
    return conn.execute(sql, params)


main_routes.get_db = lambda: _ConnProxy(orig_get_db(), _poison)
try:
    r4 = client(app).get("/")
finally:
    main_routes.get_db = orig_get_db
h4 = r4.get_data(as_text=True)
broken_seen = set(re.findall(r'data-exec="([a-z_]+)"', h4))
check("broken query: page still 200", r4.status_code == 200, f"status={r4.status_code}")
check("broken query: warehouse tile gone", "mat_short" not in broken_seen)
check("broken query: other tiles survive", len(broken_seen) >= 4, f"tiles={sorted(broken_seen)}")

print("\n=== 5. N+1: query count does not grow with the number of orders ===")
n5 = count_queries(app, "/")
conn = get_db()
try:
    for i in range(45):
        conn.execute("INSERT INTO ord_orders (order_no,buyer,qty,ship_date,status,created_at) "
                     "VALUES (?,?,?,?,?,?)",
                     (f"N1-{i}", "Load Buyer", 100, "2099-01-01", "in_production", "2026-01-01"))
    conn.commit()
    total_orders = conn.execute("SELECT COUNT(*) AS c FROM ord_orders").fetchone()["c"]
finally:
    conn.close()
n50 = count_queries(app, "/")
check("query count bounded (5 -> 50 orders)", n50 <= n5,
      f"orders={total_orders}  queries {n5} -> {n50}")

print("\n=== 6. i18n: 200 in en/ar/tr and every data-i18n key resolves ===")
i18n_dir = REPO / "app" / "static" / "i18n"
langs = {}
for lang in ("en", "ar", "tr"):
    langs[lang] = json.loads((i18n_dir / f"{lang}.json").read_text(encoding="utf-8"))

conn = get_db()
try:
    conn.execute("UPDATE users SET lang_pref=? WHERE id=1", ("en",))
    conn.commit()
finally:
    conn.close()

page_keys = set()
for lang in ("en", "ar", "tr"):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
        conn.commit()
    finally:
        conn.close()
    rl = client(app).get("/")
    check(f"dashboard 200 in {lang}", rl.status_code == 200, f"status={rl.status_code}")
    page_keys |= set(re.findall(r'data-i18n="([^"{}]+)"', rl.get_data(as_text=True)))

# Assert against the SHIPPED dictionary only. An earlier version unioned in the
# lane's own new-key list, which made this unfalsifiable: any key the author
# invented was automatically "known", and 22 raw keys shipped while it passed.
unresolved = sorted(k for k in page_keys if k not in set(langs["en"]))
check("every data-i18n key on the dashboard resolves", not unresolved, f"unresolved={unresolved}")

# The pulse labels are computed server-side and ride in data-loc-en/ar/tr, which
# app.js swaps. A trio missing a language would silently fall back to English.
html = rl.get_data(as_text=True)
trios = re.findall(r'<[^>]*\bdata-loc-en="([^"]*)"[^>]*>', html)
incomplete = len(trios) - len(re.findall(
    r'<[^>]*\bdata-loc-en="[^"]*"[^>]*\bdata-loc-ar="[^"]*"[^>]*\bdata-loc-tr="[^"]*"[^>]*>', html))
check("every data-loc element carries all three languages",
      incomplete == 0, f"{len(trios)} trios, {incomplete} incomplete")

print("\n=== 7. re-seed idempotency (create_and_seed x3) ===")
IDEM = Path(tempfile.mkdtemp(prefix="dash_idem_"))
config.Config.DB_PATH = IDEM / "platform.db"
idem_app = create_app()
from app.orders import schema as ord_schema          # noqa: E402
conn = get_db()
try:
    conn.execute("UPDATE ord_orders SET buyer='ADMIN EDITED' WHERE id=1")
    conn.commit()
    for _ in range(3):
        ord_schema.create_and_seed(conn)
        conn.commit()
    n = conn.execute("SELECT COUNT(*) AS c FROM ord_orders").fetchone()["c"]
    edited = conn.execute("SELECT buyer FROM ord_orders WHERE id=1").fetchone()["buyer"]
finally:
    conn.close()
check("create_and_seed x3: no duplicate orders", n == 3, f"orders={n}")
check("create_and_seed x3: admin edit survives", edited == "ADMIN EDITED", f"buyer={edited}")
config.Config.DB_PATH = TMP / "platform.db"

print(f"\n==== {len(PASS)} passed, {len(FAIL)} failed ====")
if FAIL:
    print("FAILED: " + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)
