"""Pack-size units on the request form: "2 x Box (12)" is stored as 24 Pcs.

    python -m app.approvals.tests_pack_units
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "packs.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from werkzeug.datastructures import MultiDict
        from app.approvals import constants as C, services as svc
        from app.db import get_db
        from app.routes.approvals import _parse_items, _unpack

        # offered on the form, right after Box; never master data
        assert C.PR_UNITS[C.PR_UNITS.index("Box") + 1:][:2] == ["Box (12)", "Box (24)"]
        assert "Box (12)" not in C.UNITS

        assert _unpack("Box (12)", "2") == ("Pcs", 24.0, "2 x Box (12) = 24 Pcs")
        assert _unpack("Box (24)", "1.5") == ("Pcs", 36.0, "1.5 x Box (24) = 36 Pcs")
        assert _unpack("Kg", "7") == ("Kg", "7", "")            # untouched
        assert _unpack("Box (12)", "abc")[1] == "abc"            # left for create_pr to reject

        form = MultiDict([("item[]", ""), ("description[]", "Gloves"), ("unit[]", "Box (12)"),
                          ("qty[]", "2"), ("item_notes[]", "nitrile"),
                          ("item[]", ""), ("description[]", "Tape"), ("unit[]", "Box (24)"),
                          ("qty[]", "3"), ("item_notes[]", ""),
                          ("item[]", ""), ("description[]", "Oil"), ("unit[]", "Liter"),
                          ("qty[]", "5"), ("item_notes[]", "")])
        items = _parse_items(form)
        assert [(i["unit"], i["qty"]) for i in items] == [("Pcs", 24.0), ("Pcs", 72.0), ("Liter", "5")]
        assert items[0]["notes"] == "2 x Box (12) = 24 Pcs | nitrile", items[0]["notes"]
        assert items[1]["notes"] == "3 x Box (24) = 72 Pcs"

        c = get_db()
        req = dict(c.execute("SELECT * FROM users ORDER BY id LIMIT 1").fetchone())
        c.close()
        pid = svc.create_pr({"title": "Packs", "department": "Maintenance"}, items, req,
                            submit=False, priced=False)
        pid = pid[0] if isinstance(pid, tuple) else pid
        c = get_db()
        rows = [dict(r) for r in c.execute(
            "SELECT unit, qty FROM pr_items WHERE pr_id=? ORDER BY id", (pid,)).fetchall()]
        c.close()
        assert [(r["unit"], float(r["qty"])) for r in rows] == [("Pcs", 24), ("Pcs", 72), ("Liter", 5)], rows
    print("pack units: OK")


if __name__ == "__main__":
    run()
