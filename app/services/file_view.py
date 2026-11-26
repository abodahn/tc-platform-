"""In-app file viewer shared by every attachment / document download route.

A route keeps ALL of its own permission checks and calls file_response() as its
very last line, so the viewer is served by the same route with the same gate
(no generic "view any file by id" endpoint exists):

    ?view=1      -> an HTML page (inside base.html) showing the file: images,
                    PDFs (pdf.js), Excel/CSV and Word as server-rendered HTML,
                    text as <pre>, anything else a "can't be previewed" card
    ?inline=1    -> the raw bytes with Content-Disposition inline, ONLY for PDFs
                    and raster images recognised by their magic bytes; every
                    other type (SVG, HTML, unknown...) is forced to download
    ?download=1  -> forced download
    (none)       -> exactly what the route did before
"""
import csv
import datetime
import io
import os
import zipfile
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from flask import render_template, request, send_file

MAX_PARSE = 15 * 1024 * 1024        # bytes we are willing to parse server-side
MAX_UNZIPPED = 40 * 1024 * 1024     # zip-bomb guard for xlsx/docx (whole archive)
MAX_PART = 20 * 1024 * 1024         # one XML part we parse fully in memory
MAX_ZIP_ENTRIES = 5000
MAX_ROWS, MAX_COLS = 2000, 60       # per sheet
MAX_TEXT = 1024 * 1024              # characters of .txt/.csv shown
MAX_BLOCKS = 5000                   # docx paragraphs/tables shown

_RASTER = ((b"\x89PNG\r\n\x1a\n", "image/png"), (b"\xff\xd8\xff", "image/jpeg"),
           (b"GIF87a", "image/gif"), (b"GIF89a", "image/gif"))
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class TooBig(ValueError):
    """Over a parse cap: the viewer shows the 'too large' card."""


def _is_bmp(raw):
    # "BM" alone also starts plain text ("BMW parts..."): require the zero reserved
    # field and a known DIB header size.
    return (len(raw) >= 26 and raw[:2] == b"BM" and raw[6:10] == b"\0\0\0\0"
            and int.from_bytes(raw[14:18], "little") in (12, 40, 52, 56, 64, 108, 124))


def _ext(name):
    return os.path.splitext(name or "")[1].lower().lstrip(".")


def sniff(raw, content_type, filename):
    """(kind, inline_mime). The bytes decide PDF/image, never the stored type:
    content_type is whatever the uploader's browser claimed."""
    raw = raw or b""
    ext = _ext(filename)
    ct = (content_type or "").split(";")[0].strip().lower()
    # Leading junk before %PDF- is tolerated only for files that say they are PDFs,
    # so a CSV/txt that merely mentions "%PDF-" is not sent to pdf.js.
    if raw.startswith(b"%PDF-") or ((ext == "pdf" or ct == "application/pdf") and b"%PDF-" in raw[:1024]):
        return "pdf", "application/pdf"
    for magic, mime in _RASTER:
        if raw.startswith(magic):
            return "image", mime
    if _is_bmp(raw):
        return "image", "image/bmp"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image", "image/webp"
    if raw[:4] == b"PK\x03\x04":
        if ext in ("xlsx", "xlsm"):
            return "xlsx", None
        if ext == "docx":
            return "docx", None
        try:
            names = set(zipfile.ZipFile(io.BytesIO(raw)).namelist())
        except Exception:
            return "other", None
        if "word/document.xml" in names:
            return "docx", None
        if "xl/workbook.xml" in names:
            return "xlsx", None
        return "other", None
    if raw[:8] == _OLE:
        return ("xls", None) if ext == "xls" or ct == "application/vnd.ms-excel" else ("other", None)
    if ext in ("csv", "tsv") or ct == "text/csv":
        return "csv", None
    if ext in ("txt", "log") or ct == "text/plain":
        return "text", None
    return "other", None


# ---------------------------------------------------------------- converters
def _fmt(v):
    """(text, is_number) for one cell."""
    if v is None:
        return "", False
    if isinstance(v, bool):
        return ("TRUE" if v else "FALSE"), False
    if isinstance(v, datetime.datetime):
        return (v.strftime("%Y-%m-%d") if v.time() == datetime.time() else v.strftime("%Y-%m-%d %H:%M")), True
    if isinstance(v, (datetime.date, datetime.time)):
        return v.isoformat(), True
    if isinstance(v, int):
        return f"{v:,}", True
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return str(v), True
        if v.is_integer() and abs(v) < 1e15:
            return f"{int(v):,}", True
        return f"{v:,.6f}".rstrip("0").rstrip("."), True
    return str(v), False


def _sheet(name, rows_iter, total_rows=None, total_cols=None):
    rows, more, wide = [], False, False
    for i, row in enumerate(rows_iter):
        if i >= MAX_ROWS:
            more = True
            break
        row = list(row)
        wide = wide or len(row) > MAX_COLS
        rows.append([_fmt(v) for v in row[:MAX_COLS]])
    while rows and not any(t for t, _n in rows[-1]):        # trailing blank rows
        rows.pop()
    width = max((max((j + 1 for j, (t, _n) in enumerate(r) if t), default=0) for r in rows), default=0)
    rows = [r[:width] + [("", False)] * (width - len(r[:width])) for r in rows]
    return {"name": name, "rows": rows,
            "more_rows": more or bool(total_rows and total_rows > MAX_ROWS),
            "more_cols": wide or bool(total_cols and total_cols > MAX_COLS)}


def _zip_guard(raw):
    zf = zipfile.ZipFile(io.BytesIO(raw))
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise TooBig("archive too large")
    for i in infos:     # parts read whole into memory (sharedStrings via openpyxl, document.xml via ET)
        n = i.filename
        if (n in ("xl/sharedStrings.xml", "word/document.xml") or n.startswith("xl/worksheets/")) \
                and i.file_size > MAX_PART:
            raise TooBig("part too large")
    return zf


def xlsx_sheets(raw):
    import openpyxl
    _zip_guard(raw)
    wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        return [_sheet(ws.title, ws.iter_rows(max_row=MAX_ROWS + 1, max_col=MAX_COLS, values_only=True),
                       ws.max_row, ws.max_column) for ws in wb.worksheets]
    finally:
        wb.close()


def xls_sheets(raw):
    import xlrd
    book = xlrd.open_workbook(file_contents=raw, on_demand=True)
    out = []
    for sh in book.sheets():
        def rows(sh=sh):
            for r in range(min(sh.nrows, MAX_ROWS + 1)):
                vals = []
                for cell in sh.row(r)[:MAX_COLS]:
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            vals.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                        except Exception:
                            vals.append(cell.value)
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        vals.append(bool(cell.value))
                    elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR):
                        vals.append(None)
                    else:
                        vals.append(cell.value)
                yield vals
        out.append(_sheet(sh.name, rows(), sh.nrows, sh.ncols))
    book.release_resources()
    return out


def _decode(raw):
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1256", "replace")      # Arabic Windows Excel exports


def csv_sheets(raw, filename):
    text = _decode(raw[:MAX_TEXT * 4])
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    sh = _sheet(filename or "CSV", csv.reader(io.StringIO(text), dialect))
    sh["more_rows"] = sh["more_rows"] or len(raw) > MAX_TEXT * 4
    return [sh]


def _para_text(p):
    out = []
    for el in p.iter():
        if el.tag == _W + "t":
            out.append(el.text or "")
        elif el.tag == _W + "tab":
            out.append("\t")
        elif el.tag in (_W + "br", _W + "cr"):
            out.append("\n")
    return "".join(out)


def docx_blocks(raw):
    """Paragraphs, headings, bullet lists and tables of word/document.xml.
    shortcut: embedded images and text formatting are skipped; upgrade if people ask."""
    zf = _zip_guard(raw)
    xml = zf.read("word/document.xml")
    if b"<!DOCTYPE" in xml[:4096] or b"<!ENTITY" in xml[:4096]:
        raise ValueError("DTD not allowed")             # entity-expansion guard
    body = ET.fromstring(xml).find(_W + "body")
    blocks, more = [], False

    def walk(parent):
        nonlocal more
        for el in parent:
            if len(blocks) >= MAX_BLOCKS:
                more = True
                return
            if el.tag == _W + "p":
                text = _para_text(el)
                ppr = el.find(_W + "pPr")
                style = ""
                if ppr is not None and ppr.find(_W + "pStyle") is not None:
                    style = ppr.find(_W + "pStyle").get(_W + "val") or ""
                low = style.lower()
                if ppr is not None and ppr.find(_W + "numPr") is not None or low.startswith("listbullet"):
                    if blocks and blocks[-1]["t"] == "ul":
                        blocks[-1]["items"].append(text)
                    else:
                        blocks.append({"t": "ul", "items": [text]})
                elif low.startswith("heading") or low == "title":
                    digits = "".join(ch for ch in style if ch.isdigit())
                    blocks.append({"t": "h", "level": min(int(digits or 1), 4), "text": text})
                else:
                    blocks.append({"t": "p", "text": text})
            elif el.tag == _W + "tbl":
                rows = []
                trs = el.findall(_W + "tr")
                more = more or len(trs) > MAX_ROWS
                for tr in trs[:MAX_ROWS]:
                    tcs = tr.findall(_W + "tc")
                    more = more or len(tcs) > MAX_COLS
                    rows.append(["\n".join(_para_text(p) for p in tc.findall(_W + "p"))
                                 for tc in tcs[:MAX_COLS]])
                blocks.append({"t": "table", "rows": rows})
            elif el.tag == _W + "sdt":
                content = el.find(_W + "sdtContent")
                if content is not None:
                    walk(content)

    if body is not None:
        walk(body)
    while blocks and blocks[-1]["t"] == "p" and not blocks[-1]["text"].strip():
        blocks.pop()
    return blocks, more


# ---------------------------------------------------------------- responses
def _url(**extra):
    """This same route (same-origin, relative), with view/inline/download swapped."""
    args = [(k, v) for k, v in request.args.items(multi=True) if k not in ("view", "inline", "download")]
    args += list(extra.items())
    return request.script_root + request.path + ("?" + urlencode(args) if args else "")


def _download(raw, filename):
    return send_file(io.BytesIO(raw), mimetype="application/octet-stream",
                     as_attachment=True, download_name=filename or "file")


def wanted():
    return any(request.args.get(k) == "1" for k in ("view", "inline", "download"))


def viewer_context(raw, content_type, filename):
    raw = raw or b""
    kind, _mime = sniff(raw, content_type, filename)
    fv = {"kind": kind, "filename": filename or "file", "size": len(raw),
          "raw_url": _url(inline="1"), "download_url": _url(download="1"),
          "sheets": None, "blocks": None, "text": None, "more": False, "error": False}
    if kind in ("xlsx", "xls", "csv", "docx", "text") and len(raw) > MAX_PARSE:
        fv.update(kind="other", error="big")
        return fv
    try:
        if kind == "xlsx":
            fv["sheets"] = xlsx_sheets(raw)
        elif kind == "xls":
            fv["sheets"] = xls_sheets(raw)
        elif kind == "csv":
            fv["sheets"] = csv_sheets(raw, fv["filename"])
        elif kind == "docx":
            fv["blocks"], fv["more"] = docx_blocks(raw)
        elif kind == "text":
            text = _decode(raw)
            fv["text"], fv["more"] = text[:MAX_TEXT], len(text) > MAX_TEXT
    except TooBig:
        fv.update(kind="other", error="big", sheets=None, blocks=None, text=None)
    except Exception:  # noqa: BLE001 — any malformed upload gets the friendly card, never a 500
        fv.update(kind="other", error="bad", sheets=None, blocks=None, text=None)
    return fv


def file_response(raw, content_type, filename, as_attachment=True):
    """Call as the LAST line of a download route, after its permission checks."""
    if request.args.get("view") == "1":
        return render_template("file_view.html", fv=viewer_context(raw, content_type, filename))
    if request.args.get("inline") == "1":
        kind, mime = sniff(raw, content_type, filename)
        if kind not in ("pdf", "image"):
            return _download(raw, filename)          # SVG/HTML/unknown: never rendered on our origin
        resp = send_file(io.BytesIO(raw), mimetype=mime, as_attachment=False,
                         download_name=filename or "file")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Cache-Control"] = "private, no-store"
        return resp
    if request.args.get("download") == "1":
        # never echo the uploader's claimed type (text/html, image/svg+xml...) back out
        resp = _download(raw, filename)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp
    return send_file(io.BytesIO(raw), mimetype=content_type or "application/octet-stream",
                     as_attachment=as_attachment, download_name=filename or "file")
