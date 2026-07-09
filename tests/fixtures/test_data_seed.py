"""
Standalone QA test-data seed.

The platform already seeds demo + edge-case data idempotently on boot
(app.db.init_db + each module's create_and_seed). This module is a thin,
explicit entry point so QA can (re)load the dataset and add a couple of extra
edge records on demand, e.g.:

    python -m tests.fixtures.test_data_seed

It NEVER touches production data destructively — all inserts are additive and
guarded, and it uses whatever DB the app is configured for (SQLite locally).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def seed():
    from app import create_app
    app = create_app()                       # runs init_db + all module seeders
    with app.app_context():
        from app.db import get_db, utcnow
        conn = get_db()
        try:
            # extra edge-case rows for exploratory testing (guarded, additive)
            extras = [
                ("assets", "INSERT INTO assets (tag,name,category,department,owner,location,"
                           "purchase_cost,purchase_date,warranty_expiry,status,condition,maint_cost_ytd) "
                           "VALUES ('TC-EDGE-1','عربة نقل (Arabic name)','Trolley','Warehouse','مستخدم','مخزن',"
                           "5000,'2016-01-01','2015-01-01','in_use','poor',4800)"),
                ("hr_probation", "INSERT INTO hr_probation (employee_code,name,department,manager,start_date,"
                                 "due_date,status,score) VALUES ('E999','Çalışan (Turkish)','Sewing','Mgr',"
                                 "'2026-01-01','2026-02-01','failed',35)"),
            ]
            for _t, sql in extras:
                try:
                    conn.execute(sql); conn.commit()
                except Exception:
                    conn.rollback()
            # summary
            for t in ("users", "assets", "payroll_rows", "hr_probation", "paper_usage",
                      "mnt_tickets", "pr_requests"):
                try:
                    n = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                    print(f"  {t:14} {n} rows")
                except Exception:
                    print(f"  {t:14} (table not present)")
        finally:
            conn.close()
    print("Seed complete.")


if __name__ == "__main__":
    seed()
