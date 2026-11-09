from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app.security import ClerkJWTVerifier, Principal

ISSUER = "https://clerk.famguard.test"
ORIGIN = "http://localhost:3000"


def new_rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


SIGNING_KEY = new_rsa_key()


class _Key:
    def __init__(self, key: Any) -> None:
        self.key = key


class FakeKeyProvider:
    def __init__(self, private_key: rsa.RSAPrivateKey = SIGNING_KEY,
                 error: Exception | None = None) -> None:
        self._public = private_key.public_key()
        self._error = error

    def get_signing_key_from_jwt(self, token: str) -> _Key:
        if self._error is not None:
            raise self._error
        jwt.get_unverified_header(token)
        return _Key(self._public)


def make_verifier(provider: FakeKeyProvider | None = None) -> ClerkJWTVerifier:
    return ClerkJWTVerifier(
        issuer=ISSUER, allowed_origins=[ORIGIN], key_provider=provider or FakeKeyProvider()
    )


def make_token(
    sub: str = "user_1",
    *,
    key: rsa.RSAPrivateKey = SIGNING_KEY,
    issuer: str = ISSUER,
    azp: str | None = ORIGIN,
    expires_in: int = 300,
    email: str | None = None,
    name: str | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"sub": sub, "iss": issuer, "iat": now, "nbf": now,
                              "exp": now + expires_in}
    if azp is not None:
        claims["azp"] = azp
    if email is not None:
        claims["email"] = email
    if name is not None:
        claims["name"] = name
    for k in drop:
        claims.pop(k, None)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test"})


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_principal(role: str = "admin", **overrides: Any) -> Principal:
    values: dict[str, Any] = {
        "clerk_user_id": "user_x", "family_id": uuid4(), "member_id": uuid4(), "role": role,
        "display_name": "X", "email": None,
    }
    values.update(overrides)
    return Principal(**values)


TokenFactory = Callable[..., str]
