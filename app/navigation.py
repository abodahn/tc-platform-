"""
TC Platform — sidebar navigation definition.

Item: (key, i18n_key, icon, endpoint, permission[, band]).
The optional 6th element groups items under a small label INSIDE a section.

WHY IT IS SHAPED THIS WAY

Fourteen collapsed section headers plus one open section of nineteen flat rows is
about thirty rows on screen at rest, which is the "too long" problem. So there
are now EIGHT top-level domains, and the big ones carry visible sub-bands.

Bands are a property of the ITEMS, not a separate list, which matters: the
permission filter runs per item, so a band whose every item is hidden from a
user simply never appears. A parallel list of headings would leave orphan labels
above nothing for scope-restricted users.

ORDER IS THE FLOW OF A GARMENT ORDER:

    design it -> sell and plan it -> buy for it -> make it -> ship it

then the functions that keep that running (maintenance, people), then the layers
that watch it, then govern and administer it. Materials sits ABOVE the floor
that consumes it. A supervisor reading top to bottom walks the factory in the
order the work happens.

TWO RULES, learned from the mess this replaces:

  1. ONE NAME PER JOB. "Quality" and "AI Insights" each appeared twice under
     different sections, and "Reports Center"/"Reports Centre" were two different
     pages one letter apart. The sidebar has a filter box, which flattens
     sections, so identical names are genuinely ambiguous and not just untidy.
  2. Smart Factory is ONE entry, not thirteen. Its sub-pages duplicated the
     native modules and are reachable from its own command center.
"""

NAV = [
    # ---- Where a shift starts -----------------------------------------
    {"section": "nav.g_home", "items": [
        ("command_center", "nav.command_center", "grid", "main.dashboard", "view_dashboard"),
        ("my_work", "nav.my_work", "check", "main.my_work", "open_module"),
        ("launcher", "nav.launcher", "apps", "main.launcher", "open_module"),
    ]},

    # ---- What we promised a client, and whether it pays ---------------
    {"section": "nav.g_orders", "items": [
        ("plm", "nav.plm", "book", "plm.index", "plm_view", "nav.b_product"),
        ("plm_styles", "nav.plm_styles", "kanban", "plm.styles", "plm_view", "nav.b_product"),
        ("orders", "nav.orders", "boxes", "orders.index", "view_dashboard", "nav.b_order"),
        ("orders_tna", "nav.orders_tna", "clock", "orders.tna", "view_dashboard", "nav.b_order"),
        ("planning", "nav.planning_board", "target", "planning.index", "pln_view", "nav.b_plan"),
        ("pln_lines", "nav.pln_lines", "factory", "planning.lines", "pln_view", "nav.b_plan"),
        ("costing", "nav.costing", "wallet", "costing.index", "cost_view", "nav.b_money"),
        ("cost_orders", "nav.cost_orders", "chart", "costing.orders", "cost_view", "nav.b_money"),
    ]},

    # ---- Buying for it ------------------------------------------------
    {"section": "nav.procurement_cycle", "items": [
        ("procurement", "nav.proc_home", "cart", "approvals.index", "proc_view", "nav.b_requests"),
        ("proc_new", "nav.proc_new", "report", "approvals.new", "proc_create", "nav.b_requests"),
        ("proc_list", "nav.proc_list", "kanban", "approvals.listing", "proc_view", "nav.b_requests"),
        # Sits with the requests, not under Insight: "who is sitting on my PR" is
        # something a buyer acts on this morning, not something they report on.
        ("proc_aging", "aging.nav", "clock", "proc_aging.index", "proc_view", "nav.b_requests"),
        # DOAM §3.4 — the register that makes "...or agreed forecast" real.
        ("proc_forecasts", "nav.proc_forecasts", "target", "approvals.forecasts", "proc_view", "nav.b_requests"),
        ("proc_item_requests", "nir.queue", "boxes", "approvals.item_requests", "proc_purchasing", "nav.b_requests"),
        # The store maintains the item master, so this sits with the requests the
        # store already works from rather than behind the admin import screen.
        ("proc_items", "items.nav", "boxes", "approvals.items", "proc_catalogue", "nav.b_requests"),
        ("proc_vendors", "nav.proc_vendors", "users", "approvals.vendors", "proc_view", "nav.b_suppliers"),
        ("proc_budgets", "nav.proc_budgets", "wallet", "approvals.budgets", "proc_view", "nav.b_suppliers"),
        ("proc_delegations", "nav.proc_delegations", "users", "approvals.delegations", "proc_view", "nav.b_suppliers"),
        ("proc_analytics", "nav.proc_analytics", "chart", "approvals.analytics", "proc_view", "nav.b_insight"),
        ("proc_workflow", "nav.proc_workflow", "map", "approvals.workflow", "proc_view", "nav.b_setup"),
        ("proc_settings", "nav.proc_settings", "settings", "approvals.settings", "proc_admin", "nav.b_setup"),
    ]},

    # ---- Making it. Material in, then cut, sew, wash, inspect, ship ---
    {"section": "nav.g_factory", "items": [
        ("warehouse", "nav.warehouse", "server", "warehouse.index", "wh_view", "nav.b_materials"),
        ("wh_rolls", "nav.wh_rolls", "boxes", "warehouse.rolls", "wh_view", "nav.b_materials"),
        ("wh_fg", "nav.wh_fg", "kanban", "warehouse.fg", "wh_view", "nav.b_materials"),
        ("cutroom", "nav.cutroom", "target", "cutroom.index", "cut_view", "nav.b_make"),
        ("mes", "nav.mes", "activity", "mes.index", "mes_view", "nav.b_make"),
        ("mes_board", "nav.mes_board", "grid", "mes.board", "mes_view", "nav.b_make"),
        ("mes_bundles", "nav.mes_bundles", "boxes", "mes.bundles", "mes_view", "nav.b_make"),
        ("wash", "nav.wash", "refresh", "wash.index", "wsh_view", "nav.b_make"),
        ("quality", "nav.quality", "check", "quality.index", "qc_view", "nav.b_make"),
        ("production", "nav.production", "factory", "production.index", "open_module", "nav.b_make"),
        ("shipping", "nav.shipping", "cart", "shipping.index", "shp_view", "nav.b_out"),
        ("trace", "nav.trace", "map", "trace.index", "trc_view", "nav.b_out"),
    ]},

    # ---- Keeping it running. Nineteen items, so they are banded -------
    {"section": "nav.maintenance", "items": [
        ("maint_floor", "nav.maint_floor", "target", "maintenance.floor", "maint_view", "nav.b_today"),
        ("maint_dashboard", "nav.maint_dashboard", "grid", "maintenance.dashboard", "maint_view", "nav.b_today"),
        ("maint_new", "nav.maint_new", "report", "maintenance.ticket_new", "maint_ticket_create", "nav.b_today"),
        ("maint_tickets", "nav.maint_tickets", "kanban", "maintenance.tickets", "maint_view", "nav.b_today"),
        ("maint_pm", "nav.maint_pm", "refresh", "maintenance.pm", "maint_view", "nav.b_planned"),
        ("maint_calendar", "nav.maint_calendar", "clock", "maintenance.calendar", "maint_view", "nav.b_planned"),
        # The engineering justification comes BEFORE a requisition exists, so it
        # leads the parts band (DOAM §6).
        ("maint_ejr", "nav.maint_ejr", "shield", "maintenance.justifications", "maint_view", "nav.b_parts"),
        ("maint_spares", "nav.maint_spares", "boxes", "maintenance.spares", "maint_view", "nav.b_parts"),
        ("maint_stock", "nav.maint_stock", "server", "maintenance.stock", "maint_view", "nav.b_parts"),
        ("maint_requests", "nav.maint_requests", "cart", "maintenance.requests", "maint_view", "nav.b_parts"),
        ("maint_approvals", "nav.maint_approvals", "check", "maintenance.approvals", "maint_approve", "nav.b_parts"),
        ("maint_machines", "nav.maint_machines", "factory", "maintenance.machines", "maint_view", "nav.b_assets"),
        ("maint_locations", "nav.maint_locations", "map", "maintenance.locations", "maint_view", "nav.b_assets"),
        ("maint_needle", "nav.maint_needle", "wallet", "maintenance.needle_costs", "maint_view", "nav.b_assets"),
        ("maint_ai", "nav.maint_ai", "sparkles", "maintenance.ai_insights", "maint_view", "nav.b_insight"),
        ("maint_reports", "nav.maint_reports", "chart", "maintenance.reports", "maint_view", "nav.b_insight"),
        ("maint_workflow", "nav.maint_workflow", "map", "maintenance.workflow", "maint_view", "nav.b_setup"),
        ("maint_import", "nav.maint_import", "boxes", "maintenance.import_page", "maint_admin", "nav.b_setup"),
        ("maint_settings", "nav.maint_settings", "settings", "maintenance.settings", "maint_admin", "nav.b_setup"),
    ]},

    # ---- The people who do the work ----------------------------------
    {"section": "nav.hr", "items": [
        ("people", "nav.people", "users", "people.index", "ppl_view", "nav.b_workforce"),
        ("ppl_attendance", "nav.ppl_attendance", "check", "people.attendance", "ppl_view", "nav.b_workforce"),
        ("ppl_leave", "nav.ppl_leave", "clock", "people.leave", "ppl_view", "nav.b_workforce"),
        ("ppl_skills", "nav.ppl_skills", "trend", "people.skills", "ppl_view", "nav.b_workforce"),
        ("prob_dashboard", "nav.prob_dashboard", "grid", "probation.dashboard", "prob_view", "nav.b_probation"),
        ("prob_mine", "nav.prob_mine", "check", "probation.mine", "prob_view", "nav.b_probation"),
        ("prob_cases", "nav.prob_cases", "users", "probation.cases", "prob_view", "nav.b_probation"),
        ("prob_initiate", "nav.prob_initiate", "report", "probation.initiate", "prob_hr_review", "nav.b_probation"),
        ("prob_review", "nav.prob_review", "check", "probation.review", "prob_hr_review", "nav.b_probation"),
        ("prob_reminders", "nav.prob_reminders", "clock", "probation.reminders", "prob_hr_review", "nav.b_probation"),
        ("prob_reports", "nav.prob_reports", "chart", "probation.reports", "prob_reports", "nav.b_probation"),
        ("prob_import", "nav.prob_import", "boxes", "probation.import_data", "prob_import", "nav.b_setup"),
        ("prob_settings", "nav.prob_settings", "settings", "probation.settings", "prob_admin", "nav.b_setup"),
    ]},

    # ---- Watching all of it. Smart Factory is ONE door here ----------
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

    # ---- The other systems, proving we run it properly, and admin ----
    {"section": "nav.g_systems", "items": [
        ("itsm", "nav.service_desk", "headset", "main.module", "open_module", "nav.b_connected"),
        ("assets", "nav.assets", "boxes", "main.module", "open_module", "nav.b_connected"),
        ("monitoring", "nav.monitoring", "activity", "main.module", "open_module", "nav.b_connected"),
        ("commandtrack", "nav.work", "kanban", "main.module", "open_module", "nav.b_connected"),
        ("registry", "nav.registry", "users", "main.registry", "open_module", "nav.b_connected"),
        ("compliance", "nav.compliance", "shield", "compliance.index", "cmp_view", "nav.b_compliance"),
        ("cmp_audits", "nav.cmp_audits", "check", "compliance.audits", "cmp_view", "nav.b_compliance"),
        ("cmp_certs", "nav.cmp_certs", "book", "compliance.certs", "cmp_view", "nav.b_compliance"),
        ("governance", "nav.governance", "map", "governance.index", "access_admin", "nav.b_compliance"),
        # The two report pages are NOT duplicates and are named for what each
        # holds: the hub is where every module registers its own reports;
        # "Platform Reports" owns the audit log, users, notifications,
        # maintenance and production.
        ("reporting", "nav.reporting", "chart", "reports_hub.hub", "view_reports", "nav.b_reports"),
        ("reports", "nav.reports", "report", "main.reports", "view_reports", "nav.b_reports"),
        ("health", "nav.health", "pulse", "main.health", "view_system_health", "nav.b_system"),
        ("roadmap", "nav.roadmap", "map", "main.roadmap", "view_dashboard", "nav.b_system"),
        ("admin", "nav.admin_center", "settings", "admin.index", "access_admin", "nav.b_admin"),
        ("registrations", "nav.registrations", "users", "admin.registrations", "users_view", "nav.b_admin"),
        ("garamento_kb", "nav.garamento_kb", "book", "admin.garamento", "access_admin", "nav.b_admin"),
    ]},

    # ---- NOT RENDERED IN THE SIDEBAR ---------------------------------
    # Every item below is in WIP_KEYS, so the sidebar filters this section to
    # empty and drops it. They stay declared on purpose: the module dispatcher
    # and the App Launcher (which reads the `systems` table) still serve them,
    # and keeping them listed means WIP_KEYS has something to point at. Move an
    # item into a real section the day it is built.
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
# out). Hidden from the sidebar for EVERYONE, admins included — six dead entries
# mixed in with working modules was a large part of why the menu felt confusing.
# They remain reachable from the App Launcher, which lists the `systems` table
# rather than this file. Move a key out of here the moment its module is real.
WIP_KEYS = {"ai_hub", "finance", "automation", "hr", "saplite", "kb"}


def band_of(item):
    """The band label for an item, or None. Items are 5- or 6-tuples, so this
    is the one place that difference is handled."""
    return item[5] if len(item) > 5 else None
