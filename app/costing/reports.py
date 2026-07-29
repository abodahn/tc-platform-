"""Order costing — report declarations for the shared reporting engine.

Declaration ONLY (no DB access at import; gunicorn runs --preload).

WHY THE ESTIMATE, AND NOT THE ACTUAL, IS REPORTED HERE
------------------------------------------------------
services._material_actual() picks the material actual from ONE of three sources
(warehouse issues / procurement receipts / manual entries) to avoid counting the
same fabric twice, and the warehouse side of that is a Python call into another
module. It cannot be reproduced faithfully in a single SQL statement, and a
"Actual" column that quietly disagreed with the cost sheet would be worse than
no column at all. So:

  * costing_orders  — the ESTIMATE and the margin it implies, computed with the
    same formulas as services._estimate() / cm_unit() / margin();
  * costing_actuals — the actuals that ARE booked in cst_actuals, named as such;
  * the reconciled estimate-vs-actual view stays on /costing (and its CSV),
    where the double-count guard runs.

The one deliberate difference from the screen: the engine sums in SQL without
the per-line 2-dp rounding the cost sheet applies, so a large BOM can differ by
a cent or two. Rounding inside SQL is not portable between SQLite and
PostgreSQL, and a cent is not worth a per-dialect expression.
"""
from app.services import reporting as R

MODULE = "costing"
LABEL_EN, LABEL_AR, LABEL_TR = "Costing", "التكاليف", "Maliyetlendirme"
PERM = "cost_view"

# --- shared SQL fragments ---------------------------------------------------
# Material cost per finished garment: consumption x (1 + allowance%) x price.
# Aggregated per unit in a sub-query so it can simply be multiplied by the order
# quantity outside — identical arithmetic to services.required_qty().
_BOM = ("LEFT JOIN (SELECT order_id, "
        "SUM(COALESCE(consumption,0) * (1 + COALESCE(allowance_pct,0)/100.0) "
        "    * COALESCE(unit_price,0)) AS mat_unit, "
        "SUM(CASE WHEN unit_price IS NULL THEN 1 ELSE 0 END) AS unpriced "
        "FROM cst_bom_lines GROUP BY order_id) b ON b.order_id = o.id")

# CM per unit = SMV x rate when both are set, else the flat figure (cm_unit()).
_CM = ("(CASE WHEN COALESCE(s.smv,0) > 0 AND COALESCE(s.cm_rate,0) > 0 "
       "THEN s.smv * s.cm_rate ELSE COALESCE(s.cm_per_unit,0) END)")
_OTHERS = ("(COALESCE(s.overhead_per_unit,0) + COALESCE(s.freight_per_unit,0) "
           "+ COALESCE(s.duty_per_unit,0) + COALESCE(s.other_per_unit,0))")
_UNIT_COST = f"(COALESCE(b.mat_unit,0) + {_CM} + {_OTHERS})"
_QTY = "COALESCE(o.qty,0)"
_EST = f"({_QTY} * {_UNIT_COST})"
_REV = f"({_QTY} * COALESCE(o.unit_price,0))"
_MARGIN = f"({_REV} - {_EST})"

_KIND_OPTS = [
    ("fabric", "Fabric", "قماش", "Kumaş"),
    ("trim", "Trim", "إكسسوارات", "Aksesuar"),
    ("other", "Other", "أخرى", "Diğer"),
]
_CAT_OPTS = [
    ("material", "Material", "الخامات", "Malzeme"),
    ("cm", "CM", "تكلفة التصنيع", "Dikim (CM)"),
    ("overhead", "Overhead", "التكاليف غير المباشرة", "Genel gider"),
    ("freight", "Freight", "الشحن", "Navlun"),
    ("duty", "Duty", "الجمارك", "Gümrük"),
    ("other", "Other", "أخرى", "Diğer"),
]


def _common(**kw):
    kw.setdefault("module", MODULE)
    kw.setdefault("module_label", LABEL_EN)
    kw.setdefault("module_label_ar", LABEL_AR)
    kw.setdefault("module_label_tr", LABEL_TR)
    kw.setdefault("perm", PERM)
    return kw


# ---------------------------------------------------------------------------
# 1. Estimated cost & margin per order
# ---------------------------------------------------------------------------
R.register(**_common(
    key="costing_orders",
    title="Estimated cost & margin by order",
    title_ar="التكلفة التقديرية وهامش الربح لكل طلب",
    title_tr="Siparişe göre tahmini maliyet ve marj",
    desc="Only orders that have been costed (a BOM or a cost sheet). Unpriced "
         "BOM lines are counted so an incomplete — and therefore flattering — "
         "estimate is visible. Amounts are in each order's own currency.",
    desc_ar="فقط الطلبات التي جرى تسعيرها (قائمة مواد أو ورقة تكلفة). تُحسب بنود "
            "قائمة المواد غير المسعّرة حتى يظهر التقدير الناقص — والمضلل — بوضوح. "
            "المبالغ بعملة كل طلب.",
    desc_tr="Yalnızca maliyetlendirilmiş siparişler (BOM veya maliyet kartı "
            "olan). Fiyatsız BOM satırları sayılır, böylece eksik ve bu yüzden "
            "iyimser tahmin görünür. Tutarlar siparişin kendi para biriminde.",
    select=(
        "o.order_no AS order_no, o.buyer AS buyer, "
        "COALESCE(o.style_name, o.style_ref) AS style, "
        "o.currency AS currency, o.ship_date AS ship_date, "
        f"{_QTY} AS qty, "
        f"{_REV} AS revenue, "
        "COALESCE(b.mat_unit,0) * COALESCE(o.qty,0) AS material, "
        f"{_CM} * {_QTY} AS cm, "
        f"{_OTHERS} * {_QTY} AS overheads, "
        f"{_EST} AS est_cost, "
        f"{_UNIT_COST} AS unit_cost, "
        f"{_MARGIN} AS margin, "
        f"{_MARGIN} / NULLIF({_REV}, 0) * 100.0 AS margin_pct, "
        "COALESCE(b.unpriced,0) AS unpriced"
    ),
    frm=("ord_orders o "
         "LEFT JOIN cst_sheets s ON s.order_id = o.id "
         f"{_BOM}"),
    # Uncosted orders would report a zero cost and a 100% margin. Never show them.
    base_where=["(b.order_id IS NOT NULL OR s.order_id IS NOT NULL)"],
    order="o.ship_date ASC, o.id DESC",
    date_col="o.ship_date",
    columns=[
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("style", "Style", "الموديل", "Model"),
        R.col("currency", "Currency", "العملة", "Para birimi"),
        R.col("qty", "Qty", "الكمية", "Miktar", "num", total=f"SUM({_QTY})"),
        R.col("revenue", "Revenue", "الإيراد", "Gelir", "num",
              total=f"SUM({_REV})"),
        R.col("material", "Material", "الخامات", "Malzeme", "num",
              total="SUM(COALESCE(b.mat_unit,0) * COALESCE(o.qty,0))"),
        R.col("cm", "CM", "تكلفة التصنيع", "Dikim (CM)", "num",
              total=f"SUM({_CM} * {_QTY})"),
        R.col("overheads", "Overheads & logistics", "التكاليف الأخرى",
              "Genel gider ve lojistik", "num", total=f"SUM({_OTHERS} * {_QTY})"),
        R.col("est_cost", "Estimated cost", "التكلفة التقديرية",
              "Tahmini maliyet", "num", total=f"SUM({_EST})"),
        R.col("unit_cost", "Cost / unit", "التكلفة للوحدة", "Birim maliyet", "num"),
        R.col("margin", "Margin", "هامش الربح", "Marj", "num",
              total=f"SUM({_MARGIN})"),
        R.col("margin_pct", "Margin %", "هامش الربح %", "Marj %", "num"),
        R.col("unpriced", "Unpriced BOM lines", "بنود بلا سعر", "Fiyatsız satır",
              "num", total="SUM(COALESCE(b.unpriced,0))"),
        R.col("ship_date", "Ship date", "تاريخ الشحن", "Sevk tarihi", "date"),
    ],
    filters=[
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "o.buyer"),
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
        R.filt("currency", "Currency", "العملة", "Para birimi", "o.currency",
               "text", "="),
    ],
    kpis=[
        R.kpi("orders", "Orders costed", "طلبات مسعّرة", "Maliyetlenen sipariş",
              "COUNT(*)"),
        R.kpi("revenue", "Revenue", "الإيراد", "Gelir", f"SUM({_REV})"),
        R.kpi("cost", "Estimated cost", "التكلفة التقديرية", "Tahmini maliyet",
              f"SUM({_EST})", better="down"),
        R.kpi("margin", "Margin", "هامش الربح", "Marj", f"SUM({_MARGIN})"),
        R.kpi("margin_pct", "Margin %", "هامش الربح %", "Marj %",
              f"SUM({_MARGIN}) / NULLIF(SUM({_REV}), 0) * 100.0"),
        R.kpi("unpriced", "Unpriced BOM lines", "بنود بلا سعر", "Fiyatsız satır",
              "SUM(COALESCE(b.unpriced,0))", better="down"),
    ],
    chart=R.chart("bar", "o.buyer", f"SUM({_MARGIN})", "o.buyer",
                  "Estimated margin by buyer", "الهامش التقديري حسب العميل",
                  "Müşteriye göre tahmini marj"),
))


# ---------------------------------------------------------------------------
# 2. BOM material plan — what has to be bought, and from whom
# ---------------------------------------------------------------------------
_REQ_QTY = ("(COALESCE(o.qty,0) * COALESCE(l.consumption,0) "
            "* (1 + COALESCE(l.allowance_pct,0)/100.0))")
_LINE_COST = f"({_REQ_QTY} * COALESCE(l.unit_price,0))"

R.register(**_common(
    key="costing_bom",
    title="BOM material plan & supplier exposure",
    title_ar="خطة الخامات والانكشاف على الموردين",
    title_tr="BOM malzeme planı ve tedarikçi riski",
    desc="Every BOM line costed out to the order quantity, including the "
         "cutting allowance. Required qty = order qty x consumption x "
         "(1 + allowance %).",
    desc_ar="كل بند في قائمة المواد محسوباً على كمية الطلب، شاملاً نسبة الهدر. "
            "الكمية المطلوبة = كمية الطلب × الاستهلاك × (١ + نسبة الهدر ٪).",
    desc_tr="Her BOM satırı sipariş miktarına göre maliyetlendirilir, kesim "
            "payı dahil. Gerekli miktar = sipariş x tüketim x (1 + fire %).",
    select=(
        "o.order_no AS order_no, o.buyer AS buyer, l.item AS item, "
        "l.kind AS kind, l.colour AS colour, l.supplier AS supplier, "
        "l.uom AS uom, COALESCE(l.consumption,0) AS consumption, "
        "COALESCE(l.allowance_pct,0) AS allowance_pct, "
        f"{_REQ_QTY} AS required_qty, "
        "l.unit_price AS unit_price, "
        f"{_LINE_COST} AS line_cost, o.currency AS currency"
    ),
    frm="cst_bom_lines l JOIN ord_orders o ON o.id = l.order_id",
    order="o.order_no ASC, l.seq ASC, l.id ASC",
    date_col="l.created_at",
    columns=[
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("item", "Material", "الخامة", "Malzeme"),
        R.col("kind", "Kind", "النوع", "Tür"),
        R.col("colour", "Colour", "اللون", "Renk"),
        R.col("supplier", "Supplier", "المورد", "Tedarikçi"),
        R.col("uom", "UoM", "الوحدة", "Birim"),
        R.col("consumption", "Consumption / unit", "الاستهلاك للوحدة",
              "Birim tüketim", "num"),
        R.col("allowance_pct", "Allowance %", "نسبة الهدر %", "Fire %", "num"),
        R.col("required_qty", "Required qty", "الكمية المطلوبة", "Gerekli miktar",
              "num", total=f"SUM({_REQ_QTY})"),
        R.col("unit_price", "Unit price", "سعر الوحدة", "Birim fiyat", "num"),
        R.col("line_cost", "Line cost", "تكلفة البند", "Satır maliyeti", "num",
              total=f"SUM({_LINE_COST})"),
        R.col("currency", "Currency", "العملة", "Para birimi"),
    ],
    filters=[
        R.filt("supplier", "Supplier", "المورد", "Tedarikçi", "l.supplier"),
        R.filt("kind", "Kind", "النوع", "Tür", "l.kind", "select", "=", _KIND_OPTS),
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
        R.filt("item", "Material", "الخامة", "Malzeme", "l.item"),
    ],
    kpis=[
        R.kpi("lines", "BOM lines", "عدد البنود", "BOM satırı", "COUNT(*)"),
        R.kpi("cost", "Material cost", "تكلفة الخامات", "Malzeme maliyeti",
              f"SUM({_LINE_COST})", better="down"),
        R.kpi("unpriced", "Unpriced lines", "بنود بلا سعر", "Fiyatsız satır",
              "SUM(CASE WHEN l.unit_price IS NULL THEN 1 ELSE 0 END)",
              better="down"),
        R.kpi("suppliers", "Suppliers", "عدد الموردين", "Tedarikçi sayısı",
              "COUNT(DISTINCT l.supplier)"),
    ],
    chart=R.chart("pareto", "l.supplier", f"SUM({_LINE_COST})", "l.supplier",
                  "Material cost by supplier", "تكلفة الخامات حسب المورد",
                  "Tedarikçiye göre malzeme maliyeti"),
))


# ---------------------------------------------------------------------------
# 3. Booked actual costs
# ---------------------------------------------------------------------------
R.register(**_common(
    key="costing_actuals",
    title="Booked actual costs",
    title_ar="التكاليف الفعلية المسجلة",
    title_tr="Kaydedilen fiili maliyetler",
    desc="Actual costs entered against orders (cst_actuals) only. The cost "
         "sheet's material actual may instead come from procurement receipts "
         "or warehouse issues, so it can legitimately differ from this total.",
    desc_ar="التكاليف الفعلية المُدخلة على الطلبات فقط. قد تأتي تكلفة الخامات "
            "الفعلية في ورقة التكلفة من مستلمات المشتريات أو صرف المخازن، "
            "لذا قد تختلف عن هذا الإجمالي بشكل مشروع.",
    desc_tr="Yalnızca siparişlere elle girilen fiili maliyetler. Maliyet "
            "kartındaki fiili malzeme, satın alma mal kabulü veya depo "
            "çıkışından gelebilir; bu nedenle bu toplamdan farklı olabilir.",
    select=(
        "o.order_no AS order_no, o.buyer AS buyer, a.category AS category, "
        "COALESCE(a.amount,0) AS amount, o.currency AS currency, "
        "a.source AS source, a.created_by AS created_by, "
        "a.created_at AS created_at"
    ),
    frm="cst_actuals a JOIN ord_orders o ON o.id = a.order_id",
    order="a.id DESC",
    date_col="a.created_at",
    columns=[
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("category", "Category", "الفئة", "Kategori"),
        R.col("amount", "Amount", "المبلغ", "Tutar", "num",
              total="SUM(COALESCE(a.amount,0))"),
        R.col("currency", "Currency", "العملة", "Para birimi"),
        R.col("source", "Source", "المصدر", "Kaynak"),
        R.col("created_by", "Booked by", "سجّلها", "Kaydeden"),
        R.col("created_at", "Booked", "تاريخ التسجيل", "Kayıt tarihi", "date"),
    ],
    filters=[
        R.filt("category", "Category", "الفئة", "Kategori", "a.category",
               "select", "=", _CAT_OPTS),
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "o.buyer"),
    ],
    kpis=[
        R.kpi("entries", "Entries", "عدد القيود", "Kayıt sayısı", "COUNT(*)"),
        R.kpi("amount", "Booked cost", "التكلفة المسجلة", "Kaydedilen maliyet",
              "SUM(COALESCE(a.amount,0))", better="down"),
        R.kpi("orders", "Orders affected", "طلبات متأثرة", "Etkilenen sipariş",
              "COUNT(DISTINCT a.order_id)"),
    ],
    chart=R.chart("bar", "a.category", "SUM(COALESCE(a.amount,0))", "a.category",
                  "Booked cost by category", "التكلفة المسجلة حسب الفئة",
                  "Kategoriye göre kaydedilen maliyet"),
))
