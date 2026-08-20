"""
TC Platform — Procurement PDF generation (Purchase Request + Purchase Order).

Renders the digital PR/PO into a clean, gridded document with the T&C brand mark,
a bordered item table, boxed metadata, an approval-signature grid (embedding each
approver's stamped digital signature), a totals box, and the standard footer
notes. Pure reportlab (already vendored for BI); no external services.
"""
import base64
import io
import json
import os

from app.approvals import constants as C

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


def _fmt(n, dec=2):
    """Money keeps its two decimals. `dec=0` is for counts (quantities, stock):
    120,000 pieces is 120,000, and the '.00' only eats column width."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return str(n or "")
    return f"{v:,.0f}" if (dec == 0 and v == int(v)) else f"{v:,.2f}"


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
def _draw_header(c, w, h, cm, title, doc_no, sub, form_code=None):
    """Clean white letterhead: full brand lockup on the left, document title on the
    right, and a red rule beneath. Returns the y to start body content at.

    `form_code` is the DOAM Annex controlled-form code (T&C-PUF-nn). Printed small
    under the brand so a filed PDF can be traced to the register entry that sets
    its retention period, which is the whole point of a controlled form."""
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
    if form_code:
        c.setFont("Helvetica", 7.5)
        c.setFillColorRGB(0.55, 0.55, 0.55)
        c.drawString(1.5 * cm, top - logo_h - 0.42 * cm, form_code)
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


def _meta_grid(c, w, cm, y, pairs, cols=2, upper=True):
    """Label/value block inside a light box, `cols` fields across. Returns the new y.
    `upper=False` prints the labels as given — the PR reproduces a customer form
    whose wording (and casing) has to match; PO/GRN/DN keep the house uppercase."""
    rows = (len(pairs) + cols - 1) // cols
    box_h = rows * 0.62 * cm + 0.5 * cm
    c.setStrokeColorRGB(0.88, 0.88, 0.9)
    c.setLineWidth(0.6)
    c.roundRect(1.5 * cm, y - box_h, w - 3 * cm, box_h, 4, stroke=1, fill=0)
    colw = (w - 3 * cm) / cols
    yy = y - 0.55 * cm
    for i, (label, val) in enumerate(pairs):
        col = i % cols
        x = 1.7 * cm + col * colw
        c.setFont("Helvetica", 7.5)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawString(x, yy, label.upper() if upper else label)
        c.setFont("Helvetica-Bold", 9.5)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawString(x, yy - 0.33 * cm, _clip(c, val or "—", "Helvetica-Bold", 9.5, colw - 0.6 * cm))
        if col == cols - 1:
            yy -= 0.62 * cm
    c.setFillColorRGB(0, 0, 0)
    return y - box_h - 0.4 * cm


# A table cell that prints an empty ruled box instead of a value. No existing
# document passes it, so every other PDF renders exactly as it did before.
FILL_IN = object()


def _table(c, w, cm, y, cols, rows, h, page_notes, title=None, wrap_col=None,
           max_lines=4, font_size=8.5, pad=None, row_h=None):
    """Bordered table with variable-height rows. cols = [(width_cm, header, align)].
    wrap_col makes that column a rich cell: pass the cell as (item, description) —
    the item renders bold and the description wraps beneath it (up to max_lines),
    and the row grows to fit. It may also be a list of column indexes when more
    than one column wraps. Headings are wrapped (then shrunk, then clipped) into
    their own column, so a many-column form never bleeds one into the next.
    `font_size` and `pad` shrink the whole table for wide forms like the 21-column
    paper PR. Paginates automatically, repeating the header."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    x0 = 1.5 * cm
    total_w = w - 3 * cm
    widths = [cw * cm for cw, _, _ in cols]
    scale = total_w / sum(widths)
    widths = [wd * scale for wd in widths]
    if pad is None:
        pad = 0.18 * cm
    hdr_size = font_size - 0.5
    line_h = font_size * 1.2                          # 0.36 cm at the default 8.5
    # `row_h` raises the MINIMUM row height (a row still grows to fit its text).
    # Only the RFQ passes it, so that a hand-filled box is big enough to write in.
    base_rh = row_h or 0.6 * cm
    wcols = [] if wrap_col is None else (
        [wrap_col] if isinstance(wrap_col, int) else list(wrap_col))

    def fit_hdr(label, wd):
        """Fit a heading inside its own column so it can never bleed into the next
        one: wrap it, and if a word still will not fit, shrink that heading a
        little before finally clipping it — a truncated heading is worse than a
        slightly smaller one. Returns (font size, lines)."""
        avail = wd - 2 * pad
        size = hdr_size
        while size > 5.0:
            lines = _wrap_lines(c, label, "Helvetica-Bold", size, avail, 3)
            if all(stringWidth(ln, "Helvetica-Bold", size) <= avail for ln in lines):
                return size, lines
            size -= 0.25
        return size, [_clip(c, ln, "Helvetica-Bold", size, avail)
                      for ln in _wrap_lines(c, label, "Helvetica-Bold", size, avail, 3)]

    hdr = [fit_hdr(label, wd) for (_, label, _), wd in zip(cols, widths)]
    hrh = max(0.6 * cm, max(len(x[1]) for x in hdr) * line_h + 0.22 * cm)

    def header(yy):
        c.setFillColorRGB(0.11, 0.12, 0.16)
        c.rect(x0, yy - hrh, total_w, hrh, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        xx = x0
        for (cw, label, align), wd, (hsize, lines) in zip(cols, widths, hdr):
            c.setFont("Helvetica-Bold", hsize)
            for k, ln in enumerate(lines):           # stacked up from the band floor
                by = yy - hrh + 0.19 * cm + (len(lines) - 1 - k) * line_h
                if align == "r":
                    c.drawRightString(xx + wd - pad, by, ln)
                else:
                    c.drawString(xx + pad, by, ln)
            xx += wd
        c.setFillColorRGB(0, 0, 0)
        return yy - hrh

    def cell_lines(cell, wd):
        """Rich wrap cell -> list of (bold?, text)."""
        head, body = (cell if isinstance(cell, (list, tuple)) else (None, cell))
        out = []
        if head:
            out.append((True, _clip(c, head, "Helvetica-Bold", font_size + 0.1, wd - 2 * pad)))
        for ln in _wrap_lines(c, body, "Helvetica", font_size - 0.3, wd - 2 * pad, max_lines):
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
        wrapped = {j: cell_lines(row[j], widths[j]) for j in wcols}
        rh = max([base_rh] + [len(v) * line_h + 0.22 * cm for v in wrapped.values()])
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
            if cell is FILL_IN:
                # An empty ruled box for the reader to WRITE IN — not a blank
                # cell and never a 0.00. Used by the RFQ price columns: nothing
                # is priced on a request for quotation, the supplier fills it.
                c.setFillColorRGB(1, 1, 1)
                c.setStrokeColorRGB(0.62, 0.62, 0.67)
                c.setLineWidth(0.6)
                c.roundRect(xx + pad, y - rh + 0.13 * cm, wd - 2 * pad,
                            rh - 0.26 * cm, 2, stroke=1, fill=1)
                c.setFillColorRGB(0, 0, 0)
            elif idx in wrapped:
                ly = y - line_h
                for is_bold, txt in wrapped[idx]:
                    if is_bold:
                        c.setFont("Helvetica-Bold", font_size + 0.1)
                        c.setFillColorRGB(0.1, 0.1, 0.13)
                    else:
                        c.setFont("Helvetica", font_size - 0.3)
                        c.setFillColorRGB(0.34, 0.34, 0.38)
                    c.drawString(xx + pad, ly, txt)
                    ly -= line_h
                c.setFillColorRGB(0, 0, 0)
            else:
                # Same shrink-before-clip fit_hdr gives headings: a truncated
                # number is a wrong number, so shrink the cell until it fits and
                # only clip once 4.5pt still will not hold it.
                s, txt = font_size, str(cell or "")
                avail = wd - 2 * pad
                while s > 4.5 and stringWidth(txt, "Helvetica", s) > avail:
                    s -= 0.25
                c.setFont("Helvetica", s)
                txt = _clip(c, txt, "Helvetica", s, avail)
                if align == "r":
                    c.drawRightString(xx + wd - pad, cyc, txt)
                else:
                    c.drawString(xx + pad, cyc, txt)
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


def _cost_object_pairs(pr):
    """DOAM §5 — the controlling cost object, printed on every document in the
    chain so cost and margin can be read per order. Only the ones actually set
    are printed; an empty "SO: —" on an MRO order is noise, not a control."""
    out = []
    if pr.get("so_no"):
        out.append(("Sales order", pr.get("so_no")))
    # DOAM §3.4 — the clause's other cost object. A forecast-driven purchase has
    # no sales order, so this IS its golden thread; it prints wherever one does.
    if pr.get("forecast_ref"):
        out.append(("Agreed forecast", pr.get("forecast_ref")))
    if pr.get("cost_center"):
        out.append(("Cost centre", pr.get("cost_center")))
    if pr.get("asset_code"):
        out.append(("Asset", pr.get("asset_code")))
    return out


# The customer's own paper PR form (sheet "Kadysoft (2)"), column for column.
# Widths start from the xlsx column widths and are ratios, not centimetres —
# _table normalises them onto the usable width. Twenty-one columns across one
# landscape page is tighter than the spreadsheet, so width was moved off the
# three columns the system can never fill (Stock days / Pending qty / Last Stock)
# and off an oversized P.O#, onto the money and quantity columns where a clipped
# character would change the number. Width alone is not the guarantee, though:
# _table shrinks a cell before it clips it, so a number prints whole at any
# magnitude and these ratios only keep the common case at full size.
# scratchpad/fit.py walks each column's magnitudes UP until it breaks, then reads
# rendered pages back; keep it printing "0 problems".
_PR_COLS = [
    (8.0, "S", "l"), (22.0, "ITEM", "l"), (40.0, "Description", "l"),
    (9.0, "UNIT", "l"), (17.0, "QTY", "r"), (16.5, "On Hand", "r"),
    (8.0, "Stock days", "r"), (10.5, "Pending qty", "r"),
    (15.0, "LAST ORDER QTY.", "r"), (14.0, "LAST ORDER DATE", "l"),
    (18.8, "VENDOR", "l"), (15.0, "UNIT PRICE", "r"), (19.0, "EST. COST", "r"),
    (8.8, "CUR.", "l"), (16.0, "LAST ORDER PRICE", "r"), (19.0, "P.O#", "l"),
    (8.0, "Last Stock", "r"), (16.6, "PAY. COND.", "l"), (14.0, "DEL. COND.", "l"),
    (14.0, "ETA", "l"), (12.0, "LEAD Time", "l"),
]


def _chosen_lead_time(bundle):
    """LEAD Time for the request: the lead time promised by the quote that was
    actually chosen. There is no per-item source — proc_vendors has no
    lead_time_days column at all, and the mnt_spare_parts one is not joined into
    the PR bundle — so an unquoted request prints this blank for the buyer."""
    for q in bundle.get("quotes") or []:
        if q.get("is_chosen") and q.get("lead_time_days"):
            return "%g days" % float(q["lead_time_days"])
    return ""


def pr_pdf(bundle):
    """The customer's paper PURCHASE REQUEST form: 21 columns, landscape."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr, items, steps = bundle["pr"], bundle["items"], bundle["steps"]
    buf = io.BytesIO()
    page = landscape(A4)
    c = canvas.Canvas(buf, pagesize=page)
    w, h = page
    pn = {"page": 1, "notes": _PR_NOTES}

    y = _draw_header(c, w, h, cm, "PURCHASE REQUEST", pr.get("pr_no") or "PR",
                     "STATUS: " + (pr.get("status") or "").upper(),
                     form_code=C.FORM_CODES["capex"]
                     if (pr.get("expenditure_kind") == "capex")
                     else C.FORM_CODES["pr"])

    cur = pr.get("currency") or ""
    y = _meta_grid(c, w, cm, y, [
        ("Date", pr.get("request_date")),
        ("Order Type (Foreign - Local)", "Local" if (cur or "EGP").upper() == "EGP"
         else "Foreign"),
        ("PR S.N", pr.get("pr_no")),
        ("Name of Requestor", pr.get("requester_name") or pr.get("requester")),
        ("REQUEST FOR", pr.get("request_for")),
        ("Department Name", pr.get("department")),
        ("Title", pr.get("title")),
    ] + _cost_object_pairs(pr), cols=3, upper=False)

    # Stock days / Pending qty / Last Stock stay blank on purpose: nothing in the
    # system knows them, and a printed 0 would be a claim the buyer would believe.
    lead = _chosen_lead_time(bundle)
    rows = [[str(i), it.get("item") or "", it.get("description") or "",
             it.get("unit") or "", _fmt(it.get("qty"), 0),
             _fmt(it.get("current_stock"), 0),
             "", "", _fmt(it.get("last_order_qty"), 0), it.get("last_order_date") or "",
             it.get("vendor") or pr.get("vendor") or "", _fmt(it.get("unit_price")),
             _fmt(it.get("est_cost")), cur, _fmt(it.get("last_order_price")),
             pr.get("po_no") or "", "", pr.get("payment_condition") or "",
             pr.get("delivery_condition") or "", pr.get("req_del_date") or "", lead]
            for i, it in enumerate(items, 1)]
    # VENDOR / PAY. COND. / DEL. COND. wrap: they are the only place those three
    # contract terms appear, and an Incoterm without its named place ("DDP C…")
    # is not a delivery condition.
    y = _table(c, w, cm, y, _PR_COLS, rows, h, pn, title="Line items",
               wrap_col=[1, 2, 10, 17, 18], max_lines=4, font_size=6, pad=0.07 * cm)

    if y - 3.4 * cm < 2.2 * cm:                      # keep the totals box whole
        _footer(c, w, cm, pn["page"], _PR_NOTES)
        c.showPage()
        pn["page"] += 1
        y = h - 2.5 * cm
    y = _totals_block(c, w, cm, y, pr, cur)

    # signatures (the paper form's A20:U25 notes/signature block)
    _signature_grid(c, w, h, cm, y, pr, steps, pn)
    _footer(c, w, cm, pn["page"], _PR_NOTES)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def _role_names(csv):
    """'storekeeper,warehouse_manager' -> 'Storekeeper, Warehouse Manager'."""
    keys = [k.strip() for k in str(csv or "").split(",") if k.strip()]
    try:
        from app.security import role_label
        return ", ".join(role_label(k) for k in sorted(keys))
    except Exception:
        return ", ".join(sorted(keys))


def _esc_lines(steps):
    """One printed line per rung the SoD escalation moved, so the deviation is on
    the paper an auditor reads and not only inside the app."""
    out = []
    for s in steps:
        frm = s.get("esc_from")
        if not frm:
            continue
        to = s.get("esc_role")
        stage = s.get("stage") or ""
        label = str(stage).replace("_", " ").title()
        out.append(
            f"• {label}: escalated from {_role_names(frm)} to {_role_names(to)} — "
            f"the requester holds the normal signing role." if to else
            f"• {label}: the requester is its only eligible signer and no superior "
            f"role is configured — needs a delegation or a Procurement admin.")
    return out


# DOAM Table 5 letters, printed on the signature block.
_ACTION_WORDS = {"review": "REVIEW (R)", "approve": "APPROVE (A)",
                 "endorse": "ENDORSE (E)"}


def _signature_grid(c, w, h, cm, y, pr, steps, pn):
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1.5 * cm, y, "Approval signatures (DOAM Table 5: P prepare / "
                              "R review / A approve / E endorse)")
    y -= 0.35 * cm
    for line in _esc_lines(steps):
        c.setFont("Helvetica-Oblique", 7.4)
        c.setFillColorRGB(0.62, 0.18, 0.08)
        c.drawString(1.5 * cm, y, _clip(c, line, "Helvetica-Oblique", 7.4, w - 3 * cm))
        c.setFillColorRGB(0, 0, 0)
        y -= 0.3 * cm
    blocks = [{"role": "Requester — PREPARE (P)",
               "name": pr.get("requester_name") or pr.get("requester"),
               "sig": None, "date": (pr.get("submitted_at") or pr.get("request_date") or "")[:10],
               "status": "originator"}]
    for s in steps:
        # DOAM Table 5 — the block says WHAT the signature is (Review / Approve /
        # Endorse), not just whose it is. Without it the printed form evidences a
        # signature was collected and nothing about the authority it carried.
        act = str(s.get("action") or s.get("action_type") or "approve").lower()
        act = act if act in _ACTION_WORDS else "approve"
        blocks.append({"role": (s.get("approver_role") or s.get("stage") or "")
                       + " — " + _ACTION_WORDS[act]
                       + (" (ESCALATED)" if s.get("esc_from") else ""),
                       "name": s.get("approver_name"), "sig": s.get("sig_png"),
                       "date": (s.get("acted_at") or "")[:10], "status": s.get("status"),
                       "code": s.get("verify_code")})

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
        # signature-event verification handle (checked at /procurement/verify/<code>)
        if b.get("code"):
            c.setFont("Helvetica", 5.8)
            c.setFillColorRGB(0.5, 0.5, 0.55)
            c.drawString(x + 0.2 * cm, y + 0.08 * cm,
                         _clip(c, "Verify: " + _verify_base() + "/procurement/verify/" + b["code"],
                               "Helvetica", 5.8, bw - 0.4 * cm))
        c.setFillColorRGB(0, 0, 0)
    return y


def _verify_base():
    """Public base URL for the printed verification links (blank -> relative)."""
    try:
        from config import Config
        return (Config.PUBLIC_URL or "").rstrip("/")
    except Exception:
        return ""


# --------------------------------------------------------------------------
# Purchase Order
# --------------------------------------------------------------------------
def po_pdf(bundle, po=None):
    """Render ONE vendor's Purchase Order.

    `po` is a row from bundle["pos"]; omitted, the primary (first) order — which
    is the ONLY order on a single-vendor request and on every request issued
    before purchase orders were split per vendor, so the old one-argument call
    keeps printing exactly what it printed before.

    When the request was split across suppliers, the lines and the totals are
    filtered to `po`'s vendor: this document is sent to that supplier and must
    never show them a competitor's lines or prices. A request with a single
    order is never filtered — a legacy line whose vendor differs from the header
    must not silently vanish off its own PO.

    FILTERING is a split concern; IDENTITY is not. The vendor and number always
    come from the order row, because the order row is what the register, the
    screen and the supplier's copy all agree on. Gating that on a split printed
    the stale HEADER vendor whenever one requisition resolved to a single
    supplier the header did not name — and "—", no supplier at all, when the
    header was left blank, which is the natural way to use the per-line field."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr, items = bundle["pr"], bundle["items"]
    pos = bundle.get("pos") or []
    po = po or (pos[0] if pos else None)
    if po:
        if len(pos) > 1:
            # C.vendor_key on BOTH sides — the same identity po_groups() grouped
            # on. A raw comparison dropped every line whose vendor differed only
            # by surrounding whitespace or case: the line was grouped onto this
            # order, priced into its subtotal, and then printed on no document at
            # all. Only the two web forms trim the field; the maintenance
            # auto-reorder bridge and the ERP item-master import do not.
            head_vendor = pr.get("vendor")
            want = C.vendor_key(po.get("vendor"))
            items = [it for it in items
                     if C.vendor_key(it.get("vendor") or head_vendor) == want]
            pr = dict(pr, total=po.get("subtotal"))
        # The legacy synthesised row from _pos_for() carries the request's own
        # vendor and po_no, so this is a no-op for every pre-split request.
        pr = dict(pr, vendor=po.get("vendor") or pr.get("vendor"),
                  po_no=po.get("po_no") or pr.get("po_no"),
                  po_rev=po.get("rev") or pr.get("po_rev"))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pn = {"page": 1, "notes": None}

    # Revision-aware document number: "PO-... · Rev N" once the order has been
    # revised, so a reprinted PDF is never mistaken for the original issue.
    po_no = pr.get("po_no") or "PO"
    try:
        po_rev = int(pr.get("po_rev") or 0)
    except (TypeError, ValueError):
        po_rev = 0
    y = _draw_header(c, w, h, cm, "PURCHASE ORDER",
                     f"{po_no} · Rev {po_rev}" if po_rev > 0 else po_no,
                     "Ref " + (pr.get("pr_no") or ""), form_code=C.FORM_CODES["po"])

    try:
        fx = float(pr.get("fx_rate") or 0)
    except (TypeError, ValueError):
        fx = 0.0
    meta_pairs = [
        ("Vendor", pr.get("vendor")), ("Department", pr.get("department")),
        ("Payment terms", pr.get("payment_condition")),
        ("Delivery", pr.get("delivery_condition")),
        ("Approved on", (pr.get("approved_at") or "")[:10]),
        ("Currency", pr.get("currency")),
    ]
    if fx not in (0.0, 1.0):
        meta_pairs.append(("FX rate", f"1 {pr.get('currency') or ''} = {fx:g} EGP"))
    meta_pairs += _cost_object_pairs(pr)
    y = _meta_grid(c, w, cm, y, meta_pairs)

    cur = pr.get("currency") or ""
    rows = [[str(i), (it.get("item") or "", it.get("description") or ""),
             _fmt(it.get("qty")), _fmt(it.get("unit_price")), _fmt(it.get("est_cost"))]
            for i, it in enumerate(items, 1)]
    y = _table(c, w, cm, y, [
        (0.6, "#", "l"), (10.6, "ITEM / DESCRIPTION", "l"),
        (1.6, "QTY", "r"), (2.4, "UNIT PRICE", "r"), (2.6, "TOTAL", "r")],
        rows, h, pn, title="Order lines", wrap_col=1)

    y = _totals_block(c, w, cm, y, pr, cur)

    # EGP-equivalent line for foreign-currency orders (thresholds routed on it).
    # Same rule as services.egp_total, computed inline: total * fx when the
    # order currency is not EGP.
    if fx not in (0.0, 1.0):
        try:
            _tot = float(pr.get("total") or 0)
        except (TypeError, ValueError):
            _tot = 0.0
        egp_eq = _tot * fx if (str(pr.get("currency") or "EGP").upper() != "EGP") else _tot
        c.setFont("Helvetica-Oblique", 8.5)
        c.setFillColorRGB(0.3, 0.3, 0.3)
        c.drawString(1.5 * cm, y,
                     f"EGP equivalent: {_fmt(egp_eq)} EGP (@ {fx:g} EGP / {cur or 'unit'})")
        c.setFillColorRGB(0, 0, 0)
        y -= 0.45 * cm

    # Approval deviations carried over from the request: a rung whose normal
    # signer was the originator was signed one level up. An auditor holding only
    # the PO must be able to see that without opening the app.
    esc = _esc_lines(bundle.get("steps") or [])
    if esc:
        c.setFont("Helvetica-Bold", 8.2)
        c.setFillColorRGB(0.62, 0.18, 0.08)
        c.drawString(1.5 * cm, y, "Approval deviations (segregation of duties):")
        y -= 0.34 * cm
        for line in esc:
            c.setFont("Helvetica-Oblique", 7.4)
            c.drawString(1.5 * cm, y, _clip(c, line, "Helvetica-Oblique", 7.4, w - 3 * cm))
            y -= 0.3 * cm
        c.setFillColorRGB(0, 0, 0)
        y -= 0.15 * cm

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


# --------------------------------------------------------------------------
# Request for Quotation — the PO's layout, with the money taken out
# --------------------------------------------------------------------------
_RFQ_NOTES = [
    "• This is a REQUEST FOR QUOTATION, not a purchase order: it commits neither "
    "party and no order is placed by it.",
    "• Please write your unit price and line total in the boxes provided, sign and "
    "stamp below, and return this form by the reply-by date.",
    "• Quoted prices are understood to include the stated delivery terms; state any "
    "exclusion, validity period or lead time in writing.",
]


def rfq_pdf(bundle, rfq):
    """Render ONE vendor's Request for Quotation.

    Deliberately the PURCHASE ORDER's layout — same letterhead, meta grid,
    bordered line table and footer — so a filed set reads as one family of
    document. What is missing is the point of it: the UNIT PRICE and TOTAL
    columns are printed as empty ruled boxes for the supplier to fill in, and
    there is no totals block, no amount in words and no approval signature
    grid. Nothing is being approved or committed by this sheet.

    `rfq` is a pr_rfqs row. The lines are that supplier's own; a supplier who
    owns no line on the request is being asked to quote the whole of it, which
    is what asking a new supplier for a price means."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr, items = bundle["pr"], bundle["items"]
    rfq = dict(rfq or {})
    vendor = (rfq.get("vendor") or "").strip()
    head_vendor = pr.get("vendor")
    mine = [it for it in items
            if ((it.get("vendor") or head_vendor or "").strip() == vendor)]
    items = mine or items

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pn = {"page": 1, "notes": _RFQ_NOTES}

    y = _draw_header(c, w, h, cm, "REQUEST FOR QUOTATION", rfq.get("rfq_no") or "RFQ",
                     "Ref " + (pr.get("pr_no") or ""), form_code=C.FORM_CODES["rfq"])

    pairs = [
        ("To (supplier)", vendor or "—"),
        ("Reply by", rfq.get("reply_by") or "—"),
        ("Issued on", (rfq.get("sent_at") or "")[:10]),
        ("Issued by", rfq.get("created_by")),
        ("Delivery", pr.get("delivery_condition")),
        ("Payment terms", pr.get("payment_condition")),
        ("Required delivery date", pr.get("req_del_date")),
        ("Quote in currency", pr.get("currency")),
    ]
    if rfq.get("notes"):
        pairs.append(("Notes", rfq.get("notes")))
    y = _meta_grid(c, w, cm, y, pairs)

    # The quantities are real; the two money columns are empty boxes.
    rows = [[str(i), (it.get("item") or "", it.get("description") or ""),
             it.get("unit") or "", _fmt(it.get("qty"), 0), FILL_IN, FILL_IN]
            for i, it in enumerate(items, 1)]
    y = _table(c, w, cm, y, [
        (0.6, "#", "l"), (9.0, "ITEM / DESCRIPTION", "l"), (1.4, "UNIT", "l"),
        (1.6, "QTY", "r"), (2.9, "UNIT PRICE", "r"), (3.0, "TOTAL", "r")],
        rows, h, pn, title="Items to be quoted", wrap_col=1, row_h=1.0 * cm)

    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.drawString(1.5 * cm, y, "Prices to be entered by the supplier in %s. No value is "
                 "stated by TC Garments on this form." % (pr.get("currency") or "the order currency"))
    c.setFillColorRGB(0, 0, 0)
    y -= 0.75 * cm

    if y - 3.2 * cm < 2.2 * cm:                     # keep the sign-off whole
        _footer(c, w, cm, pn["page"], _RFQ_NOTES)
        c.showPage()
        pn["page"] += 1
        y = h - 2.5 * cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1.5 * cm, y, "Supplier's quotation — signature and stamp")
    y -= 0.35 * cm
    _grn_sign_strip(c, w, cm, y, [
        ("Quoted by (supplier)", None, None),
        ("Signature & company stamp", None, None),
        ("Quotation valid until", None, None),
    ])

    _footer(c, w, cm, pn["page"], _RFQ_NOTES)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# --------------------------------------------------------------------------
# Goods Received Note
# --------------------------------------------------------------------------
_GRN_NOTES = [
    "• Goods received as listed above; discrepancies must be reported within 48 hours.",
    "• Rejected quantities are NOT received, are NOT taken into stock, and are returned "
    "to the supplier under a debit note.",
]


def grn_doc_no(pr_no):
    """LEGACY fallback only — used for receipts booked before GRNs were persisted.
    PR-2026-000012 -> GRN-2026-000012 (fallback: plain GRN prefix). A live receipt
    now carries its own pre-allocated pr_grn.grn_no; deriving it here is exactly
    what made every partial delivery reprint the same document number."""
    s = str(pr_no or "").strip()
    if s.upper().startswith("PR-"):
        return "GRN-" + s[3:]
    return ("GRN-" + s) if s else "GRN"


def _grn_sign_strip(c, w, cm, y, boxes):
    """Three manual sign-off boxes (no signature images): dark role strip on top,
    then a Name line and a Date line — pre-filled only where we have the data."""
    per_row, gap = 3, 0.3 * cm
    bw = (w - 3 * cm - (per_row - 1) * gap) / per_row
    bh = 2.2 * cm
    x0 = 1.5 * cm
    y -= bh
    for idx, (role, name, date) in enumerate(boxes[:per_row]):
        x = x0 + idx * (bw + gap)
        # role header strip
        c.setFillColorRGB(0.11, 0.12, 0.16)
        c.roundRect(x, y + bh - 0.55 * cm, bw, 0.55 * cm, 3, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 0.2 * cm, y + bh - 0.38 * cm,
                     _clip(c, role, "Helvetica-Bold", 8, bw - 0.4 * cm))
        # body box
        c.setStrokeColorRGB(0.82, 0.82, 0.85)
        c.setLineWidth(0.6)
        c.rect(x, y, bw, bh - 0.55 * cm, stroke=1, fill=0)
        # name + date rule lines
        for label, value, ly in (("Name:", name, y + 0.95 * cm),
                                 ("Date:", date, y + 0.35 * cm)):
            c.setFont("Helvetica", 7.2)
            c.setFillColorRGB(0.5, 0.5, 0.5)
            c.drawString(x + 0.2 * cm, ly, label)
            c.setStrokeColorRGB(0.88, 0.88, 0.9)
            c.setLineWidth(0.5)
            c.line(x + 1.05 * cm, ly - 0.06 * cm, x + bw - 0.2 * cm, ly - 0.06 * cm)
            if value:
                c.setFont("Helvetica-Bold", 8.2)
                c.setFillColorRGB(0.1, 0.1, 0.1)
                c.drawString(x + 1.1 * cm, ly,
                             _clip(c, value, "Helvetica-Bold", 8.2, bw - 1.35 * cm))
        c.setFillColorRGB(0, 0, 0)
        c.setStrokeColorRGB(0, 0, 0)
    return y - 0.5 * cm


def grn_pdf(bundle, grn=None):
    """Goods Received Note for ONE receipt event: what arrived on this delivery,
    what was accepted, what went to quarantine, and the outstanding balance.
    `grn` is a pr_grn row; without one this falls back to the whole-PR summary
    printed by pre-GRN-table receipts."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr, items = bundle["pr"], bundle["items"]
    by_line = {}
    if grn:
        try:
            by_line = {int(l["item_id"]): l for l in json.loads(grn.get("lines_json") or "[]")}
        except Exception:
            by_line = {}
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pn = {"page": 1, "notes": _GRN_NOTES}

    y = _draw_header(c, w, h, cm, "GOODS RECEIVED NOTE",
                     (grn or {}).get("grn_no") or grn_doc_no(pr.get("pr_no")),
                     "Ref " + (pr.get("po_no") or pr.get("pr_no") or ""),
                     form_code=C.FORM_CODES["grn"])

    # received_at is only stamped on the PR once FULLY received; for a partial
    # receipt fall back to the latest goods-receipt entry in the audit trail.
    received_at = ((grn or {}).get("created_at") or pr.get("received_at") or "")[:10]
    if not received_at:
        for ev in bundle.get("events") or []:      # newest first
            if ev.get("action") in ("goods_received", "received"):
                received_at = (ev.get("created_at") or "")[:10]
                break

    # WHICH suppliers this delivery came from. A requisition may buy from
    # several, and this note is the receiving evidence in the three-way match:
    # naming one supplier at the top attributed the other two's deliveries to it.
    # One supplier -> its own name and its own order number (which is not
    # necessarily the header's). Several -> say so, and name the supplier on
    # every line instead.
    vendors = []
    for it in items:
        if grn and not by_line.get(it.get("id")):
            continue
        v = (it.get("vendor") or pr.get("vendor") or "").strip() or None
        if v not in vendors:
            vendors.append(v)
    multi = len(vendors) > 1
    one = vendors[0] if len(vendors) == 1 else None
    if multi:
        vendor_cell = po_cell = "Several suppliers — see line vendor"
    else:
        vendor_cell = one or pr.get("vendor")
        po_cell = next((p.get("po_no") for p in (bundle.get("pos") or [])
                        if p.get("vendor") == vendor_cell and p.get("po_no")),
                       pr.get("po_no") or pr.get("pr_no"))

    pairs = [
        ("Vendor", vendor_cell), ("Department", pr.get("department")),
        ("PO No", po_cell),
        ("Received by", (grn or {}).get("received_by") or pr.get("received_by")),
        ("Received at", received_at),
        ("Delivery condition", pr.get("delivery_condition")),
    ]
    pairs += _cost_object_pairs(pr)
    if grn:
        pairs.append(("Receipt no", "%s of this PO" % (grn.get("seq") or 1)))
        if float(grn.get("quarantined_qty") or 0) > 0:
            pairs.append(("Quarantined", "%s (over-delivery, NOT stocked)"
                          % _fmt(float(grn.get("quarantined_qty") or 0))))
    notes = (grn or {}).get("notes") or pr.get("receipt_notes")
    if notes:
        pairs.append(("Receipt notes", notes))
    y = _meta_grid(c, w, cm, y, pairs)

    rows = []
    for i, it in enumerate(items, 1):
        ordered = float(it.get("qty") or 0)
        received = float(it.get("received_qty") or 0)
        line = by_line.get(it.get("id"))
        if grn and not line:
            continue                       # this line did not move on this delivery
        # "this note" = the accepted quantity of THIS delivery; the cumulative
        # figure stays visible as the outstanding balance.
        this_note = float(line["accepted"]) if line else received
        quar = float(line["quarantined"]) if line else 0.0
        rej = float(line.get("rejected") or 0) if line else 0.0
        row = [str(len(rows) + 1), (it.get("item") or "", it.get("description") or ""),
               it.get("unit") or "", _fmt(ordered), _fmt(this_note), _fmt(rej),
               _fmt(quar), _fmt(max(0.0, ordered - received))]
        if multi:
            row.insert(2, (it.get("vendor") or pr.get("vendor") or "—"))
        rows.append(row)
    # Same total width either way; only a multi-supplier delivery pays for the
    # extra column, so a single-supplier GRN prints exactly as it always has.
    cols = [(0.6, "#", "l"), (6.2, "ITEM / DESCRIPTION", "l"), (1.2, "UNIT", "l"),
            (1.7, "ORDERED", "r"), (1.8, "ACCEPTED", "r"), (1.7, "REJECTED", "r"),
            (2.0, "QUARANTINED", "r"), (2.1, "OUTSTANDING", "r")]
    if multi:
        cols[1] = (4.4, "ITEM / DESCRIPTION", "l")
        cols.insert(2, (1.8, "VENDOR", "l"))
    y = _table(c, w, cm, y, cols, rows, h, pn, title="Received lines", wrap_col=1)

    # three-box sign-off strip (page-break guard first)
    if y - 3.2 * cm < 2.2 * cm:
        _footer(c, w, cm, pn["page"], _GRN_NOTES)
        c.showPage()
        pn["page"] += 1
        y = h - 2.5 * cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1.5 * cm, y, "Goods receipt sign-off")
    y -= 0.35 * cm
    _grn_sign_strip(c, w, cm, y, [
        ("Received by (Warehouse)",
         (grn or {}).get("received_by") or pr.get("received_by"), received_at),
        ("Checked by (Purchasing)", None, None),
        ("Approved by (Manager)", None, None),
    ])

    _footer(c, w, cm, pn["page"], _GRN_NOTES)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


# --------------------------------------------------------------------------
# Debit Note (return to vendor)
# --------------------------------------------------------------------------
_DN_NOTES = [
    "• This debit note covers goods rejected on receipt and returned to the supplier.",
    "• The quantities below were NOT received and were NOT taken into stock; the value "
    "shown is deducted from what is payable against the referenced Purchase Order.",
    "• Please issue a credit note, or replace the goods, within the agreed payment terms.",
]


def debit_note_pdf(bundle, ret):
    """Debit note for ONE return-to-vendor event — same letterhead, meta grid,
    bordered table and footer as the PR/PO/GRN, so a filed set reads as one family.

    `ret` is a pr_returns row: it carries its own document number (dn_no), the
    rejected lines, the reason and the value being debited."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr = bundle["pr"]
    ret = dict(ret or {})
    try:
        lines = json.loads(ret.get("lines_json") or "[]")
    except Exception:
        lines = []
    cur = ret.get("currency") or pr.get("currency") or "EGP"
    # The RETURN's own supplier and that supplier's OWN order. A requisition can
    # buy from several suppliers, and this document takes money off one of them:
    # printing the header vendor debited whoever happened to be on the header for
    # goods a different supplier shipped.
    vendor = ret.get("vendor") or pr.get("vendor")
    po_ref = next((p.get("po_no") for p in (bundle.get("pos") or [])
                   if p.get("vendor") == vendor and p.get("po_no")),
                  pr.get("po_no") or pr.get("pr_no"))

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pn = {"page": 1, "notes": _DN_NOTES}

    y = _draw_header(c, w, h, cm, "DEBIT NOTE", ret.get("dn_no") or "DN",
                     "Ref " + (po_ref or ""), form_code=C.FORM_CODES["dn"])

    pairs = [
        ("Supplier", vendor),
        ("PO No", po_ref),
        ("Department", pr.get("department")),
        ("Raised by", ret.get("created_by")),
        ("Date", (ret.get("created_at") or "")[:10]),
        ("Status", "Settled by supplier" if ret.get("status") == "settled"
         else "Open — awaiting supplier credit"),
    ]
    pairs += _cost_object_pairs(pr)
    if ret.get("reason"):
        pairs.append(("Reason for rejection", ret.get("reason")))
    if ret.get("settled_at"):
        pairs.append(("Settled", "%s · %s" % ((ret.get("settled_at") or "")[:10],
                                              ret.get("settled_by") or "")))
    y = _meta_grid(c, w, cm, y, pairs)

    rows = []
    for i, l in enumerate(lines, 1):
        qty = float(l.get("qty") or 0)
        up = float(l.get("unit_price") or 0)
        rows.append([str(i), (l.get("item") or "", ""), l.get("unit") or "",
                     _fmt(qty), _fmt(up), _fmt(round(qty * up, 2))])
    y = _table(c, w, cm, y, [
        (0.6, "#", "l"), (8.4, "ITEM REJECTED", "l"), (1.5, "UNIT", "l"),
        (2.0, "QTY", "r"), (2.6, "UNIT PRICE", "r"), (2.9, "VALUE", "r")],
        rows, h, pn, title="Returned to supplier", wrap_col=1)

    # totals — built from the return's own figures, NOT the PR's
    net = float(ret.get("net") or 0)
    tax = float(ret.get("tax") or 0)
    total = float(ret.get("total") or round(net + tax, 2))
    if y - 4.2 * cm < 2.2 * cm:
        _footer(c, w, cm, pn["page"], _DN_NOTES)
        c.showPage()
        pn["page"] += 1
        y = h - 2.5 * cm
    bx_w = 7.0 * cm
    bx = w - 1.5 * cm - bx_w
    tl = [("Net value returned", net)] + ([("VAT", tax)] if tax else [])
    bx_h = 0.5 * cm * len(tl) + 0.75 * cm
    c.setStrokeColorRGB(0.85, 0.85, 0.87)
    c.setLineWidth(0.6)
    c.roundRect(bx, y - bx_h, bx_w, bx_h, 4, stroke=1, fill=0)
    yy = y - 0.45 * cm
    c.setFont("Helvetica", 9)
    for label, val in tl:
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawString(bx + 0.3 * cm, yy, label)
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.drawRightString(bx + bx_w - 0.3 * cm, yy, f"{_fmt(val)} {cur}")
        yy -= 0.5 * cm
    c.setFillColorRGB(0.93, 0.11, 0.14)
    c.roundRect(bx, y - bx_h, bx_w, 0.7 * cm, 4, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(bx + 0.3 * cm, y - bx_h + 0.24 * cm, "TOTAL DEBITED")
    c.drawRightString(bx + bx_w - 0.3 * cm, y - bx_h + 0.24 * cm, f"{_fmt(total)} {cur}")
    c.setFillColorRGB(0, 0, 0)
    y -= bx_h + 0.5 * cm
    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    c.drawString(1.5 * cm, y, "Amount in words: " + _amount_words(total, cur))
    c.setFillColorRGB(0, 0, 0)
    y -= 0.9 * cm

    if y - 3.2 * cm < 2.2 * cm:
        _footer(c, w, cm, pn["page"], _DN_NOTES)
        c.showPage()
        pn["page"] += 1
        y = h - 2.5 * cm
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1.5 * cm, y, "Return sign-off")
    y -= 0.35 * cm
    _grn_sign_strip(c, w, cm, y, [
        ("Rejected by (Warehouse)", ret.get("created_by"),
         (ret.get("created_at") or "")[:10]),
        ("Raised by (Purchasing)", None, None),
        ("Acknowledged by (Supplier)", None, None),
    ])

    _footer(c, w, cm, pn["page"], _DN_NOTES)
    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
