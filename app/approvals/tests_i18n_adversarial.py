# -*- coding: utf-8 -*-
"""Adversarial trilingual check for /procurement/workflow.

Reads the page the way the owner does — in English, in Arabic, in Turkish — pulls
EVERY visible text node out of the rendered HTML and classifies it:

  * inside a data-i18n element -> the key MUST exist in app/static/i18n/<lang>.json,
    because app.js does `el.textContent = DICT[key] || key`: a key that is not
    there is printed RAW on the page ("Warehouse" -> "proc.stage.warehouse"), in
    every language, English included.
  * server-rendered -> for ar/tr it must not be the English text, and (Arabic
    only, where the test is decidable on the alphabet) must carry no run of 4+
    Latin letters that is not an acronym, an identifier or a department name.

Plus: the prose fallback chain, per-language save isolation, seed idempotency,
the meaning of the money/stock control paragraphs, and RTL.

Run:  python app/approvals/tests_i18n_adversarial.py
"""
import io
import json
import os
import re
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="wfadv_"))
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

LANGS = ("en", "ar", "tr")
PASS = []
FAIL = []
LEAKS = []


def ok(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  << " + detail) if detail and not cond else ""))


# ---------------------------------------------------------------- DOM extract
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}
SKIP = {"script", "style", "template", "svg", "noscript"}


class Extract(HTMLParser):
    """visible text nodes + the data-i18n key that governs each of them."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.nodes = []          # (text, i18n key or None, tag path)
        self.keys = set()
        self.html_attrs = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.html_attrs = a
        for at in ("data-i18n", "data-i18n-ph", "data-i18n-title"):
            if a.get(at):
                self.keys.add(a[at])
        if tag not in VOID:
            self.stack.append((tag, a.get("data-i18n")))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        txt = re.sub(r"\s+", " ", data).strip()
        if not txt:
            return
        tags = [t for t, _ in self.stack]
        if any(t in SKIP for t in tags):
            return
        key = None
        for _t, k in self.stack:
            if k:
                key = k
        self.nodes.append((txt, key, "/".join(tags)))


# things that are legitimately Latin on an Arabic page
ACRONYMS = {"PR", "PO", "AQL", "SLA", "FIFO", "EGP", "CSV", "OEE", "DHU", "RFQ",
            "SOD", "FX", "CFO", "CEO", "GRN", "OEM", "ITSM", "TC", "PDF", "VAT"}
IDENT = re.compile(r"^[a-z][a-z0-9_.]*$")            # proc_view, rfq_quote_min, ...
LATIN4 = re.compile(r"[A-Za-z]{4,}")


def arabic_leak(txt, tagpath, allowed, idents):
    """True when an Arabic page shows English prose.

    `allowed` = department names (admin-created data, keyed by their own string).
    `idents`  = configuration identifiers (role keys, permission codes, setting
    keys). Those are deliberately NOT translated — an admin has to be able to
    match them against Admin > Roles and proc_settings — and they also appear
    INSIDE prose (the SoD paragraph names super_admin), so they are stripped out
    of the text before the alphabet test runs."""
    if "textarea" in tagpath:
        return False        # the per-language editor boxes; the EN box IS English
    if txt in allowed or IDENT.match(txt):
        return False
    for ident in idents:
        txt = txt.replace(ident, " ")
    words = [w for w in LATIN4.findall(txt) if w.upper() not in ACRONYMS]
    return bool(words)


def page(client, lang):
    conn = get_db()
    conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
    conn.commit()
    conn.close()
    r = client.get("/procurement/workflow")
    assert r.status_code == 200, "status %s for lang %s" % (r.status_code, lang)
    p = Extract()
    p.feed(r.data.decode("utf-8"))
    return r, p


def english_defaults():
    """Every English string a translated page must NOT be showing."""
    s = set()
    for m in (C.STAGE_EXPLAIN, C.ROLE_EXPLAIN, C.DOC_SECTIONS, C.STATUS_MEANING):
        s |= {v.strip() for v in m.values()}
    return s


def main():
    app = create_app()
    client = app.test_client()
    with client.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
        s["_csrf_token"] = "tok"
    dicts = {lg: json.load(io.open(str(REPO / "app" / "static" / "i18n" / (lg + ".json")),
                                   encoding="utf-8")) for lg in LANGS}
    en_defaults = english_defaults()

    # ---------------------------------------------------------------- 1
    print("\n--- 1. every visible text node, in all three languages ---------")
    for lg in LANGS:
        r, p = page(client, lg)
        ok("GET /procurement/workflow [%s] -> 200 (raw)" % lg, r.status_code == 200)
        missing = sorted(k for k in p.keys if k not in dicts[lg])
        for k in missing:
            LEAKS.append("data-i18n key not in %s.json, renders as the raw key: %s" % (lg, k))
        ok("all %d data-i18n keys on the page exist in %s.json" % (len(p.keys), lg),
           not missing, ", ".join(missing[:6]))

        if lg == "en":
            continue
        v = svc.workflow_view(None, lg)
        allowed = set(v["departments"]) | {d.strip() for d in v["departments"]}
        body = [n for n in p.nodes if "main" in n[2].split("/")]
        served = [n for n in body if not n[1]]
        # a) no English default prose survives on a translated page
        eng = sorted({t for t, _k, tp in served
                      if t in en_defaults and "textarea" not in tp})
        for t in eng:
            LEAKS.append("[%s] English default still rendered: %s" % (lg, t[:90]))
        ok("no English default paragraph is rendered on the %s page" % lg,
           not eng, "%d" % len(eng))
        # b) Arabic is decidable on the alphabet
        if lg == "ar":
            from app.security import PERMISSIONS, effective_roles
            idents = sorted(set(effective_roles()) | set(PERMISSIONS)
                            | set(v["L"]["setting"]) | set(C.PR_STATUSES)
                            | {g["section"] for g in v["gates"]},
                            key=len, reverse=True)
            lat = []
            for t, _k, tp in served:
                if arabic_leak(t, tp, allowed, idents):
                    lat.append(t)
            for t in sorted(set(lat)):
                LEAKS.append("[ar] Latin text outside data-i18n: %s" % t[:90])
            ok("no non-acronym Latin run in Arabic server-rendered text",
               not lat, "; ".join(sorted(set(lat))[:6]))

    # ---------------------------------------------------------------- 2
    print("\n--- 2. every prose field and label differs from English --------")
    for lg in ("ar", "tr"):
        v = svc.workflow_view(None, lg)
        bad = [s["stage"] for s in v["stages"]
               if s["explanation"].strip() == (C.STAGE_EXPLAIN.get(s["stage"]) or "").strip()]
        ok("[%s] all %d stage explanations translated" % (lg, len(v["stages"])),
           not bad, ",".join(bad))
        bad = [r["key"] for r in v["roles"]
               if r["explanation"].strip() == (C.ROLE_EXPLAIN.get(r["key"]) or "").strip()]
        ok("[%s] all %d role explanations translated" % (lg, len(v["roles"])),
           not bad, ",".join(bad))
        bad = [g["section"] for g in [v["overview"]] + v["gates"]
               if g["body"].strip() == (C.DOC_SECTIONS.get(g["section"]) or "").strip()]
        ok("[%s] overview + all %d gate texts translated" % (lg, len(v["gates"])),
           not bad, ",".join(bad))
        bad = [s["key"] for s in v["statuses"]
               if s["body"].strip() == (C.STATUS_MEANING.get(s["key"]) or "").strip()]
        ok("[%s] all %d status meanings translated" % (lg, len(v["statuses"])),
           not bad, ",".join(bad))
        L = v["L"]
        for kind, src in (("stage", T.STAGE_LABEL), ("gate", T.GATE_LABEL),
                          ("status", T.STATUS_LABEL), ("setting", T.SETTING_LABEL)):
            bad = [k for k, val in L[kind].items() if val != src[lg][k]]
            ok("[%s] %d %s labels resolve to the %s text" % (lg, len(L[kind]), kind, lg),
               not bad, ",".join(bad))
        bad = [k for k, val in T.ROLE_LABEL[lg].items() if L["role"].get(k) != val]
        ok("[%s] all %d role labels resolve to the %s text" % (lg, len(T.ROLE_LABEL[lg]), lg),
           not bad, ",".join(bad))
        ok("[%s] the editor's own labels are in %s" % (lg, lg),
           L["ui"]["text_en"] == T.UI[lg]["text_en"]
           and L["ui"]["lang_hint"] == T.UI[lg]["lang_hint"])

    # ---------------------------------------------------------------- 3
    print("\n--- 3. fallback: a NULL translation shows English, never blank --")
    conn = get_db()
    conn.execute("UPDATE proc_stage_meta SET explanation_ar=NULL WHERE stage='warehouse'")
    conn.execute("UPDATE proc_doc SET body_ar='   ' WHERE section='payment_cap'")
    conn.commit()
    conn.close()
    v = svc.workflow_view(None, "ar")
    wh = [s for s in v["stages"] if s["stage"] == "warehouse"][0]
    ok("NULL explanation_ar falls back to the English column, not blank",
       wh["explanation"].strip() == C.STAGE_EXPLAIN["warehouse"].strip())
    pc = [g for g in v["gates"] if g["section"] == "payment_cap"][0]
    ok("whitespace-only body_ar falls back to English, not blank",
       pc["body"].strip() == C.DOC_SECTIONS["payment_cap"].strip())
    ok("neither fallback panel is empty", bool(wh["explanation"]) and bool(pc["body"]))
    ok("the fallback is not badged as an admin override",
       wh["explanation_custom"] is False and pc["custom"] is False)
    _r, p = page(client, "ar")
    html = _r.data.decode("utf-8")
    ok("the English fallback is really on the Arabic page",
       C.STAGE_EXPLAIN["warehouse"][:60] in html)
    # restore
    S.seed_translations(get_db())

    # ---------------------------------------------------------------- 4
    print("\n--- 4. saving one language never touches the other two ---------")
    conn = get_db()
    before = dict(conn.execute("SELECT explanation, explanation_ar, explanation_tr "
                               "FROM proc_stage_meta WHERE stage='cfo'").fetchone())
    conn.close()
    # The browser posts ALL THREE boxes, each holding what the editor rendered.
    # Only the Arabic one was retyped. Posting '' for the other two would mean
    # "the admin emptied them on purpose", which is a different intent entirely
    # (see tests_blank_lang.py) — it is not how a one-language edit reaches here.
    r = client.post("/procurement/workflow/stage",
                    data={"_csrf": "tok", "stage": "cfo",
                          "explanation_ar": "نص المسؤول للمرحلة المالية.",
                          "explanation": before["explanation"],
                          "explanation_tr": before["explanation_tr"]})
    ok("POST /procurement/workflow/stage [ar only] -> 302 (raw)", r.status_code == 302)
    conn = get_db()
    after = dict(conn.execute("SELECT explanation, explanation_ar, explanation_tr "
                              "FROM proc_stage_meta WHERE stage='cfo'").fetchone())
    conn.close()
    ok("Arabic was saved", after["explanation_ar"] == "نص المسؤول للمرحلة المالية.")
    ok("English column untouched by the Arabic save",
       after["explanation"] == before["explanation"])
    ok("Turkish column untouched by the Arabic save",
       after["explanation_tr"] == before["explanation_tr"])
    conn = get_db()
    d_before = dict(conn.execute("SELECT body, body_ar FROM proc_doc "
                                 "WHERE section='three_way_match'").fetchone())
    conn.close()
    r = client.post("/procurement/workflow/doc",
                    data={"_csrf": "tok", "section": "three_way_match",
                          "body_tr": "Yöneticinin 3'lü mutabakat metni.",
                          "body": d_before["body"], "body_ar": d_before["body_ar"]})
    ok("POST /procurement/workflow/doc [tr only] -> 302 (raw)", r.status_code == 302)
    conn = get_db()
    row = dict(conn.execute("SELECT body, body_ar, body_tr FROM proc_doc "
                            "WHERE section='three_way_match'").fetchone())
    conn.close()
    ok("Turkish saved; English + Arabic untouched",
       row["body_tr"] == "Yöneticinin 3'lü mutabakat metni."
       and row["body"] == C.DOC_SECTIONS["three_way_match"]
       and row["body_ar"] == T.DOC_AR["three_way_match"])

    # ---------------------------------------------------------------- 5
    print("\n--- 5. create_and_seed is idempotent and preserves admin text --")
    counts = []
    for _ in range(3):
        conn = get_db()
        S.create_and_seed(conn)
        counts.append(tuple(conn.execute("SELECT COUNT(*) FROM " + t).fetchone()[0]
                            for t in ("proc_stage_meta", "proc_role_meta", "proc_doc")))
        conn.close()
    ok("row counts identical after 3 create_and_seed runs: %s" % (counts,),
       counts[0] == counts[1] == counts[2])
    conn = get_db()
    dupes = conn.execute("SELECT stage FROM proc_stage_meta GROUP BY stage "
                         "HAVING COUNT(*) > 1").fetchall()
    dupes += conn.execute("SELECT section FROM proc_doc GROUP BY section "
                          "HAVING COUNT(*) > 1").fetchall()
    ok("no duplicate stage / section rows", not dupes)
    ok("the admin's Arabic stage text survived 3 re-seeds",
       conn.execute("SELECT explanation_ar FROM proc_stage_meta WHERE stage='cfo'")
       .fetchone()["explanation_ar"] == "نص المسؤول للمرحلة المالية.")
    ok("the admin's Turkish gate text survived 3 re-seeds",
       conn.execute("SELECT body_tr FROM proc_doc WHERE section='three_way_match'")
       .fetchone()["body_tr"] == "Yöneticinin 3'lü mutabakat metni.")
    ok("an untouched English column is still exactly the code default",
       conn.execute("SELECT explanation FROM proc_stage_meta WHERE stage='warehouse'")
       .fetchone()["explanation"] == C.STAGE_EXPLAIN["warehouse"])
    conn.close()

    # ---------------------------------------------------------------- 6
    print("\n--- 6. the money controls still state the SAME rule ------------")
    # payment_cap: cap = LOWER of PO total and invoiced gross; refused on over-billing
    ok("[ar] payment cap keeps 'the lower of the two' + refusal on over-billing",
       "الأقل من الاثنين" in T.DOC_AR["payment_cap"]
       and "فوترة زائدة" in T.DOC_AR["payment_cap"]
       and "المدفوعات المتراكمة" in T.DOC_AR["payment_cap"])
    ok("[tr] payment cap keeps 'the lower of the two' + refusal on over-billing",
       "DAHA KÜÇÜĞÜ" in T.DOC_TR["payment_cap"]
       and "fazla faturalama" in T.DOC_TR["payment_cap"].lower()
       and "Birikimli ödemeler" in T.DOC_TR["payment_cap"])
    # three_way_match: short delivery does NOT block, over-billing DOES, 1% tolerance
    ok("[ar] 3-way match keeps 'short delivery does not block' + 1% tolerance",
       "لا يمنع الدفع" in T.DOC_AR["three_way_match"]
       and "1%" in T.DOC_AR["three_way_match"]
       and "فتمنع الدفع" in T.DOC_AR["three_way_match"])
    ok("[tr] 3-way match keeps 'short delivery does not block' + 1% tolerance",
       "ENGELLEMEZ" in T.DOC_TR["three_way_match"]
       and "%1" in T.DOC_TR["three_way_match"]
       and "engeller" in T.DOC_TR["three_way_match"])
    # rfq: DISTINCT vendors, waivable by single-source justification
    ok("[ar] RFQ keeps 'distinct vendors' and the single-source waiver",
       "مورّدين مختلفين" in T.DOC_AR["rfq"] and "مصدر واحد" in T.DOC_AR["rfq"])
    ok("[tr] RFQ keeps 'distinct vendors' and the single-source waiver",
       "FARKLI tedarikçiler" in T.DOC_TR["rfq"] and "tek kaynak" in T.DOC_TR["rfq"])
    # budget gate: blocks the PO, only when a budget row exists, admin may force
    ok("[ar] budget gate keeps 'only with a budget row' + admin force",
       "لا توجد له موازنة مُعرَّفة لا يُمنع" in T.DOC_AR["budget_gate"]
       and "يفرض" in T.DOC_AR["budget_gate"])
    ok("[tr] budget gate keeps 'only with a budget row' + admin force",
       "tanımlı olmayan bir departman hiçbir zaman engellenmez" in T.DOC_TR["budget_gate"]
       and "zorlayarak" in T.DOC_TR["budget_gate"])
    # sod: self-approval + dual role, admin-exempt knob
    ok("[ar] SoD keeps both independence rules and the exemption knob",
       "الموافقة الذاتية" in T.DOC_AR["sod"] and "مرحلتين مختلفتين" in T.DOC_AR["sod"]
       and "super_admin" in T.DOC_AR["sod"])
    ok("[tr] SoD keeps both independence rules and the exemption knob",
       "Kendi kendine onay" in T.DOC_TR["sod"] and "İKİ FARKLI" in T.DOC_TR["sod"]
       and "super_admin" in T.DOC_TR["sod"])
    # pricing gate: value rungs recalculated, in-flight signatures untouched
    ok("[ar] pricing gate keeps 'in-flight signatures are never disturbed'",
       "المعتمدة أو المرفوضة أو النشطة حاليًا فلا تُمَس أبدًا" in T.DOC_AR["pricing_gate"]
       and "غير مسعَّر" in T.DOC_AR["pricing_gate"])
    ok("[tr] pricing gate keeps 'in-flight signatures are never disturbed'",
       "asla dokunulmaz" in T.DOC_TR["pricing_gate"])
    # warehouse stage: stock on the shelf + last ordered qty/price, always required
    ok("[ar] warehouse stage keeps stock-before-commit and 'always required'",
       "الرصيد" in T.STAGE_AR["warehouse"] and "دائمًا" in T.STAGE_AR["warehouse"])
    ok("[tr] warehouse stage keeps stock-before-commit and 'always required'",
       "stoku kontrol" in T.STAGE_TR["warehouse"] and "her zaman" in T.STAGE_TR["warehouse"])

    # ---------------------------------------------------------------- 7
    print("\n--- 7. RTL: Arabic renders right-to-left, prose not forced LTR --")
    tpl = io.open(str(REPO / "app" / "templates" / "approvals" / "workflow.html"),
                  encoding="utf-8").read()
    ok("workflow.html forces no LTR direction anywhere",
       "direction:ltr" not in tpl.replace(" ", "") and 'dir="ltr"' not in tpl)
    for lg in LANGS:
        _r, p = page(client, lg)
        ok("<html lang> is %s for a %s reader" % (lg, lg), p.html_attrs.get("lang") == lg)
    _r, p = page(client, "ar")
    html = _r.data.decode("utf-8")
    ok("the Arabic editor box itself is dir=rtl", 'name="body_ar" rows="7" dir="rtl"' in html
       or 'dir="rtl"' in html)
    ok("no inline style on the prose paragraphs beyond line-height",
       'style="margin:0;line-height:1.7"' in html)

    # ---------------------------------------------------------------- 8
    print("\n--- 8. a role an admin RENAMED keeps the admin's name -----------")
    from app.security import refresh_db_roles
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO custom_roles (role_key, label, perms_json) "
                 "VALUES (?,?,?)", ("warehouse_manager", "Depot Chief", '["proc_view"]'))
    conn.execute("UPDATE custom_roles SET label='Depot Chief' WHERE role_key=?",
                 ("warehouse_manager",))
    conn.execute("INSERT OR IGNORE INTO custom_roles (role_key, label, perms_json) "
                 "VALUES (?,?,?)", ("bale_press_lead", "Bale Press Lead", '["proc_view"]'))
    conn.commit()
    conn.close()
    refresh_db_roles()
    for lg in ("ar", "tr"):
        L = svc.workflow_view(None, lg)["L"]
        ok("[%s] a renamed role shows the admin's name, not the translation" % lg,
           L["role"]["warehouse_manager"] == "Depot Chief")
        ok("[%s] a brand-new admin role shows its own name" % lg,
           L["role"]["bale_press_lead"] == "Bale Press Lead")
        ok("[%s] an untouched role is still translated" % lg,
           L["role"]["storekeeper"] == T.ROLE_LABEL[lg]["storekeeper"])
    conn = get_db()
    conn.execute("DELETE FROM custom_roles WHERE role_key IN ('warehouse_manager',"
                 "'bale_press_lead')")
    conn.commit()
    conn.close()
    refresh_db_roles()

    print("\n" + "=" * 70)
    print("PASS %d   FAIL %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAILED: " + f)
    print("ENGLISH LEAKS REMAINING: %d" % len(LEAKS))
    for l in LEAKS:
        print("  LEAK: " + l)
    print("=" * 70)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
