"""A requisition buys from several suppliers — one vendor per LINE.

The vendor used to live only on the request header, so a six-line PR naming two
suppliers attributed all six lines to one of them. pr_items.vendor and the
services.py fallback already existed; what was missing was the form field, the
parser and the display. This checks the whole pipe, and — just as important —
that a line left blank still inherits the header vendor, which is what every
request raised before today relies on.

    python app/approvals/tests_line_vendor.py
"""
import base64
import os
import re
import sys
import tempfile
from pathlib import Path
import zlib


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "linevendor.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


def _pdf_text(blob):
    """The PDF's content streams, decoded (reportlab writes them ASCII85 + Flate)
    so the printed words can be read back in the order they were drawn."""
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", blob, re.S):
        data = m.group(1)
        for step in (lambda x: base64.a85decode(x, adobe=True), zlib.decompress):
            try:
                data = step(data)
            except Exception:                       # noqa: BLE001 — image / raw stream
                pass
        out.append(data)
    return b"\n".join(out)


def run():
    app = _app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc, pdf
        from app.routes.approvals import _parse_items
        from werkzeug.datastructures import MultiDict

        buyer = {"username": "buyer", "id": 1}
        # Single-word fixtures on purpose: the PR form's VENDOR column wraps, so
        # a two-word supplier is drawn as two separate runs and could not be
        # read back in row order below.
        HEAD, A, B = "Nilehouse", "Alphatex", "Betatrim"

        # --- 1. the form's POST is read positionally, blank lines included ----
        items = _parse_items(MultiDict([
            ("item[]", "Cotton twill"), ("qty[]", "500"), ("vendor[]", A),
            ("item[]", "Sewing thread"), ("qty[]", "40"), ("vendor[]", "  "),
            ("item[]", "Metal zippers"), ("qty[]", "2000"), ("vendor[]", B),
        ]))
        assert [i["vendor"] for i in items] == [A, "", B], items

        # a form that posts no vendor[] at all (an older cached page) must not
        # throw and must leave every line inheriting the header
        legacy = _parse_items(MultiDict([("item[]", "Bearing 6204"), ("qty[]", "4")]))
        assert legacy[0]["vendor"] == "", legacy

        # --- 2. stored: three lines, three different suppliers ---------------
        pr_id, _ = svc.create_pr({"title": "Trims and fabric for style 4471",
                                  "department": "Production", "vendor": HEAD},
                                 items, buyer, submit=False)
        conn = get_db()
        stored = [r["vendor"] for r in conn.execute(
            "SELECT vendor FROM pr_items WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
        conn.close()
        assert stored == [A, HEAD, B], stored          # blank line inherited

        # --- 3. an edit does not collapse them back to the header ------------
        b = svc.get_pr(pr_id)
        ok, msg = svc.update_pr(pr_id, {"title": "Trims and fabric for style 4471",
                                        "department": "Production", "vendor": HEAD},
                                [dict(it) for it in b["items"]], buyer)
        assert ok, msg
        conn = get_db()
        stored = [r["vendor"] for r in conn.execute(
            "SELECT vendor FROM pr_items WHERE pr_id=? ORDER BY seq", (pr_id,)).fetchall()]
        conn.close()
        assert stored == [A, HEAD, B], stored

        # --- 4. the printed PR carries the line's own vendor, row by row -----
        #     The 21-column paper form has ONE vendor column; it used to print
        #     the header vendor on every row. Rows are drawn top-down, so the
        #     order the three names appear in the content stream IS the order
        #     they are printed against the three lines.
        text = _pdf_text(pdf.pr_pdf(svc.get_pr(pr_id)))
        seen = [w.decode() for w in re.findall(
            b"|".join(n.encode() for n in (A, B, HEAD)), text)]
        assert seen == [A, HEAD, B], seen

        # --- 5. the single-supplier request is untouched ---------------------
        one, _ = svc.create_pr({"title": "Bearings for line 3",
                                "department": "Maintenance", "vendor": HEAD},
                               [{"item": "Bearing 6204", "qty": 4},
                                {"item": "Grease cartridge", "qty": 2}],
                               buyer, submit=False)
        conn = get_db()
        stored = [r["vendor"] for r in conn.execute(
            "SELECT vendor FROM pr_items WHERE pr_id=? ORDER BY seq", (one,)).fetchall()]
        conn.close()
        assert stored == [HEAD, HEAD], stored

        print("PASS: every line carries its own vendor; a blank line inherits the header")
        return True


if __name__ == "__main__":
    run()
