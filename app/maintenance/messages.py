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
}


# The DOAM-mandatory fields, by the label eng_justification.REQUIRED_FIELDS gives
# them. These are not sentences — they are the names of the boxes still to fill
# in, and they reach the reader twice: joined into "Still needed: ..." on the
# report page, and again in the refusal when submitting. Left in English they
# turned an otherwise Arabic refusal back into English at the only part that
# says what to actually do.
FIELD_LABELS = {
    "alternatives considered": {
        "ar": "البدائل التي جرى بحثها",
        "tr": "değerlendirilen alternatifler",
    },
    "asset / machine": {
        "ar": "الأصل / الماكينة",
        "tr": "varlık / makine",
    },
    "criticality": {
        "ar": "درجة الأهمية",
        "tr": "kritiklik",
    },
    "description": {
        "ar": "الوصف",
        "tr": "açıklama",
    },
    "downtime or risk if not actioned": {
        "ar": "التوقف أو الخطر إن لم يُنفَّذ",
        "tr": "yapılmazsa duruş veya risk",
    },
    "quantity on hand": {
        "ar": "الكمية الموجودة في المخزن",
        "tr": "mevcut miktar",
    },
    "request type": {
        "ar": "نوع الطلب",
        "tr": "talep türü",
    },
    "root cause": {
        "ar": "السبب الجذري",
        "tr": "kök neden",
    },
    "store stock check": {
        "ar": "التحقق من رصيد المخزن",
        "tr": "depo stok kontrolü",
    },
}

# Arabic separates a list with its own comma. Using "," there is the kind of
# detail that makes a translated sentence still read as translated.
_SEP = {"ar": "، ", "tr": ", ", "en": ", "}


def labels(text, lang):
    """The "still needed" field list, in `lang`.

    Takes either the joined string the service returns ("root cause, criticality")
    or a list of labels, and gives back a joined string with each label
    translated. A label nobody has translated passes through in English rather
    than disappearing out of the list — an incomplete list is worse than an
    English one, because the reader would go and fill in the wrong boxes.
    """
    items = text if isinstance(text, (list, tuple)) else str(text or "").split(", ")
    items = [i.strip() for i in items if str(i).strip()]
    if lang in (None, "", "en"):
        return ", ".join(items)
    out = [(FIELD_LABELS.get(i, {}) or {}).get(lang) or i for i in items]
    return _SEP.get(lang, ", ").join(out)

# The service answers a refusal with a CODE — "already_submitted", "not_pending"
# — because a code is what the callers branch on. The two routes that had no
# branch for a given code printed it raw inside an otherwise translated
# sentence: «تعذّر إرسال هذا التقرير (already_submitted).» An Arabic reader got a
# fluent sentence ending in an English identifier that told them nothing about
# what to do. These are the sentences those codes mean.
CODE_REASONS = {
    "already_submitted": {
        "en": "This report has already been sent for signature.",
        "ar": "سبق إرسال هذا التقرير للتوقيع.",
        "tr": "Bu rapor imza için zaten gönderildi.",
    },
    "incomplete": {
        "en": "Some required fields are still blank.",
        "ar": "ما زالت بعض الحقول المطلوبة فارغة.",
        "tr": "Bazı zorunlu alanlar hâlâ boş.",
    },
    "not_found": {
        "en": "That report no longer exists.",
        "ar": "لم يعد هذا التقرير موجوداً.",
        "tr": "Bu rapor artık mevcut değil.",
    },
    "not_pending": {
        "en": "This report is not waiting for a signature.",
        "ar": "هذا التقرير ليس في انتظار توقيع.",
        "tr": "Bu rapor imza beklemiyor.",
    },
    # DOAM §3.4 — no person may approve a transaction that names them as
    # requestor. The rule is named, because "you cannot" alone reads as a bug.
    "self_approval_blocked": {
        "en": "You raised this report, so you cannot also sign it. Segregation of duties applies.",
        "ar": "أنت من كتب هذا التقرير، فلا يمكنك توقيعه. يجب الفصل بين من يطلب ومن يوقّع.",
        "tr": "Bu raporu siz açtınız, bu yüzden imzalayamazsınız. Görevler ayrılığı gereği.",
    },
}


def reason(code, lang):
    """The sentence a refusal code means, or None if this code has none.

    None rather than the code itself, so the caller keeps its "(%s)" fallback: a
    code nobody has written a sentence for should still reach the screen. Silently
    swallowing it would turn a new refusal into a message that says nothing at
    all, which is worse than one that says something only IT understands.
    """
    row = CODE_REASONS.get(str(code or "").split(":", 1)[0].strip())
    if not row:
        return None
    return row.get(lang or "en") or row.get("en")

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
