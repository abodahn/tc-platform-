# QA_UI_UX_CHARACTER_REVIEW

**Method:** platform-wide scan of 731 `data-i18n` keys across all templates vs the EN/AR/TR dictionaries; review of RTL handling, theming, empty/loading/error states, and the design-lint hook output accumulated during the build.

## Result: PASS. Full trilingual parity; one missing key fixed.

| Check | Result |
|---|---|
| i18n coverage (static keys) | 731 keys used across templates; **0 missing** after fixing `m.approvals`. |
| EN / AR / TR parity | **0 keys missing** in Arabic or Turkish (full parity). |
| Untranslated leftovers | 1 AR value identical to EN (a proper noun) — negligible. |
| RTL (Arabic) | `dir="rtl"` applied on `<html>` for Arabic; verified live on the AI Intelligence Center (tabs, hero, cards flip). Layout uses logical properties (`inset-inline-*`, `margin-inline-*`). |
| LTR (English/Turkish) | Correct. |
| Theming | Light/Dark/Auto tokens (`tokens.css`); theme switch persisted per user (`/prefs`). |
| Design-lint (impeccable hook) | Ran on every UI edit this session. One flagged "side-stripe" on AI alert cards was refactored to a full border + severity dot. Pre-existing brand em-dashes left intentional. |
| Empty states | Present (BI, Reports, Alert Center "No alerts match", Command Center "No run yet", tables "No … yet"). |
| Loading states | Present (Garamento typing, market-research "scouring…", AI assistant "Thinking…", tunnel/mascot loaders). |
| Error states | Flash → toast pipeline; graceful offline messages for AI features. |
| Exports | Buttons present on Reports, Alert Center, BI; verified 200 + correct content-type. |
| Mobile / responsive | Grids use `auto-fit minmax`; Garamento panel full-screen ≤520px; AI hero stacks ≤720px. e2e mobile test present (`test_e2e_mobile.py`). |

## Notes / dynamic content
- AI-*generated* text (Intelligence alert titles, "why", recommendations) is produced by the engine in English — it is data, not a UI label, so it is not covered by `data-i18n`. Documented as a known limitation; translating it would require translating predictor output.
- Number/date formatting uses locale-aware `toLocaleString` in client renderers; currency shown with explicit codes (EGP/USD/…).

## Recommendations (non-blocking)
1. If fully-localized AI narratives are required, translate predictor output (LLM or per-language templates).
2. Add a CI check that fails the build if any `data-i18n` key is missing from EN (prevents raw-key regressions).
