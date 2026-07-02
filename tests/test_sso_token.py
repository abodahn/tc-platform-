"""Unit tests for the SSO token library (app/services/sso.py).

These lock down the security-critical behaviour: a token only verifies with the
right secret, right audience, before it expires, once (replay), and never with a
tampered payload or a downgraded algorithm.
"""
import base64
import json
import time

import pytest

from app.services import sso

SECRET = "unit-test-secret-key-32-bytes-long!!"


def test_roundtrip_ok():
    tok = sso.make_token(SECRET, "ahmed", audience="itsm", name="Ahmed", email="a@x.com", role="super_admin")
    claims = sso.verify_token(SECRET, tok, audience="itsm")
    assert claims["sub"] == "ahmed"
    assert claims["aud"] == "itsm"
    assert claims["name"] == "Ahmed"
    assert claims["email"] == "a@x.com"
    assert claims["role"] == "super_admin"
    assert claims["iss"] == "tc-platform"
    assert "jti" in claims and "exp" in claims


def test_wrong_secret_rejected():
    tok = sso.make_token(SECRET, "ahmed", audience="itsm")
    with pytest.raises(sso.SSOError):
        sso.verify_token(SECRET + "x", tok, audience="itsm")


def test_wrong_audience_rejected():
    # token minted for ITSM must not open Assets
    tok = sso.make_token(SECRET, "ahmed", audience="itsm")
    with pytest.raises(sso.SSOError):
        sso.verify_token(SECRET, tok, audience="assets")


def test_expired_rejected():
    # ttl negative -> exp is already in the past; deterministic, no sleep/timing flake
    tok = sso.make_token(SECRET, "ahmed", audience="itsm", ttl=-5)
    with pytest.raises(sso.SSOError):
        sso.verify_token(SECRET, tok, audience="itsm", leeway=0)


def test_tampered_payload_rejected():
    tok = sso.make_token(SECRET, "ahmed", audience="itsm", role="normal_user")
    header, payload, sig = tok.split(".")
    body = json.loads(base64.urlsafe_b64decode(payload + "=="))
    body["role"] = "super_admin"          # privilege escalation attempt
    forged = base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode()
    tampered = f"{header}.{forged}.{sig}"
    with pytest.raises(sso.SSOError):
        sso.verify_token(SECRET, tampered, audience="itsm")


def test_alg_none_downgrade_rejected():
    # forge a header claiming alg=none with an empty signature
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "x", "aud": "itsm", "iss": "tc-platform",
                    "exp": int(time.time()) + 60}).encode()).rstrip(b"=").decode()
    forged = f"{header}.{payload}."
    with pytest.raises(sso.SSOError):
        sso.verify_token(SECRET, forged, audience="itsm")


def test_replay_rejected_with_nonce_cache():
    seen = sso.NonceCache()
    tok = sso.make_token(SECRET, "ahmed", audience="itsm")
    sso.verify_token(SECRET, tok, audience="itsm", seen=seen)     # first use ok
    with pytest.raises(sso.SSOError):
        sso.verify_token(SECRET, tok, audience="itsm", seen=seen)  # replay blocked


def test_weak_secret_refuses_to_mint():
    for weak in ("", "short", "change-me", "changeme"):
        with pytest.raises(sso.SSOError):
            sso.make_token(weak, "ahmed", audience="itsm")
    assert sso.is_secret_usable(SECRET) is True
    assert sso.is_secret_usable("change-me") is False
    assert sso.is_secret_usable("x" * 15) is False   # too short
    assert sso.is_secret_usable("x" * 16) is True


def test_malformed_tokens_rejected():
    for bad in ("", "not-a-token", "a.b", "a.b.c.d"):
        with pytest.raises(sso.SSOError):
            sso.verify_token(SECRET, bad, audience="itsm")
