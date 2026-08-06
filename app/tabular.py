# -*- coding: utf-8 -*-
"""
TC Platform — ONE reader for every uploaded table.

Five modules had each grown their own copy (maintenance machines, maintenance
register, procurement catalogue, probation roster, admin users, BI). They drifted:
one took .xlsx only, one took .csv only, none of them read a semicolon CSV. Every
import screen now calls `read_grid` here, so a format supported anywhere is
supported everywhere.

What it handles, and why each one is not optional in this company:

* **Format from CONTENT, never the extension.** People rename files, and mail
  systems mangle them. A .xlsx read as text imports mojibake instead of failing,
  which is worse than refusing it.

* **The semicolon.** Excel on a Turkish or Arabic Windows writes CSV with `;`,
  because `,` is the decimal separator there. Read with the comma default, every
  row arrives as ONE field and the import silently produces garbage. The
  delimiter is measured, not assumed.

* **cp1254 / cp1256.** "Save as CSV" (not "CSV UTF-8") on a Turkish Windows
  emits cp1254; on an Arabic one, cp1256. Neither raises when decoded wrongly —
  it just yields the wrong letters. utf-8 is tried strictly first, and only if
  that fails is the legacy codepage chosen by scoring the text against the
  Turkish and Arabic signature characters. The choice is REPORTED in meta, not
  hidden.

* **Numbers Excel stored as floats.** Serial 12345 comes back as 12345.0, whose
  string form matches nothing. Integral floats are written back as integers.

* **Dates.** An .xls date is a bare float (44927.0) unless the cell type is
  consulted; an .xlsx date is a datetime whose str() carries a spurious
  00:00:00. Both become ISO dates.

Values come back as strings by default because every import parser downstream
stringifies anyway. `stringify=False` preserves native types for BI, whose
profiler does its own inference.
"""
import csv
import datetime as _dt
import io
import os
import re

# One oversized cell should not kill a 5,000-row load with an opaque csv.Error.
try:
    csv.field_size_limit(4 * 1024 * 1024)
except (OverflowError, ValueError):      # 32-bit builds reject large limits
    pass

# What every import <input accept="..."> should offer. One constant so a format
# added here cannot be missing from a form.
ACCEPT = ".csv,.tsv,.txt,.xls,.xlsx,.xlsm"
EXTS = tuple(ACCEPT.split(","))

_OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"     # .xls, .doc — legacy compound file
_ZIP = b"PK"                                    # .xlsx/.xlsm — and .ods, caught below
# A bare BIFF2/3/4 record stream: a real .xls that was never OLE2-wrapped. xlrd
# reads these fine. Without this branch they fall through to the text path and
# decode into binary rubbish instead of being refused — silent corruption, which
# is the exact failure this module exists to prevent.
_BIFF = (b"\x09\x00", b"\x09\x02", b"\x09\x04", b"\x09\x08")

# Files that are definitely not tables, by magic number. Naming them beats
# "could not read the file" — the user knows immediately what they picked.
_NOT_A_TABLE = (
    (b"%PDF", "a PDF"), (b"\xff\xd8\xff", "a JPEG image"),
    (b"\x89PNG", "a PNG image"), (b"GIF8", "a GIF image"),
    (b"BM", "a bitmap image"), (b"{\\rtf", "an RTF document"),
    (b"\x1f\x8b", "a gzip archive"), (b"Rar!", "a RAR archive"),
    (b"7z\xbc\xaf", "a 7-Zip archive"), (b"\x00\x00\x01\x00", "an icon"),
    (b"OggS", "an audio file"), (b"\x1aE\xdf\xa3", "a video file"),
)

# The six letters that exist in cp1254 but NOT cp1252 — the only honest
# discriminator between the two, since the codepages are otherwise identical.
_TR_SIGNATURE = set("ğĞıİşŞ")
# cp1252's own accented letters, for European text that is neither TR nor AR.
_WEST_SIGNATURE = set("àáâãäåèéêëìíîïñòóôõöùúûüýÿÀÁÂÃÄÅÈÉÊËÌÍÎÏÑÒÓÔÕÖÙÚÛÜ")


class TableError(Exception):
    """The file cannot be read as a table. The message is shown to the user."""


def sniff_kind(raw):
    """'xls' | 'xlsx' | 'ods' | 'csv' from the leading bytes."""
    if raw[:8] == _OLE2 or raw[:2] in _BIFF:
        return "xls"
    if raw[:2] == _ZIP:
        # .ods is also a zip. openpyxl fails on it with an opaque error, so name it.
        return "ods" if b"opendocument" in raw[:200].lower() else "xlsx"
    return "csv"


def _raw_bytes(src):
    """Path / bytes / file-like / Flask FileStorage -> bytes."""
    if isinstance(src, bytes):
        return src
    if isinstance(src, bytearray):
        return bytes(src)
    if isinstance(src, (str, os.PathLike)):
        with io.open(src, "rb") as fh:
            return fh.read()
    if hasattr(src, "read"):
        try:                       # a stream already consumed by a caller reads empty
            src.seek(0)
        except Exception:          # noqa: BLE001 — not every stream is seekable
            pass
        data = src.read()
        return data.encode("utf-8") if isinstance(data, str) else (data or b"")
    return b""


def _is_arabic(ch):
    return 0x0600 <= ord(ch) <= 0x06FF


def _score_arabic(text):
    """Arabic letters that sit NEXT TO another Arabic letter.

    Counting Arabic characters outright is what made the first version of this
    function wrong: Turkish text misdecoded as cp1256 sprays ISOLATED Arabic
    letters between Latin ones and outscored the correct reading, so
    "Dikiş iğnesi" imported as "Diki‏ iًnesi". Real Arabic comes in words; a
    misdecode does not. Measured on live samples, this separates them cleanly.
    """
    n = 0
    for i, ch in enumerate(text):
        if _is_arabic(ch) and ((i and _is_arabic(text[i - 1]))
                               or (i + 1 < len(text) and _is_arabic(text[i + 1]))):
            n += 1
    return n


def _score_junk(text):
    """Non-ASCII characters that are not letters — the fingerprint of a wrong
    codepage. A correct decoding of business data produces almost none."""
    return sum(1 for ch in text if ord(ch) > 127 and not ch.isalpha())


def decode_text(raw):
    """(text, encoding). Tries the encodings this company actually produces.

    utf-8 first and strictly — it is what our own exports write, and a valid
    utf-8 decode is never a coincidence. UTF-16 next, because Excel's
    "Save as -> Unicode Text (*.txt)" writes it and the NUL bytes would
    otherwise be stripped and the text decoded as garbage. Only then the legacy
    single-byte codepages, which never raise and so must be CHOSEN, not
    stumbled into.
    """
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or raw[:4] in (b"\xff\xfe\x00\x00",
                                                            b"\x00\x00\xfe\xff"):
        for enc in ("utf-32", "utf-16"):
            try:
                return raw.decode(enc), enc
            except (UnicodeDecodeError, LookupError):
                continue
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    # BOM-less UTF-16 from a plain "Save as Unicode": every other byte is NUL.
    head = raw[:512]
    if head and sum(1 for b in head if b == 0) > len(head) // 3:
        for enc in ("utf-16-le", "utf-16-be"):
            try:
                text = raw.decode(enc)
                if _score_junk(text) < len(text) // 4:
                    return text, enc
            except (UnicodeDecodeError, LookupError):
                continue

    best, best_enc, best_score = None, "cp1252", None
    for enc, signature in (("cp1254", _TR_SIGNATURE), ("cp1256", None),
                           ("cp1252", _WEST_SIGNATURE)):
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        hits = _score_arabic(text) if signature is None else \
            sum(1 for ch in text if ch in signature)
        score = hits - 2 * _score_junk(text)   # a wrong codepage shows as junk
        if best_score is None or score > best_score:
            best, best_enc, best_score = text, enc, score
    if best is None:                          # nothing decoded — never lose the load
        return raw.decode("utf-8", errors="replace"), "utf-8/replace"
    return best, best_enc


def _refuse_if_not_text(raw, text):
    """A PDF or a JPEG must not import as a table of gibberish.

    Dropping the old extension check made every surface accept anything; magic
    numbers and a control-character ratio put the refusal back where it belongs
    — on the CONTENT, so a .pdf renamed .csv is still refused.
    """
    for magic, what in _NOT_A_TABLE:
        if raw.startswith(magic):
            raise TableError(f"that file is {what}, not a table — "
                             "export it as CSV or Excel first")
    sample = text[:8192]
    if not sample.strip():
        raise TableError("the file has no readable content")
    ctrl = sum(1 for ch in sample if ord(ch) < 32 and ch not in "\t\r\n")
    ctrl += sample.count("�")            # replacement chars = wrong decoding
    if ctrl > max(4, len(sample) // 100):
        raise TableError("that file is not a table — it looks like a program, an "
                         "image or a document. Export it as CSV or Excel first")


def sniff_delimiter(text):
    """The delimiter that splits the sample into the most columns CONSISTENTLY.

    csv.Sniffer guesses from punctuation frequency and picks ',' off a single
    comma inside a quoted name. Counting parsed fields per line cannot make that
    mistake: a wrong delimiter yields one field per line and scores 0.
    """
    sample = [ln for ln in text.splitlines()[:50] if ln.strip()][:20]
    if not sample:
        return ","
    best, best_score = ",", (0, 0)
    for delim in (",", ";", "\t", "|"):
        try:
            counts = [len(row) for row in csv.reader(sample, delimiter=delim)]
        except csv.Error:
            continue
        if not counts:
            continue
        modal = max(set(counts), key=counts.count)
        if modal < 2:                          # did not split anything
            continue
        score = (counts.count(modal), modal)   # agreement first, then width
        if score > best_score:
            best, best_score = delim, score
    return best


def _cell(v, stringify):
    if v is None:
        return "" if stringify else None
    if isinstance(v, _dt.datetime):
        v = v.strftime("%Y-%m-%d") if (v.hour, v.minute, v.second) == (0, 0, 0) \
            else v.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(v, _dt.date):
        v = v.isoformat()
    elif isinstance(v, _dt.time):
        v = v.strftime("%H:%M:%S")
    elif isinstance(v, float) and v == int(v):
        v = int(v)                             # 12345.0 -> 12345, so serials match
    if not stringify:
        return v
    return str(v)


def _first_with_data(items, size_of):
    """The first sheet that actually holds rows, else the first one."""
    for it in items:
        try:
            if size_of(it) > 1:
                return it
        except Exception:                      # noqa: BLE001 — a broken sheet is not fatal
            continue
    return items[0]


def _rows_from_xls(raw, stringify, every_sheet):
    try:
        import xlrd
    except ImportError:                        # pragma: no cover - dependency pinned
        raise TableError("This .xls needs the xlrd library. Save it as .xlsx or .csv.")
    try:
        book = xlrd.open_workbook(file_contents=raw)
    except Exception as exc:                   # noqa: BLE001
        raise TableError(f"the .xls file could not be opened: {exc}")
    sheets = book.sheets()
    if not every_sheet:
        sheets = [_first_with_data(sheets, lambda s: s.nrows)]
    out = []
    for sh in sheets:
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                kind, val = sh.cell_type(r, c), sh.cell_value(r, c)
                if kind == xlrd.XL_CELL_DATE:
                    y, mo, d, h, mi, s = xlrd.xldate_as_tuple(val, book.datemode)
                    val = _dt.datetime(y, mo, d, h, mi, s) if y else _dt.time(h, mi, s)
                elif kind == xlrd.XL_CELL_BOOLEAN:
                    val = bool(val)
                elif kind == xlrd.XL_CELL_ERROR:
                    val = None
                row.append(_cell(val, stringify))
            rows.append(row)
        out.append((sh.name, rows))
    return out, None            # .xls carries no reliable "active sheet" marker


def _rows_from_xlsx(raw, stringify, every_sheet):
    import openpyxl
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:                   # noqa: BLE001
        raise TableError(f"the Excel file could not be opened: {exc}")
    try:
        if every_sheet:
            names = list(wb.sheetnames)
        else:
            # The ACTIVE sheet, not sheet 0. Excel records whichever tab was
            # selected when the file was saved, and a workbook whose first sheet
            # is a cover or instructions page is completely normal — reading
            # sheet 0 there imports nothing and reports success.
            active = wb.active.title if wb.active is not None else wb.sheetnames[0]
            names = [active if (wb[active].max_row or 0) > 1
                     else _first_with_data(wb.sheetnames, lambda n: wb[n].max_row or 0)]
        active = wb.active.title if wb.active is not None else None
        out = []
        for name in names:
            ws = wb[name]
            # A workbook can hold Chartsheets, which have no rows at all. Calling
            # iter_rows on one raised AttributeError straight out of the route as
            # a 500 page. A chart tab is not data — skip it, don't crash on it.
            if not hasattr(ws, "iter_rows"):
                continue
            out.append((name, [[_cell(v, stringify) for v in row]
                               for row in ws.iter_rows(values_only=True)]))
        if not out:
            raise TableError("this workbook has no data sheets — only charts or "
                             "pictures. Save the data as CSV or a normal sheet")
        return out, active
    finally:
        wb.close()


def read_sheets(src, stringify=True):
    """([(sheet_name, rows), ...], meta) — sheets kept SEPARATE.

    This exists because the flattened alternative shipped real damage twice in
    one afternoon. Concatenating every worksheet into one grid loses the sheet
    boundary, so a caller that finds its header row once then reads sheet 2
    through sheet 1's column map: a "Departments" tab became seven sign-in
    accounts, and a roster whose first sheet was a cover page imported nobody.

    A caller that wants every sheet must therefore handle each sheet's header
    itself. That is the whole point — it cannot silently get it wrong.
    """
    raw = _raw_bytes(src)
    meta = {"kind": "csv", "encoding": None, "delimiter": None, "sheets": [],
            "active": None, "rows": 0}
    if not raw:
        raise TableError("the file is empty")

    kind = sniff_kind(raw)
    meta["kind"] = kind
    if kind == "ods":
        raise TableError("OpenDocument (.ods) is not supported — save it as .xlsx or .csv")
    if kind in ("xls", "xlsx"):
        sheets, active = (_rows_from_xls if kind == "xls" else _rows_from_xlsx)(
            raw, stringify, True)
        meta["sheets"] = [name for name, _ in sheets]
        meta["active"] = active
        meta["rows"] = sum(len(r) for _, r in sheets)
        return sheets, meta

    text, encoding = decode_text(raw)
    _refuse_if_not_text(raw, text)             # a PDF must not import as gibberish
    text = text.replace("\x00", "")            # Excel writes stray NULs; csv refuses them
    delim = sniff_delimiter(text)
    meta["encoding"], meta["delimiter"] = encoding, delim
    try:
        rows = [[_cell(c, stringify) for c in row]
                for row in csv.reader(io.StringIO(text, newline=""), delimiter=delim)]
    except csv.Error as exc:
        raise TableError(f"the file is not readable as a table: {exc}")
    meta["rows"] = len(rows)
    return [("", rows)], meta


def read_grid(src, stringify=True):
    """(rows, meta) from ONE sheet — the active one for a workbook, or the CSV.

    There is deliberately no `all_sheets` flag: see read_sheets for why a
    flattened multi-sheet grid is a trap rather than a convenience.
    """
    sheets, meta = read_sheets(src, stringify=stringify)
    if meta["kind"] in ("xls", "xlsx") and len(sheets) > 1:
        # The sheet Excel had SELECTED when the file was saved — a workbook whose
        # first tab is a cover or instructions page is completely normal, and
        # reading sheet 0 there imports nothing while reporting success. Fall back
        # to the first sheet that actually holds rows.
        by_name = dict(sheets)
        active = meta.get("active")
        name = active if (active in by_name and len(by_name[active]) > 1)             else _first_with_data(sheets, lambda s: len(s[1]))[0]
        sheets = [(name, by_name[name])]
        meta["sheets"] = [name]
    rows = sheets[0][1] if sheets else []
    meta["rows"] = len(rows)
    return rows, meta


def read_rows(src, stringify=True):
    """read_grid without the meta — a drop-in for the per-module readers it replaces."""
    return read_grid(src, stringify=stringify)[0]


_NUM_KEEP = re.compile(r"[^\d.,]")
_SCIENTIFIC = re.compile(r"^[+-]?\d+(?:[.,]\d+)?[eE][+-]?\d+$")
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)
# Currency written as letters, which a price column legitimately carries. Only
# these — anything else with a letter in it is prose, not a price.
_CURRENCY_WORDS = ("usd", "egp", "try", "tl", "eur", "gbp", "sar", "aed", "kr")
_CURRENCY_CHARS = "$€£₺%¢¥₹﷼"


def to_number(value, default=None, decimal=None):
    """A number out of whatever a spreadsheet put in the cell.

    Accepting semicolon CSVs made this mandatory rather than nice: the same
    Turkish Excel that writes `;` writes `12,50` for twelve and a half. Stripping
    commas as thousands separators — which is right for a US export — turns that
    into 1250, and a cost price silently lands 100x too high.

    When both separators appear, the LAST one is the decimal point, which reads
    `1.234,50` and `1,234.50` correctly. A lone comma is a decimal point unless
    it is followed by exactly three digits, where thousands is the safer reading.
    Returns `default` for blanks and for anything that is not a number, so a
    caller can still tell "empty" from "zero".

    Pass `decimal=','` when the caller KNOWS the file is European (a semicolon
    delimiter is the giveaway — read_grid reports it in meta). Only then is a
    lone dot read as a thousands separator, because guessing that from the
    number alone would turn a genuine 1.234 into 1234.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(" ", "").replace(" ", "")
    if not s:
        return default
    # Scientific notation, which is how CPython's str() renders any float below
    # 1e-4 — so an .xlsx cell holding 0.00001 reaches here as '1e-05'. Stripping
    # the exponent as punctuation turned that into 105.0: a per-gram trim price
    # overstated ten-million-fold, silently, with has_cost set.
    if _SCIENTIFIC.match(s):
        try:
            return float(s.replace(",", "."))
        except ValueError:
            return default
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    # Strip the currency the value is allowed to carry, THEN refuse anything that
    # still has a letter in it. Without this, _NUM_KEEP deleted the words and
    # kept the digits: 'ask Ahmed 2024' became a cost price of 2024.00, written
    # over a correct one, with no reject row and no way to notice. A row that
    # cannot be read as a number must be REJECTED, never fabricated.
    bare = s.strip("()+-").strip()
    for ch in _CURRENCY_CHARS:
        bare = bare.replace(ch, "")
    low = bare.lower()
    for word in _CURRENCY_WORDS:
        if low.startswith(word):
            bare = bare[len(word):]
        elif low.endswith(word):
            bare = bare[:-len(word)]
        low = bare.lower()
    if _LETTER.search(bare):
        return default
    s = _NUM_KEEP.sub("", bare)
    if not s or not any(c.isdigit() for c in s):
        return default
    if "." in s and "," in s:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        s = s.replace("," if dec == "." else ".", "").replace(dec, ".")
    elif "," in s:
        if decimal == ",":                     # caller knows the file is European
            s = s.replace(",", ".")
        else:
            head, _, tail = s.rpartition(",")
            s = s.replace(",", "") if (len(tail) == 3 and head.count(",") == 0
                                       and len(head) > 0) else s.replace(",", ".")
    elif s.count(".") >= 2 and decimal == ",":
        # 1.234.567 — TWO or more dots is unambiguous grouping, so strip them.
        # A SINGLE dot is not: in a European file '12.500' could be twelve
        # thousand five hundred or twelve point five, and an earlier version of
        # this guessed "thousands" and multiplied three-decimal prices by 1000.
        # Where the evidence does not decide, do not decide either.
        if all(len(part) == 3 for part in s.split(".")[1:]):
            s = s.replace(".", "")
    try:
        n = float(s)
    except ValueError:
        return default
    return -n if negative else n


def describe(meta):
    """One line for the UI: what we actually read, so a wrong guess is visible."""
    if meta.get("kind") in ("xls", "xlsx"):
        sheets = ", ".join(meta.get("sheets") or [])
        return f"{meta['kind'].upper()} · sheet {sheets}" if sheets else meta["kind"].upper()
    delim = {",": "comma", ";": "semicolon", "\t": "tab", "|": "pipe"}.get(
        meta.get("delimiter"), meta.get("delimiter"))
    return f"CSV · {delim}-separated · {meta.get('encoding')}"


if __name__ == "__main__":          # self-check: python -m app.tabular
    def eq(label, got, want):
        assert got == want, f"{label}: got {got!r}, want {want!r}"
        print("  ok  ", label)

    rows, m = read_grid(b"code,name\nM-1,Juki\n")
    eq("comma csv", rows, [["code", "name"], ["M-1", "Juki"]])
    eq("comma detected", m["delimiter"], ",")

    rows, m = read_grid("kod;ad;adet\nSP-1;İğne;12\n".encode("utf-8"))
    eq("semicolon csv", rows, [["kod", "ad", "adet"], ["SP-1", "İğne", "12"]])
    eq("semicolon detected", m["delimiter"], ";")

    rows, m = read_grid("kod;ad\nSP-1;Dikiş iğnesi\n".encode("cp1254"))
    eq("cp1254 turkish", rows[1][1], "Dikiş iğnesi")
    eq("cp1254 chosen", m["encoding"], "cp1254")

    rows, m = read_grid("code,name\nSP-1,إبرة خياطة\n".encode("cp1256"))
    eq("cp1256 arabic", rows[1][1], "إبرة خياطة")
    eq("cp1256 chosen", m["encoding"], "cp1256")

    rows, _ = read_grid(b"\xef\xbb\xbfcode,name\nM-1,Juki\n")
    eq("utf-8 BOM stripped", rows[0][0], "code")

    rows, m = read_grid(b"a\tb\n1\t2\n")
    eq("tab detected", m["delimiter"], "\t")

    # A single quoted comma must not beat the real semicolon delimiter.
    rows, m = read_grid('name;qty\n"Needle, fine";3\n'.encode("utf-8"))
    eq("quoted comma ignored", rows[1], ["Needle, fine", "3"])

    # One column, no delimiter anywhere: must not invent one.
    rows, _ = read_grid(b"code\nM-1\nM-2\n")
    eq("single column", rows, [["code"], ["M-1"], ["M-2"]])

    import openpyxl                       # round-trip a real workbook
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["code", "serial", "bought"])
    ws.append(["M-1", 4998129, _dt.datetime(2023, 5, 1)])
    buf = io.BytesIO(); wb.save(buf)
    rows, m = read_grid(buf.getvalue())
    eq("xlsx by bytes", rows[1], ["M-1", "4998129", "2023-05-01"])
    eq("xlsx sniffed", m["kind"], "xlsx")
    buf.seek(0)
    eq("xlsx by stream", read_rows(buf)[1][1], "4998129")

    # --- every bug the adversarial verifiers reproduced, pinned here ---------
    # 1. Turkish must not be read as Arabic. The first version scored cp1256 by
    #    counting every Arabic char, and any Turkish text containing ü/ö/ç won it.
    tr = "Dikiş iğnesi (İĞNE) ÜŞÖÇ açıklama"
    rows, m = read_grid(("kod;ad\r\nSP-1;%s\r\n" % tr).encode("cp1254"))
    eq("turkish is not read as arabic", rows[1][1], tr)
    eq("...and cp1254 is the reported encoding", m["encoding"], "cp1254")
    ar = "إبرة خياطة قطع غيار"
    rows, m = read_grid(("code;name\r\nSP-2;%s\r\n" % ar).encode("cp1256"))
    eq("arabic still detected", rows[1][1], ar)
    eq("...as cp1256", m["encoding"], "cp1256")
    rows, _ = read_grid("kod;ad\nSP-3;Şerit\n".encode("cp1254"))
    eq("short turkish too (one signature letter)", rows[1][1], "Şerit")

    # 2. Excel "Save as -> Unicode Text (*.txt)" is UTF-16, and .txt is in ACCEPT.
    rows, m = read_grid(("ad\tadet\r\nDikiş iğnesi\t5\r\n").encode("utf-16"))
    eq("utf-16 turkish survives", rows[1][0], "Dikiş iğnesi")
    eq("utf-16 tab delimiter", m["delimiter"], "\t")

    # 3. A PDF/JPEG must be refused, not imported as rows of gibberish.
    for blob, what in ((b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\n", "PDF"),
                       (b"\xff\xd8\xff\xe0\x00\x10JFIF" + bytes(range(256)), "JPEG"),
                       (b"\x89PNG\r\n\x1a\n" + bytes(range(200)), "PNG"),
                       (bytes(range(256)) * 8, "binary junk")):
        try:
            read_grid(blob)
            raise AssertionError(f"{what} must be refused, not parsed")
        except TableError:
            print(f"  ok   {what} refused with a reason")

    # 4. The ACTIVE sheet, not sheet 0 — a cover sheet in front of the data is
    #    normal and used to import zero rows while reporting success.
    wb = openpyxl.Workbook()
    cover = wb.active; cover.title = "Instructions"
    cover.append(["Fill in the DATA sheet, not this one"])
    data = wb.create_sheet("DATA")
    data.append(["code", "name"]); data.append(["SP-9", "Needle"])
    wb.active = wb.sheetnames.index("DATA")
    buf = io.BytesIO(); wb.save(buf)
    rows, m = read_grid(buf.getvalue())
    eq("reads the ACTIVE sheet, not sheet 0", rows[1], ["SP-9", "Needle"])
    eq("...and says which sheet", m["sheets"], ["DATA"])

    # 5. A bare BIFF stream is a real .xls; catalogue's fixtures build exactly this.
    biff = b"\x09\x00\x04\x00\x00\x00\x10\x00"
    eq("bare BIFF sniffs as xls", sniff_kind(biff), "xls")

    # 6. Turkish decimal commas: a semicolon CSV carries them by definition.
    for raw_value, want in (("12,5", 12.5), ("1.234,50", 1234.5), ("1,234.50", 1234.5),
                            ("1,234", 1234.0), ("12.5", 12.5), ("", None),
                            ("abc", None), ("(1,5)", -1.5), ("0", 0.0)):
        eq(f"to_number({raw_value!r})", to_number(raw_value), want)

    # 7. SHEETS STAY SEPARATE. The flattened multi-sheet grid shipped real damage
    #    twice: a "Departments" tab became seven sign-in accounts because sheet 2
    #    was read through sheet 1's column map.
    wb = openpyxl.Workbook()
    staff = wb.active; staff.title = "Staff"
    staff.append(["Display Name", "Alias", "Primary SMTP Address"])
    staff.append(["Ahmed ElGohary", "aelgohary", "a@tc.com"])
    depts = wb.create_sheet("Departments")
    depts.append(["Department", "Head"]); depts.append(["Sewing", "Mona Said"])
    buf = io.BytesIO(); wb.save(buf)
    sheets, m = read_sheets(buf.getvalue())
    eq("read_sheets keeps sheets apart", [n for n, _ in sheets], ["Staff", "Departments"])
    eq("...each with its own rows", [len(r) for _, r in sheets], [2, 2])
    eq("read_grid still returns ONE sheet", len(read_grid(buf.getvalue())[0]), 2)
    try:
        read_grid(buf.getvalue(), all_sheets=True)
        raise AssertionError("all_sheets must be gone, not silently ignored")
    except TypeError:
        print("  ok   the flattening trap no longer exists as an option")

    # 8. Scientific notation: str(0.00001) is '1e-05', and stripping the exponent
    #    as punctuation read that as 105.0 — ten million times too high.
    eq("to_number('1e-05')", to_number("1e-05"), 1e-05)
    eq("to_number('1E-05')", to_number("1E-05"), 1e-05)
    eq("to_number('1.5e+10')", to_number("1.5e+10"), 15000000000.0)
    eq("to_number('2,5E3') european", to_number("2,5E3"), 2500.0)
    eq("str() of a small float round-trips", to_number(str(0.00001)), 1e-05)

    # 9. The European thousands DOT, but only when the caller has evidence.
    eq("'1.234' stays 1.234 without a hint", to_number("1.234"), 1.234)
    # This pin USED to assert 1234.0 — that was the bug, not the contract. A lone
    # dot in a European file is genuinely ambiguous ('12.500' is either twelve
    # thousand five hundred or twelve and a half) and guessing "thousands"
    # multiplied every three-decimal price by 1000. Only unambiguous grouping
    # (two or more dots) is now stripped; see check 11.
    eq("'1.234' stays ambiguous even when told European",
       to_number("1.234", decimal=","), 1.234)
    eq("'1.234,50' unaffected by the hint", to_number("1.234,50", decimal=","), 1234.5)
    eq("'12,5' unaffected by the hint", to_number("12,5", decimal=","), 12.5)
    eq("'1.5' is not mangled by the hint", to_number("1.5", decimal=","), 1.5)

    # 10. Prose is not a price. _NUM_KEEP used to delete the words and keep the
    #     digits, so a Cost Price cell reading "ask Ahmed 2024" was written over a
    #     correct stored cost as 2024.00, with no reject row.
    for junk in ("N/A 5", "call supplier 3", "ask Ahmed 2024", "TBD", "n/a",
                 "see note 7", "-", "price on request 99"):
        eq(f"to_number({junk!r}) refuses", to_number(junk), None)
    for money, want in (("$50", 50.0), ("50 USD", 50.0), ("12,5 TL", 12.5),
                        ("1.234,50 EGP", 1234.5), ("(100)", -100.0), ("%15", 15.0)):
        eq(f"to_number({money!r}) still reads", to_number(money), want)

    # 11. The locale hint must not guess. It multiplied 3-decimal prices by 1000.
    eq("'12.500' under ';' is not 12500", to_number("12.500", decimal=","), 12.5)
    eq("'1.234.567' IS grouped", to_number("1.234.567", decimal=","), 1234567.0)
    eq("'12.50000' unchanged", to_number("12.50000", decimal=","), 12.5)

    # 12. A chart tab must not 500 the import.
    from openpyxl.chart import BarChart, Reference
    wb = openpyxl.Workbook()
    ws = wb.active; ws.title = "Data"
    ws.append(["code", "qty"]); ws.append(["SP-1", 7])
    cs = wb.create_chartsheet("Chart1")
    _chart = BarChart()
    _chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=2), titles_from_data=True)
    cs.add_chart(_chart)              # openpyxl refuses to save an EMPTY chartsheet
    buf = io.BytesIO(); wb.save(buf)
    rows, m = read_grid(buf.getvalue())
    eq("chart tab skipped, data still read", rows[1], ["SP-1", "7"])
    eq("...and it is not listed as a sheet", m["sheets"], ["Data"])

    try:
        read_grid(b"")
        raise AssertionError("empty file must raise")
    except TableError:
        print("  ok   empty file refused with a reason")

    print("app/tabular.py self-check PASSED")
