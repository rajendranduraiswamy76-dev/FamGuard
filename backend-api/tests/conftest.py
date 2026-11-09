from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import AsyncIterator, Callable

import pytest

# Must be set before any app module reads settings.
os.environ.setdefault("ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/famguard_test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")

import asyncpg
import httpx
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.extractor import FakeExtractor
from app.agents.graph import IngestionDeps, IngestionService
from app.agents.rag_engine import (
    KeywordContextResolver,
    RAGDeps,
    RAGEngine,
    TemplateAnswerGenerator,
)
from app.api.main import create_app
from app.bulk import BulkImporter
from app.config import get_settings
from app.database import apply_schema, create_pool_with_retry
from app.dependencies import (
    provide_bulk_importer,
    provide_embedder,
    provide_ingestion_service,
    provide_rag_engine,
    provide_storage,
    provide_task_queue,
    provide_vector_store,
)
from app.embeddings import FakeEmbedder
from app.ratelimit import limiter
from app.security import get_db, get_verifier
from app.storage import InMemoryStorage
from app.tasks import RecordingTaskQueue
from app.vectorstore import InMemoryVectorIndex, VectorStore
from tests.api_harness import ApiHarness
from tests.helpers import make_verifier

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]] | None:
    # psycopg's async driver (LangGraph Postgres checkpointer) cannot run on the Proactor loop.
    if sys.platform == "win32":
        return {"selector": asyncio.SelectorEventLoop}
    return None


@pytest.fixture
async def db_pool() -> AsyncIterator[asyncpg.Pool]:
    """Pool bound to an isolated, throwaway schema in TEST_DATABASE_URL."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    schema = f"famguard_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await admin.close()

    pool = await create_pool_with_retry(
        TEST_DATABASE_URL,
        min_size=1,
        max_size=4,
        max_attempts=1,
        server_settings={"search_path": f"{schema},public"},
    )
    try:
        await apply_schema(pool)
        yield pool
    finally:
        await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            await admin.close()


@pytest.fixture
async def api(db_pool: asyncpg.Pool) -> AsyncIterator[ApiHarness]:
    storage, queue = InMemoryStorage(), RecordingTaskQueue()
    index, embedder = InMemoryVectorIndex(), FakeEmbedder()
    vectors = VectorStore(index, "family-vault")
    engine = RAGEngine(
        RAGDeps(
            pool=db_pool,
            embedder=embedder,
            vectors=vectors,
            resolver=KeywordContextResolver(),
            generator=TemplateAnswerGenerator(),
        )
    )
    app = create_app()
    limiter.reset()
    app.dependency_overrides[get_db] = lambda: db_pool
    app.dependency_overrides[get_verifier] = lambda: make_verifier()
    app.dependency_overrides[provide_storage] = lambda: storage
    app.dependency_overrides[provide_task_queue] = lambda: queue
    app.dependency_overrides[provide_rag_engine] = lambda: engine
    app.dependency_overrides[provide_vector_store] = lambda: vectors
    app.dependency_overrides[provide_embedder] = lambda: embedder
    app.dependency_overrides[provide_bulk_importer] = lambda: BulkImporter(
        db_pool, embedder, vectors
    )
    ingestion = IngestionService(IngestionDeps(
        pool=db_pool, storage=storage, extractor=FakeExtractor(),
        embedder=embedder, vectors=vectors,
    ), InMemorySaver())
    app.dependency_overrides[provide_ingestion_service] = lambda: ingestion
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield ApiHarness(app, client, db_pool, storage, queue, index, vectors, embedder, ingestion)
