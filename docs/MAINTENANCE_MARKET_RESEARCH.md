# Maintenance Module — Market Research & Recommendation
*Prepared for T&C Garments · June 2026*

This is a market scan of the leading CMMS/EAM products, a gap analysis of the
TC Platform maintenance module against them, and a recommendation on whether to
keep it inside TC Platform or split it into a separate app (like ITSM).

---

## 1. The market leaders (2026)

| Product | Positioning | Strengths | Notes |
|---|---|---|---|
| **MaintainX** | Mobile-first work orders | Best technician mobile app, real-time messaging, free tier; Premium adds parts inventory + POs | Best for SMB/mid-market, simpler |
| **UpKeep** | Mobile-first + EHS + IoT | Ranked #1 for 2026; native EHS module, IoT hardware, quick ROI | Higher cost |
| **Limble** | User-centric, configurable | Modern UI, deep customization of fields/workflows/reports, strong PM & reporting | Great UX, flexible |
| **Fiix** (Rockwell) | Factory OT integration | Deep tie-in to Allen-Bradley PLCs / FactoryTalk; good for automated plants | Best if you run Rockwell OT |
| **IBM Maximo** | Enterprise EAM | The standard for tens of thousands of assets in regulated environments | Heavy, expensive |
| eMaint, Hippo, Fracttal, MicroMain | Mid-market | Solid CMMS feature sets | — |

Mid-market pricing is **$16–55 / user / month**; parts inventory + PO management
usually sits in the **premium** tiers.

## 2. What "best-in-class" CMMS means today (the checklist)

From the 2026 requirement guides, a top CMMS must have:

1. **Work-order management** — request → assign → execute → close, with photos/notes/time.
2. **Preventive maintenance** — time/usage/condition triggers, checklists, auto-generated WOs.
3. **Asset management** — hierarchy, history, QR, criticality, cost & downtime.
4. **Spare-parts/MRO inventory** — real-time stock, multi-location, reorder points, POs.
5. **Mobile-first + offline** — technicians do everything on a phone; **QR scan → full asset history**; sync when back online.
6. **Real-time notifications** — assignments, escalations, SLA.
7. **Analytics** — MTTR, MTBF, downtime, cost, PM compliance; dashboards.
8. **Integrations** — ERP/HR/finance; IoT/OT for condition data.
9. **AI / predictive** — the 2026 frontier (see below).

## 3. Where TC Platform's module already stands

✅ **Have, at parity:** work orders + full ticket lifecycle (16 states, guarded),
PM plans + checklists, asset/machine registry with QR + criticality + downtime +
cost, **spare-parts inventory with an atomic issue transaction** (stock can't go
negative — many cheap CMMS get this wrong), configurable **approval matrix**,
stock movements & vouchers, role-based notifications, audit trail, MTTR/downtime
KPIs, CSV reports, **trilingual EN/AR/TR + RTL** (most competitors are English-first).

🟡 **Partial / next:** mobile-optimized technician views & **offline** (responsive
now, not yet a PWA); **camera photo upload** (metadata model + hooks exist, UI
pending); Excel import & PDF reports (CSV live); multi-warehouse; PO module;
month-grid calendar.

🔴 **Not yet (the 2026 differentiators):** IoT/condition data, **AI predictive /
prescriptive maintenance**, machine **health score**, downtime heatmaps.

## 4. The 2026 trend you should aim at

- Predictive-maintenance market: **$17.1B (2026) → $97.4B (2034)**; AI-PdM growing **~39% CAGR**.
- **65% of maintenance teams plan to adopt AI by end of 2026.**
- The frontier is **prescriptive** maintenance (AI recommends the fix), per-asset
  **health scores** from sensor + history data, and predicting failures
  **30–90 days out at 80–97% accuracy**, cutting unplanned downtime **30–50%**.
- This is exactly where TC Platform's **AI Transformation Hub** can plug into the
  maintenance data we already capture (downtime, breakdowns, consumption).

## 5. Recommendation: keep it **integrated** (don't split it off) — with a mobile PWA

**Recommendation: keep Maintenance as a module inside TC Platform.** Do **not**
spin it into a separate app like ITSM. Reasons:

- **One login, one identity, one RBAC.** The whole value of TC Platform is unification.
  A storekeeper, technician and factory manager already exist as platform roles —
  splitting fragments that.
- **Shared everything** — theme, trilingual/RTL, audit, notifications bell,
  reports center, and the executive dashboard. A separate app re-implements all of it.
- **Cross-module value** lives in the platform: maintenance cost → Finance module,
  downtime → Production Visibility, machine issues → Assets, AI predictions → AI Hub.
  That synergy only exists if it's one platform.
- **It's already cleanly isolated** — a self-contained `app/maintenance/` package +
  blueprint + own tables (`mnt_*`). So it can be **extracted later** with low effort
  if scale ever demands a dedicated service. We keep the option without paying for it now.

**The one thing worth a "separate" treatment is the factory-floor experience** —
but that's a **mobile/PWA view of the same module**, not a separate app:
big-button technician screens, camera capture, QR scan, and offline cache. That
gives you MaintainX-style mobility *without* fragmenting the platform.

### Suggested path to "best app on the market"
1. **Mobile PWA + offline** technician/supervisor views (closes the #1 competitor gap).
2. **Camera photo proof** upload (model already there) + printable QR labels.
3. **Excel import / PDF reports** (CSV is live).
4. **Machine health score** from the data we already store (downtime, breakdowns,
   PM compliance, repeat failures) — a simple model first, then sensor/IoT.
5. **AI predictive/prescriptive** via the AI Hub — the 2026 differentiator.
6. Multi-warehouse + purchase-order module for full MRO.

Delivering 1–4 would put this module **at or above mid-market CMMS parity** while
keeping the unified-platform advantage none of the standalone tools have.

---

### Sources
- [UpKeep — 10 Best CMMS 2026](https://upkeep.com/blog/best-cmms-software/) ·
  [Limble — CMMS comparison 2026](https://limble.com/learn/cmms/software-comparison/) ·
  [Limble — 16 best CMMS](https://limble.com/learn/best-cmms-software)
- [Limble — CMMS system requirements](https://limble.com/learn/system-requirements) ·
  [LLumin — must-have features 2025](https://llumin.com/blog/10-must-have-features-in-a-cmms-system-for-2025/) ·
  [Coast — features checklist](https://coastapp.com/blog/cmms-features/)
- [iFactory — AI & IoT CMMS 2026](https://ifactoryapp.com/preventive-maintenance/future-preventive-maintenance-ai-iot-cmms-2026) ·
  [MaintainX — maintenance stats 2026](https://www.getmaintainx.com/blog/maintenance-stats-trends-and-insights) ·
  [MarketsandMarkets — AI PdM market](https://www.marketsandmarkets.com/Market-Reports/ai-driven-predictive-maintenance-market-56600288.html) ·
  [Oxmaint — future maintenance trends 2026](https://oxmaint.com/article/future-maintenance-trends-2026-ai-cmms)
