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
    try:
        from reportlab.lib.utils import ImageReader
        for name in ("logo.png", "logo-full.png", "icon-192.png"):
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


def _clip(c, s, font, size, max_w):
    """Truncate a string with an ellipsis so it fits max_w points."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    s = str(s or "")
    if stringWidth(s, font, size) <= max_w:
        return s
    while s and stringWidth(s + "…", font, size) > max_w:
        s = s[:-1]
    return s + "…"


# --------------------------------------------------------------------------
# shared chrome
# --------------------------------------------------------------------------
def _draw_header(c, w, h, cm, title, doc_no, sub):
    c.setFillColorRGB(0.93, 0.11, 0.14)
    c.rect(0, h - 1.7 * cm, w, 1.7 * cm, fill=1, stroke=0)
    logo = _logo()
    tx = 1.5 * cm
    if logo:
        try:
            c.drawImage(logo, 1.4 * cm, h - 1.48 * cm, width=1.25 * cm, height=1.25 * cm,
                        mask="auto", preserveAspectRatio=True)
            tx = 3.0 * cm
        except Exception:
            pass
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(tx, h - 0.85 * cm, "T&C GARMENTS")
    c.setFont("Helvetica", 8.5)
    c.drawString(tx, h - 1.32 * cm, "Textile & Clothing")
    c.setFont("Helvetica-Bold", 14)
    c.drawRightString(w - 1.5 * cm, h - 0.85 * cm, title)
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 1.5 * cm, h - 1.32 * cm, f"{doc_no}   {sub}")
    c.setFillColorRGB(0, 0, 0)


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


def _table(c, w, cm, y, cols, rows, h, page_notes, title=None):
    """Generic bordered table. cols = [(width_cm, header, align)]. Returns new y.
    Paginates automatically, repeating the header."""
    x0 = 1.5 * cm
    total_w = w - 3 * cm
    widths = [cw * cm for cw, _, _ in cols]
    scale = total_w / sum(widths)
    widths = [wd * scale for wd in widths]
    rh = 0.62 * cm

    def header(yy):
        c.setFillColorRGB(0.11, 0.12, 0.16)
        c.rect(x0, yy - rh, total_w, rh, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 8)
        xx = x0
        for (cw, label, align), wd in zip(cols, widths):
            if align == "r":
                c.drawRightString(xx + wd - 0.15 * cm, yy - rh + 0.2 * cm, label)
            else:
                c.drawString(xx + 0.15 * cm, yy - rh + 0.2 * cm, label)
            xx += wd
        c.setFillColorRGB(0, 0, 0)
        return yy - rh

    if title:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(x0, y, title)
        y -= 0.5 * cm
    y = header(y)
    c.setFont("Helvetica", 8.2)
    for i, row in enumerate(rows):
        if y - rh < 3 * cm:                       # new page
            _footer(c, w, cm, page_notes["page"], page_notes.get("notes"))
            c.showPage()
            page_notes["page"] += 1
            y = h - 2 * cm
            y = header(y)
            c.setFont("Helvetica", 8.2)
        if i % 2 == 1:                            # zebra
            c.setFillColorRGB(0.97, 0.97, 0.98)
            c.rect(x0, y - rh, total_w, rh, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)
        xx = x0
        for (cw, _, align), wd, cell in zip(cols, widths, row):
            txt = _clip(c, cell, "Helvetica", 8.2, wd - 0.3 * cm)
            if align == "r":
                c.drawRightString(xx + wd - 0.15 * cm, y - rh + 0.2 * cm, txt)
            else:
                c.drawString(xx + 0.15 * cm, y - rh + 0.2 * cm, txt)
            xx += wd
        y -= rh
    # outer border + column separators
    c.setStrokeColorRGB(0.8, 0.8, 0.82)
    c.setLineWidth(0.5)
    top = y + rh * len(rows) + rh
    xx = x0
    for wd in widths[:-1]:
        xx += wd
        c.line(xx, y, xx, top - rh)
    c.rect(x0, y, total_w, rh * len(rows), stroke=1, fill=0)
    return y - 0.3 * cm


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

    _draw_header(c, w, h, cm, "PURCHASE REQUEST", pr.get("pr_no") or "PR",
                 (pr.get("status") or "").upper())
    y = h - 2.3 * cm

    y = _meta_grid(c, w, cm, y, [
        ("Title", pr.get("title")), ("Request for", pr.get("request_for")),
        ("Requester", pr.get("requester_name") or pr.get("requester")),
        ("Department", pr.get("department")),
        ("Request date", pr.get("request_date")), ("Vendor", pr.get("vendor")),
        ("Payment", pr.get("payment_condition")), ("Delivery", pr.get("delivery_condition")),
    ])

    cur = pr.get("currency") or ""
    rows = [[str(i), it.get("item") or "", it.get("description") or "",
             it.get("unit") or "", _fmt(it.get("qty")), _fmt(it.get("current_stock")),
             _fmt(it.get("unit_price")), _fmt(it.get("est_cost"))]
            for i, it in enumerate(items, 1)]
    y = _table(c, w, cm, y, [
        (0.7, "#", "l"), (2.6, "ITEM", "l"), (5.6, "DESCRIPTION", "l"),
        (1.3, "UNIT", "l"), (1.3, "QTY", "r"), (1.4, "STOCK", "r"),
        (2.1, "UNIT PRICE", "r"), (2.2, "EST. COST", "r")], rows, h, pn, title="Line items")

    # totals box (right-aligned)
    bx_w, bx_h = 6.2 * cm, 0.85 * cm
    bx = w - 1.5 * cm - bx_w
    c.setFillColorRGB(0.97, 0.94, 0.94)
    c.setStrokeColorRGB(0.93, 0.11, 0.14)
    c.setLineWidth(0.8)
    c.roundRect(bx, y - bx_h, bx_w, bx_h, 4, stroke=1, fill=1)
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(bx + 0.3 * cm, y - 0.55 * cm, "TOTAL")
    c.setFont("Helvetica-Bold", 12)
    c.setFillColorRGB(0.8, 0.06, 0.14)
    c.drawRightString(bx + bx_w - 0.3 * cm, y - 0.58 * cm, f"{_fmt(pr.get('total'))} {cur}")
    c.setFillColorRGB(0, 0, 0)
    y -= bx_h + 0.7 * cm

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

    _draw_header(c, w, h, cm, "PURCHASE ORDER", pr.get("po_no") or "PO",
                 f"Ref {pr.get('pr_no') or ''}")
    y = h - 2.3 * cm

    y = _meta_grid(c, w, cm, y, [
        ("Vendor", pr.get("vendor")), ("Department", pr.get("department")),
        ("Payment terms", pr.get("payment_condition")),
        ("Delivery", pr.get("delivery_condition")),
        ("Approved on", (pr.get("approved_at") or "")[:10]),
        ("Currency", pr.get("currency")),
    ])

    cur = pr.get("currency") or ""
    rows = [[str(i), (it.get("item") or "") + " — " + (it.get("description") or ""),
             _fmt(it.get("qty")), _fmt(it.get("unit_price")), _fmt(it.get("est_cost"))]
            for i, it in enumerate(items, 1)]
    y = _table(c, w, cm, y, [
        (0.7, "#", "l"), (10.5, "ITEM / DESCRIPTION", "l"),
        (1.6, "QTY", "r"), (2.4, "UNIT PRICE", "r"), (2.6, "TOTAL", "r")],
        rows, h, pn, title="Order lines")

    bx_w, bx_h = 6.2 * cm, 0.9 * cm
    bx = w - 1.5 * cm - bx_w
    c.setFillColorRGB(0.11, 0.12, 0.16)
    c.roundRect(bx, y - bx_h, bx_w, bx_h, 4, stroke=0, fill=1)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(bx + 0.3 * cm, y - 0.58 * cm, "GRAND TOTAL")
    c.drawRightString(bx + bx_w - 0.3 * cm, y - 0.58 * cm, f"{_fmt(pr.get('total'))} {cur}")
    c.setFillColorRGB(0, 0, 0)
    y -= bx_h + 1.0 * cm

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
