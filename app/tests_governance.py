"""
Governance review surface (/governance) — self-verification.

Run standalone:  python app/tests_governance.py
Config.DB_PATH is redirected to a temp dir so the repo's platform.db is never
touched, and nothing is ever written into the repo.

Covers: matrix correctness against app.security (super_admin's '*', a built-in
role, a DB custom role, a role with no permissions at all), each findings
category detecting a deliberately planted case, permission refusal for a
non-admin on the page AND both export formats, that a refused page leaks no
permission names, and 200 in en/ar/tr with every data-i18n key resolving.
"""
import os
import re
import sys
import json
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="gov_"))
os.chdir(TMP)
sys.path.insert(0, str(REPO))
os.environ["TC_ENV"] = "development"
os.environ.pop("DATABASE_URL", None)
os.environ["TC_HEALTH_TIMEOUT"] = "1"
os.environ["TC_AUTO_TICKET_ENABLED"] = "false"

import config                                       # noqa: E402
config.Config.DB_PATH = TMP / "platform.db"

from app import create_app                          # noqa: E402
from app import security as sec                     # noqa: E402
from app.db import get_db, utcnow                   # noqa: E402
from app.routes import governance as gov            # noqa: E402


# ===========================================================================
# The i18n payload this lane introduces. The orchestrator owns
# app/static/i18n/**, so a key is valid if it is already shipped OR declared
# here with all three languages — and this file is the exact payload reported.
# ===========================================================================
NEW_I18N = {
 "gov.eyebrow": ("Governance", "الحوكمة", "Yönetişim"),
 "gov.title": ("Responsibility, Permissions & Rules", "المسؤوليات والصلاحيات والقواعد",
               "Sorumluluk, Yetkiler ve Kurallar"),
 "gov.sub": ("Who can do what on this platform, which rules the modules enforce, and where the authorisation model has holes. Read-only — nothing on these pages changes a permission.",
             "من يستطيع فعل ماذا في هذه المنصة، وما القواعد التي تفرضها الوحدات، وأين توجد الثغرات في نموذج الصلاحيات. للعرض فقط — لا شيء في هذه الصفحات يغيّر أي صلاحية.",
             "Bu platformda kimin neyi yapabildiği, modüllerin uyguladığı kurallar ve yetkilendirme modelindeki boşluklar. Salt okunur — bu sayfalar hiçbir yetkiyi değiştirmez."),
 "gov.tab.matrix": ("Permission matrix", "مصفوفة الصلاحيات", "Yetki matrisi"),
 "gov.tab.users": ("Per-user grants", "منح على مستوى المستخدم", "Kullanıcı bazlı yetkiler"),
 "gov.tab.responsibility": ("Responsibility matrix", "مصفوفة المسؤوليات", "Sorumluluk matrisi"),
 "gov.tab.rules": ("Rules & ladders", "القواعد وسلالم الاعتماد", "Kurallar ve onay basamakları"),
 "gov.tab.findings": ("Findings", "الملاحظات", "Bulgular"),
 "gov.export.csv": ("CSV", "CSV", "CSV"),
 "gov.export.pdf": ("PDF", "PDF", "PDF"),
 "gov.kpi.roles": ("Roles", "الأدوار", "Roller"),
 "gov.kpi.perms": ("Permissions", "الصلاحيات", "Yetkiler"),
 "gov.kpi.groups": ("Permission groups", "مجموعات الصلاحيات", "Yetki grupları"),
 "gov.filter.title": ("Filter", "تصفية", "Filtre"),
 "gov.filter.group": ("Permission group", "مجموعة الصلاحيات", "Yetki grubu"),
 "gov.filter.role": ("Role", "الدور", "Rol"),
 "gov.filter.all": ("All", "الكل", "Tümü"),
 "gov.filter.apply": ("Apply", "تطبيق", "Uygula"),
 "gov.filter.clear": ("Clear", "مسح", "Temizle"),
 "gov.legend.granted": ("Granted", "ممنوحة", "Verilmiş"),
 "gov.legend.star": ("Granted via *", "ممنوحة عبر *", "* ile verilmiş"),
 "gov.legend.none": ("Not granted", "غير ممنوحة", "Verilmemiş"),
 "gov.col.role": ("Role", "الدور", "Rol"),
 "gov.col.holders": ("Holders", "عدد الحاملين", "Sahip sayısı"),
 "gov.col.user": ("User", "المستخدم", "Kullanıcı"),
 "gov.col.beyond": ("Beyond the role", "خارج نطاق الدور", "Rolün ötesinde"),
 "gov.col.redundant": ("Already in the role", "موجودة أصلاً في الدور", "Zaten rolde mevcut"),
 "gov.col.module": ("Module", "الوحدة", "Modül"),
 "gov.col.view": ("Can view", "يمكنه الاطلاع", "Görüntüleyebilir"),
 "gov.col.change": ("Can change", "يمكنه التعديل", "Değiştirebilir"),
 "gov.col.approve": ("Can approve", "يمكنه الاعتماد", "Onaylayabilir"),
 "gov.col.admin": ("Can administer", "يمكنه الإدارة", "Yönetebilir"),
 "gov.col.perm": ("Permission", "الصلاحية", "Yetki"),
 "gov.col.mapped": ("Mapped to", "مصنّفة كـ", "Şuna eşlendi"),
 "gov.col.roles": ("Roles", "الأدوار", "Roller"),
 "gov.col.detail": ("Detail", "التفاصيل", "Ayrıntı"),
 "gov.col.route": ("Route", "المسار", "Rota"),
 "gov.col.endpoint": ("Endpoint", "نقطة النهاية", "Uç nokta"),
 "gov.col.methods": ("Methods", "الطرق", "Metotlar"),
 "gov.col.login": ("Login required", "يتطلب تسجيل الدخول", "Giriş gerekli"),
 "gov.col.perm_count": ("Permissions granted", "عدد الصلاحيات الممنوحة", "Verilen yetki sayısı"),
 "gov.col.type": ("Type", "النوع", "Tür"),
 "gov.col.risk": ("Risk", "المخاطرة", "Risk"),
 "gov.tag.custom": ("Custom role", "دور مخصص", "Özel rol"),
 "gov.tag.builtin": ("Built-in", "مدمج", "Yerleşik"),
 "gov.tag.star": ("All permissions", "كل الصلاحيات", "Tüm yetkiler"),
 "gov.empty.roles": ("No role matches this filter.", "لا يوجد دور مطابق لهذه التصفية.",
                     "Bu filtreye uyan rol yok."),
 "gov.empty.extra": ("No active user carries a per-user permission grant. Every permission on this platform comes from a role.",
                     "لا يوجد مستخدم نشط يحمل صلاحية ممنوحة له بشكل فردي. كل صلاحية في هذه المنصة تأتي من دور.",
                     "Hiçbir aktif kullanıcı kişisel yetki taşımıyor. Bu platformdaki her yetki bir rolden gelir."),
 "gov.users.title": ("Per-user extra permissions", "الصلاحيات الإضافية لكل مستخدم",
                     "Kullanıcıya özel ek yetkiler"),
 "gov.users.sub": ("A permission granted directly on a user account sits outside every role. It is what an auditor asks about first and the only place on the platform where it is visible.",
                   "الصلاحية الممنوحة مباشرة لحساب مستخدم تقع خارج كل الأدوار. وهي أول ما يسأل عنه المدقق، وهذه هي الصفحة الوحيدة في المنصة التي تُعرض فيها.",
                   "Doğrudan bir kullanıcı hesabına verilen yetki hiçbir rolün içinde değildir. Denetçinin ilk sorduğu şeydir ve platformda görülebildiği tek yer burasıdır."),
 "gov.raci.sub": ("Derived from the permission names themselves (_view / _manage / _approve / _admin), never from a hand-maintained list, so this cannot drift away from the code. Permissions whose name does not follow the convention are listed separately below instead of being guessed.",
                  "مشتقة من أسماء الصلاحيات نفسها (_view / _manage / _approve / _admin) وليست من قائمة تُحدَّث يدوياً، لذا لا يمكن أن تنحرف عن الكود. أما الصلاحيات التي لا تتبع نظام التسمية فتُدرج بالأسفل بدلاً من تخمينها.",
                  "Doğrudan yetki adlarından (_view / _manage / _approve / _admin) türetilir, elle güncellenen bir listeden değil; bu yüzden koddan sapamaz. Adlandırma kuralına uymayan yetkiler tahmin edilmek yerine aşağıda ayrıca listelenir."),
 "gov.raci.exceptions": ("Where the naming convention does not hold", "حيث لا ينطبق نظام التسمية",
                         "Adlandırma kuralının geçerli olmadığı yerler"),
 "gov.raci.exceptions_sub": ("These permission names do not end in _view, _manage, _approve or _admin. Each one is either mapped by an explicit exception recorded in the code, or left unclassified. Nothing here is inferred silently.",
                             "أسماء هذه الصلاحيات لا تنتهي بـ _view أو _manage أو _approve أو _admin. كل واحدة إما مصنّفة عبر استثناء صريح مسجّل في الكود أو تُركت دون تصنيف. لا شيء هنا يُستنتج بصمت.",
                             "Bu yetki adları _view, _manage, _approve veya _admin ile bitmiyor. Her biri ya kodda açıkça kayıtlı bir istisnayla eşlenmiştir ya da sınıflandırılmamış bırakılmıştır. Burada hiçbir şey sessizce varsayılmaz."),
 "gov.raci.unclassified": ("Not classifiable from the name", "لا يمكن تصنيفها من الاسم",
                           "Adından sınıflandırılamıyor"),
 "gov.rules.sod": ("Segregation of duties", "الفصل بين المهام", "Görevler ayrılığı"),
 "gov.rules.exempt_on": ("Admins exempt: ON", "استثناء المسؤولين: مفعّل", "Yöneticiler muaf: AÇIK"),
 "gov.rules.exempt_off": ("Admins exempt: OFF (strict)", "استثناء المسؤولين: معطّل (وضع صارم)",
                          "Yöneticiler muaf: KAPALI (katı)"),
 "gov.rules.sod_self": ("Self-approval: the person who raised a purchase request may never sign any of its approval stages — raising it is already their signature.",
                        "الاعتماد الذاتي: من يرفع طلب الشراء لا يجوز له توقيع أي من مراحل اعتماده — فرفع الطلب هو توقيعه بالفعل.",
                        "Kendi kendini onaylama: Satın alma talebini açan kişi, o talebin hiçbir onay aşamasını imzalayamaz — talebi açması zaten onun imzasıdır."),
 "gov.rules.sod_dual": ("Dual role: one person may not sign two different stages of the same request, not even when a delegation makes them eligible for both.",
                        "ازدواج الأدوار: لا يجوز لشخص واحد توقيع مرحلتين مختلفتين من الطلب نفسه، ولو أهّله التفويض لكلتيهما.",
                        "Çifte rol: Bir kişi aynı talebin iki farklı aşamasını imzalayamaz; bir vekâlet onu her ikisi için uygun hâle getirse bile."),
 "gov.rules.warning": ("Warning", "تحذير", "Uyarı"),
 "gov.rules.sod_warn": ("While \"admins exempt\" is on, super_admin and every role holding Procurement admin bypass BOTH rules and can walk a request through the whole ladder alone. This materially weakens segregation of duties. Turn it off in Procurement settings for strict mode.",
                        "طالما أن «استثناء المسؤولين» مفعّل، يتجاوز المسؤول الأعلى وكل دور يحمل صلاحية إدارة المشتريات كلتا القاعدتين ويستطيع تمرير الطلب عبر السلم بأكمله منفرداً. هذا يضعف الفصل بين المهام بشكل جوهري. عطّله من إعدادات المشتريات لتفعيل الوضع الصارم.",
                        "\"Yöneticiler muaf\" açıkken super_admin ve Satın Alma yöneticisi yetkisini taşıyan her rol HER İKİ kuralı da atlar ve bir talebi tek başına tüm basamaklardan geçirebilir. Bu, görevler ayrılığını esaslı biçimde zayıflatır. Katı mod için Satın Alma ayarlarından kapatın."),
 "gov.rules.sod_strict": ("Strict mode: administrators are bound by both independence rules exactly like everyone else.",
                          "الوضع الصارم: المسؤولون ملزمون بقاعدتي الاستقلالية تماماً كغيرهم.",
                          "Katı mod: Yöneticiler her iki bağımsızlık kuralına da herkes gibi tabidir."),
 "gov.rules.code_default": ("Code default", "القيمة الافتراضية في الكود", "Koddaki varsayılan"),
 "gov.rules.overridden": ("Overridden in settings", "تم تجاوزها من الإعدادات",
                          "Ayarlardan geçersiz kılındı"),
 "gov.rules.unavailable": ("Not available in this build.", "غير متاح في هذه النسخة.",
                           "Bu sürümde mevcut değil."),
 "gov.rules.proc": ("Procurement approval ladder", "سلم اعتماد المشتريات",
                    "Satın alma onay basamakları"),
 "gov.rules.currency": ("EGP-equivalent", "بما يعادل الجنيه المصري", "EGP karşılığı"),
 "gov.rules.stage": ("Stage", "المرحلة", "Aşama"),
 "gov.rules.kind": ("Type", "النوع", "Tür"),
 "gov.rules.threshold": ("Joins the ladder from", "تنضم للسلم ابتداءً من",
                         "Şu tutardan itibaren devreye girer"),
 "gov.rules.signers": ("Signing roles", "الأدوار الموقّعة", "İmzalayan roller"),
 "gov.rules.demand": ("Always required", "مطلوبة دائماً", "Her zaman gerekli"),
 "gov.rules.value": ("Value-gated", "حسب القيمة", "Tutara bağlı"),
 "gov.rules.examples": ("Signatures required by request value",
                        "عدد التوقيعات المطلوبة حسب قيمة الطلب",
                        "Talep tutarına göre gereken imzalar"),
 "gov.rules.signatures": ("signatures", "توقيعات", "imza"),
 "gov.rules.rfq": ("Competitive quotation gate", "بوابة عروض الأسعار التنافسية",
                   "Rekabetçi teklif kapısı"),
 "gov.rules.rfq_quotes": ("quotes from distinct vendors at or above",
                          "عروض أسعار من موردين مختلفين عند أو أعلى من",
                          "farklı tedarikçiden teklif, şu tutar ve üzerinde"),
 "gov.rules.rfq_waiver": ("unless Purchasing records a single-source justification.",
                          "ما لم تسجّل إدارة المشتريات مبرر المصدر الوحيد.",
                          "Satın alma tek kaynak gerekçesi kaydetmedikçe."),
 "gov.rules.sla": ("Stage SLA", "مهلة المرحلة", "Aşama SLA"),
 "gov.rules.escalation": ("Segregation-of-duties escalation chain",
                          "سلسلة تصعيد الفصل بين المهام",
                          "Görevler ayrılığı yükseltme zinciri"),
 "gov.rules.escalation_sub": ("When the requester is the only person who may sign a rung, that rung would wait for a signature that can never legally arrive. It is escalated one level up the org chart instead. A role with no superior configured means nobody above it, and the request is refused at submit rather than left to rot.",
                              "عندما يكون مقدّم الطلب هو الشخص الوحيد المخوَّل بتوقيع درجة ما، تظل تلك الدرجة تنتظر توقيعاً لا يمكن أن يصل نظاماً. لذلك تُصعَّد درجة واحدة أعلى في الهيكل التنظيمي. والدور الذي لم يُحدَّد له رئيس يعني أنه لا أحد فوقه، وعندها يُرفض الطلب عند الإرسال بدلاً من تركه معلّقاً.",
                              "Talebi açan kişi bir basamağı imzalayabilecek tek kişiyse, o basamak hiçbir zaman yasal olarak gelemeyecek bir imzayı bekler. Bunun yerine organizasyon şemasında bir seviye yukarı yükseltilir. Üstü tanımlanmamış bir rol, üzerinde kimse olmadığı anlamına gelir; talep beklemede çürümek yerine gönderim anında reddedilir."),
 "gov.rules.superior": ("Escalates to", "يُصعَّد إلى", "Yükseltilir"),
 "gov.rules.nobody": ("Nobody above", "لا أحد أعلى منه", "Üstünde kimse yok"),
 "gov.rules.maint": ("Maintenance approval ladder", "سلم اعتماد الصيانة",
                     "Bakım onay basamakları"),
 "gov.rules.name": ("Rule", "القاعدة", "Kural"),
 "gov.rules.criticality": ("Part criticality", "أهمية قطعة الغيار", "Parça kritikliği"),
 "gov.rules.levels": ("Approver roles, in order", "أدوار الاعتماد بالترتيب",
                      "Onaylayan roller, sırayla"),
 "gov.rules.escalation_hours": ("Escalates after", "يُصعَّد بعد", "Şu süre sonra yükseltilir"),
 "gov.rules.inactive": ("Inactive", "غير مفعّلة", "Pasif"),
 "gov.rules.holders": ("Who holds each role right now", "من يشغل كل دور حالياً",
                       "Her rolü şu anda kim taşıyor"),
 "gov.rules.holders_sub": ("A role with no holder is a governance hole; a role with one holder is a continuity risk.",
                           "دور بلا شاغل يمثل ثغرة حوكمة، ودور بشاغل واحد يمثل مخاطرة استمرارية.",
                           "Sahibi olmayan rol bir yönetişim boşluğudur; tek sahibi olan rol bir süreklilik riskidir."),
 "gov.rules.no_holder": ("No holder", "بلا شاغل", "Sahibi yok"),
 "gov.rules.single": ("Single holder", "شاغل واحد", "Tek sahip"),
 "gov.rules.ok": ("Covered", "مغطّى", "Kapsanıyor"),
 "gov.find.k_unheld": ("Unheld permissions", "صلاحيات بلا حامل", "Sahipsiz yetkiler"),
 "gov.find.k_star_only": ("Held only by an all-permissions account",
                          "بحوزة حساب كل الصلاحيات فقط",
                          "Yalnızca tüm yetkilere sahip hesapta"),
 "gov.find.star_only": ("Permissions whose only holder is an all-permissions account",
                        "صلاحيات حاملها الوحيد حساب يملك كل الصلاحيات",
                        "Tek sahibi tüm yetkilere sahip bir hesap olan yetkiler"),
 "gov.find.star_note": ("Somebody can technically do these, but only because they hold every permission. No business role holder exists, so there is nobody to name as accountable and nobody to cover the work if the root account is unavailable.",
                        "يستطيع أحدهم تقنياً تنفيذ هذه الصلاحيات، لكن فقط لأنه يملك كل الصلاحيات. لا يوجد شاغل لدور وظيفي يحملها، فلا أحد يُسمّى مسؤولاً عنها ولا أحد يغطي العمل إذا تعذّر الوصول إلى حساب المسؤول الجذر.",
                        "Bunları teknik olarak biri yapabilir, ancak yalnızca tüm yetkilere sahip olduğu için. Bir iş rolü sahibi yoktur; dolayısıyla sorumlu olarak gösterilecek kimse ve kök hesap ulaşılamazsa işi devralacak kimse yoktur."),
 "gov.find.no_named_role": ("No role names it", "لا يذكرها أي دور", "Hiçbir rol adlandırmıyor"),
 "gov.find.indirect_note": ("Routes that hand their check to a helper in the same module are counted as guarded and excluded here:",
                            "المسارات التي تُسنِد فحصها إلى دالة مساعدة في الوحدة نفسها تُحتسب محمية وتُستبعد هنا:",
                            "Denetimini aynı modüldeki bir yardımcıya devreden rotalar korumalı sayılır ve buraya alınmaz:"),
 "gov.find.k_empty": ("Empty roles", "أدوار فارغة", "Boş roller"),
 "gov.find.k_dead": ("Unenforced permissions", "صلاحيات غير مطبَّقة", "Uygulanmayan yetkiler"),
 "gov.find.k_open": ("Unguarded routes", "مسارات بلا حماية", "Korumasız rotalar"),
 "gov.find.k_extra": ("Users above their role", "مستخدمون فوق نطاق دورهم",
                      "Rolünün üzerindeki kullanıcılar"),
 "gov.find.unheld": ("Permissions granted by a role but held by no active user",
                     "صلاحيات يمنحها دور ولا يحملها أي مستخدم نشط",
                     "Bir rolün verdiği ama hiçbir aktif kullanıcının taşımadığı yetkiler"),
 "gov.find.empty_roles": ("Roles with no active users", "أدوار بلا مستخدمين نشطين",
                          "Aktif kullanıcısı olmayan roller"),
 "gov.find.single_roles": ("Roles with a single holder (continuity risk)",
                           "أدوار بشاغل واحد (مخاطرة استمرارية)",
                           "Tek sahibi olan roller (süreklilik riski)"),
 "gov.find.dead": ("Permissions no route enforces", "صلاحيات لا يفرضها أي مسار",
                   "Hiçbir rotanın uygulamadığı yetkiler"),
 "gov.find.open_routes": ("Routes with no permission check", "مسارات بلا فحص صلاحيات",
                          "Yetki denetimi olmayan rotalar"),
 "gov.find.escalations": ("Users whose extra permissions exceed their role",
                          "مستخدمون تتجاوز صلاحياتهم الإضافية نطاق دورهم",
                          "Ek yetkileri rolünü aşan kullanıcılar"),
 "gov.find.none": ("Nothing found in this category.", "لا توجد نتائج في هذه الفئة.",
                   "Bu kategoride bir şey bulunamadı."),
 "gov.find.scan_note": ("Static scan of the source of every registered route, looking for the permission decorator and the in-handler guards. A permission enforced only inside a service the route calls therefore appears here — that is deliberate: enforcement an auditor cannot see on the route is enforcement they cannot sign off.",
                        "فحص ساكن لشيفرة كل مسار مسجَّل بحثاً عن مُزخرِف الصلاحية وعن الفحوص داخل المعالج. لذلك تظهر هنا أي صلاحية تُفرض داخل خدمة يستدعيها المسار فقط — وهذا مقصود: الفرض الذي لا يراه المدقق على المسار لا يستطيع اعتماده.",
                        "Kayıtlı her rotanın kaynak kodunun, yetki dekoratörü ve işleyici içi denetimler için statik taraması. Bu nedenle yalnızca rotanın çağırdığı bir servisin içinde uygulanan yetki burada görünür — bu kasıtlıdır: Denetçinin rota üzerinde göremediği bir denetimi onaylaması da mümkün değildir."),
 "gov.find.open_note": ("Every registered route that carries no permission guard in its own source. Some are legitimately public (sign-in, health, static assets); the ones that are not are the most serious finding on this page.",
                        "كل مسار مسجَّل لا يحمل أي فحص صلاحية في شيفرته. بعضها عام بشكل مشروع (تسجيل الدخول، حالة النظام، الملفات الثابتة)، وما عدا ذلك هو أخطر ملاحظة في هذه الصفحة.",
                        "Kendi kaynağında hiçbir yetki denetimi taşımayan her kayıtlı rota. Bazıları meşru şekilde herkese açıktır (oturum açma, sağlık, statik dosyalar); geri kalanlar bu sayfadaki en ciddi bulgudur."),
 "gov.find.mentioned": ("Referenced elsewhere in the code", "مُشار إليها في مكان آخر بالكود",
                        "Kodun başka yerinde referans veriliyor"),
 "gov.find.unreferenced": ("Not referenced anywhere", "غير مُشار إليها في أي مكان",
                           "Hiçbir yerde referans yok"),
 "gov.col.stage": ("Stage", "المرحلة", "Aşama"),
 "gov.find.k_unsignable": ("Approval rungs nobody can sign", "مراحل اعتماد لا يستطيع أحد توقيعها",
                           "Kimsenin imzalayamadığı onay basamakları"),
 "gov.find.unsignable": ("Approval rungs no active user can sign",
                         "مراحل اعتماد لا يمكن لأي مستخدم نشط توقيعها",
                         "Hiçbir aktif kullanıcının imzalayamadığı onay basamakları"),
 "gov.find.unsignable_note": ("A request that reaches one of these rungs waits for a signature nobody holds the role to give. Nothing reports an error — it simply stops until a platform administrator signs it by override. Assign the role to a person in Admin → Users, or point the stage at a staffed role in Procurement → Settings.",
                              "الطلب الذي يصل إلى إحدى هذه المراحل ينتظر توقيعًا لا يملك أحد الدور اللازم لمنحه. لا يظهر أي خطأ — يتوقف الطلب ببساطة حتى يوقّعه مسؤول المنصة بصلاحية التجاوز. عيّن الدور لشخص من الإدارة ← المستخدمون، أو وجّه المرحلة إلى دور مشغول من المشتريات ← الإعدادات.",
                              "Bu basamaklardan birine ulaşan bir talep, kimsenin rolüne sahip olmadığı bir imzayı bekler. Hiçbir hata bildirilmez — bir platform yöneticisi geçersiz kılma ile imzalayana kadar öylece durur. Rolü Yönetim → Kullanıcılar bölümünden bir kişiye atayın veya aşamayı Satın Alma → Ayarlar bölümünden görevlendirilmiş bir role yönlendirin."),
 "gov.find.unregistered": ("Not in any role registry:", "غير مُسجَّل في أي سجل أدوار:",
                           "Hiçbir rol kaydında yok:"),
 "gov.yes": ("Yes", "نعم", "Evet"),
 "gov.no": ("No", "لا", "Hayır"),
}


PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + detail) if detail else ""))


# ===========================================================================
# App + the deliberately planted unguarded route
# ===========================================================================
app = create_app()
SELF_REGISTERED = "governance" not in app.blueprints
if SELF_REGISTERED:
    app.register_blueprint(gov.bp)


@app.route("/gov-planted-open-route")
def _gov_planted_open_route():
    return "planted"      # deliberately unguarded: no login, no permission


PAGES = ["/governance/", "/governance/users", "/governance/responsibility",
         "/governance/rules", "/governance/findings"]
EXPORTS = ["matrix", "extra_perms", "responsibility", "holders", "findings"]


def uid_of(username):
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    finally:
        conn.close()
    return row["id"] if row else None


def client(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["uid"] = uid
        s["ep"] = 0
    return c


def sql(stmt, args=()):
    conn = get_db()
    try:
        conn.execute(stmt, args)
        conn.commit()
    finally:
        conn.close()


with app.app_context():
    ADMIN_UID = uid_of("admin")
    DIRECTOR_UID = uid_of("director")       # it_director: access_admin, not '*'
    AGENT_UID = uid_of("agent")             # service_desk_agent: view_reports only


print("\n=== 1. blueprint & registration ===")
check("governance blueprint reachable (%s)"
      % ("registered by the test — app/__init__.py still needs the wiring"
         if SELF_REGISTERED else "already registered in app/__init__.py"),
      "governance" in app.blueprints)
check("the gate is access_admin, not view_reports", gov.PERM == "access_admin")


print("\n=== 2. matrix correctness against app.security ===")
with app.app_context():
    roles = {r["key"]: r for r in gov.role_rows()}
    sa = roles.get("super_admin")
    check("super_admin present and flagged as '*'", bool(sa) and sa["star"])
    check("every permission reads 'star' for super_admin",
          bool(sa) and all(gov.cell(sa, p) == "star" for p in sec.PERMISSIONS))
    nu = roles.get("normal_user")
    check("normal_user: view_dashboard granted, proc_admin not",
          gov.cell(nu, "view_dashboard") == "yes" and gov.cell(nu, "proc_admin") == "no")
    check("itsm_user: open_module granted, view_dashboard not",
          gov.cell(roles["itsm_user"], "open_module") == "yes"
          and gov.cell(roles["itsm_user"], "view_dashboard") == "no")
    mismatch = [(k, p) for k, r in roles.items() for p in sec.PERMISSIONS
                if (gov.cell(r, p) != "no") != sec.has_permission(k, p)]
    check("every cell agrees with security.has_permission (%d roles x %d perms)"
          % (len(roles), len(sec.PERMISSIONS)), not mismatch, str(mismatch[:3]))


print("\n=== 3. DB custom roles + edge cases ===")
with app.app_context():
    sql("INSERT INTO custom_roles (role_key,label,perms_json,is_builtin,created_at) "
        "VALUES (?,?,?,?,?)",
        ("gov_test_custom", "Gov Test Custom", json.dumps(["prob_import", "cmp_view"]),
         0, utcnow()))
    sql("INSERT INTO custom_roles (role_key,label,perms_json,is_builtin,created_at) "
        "VALUES (?,?,?,?,?)",
        ("gov_test_noperm", "Gov Test No Permissions", json.dumps([]), 0, utcnow()))
    sec.refresh_db_roles()
    roles = {r["key"]: r for r in gov.role_rows()}
    c = roles.get("gov_test_custom")
    check("DB custom role appears and is flagged custom",
          bool(c) and c["custom"] and not c["star"])
    check("DB custom role cells match its perms_json",
          gov.cell(c, "prob_import") == "yes" and gov.cell(c, "cmp_view") == "yes"
          and gov.cell(c, "proc_admin") == "no")
    e = roles.get("gov_test_noperm")
    check("edge: a role with NO permissions renders as all-'no'",
          bool(e) and not e["star"] and all(gov.cell(e, p) == "no" for p in sec.PERMISSIONS))

    # edge: users whose extra_perms are absent / blank / not JSON / not a list
    for raw, label in ((None, "NULL"), ("", "empty"), ("{oops", "invalid JSON"),
                       ('"proc_admin"', "JSON string, not a list")):
        check("edge: extra_perms %s parses to [] without raising" % label,
              gov._extra_list(raw) == [])
    check("edge: a user with no extra_perms is absent from the per-user view",
          all(u["extra"] for u in gov.extra_perm_users()))


print("\n=== 4. permission refusal (raw status codes) ===")
agent = client(AGENT_UID)
for path in PAGES:
    r = agent.get(path)
    check("service_desk_agent -> 403 on %s" % path, r.status_code == 403,
          "got %s" % r.status_code)
for key in EXPORTS:
    for ext in ("csv", "pdf"):
        r = agent.get("/governance/export/%s.%s" % (key, ext))
        check("service_desk_agent -> 403 on export/%s.%s" % (key, ext),
              r.status_code == 403, "got %s" % r.status_code)
check("the refusal is NOT a redirect to login (would mask the 403)",
      agent.get("/governance/").status_code == 403)

body = agent.get("/governance/").get_data(as_text=True)
leaked = [p for p in sec.PERMISSIONS if p in body]
check("a refused viewer sees no permission name at all", not leaked, str(leaked[:5]))
anon = app.test_client()
check("anonymous -> redirect to login, never the page",
      anon.get("/governance/").status_code in (301, 302))


print("\n=== 5. pages render for an admin ===")
admin = client(ADMIN_UID)
for path in PAGES:
    r = admin.get(path)
    check("200 %s" % path, r.status_code == 200, "got %s" % r.status_code)
r = admin.get("/governance/?group=Procurement&role=cfo")
check("matrix filter by group+role -> 200 and only that role",
      r.status_code == 200 and r.get_data(as_text=True).count("Chief Financial Officer") >= 1)
r = admin.get("/governance/?role=__nope__")
check("matrix with a filter that matches nothing -> 200 + empty state",
      r.status_code == 200 and "gov.empty.roles" in r.get_data(as_text=True))
check("it_director (access_admin, not '*') also gets 200",
      client(DIRECTOR_UID).get("/governance/").status_code == 200)


print("\n=== 6. exports ===")
for key in EXPORTS:
    r = admin.get("/governance/export/%s.csv" % key)
    ok = r.status_code == 200 and r.mimetype == "text/csv"
    check("CSV export %s -> 200 text/csv" % key, ok, "got %s %s" % (r.status_code, r.mimetype))
    if ok:
        check("CSV export %s starts with the UTF-8 BOM (Excel + Arabic)" % key,
              r.data.startswith(b"\xef\xbb\xbf"))
    r = admin.get("/governance/export/%s.pdf" % key)
    check("PDF export %s -> 200 application/pdf" % key,
          r.status_code == 200 and r.mimetype == "application/pdf",
          "got %s %s" % (r.status_code, r.mimetype))
    if r.status_code == 200:
        check("PDF export %s is a real PDF (binary, not markup)" % key,
              r.data.startswith(b"%PDF"))
check("unknown export key -> 404 (csv)",
      admin.get("/governance/export/nope.csv").status_code == 404)
check("unknown export key -> 404 (pdf)",
      admin.get("/governance/export/nope.pdf").status_code == 404)

with app.app_context():
    h, rows = gov.export_dataset("matrix")
    check("matrix CSV has a column per permission",
          len(h) == len(sec.PERMISSIONS) + 4 and h[4:] == list(sec.PERMISSIONS))
    sa_row = next(r for r in rows if r[0] == "super_admin")
    check("matrix CSV marks super_admin's grants as '*'",
          all(v == "*" for v in sa_row[4:]))


print("\n=== 7. findings — each category detects a planted case ===")
with app.app_context():
    # --- plant ---------------------------------------------------------
    # (a) no active '*' holder, so an unheld permission is possible at all
    sql("UPDATE users SET is_active = 0 WHERE role = 'super_admin'")
    # (b) a user whose extra_perms exceed their role
    sql("UPDATE users SET extra_perms = ? WHERE username = 'agent'",
        (json.dumps(["access_admin", "view_reports"]),))
    # (c) a permission nobody holds, granted by a role nobody holds
    holders = gov.permission_holders()
    orphan = next(p for p in sec.PERMISSIONS if holders.get(p, 0) == 0)
    sql("INSERT INTO custom_roles (role_key,label,perms_json,is_builtin,created_at) "
        "VALUES (?,?,?,?,?)",
        ("gov_test_orphan", "Gov Test Orphan", json.dumps([orphan]), 0, utcnow()))
    sec.refresh_db_roles()
    # (d) a registered permission no route enforces. Assembled at runtime so the
    #     literal never appears as a quoted token in any file the scanner reads.
    DEAD = "gov" + "_planted_" + "dead"
    sec.PERMISSIONS.append(DEAD)
    gov._SCAN.clear()

    f = gov.findings()
    unheld = {x["perm"] for x in f["unheld"]}
    check("planted: permission granted by a role but held by no active user (%s)" % orphan,
          orphan in unheld)
    check("planted: the granting role is named on the finding",
          any(x["perm"] == orphan and "gov_test_orphan" in x["roles"] for x in f["unheld"]))
    empty = {x["key"] for x in f["empty_roles"]}
    check("planted: role with no active users (gov_test_orphan)", "gov_test_orphan" in empty)
    check("planted: the zero-permission role is reported too",
          "gov_test_noperm" in empty)
    dead = {x["perm"]: x for x in f["dead"]}
    check("planted: permission no route enforces (%s)" % DEAD, DEAD in dead)
    check("planted: and it is correctly reported as referenced nowhere",
          DEAD in dead and dead[DEAD]["mentioned"] is False)
    open_rules = {x["rule"] for x in f["open_routes"]}
    check("planted: route with no permission check (/gov-planted-open-route)",
          "/gov-planted-open-route" in open_rules)
    check("planted: and it is flagged as having no login_required either",
          any(x["rule"] == "/gov-planted-open-route" and not x["login"]
              for x in f["open_routes"]))
    esc = {x["username"]: x for x in f["escalations"]}
    check("planted: user whose extra_perms exceed their role (agent)",
          "agent" in esc and "access_admin" in esc["agent"]["beyond"])
    check("an extra permission the role ALREADY has is not reported as an escalation",
          "agent" in esc and "view_reports" not in esc["agent"]["beyond"])
    check("single-holder continuity risk is detected (%d roles)"
          % len(f["single_roles"]), len(f["single_roles"]) > 0)
    check("a guarded route is NOT reported as open",
          "/governance/" not in open_rules)
    check("an enforced permission is NOT reported as dead", "access_admin" not in dead)

    # findings must survive being rendered, too
    sec.PERMISSIONS.remove(DEAD)
    gov._SCAN.clear()

check("findings page still 200 after the plants (director)",
      client(DIRECTOR_UID).get("/governance/findings").status_code == 200)
check("per-user grants page shows the planted grant",
      "access_admin" in client(DIRECTOR_UID).get("/governance/users").get_data(as_text=True))


print("\n=== 8. responsibility matrix derives from the permission names ===")
with app.app_context():
    modules, exceptions = gov.responsibility()
    by_name = {m["module"]: m for m in modules}
    proc = by_name.get("Procurement")
    check("Procurement: a role with proc_view is under 'can view'",
          bool(proc) and any(k == "purchasing_manager" for k, _ in proc["view"]))
    check("Procurement: a role with proc_approve is under 'can approve'",
          bool(proc) and any(k == "cfo" for k, _ in proc["approve"]))
    check("Procurement: proc_admin lands under 'can administer'",
          bool(proc) and any(k == "it_director" for k, _ in proc["admin"]))
    check("classify() follows the convention for _view/_manage/_approve/_admin",
          gov.classify("wh_view")[2:] == ("view", True)
          and gov.classify("wh_manage")[2:] == ("change", True)
          and gov.classify("plm_approve")[2:] == ("approve", True)
          and gov.classify("maint_admin")[2:] == ("admin", True))
    check("a non-conforming name is reported as an exception, never guessed silently",
          any(e["perm"] == "mes_entry" for e in exceptions)
          and gov.classify("mes_entry")[3] is False)
    check("the core platform permissions are declared exceptions too",
          any(e["perm"] == "view_dashboard" and e["module"] == "Platform (core)"
              for e in exceptions))
    check("every exception is either mapped or explicitly unclassified",
          all(("kind" in e) for e in exceptions))


print("\n=== 9. the rules are surfaced ===")
html = client(DIRECTOR_UID).get("/governance/rules").get_data(as_text=True)
with app.app_context():
    proc = gov.procurement_rules()
check("procurement ladder available", proc["ok"])
check("thresholds shown: finance 10,000 / cfo 25,000 / ceo 100,000",
      "10,000" in html and "25,000" in html and "100,000" in html)
check("the SoD admin-exemption state is on the page",
      ("gov.rules.exempt_on" in html) == bool(proc["sod_live"]))
check("sod_admin_exempt reads live from proc_settings", proc["sod_live"] in (True, False))
check("the escalation chain comes from proc_escalations, not the constants",
      proc["escalation_source"] == "proc_escalations", proc["escalation_source"])
check("a role with nobody above it is flagged", "gov.rules.nobody" in html)
with app.app_context():
    maint = gov.maintenance_rules()
check("maintenance ladder read from mnt_approval_matrix (%d rules)" % len(maint),
      len(maint) >= 1 and all(m["levels"] for m in maint))


print("\n=== 10. i18n — every data-i18n key resolves in en / ar / tr ===")
DICTS = {}
for lang in ("en", "ar", "tr"):
    with open(REPO / "app" / "static" / "i18n" / ("%s.json" % lang), encoding="utf-8") as fh:
        DICTS[lang] = json.load(fh)

missing_tr = [k for k, v in NEW_I18N.items()
              if len(v) != 3 or not all(isinstance(x, str) and x.strip() for x in v)]
check("every declared new key carries real EN + AR + TR", not missing_tr, str(missing_tr[:5]))

declared = set(NEW_I18N)
used = set()
for lang in ("en", "ar", "tr"):
    with app.app_context():
        sql("UPDATE users SET lang_pref = ? WHERE id = ?", (lang, DIRECTOR_UID))
    c = client(DIRECTOR_UID)
    for path in PAGES:
        r = c.get(path)
        check("200 %s in %s" % (path, lang), r.status_code == 200, "got %s" % r.status_code)
        page = r.get_data(as_text=True)
        keys = set(re.findall(r'data-i18n="([^"]+)"', page))
        used |= keys
        unresolved = sorted(k for k in keys if k not in DICTS[lang] and k not in declared)
        check("%s: every data-i18n key on %s resolves" % (lang, path),
              not unresolved, str(unresolved[:6]))
        if lang == "ar":
            check("ar: page is rendered RTL", 'dir="rtl"' in page)

template_keys = set()
for tpl in sorted((REPO / "app" / "templates" / "governance").glob("*.html")):
    template_keys |= set(re.findall(r'data-i18n="([^"]+)"',
                                    tpl.read_text(encoding="utf-8")))
check("every data-i18n key in the governance templates is declared here",
      not (template_keys - declared), str(sorted(template_keys - declared)[:6]))
check("no declared key is dead (every one is used in a template)",
      not (declared - template_keys), str(sorted(declared - template_keys)[:6]))
check("the pages actually exercised %d of the %d declared keys"
      % (len(declared & used), len(declared)), bool(declared & used))
gov_shipped = {k for k in DICTS["en"] if k.startswith("gov.")}
check("no gov.* key already shipped in en is missing from ar/tr",
      all(k in DICTS["ar"] and k in DICTS["tr"] for k in gov_shipped))
check("no declared key collides with a different existing en value",
      all(DICTS["en"].get(k, NEW_I18N[k][0]) == NEW_I18N[k][0] for k in NEW_I18N))


print("\n=== 11. no repo pollution ===")
check("the repo's platform.db was never created by this run",
      not (REPO / "platform.db").exists() or
      (REPO / "platform.db").stat().st_mtime < os.path.getmtime(str(TMP)))

print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
if FAIL:
    print("\nFAILURES:")
    for name in FAIL:
        print("  - " + name)
sys.exit(1 if FAIL else 0)
