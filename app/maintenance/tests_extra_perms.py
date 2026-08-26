"""A permission granted to ONE PERSON works on the maintenance screens.

An administrator can grant a permission two ways: through the user's role, or
directly on the user record (users.extra_perms) for the one person who needs one
extra screen. The sidebar honoured both. Procurement honoured both. Maintenance
checked has_permission(user["role"], perm) — the role only.

So granting somebody maint_admin as an extra permission put "Workflow &
Governance" in their sidebar and a 403 behind it. The module looked broken to
the one person an admin had deliberately given access to, and the admin had no
way to tell why: the grant was plainly there on the user record.

    python app/maintenance/tests_extra_perms.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ.pop("DATABASE_URL", None)
os.environ["TC_ENV"] = "development"

import config                                                     # noqa: E402
config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="xperm_"), "x.db")

from app import create_app                                        # noqa: E402
from app.db import get_db                                         # noqa: E402

app = create_app()
OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def make_user(username, role, extras=None):
    with app.app_context():
        conn = get_db()
        try:
            conn.execute("DELETE FROM users WHERE username=?", (username,))
            cur = conn.execute(
                "INSERT INTO users (username,password_hash,full_name,email,role,is_active,"
                "extra_perms,created_at) VALUES (?,?,?,?,?,1,?,datetime('now'))",
                (username, "x", username, username + "@t.test", role,
                 json.dumps(extras) if extras else None))
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()


def get(uid, path):
    with app.test_client() as cl:
        with cl.session_transaction() as s:
            s["uid"] = uid
            s["ep"] = 0
        return cl.get(path).status_code


def run():
    # A role with NO maintenance permissions at all.
    plain = make_user("xperm.plain", "normal_user")
    # The same role, plus maint_admin granted on the person.
    granted = make_user("xperm.granted", "normal_user", ["maint_view", "maint_admin"])

    print("the module is still closed to somebody with no grant")
    chk("no role permission, no extra -> refused",
        get(plain, "/maintenance/workflow") in (302, 403), get(plain, "/maintenance/workflow"))

    print("\nand open to somebody an admin granted it directly")
    code = get(granted, "/maintenance/workflow")
    chk("maint_view as an EXTRA permission opens the page", code == 200, code)

    print("\nthe grant is what decides it, not the role name")
    with app.app_context():
        from app.security import user_has_permission, has_permission
        conn = get_db()
        u = dict(conn.execute("SELECT * FROM users WHERE username='xperm.granted'").fetchone())
        conn.close()
    chk("the ROLE alone does not carry maint_admin",
        not has_permission(u["role"], "maint_admin"))
    chk("but the USER does, via extra_perms",
        user_has_permission(u, "maint_admin"))

    print("\nand an extra permission does not grant more than it says")
    chk("maint_view + maint_admin does not become proc_admin",
        not user_has_permission(u, "proc_admin"))

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
