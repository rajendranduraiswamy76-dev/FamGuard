"""Uniform error envelope: {"error": {"code", "message", "details"}}."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import DatabaseError
from app.repositories import ConflictError, DuplicateDocumentError, NotFoundError

logger = logging.getLogger(__name__)

_STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    503: "service_unavailable",
}


def error_response(
    status_code: int,
    message: str,
    *,
    code: str | None = None,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code or _STATUS_CODES.get(status_code, "error"),
                "message": message,
                "details": details or {},
            }
        },
        headers=headers,
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(exc.status_code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg", ""), "type": e.get("type", "")}
            for e in exc.errors()
        ]
        return error_response(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Request validation failed",
            details={"errors": errors},
        )

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limited(_: Request, exc: RateLimitExceeded) -> JSONResponse:
        return error_response(status.HTTP_429_TOO_MANY_REQUESTS,
                              "Too many requests, please slow down",
                              headers={"Retry-After": "60"})

    @app.exception_handler(DuplicateDocumentError)
    async def _duplicate(_: Request, exc: DuplicateDocumentError) -> JSONResponse:
        return error_response(
            status.HTTP_409_CONFLICT, "Document already exists", code="duplicate_document",
            details={"existing_document_id": str(exc.existing_document_id)},
        )

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return error_response(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return error_response(status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(DatabaseError)
    async def _db_down(_: Request, exc: DatabaseError) -> JSONResponse:
        logger.error("database unavailable", extra={"error_type": type(exc).__name__})
        return error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "Database unavailable")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return error_response(500, "Internal server error", code="internal_error")
