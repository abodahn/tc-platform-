# -*- coding: utf-8 -*-
"""A DELIBERATELY BLANKED TRANSLATION MUST STAY BLANK — /maintenance/workflow.

The editor hint promises: "Leave a language empty and its readers see the English
text." The write path already honoured that (an empty box is stored as ''), but
the boot seed refilled every *_ar / *_tr column that was NULL **or blank**, so a
Render restart handed the admin the shipped Arabic back.

The distinction lives in SQL and needs no marker column:
    NULL  -> never translated      -> the seed MAY fill it
    ''    -> cleared on purpose    -> the seed must NEVER touch it

Run:  python app/maintenance/tests_blank_lang.py
"""
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="blank_mnt_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                             # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402
from app.db import get_db                                 # noqa: E402
from app.maintenance import schema as MS                  # noqa: E402
from app.maintenance import workflow as wf                # noqa: E402

LANGS = ("en", "ar", "tr")
PASS, FAIL = [], []
I18N_KEY = re.compile(r'data-i18n(?:-ph|-title)?="([^"]+)"')


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  << %s" % (detail,)) if detail and not cond else ""))


def cell(status, col):
    conn = get_db()
    try:
        r = conn.execute("SELECT %s v FROM mnt_status_meta WHERE status=?" % col,
                         (status,)).fetchone()
        return r["v"] if r else None
    finally:
        conn.close()


def stored(status):
    conn = get_db()
    try:
        return dict(wf.text_rows(conn, "status")[status])
    finally:
        conn.close()


def reseed(times=3):
    """What a Render restart does: create_and_seed -> workflow.ensure on every boot."""
    for _ in range(times):
        conn = get_db()
        try:
            MS.create_and_seed(conn)
        finally:
            conn.close()


def set_lang(lang):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
        conn.commit()
    finally:
        conn.close()


def main():
    app = create_app()
    client = app.test_client()
    with client.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
        s["_csrf_token"] = "tok"

    KEY = "closed"          # the money/stock paragraph — the one that must not lie

    print("\n--- 1. baseline: the shipped Arabic is seeded ------------------")
    before = stored(KEY)
    ok("status '%s' ships with English + Arabic + Turkish" % KEY,
       before["en"] == wf.STATUS_TEXT[KEY]
       and before["ar"] == wf.STATUS_TEXT_AR[KEY]
       and before["tr"] == wf.STATUS_TEXT_TR[KEY], before)

    print("\n--- 2. clearing Arabic through the REAL POST route --------------")
    r = client.post("/maintenance/workflow/text",
                    data={"_csrf": "tok", "kind": "status", "key": KEY,
                          "explanation": before["en"],
                          "explanation_ar": "",
                          "explanation_tr": before["tr"]})
    ok("POST /maintenance/workflow/text -> 302 (raw)", r.status_code == 302, r.status_code)
    after = stored(KEY)
    ok("Arabic is stored as '' — cleared on purpose, NOT NULL",
       cell(KEY, "explanation_ar") == "", repr(cell(KEY, "explanation_ar")))
    ok("English is byte-identical", after["en"] == before["en"], repr(after["en"])[:70])
    ok("Turkish is byte-identical", after["tr"] == before["tr"], repr(after["tr"])[:70])

    print("\n--- 3. an Arabic reader now sees the English -------------------")
    ok("pick() falls back to the English for an Arabic reader",
       wf.pick("status", KEY, after, "ar") == before["en"],
       wf.pick("status", KEY, after, "ar")[:60])
    ok("the Turkish reader still gets Turkish",
       wf.pick("status", KEY, after, "tr") == before["tr"])
    ok("the editor box for Arabic renders EMPTY (text_rows keeps the blank)",
       after["ar"] == "", repr(after["ar"])[:60])

    print("\n--- 4. THREE more boots must NOT bring the Arabic back ----------")
    reseed(3)
    survived = stored(KEY)
    ok("after 3x create_and_seed the Arabic is STILL ''",
       cell(KEY, "explanation_ar") == "", repr(cell(KEY, "explanation_ar"))[:60])
    ok("after 3x create_and_seed English is still byte-identical",
       survived["en"] == before["en"])
    ok("after 3x create_and_seed Turkish is still byte-identical",
       survived["tr"] == before["tr"])
    ok("an Arabic reader still sees the English after the restarts",
       wf.pick("status", KEY, survived, "ar") == before["en"])
    conn = get_db()
    try:
        ok("no duplicate row was created by re-seeding",
           conn.execute("SELECT COUNT(*) c FROM mnt_status_meta WHERE status=?",
                        (KEY,)).fetchone()["c"] == 1)
    finally:
        conn.close()

    print("\n--- 5. a genuinely NULL column still gets seeded ----------------")
    conn = get_db()
    try:
        conn.execute("UPDATE mnt_status_meta SET explanation_ar=NULL WHERE status='repair'")
        conn.execute("UPDATE mnt_role_meta SET explanation_tr=NULL "
                     "WHERE role_key='storekeeper'")
        conn.execute("UPDATE mnt_doc SET body_ar=NULL WHERE section='reservation'")
        conn.commit()
    finally:
        conn.close()
    reseed(1)
    conn = get_db()
    try:
        st = wf.text_rows(conn, "status")["repair"]
        rl = wf.text_rows(conn, "role")["storekeeper"]
        dc = wf.text_rows(conn, "doc")["reservation"]
    finally:
        conn.close()
    ok("NULL status Arabic was seeded", st["ar"] == wf.STATUS_TEXT_AR["repair"])
    ok("NULL role Turkish was seeded", rl["tr"] == wf.ROLE_TEXT_TR["storekeeper"])
    ok("NULL doc Arabic was seeded", dc["ar"] == wf.DOC_TEXT_AR["reservation"])

    print("\n--- 6. an admin-AUTHORED translation survives re-seeding --------")
    AUTH_AR = "نص المسؤول لحالة الإصلاح — لا يجوز استبداله."
    r = client.post("/maintenance/workflow/text",
                    data={"_csrf": "tok", "kind": "status", "key": "repair",
                          "explanation": wf.STATUS_TEXT["repair"],
                          "explanation_ar": AUTH_AR,
                          "explanation_tr": wf.STATUS_TEXT_TR["repair"]})
    ok("POST saving an authored Arabic -> 302 (raw)", r.status_code == 302, r.status_code)
    reseed(3)
    auth = stored("repair")
    ok("the admin's Arabic is untouched after 3x create_and_seed",
       auth["ar"] == AUTH_AR, repr(auth["ar"])[:60])
    ok("an Arabic reader sees the admin's own wording",
       wf.pick("status", "repair", auth, "ar") == AUTH_AR)
    ok("the admin's English edit also survived", auth["en"] == wf.STATUS_TEXT["repair"])

    print("\n--- 7. clearing works on roles and docs too --------------------")
    r_before, d_before = None, None
    conn = get_db()
    try:
        r_before = dict(wf.text_rows(conn, "role")["storekeeper"])
        d_before = dict(wf.text_rows(conn, "doc")["reservation"])
    finally:
        conn.close()
    client.post("/maintenance/workflow/text",
                data={"_csrf": "tok", "kind": "role", "key": "storekeeper",
                      "explanation": r_before["en"], "explanation_ar": "",
                      "explanation_tr": r_before["tr"]})
    client.post("/maintenance/workflow/text",
                data={"_csrf": "tok", "kind": "doc", "key": "reservation",
                      "explanation": d_before["en"], "explanation_ar": "",
                      "explanation_tr": d_before["tr"]})
    reseed(3)
    conn = get_db()
    try:
        r_after = dict(wf.text_rows(conn, "role")["storekeeper"])
        d_after = dict(wf.text_rows(conn, "doc")["reservation"])
    finally:
        conn.close()
    ok("a cleared ROLE Arabic stays '' across 3 boots", r_after["ar"] == "",
       repr(r_after["ar"])[:60])
    ok("the role's English + Turkish are byte-identical",
       r_after["en"] == r_before["en"] and r_after["tr"] == r_before["tr"])
    ok("a cleared DOC Arabic stays '' across 3 boots", d_after["ar"] == "",
       repr(d_after["ar"])[:60])
    ok("the doc's English + Turkish are byte-identical",
       d_after["en"] == d_before["en"] and d_after["tr"] == d_before["tr"])
    ok("the stock-reservation paragraph an Arabic reader gets is the English one",
       wf.pick("doc", "reservation", d_after, "ar") == wf.DOC_TEXT["reservation"])

    print("\n--- 8. Reset still restores the shipped text -------------------")
    client.post("/maintenance/workflow/text",
                data={"_csrf": "tok", "kind": "status", "key": KEY, "reset": "1"})
    reseed(1)
    rst = stored(KEY)
    ok("after Reset + a boot the shipped Arabic is back",
       rst["ar"] == wf.STATUS_TEXT_AR[KEY], repr(rst["ar"])[:60])

    print("\n--- 9. the page still renders in all three languages ------------")
    dicts = {lg: json.load(io.open(str(REPO / "app" / "static" / "i18n" / (lg + ".json")),
                                   encoding="utf-8")) for lg in LANGS}
    for lg in LANGS:
        set_lang(lg)
        resp = client.get("/maintenance/workflow")
        ok("GET /maintenance/workflow [%s] -> 200 (raw)" % lg,
           resp.status_code == 200, resp.status_code)
        html = resp.data.decode("utf-8")
        keys = sorted(set(I18N_KEY.findall(html)))
        missing = [k for k in keys if k not in dicts[lg]]
        ok("all %d data-i18n keys resolve in %s.json" % (len(keys), lg),
           not missing, ", ".join(missing[:6]))

    print("\n================ %d passed, %d failed ================"
          % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
