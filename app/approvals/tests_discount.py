"""Discounts entered by Purchasing: per line, on the whole request, on quotations.

    python -m app.approvals.tests_discount

Proves:
  1. line % and value discounts + a request % / value discount give the exact
     est_cost / total / VAT / grand (one calculation: line net -> subtotal ->
     request discount -> VAT on the net);
  2. clamps: 150% -> 100%, a value above the line -> the line, above the
     subtotal -> the subtotal;
  3. pr.total is the NET, so egp_commitment / rfq_requirement route on it;
  4. a user without proc_purchasing cannot set a discount;
  5. quotes rank on the after-discount figure; the per-item matrix too;
  6. choosing a quote carries its line + quote discounts onto the request;
  7. PR and PO PDFs print the DISC. column and a Discount total row;
  8. a viewer without money visibility sees no discount figure;
  9. a request with no discount totals exactly as before;
 10. receipts, debit notes and the three-way match value units at the
     discounted price; a split PO shares the request discount.
"""
import io
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OK = [True]


def chk(label, cond, extra=""):
    OK[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def run():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ["TC_ENV"] = "development"
    os.environ.pop("DATABASE_URL", None)
    os.environ.pop("TC_DATABASE_URL", None)
    import config
    tmp = tempfile.mkdtemp(prefix="prdisc_")
    config.Config.DB_PATH = os.path.join(tmp, "d.db")
    config.Config.DATABASE_URL = ""
    config.Config.UPLOAD_DIR = Path(tmp) / "uploads"
    from app import create_app
    app = create_app()
    from werkzeug.security import generate_password_hash
    from app.db import get_db
    from app.approvals import services as svc
    from app.approvals import pdf as P

    def q1(sql, *a):
        c = get_db()
        try:
            r = c.execute(sql, a).fetchone()
            return dict(r) if r else None
        finally:
            c.close()

    def exe(sql, *a):
        c = get_db()
        try:
            c.execute(sql, a)
            c.commit()
        finally:
            c.close()

    def items_of(pid):
        return {it["item"]: it for it in svc.get_pr(pid)["items"]}

    with app.app_context():
        c = get_db()
        users = {}
        for name, role in (("req", "normal_user"), ("other", "normal_user"),
                           ("buyer", "purchasing_manager")):
            c.execute("INSERT INTO users (username,password_hash,full_name,role,is_active,"
                      "created_at) VALUES (?,?,?,?,1,'2026-01-01')",
                      ("disc_" + name, generate_password_hash("x"), "disc_" + name, role))
            users[name] = dict(c.execute("SELECT * FROM users WHERE username=?",
                                         ("disc_" + name,)).fetchone())
        users["admin"] = dict(c.execute(
            "SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        c.commit()
        c.close()
        buyer = users["buyer"]

        # ---- 9. no discount: exactly the old arithmetic ---------------------
        plain, _ = svc.create_pr({"title": "Plain", "department": "Maintenance"},
                                 [{"item": "P1", "qty": 3, "unit_price": 0},
                                  {"item": "P2", "qty": 7, "unit_price": 0}],
                                 users["req"], submit=False, priced=False)
        it = items_of(plain)
        ok, msg = svc.price_pr(plain, {it["P1"]["id"]: 33.333, it["P2"]["id"]: 12.5},
                               {"tax_rate": "14"}, buyer)
        it = items_of(plain)
        prp = q1("SELECT * FROM pr_requests WHERE id=?", plain)
        old_est = [round(3 * 33.333, 2), round(7 * 12.5, 2)]
        old_total = round(sum(old_est), 2)
        old_tax = round(old_total * 14 / 100.0, 2)
        a = svc.pr_amounts(prp)
        chk("no discount: est_cost and total are the old figures (%s)" % prp["total"],
            ok and [it["P1"]["est_cost"], it["P2"]["est_cost"]] == old_est
            and prp["total"] == old_total, msg)
        chk("no discount: pr_amounts keeps its old keys and values",
            (a["subtotal"], a["tax_rate"], a["tax"], a["grand"])
            == (old_total, 14.0, old_tax, round(old_total + old_tax, 2))
            and a["discount_total"] == 0 and a["gross"] == old_total, a)
        chk("no discount: discount columns stay empty",
            all(x["disc_amount"] is None and x["net_price"] is None for x in it.values())
            and prp["disc_amount"] is None and prp["line_disc"] is None)

        # ---- 1. line % + line value + request % ----------------------------
        pid, _ = svc.create_pr({"title": "Discounted", "department": "Maintenance"},
                               [{"item": "L1", "qty": 10, "unit_price": 0},
                                {"item": "L2", "qty": 4, "unit_price": 0},
                                {"item": "L3", "qty": 2, "unit_price": 0}],
                               users["req"], submit=False, priced=False)
        it = items_of(pid)
        L1, L2, L3 = it["L1"]["id"], it["L2"]["id"], it["L3"]["id"]
        ok, msg = svc.price_pr(pid, {L1: 100, L2: 250, L3: 50},
                               {"tax_rate": "14", "disc_type": "pct", "disc_value": "10"},
                               buyer, discounts={L1: ("pct", "10"), L2: ("val", "150")})
        it = items_of(pid)
        pr = q1("SELECT * FROM pr_requests WHERE id=?", pid)
        a = svc.pr_amounts(pr)
        chk("priced with discounts", ok, msg)
        chk("line nets 900 / 850 / 100, unit price stays the list price",
            (it["L1"]["est_cost"], it["L2"]["est_cost"], it["L3"]["est_cost"]) == (900, 850, 100)
            and it["L1"]["unit_price"] == 100 and it["L2"]["unit_price"] == 250,
            [(x["est_cost"], x["unit_price"]) for x in it.values()])
        chk("line discounts 100 (10%) and 150 (value)",
            it["L1"]["disc_amount"] == 100 and it["L1"]["disc_type"] == "pct"
            and it["L2"]["disc_amount"] == 150 and it["L2"]["disc_type"] == "val"
            and it["L3"]["disc_amount"] is None)
        chk("request 10%% on 1,850 = 185 -> total (net) 1,665 (%s)" % pr["total"],
            pr["total"] == 1665 and pr["disc_amount"] == 185 and pr["line_disc"] == 250)
        chk("pr_amounts: gross 2,100, discount 435, VAT 233.10, grand 1,898.10 (%s)" % a,
            (a["gross"], a["line_discount"], a["header_discount"], a["discount_total"],
             a["subtotal"], a["net"], a["tax"], a["grand"])
            == (2100, 250, 185, 435, 1665, 1665, 233.1, 1898.1))
        chk("net unit prices carry the request share (81 / 191.25 / 45)",
            (it["L1"]["net_price"], it["L2"]["net_price"], it["L3"]["net_price"])
            == (81.0, 191.25, 45.0),
            [x["net_price"] for x in it.values()])
        b = svc.price_breakdown([{"qty": 10, "unit_price": 100, "disc_type": "pct",
                                  "disc_value": 10}], "val", 50, 14)
        chk("price_breakdown is the one formula (900 - 50 = 850, +14% = 969)",
            (b["net"], b["tax"], b["grand"]) == (850, 119, 969), b)

        # ---- request value discount ----------------------------------------
        ok, msg = svc.price_pr(pid, {}, {"disc_type": "val", "disc_value": "65"}, buyer)
        pr = q1("SELECT * FROM pr_requests WHERE id=?", pid)
        a = svc.pr_amounts(pr)
        chk("request value 65 -> net 1,785, VAT 249.90, grand 2,034.90 (%s)" % a,
            ok and (a["net"], a["tax"], a["grand"], a["header_discount"])
            == (1785, 249.9, 2034.9, 65))
        chk("line discounts kept when only the request discount is posted",
            items_of(pid)["L1"]["disc_amount"] == 100)

        # ---- 3. routing sees the NET ---------------------------------------
        _c = get_db()
        rq = svc.rfq_requirement(_c, pr)
        _c.close()
        chk("egp_commitment = discounted grand 2,034.90 (%s)" % svc.egp_commitment(pr),
            svc.egp_commitment(pr) == 2034.9)
        chk("rfq_requirement routes on the discounted value (%s)" % rq["value"],
            rq["value"] == 2034.9)

        # ---- 2. clamps -----------------------------------------------------
        cl_id, _ = svc.create_pr({"title": "Clamp", "department": "Maintenance"},
                                 [{"item": "C1", "qty": 2, "unit_price": 0},
                                  {"item": "C2", "qty": 1, "unit_price": 0},
                                  {"item": "C3", "qty": 1, "unit_price": 0}],
                                 users["req"], submit=False, priced=False)
        it = items_of(cl_id)
        C1, C2, C3 = it["C1"]["id"], it["C2"]["id"], it["C3"]["id"]
        ok, msg = svc.price_pr(cl_id, {C1: 50, C2: 1000, C3: 300},
                               {"tax_rate": "0", "disc_type": "val", "disc_value": "99999"},
                               buyer, discounts={C1: ("pct", "150"), C2: ("val", "5000")})
        it = items_of(cl_id)
        pr = q1("SELECT * FROM pr_requests WHERE id=?", cl_id)
        chk("150% is clamped to 100% (line nets to 0)",
            it["C1"]["disc_value"] == 100 and it["C1"]["disc_amount"] == 100
            and it["C1"]["est_cost"] == 0)
        chk("a value above the line is clamped to the line (1,000)",
            it["C2"]["disc_value"] == 1000 and it["C2"]["est_cost"] == 0)
        chk("a request value above the subtotal is clamped to it (300) -> net 0",
            pr["disc_amount"] == 300 and pr["total"] == 0, (pr["disc_amount"], pr["total"]))
        chk("a fully discounted line is worth 0, not its gross",
            svc._amount(dict(it["C2"])) == 0)
        ok, msg = svc.price_pr(cl_id, {}, {"disc_type": "", "disc_value": ""}, buyer,
                               discounts={C1: ("pct", ""), C2: ("val", "")})
        pr = q1("SELECT * FROM pr_requests WHERE id=?", cl_id)
        it = items_of(cl_id)
        chk("clearing every discount restores the plain total 1,400",
            pr["total"] == 1400 and pr["disc_amount"] is None
            and all(x["net_price"] is None and x["disc_amount"] is None for x in it.values()))

        # ---- 10. receipt / debit note / three-way match at the net price ----
        svc.price_pr(pid, {}, {"disc_type": "pct", "disc_value": "10"}, buyer)
        exe("UPDATE pr_requests SET status='po_issued', po_no='PO-T-1' WHERE id=?", pid)
        ok, msg = svc.receive_items(pid, {L1: 10, L2: 3}, buyer, post_stock=False,
                                    rejects={L2: 1}, reject_reason="Damaged")
        m = svc.three_way_match(pid)
        chk("received value = 10 x 81 + 3 x 191.25 = 1,383.75 (%s)" % (m or {}).get("received_value"),
            ok and m and m["received_value"] == 1383.75, msg)
        ret = q1("SELECT net FROM pr_returns WHERE pr_id=?", pid)
        chk("the debit note values the rejected unit at 191.25 (%s)" % ret,
            ret and ret["net"] == 191.25)

        # split PO: the request discount is shared by net line value
        sp, _ = svc.create_pr({"title": "Split", "department": "Maintenance"},
                              [{"item": "S1", "qty": 1, "unit_price": 0},
                               {"item": "S2", "qty": 1, "unit_price": 0}],
                              users["req"], submit=False, priced=False)
        it = items_of(sp)
        svc.price_pr(sp, {it["S1"]["id"]: 300, it["S2"]["id"]: 100},
                     {"tax_rate": "0", "disc_type": "val", "disc_value": "40"}, buyer,
                     vendors={it["S1"]["id"]: "Vendor A", it["S2"]["id"]: "Vendor B"})
        c = get_db()
        try:
            prs = c.execute("SELECT * FROM pr_requests WHERE id=?", (sp,)).fetchone()
            rows = svc._issue_po_rows(c, sp, prs, "PO-T-2", buyer)
            c.commit()
        finally:
            c.close()
        chk("split POs share the 40 discount 30 / 10 (subtotals %s)"
            % [r["subtotal"] for r in rows],
            sorted(r["subtotal"] for r in rows) == [90, 270]
            and round(sum(r["subtotal"] for r in rows), 2) == 360)

        # ---- 5./6. quotations ------------------------------------------------
        qp, _ = svc.create_pr({"title": "Quotes", "department": "Maintenance"},
                              [{"item": "Q1", "qty": 10, "unit_price": 0},
                               {"item": "Q2", "qty": 5, "unit_price": 0}],
                              users["req"], submit=False, priced=False)
        it = items_of(qp)
        Q1, Q2 = it["Q1"]["id"], it["Q2"]["id"]
        b64 = "JVBERi0xLjQK"
        okx, w1 = svc.add_quote(qp, {"vendor": "Plain Co", "payment_terms": "30 days",
                                     "vat_applies": "1", "lines": {Q1: "100", Q2: "90"}},
                                buyer, filename="x.pdf", content_type="application/pdf",
                                content_b64=b64)
        oky, w2 = svc.add_quote(qp, {"vendor": "Discount Co", "payment_terms": "30 days",
                                     "vat_applies": "1", "lines": {Q1: "120", Q2: "100"},
                                     "line_discs": {Q1: ("pct", "20"), Q2: ("val", "50")},
                                     "disc_type": "pct", "disc_value": "5"},
                                buyer, filename="y.pdf", content_type="application/pdf",
                                content_b64=b64)
        quotes = svc.get_pr(qp)["quotes"]
        y = next(q for q in quotes if q["vendor"] == "Discount Co")
        chk("quote stored after discounts: 960 + 450 = 1,410 - 5%% = 1,339.50 (%s)" % y["amount"],
            okx and oky and y["amount"] == 1339.5 and y["disc_amount"] == 70.5, (w1, w2))
        chk("the discounted quote ranks first (%s)" % [q["vendor"] for q in quotes],
            quotes[0]["vendor"] == "Discount Co")
        cmp_ = svc.quote_comparison(quotes)
        chk("lowest = 1,527.03 after VAT, saving 125.97 (%s)" % cmp_,
            cmp_["lowest"] == 1527.03 and cmp_["saving"] == 125.97)
        mx = svc.quote_matrix(svc.get_pr(qp)["items"], quotes)
        row1 = next(r for r in mx["rows"] if r["item"]["id"] == Q1)
        best = [q["vendor"] for q, cell in zip(mx["quotes"], row1["cells"]) if cell["best"]]
        chk("matrix: 120 less 20%% (net 96) beats 100 (%s)" % best, best == ["Discount Co"])

        okc, _m = svc.choose_quote(qp, y["id"], buyer)
        oka, ma = svc.apply_chosen_quote(qp, y["id"], buyer)
        it = items_of(qp)
        pr = q1("SELECT * FROM pr_requests WHERE id=?", qp)
        chk("choosing the quote carries line discounts (20% / 50)",
            okc and oka and it["Q1"]["disc_type"] == "pct" and it["Q1"]["disc_value"] == 20
            and it["Q2"]["disc_type"] == "val" and it["Q2"]["disc_value"] == 50
            and it["Q1"]["unit_price"] == 120, ma)
        chk("...and the quote discount: request 5%%, total 1,339.50, VAT 14 (%s)" % pr["total"],
            pr["disc_type"] == "pct" and pr["disc_value"] == 5 and pr["total"] == 1339.5
            and pr["tax_rate"] == 14 and svc.pr_amounts(pr)["grand"] == 1527.03)

        # editing a quote's discount re-nets it
        okx2, _w = svc.update_quote(next(q for q in quotes if q["vendor"] == "Plain Co")["id"],
                                    {"disc_type": "val", "disc_value": "100"}, user=buyer)
        xq = q1("SELECT * FROM pr_quotes WHERE vendor='Plain Co'")
        chk("a quote discount added by edit: 1,450 - 100 = 1,350 (%s)" % xq["amount"],
            okx2 and xq["amount"] == 1350 and xq["disc_amount"] == 100)

        # a partial quote's % discount covers only the lines it priced
        pq, _ = svc.create_pr({"title": "Partial", "department": "Maintenance"},
                              [{"item": "PA", "qty": 10, "unit_price": 0},
                               {"item": "PB", "qty": 1, "unit_price": 0}],
                              users["req"], submit=False, priced=False)
        it = items_of(pq)
        PA, PB = it["PA"]["id"], it["PB"]["id"]
        svc.price_pr(pq, {PA: 0, PB: 500}, {"tax_rate": "0"}, buyer)
        okp, wp = svc.add_quote(pq, {"vendor": "Part Co", "payment_terms": "30 days",
                                     "vat_applies": "0", "lines": {PA: "100"},
                                     "disc_type": "pct", "disc_value": "10"},
                                buyer, filename="p.pdf", content_type="application/pdf",
                                content_b64=b64)
        pqid = svc.get_pr(pq)["quotes"][0]["id"]
        svc.choose_quote(pq, pqid, buyer)
        svc.apply_chosen_quote(pq, pqid, buyer)
        prq = q1("SELECT * FROM pr_requests WHERE id=?", pq)
        chk("a partial quote's 10%% stays on its lines: 900 + 500 = 1,400 (%s)" % prq["total"],
            okp and prq["total"] == 1400, wp)

        # the per-item matrix counts the quote-level discount too
        mq, _ = svc.create_pr({"title": "Matrix", "department": "Maintenance"},
                              [{"item": "M1", "qty": 2, "unit_price": 0}],
                              users["req"], submit=False, priced=False)
        M1 = items_of(mq)["M1"]["id"]
        for vend, price, extra in (("X Co", "100", {"disc_type": "pct", "disc_value": "10"}),
                                   ("Y Co", "95", {})):
            svc.add_quote(mq, dict({"vendor": vend, "payment_terms": "30 days",
                                    "vat_applies": "0", "lines": {M1: price}}, **extra),
                          buyer, filename="m.pdf", content_type="application/pdf",
                          content_b64=b64)
        mquotes = svc.get_pr(mq)["quotes"]
        mx = svc.quote_matrix(svc.get_pr(mq)["items"], mquotes)
        best = [q["vendor"] for q, cell in zip(mx["quotes"], mx["rows"][0]["cells"]) if cell["best"]]
        chk("matrix: 100 less a 10%% quote discount (90) beats 95 (%s)" % best,
            best == ["X Co"] and mquotes[0]["vendor"] == "X Co")

        # a value discount clamped by a shrunk quantity comes back with it
        rq, _ = svc.create_pr({"title": "Ratchet", "department": "Maintenance"},
                              [{"item": "R1", "qty": 10, "unit_price": 0},
                               {"item": "R2", "qty": 1, "unit_price": 0}],
                              users["req"], submit=False, priced=False)
        it = items_of(rq)
        R1, R2 = it["R1"]["id"], it["R2"]["id"]
        svc.price_pr(rq, {R1: 100, R2: 1000}, {"tax_rate": "0", "disc_type": "val",
                                               "disc_value": "500"}, buyer,
                     discounts={R1: ("val", "300")})
        t0 = q1("SELECT total FROM pr_requests WHERE id=?", rq)["total"]
        for q_r1, q_r2 in ((2, 0), (10, 1)):
            exe("UPDATE pr_items SET qty=? WHERE id=?", q_r1, R1)
            exe("UPDATE pr_items SET qty=? WHERE id=?", q_r2, R2)
            c = get_db()
            try:
                svc._reprice(c, rq)
                c.commit()
            finally:
                c.close()
        rr = q1("SELECT total, disc_value FROM pr_requests WHERE id=?", rq)
        chk("qty cut and restored: total back to 1,200 (%s -> %s), discounts as entered"
            % (t0, rr["total"]), t0 == 1200 and rr["total"] == 1200 and rr["disc_value"] == 500
            and items_of(rq)["R1"]["disc_value"] == 300)


        # ---- 7. PDFs -------------------------------------------------------
        from pypdf import PdfReader

        def text(raw):
            return " ".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(raw)).pages)
        bundle = svc.get_pr(pid)
        t_pr = text(P.pr_pdf(bundle, money=True))
        t_po = text(P.po_pdf(bundle))
        chk("PR PDF: DISC. column with 100.00 and 150.00",
            "DISC." in t_pr and "100.00" in t_pr and "150.00" in t_pr)
        chk("PR PDF: Discount row -435.00 and grand 1,898.10",
            "Discount" in t_pr and "-435.00" in t_pr and "1,898.10" in t_pr)
        chk("PR PDF stays on one page", len(PdfReader(io.BytesIO(P.pr_pdf(bundle))).pages) == 1)
        chk("PO PDF: DISC. column, Discount row and grand 1,898.10",
            "DISC." in t_po and "-435.00" in t_po and "1,898.10" in t_po
            and "Subtotal" in t_po and "2,100.00" in t_po)
        chk("PO PDF: line VAT/After VAT carry the 10% total discount (113.40 / 923.40)",
            "113.40" in t_po and "923.40" in t_po and "126.00" not in t_po)
        t_plain = text(P.po_pdf(svc.get_pr(plain))) + text(P.pr_pdf(svc.get_pr(plain)))
        chk("no discount: no DISC. column on the PR or PO PDF", "DISC." not in t_plain)
        t_hidden = text(P.pr_pdf(bundle, money=False))

        chk("PR PDF without money prints no discount", "435.00" not in t_hidden
            and "150.00" not in t_hidden)

        # ---- 4./8. routes and visibility -----------------------------------
        vis, _ = svc.create_pr({"title": "Visibility", "department": "Maintenance"},
                               [{"item": "V1", "qty": 1, "unit_price": 0}],
                               users["req"], submit=True, priced=False)
        v1 = items_of(vis)["V1"]["id"]
        ok, msg = svc.price_pr(vis, {v1: 9876}, {"tax_rate": "0"}, buyer,
                               discounts={v1: ("val", "432.1")})
        chk("visibility request priced with a 432.10 discount", ok, msg)

    def client(uid):
        cl = app.test_client()
        with cl.session_transaction() as s:
            s["uid"] = uid
            s["ep"] = 0
        return cl

    adm = client(users["admin"]["id"])
    html = adm.get("/procurement/pr/%d" % vis).get_data(as_text=True)
    chk("purchasing/admin sees the discount and the net (432.10 / 9,443.90)",
        "432.10" in html and "9,443.90" in html and 'data-i18n="proc.disc.label"' in html)
    r = client(users["other"]["id"]).get("/procurement/pr/%d" % vis)
    h2 = r.get_data(as_text=True)
    chk("a viewer without money visibility sees no discount figure (%s)" % r.status_code,
        r.status_code in (200, 302, 403) and "432.10" not in h2 and "9,443.90" not in h2
        and "9,876" not in h2)
    html = adm.get("/procurement/pr/%d" % pid).get_data(as_text=True)
    chk("line items panel: discount (10%) and the Net cost column",
        "(10%)" in html and 'data-i18n="proc.disc.net_cost"' in html)

    # a requester cannot set a discount through the pricing route
    req = client(users["req"]["id"])
    req.get("/procurement/pr/%d" % vis)
    with req.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = req.post("/procurement/pr/%d/price" % vis,
                 data={"_csrf": tok, "price_%d" % v1: "9876", "disc_%d" % v1: "50",
                       "disctype_%d" % v1: "pct", "disc_type": "pct", "disc_value": "50"})
    with app.app_context():
        it = items_of(vis)["V1"]
        prv = q1("SELECT * FROM pr_requests WHERE id=?", vis)
    chk("the requester's discount post is refused (%s) and changes nothing" % r.status_code,
        r.status_code in (302, 403) and it["disc_value"] == 432.1 and prv["disc_value"] is None)

    # purchasing sets one through the route
    adm.get("/procurement/pr/%d" % vis)
    with adm.session_transaction() as s:
        tok = s.get("_csrf_token")
    r = adm.post("/procurement/pr/%d/price" % vis,
                 data={"_csrf": tok, "price_%d" % v1: "9876", "disc_%d" % v1: "10",
                       "disctype_%d" % v1: "pct", "disc_type": "val", "disc_value": "88.4"})
    with app.app_context():
        prv = q1("SELECT * FROM pr_requests WHERE id=?", vis)
    # 9,876 - 10% (987.60) = 8,888.40 - 88.40 = 8,800
    chk("purchasing's discount post lands: net 8,800 (%s, %s)" % (r.status_code, prv["total"]),
        r.status_code in (302, 303) and prv["total"] == 8800)

    print("\nALL PASS" if OK[0] else "\nSOME FAILED")
    return OK[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
