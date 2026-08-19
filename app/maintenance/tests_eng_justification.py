# -*- coding: utf-8 -*-
"""Engineering Justification gate — DOAM §6, Table 13 and Table 14.

Run: python app/maintenance/tests_eng_justification.py

What matters here is not that the happy path works. It is that the gate cannot
be got round, and that it does not block the ONE thing the DOAM exempts. Both
failures are silent and expensive: a leaky gate means unjustified spares spend,
and an over-tight gate stops spare stock being replenished and the factory stops.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

_tmp = tempfile.mkdtemp(prefix="ejr-")
os.environ["TC_DATA_DIR"] = _tmp

from config import Config                                    # noqa: E402
Config.DB_PATH = os.path.join(_tmp, "ejr.db")

from app import create_app                                   # noqa: E402
from app.db import get_db                                    # noqa: E402
import app.maintenance.eng_justification as E                # noqa: E402

ok = True


def chk(label, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))


app = create_app()

COMPLETE = dict(
    machine_id=1, location="Sewing line 3", request_type="breakdown",
    description="Feed dog worn, skipped stitches", root_cause="Normal wear, 14 months",
    criticality="production_critical", downtime_risk="Line 3 stops, ~600 pcs/shift",
    stock_on_hand=0, stock_checked_with="Storekeeper Ali", alternatives="Repair not possible; local part available",
)


def row(conn, table, **cols):
    keys = ",".join(cols)
    qs = ",".join("?" * len(cols))
    cur = conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({qs})", tuple(cols.values()))
    return cur.lastrowid


with app.app_context():
    conn = get_db()

    # The gate ships OFF, because switching it on halts every maintenance
    # purchase that has no signed report — correct per DOAM §6, but not on the
    # strength of a document still marked "Draft v1.0" with a blank approval line.
    print("the control ships switched OFF")
    chk("by default the gate lets everything through",
        E.ejr_gate_check(conn, {"source_module": "maintenance", "source_ref": "ticket:1",
                                "department": None, "ejr_id": None}) == (True, "gate_disabled"))
    chk("'gate_disabled' is distinct from 'not_mro', so the audit trail can tell "
        "'DOAM not in force' from 'this purchase never needed a report'",
        E.gate_enabled(conn) is False)

    # Everything below tests the control as it behaves ONCE SWITCHED ON.
    conn.execute("INSERT OR REPLACE INTO mnt_settings (key, value) VALUES (?, '1')",
                 (E.EJR_GATE_SETTING,))
    chk("the mnt_settings row switches it on", E.gate_enabled(conn) is True)
    print()

    # ---------------------------------------------------------------- Table 14
    print("DOAM Table 14 — a report cannot be signed while a mandatory field is blank")
    partial = dict(COMPLETE)
    partial.pop("criticality")
    partial.pop("stock_checked_with")
    pid, pno = E.create(conn, partial, {"username": "tech"})
    good, msg = E.submit(conn, pid)
    chk("an incomplete report is refused at submit", not good and msg.startswith("incomplete"), msg)
    chk("it names the missing fields, so it can be fixed",
        "criticality" in msg and "store stock check" in msg, msg)

    full_id, full_no = E.create(conn, COMPLETE, {"username": "tech"})
    chk("a complete report submits", E.submit(conn, full_id)[0])
    chk("the form code is on the record (T&C-PUF-09)", full_no.startswith("T&C-PUF-09-"), full_no)

    print("\nDOAM Table 13 step 3 + §3.4 — the Engineering Head signs, and not the author")
    bad, msg = E.decide(conn, full_id, True, {"username": "tech"})
    chk("the author cannot approve their own justification",
        not bad and msg == "self_approval_blocked", msg)
    good, msg = E.decide(conn, full_id, True, {"username": "eng_head"}, note="Agreed")
    chk("the Engineering Head can", good and msg == "approved", msg)
    chk("an already-decided report cannot be decided twice",
        not E.decide(conn, full_id, True, {"username": "eng_head"})[0])

    # ---------------------------------------------------------------- §6 scope
    print("\nDOAM §6 — who needs a report at all")
    CASES = [
        ("maintenance ticket PR",   {"source_module": "maintenance", "source_ref": "ticket:9"},  True,  "maintenance_origin"),
        ("auto min/max reorder",    {"source_module": "maintenance", "source_ref": "spare:41"},  False, "min_max_replenishment"),
        ("fabric PR from costing",  {"source_module": "costing", "source_ref": "order:3"},       False, "not_mro"),
        ("raised in General Maintenance", {"source_module": None, "department": "General Maintenance"}, True, "mro_department"),
        ("ordinary IT purchase",    {"source_module": None, "department": "IT"},                 False, "not_mro"),
    ]
    for label, pr, want_req, want_why in CASES:
        base = {"source_module": None, "source_ref": None, "department": None, "ejr_id": None}
        base.update(pr)
        req, why = E.ejr_required(base)
        chk(f"{label:<32} -> {'needs a report' if req else 'exempt':<15} ({why})",
            req == want_req and why == want_why, f"expected {want_req}/{want_why}")

    print("\n  the DOAM's exemption is the one that must not regress:")
    chk("automatic replenishment is NEVER gated (the factory depends on it)",
        E.ejr_gate_check(conn, {"source_module": "maintenance", "source_ref": "spare:7",
                                "department": None, "ejr_id": None})[0])

    print("\n  a spare-part LINE pulls a request in even if nothing else does")
    class _R(dict):
        def keys(self):  # sqlite3.Row-like
            return super().keys()
    req, why = E.ejr_required(
        _R({"source_module": None, "source_ref": None, "department": "Production", "ejr_id": None}),
        [_R({"spare_id": 12})])
    chk("a request with a spare-part line needs a report", req and why == "spare_part_line", why)

    # ---------------------------------------------------------------- the gate
    print("\nthe gate itself: 'Procurement does not accept the requisition without it'")
    mro = {"source_module": "maintenance", "source_ref": "ticket:9", "department": None}

    chk("no report at all -> blocked",
        E.ejr_gate_check(conn, dict(mro, ejr_id=None)) == (False, "ejr_missing"))
    chk("a report that does not exist -> blocked, not waved through",
        E.ejr_gate_check(conn, dict(mro, ejr_id=999999)) == (False, "ejr_missing"))

    draft_id, _ = E.create(conn, COMPLETE, {"username": "tech"})
    chk("an unsigned draft -> blocked (Table 13: signed BEFORE it proceeds)",
        E.ejr_gate_check(conn, dict(mro, ejr_id=draft_id)) == (False, "ejr_not_approved"))
    E.submit(conn, draft_id)
    chk("submitted but not yet signed -> still blocked",
        E.ejr_gate_check(conn, dict(mro, ejr_id=draft_id)) == (False, "ejr_not_approved"))
    E.decide(conn, draft_id, False, {"username": "eng_head"}, note="Repair it")
    chk("a REJECTED report -> blocked", E.ejr_gate_check(conn, dict(mro, ejr_id=draft_id))[1] == "ejr_rejected")

    chk("an approved, complete report -> Procurement may proceed",
        E.ejr_gate_check(conn, dict(mro, ejr_id=full_id)) == (True, "ejr_approved"))

    print("\n  the way this gate would actually be defeated: gut the report AFTER signing")
    conn.execute("UPDATE mnt_eng_justifications SET criticality=NULL, stock_checked_with=NULL "
                 "WHERE id=?", (full_id,))
    okg, why = E.ejr_gate_check(conn, dict(mro, ejr_id=full_id))
    chk("an approved report emptied out later is still refused",
        not okg and why.startswith("ejr_incomplete"), why)
    chk("...and it says which fields went missing",
        "criticality" in why and "store stock check" in why, why)

    print("\n  soft-deleting the report does not unlock the purchase either")
    conn.execute("UPDATE mnt_eng_justifications SET is_active=0 WHERE id=?", (full_id,))
    chk("an archived report reads as missing, not as approved",
        E.ejr_gate_check(conn, dict(mro, ejr_id=full_id)) == (False, "ejr_missing"))

    print("\n§7.4.2 — an emergency may proceed, but the report still follows")
    em_id, _ = E.create(conn, dict(COMPLETE, is_emergency=True,
                                   emergency_due_at="2026-08-18 06:00:00"),
                        {"username": "tech"})
    r = E.get(conn, em_id)
    chk("the emergency flag and its 24-hour deadline are recorded",
        r["is_emergency"] == 1 and r["emergency_due_at"], dict(r).get("emergency_due_at"))
    chk("an emergency does NOT bypass the gate on its own",
        not E.ejr_gate_check(conn, dict(mro, ejr_id=em_id))[0])

    conn.commit()
    conn.close()

print("\nRESULT:", "ALL GREEN" if ok else "FAILURES ABOVE")
sys.exit(0 if ok else 1)
