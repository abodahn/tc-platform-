import re

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from _helpers import ui_login  # noqa: E402

pytestmark = pytest.mark.e2e


def test_login_lands_on_dashboard(page, live_server):
    ui_login(page, live_server)
    expect(page).to_have_url(re.compile(r"/$"))
    expect(page.locator("body")).to_contain_text("Command Center")


def test_bad_password_stays_on_login(page, live_server):
    page.goto(live_server + "/login")
    page.fill("input[name='username']", "admin")
    page.fill("input[name='password']", "definitely-wrong")
    page.click("button[type='submit']")
    expect(page).to_have_url(re.compile(r"/login"))
    # still shows the login form (not the dashboard)
    expect(page.locator("input[name='password']")).to_be_visible()


def test_logout_via_user_menu(page, live_server):
    ui_login(page, live_server)
    page.click("#userBtn")
    page.click("a[href*='logout']")
    expect(page).to_have_url(re.compile(r"/login"))
