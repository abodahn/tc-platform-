"""CMMS -> MES bridge executable proof.

Run: python app/maintenance/tests_mes_bridge.py
Uses only generic sample data in an isolated temporary database.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
TMP = Path(tempfile.mkdtemp(prefix="mes_bridge_"))
os.chdir(TMP)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                   # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                      # noqa: E402
from app.db import get_db                                       # noqa: E402
from app.maintenance import services as svc                     # noqa: E402
from app.maintenance.mes_bridge import down_machines             # noqa: E402
from app.mes.constants import MAX_DOWNTIME_MIN                   # noqa: E402
from app.services import registry                               # noqa: E402

app = create_app()
FAIL = []
ADMIN = {"id": 1, "username": "admin", "role": "super_admin", "full_name": "Admin"}


def ck(label, cond):
    if not cond:
        FAIL.append(label)
    print(("  ok   " if cond else "  FAIL ") + label)


def ticket(mid, stopped, age=90):
    tid, err = svc.create_ticket({"description": "Sample machine inspection",
                                  "machine_id": mid, "production_stopped": stopped,
                                  "allow_duplicate": True}, ADMIN)
    ck("ticket created", bool(tid) and not err)
    conn = get_db()
    try:
        created = (datetime.now(timezone.utc) - timedelta(minutes=age)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE mnt_tickets SET status='resolved',created_at=? WHERE id=?",
                     (created, tid))
        conn.commit()
    finally:
        conn.close()
    return tid


def exported(tid):
    conn = get_db()
    try:
        t = conn.execute("SELECT * FROM mnt_tickets WHERE id=?", (tid,)).fetchone()
        rows = conn.execute("SELECT * FROM mes_downtime WHERE created_by=?",
                            ("cmms:" + t["ticket_no"],)).fetchall()
        return t, rows
    finally:
        conn.close()


with app.app_context():
    conn = get_db()
    try:
        conn.execute("INSERT INTO production_lines (name) VALUES (?)", (" Sample Line ",))
        mid = conn.execute(
            "INSERT INTO mnt_machines (code,name,line_no,status,created_at) VALUES (?,?,?,?,?)",
            ("SAMPLE-1", "Sample machine", "sample line", "running", svc._now())).lastrowid
        unmatched = conn.execute(
            "INSERT INTO mnt_machines (code,name,line_no,status,created_at) VALUES (?,?,?,?,?)",
            ("SAMPLE-2", "Sample machine two", "Unmatched sample line", "running", svc._now())).lastrowid
        conn.commit()
    finally:
        conn.close()

    tid = ticket(mid, True, age=1500)
    ck("production stop closes", svc.close_ticket(tid, ADMIN) == (True, ""))
    t, rows = exported(tid)
    ck("one MES row linked to the ticket", len(rows) == 1 and t["mes_downtime_id"] == rows[0]["id"])
    ck("MES date, reason and daily cap", len(rows) == 1
       and rows[0]["minutes"] == MAX_DOWNTIME_MIN
       and rows[0]["work_date"] == t["created_at"][:10]
       and rows[0]["reason"] == "machine_breakdown")
    first = dict(rows[0]) if rows else None
    ck("ticket reopens", svc.reopen_ticket(tid, ADMIN) == (True, ""))
    conn = get_db()
    try:
        conn.execute("UPDATE mnt_tickets SET status='resolved',created_at=? WHERE id=?",
                     (svc._now(), tid))
        conn.commit()
    finally:
        conn.close()
    ck("ticket closes again", svc.close_ticket(tid, ADMIN) == (True, ""))
    t, rows = exported(tid)
    ck("reclose preserves exactly one unchanged MES row", len(rows) == 1
       and dict(rows[0]) == first and t["mes_downtime_id"] == rows[0]["id"])

    tid = ticket(mid, False)
    ck("non-stopping ticket closes", svc.close_ticket(tid, ADMIN) == (True, ""))
    t, rows = exported(tid)
    ck("non-stopping ticket has no MES row", not rows and t["mes_downtime_id"] is None)

    tid = ticket(unmatched, True)
    ck("unmatched line closes without error", svc.close_ticket(tid, ADMIN) == (True, ""))
    t, rows = exported(tid)
    ck("unmatched line has no MES row", not rows and t["mes_downtime_id"] is None)

    tid, err = svc.create_ticket({"description": "Sample production stop", "machine_id": mid,
                                  "production_stopped": True, "allow_duplicate": True}, ADMIN)
    ck("open stop ticket created", bool(tid) and not err)
    down = next((m for m in down_machines() if m["id"] == mid), None)
    ck("floor board lists stopped machine and its open ticket", down is not None
       and down["status"] == "stopped" and down["ticket_id"] == tid
       and down["line_name"] == " Sample Line ")
    ck("empty registry lookup", registry.find_asset([], "X") is None)

print("FAIL" if FAIL else "ok: MES bridge self-test passed")
sys.exit(1 if FAIL else 0)
