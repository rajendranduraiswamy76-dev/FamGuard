"""Clerk JWT verification, principal resolution and role-based access helpers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any, Protocol
from uuid import UUID

import asyncpg
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.database import get_pool, transaction
from app.repositories import (
    ConflictError,
    FamilyMember,
    FamilyRepo,
    MemberRepo,
    Role,
)

logger = logging.getLogger(__name__)

_LEEWAY_S = 5
_bearer = HTTPBearer(auto_error=False)


class AuthError(Exception):
    """Token is missing, malformed, expired or not trusted."""


class AuthUnavailableError(Exception):
    """Identity provider keys could not be fetched."""


class SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True, slots=True)
class Principal:
    clerk_user_id: str
    family_id: UUID
    member_id: UUID
    role: Role
    display_name: str
    email: str | None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    subject: str
    email: str | None
    name: str | None


class ClerkJWTVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        allowed_origins: list[str],
        key_provider: SigningKeyProvider,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._allowed_origins = {o.rstrip("/") for o in allowed_origins}
        self._keys = key_provider

    def verify(self, token: str) -> VerifiedToken:
        try:
            signing_key = self._keys.get_signing_key_from_jwt(token)
        except jwt.PyJWKClientConnectionError as exc:
            raise AuthUnavailableError("Unable to fetch signing keys") from exc
        except (jwt.PyJWKClientError, jwt.DecodeError) as exc:
            raise AuthError("Unknown signing key") from exc

        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=getattr(signing_key, "key", signing_key),
                algorithms=["RS256"],
                issuer=self._issuer,
                leeway=_LEEWAY_S,
                options={"require": ["exp", "iat", "iss", "sub"], "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("Token expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError("Invalid token") from exc

        azp = claims.get("azp")
        if azp is not None and str(azp).rstrip("/") not in self._allowed_origins:
            raise AuthError("Token issued for an untrusted origin")

        subject = claims["sub"]
        if not isinstance(subject, str) or not subject:
            raise AuthError("Invalid subject")
        email = claims.get("email")
        name = claims.get("name")
        return VerifiedToken(
            subject=subject,
            email=email.strip().lower() if isinstance(email, str) and email.strip() else None,
            name=name.strip()[:80] if isinstance(name, str) and name.strip() else None,
        )


@lru_cache(maxsize=1)
def _cached_verifier(jwks_url: str, issuer: str, origins: tuple[str, ...]) -> ClerkJWTVerifier:
    client = jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=3600, timeout=5)
    return ClerkJWTVerifier(issuer=issuer, allowed_origins=list(origins), key_provider=client)


def get_verifier(settings: Annotated[Settings, Depends(get_settings)]) -> ClerkJWTVerifier:
    if not settings.clerk_jwks_url or not settings.clerk_issuer:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication is not configured")
    return _cached_verifier(
        settings.clerk_jwks_url, settings.clerk_issuer, tuple(settings.allowed_origins)
    )


def get_db() -> asyncpg.Pool:
    return get_pool()


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def resolve_member(pool: asyncpg.Pool, token: VerifiedToken) -> FamilyMember:
    """Find the caller's member row, claiming an invite or creating a new family on first login."""
    members = MemberRepo(pool)
    member = await members.get_by_clerk_user_id(token.subject)
    if member is not None:
        return member

    if token.email:
        try:
            claimed = await members.claim_by_email(token.email, token.subject)
        except asyncpg.UniqueViolationError:
            claimed = None
        if claimed is not None:
            logger.info("member invite claimed", extra={"family_id": str(claimed.family_id)})
            return claimed

    display_name = token.name or "Family Admin"
    try:
        async with transaction(pool) as conn:
            family = await FamilyRepo(conn).create(f"{display_name}'s Family")
            member = await MemberRepo(conn).create(
                family.id,
                display_name=display_name,
                role="admin",
                email=token.email,
                clerk_user_id=token.subject,
            )
        logger.info("family bootstrapped", extra={"family_id": str(family.id)})
        return member
    except ConflictError:
        # A concurrent first request already created the family; its transaction won.
        existing = await members.get_by_clerk_user_id(token.subject)
        if existing is None:
            raise
        return existing


async def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    verifier: Annotated[ClerkJWTVerifier, Depends(get_verifier)],
    pool: Annotated[asyncpg.Pool, Depends(get_db)],
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise _unauthorized("Missing bearer token")
    try:
        # PyJWKClient performs blocking HTTP when its key cache is cold.
        token = await asyncio.to_thread(verifier.verify, credentials.credentials)
    except AuthUnavailableError as exc:
        logger.warning("jwks unavailable")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Authentication provider unavailable"
        ) from exc
    except AuthError as exc:
        raise _unauthorized(str(exc)) from exc

    member = await resolve_member(pool, token)
    principal = Principal(
        clerk_user_id=token.subject,
        family_id=member.family_id,
        member_id=member.id,
        role=member.role,
        display_name=member.display_name,
        email=member.email,
    )
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def require_admin(principal: CurrentPrincipal) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required")
    return principal


AdminPrincipal = Annotated[Principal, Depends(require_admin)]


async def assert_member_access(
    pool: asyncpg.Pool, principal: Principal, member_id: UUID
) -> FamilyMember:
    """404 if the member is outside the caller's family; 403 if a non-admin targets someone else."""
    member = await MemberRepo(pool).get(principal.family_id, member_id)
    if member is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Family member not found")
    if not principal.is_admin and member.id != principal.member_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed to access this member")
    return member
