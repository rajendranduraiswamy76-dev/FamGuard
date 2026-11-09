from __future__ import annotations

import io
import os
import uuid
from collections.abc import Iterator, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest

os.environ.setdefault("ENV", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/famguard_test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test")
os.environ.setdefault("PINECONE_API_KEY", "pc-test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

import asyncpg
from langgraph.checkpoint.memory import InMemorySaver
from PIL import Image

from app.agents.extractor import FakeExtractor
from app.agents.graph import IngestionDeps, IngestionService
from app.agents.state import UnifiedVaultExtraction
from app.database import apply_schema, create_pool_with_retry
from app.embeddings import FakeEmbedder
from app.repositories import Document, DocumentRepo, FamilyMember, FamilyRepo, MemberRepo
from app.storage import InMemoryStorage
from app.vectorstore import InMemoryVectorIndex, VectorStore, build_records
from famguard_worker.email.sender import InMemorySender
from famguard_worker.runtime import Resources, runtime
from famguard_worker.worker import app as celery_app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
NS = "family-vault"


@pytest.fixture(autouse=True, scope="session")
def _eager_celery() -> Iterator[None]:
    # eager_propagates must stay off, otherwise Celery raises Retry instead of re-applying;
    # EagerResult.get() still re-raises the final error.
    celery_app.conf.update(task_always_eager=True, task_eager_propagates=False)
    yield


class FlakyEmbedder(FakeEmbedder):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.failures > 0:
            self.failures -= 1
            raise ConnectionError("embedding service unavailable")
        return await super().embed(texts)


@dataclass
class World:
    schema: str
    storage: InMemoryStorage = field(default_factory=InMemoryStorage)
    index: InMemoryVectorIndex = field(default_factory=InMemoryVectorIndex)
    extractor: FakeExtractor = field(default_factory=FakeExtractor)
    embedder: FakeEmbedder = field(default_factory=FakeEmbedder)
    sender: InMemorySender = field(default_factory=InMemorySender)
    member: FamilyMember | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        return runtime.resources().pool

    def add_member(self, name: str, role: str, email: str | None) -> FamilyMember:
        assert self.member is not None
        return runtime.run(MemberRepo(self.pool).create(
            self.member.family_id, display_name=name, role=role, email=email,  # type: ignore[arg-type]
        ))

    def seed_document(self, owner: FamilyMember, name: str, expires: date | None, *,
                      chunks: int = 1, requires_alert: bool = True,
                      category: str = "identitycards") -> Document:
        """A completed document in Postgres plus its vectors in the fake index."""
        doc = runtime.run(DocumentRepo(self.pool).create(
            owner.family_id, family_member_id=owner.id, category_slug=category,
            document_name=name, source="bulk", status="completed", expiration_date=expires,
            requires_alert=requires_alert, chunk_count=chunks,
        ))
        extraction = UnifiedVaultExtraction.model_validate({
            "category": category, "document_name": name,
            "expiration_date": expires.isoformat() if expires else None,
            "requires_alert": requires_alert, "extracted_text": name,
            "financial_value": None, "confidence_score": 1.0,
        })
        texts = [f"{name} part {i}" for i in range(chunks)]
        vectors = runtime.run(FakeEmbedder().embed(texts))
        runtime.run(VectorStore(self.index, NS).upsert(build_records(
            family_id=owner.family_id, family_member_id=owner.id, document_id=doc.id,
            extraction=extraction, chunks=texts, vectors=vectors, source="bulk",
        )))
        return doc

    def register(self, filename: str = "passport.jpg") -> Document:
        assert self.member is not None
        buf = io.BytesIO()
        Image.new("RGB", (24, 24), "white").save(buf, format="JPEG")
        doc_id = uuid.uuid4()
        key = f"{self.member.family_id}/{doc_id}/{uuid.uuid4().hex}.jpg"
        runtime.run(self.storage.put(key, buf.getvalue(), "image/jpeg"))
        return runtime.run(DocumentRepo(self.pool).create(
            self.member.family_id, family_member_id=self.member.id,
            category_slug="miscellaneous", document_name=filename, source="upload",
            thread_id=f"ing-{uuid.uuid4().hex}", original_filename=filename, r2_object_key=key,
        ))

    def document(self, doc: Document) -> Document:
        found = runtime.run(DocumentRepo(self.pool).get(doc.family_id, doc.id))
        assert found is not None
        return found

    def vectors_for(self, doc: Document) -> list[Any]:
        return [r for r in self.index.namespaces.get(NS, {}).values()
                if r["metadata"]["document_id"] == str(doc.id)]


@pytest.fixture
def world(request: pytest.FixtureRequest) -> Iterator[World]:
    """Worker runtime bound to an isolated Postgres schema and in-memory externals."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    params = getattr(request, "param", {})
    w = World(schema=f"famguard_wtest_{uuid.uuid4().hex[:12]}")
    if "responses" in params:
        w.extractor = FakeExtractor(params["responses"])
    if "embed_failures" in params:
        w.embedder = FlakyEmbedder(params["embed_failures"])
    url = TEST_DATABASE_URL

    async def factory(stack: AsyncExitStack) -> Resources:
        admin = await asyncpg.connect(url)
        try:
            await admin.execute(f'CREATE SCHEMA "{w.schema}"')
        finally:
            await admin.close()
        pool = await create_pool_with_retry(
            url, min_size=1, max_size=3, max_attempts=1,
            server_settings={"search_path": f"{w.schema},public"},
        )
        stack.push_async_callback(pool.close)
        await apply_schema(pool)
        deps = IngestionDeps(pool=pool, storage=w.storage, extractor=w.extractor,
                             embedder=w.embedder, vectors=VectorStore(w.index, NS),
                             sleep=_no_sleep)
        return Resources(pool=pool, ingestion=IngestionService(deps, InMemorySaver()),
                         vectors=VectorStore(w.index, NS), sender=w.sender)

    runtime.configure(factory)
    pool = runtime.resources().pool
    family = runtime.run(FamilyRepo(pool).create("Worker Family"))
    w.member = runtime.run(MemberRepo(pool).create(family.id, display_name="Ann", role="admin",
                                                   email="ann@example.com"))
    try:
        yield w
    finally:
        runtime.close()

        async def _drop() -> None:
            conn = await asyncpg.connect(url)
            try:
                await conn.execute(f'DROP SCHEMA "{w.schema}" CASCADE')
            finally:
                await conn.close()

        runtime.run(_drop())
        runtime.close()


async def _no_sleep(_: float) -> None:
    return None
