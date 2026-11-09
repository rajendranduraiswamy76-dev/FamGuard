from __future__ import annotations

import hashlib
import hmac
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

import trigger_expiry_sweep as cron

SECRET = "test-secret"


class _Handler(BaseHTTPRequestHandler):
    health_failures: ClassVar[int] = 0
    trigger_statuses: ClassVar[list[int]] = []
    received: ClassVar[list[dict[str, str]]] = []

    def log_message(self, *args: Any) -> None:
        return None

    def do_GET(self) -> None:
        if _Handler.health_failures > 0:
            _Handler.health_failures -= 1
            self.send_response(502)
        else:
            self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:
        ts = self.headers.get("X-Cron-Timestamp", "")
        sig = self.headers.get("X-Cron-Signature", "")
        _Handler.received.append({"ts": ts, "sig": sig})
        expected = hmac.new(SECRET.encode(), f"{ts}:expiry-sweep".encode(),
                            hashlib.sha256).hexdigest()
        status = _Handler.trigger_statuses.pop(0) if _Handler.trigger_statuses else 202
        if status == 202 and not hmac.compare_digest(sig, expected):
            status = 401
        self.send_response(status)
        self.end_headers()


@pytest.fixture
def server() -> Iterator[str]:
    _Handler.health_failures = 0
    _Handler.trigger_statuses = []
    _Handler.received = []
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _no_sleep(_: float) -> None:
    return None


def test_signature_matches_api_contract() -> None:
    assert cron.sign("s", "1700000000") == hmac.new(
        b"s", b"1700000000:expiry-sweep", hashlib.sha256).hexdigest()


def test_wake_retries_until_healthy(server: str) -> None:
    _Handler.health_failures = 2
    cron.wake(server, attempts=5, sleep=_no_sleep)
    with pytest.raises(cron.TriggerError):
        _Handler.health_failures = 10
        cron.wake(server, attempts=3, sleep=_no_sleep)


def test_trigger_signs_and_retries_server_errors(server: str) -> None:
    _Handler.trigger_statuses = [503, 202]
    cron.trigger(server, SECRET, attempts=3, sleep=_no_sleep)
    assert len(_Handler.received) == 2


def test_wrong_secret_fails_fast(server: str) -> None:
    with pytest.raises(cron.TriggerError, match="CRON_SECRET"):
        cron.trigger(server, "wrong", attempts=3, sleep=_no_sleep)
    assert len(_Handler.received) == 1


def test_unreachable_api_fails() -> None:
    with pytest.raises(cron.TriggerError):
        cron.wake("http://127.0.0.1:9", attempts=2, sleep=_no_sleep)


def test_main_requires_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FAMGUARD_API_URL", raising=False)
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert cron.main() == 2
