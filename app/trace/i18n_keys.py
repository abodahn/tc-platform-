"""
Every data-i18n key rendered by app/templates/trace/*.html -> (en, ar, tr).

app.js t(key) returns THE KEY when a translation is missing and applies it
unconditionally, so a key that is not merged into app/static/i18n/*.json shows
up on screen as literal text like "trc.field.lot". tests_adversarial.py diffs
this map against the templates in both directions, so a new string cannot ship
untranslated and a dead key cannot linger.
"""

I18N = {
    # --- chrome / navigation ------------------------------------------------
    "trc.eyebrow": ("Sustainability · Traceability & Product Passport",
                    "الاستدامة · التتبع وجواز المنتج",
                    "Sürdürülebilirlik · İzlenebilirlik ve Ürün Pasaportu"),
    "trc.nav.home": ("Traceability & DPP", "التتبع وجواز المنتج الرقمي",
                     "İzlenebilirlik ve DÜP"),
    "trc.nav.partners": ("Supply-chain partners", "شركاء سلسلة التوريد",
                         "Tedarik zinciri iş ortakları"),
    "trc.nav.lots": ("Material lots", "دفعات الخامات", "Malzeme partileri"),
    "trc.nav.certs": ("Material certificates", "شهادات الخامات", "Malzeme sertifikaları"),
    "trc.nav.passports": ("Product passports", "جوازات المنتج", "Ürün pasaportları"),
    "trc.nav.esg": ("ESG footprint register", "سجل الأثر البيئي والاجتماعي",
                    "ÇSY ayak izi kaydı"),

    # --- page subtitles -----------------------------------------------------
    "trc.dash.sub": ("Fibre-to-product chain of custody, material certificates and the EU "
                     "Digital Product Passport data set — built before the deadline, not after it.",
                     "سلسلة عهدة من الليف إلى المنتج، وشهادات الخامات، وبيانات جواز المنتج الرقمي "
                     "الأوروبي — جاهزة قبل الموعد النهائي لا بعده.",
                     "Elyaftan ürüne gözetim zinciri, malzeme sertifikaları ve AB Dijital Ürün "
                     "Pasaportu veri seti — son tarihten sonra değil, önce hazır."),
    "trc.partners.sub": ("Every mill, spinner, dyehouse and farm behind the product, placed at its tier.",
                         "كل مصنع نسيج وغزل وصباغة ومزرعة خلف المنتج، مرتبًا حسب مستواه.",
                         "Ürünün arkasındaki her dokuma, iplik, boyahane ve çiftlik, kademesiyle birlikte."),
    "trc.lots.sub": ("Each lot points at the lot it was made from. That single link is what turns "
                     "a fabric roll into a fibre-to-product chain.",
                     "كل دفعة تشير إلى الدفعة التي صُنعت منها. هذا الرابط الواحد هو ما يحوّل لفة "
                     "القماش إلى سلسلة من الليف إلى المنتج.",
                     "Her parti, üretildiği partiyi işaret eder. Bir kumaş topunu elyaftan ürüne "
                     "zincire dönüştüren tek bağlantı budur."),
    "trc.certs.sub": ("GOTS, OEKO-TEX, GRS, RCS, ZDHC and bluesign on the MATERIAL. Factory "
                      "certificates (WRAP, BSCI, licences) live in Compliance.",
                      "شهادات GOTS و OEKO-TEX و GRS و RCS و ZDHC و bluesign على الخامة. أما شهادات "
                      "المصنع (WRAP وBSCI والتراخيص) فمكانها وحدة الامتثال.",
                      "MALZEMEYE ait GOTS, OEKO-TEX, GRS, RCS, ZDHC ve bluesign. Fabrika "
                      "sertifikaları (WRAP, BSCI, lisanslar) Uyum modülünde tutulur."),
    "trc.passports.sub": ("One passport per order. The completeness score is the to-do list before "
                          "the EU ESPR passport can be filed.",
                          "جواز واحد لكل أمر توريد. نسبة الاكتمال هي قائمة المهام قبل تقديم جواز "
                          "المنتج الأوروبي ESPR.",
                          "Her sipariş için bir pasaport. Tamlık puanı, AB ESPR pasaportu "
                          "sunulmadan önceki yapılacaklar listesidir."),
    "trc.esg.sub": ("Energy, water and waste as recorded per order or per month, divided by pieces produced.",
                    "الطاقة والمياه والمخلفات كما هي مسجلة لكل أمر توريد أو لكل شهر، مقسومة على "
                    "عدد القطع المنتجة.",
                    "Sipariş veya ay bazında kaydedilen enerji, su ve atık; üretilen adede bölünür."),
    "trc.passport.title": ("Digital Product Passport", "جواز المنتج الرقمي", "Dijital Ürün Pasaportu"),

    # --- KPIs ---------------------------------------------------------------
    "trc.kpi.partners": ("Supply-chain partners", "شركاء سلسلة التوريد", "Tedarik zinciri iş ortakları"),
    "trc.kpi.lots": ("Material lots", "دفعات الخامات", "Malzeme partileri"),
    "trc.kpi.lots_traced": ("Lots with an upstream link", "دفعات لها رابط أعلى السلسلة",
                            "Üst kademeye bağlı partiler"),
    "trc.kpi.certs_valid": ("Certificates valid", "شهادات سارية", "Geçerli sertifikalar"),
    "trc.kpi.certs_expiring": ("Expiring soon", "قاربت على الانتهاء", "Yakında sona erecek"),
    "trc.kpi.certs_expired": ("Expired", "منتهية", "Süresi dolmuş"),
    "trc.kpi.avg_complete": ("Average passport completeness", "متوسط اكتمال الجواز",
                             "Ortalama pasaport tamlığı"),
    "trc.kpi.weak": ("Passports below target", "جوازات دون المستهدف", "Hedefin altındaki pasaportlar"),
    "trc.kpi.orders_covered": ("Live orders assessed", "أوامر التوريد النشطة المقيَّمة",
                               "Değerlendirilen aktif siparişler"),
    "trc.kpi.tier4": ("Tier-4 fibre sources", "مصادر الألياف بالمستوى الرابع",
                      "Kademe-4 elyaf kaynakları"),
    "trc.kpi.completeness": ("Passport completeness", "اكتمال الجواز", "Pasaport tamlığı"),
    "trc.kpi.points": ("Data points present", "بنود البيانات المتوفرة", "Mevcut veri noktaları"),
    "trc.kpi.deepest": ("Deepest tier reached", "أعمق مستوى تم الوصول إليه", "Ulaşılan en derin kademe"),
    "trc.kpi.chain_nodes": ("Chain nodes", "حلقات السلسلة", "Zincir düğümleri"),
    "trc.kpi.chain_steps": ("Chain steps", "خطوات السلسلة", "Zincir adımları"),
    "trc.kpi.chain_certs": ("Certificates on the chain", "شهادات على السلسلة", "Zincirdeki sertifikalar"),
    "trc.kpi.energy_pc": ("kWh / garment", "كيلوواط ساعة / قطعة", "kWh / giysi"),
    "trc.kpi.water_pc": ("Water L / garment", "لتر ماء / قطعة", "Su L / giysi"),
    "trc.kpi.waste_pc": ("Waste g / garment", "جرام مخلفات / قطعة", "Atık g / giysi"),
    "trc.kpi.esg_records": ("Records", "السجلات", "Kayıtlar"),

    # --- panel headings -----------------------------------------------------
    "trc.dash.tiers": ("Supply chain by tier", "سلسلة التوريد حسب المستوى", "Kademeye göre tedarik zinciri"),
    "trc.dash.watch": ("Material certificates expiring", "شهادات خامات قاربت على الانتهاء",
                       "Süresi dolmak üzere olan malzeme sertifikaları"),
    "trc.dash.passports": ("Passport completeness by order", "اكتمال الجواز حسب أمر التوريد",
                           "Siparişe göre pasaport tamlığı"),
    "trc.dash.tier_note": ("Traceability depth is how far upstream the chain actually reaches — "
                           "tier 4 is the fibre itself.",
                           "عمق التتبع هو مدى وصول السلسلة فعليًا إلى أعلى — المستوى الرابع هو الليف نفسه.",
                           "İzlenebilirlik derinliği, zincirin gerçekte ne kadar yukarı ulaştığıdır — "
                           "kademe 4 elyafın kendisidir."),
    "trc.passport.checklist": ("Required data points", "بنود البيانات المطلوبة", "Gerekli veri noktaları"),
    "trc.passport.product": ("Product declaration", "بيان المنتج", "Ürün beyanı"),
    "trc.passport.chain": ("Chain of custody", "سلسلة العهدة", "Gözetim zinciri"),
    "trc.passport.certs": ("Material certificates on this chain", "شهادات الخامات على هذه السلسلة",
                           "Bu zincirdeki malzeme sertifikaları"),
    "trc.passport.esg": ("Recorded ESG footprint", "الأثر البيئي المسجل", "Kaydedilen ÇSY ayak izi"),
    "trc.lot.chain": ("Upstream chain", "السلسلة أعلى المنبع", "Üst kademe zinciri"),
    "trc.lot.certs": ("Certificates covering this chain", "الشهادات التي تغطي هذه السلسلة",
                      "Bu zinciri kapsayan sertifikalar"),
    "trc.lot.orders": ("Used on orders", "مستخدمة في أوامر توريد", "Kullanıldığı siparişler"),
    "trc.lot.no_upstream": ("No upstream lot recorded — traceability stops at this supplier.",
                            "لا توجد دفعة أعلى مسجلة — يتوقف التتبع عند هذا المورد.",
                            "Üst kademe partisi kaydedilmemiş — izlenebilirlik bu tedarikçide duruyor."),

    # --- table / form fields ------------------------------------------------
    "trc.field.tier": ("Tier", "المستوى", "Kademe"),
    "trc.field.partner": ("Partner", "الشريك", "İş ortağı"),
    "trc.field.partners": ("Partners", "الشركاء", "İş ortakları"),
    "trc.field.country": ("Country", "الدولة", "Ülke"),
    "trc.field.role": ("Role", "الدور", "Rol"),
    "trc.field.certifications": ("Certifications", "الشهادات", "Sertifikalar"),
    "trc.field.status": ("Status", "الحالة", "Durum"),
    "trc.field.contact": ("Contact", "جهة الاتصال", "İletişim kişisi"),
    "trc.field.email": ("Contact e-mail", "البريد الإلكتروني", "İletişim e-postası"),
    "trc.field.lot": ("Lot", "الدفعة", "Parti"),
    "trc.field.lots": ("Chain nodes", "حلقات السلسلة", "Zincir düğümleri"),
    "trc.field.material": ("Material", "الخامة", "Malzeme"),
    "trc.field.composition": ("Fibre composition", "تركيب الألياف", "Elyaf bileşimi"),
    "trc.field.qty": ("Qty", "الكمية", "Miktar"),
    "trc.field.qty_used": ("Qty used", "الكمية المستخدمة", "Kullanılan miktar"),
    "trc.field.uom": ("Unit", "الوحدة", "Birim"),
    "trc.field.received": ("Received", "تاريخ الاستلام", "Teslim alındı"),
    "trc.field.parent": ("From lot", "من الدفعة", "Kaynak parti"),
    "trc.field.step": ("Step", "الخطوة", "Adım"),
    "trc.field.standard": ("Standard", "المعيار", "Standart"),
    "trc.field.certno": ("Certificate no.", "رقم الشهادة", "Sertifika no."),
    "trc.field.issuer": ("Issuer", "جهة الإصدار", "Düzenleyen kurum"),
    "trc.field.scope": ("Scope", "النطاق", "Kapsam"),
    "trc.field.covers": ("Covers", "يغطي", "Kapsadığı"),
    "trc.field.valid_from": ("Valid from", "ساري من", "Geçerlilik başlangıcı"),
    "trc.field.valid_until": ("Valid until", "ساري حتى", "Geçerlilik bitişi"),
    "trc.field.days": ("Days", "الأيام", "Gün"),
    "trc.field.order": ("Order", "أمر التوريد", "Sipariş"),
    "trc.field.buyer": ("Buyer", "المشتري", "Alıcı"),
    "trc.field.ship": ("Ship date", "تاريخ الشحن", "Sevk tarihi"),
    "trc.field.depth": ("Chain depth", "عمق السلسلة", "Zincir derinliği"),
    "trc.field.completeness": ("Completeness", "نسبة الاكتمال", "Tamlık"),
    "trc.field.missing": ("Missing", "الناقص", "Eksik"),
    "trc.field.origin": ("Country of origin", "بلد المنشأ", "Menşe ülkesi"),
    "trc.field.material_origin": ("Material origins", "مناشئ الخامات", "Malzeme menşeleri"),
    "trc.field.recycled": ("Recycled content", "المحتوى المعاد تدويره", "Geri dönüştürülmüş içerik"),
    "trc.field.care": ("Care instructions", "تعليمات العناية", "Bakım talimatları"),
    "trc.field.recycling": ("End-of-life / recycling", "نهاية العمر / إعادة التدوير",
                            "Kullanım ömrü sonu / geri dönüşüm"),
    "trc.field.energy": ("Energy kWh", "الطاقة كيلوواط ساعة", "Enerji kWh"),
    "trc.field.water": ("Water m³", "المياه م³", "Su m³"),
    "trc.field.waste": ("Waste kg", "المخلفات كجم", "Atık kg"),
    "trc.field.garments": ("Garments", "عدد القطع", "Giysi adedi"),
    "trc.field.source": ("Source of the figures", "مصدر الأرقام", "Rakamların kaynağı"),
    "trc.field.period": ("Period (YYYY-MM)", "الفترة (سنة-شهر)", "Dönem (YYYY-AA)"),
    "trc.field.scope_label": ("Scope", "النطاق", "Kapsam"),

    # --- actions ------------------------------------------------------------
    "trc.action.add_partner": ("Add supply-chain partner", "إضافة شريك سلسلة توريد",
                               "Tedarik zinciri iş ortağı ekle"),
    "trc.action.add_lot": ("Add material lot", "إضافة دفعة خامة", "Malzeme partisi ekle"),
    "trc.action.add_cert": ("Add material certificate", "إضافة شهادة خامة", "Malzeme sertifikası ekle"),
    "trc.action.add_esg": ("Record ESG consumption", "تسجيل استهلاك بيئي", "ÇSY tüketimi kaydet"),
    "trc.action.renew": ("Renew", "تجديد", "Yenile"),
    "trc.action.save": ("Save", "حفظ", "Kaydet"),
    "trc.action.link_lot": ("Link a material lot", "ربط دفعة خامة", "Malzeme partisi bağla"),
    "trc.action.unlink": ("Unlink", "إلغاء الربط", "Bağlantıyı kaldır"),
    "trc.action.upstream": ("upstream", "أعلى السلسلة", "üst kademe"),
    "trc.action.open_order": ("Open order", "فتح أمر التوريد", "Siparişi aç"),
    "trc.action.edit_passport": ("Passport declaration", "بيان الجواز", "Pasaport beyanı"),
    "trc.action.all_certs": ("All certificates", "كل الشهادات", "Tüm sertifikalar"),
    "trc.action.all_passports": ("All passports", "كل الجوازات", "Tüm pasaportlar"),
    "trc.action.export": ("Export CSV", "تصدير CSV", "CSV olarak indir"),

    # --- filters / options --------------------------------------------------
    "trc.filter.all_tiers": ("All tiers", "كل المستويات", "Tüm kademeler"),
    "trc.filter.all_standards": ("All standards", "كل المعايير", "Tüm standartlar"),
    "trc.filter.clear": ("Clear", "مسح", "Temizle"),
    "trc.opt.none": ("— none —", "— بدون —", "— yok —"),

    # --- tiers (rendered as data-i18n="trc.tier.{{ n }}") -------------------
    "trc.tier.1": ("Tier 1 — CMT / garment assembly", "المستوى 1 — التصنيع وتجميع الملابس",
                   "Kademe 1 — CMT / konfeksiyon montajı"),
    "trc.tier.2": ("Tier 2 — fabric mill (knit / weave)", "المستوى 2 — مصنع الأقمشة (تريكو / نسيج)",
                   "Kademe 2 — kumaş fabrikası (örme / dokuma)"),
    "trc.tier.3": ("Tier 3 — spinner / dyehouse / wet processing",
                   "المستوى 3 — الغزل / الصباغة / التجهيز الرطب",
                   "Kademe 3 — iplik / boyahane / yaş işlem"),
    "trc.tier.4": ("Tier 4 — fibre farm / raw material", "المستوى 4 — مزرعة الألياف / الخامة الأولية",
                   "Kademe 4 — elyaf çiftliği / ham madde"),

    # --- statuses & badges --------------------------------------------------
    "trc.status.valid": ("valid", "سارية", "geçerli"),
    "trc.status.expiring": ("expiring", "قاربت على الانتهاء", "süresi doluyor"),
    "trc.status.expired": ("expired", "منتهية", "süresi dolmuş"),
    "trc.status.revoked": ("revoked", "ملغاة", "iptal edilmiş"),
    # a certificate whose validity STARTS in the future — never shown as 'valid'
    "trc.status.pending": ("not yet in force", "لم تدخل حيّز السريان بعد", "henüz yürürlükte değil"),
    "trc.status.unknown": ("unknown", "غير معروف", "bilinmiyor"),
    "trc.status.active": ("active", "نشط", "aktif"),
    "trc.badge.below": ("below target", "دون المستهدف", "hedefin altında"),
    "trc.badge.untraced": ("Not traced", "غير متتبع", "İzlenmiyor"),
    "trc.badge.chain_end": ("chain ends here", "تنتهي السلسلة هنا", "zincir burada bitiyor"),
    "trc.badge.no_supplier": ("no supplier", "بدون مورد", "tedarikçi yok"),

    # --- DPP checklist points (constants.DPP_POINTS) ------------------------
    "trc.dpp.lots_linked": ("A material lot is linked to the order",
                            "ربط دفعة خامة بأمر التوريد",
                            "Siparişe bir malzeme partisi bağlandı"),
    "trc.dpp.composition": ("Every lot in the chain states its fibre composition",
                            "كل دفعة في السلسلة تذكر تركيب أليافها",
                            "Zincirdeki her parti elyaf bileşimini belirtiyor"),
    "trc.dpp.supplier": ("Every lot in the chain names its supplier",
                         "كل دفعة في السلسلة تحدد المورد",
                         "Zincirdeki her parti tedarikçisini belirtiyor"),
    "trc.dpp.tier3": ("A tier-3 spinner / dyehouse is named in the chain",
                      "السلسلة تحدد مصنع غزل / صباغة من المستوى 3",
                      "Zincirde kademe 3 iplikçi / boyahane belirtilmiş"),
    "trc.dpp.tier4": ("The chain reaches tier 4 (fibre origin)",
                      "تصل السلسلة إلى المستوى 4 (منشأ الليف)",
                      "Zincir kademe 4'e ulaşıyor (elyaf menşei)"),
    "trc.dpp.certificates": ("A valid material certificate covers the chain",
                             "شهادة خامة سارية تغطي السلسلة",
                             "Zinciri kapsayan geçerli bir malzeme sertifikası var"),
    "trc.dpp.origin": ("Country of origin is declared", "بلد المنشأ معلن", "Menşe ülkesi beyan edildi"),
    "trc.dpp.care": ("Care instructions are declared", "تعليمات العناية معلنة",
                     "Bakım talimatları beyan edildi"),
    "trc.dpp.recycling": ("End-of-life / recycling information is declared",
                          "معلومات نهاية العمر / إعادة التدوير معلنة",
                          "Kullanım ömrü sonu / geri dönüşüm bilgisi beyan edildi"),
    "trc.dpp.footprint": ("An ESG footprint is recorded for the order",
                          "تم تسجيل الأثر البيئي لأمر التوريد",
                          "Sipariş için ÇSY ayak izi kaydedildi"),

    # --- empty states -------------------------------------------------------
    "trc.empty.partners": ("No supply-chain partners recorded yet.", "لم يتم تسجيل شركاء سلسلة توريد بعد.",
                           "Henüz tedarik zinciri iş ortağı kaydedilmedi."),
    "trc.empty.lots": ("No material lots recorded yet.", "لم يتم تسجيل دفعات خامات بعد.",
                       "Henüz malzeme partisi kaydedilmedi."),
    "trc.empty.certs": ("No material certificates recorded yet.", "لم يتم تسجيل شهادات خامات بعد.",
                        "Henüz malzeme sertifikası kaydedilmedi."),
    "trc.empty.esg": ("No ESG records yet.", "لا توجد سجلات بيئية بعد.", "Henüz ÇSY kaydı yok."),
    "trc.empty.watch": ("No material certificate is close to expiry.",
                        "لا توجد شهادة خامة قاربت على الانتهاء.",
                        "Süresi dolmak üzere olan malzeme sertifikası yok."),
    "trc.empty.passports": ("No live orders to assess yet.", "لا توجد أوامر توريد نشطة للتقييم بعد.",
                            "Değerlendirilecek aktif sipariş yok."),
    "trc.empty.chain": ("No material lot is linked to this order yet — the passport cannot be traced.",
                        "لا توجد دفعة خامة مرتبطة بهذا الأمر بعد — لا يمكن تتبع الجواز.",
                        "Bu siparişe henüz malzeme partisi bağlanmadı — pasaport izlenemiyor."),
    "trc.empty.chain_certs": ("No material certificate covers this chain.",
                              "لا توجد شهادة خامة تغطي هذه السلسلة.",
                              "Bu zinciri kapsayan malzeme sertifikası yok."),

    # --- warnings & disclaimers --------------------------------------------
    "trc.warn.truncated": ("The upstream walk was stopped early — this chain loops back on itself "
                           "or is longer than the safety limit. Fix the parent lot links.",
                           "تم إيقاف التتبع لأعلى مبكرًا — هذه السلسلة تعود على نفسها أو أطول من الحد "
                           "الآمن. صحّح روابط الدفعات الأصل.",
                           "Üst kademe taraması erken durduruldu — bu zincir kendine dönüyor ya da "
                           "güvenlik sınırından uzun. Üst parti bağlantılarını düzeltin."),
    "trc.note.register": ("Live orders by ship date, soonest first. Orders with no ship date come last.",
                          "أوامر التوريد النشطة حسب تاريخ الشحن، الأقرب أولًا. والأوامر بدون تاريخ شحن في الآخر.",
                          "Aktif siparişler sevk tarihine göre, en yakın önce. Sevk tarihi olmayanlar en sonda."),
    "trc.esg.disclaimer": ("Recorded metered/invoiced data divided by pieces produced. This is not "
                           "a certified LCA or a verified carbon footprint.",
                           "بيانات مسجلة من العدادات/الفواتير مقسومة على عدد القطع المنتجة. هذا ليس "
                           "تقييم دورة حياة معتمدًا ولا بصمة كربونية موثقة.",
                           "Sayaç/fatura verisi üretilen adede bölünmüştür. Bu, sertifikalı bir YDD "
                           "veya doğrulanmış bir karbon ayak izi değildir."),

    # --- placeholders (data-i18n-ph) ---------------------------------------
    "trc.ph.material": ("Single jersey 180 gsm", "سنجل جيرسيه 180 جم/م²", "Süprem 180 g/m²"),
    "trc.ph.lot": ("auto if left blank", "تلقائي إذا تُرك فارغًا", "boş bırakılırsa otomatik"),
    "trc.ph.composition": ("100% Organic Cotton", "100% قطن عضوي", "%100 Organik Pamuk"),
    "trc.ph.certifications": ("GOTS, OEKO-TEX Standard 100", "GOTS، OEKO-TEX Standard 100",
                              "GOTS, OEKO-TEX Standard 100 (belgeler)"),
    "trc.ph.source": ("Sub-meter reading / utility invoice", "قراءة عداد فرعي / فاتورة مرافق",
                      "Alt sayaç okuması / fatura"),
    "trc.ph.esg_label": ("Site total — current month", "إجمالي الموقع — الشهر الحالي",
                         "Tesis toplamı — bu ay"),
}
