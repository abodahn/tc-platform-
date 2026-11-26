"""Purchase requests are English only.

    python -m app.approvals.tests_english_only
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "en.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    from werkzeug.datastructures import MultiDict
    from app.approvals.english_only import arabic_in_form
    ok = MultiDict([("title", "Repair forklift battery"), ("item[]", ""), ("item_id[]", ""),
                    ("spare_id[]", ""), ("description[]", "Battery 48V"), ("notes", "")])
    assert arabic_in_form(ok) == []
    bad = MultiDict([("title", "تصليح بطارية"), ("notes", "urgent"),
                     ("item[]", ""), ("item_id[]", ""), ("spare_id[]", ""), ("description[]", "Battery"),
                     ("item[]", ""), ("item_id[]", ""), ("spare_id[]", ""), ("description[]", "بطارية ٤٨ فولت")])
    assert arabic_in_form(bad) == ["Title", "Line 2 description"], arabic_in_form(bad)
    # A catalogue pick keeps its master name even when it is Arabic.
    pick = MultiDict([("title", "Spare"), ("item[]", "SP-1 — ماكينة"), ("item_id[]", "5"),
                      ("spare_id[]", ""), ("description[]", "Needle")])
    assert arabic_in_form(pick) == []
    typed = MultiDict([("title", "Spare"), ("item[]", "ماكينة"), ("item_id[]", ""),
                       ("spare_id[]", ""), ("description[]", "Needle")])
    assert arabic_in_form(typed) == ["Line 1 item"]

    app = _app()
    from flask import g
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.db import get_db
        c = get_db()
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        n0 = c.execute("SELECT COUNT(*) n FROM pr_requests").fetchone()["n"]
        c.close()
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = admin["id"], 0, "tok"
        r = cli.post("/procurement/new", data={"_csrf": "tok", "title": "طلب صيانة", "department": "IT",
                                               "expenditure_kind": "opex", "item[]": "", "description[]": "Cable",
                                               "qty[]": "1", "unit[]": "Pcs", "action": "draft"})
        assert r.status_code == 302 and r.location.endswith("/procurement/new"), (r.status_code, r.location)
        c = get_db()
        assert c.execute("SELECT COUNT(*) n FROM pr_requests").fetchone()["n"] == n0, "Arabic request was saved"
        c.close()
        html = cli.get("/procurement/new").get_data(as_text=True)
        assert "english-only.js" in html and "proc.en_only.note" in html
    print("english only: OK")


if __name__ == "__main__":
    run()
