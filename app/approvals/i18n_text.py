# -*- coding: utf-8 -*-
"""
Arabic and Turkish for the Workflow & Governance prose.

The English wording lives in app/approvals/constants.py (STAGE_EXPLAIN,
ROLE_EXPLAIN, DOC_SECTIONS, STATUS_MEANING) and is the source of truth for what
the code actually enforces. The texts here are translations of exactly those
paragraphs — same meaning, same controls, nothing added or softened.

They are SEEDED into the *_ar / *_tr columns of proc_stage_meta, proc_role_meta
and proc_doc (only where the column is still NULL — never translated — so neither
an admin's edit nor a box he emptied on purpose is ever overwritten) and act as
the code default for a row that has none.

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
    "scd": (
        "مدير سلسلة الإمداد مسؤول عن التشغيل وإعادة تدبير المخزون: يؤكّد أن "
        "الطلب حاجة تجديد فعلية، وأن مصدره وتوقيته صحيحان مقابل الرصيد. مرحلة "
        "قيمة في المصروفات التشغيلية — لا تُضاف إلى سلسلة الموافقات إلا عندما "
        "يتجاوز الإجمالي المكافئ بالجنيه المصري (EGP) الحد الخاص بها — أما في "
        "الطلبات الرأسمالية فهو يوقّع أيًا كانت القيمة. وإذا تبيّن بوضوح أن "
        "الطلب التزام إنتاجي أو خاص بالصيانة، فإن مرحلة مدير المصنع تحمل هذا "
        "المستوى بدلًا منها وتُسقَط هذه المرحلة؛ وإذا جمع الطلب الأمرين معًا أو "
        "تعذّر التمييز، فإن المديرين كليهما يوقّعان. لا يوقّع على هذه المرحلة "
        "افتراضيًا "
        "سوى دور مدير سلسلة الإمداد، وهو يعتمد فقط — لا يُسعّر ولا يشتري ولا "
        "يدفع."),
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
    "bod": (
        "مجلس الإدارة هو أعلى سلطة في سلسلة الموافقات: يوقّع على أكبر "
        "الالتزامات، وليس فوقه جهة يُصعَّد إليها. مرحلة قيمة — لا تُضاف إلى "
        "سلسلة الموافقات إلا عندما يتجاوز الإجمالي المكافئ بالجنيه المصري (EGP) "
        "الحد الخاص بها، وهذا الحد في الطلبات الرأسمالية أقل منه في المصروفات "
        "التشغيلية. وفوق 10,000,000 جنيه من قيمة الالتزام لا يستطيع المجلس "
        "الاعتماد قبل تسجيل دراسة جدوى مكتوبة على الطلب، ويُفحص الشرط نفسه "
        "مبكرًا عند مرحلة المشتريات حتى لا يدور طلب بهذا الحجم بدونها. لا يوقّع "
        "على هذه المرحلة افتراضيًا سوى دور مجلس الإدارة، وهو يعتمد فقط — لا "
        "يُسعّر ولا يشتري ولا يدفع. وبمجرد وصول هذا التوقيع الأخير يصبح الطلب "
        "معتمدًا ويُجهَّز رقم أمر الشراء تلقائيًا.")
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
    "scd": (
        "Tedarik Zinciri Direktörü, operasyonel ve stok ikmalinin sahibidir: "
        "talebin gerçekten bir ikmal ihtiyacı olduğunu, doğru kaynaktan ve stoka "
        "göre doğru zamanda istendiğini teyit eder. İşletme harcamalarında tutar "
        "aşaması — yalnızca EGP karşılığı toplam kendi eşiğini aştığında onay "
        "zincirine katılır — sermaye harcaması niteliğindeki bir talepte ise "
        "tutar ne olursa olsun imzalar. Bir talep açıkça üretim veya bakım "
        "taahhüdü olarak belirlendiğinde bu kademeyi Fabrika Müdürü aşaması "
        "taşır ve bu aşama düşer; talep ikisi birden olduğunda ya da ayırt "
        "edilemediğinde her iki direktör de imzalar. Bu aşamayı varsayılan "
        "olarak yalnızca Tedarik Zinciri Direktörü rolü imzalar ve yalnızca "
        "onaylar — fiyatlandırmaz, satın almaz, ödeme yapmaz."),
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
    "bod": (
        "Yönetim Kurulu, onay zincirindeki en üst yetkidir: en büyük taahhütleri "
        "imzalar ve üzerinde yükseltilebilecek bir merci yoktur. Tutar aşaması — "
        "yalnızca EGP karşılığı toplam kendi eşiğini aştığında onay zincirine "
        "katılır; bu eşik sermaye harcamalarında işletme harcamalarına göre daha "
        "düşüktür. Taahhüt edilen tutar 10.000.000 EGP'yi aştığında Yönetim "
        "Kurulu, talebe yazılı bir iş gerekçesi kaydedilmeden onay veremez; aynı "
        "koşul daha önce Satın Alma aşamasında da kontrol edilir, böylece bu "
        "büyüklükte bir talep gerekçesiz dolaşıma girmez. Bu aşamayı varsayılan "
        "olarak yalnızca Yönetim Kurulu rolü imzalar ve yalnızca onaylar — "
        "fiyatlandırmaz, satın almaz, ödeme yapmaz. Bu son imza geldiğinde talep "
        "Onaylandı durumuna geçer ve otomatik olarak bir Satın alma emri "
        "numarası hazırlanır.")
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
    "plant_director": (
        "مسؤول عن الالتزامات الإنتاجية والخاصة بالصيانة. يوقّع على مرحلة مدير "
        "المصنع: إذا تبيّن بوضوح أن الطلب التزام من هذا النوع، فإن هذا المدير "
        "يحمل ذلك المستوى وتُسقَط مرحلة مدير سلسلة الإمداد. يملك الاطلاع والاعتماد فقط — "
        "لا ينشئ الطلبات ولا يُسعّرها ولا يشتري ولا يدفع."),
    "supply_chain_director": (
        "مسؤول عن التشغيل وإعادة تدبير المخزون. الدور الوحيد المرتبط بمرحلة مدير "
        "سلسلة الإمداد: إذا تبيّن بوضوح أن الطلب إعادة تدبير للمخزون، فإن هذا "
        "المدير يحمل ذلك المستوى وتُسقَط مرحلة مدير المصنع. يملك الاطلاع والاعتماد "
        "فقط — لا ينشئ الطلبات ولا يُسعّرها ولا يشتري ولا يدفع."),
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
    "financial_director": (
        "مسؤول عن الرقابة على الدفع. يوقّع على مرحلة الإدارة المالية — مراجعة "
        "الطلب بعد تسعيره مقابل موازنة القسم والضريبة وشروط الدفع — ويصرف "
        "المدفوعات للمورّدين، ولهذا يقع هذا الحق هنا لا لدى المشتري الذي التزم "
        "بالمبلغ. يملك الاطلاع والاعتماد وصرف المدفوعات؛ ولا يستطيع تسعير طلب ولا "
        "إصدار "
        "أمر شراء (PO)."),
    "cfo": (
        "المدير المالي. يوقّع على مرحلة CFO للإنفاق الملتزم به الكبير، وهو صاحب "
        "القرار في حالات تجاوز الموازنة."),
    "ceo": (
        "الرئيس التنفيذي. يوقّع على مرحلة CEO — السلطة النهائية على أكبر عمليات "
        "الشراء."),
    "managing_director": (
        "العضو المنتدب. يوقّع على مرحلة CEO — نفس الدرجة التي يوقّع عليها الرئيس التنفيذي، لأن "
        "الدورين مرتبطان بها وأيٌّ من التوقيعين يفي بها. وهو التوقيع الأخير على "
        "الإنفاق الكبير دون مستوى مجلس الإدارة. يملك الاطلاع والاعتماد فقط — "
        "يلتزم بالمبلغ ولا يشتري ولا يدفع."),
    "board": (
        "مجلس الإدارة. يوقّع على مرحلة مجلس الإدارة في أكبر الالتزامات، ولا "
        "يستطيع اعتماد التزام يتجاوز 10,000,000 جنيه قبل تسجيل دراسة جدوى "
        "مكتوبة على الطلب. يملك الاطلاع والاعتماد فقط — بلا صلاحيات شراء ولا "
        "صرف.")
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
    "plant_director": (
        "Üretim ve bakım taahhütlerinden sorumludur. Fabrika Müdürü aşamasını "
        "imzalar: bir talep açıkça üretim veya bakım taahhüdü olarak "
        "belirlendiğinde bu "
        "kademeyi bu direktör taşır ve Tedarik Zinciri Direktörü aşaması düşer. "
        "Yalnızca görüntüleme ve onay yetkisi vardır — talep açmaz, "
        "fiyatlandırmaz, satın almaz, ödeme yapmaz."),
    "supply_chain_director": (
        "Operasyonel ve stok ikmalinden sorumludur. Tedarik Zinciri Direktörü "
        "aşamasına bağlı tek roldür: bir talep açıkça stok ikmali olarak "
        "belirlendiğinde bu kademeyi bu direktör taşır ve Fabrika Müdürü aşaması "
        "düşer. Yalnızca görüntüleme ve onay yetkisi vardır — talep açmaz, "
        "fiyatlandırmaz, satın almaz, ödeme yapmaz."),
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
    "financial_director": (
        "Ödeme kontrolünden sorumludur. Finans aşamasını imzalar — "
        "fiyatlandırılmış talebi departman bütçesi, vergi ve ödeme koşullarına "
        "göre kontrol eder — ve tedarikçilere ödemeleri serbest bırakır; bu "
        "yetki bu yüzden harcamayı taahhüt eden alıcıda değil buradadır. "
        "Görüntüleme, onay ve ödeme yetkileri vardır; talebi fiyatlandıramaz ve "
        "satın alma emri açamaz."),
    "cfo": (
        "Mali İşler Direktörü (CFO). Önemli tutarlı taahhüt edilen harcama için "
        "CFO aşamasını imzalar ve bütçe aşımlarında yetkili kişidir."),
    "ceo": (
        "Genel Müdür (CEO). CEO aşamasını imzalar — en büyük satın almalarda son "
        "yetkili."),
    "managing_director": (
        "Murahhas Aza. CEO aşamasını imzalar — Genel Müdür ile aynı kademe; her iki rol de bu "
        "aşamaya bağlıdır ve iki imzadan biri onu karşılar. Yönetim Kurulu "
        "düzeyinin altındaki büyük harcamalarda son imzadır. Yalnızca "
        "görüntüleme ve onay yetkisi vardır — parayı taahhüt eder, satın almaz "
        "ve ödeme yapmaz."),
    "board": (
        "Yönetim Kurulu. En büyük taahhütlerde Yönetim Kurulu aşamasını imzalar "
        "ve 10.000.000 EGP'yi aşan bir taahhüdü, talebe yazılı bir iş gerekçesi "
        "kaydedilmeden onaylayamaz. Yalnızca görüntüleme ve onay yetkisi vardır "
        "— satın alma ve ödeme yetkisi yoktur.")
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
    "sod_escalation": (
        "لا يوافق أحد أبدًا على طلبه هو — لكن إذا كان مُقدّم الطلب هو الشخص الوحيد "
        "الذي يجوز له التوقيع على إحدى مراحل السلسلة، فستنتظر تلك المرحلة توقيعًا لا "
        "يمكن أن يأتي قانونيًا أبدًا. لذلك يصعّد النظام تلك المرحلة درجة واحدة أعلى "
        "في الهيكل التنظيمي: يوقّعها الرئيس المباشر للدور المُعطَّل بدلًا منه. ولا "
        "يحدث ذلك إلا عندما لا يوجد فعلًا أي شخص آخر — فإن وُجد زميل ثانٍ يحمل نفس "
        "الدور فهو الذي يوقّع، ولا يتغيّر أي شيء في الطلب. والصعود لا ينتهي أبدًا عند "
        "مُقدّم الطلب: إذا كان الرئيس المباشر هو نفسه مُقدّم الطلب يواصل النظام "
        "الصعود لأعلى، وإذا ضبط المسؤول السلسلة في حلقة مغلقة يكتشف النظام ذلك "
        "ويعتبرها «لا يوجد أحد أعلى». وكل مرحلة تم تصعيدها تُوسَم على الطلب، وتظهر في "
        "سجل الموافقات، وتُطبَع على مستندي طلب الشراء وأمر الشراء، حتى يرى المراجع "
        "الاستثناء دون فتح النظام. وإذا انتهى الصعود دون العثور على أحد — كأن يرفع "
        "الرئيس التنفيذي (CEO) طلبًا يحتاج توقيع الرئيس التنفيذي — يُرفَض الطلب عند "
        "الإرسال بسبب واضح، ويُبلَّغ مسؤولو المشتريات ليُدبَّر تفويض أو تدخّل من "
        "مسؤول، بدلًا من أن يبقى الطلب معلّقًا في الصفّ بلا نهاية. ولا يمسّ ذلك "
        "المراحل الموجودة ولا ترتيبها ولا الحدود المالية ولا أي بوابة: التصعيد يغيّر "
        "«مَن يوقّع» المرحلة فقط. والسلسلة نفسها قابلة للتعديل لكل دور من إعدادات "
        "المشتريات؛ والدور الذي لم يُحدَّد له رئيس مباشر يعني أنه لا يوجد أحد أعلى "
        "منه."),
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
        "المورّد المسجَّلة، بالإجمالي شاملًا الضريبة). أما الفوترة الزائدة فتمنع "
        "الدفع: فاتورة تتجاوز إجمالي أمر الشراء، أو فاتورة قبل الضريبة تتجاوز "
        "قيمة ما تم استلامه. ونقص التوريد لا يمنع الدفع بل يحدّه: يُذكر النقص "
        "بالكمية وبالقيمة، ولا يجوز أن تتجاوز المدفوعات المتراكمة قيمة ما تم "
        "استلامه فعلًا — فالدفع مقابل ما تم توريده أمر مشروع، أما دفع قيمة أمر "
        "الشراء كاملة مقابل توريد جزئي فلا. وتسمح المقارنة بسماحيات الدليل: 2% "
        "من إجمالي أمر الشراء أو 500 جنيه على القيمة، أيهما أكبر، و5% على "
        "الكمية."),
    "payment_cap": (
        "لا يمكن إثبات الدفع إلا بعد وجود أمر شراء — ولا يجوز أبدًا مقابل طلب "
        "مسوَّدة أو قيد الموافقة أو ملغى. ولا يجوز أن تتجاوز المدفوعات المتراكمة "
        "إجمالي أمر الشراء، وإذا وُجدت فواتير فلا يجوز أن تتجاوز إجمالي الفواتير "
        "شاملًا الضريبة كذلك، وفي حالة نقص التوريد لا يجوز أن تتجاوز قيمة ما تم "
        "استلامه فعلًا شاملة الضريبة — أي أن الحد الفعلي هو الأقل من الثلاثة "
        "المنطبقة، مع تطبيق هامش تفاوت الدفع على كل منها. ويُرفض الدفع أيضًا ما "
        "دامت المطابقة الثلاثية تُظهر فوترة زائدة. ويمكن للمسؤول تجاوز أي من هذه "
        "القيود، ويُسجَّل التجاوز في سجل التدقيق.")
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
    "sod_escalation": (
        "Hiç kimse kendi talebini onaylamaz — ancak bir basamağı imzalayabilecek TEK "
        "kişi talep sahibinin kendisiyse, o basamak hukuken hiçbir zaman gelemeyecek "
        "bir imzayı bekler. Bu durumda motor o basamağı organizasyon şemasında BİR "
        "SEVİYE YUKARI yükseltir: engellenen rolün üst yöneticisi basamağı onun "
        "yerine imzalar. Bu yalnızca gerçekten başka hiç kimse yokken devreye girer — "
        "rolü taşıyan ikinci bir kişi varsa o meslektaş imzalar ve talepte hiçbir şey "
        "değişmez. Tırmanma asla talep sahibinde durmaz: üst yönetici de talebi açan "
        "kişiyse yukarı çıkmaya devam eder; bir yöneticinin döngü hâline getirdiği "
        "zincir tespit edilip «üstte kimse yok» olarak değerlendirilir. Yükseltilen "
        "bir basamak talebin üzerine damgalanır, onay izinde gösterilir ve satın alma "
        "talebi ile satın alma emri belgelerine yazdırılır; böylece denetçi sapmayı "
        "uygulamayı açmadan görür. Tırmanma tükendiğinde — CEO imzası gerektiren bir "
        "talebi CEO'nun kendisinin açması gibi — talep gönderim anında açık bir "
        "gerekçeyle REDDEDİLİR ve Satın Alma yöneticileri bilgilendirilir; böylece "
        "talep kuyrukta sessizce beklemek yerine bir vekâlet ya da yönetici "
        "müdahalesi ayarlanabilir. Hangi aşamaların var olduğu, sıraları, tutar "
        "eşikleri ve bütün kapılar bundan etkilenmez: yükseltme yalnızca bir basamağı "
        "KİMİN imzalayacağını değiştirir. Zincirin kendisi Satın Alma ayarlarında rol "
        "bazında düzenlenebilir; üst yöneticisi tanımlanmamış bir rol, üstünde kimse "
        "olmadığı anlamına gelir."),
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
        "karşılaştırılır. Fazla faturalama ödemeyi engeller: PO toplamının "
        "üzerinde faturalanmış olması, ya da vergi öncesi faturanın teslim "
        "alınanın değerini aşması. Eksik teslim ödemeyi ENGELLEMEZ, SINIRLAR: "
        "eksiklik hem miktar hem tutar olarak belirtilir ve birikimli ödeme "
        "fiilen teslim alınanın değerini aşamaz — kısmi bir teslimde teslim "
        "alınan için ödeme yapmak meşrudur, parçası için PO'nun tamamını ödemek "
        "değil. Karşılaştırma DOAM toleranslarını tanır: değerde PO toplamının "
        "%2'si veya 500 EGP (hangisi büyükse), miktarda %5."),
    "payment_cap": (
        "Bir ödeme yalnızca Satın alma emri var olduktan sonra kaydedilebilir — "
        "taslak, onay bekleyen veya iptal edilmiş bir talebe karşı asla. "
        "Birikimli ödemeler PO genel toplamını aşamaz; fatura mevcutsa "
        "faturalanan brüt toplamı da aşamaz; eksik teslimde ise fiilen teslim "
        "alınanın brüt değerini aşamaz. Yani pratikte üst sınır, her birine "
        "ödeme toleransı uygulanmış hâlleriyle geçerli olanların EN KÜÇÜĞÜdür. "
        "3'lü mutabakat fazla faturalama gösterirken de ödeme reddedilir. Bir "
        "yönetici bunların herhangi birini geçersiz kılabilir ve bu istisna "
        "denetim kaydına girer.")
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
    "cancelled": "سُحب من مقدّم الطلب أو المشتريات أو المسؤول قبل الاكتمال."
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
                 "tarafından geri çekildi."
}

# --- What the seeder and the renderer consume ------------------------------
# {lang: {row key: text}} per prose table. 'en' is deliberately absent: the
# English column and constants.py stay the single English source.
STAGE = {"ar": STAGE_AR, "tr": STAGE_TR}
ROLE = {"ar": ROLE_AR, "tr": ROLE_TR}
DOC = {
    "ar": dict(DOC_AR, **{f"status.{k}": v for k, v in STATUS_AR.items()}),
    "tr": dict(DOC_TR, **{f"status.{k}": v for k, v in STATUS_TR.items()})
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
           "finance": "Finance", "cfo": "CFO", "ceo": "CEO",
           "scd": "Supply Chain Director", "bod": "Board of Directors"},
    "ar": {"requester": "مقدّم الطلب", "warehouse": "المخزن",
           "factory_manager": "مدير المصنع", "purchasing": "المشتريات",
           "finance": "الإدارة المالية", "cfo": "المدير المالي (CFO)",
           "ceo": "الرئيس التنفيذي (CEO)",
           "scd": "مدير سلسلة الإمداد", "bod": "مجلس الإدارة"},
    "tr": {"requester": "Talep eden", "warehouse": "Depo",
           "factory_manager": "Fabrika Müdürü", "purchasing": "Satın Alma",
           "finance": "Finans", "cfo": "CFO", "ceo": "CEO",
           "scd": "Tedarik Zinciri Direktörü", "bod": "Yönetim Kurulu"}
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
           "three_way_match": "3'lü mutabakat", "payment_cap": "Ödeme üst sınırı"}
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
           "closed": "Kapatıldı", "cancelled": "İptal edildi"}
}

# Full titles of the configurable rules (the short tag forms stay on their
# existing pwf.set.* client keys).
SETTING_LABEL = {
    "en": {"rfq_quote_min": "Competing quotes required",
           "rfq_value_threshold": "Quotes required from (EGP)",
           "sod_admin_exempt": "Admins exempt from segregation of duties",
           "payment_tolerance_pct": "Payment tolerance (%)",
           "show_expenditure_kind": "Show ‘Expenditure type’ on requests",
           "show_sales_order": "Show ‘Sales order’ on requests",
           "show_forecast_ref": "Show ‘Agreed forecast’ on requests",
           "show_cost_center": "Show ‘Cost centre’ on requests",
           "show_delivery_condition": "Show ‘Delivery condition’ on requests"},
    "ar": {"rfq_quote_min": "عدد عروض الأسعار المتنافسة المطلوبة",
           "rfq_value_threshold": "عروض الأسعار مطلوبة من (EGP)",
           "sod_admin_exempt": "استثناء المسؤولين من الفصل بين المهام",
           "payment_tolerance_pct": "هامش تفاوت الدفع (%)",
           "show_expenditure_kind": "إظهار «نوع الإنفاق» في الطلبات",
           "show_sales_order": "إظهار «أمر البيع» في الطلبات",
           "show_forecast_ref": "إظهار «التوقّع المعتمد» في الطلبات",
           "show_cost_center": "إظهار «مركز التكلفة» في الطلبات",
           "show_delivery_condition": "إظهار «شرط التسليم» في الطلبات"},
    "tr": {"rfq_quote_min": "Gereken rekabetçi teklif sayısı",
           "rfq_value_threshold": "Tekliflerin gerekli olduğu tutar (EGP)",
           "sod_admin_exempt": "Yöneticiler görevler ayrılığından muaf",
           "payment_tolerance_pct": "Ödeme toleransı (%)",
           "show_expenditure_kind": "Taleplerde ‘Harcama türü’ göster",
           "show_sales_order": "Taleplerde ‘Satış siparişi’ göster",
           "show_forecast_ref": "Taleplerde ‘Onaylı tahmin’ göster",
           "show_cost_center": "Taleplerde ‘Masraf merkezi’ göster",
           "show_delivery_condition": "Taleplerde ‘Teslim koşulu’ göster"}
}


# Consequence of switching each optional request field OFF. Server-rendered on
# the workflow page, so it needs all three languages: an English-only sentence on
# an Arabic page is the untranslated Latin run tests_i18n_adversarial refuses.
SETTING_WARN = {
    "en": {
        "show_expenditure_kind": "Every request is then treated as OPEX. CAPEX routes through a stricter ladder (DOAM 4.2), so hiding this gives capital spend the weaker one.",
        "show_sales_order": "DOAM 3.4 requires a valid sales order OR an agreed forecast on any request that is not maintenance spares. Hide BOTH and those requests are refused at submission — the gate is not switched off by hiding its field.",
        "show_forecast_ref": "The other half of the DOAM 3.4 cost object. Safe to hide on its own if the plant works to sales orders; not safe to hide together with Sales order.",
        "show_cost_center": "The route maintenance and facility spend uses to answer DOAM 3.4 instead of a sales order. Hiding it does not stop spare-part requests, which are recognised from the parts master.",
        "show_delivery_condition": "Prints on the purchase order. Nothing routes on it.",
    },
    "ar": {
        "show_expenditure_kind": "عندئذٍ يُعامَل كل طلب على أنه مصروف تشغيلي. أما المصروف الرأسمالي فيمرّ بسلسلة موافقات أشدّ (البند 4.2)، لذا فإخفاء هذا الحقل يمنح الإنفاق الرأسمالي السلسلة الأضعف.",
        "show_sales_order": "يشترط البند 3.4 وجود أمر بيع صالح أو توقّع معتمد في أي طلب غير خاص بقطع غيار الصيانة. وإخفاء الحقلين معًا يعني رفض هذه الطلبات عند الإرسال — فإخفاء الحقل لا يوقف الضابط.",
        "show_forecast_ref": "النصف الآخر من مرجع التكلفة في البند 3.4. إخفاؤه وحده آمن إذا كان المصنع يعمل بأوامر البيع، لكن إخفاءه مع «أمر البيع» غير آمن.",
        "show_cost_center": "الطريق الذي تستخدمه مصروفات الصيانة والمرافق للإجابة على البند 3.4 بدلاً من أمر البيع. إخفاؤه لا يوقف طلبات قطع الغيار، فهي تُعرَف من دليل قطع الغيار.",
        "show_delivery_condition": "يُطبع على أمر الشراء. ولا يُبنى عليه أي توجيه للموافقات.",
    },
    "tr": {
        "show_expenditure_kind": "O zaman her talep işletme gideri sayılır. Sermaye harcaması daha katı bir onay zincirinden geçer (DOAM 4.2); bunu gizlemek sermaye harcamasına zayıf olanı verir.",
        "show_sales_order": "DOAM 3.4, bakım yedek parçası olmayan her talepte geçerli bir satış siparişi VEYA onaylı bir tahmin ister. İkisini birden gizlerseniz bu talepler gönderimde reddedilir — alanı gizlemek denetimi kapatmaz.",
        "show_forecast_ref": "DOAM 3.4 maliyet nesnesinin diğer yarısı. Fabrika satış siparişleriyle çalışıyorsa tek başına gizlemek güvenlidir; Satış siparişi ile birlikte gizlemek değildir.",
        "show_cost_center": "Bakım ve tesis harcamalarının satış siparişi yerine DOAM 3.4'e cevap verirken kullandığı yol. Gizlemek yedek parça taleplerini durdurmaz; onlar parça ana verisinden tanınır.",
        "show_delivery_condition": "Satın alma emrinde yazdırılır. Hiçbir yönlendirme buna bağlı değildir.",
    },
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
        "supply_chain_director": "مدير سلسلة الإمداد",
        "plant_director": "مدير عام المصنع",
        "financial_director": "المدير المالي",
        "managing_director": "العضو المنتدب",
        "board": "مجلس الإدارة",
        "hr_officer": "مسؤول الموارد البشرية",
        "hr_probation_admin": "مسؤول فترة الاختبار (الموارد البشرية)"
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
        "supply_chain_director": "Tedarik Zinciri Direktörü",
        "plant_director": "Fabrika Direktörü",
        "financial_director": "Mali İşler Direktörü",
        "managing_director": "Murahhas Aza",
        "board": "Yönetim Kurulu",
        "hr_officer": "İK Sorumlusu",
        "hr_probation_admin": "İK Deneme Süresi Yöneticisi"
    }
}

# Small UI strings the trilingual editor itself needs.
UI = {
    "en": {
        "text_en": "Text (English)", "text_ar": "Text (Arabic)",
        "text_tr": "Text (Turkish)",
        "lang_hint": "Each language is saved on its own. Empty a box and that "
                     "language's readers see the English text instead — it stays "
                     "empty. Reset puts all three back to the built-in wording.",
        "no_approve_opt": "No approve permission",
        # --- SoD escalation (chain editor + the stamp on a request) ---
        "esc_title": "Escalation chain — one level up",
        "esc_sub": "Used only when the requester is the only person who could sign a "
                   "rung. The superior signs instead; the deviation is recorded on the "
                   "request. A role with no superior means nobody above it.",
        "esc_role": "Role",
        "esc_superior": "Escalates to",
        "esc_none": "— nobody above —",
        "esc_signs": "Signs",
        "esc_save": "Save chain",
        "esc_self_err": "A role cannot be its own superior.",
        "esc_tag": "Escalated",
        "esc_from_to": "Escalated from %(from)s to %(to)s — the originator holds the "
                       "normal signing role.",
        "esc_stuck": "No superior available — this rung needs a delegation or a "
                     "Procurement admin.",
        "esc_note": "One or more rungs of this request were escalated one level up the "
                    "org chart because the requester is the only person who normally "
                    "signs them. Nobody approves their own request.",
        "esc_blocked_flash": "This request cannot be submitted: you are the only person "
                             "who could sign one of its approval stages, and nobody above "
                             "that role is free to sign it instead. Procurement admins "
                             "have been notified — a delegation, a second holder of the "
                             "role, or an admin override is needed.",
        # --- DOAM §5 golden thread: the sales-order gate ---
        "cost_object_required_flash":
            "This request buys direct materials — fabric, yarn, trims, thread, "
            "labels, packaging, wash or print work — so it must name what it is "
            "costed against (DOAM §3.4): either the client sales order, or an "
            "agreed production forecast. Pick one from the lists on the request "
            "and submit again.",
        "so_unknown_flash":
            "The sales order on this request is not an order on file, so it is not "
            "a cost object (DOAM §5). Pick a real, open sales order from the list "
            "on the request and submit again — or, if this material is being bought "
            "ahead of a confirmed client order, clear the sales order and cite an "
            "agreed production forecast instead.",
        "so_closed_flash":
            "The sales order on this request is closed or cancelled, so nothing can "
            "be costed to it (DOAM §5). Name an open sales order and submit again, "
            "or clear it and cite an agreed production forecast if the material is "
            "being bought ahead of a confirmed order. If this purchase is not a "
            "direct material for a client order at all, remove the sales order and "
            "use the cost centre instead.",
        # --- DOAM §3.4, the clause's other half: "...or agreed forecast" ---
        "fc_unknown_flash":
            "The forecast reference on this request is not in the forecast "
            "register, so it is not a cost object (DOAM §3.4). Record it under "
            "Procurement → Agreed Forecasts and have the Supply Chain Director "
            "agree it, or name an open client sales order instead.",
        "fc_unapproved_flash":
            "The forecast on this request is still a draft. A forecast becomes a "
            "cost object only when it is AGREED — the DOAM puts operational and "
            "inventory replenishment with the Supply Chain Director (Table 4 L2), "
            "so ask for it to be agreed, or name an open client sales order.",
        "fc_expired_flash":
            "The forecast on this request is outside its validity dates, so nothing "
            "can be bought against it (DOAM §3.4). Have a current forecast agreed "
            "for this period, or name an open client sales order instead.",
        "fc_lapsed_flash":
            "This forecast's validity dates have already passed, so agreeing "
            "it would buy nothing: the gate would refuse every request that "
            "cited it. Record a forecast covering the current period instead.",
        "own_forecast_flash":
            "You drafted this forecast, so you may not also agree it. A forecast "
            "is the alternative to a client sales order, so one signature on both "
            "ends of it would let the same person invent a cost object and then "
            "spend against it. Ask another Supply Chain Director or Procurement "
            "admin to agree it.",
        # --- DOAM §4.3 / §7.3.3 / §7.3.4: why a payment was refused ---
        # Same gates the governance page describes in this reader's language;
        # the refusal itself used to arrive in English only.
        "not_payable_flash": "Payments start once the Purchase Order is issued.",
        "match_blocked_flash":
            "Payment blocked: the 3-way match shows over-billing (invoice exceeds "
            "the PO or the received value). Resolve the mismatch first — an "
            "administrator can override.",
        "over_payment_flash":
            "This payment would exceed the PO total. Check the amount — an "
            "administrator can override if intentional.",
        "exceeds_invoiced_flash":
            "This payment would exceed what the supplier has invoiced. Book the "
            "invoice first, or reduce the amount — an administrator can override.",
        "exceeds_received_flash":
            "Short delivery: this payment would exceed the value of the goods "
            "actually received. Pay for what was received, book the rest once it "
            "arrives — or ask an administrator to override.",
        # --- a requisition buying from several suppliers ---
        "po_required_flash":
            "This request buys from several suppliers, so it has one purchase "
            "order per supplier. Choose which order this belongs to.",
        "exceeds_po_flash":
            "This would bill that supplier's purchase order for more than the "
            "order is worth. Check the amount, or book it against the right "
            "supplier's order.",
        "advance_not_authorised_flash":
            "This is an advance payment (nothing invoiced yet). DOAM §4.3 requires "
            "it to be authorised first — record the advance authorisation on this "
            "request.",
        "advance_exceeds_authorised_flash":
            "This payment is larger than the advance that was authorised. "
            "Re-authorise for the higher percentage, or reduce the amount.",
        "advance_guarantee_required_flash":
            "An advance above 25% on an order over 500,000 EGP needs a bank "
            "guarantee reference.",
        "advance_vendor_not_approved_flash":
            "No advance may be paid to a supplier off the approved vendor list "
            "(DOAM §4.3). Add the supplier to the vendor master first."
    },
    "ar": {
        "text_en": "النص (بالإنجليزية)", "text_ar": "النص (بالعربية)",
        "text_tr": "النص (بالتركية)",
        "lang_hint": "كل لغة تُحفَظ على حدة. أفرِغ أي حقل فيرى قرّاء تلك اللغة النص "
                     "الإنجليزي بدلًا منه، ويبقى الحقل فارغًا. و«إعادة التعيين» تُرجع "
                     "اللغات الثلاث إلى الصياغة المدمجة.",
        "no_approve_opt": "لا يحمل صلاحية الموافقة",
        "esc_title": "سلسلة التصعيد — درجة واحدة أعلى",
        "esc_sub": "تُستخدم فقط عندما يكون مُقدّم الطلب هو الشخص الوحيد الذي يمكنه "
                   "التوقيع على إحدى المراحل. فيوقّع الرئيس المباشر بدلًا منه، ويُسجَّل "
                   "هذا الاستثناء على الطلب. والدور الذي لا رئيس مباشر له يعني أنه لا "
                   "يوجد أحد أعلى منه.",
        "esc_role": "الدور",
        "esc_superior": "يُصعَّد إلى",
        "esc_none": "— لا يوجد أحد أعلى —",
        "esc_signs": "يوقّع",
        "esc_save": "حفظ سلسلة التصعيد",
        "esc_self_err": "لا يمكن أن يكون الدور رئيسًا مباشرًا لنفسه.",
        "esc_tag": "تم التصعيد",
        "esc_from_to": "تم التصعيد من %(from)s إلى %(to)s — لأن مُقدّم الطلب هو صاحب "
                       "الدور الموقّع المعتاد.",
        "esc_stuck": "لا يوجد رئيس مباشر متاح — هذه المرحلة تحتاج تفويضًا أو تدخّل "
                     "مسؤول المشتريات.",
        "esc_note": "تم تصعيد مرحلة أو أكثر من مراحل هذا الطلب درجة واحدة أعلى في "
                    "الهيكل التنظيمي، لأن مُقدّم الطلب هو الشخص الوحيد الذي يوقّعها "
                    "عادةً. ولا يوافق أحد على طلبه هو.",
        "esc_blocked_flash": "لا يمكن إرسال هذا الطلب: أنت الشخص الوحيد الذي يمكنه "
                             "التوقيع على إحدى مراحل الموافقة، ولا يوجد أحد أعلى من ذلك "
                             "الدور متفرّغ للتوقيع بدلًا منك. وقد أُبلِغ مسؤولو المشتريات "
                             "— المطلوب تفويض أو إضافة شخص ثانٍ لهذا الدور أو تدخّل من "
                             "مسؤول.",
        "cost_object_required_flash":
            "هذا الطلب يشتري خامات إنتاج مباشرة — أقمشة أو خيوط أو إكسسوارات أو "
            "ليبل أو مواد تغليف أو غسيل أو طباعة — لذلك يجب أن يذكر ما تُحمّل "
            "عليه تكلفته (البند 3-4 من دليل الصلاحيات): إما أمر بيع العميل وإما "
            "توقعات إنتاج معتمدة. اختر أحدهما من القوائم الموجودة في الطلب ثم "
            "أعد الإرسال.",
        "so_unknown_flash":
            "أمر البيع المكتوب في هذا الطلب غير مسجّل في النظام، وبالتالي فهو ليس "
            "مركز تكلفة صالحًا (البند 5 من دليل الصلاحيات). اختر أمر بيع حقيقيًا "
            "ومفتوحًا من القائمة ثم أعد الإرسال — أو امسح أمر البيع واذكر "
            "توقعات إنتاج معتمدة إذا كنت تشتري قبل تأكيد أمر العميل.",
        "so_closed_flash":
            "أمر البيع المذكور في هذا الطلب مقفل أو ملغى، فلا يمكن تحميل أي "
            "تكلفة عليه (البند 5 من دليل الصلاحيات). اذكر أمر بيع مفتوحًا ثم "
            "أعد الإرسال، أو امسحه واذكر توقعات إنتاج معتمدة إذا كان "
            "الشراء قبل تأكيد أمر العميل، أو احذف أمر البيع واستخدم مركز "
            "التكلفة إذا لم يكن هذا الشراء خامة إنتاج مباشرة لأمر عميل.",
        "fc_unknown_flash":
            "مرجع التوقعات المذكور في هذا الطلب غير موجود في سجل التوقعات، "
            "فليس مركز تكلفة (البند 3-4 من دليل الصلاحيات). سجّله في صفحة "
            "التوقعات المعتمدة واطلب اعتماد مدير سلسلة الإمداد له، أو اذكر "
            "أمر بيع عميل مفتوحًا بدلًا منه.",
        "fc_unapproved_flash":
            "التوقعات المذكورة في هذا الطلب ما زالت مسودة. ولا تصبح التوقعات "
            "مركز تكلفة إلا بعد اعتمادها — ويضع الدليل مسؤولية التشغيل "
            "وإعادة تدبير المخزون لدى مدير سلسلة الإمداد (الجدول 4 المستوى "
            "الثاني)، فاطلب اعتمادها أو اذكر أمر بيع عميل مفتوحًا.",
        "fc_expired_flash":
            "التوقعات المذكورة في هذا الطلب خارج مدة سريانها، فلا يمكن الشراء "
            "مقابلها (البند 3-4 من دليل الصلاحيات). اعتمد توقعات سارية لهذه "
            "الفترة، أو اذكر أمر بيع عميل مفتوحًا بدلًا منها.",
        "fc_lapsed_flash":
            "انتهت مدة سريان هذه التوقعات، فاعتمادها لن يتيح أي شراء: سيرفض "
            "النظام كل طلب يستند إليها. سجّل توقعات تغطي الفترة الحالية بدلًا منها.",
        "own_forecast_flash":
            "أنت من سجّل هذه التوقعات، فلا يجوز أن تعتمدها بنفسك. التوقعات بديل "
            "عن أمر بيع العميل، ولو وقّع الشخص نفسه على طرفيها لأصبح بإمكانه أن "
            "ينشئ مركز تكلفة ثم ينفق عليه. اطلب من مدير سلسلة إمداد آخر أو من "
            "مسؤول مشتريات آخر اعتمادها.",
        # --- DOAM §4.3 / §7.3.3 / §7.3.4: why a payment was refused ---
        # Same gates the governance page describes in this reader's language;
        # the refusal itself used to arrive in English only.
        "not_payable_flash": "لا يبدأ السداد إلا بعد إصدار أمر الشراء.",
        "match_blocked_flash":
            "السداد موقوف: المطابقة الثلاثية تُظهر زيادة في الفوترة (الفاتورة تتجاوز "
            "أمر الشراء أو قيمة المستلم). عالج الفرق أولًا — ويمكن لمسؤول النظام "
            "التجاوز.",
        "over_payment_flash":
            "هذا السداد يتجاوز إجمالي أمر الشراء. راجع المبلغ — ويمكن لمسؤول النظام "
            "التجاوز إذا كان مقصودًا.",
        "exceeds_invoiced_flash":
            "هذا السداد يتجاوز ما فوتره المورد. سجّل الفاتورة أولًا أو خفّض المبلغ — "
            "ويمكن لمسؤول النظام التجاوز.",
        "exceeds_received_flash":
            "نقص في التوريد: هذا السداد يتجاوز قيمة البضاعة المستلمة فعلًا. ادفع "
            "مقابل ما تم استلامه وسجّل الباقي عند وصوله — أو اطلب من مسؤول النظام "
            "التجاوز.",
        # --- a requisition buying from several suppliers ---
        "po_required_flash":
            "هذا الطلب يشتري من أكثر من مورد، ولكل مورد أمر شراء خاص به. اختر أمر "
            "الشراء الذي يخص هذه العملية.",
        "exceeds_po_flash":
            "هذا يتجاوز قيمة أمر الشراء الخاص بذلك المورد. راجع المبلغ أو سجّله على "
            "أمر شراء المورد الصحيح.",
        "advance_not_authorised_flash":
            "هذه دفعة مقدّمة (لا توجد فواتير بعد). يشترط البند 4-3 من دليل الصلاحيات "
            "اعتمادها أولًا — سجّل اعتماد الدفعة المقدّمة على هذا الطلب.",
        "advance_exceeds_authorised_flash":
            "هذا السداد أكبر من الدفعة المقدّمة المعتمدة. اعتمد نسبة أعلى أو خفّض "
            "المبلغ.",
        "advance_guarantee_required_flash":
            "الدفعة المقدّمة التي تتجاوز 25% على طلب تزيد قيمته عن 500,000 جنيه تحتاج "
            "إلى مرجع خطاب ضمان بنكي.",
        "advance_vendor_not_approved_flash":
            "لا يجوز دفع أي دفعة مقدّمة لمورد خارج قائمة الموردين المعتمدين (البند "
            "4-3). أضف المورد إلى سجل الموردين أولًا."
    },
    "tr": {
        "text_en": "Metin (İngilizce)", "text_ar": "Metin (Arapça)",
        "text_tr": "Metin (Türkçe)",
        "lang_hint": "Her dil kendi başına kaydedilir. Bir kutuyu boşaltırsanız o "
                     "dilin okuyucuları İngilizce metni görür ve kutu boş kalır. "
                     "Sıfırla, üç dilin hepsini yerleşik metne döndürür.",
        "no_approve_opt": "Onay yetkisi yok",
        "esc_title": "Yükseltme zinciri — bir seviye yukarı",
        "esc_sub": "Yalnızca bir basamağı imzalayabilecek tek kişi talep sahibi "
                   "olduğunda kullanılır. Onun yerine üst yönetici imzalar ve bu sapma "
                   "talebe kaydedilir. Üst yöneticisi olmayan bir rol, üstünde kimse "
                   "olmadığı anlamına gelir.",
        "esc_role": "Rol",
        "esc_superior": "Şuraya yükseltilir",
        "esc_none": "— üstte kimse yok —",
        "esc_signs": "İmzaladığı aşamalar",
        "esc_save": "Zinciri kaydet",
        "esc_self_err": "Bir rol kendisinin üst yöneticisi olamaz.",
        "esc_tag": "Yükseltildi",
        "esc_from_to": "%(from)s aşamasından %(to)s aşamasına yükseltildi — normal imza "
                       "rolü talep sahibinin kendisinde.",
        "esc_stuck": "Uygun bir üst yönetici yok — bu basamak bir vekâlet ya da Satın "
                     "Alma yöneticisi müdahalesi gerektiriyor.",
        "esc_note": "Bu talebin bir veya daha fazla basamağı, normalde onları imzalayan "
                    "tek kişi talep sahibinin kendisi olduğu için organizasyon "
                    "şemasında bir seviye yukarı yükseltildi. Hiç kimse kendi talebini "
                    "onaylamaz.",
        "esc_blocked_flash": "Bu talep gönderilemez: onay aşamalarından birini "
                             "imzalayabilecek tek kişi sizsiniz ve o rolün üstünde onun "
                             "yerine imzalayabilecek uygun kimse yok. Satın Alma "
                             "yöneticileri bilgilendirildi — bir vekâlet, rolü taşıyan "
                             "ikinci bir kişi ya da yönetici müdahalesi gerekiyor.",
        "cost_object_required_flash":
            "Bu talep doğrudan üretim malzemesi satın alıyor — kumaş, iplik, "
            "aksesuar, dikiş ipliği, etiket, ambalaj, yıkama veya baskı — bu yüzden "
            "neye maliyetlendirildiğini belirtmesi gerekir (DOAM §3.4): ya müşteri "
            "satış siparişi ya da onaylı bir üretim tahmini. Talepteki listelerden "
            "birini seçip yeniden gönderin.",
        "so_unknown_flash":
            "Bu talepteki satış siparişi sistemde kayıtlı bir sipariş değil, "
            "dolayısıyla geçerli bir maliyet nesnesi sayılmaz (DOAM §5). Talepteki "
            "listeden gerçek ve açık bir satış siparişi seçip yeniden gönderin — ya "
            "da malzeme kesinleşmiş bir müşteri siparişinden önce alınıyorsa satış "
            "siparişini boşaltıp onaylı bir üretim tahmini belirtin.",
        "so_closed_flash":
            "Bu talepteki satış siparişi kapatılmış ya da iptal edilmiş; ona hiçbir "
            "maliyet yüklenemez (DOAM §5). Açık bir satış siparişi belirtip yeniden "
            "gönderin, ya da alım kesinleşmiş bir siparişten önce yapılıyorsa onaylı "
            "bir üretim tahmini belirtin. Bu alım bir müşteri siparişine ait "
            "doğrudan üretim malzemesi değilse satış siparişini kaldırıp masraf "
            "merkezini kullanın.",
        "fc_unknown_flash":
            "Bu talepteki tahmin referansı tahmin kaydında yok, dolayısıyla bir "
            "maliyet nesnesi değil (DOAM §3.4). Onu Onaylı Tahminler sayfasında "
            "kaydedip Tedarik Zinciri Direktörüne onaylatın ya da açık bir müşteri "
            "satış siparişi belirtin.",
        "fc_unapproved_flash":
            "Bu talepteki tahmin hâlâ taslak. Bir tahmin ancak ONAYLANDIĞINDA maliyet "
            "nesnesi olur — DOAM, operasyonel ve stok ikmal sorumluluğunu Tedarik "
            "Zinciri Direktörüne verir (Tablo 4 L2). Onaylanmasını isteyin ya da "
            "açık bir müşteri satış siparişi belirtin.",
        "fc_expired_flash":
            "Bu talepteki tahmin geçerlilik tarihlerinin dışında; ona dayanılarak "
            "hiçbir şey satın alınamaz (DOAM §3.4). Bu dönem için güncel bir tahmin "
            "onaylatın ya da açık bir müşteri satış siparişi belirtin.",
        "fc_lapsed_flash":
            "Bu tahminin geçerlilik tarihleri geçmiş; onaylamak hiçbir şey satın "
            "aldırmaz: ona dayanan her talep reddedilir. Bunun yerine mevcut dönemi "
            "kapsayan bir tahmin kaydedin.",
        "own_forecast_flash":
            "Bu tahmini siz taslak olarak girdiniz, bu yüzden onu kendiniz "
            "onaylayamazsınız. Tahmin, müşteri satış siparişinin alternatifidir; "
            "iki ucunda da aynı imza olursa aynı kişi hem maliyet nesnesini "
            "yaratmış hem de ona harcama yapmış olur. Onayı başka bir Tedarik "
            "Zinciri Direktöründen ya da Satın Alma yöneticisinden isteyin.",
        # --- DOAM §4.3 / §7.3.3 / §7.3.4: why a payment was refused ---
        # Same gates the governance page describes in this reader's language;
        # the refusal itself used to arrive in English only.
        "not_payable_flash":
            "Ödemeler ancak Satın Alma Siparişi düzenlendikten sonra başlar.",
        "match_blocked_flash":
            "Ödeme engellendi: 3'lü mutabakat fazla faturalama gösteriyor (fatura, "
            "siparişi veya teslim alınan değeri aşıyor). Önce farkı giderin — bir "
            "yönetici geçersiz kılabilir.",
        "over_payment_flash":
            "Bu ödeme sipariş toplamını aşıyor. Tutarı kontrol edin — kasıtlıysa bir "
            "yönetici geçersiz kılabilir.",
        "exceeds_invoiced_flash":
            "Bu ödeme, tedarikçinin faturaladığı tutarı aşıyor. Önce faturayı "
            "kaydedin ya da tutarı düşürün — bir yönetici geçersiz kılabilir.",
        "exceeds_received_flash":
            "Eksik teslimat: bu ödeme fiilen teslim alınan malın değerini aşıyor. "
            "Teslim alınan kadarını ödeyin, kalanı geldiğinde kaydedin — ya da bir "
            "yöneticiden geçersiz kılmasını isteyin.",
        # --- a requisition buying from several suppliers ---
        "po_required_flash":
            "Bu talep birden fazla tedarikçiden alım yapıyor ve her tedarikçinin "
            "kendi siparişi var. Bunun hangi siparişe ait olduğunu seçin.",
        "exceeds_po_flash":
            "Bu, o tedarikçinin siparişini sipariş değerinin üzerinde faturalar. "
            "Tutarı kontrol edin ya da doğru tedarikçinin siparişine kaydedin.",
        "advance_not_authorised_flash":
            "Bu bir avans ödemesidir (henüz fatura yok). DOAM §4.3 önce "
            "yetkilendirilmesini şart koşar — avans yetkisini bu talebe kaydedin.",
        "advance_exceeds_authorised_flash":
            "Bu ödeme, yetkilendirilen avanstan büyük. Daha yüksek yüzde için "
            "yeniden yetki alın ya da tutarı düşürün.",
        "advance_guarantee_required_flash":
            "500.000 EGP üzerindeki bir siparişte %25'i aşan avans için banka "
            "teminat mektubu referansı gerekir.",
        "advance_vendor_not_approved_flash":
            "Onaylı tedarikçi listesi dışındaki bir tedarikçiye avans ödenemez "
            "(DOAM §4.3). Önce tedarikçiyi tedarikçi ana kaydına ekleyin."
    }
}
