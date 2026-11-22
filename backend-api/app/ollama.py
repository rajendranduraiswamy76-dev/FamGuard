"""Local Ollama API client used by the optional AI provider."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx


class OllamaClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    async def request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=600, trust_env=False
        ) as client:
            response = await client.post(path, json=payload)
            response.raise_for_status()
            result = response.json()
        if not isinstance(result, dict):
            raise ValueError("Invalid Ollama response")
        if result.get("error"):
            raise RuntimeError(f"Ollama error: {result['error']}")
        return result

    async def chat_stream(
        self, model: str, messages: Sequence[dict[str, Any]]
    ) -> AsyncIterator[str]:
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=600, trust_env=False
        ) as client:
            async with client.stream(
                "POST", "/api/chat",
                json={"model": model, "messages": list(messages), "stream": True,
                      "options": {"temperature": 0.2}},
            ) as response:
                response.raise_for_status()
                done = False
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    item = json.loads(line)
                    if item.get("error"):
                        raise RuntimeError(f"Ollama error: {item['error']}")
                    content = item.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if item.get("done") is True:
                        done = True
                if not done:
                    raise RuntimeError("Ollama stream ended before completion")
