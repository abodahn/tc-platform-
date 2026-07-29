"""Compliance reports — declared against the shared engine
(app/services/reporting.py). Declaration only: no DB work at import, no route,
no template, no exporter. Both carry `cmp_view`, the permission that guards the
compliance pages.

Two things a compliance manager actually acts on:
  1. corrective actions — what is still open, what is overdue, in which area;
  2. what expires next — audits and certificates in ONE list, because a lapsed
     certificate and a lapsed audit stop shipments the same way.

Overdue / expired are evaluated with date('now'), which app/db.py translates for
PostgreSQL — no Python-side date arithmetic, so a printed PDF and the screen can
never disagree about "today".
"""
from app.services import reporting as R

_MOD = dict(module="compliance", module_label="Compliance",
            module_label_ar="الامتثال", module_label_tr="Uyum")

_OPEN = "f.status IN ('open','in_progress')"
_OVERDUE_SQL = (f"CASE WHEN {_OPEN} AND f.due_date IS NOT NULL "
                "AND f.due_date < date('now') THEN 1 ELSE 0 END")


# ---------------------------------------------------------------------------
# 1. Corrective actions (CAP)
# ---------------------------------------------------------------------------
R.register(
    key="compliance_findings", **_MOD,
    title="Corrective Actions", title_ar="الإجراءات التصحيحية",
    title_tr="Düzeltici Faaliyetler",
    desc="Audit findings by status, severity and area — with what is overdue.",
    desc_ar="ملاحظات التدقيق حسب الحالة والخطورة والبند — وما تجاوز موعده.",
    desc_tr="Denetim bulguları: durum, önem ve alan — vadesi geçenlerle birlikte.",
    perm="cmp_view",
    select="a.ref AS audit_ref, a.scheme AS scheme, f.clause AS clause, "
           "f.finding AS finding, f.severity AS severity, f.owner AS owner, "
           "f.due_date AS due_date, f.status AS status, "
           f"{_OVERDUE_SQL} AS overdue, f.closed_at AS closed_at",
    frm="cmp_findings f LEFT JOIN cmp_audits a ON a.id = f.audit_id",
    date_col="f.created_at",
    order=f"CASE WHEN {_OPEN} THEN 0 ELSE 1 END, f.due_date ASC, f.id DESC",
    columns=[
        R.col("audit_ref", "Audit", "التدقيق", "Denetim"),
        R.col("scheme", "Scheme", "المعيار", "Standart"),
        R.col("clause", "Area / clause", "البند", "Alan / madde"),
        R.col("finding", "Finding", "الملاحظة", "Bulgu"),
        R.col("severity", "Severity", "الخطورة", "Önem"),
        R.col("owner", "Owner", "المسؤول", "Sorumlu"),
        R.col("due_date", "Due", "تاريخ الاستحقاق", "Termin", "date"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("overdue", "Overdue", "متأخر", "Gecikmiş", "num",
              total=f"SUM({_OVERDUE_SQL})"),
        R.col("closed_at", "Closed", "تاريخ الإغلاق", "Kapanış", "date"),
    ],
    filters=[
        R.filt("status", "Status", "الحالة", "Durum", "f.status", "select", "=",
               [("open", "Open", "مفتوح", "Açık"),
                ("in_progress", "In progress", "قيد التنفيذ", "Devam ediyor"),
                ("closed", "Closed", "مغلق", "Kapalı"),
                ("verified", "Verified", "تم التحقق", "Doğrulandı")]),
        R.filt("severity", "Severity", "الخطورة", "Önem", "f.severity", "select", "=",
               [("observation", "Observation", "ملاحظة", "Gözlem"),
                ("minor", "Minor", "بسيطة", "Küçük"),
                ("major", "Major", "كبيرة", "Büyük"),
                ("critical", "Critical", "حرجة", "Kritik"),
                ("zero_tolerance", "Zero tolerance", "غير مقبولة", "Sıfır tolerans")]),
        R.filt("scheme", "Scheme", "المعيار", "Standart", "a.scheme"),
        R.filt("owner", "Owner", "المسؤول", "Sorumlu", "f.owner"),
    ],
    kpis=[
        R.kpi("total", "Findings", "الملاحظات", "Bulgu", "COUNT(*)", better="none"),
        R.kpi("open", "Open", "مفتوحة", "Açık",
              f"SUM(CASE WHEN {_OPEN} THEN 1 ELSE 0 END)", better="down"),
        R.kpi("overdue", "Overdue", "متأخرة", "Gecikmiş", f"SUM({_OVERDUE_SQL})",
              better="down"),
        R.kpi("critical", "Open critical", "حرجة مفتوحة", "Açık kritik",
              f"SUM(CASE WHEN {_OPEN} AND f.severity IN ('critical','zero_tolerance') "
              "THEN 1 ELSE 0 END)", better="down"),
        R.kpi("closed", "Closed", "مغلقة", "Kapalı",
              "SUM(CASE WHEN f.status IN ('closed','verified') THEN 1 ELSE 0 END)"),
        R.kpi("closure_pct", "Closure %", "نسبة الإغلاق", "Kapanma %",
              "100.0 * SUM(CASE WHEN f.status IN ('closed','verified') THEN 1 ELSE 0 END) "
              "/ NULLIF(COUNT(*),0)"),
    ],
    chart=R.chart("pareto", "COALESCE(f.clause,'—')", "COUNT(*)", "COALESCE(f.clause,'—')",
                  "Findings by area", "الملاحظات حسب البند", "Alana göre bulgu"),
)


# ---------------------------------------------------------------------------
# 2. Expiry watchlist — audits + certificates in one list
# ---------------------------------------------------------------------------
# One UNION ALL over the two tables. The engine only ever composes SELECT /
# WHERE / ORDER BY around this text and binds every user value as a parameter,
# so a sub-query FROM is safe here.
_EXPIRY_FRM = (
    "(SELECT 'audit' AS kind, a.scheme AS name, a.ref AS reference, "
    "        a.auditor AS issuer, a.site AS scope, a.valid_until AS expires, "
    "        a.status AS status "
    "   FROM cmp_audits a "
    "  WHERE a.valid_until IS NOT NULL AND a.status <> 'cancelled' "
    " UNION ALL "
    " SELECT 'certificate', c.name, c.cert_no, c.issuer, c.scope, c.expiry_date, c.status "
    "   FROM cmp_certs c "
    "  WHERE c.expiry_date IS NOT NULL AND c.status <> 'revoked') x")

_EXPIRED = "CASE WHEN x.expires < date('now') THEN 1 ELSE 0 END"

R.register(
    key="compliance_expiry", **_MOD,
    title="Expiry Watchlist", title_ar="قائمة الانتهاء",
    title_tr="Süre Sonu Listesi",
    desc="Audits and certificates by expiry date — soonest first.",
    desc_ar="التدقيقات والشهادات حسب تاريخ الانتهاء — الأقرب أولاً.",
    desc_tr="Denetim ve sertifikalar, süre sonuna göre — en yakın önce.",
    perm="cmp_view",
    select="x.kind AS kind, x.name AS name, x.reference AS reference, "
           "x.issuer AS issuer, x.scope AS scope, x.expires AS expires, "
           "x.status AS status, "
           f"{_EXPIRED} AS expired",
    frm=_EXPIRY_FRM,
    date_col="x.expires",
    order="x.expires ASC",
    columns=[
        R.col("kind", "Type", "النوع", "Tür"),
        R.col("name", "Name", "الاسم", "Ad"),
        R.col("reference", "Reference", "المرجع", "Referans"),
        R.col("issuer", "Issuer / auditor", "الجهة المانحة", "Veren / denetçi"),
        R.col("scope", "Scope / site", "النطاق", "Kapsam / saha"),
        R.col("expires", "Expires", "تاريخ الانتهاء", "Bitiş", "date"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("expired", "Expired", "منتهٍ", "Süresi doldu", "num",
              total=f"SUM({_EXPIRED})"),
    ],
    filters=[
        R.filt("kind", "Type", "النوع", "Tür", "x.kind", "select", "=",
               [("audit", "Audit", "تدقيق", "Denetim"),
                ("certificate", "Certificate", "شهادة", "Sertifika")]),
        R.filt("name", "Name", "الاسم", "Ad", "x.name"),
        R.filt("expires_before", "Expires before", "ينتهي قبل", "Şu tarihten önce biter",
               "x.expires", "date", "<="),
    ],
    kpis=[
        R.kpi("items", "Items tracked", "العناصر المتابعة", "İzlenen kayıt",
              "COUNT(*)", better="none"),
        R.kpi("expired", "Expired", "منتهية", "Süresi dolmuş", f"SUM({_EXPIRED})",
              better="down"),
        R.kpi("valid", "Still valid", "سارية", "Hâlâ geçerli",
              "SUM(CASE WHEN x.expires >= date('now') THEN 1 ELSE 0 END)"),
    ],
    # deliberately no chart: an expiry list is read as a list, and a bar of
    # "count per type" would be decoration.
)
