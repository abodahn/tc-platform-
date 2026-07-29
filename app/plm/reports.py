"""PLM reports — declared against the shared engine (app/services/reporting.py).

Declaration only: no DB work at import, no route, no template, no exporter.
Both reports carry `plm_view`, the permission that guards the PLM pages.

Sampling arithmetic, stated once so the KPI and the column agree:
  decided   = rounds with a verdict other than 'pending'
  pass %    = approved / decided
  first-pass% = round 1 approved / round 1 decided   (the merchandising KPI:
              how often a style is right the first time it is sent)
Both denominators go through NULLIF(...,0), so a style with only pending rounds
reads 0, never a division by zero.
"""
from app.services import reporting as R

_MOD = dict(module="plm", module_label="Product Development",
            module_label_ar="تطوير المنتج", module_label_tr="Ürün Geliştirme")

_APPROVED = "SUM(CASE WHEN sp.verdict = 'approved' THEN 1 ELSE 0 END)"
_FAILED = "SUM(CASE WHEN sp.verdict IN ('rejected','revise') THEN 1 ELSE 0 END)"
_PENDING = "SUM(CASE WHEN sp.verdict = 'pending' THEN 1 ELSE 0 END)"
_DECIDED = "SUM(CASE WHEN sp.verdict IN ('approved','rejected','revise') THEN 1 ELSE 0 END)"
_FIRST_OK = ("SUM(CASE WHEN COALESCE(sp.round_no,1) = 1 AND sp.verdict = 'approved' "
             "THEN 1 ELSE 0 END)")
_FIRST_DEC = ("SUM(CASE WHEN COALESCE(sp.round_no,1) = 1 AND "
              "sp.verdict IN ('approved','rejected','revise') THEN 1 ELSE 0 END)")


# ---------------------------------------------------------------------------
# 1. Sample rounds & pass rate, per style
# ---------------------------------------------------------------------------
R.register(
    key="plm_sample_pass", **_MOD,
    title="Sample Pass Rate", title_ar="معدل قبول العينات",
    title_tr="Numune Onay Oranı",
    desc="Rounds sent, approved, rejected and the pass rate per style.",
    desc_ar="الجولات المرسلة والمقبولة والمرفوضة ومعدل القبول لكل موديل.",
    desc_tr="Model başına gönderilen, onaylanan, reddedilen tur ve onay oranı.",
    perm="plm_view",
    select="s.style_ref AS style_ref, s.name AS style, s.buyer AS buyer, "
           "s.status AS status, COUNT(*) AS rounds, "
           f"{_APPROVED} AS approved, {_FAILED} AS failed, {_PENDING} AS pending, "
           f"100.0 * {_APPROVED} / NULLIF({_DECIDED},0) AS pass_pct, "
           "MAX(sp.sent_date) AS last_sent, "
           # selected for the first-pass KPI only — not a displayed column
           f"{_FIRST_OK} AS first_ok, {_FIRST_DEC} AS first_dec",
    frm="plm_samples sp JOIN plm_styles s ON s.id = sp.style_id",
    group="s.style_ref, s.name, s.buyer, s.status",
    date_col="sp.sent_date",
    order="failed DESC, rounds DESC",
    columns=[
        R.col("style_ref", "Style ref", "كود الموديل", "Model kodu"),
        R.col("style", "Style", "الموديل", "Model"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("status", "Style status", "حالة الموديل", "Model durumu"),
        R.col("rounds", "Rounds sent", "الجولات المرسلة", "Gönderilen tur", "num",
              total="SUM(rounds)"),
        R.col("approved", "Approved", "مقبولة", "Onaylı", "num", total="SUM(approved)"),
        R.col("failed", "Rejected / revise", "مرفوضة / للتعديل", "Ret / revize", "num",
              total="SUM(failed)"),
        R.col("pending", "Pending", "قيد الانتظار", "Beklemede", "num",
              total="SUM(pending)"),
        R.col("pass_pct", "Pass %", "نسبة القبول", "Onay %", "num",
              total="100.0 * SUM(approved) / NULLIF(SUM(approved) + SUM(failed),0)"),
        R.col("last_sent", "Last sent", "آخر إرسال", "Son gönderim", "date"),
    ],
    filters=[
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "s.buyer"),
        R.filt("style_ref", "Style ref", "كود الموديل", "Model kodu", "s.style_ref"),
        R.filt("stage", "Sample stage", "مرحلة العينة", "Numune aşaması", "sp.stage",
               "select", "=",
               [("proto", "Proto", "أولية", "Proto"),
                ("fit", "Fit", "المقاس", "Kalıp"),
                ("size_set", "Size set", "مجموعة المقاسات", "Beden seti"),
                ("sms", "SMS", "عينة إنتاج", "SMS"),
                ("pp", "Pre-production", "ما قبل الإنتاج", "Üretim öncesi"),
                ("top", "Top of production", "بداية الإنتاج", "Üretim başı")]),
        R.filt("verdict", "Verdict", "القرار", "Karar", "sp.verdict", "select", "=",
               [("pending", "Pending", "قيد الانتظار", "Beklemede"),
                ("approved", "Approved", "مقبولة", "Onaylı"),
                ("rejected", "Rejected", "مرفوضة", "Reddedildi"),
                ("revise", "Revise", "للتعديل", "Revize")]),
    ],
    kpis=[
        R.kpi("styles", "Styles sampled", "موديلات بعينات", "Numunelenen model",
              "COUNT(*)", better="none"),
        R.kpi("rounds", "Rounds sent", "الجولات المرسلة", "Gönderilen tur",
              "SUM(rounds)", better="none"),
        R.kpi("pass_pct", "Pass %", "نسبة القبول", "Onay %",
              "100.0 * SUM(approved) / NULLIF(SUM(approved) + SUM(failed),0)"),
        R.kpi("first_pass_pct", "First-round pass %", "نسبة القبول من أول جولة",
              "İlk turda onay %",
              "100.0 * SUM(first_ok) / NULLIF(SUM(first_dec),0)"),
        R.kpi("failed", "Rejected / revise", "مرفوضة / للتعديل", "Ret / revize",
              "SUM(failed)", better="down"),
        R.kpi("pending", "Awaiting buyer", "بانتظار العميل", "Müşteri bekliyor",
              "SUM(pending)", better="down"),
    ],
    chart=R.chart("pareto", "s.style_ref", _FAILED, "s.style_ref",
                  "Failed rounds by style", "الجولات الفاشلة حسب الموديل",
                  "Modele göre başarısız tur"),
)


# ---------------------------------------------------------------------------
# 2. Style pipeline
# ---------------------------------------------------------------------------
R.register(
    key="plm_style_pipeline", **_MOD,
    title="Style Pipeline", title_ar="مسار الموديلات", title_tr="Model Hattı",
    desc="Where every style stands, with its tech-pack versions and sample rounds.",
    desc_ar="موقف كل موديل مع عدد نسخ الملف الفني وجولات العينات.",
    desc_tr="Her modelin durumu, teknik dosya sürümleri ve numune turları ile.",
    perm="plm_view",
    select="s.style_ref AS style_ref, s.name AS style, s.buyer AS buyer, "
           "s.season AS season, s.category AS category, s.status AS status, "
           "s.merchandiser AS merchandiser, "
           "(SELECT COUNT(*) FROM plm_techpacks t WHERE t.style_id = s.id) AS versions, "
           "(SELECT COUNT(*) FROM plm_samples x WHERE x.style_id = s.id) AS rounds, "
           "(SELECT COUNT(*) FROM plm_samples x WHERE x.style_id = s.id "
           " AND x.verdict = 'pending') AS pending, "
           "s.updated_at AS updated_at",
    frm="plm_styles s",
    date_col="s.created_at",
    order="s.updated_at DESC, s.id DESC",
    columns=[
        R.col("style_ref", "Style ref", "كود الموديل", "Model kodu"),
        R.col("style", "Style", "الموديل", "Model"),
        R.col("buyer", "Buyer", "العميل", "Müşteri"),
        R.col("season", "Season", "الموسم", "Sezon"),
        R.col("category", "Category", "الفئة", "Kategori"),
        R.col("status", "Status", "الحالة", "Durum"),
        R.col("merchandiser", "Merchandiser", "مسؤول الحساب", "Merchandiser"),
        # no totals row on the three counts: they are per-style scalar sub-queries
        # and a SUM() over them would cost a second pass for a number nobody acts on
        R.col("versions", "Tech-pack versions", "نسخ الملف الفني", "Teknik dosya sürümü", "num"),
        R.col("rounds", "Sample rounds", "جولات العينات", "Numune turu", "num"),
        R.col("pending", "Pending rounds", "جولات معلقة", "Bekleyen tur", "num"),
        R.col("updated_at", "Updated", "آخر تحديث", "Güncellendi", "date"),
    ],
    filters=[
        R.filt("buyer", "Buyer", "العميل", "Müşteri", "s.buyer"),
        R.filt("season", "Season", "الموسم", "Sezon", "s.season"),
        R.filt("status", "Status", "الحالة", "Durum", "s.status", "select", "=",
               [("development", "Development", "قيد التطوير", "Geliştirme"),
                ("sampling", "Sampling", "قيد العينات", "Numune"),
                ("approved", "Approved", "معتمد", "Onaylı"),
                ("in_production", "In production", "قيد الإنتاج", "Üretimde"),
                ("dropped", "Dropped", "ملغى", "İptal")]),
    ],
    kpis=[
        R.kpi("styles", "Styles", "الموديلات", "Model", "COUNT(*)", better="none"),
        R.kpi("bulk", "Cleared for bulk", "معتمد للإنتاج", "Üretime hazır",
              "SUM(CASE WHEN s.status IN ('approved','in_production') THEN 1 ELSE 0 END)"),
        R.kpi("developing", "In development / sampling", "قيد التطوير أو العينات",
              "Geliştirme / numune",
              "SUM(CASE WHEN s.status IN ('development','sampling') THEN 1 ELSE 0 END)",
              better="none"),
        R.kpi("dropped", "Dropped", "ملغاة", "İptal",
              "SUM(CASE WHEN s.status = 'dropped' THEN 1 ELSE 0 END)", better="down"),
    ],
    chart=R.chart("bar", "s.status", "COUNT(*)", "s.status",
                  "Styles by status", "الموديلات حسب الحالة", "Duruma göre model"),
)
