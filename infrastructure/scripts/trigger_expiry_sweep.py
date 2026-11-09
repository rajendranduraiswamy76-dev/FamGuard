"""Trigger the FamGuard expiry sweep through the signed cron endpoint.

Used by the scheduled GitHub Actions workflow (free-tier replacement for Celery Beat).
Standard library only. Environment:
  FAMGUARD_API_URL   e.g. https://famguard-api.onrender.com
  CRON_SECRET        shared secret configured on the API
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Final

WAKE_ATTEMPTS: Final = 8
WAKE_DELAY_S: Final = 15.0
TRIGGER_ATTEMPTS: Final = 4
TRIGGER_DELAY_S: Final = 10.0
REQUEST_TIMEOUT_S: Final = 90.0


class TriggerError(Exception):
    pass


def sign(secret: str, timestamp: str) -> str:
    return hmac.new(
        secret.encode(), f"{timestamp}:expiry-sweep".encode(), hashlib.sha256
    ).hexdigest()


def _request(url: str, *, method: str = "GET", headers: dict[str, str] | None = None) -> int:
    req = urllib.request.Request(url, method=method, headers=headers or {})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_S) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def wake(base_url: str, *, attempts: int = WAKE_ATTEMPTS, delay_s: float = WAKE_DELAY_S,
         sleep: Callable[[float], None] = time.sleep) -> None:
    """Render free services sleep when idle; poll /healthz until the container answers."""
    for attempt in range(1, attempts + 1):
        try:
            if _request(f"{base_url}/healthz") == 200:
                print(f"api awake after {attempt} attempt(s)")
                return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"wake attempt {attempt}: {type(exc).__name__}")
        if attempt < attempts:
            sleep(delay_s)
    raise TriggerError(f"API did not become healthy after {attempts} attempts")


def trigger(base_url: str, secret: str, *, attempts: int = TRIGGER_ATTEMPTS,
            delay_s: float = TRIGGER_DELAY_S,
            sleep: Callable[[float], None] = time.sleep) -> None:
    for attempt in range(1, attempts + 1):
        timestamp = str(int(time.time()))
        headers = {"X-Cron-Timestamp": timestamp, "X-Cron-Signature": sign(secret, timestamp)}
        try:
            status = _request(f"{base_url}/internal/cron/expiry-sweep", method="POST",
                              headers=headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status = 0
            print(f"trigger attempt {attempt}: {type(exc).__name__}")
        if status == 202:
            print("expiry sweep enqueued")
            return
        if status in (401, 403):
            raise TriggerError("API rejected the cron signature; check CRON_SECRET")
        print(f"trigger attempt {attempt}: HTTP {status}")
        if attempt < attempts:
            sleep(delay_s)
    raise TriggerError(f"Cron trigger failed after {attempts} attempts")


def main() -> int:
    base_url = os.environ.get("FAMGUARD_API_URL", "").rstrip("/")
    secret = os.environ.get("CRON_SECRET", "")
    if not base_url.startswith(("https://", "http://")) or not secret:
        print("FAMGUARD_API_URL (http/https) and CRON_SECRET must be set", file=sys.stderr)
        return 2
    try:
        wake(base_url)
        trigger(base_url, secret)
    except TriggerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
