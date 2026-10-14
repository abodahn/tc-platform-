"""Admin -> Users -> "Create test accounts": the whole purchase-cycle test cast
in one click, so the online version can be tested without thirteen forms.

The cast includes the Board, the CFO and the Managing Director — accounts that
sign the largest commitments — so the button is checked for what it must NOT
do as much as for what it does:

  * it creates every missing account with the right role, active, and the
    password the admin typed
  * a weak password, or two that do not match, creates nothing
  * a second click changes nothing — an existing account keeps its password
    and its role, so the button can never reset somebody's login
  * it needs manage_users and a CSRF token, like every other user write
  * the audit log records who did it, and never the password
  * the command-line script still re-applies roles (reset_existing=True)

    python app/tests_test_cast.py
"""
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="testcast_"), "t.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    from werkzeug.security import check_password_hash, generate_password_hash
    from app.db import get_db
    from app.test_cast import CAST, seed

    NAMES = [u for u, _r, _f, _d in CAST]
    ROLE = {u: r for u, r, _f, _d in CAST}
    GOOD = "Tester2026x"

    def q(sql, *a):
        with app.app_context():
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, a).fetchall()]
            finally:
                conn.close()

    def cast_rows():
        ph = ",".join("?" * len(NAMES))
        return {r["username"]: dict(r) for r in q(
            "SELECT username, role, is_active, password_hash FROM users "
            "WHERE username IN (%s)" % ph, *NAMES)}

    with app.app_context():
        # setup only; every request below runs outside this context (see q)
        admin_id = q("SELECT id FROM users WHERE role='super_admin' AND is_active=1 "
                     "LIMIT 1")[0]["id"]
        conn = get_db()
        try:
            # somebody who may NOT manage users
            conn.execute("INSERT INTO users (username,password_hash,full_name,email,role,"
                         "is_active,created_at) VALUES (?,?,?,?,?,1,?)",
                         ("plain.user", generate_password_hash("x"), "Plain", "p@x",
                          "normal_user", "2026-10-14 00:00:00"))
            # a cast name that already exists, with a DIFFERENT role and password
            conn.execute("DELETE FROM users WHERE username='bod.tester'")
            conn.execute("INSERT INTO users (username,password_hash,full_name,email,role,"
                         "is_active,created_at) VALUES (?,?,?,?,?,1,?)",
                         ("bod.tester", generate_password_hash("Original99"), "Pre",
                          "b@x", "normal_user", "2026-10-14 00:00:00"))
            conn.commit()
        finally:
            conn.close()
        plain_id = q("SELECT id FROM users WHERE username='plain.user'")[0]["id"]
        before = cast_rows()

    def client(uid):
        c = app.test_client()
        with c.session_transaction() as s:
            s["uid"] = uid
            s["ep"] = 0
            s["_csrf_token"] = "tok"
        return c

    def post(c, pw, pw2=None, csrf="tok"):
        data = {"password": pw, "password2": pw if pw2 is None else pw2}
        if csrf is not None:
            data["_csrf"] = csrf
        return c.post("/admin/users/test-cast", data=data)

    def flashes(c):
        with c.session_transaction() as s:
            return [m for _cat, m in s.get("_flashes", [])]

    if True:                      # NOT app.app_context(): see q() above
        adm = client(admin_id)

        # ---- 1. the panel ----------------------------------------------------
        print("\n1. the Users tab shows the cast and who already exists")
        r = adm.get("/admin/")
        body = r.get_data(as_text=True)
        chk("the admin page renders", r.status_code == 200, r.status_code)
        chk("the panel is there", 'data-i18n="admin.test_cast"' in body)
        chk("every cast username is listed", all(u in body for u in NAMES))
        chk("the panel posts to the new route", "/admin/users/test-cast" in body)

        # ---- 2. refusals create nothing -------------------------------------
        print("\n2. a weak or mistyped password creates nothing")
        for pw, pw2, why, key in (("1234", None, "1234", "pw_too_short"),
                                  ("abcdefgh", None, "letters only", "pw_need_alpha_digit"),
                                  (GOOD, GOOD + "!", "mismatch", "test_cast_mismatch"),
                                  ("", None, "blank", "pw_too_short")):
            c = client(admin_id)
            r = post(c, pw, pw2)
            chk("%-12s refused (%s)" % (why, key),
                r.status_code == 302 and key in flashes(c), flashes(c))
        chk("...and no account was created or changed", cast_rows() == before)

        # ---- 3. authority ---------------------------------------------------
        print("\n3. it needs manage_users and a CSRF token")
        r = post(client(plain_id), GOOD)
        chk("a signed-in user without manage_users gets 403 (not a redirect)",
            r.status_code == 403, r.status_code)
        chk("...and nothing was created", cast_rows() == before)
        r = post(client(admin_id), GOOD, csrf=None)
        chk("an admin POST without the CSRF token is refused",
            r.status_code in (302, 400, 403), r.status_code)
        chk("...and nothing was created", cast_rows() == before)

        # ---- 4. the real thing ----------------------------------------------
        print("\n4. a good password creates every missing account")
        c = client(admin_id)
        r = post(c, GOOD)
        chk("accepted", r.status_code == 302 and "test_cast_done" in flashes(c),
            flashes(c))
        after = cast_rows()
        missing = [u for u in NAMES if u not in before]
        chk("every cast account now exists", set(after) == set(NAMES),
            sorted(set(NAMES) - set(after)))
        chk("each NEW one carries exactly its cast role",
            all(after[u]["role"] == ROLE[u] for u in missing),
            [(u, after[u]["role"]) for u in missing if after[u]["role"] != ROLE[u]])
        chk("each new one is active", all(after[u]["is_active"] for u in missing))
        chk("each new one signs in with the typed password",
            all(check_password_hash(after[u]["password_hash"], GOOD) for u in missing))
        chk("the pre-existing bod.tester kept ITS role (not promoted to Board)",
            after["bod.tester"]["role"] == "normal_user", after["bod.tester"]["role"])
        chk("...and ITS password",
            after["bod.tester"]["password_hash"] == before["bod.tester"]["password_hash"])

        # ---- 5. a second click changes nothing ------------------------------
        print("\n5. a second click can never reset anybody's login")
        c = client(admin_id)
        post(c, "Different2027y")
        chk("it says nothing was changed", "test_cast_none" in flashes(c), flashes(c))
        chk("every password hash is byte-for-byte what it was", cast_rows() == after)

        # ---- 6. the audit trail ---------------------------------------------
        print("\n6. the audit log records it, and never the password")
        logs = q("SELECT username, action, detail FROM audit_logs "
                 "WHERE action='test_cast_create' ORDER BY id")
        chk("two audit rows (one per accepted click)", len(logs) == 2, len(logs))
        chk("attributed to the admin who clicked",
            all(r["username"] != "plain.user" for r in logs))
        chk("the password never appears in the audit log",
            all(GOOD not in (r["detail"] or "") and "Different2027y" not in (r["detail"] or "")
                for r in q("SELECT detail FROM audit_logs")))

        # ---- 7. the script keeps its behaviour ------------------------------
        print("\n7. the command-line script still re-applies roles")
        with app.app_context():
            conn = get_db()
            try:
                created, updated, skipped = seed(conn, "Script2026z", reset_existing=True)
            finally:
                conn.close()
        final = cast_rows()
        chk("reset_existing=True updates every account, creates none, skips none",
            not created and not skipped and sorted(updated) == sorted(NAMES))
        chk("bod.tester is now the Board, as the script documents",
            final["bod.tester"]["role"] == ROLE["bod.tester"])

        # ---- 8. no raw keys on screen ---------------------------------------
        print("\n8. every new message exists in all three languages")
        import json
        root = Path(__file__).resolve().parent / "static" / "i18n"
        keys = ("admin.test_cast", "admin.test_cast_hint", "admin.password_again",
                "test_cast_done", "test_cast_none", "test_cast_mismatch")
        for lg in ("en", "ar", "tr"):
            d = json.loads((root / ("%s.json" % lg)).read_text(encoding="utf-8"))
            gone = [k for k in keys if not d.get(k)]
            chk("%s: all %d keys present" % (lg, len(keys)), not gone, gone)

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
