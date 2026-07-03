"""
TC Platform — sidebar navigation definition.

Each item: (key, i18n_key, icon, endpoint, permission). Sections group items.
Menu visibility is enforced by `permission` against the user's role.
"""

NAV = [
    {"section": "nav.command", "items": [
        ("command_center", "nav.command_center", "grid", "main.dashboard", "view_dashboard"),
        ("launcher", "nav.launcher", "apps", "main.launcher", "open_module"),
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
    ]},
    {"section": "nav.services", "items": [
        ("hr", "nav.hr", "users", "main.module", "open_module"),
        ("saplite", "nav.saplite", "server", "main.module", "open_module"),
        ("kb", "nav.kb", "book", "main.module", "open_module"),
    ]},
    {"section": "nav.govern", "items": [
        ("reports", "nav.reports", "report", "main.reports", "view_reports"),
        ("governance", "nav.governance", "shield", "main.module", "open_module"),
        ("health", "nav.health", "pulse", "main.health", "view_system_health"),
        ("roadmap", "nav.roadmap", "map", "main.roadmap", "view_dashboard"),
    ]},
    {"section": "nav.admin", "items": [
        ("admin", "nav.admin_center", "settings", "admin.index", "access_admin"),
    ]},
]
