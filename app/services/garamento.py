"""
TC Platform — Garamento, the textile & fashion AI assistant.

Garamento is a witty, dapper master of fabric, fashion and manufacturing who
also knows his way around this platform. This module wraps OpenRouter so the
rest of the app never touches HTTP or the API key directly.

Two public entry points:
  * ``chat(history, user)``          -> a friendly assistant reply (string)
  * ``market_research(item, ...)``   -> a live approximate price (dict)

The API key is read from config (environment only). If it is missing, both
functions fail gracefully with a friendly, in-character message rather than an
exception, so the UI can always show *something* sensible.
"""
from __future__ import annotations

import json
import re

import requests

from config import Config


# --------------------------------------------------------------------------
# Persona
# --------------------------------------------------------------------------
PERSONA = (
    "You are Garamento — the charming, quick-witted textile & fashion maestro of "
    "the T&C Garments Operations Platform. Think third-generation Italian master "
    "tailor crossed with a supply-chain nerd. You are an EXPERT in: fabrics and "
    "fibres (cotton, denim, linen, wool, silk, polyester, blends, GSM, weaves, "
    "knits), garment construction, patterns, grading, stitching, trims, dyeing "
    "and finishing, quality standards (AQL, ISO), sizing, fashion trends, "
    "sourcing, costing, MOQs, Incoterms and procurement.\n\n"
    "Personality: warm, funny, and a little theatrical. You sprinkle in tasteful "
    "textile puns ('sew good', 'that idea is seamless', 'let's stitch this up', "
    "'material difference') — but never at the cost of being genuinely useful. "
    "One pun per reply at most; substance first, sparkle second.\n\n"
    "Rules:\n"
    "- Be concise and practical. Prefer short paragraphs, tight bullet lists, and "
    "real numbers over fluff.\n"
    "- When asked about prices, give ranges and say they are approximate.\n"
    "- You also help users of THIS platform: procurement/purchase requests, "
    "approvals, maintenance, BI reports, inventory. If someone asks how to do "
    "something in the app, guide them clearly.\n"
    "- If a question is truly outside textiles, fashion, manufacturing, "
    "procurement or this platform, answer briefly and steer back with a smile.\n"
    "- Never invent platform features you are unsure exist; say what you'd check.\n"
    "- Use the user's language: reply in Arabic if they write Arabic, Turkish if "
    "Turkish, otherwise English.\n"
    "- Keep replies under ~180 words unless the user asks for depth."
)

_GREETING = (
    "Ciao! I'm **Garamento** \U0001F9F5 — your textile & fashion right hand. "
    "Ask me about fabrics, costing, sourcing, garment construction, or how to get "
    "things done here on the platform. What are we making today?"
)


# --------------------------------------------------------------------------
# Low-level OpenRouter call
# --------------------------------------------------------------------------
class GaramentoOffline(Exception):
    """Raised when no API key is configured."""


def is_enabled() -> bool:
    return bool(Config.OPENROUTER_API_KEY)


def greeting() -> str:
    return _GREETING


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {Config.OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Optional attribution headers OpenRouter recommends.
        "HTTP-Referer": Config.PUBLIC_URL or "https://tc-platform.onrender.com",
        "X-Title": "TC Platform Garamento",
    }


def _complete(messages, model=None, temperature=0.6, max_tokens=700, timeout=None):
    """Call OpenRouter chat/completions and return the assistant text.

    Raises GaramentoOffline if unconfigured; requests exceptions bubble up so
    callers can translate them into friendly errors.
    """
    if not is_enabled():
        raise GaramentoOffline()
    payload = {
        "model": model or Config.OPENROUTER_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = requests.post(
        f"{Config.OPENROUTER_BASE}/chat/completions",
        headers=_headers(),
        json=payload,
        timeout=timeout or Config.OPENROUTER_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------
def chat(history, user=None):
    """`history` is a list of {role, content} from the browser (user/assistant).

    Returns a dict: {ok, reply} or {ok:False, error}.
    """
    if not is_enabled():
        return {
            "ok": False,
            "offline": True,
            "reply": (
                "\U0001F9F5 Garamento is off the clock — no OpenRouter API key is "
                "configured yet. Ask your admin to set OPENROUTER_API_KEY and I'll "
                "be back on the floor in a snap."
            ),
        }

    # Keep only the last ~12 turns to bound cost, and sanitise roles.
    turns = []
    for m in (history or [])[-12:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            turns.append({"role": role, "content": content[:4000]})
    if not turns:
        turns = [{"role": "user", "content": "Hello!"}]

    sys = PERSONA
    if user:
        who = user.get("full_name") or user.get("username") or ""
        if who:
            sys += f"\n\nThe person you're talking to is {who} at T&C Garments."

    messages = [{"role": "system", "content": sys}] + turns
    try:
        reply = _complete(messages, temperature=0.7, max_tokens=650)
    except requests.exceptions.Timeout:
        return {"ok": False, "reply": "I got tangled in a slow thread — give me another try?"}
    except requests.exceptions.RequestException as exc:
        detail = _http_detail(exc)
        return {"ok": False, "reply": f"My connection to the atelier dropped ({detail}). Try again in a moment."}
    except Exception:  # noqa: BLE001
        return {"ok": False, "reply": "Something unravelled on my end. Mind trying that again?"}
    return {"ok": True, "reply": reply or "…"}


# --------------------------------------------------------------------------
# Market research (live web price)
# --------------------------------------------------------------------------
_MARKET_SYS = (
    "You are Garamento, a sharp procurement price researcher for a garment "
    "manufacturer. Using live web results, estimate the current market price of "
    "each requested item. Prefer B2B/wholesale and reputable suppliers (Alibaba, "
    "IndiaMART, Made-in-China, and local/regional distributors). Always price PER "
    "the item's stated unit; if a source quotes a different unit, convert it and "
    "note that. Give realistic ranges, flag obvious outliers, and pick the best "
    "value vendor. If the requested currency differs from the source, convert at "
    "a recent rate. ALWAYS answer with JSON only and nothing else."
)

_ITEM_JSON_SHAPE = (
    "{\n"
    '  "item": string,\n'
    '  "currency": string (ISO code = the requested currency),\n'
    '  "unit": string (the pricing unit, normalized, e.g. "meter", "piece", "kg"),\n'
    '  "price_low": number,\n'
    '  "price_high": number,\n'
    '  "price_est": number (best single estimate, PER unit),\n'
    '  "confidence": "low" | "medium" | "high",\n'
    '  "as_of": string (when these prices are from, e.g. "2026" or "recent"),\n'
    '  "best_vendor": {"name": string, "reason": string} | null,\n'
    '  "note": string (caveats: unit conversion, outliers, currency conversion; "" if none),\n'
    '  "summary": string (<= 220 chars, friendly, at most one gentle Garamento pun),\n'
    '  "sources": [ {"name": string, "price": string, "url": string} ]  // up to 4, best value first\n'
    "}"
)

_MARKET_SCHEMA_HINT = (
    "Respond ONLY with a single JSON object of this exact shape:\n" + _ITEM_JSON_SHAPE + "\n"
    "Numbers must be plain (no currency symbols, no thousands separators). If you "
    "cannot find prices, still return the JSON with your best estimate and "
    'confidence "low".'
)


def _web_model() -> str:
    if Config.OPENROUTER_MODEL_WEB:
        return Config.OPENROUTER_MODEL_WEB
    base = Config.OPENROUTER_MODEL
    return base if base.endswith(":online") else base + ":online"


def _extract_json(text):
    """Pull the first JSON object out of a model reply (handles code fences)."""
    if not text:
        return None
    # strip ```json fences
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    raw = fenced.group(1) if fenced else None
    if raw is None:
        start = text.find("{")
        end = text.rfind("}")
        raw = text[start:end + 1] if start != -1 and end > start else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def _num(v):
    try:
        if isinstance(v, str):
            v = re.sub(r"[^0-9.\-]", "", v)
        return round(float(v), 2)
    except Exception:  # noqa: BLE001
        return None


def _normalize_price(data, item, unit, currency):
    """Turn one raw model dict into a clean, validated price result (or None)."""
    if not isinstance(data, dict):
        return None
    est = _num(data.get("price_est"))
    low = _num(data.get("price_low"))
    high = _num(data.get("price_high"))
    if est is None and low is not None and high is not None:
        est = round((low + high) / 2, 2)
    if est is None and (low is not None or high is not None):
        est = low if low is not None else high
    if est is None:
        return None
    # keep low/high sane relative to est
    if low is not None and high is not None and low > high:
        low, high = high, low

    sources = []
    for s in (data.get("sources") or [])[:4]:
        if isinstance(s, dict) and (s.get("name") or s.get("url")):
            sources.append({
                "name": str(s.get("name") or s.get("url") or "source")[:80],
                "price": str(s.get("price") or "")[:60],
                "url": str(s.get("url") or "")[:400],
            })

    bv = data.get("best_vendor")
    best_vendor = None
    if isinstance(bv, dict) and bv.get("name"):
        best_vendor = {"name": str(bv.get("name"))[:80], "reason": str(bv.get("reason") or "")[:160]}

    conf = str(data.get("confidence") or "medium").lower()
    if conf not in ("low", "medium", "high"):
        conf = "medium"

    return {
        "ok": True,
        "item": str(data.get("item") or item)[:120],
        "currency": str(data.get("currency") or currency)[:8].upper(),
        "unit": str(data.get("unit") or unit or "")[:40],
        "price_est": est,
        "price_low": low,
        "price_high": high,
        "confidence": conf,
        "as_of": str(data.get("as_of") or "")[:40],
        "best_vendor": best_vendor,
        "note": str(data.get("note") or "")[:240],
        "summary": str(data.get("summary") or "Approximate market estimate.")[:300],
        "sources": sources,
    }


def _web_call(messages, extra_timeout=0):
    """Shared web-search completion with friendly error translation.
    Returns (text, error_dict). One of them is None."""
    if not is_enabled():
        return None, {"ok": False, "offline": True,
                      "message": ("Garamento's price radar is offline — no OpenRouter API "
                                  "key configured. Ask your admin to set OPENROUTER_API_KEY.")}
    try:
        text = _complete(messages, model=_web_model(), temperature=0.2,
                         max_tokens=800 + extra_timeout,
                         timeout=max(Config.OPENROUTER_TIMEOUT, 55) + extra_timeout / 20)
        return text, None
    except requests.exceptions.Timeout:
        return None, {"ok": False, "error": "timeout", "message": "The web was slow to answer — try once more?"}
    except requests.exceptions.RequestException as exc:
        return None, {"ok": False, "error": "http", "message": f"Price radar hit a snag ({_http_detail(exc)})."}
    except Exception:  # noqa: BLE001
        return None, {"ok": False, "error": "unknown", "message": "Something snagged while researching. Try again?"}


def market_research(item, description="", unit="", qty=1, currency="EGP"):
    """Estimate a live market price for one procurement line item."""
    item = (item or "").strip()
    if not item:
        return {"ok": False, "error": "no_item", "message": "Type an item name first, then I'll go price-hunting."}

    currency = (currency or "EGP").strip().upper()
    desc = f" ({description.strip()})" if description else ""
    unit_hint = f" priced per {unit.strip()}" if unit else ""
    prompt = (
        f"Find the approximate current market price of: {item}{desc}{unit_hint}. "
        f"Answer in {currency}. Typical order quantity: about {qty}.\n\n"
        + _MARKET_SCHEMA_HINT
    )
    messages = [{"role": "system", "content": _MARKET_SYS}, {"role": "user", "content": prompt}]
    text, err = _web_call(messages)
    if err:
        return err

    result = _normalize_price(_extract_json(text) or {}, item, unit, currency)
    if not result:
        return {"ok": False, "error": "no_price",
                "message": ("Couldn't pin a solid number this time — the market's playing hard to get. "
                            "Try a more specific item name."),
                "raw": (text or "")[:500]}
    return result


def market_research_bulk(items, currency="EGP"):
    """Price several line items in ONE web-search call.

    `items` is a list of {item, description, unit, qty}. Returns
    {ok, currency, results:[{index, ...price fields...} | {index, ok:False, item}]}.
    """
    currency = (currency or "EGP").strip().upper()
    clean = []
    for i, it in enumerate(items or []):
        name = (it.get("item") or "").strip()
        if name:
            clean.append({"index": i, "item": name,
                          "description": (it.get("description") or "").strip(),
                          "unit": (it.get("unit") or "").strip(),
                          "qty": it.get("qty", 1)})
    if not clean:
        return {"ok": False, "error": "no_items", "message": "Add at least one item with a name first."}
    clean = clean[:15]  # bound cost

    lines = []
    for c in clean:
        d = f" ({c['description']})" if c["description"] else ""
        u = f" [per {c['unit']}]" if c["unit"] else ""
        lines.append(f'{c["index"]}. {c["item"]}{d}{u} — qty ~{c["qty"]}')
    prompt = (
        "Research the current approximate market price for EACH of these items and "
        f"answer in {currency}:\n" + "\n".join(lines) + "\n\n"
        "Respond ONLY with JSON: { \"results\": [ ITEM_OBJECT, ... ] } where each "
        "ITEM_OBJECT has this exact shape AND an extra integer field \"index\" "
        "matching the item number above:\n" + _ITEM_JSON_SHAPE + "\n"
        "Return one object per requested item. Numbers must be plain (no symbols/separators)."
    )
    messages = [{"role": "system", "content": _MARKET_SYS}, {"role": "user", "content": prompt}]
    text, err = _web_call(messages, extra_timeout=400)
    if err:
        return err

    parsed = _extract_json(text) or {}
    raw_results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(raw_results, list):
        raw_results = parsed if isinstance(parsed, list) else []

    by_index = {}
    for pos, rd in enumerate(raw_results):
        if not isinstance(rd, dict):
            continue
        idx = rd.get("index")
        try:
            idx = int(idx)
        except Exception:  # noqa: BLE001
            idx = clean[pos]["index"] if pos < len(clean) else pos
        by_index[idx] = rd

    results = []
    for c in clean:
        rd = by_index.get(c["index"])
        norm = _normalize_price(rd, c["item"], c["unit"], currency) if rd else None
        if norm:
            norm["index"] = c["index"]
            results.append(norm)
        else:
            results.append({"index": c["index"], "ok": False, "item": c["item"]})
    return {"ok": True, "currency": currency, "results": results}


# --------------------------------------------------------------------------
# Text polish (spelling / grammar / terminology autocorrect)
# --------------------------------------------------------------------------
_POLISH_SYS = (
    "You are a meticulous copy editor for a garment manufacturer's procurement "
    "system. Fix spelling, grammar, capitalization and spacing, and standardize "
    "textile/fashion/procurement terminology (correct fibre and material names, "
    "units, common abbreviations). Preserve the original meaning, language and "
    "intent — do NOT translate, do NOT add new facts or commentary, do NOT wrap "
    "the text in quotes. Return ONLY the corrected text."
)

_POLISH_HINT = {
    "title": "This is a short purchase-request title. Keep it concise (a few words), no ending period.",
    "item": "This is a product/item name. Keep it short, specific, no ending period.",
    "description": "This is a brief item description. Keep it short and clear.",
    "notes": "These are notes to approvers. Keep the tone professional and clear.",
    "generic": "Keep it natural and concise.",
}


def polish(text, kind="generic"):
    """Correct spelling/grammar/terminology of a short field value.

    Returns {ok, text} or {ok:False, ...} (with `text` echoing the input on failure).
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty", "text": ""}
    if not is_enabled():
        return {"ok": False, "offline": True, "text": text,
                "message": "Polish is offline — no OpenRouter API key configured."}

    kind = kind if kind in _POLISH_HINT else "generic"
    one_line = kind in ("title", "item")
    prompt = _POLISH_HINT[kind] + "\n\nText:\n" + text[:1500]
    messages = [{"role": "system", "content": _POLISH_SYS}, {"role": "user", "content": prompt}]
    try:
        out = _complete(messages, temperature=0.1, max_tokens=400)
    except requests.exceptions.RequestException:
        return {"ok": False, "error": "http", "text": text}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "unknown", "text": text}

    out = (out or "").strip().strip('"').strip("'").strip()
    if one_line:
        out = " ".join(out.split())  # collapse newlines/whitespace for single-line fields
    if not out:
        return {"ok": False, "error": "empty", "text": text}
    return {"ok": True, "text": out[:2000], "changed": out != text}


def _http_detail(exc) -> str:
    resp = getattr(exc, "response", None)
    if resp is not None:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message") or body.get("message")
            if msg:
                return f"{resp.status_code}: {str(msg)[:120]}"
        except Exception:  # noqa: BLE001
            pass
        return f"HTTP {resp.status_code}"
    return type(exc).__name__
