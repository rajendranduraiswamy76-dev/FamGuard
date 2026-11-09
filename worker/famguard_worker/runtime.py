"""Per-process async runtime for Celery tasks.

Celery tasks are synchronous; each worker process owns one event loop plus the
asyncpg pool, checkpointer and service objects bound to it. Resources are created
lazily on first use so prefork children never inherit sockets from the parent.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import Any, TypeVar

import asyncpg

from app.agents.extractor import get_extractor
from app.agents.graph import IngestionDeps, IngestionService, open_checkpointer
from app.config import get_settings
from app.database import create_pool_with_retry
from app.embeddings import get_embedder
from app.storage import get_storage
from app.vectorstore import VectorStore, get_vector_store
from famguard_worker.email.sender import EmailSender, get_sender

T = TypeVar("T")


@dataclass
class Resources:
    pool: asyncpg.Pool
    ingestion: IngestionService
    vectors: VectorStore
    sender: EmailSender


ResourceFactory = Callable[[AsyncExitStack], Awaitable[Resources]]


async def default_factory(stack: AsyncExitStack) -> Resources:
    settings = get_settings()
    pool = await create_pool_with_retry(
        settings.database_url,
        min_size=1,
        max_size=settings.db_pool_max_size,
        max_attempts=settings.db_connect_max_attempts,
        base_delay_s=settings.db_connect_base_delay_s,
    )
    stack.push_async_callback(pool.close)
    checkpointer = await stack.enter_async_context(open_checkpointer(settings))
    vectors = get_vector_store(settings)
    deps = IngestionDeps(
        pool=pool,
        storage=get_storage(settings),
        extractor=get_extractor(settings),
        embedder=get_embedder(settings),
        vectors=vectors,
    )
    return Resources(
        pool=pool,
        ingestion=IngestionService(deps, checkpointer),
        vectors=vectors,
        sender=get_sender(settings),
    )


class WorkerRuntime:
    def __init__(self, factory: ResourceFactory = default_factory) -> None:
        self._factory = factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stack: AsyncExitStack | None = None
        self._resources: Resources | None = None

    def configure(self, factory: ResourceFactory) -> None:
        self.close()
        self._factory = factory

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            # psycopg's async driver cannot use the Windows Proactor loop.
            self._loop = (
                asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
            )
        return self._loop

    def run(self, coro: Coroutine[Any, Any, T]) -> T:
        return self.loop.run_until_complete(coro)

    def resources(self) -> Resources:
        if self._resources is None:
            self._stack, self._resources = self.run(self._open())
        return self._resources

    async def _open(self) -> tuple[AsyncExitStack, Resources]:
        stack = AsyncExitStack()
        try:
            resources = await self._factory(stack)
        except BaseException:
            await stack.aclose()
            raise
        return stack, resources

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            if self._stack is not None:
                self.run(self._stack.aclose())
            self._loop.close()
        self._loop = None
        self._stack = None
        self._resources = None


runtime = WorkerRuntime()
