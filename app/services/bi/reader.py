"""
Read an uploaded file into a simple table: (columns, rows).

Supports CSV / TSV (stdlib csv) and Excel .xlsx (openpyxl, already a dependency).
Pure-python, no pandas. Values come back as native types where obvious:
numbers as float, blanks as None, everything else as trimmed strings. Type
*inference* is the profiler's job — the reader only normalises.
"""
from __future__ import annotations

import csv
import io
import os

MAX_ROWS = 50000      # hard cap so a huge upload can't exhaust memory
MAX_COLS = 80

_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}


class ReadError(Exception):
    pass


def _clean_header(values):
    cols, seen = [], {}
    for i, v in enumerate(values):
        name = (str(v).strip() if v is not None else "") or f"Column {i + 1}"
        # de-duplicate repeated headers so downstream keys stay unique
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 0
        cols.append(name)
    return cols[:MAX_COLS]


def _coerce(v):
    """Light normalisation only. The profiler decides real column types."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s == "":
        return None
    return s


def read_bytes(data: bytes, filename: str = "") -> tuple[list[str], list[list]]:
    """Read raw bytes of an uploaded file into (columns, rows)."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return _read_xlsx(data)
    if ext in (".csv", ".tsv", ".txt", ""):
        return _read_csv(data, ext)
    raise ReadError(f"Unsupported file type: {ext or 'unknown'}")


def _read_csv(data: bytes, ext: str) -> tuple[list[str], list[list]]:
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    delim = "\t" if ext == ".tsv" else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except Exception:
            delim = ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows_iter = iter(reader)
    try:
        header = next(rows_iter)
    except StopIteration:
        raise ReadError("The file is empty.")
    cols = _clean_header(header)
    ncol = len(cols)
    rows = []
    for raw in rows_iter:
        if len(rows) >= MAX_ROWS:
            break
        if not any((c or "").strip() for c in raw):
            continue  # skip fully blank lines
        row = [_coerce(x) for x in raw[:ncol]]
        row += [None] * (ncol - len(row))  # pad short rows
        rows.append(row)
    if not rows:
        raise ReadError("No data rows found under the header.")
    return cols, rows


_MAX_XLSX_UNCOMPRESSED = 300 * 1024 * 1024   # 300 MB expanded ceiling
_MAX_XLSX_RATIO = 200                         # reject >200x compression (zip bomb)


def _read_xlsx(data: bytes) -> tuple[list[str], list[list]]:
    try:
        from openpyxl import load_workbook
    except Exception as exc:  # pragma: no cover
        raise ReadError("Excel support unavailable (openpyxl missing).") from exc
    # Guard against a decompression bomb: an .xlsx is a zip; a small (allowed)
    # file can expand to gigabytes. Reject before openpyxl loads it into memory.
    try:
        import zipfile
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            uncompressed = sum(i.file_size for i in zf.infolist())
        if uncompressed > _MAX_XLSX_UNCOMPRESSED or (data and uncompressed / len(data) > _MAX_XLSX_RATIO):
            raise ReadError("This Excel file is too large or looks malformed.")
    except zipfile.BadZipFile as exc:
        raise ReadError("Could not open the Excel file.") from exc
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ReadError("Could not open the Excel file.") from exc
    ws = wb.active
    it = ws.iter_rows(values_only=True)
    header = None
    for r in it:
        if r is not None and any(c is not None and str(c).strip() for c in r):
            header = r
            break
    if header is None:
        raise ReadError("The sheet is empty.")
    cols = _clean_header(list(header))
    ncol = len(cols)
    rows = []
    for r in it:
        if len(rows) >= MAX_ROWS:
            break
        if r is None or not any(c is not None and str(c).strip() for c in r):
            continue
        row = [_coerce(x) for x in list(r)[:ncol]]
        row += [None] * (ncol - len(row))
        rows.append(row)
    try:
        wb.close()
    except Exception:
        pass
    if not rows:
        raise ReadError("No data rows found under the header.")
    return cols, rows
