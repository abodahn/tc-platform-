import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from _helpers import ui_login  # noqa: E402

pytestmark = pytest.mark.e2e


def test_arabic_switches_to_rtl(page, live_server):
    ui_login(page, live_server)
    page.click("button[data-lang-btn='ar']")
    expect(page.locator("html")).to_have_attribute("lang", "ar")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")


def test_turkish_then_back_to_english(page, live_server):
    ui_login(page, live_server)
    page.click("button[data-lang-btn='tr']")
    expect(page.locator("html")).to_have_attribute("lang", "tr")
    expect(page.locator("html")).to_have_attribute("dir", "ltr")
    page.click("button[data-lang-btn='en']")
    expect(page.locator("html")).to_have_attribute("lang", "en")
    expect(page.locator("html")).to_have_attribute("dir", "ltr")


def test_dark_theme_applies(page, live_server):
    ui_login(page, live_server)
    page.click("button[data-theme-btn='dark']")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.click("button[data-theme-btn='light']")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
