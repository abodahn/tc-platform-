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
from concurrent.futures import ThreadPoolExecutor, wait
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


# The Command Center probes every integrated system before it renders. Serially,
# that is N x HEALTH_TIMEOUT — and the four legacy systems live on the factory
# network, which Render cannot reach AT ALL, so every probe pays the full
# timeout. Combined with fetch_overview's own serial 4s-per-system pass, opening
# the platform took 35-60+ seconds and looked like a hang. It was the single
# thing users meant by "the system does not open".
#
# Probe in PARALLEL under one deadline. A system that has not answered by then
# keeps its last-known status instead of holding the page hostage.
# The home page must NEVER wait on the factory network. 0.8s is enough for a
# system that is actually reachable to answer; anything slower is served from
# cache and refreshed in the background. A dashboard that renders instantly with
# a slightly stale tile beats a dashboard nobody can open.
_ALL_DEADLINE = 0.8


def check_all(system_rows, use_cache=True, deadline=_ALL_DEADLINE):
    rows = [r for r in system_rows if r["is_integrated"]]
    if not rows:
        return {}
    out = {}
    pool = ThreadPoolExecutor(max_workers=min(8, len(rows)))
    try:
        futures = {pool.submit(check_system, r, use_cache=use_cache): r for r in rows}
        done, pending = wait(futures, timeout=deadline)
        for f in done:
            row = futures[f]
            try:
                out[row["key"]] = f.result()
            except Exception:                      # noqa: BLE001
                out[row["key"]] = _unknown(row)
        for f in pending:
            # Do not cancel — let it finish into the cache for the next load.
            out[futures[f]["key"]] = _unknown(futures[f])
    finally:
        # shutdown(wait=False): exiting a `with ThreadPoolExecutor` block calls
        # shutdown(wait=True), which blocks for the stragglers anyway and threw
        # the deadline away. Let them finish in the background instead — they
        # populate the cache for the next page load.
        pool.shutdown(wait=False)
    return out


def _unknown(row):
    """A system that did not answer within the deadline. Shown as unknown rather
    than 'offline', because we did not actually learn that it is down."""
    return {"status": "unknown", "detail": "not checked in time", "ms": None,
            "key": row["key"]}
