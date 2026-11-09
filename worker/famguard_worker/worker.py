"""Celery application and Beat schedule (spec: `app/worker.py`)."""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import task_postrun, task_prerun, worker_process_shutdown

from app.config import get_settings
from app.observability import configure_logging
from app.tasks import EXPIRY_SWEEP_TASK
from famguard_worker.observability import (
    WorkerJsonFormatter,
    WorkerLogFilter,
    task_id_context,
)
from famguard_worker.runtime import runtime

logger = logging.getLogger(__name__)

settings = get_settings()
configure_logging(settings.log_level)
for _handler in logging.getLogger().handlers:
    _handler.addFilter(WorkerLogFilter())
    _handler.setFormatter(WorkerJsonFormatter())

app = Celery(
    "famguard",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["famguard_worker.tasks.ingestion", "famguard_worker.tasks.expiry_sweep"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=settings.celery_timezone,
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    # acks_late on Redis: redelivery happens only after this timeout, so it must exceed task time.
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "daily-expiry-sweep": {
            "task": EXPIRY_SWEEP_TASK,
            "schedule": crontab(hour=0, minute=0),
        },
    },
)


@worker_process_shutdown.connect
def _close_runtime(**_: Any) -> None:
    runtime.close()


@task_prerun.connect
def _set_task_log_context(task_id: str | None = None, **_: Any) -> None:
    task_id_context.set(task_id)


@task_postrun.connect
def _clear_task_log_context(**_: Any) -> None:
    task_id_context.set(None)
