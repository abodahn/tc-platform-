"""
MES self-test — service + schema layer against an isolated throwaway database.

Run:  python app/mes/tests_selftest.py
Covers the arithmetic against hand-computed OEE/efficiency numbers, the two
quantity invariants (rejects <= actual; a bundle never passes more than it holds),
idempotent seeding, idempotent alerts, and the negative paths (zero, None, blank,
non-numeric, missing line, division by zero, double-apply).
"""
import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="mes_"))
os.chdir(TMP)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                    # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                       # noqa: E402

OK = []


def check(name, cond, detail=""):
    OK.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  -> " + str(detail)) if detail else ""))


def main():
    app = create_app()
    with app.app_context():
        from app.db import get_db
        from app.mes.schema import create_and_seed
        from app.mes import services as svc
        from app.mes.constants import SECTIONS

        conn = get_db()
        create_and_seed(conn)
        n1 = conn.execute("SELECT COUNT(*) AS c FROM mes_hourly").fetchone()["c"]
        b1 = conn.execute("SELECT COUNT(*) AS c FROM mes_bundles").fetchone()["c"]
        create_and_seed(conn)                    # every boot, forever
        n2 = conn.execute("SELECT COUNT(*) AS c FROM mes_hourly").fetchone()["c"]
        b2 = conn.execute("SELECT COUNT(*) AS c FROM mes_bundles").fetchone()["c"]
        conn.commit()
        check("seed is idempotent (hourly %d==%d, bundles %d==%d)" % (n1, n2, b1, b2),
              n1 == n2 == 12 and b1 == b2 == 6)
        conn.close()

        # ---- pure arithmetic ------------------------------------------------
        check("achievement 167/180 = 92.8", svc.achievement(167, 180) == 92.8,
              svc.achievement(167, 180))
        check("achievement guards zero/None/blank target",
              svc.achievement(5, 0) == 0.0 and svc.achievement(5, None) == 0.0
              and svc.achievement(5, "") == 0.0 and svc.achievement("abc", "x") == 0.0)
        check("rag thresholds 95/94.9/85/84.9",
              [svc.rag(95), svc.rag(94.9), svc.rag(85), svc.rag(84.9)]
              == ["green", "amber", "amber", "red"])
        check("efficiency 4091.5/5040 = 81.2", svc.efficiency(4091.5, 5040) == 81.2,
              svc.efficiency(4091.5, 5040))
        check("efficiency guards zero man-minutes", svc.efficiency(100, 0) == 0.0)

        # Hand-computed line A: 6 h x 60 = 360 planned, 40 down -> 320 operating.
        # ideal = 167 pcs x 24.5 SMV / 14 operators = 292.25 line-minutes.
        # A = 320/360 = 88.9 | P = 292.25/320 = 91.3 | Q = 163/167 = 97.6
        # OEE = 88.888.. x 91.328125 x 97.6047.. / 10000 = 79.2
        a = svc.oee_parts(360, 40, 292.25, 167, 163)
        check("OEE line A A/P/Q/OEE = 88.9/91.3/97.6/79.2",
              (a["availability"], a["performance"], a["quality"], a["oee"])
              == (88.9, 91.3, 97.6, 79.2), a)
        # Identity: with constant manning, Availability x Performance / 100 == efficiency.
        check("A x P / 100 == line efficiency (81.2)",
              round(320 / 360 * 100 * (292.25 / 320 * 100) / 100, 1)
              == svc.efficiency(167 * 24.5, 14 * 60 * 6), svc.efficiency(167 * 24.5, 14 * 60 * 6))
        # Hand-computed line B: 360 planned, 65 down -> 295 operating.
        # ideal = 239 x 12 / 10 = 286.8 | Q = 233/239
        b = svc.oee_parts(360, 65, 286.8, 239, 233)
        check("OEE line B A/P/Q/OEE = 81.9/97.2/97.5/77.7",
              (b["availability"], b["performance"], b["quality"], b["oee"])
              == (81.9, 97.2, 97.5, 77.7), b)
        z = svc.oee_parts(0, 0, 0, 0, 0)
        check("OEE all-zero is 0.0 everywhere, no crash",
              (z["availability"], z["performance"], z["quality"], z["oee"]) == (0.0, 0.0, 0.0, 0.0), z)
        c = svc.oee_parts(360, 999, 100, 10, 10)
        check("downtime clamped to planned -> availability 0, never negative",
              c["availability"] == 0.0 and c["operating_min"] == 0.0 and c["performance"] == 0.0, c)
        n = svc.oee_parts(None, "", "abc", None, "")
        check("OEE tolerates None/blank/non-numeric", n["oee"] == 0.0, n)

        # ---- roll-up against the seeded day ---------------------------------
        d = svc.dashboard()
        lines = {l["line_name"]: l for l in d["lines"]}
        check("dashboard rolls up 2 lines", len(d["lines"]) == 2, list(lines))
        la = [l for l in d["lines"] if l["operator_min"] == 5040]
        check("seeded line A matches the hand computation (167 pcs, 81.2%, OEE 79.2)",
              la and la[0]["actual"] == 167 and la[0]["efficiency"] == 81.2
              and la[0]["oee"] == 79.2, la and la[0])
        check("factory achievement 406/450 = 90.2", d["t"]["achievement"] == 90.2, d["t"]["achievement"])
        check("day downtime 40 + 65 = 105", d["downtime_day"] == 105.0, d["downtime_day"])
        check("top downtime reason ranked first is machine_breakdown (45)",
              d["downtime_top"][0]["reason"] == "machine_breakdown"
              and d["downtime_top"][0]["minutes"] == 45.0, d["downtime_top"][:2])
        check("2 red hours across the day", d["t"]["red_hours"] == 2, d["t"]["red_hours"])

        board = svc.hourly_board()
        check("board grid has 2 lines x 6 filled cells",
              len(board["lines"]) == 2 and all(len(l["cells"]) == 6 for l in board["lines"]))

        # ---- WIP + reconciliation -------------------------------------------
        w = svc.wip_by_section()
        by = {r["section"]: r["qty"] for r in w["rows"]}
        # 6 bundles x 60 = 360 cut; 4 moved to sewing, 2 of those on to finishing.
        check("WIP cascade cutting 120 / sewing 120 / finishing 120, total 360",
              (by["cutting"], by["sewing"], by["finishing"], w["wip_units"]) == (120, 120, 120, 360), by)
        check("WIP conserves the cut quantity", sum(by.values()) == 360, sum(by.values()))
        rec = svc.order_reconciliation()
        check("reconciliation cut 360 / sewn 240 / balance 120",
              rec and (rec[0]["cut"], rec[0]["sewn"], rec[0]["balance"]) == (360, 240, 120), rec)

        # ---- writes: hourly --------------------------------------------------
        line_id = svc.list_lines()[0]["id"]
        u = {"username": "selftest"}
        check("save_hourly rejects an unknown hour slot",
              svc.save_hourly({"line_id": line_id, "hour_slot": "99:00-99:00",
                               "target_qty": 10, "actual_qty": 5}, u)[1] == "mes.msg.bad_slot")
        check("save_hourly rejects a missing line id",
              svc.save_hourly({"line_id": 999999, "hour_slot": "08:00-09:00",
                               "target_qty": 10, "actual_qty": 5}, u)[1] == "mes.msg.no_line")
        check("save_hourly rejects rejects > actual (good would go negative)",
              svc.save_hourly({"line_id": line_id, "hour_slot": "15:00-16:00",
                               "target_qty": 10, "actual_qty": 5, "reject_qty": 9}, u)[1]
              == "mes.msg.reject_gt_actual")
        check("save_hourly rejects negative quantities",
              svc.save_hourly({"line_id": line_id, "hour_slot": "15:00-16:00",
                               "target_qty": -5, "actual_qty": 5}, u)[1] == "mes.msg.negative")
        ok, msg = svc.save_hourly({"line_id": line_id, "hour_slot": "15:00-16:00",
                                   "target_qty": "abc", "actual_qty": "", "operators": None,
                                   "smv": "x"}, u)
        check("save_hourly coerces blank/None/non-numeric to 0 instead of crashing", ok, msg)
        before = svc.dashboard()["t"]["hours"]
        svc.save_hourly({"line_id": line_id, "hour_slot": "15:00-16:00",
                         "target_qty": 30, "actual_qty": 25, "operators": 14, "smv": 24.5}, u)
        svc.save_hourly({"line_id": line_id, "hour_slot": "15:00-16:00",
                         "target_qty": 30, "actual_qty": 25, "operators": 14, "smv": 24.5}, u)
        after = svc.dashboard()["t"]["hours"]
        check("double-apply of the same (line,date,hour) edits, never duplicates",
              before == after == 13, (before, after))

        # ---- writes: downtime ------------------------------------------------
        check("add_downtime rejects an unknown reason code",
              svc.add_downtime({"line_id": line_id, "reason": "aliens", "minutes": 10}, u)[1]
              == "mes.msg.bad_reason")
        for bad in (0, "", None, "abc", -5):
            r = svc.add_downtime({"line_id": line_id, "reason": "power", "minutes": bad}, u)
            if r[0]:
                check("add_downtime should reject minutes=%r" % bad, False, r)
                break
        else:
            check("add_downtime rejects zero/blank/None/non-numeric/negative minutes", True)
        check("add_downtime rejects a missing line id",
              svc.add_downtime({"line_id": 999999, "reason": "power", "minutes": 10}, u)[1]
              == "mes.msg.no_line")
        check("add_downtime accepts a coded loss",
              svc.add_downtime({"line_id": line_id, "reason": "no_operator", "minutes": 12}, u)[0])

        # ---- writes: bundles + the movement invariant -------------------------
        for bad in (0, "", None, "abc", -3):
            r = svc.create_bundle({"qty": bad}, u)
            if r[0]:
                check("create_bundle should reject qty=%r" % bad, False, r)
                break
        else:
            check("create_bundle rejects zero/blank/None/non-numeric/negative qty", True)
        check("create_bundle rejects an unknown origin section",
              svc.create_bundle({"qty": 10, "origin_section": "moon"}, u)[1] == "mes.msg.bad_section")
        ok, bid = svc.create_bundle({"qty": 100, "size": "M", "color": "Black"}, u)
        check("create_bundle numbers from its own row id", ok and isinstance(bid, int), bid)

        check("move rejects a missing bundle",
              svc.move_bundle(999999, {"to_section": "sewing", "qty": 1}, u)[1] == "mes.msg.no_bundle")
        check("move rejects an unknown section",
              svc.move_bundle(bid, {"to_section": "moon", "qty": 1}, u)[1] == "mes.msg.bad_section")
        check("move rejects from==to",
              svc.move_bundle(bid, {"to_section": "cutting", "qty": 1}, u)[1] == "mes.msg.same_section")
        for bad in (0, "", None, "abc", -1):
            r = svc.move_bundle(bid, {"to_section": "sewing", "qty": bad}, u)
            if r[0]:
                check("move should reject qty=%r" % bad, False, r)
                break
        else:
            check("move rejects zero/blank/None/non-numeric/negative qty", True)
        # THE INVARIANT: never pass more pieces than are standing in the section.
        check("move refuses 101 of a 100-piece bundle",
              svc.move_bundle(bid, {"to_section": "sewing", "qty": 101}, u)[1]
              == "mes.msg.exceeds_bundle")
        check("move 60 of 100 is accepted",
              svc.move_bundle(bid, {"from_section": "cutting", "to_section": "sewing", "qty": 60}, u)[0])
        check("move refuses another 60 (only 40 left in cutting)",
              svc.move_bundle(bid, {"from_section": "cutting", "to_section": "sewing", "qty": 60}, u)[1]
              == "mes.msg.exceeds_bundle")
        check("move of the remaining 40 is accepted",
              svc.move_bundle(bid, {"from_section": "cutting", "to_section": "sewing", "qty": 40}, u)[0])
        check("cutting is now empty for this bundle -> any further move refused",
              svc.move_bundle(bid, {"from_section": "cutting", "to_section": "sewing", "qty": 1}, u)[1]
              == "mes.msg.exceeds_bundle")
        w2 = svc.wip_by_section()
        by2 = {r["section"]: r["qty"] for r in w2["rows"]}
        check("WIP still conserves everything cut (360 + 100)",
              sum(by2.values()) == 460 and by2["sewing"] == 220, by2)
        # walk it to the end -> completed
        for s_from, s_to in zip(SECTIONS[1:], SECTIONS[2:]):
            svc.move_bundle(bid, {"from_section": s_from, "to_section": s_to, "qty": 100}, u)
        done = [r for r in svc.list_bundles() if r["id"] == bid][0]
        check("bundle reaching dispatch in full is marked completed",
              done["status"] == "completed", done["status"])
        check("dispatch is finished goods, excluded from the WIP total",
              svc.wip_by_section()["wip_units"] == 360, svc.wip_by_section())
        svc.set_bundle_status(bid, "rejected", u)
        check("a rejected bundle leaves the WIP ledger entirely",
              sum(r["qty"] for r in svc.wip_by_section()["rows"]) == 360)
        mv = svc.recent_moves()
        check("scan ledger is append-only and names the bundle + operator",
              mv and mv[0]["bundle_no"] and mv[0]["operator"] == "selftest"
              and len(mv) == min(25, 6 + 6), len(mv))
        check("set_bundle_status rejects an unknown status",
              svc.set_bundle_status(bid, "banana", u)[1] == "mes.msg.bad_status")

        # ---- alerts ----------------------------------------------------------
        conn = get_db()
        conn.execute("DELETE FROM notifications WHERE module='mes'")
        conn.commit()
        conn.close()
        svc.alert_sweep()
        conn = get_db()
        n_first = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='mes'").fetchone()["c"]
        crit = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='mes' "
                            "AND severity='critical'").fetchone()["c"]
        conn.close()
        svc.alert_sweep()
        svc.alert_sweep()
        conn = get_db()
        n_again = conn.execute("SELECT COUNT(*) AS c FROM notifications WHERE module='mes'").fetchone()["c"]
        conn.close()
        check("sweep raises alerts for the red hours + the 65-minute line",
              n_first >= 3 and crit == 1, (n_first, crit))
        check("sweep is idempotent (no duplicate bell spam)", n_first == n_again, (n_first, n_again))

        # ---- line detail ------------------------------------------------------
        det = svc.line_detail(line_id)
        check("line_detail returns an OEE breakdown", det and "oee" in det["stat"])
        check("line_detail 404s on an unknown line", svc.line_detail(999999) is None)
        check("resolve_date falls back to the latest day with production",
              svc.resolve_date() == svc.dashboard()["work_date"])

    bad = [n for n, o in OK if not o]
    print("\n%d checks, %d failed" % (len(OK), len(bad)))
    for n in bad:
        print("  FAILED: " + n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
