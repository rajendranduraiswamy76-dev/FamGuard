from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
from fastapi import FastAPI

from app.agents.graph import IngestionService
from app.embeddings import FakeEmbedder
from app.storage import InMemoryStorage
from app.tasks import RecordingTaskQueue
from app.vectorstore import InMemoryVectorIndex, VectorStore
from tests.helpers import auth, make_token


@dataclass
class ApiHarness:
    app: FastAPI
    client: httpx.AsyncClient
    pool: asyncpg.Pool
    storage: InMemoryStorage
    queue: RecordingTaskQueue
    index: InMemoryVectorIndex
    vectors: VectorStore
    embedder: FakeEmbedder
    ingestion: IngestionService

    async def login(self, sub: str, *, email: str | None = None,
                    name: str | None = None) -> tuple[dict[str, str], dict[str, Any]]:
        headers = auth(make_token(sub, email=email, name=name))
        resp = await self.client.get("/api/me", headers=headers)
        assert resp.status_code == 200, resp.text
        return headers, resp.json()
