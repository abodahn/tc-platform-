# -*- coding: utf-8 -*-
"""
TC Platform — Procurement item catalogue: ONE parser, two front doors.

`parse_workbook` reads the ERP "List Of Item" export into plain dicts. It is
deliberately PURE — no database, no Flask — so the admin upload screen
(app/routes/approvals.py) and the CLI (scripts/import_items.py) cannot drift
apart, and the tests can drive it without an app.

Reading the file is NOT this module's job: app/tabular.read_grid does it, so
every format the platform accepts anywhere (comma / semicolon / tab CSV in
utf-8, cp1254 or cp1256, .xls, .xlsx, .xlsm) lands here for free, detected from
the file's CONTENT rather than its name. read_grid also reports HOW it read the
file, which is what tells this parser whether the numbers are European.

The export's shape (and why each rule below exists):
    row 0        column numbers 1..9                       -> noise
    rows 1-2     "From Category: ... To", "From Item: ..."  -> report filter echo
    row 3        Code | Item Name | Unit | Sales Pric | Cost Price | ...
    row 4+       data, interrupted by BAND rows in column A:
                     "Category: 05 Spare Parts & Maintenance"
                 every following item belongs to that band until the next one.
The header row is FOUND, not assumed, so next year's export still imports even
if the filter echo grows a line. Codes carry 1..8 dash-separated segments with
no single scheme — they are opaque keys and are never parsed for meaning.
"""
import re

from app.tabular import read_grid, to_number, TableError

# The file's units are numbered strings ("02 Piece"). Map them onto the
# platform's C.UNITS. Anything not here is REPORTED as a reject, never
# defaulted: a wrong unit on a purchase order is a real ordering error.
UNIT_MAP = {
    "01 kg": "Kg",
    "02 piece": "Pcs",
    "03 meter": "Meter",
    "04 liter": "Liter",
    "05 yard": "Yard",
    "06 pkt": "Packet",
    "07 cone": "Cone",
    "08 roll": "Roll",
    "09 barrel": "Barrel",
    "10 sheet": "Sheet",
    "11 set": "Set",
    "12 ktn": "Carton",
    "13 drum": "Drum",
}
# Un-numbered spellings, so a hand-made CSV or a re-exported sheet still lands.
UNIT_ALIASES = {
    "kg": "Kg", "kgs": "Kg", "piece": "Pcs", "pieces": "Pcs", "pcs": "Pcs",
    "pc": "Pcs", "meter": "Meter", "metre": "Meter", "m": "Meter",
    "liter": "Liter", "litre": "Liter", "l": "Liter", "yard": "Yard",
    "yd": "Yard", "pkt": "Packet", "packet": "Packet", "cone": "Cone",
    "roll": "Roll", "barrel": "Barrel", "sheet": "Sheet", "set": "Set",
    "ktn": "Carton", "carton": "Carton", "drum": "Drum", "box": "Box",
    "service": "Service", "lot": "Lot",
}

# Every data-i18n key the catalogue pages introduce, with real EN / AR / TR.
# app/static/i18n/** is owned by the orchestrator, so this dict is the source it
# merges from — and tests_catalogue.py asserts that every key rendered on the
# catalogue pages is covered here (or already live in en.json), because a key
# missing from en.json renders as the literal string to EVERY user.
I18N = {
    "cat.title": ("Item catalogue", "كتالوج الأصناف", "Kalem kataloğu"),
    "cat.sub": ("Load the ERP item master so requesters can pick a real item instead of typing it. Picking one stays optional — a free-text line still works.",
                "حمّل قائمة الأصناف من نظام ERP ليتمكن مقدّم الطلب من اختيار صنف حقيقي بدلاً من كتابته. الاختيار يبقى اختيارياً — ويمكن كتابة الصنف يدوياً كما كان.",
                "Talep edenlerin kalemi yazmak yerine gerçek bir kalem seçebilmesi için ERP kalem listesini yükleyin. Seçim isteğe bağlıdır — serbest metin satırı eskisi gibi çalışır."),
    "cat.on_file": ("On file", "المسجّل حالياً", "Kayıtlı"),
    "cat.items": ("items", "صنف", "kalem"),
    "cat.with_price": ("with a cost on file", "له تكلفة مسجّلة", "maliyet kaydı olan"),
    "cat.no_price": ("no price on file", "لا يوجد سعر مسجل", "fiyat kaydı yok"),
    "cat.categories": ("Categories", "الفئات", "Kategoriler"),
    "cat.empty": ("The catalogue is empty. Import the ERP export below.",
                  "الكتالوج فارغ. استورد ملف ERP من الأسفل.",
                  "Katalog boş. ERP dosyasını aşağıdan içe aktarın."),
    "cat.import_h": ("Import items", "استيراد الأصناف", "Kalemleri içe aktar"),
    "cat.import_hint": ("Excel (.xls / .xlsx) or CSV export with Code, Item Name, Unit and Cost Price columns. Items are matched by code: new codes are added, changed ones updated, and a blank cell never overwrites a value edited here.",
                        "ملف Excel‏ (.xls / .xlsx) أو CSV يحتوي أعمدة الكود واسم الصنف والوحدة وسعر التكلفة. تتم المطابقة بالكود: تُضاف الأكواد الجديدة وتُحدَّث المتغيّرة، والخانة الفارغة لا تستبدل أبداً قيمة تم تعديلها هنا.",
                        "Kod, Kalem Adı, Birim ve Maliyet Fiyatı sütunlarını içeren Excel (.xls / .xlsx) veya CSV dosyası. Eşleştirme koda göre yapılır: yeni kodlar eklenir, değişenler güncellenir ve boş bir hücre burada düzenlenmiş bir değeri asla üzerine yazmaz."),
    "cat.file": ("File", "الملف", "Dosya"),
    "cat.formats": ("CSV (comma or semicolon), .xls, .xlsx or .xlsm — the format is read from the file itself, not from its name.",
                    "ملف CSV (بفاصلة أو فاصلة منقوطة) أو ‎.xls أو ‎.xlsx أو ‎.xlsm — يتم تحديد الصيغة من محتوى الملف نفسه وليس من اسمه.",
                    "CSV (virgül veya noktalı virgül), .xls, .xlsx veya .xlsm — biçim dosya adından değil, dosyanın kendi içeriğinden okunur."),
    "cat.run": ("Import", "استيراد", "İçe aktar"),
    "cat.back": ("Back to settings", "العودة إلى الإعدادات", "Ayarlara dön"),
    "cat.result": ("Import result", "نتيجة الاستيراد", "İçe aktarma sonucu"),
    "cat.added": ("Added", "مُضاف", "Eklenen"),
    "cat.updated": ("Updated", "مُحدَّث", "Güncellenen"),
    "cat.unchanged": ("Unchanged", "دون تغيير", "Değişmeyen"),
    "cat.rejected": ("Rejected", "مرفوض", "Reddedilen"),
    "cat.unmapped_h": ("Units that could not be mapped", "وحدات تعذّر تحويلها",
                       "Eşlenemeyen birimler"),
    "cat.unmapped_hint": ("These rows were NOT imported. A guessed unit becomes a wrong quantity on a purchase order, so they are reported instead.",
                          "لم يتم استيراد هذه الصفوف. الوحدة المُخمَّنة تعني كمية خاطئة في أمر الشراء، لذلك يتم الإبلاغ عنها بدلاً من تخمينها.",
                          "Bu satırlar içe aktarılmadı. Tahmin edilen bir birim satın alma siparişinde yanlış miktar demektir, bu yüzden tahmin yerine raporlanır."),
    "cat.unit": ("Unit", "الوحدة", "Birim"),
    "cat.rows": ("Rows", "الصفوف", "Satırlar"),
    "cat.rejects_h": ("Rejected rows", "الصفوف المرفوضة", "Reddedilen satırlar"),
    "cat.rejects_hint": ("rows were skipped, each with its reason. Nothing is dropped silently.",
                         "صفاً تم تخطيه، مع سبب لكل منها. لا يتم إسقاط أي صف بصمت.",
                         "satır atlandı, her biri gerekçesiyle. Hiçbir satır sessizce düşürülmez."),
    "cat.row": ("Row", "الصف", "Satır"),
    "cat.code": ("Code", "الكود", "Kod"),
    "cat.reason": ("Reason", "السبب", "Gerekçe"),
    "cat.filter": ("Catalogue category", "فئة الكتالوج", "Katalog kategorisi"),
    "cat.all": ("All categories", "كل الفئات", "Tüm kategoriler"),
    "cat.pick_hint": ("Start typing an item code or name to pick from the catalogue — or just type what you need, exactly as before.",
                      "ابدأ بكتابة كود الصنف أو اسمه للاختيار من الكتالوج — أو اكتب ما تحتاجه مباشرة تماماً كما كان.",
                      "Katalogdan seçmek için kalem kodunu veya adını yazmaya başlayın — ya da ihtiyacınızı eskisi gibi doğrudan yazın."),
    "cat.more": ("More matches — refine the search or pick a category.",
                 "هناك نتائج أخرى — حدّد البحث أكثر أو اختر فئة.",
                 "Daha fazla eşleşme var — aramayı daraltın veya bir kategori seçin."),
    "cat.last_cost": ("Last known catalogue cost", "آخر تكلفة معروفة في الكتالوج",
                      "Katalogdaki bilinen son maliyet"),
    "cat.ref_only": ("— reference only, not an approved price. Type the real value.",
                     "— للاسترشاد فقط وليس سعراً معتمداً. اكتب القيمة الفعلية.",
                     "— yalnızca referans, onaylı bir fiyat değildir. Gerçek değeri yazın."),
}

_BAND = re.compile(r"^\s*category\s*:\s*(.*)$", re.I)
_FILTER_ECHO = re.compile(r"^\s*from\s+(category|item)\s*:", re.I)
# "05 Spare Parts & Maintenance" -> ("05", "Spare Parts & Maintenance").
# A band with no numeric prefix keeps the whole string as the name.
_BAND_CODE = re.compile(r"^\s*(\d+)\s+(.*)$")

# Header labels we need. The real export truncates "Sales Price" to "Sales Pric"
# and pads " V.A. Tax%", so match on a normalised prefix, not equality.
_WANT = {"code": "code", "item name": "name", "unit": "unit",
         "cost price": "cost", "category": "category"}


# NOTE on spreadsheet formula injection, which a review raised here:
# a cell like "=cmd|'/c calc'!A1" is only dangerous at the moment a CSV is
# OPENED IN EXCEL, and app/services/reports.py already neutralises leading
# = + - @ on export — the correct boundary. Doing it again on import was tried
# and reverted: in a real item master, 19 spare-part names legitimately begin
# with a hyphen (part numbers, and Arabic descriptions written that way).
# Prefixing those corrupts the master and breaks search for the storekeeper who
# types the part name. The catalogue therefore stores exactly what the ERP
# exported, and the export layer stays responsible for making it safe to open.
def norm_unit(raw):
    """File unit -> platform unit, or None when it cannot be mapped."""
    s = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not s:
        return None
    low = s.lower()
    if low in UNIT_MAP:
        return UNIT_MAP[low]
    if low in UNIT_ALIASES:
        return UNIT_ALIASES[low]
    # "02 Piece" written as "02  piece" / "2 Piece" — drop the numeric prefix.
    m = re.match(r"^(\d+)\s+(.*)$", low)
    if m and m.group(2) in UNIT_ALIASES:
        return UNIT_ALIASES[m.group(2)]
    return None


def _num(s, decimal=None):
    """('1.35000' | '12,50' | 1.35 | '') -> float or None.

    Delegates to app.tabular.to_number. Stripping ',' as a thousands separator
    here was right for the US comma CSV and 100x wrong for the Turkish
    semicolon CSV read_grid now accepts: '12,50' is twelve and a half, not
    1250. None (not 0.0) for a blank, so has_cost can still tell "no price on
    file" from a genuine 0.00.

    `decimal=","` is passed only when parse_workbook has EVIDENCE the file is
    European (a ';' delimiter). It makes a lone dot a thousands separator, so
    '1.234' reads as 1234 — right for that file, wrong for every other, which
    is why it is never guessed from the number alone.
    """
    return to_number(s, decimal=decimal)


def parse_workbook(src, zero_is_missing=True):
    """(items, stats) from an ERP item export.

    `src` is anything app.tabular.read_grid takes: a path, bytes, an open file
    or an uploaded FileStorage, in any CSV or Excel flavour it supports.

    items: [{code, name, unit, category_code, category_name, cost_price, has_cost}]
           cost_price is None when the file gave NO USABLE PRICE (blank cell,
           text, or a negative). None means "leave whatever is stored alone" —
           upsert_items skips it, which is the promise the import screen makes
           in three languages. A genuine 0.00 is a value and is written as 0.0.
    stats: {rows, items, blanks, bands, header_rows, filter_rows,
            rejects: [{row, code, reason}], unmapped_units: {raw: count},
            categories: {code: name}, error: str|None}

    `zero_is_missing` (default True) reads a 0.00 cost as "no price on file",
    which is what the ERP export means: it writes 0.00000 for an item nobody has
    ever priced. Set False when 0.00 is a real, chosen price — has_cost is what
    keeps the two apart so "no price on file" is never rendered as free.
    """
    items, rejects = [], []
    stats = {"rows": 0, "items": 0, "blanks": 0, "bands": 0, "header_rows": 0,
             "filter_rows": 0, "rejects": rejects, "unmapped_units": {},
             "categories": {}, "error": None}
    cols, header_cells = None, None
    cat_code = cat_name = ""
    seen = set()

    try:
        grid, meta = read_grid(src)
    except TableError as exc:
        # Written to be shown to the user; both front doors print stats["error"].
        stats["error"] = str(exc)
        return [], stats

    # A ';' delimiter IS the locale: Excel writes it exactly when the decimal
    # separator is ',', so in that file '1.234' is one thousand two hundred and
    # thirty-four. The comma-CSV ERP export (the 19,025-item source of truth)
    # reports ',' here and gets no hint at all, so it parses byte-identically.
    dec = "," if meta.get("delimiter") == ";" else None

    for n, raw in enumerate(grid, start=1):
        row = [c.strip() for c in raw]      # read_grid already stringified
        stats["rows"] = n
        first = row[0] if row else ""

        if not any(row):
            stats["blanks"] += 1
            continue
        if _FILTER_ECHO.match(first):
            stats["filter_rows"] += 1
            continue

        band = _BAND.match(first) if first else None
        if band and not any(row[1:]):
            label = band.group(1).strip()
            m = _BAND_CODE.match(label)
            cat_code, cat_name = (m.group(1), m.group(2).strip()) if m else ("", label)
            stats["bands"] += 1
            if cat_code or cat_name:
                stats["categories"][cat_code] = cat_name
            continue

        # Header row: found by content, wherever it sits.
        low = [re.sub(r"[^a-z0-9 %.]", "", c.lower()).strip() for c in row]
        if any(c == "code" for c in low) and any(c.startswith("item name") for c in low):
            if cols is None:
                cols = {}
                for i, c in enumerate(low):
                    for prefix, key in _WANT.items():
                        if c.startswith(prefix) and key not in cols:
                            cols[key] = i
                header_cells = row
                if "code" not in cols or "name" not in cols:
                    stats["error"] = "header row found but Code / Item Name missing"
                    return [], stats
            else:
                stats["header_rows"] += 1   # the export repeats it per page
            continue
        if header_cells and row == header_cells:
            stats["header_rows"] += 1
            continue
        if cols is None:
            # Anything before the header (the "1 2 3 …" column-number row) is noise.
            continue

        def cell(key):
            i = cols.get(key)
            return row[i] if i is not None and i < len(row) else ""

        code = cell("code").strip()
        name = cell("name").strip()
        if not code:
            rejects.append({"row": n, "code": "", "reason": "missing code"})
            continue
        if not name:
            rejects.append({"row": n, "code": code, "reason": "missing item name"})
            continue
        if code in seen:
            rejects.append({"row": n, "code": code, "reason": "duplicate code in file"})
            continue

        raw_unit = cell("unit")
        unit = norm_unit(raw_unit)
        if unit is None:
            key = (raw_unit or "").strip() or "(blank)"
            stats["unmapped_units"][key] = stats["unmapped_units"].get(key, 0) + 1
            rejects.append({"row": n, "code": code,
                            "reason": f"unmapped unit: {key}"})
            continue

        # THREE distinct states, and collapsing them to (0.0, has_cost 0) is how
        # a blank cell silently WIPED a cost an admin had edited here:
        #   blank / text / negative -> None: no usable price, touch nothing
        #   a genuine 0.00          -> 0.0 : a value the file actually states
        #   anything above zero     -> the price
        # A negative is not a price. Treating it as "no usable price" keeps the
        # last known good cost instead of zeroing it on one bad cell.
        cost = _num(cell("cost"), dec)
        if cost is not None and cost < 0:
            cost = None
        has_cost = cost is not None and (cost > 0 if zero_is_missing else True)

        # An explicit Category column, if this export has one, beats the band.
        ccode, cname = cat_code, cat_name
        if "category" in cols and cell("category").strip():
            label = cell("category").strip()
            m = _BAND_CODE.match(label)
            ccode, cname = (m.group(1), m.group(2).strip()) if m else ("", label)

        seen.add(code)
        items.append({"code": code, "name": name, "unit": unit,
                      "category_code": ccode, "category_name": cname,
                      "cost_price": None if cost is None else float(cost),
                      "has_cost": 1 if has_cost else 0})

    if cols is None and stats["error"] is None:
        stats["error"] = "no header row (Code / Item Name) found in the file"
    stats["items"] = len(items)
    return items, stats


# --------------------------------------------------------------------------
# Front doors share this too: the write side.
# --------------------------------------------------------------------------
_FIELDS = ("name", "unit", "category_code", "category_name", "cost_price", "has_cost")
_CHUNK = 500       # rows per multi-row INSERT — 19k items in ~40 round trips


def upsert_items(conn, items, user, source="import"):
    """Upsert BY CODE. Returns {added, updated, unchanged, rejected}.

    A blank incoming value never overwrites a value already on the row: an admin
    who fixed an item name by hand keeps it when the next export is loaded with
    that cell empty. Blank is "" for a text column and None for cost_price —
    the parser only sends a number when the file actually stated one, and a
    blank cost carries has_cost with it, because writing has_cost=0 over a
    stored 12.50 would render "no price on file" against a real cost.
    `rejected` counts rows this function itself refused (missing code); the
    parser's own rejects are reported separately.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    who = (user or {}).get("username") if isinstance(user, dict) else (user or "system")
    counts = {"added": 0, "updated": 0, "unchanged": 0, "rejected": 0}

    existing = {}
    for r in conn.execute("SELECT id, code, name, unit, category_code, category_name, "
                          "cost_price, has_cost FROM proc_items").fetchall():
        existing[r["code"]] = r

    inserts, updates = [], []
    for it in items:
        code = str(it.get("code") or "").strip()
        if not code:
            counts["rejected"] += 1
            continue
        row = existing.get(code)
        no_cost = it.get("cost_price") is None      # the file stated no price
        vals = {
            "name": str(it.get("name") or "").strip(),
            "unit": str(it.get("unit") or "").strip(),
            "category_code": str(it.get("category_code") or "").strip(),
            "category_name": str(it.get("category_name") or "").strip(),
            "cost_price": None if no_cost else float(it.get("cost_price") or 0),
            "has_cost": None if no_cost else (1 if it.get("has_cost") else 0),
        }
        if row is None:
            # A new row has nothing to preserve, so "no price" is stored as the
            # 0 / has_cost 0 pair the screens read as "no price on file".
            inserts.append((code, vals["name"], vals["unit"], vals["category_code"],
                            vals["category_name"], vals["cost_price"] or 0.0,
                            vals["has_cost"] or 0, source, who, now))
            counts["added"] += 1
            continue
        changed = {}
        for f in _FIELDS:
            new = vals[f]
            if new is None or (isinstance(new, str) and not new):
                continue                     # blank never clobbers a hand edit
            old = row[f]
            if f in ("cost_price",):
                if abs(float(old or 0) - new) > 1e-9:
                    changed[f] = new
            elif f in ("has_cost",):
                if int(old or 0) != new:
                    changed[f] = new
            elif (old or "") != new:
                changed[f] = new
        if not changed:
            counts["unchanged"] += 1
            continue
        sets = ", ".join(f"{f}=?" for f in changed)
        updates.append((f"UPDATE proc_items SET {sets}, source=?, updated_by=?, "
                        f"updated_at=? WHERE code=?",
                        tuple(changed.values()) + (source, who, now, code)))
        counts["updated"] += 1

    cols = ("code, name, unit, category_code, category_name, cost_price, has_cost, "
            "source, updated_by, updated_at")
    for i in range(0, len(inserts), _CHUNK):
        chunk = inserts[i:i + _CHUNK]
        vals = ",".join(["(?,?,?,?,?,?,?,?,?,?)"] * len(chunk))
        conn.execute(f"INSERT INTO proc_items ({cols}) VALUES {vals}",
                     tuple(v for rowvals in chunk for v in rowvals))
        conn.commit()
    # ponytail: updates go one statement at a time — a re-import changes a
    # handful of rows, not 19k. Batch them the day a full re-price lands.
    for sql, params in updates:
        conn.execute(sql, params)
    conn.commit()
    return counts


def catalogue_stats(conn):
    """{total, priced, unpriced, categories:[(code,name,n)]} for the admin screen."""
    row = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(has_cost),0) p "
                       "FROM proc_items WHERE active=1").fetchone()
    total = int(row["c"] or 0)
    priced = int(row["p"] or 0)
    cats = conn.execute(
        "SELECT category_code cc, category_name cn, COUNT(*) n FROM proc_items "
        "WHERE active=1 GROUP BY category_code, category_name ORDER BY category_code"
    ).fetchall()
    return {"total": total, "priced": priced, "unpriced": total - priced,
            "categories": [(r["cc"] or "", r["cn"] or "", int(r["n"])) for r in cats]}
