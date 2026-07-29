"""
Quality (QMS) — report declarations for the shared reporting engine.

Pure data, declared at import time; no DB work here (gunicorn --preload).

Same denominators as services.metrics(), so a report can never disagree with the
screen it was opened from:
    DHU          = defects / units inspected x 100
    RFT %        = (units - defective units) / units x 100
    defect count = SUM(qc_defects.qty) for the inspection
The defect count arrives through a PRE-GROUPED sub-query. Joining qc_defects raw
would repeat each inspection once per defect line and multiply units inspected —
the classic way a DHU report reads a third of the truth.
Stage roll-ups keep services.by_section()'s `units_inspected > 0` rule: defects
booked against a lot whose units were never counted have no denominator, and
counting them invents DHU out of thin air.

NOT declared, for lack of data — say so rather than fake it:
  * AQL pass/fail by SUPPLIER. qc_inspections has no supplier/vendor column, and
    ord_orders carries the buyer (who we sell to), not the supplier. There is
    nothing to group on.
  * AQL pass/fail by LINE. Inspections record a STAGE (cutting / sewing_inline /
    final ...), never a production line id; mes_hourly owns the line dimension and
    the two are not linked. "By stage" below is the honest version of the same
    question.
"""
from app.quality.constants import (DEFECT_SECTIONS, DEFECT_SEVERITY, QC_STAGES,
                                   VERDICTS)
from app.services import reporting as R

MOD = dict(module="quality", module_label="Quality",
           module_label_ar="الجودة", module_label_tr="Kalite",
           perm="qc_view")

_DEFECTS = ("LEFT JOIN (SELECT inspection_id, SUM(qty) AS defects FROM qc_defects "
            "GROUP BY inspection_id) d ON d.inspection_id = i.id")
_UNITS = "COALESCE(i.units_inspected, 0)"
_DEFECTIVE = "COALESCE(i.defective_units, 0)"
_DEF = "COALESCE(d.defects, 0)"

# --------------------------------------------------------------------------
# 1. AQL inspection register with DHU / RFT
# --------------------------------------------------------------------------
R.register(
    key="quality_inspections", **MOD,
    title="AQL inspections, DHU & RFT", title_ar="فحوصات AQL ومعدل العيوب والقبول",
    title_tr="AQL denetimleri, DHU ve RFT",
    desc="Every inspection with its frozen sampling plan, verdict, DHU and right-first-time.",
    desc_ar="كل فحص مع خطة العينة المثبتة والحكم ومعدل العيوب ونسبة الصحيح من أول مرة.",
    desc_tr="Her denetim: dondurulmuş örnekleme planı, karar, DHU ve ilk seferde doğru oranı.",
    select=("i.ref AS ref, i.inspection_date AS insp_date, o.order_no AS order_no, "
            "o.buyer AS buyer, i.stage AS stage, i.aql AS aql, "
            "i.code_letter AS code_letter, COALESCE(i.sample_size, 0) AS sample_size, "
            "COALESCE(i.accept_no, 0) AS accept_no, "
            f"{_UNITS} AS units, {_DEFECTIVE} AS defective, {_DEF} AS defects, "
            f"ROUND(CAST(100.0 * {_DEF} / NULLIF(i.units_inspected, 0) AS NUMERIC), 2) AS dhu, "
            f"ROUND(CAST(100.0 * ({_UNITS} - {_DEFECTIVE}) / NULLIF(i.units_inspected, 0) "
            "AS NUMERIC), 2) AS rft, "
            "i.verdict AS verdict, i.inspector AS inspector"),
    frm=f"qc_inspections i LEFT JOIN ord_orders o ON o.id = i.order_id {_DEFECTS}",
    date_col="i.inspection_date",
    order="i.inspection_date DESC, i.id DESC",
    columns=[
        R.col("ref", "Ref", "المرجع", "Referans"),
        R.col("insp_date", "Date", "التاريخ", "Tarih", "date"),
        R.col("order_no", "Order", "الطلب", "Sipariş"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("stage", "Stage", "المرحلة", "Aşama"),
        R.col("aql", "AQL", "مستوى الجودة المقبول", "AQL", "num"),
        R.col("code_letter", "Code", "الرمز", "Kod"),
        R.col("sample_size", "Sample", "حجم العينة", "Numune", "num"),
        R.col("accept_no", "Ac", "حد القبول", "Kabul", "num"),
        R.col("units", "Units", "الوحدات", "Adet", "num", total=f"SUM({_UNITS})"),
        R.col("defective", "Defective", "الوحدات المعيبة", "Hatalı adet", "num",
              total=f"SUM({_DEFECTIVE})"),
        R.col("defects", "Defects", "العيوب", "Hata", "num", total=f"SUM({_DEF})"),
        R.col("dhu", "DHU", "معدل العيوب", "DHU", "num"),
        R.col("rft", "RFT %", "الصحيح من أول مرة %", "RFT %", "num"),
        R.col("verdict", "Verdict", "الحكم", "Karar"),
        R.col("inspector", "Inspector", "المفتش", "Denetçi"),
    ],
    filters=[
        R.filt("stage", "Stage", "المرحلة", "Aşama", "i.stage", "select", "=", QC_STAGES),
        R.filt("verdict", "Verdict", "الحكم", "Karar", "i.verdict", "select", "=", VERDICTS),
        R.filt("order_no", "Order", "الطلب", "Sipariş", "o.order_no"),
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "o.buyer"),
    ],
    kpis=[
        R.kpi("inspections", "Inspections", "الفحوصات", "Denetim", "COUNT(*)", better="none"),
        R.kpi("units", "Units inspected", "الوحدات المفحوصة", "Denetlenen adet",
              f"SUM({_UNITS})", better="none"),
        R.kpi("pass_rate", "AQL pass rate %", "نسبة النجاح %", "AQL geçme oranı %",
              "ROUND(CAST(100.0 * SUM(CASE WHEN i.verdict = 'pass' THEN 1 ELSE 0 END) "
              "/ NULLIF(COUNT(*), 0) AS NUMERIC), 1)"),
        R.kpi("failed", "Failed lots", "الدفعات الراسبة", "Reddedilen parti",
              "SUM(CASE WHEN i.verdict = 'fail' THEN 1 ELSE 0 END)", better="down"),
        R.kpi("dhu", "DHU", "معدل العيوب", "DHU",
              f"ROUND(CAST(100.0 * SUM({_DEF}) / NULLIF(SUM(i.units_inspected), 0) "
              "AS NUMERIC), 2)", better="down"),
        R.kpi("rft", "RFT %", "الصحيح من أول مرة %", "RFT %",
              f"ROUND(CAST(100.0 * (SUM({_UNITS}) - SUM({_DEFECTIVE})) "
              "/ NULLIF(SUM(i.units_inspected), 0) AS NUMERIC), 2)"),
    ],
    chart=R.chart("trend", "i.inspection_date",
                  f"ROUND(CAST(100.0 * SUM({_DEF}) / NULLIF(SUM(i.units_inspected), 0) "
                  "AS NUMERIC), 2)", "i.inspection_date",
                  "DHU trend", "اتجاه معدل العيوب", "DHU eğilimi"),
)

# --------------------------------------------------------------------------
# 2. Defect Pareto — the few defect types that drive most of the rework
# --------------------------------------------------------------------------
R.register(
    key="quality_defect_pareto", **MOD,
    title="Defect Pareto", title_ar="باريتو العيوب", title_tr="Hata Pareto",
    desc="Defect quantities ranked by type, with the section that created them.",
    desc_ar="كميات العيوب مرتبة حسب النوع مع القسم المتسبب فيها.",
    desc_tr="Türe göre sıralanan hata adetleri ve hatayı yaratan bölüm.",
    select=("d.defect_type AS defect_type, d.section AS section, d.severity AS severity, "
            "COUNT(*) AS lines, ROUND(CAST(SUM(COALESCE(d.qty, 0)) AS NUMERIC), 0) AS qty"),
    frm="qc_defects d JOIN qc_inspections i ON i.id = d.inspection_id",
    group="d.defect_type, d.section, d.severity",
    order="qty DESC",
    date_col="i.inspection_date",
    columns=[
        R.col("defect_type", "Defect", "العيب", "Hata"),
        R.col("section", "Section", "القسم", "Bölüm"),
        R.col("severity", "Severity", "الخطورة", "Önem"),
        R.col("lines", "Occurrences", "عدد الحالات", "Kayıt", "num", total="SUM(lines)"),
        R.col("qty", "Qty", "الكمية", "Adet", "num", total="SUM(qty)"),
    ],
    filters=[
        R.filt("section", "Section", "القسم", "Bölüm", "d.section", "select", "=",
               DEFECT_SECTIONS),
        R.filt("severity", "Severity", "الخطورة", "Önem", "d.severity", "select", "=",
               DEFECT_SEVERITY),
        R.filt("stage", "Stage", "المرحلة", "Aşama", "i.stage", "select", "=", QC_STAGES),
        R.filt("defect_type", "Defect", "العيب", "Hata", "d.defect_type"),
    ],
    kpis=[
        R.kpi("qty", "Defects", "العيوب", "Hata", "SUM(qty)", better="down"),
        R.kpi("lines", "Occurrences", "عدد الحالات", "Kayıt", "SUM(lines)", better="down"),
        R.kpi("critical", "Critical", "حرجة", "Kritik",
              "SUM(CASE WHEN severity = 'critical' THEN qty ELSE 0 END)", better="down"),
    ],
    chart=R.chart("pareto", "d.defect_type", "SUM(COALESCE(d.qty, 0))", "d.defect_type",
                  "Defects by type", "العيوب حسب النوع", "Türe göre hatalar"),
)

# --------------------------------------------------------------------------
# 3. DHU / right-first-time by inspection stage
# --------------------------------------------------------------------------
R.register(
    key="quality_stage_dhu", **MOD,
    title="DHU & RFT by stage", title_ar="معدل العيوب والقبول حسب المرحلة",
    title_tr="Aşamaya göre DHU ve RFT",
    desc="Where quality is lost: DHU, right-first-time and failed lots per inspection stage.",
    desc_ar="أين تُفقد الجودة: معدل العيوب والصحيح من أول مرة والدفعات الراسبة لكل مرحلة.",
    desc_tr="Kalitenin kaybedildiği yer: aşama başına DHU, ilk seferde doğru ve reddedilen parti.",
    select=("i.stage AS stage, COUNT(*) AS inspections, "
            f"SUM({_UNITS}) AS units, SUM({_DEFECTIVE}) AS defective, SUM({_DEF}) AS defects, "
            f"ROUND(CAST(100.0 * SUM({_DEF}) / NULLIF(SUM(i.units_inspected), 0) "
            "AS NUMERIC), 2) AS dhu, "
            f"ROUND(CAST(100.0 * (SUM({_UNITS}) - SUM({_DEFECTIVE})) "
            "/ NULLIF(SUM(i.units_inspected), 0) AS NUMERIC), 2) AS rft, "
            "SUM(CASE WHEN i.verdict = 'fail' THEN 1 ELSE 0 END) AS failed"),
    frm=f"qc_inspections i {_DEFECTS}",
    base_where=["i.units_inspected > 0"],
    group="i.stage",
    order="dhu DESC",
    date_col="i.inspection_date",
    columns=[
        R.col("stage", "Stage", "المرحلة", "Aşama"),
        R.col("inspections", "Inspections", "الفحوصات", "Denetim", "num",
              total="SUM(inspections)"),
        R.col("units", "Units", "الوحدات", "Adet", "num", total="SUM(units)"),
        R.col("defective", "Defective", "الوحدات المعيبة", "Hatalı adet", "num",
              total="SUM(defective)"),
        R.col("defects", "Defects", "العيوب", "Hata", "num", total="SUM(defects)"),
        R.col("dhu", "DHU", "معدل العيوب", "DHU", "num"),
        R.col("rft", "RFT %", "الصحيح من أول مرة %", "RFT %", "num"),
        R.col("failed", "Failed lots", "الدفعات الراسبة", "Reddedilen parti", "num",
              total="SUM(failed)"),
    ],
    filters=[
        R.filt("stage", "Stage", "المرحلة", "Aşama", "i.stage", "select", "=", QC_STAGES),
    ],
    kpis=[
        R.kpi("units", "Units inspected", "الوحدات المفحوصة", "Denetlenen adet",
              "SUM(units)", better="none"),
        R.kpi("defects", "Defects", "العيوب", "Hata", "SUM(defects)", better="down"),
        R.kpi("dhu", "DHU", "معدل العيوب", "DHU",
              "ROUND(CAST(100.0 * SUM(defects) / NULLIF(SUM(units), 0) AS NUMERIC), 2)",
              better="down"),
        R.kpi("rft", "RFT %", "الصحيح من أول مرة %", "RFT %",
              "ROUND(CAST(100.0 * (SUM(units) - SUM(defective)) / NULLIF(SUM(units), 0) "
              "AS NUMERIC), 2)"),
        R.kpi("failed", "Failed lots", "الدفعات الراسبة", "Reddedilen parti",
              "SUM(failed)", better="down"),
    ],
    chart=R.chart("bar", "i.stage",
                  f"ROUND(CAST(100.0 * SUM({_DEF}) / NULLIF(SUM(i.units_inspected), 0) "
                  "AS NUMERIC), 2)", "i.stage",
                  "DHU by stage", "معدل العيوب حسب المرحلة", "Aşamaya göre DHU"),
)
