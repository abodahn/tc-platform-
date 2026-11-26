"""Lane navback: top-bar Back button + file links marked for the installed app.

    python -m app.tests_navback

Temp SQLite DB; the repo's platform.db is never touched. The JavaScript
(app/static/js/navback.js) cannot run here; this covers the server side: where
the button appears, the parent it falls back to, that the fallback chain always
ends at home without a loop, that a page's own override cannot point off-site,
and that every export link carries data-file-download.
"""
import os
import re
import sys
import tempfile
from pathlib import Path


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "navback.db")
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_HEALTH_TIMEOUT"] = "1"
    from app import create_app
    return create_app()


BACK = re.compile(r'<a class="icon-btn navback" id="navBack" href="([^"]*)"')


def run():
    app = _app()
    from flask import g, render_template_string, session
    # One app context for the whole run: drop g.user so a client never inherits
    # the previous client's user. FIRST in line, so the scope / password hooks
    # that run before any view already see this request's user.
    app.before_request_funcs.setdefault(None, []).insert(0, lambda: g.pop("user", None) and None)
    with app.app_context():
        from app.approvals import services as svc
        from app.db import get_db
        from app import navback

        c = get_db()
        admin = dict(c.execute("SELECT * FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone())
        c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                  "VALUES ('nb.desk', 'x', 'Desk Test', 'itsm_user', 1, '2026-01-01')")
        desk_id = c.execute("SELECT id FROM users WHERE username='nb.desk'").fetchone()["id"]
        c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                  "VALUES ('nb.staff', 'x', 'Staff Test', 'normal_user', 1, '2026-01-01')")
        staff_id = c.execute("SELECT id FROM users WHERE username='nb.staff'").fetchone()["id"]
        c.commit()
        c.close()
        pid, _ = svc.create_pr({"title": "Navback test", "department": "IT"},
                               [{"item": "Test toner", "qty": 1, "unit_price": 10}], admin,
                               submit=False, priced=True)

        def client(uid):
            cli = app.test_client()
            with cli.session_transaction() as s:
                s["uid"], s["ep"], s["_csrf_token"] = uid, 0, "tok"
            return cli

        def back_of(cli, path):
            r = cli.get(path)
            html = r.get_data(as_text=True)
            m = BACK.search(html)
            return r.status_code, (m.group(1) if m else None), html

        a = client(admin["id"])

        # -- the button: absent on home, present with the right parent elsewhere
        st, b, html = back_of(a, "/")
        assert st == 200 and b is None, (st, b)
        assert "js/navback.js" in html and "css/navback.css" in html
        for path, parent in (("/my-work", "/"),
                             ("/procurement/", "/"),
                             ("/procurement/list", "/procurement/"),
                             ("/procurement/pr/%d" % pid, "/procurement/list"),   # PARENTS override
                             ("/maintenance/floor", "/maintenance/"),             # prefix rule
                             ("/maintenance/tickets", "/maintenance/"),
                             ("/maintenance/", "/")):
            st, b, html = back_of(a, path)
            assert st == 200, (path, st)
            assert b == parent, (path, b, parent)
            assert 'data-parent="%s"' % parent in html and 'data-i18n="navback.back"' in html, path
        print("ok  back button + parent fallback")

        # -- error pages carry one too (403/404 are not dead ends)
        r = a.get("/procurement/pr/987654")
        if r.status_code == 404:
            assert 'data-navback' in r.get_data(as_text=True)
        r = a.get("/no-such-page-navback")
        assert r.status_code == 404 and 'data-navback data-parent="/"' in r.get_data(as_text=True)
        assert 'data-i18n="navback.go_home"' in r.get_data(as_text=True)
        print("ok  error page back button")

        # -- scope-locked user: their landing page is home, so no button there
        d = client(desk_id)
        r = d.get("/")
        assert r.status_code == 302 and r.headers["Location"].endswith("/module/itsm"), r.headers.get("Location")
        st, b, _ = back_of(d, "/module/itsm")
        assert st == 200 and b is None, (st, b)
        print("ok  scope-locked home")

        # -- a refusal (403) is still refused, and is not a dead end
        r = client(staff_id).get("/procurement/settings")
        assert r.status_code == 403, (r.status_code, r.headers.get("Location"))
        assert "data-navback" in r.get_data(as_text=True)
        print("ok  403 page back button")

        # -- every sidebar page walks up to home, never loops, never leaves the site
        adapter = app.url_map.bind("localhost")
        with app.test_request_context("/"):
            session["uid"], session["ep"] = admin["id"], 0
            g.pop("user", None)
            from app.routes.main import inject_globals
            nav = inject_globals()["nav"]
            user = navback.current_user()
            starts = [navback._item_url(it) for sec in nav for it in sec["items"]]
            starts.append("/procurement/pr/%d" % pid)
            for start in starts:
                seen, path = [], start
                while path is not None:
                    assert path.startswith("/") and not path.startswith("//"), (start, path)
                    assert path not in seen, ("loop", start, seen + [path])
                    seen.append(path)
                    ep, _ = adapter.match(path)
                    path = navback.parent_url(nav, ep, path, user)
                assert seen[-1] == "/", (start, seen)
                assert len(seen) <= 5, (start, seen)
        print("ok  %d sidebar pages reach home without a loop" % len(starts))

        # -- a page may name its own parent, but only a same-origin path
        tpl = '{% extends "base.html" %}{% set back_parent = P %}{% block content %}x{% endblock %}'
        for given, expect in (("/procurement/list", "/procurement/list"),
                              ("//evil.example/x", "/"), ("https://evil.example/", "/"),
                              ("/\\evil.example", "/")):
            with app.test_request_context("/my-work"):
                session["uid"], session["ep"] = admin["id"], 0
                g.pop("user", None)
                m = BACK.search(render_template_string(tpl, P=given))
                assert m and m.group(1) == expect, (given, m and m.group(1))
        print("ok  back_parent override is same-origin only")

        # -- export links are marked for the installed-app download handler
        for path, needle in (("/procurement/list", "listing_xlsx"),
                             ("/procurement/pr/%d" % pid, "pr.xlsx"),
                             ("/maintenance/tickets", "/reports/export/tickets.csv"),
                             ("/warehouse/materials", "/export/materials.csv")):
            r = a.get(path)
            html = r.get_data(as_text=True)
            assert r.status_code == 200, (path, r.status_code)
            tags = [t for t in re.findall(r"<a\b[^>]*>", html) if needle in t or
                    (needle == "listing_xlsx" and "/list.xlsx" in t)]
            assert tags, (path, needle)
            assert all("data-file-download" in t for t in tags), (path, tags)
        print("ok  export links carry data-file-download")

        # -- static scan: every template link to a file-export endpoint is marked
        root = Path(__file__).resolve().parent / "templates"
        ep = re.compile(r"url_for\('(?:[a-z_]+\.export_csv|[a-z_]+\.(?:\w+_xlsx|\w+_pdf)|"
                        r"approvals\.analytics_data|main\.report_export|reports_hub\.export|"
                        r"smartfactory\.report_export|intelligence\.report_alerts|maintenance\.report_export|"
                        r"maintenance\.import_template|probation\.reports_csv|backup\.download|"
                        r"admin\.registrations_export|warehouse\.material_import_template)'")
        missing = []
        for f in root.rglob("*.html"):
            s = f.read_text(encoding="utf-8")
            for m in re.finditer(r"<a\b[^>]*>", s):
                if ep.search(m.group(0)) and "data-file-download" not in m.group(0) \
                        and "data-file-view" not in m.group(0):
                    missing.append("%s: %s" % (f.relative_to(root), m.group(0)[:90]))
        assert not missing, "\n".join(missing)
        print("ok  no unmarked export link in templates")

        # -- i18n keys exist in all three languages
        import json
        i18n = Path(__file__).resolve().parent / "static" / "i18n"
        keys = ("navback.back", "navback.go_home", "navback.preparing", "navback.file_ready",
                "navback.open_file", "navback.failed")
        for lang in ("en", "ar", "tr"):
            d = json.loads((i18n / ("%s.json" % lang)).read_text(encoding="utf-8"))
            for k in keys:
                assert d.get(k), (lang, k)
        print("ok  i18n EN/AR/TR")

    print("ALL NAVBACK CHECKS PASSED")


if __name__ == "__main__":
    run()
