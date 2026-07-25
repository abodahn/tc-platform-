"""
TC Platform — sidebar navigation definition.

Each item: (key, i18n_key, icon, endpoint, permission). Sections group items.
Menu visibility is enforced by `permission` against the user's role.
"""

NAV = [
    {"section": "nav.command", "items": [
        ("command_center", "nav.command_center", "grid", "main.dashboard", "view_dashboard"),
        ("my_work", "nav.my_work", "check", "main.my_work", "open_module"),
        ("launcher", "nav.launcher", "apps", "main.launcher", "open_module"),
    ]},
    {"section": "nav.intelligence", "items": [
        ("ai_center", "nav.ai_center", "sparkles", "intelligence.command_center", "view_dashboard"),
        ("ai_alerts", "nav.ai_alerts", "alert", "intelligence.alerts", "view_dashboard"),
        ("ai_breakdown", "nav.ai_breakdown", "chart", "intelligence.breakdown", "view_dashboard"),
        ("ai_assistant", "nav.ai_assistant", "robot", "intelligence.assistant", "view_dashboard"),
        ("ai_reports", "nav.ai_reports", "report", "intelligence.reports", "view_dashboard"),
        ("ai_settings", "nav.ai_settings", "settings", "intelligence.settings", "access_admin"),
    ]},
    {"section": "nav.operations", "items": [
        ("itsm", "nav.service_desk", "headset", "main.module", "open_module"),
        ("assets", "nav.assets", "boxes", "main.module", "open_module"),
        ("monitoring", "nav.monitoring", "activity", "main.module", "open_module"),
        ("commandtrack", "nav.work", "kanban", "main.module", "open_module"),
        ("registry", "nav.registry", "users", "main.registry", "open_module"),
    ]},
    {"section": "nav.maintenance", "items": [
        ("maint_floor", "nav.maint_floor", "target", "maintenance.floor", "maint_view"),
        ("maint_dashboard", "nav.maint_dashboard", "grid", "maintenance.dashboard", "maint_view"),
        ("maint_ai", "nav.maint_ai", "sparkles", "maintenance.ai_insights", "maint_view"),
        ("maint_new", "nav.maint_new", "report", "maintenance.ticket_new", "maint_ticket_create"),
        ("maint_tickets", "nav.maint_tickets", "kanban", "maintenance.tickets", "maint_view"),
        ("maint_machines", "nav.maint_machines", "factory", "maintenance.machines", "maint_view"),
        ("maint_spares", "nav.maint_spares", "boxes", "maintenance.spares", "maint_view"),
        ("maint_requests", "nav.maint_requests", "cart", "maintenance.requests", "maint_view"),
        ("maint_approvals", "nav.maint_approvals", "check", "maintenance.approvals", "maint_approve"),
        ("maint_pm", "nav.maint_pm", "refresh", "maintenance.pm", "maint_view"),
        ("maint_calendar", "nav.maint_calendar", "clock", "maintenance.calendar", "maint_view"),
        ("maint_stock", "nav.maint_stock", "server", "maintenance.stock", "maint_view"),
        ("maint_reports", "nav.maint_reports", "chart", "maintenance.reports", "maint_view"),
        ("maint_settings", "nav.maint_settings", "settings", "maintenance.settings", "maint_admin"),
    ]},
    # Product development — the single source of style truth.
    {"section": "nav.product", "items": [
        ("plm", "nav.plm", "book", "plm.index", "plm_view"),
        ("plm_styles", "nav.plm_styles", "kanban", "plm.styles", "plm_view"),
    ]},
    # Orders, capacity and margin — what we promised, can we make it, does it pay.
    {"section": "nav.planning", "items": [
        ("orders", "nav.orders", "boxes", "orders.index", "view_dashboard"),
        ("orders_tna", "nav.orders_tna", "clock", "orders.tna", "view_dashboard"),
        ("planning", "nav.planning_board", "target", "planning.index", "pln_view"),
        ("pln_lines", "nav.pln_lines", "factory", "planning.lines", "pln_view"),
        ("costing", "nav.costing", "wallet", "costing.index", "cost_view"),
        ("cost_orders", "nav.cost_orders", "chart", "costing.orders", "cost_view"),
    ]},
    # The live shop floor.
    {"section": "nav.factory_floor", "items": [
        ("mes", "nav.mes", "activity", "mes.index", "mes_view"),
        ("mes_board", "nav.mes_board", "grid", "mes.board", "mes_view"),
        ("mes_bundles", "nav.mes_bundles", "boxes", "mes.bundles", "mes_view"),
        ("cutroom", "nav.cutroom", "target", "cutroom.index", "cut_view"),
        ("quality", "nav.quality", "check", "quality.index", "qc_view"),
        ("wash", "nav.wash", "refresh", "wash.index", "wsh_view"),
    ]},
    # Material in, finished goods out, and provenance for both.
    {"section": "nav.supply_chain", "items": [
        ("warehouse", "nav.warehouse", "server", "warehouse.index", "wh_view"),
        ("wh_rolls", "nav.wh_rolls", "boxes", "warehouse.rolls", "wh_view"),
        ("wh_fg", "nav.wh_fg", "kanban", "warehouse.fg", "wh_view"),
        ("shipping", "nav.shipping", "cart", "shipping.index", "shp_view"),
        ("trace", "nav.trace", "map", "trace.index", "trc_view"),
    ]},
    {"section": "nav.smart_factory", "items": [
        ("sf_cc", "nav.sf_cc", "grid", "smartfactory.command_center", "view_dashboard"),
        ("sf_floor", "nav.sf_floor", "target", "smartfactory.floor", "view_dashboard"),
        ("sf_production", "nav.sf_production", "report", "smartfactory.production", "view_dashboard"),
        ("sf_bundles", "nav.sf_bundles", "boxes", "smartfactory.bundles", "view_dashboard"),
        ("sf_quality", "nav.sf_quality", "check", "smartfactory.quality", "view_dashboard"),
        ("sf_wash", "nav.sf_wash", "refresh", "smartfactory.wash", "view_dashboard"),
        ("sf_workforce", "nav.sf_workforce", "users", "smartfactory.workforce", "view_dashboard"),
        ("sf_costing", "nav.sf_costing", "wallet", "smartfactory.costing", "view_dashboard"),
        ("sf_approvals", "nav.sf_approvals", "check", "smartfactory.approvals", "view_dashboard"),
        ("sf_orders", "nav.sf_orders", "cart", "smartfactory.orders", "view_dashboard"),
        ("sf_ai", "nav.sf_ai", "sparkles", "smartfactory.ai", "view_dashboard"),
        ("sf_intel", "nav.sf_intel", "trend", "smartfactory.intelligence", "view_dashboard"),
        ("sf_reports", "nav.sf_reports", "chart", "smartfactory.reports", "view_dashboard"),
    ]},
    {"section": "nav.hr", "items": [
        ("people", "nav.people", "users", "people.index", "ppl_view"),
        ("ppl_attendance", "nav.ppl_attendance", "check", "people.attendance", "ppl_view"),
        ("ppl_leave", "nav.ppl_leave", "clock", "people.leave", "ppl_view"),
        ("ppl_skills", "nav.ppl_skills", "trend", "people.skills", "ppl_view"),
        ("prob_dashboard", "nav.prob_dashboard", "grid", "probation.dashboard", "prob_view"),
        ("prob_mine", "nav.prob_mine", "check", "probation.mine", "prob_view"),
        ("prob_cases", "nav.prob_cases", "users", "probation.cases", "prob_view"),
        ("prob_initiate", "nav.prob_initiate", "report", "probation.initiate", "prob_hr_review"),
        ("prob_review", "nav.prob_review", "check", "probation.review", "prob_hr_review"),
        ("prob_reminders", "nav.prob_reminders", "clock", "probation.reminders", "prob_hr_review"),
        ("prob_reports", "nav.prob_reports", "chart", "probation.reports", "prob_reports"),
        ("prob_import", "nav.prob_import", "boxes", "probation.import_data", "prob_import"),
        ("prob_settings", "nav.prob_settings", "settings", "probation.settings", "prob_admin"),
    ]},
    {"section": "nav.transformation", "items": [
        ("bi", "nav.bi", "chart", "bi.index", "open_module"),
        ("ai_hub", "nav.ai_hub", "sparkles", "main.module", "open_module"),
        ("finance", "nav.finance", "wallet", "main.module", "open_module"),
        ("automation", "nav.automation", "robot", "main.module", "open_module"),
        ("production", "nav.production", "factory", "production.index", "open_module"),
    ]},
    {"section": "nav.procurement_cycle", "items": [
        ("procurement", "nav.proc_home", "cart", "approvals.index", "proc_view"),
        ("proc_new", "nav.proc_new", "report", "approvals.new", "proc_create"),
        ("proc_list", "nav.proc_list", "kanban", "approvals.listing", "proc_view"),
        ("proc_analytics", "nav.proc_analytics", "chart", "approvals.analytics", "proc_view"),
        ("proc_vendors", "nav.proc_vendors", "boxes", "approvals.vendors", "proc_view"),
        ("proc_budgets", "nav.proc_budgets", "wallet", "approvals.budgets", "proc_view"),
        ("proc_delegations", "nav.proc_delegations", "users", "approvals.delegations", "proc_view"),
        ("proc_settings", "nav.proc_settings", "settings", "approvals.settings", "proc_admin"),
    ]},
    {"section": "nav.services", "items": [
        ("hr", "nav.hr", "users", "main.module", "open_module"),
        ("saplite", "nav.saplite", "server", "main.module", "open_module"),
        ("kb", "nav.kb", "book", "main.module", "open_module"),
    ]},
    {"section": "nav.govern", "items": [
        ("compliance", "nav.compliance", "shield", "compliance.index", "cmp_view"),
        ("cmp_audits", "nav.cmp_audits", "check", "compliance.audits", "cmp_view"),
        ("cmp_certs", "nav.cmp_certs", "book", "compliance.certs", "cmp_view"),
        ("reports", "nav.reports", "report", "main.reports", "view_reports"),
        ("governance", "nav.governance", "map", "main.module", "open_module"),
        ("health", "nav.health", "pulse", "main.health", "view_system_health"),
        ("roadmap", "nav.roadmap", "map", "main.roadmap", "view_dashboard"),
    ]},
    {"section": "nav.admin", "items": [
        ("admin", "nav.admin_center", "settings", "admin.index", "access_admin"),
        ("registrations", "nav.registrations", "users", "admin.registrations", "users_view"),
        ("garamento_kb", "nav.garamento_kb", "book", "admin.garamento", "access_admin"),
    ]},
]

# Modules that are still placeholders (a "coming soon" landing page, not built out).
# They are HIDDEN for normal users and shown to admins under an "In Progress"
# group instead of their normal sections. Move a key out of here the moment its
# module becomes real.
WIP_KEYS = {"ai_hub", "finance", "automation", "hr", "saplite", "kb", "governance"}
