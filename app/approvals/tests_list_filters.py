"""Requests list: search, department / priority / date filters and sorting.

    python -m app.approvals.tests_list_filters
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "listf.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.approvals import services as svc
        from app.db import get_db

        c = get_db()
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        c.close()
        ids = {}
        for t, d, item, prio, price in (("Pumps", "Production", "Mechanical seal", "urgent", 4200),
                                        ("Needles", "Sewing", "Needle DBx1", "normal", 900),
                                        ("Blades", "Cutting", "Knife blade", "top_urgent", 15600)):
            pid, _ = svc.create_pr({"title": t, "department": d, "priority": prio},
                                   [{"item": item, "qty": 2, "unit_price": price / 2}],
                                   admin, submit=False, priced=True)
            ids[t] = pid
        c = get_db()
        # A USD request: 100 x 50 = 5,000 EGP, so it ranks between 4,200 and 15,600.
        c.execute("UPDATE pr_requests SET currency='USD', fx_rate=50, total=100 WHERE id=?", (ids["Pumps"],))
        c.execute("UPDATE pr_requests SET request_date='2026-01-10' WHERE id=?", (ids["Needles"],))
        c.commit()
        c.close()

        # Only this test's requests: the seed data carries a demo request too.
        titles = lambda **kw: [p["title"] for p in svc.list_prs(**kw) if p["id"] in ids.values()]
        assert titles(q="needle") == ["Needles"]                    # by line item
        assert titles(q="PUMPS") == ["Pumps"]                       # by title, any case
        assert titles(dept="Cutting") == ["Blades"]
        assert titles(priority="top_urgent") == ["Blades"]
        assert titles(date_to="2026-01-31") == ["Needles"]
        assert "Needles" not in titles(date_from="2026-02-01")
        assert titles(sort="value_desc") == ["Blades", "Pumps", "Needles"]   # on the EGP value
        assert titles(sort="priority")[:2] == ["Blades", "Pumps"]
        assert titles(sort="old") == ["Pumps", "Needles", "Blades"]
        assert titles(sort="1; DROP TABLE pr_requests") == titles(sort="new")  # whitelist

        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"] = admin["id"], 0
        html = cli.get("/procurement/list?q=needle&sort=bogus&from=bad").get_data(as_text=True)
        assert "Needles" in html and "Blades" not in html
        assert "list.xlsx?q=needle" in html                         # Excel keeps the filters
        x = cli.get("/procurement/list.xlsx?dept=Sewing")
        assert x.status_code == 200 and x.mimetype.endswith("sheet")
    print("list filters: OK")


if __name__ == "__main__":
    run()
