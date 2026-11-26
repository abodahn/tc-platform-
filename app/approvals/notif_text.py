# -*- coding: utf-8 -*-
"""
Procurement bell notifications in the READER's language.

notify_users() stores one English row per person; the stored rows are never
rewritten. This translates them on the way out (bell, live feed, /my-work), the
same way messages.py translates flashes: keyed by the English title / body
PATTERN, with the variable parts (request numbers, names, reasons) carried over
as they are. A row that matches no pattern is shown as stored.
"""
import re

# (English pattern, Arabic, Turkish). Groups are carried over as \g<n>.
TITLES = [
    (r"Approval waiting (\d+) days", r"موافقة مستنية من \g<1> يوم", r"Onay \g<1> gündür bekliyor"),
    (r"New item requested", "طلب صنف جديد", "Yeni ürün talep edildi"),
    (r"New item approved", "تم اعتماد الصنف الجديد", "Yeni ürün onaylandı"),
    (r"New item not added", "الصنف الجديد ماتضافش", "Yeni ürün eklenmedi"),
    (r"Item request decided", "تم البت في طلب الصنف", "Ürün talebi karara bağlandı"),
    (r"New request to sign", "طلب جديد محتاج توقيعك", "İmzalanacak yeni talep"),
    (r"Approval rung has no signer", "مرحلة موافقة مالهاش موقّع", "Onay basamağında imzacı yok"),
    (r"Approval rung needs a signer", "مرحلة موافقة محتاجة موقّع", "Onay basamağı bir imzacı bekliyor"),
    (r"Signed in your place", "تم التوقيع بدلاً منك", "Sizin yerinize imzalandı"),
    (r"Re-approval needed on (.+)", r"مطلوب إعادة الموافقة على \g<1>", r"\g<1> için yeniden onay gerekli"),
    (r"Approval stage cannot be signed", "مرحلة الموافقة مش ممكن تتوقّع", "Onay aşaması imzalanamıyor"),
    (r"Request priced", "تم تسعير الطلب", "Talep fiyatlandırıldı"),
    (r"Sent back: (.+)", r"مرتجع: \g<1>", r"Geri gönderildi: \g<1>"),
    (r"On hold: (.+)", r"معلّق: \g<1>", r"Beklemede: \g<1>"),
    (r"Resumed: (.+)", r"اتستأنف: \g<1>", r"Devam ediyor: \g<1>"),
    (r"Request rejected", "الطلب اترفض", "Talep reddedildi"),
    (r"Request approved", "الطلب اتعتمد", "Talep onaylandı"),
    (r"PO ready to issue", "أمر الشراء جاهز للإصدار", "Sipariş verilmeye hazır"),
    (r"Purchase Order issued", "صدر أمر الشراء", "Satın alma siparişi verildi"),
    (r"PO revised", "تم تعديل أمر الشراء", "Sipariş revize edildi"),
    (r"Over-delivery awaiting decision", "توريد زيادة مستني قرار", "Fazla teslimat karar bekliyor"),
    (r"Over-delivery quarantined", "توريد زيادة اتحجز", "Fazla teslimat karantinada"),
    (r"Surplus delivery rejected", "التوريد الزيادة اترفض", "Fazla teslimat reddedildi"),
    (r"Goods rejected — debit note raised", "بضاعة مرفوضة — اتعمل إشعار خصم",
     "Mal reddedildi — borç dekontu açıldı"),
    (r"Goods rejected on receipt", "البضاعة اترفضت عند الاستلام", "Mal teslim alımda reddedildi"),
    (r"On the way: (.+)", r"في الطريق: \g<1>", r"Yolda: \g<1>"),
    (r"Goods received - record the invoice", "البضاعة وصلت — سجّل الفاتورة",
     "Mal teslim alındı - faturayı kaydedin"),
    (r"Delivery confirmed", "تم تأكيد الاستلام", "Teslimat onaylandı"),
    (r"Partial delivery received", "وصل جزء من الطلبية", "Kısmi teslimat alındı"),
    (r"Receipt not booked: ambiguous material", "الاستلام ماتسجّلش: الخامة مش واضحة",
     "Giriş kaydedilmedi: belirsiz malzeme"),
    (r"3-way match mismatch", "المطابقة الثلاثية مش مظبوطة", "Üçlü eşleşme uyuşmazlığı"),
    (r"Invoice ready for payment", "فاتورة جاهزة للدفع", "Fatura ödemeye hazır"),
    (r"Advance payment authorised", "تم اعتماد الدفعة المقدمة", "Avans ödemesi onaylandı"),
    (r"Ready to close", "جاهز للإقفال", "Kapatmaya hazır"),
    (r"Payment complete", "تم السداد بالكامل", "Ödeme tamamlandı"),
    (r"Request short-closed", "الطلب اتقفل قبل ما التوريد يكمل", "Talep eksik teslimatla kapatıldı"),
    (r"Request cancelled", "الطلب اتلغى", "Talep iptal edildi"),
    (r"You can now sign for (.+)", r"تقدر توقّع دلوقتي بالنيابة عن \g<1>",
     r"Artık \g<1> adına imzalayabilirsiniz"),
    (r"Signing delegated to (.+)", r"تم تفويض التوقيع لـ \g<1>", r"İmza yetkisi \g<1> kişisine devredildi"),
    (r"Delegation revoked", "تم إلغاء التفويض", "Yetki devri iptal edildi"),
    (r"Pricing pending", "التسعير متأخر", "Fiyatlandırma bekliyor"),
    (r"Quotes needed before the next signature", "محتاجين عروض أسعار قبل التوقيع اللي جاي",
     "Sonraki imzadan önce teklif gerekli"),
    (r"Approval overdue \(level 2\)", "الموافقة متأخرة (المستوى 2)", "Onay gecikti (2. seviye)"),
    (r"Approval overdue", "الموافقة متأخرة", "Onay gecikti"),
    (r"Quantity corrected on (.+)", r"تم تصحيح الكمية في \g<1>", r"\g<1> üzerinde miktar düzeltildi"),
    (r"(.+) partly met from stock", r"\g<1> اتصرف جزء منه من المخزن", r"\g<1> kısmen stoktan karşılandı"),
    (r"(.+) met from stock", r"\g<1> اتصرف من المخزن", r"\g<1> stoktan karşılandı"),
    (r"Stock issue reversed on (.+)", r"تم إلغاء صرف المخزن على \g<1>",
     r"\g<1> üzerindeki stok çıkışı geri alındı"),
    (r"New comment on (.+)", r"تعليق جديد على \g<1>", r"\g<1> üzerinde yeni yorum"),
    # catalogue changes (items_admin.py: "Item %s %s")
    (r"Item (\S+) added", r"الصنف \g<1> اتضاف", r"\g<1> ürünü eklendi"),
    (r"Item (\S+) edited", r"الصنف \g<1> اتعدّل", r"\g<1> ürünü düzenlendi"),
    (r"Item (\S+) retired", r"الصنف \g<1> اتوقف", r"\g<1> ürünü kullanımdan kaldırıldı"),
    (r"Item (\S+) restored", r"الصنف \g<1> رجع تاني", r"\g<1> ürünü geri alındı"),
    (r"Item (\S+) changed", r"الصنف \g<1> اتغيّر", r"\g<1> ürünü değişti"),
]

BODIES = [
    (r"(\S+) is fully paid — close the request\.", r"\g<1> اتدفع بالكامل — اقفل الطلب.",
     r"\g<1> tamamen ödendi — talebi kapatın."),
    (r"(\S+) is fully paid\.", r"\g<1> اتدفع بالكامل.", r"\g<1> tamamen ödendi."),
    (r"(\S+) has been priced by Purchasing and is moving through the approval ladder\.",
     r"المشتريات سعّرت \g<1> وهو ماشي في سلسلة الموافقات.",
     r"\g<1> Satın Alma tarafından fiyatlandırıldı ve onay zincirinde ilerliyor."),
    (r"(\S+) is fully approved\. PO (\S+) drafted\.",
     r"\g<1> اتعتمد بالكامل. تم تجهيز أمر الشراء \g<2>.",
     r"\g<1> tamamen onaylandı. \g<2> siparişinin taslağı hazırlandı."),
    (r"(\S+) approved — issue PO (\S+)\.", r"\g<1> اتعتمد — أصدر أمر الشراء \g<2>.",
     r"\g<1> onaylandı — \g<2> siparişini verin."),
    (r"(\S+) issued for (\S+)\.", r"صدر \g<1> للطلب \g<2>.", r"\g<2> için \g<1> verildi."),
    (r"(\S+) fully received \((\S+)\)\.", r"\g<1> اتستلم بالكامل (\g<2>).",
     r"\g<1> tamamen teslim alındı (\g<2>)."),
    (r"(\S+): part of the order arrived \((\S+)\); the rest is still outstanding\.",
     r"\g<1>: جزء من الطلبية وصل (\g<2>)، والباقي لسه.",
     r"\g<1>: siparişin bir kısmı geldi (\g<2>); kalanı hâlâ bekleniyor."),
    (r"(\S+): (?:full|partial) receipt booked \((\S+)\)\. Record the supplier invoice\.",
     r"\g<1>: تم تسجيل الاستلام (\g<2>). سجّل فاتورة المورد.",
     r"\g<1>: teslim alma kaydedildi (\g<2>). Tedarikçi faturasını kaydedin."),
    (r"(\S+) put this on hold: (.*)", r"\g<1> علّق الطلب ده: \g<2>", r"\g<1> bunu beklemeye aldı: \g<2>"),
    (r"(\S+) lifted the hold and the approvals are moving again\.",
     r"\g<1> رفع التعليق والموافقات ماشية تاني.",
     r"\g<1> beklemeyi kaldırdı, onaylar yeniden ilerliyor."),
    (r"The supplier has dispatched this order\.(.*)", r"المورد شحن الطلبية دي.\g<1>",
     r"Tedarikçi bu siparişi sevk etti.\g<1>"),
    (r"The commercial terms changed after you approved, so your approval was withdrawn "
     r"and is needed again\.",
     "الشروط التجارية اتغيّرت بعد موافقتك، فموافقتك اتسحبت ومحتاجينها تاني.",
     "Siz onayladıktan sonra ticari koşullar değişti; onayınız geri çekildi ve yeniden gerekiyor."),
    (r"(\S+) has waited (\d+)h without commercial pricing — enter the value so it can move on\.",
     r"\g<1> مستني \g<2> ساعة من غير تسعير — اكتب القيمة عشان يكمّل.",
     r"\g<1> \g<2> saattir fiyatlandırılmadan bekliyor — ilerlemesi için tutarı girin."),
    # services._quote_wait_body — a rung past Purchasing waiting on quotes
    (r"(\S+) cannot be signed at (.+?) until it has a quotation with the supplier's file\. "
     r"Add it or record a single-source justification\.",
     r"\g<1> مش هيتوقّع عند \g<2> غير لما يبقى فيه عرض سعر بملف المورد. "
     r"ضيفه أو سجّل مبرر المورد الوحيد.",
     r"\g<1>, \g<2> aşamasında imzalanamaz: tedarikçi dosyasıyla bir teklif gerekli. "
     r"Ekleyin veya tek kaynak gerekçesi kaydedin."),
    (r"(\S+) cannot be signed at (.+?) until it has (\d+) quotes from different suppliers, "
     r"each with its file \(it has (\d+)\)\. Add them or record a single-source justification\.",
     r"\g<1> مش هيتوقّع عند \g<2> غير لما يبقى فيه \g<3> عروض أسعار من موردين مختلفين، "
     r"كل عرض بملفه (موجود \g<4>). ضيفهم أو سجّل مبرر المورد الوحيد.",
     r"\g<1>, \g<2> aşamasında imzalanamaz: her biri dosyasıyla farklı tedarikçilerden "
     r"\g<3> teklif gerekli (\g<4> var). Ekleyin veya tek kaynak gerekçesi kaydedin."),
    (r"(\S+) cannot be signed at (.+?): a quotation on file has no supplier document\. "
     r"Attach the file to each quotation\.",
     r"\g<1> مش هيتوقّع عند \g<2>: فيه عرض سعر متسجّل من غير مستند المورد. ارفع الملف لكل عرض.",
     r"\g<1>, \g<2> aşamasında imzalanamaz: kayıtlı bir teklifin tedarikçi belgesi yok. "
     r"Her teklife dosyasını ekleyin."),
    (r"(\S+) cannot be signed at (.+?): the chosen quotation has no supplier file\. Attach it\.",
     r"\g<1> مش هيتوقّع عند \g<2>: العرض المختار مالوش ملف من المورد. ارفعه.",
     r"\g<1>, \g<2> aşamasında imzalanamaz: seçilen teklifin tedarikçi dosyası yok. Ekleyin."),
    (r"(\S+) was cancelled by (\S+)\.", r"\g<1> اتلغى بواسطة \g<2>.",
     r"\g<1>, \g<2> tarafından iptal edildi."),
    (r"(\S+) was closed before everything arrived; the rest will not be delivered\. "
     r"Reason: (.*)",
     r"\g<1> اتقفل قبل ما كل الطلبية توصل؛ الباقي مش هيتورّد. السبب: \g<2>",
     r"\g<1> her şey gelmeden kapatıldı; kalanı teslim edilmeyecek. Neden: \g<2>"),
    (r"(\S+): an advance of ([\d.]+)% was authorised by (.+) — it can now be paid\.",
     r"\g<1>: تم اعتماد دفعة مقدمة \g<2>% بواسطة \g<3> — ممكن تتصرف دلوقتي.",
     r"\g<1>: %\g<2> avans \g<3> tarafından onaylandı — artık ödenebilir."),
]

_LANG_COL = {"ar": 1, "tr": 2}
_TITLES = [(re.compile(p, re.S), a, t) for p, a, t in TITLES]
_BODIES = [(re.compile(p, re.S), a, t) for p, a, t in BODIES]


def _one(text, lang, table):
    if not text:
        return text
    s = str(text)
    for rx, ar, tr in table:
        m = rx.fullmatch(s.strip())
        if m:
            return m.expand(ar if lang == "ar" else tr)
    return text


def localise(title, message, lang):
    """(title, message) in `lang`; unchanged for English or an unknown pattern."""
    if lang not in _LANG_COL:
        return title, message
    return _one(title, lang, _TITLES), _one(message, lang, _BODIES)


def localise_rows(rows, lang):
    """Bell rows (Row or dict) -> the same rows, procurement ones translated.
    Returns the input untouched for English so nothing changes there."""
    if lang not in _LANG_COL:
        return rows
    out = []
    for r in rows:
        d = dict(r)
        if (d.get("module") or "") == "procurement":
            d["title"], d["message"] = localise(d.get("title"), d.get("message"), lang)
        out.append(d)
    return out
