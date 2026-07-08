"""Export AI alerts / risk scores to CSV, Excel and PDF."""
import csv
import io
from datetime import datetime


def _cell(v):
    """Guard against CSV/Excel formula injection."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def alerts_csv(alerts):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ID", "Domain", "Title", "Risk", "Severity", "Status",
                "Responsible", "Recommendation", "Impact", "Created", "Due"])
    for a in alerts:
        w.writerow([_cell(a.get("alert_uid")), _cell(a.get("domain")), _cell(a.get("title")),
                    _cell(a.get("risk_score")), _cell(a.get("severity")), _cell(a.get("status")),
                    _cell(a.get("responsible")), _cell(a.get("recommendation")),
                    _cell(a.get("impact")), _cell(a.get("created_at")), _cell(a.get("due_date"))])
    return buf.getvalue().encode("utf-8-sig")


def alerts_xlsx(alerts):
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "AI Alerts"
    headers = ["ID", "Domain", "Title", "Risk", "Severity", "Status",
               "Responsible", "Recommendation", "Impact", "Created", "Due"]
    ws.append(headers)
    for a in alerts:
        ws.append([_cell(a.get("alert_uid")), _cell(a.get("domain")), _cell(a.get("title")),
                   a.get("risk_score"), _cell(a.get("severity")), _cell(a.get("status")),
                   _cell(a.get("responsible")), _cell(a.get("recommendation")),
                   _cell(a.get("impact")), _cell(a.get("created_at")), _cell(a.get("due_date"))])
    for i, w in enumerate([16, 14, 40, 8, 10, 12, 18, 44, 34, 20, 12], 1):
        ws.column_dimensions[chr(64 + i)].width = w
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue()


def risk_pdf(title, scores, alerts, health=None):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=A4, topMargin=18 * mm, bottomMargin=16 * mm,
                            leftMargin=15 * mm, rightMargin=15 * mm, title=title)
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Title"], textColor=colors.HexColor("#ED1C24"), fontSize=20)
    small = ParagraphStyle("s", parent=ss["Normal"], fontSize=8.5, textColor=colors.HexColor("#667085"))
    el = [Paragraph(title, h)]
    stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    el.append(Paragraph(f"T&amp;C Garments · AI Prediction &amp; Intelligence Center · {stamp}"
                        + (f" · Company health {health}/100" if health is not None else ""), small))
    el.append(Spacer(1, 8 * mm))

    if scores:
        el.append(Paragraph("Domain risk scores", ss["Heading2"]))
        data = [["Domain", "Score", "Level"]] + [[s["domain"], s["score"], s["level"]] for s in scores]
        t = Table(data, colWidths=[80 * mm, 30 * mm, 40 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#07080B")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
            ("FONTSIZE", (0, 0), (-1, -1), 9), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ]))
        el.append(t)
        el.append(Spacer(1, 8 * mm))

    el.append(Paragraph(f"Top alerts ({len(alerts)})", ss["Heading2"]))
    rows = [["Domain", "Risk", "Title", "Recommendation"]]
    for a in alerts[:40]:
        rows.append([a.get("domain"), a.get("risk_score"),
                     Paragraph(str(a.get("title") or "")[:70], small),
                     Paragraph(str(a.get("recommendation") or "")[:90], small)])
    t2 = Table(rows, colWidths=[24 * mm, 15 * mm, 66 * mm, 75 * mm], repeatRows=1)
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#07080B")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e5e7eb")),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ]))
    el.append(t2)
    doc.build(el)
    return bio.getvalue()
