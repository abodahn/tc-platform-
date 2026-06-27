"""
TC Platform — read-only seed content for the future-ready module pages.

This data is intentionally kept in Python (not the DB) so the placeholder
modules render rich, realistic tables without polluting the metadata database.
Replace these with live API adapters as each integration matures (Roadmap P3+).
"""

AI_USE_CASES = [
    ("AI Helpdesk Assistant", "IT", "Slow first response on tickets", "High", "in_progress", "IT Service Desk", "high", "P2"),
    ("AI Ticket Classification", "IT", "Manual triage and routing", "Medium", "planned", "IT Service Desk", "high", "P2"),
    ("AI SLA Risk Prediction", "IT", "SLA breaches detected too late", "High", "planned", "IT Operations", "medium", "P3"),
    ("AI Asset Data Cleaning", "IT", "Inconsistent asset records", "Medium", "planned", "Asset Governance", "medium", "P3"),
    ("AI Production Anomaly Insights", "Production", "Late downtime detection", "High", "research", "Manufacturing", "high", "P4"),
    ("AI Finance Document Assistant", "Finance", "Manual invoice reading", "High", "research", "Finance", "high", "P4"),
    ("AI Procurement Comparison", "Procurement", "Slow vendor comparison", "Medium", "research", "Procurement", "medium", "P4"),
    ("AI HR Policy Assistant", "HR", "Repetitive policy questions", "Low", "research", "HR", "low", "P5"),
    ("AI Knowledge Base Search", "IT", "Hard to find KB answers", "Medium", "planned", "Knowledge", "medium", "P3"),
    ("AI Daily Executive Summary", "Executive", "Manual status compilation", "Medium", "research", "Digital Transformation", "high", "P5"),
]

AUTOMATION_PROCESSES = [
    ("Excel Report Automation", "Finance", "8 h/week", "Weekly", "Power Automate", "in_progress", "8 h/week", "Low", "Finance Ops"),
    ("Email Notification Automation", "IT", "5 h/week", "Daily", "Python script", "live", "5 h/week", "Low", "IT Ops"),
    ("Ticket Escalation Automation", "IT", "4 h/week", "Continuous", "ITSM rules", "in_progress", "4 h/week", "Medium", "Service Desk"),
    ("Asset Audit Automation", "IT", "12 h/quarter", "Quarterly", "Asset API", "planned", "12 h/quarter", "Medium", "Asset Gov"),
    ("Finance Invoice Tracking", "Finance", "10 h/week", "Daily", "RPA + OCR", "planned", "10 h/week", "High", "Finance"),
    ("HR Onboarding Workflow", "HR", "6 h/hire", "On demand", "Workflow engine", "planned", "6 h/hire", "Medium", "HR"),
    ("Procurement Approval Workflow", "Procurement", "5 h/week", "Continuous", "Workflow engine", "planned", "5 h/week", "Medium", "Procurement"),
    ("Production Report Consolidation", "Production", "9 h/week", "Daily", "Power BI + API", "research", "9 h/week", "High", "Manufacturing"),
]

FINANCE_ITEMS = [
    ("Invoice Workflow Digitalization", "Capex", "₺ 1,250,000", "in_progress", "Finance", "Reduce manual invoice handling"),
    ("Payment Approval Tracking", "Opex", "₺ 320,000", "live", "Finance", "End-to-end approval visibility"),
    ("Budget Monitoring Dashboard", "Opex", "₺ 180,000", "in_progress", "FP&A", "Real-time budget vs actual"),
    ("Excel-to-System Migration", "Capex", "₺ 540,000", "planned", "Finance IT", "Move ledgers off spreadsheets"),
    ("Vendor Payment Status Portal", "Opex", "₺ 95,000", "planned", "AP Team", "Self-service vendor status"),
]

BI_DASHBOARDS = [
    ("IT Executive Dashboard", "IT Director", "Daily", "live", "2026-06-24"),
    ("Finance Dashboard", "CFO Office", "Daily", "live", "2026-06-24"),
    ("Assets Dashboard", "Asset Manager", "Weekly", "in_progress", "2026-06-20"),
    ("Service Desk Dashboard", "Service Desk Lead", "Hourly", "live", "2026-06-25"),
    ("Production Dashboard", "Plant Manager", "Shift", "planned", "—"),
    ("HR Dashboard", "HR Director", "Weekly", "planned", "—"),
    ("Procurement Dashboard", "Procurement Lead", "Weekly", "planned", "—"),
    ("Cost Saving Dashboard", "Transformation Office", "Monthly", "in_progress", "2026-06-18"),
]

PRODUCTION_LINES = [
    ("Cutting Line A", "running", "96%", "0", "1", "Low"),
    ("Sewing Line 1", "running", "91%", "1", "0", "Medium"),
    ("Sewing Line 2", "maintenance", "—", "1", "0", "High"),
    ("Finishing Line", "running", "88%", "0", "2", "Medium"),
    ("Packing Line", "idle", "—", "0", "0", "Low"),
]

HR_SERVICES = [
    ("Employee Service Requests", "live", "Self-service portal for HR requests"),
    ("Onboarding Checklist", "in_progress", "Standardized new-hire workflow"),
    ("Offboarding Checklist", "planned", "Exit process and asset return"),
    ("Policy Documents", "live", "Central HR policy library"),
    ("Training Tracker", "planned", "Skills and compliance training"),
    ("AI HR Assistant", "research", "Answer policy questions automatically"),
]

PROCUREMENT_ITEMS = [
    ("PR-2041", "Laptops x15", "IT", "Pending IT approval", "₺ 420,000"),
    ("PR-2042", "Sewing machine parts", "Production", "Approved", "₺ 86,000"),
    ("PR-2043", "Office furniture", "Admin", "Pending finance", "₺ 54,000"),
    ("PR-2044", "Network switches", "IT", "PO issued", "₺ 132,000"),
]

GOVERNANCE_ITEMS = [
    ("SLA Governance", "active", "Monthly SLA review board"),
    ("IT Policy Library", "active", "Centralised IT policies"),
    ("Access Review", "in_progress", "Quarterly access recertification"),
    ("Backup Status", "active", "Daily backup verification"),
    ("Risk Register", "active", "Enterprise IT risk register"),
    ("Change Advisory Board (CAB)", "active", "Weekly change approvals"),
    ("Compliance Checklist", "in_progress", "Regulatory compliance tracking"),
    ("Security Baseline", "active", "Windows server hardening baseline"),
    ("DR Readiness", "in_progress", "Disaster recovery test plan"),
]

REPORTS = [
    ("Executive Summary", "All modules", "Cross-platform executive KPIs"),
    ("IT Operations Report", "ITSM + Monitoring", "Service desk and infrastructure"),
    ("SLA Report", "ITSM", "SLA compliance and breaches"),
    ("Asset Report", "Assets", "Asset lifecycle and inventory"),
    ("Monitoring Report", "Monitoring", "Server uptime and alerts"),
    ("Project / Task Report", "CommandTrack", "Delivery and workload"),
    ("Finance Transformation Report", "Finance", "Digitalization progress"),
    ("Automation Progress Report", "Automation", "Hours saved and pipeline"),
    ("Cost Saving Report", "All modules", "Realized and projected savings"),
    ("Production Visibility Report", "Production", "Line efficiency and downtime"),
]

# Plain-language explanation shown at the top of each module page so anyone
# (executive or new user) understands what it is and why it exists.
# (title, one-line purpose, [capability bullets], status_note)
MODULE_ABOUT = {
    "ai_hub": (
        "A control room for T&C's Artificial Intelligence journey.",
        "It is the single place where every AI idea is captured, costed and tracked - so leadership can see where AI will save money and time across IT, Finance, Production, HR and Procurement.",
        ["Catalogue every AI use-case in one portfolio",
         "Estimate expected savings and ROI per idea",
         "Track each from research -> planned -> in progress -> live",
         "Prioritise by business value and effort"],
        "Planning module - the list below is live and ready to be filled with your real initiatives.",
    ),
    "finance": (
        "The digital transformation of T&C's Finance department.",
        "It replaces manual, Excel-based finance work with tracked digital workflows - invoices, approvals, budgets and a clear view of where costs can be cut.",
        ["Digital invoice and payment-approval workflow",
         "Budget vs actual monitoring (CAPEX / OPEX)",
         "Cost-saving tracker with expected value",
         "Excel-to-system migration progress"],
        "Roadmap module - structure is ready to connect to live finance data (Phase 4-6).",
    ),
    "automation": (
        "T&C's hub for Automation & RPA (Robotic Process Automation).",
        "It finds repetitive manual tasks across departments and turns them into automated processes, then measures the hours saved.",
        ["Pipeline of processes to automate",
         "Manual effort and estimated time saved per process",
         "Recommended tool (Power Automate, scripts, RPA)",
         "Status from idea to live automation"],
        "Roadmap module - ready to track your real automation backlog.",
    ),
    "bi": (
        "A single portal for all Power BI / Business Intelligence dashboards.",
        "Instead of hunting for reports, every executive and operational dashboard lives here with its owner, refresh schedule and status.",
        ["One catalogue of all BI dashboards",
         "Owner and refresh frequency per dashboard",
         "Direct links to Power BI (placeholders now)",
         "Status and last-update visibility"],
        "Roadmap module - paste your Power BI links per card when ready.",
    ),
    "production": (
        "Shop-floor visibility for T&C's garment manufacturing.",
        "It brings production lines, machine status, downtime and quality into one live view so the plant and management share the same picture.",
        ["Production line status and efficiency KPIs",
         "Downtime and quality-issue tracking",
         "Maintenance request visibility",
         "Future link to IoT / shop-floor systems"],
        "Roadmap module - ready for integration with production/IoT sources (Phase 6).",
    ),
    "hr": (
        "Digital HR services for T&C employees.",
        "A self-service home for HR: requests, onboarding, policies and approvals - reducing paperwork and email back-and-forth.",
        ["Employee service requests",
         "Onboarding / offboarding checklists",
         "Central policy library and training tracker",
         "HR approval workflows"],
        "Roadmap module - ready to wire to HR data and approvals.",
    ),
    "procurement": (
        "End-to-end Procurement for T&C.",
        "From a purchase request to an approved purchase order and delivery - tracked in one place and linked to assets and inventory.",
        ["Purchase requests and approval workflow",
         "Vendor comparison and PO tracking",
         "Delivery tracking",
         "Links to budget, assets and inventory"],
        "Roadmap module - ready to connect to procurement data.",
    ),
    "governance": (
        "IT Governance & Compliance for T&C.",
        "The oversight layer: SLAs, policies, audits, risk and disaster-recovery readiness - so IT runs in a controlled, auditable way.",
        ["SLA governance and IT policy library",
         "Access reviews and audit logs",
         "Risk register and change advisory board (CAB)",
         "Security baseline and DR readiness"],
        "Governance module - tracks the controls that keep operations safe.",
    ),
    "kb": (
        "A unified Knowledge Base across all T&C systems.",
        "One searchable place for how-to guides and answers, so staff solve problems without raising a ticket.",
        ["Articles across ITSM, Assets, Monitoring and Work",
         "How-to guides and FAQs",
         "Reduces repeat service-desk tickets",
         "Future AI-powered search"],
        "Roadmap module - ready to fill with real articles.",
    ),
    "saplite": (
        "Lightweight support around T&C's SAP / ERP processes.",
        "A simple intake for SAP-related requests and process notes while deeper ERP integration matures.",
        ["SAP master-data and request intake",
         "Process notes by module (MM, CO, ...)",
         "Bridge to full ERP integration later"],
        "Roadmap module - placeholder for ERP support.",
    ),
}

ROADMAP = [
    ("Phase 1", "Unified Portal", "done",
     "Unified portal, app launcher, design system, health checks, trilingual UI."),
    ("Phase 2", "Unified Login & RBAC", "in_progress",
     "Unified login and role-based access across all apps."),
    ("Phase 3", "Notifications & APIs", "planned",
     "Shared notification center and deeper API integration."),
    ("Phase 4", "Unified Reporting", "planned",
     "Unified reporting and executive dashboards."),
    ("Phase 5", "AI & Automation", "planned",
     "AI assistant and automation orchestration."),
    ("Phase 6", "Production & Finance Tower", "planned",
     "Production visibility and finance digital control tower."),
]
