"""Verify a Clerk session token with FamGuard's backend JWT verifier."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend-api"))

import jwt
from app.security import (
    AuthError,
    AuthUnavailableError,
    ClerkJWTVerifier,
    VerifiedToken,
)


def verify_token(
    token: str, *, jwks_url: str, issuer: str, allowed_origins: list[str]
) -> VerifiedToken:
    verifier = ClerkJWTVerifier(
        issuer=issuer,
        allowed_origins=allowed_origins,
        key_provider=jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5),
    )
    return verifier.verify(token)


def main() -> int:
    jwks_url = os.environ.get("CLERK_JWKS_URL", "").strip()
    issuer = os.environ.get("CLERK_ISSUER", "").strip()
    origins = [
        origin.strip().rstrip("/")
        for origin in os.environ.get("ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if not jwks_url or not issuer or not origins:
        print(
            "CLERK_JWKS_URL, CLERK_ISSUER, and ALLOWED_ORIGINS must be set",
            file=sys.stderr,
        )
        return 2

    token = getpass.getpass("Paste a short-lived Clerk session token (input hidden): ").strip()
    if not token:
        print("A Clerk session token is required", file=sys.stderr)
        return 2

    try:
        verified = verify_token(
            token,
            jwks_url=jwks_url,
            issuer=issuer,
            allowed_origins=origins,
        )
    except AuthUnavailableError:
        print(
            "Unable to fetch Clerk signing keys; check CLERK_JWKS_URL and connectivity",
            file=sys.stderr,
        )
        return 1
    except AuthError as exc:
        print(f"Clerk token verification failed: {exc}", file=sys.stderr)
        return 1

    missing = [
        claim for claim, value in (("email", verified.email), ("name", verified.name)) if not value
    ]
    if missing:
        print(
            f"Token verified, but required custom claims are missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1
    print(f"Token verified for {verified.subject}; email and name claims are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
