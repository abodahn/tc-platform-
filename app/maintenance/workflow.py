"""
TC Platform — Maintenance "Workflow & Governance".

Admin-editable explanation text plus the handful of operational knobs the
maintenance workflow actually reads. Nothing here invents new behaviour: every
accessor is  DB override -> the constant the code used before.  With no override
rows the numbers returned are exactly the ones services.py / procure_bridge.py
read until now, so the ticket lifecycle, the spare-request ladder, the stock
reservation gates and the auto-reorder bridge behave identically.

A stored value that is blank, non-numeric, NaN/inf, out of range, an unknown role
or a role that cannot sign is treated as ABSENT and the constant wins -- a bad
edit can never empty an approval rung, hand it to somebody who cannot sign, or
silently disable a gate.

Tables use `id INTEGER PRIMARY KEY AUTOINCREMENT` + a UNIQUE natural key rather
than a bare TEXT primary key, because app/db.py's PostgreSQL shim appends
"RETURNING id" to every INSERT for tables outside its own _NO_ID_TABLES set
(db.py is not ours to edit). Uniqueness -- and therefore INSERT OR IGNORE -- is
unchanged.
"""
import math

from app.maintenance.constants import (MAINT_ROLE_LABELS, MAINT_ROLE_PERMS,
                                       TICKET_STATUSES, TICKET_TRANSITIONS)

# --- defaults that used to be literals in the workflow code -----------------
# SLA target scales with priority (critical is far tighter than the base hours).
SLA_FACTOR = {"critical": 0.25, "high": 0.5, "medium": 1.0, "low": 2.0}
# Request value at/above which _pick_matrix_levels switched to the longer ladder.
CRITICAL_COST_DEFAULT = 100.0
# _pick_matrix_levels' own fallback when no matrix rule can be read.
DEFAULT_LEVELS = "maintenance_manager,storekeeper"
# The storekeeper ISSUES approved parts; they were never an approval vote, and
# making them one would both break segregation of duties and (because the picker
# strips them from the levels string) silently delete the rung.
ISSUER_ROLE = "storekeeper"

# key -> (default, kind, min, max). Bounds reject nonsense on read AND on write.
SETTINGS = {
    "response_sla_hours":      (4.0, "num", 0.01, 87600.0),
    "resolution_sla_hours":    (24.0, "num", 0.01, 87600.0),
    "sla_factor_critical":     (SLA_FACTOR["critical"], "num", 0.001, 100.0),
    "sla_factor_high":         (SLA_FACTOR["high"], "num", 0.001, 100.0),
    "sla_factor_medium":       (SLA_FACTOR["medium"], "num", 0.001, 100.0),
    "sla_factor_low":          (SLA_FACTOR["low"], "num", 0.001, 100.0),
    "critical_cost_threshold": (CRITICAL_COST_DEFAULT, "num", 0.0, 1e9),
    "auto_reorder_pr":         (True, "bool", None, None),
    "duplicate_ticket_guard":  (True, "bool", None, None),
    # DOAM §6 engineering-justification gate. Ships OFF: switching it on stops
    # every spares / MRO requisition that has no Engineering-Head-signed report,
    # so an existing site must turn it on deliberately, not discover it on
    # upgrade morning. eng_justification.gate_enabled reads it through flag().
    "ejr_gate":                (False, "bool", None, None),
}

# Where each knob's default comes from, and who reads it (shown on the page).
SETTING_SOURCE = {
    "response_sla_hours": ("workflow.SETTINGS (4h)", "services.create_ticket"),
    "resolution_sla_hours": ("workflow.SETTINGS (24h)", "services.create_ticket, ai.py"),
    "sla_factor_critical": ("workflow.SLA_FACTOR", "services.create_ticket"),
    "sla_factor_high": ("workflow.SLA_FACTOR", "services.create_ticket"),
    "sla_factor_medium": ("workflow.SLA_FACTOR", "services.create_ticket"),
    "sla_factor_low": ("workflow.SLA_FACTOR", "services.create_ticket"),
    # identifiers only: this column is read in every language, so no English prose
    "critical_cost_threshold": ("mnt_approval_matrix.cost_threshold | 100",
                                "services._pick_matrix_levels"),
    "auto_reorder_pr": ("on", "procure_bridge._enabled"),
    "duplicate_ticket_guard": ("on", "services.create_ticket"),
    "ejr_gate": ("off", "eng_justification.ejr_gate_check"),
}

RULE_KEYS = ("std", "crit")

WF_SCHEMA = """
CREATE TABLE IF NOT EXISTS mnt_stage_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stage TEXT UNIQUE NOT NULL,
    role TEXT, explanation TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mnt_role_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role_key TEXT UNIQUE NOT NULL,
    explanation TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mnt_status_meta (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT UNIQUE NOT NULL,
    explanation TEXT, updated_by TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mnt_doc (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT UNIQUE NOT NULL,
    body TEXT, updated_by TEXT, updated_at TEXT
);
"""

# Trilingual prose, added after first release: the English column is NEVER touched,
# each language gets its own nullable column. Applied as idempotent ALTERs on every
# boot exactly like schema._MIGRATIONS -- on an already-migrated database the ALTER
# raises and the rollback makes it a no-op.
WF_MIGRATIONS = [
    "ALTER TABLE mnt_status_meta ADD COLUMN explanation_ar TEXT",
    "ALTER TABLE mnt_status_meta ADD COLUMN explanation_tr TEXT",
    "ALTER TABLE mnt_role_meta ADD COLUMN explanation_ar TEXT",
    "ALTER TABLE mnt_role_meta ADD COLUMN explanation_tr TEXT",
    "ALTER TABLE mnt_stage_meta ADD COLUMN explanation_ar TEXT",
    "ALTER TABLE mnt_stage_meta ADD COLUMN explanation_tr TEXT",
    "ALTER TABLE mnt_doc ADD COLUMN body_ar TEXT",
    "ALTER TABLE mnt_doc ADD COLUMN body_tr TEXT",
]

LANGS = ("en", "ar", "tr")

# --- seeded default text (read off the code; admins may rewrite any of it) ---
STATUS_TEXT = {
    "draft": "Saved but not submitted. Nobody is notified and the SLA breach sweep "
             "skips drafts.",
    "submitted": "Raised; the maintenance manager was notified. The response and "
                 "resolution SLA clocks started at creation, scaled by priority.",
    "under_review": "The maintenance manager is triaging: confirm the fault, set the "
                    "priority, decide who repairs it. Stamps reviewed_at.",
    "assigned": "A technician and a work-order number are attached. Stamps assigned_at.",
    "diagnosis": "The technician is inspecting the machine and recording fault, root "
                 "cause and required action. Stamps diag_started_at.",
    "spare_required": "Diagnosis says a spare part is needed; the next step is a "
                      "spare-part request.",
    "waiting_stock": "At least one requested part is not available (on hand minus "
                     "reserved), so nothing can be approved for issue until stock "
                     "arrives. Storekeeper and maintenance manager were notified.",
    "waiting_approval": "A spare-part request is on the approval ladder. Stamps "
                        "approval_started_at.",
    "approved_issue": "Every rung signed and the parts are RESERVED for this ticket, "
                      "waiting for the storekeeper to issue them. Stamps approval_done_at.",
    "rejected": "The ticket or its parts request was refused with a written reason; any "
                "reservation this ticket held was released.",
    "parts_issued": "The storekeeper issued the parts: stock decreased, an issue voucher "
                    "was written, the reservation released and the parts value added to "
                    "the ticket cost. Stamps issued_at.",
    "repair": "The technician is carrying out the repair. Stamps repair_started_at.",
    "testing": "Repair done; the machine is being tested and handed back to production. "
               "Stamps repair_done_at.",
    "resolved": "Tested and working, awaiting the maintenance manager's closure. Stamps "
                "testing_done_at; the SLA breach sweep stops here.",
    "closed": "Closed by the maintenance manager. Downtime is measured from creation to "
              "closure; the machine returns to running; cost, downtime and the breakdown "
              "count roll into the machine ONCE (machine_rolled). Approved-but-unissued "
              "reservations are released.",
    "cancelled": "Abandoned. Terminal - nothing can follow it. Reservations released.",
    "reopened": "The fault came back after closure. The machine goes back to "
                "under_maintenance and the cost/downtime roll-up is NOT repeated. "
                "Stamps reopened_at.",
}

ROLE_TEXT = {
    "maintenance_manager": "Reviews and assigns tickets, closes resolved work, owns "
                           "preventive maintenance, and signs the first rung of the "
                           "spare-request ladder.",
    "maintenance_technician": "Diagnoses the fault, requests spare parts, records the "
                             "repair proof and the test result.",
    "storekeeper": "Runs the spare-parts store: issues approved parts, receives stock, "
                   "adjusts stock. Deliberately the ISSUER and not an approval rung.",
    "production_supervisor": "Raises tickets from the floor and takes the machine back "
                             "after testing.",
    "factory_manager": "Signs the extra rung on critical or high-value spare requests.",
    "production_manager": "Raises tickets from production and can sign approvals.",
    "it_director": "Administers the module: settings, master data, approval matrix.",
    "it_manager": "Administers module settings.",
    "finance_user": "Read-only view of maintenance cost and history.",
    "executive_viewer": "Read-only view of the maintenance dashboards.",
}

STAGE_TEXT = {
    "std_l1": "Level 1 of a normal spare request: the maintenance manager confirms the "
              "part and the quantity are really needed for this ticket. Only the role "
              "that owns this rung may sign it.",
    "crit_l1": "Level 1 of a critical or high-value request: the maintenance manager "
               "confirms the part and the quantity.",
    "crit_l2": "Level 2 of a critical or high-value request: the factory manager "
               "authorises the spend. The request is approved only once EVERY rung has "
               "signed.",
}

# Rung names, shown on the ladder (translated in the UI as mnt.stage.<stage>).
STAGE_LABELS = {
    "std_l1": "Standard route — level 1",
    "crit_l1": "Critical route — level 1",
    "crit_l2": "Critical route — level 2",
}

DOC_SECTIONS = ("lifecycle", "reservation", "auto_reorder", "sla", "costing")
# Rules-panel headings (translated in the UI as mnt.rule.<section>).
DOC_LABELS = {
    "lifecycle": "How a ticket moves",
    "reservation": "Stock reservation",
    "auto_reorder": "Automatic reorder (PR)",
    "sla": "SLA clocks",
    "costing": "Part costing",
}
DOC_TEXT = {
    "lifecycle": "A ticket walks the fixed lifecycle below. Each move is checked against "
                 "the allowed transitions, is written to the audit trail with who and "
                 "when, and stamps its own timestamp column. Only a spare-part need "
                 "diverts the ticket into the approval ladder; everything else goes "
                 "diagnosis -> repair -> testing -> resolved -> closed.",
    "reservation": "available = on hand - reserved. Approving a request reserves the "
                   "parts atomically (one conditional UPDATE per line that only succeeds "
                   "while the part is still available), so two approvals can never claim "
                   "the same physical unit. If any line cannot be reserved the whole "
                   "batch is rolled back to out-of-stock. A reservation is released on "
                   "issue, on rejection and on ticket close, so stock is never stranded.",
    "auto_reorder": "A low spare raises an unpriced purchase requisition automatically "
                    "when auto_reorder_pr is on: when available stock falls to or below "
                    "the reorder level, one PR per spare is sent to Procurement's pricing "
                    "gate and the normal procurement ladder. It is deduplicated against "
                    "any open PR for that spare, and a goods receipt on that PR posts "
                    "straight back into spare stock.",
    "sla": "The response and resolution clocks start when the ticket is created, not at "
           "assignment. Base hours come from the settings below and are multiplied by "
           "the priority factor (critical is the tightest). Any open ticket past its "
           "resolution due time is flagged sla_breach once and the maintenance manager "
           "is notified.",
    "costing": "Receiving stock moves the part's cost by weighted average: "
               "(old_qty x old_avg + received_qty x price) / (old_qty + received_qty). "
               "Issuing parts charges qty x avg_cost to the ticket, and closing the "
               "ticket rolls that cost into the machine's cost-to-date exactly once. "
               "Part cost therefore drives both approval routing and cost reporting.",
}

# --- the same prose in Arabic and Turkish ----------------------------------
# Translated from the English rows above, which were written off what the code
# actually enforces; the sense of every financial / stock control is kept exact.
# Seeded into the *_ar / *_tr columns ONLY where they are still NULL (never
# translated), so neither an admin's own wording nor a box he emptied on purpose
# is ever overwritten (see ensure()).
STATUS_TEXT_AR = {
    "draft": "محفوظة ولم تُرسَل بعد. لا يُبلَّغ أحد، وفحص خرق SLA يتجاهل المسودات.",
    "submitted": "تم رفع التذكرة وإبلاغ مدير الصيانة. بدأ عدّاد SLA للاستجابة والإصلاح من "
                 "لحظة الإنشاء، مضروبًا في معامل الأولوية.",
    "under_review": "مدير الصيانة يراجع التذكرة: تأكيد العطل، وتحديد الأولوية، واختيار من "
                    "يقوم بالإصلاح. تُسجَّل في reviewed_at.",
    "assigned": "تم إسناد التذكرة إلى فنّي وربطها برقم أمر عمل. تُسجَّل في assigned_at.",
    "diagnosis": "الفنّي يفحص الماكينة ويسجّل العطل والسبب الجذري والإجراء المطلوب. تُسجَّل "
                 "في diag_started_at.",
    "spare_required": "التشخيص يفيد بالحاجة إلى قطعة غيار؛ والخطوة التالية هي طلب قطع غيار.",
    "waiting_stock": "قطعة مطلوبة واحدة على الأقل غير متاحة (الرصيد الفعلي ناقص المحجوز)، "
                     "فلا يمكن اعتماد أي صرف حتى يصل المخزون. تم إبلاغ أمين المخزن ومدير "
                     "الصيانة.",
    "waiting_approval": "طلب قطع الغيار على سلم الموافقات. تُسجَّل في approval_started_at.",
    "approved_issue": "وقّعت كل الدرجات، والقطع محجوزة لهذه التذكرة في انتظار صرفها من "
                      "أمين المخزن. تُسجَّل في approval_done_at.",
    "rejected": "تم رفض التذكرة أو طلب قطعها بسبب مكتوب، وتم تحرير أي حجز كان مرتبطًا بها.",
    "parts_issued": "صرف أمين المخزن القطع: نقص المخزون، وصدر إذن صرف، وتم تحرير الحجز، "
                    "وأُضيفت قيمة القطع إلى تكلفة التذكرة. تُسجَّل في issued_at.",
    "repair": "الفنّي ينفّذ الإصلاح. تُسجَّل في repair_started_at.",
    "testing": "انتهى الإصلاح؛ ويجري اختبار الماكينة وتسليمها للإنتاج. تُسجَّل في "
               "repair_done_at.",
    "resolved": "تم الاختبار والماكينة تعمل، في انتظار إغلاق مدير الصيانة. تُسجَّل في "
                "testing_done_at، ويتوقف عندها فحص خرق SLA.",
    "closed": "أغلقها مدير الصيانة. يُحسب التوقف من الإنشاء حتى الإغلاق، وتعود الماكينة إلى "
              "التشغيل، وتُضاف التكلفة والتوقف وعدد الأعطال إلى سجل الماكينة مرة واحدة فقط "
              "(machine_rolled). ويُحرَّر أي حجز معتمد لم يُصرف.",
    "cancelled": "أُلغيت. حالة نهائية — لا شيء يأتي بعدها. ويُحرَّر أي حجز.",
    "reopened": "عاد العطل بعد الإغلاق. تعود الماكينة إلى حالة تحت الصيانة، ولا يُعاد ترحيل "
                "التكلفة والتوقف مرة أخرى. تُسجَّل في reopened_at.",
}

STATUS_TEXT_TR = {
    "draft": "Kaydedildi ancak gönderilmedi. Kimseye bildirim gitmez ve SLA ihlali "
             "taraması taslakları atlar.",
    "submitted": "Talep açıldı; bakım müdürüne bildirim gitti. Yanıt ve çözüm SLA saatleri "
                 "oluşturma anında başladı ve önceliğe göre ölçeklendi.",
    "under_review": "Bakım müdürü değerlendiriyor: arızayı doğrular, önceliği belirler, "
                    "onarımı kimin yapacağına karar verir. reviewed_at damgalanır.",
    "assigned": "Bir teknisyen ve iş emri numarası eklendi. assigned_at damgalanır.",
    "diagnosis": "Teknisyen makineyi inceliyor; arızayı, kök nedeni ve gereken işlemi "
                 "kaydediyor. diag_started_at damgalanır.",
    "spare_required": "Teşhis, yedek parça gerektiğini gösteriyor; sonraki adım yedek "
                      "parça talebidir.",
    "waiting_stock": "İstenen parçalardan en az biri müsait değil (eldeki stok eksi "
                     "rezerve), bu yüzden stok gelmeden hiçbir çıkış onaylanamaz. Depo "
                     "sorumlusu ve bakım müdürü bilgilendirildi.",
    "waiting_approval": "Yedek parça talebi onay merdiveninde. approval_started_at "
                        "damgalanır.",
    "approved_issue": "Her basamak imzalandı ve parçalar bu talep için REZERVE edildi; "
                      "depo sorumlusunun çıkış yapmasını bekliyor. approval_done_at "
                      "damgalanır.",
    "rejected": "Talep veya parça isteği yazılı gerekçeyle reddedildi; bu talebin tuttuğu "
                "rezerve serbest bırakıldı.",
    "parts_issued": "Depo sorumlusu parçaları verdi: stok düştü, çıkış fişi yazıldı, "
                    "rezerve serbest kaldı ve parça değeri talep maliyetine eklendi. "
                    "issued_at damgalanır.",
    "repair": "Teknisyen onarımı yapıyor. repair_started_at damgalanır.",
    "testing": "Onarım tamam; makine test ediliyor ve üretime teslim ediliyor. "
               "repair_done_at damgalanır.",
    "resolved": "Test edildi ve çalışıyor; bakım müdürünün kapatmasını bekliyor. "
                "testing_done_at damgalanır; SLA ihlali taraması burada durur.",
    "closed": "Bakım müdürü kapattı. Duruş, oluşturmadan kapanışa kadar ölçülür; makine "
              "çalışır duruma döner; maliyet, duruş ve arıza sayısı makineye SADECE BİR KEZ "
              "işlenir (machine_rolled). Onaylanmış ama çıkılmamış rezerveler serbest "
              "bırakılır.",
    "cancelled": "Vazgeçildi. Son durum — sonrasında hiçbir adım yok. Rezerveler serbest "
                 "bırakılır.",
    "reopened": "Arıza kapanıştan sonra tekrar etti. Makine bakımda durumuna geri döner ve "
                "maliyet/duruş aktarımı TEKRARLANMAZ. reopened_at damgalanır.",
}

ROLE_TEXT_AR = {
    "maintenance_manager": "يراجع التذاكر ويسندها، ويغلق الأعمال المنتهية، ويتولى الصيانة "
                           "الوقائية، ويوقّع الدرجة الأولى في سلم طلب قطع الغيار.",
    "maintenance_technician": "يشخّص العطل، ويطلب قطع الغيار، ويسجّل إثبات الإصلاح ونتيجة "
                              "الاختبار.",
    "storekeeper": "يدير مخزن قطع الغيار: يصرف القطع المعتمدة، ويستلم المخزون، ويجري "
                   "التسويات. وهو الصارف عن قصد وليس درجة موافقة.",
    "production_supervisor": "يفتح التذاكر من صالة الإنتاج، ويستلم الماكينة بعد الاختبار.",
    "factory_manager": "يوقّع الدرجة الإضافية في طلبات قطع الغيار الحرجة أو مرتفعة القيمة.",
    "production_manager": "يفتح التذاكر من الإنتاج ويمكنه توقيع الموافقات.",
    "it_director": "يدير الوحدة: الإعدادات والبيانات الأساسية ومصفوفة الموافقات.",
    "it_manager": "يدير إعدادات الوحدة.",
    "finance_user": "عرض للقراءة فقط لتكلفة الصيانة وسجلها.",
    "executive_viewer": "عرض للقراءة فقط للوحات متابعة الصيانة.",
}

ROLE_TEXT_TR = {
    "maintenance_manager": "Talepleri inceler ve atar, çözülen işleri kapatır, önleyici "
                           "bakımdan sorumludur ve yedek parça merdiveninin ilk basamağını "
                           "imzalar.",
    "maintenance_technician": "Arızayı teşhis eder, yedek parça ister, onarım kanıtını ve "
                              "test sonucunu kaydeder.",
    "storekeeper": "Yedek parça deposunu yürütür: onaylı parçaları verir, stok girişi "
                   "yapar, stok düzeltir. Bilinçli olarak ÇIKIŞI YAPAN taraftır, onay "
                   "basamağı değildir.",
    "production_supervisor": "Sahadan talep açar ve testten sonra makineyi geri alır.",
    "factory_manager": "Kritik veya yüksek değerli yedek parça taleplerindeki ek basamağı "
                       "imzalar.",
    "production_manager": "Üretimden talep açar ve onayları imzalayabilir.",
    "it_director": "Modülü yönetir: ayarlar, ana veriler, onay matrisi.",
    "it_manager": "Modül ayarlarını yönetir.",
    "finance_user": "Bakım maliyeti ve geçmişini yalnızca görüntüler.",
    "executive_viewer": "Bakım panolarını yalnızca görüntüler.",
}

STAGE_TEXT_AR = {
    "std_l1": "الدرجة الأولى في طلب قطع غيار عادي: يؤكد مدير الصيانة أن القطعة والكمية "
              "لازمتان فعلًا لهذه التذكرة. ولا يوقّع هذه الدرجة إلا الدور المالك لها.",
    "crit_l1": "الدرجة الأولى في طلب حرج أو مرتفع القيمة: يؤكد مدير الصيانة القطعة والكمية.",
    "crit_l2": "الدرجة الثانية في طلب حرج أو مرتفع القيمة: يعتمد مدير المصنع الإنفاق. ولا "
               "يُعتمد الطلب إلا بعد توقيع كل الدرجات.",
}

STAGE_TEXT_TR = {
    "std_l1": "Normal bir yedek parça talebinin 1. seviyesi: bakım müdürü, parçanın ve "
              "miktarın bu talep için gerçekten gerekli olduğunu doğrular. Bu basamağı "
              "yalnızca sahibi olan rol imzalayabilir.",
    "crit_l1": "Kritik veya yüksek değerli bir talebin 1. seviyesi: bakım müdürü parçayı "
               "ve miktarı doğrular.",
    "crit_l2": "Kritik veya yüksek değerli bir talebin 2. seviyesi: fabrika müdürü "
               "harcamayı onaylar. Talep, ancak TÜM basamaklar imzalandığında onaylanır.",
}

DOC_TEXT_AR = {
    "lifecycle": "تسير التذكرة في دورة الحياة الثابتة الموضحة أدناه. يُفحص كل انتقال مقابل "
                 "الانتقالات المسموح بها، ويُكتب في سجل التدقيق بمن قام به ومتى، ويُسجَّل في "
                 "عمود التوقيت الخاص به. ولا يحوّل التذكرة إلى سلم الموافقات إلا الحاجة إلى "
                 "قطعة غيار؛ وما عدا ذلك يسير: تشخيص ← إصلاح ← اختبار ← تم الحل ← إغلاق.",
    "reservation": "المتاح = الرصيد الفعلي ناقص المحجوز. اعتماد الطلب يحجز القطع بشكل ذرّي "
                   "(تحديث شرطي واحد لكل بند لا ينجح إلا ما دامت القطعة متاحة)، فلا يمكن "
                   "أبدًا أن يطالب اعتمادان بنفس الوحدة الفعلية. وإذا تعذّر حجز أي بند تُرجَع "
                   "الدفعة بالكامل إلى حالة عدم التوفر. ويُحرَّر الحجز عند الصرف وعند الرفض "
                   "وعند إغلاق التذكرة، فلا يبقى المخزون معلَّقًا.",
    "auto_reorder": "عند تفعيل auto_reorder_pr ترفع قطعة الغيار المنخفضة طلب شراء (PR) بدون "
                    "سعر تلقائيًا: فحين يهبط المتاح إلى حد إعادة الطلب أو أقل، يُرسل PR واحد "
                    "لكل قطعة إلى بوابة التسعير في المشتريات ثم إلى سلم موافقات المشتريات "
                    "المعتاد. ويُستبعد التكرار مقابل أي PR مفتوح لنفس القطعة، ويُرحَّل استلام "
                    "البضاعة على ذلك PR مباشرة إلى مخزون قطع الغيار.",
    "sla": "يبدأ عدّاد الاستجابة وعدّاد الإصلاح من لحظة إنشاء التذكرة، وليس من لحظة الإسناد. "
           "تُؤخذ الساعات الأساسية من الإعدادات أدناه وتُضرب في معامل الأولوية (الحرج هو "
           "الأضيق). وأي تذكرة مفتوحة تجاوزت موعد إصلاحها تُعلَّم sla_breach مرة واحدة "
           "ويُبلَّغ مدير الصيانة.",
    "costing": "استلام المخزون يحرّك تكلفة القطعة بالمتوسط المرجّح: (الكمية القديمة × "
               "المتوسط القديم + الكمية المستلمة × السعر) ÷ (الكمية القديمة + الكمية "
               "المستلمة). وصرف القطع يحمّل التذكرة بالكمية × متوسط التكلفة، وإغلاق التذكرة "
               "يرحّل هذه التكلفة إلى تكلفة الماكينة حتى تاريخه مرة واحدة بالضبط. ولذلك "
               "تحدّد تكلفة القطعة مسار الموافقات وتقارير التكلفة معًا.",
}

DOC_TEXT_TR = {
    "lifecycle": "Bir talep aşağıdaki sabit yaşam döngüsünü izler. Her geçiş izinli "
                 "geçişlere karşı denetlenir, kim ve ne zaman bilgisiyle denetim izine "
                 "yazılır ve kendi zaman damgası kolonunu doldurur. Talebi onay "
                 "merdivenine yalnızca yedek parça ihtiyacı yönlendirir; bunun dışındaki "
                 "akış teşhis → onarım → test → çözüldü → kapalı şeklindedir.",
    "reservation": "müsait = eldeki stok eksi rezerve. Bir talebin onaylanması parçaları "
                   "atomik olarak rezerve eder (her satır için, parça hâlâ müsaitken "
                   "başarılı olan tek bir koşullu UPDATE); böylece iki onay asla aynı "
                   "fiziksel adedi talep edemez. Herhangi bir satır rezerve edilemezse tüm "
                   "parti stok yok durumuna geri alınır. Rezerve; çıkışta, rette ve talep "
                   "kapanışında serbest bırakılır, böylece stok asla bloke kalmaz.",
    "auto_reorder": "auto_reorder_pr açıkken azalan bir yedek parça otomatik olarak "
                    "fiyatsız bir satın alma talebi (PR) açar: müsait stok sipariş "
                    "seviyesine düştüğünde veya altına indiğinde, parça başına bir PR "
                    "Satın Alma'nın fiyatlandırma kapısına ve normal satın alma "
                    "merdivenine gider. Aynı parça için açık bir PR varsa mükerrer kayıt "
                    "engellenir ve o PR'ın mal girişi doğrudan yedek parça stoğuna işlenir.",
    "sla": "Yanıt ve çözüm saatleri, atama anında değil talep oluşturulduğunda başlar. "
           "Temel saatler aşağıdaki ayarlardan alınır ve öncelik katsayısıyla çarpılır (en "
           "sıkısı kritiktir). Çözüm süresi geçen her açık talep bir kez sla_breach olarak "
           "işaretlenir ve bakım müdürüne bildirim gider.",
    "costing": "Stok girişi, parçanın maliyetini ağırlıklı ortalama ile günceller: (eski "
               "miktar × eski ortalama + gelen miktar × fiyat) ÷ (eski miktar + gelen "
               "miktar). Parça çıkışı, talebe miktar × ortalama maliyet tutarını yazar; "
               "talebin kapatılması bu maliyeti makinenin güncel maliyetine tam olarak bir "
               "kez aktarır. Bu nedenle parça maliyeti hem onay yönlendirmesini hem de "
               "maliyet raporlamasını belirler.",
}

# --- short LABELS (not prose): enum / role / knob names --------------------
# The lifecycle statuses already have a translated platform dictionary
# (mx.<value>, swapped client-side by the |mhuman filter), so they are NOT here.
# Everything below has no dictionary key at all, so it ships its own three
# languages and is emitted as data-loc-en/ar/tr — the attribute trio app.js
# already uses for dynamic content, so a language switch re-swaps it without a
# reload and a missing dictionary key can never surface as a raw i18n key.
# (en, ar, tr); en=None means "keep the English the caller already has"
# (a role label and a matrix rule name are admin-editable master data).
LABEL_I18N = {
    "stage": {
        "std_l1": (None, "المسار العادي — الدرجة 1", "Standart yol — 1. seviye"),
        "crit_l1": (None, "المسار الحرج — الدرجة 1", "Kritik yol — 1. seviye"),
        "crit_l2": (None, "المسار الحرج — الدرجة 2", "Kritik yol — 2. seviye"),
    },
    "rule": {
        "lifecycle": (None, "كيف تسير التذكرة", "Bir talep nasıl ilerler"),
        "reservation": (None, "حجز المخزون", "Stok rezervasyonu"),
        "auto_reorder": (None, "إعادة الطلب التلقائي (PR)", "Otomatik yeniden sipariş (PR)"),
        "sla": (None, "عدّادات SLA", "SLA saatleri"),
        "costing": (None, "تكلفة القطعة", "Parça maliyeti"),
    },
    # the configurable knobs: humanising the key gave "Response Sla Hours"
    "setting": {
        "response_sla_hours": ("Response SLA hours", "ساعات SLA للاستجابة",
                               "Yanıt SLA saati"),
        "resolution_sla_hours": ("Resolution SLA hours", "ساعات SLA للإصلاح",
                                 "Çözüm SLA saati"),
        "sla_factor_critical": ("SLA factor — critical", "معامل SLA — أولوية حرجة",
                                "SLA katsayısı — kritik"),
        "sla_factor_high": ("SLA factor — high", "معامل SLA — أولوية عالية",
                            "SLA katsayısı — yüksek"),
        "sla_factor_medium": ("SLA factor — medium", "معامل SLA — أولوية متوسطة",
                              "SLA katsayısı — orta"),
        "sla_factor_low": ("SLA factor — low", "معامل SLA — أولوية منخفضة",
                           "SLA katsayısı — düşük"),
        "critical_cost_threshold": ("Critical-ladder value threshold",
                                    "حد القيمة للمسار الحرج",
                                    "Kritik merdiven değer eşiği"),
        "auto_reorder_pr": ("Automatic reorder PR", "طلب شراء تلقائي عند نقص المخزون",
                            "Otomatik yeniden sipariş PR'ı"),
        "duplicate_ticket_guard": ("Duplicate-ticket guard", "منع التذاكر المكرّرة",
                                   "Mükerrer talep engeli"),
        "ejr_gate": ("Engineering justification gate (DOAM §6)",
                     "بوابة التبرير الهندسي (DOAM §6)",
                     "Mühendislik gerekçe kontrolü (DOAM §6)"),
    },
    "role": {
        "maintenance_manager": (None, "مدير الصيانة", "Bakım Müdürü"),
        "maintenance_technician": (None, "فنّي الصيانة", "Bakım Teknisyeni"),
        "storekeeper": (None, "أمين المخزن", "Depo Sorumlusu"),
        "production_supervisor": (None, "مشرف الإنتاج", "Üretim Şefi"),
        "factory_manager": (None, "مدير المصنع", "Fabrika Müdürü"),
        "production_manager": (None, "مدير الإنتاج", "Üretim Müdürü"),
        "it_director": (None, "مدير تقنية المعلومات", "BT Direktörü"),
        "it_manager": (None, "مسؤول تقنية المعلومات", "BT Yöneticisi"),
        "finance_user": (None, "مستخدم الشؤون المالية", "Finans Kullanıcısı"),
        "executive_viewer": (None, "مشاهد تنفيذي", "Yönetici İzleyici"),
    },
    # the permission CODE stays on the tag's title so an admin can still match it
    # against Admin > Roles; the tag itself reads in the reader's language.
    "perm": {
        "maint_view": ("View maintenance", "عرض الصيانة", "Bakımı görüntüleme"),
        "maint_ticket_create": ("Raise tickets", "فتح التذاكر", "Talep açma"),
        "maint_technician": ("Technician work", "أعمال الفنّي", "Teknisyen işleri"),
        "maint_manage": ("Manage tickets & PM", "إدارة التذاكر والصيانة الوقائية",
                         "Talep ve önleyici bakım yönetimi"),
        "maint_approve": ("Sign approvals", "توقيع الموافقات", "Onay imzalama"),
        "maint_store": ("Run the spare store", "إدارة مخزن قطع الغيار",
                        "Yedek parça deposu"),
        "maint_admin": ("Administer the module", "إدارة الوحدة", "Modül yönetimi"),
    },
    # mnt_approval_matrix.name — keyed by the seeded English name; a rule an admin
    # renamed is not in this map and keeps its stored name in all three languages.
    "matrix": {
        "Default spare issue": (None, "صرف قطع غيار عادي",
                                "Standart yedek parça çıkışı"),
        "Critical/expensive spare issue": (None, "صرف قطع غيار حرجة أو مرتفعة القيمة",
                                           "Kritik/pahalı yedek parça çıkışı"),
    },
    # the admin editor's own labels
    "ui": {
        "expl_en": ("Explanation (English)", "الشرح (بالإنجليزية)",
                    "Açıklama (İngilizce)"),
        "expl_ar": ("Explanation (Arabic)", "الشرح (بالعربية)", "Açıklama (Arapça)"),
        "expl_tr": ("Explanation (Turkish)", "الشرح (بالتركية)", "Açıklama (Türkçe)"),
        "editor_hint": ("Leave a language empty and its readers see the English text.",
                        "اترك أي لغة فارغة فيرى قرّاؤها النص الإنجليزي.",
                        "Bir dili boş bırakın; o dilin okuyucuları İngilizce metni görür."),
    },
}


def label(kind, key, en=None):
    """{'en','ar','tr'} for one short label. A key with no translation (a custom
    role, a renamed matrix rule, a permission added later) falls back to the
    English it was given in all three languages, so it shows the literal text
    instead of a blank or a raw key."""
    row = LABEL_I18N.get(kind, {}).get(key)
    base = en if (en not in (None, "")) else str(key).replace("_", " ").title()
    if not row:
        return {"en": base, "ar": base, "tr": base}
    base = row[0] or base
    return {"en": base, "ar": row[1] or base, "tr": row[2] or base}


# kind -> lang -> {key: text}. English lives in _TEXT_TABLES' defaults.
_TEXT_I18N = {
    "status": {"ar": STATUS_TEXT_AR, "tr": STATUS_TEXT_TR},
    "role": {"ar": ROLE_TEXT_AR, "tr": ROLE_TEXT_TR},
    "stage": {"ar": STAGE_TEXT_AR, "tr": STAGE_TEXT_TR},
    "doc": {"ar": DOC_TEXT_AR, "tr": DOC_TEXT_TR},
}


# --- schema ----------------------------------------------------------------
def ensure(conn):
    """Create the workflow tables, add the per-language columns and seed the default
    text in all three languages. Idempotent: INSERT OR IGNORE never overwrites text
    an admin has since edited, and a translation is only written into a column that
    is still NULL.

    NULL and '' are deliberately different: NULL means "never translated" and the
    seed may fill it, '' means an admin emptied that box on purpose so his readers
    fall back to the English (what the editor hint promises). Seeding a blank would
    hand him the shipped translation back on the next restart."""
    try:
        conn.executescript(WF_SCHEMA)
        conn.commit()
    except Exception:
        conn.rollback()
        return
    for ddl in WF_MIGRATIONS:
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            conn.rollback()          # column already there -> nothing to do
    for kind, (table, keycol, valcol, data) in _TEXT_TABLES.items():
        for k, v in data.items():
            try:
                conn.execute("INSERT OR IGNORE INTO %s (%s,%s) VALUES (?,?)"
                             % (table, keycol, valcol), (k, v))
            except Exception:
                conn.rollback()
        for lang, texts_ in _TEXT_I18N.get(kind, {}).items():
            col = "%s_%s" % (valcol, lang)
            for k, v in texts_.items():
                try:
                    conn.execute(
                        "UPDATE %s SET %s=? WHERE %s=? AND %s IS NULL"
                        % (table, col, keycol, col), (v, k))
                except Exception:
                    conn.rollback()  # column missing (ALTER failed) -> English only
    conn.commit()


# --- setting accessors (override -> constant) ------------------------------
def _raw(conn, key):
    """Stored override or None. Blank counts as absent."""
    try:
        r = conn.execute("SELECT value FROM mnt_settings WHERE key=?", (key,)).fetchone()
    except Exception:
        return None                      # table not ready -> constants only
    v = r["value"] if r else None
    return v if (v is not None and str(v).strip() != "") else None


def num_override(conn, key):
    """Valid numeric override or None. Rejects blank, non-numeric, NaN/inf and
    out-of-range values so a bad row can never poison a live calculation."""
    spec = SETTINGS.get(key)
    lo = spec[2] if spec and spec[2] is not None else 0.0
    hi = spec[3] if spec and spec[3] is not None else 1e9
    raw = _raw(conn, key)
    if raw is None:
        return None
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < lo or v > hi:
        return None
    return v


def num(conn, key):
    v = num_override(conn, key)
    return SETTINGS[key][0] if v is None else v


def flag(conn, key):
    """Boolean knob. Anything the UI did not write falls back to the default."""
    raw = _raw(conn, key)
    if raw is None:
        return bool(SETTINGS[key][0])
    s = str(raw).strip().lower()
    if s in ("0", "false", "no", "off"):
        return False
    if s in ("1", "true", "yes", "on"):
        return True
    return bool(SETTINGS[key][0])


def sla_hours(conn, priority, base_default, key):
    """Base SLA hours x the priority factor. `base_default` keeps the caller's own
    literal as the last-resort fallback."""
    v = num_override(conn, key)
    base = base_default if v is None else v
    return base * sla_factor(conn, priority)


def sla_factor(conn, priority):
    """Priority multiplier. An unknown priority keeps the 1.0 fallback the code
    used before, without touching the database."""
    key = "sla_factor_%s" % (priority or "")
    if key not in SETTINGS:
        return SLA_FACTOR.get(priority, 1.0)
    return num(conn, key)


def dup_guard_on(conn):
    """Duplicate-open-ticket guard. Default ON == the previous unconditional check."""
    return flag(conn, "duplicate_ticket_guard")


def critical_default(conn):
    """The threshold with NO settings override: the critical matrix rule's own
    cost_threshold, else the 100 that _pick_matrix_levels hardcoded."""
    try:
        row = conn.execute("SELECT cost_threshold FROM mnt_approval_matrix WHERE active=1 "
                           "AND part_criticality='critical' LIMIT 1").fetchone()
        m = float(row["cost_threshold"]) if row and row["cost_threshold"] is not None else None
    except Exception:
        m = None
    if m is not None and math.isfinite(m) and m >= 0:
        return m
    return CRITICAL_COST_DEFAULT


def currency(conn, default="TRY"):
    """The unit the spare-part money figures are in.

    Stored in mnt_settings and, until now, read by nothing — so the critical
    threshold rendered as a bare number that could have been anything. This
    does not decide what the currency SHOULD be; it surfaces what is set.
    """
    v = (_raw(conn, "currency") or "").strip()
    return v or default


def critical_threshold(conn):
    """Request value at/above which the critical ladder is used."""
    v = num_override(conn, "critical_cost_threshold")
    return critical_default(conn) if v is None else v


# --- approval ladder -------------------------------------------------------
def rules(conn):
    """The two matrix rules the picker chooses between, selected exactly as
    services._pick_matrix_levels selects them."""
    crit = conn.execute("SELECT * FROM mnt_approval_matrix WHERE active=1 AND "
                        "part_criticality='critical' LIMIT 1").fetchone()
    std = conn.execute("SELECT * FROM mnt_approval_matrix WHERE active=1 "
                       "ORDER BY id LIMIT 1").fetchone()
    return {"std": std, "crit": crit}


def default_approvers(rule):
    """Approval rungs a matrix rule defines. The issuer is filtered out here, same
    as before, because issuing is not a vote."""
    levels = (rule["levels"] if rule and rule["levels"] else DEFAULT_LEVELS).split(",")
    return [r.strip() for r in levels if r.strip() and r.strip() != ISSUER_ROLE]


def stage_key(rule_key, level):
    return "%s_l%d" % (rule_key, level)


def _can_approve(role):
    """A stage override must name a real role that can actually sign, and must not
    be the issuer -- otherwise the rung would be unsignable (locked) or deleted."""
    role = (role or "").strip()
    if not role or role == ISSUER_ROLE:
        return False
    try:
        from app.security import effective_roles, has_permission
        return role in effective_roles() and has_permission(role, "maint_approve")
    except Exception:
        # RBAC layer unavailable (tooling): fall back to the module's own grants.
        return "maint_approve" in MAINT_ROLE_PERMS.get(role, [])


def stage_rows(conn):
    """{stage: {"role":..., "explanation":...}} — raw, no validation."""
    out = {}
    try:
        for r in conn.execute("SELECT stage, role, explanation FROM mnt_stage_meta").fetchall():
            out[r["stage"]] = {"role": r["role"], "explanation": r["explanation"]}
    except Exception:
        pass
    return out


def stage_role_override(conn, stage, rows=None):
    r = (rows if rows is not None else stage_rows(conn)).get(stage) or {}
    role = (r.get("role") or "").strip()
    return role if _can_approve(role) else None


def approver_roles(conn, rule_key, rule):
    """Effective rungs: the matrix default per level, replaced by a VALID
    stage->role override. An invalid override is ignored, so the rung keeps its
    default signer instead of becoming unsignable."""
    rows = stage_rows(conn)
    return [stage_role_override(conn, stage_key(rule_key, i), rows) or role
            for i, role in enumerate(default_approvers(rule), start=1)]


# --- text accessors --------------------------------------------------------
def _text_map(conn, table, keycol, valcol):
    out = {}
    try:
        for r in conn.execute("SELECT %s k, %s v FROM %s" % (keycol, valcol, table)).fetchall():
            out[r["k"]] = r["v"]
    except Exception:
        pass
    return out


def _cell(row, col):
    """Column value, or None when the column does not exist (pre-migration DB).
    sqlite3.Row raises IndexError for an unknown name, a dict row raises KeyError."""
    try:
        return row[col]
    except (KeyError, IndexError):
        return None


def text_rows(conn, kind):
    """{key: {"en":.., "ar":.., "tr":..}} exactly as stored — blanks preserved, so
    the editor shows an admin the real state of each language."""
    table, keycol, valcol, _ = _TEXT_TABLES[kind]
    out = {}
    try:
        rows = conn.execute("SELECT * FROM %s" % table).fetchall()
    except Exception:
        return out
    for r in rows:
        out[r[keycol]] = {"en": _cell(r, valcol),
                          "ar": _cell(r, "%s_ar" % valcol),
                          "tr": _cell(r, "%s_tr" % valcol)}
    return out


def pick(kind, key, row, lang):
    """THE display rule. The reader's language if that column has text, else the
    stored English, else the shipped English default — a panel is never empty."""
    for cand in ((row or {}).get(lang) if lang in ("ar", "tr") else None,
                 (row or {}).get("en"),
                 _TEXT_TABLES[kind][3].get(key)):
        if cand is not None and str(cand).strip() != "":
            return cand
    return ""


def prose(kind, key, row):
    """The SAME paragraph resolved for all three readers ({'en','ar','tr'}), each
    one through pick() so a missing translation is already the English. Shipped to
    the page as data-loc-en/ar/tr so the language button re-swaps the prose without
    a reload — the mechanism app.js already uses for DB text elsewhere."""
    return {lg: pick(kind, key, row, lg) for lg in LANGS}


def texts(conn, kind, lang=None):
    """lang=None -> the raw stored ENGLISH map (what an admin edited; used to detect
    an override). lang in ('en','ar','tr') -> the DISPLAY map for that reader."""
    if lang is None:
        t = _TEXT_TABLES[kind]
        return _text_map(conn, t[0], t[1], t[2])
    rows = text_rows(conn, kind)
    return {k: pick(kind, k, r, lang) for k, r in rows.items()}


# kind -> (table, key column, value column, seeded defaults)
_TEXT_TABLES = {
    "status": ("mnt_status_meta", "status", "explanation", STATUS_TEXT),
    "role": ("mnt_role_meta", "role_key", "explanation", ROLE_TEXT),
    "stage": ("mnt_stage_meta", "stage", "explanation", STAGE_TEXT),
    "doc": ("mnt_doc", "section", "body", DOC_TEXT),
}


# --- writes (audited) ------------------------------------------------------
# The entity types set_setting/set_text write under. Kept beside the writers so
# a new governance surface cannot be added without this list being in view.
GOVERNANCE_ENTITIES = ("mnt_setting", "mnt_status", "mnt_role", "mnt_stage", "mnt_doc")


def change_log(conn, limit=25):
    """Recent changes to the rules on this page: what, who, when, from -> to.

    Reads mnt_audit rather than a settings table, because a RESET leaves no row
    behind — the only record that it happened is the audit entry.
    """
    try:
        rows = conn.execute(
            "SELECT action, entity_type, old_value, new_value, comment, username, "
            "role, created_at FROM mnt_audit WHERE entity_type IN (%s) "
            "ORDER BY id DESC LIMIT ?" % ",".join("?" * len(GOVERNANCE_ENTITIES)),
            (*GOVERNANCE_ENTITIES, limit)).fetchall()
    except Exception:  # noqa: BLE001
        return []                       # table not ready -> no panel, not a 500
    out = []
    for r in rows:
        d = dict(r)
        d["is_reset"] = str(d.get("action") or "").endswith("_reset")
        # comment carries the setting key / status / role the change was about
        d["subject"] = d.get("comment") or d.get("entity_type")
        out.append(d)
    return out


def _write_audit(conn, user, action, entity_type, old, new, comment):
    from app.maintenance.services import audit          # lazy: avoid import cycle
    audit(conn, user, action, entity_type, 0, old, new, comment)


def set_setting(conn, key, value, user):
    """Store a knob override. Returns (ok, msg). Rejects a value that would not
    survive the read-side coercion, so the UI can say so instead of storing junk."""
    if key not in SETTINGS:
        return False, "unknown_setting"
    kind, lo, hi = SETTINGS[key][1], SETTINGS[key][2], SETTINGS[key][3]
    if kind == "bool":
        store = "1" if str(value).strip().lower() in ("1", "true", "yes", "on") else "0"
    else:
        try:
            v = float(str(value).strip())
        except (TypeError, ValueError):
            return False, "not_a_number"
        if not math.isfinite(v) or v < lo or v > hi:
            return False, "out_of_range"
        store = ("%g" % v)
    old = _raw(conn, key)
    conn.execute("INSERT OR IGNORE INTO mnt_settings (key,value) VALUES (?,?)", (key, store))
    conn.execute("UPDATE mnt_settings SET value=? WHERE key=?", (store, key))
    _write_audit(conn, user, "wf_setting_set", "mnt_setting", old, store, key)
    conn.commit()
    return True, ""


def reset_setting(conn, key, user):
    """Delete the override row so the constant default applies again."""
    if key not in SETTINGS:
        return False, "unknown_setting"
    old = _raw(conn, key)
    conn.execute("DELETE FROM mnt_settings WHERE key=?", (key,))
    _write_audit(conn, user, "wf_setting_reset", "mnt_setting", old, None, key)
    conn.commit()
    return True, ""


def set_text(conn, kind, key, explanation, user, role=None, ar=None, tr=None):
    """Upsert an explanation per language (and, for a stage, its signing role).
    A language passed as None was NOT submitted and is left exactly as it was, so
    saving one language never blanks the other two. Returns (ok,msg)."""
    t = _TEXT_TABLES.get(kind)
    if not t or not (key or "").strip():
        return False, "unknown_target"
    table, keycol, valcol, _ = t
    key = key.strip()
    role = (role or "").strip()
    # Validate BEFORE writing anything: a rung must never end up unsignable.
    if kind == "stage" and role and not _can_approve(role):
        return False, "role_cannot_sign"
    old = _text_map(conn, table, keycol, valcol).get(key)
    conn.execute("INSERT OR IGNORE INTO %s (%s) VALUES (?)" % (table, keycol), (key,))
    written = []
    for lang, col, val in (("en", valcol, explanation),
                           ("ar", "%s_ar" % valcol, ar),
                           ("tr", "%s_tr" % valcol, tr)):
        if val is None:
            continue
        try:
            conn.execute("UPDATE %s SET %s=? WHERE %s=?" % (table, col, keycol), (val, key))
            written.append(lang)
        except Exception:
            conn.rollback()          # column missing -> that language is dropped
    conn.execute("UPDATE %s SET updated_by=?, updated_at=? WHERE %s=?" % (table, keycol),
                 ((user or {}).get("username"), _now(), key))
    if kind == "stage":
        conn.execute("UPDATE mnt_stage_meta SET role=? WHERE stage=?", (role or None, key))
    _write_audit(conn, user, "wf_text_set", "mnt_%s" % kind, old,
                 explanation if explanation is not None else (ar or tr),
                 "%s [%s]" % (key, "+".join(written) or "none"))
    conn.commit()
    return True, ""


def reset_text(conn, kind, key, user):
    """Delete the row: the seeded default text comes back on the next boot seed,
    and a stage's role override disappears immediately."""
    t = _TEXT_TABLES.get(kind)
    if not t:
        return False, "unknown_target"
    table, keycol, valcol, defaults = t
    old = _text_map(conn, table, keycol, valcol).get(key)
    conn.execute("DELETE FROM %s WHERE %s=?" % (table, keycol), (key,))
    # Put the shipped default straight back — in all three languages — so the page
    # never shows a blank explanation just because an admin pressed reset.
    if key in defaults:
        conn.execute("INSERT OR IGNORE INTO %s (%s,%s) VALUES (?,?)"
                     % (table, keycol, valcol), (key, defaults[key]))
        for lang, seeded in _TEXT_I18N.get(kind, {}).items():
            if key in seeded:
                try:
                    conn.execute("UPDATE %s SET %s_%s=? WHERE %s=?"
                                 % (table, valcol, lang, keycol), (seeded[key], key))
                except Exception:
                    conn.rollback()
    _write_audit(conn, user, "wf_text_reset", "mnt_%s" % kind, old, None, key)
    conn.commit()
    return True, ""


def _now():
    from app.maintenance.services import _now as n      # lazy: avoid import cycle
    return n()


# --- page ------------------------------------------------------------------
def role_labels():
    """{role_key: English label} for every maintenance role — also the exact set of
    keys the page may tag with data-i18n="role.<key>", so an unknown role coming out
    of the approval matrix falls back to plain text instead of showing a raw key."""
    try:
        from app.security import role_label
    except Exception:
        def role_label(r):
            return r
    return {rk: (MAINT_ROLE_LABELS.get(rk) or role_label(rk) or rk)
            for rk in MAINT_ROLE_PERMS}


# A sentence about what a setting DOES, for the settings that change who may
# do what. Not every knob needs one — an SLA factor explains itself — but a
# control that gates or permits an action has to say what On and Off mean,
# because the label alone reads the same either way. The i18n key carries the
# translation; the English here is the fallback the template renders.
SETTING_NOTES = {
    "ejr_gate": ("mwf.setting.ejr_gate_note",
                 "DOAM §6 requires this. On: Procurement cannot sign off any "
                 "spares, MRO or maintenance-department requisition without an "
                 "Engineering-Head-approved justification report — automatic "
                 "min/max reorders stay exempt. Ships off so switching it on is a "
                 "deliberate decision."),
    "duplicate_ticket_guard": ("mwf.setting.dup_guard_note",
                 "On: a second ticket on a machine that already has one open is "
                 "refused, and the reporter is shown the open ticket. They may "
                 "still tick 'create anyway' if it is a separate fault, and that "
                 "override is written to the audit trail naming both tickets. Off: "
                 "nothing is checked and no override is recorded."),
}


def page_data(conn, lang="en"):
    """Everything /maintenance/workflow renders, resolved override -> default.
    `lang` is the reader's users.lang_pref: it selects which language column the
    stored prose is read from (see pick()); it changes no behaviour or number."""
    lang = lang if lang in LANGS else "en"
    stage_meta = stage_rows(conn)
    stage_text = text_rows(conn, "stage")
    role_rows = text_rows(conn, "role")
    rule_rows = rules(conn)
    ladder = []
    for rk in RULE_KEYS:
        rule = rule_rows.get(rk)
        for i, default_role in enumerate(default_approvers(rule), start=1):
            sk = stage_key(rk, i)
            meta = stage_meta.get(sk) or {}
            eff = stage_role_override(conn, sk, stage_meta)
            ladder.append({
                "stage": sk, "rule": rk, "level": i,
                "rule_name": (rule["name"] if rule else "default"),
                "default_role": default_role,
                "role": eff or default_role,
                "stored_role": meta.get("role") or "",
                "overridden": bool(eff) and eff != default_role,
                "invalid": bool((meta.get("role") or "").strip()) and not eff,
                "explanation": pick("stage", sk, stage_text.get(sk), lang),
                "langs": prose("stage", sk, stage_text.get(sk)),
                "text": stage_text.get(sk) or {},
            })
        # the issuer rung is documented, never an approval vote
        ladder.append({
            "stage": "", "rule": rk, "level": len(default_approvers(rule)) + 1,
            "rule_name": (rule["name"] if rule else "default"),
            "default_role": ISSUER_ROLE, "role": ISSUER_ROLE, "stored_role": "",
            "overridden": False, "invalid": False, "issuer": True,
            "explanation": pick("role", ISSUER_ROLE, role_rows.get(ISSUER_ROLE), lang),
            "langs": prose("role", ISSUER_ROLE, role_rows.get(ISSUER_ROLE)),
            "text": {},
        })

    st_rows = text_rows(conn, "status")
    st_text = texts(conn, "status")            # English: detects an admin override
    statuses = [{
        "status": s,
        "next": sorted(TICKET_TRANSITIONS.get(s, set())),
        "terminal": not TICKET_TRANSITIONS.get(s),
        "explanation": pick("status", s, st_rows.get(s), lang),
        "langs": prose("status", s, st_rows.get(s)),
        "text": st_rows.get(s) or {},
        "overridden": s in st_text and (st_text.get(s) or "") != STATUS_TEXT.get(s, ""),
    } for s in TICKET_STATUSES]

    role_text = texts(conn, "role")            # English: detects an admin override
    labels = role_labels()
    try:
        from app.security import effective_roles
        eff_roles = effective_roles()
    except Exception:
        eff_roles = {}
    roles_out = []
    for rk in MAINT_ROLE_PERMS:
        perms = [p for p in (eff_roles.get(rk, {}).get("perms") or MAINT_ROLE_PERMS[rk])
                 if p.startswith("maint_") or p == "*"]
        roles_out.append({
            "role_key": rk,
            "label": labels.get(rk) or rk,
            "perms": perms,
            "explanation": pick("role", rk, role_rows.get(rk), lang),
            "langs": prose("role", rk, role_rows.get(rk)),
            "text": role_rows.get(rk) or {},
            "overridden": rk in role_text and (role_text.get(rk) or "") != ROLE_TEXT.get(rk, ""),
            "can_approve": _can_approve(rk),
        })

    doc_rows = text_rows(conn, "doc")
    doc_text = texts(conn, "doc")              # English: detects an admin override
    docs = {s: {"body": pick("doc", s, doc_rows.get(s), lang),
                "langs": prose("doc", s, doc_rows.get(s)),
                "text": doc_rows.get(s) or {},
                "overridden": s in doc_text and (doc_text.get(s) or "") != DOC_TEXT.get(s, "")}
            for s in DOC_SECTIONS}

    settings_out = []
    for key, (default, kind, _lo, _hi) in SETTINGS.items():
        eff = flag(conn, key) if kind == "bool" else num(conn, key)
        if key == "critical_cost_threshold":
            # this one's default is itself data (the matrix rule), not a literal
            default, eff = critical_default(conn), critical_threshold(conn)
        src, reader = SETTING_SOURCE.get(key, ("code", ""))
        settings_out.append({
            "key": key, "kind": kind, "default": default, "value": eff,
            "raw": _raw(conn, key), "overridden": eff != default,
            "source": src, "reader": reader,
            "note": SETTING_NOTES.get(key),
        })

    # Effective ladder for a worked example, so the page proves what it claims.
    return {
        "currency": currency(conn),
        "settings": settings_out, "ladder": ladder, "statuses": statuses,
        "roles": roles_out, "docs": docs,
        "lang": lang, "role_labels": labels,
        "threshold": critical_threshold(conn),
        "approver_choices": sorted(r["role_key"] for r in roles_out if r["can_approve"]
                                   and r["role_key"] != ISSUER_ROLE),
        "counts": {
            "overrides": sum(1 for s in settings_out if s["overridden"])
                         + sum(1 for l in ladder if l["overridden"]),
            "rungs": sum(1 for l in ladder if not l.get("issuer")),
        },
    }
