from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.agents.rag_engine import RAGEngine
from app.api.schemas import ChatIn
from app.dependencies import provide_rag_engine
from app.ratelimit import CHAT_LIMIT, limiter
from app.security import CurrentPrincipal, Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vault", tags=["chat"])

Engine = Annotated[RAGEngine, Depends(provide_rag_engine)]


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def _event_stream(
    engine: RAGEngine, message: str, principal: Principal, conversation_id: str | None
) -> AsyncIterator[str]:
    try:
        async for item in engine.stream(message, principal):
            yield sse(item["event"], item["data"])
    except Exception:
        logger.exception("chat stream failed")
        yield sse("error", {"message": "Something went wrong while answering. Please retry."})
    yield sse("done", {"conversation_id": conversation_id})


@router.post("/chat", response_class=StreamingResponse)
@limiter.limit(CHAT_LIMIT)
async def chat(
    request: Request, body: ChatIn, principal: CurrentPrincipal, engine: Engine
) -> StreamingResponse:
    return StreamingResponse(
        _event_stream(engine, body.message, principal, body.conversation_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
