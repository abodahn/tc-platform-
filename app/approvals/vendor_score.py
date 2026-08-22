# -*- coding: utf-8 -*-
"""
TC Platform — the MEASURED vendor scorecard.

proc_vendors.rating is a human judgement somebody types into a box. This module
never touches it. It computes the other half — what the supplier actually did —
from documents the platform already books:

    pr_grn.lines_json   every receipt EVENT: when it arrived, and per line how
                        much was accepted, how much arrived over the order
                        (`quarantined`) and how much was refused (`rejected`).
    pr_requests         `req_del_date` — the date the goods were promised.
    pr_items            `qty` vs `received_qty` — ordered vs actually delivered,
                        and `vendor`, which is the SUPPLIER OF THE LINE. A
                        requisition may buy from three suppliers, so the header
                        vendor is only the fallback for a line that names none.
    pr_purchase_orders  what was committed, per supplier, per document.
    pr_invoices         what was billed against it.

Nothing here reads pr_returns or pr_grn_quarantine directly: both are DERIVED
from the same GRN lines this reads (see services._record_grn / _record_return),
so counting them again would double-count the same rejection and would lose the
per-line denominator a rate needs.

EVERY metric carries its sample size, and a metric below MIN_SAMPLE returns no
number at all. A vendor with one delivery is not a 100% on-time vendor, and a
percentage printed without its n is worse than printing nothing.

Ratios are averaged per line/per order, never value- or unit-weighted: a
requisition mixes Kg with Pcs and EGP with USD, and summing across either gives
a number that means nothing.
"""
import calendar
import json
from datetime import date

from app.db import get_db
from app.approvals.constants import vendor_key

# Below this many observations a metric is "not enough history" instead of a
# percentage. Three is the smallest n where one bad delivery is not the whole
# story; it is a judgement, not a statistic, which is why it is a parameter.
MIN_SAMPLE = 3

DEFAULT_MONTHS = 12


def window_start(months=DEFAULT_MONTHS, today=None):
    """First day counted, `months` calendar months back from today (YYYY-MM-DD).

    Calendar months, not months*30: a 12-month window that drifts 5 days a year
    quietly changes what "the last year" means every time the page is opened.
    """
    t = today or date.today()
    m = t.month - int(months)
    y, m = t.year + (m - 1) // 12, (m - 1) % 12 + 1
    return date(y, m, min(t.day, calendar.monthrange(y, m)[1])).isoformat()


def _metric(values, min_sample):
    """One metric from a list of per-observation ratios/percentages."""
    n = len(values)
    if n < min_sample:
        return {"pct": None, "n": n, "enough": False}
    return {"pct": round(sum(values) / n, 1), "n": n, "enough": True}


def _rows(conn, sql, *args):
    """Query that tolerates a database predating one of these tables.

    Same shape as services._pos_for: a failed statement on PostgreSQL poisons the
    transaction, so roll it back before the next one runs."""
    try:
        return conn.execute(sql, args).fetchall()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []


def _delivery_metrics(conn, start, buckets):
    """On-time (per receipt event) and rejection (per receipt line), from GRNs."""
    grns = _rows(conn, """
        SELECT g.id, g.pr_id, g.lines_json, g.created_at,
               p.req_del_date, p.vendor AS head_vendor
          FROM pr_grn g JOIN pr_requests p ON p.id = g.pr_id
         WHERE COALESCE(g.created_at,'') >= ?""", start)
    if not grns:
        return
    line_vendor = {r["id"]: r["vendor"] for r in _rows(conn, """
        SELECT DISTINCT i.id, i.vendor
          FROM pr_items i JOIN pr_grn g ON g.pr_id = i.pr_id
         WHERE COALESCE(g.created_at,'') >= ?""", start)}

    for g in grns:
        try:
            lines = json.loads(g["lines_json"] or "[]")
        except (TypeError, ValueError):
            continue
        arrived = (g["created_at"] or "")[:10]
        promised = (g["req_del_date"] or "").strip()
        # Which suppliers moved goods on THIS receipt. One GRN can carry lines
        # from two suppliers on a split requisition, and each is judged on its
        # own lines — not on whether the truck as a whole was late.
        on_this_grn = set()
        for l in lines:
            acc = float(l.get("accepted") or 0)
            over = float(l.get("quarantined") or 0)
            rej = float(l.get("rejected") or 0)
            delivered = acc + over + rej
            if delivered <= 0:
                continue
            name = (line_vendor.get(l.get("item_id")) or g["head_vendor"] or "").strip()
            if not name:
                continue          # a receipt against no named supplier scores nobody
            b = buckets(name)
            # Refused = arrived and did NOT go into stock: rejected on the door
            # (returned with a debit note) plus the over-delivery held in
            # quarantine. Both are the supplier sending the wrong quantity.
            b["reject"].append(100.0 * (over + rej) / delivered)
            on_this_grn.add(name)
        # A promise nobody recorded cannot be scored, so the whole event drops
        # out of the on-time sample rather than counting as a pass.
        if not promised:
            continue
        for name in on_this_grn:
            buckets(name)["on_time"].append(100.0 if arrived and arrived <= promised else 0.0)


def _fill_metrics(conn, start, today, buckets):
    """Quantity accuracy per ORDER LINE that is already past its promised date.

    The gate is the promised date, not the request's status: a line still sitting
    at 'partially_received' two months after it was due is exactly the line a
    fill rate exists to expose, and gating on 'received' would only ever measure
    orders that completed — every vendor scores 100%."""
    for r in _rows(conn, """
        SELECT i.qty, i.received_qty, i.vendor, p.vendor AS head_vendor
          FROM pr_items i JOIN pr_requests p ON p.id = i.pr_id
         WHERE COALESCE(p.req_del_date,'') >= ? AND COALESCE(p.req_del_date,'') <= ?
           AND p.status IN ('po_issued','partially_received','received','closed')""",
            start, today):
        ordered = float(r["qty"] or 0)
        if ordered <= 0:
            continue
        name = ((r["vendor"] or r["head_vendor"]) or "").strip()
        if not name:
            continue
        got = min(float(r["received_qty"] or 0), ordered)
        buckets(name)["fill"].append(100.0 * got / ordered)


def _price_metrics(conn, start, buckets):
    """Signed invoiced-vs-ordered variance, one observation per PURCHASE ORDER.

    Per order, not per invoice: a supplier that bills an order in four
    instalments is not four data points, and the partial instalments would each
    read as a huge negative variance until the last one landed.

    Read from the PO REGISTER, so an order issued before that register existed
    (pr_requests.po_no alone — see services._pos_for) simply does not score here.
    A missing observation is honest; synthesising one is not."""
    pos = _rows(conn, """
        SELECT o.id, o.pr_id, o.vendor, o.grand
          FROM pr_purchase_orders o JOIN pr_requests p ON p.id = o.pr_id
         WHERE COALESCE(o.issued_at, p.request_date, '') >= ?""", start)
    if not pos:
        return
    billed, per_pr = {}, {}
    # DISTINCT on the invoice id: a requisition split across three suppliers has
    # three register rows, and the join would otherwise count each invoice thrice.
    for iv in _rows(conn, """
        SELECT DISTINCT v.id, v.pr_id, v.po_id, v.amount, v.tax
          FROM pr_invoices v
          JOIN pr_purchase_orders o ON o.pr_id = v.pr_id
          JOIN pr_requests p ON p.id = o.pr_id
         WHERE COALESCE(o.issued_at, p.request_date, '') >= ?""", start):
        gross = float(iv["amount"] or 0) + float(iv["tax"] or 0)
        if iv["po_id"]:
            billed[iv["po_id"]] = billed.get(iv["po_id"], 0.0) + gross
        else:
            # po_id is NULL on every invoice booked before the column existed and
            # on every single-supplier request, where the request IS the order.
            per_pr[iv["pr_id"]] = per_pr.get(iv["pr_id"], 0.0) + gross
    only_po = {}
    for p in pos:
        only_po[p["pr_id"]] = None if p["pr_id"] in only_po else p["id"]
    for pr_id, po_id in only_po.items():
        if po_id and per_pr.get(pr_id):
            billed[po_id] = billed.get(po_id, 0.0) + per_pr[pr_id]

    for p in pos:
        ordered = float(p["grand"] or 0)
        invoiced = billed.get(p["id"])
        name = (p["vendor"] or "").strip()
        if ordered <= 0 or not invoiced or not name:
            continue              # an order nobody has billed yet says nothing
        buckets(name)["price"].append(100.0 * (invoiced - ordered) / ordered)


def scorecards(months=DEFAULT_MONTHS, min_sample=MIN_SAMPLE, conn=None, today=None):
    """Measured scorecard per supplier, keyed by constants.vendor_key(name).

    Pass a live `conn` to borrow it (the Vendors screen does), so rendering stays
    one connection instead of a second round trip on PostgreSQL.
    """
    own = conn is None
    conn = conn or get_db()
    start = window_start(months, today)
    today_s = (today or date.today()).isoformat()
    raw, display = {}, {}

    def bucket(name):
        k = vendor_key(name)
        display.setdefault(k, name.strip())
        return raw.setdefault(k, {"on_time": [], "fill": [], "reject": [], "price": []})

    try:
        _delivery_metrics(conn, start, bucket)
        _fill_metrics(conn, start, today_s, bucket)
        _price_metrics(conn, start, bucket)
    finally:
        if own:
            conn.close()

    out = {}
    for k, b in raw.items():
        on_time = _metric(b["on_time"], min_sample)
        quantity = _metric(b["fill"], min_sample)
        rejection = _metric(b["reject"], min_sample)
        price = _metric(b["price"], min_sample)
        # The composite only exists to rank suppliers at a glance. Each metric
        # contributes as "how good", so price variance folds in by its SIZE —
        # billing 8% under the order is as wrong as billing 8% over.
        goods = []
        if on_time["enough"]:
            goods.append(on_time["pct"])
        if quantity["enough"]:
            goods.append(quantity["pct"])
        if rejection["enough"]:
            goods.append(100.0 - rejection["pct"])
        if price["enough"]:
            goods.append(max(0.0, 100.0 - abs(price["pct"])))
        out[k] = {
            "vendor": display[k], "key": k,
            "on_time": on_time, "quantity": quantity,
            "rejection": rejection, "price_variance": price,
            "score": round(sum(goods) / len(goods), 1) if len(goods) >= 2 else None,
            "score_n": len(goods),
            "months": int(months), "min_sample": int(min_sample),
            "since": start,
        }
    return out


def card_for(name, **kw):
    """One supplier's card, or None when nothing about it has been measured."""
    return scorecards(**kw).get(vendor_key(name))


# Every data-i18n key the scorecard introduces, with real EN / AR / TR.
# app/static/i18n/** is owned by the orchestrator, so this dict is the source it
# merges from — a key missing from en.json renders as the literal string.
I18N = {
    "vsc.title": ("Measured performance", "الأداء المُقاس", "Ölçülen performans"),
    "vsc.measured": ("Measured score", "التقييم المُقاس", "Ölçülen puan"),
    "vsc.manual": ("Manual rating", "التقييم اليدوي", "Manuel değerlendirme"),
    "vsc.manual_note": ("Manual rating is a person's judgement typed on this screen. The measured score is computed from goods receipts, returns and invoices — the two are shown side by side and are never mixed.",
                        "التقييم اليدوي هو حكم شخصي يُدخل من هذه الشاشة. أما التقييم المُقاس فيُحتسب من إشعارات استلام البضائع والمرتجعات والفواتير — ويُعرض الاثنان جنباً إلى جنب ولا يُدمجان أبداً.",
                        "Manuel değerlendirme, bu ekranda bir kişinin girdiği kanaattir. Ölçülen puan ise mal kabul belgeleri, iadeler ve faturalardan hesaplanır — ikisi yan yana gösterilir ve asla birbirine karıştırılmaz."),
    "vsc.on_time": ("On-time delivery", "التسليم في الموعد", "Zamanında teslimat"),
    "vsc.quantity": ("Quantity accuracy", "دقة الكمية", "Miktar doğruluğu"),
    "vsc.rejection": ("Rejection rate", "نسبة الرفض", "Ret oranı"),
    "vsc.price": ("Price variance", "انحراف السعر", "Fiyat sapması"),
    "vsc.thin": ("Not enough history", "لا يوجد سجل كافٍ", "Yeterli geçmiş yok"),
    "vsc.none": ("Nothing measured yet", "لم يُقَس شيء بعد", "Henüz ölçüm yok"),
    "vsc.picker_hint": ("Measured from goods receipts, returns and invoices over the last 12 months — not the manual rating. A supplier with too little history shows no score at all.",
                        "محتسب من إشعارات استلام البضائع والمرتجعات والفواتير خلال آخر 12 شهراً — وليس من التقييم اليدوي. والمورد الذي ليس له سجل كافٍ لا يظهر له أي تقييم.",
                        "Son 12 aydaki mal kabul belgeleri, iadeler ve faturalardan hesaplanır — manuel değerlendirmeden değil. Yeterli geçmişi olmayan tedarikçi için hiçbir puan gösterilmez."),
    "vsc.window": ("Window", "الفترة", "Dönem"),
    "vsc.months": ("months", "شهراً", "ay"),
    "vsc.samples": ("observations", "حالة", "gözlem"),
    "vsc.receipts": ("receipts", "استلام", "mal kabul"),
    "vsc.lines": ("order lines", "بند طلب", "sipariş satırı"),
    "vsc.orders": ("orders", "أمر شراء", "sipariş"),
    "vsc.legend": ("On-time is measured per goods receipt against the promised delivery date. Quantity accuracy is measured per order line that is already past that date. Rejection covers goods refused on receipt or held in quarantine. Price variance is invoiced against ordered, per purchase order — a positive figure means the supplier billed above the order. Every figure states how many observations it rests on; below {n} the platform reports no number.",
                   "يُقاس الالتزام بالموعد لكل إشعار استلام مقارنةً بتاريخ التسليم الموعود. وتُقاس دقة الكمية لكل بند طلب تجاوز ذلك التاريخ. ويشمل الرفض البضائع المرفوضة عند الاستلام أو المحتجزة في الحجر. أما انحراف السعر فهو المفوتر مقابل المطلوب لكل أمر شراء — والقيمة الموجبة تعني أن المورد فوتر بأكثر من قيمة الأمر. كل رقم يذكر عدد الحالات التي يستند إليها، وأقل من {n} لا يعرض النظام رقماً.",
                   "Zamanında teslim, söz verilen teslim tarihine göre her mal kabul belgesi için ölçülür. Miktar doğruluğu, bu tarihi geçmiş her sipariş satırı için ölçülür. Ret; kabulde reddedilen veya karantinaya alınan malları kapsar. Fiyat sapması, her satın alma siparişi için faturalanan ile sipariş edilen arasındaki farktır — pozitif değer, tedarikçinin sipariş tutarının üzerinde fatura kestiği anlamına gelir. Her rakam kaç gözleme dayandığını belirtir; {n} altında sistem hiçbir rakam göstermez."),
}
