# QA_BUG_REPORT

All defects found in this QA pass. **3 found, 3 fixed.** No open critical/high defects.

---

### BUG-001 — CSV export route contract broke smoke test
- **Module:** Reports Center (main)
- **Route:** `GET /reports/export/systems.csv` (old) → `GET /reports/<key>.<fmt>` (new)
- **Description:** The Reports Center refactor replaced the generic `/reports/export/<table>.csv` route with the registry-driven `/reports/<key>.<fmt>`. The `test_smoke.py::test_csv_export` smoke test still called the old URL → 404.
- **Steps to reproduce:** `pytest tests/test_smoke.py::test_csv_export` → `assert 404 == 200`.
- **Expected:** CSV export returns 200 `text/csv`.
- **Actual:** 404 (old URL removed).
- **Severity:** High (blocked a smoke test; export contract change) · **Priority:** P1
- **Root cause:** Test not updated when the feature was relocated. No user-facing link referenced the old URL (verified across templates).
- **Fix:** Updated the test to the current contract `GET /reports/systems.csv` and header assertion `Key,Name`. Verified export returns valid CSV.
- **Status:** Fixed · **Retest:** Pass.

### BUG-002 — DEBUG enabled by default in production
- **Module:** config
- **File:** `config.py`
- **Description:** `DEBUG = _bool(os.getenv("TC_DEBUG"), True)` defaulted to `True` regardless of environment, so a production deploy without `TC_DEBUG=false` would run with debug semantics (verbose errors).
- **Severity:** Medium (hardening) · **Priority:** P2
- **Root cause:** Default value not environment-aware.
- **Fix:** `DEBUG = _bool(os.getenv("TC_DEBUG"), ENV.lower() != "production")` — OFF in production unless explicitly enabled.
- **Status:** Fixed · **Retest:** `Config.DEBUG` is False when `TC_ENV=production`.

### BUG-003 — Missing i18n key renders raw on maintenance ticket detail
- **Module:** Maintenance
- **Page:** `maintenance/ticket_detail.html` (Approvals section header)
- **Description:** `data-i18n="m.approvals"` had no dictionary entry, so the label rendered as the raw key `m.approvals` in all languages.
- **Severity:** Low (UI text) · **Priority:** P3
- **Root cause:** Key omitted from i18n dictionaries.
- **Fix:** Added `m.approvals` → EN "Approvals", AR "الاعتمادات", TR "Onaylar".
- **Status:** Fixed · **Retest:** Key present in all three dictionaries; platform-wide scan now shows 0 missing static keys.

---

## Not defects (verified during audit)
- `bi.jobs_run` / `service_worker` without auth decorator → both correct by design (token-gated / public PWA asset).
- 4 dynamic-SQL sites → whitelisted columns, parameterized values, not injectable.
- EN/AR/TR dictionary parity → 0 missing translations.
