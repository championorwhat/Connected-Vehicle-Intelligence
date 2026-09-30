"""Known JWT attacks must all fail against TokenService.verify (no Docker needed).

Each case forges or alters a token the way an attacker would; every one must raise
InvalidToken. See docs/security/threat-model.md (Spoofing).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from prognos_api.security import InvalidToken, TokenService


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def forge(header: dict[str, Any], claims: dict[str, Any], signature: bytes = b"") -> str:
    head = b64(json.dumps(header).encode())
    body = b64(json.dumps(claims).encode())
    return f"{head}.{body}.{b64(signature)}"


@pytest.fixture(scope="module")
def svc() -> TokenService:
    return TokenService(None, key_id="k1", issuer="prognos", audience="prognos-api",
                        ttl_minutes=15)  # fmt: skip


def claims(svc: TokenService, **overrides: Any) -> dict[str, Any]:
    now = int(time.time())
    base = {"iss": svc.issuer, "aud": svc.audience, "sub": "attacker", "tid": "victim",
            "roles": ["fleet_manager"], "iat": now, "nbf": now, "exp": now + 600,
            "jti": "j"}  # fmt: skip
    return base | overrides


def test_alg_none_is_rejected(svc: TokenService) -> None:
    for alg in ("none", "None", "NONE"):
        with pytest.raises(InvalidToken):
            svc.verify(forge({"alg": alg, "typ": "JWT"}, claims(svc)))


def test_hs256_signed_with_the_public_key_is_rejected(svc: TokenService) -> None:
    """Key confusion: the public key is public, so an HMAC made with it proves nothing."""
    pem = svc.public_key.public_bytes(serialization.Encoding.PEM,
                                      serialization.PublicFormat.SubjectPublicKeyInfo)  # fmt: skip
    header, body = {"alg": "HS256", "typ": "JWT", "kid": "k1"}, claims(svc)
    unsigned = forge(header, body).rsplit(".", 1)[0]
    mac = hmac.new(pem, unsigned.encode(), hashlib.sha256).digest()
    with pytest.raises(InvalidToken):
        svc.verify(f"{unsigned}.{b64(mac)}")


def test_changing_the_payload_breaks_the_signature(svc: TokenService) -> None:
    token, _ = svc.issue("u1", "tenant-a", ["technician"])
    head, _, sig = token.split(".")
    escalated = claims(svc, sub="u1", tid="tenant-b", roles=["fleet_manager", "dpo"])
    with pytest.raises(InvalidToken):
        svc.verify(f"{head}.{b64(json.dumps(escalated).encode())}.{sig}")


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"aud": "some-other-api"}, "token minted for another service"),
        ({"iss": "https://evil.example"}, "token from another issuer"),
        ({"exp": int(time.time()) - 60}, "expired"),
        ({"nbf": int(time.time()) + 600}, "not valid yet"),
    ],
)
def test_wrong_audience_issuer_or_time_is_rejected(
    svc: TokenService, change: dict[str, Any], why: str
) -> None:
    token = jwt.encode(claims(svc, **change), svc.private_key, algorithm="RS256")
    with pytest.raises(InvalidToken):
        svc.verify(token)


@pytest.mark.parametrize("missing", ["exp", "sub", "jti", "aud", "iss", "iat"])
def test_tokens_missing_a_required_claim_are_rejected(svc: TokenService, missing: str) -> None:
    body = claims(svc)
    del body[missing]
    with pytest.raises(InvalidToken):
        svc.verify(jwt.encode(body, svc.private_key, algorithm="RS256"))


def test_garbage_is_rejected_without_crashing(svc: TokenService) -> None:
    for junk in ("", "a.b", "a.b.c", "....", "eyJhbGciOiJSUzI1NiJ9." * 3):
        with pytest.raises(InvalidToken):
            svc.verify(junk)
