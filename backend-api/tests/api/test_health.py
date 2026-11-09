from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from app.api.main import create_app
from app.observability import JsonFormatter, request_id_ctx


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # ASGITransport does not run lifespan, so no database is needed here.
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_healthz_ok(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(resp.headers["X-Request-ID"]) == 32


async def test_valid_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "abc-123.DEF_4"})
    assert resp.headers["X-Request-ID"] == "abc-123.DEF_4"


@pytest.mark.parametrize("bad", ["has space", "x" * 129, "inject\ttab", "semi;colon"])
async def test_unsafe_request_id_is_replaced(client: httpx.AsyncClient, bad: str) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": bad})
    assert resp.headers["X-Request-ID"] != bad
    assert len(resp.headers["X-Request-ID"]) == 32


async def test_unknown_route_has_request_id(client: httpx.AsyncClient) -> None:
    resp = await client.get("/nope")
    assert resp.status_code == 404
    assert "X-Request-ID" in resp.headers


def test_json_formatter_includes_request_id_and_extra() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.thread_id = "th-1"
    token = request_id_ctx.set("req-1")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_ctx.reset(token)
    assert payload["message"] == "hello world"
    assert payload["request_id"] == "req-1"
    assert payload["thread_id"] == "th-1"
    assert payload["level"] == "INFO"
