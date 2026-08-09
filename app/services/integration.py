"""
TC Platform — consolidated business overview.

Pulls each integrated system's normalized KPI summary
(``GET <base_url>/api/integration/summary`` -> ``{"kpis":[{label,value,severity}]}``)
and caches it briefly, so the platform can show real assets / tickets / servers /
tasks KPIs from all four systems on one screen — not just health.
"""
from concurrent.futures import ThreadPoolExecutor, wait
import time

import requests

_CACHE = {"at": 0.0, "data": []}
_TTL = 30           # seconds to reuse a whole overview snapshot
_LAST_GOOD = {}     # key -> {"name", "kpis", "at"} — last successful per-system pull
_STALE_MAX = 900    # keep last-known-good up to 15 min through transient failures


# One deadline for the whole pass. Probing serially at 4s each, against systems
# on the factory network that Render cannot reach at all, added ~20s to every
# Command Center load — the other half of why "the system does not open".
_OVERVIEW_DEADLINE = 2.5


def _fetch_one(row, timeout, now):
    """Pull one system's KPI summary. Never raises: a system that is down, slow
    or missing the endpoint falls back to its last-known-good snapshot."""
    key = row["key"]
    name = row["name_en"] or key
    entry = {"key": key, "name": name, "online": False, "kpis": []}
    url = str(row["base_url"]).rstrip("/") + "/api/integration/summary"
    try:
        r = requests.get(url, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            kpis = data.get("kpis") if isinstance(data, dict) else data
            if isinstance(kpis, list):
                entry["online"] = True
                entry["kpis"] = [{
                    "label": str(k.get("label", ""))[:40],
                    "value": k.get("value"),
                    "severity": (str(k.get("severity") or "ok").strip().lower()),
                } for k in kpis[:6] if isinstance(k, dict)]
                _LAST_GOOD[key] = {"name": name, "kpis": entry["kpis"], "at": now}
    except Exception:                              # noqa: BLE001
        pass          # down / slow / endpoint missing -> last-known-good below
    # A transient tunnel timeout must NOT zero out the KPIs.
    if not entry["online"]:
        lg = _LAST_GOOD.get(key)
        if lg and (now - lg["at"] < _STALE_MAX):
            entry["kpis"] = lg["kpis"]
            entry["stale"] = True
    return entry


def fetch_overview(system_rows, timeout=4.0, force=False):
    now = time.time()
    if not force and _CACHE["data"] and (now - _CACHE["at"] < _TTL):
        return _CACHE["data"]

    rows = []
    for row in system_rows:
        try:
            if row["is_integrated"] and row["base_url"]:
                rows.append(row)
        except Exception:                          # noqa: BLE001
            continue
    if not rows:
        _CACHE["at"], _CACHE["data"] = now, []
        return []

    out = []
    pool = ThreadPoolExecutor(max_workers=min(8, len(rows)))
    try:
        futs = {pool.submit(_fetch_one, r, timeout, now): r for r in rows}
        done, pending = wait(futs, timeout=_OVERVIEW_DEADLINE)
        for f in done:
            try:
                out.append(f.result())
            except Exception:                      # noqa: BLE001
                out.append(_fetch_one.__wrapped__ if False else
                           {"key": futs[f]["key"], "name": futs[f]["key"],
                            "online": False, "kpis": []})
        for f in pending:
            # Not cancelled: let it finish into _LAST_GOOD for the next load.
            row = futs[f]
            lg = _LAST_GOOD.get(row["key"])
            out.append({"key": row["key"], "name": row["name_en"] or row["key"],
                        "online": False, "stale": bool(lg),
                        "kpis": (lg or {}).get("kpis", [])})
    finally:
        # shutdown(wait=False): exiting a `with ThreadPoolExecutor` block calls
        # shutdown(wait=True), which blocks for the stragglers anyway and threw
        # the deadline away. Let them finish in the background instead — they
        # populate the cache for the next page load.
        pool.shutdown(wait=False)

    _CACHE["at"] = now
    _CACHE["data"] = out
    return out
