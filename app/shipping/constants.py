"""Shipping & export constants — incoterms, transport modes, statuses, tolerances, RBAC."""

# Incoterms 2020. The term decides who carries freight/insurance, so it prints on
# the commercial invoice and is what customs and the buyer's finance team read.
INCOTERMS = ["EXW", "FCA", "FAS", "FOB", "CFR", "CIF", "CPT", "CIP", "DAP", "DPU", "DDP"]

MODES = ["sea", "air", "road", "rail", "courier"]

SHIPMENT_STATUS = ["planned", "packed", "dispatched", "delivered", "cancelled"]

# A shipment may only be BORN before it leaves. Creating one straight into a locked
# status would freeze its packing list at birth, so the cartons could never be typed
# in and its invoice would stay empty for ever.
CREATE_STATUS = ["planned", "packed"]

# Pieces only count as SHIPPED once the goods have physically left the factory.
SHIPPED_STATUS = ("dispatched", "delivered")

# A dispatched packing list is a customs document — its lines must not change.
LOCKED_STATUS = ("dispatched", "delivered", "cancelled")

# Buyers tolerate a small packing variance; beyond it, short/over shipment is a
# chargeback (short = penalty + air-freight of the balance, over = unpaid goods).
QTY_TOLERANCE_PCT = 2.0

# ETDs inside this window appear on the dashboard's "leaving soon" list.
ETD_SOON_DAYS = 14

# --- RBAC (merged into the platform catalogue by app.security) -------------
SHP_PERMISSIONS = [
    "shp_view",     # see shipments, packing lists, invoices, reconciliation
    "shp_manage",   # create/edit shipments and packing-list lines
]

SHP_ROLE_PERMS = {
    "export_officer": ["shp_view", "shp_manage"],   # new role: export/logistics desk
    "production_manager": ["shp_view", "shp_manage"],
    "executive_viewer": ["shp_view"],
    "finance_user": ["shp_view"],                   # reads commercial invoices
    "compliance_officer": ["shp_view"],
}

SHP_ROLE_LABELS = {"export_officer": "Export & Logistics Officer"}

PERMISSION_LABELS = {
    "shp_view": "Shipping: view shipments & export documents",
    "shp_manage": "Shipping: manage shipments & packing lists",
}

# --- i18n ------------------------------------------------------------------
# Every data-i18n / data-i18n-ph key used by app/templates/shipping/*.html, in all three
# factory languages (en, ar, tr). app.js t() renders the KEY ITSELF when a translation is
# missing, so a gap here shows up on screen as raw text like "shp.field.cbm".
# tests_adversarial.py diffs the templates against this map in both directions.
I18N = {
    "shp.eyebrow": ("Logistics · Shipments & Export Documents",
                    "اللوجستيات · الشحنات ومستندات التصدير",
                    "Lojistik · Sevkiyatlar ve İhracat Belgeleri"),
    "shp.nav.home": ("Shipping", "الشحن", "Sevkiyat"),
    "shp.nav.list": ("All shipments", "كل الشحنات", "Tüm sevkiyatlar"),
    "shp.nav.recon": ("Reconciliation", "المطابقة", "Mutabakat"),

    "shp.action.new": ("New shipment", "شحنة جديدة", "Yeni sevkiyat"),
    "shp.action.create": ("Create shipment", "إنشاء شحنة", "Sevkiyat oluştur"),
    "shp.action.update": ("Update", "تحديث", "Güncelle"),
    "shp.action.add_carton": ("Add line", "إضافة سطر", "Satır ekle"),
    "shp.action.remove": ("Remove", "حذف", "Kaldır"),
    "shp.action.print": ("Print", "طباعة", "Yazdır"),
    "shp.action.back": ("Back", "رجوع", "Geri"),
    "shp.action.cancel": ("Cancel", "إلغاء", "İptal"),
    # Wording is copied verbatim from app/static/i18n/{en,ar,tr}.json — that is what
    # app.js actually renders, so the two must not say different things.
    "shp.action.export": ("Export CSV", "تصدير CSV", "CSV dışa aktar"),
    "shp.action.export_packing": ("Packing list CSV", "قائمة التعبئة CSV",
                                  "Paket listesi CSV"),
    "shp.action.export_invoice": ("Invoice lines CSV", "بنود الفاتورة CSV",
                                  "Fatura satırları CSV"),

    "shp.dash.sub": ("What actually left the factory — cartons, packing lists, commercial "
                     "invoices and ordered-vs-shipped.",
                     "ما غادر المصنع فعليًا — الكراتين وقوائم التعبئة والفواتير التجارية "
                     "والمطلوب مقابل المشحون.",
                     "Fabrikadan gerçekten çıkan mal — koliler, çeki listeleri, ticari "
                     "faturalar ve sipariş-sevkiyat karşılaştırması."),
    "shp.dash.etd": ("Leaving soon (ETD)", "تغادر قريبًا (تاريخ المغادرة)",
                     "Yakında çıkacak (ETD)"),
    "shp.dash.recon": ("Shipped vs ordered — flagged", "المشحون مقابل المطلوب — المُعلَّم",
                       "Sevk edilen ile sipariş — işaretliler"),
    "shp.dash.recent": ("Recent shipments", "أحدث الشحنات", "Son sevkiyatlar"),

    "shp.kpi.planned": ("Planned / packed", "مخططة / معبأة", "Planlanan / paketlenen"),
    "shp.kpi.dispatched": ("Dispatched", "مُرسَلة", "Sevk edildi"),
    "shp.kpi.delivered": ("Delivered", "مُسلَّمة", "Teslim edildi"),
    "shp.kpi.cartons": ("Cartons", "الكراتين", "Koli"),
    "shp.kpi.cbm": ("Total CBM", "إجمالي الحجم (م³)", "Toplam hacim (m³)"),
    "shp.kpi.short": ("Orders short shipped", "أوامر مشحونة بالنقص",
                      "Eksik sevk edilen siparişler"),
    "shp.kpi.over": ("Orders over shipped", "أوامر مشحونة بالزيادة",
                     "Fazla sevk edilen siparişler"),
    "shp.kpi.pieces": ("Pieces packed", "القطع المعبأة", "Paketlenen adet"),
    "shp.kpi.etd": ("Leaving soon", "تغادر قريبًا", "Yakında çıkacak"),
    "shp.kpi.recent": ("Recent shipments", "أحدث الشحنات", "Son sevkiyatlar"),
    "shp.kpi.net": ("Net weight (kg)", "الوزن الصافي (كجم)", "Net ağırlık (kg)"),
    "shp.kpi.gross": ("Gross weight (kg)", "الوزن القائم (كجم)", "Brüt ağırlık (kg)"),

    "shp.list.h1": ("Shipments", "الشحنات", "Sevkiyatlar"),
    "shp.list.sub": ("Every export shipment with its cartons, volume and documents.",
                     "كل شحنة تصدير مع كراتينها وحجمها ومستنداتها.",
                     "Her ihracat sevkiyatı; kolileri, hacmi ve belgeleriyle."),
    "shp.list.count": ("shipments", "شحنة", "sevkiyat"),
    "shp.filter.all_status": ("All statuses", "كل الحالات", "Tüm durumlar"),
    "shp.filter.all_modes": ("All modes", "كل وسائل الشحن", "Tüm taşıma şekilleri"),
    "shp.filter.clear": ("Clear", "مسح", "Temizle"),

    "shp.form.sub": ("Pick the order — buyer, currency and the invoice unit price are copied "
                     "from it and frozen on this shipment.",
                     "اختر الأمر — يُنسخ منه المشتري والعملة وسعر وحدة الفاتورة ويُثبَّت على هذه الشحنة.",
                     "Siparişi seçin — alıcı, para birimi ve fatura birim fiyatı ondan "
                     "kopyalanır ve bu sevkiyatta sabitlenir."),
    "shp.form.no_order": ("No order (stand-alone shipment)", "بدون أمر (شحنة مستقلة)",
                          "Sipariş yok (bağımsız sevkiyat)"),

    "shp.detail.shipment": ("Shipment", "الشحنة", "Sevkiyat"),
    "shp.detail.against": ("Against the order", "مقابل الأمر", "Siparişe karşı"),
    "shp.detail.this_ship": ("This shipment", "هذه الشحنة", "Bu sevkiyat"),
    "shp.detail.fg": ("Finished goods on hand", "المخزون الجاهز المتاح", "Mevcut mamul stoğu"),
    "shp.detail.fg_gap": ("Packed above FG stock", "المعبأ أكثر من المخزون الجاهز",
                          "Paketlenen, mamul stoğunun üstünde"),
    "shp.detail.cartons": ("Carton lines", "سطور الكراتين", "Koli satırları"),
    "shp.detail.add_carton": ("Add carton line", "إضافة سطر كراتين", "Koli satırı ekle"),
    "shp.detail.carton_note": ("Weights and dimensions are per carton. CBM = L x W x H in "
                               "metres, times the number of cartons.",
                               "الأوزان والأبعاد لكل كرتونة. الحجم = الطول × العرض × الارتفاع "
                               "بالمتر × عدد الكراتين.",
                               "Ağırlıklar ve ölçüler koli başınadır. Hacim = En x Boy x "
                               "Yükseklik (metre) x koli adedi."),
    "shp.detail.lock_note": ("Once the status is dispatched or delivered the packing list is "
                             "final and can no longer be edited.",
                             "بمجرد أن تصبح الحالة مُرسَلة أو مُسلَّمة تصبح قائمة التعبئة نهائية "
                             "ولا يمكن تعديلها.",
                             "Durum sevk edildi veya teslim edildi olduğunda çeki listesi "
                             "kesinleşir ve artık düzenlenemez."),
    "shp.detail.locked": ("This shipment has left the factory — its packing list is a customs "
                          "document and is now final.",
                          "غادرت هذه الشحنة المصنع — قائمة تعبئتها مستند جمركي وأصبحت نهائية.",
                          "Bu sevkiyat fabrikadan çıktı — çeki listesi bir gümrük belgesidir "
                          "ve artık kesindir."),
    "shp.detail.no_rights": ("You do not have permission to edit this packing list.",
                             "لا تملك صلاحية تعديل قائمة التعبئة هذه.",
                             "Bu çeki listesini düzenleme yetkiniz yok."),

    "shp.doc.packing": ("Packing list", "قائمة التعبئة", "Çeki listesi"),
    "shp.doc.invoice": ("Commercial invoice", "الفاتورة التجارية", "Ticari fatura"),
    "shp.doc.packing_totals": ("Packing totals", "إجماليات التعبئة", "Paketleme toplamları"),
    "shp.doc.note": ("Weights and dimensions are per carton; totals are per line.",
                     "الأوزان والأبعاد لكل كرتونة؛ والإجماليات لكل سطر.",
                     "Ağırlıklar ve ölçüler koli başınadır; toplamlar satır bazındadır."),
    "shp.doc.signed": ("Packed & verified by ______________________",
                       "تمت التعبئة والمراجعة بواسطة ______________________",
                       "Paketleyen ve kontrol eden ______________________"),
    "shp.doc.declare": ("We certify that the above information is true and correct.",
                        "نقر بأن المعلومات الواردة أعلاه صحيحة ودقيقة.",
                        "Yukarıdaki bilgilerin doğru ve eksiksiz olduğunu beyan ederiz."),

    "shp.field.shipment": ("Shipment", "الشحنة", "Sevkiyat"),
    "shp.field.order": ("Order", "الأمر", "Sipariş"),
    "shp.field.buyer": ("Buyer", "المشتري", "Alıcı"),
    "shp.field.destination": ("Destination", "الوجهة", "Varış"),
    "shp.field.dest_full": ("Destination / port of discharge *", "الوجهة / ميناء التفريغ *",
                            "Varış / boşaltma limanı *"),
    "shp.field.pol": ("Port of loading", "ميناء الشحن", "Yükleme limanı"),
    "shp.field.incoterm": ("Incoterm", "شرط التسليم (إنكوترم)", "Teslim şekli (Incoterm)"),
    "shp.field.mode": ("Mode", "وسيلة الشحن", "Taşıma şekli"),
    "shp.field.carrier": ("Carrier / forwarder", "الناقل / وكيل الشحن", "Taşıyıcı / nakliyeci"),
    "shp.field.container": ("Container / AWB no", "رقم الحاوية / بوليصة الشحن الجوي",
                            "Konteyner / AWB no"),
    "shp.field.etd": ("ETD", "تاريخ المغادرة المتوقع", "Tahmini çıkış (ETD)"),
    "shp.field.eta": ("ETA", "تاريخ الوصول المتوقع", "Tahmini varış (ETA)"),
    "shp.field.status": ("Status", "الحالة", "Durum"),
    "shp.field.invoice_no": ("Invoice no", "رقم الفاتورة", "Fatura no"),
    "shp.field.invoice_date": ("Invoice date", "تاريخ الفاتورة", "Fatura tarihi"),
    "shp.field.currency": ("Currency", "العملة", "Para birimi"),
    "shp.field.unit_price": ("Unit price", "سعر الوحدة", "Birim fiyat"),
    "shp.field.unit_price_hint": ("Unit price (blank = from order)",
                                  "سعر الوحدة (اتركه فارغًا = من الأمر)",
                                  "Birim fiyat (boş = siparişten)"),
    "shp.field.lc": ("LC / payment reference", "مرجع الاعتماد المستندي / السداد",
                     "Akreditif / ödeme referansı"),
    "shp.field.notes": ("Notes", "ملاحظات", "Notlar"),
    "shp.field.carton_no": ("Carton no", "رقم الكرتونة", "Koli no"),
    "shp.field.style": ("Style", "الموديل", "Model"),
    "shp.field.colour": ("Colour", "اللون", "Renk"),
    "shp.field.size": ("Size", "المقاس", "Beden"),
    "shp.field.qty_per": ("Pcs / carton", "قطع لكل كرتونة", "Koli başına adet"),
    "shp.field.qty_per_req": ("Pieces per carton *", "عدد القطع لكل كرتونة *",
                              "Koli başına adet *"),
    "shp.field.cartons": ("Cartons", "الكراتين", "Koli"),
    "shp.field.cartons_req": ("Cartons *", "الكراتين *", "Koli *"),
    "shp.field.pieces": ("Pieces", "القطع", "Adet"),
    "shp.field.net": ("Net (kg)", "الصافي (كجم)", "Net ağırlık (kg)"),
    "shp.field.gross": ("Gross (kg)", "القائم (كجم)", "Brüt (kg)"),
    "shp.field.net_per": ("Net weight / carton (kg)", "الوزن الصافي لكل كرتونة (كجم)",
                          "Koli başına net ağırlık (kg)"),
    "shp.field.gross_per": ("Gross weight / carton (kg)", "الوزن القائم لكل كرتونة (كجم)",
                            "Koli başına brüt ağırlık (kg)"),
    "shp.field.len": ("Length (cm)", "الطول (سم)", "Uzunluk (cm)"),
    "shp.field.wid": ("Width (cm)", "العرض (سم)", "Genişlik (cm)"),
    "shp.field.hei": ("Height (cm)", "الارتفاع (سم)", "Yükseklik (cm)"),
    "shp.field.dims": ("L x W x H (cm)", "الطول × العرض × الارتفاع (سم)",
                       "En x Boy x Yükseklik (cm)"),
    "shp.field.cbm": ("CBM", "الحجم (م³)", "Hacim (m³)"),
    "shp.field.amount": ("Amount", "القيمة", "Tutar"),
    "shp.field.total": ("Total", "الإجمالي", "Toplam"),
    "shp.field.docs": ("Documents", "المستندات", "Belgeler"),
    "shp.field.ordered": ("Ordered", "المطلوب", "Sipariş edilen"),
    "shp.field.packed": ("Packed", "المعبأ", "Paketlenen"),
    "shp.field.shipped": ("Shipped", "المشحون", "Sevk edilen"),
    "shp.field.balance": ("Balance", "المتبقي", "Kalan"),
    "shp.field.fulfil": ("Fulfilment", "نسبة التنفيذ", "Gerçekleşme"),
    "shp.field.flag": ("Flag", "التنبيه", "İşaret"),
    "shp.field.ship_date": ("Ex-factory", "تاريخ الخروج من المصنع", "Fabrika çıkış tarihi"),

    "shp.flag.short": ("Short", "نقص", "Eksik"),
    "shp.flag.over": ("Over", "زيادة", "Fazla"),
    "shp.flag.ok": ("In tolerance", "ضمن السماح", "Tolerans içinde"),

    "shp.recon.h1": ("Shipped vs ordered", "المشحون مقابل المطلوب",
                     "Sevk edilen ile sipariş edilen"),
    "shp.recon.sub": ("Ordered against packed and shipped. Short and over shipments are both "
                      "buyer chargebacks — they are flagged here and on the bell.",
                      "المطلوب مقابل المعبأ والمشحون. النقص والزيادة كلاهما يعرّض المصنع لخصم "
                      "من المشتري — ويُعلَّمان هنا وفي جرس التنبيهات.",
                      "Sipariş edilen; paketlenen ve sevk edilenle karşılaştırılır. Eksik ve "
                      "fazla sevkiyatın ikisi de alıcı cezasıdır — burada ve bildirim zilinde "
                      "işaretlenir."),
    "shp.recon.title": ("Fulfilment by order", "نسبة التنفيذ لكل أمر",
                        "Sipariş bazında gerçekleşme"),
    "shp.recon.tolerance": ("tolerance", "نسبة السماح", "tolerans"),

    "shp.empty.shipments": ("No shipments yet.", "لا توجد شحنات بعد.", "Henüz sevkiyat yok."),
    "shp.empty.cartons": ("No carton lines yet — add the first one.",
                          "لا توجد سطور كراتين بعد — أضف السطر الأول.",
                          "Henüz koli satırı yok — ilkini ekleyin."),
    "shp.empty.etd": ("Nothing leaving in the next two weeks.",
                      "لا شيء يغادر خلال الأسبوعين القادمين.",
                      "Önümüzdeki iki hafta içinde çıkacak sevkiyat yok."),
    "shp.empty.recon": ("Every order is shipping within tolerance.",
                        "كل الأوامر تُشحن ضمن نسبة السماح.",
                        "Tüm siparişler tolerans içinde sevk ediliyor."),
    "shp.empty.recon_rows": ("No order-linked shipments yet.",
                             "لا توجد شحنات مرتبطة بأوامر بعد.",
                             "Siparişe bağlı sevkiyat henüz yok."),
    "shp.empty.invoice": ("Nothing packed yet — the invoice has no value lines.",
                          "لم تتم تعبئة أي شيء بعد — لا توجد سطور قيمة في الفاتورة.",
                          "Henüz paketlenen yok — faturada değer satırı bulunmuyor."),

    # Placeholders that are codes, not prose — identical in all three languages on purpose.
    "shp.ph.carton_no": ("1-50", "1-50", "1-50"),
    "shp.ph.currency": ("USD", "USD", "USD"),
}
