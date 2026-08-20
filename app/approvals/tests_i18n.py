# -*- coding: utf-8 -*-
"""
Procurement Workflow & Governance trilingual self-test — throwaway database.
    python app/approvals/tests_i18n.py

Proves, by execution, that the DB-stored explanation prose really renders in the
reader's own language and that the addition is safe:
  (a) /procurement/workflow returns 200 for en / ar / tr;
  (b) in 'ar' the page carries Arabic script AND the English source sentence is
      gone; in 'tr' the Turkish text is present and differs from the English;
  (c) no explanation panel renders empty, in any language;
  (d) a row whose translation is NULL falls back to the English text, not blank;
  (e) an admin edit to ONE language leaves the other two intact;
  (f) create_and_seed three times duplicates nothing and never overwrites an
      admin's edited text (English or translated);
  (g) the stage / role / status / gate labels are wrapped in data-i18n keys.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wf_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                          # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                             # noqa: E402

app = create_app()
PASS = []

ARABIC = re.compile(r"[؀-ۿ]")


def ok(label, cond):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label)


def set_lang(conn, lang):
    conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
    conn.commit()


def get_page(client, conn, lang):
    set_lang(conn, lang)
    # This whole file runs inside ONE app context, and Flask reuses it for every
    # test request — so auth.current_user()'s per-context cache would otherwise
    # hand every request the language the FIRST one saw.
    from flask import g
    g.pop("user", None)
    r = client.get("/procurement/workflow")
    return r.status_code, r.get_data(as_text=True)


def prose_paragraphs(html):
    """The DB-driven prose as the READER sees it: <p ... line-height:1.6/1.7>
    blocks, which is exactly what the overview / stage / gate / role panels render
    their body into. Deliberately excludes the admin editor, whose textareas hold
    all three languages at once by design."""
    return [p.strip() for p in re.findall(
        r'<p[^>]*line-height:1\.[67][^>]*>(.*?)</p>', html, re.S)]


with app.app_context():
    from app.db import get_db
    from app.approvals.schema import create_and_seed
    from app.approvals import services as svc
    from app.approvals import constants as C
    from app.approvals import i18n_text as T

    conn = get_db()
    create_and_seed(conn)
    conn.commit()

    print("\n--- 1. migration: the six translation columns exist ------------")
    for table, col in (("proc_stage_meta", "explanation"),
                       ("proc_role_meta", "explanation"), ("proc_doc", "body")):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        ok(f"{table}: {col}, {col}_ar, {col}_tr all present",
           {col, col + "_ar", col + "_tr"} <= cols)

    print("\n--- 2. seed: every English row got an Arabic and a Turkish one --")
    for table, keycol, col, defaults in (
            ("proc_stage_meta", "stage", "explanation", T.STAGE),
            ("proc_role_meta", "role_key", "explanation", T.ROLE),
            ("proc_doc", "section", "body", T.DOC)):
        rows = conn.execute(f"SELECT {keycol} k, {col} en, {col}_ar ar, {col}_tr tr "
                            f"FROM {table}").fetchall()
        ok(f"{table}: {len(rows)} rows, all three languages non-blank",
           len(rows) > 0 and all((r["en"] or "").strip() and (r["ar"] or "").strip()
                                 and (r["tr"] or "").strip() for r in rows))
        ok(f"{table}: every Arabic row carries Arabic script",
           all(ARABIC.search(r["ar"] or "") for r in rows))
        ok(f"{table}: Turkish differs from English on every row",
           all((r["tr"] or "").strip() != (r["en"] or "").strip() for r in rows))
        ok(f"{table}: seeded text matches i18n_text.py exactly",
           all(r["ar"] == defaults["ar"][r["k"]] for r in rows if r["k"] in defaults["ar"]))

    print("\n--- 3. the page renders in all three languages -----------------")
    client = app.test_client()
    with client.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0

    pages, prose = {}, {}
    for lang in ("en", "ar", "tr"):
        code, html = get_page(client, conn, lang)
        pages[lang] = html
        prose[lang] = "\n".join(prose_paragraphs(html))
        ok(f"GET /procurement/workflow [{lang}] -> 200 (raw)", code == 200)

    # A status meaning renders in a table cell, so pull those from the view model.
    def statuses(lang):
        return "\n".join(s["body"] for s in
                         svc.workflow_view("Production", lang)["statuses"])

    en_sod = C.DOC_SECTIONS["sod"]
    en_overview = C.DOC_SECTIONS["overview"]
    en_ceo = C.STAGE_EXPLAIN["ceo"]
    en_cfo_role = C.ROLE_EXPLAIN["cfo"]

    ok("[ar] the rendered prose is in Arabic script", ARABIC.search(prose["ar"]))
    ok("[ar] the English SoD paragraph is ABSENT from the prose",
       en_sod not in prose["ar"])
    ok("[ar] the English overview paragraph is ABSENT from the prose",
       en_overview not in prose["ar"])
    ok("[ar] the English CEO stage paragraph is ABSENT from the prose",
       en_ceo not in prose["ar"])
    ok("[ar] the English CFO role paragraph is ABSENT from the prose",
       en_cfo_role not in prose["ar"])
    ok("[ar] NO English default paragraph survives anywhere in the prose",
       not any(t in prose["ar"] for t in
               list(C.STAGE_EXPLAIN.values()) + list(C.ROLE_EXPLAIN.values())
               + list(C.DOC_SECTIONS.values())))
    ok("[ar] the Arabic SoD paragraph IS in the prose", T.DOC_AR["sod"] in prose["ar"])
    ok("[ar] the Arabic CEO stage paragraph IS in the prose",
       T.STAGE_AR["ceo"] in prose["ar"])
    ok("[ar] the Arabic CFO role paragraph IS in the prose",
       T.ROLE_AR["cfo"] in prose["ar"])
    ok("[ar] the Arabic 'rejected' status meaning IS rendered",
       T.STATUS_AR["rejected"] in statuses("ar")
       and C.STATUS_MEANING["rejected"] not in statuses("ar"))

    ok("[tr] the Turkish SoD paragraph IS in the prose", T.DOC_TR["sod"] in prose["tr"])
    ok("[tr] the Turkish CEO stage paragraph IS in the prose",
       T.STAGE_TR["ceo"] in prose["tr"])
    ok("[tr] the Turkish CFO role paragraph IS in the prose",
       T.ROLE_TR["cfo"] in prose["tr"])
    ok("[tr] the Turkish 'rejected' status meaning IS rendered",
       T.STATUS_TR["rejected"] in statuses("tr")
       and C.STATUS_MEANING["rejected"] not in statuses("tr"))
    ok("[tr] the Turkish prose DIFFERS from the English prose",
       prose["tr"] != prose["en"] and en_sod not in prose["tr"]
       and T.DOC_TR["sod"] not in prose["en"])
    ok("[tr] no English default paragraph survives anywhere in the prose",
       not any(t in prose["tr"] for t in
               list(C.STAGE_EXPLAIN.values()) + list(C.ROLE_EXPLAIN.values())
               + list(C.DOC_SECTIONS.values())))
    ok("[en] the English wording is untouched",
       en_sod in prose["en"] and en_overview in prose["en"] and en_ceo in prose["en"]
       and not ARABIC.search(prose["en"]))

    print("\n--- 4. no empty explanation panel, in any language -------------")
    for lang in ("en", "ar", "tr"):
        paras = prose_paragraphs(pages[lang])
        # overview + requester + 8 ladder stages + 6 gates + 13 roles
        ok(f"[{lang}] {len(paras)} prose paragraphs, none blank",
           len(paras) >= 21 and all(p.strip() for p in paras))
        ok(f"[{lang}] every status meaning is non-blank",
           all((s["body"] or "").strip()
               for s in svc.workflow_view("Production", lang)["statuses"]))
        ok(f"[{lang}] no panel rendered an empty <p>",
           '<p class="mt-2" style="margin:0;line-height:1.7"></p>' not in pages[lang])

    print("\n--- 5. NULL translation -> English fallback, never blank -------")
    conn.execute("UPDATE proc_doc SET body_ar=NULL, body_tr='' WHERE section='rfq'")
    conn.commit()
    v_ar = svc.workflow_view("Production", "ar")
    rfq_ar = [g for g in v_ar["gates"] if g["section"] == "rfq"][0]
    ok("NULL body_ar falls back to the English body (not blank)",
       rfq_ar["body"] == C.DOC_SECTIONS["rfq"])
    v_tr = svc.workflow_view("Production", "tr")
    rfq_tr = [g for g in v_tr["gates"] if g["section"] == "rfq"][0]
    ok("blank body_tr falls back to the English body (not blank)",
       rfq_tr["body"] == C.DOC_SECTIONS["rfq"])
    code, html = get_page(client, conn, "ar")
    ok("[ar] the page still renders 200 with a missing translation", code == 200)
    ok("[ar] the fallback English RFQ text is on the page",
       C.DOC_SECTIONS["rfq"] in html)

    print("\n--- 6. an edit to ONE language leaves the other two intact -----")
    admin = dict(conn.execute("SELECT * FROM users WHERE id=1").fetchone())
    before = dict(conn.execute("SELECT body, body_ar, body_tr FROM proc_doc "
                              "WHERE section='sod'").fetchone())
    ok("set_doc(body_ar only) saves Arabic",
       svc.set_doc("sod", body_ar="نص عربي من المسؤول.", user=admin)[0])
    after = dict(conn.execute("SELECT body, body_ar, body_tr FROM proc_doc "
                             "WHERE section='sod'").fetchone())
    ok("English body unchanged by the Arabic edit", after["body"] == before["body"])
    ok("Turkish body unchanged by the Arabic edit", after["body_tr"] == before["body_tr"])
    ok("Arabic body is the admin's text", after["body_ar"] == "نص عربي من المسؤول.")
    ok("[ar] the admin's Arabic text is what the page shows",
       "نص عربي من المسؤول." in svc.workflow_view("Production", "ar")["gates"][2]["body"])
    ok("[en]/[tr] still show their own text",
       svc.workflow_view("Production", "en")["gates"][2]["body"] == before["body"]
       and svc.workflow_view("Production", "tr")["gates"][2]["body"] == before["body_tr"])

    # 'empty' now means "no language field was submitted at all" — a malformed
    # POST. A language submitted BLANK is a deliberate clear and is saved as ''
    # (see tests_blank_lang.py), so it must not be refused here.
    ok("set_doc with NO language submitted at all is refused ('empty')",
       svc.set_doc("sod", user=admin) == (False, "empty"))
    ok("...and the stored Arabic survived the refused save",
       conn.execute("SELECT body_ar FROM proc_doc WHERE section='sod'")
       .fetchone()["body_ar"] == "نص عربي من المسؤول.")

    svc.set_role_meta("cfo", explanation_tr="Yöneticinin kendi metni.", user=admin)
    r_cfo = dict(conn.execute("SELECT explanation, explanation_ar, explanation_tr "
                              "FROM proc_role_meta WHERE role_key='cfo'").fetchone())
    ok("set_role_meta(tr only): Turkish saved, EN + AR intact",
       r_cfo["explanation_tr"] == "Yöneticinin kendi metni."
       and r_cfo["explanation"] == C.ROLE_EXPLAIN["cfo"]
       and r_cfo["explanation_ar"] == T.ROLE_AR["cfo"])

    svc.set_stage_meta("ceo", explanation_ar="شرح المرحلة من المسؤول.", user=admin)
    s_ceo = dict(conn.execute("SELECT role, explanation, explanation_ar, explanation_tr "
                              "FROM proc_stage_meta WHERE stage='ceo'").fetchone())
    ok("set_stage_meta(ar only): Arabic saved, EN + TR + role intact",
       s_ceo["explanation_ar"] == "شرح المرحلة من المسؤول."
       and s_ceo["explanation"] == C.STAGE_EXPLAIN["ceo"]
       and s_ceo["explanation_tr"] == T.STAGE_TR["ceo"])

    print("\n--- 7. create_and_seed x3: no duplicates, no overwrites --------")
    counts_before = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                     for t in ("proc_stage_meta", "proc_role_meta", "proc_doc")}
    for _ in range(3):
        create_and_seed(conn)
        conn.commit()
    counts_after = {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
                    for t in ("proc_stage_meta", "proc_role_meta", "proc_doc")}
    ok("row counts identical after three more create_and_seed runs",
       counts_before == counts_after)
    ok("the admin's Arabic doc text survived re-seeding",
       conn.execute("SELECT body_ar FROM proc_doc WHERE section='sod'")
       .fetchone()["body_ar"] == "نص عربي من المسؤول.")
    ok("the admin's Turkish role text survived re-seeding",
       conn.execute("SELECT explanation_tr FROM proc_role_meta WHERE role_key='cfo'")
       .fetchone()["explanation_tr"] == "Yöneticinin kendi metni.")
    ok("the admin's Arabic stage text survived re-seeding",
       conn.execute("SELECT explanation_ar FROM proc_stage_meta WHERE stage='ceo'")
       .fetchone()["explanation_ar"] == "شرح المرحلة من المسؤول.")
    ok("the NULLed rfq translation was re-seeded from the code default",
       conn.execute("SELECT body_ar, body_tr FROM proc_doc WHERE section='rfq'")
       .fetchone()["body_ar"] == T.DOC_AR["rfq"])
    ok("English columns still exactly the code defaults where untouched",
       conn.execute("SELECT explanation FROM proc_stage_meta WHERE stage='warehouse'")
       .fetchone()["explanation"] == C.STAGE_EXPLAIN["warehouse"])

    print("\n--- 8. labels are resolved SERVER-SIDE, per language -----------")
    # These labels used to carry data-i18n keys (proc.stage.*, proc.gate.*,
    # proc.status.*, role.*) that were never added to app/static/i18n/*.json.
    # app.js does `el.textContent = DICT[key] || key`, so each one printed the RAW
    # KEY on the page in all three languages. They are resolved server-side now —
    # see app/approvals/services.py::_labels and tests_i18n_adversarial.py.
    from html import unescape as _unescape
    for lang in ("en", "ar", "tr"):
        code, html = get_page(client, conn, lang)
        # Jinja escapes the apostrophe in labels like "3'lü mutabakat", so the
        # comparison runs on the unescaped text the reader actually sees.
        text = _unescape(html)
        v = svc.workflow_view(None, lang)
        L = v["L"]
        ok(f"[{lang}] every ladder stage renders its {lang} label",
           all(L["stage"][s["stage"]] in text for s in v["stages"])
           and L["stage"]["requester"] in text)
        ok(f"[{lang}] every PR status renders its {lang} label",
           all(L["status"][s] in text for s in C.PR_STATUSES))
        ok(f"[{lang}] every gate renders its {lang} label",
           all(L["gate"][g["section"]] in text for g in v["gates"]))
        ok(f"[{lang}] every role in the Roles table renders its {lang} label",
           all(L["role"].get(r["key"], r["key"]) in text for r in v["roles"]))
        ok(f"[{lang}] every role in the stage editor renders its {lang} label",
           all(rc["label"] in text for rc in v["role_choices"]))
        ok(f"[{lang}] no fabricated data-i18n key is left on the page",
           'data-i18n="proc.stage.' not in html
           and 'data-i18n="proc.gate.' not in html
           and 'data-i18n="proc.status.' not in html
           and 'data-i18n="role.' not in html)
    code, html = get_page(client, conn, "en")
    ok("no bare role key leaks as a signing tag",
       '<span class="tag">warehouse_manager</span>' not in html)
    ok("the editor offers a textarea per language",
       'name="body_ar"' in html and 'name="body_tr"' in html
       and 'name="explanation_ar"' in html and 'name="explanation_tr"' in html)

    print("\n--- 9. the real POST route saves one language at a time --------")
    with client.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
        s["_csrf_token"] = "tok"
    # The browser posts all three boxes as the editor rendered them; only the
    # Arabic was retyped. Posting '' for the others would mean "cleared on
    # purpose", a different intent covered by tests_blank_lang.py.
    d0 = dict(conn.execute("SELECT body, body_tr FROM proc_doc "
                           "WHERE section='budget_gate'").fetchone())
    r = client.post("/procurement/workflow/doc",
                    data={"_csrf": "tok", "section": "budget_gate",
                          "body_ar": "بوابة الموازنة — نص المسؤول.",
                          "body": d0["body"], "body_tr": d0["body_tr"]})
    ok("POST /procurement/workflow/doc [ar only] -> 302 (raw)", r.status_code == 302)
    row = dict(conn.execute("SELECT body, body_ar, body_tr FROM proc_doc "
                            "WHERE section='budget_gate'").fetchone())
    ok("the route saved Arabic and left English + Turkish alone",
       row["body_ar"] == "بوابة الموازنة — نص المسؤول."
       and row["body"] == C.DOC_SECTIONS["budget_gate"]
       and row["body_tr"] == T.DOC_TR["budget_gate"])
    s0 = dict(conn.execute("SELECT explanation, explanation_ar FROM proc_stage_meta "
                           "WHERE stage='finance'").fetchone())
    r = client.post("/procurement/workflow/stage",
                    data={"_csrf": "tok", "stage": "finance", "roles": "finance_manager",
                          "explanation": s0["explanation"],
                          "explanation_ar": s0["explanation_ar"],
                          "explanation_tr": "Finans aşaması — yöneticinin metni."})
    ok("POST /procurement/workflow/stage [tr only] -> 302 (raw)", r.status_code == 302)
    row = dict(conn.execute("SELECT role, explanation, explanation_ar, explanation_tr "
                            "FROM proc_stage_meta WHERE stage='finance'").fetchone())
    ok("the route saved Turkish, kept EN + AR, and still applied the role override",
       row["explanation_tr"] == "Finans aşaması — yöneticinin metni."
       and row["explanation"] == C.STAGE_EXPLAIN["finance"]
       and row["explanation_ar"] == T.STAGE_AR["finance"]
       and row["role"] == "finance_manager")
    r = client.post("/procurement/workflow/stage",
                    data={"_csrf": "tok", "stage": "finance", "reset": "explanation"})
    row = dict(conn.execute("SELECT explanation, explanation_ar, explanation_tr "
                            "FROM proc_stage_meta WHERE stage='finance'").fetchone())
    # Picked by stage key, not by ladder position: index 3 was 'finance' on the
    # six-rung paper ladder and is 'scd' on the DOAM one.
    _st = {s["stage"]: s for s in svc.workflow_view("Production", "tr")["stages"]}
    ok("reset clears all three languages so the code defaults apply again",
       r.status_code == 302 and not any(row.values())
       and _st["finance"]["explanation"] == T.STAGE_TR["finance"])

    print("\n--- 10. nothing else moved ------------------------------------")
    ok("workflow_view still reports the whole ladder / 6 gates / 9 statuses / 4 knobs",
       [s["stage"] for s in v["stages"]] == list(C.LADDER) and len(v["gates"]) == 6
       and len(v["statuses"]) == len(C.PR_STATUSES) and len(v["knobs"]) == 4)
    ok("an unknown language falls back to English",
       svc.workflow_view("Production", "de")["overview"]["body"]
       == C.DOC_SECTIONS["overview"])
    ok("set_doc still refuses an unknown section",
       svc.set_doc("nope", "x", admin) == (False, "unknown_section"))
    ok("set_role_meta still refuses an unknown role",
       svc.set_role_meta("nope", "x", admin)[0] is False)
    set_lang(conn, "en")
    conn.close()

print("\n%d checks, %d passed, %d failed"
      % (len(PASS), sum(PASS), len(PASS) - sum(PASS)))
print("ALL GREEN" if all(PASS) else "FAILURES ABOVE")
sys.exit(0 if all(PASS) else 1)
