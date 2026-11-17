"""Approval amounts can no longer bring value gating back.

The owner's ladder (2026-11-17): every new request, OPEX or CAPEX, at any
amount, signs warehouse -> warehouse manager -> purchasing -> plant director ->
financial control -> CFO -> CEO. The per-rung amount knobs (thr_<kind>_<stage>)
that let an admin move a rung up the value scale are therefore retired. This
file proves the retirement is real, not cosmetic:

  * no thr_ knob is offered, and saving one is refused as an unknown setting
  * a legacy thr_ row already in the table is IGNORED by routing
  * the live matrix is the fixed ladder, every rung at 0, for both kinds
  * a request routes the full ladder at every value, through the SUBMIT path
  * a department matrix still cannot weaken the floor
  * the settings screen is shown the fixed numbers, and no rung warns

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
        FULL = ["warehouse", "warehouse_manager", "purchasing", "factory_manager",
                "control", "cfo", "ceo"]
        ZERO = {s: 0.0 for s in FULL}

        def live(kind="opex"):
            conn = get_db()
            try:
                return {k: float(v) for k, v in svc.live_matrix(conn, kind).items()}
            finally:
                conn.close()

        def ladder(total, kind="opex", dept="Production"):
            conn = get_db()
            try:
                return svc.dept_ladder(conn, dept, total, kind)
            finally:
                conn.close()

        def put_raw(key, value):
            """A row that reached the table before the knobs were retired."""
            conn = get_db()
            try:
                conn.execute("DELETE FROM proc_settings WHERE key=?", (key,))
                conn.execute("INSERT INTO proc_settings (key, value) VALUES (?,?)",
                             (key, value))
                conn.commit()
            finally:
                conn.close()

        # ---- 1. the knobs are gone ------------------------------------------
        print("\n1. no amount knob is offered")
        chk("no thr_ key in WORKFLOW_SETTINGS",
            not [k for k in C.WORKFLOW_SETTINGS if k.startswith("thr_")])
        ok, msg = svc.set_setting("thr_opex_cfo", "15000", ADMIN)
        chk("saving one is refused as unknown", not ok and msg == "unknown_setting",
            (ok, msg))
        chk("the fixed matrix is every rung at 0, OPEX",
            live("opex") == ZERO, live("opex"))
        chk("...and CAPEX", live("capex") == ZERO, live("capex"))

        # ---- 2. a legacy row cannot move a rung -----------------------------
        print("\n2. a stored amount from before 2026-11-17 is ignored")
        for key, val in (("thr_opex_cfo", "999999999"), ("thr_opex_ceo", "250000"),
                         ("thr_opex_bod", "0"), ("thr_capex_ceo", "1000000")):
            put_raw(key, val)
        chk("the live matrix did not move", live() == ZERO and live("capex") == ZERO)
        for v in (1, 9_999, 600_000, 20_000_000):
            chk("%s routes the full ladder" % format(v, ","), ladder(v) == FULL,
                ladder(v))
            chk("%s CAPEX routes the full ladder" % format(v, ","),
                ladder(v, "capex") == FULL, ladder(v, "capex"))
        chk("a legacy 'Board on every request' row brings back no Board",
            "bod" not in ladder(1))

        # ---- 3. the SUBMIT path, not just the read model --------------------
        print("\n3. the ladder a real request is actually given")
        buyer = {"username": "thr.buyer", "role": "purchasing_manager", "id": 90100}
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
        chk("its signature list is the owner's ladder despite the legacy rows",
            stages == FULL, stages)

        # ---- 4. a department matrix still cannot weaken the floor -----------
        print("\n4. a department matrix is clamped by the fixed floor")
        rows = [{"stage": s, "threshold": 0} for s in C.OPEX_MATRIX]
        ok, msg = svc.set_dept_matrix("Production", rows, ADMIN)
        chk("a matrix at the floor is accepted", ok, msg)
        raised = [{"stage": s, "threshold": (100 if s == "cfo" else 0)}
                  for s in C.OPEX_MATRIX]
        ok, msg = svc.set_dept_matrix("Production", raised, ADMIN)
        chk("raising the CFO to 100 is refused",
            (not ok) and "below_doam_floor" in str(msg), msg)
        chk("the settings screen is shown the fixed numbers",
            {k: float(v) for k, v in svc.effective_thresholds("opex").items()} == ZERO)

        # ---- 5. nothing on the settings screen talks about amounts ----------
        print("\n5. no amount warning is generated")
        for lang in ("en", "ar", "tr"):
            chk("%s: no threshold warning" % lang, T.threshold_warn(lang) == {},
                T.threshold_warn(lang))
            warn = svc.labels(lang)["setting_warn"]
            chk("%s: no thr_ key in setting_warn" % lang,
                not [k for k in warn if k.startswith("thr_")])

    print("\n" + ("RESULT: ALL GREEN" if OK[0] else "RESULT: FAILURES ABOVE"))
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
