"""Producer side of the Celery queue. The API enqueues by task name and never imports the worker."""

from __future__ import annotations

import asyncio
from typing import Any, Final, Protocol

from celery import Celery

INGEST_TASK: Final = "famguard.ingest_document"
RESUME_TASK: Final = "famguard.resume_ingestion"
EXPIRY_SWEEP_TASK: Final = "famguard.expiry_sweep"


class TaskQueue(Protocol):
    async def enqueue_ingestion(self, thread_id: str) -> None: ...
    async def enqueue_resume(self, thread_id: str, payload: dict[str, Any]) -> None: ...
    async def enqueue_expiry_sweep(self) -> None: ...


class CeleryTaskQueue:
    def __init__(self, broker_url: str) -> None:
        self._celery = Celery("famguard-api", broker=broker_url, set_as_current=False)
        self._celery.conf.update(task_serializer="json", accept_content=["json"])

    async def _send(self, name: str, args: list[Any]) -> None:
        await asyncio.to_thread(self._celery.send_task, name, args=args)

    async def enqueue_ingestion(self, thread_id: str) -> None:
        await self._send(INGEST_TASK, [thread_id])

    async def enqueue_resume(self, thread_id: str, payload: dict[str, Any]) -> None:
        await self._send(RESUME_TASK, [thread_id, payload])

    async def enqueue_expiry_sweep(self) -> None:
        await self._send(EXPIRY_SWEEP_TASK, [])


class RecordingTaskQueue:
    """In-memory queue for tests."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, list[Any]]] = []

    async def enqueue_ingestion(self, thread_id: str) -> None:
        self.sent.append((INGEST_TASK, [thread_id]))

    async def enqueue_resume(self, thread_id: str, payload: dict[str, Any]) -> None:
        self.sent.append((RESUME_TASK, [thread_id, payload]))

    async def enqueue_expiry_sweep(self) -> None:
        self.sent.append((EXPIRY_SWEEP_TASK, []))
