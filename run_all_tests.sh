#!/usr/bin/env bash
# TC Platform - run the full backend QA suite (Linux/macOS)
set -u
cd "$(dirname "$0")"
echo "============================================================"
echo " TC Platform - QA automated tests"
echo "============================================================"
python -m pytest -q -p no:cacheprovider --ignore=tests/e2e
rc=$?
echo
if [ "$rc" -eq 0 ]; then echo "RESULT: ALL BACKEND TESTS PASSED"; else echo "RESULT: FAILURES - see output above"; fi
echo "(E2E UI tests: run 'python -m pytest tests/e2e' with Chrome + Playwright installed.)"
exit $rc
