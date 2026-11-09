"""Process-wide service providers for FastAPI. Tests replace these via dependency_overrides."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from functools import lru_cache

from redis.asyncio import Redis

from app.agents.extractor import get_extractor
from app.agents.graph import IngestionDeps, IngestionService, open_checkpointer
from app.agents.rag_engine import RAGEngine, get_rag_engine_deps
from app.bulk import BulkImporter
from app.config import get_settings
from app.database import PoolNotInitializedError, get_pool, ping
from app.embeddings import Embedder, get_embedder
from app.storage import ObjectStorage, get_storage
from app.tasks import CeleryTaskQueue, TaskQueue
from app.vectorstore import VectorStore, get_vector_store


@lru_cache(maxsize=1)
def provide_storage() -> ObjectStorage:
    return get_storage(get_settings())


@lru_cache(maxsize=1)
def provide_task_queue() -> TaskQueue:
    return CeleryTaskQueue(get_settings().redis_url)


@lru_cache(maxsize=1)
def provide_embedder() -> Embedder:
    return get_embedder(get_settings())


@lru_cache(maxsize=1)
def provide_vector_store() -> VectorStore:
    return get_vector_store(get_settings())


@lru_cache(maxsize=1)
def provide_rag_engine() -> RAGEngine:
    deps = get_rag_engine_deps(
        get_settings(),
        pool=get_pool(),
        embedder=provide_embedder(),
        vectors=provide_vector_store(),
    )
    return RAGEngine(deps)


def provide_bulk_importer() -> BulkImporter:
    return BulkImporter(get_pool(), provide_embedder(), provide_vector_store())


async def provide_ingestion_service() -> AsyncIterator[IngestionService]:
    settings = get_settings()
    async with open_checkpointer(settings) as checkpointer:
        yield IngestionService(IngestionDeps(
            pool=get_pool(), storage=provide_storage(), extractor=get_extractor(settings),
            embedder=provide_embedder(), vectors=provide_vector_store(),
        ), checkpointer)


ReadinessCheck = Callable[[], Awaitable[bool]]


async def _check_postgres() -> bool:
    try:
        return await ping(get_pool())
    except PoolNotInitializedError:
        return False


async def _check_redis() -> bool:
    client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=2,
                            socket_timeout=2)
    try:
        return bool(await client.ping())
    finally:
        await client.aclose()


async def _check_pinecone() -> bool:
    return await provide_vector_store().ping()


def provide_readiness_checks() -> dict[str, ReadinessCheck]:
    return {"postgres": _check_postgres, "redis": _check_redis, "pinecone": _check_pinecone}
