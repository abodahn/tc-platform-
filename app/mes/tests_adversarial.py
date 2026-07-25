"""
ADVERSARIAL verification of app/mes — hostile inputs, independent recomputation.

    python app/mes/tests_adversarial.py

Rules this file plays by:
  * every number is recomputed in plain Python straight from the raw rows, never
    by calling the function under test twice;
  * every write is called TWICE to prove the second call is an edit or a refusal;
  * every guard is fed None / "" / negative / non-numeric / absurd / missing-id.
Runs against a throwaway DB in the OS temp dir — never the repo platform.db.
"""
import os, re, sys, tempfile, inspect
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="mes_adv_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                  # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                     # noqa: E402
from app.mes import services as svc                            # noqa: E402
from app.mes.schema import create_and_seed                     # noqa: E402
from app.mes.constants import (SECTIONS, HOUR_SLOTS, SLOT_MINUTES,   # noqa: E402
                               DOWNTIME_REASONS, BUNDLE_STATUS)
from app.mes.i18n_keys import I18N                             # noqa: E402

FAILS = []
U = {"username": "adv"}
TBL = ("mes_hourly", "mes_downtime", "mes_bundles", "mes_bundle_moves")


def ok(name, cond, detail=""):
    d = str(detail)
    if cond:
        print("  PASS  " + name + ("  -> " + d if d else ""))
    else:
        FAILS.append(name)
        print("  FAIL  " + name + "  -> " + d)


def counts(conn):
    return {t: conn.execute("SELECT COUNT(*) AS c FROM %s" % t).fetchone()["c"] for t in TBL}


app = create_app()
with app.app_context():
    from app.db import get_db
    conn = get_db()
    create_and_seed(conn)

    # =================================================== 1. seed idempotency
    c1 = counts(conn); create_and_seed(conn)
    c2 = counts(conn); create_and_seed(conn)
    c3 = counts(conn)
    ok("create_and_seed x3 neither duplicates nor destroys",
       c1 == c2 == c3 and all(v for v in c3.values()), str(c3))

    day = svc.resolve_date()
    lines = svc.list_lines()
    L1 = lines[0]["id"]

    # ============================ 2. arithmetic recomputed from the raw rows
    raw = [dict(r) for r in conn.execute(
        "SELECT * FROM mes_hourly WHERE work_date=?", (day,)).fetchall()]
    dts = [dict(r) for r in conn.execute(
        "SELECT * FROM mes_downtime WHERE work_date=?", (day,)).fetchall()]
    d = svc.dashboard(day)

    for ln in d["lines"]:
        rows = [r for r in raw if r["line_id"] == ln["line_id"]]
        act = sum(r["actual_qty"] for r in rows)
        tgt = sum(r["target_qty"] for r in rows)
        rej = sum(r["reject_qty"] for r in rows)
        earned = sum(r["actual_qty"] * r["smv"] for r in rows)
        opmin = sum(r["operators"] * SLOT_MINUTES for r in rows)
        ideal = sum(r["actual_qty"] * r["smv"] / r["operators"] for r in rows if r["operators"])
        planned = len(rows) * SLOT_MINUTES
        down = sum(x["minutes"] for x in dts if x["line_id"] == ln["line_id"])
        cdown = min(down, planned)
        A = (planned - cdown) / planned * 100 if planned else 0.0
        P = ideal / (planned - cdown) * 100 if (planned - cdown) > 0 else 0.0
        Q = (act - rej) / act * 100 if act else 0.0
        tag = "line %s" % ln["line_id"]
        ok(tag + " actual/target/reject",
           (ln["actual"], ln["target"], ln["reject"]) == (act, tgt, rej),
           "%s vs %s" % ((ln["actual"], ln["target"], ln["reject"]), (act, tgt, rej)))
        ok(tag + " achievement", ln["achievement"] == round(act / tgt * 100, 1), ln["achievement"])
        ok(tag + " efficiency = earned/man-min", ln["efficiency"] == round(earned / opmin * 100, 1),
           "%s vs %s" % (ln["efficiency"], round(earned / opmin * 100, 1)))
        ok(tag + " availability", ln["availability"] == round(A, 1),
           "%s vs %s" % (ln["availability"], round(A, 1)))
        ok(tag + " performance", ln["performance"] == round(P, 1),
           "%s vs %s" % (ln["performance"], round(P, 1)))
        ok(tag + " quality", ln["quality"] == round(Q, 1), "%s vs %s" % (ln["quality"], round(Q, 1)))
        ok(tag + " OEE = A*P*Q/10000 from UNROUNDED parts", ln["oee"] == round(A * P * Q / 10000, 1),
           "%s vs %s" % (ln["oee"], round(A * P * Q / 10000, 1)))
        ok(tag + " A*P/100 == efficiency (constant manning)",
           abs(A * P / 100 - earned / opmin * 100) < 1e-6, round(A * P / 100, 4))
        ok(tag + " reported downtime is the booked figure, not the clamp",
           ln["downtime_min"] == round(down, 1), "%s vs booked %s" % (ln["downtime_min"], down))

    tA = sum(r["target_qty"] for r in raw)
    aA = sum(r["actual_qty"] for r in raw)
    mean_of_lines = round(sum(l["achievement"] for l in d["lines"]) / len(d["lines"]), 1)
    ok("factory totals are recomputed from pieces, not averaged",
       d["t"]["achievement"] == round(aA / tA * 100, 1) and d["t"]["achievement"] != mean_of_lines,
       "%s (mean of lines would be %s)" % (d["t"]["achievement"], mean_of_lines))
    ok("day downtime KPI equals every booked minute",
       d["downtime_day"] == round(sum(x["minutes"] for x in dts), 1), d["downtime_day"])
    ok("downtime_top ranked descending and sums to the day",
       [x["minutes"] for x in d["downtime_top"]] == sorted([x["minutes"] for x in d["downtime_top"]],
                                                           reverse=True)
       and round(sum(x["minutes"] for x in d["downtime_top"]), 1) == d["downtime_day"], d["downtime_top"])

    # ============================================ 3. pure-function edge cases
    ok("achievement guards 0 / None / '' / junk target",
       [svc.achievement(5, x) for x in (0, None, "", "abc", "0")] == [0.0] * 5)
    ok("achievement guards None / junk actual",
       svc.achievement(None, 10) == 0.0 and svc.achievement("x", 10) == 0.0)
    ok("efficiency guards 0 / None / negative man-minutes",
       [svc.efficiency(100, x) for x in (0, None, "", -5)] == [0.0] * 4)
    ok("rag thresholds 95 / 94.9 / 85 / 84.9",
       [svc.rag(x) for x in (95, 94.9, 85, 84.9)] == ["green", "amber", "amber", "red"])
    ok("no target is grey, never a false red", svc.rag_for(0, 0) == "none" and svc.rag_for(0, 10) == "red")
    z = svc.oee_parts(0, 0, 0, 0, 0)
    ok("oee all-zero is 0.0 everywhere, no crash", all(v == 0.0 for v in z.values()), z)
    n = svc.oee_parts(None, None, None, None, None)
    ok("oee tolerates None everywhere", all(v == 0.0 for v in n.values()), n)
    cl = svc.oee_parts(360, 999, 100, 100, 100)
    ok("downtime clamped: availability floored at 0, never negative",
       cl["availability"] == 0.0 and cl["operating_min"] == 0.0 and cl["performance"] == 0.0, cl)
    inf = svc.oee_parts("1e400", "nan", "inf", 10, 10)
    ok("oee refuses inf/nan input", all(isinstance(v, float) and v == v for v in inf.values()), inf)

    # ============================================= 4. save_hourly: guards
    base = {"line_id": L1, "work_date": day, "hour_slot": HOUR_SLOTS[7],
            "target_qty": 30, "actual_qty": 28, "reject_qty": 2, "operators": 10, "smv": 20}
    for name, payload, key in [
            ("no line", dict(base, line_id=999999), "mes.msg.no_line"),
            ("blank line", dict(base, line_id=""), "mes.msg.no_line"),
            ("bad slot", dict(base, hour_slot="99:00-99:00"), "mes.msg.bad_slot"),
            ("blank slot", dict(base, hour_slot=""), "mes.msg.bad_slot"),
            ("junk date", dict(base, work_date="not-a-date"), "mes.msg.bad_date"),
            ("impossible date", dict(base, work_date="2026-13-45"), "mes.msg.bad_date"),
            ("negative actual", dict(base, actual_qty=-5), "mes.msg.negative"),
            ("negative smv", dict(base, smv=-1), "mes.msg.negative"),
            ("absurd qty", dict(base, actual_qty=10 ** 12), "mes.msg.too_big"),
            ("absurd smv", dict(base, smv=99999), "mes.msg.too_big"),
            ("reject > actual", dict(base, reject_qty=99), "mes.msg.reject_gt_actual")]:
        r = svc.save_hourly(payload, U)
        ok("save_hourly refuses %s" % name, r == (False, key), r)
    ok("no refused hour reached the table", counts(conn)["mes_hourly"] == c3["mes_hourly"])

    # ================================= 5. save_hourly: upsert, no double-count
    ok("save_hourly accepts a clean row", svc.save_hourly(base, U) == (True, "mes.msg.saved"))
    n1 = counts(conn)["mes_hourly"]
    ok("save_hourly again (double-click) EDITS, no second row",
       svc.save_hourly(dict(base, actual_qty=31), U) == (True, "mes.msg.saved")
       and counts(conn)["mes_hourly"] == n1, counts(conn)["mes_hourly"])
    cell = conn.execute("SELECT * FROM mes_hourly WHERE line_id=? AND work_date=? AND hour_slot=?",
                        (L1, day, HOUR_SLOTS[7])).fetchone()
    ok("the edit overwrote rather than accumulated", cell["actual_qty"] == 31, cell["actual_qty"])
    ok("non-numeric entry does not 500 the screen",
       svc.save_hourly(dict(base, actual_qty="abc", reject_qty="", operators=None, smv=""), U)
       == (True, "mes.msg.saved"))
    conn.execute("DELETE FROM mes_hourly WHERE line_id=? AND work_date=? AND hour_slot=?",
                 (L1, day, HOUR_SLOTS[7]))
    conn.commit()

    # ================================================ 6. downtime guards
    dbase = {"line_id": L1, "work_date": day, "reason": "power", "minutes": 10}
    for name, payload, key in [
            ("bad reason", dict(dbase, reason="nope"), "mes.msg.bad_reason"),
            ("blank reason", dict(dbase, reason=""), "mes.msg.bad_reason"),
            ("zero minutes", dict(dbase, minutes=0), "mes.msg.bad_minutes"),
            ("negative minutes", dict(dbase, minutes=-30), "mes.msg.bad_minutes"),
            ("junk minutes", dict(dbase, minutes="ten"), "mes.msg.bad_minutes"),
            ("longer than a day", dict(dbase, minutes=99999), "mes.msg.too_big"),
            ("missing line", dict(dbase, line_id=424242), "mes.msg.no_line"),
            ("junk date", dict(dbase, work_date="xx"), "mes.msg.bad_date")]:
        r = svc.add_downtime(payload, U)
        ok("add_downtime refuses %s" % name, r == (False, key), r)
    ok("no refused loss reached the table", counts(conn)["mes_downtime"] == c3["mes_downtime"])

    # A day where the booked loss EXCEEDS the planned time: availability must floor
    # at 0 but the reported minutes must stay the number the supervisor booked.
    OD = "2005-05-05"
    svc.save_hourly(dict(base, work_date=OD, hour_slot=HOUR_SLOTS[0], actual_qty=10,
                         reject_qty=0, target_qty=10), U)
    svc.add_downtime(dict(dbase, work_date=OD, minutes=500), U)
    od = svc.dashboard(OD)
    ok("over-planned downtime floors availability at 0",
       od["lines"][0]["availability"] == 0.0 and od["lines"][0]["oee"] == 0.0, od["lines"][0]["availability"])
    ok("over-planned downtime is REPORTED in full, not truncated to the clamp",
       od["lines"][0]["downtime_min"] == 500.0 and od["t"]["downtime_min"] == 500.0
       and od["downtime_day"] == 500.0,
       "line %s / factory %s / kpi %s" % (od["lines"][0]["downtime_min"],
                                          od["t"]["downtime_min"], od["downtime_day"]))
    conn.execute("DELETE FROM mes_hourly WHERE work_date=?", (OD,))
    conn.execute("DELETE FROM mes_downtime WHERE work_date=?", (OD,))
    conn.commit()

    # ============================================= 7. bundles: creation guards
    for name, payload, key in [
            ("zero qty", {"qty": 0}, "mes.msg.bad_qty"),
            ("negative qty", {"qty": -10}, "mes.msg.bad_qty"),
            ("junk qty", {"qty": "many"}, "mes.msg.bad_qty"),
            ("absurd qty", {"qty": 10 ** 9}, "mes.msg.too_big"),
            ("unknown section", {"qty": 10, "origin_section": "mars"}, "mes.msg.bad_section")]:
        r = svc.create_bundle(payload, U)
        ok("create_bundle refuses %s" % name, r == (False, key), r)

    _, b1 = svc.create_bundle({"qty": 100, "size": "M", "color": "Red"}, U)
    _, b2 = svc.create_bundle({"qty": 100}, U)
    nos = [r["bundle_no"] for r in conn.execute("SELECT bundle_no FROM mes_bundles").fetchall()]
    ok("bundle numbers unique and id-derived", len(nos) == len(set(nos)) and None not in nos, nos[-2:])
    conn.execute("DELETE FROM mes_bundles WHERE id=?", (b2,)); conn.commit()
    _, b3 = svc.create_bundle({"qty": 50}, U)
    nos2 = [r["bundle_no"] for r in conn.execute("SELECT bundle_no FROM mes_bundles").fetchall()]
    ok("bundle numbering survives a delete (no COUNT+1 collision)", len(nos2) == len(set(nos2)), nos2[-1])

    # ============================================= 8. moves: the qty invariant
    for name, args, key in [
            ("unknown bundle", (99999, {"qty": 1, "to_section": "sewing"}), "mes.msg.no_bundle"),
            ("same section", (b1, {"qty": 1, "to_section": "cutting"}), "mes.msg.same_section"),
            ("unknown section", (b1, {"qty": 1, "to_section": "mars"}), "mes.msg.bad_section"),
            ("zero qty", (b1, {"qty": 0, "to_section": "sewing"}), "mes.msg.bad_qty"),
            ("negative qty", (b1, {"qty": -5, "to_section": "sewing"}), "mes.msg.bad_qty"),
            ("junk qty", (b1, {"qty": "lots", "to_section": "sewing"}), "mes.msg.bad_qty"),
            ("more than was cut", (b1, {"qty": 101, "to_section": "sewing"}), "mes.msg.exceeds_bundle")]:
        r = svc.move_bundle(args[0], args[1], U)
        ok("move_bundle refuses %s" % name, r == (False, key), r)

    ok("move 60/100 cutting->sewing", svc.move_bundle(b1, {"qty": 60, "to_section": "sewing"}, U)[0])
    ok("replaying the same 60 from cutting is REFUSED (only 40 stand there)",
       svc.move_bundle(b1, {"qty": 60, "from_section": "cutting", "to_section": "sewing"}, U)
       == (False, "mes.msg.exceeds_bundle"))
    ok("the remaining 40 may still move",
       svc.move_bundle(b1, {"qty": 40, "from_section": "cutting", "to_section": "sewing"}, U)[0])
    ok("cutting is now empty and refuses even 1 piece",
       svc.move_bundle(b1, {"qty": 1, "from_section": "cutting", "to_section": "sewing"}, U)
       == (False, "mes.msg.exceeds_bundle"))
    out = conn.execute("SELECT COALESCE(SUM(qty),0) AS q FROM mes_bundle_moves "
                       "WHERE bundle_id=? AND from_section='cutting'", (b1,)).fetchone()["q"]
    ok("everything that ever left the origin is capped at the cut qty", out == 100, out)

    for s in ("washing", "finishing", "packing", "dispatch"):
        svc.move_bundle(b1, {"qty": 100, "to_section": s}, U)
    st = conn.execute("SELECT status FROM mes_bundles WHERE id=?", (b1,)).fetchone()["status"]
    ok("whole qty at the last section marks the bundle completed", st == "completed", st)
    ok("a shipped bundle cannot ship twice — nothing is left upstream",
       svc.move_bundle(b1, {"qty": 1, "from_section": "packing", "to_section": "dispatch"}, U)
       == (False, "mes.msg.exceeds_bundle"))

    w = svc.wip_by_section()
    live = conn.execute("SELECT COALESCE(SUM(qty),0) AS q FROM mes_bundles "
                        "WHERE status!='rejected'").fetchone()["q"]
    ok("every non-rejected piece stands in exactly one section",
       sum(r["qty"] for r in w["rows"]) == live, "%s vs %s" % (sum(r["qty"] for r in w["rows"]), live))
    ok("no section balance is negative", all(r["qty"] >= 0 for r in w["rows"]), w["rows"])
    ok("WIP total excludes finished goods at the last section",
       w["wip_units"] == sum(r["qty"] for r in w["rows"] if r["section"] != SECTIONS[-1]), w["wip_units"])

    # ============================================= 9. status transitions
    ok("completed can never be declared by hand",
       svc.set_bundle_status(b3, "completed", U) == (False, "mes.msg.status_ledger"))
    ok("unknown status refused", svc.set_bundle_status(b3, "shipped", U) == (False, "mes.msg.bad_status"))
    ok("status on a missing bundle refused",
       svc.set_bundle_status(88888, "rejected", U) == (False, "mes.msg.no_bundle"))
    before_wip = svc.wip_by_section()["wip_units"]
    ok("reject a bundle", svc.set_bundle_status(b3, "rejected", U)[0])
    ok("rejecting twice is a harmless no-op, scrap leaves WIP exactly once",
       svc.set_bundle_status(b3, "rejected", U)[0]
       and svc.wip_by_section()["wip_units"] == before_wip - 50, svc.wip_by_section()["wip_units"])
    ok("a rejected bundle cannot be scanned",
       svc.move_bundle(b3, {"qty": 1, "to_section": "sewing"}, U) == (False, "mes.msg.bundle_rejected"))

    # legacy NULL qty must not 500 the scan gun
    conn.execute("INSERT INTO mes_bundles (bundle_no,qty,origin_section,section,status) "
                 "VALUES ('BDL-NULL',NULL,'cutting','cutting','created')")
    conn.commit()
    bn = conn.execute("SELECT id FROM mes_bundles WHERE bundle_no='BDL-NULL'").fetchone()["id"]
    try:
        r = svc.move_bundle(bn, {"qty": 1, "to_section": "sewing"}, U)
        ok("a NULL-qty legacy bundle is refused, not a 500", r == (False, "mes.msg.exceeds_bundle"), r)
    except Exception as e:
        ok("a NULL-qty legacy bundle is refused, not a 500", False, "%s: %s" % (type(e).__name__, e))
    conn.execute("DELETE FROM mes_bundles WHERE bundle_no='BDL-NULL'"); conn.commit()

    # ============================================= 10. reconciliation
    for r in svc.order_reconciliation():
        if r["order_id"] is None:
            cut = conn.execute("SELECT COALESCE(SUM(qty),0) AS q FROM mes_bundles "
                               "WHERE status!='rejected' AND order_id IS NULL").fetchone()["q"]
        else:
            cut = conn.execute("SELECT COALESCE(SUM(qty),0) AS q FROM mes_bundles "
                               "WHERE status!='rejected' AND order_id=?", (r["order_id"],)).fetchone()["q"]
        ok("recon cut for order %s" % r["order_id"], r["cut"] == cut, "%s vs %s" % (r["cut"], cut))
        ok("recon balance = cut - sewn", r["balance"] == r["cut"] - r["sewn"], r)

    # ============================================= 11. alert sweep idempotency
    conn.execute("DELETE FROM notifications WHERE module='mes'"); conn.commit()
    svc.alert_sweep(day)
    a1 = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='mes'").fetchone()["c"]
    svc.alert_sweep(day); svc.alert_sweep(day)
    a2 = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='mes'").fetchone()["c"]
    ok("alert_sweep x3 raises each alert exactly once", a1 == a2 and a1 > 0, "%s == %s" % (a1, a2))
    reds = [r for r in raw if r["target_qty"] and round(r["actual_qty"] / r["target_qty"] * 100, 1) < 85]
    warn = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='mes' "
                        "AND severity='warning'").fetchone()["c"]
    crit = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='mes' "
                        "AND severity='critical'").fetchone()["c"]
    exp_crit = len({x["line_id"] for x in dts
                    if sum(y["minutes"] for y in dts if y["line_id"] == x["line_id"]) >= 60})
    ok("one warning per red hour", warn == len(reds), "%s vs %s" % (warn, len(reds)))
    ok("one critical per bleeding line", crit == exp_crit, "%s vs %s" % (crit, exp_crit))
    svc.alert_sweep("garbage-date"); svc.alert_sweep(None)
    ok("alert_sweep never raises on junk", True)

    # ============================================= 12. empty / missing data
    ok("line_detail on a missing line is None (404 upstream)", svc.line_detail(987654) is None)
    ld = svc.line_detail(L1, "2001-01-01")
    ok("line_detail on a day with nothing renders zeros",
       ld and ld["stat"]["oee"] == 0.0 and ld["hours"] == [], ld["stat"]["oee"])
    conn.execute("INSERT INTO mes_downtime (line_id,work_date,reason,minutes,created_by,created_at) "
                 "VALUES (?, '2001-01-01','power',120,'adv','x')", (L1,))
    conn.commit()
    ld = svc.line_detail(L1, "2001-01-01")
    ok("downtime with no hourly row is still reported on the line page",
       ld["stat"]["downtime_min"] == 120.0 and ld["losses"][0]["minutes"] == 120.0,
       ld["stat"]["downtime_min"])
    conn.execute("DELETE FROM mes_downtime WHERE work_date='2001-01-01'"); conn.commit()

    dd = svc.dashboard("2001-01-01")
    ok("dashboard on an empty day is all zeros, no crash",
       dd["t"]["actual"] == 0 and dd["t"]["oee"] == 0.0 and dd["lines"] == [])
    ok("resolve_date refuses junk and falls back to a real day",
       re.match(r"^\d{4}-\d{2}-\d{2}$", svc.resolve_date("not-a-date")) is not None,
       svc.resolve_date("not-a-date"))
    ok("hourly_board on a junk date does not explode",
       isinstance(svc.hourly_board("<script>alert(1)</script>")["lines"], list))
    ok("board grid only ever shows real hour slots",
       all(set(l["cells"]) <= set(HOUR_SLOTS) for l in svc.hourly_board(day)["lines"]))

    # ================================================================================
    # 13. REGRESSIONS — every defect found by the verification pass, each one first
    #     reproduced against the original code, then locked down here.
    # ================================================================================
    from datetime import date as _date, timedelta as _td          # noqa: E402
    TODAY = str(_date.today())
    L2 = [r["line_id"] for r in conn.execute(
        "SELECT DISTINCT line_id FROM mes_hourly WHERE work_date=? ORDER BY line_id",
        (day,)).fetchall()][1]

    # --- the entry form must never pre-fill a day that is already closed ----------
    # resolve_date() falls back to the last day WITH production. Feeding that to the
    # entry form meant the first save of a new shift landed on the UNIQUE
    # (line, date, hour) cell of an OLDER day and overwrote its actuals.
    conn.execute("UPDATE mes_hourly SET work_date=?", (str(_date.today() - _td(days=3)),))
    conn.commit()
    ok("dashboards still fall back to the last day with production",
       svc.resolve_date(None) != TODAY, svc.resolve_date(None))
    ok("the ENTRY form defaults to today, never to a closed day",
       svc.entry_date(None) == TODAY, svc.entry_date(None))
    conn.execute("UPDATE mes_hourly SET work_date=?", (day,))
    conn.commit()
    ok("an explicitly requested day is still honoured by the entry form",
       svc.entry_date("2026-01-02") == "2026-01-02" and svc.entry_date("junk") == TODAY)

    # --- one line's mistyped downtime must not zero the whole factory -------------
    FD = "2026-01-05"
    hb = {"work_date": FD, "target_qty": 30, "actual_qty": 30, "reject_qty": 0,
          "operators": 10, "smv": 2.0}
    svc.save_hourly(dict(hb, line_id=L1, hour_slot=HOUR_SLOTS[0]), U)
    for i in range(1, 7):
        svc.save_hourly(dict(hb, line_id=L2, hour_slot=HOUR_SLOTS[i]), U)
    svc.add_downtime({"line_id": L1, "work_date": FD, "reason": "power", "minutes": 500}, U)
    fd = svc.dashboard(FD)
    # L1 was planned for 60 min and booked 500: it can only lose its own 60.
    ok("factory availability clamps downtime PER LINE, not on the sum",
       fd["t"]["availability"] == round((420 - 60) / 420 * 100, 1),
       "%s (expected 85.7; summing raw booked minutes gives 0.0)" % fd["t"]["availability"])
    ok("the factory still REPORTS every booked minute", fd["t"]["downtime_min"] == 500.0,
       fd["t"]["downtime_min"])

    # --- an unmanned hour cannot manufacture efficiency ---------------------------
    EF = "2026-02-02"
    for i in range(3):
        svc.save_hourly(dict(hb, work_date=EF, line_id=L1, hour_slot=HOUR_SLOTS[i], smv=20.0), U)
    svc.save_hourly(dict(hb, work_date=EF, line_id=L1, hour_slot=HOUR_SLOTS[3], smv=20.0,
                         operators=0), U)
    ef = svc.dashboard(EF)["lines"][0]
    # 3 manned hours x 30 pcs x 20 SMV = 1800 earned over 3 x 10 x 60 = 1800 man-min.
    # Counting the unmanned hour's pieces in the numerator alone read 133.3%.
    ok("an hour with no operators earns nothing (efficiency cannot exceed reality)",
       ef["efficiency"] == 100.0 and ef["earned_min"] == 1800.0 and ef["operator_min"] == 1800.0,
       "eff %s earned %s man-min %s" % (ef["efficiency"], ef["earned_min"], ef["operator_min"]))

    # --- the RAG threshold is applied to the unrounded ratio ----------------------
    ok("94.96% is amber, not green (rounding must not cross the threshold)",
       svc.achievement(9496, 10000) == 95.0 and svc.rag_for(9496, 10000) == "amber")
    ok("84.95% is red, not amber",
       svc.achievement(8495, 10000) == 85.0 and svc.rag_for(8495, 10000) == "red")
    ok("an exact 95% / 85% still sits on the green / amber side",
       svc.rag_for(95, 100) == "green" and svc.rag_for(85, 100) == "amber")

    # --- a typo'd year must not be bookable, and must never pin the dashboards ----
    ok("save_hourly refuses a work_date years in the future",
       svc.save_hourly(dict(hb, line_id=L1, hour_slot=HOUR_SLOTS[0], work_date="2062-07-25"), U)
       == (False, "mes.msg.future_date"))
    ok("add_downtime refuses a work_date years in the future",
       svc.add_downtime({"line_id": L1, "work_date": "2062-07-25", "reason": "power",
                         "minutes": 5}, U) == (False, "mes.msg.future_date"))
    conn.execute("INSERT INTO mes_hourly (line_id,work_date,hour_slot,target_qty,actual_qty,"
                 "operators,smv) VALUES (?,?,?,?,?,?,?)",
                 (L1, "2062-07-25", HOUR_SLOTS[0], 10, 10, 5, 5))
    conn.execute("DELETE FROM mes_hourly WHERE work_date=?", (day,))
    conn.commit()
    ok("a legacy future row never becomes the fallback day",
       svc.resolve_date(None) <= TODAY, svc.resolve_date(None))
    conn.execute("DELETE FROM mes_hourly WHERE work_date='2062-07-25'")
    for r in raw:
        conn.execute("INSERT INTO mes_hourly (line_id,order_id,work_date,hour_slot,target_qty,"
                     "actual_qty,reject_qty,operators,smv,created_by,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (r["line_id"], r["order_id"], r["work_date"], r["hour_slot"], r["target_qty"],
                      r["actual_qty"], r["reject_qty"], r["operators"], r["smv"], r["created_by"],
                      r["created_at"]))
    conn.commit()

    # --- the headline downtime KPI must equal the table under it -----------------
    KD = "2026-05-05"
    svc.save_hourly(dict(hb, line_id=L1, work_date=KD, hour_slot=HOUR_SLOTS[0]), U)
    svc.add_downtime({"line_id": L2, "work_date": KD, "reason": "power", "minutes": 90}, U)
    kd = svc.dashboard(KD)
    ok("a line that lost the day without producing still appears in the table",
       kd["downtime_day"] == sum(l["downtime_min"] for l in kd["lines"]) == kd["t"]["downtime_min"] == 90.0,
       "KPI %s / table %s / totals %s" % (kd["downtime_day"],
                                          sum(l["downtime_min"] for l in kd["lines"]),
                                          kd["t"]["downtime_min"]))

    # --- progress is earned in the ledger in BOTH directions ---------------------
    _, bc = svc.create_bundle({"qty": 10}, U)
    for s in SECTIONS[1:]:
        svc.move_bundle(bc, {"qty": 10, "to_section": s}, U)
    ok("the whole qty at the last section marks it completed",
       conn.execute("SELECT status FROM mes_bundles WHERE id=?", (bc,)).fetchone()["status"]
       == "completed")
    ok("a completed bundle cannot be hand-downgraded to 'created'",
       svc.set_bundle_status(bc, "created", U) == (False, "mes.msg.status_ledger")
       and conn.execute("SELECT status FROM mes_bundles WHERE id=?",
                        (bc,)).fetchone()["status"] == "completed")
    svc.set_bundle_status(bc, "rejected", U)
    svc.set_bundle_status(bc, "created", U)
    ok("un-rejecting restores the status the SCANS imply, not the one asked for",
       conn.execute("SELECT status FROM mes_bundles WHERE id=?", (bc,)).fetchone()["status"]
       == "completed")

    # --- an order id that does not exist is refused, not stored as a dead link ----
    real_order = conn.execute("SELECT id FROM ord_orders LIMIT 1").fetchone()["id"]
    ok("save_hourly refuses a ghost order",
       svc.save_hourly(dict(hb, line_id=L1, work_date="2026-06-06", hour_slot=HOUR_SLOTS[0],
                            order_id=987654), U) == (False, "mes.msg.no_order"))
    ok("create_bundle refuses a ghost order",
       svc.create_bundle({"qty": 5, "order_id": 987654}, U) == (False, "mes.msg.no_order"))
    ok("a real order is still accepted",
       svc.save_hourly(dict(hb, line_id=L1, work_date="2026-06-06", hour_slot=HOUR_SLOTS[0],
                            order_id=real_order), U) == (True, "mes.msg.saved"))
    ok("an unassigned (blank) order is still accepted",
       svc.create_bundle({"qty": 5, "order_id": ""}, U)[0])

    # --- an id wider than an INTEGER column is a 404, never a 500 -----------------
    # Werkzeug's <int:> converter matches a number of ANY length, so /mes/line/<30
    # digits> reached the driver and raised OverflowError straight out of a route.
    for label, fn, expect in [
            ("save_hourly line_id", lambda: svc.save_hourly(
                dict(hb, line_id="1e30", hour_slot=HOUR_SLOTS[0]), U), (False, "mes.msg.no_line")),
            ("save_hourly order_id", lambda: svc.save_hourly(
                dict(hb, line_id=L1, work_date="2026-06-07", hour_slot=HOUR_SLOTS[0],
                     order_id=10 ** 30), U), (False, "mes.msg.no_order")),
            ("add_downtime line_id", lambda: svc.add_downtime(
                {"line_id": 10 ** 30, "work_date": TODAY, "reason": "power", "minutes": 5}, U),
             (False, "mes.msg.no_line")),
            ("create_bundle order_id", lambda: svc.create_bundle(
                {"qty": 5, "order_id": 10 ** 30}, U), (False, "mes.msg.no_order")),
            ("line_detail", lambda: svc.line_detail(10 ** 30), None),
            ("move_bundle", lambda: svc.move_bundle(
                10 ** 30, {"qty": 1, "to_section": "sewing"}, U), (False, "mes.msg.no_bundle")),
            ("set_bundle_status", lambda: svc.set_bundle_status(10 ** 30, "rejected", U),
             (False, "mes.msg.no_bundle")),
            ("list_bundles order filter", lambda: svc.list_bundles(order_id=10 ** 30), [])]:
        try:
            got = fn()
            ok("a 30-digit id is refused cleanly: %s" % label, got == expect, got)
        except Exception as e:
            ok("a 30-digit id is refused cleanly: %s" % label, False,
               "%s: %s" % (type(e).__name__, e))

    conn.close()

# ================================================= 13. static: routes, SQL, i18n
src_svc = (REPO / "app/mes/services.py").read_text(encoding="utf-8")
src_rt = (REPO / "app/routes/mes.py").read_text(encoding="utf-8")

ok("no f-string / % interpolation of values into SQL in services",
   not re.search(r'execute\(\s*(f["\']|["\'][^"\']*["\']\s*%)', src_svc))

missing, get_writes = [], []
for m in re.finditer(r'@bp\.route\((.*?)\)\n((?:@[\w_]+\([^\n]*\)\n|@[\w_]+\n)*)def (\w+)', src_rt):
    route, decs, name = m.groups()
    body = src_rt.split("def %s(" % name, 1)[1].split("\n@bp.route")[0]
    if "login_required" not in decs or "permission_required" not in decs:
        missing.append(name)
    writes = any(x in body for x in ("svc.save_", "svc.add_", "svc.create_", "svc.move_", "svc.set_"))
    if writes and "POST" not in route:
        get_writes.append(name)
ok("every mes route is @login_required + @permission_required", not missing and len(
    re.findall(r"@bp\.route", src_rt)) == 10, missing or "10 routes")
ok("every state-changing route is POST-only", not get_writes, get_writes)
ok("id routes are typed <int:> so a hostile id can never reach SQL as text",
   not re.search(r"<(?!int:)[^>]+>", src_rt))

# i18n: every data-i18n key the templates can emit must exist in the map
keys, dynamic = set(), set()
for p in sorted((REPO / "app/templates/mes").glob("*.html")):
    for k in re.findall(r'data-i18n="([^"]+)"', p.read_text(encoding="utf-8")):
        (dynamic if "{{" in k else keys).add(k)
for k in dynamic:
    pre = k.split("{{")[0]
    pool = (DOWNTIME_REASONS if pre == "mes.reason." else
            SECTIONS if pre == "mes.section." else
            BUNDLE_STATUS if pre == "mes.status." else [])
    ok("dynamic key family %s* is enumerable" % pre, bool(pool), k)
    keys |= {pre + v for v in pool}
flash_keys = set(re.findall(r'"(mes\.msg\.[a-z_]+)"', src_svc))
gap = sorted((keys | flash_keys) - set(I18N))
ok("no template or flash key renders as raw text", not gap, gap)
ok("every entry is a real 3-tuple of non-empty strings",
   all(len(v) == 3 and all(isinstance(x, str) and x.strip() for x in v) for v in I18N.values()))
# Acronyms that are identical in every language (OEE, "P %") are not lazy copies,
# so only real prose is required to differ.
eng = [k for k, v in I18N.items() if len(v[0]) > 6 and (v[1] == v[0] or v[2] == v[0])]
ok("ar/tr are real translations, not English copies", not eng, eng[:5])

import jinja2                                                   # noqa: E402
env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(REPO / "app/templates")))
for p in sorted((REPO / "app/templates/mes").glob("*.html")):
    try:
        env.parse(p.read_text(encoding="utf-8"), filename=p.name)
        ok("template parses: %s" % p.name, True)
    except Exception as e:
        ok("template parses: %s" % p.name, False, str(e))

# ================================================= 14. the pages as a USER gets them
# parse() proves only that the Jinja compiles. It cannot see a route handing the
# template the wrong shape — /mes/bundles was passing the whole wip_by_section()
# dict where the template iterates section rows, so Jinja walked the dict KEYS and
# drew two blank tiles labelled with the raw key "mes.section.". Only a real render
# through the real route catches that, so every page is fetched over HTTP here.
from app.routes.mes import bp as mes_bp                          # noqa: E402
if "mes" not in app.blueprints:          # the orchestrator may already have spliced it
    app.register_blueprint(mes_bp)
with app.app_context():
    from app.db import get_db
    conn = get_db()
    u = conn.execute("SELECT id, session_epoch FROM users WHERE username='admin'").fetchone()
    lid = conn.execute("SELECT line_id FROM mes_hourly LIMIT 1").fetchone()["line_id"]
    bid = conn.execute("SELECT id FROM mes_bundles LIMIT 1").fetchone()["id"]
    conn.close()

client = app.test_client()
with client.session_transaction() as s:
    s["uid"] = u["id"]
    s["ep"] = u["session_epoch"] or 0
for path in ["/mes/", "/mes/board", "/mes/entry", "/mes/bundles",
             "/mes/line/%s" % lid, "/mes/line/99999999999999999999999999",
             "/mes/bundles?status=rejected", "/mes/?date=not-a-date"]:
    r = client.get(path)
    want = 404 if "9999999999" in path else 200
    ok("GET %s -> %s" % (path, want), r.status_code == want, r.status_code)

body = client.get("/mes/bundles").get_data(as_text=True)
ok("no template renders a truncated/blank i18n key",
   not re.search(r'data-i18n="[a-z.]*\."', body) and 'data-i18n="mes.section."' not in body,
   re.findall(r'data-i18n="[a-z.]*\."', body)[:3])
strip = body.split('<div class="sf-kpis">')[1].split("</div>\n\n")[0]
ok("the WIP strip shows one tile per real section, with numbers",
   strip.count('class="sf-kpi"') == len(SECTIONS)
   and all(('mes.section.%s' % s) in strip for s in SECTIONS),
   "%s tiles" % strip.count('class="sf-kpi"'))
ok("every rendered page carries a CSRF token in each form",
   body.count("<form") == body.count('name="_csrf"') + body.count('method="get"'),
   "%s forms / %s tokens" % (body.count("<form"), body.count('name="_csrf"')))

# ================================================================================
# 15. SECOND ADVERSARIAL PASS — probes the first pass did not make. Each block
#     was written to BREAK a specific claim in the module's own docstrings.
# ================================================================================
with app.app_context():
    from app.db import get_db                                    # noqa: E402
    conn = get_db()

    def one(sql, args=()):
        return conn.execute(sql, args).fetchone()[0]

    L1 = svc.list_lines()[0]["id"]
    HB = {"line_id": L1, "target_qty": 10000, "reject_qty": 0, "operators": 10, "smv": 2.0}

    # --- 15a. the bell must use the SAME threshold arithmetic as the board --------
    # rag_for() deliberately tests the UNROUNDED ratio (84.95% is red, not amber).
    # alert_sweep fed rag() the ROUNDED achievement, so an hour the board paints RED
    # was never escalated: 8495/10000 = 84.95% -> round -> 85.0 -> "amber" -> no bell.
    RD = "2026-03-03"
    conn.execute("DELETE FROM mes_hourly WHERE work_date=?", (RD,))
    conn.execute("DELETE FROM notifications WHERE module='mes'")
    conn.commit()
    svc.save_hourly(dict(HB, work_date=RD, hour_slot=HOUR_SLOTS[0], actual_qty=8495), U)
    svc.save_hourly(dict(HB, work_date=RD, hour_slot=HOUR_SLOTS[1], actual_qty=8505), U)
    board_rag = {c["actual"]: c["rag"]
                 for c in svc.hourly_board(RD)["lines"][0]["cells"].values()}
    svc.alert_sweep(RD)
    alerts = one("SELECT COUNT(*) FROM notifications WHERE module='mes' AND severity='warning'")
    ok("the board paints 84.95% red and 85.05% amber",
       board_rag == {8495: "red", 8505: "amber"}, board_rag)
    ok("every hour the board calls RED reaches the bell (no rounding gap)",
       alerts == 1, "%s alert(s) for 1 red hour" % alerts)

    # --- 15b. losing the upsert race must EDIT the cell, never 500 ----------------
    # "Re-submitting the same hour EDITS it" is the module's headline anti-double-count
    # claim, but the check-then-insert is not atomic: with two gunicorn workers a
    # double-clicked Save has both requests SELECT (nothing), then both INSERT — the
    # loser hit the UNIQUE index and raised straight out of the route as a 500.
    class _Racer:
        """Real connection that lets a COMPETING writer create the cell in the gap
        between save_hourly's existence check and its INSERT."""
        def __init__(self, real):
            self._c, self.fired = real, False

        def execute(self, sql, params=()):
            if not self.fired and "FROM mes_hourly WHERE line_id" in sql:
                self.fired = True
                res = self._c.execute(sql, params)
                rival = _real_get_db()
                try:
                    rival.execute(
                        "INSERT INTO mes_hourly (line_id,work_date,hour_slot,target_qty,"
                        "actual_qty,reject_qty,operators,smv) VALUES (?,?,?,?,?,?,?,?)",
                        (L1, RC, HOUR_SLOTS[0], 10, 10, 0, 5, 1.0))
                    rival.commit()
                finally:
                    rival.close()
                return res
            return self._c.execute(sql, params)

        def __getattr__(self, n):
            return getattr(self._c, n)

    RC = "2026-03-04"
    _real_get_db = svc.get_db
    svc.get_db = lambda: _Racer(_real_get_db())
    try:
        got = svc.save_hourly(dict(HB, work_date=RC, hour_slot=HOUR_SLOTS[0], actual_qty=77), U)
    except Exception as e:
        got = "%s: %s" % (type(e).__name__, e)
    finally:
        svc.get_db = _real_get_db
    rows = one("SELECT COUNT(*) FROM mes_hourly WHERE work_date=? AND hour_slot=?",
               (RC, HOUR_SLOTS[0]))
    final = one("SELECT actual_qty FROM mes_hourly WHERE work_date=? AND hour_slot=?",
                (RC, HOUR_SLOTS[0]))
    ok("losing the upsert race is an EDIT, not a 500", got == (True, "mes.msg.saved"), got)
    ok("the race left exactly one cell, holding the later value",
       rows == 1 and final == 77, "%s row(s), actual %s" % (rows, final))

    # --- 15c. cut vs sewn must survive a REWORK scan ------------------------------
    # Every section-to-section scan is allowed, so finishing -> sewing (rework) is one
    # click. Counting "sewn" as every scan INTO sewing counted those pieces twice and
    # printed Sewn > Cut with a NEGATIVE balance on the dashboard.
    conn.execute("DELETE FROM mes_bundle_moves"); conn.execute("DELETE FROM mes_bundles")
    conn.commit()
    real_order = one("SELECT id FROM ord_orders LIMIT 1")
    _, rb = svc.create_bundle({"qty": 100, "order_id": real_order}, U)
    for frm, to in (("cutting", "sewing"), ("sewing", "finishing"),
                    ("finishing", "sewing")):          # <- the rework return
        ok("scan %s -> %s accepted" % (frm, to),
           svc.move_bundle(rb, {"qty": 100, "from_section": frm, "to_section": to}, U)[0])
    rec = [r for r in svc.order_reconciliation() if r["order_id"] == real_order][0]
    ok("a rework scan cannot make sewn exceed cut",
       rec["sewn"] <= rec["cut"], "cut %s / sewn %s" % (rec["cut"], rec["sewn"]))
    ok("the cut-room balance is never negative", rec["balance"] >= 0, rec)
    ok("balance is exactly the pieces still standing in the cut room",
       rec["balance"] == [w["qty"] for w in svc.wip_by_section()["rows"]
                          if w["section"] == "cutting"][0], rec)
    # and conservation still holds after the loop
    ok("a rework loop conserves the cut quantity",
       sum(w["qty"] for w in svc.wip_by_section()["rows"]) == 100,
       svc.wip_by_section()["rows"])
    svc.move_bundle(rb, {"qty": 100, "from_section": "sewing", "to_section": "cutting"}, U)
    rec = [r for r in svc.order_reconciliation() if r["order_id"] == real_order][0]
    ok("pieces sent back to the cut room stop counting as sewn",
       (rec["cut"], rec["sewn"], rec["balance"]) == (100, 0, 100), rec)

    # --- 15d. escaping: notes and bundle text are operator-supplied ---------------
    XS = "<script>alert(1)</script>"
    svc.save_hourly(dict(HB, work_date=RD, hour_slot=HOUR_SLOTS[2], actual_qty=1,
                         notes=XS), U)
    svc.create_bundle({"qty": 5, "size": XS, "color": '" onmouseover="x'}, U)

    # --- 15e. a stat block a template reads must never be missing a key -----------
    ld = svc.line_detail(L1, "1999-09-09")
    need = ("availability", "performance", "quality", "oee", "efficiency",
            "planned_min", "downtime_min", "operating_min", "earned_min")
    ok("line page KPIs exist even on a day with no data",
       all(k in ld["stat"] for k in need), sorted(set(need) - set(ld["stat"])))

    # 15f (RBAC) runs OUTSIDE this app context: current_user() caches the user on
    # flask.g, and a test client request re-uses an already-pushed app context — the
    # admin identity from section 14 would leak into it and every check would pass.
    conn.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                 "session_epoch) VALUES ('mes_nobody','x','No One','operator',1,0)")
    conn.commit()
    NOBODY = one("SELECT id FROM users WHERE username='mes_nobody'")

    # --- 15g. KNOWN, PINNED behaviour (documented limits, asserted so a future
    #          change is visible rather than silent) --------------------------------
    d0 = one("SELECT COUNT(*) FROM mes_downtime WHERE work_date='2026-04-04'")
    for _ in range(2):
        svc.add_downtime({"line_id": L1, "work_date": "2026-04-04",
                          "reason": "power", "minutes": 30}, U)
    ok("PINNED: downtime is an append-only event log: a double-click books it twice",
       one("SELECT COUNT(*) FROM mes_downtime WHERE work_date='2026-04-04'") == d0 + 2)
    ZE = "2026-04-05"
    for i in range(3):
        svc.save_hourly(dict(HB, work_date=ZE, hour_slot=HOUR_SLOTS[i], target_qty=30,
                             actual_qty=30, smv=20.0), U)
    svc.save_hourly(dict(HB, work_date=ZE, hour_slot=HOUR_SLOTS[3], target_qty=30,
                         actual_qty=30, smv=20.0, operators=0), U)
    z = svc.dashboard(ZE)["lines"][0]
    ok("PINNED: an unmanned hour still burns planned time, so A x P < efficiency",
       (z["efficiency"], z["availability"], z["performance"]) == (100.0, 100.0, 75.0),
       "eff %s / A %s / P %s" % (z["efficiency"], z["availability"], z["performance"]))

    # --- 15h. seeding again over LIVE data must not touch it ---------------------
    snap = {t: one("SELECT COUNT(*) FROM %s" % t) for t in TBL}
    live = one("SELECT COALESCE(SUM(actual_qty),0) FROM mes_hourly")
    create_and_seed(conn); create_and_seed(conn)
    ok("create_and_seed over a live, user-populated module changes nothing",
       {t: one("SELECT COUNT(*) FROM %s" % t) for t in TBL} == snap
       and one("SELECT COALESCE(SUM(actual_qty),0) FROM mes_hourly") == live, snap)
    HOURS_BEFORE = one("SELECT COUNT(*) FROM mes_hourly")
    conn.close()

# --- 15d/15f over HTTP, with NO app context pushed (see the note above) -----------
for path in ("/mes/line/1?date=2026-03-03", "/mes/bundles"):
    html = client.get(path).get_data(as_text=True)
    ok("operator text is escaped on %s" % path,
       "<script>alert(1)</script>" not in html and 'onmouseover="x' not in html)

c2 = app.test_client()
with c2.session_transaction() as s:
    s["uid"] = NOBODY
    s["ep"] = 0
    tok = s["_csrf_token"] = "t" * 16
codes = {p: c2.get(p).status_code for p in ("/mes/", "/mes/board", "/mes/entry", "/mes/bundles")}
ok("a user without mes perms is refused every page (403)", set(codes.values()) == {403}, codes)
posts = {
    "/mes/hourly": {"line_id": 1, "work_date": "2026-03-03", "hour_slot": HOUR_SLOTS[0],
                    "target_qty": 1, "actual_qty": 1, "operators": 1, "smv": 1},
    "/mes/downtime": {"line_id": 1, "work_date": "2026-03-03", "reason": "power", "minutes": 5},
    "/mes/bundles": {"qty": 5},
    "/mes/bundles/1/move": {"qty": 1, "to_section": "sewing"},
    "/mes/bundles/1/status": {"status": "rejected"},
}
pcodes = {p: c2.post(p, data=dict(d, _csrf=tok)).status_code for p, d in posts.items()}
ok("a user without mes_entry cannot POST anything (403 with a valid CSRF token)",
   set(pcodes.values()) == {403}, pcodes)
with app.app_context():
    from app.db import get_db                                    # noqa: E402
    conn = get_db()
    after = conn.execute("SELECT COUNT(*) AS c FROM mes_hourly").fetchone()["c"]
    conn.close()
ok("nothing that user posted reached the tables", after == HOURS_BEFORE,
   "%s vs %s" % (after, HOURS_BEFORE))

print("\n%s  (%d failure(s))" % ("ALL GREEN" if not FAILS else "FAILURES: " + " | ".join(FAILS), len(FAILS)))
sys.exit(1 if FAILS else 0)
