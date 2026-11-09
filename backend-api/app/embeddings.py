"""Token-based chunking and text-embedding-3-small embeddings (1536 dims)."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from functools import lru_cache
from typing import Any, Final, Protocol

import tiktoken

from app.config import Settings

EMBEDDING_MODEL: Final = "text-embedding-3-small"
EMBEDDING_DIMENSIONS: Final = 1536
EMBEDDING_BATCH_SIZE: Final = 100
CHUNK_TOKENS: Final = 800
CHUNK_OVERLAP_TOKENS: Final = 100


class EmbeddingError(Exception):
    pass


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding:
    return tiktoken.get_encoding("cl100k_base")


def chunk_text(
    text: str, *, max_tokens: int = CHUNK_TOKENS, overlap: int = CHUNK_OVERLAP_TOKENS
) -> list[str]:
    if max_tokens <= 0 or not 0 <= overlap < max_tokens:
        raise ValueError("Require max_tokens > 0 and 0 <= overlap < max_tokens")
    text = text.strip()
    if not text:
        return []
    enc = _encoding()
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return [text]
    chunks: list[str] = []
    step = max_tokens - overlap
    for start in range(0, len(tokens), step):
        window = tokens[start : start + max_tokens]
        chunks.append(enc.decode(window).strip())
        if start + max_tokens >= len(tokens):
            break
    return [c for c in chunks if c]


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Wraps an `openai.AsyncOpenAI`-compatible client."""

    def __init__(self, client: Any, *, batch_size: int = EMBEDDING_BATCH_SIZE) -> None:
        self._client = client
        self._batch_size = batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            response = await self._client.embeddings.create(
                model=EMBEDDING_MODEL, input=batch, dimensions=EMBEDDING_DIMENSIONS
            )
            data = sorted(response.data, key=lambda item: item.index)
            if len(data) != len(batch):
                raise EmbeddingError(f"Expected {len(batch)} embeddings, got {len(data)}")
            for item in data:
                if len(item.embedding) != EMBEDDING_DIMENSIONS:
                    raise EmbeddingError(
                        f"Expected {EMBEDDING_DIMENSIONS} dimensions, got {len(item.embedding)}"
                    )
                vectors.append(list(item.embedding))
        return vectors


class FakeEmbedder:
    """Deterministic unit vectors derived from a hash of the text (offline tests)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self._vector(t) for t in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        seed = hashlib.sha256(text.encode()).digest()
        raw = [((seed[i % len(seed)] + i) % 251) / 250 - 0.5 for i in range(EMBEDDING_DIMENSIONS)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]


def get_embedder(settings: Settings) -> Embedder:
    if settings.fake_externals:
        return FakeEmbedder()
    from openai import AsyncOpenAI

    return OpenAIEmbedder(
        AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), max_retries=3)
    )
