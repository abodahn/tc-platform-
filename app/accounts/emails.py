"""
Accounts — branded, trilingual (EN/AR/TR) transactional emails.

Each builder returns (subject, html, text) in the user's language; `send()`
delivers via the platform SMTP transport (app.services.alerts.send_html_to).
Links must be absolute HTTPS in production (built by the caller from PUBLIC_URL).
No token or password is ever logged. HTML is a self-contained responsive shell.
"""
from html import escape

from app.services.alerts import send_html_to

BRAND = "T&C Garments"
SUPPORT = "IT Support"

# --- palette (inlined; email clients ignore <style>) ---
NAVY = "#0d1220"
RED = "#ED1C24"
INK = "#14161b"
MUTED = "#6a7280"
LINE = "#e5e8ee"


def _dir(lang):
    return "rtl" if lang == "ar" else "ltr"


def _shell(lang, heading, body_html, cta_label=None, cta_url=None, footnote=None):
    d = _dir(lang)
    align = "right" if d == "rtl" else "left"
    cta = ""
    if cta_label and cta_url:
        cta = (f'<tr><td style="padding:8px 0 4px"><a href="{escape(cta_url)}" '
               f'style="display:inline-block;background:{RED};color:#fff;text-decoration:none;'
               f'font-weight:700;font-size:15px;padding:13px 26px;border-radius:10px">{escape(cta_label)}</a></td></tr>'
               f'<tr><td style="padding:6px 0 0;color:{MUTED};font-size:12px;word-break:break-all">{escape(cta_url)}</td></tr>')
    fn = f'<p style="color:{MUTED};font-size:12.5px;margin:18px 0 0">{footnote}</p>' if footnote else ""
    return f"""<!doctype html><html dir="{d}" lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;background:#f5f6f8;font-family:Segoe UI,Arial,sans-serif;color:{INK}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f6f8;padding:26px 12px">
<tr><td align="center">
<table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#fff;border:1px solid {LINE};border-radius:16px;overflow:hidden">
<tr><td style="background:{NAVY};padding:20px 28px" align="{align}">
  <span style="display:inline-block;width:34px;height:34px;background:{RED};border-radius:9px;color:#fff;
  font-weight:800;text-align:center;line-height:34px;font-size:13px;vertical-align:middle">TC</span>
  <span style="color:#fff;font-weight:700;font-size:15px;vertical-align:middle;margin:0 10px">{BRAND}</span>
</td></tr>
<tr><td style="padding:28px" align="{align}">
  <h1 style="margin:0 0 12px;font-size:20px;color:{INK}">{escape(heading)}</h1>
  <table role="presentation" width="100%"><tr><td align="{align}" style="font-size:14.5px;line-height:1.65;color:{INK}">
  {body_html}
  </td></tr>
  {cta}
  </table>
  {fn}
</td></tr>
<tr><td style="padding:16px 28px;border-top:1px solid {LINE};color:{MUTED};font-size:12px" align="{align}">
  {BRAND} — Secure access to the unified digital workplace.<br>
  {_i(lang,'This is an automated message. If you did not expect it, please contact %s.','هذه رسالة تلقائية. إذا لم تكن تتوقعها، يرجى التواصل مع %s.','Bu otomatik bir mesajdır. Beklemiyorduysanız lütfen %s ile iletişime geçin.') % SUPPORT}
</td></tr>
</table></td></tr></table></body></html>"""


def _i(lang, en, ar, tr):
    return {"en": en, "ar": ar, "tr": tr}.get(lang, en)


def _name(user):
    return escape((user.get("full_name") or user.get("username") or "").strip() or "there")


# ==========================================================================
# Builders  ->  (subject, html, text)
# ==========================================================================
def verify_email(user, url, lang="en"):
    subj = _i(lang, f"Verify your {BRAND} account",
              f"تأكيد حساب {BRAND}", f"{BRAND} hesabınızı doğrulayın")
    heading = _i(lang, "Confirm your email address", "أكِّد بريدك الإلكتروني", "E-posta adresinizi onaylayın")
    body = _i(lang,
        f"<p>Hello {_name(user)},</p><p>Please confirm your email to continue setting up your {BRAND} account. This link is single-use and expires soon.</p>",
        f"<p>مرحباً {_name(user)}،</p><p>يرجى تأكيد بريدك الإلكتروني لمتابعة إعداد حسابك في {BRAND}. هذا الرابط يُستخدم مرة واحدة وتنتهي صلاحيته قريباً.</p>",
        f"<p>Merhaba {_name(user)},</p><p>{BRAND} hesabınızı kurmaya devam etmek için lütfen e-postanızı onaylayın. Bu bağlantı tek kullanımlıktır ve kısa sürede sona erer.</p>")
    cta = _i(lang, "Verify Email", "تأكيد البريد", "E-postayı Doğrula")
    fn = _i(lang, "If you didn't request this, you can ignore this email.",
            "إذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة.",
            "Bunu siz talep etmediyseniz bu e-postayı yok sayabilirsiniz.")
    html = _shell(lang, heading, body, cta, url, fn)
    return subj, html, None


def registration_received(user, needs_approval, lang="en"):
    subj = _i(lang, f"{BRAND}: registration received", f"{BRAND}: تم استلام طلب التسجيل", f"{BRAND}: kayıt alındı")
    heading = _i(lang, "We received your registration", "تم استلام طلب تسجيلك", "Kaydınızı aldık")
    nextstep = _i(lang,
        "Your email is confirmed. An administrator will review your request and you'll be notified once it's approved." if needs_approval else "Your account is now active — you can sign in.",
        "تم تأكيد بريدك. سيقوم أحد المسؤولين بمراجعة طلبك وسيتم إخطارك عند الموافقة." if needs_approval else "أصبح حسابك نشطاً — يمكنك تسجيل الدخول.",
        "E-postanız onaylandı. Bir yönetici talebinizi inceleyecek ve onaylandığında bilgilendirileceksiniz." if needs_approval else "Hesabınız artık aktif — giriş yapabilirsiniz.")
    body = f"<p>Hello {_name(user)},</p><p>{escape(nextstep)}</p>"
    return subj, _shell(lang, heading, body), None


def approved(user, url, lang="en"):
    subj = _i(lang, f"{BRAND}: your account is approved", f"{BRAND}: تمت الموافقة على حسابك", f"{BRAND}: hesabınız onaylandı")
    heading = _i(lang, "Your account is approved", "تمت الموافقة على حسابك", "Hesabınız onaylandı")
    body = _i(lang, f"<p>Hello {_name(user)},</p><p>Your {BRAND} account is now active. You can sign in and start using the platform.</p>",
              f"<p>مرحباً {_name(user)}،</p><p>أصبح حسابك في {BRAND} نشطاً الآن. يمكنك تسجيل الدخول والبدء باستخدام المنصة.</p>",
              f"<p>Merhaba {_name(user)},</p><p>{BRAND} hesabınız artık aktif. Giriş yapıp platformu kullanmaya başlayabilirsiniz.</p>")
    cta = _i(lang, "Sign In", "تسجيل الدخول", "Giriş Yap")
    return subj, _shell(lang, heading, body, cta, url), None


def rejected(user, reason, lang="en"):
    subj = _i(lang, f"{BRAND}: registration update", f"{BRAND}: تحديث بشأن التسجيل", f"{BRAND}: kayıt güncellemesi")
    heading = _i(lang, "About your registration", "بخصوص طلب تسجيلك", "Kaydınız hakkında")
    r = escape((reason or "").strip())
    body = _i(lang,
        f"<p>Hello {_name(user)},</p><p>Your registration could not be approved at this time.</p><p><b>Reason:</b> {r}</p><p>If you believe this is a mistake, please contact {SUPPORT}.</p>",
        f"<p>مرحباً {_name(user)}،</p><p>تعذّرت الموافقة على طلب تسجيلك في الوقت الحالي.</p><p><b>السبب:</b> {r}</p><p>إذا كنت تعتقد أن هذا خطأ، يرجى التواصل مع {SUPPORT}.</p>",
        f"<p>Merhaba {_name(user)},</p><p>Kaydınız şu anda onaylanamadı.</p><p><b>Neden:</b> {r}</p><p>Bunun bir hata olduğunu düşünüyorsanız lütfen {SUPPORT} ile iletişime geçin.</p>")
    return subj, _shell(lang, heading, body), None


def reset_request(user, url, ttl_min, lang="en"):
    subj = _i(lang, f"{BRAND}: reset your password", f"{BRAND}: إعادة تعيين كلمة المرور", f"{BRAND}: şifrenizi sıfırlayın")
    heading = _i(lang, "Reset your password", "إعادة تعيين كلمة المرور", "Şifrenizi sıfırlayın")
    body = _i(lang,
        f"<p>Hello {_name(user)},</p><p>We received a request to reset your password. Use the button below within {ttl_min} minutes.</p>",
        f"<p>مرحباً {_name(user)}،</p><p>تلقّينا طلباً لإعادة تعيين كلمة مرورك. استخدم الزر أدناه خلال {ttl_min} دقيقة.</p>",
        f"<p>Merhaba {_name(user)},</p><p>Şifrenizi sıfırlama talebi aldık. Aşağıdaki düğmeyi {ttl_min} dakika içinde kullanın.</p>")
    cta = _i(lang, "Reset Password", "إعادة تعيين كلمة المرور", "Şifreyi Sıfırla")
    fn = _i(lang, f"If you didn't request this, you can safely ignore this email and your password stays unchanged. If you're concerned, contact {SUPPORT}.",
            f"إذا لم تطلب ذلك، يمكنك تجاهل هذه الرسالة بأمان وستبقى كلمة مرورك دون تغيير. إذا كان لديك قلق، تواصل مع {SUPPORT}.",
            f"Bunu siz talep etmediyseniz bu e-postayı güvenle yok sayabilirsiniz, şifreniz değişmez. Endişeliyseniz {SUPPORT} ile iletişime geçin.")
    return subj, _shell(lang, heading, body, cta, url, fn), None


def reset_sso_notice(user, lang="en"):
    subj = _i(lang, f"{BRAND}: sign-in help", f"{BRAND}: مساعدة تسجيل الدخول", f"{BRAND}: giriş yardımı")
    heading = _i(lang, "Use your organisation sign-in", "استخدم تسجيل دخول مؤسستك", "Kurumsal girişinizi kullanın")
    body = _i(lang,
        f"<p>Hello {_name(user)},</p><p>Your account signs in through your organisation's identity provider (SSO), so a local password reset doesn't apply. Please use the normal sign-in, or contact {SUPPORT} if you need help.</p>",
        f"<p>مرحباً {_name(user)}،</p><p>يتم تسجيل الدخول إلى حسابك عبر موفّر هوية مؤسستك (SSO)، لذا لا تنطبق إعادة تعيين كلمة مرور محلية. يرجى استخدام تسجيل الدخول المعتاد أو التواصل مع {SUPPORT}.</p>",
        f"<p>Merhaba {_name(user)},</p><p>Hesabınız kurumunuzun kimlik sağlayıcısı (SSO) üzerinden giriş yapıyor; bu nedenle yerel şifre sıfırlama geçerli değildir. Lütfen normal girişi kullanın veya {SUPPORT} ile iletişime geçin.</p>")
    return subj, _shell(lang, heading, body), None


def password_changed(user, lang="en"):
    subj = _i(lang, f"{BRAND}: your password was changed", f"{BRAND}: تم تغيير كلمة مرورك", f"{BRAND}: şifreniz değiştirildi")
    heading = _i(lang, "Your password was changed", "تم تغيير كلمة مرورك", "Şifreniz değiştirildi")
    body = _i(lang,
        f"<p>Hello {_name(user)},</p><p>This confirms your {BRAND} password was just changed. If this was you, no action is needed.</p><p><b>If you did NOT make this change, contact {SUPPORT} immediately</b> — your account may be at risk.</p>",
        f"<p>مرحباً {_name(user)}،</p><p>هذا تأكيد بأنه تم تغيير كلمة مرور حسابك في {BRAND} للتو. إذا كنت أنت من قام بذلك، فلا حاجة لأي إجراء.</p><p><b>إذا لم تقم بهذا التغيير، تواصل مع {SUPPORT} فوراً</b> — قد يكون حسابك معرّضاً للخطر.</p>",
        f"<p>Merhaba {_name(user)},</p><p>Bu, {BRAND} şifrenizin az önce değiştirildiğini onaylar. Bunu siz yaptıysanız işlem gerekmez.</p><p><b>Bu değişikliği siz yapmadıysanız hemen {SUPPORT} ile iletişime geçin</b> — hesabınız risk altında olabilir.</p>")
    return subj, _shell(lang, heading, body), None


def security_alert(user, message, lang="en"):
    subj = _i(lang, f"{BRAND}: security alert", f"{BRAND}: تنبيه أمني", f"{BRAND}: güvenlik uyarısı")
    heading = _i(lang, "Security notice", "إشعار أمني", "Güvenlik bildirimi")
    body = f"<p>Hello {_name(user)},</p><p>{escape(message)}</p><p>{_i(lang, f'If this wasn’t you, contact {SUPPORT}.', f'إذا لم يكن هذا أنت، تواصل مع {SUPPORT}.', f'Bu siz değilseniz {SUPPORT} ile iletişime geçin.')}</p>"
    return subj, _shell(lang, heading, body), None


# ==========================================================================
# send
# ==========================================================================
def send(to_email, built):
    subject, html, text = built
    return send_html_to([to_email], subject, html, text)
