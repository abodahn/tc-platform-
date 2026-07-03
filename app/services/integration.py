"""
TC Platform — consolidated business overview.

Pulls each integrated system's normalized KPI summary
(``GET <base_url>/api/integration/summary`` -> ``{"kpis":[{label,value,severity}]}``)
and caches it briefly, so the platform can show real assets / tickets / servers /
tasks KPIs from all four systems on one screen — not just health.
"""
import time

import requests

_CACHE = {"at": 0.0, "data": []}
_TTL = 30           # seconds to reuse a whole overview snapshot
_LAST_GOOD = {}     # key -> {"name", "kpis", "at"} — last successful per-system pull
_STALE_MAX = 900    # keep last-known-good up to 15 min through transient failures


def fetch_overview(system_rows, timeout=4.0, force=False):
    now = time.time()
    if not force and _CACHE["data"] and (now - _CACHE["at"] < _TTL):
        return _CACHE["data"]

    out = []
    for row in system_rows:
        try:
            if not row["is_integrated"] or not row["base_url"]:
                continue
        except Exception:
            continue
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
        except Exception:
            pass  # system down / slow / endpoint missing -> handled below
        # Last-known-good: a transient tunnel timeout must NOT zero out the KPIs.
        # If this pull failed but we have a recent good snapshot, serve it (flagged
        # stale) so the Command Center stays accurate instead of dropping to 0.
        if not entry["online"]:
            lg = _LAST_GOOD.get(key)
            if lg and (now - lg["at"] < _STALE_MAX):
                entry["kpis"] = lg["kpis"]
                entry["stale"] = True
        out.append(entry)

    _CACHE["at"] = now
    _CACHE["data"] = out
    return out
