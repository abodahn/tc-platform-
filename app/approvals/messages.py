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
    "A justification is required to waive competitive quotes.": {
        "ar": "يلزم تسجيل مبرّر للاستغناء عن عروض الأسعار التنافسية.",
        "tr": "Rekabetçi tekliflerden vazgeçmek için bir gerekçe girilmesi zorunludur.",
    },
    "A rejected quantity needs a reason.": {
        "ar": "الكمية المرفوضة تحتاج إلى تسجيل سبب الرفض.",
        "tr": "Reddedilen miktar için bir gerekçe girilmelidir.",
    },
    "Above 10,000,000 EGP the DOAM requires a written business case alongside Board approval. Record it before signing.": {
        "ar": "فوق 10,000,000 EGP يشترط الـ DOAM إرفاق دراسة جدوى مكتوبة إلى جانب موافقة مجلس الإدارة. سجّلها قبل التوقيع.",
        "tr": "10.000.000 EGP üzerinde DOAM, Yönetim Kurulu onayının yanı sıra yazılı bir iş gerekçesi ister. İmzalamadan önce kaydedin.",
    },
    "Added. %d request line(s) now point at it.": {
        "ar": "تمت الإضافة. %d من بنود الطلب أصبحت مرتبطة به الآن.",
        "tr": "Eklendi. %d talep satırı artık buna bağlı.",
    },
    "Advance authorised.": {
        "ar": "تمت الموافقة على الدفعة المقدمة.",
        "tr": "Avans onaylandı.",
    },
    "An advance above 25% on an order over 500,000 EGP needs a bank guarantee reference.": {
        "ar": "الدفعة المقدمة التي تتجاوز 25% في أمر شراء تزيد قيمته عن 500,000 EGP تتطلب رقم خطاب ضمان بنكي.",
        "tr": "500.000 EGP üzerindeki bir siparişte %25'i aşan avans için banka teminat mektubu referansı gerekir.",
    },
    "An advance of this size needs a higher authority: up to 25% the Financial Director, above that the CFO or the Managing Director.": {
        "ar": "دفعة مقدمة بهذا الحجم تحتاج صلاحية أعلى: حتى 25% المدير المالي، وما فوق ذلك الـ CFO أو العضو المنتدب.",
        "tr": "Bu büyüklükteki bir avans daha üst bir yetki gerektirir: %25'e kadar Mali Direktör, üzerinde CFO veya Murahhas Aza.",
    },
    "An item needs a name.": {
        "ar": "الصنف يحتاج إلى اسم.",
        "tr": "Malzemenin bir adı olmalıdır.",
    },
    "An item needs the code the ERP issued for it.": {
        "ar": "الصنف يحتاج إلى الكود الصادر له من نظام الـ ERP.",
        "tr": "Malzeme için ERP tarafından verilen kod girilmelidir.",
    },
    "Another item already carries that code.": {
        "ar": "هذا الكود مستخدم بالفعل لصنف آخر.",
        "tr": "Bu kod başka bir malzemede kullanılıyor.",
    },
    "Approved & signed. Waiting on the co-approver at this stage.": {
        "ar": "تمت الموافقة والتوقيع. في انتظار المعتمد المشارك في هذه المرحلة.",
        "tr": "Onaylandı ve imzalandı. Bu aşamadaki ikinci onaycı bekleniyor.",
    },
    "Approved — routed to the next approver.": {
        "ar": "تمت الموافقة — أُحيل الطلب إلى المعتمد التالي.",
        "tr": "Onaylandı — bir sonraki onaycıya iletildi.",
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
    "Competitive quotes required — attach at least 2 vendor quotes for this order value, or record a single-source justification in the Vendor quotes panel.": {
        "ar": "عروض الأسعار التنافسية مطلوبة — أرفق عرضَي سعر على الأقل من مورّدين لهذه القيمة، أو سجّل مبرّر التوريد من مصدر واحد في لوحة «عروض أسعار المورّدين».",
        "tr": "Rekabetçi teklif zorunlu — bu sipariş tutarı için en az 2 tedarikçi teklifi ekleyin ya da «Tedarikçi teklifleri» panelinde tek kaynak gerekçesini kaydedin.",
    },
    "Could not add delegation (": {
        "ar": "تعذّر إضافة التفويض (",
        "tr": "Yetki devri eklenemedi (",
    },
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
        "ar": "الإدارة والمبلغ حقلان مطلوبان.",
        "tr": "Departman ve tutar zorunludur.",
    },
    "Draft saved.": {
        "ar": "تم حفظ المسودة.",
        "tr": "Taslak kaydedildi.",
    },
    "Email logged (set the SMTP env vars to actually send).": {
        "ar": "تم تسجيل رسالة البريد الإلكتروني (اضبط متغيّرات بيئة SMTP كي تُرسل فعليًا).",
        "tr": "E-posta kaydedildi (gerçekten gönderilmesi için SMTP ortam değişkenlerini ayarlayın).",
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
        "ar": "أدخل كمية مقبولة أو كمية مرفوضة في سطر واحد على الأقل.",
        "tr": "En az bir satıra kabul edilen veya reddedilen bir miktar girin.",
    },
    "Enter the advance as a percentage of the PO value (1–100).": {
        "ar": "أدخل الدفعة المقدّمة كنسبة مئوية من قيمة أمر الشراء PO (1–100).",
        "tr": "Avansı, PO değerinin yüzdesi olarak girin (1–100).",
    },
    "Enter the pricing before approving the Purchasing stage — the request has no commercial value yet.": {
        "ar": "أدخل التسعير قبل اعتماد مرحلة المشتريات — فالطلب ليست له قيمة مالية حتى الآن.",
        "tr": "Satın alma aşamasını onaylamadan önce fiyatlandırmayı girin — talebin henüz ticari bir değeri yok.",
    },
    "Enter the reason for the revision — it is kept in the PO history.": {
        "ar": "أدخل سبب التعديل — فهو يُحفظ في سجل أمر الشراء PO.",
        "tr": "Revizyon gerekçesini girin — PO geçmişinde saklanır.",
    },
    "Escalation chain saved (": {
        "ar": "تم حفظ مسار التصعيد (",
        "tr": "Eskalasyon zinciri kaydedildi (",
    },
    "FX rate saved — approval thresholds now route on the EGP equivalent.": {
        "ar": "تم حفظ سعر الصرف FX — أصبحت حدود الموافقة تُحتسب على أساس المقابل بالـ EGP.",
        "tr": "FX kuru kaydedildi — onay limitleri artık EGP karşılığı üzerinden yönlendiriliyor.",
    },
    "File too large (max 3 MB).": {
        "ar": "حجم الملف كبير جدًا (الحد الأقصى 3 ميجابايت).",
        "tr": "Dosya çok büyük (en fazla 3 MB).",
    },
    "Final approval complete. Purchase Order drafted.": {
        "ar": "اكتملت الموافقة النهائية. وتم إنشاء مسودة أمر الشراء.",
        "tr": "Nihai onay tamamlandı. Satın alma emri taslağı oluşturuldu.",
    },
    "Foreign-currency request: enter the EGP exchange rate before pricing, so the value approvals route on the true EGP equivalent.": {
        "ar": "طلب بعملة أجنبية: أدخل سعر الصرف مقابل الـ EGP قبل التسعير، حتى تسير موافقات القيمة على المقابل الحقيقي بالـ EGP.",
        "tr": "Yabancı para birimli talep: fiyatlandırmadan önce EGP kurunu girin; böylece tutar onayları gerçek EGP karşılığı üzerinden yönlendirilir.",
    },
    "Give a reason so the requester knows what to do next.": {
        "ar": "اذكر السبب حتى يعرف مُقدّم الطلب ما الذي يفعله بعد ذلك.",
        "tr": "Talebi açan kişinin bundan sonra ne yapacağını bilmesi için bir gerekçe yazın.",
    },
    "Goods were already received against this order — close it instead of cancelling.": {
        "ar": "سبق استلام أصناف على هذا الأمر — أغلقه بدلًا من إلغائه.",
        "tr": "Bu emir üzerinden mal girişi yapılmış — iptal etmek yerine emri kapatın.",
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
        "ar": "رقم الفاتورة والمبلغ حقلان مطلوبان.",
        "tr": "Fatura numarası ve tutar zorunludur.",
    },
    "Invoice recorded and matched.": {
        "ar": "تم تسجيل الفاتورة ومطابقتها.",
        "tr": "Fatura kaydedildi ve eşleştirildi.",
    },
    "Issued from stock. The request continues for what is left to buy.": {
        "ar": "تم الصرف من المخزن. ويستمر الطلب للكمية المتبقّية للشراء.",
        "tr": "Stoktan çıkış yapıldı. Talep, satın alınacak kalan miktar için devam ediyor.",
    },
    "Issued from stock. The whole request was met off the shelf, so it is closed and nothing will be bought.": {
        "ar": "تم الصرف من المخزن. تمت تلبية الطلب بالكامل من الرصيد المتاح، لذلك أُغلق الطلب ولن يتم شراء أي شيء.",
        "tr": "Stoktan çıkış yapıldı. Talebin tamamı eldeki stoktan karşılandı; bu nedenle talep kapatıldı ve satın alma yapılmayacak.",
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
        "ar": "تم الربط. أصبح %d من بنود الطلب مرتبطًا به الآن.",
        "tr": "Bağlandı. Talebin %d satırı artık buna işaret ediyor.",
    },
    "No active approval step.": {
        "ar": "لا توجد خطوة موافقة نشطة.",
        "tr": "Aktif onay adımı yok.",
    },
    "No catalogue item holds that code.": {
        "ar": "لا يوجد صنف في الكتالوج بهذا الكود.",
        "tr": "Bu kodu taşıyan bir katalog kalemi yok.",
    },
    "No email on file for this vendor — add one in Vendors.": {
        "ar": "لا يوجد بريد إلكتروني مسجَّل لهذا المورّد — أضِف بريدًا من شاشة «الموردين».",
        "tr": "Bu tedarikçi için kayıtlı e-posta yok — «Tedarikçiler» ekranından bir e-posta ekleyin.",
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
        "ar": "لم يتم صرف أي شيء — اختر أولًا صنفًا من المخزن وكمية على أحد البنود.",
        "tr": "Hiçbir stok çıkışı yapılmadı — önce bir satırda stok kalemi ve miktar seçin.",
    },
    "Only an issued (or partially received) Purchase Order can be revised.": {
        "ar": "لا يمكن تعديل أمر الشراء إلا إذا كان صادرًا (أو مستلمًا جزئيًا).",
        "tr": "Yalnızca gönderilmiş (veya kısmen teslim alınmış) bir satın alma emri revize edilebilir.",
    },
    "Only draft or rejected requests can be edited.": {
        "ar": "لا يمكن تعديل سوى الطلبات التي في حالة مسودة أو مرفوضة.",
        "tr": "Yalnızca taslak veya reddedilmiş talepler düzenlenebilir.",
    },
    "Only the requester (or Purchasing/an admin) can cancel this request.": {
        "ar": "لا يستطيع إلغاء هذا الطلب سوى مُقدِّم الطلب (أو المشتريات/مسؤول النظام).",
        "tr": "Bu talebi yalnızca talebi açan kişi (ya da Satın Alma/bir yönetici) iptal edebilir.",
    },
    "Only the spare store can be issued from here. Materials are issued against a production order, not a purchase request.": {
        "ar": "لا يمكن الصرف من هنا إلا من مخزن قطع الغيار. أما الخامات فتُصرف على أمر إنتاج، وليس على طلب شراء.",
        "tr": "Buradan yalnızca yedek parça deposundan çıkış yapılabilir. Malzemeler satın alma talebine karşılık değil, üretim emrine karşılık çıkılır.",
    },
    "Only the warehouse rung or a procurement admin can reverse an issue.": {
        "ar": "لا يمكن عكس عملية الصرف إلا من مستوى المخزن أو من مسؤول المشتريات.",
        "tr": "Bir stok çıkışını yalnızca depo kademesi veya bir satın alma yöneticisi geri alabilir.",
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
        "ar": "تم فتح مراجعة لأمر الشراء PO — أصبح الأمر الآن يحمل المراجعة Rev",
        "tr": "PO revizyonu açıldı — emir artık şu revizyonu taşıyor: Rev",
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
        "ar": "تم حفظ التسعير — أصبح الطلب يحمل قيمته المالية، وانضمت إلى مسار الاعتماد أي موافقات مرتبطة بالقيمة.",
        "tr": "Fiyatlandırma kaydedildi — talep artık ticari değerini taşıyor ve değere bağlı onaylar onay basamaklarına eklendi.",
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
        "ar": "تمت الإعادة، وعاد الطلب إلى دورته من جديد — كان قد أُغلق لأن الرصيد غطّاه.",
        "tr": "Geri alındı ve talep yeniden akışa girdi — stok karşıladığı için kapatılmıştı.",
    },
    "Put back. The stock is on the shelf again and the line asks for the full quantity.": {
        "ar": "تمت الإعادة. عادت الكمية إلى رصيد المخزن، وأصبح البند يطلب الكمية كاملة من جديد.",
        "tr": "Geri alındı. Stok yeniden depoda ve satır tüm miktarı talep ediyor.",
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
    "Quote selected.": {
        "ar": "تم اختيار عرض السعر.",
        "tr": "Teklif seçildi.",
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
        "ar": "تم التسجيل، وأُبلغ من قاموا بإدخاله.",
        "tr": "Kaydedildi. Girişi yapan kişiler bilgilendirildi.",
    },
    "Rejected goods returned to the supplier under a debit note.": {
        "ar": "تم إرجاع الأصناف المرفوضة إلى المورّد بموجب إشعار مدين.",
        "tr": "Reddedilen mallar borç dekontu ile tedarikçiye iade edildi.",
    },
    "Renamed to “": {
        "ar": "تمت إعادة التسمية إلى “",
        "tr": "Şu adla değiştirildi: “",
    },
    "Request cancelled.": {
        "ar": "تم إلغاء الطلب.",
        "tr": "Talep iptal edildi.",
    },
    "Request closed.": {
        "ar": "تم إقفال الطلب.",
        "tr": "Talep kapatıldı.",
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
        "ar": "تم رفض الطلب، ولم يطرأ أي تغيير على بند طلب الشراء.",
        "tr": "Talep reddedildi. Satın alma talebi satırında hiçbir değişiklik yapılmadı.",
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
        "ar": "تم إيقافه. وسيظل ظاهرًا على كل مستند سبق أن استخدمه.",
        "tr": "Kullanımdan kaldırıldı. Daha önce kullanan tüm belgelerde görünmeye devam eder.",
    },
    "Saved and submitted for approval.": {
        "ar": "تم الحفظ والإرسال للموافقة.",
        "tr": "Kaydedildi ve onaya gönderildi.",
    },
    "Saved. Purchasing have been told.": {
        "ar": "تم الحفظ، وتم إبلاغ إدارة المشتريات.",
        "tr": "Kaydedildi. Satın alma birimi bilgilendirildi.",
    },
    "Saved. The requester has been told what changed.": {
        "ar": "تم الحفظ، وأُبلغ مُقدّم الطلب بما تم تغييره.",
        "tr": "Kaydedildi. Talep sahibine neyin değiştiği bildirildi.",
    },
    "Say why, and which code to use instead.": {
        "ar": "اذكر السبب، والكود الذي يُستخدم بدلًا منه.",
        "tr": "Nedenini ve bunun yerine hangi kodun kullanılacağını yazın.",
    },
    "Similar items were already on file — check it is not the same part under another name.": {
        "ar": "توجد أصناف مشابهة مسجّلة بالفعل — تأكّد أنه ليس الصنف نفسه باسم آخر.",
        "tr": "Benzer kalemler zaten kayıtlı — aynı parçanın başka bir adla girilmediğinden emin olun.",
    },
    "Single-source justification saved — competitive quotes waived.": {
        "ar": "تم حفظ مبرر التوريد من مصدر وحيد — وتم الإعفاء من عروض الأسعار التنافسية.",
        "tr": "Tek kaynak gerekçesi kaydedildi — rekabetçi tekliflerden vazgeçildi.",
    },
    "Sourcing is locked — this request is already approved or closed.": {
        "ar": "التوريد مُقفل — هذا الطلب معتمد أو مُقفل بالفعل.",
        "tr": "Tedarik kilitli — bu talep zaten onaylanmış veya kapatılmış.",
    },
    "Sourcing is locked — this request is already approved, ordered, closed or cancelled.": {
        "ar": "التوريد مُقفل — هذا الطلب معتمد أو صدر به أمر شراء أو مُقفل أو ملغى بالفعل.",
        "tr": "Tedarik kilitli — bu talep zaten onaylanmış, siparişe dönüşmüş, kapatılmış veya iptal edilmiş.",
    },
    "Submitted for approval.": {
        "ar": "تم الإرسال للموافقة.",
        "tr": "Onaya gönderildi.",
    },
    "That code is already in the catalogue — search for it instead of adding it twice.": {
        "ar": "هذا الكود موجود بالفعل في الكتالوج — ابحث عنه بدلًا من إضافته مرتين.",
        "tr": "Bu kod katalogda zaten var — ikinci kez eklemek yerine arayın.",
    },
    "That code is already in the catalogue.": {
        "ar": "هذا الكود موجود بالفعل في الكتالوج.",
        "tr": "Bu kod katalogda zaten var.",
    },
    "That debit note is already settled.": {
        "ar": "هذا الإشعار المدين تمت تسويته بالفعل.",
        "tr": "Bu borç dekontu zaten kapatılmış.",
    },
    "That engineering justification no longer exists.": {
        "ar": "لم يعد هذا المبرر الهندسي موجودًا.",
        "tr": "Bu mühendislik gerekçesi artık mevcut değil.",
    },
    "That is more than the free stock on that item (free = on hand minus what another order is already promised).": {
        "ar": "الكمية أكبر من الرصيد المتاح لهذا الصنف (المتاح = الرصيد ناقص ما هو محجوز لأمر آخر).",
        "tr": "Bu miktar, kalemin serbest stokundan fazla (serbest = elde bulunan eksi başka bir siparişe ayrılmış olan).",
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
        "ar": "تم البتّ في سجل الحجر هذا بالفعل.",
        "tr": "Bu karantina kaydı hakkında zaten karar verilmiş.",
    },
    "That report is not signed yet. Engineering must approve it before Procurement can accept the request.": {
        "ar": "هذا التقرير غير موقّع بعد. يجب أن تعتمده الإدارة الهندسية قبل أن تتمكن المشتريات من قبول الطلب.",
        "tr": "Bu rapor henüz imzalanmadı. Satın almanın talebi kabul edebilmesi için önce mühendisliğin onaylaması gerekir.",
    },
    "That request has already been decided.": {
        "ar": "تم البتّ في هذا الطلب بالفعل.",
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
        "ar": "هذه العملية ستنزل رصيد الرف إلى ما دون الصفر.",
        "tr": "Bu işlem raf stoğunu sıfırın altına düşürür.",
    },
    "The FX rate is locked once the request is approved.": {
        "ar": "يُثبَّت سعر الصرف FX بمجرد اعتماد الطلب.",
        "tr": "Talep onaylandıktan sonra FX kuru sabitlenir.",
    },
    "The PO isn't ready to send yet.": {
        "ar": "أمر الشراء PO غير جاهز للإرسال بعد.",
        "tr": "PO henüz gönderilmeye hazır değil.",
    },
    "The department budget for this period is exceeded — an administrator must issue this PO (or raise the budget).": {
        "ar": "تم تجاوز موازنة الإدارة لهذه الفترة — يجب أن يصدر أمر الشراء PO أحد المسؤولين (أو تُرفع الموازنة).",
        "tr": "Bu dönem için departman bütçesi aşıldı — bu PO'yu bir yönetici düzenlemeli (ya da bütçe artırılmalı).",
    },
    "The request is not on the warehouse stage.": {
        "ar": "الطلب ليس في مرحلة المخزن.",
        "tr": "Talep depo aşamasında değil.",
    },
    "There is no Purchase Order yet to advance against.": {
        "ar": "لا يوجد أمر شراء بعد يمكن صرف دفعة مقدّمة على أساسه.",
        "tr": "Henüz üzerine avans verilebilecek bir satın alma emri yok.",
    },
    "This order is off plan (over target price, over the stock ceiling, or above the net requirement after inventory netting — §3.4). DOAM §4.4 requires a written justification memo before it is signed — record it in the Coverage check / Deviation from plan panel.": {
        "ar": "هذا الأمر خارج الخطة (أعلى من السعر المستهدف، أو يتجاوز سقف المخزون، أو أكبر من الاحتياج الصافي بعد خصم الرصيد — §3.4). يشترط DOAM §4.4 مذكّرة تبرير مكتوبة قبل التوقيع — سجّلها في لوحة «فحص التغطية / الانحراف عن الخطة».",
        "tr": "Bu sipariş plan dışı (hedef fiyatın üzerinde, stok tavanının üzerinde ya da stok mahsuplaşması sonrası net ihtiyacın üzerinde — §3.4). DOAM §4.4 uyarınca imzalanmadan önce yazılı bir gerekçe notu gerekir — bunu «Karşılama kontrolü / Plandan sapma» panelinde kaydedin.",
    },
    "This record has already been retired.": {
        "ar": "هذا السجل مُستبعَد من الاستخدام بالفعل.",
        "tr": "Bu kayıt zaten kullanımdan kaldırılmış.",
    },
    "This record is still inside its retention period — it must be kept until": {
        "ar": "هذا السجل ما زال ضمن مدة الحفظ — يجب الاحتفاظ به حتى",
        "tr": "Bu kayıt hâlâ saklama süresi içinde — saklanması gereken tarih:",
    },
    "This request already carries prices — issuing now would move a total people have signed.": {
        "ar": "هذا الطلب مسعّر بالفعل — الصرف الآن سيغيّر إجماليًا سبق أن وقّع عليه المعتمدون.",
        "tr": "Bu talep zaten fiyatlandırılmış — şimdi çıkış yapmak, imzalanmış bir toplamı değiştirir.",
    },
    "This request already carries prices, so a quantity cannot be changed here — it would move a total that people have already signed. Reject it back instead.": {
        "ar": "هذا الطلب مسعّر بالفعل، فلا يمكن تعديل الكمية هنا — التعديل سيغيّر إجماليًا سبق توقيعه. أعِد الطلب بالرفض بدلًا من ذلك.",
        "tr": "Bu talep zaten fiyatlandırılmış, bu yüzden burada miktar değiştirilemez — imzalanmış bir toplamı değiştirir. Bunun yerine talebi reddedip geri gönderin.",
    },
    "This request already carries prices. Putting stock back now would restore a quantity people have priced against.": {
        "ar": "هذا الطلب مسعّر بالفعل. إرجاع الكمية إلى المخزن الآن سيعيد كمية جرى التسعير على أساسها.",
        "tr": "Bu talep zaten fiyatlandırılmış. Şimdi stoğu geri almak, üzerinden fiyat verilmiş bir miktarı geri getirir.",
    },
    "This request can no longer be priced.": {
        "ar": "لم يعد بالإمكان تسعير هذا الطلب.",
        "tr": "Bu talep artık fiyatlandırılamaz.",
    },
    "This request has moved on — it can no longer be unwound from here.": {
        "ar": "تجاوز هذا الطلب هذه المرحلة — لم يعد بالإمكان التراجع عنه من هنا.",
        "tr": "Bu talep sonraki aşamaya geçti — artık buradan geri alınamaz.",
    },
    "This request is already approved — only an administrator can cancel it.": {
        "ar": "هذا الطلب معتمد بالفعل — لا يمكن إلغاؤه إلا بواسطة أحد المسؤولين.",
        "tr": "Bu talep zaten onaylandı — yalnızca bir yönetici iptal edebilir.",
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
        "ar": "هذا المورّد غير مدرج في قائمة الموردين المعتمدين، فلا يجوز اعتماد دفعة مقدّمة له (DOAM §4.3).",
        "tr": "Bu tedarikçi onaylı tedarikçi listesinde değil, bu nedenle avans onaylanamaz (DOAM §4.3).",
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
        "ar": "اكتب المبرر الفعلي — كلمات قليلة لا تفي بمتطلب التوثيق في DOAM.",
        "tr": "Gerçek gerekçeyi yazın — birkaç kelime, DOAM'ın belge şartını karşılamaz.",
    },
    "You already signed another stage of this request — a different approver must take this one.": {
        "ar": "سبق أن وقّعت على مرحلة أخرى من هذا الطلب — يجب أن يتولى هذه المرحلة معتمد آخر.",
        "tr": "Bu talebin başka bir aşamasını zaten imzaladınız — bu aşamayı farklı bir onaylayıcı üstlenmeli.",
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
        "ar": "لا يمكنك اعتماد طلبك بنفسك — تقديمك للطلب هو توقيعك عليه.",
        "tr": "Kendi talebinizi onaylayamazsınız — talebi açmanız zaten sizin imzanızdır.",
    },
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
        if s.startswith(src[:40]) and len(src) >= 20:
            tail = s[len(src):] if s.startswith(src) else ""
            return (MESSAGES[src].get(lang) or s) + tail
    return text
