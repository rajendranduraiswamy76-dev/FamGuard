"""Postgres schema, connection pool lifecycle and migration entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Final

import asyncpg
from asyncpg.pool import PoolConnectionProxy

logger = logging.getLogger(__name__)

BASELINE_CATEGORIES: Final[tuple[tuple[str, str], ...]] = (
    ("identitycards", "Identity Cards"),
    ("certificate", "Certificates"),
    ("income", "Income"),
    ("expense", "Expenses"),
    ("medical", "Medical"),
    ("photo", "Photos"),
    ("miscellaneous", "Miscellaneous"),
)

CATEGORY_SLUG_PATTERN: Final = r"^[a-z0-9]+(-[a-z0-9]+)*$"

DDL_FAMILIES: Final = """
CREATE TABLE IF NOT EXISTS families (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    currency    CHAR(3) NOT NULL DEFAULT 'USD',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

DDL_FAMILY_MEMBERS: Final = """
CREATE TABLE IF NOT EXISTS family_members (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id      UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    clerk_user_id  TEXT UNIQUE,
    display_name   TEXT NOT NULL CHECK (length(display_name) BETWEEN 1 AND 80),
    email          TEXT,
    role           TEXT NOT NULL CHECK (role IN ('admin', 'member', 'dependent')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (family_id, display_name),
    UNIQUE (family_id, id)
)"""

DDL_VAULT_CATEGORIES: Final = f"""
CREATE TABLE IF NOT EXISTS vault_categories (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id              UUID REFERENCES families(id) ON DELETE CASCADE,
    category_slug          TEXT NOT NULL CHECK (
                               category_slug ~ '{CATEGORY_SLUG_PATTERN}'
                               AND length(category_slug) <= 48
                           ),
    category_display_name  TEXT NOT NULL CHECK (length(category_display_name) BETWEEN 1 AND 80),
    is_baseline            BOOLEAN NOT NULL DEFAULT FALSE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (is_baseline = (family_id IS NULL))
)"""

DDL_VAULT_CATEGORIES_UNIQUE: Final = """
CREATE UNIQUE INDEX IF NOT EXISTS ux_vault_categories_family_slug
    ON vault_categories (
        COALESCE(family_id, '00000000-0000-0000-0000-000000000000'::uuid),
        category_slug
    )"""

DDL_DOCUMENTS: Final = """
CREATE TABLE IF NOT EXISTS documents (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    family_id          UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    family_member_id   UUID NOT NULL,
    thread_id          TEXT UNIQUE,
    category_slug      TEXT NOT NULL,
    document_name      TEXT NOT NULL,
    original_filename  TEXT,
    r2_object_key      TEXT,
    sha256             CHAR(64),
    expiration_date    DATE,
    requires_alert     BOOLEAN NOT NULL DEFAULT FALSE,
    financial_value    NUMERIC(14, 2),
    chunk_count        INT NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    source             TEXT NOT NULL CHECK (source IN ('upload', 'bulk', 'chat')),
    status             TEXT NOT NULL CHECK (
                           status IN (
                               'processing', 'review_required', 'completed', 'failed', 'rejected'
                           )
                       ),
    error              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (family_id, sha256),
    FOREIGN KEY (family_id, family_member_id)
        REFERENCES family_members (family_id, id) ON DELETE CASCADE
)"""

DDL_DOCUMENTS_EXPIRY_INDEX: Final = """
CREATE INDEX IF NOT EXISTS ix_documents_expiry
    ON documents (expiration_date) WHERE requires_alert"""

DDL_DOCUMENTS_FAMILY_INDEX: Final = """
CREATE INDEX IF NOT EXISTS ix_documents_family_member
    ON documents (family_id, family_member_id)"""

DDL_PENDING_REVIEWS: Final = """
CREATE TABLE IF NOT EXISTS pending_reviews (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    thread_id           TEXT NOT NULL UNIQUE,
    family_id           UUID NOT NULL REFERENCES families(id) ON DELETE CASCADE,
    family_member_id    UUID NOT NULL,
    document_id         UUID REFERENCES documents(id) ON DELETE CASCADE,
    original_filename   TEXT NOT NULL,
    raw_text            TEXT NOT NULL,
    suggested_metadata  JSONB NOT NULL,
    confidence_score    REAL NOT NULL CHECK (confidence_score BETWEEN 0 AND 1),
    review_reasons      TEXT[] NOT NULL DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'approved', 'rejected')),
    resolved_by         UUID REFERENCES family_members(id) ON DELETE SET NULL,
    resolved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (family_id, family_member_id)
        REFERENCES family_members (family_id, id) ON DELETE CASCADE
)"""

DDL_PENDING_REVIEWS_OPEN_INDEX: Final = """
CREATE INDEX IF NOT EXISTS ix_pending_reviews_open
    ON pending_reviews (family_id, created_at) WHERE status = 'open'"""

DDL_ALERT_LOG: Final = """
CREATE TABLE IF NOT EXISTS alert_log (
    id           BIGSERIAL PRIMARY KEY,
    document_id  UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    days_before  SMALLINT NOT NULL CHECK (days_before IN (30, 7, 1)),
    recipient    TEXT NOT NULL,
    resend_id    TEXT,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, days_before, recipient)
)"""

SCHEMA_STATEMENTS: Final[tuple[str, ...]] = (
    DDL_FAMILIES,
    DDL_FAMILY_MEMBERS,
    """ALTER TABLE family_members ADD COLUMN IF NOT EXISTS relationship TEXT
       CHECK (relationship IN ('spouse', 'child', 'others'))""",
    DDL_VAULT_CATEGORIES,
    DDL_VAULT_CATEGORIES_UNIQUE,
    DDL_DOCUMENTS,
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS requested_category TEXT""",
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS document_details TEXT NOT NULL DEFAULT ''
       CHECK (length(document_details) <= 2000)""",
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS index_version TEXT""",
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS currency TEXT""",
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS financial_subcategory TEXT""",
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS transaction_date DATE""",
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS payment_due_date DATE""",
    """ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_paid BOOLEAN NOT NULL DEFAULT TRUE""",
    DDL_DOCUMENTS_EXPIRY_INDEX,
    DDL_DOCUMENTS_FAMILY_INDEX,
    DDL_PENDING_REVIEWS,
    DDL_PENDING_REVIEWS_OPEN_INDEX,
    DDL_ALERT_LOG,
)

SEED_BASELINE_CATEGORIES: Final = """
INSERT INTO vault_categories (family_id, category_slug, category_display_name, is_baseline)
SELECT NULL, slug, display_name, TRUE
FROM unnest($1::text[], $2::text[]) AS seed(slug, display_name)
ON CONFLICT DO NOTHING"""

# Serializes concurrent migrations from multiple containers.
_MIGRATION_LOCK_KEY: Final = 7_461_002_391

Connection = asyncpg.Connection | PoolConnectionProxy
Executor = asyncpg.Pool | Connection


class DatabaseError(Exception):
    """Base class for database-layer errors."""


class DatabaseUnavailableError(DatabaseError):
    """Raised when Postgres cannot be reached after all retries."""


class PoolNotInitializedError(DatabaseError):
    """Raised when the pool is used before init_pool()."""


_TRANSIENT_CONNECT_ERRORS: Final[tuple[type[BaseException], ...]] = (
    OSError,
    TimeoutError,
    asyncpg.CannotConnectNowError,
    asyncpg.TooManyConnectionsError,
    asyncpg.ConnectionDoesNotExistError,
)


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def create_pool_with_retry(
    dsn: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
    max_attempts: int = 5,
    base_delay_s: float = 0.5,
    max_delay_s: float = 10.0,
    server_settings: dict[str, str] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> asyncpg.Pool:
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            pool = await asyncpg.create_pool(
                dsn,
                min_size=min_size,
                max_size=max_size,
                # Supabase poolers can break server-side prepared statement caching.
                statement_cache_size=0,
                command_timeout=30,
                init=_init_connection,
                server_settings=server_settings,
            )
        except _TRANSIENT_CONNECT_ERRORS as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            delay = min(base_delay_s * 2 ** (attempt - 1), max_delay_s)
            logger.warning(
                "postgres connection failed, retrying",
                extra={"attempt": attempt, "max_attempts": max_attempts, "delay_s": delay,
                       "error": type(exc).__name__},
            )
            await sleep(delay)
        else:
            if pool is None:  # pragma: no cover - asyncpg typing allows None
                raise DatabaseUnavailableError("asyncpg returned no pool")
            logger.info("postgres pool ready", extra={"attempt": attempt})
            return pool
    raise DatabaseUnavailableError(
        f"Could not connect to Postgres after {max_attempts} attempts"
    ) from last_error


_pool: asyncpg.Pool | None = None


async def init_pool(
    dsn: str,
    *,
    min_size: int = 1,
    max_size: int = 5,
    max_attempts: int = 5,
    base_delay_s: float = 0.5,
    server_settings: dict[str, str] | None = None,
) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    _pool = await create_pool_with_retry(
        dsn,
        min_size=min_size,
        max_size=max_size,
        max_attempts=max_attempts,
        base_delay_s=base_delay_s,
        server_settings=server_settings,
    )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        pool, _pool = _pool, None
        await pool.close()


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise PoolNotInitializedError("Database pool is not initialized; call init_pool() first")
    return _pool


@asynccontextmanager
async def transaction(pool: asyncpg.Pool | None = None) -> AsyncIterator[Connection]:
    async with (pool or get_pool()).acquire() as conn, conn.transaction():
        yield conn


async def apply_schema(executor: Executor) -> None:
    """Idempotently create all tables/indexes and seed baseline categories."""
    if isinstance(executor, asyncpg.Pool):
        async with executor.acquire() as conn:
            await _apply_schema_on_connection(conn)
    else:
        await _apply_schema_on_connection(executor)


async def _apply_schema_on_connection(conn: Connection) -> None:
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _MIGRATION_LOCK_KEY)
        for statement in SCHEMA_STATEMENTS:
            await conn.execute(statement)
        slugs = [slug for slug, _ in BASELINE_CATEGORIES]
        names = [name for _, name in BASELINE_CATEGORIES]
        await conn.execute(SEED_BASELINE_CATEGORIES, slugs, names)
    logger.info("schema applied", extra={"statements": len(SCHEMA_STATEMENTS)})


async def ping(executor: Executor) -> bool:
    try:
        return bool(await executor.fetchval("SELECT 1"))
    except (asyncpg.PostgresError, OSError, TimeoutError):
        return False


async def _migrate(dsn: str) -> None:
    pool = await create_pool_with_retry(dsn, min_size=1, max_size=1)
    try:
        await apply_schema(pool)
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    from app.observability import configure_logging

    parser = argparse.ArgumentParser(prog="python -m app.database")
    parser.add_argument("--migrate", action="store_true", help="apply schema and seed data")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres DSN (defaults to $DATABASE_URL)",
    )
    args = parser.parse_args(argv)
    if not args.migrate:
        parser.print_help()
        return 1
    if not args.database_url:
        parser.error("DATABASE_URL is not set; pass --database-url")
    configure_logging(os.environ.get("LOG_LEVEL", "INFO").upper())
    try:
        asyncio.run(_migrate(args.database_url))
    except DatabaseUnavailableError:
        logger.exception("migration failed: database unavailable")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
