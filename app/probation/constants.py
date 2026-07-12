"""
Probation Management — constants (single source of truth).

Ported faithfully from the standalone Probation Evaluation Platform:
  * the 8 evaluation criteria (4 professional + 4 behaviour), trilingual, taken
    verbatim from the official "Probation Period Confirmation" form;
  * the 1..5 rating scale, scoring maths and auto-recommendation thresholds;
  * the workflow status model and final outcomes.

Also declares this module's RBAC (permissions, role→perms, role labels) which
`app.security` folds into the platform catalogue via `_merge_module_rbac`.

UI strings live in the platform i18n JSON (keys referenced here); business data
that is snapshotted onto records (criterion labels) is kept trilingual here so a
submitted evaluation never changes when a template is later edited.
"""

# ==========================================================================
# Workflow status model
# ==========================================================================
class Status:
    DRAFT = "draft"
    PENDING_MANAGER = "pending_manager"              # direct/department manager evaluation
    PENDING_SECTION_HEAD = "pending_section_head"    # section/department head review
    PENDING_HR_REVIEW = "pending_hr_review"          # HR final review
    RETURNED = "returned"                            # returned for correction
    APPROVED = "approved"                            # final; outcome in `final_outcome`
    REJECTED = "rejected"                            # not confirmed / terminate probation
    CANCELLED = "cancelled"
    CLOSED = "closed"

    ALL = [DRAFT, PENDING_MANAGER, PENDING_SECTION_HEAD, PENDING_HR_REVIEW,
           RETURNED, APPROVED, REJECTED, CANCELLED, CLOSED]

    # states an evaluator (manager/section head) may still edit
    EDITABLE_BY_EVALUATOR = [DRAFT, RETURNED]
    # states awaiting an HR decision
    OPEN_FOR_HR = [PENDING_HR_REVIEW]
    # locked = no further edits without an authorised HR reopen
    LOCKED = [APPROVED, REJECTED, CLOSED, CANCELLED]
    # still "live" cases (count against due dates / can become overdue)
    ACTIVE = [DRAFT, PENDING_MANAGER, PENDING_SECTION_HEAD, PENDING_HR_REVIEW, RETURNED]

    # i18n key per status (resolved client-side; English fallback in the value)
    I18N = {
        DRAFT: "prob.status.draft",
        PENDING_MANAGER: "prob.status.pending_manager",
        PENDING_SECTION_HEAD: "prob.status.pending_section_head",
        PENDING_HR_REVIEW: "prob.status.pending_hr_review",
        RETURNED: "prob.status.returned",
        APPROVED: "prob.status.approved",
        REJECTED: "prob.status.rejected",
        CANCELLED: "prob.status.cancelled",
        CLOSED: "prob.status.closed",
    }
    # English fallback labels (used in exports / non-i18n contexts)
    LABEL_EN = {
        DRAFT: "Draft",
        PENDING_MANAGER: "Pending Manager Evaluation",
        PENDING_SECTION_HEAD: "Pending Section Head Review",
        PENDING_HR_REVIEW: "Pending HR Review",
        RETURNED: "Returned for Correction",
        APPROVED: "Approved",
        REJECTED: "Not Confirmed",
        CANCELLED: "Cancelled",
        CLOSED: "Closed",
    }
    # semantic colour bucket for badges (maps to platform status CSS)
    TONE = {
        DRAFT: "muted", PENDING_MANAGER: "info", PENDING_SECTION_HEAD: "info",
        PENDING_HR_REVIEW: "warn", RETURNED: "warn", APPROVED: "ok",
        REJECTED: "crit", CANCELLED: "muted", CLOSED: "ok",
    }


# Final outcome once HR approves (spec: Confirm / Extend / Not Confirm).
class Outcome:
    CONFIRM = "confirm"          # confirm employment
    EXTEND = "extend"            # extend probation
    NOT_CONFIRM = "not_confirm"  # do not confirm / terminate probation

    ALL = [CONFIRM, EXTEND, NOT_CONFIRM]
    I18N = {
        CONFIRM: "prob.outcome.confirm",
        EXTEND: "prob.outcome.extend",
        NOT_CONFIRM: "prob.outcome.not_confirm",
    }
    LABEL_EN = {
        CONFIRM: "Confirm Employment",
        EXTEND: "Extend Probation",
        NOT_CONFIRM: "Do Not Confirm",
    }


# Auto recommendation produced by the score engine (advisory to HR).
class Recommendation:
    PASS = "pass"
    HR_REVIEW = "hr_review"
    NOT_RECOMMENDED = "not_recommended"

    I18N = {
        PASS: "prob.reco.pass",
        HR_REVIEW: "prob.reco.hr_review",
        NOT_RECOMMENDED: "prob.reco.not_recommended",
    }
    LABEL_EN = {
        PASS: "Recommended to confirm",
        HR_REVIEW: "HR review required",
        NOT_RECOMMENDED: "Not recommended",
    }


class ActionType:
    CREATE = "create"
    UPDATE = "update"
    SUBMIT = "submit"
    FORWARD = "forward"
    APPROVE = "approve"
    REJECT = "reject"
    RETURN = "return"
    RESUBMIT = "resubmit"
    EXTEND = "extend"
    CANCEL = "cancel"
    REOPEN = "reopen"
    EXPORT = "export"
    IMPORT = "import"
    LOCK = "lock"
    OVERRIDE = "override"


# ==========================================================================
# Evaluation criteria  (verbatim from the official form — the business truth)
# ==========================================================================
CAT_PROFESSIONAL = "professional"
CAT_BEHAVIOR = "behavior"

CATEGORIES = {
    CAT_PROFESSIONAL: {
        "label_ar": "الأداء المهني",
        "label_tr": "Mesleki Performans",
        "label_en": "Professional Performance",
    },
    CAT_BEHAVIOR: {
        "label_ar": "السلوك والانضباط",
        "label_tr": "Davranış ve Disiplin",
        "label_en": "Behavior and Discipline",
    },
}

# Rating scale 1..5
RATING_SCALE = [
    {"value": 1, "ar": "ضعيف", "tr": "Zayıf", "en": "Weak"},
    {"value": 2, "ar": "مقبول", "tr": "Kabul edilebilir", "en": "Acceptable"},
    {"value": 3, "ar": "جيد", "tr": "İyi", "en": "Good"},
    {"value": 4, "ar": "جيد جداً", "tr": "Çok iyi", "en": "Very Good"},
    {"value": 5, "ar": "ممتاز", "tr": "Mükemmel", "en": "Excellent"},
]

MAX_SCORE_PER_CRITERION = 5

# The 8 default criteria (4 professional + 4 behaviour). Weight defaults to 1;
# templates may override weight/range per criterion without touching this list.
CRITERIA = [
    {
        "key": "productivity", "category": CAT_PROFESSIONAL, "weight": 1,
        "label_ar": "الإنتاجية والالتزام بالهدف",
        "label_tr": "Verimlilik ve hedefe bağlılık",
        "label_en": "Productivity and target commitment",
        "desc_ar": "هل وصل العامل إلى الكمية المحددة له يومياً/أسبوعياً بعد فترة التدريب؟",
        "desc_tr": "Eğitim süresinden sonra işçi kendisine belirlenen günlük/haftalık üretim miktarını gerçekleştirdi mi?",
        "desc_en": "Did the worker reach the assigned daily/weekly quantity after the training period?",
    },
    {
        "key": "work_quality", "category": CAT_PROFESSIONAL, "weight": 1,
        "label_ar": "جودة العمل (معدل العيوب)",
        "label_tr": "İş Kalitesi (hata oranı)",
        "label_en": "Work quality (defect rate)",
        "desc_ar": "كم عدد القطع المعيبة التي عادت من قسم الجودة إلى هذا العامل لإعادة العمل عليها؟ (كلما قلّ العدد كان أفضل)",
        "desc_tr": "Kalite bölümünden bu işçiye yeniden işlem yapması için geri dönen hatalı parça sayısı kaçtır? (sayı ne kadar azsa o kadar iyidir)",
        "desc_en": "How many defective pieces were returned from QC to this worker for rework? (fewer is better)",
    },
    {
        "key": "learning_speed", "category": CAT_PROFESSIONAL, "weight": 1,
        "label_ar": "سرعة التعلم والمهارة",
        "label_tr": "Öğrenme hızı ve beceri",
        "label_en": "Learning speed and skill",
        "desc_ar": "هل استوعب التعليمات بسرعة؟ هل أتقن استخدام الماكينة أو أداة العمل الرئيسية في الوقت المتوقع؟",
        "desc_tr": "Talimatları hızlı bir şekilde kavradı mı? Makineyi veya ana çalışma aracını beklenen vakitte kullanmayı öğrendi mi?",
        "desc_en": "Did they grasp instructions quickly and master the machine/main work tool in the expected time?",
    },
    {
        "key": "resource_efficiency", "category": CAT_PROFESSIONAL, "weight": 1,
        "label_ar": "كفاءة استخدام الموارد",
        "label_tr": "Kaynakların etkin kullanımı",
        "label_en": "Efficient use of resources",
        "desc_ar": "هل يهدر العامل في الخامات (قماش، خيوط) أثناء العمل؟ هل يحافظ على نظافة الماكينة وأدواته لتجنب الأعطال؟",
        "desc_tr": "İşçi çalışma süresinde malzemeleri (kumaş, iplik) israf ediyor mu? Makine ve araçlarının temizliğine özen gösteriyor mu?",
        "desc_en": "Does the worker waste materials (fabric, thread)? Do they keep the machine/tools clean to avoid breakdowns?",
    },
    {
        "key": "attendance", "category": CAT_BEHAVIOR, "weight": 1,
        "label_ar": "الحضور والانضباط في المواعيد",
        "label_tr": "Devamlılık ve zaman disiplini",
        "label_en": "Attendance and punctuality",
        "desc_ar": "كم مرة غاب بدون إذن أو تأخر عن موعد الحضور أو بدء العمل؟",
        "desc_tr": "İzinsiz olarak gelmediği veya işe başlama saatine geç kaldığı kaç kez olmuştur?",
        "desc_en": "How many times were they absent without permission or late for attendance/work start?",
    },
    {
        "key": "responsiveness", "category": CAT_BEHAVIOR, "weight": 1,
        "label_ar": "الاستجابة للتوجيهات والتغيير",
        "label_tr": "Yönergelere ve değişime uyum sağlama",
        "label_en": "Response to instructions and change",
        "desc_ar": "هل يتقبل تعليمات المشرف بتغيير طريقة العمل أو المهام بهدوء وينفذها بدقة؟",
        "desc_tr": "İşçi süpervizörün iş yapma şeklini veya görevlerini değiştirme talimatlarını sakin bir şekilde kabul edip dikkatle uyguluyor mu?",
        "desc_en": "Do they calmly accept supervisor instructions to change work method/tasks and execute them accurately?",
    },
    {
        "key": "cooperation", "category": CAT_BEHAVIOR, "weight": 1,
        "label_ar": "التعاون وروح الفريق",
        "label_tr": "İşbirliği ve Ekip Ruhu",
        "label_en": "Cooperation and team spirit",
        "desc_ar": "هل يساعد زملاءه؟ هل يتصرف بطريقة تضمن استمرارية تدفق العمل على الخط دون تعطيل؟",
        "desc_tr": "İş arkadaşlarına yardımcı oluyor mu? Hattaki iş akışının kesintiye uğramadan devam etmesini sağlayacak şekilde hareket ediyor mu?",
        "desc_en": "Do they help colleagues and act to keep the line's workflow going without disruption?",
    },
    {
        "key": "safety_hygiene", "category": CAT_BEHAVIOR, "weight": 1,
        "label_ar": "الالتزام بمعايير السلامة والنظافة",
        "label_tr": "Güvenlik ve hijyen standartlarına uyum",
        "label_en": "Safety and hygiene standards",
        "desc_ar": "هل يرتدي أدوات السلامة (PPE)؟ هل منطقته منظمة ونظيفة لتقليل الحوادث؟",
        "desc_tr": "Çalışan kişisel koruyucu donanımını (PPE) takıyor mu? Çalışma alanı düzenli ve temiz mi?",
        "desc_en": "Do they wear PPE? Is their area organized and clean to reduce accidents?",
    },
]

CRITERIA_BY_KEY = {c["key"]: c for c in CRITERIA}
DEFAULT_MAX_TOTAL = sum(c["weight"] for c in CRITERIA) * MAX_SCORE_PER_CRITERION  # 40


def criteria_for(category):
    return [c for c in CRITERIA if c["category"] == category]


# Final recommendation options faithful to the form (+ extend).
FINAL_RECOMMENDATIONS = [
    {"key": Outcome.CONFIRM,
     "ar": "اجتاز الموظف فترة الاختبار بنجاح، ويتم تثبيته",
     "tr": "Çalışan deneme süresini başarıyla tamamlamıştır ve kadroya alınacaktır",
     "en": "Employee passed probation and will be confirmed"},
    {"key": Outcome.NOT_CONFIRM,
     "ar": "لم يجتز الموظف فترة الاختبار، ولا يتم تثبيته",
     "tr": "Çalışan deneme süresini geçememiştir ve kadroya alınmayacaktır",
     "en": "Employee did not pass probation and will not be confirmed"},
    {"key": Outcome.EXTEND,
     "ar": "تمديد فترة الاختبار",
     "tr": "Deneme süresinin uzatılması",
     "en": "Extend probation period"},
]

# ==========================================================================
# Defaults (overridable in prob_settings)
# ==========================================================================
DEFAULT_SETTINGS = {
    "threshold_pass": "75",         # >= % -> auto recommend confirm
    "threshold_review": "60",       # >= % -> HR review; below -> not recommended
    "approval_flow": "one_level",   # one_level | two_level (adds manager step)
    "require_notes_below": "2",     # score <= this requires a note
    "reminder_offsets": "30,14,7,3,1",   # days before probation end
    "manager_sla_days": "5",        # escalate to HR if manager idle this long
    "default_probation_days": "90",
}

# reminder day-offsets before probation end date
DEFAULT_REMINDER_OFFSETS = [30, 14, 7, 3, 1]


# ==========================================================================
# RBAC — merged into the platform catalogue by app.security
# ==========================================================================
HR_PERMISSIONS = [
    "prob_view",       # view probation dashboards/cases within scope
    "prob_evaluate",   # perform manager / section-head evaluation on assigned staff
    "prob_hr_review",  # HR: create/manage cases + final HR review & decision
    "prob_reports",    # view & export probation reports/analytics
    "prob_import",     # import employees / legacy probation data
    "prob_admin",      # module config, templates, workflow, reopen, all cases
]

HR_ROLE_PERMS = {
    # HR Probation Admin — full access
    "hr_probation_admin": ["prob_view", "prob_evaluate", "prob_hr_review",
                            "prob_reports", "prob_import", "prob_admin"],
    # HR Officer — create/manage cases + HR review within scope
    "hr_officer": ["prob_view", "prob_hr_review", "prob_reports"],
    # Direct/Department Manager — evaluate own reports only
    "department_manager": ["prob_view", "prob_evaluate"],
    # Section / Department Head — review/evaluate authorised org scope
    "section_head": ["prob_view", "prob_evaluate"],
    # Executive — read-only dashboards & reports (no confidential comments)
    "executive_viewer": ["prob_view", "prob_reports"],
    # Existing generic HR role gains read access
    "hr_user": ["prob_view", "prob_reports"],
}

HR_ROLE_LABELS = {
    "hr_probation_admin": "HR Probation Admin",
    "hr_officer": "HR Officer",
    "department_manager": "Department Manager",
    "section_head": "Section Head",
}

# Grouping + human labels for the Admin -> Roles permission editor
PERMISSION_LABELS = {
    "prob_view": "Probation: view",
    "prob_evaluate": "Probation: evaluate (manager)",
    "prob_hr_review": "Probation: HR review & decision",
    "prob_reports": "Probation: reports & export",
    "prob_import": "Probation: import data",
    "prob_admin": "Probation: admin (config, templates, reopen)",
}
PERMISSION_DESC = {
    "prob_view": "See probation dashboards and cases within your scope.",
    "prob_evaluate": "Evaluate employees assigned to you (draft, submit, correct).",
    "prob_hr_review": "Create/manage cases and take the final HR decision.",
    "prob_reports": "View and export probation reports and analytics.",
    "prob_import": "Import the employee roster or migrate legacy probation data.",
    "prob_admin": "Configure templates, workflow and thresholds; reopen locked cases.",
}
