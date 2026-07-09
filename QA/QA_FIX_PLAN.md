# QA_FIX_PLAN

Fixes are applied in this QA pass in severity/priority order. All three found this pass are **done**.

| Order | Bug | Severity/Priority | Action | Status |
|---|---|---|---|---|
| 1 | BUG-001 CSV export contract | High / P1 | Align smoke test to `/reports/<key>.<fmt>`; verify export returns valid CSV | ✅ Done, retest pass |
| 2 | BUG-002 DEBUG default in prod | Medium / P2 | `config.py`: default DEBUG off when `TC_ENV=production` | ✅ Done, retest pass |
| 3 | BUG-003 missing i18n key | Low / P3 | Add `m.approvals` to en/ar/tr | ✅ Done, 0 missing keys |

## Also fixed during broader hardening this session (pre-QA-pass, related)
- PostgreSQL `_PGConn.executemany` missing (AI seed failed on Render) → added; `ai_system_settings` registered id-less → avoids transaction abort.

## Recommended follow-ups (not blocking go-live)
| Item | Priority | Notes |
|---|---|---|
| Add Content-Security-Policy header | P3 | Hardening |
| Composite indexes `mnt_tickets(status,machine_id)`, `pr_steps(pr_id,status)` | P3 | Performance at scale |
| Replace remaining `print()` with logger | P4 | Cleanliness |
| CI gate: fail on missing i18n key | P3 | Prevent raw-key regressions |
| Rate-limit AI/OpenRouter endpoints | P3 | Cost control |
| Rotate exposed OpenRouter key | P1 (operational) | Not code — set new key in Render |
