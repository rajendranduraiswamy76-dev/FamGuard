from __future__ import annotations

import io
import json
import logging

from app.observability import JsonFormatter, SensitiveDataFilter


def test_sensitive_log_fields_are_redacted() -> None:
    fixture = "PRIVATE_OCR_FIXTURE_7f4a"
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.addFilter(SensitiveDataFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.Logger("test.redaction")
    logger.addHandler(handler)
    try:
        logger.info(
            "document processed",
            extra={"extracted_text": fixture, "context": {"raw_text": fixture}},
        )
        logger.info(
            "text field: %(extracted_text)s",
            {"extracted_text": fixture},
        )
    finally:
        logger.removeHandler(handler)
        handler.close()

    rendered = output.getvalue()
    assert fixture not in rendered
    payload, formatted_argument = [json.loads(line) for line in rendered.splitlines()]
    assert payload["extracted_text"] == "[REDACTED]"
    assert payload["context"]["raw_text"] == "[REDACTED]"
    assert formatted_argument["message"] == "text field: [REDACTED]"
