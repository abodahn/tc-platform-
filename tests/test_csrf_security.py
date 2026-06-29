"""CSRF protection behaviour: browser navigations bounce to login, XHR get 400,
valid tokens pass through."""
from conftest import get_csrf


def test_missing_token_browser_nav_redirects_to_login(client):
    # a normal form POST (Accept: text/html) with no token -> friendly redirect
    r = client.post("/login", data={"username": "admin", "password": "x"},
                    headers={"Accept": "text/html,application/xhtml+xml"})
    assert r.status_code == 302
    assert "/login" in r.headers.get("Location", "")


def test_missing_token_xhr_gets_400(client):
    # programmatic caller (Accept: */*) -> hard 400 to handle in code
    r = client.post("/login", data={"username": "admin", "password": "x"},
                    headers={"Accept": "*/*"})
    assert r.status_code == 400


def test_valid_token_passes_csrf(client):
    tok = get_csrf(client)
    # valid token but wrong password -> CSRF passes, login logic runs, re-renders (200)
    r = client.post("/login", data={"username": "admin", "password": "definitely-wrong", "_csrf": tok},
                    headers={"Accept": "text/html"})
    assert r.status_code == 200  # not 400 (csrf ok) and not redirected away by csrf guard
