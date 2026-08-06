# -*- coding: utf-8 -*-
"""
TC Platform — Maintenance machine register import: ONE parser, two front doors.

`parse_machines` reads the consolidated machine register (CSV, utf-8 with or
without a BOM, Turkish + Arabic text) into plain dicts. It is deliberately PURE
— no database, no Flask — so the admin screen (/maintenance/import) and the CLI
(scripts/import_machines.py) cannot drift apart, and the tests can drive it
without an app.

The owner's decisions this file implements (they are business rules, not taste):

1. IDENTITY IS THE SERIAL. The key is `SN-<serial>`. The card number (FIRMA NO /
   asset code) is a SLOT on the shop floor, not an asset tag: 427 cards in the
   archive carry more than one serial because a card is re-issued to the machine
   that replaced the old one. Keying on the card would silently merge two
   physical machines into one row. The card is kept, searchable, in
   `legacy_card_no`. Where a record has NO serial at all it falls back to
   `CARD-<card no>` and is FLAGGED, so an admin can see which rows are keyed on
   a slot rather than on an asset.

2. EVERYTHING LOADS, ACTIVE, BUT REVERSIBLY. Every row imports with
   `status='running'`, `is_active=1` — the owner accepted that this is ~2.4x the
   machines the archive calls current. Nothing is filtered here. Instead
   `in_register_2023` records whether the row appears in the current register,
   so "show me only the current fleet" stays one WHERE clause away. A source
   status of 'decommissioned' is NOT applied (it is exactly the filter the owner
   declined) but it is written into remarks as `source_status=` so it is not
   lost.

3. PRICES ARE USD. Written as `purchase_price=1234.00 USD` — the currency is
   stated, never implied.

4. FOUR FIELDS HAVE NO COLUMN and are not getting one: power_kw,
   country_of_origin, purchase_price, arrival_year compose into ONE readable
   line in `remarks`, tagged `[machine-import]` so a re-import replaces that one
   line and leaves an admin's own notes alone.

Header handling: the consolidated export ships every field twice, as
`x_standardized` and `x_original`. `_original` columns are dropped and the
`_standardized` suffix is stripped, which maps the whole export in one rule
instead of fifty aliases. The shipped import-package CSV (plain column names)
lands through the same map.
"""
import csv
import hashlib
import io
import os
import re

# Every data-i18n key the register import panel introduces, with real EN/AR/TR.
# app/static/i18n/** is owned by the orchestrator, so this dict is the source it
# merges from — a key missing from en.json renders as the LITERAL key string to
# every user in every language, which is why tests_machine_import.py asserts
# each one resolves in en, ar AND tr.
I18N = {
    "m.reg_tab": ("Machine register (CSV)", "سجل الماكينات (CSV)", "Makine kaydı (CSV)"),
    "m.reg_h": ("Import the machine register",
                "استيراد سجل الماكينات",
                "Makine kaydını içe aktar"),
    "m.reg_hint": (
        "CSV export of the machine register. Machines are matched by serial number "
        "(SN-…); a record with no serial falls back to its card number (CARD-…) and is "
        "flagged below. A blank cell never overwrites a value edited here, and every "
        "row that cannot be imported is listed with its reason. Nothing is written "
        "until you press Import.",
        "ملف CSV مُصدَّر من سجل الماكينات. تتم مطابقة الماكينات بالرقم التسلسلي (‏SN-…)؛ "
        "والسجل الذي بلا رقم تسلسلي يُرحَّل برقم البطاقة (‏CARD-…) ويُعلَّم في الأسفل. "
        "الخانة الفارغة لا تستبدل أبداً قيمة تم تعديلها هنا، وكل صف تعذّر استيراده يُعرض "
        "مع سببه. لا يُكتب أي شيء قبل الضغط على استيراد.",
        "Makine kaydının CSV dosyası. Makineler seri numarasına göre eşleştirilir (SN-…); "
        "seri numarası olmayan kayıt kart numarasına göre aktarılır (CARD-…) ve aşağıda "
        "işaretlenir. Boş bir hücre burada düzenlenmiş bir değeri asla üzerine yazmaz ve "
        "içe aktarılamayan her satır gerekçesiyle listelenir. İçe aktar'a basmadan hiçbir "
        "şey yazılmaz."),
    "m.reg_on_file": ("Machines on file", "الماكينات المسجّلة", "Kayıtlı makineler"),
    "m.reg_current": ("In the current 2023 register", "ضمن سجل 2023 الحالي",
                      "Güncel 2023 kaydında"),
    "m.reg_no_serial": ("Keyed by card number (no serial)",
                        "مُرحَّلة برقم البطاقة (بلا رقم تسلسلي)",
                        "Kart numarasına göre (seri no yok)"),
    "m.reg_flagged_h": ("Rows keyed by card number", "صفوف مُرحَّلة برقم البطاقة",
                        "Kart numarasına göre aktarılan satırlar"),
    "m.reg_flagged_hint": (
        "These records carry no serial number, so the card number was used as the key. "
        "A card is a slot, not an asset tag — check these before trusting them as one "
        "machine each.",
        "هذه السجلات بلا رقم تسلسلي، لذلك استُخدم رقم البطاقة كمفتاح. البطاقة موقع وليست "
        "رقم أصل — راجعها قبل اعتبار كل منها ماكينة واحدة.",
        "Bu kayıtlarda seri numarası yok, bu yüzden anahtar olarak kart numarası "
        "kullanıldı. Kart bir yuvadır, varlık etiketi değildir — her birini tek bir makine "
        "saymadan önce kontrol edin."),
    # The machine registry page — 5,100 rows need a way in, and these are the two
    # provenance columns actually earning their keep (search the card, filter the
    # current fleet).
    "m.reg_search_ph": ("Code, name, serial or card number…",
                        "الكود أو الاسم أو الرقم التسلسلي أو رقم البطاقة…",
                        "Kod, ad, seri no veya kart numarası…"),
    "m.reg_search": ("Search", "بحث", "Ara"),
    "m.reg_clear": ("Clear", "مسح", "Temizle"),
    "m.reg_all": ("All machines", "كل الماكينات", "Tüm makineler"),
    "m.reg_only_current": ("Current fleet only (2023 register)",
                           "الأسطول الحالي فقط (سجل 2023)",
                           "Yalnızca güncel filo (2023 kaydı)"),
    "m.reg_shown": ("shown of matching machines", "معروضة من الماكينات المطابقة",
                    "eşleşen makineden gösteriliyor"),
    "m.reg_capped": ("narrow the search to see the rest", "ضيّق البحث لعرض الباقي",
                     "geri kalanı görmek için aramayı daraltın"),
    "m.reg_card": ("Card no (FIRMA NO)", "رقم البطاقة (FIRMA NO)",
                   "Kart no (FIRMA NO)"),
    "m.reg_needle": ("Needle system", "نظام الإبرة", "İğne sistemi"),
    "m.reg_meter": ("Meter reading", "قراءة العداد", "Sayaç okuması"),
    "m.reg_in_2023": ("In the 2023 register", "ضمن سجل 2023", "2023 kaydında"),
    "m.reg_yes": ("Yes", "نعم", "Evet"),
}

# The export ships each field twice. Drop the raw one, strip the suffix off the
# cleaned one; fifty columns map with two lines instead of fifty aliases.
_DROP_SUFFIX = "_original"
_STD_SUFFIX = "_standardized"

# What is left after that, onto our column names.
_ALIAS = {
    "machine_type": "type",
    "line": "line_no",
    "asset_code": "card_no",
    "firma_no": "card_no",
    "card_number": "card_no",
    "country_of_manufacture": "country_of_origin",
    "country": "country_of_origin",
    "in_current_register_2023": "in_register_2023",
    "source_file": "source_register",
    "meter": "meter_reading",
    "counter": "meter_reading",
    "cycle_count": "meter_reading",
    "meter_at": "meter_reading_at",
    "meter_date": "meter_reading_at",
    "needle": "needle_system",
}

# Text fields carried straight through. `remarks`, `name`, `serial` and
# `legacy_card_no` are handled separately (composed or derived).
_TEXT = ("type", "brand", "model", "department", "area", "line_no",
         "location", "install_date", "vendor", "needle_system", "meter_reading_at")

_MAX_CODE = 120          # a card ref in the archive can be a whole file path
_CHUNK = 200             # 200 rows x 23 params = 4,600 — under every driver's limit
_TAG = "[machine-import]"
# NOT `\b` after the tag: ']' and ' ' are both non-word characters, so there is no
# word boundary between them and the old line would survive — which made every
# re-import rewrite every remarks field and report 5,000 spurious updates.
_TAG_RE = re.compile(r"^[ \t]*" + re.escape(_TAG) + r".*$", re.M)
_YES = {"yes", "y", "true", "1", "evet", "نعم"}


def _clean(v):
    return re.sub(r"\s+", " ", str(v if v is not None else "")).strip()


def _num(v):
    """'1 234,50' / '4998129' / '' -> float or None. None means 'not stated'."""
    s = _clean(v).replace(" ", "")
    if not s:
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s in ("-", ".", ","):
        return None
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else \
            s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _norm_header(h):
    h = re.sub(r"[^a-z0-9]+", "_", _clean(h).lower()).strip("_")
    if h.endswith(_STD_SUFFIX):
        h = h[:-len(_STD_SUFFIX)]
    return _ALIAS.get(h, h)


def _reader(path_or_stream):
    """Path / bytes / bytes-stream / text-stream -> csv rows. utf-8-sig strips the
    BOM that Excel writes; errors='replace' means one bad byte never kills a
    5,000-row load."""
    src = path_or_stream
    if isinstance(src, (str, os.PathLike)):
        return csv.reader(io.open(src, encoding="utf-8-sig", newline="", errors="replace"))
    if isinstance(src, bytes):
        src = io.BytesIO(src)
    if hasattr(src, "read") and not hasattr(src, "encoding"):
        src = io.TextIOWrapper(src, encoding="utf-8-sig", newline="", errors="replace")
    return csv.reader(src)


def compose_remarks(base, parts):
    """Keep everything an admin wrote; replace only our own tagged line.

    Idempotent by construction: the same input produces byte-identical output, so
    a second import reports 'unchanged' instead of churning every row.
    """
    kept = _TAG_RE.sub("", base or "").strip()
    line = (_TAG + " " + "; ".join(parts)) if parts else ""
    return "\n".join(x for x in (kept, line) if x)


def _safe(reader, stats):
    """csv refuses some inputs outright — a field over 128 KB, a NUL byte on older
    Pythons. parse_machines' whole contract is 'returns (rows, stats); trouble
    lives in stats["error"]', and front door B (scripts/import_machines.py) has no
    try/except of its own, so a csv.Error escaping here is a traceback instead of a
    stated reason. Stop at the bad row and say why."""
    try:
        for row in reader:
            yield row
    except csv.Error as exc:
        stats["error"] = f"the file is not readable as CSV: {exc}"


def parse_machines(path_or_stream):
    """CSV -> (rows, stats). Pure: no DB, no Flask.

    stats = {rows, machines, no_serial, in_register, rejects:[{row,code,reason}],
             flagged:[codes], error}
    Every row that does not become a machine appears in `rejects` WITH a reason.
    """
    rows, rejects, flagged = [], [], []
    stats = {"rows": 0, "machines": 0, "no_serial": 0, "in_register": 0,
             "rejects": rejects, "flagged": flagged, "error": None}
    try:
        reader = _reader(path_or_stream)
    except OSError as exc:
        stats["error"] = f"cannot read the file: {exc}"
        return [], stats

    cols, seen = None, set()
    for n, raw in enumerate(_safe(reader, stats), start=1):
        if not raw or not any(_clean(c) for c in raw):
            continue
        if cols is None:
            cols = {}
            for i, h in enumerate(raw):
                key = _norm_header(h)
                if key and not key.endswith(_DROP_SUFFIX) and key not in cols:
                    cols[key] = i
            if "serial" not in cols and "card_no" not in cols and "code" not in cols:
                stats["error"] = ("no serial / card number / code column found — is this "
                                  "the machine register export?")
                return [], stats
            continue

        stats["rows"] += 1

        def cell(key):
            i = cols.get(key)
            return _clean(raw[i]) if i is not None and i < len(raw) else ""

        serial = cell("serial")
        card = cell("card_no") or cell("code")
        if serial:
            code, no_serial = "SN-" + serial.upper(), False
        elif card:
            code, no_serial = "CARD-" + card.upper(), True
        else:
            rejects.append({"row": n, "code": "",
                            "reason": "no serial and no card number — nothing to key on"})
            continue
        if len(code) > _MAX_CODE:
            rejects.append({"row": n, "code": code[:40] + "…",
                            "reason": f"key longer than {_MAX_CODE} characters"})
            continue
        if code in seen:
            rejects.append({"row": n, "code": code,
                            "reason": "duplicate key in this file — a card slot re-used, "
                                      "or the same serial listed twice"})
            continue
        seen.add(code)

        # The four fields with no column, composed into ONE readable remarks line.
        # Currency is stated, not implied.
        bits = []
        for label, key in (("power_kw", "power_kw"),
                           ("country_of_origin", "country_of_origin"),
                           ("arrival_year", "arrival_year")):
            v = cell(key)
            if v:
                bits.append(f"{label}={v}")
        price = _num(cell("purchase_price"))
        if price:
            bits.insert(min(2, len(bits)), f"purchase_price={price:.2f} USD")
        src = cell("source_register")
        if src:
            bits.append(f"source_register={src}")
        reg23 = cell("in_register_2023")
        in_reg = None
        if reg23:
            in_reg = 1 if reg23.strip().lower() in _YES else 0
            bits.append("in_register_2023=" + ("yes" if in_reg else "no"))
            stats["in_register"] += in_reg
        # The owner declined the 'current fleet only' filter, so a source status of
        # 'decommissioned' is recorded but NOT applied.
        st = cell("status").lower()
        if st and st != "running":
            bits.append(f"source_status={st}")

        # A blank name stays blank so it can never clobber a hand-corrected one.
        # `name_fallback` is used on INSERT only, where something has to go in.
        row = {
            "code": code, "no_serial": no_serial, "serial": serial,
            "legacy_card_no": card, "in_register_2023": in_reg,
            "meter_reading": _num(cell("meter_reading")),
            "remarks_parts": bits, "remarks_src": cell("remarks"),
            "name": cell("name"),
            "name_fallback": " ".join(x for x in (cell("brand"), cell("model")) if x) or code,
        }
        for f in _TEXT:
            row[f] = cell(f)
        if no_serial:
            stats["no_serial"] += 1
            if len(flagged) < 500:
                flagged.append(code)
        rows.append(row)

    if cols is None and stats["error"] is None:
        stats["error"] = "the file is empty"
    if stats["error"]:
        return [], stats        # nothing partial: a stated reason writes nothing
    stats["machines"] = len(rows)
    return rows, stats


# --------------------------------------------------------------------------
# The write side. Both front doors call this one too.
# --------------------------------------------------------------------------
_UPD = ("name", "type", "brand", "model", "serial", "department", "area", "line_no",
        "location", "install_date", "vendor", "needle_system", "legacy_card_no",
        "meter_reading", "meter_reading_at", "in_register_2023", "remarks")
_INS_COLS = ("code, name, type, brand, model, serial, department, area, line_no, location, "
             "install_date, vendor, needle_system, meter_reading, meter_reading_at, "
             "legacy_card_no, in_register_2023, remarks, criticality, status, qr_token, "
             "is_active, created_at")
_INS_N = len(_INS_COLS.split(","))


def upsert_machines(conn, rows, user, source="import"):
    """Upsert BY CODE in ONE transaction. -> {added, updated, unchanged, rejected}.

    A blank incoming value never overwrites a value already on the row: an admin
    who corrected a machine name by hand keeps it when the register is loaded
    again with that cell empty. status / is_active / criticality are set on
    INSERT only — a re-import must never resurrect a machine an admin retired.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    who = (user or {}).get("username") if isinstance(user, dict) else (user or "system")
    counts = {"added": 0, "updated": 0, "unchanged": 0, "rejected": 0}

    existing = {}
    for r in conn.execute(
            "SELECT id, code, name, type, brand, model, serial, department, area, line_no, "
            "location, install_date, vendor, needle_system, meter_reading, meter_reading_at, "
            "legacy_card_no, in_register_2023, remarks FROM mnt_machines").fetchall():
        existing[r["code"]] = r

    inserts, updates = [], []
    for it in rows:
        code = _clean(it.get("code"))
        if not code:
            counts["rejected"] += 1
            continue
        old = existing.get(code)
        vals = {f: _clean(it.get(f)) for f in
                ("name", "type", "brand", "model", "serial", "department", "area",
                 "line_no", "location", "install_date", "vendor", "needle_system",
                 "meter_reading_at", "legacy_card_no")}
        vals["meter_reading"] = it.get("meter_reading")
        vals["in_register_2023"] = it.get("in_register_2023")
        parts = it.get("remarks_parts") or []
        base = (old["remarks"] if old is not None else it.get("remarks_src")) or ""
        vals["remarks"] = compose_remarks(base, parts)

        if old is None:
            inserts.append((
                code, vals["name"] or _clean(it.get("name_fallback")) or code,
                vals["type"], vals["brand"], vals["model"],
                vals["serial"], vals["department"], vals["area"], vals["line_no"],
                vals["location"], vals["install_date"], vals["vendor"],
                vals["needle_system"], vals["meter_reading"], vals["meter_reading_at"],
                vals["legacy_card_no"],
                0 if vals["in_register_2023"] is None else int(vals["in_register_2023"]),
                vals["remarks"], "medium", "running", _qr(code), 1, now))
            counts["added"] += 1
            continue

        changed = {}
        for f in _UPD:
            new = vals[f]
            if new is None or (isinstance(new, str) and not new):
                continue                       # blank never clobbers a hand edit
            cur = old[f]
            if f == "meter_reading":
                if cur is None or abs(float(cur) - float(new)) > 1e-9:
                    changed[f] = float(new)
            elif f == "in_register_2023":
                if int(cur or 0) != int(new):
                    changed[f] = int(new)
            elif (cur or "") != new:
                changed[f] = new
        if not changed:
            counts["unchanged"] += 1
            continue
        sets = ", ".join(f"{f}=?" for f in changed)
        updates.append((f"UPDATE mnt_machines SET {sets} WHERE code=?",
                        tuple(changed.values()) + (code,)))
        counts["updated"] += 1

    try:
        for i in range(0, len(inserts), _CHUNK):
            chunk = inserts[i:i + _CHUNK]
            ph = ",".join(["(" + ",".join(["?"] * _INS_N) + ")"] * len(chunk))
            conn.execute(f"INSERT INTO mnt_machines ({_INS_COLS}) VALUES {ph}",
                         tuple(v for r in chunk for v in r))
        # ponytail: updates go one statement at a time. The first load is all
        # INSERTs and a re-load changes a handful of rows; batch them the day a
        # full re-survey lands.
        for sql, params in updates:
            conn.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return counts


def _qr(code):
    """The machine's QR token — must be UNIQUE, because the scan lookup is
    `WHERE code=? OR qr_token=?`; two machines sharing a token resolve a scan to
    the wrong asset. Stripping punctuation and truncating to 32 characters is not
    enough on its own: a card ref in this archive can be a whole file path
    (_MAX_CODE is 120), and two paths in the same folder collide on their first 32
    characters. Short codes keep the plain, readable form; only long ones pay for a
    digest tail.
    """
    a = re.sub(r"[^A-Za-z0-9]", "", code)
    if len(a) <= 32:
        return "MQR" + a
    return "MQR" + a[:24] + hashlib.sha1(code.encode("utf-8")).hexdigest()[:8].upper()


def machine_stats(conn):
    """{total, in_register, no_serial} for the admin screen."""
    r = conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(in_register_2023),0) g, "
        "COALESCE(SUM(CASE WHEN code LIKE 'CARD-%' THEN 1 ELSE 0 END),0) s "
        "FROM mnt_machines WHERE is_active=1").fetchone()
    return {"total": int(r["c"] or 0), "in_register": int(r["g"] or 0),
            "no_serial": int(r["s"] or 0)}
