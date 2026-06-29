import re

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from _helpers import ui_login  # noqa: E402

pytestmark = pytest.mark.e2e


def test_mobile_sidebar_drawer_toggles(page, live_server):
    page.set_viewport_size({"width": 390, "height": 800})   # phone width
    ui_login(page, live_server)
    toggle = page.locator("#sidebarToggle")
    expect(toggle).to_be_visible()                          # hamburger shows on mobile
    toggle.click()                                          # open the off-canvas drawer
    expect(page.locator(".app")).to_have_class(re.compile(r"mobile-open"))
    expect(page.locator(".sidebar")).to_contain_text("Command Center")
