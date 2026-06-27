"""
TC Platform — Playwright screenshot capture.

Logs in and captures the key screens in light + dark + Arabic RTL + mobile.
Requires the platform to be running (default http://127.0.0.1:7000) and:

    pip install playwright
    python -m playwright install chromium
    python tests/capture_screenshots.py

Screenshots are written to ../screenshots/.
"""
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.getenv("TC_PLATFORM_URL", "http://127.0.0.1:7000")
USER = os.getenv("TC_ADMIN_USER", "admin")
PWD = os.getenv("TC_ADMIN_PASSWORD", "Admin@12345")
OUT = Path(__file__).resolve().parent.parent / "screenshots"
OUT.mkdir(exist_ok=True)


def shot(page, name):
    page.wait_for_timeout(700)  # let counters/i18n settle
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
    print(f"  saved {name}.png")


def login(page):
    page.goto(f"{BASE}/login", wait_until="networkidle")
    page.fill("input[name=username]", USER)
    page.fill("input[name=password]", PWD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/", wait_until="networkidle")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # --- Desktop (1440x900) ---
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        page.goto(f"{BASE}/login", wait_until="networkidle")
        shot(page, "01_login")

        login(page)
        shot(page, "02_dashboard_light")

        # dark mode
        page.click('[data-theme-btn="dark"]')
        page.wait_for_timeout(500)
        shot(page, "03_dashboard_dark")
        page.click('[data-theme-btn="light"]')

        # Arabic RTL
        page.click('[data-lang-btn="ar"]')
        page.wait_for_timeout(700)
        shot(page, "04_dashboard_arabic_rtl")
        page.click('[data-lang-btn="en"]')
        page.wait_for_timeout(400)

        page.goto(f"{BASE}/launcher", wait_until="networkidle")
        shot(page, "05_launcher")

        page.goto(f"{BASE}/admin/", wait_until="networkidle")
        shot(page, "06_admin")

        page.goto(f"{BASE}/health", wait_until="networkidle")
        shot(page, "07_health")

        page.goto(f"{BASE}/module/ai_hub", wait_until="networkidle")
        shot(page, "08_module_ai_hub")
        ctx.close()

        # --- Mobile (390x844) ---
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        login(page)
        shot(page, "09_mobile_dashboard")
        ctx.close()

        browser.close()
    print(f"\nAll screenshots saved to {OUT}")


if __name__ == "__main__":
    main()
