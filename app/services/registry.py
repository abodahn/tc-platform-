"""
TC Platform — shared employee & asset registry (Phase 5).

Aggregates the canonical people and asset records that the integrated systems
own (``GET <base_url>/api/integration/registry`` ->
``{"employees":[...], "assets":[...]}``) into one searchable directory, and
attaches a **platform deep link** to every record:

    /sso/launch/<system_key>?next=<record path in that system>

Clicking a result therefore opens the owning system *at that exact record*,
signing the user in on the way (Single Sign-On, Phase 4). Any system that
exposes the registry endpoint is picked up automatically — no code change here
— so the directory grows as more systems add the endpoint.

Cached briefly; every system is polled independently and a slow/down/missing
system is simply skipped (never crashes the page).
"""
from __future__ import annotations

import time
import urllib.parse

import requests

_CACHE: dict[str, object] = {"at": 0.0, "data": None}
_TTL = 60  # seconds


def _deep_link(system_key: str, path: str) -> str:
    """Platform URL that opens `path` inside `system_key` via SSO."""
    path = (path or "").strip()
    if not (path.startswith("/") and not path.startswith("//") and "://" not in path):
        return f"/sso/launch/{urllib.parse.quote(system_key)}"
    return f"/sso/launch/{urllib.parse.quote(system_key)}?next={urllib.parse.quote(path)}"


def fetch_registry(system_rows, timeout: float = 3.0, force: bool = False) -> dict:
    """Return {'people': [...], 'assets': [...], 'sources': [...]} aggregated
    across all integrated systems that expose /api/integration/registry."""
    now = time.time()
    cached = _CACHE.get("data")
    if not force and cached and (now - float(_CACHE["at"]) < _TTL):
        return cached  # type: ignore[return-value]

    people: list[dict] = []
    assets: list[dict] = []
    sources: list[dict] = []

    for row in system_rows:
        try:
            if not row["is_integrated"] or not row["base_url"]:
                continue
        except Exception:
            continue
        key = row["key"]
        name = row["name_en"] or key
        src = {"key": key, "name": name, "online": False, "people": 0, "assets": 0}
        url = str(row["base_url"]).rstrip("/") + "/api/integration/registry"
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code == 200:
                data = r.json() if r.content else {}
                emps = data.get("employees") or data.get("people") or []
                asts = data.get("assets") or []
                if isinstance(emps, list) or isinstance(asts, list):
                    src["online"] = True
                for e in (emps if isinstance(emps, list) else []):
                    if not isinstance(e, dict):
                        continue
                    people.append({
                        "system_key": key,
                        "system_name": name,
                        "name": str(e.get("name") or "")[:120],
                        "code": str(e.get("code") or "")[:60],
                        "email": str(e.get("email") or "")[:120],
                        "phone": str(e.get("phone") or "")[:40],
                        "title": str(e.get("title") or "")[:80],
                        "department": str(e.get("department") or "")[:80],
                        "status": str(e.get("status") or "")[:40],
                        "link": _deep_link(key, str(e.get("deep_link") or "")),
                    })
                    src["people"] += 1
                for a in (asts if isinstance(asts, list) else []):
                    if not isinstance(a, dict):
                        continue
                    assets.append({
                        "system_key": key,
                        "system_name": name,
                        "asset_id": str(a.get("asset_id") or a.get("id") or "")[:60],
                        "name": str(a.get("name") or "")[:120],
                        "category": str(a.get("category") or "")[:60],
                        "type": str(a.get("type") or "")[:60],
                        "serial_number": str(a.get("serial_number") or "")[:80],
                        "status": str(a.get("status") or "")[:40],
                        "assignee": str(a.get("assignee") or "")[:120],
                        "location": str(a.get("location") or "")[:80],
                        "link": _deep_link(key, str(a.get("deep_link") or "")),
                    })
                    src["assets"] += 1
        except Exception:
            pass  # system down / endpoint absent -> skip, page still renders
        sources.append(src)

    people.sort(key=lambda p: p["name"].lower())
    assets.sort(key=lambda a: a["name"].lower())
    result = {"people": people, "assets": assets, "sources": sources}
    _CACHE["at"] = now
    _CACHE["data"] = result
    return result


def search_registry(system_rows, query: str, limit: int = 200) -> dict:
    """Case-insensitive substring search across people + assets."""
    reg = fetch_registry(system_rows)
    q = (query or "").strip().lower()
    if not q:
        return {"people": reg["people"][:limit], "assets": reg["assets"][:limit],
                "sources": reg["sources"]}

    def _match(d: dict) -> bool:
        return any(q in str(v).lower() for v in d.values())

    return {
        "people": [p for p in reg["people"] if _match(p)][:limit],
        "assets": [a for a in reg["assets"] if _match(a)][:limit],
        "sources": reg["sources"],
    }
