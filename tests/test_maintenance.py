"""
TC Platform — Maintenance module end-to-end tests.

Covers the main test scenario (#33): supervisor reports issue -> manager assigns
-> technician diagnoses -> requests spare -> manager approves -> storekeeper
issues (stock decreases atomically) -> technician receives -> repair -> test ->
close. Plus the rejection and out-of-stock scenarios.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app          # noqa: E402
from app.db import get_db           # noqa: E402
from app.maintenance import services as svc  # noqa: E402
from app.maintenance import ai as ai_engine  # noqa: E402


@pytest.fixture()
def app_ctx(tmp_path, monkeypatch):
    # Isolate each maintenance test on a fresh temp DB. The end-to-end workflow
    # depends on seeded spare-parts stock; sharing the real dev platform.db made
    # this test flaky (stock depletes across runs -> 'waiting_stock' instead of
    # 'waiting_approval'). A fresh DB is seeded with full stock every time.
    from config import Config
    monkeypatch.setattr(Config, "DB_PATH", tmp_path / "mnt.db")
    monkeypatch.setattr(Config, "DATABASE_URL", "")
    app = create_app()
    app.config["TESTING"] = True
    with app.app_context():
        yield app


def _user(username):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return dict(row)


def _machine(code):
    conn = get_db()
    row = conn.execute("SELECT * FROM mnt_machines WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row)


def _spare(code):
    conn = get_db()
    row = conn.execute("SELECT * FROM mnt_spare_parts WHERE code=?", (code,)).fetchone()
    conn.close()
    return dict(row)


def _ticket(tid):
    conn = get_db()
    row = conn.execute("SELECT * FROM mnt_tickets WHERE id=?", (tid,)).fetchone()
    conn.close()
    return dict(row)


def test_full_workflow_end_to_end(app_ctx):
    supervisor = _user("supervisor")
    manager = _user("maint")
    tech = _user("tech")
    store = _user("store")
    m1 = _machine("M-001")
    sp = _spare("SP-002")            # rotary hook, stock 8, criticality critical
    stock_before = sp["stock_qty"]

    # 1-2: supervisor reports a problem on M-001 -> manager receives (submitted)
    tid = svc.create_ticket({
        "machine_id": m1["id"], "machine_code": "M-001", "department": "Production",
        "description": "Skipped stitches and noise", "priority": "high",
        "production_stopped": True,
    }, supervisor)
    assert _ticket(tid)["status"] == "submitted"

    # 3: manager assigns Technician A
    svc.review_assign(tid, "Technician A", "high", None, None, manager)
    assert _ticket(tid)["status"] == "assigned"
    assert _ticket(tid)["work_order_no"]

    # 4-5: technician diagnoses, one spare required
    svc.add_diagnosis(tid, {"fault_found": "Worn hook", "root_cause": "wear_tear",
                            "spare_needed": True}, tech)
    assert _ticket(tid)["status"] == "spare_required"

    # 6-7-8-9: request spare (in stock) -> goes to approval
    rid, msg = svc.create_request(tid, [{"spare_id": sp["id"], "qty": 1}], "hook worn", "high", tech)
    assert rid and _ticket(tid)["status"] == "waiting_approval"

    # 10: approve every required level (critical part needs manager + factory manager)
    factory = _user("factory")
    conn = get_db()
    pending = conn.execute("SELECT id, approver_role FROM mnt_approvals WHERE request_id=? AND status='pending' ORDER BY level", (rid,)).fetchall()
    conn.close()
    for ap in pending:
        approver = factory if ap["approver_role"] == "factory_manager" else manager
        ok, _ = svc.decide_approval(ap["id"], "approve", "ok", approver)
        assert ok
    assert _ticket(tid)["status"] == "approved_issue"

    # 11-12: storekeeper issues -> stock decreases atomically, voucher created
    ok, msg = svc.issue_parts(rid, "Technician A", store)
    assert ok, msg
    assert _spare("SP-002")["stock_qty"] == stock_before - 1
    assert _ticket(tid)["status"] == "parts_issued"
    conn = get_db()
    voucher = conn.execute("SELECT * FROM mnt_vouchers WHERE request_id=?", (rid,)).fetchone()
    movement = conn.execute("SELECT * FROM mnt_stock_movements WHERE request_id=? AND type='issue'", (rid,)).fetchone()
    conn.close()
    assert voucher is not None and movement is not None
    assert movement["after_qty"] == stock_before - 1

    # 13: technician confirms receiving
    svc.confirm_receiving(rid, tech)
    assert _ticket(tid)["status"] == "repair"

    # 14-15: repair + proof
    svc.repair_proof(tid, {"action_performed": "Replaced hook", "machine_running": "Yes"}, tech)
    assert _ticket(tid)["status"] == "testing"

    # 16: test confirms working
    svc.record_test(tid, {"test_result": "Pass", "machine_running": "Yes"}, supervisor)
    assert _ticket(tid)["status"] == "resolved"

    # 17: manager closes
    ok, msg = svc.close_ticket(tid, manager)
    assert ok, msg
    assert _ticket(tid)["status"] == "closed"

    # 18-19: machine reset to running, breakdown counted, audit present
    assert _machine("M-001")["status"] == "running"
    conn = get_db()
    audit_n = conn.execute("SELECT COUNT(*) c FROM mnt_audit WHERE entity_type='ticket' AND entity_id=?", (tid,)).fetchone()["c"]
    conn.close()
    assert audit_n >= 6  # create, assign, diagnosis, status changes, close


def test_issue_blocked_before_approval(app_ctx):
    manager, tech, store = _user("maint"), _user("tech"), _user("supervisor")
    m = _machine("M-002")
    sp = _spare("SP-003")
    tid = svc.create_ticket({"machine_id": m["id"], "machine_code": "M-002",
                             "description": "belt issue", "priority": "medium"}, tech)
    svc.review_assign(tid, "Technician A", None, None, None, manager)
    svc.add_diagnosis(tid, {"fault_found": "belt", "root_cause": "wear_tear", "spare_needed": True}, tech)
    rid, _ = svc.create_request(tid, [{"spare_id": sp["id"], "qty": 1}], "belt", "normal", tech)
    # issuing before approval must fail (rejected request cannot be issued)
    ok, msg = svc.issue_parts(rid, "Technician A", _user("store"))
    assert not ok and msg == "not_approved"


def test_rejection_requires_justification(app_ctx):
    manager, tech = _user("maint"), _user("tech")
    m = _machine("M-002")
    sp = _spare("SP-003")
    tid = svc.create_ticket({"machine_id": m["id"], "machine_code": "M-002",
                             "description": "belt", "priority": "low"}, tech)
    svc.review_assign(tid, "Technician A", None, None, None, manager)
    svc.add_diagnosis(tid, {"fault_found": "belt", "root_cause": "wear_tear", "spare_needed": True}, tech)
    rid, _ = svc.create_request(tid, [{"spare_id": sp["id"], "qty": 1}], "belt", "normal", tech)
    conn = get_db()
    ap = conn.execute("SELECT id FROM mnt_approvals WHERE request_id=? AND status='pending'", (rid,)).fetchone()
    conn.close()
    # reject without comment -> blocked
    ok, msg = svc.decide_approval(ap["id"], "reject", "", manager)
    assert not ok and msg == "reason_required"
    # reject with comment -> request rejected, ticket stays open
    ok, _ = svc.decide_approval(ap["id"], "reject", "not justified", manager)
    assert ok
    conn = get_db()
    rstatus = conn.execute("SELECT status FROM mnt_requests WHERE id=?", (rid,)).fetchone()["status"]
    conn.close()
    assert rstatus == "rejected"
    assert _ticket(tid)["status"] == "spare_required"  # remains open


def test_out_of_stock_scenario(app_ctx):
    manager, tech = _user("maint"), _user("tech")
    m = _machine("M-003")
    sp = _spare("SP-005")   # blade, stock 3
    tid = svc.create_ticket({"machine_id": m["id"], "machine_code": "M-003",
                             "description": "blade dull", "priority": "critical"}, tech)
    svc.review_assign(tid, "Technician A", None, None, None, manager)
    svc.add_diagnosis(tid, {"fault_found": "blade", "root_cause": "wear_tear", "spare_needed": True}, tech)
    rid, _ = svc.create_request(tid, [{"spare_id": sp["id"], "qty": 99}], "blade", "urgent", tech)
    conn = get_db()
    rstatus = conn.execute("SELECT status FROM mnt_requests WHERE id=?", (rid,)).fetchone()["status"]
    conn.close()
    assert rstatus == "out_of_stock"
    assert _ticket(tid)["status"] == "waiting_stock"  # downtime keeps tracking


def test_stock_cannot_go_negative(app_ctx):
    store = _user("store")
    sp = _spare("SP-009")
    ok, msg = svc.adjust_stock(sp["id"], -5, "test", store)
    assert not ok and msg == "stock_would_go_negative"


def test_machine_health_in_range(app_ctx):
    conn = get_db()
    try:
        for m in conn.execute("SELECT * FROM mnt_machines"):
            score, band = svc.machine_health(conn, m)
            assert 0 <= score <= 100
            assert band in ("good", "warn", "crit")
        # a stopped machine should score lower than a running one
        running = conn.execute("SELECT * FROM mnt_machines WHERE status='running' LIMIT 1").fetchone()
        stopped = conn.execute("SELECT * FROM mnt_machines WHERE status='stopped' LIMIT 1").fetchone()
        if running and stopped:
            assert svc.machine_health(conn, stopped)[0] <= svc.machine_health(conn, running)[0]
    finally:
        conn.close()


def test_offline_ai_risk(app_ctx):
    conn = get_db()
    try:
        ranking = ai_engine.risk_ranking(conn)
        assert len(ranking) >= 5
        for r in ranking:
            assert 0 <= r["risk"] <= 100
            assert r["band"] in ("high", "medium", "low")
            assert r["recommendation"][0].startswith("rec_")
        # ranking is sorted worst-first
        assert ranking == sorted(ranking, key=lambda x: x["risk"], reverse=True)
    finally:
        conn.close()


def test_offline_ai_reorder(app_ctx):
    conn = get_db()
    try:
        recs = ai_engine.reorder_recommendations(conn)
        codes = {r["code"] for r in recs}
        # SP-005 (blade) seed stock 3 <= reorder 8 -> should be recommended
        assert "SP-005" in codes
        for r in recs:
            assert r["suggested_qty"] >= 0
    finally:
        conn.close()


def test_offline_ai_root_cause_suggestion(app_ctx):
    conn = get_db()
    try:
        m1 = conn.execute("SELECT id FROM mnt_machines WHERE code='M-001'").fetchone()["id"]
        s = ai_engine.suggest_root_cause(conn, m1, "mechanical")
        # seed diagnosis on M-001 has root_cause 'wear_tear'
        assert s["scope"] == "machine"
        assert any(c[0] == "wear_tear" for c in s["causes"])
    finally:
        conn.close()


def test_offline_ai_triage(app_ctx):
    # safety + stoppage keywords -> high/critical; benign -> low
    hot = ai_engine.triage("Fire and smoke from the motor, machine stopped",
                           machine_criticality="critical", production_stopped=True, safety_impact=True)
    assert hot["priority"] in ("critical", "high")
    assert "smoke" in hot["tags"] and "motor" in hot["tags"]
    calm = ai_engine.triage("minor cosmetic scratch on cover", machine_criticality="low")
    assert calm["priority"] in ("low", "medium")


def test_offline_ai_recommend_technician(app_ctx):
    conn = get_db()
    try:
        recs = ai_engine.recommend_technician(conn, "mechanical")
        assert recs and "name" in recs[0]
        assert all("open_load" in r and "skill" in r for r in recs)
    finally:
        conn.close()


def test_offline_ai_estimate_repair(app_ctx):
    conn = get_db()
    try:
        m = conn.execute("SELECT id FROM mnt_machines WHERE code='M-001'").fetchone()["id"]
        est = ai_engine.estimate_repair_time(conn, m, "mechanical")
        assert "minutes" in est and est["basis"] in ("machine", "category", "global", "diagnosis", "none")
    finally:
        conn.close()


def test_triage_endpoint(app_ctx):
    app = app_ctx
    app.config["TESTING"] = True
    from app.routes import auth as auth_routes
    auth_routes._fails.clear()
    with app.test_client() as c:
        c.get("/login")
        with c.session_transaction() as s:
            tok = s.get("_csrf_token", "")
        c.post("/login", data={"username": "supervisor", "password": "Tc@12345", "_csrf": tok})
        r = c.get("/maintenance/ai/triage?desc=fire%20and%20smoke&stopped=1&safety=1")
        assert r.status_code == 200
        data = r.get_json()
        assert data["priority"] in ("critical", "high")


def test_offline_ai_alternatives(app_ctx):
    conn = get_db()
    try:
        sp = conn.execute("SELECT id, category FROM mnt_spare_parts WHERE code='SP-002'").fetchone()
        alts = ai_engine.alternative_parts(conn, sp["id"])
        assert alts, "expected at least one in-stock substitute"
        for a in alts:
            assert a["id"] != sp["id"]
            assert a["stock"] > 0
    finally:
        conn.close()


def test_low_stock_alerts(app_ctx):
    conn = get_db()
    try:
        svc.sync_stock_alerts(conn)
        # SP-005 seed stock 3 <= reorder 8 -> should raise a Low stock alert
        sp = conn.execute("SELECT id FROM mnt_spare_parts WHERE code='SP-005'").fetchone()
        alert = conn.execute(
            "SELECT id FROM mnt_notifications WHERE entity_type='spare' AND entity_id=? "
            "AND is_read=0 AND title LIKE 'Low stock:%'", (sp["id"],)).fetchone()
        assert alert is not None
        # restock above reorder and re-sync -> alert auto-resolves
        conn.execute("UPDATE mnt_spare_parts SET stock_qty=100 WHERE id=?", (sp["id"],))
        conn.commit()
        svc.sync_stock_alerts(conn)
        still = conn.execute(
            "SELECT id FROM mnt_notifications WHERE entity_type='spare' AND entity_id=? "
            "AND is_read=0 AND title LIKE 'Low stock:%'", (sp["id"],)).fetchone()
        assert still is None
        # restore seed
        conn.execute("UPDATE mnt_spare_parts SET stock_qty=3 WHERE id=?", (sp["id"],))
        conn.execute("DELETE FROM mnt_notifications WHERE entity_type='spare' AND entity_id=?", (sp["id"],))
        conn.commit()
    finally:
        conn.close()


def test_ai_core_primitives(app_ctx):
    import app.ai_core as ac
    assert round(ac.forecast_next([1, 2, 3, 4])) == 5
    assert ac.sla_risk(6, 4) == 100 and ac.sla_risk(0, 4) == 0
    assert ac.zscore_anomalies([5, 5, 5, 5, 5, 5, 5, 50], 2.0)  # clear spike detected
    label, score = ac.classify_text("fire and smoke", {"safety": ["fire", "smoke"], "noise": ["noise"]})
    assert label == "safety" and score == 2


def test_executive_summary(app_ctx):
    from app.insights import executive_summary
    s = executive_summary()
    assert "insights" in s and "headline" in s
    assert s["headline"] in ("sum_attention", "sum_watch", "sum_healthy")


def test_platform_search_finds_machine(app_ctx):
    app = app_ctx
    app.config["TESTING"] = True
    from app.routes import auth as auth_routes
    auth_routes._fails.clear()
    with app.test_client() as c:
        c.get("/login")
        with c.session_transaction() as s:
            tok = s.get("_csrf_token", "")
        c.post("/login", data={"username": "maint", "password": "Tc@12345", "_csrf": tok})
        data = c.get("/search?q=M-001").get_json()
        assert any(r["type"] == "machine" for r in data["results"])


def test_sla_risk_engine(app_ctx):
    conn = get_db()
    try:
        sla = ai_engine.open_tickets_sla(conn)
        assert isinstance(sla, list)
        for t in sla:
            assert 0 <= t["risk"] <= 100
    finally:
        conn.close()


def test_excel_import_and_pdf(app_ctx):
    import io
    import openpyxl
    app = app_ctx
    app.config["TESTING"] = True
    from app.routes import auth as auth_routes
    auth_routes._fails.clear()
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["code", "name", "type", "brand", "department", "area", "line_no", "criticality", "status"])
    ws.append(["M-IMP-1", "Imported", "Sewing", "Juki", "Prod", "Hall A", "L1", "high", "running"])
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    with app.test_client() as c:
        c.get("/login")
        with c.session_transaction() as s:
            tok = s.get("_csrf_token", "")
        c.post("/login", data={"username": "admin", "password": "Admin@12345", "_csrf": tok})
        # login clears the session -> fetch a fresh CSRF token
        c.get("/maintenance/import")
        with c.session_transaction() as s:
            tok = s.get("_csrf_token", "")
        # template download
        assert c.get("/maintenance/import/template/machines.xlsx").status_code == 200
        # import
        r = c.post("/maintenance/import/machines",
                   data={"_csrf": tok, "file": (buf, "m.xlsx")},
                   content_type="multipart/form-data", follow_redirects=True)
        assert r.status_code == 200
        # PDF export
        pdf = c.get("/maintenance/reports/pdf/inventory.pdf")
        assert pdf.status_code == 200 and pdf.headers["Content-Type"] == "application/pdf"
    conn = get_db()
    row = conn.execute("SELECT id FROM mnt_machines WHERE code='M-IMP-1'").fetchone()
    assert row is not None
    conn.execute("DELETE FROM mnt_machines WHERE code='M-IMP-1'")
    conn.commit()
    conn.close()


def test_ai_page_loads(app_ctx):
    app = app_ctx
    app.config["TESTING"] = True
    from app.routes import auth as auth_routes
    auth_routes._fails.clear()
    with app.test_client() as c:
        c.get("/login")
        with c.session_transaction() as s:
            tok = s.get("_csrf_token", "")
        c.post("/login", data={"username": "maint", "password": "Tc@12345", "_csrf": tok})
        assert c.get("/maintenance/ai").status_code == 200


def test_floor_and_scan_pages(app_ctx):
    app = app_ctx
    app.config["TESTING"] = True
    from app.routes import auth as auth_routes
    auth_routes._fails.clear()
    with app.test_client() as c:
        c.get("/login")
        with c.session_transaction() as s:
            tok = s.get("_csrf_token", "")
        c.post("/login", data={"username": "tech", "password": "Tc@12345", "_csrf": tok})
        assert c.get("/maintenance/floor").status_code == 200
        assert c.get("/maintenance/scan").status_code == 200
        # scan with a known machine code redirects to a pre-filled new ticket
        r = c.get("/maintenance/scan?code=M-001", follow_redirects=False)
        assert r.status_code == 302 and "tickets/new" in r.headers["Location"]
