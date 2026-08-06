# -*- coding: utf-8 -*-
"""
Machine register import — proof by execution.

Run:  python app/maintenance/tests_machine_import.py

EVERY row here is SYNTHETIC and built at runtime. The real register holds serial
numbers, prices and photographs and THIS REPO IS PUBLIC — no file from
ramazan_analysis_output is ever read, referenced or committed.

Proves:
  a) parser  — Turkish/Arabic survive, a BOM is handled, a malformed row is
     rejected WITH a reason, a serial-less row is keyed CARD- and flagged.
  b) upsert  — idempotent, a changed value updates, a blank never clobbers an
     admin edit, codes stay unique, the three new columns round-trip and
     meter_reading holds 4,998,129 exactly.
  c) perms   — a non-maint_admin gets a RAW 403 on GET and on POST and NOTHING
     is written (row counts asserted before and after).
  d) boot    — create_and_seed(conn) x3 adds no rows and re-runs no migration.
  e) i18n    — the page renders 200 in en/ar/tr and every data-i18n key on it
     resolves in ALL THREE dictionaries (a missing key renders as the literal
     key string to every user — this has shipped as a bug five times).
"""
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="wf_mimp_"))
os.chdir(TMP)
REPO = r"D:\TC platform\tc-platform-render"
sys.path.insert(0, REPO)
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                                        # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                                           # noqa: E402
from app.db import get_db                                            # noqa: E402
from app.maintenance.schema import create_and_seed                   # noqa: E402
from app.maintenance.machine_import import (                         # noqa: E402
    parse_machines, upsert_machines, machine_stats, compose_remarks, I18N)

app = create_app()
PASS, FAIL = [], []


def ck(label, cond, extra=""):
    (PASS if cond else FAIL).append(label)
    print(("  ok   " if cond else "  FAIL ") + label
          + ((" -> " + str(extra)) if extra and not cond else ""))


def db():
    return get_db()


# Synthetic register. Turkish (İĞNE, DÜZ DİKİŞ), Arabic (ماكينة), a BOM, a row
# with no serial and no card, and a duplicated card slot.
HEADER = ("serial_standardized,asset_code_standardized,name,brand_standardized,"
          "model_standardized,line_standardized,needle_system_standardized,"
          "power_kw_standardized,country_of_manufacture_standardized,"
          "purchase_price_standardized,arrival_year_standardized,"
          "in_current_register_2023,meter_reading,meter_reading_at,status,remarks,"
          "source_file")


def csv_text(rows, bom=True):
    return ("\ufeff" if bom else "") + HEADER + "\n" + "\n".join(rows) + "\n"


ROWS = [
    # serial, card, name (Turkish), brand, model, line, needle, kw, country, price, year, 2023, meter, at, status, remarks, src
    'SYNTH-0001,CI-1,DÜZ DİKİŞ MAKİNESİ İĞNE,JUKI,DDL-8700,HAZIRLIK,DPX17,0.55,JAPAN,1250.5,2015,YES,4998129,2026-01-31,running,,REG-A',
    'SYNTH-0002,CI-1,ماكينة الحياكة,BROTHER,S-7300A,ÜRETİM,DCX27,0.4,CHINA,900,2018,NO,12,2026-01-31,decommissioned,,REG-B',
    ',CARD-SLOT-9,OVERLOK,PEGASUS,M900,HAT-3,DPX5 SUK,,TURKEY,,,YES,,,running,,REG-A',      # no serial -> CARD-
    ',,MAKİNA (KAYIP),,,ÜRETİM,,,,,,YES,,,running,,REG-B',                                    # nothing to key on
    'SYNTH-0001,CI-77,DUPLICATE SERIAL,JUKI,DDL-8700,HAZIRLIK,DPX17,,,,,YES,,,running,,REG-A',  # dup key
]

print("=====================================================================")
print("(a) parser")
print("=====================================================================")
rows, st = parse_machines(io.BytesIO(csv_text(ROWS).encode("utf-8")))
ck("BOM handled and header found", st["error"] is None, st["error"])
ck("3 machines parsed from 5 data rows", st["machines"] == 3 and len(rows) == 3,
   (st["machines"], len(rows)))
by = {r["code"]: r for r in rows}
ck("keyed on the serial, not the card", "SN-SYNTH-0001" in by and "SN-SYNTH-0002" in by,
   sorted(by))
ck("Turkish text survives", by["SN-SYNTH-0001"]["name"] == "DÜZ DİKİŞ MAKİNESİ İĞNE",
   by["SN-SYNTH-0001"]["name"])
ck("Arabic text survives", by["SN-SYNTH-0002"]["name"] == "ماكينة الحياكة",
   by["SN-SYNTH-0002"]["name"])
ck("serial-less row is keyed CARD-", "CARD-CARD-SLOT-9" in by, sorted(by))
ck("serial-less row is FLAGGED",
   by["CARD-CARD-SLOT-9"]["no_serial"] is True and st["no_serial"] == 1
   and "CARD-CARD-SLOT-9" in st["flagged"], st["flagged"])
reasons = {r["row"]: r["reason"] for r in st["rejects"]}
ck("2 rejects, each WITH a reason",
   len(st["rejects"]) == 2 and all(r["reason"] for r in st["rejects"]), st["rejects"])
ck("a malformed row (no serial, no card) is rejected for having nothing to key on",
   any("nothing to key on" in r for r in reasons.values()), reasons)
ck("a truly blank CSV line is skipped, not counted as data",
   parse_machines(io.BytesIO(csv_text(ROWS + [",,,,,,,,,,,,,,,,"]).encode("utf-8")))[1]["rows"]
   == st["rows"])
ck("duplicate serial rejected as a duplicate key",
   any("duplicate key" in r for r in reasons.values()), reasons)
ck("same card on two rows does NOT merge them (card is a slot)",
   by["SN-SYNTH-0001"]["legacy_card_no"] == "CI-1"
   and by["SN-SYNTH-0002"]["legacy_card_no"] == "CI-1"
   and by["SN-SYNTH-0001"]["code"] != by["SN-SYNTH-0002"]["code"])
ck("in_register_2023 read as 1 / 0",
   by["SN-SYNTH-0001"]["in_register_2023"] == 1
   and by["SN-SYNTH-0002"]["in_register_2023"] == 0 and st["in_register"] == 2,
   st["in_register"])
line = " ".join(by["SN-SYNTH-0001"]["remarks_parts"])
ck("the four unmapped fields compose into ONE readable line",
   "power_kw=0.55" in line and "country_of_origin=JAPAN" in line
   and "arrival_year=2015" in line, line)
ck("price carries USD explicitly", "purchase_price=1250.50 USD" in line, line)
ck("source register recorded", "source_register=REG-A" in line, line)
ck("a source 'decommissioned' is recorded, NOT applied",
   "source_status=decommissioned" in " ".join(by["SN-SYNTH-0002"]["remarks_parts"]))
ck("no-BOM file parses identically",
   parse_machines(io.BytesIO(csv_text(ROWS, bom=False).encode("utf-8")))[1]["machines"] == 3)
ck("a file with no usable header is an error, not a crash",
   parse_machines(io.BytesIO("a,b,c\n1,2,3\n".encode("utf-8")))[1]["error"] is not None)
ck("an empty file is an error, not a crash",
   parse_machines(io.BytesIO(b""))[1]["error"] is not None)
ck("compose_remarks keeps an admin note and replaces only our line",
   compose_remarks("hand note\n[machine-import] old=1", ["new=2"])
   == "hand note\n[machine-import] new=2",
   compose_remarks("hand note\n[machine-import] old=1", ["new=2"]))

print("\n=====================================================================")
print("(b) upsert")
print("=====================================================================")
with app.app_context():
    c = db()
    try:
        base = c.execute("SELECT COUNT(*) n FROM mnt_machines").fetchone()["n"]
        r1 = upsert_machines(c, rows, {"username": "admin"})
        ck("first run adds every parsed machine",
           r1 == {"added": 3, "updated": 0, "unchanged": 0, "rejected": 0}, r1)
        rows2, _ = parse_machines(io.BytesIO(csv_text(ROWS).encode("utf-8")))
        r2 = upsert_machines(c, rows2, {"username": "admin"})
        ck("IDEMPOTENT: same file twice -> 0 added, 0 updated",
           r2 == {"added": 0, "updated": 0, "unchanged": 3, "rejected": 0}, r2)
        ck("no duplicate rows created",
           c.execute("SELECT COUNT(*) n FROM mnt_machines").fetchone()["n"] == base + 3)
        ck("codes are unique",
           c.execute("SELECT COUNT(*) n FROM (SELECT code FROM mnt_machines "
                     "GROUP BY code HAVING COUNT(*)>1) x").fetchone()["n"] == 0)

        m = c.execute("SELECT * FROM mnt_machines WHERE code='SN-SYNTH-0001'").fetchone()
        ck("needle_system round-trips", m["needle_system"] == "DPX17", m["needle_system"])
        ck("meter_reading holds 4,998,129 with no precision loss",
           float(m["meter_reading"]) == 4998129.0, m["meter_reading"])
        ck("meter_reading_at round-trips", m["meter_reading_at"] == "2026-01-31",
           m["meter_reading_at"])
        ck("legacy_card_no round-trips", m["legacy_card_no"] == "CI-1", m["legacy_card_no"])
        ck("in_register_2023 round-trips", int(m["in_register_2023"]) == 1)
        ck("everything imports ACTIVE (the owner's decision)",
           int(m["is_active"]) == 1 and m["status"] == "running",
           (m["is_active"], m["status"]))
        d = c.execute("SELECT * FROM mnt_machines WHERE code='SN-SYNTH-0002'").fetchone()
        ck("a source 'decommissioned' row is still ACTIVE but marked in remarks",
           int(d["is_active"]) == 1 and d["status"] == "running"
           and "source_status=decommissioned" in (d["remarks"] or ""),
           (d["status"], d["remarks"]))
        ck("'current fleet only' is answerable as a filter",
           c.execute("SELECT COUNT(*) n FROM mnt_machines WHERE in_register_2023=1 "
                     "AND code LIKE 'SN-%'").fetchone()["n"] == 1)

        # A changed value updates.
        changed = list(ROWS)
        changed[0] = changed[0].replace("DPX17", "DPX5", 1).replace("4998129", "5000000", 1)
        rows3, _ = parse_machines(io.BytesIO(csv_text(changed).encode("utf-8")))
        r3 = upsert_machines(c, rows3, {"username": "admin"})
        ck("a changed value updates exactly one row",
           r3["updated"] == 1 and r3["added"] == 0, r3)
        m = c.execute("SELECT * FROM mnt_machines WHERE code='SN-SYNTH-0001'").fetchone()
        ck("the change landed",
           m["needle_system"] == "DPX5" and float(m["meter_reading"]) == 5000000.0,
           (m["needle_system"], m["meter_reading"]))

        # An admin edit is not clobbered by a blank incoming cell.
        c.execute("UPDATE mnt_machines SET name=? WHERE code=?",
                  ("ADMIN CORRECTED NAME", "CARD-CARD-SLOT-9"))
        c.commit()
        blank = list(ROWS)
        blank[2] = blank[2].replace("OVERLOK", "", 1)
        rows4, _ = parse_machines(io.BytesIO(csv_text(blank).encode("utf-8")))
        upsert_machines(c, rows4, {"username": "admin"})
        got = c.execute("SELECT name FROM mnt_machines WHERE code='CARD-CARD-SLOT-9'"
                        ).fetchone()["name"]
        ck("a blank incoming cell never clobbers an admin edit",
           got == "ADMIN CORRECTED NAME", got)

        s = machine_stats(c)
        ck("machine_stats counts total / current register / card-keyed",
           s["total"] >= 3 and s["in_register"] >= 1 and s["no_serial"] >= 1, s)
    finally:
        c.close()

print("\n=====================================================================")
print("(c) permissions — a non-admin writes nothing")
print("=====================================================================")
with app.app_context():
    c = db()
    try:
        row = c.execute("SELECT id FROM users WHERE role=? LIMIT 1",
                        ("maintenance_technician",)).fetchone()
        tech_id = row["id"] if row else None
        before = c.execute("SELECT COUNT(*) n FROM mnt_machines").fetchone()["n"]
    finally:
        c.close()
ck("a maintenance_technician (no maint_admin) exists to test with", tech_id is not None)

with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = tech_id
        s["ep"] = 0
    r = cl.get("/maintenance/import?kind=register")
    ck("non-admin GET /maintenance/import == RAW 403", r.status_code == 403, r.status_code)
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = cl.post("/maintenance/import",
                data={"_csrf": tok,
                      "file": (io.BytesIO(csv_text(
                          ['HACK-1,X,HACKED,,,,,,,,,,,,running,,']).encode("utf-8")),
                          "evil.csv")},
                content_type="multipart/form-data")
    ck("non-admin POST /maintenance/import == RAW 403", r.status_code == 403, r.status_code)
with app.app_context():
    c = db()
    try:
        after = c.execute("SELECT COUNT(*) n FROM mnt_machines").fetchone()["n"]
        hacked = c.execute("SELECT COUNT(*) n FROM mnt_machines WHERE code LIKE 'SN-HACK%'"
                           ).fetchone()["n"]
    finally:
        c.close()
ck("NOTHING was written by the refused requests", after == before and hacked == 0,
   (before, after, hacked))

# ... and the same POST from an admin goes through and shows the four counts.
with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    cl.get("/maintenance/import?kind=register")     # mints the CSRF token
    with cl.session_transaction() as s:
        tok = s.get("_csrf_token")
    payload = csv_text(['SYNTH-9001,CI-9,WEB UPLOAD,JUKI,DDL-9000,HAT-1,DPX17,'
                        ',,,,YES,7,2026-02-01,running,,REG-A'])
    r = cl.post("/maintenance/import",
                data={"_csrf": tok,
                      "file": (io.BytesIO(payload.encode("utf-8")), "register.csv")},
                content_type="multipart/form-data")
    ck("admin POST /maintenance/import == 200", r.status_code == 200, r.status_code)
    body = r.get_data(as_text=True)
    ck("the page reports added / updated / unchanged / rejected",
       all(k in body for k in ("cat.added", "cat.updated", "cat.unchanged", "cat.rejected")))
    r2 = cl.post("/maintenance/import",
                 data={"_csrf": tok,
                       "file": (io.BytesIO(payload.encode("utf-8")), "register.csv")},
                 content_type="multipart/form-data")
    ck("re-posting the same file over HTTP is idempotent too", r2.status_code == 200,
       r2.status_code)
with app.app_context():
    c = db()
    try:
        n = c.execute("SELECT COUNT(*) n FROM mnt_machines WHERE code='SN-SYNTH-9001'"
                      ).fetchone()["n"]
        aud = c.execute("SELECT COUNT(*) n FROM mnt_audit WHERE action='import'"
                        ).fetchone()["n"]
    finally:
        c.close()
ck("the uploaded machine landed exactly once", n == 1, n)
ck("the import is audited", aud >= 1, aud)

print("\n=====================================================================")
print("(d) boot — create_and_seed x3 is a no-op")
print("=====================================================================")
with app.app_context():
    c = db()
    try:
        n0 = {t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
              for t in ("mnt_machines", "mnt_spare_parts", "mnt_tickets", "mnt_settings")}
        for _ in range(3):
            create_and_seed(c)
        n1 = {t: c.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"] for t in n0}
        ck("create_and_seed(conn) x3 adds no rows", n0 == n1, (n0, n1))
        cols = [r[1] for r in c.execute("PRAGMA table_info(mnt_machines)").fetchall()]
        for col in ("needle_system", "meter_reading", "meter_reading_at",
                    "legacy_card_no", "in_register_2023"):
            ck(f"column {col} exists exactly once",
               cols.count(col) == 1, cols.count(col))
        ck("the imported data survived three boots",
           c.execute("SELECT COUNT(*) n FROM mnt_machines WHERE code IN "
                     "('SN-SYNTH-0001','SN-SYNTH-0002','CARD-CARD-SLOT-9','SN-SYNTH-9001')"
                     ).fetchone()["n"] == 4)
    finally:
        c.close()

from app.maintenance import schema as _schema                        # noqa: E402
ck("exactly 5 migration statements added for this feature",
   len([m for m in _schema._MIGRATIONS if m[0] == "mnt_machines"]) == 5,
   [m[1] for m in _schema._MIGRATIONS if m[0] == "mnt_machines"])

print("\n=====================================================================")
print("(e) i18n — every data-i18n key resolves in en, ar AND tr")
print("=====================================================================")
DICT = {}
for lang in ("en", "ar", "tr"):
    with io.open(os.path.join(REPO, "app", "static", "i18n", f"{lang}.json"),
                 encoding="utf-8") as fh:
        DICT[lang] = json.load(fh)

with app.test_client() as cl:
    with cl.session_transaction() as s:
        s["uid"] = 1
        s["ep"] = 0
    bodies = {}
    for lang in ("en", "ar", "tr"):
        with app.app_context():
            c = db()
            try:
                c.execute("UPDATE users SET lang_pref=? WHERE id=1", (lang,))
                c.commit()
            finally:
                c.close()
        r = cl.get("/maintenance/import?kind=register")
        ck(f"admin GET /maintenance/import?kind=register in {lang} == 200",
           r.status_code == 200, r.status_code)
        bodies[lang] = r.get_data(as_text=True)

keys = sorted(set(re.findall(r'data-i18n="([^"]+)"', bodies["en"])))
ck("the register page actually carries data-i18n keys", len(keys) > 5, len(keys))
# app/static/i18n/** is the orchestrator's file, so the check is against the
# dictionary AS IT WILL BE once I18N is merged: live keys + the new ones this
# module ships. A key present in neither is the bug that has shipped five times.
MERGED = {lang: dict(DICT[lang], **{k: v[i] for k, v in I18N.items()})
          for i, lang in enumerate(("en", "ar", "tr"))}
for lang in ("en", "ar", "tr"):
    gone = [k for k in keys if k not in MERGED[lang]]
    ck(f"every data-i18n key on the page resolves in {lang} (live + new keys)",
       not gone, gone)
    ck(f"no {lang} value is just the key echoed back",
       all(MERGED[lang][k] != k for k in keys), [k for k in keys if MERGED[lang][k] == k])

new_keys = sorted(I18N)
ck("every NEW key ships real EN + AR + TR text (no key echoed back)",
   all(len(v) == 3 and all(isinstance(x, str) and x.strip() and x.strip() != k
                           for x in v) for k, v in I18N.items()),
   [k for k, v in I18N.items() if not all(str(x).strip() for x in v)])
ck("the AR text is actually Arabic script",
   all(re.search(r"[\u0600-\u06FF]", v[1]) for v in I18N.values()),
   [k for k, v in I18N.items() if not re.search(r"[\u0600-\u06FF]", v[1])])
still_needed = [k for k in new_keys if k not in DICT["en"]]
print(f"  note: {len(still_needed)} new key(s) still to be merged into "
      f"app/static/i18n/** by the orchestrator: {still_needed}")
ck("every data-i18n key on the page is either already live or in I18N",
   all(k in DICT["en"] or k in I18N for k in keys),
   [k for k in keys if k not in DICT["en"] and k not in I18N])
ck("machine names render as DATA, not through data-i18n",
   "DÜZ DİKİŞ" not in " ".join(keys))

print("\n=====================================================================")
print(f"PASS {len(PASS)}   FAIL {len(FAIL)}")
for f in FAIL:
    print("  FAILED: " + f)
print("=====================================================================")
sys.exit(1 if FAIL else 0)
