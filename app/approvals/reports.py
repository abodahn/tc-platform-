"""Procurement & Approvals — report declarations for the shared reporting engine.

Declaration ONLY: plain dicts built at import time, no DB access here (gunicorn
runs --preload, so anything that queries at import runs before the port binds).
The engine composes the SQL, binds every user value as a parameter, renders the
page, exports CSV/XLSX/PDF and enforces `proc_view` on all four surfaces.

Money notes, because a report with a quietly wrong number is worse than none:
  * `grand` is total + tax, the SAME formula as services.pr_amounts().
  * `egp` is the GROSS (grand) converted with the SAME currency rules as
    services.egp_total(): a PR already in EGP is never multiplied by fx_rate, and
    a non-positive rate falls back to 1:1. It is the only figure that may be
    SUMmed across a mixed-currency data set, so every grouped/aggregated report
    works in EGP and says so in its labels. It is deliberately gross where
    egp_total() is net — egp_total() exists to route the approval ladder on the
    order value, these reports exist to state what the money is.
  * the 3-way-match tolerances are the DOAM's, read from the SAME constants
    services.three_way_match() reads: max(grand * MATCH_TOLERANCE_PCT,
    MATCH_TOLERANCE_ABS) on value and MATCH_QTY_TOLERANCE_PCT on quantity. They
    are interpolated into the SQL from constants.py rather than typed as
    literals, because the previous literals (a flat 1% on value, no quantity
    tolerance at all) made this report disagree with the control it reports on.
    Exceptions are reported as AMOUNTS, not as English words, so they need no
    translation and can be sorted and totalled.
  * "committed" is the SAME status set as services.budget_status(): approved and
    beyond. A request still in the approval ladder has committed nothing yet —
    proc_waiting is where the pipeline is reported.
"""
from app.services import reporting as R
from app.approvals import constants as C   # tolerance numbers only, no DB

MODULE = "procurement"
LABEL_EN, LABEL_AR, LABEL_TR = "Procurement", "المشتريات", "Satın Alma"
PERM = "proc_view"

# --- shared SQL fragments (module-authored; never touched by user input) -----
_SUB = "COALESCE(p.total,0)"
_GRAND = f"({_SUB} * (1 + COALESCE(p.tax_rate,0)/100.0))"
# The two guards services.egp_total() applies, in SQL. Both are load-bearing:
#   * a PR repriced from USD back to EGP keeps the fx_rate of the currency it
#     used to be in (services.price_pr never resets it), so a bare
#     `grand * fx_rate` reported a 2,000 EGP request as 60,000 EGP;
#   * a zero/negative rate must fall back to 1:1, not annihilate the value —
#     COALESCE alone only catches NULL.
_IS_EGP = "UPPER(TRIM(COALESCE(p.currency,'EGP'))) = 'EGP'"
_FX = "(CASE WHEN COALESCE(p.fx_rate,1) > 0 THEN COALESCE(p.fx_rate,1) ELSE 1 END)"
_EGP = f"(CASE WHEN {_IS_EGP} THEN {_GRAND} ELSE {_GRAND} * {_FX} END)"
_PAID_EGP = (f"(CASE WHEN {_IS_EGP} THEN COALESCE(p.paid_amount,0) "
             f"ELSE COALESCE(p.paid_amount,0) * {_FX} END)")
# three_way_match's VALUE tolerance, expressed without a MAX() aggregate
# (SQLite's MAX(a,b) scalar form does not exist on PostgreSQL). The numbers come
# from constants.py, so the report and the control can only ever say the same
# thing: DOAM = max(2% of value, 500 EGP); pre-DOAM = max(1%, 1).
_TOL_PCT = C.MATCH_TOLERANCE_PCT if C.DOAM_IN_FORCE else 1.0
_TOL_ABS = C.MATCH_TOLERANCE_ABS if C.DOAM_IN_FORCE else 1.0
_TOL = (f"(CASE WHEN {_GRAND}*{_TOL_PCT / 100.0!r} > {_TOL_ABS!r} "
        f"THEN {_GRAND}*{_TOL_PCT / 100.0!r} ELSE {_TOL_ABS!r} END)")

# Statuses that represent real money the company has committed to spend. EXACTLY
# the set services.budget_status() gates a new request against — a request still
# climbing the ladder has committed nothing, and a report that said otherwise
# would disagree with the budget check that actually blocks a submission.
_COMMITTED = ("'approved','po_issued','partially_received','received','closed'")

_STATUS_OPTS = [
    ("draft", "Draft", "مسودة", "Taslak"),
    ("pending", "Pending", "قيد الاعتماد", "Beklemede"),
    ("approved", "Approved", "معتمد", "Onaylandı"),
    ("rejected", "Rejected", "مرفوض", "Reddedildi"),
    ("po_issued", "PO issued", "صدر أمر الشراء", "Sipariş verildi"),
    ("partially_received", "Partially received", "مستلم جزئياً", "Kısmen teslim"),
    ("received", "Received", "مستلم", "Teslim alındı"),
    ("closed", "Closed", "مغلق", "Kapandı"),
    ("cancelled", "Cancelled", "ملغي", "İptal"),
]

_STAGE_OPTS = [
    ("warehouse", "Warehouse", "المخازن", "Depo"),
    ("factory_manager", "Factory Manager", "مدير المصنع", "Fabrika Müdürü"),
    ("purchasing", "Purchasing", "المشتريات", "Satın Alma"),
    ("finance", "Finance", "المالية", "Finans"),
    ("cfo", "CFO", "المدير المالي", "Mali İşler Direktörü"),
    ("ceo", "CEO", "الرئيس التنفيذي", "Genel Müdür"),
]

_PAY_OPTS = [
    ("unpaid", "Unpaid", "غير مدفوع", "Ödenmedi"),
    ("partial", "Partly paid", "مدفوع جزئياً", "Kısmi ödendi"),
    ("paid", "Paid", "مدفوع", "Ödendi"),
]


def _common(**kw):
    kw.setdefault("module", MODULE)
    kw.setdefault("module_label", LABEL_EN)
    kw.setdefault("module_label_ar", LABEL_AR)
    kw.setdefault("module_label_tr", LABEL_TR)
    kw.setdefault("perm", PERM)
    return kw


# ---------------------------------------------------------------------------
# 1. Purchase requests & spend — the register, and spend by supplier
# ---------------------------------------------------------------------------
R.register(**_common(
    key="proc_requests",
    title="Purchase requests & spend",
    title_ar="طلبات الشراء والإنفاق",
    title_tr="Satın alma talepleri ve harcama",
    desc="Every purchase request with its value. 'Value (EGP)' is the GROSS "
         "amount — net + tax, then converted (gross x FX rate) — so "
         "mixed-currency requests add up honestly.",
    desc_ar="كل طلب شراء بقيمته. «القيمة (ج.م)» هي الإجمالي شاملاً الضريبة "
            "(الصافي + الضريبة) ثم محوّلاً (الإجمالي × سعر الصرف)، حتى تُجمع "
            "الطلبات متعددة العملات بصدق.",
    desc_tr="Her satın alma talebi ve değeri. 'Değer (EGP)' KDV DAHİL tutardır "
            "(net + vergi), sonra çevrilir (brüt x kur); böylece farklı para "
            "birimleri doğru toplanır.",
    select=(
        "p.pr_no AS pr_no, p.title AS title, p.department AS department, "
        "p.vendor AS vendor, p.status AS status, p.currency AS currency, "
        f"{_SUB} AS subtotal, {_GRAND} AS grand, {_EGP} AS egp, "
        "p.request_date AS request_date"
    ),
    frm="pr_requests p",
    base_where=["COALESCE(p.is_active,1) = 1"],
    order="p.id DESC",
    date_col="p.created_at",
    columns=[
        R.col("pr_no", "PR No", "رقم الطلب", "Talep No"),
        R.col("title", "Title", "العنوان", "Başlık"),
        R.col("department", "Department", "الإدارة", "Departman"),
        R.col("vendor", "Supplier", "المورد", "Tedarikçi"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("currency", "Currency", "العملة", "Para birimi"),
        R.col("subtotal", "Net", "الصافي", "Net", "num"),
        R.col("grand", "Gross", "الإجمالي مع الضريبة", "KDV dahil", "num"),
        R.col("egp", "Value (EGP)", "القيمة (ج.م)", "Değer (EGP)", "num",
              total=f"SUM({_EGP})"),
        R.col("request_date", "Requested", "تاريخ الطلب", "Talep tarihi", "date"),
    ],
    filters=[
        R.filt("status", "Status", "الحالة", "Durum", "p.status", "select", "=",
               _STATUS_OPTS),
        R.filt("department", "Department", "الإدارة", "Departman", "p.department"),
        R.filt("vendor", "Supplier", "المورد", "Tedarikçi", "p.vendor"),
        R.filt("currency", "Currency", "العملة", "Para birimi", "p.currency",
               "text", "="),
    ],
    kpis=[
        R.kpi("n", "Requests", "عدد الطلبات", "Talep sayısı", "COUNT(*)"),
        R.kpi("value", "Total value (EGP)", "إجمالي القيمة (ج.م)",
              "Toplam değer (EGP)", f"SUM({_EGP})"),
        R.kpi("committed", "Committed (EGP)", "المرتبط به (ج.م)",
              "Taahhüt (EGP)",
              f"SUM(CASE WHEN p.status IN ({_COMMITTED}) THEN {_EGP} ELSE 0 END)"),
        R.kpi("waiting", "Awaiting approval", "بانتظار الاعتماد", "Onay bekleyen",
              "SUM(CASE WHEN p.status = 'pending' THEN 1 ELSE 0 END)",
              better="down"),
    ],
    chart=R.chart("pareto", "p.vendor", f"SUM({_EGP})", "p.vendor",
                  "Spend by supplier (EGP)", "الإنفاق حسب المورد (ج.م)",
                  "Tedarikçiye göre harcama (EGP)"),
))


# ---------------------------------------------------------------------------
# 2. Budget vs committed vs spent, per department and year
# ---------------------------------------------------------------------------
_DEPT = "COALESCE(p.department,'')"
_PERIOD = "substr(COALESCE(p.request_date, p.created_at), 1, 4)"
_COMMIT_SUM = (f"SUM(CASE WHEN p.status IN ({_COMMITTED}) THEN {_EGP} ELSE 0 END)")
_SPENT_SUM = f"SUM({_PAID_EGP})"

# EVERY (department, year) that either holds a budget or carries a request. The
# obvious `pr_requests LEFT JOIN budgets` cannot be used: it silently drops a
# department that has a budget and has not spent it, which is the single most
# important row on a budget report and made the "Budget (EGP)" headline read
# 1.65M against 6.09M actually allocated. Neither side may drive, so the KEYS
# drive and both sides hang off them.
# COALESCE on department, not the raw column: NULL = NULL is false in SQL, so a
# request with no department would otherwise fall out of its own report.
_KEYS = (
    "(SELECT COALESCE(department,'') AS department, COALESCE(period,'') AS period "
    "FROM proc_budgets GROUP BY COALESCE(department,''), COALESCE(period,'') "
    "UNION "
    "SELECT COALESCE(department,''), "
    "substr(COALESCE(request_date, created_at), 1, 4) "
    "FROM pr_requests WHERE COALESCE(is_active,1) = 1 "
    "GROUP BY COALESCE(department,''), "
    "substr(COALESCE(request_date, created_at), 1, 4)) k"
)
# proc_budgets has no unique key: two rows for the same department+year must add
# up once, not multiply every PR row through the join.
_BUDGETS = ("(SELECT COALESCE(department,'') AS department, "
            "COALESCE(period,'') AS period, SUM(COALESCE(amount,0)) AS amount "
            "FROM proc_budgets GROUP BY COALESCE(department,''), "
            "COALESCE(period,'')) b")

R.register(**_common(
    key="proc_budget",
    title="Budget vs committed vs spent",
    title_ar="الموازنة مقابل المرتبط والمنصرف",
    title_tr="Bütçe / taahhüt / harcama",
    desc="Per department and year: the annual budget, what approved requests "
         "have committed against it, and what has actually been paid. EGP. A "
         "department that holds a budget and has spent nothing is listed too.",
    desc_ar="لكل إدارة وسنة: الموازنة السنوية، وما ارتبطت به الطلبات المعتمدة، "
            "وما تم دفعه فعلياً. بالجنيه المصري. وتظهر أيضاً الإدارة التي لديها "
            "موازنة ولم تنفق منها شيئاً.",
    desc_tr="Departman ve yıl bazında: yıllık bütçe, onaylı taleplerle "
            "taahhüt edilen tutar ve fiilen ödenen tutar. EGP. Bütçesi olup "
            "hiç harcamamış departman da listelenir.",
    select=(
        "k.department AS department, "
        "k.period AS period, "
        "MAX(COALESCE(b.amount,0)) AS budget, "
        "COUNT(p.id) AS requests, "
        f"{_COMMIT_SUM} AS committed, "
        f"{_SPENT_SUM} AS spent, "
        f"MAX(COALESCE(b.amount,0)) - {_COMMIT_SUM} AS remaining"
    ),
    frm=(f"{_KEYS} "
         f"LEFT JOIN {_BUDGETS} ON b.department = k.department "
         "AND b.period = k.period "
         "LEFT JOIN pr_requests p ON " + _DEPT + " = k.department "
         f"AND {_PERIOD} = k.period AND COALESCE(p.is_active,1) = 1"),
    group="k.department, k.period",
    order="2 DESC, 5 DESC",
    # No date range on purpose. The grain is a YEAR; a window that cut a year in
    # half would shrink `committed` while `budget` stayed annual, and the report
    # would read as an overspend that is not there. Filter by the Year column.
    date_col=None,
    columns=[
        R.col("department", "Department", "الإدارة", "Departman"),
        R.col("period", "Year", "السنة", "Yıl"),
        R.col("budget", "Budget (EGP)", "الموازنة (ج.م)", "Bütçe (EGP)", "num",
              total="SUM(budget)"),
        R.col("requests", "Requests", "عدد الطلبات", "Talep", "num",
              total="SUM(requests)"),
        R.col("committed", "Committed (EGP)", "المرتبط (ج.م)", "Taahhüt (EGP)",
              "num", total="SUM(committed)"),
        R.col("spent", "Paid (EGP)", "المدفوع (ج.م)", "Ödenen (EGP)", "num",
              total="SUM(spent)"),
        R.col("remaining", "Remaining (EGP)", "المتبقي (ج.م)", "Kalan (EGP)",
              "num", total="SUM(remaining)"),
    ],
    filters=[
        R.filt("department", "Department", "الإدارة", "Departman", "k.department"),
        # Named "year", not "period": `period` is the engine's own date-preset
        # query parameter and the two would fight over the same query string.
        R.filt("year", "Year", "السنة", "Yıl", "k.period", "text", "="),
    ],
    kpis=[
        R.kpi("budget", "Budget (EGP)", "الموازنة (ج.م)", "Bütçe (EGP)",
              "SUM(budget)"),
        R.kpi("committed", "Committed (EGP)", "المرتبط (ج.م)", "Taahhüt (EGP)",
              "SUM(committed)", better="down"),
        R.kpi("spent", "Paid (EGP)", "المدفوع (ج.م)", "Ödenen (EGP)",
              "SUM(spent)"),
        R.kpi("remaining", "Remaining (EGP)", "المتبقي (ج.م)", "Kalan (EGP)",
              "SUM(remaining)"),
    ],
    chart=R.chart("bar", "k.department", _COMMIT_SUM, "k.department",
                  "Committed by department (EGP)", "المرتبط حسب الإدارة (ج.م)",
                  "Departmana göre taahhüt (EGP)"),
))


# ---------------------------------------------------------------------------
# 3. Where requests are waiting — the live approval queue, oldest first
# ---------------------------------------------------------------------------
R.register(**_common(
    key="proc_waiting",
    title="Approvals waiting",
    title_ar="الاعتمادات المعلقة",
    title_tr="Bekleyen onaylar",
    desc="Every approval step that is active and unsigned, oldest first, with "
         "the value it is holding up. Sort by 'Waiting since' to see the queue.",
    desc_ar="كل خطوة اعتماد مفعّلة ولم تُوقّع بعد، الأقدم أولاً، مع القيمة "
            "المحتجزة. رتّب حسب «معلق منذ» لرؤية الطابور.",
    desc_tr="Etkin ve imzalanmamış her onay adımı, en eskisi başta, beklettiği "
            "tutarla birlikte. Sırayı görmek için 'Bekliyor' sütununa göre sırala.",
    select=(
        "p.pr_no AS pr_no, p.title AS title, p.department AS department, "
        "s.seq AS seq, s.stage AS stage, s.approver_role AS approver_role, "
        "p.currency AS currency, "
        f"{_GRAND} AS grand, {_EGP} AS egp, "
        "s.activated_at AS activated_at"
    ),
    frm="pr_steps s JOIN pr_requests p ON p.id = s.pr_id",
    base_where=["s.status = 'pending'", "s.activated_at IS NOT NULL",
                "p.status = 'pending'", "COALESCE(p.is_active,1) = 1"],
    order="s.activated_at ASC, s.id ASC",
    date_col="s.activated_at",
    columns=[
        R.col("pr_no", "PR No", "رقم الطلب", "Talep No"),
        R.col("title", "Title", "العنوان", "Başlık"),
        R.col("department", "Department", "الإدارة", "Departman"),
        R.col("seq", "Step", "الخطوة", "Adım", "num"),
        R.col("stage", "Stage", "المرحلة", "Aşama"),
        R.col("approver_role", "Approver", "المعتمد", "Onaylayan"),
        R.col("currency", "Currency", "العملة", "Para birimi"),
        R.col("grand", "Value", "القيمة", "Tutar", "num"),
        R.col("egp", "Value (EGP)", "القيمة (ج.م)", "Değer (EGP)", "num",
              total=f"SUM({_EGP})"),
        R.col("activated_at", "Waiting since", "معلق منذ", "Bekliyor", "date"),
    ],
    filters=[
        R.filt("stage", "Stage", "المرحلة", "Aşama", "s.stage", "select", "=",
               _STAGE_OPTS),
        R.filt("department", "Department", "الإدارة", "Departman", "p.department"),
    ],
    kpis=[
        R.kpi("steps", "Steps waiting", "خطوات معلقة", "Bekleyen adım",
              "COUNT(*)", better="down"),
        R.kpi("value", "Value held up (EGP)", "القيمة المحتجزة (ج.م)",
              "Bekleyen tutar (EGP)", f"SUM({_EGP})", better="down"),
        R.kpi("prs", "Requests affected", "طلبات متأثرة", "Etkilenen talep",
              "COUNT(DISTINCT p.id)", better="down"),
    ],
    chart=R.chart("bar", "s.stage", "COUNT(*)", "s.stage",
                  "Waiting steps by stage", "الخطوات المعلقة حسب المرحلة",
                  "Aşamaya göre bekleyen adım"),
))


# ---------------------------------------------------------------------------
# 4. Receipts, invoices, payments — 3-way-match exposure and what is unpaid
# ---------------------------------------------------------------------------
_INV = ("LEFT JOIN (SELECT pr_id, "
        "SUM(COALESCE(amount,0)+COALESCE(tax,0)) AS inv_gross, "
        "SUM(COALESCE(amount,0)) AS inv_net "
        "FROM pr_invoices GROUP BY pr_id) i ON i.pr_id = p.id")
_ITM = ("LEFT JOIN (SELECT pr_id, SUM(COALESCE(qty,0)) AS ord_qty, "
        "SUM(COALESCE(received_qty,0)) AS rcv_qty, "
        "SUM(COALESCE(received_qty,0)*COALESCE(unit_price,0)) AS rcv_val "
        "FROM pr_items GROUP BY pr_id) t ON t.pr_id = p.id")

_OVER_BILLED = (f"CASE WHEN COALESCE(i.inv_gross,0) > {_GRAND} + {_TOL} "
                f"THEN COALESCE(i.inv_gross,0) - {_GRAND} ELSE 0 END")
_OVER_RECEIVED = (f"CASE WHEN COALESCE(i.inv_net,0) > COALESCE(t.rcv_val,0) + {_TOL} "
                  "THEN COALESCE(i.inv_net,0) - COALESCE(t.rcv_val,0) ELSE 0 END")
_SHORT_QTY = ("CASE WHEN COALESCE(t.rcv_qty,0) < COALESCE(t.ord_qty,0) "
              "THEN COALESCE(t.ord_qty,0) - COALESCE(t.rcv_qty,0) ELSE 0 END")
# The column above states the WHOLE shortfall — that is the point of the column,
# the DOAM wants a short delivery visible even when it is tolerable. Whether it
# is an EXCEPTION is a different question, and it is answered PER LINE with the
# same quantity tolerance three_way_match applies. Per line, because the summed
# quantities above mix units: 1,000 metres of thread delivered in full hides two
# machines that never arrived, and the KPI would report the order as clean.
_QTY_FACTOR = 1.0 - (C.MATCH_QTY_TOLERANCE_PCT / 100.0 if C.DOAM_IN_FORCE else 0.0)
_QTY_EXC = ("EXISTS (SELECT 1 FROM pr_items x WHERE x.pr_id = p.id AND "
            f"COALESCE(x.received_qty,0) < COALESCE(x.qty,0) * {_QTY_FACTOR!r} - 1e-9)")
# Goods returned to the supplier on receipt: an OPEN debit note is value this PO
# will never be paid, because add_payment/three_way_match refuse to release it.
# Without this join the report told finance a supplier was still owed exactly the
# money the payment gate was holding back, on a row it also labelled 'paid'.
_DN = ("LEFT JOIN (SELECT pr_id, SUM(COALESCE(total,0)) AS dn_open "
       "FROM pr_returns WHERE status='open' GROUP BY pr_id) d ON d.pr_id = p.id")
_DN_OPEN = "COALESCE(d.dn_open,0)"
_OUTSTANDING = f"({_GRAND} - {_DN_OPEN} - COALESCE(p.paid_amount,0))"
# Exposure only. An OVERPAID request has a negative outstanding, which is a real
# fact in the column, but feeding it to a pareto produced a cumulative % that ran
# past 100 and then came back down — an invented number. The chart sums what is
# still owed; the column keeps the sign.
_EXPOSURE = f"(CASE WHEN {_OUTSTANDING} > 0 THEN {_OUTSTANDING} ELSE 0 END)"
# A 3-way match needs three documents. With no invoice on file, services
# .three_way_match() returns status 'pending', NOT 'mismatch' — a PO issued this
# morning with nothing received yet is not an exception, it is an open order.
# Without this guard the KPI counted every open PO and read as "everything is
# broken".
_HAS_INVOICE = "i.pr_id IS NOT NULL"

R.register(**_common(
    key="proc_payables",
    title="Receipts, invoices & payment exposure",
    title_ar="الاستلام والفواتير والمكشوف من السداد",
    title_tr="Mal kabul, fatura ve ödeme riski",
    desc=("Ordered vs received vs invoiced vs paid, for orders that reached PO "
          "stage. Over-billed and short-delivered are shown as amounts, using "
          "exactly the tolerances the 3-way match on the PR page applies: "
          "%g%% of value or %g EGP, whichever is greater, and %g%% of quantity. "
          "Outstanding is net of open debit notes (Debited back): that value is "
          "held back by the payment gate, so counting it as owed "
          "overstates exposure."
          % (_TOL_PCT, _TOL_ABS, C.MATCH_QTY_TOLERANCE_PCT)),
    desc_ar=("المطلوب مقابل المستلم مقابل المفوتر مقابل المدفوع، للطلبات التي "
             "وصلت لأمر الشراء. الزيادة في الفوترة والنقص في التوريد تظهر كمبالغ "
             "بنفس سماحية المطابقة الثلاثية: %g%% من القيمة أو %g جنيه أيهما "
             "أكبر، و%g%% من الكمية. المتبقي للسداد بعد خصم الإشعارات المدينة "
             "المفتوحة، لأن بوابة الدفع تحجز هذه القيمة." % (_TOL_PCT, _TOL_ABS,
                                        C.MATCH_QTY_TOLERANCE_PCT)),
    desc_tr=("Sipariş / teslim / fatura / ödeme karşılaştırması, sipariş "
             "aşamasına gelen talepler için. Fazla faturalama ve eksik teslimat "
             "tutar olarak, 3'lü mutabakatın uyguladığı toleranslarla: değerin "
             "%%%g'i veya %g EGP (hangisi büyükse) ve miktarın %%%g'i. Bakiye, açık "
             "borç dekontları düşülerek hesaplanır; o tutarı ödeme kapısı zaten tutar."
             % (_TOL_PCT, _TOL_ABS, C.MATCH_QTY_TOLERANCE_PCT)),
    select=(
        "p.pr_no AS pr_no, p.vendor AS vendor, p.department AS department, "
        "p.status AS status, p.currency AS currency, "
        f"{_GRAND} AS grand, "
        "COALESCE(i.inv_gross,0) AS invoiced, "
        "COALESCE(t.rcv_val,0) AS received_value, "
        "COALESCE(p.paid_amount,0) AS paid, "
        f"{_DN_OPEN} AS dn_open, "
        f"{_OUTSTANDING} AS outstanding, "
        f"{_OVER_BILLED} AS over_billed, "
        f"{_OVER_RECEIVED} AS over_received, "
        f"{_SHORT_QTY} AS short_qty, "
        "p.payment_status AS payment_status, p.due_date AS due_date"
    ),
    frm=f"pr_requests p {_INV} {_ITM} {_DN}",
    base_where=["COALESCE(p.is_active,1) = 1",
                "p.status IN ('po_issued','partially_received','received','closed')"],
    order="p.due_date ASC, p.id DESC",
    date_col="p.created_at",
    columns=[
        R.col("pr_no", "PR No", "رقم الطلب", "Talep No"),
        R.col("vendor", "Supplier", "المورد", "Tedarikçi"),
        R.col("department", "Department", "الإدارة", "Departman"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("currency", "Currency", "العملة", "Para birimi"),
        R.col("grand", "Ordered", "المطلوب", "Sipariş", "num",
              total=f"SUM({_GRAND})"),
        R.col("received_value", "Received value", "قيمة المستلم", "Teslim değeri",
              "num", total="SUM(COALESCE(t.rcv_val,0))"),
        R.col("invoiced", "Invoiced", "المفوتر", "Faturalanan", "num",
              total="SUM(COALESCE(i.inv_gross,0))"),
        R.col("paid", "Paid", "المدفوع", "Ödenen", "num",
              total="SUM(COALESCE(p.paid_amount,0))"),
        R.col("dn_open", "Debited back", "إشعارات مدينة مفتوحة", "Borç dekontu",
              "num", total=f"SUM({_DN_OPEN})"),
        R.col("outstanding", "Outstanding", "المتبقي للسداد", "Bakiye", "num",
              total=f"SUM({_OUTSTANDING})"),
        R.col("over_billed", "Over-billed", "زيادة فوترة", "Fazla fatura", "num",
              total=f"SUM({_OVER_BILLED})"),
        R.col("over_received", "Invoiced over receipt", "مفوتر أكثر من المستلم",
              "Teslimden fazla fatura", "num", total=f"SUM({_OVER_RECEIVED})"),
        R.col("short_qty", "Short qty", "نقص الكمية", "Eksik miktar", "num",
              total=f"SUM({_SHORT_QTY})"),
        R.col("payment_status", "Payment", "حالة السداد", "Ödeme"),
        R.col("due_date", "Due", "تاريخ الاستحقاق", "Vade", "date"),
    ],
    filters=[
        R.filt("payment_status", "Payment status", "حالة السداد", "Ödeme durumu",
               "p.payment_status", "select", "=", _PAY_OPTS),
        R.filt("vendor", "Supplier", "المورد", "Tedarikçi", "p.vendor"),
        R.filt("due_before", "Due on or before", "مستحق حتى", "Vadesi şu tarihe kadar",
               "p.due_date", "date", "<="),
        R.filt("currency", "Currency", "العملة", "Para birimi", "p.currency",
               "text", "="),
    ],
    kpis=[
        R.kpi("ordered", "Ordered", "المطلوب", "Sipariş", f"SUM({_GRAND})"),
        R.kpi("invoiced", "Invoiced", "المفوتر", "Faturalanan",
              "SUM(COALESCE(i.inv_gross,0))"),
        R.kpi("paid", "Paid", "المدفوع", "Ödenen", "SUM(COALESCE(p.paid_amount,0))"),
        R.kpi("outstanding", "Outstanding", "المتبقي للسداد", "Bakiye",
              f"SUM({_OUTSTANDING})", better="down"),
        R.kpi("exceptions", "Match exceptions", "استثناءات المطابقة",
              "Mutabakat istisnası",
              f"SUM(CASE WHEN {_HAS_INVOICE} AND ({_OVER_BILLED} > 0 "
              f"OR {_OVER_RECEIVED} > 0 OR {_QTY_EXC}) "
              f"THEN 1 ELSE 0 END)",
              better="down"),
    ],
    chart=R.chart("pareto", "p.vendor", f"SUM({_EXPOSURE})", "p.vendor",
                  "Outstanding by supplier", "المتبقي حسب المورد",
                  "Tedarikçiye göre bakiye"),
))


# ---------------------------------------------------------------------------
# 6. Bought off-catalogue — the list that keeps the item master alive
#
# Every PR line with NO catalogue link, grouped by the TEXT people typed, so the
# same part typed six ways reads as six rows of six lines rather than 36 lines
# of noise. `Asked about` counts the new-item requests already raised for that
# text: a row with a high line count and 0 asked about is exactly what should be
# turned into a request and given an ERP code, and it is invisible without this.
#
# NO money column on purpose. This is a data-hygiene report, it runs on the
# module's own `proc_view`, and a requester holds that — showing what the floor
# buys is fine, showing what it costs is the pricing gate's business.
# ---------------------------------------------------------------------------
_OFF_KEY = "LOWER(TRIM(i.item))"
_RAISED = ("MAX((SELECT COUNT(*) FROM proc_item_requests r "
           f"WHERE LOWER(TRIM(r.name)) = {_OFF_KEY}))")

R.register(**_common(
    key="proc_off_catalogue",
    title="Bought off-catalogue",
    title_ar="المشتَرى خارج الكتالوج",
    title_tr="Katalog dışı alınanlar",
    desc="Request lines that are NOT linked to a catalogue item, grouped by the "
         "text people typed. The master decays through this list: a text bought "
         "again and again with nothing asked about it is an item missing its ERP "
         "code. Raise a new-item request from the request form to close it.",
    desc_ar="سطور الطلبات غير المرتبطة بصنف في الكتالوج، مجمّعة حسب النص الذي "
            "كتبه المستخدمون. من هنا تتآكل قائمة الأصناف: نص يتكرر شراؤه ولم "
            "يُرفع بشأنه أي طلب يعني صنفاً بلا كود ERP. ارفع طلب صنف جديد من "
            "نموذج الطلب لإغلاقه.",
    desc_tr="Katalog kalemine bağlı OLMAYAN talep satırları, kullanıcıların "
            "yazdığı metne göre gruplanır. Ana liste buradan aşınır: tekrar "
            "tekrar alınan ama hakkında hiç talep açılmamış bir metin, ERP kodu "
            "olmayan bir kalemdir. Talep formundan yeni kalem talebi açarak "
            "kapatın.",
    select=(
        "MAX(i.item) AS item_text, "
        "MAX(COALESCE(i.unit,'')) AS unit, "
        "COUNT(*) AS lines, "
        "COUNT(DISTINCT i.pr_id) AS requests, "
        "SUM(COALESCE(i.qty,0)) AS qty, "
        "COUNT(DISTINCT COALESCE(p.department,'')) AS departments, "
        f"{_RAISED} AS raised, "
        "MAX(COALESCE(p.request_date, p.created_at)) AS last_seen"
    ),
    frm="pr_items i JOIN pr_requests p ON p.id = i.pr_id",
    base_where=["i.item_id IS NULL",
                "COALESCE(p.is_active,1) = 1",
                "TRIM(COALESCE(i.item,'')) <> ''"],
    group=_OFF_KEY,
    order="3 DESC, 4 DESC",
    date_col="p.created_at",
    columns=[
        R.col("item_text", "Item as typed", "الصنف كما كُتب", "Yazıldığı hâliyle kalem"),
        R.col("unit", "Unit", "الوحدة", "Birim"),
        R.col("lines", "Lines", "عدد السطور", "Satır", "num", total="SUM(lines)"),
        R.col("requests", "Requests", "عدد الطلبات", "Talep", "num",
              total="SUM(requests)"),
        R.col("qty", "Qty", "الكمية", "Miktar", "num", total="SUM(qty)"),
        R.col("departments", "Departments", "عدد الإدارات", "Departman", "num"),
        R.col("raised", "Asked about", "طلبات إضافته", "Hakkında talep", "num",
              total="SUM(raised)"),
        R.col("last_seen", "Last asked for", "آخر مرة طُلب", "Son talep", "date"),
    ],
    filters=[
        R.filt("item", "Item text", "نص الصنف", "Kalem metni", "i.item"),
        R.filt("department", "Department", "الإدارة", "Departman", "p.department"),
    ],
    kpis=[
        R.kpi("texts", "Distinct item texts", "نصوص أصناف مختلفة",
              "Farklı kalem metni", "COUNT(*)", better="down"),
        R.kpi("lines", "Off-catalogue lines", "سطور خارج الكتالوج",
              "Katalog dışı satır", "SUM(lines)", better="down"),
        R.kpi("never_asked", "Never asked about", "لم يُطلب إضافتها إطلاقاً",
              "Hiç talep açılmamış",
              "SUM(CASE WHEN raised = 0 THEN 1 ELSE 0 END)", better="down"),
    ],
    chart=R.chart("bar", "MAX(i.item)", "COUNT(*)", _OFF_KEY,
                  "Most-typed off-catalogue items", "أكثر الأصناف كتابةً خارج الكتالوج",
                  "En çok yazılan katalog dışı kalemler"),
))


# ---------------------------------------------------------------------------
# N. Records retention — what may be disposed of, and what may NOT
# ---------------------------------------------------------------------------
# Audit 3.4-b9b: retention existed only as the words "5 yrs" / "10 yrs" on the
# controlled-forms page. Every request now carries retention_until, and this is
# the REPORT a records officer works from — deliberately a report and not a job.
# Nothing here deletes anything: it lists what has passed its date so a human can
# retire records one at a time (services.dispose_pr, which refuses anything still
# inside its window). An unattended purge of financial records is a much larger
# risk than keeping them too long.
_RET_DUE = ("(CASE WHEN COALESCE(NULLIF(TRIM(p.retention_until),''),'9999-12-31') "
            "<= date('now') THEN '1' ELSE '0' END)")

R.register(**_common(
    key="proc_retention",
    title="Records retention & disposal",
    title_ar="حفظ السجلات والتخلص منها",
    title_tr="Kayıt saklama ve imha",
    desc="Every live purchase record with the date it may FIRST be disposed of "
         "— 10 years for capital expenditure, 5 for everything else, counted "
         "from the day it was raised. 'Disposal due' = Yes means the retention "
         "period has passed and a records officer may retire the record; "
         "nothing is ever removed automatically.",
    desc_ar="كل سجل شراء قائم مع تاريخ أول موعد يجوز فيه التخلص منه — 10 سنوات "
            "للنفقات الرأسمالية و5 سنوات لما عداها، محسوبة من تاريخ إنشائه. "
            "«حان التخلص = نعم» تعني انتهاء مدة الحفظ وجواز إحالة السجل للتخلص "
            "بقرار موظف السجلات؛ ولا يُحذف أي سجل تلقائياً.",
    desc_tr="Her canlı satın alma kaydı ve ilk imha edilebileceği tarih — "
            "yatırım harcamaları için 10 yıl, diğerleri için 5 yıl, kaydın "
            "açıldığı günden sayılır. 'İmha zamanı = Evet' saklama süresinin "
            "dolduğunu ve kayıt sorumlusunun kaydı emekliye ayırabileceğini "
            "gösterir; hiçbir kayıt otomatik silinmez.",
    select=(
        "p.pr_no AS pr_no, p.title AS title, p.department AS department, "
        "p.status AS status, COALESCE(NULLIF(TRIM(p.expenditure_kind),''),'opex') AS kind, "
        f"{_EGP} AS egp, COALESCE(p.request_date, p.created_at) AS raised, "
        "p.retention_until AS retention_until, "
        f"{_RET_DUE} AS due"
    ),
    frm="pr_requests p",
    base_where=["COALESCE(p.is_active,1) = 1"],
    order="COALESCE(p.retention_until,'9999-12-31') ASC, p.id ASC",
    date_col="p.created_at",
    columns=[
        R.col("pr_no", "PR No", "رقم الطلب", "Talep No"),
        R.col("title", "Title", "العنوان", "Başlık"),
        R.col("department", "Department", "الإدارة", "Departman"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("kind", "Expenditure", "نوع الإنفاق", "Harcama türü"),
        R.col("egp", "Value (EGP)", "القيمة (ج.م)", "Değer (EGP)", "num",
              total=f"SUM({_EGP})"),
        R.col("raised", "Raised", "تاريخ الإنشاء", "Açılış", "date"),
        R.col("retention_until", "Keep until", "يُحفظ حتى", "Saklama sonu", "date"),
        R.col("due", "Disposal due", "حان التخلص", "İmha zamanı"),
    ],
    filters=[
        R.filt("due", "Disposal due", "حان التخلص", "İmha zamanı", _RET_DUE,
               "select", "=", [("1", "Yes", "نعم", "Evet"),
                               ("0", "No", "لا", "Hayır")]),
        R.filt("kind", "Expenditure", "نوع الإنفاق", "Harcama türü",
               "COALESCE(NULLIF(TRIM(p.expenditure_kind),''),'opex')", "select", "=",
               [("opex", "Operating", "تشغيلي", "İşletme"),
                ("capex", "Capital", "رأسمالي", "Yatırım")]),
        R.filt("department", "Department", "الإدارة", "Departman", "p.department"),
        R.filt("status", "Status", "الحالة", "Durum", "p.status", "select", "=",
               _STATUS_OPTS),
    ],
    kpis=[
        R.kpi("n", "Records held", "سجلات محفوظة", "Saklanan kayıt", "COUNT(*)"),
        R.kpi("due", "Disposal due", "حان التخلص عنها", "İmha zamanı gelen",
              f"SUM(CASE WHEN {_RET_DUE} = '1' THEN 1 ELSE 0 END)", better="none"),
        R.kpi("capex", "Capital (10-year)", "رأسمالي (10 سنوات)",
              "Yatırım (10 yıl)",
              "SUM(CASE WHEN LOWER(TRIM(COALESCE(p.expenditure_kind,'opex'))) = 'capex' "
              "THEN 1 ELSE 0 END)", better="none"),
        R.kpi("unstamped", "No retention date", "بلا تاريخ حفظ", "Saklama tarihi yok",
              "SUM(CASE WHEN TRIM(COALESCE(p.retention_until,'')) = '' THEN 1 ELSE 0 END)",
              better="down"),
    ],
    chart=R.chart("bar", "substr(COALESCE(p.retention_until,'—'),1,4)", "COUNT(*)",
                  "substr(COALESCE(p.retention_until,'—'),1,4)",
                  "Records falling due by year", "السجلات المستحقة حسب السنة",
                  "Yıla göre süresi dolan kayıtlar"),
))
