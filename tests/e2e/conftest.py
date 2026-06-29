"""
E2E harness: runs the real Flask app in a background thread and points
Playwright at it. Browser tests are marked `e2e` and excluded from the default
`pytest` run (see pytest.ini); run them with `pytest tests/e2e -m e2e`.
"""
import json
import os
import socket
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def live_server():
    os.environ["TC_ADMIN_PASSWORD"] = "Admin@12345"   # known admin password for the UI login
    os.environ.setdefault("TC_ENV", "development")
    os.environ.pop("DATABASE_URL", None)

    from werkzeug.serving import make_server
    from app import create_app

    app = create_app()
    app.config["TESTING"] = True

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = make_server("127.0.0.1", port, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


@pytest.fixture()
def browser_context_args(browser_context_args):
    # Block the service worker (sw.js) so page.route reliably intercepts the
    # notification feed fetches (a SW would otherwise handle them out of band).
    return {**browser_context_args, "service_workers": "block"}


@pytest.fixture(autouse=True)
def quiet_feed(page):
    """Default the notification feed to empty so tests don't depend on the four
    LAN systems being up. Individual tests can re-route it to inject alerts."""
    page.route("**/notifications/feed", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"unread": 0, "max_id": 0, "items": []})))
    yield
