# -*- coding: utf-8 -*-
"""DOAM §4.2 — an expenditure type nobody can read is a REFUSAL, not an OPEX.

C.normalise_expenditure_kind has always reported two things: the kind, and
whether it recognised what it was given. Only the first was ever used. So
"capitol", "1" and a blank all arrived as OPEX — the SHORTER §4.1 ladder — and
a capital request quietly lost the rungs §4.2 puts above it, with nothing in
the audit trail naming the downgrade.

The missing half is wired here: the routes refuse the POST, and say what
arrived and what the two answers are.

What this file holds in place:

  * an unreadable answer ("capitol", "1", a blank) is refused at POST time and
    NOTHING is written — a request stored on the wrong ladder is already wrong
  * the refusal quotes what was received and names both OPEX and CAPEX, so a
    requester can fix it without guessing, in EN / AR / TR
  * capex and opex both still go through, and so does the generous spelling
    the normaliser accepts ("Capital", "  CAPEX  ")
  * a field the form never posted is NOT a refusal. The browser omits it when
    an admin hides the switch (everything is then OPEX by policy), and the
    other in-house forms that reach the parser have never sent it — refusing
    those would be a new outage dressed up as a control
  * a re-file after rejection cannot downgrade a CAPEX request through the
    edit form, and a deliberate reclassification is still audited

    python app/approvals/tests_expenditure_gate.py
"""
import os
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="expgate_"), "f.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def run():
    app = _app()
    ok_all = [True]

    def chk(label, cond, extra=""):
        ok_all[0] &= bool(cond)
        print(("  PASS  " if cond else "  FAIL  ") + label
              + ((" | " + str(extra)) if extra else ""))

    from app.approvals import constants as C
    from app.routes import approvals as rt

    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = 1                       # the seeded administrator
        s["ep"] = 0
        s["_csrf_token"] = "tok"

    def flashes():
        """Read and clear the flash queue, the way rendering a page would."""
        with c.session_transaction() as s:
            msgs = [m for _cat, m in (s.get("_flashes") or [])]
            s["_flashes"] = []
        return msgs

    def db(sql, args=()):
        with app.app_context():
            from app.db import get_db
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

    def count():
        return db("SELECT COUNT(*) AS n FROM pr_requests")[0]["n"]

    def post_new(title, kind=..., action="draft"):
        """A request raised the way the form raises one. kind=... omits the
        field entirely, which is what the browser posts when it is hidden."""
        data = {"_csrf": "tok", "title": title, "department": "Production",
                "currency": "EGP",
                "item[]": "Bearing 6204", "description[]": "", "unit[]": "Pcs",
                "qty[]": "2", "current_stock[]": "0", "unit_price[]": "0",
                "item_notes[]": "", "spare_id[]": "", "item_id[]": "",
                "vendor[]": "", "action": action}
        if kind is not ...:
            data["expenditure_kind"] = kind
        r = c.post("/procurement/new", data=data, follow_redirects=False)
        return r, flashes()

    def kind_of(title):
        rows = db("SELECT expenditure_kind FROM pr_requests WHERE title=?", (title,))
        return rows[0]["expenditure_kind"] if rows else None

    def set_switch(key, on):
        return c.post("/procurement/workflow/setting",
                      data={"key": key, "value": "1" if on else "0",
                            "_csrf": "tok"}).status_code

    # ---- 0. the downgrade being prevented is a real one ---------------------
    print("the two ladders really do differ, so guessing one costs signatures")
    l_opex = C.build_ladder(900_000, "opex")
    l_capex = C.build_ladder(900_000, "capex")
    chk("900,000 routes differently as CAPEX than as OPEX", l_opex != l_capex,
        "%s vs %s" % (l_opex, l_capex))
    chk("and an unrecognised value falls to the OPEX side — which is exactly "
        "why it may not be silent",
        C.normalise_expenditure_kind("capitol") == ("opex", False))

    # ---- 1. the two real answers still work --------------------------------
    print("\nthe answers a requester can actually give still go through")
    for title, sent, want in [("Gate opex line", "opex", "opex"),
                              ("Gate capex line", "capex", "capex"),
                              ("Gate capital word", "Capital", "capex"),
                              ("Gate padded capex", "  CAPEX  ", "capex")]:
        r, msgs = post_new(title, sent)
        chk("%-12r accepted as %s" % (sent, want),
            r.status_code in (301, 302) and kind_of(title) == want,
            "%s / %r / %s" % (r.status_code, kind_of(title), msgs))

    # ---- 2. THE ONE THAT MATTERS: an unreadable answer is refused -----------
    print("\nan expenditure type nobody can read is refused, and nothing is stored")
    for sent in ("capitol", "1", "", "   ", "cap-ital-ish"):
        title = "Gate refused %r" % (sent,)
        before = count()
        r, msgs = post_new(title, sent)
        text = " ".join(msgs)
        chk("%-14r refused, and no request was written" % (sent,),
            r.status_code in (301, 302) and kind_of(title) is None
            and count() == before,
            "%s / stored=%r / %s" % (r.status_code, kind_of(title), text))
        chk("%-14r refusal names both OPEX and CAPEX" % (sent,),
            "OPEX" in text and "CAPEX" in text, text)
        if sent.strip():
            chk("%-14r refusal quotes what was received" % (sent,),
                sent.strip() in text, text)
        else:
            chk("%-14r refusal says nothing was chosen" % (sent,),
                rt.I18N["proc.kind_blank"][0] in text, text)

    # ---- 3. a field that was never posted is not an unreadable answer -------
    print("\nthe field simply not being posted is the unmarked request, not a refusal")
    r, msgs = post_new("Gate no field at all", ...)
    chk("a post carrying no expenditure_kind at all is accepted as OPEX",
        r.status_code in (301, 302) and kind_of("Gate no field at all") == "opex",
        "%s / %r / %s" % (r.status_code, kind_of("Gate no field at all"), msgs))

    # ---- 4. and neither is the admin switch being off ----------------------
    print("\nhiding the field is a policy answer (OPEX), not an unreadable one")
    chk("the switch saves", set_switch("show_expenditure_kind", False) in (200, 302))
    with app.app_context():
        from app.approvals import services as svc
        chk("visible_fields agrees the field is off",
            svc.visible_fields().get("show_expenditure_kind") is False)
    r, msgs = post_new("Gate hidden field", ...)
    chk("a request raised while the field is hidden is accepted as OPEX",
        r.status_code in (301, 302) and kind_of("Gate hidden field") == "opex",
        "%s / %r / %s" % (r.status_code, kind_of("Gate hidden field"), msgs))
    # A stale tab (or a hand-crafted post) still carrying the field while the
    # switch is off: the switch decides, and it decides OPEX. Refusing here
    # would let a display setting break submissions.
    r, msgs = post_new("Gate hidden but posted", "capitol")
    chk("junk posted while the field is hidden is still accepted as OPEX",
        r.status_code in (301, 302) and kind_of("Gate hidden but posted") == "opex",
        "%s / %r / %s" % (r.status_code, kind_of("Gate hidden but posted"), msgs))
    chk("the switch goes back on", set_switch("show_expenditure_kind", True) in (200, 302))

    # ---- 5. the re-file after a rejection cannot downgrade -----------------
    print("\nediting a CAPEX request cannot quietly turn it into an OPEX one")
    post_new("Gate edit capex", "capex")
    pid = db("SELECT id FROM pr_requests WHERE title=?",
             ("Gate edit capex",))[0]["id"]

    def post_edit(kind=...):
        data = {"_csrf": "tok", "title": "Gate edit capex",
                "department": "Production", "currency": "EGP",
                "item[]": "Bearing 6204", "description[]": "", "unit[]": "Pcs",
                "qty[]": "2", "current_stock[]": "0", "unit_price[]": "0",
                "item_notes[]": "", "spare_id[]": "", "item_id[]": "",
                "vendor[]": "", "action": "draft"}
        if kind is not ...:
            data["expenditure_kind"] = kind
        r = c.post("/procurement/pr/%d/edit" % pid, data=data,
                   follow_redirects=False)
        return r, flashes()

    r, msgs = post_edit("capitol")
    chk("an unreadable type on the edit form is refused",
        r.status_code in (301, 302) and kind_of("Gate edit capex") == "capex",
        "%s / %r / %s" % (r.status_code, kind_of("Gate edit capex"), msgs))
    chk("and the refusal is the same readable sentence",
        "capitol" in " ".join(msgs) and "CAPEX" in " ".join(msgs), msgs)

    r, msgs = post_edit("opex")
    chk("a DELIBERATE reclassification is still accepted",
        kind_of("Gate edit capex") == "opex",
        "%r / %s" % (kind_of("Gate edit capex"), msgs))
    actions = [x["action"] for x in
               db("SELECT action FROM pr_events WHERE pr_id=?", (pid,))]
    chk("and it is still audited as expenditure_kind_changed",
        "expenditure_kind_changed" in actions, actions)

    # ---- 6. the refusal reaches the reader in their own language ------------
    print("\nthe refusal is not English-only")
    chk("three distinct languages are on file for the refusal",
        len(rt.I18N["proc.kind_unreadable"]) == 3
        and len(set(rt.I18N["proc.kind_unreadable"])) == 3
        and all(t.strip() for t in rt.I18N["proc.kind_unreadable"]))
    chk("and for the blank case too",
        len(set(rt.I18N["proc.kind_blank"])) == 3
        and all(t.strip() for t in rt.I18N["proc.kind_blank"]))
    for lang, needle in (("ar", "الإنفاق"), ("tr", "harcama"), ("en", "Expenditure")):
        with app.app_context():
            from app.db import get_db
            conn = get_db()
            conn.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
            conn.commit()
            conn.close()
        _r, msgs = post_new("Gate lang %s" % lang, "capitol")
        chk("a %s reader is refused in %s" % (lang, lang),
            any(needle in m for m in msgs), msgs)

    print("\n" + ("RESULT: ALL GREEN" if ok_all[0] else "RESULT: FAILURES ABOVE"))
    return ok_all[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
