# -*- coding: utf-8 -*-
"""
Arabic and Turkish for the Workflow & Governance prose.

The English wording lives in app/approvals/constants.py (STAGE_EXPLAIN,
ROLE_EXPLAIN, DOC_SECTIONS, STATUS_MEANING) and is the source of truth for what
the code actually enforces. The texts here are translations of exactly those
paragraphs — same meaning, same controls, nothing added or softened.

They are SEEDED into the *_ar / *_tr columns of proc_stage_meta, proc_role_meta
and proc_doc (only where the column is still empty, so an admin's edit is never
overwritten) and act as the code default for a row that has none.

Acronyms stay acronyms inside the sentence: PR, PO, RFQ, EGP, FX, OEM, CFO, CEO.
"""

# --- Ladder stages (proc_stage_meta.explanation_ar / _tr) ------------------
STAGE_AR = {
    "requester": (
        "أي مستخدم لديه صلاحية «إنشاء طلبات الشراء» يفتح الطلب ويحدّد المطلوب: "
        "الصنف، الكمية، الوحدة، المواصفات، والقسم الذي يُطلب له. إرسال الطلب هو "
        "نفسه توقيع مقدّم الطلب، ولهذا لا يجوز له أبدًا التوقيع على أي مرحلة "
        "موافقة في طلبه هو. كما لا يستطيع مقدّم الطلب إدخال أي قيمة مالية — سعر "
        "الوحدة والتكلفة التقديرية ونسبة الضريبة وشرط الدفع تُحذف من أي شيء "
        "يرسله، ويُدخلها قسم المشتريات لاحقًا."),
    "warehouse": (
        "المخزن يراجع الرصيد قبل الالتزام بأي مبلغ: هل الصنف موجود فعلًا على "
        "الرف، وما هي آخر كمية طُلبت وبأي سعر. هذه مرحلة احتياج — مطلوبة دائمًا، "
        "أيًا كانت قيمة الطلب."),
    "factory_manager": (
        "مدير المصنع يؤكّد أن الطلب ضروري تشغيليًا وأن مواصفاته صحيحة للأصل أو "
        "الماكينة أو الخط الذي طُلب من أجله. مرحلة احتياج — مطلوبة دائمًا، أيًا "
        "كانت قيمة الطلب."),
    "purchasing": (
        "المشتريات مسؤولة عن الجانب التجاري: اختيار المورّد، وإدخال الأسعار، "
        "وتحديد سعر الصرف (FX) في الطلب بعملة أجنبية، وجمع عروض الأسعار "
        "المتنافسة (أو تسجيل مبرّر الشراء من مصدر واحد) — وبعد اكتمال كل "
        "التوقيعات، إصدار أمر الشراء. تعمل بوابتان في هذه المرحلة: لا يمكن للطلب "
        "أن يتجاوزها بدون تسعير، ولا يمكن لطلب مرتفع القيمة أن يتجاوزها بدون "
        "عروض أسعار متنافسة."),
    "finance": (
        "الإدارة المالية تراجع الطلب بعد تسعيره مقابل موازنة القسم والضريبة "
        "وشروط الدفع قبل الالتزام بالمبلغ. مرحلة قيمة — لا تُضاف إلى سلسلة "
        "الموافقات إلا عندما يبلغ الإجمالي المكافئ بالجنيه المصري (EGP) الحد "
        "الخاص بها."),
    "cfo": (
        "المدير المالي (CFO) يعتمد الإنفاق الملتزم به الكبير ويؤكّد توفّر "
        "التمويل له. مرحلة قيمة — لا تُضاف إلى سلسلة الموافقات إلا عندما يبلغ "
        "الإجمالي المكافئ بالجنيه المصري (EGP) الحد الخاص بها."),
    "ceo": (
        "الرئيس التنفيذي (CEO) هو السلطة النهائية على الإنفاق الكبير. مرحلة قيمة "
        "— لا تُضاف إلى سلسلة الموافقات إلا عندما يبلغ الإجمالي المكافئ بالجنيه "
        "المصري (EGP) الحد الخاص بها. وبمجرد وصول هذا التوقيع الأخير يصبح الطلب "
        "معتمدًا ويُجهَّز رقم أمر الشراء تلقائيًا."),
}

STAGE_TR = {
    "requester": (
        "«Satın alma talebi oluşturma» yetkisi olan her kullanıcı talebi açar ve "
        "neyin gerektiğini yazar: malzeme, miktar, birim, teknik özellik ve hangi "
        "departman için istendiği. Talebi göndermek talep edenin imzası sayılır; "
        "bu yüzden kendi talebinin hiçbir onay aşamasını imzalayamaz. Talep eden "
        "para bilgisi de giremez — birim fiyat, tahmini maliyet, vergi oranı ve "
        "ödeme koşulu gönderdiği her şeyden çıkarılır ve sonradan Satın Alma "
        "tarafından doldurulur."),
    "warehouse": (
        "Depo, herhangi bir para taahhüdünden önce stoku kontrol eder: malzeme "
        "rafta var mı, en son ne kadar sipariş edildi ve hangi fiyata. Bu bir "
        "ihtiyaç aşamasıdır — talebin tutarı ne olursa olsun her zaman gereklidir."),
    "factory_manager": (
        "Fabrika Müdürü, talebin işletme açısından gerekli olduğunu ve talep "
        "edildiği varlık, makine veya hat için doğru tanımlandığını teyit eder. "
        "İhtiyaç aşaması — talebin tutarı ne olursa olsun her zaman gereklidir."),
    "purchasing": (
        "Satın Alma ticari tarafın sahibidir: tedarikçiyi seçer, fiyatları girer, "
        "döviz cinsinden bir talepte kur (FX) oranını belirler, rekabetçi "
        "teklifleri toplar (ya da tek kaynak gerekçesini kaydeder) ve tüm imzalar "
        "tamamlandığında Satın alma emrini açar. Bu aşamada iki kapı çalışır: "
        "talep fiyatlandırılmadan bu aşamayı geçemez ve yüksek tutarlı bir talep "
        "rekabetçi teklifler olmadan geçemez."),
    "finance": (
        "Finans, para taahhüt edilmeden önce fiyatlandırılmış talebi departman "
        "bütçesi, vergi ve ödeme koşullarına göre kontrol eder. Tutar aşaması — "
        "yalnızca EGP karşılığı toplam kendi eşiğine ulaştığında onay zincirine "
        "katılır."),
    "cfo": (
        "CFO, önemli tutarlı taahhüt edilen harcamayı yetkilendirir ve kaynağının "
        "bulunduğunu teyit eder. Tutar aşaması — yalnızca EGP karşılığı toplam "
        "kendi eşiğine ulaştığında onay zincirine katılır."),
    "ceo": (
        "CEO, büyük harcamalarda son yetkilidir. Tutar aşaması — yalnızca EGP "
        "karşılığı toplam kendi eşiğine ulaştığında onay zincirine katılır. Bu "
        "son imza geldiğinde talep Onaylandı durumuna geçer ve otomatik olarak "
        "bir Satın alma emri numarası hazırlanır."),
}

# --- Roles (proc_role_meta.explanation_ar / _tr) ---------------------------
ROLE_AR = {
    "storekeeper": (
        "أمين المخزن. يوقّع على مرحلة المخزن: يؤكّد المخزون الفعلي وكمية آخر طلب "
        "وسعره، ويستلم الأصناف مقابل أمر الشراء (PO)."),
    "warehouse_manager": (
        "مسؤول عن المخزن ككل. يوقّع على مرحلة المخزن، وهو جهة التصعيد عند عدم "
        "توفّر أمين المخزن."),
    "factory_manager": (
        "مسؤول عن المصنع. يوقّع على مرحلة مدير المصنع: يؤكّد أن الطلب مبرَّر "
        "تشغيليًا ومواصفاته صحيحة."),
    "purchasing_manager": (
        "يدير المشتريات. يُسعّر الطلبات، ويحدّد أسعار الصرف (FX)، ويجمع عروض "
        "أسعار المورّدين ويقارنها، ويسجّل مبرّرات الشراء من مصدر واحد، ويوقّع على "
        "مرحلة المشتريات، ويصدر أوامر الشراء."),
    "finance_manager": (
        "مسؤول عن الرقابة المالية. يوقّع على مرحلة الإدارة المالية، ويتولّى "
        "موازنات الأقسام، ويسجّل فواتير المورّدين والمدفوعات."),
    "finance_user": (
        "عضو في فريق الإدارة المالية. يوقّع على مرحلة الإدارة المالية، ويتولّى "
        "تسجيل الفواتير وإثبات المدفوعات في العمل اليومي."),
    "cfo": (
        "المدير المالي. يوقّع على مرحلة CFO للإنفاق الملتزم به الكبير، وهو صاحب "
        "القرار في حالات تجاوز الموازنة."),
    "ceo": (
        "الرئيس التنفيذي. يوقّع على مرحلة CEO — السلطة النهائية على أكبر عمليات "
        "الشراء."),
}

ROLE_TR = {
    "storekeeper": (
        "Depo sorumlusudur. Depo aşamasını imzalar: güncel stoku, son sipariş "
        "miktarını ve son sipariş fiyatını teyit eder ve malları PO karşılığında "
        "teslim alır."),
    "warehouse_manager": (
        "Deponun bütününden sorumludur. Depo aşamasını imzalar ve depo sorumlusu "
        "bulunmadığında yükseltme noktasıdır."),
    "factory_manager": (
        "Fabrikadan sorumludur. Fabrika Müdürü aşamasını imzalar: talebin işletme "
        "açısından gerekçeli ve doğru tanımlanmış olduğunu teyit eder."),
    "purchasing_manager": (
        "Satın almayı yürütür. Talepleri fiyatlandırır, FX kurlarını belirler, "
        "tedarikçi tekliflerini toplayıp karşılaştırır, tek kaynak gerekçelerini "
        "kaydeder, Satın Alma aşamasını imzalar ve satın alma emirlerini açar."),
    "finance_manager": (
        "Mali kontrolden sorumludur. Finans aşamasını imzalar, departman "
        "bütçelerinin sahibidir, tedarikçi faturalarını kaydeder ve ödemeleri "
        "işler."),
    "finance_user": (
        "Finans ekibi üyesidir. Finans aşamasını imzalar; fatura kaydı ve ödeme "
        "girişini günlük olarak yürütür."),
    "cfo": (
        "Mali İşler Direktörü (CFO). Önemli tutarlı taahhüt edilen harcama için "
        "CFO aşamasını imzalar ve bütçe aşımlarında yetkili kişidir."),
    "ceo": (
        "Genel Müdür (CEO). CEO aşamasını imzalar — en büyük satın almalarda son "
        "yetkili."),
}

# --- Free-text blocks: overview + one per gate (proc_doc.body_ar / _tr) ----
DOC_AR = {
    "overview": (
        "يبدأ طلب الشراء كاحتياج خالص: مقدّم الطلب يحدّد «ما» هو المطلوب، ولا "
        "يحدّد تكلفته أبدًا. ثم يُوجَّه الطلب صاعدًا في سلسلة من التوقيعات. المخزن "
        "ومدير المصنع والمشتريات يوقّعون دائمًا. تُدخل المشتريات التسعير، وبعد "
        "ذلك فقط تُضاف الموافقات المبنية على القيمة (الإدارة المالية، CFO، CEO) "
        "إلى السلسلة — كل واحدة من الحد الخاص بها وما فوقه، والمقارنة تكون على "
        "الإجمالي المكافئ بالجنيه المصري (EGP)، أي أن الطلب بعملة أجنبية يُحوَّل "
        "أولًا. كل معتمد يضع توقيعه الرقمي المحفوظ، ويُسجَّل كل توقيع كحدث قابل "
        "للتحقق منه بشكل مستقل. وعند موافقة المرحلة الأخيرة يُجهَّز أمر الشراء "
        "تلقائيًا؛ تصدره المشتريات (بشرط موازنة القسم)، ثم تُستلم الأصناف سطرًا "
        "بسطر، وتُسجَّل فاتورة المورّد، وتُجرى المطابقة الثلاثية، وبعد ذلك فقط "
        "يمكن إثبات الدفع — بحدٍّ أقصى هو ما طُلب وما تمت الفاتورة به."),
    "pricing_gate": (
        "لا يمكن اعتماد مرحلة المشتريات ما دام الطلب غير مسعَّر. هذا ما يمنع طلبًا "
        "بقيمة صفر من التسلّل عبر الموافقات المبنية على القيمة (الإدارة المالية / "
        "CFO / CEO). وعند إدخال التسعير على طلب يدور بالفعل في الدورة، يُعاد حساب "
        "مراحل القيمة على الإجمالي الجديد: تُضاف المراحل التي أصبحت مطلوبة، "
        "وتُحذف مراحل القيمة التي لم تعد مستحقة ولم يُوصل إليها بعد. أما المراحل "
        "المعتمدة أو المرفوضة أو النشطة حاليًا فلا تُمَس أبدًا، حتى لا يتأثر "
        "توقيع جارٍ. كما يجب أن يحمل الطلب بعملة غير الجنيه المصري سعر صرف "
        "حقيقيًا قبل تسعيره، وإلا لتم توجيه قيمته على الرقم الأجنبي كما هو."),
    "rfq": (
        "الطلب المسعَّر الذي يبلغ إجماليه المكافئ بالجنيه المصري (EGP) حد طلب عروض "
        "الأسعار (RFQ) يجب أن يحمل الحد الأدنى من عروض الأسعار من مورّدين "
        "مختلفين قبل أن توقّع المشتريات — عرضان مُدخلان لنفس المورّد لا يحقّقان "
        "القاعدة. ويمكن للمشتريات تجاوز القاعدة بتسجيل مبرّر الشراء من مصدر واحد "
        "على الطلب (قطعة من المُنتِج الأصلي OEM فقط، قطعة غيار حصرية، حالة طارئة "
        "حقيقية)؛ ويُحفظ المبرّر على الطلب ويُدرج في سجل التدقيق. أما تحت الحد، أو "
        "ما دام الطلب غير مسعَّر، فلا تعمل هذه البوابة."),
    "sod": (
        "تعمل قاعدتان للاستقلالية على كل توقيع. (١) الموافقة الذاتية: لا يجوز "
        "لمقدّم الطلب أن يوقّع على أي مرحلة موافقة في طلبه — فإنشاؤه للطلب هو "
        "توقيعه بالفعل. (٢) الدور المزدوج: لا يجوز لشخص واحد أن يوقّع على مرحلتين "
        "مختلفتين في الطلب نفسه، ولا حتى إذا جعله تفويضٌ مؤهَّلًا للاثنتين؛ فكل "
        "مرحلة يجب أن تكون عيونًا مستقلة. والطلب المرفوض الذي يُعاد إرساله يحصل "
        "على مجموعة مراحل جديدة تمامًا، فلا يعيق التاريخ القديم دورة جديدة. "
        "وعندما يكون خيار «إعفاء المسؤولين» مفعَّلًا، يتجاوز super_admin وأي دور "
        "يحمل صلاحية مسؤول المشتريات كلتا القاعدتين حتى يستطيع فريق صغير أن يسير "
        "بالطلب في السلسلة كاملة؛ أوقِف الخيار للوضع الصارم، حيث يلتزم المسؤولون "
        "بالقواعد تمامًا كأي شخص آخر."),
    "budget_gate": (
        "يُمنع إصدار أمر الشراء إذا كان للقسم سطر موازنة محدّد للسنة الحالية وكان "
        "إنفاقه الملتزم به قد تجاوزها بالفعل. ويُحسب الإنفاق الملتزم به من "
        "الطلبات المؤرَّخة في تلك السنة بالحالات: معتمد، صدر أمر شراء، مستلم "
        "جزئيًا، مستلم، أو مغلق. والقسم الذي لا توجد له موازنة مُعرَّفة لا يُمنع "
        "أبدًا. ويمكن للمسؤول أن يفرض إصدار أمر الشراء؛ ويُكتب هذا التجاوز في سجل "
        "التدقيق."),
    "three_way_match": (
        "قبل الدفع، يُقارَن «المطلوب» (إجمالي أمر الشراء وكمياته) مع «المستلم» "
        "(كميات إذون الاستلام وقيمتها بسعر أمر الشراء) ومع «المفوتَر» (فواتير "
        "المورّد المسجَّلة، بالإجمالي شاملًا الضريبة). ويُنبَّه على نقص التوريد "
        "لكنه لا يمنع الدفع — فالدفع مقابل ما تم توريده فعلًا في استلام جزئي أمر "
        "مشروع. أما الفوترة الزائدة فتمنع الدفع: فاتورة تتجاوز إجمالي أمر الشراء، "
        "أو فاتورة قبل الضريبة تتجاوز قيمة ما تم استلامه. وتسمح المقارنة بهامش "
        "تفاوت 1% من إجمالي أمر الشراء أو وحدة عملة واحدة، أيهما أكبر."),
    "payment_cap": (
        "لا يمكن إثبات الدفع إلا بعد وجود أمر شراء — ولا يجوز أبدًا مقابل طلب "
        "مسوَّدة أو قيد الموافقة أو ملغى. ولا يجوز أن تتجاوز المدفوعات المتراكمة "
        "إجمالي أمر الشراء، وإذا وُجدت فواتير فلا يجوز أن تتجاوز إجمالي الفواتير "
        "شاملًا الضريبة كذلك — أي أن الحد الفعلي هو الأقل من الاثنين، مع تطبيق "
        "هامش تفاوت الدفع على كل منهما. ويُرفض الدفع أيضًا ما دامت المطابقة "
        "الثلاثية تُظهر فوترة زائدة. ويمكن للمسؤول تجاوز أي من هذه القيود، "
        "ويُسجَّل التجاوز في سجل التدقيق."),
}

DOC_TR = {
    "overview": (
        "Bir satın alma talebi saf ihtiyaç olarak başlar: talep eden NEyin "
        "gerektiğini yazar, maliyetini asla yazmaz. Sonra talep bir imza zinciri "
        "boyunca yukarı yönlendirilir. Depo, Fabrika Müdürü ve Satın Alma her "
        "zaman imzalar. Fiyatlandırmayı Satın Alma girer; ancak ondan sonra "
        "tutara bağlı onaylar (Finans, CFO, CEO) zincire katılır — her biri kendi "
        "tutarından itibaren ve karşılaştırma EGP karşılığı toplam üzerinden "
        "yapılır, yani döviz cinsinden bir talep önce çevrilir. Her onaylayan "
        "kayıtlı dijital imzasını basar ve her imza ayrıca doğrulanabilir bir "
        "olay olarak kaydedilir. Son aşama onayladığında otomatik olarak bir "
        "Satın alma emri hazırlanır; Satın Alma bunu açar (departman bütçesine "
        "bağlı olarak), mallar satır satır teslim alınır, tedarikçi faturası "
        "kaydedilir, 3'lü mutabakat çalışır ve ancak ondan sonra bir ödeme "
        "kaydedilebilir — sipariş edilen ve faturalanan tutarla sınırlı olarak."),
    "pricing_gate": (
        "Talep fiyatlandırılmamışken Satın Alma aşaması onaylanamaz. Sıfır "
        "tutarlı bir talebin, tutara bağlı Finans / CFO / CEO onaylarını atlayarak "
        "geçmesini engelleyen kural budur. Zaten dolaşımda olan bir talebe fiyat "
        "girildiğinde, tutara bağlı basamaklar yeni toplama göre yeniden "
        "hesaplanır: artık gereken basamaklar eklenir, artık gerekmeyen ve henüz "
        "sıra gelmemiş tutar basamakları kaldırılır. Onaylanmış, reddedilmiş veya "
        "şu anda aktif olan aşamalara asla dokunulmaz; böylece devam eden bir "
        "imza hiç bozulmaz. EGP dışı bir talep, fiyatlandırılabilmesi için gerçek "
        "bir kur oranı da taşımak zorundadır; aksi hâlde tutarı ham döviz rakamı "
        "üzerinden yönlendirilirdi."),
    "rfq": (
        "EGP karşılığı toplamı RFQ eşiğine ulaşan fiyatlandırılmış bir talep, "
        "Satın Alma imzalayabilmeden önce en az asgari sayıda teklifi FARKLI "
        "tedarikçilerden taşımak zorundadır — aynı tedarikçiye girilen iki teklif "
        "bu kuralı karşılamaz. Satın Alma, talebe tek kaynak gerekçesi kaydederek "
        "kuralı geçebilir (yalnızca OEM parça, tek kaynaklı yedek parça, gerçek "
        "acil durum); gerekçe talep üzerinde saklanır ve denetim kaydına girer. "
        "Eşiğin altında veya talep hâlâ fiyatlandırılmamışken bu kapı çalışmaz."),
    "sod": (
        "Her imzada iki bağımsızlık kuralı çalışır. (1) Kendi kendine onay: bir "
        "talebi açan kişi, o talebin hiçbir onay aşamasını imzalayamaz — talebi "
        "açmak zaten onun imzasıdır. (2) Çifte rol: bir kişi aynı talebin İKİ "
        "FARKLI aşamasını imzalayamaz; bir vekâlet onu her ikisi için yetkili "
        "kılsa bile. Her basamak bağımsız bir çift göz olmalıdır. Reddedilip "
        "yeniden gönderilen bir talep tamamen yeni bir adım seti alır; böylece "
        "eski geçmiş yeni bir döngüyü engellemez. «Yöneticiler muaf» açıkken "
        "super_admin ve Satın Alma yöneticisi yetkisi taşıyan her rol bu iki "
        "kuralı atlar; böylece küçük bir ekip bir talebi zincirin tamamında "
        "yürütebilir. Katı mod için bunu kapatın; o zaman yöneticiler de tam "
        "olarak herkes gibi bu kurallara bağlıdır."),
    "budget_gate": (
        "Departmanın cari yıl için tanımlı bir bütçe satırı varsa VE taahhüt "
        "edilen harcaması bunu zaten aşmışsa, Satın alma emrinin açılması "
        "engellenir. Taahhüt edilen harcama, o yıl tarihli ve durumu onaylandı, "
        "PO açıldı, kısmen teslim alındı, teslim alındı veya kapatıldı olan "
        "talepleri sayar. Bütçe satırı tanımlı olmayan bir departman hiçbir zaman "
        "engellenmez. Bir yönetici PO'yu zorlayarak açabilir; bu istisna denetim "
        "kaydına yazılır."),
    "three_way_match": (
        "Ödemeden önce SİPARİŞ EDİLEN (PO genel toplamı ve miktarları), TESLİM "
        "ALINAN (mal kabul miktarları ve bunların sipariş fiyatıyla değeri) ve "
        "FATURALANAN (kayıtlı tedarikçi faturaları, vergi dahil brüt) "
        "karşılaştırılır. Eksik teslim işaretlenir ancak ödemeyi ENGELLEMEZ — "
        "kısmi bir teslimde fiilen teslim alınan için ödeme yapmak meşrudur. "
        "Fazla faturalama ise engeller: PO toplamının üzerinde faturalanmış "
        "olması, ya da vergi öncesi faturanın teslim alınanın değerini aşması. "
        "Karşılaştırma, PO toplamının %1'i veya 1 para birimi (hangisi büyükse) "
        "kadar tolerans tanır."),
    "payment_cap": (
        "Bir ödeme yalnızca Satın alma emri var olduktan sonra kaydedilebilir — "
        "taslak, onay bekleyen veya iptal edilmiş bir talebe karşı asla. "
        "Birikimli ödemeler PO genel toplamını aşamaz ve fatura mevcutsa "
        "faturalanan brüt toplamı da aşamaz; yani pratikte üst sınır, her birine "
        "ödeme toleransı uygulanmış hâlleriyle bu ikisinin DAHA KÜÇÜĞÜdür. 3'lü "
        "mutabakat fazla faturalama gösterirken de ödeme reddedilir. Bir yönetici "
        "bunların herhangi birini geçersiz kılabilir ve bu istisna denetim "
        "kaydına girer."),
}

# --- PR status meanings (proc_doc sections 'status.<key>') -----------------
STATUS_AR = {
    "draft": "قيد الاستكمال من مقدّم الطلب — لم يدخل سلسلة الموافقات بعد.",
    "pending": "داخل سلسلة الموافقات، في انتظار توقيع المرحلة الحالية.",
    "approved": "وقّعت كل المراحل المطلوبة؛ وتم تجهيز رقم أمر شراء (PO).",
    "rejected": "رفضته إحدى المراحل مع ذكر السبب؛ ويرتد إلى مقدّم الطلب الذي "
                "يمكنه التصحيح وإعادة الإرسال (وهذا يبني سلسلة الموافقات من جديد).",
    "po_issued": "صدر أمر الشراء إلى المورّد؛ ويمكن بدء الدفع.",
    "partially_received": "تم استلام بعض بنود الطلب وليس كلها.",
    "received": "تم تأكيد التوريد لكل البنود.",
    "closed": "مكتمل ومؤرشف — لا يُتوقَّع أي إجراء آخر.",
    "cancelled": "سُحب من مقدّم الطلب أو المشتريات أو المسؤول قبل الاكتمال.",
}

STATUS_TR = {
    "draft": "Talep eden tarafından dolduruluyor — henüz onay zincirinde değil.",
    "pending": "Zincirde; mevcut aşamanın imzası bekleniyor.",
    "approved": "Gereken tüm aşamalar imzaladı; bir PO numarası hazırlandı.",
    "rejected": "Bir aşama gerekçesiyle reddetti; talep, düzeltip yeniden "
                "gönderebilecek olan talep edene geri döner (bu, zinciri "
                "sıfırdan yeniden kurar).",
    "po_issued": "Satın alma emri tedarikçiye açıldı; ödeme başlayabilir.",
    "partially_received": "Sipariş satırlarının bir kısmı teslim alındı, tamamı değil.",
    "received": "Her satır için teslim teyit edildi.",
    "closed": "Tamamlandı ve arşivlendi — başka bir işlem beklenmiyor.",
    "cancelled": "Tamamlanmadan önce talep eden, Satın Alma veya bir yönetici "
                 "tarafından geri çekildi.",
}

# --- What the seeder and the renderer consume ------------------------------
# {lang: {row key: text}} per prose table. 'en' is deliberately absent: the
# English column and constants.py stay the single English source.
STAGE = {"ar": STAGE_AR, "tr": STAGE_TR}
ROLE = {"ar": ROLE_AR, "tr": ROLE_TR}
DOC = {
    "ar": dict(DOC_AR, **{f"status.{k}": v for k, v in STATUS_AR.items()}),
    "tr": dict(DOC_TR, **{f"status.{k}": v for k, v in STATUS_TR.items()}),
}


# =========================================================================
# LABELS — the short strings that NAME a stage, gate, status, rule or role.
#
# These are not database prose, but they cannot go through the client-side
# data-i18n swap either: the keys they would need live in app/static/i18n/*.json,
# which this module does not own, and app.js does
#     el.textContent = DICT[key] || key
# so a key that is not in the dictionary is printed RAW on the page ("Warehouse"
# becomes "proc.stage.warehouse") — in every language, English included. So the
# workflow page resolves them server-side, exactly like the prose beside them.
# See app/approvals/services.py::_labels.
#
# 'en' is present here so the template has one source per label instead of a
# Jinja literal map that only English readers ever see.
# =========================================================================

STAGE_LABEL = {
    "en": {"requester": "Requester", "warehouse": "Warehouse",
           "factory_manager": "Factory Manager", "purchasing": "Purchasing",
           "finance": "Finance", "cfo": "CFO", "ceo": "CEO"},
    "ar": {"requester": "مقدّم الطلب", "warehouse": "المخزن",
           "factory_manager": "مدير المصنع", "purchasing": "المشتريات",
           "finance": "الإدارة المالية", "cfo": "المدير المالي (CFO)",
           "ceo": "الرئيس التنفيذي (CEO)"},
    "tr": {"requester": "Talep eden", "warehouse": "Depo",
           "factory_manager": "Fabrika Müdürü", "purchasing": "Satın Alma",
           "finance": "Finans", "cfo": "CFO", "ceo": "CEO"},
}

GATE_LABEL = {
    "en": {"pricing_gate": "Pricing gate", "rfq": "RFQ / competitive quotes",
           "sod": "Segregation of duties", "budget_gate": "Budget gate",
           "three_way_match": "3-way match", "payment_cap": "Payment cap"},
    "ar": {"pricing_gate": "بوابة التسعير",
           "rfq": "طلب عروض الأسعار (RFQ) — العروض المتنافسة",
           "sod": "الفصل بين المهام (SoD)", "budget_gate": "بوابة الموازنة",
           "three_way_match": "المطابقة الثلاثية", "payment_cap": "حد الدفع"},
    "tr": {"pricing_gate": "Fiyatlandırma kapısı",
           "rfq": "RFQ / rekabetçi teklifler",
           "sod": "Görevler ayrılığı (SoD)", "budget_gate": "Bütçe kapısı",
           "three_way_match": "3'lü mutabakat", "payment_cap": "Ödeme üst sınırı"},
}

STATUS_LABEL = {
    "en": {"draft": "Draft", "pending": "Pending", "approved": "Approved",
           "rejected": "Rejected", "po_issued": "PO issued",
           "partially_received": "Partially received", "received": "Received",
           "closed": "Closed", "cancelled": "Cancelled"},
    "ar": {"draft": "مسوَّدة", "pending": "قيد الموافقة", "approved": "معتمد",
           "rejected": "مرفوض", "po_issued": "صدر أمر الشراء",
           "partially_received": "مستلم جزئيًا", "received": "مستلم",
           "closed": "مغلق", "cancelled": "ملغى"},
    "tr": {"draft": "Taslak", "pending": "Onay bekliyor", "approved": "Onaylandı",
           "rejected": "Reddedildi", "po_issued": "PO açıldı",
           "partially_received": "Kısmen teslim alındı", "received": "Teslim alındı",
           "closed": "Kapatıldı", "cancelled": "İptal edildi"},
}

# Full titles of the configurable rules (the short tag forms stay on their
# existing pwf.set.* client keys).
SETTING_LABEL = {
    "en": {"rfq_quote_min": "Competing quotes required",
           "rfq_value_threshold": "Quotes required from (EGP)",
           "sod_admin_exempt": "Admins exempt from segregation of duties",
           "payment_tolerance_pct": "Payment tolerance (%)"},
    "ar": {"rfq_quote_min": "عدد عروض الأسعار المتنافسة المطلوبة",
           "rfq_value_threshold": "عروض الأسعار مطلوبة من (EGP)",
           "sod_admin_exempt": "استثناء المسؤولين من الفصل بين المهام",
           "payment_tolerance_pct": "هامش تفاوت الدفع (%)"},
    "tr": {"rfq_quote_min": "Gereken rekabetçi teklif sayısı",
           "rfq_value_threshold": "Tekliflerin gerekli olduğu tutar (EGP)",
           "sod_admin_exempt": "Yöneticiler görevler ayrılığından muaf",
           "payment_tolerance_pct": "Ödeme toleransı (%)"},
}

# Platform role labels. English comes from app/security.py ROLES (the code
# default) — a role an admin RENAMED keeps the admin's name in every language,
# same principle as an admin-edited explanation.
ROLE_LABEL = {
    "ar": {
        "super_admin": "مسؤول النظام الأعلى",
        "it_director": "مدير عام تقنية المعلومات",
        "it_manager": "مدير تقنية المعلومات",
        "service_desk_agent": "موظف مكتب الخدمة",
        "asset_manager": "مدير الأصول",
        "monitoring_admin": "مسؤول المراقبة",
        "project_manager": "مدير المشروعات",
        "finance_user": "موظف بالإدارة المالية",
        "hr_user": "موظف الموارد البشرية",
        "quality_inspector": "مفتّش الجودة",
        "storekeeper": "أمين المخزن",
        "compliance_officer": "مسؤول الالتزام",
        "production_manager": "مدير الإنتاج",
        "executive_viewer": "مُشاهد تنفيذي",
        "normal_user": "مستخدم عادي",
        "itsm_user": "مستخدم نظام الدعم الفني (ITSM)",
        "maintenance_manager": "مدير الصيانة",
        "maintenance_technician": "فني صيانة",
        "production_supervisor": "مشرف إنتاج",
        "section_head": "رئيس قسم",
        "department_manager": "مدير إدارة",
        "factory_manager": "مدير المصنع",
        "purchasing_manager": "مدير المشتريات",
        "finance_manager": "مدير الإدارة المالية",
        "warehouse_manager": "مدير المخازن",
        "cfo": "المدير المالي (CFO)",
        "ceo": "الرئيس التنفيذي (CEO)",
        "hr_officer": "مسؤول الموارد البشرية",
        "hr_probation_admin": "مسؤول فترة الاختبار (الموارد البشرية)",
    },
    "tr": {
        "super_admin": "Süper Yönetici",
        "it_director": "BT Direktörü",
        "it_manager": "BT Müdürü",
        "service_desk_agent": "Servis Masası Temsilcisi",
        "asset_manager": "Varlık Yöneticisi",
        "monitoring_admin": "İzleme Yöneticisi",
        "project_manager": "Proje Yöneticisi",
        "finance_user": "Finans Kullanıcısı",
        "hr_user": "İK Kullanıcısı",
        "quality_inspector": "Kalite Kontrolörü",
        "storekeeper": "Depo Sorumlusu",
        "compliance_officer": "Uyum Sorumlusu",
        "production_manager": "Üretim Müdürü",
        "executive_viewer": "Yönetici Görüntüleyici",
        "normal_user": "Standart Kullanıcı",
        "itsm_user": "ITSM Kullanıcısı",
        "maintenance_manager": "Bakım Müdürü",
        "maintenance_technician": "Bakım Teknisyeni",
        "production_supervisor": "Üretim Şefi",
        "section_head": "Kısım Şefi",
        "department_manager": "Departman Müdürü",
        "factory_manager": "Fabrika Müdürü",
        "purchasing_manager": "Satın Alma Müdürü",
        "finance_manager": "Finans Müdürü",
        "warehouse_manager": "Depo Müdürü",
        "cfo": "Mali İşler Direktörü (CFO)",
        "ceo": "Genel Müdür (CEO)",
        "hr_officer": "İK Sorumlusu",
        "hr_probation_admin": "İK Deneme Süresi Yöneticisi",
    },
}

# Small UI strings the trilingual editor itself needs.
UI = {
    "en": {
        "text_en": "Text (English)", "text_ar": "Text (Arabic)",
        "text_tr": "Text (Turkish)",
        "lang_hint": "Each language is saved on its own: a box left empty keeps the "
                     "text already stored for that language. Reset clears all three "
                     "back to the built-in wording.",
        "no_approve_opt": "No approve permission",
    },
    "ar": {
        "text_en": "النص (بالإنجليزية)", "text_ar": "النص (بالعربية)",
        "text_tr": "النص (بالتركية)",
        "lang_hint": "كل لغة تُحفَظ على حدة: الحقل المتروك فارغًا يُبقي النص المحفوظ "
                     "لتلك اللغة كما هو. و«إعادة التعيين» تُرجع اللغات الثلاث إلى "
                     "الصياغة المدمجة.",
        "no_approve_opt": "لا يحمل صلاحية الموافقة",
    },
    "tr": {
        "text_en": "Metin (İngilizce)", "text_ar": "Metin (Arapça)",
        "text_tr": "Metin (Türkçe)",
        "lang_hint": "Her dil kendi başına kaydedilir: boş bırakılan bir kutu, o dil "
                     "için saklı metni olduğu gibi bırakır. Sıfırla, üç dilin hepsini "
                     "yerleşik metne döndürür.",
        "no_approve_opt": "Onay yetkisi yok",
    },
}
