"""Costing module constants — BOM kinds, cost categories, RBAC perms."""

# What a BOM line is. Drives grouping/colour on the cost sheet, nothing else.
BOM_KINDS = ["fabric", "trim", "other"]

# Cost-sheet categories, in report order. 'material' is DERIVED from the BOM —
# it is never typed in; the other five are entered per-unit figures.
CATEGORIES = ["material", "cm", "overhead", "freight", "duty", "other"]

# Common BOM units. Free text is still accepted; this is just the picker.
UOMS = ["m", "yd", "kg", "pcs", "set", "cone", "ctn", "dz"]

# Actual total may exceed the estimate by this % before the bell rings.
VARIANCE_ALERT_PCT = 5.0

# RBAC — folded into the platform catalogue by app.security._merge_module_rbac.
# Margin data is commercially sensitive, so costing does NOT ride on view_dashboard.
COST_PERMISSIONS = ["cost_view", "cost_manage"]

COST_ROLE_PERMS = {
    "production_manager": ["cost_view", "cost_manage"],
    "finance_user": ["cost_view", "cost_manage"],
    "executive_viewer": ["cost_view"],
    "it_director": ["cost_view"],
}

# No new roles — the people who own cost sheets already have accounts.
COST_ROLE_LABELS = {}

PERMISSION_LABELS = {
    "cost_view": "Costing: view cost sheets & margin",
    "cost_manage": "Costing: manage BOM, cost sheets & actuals",
}

PERMISSION_DESC = {
    "cost_view": "See per-order BOM, estimate, actual cost, variance and margin.",
    "cost_manage": "Add/edit BOM lines, set cost-sheet figures, book actual costs "
                   "and link purchase requisitions to an order.",
}

# Every data-i18n key used by app/templates/costing/*.html, in all three factory
# languages. app.js t() renders the KEY ITSELF when a translation is missing, so a
# gap here shows up on screen as "cst.field.roll". tests_adversarial.py diffs the
# templates against this map.
I18N = {
    "cst.nav.home": ("BOM & Order Costing", "قائمة الخامات وتكلفة الأوامر", "Reçete ve Sipariş Maliyeti"),
    "cst.nav.orders": ("All orders", "كل الأوامر", "Tüm siparişler"),
    "cst.eyebrow": ("Planning · BOM & Order Costing", "التخطيط · قائمة الخامات وتكلفة الأوامر", "Planlama · Reçete ve Sipariş Maliyeti"),
    "cst.dash.sub": ("Every order costed once and reconciled to what it actually cost — before the margin is gone.",
                     "كل أمر يُسعَّر مرة ويُطابَق بتكلفته الفعلية — قبل أن يضيع هامش الربح.",
                     "Her sipariş bir kez maliyetlendirilir ve gerçek maliyetiyle karşılaştırılır — kâr marjı kaybolmadan önce."),
    "cst.dash.worst": ("Biggest overrun", "أكبر تجاوز للتكلفة", "En büyük maliyet aşımı"),
    "cst.dash.all": ("Costed orders", "الأوامر المسعّرة", "Maliyetlendirilmiş siparişler"),
    "cst.orders.sub": ("Quoted margin against delivered margin, order by order.",
                       "الهامش المعروض مقابل الهامش المحقق، أمرًا بأمر.",
                       "Teklif marjı ile gerçekleşen marj, sipariş sipariş."),
    "cst.filter.costed_only": ("Only orders with a BOM or cost sheet",
                               "الأوامر التي لها قائمة خامات أو ورقة تكلفة فقط",
                               "Yalnızca reçetesi veya maliyet formu olan siparişler"),
    "cst.filter.clear": ("Clear", "مسح", "Temizle"),

    "cst.kpi.costed": ("Orders costed", "أوامر مسعّرة", "Maliyetlenen sipariş"),
    "cst.kpi.unfav": ("Over estimate", "تجاوزت التقدير", "Tahmini aşan"),
    "cst.kpi.est_total": ("Estimated cost (all)", "التكلفة التقديرية (الإجمالي)", "Tahmini maliyet (tümü)"),
    "cst.kpi.act_total": ("Actual booked", "الفعلي المسجَّل", "Kaydedilen gerçekleşen"),
    "cst.kpi.worst": ("Worst variance", "أسوأ انحراف", "En kötü sapma"),
    "cst.kpi.revenue": ("Selling value", "قيمة البيع", "Satış değeri"),
    "cst.kpi.estimate": ("Estimated cost", "التكلفة التقديرية", "Tahmini maliyet"),
    "cst.kpi.actual": ("Actual cost", "التكلفة الفعلية", "Gerçekleşen maliyet"),
    "cst.kpi.margin_est": ("Quoted margin", "الهامش المعروض", "Teklif marjı"),
    "cst.kpi.margin_act": ("Delivered margin", "الهامش المحقق", "Gerçekleşen marj"),

    "cst.panel.compare": ("Estimate vs actual", "التقديري مقابل الفعلي", "Tahmini ile gerçekleşen"),
    "cst.panel.sheet": ("Cost sheet (per unit)", "ورقة التكلفة (للقطعة)", "Maliyet formu (birim başına)"),
    "cst.panel.bom": ("Bill of materials", "قائمة الخامات", "Malzeme listesi"),
    "cst.panel.sources": ("Actual material sources", "مصادر الخامات الفعلية", "Gerçekleşen malzeme kaynakları"),
    "cst.panel.actuals": ("Booked actual costs", "التكاليف الفعلية المسجَّلة", "Kaydedilen gerçek maliyetler"),

    "cst.cat.material": ("Material", "الخامات", "Malzeme"),
    "cst.cat.cm": ("CM / labour", "أجر التصنيع / العمالة", "İşçilik (CM)"),
    "cst.cat.overhead": ("Overhead", "المصاريف غير المباشرة", "Genel giderler"),
    "cst.cat.freight": ("Freight & logistics", "الشحن والنقل", "Nakliye ve lojistik"),
    "cst.cat.duty": ("Duty", "الرسوم الجمركية", "Gümrük vergisi"),
    "cst.cat.other": ("Finance / other", "تمويل / أخرى", "Finansman / diğer"),

    "cst.kind.fabric": ("fabric", "قماش", "kumaş"),
    "cst.kind.trim": ("trim", "مستلزمات", "aksesuar"),
    "cst.kind.other": ("other", "أخرى", "diğer"),

    "cst.basis.issued": ("warehouse issues", "صرف المخزن", "depo çıkışları"),
    "cst.basis.procured": ("procurement receipts", "استلام المشتريات", "satın alma girişleri"),
    "cst.basis.manual": ("manual entries", "إدخال يدوي", "manuel girişler"),
    "cst.basis.none": ("nothing booked", "لا يوجد تسجيل", "kayıt yok"),

    "cst.field.order": ("Order", "الأمر", "Sipariş"),
    "cst.field.buyer": ("Buyer", "العميل", "Müşteri"),
    "cst.field.ship": ("Ship", "الشحن", "Sevkiyat"),
    "cst.field.qty": ("Qty", "الكمية", "Adet"),
    "cst.field.pcs": ("pcs", "قطعة", "adet"),
    "cst.field.unit": ("unit", "وحدة", "birim"),
    "cst.field.revenue": ("Revenue", "الإيراد", "Ciro"),
    "cst.field.estimate": ("Estimate", "التقديري", "Tahmini"),
    "cst.field.actual": ("Actual", "الفعلي", "Gerçekleşen"),
    "cst.field.variance": ("Variance", "الانحراف", "Sapma"),
    "cst.field.measure": ("Measure", "البند", "Ölçüt"),
    "cst.field.category": ("Category", "البند", "Kategori"),
    "cst.field.total_cost": ("Total cost", "إجمالي التكلفة", "Toplam maliyet"),
    "cst.field.cost_unit": ("Cost/unit", "التكلفة/قطعة", "Birim maliyet"),
    "cst.field.price_unit": ("Price/unit", "السعر/قطعة", "Birim fiyat"),
    "cst.field.margin": ("Margin", "الهامش", "Marj"),
    "cst.field.margin_est": ("Margin (est)", "الهامش (تقديري)", "Marj (tahmini)"),
    "cst.field.margin_act": ("Margin (act)", "الهامش (فعلي)", "Marj (gerçekleşen)"),
    "cst.field.basis": ("Actual basis", "أساس الفعلي", "Gerçekleşen kaynağı"),
    "cst.field.smv": ("SMV (minutes)", "الزمن المعياري (دقائق)", "SMV (dakika)"),
    "cst.field.cm_rate": ("Cost per minute", "تكلفة الدقيقة", "Dakika maliyeti"),
    "cst.field.cm_flat": ("Flat CM per unit (used when SMV × rate is not set)",
                          "أجر تصنيع ثابت للقطعة (يُستخدم عند عدم ضبط الزمن × السعر)",
                          "Sabit birim işçilik (SMV × oran girilmediğinde kullanılır)"),
    "cst.field.cm_effective": ("CM applied", "أجر التصنيع المطبَّق", "Uygulanan işçilik"),
    "cst.field.material_unit": ("Material/unit", "الخامات/قطعة", "Birim malzeme"),
    "cst.field.notes": ("Notes", "ملاحظات", "Notlar"),
    "cst.field.item": ("Material / item", "الخامة / الصنف", "Malzeme / kalem"),
    "cst.field.kind": ("Type", "النوع", "Tür"),
    "cst.field.colour": ("Colour", "اللون", "Renk"),
    "cst.field.consumption": ("Cons./unit", "الاستهلاك/قطعة", "Birim tüketim"),
    "cst.field.uom": ("Unit", "الوحدة", "Birim"),
    "cst.field.allowance": ("Allow. %", "نسبة الهالك %", "Fire %"),
    "cst.field.required": ("Required qty", "الكمية المطلوبة", "Gereken miktar"),
    "cst.field.unit_price": ("Unit price", "سعر الوحدة", "Birim fiyat"),
    "cst.field.line_cost": ("Line cost", "تكلفة السطر", "Satır maliyeti"),
    "cst.field.supplier": ("Supplier", "المورد", "Tedarikçi"),
    "cst.field.pr": ("PR", "طلب الشراء", "Satınalma talebi"),
    "cst.field.pr_ref": ("PR number", "رقم طلب الشراء", "Talep numarası"),
    "cst.field.vendor": ("Vendor", "المورد", "Tedarikçi"),
    "cst.field.status": ("Status", "الحالة", "Durum"),
    "cst.field.source": ("Source / reference", "المصدر / المرجع", "Kaynak / referans"),
    "cst.field.amount": ("Amount", "المبلغ", "Tutar"),
    "cst.field.amount_total": ("Amount (total)", "المبلغ (الإجمالي)", "Tutar (toplam)"),

    "cst.action.open_order": ("Open order", "فتح الأمر", "Siparişi aç"),
    "cst.action.back": ("Back", "رجوع", "Geri"),
    "cst.action.save": ("Save", "حفظ", "Kaydet"),
    "cst.action.remove": ("Remove", "حذف", "Kaldır"),
    "cst.action.save_sheet": ("Save cost sheet", "حفظ ورقة التكلفة", "Maliyet formunu kaydet"),
    "cst.action.add_bom": ("Add BOM line", "إضافة سطر خامات", "Reçete satırı ekle"),
    "cst.action.link_pr": ("Link to this order", "ربط بهذا الأمر", "Bu siparişe bağla"),
    "cst.action.add_actual": ("Book actual cost", "تسجيل تكلفة فعلية", "Gerçek maliyeti kaydet"),

    "cst.badge.unpriced": ("unpriced", "بدون سعر", "fiyatsız"),
    "cst.badge.no_actual": ("not booked", "غير مسجَّل", "kaydedilmedi"),
    "cst.badge.not_counted": ("not counted", "غير محتسب", "sayılmadı"),

    "cst.bom.formula": ("Required qty = order qty × consumption per unit × (1 + allowance %). "
                        "The allowance is cutting and process wastage, so it increases the buy.",
                        "الكمية المطلوبة = كمية الأمر × الاستهلاك للقطعة × (١ + نسبة الهالك ٪). "
                        "الهالك هو فاقد القص والتشغيل، لذلك يزيد كمية الشراء.",
                        "Gereken miktar = sipariş adedi × birim tüketim × (1 + fire %). "
                        "Fire, kesim ve üretim kaybıdır; bu nedenle alım miktarını artırır."),
    "cst.sources.note": ("Material bought on a linked purchase requisition and then issued from the "
                         "warehouse is one cost, not two. The actual is taken from a single source: "
                         "warehouse issues first, then procurement receipts, then manual entries.",
                         "الخامة المشتراة بطلب شراء مرتبط ثم المصروفة من المخزن هي تكلفة واحدة لا اثنتان. "
                         "يؤخذ الفعلي من مصدر واحد فقط: صرف المخزن أولًا، ثم استلام المشتريات، ثم الإدخال اليدوي.",
                         "Bağlı bir satınalma talebiyle alınan ve sonra depodan çıkılan malzeme iki değil tek "
                         "maliyettir. Gerçekleşen tek bir kaynaktan alınır: önce depo çıkışları, sonra satınalma "
                         "girişleri, sonra manuel girişler."),
    "cst.sources.issued": ("Issued from warehouse", "المصروف من المخزن", "Depodan çıkan"),
    "cst.sources.received": ("Received on linked PRs", "المستلم على طلبات الشراء المرتبطة", "Bağlı taleplerde teslim alınan"),
    "cst.sources.invoiced": ("Invoiced", "المفوتر", "Faturalanan"),
    "cst.sources.paid": ("Paid", "المدفوع", "Ödenen"),
    "cst.sources.na": ("not available", "غير متاح", "mevcut değil"),
    "cst.sources.mixed": ("Linked PRs in another currency are shown below but not added — there is no "
                          "order-currency rate to convert them.",
                          "طلبات الشراء المرتبطة بعملة أخرى معروضة أدناه لكنها غير مضافة — لا يوجد سعر صرف "
                          "لعملة الأمر لتحويلها.",
                          "Başka para birimindeki bağlı talepler aşağıda gösterilir ancak eklenmez — sipariş "
                          "para birimine çevirecek bir kur yok."),

    "cst.warn.unpriced": ("BOM lines have no unit price — those estimates are incomplete and understate the cost.",
                          "سطور في قائمة الخامات بدون سعر وحدة — تلك التقديرات ناقصة وتقلل التكلفة الحقيقية.",
                          "Bazı reçete satırlarında birim fiyat yok — bu tahminler eksiktir ve maliyeti olduğundan düşük gösterir."),
    "cst.warn.overrun": ("This order is running over its estimate — the quoted margin is not what will be delivered.",
                         "هذا الأمر يتجاوز تكلفته التقديرية — الهامش المعروض ليس ما سيتحقق.",
                         "Bu sipariş tahmini maliyetini aşıyor — teklif edilen marj gerçekleşmeyecek."),

    "cst.empty.bom": ("No BOM yet — add the fabric and trims that go into one garment.",
                      "لا توجد قائمة خامات بعد — أضف القماش والمستلزمات الداخلة في القطعة الواحدة.",
                      "Henüz reçete yok — bir ürüne giren kumaş ve aksesuarları ekleyin."),
    "cst.empty.prs": ("No purchase requisition linked to this order yet.",
                      "لا يوجد طلب شراء مرتبط بهذا الأمر بعد.",
                      "Bu siparişe bağlı satınalma talebi yok."),
    "cst.empty.actuals": ("Nothing booked yet.", "لم يُسجَّل شيء بعد.", "Henüz kayıt yok."),
    "cst.empty.costed": ("No order has a BOM or cost sheet yet — open an order to build one.",
                         "لا يوجد أمر له قائمة خامات أو ورقة تكلفة بعد — افتح أمرًا لإنشائها.",
                         "Henüz reçetesi veya maliyet formu olan sipariş yok — bir sipariş açıp oluşturun."),
    "cst.empty.orders": ("No orders to cost yet.", "لا توجد أوامر لتسعيرها بعد.", "Maliyetlenecek sipariş yok."),
}

# sheet.html shows every stored SMV for the order and flags a disagreement with the
# number it was priced on. The tag is shared with planning and the MES line page, so
# its wording is defined once beside the resolver rather than copied per module.
from app.services.smv import I18N as _SMV_I18N          # noqa: E402
I18N.update(_SMV_I18N)
