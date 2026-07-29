"""
Cut room — report declarations for the shared reporting engine.

Pure data, declared at import time; no DB work here (gunicorn --preload).

The fabric arithmetic is services.lay_metrics(), expressed in SQL so the engine
can aggregate it. Same formulas, same fallbacks:
    pieces        = plies x pieces_per_ply
    theoretical_m = marker_length x plies
    planned_m     = (marker_length + end_allow) x plies
    used_m        = measured actual when > 0, else planned
    utilisation % = theoretical / used x 100        waste % = 100 - utilisation
    marker eff %  = CAD area / (marker length x width) x 100, else the entered %
Cancelled lays are excluded exactly as services._rollup() excludes them, with
COALESCE(status,'') so a NULL status is not silently dropped.

NOT declared, for lack of data — say so rather than fake it:
  * Fabric variance vs the BOM plan (the cut room's headline KPI). The planned
    consumption per garment lives in the COSTING module's BOM and is read
    defensively there (services._planned_cpg_map) because the module may be
    absent; a hard SQL join to another module's tables would 500 this page on any
    install without costing. /cutroom and the order-summary CSV carry it.
  * Cut-to-sew balance. mes_bundles owns the sewn side; it is reported from the
    MES side, not here.
"""
from app.cutroom.constants import LAY_STATUS
from app.services import reporting as R

MOD = dict(module="cutroom", module_label="Cut Room",
           module_label_ar="غرفة القص", module_label_tr="Kesim Odası",
           perm="cut_view")

# COALESCE on every input: a hand-inserted NULL must read as 0, not blank out the
# whole row.
_PLIES = "COALESCE(cl.plies, 0)"
_PPP = "COALESCE(cl.pieces_per_ply, 0)"
_ML = "COALESCE(cl.marker_length_m, 0)"
_END = "COALESCE(cl.end_allow_m, 0)"
_ACT = "COALESCE(cl.actual_fabric_m, 0)"
_AREA = "COALESCE(cl.marker_area_m2, 0)"
_MW = "COALESCE(cl.marker_width_cm, 0)"

PIECES = f"({_PLIES} * {_PPP})"
THEO = f"({_ML} * {_PLIES})"
PLANNED = f"(({_ML} + {_END}) * {_PLIES})"
USED = f"(CASE WHEN {_ACT} > 0 THEN {_ACT} ELSE {PLANNED} END)"
UTIL = f"ROUND(CAST(100.0 * {THEO} / NULLIF({USED}, 0) AS NUMERIC), 2)"
WASTE = f"ROUND(CAST(100.0 - 100.0 * {THEO} / NULLIF({USED}, 0) AS NUMERIC), 2)"
# NULLIF on the marker rectangle, not a CASE guard: a CASE arm is not a promise
# the divisor is never evaluated.
EFF = (f"CASE WHEN {_AREA} > 0 THEN "
       f"ROUND(CAST(100.0 * {_AREA} / NULLIF({_ML} * {_MW} / 100.0, 0) AS NUMERIC), 1) "
       "WHEN COALESCE(cl.marker_eff_pct, 0) > 0 THEN "
       "ROUND(CAST(cl.marker_eff_pct AS NUMERIC), 1) END")

LIVE = "COALESCE(cl.status, '') != 'cancelled'"
FRM = "cut_lays cl LEFT JOIN ord_orders o ON o.id = cl.order_id"

# --------------------------------------------------------------------------
# 1. Lay register — fabric utilisation and wastage, one row per lay
# --------------------------------------------------------------------------
R.register(
    key="cutroom_utilisation", **MOD,
    title="Fabric utilisation by lay", title_ar="استغلال القماش لكل فرشة",
    title_tr="Serim bazında kumaş kullanımı",
    desc="Every lay with its marker efficiency, metres used per garment and wastage.",
    desc_ar="كل فرشة مع كفاءة الماركر والأمتار لكل قطعة ونسبة الهدر.",
    desc_tr="Her serim: marker verimliliği, parça başına metre ve fire oranı.",
    select=(f"cl.lay_no AS lay_no, cl.cut_date AS cut_date, o.order_no AS order_no, "
            f"cl.fabric_ref AS fabric_ref, cl.colour AS colour, cl.shade_lot AS shade_lot, "
            f"cl.status AS status, {_PLIES} AS plies, {PIECES} AS pieces, "
            f"ROUND(CAST({USED} AS NUMERIC), 2) AS used_m, "
            f"ROUND(CAST({USED} / NULLIF({PIECES}, 0) AS NUMERIC), 4) AS cons, "
            f"{EFF} AS marker_eff, {UTIL} AS util, {WASTE} AS waste"),
    frm=FRM,
    base_where=[LIVE],
    date_col="cl.cut_date",
    order="cl.cut_date DESC, cl.id DESC",
    columns=[
        R.col("lay_no", "Lay", "الفرشة", "Serim"),
        R.col("cut_date", "Cut date", "تاريخ القص", "Kesim tarihi", "date"),
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("fabric_ref", "Fabric", "القماش", "Kumaş"),
        R.col("colour", "Colour", "اللون", "Renk"),
        R.col("shade_lot", "Shade lot", "دفعة الصبغة", "Parti"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("plies", "Plies", "الطبقات", "Kat", "num", total=f"SUM({_PLIES})"),
        R.col("pieces", "Pieces cut", "القطع المقصوصة", "Kesilen adet", "num",
              total=f"SUM({PIECES})"),
        R.col("used_m", "Fabric used (m)", "القماش المستهلك (م)", "Kullanılan kumaş (m)",
              "num", total=f"ROUND(CAST(SUM({USED}) AS NUMERIC), 2)"),
        R.col("cons", "m / garment", "م لكل قطعة", "m / parça", "num"),
        R.col("marker_eff", "Marker eff %", "كفاءة الماركر %", "Marker verimi %", "num"),
        R.col("util", "Utilisation %", "الاستغلال %", "Kullanım %", "num"),
        R.col("waste", "Waste %", "الهدر %", "Fire %", "num"),
    ],
    filters=[
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
        R.filt("fabric_ref", "Fabric", "القماش", "Kumaş", "cl.fabric_ref"),
        R.filt("shade_lot", "Shade lot", "دفعة الصبغة", "Parti", "cl.shade_lot"),
        R.filt("status", "Status", "الحالة", "Durum", "cl.status", "select", "=",
               [s for s in LAY_STATUS if s != "cancelled"]),
    ],
    kpis=[
        R.kpi("lays", "Lays", "الفرشات", "Serim", "COUNT(*)", better="none"),
        R.kpi("pieces", "Pieces cut", "القطع المقصوصة", "Kesilen adet", f"SUM({PIECES})"),
        R.kpi("used_m", "Fabric used (m)", "القماش المستهلك (م)", "Kullanılan kumaş (m)",
              f"ROUND(CAST(SUM({USED}) AS NUMERIC), 1)", better="down"),
        R.kpi("util", "Utilisation %", "الاستغلال %", "Kullanım %",
              f"ROUND(CAST(100.0 * SUM({THEO}) / NULLIF(SUM({USED}), 0) AS NUMERIC), 2)"),
        R.kpi("waste_m", "Metres wasted", "الأمتار المهدرة", "Fire metre",
              f"ROUND(CAST(SUM({USED} - {THEO}) AS NUMERIC), 1)", better="down"),
    ],
    chart=R.chart("bar", "o.order_no", f"ROUND(CAST(SUM({USED} - {THEO}) AS NUMERIC), 1)",
                  "o.order_no", "Metres wasted by order", "الأمتار المهدرة حسب الطلب",
                  "Siparişe göre fire metre"),
)

# --------------------------------------------------------------------------
# 2. Cut plan vs actual — pieces cut against the order quantity
# --------------------------------------------------------------------------
# MAX(o.qty) not SUM: the order quantity is a property of the order, and the
# group is one order, so summing it once per lay would multiply it.
R.register(
    key="cutroom_vs_order", **MOD,
    title="Cut plan vs actual", title_ar="خطة القص مقابل الفعلي",
    title_tr="Kesim planı / gerçekleşen",
    desc="Pieces cut against the ordered quantity, with the short- or over-cut balance.",
    desc_ar="القطع المقصوصة مقابل الكمية المطلوبة مع الفارق بالنقص أو الزيادة.",
    desc_tr="Sipariş adedine karşı kesilen adet ve eksik/fazla kesim farkı.",
    select=("o.order_no AS order_no, o.buyer AS buyer, COUNT(*) AS lays, "
            f"SUM({PIECES}) AS pieces, MAX(COALESCE(o.qty, 0)) AS order_qty, "
            f"ROUND(CAST(100.0 * SUM({PIECES}) / NULLIF(MAX(COALESCE(o.qty, 0)), 0) "
            "AS NUMERIC), 1) AS cut_pct, "
            f"(MAX(COALESCE(o.qty, 0)) - SUM({PIECES})) AS balance, "
            f"ROUND(CAST(SUM({USED}) AS NUMERIC), 2) AS used_m, "
            f"ROUND(CAST(SUM({USED}) / NULLIF(SUM({PIECES}), 0) AS NUMERIC), 4) AS cons"),
    frm=FRM,
    base_where=[LIVE, "cl.order_id IS NOT NULL"],
    group="cl.order_id, o.order_no, o.buyer",
    order="cut_pct ASC",
    date_col="cl.cut_date",
    columns=[
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("lays", "Lays", "الفرشات", "Serim", "num", total="SUM(lays)"),
        R.col("order_qty", "Ordered", "الكمية المطلوبة", "Sipariş adedi", "num",
              total="SUM(order_qty)"),
        R.col("pieces", "Cut", "المقصوص", "Kesilen", "num", total="SUM(pieces)"),
        R.col("cut_pct", "Cut %", "نسبة القص %", "Kesim %", "num"),
        R.col("balance", "Balance", "الفارق", "Fark", "num", total="SUM(balance)"),
        R.col("used_m", "Fabric used (m)", "القماش المستهلك (م)", "Kullanılan kumaş (m)",
              "num", total="SUM(used_m)"),
        R.col("cons", "m / garment", "م لكل قطعة", "m / parça", "num"),
    ],
    filters=[
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "o.buyer"),
    ],
    kpis=[
        R.kpi("orders", "Orders", "الطلبات", "Sipariş", "COUNT(*)", better="none"),
        R.kpi("order_qty", "Ordered", "الكمية المطلوبة", "Sipariş adedi",
              "SUM(order_qty)", better="none"),
        R.kpi("pieces", "Cut", "المقصوص", "Kesilen", "SUM(pieces)"),
        R.kpi("cut_pct", "Cut %", "نسبة القص %", "Kesim %",
              "ROUND(CAST(100.0 * SUM(pieces) / NULLIF(SUM(order_qty), 0) AS NUMERIC), 1)"),
        R.kpi("short", "Orders short-cut", "طلبات ناقصة القص", "Eksik kesilen sipariş",
              "SUM(CASE WHEN balance > 0 THEN 1 ELSE 0 END)", better="down"),
    ],
)
