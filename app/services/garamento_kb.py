"""
TC Platform — Garamento's knowledge base (the built-in user manual).

A curated, structured how-to guide for the platform, plus a lightweight
keyword retriever. `context_for(query)` returns an always-on topic index plus
the few sections most relevant to the user's question, sized to a small token
budget, to be injected into Garamento's system prompt. No vector DB, no extra
API — grounded, cheap, and easy to maintain: edit TOPICS when the app changes.
"""
from __future__ import annotations

import re

# Each topic: id, title, keywords (incl. a few AR/TR aliases for recall), body.
# Bodies are concise, numbered steps grounded in the real sidebar/navigation.
TOPICS = [
    {
        "id": "basics",
        "title": "Signing in & the basics",
        "keywords": ["login", "log in", "sign in", "password", "language", "arabic",
                     "turkish", "theme", "dark", "light", "start", "دخول", "giriş"],
        "body": (
            "Signing in & getting around:\n"
            "1. Open the platform URL and sign in with your username and password (ask an admin if you don't have one).\n"
            "2. Top-right: switch language (EN / ع / TR), switch theme (light / dark / auto), and open the notifications bell.\n"
            "3. Press Ctrl+K (⌘K on Mac) anywhere to open the command palette and jump to any module or record.\n"
            "4. The left sidebar is grouped (Command, Operations, Maintenance, Transformation, Procurement, Services, Govern, Admin). Use the 'Filter menu' box to find an item, and the star to pin favourites."
        ),
    },
    {
        "id": "maint_ticket_new",
        "title": "Open / report a maintenance ticket",
        "keywords": ["open ticket", "new ticket", "report", "fault", "breakdown",
                     "maintenance ticket", "raise ticket", "create ticket", "machine down",
                     "تذكرة", "صيانة", "عطل", "arıza", "bakım"],
        "body": (
            "Open a maintenance ticket:\n"
            "1. Sidebar → Maintenance → 'Report / New ticket'.\n"
            "2. Describe the problem, pick the machine/asset and area, set priority, and attach a photo or voice note if useful.\n"
            "3. Submit — the ticket appears on the Maintenance → Tickets board and the right people are notified.\n"
            "Tip: you can also start a purchase request straight from a ticket when a repair needs a spare part."
        ),
    },
    {
        "id": "maint_ticket_work",
        "title": "Assign, work and close a ticket",
        "keywords": ["assign", "assignee", "assign ticket", "take ticket", "status",
                     "in progress", "close ticket", "resolve", "technician", "board",
                     "تعيين", "atama", "ata"],
        "body": (
            "Work a ticket (Maintenance → Tickets):\n"
            "1. Open the ticket from the Tickets board (or via search / command palette).\n"
            "2. Assign it to a technician, set the status (e.g. In progress), and add notes or photo proof as work happens.\n"
            "3. Some tickets need sign-off — approvers act under Maintenance → Approvals.\n"
            "4. Mark it resolved/closed when done; it stays searchable for history and reports (Maintenance → Reports)."
        ),
    },
    {
        "id": "maint_more",
        "title": "Machines, spares, PM & calendar",
        "keywords": ["machine", "machines", "spare", "spares", "stock", "inventory",
                     "preventive", "pm", "schedule", "calendar", "requests"],
        "body": (
            "Other maintenance tools (Maintenance group):\n"
            "- Machines: the equipment register and each machine's history.\n"
            "- Spares / Stock: spare parts and stock levels; Requests: ask for parts.\n"
            "- PM: preventive-maintenance plans; Calendar: what's scheduled.\n"
            "- Floor: live shop-floor view; AI Insights: predicted issues; Reports: KPIs and exports.\n"
            "- Settings (admins) configures the maintenance module."
        ),
    },
    {
        "id": "service_desk",
        "title": "IT Service Desk, Assets, Monitoring, Work",
        "keywords": ["service desk", "itsm", "it ticket", "helpdesk", "asset", "assets",
                     "monitoring", "work", "kanban", "commandtrack", "launcher", "open module"],
        "body": (
            "Operations group:\n"
            "- Service Desk (ITSM): IT tickets/incidents. Assets: the asset inventory. Monitoring: system health. Work: the kanban work board.\n"
            "- These open the connected systems; if single sign-on is enabled they open logged-in automatically (no second password).\n"
            "- The Launcher (Command group) is a tile view of every module you can open."
        ),
    },
    {
        "id": "registry",
        "title": "Registry — people & assets directory",
        "keywords": ["registry", "directory", "people", "employees", "who", "staff", "find person"],
        "body": (
            "Registry (Operations → Registry) is the shared directory of employees and assets across all "
            "systems. Search a person or asset to see their details and deep-links into the relevant module."
        ),
    },
    {
        "id": "proc_new",
        "title": "Raise a purchase request (PR)",
        "keywords": ["purchase", "purchase request", "pr", "buy", "procurement", "raise pr",
                     "new request", "requisition", "order", "line item", "attachment",
                     "شراء", "طلب شراء", "satın", "satınalma", "talep"],
        "body": (
            "Raise a purchase request (Procurement → New request):\n"
            "1. Fill the details — title, department, vendor, currency, payment & delivery (mostly dropdowns).\n"
            "2. Add line items: item, description, unit, qty, unit price. Use the 🔎 button to research a market price, or 'Price all items' to price them together.\n"
            "3. Attach quotations/specs if you have them; use the ✨ button to auto-polish the wording.\n"
            "4. Submit for approval — the approval route is chosen automatically from the total and the department's responsibility matrix. Or Save as draft to finish later."
        ),
    },
    {
        "id": "proc_approve",
        "title": "Approve / sign a purchase request",
        "keywords": ["approve", "approval", "sign", "authorize", "reject", "ladder",
                     "my stage", "pending", "signature", "موافقة", "توقيع", "onay", "imza"],
        "body": (
            "Approve requests (Procurement):\n"
            "1. Requests waiting on you show up in the Procurement list and the notifications bell.\n"
            "2. Open the request, review the line items and totals, then Approve (sign) or Reject with a comment.\n"
            "3. Approval is a ladder by amount/department (e.g. warehouse → factory manager → purchasing → finance → CFO → CEO); each required approver signs in turn.\n"
            "4. Once fully approved, a Purchase Order is generated automatically. Set up your signature first in Profile (see 'Digital signature')."
        ),
    },
    {
        "id": "proc_more",
        "title": "Vendors, budgets, delegations & the responsibility matrix",
        "keywords": ["vendor", "vendors", "supplier", "budget", "budgets", "delegation",
                     "delegate", "responsibility matrix", "proc settings", "analytics", "po"],
        "body": (
            "Procurement group:\n"
            "- List: all requests; Analytics: spend/cycle KPIs; Vendors: supplier list; Budgets: department budgets.\n"
            "- Delegations: hand your approvals to someone while you're away.\n"
            "- Settings → Responsibility matrix (admins): define, per department, who approves and above what amount. This drives the approval route."
        ),
    },
    {
        "id": "bi",
        "title": "BI — turn a file into a dashboard",
        "keywords": ["bi", "business intelligence", "dashboard", "analytics", "upload",
                     "excel", "csv", "chart", "kpi", "insight", "forecast", "report data"],
        "body": (
            "BI (Transformation → BI):\n"
            "1. Upload a data file (Excel/CSV).\n"
            "2. BI profiles it and auto-builds KPIs, charts, insights and a simple forecast.\n"
            "3. Notable findings can raise alerts into the bell, and you can schedule digest summaries.\n"
            "It runs offline on the platform — your data isn't sent to any external service."
        ),
    },
    {
        "id": "reports",
        "title": "Reports & exports centre",
        "keywords": ["report", "reports", "export", "excel", "pdf", "csv", "download", "govern"],
        "body": (
            "Reports (Govern → Reports) is a catalogue of ready reports across procurement, maintenance, "
            "production and platform data. Pick a report, apply filters, preview on screen, then export to "
            "CSV, Excel or PDF."
        ),
    },
    {
        "id": "signature",
        "title": "Set up your digital signature",
        "keywords": ["signature", "sign", "profile", "sign-off", "e-sign", "توقيع", "imza"],
        "body": (
            "Set up your digital signature (top-right menu → Profile):\n"
            "1. Go to the Signature section, type your name, and click Generate.\n"
            "2. Pick one of the signature-font styles and Save.\n"
            "Your signature is then applied when you approve purchase requests and other sign-offs."
        ),
    },
    {
        "id": "notifications",
        "title": "Notifications & search",
        "keywords": ["notification", "notifications", "bell", "alert", "search", "find",
                     "mark read", "sound"],
        "body": (
            "- Notifications: the bell (top-right) aggregates alerts from every system; click one to jump to the "
            "record, mute/enable the sound, or 'Mark all read'.\n"
            "- Search: the top search box and the Ctrl/⌘+K command palette find modules, pages and records (PRs, tickets, assets)."
        ),
    },
    {
        "id": "garamento",
        "title": "Using Garamento (this assistant) & market research",
        "keywords": ["garamento", "chatbot", "assistant", "market research", "price",
                     "polish", "autocorrect", "help"],
        "body": (
            "Garamento (me!):\n"
            "- Chat: click the 🧵 button (bottom-right) on any page to ask about fabrics, fashion, costing, sourcing or how to use the platform.\n"
            "- Market research: on Procurement → New request, the 🔎 button (or 'Price all items') fetches live approximate prices with sources.\n"
            "- ✨ Polish: fixes spelling/grammar in the title and notes fields; common typos are also auto-corrected as you type."
        ),
    },
    {
        "id": "admin",
        "title": "Admin — users, roles & integrations",
        "keywords": ["admin", "role", "roles", "permission", "permissions", "user", "users",
                     "access", "integration", "settings", "governance", "health", "roadmap"],
        "body": (
            "Admin (admins only, sidebar → Admin):\n"
            "- Manage users, roles and per-user permissions (who can see/do what).\n"
            "- Configure integrations (the connected systems' URLs) and platform settings.\n"
            "- Govern group: Health (live system status), Governance, Roadmap. An audit/activity viewer records key actions."
        ),
    },
]

_INDEX = "Platform topics I can guide you through: " + "; ".join(t["title"] for t in TOPICS) + "."

_PREAMBLE = (
    "TC PLATFORM GUIDE (use this to answer 'how do I…' questions about the platform "
    "accurately; give concrete steps and name the exact sidebar path. If the answer "
    "is not covered here, say so and suggest where to look rather than inventing UI):\n"
)


def _tokens(text):
    return set(re.findall(r"[a-z؀-ۿ]{3,}", (text or "").lower()))


def retrieve(query, k=3):
    """Return up to k topics most relevant to the query (by keyword overlap)."""
    q = _tokens(query)
    ql = (query or "").lower()
    scored = []
    for t in TOPICS:
        score = 0
        for kw in t["keywords"]:
            if kw in ql:                       # phrase / alias match
                score += 3
        title_tokens = _tokens(t["title"])
        body_tokens = _tokens(t["body"])
        score += 2 * len(q & title_tokens)
        score += len(q & body_tokens)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda s: s[0], reverse=True)
    return [t for _, t in scored[:k]]


def context_for(query, k=3):
    """Build the knowledge block to inject into the system prompt."""
    hits = retrieve(query, k=k)
    if not hits:
        # No strong match: give the index plus the two most common flows.
        hits = [t for t in TOPICS if t["id"] in ("proc_new", "maint_ticket_new")]
    body = "\n\n".join(f"### {t['title']}\n{t['body']}" for t in hits)
    return _PREAMBLE + _INDEX + "\n\n" + body
