"""Does every PR column hold a real garment-industry number, whole?

Run: python scratchpad/fit.py   (exit 0 = pass)

The previous version of this file probed values picked small enough to pass
(1,200.00 for a quantity). This one does the opposite: for each column it walks
the magnitudes UP until the cell can no longer be rendered whole, and asserts
that ceiling clears a realistic one. Then it renders actual PDFs and reads
the text back (0, 1, 30 and 120 line items), because measuring the wrong thing is
exactly how this got shipped.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fitz  # PyMuPDF, for reading the rendered page back
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import cm
from reportlab.pdfbase.pdfmetrics import stringWidth

from app.approvals import pdf as P

FONT_FLOOR = 4.5          # _table stops shrinking here and starts clipping
PAD = 0.07 * cm
BASE = 6.0
MONEY_CEIL = 9_999_999.99     # a fabric order's est. cost
QTY_CEIL = 9_999_999          # metres of fabric / pieces

PAGE_W = landscape(A4)[0]
TOTAL_W = PAGE_W - 3 * cm
_RATIOS = [r for r, _, _ in P._PR_COLS]
_SCALE = TOTAL_W / (sum(_RATIOS) * cm)
AVAIL = {label: r * cm * _SCALE - 2 * PAD for r, label, _ in P._PR_COLS}
WRAPPED = {P._PR_COLS[i][1] for i in (1, 2, 10, 17, 18)}


def fits(text, avail):
    """Mirror _table's non-wrapping branch: shrink, then give up at FONT_FLOOR."""
    s = BASE
    while s > FONT_FLOOR and stringWidth(text, "Helvetica", s) > avail:
        s -= 0.25
    return stringWidth(text, "Helvetica", s) <= avail, s


def ceiling(label, dec):
    """Largest power-of-ten value this column still renders whole."""
    v, last = 1.0, None
    while v < 1e12:
        ok, _ = fits(P._fmt(v * 1.111 + (0.99 if dec else 0), dec), AVAIL[label])
        if not ok:
            return last
        last = v * 1.111
        v *= 10
    return last


def main():
    bad = []

    # ---- 1. numeric headroom, found by walking up, not by guessing -----------
    for label, dec, need in (
        ("QTY", 0, QTY_CEIL), ("On Hand", 0, QTY_CEIL),
        ("LAST ORDER QTY.", 0, QTY_CEIL), ("UNIT PRICE", 2, MONEY_CEIL),
        ("EST. COST", 2, MONEY_CEIL), ("LAST ORDER PRICE", 2, MONEY_CEIL),
    ):
        top = ceiling(label, dec)
        if top is None or top < need:
            bad.append(f"{label}: holds only up to {top!r}, needs {need:,}")

    # ---- 2. the specific values the verifier broke it with ------------------
    for label, dec, val in (
        ("QTY", 0, 120000), ("On Hand", 0, 45300), ("On Hand", 0, 12000),
        ("EST. COST", 2, 25290000.0), ("UNIT PRICE", 2, 210.75),
        ("LAST ORDER PRICE", 2, 189999.5), ("LAST ORDER QTY.", 0, 480000),
        ("S", 0, 120), ("S", 0, 999),
    ):
        txt = P._fmt(val, dec) if label != "S" else str(val)
        ok, size = fits(txt, AVAIL[label])
        if not ok:
            bad.append(f"{label}: '{txt}' still does not fit at {FONT_FLOOR}pt")
        elif size < 5.0:
            bad.append(f"{label}: '{txt}' only fits by shrinking to {size}pt")

    # ---- 3. the text columns that carry contract terms must wrap ------------
    for label in ("VENDOR", "PAY. COND.", "DEL. COND."):
        if label not in WRAPPED:
            bad.append(f"{label} is not in wrap_col — it will be clipped")

    # ---- 4. render for real and read the page back --------------------------
    long_vendor = "SKF Middle East Trading & Industrial Supplies LLC"
    item = {"item": "FAB-CVC-6040", "description": "CVC 60/40 poplin 145gsm, "
            "reactive dyed, width 150cm, supplier lot controlled",
            "unit": "MTR", "qty": 120000, "current_stock": 45300,
            "last_order_qty": 480000, "last_order_date": "2026-03-11",
            "vendor": long_vendor, "unit_price": 210.75, "est_cost": 25290000.0,
            "last_order_price": 189999.5}
    pr = {"pr_no": "PR-2026-00417", "status": "approved", "currency": "EGP",
          "request_date": "2026-08-18", "requester_name": "Ahmed ElGohary",
          "request_for": "Production", "department": "Fabric Store",
          "title": "Bulk poplin for SO-8841", "vendor": long_vendor,
          "payment_condition": "60 days net from invoice date",
          "delivery_condition": "DDP Cairo", "req_del_date": "2026-10-01",
          "po_no": "PO-2026-00998", "total": 25290000.0, "tax_rate": 14,
          "so_no": "SO-8841", "cost_center": "CC-FAB-01"}

    def render(n, name, **over):
        b = {"pr": dict(pr, **over), "items": [dict(item) for _ in range(n)],
             "steps": [], "quotes": [{"is_chosen": 1, "lead_time_days": 45}]}
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        with open(out, "wb") as f:
            f.write(P.pr_pdf(b))
        return out

    for n, name in ((0, "pr_0.pdf"), (1, "pr_1.pdf"), (30, "pr_30.pdf"), (120, "pr_120.pdf")):
        path = render(n, name)
        doc = fitz.open(path)
        p0 = doc[0]
        assert round(p0.rect.width) > round(p0.rect.height), f"{name}: not landscape"
        flat = re.sub(r"\s+", " ", " ".join(pg.get_text() for pg in doc))
        for _, label, _ in P._PR_COLS:               # all 21 headings, exact
            if label not in flat:                    # headings wrap onto 2-3 lines
                bad.append(f"{name}: heading {label!r} missing")
        if n:
            for want in ("120,000", "45,300", "25,290,000.00", "480,000",
                         "210.75", "189,999.50", "DDP Cairo", "PO-2026-00998"):
                if want not in flat:
                    bad.append(f"{name}: {want!r} not rendered whole")
            # the long vendor survives, wrapped across lines
            if not all(w in flat for w in long_vendor.split()):
                bad.append(f"{name}: vendor name lost words")
            # blank columns stayed blank: no stray zeros in the numeric run
            if re.search(r"\b0\b", p0.get_text()):
                bad.append(f"{name}: a bare '0' printed (blank column filled in?)")
        if "…" in flat:                         # nothing may be truncated
            bad.append(f"{name}: an ellipsis survived — something was clipped")
        # nothing may spill past the right margin
        for blk in p0.get_text("words"):
            if blk[2] > p0.rect.width - 1.5 * cm + 1.5:
                bad.append(f"{name}: {blk[4]!r} spills past the right margin")
                break
        doc.close()

    # the other Incoterms and payment terms a buyer actually types
    for dc, pc in (("CIF Alexandria", "LC at sight"),
                   ("FOB Shanghai", "30% advance, 70% against B/L copy"),
                   ("EXW Izmir Organized Industrial Zone", "cash on delivery")):
        doc = fitz.open(render(1, "pr_terms.pdf", delivery_condition=dc,
                               payment_condition=pc))
        flat = re.sub(r"\s+", " ", doc[0].get_text())
        for want in (dc, pc):
            if want not in flat:
                bad.append(f"terms: {want!r} not rendered whole -> "
                           f"{'…' if '…' in flat else 'missing'}")
        doc.close()

    print("\n".join(bad) if bad else "0 problems")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
