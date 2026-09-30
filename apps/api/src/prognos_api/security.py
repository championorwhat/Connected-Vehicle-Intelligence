"""Passwords (argon2id) and access tokens (JWT, RS256, published as JWKS).

RS256 rather than HS256: services that only verify tokens (the dashboard's
backend-for-frontend, a future gateway) need the public key only, and an external
OIDC identity provider can replace `issue()` later without changing verification.
"""

from __future__ import annotations

import base64
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_hasher = PasswordHasher()  # argon2id with library defaults (OWASP-compliant parameters)
# Verifying against this when the account does not exist keeps login time constant,
# so response timing does not reveal which emails are registered.
_DUMMY_HASH = _hasher.hash("timing-equaliser")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _b64(n: int) -> str:
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str | None  # None = platform staff
    roles: frozenset[str]
    permissions: frozenset[str]
    token_id: str

    def can(self, permission: str) -> bool:
        return permission in self.permissions


class TokenService:
    def __init__(
        self, private_key_pem: str | None, *, key_id: str, issuer: str, audience: str,
        ttl_minutes: int,
    ) -> None:  # fmt: skip
        if private_key_pem:
            key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
            if not isinstance(key, rsa.RSAPrivateKey):
                raise ValueError("JWT signing key must be an RSA private key")
            self.ephemeral = False
        else:
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.ephemeral = True  # dev only: tokens become invalid on restart
        self.private_key = key
        self.public_key = key.public_key()
        self.key_id, self.issuer, self.audience = key_id, issuer, audience
        self.ttl_s = ttl_minutes * 60

    def issue(self, user_id: str, tenant_id: str | None, roles: list[str]) -> tuple[str, int]:
        now = int(time.time())
        claims = {
            "iss": self.issuer, "aud": self.audience, "sub": user_id, "tid": tenant_id,
            "roles": sorted(roles), "iat": now, "nbf": now, "exp": now + self.ttl_s,
            "jti": str(uuid.uuid4()),
        }  # fmt: skip
        token = jwt.encode(claims, self.private_key, algorithm="RS256",
                           headers={"kid": self.key_id})  # fmt: skip
        return token, self.ttl_s

    def verify(self, token: str) -> dict[str, Any]:
        try:
            claims: dict[str, Any] = jwt.decode(
                token, self.public_key, algorithms=["RS256"],  # never accept "none"/HS*
                audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "iat", "sub", "aud", "iss", "jti"]},
                leeway=5,
            )  # fmt: skip
        except jwt.PyJWTError as exc:
            raise InvalidToken(str(exc)) from exc
        return claims

    def jwks(self) -> dict[str, Any]:
        numbers = self.public_key.public_numbers()
        return {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.key_id,
                          "n": _b64(numbers.n), "e": _b64(numbers.e)}]}  # fmt: skip


def principal_from_claims(claims: dict[str, Any], policy: dict[str, frozenset[str]]) -> Principal:
    """Permissions come from the server-side role policy, never from the token itself."""
    roles = frozenset(claims.get("roles") or ())
    perms = frozenset(p for r in roles for p in policy.get(r, frozenset()))
    return Principal(claims["sub"], claims.get("tid"), roles, perms, claims["jti"])
