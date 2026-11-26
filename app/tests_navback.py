"""Lane navback: top-bar Back button + file links marked for the installed app.

    python -m app.tests_navback

Temp SQLite DB; the repo's platform.db is never touched. The JavaScript
(app/static/js/navback.js) runs under Node with stubbed browser objects when
`node` is on PATH (skipped otherwise). The rest covers the server side: where
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


# Runs navback.js with stub window/document/history/sessionStorage objects and
# prints one JSON line per run; the Python side asserts on the results.
_JS_HARNESS = r"""
const src = require('fs').readFileSync(process.argv[1], 'utf8');
function env(o) {
  const L = {}, ss = new Map(Object.entries(o.ss || {})), calls = [];
  const on = (k) => (t, f) => ((L[k + t] = L[k + t] || []).push(f));
  const [pathname, q] = o.path.split('?');
  const loc = { origin: 'https://x.test', pathname, search: q ? '?' + q : '',
                replace: (u) => calls.push(['replace', u]) };
  loc.href = 'https://x.test' + o.path;
  const hist = { length: o.len || 3, state: null, go: (n) => calls.push(['go', n]),
                 back: () => calls.push(['go', -1]), replaceState(s) { hist.state = s; } };
  const win = { addEventListener: on('w'), navigator: { standalone: o.standalone } };
  const doc = { referrer: o.ref ? 'https://x.test' + o.ref : '', addEventListener: on('d') };
  const sto = { getItem: (k) => (ss.has(k) ? ss.get(k) : null), setItem: (k, v) => ss.set(k, String(v)),
                removeItem: (k) => ss.delete(k) };
  if (o.stack) ss.set('tc_navstack', JSON.stringify(o.stack));
  new Function('window', 'document', 'location', 'history', 'sessionStorage', src)(win, doc, loc, hist, sto);
  const fire = (k, t, ev) => (L[k + t] || []).forEach((f) => f(ev));
  const ev = (a) => ({ target: { closest: (sel) => (sel.indexOf('data-navback') >= 0 ? (a.nav ? a : null) : (a.nav ? null : a)) },
                       button: 0, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } });
  const backBtn = { nav: 1, getAttribute: (n) => (n === 'data-parent' || n === 'href' ? o.parent : null) };
  return { L, ss, calls, hist, loc, fire, ev, back() { fire('d', 'click', ev(backBtn)); return calls.slice(-1)[0]; } };
}
const out = {};
// 1. list -> detail by a link: Back steps back one entry
let e = env({ path: '/procurement/pr/9', ref: '/procurement/', stack: ['/procurement/'], parent: '/procurement/' });
e.fire('w', 'pageshow', {}); out.plain = e.back();
// 2. new form -> POST -> redirect to the detail: Back goes UP, never to the filled form
e = env({ path: '/procurement/new', ref: '/procurement/', stack: ['/procurement/'], parent: '/procurement/' });
e.fire('w', 'pageshow', {});
const form = (m) => ({ getAttribute: (n) => (n === 'method' ? m : null) });
e.fire('w', 'submit', { target: form('get'), defaultPrevented: false }); out.get_marks = e.ss.has('tc_nb_submit');
e.fire('w', 'submit', { target: form('POST'), defaultPrevented: true }); out.cancelled_marks = e.ss.has('tc_nb_submit');
e.fire('w', 'submit', { target: form('POST'), defaultPrevented: false }); out.post_marks = e.ss.has('tc_nb_submit');
const after = env({ path: '/procurement/pr/9', ref: '/procurement/new', stack: ['/procurement/', '/procurement/new'],
                    ss: { tc_nb_submit: '1' }, parent: '/procurement/' });
after.fire('w', 'pageshow', {}); out.after_submit = after.back(); out.marker_left = after.ss.has('tc_nb_submit');
// 3. two #anchor taps on the page: Back skips them and lands on the page before
e = env({ path: '/procurement/guide', ref: '/procurement/', stack: ['/procurement/'], parent: '/procurement/', len: 4 });
e.fire('w', 'pageshow', {}); out.stamp0 = e.hist.state.nbHash;
e.hist.state = null; e.fire('w', 'hashchange', {});
e.hist.state = null; e.fire('w', 'hashchange', {});
out.two_anchors = e.back();
e.hist.state = { nbHash: 1 }; e.fire('w', 'hashchange', {});   // native back over one anchor
e.hist.state = null; e.fire('w', 'hashchange', {});              // then a new anchor from there
out.renumbered = e.hist.state.nbHash; out.after_native = e.back();
// 4. a tab with no history at all: UP
e = env({ path: '/procurement/pr/9', ref: '/procurement/', stack: ['/procurement/'], parent: '/procurement/', len: 1 });
e.fire('w', 'pageshow', {}); out.no_history = e.back();
// 5. file handling is iPhone/iPad home-screen only (Android/desktop: navigator.standalone absent)
out.listeners_other = (env({ path: '/a', parent: '/' }).L.dclick || []).length;
e = env({ path: '/module/itsm', parent: '/', standalone: true });
out.listeners_ios = (e.L.dclick || []).length;
// 6. target=_blank: an /sso/ launch keeps its browser sheet, an app page opens in place
const blank = (href) => ({ href, target: '_blank', hasAttribute: () => false, getAttribute: () => null });
let c = e.ev(blank('https://x.test/sso/launch/itsm')); e.fire('d', 'click', c); out.sso_prevented = c.defaultPrevented;
c = e.ev(blank('https://x.test/procurement/pr/3')); e.fire('d', 'click', c);
out.page_prevented = c.defaultPrevented; out.page_href = e.loc.href;
console.log(JSON.stringify(out));
"""


def js_checks():
    import json
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        print("skip navback.js behaviour (node not on PATH)")
        return
    js = Path(__file__).resolve().parent / "static" / "js" / "navback.js"
    r = subprocess.run([node, "-e", _JS_HARNESS, str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.strip().splitlines()[-1])
    assert o["plain"] == ["go", -1], o
    assert (o["get_marks"], o["cancelled_marks"], o["post_marks"]) == (False, False, True), o
    assert o["after_submit"] == ["replace", "/procurement/"] and not o["marker_left"], o
    assert o["stamp0"] == 0 and o["two_anchors"] == ["go", -3], o
    assert o["renumbered"] == 2 and o["after_native"] == ["go", -3], o
    assert o["no_history"] == ["replace", "/procurement/"], o
    assert o["listeners_other"] == 1 and o["listeners_ios"] == 2, o
    assert o["sso_prevented"] is False, o
    assert o["page_prevented"] is True and o["page_href"] == "https://x.test/procurement/pr/3", o
    print("ok  navback.js: back, POST->redirect, #anchors, no history, iOS-only files, /sso/ links")


def run():
    js_checks()
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
            # an export either downloads (data-file-download) or opens in the in-app viewer
            # (data-file-view, e.g. PR/PO/RFQ Excel) - never a bare link that strands iOS
            assert all("data-file-download" in t or ("data-file-view" in t and "view=1" in t)
                       for t in tags), (path, tags)
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
