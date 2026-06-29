import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from _helpers import ui_login, PNG_1x1  # noqa: E402

pytestmark = pytest.mark.e2e


def test_picture_report_flow_to_send(page, live_server):
    ui_login(page, live_server)
    page.goto(live_server + "/maintenance/easy")

    # step 0 — pick a machine
    expect(page.locator(".er-step[data-step='0']")).to_be_visible()
    machines = page.locator("#erMachines .er-card")
    if machines.count() == 0:
        pytest.skip("no machines seeded")
    machines.first.click()

    # step 1 — pick a category (first category 'Sewing' has sub-categories)
    expect(page.locator(".er-step[data-step='1']")).to_be_visible()
    page.locator("#erCats .er-card").first.click()

    # step 2 — sub-category (only when the category has subs)
    if page.locator(".er-step[data-step='2']").is_visible():
        page.locator("#erSubs .er-card").first.click()

    # step 3 — stopped / running
    expect(page.locator(".er-step[data-step='3']")).to_be_visible()
    page.locator("[data-stopped='1']").click()

    # step 4 — SEND is disabled until a photo is attached
    expect(page.locator(".er-step[data-step='4']")).to_be_visible()
    submit = page.locator("#erSubmit")
    expect(submit).to_be_disabled()
    page.set_input_files("#erPhoto", {"name": "proof.png", "mimeType": "image/png", "buffer": PNG_1x1})
    expect(submit).to_be_enabled()


def test_tap_to_hear_requests_audio_clip(page, live_server):
    ui_login(page, live_server)
    page.goto(live_server + "/maintenance/easy")
    # tapping a speaker plays the pre-generated neural clip for the chosen language
    with page.expect_request("**/static/audio/**") as req:
        page.locator("[data-speak]").first.click()
    assert "/static/audio/" in req.value.url
    assert req.value.url.endswith(".mp3")
