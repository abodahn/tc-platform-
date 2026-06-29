import json

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from _helpers import ui_login  # noqa: E402

pytestmark = pytest.mark.e2e


def _inject_feed(page, item):
    """Re-route the feed (added after the autouse empty route, so it wins)."""
    page.route("**/notifications/feed", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"unread": 9, "max_id": item["id"], "items": [item]})))


CRITICAL = {"id": 999999, "severity": "critical", "module": "monitoring",
            "title": "E2E Server offline", "message": "DB-SRV-02 heartbeat lost",
            "created_at": "now", "is_read": 0}


def test_new_notification_pops_up_and_updates_bell(page, live_server):
    ui_login(page, live_server)
    _inject_feed(page, CRITICAL)
    page.click("#notifBtn")                       # opening the bell triggers a poll()
    # the slide-in popup appears
    popup = page.locator(".npop")
    expect(popup).to_be_visible(timeout=5000)
    expect(page.locator(".npop .npop-title")).to_contain_text("E2E Server offline")
    # and the item is prepended into the bell list
    expect(page.locator("#notifPanel .notif[data-nid='999999']")).to_have_count(1)


def test_sound_toggle_flips(page, live_server):
    ui_login(page, live_server)
    page.click("#notifBtn")
    sound = page.locator("#notifSound")
    before = sound.inner_text()
    sound.click()
    expect(sound).not_to_have_text(before)        # 🔔 <-> 🔕


def test_clicking_notification_opens_its_module(page, live_server):
    ui_login(page, live_server)
    _inject_feed(page, CRITICAL)
    page.click("#notifBtn")
    item = page.locator("#notifPanel .notif[data-nid='999999']")
    expect(item).to_have_count(1)
    item.click()
    page.wait_for_url("**/module/monitoring**")
