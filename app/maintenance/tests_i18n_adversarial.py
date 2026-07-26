"""
Maintenance Workflow & Governance — ADVERSARIAL trilingual audit.

Run:  python app/maintenance/tests_i18n_adversarial.py

Checks the owner's complaint the way he would: it renders /maintenance/workflow in
each language, pulls EVERY visible text node out of the HTML, and classifies it.

  1. node audit   — for a node inside data-i18n the key MUST exist in
                    app/static/i18n/<lang>.json (a missing key renders as the raw
                    key, e.g. "mnt.status.draft", which is worse than English);
                    for a server-rendered node in ar/tr, text that is IDENTICAL to
                    the English rendering of the same node is an English leak
                    unless it is an acronym, a number or a code identifier.
  2. fallback     — NULL one row's _ar: the Arabic reader gets the English, never
                    a blank panel.
  3. no data loss — POST one language through the real route; the other two rows
                    are untouched in the database.
  4. idempotency  — create_and_seed(conn) x3: no duplicate rows, and an
                    admin-edited translation survives.
  5. meaning      — the money/stock control paragraphs still state the SAME rule in
                    Arabic and Turkish (available = on hand - reserved, atomic
                    reservation, weighted average, roll up exactly once, ...).
  6. RTL          — lang/dir are set for Arabic and no inline style forces LTR on
                    the prose (the editor's own EN/TR textareas may, by design).
"""
import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(r"D:\TC platform\tc-platform-render")
TMP = Path(tempfile.mkdtemp(prefix="wf_adv_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                    # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                       # noqa: E402
from app.db import get_db                                        # noqa: E402
from app.maintenance import workflow as wf                        # noqa: E402
from app.maintenance.schema import create_and_seed                # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

app = create_app()
PASS, FAIL = [], []
LEAKS = []
ADMIN = {"id": 1, "username": "admin", "role": "super_admin", "full_name": "Admin"}
DICT = {lg: json.loads((REPO / ("app/static/i18n/%s.json" % lg)).read_text(encoding="utf-8"))
        for lg in ("en", "ar", "tr")}

# Things that are NOT an English leak when an Arabic or Turkish reader sees them.
ACRONYMS = {"PR", "PO", "AQL", "SLA", "FIFO", "EGP", "CSV", "OEE", "DHU", "RFQ",
            "SoD", "EN", "AR", "TR", "ID", "PM", "AI", "QR", "KPI"}
# a code identifier: services._pick_matrix_levels, sla_breach, ai.py, SLA_FACTOR
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*$")
LATIN4 = re.compile(r"[A-Za-z]{4,}")
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


# --- every visible text node, with the i18n mechanism that owns it -------------
SKIP_TAGS = {"script", "style", "svg", "head", "title"}
VOID = {"br", "hr", "img", "input", "meta", "link", "use", "path", "source"}


class Nodes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.out, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        if tag not in VOID:
            self.stack.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip or not data.strip():
            return
        key, loc, attrs = None, None, {}
        for tag, a in reversed(self.stack):
            if "data-i18n" in a:
                key = a["data-i18n"]
                break
            if "data-loc-en" in a:
                loc = a
                break
        path = "/".join(t for t, _ in self.stack)
        self.out.append({"text": data.strip(), "key": key, "loc": loc, "path": path,
                         "attrs": attrs})


def nodes(html):
    p = Nodes()
    p.feed(html)
    # the page body only: base.html's shell (sidebar, bell) is another module's file
    return [n for n in p.out if "/main" in n["path"]]


def is_leak(text):
    """Does this text contain an English WORD (not an acronym, a number or a code
    identifier)? Only ever applied to a node that renders identically in English
    and in the reader's language -- Turkish prose is Latin too."""
    if not LATIN4.search(text):
        return False
    for tok in re.split(r"[\s,;:()\[\]/=+×÷|]+", text):
        tok = tok.strip(".—-'\"")
        if not tok or not LATIN4.search(tok):
            continue
        if tok in ACRONYMS or tok.upper() in ACRONYMS:
            continue
        if ("_" in tok or "." in tok) and IDENT.match(tok):
            continue                       # services._pick_matrix_levels, ai.py
        return True                        # a real English word survived
    return False


def get_page(cl, lang):
    set_lang(lang)
    r = cl.get("/maintenance/workflow")
    return r.status_code, r.get_data(as_text=True)


# =====================================================================
print("\n(0) THE DETECTOR ITSELF — it must catch the leaks it is looking for")
# =====================================================================
ck("catches the matrix rule name the previous pass left in English",
   is_leak("Critical/expensive spare issue") and is_leak("Default spare issue"))
ck("catches a role label left in English", is_leak("Chief Financial Officer"))
RAWKEY = re.compile(r">\s*(?:mnt|mwf|role)\.[a-z_.]+")
ck("a raw i18n key is caught by the dictionary check, not by is_leak",
   is_leak("mnt.status.draft") is False
   and RAWKEY.search('<span>mnt.status.draft</span>') is not None)
ck("does NOT flag a code identifier",
   not any(is_leak(s) for s in ("services._pick_matrix_levels", "procure_bridge._enabled",
                                "workflow.SLA_FACTOR", "services.create_ticket, ai.py",
                                "mnt_approval_matrix.cost_threshold | 100")))
ck("does NOT flag an acronym or a number",
   not any(is_leak(s) for s in ("SLA", "PR", "4", "0.25", "100", "2026-07-26")))
ck("does NOT flag Arabic", not is_leak("مسودة") and not is_leak("حجز المخزون"))

# =====================================================================
print("\n(1) NODE AUDIT — every visible text node in en / ar / tr")
# =====================================================================
pages, ns = {}, {}
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    for lang in ("en", "ar", "tr"):
        code, html = get_page(cl, lang)
        ck("[%s] GET /maintenance/workflow == 200 (raw)" % lang, code == 200, code)
        pages[lang], ns[lang] = html, nodes(html)

ck("the three renderings have the same node count",
   len(ns["en"]) == len(ns["ar"]) == len(ns["tr"]),
   {lg: len(v) for lg, v in ns.items()})

for lang in ("en", "ar", "tr"):
    missing = sorted({n["key"] for n in ns[lang]
                      if n["key"] and n["key"] not in DICT[lang]})
    for k in missing:
        LEAKS.append("[%s] missing i18n key renders as the raw key: %s" % (lang, k))
    ck("[%s] every data-i18n key exists in %s.json (%d keyed nodes)"
       % (lang, lang, sum(1 for n in ns[lang] if n["key"])), not missing, missing)

for lang in ("ar", "tr"):
    bad = []
    for en_n, n in zip(ns["en"], ns[lang]):
        if n["key"]:
            continue                                  # swapped client-side, checked above
        if n["path"].endswith("textarea"):
            continue                                  # the admin editor's source fields
        if n["loc"] is not None:
            if ("data-loc-" + lang) not in n["loc"]:
                bad.append("no data-loc-%s: %r" % (lang, n["text"][:60]))
            continue
        if n["text"] == en_n["text"] and is_leak(n["text"]):
            bad.append(repr(n["text"][:110]))
    for b in bad:
        LEAKS.append("[%s] server-rendered English: %s" % (lang, b))
    ck("[%s] no server-rendered node still reads as English" % lang, not bad, bad)

# the statuses ride the platform's existing mx.<value> dictionary
missing_mx = [s for s in wf.TICKET_STATUSES
              if any(("mx." + s) not in DICT[lg] for lg in ("en", "ar", "tr"))]
ck("all 17 lifecycle statuses have a translated mx.<status> entry",
   not missing_mx, missing_mx)

# the labels the platform dictionary has NO key for ship their own three languages
for kind, m in wf.LABEL_I18N.items():
    for key in m:
        L = wf.label(kind, key, "X")
        ck("label %s/%s has all three languages" % (kind, key),
           bool(L["en"] and L["ar"] and L["tr"]) and L["ar"] != L["en"], L)
ck("an unknown label key falls back to its literal, not a blank",
   wf.label("role", "warehouse_manager", "Warehouse Manager")
   == {"en": "Warehouse Manager", "ar": "Warehouse Manager", "tr": "Warehouse Manager"})
ck("an unknown label with no English at all is humanised, not blank",
   wf.label("perm", "maint_custom")["ar"] == "Maint Custom")

# =====================================================================
print("\n(2) FALLBACK — a NULL translation shows English, never a blank panel")
# =====================================================================
with app.app_context():
    c = db()
    try:
        keep = wf.text_rows(c, "doc")["reservation"]["ar"]
        c.execute("UPDATE mnt_doc SET body_ar=NULL WHERE section='reservation'")
        c.commit()
        ck("the Arabic column really is NULL",
           wf.text_rows(c, "doc")["reservation"]["ar"] is None)
        body = wf.page_data(c, "ar")["docs"]["reservation"]["body"]
        ck("[ar] the English body is shown instead", body == wf.DOC_TEXT["reservation"])
        ck("[ar] the panel is NOT empty", bool(body.strip()))
        # blank (not NULL) must behave the same
        c.execute("UPDATE mnt_doc SET body_ar='   ' WHERE section='reservation'")
        c.commit()
        ck("[ar] a whitespace-only translation also falls back",
           wf.page_data(c, "ar")["docs"]["reservation"]["body"] == wf.DOC_TEXT["reservation"])
        # and an admin who blanks the English still gets the shipped default
        c.execute("UPDATE mnt_doc SET body='' WHERE section='reservation'")
        c.commit()
        ck("[ar] blank Arabic + blank English still shows the shipped default",
           wf.page_data(c, "ar")["docs"]["reservation"]["body"] == wf.DOC_TEXT["reservation"])
        c.execute("UPDATE mnt_doc SET body=?, body_ar=? WHERE section='reservation'",
                  (wf.DOC_TEXT["reservation"], keep))
        c.commit()
    finally:
        c.close()

with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    with app.app_context():
        c = db()
        try:
            c.execute("UPDATE mnt_status_meta SET explanation_ar=NULL WHERE status='closed'")
            c.commit()
        finally:
            c.close()
    code, html = get_page(cl, "ar")
    shown = re.sub(r"<details.*?</details>", "", html, flags=re.S)
    ck("[ar] page still 200 with a NULL translation", code == 200, code)
    ck("[ar] the English 'closed' explanation is on the page",
       "Downtime is measured from creation to" in shown)
    ck("[ar] the rest of the page is still Arabic", bool(ARABIC.search(shown)))
    with app.app_context():
        c = db()
        try:
            c.execute("UPDATE mnt_status_meta SET explanation_ar=? WHERE status='closed'",
                      (wf.STATUS_TEXT_AR["closed"],))
            c.commit()
        finally:
            c.close()

# =====================================================================
print("\n(3) NO DATA LOSS — editing one language leaves the other two alone")
# =====================================================================
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    cl.get("/maintenance/workflow")                 # mints the CSRF token
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    ck("the form's CSRF token exists", bool(tok))
    with app.app_context():
        c = db()
        try:
            before = dict(wf.text_rows(c, "role")["factory_manager"])
        finally:
            c.close()
    r = cl.post("/maintenance/workflow/text",
                data={"_csrf": tok, "kind": "role", "key": "factory_manager",
                      "explanation_ar": "يوقّع الدرجة الإضافية — تعديل المسؤول."})
    ck("POST one language == 302 (raw)", r.status_code == 302, r.status_code)
    with app.app_context():
        c = db()
        try:
            after = dict(wf.text_rows(c, "role")["factory_manager"])
            ck("the Arabic was stored",
               after["ar"] == "يوقّع الدرجة الإضافية — تعديل المسؤول.", after["ar"])
            ck("the English row is byte-identical", after["en"] == before["en"])
            ck("the Turkish row is byte-identical", after["tr"] == before["tr"])
        finally:
            c.close()

# =====================================================================
print("\n(4) IDEMPOTENCY — create_and_seed x3")
# =====================================================================
with app.app_context():
    c = db()
    try:
        tables = ("mnt_stage_meta", "mnt_role_meta", "mnt_status_meta", "mnt_doc",
                  "mnt_approval_matrix")
        before = {t: c.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"]
                  for t in tables}
        for _ in range(3):
            create_and_seed(c)
        after = {t: c.execute("SELECT COUNT(*) n FROM %s" % t).fetchone()["n"]
                 for t in tables}
        ck("no duplicate rows after 3 re-seeds", before == after, (before, after))
        row = wf.text_rows(c, "role")["factory_manager"]
        ck("the admin's Arabic edit survived 3 re-seeds",
           row["ar"] == "يوقّع الدرجة الإضافية — تعديل المسؤول.", row["ar"])
        ck("the shipped English was not overwritten either",
           row["en"] == wf.ROLE_TEXT["factory_manager"])
        ck("the ladder still resolves to the same signers",
           [l["role"] for l in wf.page_data(c, "ar")["ladder"]]
           == ["maintenance_manager", "storekeeper", "maintenance_manager",
               "factory_manager", "storekeeper"])
        ck("the threshold and the knobs are unchanged by the language work",
           (wf.critical_threshold(c), wf.sla_factor(c, "critical"),
            wf.dup_guard_on(c), wf.flag(c, "auto_reorder_pr")) == (100.0, 0.25, True, True))
    finally:
        c.close()

# =====================================================================
print("\n(5) MEANING — the money / stock controls say the same thing")
# =====================================================================
# Each entry: the rule the English states, and a fact that MUST be in the
# translation for that rule to still be stated.
MEANING = [
    ("doc/reservation ar: available = on hand MINUS reserved",
     wf.DOC_TEXT_AR["reservation"], ["المتاح", "ناقص المحجوز"]),
    ("doc/reservation ar: the reservation is atomic / per-line conditional",
     wf.DOC_TEXT_AR["reservation"], ["ذرّي", "شرطي"]),
    ("doc/reservation ar: a failed line rolls the whole batch back",
     wf.DOC_TEXT_AR["reservation"], ["الدفعة بالكامل"]),
    ("doc/reservation tr: available = on hand MINUS reserved",
     wf.DOC_TEXT_TR["reservation"], ["müsait = eldeki stok eksi rezerve"]),
    ("doc/reservation tr: atomic, one conditional UPDATE per line",
     wf.DOC_TEXT_TR["reservation"], ["atomik", "koşullu UPDATE"]),
    ("doc/reservation tr: the whole batch is rolled back",
     wf.DOC_TEXT_TR["reservation"], ["tüm", "geri alınır"]),
    ("doc/costing ar: weighted average, both quantities and the price",
     wf.DOC_TEXT_AR["costing"], ["المتوسط المرجّح", "الكمية المستلمة", "السعر"]),
    ("doc/costing ar: the roll-up into the machine happens exactly ONCE",
     wf.DOC_TEXT_AR["costing"], ["مرة واحدة"]),
    ("doc/costing tr: weighted average with the same formula",
     wf.DOC_TEXT_TR["costing"], ["ağırlıklı ortalama", "gelen miktar", "fiyat"]),
    ("doc/costing tr: exactly once",
     wf.DOC_TEXT_TR["costing"], ["bir kez"]),
    ("doc/auto_reorder ar: UNPRICED PR at or below the reorder level, deduplicated",
     wf.DOC_TEXT_AR["auto_reorder"], ["بدون", "سعر", "حد إعادة الطلب أو أقل", "التكرار"]),
    ("doc/auto_reorder tr: unpriced PR at/below the reorder level, deduplicated",
     wf.DOC_TEXT_TR["auto_reorder"], ["fiyatsız", "veya altına", "mükerrer"]),
    ("doc/sla ar: the clock starts at CREATION, not at assignment",
     wf.DOC_TEXT_AR["sla"], ["إنشاء", "وليس من لحظة الإسناد"]),
    ("doc/sla tr: starts at creation, NOT at assignment",
     wf.DOC_TEXT_TR["sla"], ["atama anında değil", "oluşturulduğunda başlar"]),
    ("status/waiting_stock ar: available = on hand minus reserved, nothing issued",
     wf.STATUS_TEXT_AR["waiting_stock"], ["الرصيد الفعلي ناقص المحجوز", "حتى يصل المخزون"]),
    ("status/waiting_stock tr: on hand minus reserved, nothing approved for issue",
     wf.STATUS_TEXT_TR["waiting_stock"], ["eldeki stok eksi rezerve", "onaylanamaz"]),
    ("status/closed ar: cost/downtime roll into the machine ONCE (machine_rolled)",
     wf.STATUS_TEXT_AR["closed"], ["مرة واحدة", "machine_rolled"]),
    ("status/closed tr: rolled into the machine ONLY ONCE (machine_rolled)",
     wf.STATUS_TEXT_TR["closed"], ["BİR KEZ", "machine_rolled"]),
    ("status/reopened ar: the roll-up is NOT repeated",
     wf.STATUS_TEXT_AR["reopened"], ["لا يُعاد ترحيل"]),
    ("status/reopened tr: the roll-up is NOT repeated",
     wf.STATUS_TEXT_TR["reopened"], ["TEKRARLANMAZ"]),
    ("stage/crit_l2 ar: approved only once EVERY rung signed",
     wf.STAGE_TEXT_AR["crit_l2"], ["كل الدرجات"]),
    ("stage/crit_l2 tr: approved only when ALL rungs signed",
     wf.STAGE_TEXT_TR["crit_l2"], ["TÜM basamaklar"]),
    ("role/storekeeper ar: the ISSUER, deliberately not an approval rung",
     wf.ROLE_TEXT_AR["storekeeper"], ["الصارف", "وليس درجة موافقة"]),
    ("role/storekeeper tr: the ISSUER, deliberately not an approval rung",
     wf.ROLE_TEXT_TR["storekeeper"], ["ÇIKIŞI YAPAN", "onay basamağı değildir"]),
]
for label_, text, musts in MEANING:
    miss = [m for m in musts if m not in text]
    ck(label_, not miss, miss)

# the column / setting names an admin has to match are kept verbatim
for name, text in (("reviewed_at", wf.STATUS_TEXT_AR["under_review"]),
                   ("machine_rolled", wf.STATUS_TEXT_TR["closed"]),
                   ("auto_reorder_pr", wf.DOC_TEXT_AR["auto_reorder"]),
                   ("sla_breach", wf.DOC_TEXT_TR["sla"])):
    ck("the DB/setting name %s is kept verbatim in the translation" % name, name in text)

# =====================================================================
print("\n(6) RTL — Arabic must not break the layout")
# =====================================================================
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    code, html = get_page(cl, "ar")
    ck('[ar] <html lang="ar"> is set', '<html lang="ar"' in html, code)
    # base.html ships lang server-side and app.js flips documentElement.dir to rtl
    # from TC_CFG.lang on boot (RTL = {"ar"}); this module must not fight that.
    ck('[ar] TC_CFG.lang is "ar" so app.js switches the document to RTL',
       'lang: "ar"' in html)
    body = html.split("<main", 1)[-1]
    prose = re.sub(r"<details.*?</details>", "", body, flags=re.S)
    ck("[ar] no inline direction/ltr forced on the prose",
       "direction:ltr" not in prose.replace(" ", "") and 'dir="ltr"' not in prose,
       [m for m in re.findall(r'dir="ltr"|direction: *ltr', prose)])
    ck("[ar] the editor's own EN/TR textareas keep dir=ltr (correct)",
       body.count('dir="ltr"') >= 2 and 'dir="rtl"' in body)
    ck("[ar] no raw i18n key of the mnt./mwf./role. families is in the text",
       not re.search(r">\s*(?:mnt|mwf|role)\.[a-z_]+", html),
       re.findall(r">\s*(?:mnt|mwf|role)\.[a-z_.]+", html)[:5])
    code_tr, html_tr = get_page(cl, "tr")
    ck('[tr] <html lang="tr"> and TC_CFG.lang "tr" (stays LTR)',
       '<html lang="tr"' in html_tr and 'lang: "tr"' in html_tr)

# =====================================================================
print("\n(7) THE LANGUAGE BUTTON — base.html's EN/ع/TR switch does NOT reload,"
      "\n    so every translated node must carry its other languages with it")
# =====================================================================
PROSE = re.compile(r'<span data-loc-en="([^"]*)" data-loc-ar="([^"]*)" '
                   r'data-loc-tr="([^"]*)">(.*?)</span>', re.S)
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    code, html_en = get_page(cl, "en")
    trios = PROSE.findall(html_en)
    ck("the English page carries every label/paragraph in 3 languages",
       len(trios) >= 60, len(trios))
    ck("no trio has a blank language", all(e.strip() and a.strip() and t.strip()
                                          for e, a, t, _ in trios))
    # every DB-stored paragraph page_data resolved must be on the page in all 3
    with app.app_context():
        c = db()
        try:
            d = wf.page_data(c, "en")
        finally:
            c.close()
    expected = ([s["langs"] for s in d["statuses"]] + [r["langs"] for r in d["roles"]]
                + [l["langs"] for l in d["ladder"]]
                + [d["docs"][s]["langs"] for s in wf.DOC_SECTIONS])
    from markupsafe import escape
    absent = [p["ar"][:40] for p in expected
              if ('data-loc-ar="%s"' % escape(p["ar"])) not in html_en]
    ck("all %d DB paragraphs ship Arabic in the markup" % len(expected),
       not absent and len(expected) == 37, (len(expected), absent[:3]))
    long_ones = [(e, a, t) for e, a, t, _ in trios if len(e) > 80]
    ck("the long PARAGRAPHS (not just the labels) ship Arabic and Turkish",
       len(long_ones) >= 25, len(long_ones))
    ck("pressing ع on the English page yields Arabic script for every paragraph",
       all(ARABIC.search(a) for _, a, _ in long_ones),
       [a[:40] for _, a, _ in long_ones if not ARABIC.search(a)][:3])
    ck("pressing TR yields text that differs from the English for every paragraph",
       all(t != e for e, _, t in long_ones))
    ck("the visible span rendered for an English reader IS the English",
       all(unescaped.strip() == e.replace("&#34;", '"').replace("&amp;", "&").strip()
           for e, _, _, unescaped in
           [(e, a, t, v) for e, a, t, v in trios if "<" not in v][:20]))
    ck("no admin editor sits INSIDE a swappable span (it would be wiped)",
       not any("<details" in v for _, _, _, v in trios))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    for f in FAIL:
        print("  FAILED: " + f)
print("english_leaks: %d" % len(LEAKS))
for l in LEAKS:
    print("  LEAK " + l)
sys.exit(1 if FAIL else 0)
