"""
TC Platform — live page check for product price search.

The price search asks a web-search model for offers. A model can misread a page
or invent one, and the owner buys from these links, so every candidate offer is
re-checked HERE, on the store's own page, before it is shown as a price:

  confirmed      the page states the claimed price (within 2% or 1 unit)
  price_changed  the page states a clear product price that differs
  unconfirmed    the page could not be read (blocked, captcha, JS-only, 403/429)
  dead           404/410, or the URL failed the SSRF guard -> the offer is dropped

The URLs come from model output, so they are untrusted: only public http(s)
hosts on ports 80/443 are fetched, every redirect hop is re-checked, no cookies
or credentials are sent, and at most 1.5 MB is read. Stdlib parsing only.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import requests

MAX_BYTES = 1_500_000
TIMEOUT = (4, 8)            # connect, read
MAX_REDIRECTS = 4
MAX_OFFERS = 12
WORKERS = 6
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en,ar",
}
_BAD_SUFFIX = (".local", ".internal", ".localhost", ".lan", ".home.arpa", ".intranet", ".corp")


class Refused(Exception):
    """The URL is not a public http(s) address we may fetch."""


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
    """Raise Refused unless `url` is http(s), port 80/443, and every address the
    host resolves to is public."""
    try:
        p = urlsplit(str(url or ""))
        port = p.port
    except ValueError:
        raise Refused("bad url")
    if p.scheme not in ("http", "https"):
        raise Refused("scheme")
    if p.username or p.password:
        raise Refused("credentials in url")
    host = (p.hostname or "").rstrip(".").lower()
    if not host or host == "localhost" or host.endswith(_BAD_SUFFIX) or "." not in host and ":" not in host:
        raise Refused("host")
    if port not in (None, 80, 443):
        raise Refused("port")
    try:
        infos = _resolve(host, port or (443 if p.scheme == "https" else 80))
    except (OSError, UnicodeError):
        raise Refused("dns")
    if not infos:
        raise Refused("dns")
    for info in infos:
        try:
            ok = _ip_ok(info[4][0])
        except ValueError:
            ok = False
        if not ok:
            raise Refused("private address")


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
def _get(url):
    """Indirection so tests can stub HTTP. No Session: no cookie jar, so nothing
    a hop sets is ever sent to the next."""
    return requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True,
                        allow_redirects=False)


def _read_capped(resp):
    buf = bytearray()
    for chunk in resp.iter_content(65536):
        buf += chunk
        if len(buf) >= MAX_BYTES:
            del buf[MAX_BYTES:]
            break
    ctype = (resp.headers.get("Content-Type") or "").lower()
    enc = resp.encoding if "charset=" in ctype and resp.encoding else "utf-8"
    try:
        return bytes(buf).decode(enc, errors="replace")
    except LookupError:
        return bytes(buf).decode("utf-8", errors="replace")


def fetch(url):
    """(status, final_url, text). Follows up to MAX_REDIRECTS hops by hand,
    re-running the SSRF guard on each. Raises Refused or requests exceptions.
    shortcut: requests re-resolves DNS after check_url, so a rebinding host could
    swap to a private IP in between; pin the checked IP in a custom adapter if
    this ever fetches more than a price (nothing but a parsed number is returned)."""
    for _hop in range(MAX_REDIRECTS + 1):
        check_url(url)
        resp = _get(url)
        try:
            loc = resp.headers.get("Location")
            if resp.status_code in (301, 302, 303, 307, 308) and loc:
                url = urljoin(url, loc)
                continue
            return resp.status_code, url, _read_capped(resp)
        finally:
            resp.close()
    raise Refused("too many redirects")


# --------------------------------------------------------------------------
# Price extraction
# --------------------------------------------------------------------------
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٬٫", "01234567890123456789,.")


def parse_number(v):
    """'1,234.50' / '١٬٢٣٤٫٥' / 1234.5 -> 1234.5; None when not a positive number."""
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
    return round(n, 2) if n > 0 else None


def _types(d):
    t = d.get("@type")
    return {str(x).lower() for x in (t if isinstance(t, list) else [t]) if x}


def _jsonld_prices(node, out, cur=None):
    """Walk any JSON-LD shape (@graph, lists, Product.offers, nested offers)."""
    if isinstance(node, list):
        for n in node:
            _jsonld_prices(n, out, cur)
        return
    if not isinstance(node, dict):
        return
    cur = node.get("priceCurrency") or cur
    types = _types(node)
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
                                   or cur or "").upper()))
                break
    for k, v in node.items():
        if isinstance(v, (dict, list)) and k != "priceSpecification":
            _jsonld_prices(v, out, cur)


class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.ld, self.text = {}, [], []
        self._in, self._buf = None, []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in, self._buf = "ld", []
        elif tag in ("script", "style", "noscript", "template"):
            self._in = "skip"
        key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
        if key and "content" in a and key not in self.meta:
            self.meta[key] = a["content"]

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "template") and self._in:
            if self._in == "ld":
                self.ld.append("".join(self._buf))
            self._in = None

    def handle_data(self, data):
        if self._in == "ld":
            self._buf.append(data)
        elif self._in is None and data.strip():
            self.text.append(data.strip())


_CUR = r"(?:EGP|L\.?E\.?|E£|ج\s?\.\s?م\.?|جنيه(?:\s*مصري)?|جنية)"
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_TEXT_PRICE = re.compile(_CUR + r"\s*" + _NUM + r"|" + _NUM + r"\s*" + _CUR, re.I)


def extract(page_html):
    """{"structured": [(price, CUR)], "text": [price, ...]} from one page.
    Structured = JSON-LD then meta tags (authoritative); text = EGP-adjacent
    numbers in the visible text (only good enough to CONFIRM a claim)."""
    p = _PageParser()
    try:
        p.feed(page_html or "")
        p.close()
    except Exception:  # noqa: BLE001 — a malformed page is just a page with less data
        pass
    found = []
    for raw in p.ld:
        for txt in (raw, unescape(raw)):
            try:
                _jsonld_prices(json.loads(txt.strip()), found)
                break
            except ValueError:
                continue
    if not found:
        m = p.meta
        for amt, cur in (("product:price:amount", "product:price:currency"),
                         ("og:price:amount", "og:price:currency"),
                         ("price", "pricecurrency")):
            n = parse_number(m.get(amt))
            if n is not None:
                found.append((n, (m.get(cur) or "").upper()))
                break
    text = " ".join(p.text)[:400_000].translate(_DIGITS)
    nums = []
    for mt in _TEXT_PRICE.finditer(text):
        n = parse_number(mt.group(1) or mt.group(2))
        if n is not None:
            nums.append(n)
        if len(nums) >= 8:       # the product's own price sits near the top
            break
    return {"structured": found, "text": nums}


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
    if cur == "EGP":
        for n in page.get("text") or []:
            if _close(n, claim):
                return "confirmed", n, cur
    # Loose text numbers are never trusted to SAY the price changed.
    return "unconfirmed", None, None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def verify_offer(offer):
    """Return the offer with status/page_price/checked_at set, or None if dead."""
    o = dict(offer)
    o["checked_at"] = _now()
    o["claimed_price"] = o.get("price")
    try:
        status, _final, body = fetch(o["url"])
    except Refused:
        return None
    except Exception:  # noqa: BLE001 — timeouts, TLS, resets: the page is unread, not gone
        status, body = None, None
    if status in (404, 410):
        return None
    page = extract(body) if status == 200 and body else None
    st, page_price, page_cur = classify(o["price"], o.get("currency"), page)
    o["status"] = st
    o["page_price"] = page_price
    if page_price is not None:
        o["price"] = page_price               # the page is authoritative
        o["currency"] = page_cur or o.get("currency")
    return o


def verify_offers(offers):
    """Check up to MAX_OFFERS offers in parallel; dead ones are dropped."""
    offers = list(offers)[:MAX_OFFERS]
    if not offers:
        return []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return [o for o in pool.map(verify_offer, offers) if o]
