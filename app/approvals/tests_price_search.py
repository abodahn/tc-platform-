"""Online price comparison on a request line (proc_price_search).

Financial Control and everyone above them can compare a line's price across
online stores from the request page. The model's offers are only candidates:
each is matched to the search citations and re-checked live on the store's own
page, and the page price is the one shown. No network here — OpenRouter, the
store pages, DNS and SerpAPI are all stubbed.

    python -m app.approvals.tests_price_search
"""
import json
import os
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _app():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import config
    config.Config.DB_PATH = os.path.join(tempfile.mkdtemp(), "ps.db")
    config.Config.OPENROUTER_API_KEY = "test-key-not-real"
    config.Config.SERPAPI_API_KEY = ""
    os.environ.pop("DATABASE_URL", None)
    os.environ["TC_HEALTH_TIMEOUT"] = "1"
    from app import create_app
    return create_app()


OK_ALL = [True]


def chk(label, cond, extra=""):
    OK_ALL[0] &= bool(cond)
    print(("  PASS  " if cond else "  FAIL  ") + label + ((" | " + str(extra)) if extra else ""))


# ---------------------------------------------------------------- stubs
class FakeResp:
    def __init__(self, status=200, body="", headers=None, chunks=None, payload=None):
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self._chunks = chunks
        self._payload = payload
        self.consumed = 0

    def iter_content(self, n):
        if self._chunks is not None:
            for c in self._chunks:
                self.consumed += 1
                yield c
            return
        for i in range(0, len(self._body), n):
            self.consumed += 1
            yield self._body[i:i + n]

    def close(self):
        pass

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


PAGES = {}           # url -> FakeResp factory
DNS = {}             # host -> ip
GETS = []            # urls actually requested by the page checker
POSTS = []           # OpenRouter payloads
MODEL = {"offers": [], "annotations": []}


def ld_page(price, cur="EGP"):
    return ('<html><head><script type="application/ld+json">'
            + json.dumps({"@context": "https://schema.org", "@type": "Product", "name": "x",
                          "offers": {"@type": "Offer", "price": str(price), "priceCurrency": cur}})
            + "</script></head><body>x</body></html>")


def fake_get(url):
    GETS.append(url)
    f = PAGES.get(url)
    if f is None:          # default: the page states exactly what the model claimed
        claim = next((o["price"] for o in MODEL["offers"] if o.get("url") == url), None)
        return FakeResp(200, ld_page(claim)) if claim else FakeResp(403, "blocked")
    return f()


def fake_resolve(host, port):
    import ipaddress
    try:
        ipaddress.ip_address(host)
        ip = host
    except ValueError:
        ip = DNS.get(host, "93.184.216.34")
    return [(2, 1, 6, "", (ip, port))]


def fake_post(url, headers=None, json=None, timeout=None):   # noqa: A002 — requests' own name
    POSTS.append(json)
    import json as _j
    msg = {"role": "assistant",
           "content": "```json\n" + _j.dumps({"query": "q", "currency": "EGP",
                                               "offers": MODEL["offers"], "summary": "ok"}) + "\n```",
           "annotations": [{"type": "url_citation", "url_citation": {"url": u, "title": "t"}}
                           for u in MODEL["annotations"]]}
    return FakeResp(200, payload={"choices": [{"message": msg}]})


def install():
    import requests
    from app.services import price_verify as pv
    pv._get = fake_get
    pv._resolve = fake_resolve
    requests.post = fake_post


# ---------------------------------------------------------------- 1. page check
def page_check():
    from app.services import price_verify as pv
    print("the live page check reads the store's own price")
    graph = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage", "name": "p"},
        {"@type": "Product", "name": "bearing",
         "offers": {"@type": "AggregateOffer", "lowPrice": "185.00", "highPrice": "240",
                    "priceCurrency": "EGP"}}]}
    html = '<script type="application/ld+json">%s</script>' % json.dumps(graph)
    chk("JSON-LD @graph + AggregateOffer lowPrice", pv.extract(html)["structured"][:1] == [(185.0, "EGP")],
        pv.extract(html))
    chk("JSON-LD Product.offers price", pv.extract(ld_page("1,234.50"))["structured"] == [(1234.5, "EGP")])
    lst = '<script type="application/ld+json">[{"@type":"Offer","priceSpecification":{"price":99,"priceCurrency":"EGP"}}]</script>'
    chk("JSON-LD list + priceSpecification", pv.extract(lst)["structured"] == [(99.0, "EGP")])
    meta = ('<meta property="product:price:amount" content="450.00">'
            '<meta property="product:price:currency" content="EGP">')
    chk("meta product:price:amount", pv.extract(meta)["structured"] == [(450.0, "EGP")])
    chk("itemprop=price meta", pv.extract('<meta itemprop="price" content="77">')["structured"] == [(77.0, "")])
    ar = "<body><h1>مياه معدنية</h1><span>السعر: ١٬٢٣٤٫٥٠ ج.م</span><script>var p=1</script></body>"
    chk("Arabic-Indic digits + ج.م in text", pv.extract(ar)["text"][:1] == [1234.5], pv.extract(ar))
    chk("'EGP 1,250' and '99 LE' in text", pv.extract("<p>EGP 1,250</p><p>99 LE</p>")["text"] == [1250.0, 99.0])

    page = {"structured": [(101.5, "EGP")], "text": []}
    chk("within 2% -> confirmed (page price shown)", pv.classify(100, "EGP", page) == ("confirmed", 101.5, "EGP"))
    chk("clear different price -> price_changed", pv.classify(80, "EGP", page) == ("price_changed", 101.5, "EGP"))
    chk("unreadable page -> unconfirmed", pv.classify(80, "EGP", None)[0] == "unconfirmed")
    chk("text-only number confirms a matching claim",
        pv.classify(1234, "EGP", {"structured": [], "text": [5.0, 1234.5]})[0] == "confirmed")
    chk("text-only number never claims a change",
        pv.classify(900, "EGP", {"structured": [], "text": [1234.5]})[0] == "unconfirmed")

    PAGES.clear()
    PAGES["https://s.example.com/gone"] = lambda: FakeResp(404, "nope")
    PAGES["https://s.example.com/blocked"] = lambda: FakeResp(429, "slow down")
    PAGES["https://s.example.com/moved"] = lambda: FakeResp(200, ld_page(130))
    PAGES["https://s.example.com/ok"] = lambda: FakeResp(200, ld_page(100))
    o = lambda u, p: {"url": u, "price": p, "currency": "EGP", "title": "t", "store": "s"}
    chk("404 -> dead, dropped", pv.verify_offer(o("https://s.example.com/gone", 10)) is None)
    r = pv.verify_offer(o("https://s.example.com/blocked", 10))
    chk("429 -> unconfirmed, claim kept", r["status"] == "unconfirmed" and r["price"] == 10 and r["checked_at"])
    r = pv.verify_offer(o("https://s.example.com/moved", 100))
    chk("changed -> page price shown, claim kept",
        r["status"] == "price_changed" and r["price"] == 130 and r["claimed_price"] == 100, r)
    r = pv.verify_offer(o("https://s.example.com/ok", 100))
    chk("confirmed", r["status"] == "confirmed" and r["page_price"] == 100)

    print("the SSRF guard")
    DNS["intranet-box.example.com"] = "192.168.1.10"
    DNS["internal.example.com"] = "10.1.1.1"
    DNS["mapped.example.com"] = "::ffff:10.0.0.1"
    for bad in ("http://127.0.0.1/", "http://10.0.0.5/x", "http://169.254.169.254/latest/meta-data",
                "http://localhost/", "http://[::1]/", "http://intranet-box.example.com/p",
                "http://mapped.example.com/", "ftp://s.example.com/", "https://s.example.com:8443/",
                "http://printer.local/", "https://user:pw@s.example.com/", "javascript:alert(1)"):
        try:
            pv.check_url(bad)
            refused = False
        except pv.Refused:
            refused = True
        chk("refuses %s" % bad, refused)
    try:
        pv.check_url("https://www.amazon.eg/dp/X")
        chk("accepts a public store page", True)
    except pv.Refused as e:
        chk("accepts a public store page", False, e)
    GETS.clear()
    PAGES["https://s.example.com/hop"] = lambda: FakeResp(302, "", {"Location": "http://internal.example.com/admin"})
    chk("a redirect hop to a private IP -> dead",
        pv.verify_offer(o("https://s.example.com/hop", 10)) is None)
    chk("and the private hop was never requested", GETS == ["https://s.example.com/hop"], GETS)
    PAGES["https://s.example.com/hop2"] = lambda: FakeResp(301, "", {"Location": "/ok"})
    chk("a same-site relative redirect is followed",
        pv.verify_offer(o("https://s.example.com/hop2", 100))["status"] == "confirmed")

    big = FakeResp(200, chunks=[b"a" * 65536] * 48)          # 3 MB on offer
    text = pv._read_capped(big)
    chk("reads at most 1.5 MB", len(text) == pv.MAX_BYTES, len(text))
    chk("and stops pulling from the socket", big.consumed < 48, big.consumed)


# ---------------------------------------------------------------- 2. engine
def engine():
    import config
    from app.services import garamento as gm
    print("the search engine: citations, cleaning, ranking, stats, cache")
    gm._PS_CACHE.clear()
    PAGES.clear()
    for u in ("https://www.cited.example.com/p/1", "https://cited.example.com/other/9",
              "https://nocite.example.org/x"):
        PAGES[u] = lambda: FakeResp(403, "captcha")
    MODEL["annotations"] = ["https://cited.example.com/p/1/?utm_source=openrouter"]
    MODEL["offers"] = [
        {"title": "C", "store": "NoCite", "price": 5, "url": "https://nocite.example.org/x"},
        {"title": "B", "store": "Cited", "price": 9, "url": "https://cited.example.com/other/9"},
        {"title": "A", "store": "Cited", "price": 12, "url": "https://www.cited.example.com/p/1"},
        {"title": "js", "store": "x", "price": 3, "url": "javascript:alert(1)"},
        {"title": "data", "store": "x", "price": 3, "url": "data:text/html,hi"},
        {"title": "zero", "store": "x", "price": 0, "url": "https://z.example.com/0"},
        {"title": "nan", "store": "x", "price": "call us", "url": "https://z.example.com/n"},
        {"title": "dup", "store": "x", "price": 12, "url": "https://cited.example.com/p/1/"},
    ]
    POSTS.clear()
    r = gm.price_search("  Mineral   water 1.5L  ")
    chk("ok", r.get("ok"), r.get("message", ""))
    chk("query cleaned", r["query"] == "Mineral water 1.5L", r["query"])
    chk("the web plugin was asked for", POSTS[0].get("plugins") == [{"id": "web", "max_results": 10}])
    titles = [o["title"] for o in r["offers"]]
    chk("javascript:/data:, zero, non-numeric and duplicate URLs dropped", sorted(titles) == ["A", "B", "C"], titles)
    lv = {o["title"]: (o["verified"], o["verified_level"]) for o in r["offers"]}
    chk("URL = citation -> verified page", lv["A"] == (True, "page"), lv)
    chk("same store as a citation -> verified site", lv["B"] == (True, "site"), lv)
    chk("uncited host -> unverified", lv["C"] == (False, ""), lv)
    chk("unconfirmed sorted by citation strength", titles == ["A", "B", "C"], titles)
    chk("stats flagged as estimate (nothing confirmed)", r["stats"]["estimate"] and r["stats"]["confirmed"] == 0)
    chk("sources returned", [s["url"] for s in r["sources"]] == MODEL["annotations"])

    gm._PS_CACHE.clear()
    PAGES.clear()
    MODEL["annotations"] = []
    MODEL["offers"] = [
        {"title": "150", "store": "s1", "price": 150, "url": "https://a.example.com/1"},
        {"title": "100", "store": "s2", "price": 100, "url": "https://b.example.com/2"},
        {"title": "u50", "store": "s3", "price": 50, "url": "https://c.example.com/3"},
        {"title": "120", "store": "s4", "price": 120, "url": "https://d.example.com/4"},
    ]
    PAGES["https://c.example.com/3"] = lambda: FakeResp(403, "blocked")
    POSTS.clear()
    r = gm.price_search("SKF 6204 bearing")
    st = r["stats"]
    chk("stats over confirmed only: low/median/high",
        (st["low"], st["median"], st["high"], st["count"], st["estimate"]) == (100, 120, 150, 3, False), st)
    chk("confirmed first by price, unconfirmed last",
        [o["title"] for o in r["offers"]] == ["100", "120", "150", "u50"])
    chk("the cheapest confirmed offer is 'lowest'",
        [o["title"] for o in r["offers"] if o["lowest"]] == ["100"])
    r2 = gm.price_search("skf 6204   BEARING")
    chk("second search is served from cache", r2["cached"] and len(POSTS) == 1, len(POSTS))
    r3 = gm.price_search("SKF 6204 bearing", fresh=True)
    chk("fresh=1 bypasses the cache", not r3["cached"] and len(POSTS) == 2, len(POSTS))
    chk("1-character query refused", gm.price_search(" x ")["error"] == "bad_query")

    print("SerpAPI, when its key is set")
    import requests
    real_get = requests.get
    seen = {}

    def serp_get(url, params=None, timeout=None):
        seen.update(params or {})
        return FakeResp(200, payload={"shopping_results": [
            {"title": "Water 6x1.5L", "source": "Shop Z", "extracted_price": 60.0,
             "link": "https://z.example.net/water"}]})
    requests.get = serp_get
    config.Config.SERPAPI_API_KEY = "test-serp"
    gm._PS_CACHE.clear()
    try:
        r = gm.price_search("water")
    finally:
        requests.get = real_get
        config.Config.SERPAPI_API_KEY = ""
    z = [o for o in r["offers"] if o["engine"] == "serpapi"]
    chk("engine offers merged and page-checked",
        len(z) == 1 and z[0]["status"] == "unconfirmed" and z[0]["verified_level"] == "page", z)
    chk("google_shopping, gl=eg", seen.get("engine") == "google_shopping" and seen.get("gl") == "eg")

    config.Config.OPENROUTER_API_KEY = ""
    r = gm.price_search("water bottle")
    config.Config.OPENROUTER_API_KEY = "test-key-not-real"
    chk("no key -> the friendly offline dict", r.get("ok") is False and r.get("offline") is True, r)


# ---------------------------------------------------------------- 3. routes
def routes(app):
    import config
    from app.services import garamento as gm
    from app.db import get_db
    from app.approvals import services as svc
    print("who may search, on which line")
    roles = ("financial_controller", "cfo", "ceo", "managing_director", "board",
             "normal_user", "storekeeper", "purchasing_manager")
    ids = {}
    with app.app_context():
        c = get_db()
        for role in roles + ("normal_user",):
            un = "ps." + role if role not in ids else "ps.req"
            c.execute("INSERT INTO users (username, password_hash, full_name, role, is_active, created_at) "
                      "VALUES (?, 'x', ?, ?, 1, '2026-01-01')", (un, un, role))
            ids[un.replace("ps.", "")] = c.execute("SELECT id FROM users WHERE username=?", (un,)).fetchone()["id"]
        ids["super_admin"] = c.execute("SELECT id FROM users WHERE role='super_admin' ORDER BY id LIMIT 1").fetchone()["id"]
        c.commit()
        c.close()
        req = {"username": "ps.req", "id": ids["req"], "role": "normal_user"}
        pr1, _ = svc.create_pr({"title": "Water", "department": "IT"},
                               [{"item": "Mineral water 1.5L", "description": "Nestle, 6 bottles", "qty": 4, "unit": "Carton"},
                                {"item": "SKF 6204 bearing", "qty": 2, "unit": "Pcs"}], req, submit=False)
        pr2, _ = svc.create_pr({"title": "Other", "department": "IT"},
                               [{"item": "Toner", "qty": 1}], req, submit=False)
        pr3, _ = svc.create_pr({"title": "Many", "department": "IT"},
                               [{"item": "Part %02d" % i, "qty": 1} for i in range(26)], req, submit=False)
        lines1 = [it["id"] for it in svc.get_pr(pr1)["items"]]
        other_line = svc.get_pr(pr2)["items"][0]["id"]

    def client(key):
        cli = app.test_client()
        with cli.session_transaction() as s:
            s["uid"], s["ep"], s["_csrf_token"] = ids[key], 0, "tok"
        return cli

    H = {"X-CSRF-Token": "tok"}
    url1 = "/procurement/pr/%d/price-search" % pr1
    MODEL["annotations"] = []
    MODEL["offers"] = [{"title": "Water", "store": "s", "price": 60, "url": "https://w.example.com/1"}]
    PAGES.clear()
    for key in ("financial_controller", "cfo", "ceo", "managing_director", "board", "super_admin"):
        cli = client(key)
        gm._PS_CACHE.clear()
        r = cli.post(url1, json={"line_id": lines1[0]}, headers=H)
        chk("%s may search a line" % key, r.status_code == 200 and r.get_json().get("ok"), r.status_code)
        page = cli.get("/procurement/pr/%d" % pr1)
        html = page.get_data(as_text=True)
        chk("%s sees the buttons on the request page" % key,
            page.status_code == 200 and "data-ps-line=" in html and "data-ps-all" in html, page.status_code)
    for key in ("normal_user", "storekeeper", "purchasing_manager", "req"):
        cli = client(key)
        r = cli.post(url1, json={"line_id": lines1[0]}, headers=H)
        r2 = cli.post(url1 + "-all", json={}, headers=H)
        chk("%s is refused (403)" % key, r.status_code == 403 and r2.status_code == 403, (r.status_code, r2.status_code))
        page = cli.get("/procurement/pr/%d" % pr1)
        chk("%s sees no buttons" % key, "data-ps-line=" not in page.get_data(as_text=True))

    cfo = client("cfo")
    r = cfo.post(url1, json={"line_id": other_line}, headers=H)
    chk("a line from another request is refused", r.status_code == 404, r.status_code)
    r = cfo.post(url1, json={"line_id": lines1[0]})
    chk("no CSRF token -> refused", r.status_code in (400, 403), r.status_code)

    gm._PS_CACHE.clear()
    POSTS.clear()
    r = cfo.post(url1, json={"line_id": lines1[0], "query": ""}, headers=H).get_json()
    prompt = POSTS[-1]["messages"][1]["content"] if POSTS else ""
    chk("empty query -> built from the line in the database",
        r.get("query") == "Mineral water 1.5L Nestle, 6 bottles Carton"
        and "Product: Mineral water 1.5L Nestle, 6 bottles Carton" in prompt, r.get("query"))
    r = cfo.post(url1, json={"line_id": lines1[1], "query": "SKF 6204-2RS"}, headers=H).get_json()
    chk("an edited query is used as typed", r.get("query") == "SKF 6204-2RS")
    chk("a count unit is left out of the search text",
        cfo.post(url1, json={"line_id": lines1[1]}, headers=H).get_json().get("query") == "SKF 6204 bearing")
    with app.app_context():
        c = get_db()
        n = c.execute("SELECT COUNT(*) n FROM pr_events WHERE pr_id=? AND action='price_search'", (pr1,)).fetchone()["n"]
        c.close()
    chk("fresh searches land on the request's audit trail", n >= 2, n)

    print("compare all")
    gm._PS_CACHE.clear()
    r = cfo.post(url1 + "-all", json={}, headers=H).get_json()
    chk("one entry per line", [x["line_id"] for x in r["lines"]] == lines1
        and all(x["result"].get("ok") for x in r["lines"]), len(r["lines"]))
    r = cfo.post(url1 + "-all", json={"line_ids": [lines1[1]]}, headers=H).get_json()
    chk("a batch of line_ids", [x["line_id"] for x in r["lines"]] == [lines1[1]])
    r = cfo.post("/procurement/pr/%d/price-search-all" % pr3, json={}, headers=H).get_json()
    chk("capped at 25 lines", len(r["lines"]) == 25 and r["truncated"] is True, len(r["lines"]))

    print("procurement-only scope and offline")
    for key in ("cfo", "financial_controller"):
        page = client(key).get("/procurement/pr/%d" % pr1)
        chk("%s (procurement-only) opens the request page" % key, page.status_code == 200, page.status_code)
    config.Config.OPENROUTER_API_KEY = ""
    gm._PS_CACHE.clear()
    try:
        page = cfo.get("/procurement/pr/%d" % pr1)
        r = cfo.post(url1, json={"line_id": lines1[0]}, headers=H)
    finally:
        config.Config.OPENROUTER_API_KEY = "test-key-not-real"
    chk("offline: the request page still renders", page.status_code == 200)
    chk("offline: the friendly offline dict", r.status_code == 200 and r.get_json().get("offline") is True)


def run():
    app = _app()
    install()
    from app.approvals import constants as C
    from app.security import user_has_permission
    print("the permission")
    chk("registered", "proc_price_search" in C.PROC_PERMISSIONS)
    for role in ("financial_controller", "cfo", "financial_director", "ceo", "managing_director", "board", "super_admin"):
        chk("%s holds it" % role, user_has_permission({"role": role}, "proc_price_search"))
    for role in ("normal_user", "storekeeper", "purchasing_manager", "finance_manager", "warehouse_manager"):
        chk("%s does not" % role, not user_has_permission({"role": role}, "proc_price_search"))
    page_check()
    engine()
    routes(app)
    print("ALL PASS" if OK_ALL[0] else "SOME CHECKS FAILED")
    return OK_ALL[0]


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
