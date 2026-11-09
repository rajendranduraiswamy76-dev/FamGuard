"""Container health check that adapts to RUN_MODE (used by Docker/compose HEALTHCHECK)."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.request


def check_api() -> None:
    port = os.environ.get("PORT", "8000")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=4) as resp:  # noqa: S310
        if resp.status != 200:
            raise RuntimeError(f"/healthz returned {resp.status}")


def check_worker() -> None:
    subprocess.run(  # noqa: S603
        ["celery", "-A", "famguard_worker.worker", "inspect", "ping",  # noqa: S607
         "--destination", f"celery@{socket.gethostname()}", "--timeout", "5"],
        check=True, capture_output=True, timeout=25,
    )


def main() -> int:
    mode = os.environ.get("RUN_MODE", "api")
    try:
        if mode in ("api", "combined"):
            check_api()
        if mode in ("worker", "combined"):
            check_worker()
    except Exception as exc:
        print(f"unhealthy ({mode}): {type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
