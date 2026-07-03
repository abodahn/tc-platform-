# Product

## Register

product

## Users

Internal staff at T&C Garments — IT/operations leads, department managers, and floor
supervisors. They open the platform on desktop at their desks (and occasionally on a
phone/tablet on the factory floor) to check the state of the business and jump into one
of four underlying systems (Service Desk / ITSM, Asset & Inventory, Monitoring,
CommandTrack) without logging in again. Trilingual audience: English, Arabic (RTL),
Turkish. The job to be done is *orient fast, then act* — see what needs attention across
all systems on one screen, then deep-link into the right tool.

## Product Purpose

A single trilingual "command center" that unifies four on-prem LAN systems behind one
login (SSO), one notifications bell, one people/asset registry, and an offline BI engine.
Success looks like: a manager trusts the numbers on the dashboard at a glance, never has
to re-authenticate, and reaches any task in ≤2 clicks. The interface should disappear into
the work.

## Brand Personality

Confident, calm, industrial-precise. Three words: **dependable, sharp, unfussy.** It is an
operations tool, not a marketing surface — it should feel like instrumentation a control
room would trust. Brand identity is carried by one saturated color (T&C red `#ED1C24`) on a
deep navy shell (`#07080B`); everything else is quiet neutrals.

## Anti-references

- Not a consumer SaaS marketing dashboard: no gradient-text, no hero-metric template, no
  glow-soup, no decorative motion.
- Not a rainbow admin theme: color is reserved for state and the current selection, never
  decoration. Inactive items stay neutral.
- Not over-animated: users are in flow; choreography that makes them wait is wrong.

## Design Principles

1. **Instrumentation, not decoration** — every accent means something (state, selection,
   severity). If it doesn't carry information, it's noise.
2. **Orient before act** — wayfinding is the first job of navigation; the current location
   must be unmistakable without shouting.
3. **Earned familiarity** — standard product patterns (side nav, top bar, command search)
   done cleanly beat invented affordances.
4. **Trilingual-first** — every layout is RTL-correct and copes with EN/AR/TR string
   lengths; nothing is pixel-hardcoded to English.
5. **One saturated color, spent carefully** — T&C red is a scarce resource; neutrals do the
   structural work.

## Accessibility & Inclusion

WCAG 2.1 AA target. Body/label text meets ≥4.5:1 (≥3:1 for large). Full keyboard
operability and visible focus. `prefers-reduced-motion` honored on every transition. RTL
parity for Arabic. Light and dark themes both maintained.
