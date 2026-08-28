"""The six hardening fixes, each asserted so that reverting it fails here.

They were found by review, not by a bug report, so nothing in the suite covered
any of them. Four are behavioural and are checked by driving the real routes.
Two are PostgreSQL DIALECT bugs -- they behave correctly on SQLite, which is what
this suite runs on, so no amount of exercising them here would catch a
regression. Those two are asserted against the emitted SQL instead, and are
labelled as such below rather than dressed up as behaviour they do not test.

    python app/tests_hardening.py
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.pop("DATABASE_URL", None)
os.environ["TC_ENV"] = "development"

import config                                                     # noqa: E402
_TMP = tempfile.mkdtemp(prefix="harden_")
config.Config.DB_PATH = os.path.join(_TMP, "h.db")
# Uploads too, not just the database: check 1 writes a file containing a <script>
# tag, and it has no business landing in the real .data/uploads of whatever
# checkout this is run from.
config.Config.UPLOAD_DIR = Path(_TMP) / "uploads"

from app import create_app                                        # noqa: E402
from app.db import get_db                                         # noqa: E402

app = create_app()
OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def db(fn):
    with app.app_context():
        conn = get_db()
        try:
            return fn(conn)
        finally:
            conn.close()


def admin_client():
    cl = app.test_client()
    with cl.session_transaction() as s:
        s["uid"] = 1                       # admin, seeded by create_app
        s["ep"] = 0
    return cl


# ---------------------------------------------------------------- 1. MIME ---
def attachments():
    """An .html and a .png, both stored exactly as an upload would store them."""
    updir = Path(config.Config.UPLOAD_DIR) / "maintenance"
    updir.mkdir(parents=True, exist_ok=True)
    (updir / "evil.html").write_text("<script>alert(document.cookie)</script>",
                                     encoding="utf-8")
    (updir / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 32)

    def ins(conn):
        ids = {}
        for fn, orig, ct in (("evil.html", "report.html", "text/html"),
                             ("photo.png", "photo.png", "image/png")):
            cur = conn.execute(
                "INSERT INTO mnt_attachments (entity_type, entity_id, kind, filename,"
                " original_name, content_type, size) VALUES ('ticket',1,'photo',?,?,?,10)",
                (fn, orig, ct))
            ids[ct] = cur.lastrowid
        conn.commit()
        return ids
    return db(ins)


def check_attachment_mime():
    print("\n1. an uploaded .html cannot run script on this origin")
    ids = attachments()
    cl = admin_client()

    r = cl.get("/maintenance/attachment/%d" % ids["text/html"])
    ct = r.headers.get("Content-Type", "")
    cd = r.headers.get("Content-Disposition", "")
    chk("the stored text/html is NOT served back as text/html", "text/html" not in ct, ct)
    chk("it is sent as a download, so no browser renders it",
        cd.lower().startswith("attachment"), cd)
    chk("the file itself is served verbatim -- the header is what protects the viewer",
        b"<script>" in r.get_data())

    r = cl.get("/maintenance/attachment/%d" % ids["image/png"])
    ct = r.headers.get("Content-Type", "")
    cd = r.headers.get("Content-Disposition", "")
    chk("a real photo still renders inline -- the ticket page shows these in <img>",
        "image/png" in ct, ct)
    chk("and is not forced to download", not cd.lower().startswith("attachment"), cd)


# ------------------------------------------------------- 2. reserved_qty ---
def check_spare_reserve():
    print("\n2. the spare-part reservation runs on PostgreSQL")
    print("   (SOURCE assertion: MAX(0,x) is valid SQLite and an error on")
    print("    PostgreSQL, where max() is an aggregate. This suite is SQLite,")
    print("    so behaviour here cannot tell the two apart.)")
    src = (ROOT / "app" / "maintenance" / "services.py").read_text(encoding="utf-8")
    # Comments stripped: the fix's own comment names the construct it removed.
    code = " ".join(ln.split("#")[0] for ln in src.splitlines())
    chk("no two-argument MAX( in the maintenance service layer",
        "MAX(0," not in code.replace(" ", ""))

    def scenario(conn):
        cur = conn.execute("INSERT INTO mnt_spare_parts (code, name, stock_qty,"
                           " reserved_qty, is_active) VALUES ('HARD-1','fixture',10,0,1)")
        sid = cur.lastrowid
        conn.commit()
        from app.maintenance import services as msvc
        msvc._reserve(conn, sid, 4)
        msvc._reserve(conn, sid, -9)       # release more than is held
        conn.commit()
        return conn.execute("SELECT reserved_qty r FROM mnt_spare_parts WHERE id=?",
                            (sid,)).fetchone()["r"]
    left = db(scenario)
    chk("over-releasing floors at zero rather than going negative", left == 0, left)


# ---------------------------------------------------------- 3. dismiss ---
def check_dismiss_owner():
    print("\n3. one person cannot dismiss another person's notification")

    def seed(conn):
        conn.execute("INSERT INTO notifications (severity, module, title, message,"
                     " is_read, target_user) VALUES ('critical','proc','Sign','x',0,'cfo')")
        nid = conn.execute("SELECT MAX(id) m FROM notifications").fetchone()["m"]
        conn.commit()
        return nid
    nid = db(seed)

    cl = admin_client()                    # signed in as admin, NOT as cfo
    cl.get("/")
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    cl.post("/notifications/read", json={"id": nid},
            headers={"X-CSRF-Token": tok or ""})

    still = db(lambda c: c.execute("SELECT is_read r FROM notifications WHERE id=?",
                                   (nid,)).fetchone()["r"])
    chk("the CFO's unread notification is still unread", not still, "is_read=%s" % still)

    def mine_seed(conn):
        conn.execute("INSERT INTO notifications (severity, module, title, message,"
                     " is_read, target_user) VALUES ('info','proc','Mine','x',0,'admin')")
        n = conn.execute("SELECT MAX(id) m FROM notifications").fetchone()["m"]
        conn.commit()
        return n
    own = db(mine_seed)
    cl.post("/notifications/read", json={"id": own},
            headers={"X-CSRF-Token": tok or ""})
    mine = db(lambda c: c.execute("SELECT is_read r FROM notifications WHERE id=?",
                                  (own,)).fetchone()["r"])
    chk("but I can still dismiss my own", bool(mine), "is_read=%s" % mine)


# ------------------------------------------------------------ 4. search ---
def check_search_case():
    print("\n4. catalogue search finds a mixed-case item")
    print("   (SOURCE assertion: SQLite's LIKE ignores ASCII case and")
    print("    PostgreSQL's does not, so on SQLite this passes either way.)")
    for rel in ("app/approvals/services.py", "app/routes/approvals.py",
                "app/routes/main.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        chk("%s lowers both sides" % rel, "LOWER(code) LIKE LOWER(?)" in src)

    def seed(conn):
        conn.execute("INSERT INTO proc_items (code, name, unit, active) "
                     "VALUES ('Hard-Steel-01','Needle Bar Assembly','Pcs',1)")
        conn.commit()
    db(seed)
    cl = admin_client()
    r = cl.get("/search?q=needle")
    chk("typing 'needle' finds 'Needle Bar Assembly'",
        "Needle Bar Assembly" in r.get_data(as_text=True))
    r = cl.get("/search?q=HARD-STEEL")
    chk("and 'HARD-STEEL' finds 'Hard-Steel-01'",
        "Hard-Steel-01" in r.get_data(as_text=True))


# ---------------------------------------------------------------- 5. FX ---
def check_budget_fx():
    print("\n5. an EGP request is not multiplied by a leftover FX rate")

    def seed(conn):
        conn.execute("INSERT INTO proc_budgets (department, period, currency, amount) "
                     "VALUES ('HARDEN','2026','EGP',100000)")
        # Repriced from USD back to EGP. fx_rate is NOT cleared on the way back
        # (see approvals/reports.py) -- this is the row that read as 96,000.
        conn.execute("INSERT INTO pr_requests (pr_no, department, status, total,"
                     " currency, fx_rate, tax_rate, request_date) "
                     "VALUES ('HARD-EGP','HARDEN','approved',2000,'EGP',48,0,'2026-03-01')")
        conn.commit()
    db(seed)

    from app.approvals import services as asvc
    with app.app_context():
        st = asvc.budget_status("HARDEN", "2026")
    chk("2,000 EGP counts as 2,000", round(st["spent"]) == 2000, st["spent"])
    chk("so the department is not falsely over its 100,000 budget", not st["over"])

    def usd(conn):
        conn.execute("INSERT INTO pr_requests (pr_no, department, status, total,"
                     " currency, fx_rate, tax_rate, request_date) "
                     "VALUES ('HARD-USD','HARDEN','approved',1000,'USD',48,0,'2026-03-01')")
        conn.commit()
    db(usd)
    with app.app_context():
        st = asvc.budget_status("HARDEN", "2026")
    chk("a genuine 1,000 USD request still converts at 48",
        round(st["spent"]) == 50000, st["spent"])


# ---------------------------------------------------- 6. open redirect ---
def check_open_redirect():
    print("\n6. ?next cannot bounce a signed-in user off the site")
    for hostile in ("//evil.example.com", "/\\evil.example.com",
                    "https://evil.example.com"):
        cl = app.test_client()
        cl.get("/login")
        with cl.session_transaction() as s:
            tok = s.get("_csrf_token")
        r = cl.post("/login", data={"username": "admin", "password": "Admin@12345",
                                    "next": hostile, "_csrf": tok},
                    follow_redirects=False)
        loc = r.headers.get("Location", "")
        chk("%-26s does not leave the site" % hostile,
            "evil.example.com" not in loc, loc)

    cl = app.test_client()
    cl.get("/login")
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = cl.post("/login", data={"username": "admin", "password": "Admin@12345",
                                "next": "/maintenance/tickets", "_csrf": tok},
                follow_redirects=False)
    chk("a real internal next still works",
        "/maintenance/tickets" in r.headers.get("Location", ""),
        r.headers.get("Location", ""))


def run():
    check_attachment_mime()
    check_spare_reserve()
    check_dismiss_owner()
    check_search_case()
    check_budget_fx()
    check_open_redirect()
    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
