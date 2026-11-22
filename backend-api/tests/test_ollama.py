from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx

from app.agents.extractor import ExtractionError, OllamaVisionExtractor
from app.agents.rag_engine import OllamaAnswerGenerator, OllamaContextResolver
from app.agents.state import UnifiedVaultExtraction
from app.embeddings import EmbeddingError, OllamaEmbedder
from app.ollama import OllamaClient
from app.parsers import PreparedInput
from app.vectorstore import SQLiteVectorIndex, VectorMatch, VectorStore, build_filter, build_records
from tests.helpers import make_principal

BASE = "http://127.0.0.1:11434"
VALID = {
    "category": "identitycards", "document_name": "Test ID",
    "expiration_date": "bad date", "requires_alert": True, "extracted_text": "synthetic",
    "financial_value": None, "confidence_score": 0.8,
}


@respx.mock
async def test_extraction_sends_image_and_preserves_unparsed_date() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, json={
            "message": {"content": json.dumps(VALID)}, "prompt_eval_count": 5, "eval_count": 7,
        })
    )
    result = await OllamaVisionExtractor(OllamaClient(BASE), "model").extract(
        PreparedInput(images=[b"image"], text="synthetic"), categories=["identitycards"],
        filename="id.jpg",
    )
    body = json.loads(route.calls.last.request.content)
    assert body["messages"][1]["images"] == [base64.b64encode(b"image").decode()]
    assert "<document>" in body["messages"][1]["content"]
    assert result.expiration_raw == "bad date"
    assert (result.prompt_tokens, result.completion_tokens) == (5, 7)


@respx.mock
async def test_invalid_extraction_is_an_error() -> None:
    respx.post(f"{BASE}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "{}"}})
    )
    with pytest.raises(ExtractionError):
        await OllamaVisionExtractor(OllamaClient(BASE), "model").extract(
            PreparedInput(text="x"), categories=["miscellaneous"], filename="x"
        )


@respx.mock
async def test_embeddings_validate_count_dimensions_and_no_truncation() -> None:
    route = respx.post(f"{BASE}/api/embed").mock(
        return_value=httpx.Response(200, json={"embeddings": [[0.1] * 768]})
    )
    embedder = OllamaEmbedder(OllamaClient(BASE), "nomic-embed-text")
    assert len((await embedder.embed(["synthetic"]))[0]) == 768
    assert json.loads(route.calls.last.request.content)["truncate"] is False
    assert json.loads(route.calls.last.request.content)["input"] == ["search_document: synthetic"]
    await embedder.embed_query("question")
    assert json.loads(route.calls.last.request.content)["input"] == ["search_query: question"]
    route.mock(return_value=httpx.Response(200, json={"embeddings": [[0.1] * 3]}))
    with pytest.raises(EmbeddingError):
        await embedder.embed(["synthetic"])


@respx.mock
async def test_chat_resolver_and_stream() -> None:
    route = respx.post(f"{BASE}/api/chat").mock(return_value=httpx.Response(
        200, json={"message": {"content": '{"member_name":"Test","category":"medical"}'}}
    ))
    client = OllamaClient(BASE)
    resolution = await OllamaContextResolver(client, "model").resolve(
        "question", member_names=["Test"], categories=["medical"]
    )
    assert resolution.member_name == "Test"
    assert "Test" in json.loads(route.calls.last.request.content)["messages"][0]["content"]
    route.mock(return_value=httpx.Response(
        200, text='{"message":{"content":"Hello "},"done":false}\n'
                  '{"message":{"content":"world"},"done":true}\n'
    ))
    match = VectorMatch(id="1", score=1, metadata={"text": "synthetic"})
    assert [part async for part in OllamaAnswerGenerator(client, "model").stream(
        "question", [match]
    )] == ["Hello ", "world"]
    route.mock(return_value=httpx.Response(200, text='{"message":{"content":"partial"}}\n'))
    with pytest.raises(RuntimeError, match="before completion"):
        _ = [part async for part in client.chat_stream("model", [])]


async def test_local_index_dimensions_isolation_and_model_guard(tmp_path: Path) -> None:
    path = tmp_path / "vectors.sqlite3"
    store = VectorStore(SQLiteVectorIndex(path, embedding_model="nomic", dimensions=768),
                        "vault", dimensions=768)
    principal = make_principal("admin")
    other = make_principal("admin")
    extraction = UnifiedVaultExtraction.model_validate(VALID)
    records = build_records(
        family_id=principal.family_id, family_member_id=uuid4(), document_id=uuid4(),
        extraction=extraction, chunks=["synthetic"], vectors=[[0.1] * 768],
        source="upload", dimensions=store.dimensions,
    )
    await store.upsert(records)
    assert len(await store.query([0.1] * 768, filter=build_filter(principal))) == 1
    assert await store.query([0.1] * 768, filter=build_filter(other)) == []
    with pytest.raises(ValueError, match="configuration differs"):
        SQLiteVectorIndex(path, embedding_model="other", dimensions=768)
    with pytest.raises(ValueError, match="fake index"):
        SQLiteVectorIndex(path)


async def test_existing_unmarked_index_cannot_be_reused(tmp_path: Path) -> None:
    path = tmp_path / "vectors.sqlite3"
    index = SQLiteVectorIndex(path)
    index.upsert(
        vectors=[{"id": "test", "values": [0.1] * 1536, "metadata": {}}],
        namespace="vault",
    )
    with pytest.raises(ValueError, match="no embedding configuration"):
        SQLiteVectorIndex(path, embedding_model="nomic", dimensions=768)
