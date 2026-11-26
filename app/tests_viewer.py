"""In-app file viewer (app/services/file_view.py): converters, the
view / inline / download branches, XSS refusals, malformed files and one real
route end to end.

    python -m app.tests_viewer
"""
import base64
import datetime
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
HTML = b"<html><body><script>alert(1)</script></body></html>"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(body):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, body))
    return buf.getvalue()


def _xlsx():
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Lines"
    ws.append(["Item", "Qty", "Date"])
    ws.append(["Fake toner <b>", 1500, datetime.datetime(2026, 1, 2)])
    ws.append(["حبر تجريبي", 2.5, None])
    wb.create_sheet("Empty")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "viewer.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def converters():
    from app.services import file_view as fv

    # -- sniffing: the BYTES decide pdf/image, never the claimed type
    assert fv.sniff(PDF, "application/octet-stream", "x.bin") == ("pdf", "application/pdf")
    assert fv.sniff(PNG, "application/octet-stream", "x") == ("image", "image/png")
    assert fv.sniff(b"\xff\xd8\xff\xe0" + b"0" * 10, "", "a.jpg") == ("image", "image/jpeg")
    assert fv.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "", "a.webp") == ("image", "image/webp")
    assert fv.sniff(SVG, "image/svg+xml", "evil.svg")[0] == "other"
    assert fv.sniff(HTML, "text/html", "evil.html")[0] == "other"
    assert fv.sniff(HTML, "image/png", "evil.png")[0] == "other"          # lying type
    assert fv.sniff(b"\x00\x00\x00 ftypheic", "image/heic", "p.heic")[0] == "other"
    assert fv.sniff(_xlsx(), "application/octet-stream", "noext")[0] == "xlsx"
    assert fv.sniff(_docx("<w:p/>"), "application/octet-stream", "noext")[0] == "docx"
    assert fv.sniff(b"a,b\n1,2\n", "", "x.csv")[0] == "csv"
    assert fv.sniff(b"hello", "text/plain", "x")[0] == "text"
    assert fv.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8, "", "old.doc")[0] == "other"

    # -- xlsx: sheets, header, numbers, dates, escaping left to the template
    sheets = fv.xlsx_sheets(_xlsx())
    assert [s["name"] for s in sheets] == ["Lines", "Empty"], sheets
    rows = sheets[0]["rows"]
    assert rows[0] == [("Item", False), ("Qty", False), ("Date", False)], rows[0]
    assert rows[1] == [("Fake toner <b>", False), ("1,500", True), ("2026-01-02", True)], rows[1]
    assert rows[2][1] == ("2.5", True) and rows[2][2] == ("", False), rows[2]
    assert sheets[1]["rows"] == [] and not sheets[0]["more_rows"]

    # -- row/column caps
    old = fv.MAX_ROWS, fv.MAX_COLS
    fv.MAX_ROWS, fv.MAX_COLS = 2, 2
    try:
        s = fv.xlsx_sheets(_xlsx())[0]
        assert len(s["rows"]) == 2 and len(s["rows"][0]) == 2 and s["more_rows"] and s["more_cols"], s
    finally:
        fv.MAX_ROWS, fv.MAX_COLS = old

    # -- csv (semicolon, BOM, Arabic) and cp1256 fallback
    s = fv.csv_sheets("﻿name;qty\nقلم;3\n".encode("utf-8"), "x.csv")[0]
    assert s["rows"] == [[("name", False), ("qty", False)], [("قلم", False), ("3", False)]], s
    s = fv.csv_sheets("صنف,1\n".encode("cp1256"), "x.csv")[0]
    assert s["rows"][0][0][0] == "صنف", s

    # -- docx: heading, paragraph with break, bullets, table
    body = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Title &lt;x&gt;</w:t></w:r></w:p>'
            '<w:p><w:r><w:t>Line one</w:t><w:br/><w:t>Line two</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>A</w:t></w:r></w:p>'
            '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>B</w:t></w:r></w:p>'
            '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>c1</w:t></w:r></w:p></w:tc>'
            '<w:tc><w:p><w:r><w:t>c2</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')
    blocks, more = fv.docx_blocks(_docx(body))
    assert blocks == [{"t": "h", "level": 1, "text": "Title <x>"},
                      {"t": "p", "text": "Line one\nLine two"},
                      {"t": "ul", "items": ["A", "B"]},
                      {"t": "table", "rows": [["c1", "c2"]]}] and not more, blocks

    # -- malformed / hostile files -> friendly fallback, never an exception
    junk = b"PK\x03\x04" + b"garbage" * 50
    for name in ("bad.xlsx", "bad.docx"):
        ctx = fv.viewer_context(junk, "", name)
        assert ctx["kind"] == "other" and ctx["error"] == "bad", (name, ctx)
    bomb = _docx('<w:p><w:r><w:t>%s</w:t></w:r></w:p>' % ("x" * 5000))
    old = fv.MAX_UNZIPPED
    fv.MAX_UNZIPPED = 1000
    try:
        assert fv.viewer_context(bomb, "", "bomb.docx")["error"] == "bad"
    finally:
        fv.MAX_UNZIPPED = old
    dtd = _docx("").replace(b'<?xml version="1.0"?>', b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>')
    assert fv.viewer_context(dtd, "", "dtd.docx")["error"] == "bad"
    old = fv.MAX_PARSE
    fv.MAX_PARSE = 10
    try:
        assert fv.viewer_context(b"a,b\n" * 10, "", "x.csv")["error"] == "big"
    finally:
        fv.MAX_PARSE = old
    assert fv.viewer_context(b"\x00\x01", "", "x.bin")["kind"] == "other"
    print("converters ok")


def branches(app):
    from app.services import file_view as fv
    for qs, raw, ct, name, want_ct, want_disp in (
            ("", PDF, "application/pdf", "a.pdf", "application/pdf", "attachment"),     # unchanged default
            ("?inline=1", PDF, "application/octet-stream", "a.pdf", "application/pdf", "inline"),
            ("?inline=1", PNG, "", "p.png", "image/png", "inline"),
            ("?inline=1", SVG, "image/svg+xml", "e.svg", "application/octet-stream", "attachment"),
            ("?inline=1", HTML, "text/html", "e.html", "application/octet-stream", "attachment"),
            ("?inline=1", HTML, "image/png", "e.png", "application/octet-stream", "attachment"),
            ("?download=1", PDF, "application/pdf", "a.pdf", "application/pdf", "attachment")):
        with app.test_request_context("/x" + qs):
            r = fv.file_response(raw, ct, name)
            r.direct_passthrough = False
            assert r.mimetype == want_ct, (qs, name, r.mimetype)
            assert r.headers["Content-Disposition"].startswith(want_disp), (qs, name, r.headers)
            if want_disp == "inline":
                assert r.headers["X-Content-Type-Options"] == "nosniff"
    with app.test_request_context("/x"):
        r = fv.file_response(PDF, "application/pdf", "a.pdf", as_attachment=False)
        assert r.headers["Content-Disposition"].startswith("inline")                  # PR/PO PDFs as before
    with app.test_request_context("/p/1/po.pdf?po=7&view=1"):
        assert fv._url(inline="1") == "/p/1/po.pdf?po=7&inline=1"
        assert fv._url(download="1") == "/p/1/po.pdf?po=7&download=1"
    print("branches ok")


def end_to_end(app):
    from flask import g
    app.before_request(lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import services as svc
        from app.db import get_db
        c = get_db()
        ids = {}
        for un in ("fv.staff", "fv.other"):
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, 'normal_user', 1, '2026-01-01')", (un, un))
            ids[un] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        c.commit()
        c.close()
        staff = {"username": "fv.staff", "id": ids["fv.staff"], "role": "normal_user"}
        pid, _ = svc.create_pr({"title": "Viewer test", "department": "IT"},
                               [{"item": "Fake item", "qty": 1, "unit_price": 10}], staff, submit=False, priced=True)
        files = {"q.pdf": (PDF, "application/pdf"), "evil.svg": (SVG, "image/svg+xml"),
                 "lines.xlsx": (_xlsx(), "application/octet-stream")}
        for name, (raw, ct) in files.items():
            assert svc.add_attachment(pid, name, ct, base64.b64encode(raw).decode(), len(raw), staff)[0]
        c = get_db()
        att = {r["filename"]: r["id"] for r in c.execute(
            "SELECT id, filename FROM pr_attachments WHERE pr_id=?", (pid,)).fetchall()}
        c.close()

        def client(uid):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
            return cli

        me = client(ids["fv.staff"])
        page = me.get("/procurement/pr/%d" % pid).get_data(as_text=True)
        assert "/procurement/attachment/%d?view=1" % att["q.pdf"] in page and "data-file-view" in page

        r = me.get("/procurement/attachment/%d?view=1" % att["q.pdf"])
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and 'id="fvPdf"' in html and "q.pdf" in html, r.status_code
        assert "/procurement/attachment/%d?inline=1" % att["q.pdf"] in html
        assert 'class="topbar' in html or "<header" in html                              # app chrome kept
        r = me.get("/procurement/attachment/%d?inline=1" % att["q.pdf"])
        assert r.mimetype == "application/pdf" and r.headers["Content-Disposition"].startswith("inline")
        r = me.get("/procurement/attachment/%d" % att["q.pdf"])                          # as before
        assert r.headers["Content-Disposition"].startswith("attachment") and r.data == PDF

        r = me.get("/procurement/attachment/%d?inline=1" % att["evil.svg"])
        assert r.mimetype == "application/octet-stream" and r.headers["Content-Disposition"].startswith("attachment")
        html = me.get("/procurement/attachment/%d?view=1" % att["evil.svg"]).get_data(as_text=True)
        assert "fv.none_title" in html and "<script>alert" not in html

        html = me.get("/procurement/attachment/%d?view=1" % att["lines.xlsx"]).get_data(as_text=True)
        assert "Fake toner &lt;b&gt;" in html and "Fake toner <b>" not in html and 'role="tab"' in html

        r = me.get("/procurement/pr/%d/pdf?view=1" % pid)
        assert r.status_code == 200 and 'id="fvPdf"' in r.get_data(as_text=True)

        # another plain requester: refused on every branch, the viewer included
        other = client(ids["fv.other"])
        for qs in ("?view=1", "?inline=1", "?download=1", ""):
            r = other.get("/procurement/attachment/%d%s" % (att["q.pdf"], qs))
            assert r.status_code in (403, 404), (qs, r.status_code)
            assert b"%PDF" not in r.data and b"fvPdf" not in r.data
        r = app.test_client().get("/procurement/attachment/%d?view=1" % att["q.pdf"])  # signed out
        assert r.status_code in (302, 401), r.status_code
    print("end to end ok")


def run():
    app = _app()
    with app.test_request_context("/x?view=1"):
        converters()
    branches(app)
    end_to_end(app)
    print("tests_viewer: ALL OK")


if __name__ == "__main__":
    run()
