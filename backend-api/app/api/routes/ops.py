from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.dependencies import ReadinessCheck, provide_readiness_checks, provide_task_queue
from app.tasks import TaskQueue

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ops"])

CRON_MAX_SKEW_S: Final = 300
READINESS_TIMEOUT_S: Final = 3.0


def cron_signature(secret: str, timestamp: str) -> str:
    message = f"{timestamp}:expiry-sweep".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


async def _run_check(check: ReadinessCheck) -> bool:
    try:
        return await asyncio.wait_for(check(), timeout=READINESS_TIMEOUT_S)
    except Exception:  # any failure means "not ready"; details stay in logs
        logger.warning("readiness check failed", exc_info=True)
        return False


@router.get("/readyz")
async def readyz(
    checks: Annotated[dict[str, ReadinessCheck], Depends(provide_readiness_checks)],
) -> JSONResponse:
    names = list(checks)
    results = await asyncio.gather(*(_run_check(checks[n]) for n in names))
    report = {n: ("ok" if ok else "error") for n, ok in zip(names, results, strict=True)}
    ready = all(results)
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ok" if ready else "degraded", "checks": report},
    )


@router.post("/internal/cron/expiry-sweep", status_code=status.HTTP_202_ACCEPTED,
             include_in_schema=False)
async def trigger_expiry_sweep(
    settings: Annotated[Settings, Depends(get_settings)],
    queue: Annotated[TaskQueue, Depends(provide_task_queue)],
    x_cron_timestamp: Annotated[str | None, Header()] = None,
    x_cron_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    secret = settings.cron_secret.get_secret_value()
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Cron trigger is not configured")
    if not x_cron_timestamp or not x_cron_signature or not x_cron_timestamp.isdigit():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid cron signature")
    if abs(time.time() - int(x_cron_timestamp)) > CRON_MAX_SKEW_S:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Cron signature expired")
    expected = cron_signature(secret, x_cron_timestamp)
    if not hmac.compare_digest(expected, x_cron_signature.strip().lower()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid cron signature")
    await queue.enqueue_expiry_sweep()
    logger.info("expiry sweep enqueued by cron trigger")
    return {"status": "enqueued"}
