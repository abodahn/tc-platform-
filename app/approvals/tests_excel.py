"""PR / PO / RFQ as Excel (.xlsx) beside every PDF.

    python -m app.approvals.tests_excel

Proves, on the response bytes read back with openpyxl:
  1. pr.xlsx / po.xlsx / rfq.xlsx answer 200 + the xlsx MIME as an attachment
     named like the PDF, and refuse exactly as the PDF route refuses;
  2. line values and totals are NUMBERS equal to pr_amounts() and to the
     figures the PDF prints (VAT, discount, grand total);
  3. a user the PDF shows no money gets no price / discount / VAT / total;
  4. ?po= picks that vendor's lines only; the RFQ carries no prices;
  5. "=HYPERLINK(...)", "+cmd", "@SUM(1)" are stored as text, never formulas;
     Arabic item text round-trips;
  6. the detail page shows an Excel button exactly where a PDF link is.
"""
import base64
import io
import os
import re
import sys
import tempfile
import zipfile
import zlib
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
EVIL = ['=HYPERLINK("http://example.invalid","x")', "+cmd|' /C calc'!A0", "@SUM(1)"]
ARABIC = "حزام ناقل"


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def _pdf_text(blob):
    out = []
    for m in re.finditer(rb"stream\r?\n(.*?)\s*endstream", blob, re.S):
        data = m.group(1)
        for step in (lambda x: base64.a85decode(x, adobe=True), zlib.decompress):
            try:
                data = step(data)
            except Exception:                       # noqa: BLE001
                pass
        out.append(data)
    return b"\n".join(out).decode("latin-1")


def _book(resp):
    import openpyxl
    return openpyxl.load_workbook(io.BytesIO(resp.get_data())).active


def _table(ws, first):
    """{header: column} and the data rows (cells) of the table whose first header is `first`."""
    for row in ws.iter_rows():
        if row[0].value == first and row[0].font.b:
            hdr = {c.value: c.column for c in row if c.value is not None}
            rows = []
            for r in ws.iter_rows(min_row=row[0].row + 1):
                if not isinstance(r[0].value, int):
                    break
                rows.append(r)
            return hdr, rows, row[0].row
    return {}, [], 0


def _beside(ws, label):
    """The cell right of the cell whose text is `label` (the totals block)."""
    for row in ws.iter_rows():
        for c in row:
            if c.value == label:
                return ws.cell(row=c.row, column=c.column + 1)
    return None


def _cells(ws):
    return [c for row in ws.iter_rows() for c in row if c.value is not None]


def run():                                          # noqa: C901 — one linear story
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="xlsx_"), "x.db")
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.db import get_db
        from app.approvals import services as svc
        from app.approvals.constants import STAGE_ROLES

        buyer = {"username": "buyer", "role": "purchasing_manager", "id": 1}

        def q(sql, *args):
            conn = get_db()
            try:
                return [dict(r) for r in conn.execute(sql, args).fetchall()]
            finally:
                conn.close()

        def make(title, lines, discounts=None, hdr=None, tax="14"):
            """lines = [(item, description, qty, price, vendor)]"""
            pr_id, _ = svc.create_pr(
                {"title": title, "department": "Maintenance", "vendor": lines[0][4]},
                [{"item": i, "description": d, "qty": qty, "unit_price": 0, "vendor": v}
                 for i, d, qty, _p, v in lines],
                {"username": "tech", "id": 9}, priced=False)
            ids = [r["id"] for r in q("SELECT id FROM pr_items WHERE pr_id=? ORDER BY seq, id",
                                      pr_id)]
            extra = dict({"tax_rate": tax}, **(hdr or {}))
            ok, msg = svc.price_pr(pr_id, {i: l[3] for i, l in zip(ids, lines)}, extra, buyer,
                                   **({"discounts": {ids[k]: v for k, v in discounts.items()}}
                                      if discounts else {}))
            assert ok, "pricing failed: %s" % msg
            return pr_id, ids

        def approve_and_issue(pr_id):
            svc.set_single_source(pr_id, "Sole authorised supplier for these parts", buyer)
            for _ in range(20):
                pr = q("SELECT status, current_seq FROM pr_requests WHERE id=?", pr_id)[0]
                steps = q("SELECT stage FROM pr_steps WHERE pr_id=? AND seq=? "
                          "AND status='pending'", pr_id, pr["current_seq"])
                if pr["status"] != "pending" or not steps:
                    break
                for s in steps:
                    role = sorted(STAGE_ROLES[s["stage"]])[0]
                    ok, msg = svc.act_on_step(
                        pr_id, {"username": "u_%s_%s" % (role, pr_id), "role": role,
                                "id": 100 + pr_id}, "approve", "ok")
                    assert ok, "ladder stuck at %s: %s" % (s["stage"], msg)
            c = get_db()
            c.execute("INSERT OR IGNORE INTO proc_vendors (name, is_active, created_at) "
                      "SELECT DISTINCT TRIM(COALESCE(i.vendor, r.vendor)), 1, '2026-01-01' "
                      "FROM pr_items i JOIN pr_requests r ON r.id=i.pr_id WHERE i.pr_id=? "
                      "AND TRIM(COALESCE(i.vendor, r.vendor, ''))<>''", (pr_id,))
            c.commit()
            c.close()
            ok, res = svc.issue_po(pr_id, buyer)
            assert ok, "issue_po refused: %s" % res

        # ---- a discounted, VAT-bearing, single-supplier request -> PO -------
        disc, d_ids = make("Excel export", [
            (EVIL[0], "Belt drive", 10, 100, "Alphatex"),
            (ARABIC, "", 4, 250, "Alphatex"),
            (EVIL[1], EVIL[2], 2, 50, "Alphatex")],
            discounts={0: ("pct", "10"), 1: ("val", "150")},
            hdr={"disc_type": "pct", "disc_value": "10"})
        approve_and_issue(disc)
        amounts = svc.pr_amounts(q("SELECT * FROM pr_requests WHERE id=?", disc)[0])
        chk("fixture: pr_amounts gross 2,100 / discount 435 / VAT 233.10 / grand 1,898.10",
            (amounts["gross"], amounts["discount_total"], amounts["tax"], amounts["grand"])
            == (2100, 435, 233.1, 1898.1), amounts)
        d_pr_no = q("SELECT pr_no FROM pr_requests WHERE id=?", disc)[0]["pr_no"]

        # ---- two suppliers, one order each ---------------------------------
        split, _ = make("Split order", [("Alphabearing", "", 10, 100, "Alphatex"),
                                        ("Betaseal", "", 20, 100, "Betatrim")], tax="0")
        approve_and_issue(split)
        pos = q("SELECT * FROM pr_purchase_orders WHERE pr_id=? ORDER BY id", split)

        # ---- an open request with an RFQ out --------------------------------
        open_pr, _ = make("RFQ export", [(ARABIC, "", 6, 777, "Alphatex"),
                                         (EVIL[0], EVIL[2], 3, 555, "Alphatex")])
        ok, rfqs = svc.issue_rfqs(open_pr, ["Zetasupply"], buyer, payment_terms="30 days",
                                  notes=EVIL[1])
        chk("fixture: RFQ issued", ok, rfqs)
        rfq = rfqs[0]

        conn = get_db()
        for name, role in (("xl_admin", "super_admin"), ("xl_nomoney", "normal_user")):
            conn.execute("INSERT OR IGNORE INTO users (username, password_hash, full_name, "
                         "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                         (name, "x", name, role))
        conn.commit()
        conn.close()
        uids = {r["username"]: r["id"] for r in
                q("SELECT id, username FROM users WHERE username LIKE 'xl_%'")}

    def client(name=None):
        cl = app.test_client()
        if name:
            with cl.session_transaction() as s:
                s["uid"], s["ep"] = uids[name], 0
        return cl

    adm, nom, anon = client("xl_admin"), client("xl_nomoney"), client()
    pdf_url = {"pr": "/procurement/pr/%d/pdf", "po": "/procurement/pr/%d/po.pdf",
               "rfq": "/procurement/pr/%d/rfq.pdf"}
    xl_url = {"pr": "/procurement/pr/%d/pr.xlsx", "po": "/procurement/pr/%d/po.xlsx",
              "rfq": "/procurement/pr/%d/rfq.xlsx"}

    # ---- 1. headers, names, and the same refusals as the PDF -------------
    for kind, pid, qs, fname in (("pr", disc, "", d_pr_no), ("po", disc, "", pos and None),
                                 ("rfq", open_pr, "?rfq=%d" % rfq["id"], rfq["rfq_no"])):
        r = adm.get(xl_url[kind] % pid + qs)
        cd = r.headers.get("Content-Disposition", "")
        chk("%s.xlsx 200 + xlsx MIME" % kind, r.status_code == 200 and r.mimetype == XLSX,
            (r.status_code, r.mimetype))
        chk("%s.xlsx is an attachment named *.xlsx" % kind,
            cd.startswith("attachment") and ".xlsx" in cd
            and (fname is None or fname + ".xlsx" in cd), cd)
    po_no = q("SELECT po_no FROM pr_requests WHERE id=?", disc)[0]["po_no"]
    chk("po.xlsx filename follows the PDF's (%s.xlsx)" % po_no,
        po_no + ".xlsx" in adm.get(xl_url["po"] % disc).headers.get("Content-Disposition", ""))

    cases = [
        ("missing PR", adm, "pr", 999999, ""), ("missing PR", adm, "po", 999999, ""),
        ("missing PR", adm, "rfq", 999999, ""),
        ("not signed in", anon, "pr", disc, ""), ("not signed in", anon, "po", disc, ""),
        ("not signed in", anon, "rfq", open_pr, ""),
        ("no-money user PO", nom, "po", disc, ""),
        ("PO before approval", adm, "po", open_pr, ""),
        ("unknown ?po=", adm, "po", split, "?po=999999"),
        ("unknown ?rfq=", adm, "rfq", open_pr, "?rfq=999999"),
        ("no RFQ on request", adm, "rfq", disc, ""),
    ]
    for label, cl, kind, pid, qs in cases:
        a = cl.get(pdf_url[kind] % pid + qs).status_code
        b = cl.get(xl_url[kind] % pid + qs).status_code
        chk("%s: %s.xlsx refuses like the PDF (%s)" % (label, kind, a), a == b and a != 200,
            (a, b))
    r = nom.get(xl_url["pr"] % disc)
    chk("no-money user still gets the PR spreadsheet (as the PDF)",
        r.status_code == 200 and nom.get(pdf_url["pr"] % disc).status_code == 200)

    # ---- 2. figures: numbers, equal to pr_amounts() and the PDF -----------
    ws = _book(adm.get(xl_url["pr"] % disc))
    pdf_txt = _pdf_text(adm.get(pdf_url["pr"] % disc).get_data())
    hdr, rows, hrow = _table(ws, "S")
    chk("PR line table has the PDF's money columns incl. DISC., VAT, AFTER VAT",
        all(h in hdr for h in ("UNIT PRICE", "DISC.", "EST. COST", "VAT", "AFTER VAT")),
        list(hdr))
    chk("PR header row is frozen", ws.freeze_panes == "A%d" % (hrow + 1), ws.freeze_panes)
    chk("print setup: landscape, fit to one page wide",
        ws.page_setup.orientation == "landscape" and ws.page_setup.fitToWidth == 1
        and ws.sheet_properties.pageSetUpPr.fitToPage)
    items = {it["id"]: it for it in svc_items(app, disc)}
    for r, iid in zip(rows, d_ids):
        it = items[iid]
        net = round(float(it["qty"]) * float(it["net_price"]), 2) \
            if it.get("net_price") is not None else float(it["est_cost"])
        vat = round(net * 14 / 100.0, 2)
        cell = {h: r[c - 1] for h, c in hdr.items()}
        chk("line %s: qty/price/est/VAT/after-VAT are numeric and exact" % it["seq"],
            all(cell[h].data_type == "n" for h in ("QTY", "UNIT PRICE", "EST. COST", "VAT",
                                                     "AFTER VAT"))
            and cell["QTY"].value == it["qty"] and cell["UNIT PRICE"].value == it["unit_price"]
            and cell["EST. COST"].value == it["est_cost"] and cell["VAT"].value == vat
            and cell["AFTER VAT"].value == round(net + vat, 2),
            [cell[h].value for h in ("QTY", "UNIT PRICE", "EST. COST", "VAT", "AFTER VAT")])
        chk("line %s: money cells use #,##0.00" % it["seq"],
            cell["EST. COST"].number_format == "#,##0.00")
    tot = {k: _beside(ws, k + " (EGP)") for k in ("Subtotal", "Discount", "VAT (14%)",
                                                 "GRAND TOTAL")}
    chk("totals are numbers equal to pr_amounts (gross / -discount / VAT / grand)",
        all(c is not None and c.data_type == "n" for c in tot.values())
        and (tot["Subtotal"].value, tot["Discount"].value, tot["VAT (14%)"].value,
             tot["GRAND TOTAL"].value)
        == (amounts["gross"], -amounts["discount_total"], amounts["tax"], amounts["grand"]),
        {k: (c.value if c else None) for k, c in tot.items()})
    chk("...and the PDF prints the same figures",
        all(s in pdf_txt for s in ("2,100.00", "435.00", "233.10", "1,898.10")))
    chk("amount in words is written",
        any(str(c.value).startswith("Amount in words") for c in _cells(ws)))
    sig = next((c for c in _cells(ws) if c.value == "Rung"), None)
    sig_rows = []
    if sig:
        heads = [c.value for c in ws[sig.row]]
        for rr in ws.iter_rows(min_row=sig.row + 1, values_only=True):
            if not rr[0]:
                break
            sig_rows.append(dict(zip(heads, rr)))
    signed = [s for s in sig_rows if s.get("Status") == "Signed"]
    chk("signature section: requester + every signed rung with signer, role and date/time",
        sig_rows and sig_rows[0]["Rung"] == "Requester" and len(signed) >= 2
        and all(s["Signer"] and s["Role"] and hasattr(s["Date / time"], "year")
                for s in signed) and "Signed in place of" in heads,
        sig_rows[:3])

    # ---- 3. the no-money user -------------------------------------------
    ws_n = _book(nom.get(xl_url["pr"] % disc))
    hdr_n, rows_n, _ = _table(ws_n, "S")
    money_heads = {"UNIT PRICE", "DISC.", "EST. COST", "VAT", "AFTER VAT", "LAST ORDER PRICE"}
    chk("no-money PR: no price / discount / VAT / total column", not (money_heads & set(hdr_n)),
        list(hdr_n))
    texts = " ".join(str(c.value) for c in _cells(ws_n))
    chk("no-money PR: no totals block or amount in words",
        "GRAND TOTAL" not in texts and "Subtotal" not in texts and "Amount in words" not in texts)
    nums = {c.value for c in _cells(ws_n) if c.data_type == "n"}
    leaked = nums & {100, 250, 50, 900, 850, 100.0, 2100, 1665, 233.1, 1898.1, 435}
    chk("no-money PR: no price or total figure anywhere", not leaked, leaked)
    chk("no-money PR still lists the lines", len(rows_n) == 3, len(rows_n))

    # ---- 4. split PO and RFQ --------------------------------------------
    beta = next(p for p in pos if p["vendor"] == "Betatrim")
    r = adm.get(xl_url["po"] % split + "?po=%d" % beta["id"])
    ws_b = _book(r)
    hdr_b, rows_b, _ = _table(ws_b, "#")
    names = [row[hdr_b["ITEM"] - 1].value for row in rows_b]
    chk("?po= Betatrim: only Betatrim's line", names == ["Betaseal"], names)
    g = _beside(ws_b, "GRAND TOTAL (EGP)")
    chk("?po= Betatrim: grand total is its own 2,000", g is not None and g.value == 2000,
        g and g.value)
    chk("?po= names that order", beta["po_no"] in r.headers.get("Content-Disposition", ""))
    chk("PO carries VAT / AFTER VAT and DISC. when discounted",
        {"UNIT PRICE", "DISC.", "TOTAL", "VAT 14%", "AFTER VAT"}
        <= set(_table(_book(adm.get(xl_url["po"] % disc)), "#")[0]))
    gpo = _beside(_book(adm.get(xl_url["po"] % disc)), "GRAND TOTAL (EGP)")
    chk("PO grand total = pr_amounts grand", gpo is not None and gpo.value == amounts["grand"],
        gpo and gpo.value)

    ws_r = _book(adm.get(xl_url["rfq"] % open_pr + "?rfq=%d" % rfq["id"]))
    hdr_r, rows_r, _ = _table(ws_r, "#")
    chk("RFQ lists the lines with quantities", [r_[hdr_r["QTY"] - 1].value for r_ in rows_r]
        == [6, 3])
    chk("RFQ price boxes are empty", all(r_[hdr_r[h] - 1].value is None for r_ in rows_r
                                         for h in ("UNIT PRICE", "TOTAL")))
    rnums = {c.value for c in _cells(ws_r) if c.data_type == "n"}
    chk("RFQ carries no price anywhere", not (rnums & {777, 555, 4662, 1665}), rnums)

    # ---- 5. formula injection + Arabic ----------------------------------
    for label, resp in (("PR", adm.get(xl_url["pr"] % disc)),
                        ("PO", adm.get(xl_url["po"] % disc)),
                        ("RFQ", adm.get(xl_url["rfq"] % open_pr))):
        wsx = _book(resp)
        cells = _cells(wsx)
        sheet = zipfile.ZipFile(io.BytesIO(resp.get_data())).read("xl/worksheets/sheet1.xml")
        chk("%s: no formula anywhere in the sheet" % label,
            b"<f>" not in sheet and b"<f " not in sheet
            and all(c.data_type != "f" for c in cells))
        for evil in EVIL:
            hit = [c for c in cells if c.value == evil]
            chk("%s: %r kept as quoted text" % (label, evil[:12]),
                hit and all(c.data_type == "s" and c.quotePrefix for c in hit), len(hit))
        chk("%s: Arabic item text round-trips" % label, any(c.value == ARABIC for c in cells))

    # ---- 6. buttons beside the PDF links --------------------------------
    def links(html, pid):
        found = {}
        for m in re.finditer(r'/procurement/pr/%d/(pdf|pr\.xlsx|po\.pdf|po\.xlsx|rfq\.pdf|'
                             r'rfq\.xlsx)(\?[^"]*)?"' % pid, html):
            found.setdefault(m.group(1), []).append((m.group(2) or "").replace("&amp;", "&"))
        return found

    for who, cl in (("admin", adm), ("no-money user", nom)):
        for pid in (disc, split, open_pr):
            f = links(cl.get("/procurement/pr/%d" % pid).get_data(as_text=True), pid)
            pairs = (("pdf", "pr.xlsx"), ("po.pdf", "po.xlsx"), ("rfq.pdf", "rfq.xlsx"))
            chk("%s, PR %d: an Excel link for each PDF link, same parameters" % (who, pid),
                all(sorted(f.get(a, [])) == sorted(f.get(b, [])) for a, b in pairs),
                {k: len(v) for k, v in f.items()})
    f = links(adm.get("/procurement/pr/%d" % split).get_data(as_text=True), split)
    chk("admin sees per-vendor PO Excel links (one per order)",
        all("?po=%d" % p["id"] in f.get("po.xlsx", []) for p in pos), f.get("po.xlsx"))
    f = links(nom.get("/procurement/pr/%d" % disc).get_data(as_text=True), disc)
    chk("no-money user: no PO Excel link, PR Excel link present",
        not f.get("po.xlsx") and f.get("pr.xlsx") == [""], f)
    f = links(adm.get("/procurement/pr/%d" % open_pr).get_data(as_text=True), open_pr)
    chk("RFQ panel offers RFQ Excel for the RFQ",
        f.get("rfq.xlsx") == ["?rfq=%d" % rfq["id"]], f.get("rfq.xlsx"))

    print("\nRESULT:", "ALL PASS" if OK[0] else "FAILURES")
    return OK[0]


def svc_items(app, pr_id):
    with app.app_context():
        from app.approvals import services as svc
        return svc.get_pr(pr_id)["items"]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
