"""Worker log context and PII-safe structured formatting."""

from __future__ import annotations

import contextvars
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from app.observability import JsonFormatter

task_id_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "worker_task_id", default=None
)

_EMAIL = re.compile(
    r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+\b"
)
_SENSITIVE_FIELDS = frozenset(
    {"email", "to", "recipient", "extracted_text", "raw_text", "subject", "html", "text"}
)


def _redact(value: Any, *, field: str = "") -> Any:
    if field.lower() in _SENSITIVE_FIELDS:
        return "[REDACTED]"
    if isinstance(value, str):
        return _EMAIL.sub("[REDACTED]", value)
    if isinstance(value, Mapping):
        return {key: _redact(item, field=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


class WorkerLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = task_id_context.get()
        for key, value in tuple(record.__dict__.items()):
            if key in {"msg", "args"} or isinstance(value, Mapping | list | tuple | str):
                record.__dict__[key] = _redact(value, field=key)
        return True


class WorkerJsonFormatter(JsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        exc_info = record.exc_info
        if exc_info is None:
            return super().format(record)

        record.exc_info = None
        try:
            payload = json.loads(super().format(record))
        finally:
            record.exc_info = exc_info
        payload["exception_type"] = exc_info[0].__name__ if exc_info[0] else "Exception"
        return json.dumps(payload, default=str)
