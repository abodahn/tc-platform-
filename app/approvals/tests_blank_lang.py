# -*- coding: utf-8 -*-
"""A DELIBERATELY BLANKED TRANSLATION MUST STAY BLANK — /procurement/workflow.

The editor promises: empty a language box and that language's readers see the
English. That promise used to survive exactly until the next Render restart,
because the boot seed refilled every *_ar / *_tr column that was NULL **or
blank**, and because a blank box was never written to the database at all.

The distinction lives in SQL and needs no marker column:
    NULL  -> never translated      -> the seed MAY fill it
    ''    -> cleared on purpose    -> the seed must NEVER touch it

Run:  python app/approvals/tests_blank_lang.py
"""
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="blank_proc_"))
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
from app.approvals import constants as C                  # noqa: E402
from app.approvals import i18n_text as T                  # noqa: E402
from app.approvals import schema as S                     # noqa: E402
from app.approvals import services as svc                 # noqa: E402
from app.navigation import NAV, band_of                   # noqa: E402

LANGS = ("en", "ar", "tr")
PASS, FAIL = [], []
I18N_KEY = re.compile(r'data-i18n(?:-ph|-title)?="([^"]+)"')


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name
          + (("  << %s" % (detail,)) if detail and not cond else ""))


def row(conn, table, keycol, key, cols):
    r = conn.execute("SELECT %s FROM %s WHERE %s=?" % (", ".join(cols), table, keycol),
                     (key,)).fetchone()
    return dict(r) if r else None


def reseed(times=3):
    """What a Render restart does: create_and_seed on every boot."""
    for _ in range(times):
        conn = get_db()
        try:
            S.create_and_seed(conn)
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

    STAGE = "warehouse"
    COLS = ("explanation", "explanation_ar", "explanation_tr")

    print("\n--- 1. baseline: the shipped Arabic is seeded ------------------")
    conn = get_db()
    before = row(conn, "proc_stage_meta", "stage", STAGE, COLS)
    conn.close()
    ok("stage '%s' ships with English + Arabic + Turkish" % STAGE,
       before is not None
       and before["explanation"] == C.STAGE_EXPLAIN[STAGE]
       and before["explanation_ar"] == T.STAGE_AR[STAGE]
       and before["explanation_tr"] == T.STAGE_TR[STAGE], before)

    print("\n--- 2. clearing Arabic through the REAL POST route --------------")
    r = client.post("/procurement/workflow/stage",
                    data={"_csrf": "tok", "stage": STAGE,
                          "explanation": before["explanation"],
                          "explanation_ar": "",
                          "explanation_tr": before["explanation_tr"]})
    ok("POST /procurement/workflow/stage -> 302 (raw)", r.status_code == 302, r.status_code)
    conn = get_db()
    after = row(conn, "proc_stage_meta", "stage", STAGE, COLS)
    conn.close()
    ok("the row survived (an emptied language is not an empty row)", after is not None)
    ok("Arabic is stored as '' — cleared on purpose, NOT NULL",
       after["explanation_ar"] == "", repr(after["explanation_ar"]))
    ok("English is byte-identical", after["explanation"] == before["explanation"],
       repr(after["explanation"]))
    ok("Turkish is byte-identical", after["explanation_tr"] == before["explanation_tr"],
       repr(after["explanation_tr"]))

    print("\n--- 3. an Arabic reader now sees the English -------------------")
    v_ar = {s["stage"]: s for s in svc.workflow_view(None, "ar")["stages"]}
    ok("the Arabic page shows the English paragraph for '%s'" % STAGE,
       v_ar[STAGE]["explanation"] == C.STAGE_EXPLAIN[STAGE],
       v_ar[STAGE]["explanation"][:60])
    ok("the Arabic editor box is EMPTY, not refilled with the shipped Arabic",
       v_ar[STAGE]["edit"]["ar"] == "",
       repr(v_ar[STAGE]["edit"]["ar"])[:60])
    v_tr = {s["stage"]: s for s in svc.workflow_view(None, "tr")["stages"]}
    ok("Turkish readers are untouched by the Arabic clear",
       v_tr[STAGE]["explanation"] == T.STAGE_TR[STAGE])

    print("\n--- 4. THREE more boots must NOT bring the Arabic back ----------")
    reseed(3)
    conn = get_db()
    survived = row(conn, "proc_stage_meta", "stage", STAGE, COLS)
    conn.close()
    ok("after 3x create_and_seed the Arabic is STILL ''",
       survived["explanation_ar"] == "", repr(survived["explanation_ar"])[:60])
    ok("after 3x create_and_seed English is still byte-identical",
       survived["explanation"] == before["explanation"])
    ok("after 3x create_and_seed Turkish is still byte-identical",
       survived["explanation_tr"] == before["explanation_tr"])
    ok("an Arabic reader still sees the English after the restarts",
       {s["stage"]: s for s in svc.workflow_view(None, "ar")["stages"]
        }[STAGE]["explanation"] == C.STAGE_EXPLAIN[STAGE])

    print("\n--- 5. a genuinely NULL column still gets seeded ----------------")
    # a database deployed before the *_ar column existed: the feature must work
    conn = get_db()
    conn.execute("UPDATE proc_stage_meta SET explanation_ar=NULL WHERE stage='finance'")
    conn.execute("UPDATE proc_role_meta SET explanation_tr=NULL WHERE role_key='cfo'")
    conn.execute("UPDATE proc_doc SET body_ar=NULL WHERE section='budget_gate'")
    conn.commit()
    conn.close()
    reseed(1)
    conn = get_db()
    st = row(conn, "proc_stage_meta", "stage", "finance", ("explanation_ar",))
    rl = row(conn, "proc_role_meta", "role_key", "cfo", ("explanation_tr",))
    dc = row(conn, "proc_doc", "section", "budget_gate", ("body_ar",))
    conn.close()
    ok("NULL stage Arabic was seeded", st["explanation_ar"] == T.STAGE_AR["finance"])
    ok("NULL role Turkish was seeded", rl["explanation_tr"] == T.ROLE_TR["cfo"])
    ok("NULL doc Arabic was seeded", dc["body_ar"] == T.DOC_AR["budget_gate"])

    print("\n--- 6. an admin-AUTHORED translation survives re-seeding --------")
    AUTH_AR = "نص المسؤول للمرحلة المالية — لا يجوز استبداله."
    r = client.post("/procurement/workflow/stage",
                    data={"_csrf": "tok", "stage": "finance",
                          "explanation": C.STAGE_EXPLAIN["finance"],
                          "explanation_ar": AUTH_AR,
                          "explanation_tr": T.STAGE_TR["finance"]})
    ok("POST saving an authored Arabic -> 302 (raw)", r.status_code == 302, r.status_code)
    reseed(3)
    conn = get_db()
    auth = row(conn, "proc_stage_meta", "stage", "finance", COLS)
    conn.close()
    ok("the admin's Arabic is untouched after 3x create_and_seed",
       auth["explanation_ar"] == AUTH_AR, repr(auth["explanation_ar"])[:60])
    ok("an Arabic reader sees the admin's own wording",
       {s["stage"]: s for s in svc.workflow_view(None, "ar")["stages"]
        }["finance"]["explanation"] == AUTH_AR)

    print("\n--- 7. clearing works on roles and docs too --------------------")
    conn = get_db()
    r_before = row(conn, "proc_role_meta", "role_key", "cfo",
                   ("explanation", "explanation_ar", "explanation_tr"))
    conn.close()
    client.post("/procurement/workflow/role",
                data={"_csrf": "tok", "role_key": "cfo",
                      "explanation": r_before["explanation"],
                      "explanation_ar": "",
                      "explanation_tr": r_before["explanation_tr"]})
    d_before = None
    conn = get_db()
    d_before = row(conn, "proc_doc", "section", "budget_gate", ("body", "body_ar", "body_tr"))
    conn.close()
    client.post("/procurement/workflow/doc",
                data={"_csrf": "tok", "section": "budget_gate",
                      "body": d_before["body"], "body_ar": "",
                      "body_tr": d_before["body_tr"]})
    reseed(3)
    conn = get_db()
    r_after = row(conn, "proc_role_meta", "role_key", "cfo",
                  ("explanation", "explanation_ar", "explanation_tr"))
    d_after = row(conn, "proc_doc", "section", "budget_gate", ("body", "body_ar", "body_tr"))
    conn.close()
    ok("a cleared ROLE Arabic stays '' across 3 boots", r_after["explanation_ar"] == "",
       repr(r_after["explanation_ar"])[:60])
    ok("the role's English + Turkish are byte-identical",
       r_after["explanation"] == r_before["explanation"]
       and r_after["explanation_tr"] == r_before["explanation_tr"])
    ok("a cleared DOC Arabic stays '' across 3 boots", d_after["body_ar"] == "",
       repr(d_after["body_ar"])[:60])
    ok("the doc's English + Turkish are byte-identical",
       d_after["body"] == d_before["body"] and d_after["body_tr"] == d_before["body_tr"])

    print("\n--- 8. reset still restores the shipped text -------------------")
    client.post("/procurement/workflow/stage",
                data={"_csrf": "tok", "stage": STAGE, "reset": "explanation"})
    reseed(1)
    conn = get_db()
    rst = row(conn, "proc_stage_meta", "stage", STAGE, COLS)
    conn.close()
    ok("after Reset + a boot the shipped Arabic is back",
       rst["explanation_ar"] == T.STAGE_AR[STAGE], repr(rst["explanation_ar"])[:60])

    print("\n--- 9. the page still renders in all three languages ------------")
    dicts = {lg: json.load(io.open(str(REPO / "app" / "static" / "i18n" / (lg + ".json")),
                                   encoding="utf-8")) for lg in LANGS}
    for lg in LANGS:
        set_lang(lg)
        resp = client.get("/procurement/workflow")
        ok("GET /procurement/workflow [%s] -> 200 (raw)" % lg,
           resp.status_code == 200, resp.status_code)
        html = resp.data.decode("utf-8")
        keys = sorted(set(I18N_KEY.findall(html)))
        missing = [k for k in keys if k not in dicts[lg]]
        ok("all %d data-i18n keys resolve in %s.json" % (len(keys), lg),
           not missing, ", ".join(missing[:6]))

    print("\n--- 10. every sidebar key declared in navigation.py resolves -----")
    # Section 9 only sees data-i18n attributes in rendered HTML, so it is blind
    # to keys emitted from Python. Walk NAV itself: section headers, item labels
    # and band labels, including the WIP section the sidebar filters out (its
    # keys are still one promotion away from being on screen).
    nav_keys = sorted({k for sec in NAV
                       for k in [sec["section"]]
                       + [i[1] for i in sec["items"]]
                       + [band_of(i) for i in sec["items"]]
                       if k})
    for lg in LANGS:
        missing = [k for k in nav_keys if k not in dicts[lg]]
        ok("all %d navigation.py keys resolve in %s.json" % (len(nav_keys), lg),
           not missing, ", ".join(missing[:6]))

    print("\n================ %d passed, %d failed ================"
          % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
