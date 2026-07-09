#!/usr/bin/env python
"""
TC Platform — QA test runner.

Usage:
    python run_tests.py            # all backend tests (skips e2e)
    python run_tests.py backend    # unit + integration
    python run_tests.py security   # RBAC + CSRF + routes access
    python run_tests.py qa         # the new QA suites (intelligence + AI features)
    python run_tests.py e2e        # Playwright UI tests (needs a browser)
    python run_tests.py cov        # backend tests + HTML coverage report
    python run_tests.py all        # backend + e2e
"""
import subprocess
import sys

SUITES = {
    "backend": ["-q", "--ignore=tests/e2e"],
    "security": ["-q", "tests/test_security_rbac.py", "tests/test_csrf_security.py", "tests/test_routes_access.py"],
    "qa": ["-q", "tests/test_qa_intelligence.py", "tests/test_qa_ai_features.py"],
    "e2e": ["-q", "tests/e2e"],
    "cov": ["-q", "--ignore=tests/e2e", "--cov=app", "--cov-report=html", "--cov-report=term-missing"],
    "all": ["-q"],
}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "backend"
    args = SUITES.get(mode)
    if args is None:
        print(__doc__)
        return 2
    print(f"== running suite: {mode} ==")
    return subprocess.call([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args])


if __name__ == "__main__":
    raise SystemExit(main())
