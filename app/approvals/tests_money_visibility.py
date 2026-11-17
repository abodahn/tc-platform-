"""Money visibility regression self-test using an isolated repository-local DB.

Run: python -m app.approvals.tests_money_visibility
"""
import io
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
    with tempfile.TemporaryDirectory(prefix="money_visibility_", ignore_cleanup_errors=True) as tmp:
        import config
        config.Config.DB_PATH = Path(tmp) / "test.db"
        config.Config.DATABASE_URL = ""
        config.Config.UPLOAD_DIR = Path(tmp) / "uploads"
        from app import create_app
        app = create_app()
        from app.db import get_db
        from app.approvals import services as svc
        from app.approvals import pdf
        from werkzeug.security import generate_password_hash

        with app.app_context():
            conn = get_db()
            try:
                users = {}
                for name, role in (("A", "normal_user"), ("B", "normal_user"),
                                   ("store", "storekeeper"),
                                   ("buyer", "purchasing_manager"),
                                   ("finance", "finance_manager")):
                    username = "money_" + name
                    conn.execute(
                        "INSERT INTO users (username,password_hash,full_name,role,"
                        "is_active,created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                        (username, generate_password_hash("x"), username, role))
                    users[name] = dict(conn.execute("SELECT * FROM users WHERE username=?",
                                                    (username,)).fetchone())
                users["admin"] = dict(conn.execute(
                    "SELECT * FROM users WHERE role='super_admin' AND is_active=1 "
                    "AND COALESCE(account_status,'active')='active' LIMIT 1").fetchone())
                conn.commit()
            finally:
                conn.close()
            pr_id, pr_no = svc.create_pr(
                {"title": "Money visibility request", "department": "Maintenance",
                 "currency": "EGP"},
                [{"item": "Visibility part", "qty": 1, "unit_price": 0}],
                users["A"], submit=True, priced=False)
            bundle = svc.get_pr(pr_id)
            priced, msg = svc.price_pr(pr_id, {bundle["items"][0]["id"]: 4321},
                                       {"tax_rate": 0}, users["buyer"])
            chk("Purchasing prices the request", priced, msg)
            svc.add_attachment(pr_id, "quote.csv", "text/csv", "YSxi", 3, users["A"])
            bundle = svc.get_pr(pr_id)
            att_id = bundle["attachments"][0]["id"]
            warehouse =[s for s in bundle["steps"] if s["stage"] == "warehouse"]
            if warehouse:
                chk("Storekeeper can sign warehouse step",
                    svc.can_act_step(users["store"], warehouse[0]))
            else:
                conn = get_db()
                try:
                    conn.execute("UPDATE pr_steps SET approver_user=? WHERE id=?",
                                 (users["store"]["username"], bundle["steps"][0]["id"]))
                    conn.commit()
                finally:
                    conn.close()
                bundle = svc.get_pr(pr_id)
                chk("Storekeeper signed a ladder step", any(
                    s["approver_user"] == users["store"]["username"] for s in bundle["steps"]))
            expected = {"A": "own", "B": "", "store": "full", "buyer": "full",
                        "finance": "full", "admin": "full"}
            for name, level in expected.items():
                chk(name + " money_view", svc.money_view(users[name], bundle["pr"],
                                                         bundle["steps"]) == level)
            chk("B batch hides money", not svc.money_views(users["B"], [bundle["pr"]])[pr_id])
            chk("Storekeeper batch shows money", bool(svc.money_views(
                users["store"], [bundle["pr"]])[pr_id]))
            chk("Inactive admin cannot see money", svc.money_view(
                dict(users["admin"], account_status="suspended"), bundle["pr"], bundle["steps"]) == "")
            chk("No user cannot see money", svc.money_view(None, bundle["pr"], []) == "")
            chk("PR PDFs render with and without money",
                bool(pdf.pr_pdf(bundle, money=False)) and bool(pdf.pr_pdf(bundle, money=True)))

        # Requests outside app_context avoid leaking current_user's g cache.
        for name, level in expected.items():
            cl = app.test_client()
            with cl.session_transaction() as session:
                session["uid"] = users[name]["id"]
                session["ep"] = 0
            for url in ("/procurement/list", "/procurement/pr/%d" % pr_id):
                r = cl.get(url)
                html = r.get_data(as_text=True)
                chk(name + " opens " + url, r.status_code == 200, r.status_code)
                chk(name + " request stays visible " + url, pr_no in html)
                chk(name + " money visibility " + url, ("4,321" in html) == bool(level))
            r = cl.get("/procurement/pr/%d/pdf" % pr_id)
            chk(name + " PR PDF stays open", r.status_code == 200, r.status_code)
            # money gate runs before the status gate: 403 without money, 400 (not approved yet) with it
            r = cl.get("/procurement/pr/%d/po.pdf" % pr_id)
            chk(name + " PO PDF gate", r.status_code == (400 if level else 403), r.status_code)
            for dataset in ("payments", "register", "spend_dept", "spend_vendor", "open_orders"):
                r = cl.get("/procurement/export/%s.csv" % dataset)
                want = 200 if name in ("buyer", "finance", "admin") else 403
                chk(name + " CSV " + dataset, r.status_code == want, r.status_code)
            # the shared report engines carry the same money: cross-request, so global perms only
            want = 200 if name in ("buyer", "finance", "admin") else 403
            for url in ("/reporting/proc_requests", "/reporting/proc_payables.csv"):
                r = cl.get(url)
                chk(name + " report " + url, r.status_code == want, r.status_code)
            # the older /reports catalogue also needs view_reports / export_reports
            # (refuses with 404); only the admin holds those among these users
            for url in ("/reports/proc_payments", "/reports/proc_prs.csv"):
                r = cl.get(url)
                chk(name + " report " + url, (r.status_code == 200) == (name == "admin"),
                    r.status_code)
            r = cl.get("/reporting/proc_off_catalogue")
            chk(name + " money-free report stays open", r.status_code == 200, r.status_code)
            r = cl.get("/procurement/attachment/%d" % att_id)
            chk(name + " PR document gate", r.status_code == (200 if level else 403), r.status_code)
            r = cl.get("/procurement/analytics")
            chk(name + " analytics opens", r.status_code == 200, r.status_code)
            chk(name + " analytics hides money data", ("committed_spend" in r.get_data(as_text=True))
                == (name in ("buyer", "finance", "admin")))
            r = cl.get("/procurement/list.xlsx")
            chk(name + " Excel stays open", r.status_code == 200, r.status_code)
            if r.status_code == 200:
                import openpyxl
                ws = openpyxl.load_workbook(io.BytesIO(r.data)).active
                row = next(row for row in ws.iter_rows(min_row=2, values_only=True) if row[0] == pr_no)
                chk(name + " Excel total visibility", row[7] == (4321 if level else None))
                chk(name + " Excel payment visibility", (row[14] is not None) == (level == "full"))
        print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
        return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
