"""
Shop-floor MES — report declarations for the shared reporting engine.

Pure data: this module runs at import time and MUST NOT touch the database
(gunicorn --preload boots before the port is bound).

Every number here is the SAME arithmetic the MES screens use
(app/mes/services.py), expressed once in SQL so the engine can aggregate it:
    achievement % = actual / target x 100          (services.achievement)
    red hour      = achievement < RAG_AMBER        (services.rag_for)
    efficiency %  = earned minutes / operator minutes x 100
                    earned = good pcs x SMV, operator = operators x 60
                                                    (services.efficiency)
Portability: ROUND(double, n) does not exist on PostgreSQL, so every rounded
expression is CAST(... AS NUMERIC) first; every division is guarded with NULLIF
so an empty or zero-target period returns NULL (rendered blank), never a
division-by-zero.

NOT declared, for lack of data — say so rather than fake it:
  * OEE by line. Availability needs planned minutes and Performance needs ideal
    cycle time; services._rollup() already derives both in Python, and a second
    implementation in SQL would eventually disagree with the board the factory
    argues about. Use /mes and the oee-by-line CSV.
  * Operator-level efficiency. mes_hourly records operators as a COUNT, not who
    they were; there is no operator dimension to group on.
"""
from app.mes.constants import DOWNTIME_REASONS, HOUR_SLOTS, RAG_AMBER
from app.services import reporting as R

MOD = dict(module="mes", module_label="Shop Floor",
           module_label_ar="صالة الإنتاج", module_label_tr="Atölye",
           perm="mes_view")

_ACH = "ROUND(CAST(100.0 * h.actual_qty / NULLIF(h.target_qty, 0) AS NUMERIC), 1)"
_EARNED = "SUM((h.actual_qty - h.reject_qty) * h.smv)"
_OPER_MIN = "SUM(h.operators * 60.0)"

# --------------------------------------------------------------------------
# 1. Hourly output vs target — the board, as a filterable/exportable register
# --------------------------------------------------------------------------
R.register(
    key="mes_hourly", **MOD,
    title="Hourly output vs target", title_ar="الإنتاج بالساعة مقابل الهدف",
    title_tr="Saatlik üretim / hedef",
    desc="Every recorded hour with its achievement, rejects and red-hour count.",
    desc_ar="كل ساعة مسجلة مع نسبة الإنجاز والمرفوضات وعدد الساعات الحمراء.",
    desc_tr="Kaydedilen her saat: başarı oranı, fire ve kırmızı saat sayısı.",
    select=("h.work_date AS work_date, l.name AS line, h.hour_slot AS hour_slot, "
            "h.target_qty AS target, h.actual_qty AS actual, h.reject_qty AS reject, "
            "(h.actual_qty - h.reject_qty) AS good, "
            f"{_ACH} AS achievement, h.operators AS operators"),
    frm="mes_hourly h LEFT JOIN production_lines l ON l.id = h.line_id",
    date_col="h.work_date",
    order="h.work_date DESC, l.name, h.hour_slot",
    columns=[
        R.col("work_date", "Date", "التاريخ", "Tarih", "date"),
        R.col("line", "Line", "الخط", "Hat"),
        R.col("hour_slot", "Hour", "الساعة", "Saat"),
        R.col("target", "Target", "الهدف", "Hedef", "num", total="SUM(h.target_qty)"),
        R.col("actual", "Output", "الإنتاج", "Üretim", "num", total="SUM(h.actual_qty)"),
        R.col("reject", "Rejects", "المرفوض", "Fire", "num", total="SUM(h.reject_qty)"),
        R.col("good", "Good pcs", "القطع السليمة", "Sağlam adet", "num",
              total="SUM(h.actual_qty - h.reject_qty)"),
        R.col("achievement", "Achievement %", "الإنجاز %", "Başarı %", "num"),
        R.col("operators", "Operators", "العمال", "Operatör", "num"),
    ],
    filters=[
        R.filt("line", "Line", "الخط", "Hat", "l.name"),
        R.filt("hour_slot", "Hour", "الساعة", "Saat", "h.hour_slot", "select", "=",
               HOUR_SLOTS),
    ],
    kpis=[
        R.kpi("actual", "Output", "الإنتاج", "Üretim", "SUM(h.actual_qty)"),
        R.kpi("target", "Target", "الهدف", "Hedef", "SUM(h.target_qty)", better="none"),
        R.kpi("achievement", "Achievement %", "الإنجاز %", "Başarı %",
              "ROUND(CAST(100.0 * SUM(h.actual_qty) / NULLIF(SUM(h.target_qty), 0) "
              "AS NUMERIC), 1)"),
        R.kpi("reject_pct", "Reject %", "نسبة المرفوض %", "Fire %",
              "ROUND(CAST(100.0 * SUM(h.reject_qty) / NULLIF(SUM(h.actual_qty), 0) "
              "AS NUMERIC), 2)", better="down"),
        R.kpi("red_hours", "Red hours", "الساعات الحمراء", "Kırmızı saat",
              "SUM(CASE WHEN 100.0 * h.actual_qty / NULLIF(h.target_qty, 0) < "
              f"{RAG_AMBER} THEN 1 ELSE 0 END)", better="down"),
    ],
    chart=R.chart("trend", "h.work_date", "SUM(h.actual_qty)", "h.work_date",
                  "Output per day", "الإنتاج اليومي", "Günlük üretim"),
)

# --------------------------------------------------------------------------
# 2. Line efficiency and downtime, one row per line per day
# --------------------------------------------------------------------------
# The downtime join is a PRE-GROUPED sub-query: mes_downtime holds several rows
# per (line, day), and joining it raw would multiply every hourly row and inflate
# output. Pre-grouped it is 1:1, so MAX(dt.mins) is the day's lost minutes exactly
# once.
_DT = ("LEFT JOIN (SELECT line_id, work_date, SUM(minutes) AS mins FROM mes_downtime "
       "GROUP BY line_id, work_date) dt "
       "ON dt.line_id = h.line_id AND dt.work_date = h.work_date")

R.register(
    key="mes_line_day", **MOD,
    title="Line efficiency & downtime", title_ar="كفاءة الخط والتوقفات",
    title_tr="Hat verimliliği ve duruş",
    desc="One row per line per day: output against plan, sewing efficiency and lost minutes.",
    desc_ar="صف لكل خط في اليوم: الإنتاج مقابل الخطة وكفاءة الخياطة والدقائق المفقودة.",
    desc_tr="Hat ve gün başına bir satır: plana karşı üretim, dikiş verimliliği ve kayıp dakika.",
    select=("h.work_date AS work_date, l.name AS line, COUNT(*) AS hours, "
            "SUM(h.target_qty) AS target, SUM(h.actual_qty) AS actual, "
            "SUM(h.actual_qty - h.reject_qty) AS good, "
            "ROUND(CAST(100.0 * SUM(h.actual_qty) / NULLIF(SUM(h.target_qty), 0) "
            "AS NUMERIC), 1) AS achievement, "
            f"ROUND(CAST(100.0 * {_EARNED} / NULLIF({_OPER_MIN}, 0) AS NUMERIC), 1) "
            "AS efficiency, "
            "COALESCE(MAX(dt.mins), 0) AS downtime, "
            f"{_EARNED} AS earned_min, {_OPER_MIN} AS oper_min"),
    frm=f"mes_hourly h LEFT JOIN production_lines l ON l.id = h.line_id {_DT}",
    group="h.work_date, l.name",
    order="work_date DESC, line",
    date_col="h.work_date",
    columns=[
        R.col("work_date", "Date", "التاريخ", "Tarih", "date"),
        R.col("line", "Line", "الخط", "Hat"),
        R.col("hours", "Hours", "الساعات", "Saat", "num", total="SUM(hours)"),
        R.col("target", "Target", "الهدف", "Hedef", "num", total="SUM(target)"),
        R.col("actual", "Output", "الإنتاج", "Üretim", "num", total="SUM(actual)"),
        R.col("good", "Good pcs", "القطع السليمة", "Sağlam adet", "num", total="SUM(good)"),
        R.col("achievement", "Achievement %", "الإنجاز %", "Başarı %", "num"),
        R.col("efficiency", "Efficiency %", "الكفاءة %", "Verimlilik %", "num"),
        R.col("downtime", "Downtime min", "دقائق التوقف", "Duruş dakika", "num",
              total="SUM(downtime)"),
    ],
    filters=[R.filt("line", "Line", "الخط", "Hat", "l.name")],
    kpis=[
        R.kpi("line_days", "Line-days", "أيام الخطوط", "Hat-gün", "COUNT(*)", better="none"),
        R.kpi("actual", "Output", "الإنتاج", "Üretim", "SUM(actual)"),
        R.kpi("efficiency", "Efficiency %", "الكفاءة %", "Verimlilik %",
              "ROUND(CAST(100.0 * SUM(earned_min) / NULLIF(SUM(oper_min), 0) AS NUMERIC), 1)"),
        R.kpi("downtime", "Downtime min", "دقائق التوقف", "Duruş dakika",
              "SUM(downtime)", better="down"),
    ],
    chart=R.chart("bar", "l.name", "SUM(h.actual_qty)", "l.name",
                  "Output by line", "الإنتاج حسب الخط", "Hat bazında üretim"),
)

# --------------------------------------------------------------------------
# 3. Downtime Pareto — the coded losses, biggest first
# --------------------------------------------------------------------------
R.register(
    key="mes_downtime", **MOD,
    title="Downtime Pareto", title_ar="باريتو التوقفات", title_tr="Duruş Pareto",
    desc="Coded lost minutes by reason and line — the few causes that cost the most.",
    desc_ar="الدقائق المفقودة حسب السبب والخط — الأسباب القليلة الأعلى تكلفة.",
    desc_tr="Neden ve hat bazında kodlanmış kayıp dakikalar — en pahalı birkaç neden.",
    select=("d.reason AS reason, l.name AS line, COUNT(*) AS events, "
            "ROUND(CAST(SUM(d.minutes) AS NUMERIC), 1) AS minutes"),
    frm="mes_downtime d LEFT JOIN production_lines l ON l.id = d.line_id",
    group="d.reason, l.name",
    order="minutes DESC",
    date_col="d.work_date",
    columns=[
        R.col("reason", "Reason", "السبب", "Neden"),
        R.col("line", "Line", "الخط", "Hat"),
        R.col("events", "Events", "عدد المرات", "Olay", "num", total="SUM(events)"),
        R.col("minutes", "Lost minutes", "الدقائق المفقودة", "Kayıp dakika", "num",
              total="SUM(minutes)"),
    ],
    filters=[
        R.filt("reason", "Reason", "السبب", "Neden", "d.reason", "select", "=",
               DOWNTIME_REASONS),
        R.filt("line", "Line", "الخط", "Hat", "l.name"),
    ],
    kpis=[
        R.kpi("minutes", "Lost minutes", "الدقائق المفقودة", "Kayıp dakika",
              "SUM(minutes)", better="down"),
        R.kpi("events", "Stoppages", "عدد التوقفات", "Duruş sayısı", "SUM(events)",
              better="down"),
    ],
    chart=R.chart("pareto", "d.reason", "SUM(d.minutes)", "d.reason",
                  "Lost minutes by reason", "الدقائق المفقودة حسب السبب",
                  "Nedene göre kayıp dakika"),
)
