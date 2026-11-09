from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.agents.rag_engine import RAGEngine
from app.dependencies import provide_rag_engine
from tests.api_harness import ApiHarness


def parse_sse(body: str) -> list[tuple[str, Any]]:
    events = []
    for frame in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


@pytest.mark.integration
async def test_chat_streams_sse_in_order(api: ApiHarness) -> None:
    headers, _ = await api.login("user_ann", email="ann@example.com", name="Ann")
    resp = await api.client.post("/api/vault/chat", headers=headers,
                                 json={"message": "Any passports?", "conversation_id": "c1"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(resp.text)
    names = [name for name, _ in events]
    assert names[0] == "filter"
    assert names[-2:] == ["sources", "done"]
    assert set(names[1:-2]) == {"token"}
    assert events[0][1]["category"] == "identitycards"
    assert events[-1][1] == {"conversation_id": "c1"}


@pytest.mark.integration
async def test_chat_engine_failure_emits_error_event(api: ApiHarness) -> None:
    class Broken(RAGEngine):
        def __init__(self) -> None:
            pass

        async def stream(self, question: str, principal: Any) -> AsyncIterator[dict[str, Any]]:
            yield {"event": "filter", "data": {}}
            raise RuntimeError("pinecone down")

    api.app.dependency_overrides[provide_rag_engine] = lambda: Broken()
    headers, _ = await api.login("user_ann", email="ann@example.com", name="Ann")
    resp = await api.client.post("/api/vault/chat", headers=headers, json={"message": "hi"})
    names = [n for n, _ in parse_sse(resp.text)]
    assert names == ["filter", "error", "done"]
    assert "pinecone" not in resp.text


@pytest.mark.integration
async def test_chat_validation_and_auth(api: ApiHarness) -> None:
    assert (await api.client.post("/api/vault/chat", json={"message": "hi"})).status_code == 401
    headers, _ = await api.login("user_ann", email="ann@example.com", name="Ann")
    assert (await api.client.post("/api/vault/chat", headers=headers,
                                  json={"message": ""})).status_code == 422
    assert (await api.client.post("/api/vault/chat", headers=headers,
                                  json={"message": "x" * 2001})).status_code == 422
