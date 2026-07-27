"""
Keys added when /factory stopped duplicating the real modules: key -> (en, ar, tr).

app.js t(key) renders THE KEY ITSELF when a translation is missing, so a gap here
ships as raw text like "sf.reads_from" to every user, English included. The
orchestrator splices this map into app/static/i18n/{en,ar,tr}.json;
tests_single_source.py fails if a /factory template uses a key that is in neither
this map nor en.json.

Garment vocabulary, kept consistent with the rest of the platform:
  SMV = الدقيقة المعيارية / standart dakika, efficiency = الكفاءة / verimlilik,
  plan = الخطة / plan, actual = الفعلي / gerçekleşen, variance = الانحراف / sapma,
  line = الخط / hat, order = الطلب / sipariş, capacity = الطاقة / kapasite.
"""

I18N = {
    # --- provenance: which module owns the numbers on this page --------------
    "sf.reads_from": (
        "Read-only view of live records owned by",
        "عرض للقراءة فقط لسجلات حيّة يملكها",
        "Şu modülün canlı kayıtlarının salt-okunur görünümü:"),
    "sf.showing": ("Showing", "المعروض", "Gösterilen"),
    "sf.showing_note": ("the last day the floor reported.",
                        "آخر يوم سجّل فيه الإنتاج بيانات.",
                        "sahanın rapor verdiği son gün."),

    # --- command center / floor ---------------------------------------------
    "sf.achievement": ("Achievement (actual/target)", "الإنجاز (الفعلي/الخطة)",
                       "Gerçekleşme (gerçekleşen/hedef)"),
    "sf.smv_eff": ("SMV efficiency", "كفاءة الدقيقة المعيارية", "SMV verimliliği"),

    # --- wash / sustainability ----------------------------------------------
    "sf.per_kg": ("per kg of goods", "لكل كيلوجرام من البضاعة", "kg ürün başına"),
    "sf.water_kg": ("Water / kg", "المياه / كجم", "Su / kg"),
    "sf.heat_kg": ("Heat index / kg", "مؤشر الحرارة / كجم", "Isı endeksi / kg"),
    "sf.chem_kg": ("Chemical / kg", "المواد الكيميائية / كجم", "Kimyasal / kg"),
    "sf.heat": ("Heat L·K", "الحرارة (لتر·كلفن)", "Isı L·K"),
    "sf.load_kg": ("Load kg", "الحمولة (كجم)", "Yük kg"),
    "sf.wash_sub2": (
        "Executed wash lots against their recipe version — water, chemicals and heat "
        "per kg of goods.",
        "دفعات الغسيل المنفَّذة مقابل نسخة الوصفة الخاصة بها — المياه والمواد الكيميائية "
        "والحرارة لكل كيلوجرام من البضاعة.",
        "Reçete sürümüne karşı yürütülen yıkama partileri — kg ürün başına su, kimyasal "
        "ve ısı."),
    "sf.deviations": ("Batches off recipe", "دفعات خارج الوصفة", "Reçete dışı partiler"),
    "sf.deviation": ("Deviation", "الانحراف", "Sapma"),
    "sf.from_recipe": ("(recipe)", "(من الوصفة)", "(reçeteden)"),
    "sf.wash_note": (
        "Water is the metered figure when the machine reported one, otherwise the "
        "recipe version's bath total scaled to the load that ran. Heat is an index "
        "(litre-kelvin), not kWh.",
        "المياه هي القراءة المقاسة عندما تسجّلها الماكينة، وإلا فهي إجمالي حمّامات نسخة "
        "الوصفة مُعدّلًا على الحمولة التي شُغّلت فعليًا. الحرارة مؤشر (لتر·كلفن) وليست "
        "كيلوواط·ساعة.",
        "Su, makine ölçüm bildirdiğinde ölçülen değerdir; aksi hâlde reçete "
        "sürümünün banyo toplamının çalışan yüke ölçeklenmiş hâlidir. Isı bir "
        "endekstir (litre-kelvin), kWh değildir."),

    # --- production / quality registers --------------------------------------
    "sf.date": ("Date", "التاريخ", "Tarih"),
    "sf.ref": ("Ref", "المرجع", "Referans"),
    "sf.verdict": ("Verdict", "القرار", "Karar"),
    "sf.production_sub": (
        "The MES hourly board — the same rows the floor records, not a second copy.",
        "لوحة الساعات في نظام تنفيذ الإنتاج — نفس السجلات التي يدخلها الإنتاج، وليست نسخة ثانية.",
        "MES saatlik panosu — sahanın kaydettiği satırların ta kendisi, ikinci bir kopya değil."),
    "sf.quality_sub": (
        "The QMS inspection register — AQL verdicts and the defect pareto behind them.",
        "سجل الفحص في نظام الجودة — قرارات مستوى الجودة المقبول وتحليل باريتو للعيوب خلفها.",
        "QMS muayene kaydı — AQL kararları ve arkasındaki hata Pareto analizi."),
    "sf.orders_sub": (
        "The platform order book, with the canonical SMV every module resolves.",
        "دفتر طلبات المنصة، مع الدقيقة المعيارية المعتمدة التي تقرأها كل الوحدات.",
        "Platformun sipariş defteri, tüm modüllerin çözümlediği tek standart dakika ile."),
    "sf.smv_differs": ("SMV differs", "الدقيقة المعيارية مختلفة", "SMV farklı"),

    # --- bundles / WIP / rolls ------------------------------------------------
    "sf.bundles_sub2": (
        "The MES bundle ledger and the warehouse roll master — one set of pieces.",
        "سجل الحزم في نظام تنفيذ الإنتاج ورولات المخزن — مجموعة قطع واحدة.",
        "MES demet defteri ve depo top kaydı — tek bir parça kümesi."),
    "sf.section": ("Section", "المرحلة", "Bölüm"),
    "sf.wip": ("WIP on the floor", "الإنتاج تحت التشغيل", "Sahadaki yarı mamul"),
    "sf.wip_section": ("WIP by section", "الإنتاج تحت التشغيل حسب المرحلة",
                       "Bölüme göre yarı mamul"),
    "sf.remaining": ("Remaining m", "المتبقي (متر)", "Kalan m"),
    "sf.no_rolls": ("No fabric rolls in the warehouse.", "لا توجد رولات أقمشة في المخزن.",
                    "Depoda kumaş topu yok."),

    # --- workforce -------------------------------------------------------------
    "sf.workforce_sub2": (
        "Operator scorecards from the piece-rate record — the same earned minutes payroll uses.",
        "بطاقات أداء العاملين من سجل الأجر بالقطعة — نفس الدقائق المكتسبة التي تحسب بها الأجور.",
        "Parça başı kayıttan operatör karneleri — bordronun kullandığı kazanılan dakikaların aynısı."),
    "sf.department": ("Department", "القسم", "Departman"),
    "sf.period": ("Period", "الفترة", "Dönem"),
    "sf.minute_weighted": (
        "efficiency is minute-weighted (earned ÷ worked), not an average of daily percentages.",
        "الكفاءة مرجّحة بالدقائق (المكتسبة ÷ المشتغلة)، وليست متوسطًا لنسب يومية.",
        "verimlilik dakika ağırlıklıdır (kazanılan ÷ çalışılan), günlük yüzdelerin ortalaması değil."),

    # --- floor cost ------------------------------------------------------------
    "sf.costing_sub2": (
        "What the floor did to the price: minutes run, pieces scrapped, utilities consumed.",
        "ما فعله الإنتاج بالتكلفة: الدقائق المشتغلة والقطع الهالكة والمرافق المستهلكة.",
        "Sahanın fiyata etkisi: çalışılan dakikalar, fire olan parçalar, tüketilen enerji ve su."),
    "sf.rate": ("Rate/min", "التكلفة/دقيقة", "Dakika ücreti"),
    "sf.scrap": ("Scrap", "الهالك", "Fire"),
    "sf.default_rate": ("(default)", "(افتراضي)", "(varsayılan)"),
    "sf.cost_note2": (
        "Conversion cost only — the priced cost sheet, the BOM, the booked actuals and "
        "the margin live in Costing. The minute rate is the order's own CM rate when it "
        "is set; rows marked \"(default)\" fall back to an internal tariff. Downtime is "
        "booked per line and per day, so it is not split across orders here.",
        "تكلفة التصنيع فقط — أما ورقة التكلفة المسعّرة وقائمة الخامات والتكاليف الفعلية "
        "وهامش الربح فتوجد في وحدة التكاليف. سعر الدقيقة هو سعر تصنيع الطلب نفسه عند "
        "ضبطه، والصفوف المعلَّمة بـ«افتراضي» ترجع إلى تعرفة داخلية. التوقف يُسجَّل لكل خط "
        "ولكل يوم، لذلك لا يُوزَّع على الطلبات هنا.",
        "Yalnızca dönüşüm maliyeti — fiyatlı maliyet sayfası, ürün ağacı, işlenen "
        "gerçekleşenler ve kâr marjı Maliyet modülündedir. Dakika ücreti, ayarlıysa "
        "siparişin kendi CM ücretidir; \"(varsayılan)\" işaretli satırlar dâhilî tarifeye "
        "döner. Duruş, hat ve gün bazında işlenir; bu yüzden burada siparişlere "
        "dağıtılmaz."),

    # --- efficiency bridge causes (built at render time) ----------------------
    "sf.cause.downtime": ("Downtime / no-feeding", "التوقف / نقص التغذية",
                          "Duruş / besleme yok"),
    "sf.cause.quality": ("Quality redo (rejects)", "إعادة العمل للجودة (المرفوضات)",
                         "Kalite tekrarı (ret)"),
    "sf.cause.balance": ("Line balance / bottleneck", "توازن الخط / الاختناق",
                         "Hat dengesi / darboğaz"),

    # --- reports ---------------------------------------------------------------
    "sf.reports_sub2": (
        "Production, quality, cost and wash — the live records, exported from one place.",
        "الإنتاج والجودة والتكلفة والغسيل — السجلات الحيّة، مُصدَّرة من مكان واحد.",
        "Üretim, kalite, maliyet ve yıkama — canlı kayıtlar, tek yerden dışa aktarılır."),

    # --- the one page still on its own data -----------------------------------
    "sf.own_data_h": ("Smart-Factory data only.", "بيانات المصنع الذكي فقط.",
                      "Yalnızca Akıllı Fabrika verisi."),
    "sf.own_data": (
        "This page runs on its own /factory approval tables. It is NOT connected to the "
        "platform's procurement approval ladder, and a request raised here approves "
        "nothing in Procurement. Its order list is the legacy /factory order list for "
        "the same reason.",
        "تعمل هذه الصفحة على جداول الموافقات الخاصة بـ/factory وحدها. وهي غير مرتبطة "
        "بسلسلة موافقات المشتريات في المنصة، والطلب المرفوع هنا لا يعتمد شيئًا في "
        "المشتريات. ولنفس السبب فإن قائمة الطلبات فيها هي قائمة /factory القديمة.",
        "Bu sayfa kendi /factory onay tablolarıyla çalışır. Platformun satın alma onay "
        "zinciriyle bağlantılı DEĞİLDİR ve burada açılan bir talep Satın Alma'da hiçbir "
        "şeyi onaylamaz. Sipariş listesi de aynı nedenle eski /factory listesidir."),
}
