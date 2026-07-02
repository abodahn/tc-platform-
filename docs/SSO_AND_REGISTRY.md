# TC Platform — Single Sign-On (SSO) & Shared Registry

_Phase 4 (SSO) and Phase 5 (Shared Registry) of the unification roadmap._

The TC Platform is now the **single front door** to the whole ecosystem. A user
logs in once to the platform; opening any integrated system signs them in there
automatically — no second password — and the platform hosts one searchable
**People & Assets directory** whose every row opens the exact record in its
owning system.

---

## 1. What was built

| Capability | Where | Result |
|---|---|---|
| **SSO Identity Provider** | Platform (`/sso/launch/<key>`) | Mints a short-lived signed token for the logged-in user and hands off to the system. |
| **SSO Service Providers** | ITSM, Assets, Monitoring, CommandTrack (`/sso/login`) | Verify the token, auto-provision the user if new, and start their own session. |
| **Deep links** | `?next=<path>` end-to-end | The handoff can target a specific record, so a link opens the system *at that record*. |
| **Shared Registry** | Platform (`/registry`) | One directory of people + assets aggregated from every system, each row deep-linked via SSO. |

Fail-closed: if the shared secret is missing or weak, SSO simply turns off and
every module opens with its own normal login. Nothing breaks.

---

## 2. Architecture

```
                          ┌──────────────────────────────┐
   1. user logs in ─────► │        TC PLATFORM (IdP)      │
                          │  session: who you are         │
                          └───────────────┬──────────────┘
   2. user opens a module                 │  /sso/launch/itsm?next=/tickets/42
                                          │  → mint HS256 token (aud=itsm, exp=120s, jti)
                                          ▼
                          ┌──────────────────────────────┐
                          │  ITSM  /sso/login?token=…&next│  3. verify signature + aud + exp + jti
                          │  → find-or-create local user  │     establish ITSM session
                          │  → redirect to /tickets/42    │  4. user lands, logged in, on the record
                          └──────────────────────────────┘
```

The token is a standard **RFC 7519 JWT signed with HS256** — but the verifier
(`tc_sso.py`) is **pure Python standard library**, so the same file drops into
the Flask systems *and* the raw `http.server` CommandTrack app with no new
dependencies. It's also verifiable by any JWT library if a system prefers that.

### Token claims

```
iss  "tc-platform"      sub  "<username>"     aud  "<system key>"
iat / nbf / exp         jti  "<nonce>"        name / email / role
```

### Security properties

- **Signed (HS256)** — tamper-proof; the payload can't be edited (a forged role
  is rejected). Constant-time signature compare.
- **Audience-bound** — a token minted to open ITSM is rejected by Assets.
- **Short-lived** — ~120 s; it only has to survive the redirect handoff.
- **Single-use** — each token carries a nonce (`jti`); each SP keeps an
  in-memory nonce cache, so a captured token can't be replayed.
- **alg-confusion safe** — only `HS256` is accepted; `alg:none` is rejected.
- **Fail closed** — a missing/weak secret disables minting *and* verifying.
- **Least privilege on provisioning** — a brand-new user is created with a safe
  default role (below); an existing user keeps whatever role they already have.

---

## 3. Setup — how to switch it on

SSO activates only when **the same strong `TC_SSO_SECRET` is set on the platform
and on all four systems.** Until both sides have it, everything keeps working
with manual login (fail-closed), so you can roll it out in any order.

### 3.1 The shared secret (local systems)

A secret was generated at `D:\TC platform\.tc_sso_secret` (64 chars, gitignored).
`GO_ONLINE.ps1` loads it into the environment before starting the four systems,
so they pick it up automatically on the next start. To see the value:

```powershell
Get-Content "D:\TC platform\.tc_sso_secret"
```

### 3.2 The shared secret (cloud platform on Render)

Set the **same value** on Render so the deployed platform signs with it:

1. Render dashboard → your `tc-platform` service → **Environment**.
2. Add `TC_SSO_SECRET` = _(the value from the file above)_.
3. Save → Render redeploys. Done — SSO is now live.

`TC_SSO_ENABLED` defaults to **on**; set it to `false` on Render to disable
without removing the secret.

### 3.3 Activate the systems

Restart the four systems so they read the secret (or just run `GO_ONLINE.ps1`,
which now injects it). No code change is needed on your part.

### 3.4 Verify

- Log in to the platform, open **Service Desk** (or any system): you should land
  inside it already signed in.
- Open **Registry** in the sidebar: you should see people/assets, and clicking a
  row opens the owning system at that record.
- If a system still shows its own login, its `TC_SSO_SECRET` doesn't match the
  platform's — re-check both values.

---

## 4. Role mapping (new users only)

When SSO meets a user the target system has never seen, it creates them with a
sensible default. **Existing users are never touched** — their role is preserved.

| Platform role | ITSM | Assets | Monitoring | CommandTrack |
|---|---|---|---|---|
| super_admin | Super Admin | Super Admin | Admin | Admin |
| it_director / it_manager | IT Manager | IT Manager | Admin | Manager |
| service_desk_agent | Support Engineer | Employee/View Only | Operator | Member |
| asset_manager | Asset Manager | Asset Manager | Manager | Manager |
| monitoring_admin | Requester/Employee | Employee/View Only | Admin | Manager |
| finance_user | Requester/Employee | Finance | Viewer | Viewer |
| _(anything else)_ | Requester/Employee | Employee/View Only | Viewer | Member |

Tune these maps in each system's `/sso/login` block if you want different
defaults.

---

## 5. Shared Registry (Phase 5)

`/registry` aggregates `GET <base_url>/api/integration/registry` from every
integrated system (shape: `{"employees":[…], "assets":[…]}`) into one directory
and attaches a platform deep link to each row:

```
/sso/launch/<system>?next=<record path in that system>
```

- **Live search** across all fields (name, code, email, asset id, serial…).
- **Source chips** show which systems answered and how many records each gave.
- **Resilient** — a down or slow system is skipped; the page still renders.
- **Zero-config growth** — any system that adds the registry endpoint is picked
  up automatically. Currently the **Assets** system supplies both people and
  assets; add the same endpoint to ITSM/CommandTrack/Monitoring to include their
  records (and ticket/task deep links) with no platform change.

---

## 6. Extending

- **Add a registry source:** expose `GET /api/integration/registry` on the
  system returning `{"employees":[{code,name,email,title,department,status,deep_link}],
  "assets":[{asset_id,name,category,status,assignee,location,deep_link}]}`. Each
  `deep_link` is a path *within that system*.
- **Single logout (SLO):** not yet implemented. Because the systems set
  `SameSite=None; Secure` cookies in separate origins, a true server-side SLO
  needs each system's `/logout` to be pinged on platform logout. The pragmatic
  mitigation today is the short session lifetimes each system already enforces.
- **Rotate the secret:** change `.tc_sso_secret` and the Render env var to the
  same new value, then restart the systems. Old tokens (≤120 s) expire almost
  immediately.

---

## 7. Files

| File | Role |
|---|---|
| `app/services/sso.py` | Token library (mint/verify/nonce/safe-next). Canonical copy. |
| `app/routes/sso.py` | `/sso/launch/<key>` IdP route. |
| `app/services/registry.py` | Registry aggregation + search. |
| `app/routes/main.py` | `/registry` page + `/api/registry/search`. |
| `app/templates/registry.html` | Registry UI. |
| `<each system>/tc_sso.py` | Byte-identical verifier dropped into each system. |
| `<each system>` `/sso/login` | Service-Provider endpoint. |
| `.tc_sso_secret` | The shared secret (gitignored, local only). |

Tests: `tests/test_sso_token.py`, `tests/test_sso_launch.py`, `tests/test_registry.py`.
