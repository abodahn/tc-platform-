"""
Read an uploaded file into a simple table: (columns, rows).

Every format question — is this CSV or Excel, which delimiter, which codepage,
how does an .xls store a date — belongs to app/tabular.py, the one reader every
import screen uses. This module only shapes its grid the way BI wants it:
native values (the profiler infers types, so it must NOT get strings), a
de-duplicated header, the row/column caps, and padded short rows.
"""
from __future__ import annotations

import io
import zipfile

from app.tabular import TableError, read_grid, sniff_kind

MAX_ROWS = 50000      # hard cap so a huge upload can't exhaust memory
MAX_COLS = 80


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


def _blank(row):
    return not any(c is not None and str(c).strip() for c in row)


_MAX_XLSX_UNCOMPRESSED = 300 * 1024 * 1024   # 300 MB expanded ceiling
_MAX_XLSX_RATIO = 200                         # reject >200x compression (zip bomb)


def _guard_zip_bomb(data: bytes) -> None:
    """An .xlsx is a zip: a small (allowed) upload can expand to gigabytes.
    Reject before openpyxl loads it into memory. A zip we cannot open at all is
    left to the reader, whose message is already written for the user."""
    if not isinstance(data, bytes) or sniff_kind(data) != "xlsx":
        return
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            uncompressed = sum(i.file_size for i in zf.infolist())
    except zipfile.BadZipFile:
        return
    if uncompressed > _MAX_XLSX_UNCOMPRESSED or uncompressed / len(data) > _MAX_XLSX_RATIO:
        raise ReadError("This Excel file is too large or looks malformed.")


def read_bytes(data: bytes, filename: str = "") -> tuple[list[str], list[list]]:
    """Read raw bytes of an uploaded file into (columns, rows).

    Any format app/tabular.py supports: CSV/TSV with any delimiter and any of
    utf-8/cp1254/cp1256, .xls, .xlsx/.xlsm. `filename` is accepted for callers'
    convenience and deliberately ignored — the format comes from the bytes.
    """
    if not data:
        raise ReadError("The file is empty.")
    _guard_zip_bomb(data)
    try:
        grid, _meta = read_grid(data, stringify=False)   # native values: the
    except TableError as exc:                            # profiler types them
        raise ReadError(str(exc)) from exc

    header, body = None, []
    for i, r in enumerate(grid):
        if not _blank(r):
            header, body = r, grid[i + 1:]
            break
    if header is None:
        raise ReadError("The file is empty.")

    cols = _clean_header(list(header))
    ncol = len(cols)
    rows = []
    for r in body:
        if len(rows) >= MAX_ROWS:
            break
        if _blank(r):
            continue  # skip fully blank lines
        row = [_coerce(x) for x in list(r)[:ncol]]
        row += [None] * (ncol - len(row))  # pad short rows
        rows.append(row)
    if not rows:
        raise ReadError("No data rows found under the header.")
    return cols, rows
