"""Shared helpers for the Playwright E2E tests."""
import base64

# a minimal valid 1x1 transparent PNG (for the Easy Report photo upload)
PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def ui_login(page, base, user="admin", pwd="Admin@12345"):
    """Log in through the real login form and land on the dashboard."""
    page.goto(base + "/login")
    page.fill("input[name='username']", user)
    page.fill("input[name='password']", pwd)
    page.click("button[type='submit']")
    page.wait_for_load_state("networkidle")
