"""
Wash / finishing — report declarations for the shared reporting engine.

Pure data, declared at import time; no DB work here (gunicorn --preload).

Planned cycle time comes from the version's own steps (SUM(wsh_steps.minutes)),
pre-grouped per version so the join stays 1:1 with the batch — joining the steps
raw would multiply every batch by its step count and treble the kilos.
A batch is traced to its VERSION, never to the recipe head, exactly as the module
records it: that is what lets a shade be re-run after the recipe moved on.

NOT declared, for lack of data — say so rather than fake it:
  * Reprocess / rewash rate. BATCH_STATUS carries a 'rewash' value, but nothing
    links a rewash back to the batch it re-does, so a rewash cannot be attributed
    to the recipe, machine or lot that caused it. Counting bare 'rewash' rows
    would be a number nobody can act on. Add a `rewash_of` column and this report
    becomes honest.
  * Water and chemical consumption per kg. act_water_l is metered only when the
    machine reports it (mostly 0), and the chemical dosing is per bath in the
    version, not per executed batch. The internal impact index on /wash is the
    honest surface for that.
"""
from app.services import reporting as R
from app.wash.constants import BATCH_STATUS, MACHINES, WASH_TYPES

MOD = dict(module="wash", module_label="Wash & Finishing",
           module_label_ar="الغسيل والتجهيز", module_label_tr="Yıkama ve Finisaj",
           perm="wsh_view")

_PLAN = ("LEFT JOIN (SELECT version_id, SUM(minutes) AS plan_min FROM wsh_steps "
         "GROUP BY version_id) s ON s.version_id = b.version_id")
FRM = ("wsh_batches b "
       "LEFT JOIN wsh_recipes r ON r.id = b.recipe_id "
       "LEFT JOIN wsh_versions v ON v.id = b.version_id "
       f"{_PLAN} "
       "LEFT JOIN ord_orders o ON o.id = b.order_id")

_DEV = "CASE WHEN COALESCE(b.deviation, '') != '' THEN 1 ELSE 0 END"

# --------------------------------------------------------------------------
# 1. Batch register — throughput and cycle time against the recipe
# --------------------------------------------------------------------------
R.register(
    key="wash_batches", **MOD,
    title="Wash batch throughput", title_ar="إنتاجية دفعات الغسيل",
    title_tr="Yıkama parti verimi",
    desc="Every batch with its load, actual cycle time against the recipe plan and deviations.",
    desc_ar="كل دفعة مع الحمولة وزمن الدورة الفعلي مقابل خطة الوصفة والانحرافات.",
    desc_tr="Her parti: yük, reçete planına karşı gerçek çevrim süresi ve sapmalar.",
    select=("b.batch_no AS batch_no, substr(b.started_at, 1, 10) AS run_date, "
            "b.machine AS machine, r.code AS recipe, v.version AS version, "
            "o.order_no AS order_no, COALESCE(b.load_kg, 0) AS load_kg, "
            "COALESCE(b.act_minutes, 0) AS act_min, COALESCE(s.plan_min, 0) AS plan_min, "
            "ROUND(CAST(100.0 * COALESCE(b.act_minutes, 0) / NULLIF(s.plan_min, 0) - 100.0 "
            "AS NUMERIC), 1) AS time_var, "
            "COALESCE(b.act_temp_c, 0) AS act_temp, b.status AS status, "
            "b.deviation AS deviation"),
    frm=FRM,
    date_col="b.started_at",
    order="b.started_at DESC, b.id DESC",
    columns=[
        R.col("batch_no", "Batch", "الدفعة", "Parti"),
        R.col("run_date", "Date", "التاريخ", "Tarih", "date"),
        R.col("machine", "Machine", "الماكينة", "Makine"),
        R.col("recipe", "Recipe", "الوصفة", "Reçete"),
        R.col("version", "Version", "الإصدار", "Versiyon", "num"),
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("load_kg", "Load (kg)", "الحمولة (كجم)", "Yük (kg)", "num",
              total="ROUND(CAST(SUM(COALESCE(b.load_kg, 0)) AS NUMERIC), 1)"),
        R.col("act_min", "Actual min", "الدقائق الفعلية", "Gerçek dakika", "num",
              total="ROUND(CAST(SUM(COALESCE(b.act_minutes, 0)) AS NUMERIC), 0)"),
        R.col("plan_min", "Plan min", "الدقائق المخططة", "Plan dakika", "num",
              total="ROUND(CAST(SUM(COALESCE(s.plan_min, 0)) AS NUMERIC), 0)"),
        R.col("time_var", "Time var %", "انحراف الزمن %", "Süre sapması %", "num"),
        R.col("act_temp", "Peak °C", "أعلى حرارة °م", "Tepe °C", "num"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("deviation", "Deviation", "الانحراف", "Sapma"),
    ],
    filters=[
        R.filt("machine", "Machine", "الماكينة", "Makine", "b.machine", "select", "=",
               MACHINES),
        R.filt("status", "Status", "الحالة", "Durum", "b.status", "select", "=",
               BATCH_STATUS),
        R.filt("wash_type", "Wash type", "نوع الغسيل", "Yıkama tipi", "r.wash_type",
               "select", "=", WASH_TYPES),
        R.filt("recipe", "Recipe", "الوصفة", "Reçete", "r.code"),
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
    ],
    kpis=[
        R.kpi("batches", "Batches", "الدفعات", "Parti", "COUNT(*)"),
        R.kpi("load_kg", "Load (kg)", "الحمولة (كجم)", "Yük (kg)",
              "ROUND(CAST(SUM(COALESCE(b.load_kg, 0)) AS NUMERIC), 1)"),
        R.kpi("avg_min", "Avg cycle min", "متوسط زمن الدورة", "Ort. çevrim dakika",
              "ROUND(CAST(AVG(COALESCE(b.act_minutes, 0)) AS NUMERIC), 1)", better="down"),
        R.kpi("deviations", "Deviations", "الانحرافات", "Sapmalar",
              f"SUM({_DEV})", better="down"),
        R.kpi("clean_pct", "On-spec %", "المطابق للمواصفة %", "Spesifikasyona uygun %",
              f"ROUND(CAST(100.0 * SUM(1 - {_DEV}) / NULLIF(COUNT(*), 0) AS NUMERIC), 1)"),
    ],
    chart=R.chart("bar", "b.machine", "ROUND(CAST(SUM(COALESCE(b.load_kg, 0)) AS NUMERIC), 1)",
                  "b.machine", "Load by machine", "الحمولة حسب الماكينة",
                  "Makineye göre yük"),
)

# --------------------------------------------------------------------------
# 2. Throughput per recipe version — which recipe is slow, and how slow
# --------------------------------------------------------------------------
R.register(
    key="wash_recipe_throughput", **MOD,
    title="Throughput by recipe version", title_ar="الإنتاجية حسب إصدار الوصفة",
    title_tr="Reçete versiyonuna göre verim",
    desc="Kilos washed and average cycle time per recipe version against its planned cycle.",
    desc_ar="الكيلوغرامات المغسولة ومتوسط زمن الدورة لكل إصدار مقابل الزمن المخطط.",
    desc_tr="Reçete versiyonu başına yıkanan kilo ve planlanan çevrime karşı ortalama süre.",
    select=("r.code AS recipe, r.name AS recipe_name, r.wash_type AS wash_type, "
            "v.version AS version, COUNT(*) AS batches, "
            "ROUND(CAST(SUM(COALESCE(b.load_kg, 0)) AS NUMERIC), 1) AS load_kg, "
            "ROUND(CAST(AVG(COALESCE(b.act_minutes, 0)) AS NUMERIC), 1) AS avg_min, "
            "ROUND(CAST(MAX(COALESCE(s.plan_min, 0)) AS NUMERIC), 1) AS plan_min, "
            "ROUND(CAST(100.0 * AVG(COALESCE(b.act_minutes, 0)) "
            "/ NULLIF(MAX(COALESCE(s.plan_min, 0)), 0) - 100.0 AS NUMERIC), 1) AS time_var, "
            f"SUM({_DEV}) AS deviations"),
    frm=FRM,
    group="r.code, r.name, r.wash_type, v.version",
    order="load_kg DESC",
    date_col="b.started_at",
    columns=[
        R.col("recipe", "Recipe", "الوصفة", "Reçete"),
        R.col("recipe_name", "Name", "الاسم", "Ad"),
        R.col("wash_type", "Wash type", "نوع الغسيل", "Yıkama tipi"),
        R.col("version", "Version", "الإصدار", "Versiyon", "num"),
        R.col("batches", "Batches", "الدفعات", "Parti", "num", total="SUM(batches)"),
        R.col("load_kg", "Load (kg)", "الحمولة (كجم)", "Yük (kg)", "num",
              total="ROUND(CAST(SUM(load_kg) AS NUMERIC), 1)"),
        R.col("avg_min", "Avg cycle min", "متوسط زمن الدورة", "Ort. çevrim dakika", "num"),
        R.col("plan_min", "Plan min", "الدقائق المخططة", "Plan dakika", "num"),
        R.col("time_var", "Time var %", "انحراف الزمن %", "Süre sapması %", "num"),
        R.col("deviations", "Deviations", "الانحرافات", "Sapmalar", "num",
              total="SUM(deviations)"),
    ],
    filters=[
        R.filt("wash_type", "Wash type", "نوع الغسيل", "Yıkama tipi", "r.wash_type",
               "select", "=", WASH_TYPES),
        R.filt("recipe", "Recipe", "الوصفة", "Reçete", "r.code"),
    ],
    kpis=[
        R.kpi("versions", "Versions run", "الإصدارات المنفذة", "Çalışan versiyon",
              "COUNT(*)", better="none"),
        R.kpi("batches", "Batches", "الدفعات", "Parti", "SUM(batches)"),
        R.kpi("load_kg", "Load (kg)", "الحمولة (كجم)", "Yük (kg)",
              "ROUND(CAST(SUM(load_kg) AS NUMERIC), 1)"),
        R.kpi("deviations", "Deviations", "الانحرافات", "Sapmalar",
              "SUM(deviations)", better="down"),
    ],
    chart=R.chart("bar", "r.code", "ROUND(CAST(SUM(COALESCE(b.load_kg, 0)) AS NUMERIC), 1)",
                  "r.code", "Load by recipe", "الحمولة حسب الوصفة",
                  "Reçeteye göre yük"),
)
