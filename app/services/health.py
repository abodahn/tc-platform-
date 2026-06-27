"""
TC Platform — health / status service.

Performs non-intrusive reachability checks against each integrated system.
A check is a plain HTTP GET against the configured health_url (falling back to
base_url). We never send credentials or mutate anything.

Status model:
  online   -> reachable, HTTP < 500 (login redirects count as online)
  warning  -> reachable but HTTP 5xx
  offline  -> connection refused / timeout / DNS failure
  unknown  -> no URL configured
"""
import time

import requests

from config import Config

# Simple in-process cache so the dashboard doesn't hammer every system on
# every page load. Keyed by system id.
_CACHE = {}
_CACHE_TTL = 15  # seconds


def check_one(base_url, health_url=None, timeout=None):
    timeout = timeout or Config.HEALTH_TIMEOUT
    target = health_url or base_url
    result = {"status": "unknown", "http": None, "response_ms": None, "error": None, "url": target}
    if not target:
        return result
    start = time.perf_counter()
    try:
        resp = requests.get(target, timeout=timeout, allow_redirects=True)
        elapsed = round((time.perf_counter() - start) * 1000)
        result["http"] = resp.status_code
        result["response_ms"] = elapsed
        result["status"] = "warning" if resp.status_code >= 500 else "online"
    except requests.exceptions.RequestException as exc:
        result["status"] = "offline"
        result["error"] = type(exc).__name__
    return result


def check_system(system_row, use_cache=True):
    sid = system_row["id"]
    now = time.time()
    if use_cache and sid in _CACHE and now - _CACHE[sid]["_ts"] < _CACHE_TTL:
        return _CACHE[sid]["data"]
    data = check_one(system_row["base_url"], system_row["health_url"])
    data["checked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _CACHE[sid] = {"_ts": now, "data": data}
    return data


def check_all(system_rows, use_cache=True):
    out = {}
    for row in system_rows:
        if not row["is_integrated"]:
            continue
        out[row["key"]] = check_system(row, use_cache=use_cache)
    return out
