"""Warehouse material import self-test against an isolated throwaway database.

Run: python -m app.warehouse.tests_material_import
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
TMP = Path(tempfile.mkdtemp(prefix="wh_import_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                             # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  [{extra}]" if extra else ""))
    if not cond:
        FAILS.append(name)


app = create_app()
with app.app_context():
    from app.db import get_db
    from app.warehouse import services as svc
    from app.warehouse.constants import IMPORT_COLUMNS

    U = {"username": "import-selftest"}

    def row(code, **values):
        return dict(dict.fromkeys(IMPORT_COLUMNS, ""), code=code, line=2, **values)

    def material(code):
        conn = get_db()
        try:
            found = conn.execute("SELECT * FROM wh_materials WHERE code=?", (code,)).fetchone()
            return dict(found) if found else None
        finally:
            conn.close()

    def movements(mid):
        conn = get_db()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM wh_movements WHERE material_id=?", (mid,)).fetchall()]
        finally:
            conn.close()

    trim = row(" imp-trim ", name="Buttons", kind="trim", uom="pcs", color="Blue",
               composition="Polyester", supplier="Supplier", width_cm="18", opening_qty="500")
    preview = svc.import_materials([trim], U)
    check("dry-run reports opening but writes nothing", preview["opening_total"] == 500
          and material("IMP-TRIM") is None)
    result = svc.import_materials([trim], U, commit=True)
    mat = material("IMP-TRIM")
    moves = movements(mat["id"])
    check("new trim opening stock and ledger agree", len(result["added"]) == 1
          and mat["stock_qty"] == 500 and len(moves) == 1 and moves[0]["type"] == "opening"
          and moves[0]["qty"] == 500 and moves[0]["before_qty"] == 0 and moves[0]["after_qty"] == 500
          and moves[0]["ref"] == "IMPORT" and moves[0]["performed_by"] == U["username"])
    result = svc.import_materials([row("IMP-ROLL", roll_tracked="yes", opening_qty="10")], U, True)
    check("roll-tracked opening rejected without material", len(result["rejected"]) == 1
          and "Receive page" in result["rejected"][0]["reason"] and material("IMP-ROLL") is None)
    result = svc.import_materials([row("IMP-TRIM", name="New buttons", opening_qty="40")], U, True)
    check("live opening ignored while updates apply", len(result["updated"]) == 1
          and "opening ignored" in result["updated"][0]["note"] and result["opening_total"] == 0
          and material("IMP-TRIM")["stock_qty"] == 500 and len(movements(mat["id"])) == 1)
    before = material("IMP-TRIM")
    result = svc.import_materials([row("IMP-TRIM")], U, True)
    check("blank cells preserve every existing value", len(result["unchanged"]) == 1
          and material("IMP-TRIM") == before)
    result = svc.import_materials([row("IMP-DUP"), row(" imp-dup ")], U, True)
    check("every duplicate occurrence rejected", len(result["rejected"]) == 2
          and all(r["reason"] == "duplicate code in file" for r in result["rejected"])
          and material("IMP-DUP") is None)
    for key, value in (("kind", "bad"), ("uom", "bad"), ("width_cm", "oops"),
                       ("opening_qty", "oops"), ("width_cm", "-1"), ("opening_qty", "-1"),
                       ("opening_qty", "1e400"), ("width_cm", "nan"), ("roll_tracked", "maybe"),
                       ("name", "x" * 201), ("color", "x" * 201),
                       ("composition", "x" * 201), ("supplier", "x" * 201)):
        result = svc.import_materials([row("IMP-BAD", **{key: value})], U, True)
        check(f"reject {key}={value[:12]}", len(result["rejected"]) == 1 and material("IMP-BAD") is None)
    for key, value in (("roll_tracked", "yes"), ("uom", "kg")):
        result = svc.import_materials([row("IMP-TRIM", name="Must not land", **{key: value})], U, True)
        check(f"history blocks {key} changes atomically", len(result["rejected"]) == 1
              and "stock history" in result["rejected"][0]["reason"] and material("IMP-TRIM") == before)
    result = svc.import_materials([row("IMP-TRIM", name="New buttons", opening_qty="500")], U, True)
    check("second identical run unchanged", len(result["unchanged"]) == 1
          and material("IMP-TRIM")["stock_qty"] == 500)
    result = svc.import_materials([row("IMP-MIX-OK", opening_qty="3"),
                                   row("IMP-MIX-BAD", kind="invalid"), row("IMP-MIX-OK2")], U, True)
    check("mixed file lands only valid rows", len(result["added"]) == 2
          and len(result["rejected"]) == 1 and material("IMP-MIX-OK")["stock_qty"] == 3
          and material("IMP-MIX-OK2") is not None and material("IMP-MIX-BAD") is None)
    result = svc.import_materials([row("IMP-DEC", opening_qty="1,250")], U, True, decimal=",")
    check("decimal comma hint preserved", result["opening_total"] == 1.25)
    result = svc.import_materials([row(""), row("", name="Missing code")], U, True)
    check("blank rows skipped, missing code rejected", len(result["rejected"]) == 1)
    # Rows are validated before any write, so a stock-writer refusal is a bug: the whole file aborts.
    with patch.object(svc, "_move_stock", return_value=(False, "test refusal")):
        try:
            svc.import_materials([row("IMP-SURVIVE"), row("IMP-REFUSE", opening_qty="5")], U, True)
            check("movement refusal raises", False)
        except RuntimeError:
            check("movement refusal rolls back whole file", material("IMP-REFUSE") is None
                  and material("IMP-SURVIVE") is None)
    svc.import_materials([row("IMP-SURVIVE")], U, True)
    with patch.object(svc, "_move_stock", side_effect=RuntimeError("test failure")):
        try:
            svc.import_materials([row("IMP-ATOMIC"), row("IMP-CRASH", opening_qty="5")], U, True)
            check("unexpected exception re-raised", False)
        except RuntimeError:
            check("unexpected exception rolls back whole file", material("IMP-ATOMIC") is None
                  and material("IMP-CRASH") is None)
    conn = get_db()
    conn.execute("UPDATE wh_materials SET uom=? WHERE code=?", ("Legacy Unit", "IMP-SURVIVE"))
    conn.commit()
    conn.close()
    result = svc.import_materials([row("IMP-SURVIVE", uom="legacy unit")], U, True)
    check("legacy unit round-trip is unchanged", len(result["unchanged"]) == 1)
    svc.import_materials([row("IMP-EMPTY")], U, True)
    result = svc.import_materials([row("IMP-EMPTY", opening_qty="2")], U)
    check("existing empty material preview rolls back opening", result["opening_total"] == 2
          and material("IMP-EMPTY")["stock_qty"] == 0 and not movements(material("IMP-EMPTY")["id"]))
    result = svc.import_materials([row("IMP-EMPTY", opening_qty="2")], U, True)
    check("existing empty material accepts opening", result["opening_total"] == 2
          and len(result["updated"]) == 1
          and material("IMP-EMPTY")["stock_qty"] == 2)

    conn = get_db()
    admin = conn.execute("SELECT * FROM users WHERE role IN ('admin','super_admin') AND is_active=1 "
                         "ORDER BY id LIMIT 1").fetchone()
    conn.close()
    if not admin:
        print("  skip HTTP checks: no seeded active admin/super_admin user")
    else:
        client = app.test_client()
        token = "t" * 64
        with client.session_transaction() as session:
            session["uid"] = admin["id"]
            session["ep"] = admin["session_epoch"] or 0
            session["_csrf_token"] = token
        url = "/warehouse/materials/import"
        response = client.get(url)
        check("authorized import GET", response.status_code == 200)
        response = client.get(url + "/template.xlsx")
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(response.data))
        check("template downloads valid xlsx", response.status_code == 200
              and list(next(workbook.active.values)) == IMPORT_COLUMNS)
        workbook.close()
        response = client.post(url, data={"_csrf": token, "action": "preview", "file": (
            io.BytesIO(b'\nName;Opening Qty;Code;Kind;UOM\nHTTP buttons;1,250;IMP-HTTP;trim;pcs\n'), "test.csv")})
        check("HTTP preview binds headers and writes nothing", response.status_code == 200
              and b'IMP-HTTP' in response.data and material("IMP-HTTP") is None)
        from html import unescape
        import re
        match = re.search(r'name="payload" value="([^"]*)"', response.get_data(as_text=True))
        check("preview carries confirm payload", match is not None)
        if match:
            payload = unescape(match.group(1))
            check("payload preserves spreadsheet line and decimal", json.loads(payload)["rows"][0]["line"] == 3
                  and json.loads(payload)["decimal"] == ",")
            response = client.post(url, data={"_csrf": token, "action": "confirm", "payload": payload})
            check("HTTP confirm books opening and redirects", response.status_code == 302
                  and response.location.endswith("/warehouse/materials")
                  and material("IMP-HTTP")["stock_qty"] == 1.25)
        for payload in ("bad json", "[]", '{"rows":[1]}', json.dumps({"rows": [row("X")] * 5001}),
                        json.dumps({"rows": [{"line": 2, "code": ["bad"]}]})):
            response = client.post(url, data={"_csrf": token, "action": "confirm", "payload": payload})
            check("invalid confirm payload redirects", response.status_code == 302
                  and response.location.endswith("/materials/import"))

print(f"\n{len(FAILS)} failures: {', '.join(FAILS)}" if FAILS else "\nAll material import checks passed.")
sys.exit(1 if FAILS else 0)
