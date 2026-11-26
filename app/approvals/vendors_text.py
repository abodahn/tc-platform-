# -*- coding: utf-8 -*-
"""
Server-side strings for the vendors lane (vendor approval, supplier documents,
request comments): refusal / success flashes merged into i18n_text.UI as
"<code>_flash", and the English sentences the existing quote routes flash,
merged into messages.MESSAGES. No imports: i18n_text and messages load it.
"""

UI_FLASH = {
    "en": {
        "vendor_pending_flash": "This supplier is still pending approval. Finance or a procurement admin must approve it on the Vendors page before it can be chosen, awarded, ordered from or paid an advance.",
        "vendor_blocked_flash": "This supplier is blocked. It cannot be chosen, awarded, ordered from or paid an advance.",
        "vendor_approve_forbidden_flash": "Only Finance or a procurement admin can approve or unblock a supplier.",
        "vendor_self_approve_flash": "You created this supplier (or changed its email or bank details), so someone else must approve it.",
        "vendor_block_forbidden_flash": "You are not allowed to change this supplier.",
        "block_reason_required_flash": "Write why the supplier is being blocked.",
        "bad_date_flash": "Enter the expiry dates as real dates.",
        "vendor_doc_type_flash": "Upload the document as a PDF or an image (PNG, JPG, WEBP).",
        "vendor_doc_empty_flash": "Choose the document file to upload.",
        "vendor_doc_too_large_flash": "The document is too large (max 3 MB).",
        "vendor_doc_saved_flash": "Document uploaded.",
        "vendor_approved_ok_flash": "Supplier approved.",
        "vendor_blocked_ok_flash": "Supplier blocked.",
        "comment_saved_flash": "Comment posted.",
        "comment_required_flash": "Write the comment first.",
        "comment_too_long_flash": "The comment is too long (max 1000 characters).",
        "comment_forbidden_flash": "Only the requester, the signers, buyers and admins can comment on this request.",
    },
    "ar": {
        "vendor_pending_flash": "المورد ده لسه مستني الاعتماد. لازم الحسابات أو مسؤول المشتريات يعتمده من صفحة الموردين قبل ما نختاره أو نرسّي عليه أو نطلع له أمر شراء أو نصرف له دفعة مقدمة.",
        "vendor_blocked_flash": "المورد ده موقوف. مش ممكن نختاره أو نرسّي عليه أو نطلع له أمر شراء أو نصرف له دفعة مقدمة.",
        "vendor_approve_forbidden_flash": "الحسابات أو مسؤول المشتريات بس هم اللي يقدروا يعتمدوا المورد أو يرفعوا الإيقاف.",
        "vendor_self_approve_flash": "إنت اللي ضفت المورد ده (أو غيّرت إيميله أو بياناته البنكية)، فلازم حد تاني يعتمده.",
        "vendor_block_forbidden_flash": "مش مسموح لك تعدّل المورد ده.",
        "block_reason_required_flash": "اكتب سبب إيقاف المورد.",
        "bad_date_flash": "اكتب تواريخ الانتهاء كتواريخ صحيحة.",
        "vendor_doc_type_flash": "ارفع المستند PDF أو صورة (PNG أو JPG أو WEBP).",
        "vendor_doc_empty_flash": "اختار ملف المستند اللي هترفعه.",
        "vendor_doc_too_large_flash": "المستند كبير قوي (الحد الأقصى 3 ميجا).",
        "vendor_doc_saved_flash": "تم رفع المستند.",
        "vendor_approved_ok_flash": "تم اعتماد المورد.",
        "vendor_blocked_ok_flash": "تم إيقاف المورد.",
        "comment_saved_flash": "تم نشر التعليق.",
        "comment_required_flash": "اكتب التعليق الأول.",
        "comment_too_long_flash": "التعليق طويل قوي (الحد الأقصى 1000 حرف).",
        "comment_forbidden_flash": "مقدّم الطلب والموقّعين والمشتريات والمسؤولين بس هم اللي يقدروا يعلّقوا على الطلب ده.",
    },
    "tr": {
        "vendor_pending_flash": "Bu tedarikçi hâlâ onay bekliyor. Seçilmeden, kalem verilmeden, sipariş açılmadan veya avans ödenmeden önce Finans ya da bir satın alma yöneticisi Tedarikçiler sayfasından onaylamalıdır.",
        "vendor_blocked_flash": "Bu tedarikçi engellendi. Seçilemez, kalem verilemez, sipariş açılamaz ve avans ödenemez.",
        "vendor_approve_forbidden_flash": "Bir tedarikçiyi yalnızca Finans veya bir satın alma yöneticisi onaylayabilir ya da engelini kaldırabilir.",
        "vendor_self_approve_flash": "Bu tedarikçiyi siz oluşturdunuz (veya e-postasını ya da banka bilgilerini değiştirdiniz); onu başka biri onaylamalıdır.",
        "vendor_block_forbidden_flash": "Bu tedarikçiyi değiştirme yetkiniz yok.",
        "block_reason_required_flash": "Tedarikçinin neden engellendiğini yazın.",
        "bad_date_flash": "Son geçerlilik tarihlerini geçerli tarih olarak girin.",
        "vendor_doc_type_flash": "Belgeyi PDF veya görüntü (PNG, JPG, WEBP) olarak yükleyin.",
        "vendor_doc_empty_flash": "Yüklenecek belge dosyasını seçin.",
        "vendor_doc_too_large_flash": "Belge çok büyük (en fazla 3 MB).",
        "vendor_doc_saved_flash": "Belge yüklendi.",
        "vendor_approved_ok_flash": "Tedarikçi onaylandı.",
        "vendor_blocked_ok_flash": "Tedarikçi engellendi.",
        "comment_saved_flash": "Yorum gönderildi.",
        "comment_required_flash": "Önce yorumu yazın.",
        "comment_too_long_flash": "Yorum çok uzun (en fazla 1000 karakter).",
        "comment_forbidden_flash": "Bu talebe yalnızca talep eden, imzacılar, satın alma ve yöneticiler yorum yazabilir.",
    },
}

# English sentences the EXISTING quote routes flash (via _QUOTE_ERRORS) for the
# two new refusal codes; see app/routes/approvals_vendors.py.
QUOTE_ERRORS = {
    "vendor_pending": UI_FLASH["en"]["vendor_pending_flash"],
    "vendor_blocked": UI_FLASH["en"]["vendor_blocked_flash"],
}

MESSAGES = {
    UI_FLASH["en"]["vendor_pending_flash"]: {"ar": UI_FLASH["ar"]["vendor_pending_flash"],
                                             "tr": UI_FLASH["tr"]["vendor_pending_flash"]},
    UI_FLASH["en"]["vendor_blocked_flash"]: {"ar": UI_FLASH["ar"]["vendor_blocked_flash"],
                                             "tr": UI_FLASH["tr"]["vendor_blocked_flash"]},
}
