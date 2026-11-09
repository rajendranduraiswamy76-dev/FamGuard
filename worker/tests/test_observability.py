from __future__ import annotations

import io
import json
import logging

from famguard_worker.observability import (
    WorkerJsonFormatter,
    WorkerLogFilter,
    task_id_context,
)
from famguard_worker.worker import _clear_task_log_context, _set_task_log_context


def test_celery_task_signals_set_and_clear_task_id() -> None:
    _set_task_log_context("task-123")
    assert task_id_context.get() == "task-123"

    _clear_task_log_context()
    assert task_id_context.get() is None


def test_task_id_is_structured_and_pii_is_redacted() -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(WorkerLogFilter())
    handler.setFormatter(WorkerJsonFormatter())
    logger = logging.Logger("test.worker")
    logger.addHandler(handler)
    token = task_id_context.set("task-123")
    email = "private.person@example.com"
    extracted_text = "PRIVATE_OCR_FIXTURE_7f4a"
    try:
        try:
            raise ValueError(f"{email}: {extracted_text}")
        except ValueError:
            logger.exception(
                "task failed for %s",
                email,
                extra={
                    "recipient": email,
                    "extracted_text": extracted_text,
                    "context": {"raw_text": extracted_text, "email": email},
                },
            )
    finally:
        task_id_context.reset(token)
        logger.removeHandler(handler)
        handler.close()

    rendered = output.getvalue()
    assert email not in rendered
    assert extracted_text not in rendered
    record = json.loads(rendered)
    assert record["task_id"] == "task-123"
    assert record["message"] == "task failed for [REDACTED]"
    assert record["recipient"] == "[REDACTED]"
    assert record["extracted_text"] == "[REDACTED]"
    assert record["context"] == {
        "raw_text": "[REDACTED]",
        "email": "[REDACTED]",
    }
    assert record["exception_type"] == "ValueError"
    assert "exc_info" not in record
