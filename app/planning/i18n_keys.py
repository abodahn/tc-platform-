"""
Keys added by the SMV-source-of-truth / plan-vs-actual work: key -> (en, ar, tr).

app.js t(key) renders THE KEY ITSELF when a translation is missing, so a gap here
ships as raw text like "pln.actual.title" to every user, English included. The
orchestrator splices this map into app/static/i18n/{en,ar,tr}.json;
tests_spine.py fails if a template uses a key that is in neither this map nor
en.json.

The shared `smv.*` keys are NOT redefined here: the same disagreement tag is
rendered by planning, costing and the MES line page, so they live once next to
the resolver that emits them (app/services/smv.py) and are merged in below.
"""
from app.services.smv import I18N as _SMV_I18N

I18N = {
    # --- plan vs actual --------------------------------------------------
    "pln.actual.title": ("Plan vs actual — from the floor",
                         "الخطة مقابل الفعلي — من الإنتاج",
                         "Plan ve gerçekleşen — sahadan"),
    "pln.actual.hint": (
        "Measured from the hourly MES record. Only complete days count, so a shift "
        "still running is never reported as a shortfall.",
        "محسوبة من سجل الإنتاج بالساعة. تُحتسب الأيام المكتملة فقط، فلا تظهر الوردية "
        "الجارية كعجز.",
        "Saatlik MES kaydından ölçülür. Yalnızca tamamlanan günler sayılır; devam eden "
        "vardiya asla eksik olarak raporlanmaz."),
    "pln.actual.none": ("No actuals recorded for this order yet.",
                        "لا يوجد إنتاج فعلي مسجل لهذا الطلب بعد.",
                        "Bu sipariş için henüz gerçekleşen kayıt yok."),
    "pln.actual.none_short": ("no actuals", "لا يوجد فعلي", "gerçekleşen yok"),
    "pln.actual.unmanned": ("No manned hours recorded — efficiency cannot be measured.",
                            "لا توجد ساعات بعمالة مسجلة — لا يمكن قياس الكفاءة.",
                            "Operatörlü saat kaydı yok — verimlilik ölçülemez."),
    "pln.field.produced": ("Produced", "المنتَج فعليًا", "Üretilen"),
    "pln.field.expected": ("Expected to date", "المتوقع حتى تاريخه", "Bugüne kadar beklenen"),
    "pln.field.variance": ("Variance (pcs)", "الانحراف (قطعة)", "Sapma (adet)"),
    "pln.field.variance_days": ("Variance (days)", "الانحراف (أيام)", "Sapma (gün)"),
    "pln.field.eff_measured": ("Measured efficiency", "الكفاءة المقاسة", "Ölçülen verimlilik"),
    "pln.status.ahead": ("Ahead of plan", "متقدم عن الخطة", "Planın önünde"),
    "pln.status.behind": ("Behind plan", "متأخر عن الخطة", "Planın gerisinde"),
    "pln.status.on_plan": ("On plan", "مطابق للخطة", "Plana uygun"),
    "pln.status.no_actuals": ("No actuals", "لا يوجد فعلي", "Gerçekleşen yok"),
    "pln.kpi.behind": ("Orders behind the plan", "طلبات متأخرة عن الخطة",
                       "Planın gerisindeki siparişler"),
    "pln.kpi.smv_conflicts": ("SMV disagreements", "اختلافات في الدقيقة المعيارية",
                              "SMV uyuşmazlıkları"),
    "pln.action.apply_eff": ("Use measured", "استخدم المقاسة", "Ölçüleni kullan"),
    "pln.lines.eff_hint": (
        "Measured efficiency comes from the last 14 days of the MES hourly record. "
        "It is a suggestion — nothing changes until you apply it.",
        "الكفاءة المقاسة من آخر ١٤ يومًا من سجل الإنتاج بالساعة. إنها اقتراح — لا يتغير "
        "شيء حتى تعتمدها.",
        "Ölçülen verimlilik son 14 günlük saatlik MES kaydından gelir. Bu bir öneridir — "
        "siz uygulayana kadar hiçbir şey değişmez."),
}

I18N.update(_SMV_I18N)
