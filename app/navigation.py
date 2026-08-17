"""
TC Platform — sidebar navigation definition.

Each item: (key, i18n_key, icon, endpoint, permission). Sections group items.
Menu visibility is enforced by `permission` against the user's role.

ORDER IS THE FLOW OF A GARMENT ORDER, top to bottom:

    design it -> sell it -> plan it -> buy for it -> receive material ->
    cut, sew, wash, inspect -> ship it

Then the functions that support that flow (keep the machines running, manage the
people), then the layers that watch it (intelligence, the connected systems),
then govern and administer it. A supervisor reading the menu top to bottom is
walking the factory in the order the work happens, which is why Materials now
sits ABOVE the floor that consumes it — it used to sit below.

TWO RULES, learned from the mess this replaces:

  1. ONE NAME PER JOB. "Quality" and "AI Insights" each appeared twice under
     different sections, and "Reports Center"/"Reports Centre" were two
     different pages one letter apart. If two entries would answer the same
     question, either merge them or name them by what actually differs.
  2. Smart Factory is ONE entry, not thirteen. Its sub-pages duplicated the
     native modules (Quality, Wash, Bundles, Costing, Orders, Workforce) and are
     reachable from its own command center — they do not belong in this list.
"""

NAV = [
    # ---- Where a shift starts ------------------------------------------
    {"section": "nav.command", "items": [
        ("command_center", "nav.command_center", "grid", "main.dashboard", "view_dashboard"),
        ("my_work", "nav.my_work", "check", "main.my_work", "open_module"),
        ("launcher", "nav.launcher", "apps", "main.launcher", "open_module"),
    ]},

    # ---- 1. Design it: the single source of style truth -----------------
    {"section": "nav.product", "items": [
        ("plm", "nav.plm", "book", "plm.index", "plm_view"),
        ("plm_styles", "nav.plm_styles", "kanban", "plm.styles", "plm_view"),
    ]},

    # ---- 2. Sell it and plan it: what we promised, can we make it, does
    #         it pay -----------------------------------------------------
    {"section": "nav.planning", "items": [
        ("orders", "nav.orders", "boxes", "orders.index", "view_dashboard"),
        ("orders_tna", "nav.orders_tna", "clock", "orders.tna", "view_dashboard"),
        ("planning", "nav.planning_board", "target", "planning.index", "pln_view"),
        ("pln_lines", "nav.pln_lines", "factory", "planning.lines", "pln_view"),
        ("costing", "nav.costing", "wallet", "costing.index", "cost_view"),
        ("cost_orders", "nav.cost_orders", "chart", "costing.orders", "cost_view"),
    ]},

    # ---- 3. Buy for it -------------------------------------------------
    {"section": "nav.procurement_cycle", "items": [
        ("procurement", "nav.proc_home", "cart", "approvals.index", "proc_view"),
        ("proc_new", "nav.proc_new", "report", "approvals.new", "proc_create"),
        ("proc_list", "nav.proc_list", "kanban", "approvals.listing", "proc_view"),
        ("proc_item_requests", "nir.queue", "boxes", "approvals.item_requests", "proc_purchasing"),
        ("proc_vendors", "nav.proc_vendors", "users", "approvals.vendors", "proc_view"),
        ("proc_budgets", "nav.proc_budgets", "wallet", "approvals.budgets", "proc_view"),
        ("proc_delegations", "nav.proc_delegations", "users", "approvals.delegations", "proc_view"),
        ("proc_analytics", "nav.proc_analytics", "chart", "approvals.analytics", "proc_view"),
        ("proc_workflow", "nav.proc_workflow", "map", "approvals.workflow", "proc_view"),
        ("proc_settings", "nav.proc_settings", "settings", "approvals.settings", "proc_admin"),
    ]},

    # ---- 4. Receive the material. ABOVE the floor, because the floor
    #         cannot cut fabric that has not arrived --------------------
    {"section": "nav.sec_materials", "items": [
        ("warehouse", "nav.warehouse", "server", "warehouse.index", "wh_view"),
        ("wh_rolls", "nav.wh_rolls", "boxes", "warehouse.rolls", "wh_view"),
        ("wh_fg", "nav.wh_fg", "kanban", "warehouse.fg", "wh_view"),
    ]},

    # ---- 5. Make it, in the order it is made ---------------------------
    {"section": "nav.factory_floor", "items": [
        ("cutroom", "nav.cutroom", "target", "cutroom.index", "cut_view"),
        ("mes", "nav.mes", "activity", "mes.index", "mes_view"),
        ("mes_board", "nav.mes_board", "grid", "mes.board", "mes_view"),
        ("mes_bundles", "nav.mes_bundles", "boxes", "mes.bundles", "mes_view"),
        ("wash", "nav.wash", "refresh", "wash.index", "wsh_view"),
        ("quality", "nav.quality", "check", "quality.index", "qc_view"),
        ("production", "nav.production", "factory", "production.index", "open_module"),
    ]},

    # ---- 6. Ship it, and be able to prove where it came from -----------
    {"section": "nav.sec_shipping", "items": [
        ("shipping", "nav.shipping", "cart", "shipping.index", "shp_view"),
        ("trace", "nav.trace", "map", "trace.index", "trc_view"),
    ]},

    # ---- Keeping it all running. Eighteen items, so they are ordered in
    #      bands: today's work, then planned work, then parts, then the
    #      asset records, then insight, then setup ----------------------
    {"section": "nav.maintenance", "items": [
        # today
        ("maint_floor", "nav.maint_floor", "target", "maintenance.floor", "maint_view"),
        ("maint_dashboard", "nav.maint_dashboard", "grid", "maintenance.dashboard", "maint_view"),
        ("maint_new", "nav.maint_new", "report", "maintenance.ticket_new", "maint_ticket_create"),
        ("maint_tickets", "nav.maint_tickets", "kanban", "maintenance.tickets", "maint_view"),
        # planned
        ("maint_pm", "nav.maint_pm", "refresh", "maintenance.pm", "maint_view"),
        ("maint_calendar", "nav.maint_calendar", "clock", "maintenance.calendar", "maint_view"),
        # parts
        ("maint_spares", "nav.maint_spares", "boxes", "maintenance.spares", "maint_view"),
        ("maint_stock", "nav.maint_stock", "server", "maintenance.stock", "maint_view"),
        ("maint_requests", "nav.maint_requests", "cart", "maintenance.requests", "maint_view"),
        ("maint_approvals", "nav.maint_approvals", "check", "maintenance.approvals", "maint_approve"),
        # the assets themselves
        ("maint_machines", "nav.maint_machines", "factory", "maintenance.machines", "maint_view"),
        ("maint_locations", "nav.maint_locations", "map", "maintenance.locations", "maint_view"),
        ("maint_needle", "nav.maint_needle", "wallet", "maintenance.needle_costs", "maint_view"),
        # insight
        ("maint_ai", "nav.maint_ai", "sparkles", "maintenance.ai_insights", "maint_view"),
        ("maint_reports", "nav.maint_reports", "chart", "maintenance.reports", "maint_view"),
        # setup
        ("maint_workflow", "nav.maint_workflow", "map", "maintenance.workflow", "maint_view"),
        ("maint_import", "nav.maint_import", "boxes", "maintenance.import_page", "maint_admin"),
        ("maint_settings", "nav.maint_settings", "settings", "maintenance.settings", "maint_admin"),
    ]},

    # ---- The people who do the work: the workforce first, then the
    #      probation lifecycle in the order a case moves ----------------
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

    # ---- Watching all of the above. Smart Factory is ONE door here; its
    #      thirteen sub-pages duplicated the native modules ------------
    {"section": "nav.intelligence", "items": [
        ("ai_center", "nav.ai_center", "sparkles", "intelligence.command_center", "view_dashboard"),
        ("ai_alerts", "nav.ai_alerts", "alert", "intelligence.alerts", "view_dashboard"),
        ("ai_breakdown", "nav.ai_breakdown", "chart", "intelligence.breakdown", "view_dashboard"),
        ("ai_assistant", "nav.ai_assistant", "robot", "intelligence.assistant", "view_dashboard"),
        ("bi", "nav.bi", "chart", "bi.index", "open_module"),
        ("sf_cc", "nav.smart_factory", "cpu", "smartfactory.command_center", "view_dashboard"),
        ("ai_reports", "nav.ai_reports", "report", "intelligence.reports", "view_dashboard"),
        ("ai_settings", "nav.ai_settings", "settings", "intelligence.settings", "access_admin"),
    ]},

    # ---- The four separate systems this platform fronts ----------------
    {"section": "nav.operations", "items": [
        ("itsm", "nav.service_desk", "headset", "main.module", "open_module"),
        ("assets", "nav.assets", "boxes", "main.module", "open_module"),
        ("monitoring", "nav.monitoring", "activity", "main.module", "open_module"),
        ("commandtrack", "nav.work", "kanban", "main.module", "open_module"),
        ("registry", "nav.registry", "users", "main.registry", "open_module"),
    ]},

    # ---- Proving we run it properly -----------------------------------
    {"section": "nav.govern", "items": [
        ("compliance", "nav.compliance", "shield", "compliance.index", "cmp_view"),
        ("cmp_audits", "nav.cmp_audits", "check", "compliance.audits", "cmp_view"),
        ("cmp_certs", "nav.cmp_certs", "book", "compliance.certs", "cmp_view"),
        ("governance", "nav.governance", "map", "governance.index", "access_admin"),
    ]},

    # ---- Reporting and the state of the platform itself.
    #      The two report pages are NOT duplicates and are now named for
    #      what each actually holds: the hub is where every module
    #      registers its own reports (PLM, MES, quality, wash, warehouse,
    #      planning, costing, people, compliance, shipping, trace,
    #      procurement); "Platform Reports" is the fixed set that owns the
    #      audit log, users, notifications, maintenance and production.
    {"section": "nav.sec_system", "items": [
        ("reporting", "nav.reporting", "chart", "reports_hub.hub", "view_reports"),
        ("reports", "nav.reports", "report", "main.reports", "view_reports"),
        ("health", "nav.health", "pulse", "main.health", "view_system_health"),
        ("roadmap", "nav.roadmap", "map", "main.roadmap", "view_dashboard"),
    ]},

    {"section": "nav.admin", "items": [
        ("admin", "nav.admin_center", "settings", "admin.index", "access_admin"),
        ("registrations", "nav.registrations", "users", "admin.registrations", "users_view"),
        ("garamento_kb", "nav.garamento_kb", "book", "admin.garamento", "access_admin"),
    ]},

    # ---- NOT RENDERED IN THE SIDEBAR ----------------------------------
    # Every item below is in WIP_KEYS, so the sidebar filters this section
    # to empty and drops it. They stay declared here on purpose: the module
    # dispatcher and the App Launcher (which reads the `systems` table) still
    # serve them, and keeping them listed means WIP_KEYS has something to
    # point at. Move an item up into a real section the day it is built.
    {"section": "nav.services", "items": [
        ("ai_hub", "nav.ai_hub", "sparkles", "main.module", "open_module"),
        ("finance", "nav.finance", "wallet", "main.module", "open_module"),
        ("automation", "nav.automation", "robot", "main.module", "open_module"),
        ("hr", "nav.hr_services", "users", "main.module", "open_module"),
        ("saplite", "nav.saplite", "server", "main.module", "open_module"),
        ("kb", "nav.kb", "book", "main.module", "open_module"),
    ]},
]

# Modules that are still placeholders (a "coming soon" landing page, not built
# out). They are hidden from the sidebar for EVERYONE, including admins — six
# dead entries mixed in with working modules was a large part of why the menu
# felt confusing. They remain reachable from the App Launcher, which lists the
# `systems` table rather than this file. Move a key out of here the moment its
# module becomes real.
WIP_KEYS = {"ai_hub", "finance", "automation", "hr", "saplite", "kb"}
