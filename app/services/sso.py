"""
TC Platform — Single Sign-On (SSO) token library.

The platform is the Identity Provider (IdP). A user logs in once to the
platform; when they open an integrated system, the platform mints a short-lived,
signed token and hands it to that system, which trusts it and establishes its
own local session. No passwords ever travel between systems.

Design goals
------------
* **Zero third-party dependencies.** Pure Python standard library so the exact
  same file can be dropped into every integrated system — including the raw
  ``http.server`` CommandTrack app that has no Flask / PyJWT.
* **A real standard.** Tokens are RFC 7519 JWTs signed with HS256, so they can
  also be verified by PyJWT or any JWT library if a system prefers that later.
* **Fail closed.** If the shared secret is missing or weak, SSO is disabled and
  minting/verifying refuses rather than issuing an unsigned or guessable token.
* **Tight blast radius.** Tokens live ~2 minutes, are bound to a single target
  system (``aud``), and carry a nonce (``jti``) so a leaked token can be
  replayed at most once within its short lifetime (and not at all if the SP
  keeps a nonce cache).

Claims
------
    iss  "tc-platform"           issuer
    sub  "<username>"            platform username (stable identity)
    aud  "<system key>"          e.g. "itsm" — the ONLY system that may accept it
    iat  <unix seconds>          issued-at
    nbf  <unix seconds>          not-before (== iat)
    exp  <unix seconds>          expiry (iat + ttl)
    jti  "<hex>"                 unique nonce for replay detection
    name  "<display name>"       for provisioning the local user
    email "<email>"              for provisioning / mapping
    role  "<platform role>"      single primary role
    roles [ ... ]                full role list (optional)

This module is imported by the platform (the IdP). A byte-identical portable
copy named ``tc_sso.py`` is dropped into each integrated system (the SPs); keep
the two in sync — :func:`make_token` / :func:`verify_token` must match.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any, Iterable, Optional

__all__ = [
    "SSOError", "make_token", "verify_token", "decode_unverified",
    "NonceCache", "is_secret_usable", "safe_next_path",
]

# Secrets that must never be trusted (dev placeholders / empties).
_WEAK_SECRETS = {
    "", "change-me", "changeme", "secret", "tc-sso-dev-secret-change-me",
    "change-this-to-a-long-random-string-in-production",
}
_MIN_SECRET_LEN = 16


class SSOError(Exception):
    """Raised for any token problem: bad signature, expired, wrong audience,
    malformed, replayed, or an unusable secret. Never leak which one to the
    end user — log it, show a generic 'SSO sign-in failed'."""


# --- base64url helpers (no padding, per JWT) -----------------------------------
def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode(txt + pad)


def safe_next_path(nxt: Optional[str], default: str = "/") -> str:
    """Return ``nxt`` if it is a safe same-site path, else ``default``.

    Used for the SSO deep-link ``next`` parameter so it can't be turned into an
    open redirect. Accepts only paths that start with a single ``/`` and carry
    no scheme, no protocol-relative ``//``, and no backslash.
    """
    if not nxt:
        return default
    n = str(nxt).strip()
    if n.startswith("/") and not n.startswith("//") and "://" not in n and "\\" not in n:
        return n
    return default


def is_secret_usable(secret: Optional[str]) -> bool:
    """True only if the shared secret is present and strong enough to trust.
    Used to fail closed: when this returns False, SSO is effectively disabled."""
    if not secret:
        return False
    s = secret.strip()
    return len(s) >= _MIN_SECRET_LEN and s.lower() not in _WEAK_SECRETS


def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def make_token(
    secret: str,
    subject: str,
    *,
    audience: str,
    name: str = "",
    email: str = "",
    role: str = "",
    roles: Optional[Iterable[str]] = None,
    ttl: int = 120,
    issuer: str = "tc-platform",
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """Mint a signed SSO token for ``subject`` targeting one system (``audience``).

    Raises :class:`SSOError` if the secret is unusable (fail closed).
    """
    if not is_secret_usable(secret):
        raise SSOError("SSO secret is missing or too weak to sign tokens")
    if not subject:
        raise SSOError("cannot mint a token without a subject (username)")
    if not audience:
        raise SSOError("cannot mint a token without an audience (system key)")

    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "iss": issuer,
        "sub": str(subject),
        "aud": str(audience),
        "iat": now,
        "nbf": now,
        "exp": now + int(ttl),
        "jti": secrets.token_hex(16),
        "name": name or "",
        "email": email or "",
        "role": role or "",
    }
    if roles is not None:
        payload["roles"] = list(roles)
    if extra:
        # Never let extra claims silently override the security-critical ones.
        for k, v in extra.items():
            payload.setdefault(k, v)

    segs = [
        _b64u_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64u_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = ".".join(segs).encode("ascii")
    segs.append(_b64u_encode(_sign(signing_input, secret)))
    return ".".join(segs)


def decode_unverified(token: str) -> dict[str, Any]:
    """Return the payload WITHOUT checking the signature. For logging/debug only
    — never trust these values for an auth decision."""
    try:
        _, body, _ = token.split(".")
        return json.loads(_b64u_decode(body))
    except Exception as exc:  # noqa: BLE001
        raise SSOError("malformed token") from exc


def verify_token(
    secret: str,
    token: str,
    *,
    audience: Optional[str] = None,
    issuer: str = "tc-platform",
    leeway: int = 30,
    seen: "Optional[NonceCache]" = None,
) -> dict[str, Any]:
    """Verify signature + claims and return the payload, or raise :class:`SSOError`.

    * ``audience`` — if given, the token's ``aud`` must match exactly (binds the
      token to this one system; rejects a token minted for a different system).
    * ``leeway`` — clock-skew tolerance in seconds for exp/nbf.
    * ``seen`` — optional :class:`NonceCache`; if provided, a token whose ``jti``
      was already accepted is rejected (single-use / replay protection).
    """
    if not is_secret_usable(secret):
        raise SSOError("SSO secret is missing or too weak to verify tokens")
    if not token or token.count(".") != 2:
        raise SSOError("malformed token")

    header_seg, payload_seg, sig_seg = token.split(".")
    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")

    # 1) signature — constant-time compare
    try:
        expected = _sign(signing_input, secret)
        given = _b64u_decode(sig_seg)
    except Exception as exc:  # noqa: BLE001
        raise SSOError("bad token encoding") from exc
    if not hmac.compare_digest(expected, given):
        raise SSOError("bad signature")

    # 2) header alg — reject anything but HS256 (defends against alg confusion)
    try:
        header = json.loads(_b64u_decode(header_seg))
    except Exception as exc:  # noqa: BLE001
        raise SSOError("bad header") from exc
    if header.get("alg") != "HS256":
        raise SSOError("unexpected signing algorithm")

    # 3) payload + claim checks
    try:
        payload = json.loads(_b64u_decode(payload_seg))
    except Exception as exc:  # noqa: BLE001
        raise SSOError("bad payload") from exc

    now = int(time.time())
    if issuer and payload.get("iss") != issuer:
        raise SSOError("wrong issuer")
    if audience is not None and payload.get("aud") != audience:
        raise SSOError("wrong audience")
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or now > exp + leeway:
        raise SSOError("token expired")
    nbf = payload.get("nbf", payload.get("iat", now))
    if isinstance(nbf, (int, float)) and now + leeway < nbf:
        raise SSOError("token not yet valid")
    if not payload.get("sub"):
        raise SSOError("token has no subject")

    # 4) replay protection (optional)
    if seen is not None:
        jti = payload.get("jti")
        if not jti or not seen.add(str(jti), exp):
            raise SSOError("token already used (replay)")

    return payload


class NonceCache:
    """A tiny in-process store of accepted token nonces for replay protection.

    Bounded and self-pruning: entries are dropped once their token's ``exp`` has
    passed (a replayed token would fail the exp check anyway). Per-process only —
    good enough because tokens live ~2 minutes and the launch handoff is a single
    request; a multi-process SP simply narrows, not widens, the replay window.
    """

    def __init__(self, max_entries: int = 4096) -> None:
        self._exp_by_jti: dict[str, float] = {}
        self._max = max_entries

    def add(self, jti: str, exp: float) -> bool:
        """Record ``jti``. Return True if new, False if it was already seen."""
        now = time.time()
        if len(self._exp_by_jti) >= self._max:
            self._prune(now)
        if jti in self._exp_by_jti and self._exp_by_jti[jti] >= now:
            return False
        self._exp_by_jti[jti] = float(exp)
        return True

    def _prune(self, now: float) -> None:
        dead = [j for j, e in self._exp_by_jti.items() if e < now]
        for j in dead:
            self._exp_by_jti.pop(j, None)
        # If everything is still live (unlikely), drop the oldest to stay bounded.
        if len(self._exp_by_jti) >= self._max:
            oldest = sorted(self._exp_by_jti.items(), key=lambda kv: kv[1])
            for j, _ in oldest[: self._max // 4]:
                self._exp_by_jti.pop(j, None)
