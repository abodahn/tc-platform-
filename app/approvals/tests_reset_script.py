"""scripts/reset_procurement.py on a throwaway SQLite database: real requests walked to
PO, receipt, invoice and payment; dry run changes nothing; apply empties procurement,
keeps master data and restarts numbering at 000001; restore puts every row back.

    python -m app.approvals.tests_reset_script
"""
import os
import sys
import tempfile
from pathlib import Path

STAMP1, STAMP2 = "20990101_0000", "20990101_0001"
KEEP = ["proc_vendors", "proc_items", "proc_budgets", "proc_settings", "proc_resp_matrix",
        "users", "wh_materials", "mnt_spare_parts"]


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DATABASE_URL = ""
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="reset_"), "reset.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        checks()
    print("all reset-script checks passed")


def checks():
    from app.db import get_db
    from app.approvals import services as svc, constants as C
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import reset_procurement as R

    conn = get_db()
    signer = {}
    for stage, roles in C.STAGE_ROLES.items():
        role = sorted(roles)[0]
        conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                     "is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                     ("rs_" + stage, "x", "rs_" + stage, role))
        signer[stage] = {"username": "rs_" + stage, "role": role}
    conn.execute("INSERT INTO wh_materials (code, name, kind, uom, roll_tracked, is_active) "
                 "VALUES ('RS-TRIM-1', 'RS button', 'trim', 'pcs', 0, 1)")
    conn.commit()
    for s in signer.values():
        s["id"] = conn.execute("SELECT id FROM users WHERE username=?", (s["username"],)).fetchone()["id"]
    mat = conn.execute("SELECT id, code, stock_qty FROM wh_materials WHERE is_active=1 AND "
                       "COALESCE(roll_tracked,0)=0 ORDER BY id LIMIT 1").fetchone()
    assert mat, "no quantity material seeded"
    conn.close()
    buyer, reqr = signer["purchasing"], {"username": "rs_req", "id": 77}

    def to_po(title, item, qty, price):
        pr_id, _ = svc.create_pr({"title": title, "department": "Production", "currency": "EGP",
                                  "vendor": "RS Vendor"},
                                 [{"item": item, "qty": qty, "unit_price": 0}], reqr, priced=False)
        c = get_db()
        li = c.execute("SELECT id FROM pr_items WHERE pr_id=?", (pr_id,)).fetchone()["id"]
        c.close()
        ok, msg = svc.price_pr(pr_id, {li: price}, {"tax_rate": 0}, buyer)
        assert ok, msg
        for _ in range(15):
            c = get_db()
            r = c.execute("SELECT stage FROM pr_steps WHERE pr_id=? AND status='pending' "
                          "ORDER BY seq LIMIT 1", (pr_id,)).fetchone()
            c.close()
            if not r:
                break
            ok, msg = svc.act_on_step(pr_id, signer[r["stage"]], "approve")
            if not ok and msg == "needs_quotes":
                for n, m in (("A", 1.0), ("B", 1.05)):
                    svc.add_quote(pr_id, {"payment_terms": "30 days", "vendor": "RS Vendor %s" % n, "amount": qty * price * m}, buyer, filename="q.pdf", content_type="application/pdf", content_b64="JVBERi0xLjQK")
                continue
            assert ok, msg
            if msg == "approved":
                break
        c = get_db()
        c.execute("INSERT OR IGNORE INTO proc_vendors (name, is_active, created_at) "
                  "VALUES ('RS Vendor', 1, '2026-01-01')")
        c.commit()
        c.close()
        ok, msg = svc.issue_po(pr_id, buyer)
        assert ok, msg
        return pr_id, li

    p1, l1 = to_po("Trims for style A", mat["code"], 10, 5)
    ok, msg = svc.receive_items(p1, {l1: 10}, signer["warehouse"])
    assert ok, msg
    ok, msg = svc.add_invoice(p1, {"invoice_no": "RS-INV-1", "amount": 50, "tax": 0}, buyer)
    assert ok, msg
    ok, msg = svc.add_payment(p1, {"amount": 50}, signer["finance"])
    assert ok, msg
    to_po("Second request", "Toner cartridge", 2, 300)

    conn = get_db()
    conn.execute("INSERT INTO notifications (module, title, created_at) VALUES "
                 "('maintenance','not procurement','2026-01-01')")
    conn.execute("INSERT INTO wh_movements (material_id, type, qty, ref, created_at) VALUES "
                 "(?, 'adjust', 0, 'PO-88123', '2026-01-01')", (mat["id"],))
    conn.commit()

    def snap(names):
        return {t: sorted(tuple(r) for r in conn.execute("SELECT * FROM " + t).fetchall())
                for t in names}

    every = sorted(R.tables(conn) - {"sqlite_sequence"})
    before = snap(every)
    assert all(before[t] for t in ("pr_requests", "pr_grn", "pr_invoices", "pr_payments",
                                   "pr_purchase_orders", "pr_stock_postings")), \
        "fixture did not reach PO/receipt/invoice/payment"
    stock = conn.execute("SELECT stock_qty FROM wh_materials WHERE id=?", (mat["id"],)).fetchone()[0]
    assert stock == mat["stock_qty"] + 10, "receipt did not post to the warehouse"
    max_id = max(r[0] for r in before["pr_requests"])

    # 1. dry run changes nothing
    plan = R.report(conn)
    assert plan["pr_requests"] == len(before["pr_requests"]) and plan["notifications"] > 0
    assert snap(every) == before, "dry run changed data"

    # 2. apply
    made = R.apply(conn, STAMP1)
    assert "zz_backup_%s_pr_requests" % STAMP1 in made
    for t in R.PR_TABLES:
        assert conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0] == 0, t
    assert snap(KEEP) == {t: before[t] for t in KEEP}, "master data changed"
    left = conn.execute("SELECT module, link FROM notifications").fetchall()
    assert ("maintenance", None) in [tuple(r) for r in left]
    assert not [r for r in left if r[0] == "procurement" or str(r[1] or "").startswith("/procurement/pr/")]
    assert conn.execute("SELECT COUNT(*) FROM wh_movements WHERE ref LIKE 'PR-%'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM wh_movements WHERE ref LIKE 'TEST-PR-%'").fetchone()[0] > 0
    assert conn.execute("SELECT COUNT(*) FROM wh_movements WHERE ref='PO-88123'").fetchone()[0] == 1,         "a supplier PO number that is not ours was retagged"
    assert conn.execute("SELECT stock_qty FROM wh_materials WHERE id=?", (mat["id"],)).fetchone()[0] == stock
    assert conn.execute("SELECT value FROM mnt_settings WHERE key='auto_reorder_pr'").fetchone()[0] == "0"

    # 3. restore: every row back, byte for byte
    R.restore(conn, STAMP1)
    now = snap(every)
    assert {t: now[t] for t in every if not t.startswith("zz_backup_")} == \
        {t: before[t] for t in every if not t.startswith("zz_backup_")}, "restore incomplete"
    p3, _ = svc.create_pr({"title": "after restore", "department": "IT"},
                          [{"item": "Mouse", "qty": 1, "unit_price": 10}], reqr, submit=False)
    assert p3 > max_id, "restored ids would be reused"

    # 4. apply again; the next request is number one; restore now refuses
    R.apply(conn, STAMP2)
    p4, _ = svc.create_pr({"title": "day one", "department": "IT"},
                          [{"item": "Mouse", "qty": 1, "unit_price": 10}], reqr, submit=False)
    no = conn.execute("SELECT pr_no FROM pr_requests WHERE id=?", (p4,)).fetchone()[0]
    assert p4 == 1 and no.endswith("-000001"), no
    try:
        R.restore(conn, STAMP1)
        raise AssertionError("restore merged into a live procurement")
    except SystemExit as e:
        assert "refused" in str(e)
    assert conn.execute("SELECT COUNT(*) FROM pr_requests").fetchone()[0] == 1
    try:
        R.main(["--apply"])
        raise AssertionError("--apply ran without --i-understand")
    except SystemExit:
        pass
    import config
    config.Config.DATABASE_URL = "postgresql://refuse.invalid/x"
    try:
        R.main(["--db", config.Config.DB_PATH])
        raise AssertionError("--db was silently ignored with DATABASE_URL set")
    except SystemExit:
        pass
    finally:
        config.Config.DATABASE_URL = ""
    conn.close()
    pg_statements(R)


def pg_statements(R):
    """No PostgreSQL here, so record what apply() sends on the PG path: one transaction,
    LOCK before the first read of data, TRUNCATE ... RESTART IDENTITY (transactional)
    instead of setval (not), and any failure rolls back without a commit."""
    from types import SimpleNamespace
    from app.db import _translate_sql

    class Rec:
        def __init__(self, fail_on=None):
            self.sql, self.fail_on = [], fail_on

        def execute(self, sql, params=()):
            if self.fail_on and sql.startswith(self.fail_on):
                raise RuntimeError("injected")
            self.sql.append(sql)
            rows = [{"name": t} for t in R.PR_TABLES + ["notifications", "wh_movements"]]                 if "information_schema" in sql else [{"c": 1}]
            return SimpleNamespace(fetchall=lambda: rows, fetchone=lambda: rows[0])

        def commit(self):
            self.sql.append("COMMIT")

        def rollback(self):
            self.sql.append("ROLLBACK")

    real, R._pg = R._pg, lambda: True
    try:
        rec = Rec()
        R.apply(rec, "20990101_0002")
        s = rec.sql
        assert s[0] == "COMMIT" and s[-1] == "COMMIT" and s.count("COMMIT") == 2, s
        lock = next(i for i, q in enumerate(s) if q.startswith("LOCK TABLE"))
        assert lock == 2 and s[lock].endswith("IN ACCESS EXCLUSIVE MODE"), s[:3]
        assert all(t in s[lock] for t in R.PR_TABLES + ["notifications", "wh_movements"])
        trunc = [q for q in s if q.startswith("TRUNCATE")]
        assert len(trunc) == 1 and trunc[0].endswith(" RESTART IDENTITY"), trunc
        # all 18 in ONE statement: PG refuses to truncate an FK parent on its own
        assert trunc[0][9:-17].split(", ") == R.PR_TABLES, trunc
        assert not [q for q in s if "setval" in q], "setval is not undone by a rollback"
        for q in (s[lock], trunc[0]):   # app/db.py passes these through unchanged
            assert _translate_sql(q)[0] == q
        for step in ("LOCK", "CREATE", "DELETE", "TRUNCATE"):
            rec = Rec(fail_on=step)
            try:
                R.apply(rec, "20990101_0003")
                raise AssertionError("no error raised")
            except RuntimeError:
                pass
            assert rec.sql[-1] == "ROLLBACK" and rec.sql.count("COMMIT") == 1, (step, rec.sql)
    finally:
        R._pg = real


if __name__ == "__main__":
    run()
