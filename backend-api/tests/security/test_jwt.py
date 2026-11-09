from __future__ import annotations

import jwt
import pytest

from app.security import AuthError, AuthUnavailableError
from tests.helpers import (
    FakeKeyProvider,
    make_token,
    make_verifier,
    new_rsa_key,
)


def test_valid_token() -> None:
    token = make_verifier().verify(
        make_token("user_42", email=" Ann@Example.COM ", name="Ann")
    )
    assert token.subject == "user_42"
    assert token.email == "ann@example.com"
    assert token.name == "Ann"


def test_token_without_azp_is_accepted() -> None:
    assert make_verifier().verify(make_token(azp=None)).subject == "user_1"


@pytest.mark.parametrize(
    ("token_kwargs", "message"),
    [
        ({"expires_in": -60}, "expired"),
        ({"issuer": "https://evil.example"}, "Invalid token"),
        ({"azp": "https://evil.example"}, "untrusted origin"),
        ({"key": new_rsa_key()}, "Invalid token"),
        ({"drop": ("sub",)}, "Invalid token"),
        ({"drop": ("exp",)}, "Invalid token"),
    ],
)
def test_rejected_tokens(token_kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(AuthError, match=message):
        make_verifier().verify(make_token(**token_kwargs))  # type: ignore[arg-type]


def test_hs256_algorithm_confusion_rejected() -> None:
    forged = jwt.encode({"sub": "u", "iss": "x", "exp": 9999999999, "iat": 1}, "s" * 32,
                        algorithm="HS256")
    with pytest.raises(AuthError):
        make_verifier().verify(forged)


def test_garbage_token_rejected() -> None:
    with pytest.raises(AuthError):
        make_verifier().verify("not-a-jwt")


def test_jwks_outage_is_unavailable_not_unauthorized() -> None:
    provider = FakeKeyProvider(error=jwt.PyJWKClientConnectionError("down"))
    with pytest.raises(AuthUnavailableError):
        make_verifier(provider).verify(make_token())


def test_unknown_kid_is_unauthorized() -> None:
    provider = FakeKeyProvider(error=jwt.PyJWKClientError("kid not found"))
    with pytest.raises(AuthError):
        make_verifier(provider).verify(make_token())
