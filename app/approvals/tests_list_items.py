"""Purchase-request list shows what is being bought (items line), never money.

Run: python -m app.approvals.tests_list_items
"""
import os
import sys
import tempfile
from pathlib import Path

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def run():
    os.environ["TC_ENV"] = "development"
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("TC_DATABASE_URL", None)
    os.environ["TC_AUTO_TICKET_ENABLED"] = "false"
    with tempfile.TemporaryDirectory(prefix="list_items_", ignore_cleanup_errors=True) as tmp:
        import config
        config.Config.DB_PATH = Path(tmp) / "test.db"
        config.Config.DATABASE_URL = ""
        config.Config.UPLOAD_DIR = Path(tmp) / "uploads"
        from app import create_app
        app = create_app()
        from app.db import get_db
        from app.approvals import services as svc
        from werkzeug.security import generate_password_hash

        with app.app_context():
            conn = get_db()
            try:
                users = {}
                for name, role in (("req", "normal_user"), ("buyer", "purchasing_manager")):
                    username = "listitems_" + name
                    conn.execute(
                        "INSERT INTO users (username,password_hash,full_name,role,"
                        "is_active,created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                        (username, generate_password_hash("x"), username, role))
                    users[name] = dict(conn.execute(
                        "SELECT * FROM users WHERE username=?", (username,)).fetchone())
                conn.commit()
            finally:
                conn.close()
            names = ["Bearing 6205", "Belt B-42", "سير ناقل", "Grease cartridge", "Seal kit"]
            pr_id, pr_no = svc.create_pr(
                {"title": "for warehouse needs", "department": "Maintenance",
                 "currency": "EGP"},
                [{"item": n, "qty": q, "unit": "Pcs", "unit_price": 0}
                 for n, q in zip(names, (4, 1500000, 1.5, 3, 1))],
                users["req"], submit=True, priced=False)
            bundle = svc.get_pr(pr_id)
            ok, msg = svc.price_pr(pr_id, {it["id"]: 98765 for it in bundle["items"]},
                                   {"tax_rate": 0}, users["buyer"])
            chk("Purchasing prices the request", ok, msg)

            prev = svc.items_preview([{"id": pr_id}])
            lines, more = prev.get(pr_id, ([], 0))
            chk("preview keeps the first 3 lines in order",
                [l["item"] for l in lines] == names[:3], lines)
            chk("preview counts the rest", more == 2, more)
            chk("preview carries no price field",
                all(set(l) == {"item", "qty", "unit"} for l in lines))
            chk("empty input -> no query, empty map", svc.items_preview([]) == {})
            total = "{:,.0f}".format(svc.get_pr(pr_id)["pr"]["total"] or 0)

        def line_of(h):
            h = h[h.find('class="pr-items-line"'):]
            return h[:h.find("</div>")]

        for who in ("req", "buyer"):
            cl = app.test_client()
            with cl.session_transaction() as session:
                session["uid"] = users[who]["id"]
                session["ep"] = 0
            for url in ("/procurement/list", "/procurement/"):
                r = cl.get(url)
                html = r.get_data(as_text=True)
                chk("%s %s opens" % (who, url), r.status_code == 200, r.status_code)
                if url == "/procurement/" and pr_no not in html:
                    continue   # landing shows only recent/queue; list page is the contract
                chk("%s %s shows the first 3 items" % (who, url),
                    all(n in html for n in names[:3]))
                chk("%s %s hides items 4-5" % (who, url),
                    names[3] not in html and names[4] not in html)
                chk("%s %s shows +2 more" % (who, url),
                    "+2</bdi>" in html and 'data-i18n="proc.items_more"' in html)
                chk("%s %s isolates names for bidi" % (who, url),
                    'dir="auto" class="nm" title="سير ناقل"' in html)
                chk("%s %s formats qty" % (who, url), "×1.5<" in html and "×4<" in html
                    and "×1,500,000<" in html and "e+" not in line_of(html))
                # The Total column follows the existing money_views() rules; the
                # items line itself must never carry a price for anyone.
                line = html[html.find('class="pr-items-line"'):]
                line = line[:line.find("</div>")]
                chk("%s %s items line has no price" % (who, url),
                    line and not any(m in line for m in (total, "98,765", "98765")), line)
                chk("%s %s no unit price anywhere" % (who, url),
                    "98,765" not in html and "98765" not in html)
        print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
        return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
