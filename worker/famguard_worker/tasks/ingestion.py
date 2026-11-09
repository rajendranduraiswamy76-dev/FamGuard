"""Ingestion tasks: run a new upload through the graph, or resume it after human review."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any, Final

from celery import Task
from pydantic import ValidationError

from app.agents.graph import IngestionStateError, ReviewResume
from app.tasks import INGEST_TASK, RESUME_TASK
from famguard_worker.runtime import runtime
from famguard_worker.worker import app

logger = logging.getLogger(__name__)

MAX_RETRIES: Final = 3
RETRY_COUNTDOWNS_S: Final = (10, 60, 300)


def retry_countdown(retries: int) -> int:
    return RETRY_COUNTDOWNS_S[min(retries, len(RETRY_COUNTDOWNS_S) - 1)]


@app.task(bind=True, name=INGEST_TASK, max_retries=MAX_RETRIES)
def ingest_document(self: Task, thread_id: str) -> dict[str, Any]:
    service = runtime.resources().ingestion
    try:
        outcome = runtime.run(service.run(thread_id))
    except IngestionStateError as exc:
        logger.error(
            "ingestion not runnable",
            extra={"thread_id": thread_id, "error_type": type(exc).__name__},
        )
        return {"thread_id": thread_id, "status": "failed", "error": str(exc)}
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                "ingestion failed permanently",
                extra={"thread_id": thread_id, "error_type": type(exc).__name__},
            )
            runtime.run(service.mark_failed(thread_id, f"Processing failed: {type(exc).__name__}"))
            raise
        logger.warning(
            "ingestion retry scheduled",
            extra={
                "thread_id": thread_id,
                "retries": self.request.retries,
                "error_type": type(exc).__name__,
            },
        )
        raise self.retry(exc=exc, countdown=retry_countdown(self.request.retries)) from exc
    logger.info("ingestion task finished", extra={"thread_id": thread_id, "status": outcome.status})
    return asdict(outcome)


@app.task(bind=True, name=RESUME_TASK, max_retries=MAX_RETRIES)
def resume_ingestion(self: Task, thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resume = ReviewResume.model_validate(payload)
    except ValidationError:
        logger.error("invalid review payload", extra={"thread_id": thread_id})
        return {"thread_id": thread_id, "status": "failed", "error": "invalid review payload"}

    service = runtime.resources().ingestion
    try:
        outcome = runtime.run(service.resume(thread_id, resume))
    except IngestionStateError:
        # Duplicate delivery after a successful resume, or the thread was never paused.
        logger.info("resume skipped: thread not awaiting review", extra={"thread_id": thread_id})
        return {"thread_id": thread_id, "status": "not_waiting"}
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                "resume failed permanently",
                extra={"thread_id": thread_id, "error_type": type(exc).__name__},
            )
            runtime.run(
                service.mark_failed(
                    thread_id,
                    f"Review could not be applied: {type(exc).__name__}",
                    from_statuses=("processing", "review_required"),
                )
            )
            raise
        raise self.retry(exc=exc, countdown=retry_countdown(self.request.retries)) from exc
    return asdict(outcome)
