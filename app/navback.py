"""
TC Platform — where the top-bar Back button goes when there is no in-app
history to step back through (a page opened from a notification, a bookmark,
a fresh tab, or the installed app after a restart).

The browser history is tried first, in the browser (app/static/js/navback.js).
This module only computes the PARENT fallback, server-side, so it is always a
same-origin path built with url_for and never something taken from the request.

Rules, first match wins:
  1. the page set `back_parent` itself ({% set back_parent = url_for(...) %})
  2. an explicit override in PARENTS (detail page -> its list)
  3. the longest sidebar entry whose path is a prefix of this path
     (/maintenance/tickets/7 -> /maintenance/tickets)
  4. the module's home (its shortest visible sidebar URL); the module home
     itself goes to 5.
  5. the user's home
Rule 3 always moves to a strictly shorter path and rule 4 to the module home,
which never points back down, so two pages can never send each other back and
forth (tests_navback walks every sidebar page up to home to prove it).
"""
from flask import request, url_for

from app.auth import current_user
from app.security import system_scope

# Detail pages whose natural parent is a list rather than the module home.
PARENTS = {
    "approvals.detail": "approvals.listing",
}


def home_url(user):
    """Where the user lands — mirrors main.dashboard's scope redirect."""
    scope = system_scope(user) if user else None
    if scope:
        if len(scope) == 1:
            return url_for("main.module", key=next(iter(scope)))
        return url_for("main.launcher")
    return url_for("main.dashboard")


def _item_url(it):
    if it[3] == "main.module":
        return url_for("main.module", key=it[0])
    return url_for(it[3])


def _bp(endpoint):
    return endpoint.rsplit(".", 1)[0] if endpoint and "." in endpoint else None


def parent_url(nav, endpoint, path, user):
    """The fallback target for the Back button, or None on the home page."""
    home = home_url(user)
    if path == home or endpoint == "main.dashboard":
        return None
    items = [it for sec in (nav or []) for it in sec["items"]]
    urls = [(it, _item_url(it)) for it in items]

    # An override counts only when the user can see that page in the sidebar.
    want = PARENTS.get(endpoint)
    for it, u in urls:
        if want and it[3] == want and u != path:
            return u

    best = None
    for _, u in urls:
        stem = u.rstrip("/")
        if stem and u != path and path.startswith(stem + "/") and (best is None or len(stem) > len(best.rstrip("/"))):
            best = u
    if best:
        return best

    # The module's home is its shortest visible sidebar URL (/maintenance/,
    # /procurement/); the module home itself goes to the user's home.
    bp = _bp(endpoint)
    mine = [u for it, u in urls if _bp(it[3]) == bp]
    if mine:
        mod_home = min(mine, key=len)
        return home if mod_home == path else mod_home
    return home


def navback_target(nav, override=None):
    """Jinja global used by _navback.html. `override` is the page's own
    back_parent; it is only honoured when it is a same-origin relative path."""
    user = current_user()
    path = request.path
    if (isinstance(override, str) and override.startswith("/")
            and not override.startswith("//") and "\\" not in override
            and path != home_url(user)):
        return override
    try:
        return parent_url(nav, request.endpoint, path, user)
    except Exception:   # a nav entry that cannot build a URL must never break a page
        return home_url(user) if path != home_url(user) else None


def register(app):
    app.add_template_global(navback_target)
