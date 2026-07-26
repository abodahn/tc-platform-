"""
Maintenance Workflow & Governance — trilingual proof.

Run:  python app/maintenance/tests_i18n.py

Proves, by execution, that /maintenance/workflow is readable in English, Arabic and
Turkish:
  a) the per-language columns exist on all four prose tables,
  b) every seeded English row now has an Arabic and a Turkish row,
  c) for each of en/ar/tr the page renders 200 and the DB-driven prose comes back in
     THAT script — for 'ar' the English source sentence is gone from the displayed
     text, for 'tr' the Turkish text is there and differs from the English,
  d) no explanation panel is ever empty, and a row with a NULL translation falls
     back to the English text instead of blanking,
  e) an admin edit to ONE language leaves the other two intact,
  f) create_and_seed(conn) x3 duplicates nothing and never overwrites edited text.

The admin editor also holds the English text (in a <textarea>), so the assertions
run against the DISPLAYED prose: the page with every <details> editor removed.
"""
import os
import re
import sys
import tempfile
from html import unescape
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wf_i18n_"))
os.chdir(TMP)
sys.path.insert(0, r"D:\TC platform\tc-platform-render")
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                      # noqa: E402
from app.db import get_db                                       # noqa: E402
from app.maintenance import workflow as wf                       # noqa: E402
from app.maintenance.schema import create_and_seed               # noqa: E402

app = create_app()
PASS, FAIL = [], []
ADMIN = {"id": 1, "username": "admin", "role": "super_admin", "full_name": "Admin"}
ARABIC = re.compile(r"[\u0600-\u06FF]")


def ck(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label
          + ((" -> " + str(extra)) if extra and not cond else ""))


def db():
    return get_db()


def set_lang(lang):
    c = db()
    try:
        c.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
        c.commit()
    finally:
        c.close()


def displayed(html):
    """The page minus the admin <details> editors (which deliberately still contain
    the English text for editing). Markup kept: used for the structural checks."""
    return unescape(re.sub(r"<details.*?</details>", "", html, flags=re.S))


def visible(html):
    """Only the text a reader actually SEES: editors removed and every tag stripped,
    so the English kept in data-loc-en (the fallback app.js swaps from when the
    language button is pressed) is not mistaken for English on screen."""
    return unescape(re.sub(r"<[^>]+>", " ",
                           re.sub(r"<details.*?</details>", "", html, flags=re.S)))


def get_page(cl):
    r = cl.get("/maintenance/workflow")
    return r.status_code, r.get_data(as_text=True)


# =====================================================================
print("\n(a) MIGRATION — the per-language columns exist")
# =====================================================================
with app.app_context():
    c = db()
    try:
        for table, col in (("mnt_status_meta", "explanation"), ("mnt_role_meta", "explanation"),
                           ("mnt_stage_meta", "explanation"), ("mnt_doc", "body")):
            row = c.execute("SELECT * FROM %s LIMIT 1" % table).fetchone()
            cols = set(row.keys())
            ck("%s has %s / _ar / _tr" % (table, col),
               {col, col + "_ar", col + "_tr"} <= cols, sorted(cols))
    finally:
        c.close()

# =====================================================================
print("\n(b) SEED — every English row has an Arabic and a Turkish row")
# =====================================================================
with app.app_context():
    c = db()
    try:
        total = 0
        for kind in ("status", "role", "stage", "doc"):
            rows = wf.text_rows(c, kind)
            seeded = wf._TEXT_TABLES[kind][3]
            miss_ar = [k for k in seeded if not (rows.get(k, {}).get("ar") or "").strip()]
            miss_tr = [k for k in seeded if not (rows.get(k, {}).get("tr") or "").strip()]
            ck("%s: %d rows, all have Arabic" % (kind, len(seeded)), not miss_ar, miss_ar)
            ck("%s: all have Turkish" % kind, not miss_tr, miss_tr)
            ck("%s: Arabic really is Arabic script" % kind,
               all(ARABIC.search(rows[k]["ar"] or "") for k in seeded))
            ck("%s: Turkish differs from English" % kind,
               all((rows[k]["tr"] or "") != (rows[k]["en"] or "") for k in seeded))
            total += len(seeded)
        ck("35 prose rows translated x2 languages == 70 translations", total == 35, total)
    finally:
        c.close()

# =====================================================================
print("\n(c) THE PAGE — 200 and the right script for each reader")
# =====================================================================
# One English sentence per table, used as the "is the English still showing?" probe.
PROBE = {
    "status": ("closed", wf.STATUS_TEXT["closed"], wf.STATUS_TEXT_AR["closed"],
               wf.STATUS_TEXT_TR["closed"]),
    "role": ("storekeeper", wf.ROLE_TEXT["storekeeper"], wf.ROLE_TEXT_AR["storekeeper"],
             wf.ROLE_TEXT_TR["storekeeper"]),
    "stage": ("crit_l2", wf.STAGE_TEXT["crit_l2"], wf.STAGE_TEXT_AR["crit_l2"],
              wf.STAGE_TEXT_TR["crit_l2"]),
    "doc": ("reservation", wf.DOC_TEXT["reservation"], wf.DOC_TEXT_AR["reservation"],
            wf.DOC_TEXT_TR["reservation"]),
}
# every prose block ships all three languages; none of them may be blank, and the
# span the reader actually sees may not be empty either
BLANK_LANG = re.compile(r'data-loc-(?:en|ar|tr)="\s*"')
BLANK_PROSE = re.compile(r'<span [^>]*data-loc-en="[^"]+"[^>]*>\s*</span>')

with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    for lang in ("en", "ar", "tr"):
        set_lang(lang)
        code, html = get_page(cl)
        ck("[%s] GET /maintenance/workflow == 200" % lang, code == 200, code)
        if code != 200:
            continue
        shown, vis = displayed(html), visible(html)
        ck("[%s] <html lang> is %s" % (lang, lang), ('<html lang="%s"' % lang) in html)
        for kind, (key, en, ar, tr) in PROBE.items():
            if lang == "en":
                ck("[en] %s prose is the English text" % kind, en in vis)
            elif lang == "ar":
                ck("[ar] %s prose is the Arabic text" % kind, ar in vis)
                ck("[ar] %s English source sentence is ABSENT" % kind, en not in vis)
            else:
                ck("[tr] %s prose is the Turkish text" % kind, tr in vis)
                ck("[tr] Turkish %s text differs from the English" % kind, tr != en)
                ck("[tr] %s English source sentence is ABSENT" % kind, en not in vis)
        if lang == "ar":
            ck("[ar] Arabic script present in the page", bool(ARABIC.search(vis)))
        ck("[%s] no language of any prose block resolved to blank" % lang,
           not BLANK_LANG.search(shown), BLANK_LANG.findall(shown)[:3])
        ck("[%s] no prose block renders empty on screen" % lang,
           not BLANK_PROSE.search(shown), BLANK_PROSE.findall(shown)[:3])
        # The labels that used to be raw Python constants are now translated. A
        # data-i18n key the dictionary does not define renders AS THE KEY, so each
        # label rides a mechanism that actually resolves: the statuses use the
        # platform's existing mx.<status> entries, and the labels with no
        # dictionary key at all ship their own three languages in data-loc-*.
        ck("[%s] role labels carry all three languages" % lang,
           ('data-loc-ar="%s"' % wf.LABEL_I18N["role"]["maintenance_manager"][1]) in html
           and ('data-loc-tr="%s"' % wf.LABEL_I18N["role"]["factory_manager"][2]) in html)
        ck("[%s] every lifecycle status uses the translated mx.<status> entry" % lang,
           all(('data-i18n="mx.%s"' % s) in html for s in
               ("draft", "waiting_stock", "approved_issue", "reopened")))
        ck("[%s] the rungs carry all three languages" % lang,
           all(('data-loc-ar="%s"' % wf.LABEL_I18N["stage"][s][1]) in html
               for s in wf.STAGE_LABELS))
        ck("[%s] the rule and knob names carry all three languages" % lang,
           all(('data-loc-ar="%s"' % (wf.LABEL_I18N["rule"].get(s)
                                      or wf.LABEL_I18N["setting"][s])[1]) in html
               for s in ("lifecycle", "sla", "costing", "response_sla_hours",
                         "duplicate_ticket_guard")))
        ck("[%s] no raw i18n key leaked into the markup" % lang,
           not re.search(r">\s*(?:mnt|mwf|role|mx)\.[a-z_.]+", html),
           re.findall(r">\s*(?:mnt|mwf|role|mx)\.[a-z_.]+", html)[:3])

    # ----- a NULL translation must fall back to English, never blank -----
    with app.app_context():
        c = db()
        try:
            c.execute("UPDATE mnt_status_meta SET explanation_ar=NULL WHERE status='closed'")
            c.execute("UPDATE mnt_doc SET body_ar='' WHERE section='costing'")
            c.commit()
        finally:
            c.close()
    set_lang("ar")
    code, html = get_page(cl)
    shown, vis = displayed(html), visible(html)
    ck("[ar] NULL Arabic status falls back to the English text",
       code == 200 and wf.STATUS_TEXT["closed"] in vis, code)
    ck("[ar] BLANK Arabic rules body falls back to the English text",
       wf.DOC_TEXT["costing"] in vis)
    ck("[ar] the fallback panel is not empty", not BLANK_PROSE.search(shown))
    ck("[ar] the other rows are still Arabic", wf.STATUS_TEXT_AR["draft"] in vis)
    with app.app_context():
        c = db()
        try:
            ck("pick() returns English for a NULL translation",
               wf.pick("status", "closed", wf.text_rows(c, "status")["closed"], "ar")
               == wf.STATUS_TEXT["closed"])
            ck("pick() returns the shipped default when the row is empty too",
               wf.pick("doc", "sla", {"en": "", "ar": None, "tr": None}, "ar")
               == wf.DOC_TEXT["sla"])
        finally:
            c.close()

    # =====================================================================
    print("\n(e) EDITOR — one language at a time, the others untouched")
    # =====================================================================
    with app.app_context():
        c = db()
        try:
            before = wf.text_rows(c, "doc")["sla"]
            wf.set_text(c, "doc", "sla", None, ADMIN, ar="نص المسؤول عن SLA.")
            after = wf.text_rows(c, "doc")["sla"]
            ck("saving Arabic only changed Arabic", after["ar"] == "نص المسؤول عن SLA.")
            ck("saving Arabic left English intact", after["en"] == before["en"])
            ck("saving Arabic left Turkish intact", after["tr"] == before["tr"])
            wf.set_text(c, "doc", "sla", None, ADMIN, tr="Yonetici SLA notu.")
            after = wf.text_rows(c, "doc")["sla"]
            ck("saving Turkish only changed Turkish", after["tr"] == "Yonetici SLA notu.")
            ck("saving Turkish left the admin's Arabic intact",
               after["ar"] == "نص المسؤول عن SLA.")
            ck("saving Turkish left English intact", after["en"] == before["en"])
        finally:
            c.close()
    # the real form: three textareas in one POST
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = cl.post("/maintenance/workflow/text",
                data={"_csrf": tok, "kind": "status", "key": "repair",
                      "explanation": "EN repair note.", "explanation_ar": "ملاحظة الإصلاح.",
                      "explanation_tr": "Onarim notu."})
    ck("POST the 3-language editor == 302", r.status_code == 302, r.status_code)
    with app.app_context():
        c = db()
        try:
            row = wf.text_rows(c, "status")["repair"]
            ck("all three languages were stored",
               (row["en"], row["ar"], row["tr"])
               == ("EN repair note.", "ملاحظة الإصلاح.", "Onarim notu."), row)
            ck("the audit trail recorded which languages were written",
               (c.execute("SELECT comment FROM mnt_audit WHERE action='wf_text_set' "
                          "ORDER BY id DESC LIMIT 1").fetchone()["comment"] or "")
               == "repair [en+ar+tr]")
        finally:
            c.close()
    set_lang("ar")
    code, html = get_page(cl)
    ck("[ar] the admin's Arabic edit is what an Arabic reader sees",
       "ملاحظة الإصلاح." in visible(html))
    set_lang("tr")
    code, html = get_page(cl)
    ck("[tr] the admin's Turkish edit is what a Turkish reader sees",
       "Onarim notu." in visible(html))

# =====================================================================
print("\n(f) IDEMPOTENCY — create_and_seed x3 keeps every language")
# =====================================================================
with app.app_context():
    c = db()
    try:
        tables = ("mnt_stage_meta", "mnt_role_meta", "mnt_status_meta", "mnt_doc")
        before = {t: c.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"] for t in tables}
        for _ in range(3):
            create_and_seed(c)
        after = {t: c.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"] for t in tables}
        ck("no duplicate rows after 3 re-seeds", before == after, (before, after))
        row = wf.text_rows(c, "status")["repair"]
        ck("the admin's English survived re-seeding", row["en"] == "EN repair note.")
        ck("the admin's Arabic survived re-seeding", row["ar"] == "ملاحظة الإصلاح.")
        ck("the admin's Turkish survived re-seeding", row["tr"] == "Onarim notu.")
        sla = wf.text_rows(c, "doc")["sla"]
        ck("the admin's Arabic rules note survived", sla["ar"] == "نص المسؤول عن SLA.")
        ck("the NULLed Arabic translation was re-filled by the seed",
           wf.text_rows(c, "status")["closed"]["ar"] == wf.STATUS_TEXT_AR["closed"])
        # reset restores all three languages
        wf.reset_text(c, "status", "repair", ADMIN)
        row = wf.text_rows(c, "status")["repair"]
        ck("reset restored the shipped English", row["en"] == wf.STATUS_TEXT["repair"])
        ck("reset restored the shipped Arabic", row["ar"] == wf.STATUS_TEXT_AR["repair"])
        ck("reset restored the shipped Turkish", row["tr"] == wf.STATUS_TEXT_TR["repair"])
    finally:
        c.close()

# =====================================================================
print("\n(g) NO BEHAVIOUR CHANGE — the numbers and the ladder are untouched")
# =====================================================================
with app.app_context():
    c = db()
    try:
        ck("critical threshold still 100", wf.critical_threshold(c) == 100.0)
        ck("SLA factors unchanged",
           (wf.sla_factor(c, "critical"), wf.sla_factor(c, "medium")) == (0.25, 1.0))
        ck("dup guard + auto-reorder still ON",
           wf.dup_guard_on(c) is True and wf.flag(c, "auto_reorder_pr") is True)
        en = wf.page_data(c, "en")
        ar = wf.page_data(c, "ar")
        ck("the ladder is identical in every language",
           [l["role"] for l in en["ladder"]] == [l["role"] for l in ar["ladder"]]
           == ["maintenance_manager", "storekeeper", "maintenance_manager",
               "factory_manager", "storekeeper"],
           [l["role"] for l in en["ladder"]])
        ck("the lifecycle is identical in every language",
           [s["next"] for s in en["statuses"]] == [s["next"] for s in ar["statuses"]])
        ck("an unknown lang_pref falls back to English",
           wf.page_data(c, "de")["docs"]["sla"]["body"] == en["docs"]["sla"]["body"])
        ck("no prose is empty in any language",
           all(x["explanation"].strip() for lg in ("en", "ar", "tr")
               for x in wf.page_data(c, lg)["statuses"] + wf.page_data(c, lg)["roles"]
               + wf.page_data(c, lg)["ladder"]))
        ck("no rules panel is empty in any language",
           all(wf.page_data(c, lg)["docs"][s]["body"].strip()
               for lg in ("en", "ar", "tr") for s in wf.DOC_SECTIONS))
    finally:
        c.close()

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
sys.exit(1 if FAIL else 0)
