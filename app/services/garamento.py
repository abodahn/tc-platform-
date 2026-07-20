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

    # Ground platform "how do I…" answers in the built-in user manual: inject the
    # topic index plus the sections most relevant to the latest user message.
    try:
        from app.services import garamento_kb as kb
        last_user = ""
        for m in reversed(turns):
            if m["role"] == "user":
                last_user = m["content"]
                break
        sys += "\n\n" + kb.context_for(last_user)
    except Exception:  # noqa: BLE001 — knowledge is best-effort, never break chat
        pass

    # Live platform status so Garamento can answer "what's open/pending" questions.
    try:
        from app.services import garamento_data
        snap = garamento_data.snapshot(user)
        if snap:
            sys += "\n\n" + snap
    except Exception:  # noqa: BLE001
        pass

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
    '  "sources": [ {"name": string, "price": string, "url": string} ],  // up to 4, best value first\n'
    '  "local_suppliers": [ {"name": string, "area": string, "approx_km": number, '
    '"price": string, "contact": string, "url": string} ],  // ONLY when a search area was given; [] otherwise\n'
    '  "search_area": string  // "<area> +<radius>km" when a search area was given; "" otherwise\n'
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

    # Local-market results (area-scoped research): physical suppliers near the
    # chosen area, each with an approximate distance inside the search radius.
    local = []
    for s in (data.get("local_suppliers") or [])[:5]:
        if isinstance(s, dict) and s.get("name"):
            local.append({
                "name": str(s.get("name"))[:90],
                "area": str(s.get("area") or "")[:90],
                "approx_km": _num(s.get("approx_km")),
                "price": str(s.get("price") or "")[:60],
                "contact": str(s.get("contact") or s.get("phone_or_where") or "")[:120],
                "url": str(s.get("url") or "")[:400],
            })

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
        "local_suppliers": local,
        "search_area": str(data.get("search_area") or "")[:120],
    }


def _local_block(area, radius_km, lang="en"):
    """Extra research instructions for area-scoped, local-market-first pricing.
    Returned string is appended to the user prompt when an area is chosen."""
    area = (area or "").strip()
    if not area:
        return ""
    try:
        r = max(0, min(30, int(float(radius_km or 30))))
    except (TypeError, ValueError):
        r = 30
    lang_line = {"ar": "Write summary/note fields in Arabic.",
                 "tr": "Write summary/note fields in Turkish."}.get(lang, "")
    return (
        f"\n\nLOCAL MARKET RESEARCH — MANDATORY:\n"
        f"The buyer is located in: {area} (Egypt unless the area says otherwise). "
        f"Search radius: {r} km around that area.\n"
        f"1) Search DEEPLY for physical suppliers, dealers, wholesalers and industrial "
        f"markets WITHIN {r} km of {area}: search in Arabic too "
        f"(e.g. 'موردين', 'اسعار', 'سوق', 'تجار جملة' + the item + the area name), "
        f"check local marketplaces (Dubizzle/OLX Egypt, Facebook marketplace listings), "
        f"business directories (yellowpages.com.eg, dalilak), and the industrial zones "
        f"near the area (e.g. El Obour industrial zone, 10th of Ramadan, Badr) when in range.\n"
        f"2) Fill `local_suppliers` with up to 5 REAL nearby options: name, area/district, "
        f"approx_km (your best estimate of distance from {area}, must be <= {r}), their "
        f"price if quoted, and contact (phone / address / where to find them) when public. "
        f"NEVER invent a supplier — if you cannot verify nearby options, return fewer or "
        f"an empty list and say so in `note`.\n"
        f"3) Local prices FIRST: when a local price within the radius exists, weight "
        f"price_est toward it; use national/online B2B prices only as the fallback and "
        f"comparison sources.\n"
        f"4) Set `search_area` to \"{area} +{r}km\". {lang_line}"
    )


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


def market_research(item, description="", unit="", qty=1, currency="EGP",
                    area="", radius_km=None, lang="en"):
    """Estimate a live market price for one procurement line item.

    When `area` is given the research is LOCAL-FIRST: physical suppliers within
    `radius_km` (0-30) of the chosen area are searched deeply (Arabic sources
    included) and returned in `local_suppliers`, with the estimate weighted
    toward verified local prices."""
    item = (item or "").strip()
    if not item:
        return {"ok": False, "error": "no_item", "message": "Type an item name first, then I'll go price-hunting."}

    currency = (currency or "EGP").strip().upper()
    desc = f" ({description.strip()})" if description else ""
    unit_hint = f" priced per {unit.strip()}" if unit else ""
    prompt = (
        f"Find the approximate current market price of: {item}{desc}{unit_hint}. "
        f"Answer in {currency}. Typical order quantity: about {qty}."
        + _local_block(area, radius_km, lang)
        + "\n\n" + _MARKET_SCHEMA_HINT
    )
    messages = [{"role": "system", "content": _MARKET_SYS}, {"role": "user", "content": prompt}]
    # area mode digs deeper (local + national + Arabic sources) -> bigger budget
    text, err = _web_call(messages, extra_timeout=600 if (area or "").strip() else 0)
    if err:
        return err

    result = _normalize_price(_extract_json(text) or {}, item, unit, currency)
    if not result:
        return {"ok": False, "error": "no_price",
                "message": ("Couldn't pin a solid number this time — the market's playing hard to get. "
                            "Try a more specific item name."),
                "raw": (text or "")[:500]}
    return result


def market_research_bulk(items, currency="EGP", area="", radius_km=None, lang="en"):
    """Price several line items in ONE web-search call.

    `items` is a list of {item, description, unit, qty}. Returns
    {ok, currency, results:[{index, ...price fields...} | {index, ok:False, item}]}.
    `area`/`radius_km` switch on the local-market-first deep research.
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
        f"answer in {currency}:\n" + "\n".join(lines)
        + _local_block(area, radius_km, lang) + "\n\n"
        "Respond ONLY with JSON: { \"results\": [ ITEM_OBJECT, ... ] } where each "
        "ITEM_OBJECT has this exact shape AND an extra integer field \"index\" "
        "matching the item number above:\n" + _ITEM_JSON_SHAPE + "\n"
        "Return one object per requested item. Numbers must be plain (no symbols/separators)."
    )
    messages = [{"role": "system", "content": _MARKET_SYS}, {"role": "user", "content": prompt}]
    text, err = _web_call(messages, extra_timeout=600 if (area or "").strip() else 400)
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


# --------------------------------------------------------------------------
# Auto-draft a purchase request from a plain-language description
# --------------------------------------------------------------------------
_DRAFT_SYS = (
    "You turn a plain-language purchasing need into a structured purchase request "
    "for a garment manufacturer. Extract each line item with a sensible unit and "
    "quantity. Do NOT invent prices — set unit_price to 0 unless a price is clearly "
    "stated. Map the department to one from the provided list when possible, else "
    "leave it blank. Keep the title short. Answer with JSON only."
)


def draft_pr(text, departments=None, units=None, currency="EGP"):
    """Draft a purchase request from free text. Returns {ok, draft:{...}}."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "empty", "message": "Describe what you need first."}
    if not is_enabled():
        return {"ok": False, "offline": True,
                "message": "Draft is offline — no OpenRouter API key configured."}
    dept_hint = ("Known departments: " + ", ".join(departments) + ".\n") if departments else ""
    unit_hint = ("Valid units (pick the closest): " + ", ".join(units) + ".\n") if units else ""
    prompt = (
        dept_hint + unit_hint +
        f"Currency: {currency}.\n\nNeed:\n{text[:2000]}\n\n"
        "Respond ONLY with JSON of this shape:\n"
        "{\n"
        '  "title": string,\n'
        '  "department": string,\n'
        '  "request_for": string,\n'
        '  "notes": string,\n'
        '  "items": [ {"item": string, "description": string, "unit": string, '
        '"qty": number, "unit_price": number} ]\n'
        "}"
    )
    messages = [{"role": "system", "content": _DRAFT_SYS}, {"role": "user", "content": prompt}]
    try:
        out = _complete(messages, temperature=0.2, max_tokens=800)
    except requests.exceptions.RequestException:
        return {"ok": False, "error": "http", "message": "Couldn't reach the drafting service. Try again."}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "unknown", "message": "Something snagged while drafting. Try again."}
    data = _extract_json(out) or {}
    items = []
    for it in (data.get("items") or [])[:30]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("item") or "").strip()
        if not name:
            continue
        items.append({
            "item": name[:120],
            "description": str(it.get("description") or "")[:200],
            "unit": str(it.get("unit") or "")[:40],
            "qty": _num(it.get("qty")) or 1,
            "unit_price": _num(it.get("unit_price")) or 0,
        })
    if not items:
        return {"ok": False, "error": "no_items",
                "message": "I couldn't spot any line items — try naming what to buy and how many."}
    dept = str(data.get("department") or "").strip()
    if departments and dept and dept not in departments:
        dept = next((d for d in departments if d.lower() == dept.lower()), "")
    return {"ok": True, "draft": {
        "title": str(data.get("title") or "")[:120],
        "department": dept,
        "request_for": str(data.get("request_for") or "")[:120],
        "notes": str(data.get("notes") or "")[:600],
        "items": items,
    }}


# --------------------------------------------------------------------------
# Maintenance ticket triage (AI-enhanced; complements the offline engine)
# --------------------------------------------------------------------------
_TRIAGE_SYS = (
    "You are a maintenance triage expert for a garment factory (sewing, cutting, "
    "finishing, embroidery machines and utilities). From a fault description, "
    "assess it. Be concise and practical. Answer with JSON only."
)


def triage_ticket(description, machine="", criticality="medium"):
    """Return {ok, priority, category, likely_causes[], suggested_parts[], summary}."""
    description = (description or "").strip()
    if not description:
        return {"ok": False, "error": "empty", "message": "Describe the fault first."}
    if not is_enabled():
        return {"ok": False, "offline": True,
                "message": "AI triage is offline — no OpenRouter API key configured."}
    ctx = f"Machine: {machine}. " if machine else ""
    ctx += f"Machine criticality: {criticality}."
    prompt = (
        ctx + "\n\nFault description:\n" + description[:1500] + "\n\n"
        "Respond ONLY with JSON:\n"
        "{\n"
        '  "priority": "low" | "medium" | "high" | "critical",\n'
        '  "category": string (e.g. mechanical, electrical, pneumatic, control, safety, other),\n'
        '  "likely_causes": [ string ],   // up to 4, most likely first\n'
        '  "suggested_parts": [ string ], // spares that may be needed, up to 5\n'
        '  "summary": string              // one practical sentence for the technician\n'
        "}"
    )
    messages = [{"role": "system", "content": _TRIAGE_SYS}, {"role": "user", "content": prompt}]
    try:
        out = _complete(messages, temperature=0.2, max_tokens=500)
    except requests.exceptions.RequestException:
        return {"ok": False, "error": "http", "message": "Couldn't reach the triage service."}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "unknown", "message": "Something snagged during triage."}
    d = _extract_json(out) or {}
    pri = str(d.get("priority") or "").lower()
    if pri not in ("low", "medium", "high", "critical"):
        pri = criticality if criticality in ("low", "medium", "high", "critical") else "medium"
    def _slist(v, n):
        return [str(x)[:80] for x in (v or []) if str(x).strip()][:n] if isinstance(v, list) else []
    return {
        "ok": True,
        "priority": pri,
        "category": str(d.get("category") or "other")[:40],
        "likely_causes": _slist(d.get("likely_causes"), 4),
        "suggested_parts": _slist(d.get("suggested_parts"), 5),
        "summary": str(d.get("summary") or "")[:280],
    }


# --------------------------------------------------------------------------
# BI — ask a dataset a natural-language question
# --------------------------------------------------------------------------
_BI_SYS = (
    "You are a careful data analyst. Answer the user's question using ONLY the "
    "dataset provided (columns, summary stats, and sample rows). If the dataset is "
    "a sample of a larger set, say your answer is based on the sample. Give exact "
    "numbers when the stats support them; never fabricate. Answer with JSON only."
)


def bi_ask(question, columns, rows, stats=None, total_rows=None):
    """Answer a NL question over a dataset. Returns {ok, answer, chart}."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "empty", "message": "Ask a question about the data."}
    if not is_enabled():
        return {"ok": False, "offline": True,
                "message": "AI answers are offline — no OpenRouter API key configured."}
    cols = [str(c) for c in (columns or [])][:40]
    sample = rows[:40] if isinstance(rows, list) else []
    n = total_rows if total_rows is not None else len(sample)
    lines = ["Columns: " + ", ".join(cols),
             f"Total rows: {n}" + (" (sample of first 40 shown)" if n > len(sample) else "")]
    if stats:
        try:
            lines.append("Column stats: " + json.dumps(stats)[:1500])
        except Exception:  # noqa: BLE001
            pass
    lines.append("Sample rows (JSON): " + json.dumps(sample)[:3500])
    prompt = (
        "\n".join(lines) + "\n\nQuestion: " + question[:400] + "\n\n"
        "Respond ONLY with JSON:\n"
        "{\n"
        '  "answer": string (concise, with numbers),\n'
        '  "chart": {"type": "bar"|"line"|"pie", "x": column, "y": column, "agg": "sum"|"avg"|"count"} | null\n'
        "}"
    )
    messages = [{"role": "system", "content": _BI_SYS}, {"role": "user", "content": prompt}]
    try:
        out = _complete(messages, temperature=0.1, max_tokens=600)
    except requests.exceptions.RequestException:
        return {"ok": False, "error": "http", "message": "Couldn't reach the analysis service."}
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "unknown", "message": "Something snagged during analysis."}
    d = _extract_json(out) or {}
    ans = str(d.get("answer") or "").strip()
    if not ans:
        return {"ok": False, "error": "no_answer", "message": "I couldn't answer that from the data."}
    chart = d.get("chart") if isinstance(d.get("chart"), dict) else None
    if chart:
        chart = {"type": str(chart.get("type") or "bar")[:10], "x": str(chart.get("x") or "")[:60],
                 "y": str(chart.get("y") or "")[:60], "agg": str(chart.get("agg") or "sum")[:10]}
        if chart["x"] not in cols or (chart["y"] and chart["y"] not in cols):
            chart = None
    return {"ok": True, "answer": ans, "chart": chart}


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
