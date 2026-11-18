# -*- coding: utf-8 -*-
"""
Procurement cycle training guide self-test — runs against a throwaway database.
    python -m app.approvals.tests_guide

Proves, by execution:
  (a) every proc.guide.* key exists, non-empty, in en / ar / tr, and the three
      files carry the same proc.guide.* key set;
  (b) every screen label a guide string points at ([[btn:..]] / [[ui:..]])
      resolves in all three languages, and each string names the same screens
      in every language;
  (c) /procurement/guide returns 200 for a requester and for an admin in
      en / ar / tr, with every role section and the troubleshooting section,
      the Arabic page right-to-left and in Arabic, no raw marker or raw key left;
  (d) "you are here": the requester's own section is marked, a rung it does
      not sign is not; the Financial Controller's rung and Finance are marked;
  (e) a signed-in user without procurement access is refused (403), and an
      anonymous visitor is sent to sign in.
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="guide_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                          # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                             # noqa: E402
from app.approvals import guide as G                   # noqa: E402

app = create_app()
PASS = []
ARABIC = re.compile(r"[؀-ۿ]")


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


# ----------------------------------------------------------------------- (a)
print("-- (a) every proc.guide.* key in all three languages --")
D = {lg: json.load(open(G._I18N / f"{lg}.json", encoding="utf-8")) for lg in G.LANGS}
sets = {lg: {k for k in D[lg] if k.startswith("proc.guide.")} for lg in G.LANGS}
ok("en defines proc.guide.* keys (%d)" % len(sets["en"]), len(sets["en"]) > 150)
ok("ar and tr carry exactly the en key set (missing ar %s, tr %s; extra ar %s, tr %s)"
   % (sorted(sets["en"] - sets["ar"])[:5], sorted(sets["en"] - sets["tr"])[:5],
      sorted(sets["ar"] - sets["en"])[:5], sorted(sets["tr"] - sets["en"])[:5]),
   sets["en"] == sets["ar"] == sets["tr"])
empty = [(lg, k) for lg in G.LANGS for k in sets["en"] if not str(D[lg].get(k) or "").strip()]
ok("no proc.guide.* value is blank %r" % empty[:5], not empty)
ok("nav.proc_guide exists in all three", all(D[lg].get("nav.proc_guide") for lg in G.LANGS))
same = [k for k in sets["en"] if k != "proc.guide.search_terms" and D["en"][k] == D["ar"].get(k)]
ok("no Arabic value is the English text left untranslated %r" % same[:5], not same)
no_ar = [k for k in sets["en"] if not ARABIC.search(D["ar"][k])]
ok("every Arabic value carries Arabic script %r" % no_ar[:5], not no_ar)

# ----------------------------------------------------------------------- (b)
print("\n-- (b) every button / screen label the guide names resolves --")
chips = G.chip_keys()
ok("the guide names real screen labels (%d keys)" % len(chips), len(chips) > 40)
missing = [(lg, c) for lg in G.LANGS for c in chips if not D[lg].get(c)]
ok("every referenced label resolves in en/ar/tr %r" % missing[:5], not missing)
diff = [k for k in sets["en"]
        if {m[1] for m in G.CHIP.findall(D["en"][k])}
        != {m[1] for m in G.CHIP.findall(D["ar"][k])}
        or {m[1] for m in G.CHIP.findall(D["en"][k])}
        != {m[1] for m in G.CHIP.findall(D["tr"][k])}]
ok("each string names the same screen labels in every language %r" % diff[:5], not diff)
for must in ("proc.submit", "proc.act.review_sign", "proc.act.approve_sign", "proc.wh_save",
             "proc.save_pricing", "proc.issue_po", "proc.record_receipt", "proc.add_payment",
             "proc.close", "proc.add_deleg", "proc.reject", "proc.hold", "proc.back"):
    ok("the guide uses the real key %s" % must, must in chips)
for lg in G.LANGS:
    html = str(G.rich(lg, "proc.guide.req.step.5"))
    ok("%s: a rendered step carries the screen's own button label %r" % (lg, D[lg]["proc.submit"]),
       'data-i18n="proc.submit"' in html and D[lg]["proc.submit"] in html and "[[" not in html)

# ----------------------------------------------------------------------- setup
SECTIONS = [s for s, _ in G.SECTIONS]


def mkuser(conn, username, role, lang):
    conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, role, "
                 "is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                 (username, "x", username.title(), role))
    conn.execute("UPDATE users SET lang_pref=? WHERE username=?", (lang, username))
    conn.commit()
    return conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]


def get(uid, path="/procurement/guide"):
    with app.test_client() as cl:
        if uid:
            with cl.session_transaction() as s:
                s["uid"] = uid
                s["ep"] = 0
                s["_csrf_token"] = "tok"
        return cl.get(path)


with app.app_context():
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    conn = get_db()
    create_and_seed(conn)
    conn.commit()
    ids = {}
    for lg in G.LANGS:
        ids[("req", lg)] = mkuser(conn, "g_req_" + lg, "normal_user", lg)
        ids[("admin", lg)] = mkuser(conn, "g_admin_" + lg, "super_admin", lg)
    ids["ctrl"] = mkuser(conn, "g_ctrl", "financial_controller", "en")
    ids["none"] = mkuser(conn, "g_none", "itsm_user", "en")
    conn.close()

# ----------------------------------------------------------------------- (c)
print("\n-- (c) the page renders for a requester and an admin, in en/ar/tr --")
for who in ("req", "admin"):
    for lg in G.LANGS:
        r = get(ids[(who, lg)])
        body = r.get_data(as_text=True)
        ok("%s/%s: GET /procurement/guide -> 200 (got %d)" % (who, lg, r.status_code),
           r.status_code == 200)
        absent = [s for s in SECTIONS + ["faq"] if 'id="g-%s"' % s not in body]
        ok("%s/%s: every role section + troubleshooting present %r" % (who, lg, absent), not absent)
        ok("%s/%s: the page title is in the reader's language" % (who, lg),
           D[lg]["proc.guide.title"] in body)
        ok("%s/%s: no raw [[marker]] or unresolved proc.guide key in the text" % (who, lg),
           "[[" not in body and not re.search(r">\s*proc\.guide\.", body))
        if lg == "ar":
            ok("%s/ar: the page is right-to-left" % who, 'dir="rtl"' in body)
            ok("%s/ar: the Arabic travel paragraph is shown, the English one is not" % who,
               D["ar"]["proc.guide.travel_p"] in body and D["en"]["proc.guide.travel_p"] not in body)
        if lg == "tr":
            ok("%s/tr: the Turkish steps are shown" % who, D["tr"]["proc.guide.h.steps"] in body)

# ----------------------------------------------------------------------- (d)
print("\n-- (d) 'you are here' follows the reader's role --")
body = get(ids[("req", "en")]).get_data(as_text=True)
ok("requester: own section is marked", 'class="panel g-sec is-mine" id="g-req"' in body)
ok("requester: the CFO rung is not marked", 'class="panel g-sec" id="g-cfo"' in body)
body = get(ids["ctrl"]).get_data(as_text=True)
ok("financial controller: the Control rung is marked", 'class="panel g-sec is-mine" id="g-ctrl"' in body)
ok("financial controller: Finance is marked (holds the pay right)",
   'class="panel g-sec is-mine" id="g-fin"' in body)
ok("financial controller: the Warehouse rung is not marked", 'class="panel g-sec" id="g-wh"' in body)
body = get(ids[("admin", "en")]).get_data(as_text=True)
ok("admin: the admin section is marked", 'class="panel g-sec is-mine" id="g-admin"' in body)
ok("admin: not every section is marked (Finance is not)", 'class="panel g-sec" id="g-fin"' in body)

# ----------------------------------------------------------------------- (e)
print("\n-- (e) no procurement access -> refused --")
r = get(ids["none"])
# itsm_user is scope-locked to its own system, so the platform may bounce it
# away before the permission check: either way it must not get the guide.
ok("a user without procurement access is refused or sent away (got %d -> %s)"
   % (r.status_code, r.headers.get("Location")),
   r.status_code == 403 or (r.status_code in (301, 302)
                            and "/procurement/guide" not in (r.headers.get("Location") or "")))
ok("... and is not shown the guide", 'id="g-req"' not in r.get_data(as_text=True))
r = get(None)
ok("an anonymous visitor is redirected to sign in (got %d)" % r.status_code,
   r.status_code in (301, 302) and "login" in (r.headers.get("Location") or ""))

print("\n%d checks, %d passed, %d failed" % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
sys.exit(0 if all(PASS) else 1)
