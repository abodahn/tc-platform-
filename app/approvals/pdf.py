"""
TC Platform — Procurement PDF generation (Purchase Request + Purchase Order).

Renders the digital PR into a document resembling the paper form, embedding each
approver's stamped digital signature. Pure reportlab (already vendored for BI);
no external services. Returns raw PDF bytes.
"""
import base64
import io


def _img_reader(data_url):
    """Turn a data:image/png;base64,... string into a reportlab ImageReader,
    or None if it isn't a usable PNG."""
    if not data_url or "," not in data_url:
        return None
    try:
        from reportlab.lib.utils import ImageReader
        raw = base64.b64decode(data_url.split(",", 1)[1])
        return ImageReader(io.BytesIO(raw))
    except Exception:
        return None


def _fmt(n):
    try:
        return f"{float(n):,.2f}"
    except (TypeError, ValueError):
        return str(n or "")


def _header(c, w, h, title, pr, cm):
    c.setFillColorRGB(0.93, 0.11, 0.14)  # T&C red
    c.rect(0, h - 1.4 * cm, w, 1.4 * cm, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(1.5 * cm, h - 0.95 * cm, "T&C GARMENTS")
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(w - 1.5 * cm, h - 0.95 * cm, title)
    c.setFillColorRGB(0, 0, 0)


def pr_pdf(bundle):
    """bundle = services.get_pr(...) result. Returns PDF bytes for the PR form."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr = bundle["pr"]
    items = bundle["items"]
    steps = bundle["steps"]

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    _header(c, w, h, "PURCHASE REQUEST", pr, cm)

    y = h - 2.1 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(1.5 * cm, y, pr.get("pr_no") or "PR")
    c.setFont("Helvetica", 9)
    c.setFillColorRGB(0.3, 0.3, 0.3)
    status = (pr.get("status") or "").upper()
    c.drawRightString(w - 1.5 * cm, y, f"STATUS: {status}")
    c.setFillColorRGB(0, 0, 0)
    y -= 0.75 * cm

    # meta grid
    c.setFont("Helvetica", 9)
    meta = [
        ("Title", pr.get("title")), ("Request for", pr.get("request_for")),
        ("Requester", pr.get("requester_name") or pr.get("requester")),
        ("Department", pr.get("department")), ("Date", pr.get("request_date")),
        ("Vendor", pr.get("vendor")), ("Payment", pr.get("payment_condition")),
        ("Delivery", pr.get("delivery_condition")),
    ]
    col = 0
    for label, val in meta:
        x = 1.5 * cm + (col % 2) * (w / 2 - 0.5 * cm)
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawString(x, y, f"{label}:")
        c.setFillColorRGB(0, 0, 0)
        c.drawString(x + 2.3 * cm, y, str(val or "—")[:48])
        col += 1
        if col % 2 == 0:
            y -= 0.55 * cm
    if col % 2 != 0:
        y -= 0.55 * cm
    y -= 0.3 * cm

    # items table
    c.setFillColorRGB(0.96, 0.96, 0.98)
    c.rect(1.5 * cm, y - 0.15 * cm, w - 3 * cm, 0.6 * cm, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 8.5)
    cols = [(1.6, "#"), (2.1, "ITEM"), (6.6, "DESCRIPTION"), (12.6, "UNIT"),
            (14.0, "QTY"), (15.2, "STOCK"), (16.6, "UNIT PRICE"), (18.6, "EST. COST")]
    for x, label in cols:
        c.drawString(x * cm, y, label)
    y -= 0.6 * cm
    c.setFont("Helvetica", 8.5)
    for i, it in enumerate(items, start=1):
        if y < 8 * cm:
            c.showPage()
            y = h - 2 * cm
            c.setFont("Helvetica", 8.5)
        c.drawString(1.6 * cm, y, str(i))
        c.drawString(2.1 * cm, y, str(it.get("item") or "")[:22])
        desc = str(it.get("description") or "")
        c.drawString(6.6 * cm, y, desc[:52])
        c.drawString(12.6 * cm, y, str(it.get("unit") or ""))
        c.drawRightString(14.6 * cm, y, _fmt(it.get("qty")))
        c.drawRightString(16.0 * cm, y, _fmt(it.get("current_stock")))
        c.drawRightString(18.2 * cm, y, _fmt(it.get("unit_price")))
        c.drawRightString(20.4 * cm, y, _fmt(it.get("est_cost")))
        y -= 0.5 * cm

    y -= 0.2 * cm
    c.setLineWidth(0.5)
    c.line(1.5 * cm, y, w - 1.5 * cm, y)
    y -= 0.6 * cm
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(w - 1.5 * cm, y,
                      f"TOTAL: {_fmt(pr.get('total'))} {pr.get('currency') or ''}")
    y -= 1.0 * cm

    # signature ladder — Requester first, then each approved stage
    c.setFont("Helvetica-Bold", 10)
    c.drawString(1.5 * cm, y, "Approval signatures")
    y -= 0.2 * cm
    blocks = [{"label": "Requester", "name": pr.get("requester_name") or pr.get("requester"),
               "sig": None, "date": pr.get("submitted_at") or pr.get("request_date"),
               "status": "originator"}]
    for s in steps:
        blocks.append({"label": s.get("approver_role") or s.get("stage"),
                       "name": s.get("approver_name") or "—",
                       "sig": s.get("sig_png"), "date": (s.get("acted_at") or "")[:10],
                       "status": s.get("status")})

    bx, bw, bh = 1.5 * cm, (w - 3 * cm) / 3, 2.4 * cm
    for idx, b in enumerate(blocks):
        col = idx % 3
        if col == 0:
            y -= bh + 0.3 * cm
            if y < 2 * cm:
                c.showPage()
                y = h - 3 * cm
        x = bx + col * bw
        c.setLineWidth(0.4)
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.rect(x, y, bw - 0.3 * cm, bh, fill=0, stroke=1)
        c.setStrokeColorRGB(0, 0, 0)
        c.setFont("Helvetica-Bold", 8.5)
        c.drawString(x + 0.2 * cm, y + bh - 0.45 * cm, str(b["label"]))
        # signature image
        img = _img_reader(b["sig"])
        if img:
            try:
                c.drawImage(img, x + 0.2 * cm, y + 0.7 * cm, width=bw - 0.9 * cm,
                            height=1.0 * cm, mask="auto", preserveAspectRatio=True)
            except Exception:
                pass
        elif b["status"] == "pending":
            c.setFont("Helvetica-Oblique", 8)
            c.setFillColorRGB(0.6, 0.6, 0.6)
            c.drawString(x + 0.2 * cm, y + 1.1 * cm, "Awaiting signature")
            c.setFillColorRGB(0, 0, 0)
        c.setFont("Helvetica", 7.5)
        c.setFillColorRGB(0.35, 0.35, 0.35)
        c.drawString(x + 0.2 * cm, y + 0.35 * cm,
                     f"{b['name']}  {('· ' + b['date']) if b['date'] else ''}"[:40])
        c.setFillColorRGB(0, 0, 0)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()


def po_pdf(bundle):
    """Render the Purchase Order for a fully-approved PR. Returns PDF bytes."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.pdfgen import canvas

    pr = bundle["pr"]
    items = bundle["items"]
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    _header(c, w, h, "PURCHASE ORDER", pr, cm)

    y = h - 2.1 * cm
    c.setFont("Helvetica-Bold", 12)
    c.drawString(1.5 * cm, y, pr.get("po_no") or "PO")
    c.setFont("Helvetica", 9)
    c.drawRightString(w - 1.5 * cm, y, f"Ref PR: {pr.get('pr_no') or ''}")
    y -= 0.8 * cm

    c.setFont("Helvetica", 9)
    for label, val in [("Vendor", pr.get("vendor")), ("Department", pr.get("department")),
                       ("Payment terms", pr.get("payment_condition")),
                       ("Delivery", pr.get("delivery_condition")),
                       ("Approved", (pr.get("approved_at") or "")[:10])]:
        c.setFillColorRGB(0.45, 0.45, 0.45)
        c.drawString(1.5 * cm, y, f"{label}:")
        c.setFillColorRGB(0, 0, 0)
        c.drawString(3.9 * cm, y, str(val or "—")[:60])
        y -= 0.55 * cm
    y -= 0.3 * cm

    c.setFillColorRGB(0.96, 0.96, 0.98)
    c.rect(1.5 * cm, y - 0.15 * cm, w - 3 * cm, 0.6 * cm, fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica-Bold", 8.5)
    for x, label in [(1.6, "#"), (2.1, "ITEM / DESCRIPTION"), (13.5, "QTY"),
                     (15.5, "UNIT PRICE"), (18.2, "TOTAL")]:
        c.drawString(x * cm, y, label)
    y -= 0.6 * cm
    c.setFont("Helvetica", 8.5)
    for i, it in enumerate(items, start=1):
        c.drawString(1.6 * cm, y, str(i))
        c.drawString(2.1 * cm, y, (str(it.get("item") or "") + " — " +
                                   str(it.get("description") or ""))[:64])
        c.drawRightString(14.5 * cm, y, _fmt(it.get("qty")))
        c.drawRightString(17.6 * cm, y, _fmt(it.get("unit_price")))
        c.drawRightString(20.4 * cm, y, _fmt(it.get("est_cost")))
        y -= 0.5 * cm
    y -= 0.2 * cm
    c.line(1.5 * cm, y, w - 1.5 * cm, y)
    y -= 0.6 * cm
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(w - 1.5 * cm, y,
                      f"TOTAL: {_fmt(pr.get('total'))} {pr.get('currency') or ''}")

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
