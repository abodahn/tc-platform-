# -*- coding: utf-8 -*-
"""
Arabic and Turkish for the messages the procurement module flashes at people.

WHY THIS EXISTS. The module's SCREENS were translated — some four thousand keys
in app/static/i18n — while its MESSAGES were not. Six submit refusals went
through services.labels(); the other ninety-odd were English sentences written
inline in the routes. So an Arabic user read an Arabic screen and an English
sentence the moment anything went wrong, which is exactly the moment they most
need to read it.

WHY IT IS KEYED BY THE ENGLISH TEXT. The alternative was to give every message a
key and rewrite ninety-three call sites — a very wide diff through the route of
every action in the module, to change no behaviour at all. Keying on the English
sentence leaves the routes untouched: the text is translated on its way out, at
the one place every message passes through.

The cost of that choice is that editing an English sentence silently orphans its
translation. tests_messages_i18n.py closes it: it extracts every user-facing
string from the routes and fails when one has no entry here, so an untranslated
message cannot ship unnoticed — including a new one somebody adds next month.

Translations are of the SENTENCE, not word by word: same meaning, same tone, and
the same concrete instruction. A refusal that does not tell somebody what to do
next is not worth translating carefully.
"""

# English source string -> {"ar": ..., "tr": ...}
#
# Keyed by the ENGLISH SENTENCE, so the routes did not have to change: see the
# module docstring for why, and tests_messages_i18n.py for the check that keeps
# that choice honest.
MESSAGES = {
    "Write the request in English only. Arabic text is not accepted in: ": {"ar": "اكتب الطلب بالإنجليزي بس. الكتابة بالعربي مش مقبولة في: ", "tr": "Talebi yalnızca İngilizce yazın. Arapça metin kabul edilmiyor: "},
    "That purchase order is cancelled, so it cannot be revised.": {"ar": "أمر الشراء ده ملغي، فمش ممكن يتعدّل.", "tr": "Bu satın alma siparişi iptal edildi, revize edilemez."},
    # --- Imports + framework agreements (app/routes/approvals_imports.py) ---
    'Not found or no longer available.': {'ar': 'مش موجود أو مبقاش متاح.', 'tr': 'Bulunamadı veya artık mevcut değil.'},
    'That could not be done. Reload the page and try again.': {'ar': 'مقدرناش ننفذ ده. حدّث الصفحة وجرّب تاني.', 'tr': 'Bu işlem yapılamadı. Sayfayı yenileyip tekrar deneyin.'},
    'You are not allowed to do this.': {'ar': 'مش مسموح لك تعمل ده.', 'tr': 'Bunu yapma yetkiniz yok.'},
    'This request is not an import order. Shipments and landed costs are only for import orders.': {'ar': 'الطلب ده مش أمر استيراد. الشحنات والتكلفة الكاملة لأوامر الاستيراد بس.', 'tr': 'Bu talep bir ithalat siparişi değil. Sevkiyat ve varış maliyeti yalnızca ithalat siparişleri içindir.'},
    'Shipments and landed costs can only be recorded once the purchase order is issued.': {'ar': 'الشحنات والتكلفة الكاملة بتتسجل بس بعد ما أمر الشراء يطلع.', 'tr': 'Sevkiyat ve varış maliyeti ancak satın alma siparişi verildikten sonra kaydedilebilir.'},
    "Choose one of this request's purchase orders.": {'ar': 'اختار أمر شراء من أوامر الطلب ده.', 'tr': 'Bu talebin satın alma siparişlerinden birini seçin.'},
    'Choose an incoterm from the list.': {'ar': 'اختار شرط التسليم (Incoterm) من القائمة.', 'tr': 'Listeden bir teslim şekli (Incoterm) seçin.'},
    'Choose how to allocate the charges: by value or by quantity.': {'ar': 'اختار طريقة توزيع المصروفات: حسب القيمة أو حسب الكمية.', 'tr': 'Masrafların nasıl dağıtılacağını seçin: değere veya miktara göre.'},
    'A date is not valid. Use the date picker.': {'ar': 'فيه تاريخ مش صحيح. استخدم أداة اختيار التاريخ.', 'tr': 'Bir tarih geçersiz. Tarih seçiciyi kullanın.'},
    'The milestone dates are out of order: shipped, then at port, then cleared, then delivered.': {'ar': 'تواريخ المراحل مش مترتبة: الشحن، بعدين الوصول للميناء، بعدين الإفراج، بعدين التسليم.', 'tr': 'Aşama tarihleri sırasız: önce yükleme, sonra liman, sonra gümrük, sonra teslim.'},
    'Choose the type of charge.': {'ar': 'اختار نوع المصروف.', 'tr': 'Masraf türünü seçin.'},
    'Enter the amount as a positive number.': {'ar': 'اكتب المبلغ كرقم موجب.', 'tr': 'Tutarı pozitif bir sayı olarak girin.'},
    'Choose a currency from the list.': {'ar': 'اختار العملة من القائمة.', 'tr': 'Listeden bir para birimi seçin.'},
    'Enter the exchange rate to EGP for a charge in a foreign currency.': {'ar': 'اكتب سعر التحويل للجنيه لأي مصروف بعملة أجنبية.', 'tr': 'Yabancı para birimindeki masraf için EGP kurunu girin.'},
    'This charge was already voided.': {'ar': 'المصروف ده اتلغى قبل كده.', 'tr': 'Bu masraf zaten iptal edildi.'},
    'Write why the charge is being voided.': {'ar': 'اكتب سبب إلغاء المصروف.', 'tr': 'Masrafın neden iptal edildiğini yazın.'},
    'The document must be a PDF, image, Excel or CSV file of 3 MB or less.': {'ar': 'المستند لازم يكون PDF أو صورة أو إكسيل أو CSV، وحجمه 3 ميجا أو أقل.', 'tr': 'Belge en fazla 3 MB boyutunda PDF, resim, Excel veya CSV dosyası olmalıdır.'},
    'Enter valid from and valid to dates. The end cannot be before the start or already past.': {'ar': 'اكتب تاريخ بداية ونهاية صحيحين. النهاية مينفعش تكون قبل البداية أو تكون عدّت خلاص.', 'tr': 'Geçerli başlangıç ve bitiş tarihleri girin. Bitiş, başlangıçtan önce veya geçmişte olamaz.'},
    'Enter the ceiling as a positive number.': {'ar': 'اكتب السقف كرقم موجب.', 'tr': 'Tavanı pozitif bir sayı olarak girin.'},
    'Give the agreement a ceiling value, a ceiling quantity, or both.': {'ar': 'حدد للاتفاقية سقف قيمة أو سقف كمية أو الاتنين.', 'tr': 'Anlaşmaya bir tavan değer, bir tavan miktar veya ikisini birden verin.'},
    'Choose an active supplier from the approved vendor list.': {'ar': 'اختار مورد نشط من قائمة الموردين المعتمدين.', 'tr': 'Onaylı tedarikçi listesinden aktif bir tedarikçi seçin.'},
    'Add at least one agreement line with its agreed unit price.': {'ar': 'ضيف بند واحد على الأقل للاتفاقية بسعر الوحدة المتفق عليه.', 'tr': 'Anlaşılan birim fiyatıyla en az bir anlaşma kalemi ekleyin.'},
    'Each agreement line needs an item and an agreed unit price above zero.': {'ar': 'كل بند في الاتفاقية محتاج صنف وسعر وحدة متفق عليه أكبر من صفر.', 'tr': 'Her anlaşma kalemi bir kalem ve sıfırdan büyük bir anlaşılan birim fiyat gerektirir.'},
    'An item code is not in the catalogue. Check the code or type the item as free text.': {'ar': 'فيه كود صنف مش موجود في الكتالوج. راجع الكود أو اكتب الصنف كتابة حرة.', 'tr': 'Bir kalem kodu katalogda yok. Kodu kontrol edin veya kalemi serbest metin olarak yazın.'},
    'Only a draft agreement can be approved.': {'ar': 'مينفعش يتعتمد غير الاتفاقية اللي لسه مسودة.', 'tr': 'Yalnızca taslak bir anlaşma onaylanabilir.'},
    'You created this agreement, so someone else must approve it.': {'ar': 'إنت اللي عملت الاتفاقية دي، فلازم حد تاني هو اللي يعتمدها.', 'tr': 'Bu anlaşmayı siz oluşturdunuz; başka biri onaylamalıdır.'},
    'This agreement is already closed.': {'ar': 'الاتفاقية دي مقفولة خلاص.', 'tr': 'Bu anlaşma zaten kapalı.'},
    'This request can no longer be priced from an agreement.': {'ar': 'الطلب ده مبقاش ينفع يتسعّر من اتفاقية.', 'tr': 'Bu talep artık bir anlaşmadan fiyatlandırılamaz.'},
    'You raised this request, so you cannot price it.': {'ar': 'إنت اللي قدمت الطلب ده، فمينفعش تسعّره.', 'tr': 'Bu talebi siz açtınız; fiyatlandıramazsınız.'},
    'The agreement has expired. Price this line through the normal quotation process.': {'ar': 'الاتفاقية انتهت. سعّر البند ده بإجراءات عروض الأسعار العادية.', 'tr': 'Anlaşmanın süresi doldu. Bu kalemi normal teklif süreciyle fiyatlandırın.'},
    'The agreement is not active. Price this line through the normal quotation process.': {'ar': 'الاتفاقية مش سارية. سعّر البند ده بإجراءات عروض الأسعار العادية.', 'tr': 'Anlaşma geçerli değil. Bu kalemi normal teklif süreciyle fiyatlandırın.'},
    'That agreement line is for a different item.': {'ar': 'بند الاتفاقية ده لصنف تاني.', 'tr': 'Bu anlaşma kalemi başka bir kalem için.'},
    'The agreement is in a different currency from this request.': {'ar': 'الاتفاقية بعملة مختلفة عن عملة الطلب ده.', 'tr': 'Anlaşma bu talepten farklı bir para biriminde.'},
    'This line would take the agreement over its ceiling. Price it through the normal quotation process.': {'ar': 'البند ده هيعدّي سقف الاتفاقية. سعّره بإجراءات عروض الأسعار العادية.', 'tr': 'Bu kalem anlaşmayı tavanının üzerine çıkarır. Normal teklif süreciyle fiyatlandırın.'},
    "Set this request's exchange rate to EGP before pricing it.": {'ar': 'حدد سعر تحويل الطلب ده للجنيه قبل ما تسعّره.', 'tr': 'Fiyatlandırmadan önce bu talebin EGP kurunu girin.'},
    'Shipment saved.': {'ar': 'تم حفظ الشحنة.', 'tr': 'Sevkiyat kaydedildi.'},
    'Charge recorded.': {'ar': 'تم تسجيل المصروف.', 'tr': 'Masraf kaydedildi.'},
    'Charge corrected.': {'ar': 'تم تصحيح المصروف.', 'tr': 'Masraf düzeltildi.'},
    'Charge voided.': {'ar': 'تم إلغاء المصروف.', 'tr': 'Masraf iptal edildi.'},
    'Line priced from the agreement.': {'ar': 'البند اتسعّر من الاتفاقية.', 'tr': 'Kalem anlaşmadan fiyatlandırıldı.'},
    'Line released from the agreement. The quotation rule applies to it again.': {'ar': 'البند اتفك من الاتفاقية، وقاعدة عروض الأسعار رجعت تنطبق عليه.', 'tr': 'Kalemin anlaşma bağı kaldırıldı. Teklif kuralı yeniden uygulanır.'},
    'Agreement saved as a draft. A Procurement admin or Finance must approve it.': {'ar': 'الاتفاقية اتحفظت كمسودة. لازم يعتمدها مسؤول المشتريات أو الحسابات.', 'tr': 'Anlaşma taslak olarak kaydedildi. Bir Satın Alma yöneticisi veya Finans onaylamalıdır.'},
    'Agreement approved and active.': {'ar': 'الاتفاقية اتعتمدت وبقت سارية.', 'tr': 'Anlaşma onaylandı ve geçerli.'},
    'Agreement closed.': {'ar': 'الاتفاقية اتقفلت.', 'tr': 'Anlaşma kapatıldı.'},
    'Enter the budget as a positive number.': {'ar': 'اكتب الميزانية كرقم موجب.', 'tr': 'Bütçeyi pozitif bir sayı olarak girin.'},
    # --- Procurement analytics: saved reports ---
    'Report saved.': {'ar': 'التقرير اتحفظ.', 'tr': 'Rapor kaydedildi.'},
    'Report deleted.': {'ar': 'التقرير اتمسح.', 'tr': 'Rapor silindi.'},
    'Report not found.': {'ar': 'التقرير مش موجود.', 'tr': 'Rapor bulunamadı.'},
    'Report unpinned.': {'ar': 'التقرير اتشال من اللوحة.', 'tr': 'Raporun sabitlemesi kaldırıldı.'},
    'Report pinned to the analytics dashboard.': {'ar': 'التقرير اتثبّت في لوحة التحليلات.', 'tr': 'Rapor analitik panosuna sabitlendi.'},
    'Give the report a name.': {'ar': 'اكتب اسم للتقرير.', 'tr': 'Rapora bir ad verin.'},
    'This report shows money, which your role does not see.': {'ar': 'التقرير ده فيه أرقام مالية، ودورك مش بيشوفها.', 'tr': 'Bu rapor parasal tutarlar gösterir; rolünüz bunları görmez.'},
    'Only Purchasing, Finance and procurement admins can share reports.': {'ar': 'المشاركة متاحة للمشتريات والمالية ومديري المشتريات بس.', 'tr': 'Raporları yalnızca Satın Alma, Finans ve satın alma yöneticileri paylaşabilir.'},
    'Only the person who created this report can change it.': {'ar': 'اللي عمل التقرير بس هو اللي يقدر يعدّله.', 'tr': 'Bu raporu yalnızca oluşturan kişi değiştirebilir.'},
    'Choose what to group the report by.': {'ar': 'اختار هتجمّع التقرير بإيه.', 'tr': 'Raporun neye göre gruplanacağını seçin.'},
    'Choose what the report should measure.': {'ar': 'اختار التقرير هيقيس إيه.', 'tr': 'Raporun neyi ölçeceğini seçin.'},
    'This measure is counted per request, so it cannot be split by item or category.': {'ar': 'المقياس ده بيتحسب لكل طلب، فمش ينفع يتقسم حسب الصنف أو الفئة.', 'tr': 'Bu ölçü talep başına hesaplanır; kaleme veya kategoriye göre bölünemez.'},
    # --- Quick fixes 2026-11-26 ---
    'This request is closed or cancelled, so files can no longer be attached.': {'ar': 'الطلب مقفول أو ملغي، فمش ممكن نرفق ملفات تاني.', 'tr': 'Bu talep kapalı veya iptal edildi; artık dosya eklenemez.'},
    'Choose how the supplier settled the debit note.': {'ar': 'اختر طريقة تسوية المورد لإشعار الخصم.', 'tr': 'Tedarikçinin borç dekontunu nasıl kapattığını seçin.'},
    "Enter the supplier's document number.": {'ar': 'اكتب رقم مستند المورد.', 'tr': 'Tedarikçinin belge numarasını girin.'},
    'Enter the credited amount.': {'ar': 'اكتب المبلغ المُسوّى.', 'tr': 'Alacaklandırılan tutarı girin.'},
    # --- Change requester (admin) ---
    'Requester changed.': {'ar': 'تم تغيير مقدّم الطلب.', 'tr': 'Talep eden değiştirildi.'},
    'Write why the requester is being changed.': {'ar': 'اكتب سبب تغيير مقدّم الطلب.', 'tr': 'Talep edenin neden değiştirildiğini yazın.'},
    'No such user. Pick the person from the list.': {'ar': 'لا يوجد مستخدم بهذا الاسم. اختر الشخص من القائمة.', 'tr': 'Böyle bir kullanıcı yok. Kişiyi listeden seçin.'},
    'That user is not active.': {'ar': 'هذا المستخدم غير مفعّل.', 'tr': 'Bu kullanıcı aktif değil.'},
    'That user is already the requester.': {'ar': 'هذا المستخدم هو مقدّم الطلب بالفعل.', 'tr': 'Bu kullanıcı zaten talep eden.'},
    'That user already signed this request, so they cannot become its requester.': {'ar': 'هذا المستخدم وقّع على الطلب بالفعل، فلا يمكن أن يصبح مقدّمه.', 'tr': 'Bu kullanıcı talebi zaten imzaladı, bu yüzden talep eden olamaz.'},
    'Stock posting retry completed.': {'ar': 'تمت إعادة محاولة ترحيل المخزون.', 'tr': 'Stok aktarımı yeniden denendi.'},
    'Some stock postings still need attention. Check the store before retrying.': {'ar': 'في ترحيلات مخزون لسه محتاجة مراجعة. راجع المخزن قبل ما تحاول تاني.', 'tr': 'Bazı stok aktarımlarının incelenmesi gerekiyor. Yeniden denemeden önce depoyu kontrol edin.'},

    # --- Hold, Resume and Send back (spec: hold & reject options) -----------
    "Put on hold. It stays in the queue and nobody can sign it until it is resumed — the requester has been told why.": {
        "ar": "تم التعليق. يبقى الطلب في قائمة الانتظار ولا يستطيع أحد التوقيع عليه حتى يُستأنف — وقد أُبلغ مقدّم الطلب بالسبب.",
        "tr": "Beklemeye alındı. Kuyrukta kalır ve devam ettirilene kadar kimse imzalayamaz — talep edene nedeni bildirildi.",
    },
    "Say why it is on hold. A request parked with no reason cannot be told apart from one forgotten.": {
        "ar": "اكتب سبب التعليق. الطلب الموقوف بلا سبب لا يمكن تمييزه عن طلب منسيّ.",
        "tr": "Neden beklemede olduğunu yazın. Gerekçesiz bekletilen bir talep, unutulmuş bir talepten ayırt edilemez.",
    },
    "This request is already on hold.": {
        "ar": "هذا الطلب معلّق بالفعل.",
        "tr": "Bu talep zaten beklemede.",
    },
    "This request is not on hold.": {
        "ar": "هذا الطلب ليس معلّقًا.",
        "tr": "Bu talep beklemede değil.",
    },
    "Only somebody who could sign this rung can hold it.": {
        "ar": "لا يستطيع تعليق هذه المرحلة إلا من يملك التوقيع عليها.",
        "tr": "Bu basamağı yalnızca onu imzalayabilecek biri bekletebilir.",
    },
    "Only somebody who could sign this rung can resume it.": {
        "ar": "لا يستطيع استئناف هذه المرحلة إلا من يملك التوقيع عليها.",
        "tr": "Bu basamağı yalnızca onu imzalayabilecek biri devam ettirebilir.",
    },
    "Only a circulating request can be held.": {
        "ar": "لا يمكن تعليق إلا طلب قيد التداول.",
        "tr": "Yalnızca dolaşımdaki bir talep bekletilebilir.",
    },
    "Hold lifted — the approvals are moving again.": {
        "ar": "رُفع التعليق — واستأنفت الموافقات حركتها.",
        "tr": "Bekleme kaldırıldı — onaylar yeniden ilerliyor.",
    },
    "Could not hold it (%s).": {
        "ar": "تعذّر التعليق (%s).",
        "tr": "Bekletilemedi (%s).",
    },
    "Could not resume it (%s).": {
        "ar": "تعذّر الاستئناف (%s).",
        "tr": "Devam ettirilemedi (%s).",
    },
    "Sent back to %s. Every signature from there up has been withdrawn and the people who gave them have been told.": {
        "ar": "أُرجع الطلب إلى %s. وسُحبت كل التوقيعات من تلك المرحلة فما فوق، وأُبلغ أصحابها.",
        "tr": "%s adımına geri gönderildi. O adımdan yukarıdaki tüm imzalar geri alındı ve imzalayanlara bildirildi.",
    },
    "Say why it is going back. The rung receiving it needs to know what to look at.": {
        "ar": "اكتب سبب الإرجاع. المرحلة التي سيعود إليها تحتاج أن تعرف ما الذي تراجعه.",
        "tr": "Neden geri gönderildiğini yazın. Talebi alacak basamağın neye bakacağını bilmesi gerekir.",
    },
    "A request can only go BACKWARDS. Sending it forward would skip the rungs in between.": {
        "ar": "لا يمكن للطلب أن يتحرّك إلا إلى الخلف. فإرساله إلى الأمام يعني تخطّي المراحل التي بينهما.",
        "tr": "Bir talep yalnızca GERİYE gidebilir. İleri göndermek, aradaki basamakları atlamak olurdu.",
    },
    "That rung is not on this request's ladder.": {
        "ar": "هذه المرحلة ليست ضمن سلم موافقات هذا الطلب.",
        "tr": "Bu basamak, bu talebin onay merdiveninde yok.",
    },
    "Only somebody who could sign the live rung can send it back.": {
        "ar": "لا يستطيع إرجاع الطلب إلا من يملك التوقيع على المرحلة الحالية.",
        "tr": "Talebi yalnızca mevcut basamağı imzalayabilecek biri geri gönderebilir.",
    },
    "Choose a rung to send it back to.": {
        "ar": "اختر المرحلة التي سيُرجَع إليها الطلب.",
        "tr": "Geri gönderilecek basamağı seçin.",
    },
    "Could not send it back (%s).": {
        "ar": "تعذّر الإرجاع (%s).",
        "tr": "Geri gönderilemedi (%s).",
    },
    # --- order dispatched (spec: PO status) --------------------------------
    "Marked as on the way — the requester has been told.": {
        "ar": "تم تحديدها كـ«في الطريق» — وأُبلغ مقدّم الطلب.",
        "tr": "Yolda olarak işaretlendi — talep eden bilgilendirildi.",
    },
    "Only an order that is still on order can be marked as dispatched.": {
        "ar": "لا يمكن تحديد الشحن إلا لأمر شراء ما زال في حالة «تم إصدار الأمر».",
        "tr": "Yalnızca hâlâ siparişte olan bir emir gönderildi olarak işaretlenebilir.",
    },
    "Could not update (%s).": {
        "ar": "تعذّر التحديث (%s).",
        "tr": "Güncellenemedi (%s).",
    },
    # --- segregation of duties on the pricing gate -------------------------
    "You raised this request, so you cannot price it. The price decides which approvals it needs, and nobody sets their own threshold. Another buyer — or a procurement admin — enters it.": {
        "ar": "أنت من قدّم هذا الطلب، لذا لا يمكنك تسعيره. السعر هو ما يحدّد الموافقات المطلوبة، ولا يضع أحد حدّ صلاحيته بنفسه. يُدخله مشترٍ آخر أو مسؤول المشتريات.",
        "tr": "Bu talebi siz açtınız, bu yüzden fiyatlandıramazsınız. Fiyat, hangi onayların gerektiğini belirler ve kimse kendi eşiğini kendisi koyamaz. Başka bir satın almacı veya bir satın alma yöneticisi girer.",
    },
    "The warehouse has not checked this request yet. Pricing it now would lock the quantity before the store has confirmed what is on the shelf. It can be priced once it reaches the Purchasing rung.": {
        "ar": "لم يراجع المخزن هذا الطلب بعد. تسعيره الآن يُثبّت الكمية قبل أن يؤكّد المخزن ما هو متاح على الرف. يمكن تسعيره عند وصوله إلى مرحلة المشتريات.",
        "tr": "Depo bu talebi henüz kontrol etmedi. Şimdi fiyatlandırmak, depo rafta ne olduğunu teyit etmeden miktarı kilitler. Talep Satın Alma basamağına ulaştığında fiyatlandırılabilir.",
    },
    # --- RFQ and quotation editing (spec §4, §5) ---------------------------
    "Request for quotation updated.": {
        "ar": "تم تحديث طلب عرض السعر.",
        "tr": "Teklif talebi güncellendi.",
    },
    "Quotation updated.": {
        "ar": "تم تحديث عرض السعر.",
        "tr": "Teklif güncellendi.",
    },
    "Quotation removed.": {
        "ar": "تم حذف عرض السعر.",
        "tr": "Teklif kaldırıldı.",
    },
    "No change.": {
        "ar": "لا يوجد تغيير.",
        "tr": "Değişiklik yok.",
    },
    "Could not update the RFQ (%s).": {
        "ar": "تعذّر تحديث طلب عرض السعر (%s).",
        "tr": "Teklif talebi güncellenemedi (%s).",
    },
    "Could not update the quotation (%s).": {
        "ar": "تعذّر تحديث عرض السعر (%s).",
        "tr": "Teklif güncellenemedi (%s).",
    },
    "Could not remove the quotation (%s).": {
        "ar": "تعذّر حذف عرض السعر (%s).",
        "tr": "Teklif kaldırılamadı (%s).",
    },
    # The refusal texts behind those, which reach the reader through the same
    # flash() but as dictionary values, so the static collector cannot see them.
    "This RFQ has already been answered. Remove the quotation against it first, or issue a new RFQ.": {
        "ar": "تم الرد على طلب عرض السعر هذا. احذف عرض السعر المقدَّم عليه أولًا، أو أصدر طلبًا جديدًا.",
        "tr": "Bu teklif talebi yanıtlanmış. Önce buna gelen teklifi kaldırın veya yeni bir talep açın.",
    },
    "An RFQ has to ask for at least one line.": {
        "ar": "يجب أن يتضمن طلب عرض السعر بندًا واحدًا على الأقل.",
        "tr": "Bir teklif talebi en az bir satır istemelidir.",
    },
    "Request for quotation not found.": {
        "ar": "طلب عرض السعر غير موجود.",
        "tr": "Teklif talebi bulunamadı.",
    },
    "This is the chosen quotation. Choose another one first, then remove this.": {
        "ar": "هذا هو عرض السعر المعتمد. اختر عرضًا آخر أولًا، ثم احذف هذا.",
        "tr": "Bu, seçilmiş tekliftir. Önce başka bir teklif seçin, sonra bunu kaldırın.",
    },
    "Choose the quotation's currency from the list.": {
        "ar": "اختر عملة عرض السعر من القائمة.",
        "tr": "Teklifin para birimini listeden seçin.",
    },
    "Enter the FX rate to EGP for a quotation in a foreign currency.": {
        "ar": "ادخل سعر الصرف مقابل الجنيه لعرض السعر اللي بعملة أجنبية.",
        "tr": "Döviz cinsinden bir teklif için EGP kurunu girin.",
    },
    "You raised this request, so you cannot choose or change its chosen quotation. Another buyer — or a procurement admin — does it.": {
        "ar": "أنت من أنشأ هذا الطلب، لذلك لا يمكنك اختيار عرض السعر أو تعديل العرض المختار. يقوم بذلك مشترٍ آخر — أو مسؤول المشتريات.",
        "tr": "Bu talebi siz oluşturdunuz; bu yüzden teklifini seçemez veya seçilen teklifi değiştiremezsiniz. Bunu başka bir satın almacı — ya da bir satın alma yöneticisi — yapar.",
    },
    "Enter the quoted amount as a positive number.": {
        "ar": "اكتب قيمة عرض السعر كرقم موجب.",
        "tr": "Teklif tutarını pozitif bir sayı olarak girin.",
    },
    "Enter the payment terms the supplier is offering.": {
        "ar": "اكتب شروط الدفع التي يعرضها المورّد.",
        "tr": "Tedarikçinin teklif ettiği ödeme koşullarını girin.",
    },
    "Enter the payment terms you are asking the supplier for.": {
        "ar": "اكتب شروط الدفع التي تطلبها من المورّد.",
        "tr": "Tedarikçiden talep ettiğiniz ödeme koşullarını girin.",
    },
    "Quotation not found.": {
        "ar": "عرض السعر غير موجود.",
        "tr": "Teklif bulunamadı.",
    },
    "Could not reach the quotations table.": {
        "ar": "تعذّر الوصول إلى جدول عروض الأسعار.",
        "tr": "Teklifler tablosuna erişilemedi.",
    },
    "A justification is required to waive competitive quotes.": {
        "ar": "يجب كتابة مبرّر للاستغناء عن عروض الأسعار التنافسية.",
        "tr": "Rekabetçi tekliflerden vazgeçmek için gerekçe yazmanız gerekir.",
    },
    "A rejected quantity needs a reason.": {
        "ar": "اكتب سبب رفض هذه الكمية.",
        "tr": "Reddedilen miktarın gerekçesini yazın.",
    },
    "Above 10,000,000 EGP the DOAM requires a written business case before this request is signed. Record it first.": {
        "ar": "فوق 10,000,000 EGP يطلب الـ DOAM دراسة جدوى مكتوبة قبل توقيع هذا الطلب. سجّلها أولًا.",
        "tr": "10.000.000 EGP üzerinde DOAM, bu talep imzalanmadan önce yazılı bir iş gerekçesi ister. Önce kaydedin.",
    },
    "Added. %d request line(s) now point at it.": {
        "ar": "تمت الإضافة. %d من بنود الطلب مرتبطة به الآن.",
        "tr": "Eklendi. %d talep satırı artık buna bağlı.",
    },
    "Advance authorised.": {
        "ar": "تمت الموافقة على الدفعة المقدمة.",
        "tr": "Avans onaylandı.",
    },
    "An advance above 25% on an order over 500,000 EGP needs a bank guarantee reference.": {
        "ar": "الدفعة المقدمة فوق 25% في أمر شراء أكبر من 500,000 EGP تحتاج رقم خطاب ضمان بنكي.",
        "tr": "500.000 EGP üzerindeki bir siparişte %25'ten fazla avans için banka teminat mektubu referansı gerekir.",
    },
    "An advance of this size needs a higher authority: up to 25% the Financial Director, above that the CFO or the Managing Director.": {
        "ar": "دفعة مقدمة بهذا الحجم تحتاج صلاحية أعلى. حتى 25%: المدير المالي. أكثر من ذلك: الـ CFO أو العضو المنتدب.",
        "tr": "Bu büyüklükteki avans daha üst bir yetki ister. %25'e kadar: Mali Direktör. Üzerinde: CFO veya Murahhas Aza.",
    },
    "An item needs a name.": {
        "ar": "الصنف يحتاج إلى اسم.",
        "tr": "Malzemenin bir adı olmalıdır.",
    },
    "An item needs the code the ERP issued for it.": {
        "ar": "الصنف يحتاج كود الـ ERP الخاص به.",
        "tr": "Malzemenin ERP kodunu girin.",
    },
    "Another item already carries that code.": {
        "ar": "هذا الكود مستخدم بالفعل لصنف آخر.",
        "tr": "Bu kod başka bir malzemede kullanılıyor.",
    },
    "Approved & signed. Waiting on the co-approver at this stage.": {
        "ar": "تمت الموافقة والتوقيع. في انتظار المعتمد الآخر في هذه المرحلة.",
        "tr": "Onaylandı ve imzalandı. Bu aşamadaki ikinci onaycı bekleniyor.",
    },
    "Approved — routed to the next approver.": {
        "ar": "تمت الموافقة. أُرسل الطلب إلى المعتمد التالي.",
        "tr": "Onaylandı. Sıradaki onaycıya gönderildi.",
    },
    "Back in the queue.": {
        "ar": "أُعيد إلى قائمة الانتظار.",
        "tr": "Yeniden sıraya alındı.",
    },
    "Back in use.": {
        "ar": "أُعيد تفعيله.",
        "tr": "Yeniden kullanımda.",
    },
    "Budget saved.": {
        "ar": "تم حفظ الموازنة.",
        "tr": "Bütçe kaydedildi.",
    },
    "Business case recorded.": {
        "ar": "تم تسجيل دراسة الجدوى.",
        "tr": "İş gerekçesi kaydedildi.",
    },
    "Choose a department first.": {
        "ar": "اختر الإدارة أولاً.",
        "tr": "Önce bir departman seçin.",
    },
    "Choose a file to import.": {
        "ar": "اختر ملفاً للاستيراد.",
        "tr": "İçe aktarılacak dosyayı seçin.",
    },
    "Choose an approved engineering justification.": {
        "ar": "اختر مبرّراً هندسياً معتمداً.",
        "tr": "Onaylı bir mühendislik gerekçesi seçin.",
    },
    "Choose at least one vendor to send the RFQ to.": {
        "ar": "اختر مورّداً واحداً على الأقل لإرسال الـ RFQ إليه.",
        "tr": "RFQ gönderilecek en az bir tedarikçi seçin.",
    },
    "Choose or name a department.": {
        "ar": "اختر إدارة أو اكتب اسمها.",
        "tr": "Bir departman seçin veya adını yazın.",
    },
    "Competitive quotes required — this order value needs more quotations than are on file. The Vendor quotes panel shows how many: each must come from a different supplier with its file attached. Or record a single-source justification there.": {
        "ar": "مطلوب عروض أسعار تنافسية — قيمة الطلب دي محتاجة عروض أكتر من الموجودة. لوحة «عروض أسعار المورّدين» بتوضح العدد المطلوب: كل عرض من مورّد مختلف ومرفق بيه ملفه. أو سجّل مبرّر التوريد من مصدر واحد هناك.",
        "tr": "Rekabetçi teklif gerekli — bu sipariş tutarı kayıtlı olandan fazla teklif gerektirir. Kaç tane gerektiği «Tedarikçi teklifleri» panelinde yazar: her biri farklı bir tedarikçiden ve dosyası ekli olmalı. Ya da orada tek kaynak gerekçesini kaydedin.",
    },
    "Could not add delegation (": {
        "ar": "تعذّر إضافة التفويض (",
        "tr": "Yetki devri eklenemedi (",
    },
    # --- PO lane (porev): PO revisions, bulk signing, raise from stock ---
    'This request already has a PO revision waiting for signatures. Finish or withdraw it first.': {'ar': 'الطلب ده عليه تعديل أمر شراء مستني توقيعات. خلّصه أو اسحبه الأول.', 'tr': 'Bu talebin imza bekleyen bir PO revizyonu var. Önce onu tamamlayın veya geri çekin.'},
    "Choose which supplier's purchase order you are revising.": {'ar': 'اختار أمر الشراء بتاع أنهي مورد اللي بتعدّله.', 'tr': 'Hangi tedarikçinin satın alma emrini revize ettiğinizi seçin.'},
    'Enter each quantity as a number greater than zero.': {'ar': 'اكتب كل كمية كرقم أكبر من صفر.', 'tr': 'Her miktarı sıfırdan büyük bir sayı olarak girin.'},
    'Enter each unit price as a number of zero or more.': {'ar': 'اكتب سعر الوحدة كرقم صفر أو أكتر.', 'tr': 'Her birim fiyatı sıfır veya daha büyük bir sayı olarak girin.'},
    'A quantity cannot go below what has already been received.': {'ar': 'الكمية مينفعش تبقى أقل من اللي اتستلم فعلًا.', 'tr': 'Miktar, teslim alınmış olanın altına düşemez.'},
    'Enter the delivery date as a date.': {'ar': 'اكتب تاريخ التوريد كتاريخ صحيح.', 'tr': 'Teslim tarihini geçerli bir tarih olarak girin.'},
    'Nothing was changed — the purchase order stays as it is.': {'ar': 'مفيش أي تغيير — أمر الشراء زي ما هو.', 'tr': 'Hiçbir şey değişmedi — satın alma emri olduğu gibi kalıyor.'},
    'The revised order would be worth less than what has already been invoiced or paid against it.': {'ar': 'قيمة أمر الشراء بعد التعديل هتبقى أقل من اللي اتفوتر أو اتدفع عليه فعلًا.', 'tr': 'Revize sipariş, karşılığında faturalanan veya ödenen tutardan daha az değerde olur.'},
    'You raised this request, so you cannot revise its purchase order.': {'ar': 'إنت اللي عامل الطلب ده، فمينفعش تعدّل أمر الشراء بتاعه.', 'tr': 'Bu talebi siz açtınız; bu yüzden satın alma emrini revize edemezsiniz.'},
    'PO revised — the new revision is live and numbered on the PO.': {'ar': 'أمر الشراء اتعدّل — التعديل الجديد شغال ورقمه مكتوب على أمر الشراء.', 'tr': 'PO revize edildi — yeni revizyon geçerli ve PO üzerinde numaralandı.'},
    'The revision raises the order above its approved value, so it is waiting for approval. The PO stays as it is until it is signed.': {'ar': 'التعديل بيزوّد أمر الشراء عن القيمة المعتمدة، فهو مستني الاعتماد. أمر الشراء هيفضل زي ما هو لحد ما يتوقّع.', 'tr': 'Revizyon siparişi onaylı değerinin üzerine çıkarıyor, bu yüzden onay bekliyor. İmzalanana kadar PO olduğu gibi kalır.'},
    'This revision is no longer waiting for a signature.': {'ar': 'التعديل ده مبقاش مستني توقيع.', 'tr': 'Bu revizyon artık imza beklemiyor.'},
    'You raised this revision, so a different approver must sign it.': {'ar': 'إنت اللي عامل التعديل ده، فلازم معتمد تاني يوقّع عليه.', 'tr': 'Bu revizyonu siz açtınız; başka bir onaylayan imzalamalı.'},
    'A line of this revision no longer exists on the request.': {'ar': 'فيه بند في التعديل ده مبقاش موجود في الطلب.', 'tr': 'Bu revizyondaki bir satır artık talepte yok.'},
    'Final approval complete — the PO revision is now live.': {'ar': 'الاعتماد النهائي خلص — تعديل أمر الشراء بقى ساري.', 'tr': 'Son onay tamamlandı — PO revizyonu artık geçerli.'},
    'Revision rejected — the purchase order is unchanged.': {'ar': 'التعديل اترفض — أمر الشراء زي ما هو.', 'tr': 'Revizyon reddedildi — satın alma emri değişmedi.'},
    'This request is on hold. Open it and resume it before signing.': {'ar': 'الطلب ده موقوف مؤقتًا. افتحه وكمّله الأول قبل ما توقّع.', 'tr': 'Bu talep beklemede. İmzalamadan önce açıp devam ettirin.'},
    'An open purchase request for this item already exists — check it before raising another one:': {'ar': 'فيه طلب شراء مفتوح للصنف ده بالفعل — راجعه قبل ما تعمل طلب تاني:', 'tr': 'Bu kalem için zaten açık bir satın alma talebi var — başka bir talep açmadan önce onu kontrol edin:'},
    "This request's header discount is shared by several suppliers' orders, so one order cannot be revised on its own — it would change the others' money.": {'ar': 'الخصم اللي على الطلب ككل متقسّم على أوامر شراء أكتر من مورد، فمينفعش تعدّل أمر واحد لوحده — ده هيغيّر فلوس الأوامر التانية.', 'tr': 'Bu talebin genel indirimi birden fazla tedarikçinin siparişine paylaştırılmış; bu yüzden tek bir sipariş tek başına revize edilemez — diğer siparişlerin tutarlarını değiştirir.'},
    'Up to 50 requests are signed at a time — only the first 50 ticked were signed. Sign the rest from the queue again.': {'ar': 'بنوقّع على 50 طلب بالكتير في المرة الواحدة — اتوقّع على أول 50 طلب متعلّم عليهم بس. وقّع الباقي من قايمة الانتظار تاني.', 'tr': 'Tek seferde en fazla 50 talep imzalanır — yalnızca işaretlenen ilk 50 talep imzalandı. Kalanları kuyruktan tekrar imzalayın.'},
    'Could not sign the revision (': {'ar': 'معرفناش نوقّع على التعديل (', 'tr': 'Revizyon imzalanamadı ('},
    'Could not withdraw the revision (': {'ar': 'معرفناش نسحب التعديل (', 'tr': 'Revizyon geri çekilemedi ('},
    'PO revised.': {'ar': 'أمر الشراء اتعدّل.', 'tr': 'PO revize edildi.'},
    'Revision withdrawn — the purchase order is unchanged.': {'ar': 'التعديل اتسحب — أمر الشراء زي ما هو.', 'tr': 'Revizyon geri çekildi — satın alma emri değişmedi.'},
    'Save your digital signature in your profile before signing several requests at once.': {'ar': 'احفظ توقيعك الإلكتروني من صفحتك الشخصية قبل ما توقّع على كذا طلب مرة واحدة.', 'tr': 'Birden fazla talebi aynı anda imzalamadan önce dijital imzanızı profilinize kaydedin.'},
    'Tick at least one request to sign.': {'ar': 'علّم على طلب واحد على الأقل علشان توقّع.', 'tr': 'İmzalamak için en az bir talep işaretleyin.'},
    'Approved.': {'ar': 'اتعتمد.', 'tr': 'Onaylandı.'},
    'Signed.': {'ar': 'اتوقّع.', 'tr': 'İmzalandı.'},
    "Could not approve (": {
        "ar": "تعذّرت الموافقة (",
        "tr": "Onaylanamadı (",
    },
    "Could not attach the report (": {
        "ar": "تعذّر إرفاق التقرير (",
        "tr": "Rapor eklenemedi (",
    },
    "Could not authorise the advance (": {
        "ar": "تعذّرت الموافقة على الدفعة المقدمة (",
        "tr": "Avans onaylanamadı (",
    },
    "Could not cancel (": {
        "ar": "تعذّر الإلغاء (",
        "tr": "İptal edilemedi (",
    },
    "Could not close (": {
        "ar": "تعذّر الإقفال (",
        "tr": "Kapatılamadı (",
    },
    "Could not issue PO (": {
        "ar": "تعذّر إصدار أمر الشراء PO (",
        "tr": "PO düzenlenemedi (",
    },
    "Could not issue the RFQ (": {
        "ar": "تعذّر إصدار الـ RFQ (",
        "tr": "RFQ düzenlenemedi (",
    },
    "Could not record receipt (": {
        "ar": "تعذّر تسجيل الاستلام (",
        "tr": "Mal kabulü kaydedilemedi (",
    },
    "Could not record the forecast (": {
        "ar": "تعذّر تسجيل التوقّع (",
        "tr": "Tahmin kaydedilemedi (",
    },
    "Could not reject (": {
        "ar": "تعذّر الرفض (",
        "tr": "Reddedilemedi (",
    },
    "Could not resolve the quarantine (": {
        "ar": "تعذّرت تسوية حالة الحجر (",
        "tr": "Karantina durumu çözülemedi (",
    },
    "Could not retire this record (": {
        "ar": "تعذّر إيقاف هذا السجل (",
        "tr": "Bu kayıt kullanımdan kaldırılamadı (",
    },
    "Could not revise the PO (": {
        "ar": "تعذّر تعديل أمر الشراء PO (",
        "tr": "PO revize edilemedi (",
    },
    "Could not save (": {
        "ar": "تعذّر الحفظ (",
        "tr": "Kaydedilemedi (",
    },
    "Could not save pricing (": {
        "ar": "تعذّر حفظ التسعير (",
        "tr": "Fiyatlandırma kaydedilemedi (",
    },
    "Could not save the justification (": {
        "ar": "تعذّر حفظ المبرر (",
        "tr": "Gerekçe kaydedilemedi (",
    },
    "Could not save.": {
        "ar": "تعذّر الحفظ.",
        "tr": "Kaydedilemedi.",
    },
    "Could not select quote (": {
        "ar": "تعذّر اختيار عرض السعر (",
        "tr": "Teklif seçilemedi (",
    },
    "Could not send (": {
        "ar": "تعذّر الإرسال (",
        "tr": "Gönderilemedi (",
    },
    "Could not set the FX rate (": {
        "ar": "تعذّر ضبط سعر الصرف FX (",
        "tr": "FX kuru ayarlanamadı (",
    },
    "Could not settle (": {
        "ar": "تعذّرت التسوية (",
        "tr": "Mahsup edilemedi (",
    },
    "Debit note": {
        "ar": "إشعار مدين",
        "tr": "Borç dekontu",
    },
    "Debit note not found.": {
        "ar": "إشعار المدين غير موجود.",
        "tr": "Borç dekontu bulunamadı.",
    },
    "That person cannot sign procurement requests (no approval permission, or the account is inactive). Choose a delegate who can sign.": {
        "ar": "هذا الشخص لا يملك التوقيع على طلبات الشراء (ليست لديه صلاحية الاعتماد، أو حسابه غير نشط). اختر مفوَّضًا يملك التوقيع.",
        "tr": "Bu kişi satın alma taleplerini imzalayamaz (onay yetkisi yok ya da hesabı pasif). İmza yetkisi olan bir vekil seçin.",
    },
    "Delegation added.": {
        "ar": "تمت إضافة التفويض.",
        "tr": "Yetki devri eklendi.",
    },
    "Delegation revoked.": {
        "ar": "تم إلغاء التفويض.",
        "tr": "Yetki devri iptal edildi.",
    },
    "Delivery fully confirmed.": {
        "ar": "تم تأكيد التوريد بالكامل.",
        "tr": "Teslimat tamamen onaylandı.",
    },
    "Department and amount are required.": {
        "ar": "الإدارة والمبلغ مطلوبان.",
        "tr": "Departman ve tutar zorunludur.",
    },
    "Draft saved.": {
        "ar": "تم حفظ المسودة.",
        "tr": "Taslak kaydedildi.",
    },
    "Email logged (set the SMTP env vars to actually send).": {
        "ar": "تم تسجيل البريد الإلكتروني (اضبط متغيّرات بيئة SMTP ليُرسل فعليًا).",
        "tr": "E-posta kaydedildi (gerçekten göndermek için SMTP ortam değişkenlerini ayarlayın).",
    },
    "Engineering justification": {
        "ar": "المبرر الهندسي",
        "tr": "Teknik gerekçe",
    },
    "Enter a valid FX rate greater than zero.": {
        "ar": "أدخل سعر صرف FX صحيحًا أكبر من صفر.",
        "tr": "Sıfırdan büyük geçerli bir FX kuru girin.",
    },
    "Enter an accepted or a rejected quantity on at least one line.": {
        "ar": "أدخل كمية مقبولة أو مرفوضة في سطر واحد على الأقل.",
        "tr": "En az bir satıra kabul veya ret miktarı girin.",
    },
    "Enter the advance as a percentage of the PO value (1–100).": {
        "ar": "أدخل الدفعة المقدّمة كنسبة مئوية من قيمة أمر الشراء PO (1–100).",
        "tr": "Avansı, PO değerinin yüzdesi olarak girin (1–100).",
    },
    "Enter the pricing before approving the Purchasing stage — the request has no commercial value yet.": {
        "ar": "أدخل التسعير قبل اعتماد مرحلة المشتريات. الطلب بلا قيمة مالية حتى الآن.",
        "tr": "Satın alma aşamasını onaylamadan önce fiyatlandırmayı girin. Talebin henüz ticari değeri yok.",
    },
    "Enter the reason for the revision — it is kept in the PO history.": {
        "ar": "أدخل سبب التعديل. سيُحفظ في سجل أمر الشراء PO.",
        "tr": "Revizyon gerekçesini girin. PO geçmişine kaydedilir.",
    },
    "Escalation chain saved (": {
        "ar": "تم حفظ مسار التصعيد (",
        "tr": "Eskalasyon zinciri kaydedildi (",
    },
    "FX rate saved — approval thresholds now route on the EGP equivalent.": {
        "ar": "تم حفظ سعر الصرف FX. حدود الموافقة تُحسب الآن على المقابل بالـ EGP.",
        "tr": "FX kuru kaydedildi. Onay limitleri artık EGP karşılığı üzerinden hesaplanıyor.",
    },
    "File too large (max 3 MB).": {
        "ar": "حجم الملف كبير جدًا (الحد الأقصى 3 ميجابايت).",
        "tr": "Dosya çok büyük (en fazla 3 MB).",
    },
    "Final approval complete. Purchase Order drafted.": {
        "ar": "اكتملت الموافقة النهائية. تم إنشاء مسودة أمر الشراء.",
        "tr": "Nihai onay tamamlandı. Satın alma emri taslağı oluşturuldu.",
    },
    "Foreign-currency request: enter the EGP exchange rate before pricing, so the value approvals route on the true EGP equivalent.": {
        "ar": "الطلب بعملة أجنبية. أدخل سعر الصرف مقابل الـ EGP قبل التسعير. عندها تسير الموافقات على القيمة الحقيقية بالـ EGP.",
        "tr": "Talep yabancı para birimli. Fiyatlandırmadan önce EGP kurunu girin. Böylece onaylar gerçek EGP karşılığı üzerinden ilerler.",
    },
    "Give a reason so the requester knows what to do next.": {
        "ar": "اذكر السبب حتى يعرف مُقدّم الطلب ما يفعله بعد ذلك.",
        "tr": "Bir gerekçe yazın. Talebi açan kişi ne yapacağını bilsin.",
    },
    "Goods were already received against this order — close it instead of cancelling.": {
        "ar": "تم استلام أصناف على هذا الأمر. أغلقه بدلًا من إلغائه.",
        "tr": "Bu emirde mal girişi yapılmış. İptal etmek yerine emri kapatın.",
    },
    "Import failed part-way (": {
        "ar": "توقّف الاستيراد في منتصفه (",
        "tr": "İçe aktarma yarıda kaldı (",
    },
    "Invoice file too large (max 3 MB).": {
        "ar": "ملف الفاتورة كبير جدًا (الحد الأقصى 3 ميجابايت).",
        "tr": "Fatura dosyası çok büyük (en fazla 3 MB).",
    },
    "Invoice number and amount are required.": {
        "ar": "رقم الفاتورة والمبلغ مطلوبان.",
        "tr": "Fatura numarası ve tutar zorunludur.",
    },
    "Invoice recorded and matched.": {
        "ar": "تم تسجيل الفاتورة ومطابقتها.",
        "tr": "Fatura kaydedildi ve eşleştirildi.",
    },
    "Issued from stock. The request continues for what is left to buy.": {
        "ar": "تم الصرف من المخزن. الطلب مستمر للكمية المتبقّية للشراء.",
        "tr": "Stoktan çıkış yapıldı. Talep, satın alınacak kalan miktar için devam ediyor.",
    },
    "Issued from stock. The whole request was met off the shelf, so it is closed and nothing will be bought.": {
        "ar": "تم الصرف من المخزن. الرصيد غطّى الطلب بالكامل. أُغلق الطلب ولن يتم شراء أي شيء.",
        "tr": "Stoktan çıkış yapıldı. Talebin tamamı stoktan karşılandı. Talep kapatıldı, satın alma yapılmayacak.",
    },
    "Item %s added.": {
        "ar": "تمت إضافة الصنف %s.",
        "tr": "%s kalemi eklendi.",
    },
    "Item added to the catalogue.": {
        "ar": "تمت إضافة الصنف إلى الكتالوج.",
        "tr": "Kalem kataloğa eklendi.",
    },
    "Justification memo recorded.": {
        "ar": "تم تسجيل مذكرة التبرير.",
        "tr": "Gerekçe notu kaydedildi.",
    },
    "Linked. %d request line(s) now point at it.": {
        "ar": "تم الربط. أصبح %d من بنود الطلب مرتبطًا به.",
        "tr": "Bağlandı. Talebin %d satırı artık buna bağlı.",
    },
    "No active approval step.": {
        "ar": "لا توجد خطوة موافقة نشطة.",
        "tr": "Aktif onay adımı yok.",
    },
    "No catalogue item holds that code.": {
        "ar": "لا يوجد صنف في الكتالوج بهذا الكود.",
        "tr": "Bu koda sahip bir katalog kalemi yok.",
    },
    "No email on file for this vendor — add one in Vendors.": {
        "ar": "لا يوجد بريد إلكتروني لهذا المورّد. أضِف بريدًا من شاشة «الموردين».",
        "tr": "Bu tedarikçinin kayıtlı e-postası yok. «Tedarikçiler» ekranından bir e-posta ekleyin.",
    },
    "No file selected.": {
        "ar": "لم يتم اختيار ملف.",
        "tr": "Dosya seçilmedi.",
    },
    "No overdue approvals.": {
        "ar": "لا توجد موافقات متأخرة.",
        "tr": "Geciken onay yok.",
    },
    "Nothing to add.": {
        "ar": "لا يوجد ما يُضاف.",
        "tr": "Eklenecek bir şey yok.",
    },
    "Nothing to link.": {
        "ar": "لا يوجد ما يُربط.",
        "tr": "Bağlanacak bir şey yok.",
    },
    "Nothing to reject.": {
        "ar": "لا يوجد ما يُرفض.",
        "tr": "Reddedilecek bir şey yok.",
    },
    "Nothing was changed.": {
        "ar": "لم يتم تغيير أي شيء.",
        "tr": "Hiçbir değişiklik yapılmadı.",
    },
    "Nothing was issued on that line.": {
        "ar": "لم يتم صرف أي كمية على هذا البند.",
        "tr": "Bu satırda hiçbir stok çıkışı yapılmadı.",
    },
    "Nothing was issued — pick a stock item and a quantity on a line first.": {
        "ar": "لم يتم صرف أي شيء. اختر أولًا صنفًا من المخزن وكمية في أحد البنود.",
        "tr": "Stok çıkışı yapılmadı. Önce bir satırda stok kalemi ve miktar seçin.",
    },
    "Only an issued (or partially received) Purchase Order can be revised.": {
        "ar": "لا يمكن تعديل أمر الشراء إلا إذا كان صادرًا (أو مستلمًا جزئيًا).",
        "tr": "Yalnızca gönderilmiş (veya kısmen teslim alınmış) bir satın alma emri revize edilebilir.",
    },
    "Only draft or rejected requests can be edited.": {
        "ar": "لا يمكن تعديل سوى الطلبات في حالة مسودة أو مرفوضة.",
        "tr": "Yalnızca taslak veya reddedilmiş talepler düzenlenebilir.",
    },
    "Only the requester (or Purchasing/an admin) can cancel this request.": {
        "ar": "لا يستطيع إلغاء هذا الطلب إلا مُقدِّم الطلب أو المشتريات أو مسؤول النظام.",
        "tr": "Bu talebi yalnızca talebi açan kişi, Satın Alma veya bir yönetici iptal edebilir.",
    },
    "Only the spare store can be issued from here. Materials are issued against a production order, not a purchase request.": {
        "ar": "الصرف من هنا يكون من مخزن قطع الغيار فقط. الخامات تُصرف على أمر إنتاج، وليس على طلب شراء.",
        "tr": "Buradan yalnızca yedek parça deposundan çıkış yapılabilir. Malzeme çıkışı satın alma talebiyle değil, üretim emriyle yapılır.",
    },
    "Only the warehouse rung or a procurement admin can reverse an issue.": {
        "ar": "لا يمكن التراجع عن الصرف إلا من مستوى المخزن أو مسؤول المشتريات.",
        "tr": "Stok çıkışını yalnızca depo kademesi veya satın alma yöneticisi geri alabilir.",
    },
    "Over-delivery accepted as a free issue (stock unchanged).": {
        "ar": "تم قبول الكمية الزائدة كتوريد مجاني (الرصيد دون تغيير).",
        "tr": "Fazla teslimat bedelsiz olarak kabul edildi (stok değişmedi).",
    },
    "Over-delivery marked for return to the supplier.": {
        "ar": "تم تحديد الكمية الزائدة لإرجاعها إلى المورّد.",
        "tr": "Fazla teslimat tedarikçiye iade edilmek üzere işaretlendi.",
    },
    "PO revision opened — the order now reads Rev": {
        "ar": "تم فتح مراجعة لأمر الشراء PO. رقم المراجعة الآن Rev",
        "tr": "PO revizyonu açıldı. Emrin revizyonu artık Rev",
    },
    "Partial receipt recorded.": {
        "ar": "تم تسجيل استلام جزئي.",
        "tr": "Kısmi teslim alma kaydedildi.",
    },
    "Payment recorded (": {
        "ar": "تم تسجيل الدفعة (",
        "tr": "Ödeme kaydedildi (",
    },
    "Pricing saved — the request now carries its commercial value and any value-based approvals have joined the ladder.": {
        "ar": "تم حفظ التسعير. أصبح للطلب قيمة مالية، وأُضيفت إلى مسار الاعتماد الموافقات المرتبطة بالقيمة.",
        "tr": "Fiyatlandırma kaydedildi. Talebin artık ticari değeri var. Değere bağlı onaylar onay basamaklarına eklendi.",
    },
    "Purchase Order": {
        "ar": "أمر شراء",
        "tr": "Satın alma emri",
    },
    "Purchase Order emailed to the vendor.": {
        "ar": "تم إرسال أمر الشراء إلى المورّد بالبريد الإلكتروني.",
        "tr": "Satın alma emri tedarikçiye e-posta ile gönderildi.",
    },
    "Purchase request": {
        "ar": "طلب شراء",
        "tr": "Satın alma talebi",
    },
    "Put back, and the request is circulating again — it had been closed because stock covered it.": {
        "ar": "تمت الإعادة. عاد الطلب إلى دورته من جديد. كان قد أُغلق لأن الرصيد غطّاه.",
        "tr": "Geri alındı. Talep yeniden akışa girdi. Stok karşıladığı için kapatılmıştı.",
    },
    "Put back. The stock is on the shelf again and the line asks for the full quantity.": {
        "ar": "تمت الإعادة. عادت الكمية إلى رصيد المخزن. البند يطلب الكمية كاملة من جديد.",
        "tr": "Geri alındı. Stok yeniden depoda. Satır tüm miktarı istiyor.",
    },
    "Quotation attached.": {
        "ar": "تم إرفاق عرض السعر.",
        "tr": "Teklif dosyası eklendi.",
    },
    "Quote added.": {
        "ar": "تمت إضافة عرض السعر.",
        "tr": "Teklif eklendi.",
    },
    "Quote file too large (max 3 MB).": {
        "ar": "ملف عرض السعر كبير جدًا (الحد الأقصى 3 ميجابايت).",
        "tr": "Teklif dosyası çok büyük (en fazla 3 MB).",
    },
    "Quote selected — its prices and VAT are now the request's prices.": {
        "ar": "تم اختيار العرض — أسعاره والضريبة بقت هي أسعار الطلب.",
        "tr": "Teklif seçildi — fiyatları ve KDV'si artık talebin fiyatlarıdır."},
    "Quote selected. It has no per-item prices, so enter each line's price in Pricing.": {
        "ar": "تم اختيار العرض. مفيهوش سعر لكل صنف، فادخل سعر كل بند في التسعير.",
        "tr": "Teklif seçildi. Kalem bazında fiyat yok; her satırın fiyatını Fiyatlandırma'da girin."},
    "Quote selected. It is in a foreign currency: enter the EGP exchange rate in Pricing, then save to apply its prices.": {
        "ar": "تم اختيار العرض. العرض بعملة أجنبية: ادخل سعر الصرف بالجنيه في التسعير واحفظ عشان أسعاره تتطبق.",
        "tr": "Teklif seçildi. Döviz cinsinden: Fiyatlandırma'da EGP kurunu girip kaydedin, fiyatlar öyle uygulanır."},
    "Quote selected. Its prices will apply once the warehouse has checked the request — save Pricing then.": {
        "ar": "تم اختيار العرض. أسعاره هتتطبق بعد ما المخزن يراجع الطلب — احفظ التسعير وقتها.",
        "tr": "Teklif seçildi. Depo talebi kontrol ettikten sonra fiyatlar uygulanır — o zaman Fiyatlandırma'yı kaydedin."},
    "Quote selected, but its prices were not applied. Enter them in Pricing.": {
        "ar": "تم اختيار العرض، لكن أسعاره متطبقتش. ادخلها في التسعير.",
        "tr": "Teklif seçildi ama fiyatları uygulanmadı. Fiyatlandırma'da girin."},
    "Quote selected.": {
        "ar": "تم اختيار عرض السعر.",
        "tr": "Teklif seçildi.",
    },
    # --- the chosen quotation's prices did not reach the request -------------
    "Its prices were not applied: it has no per-item prices, so enter each line's price in Pricing.": {
        "ar": "أسعاره متطبقتش: مفيهوش سعر لكل صنف، فادخل سعر كل بند في التسعير.",
        "tr": "Fiyatları uygulanmadı: kalem bazında fiyat yok; her satırın fiyatını Fiyatlandırma'da girin."},
    "Its prices were NOT applied: the quotation is in a foreign currency and has no FX rate to EGP. Edit the quotation and enter its FX rate — Purchasing cannot sign the request off until then.": {
        "ar": "أسعاره لم تُطبَّق: عرض السعر بعملة أجنبية ومن غير سعر صرف مقابل الجنيه. عدّل عرض السعر وادخل سعر الصرف — المشتريات مش هتقدر توقّع الطلب لحد ما ده يتعمل.",
        "tr": "Fiyatları UYGULANMADI: teklif döviz cinsinden ve EGP kuru yok. Teklifi düzenleyip kurunu girin — o zamana kadar Satın Alma talebi onaylayamaz."},
    "Its prices were NOT applied: the quotation's currency is not recognised. Edit the quotation and choose its currency.": {
        "ar": "أسعاره لم تُطبَّق: عملة عرض السعر غير معروفة. عدّل عرض السعر واختر عملته.",
        "tr": "Fiyatları UYGULANMADI: teklifin para birimi tanınmıyor. Teklifi düzenleyip para birimini seçin."},
    "Its prices were NOT applied: it prices only some lines, in another currency than the request. Add the other lines' prices to the quotation, or price the request in Pricing.": {
        "ar": "أسعاره لم تُطبَّق: يسعّر بعض البنود فقط وبعملة غير عملة الطلب. أضف أسعار البنود الأخرى إلى عرض السعر، أو سعّر الطلب من شاشة التسعير.",
        "tr": "Fiyatları UYGULANMADI: yalnızca bazı kalemleri, talepten farklı bir para biriminde fiyatlıyor. Diğer kalemlerin fiyatlarını teklife ekleyin ya da talebi Fiyatlandırma'da fiyatlayın."},
    "Its prices will apply once the warehouse has checked the request — save the quotation again then.": {
        "ar": "أسعاره هتتطبق بعد ما المخزن يراجع الطلب — احفظ عرض السعر تاني وقتها.",
        "tr": "Depo talebi kontrol ettikten sonra fiyatları uygulanır — o zaman teklifi yeniden kaydedin."},
    "Its prices were not applied. Enter them in Pricing.": {
        "ar": "أسعاره متطبقتش. ادخلها في التسعير.",
        "tr": "Fiyatları uygulanmadı. Fiyatlandırma'da girin."},
    "The chosen quotation's prices are not on the request: it is in another currency. Edit the chosen quotation, check its currency and FX rate to EGP, and save it — then approve.": {
        "ar": "أسعار عرض السعر المعتمد مش على الطلب: هو بعملة تانية. عدّل عرض السعر المعتمد وراجع عملته وسعر الصرف مقابل الجنيه واحفظه — وبعدين وقّع.",
        "tr": "Seçilen teklifin fiyatları talepte değil: başka bir para biriminde. Seçilen teklifi düzenleyin, para birimini ve EGP kurunu kontrol edip kaydedin — sonra onaylayın."},
    # --- a quotation needs the supplier's own file ---------------------------
    "Attach the supplier's quotation file.": {
        "ar": "أرفق ملف عرض السعر الصادر من المورد.",
        "tr": "Tedarikçinin teklif dosyasını ekleyin.",
    },
    "This quotation has no supplier file. Attach it (Edit this quotation) before choosing it.": {
        "ar": "عرض السعر هذا بلا ملف من المورد. أرفقه (تعديل عرض السعر) قبل اختياره.",
        "tr": "Bu teklifin tedarikçi dosyası yok. Seçmeden önce dosyayı ekleyin (Bu teklifi düzenle).",
    },
    "The quotation on file has no supplier document. Attach the supplier's quotation file before approving.": {
        "ar": "عرض السعر المسجّل بلا مستند من المورد. أرفق ملف عرض السعر الصادر من المورد قبل الاعتماد.",
        "tr": "Kayıtlı teklifin tedarikçi belgesi yok. Onaylamadan önce tedarikçinin teklif dosyasını ekleyin.",
    },
    "The chosen quotation has no supplier file. Attach it in the Vendor quotes panel before approving.": {
        "ar": "عرض السعر المختار بلا ملف من المورد. أرفقه في لوحة عروض الموردين قبل الاعتماد.",
        "tr": "Seçilen teklifin tedarikçi dosyası yok. Onaylamadan önce Tedarikçi teklifleri panelinden ekleyin.",
    },
    "Receipt recorded.": {
        "ar": "تم تسجيل الاستلام.",
        "tr": "Mal kabulü kaydedildi.",
    },
    "Record retired from the register (retention expired": {
        "ar": "تم سحب السجل من الدفتر (انتهت مدة الاحتفاظ",
        "tr": "Kayıt kütükten çıkarıldı (saklama süresi doldu",
    },
    "Recorded. The people who typed it have been told.": {
        "ar": "تم التسجيل. وتم إبلاغ من أدخلوه.",
        "tr": "Kaydedildi. Girişi yapan kişiler bilgilendirildi.",
    },
    "Rejected goods returned to the supplier under a debit note.": {
        "ar": "تم إرجاع الأصناف المرفوضة إلى المورّد بموجب إشعار مدين.",
        "tr": "Reddedilen mallar borç dekontu ile tedarikçiye iade edildi.",
    },
    "Renamed to “": {
        "ar": "تم تغيير الاسم إلى “",
        "tr": "Şu ada değiştirildi: “",
    },
    "Request cancelled.": {
        "ar": "تم إلغاء الطلب.",
        "tr": "Talep iptal edildi.",
    },
    "Request closed.": {
        "ar": "تم إقفال الطلب.",
        "tr": "Talep kapatıldı.",
    },
    "Part of this order was never received. Give a short-close reason to close it.": {
        "ar": "جزء من الطلب ده لسه ما اتستلمش. اكتب سبب إقفال الطلب من غير استلام الباقي.",
        "tr": "Bu siparişin bir kısmı hiç teslim alınmadı. Kapatmak için eksik teslimatla kapatma nedenini yazın.",
    },
    "This request is not open for receiving — a receipt can only be booked once the purchase order has been issued.": {
        "ar": "الطلب ده مش متاح للاستلام. لازم أمر الشراء يصدر الأول عشان تسجّل الاستلام.",
        "tr": "Bu talep teslim almaya açık değil — teslim alma ancak satın alma siparişi düzenlendikten sonra kaydedilebilir.",
    },
    "Request not found.": {
        "ar": "الطلب غير موجود.",
        "tr": "Talep bulunamadı.",
    },
    "Request rejected and returned to the requester.": {
        "ar": "تم رفض الطلب وإعادته إلى مُقدّمه.",
        "tr": "Talep reddedildi ve talep sahibine iade edildi.",
    },
    "Request rejected. The purchase-request line is untouched.": {
        "ar": "تم رفض الطلب. بند طلب الشراء لم يتغيّر.",
        "tr": "Talep reddedildi. Satın alma talebi satırı değişmedi.",
    },
    "Requests for quotation are not available on this database yet.": {
        "ar": "طلبات عروض الأسعار (RFQ) غير متاحة على قاعدة البيانات هذه بعد.",
        "tr": "Teklif talepleri (RFQ) bu veritabanında henüz kullanılamıyor.",
    },
    "Responsibility matrix saved for": {
        "ar": "تم حفظ مصفوفة المسؤوليات لـ",
        "tr": "Sorumluluk matrisi şunun için kaydedildi:",
    },
    "Retired. It stays on every document that already used it.": {
        "ar": "تم إيقافه. وسيبقى ظاهرًا في المستندات التي استخدمته من قبل.",
        "tr": "Kullanımdan kaldırıldı. Daha önce kullanan tüm belgelerde görünmeye devam eder.",
    },
    "Saved and submitted for approval.": {
        "ar": "تم الحفظ والإرسال للموافقة.",
        "tr": "Kaydedildi ve onaya gönderildi.",
    },
    "Saved. Purchasing have been told.": {
        "ar": "تم الحفظ. وتم إبلاغ إدارة المشتريات.",
        "tr": "Kaydedildi. Satın alma birimi bilgilendirildi.",
    },
    "Saved. The requester has been told what changed.": {
        "ar": "تم الحفظ. وتم إبلاغ مُقدّم الطلب بما تغيّر.",
        "tr": "Kaydedildi. Talep sahibine neyin değiştiği bildirildi.",
    },
    "Say why, and which code to use instead.": {
        "ar": "اكتب السبب، والكود البديل.",
        "tr": "Nedenini ve bunun yerine kullanılacak kodu yazın.",
    },
    "Similar items were already on file — check it is not the same part under another name.": {
        "ar": "توجد أصناف مشابهة مسجّلة بالفعل. تأكّد أنه ليس الصنف نفسه باسم آخر.",
        "tr": "Benzer kalemler zaten kayıtlı. Aynı parçanın başka bir adla girilmediğinden emin olun.",
    },
    "Single-source justification saved — competitive quotes waived.": {
        "ar": "تم حفظ مبرر التوريد من مصدر وحيد. لا حاجة إلى عروض أسعار تنافسية.",
        "tr": "Tek kaynak gerekçesi kaydedildi. Rekabetçi teklif istenmeyecek.",
    },
    "Sourcing is locked — this request is already approved or closed.": {
        "ar": "التوريد مُقفل. هذا الطلب معتمد أو مُقفل بالفعل.",
        "tr": "Tedarik kilitli. Bu talep zaten onaylanmış veya kapatılmış.",
    },
    "Sourcing is locked — this request is already approved, ordered, closed or cancelled.": {
        "ar": "التوريد مُقفل. هذا الطلب معتمد أو صدر به أمر شراء أو مُقفل أو ملغى بالفعل.",
        "tr": "Tedarik kilitli. Bu talep zaten onaylanmış, siparişe dönüşmüş, kapatılmış veya iptal edilmiş.",
    },
    "Submitted for approval.": {
        "ar": "تم الإرسال للموافقة.",
        "tr": "Onaya gönderildi.",
    },
    "That code is already in the catalogue — search for it instead of adding it twice.": {
        "ar": "هذا الكود موجود بالفعل في الكتالوج. ابحث عنه بدلًا من إضافته مرة أخرى.",
        "tr": "Bu kod katalogda zaten var. Yeniden eklemek yerine arayın.",
    },
    "That code is already in the catalogue.": {
        "ar": "هذا الكود موجود بالفعل في الكتالوج.",
        "tr": "Bu kod katalogda zaten var.",
    },
    "That debit note is already settled.": {
        "ar": "تمت تسوية هذا الإشعار المدين بالفعل.",
        "tr": "Bu borç dekontu zaten kapatılmış.",
    },
    "That engineering justification no longer exists.": {
        "ar": "لم يعد هذا المبرر الهندسي موجودًا.",
        "tr": "Bu mühendislik gerekçesi artık mevcut değil.",
    },
    "That is more than the free stock on that item (free = on hand minus what another order is already promised).": {
        "ar": "الكمية أكبر من الرصيد المتاح لهذا الصنف (المتاح = الرصيد ناقص ما هو محجوز لأمر آخر).",
        "tr": "Bu miktar, kalemin serbest stokundan fazla (serbest stok = eldeki stok eksi başka siparişe ayrılan miktar).",
    },
    "That item no longer exists.": {
        "ar": "لم يعد هذا الصنف موجودًا.",
        "tr": "Bu kalem artık mevcut değil.",
    },
    "That line has no text to use as a name.": {
        "ar": "لا يحتوي هذا البند على نص يصلح لاستخدامه اسمًا.",
        "tr": "Bu satırda ad olarak kullanılabilecek bir metin yok.",
    },
    "That line is not on this request.": {
        "ar": "هذا البند غير موجود ضمن هذا الطلب.",
        "tr": "Bu satır bu talebe ait değil.",
    },
    "That quarantine record is already decided.": {
        "ar": "تم اتخاذ قرار بشأن سجل الحجر هذا بالفعل.",
        "tr": "Bu karantina kaydı hakkında zaten karar verilmiş.",
    },
    "That report is not signed yet. Engineering must approve it before Procurement can accept the request.": {
        "ar": "هذا التقرير غير موقّع بعد. يجب أن تعتمده الإدارة الهندسية أولًا، وبعدها يمكن للمشتريات قبول الطلب.",
        "tr": "Bu rapor henüz imzalanmadı. Önce mühendislik onaylamalı, sonra satın alma talebi kabul edebilir.",
    },
    "That request has already been decided.": {
        "ar": "تم اتخاذ قرار بشأن هذا الطلب بالفعل.",
        "tr": "Bu talep hakkında zaten karar verilmiş.",
    },
    "That request no longer exists.": {
        "ar": "لم يعد هذا الطلب موجودًا.",
        "tr": "Bu talep artık mevcut değil.",
    },
    "That stock item is no longer in the register.": {
        "ar": "هذا الصنف لم يعد موجودًا في سجل الأصناف.",
        "tr": "Bu stok kalemi artık kayıtta yok.",
    },
    "That would take the shelf below zero.": {
        "ar": "هذه العملية ستجعل رصيد الرف أقل من صفر.",
        "tr": "Bu işlem raf stoğunu sıfırın altına düşürür.",
    },
    "The FX rate is locked once the request is approved.": {
        "ar": "سعر الصرف FX يثبت بمجرد اعتماد الطلب.",
        "tr": "Talep onaylandıktan sonra FX kuru sabitlenir.",
    },
    "The PO isn't ready to send yet.": {
        "ar": "أمر الشراء PO غير جاهز للإرسال بعد.",
        "tr": "PO henüz gönderilmeye hazır değil.",
    },
    "The department budget for this period is exceeded — an administrator must issue this PO (or raise the budget).": {
        "ar": "تم تجاوز موازنة الإدارة لهذه الفترة. يلزم مسؤول لإصدار أمر الشراء PO، أو زيادة الموازنة.",
        "tr": "Bu dönemin departman bütçesi aşıldı. Bu PO'yu bir yönetici düzenlemeli ya da bütçe artırılmalı.",
    },
    "The request is not on the warehouse stage.": {
        "ar": "الطلب ليس في مرحلة المخزن.",
        "tr": "Talep depo aşamasında değil.",
    },
    "There is no Purchase Order yet to advance against.": {
        "ar": "لا يوجد أمر شراء بعد لصرف دفعة مقدّمة عليه.",
        "tr": "Henüz avans verilecek bir satın alma emri yok.",
    },
    "This order is off plan (over target price, over the stock ceiling, or above the net requirement after inventory netting — §3.4). DOAM §4.4 requires a written justification memo before it is signed — record it in the Coverage check / Deviation from plan panel.": {
        "ar": "هذا الأمر خارج الخطة: أعلى من السعر المستهدف، أو فوق سقف المخزون، أو أكبر من الاحتياج الصافي بعد خصم الرصيد (§3.4). يطلب DOAM §4.4 مذكّرة تبرير مكتوبة قبل التوقيع. سجّلها في لوحة «فحص التغطية / الانحراف عن الخطة».",
        "tr": "Bu sipariş plan dışı: hedef fiyatın üzerinde, stok tavanının üzerinde ya da stok mahsuplaşması sonrası net ihtiyacın üzerinde (§3.4). DOAM §4.4, imzadan önce yazılı bir gerekçe notu ister. Notu «Karşılama kontrolü / Plandan sapma» panelinde kaydedin.",
    },
    "This record has already been retired.": {
        "ar": "هذا السجل مستبعد من الاستخدام بالفعل.",
        "tr": "Bu kayıt zaten kullanımdan kaldırılmış.",
    },
    "This record is still inside its retention period — it must be kept until": {
        "ar": "هذا السجل ما زال ضمن مدة الحفظ. يجب الاحتفاظ به حتى",
        "tr": "Bu kayıt hâlâ saklama süresi içinde. Şu tarihe kadar saklanmalı:",
    },
    "This request already carries prices — issuing now would move a total people have signed.": {
        "ar": "هذا الطلب مسعّر بالفعل. الصرف الآن سيغيّر الإجمالي الذي وقّع عليه المعتمدون.",
        "tr": "Bu talep zaten fiyatlandırılmış. Şimdi çıkış yapmak, imzalanmış toplamı değiştirir.",
    },
    "This request already carries prices, so a quantity cannot be changed here — it would move a total that people have already signed. Reject it back instead.": {
        "ar": "هذا الطلب مسعّر بالفعل، فلا يمكن تعديل الكمية هنا. التعديل سيغيّر الإجمالي الذي وقّع عليه المعتمدون. أعِد الطلب بالرفض بدلًا من ذلك.",
        "tr": "Bu talep zaten fiyatlandırılmış, bu yüzden burada miktar değiştirilemez. Değişiklik, imzalanmış toplamı değiştirir. Bunun yerine talebi reddedip geri gönderin.",
    },
    "This request already carries prices. Putting stock back now would restore a quantity people have priced against.": {
        "ar": "هذا الطلب مسعّر بالفعل. إرجاع الكمية إلى المخزن الآن سيعيد كمية تم التسعير عليها.",
        "tr": "Bu talep zaten fiyatlandırılmış. Şimdi stoğu geri almak, fiyatı verilmiş bir miktarı geri getirir.",
    },
    "This request can no longer be priced.": {
        "ar": "لم يعد بالإمكان تسعير هذا الطلب.",
        "tr": "Bu talep artık fiyatlandırılamaz.",
    },
    "This request has moved on — it can no longer be unwound from here.": {
        "ar": "هذا الطلب تجاوز هذه المرحلة. لا يمكن التراجع عنه من هنا.",
        "tr": "Bu talep sonraki aşamaya geçti. Artık buradan geri alınamaz.",
    },
    "This request is already approved — only an administrator can cancel it.": {
        "ar": "هذا الطلب معتمد بالفعل. لا يلغيه إلا أحد المسؤولين.",
        "tr": "Bu talep zaten onaylandı. Yalnızca bir yönetici iptal edebilir.",
    },
    "This request is closed.": {
        "ar": "هذا الطلب مُغلق.",
        "tr": "Bu talep kapatıldı.",
    },
    "This request is not awaiting approval.": {
        "ar": "هذا الطلب ليس في انتظار الموافقة.",
        "tr": "Bu talep onay beklemiyor.",
    },
    "This request is not circulating.": {
        "ar": "هذا الطلب ليس قيد التداول.",
        "tr": "Bu talep dolaşımda değil.",
    },
    "This supplier is not on the approved vendor list, so no advance may be authorised (DOAM §4.3).": {
        "ar": "هذا المورّد غير مدرج في قائمة الموردين المعتمدين. لا يجوز صرف دفعة مقدّمة له (DOAM §4.3).",
        "tr": "Bu tedarikçi onaylı tedarikçi listesinde değil. Bu yüzden avans onaylanamaz (DOAM §4.3).",
    },
    "Type the Optima code before adding.": {
        "ar": "اكتب كود Optima قبل الإضافة.",
        "tr": "Eklemeden önce Optima kodunu yazın.",
    },
    "Type the Optima code before approving.": {
        "ar": "اكتب كود Optima قبل الاعتماد.",
        "tr": "Onaylamadan önce Optima kodunu yazın.",
    },
    "Type the name of the item you need.": {
        "ar": "اكتب اسم الصنف المطلوب.",
        "tr": "İhtiyacınız olan kalemin adını yazın.",
    },
    "Unknown document.": {
        "ar": "مستند غير معروف.",
        "tr": "Bilinmeyen belge.",
    },
    "Unsupported file type.": {
        "ar": "نوع الملف غير مدعوم.",
        "tr": "Desteklenmeyen dosya türü.",
    },
    "Unsupported invoice file type.": {
        "ar": "نوع ملف الفاتورة غير مدعوم.",
        "tr": "Desteklenmeyen fatura dosyası türü.",
    },
    "Unsupported quote file type.": {
        "ar": "نوع ملف عرض السعر غير مدعوم.",
        "tr": "Desteklenmeyen teklif dosyası türü.",
    },
    "Vendor and amount are required for a quote.": {
        "ar": "اسم المورّد والمبلغ مطلوبان لتسجيل عرض السعر.",
        "tr": "Teklif için tedarikçi ve tutar zorunludur.",
    },
    "Vendor name is required.": {
        "ar": "اسم المورّد مطلوب.",
        "tr": "Tedarikçi adı zorunludur.",
    },
    "Vendor saved.": {
        "ar": "تم حفظ المورّد.",
        "tr": "Tedarikçi kaydedildi.",
    },
    "Write the actual reasoning — a few words cannot satisfy a DOAM document requirement.": {
        "ar": "اكتب المبرر الفعلي. كلمات قليلة لا تكفي لمتطلب التوثيق في DOAM.",
        "tr": "Gerçek gerekçeyi yazın. Birkaç kelime DOAM'ın belge şartını karşılamaz.",
    },
    "You already signed another stage of this request — a different approver must take this one.": {
        "ar": "أنت وقّعت على مرحلة أخرى من هذا الطلب. هذه المرحلة يجب أن يوقّعها معتمد آخر.",
        "tr": "Bu talebin başka bir aşamasını zaten imzaladınız. Bu aşamayı başka bir onaylayıcı imzalamalı.",
    },
    "You are not authorised for this approval stage.": {
        "ar": "ليست لديك صلاحية على مرحلة الموافقة هذه.",
        "tr": "Bu onay aşaması için yetkiniz yok.",
    },
    "You are not one of the people who signs the warehouse stage on this request.": {
        "ar": "لست ضمن الموقّعين على مرحلة المخزن في هذا الطلب.",
        "tr": "Bu talepte depo aşamasını imzalayanlar arasında değilsiniz.",
    },
    "You cannot approve your own request — raising it is your signature.": {
        "ar": "لا يمكنك اعتماد طلبك بنفسك. تقديمك للطلب هو توقيعك.",
        "tr": "Kendi talebinizi onaylayamazsınız. Talebi açmanız zaten sizin imzanız.",
    },
    "Engineering rejected the justification report for this request. It has to be corrected and signed before Procurement can accept the request.": {
        "ar": "رفضت الإدارة الهندسية تقرير التبرير لهذا الطلب. يجب تصحيحه وتوقيعه قبل أن يمكن للمشتريات قبول الطلب.",
        "tr": "Mühendislik bu talebin gerekçe raporunu reddetti. Satınalmanın talebi kabul edebilmesi için düzeltilip imzalanması gerekir.",
    },
    "The engineering justification report is not finished, so it cannot be relied on. Open the report to see what is still needed.": {
        "ar": "تقرير التبرير الهندسي غير مكتمل، فلا يمكن الاعتماد عليه. افتح التقرير لترى ما زال مطلوباً.",
        "tr": "Mühendislik gerekçe raporu tamamlanmamış, bu yüzden esas alınamaz. Neyin eksik olduğunu görmek için raporu açın.",
    },
    "This request has no signed engineering justification. Cite an Engineering-Head-approved report before approving.": {
        "ar": "لا يوجد لهذا الطلب تبرير هندسي موقّع. أرفق تقريراً معتمداً من مدير الإدارة الهندسية قبل الاعتماد.",
        "tr": "Bu talebin imzalı bir mühendislik gerekçesi yok. Onaylamadan önce Mühendislik Müdürü tarafından onaylanmış bir rapor belirtin.",
    },
    "The engineering justification could not be checked, so this request cannot be approved yet. Report this to IT.": {
        "ar": "تعذّر التحقق من التبرير الهندسي، لذا لا يمكن اعتماد هذا الطلب الآن. أبلغ قسم تقنية المعلومات.",
        "tr": "Mühendislik gerekçesi kontrol edilemedi, bu yüzden bu talep şimdilik onaylanamaz. Bilgi İşlem'e bildirin.",
    },
    'Stock posting marked as posted after checking the store.': {"ar": 'تم تحديد ترحيل المخزون كمرحّل بعد التحقق من المخزن.', "tr": 'Depo kontrolünden sonra stok aktarımı aktarıldı olarak işaretlendi.'},
    'Stock posting marked as failed. You can retry it now.': {"ar": 'تم تحديد ترحيل المخزون كفاشل. يمكنك إعادة المحاولة الآن.', "tr": 'Stok aktarımı başarısız olarak işaretlendi. Şimdi yeniden deneyebilirsiniz.'},
    'Choose posted or failed to resolve the stock posting.': {"ar": 'اختر مرحّلاً أو فاشلاً لتسوية حالة ترحيل المخزون.', "tr": 'Stok aktarımını çözümlemek için aktarıldı veya başarısız seçin.'},
    'Enter a note about checking the store.': {"ar": 'أدخل ملاحظة حول التحقق من المخزن.', "tr": 'Depo kontrolü hakkında bir not girin.'},
    'This stock posting is no longer stuck in posting.': {"ar": 'لم يعد ترحيل المخزون هذا عالقاً في حالة الترحيل.', "tr": 'Bu stok aktarımı artık aktarılıyor durumunda takılı değil.'},
    'That reference belongs to a purchase request that has no purchase order yet. Issue the PO first, or receive here without the reference.': {"ar": 'هذا المرجع يخص طلب شراء لم يصدر له أمر شراء بعد. أصدر أمر الشراء أولاً، أو سجّل الاستلام هنا دون المرجع.', "tr": 'Bu referans henüz satınalma siparişi olmayan bir satınalma talebine ait. Önce siparişi düzenleyin veya burada referans olmadan teslim alın.'},
    'That reference belongs to a purchase request that was rejected. Nothing can be received against it - receive here without the reference.': {"ar": 'هذا المرجع يخص طلب شراء مرفوض. لا يمكن تسجيل أي استلام عليه - سجّل الاستلام هنا دون المرجع.', "tr": 'Bu referans reddedilmiş bir satınalma talebine ait. Bu talebe karşı teslim alınamaz - burada referans olmadan teslim alın.'},
    'That reference belongs to a purchase order. Receiving against a PO needs purchasing rights — use the Receiving tab on the request, or receive here without the PO reference.': {"ar": 'هذا المرجع يخص أمر شراء. يتطلب الاستلام على أمر شراء صلاحيات المشتريات — استخدم علامة تبويب الاستلام في الطلب، أو سجّل الاستلام هنا دون مرجع أمر الشراء.', "tr": 'Bu referans bir satınalma siparişine ait. Siparişe karşı teslim almak satınalma yetkisi gerektirir — talepteki Teslim Alma sekmesini kullanın veya burada sipariş referansı olmadan teslim alın.'},
    'Stock booked, but the purchase order refused the receipt (': {"ar": 'تم تسجيل المخزون، لكن أمر الشراء رفض الاستلام (', "tr": 'Stok kaydedildi ancak satınalma siparişi teslim almayı reddetti ('},
    'Quarantined, not added to stock - over the ordered quantity: ': {"ar": 'تم عزل الكمية الزائدة ولم تُضف إلى المخزون - الزيادة عن الكمية المطلوبة: ', "tr": 'Karantinaya alındı, stoğa eklenmedi - sipariş miktarını aşan: '},
    # --- per-line supplier choice (owner, 2026-11-25) --------------------------
    "Line awarded — its price, line discount and supplier are now on the request.": {
        "ar": "تم اختيار المورد لهذا البند — سعره وخصم البند والمورد أصبحت على الطلب.",
        "tr": "Kalem tedarikçiye verildi — fiyatı, kalem indirimi ve tedarikçisi artık talepte.",
    },
    "Could not award the line (": {
        "ar": "تعذّر اختيار المورد لهذا البند (",
        "tr": "Kalem tedarikçiye verilemedi (",
    },
    "This quotation has no price for that line.": {
        "ar": "عرض السعر هذا لا يتضمن سعرًا لهذا البند.",
        "tr": "Bu teklifte o kalem için fiyat yok.",
    },
    "This quotation's VAT differs from the request's other priced lines. A request has one VAT rate — choose a quotation with the same VAT for this line, or enter its price in Pricing.": {
        "ar": "ضريبة القيمة المضافة في هذا العرض تختلف عن باقي البنود المسعّرة في الطلب. للطلب نسبة ضريبة واحدة — اختر لهذا البند عرضًا بنفس الضريبة، أو أدخل سعره في التسعير.",
        "tr": "Bu teklifin KDV'si talebin fiyatlanmış diğer kalemlerinden farklı. Bir talebin tek bir KDV oranı vardır — bu kalem için aynı KDV'li bir teklif seçin veya fiyatını Fiyatlandırma'da girin.",
    },
    "This quotation is in another currency than the request's other priced lines. A request has one currency — choose a quotation in the same currency for this line.": {
        "ar": "هذا العرض بعملة مختلفة عن باقي البنود المسعّرة في الطلب. للطلب عملة واحدة — اختر لهذا البند عرضًا بنفس العملة.",
        "tr": "Bu teklif, talebin fiyatlanmış diğer kalemlerinden farklı bir para biriminde. Bir talebin tek bir para birimi vardır — bu kalem için aynı para biriminde bir teklif seçin.",
    },
    "A line awarded from this quotation could not take its corrected figures, so it is no longer marked as awarded. Check that line's price in Pricing.": {
        "ar": "تعذّر نقل الأرقام المصحّحة إلى بند مُسند إلى هذا العرض، لذلك لم يعد مُعلّمًا كمُسند إليه. راجع سعر هذا البند في التسعير.",
        "tr": "Bu tekliften verilen bir kalem düzeltilmiş rakamları alamadı, bu yüzden artık verilmiş olarak işaretli değil. O kalemin fiyatını Fiyatlandırma'da kontrol edin.",
    },
    "A line of this request is awarded to this quotation. Choose another supplier for that line first, then remove this.": {
        "ar": "أحد بنود هذا الطلب مُسند إلى هذا العرض. اختر موردًا آخر لهذا البند أولًا، ثم احذف هذا العرض.",
        "tr": "Bu talebin bir kalemi bu teklife verilmiş. Önce o kalem için başka bir tedarikçi seçin, sonra bunu kaldırın.",
    },
    # --- Delivery follow-up (app/approvals/delivery.py) ---
    "Enter the required-by date as a valid date.": {"ar": "اكتب تاريخ \"مطلوب قبل يوم\" بشكل صحيح.", "tr": "Gerekli tarihi geçerli bir tarih olarak girin."},
    "The required-by date cannot be in the past.": {"ar": "تاريخ \"مطلوب قبل يوم\" مينفعش يكون فات.", "tr": "Gerekli tarih geçmişte olamaz."},
    "That supplier's purchase order was cancelled — its lines cannot be received.": {"ar": "أمر الشراء بتاع المورد ده اتلغى — بنوده مينفعش تتستلم.", "tr": "Bu tedarikçinin satın alma siparişi iptal edildi — kalemleri teslim alınamaz."},
    "Enter the promised delivery date as a valid date.": {"ar": "اكتب ميعاد التوريد المتفق عليه بشكل صحيح.", "tr": "Söz verilen teslimat tarihini geçerli bir tarih olarak girin."},
    "That purchase order no longer exists.": {"ar": "أمر الشراء ده مبقاش موجود.", "tr": "Bu satın alma siparişi artık yok."},
    "That purchase order is cancelled.": {"ar": "أمر الشراء ده ملغي.", "tr": "Bu satın alma siparişi iptal edildi."},
    "This request is no longer waiting for delivery.": {"ar": "الطلب ده مبقاش مستني توريد.", "tr": "Bu talep artık teslimat beklemiyor."},
    "Write why this purchase order is being cancelled.": {"ar": "اكتب سبب إلغاء أمر الشراء ده.", "tr": "Bu satın alma siparişinin neden iptal edildiğini yazın."},
    "This request has only one purchase order — cancel the request itself instead.": {"ar": "الطلب ده ليه أمر شراء واحد بس — ألغي الطلب نفسه بدل كده.", "tr": "Bu talebin tek bir satın alma siparişi var — bunun yerine talebin kendisini iptal edin."},
    "This purchase order is already cancelled.": {"ar": "أمر الشراء ده ملغي بالفعل.", "tr": "Bu satın alma siparişi zaten iptal edildi."},
    "Only an order that is still waiting for delivery can be cancelled.": {"ar": "مينفعش تلغي غير أمر لسه مستني توريد.", "tr": "Yalnızca hâlâ teslimat bekleyen bir sipariş iptal edilebilir."},
    "Goods were already received or returned against this order, so it cannot be cancelled. Close the request short instead.": {"ar": "في بضاعة اتستلمت أو اترجعت على الأمر ده، فمينفعش يتلغي. اقفل الطلب على الكمية اللي وصلت بدل كده.", "tr": "Bu siparişe karşı zaten mal teslim alındı veya iade edildi, bu yüzden iptal edilemez. Bunun yerine talebi eksik kapatın."},
    "Money was already invoiced or paid against this order, so it cannot be cancelled.": {"ar": "في فاتورة أو دفعة اتسجلت على الأمر ده، فمينفعش يتلغي.", "tr": "Bu siparişe karşı zaten fatura kesildi veya ödeme yapıldı, bu yüzden iptal edilemez."},
    "Purchase order cancelled.": {"ar": "تم إلغاء أمر الشراء.", "tr": "Satın alma siparişi iptal edildi."},
    "Purchase order cancelled and the cancellation emailed to the supplier.": {"ar": "تم إلغاء أمر الشراء واتبعت الإلغاء للمورد على الإيميل.", "tr": "Satın alma siparişi iptal edildi ve iptal tedarikçiye e-postayla gönderildi."},
    "Purchase order cancelled. The cancellation email was not sent — email (SMTP) is not set up on this server, so tell the supplier yourself.": {"ar": "تم إلغاء أمر الشراء. إيميل الإلغاء متبعتش — الإيميل (SMTP) مش متظبط على السيرفر ده، فبلّغ المورد بنفسك.", "tr": "Satın alma siparişi iptal edildi. İptal e-postası gönderilmedi — bu sunucuda e-posta (SMTP) ayarlı değil, tedarikçiye kendiniz bildirin."},
    "Purchase order cancelled. The supplier has no email address on file, so tell them yourself.": {"ar": "تم إلغاء أمر الشراء. المورد مالوش إيميل مسجل، فبلّغه بنفسك.", "tr": "Satın alma siparişi iptal edildi. Tedarikçinin kayıtlı e-posta adresi yok, kendiniz bildirin."},
    "Promised delivery date saved.": {"ar": "تم حفظ ميعاد التوريد المتفق عليه.", "tr": "Söz verilen teslimat tarihi kaydedildi."},
    "This is the last open order on the request, so cancelling it cancels the whole request — only an administrator can do that.": {"ar": "ده آخر أمر شراء مفتوح على الطلب، فإلغاؤه هيلغي الطلب كله — وده مينفعش يعمله غير الأدمن.", "tr": "Bu, talepteki son açık sipariş; iptal etmek talebin tamamını iptal eder — bunu yalnızca bir yönetici yapabilir."},
    "Purchase order cancelled, but the cancellation email could not be sent — tell the supplier yourself.": {"ar": "تم إلغاء أمر الشراء، بس إيميل الإلغاء معرفش يتبعت — بلّغ المورد بنفسك.", "tr": "Satın alma siparişi iptal edildi, ancak iptal e-postası gönderilemedi — tedarikçiye kendiniz bildirin."},
    "That purchase order was cancelled. Nothing can be received against it - receive here without the reference.": {"ar": "أمر الشراء ده اتلغى. مينفعش يتسجل عليه أي استلام - سجّل الاستلام هنا من غير المرجع.", "tr": "Bu satın alma siparişi iptal edildi. Bu siparişe karşı teslim alınamaz - burada referans olmadan teslim alın."},
}



def _t(text, lang):
    """`text` in `lang`, or the English original when there is no translation."""
    if not text or lang in (None, "", "en"):
        return text
    row = MESSAGES.get(text.strip())
    if not row:
        return text
    return row.get(lang) or text


def translate(text, lang):
    """Translate a whole flashed message.

    Messages are sometimes assembled — "Added." + a count, a refusal with a code
    appended — so an exact hit is tried first and a prefix match second, which
    catches the fixed part of a sentence that carries a number.
    """
    if not text or lang in (None, "", "en"):
        return text
    s = str(text).strip()
    hit = _t(s, lang)
    if hit != s:
        return hit
    # Longest first: "Item %s added." must not be matched by a shorter prefix
    # that happens to share its opening words.
    for src in sorted(MESSAGES, key=len, reverse=True):
        # The length floor stops a short fragment matching half the file. An entry
        # ending in "(" is already unambiguous, and requiring 20 characters left
        # eight of them dead — "Could not approve (" is nineteen, so every refusal
        # that fell through to it reached an Arabic reader in English.
        if s.startswith(src[:40]) and (len(src) >= 20 or src.rstrip().endswith("(")):
            tail = s[len(src):] if s.startswith(src) else ""
            return (MESSAGES[src].get(lang) or s) + tail
    return text


# Vendors lane: the two new quote-route refusals (vendor pending / blocked).
from app.approvals.vendors_text import MESSAGES as _VMSG  # noqa: E402
MESSAGES.update(_VMSG)
# Item control lane: the settings page messages.
from app.approvals.item_control import MESSAGES as _ICMSG  # noqa: E402
MESSAGES.update(_ICMSG)
