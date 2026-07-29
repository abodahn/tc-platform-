"""HR Core reports — declared against the shared engine (app/services/reporting.py).

Declaration only: no DB work at import (the --preload deploy constraint), no
route, no template, no exporter and no filter parsing — the engine owns all of
those. Every report carries `ppl_view`, the same permission that guards the HR
pages, so an export URL can never leak numbers a user cannot already see.

The formulas deliberately mirror app/people/services.py so a report and the page
it came from can never disagree:
  * scheduled  = attendance rows with a WORKING status (holiday/off are not
                 scheduled — constants.WORKING_STATUSES)
  * present    = present + late            (constants.PRESENT_STATUSES)
  * efficiency = 100 * SUM(earned minutes) / SUM(minutes worked), minute-weighted
                 over the whole period — never a mean of daily percentages
                 (see services.incentive_summary)
  * capable    = skill level >= 2          (constants.CAPABLE_MIN_LEVEL; level 1
                 is a trainee and is not deployable)

NULLIF(...,0) guards every division: an empty period returns NULL, which the
engine prints as 0 — never a ZeroDivisionError.
"""
from app.services import reporting as R

_MOD = dict(module="people", module_label="People",
            module_label_ar="الأفراد", module_label_tr="İnsan Kaynakları")

# Repeated SQL fragments — written once so the KPI, the column and the chart can
# never drift apart.
_SCHEDULED = "SUM(CASE WHEN a.status IN ('present','absent','late','leave') THEN 1 ELSE 0 END)"
_ABSENT = "SUM(CASE WHEN a.status = 'absent' THEN 1 ELSE 0 END)"
_LATE = "SUM(CASE WHEN a.status = 'late' THEN 1 ELSE 0 END)"
_PRESENT = "SUM(CASE WHEN a.status IN ('present','late') THEN 1 ELSE 0 END)"
_REMAINING = "COALESCE(b.entitled,0) - COALESCE(b.taken,0)"
_CAPABLE = "SUM(CASE WHEN COALESCE(s.level,0) >= 2 THEN 1 ELSE 0 END)"


# ---------------------------------------------------------------------------
# 1. Attendance & absence
# ---------------------------------------------------------------------------
R.register(
    key="people_attendance", **_MOD,
    title="Attendance & Absence", title_ar="الحضور والغياب",
    title_tr="Devam ve Devamsızlık",
    desc="Every marked day with absenteeism, lateness and overtime.",
    desc_ar="كل يوم مسجل مع نسبة الغياب والتأخير والعمل الإضافي.",
    desc_tr="İşaretlenen her gün: devamsızlık, geç kalma ve fazla mesai.",
    perm="ppl_view",
    select="a.work_date AS work_date, e.employee_code AS code, "
           "e.employee_name AS employee, e.department AS department, "
           "e.section AS section, a.status AS status, "
           "COALESCE(a.worked_hours,0) AS worked_hours, "
           "COALESCE(a.ot_hours,0) AS ot_hours",
    frm="ppl_attendance a JOIN prob_employees e ON e.id = a.employee_id",
    date_col="a.work_date",
    order="a.work_date DESC, e.employee_code",
    columns=[
        R.col("work_date", "Date", "التاريخ", "Tarih", "date"),
        R.col("code", "Code", "الكود", "Kod"),
        R.col("employee", "Employee", "الموظف", "Personel"),
        R.col("department", "Department", "القسم", "Bölüm"),
        R.col("section", "Section", "الوحدة", "Birim"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("worked_hours", "Worked hours", "ساعات العمل", "Çalışılan saat",
              "num", total="SUM(COALESCE(a.worked_hours,0))"),
        R.col("ot_hours", "Overtime hours", "ساعات إضافية", "Fazla mesai",
              "num", total="SUM(COALESCE(a.ot_hours,0))"),
    ],
    filters=[
        R.filt("department", "Department", "القسم", "Bölüm", "e.department"),
        R.filt("code", "Employee code", "كود الموظف", "Personel kodu", "e.employee_code"),
        R.filt("status", "Status", "الحالة", "Durum", "a.status", "select", "=",
               [("present", "Present", "حاضر", "Mevcut"),
                ("absent", "Absent", "غائب", "Devamsız"),
                ("late", "Late", "متأخر", "Geç"),
                ("leave", "Leave", "إجازة", "İzinli"),
                ("holiday", "Holiday", "عطلة", "Tatil"),
                ("off", "Off", "راحة", "İzin günü")]),
    ],
    kpis=[
        R.kpi("days", "Days marked", "أيام مسجلة", "İşaretlenen gün", "COUNT(*)", better="none"),
        R.kpi("scheduled", "Scheduled days", "أيام مجدولة", "Planlanan gün",
              _SCHEDULED, better="none"),
        R.kpi("attendance_pct", "Attendance %", "نسبة الحضور", "Devam %",
              f"100.0 * {_PRESENT} / NULLIF({_SCHEDULED},0)"),
        R.kpi("absenteeism_pct", "Absenteeism %", "نسبة الغياب", "Devamsızlık %",
              f"100.0 * {_ABSENT} / NULLIF({_SCHEDULED},0)", better="down"),
        R.kpi("late", "Late days", "أيام تأخير", "Geç gelinen gün", _LATE, better="down"),
        R.kpi("ot", "Overtime hours", "ساعات إضافية", "Fazla mesai saati",
              "SUM(COALESCE(a.ot_hours,0))", better="none"),
    ],
    chart=R.chart("trend", "substr(a.work_date,1,10)", _ABSENT, "substr(a.work_date,1,10)",
                  "Absences per day", "الغياب يومياً", "Günlük devamsızlık"),
)


# ---------------------------------------------------------------------------
# 2. Leave liability — what the factory still owes in days
# ---------------------------------------------------------------------------
R.register(
    key="people_leave_liability", **_MOD,
    title="Leave Liability", title_ar="التزام الإجازات", title_tr="İzin Yükümlülüğü",
    desc="Entitlement, days taken and the days still owed per employee.",
    desc_ar="الرصيد والمستخدم والمتبقي لكل موظف.",
    desc_tr="Personel başına hak ediş, kullanılan ve kalan gün.",
    perm="ppl_view",
    select="e.employee_code AS code, e.employee_name AS employee, "
           "e.department AS department, b.year AS year, b.leave_type AS leave_type, "
           "COALESCE(b.entitled,0) AS entitled, COALESCE(b.taken,0) AS taken, "
           f"{_REMAINING} AS remaining",
    frm="ppl_leave_balance b JOIN prob_employees e ON e.id = b.employee_id",
    order=f"{_REMAINING} DESC, e.employee_code",
    columns=[
        R.col("code", "Code", "الكود", "Kod"),
        R.col("employee", "Employee", "الموظف", "Personel"),
        R.col("department", "Department", "القسم", "Bölüm"),
        R.col("year", "Year", "السنة", "Yıl", "num"),
        R.col("leave_type", "Leave type", "نوع الإجازة", "İzin türü"),
        R.col("entitled", "Entitled", "المستحق", "Hak ediş", "num",
              total="SUM(COALESCE(b.entitled,0))"),
        R.col("taken", "Taken", "المستخدم", "Kullanılan", "num",
              total="SUM(COALESCE(b.taken,0))"),
        R.col("remaining", "Remaining", "المتبقي", "Kalan", "num",
              total=f"SUM({_REMAINING})"),
    ],
    filters=[
        R.filt("department", "Department", "القسم", "Bölüm", "e.department"),
        R.filt("year", "Year", "السنة", "Yıl", "b.year", "num", "="),
        R.filt("leave_type", "Leave type", "نوع الإجازة", "İzin türü", "b.leave_type",
               "select", "=",
               [("annual", "Annual", "سنوية", "Yıllık"),
                ("sick", "Sick", "مرضية", "Hastalık"),
                ("unpaid", "Unpaid", "بدون أجر", "Ücretsiz"),
                ("other", "Other", "أخرى", "Diğer")]),
    ],
    kpis=[
        R.kpi("employees", "Employees", "الموظفون", "Personel",
              "COUNT(DISTINCT b.employee_id)", better="none"),
        R.kpi("entitled", "Entitled days", "أيام مستحقة", "Hak edilen gün",
              "SUM(COALESCE(b.entitled,0))", better="none"),
        R.kpi("taken", "Days taken", "أيام مستخدمة", "Kullanılan gün",
              "SUM(COALESCE(b.taken,0))", better="none"),
        R.kpi("remaining", "Days owed", "أيام مستحقة متبقية", "Borçlu olunan gün",
              f"SUM({_REMAINING})", better="down"),
    ],
    chart=R.chart("bar", "e.department", f"SUM({_REMAINING})", "e.department",
                  "Days owed by department", "الأيام المتبقية حسب القسم",
                  "Bölüme göre kalan gün"),
)


# ---------------------------------------------------------------------------
# 3. Skill coverage & single points of failure
# ---------------------------------------------------------------------------
R.register(
    key="people_skill_coverage", **_MOD,
    title="Skill Coverage", title_ar="تغطية المهارات", title_tr="Beceri Kapsamı",
    desc="Operators rated per operation — the operations one absence can stop.",
    desc_ar="عدد العمال المؤهلين لكل عملية — العمليات التي يوقفها غياب واحد.",
    desc_tr="Operasyon başına yetkin operatör — bir devamsızlığın durdurduğu işler.",
    perm="ppl_view",
    select="s.operation AS operation, COUNT(*) AS rated, "
           f"{_CAPABLE} AS capable, "
           "SUM(CASE WHEN COALESCE(s.level,0) >= 4 THEN 1 ELSE 0 END) AS experts, "
           "AVG(COALESCE(s.efficiency_pct,0)) AS avg_eff",
    frm="ppl_skills s JOIN prob_employees e ON e.id = s.employee_id",
    base_where=["e.is_deleted = 0", "e.active = 1"],
    group="s.operation",
    order="capable ASC, rated ASC",
    columns=[
        R.col("operation", "Operation", "العملية", "Operasyon"),
        R.col("rated", "Operators rated", "عمال مقيَّمون", "Değerlendirilen operatör",
              "num", total="SUM(rated)"),
        R.col("capable", "Capable (level 2+)", "مؤهلون (مستوى 2+)", "Yetkin (seviye 2+)",
              "num", total="SUM(capable)"),
        R.col("experts", "Experts (level 4+)", "خبراء (مستوى 4+)", "Uzman (seviye 4+)",
              "num", total="SUM(experts)"),
        R.col("avg_eff", "Avg efficiency %", "متوسط الكفاءة", "Ortalama verimlilik %", "num"),
    ],
    filters=[
        R.filt("operation", "Operation", "العملية", "Operasyon", "s.operation"),
        R.filt("department", "Department", "القسم", "Bölüm", "e.department"),
    ],
    kpis=[
        R.kpi("operations", "Operations rated", "العمليات المقيَّمة", "Değerlendirilen operasyon",
              "COUNT(*)", better="none"),
        R.kpi("spof", "Single points of failure", "نقاط انهيار مفردة", "Tek arıza noktası",
              "SUM(CASE WHEN capable <= 1 THEN 1 ELSE 0 END)", better="down"),
        R.kpi("uncovered", "No capable operator", "بلا عامل مؤهل", "Yetkin operatör yok",
              "SUM(CASE WHEN capable = 0 THEN 1 ELSE 0 END)", better="down"),
        R.kpi("capable", "Capable ratings", "تقييمات مؤهلة", "Yetkin değerlendirme",
              "SUM(capable)", better="none"),
    ],
    chart=R.chart("bar", "s.operation", _CAPABLE, "s.operation",
                  "Capable operators per operation", "العمال المؤهلون لكل عملية",
                  "Operasyon başına yetkin operatör"),
)


# ---------------------------------------------------------------------------
# 4. Operator efficiency & incentive
# ---------------------------------------------------------------------------
_EFF = ("100.0 * SUM(COALESCE(p.earned_minutes,0)) / "
        "NULLIF(SUM(COALESCE(p.minutes_worked,0)),0)")
_EFF_T = "100.0 * SUM(earned_minutes) / NULLIF(SUM(minutes_worked),0)"

R.register(
    key="people_incentive", **_MOD,
    title="Operator Efficiency & Incentive", title_ar="كفاءة العامل والحافز",
    title_tr="Operatör Verimliliği ve Prim",
    desc="Earned minutes, minute-weighted efficiency and incentive per operator.",
    desc_ar="الدقائق المكتسبة والكفاءة المرجّحة والحافز لكل عامل.",
    desc_tr="Operatör başına kazanılan dakika, ağırlıklı verimlilik ve prim.",
    perm="ppl_view",
    select="e.employee_code AS code, e.employee_name AS employee, "
           "e.department AS department, COUNT(DISTINCT p.work_date) AS days, "
           "SUM(COALESCE(p.pieces,0)) AS pieces, "
           "SUM(COALESCE(p.earned_minutes,0)) AS earned_minutes, "
           "SUM(COALESCE(p.minutes_worked,0)) AS minutes_worked, "
           f"{_EFF} AS efficiency_pct, "
           "SUM(COALESCE(p.incentive,0)) AS incentive",
    frm="ppl_piece_rate p JOIN prob_employees e ON e.id = p.employee_id",
    group="e.employee_code, e.employee_name, e.department",
    date_col="p.work_date",
    order="incentive DESC, code",
    columns=[
        R.col("code", "Code", "الكود", "Kod"),
        R.col("employee", "Employee", "الموظف", "Personel"),
        R.col("department", "Department", "القسم", "Bölüm"),
        R.col("days", "Days", "الأيام", "Gün", "num", total="SUM(days)"),
        R.col("pieces", "Pieces", "القطع", "Adet", "num", total="SUM(pieces)"),
        R.col("earned_minutes", "Earned minutes", "دقائق مكتسبة", "Kazanılan dakika",
              "num", total="SUM(earned_minutes)"),
        R.col("minutes_worked", "Minutes worked", "دقائق العمل", "Çalışılan dakika",
              "num", total="SUM(minutes_worked)"),
        R.col("efficiency_pct", "Efficiency %", "الكفاءة %", "Verimlilik %", "num",
              total=_EFF_T),
        R.col("incentive", "Incentive", "الحافز", "Prim", "num", total="SUM(incentive)"),
    ],
    filters=[
        R.filt("department", "Department", "القسم", "Bölüm", "e.department"),
        R.filt("operation", "Operation", "العملية", "Operasyon", "p.operation"),
        R.filt("code", "Employee code", "كود الموظف", "Personel kodu", "e.employee_code"),
    ],
    kpis=[
        R.kpi("operators", "Operators", "العمال", "Operatör", "COUNT(*)", better="none"),
        R.kpi("pieces", "Pieces", "القطع", "Adet", "SUM(pieces)"),
        R.kpi("efficiency_pct", "Efficiency %", "الكفاءة %", "Verimlilik %", _EFF_T),
        R.kpi("incentive", "Incentive earned", "الحافز المستحق", "Kazanılan prim",
              "SUM(incentive)"),
    ],
    chart=R.chart("trend", "substr(p.work_date,1,10)", _EFF, "substr(p.work_date,1,10)",
                  "Efficiency % over time", "الكفاءة عبر الزمن",
                  "Zaman içinde verimlilik %"),
)
