"""The amount each approval joins at is an admin setting, not a code edit.

Asked for directly: "I need to control the value for each approval from the
settings". Until now DOAM 4.1 / 4.2 lived only in constants.py, and the only way
to move a number was to edit the file — or to LOWER it for one department through
the responsibility matrix, which the DOAM floor then clamped.

So every rung now carries a knob (thr_<kind>_<stage>) on the same
"DB override -> constant default" rail as every other governance setting. This
file is the proof that the rail did not become a hole in the process:

  * an empty database routes exactly as the shipped DOAM does
  * a saved number really changes who signs, through the SUBMIT path
  * a value the coercion rejects is ignored, and a negative one is refused
    outright, so a bad entry can never silently make a request cheap
  * a knob moves a rung, it never deletes one
  * a department matrix is measured against the LIVE floor, not the shipped one
  * every generated knob has a human label in all three languages — an Arabic
    admin must not be shown `thr_opex_cfo`

    python app/approvals/tests_thresholds.py
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
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="thresh_"), "s.db")
    from app import create_app
    return create_app()


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import constants as C
        from app.approvals import i18n_text as T
        from app.approvals import services as svc

        ADMIN = {"username": "admin", "role": "super_admin"}
        DOAM = {k: float(v) for k, v in C.OPEX_MATRIX.items()}

        def live(kind="opex"):
            conn = get_db()
            try:
                return svc.live_matrix(conn, kind)
            finally:
                conn.close()

        def ladder(total, kind="opex", dept="Production"):
            conn = get_db()
            try:
                return svc.dept_ladder(conn, dept, total, kind)
            finally:
                conn.close()

        def raw(key):
            conn = get_db()
            try:
                r = conn.execute("SELECT value FROM proc_settings WHERE key=?",
                                 (key,)).fetchone()
                return r["value"] if r else None
            finally:
                conn.close()

        def put_raw(key, value):
            """Write a row WITHOUT set_setting's validation — the shape of a bad
            row that reached the table some other way (a script, a migration)."""
            conn = get_db()
            try:
                conn.execute("DELETE FROM proc_settings WHERE key=?", (key,))
                conn.execute("INSERT INTO proc_settings (key, value) VALUES (?,?)",
                             (key, value))
                conn.commit()
            finally:
                conn.close()

        # ---- 1. nothing stored == the manual as shipped ---------------------
        print("\n1. an empty database routes exactly as the DOAM in the code")
        chk("one knob per rung per expenditure kind",
            len([k for k in C.WORKFLOW_SETTINGS if k.startswith("thr_")])
            == len(C.OPEX_MATRIX) + len(C.CAPEX_MATRIX))
        chk("every knob's default IS the DOAM number",
            all(C.WORKFLOW_SETTINGS[C.threshold_key("opex", s)]["default"] == float(v)
                for s, v in C.OPEX_MATRIX.items()))
        chk("live matrix == OPEX_MATRIX with no overrides", live() == DOAM, live())
        chk("matrix_for() with no getter is the constant, untouched",
            C.matrix_for("opex") == DOAM and C.matrix_for("capex")
            == {k: float(v) for k, v in C.CAPEX_MATRIX.items()})
        base20 = ladder(20_000)
        chk("20,000 needs no CFO as shipped", "cfo" not in base20, base20)

        # ---- 2. a saved number changes who signs ----------------------------
        print("\n2. a number an admin saves changes the ladder")
        ok, msg = svc.set_setting("thr_opex_cfo", "15000", ADMIN)
        chk("the CFO threshold saves", ok, msg)
        chk("the live matrix reads it back", live()["cfo"] == 15000.0, live()["cfo"])
        now20 = ladder(20_000)
        chk("20,000 now collects the CFO", "cfo" in now20, now20)
        chk("and nothing else moved",
            [s for s in now20 if s != "cfo"] == base20, (base20, now20))
        chk("15,000 itself does not — the band is exclusive, as the DOAM reads",
            "cfo" not in ladder(15_000))
        chk("15,001 does", "cfo" in ladder(15_001))

        ok, _ = svc.set_setting("thr_opex_bod", "0", ADMIN)
        chk("the Board can be put on every request (0 = always)",
            ok and "bod" in ladder(1), ladder(1))
        svc.reset_setting("thr_opex_bod", ADMIN)
        chk("reset puts the DOAM number back",
            live()["bod"] == DOAM["bod"] and raw("thr_opex_bod") is None, live()["bod"])

        # ---- 3. the SUBMIT path, not just the read model --------------------
        print("\n3. the ladder a real request is actually given")

        def role(r):
            conn = get_db()
            try:
                row = conn.execute("SELECT id, username, role FROM users "
                                   "WHERE role=? AND is_active=1 LIMIT 1",
                                   (r,)).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

        buyer = role("purchasing_manager")
        if not buyer:
            print("  SKIP  this database has no purchasing roster")
        else:
            plain = {"username": "thr.requester", "role": "normal_user", "id": 90101}
            pr_id, _ = svc.create_pr(
                {"title": "threshold routing", "department": "Production",
                 "currency": "EGP"},
                [{"item": "THR-PART", "qty": 1, "unit_price": 0}],
                plain, submit=True, priced=False)
            conn = get_db()
            try:
                line = conn.execute("SELECT id FROM pr_items WHERE pr_id=?",
                                    (pr_id,)).fetchone()["id"]
            finally:
                conn.close()
            okp, msgp = svc.price_pr(pr_id, {line: 20_000}, {"tax_rate": 0}, buyer)
            chk("a 20,000 request prices", okp, msgp)
            conn = get_db()
            try:
                stages = [r["stage"] for r in conn.execute(
                    "SELECT stage FROM pr_steps WHERE pr_id=? ORDER BY seq",
                    (pr_id,)).fetchall()]
            finally:
                conn.close()
            chk("its signature list carries the CFO because the SETTING says 15,000",
                "cfo" in stages, stages)

        # ---- 4. a bad value can never make a request cheap ------------------
        print("\n4. a value that would weaken the gate is refused or ignored")
        before = live()["cfo"]
        for bad, why in (("-5", "negative"), ("", "blank"), ("abc", "not a number"),
                         ("nan", "NaN"), ("inf", "infinity")):
            ok, msg = svc.set_setting("thr_opex_cfo", bad, ADMIN)
            chk("%-7s is refused at the write (%s)" % (repr(bad), why), not ok, msg)
        chk("...and the stored number never moved", live()["cfo"] == before,
            live()["cfo"])

        put_raw("thr_opex_cfo", "not-a-number")
        chk("a bad row that reached the table some other way is IGNORED",
            live()["cfo"] == DOAM["cfo"], live()["cfo"])
        put_raw("thr_opex_cfo", "-1")
        chk("so is an out-of-bounds row — the DOAM number applies",
            live()["cfo"] == DOAM["cfo"], live()["cfo"])
        chk("and the request routes on the DOAM, not on the bad row",
            ("cfo" in ladder(600_000)) and ("cfo" not in ladder(20_000)))

        # A knob moves a rung. It must not be able to DELETE one: the rung list
        # is the matrix's, and dropping a signature is a DOAM amendment.
        svc.set_setting("thr_opex_cfo", "999999999", ADMIN)
        chk("a huge threshold moves the rung up — it does not remove the stage",
            "cfo" in live() and "cfo" not in ladder(600_000)
            and "cfo" in ladder(10 ** 10), live()["cfo"])
        svc.reset_setting("thr_opex_cfo", ADMIN)
        chk("every DOAM rung is still a key in the live matrix",
            set(live()) == set(DOAM))

        # ---- 5. a department matrix is measured against the LIVE floor ------
        print("\n5. a department matrix is clamped by the number IN FORCE")
        rows = [{"stage": s, "threshold": DOAM[s]} for s in C.OPEX_MATRIX]
        ok, msg = svc.set_dept_matrix("Production", rows, ADMIN)
        chk("a matrix at the DOAM numbers is accepted", ok, msg)
        svc.set_setting("thr_opex_cfo", "100", ADMIN)
        ok, msg = svc.set_dept_matrix("Production", rows, ADMIN)
        chk("after the company LOWERS the CFO to 100, the same matrix is refused",
            (not ok) and "below_doam_floor" in str(msg), msg)
        chk("...and the refusal quotes the live floor, not the shipped 500,000",
            "starts at 100 " in str(msg), msg)
        svc.reset_setting("thr_opex_cfo", ADMIN)
        ok, msg = svc.set_dept_matrix("Production", rows, ADMIN)
        chk("reset, and it is accepted again", ok, msg)
        chk("the settings screen is shown the LIVE numbers",
            svc.effective_thresholds("opex") == live())

        # ---- 6. nobody is shown a raw key -----------------------------------
        print("\n6. every generated knob has a human label, in all three languages")
        keys = [k for k in C.WORKFLOW_SETTINGS if k.startswith("thr_")]
        for lang in ("en", "ar", "tr"):
            L = svc.labels(lang)
            missing = [k for k in keys
                       if not L["setting"].get(k) or L["setting"][k] == k]
            chk("%s: no knob falls back to its key" % lang, not missing, missing)
            labels = [L["setting"][k] for k in keys]
            chk("%s: every label is distinct" % lang,
                len(set(labels)) == len(labels))
        ar = svc.labels("ar")["setting"]["thr_opex_cfo"]
        chk("the Arabic label is Arabic, not an English string",
            any("؀" <= ch <= "ۿ" for ch in ar), ar)
        warn = svc.labels("en")["setting_warn"]
        chk("a rung that is on EVERY request warns before it is given an amount",
            all(C.threshold_key("opex", s) in warn
                for s, v in C.OPEX_MATRIX.items() if v == 0),
            sorted(k for k in warn if k.startswith("thr_")))
        chk("and a rung that already has one does not nag",
            C.threshold_key("opex", "cfo") not in warn)
        chk("the purchasing warning says the pricing gate goes with it",
            "pricing gate" in warn[C.threshold_key("opex", "purchasing")].lower())
        chk("threshold_warn is translated, not English on an Arabic page",
            all(any("؀" <= ch <= "ۿ" for ch in t)
                for t in T.threshold_warn("ar").values()))

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
