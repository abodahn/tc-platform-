"""TC Terminal machine events -> maintenance tickets (app/maintenance/machine_events.py)."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app
from app.db import get_db
from app.maintenance import services as svc
from config import Config

TOKEN = "test-token-for-machine-events"
URL = "/api/maintenance/machine-event"


@pytest.fixture(scope="module")
def app():
    """One app and database for the module: building one per test costs ~10 s each."""
    old = Config.MACHINE_EVENT_TOKEN
    Config.MACHINE_EVENT_TOKEN = TOKEN
    a = create_app()
    a.config["TESTING"] = True
    with a.app_context():
        conn = get_db()
        conn.execute("INSERT INTO mnt_machines (code, name, department, area, line_no, status, is_active) "
                     "VALUES ('SN-TCT-001', 'Overlock 3', 'Sewing', 'Hall A', 'L3', 'running', 1)")
        for username, full_name, role in (("tct_chief", "Mona Chief", "production_supervisor"),
                                          ("tct_mech", "Karim Mechanic", "maintenance_technician")):
            conn.execute("INSERT INTO users (username, password_hash, full_name, role, is_active) "
                         "VALUES (?, 'x', ?, ?, 1)", (username, full_name, role))
        for key, value in (("machine:001", "SN-TCT-001"), ("person:201", "tct_chief"),
                           ("person:301", "tct_mech"), ("fault:1", "mechanical")):
            conn.execute("INSERT INTO mnt_ext_map (ext_key, local_value, is_active) VALUES (?, ?, 1)", (key, value))
        conn.commit()
        conn.close()
    yield a
    Config.MACHINE_EVENT_TOKEN = old


@pytest.fixture()
def client(app):
    with app.test_client() as c:
        yield c


CHIEF = {"id": 201, "name": "Mona S."}
MECH = {"id": 301, "name": "Karim A."}


def event(name, fault, machine="001", person=CHIEF, **extra):
    return {"event": name, "event_uid": uuid.uuid4().hex, "fault_uid": fault, "machine_code": machine,
            "machine_name": "Line 3 / Machine " + machine, "occurred_at": datetime.now().astimezone().isoformat(),
            "clock_ok": True, "person": person, **extra}


def send(client, ev, token=TOKEN):
    r = client.post(URL, json=ev, headers={"X-TC-Token": token})
    return r.status_code, r.get_json()


def row(app, sql, *args):
    with app.app_context():
        conn = get_db()
        try:
            r = conn.execute(sql, args).fetchone()
            return dict(r) if r else None
        finally:
            conn.close()


def ticket(app, ticket_no):
    return row(app, "SELECT * FROM mnt_tickets WHERE ticket_no=?", ticket_no)


def machine_status(app):
    return row(app, "SELECT status FROM mnt_machines WHERE code='SN-TCT-001'")["status"]


def test_full_cycle_follows_the_machine(app, client):
    fault = uuid.uuid4().hex
    opened_at = (datetime.now(timezone.utc) - timedelta(minutes=12)).astimezone()
    code, body = send(client, event("fault_opened", fault, fault_type={"id": 1, "name": "Needle broken"},
                                    machine_stopped=True, occurred_at=opened_at.isoformat()))
    assert code == 200 and body["result"] == "opened" and body["status"] == "submitted"
    t = ticket(app, body["ticket_no"])
    assert (t["priority"], t["severity"], t["production_stopped"]) == ("high", "major", 1)
    assert t["requester"] == "Mona Chief" and t["issue_category"] == "mechanical"
    assert t["machine_code"] == "SN-TCT-001" and t["machine_id"] and t["line_no"] == "L3"
    assert "Needle broken" in t["description"]
    assert t["created_at"] == opened_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")   # machine time
    assert machine_status(app) == "stopped"
    due = (t["response_due"], t["resolution_due"])

    code, body = send(client, event("repair_started", fault, person=MECH))
    t = ticket(app, body["ticket_no"])
    assert body["status"] == "repair" and t["assigned_to"] == "Karim Mechanic" and t["work_order_no"]
    assert (t["response_due"], t["resolution_due"]) == due        # the SLA clock survives the assignment
    assert t["repair_started_at"] and machine_status(app) == "under_maintenance"

    assert send(client, event("parts_waiting_on", fault))[1]["status"] == "spare_required"
    assert machine_status(app) == "waiting_spare"
    assert send(client, event("parts_waiting_off", fault))[1]["status"] == "repair"

    code, body = send(client, event("repair_finished", fault, person=MECH, fault_open_after=False))
    assert body["status"] == "resolved" and machine_status(app) == "running"   # the manager closes
    t = ticket(app, body["ticket_no"])
    assert t["repair_done_at"] and t["action_performed"].endswith("Karim Mechanic")
    with app.test_request_context():
        assert svc.close_ticket(t["id"], {"username": "manager", "role": "maintenance_manager"}) == (True, "")
    assert ticket(app, body["ticket_no"])["status"] == "closed"


def test_a_resent_event_changes_nothing(app, client):
    fault = uuid.uuid4().hex
    ev = event("fault_opened", fault, machine="002")
    first, again = send(client, ev)[1], send(client, ev)[1]
    assert again["duplicate"] and again["ticket_no"] == first["ticket_no"]
    n = row(app, "SELECT COUNT(*) n FROM mnt_machine_events WHERE fault_ref=?", "tcterm:002:" + fault)["n"]
    assert n == 1
    again_open = send(client, event("fault_opened", fault, machine="002"))[1]   # the same fault, a new event
    assert again_open["result"] == "comment" and again_open["ticket_no"] == first["ticket_no"]


def test_ended_without_a_repair_is_cancelled_not_a_breakdown(app, client):
    fault = uuid.uuid4().hex
    no = send(client, event("fault_opened", fault, machine="003"))[1]["ticket_no"]
    code, body = send(client, event("fault_closed", fault, machine="003"))
    assert body["result"] == "cancelled" and ticket(app, no)["status"] == "cancelled"


def test_unknown_machine_person_and_fault_are_ticketed_and_listed(app, client):
    fault = uuid.uuid4().hex
    body = send(client, event("fault_opened", fault, machine="077", person={"id": 999, "name": "Omar X"},
                              fault_type={"id": 42, "name": "Oil leak"}))[1]
    t = ticket(app, body["ticket_no"])
    assert t["machine_code"] == "EGEMEN-077" and t["machine_id"] is None
    assert t["requester"] == "Omar X" and t["issue_category"] == "other"
    for key, name in (("machine:077", "Line 3 / Machine 077"), ("person:999", "Omar X"), ("fault:42", "Oil leak")):
        m = row(app, "SELECT ext_name, local_value FROM mnt_ext_map WHERE ext_key=?", key)
        assert m == {"ext_name": name, "local_value": None}            # left for an admin to link


def test_a_fault_still_open_after_the_repair_resolves_when_the_chief_ends_it(app, client):
    fault = uuid.uuid4().hex
    send(client, event("fault_opened", fault, machine="004"))
    send(client, event("repair_started", fault, machine="004", person=MECH))
    assert send(client, event("repair_finished", fault, machine="004", person=MECH,
                              fault_open_after=True))[1]["status"] == "testing"
    assert send(client, event("fault_closed", fault, machine="004"))[1]["status"] == "resolved"


def test_a_repair_on_a_fault_from_before_the_link_opens_its_ticket(app, client):
    body = send(client, event("repair_started", uuid.uuid4().hex, machine="005", person=MECH))[1]
    assert body["status"] == "repair" and body["ticket_no"]
    assert send(client, event("fault_closed", uuid.uuid4().hex, machine="005"))[1]["result"] == "no_ticket"


def test_a_machine_with_an_open_ticket_gets_no_second_one(app, client):
    with app.test_request_context():
        mid = row(app, "SELECT id FROM mnt_machines WHERE code='SN-TCT-001'")["id"]
        tid, err = svc.create_ticket({"machine_id": mid, "machine_code": "SN-TCT-001", "description": "manual"},
                                     {"id": None, "username": "someone", "full_name": "Someone", "role": "x"})
    assert not err
    manual = row(app, "SELECT ticket_no FROM mnt_tickets WHERE id=?", tid)["ticket_no"]
    fault = uuid.uuid4().hex
    body = send(client, event("fault_opened", fault))[1]
    assert body["result"] == "joined_open_ticket" and body["ticket_no"] == manual
    # a fault that joined someone else's ticket only comments on it: never moves, resolves or cancels it
    for name, who in (("repair_started", MECH), ("repair_finished", MECH), ("fault_closed", CHIEF)):
        body = send(client, event(name, fault, person=who))[1]
        assert (body["result"], body["ticket_no"], body["status"]) == ("comment", manual, "submitted")
    with app.test_request_context():
        conn = get_db()
        svc.set_ticket_status(conn, tid, "cancelled", {"username": "t", "role": "x"}, force=True)
        conn.commit()
        conn.close()


def test_a_board_clock_not_yet_set_never_backdates(app, client):
    body = send(client, event("fault_opened", uuid.uuid4().hex, machine="006", clock_ok=False,
                              occurred_at="1970-01-01T00:05:00+00:00"))[1]
    created = datetime.strptime(ticket(app, body["ticket_no"])["created_at"], "%Y-%m-%d %H:%M:%S")
    assert abs(created - datetime.now(timezone.utc).replace(tzinfo=None)) < timedelta(minutes=2)


def test_token_and_payload_checks(app, client, monkeypatch):
    ev = event("fault_opened", uuid.uuid4().hex, machine="007")
    assert send(client, ev, token="wrong")[0] == 403
    assert send(client, ev, token="")[0] == 403
    for bad in ({**ev, "event": "drop_table"}, {**ev, "machine_code": "1; DROP"}, {**ev, "fault_uid": "x"}, []):
        assert send(client, bad)[0] == 400
    monkeypatch.setattr(Config, "MACHINE_EVENT_TOKEN", "")
    assert send(client, ev)[0] == 503                                   # off until the token is set


def test_import_fills_an_empty_link_and_never_changes_a_set_one(app, client):
    import io
    from _support import get_csrf, login_admin
    send(client, event("fault_opened", uuid.uuid4().hex, machine="088"))   # leaves machine:088 empty
    login_admin(client)
    csv = ("ext_key,ext_name,local_value,notes\n"
           "machine:088,Line 3 / Machine 088,SN-TCT-001,\n"
           "machine:001,,SN-SOMETHING-ELSE,\n"                               # already linked: kept
           "person:777,New person,tct_mech,\n").encode()
    r = client.post("/maintenance/import/ext_map", data={"_csrf": get_csrf(client), "file": (io.BytesIO(csv), "links.csv")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert r.status_code == 200
    value = lambda key: row(app, "SELECT local_value FROM mnt_ext_map WHERE ext_key=?", key)["local_value"]   # noqa: E731
    assert (value("machine:088"), value("machine:001"), value("person:777")) == ("SN-TCT-001", "SN-TCT-001", "tct_mech")



def force_status(app, ticket_no, status):
    with app.test_request_context():
        conn = get_db()
        tid = conn.execute("SELECT id FROM mnt_tickets WHERE ticket_no=?", (ticket_no,)).fetchone()["id"]
        svc.set_ticket_status(conn, tid, status, {"username": "t", "role": "x"}, force=True)
        conn.commit()
        conn.close()


def test_the_parts_approval_chain_is_never_skipped(app, client):
    fault = uuid.uuid4().hex
    no = send(client, event("fault_opened", fault, machine="010"))[1]["ticket_no"]
    send(client, event("repair_started", fault, machine="010", person=MECH))
    force_status(app, no, "waiting_approval")                 # a spare request raised on the platform
    body = send(client, event("repair_finished", fault, machine="010", person=MECH))[1]
    assert (body["result"], body["status"]) == ("comment", "waiting_approval")


def test_a_copy_still_running_answers_409_and_a_dead_claim_is_taken_over(app, client):
    ev = event("fault_opened", uuid.uuid4().hex, machine="011")
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO mnt_machine_events (event_uid, fault_ref, event, received_at, result) "
                     "VALUES (?, 'x', 'fault_opened', ?, 'pending:other')", (ev["event_uid"], svc._now()))
        conn.commit()
        conn.close()
    assert send(client, ev)[0] == 409                         # the bridge sends it again later
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE mnt_machine_events SET received_at='2001-01-01 00:00:00' WHERE event_uid=?",
                     (ev["event_uid"],))
        conn.commit()
        conn.close()
    code, body = send(client, ev)
    assert code == 200 and body["result"] == "opened"


def test_a_copy_that_failed_half_way_is_finished_by_the_resend(app, client, monkeypatch):
    from app.maintenance import machine_events as mev
    real, calls = mev._stamp, []

    def flaky(tid, column, ev):
        calls.append(column)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        real(tid, column, ev)
    monkeypatch.setattr(mev, "_stamp", flaky)
    ev = event("fault_opened", uuid.uuid4().hex, machine="012")     # an unmapped machine: no duplicate guard
    assert send(client, ev)[0] == 500                         # the ticket exists, the answer is a 500
    code, body = send(client, ev)
    assert code == 200 and body["result"] == "opened"
    n = row(app, "SELECT COUNT(*) n FROM mnt_tickets WHERE machine_code='EGEMEN-012'")["n"]
    assert n == 1                                              # one fault, one ticket

    fault = uuid.uuid4().hex
    send(client, event("fault_opened", fault, machine="013"))
    calls.clear()
    finished = event("repair_finished", fault, machine="013", person=MECH)
    assert send(client, finished)[0] == 500                   # stopped right after 'testing'
    assert send(client, finished)[1]["status"] == "resolved"  # the resend owes 'resolved' and does it


def test_events_out_of_order(app, client):
    fault = uuid.uuid4().hex
    assert send(client, event("fault_closed", fault, machine="014"))[1]["result"] == "no_ticket"
    body = send(client, event("fault_opened", fault, machine="014"))[1]
    assert body["result"] == "already_closed" and body["ticket_no"] is None


def test_a_fault_ended_elsewhere_during_the_repair_is_resolved(app, client):
    fault = uuid.uuid4().hex
    send(client, event("fault_opened", fault, machine="015"))
    send(client, event("repair_started", fault, machine="015", person=MECH))
    assert send(client, event("fault_closed", fault, machine="015"))[1]["status"] == "resolved"


def test_hostile_text_and_times_never_poison_the_queue(app, client):
    ev = event("fault_opened", uuid.uuid4().hex, machine="016", person={"id": 5, "name": "Bad\x00Name\ud800"},
               occurred_at="0001-01-01T00:00:00+14:00", note="<script>alert(1)</script>")
    code, body = send(client, ev)
    assert code == 200
    t = ticket(app, body["ticket_no"])
    assert "\x00" not in t["requester"] and "<script>" in t["description"]   # stored as text...
    with app.test_request_context():
        from app.routes.maintenance import mhuman
        assert "<script>" not in str(mhuman("<script>x</script>"))       # ...and shown escaped




def test_events_of_one_fault_are_applied_one_at_a_time(app, client):
    fault = uuid.uuid4().hex
    with app.app_context():                                   # fault_opened of this fault is still running
        conn = get_db()
        conn.execute("INSERT INTO mnt_machine_events (event_uid, fault_ref, event, received_at, result) "
                     "VALUES (?, ?, 'fault_opened', ?, 'pending:busy')",
                     (uuid.uuid4().hex, "tcterm:020:" + fault, svc._now()))
        conn.commit()
        conn.close()
    later = event("repair_started", fault, machine="020", person=MECH)
    assert send(client, later)[0] == 409                      # waits its turn
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE mnt_machine_events SET result='no_ticket' WHERE result='pending:busy'")
        conn.commit()
        conn.close()
    assert send(client, later)[1]["status"] == "repair"       # the resend goes through at once


def test_a_joined_fault_gets_its_own_ticket_when_the_other_one_ends(app, client):
    owner_fault, joined_fault = uuid.uuid4().hex, uuid.uuid4().hex
    with app.test_request_context():
        conn = get_db()
        conn.execute("INSERT INTO mnt_machines (code, status, is_active) VALUES ('SN-TCT-021', 'running', 1)")
        conn.execute("INSERT INTO mnt_ext_map (ext_key, local_value, is_active) VALUES ('machine:021', 'SN-TCT-021', 1)")
        conn.commit()
        conn.close()
    first = send(client, event("fault_opened", owner_fault, machine="021"))[1]
    assert send(client, event("fault_opened", joined_fault, machine="021"))[1]["result"] == "joined_open_ticket"
    send(client, event("fault_closed", owner_fault, machine="021"))           # cancelled: no repair
    body = send(client, event("repair_started", joined_fault, machine="021", person=MECH))[1]
    assert body["ticket_no"] != first["ticket_no"] and body["status"] == "repair"
    assert send(client, event("repair_finished", joined_fault, machine="021", person=MECH))[1]["status"] == "resolved"


def test_a_machine_another_ticket_still_holds_is_not_marked_running(app, client):
    fault = uuid.uuid4().hex
    with app.test_request_context():
        conn = get_db()
        conn.execute("INSERT INTO mnt_machines (code, status, is_active) VALUES ('SN-TCT-022', 'running', 1)")
        conn.execute("INSERT INTO mnt_ext_map (ext_key, local_value, is_active) VALUES ('machine:022', 'SN-TCT-022', 1)")
        mid = conn.execute("SELECT id FROM mnt_machines WHERE code='SN-TCT-022'").fetchone()["id"]
        conn.commit()
        conn.close()
    send(client, event("fault_opened", fault, machine="022"))
    send(client, event("repair_started", fault, machine="022", person=MECH))
    with app.test_request_context():                          # someone raises a second ticket by hand
        _, err = svc.create_ticket({"machine_id": mid, "machine_code": "SN-TCT-022", "description": "oil",
                                    "production_stopped": True, "allow_duplicate": True},
                                   {"id": None, "username": "x", "full_name": "X", "role": "x"})
    assert not err
    assert send(client, event("repair_finished", fault, machine="022", person=MECH))[1]["status"] == "resolved"
    assert row(app, "SELECT status FROM mnt_machines WHERE id=?", mid)["status"] == "stopped"


def test_a_resend_restores_the_machine_time_the_failed_copy_owed(app, client, monkeypatch):
    from app.maintenance import machine_events as mev
    real, calls = mev._stamp, []

    def flaky(tid, column, ev):
        calls.append(column)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        real(tid, column, ev)
    monkeypatch.setattr(mev, "_stamp", flaky)
    at = (datetime.now(timezone.utc) - timedelta(minutes=30)).astimezone()
    ev = event("fault_opened", uuid.uuid4().hex, machine="023", occurred_at=at.isoformat())
    assert send(client, ev)[0] == 500
    body = send(client, ev)[1]
    assert ticket(app, body["ticket_no"])["created_at"] == at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def test_a_repair_after_the_fault_ended_opens_nothing(app, client):
    fault = uuid.uuid4().hex
    send(client, event("fault_closed", fault, machine="024"))
    assert send(client, event("repair_started", fault, machine="024", person=MECH))[1]["result"] == "already_closed"


def test_a_fault_ended_while_waiting_for_parts_is_resolved(app, client):
    fault = uuid.uuid4().hex
    send(client, event("fault_opened", fault, machine="025"))
    send(client, event("repair_started", fault, machine="025", person=MECH))
    assert send(client, event("parts_waiting_on", fault, machine="025"))[1]["status"] == "spare_required"
    assert send(client, event("fault_closed", fault, machine="025"))[1]["status"] == "resolved"


def test_a_failure_before_applying_never_blocks_the_resend(app, client, monkeypatch):
    from app.maintenance import machine_events as mev
    real, calls = mev._context, []

    def flaky(conn, ev):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        return real(conn, ev)
    monkeypatch.setattr(mev, "_context", flaky)
    ev = event("fault_opened", uuid.uuid4().hex, machine="026")
    assert send(client, ev)[0] == 500
    assert send(client, ev)[0] == 200                         # not 409 for ten minutes


def test_a_failure_between_creating_and_linking_opens_no_second_ticket(app, client, monkeypatch):
    from app.maintenance import machine_events as mev
    real, calls = mev._link, []

    def flaky(uid, tid, opened):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database went away")
        real(uid, tid, opened)
    monkeypatch.setattr(mev, "_link", flaky)
    ev = event("fault_opened", uuid.uuid4().hex, machine="027")    # unmapped: no duplicate guard to help
    assert send(client, ev)[0] == 500
    assert send(client, ev)[1]["result"] == "opened"
    assert row(app, "SELECT COUNT(*) n FROM mnt_tickets WHERE machine_code='EGEMEN-027'")["n"] == 1
