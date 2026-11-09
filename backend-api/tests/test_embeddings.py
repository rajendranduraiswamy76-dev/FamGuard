from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pytest

from app.embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    EmbeddingError,
    FakeEmbedder,
    OpenAIEmbedder,
    chunk_text,
)


@dataclass
class _Item:
    index: int
    embedding: list[float]


@dataclass
class _Response:
    data: list[_Item]


class _FakeEmbeddingsAPI:
    def __init__(self, dims: int = EMBEDDING_DIMENSIONS, reverse: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._dims = dims
        self._reverse = reverse

    async def create(self, *, model: str, input: list[str], dimensions: int) -> _Response:
        self.calls.append({"model": model, "input": input, "dimensions": dimensions})
        items = [_Item(i, [float(len(t))] * self._dims) for i, t in enumerate(input)]
        return _Response(list(reversed(items)) if self._reverse else items)


class _FakeClient:
    def __init__(self, api: _FakeEmbeddingsAPI) -> None:
        self.embeddings = api


async def test_batches_250_texts_into_3_requests() -> None:
    api = _FakeEmbeddingsAPI()
    vectors = await OpenAIEmbedder(_FakeClient(api)).embed([f"t{i}" for i in range(250)])
    assert [len(c["input"]) for c in api.calls] == [100, 100, 50]
    assert all(c["model"] == EMBEDDING_MODEL and c["dimensions"] == 1536 for c in api.calls)
    assert len(vectors) == 250 and all(len(v) == 1536 for v in vectors)


async def test_preserves_order_when_api_returns_out_of_order() -> None:
    api = _FakeEmbeddingsAPI(reverse=True)
    vectors = await OpenAIEmbedder(_FakeClient(api)).embed(["a", "bbb"])
    assert vectors[0][0] == 1.0 and vectors[1][0] == 3.0


async def test_wrong_dimensions_raise() -> None:
    with pytest.raises(EmbeddingError):
        await OpenAIEmbedder(_FakeClient(_FakeEmbeddingsAPI(dims=3072))).embed(["x"])


async def test_fake_embedder_is_deterministic_unit_vectors() -> None:
    emb = FakeEmbedder()
    a1, a2, b = await emb.embed(["alpha", "alpha", "beta"])
    assert a1 == a2 and a1 != b
    assert math.isclose(math.sqrt(sum(v * v for v in a1)), 1.0)


def test_short_text_single_chunk() -> None:
    assert chunk_text("  Passport of John  ") == ["Passport of John"]
    assert chunk_text("   ") == []


def test_long_text_chunks_with_overlap() -> None:
    words = [f"word{i}" for i in range(3000)]
    chunks = chunk_text(" ".join(words), max_tokens=800, overlap=100)
    assert len(chunks) >= 4
    # Overlap: the tail of each chunk reappears at the head of the next.
    assert chunks[0].split()[-1] in chunks[1]
    assert words[-1] in chunks[-1]


@pytest.mark.parametrize(("max_tokens", "overlap"), [(0, 0), (100, 100), (100, -1)])
def test_invalid_chunk_params(max_tokens: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text("abc", max_tokens=max_tokens, overlap=overlap)
