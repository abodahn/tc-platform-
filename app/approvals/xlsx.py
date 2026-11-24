"""Excel (.xlsx) twins of the PR / PO / RFQ PDFs.

Each workbook is built from the SAME bundle and the SAME selection helpers as
its PDF (pdf.po_view, pdf.rfq_view, pdf.pr_meta_pairs, pdf._totals_lines ...),
so a supplier's spreadsheet can never carry a line, a price or a figure its
printed copy does not. Figures are written as numbers (values, not formulas),
so they equal the system to the cent and still add up in Excel.
"""
import datetime as _dt
import io
import re

from app.approvals import constants as C
from app.approvals import pdf as P

MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
COMPANY = "TC Garments"
MONEY = "#,##0.00"
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


def _num(v):
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _date(v):
    """ISO date / datetime text -> a real date; anything else stays text."""
    s = str(v or "").strip()
    if not s:
        return None
    if _ISO_DT.match(s):
        try:
            d = _dt.datetime.fromisoformat(s.replace(" ", "T"))
            return d.date() if len(s) == 10 else d.replace(microsecond=0)
        except ValueError:
            pass
    return s


class _Sheet:
    """A worksheet written top to bottom. `wide` rows (titles, notes, sentences)
    are left out of the column-width sizing so one long sentence does not make
    a column forty characters wide."""

    def __init__(self, title):
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill
        self.Alignment, self.Font = Alignment, Font
        self.head_fill = PatternFill("solid", fgColor="EEEFF2")
        self.wb = openpyxl.Workbook()
        self.ws = self.wb.active
        self.ws.title = title[:31]
        self.r = 0
        self.wide = set()
        self.wrap_cols = set()

    def put(self, row, col, v, bold=False, fmt=None, wrap=False, right=False):
        c = self.ws.cell(row=row, column=col)
        if isinstance(v, str):
            v = _ILLEGAL.sub("", v)
            c.value = v
            # Formula injection: user text starting = + - @ (or tab / CR) is
            # kept as TEXT — quotePrefix is Excel's own leading apostrophe.
            if v[:1] in ("=", "+", "-", "@", "\t", "\r"):
                c.data_type = "s"
                c.quotePrefix = True
        else:
            c.value = v
        if isinstance(v, _dt.datetime):
            c.number_format = "yyyy-mm-dd hh:mm"
        elif isinstance(v, _dt.date):
            c.number_format = "yyyy-mm-dd"
        elif fmt:
            c.number_format = fmt
        if bold:
            c.font = self.Font(bold=True)
        if wrap or right:
            c.alignment = self.Alignment(wrap_text=wrap, vertical="top",
                                         horizontal="right" if right else None)
        return c

    def line(self, text="", bold=False, size=None):
        """One sentence in column A, spilling right (never sizes a column)."""
        self.r += 1
        if text:
            c = self.put(self.r, 1, text, bold=bold)
            if size:
                c.font = self.Font(bold=bold, size=size)
        self.wide.add(self.r)

    def title(self, title, doc_no, sub=""):
        self.line(COMPANY, bold=True, size=12)
        self.line("%s  %s" % (title, doc_no), bold=True, size=14)
        if sub:
            self.line(sub)
        self.r += 1

    def meta(self, pairs, per_row=3):
        """Label / value pairs, `per_row` across: label bold, value beside it."""
        for i, (label, val) in enumerate(pairs):
            if i % per_row == 0:
                self.r += 1
            col = (i % per_row) * 2 + 1
            self.put(self.r, col, label, bold=True)
            self.put(self.r, col + 1, _date(val) if label in _DATE_LABELS else
                     (val if val not in (None, "") else ""), wrap=True)
        self.r += 1

    def table(self, cols, rows, wrap=(), freeze=True):
        """cols = [(header, number_format or None)]; the line table's header row
        is frozen and repeated on every printed page."""
        self.r += 1
        hdr = self.r
        for j, (h, _f) in enumerate(cols, 1):
            c = self.put(hdr, j, h, bold=True, wrap=True)
            c.fill = self.head_fill
        if freeze:
            self.ws.freeze_panes = "A%d" % (hdr + 1)
            self.ws.print_title_rows = "%d:%d" % (hdr, hdr)
        self.wrap_cols |= {j for j, (h, _f) in enumerate(cols, 1) if h in wrap}
        for row in rows:
            self.r += 1
            for j, ((h, f), v) in enumerate(zip(cols, row), 1):
                self.put(self.r, j, v, fmt=f, wrap=j in self.wrap_cols)
        self.r += 1
        return hdr

    def totals(self, lines, grand, value_col, currency):
        """The totals box: label right-aligned beside the value column."""
        cur = currency or ""
        for label, val in list(lines) + [("GRAND TOTAL", grand)]:
            self.r += 1
            self.put(self.r, value_col - 1, "%s (%s)" % (label, cur) if cur else label,
                     bold=label == "GRAND TOTAL", right=True)
            self.put(self.r, value_col, val, fmt=MONEY, bold=label == "GRAND TOTAL")
            self.wide.add(self.r)
        self.line("Amount in words: " + P._amount_words(grand, currency))

    def save(self):
        from openpyxl.utils import get_column_letter
        ws = self.ws
        widths = {}
        for row in ws.iter_rows():
            for c in row:
                if c.row in self.wide or c.value is None:
                    continue
                if isinstance(c.value, (_dt.date, _dt.datetime)):
                    n = 16
                elif isinstance(c.value, float):
                    n = len("{:,.2f}".format(c.value))
                else:
                    n = max(len(s) for s in str(c.value).split("\n"))
                widths[c.column] = max(widths.get(c.column, 0), n)
        for col, n in widths.items():
            cap = 45 if col in self.wrap_cols else 30
            ws.column_dimensions[get_column_letter(col)].width = min(max(n + 2, 8), cap)
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        buf = io.BytesIO()
        self.wb.save(buf)
        return buf.getvalue()


_DATE_LABELS = {"Date", "Approved on", "Issued on", "Reply by", "Required delivery date"}


def _lead_days(bundle, it):
    """The line's own lead time, else the chosen quote's (pdf._chosen_lead_time)."""
    if it.get("lead_time_days") is not None:
        return _num(it["lead_time_days"])
    for q in bundle.get("quotes") or []:
        if q.get("is_chosen") and q.get("lead_time_days"):
            return _num(q["lead_time_days"])
    return None


def _issued(items):
    return any(float(it.get("issued_qty") or 0) > 0 for it in items)


_STATUS_WORDS = {"approved": "Signed", "pending": "Awaiting signature",
                 "rejected": "Rejected", "skipped": "Skipped",
                 "originator": "Signed by submitting"}


def _signatures(sh, pr, steps):
    """pdf._signature_grid as rows of text."""
    sh.r += 1
    sh.line("Approval signatures", bold=True)
    sh.line("DOAM Table 5:  P prepare · R review · A approve · E endorse")
    sh.line("Verify a signature: " + P._verify_base() + "/procurement/verify/<code>")
    if sum(1 for s in (steps or []) if P._step_action(s) == "approve") > 1:
        sh.line(P.JOINT_APPROVAL_NOTE)
    for ln in P._esc_lines(steps or []):
        sh.line(ln)
    rows = [["Requester", "P", pr.get("requester_name") or pr.get("requester") or "",
             "", _STATUS_WORDS["originator"],
             _date(pr.get("submitted_at") or pr.get("request_date")), "", ""]]
    for s in steps or []:
        rung = C.stage_label(s.get("stage") or "") or s.get("stage") or ""
        rows.append([rung + (" (escalated)" if s.get("esc_from") else ""),
                     C.STEP_ACTION_CODE[P._step_action(s)],
                     s.get("approver_name") or "",
                     P._role_names(s.get("approver_role")) if s.get("approver_role") else "",
                     _STATUS_WORDS.get(s.get("status"), s.get("status") or ""),
                     _date(s.get("acted_at")), s.get("in_place_of") or "",
                     s.get("verify_code") or ""])
    sh.table([("Rung", None), ("Action", None), ("Signer", None), ("Role", None),
              ("Status", None), ("Date / time", None), ("Signed in place of", None),
              ("Verification code", None)], rows, freeze=False)


# --------------------------------------------------------------------------
def pr_xlsx(bundle, money=True):
    """pdf.pr_pdf as a workbook. Without `money` the price, discount, cost,
    VAT and last-price columns and the totals are not written at all."""
    pr, items, steps = bundle["pr"], bundle["items"], bundle["steps"]
    sh = _Sheet("Purchase request")
    sh.title("PURCHASE REQUEST", pr.get("pr_no") or "PR",
             "%s · Status: %s" % (C.FORM_CODES["capex"] if pr.get("expenditure_kind") == "capex"
                                  else C.FORM_CODES["pr"],
                                  (pr.get("status") or "").replace("_", " ")))
    sh.meta(P.pr_meta_pairs(pr))

    cur = pr.get("currency") or ""
    rate = P._vat_rate(pr)
    disc = money and P._has_disc(pr, items)
    stock = _issued(items)
    cols = ([("S", None), ("ITEM", None), ("Description", None), ("UNIT", None),
             ("QTY", "General")] + ([("From stock", "General")] if stock else [])
            + [("On Hand", "General"), ("Stock days", "General"),
               ("Pending qty", "General"), ("LAST ORDER QTY.", "General"),
               ("LAST ORDER DATE", None), ("VENDOR", None)]
            + ([("UNIT PRICE", MONEY)] + ([("DISC.", MONEY)] if disc else [])
               + [("EST. COST", MONEY), ("VAT", MONEY), ("AFTER VAT", MONEY)]
               if money else [])
            + [("CUR.", None)] + ([("LAST ORDER PRICE", MONEY)] if money else [])
            + [("P.O#", None), ("Last Stock", "General"), ("PAY. COND.", None),
               ("DEL. COND.", None), ("ETA", None), ("LEAD Time (days)", "General")])
    rows = []
    for i, it in enumerate(items, 1):
        net = P._line_net(it)
        vat = round(net * rate / 100.0, 2)
        rows.append(
            [i, it.get("item") or "", P._line_desc(it), it.get("unit") or "",
             _num(it.get("qty"))]
            + ([_num(it.get("issued_qty")) or None] if stock else [])
            + [_num(it.get("current_stock")), _num(it.get("stock_days")),
               _num(it.get("pending_qty")) or None, _num(it.get("last_order_qty")),
               _date(it.get("last_order_date")), it.get("vendor") or pr.get("vendor") or ""]
            + ([_num(it.get("unit_price"))]
               + ([_num(it.get("disc_amount")) or None] if disc else [])
               + [_num(it.get("est_cost")) or 0.0, vat, round(net + vat, 2)]
               if money else [])
            + [cur] + ([_num(it.get("last_order_price"))] if money else [])
            + [pr.get("po_no") or "", _num(it.get("last_stock")),
               pr.get("payment_condition") or "", pr.get("delivery_condition") or "",
               _date(it.get("eta") or pr.get("req_del_date")), _lead_days(bundle, it)])
    sh.table(cols, rows, wrap=("ITEM", "Description", "VENDOR", "PAY. COND.", "DEL. COND."))

    if money:
        lines, grand = P._totals_lines(pr, P._discount_of(pr, items))
        sh.totals(lines, grand, [h for h, _f in cols].index("AFTER VAT") + 1,
                  pr.get("currency"))

    _signatures(sh, pr, steps)
    sh.r += 1
    for n in P._PR_NOTES:
        sh.line(n)
    return sh.save()


def po_xlsx(bundle, po=None):
    """pdf.po_pdf as a workbook: ONE vendor's order, its lines and totals only."""
    pr, items = P.po_view(bundle, po)
    sh = _Sheet("Purchase order")
    sh.title("PURCHASE ORDER", P.po_doc_no(pr),
             "Ref %s · %s" % (pr.get("pr_no") or "", C.FORM_CODES["po"]))
    sh.meta(P.po_meta_pairs(pr))

    cur = pr.get("currency") or ""
    rate = P._vat_rate(pr)
    disc = P._has_disc(pr, items)
    cols = ([("#", None), ("ITEM", None), ("DESCRIPTION", None), ("QTY", "General"),
             ("UNIT PRICE", MONEY)] + ([("DISC.", MONEY)] if disc else [])
            + [("TOTAL", MONEY), ("VAT %g%%" % rate, MONEY), ("AFTER VAT", MONEY)])
    rows = []
    for i, it in enumerate(items, 1):
        net = P._line_net(it)
        v = round(net * rate / 100.0, 2)
        rows.append([i, it.get("item") or "", P._line_desc(it), _num(it.get("qty")),
                     _num(it.get("unit_price"))]
                    + ([_num(it.get("disc_amount")) or None] if disc else [])
                    + [_num(it.get("est_cost")) or 0.0, v, round(net + v, 2)])
    sh.table(cols, rows, wrap=("ITEM", "DESCRIPTION"))

    lines, grand = P._totals_lines(pr, P._discount_of(pr, items))
    sh.totals(lines, grand, len(cols), pr.get("currency"))
    fx = P._fx_rate(pr)
    if fx not in (0.0, 1.0):
        sh.r += 1
        sh.put(sh.r, len(cols) - 1, "EGP equivalent (@ %g EGP / %s)" % (fx, cur or "unit"),
               right=True)
        sh.put(sh.r, len(cols), round(P.egp_equivalent(pr), 2), fmt=MONEY)
        sh.wide.add(sh.r)

    esc = P._esc_lines(bundle.get("steps") or [])
    if esc:
        sh.r += 1
        sh.line("Approval deviations (segregation of duties):", bold=True)
        for ln in esc:
            sh.line(ln)
    sh.r += 1
    for ln in P.po_authority_lines(bundle):
        sh.line(ln)
    sh.line(P.PO_REF_LINE)
    return sh.save()


def rfq_xlsx(bundle, rfq):
    """pdf.rfq_pdf as a workbook: quantities only; the two price columns are
    left empty for the supplier, exactly as the printed boxes are."""
    pr = bundle["pr"]
    rfq, vendor, items = P.rfq_view(bundle, rfq)
    sh = _Sheet("Request for quotation")
    sh.title("REQUEST FOR QUOTATION", rfq.get("rfq_no") or "RFQ",
             "Ref %s · %s" % (pr.get("pr_no") or "", C.FORM_CODES["rfq"]))
    sh.meta(P.rfq_meta_pairs(pr, rfq, vendor))
    stock = _issued(items)
    cols = ([("#", None), ("ITEM", None), ("DESCRIPTION", None), ("UNIT", None),
             ("QTY", "General")] + ([("From stock", "General")] if stock else [])
            + [("UNIT PRICE", MONEY), ("TOTAL", MONEY)])
    rows = [[i, it.get("item") or "", P._line_desc(it), it.get("unit") or "",
             _num(it.get("qty"))] + ([_num(it.get("issued_qty")) or None] if stock else [])
            + [None, None]
            for i, it in enumerate(items, 1)]
    sh.table(cols, rows, wrap=("ITEM", "DESCRIPTION"))
    sh.line(P.rfq_prices_line(pr))
    sh.r += 1
    sh.line("Supplier's quotation — signature and stamp", bold=True)
    for b in P.RFQ_SIGN_BOXES:
        sh.r += 1
        sh.put(sh.r, 1, b, bold=True)
        sh.wide.add(sh.r)
    sh.r += 1
    for n in P._RFQ_NOTES:
        sh.line(n)
    return sh.save()
