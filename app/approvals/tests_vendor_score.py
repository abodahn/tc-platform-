# -*- coding: utf-8 -*-
"""The MEASURED vendor scorecard, proved on receipts this file actually books.

Nothing is injected. Every figure the scorecard reports here comes from a
requisition raised through create_pr, priced at the pricing gate, signed up the
real ladder, turned into a real purchase order, received through receive_items
(with real rejections and a real over-delivery) and invoiced through add_invoice.
That is the whole point: a score computed from rows a user could never produce
would be exactly as untrustworthy as the number somebody types into the rating
box today.

What is checked:
  1  the four metrics, to the decimal, on a good supplier and a bad one
  2  every metric states its SAMPLE SIZE, and the n is the real count
  3  a supplier with one delivery gets NO number — "not enough history" — and
     the minimum is a parameter, not a constant baked into the arithmetic
  4  a receipt shared by two suppliers scores each on ITS OWN lines
  5  an invoice on a split requisition scores the order it names, not the header
  6  the window actually excludes what falls outside it
  7  proc_vendors.rating is never read, never written, never mixed in
  8  the Vendors screen renders both numbers, each with its sample size, and
     still renders when nothing has been measured
  9  every data-i18n key the screen introduces has real EN / AR / TR ready

    python app/approvals/tests_vendor_score.py
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PASS = []


def ok(label, cond, extra=""):
    PASS.append(bool(cond))
    print(("  PASS  " if cond else "  FAIL  ") + label
          + ((" | " + str(extra)) if extra else ""))


def near(a, b, tol=0.05):
    return a is not None and abs(float(a) - float(b)) <= tol


def _app():
    sys.path.insert(0, str(REPO))
    os.environ.setdefault("TC_ENV", "development")
    import config
    # NOT an environment variable: a stray TC_DB/DATABASE_URL would run these
    # checks against the live beta database.
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(prefix="vsc_"), "score.db")
    os.environ.pop("DATABASE_URL", None)
    from app import create_app
    return create_app()


app = _app()

with app.app_context():
    from app.db import get_db
    from app.approvals import services as svc, constants as C
    from app.approvals import vendor_score as VS
    from app.approvals.vendor_score import I18N

    # The receipt's timestamp is UTC (services._now), so the day the scorecard is
    # asked about must be the same UTC day or the on-time comparison drifts by
    # one across midnight local time. Every call below passes it explicitly.
    TODAY = datetime.now(timezone.utc).date()
    DUE_TODAY = TODAY.isoformat()
    OVERDUE = (TODAY - timedelta(days=3)).isoformat()

    # ---- one real signer per rung, separate people (the dual-role SoD rule) --
    conn = get_db()
    signer = {}
    for stage, roles in C.STAGE_ROLES.items():
        role = sorted(roles)[0]
        uname = "vsc_" + stage
        conn.execute(
            "INSERT OR IGNORE INTO users (username, password_hash, full_name, "
            "role, is_active, created_at) VALUES (?,?,?,?,1,'2026-01-01')",
            (uname, "x", uname, role))
        signer[stage] = {"username": uname, "role": role}
    conn.commit()
    for stage in signer:
        signer[stage]["id"] = conn.execute(
            "SELECT id FROM users WHERE username=?",
            (signer[stage]["username"],)).fetchone()["id"]
    conn.close()
    buyer, store = signer["purchasing"], signer["warehouse"]
    reqr = {"username": "vsc_req", "id": 91}
    seq = [0]

    # Registered suppliers, each with a HUMAN rating typed in. The measured score
    # must never touch these numbers.
    HUMAN = {"VS Alpha": 4.5, "VS Beta": 5.0, "VS Gamma": 2.0,
             "VS Delta": 3.0, "VS Epsilon": 1.5}
    for name, rating in HUMAN.items():
        svc.create_vendor({"name": name, "rating": rating}, buyer)

    def current_stage(pr_id):
        conn = get_db()
        r = conn.execute("SELECT stage FROM pr_steps WHERE pr_id=? AND "
                         "status='pending' ORDER BY seq LIMIT 1", (pr_id,)).fetchone()
        conn.close()
        return r["stage"] if r else None

    def order(title, vendor, lines, due):
        """A requisition the way the UI makes one, walked to a live purchase order.

        `lines` = [(item, unit, qty, unit_price, line_vendor)]. Raised with NO
        price (the requester price lockout), priced at the pricing gate, signed
        up the ladder, PO issued. Returns (pr_id, [line_id, ...])."""
        pr_id, _ = svc.create_pr(
            {"title": title, "department": "Production", "currency": "EGP",
             "vendor": vendor, "req_del_date": due},
            [{"item": it, "unit": u, "qty": q, "unit_price": 0, "vendor": lv}
             for it, u, q, _p, lv in lines], reqr, priced=False)
        conn = get_db()
        lids = [r["id"] for r in conn.execute(
            "SELECT id FROM pr_items WHERE pr_id=? ORDER BY id", (pr_id,)).fetchall()]
        conn.close()
        assert len(lids) == len(lines), (lids, lines)
        okp, msg = svc.price_pr(
            pr_id, {li: p for li, (_i, _u, _q, p, _v) in zip(lids, lines)},
            {"tax_rate": 0}, buyer)
        assert okp, "pricing gate refused: %s" % msg
        total = sum(q * p for _i, _u, q, p, _v in lines)
        for _ in range(15):
            stage = current_stage(pr_id)
            if stage is None:
                break
            okp, msg = svc.act_on_step(pr_id, signer[stage], "approve")
            if not okp and msg == "needs_quotes":
                seq[0] += 1
                for n, mult in (("A", 1.0), ("B", 1.05)):
                    svc.add_quote(pr_id, {"vendor": "VS Quote %s%d" % (n, seq[0]),
                                          "amount": total * mult}, buyer)
                continue
            assert okp, "ladder stuck on %s: %s" % (title, msg)
            if msg == "approved":
                break
        okp, msg = svc.issue_po(pr_id, buyer)
        assert okp, "PO not issued for %s: %s" % (title, msg)
        return pr_id, lids

    def one(title, vendor, qty, price, due):
        pr_id, lids = order(title, vendor, [("Widget", "Pcs", qty, price, vendor)], due)
        return pr_id, lids[0]

    def receive(pr_id, accepted, rejected=None):
        okr, msg = svc.receive_items(pr_id, accepted, store, rejects=rejected or {},
                                     reject_reason="quality" if rejected else None)
        assert okr, "receipt refused: %s" % msg

    def bill(pr_id, no, amount, po_id=None):
        data = {"invoice_no": no, "amount": amount, "tax": 0}
        if po_id:
            data["po_id"] = po_id
        okb, msg = svc.add_invoice(pr_id, data, buyer)
        assert okb, "invoice refused: %s" % msg

    def pos_of(pr_id):
        conn = get_db()
        rows = [dict(r) for r in conn.execute(
            "SELECT id, vendor, grand FROM pr_purchase_orders WHERE pr_id=? ORDER BY id",
            (pr_id,)).fetchall()]
        conn.close()
        return rows

    # =====================================================================
    # The fixture. Four deliveries each for Alpha (faultless) and Beta (not),
    # one for Gamma (thin), and one requisition split between Delta and Epsilon.
    # =====================================================================
    print("--- building the evidence through the real flow " + "-" * 15)

    # --- VS Alpha: on time, in full, no rejections, billed exactly ----------
    for n in range(4):
        pr, li = one("VSC Alpha %d" % n, "VS Alpha", 10, 100.0, DUE_TODAY)
        receive(pr, {li: 10})
        bill(pr, "VSA-%d" % n, 1000.0)

    # --- VS Beta: two late, one short with rejects, one over-delivery -------
    b0, l0 = one("VSC Beta 0", "VS Beta", 10, 100.0, DUE_TODAY)
    receive(b0, {l0: 10})
    bill(b0, "VSB-0", 1100.0)                       # billed 10% above the order

    b1, l1 = one("VSC Beta 1", "VS Beta", 10, 100.0, OVERDUE)
    receive(b1, {l1: 10})                           # arrived after the promise
    bill(b1, "VSB-1", 1000.0)

    b2, l2 = one("VSC Beta 2", "VS Beta", 10, 100.0, OVERDUE)
    receive(b2, {l2: 6}, {l2: 2})                   # 6 accepted, 2 refused, 2 never came
    bill(b2, "VSB-2", 1050.0)

    b3, l3 = one("VSC Beta 3", "VS Beta", 10, 100.0, DUE_TODAY)
    receive(b3, {l3: 12})                           # 10 stocked, 2 into quarantine
    bill(b3, "VSB-3", 1000.0)

    # --- VS Gamma: exactly one of everything -------------------------------
    g0, gl = one("VSC Gamma 0", "VS Gamma", 10, 100.0, DUE_TODAY)
    receive(g0, {gl: 10})
    bill(g0, "VSG-0", 1000.0)

    # --- one requisition, two suppliers, one shared goods receipt ----------
    sp, (sl_d, sl_e) = order("VSC Split", "VS Delta",
                             [("Widget", "Pcs", 5, 100.0, "VS Delta"),
                              ("Gadget", "Pcs", 5, 200.0, "VS Epsilon")], DUE_TODAY)
    receive(sp, {sl_d: 5, sl_e: 5})
    split_pos = {p["vendor"]: p for p in pos_of(sp)}
    ok("a split requisition issues one order per supplier", len(split_pos) == 2, split_pos)
    bill(sp, "VSS-D", 500.0, po_id=split_pos["VS Delta"]["id"])
    bill(sp, "VSS-E", 1200.0, po_id=split_pos["VS Epsilon"]["id"])   # +20%

    cards = VS.scorecards(today=TODAY)
    A = cards.get("vs alpha")
    B = cards.get("vs beta")
    G = cards.get("vs gamma")

    # =====================================================================
    print("\n--- 1. the four metrics, to the decimal " + "-" * 22)
    ok("the faultless supplier is measured at all", A is not None)
    ok("on-time 100.0% for a supplier that never missed a date",
       near(A["on_time"]["pct"], 100.0), A["on_time"])
    ok("quantity accuracy 100.0% when every line was filled",
       near(A["quantity"]["pct"], 100.0), A["quantity"])
    ok("rejection 0.0% when nothing was refused",
       near(A["rejection"]["pct"], 0.0), A["rejection"])
    ok("price variance 0.0% when the invoice equals the order",
       near(A["price_variance"]["pct"], 0.0), A["price_variance"])

    ok("the bad supplier is measured too", B is not None)
    # 2 of 4 receipts arrived after the promised date.
    ok("on-time 50.0% — two of four deliveries missed the date",
       near(B["on_time"]["pct"], 50.0), B["on_time"])
    # lines filled 10/10, 10/10, 6/10, 10/10  ->  (100+100+60+100)/4
    ok("quantity accuracy 90.0% — one line delivered 6 of 10",
       near(B["quantity"]["pct"], 90.0), B["quantity"])
    # refused/delivered per receipt: 0, 0, 2/8, 2/12  ->  (0+0+25+16.667)/4
    ok("rejection 10.4% — refused goods AND the quarantined over-delivery",
       near(B["rejection"]["pct"], 10.4, 0.06), B["rejection"])
    # (+10 + 0 + 5 + 0)/4
    ok("price variance +3.8% — the supplier billed above the order",
       near(B["price_variance"]["pct"], 3.8, 0.06), B["price_variance"])
    ok("a positive price variance means OVER-billed, and is signed",
       B["price_variance"]["pct"] > 0 and A["price_variance"]["pct"] == 0.0)

    ok("the composite ranks the faultless supplier above the bad one",
       A["score"] is not None and B["score"] is not None and A["score"] > B["score"],
       (A["score"], B["score"]))

    # =====================================================================
    print("\n--- 2. every metric states its sample size " + "-" * 19)
    for key, n, what in (("on_time", 4, "receipts"), ("quantity", 4, "order lines"),
                         ("rejection", 4, "receipt lines"), ("price_variance", 4, "orders")):
        ok("Alpha %s carries n=%d %s" % (key, n, what), A[key]["n"] == n, A[key])
        ok("Beta %s carries n=%d %s" % (key, n, what), B[key]["n"] == n, B[key])
    ok("a metric with a number always declares it has enough history",
       all(m["enough"] and m["pct"] is not None
           for m in (A["on_time"], A["quantity"], A["rejection"], A["price_variance"])))
    ok("the card records the window it was computed over",
       A["months"] == 12 and A["since"] == VS.window_start(12, TODAY), A["since"])

    # =====================================================================
    print("\n--- 3. thin history says so instead of printing a number " + "-" * 5)
    ok("the one-delivery supplier appears at all", G is not None)
    ok("...with one observation on every metric",
       [G[k]["n"] for k in ("on_time", "quantity", "rejection", "price_variance")]
       == [1, 1, 1, 1], G)
    ok("...and NO percentage on any of them",
       all(G[k]["pct"] is None and not G[k]["enough"]
           for k in ("on_time", "quantity", "rejection", "price_variance")))
    ok("...so one perfect delivery does not make a 100% supplier",
       G["score"] is None and G["score_n"] == 0, (G["score"], G["score_n"]))
    ok("the default minimum is 3, not 1", VS.MIN_SAMPLE == 3)

    loose = VS.scorecards(today=TODAY, min_sample=1)
    ok("the minimum is a PARAMETER: at min_sample=1 the same vendor scores",
       near(loose["vs gamma"]["on_time"]["pct"], 100.0)
       and loose["vs gamma"]["on_time"]["n"] == 1, loose["vs gamma"]["on_time"])
    ok("...and the card says which minimum produced it",
       loose["vs gamma"]["min_sample"] == 1 and G["min_sample"] == 3)
    strict = VS.scorecards(today=TODAY, min_sample=5)
    ok("raising the minimum above the evidence withdraws the numbers",
       strict["vs alpha"]["score"] is None
       and strict["vs alpha"]["on_time"]["pct"] is None
       and strict["vs alpha"]["on_time"]["n"] == 4)

    # =====================================================================
    print("\n--- 4/5. one receipt, two suppliers, two invoices " + "-" * 12)
    D, E = loose["vs delta"], loose["vs epsilon"]
    ok("each supplier on the shared receipt is judged on its OWN lines",
       D["on_time"]["n"] == 1 and E["on_time"]["n"] == 1
       and D["rejection"]["n"] == 1 and E["rejection"]["n"] == 1)
    ok("the line-level supplier wins over the header supplier",
       E["quantity"]["n"] == 1 and near(E["quantity"]["pct"], 100.0),
       "Epsilon is named on the line only; the header says Delta")
    ok("the invoice scores the ORDER it names: Delta billed to the penny",
       near(D["price_variance"]["pct"], 0.0), D["price_variance"])
    ok("...and Epsilon's 20% over-billing lands on Epsilon, not on the header",
       near(E["price_variance"]["pct"], 20.0), E["price_variance"])

    # =====================================================================
    print("\n--- 6. the window excludes what falls outside it " + "-" * 13)
    future = VS.scorecards(today=TODAY + timedelta(days=400), min_sample=1)
    ok("a 12-month window a year and a bit later measures none of this",
       not future, sorted(future))
    ok("...and widening the window to 24 months finds it again",
       "vs alpha" in VS.scorecards(months=24, today=TODAY + timedelta(days=400),
                                   min_sample=1))
    ok("window_start counts CALENDAR months, not 30-day blocks",
       VS.window_start(12, TODAY) == TODAY.replace(year=TODAY.year - 1).isoformat()
       or TODAY.month == 2 and TODAY.day == 29)

    # =====================================================================
    print("\n--- 7. the typed rating is left completely alone " + "-" * 13)
    conn = get_db()
    stored = {r["name"]: r["rating"] for r in conn.execute(
        "SELECT name, rating FROM proc_vendors").fetchall()}
    conn.close()
    ok("every hand-entered rating survives the measurement untouched",
       all(near(stored.get(n), r) for n, r in HUMAN.items()), stored)
    ok("the measured card carries no 'rating' key to be confused with it",
       "rating" not in A and "rating" not in B)
    ok("the two disagree, which is the whole point",
       stored["VS Beta"] == 5.0 and B["score"] < A["score"],
       "a 5-star supplier measured worse than a 4.5-star one")
    src = (REPO / "app" / "approvals" / "vendor_score.py").read_text(encoding="utf-8")
    ok("the scorer runs no SQL at all against proc_vendors",
       not re.search(r"(FROM|JOIN|UPDATE|INTO)\s+proc_vendors", src, re.I),
       "the docstring may NAME the table; the code may not touch it")

    # =====================================================================
    print("\n--- 8. the Vendors screen shows both, each with its n " + "-" * 8)
    conn = get_db()
    conn.execute("UPDATE users SET lang_pref='en' WHERE id=1")
    conn.commit()
    conn.close()

    client = app.test_client()
    with client.session_transaction() as s:
        s["user"] = {"username": "vsc_purchasing", "role": buyer["role"],
                     "id": buyer["id"]}
        s["uid"] = buyer["id"]
    r = client.get("/procurement/vendors")
    ok("GET /procurement/vendors -> 200", r.status_code == 200, r.status_code)
    live = r.get_data(as_text=True)
    ok("...and renders without a score in context (nothing breaks before the "
       "route passes one)", "VS Alpha" in live and "vsc.none" in live)

    with app.test_request_context("/procurement/vendors"):
        from flask import render_template
        page = render_template("approvals/vendors.html", active="proc_vendors",
                               vendors=svc.list_vendors(active_only=False),
                               scores=cards, score_months=12, can_manage=True,
                               csrf_token="test")
    ok("the screen prints the measured percentage", "100.0%" in page, )
    ok("...next to the number of receipts it rests on", "(4 " in page)
    ok("...and the sample-size label is on the page",
       'data-i18n="vsc.receipts"' in page and 'data-i18n="vsc.orders"' in page)
    ok("the thin-history supplier is named as such, not scored",
       'data-i18n="vsc.thin"' in page)
    ok("the hand-entered rating is still there, labelled as MANUAL",
       "4.5" in page and 'data-i18n="vsc.manual"' in page)
    ok("...and the two are explicitly separated for the reader",
       'data-i18n="vsc.manual_note"' in page)
    ok("all four metrics are on the screen",
       all('data-i18n="vsc.%s"' % k in page
           for k in ("on_time", "quantity", "rejection", "price")))
    ok("the screen explains what each figure is measured against",
       'data-i18n="vsc.legend"' in page)

    # =====================================================================
    # detail.html is owned by another agent right now, so the RFQ-picker change
    # is REPORTED rather than applied — and reported already proven to render,
    # against the real `rfq_vendors` (a list of supplier NAMES, see
    # routes/approvals.py) and the real scorecard dict. Paste replaces the
    # `{% if rfq_vendors %}` block inside the "Issue requests for quotation"
    # form; the route adds `vendor_scores=VSC.scorecards()` beside it.
    print("\n--- 8b. the RFQ picker snippet reported to the orchestrator " + "-" * 1)
    RFQ_SNIPPET = """
{% if rfq_vendors %}
<div class="field"><label data-i18n="proc.rfq.ask">Ask these vendors</label>
  <div class="flex gap wrap center">
    {% for v in rfq_vendors %}
    {% set s = (vendor_scores|default({}, true)).get(v|trim|lower) %}
    <label class="tag" style="cursor:pointer">
      <input type="checkbox" name="vendors" value="{{ v }}" checked> {{ v }}
      {% if s and s.score is not none %}
      <span class="badge {{ 'b-approved' if s.score >= 85 else ('b-warning' if s.score >= 65 else 'b-rejected') }}"
            style="margin-inline-start:6px;font-variant-numeric:tabular-nums"
            >{{ '%.0f'|format(s.score) }}<span style="opacity:.7">/100</span></span>
      <span class="muted" style="font-size:11px">{{ s.score_n }} <span data-i18n="vsc.samples">observations</span></span>
      {% elif s %}
      <span class="muted" style="font-size:11px;margin-inline-start:6px" data-i18n="vsc.thin">Not enough history</span>
      {% else %}
      <span class="muted" style="font-size:11px;margin-inline-start:6px" data-i18n="vsc.none">Nothing measured yet</span>
      {% endif %}
    </label>
    {% endfor %}
  </div>
  <div class="muted" style="font-size:11px;margin-top:4px" data-i18n="vsc.picker_hint"
    >Measured from goods receipts, returns and invoices over the last 12 months — not the manual rating. A supplier with too little history shows no score at all.</div>
</div>
{% endif %}
"""
    picker = app.jinja_env.from_string(RFQ_SNIPPET).render(
        rfq_vendors=["VS Alpha", "VS Beta", "VS Gamma", "VS Unknown"], vendor_scores=cards)
    ok("the reported RFQ-picker snippet renders", "VS Alpha" in picker)
    ok("...scoring the supplier at the moment sourcing picks who to ask",
       "100</span><span style=\"opacity:.7\">/100" in picker
       or ">100<span style=\"opacity:.7\">/100</span>" in picker, picker[:0])
    ok("...with the number of observations behind the score",
       'data-i18n="vsc.samples"' in picker)
    ok("...saying 'not enough history' for the thin supplier",
       'data-i18n="vsc.thin"' in picker)
    ok("...and 'nothing measured yet' for a supplier off the register",
       'data-i18n="vsc.none"' in picker)
    ok("...and it still ticks every vendor, exactly as it does today",
       picker.count('name="vendors"') == 4 and picker.count("checked") == 4)

    # =====================================================================
    print("\n--- 9. trilingual keys ready for the merge " + "-" * 19)
    DICT = {}
    for lang in ("en", "ar", "tr"):
        with open(REPO / "app" / "static" / "i18n" / ("%s.json" % lang),
                  encoding="utf-8") as fh:
            DICT[lang] = json.load(fh)
    # Read from the TEMPLATE SOURCE, not the rendered page: the rendered page
    # also carries base.html's sidebar, and another agent's half-landed nav entry
    # is not this screen's key to answer for.
    tpl = (REPO / "app" / "templates" / "approvals" / "vendors.html").read_text(encoding="utf-8")
    # A macro writes some keys through a variable, so the literal keys come from
    # the source and the macro-produced ones from the rendered page.
    keys = {k for k in re.findall(r'data-i18n="([^"]+)"', tpl + picker) if "{{" not in k}
    keys |= {k for k in re.findall(r'data-i18n="([^"]+)"', page) if k.startswith("vsc.")}
    mine = {k for k in keys if k.startswith("vsc.")}
    ok("the screen renders the new vsc.* keys", len(mine) >= 10, sorted(mine))
    missing = {k for k in mine if k not in I18N and k not in DICT["en"]}
    ok("every new key has EN/AR/TR ready to merge %s" % (sorted(missing) or ""),
       not missing)
    ok("every I18N entry carries a real EN, AR and TR string",
       all(len(v) == 3 and all(str(x).strip() for x in v) for v in I18N.values()))
    ok("the Arabic strings are actually Arabic script",
       all(re.search(r"[؀-ۿ]", v[1]) for v in I18N.values()))
    ok("the Turkish strings are not the English copied over",
       all(v[2].strip() != v[0].strip() for v in I18N.values()))
    reused = {k for k in keys if not k.startswith("vsc.")}
    for lang in ("en", "ar", "tr"):
        gap = {k for k in reused if k not in DICT[lang]}
        ok("every pre-existing key on this screen still resolves in %s %s"
           % (lang, sorted(gap)[:5]), not gap)

print("\n" + "=" * 62)
print("  %d/%d checks passed" % (sum(PASS), len(PASS)))
print("=" * 62)
sys.exit(0 if all(PASS) else 1)
