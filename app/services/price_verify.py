"""
TC Platform — live page check for product price search.

The price search asks a web-search model for offers. A model can misread a page
or invent one, and the owner buys from these links, so every candidate offer is
re-checked HERE, on the store's own page, before it is shown as a price:

  confirmed      the page states the claimed price (within 2% or 1 unit)
  price_changed  the page states a clear product price that differs
  unconfirmed    the page could not be read (blocked, captcha, JS-only, 403/429,
                 slow, redirect loop), or it lists several products, or the link
                 now lands on a different page
  dead           404/410, or the URL failed the SSRF guard -> the offer is dropped

The URLs come from model output, so they are untrusted: only public http(s)
hosts on ports 80/443 are fetched, every redirect hop is re-checked, the socket's
real peer address is checked again before a byte is sent (DNS rebinding), no
cookies, credentials or environment proxies are used, at most 1.5 MB is read and
each offer has a wall-clock deadline. Stdlib parsing only.
"""
from __future__ import annotations

import ipaddress
import json
import math
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.util import parse_url

MAX_BYTES = 1_500_000
TIMEOUT = (4, 8)            # connect, read (per socket operation)
DEADLINE = 15.0             # seconds per offer: every hop and the body together
BATCH_WAIT = 35.0           # seconds verify_offers waits before giving up on stragglers
MAX_REDIRECTS = 4
MAX_OFFERS = 12
WORKERS = 6
MAX_DEPTH = 30              # JSON-LD nesting walked
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en,ar",
}
_BAD_SUFFIX = (".local", ".internal", ".localhost", ".lan", ".home.arpa", ".intranet", ".corp")
_HOST = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_NXDOMAIN = {getattr(socket, n) for n in ("EAI_NONAME", "EAI_NODATA") if hasattr(socket, n)}


class Refused(Exception):
    """The URL is not a public http(s) address we may fetch (-> offer dropped)."""


class Unreadable(Exception):
    """The page could not be read this time (-> offer kept, unconfirmed)."""


# --------------------------------------------------------------------------
# SSRF guard
# --------------------------------------------------------------------------
def _resolve(host, port):
    """Indirection so tests can stub DNS."""
    return socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)


def _ip_ok(addr):
    ip = ipaddress.ip_address(addr.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified or not ip.is_global)


def check_url(url):
    """Return the URL exactly as requests will send it, or raise Refused unless
    it is http(s), port 80/443, and every address its host resolves to is
    public. The host checked is the one requests dials (parsed from the prepared
    URL), so parser differences such as a backslash cannot split the two."""
    url = str(url or "")
    if re.search(r"[\\\s\x00-\x1f\x7f]", url):
        raise Refused("bad url")
    try:
        prepared = requests.Request("GET", url).prepare().url
        u = parse_url(prepared)
        port = u.port
    except Exception:  # noqa: BLE001 — anything requests cannot parse is not fetched
        raise Refused("bad url")
    if u.scheme not in ("http", "https"):
        raise Refused("scheme")
    if u.auth:
        raise Refused("credentials in url")
    host = (u.host or "").strip("[]").rstrip(".").lower()
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        literal = True
    except ValueError:
        literal = False
    if (not host or host == "localhost" or host.endswith(_BAD_SUFFIX)
            or not literal and not _HOST.match(host)):
        raise Refused("host")
    if port not in (None, 80, 443):
        raise Refused("port")
    try:
        infos = _resolve(host, port or (443 if u.scheme == "https" else 80))
    except socket.gaierror as e:
        if e.errno in _NXDOMAIN:
            raise Refused("dns")
        raise Unreadable("dns")              # resolver hiccup, not a missing host
    except UnicodeError:
        raise Refused("dns")
    except OSError:
        raise Unreadable("dns")
    if not infos:
        raise Refused("dns")
    for info in infos:
        try:
            ok = _ip_ok(info[4][0])
        except ValueError:
            ok = False
        if not ok:
            raise Refused("private address")
    return prepared


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
_live = threading.local()       # the running fetch's {"sock": ...}, for the deadline cut


class _PeerCheck:
    """Check the address the socket is really connected to before anything is
    sent: DNS may answer differently now than it did for check_url."""

    def _new_conn(self):
        sock = super()._new_conn()
        try:
            if not _ip_ok(sock.getpeername()[0]):
                raise Refused("private address")
        except Exception:
            sock.close()
            raise
        holder = getattr(_live, "holder", None)
        if holder is not None:
            holder["sock"] = sock
        return sock


class _HTTPConn(_PeerCheck, HTTPConnection):
    pass


class _HTTPSConn(_PeerCheck, HTTPSConnection):
    pass


class _HTTPPool(HTTPConnectionPool):
    ConnectionCls = _HTTPConn


class _HTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _HTTPSConn


class _GuardAdapter(HTTPAdapter):
    def init_poolmanager(self, *a, **kw):
        super().init_poolmanager(*a, **kw)
        self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}


def _get(url):
    """Indirection so tests can stub HTTP. A fresh session per hop: nothing a
    hop sets as a cookie is sent to the next, and no environment proxy is used."""
    with requests.Session() as s:
        s.trust_env = False
        a = _GuardAdapter()
        s.mount("http://", a)
        s.mount("https://", a)
        return s.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True, allow_redirects=False)


def _cut(holder):
    """Deadline reached: shut the socket so a blocked read returns now."""
    holder["cut"] = True
    sock = holder.get("sock")
    try:
        if sock is not None:
            sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _read_capped(resp, deadline=None):
    buf = bytearray()
    for chunk in resp.iter_content(65536):
        buf += chunk
        if len(buf) >= MAX_BYTES:
            del buf[MAX_BYTES:]
            break
        if deadline is not None and time.monotonic() > deadline:
            raise Unreadable("deadline")
    ctype = (resp.headers.get("Content-Type") or "").lower()
    enc = resp.encoding if "charset=" in ctype and resp.encoding else "utf-8"
    try:
        return bytes(buf).decode(enc, errors="replace")
    except LookupError:
        return bytes(buf).decode("utf-8", errors="replace")


def fetch(url):
    """(status, final_url, text). Follows up to MAX_REDIRECTS hops by hand,
    re-running the SSRF guard on each, all within DEADLINE seconds. Raises
    Refused (drop the offer), Unreadable or requests exceptions (keep it)."""
    deadline = time.monotonic() + DEADLINE
    holder = _live.holder = {}
    timer = threading.Timer(DEADLINE, _cut, (holder,))
    timer.daemon = True
    timer.start()
    try:
        for _hop in range(MAX_REDIRECTS + 1):
            if holder.get("cut") or time.monotonic() > deadline:
                raise Unreadable("deadline")
            url = check_url(url)
            resp = _get(url)
            try:
                loc = resp.headers.get("Location")
                if resp.status_code in (301, 302, 303, 307, 308) and loc:
                    url = urljoin(url, loc)
                    continue
                return resp.status_code, url, _read_capped(resp, deadline)
            finally:
                resp.close()
        raise Unreadable("too many redirects")   # often a cookie-check loop, not a dead page
    finally:
        timer.cancel()
        _live.holder = None


# --------------------------------------------------------------------------
# Price extraction
# --------------------------------------------------------------------------
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬٫", "01234567890123456789,.")


def parse_number(v):
    """'1,234.50' / '١٬٢٣٤٫٥' / 1234.5 -> 1234.5; None unless a positive finite number."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        s = str(v).translate(_DIGITS)
        m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
        if not m:
            return None
        try:
            n = float(m.group(0).replace(",", ""))
        except ValueError:
            return None
    return round(n, 2) if math.isfinite(n) and n > 0 else None


def _types(d):
    t = d.get("@type")
    return {str(x).lower() for x in (t if isinstance(t, list) else [t]) if x}


def _jsonld_prices(node, out, cur=None, owner=None, depth=0):
    """Walk any JSON-LD shape (@graph, lists, Product.offers, nested offers).
    Appends (price, CUR, owner): owner is the Product the offer belongs to (or
    the bare Offer itself), so a page listing several products is recognised."""
    if depth > MAX_DEPTH:
        return
    if isinstance(node, list):
        for n in node:
            _jsonld_prices(n, out, cur, owner, depth + 1)
        return
    if not isinstance(node, dict):
        return
    cur = node.get("priceCurrency") or cur
    types = _types(node)
    if "product" in types:
        owner = node
    if types & {"offer", "aggregateoffer"}:
        spec = node.get("priceSpecification")
        if isinstance(spec, list):
            spec = spec[0] if spec else None
        spec = spec if isinstance(spec, dict) else {}
        for key in ("price", "lowPrice"):
            n = parse_number(node.get(key))
            if n is None and key == "price":
                n = parse_number(spec.get("price"))
            if n is not None:
                out.append((n, str(node.get("priceCurrency") or spec.get("priceCurrency")
                                   or cur or "").upper(), owner if owner is not None else node))
                break
    for k, v in node.items():
        if isinstance(v, (dict, list)) and k != "priceSpecification":
            _jsonld_prices(v, out, cur, owner, depth + 1)


def _page_key(u, base=None):
    """(host without www., path without trailing slash): 'the same page'."""
    try:
        p = urlsplit(urljoin(base or "", str(u or "")))
        host = (p.hostname or "").lower()
    except ValueError:
        return None
    return (host[4:] if host.startswith("www.") else host), p.path.rstrip("/")


def _owner_is(owner, url):
    """Does this Product / Offer name `url` as its own page?"""
    if not url:
        return False
    want = _page_key(url)
    for k in ("url", "@id"):
        v = owner.get(k)
        if isinstance(v, str) and v and _page_key(v, url) == want:
            return True
    return False


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
         "param", "source", "track", "wbr"}
# Text inside these is a price that is NOT the one to pay: old/struck prices,
# shipping and delivery fees, instalment amounts.
_NOT_PRICE = {"old", "was", "strike", "strikethrough", "through", "crossed", "shipping",
              "delivery", "installment", "installments", "instalment", "instalments"}


def _not_price_class(cls):
    return any(p.lower() in _NOT_PRICE for p in re.split(r"[\s_-]+|(?<=[a-z])(?=[A-Z])", cls) if p)


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.ld, self.text = {}, [], []
        self._in, self._buf = None, []
        self._skip_tag, self._skip_n = None, 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in, self._buf = "ld", []
        elif tag in ("script", "style", "noscript", "template"):
            self._in = "skip"
        if self._skip_tag:
            if tag == self._skip_tag:
                self._skip_n += 1
        elif tag not in _VOID and (tag in ("s", "del", "strike") or _not_price_class(a.get("class", ""))):
            self._skip_tag, self._skip_n = tag, 1
        key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
        if key and "content" in a and key not in self.meta:
            self.meta[key] = a["content"]

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._in:
            if self._in == "ld":
                self.ld.append("".join(self._buf))
            self._in = None
        if tag == self._skip_tag:
            self._skip_n -= 1
            if self._skip_n <= 0:
                self._skip_tag = None

    def handle_data(self, data):
        if self._in == "ld":
            self._buf.append(data)
        elif self._in is None and not self._skip_tag and data.strip():
            self.text.append(data.strip())


# Latin tokens need a non-letter on both sides: the "le" of "left" or "Sale" is not LE.
_CUR = (r"(?:(?<![A-Za-z])(?:EGP|L\.?E|E£)(?![A-Za-z])\.?"
        r"|ج\s?\.\s?م\.?|جنيه(?:\s*مصري)?|جنية)")
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_TEXT_PRICE = re.compile(_CUR + r"\s*" + _NUM + r"|" + _NUM + r"\s*" + _CUR, re.I)


def extract(page_html, url=None):
    """{"structured": [(price, CUR)], "text": [price, ...], "multi": bool} from
    one page. Structured = JSON-LD then meta tags of ONE product (authoritative);
    text = EGP-adjacent numbers in the visible text (only good enough to CONFIRM
    a claim). multi = the page carries several products and none of them is
    `url` itself: nothing on it is trusted."""
    p = _PageParser()
    try:
        p.feed(page_html or "")
        p.close()
    except Exception:  # noqa: BLE001 — a malformed page is just a page with less data
        pass
    found = []
    for raw in p.ld:
        for txt in (raw, unescape(raw)):
            got = []
            try:
                _jsonld_prices(json.loads(txt.strip()), got)
            except Exception:  # noqa: BLE001 — bad or hostile JSON, RecursionError included
                continue
            found += got
            break
    owners = {id(o): o for _n, _c, o in found}
    multi = False
    if len(owners) > 1:
        mine = [k for k, o in owners.items() if _owner_is(o, url)]
        if len(mine) == 1:
            found = [f for f in found if id(f[2]) == mine[0]]
        else:
            found, multi = [], True
    structured = [(n, c) for n, c, _o in found]
    if not structured and not multi:
        m = p.meta
        for amt, cur in (("product:price:amount", "product:price:currency"),
                         ("og:price:amount", "og:price:currency"),
                         ("price", "pricecurrency")):
            n = parse_number(m.get(amt))
            if n is not None:
                structured.append((n, (m.get(cur) or "").upper()))
                break
    nums = []
    if not multi:
        text = " ".join(p.text)[:400_000].translate(_DIGITS)
        for mt in _TEXT_PRICE.finditer(text):
            n = parse_number(mt.group(1) or mt.group(2))
            if n is not None:
                nums.append(n)
            if len(nums) >= 8:       # the product's own price sits near the top
                break
    return {"structured": structured, "text": nums, "multi": multi}


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------
def _close(a, b):
    return abs(a - b) <= max(0.02 * b, 1.0)


def classify(claim, currency, page):
    """(status, page_price, page_currency) for a claimed price against
    extract() output, or page=None when nothing was read."""
    if not page:
        return "unconfirmed", None, None
    cur = (currency or "").upper()
    structured = page.get("structured") or []
    same = [(n, c) for n, c in structured if not c or c == cur]
    for n, c in same:
        if _close(n, claim):
            return "confirmed", n, c or cur
    if structured:
        n, c = same[0] if same else structured[0]
        return "price_changed", n, c or cur
    # Loose text confirms only when the page shows ONE price and it is the
    # claim (2%, no absolute slack). It never says the price changed.
    nums = set(page.get("text") or [])
    if cur == "EGP" and len(nums) == 1:
        n = nums.pop()
        if abs(n - claim) <= 0.02 * claim:
            return "confirmed", n, cur
    return "unconfirmed", None, None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unconfirmed(offer):
    o = dict(offer)
    o.setdefault("checked_at", _now())
    o.setdefault("claimed_price", o.get("price"))
    o.update(status="unconfirmed", page_price=None)
    return o


def verify_offer(offer):
    """Return the offer with status/page_price/checked_at set, or None if dead."""
    o = dict(offer)
    o["checked_at"] = _now()
    o["claimed_price"] = o.get("price")
    try:
        status, final, body = fetch(o["url"])
    except Refused:
        return None
    except Exception:  # noqa: BLE001 — timeouts, TLS, resets, deadline: unread, not gone
        status, final, body = None, None, None
    if status in (404, 410):
        return None
    try:
        # A link that now lands on another page (category, home, login, another
        # product) says nothing about this offer: only the page itself counts.
        same = final is not None and _page_key(final) == _page_key(o["url"])
        page = extract(body, final) if status == 200 and body and same else None
        st, page_price, page_cur = classify(o["price"], o.get("currency"), page)
    except Exception:  # noqa: BLE001 — one odd page never sinks the search
        st, page_price, page_cur = "unconfirmed", None, None
    o["status"] = st
    o["page_price"] = page_price
    if page_price is not None:
        o["price"] = page_price               # the page is authoritative
        o["currency"] = page_cur or o.get("currency")
    return o


def verify_offers(offers):
    """Check up to MAX_OFFERS offers in parallel; dead ones are dropped, and any
    still running after BATCH_WAIT seconds come back unconfirmed."""
    offers = list(offers)[:MAX_OFFERS]
    if not offers:
        return []
    pool = ThreadPoolExecutor(max_workers=WORKERS)
    try:
        futs = [pool.submit(verify_offer, o) for o in offers]
        wait(futs, timeout=BATCH_WAIT)
        out = []
        for f, o in zip(futs, offers):
            if not f.done():
                out.append(_unconfirmed(o))
                continue
            try:
                r = f.result()
            except Exception:  # noqa: BLE001
                r = _unconfirmed(o)
            if r:
                out.append(r)
        return out
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
