# -*- coding: utf-8 -*-
"""
Arabic and Turkish for the messages the maintenance module flashes at people.

Most of this module already speaks three languages: fifty of its fifty-one
flash() calls pass an m_* key that app.js resolves from app/static/i18n. The
fifteen sentences here are the ones that did not, because they carry runtime
data — a ticket number, a report reference, an exception name, a list of what
is still missing — and a bare key cannot carry a value: t() in app.js is an
exact lookup, so "m_report_send_failed (timeout)" resolves to nothing and the
reader gets the raw key.

So these are translated on the server instead, keyed by their English text, the
same way app/approvals/messages.py does it. A message with a value in it is
translated as a TEMPLATE and formatted afterwards —

    flash(_msg("Could not send this report (%s).") % err)

— which is an exact match on a fixed string, rather than a guess at how much of
an already-formatted sentence was the fixed part.

tests_messages_i18n.py fails when a flash() literal in the routes has no entry
here, so a message added next month cannot ship English-only unnoticed.

Wording is plain on purpose: these appear at the moment something has gone
wrong, which is the worst moment to make somebody parse a formal sentence.
"""

# English source (a TEMPLATE where the message carries data) -> {"ar", "tr"}
MESSAGES = {
    "%s added, %s updated, %s unchanged, %s rejected.": {
        "ar": "%s مضافة، %s محدَّثة، %s دون تغيير، %s مرفوضة.",
        "tr": "%s eklendi, %s güncellendi, %s değişmedi, %s reddedildi.",
    },
    "Cannot send yet. Still needed: %s": {
        "ar": "لا يمكن الإرسال الآن. ما زال مطلوباً: %s",
        "tr": "Henüz gönderilemez. Hâlâ gerekli: %s",
    },
    "Choose a file to import.": {
        "ar": "اختر ملفاً للاستيراد.",
        "tr": "İçe aktarmak için bir dosya seçin.",
    },
    "Could not record that decision (%s).": {
        "ar": "تعذّر تسجيل القرار (%s).",
        "tr": "Karar kaydedilemedi (%s).",
    },
    "Could not send this report (%s).": {
        "ar": "تعذّر إرسال هذا التقرير (%s).",
        "tr": "Bu rapor gönderilemedi (%s).",
    },
    "Import failed (%s). Nothing was written — fix the file and run it again.": {
        "ar": "فشل الاستيراد (%s). لم يُكتب أي شيء — صحّح الملف وأعد التشغيل.",
        "tr": "İçe aktarma başarısız (%s). Hiçbir şey yazılmadı — dosyayı düzeltip yeniden çalıştırın.",
    },
    "Report approved.": {
        "ar": "تمت الموافقة على التقرير.",
        "tr": "Rapor onaylandı.",
    },
    "Report rejected.": {
        "ar": "تم رفض التقرير.",
        "tr": "Rapor reddedildi.",
    },
    "Saved as a draft. Not sent yet. %s": {
        "ar": "تم الحفظ كمسودة. لم يُرسل بعد. %s",
        "tr": "Taslak olarak kaydedildi. Henüz gönderilmedi. %s",
    },
    "Saved as a draft. Still needed: %s": {
        "ar": "تم الحفظ كمسودة. ما زال مطلوباً: %s",
        "tr": "Taslak olarak kaydedildi. Hâlâ gerekli: %s",
    },
    "Sent to Engineering for signature.": {
        "ar": "أُرسل إلى الهندسة للتوقيع.",
        "tr": "İmza için Mühendisliğe gönderildi.",
    },
    "Sent to Engineering for signature. %s": {
        "ar": "أُرسل إلى الهندسة للتوقيع. %s",
        "tr": "İmza için Mühendisliğe gönderildi. %s",
    },
    "This machine already has an open ticket (%s). Tick 'create anyway' if this is a separate fault.": {
        "ar": "لهذه الماكينة تذكرة مفتوحة بالفعل (%s). إن كان هذا عطلاً آخر، فعلّم «إنشاء على أي حال».",
        "tr": "Bu makinenin zaten açık bir talebi var (%s). Bu ayrı bir arızaysa «yine de oluştur» kutusunu işaretleyin.",
    },
    "This report is not waiting for a signature.": {
        "ar": "هذا التقرير ليس في انتظار توقيع.",
        "tr": "Bu rapor imza beklemiyor.",
    },
    "You raised this report, so you cannot also sign it. Segregation of duties applies.": {
        "ar": "أنت من كتب هذا التقرير، فلا يمكنك توقيعه. يجب الفصل بين من يطلب ومن يوقّع.",
        "tr": "Bu raporu siz açtınız, bu yüzden imzalayamazsınız. Görevler ayrılığı gereği.",
    },
}


def translate(text, lang):
    """`text` in `lang`, or the English original when there is no translation.

    Exact match only. Messages that carry a value are translated before they are
    formatted, so there is nothing to guess at: an m_* key, or any sentence
    nobody has translated yet, passes through untouched and still reaches the
    reader rather than vanishing.
    """
    if not text or lang in (None, "", "en"):
        return text
    row = MESSAGES.get(str(text).strip())
    if not row:
        return text
    return row.get(lang) or text
