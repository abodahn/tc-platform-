"""PM rescheduling, manager master data, SLA deadlines, downtime/MTTR, SLA %, cost.

Run: python -m app.maintenance.tests_pm_kpi
Generic sample data in an isolated temporary SQLite database.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
TMP = Path(tempfile.mkdtemp(prefix="mnt_pm_kpi_"))
os.environ.pop("DATABASE_URL", None)
os.environ["TC_ENV"] = "development"

import config                                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                      # noqa: E402
from app.db import get_db                                       # noqa: E402
from app.maintenance import services as svc                     # noqa: E402

app = create_app()
FAIL = []
ADMIN = {"id": 1, "username": "admin", "role": "super_admin", "full_name": "Admin"}


def ck(label, cond, extra=""):
    if not cond:
        FAIL.append(label)
    print(("  ok   " if cond else "  FAIL ") + label + (f" | {extra}" if extra else ""))


def q(sql, a=()):
    conn = get_db()
    try:
        return conn.execute(sql, a).fetchone()
    finally:
        conn.close()


def x(sql, a=()):
    conn = get_db()
    try:
        conn.execute(sql, a)
        conn.commit()
    finally:
        conn.close()


def ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def client(uid):
    cl = app.test_client()
    with cl.session_transaction() as s:
        s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "t"
    return cl


def main():
    mid = q("SELECT id FROM mnt_machines WHERE code='M-001'")["id"]

    print("1. completing a PM work order reschedules its plan, once")
    plan = q("SELECT * FROM mnt_pm_plans WHERE machine_id=?", (mid,))
    x("UPDATE mnt_pm_plans SET next_due='2000-01-01', interval_days=10 WHERE id=?", (plan["id"],))
    x("INSERT INTO mnt_pm_work_orders (pm_no,plan_id,machine_id,scheduled_date,status,created_at) "
      "VALUES ('PM-T-1',?,?,'2000-01-01','scheduled',datetime('now'))", (plan["id"], mid))
    wid = q("SELECT id FROM mnt_pm_work_orders WHERE pm_no='PM-T-1'")["id"]
    ok, _ = svc.complete_pm_wo(wid, "done", ADMIN)
    today = datetime.now(timezone.utc).date()
    p = q("SELECT last_done,next_due FROM mnt_pm_plans WHERE id=?", (plan["id"],))
    ck("completed", ok)
    ck("last_done = today", p["last_done"] == today.isoformat(), p["last_done"])
    ck("next_due = today + interval", p["next_due"] == (today + timedelta(days=10)).isoformat(), p["next_due"])
    x("UPDATE mnt_pm_plans SET next_due='2000-01-01' WHERE id=?", (plan["id"],))
    ok2, msg2 = svc.complete_pm_wo(wid, "again", ADMIN)
    ck("double submit refused and plan untouched",
       not ok2 and q("SELECT next_due FROM mnt_pm_plans WHERE id=?", (plan["id"],))["next_due"] == "2000-01-01",
       msg2)

    print("2. maintenance manager maintains day-to-day master data")
    conn = get_db()
    try:
        uid = conn.execute(
            "INSERT INTO users (username,password_hash,full_name,email,role,is_active,created_at) "
            "VALUES ('pmk.mgr','x','pmk.mgr','pmk@t.test','maintenance_manager',1,datetime('now'))").lastrowid
        tid_u = conn.execute(
            "INSERT INTO users (username,password_hash,full_name,email,role,is_active,created_at) "
            "VALUES ('pmk.tech','x','pmk.tech','pmkt@t.test','maintenance_technician',1,datetime('now'))").lastrowid
        conn.commit()
    finally:
        conn.close()
    cl = client(uid)
    r = cl.post("/maintenance/machines", data={"_csrf": "t", "code": "T-900", "name": "Test"})
    ck("manager adds a machine", r.status_code == 302 and q("SELECT 1 FROM mnt_machines WHERE code='T-900'"))
    r = cl.post("/maintenance/spares", data={"_csrf": "t", "code": "SP-T-900", "name": "Belt"})
    ck("manager adds a spare", r.status_code == 302 and q("SELECT 1 FROM mnt_spare_parts WHERE code='SP-T-900'"))
    r = cl.post("/maintenance/pm/new", data={"_csrf": "t", "machine_code": "T-900", "title": "Weekly oil",
                                            "frequency": "weekly"})
    newp = q("SELECT * FROM mnt_pm_plans WHERE title='Weekly oil'")
    ck("manager adds a PM plan (interval from frequency)",
       newp is not None and newp["interval_days"] == 7
       and newp["next_due"] == (today + timedelta(days=7)).isoformat())
    cl.post("/maintenance/pm/new", data={"_csrf": "t", "machine_code": "T-900", "title": "Weekly oil"})
    ck("duplicate PM plan refused", q("SELECT COUNT(*) c FROM mnt_pm_plans WHERE title='Weekly oil'")["c"] == 1)
    cl.post(f"/maintenance/pm/{newp['id']}/checklist", data={"_csrf": "t", "item": "Check oil"})
    ck("manager adds a checklist item",
       q("SELECT 1 FROM mnt_pm_checklist WHERE plan_id=? AND item='Check oil'", (newp["id"],)))
    ck("settings stay admin-only", cl.get("/maintenance/settings").status_code == 403)
    ck("pm page renders", cl.get("/maintenance/pm").status_code == 200)
    tc = client(tid_u)
    ck("technician still cannot add a machine",
       tc.post("/maintenance/machines", data={"_csrf": "t", "code": "T-901"}).status_code == 403
       and not q("SELECT 1 FROM mnt_machines WHERE code='T-901'"),
       tc.post("/maintenance/machines", data={"_csrf": "t", "code": "T-901"}).status_code)

    print("3. assigning keeps the SLA deadlines")
    tid, _ = svc.create_ticket({"description": "Sample fault", "machine_id": mid,
                                "production_stopped": 1, "allow_duplicate": True}, ADMIN)
    due = q("SELECT response_due,resolution_due FROM mnt_tickets WHERE id=?", (tid,))
    svc.review_assign(tid, "Technician A", "high", None, "", ADMIN)
    after = q("SELECT response_due,resolution_due FROM mnt_tickets WHERE id=?", (tid,))
    ck("deadlines kept", due["resolution_due"] and dict(after) == dict(due), dict(after))

    print("4/6. downtime to hand-back, MTTR from repair, labour + reopen cost delta")
    x("INSERT INTO mnt_settings (key,value) VALUES ('labour_rate_per_hour','10')")
    x("UPDATE mnt_tickets SET created_at=?, repair_started_at=?, status='repair', cost=5 WHERE id=?",
      (ago(300), ago(120), tid))
    m0 = q("SELECT total_downtime_min,cost_to_date FROM mnt_machines WHERE id=?", (mid,))
    svc.repair_proof(tid, {"action_performed": "fixed", "labour_hours": "2"}, ADMIN)
    ck("labour 2h x 10 added to cost", abs(q("SELECT cost FROM mnt_tickets WHERE id=?", (tid,))["cost"] - 25) < 1e-6)
    svc.repair_proof(tid, {"action_performed": "fixed", "labour_hours": "2"}, ADMIN)
    ck("double repair submit charges labour once",
       abs(q("SELECT cost FROM mnt_tickets WHERE id=?", (tid,))["cost"] - 25) < 1e-6)
    x("UPDATE mnt_tickets SET status='resolved', testing_done_at=? WHERE id=?", (ago(60), tid))
    ok, msg = svc.close_ticket(tid, ADMIN)
    t = q("SELECT * FROM mnt_tickets WHERE id=?", (tid,))
    ck("closed", ok, msg)
    ck("downtime = created -> tested (240 min), not -> closure",
       abs(t["total_downtime_min"] - 240) <= 1, t["total_downtime_min"])
    m1 = q("SELECT total_downtime_min,cost_to_date FROM mnt_machines WHERE id=?", (mid,))
    ck("machine downtime got the same figure",
       m1["total_downtime_min"] - m0["total_downtime_min"] == t["total_downtime_min"])
    ck("machine cost got the ticket cost", abs(m1["cost_to_date"] - m0["cost_to_date"] - 25) < 1e-6)
    ck("MTTR = repair start -> done (~120 min)", abs(svc.repair_minutes(t) - 120) <= 1, svc.repair_minutes(t))
    svc.reopen_ticket(tid, ADMIN)
    x("UPDATE mnt_tickets SET cost=cost+7, status='resolved' WHERE id=?", (tid,))
    svc.close_ticket(tid, ADMIN)
    m2 = q("SELECT total_downtime_min,cost_to_date,breakdowns FROM mnt_machines WHERE id=?", (mid,))
    ck("re-close rolls only the 7.00 delta", abs(m2["cost_to_date"] - m1["cost_to_date"] - 7) < 1e-6,
       m2["cost_to_date"] - m1["cost_to_date"])
    ck("re-close adds no downtime", m2["total_downtime_min"] == m1["total_downtime_min"])
    svc.reopen_ticket(tid, ADMIN)
    x("UPDATE mnt_tickets SET status='resolved' WHERE id=?", (tid,))
    svc.close_ticket(tid, ADMIN)
    ck("third close with no new parts rolls nothing",
       abs(q("SELECT cost_to_date FROM mnt_machines WHERE id=?", (mid,))["cost_to_date"] - m2["cost_to_date"]) < 1e-6)

    print("5. dashboard SLA is computed")
    x("UPDATE mnt_tickets SET resolution_due=? WHERE id=?", (ago(-600), tid))
    page = client(1).get("/maintenance/").get_data(as_text=True)
    ck("dashboard shows a computed 100% (not the old 92)", 'data-count="100" data-suffix="%"' in page)

    print("\n" + ("RESULT: ALL GREEN" if not FAIL else f"RESULT: {len(FAIL)} FAILED"))
    return not FAIL


if __name__ == "__main__":
    # no outer app_context: test_client requests would share one `g` (and its cached user)
    sys.exit(0 if main() else 1)
