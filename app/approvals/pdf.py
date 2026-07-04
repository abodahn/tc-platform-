"""
TC Platform — Procurement PDF generation (Purchase Request + Purchase Order).

Renders the digital PR/PO into a clean, gridded document with the T&C brand mark,
a bordered item table, boxed metadata, an approval-signature grid (embedding each
approver's stamped digital signature), a totals box, and the standard footer
notes. Pure reportlab (already vendored for BI); no external services.
"""
import base64
import io
import os

_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "static", "img")


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _img_reader(data_url):
    """data:image/png;base64,... -> reportlab ImageReader, or None."""
    if not data_url or "," not in data_url:
        return None
    try:
        from reportlab.lib.utils import ImageReader
        return ImageReader(io.BytesIO(base64.b64decode(data_url.split(",", 1)[1])))
    except Exception:
        return None


def _logo():
    """Prefer the full brand lockup (mark + wordmark) for the letterhead."""
    try:
        from reportlab.lib.utils import ImageReader
        for name in ("logo-full.png", "logo.png", "icon-192.png"):
            p = os.path.join(_IMG_DIR, name)
            if os.path.exists(p):
                return ImageReader(p)
    except Exception:
        pass
    return None


def _fmt(n):
    try:
        return f"{float(n):,.2f}"
    except (TypeError, ValueError):
        return str(n or "")


# --- number to words (English) ---------------------------------------------
_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALE = [(1_000_000_000, "billion"), (1_000_000, "million"), (1_000, "thousand")]
_CUR_NAMES = {"EGP": ("Egyptian Pounds", "piastres"), "USD": ("US Dollars", "cents"),
              "EUR": ("Euros", "cents"), "TRY": ("Turkish Lira", "kuruş")}


def _under_1000(n):
    out = ""
    if n >= 100:
        out += _ONES[n // 100] + " hundred"
        n %= 100
        if n:
            out += " and "
    if n >= 20:
        out += _TENS[n // 10]
        if n % 10:
            out += "-" + _ONES[n % 10]
    elif n:
        out += _ONES[n]
    return out


def _num_to_words(n):
    n = int(n)
    if n == 0:
        return "zero"
    parts = []
    for value, name in _SCALE:
        if n >= value:
            parts.append(_under_1000(n // value) + " " + name)
            n %= value
    if n:
        parts.append(_under_1000(n))
    return " ".join(parts).strip()


def _amount_words(amount, currency):
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        amount = 0.0
    whole = int(amount)
    frac = int(round((amount - whole) * 100))
    cur_name, sub_name = _CUR_NAMES.get(currency, (currency or "", "cents"))
    s = _num_to_words(whole).capitalize() + " " + cur_name
    if frac:
        s += " and " + _num_to_words(frac) + " " + sub_name
    return s + " only"


def _clip(c, s, font, size, max_w):
    """Truncate a string with an ellipsis so it fits max_w points."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    s = str(s or "")
    if stringWidth(s, font, size) <= max_w:
        return s
    while s and stringWidth(s + "…", font, size) > max_w:
        s = s[:-1]
    return s + "…"


def _wrap_lines(c, text, font, size, max_w, max_lines=2):
    """Word-wrap text to at most max_lines that each fit max_w points; the last
    line is ellipsised if content is dropped."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    words = str(text or "").split()
    lines, cur, i = [], "", 0
    while i < len(words) and len(lines) < max_lines:
        nxt = (cur + " " + words[i]).strip()
        if stringWidth(nxt, font, size) <= max_w:
            cur, i = nxt, i + 1
        elif cur:
            lines.append(cur)
            cur = ""
        else:  # a single word longer than the column
            cur, i = words[i], i + 1
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if i < len(words) and lines:  # more text remained -> ellipsise last line
        last = lines[-1]
        while last and stringWidth(last + "…", font, size) > max_w:
            last = last[:-1]
        lines[-1] = last + "…"
    return lines or [""]


# --------------------------------------------------------------------------
# shared chrome
# --------------------------------------------------------------------------
def _draw_header(c, w, h, cm, title, doc_no, sub):
    """Clean white letterhead: full brand lockup on the left, document title on the
    right, and a red rule beneath. Returns the y to start body content at."""
    top = h - 0.55 * cm
    logo_h = 2.35 * cm
    logo = _logo()
    if logo:
        try:
            iw, ih = logo.getSize()
            lw = logo_h * (iw / ih) if ih else logo_h
            c.drawImage(logo, 1.5 * cm, top - logo_h, width=lw, height=logo_h,
                        mask="auto", preserveAspectRatio=True, anchor="sw")
        except Exception:
            pass
    # right-aligned title block
    c.setFillColorRGB(0.11, 0.11, 0.13)
    c.setFont("Helvetica-Bold", 19)
    c.drawRightString(w - 1.5 * cm, top - 0.8 * cm, title)
    c.setFont("Helvetica-Bold", 11)
    c.setFillColorRGB(0.80, 0.06, 0.14)
    c.drawRightString(w - 1.5 * cm, top - 1.45 * cm, doc_no)
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.45, 0.45, 0.45)
    c.drawRightString(w - 1.5 * cm, top - 2.0 * cm, sub)
    # red separator rule under the header
    ry = top - logo_h - 0.05 * cm
    c.setStrokeColorRGB(0.93, 0.11, 0.14)
    c.setLineWidth(2.2)
    c.line(1.5 * cm, ry, w - 1.5 * cm, ry)
    c.setFillColorRGB(0, 0, 0)
    c.setStrokeColorRGB(0, 0, 0)
    return ry - 0.55 * cm


def _footer(c, w, cm, page, notes=None):
    c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.setLineWidth(0.5)
    c.line(1.5 * cm, 1.5 * cm, w - 1.5 * cm, 1.5 * cm)
    c.setFont("Helvetica", 6.8)
    c.setFillColorRGB(0.5, 0.5, 0.5)
    y = 1.25 * cm
    for line in (notes or []):
        c.drawString(1.5 * cm, y, line)
        y -= 0.28 * cm
    c.drawRightString(w - 1.5 * cm, 1.25 * cm, f"TC Platform · page {page}")
    c.setFillColorRGB(0, 0, 0)


def _meta_grid(c, w, cm, y, pairs):
    """Two-column label/value block inside a light box. Returns the new y."""
    rows = (len(pairs) + 1) // 2
    box_h = rows * 0.62 * cm + 0.5 * cm
    c.setStrokeColorRGB(0.88, 0.88, 0.9)
    c.setLineWidth(0.6)
    c.roundRect(1.5 * cm, y - box_h, w - 3 * cm, box_h, 4, stroke=1, fill=0)
    colw = (w - 3 * cm) / 2
    yy = y - 0.55 * cm
    for i, (label, val) in enumerate(pairs):
        col = i % 2
        x = 1.7 * cm + col * colw
        c.setFont("Helvetica", 7.5)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawString(x, yy, label.upper())
        c.setFont("Helvetica-Bold", 9.5)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawString(x, yy - 0.33 * cm, _clip(c, val or "—", "Helvetica-Bold", 9.5, colw - 0.6 * cm))
        if col == 1:
            yy -= 0.62 * cm
    c.setFillColorRGB(0, 0, 0)
    return y - box_h - 0.4 * cm


def _table(c, w, cm, y, cols, rows, h, page_notes, title=None, wrap_col=None, max_lines=4):
    """Bordered table with variable-height rows. cols = [(width_cm, header, align)].
    wrap_col makes that column a rich cell: pass the cell as (item, description) —
    the item renders bold and the description wraps beneath it (up to max_lines),
    and the row grows to fit. Paginates automatically, repeating the header."""
    x0 = 1.5 * cm
    total_w = w - 3 * cm
    widths = [cw * cm for cw, _, _ in cols]
    scale = total_w / sum(widths)
    widths = [wd * scale for wd in widths]
    hrh = 0.6 * cm
    line_h = 0.36 * cm
    base_rh = 0.6 * cm

    def header(yy):
        c.setFillColorRGB(0.11, 0.12, 0.16)
        c.rect(x0, yy - hrh, total_w, hrh, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 8)
        xx = x0
        for (cw, label, align), wd in zip(cols, widths):
            if align == "r":
                c.drawRightString(xx + wd - 0.18 * cm, yy - hrh + 0.19 * cm, label)
            else:
                c.drawString(xx + 0.18 * cm, yy - hrh + 0.19 * cm, label)
            xx += wd
        c.setFillColorRGB(0, 0, 0)
        return yy - hrh

    def cell_lines(cell, wd):
        """Rich wrap cell -> list of (bold?, text)."""
        head, body = (cell if isinstance(cell, (list, tuple)) else (None, cell))
        out = []
        if head:
            out.append((True, _clip(c, head, "Helvetica-Bold", 8.6, wd - 0.36 * cm)))
        for ln in _wrap_lines(c, body, "Helvetica", 8.2, wd - 0.36 * cm, max_lines):
            if ln:
                out.append((False, ln))
        return out or [(False, "")]

    if title:
        c.setFont("Helvetica-Bold", 10.5)
        c.drawString(x0, y, title)
        y -= 0.52 * cm
    y = header(y)
    body_top = y
    for i, row in enumerate(rows):
        lines = cell_lines(row[wrap_col], widths[wrap_col]) if wrap_col is not None else None
        rh = max(base_rh, len(lines) * line_h + 0.22 * cm) if lines else base_rh
        if y - rh < 3 * cm:                              # page break
            _table_borders(c, x0, y, body_top, total_w, widths)
            _footer(c, w, cm, page_notes["page"], page_notes.get("notes"))
            c.showPage()
            page_notes["page"] += 1
            y = h - 2 * cm
            y = header(y)
            body_top = y
        if i % 2 == 1:                                   # zebra
            c.setFillColorRGB(0.973, 0.973, 0.981)
            c.rect(x0, y - rh, total_w, rh, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)
        cyc = y - rh / 2 - 0.09 * cm                      # single-line vertical centre
        xx = x0
        for idx, ((cw, _, align), wd, cell) in enumerate(zip(cols, widths, row)):
            if idx == wrap_col:
                ly = y - 0.36 * cm
                for is_bold, txt in lines:
                    if is_bold:
                        c.setFont("Helvetica-Bold", 8.6)
                        c.setFillColorRGB(0.1, 0.1, 0.13)
                    else:
                        c.setFont("Helvetica", 8.2)
                        c.setFillColorRGB(0.34, 0.34, 0.38)
                    c.drawString(xx + 0.18 * cm, ly, txt)
                    ly -= line_h
                c.setFillColorRGB(0, 0, 0)
            else:
                c.setFont("Helvetica", 8.5)
                txt = _clip(c, cell, "Helvetica", 8.5, wd - 0.32 * cm)
                if align == "r":
                    c.drawRightString(xx + wd - 0.18 * cm, cyc, txt)
                else:
                    c.drawString(xx + 0.18 * cm, cyc, txt)
            xx += wd
        y -= rh
        if i < len(rows) - 1:                            # inner row separator
            c.setStrokeColorRGB(0.9, 0.9, 0.92)
            c.setLineWidth(0.4)
            c.line(x0, y, x0 + total_w, y)
    _table_borders(c, x0, y, body_top, total_w, widths)
    return y - 0.35 * cm


def _table_borders(c, x0, y_bottom, y_top, total_w, widths):
    """Outer border + full-height column separators for the current body block."""
    c.setStrokeColorRGB(0.78, 0.78, 0.81)
    c.setLineWidth(0.5)
    c.rect(x0, y_bottom, total_w, y_top - y_bottom, stroke=1, fill=0)
    xx = x0
    for wd in widths[:-1]:
        xx += wd
        c.line(xx, y_bottom, xx, y_top)


def _totals_block(c, w, cm, y, pr, cur):
    """Right-aligned Subtotal / VAT / Grand Total box + amount-in-words line."""
    subtotal = 0.0
    try:
        subtotal = float(pr.get("total") or 0)
    except (TypeError, ValueError):
        pass
    try:
        rate = float(pr.get("tax_rate") or 0)
    except (TypeError, ValueError):
        rate = 0.0
    tax = round(subtotal * rate / 100.0, 2)
    grand = round(subtotal + tax, 2)

    lines = [("Subtotal", subtotal)]
    if rate:
        lines.append((f"VAT ({rate:g}%)", tax))
    bx_w = 7.0 * cm
    bx = w - 1.5 * cm - bx_w
    row_h = 0.5 * cm
    bx_h = row_h * len(lines) + 0.75 * cm
    c.setStrokeColorRGB(0.85, 0.85, 0.87)
    c.setLineWidth(0.6)
    c.roundRect(bx, y - bx_h, bx_w, bx_h, 4, stroke=1, fill=0)
    yy = y - 0.45 * cm
    c.setFont("Helvetica", 9)
    for label, val in lines:
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawString(bx + 0.3 * cm, yy, label)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawRightString(bx + bx_w - 0.3 * cm, yy, f"{_fmt(val)} {cur}")
        yy -= row_h
    # grand total band
    c.setFillColorRGB(0.93, 0.11, 0.14)
    c.roundRect(bx, y - bx_h, bx_w, 0.7 * cm, 4, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(bx + 0.3 * cm, y - bx_h + 0.24 * cm, "GRAND TOTAL")
    c.drawRightString(bx + bx_w - 0.3 * cm, y - bx_h + 0.24 * cm, f"{_fmt(grand)} {cur}")
    c.setFillColorRGB(0, 0, 0)
    y -= bx_h + 0.5 * cm
    # amount in words
    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.drawString(1.5 * cm, y, "Amount in words: " + _amount_words(grand, pr.get("currency")))
    c.setFillColorRGB(0, 0, 0)
    return y - 0.7 * cm


# --------------------------------------------------------------------------
# Purchase Request
# --------------------------------------------------------------------------
_PR_NOTES = [
    "• Should be filled or revised by the warehouse including all needed information & specifications.",
    "• Lead time for any request is 15 working days from delivering the PR to the purchasing department;",
    "  any delay for any reason will be reported to the requester.",
]


def pr_pdf(bundle):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr, items, steps = bundle["pr"], bundle["items"], bundle["steps"]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pn = {"page": 1, "notes": _PR_NOTES}

    y = _draw_header(c, w, h, cm, "PURCHASE REQUEST", pr.get("pr_no") or "PR",
                     "STATUS: " + (pr.get("status") or "").upper())

    y = _meta_grid(c, w, cm, y, [
        ("Title", pr.get("title")), ("Request for", pr.get("request_for")),
        ("Requester", pr.get("requester_name") or pr.get("requester")),
        ("Department", pr.get("department")),
        ("Request date", pr.get("request_date")), ("Vendor", pr.get("vendor")),
        ("Payment", pr.get("payment_condition")), ("Delivery", pr.get("delivery_condition")),
    ])

    cur = pr.get("currency") or ""
    rows = [[str(i), (it.get("item") or "", it.get("description") or ""),
             it.get("unit") or "", _fmt(it.get("qty")), _fmt(it.get("current_stock")),
             _fmt(it.get("unit_price")), _fmt(it.get("est_cost"))]
            for i, it in enumerate(items, 1)]
    y = _table(c, w, cm, y, [
        (0.6, "#", "l"), (7.5, "ITEM / DESCRIPTION", "l"),
        (1.3, "UNIT", "l"), (1.2, "QTY", "r"), (1.4, "STOCK", "r"),
        (2.2, "UNIT PRICE", "r"), (2.4, "EST. COST", "r")], rows, h, pn,
        title="Line items", wrap_col=1)

    y = _totals_block(c, w, cm, y, pr, cur)

    # signatures
    _signature_grid(c, w, h, cm, y, pr, steps, pn)
    _footer(c, w, cm, pn["page"], _PR_NOTES)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def _signature_grid(c, w, h, cm, y, pr, steps, pn):
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1.5 * cm, y, "Approval signatures")
    y -= 0.35 * cm
    blocks = [{"role": "Requester", "name": pr.get("requester_name") or pr.get("requester"),
               "sig": None, "date": (pr.get("submitted_at") or pr.get("request_date") or "")[:10],
               "status": "originator"}]
    for s in steps:
        blocks.append({"role": s.get("approver_role") or s.get("stage"),
                       "name": s.get("approver_name"), "sig": s.get("sig_png"),
                       "date": (s.get("acted_at") or "")[:10], "status": s.get("status")})

    per_row, gap = 3, 0.3 * cm
    bw = (w - 3 * cm - (per_row - 1) * gap) / per_row
    bh = 2.5 * cm
    x0 = 1.5 * cm
    for idx, b in enumerate(blocks):
        col = idx % per_row
        if col == 0:
            y -= bh + 0.3 * cm
            if y < 2.2 * cm:
                _footer(c, w, cm, pn["page"], pn.get("notes"))
                c.showPage()
                pn["page"] += 1
                y = h - 2.5 * cm - bh
        x = x0 + col * (bw + gap)
        # header strip
        c.setFillColorRGB(0.11, 0.12, 0.16)
        c.roundRect(x, y + bh - 0.55 * cm, bw, 0.55 * cm, 3, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 0.2 * cm, y + bh - 0.38 * cm, _clip(c, b["role"], "Helvetica-Bold", 8, bw - 0.4 * cm))
        # body box
        c.setStrokeColorRGB(0.82, 0.82, 0.85)
        c.setLineWidth(0.6)
        c.rect(x, y, bw, bh - 0.55 * cm, stroke=1, fill=0)
        img = _img_reader(b["sig"])
        if img:
            try:
                c.drawImage(img, x + 0.25 * cm, y + 0.75 * cm, width=bw - 0.5 * cm,
                            height=1.0 * cm, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass
        elif b["status"] == "pending":
            c.setFont("Helvetica-Oblique", 8)
            c.setFillColorRGB(0.65, 0.65, 0.65)
            c.drawCentredString(x + bw / 2, y + 1.15 * cm, "awaiting signature")
        elif b["status"] == "rejected":
            c.setFont("Helvetica-Bold", 9)
            c.setFillColorRGB(0.75, 0.07, 0.23)
            c.drawCentredString(x + bw / 2, y + 1.15 * cm, "REJECTED")
        # name + date footer line
        c.setStrokeColorRGB(0.9, 0.9, 0.9)
        c.line(x + 0.2 * cm, y + 0.6 * cm, x + bw - 0.2 * cm, y + 0.6 * cm)
        c.setFont("Helvetica", 7.2)
        c.setFillColorRGB(0.35, 0.35, 0.35)
        meta = (b["name"] or "—") + (("  ·  " + b["date"]) if b["date"] else "")
        c.drawString(x + 0.2 * cm, y + 0.28 * cm, _clip(c, meta, "Helvetica", 7.2, bw - 0.4 * cm))
        c.setFillColorRGB(0, 0, 0)
    return y


# --------------------------------------------------------------------------
# Purchase Order
# --------------------------------------------------------------------------
def po_pdf(bundle):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr, items = bundle["pr"], bundle["items"]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pn = {"page": 1, "notes": None}

    y = _draw_header(c, w, h, cm, "PURCHASE ORDER", pr.get("po_no") or "PO",
                     "Ref " + (pr.get("pr_no") or ""))

    y = _meta_grid(c, w, cm, y, [
        ("Vendor", pr.get("vendor")), ("Department", pr.get("department")),
        ("Payment terms", pr.get("payment_condition")),
        ("Delivery", pr.get("delivery_condition")),
        ("Approved on", (pr.get("approved_at") or "")[:10]),
        ("Currency", pr.get("currency")),
    ])

    cur = pr.get("currency") or ""
    rows = [[str(i), (it.get("item") or "", it.get("description") or ""),
             _fmt(it.get("qty")), _fmt(it.get("unit_price")), _fmt(it.get("est_cost"))]
            for i, it in enumerate(items, 1)]
    y = _table(c, w, cm, y, [
        (0.6, "#", "l"), (10.6, "ITEM / DESCRIPTION", "l"),
        (1.6, "QTY", "r"), (2.4, "UNIT PRICE", "r"), (2.6, "TOTAL", "r")],
        rows, h, pn, title="Order lines", wrap_col=1)

    y = _totals_block(c, w, cm, y, pr, cur)

    c.setFont("Helvetica", 8.5)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(1.5 * cm, y, "Authorised by Purchasing — TC Garments. This order references the approved "
                 "purchase request above.")
    c.setFillColorRGB(0, 0, 0)

    _footer(c, w, cm, pn["page"], None)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
