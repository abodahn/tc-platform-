"""Production planning — report declarations for the shared reporting engine.

Declaration ONLY (no DB access at import; gunicorn runs --preload).

Day arithmetic is deliberately absent. SQLite has julianday(), PostgreSQL has
date subtraction, and there is no expression that means the same thing on both,
so nothing here computes "days late" or "days waiting". What IS portable is a
string comparison of two ISO dates ('2026-08-01' > '2026-07-30'), which is how
the late flag is built, and a division of minutes by minutes-per-day, which is
how the load is expressed as DAYS OF WORK — the number a planner actually acts
on ("SEW-1 is carrying 34 days").

Formulas match app/planning/services.py exactly:
  daily capacity minutes = operators x working_minutes x efficiency_pct / 100
  required minutes       = qty x smv                (the SNAPSHOT smv on the
                                                     allocation, never a later edit)
  good pieces            = actual_qty - reject_qty  (what can actually be shipped)
"""
from app.services import reporting as R

MODULE = "planning"
LABEL_EN, LABEL_AR, LABEL_TR = ("Production Planning", "تخطيط الإنتاج",
                                "Üretim Planlama")
PERM = "pln_view"

_CAP = ("(COALESCE(l.operators,0) * COALESCE(l.working_minutes,0) "
        "* COALESCE(l.efficiency_pct,0) / 100.0)")
_MIN = "(COALESCE(a.qty,0) * COALESCE(a.smv,0))"

_STATUS_OPTS = [
    ("planned", "Planned", "مخطط", "Planlandı"),
    ("running", "Running", "قيد التنفيذ", "Devam ediyor"),
    ("done", "Done", "منتهي", "Tamamlandı"),
]


def _common(**kw):
    kw.setdefault("module", MODULE)
    kw.setdefault("module_label", LABEL_EN)
    kw.setdefault("module_label_ar", LABEL_AR)
    kw.setdefault("module_label_tr", LABEL_TR)
    kw.setdefault("perm", PERM)
    return kw


# ---------------------------------------------------------------------------
# 1. Line load & capacity — how many days of work each line is carrying
# ---------------------------------------------------------------------------
R.register(**_common(
    key="planning_line_load",
    title="Line load & capacity",
    title_ar="تحميل الخطوط والطاقة",
    title_tr="Hat yükü ve kapasite",
    desc="Minutes loaded on every line, and how many working days that is at "
         "the line's own capacity (operators x minutes x efficiency). A line "
         "with no capacity shows no days rather than a division by zero.",
    desc_ar="الدقائق المحمّلة على كل خط، وكم يوم عمل تمثلها بطاقة الخط نفسه "
            "(عدد العمال × الدقائق × الكفاءة). الخط بلا طاقة لا يعرض أياماً "
            "بدلاً من القسمة على صفر.",
    desc_tr="Her hatta yüklenen dakika ve bunun hattın kendi kapasitesiyle "
            "(operatör x dakika x verimlilik) kaç iş günü ettiği. Kapasitesiz "
            "hat sıfıra bölme yerine boş gün gösterir.",
    select=(
        "l.name AS line, l.code AS code, l.section AS section, "
        "COALESCE(l.operators,0) AS operators, "
        f"{_CAP} AS capacity_min, "
        "COUNT(*) AS allocations, "
        "SUM(COALESCE(a.qty,0)) AS qty, "
        f"SUM({_MIN}) AS minutes, "
        f"SUM({_MIN}) / NULLIF({_CAP}, 0) AS days_of_work, "
        "MIN(a.start_date) AS first_start, MAX(a.end_date) AS last_end"
    ),
    frm="pln_allocations a JOIN pln_lines l ON l.id = a.pline_id",
    group=("l.id, l.name, l.code, l.section, l.operators, l.working_minutes, "
           "l.efficiency_pct"),
    order="9 DESC",
    date_col="a.start_date",
    columns=[
        R.col("line", "Line", "الخط", "Hat"),
        R.col("code", "Code", "الكود", "Kod"),
        R.col("section", "Section", "القسم", "Bölüm"),
        R.col("operators", "Operators", "عدد العمال", "Operatör", "num",
              total="SUM(operators)"),
        R.col("capacity_min", "Capacity min/day", "الطاقة دقيقة/يوم",
              "Kapasite dk/gün", "num", total="SUM(capacity_min)"),
        R.col("allocations", "Allocations", "التحميلات", "Yükleme", "num",
              total="SUM(allocations)"),
        R.col("qty", "Planned qty", "الكمية المخططة", "Planlanan miktar", "num",
              total="SUM(qty)"),
        R.col("minutes", "Loaded minutes", "الدقائق المحمّلة", "Yüklenen dakika",
              "num", total="SUM(minutes)"),
        R.col("days_of_work", "Days of work", "أيام العمل", "İş günü", "num"),
        R.col("first_start", "First start", "أول بداية", "İlk başlangıç", "date"),
        R.col("last_end", "Last finish", "آخر نهاية", "Son bitiş", "date"),
    ],
    filters=[
        R.filt("section", "Section", "القسم", "Bölüm", "l.section"),
        R.filt("line", "Line", "الخط", "Hat", "l.name"),
        R.filt("status", "Allocation status", "حالة التحميل", "Yükleme durumu",
               "a.status", "select", "=", _STATUS_OPTS),
    ],
    kpis=[
        R.kpi("lines", "Lines loaded", "خطوط محمّلة", "Yüklü hat", "COUNT(*)"),
        R.kpi("minutes", "Loaded minutes", "الدقائق المحمّلة", "Yüklenen dakika",
              "SUM(minutes)"),
        R.kpi("qty", "Planned qty", "الكمية المخططة", "Planlanan miktar",
              "SUM(qty)"),
        R.kpi("peak", "Longest queue (days)", "أطول طابور (أيام)",
              "En uzun kuyruk (gün)", "MAX(days_of_work)", better="down"),
    ],
    chart=R.chart("bar", "l.name", f"SUM({_MIN})", "l.name",
                  "Loaded minutes by line", "الدقائق المحمّلة حسب الخط",
                  "Hatta göre yüklenen dakika"),
))


# ---------------------------------------------------------------------------
# 2. Plan vs ship date — which loaded orders finish after the buyer's date
# ---------------------------------------------------------------------------
# Both dates are stored as ISO 'YYYY-MM-DD' strings, so a plain > is a correct
# calendar comparison on SQLite and PostgreSQL alike. NULL on either side means
# "unknown", never "late".
_LATE = ("(CASE WHEN a.end_date IS NULL OR o.ship_date IS NULL THEN NULL "
         "WHEN a.end_date > o.ship_date THEN 1 ELSE 0 END)")

R.register(**_common(
    key="planning_schedule",
    title="Plan vs ship date",
    title_ar="الخطة مقابل تاريخ الشحن",
    title_tr="Plan ve sevk tarihi",
    desc="Every order loaded onto a line, with its projected finish against "
         "the buyer's ship date. Late = 1 when the plan finishes after the "
         "ship date; blank when either date is unknown.",
    desc_ar="كل طلب محمّل على خط، مع تاريخ الانتهاء المتوقع مقابل تاريخ شحن "
            "العميل. متأخر = ١ عندما تنتهي الخطة بعد تاريخ الشحن؛ فارغ إذا كان "
            "أحد التاريخين غير معروف.",
    desc_tr="Bir hatta yüklenen her sipariş ve öngörülen bitişinin müşterinin "
            "sevk tarihiyle karşılaştırması. Geç = 1, plan sevk tarihinden "
            "sonra biterse; tarihlerden biri bilinmiyorsa boş.",
    select=(
        "o.order_no AS order_no, o.buyer AS buyer, "
        "COALESCE(o.style_name, o.style_ref) AS style, "
        "l.name AS line, a.status AS status, "
        "COALESCE(a.qty,0) AS qty, COALESCE(a.smv,0) AS smv, "
        f"{_MIN} AS minutes, "
        "a.start_date AS start_date, a.end_date AS end_date, "
        f"o.ship_date AS ship_date, {_LATE} AS late"
    ),
    frm=("pln_allocations a JOIN pln_lines l ON l.id = a.pline_id "
         "LEFT JOIN ord_orders o ON o.id = a.order_id"),
    order="a.start_date ASC, a.id ASC",
    date_col="a.start_date",
    columns=[
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("style", "Style", "الموديل", "Model"),
        R.col("line", "Line", "الخط", "Hat"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("qty", "Qty", "الكمية", "Miktar", "num",
              total="SUM(COALESCE(a.qty,0))"),
        R.col("smv", "SMV", "الدقيقة المعيارية", "SMV", "num"),
        R.col("minutes", "Minutes", "الدقائق", "Dakika", "num",
              total=f"SUM({_MIN})"),
        R.col("start_date", "Start", "البداية", "Başlangıç", "date"),
        R.col("end_date", "Projected finish", "الانتهاء المتوقع",
              "Öngörülen bitiş", "date"),
        R.col("ship_date", "Ship date", "تاريخ الشحن", "Sevk tarihi", "date"),
        R.col("late", "Late", "متأخر", "Geç", "num",
              total=f"SUM(COALESCE({_LATE},0))"),
    ],
    filters=[
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "o.buyer"),
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
        R.filt("line", "Line", "الخط", "Hat", "l.name"),
        R.filt("status", "Status", "الحالة", "Durum", "a.status", "select", "=",
               _STATUS_OPTS),
    ],
    kpis=[
        R.kpi("allocations", "Allocations", "التحميلات", "Yükleme", "COUNT(*)"),
        R.kpi("qty", "Planned qty", "الكمية المخططة", "Planlanan miktar",
              "SUM(COALESCE(a.qty,0))"),
        R.kpi("minutes", "Minutes", "الدقائق", "Dakika", f"SUM({_MIN})"),
        R.kpi("late", "Late allocations", "تحميلات متأخرة", "Geç yükleme",
              f"SUM(COALESCE({_LATE},0))", better="down"),
    ],
    chart=R.chart("trend", "a.start_date", "SUM(COALESCE(a.qty,0))",
                  "a.start_date", "Planned quantity by start date",
                  "الكمية المخططة حسب تاريخ البداية",
                  "Başlangıç tarihine göre planlanan miktar"),
))


# ---------------------------------------------------------------------------
# 3. Plan vs actual — planned quantity against what the floor actually booked
# ---------------------------------------------------------------------------
# Good pieces, not gross pieces: a rejected garment consumed the line's minutes
# but cannot be shipped, so it does not count towards the plan (services.measured).
# MAX(), not SUM(): the sub-query is one row per order and would otherwise be
# multiplied by the number of allocations on that order.
_MES = ("LEFT JOIN (SELECT order_id, "
        "SUM(COALESCE(actual_qty,0)) AS pcs, "
        "SUM(COALESCE(reject_qty,0)) AS rej "
        "FROM mes_hourly GROUP BY order_id) m ON m.order_id = a.order_id")
# m.pcs is deliberately NOT COALESCEd: no MES row at all must stay NULL, so the
# order reads "produced: blank" and not "0 produced, 100% behind". An order the
# floor HAS booked against, but with zero pieces, still reports a real 0.
# Floored at 0 like services.measured()['good']: more rejects than pieces is a
# keying error, and "-40 produced" is not a fact about a factory.
_GOOD = ("(CASE WHEN m.pcs - COALESCE(m.rej,0) < 0 THEN 0 "
         "ELSE m.pcs - COALESCE(m.rej,0) END)")

R.register(**_common(
    key="planning_progress",
    title="Plan vs actual output",
    title_ar="الخطة مقابل الإنتاج الفعلي",
    title_tr="Plan ve fiili üretim",
    desc="Planned quantity per order against the GOOD pieces the floor has "
         "booked (produced minus rejects), over the whole life of the order. "
         "Blank produced means the floor has booked nothing yet — not zero "
         "output.",
    desc_ar="الكمية المخططة لكل طلب مقابل القطع السليمة المسجلة من الإنتاج "
            "(المنتج ناقص المرفوض)، على عمر الطلب كاملاً. «المنتج» الفارغ يعني "
            "أنه لم يُسجَّل شيء بعد — وليس إنتاجاً صفرياً.",
    desc_tr="Sipariş başına planlanan miktar ile sahanın kaydettiği SAĞLAM "
            "adet (üretim eksi fire), siparişin tüm ömrü boyunca. Boş üretim, "
            "henüz kayıt girilmediği anlamına gelir — sıfır üretim değil.",
    select=(
        "o.order_no AS order_no, o.buyer AS buyer, "
        "COALESCE(o.style_name, o.style_ref) AS style, "
        "SUM(COALESCE(a.qty,0)) AS planned_qty, "
        f"MAX({_GOOD}) AS produced_qty, "
        "MAX(m.rej) AS rejects, "
        f"SUM(COALESCE(a.qty,0)) - MAX({_GOOD}) AS shortfall, "
        f"MAX({_GOOD}) / NULLIF(SUM(COALESCE(a.qty,0)), 0) * 100.0 AS progress_pct, "
        "MIN(a.start_date) AS start_date, MAX(a.end_date) AS end_date, "
        "o.ship_date AS ship_date"
    ),
    frm=("pln_allocations a LEFT JOIN ord_orders o ON o.id = a.order_id "
         f"{_MES}"),
    group="a.order_id, o.order_no, o.buyer, o.style_name, o.style_ref, o.ship_date",
    order="11 ASC, 1 ASC",
    # NO date range, deliberately. The two sides of this report live on different
    # clocks: the plan is dated by allocation, the actuals by mes_hourly.work_date
    # inside a pre-aggregated sub-query the engine's WHERE cannot reach. A window
    # therefore shrank the PLAN and left the OUTPUT whole — an order planned
    # 100 in July and 200 in August, with 240 good pieces booked, reported
    # "240 of 100 planned = 240% complete, shortfall -140". Whole-life on both
    # sides is the only pairing that is true. Filter by order or buyer instead.
    date_col=None,
    columns=[
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("style", "Style", "الموديل", "Model"),
        R.col("planned_qty", "Planned", "المخطط", "Planlanan", "num",
              total="SUM(planned_qty)"),
        R.col("produced_qty", "Produced (good)", "المنتج السليم", "Üretilen (sağlam)",
              "num", total="SUM(produced_qty)"),
        R.col("rejects", "Rejects", "المرفوض", "Fire", "num",
              total="SUM(rejects)"),
        R.col("shortfall", "Shortfall", "العجز", "Açık", "num",
              total="SUM(shortfall)"),
        R.col("progress_pct", "Progress %", "نسبة الإنجاز %", "İlerleme %", "num"),
        R.col("start_date", "Start", "البداية", "Başlangıç", "date"),
        R.col("end_date", "Projected finish", "الانتهاء المتوقع",
              "Öngörülen bitiş", "date"),
        R.col("ship_date", "Ship date", "تاريخ الشحن", "Sevk tarihi", "date"),
    ],
    filters=[
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "o.buyer"),
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
    ],
    kpis=[
        R.kpi("orders", "Orders planned", "طلبات مخططة", "Planlanan sipariş",
              "COUNT(*)"),
        R.kpi("planned", "Planned qty", "الكمية المخططة", "Planlanan miktar",
              "SUM(planned_qty)"),
        R.kpi("produced", "Produced (good)", "المنتج السليم", "Üretilen (sağlam)",
              "SUM(produced_qty)"),
        R.kpi("progress", "Progress %", "نسبة الإنجاز %", "İlerleme %",
              "SUM(produced_qty) / NULLIF(SUM(planned_qty), 0) * 100.0"),
    ],
    chart=R.chart("bar", "o.buyer", "SUM(COALESCE(a.qty,0))", "o.buyer",
                  "Planned quantity by buyer", "الكمية المخططة حسب العميل",
                  "Müşteriye göre planlanan miktar"),
))
