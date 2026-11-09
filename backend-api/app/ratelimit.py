"""Per-user rate limits (Redis-backed in production, in-memory in tests/fake mode)."""

from __future__ import annotations

from typing import Final

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.config import get_settings

CHAT_LIMIT: Final = "30/minute"
UPLOAD_LIMIT: Final = "20/minute"
BULK_LIMIT: Final = "5/minute"


def rate_limit_key(request: Request) -> str:
    principal = getattr(request.state, "principal", None)
    if principal is not None:
        return f"user:{principal.clerk_user_id}"
    return f"ip:{get_remote_address(request)}"


def _storage_uri() -> str:
    settings = get_settings()
    if settings.env == "test" or settings.fake_externals:
        return "memory://"
    return settings.redis_url


limiter = Limiter(
    key_func=rate_limit_key,
    storage_uri=_storage_uri(),
    # A Redis outage must not take the API down; fall back to per-process counters.
    swallow_errors=True,
    in_memory_fallback_enabled=True,
    headers_enabled=False,
)
